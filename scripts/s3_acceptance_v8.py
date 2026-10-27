"""S3 v8 c004 diagnostic: test interaction v8 and final-review v2 on a single frozen trajectory."""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import time

from scripts import s3_scripted_trajectory_v4 as core
from scripts.s3_failure_summary_v1 import state_rejection_detail
from scripts import s3_lookup_case_v1
from scripts import s3_small_real_runner as helper
from scripts.m3i_task_context import TaskContextLedger
from scripts.m3j_transition_batch import BatchStateEngine
from scripts.m3k_history_lookup import LookupPolicy
from scripts.m3n_budget_envelope import AttemptLedger
from scripts.s3_interaction_v4 import build_request, InteractionRequest
from scripts.m3m_final_review import FinalReviewRequest
from writscope.providers.mock import Script, Step
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.baselines import build_request as baseline
from writscope.budget import PriceCeiling
from writscope.contracts import load_bundle, parse_json, validate_output
from writscope.m3b import _artifacts, _code_provenance
from writscope.providers import ActualUsage, ModelConfig, ProviderError
from writscope.providers.deepseek import DeepSeekProvider, wire_bytes

VERSION = "s3_acceptance_v8"
SCOPE = "S3_c004_interaction_v8_final_review_v2_diagnostic_v1"
CASES = ("c004",)
METHODS = ("incremental",)
MODEL = ModelConfig(model_id="deepseek-flash", temperature=0.0, max_output_tokens=4096)
POLICY = LookupPolicy(max_requests=2, max_events_per_request=2)
CAP = Decimal("3.00")
PRICE_URL = "https://api-docs.deepseek.com/quick_start/pricing/"
CHECKED = "2026-10-26T15:35:16+00:00"
PRICE = PriceCeiling(input_cny_per_million=Decimal("3"), output_cny_per_million=Decimal("12"),
                     source_url=PRICE_URL, checked_at_utc=CHECKED)
PROMPTS = {"full_direct": "prompts/full_direct.md", "full_checklist": "prompts/full_checklist.md",
           "interaction": "prompts/incremental_interaction_v8.md",
           "final": "prompts/incremental_final_review_v2.md"}


def read(path):
    return parse_json(path.read_text(encoding="utf-8"))


def bundle(root, case):
    return s3_lookup_case_v1.build(root) if case == "c004" else load_bundle(root / "reports/m1-2026-10-18/inputs/verified_impact" / case)


def settings(root):
    prompts = {key: (root / path).read_text(encoding="utf-8") for key, path in PROMPTS.items()}
    return prompts, core._settings(prompts["interaction"], prompts["final"], MODEL, POLICY, PRICE, Decimal("3.00"))


