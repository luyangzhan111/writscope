"""One offline trace for M3i task context, M3j batches, and M3k lookups.

Plans contain manually supplied responses. No provider or evaluator labels are read.
The terminal assessment is structural only, never a model final review.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from pydantic import Field

from scripts import m3f_scope_policy, m3i_task_context, m3j_transition_batch, m3k_history_lookup
from scripts.m3i_task_context import ContextBatch, ContextQuote, TaskContextLedger
from scripts.m3j_transition_batch import BatchStateEngine, batch_from_response
from scripts.m3k_history_lookup import HistoryLookupSession, LookupPolicy, LookupRequest
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import load_bundle, parse_json
from writscope.events import DecisionPoint, StrictModel
from writscope.m3b import _artifacts, _code_provenance
from writscope.state import assess_impacts


TRACE_VERSION = "m3l_offline_trace_v1"


class EventPlan(StrictModel):
    event_id: str = Field(min_length=1)
    context_quotes: list[ContextQuote]
    lookup_requests: list[LookupRequest]
    response_text: str = Field(min_length=1)


class TracePlan(StrictModel):
    schema_version: str
    case_id: str = Field(min_length=1)
    lookup_policy: LookupPolicy
    events: list[EventPlan] = Field(min_length=1)


def _sources() -> dict[str, str]:
    modules = (m3f_scope_policy, m3i_task_context, m3j_transition_batch,
               m3k_history_lookup)
    return {Path(module.__file__).name: digest(Path(module.__file__).read_bytes())
            for module in modules}


def _config_hash(plan: TracePlan) -> str:
    return canonical_hash({"version": TRACE_VERSION,
                           "batch_policy": m3j_transition_batch.POLICY_VERSION,
                           "context_policy": m3i_task_context.POLICY_VERSION,
                           "lookup_policy": m3k_history_lookup.POLICY_VERSION,
                           "lookup_limits": plan.lookup_policy.model_dump()})


def _execute(bundle, plan: TracePlan) -> dict:
    review = bundle.review_input
    if plan.schema_version != "0.1-draft" or plan.case_id != review.case_id:
        raise ValueError("trace plan case or schema mismatch")
    if len(plan.events) != len(review.events) or any(
            item.event_id != event.event_id for item, event in
            zip(plan.events, review.events)):
        raise ValueError("trace plan must list exactly the visible events in order")
    config_hash = _config_hash(plan)
    state = BatchStateEngine(review.case_id, review.events, config_hash,
                             set(bundle.registry.artifacts))
    context = TaskContextLedger(review.events, set(bundle.registry.artifacts))
    steps = []
    lookup_attempts = 0
    for index, (event, item) in enumerate(zip(review.events, plan.events)):
        row = {
            "seq": event.seq, "event_id": event.event_id,
            "prior_state_sha256": state.snapshot().state_hash,
            "prior_context_sha256": context.snapshot().state_hash,
            "response_sha256": digest(item.response_text.encode("utf-8")),
            "lookups": [], "context_record": None, "batch_record": None,
            "context_elapsed_ms": None, "batch_elapsed_ms": None,
            "status": "running", "error": None,
        }
        session = HistoryLookupSession(
            review.events, DecisionPoint(event_id=event.event_id, seq=event.seq),
            plan.lookup_policy, initial_attempts=lookup_attempts)
        for request in item.lookup_requests:
            lookup = session.lookup(request)
            lookup_attempts += 1
            row["lookups"].append(lookup.model_dump())
            if lookup.status != "succeeded":
                row["status"], row["error"] = "lookup_denied", lookup.denial_reason
                break
        if row["status"] == "running":
            started = time.perf_counter()
            try:
                context_record = context.submit(ContextBatch(
                    event_id=event.event_id, seq=event.seq,
                    items=item.context_quotes))
                row["context_record"] = context_record.model_dump()
            except ValueError as exc:
                row["status"], row["error"] = "context_rejected", str(exc)
            row["context_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if row["status"] == "running":
            started = time.perf_counter()
            try:
                batch = batch_from_response(item.response_text, bundle, index,
                                            config_hash, f"offline_extract_{event.seq:03d}")
                batch_record = state.submit_batch(batch)
                row["batch_record"] = batch_record.model_dump()
                if not batch_record.accepted:
                    row["status"] = "batch_rejected"
                    row["error"] = batch_record.rejection_reason
            except ValueError as exc:
                row["status"], row["error"] = "invalid_response", str(exc)
            row["batch_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if row["status"] == "running":
            row["status"] = "accepted"
        row["state_sha256"] = state.snapshot().state_hash
        row["context_sha256"] = context.snapshot().state_hash
        steps.append(row)
        if row["status"] != "accepted":
            break
    complete = len(steps) == len(review.events) and all(
        row["status"] == "accepted" for row in steps)
    assessment = None
    if complete:
        assessment = assess_impacts(state.snapshot(), bundle.evidence,
                                    review.candidate_patch.sha256).model_dump()
    return {
        "trace": steps, "state": state.snapshot().model_dump(),
        "task_context": context.snapshot().model_dump(),
        "assessment": assessment,
        "status": "completed" if complete else "stopped_on_failure",
        "lookup_attempts": lookup_attempts,
        "lookup_elapsed_ms": round(sum(lookup["elapsed_ms"] for row in steps
                                       for lookup in row["lookups"]), 3),
    }


def _normalized(steps: list[dict]) -> list[dict]:
    return [{**{key: value for key, value in row.items()
                if key not in {"context_elapsed_ms", "batch_elapsed_ms"}},
             "lookups": [{key: value for key, value in lookup.items()
                          if key != "elapsed_ms"} for lookup in row["lookups"]]}
            for row in steps]


def _manifest(bundle, plan: TracePlan, result: dict, artifacts: dict) -> dict:
    return {
        "schema_version": "0.1-draft", "execution_kind": "offline_manual_trace",
        "trace_version": TRACE_VERSION, "case_id": bundle.review_input.case_id,
        "public_bundle_sha256": bundle.sha256,
        "visible_history_sha256": canonical_hash([
            event.model_dump() for event in bundle.review_input.events]),
        "plan_sha256": canonical_hash(plan.model_dump()),
        "config_sha256": _config_hash(plan),
        "status": result["status"], "events_attempted": len(result["trace"]),
        "lookup_attempts": result["lookup_attempts"],
        "lookup_elapsed_ms": result["lookup_elapsed_ms"],
        "new_model_calls": 0, "provider_usage": None,
        "final_model_review": "not_run",
        "state_sha256": result["state"]["state_hash"],
        "context_sha256": result["task_context"]["state_hash"],
        "assessment_kind": "structural_scope_diagnostic" if result["assessment"] else None,
        "component_sha256": _sources(), "runner_sha256": digest(Path(__file__).read_bytes()),
        **_code_provenance(), "artifacts": artifacts,
        "artifact_set_sha256": canonical_hash(artifacts),
    }


def run_offline(bundle_dir: Path, plan: TracePlan, output: Path) -> dict:
    bundle_dir, output = bundle_dir.resolve(), output.resolve()
    if output.is_relative_to(bundle_dir):
        raise ValueError("trace output cannot modify public input")
    bundle = load_bundle(bundle_dir)
    result = _execute(bundle, plan)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "plan.json", plan.model_dump())
    write_json(output / "trace.json", result["trace"])
    write_json(output / "state.json", result["state"])
    write_json(output / "task_context.json", result["task_context"])
    if result["assessment"] is not None:
        write_json(output / "assessment.json", result["assessment"])
    manifest = _manifest(bundle, plan, result, _artifacts(output))
    write_json(output / "run.json", manifest)
    return manifest


def verify_run(bundle_dir: Path, output: Path) -> dict:
    bundle = load_bundle(bundle_dir)
    manifest = parse_json((output / "run.json").read_text(encoding="utf-8"))
    artifacts = _artifacts(output)
    if artifacts != manifest.get("artifacts"):
        raise ValueError("trace artifact hash mismatch")
    plan = TracePlan.model_validate(parse_json((output / "plan.json").read_text(encoding="utf-8")))
    result = _execute(bundle, plan)
    saved_trace = parse_json((output / "trace.json").read_text(encoding="utf-8"))
    if _normalized(saved_trace) != _normalized(result["trace"]):
        raise ValueError("trace replay mismatch")
    for name, expected in (("state.json", result["state"]),
                           ("task_context.json", result["task_context"])):
        if parse_json((output / name).read_text(encoding="utf-8")) != expected:
            raise ValueError("trace replay mismatch: " + name)
    assessment_path = output / "assessment.json"
    if assessment_path.exists() != (result["assessment"] is not None):
        raise ValueError("trace assessment presence mismatch")
    if assessment_path.exists() and parse_json(assessment_path.read_text(encoding="utf-8")) != result["assessment"]:
        raise ValueError("trace assessment replay mismatch")
    result["lookup_elapsed_ms"] = round(sum(lookup["elapsed_ms"] for row in saved_trace
                                            for lookup in row["lookups"]), 3)
    expected = _manifest(bundle, plan, result, artifacts)
    if manifest != expected:
        raise ValueError("trace manifest mismatch")
    return expected


def main(argv=None):
    parser = argparse.ArgumentParser(description="M3l combined offline trace")
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path, help="strict manual trace plan JSON")
    args = parser.parse_args(argv)
    if args.command == "run":
        if args.plan is None:
            parser.error("--plan is required for run")
        plan = TracePlan.model_validate(parse_json(args.plan.read_text(encoding="utf-8")))
        result = run_offline(args.bundle, plan, args.output)
    else:
        result = verify_run(args.bundle, args.output)
    print(json.dumps({"status": result["status"],
                      "events_attempted": result["events_attempted"],
                      "lookup_attempts": result["lookup_attempts"],
                      "new_model_calls": result["new_model_calls"]}))


if __name__ == "__main__":
    main()
