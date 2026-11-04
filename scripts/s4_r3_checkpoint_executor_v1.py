"""Versioned checkpoint-enabled offline candidate executor; legacy source retained."""

import argparse
import json
import time
from pathlib import Path

from pydantic import ValidationError

from scripts.m3k_history_lookup import HistoryLookupSession, LookupPolicy, LookupRequest
from scripts.s4_p2 import METHODS, checked_bundle
from scripts.s4_p2_scope import QualifiedEngine
from scripts.s4_prompt_candidate import STAGES, adapter, preview, prompt
from scripts.s4_shared_transport_offline import SharedIntegrityError
from scripts.s4_synthetic_cost_wire import SyntheticBudgetStop
from writscope.artifacts import canonical_hash, digest, safe_path
from writscope.contracts import parse_json, validate_output
from writscope.events import DecisionPoint
from writscope.providers.mock import Script

ROOT = Path(__file__).resolve().parents[1]
AXES = ("impact", "authorization", "decision")


def bindings():
    files = ["scripts/s4_candidate_cost_mock_runner.py", "scripts/s4_synthetic_cost_wire.py", "scripts/s4_synthetic_cost_ledger.py", "scripts/s4_shared_transport_offline.py", "scripts/s4_prompt_candidate.py", "scripts/s4_p2.py",
             "scripts/s4_p2_scope.py", "scripts/s4_value_scope.py", "scripts/m3j_transition_batch.py",
             "scripts/m3f_scope_policy.py", "scripts/m3k_history_lookup.py", "uv.lock"]
    files += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "src/writscope").rglob("*.py"))]
    files += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "prompts/s4_candidate").glob("*.md"))]
    return {p: digest((ROOT / p).read_bytes()) for p in files}