def prepare(root, *, with_requests=False):
    prompts, config = settings(root)
    jobs, sources, previews = [], {}, {}
    for index, case in enumerate(CASES):
        data = bundle(root, case)
        config_hash = core._config_hash(prompts["interaction"], prompts["final"], MODEL, POLICY)
        state = BatchStateEngine(case, data.review_input.events, config_hash, set(data.registry.artifacts)).snapshot()
        context = TaskContextLedger(data.review_input.events, set(data.registry.artifacts)).snapshot()
        direct = baseline(data, "full_direct", prompts["full_direct"], MODEL)
        checklist = baseline(data, "full_checklist", prompts["full_checklist"], MODEL)
        request = build_request(data, 0, state, context, prompts["interaction"], MODEL, POLICY)
        if len({direct.evidence_sha256, checklist.evidence_sha256, request.evidence_sha256}) != 1:
            raise ValueError("common evidence parity failed")
        sources[case] = {"public_bundle_sha256": data.sha256,
                         "public_evidence_sha256": direct.evidence_sha256,
                         "tool_policy_sha256": canonical_hash(data.review_input.tool_policy.model_dump())}
        if len(wire_bytes(request)) > 100000:
            raise ValueError("initial request exceeds byte ceiling; truncation forbidden")
        previews[f"job_{len(jobs)+1:03d}"] = request.model_dump()
        jobs.append({"job_id": f"job_{len(jobs)+1:03d}", "case_id": case,
                     "method": "incremental", "repeat_index": 0, "max_attempts": 8,
                     "local_cap_cny": "3.00",
                     "first_request_sha256": canonical_hash(request.model_dump())})
    modules = [Path(__file__), Path(helper.__file__), Path(core.__file__),
               Path(helper.preflight.__file__), Path(__file__).with_name("s3_lookup_offline_preflight.py"),
               root / "specs/s3-acceptance-v8.md",
               Path(__file__).with_name("s3_acceptance_report_v8.py"),
               Path(s3_lookup_case_v1.__file__), Path(__import__("scripts.s3_interaction_v4", fromlist=["x"]).__file__),
               Path(__file__).with_name("s3_acceptance_simulation_v8.py"),
               Path(__file__).with_name("s3_coverage_v1.py"),
               Path(__file__).with_name("s3_coverage_replay_compat_v1.py"),
               Path(__file__).with_name("s3_failure_summary_v1.py")]
    modules += [Path(module.__file__) for module in core.COMPONENTS]
    plan = {"version": VERSION, "status": "draft_unapproved", "authorization_scope": SCOPE,
            "case_ids": list(CASES), "methods": list(METHODS), "jobs": jobs,
            "model": MODEL.model_dump(), "comparison": "incremental_followup_only_no_method_comparison",
            "budget_matched": False, "independent_families": 1, "repeat_count": 1,
            "reference_status": "project_authored_draft_external_review_pending",
            "case_roles": {"c004": "same_family_v8_final_review_citation_development_diagnostic"},
            "not_generalization": True,
            "prior_coverage_manifest_sha256": digest((root / "reports/local/s3-coverage-followup-live-20261026/run.json").read_bytes()),
            "trigger_policy": "model_selected_history_index_only_until_lookup",
            "policy": {"max_attempts": 8, "max_wire_bytes": 100000, "max_response_bytes": 100000,
                       "timeout_seconds": 90, "concurrency": 1, "retries": 0,
                       "stop": "any_trajectory_failure_stops_round; no_retry_or_rerun",
                       "automatic_reruns": 0},
            "local_cap_cny": str(CAP), "price": PRICE.model_dump(mode="json"),
            "official_peak_usd_per_million": {"input_cache_miss": "0.3", "output": "1.2"},
            "conservative_cny_per_usd": "10_not_exchange_quote",
            "worst_case_reserved_cny": str(AttemptLedger(PRICE, Decimal("3")).quote(100000)*8),
            "public_sources": sources, "new_authorization": None, "actual_model_calls": 0,
            "response_contract": "interaction_quote_v4", "first_requests_sha256": canonical_hash(previews),
            "reservation_is_provider_hard_cap": False, "provider_bill_cny": "unknown",
            "prompt_sha256": {key: digest(text.encode()) for key, text in prompts.items()},
            "source_sha256": {path.relative_to(root).as_posix(): digest(path.read_bytes()) for path in modules},
            **_code_provenance()}
    return (plan, previews) if with_requests else plan


def preflight(root, output, *, verify=False):
    if output.resolve().parent != root.resolve() / "reports/local":
        raise ValueError("preflight must be a direct child of reports/local")
    plan, previews = prepare(root, with_requests=True)
    if verify:
        if read(output / "plan.json") != plan:
            raise ValueError("frozen common plan mismatch")
        if read(output / "first_requests.json") != previews:
            raise ValueError("frozen first requests mismatch")
    else:
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / "plan.json", plan)
        write_json(output / "first_requests.json", previews)
    manifest = {"version": VERSION, "status": "draft_unapproved", "actual_model_calls": 0,
                "plan_sha256": canonical_hash(plan), "artifacts": _artifacts(output)}
    if verify:
        if read(output / "run.json") != manifest:
            raise ValueError("preflight artifacts mismatch")
    else:
        write_json(output / "run.json", manifest)
    return plan


