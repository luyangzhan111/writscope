"""Read-only replay of the v2 local-mapping failure and new error summaries.

No real/prepare command, provider, credentials, labels, response repair or retry.
"""
import argparse
import asyncio
from copy import deepcopy
from pathlib import Path

from scripts import s3_common_real_v2 as frozen
from scripts.s3_failure_summary_v1 import FailureSummaryCursor, VERSION
from writscope.artifacts import canonical_hash, digest, write_json


async def diagnose(root, prepared, source):
    manifest = await frozen.verify(root, prepared, source)
    jobs = frozen.read(source / "jobs.json")
    calls = frozen.read(source / "calls.json")
    plan = frozen.read(source / "plan.json")
    rows, derived_jobs = [], []
    for job in jobs:
        own = [row for row in calls if row["job_id"] == job["job_id"]]
        if not own:
            derived_jobs.append({"job_id": job["job_id"], "status": job["status"],
                                 "failure": job.get("failure")})
            continue
        unit = next(unit for unit in plan["jobs"] if unit["job_id"] == job["job_id"])
        cursor = FailureSummaryCursor(root, unit)
        await cursor.start()
        for saved in own:
            if cursor.pending is None:
                raise ValueError("saved call after stop")
            request = cursor.pending["request"].model_dump(mode="json")
            if canonical_hash(request) != canonical_hash(saved["request"]):
                raise ValueError("request changed during summary replay")
            row = deepcopy(saved)
            status, error = await cursor.consume(row, plan)
            if status != saved["status"]:
                raise ValueError("summary adapter changed call status")
            rows.append({"call_id": saved["call_id"], "job_id": job["job_id"],
                         "stage": saved["stage"], "event_seq": saved["event_seq"],
                         "status": status, "raw_error": saved["error"], "error": error,
                         "response_text_sha256": digest(saved["response_text"].encode())
                         if saved["response_text"] is not None else None})
        outcome = cursor.outcome(manifest["execution_kind"], own)
        for key in ("status", "result", "accepted_events", "state", "task_context",
                    "lookup_history", "lookup_evidence", "cost"):
            if outcome[key] != job[key]:
                raise ValueError("summary adapter changed outcome: " + key)
        derived_jobs.append({"job_id": job["job_id"], "status": outcome["status"],
                             "failure": outcome["failure"]})
    rejected = [j for j in jobs if j["status"] == "stopped_on_failure"]
    if len(rejected) != 1:
        raise ValueError("expected the single saved failed job")
    job = rejected[0]
    step = job["transition_steps"][-1]
    batch = step["batch_record"]
    if not batch or batch["accepted"] is not False:
        raise ValueError("expected saved rejected batch")
    proposal = batch["batch"]["proposals"][batch["failed_item_index"]]
    auth = proposal["authorization"]
    if proposal["operation"] != "grant" or auth is None:
        raise ValueError("expected the saved grant diagnostic")
    failed = next(j for j in derived_jobs if j["job_id"] == job["job_id"])
    return {"version": "s3_local_mapping_diagnosis_v1", "summary_adapter": VERSION,
            "execution_kind": "offline_saved_response_diagnostic", "new_model_calls": 0,
            "source_run_sha256": digest((source / "run.json").read_bytes()),
            "source_plan_sha256": manifest["plan_sha256"],
            "source_status": manifest["status"], "source_actual_model_calls": manifest["actual_model_calls"],
            "raw_failure": manifest["failure"],
            "failure": {"job_id": job["job_id"], **failed["failure"]},
            "calls": rows, "jobs": derived_jobs,
            "reproduction": {"event_id": step["event_id"], "accepted_events": job["accepted_events"],
                             "status": step["status"], "failed_item_index": batch["failed_item_index"],
                             "source_quote": proposal["source"]["quote"],
                             "subjects": auth["subjects"], "change_details": auth["change_details"],
                             "constraint_refs": auth["constraint_refs"], "conditions": auth["conditions"],
                             "rejection_reason": batch["rejection_reason"]},
            "invariants": {"requests_responses_states_context_costs_statuses_unchanged": True,
                           "scope_policy_unchanged": True, "labels_read": False,
                           "semantic_repair": False, "new_paid_entry": False},
            "metrics": None}


async def run(root, prepared, source, output, *, verify=False):
    if output.resolve().parent != root.resolve() / "reports/local":
        raise ValueError("diagnostic output must be a direct child of reports/local")
    if output.resolve() in {source.resolve(), prepared.resolve()}:
        raise ValueError("cannot overwrite source")
    result = await diagnose(root, prepared, source)
    if verify:
        if frozen.read(output / "diagnostic.json") != result:
            raise ValueError("diagnostic replay mismatch")
    else:
        output.mkdir(exist_ok=False)
        write_json(output / "diagnostic.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = asyncio.run(run(args.root, args.preflight, args.source, args.output,
                             verify=args.command == "verify"))
    print({"status": result["source_status"], "new_model_calls": result["new_model_calls"]})


if __name__ == "__main__":
    main()