def execute(bundle_root, scripts_path, cases, methods, max_attempts, dispatch, checkpoint):
    def record(case, method, kind, payload):
        try:
            checkpoint(case, method, kind, payload)
        except Exception as exc:
            raise SharedIntegrityError("checkpoint persistence failed") from exc
    if (not cases or len(cases) != len(set(cases)) or not methods or len(methods) != len(set(methods))
            or any(m not in METHODS for m in methods) or max_attempts < 1):
        raise ValueError("invalid planned jobs or attempt limit")
    if any(len(c) != 6 or not c.startswith("s4_") or not c[3:].isdigit() for c in cases):
        raise ValueError("invalid case ID")
    raw = scripts_path.read_bytes()
    dataset = parse_json(raw.decode("utf-8"))
    if dataset.get("version") != "s4_p2_scripts_v1":
        raise ValueError("unsupported scripted input")
    bundles = {c: checked_bundle(safe_path(bundle_root, c)) for c in cases}
    if any(bundle.review_input.case_id != case for case, bundle in bundles.items()):
        raise ValueError("case identity mismatch")
    scripts = {f"{c}/{m}": Script.model_validate(dataset["jobs"][f"{c}/{m}"])
               for c in cases for m in methods}
    for script in scripts.values():
        if script.execution_kind != "offline_mock" or any(s.delay_seconds != 0 or s.response_ref is not None for s in script.steps):
            raise ValueError("only inline zero-delay offline scripts are accepted")
    source = bindings()
    config = {"cases": list(cases), "methods": list(methods), "max_attempts": max_attempts,
              "prompt_sha256": {s: digest(prompt(s).encode("utf-8")) for s in STAGES},
              "failure_policy": "job_error_stops_job_budget_stops_round",
              "terminal_policy": "incremental_state_agreement_candidate_not_frozen"}
    config_hash = canonical_hash(config)
    result = {"version": "s4_candidate_cost_mock_v1", "execution_kind": "offline_mock",
              "phase": "running", "config": config, "config_sha256": config_hash,
              "source_binding": source, "scripts_sha256": digest(raw),
              "bundles": {c: bundles[c].sha256 for c in cases}, "jobs": [], "round_integrity_failure": None,
              "ledger": {"attempts_scripted": 0, "lookups": 0, "actual_model_calls": 0,
                         "provider_tokens": None, "provider_billed_cost": None}}
    stopped = False
    for case in cases:
        bundle = bundles[case]
        for method in methods:
            row = {"case_id": case, "method": method, "status": "not_run", "failure": None,
                   "attempts": [], "lookups": [], "transitions": [], "assessment": None, "final": None}
            result["jobs"].append(row)
            if stopped:
                row["failure"] = {"kind": "round_integrity_failure" if result["round_integrity_failure"] else "round_budget_exhausted", "detail": "no attempt made"}
                continue
            steps, position, lookup_count = scripts[f"{case}/{method}"].steps, 0, 0

            def call(stage, request, allow_lookup):
                nonlocal position, stopped
                if result["ledger"]["attempts_scripted"] >= max_attempts:
                    stopped = True
                    raise RuntimeError("round_budget_exhausted")
                attempt = {"stage": stage, "request": request, "response": None, "error": None,
                           "synthetic_usage": None}
                row["attempts"].append(attempt)
                result["ledger"]["attempts_scripted"] += 1
                def gated_dispatch(step):
                    nonlocal stopped
                    try:
                        dispatch(case, method, stage, request, step)
                    except SyntheticBudgetStop:
                        # Nothing was dispatched or reserved for this attempt.
                        row["attempts"].pop()
                        result["ledger"]["attempts_scripted"] -= 1
                        stopped = True
                        raise RuntimeError("round_budget_exhausted") from None
                if position >= len(steps):
                    gated_dispatch(None)
                    attempt["error"] = "script_exhausted"
                    raise ValueError("script_exhausted")
                step = steps[position]
                position += 1
                attempt["synthetic_usage"] = step.synthetic_usage.model_dump(mode="json") if step.synthetic_usage else None
                if step.action == "response":
                    attempt["response"] = step.text
                gated_dispatch(step)
                if step.action == "error":
                    attempt["error"] = step.text
                    raise ValueError("script_provider_error")
                parsed = parse_json(step.text)
                adapter(stage, allow_lookup).validate_python(parsed)
                return parsed

            def stage_call(stage, point, **kwargs):
                nonlocal lookup_count
                allow = lookup_count < 3
                request = preview(safe_path(bundle_root, case), stage, allow_lookup=allow, **kwargs)
                response = call(stage, request, allow)
                if response["kind"] != "lookup":
                    return response
                session = HistoryLookupSession(bundle.review_input.events, point,
                    LookupPolicy(max_requests=3, max_events_per_request=2), initial_attempts=lookup_count)
                record(case, method, "lookup_started", {"event_ids":response["event_ids"]})
                lookup_started = time.perf_counter()
                lookup_record = session.lookup(LookupRequest.model_validate({"event_ids": response["event_ids"]}))
                lookup_count += 1
                result["ledger"]["lookups"] += 1
                # Elapsed timing is not part of deterministic scripted replay.
                lookup = lookup_record.model_dump(mode="json", exclude={"elapsed_ms"})
                record(case, method, "lookup_finished", {"lookup":lookup,"elapsed_ms":(time.perf_counter()-lookup_started)*1000})
                row["lookups"].append(lookup)
                if lookup_record.status != "succeeded":
                    raise ValueError("history_lookup_denied:" + str(lookup_record.denial_reason))
                continuation = preview(safe_path(bundle_root, case), stage, allow_lookup=False, **kwargs)
                continuation["public_input"]["lookup_response"] = [e.model_dump(mode="json") for e in lookup_record.events]
                return call(stage, continuation, False)

            try:
                record(case, method, "job_started", {"config_sha256":config_hash})
                if method == "incremental":
                    engine = QualifiedEngine(bundle, config_hash)
                    for index, event in enumerate(bundle.review_input.events):
                        point = DecisionPoint(event_id=event.event_id, seq=event.seq)
                        response = stage_call("incremental_extract", point, event_index=index,
                            prior_state=engine.engine.snapshot(), prior_value_scopes=engine.snapshot()["value_scopes"])
                        record(case, method, "transition_started", {"event_id":event.event_id,"event_index":index})
                        transition_started = time.perf_counter()
                        transition = engine.submit(json.dumps({"items": response["items"]}, ensure_ascii=False),
                                                   index, f"{case}_{event.event_id}")
                        record(case, method, "transition_finished", {"transition":transition,"elapsed_ms":(time.perf_counter()-transition_started)*1000})
                        row["transitions"].append(transition)
                        if not transition["batch"]["accepted"]:
                            raise ValueError("state_batch_rejected:" + str(transition["batch"]["rejection_reason"]))
                    record(case, method, "assessment_started", {})
                    assessment_started = time.perf_counter()
                    row["assessment"] = engine.assess()
                    record(case, method, "assessment_finished", {"assessment":row["assessment"],"elapsed_ms":(time.perf_counter()-assessment_started)*1000})
                    response = stage_call("incremental_final", bundle.review_input.decision_point,
                                          maintained_state=engine.snapshot())
                else:
                    response = stage_call(method, bundle.review_input.decision_point)
                record(case, method, "final_validation_started", {})
                validation_started = time.perf_counter()
                final = validate_output(json.dumps(response["payload"], ensure_ascii=False), bundle,
                                        "full_checklist" if method == "full_checklist" else "full_direct",
                                        output_contract="source_quote_v1")
                if method == "incremental" and any(getattr(final, axis) != row["assessment"][axis] for axis in AXES):
                    raise ValueError("final_state_disagreement")
                record(case, method, "final_validation_finished", {"final":final.model_dump(mode="json"),"elapsed_ms":(time.perf_counter()-validation_started)*1000})
                row["final"] = final.model_dump(mode="json")
                row["status"] = "completed"
                record(case, method, "job_finished", {"status":"completed"})
            except SharedIntegrityError as exc:
                stopped = True
                result["round_integrity_failure"] = str(exc)
                row["status"] = "failed" if row["attempts"] else "not_run"
                row["final"] = None
                row["failure"] = {"kind": "shared_integrity_failure", "detail": str(exc)}
            except (ValueError, TypeError, KeyError, RuntimeError, ValidationError) as exc:
                row["status"] = "not_run" if str(exc) == "round_budget_exhausted" and not row["attempts"] else "failed"
                row["failure"] = {"kind": type(exc).__name__, "detail": str(exc)}
            if row["status"] != "completed" and not result["round_integrity_failure"]:
                try:
                    record(case, method, "job_finished", {"status":row["status"],"failure":row["failure"]})
                except SharedIntegrityError as exc:
                    stopped = True
                    result["round_integrity_failure"] = str(exc)
    result["phase"] = "completed"
    return result