def check_authorization(plan, receipt, *, now=None):
    source_name = Path(__file__).relative_to(Path(__file__).resolve().parents[1]).as_posix()
    if (plan.get("version") != VERSION or plan.get("authorization_scope") != SCOPE or
            plan.get("response_contract") != "interaction_quote_v4" or
            plan.get("price") != PRICE.model_dump(mode="json") or
            plan.get("local_cap_cny") != str(CAP) or
            plan.get("source_sha256", {}).get(source_name) != digest(Path(__file__).read_bytes())):
            raise ValueError("V3 plan/source/price binding mismatch")
    expected = {"status": "approved", "source_kind": "direct_user_message", "scope": SCOPE,
                "plan_sha256": canonical_hash(plan), "case_ids": list(CASES), "methods": list(METHODS),
                "local_cap_cny": "3.00", "max_provider_attempts": 8, "planned_rounds": 1,
                "runner_sha256": digest(Path(__file__).read_bytes()), "price_source_url": PRICE_URL,
                "price_sha256": canonical_hash(plan["price"]), "response_contract": "interaction_quote_v4"}
    if not isinstance(receipt, dict) or any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("new exact-scope incremental-round authorization required")
    if not isinstance(receipt.get("user_authorization_ref"), str) or not receipt["user_authorization_ref"].strip():
        raise ValueError("direct user authorization reference required")
    try:
        checked = datetime.fromisoformat(receipt["price_rechecked_at_utc"])
        age = (now or datetime.now(timezone.utc)) - checked
        if checked.tzinfo is None or not -timedelta(minutes=5) <= age <= timedelta(hours=24):
            raise ValueError("stale price")
    except (KeyError, TypeError, ValueError):
        raise ValueError("fresh official price recheck required") from None


def claim_path(root, plan):
    return root / "reports/local/s3-v5-paid-claims" / (canonical_hash(plan) + ".json")


def claim(root, plan, output, receipt):
    path = claim_path(root, plan)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"plan_sha256": canonical_hash(plan), "output": str(output.resolve()),
              "authorization_ref": receipt["user_authorization_ref"]}
    with path.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False)



SENTINEL = "s3_v3_probe_only"


def _script(steps):
    return Script(schema_version="0.1-draft", execution_kind="offline_mock",
                  description="V2 replay of captured provider responses",
                  steps=steps or [Step(action="error", text=SENTINEL)])


async def _probe(data, config, steps):
    result = await core._execute(data, _script(steps), config)
    if result["status"] == "completed":
        return result, None
    failure = result["failure"]
    expected = SENTINEL if not steps else "mock script exhausted"
    if (failure and failure["status"] == "provider_error" and failure["error"] == expected and
            result["script_steps_consumed"] == len(steps)+(not steps)):
        call = result["calls"][-1]
        if call["attempt"] is None:
            raise ValueError("probe reached unreserved call")
        return result, call
    return result, None


def _request(call):
    cls = FinalReviewRequest if call["stage"] == "final_review" else InteractionRequest
    return cls.model_validate(call["request"])


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
        self.result, call = await _probe(self.data, self.config, self.steps)
        self.rebuild_ms += (time.perf_counter()-started)*1000
        self.pending = ({"stage": call["stage"], "event_seq": call["event_seq"],
                         "request": _request(call)} if call is not None else None)
        if call is None and self.result["status"] != "completed":
            self.failure = self.result["failure"]

    async def consume(self, row, plan):
        raw = row["response_text"]
        error = None
        status = "succeeded"
        usage_error = False
        try:
            usage = (ActualUsage.model_validate_json(json.dumps(row["usage"])) if row["usage"] is not None else None)
        except (ValueError, TypeError):
            usage, usage_error = None, True
        if raw is None:
            if row["status"] not in {"timeout", "provider_error"} or not row["error"]:
                raise ValueError("invalid saved provider failure")
            status, error = row["status"], row["error"]
        elif len(raw.encode()) > 100000 or row["finish_reason"] != "stop":
            status, error = "invalid_output", "response limit or finish reason"
        elif usage_error:
            status, error = "usage_invalid", "provider usage is invalid"
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
                    if error is None:
                        detail = state_rejection_detail(last, self.result)
                        if detail is not None:
                            error = detail["error"]
                    if error is None and status == "invalid_output":
                        matching = [step for step in self.result["steps"]
                                    if last["call_id"] in step["call_ids"]
                                    and last["event_seq"] == step["seq"]]
                        if len(matching) == 1 and matching[0]["status"] == status:
                            error = matching[0]["error"]
                    # probe() retains a later pre-send failure separately; a valid
                    # response is not relabelled as a failed provider attempt.
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
        status = "stopped_on_failure" if self.failure is not None else "completed" if completed else "running"
        value = {**self.unit, "status": status,
                 "failure": self.failure, "result": None, "accepted_events": None,
                 "lookup_evidence": "not_applicable", "state": None, "task_context": None,
                 "lookup_history": None, "cost": totals(calls)}
        if self.unit["method"] == "incremental":
            value.update(accepted_events=self.result["ledger"]["events_accepted"],
                         state=self.result["state"], task_context=self.result["task_context"],
                         lookup_history=self.result["lookup_history"],
                         transition_steps=helper._stable_steps(self.result["steps"]))
            value["result"] = self.result["final_result"] if completed else None
            value["lookup_evidence"] = (
                helper._lookup_classification(
                    {"execution_kind": mode, "status": value["status"],
                     "final_review_valid": value["result"] is not None}, self.result, calls)
                if self.result["lookup_history"] else "not_exercised")
        else:
            value["result"] = self.baseline_result if completed else None
        return value


