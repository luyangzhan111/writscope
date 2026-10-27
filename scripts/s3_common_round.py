"""One frozen three-method development round. Scoring is a separate command."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import time

from scripts import m3p_scripted_runner as core
from scripts import s3_small_real_runner as helper
from scripts.m3i_task_context import TaskContextLedger
from scripts.m3j_transition_batch import BatchStateEngine
from scripts.m3k_history_lookup import LookupPolicy
from scripts.m3n_budget_envelope import AttemptLedger
from scripts.m3o_interaction_contract import build_request
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.baselines import build_request as baseline
from writscope.budget import PriceCeiling
from writscope.contracts import load_bundle, parse_json, validate_output
from writscope.m3b import _artifacts, _code_provenance
from writscope.providers import ActualUsage, ModelConfig, ProviderError
from writscope.providers.deepseek import DeepSeekProvider, wire_bytes

VERSION = "s3_common_round_v1"
SCOPE = "S3_three_method_three_case_development_round_v1"
CASES = ("c001", "c002", "c003")
METHODS = ("full_direct", "full_checklist", "incremental")
MODEL = ModelConfig(model_id="deepseek-flash", temperature=0.0, max_output_tokens=4096)
POLICY = LookupPolicy(max_requests=2, max_events_per_request=2)
CAP = Decimal("12.00")
PRICE_URL = "https://api-docs.deepseek.com/quick_start/pricing/"
CHECKED = "2026-10-25T13:42:43+00:00"
PRICE = PriceCeiling(input_cny_per_million=Decimal("3"), output_cny_per_million=Decimal("12"),
                     source_url=PRICE_URL, checked_at_utc=CHECKED)
PROMPTS = {"full_direct": "prompts/full_direct.md", "full_checklist": "prompts/full_checklist.md",
           "interaction": "prompts/incremental_interaction_v1.md",
           "final": "prompts/incremental_final_review_v1.md"}


def read(path):
    return parse_json(path.read_text(encoding="utf-8"))


def bundle(root, case):
    return load_bundle(root / "reports/m1-2026-10-18/inputs/verified_impact" / case)


def settings(root):
    prompts = {key: (root / path).read_text(encoding="utf-8") for key, path in PROMPTS.items()}
    return prompts, core._settings(prompts["interaction"], prompts["final"], MODEL, POLICY, PRICE, Decimal("3.00"))


def prepare(root):
    prompts, config = settings(root)
    jobs, sources = [], {}
    for index, case in enumerate(CASES):
        data = bundle(root, case)
        config_hash = core._config_hash(prompts["interaction"], prompts["final"], MODEL, POLICY)
        state = BatchStateEngine(case, data.review_input.events, config_hash, set(data.registry.artifacts)).snapshot()
        context = TaskContextLedger(data.review_input.events, set(data.registry.artifacts)).snapshot()
        requests = {method: baseline(data, method, prompts[method], MODEL) for method in METHODS[:2]}
        requests["incremental"] = build_request(data, 0, state, context, prompts["interaction"], MODEL, POLICY)
        if len({r.evidence_sha256 for r in requests.values()}) != 1:
            raise ValueError("common evidence parity failed")
        sources[case] = {"public_bundle_sha256": data.sha256,
                         "public_evidence_sha256": requests["full_direct"].evidence_sha256,
                         "tool_policy_sha256": canonical_hash(data.review_input.tool_policy.model_dump())}
        # Fixed cyclic order balances method positions; R=1, no seed sampling.
        for method in METHODS[index:] + METHODS[:index]:
            request = requests[method]
            if len(wire_bytes(request)) > 100000:
                raise ValueError("full evidence cannot fit; truncation forbidden")
            jobs.append({"job_id": f"job_{len(jobs)+1:03d}", "case_id": case, "method": method,
                         "repeat_index": 0, "max_attempts": 8 if method == "incremental" else 1,
                         "local_cap_cny": "3.00" if method == "incremental" else "0.40",
                         "first_request_sha256": canonical_hash(request.model_dump())})
    modules = [Path(__file__), Path(helper.__file__), Path(core.__file__),
               Path(helper.preflight.__file__), Path(__file__).with_name("s3_lookup_offline_preflight.py"),
               Path(__file__).with_name("s3_common_report.py")]
    modules += [Path(module.__file__) for module in core.COMPONENTS]
    plan = {"version": VERSION, "status": "draft_unapproved", "authorization_scope": SCOPE,
            "case_ids": list(CASES), "methods": list(METHODS), "jobs": jobs,
            "model": MODEL.model_dump(), "comparison": "equal_configuration_development_only",
            "budget_matched": False, "independent_families": 1, "repeat_count": 1,
            "reference_status": "project_authored_draft_external_review_pending",
            "trigger_policy": "model_selected_unchanged_full_visible_history",
            "policy": {"max_attempts": 30, "max_wire_bytes": 100000, "max_response_bytes": 100000,
                       "timeout_seconds": 90, "concurrency": 1, "retries": 0,
                       "stop": "first_failure_stops_entire_round", "automatic_reruns": 0},
            "local_cap_cny": str(CAP), "price": PRICE.model_dump(mode="json"),
            "official_peak_usd_per_million": {"input_cache_miss": "0.3", "output": "1.2"},
            "conservative_cny_per_usd": "10_not_exchange_quote",
            "worst_case_reserved_cny": str(AttemptLedger(PRICE, Decimal("3")).quote(100000)*30),
            "public_sources": sources, "new_authorization": None, "actual_model_calls": 0,
            "prompt_sha256": {key: digest(text.encode()) for key, text in prompts.items()},
            "source_sha256": {path.relative_to(root).as_posix(): digest(path.read_bytes()) for path in modules},
            **_code_provenance()}
    return plan


def preflight(root, output, *, verify=False):
    if output.resolve().parent != root.resolve() / "reports/local":
        raise ValueError("preflight must be a direct child of reports/local")
    plan = prepare(root)
    if verify:
        if read(output / "plan.json") != plan:
            raise ValueError("frozen common plan mismatch")
    else:
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / "plan.json", plan)
    manifest = {"version": VERSION, "status": "draft_unapproved", "actual_model_calls": 0,
                "plan_sha256": canonical_hash(plan), "artifacts": _artifacts(output)}
    if verify:
        if read(output / "run.json") != manifest:
            raise ValueError("preflight artifacts mismatch")
    else:
        write_json(output / "run.json", manifest)
    return plan


def check_authorization(plan, receipt):
    expected = {"status": "approved", "source_kind": "direct_user_message", "scope": SCOPE,
                "plan_sha256": canonical_hash(plan), "case_ids": list(CASES), "methods": list(METHODS),
                "local_cap_cny": "12.00", "max_provider_attempts": 30, "planned_rounds": 1,
                "runner_sha256": digest(Path(__file__).read_bytes()), "price_source_url": PRICE_URL}
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("new exact-scope common-round authorization required")
    if not isinstance(receipt.get("user_authorization_ref"), str) or not receipt["user_authorization_ref"].strip():
        raise ValueError("direct user authorization reference required")
    try:
        checked = datetime.fromisoformat(receipt["price_rechecked_at_utc"])
        age = datetime.now(timezone.utc) - checked
        if checked.tzinfo is None or not -timedelta(minutes=5) <= age <= timedelta(hours=24):
            raise ValueError("stale price")
    except (KeyError, TypeError, ValueError):
        raise ValueError("fresh official price recheck required") from None


def claim_path(root, plan):
    return root / "reports/local/s3-common-paid-claims" / (canonical_hash(plan) + ".json")


def claim(root, plan, output, receipt):
    path = claim_path(root, plan)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"plan_sha256": canonical_hash(plan), "output": str(output.resolve()),
              "authorization_ref": receipt["user_authorization_ref"]}
    with path.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False)


class Cursor:
    def __init__(self, root, unit):
        self.unit, self.data = unit, bundle(root, unit["case_id"])
        self.prompts, self.config = settings(root)
        self.steps, self.failure, self.result, self.pending = [], None, None, None
        self.baseline_result = None
        self.rebuild_ms = 0.0

    async def start(self):
        if self.unit["method"] == "incremental":
            await self.probe()
        else:
            self.pending = {"stage": "review", "event_seq": None, "request": baseline(
                self.data, self.unit["method"], self.prompts[self.unit["method"]], MODEL)}

    async def probe(self):
        started = time.perf_counter()
        self.result, call = await helper._probe(self.data, self.config, self.steps)
        self.rebuild_ms += (time.perf_counter()-started)*1000
        self.pending = ({"stage": call["stage"], "event_seq": call["event_seq"],
                         "request": helper._request(call)} if call is not None else None)
        if call is None and self.result["status"] != "completed":
            self.failure = self.result["failure"]

    async def consume(self, row, plan):
        raw = row["response_text"]
        error = None
        status = "succeeded"
        usage = (ActualUsage.model_validate_json(json.dumps(row["usage"])) if row["usage"] is not None else None)
        if raw is None:
            if row["status"] not in {"timeout", "provider_error"} or not row["error"]:
                raise ValueError("invalid saved provider failure")
            status, error = row["status"], row["error"]
        elif len(raw.encode()) > 100000 or row["finish_reason"] != "stop":
            status, error = "invalid_output", "response limit or finish reason"
        elif usage is None or usage.input_tokens is None or usage.output_tokens is None:
            status, error = "usage_missing", "provider usage missing"
        elif usage.input_tokens > row["wire_bytes"]+PRICE.framing_token_allowance or usage.output_tokens > 4096:
            status, error = "usage_exceeds_reservation", "provider usage exceeds reserved envelope"
        else:
            try:
                if self.unit["method"] == "incremental":
                    self.steps.append(helper.Step(action="response", text=raw,
                                                  synthetic_usage=helper._usage_for_core(usage)))
                    await self.probe()
                    last = self.result["calls"][len(self.steps)-1]
                    status, error = last["status"], last["error"]
                    if self.pending is None and self.result["status"] != "completed":
                        status, error = self.result["failure"]["status"], self.result["failure"]["error"]
                else:
                    self.baseline_result = validate_output(raw, self.data, self.unit["method"],
                                                           output_contract="source_quote_v1").model_dump()
                    self.pending = None
            except (ValueError, TypeError) as exc:
                status, error = "invalid_output", str(exc)
        if status != "succeeded":
            self.failure = {"stage": row["stage"], "status": status, "error": error}
            self.pending = None
        return status, error

    def outcome(self, mode, calls):
        completed = self.failure is None and self.pending is None
        value = {**self.unit, "status": "completed" if completed else "stopped_on_failure",
                 "failure": self.failure, "result": None, "accepted_events": None,
                 "lookup_evidence": "not_applicable", "state": None, "task_context": None,
                 "lookup_history": None, "cost": totals(calls)}
        if self.unit["method"] == "incremental":
            value.update(accepted_events=self.result["ledger"]["events_accepted"],
                         state=self.result["state"], task_context=self.result["task_context"],
                         lookup_history=self.result["lookup_history"],
                         transition_steps=helper._stable_steps(self.result["steps"]))
            value["result"] = self.result["final_result"] if completed else None
            value["lookup_evidence"] = helper._lookup_classification(
                {"execution_kind": mode, "status": value["status"], "final_review_valid": value["result"] is not None},
                self.result, calls)
        else:
            value["result"] = self.baseline_result if completed else None
        return value


def quote(request):
    return AttemptLedger(PRICE, Decimal("3")).quote(len(wire_bytes(request)))


def totals(calls):
    value = helper._totals(calls)
    review = [row for row in calls if row["stage"] == "review"]
    subtotal = helper._totals(review)
    value["by_stage"]["review"] = {key: item for key, item in subtotal.items()
                                        if key not in {"by_stage", "provider_attempts"}}
    value["by_stage"]["review"]["attempts"] = len(review)
    return value


def save(output, manifest, calls, jobs):
    write_json(output / "calls.json", calls)
    write_json(output / "jobs.json", jobs)
    manifest["totals"] = totals(calls)
    manifest["actual_model_calls"] = len(calls) if manifest["execution_kind"] == "real_model" else 0
    manifest["simulated_provider_attempts"] = len(calls) if manifest["execution_kind"] == "offline_simulation" else 0
    write_json(output / "run.json", manifest)


async def execute(root, prepared, output, provider, *, mode="offline_simulation", receipt=None):
    plan = preflight(root, prepared, verify=True)
    if output.resolve().parent != root.resolve() / "reports/local":
        raise ValueError("run output must be a direct child of reports/local")
    if output.exists():
        raise ValueError("fresh output required")
    if mode == "real_model":
        check_authorization(plan, receipt or {})
        if not isinstance(provider, DeepSeekProvider):
            raise ValueError("real mode requires fixed provider")
        claim(root, plan, output, receipt)
    elif mode != "offline_simulation" or isinstance(provider, DeepSeekProvider):
        raise ValueError("invalid execution provenance")
    output.mkdir(parents=True)
    write_json(output / "plan.json", plan)
    manifest = {"version": VERSION, "execution_kind": mode, "status": "running",
                "plan_sha256": canonical_hash(plan), "authorization_ref": receipt["user_authorization_ref"] if mode == "real_model" else None,
                "started_at_utc": datetime.now(timezone.utc).isoformat(), "failure": None}
    calls, jobs = [], [{**unit, "status": "not_run", "result": None} for unit in plan["jobs"]]
    suite_start, stopped = time.perf_counter(), False
    save(output, manifest, calls, jobs)
    for index, unit in enumerate(plan["jobs"]):
        if stopped:
            break
        cursor = Cursor(root, unit)
        await cursor.start()
        own = []
        while cursor.pending is not None:
            pending, started = cursor.pending, time.perf_counter()
            request, charge = pending["request"], quote(pending["request"])
            if (len(calls) >= 30 or len(own) >= unit["max_attempts"] or
                    sum(Decimal(r["reserved_cny"]) for r in calls)+charge > CAP or
                    sum(Decimal(r["reserved_cny"]) for r in own)+charge > Decimal(unit["local_cap_cny"])):
                cursor.failure = {"stage": pending["stage"], "status": "budget_exhausted", "error": "pre-send limit"}
                cursor.pending = None
                break
            row = {"call_id": f"call_{len(calls)+1:03d}", "job_id": unit["job_id"],
                   "started_at_utc": datetime.now(timezone.utc).isoformat(), "ended_at_utc": None,
                   "stage": pending["stage"], "event_seq": pending["event_seq"],
                   "request": request.model_dump(), "wire_bytes": len(wire_bytes(request)),
                   "wire_sha256": digest(wire_bytes(request)), "reserved_cny": str(charge),
                   "response_text": None, "usage": None, "finish_reason": None,
                   "provider_response_id": None, "served_model": None, "system_fingerprint": None,
                   "status": "running", "error": None, "provider_elapsed_ms": None}
            calls.append(row); own.append(row)
            save(output, manifest, calls, jobs)
            started = time.perf_counter()
            try:
                response = await asyncio.wait_for(provider.generate(request.model_copy(deep=True)), timeout=90)
                row.update(response_text=response.text,
                           usage=response.actual_usage.model_dump(mode="json") if response.actual_usage is not None else None)
                for key in ("finish_reason", "provider_response_id", "served_model", "system_fingerprint"):
                    row[key] = getattr(response, key)
            except TimeoutError:
                row.update(status="timeout", error="provider deadline exceeded")
            except ProviderError as exc:
                row.update(status="provider_error", error=str(exc))
            except Exception as exc:
                row.update(status="provider_error", error=type(exc).__name__)
            row["provider_elapsed_ms"] = round((time.perf_counter()-started)*1000, 3)
            row["ended_at_utc"] = datetime.now(timezone.utc).isoformat()
            row["status"], row["error"] = await cursor.consume(row, plan)
            jobs[index] = cursor.outcome(mode, own)
            jobs[index]["local_rebuild_elapsed_ms"] = round(cursor.rebuild_ms, 3)
            save(output, manifest, calls, jobs)
        jobs[index] = cursor.outcome(mode, own)
        jobs[index]["local_rebuild_elapsed_ms"] = round(cursor.rebuild_ms, 3)
        stopped = cursor.failure is not None
        if stopped:
            manifest["failure"] = {"job_id": unit["job_id"], **cursor.failure}
    manifest.update(status="stopped_on_failure" if stopped else "completed",
                    ended_at_utc=datetime.now(timezone.utc).isoformat(),
                    wall_elapsed_ms=round((time.perf_counter()-suite_start)*1000, 3))
    save(output, manifest, calls, jobs)
    manifest["artifacts"] = _artifacts(output)
    write_json(output / "run.json", manifest)
    return manifest


async def verify(root, prepared, output):
    plan = preflight(root, prepared, verify=True)
    manifest, calls, jobs = read(output / "run.json"), read(output / "calls.json"), read(output / "jobs.json")
    if (manifest.get("version") != VERSION or manifest.get("plan_sha256") != canonical_hash(plan) or
            read(output / "plan.json") != plan or manifest.get("artifacts") != _artifacts(output) or
            manifest.get("execution_kind") not in {"real_model", "offline_simulation"}):
        raise ValueError("common run identity/artifacts mismatch")
    mode = manifest["execution_kind"]
    if mode == "real_model":
        record = read(claim_path(root, plan))
        if record != {"plan_sha256": canonical_hash(plan), "output": str(output.resolve()),
                       "authorization_ref": manifest["authorization_ref"]} or not manifest["authorization_ref"]:
            raise ValueError("one-use launch provenance mismatch")
    elif manifest["authorization_ref"] is not None:
        raise ValueError("simulation cannot claim paid authorization")
    position, failure, reserved = 0, None, Decimal("0")
    for index, unit in enumerate(plan["jobs"]):
        if failure is not None:
            if jobs[index] != {**unit, "status": "not_run", "result": None}:
                raise ValueError("continued after first failure")
            continue
        cursor = Cursor(root, unit)
        await cursor.start()
        own = []
        while cursor.pending is not None:
            if position >= len(calls):
                raise ValueError("missing pending attempt")
            row = calls[position]
            pending, request = cursor.pending, cursor.pending["request"]
            charge = quote(request)
            if (row["job_id"] != unit["job_id"] or row["call_id"] != f"call_{position+1:03d}" or
                    row["stage"] != pending["stage"] or row["event_seq"] != pending["event_seq"] or
                    row["request"] != request.model_dump() or row["wire_sha256"] != digest(wire_bytes(request)) or
                    row["wire_bytes"] != len(wire_bytes(request)) or Decimal(row["reserved_cny"]) != charge or
                    row["provider_elapsed_ms"] is None or row["provider_elapsed_ms"] < 0):
                raise ValueError("request or cost replay mismatch")
            reserved += charge; own.append(row); position += 1
            if (position > 30 or reserved > CAP or len(own) > unit["max_attempts"] or
                    sum(Decimal(r["reserved_cny"]) for r in own) > Decimal(unit["local_cap_cny"])):
                raise ValueError("frozen attempt/cost ceiling exceeded")
            if (row["status"], row["error"]) != await cursor.consume(row, plan):
                raise ValueError("response replay mismatch")
        expected = cursor.outcome(mode, own)
        saved = dict(jobs[index]); elapsed = saved.pop("local_rebuild_elapsed_ms")
        if saved != expected or not isinstance(elapsed, (int, float)) or elapsed < 0:
            raise ValueError("job outcome replay mismatch")
        if cursor.failure is not None:
            failure = {"job_id": unit["job_id"], **cursor.failure}
    if (len(jobs) != 9 or position != len(calls) or manifest["totals"] != totals(calls) or
            manifest["failure"] != failure or manifest["status"] != ("completed" if failure is None else "stopped_on_failure") or
            manifest["actual_model_calls"] != (position if mode == "real_model" else 0) or
            manifest["simulated_provider_attempts"] != (position if mode == "offline_simulation" else 0)):
        raise ValueError("common totals/provenance mismatch")
    return manifest


def run_real(root, prepared, output, receipt):
    plan = preflight(root, prepared, verify=True)
    check_authorization(plan, receipt)
    if output.exists() or claim_path(root, plan).exists():
        raise ValueError("round already used; no automatic rerun")
    # This is the only credential read; no labels are read by execution.
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise ValueError("DEEPSEEK_API_KEY required after authorization")
    return asyncio.run(execute(root, prepared, output, DeepSeekProvider(key, PRICE), mode="real_model", receipt=receipt))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "preflight-verify", "verify", "real"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--authorization", type=Path)
    args = parser.parse_args(argv)
    if args.command in {"prepare", "preflight-verify"}:
        result = preflight(args.root, args.preflight, verify=args.command != "prepare")
    elif args.command == "verify":
        result = asyncio.run(verify(args.root, args.preflight, args.output))
    else:
        result = run_real(args.root, args.preflight, args.output, read(args.authorization))
    print(json.dumps({key: result.get(key) for key in ("status", "actual_model_calls")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
