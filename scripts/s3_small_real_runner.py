"""S3 small acceptance complete incremental pilot with an offline replayable provider boundary.

The core trajectory is rebuilt from prior responses before every new request.
The saved M3p implementation is reused unchanged, preserving older artifacts.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import time
from uuid import uuid4

from scripts import m3p_scripted_runner as core
from scripts import s3_small_real_preflight as preflight
from scripts.s3_lookup_offline_preflight import classify_lookup_evidence
from scripts.m3n_budget_envelope import AttemptLedger
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import load_bundle, parse_json
from writscope.m3b import _artifacts
from writscope.providers import (ActualUsage, ProviderError, ProviderResponse,
                                 Usage)
from writscope.providers.deepseek import DeepSeekProvider, wire_bytes
from writscope.providers.mock import Script, ScriptedMock, Step, load_script
from scripts.m3o_interaction_contract import InteractionRequest
from scripts.m3m_final_review import FinalReviewRequest


VERSION = "s3_small_complete_incremental_real_v1"
SENTINEL = "s3_small_probe_only"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _settings(root: Path) -> dict:
    interaction = (root / "prompts/incremental_interaction_v1.md").read_text(encoding="utf-8")
    final = (root / "prompts/incremental_final_review_v1.md").read_text(encoding="utf-8")
    return core._settings(interaction, final, preflight.MODEL, preflight.POLICY,
                          preflight._price(), preflight.CAP_CNY)


def _script(steps: list[Step]) -> Script:
    return Script(schema_version="0.1-draft", execution_kind="offline_mock",
                  description="S3 small acceptance replay of captured provider responses",
                  steps=steps or [Step(action="error", text=SENTINEL)])


async def _probe(bundle, settings: dict, steps: list[Step]) -> tuple[dict, dict | None]:
    result = await core._execute(bundle, _script(steps), settings)
    if result["status"] == "completed":
        return result, None
    failure = result["failure"]
    expected = SENTINEL if not steps else "mock script exhausted"
    if (failure is not None and failure["status"] == "provider_error" and
            failure["error"] == expected and
            result["script_steps_consumed"] == len(steps) + (not steps)):
        call = result["calls"][-1]
        if call["attempt"] is None or call["status"] != "provider_error":
            raise ValueError("probe reached an unreserved call")
        return result, call
    return result, None


def _request(call: dict):
    return (FinalReviewRequest if call["stage"] == "final_review"
            else InteractionRequest).model_validate(call["request"])


def _usage_for_core(actual: ActualUsage) -> Usage:
    return Usage(input_tokens=actual.input_tokens, output_tokens=actual.output_tokens)


def _totals(calls: list[dict]) -> dict:
    def field(rows: list[dict], name: str, *, decimal: bool = False) -> dict:
        values = [(row["usage"] or {}).get(name) for row in rows]
        known = [Decimal(str(value)) if decimal else value
                 for value in values if value is not None]
        subtotal = sum(known, Decimal("0") if decimal else 0)
        return {"known_subtotal": str(subtotal) if decimal else subtotal,
                "unknown_attempts": len(values) - len(known),
                "complete_total": (str(subtotal) if decimal else subtotal)
                if len(values) == len(known) else None}

    by_stage = {}
    for stage in ("extract", "lookup_continuation", "final_review"):
        subset = [row for row in calls if row["stage"] == stage]
        by_stage[stage] = {"attempts": len(subset),
                           "reserved_cny": str(sum((Decimal(row["reserved_cny"])
                                                    for row in subset), Decimal("0"))),
                           "input_tokens": field(subset, "input_tokens"),
                           "output_tokens": field(subset, "output_tokens"),
                           "cache_hit_input_tokens": field(subset, "cache_hit_input_tokens"),
                           "cache_miss_input_tokens": field(subset, "cache_miss_input_tokens"),
                           "reasoning_tokens": field(subset, "reasoning_tokens"),
                           "estimated_cost_cny": field(subset, "estimated_cost_cny", decimal=True),
                           "provider_billed_cny": {"known_subtotal": "0",
                                                   "unknown_attempts": len(subset),
                                                   "complete_total": None if subset else "0"},
                           "provider_elapsed_ms": round(sum(row["provider_elapsed_ms"] or 0
                                                            for row in subset), 3)}
    return {
        "provider_attempts": len(calls),
        "reserved_cny": str(sum((Decimal(row["reserved_cny"]) for row in calls), Decimal("0"))),
        "input_tokens": field(calls, "input_tokens"),
        "output_tokens": field(calls, "output_tokens"),
        "cache_hit_input_tokens": field(calls, "cache_hit_input_tokens"),
        "cache_miss_input_tokens": field(calls, "cache_miss_input_tokens"),
        "reasoning_tokens": field(calls, "reasoning_tokens"),
        "estimated_cost_cny": field(calls, "estimated_cost_cny", decimal=True),
        "provider_billed_cny": {"known_subtotal": "0", "unknown_attempts": len(calls),
                                "complete_total": None if calls else "0"},
        "provider_elapsed_ms": round(sum(row["provider_elapsed_ms"] or 0 for row in calls), 3),
        "by_stage": by_stage,
    }


def _local_timing(steps: list[dict]) -> dict:
    return {"task_context": round(sum(step["context_elapsed_ms"] or 0
                                      for step in steps), 3),
            "state_batch": round(sum(step["batch_elapsed_ms"] or 0
                                     for step in steps), 3),
            "history_lookup": round(sum(lookup["elapsed_ms"] for step in steps
                                        for lookup in step["lookups"]), 3)}



def _lookup_classification(manifest: dict, result: dict, calls: list[dict]) -> str:
    requests = 0
    for call in calls:
        try:
            parsed = parse_json(call["response_text"] or "{}")
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict) and parsed.get("kind") == "lookup_request":
            requests += 1
    return classify_lookup_evidence(
        execution_kind=("real_model" if manifest["execution_kind"] == "real_model" else "offline_scripted"),
        trigger_policy="model_selected", completed=manifest["status"] == "completed",
        requests=requests, succeeded=len(result["lookup_history"]),
        continuations=sum(row["stage"] == "lookup_continuation" and row["status"] == "succeeded" for row in calls),
        final_valid=manifest.get("final_review_valid", False))


def _claim_paid_launch(root: Path, plan: dict, output: Path, authorization: dict) -> Path:
    """Local atomic one-use claim. Never remove it to retry a failed launch."""
    if output.exists():
        raise ValueError("paid output already exists")
    directory = root.resolve() / "reports/local/s3-small-paid-claims"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (canonical_hash(plan) + ".json")
    record = {"scope": plan["authorization_scope"], "plan_sha256": canonical_hash(plan),
              "runner_sha256": plan["real_runner_sha256"], "output": str(output.resolve()),
              "authorization_ref": authorization["user_authorization_ref"], "claimed_at_utc": _now()}
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
    except FileExistsError:
        raise ValueError("paid plan already claimed; new authorization required for any rerun") from None
    return path


def _snapshot(output: Path, manifest: dict, result: dict, calls: list[dict]) -> None:
    write_json(output / "calls.json", calls)
    write_json(output / "state.json", result["state"])
    write_json(output / "task_context.json", result["task_context"])
    write_json(output / "lookup_history.json", result["lookup_history"])
    write_json(output / "core_steps.json", result["steps"])
    if result["final_request"] is not None:
        write_json(output / "final_request.json", result["final_request"])
    if result["final_result"] is not None:
        write_json(output / "final_result.json", result["final_result"])
    manifest["totals"] = _totals(calls)
    manifest["accepted_events"] = result["ledger"]["events_accepted"]
    manifest["lookup_attempts"] = result["ledger"]["lookup_attempts"]
    manifest["local_maintenance_elapsed_ms"] = _local_timing(result["steps"])
    manifest["trigger_policy"] = "model_selected"
    manifest["lookup_evidence"] = _lookup_classification(manifest, result, calls)
    write_json(output / "run.json", manifest)


async def run_with_provider(root: Path, preflight_dir: Path, output: Path, provider,
                            *, mode: str, authorization: dict | None = None) -> dict:
    """Run once. Offline simulation may inject a provider; real mode requires a gate."""
    root, output = root.resolve(), output.resolve()
    preflight.verify_preflight(root, preflight_dir)
    plan = parse_json((preflight_dir / "plan.json").read_text(encoding="utf-8"))
    if mode not in {"offline_simulation", "real_model"}:
        raise ValueError("unknown execution mode")
    if mode == "real_model":
        if not isinstance(provider, DeepSeekProvider) or authorization is None:
            raise ValueError("real mode requires authorized DeepSeek provider")
        _check_authorization(plan, authorization)
    elif isinstance(provider, DeepSeekProvider):
        raise ValueError("real provider cannot be used for offline simulation")
    if output.is_relative_to((root / "reports/m1-2026-10-18/inputs").resolve()):
        raise ValueError("output cannot modify public input")
    bundle = load_bundle(preflight._bundle_dir(root))
    settings = _settings(root)
    steps: list[Step] = []
    calls: list[dict] = []
    if mode == "real_model":
        _claim_paid_launch(root, plan, output, authorization)
    output.mkdir(parents=True, exist_ok=False)
    run_id = "s3_small_" + uuid4().hex
    started = time.perf_counter()
    manifest = {"schema_version": "0.1-draft", "version": VERSION,
                "execution_kind": "real_model" if mode == "real_model" else "offline_simulation",
                "run_id": run_id, "case_id": preflight.CASE,
                "status": "running", "failure": None,
                "started_at_utc": _now(), "ended_at_utc": None,
                "plan_sha256": canonical_hash(plan),
                "preflight_manifest_sha256": digest((preflight_dir / "run.json").read_bytes()),
                "authorization_ref": (authorization["user_authorization_ref"]
                                      if mode == "real_model" else None),
                "actual_model_calls": 0,
                "simulated_provider_attempts": 0,
                "usage_kind": "provider_response" if mode == "real_model" else "synthetic_adapter",
                "provider_billed_cost_cny": "unknown",
                "runner_sha256": digest(Path(__file__).read_bytes()),
                "core_runner_sha256": digest(Path(core.__file__).read_bytes()),
                "pricing": plan["pricing"], "local_cap_cny": plan["local_cap_cny"]}
    write_json(output / "plan.json", plan)
    write_json(output / "settings.json", settings)
    result, next_call = await _probe(bundle, settings, steps)
    _snapshot(output, manifest, result, calls)
    failure = None
    while next_call is not None:
        if len(calls) >= plan["policy"]["max_provider_attempts"]:
            raise ValueError("probe exceeded frozen attempt limit")
        request = _request(next_call)
        encoded = wire_bytes(request)
        if (len(encoded) > plan["policy"]["max_wire_bytes_per_attempt"] or
                Decimal(next_call["attempt"]["reserved_cny"]) !=
                AttemptLedger(preflight._price(), preflight.CAP_CNY).quote(len(encoded))):
            raise ValueError("request size or reservation mismatch")
        reserved = Decimal(next_call["attempt"]["reserved_cny"])
        if sum((Decimal(row["reserved_cny"]) for row in calls), reserved) > preflight.CAP_CNY:
            raise ValueError("local cap exhausted before provider")
        call_id = f"call_{len(calls) + 1:03d}"
        row = {"call_id": call_id, "core_call_id": next_call["call_id"],
               "stage": next_call["stage"], "event_seq": next_call["event_seq"],
               "request": request.model_dump(), "wire_sha256": digest(encoded),
               "wire_bytes": len(encoded), "reserved_cny": str(reserved),
               "response_text": None, "usage": None,
               "provider_response_id": None, "served_model": None,
               "system_fingerprint": None, "finish_reason": None,
               "status": "running", "error": None,
               "started_at_utc": _now(), "ended_at_utc": None,
               "provider_elapsed_ms": None}
        calls.append(row)
        if mode == "real_model":
            manifest["actual_model_calls"] += 1
        else:
            manifest["simulated_provider_attempts"] += 1
        _snapshot(output, manifest, result, calls)
        call_start = time.perf_counter()
        response: ProviderResponse | None = None
        try:
            response = await asyncio.wait_for(provider.generate(request.model_copy(deep=True)),
                                              timeout=plan["policy"]["timeout_seconds_per_attempt"])
        except asyncio.TimeoutError:
            row["status"], row["error"] = "timeout", "provider deadline exceeded"
        except ProviderError as exc:
            row["status"], row["error"] = "provider_error", str(exc)
        except Exception as exc:
            row["status"], row["error"] = "provider_error", type(exc).__name__
        row["provider_elapsed_ms"] = round((time.perf_counter() - call_start) * 1000, 3)
        row["ended_at_utc"] = _now()
        if response is not None:
            row["response_text"] = response.text
            row["usage"] = (response.actual_usage.model_dump(mode="json")
                            if response.actual_usage is not None else None)
            for key in ("provider_response_id", "served_model", "system_fingerprint", "finish_reason"):
                row[key] = getattr(response, key)
            if len(response.text.encode("utf-8")) > plan["policy"]["max_response_bytes_per_attempt"]:
                row["status"], row["error"] = "invalid_output", "response byte limit exceeded"
            elif response.finish_reason != "stop":
                row["status"], row["error"] = "invalid_output", "provider finish reason is not stop"
            elif (response.actual_usage is None or response.actual_usage.input_tokens is None or
                  response.actual_usage.output_tokens is None):
                row["status"], row["error"] = "usage_missing", "provider usage missing"
            elif (response.actual_usage.input_tokens > len(encoded) + preflight._price().framing_token_allowance or
                  response.actual_usage.output_tokens > plan["policy"]["max_output_tokens"]):
                row["status"], row["error"] = "usage_exceeds_reservation", "provider usage exceeds reserved token envelope"
            else:
                steps.append(Step(action="response", text=response.text,
                                  synthetic_usage=_usage_for_core(response.actual_usage)))
                result, following = await _probe(bundle, settings, steps)
                # The just-returned response is the last consumed call. Any
                # extra mock-exhaustion probe is only a next-request preview.
                actual_core = result["calls"][len(calls) - 1]
                row["status"] = actual_core["status"]
                row["error"] = actual_core["error"]
                if row["status"] == "succeeded":
                    next_call = following
                    if next_call is None and result["status"] != "completed":
                        failure = result["failure"]
                else:
                    failure = result["failure"]
                    next_call = None
        if row["status"] != "succeeded":
            next_call = None
            failure = failure or {"stage": row["stage"], "event_seq": row["event_seq"],
                                  "status": row["status"], "error": row["error"]}
        _snapshot(output, manifest, result, calls)
    if failure is None and result["status"] != "completed":
        failure = result["failure"]
    manifest["status"] = "completed" if failure is None else "stopped_on_failure"
    manifest["failure"] = failure
    manifest["ended_at_utc"] = _now()
    manifest["wall_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    manifest["final_review_valid"] = failure is None and result["final_result"] is not None
    _snapshot(output, manifest, result, calls)
    manifest["artifacts"] = _artifacts(output)
    manifest["artifact_set_sha256"] = canonical_hash(manifest["artifacts"])
    write_json(output / "run.json", manifest)
    return manifest


class SimulatedActualProvider:
    """Exercise the real response shape with a ScriptedMock, never network."""

    def __init__(self, script: Script):
        self.mock = ScriptedMock(script)

    async def generate(self, request) -> ProviderResponse:
        response = await self.mock.generate(request)
        usage = response.synthetic_usage
        return ProviderResponse(text=response.text,
            actual_usage=ActualUsage(input_tokens=usage.input_tokens if usage else None,
                                     output_tokens=usage.output_tokens if usage else None,
                                     usage_source="provider_response" if usage else "unavailable",
                                     cost_source="unavailable"),
            finish_reason="stop")


def run_real(root: Path, preflight_dir: Path, output: Path, authorization: dict) -> dict:
    """This is the only credential-reading entry; authorization precedes it."""
    preflight.verify_preflight(root, preflight_dir)
    plan = parse_json((preflight_dir / "plan.json").read_text(encoding="utf-8"))
    _check_authorization(plan, authorization)
    key = preflight.require_authorized_key(plan, authorization)
    provider = DeepSeekProvider(key, preflight._price())
    return asyncio.run(run_with_provider(root, preflight_dir, output, provider,
                                         mode="real_model", authorization=authorization))


def _check_authorization(plan: dict, authorization: dict) -> None:
    if (plan.get("real_runner_sha256") != digest(Path(__file__).read_bytes()) or
            plan.get("trigger_policy") != "model_selected" or plan.get("planned_runs") != 1 or
            authorization.get("scope") != plan.get("authorization_scope")):
        raise ValueError("new runner binding or one-run scope mismatch")
    # Validate without reading credentials; the caller then uses the new preflight gate.
    preflight.require_authorized_key(plan, authorization,
                                     key_reader=lambda: "scope-validated")
    if (authorization.get("runner_sha256") != digest(Path(__file__).read_bytes()) or
            authorization.get("price_source_url") != preflight.PRICE_URL or
            not isinstance(authorization.get("price_rechecked_at_utc"), str) or
            not authorization["price_rechecked_at_utc"].strip()):
        raise ValueError("S3 small acceptance runner or price recheck is outside authorization")
    try:
        checked = datetime.fromisoformat(authorization["price_rechecked_at_utc"])
        if checked.tzinfo is None:
            raise ValueError("timezone missing")
        age = datetime.now(timezone.utc) - checked.astimezone(timezone.utc)
    except ValueError:
        raise ValueError("invalid price recheck time") from None
    if not -timedelta(minutes=5) <= age <= timedelta(hours=24):
        raise ValueError("price recheck is not fresh")


def _stable_steps(steps: list[dict]) -> list[dict]:
    value = json.loads(json.dumps(steps, ensure_ascii=False))
    for step in value:
        step.pop("context_elapsed_ms", None)
        step.pop("batch_elapsed_ms", None)
        for lookup in step["lookups"]:
            lookup.pop("elapsed_ms", None)
    return value


async def _verify_async(root: Path, preflight_dir: Path, output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    preflight.verify_preflight(root, preflight_dir)
    plan = parse_json((preflight_dir / "plan.json").read_text(encoding="utf-8"))
    manifest = parse_json((output / "run.json").read_text(encoding="utf-8"))
    current_runner_hash = digest(Path(__file__).read_bytes())
    saved_runner_source = output.parent / (output.name + "-runner-source.py")
    saved_runner_hash = (digest(saved_runner_source.read_bytes())
                         if saved_runner_source.exists() else None)
    if (manifest.get("version") != VERSION or
            manifest.get("execution_kind") not in {"real_model", "offline_simulation"} or
            manifest.get("status") not in {"completed", "stopped_on_failure"} or
            manifest.get("case_id") != preflight.CASE or
            manifest.get("plan_sha256") != canonical_hash(plan) or
            manifest.get("preflight_manifest_sha256") !=
            digest((preflight_dir / "run.json").read_bytes()) or
            manifest.get("runner_sha256") not in {current_runner_hash, saved_runner_hash} or
            manifest.get("core_runner_sha256") != digest(Path(core.__file__).read_bytes()) or
            manifest.get("pricing") != plan["pricing"] or
            manifest.get("local_cap_cny") != plan["local_cap_cny"]):
        raise ValueError("S3 small acceptance run identity or frozen policy mismatch")
    if parse_json((output / "plan.json").read_text(encoding="utf-8")) != plan:
        raise ValueError("S3 small acceptance saved plan mismatch")
    if parse_json((output / "settings.json").read_text(encoding="utf-8")) != _settings(root):
        raise ValueError("S3 small acceptance core settings mismatch")
    artifacts = _artifacts(output)
    if (manifest.get("artifacts") != artifacts or
            manifest.get("artifact_set_sha256") != canonical_hash(artifacts)):
        raise ValueError("S3 small acceptance artifact hash mismatch")
    calls = parse_json((output / "calls.json").read_text(encoding="utf-8"))
    if len(calls) > plan["policy"]["max_provider_attempts"]:
        raise ValueError("S3 small acceptance attempt count exceeds limit")
    bundle = load_bundle(preflight._bundle_dir(root))
    settings = _settings(root)
    steps: list[Step] = []
    result, next_call = await _probe(bundle, settings, steps)
    failure = None
    reserved = Decimal("0")
    for index, row in enumerate(calls):
        if next_call is None:
            raise ValueError("S3 small acceptance continued after a terminal result")
        request = _request(next_call)
        wire = wire_bytes(request)
        quote = AttemptLedger(preflight._price(), preflight.CAP_CNY).quote(len(wire))
        if (row["call_id"] != f"call_{index + 1:03d}" or
                row["core_call_id"] != next_call["call_id"] or
                row["stage"] != next_call["stage"] or
                row["event_seq"] != next_call["event_seq"] or
                row["request"] != request.model_dump() or
                row["wire_sha256"] != digest(wire) or
                row["wire_bytes"] != len(wire) or
                Decimal(row["reserved_cny"]) != quote or
                row["provider_elapsed_ms"] is None or row["provider_elapsed_ms"] < 0):
            raise ValueError("S3 small acceptance request or reservation replay mismatch")
        reserved += quote
        if reserved > preflight.CAP_CNY:
            raise ValueError("S3 small acceptance cumulative reservation exceeds cap")
        raw = row["response_text"]
        usage = (ActualUsage.model_validate_json(json.dumps(row["usage"]))
                 if row["usage"] is not None else None)
        if raw is None:
            if row["status"] not in {"timeout", "provider_error"} or not row["error"]:
                raise ValueError("S3 small acceptance no-response failure mismatch")
            next_call = None
        elif len(raw.encode("utf-8")) > plan["policy"]["max_response_bytes_per_attempt"]:
            if row["status"] != "invalid_output" or row["error"] != "response byte limit exceeded":
                raise ValueError("S3 small acceptance response-size failure mismatch")
            next_call = None
        elif row["finish_reason"] != "stop":
            if row["status"] != "invalid_output" or row["error"] != "provider finish reason is not stop":
                raise ValueError("S3 small acceptance finish-reason failure mismatch")
            next_call = None
        elif usage is None or usage.input_tokens is None or usage.output_tokens is None:
            if row["status"] != "usage_missing" or row["error"] != "provider usage missing":
                raise ValueError("S3 small acceptance missing-usage failure mismatch")
            next_call = None
        elif (usage.input_tokens > len(wire) + preflight._price().framing_token_allowance or
              usage.output_tokens > plan["policy"]["max_output_tokens"]):
            if (row["status"] != "usage_exceeds_reservation" or
                    row["error"] != "provider usage exceeds reserved token envelope"):
                raise ValueError("reserved token envelope replay mismatch")
            next_call = None
        else:
            steps.append(Step(action="response", text=raw,
                              synthetic_usage=_usage_for_core(usage)))
            result, following = await _probe(bundle, settings, steps)
            actual_core = result["calls"][index]
            if row["status"] != actual_core["status"] or row["error"] != actual_core["error"]:
                raise ValueError("S3 small acceptance response transition replay mismatch")
            next_call = following if row["status"] == "succeeded" else None
            if result["status"] != "completed" and following is None:
                failure = result["failure"]
        if row["status"] != "succeeded":
            failure = failure or {"stage": row["stage"], "event_seq": row["event_seq"],
                                  "status": row["status"], "error": row["error"]}
            if index != len(calls) - 1:
                raise ValueError("S3 small acceptance continued after first failure")
        elif failure is not None and index != len(calls) - 1:
            raise ValueError("S3 small acceptance continued after lookup or request failure")
    if next_call is not None and failure is None:
        raise ValueError("S3 small acceptance omitted a pending provider attempt")
    if failure is None and result["status"] != "completed":
        failure = result["failure"]
    if (manifest["status"] != ("completed" if failure is None else "stopped_on_failure") or
            manifest["failure"] != failure or
            manifest["accepted_events"] != result["ledger"]["events_accepted"] or
            manifest["lookup_attempts"] != result["ledger"]["lookup_attempts"] or
            manifest["totals"] != _totals(calls) or
            manifest["final_review_valid"] !=
            (failure is None and result["final_result"] is not None)):
        raise ValueError("S3 small acceptance outcome or cost replay mismatch")
    if (manifest.get("trigger_policy") != "model_selected" or
            manifest.get("lookup_evidence") != _lookup_classification(manifest, result, calls)):
        raise ValueError("lookup evidence replay mismatch")
    if manifest["local_maintenance_elapsed_ms"] != _local_timing(parse_json(
            (output / "core_steps.json").read_text(encoding="utf-8"))):
        raise ValueError("S3 small acceptance local maintenance timing mismatch")
    real = manifest["execution_kind"] == "real_model"
    if (manifest["actual_model_calls"] != (len(calls) if real else 0) or
            manifest["simulated_provider_attempts"] != (0 if real else len(calls)) or
            (real and not manifest["authorization_ref"]) or
            (not real and manifest["authorization_ref"] is not None)):
        raise ValueError("S3 small acceptance execution provenance mismatch")
    if real:
        claim_path = root / "reports/local/s3-small-paid-claims" / (canonical_hash(plan) + ".json")
        claim = parse_json(claim_path.read_text(encoding="utf-8"))
        if (claim.get("plan_sha256") != canonical_hash(plan) or
                claim.get("output") != str(output) or
                claim.get("authorization_ref") != manifest["authorization_ref"] or
                claim.get("runner_sha256") != manifest["runner_sha256"]):
            raise ValueError("one-use paid launch provenance mismatch")
    for name, expected in (("state.json", result["state"]),
                           ("task_context.json", result["task_context"]),
                           ("lookup_history.json", result["lookup_history"])):
        if parse_json((output / name).read_text(encoding="utf-8")) != expected:
            raise ValueError("S3 small acceptance state or lookup replay mismatch")
    if _stable_steps(parse_json((output / "core_steps.json").read_text(encoding="utf-8"))) != _stable_steps(result["steps"]):
        raise ValueError("S3 small acceptance core step replay mismatch")
    for name, expected in (("final_request.json", result["final_request"]),
                           ("final_result.json", result["final_result"])):
        path = output / name
        if (path.exists() != (expected is not None) or
                (expected is not None and parse_json(path.read_text(encoding="utf-8")) != expected)):
            raise ValueError("S3 small acceptance final review replay mismatch")
    return manifest


def verify_run(root: Path, preflight_dir: Path, output: Path) -> dict:
    """No credentials or network: reconstruct requests and state from saved text."""
    return asyncio.run(_verify_async(root, preflight_dir, output))


def score_run(root: Path, preflight_dir: Path, output: Path,
              labels_path: Path, score_output: Path) -> dict:
    """Read evaluator labels only after a saved real run passes replay."""
    run = verify_run(root, preflight_dir, output)
    if run["execution_kind"] != "real_model":
        raise ValueError("offline simulation cannot be scored as real model output")
    raw_labels = labels_path.read_bytes()
    labels = parse_json(raw_labels.decode("utf-8"))
    if labels.get("kind") != "reference_labels_not_predictions":
        raise ValueError("unexpected reference label kind")
    references = [row for row in labels["labels"] if row["case_id"] == preflight.CASE]
    if len(references) != 1:
        raise ValueError("c003 reference label must be unique")
    reference = references[0]
    observed = (parse_json((output / "final_result.json").read_text(encoding="utf-8"))
                if run["final_review_valid"] else None)
    axes = ("impact", "authorization", "decision")
    expected = {axis: reference["expected_" + axis] for axis in axes}
    prediction = {axis: observed[axis] if observed else None for axis in axes}
    correct = {axis: observed is not None and observed[axis] == expected[axis]
               for axis in axes}
    report = {"schema_version": "0.1-draft",
              "execution_kind": "real_model_c003_development_diagnostic_score",
              "claim_scope": "single_debugged_case_no_method_comparison",
              "source_run_id": run["run_id"],
              "source_artifact_set_sha256": run["artifact_set_sha256"],
              "reference_sha256": digest(raw_labels),
              "reference_status": "project_authored_draft",
              "planned_cases": 1, "valid_cases": int(observed is not None),
              "case_id": preflight.CASE,
              "expected": expected, "observed": prediction, "correct": correct,
              "cost": run["totals"], "comparison_status": "not_run",
              "semantic_extraction_audit": "not_performed"}
    score_output.mkdir(parents=True, exist_ok=False)
    write_json(score_output / "summary.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="S3 small acceptance complete incremental runner")
    parser.add_argument("command", choices=("simulate", "real", "verify", "score"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--script", type=Path)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--labels", type=Path)
    args = parser.parse_args(argv)
    if args.command == "score":
        if args.labels is None:
            parser.error("score requires isolated reference labels")
        result = score_run(args.root, args.preflight, args.output,
                           args.labels, args.output.parent / (args.output.name + "-score"))
        print(json.dumps({"status": "scored", "valid_cases": result["valid_cases"]},
                         ensure_ascii=False))
        return
    if args.command == "verify":
        result = verify_run(args.root, args.preflight, args.output)
    elif args.command == "real":
        if args.authorization is None:
            parser.error("real requires a new scoped authorization file")
        receipt = parse_json(args.authorization.read_text(encoding="utf-8"))
        result = run_real(args.root, args.preflight, args.output, receipt)
    else:
        if args.script is None:
            parser.error("simulate requires a scripted response file")
        result = asyncio.run(run_with_provider(args.root, args.preflight, args.output,
            SimulatedActualProvider(load_script(args.script)), mode="offline_simulation"))
    print(json.dumps({"status": result["status"],
                      "actual_model_calls": result["actual_model_calls"],
                      "simulated_provider_attempts": result["simulated_provider_attempts"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