def quote(request):
    return AttemptLedger(PRICE, Decimal("3")).quote(len(wire_bytes(request)))


def budget_failure(plan, unit, calls, own, pending):
    try:
        request = pending["request"]
        if len(wire_bytes(request)) > plan["policy"]["max_wire_bytes"]:
            raise ValueError("wire byte limit")
        charge = quote(request)
        if (len(calls) >= plan["policy"]["max_attempts"] or len(own) >= unit["max_attempts"] or
                sum(Decimal(row["reserved_cny"]) for row in calls)+charge > Decimal(plan["local_cap_cny"]) or
                sum(Decimal(row["reserved_cny"]) for row in own)+charge > Decimal(unit["local_cap_cny"])):
            raise ValueError("pre-send limit")
    except ValueError:
        return {"stage": pending["stage"], "status": "budget_exhausted", "error": "pre-send limit"}
    return None


def totals(calls):
    checked = deepcopy(calls)
    for row in checked:
        if row["usage"] is not None:
            try:
                ActualUsage.model_validate_json(json.dumps(row["usage"]))
            except (ValueError, TypeError):
                row["usage"] = None  # Raw invalid usage remains in calls.json.
    value = helper._totals(checked)
    review = [row for row in checked if row["stage"] == "review"]
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
    elif mode != "offline_simulation" or isinstance(provider, DeepSeekProvider) or receipt is not None:
        raise ValueError("invalid execution provenance")
    output.mkdir(parents=True)
    write_json(output / "plan.json", plan)
    if mode == "real_model":
        write_json(output / "authorization.json", receipt)
    manifest = {"version": VERSION, "execution_kind": mode, "status": "running",
                "plan_sha256": canonical_hash(plan), "authorization_ref": receipt["user_authorization_ref"] if mode == "real_model" else None,
                "authorization_sha256": canonical_hash(receipt) if mode == "real_model" else None,
                "usage_kind": "provider_response" if mode == "real_model" else "synthetic_adapter",
                "started_at_utc": datetime.now(timezone.utc).isoformat(), "failure": None}
    calls, jobs = [], [{**unit, "status": "not_run", "result": None} for unit in plan["jobs"]]
    suite_start, stopped, stop_job_index, failures = time.perf_counter(), False, None, []
    save(output, manifest, calls, jobs)
    for index, unit in enumerate(plan["jobs"]):
        if stopped:
            break
        cursor = Cursor(root, unit)
        await cursor.start()
        if cursor.pending is not None and canonical_hash(cursor.pending["request"].model_dump()) != unit["first_request_sha256"]:
            raise ValueError("first request changed after preflight")
        own = []
        while cursor.pending is not None:
            pending, started = cursor.pending, time.perf_counter()
            blocked = budget_failure(plan, unit, calls, own, pending)
            if blocked is not None:
                cursor.failure = blocked
                cursor.pending = None
                break
            request, charge = pending["request"], quote(pending["request"])
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
        if cursor.failure is not None:
            item = {"job_id": unit["job_id"], **cursor.failure}
            failures.append(item)
            if cursor.failure.get("status") == "budget_exhausted":
                stopped, stop_job_index = True, index
    manifest.update(status="stopped_on_failure" if stopped else "completed_with_job_failures" if failures else "completed",
                    failure=failures[0] if failures else None, failures=failures,
                    stop_job_index=stop_job_index,
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
        if not (output / "authorization.json").is_file():
            raise ValueError("missing saved authorization receipt")
        receipt = read(output / "authorization.json")
        check_authorization(plan, receipt, now=datetime.fromisoformat(manifest["started_at_utc"]))
        if (manifest.get("authorization_sha256") != canonical_hash(receipt) or
                manifest["authorization_ref"] != receipt["user_authorization_ref"] or
                manifest.get("usage_kind") != "provider_response"):
            raise ValueError("saved authorization/usage provenance mismatch")
        record = read(claim_path(root, plan))
        if record != {"plan_sha256": canonical_hash(plan), "output": str(output.resolve()),
                       "authorization_ref": manifest["authorization_ref"]} or not manifest["authorization_ref"]:
            raise ValueError("one-use launch provenance mismatch")
    elif (manifest["authorization_ref"] is not None or manifest.get("authorization_sha256") is not None or
          (output/"authorization.json").exists() or manifest.get("usage_kind") != "synthetic_adapter"):
        raise ValueError("simulation cannot claim paid authorization")
    position, failures, reserved = 0, [], Decimal("0")
    stop_job_index = next((i for i, job in enumerate(jobs)
                           if (job.get("failure") or {}).get("status") == "budget_exhausted"), None)
    if manifest.get("stop_job_index") != stop_job_index:
        raise ValueError("round stop index mismatch")
    for index, unit in enumerate(plan["jobs"]):
        if manifest["status"] == "stopped_on_failure" and stop_job_index is not None and index > stop_job_index:
            if jobs[index] != {**unit, "status": "not_run", "result": None}:
                raise ValueError("continued after round-level budget failure")
            continue
        cursor = Cursor(root, unit)
        await cursor.start()
        own = []
        while cursor.pending is not None:
            blocked = budget_failure(plan, unit, calls[:position], own, cursor.pending)
            if blocked is not None:
                cursor.failure, cursor.pending = blocked, None
                break
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
            if (position > 8 or reserved > CAP or len(own) > unit["max_attempts"] or
                    sum(Decimal(r["reserved_cny"]) for r in own) > Decimal(unit["local_cap_cny"])):
                raise ValueError("frozen attempt/cost ceiling exceeded")
            if (row["status"], row["error"]) != await cursor.consume(row, plan):
                raise ValueError("response replay mismatch")
        expected = cursor.outcome(mode, own)
        saved = dict(jobs[index]); elapsed = saved.pop("local_rebuild_elapsed_ms")
        if saved != expected or not isinstance(elapsed, (int, float)) or elapsed < 0:
            raise ValueError("job outcome replay mismatch")
        if cursor.failure is not None:
            failures.append({"job_id": unit["job_id"], "job_index": index, **cursor.failure})
    if failures:
        failures = [{k: v for k, v in row.items() if k != "job_index"} for row in failures]
    if (len(jobs) != len(plan["jobs"]) or position != len(calls) or manifest["totals"] != totals(calls) or
            manifest.get("failures") != failures or manifest.get("failure") != (failures[0] if failures else None) or
            manifest["status"] not in {"completed", "completed_with_job_failures", "stopped_on_failure"} or
            (manifest["status"] == "completed" and failures) or
            (manifest["status"] == "completed_with_job_failures" and not failures) or
            (manifest["status"] == "stopped_on_failure" and (not failures or failures[-1]["status"] != "budget_exhausted")) or
            manifest["actual_model_calls"] != (position if mode == "real_model" else 0) or
            manifest["simulated_provider_attempts"] != (position if mode == "offline_simulation" else 0)):
        raise ValueError("common totals/provenance mismatch")
    return manifest


def run_real(root, prepared, output, receipt):
    plan = preflight(root, prepared, verify=True)
    check_authorization(plan, receipt)
    if output.resolve().parent != root.resolve() / "reports/local":
        raise ValueError("run output must be a direct child of reports/local")
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

