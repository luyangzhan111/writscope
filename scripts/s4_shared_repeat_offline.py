"""Run repeated candidate jobs with one shared, offline-only mock ledger."""
from pathlib import Path

from scripts.s4_candidate_shared_mock_runner import bindings, execute
from scripts.s4_repeat_schedule import build
from scripts.s4_simulated_artifact_bundle import _bytes, _load
from scripts.s4_shared_transport_offline import SharedIntegrityError, SharedTransport
from writscope.artifacts import canonical_hash, digest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("scripts/s4_shared_repeat_offline.py", "scripts/s4_shared_transport_offline.py",
           "scripts/s4_simulated_reservation_store.py", "scripts/s4_provider_transport_simulation.py",
           "scripts/s4_repeat_schedule.py", "scripts/s4_repeat_transport_offline.py",
           "scripts/s4_simulated_artifact_bundle.py")


def source_hashes():
    return {name: digest((ROOT / name).read_bytes()) for name in SOURCES}


def run(directory, bundle_root, scripts_path, cases, repetitions, max_attempts):
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("invalid virtual budget")
    directory, bundle_root, scripts_path = Path(directory), Path(bundle_root), Path(scripts_path)
    if directory.resolve().is_relative_to(bundle_root.resolve()) or directory.resolve() == scripts_path.resolve():
        raise ValueError("output overlaps method inputs")
    schedule = build(bundle_root, cases, repetitions)
    plan = {"version": "s4_shared_mock_plan_v1", "schedule_sha256": schedule["plan_sha256"],
            "scripts_sha256": digest(scripts_path.read_bytes()), "max_attempts": max_attempts,
            "source_sha256": source_hashes(), "execution_kind": "offline_simulation"}
    plan_hash = canonical_hash(plan)
    transport = SharedTransport(directory, plan_hash, max_attempts)
    remaining, rounds, rows, integrity_failure = max_attempts, [], [], None
    for repeat in range(1, repetitions + 1):
        if not remaining or integrity_failure:
            rows.extend({**job, "status": "not_run", "failure": {
                "kind": "round_integrity_failure" if integrity_failure else "round_budget_exhausted"},
                "attempts": 0, "final_present": False}
                for job in schedule["plan"]["jobs"] if job["repeat_index"] == repeat)
            continue

        def dispatch(case, method, stage, request, step):
            identity = {"case_id": case, "method": method, "track": "A", "repeat_index": repeat}
            transport.dispatch(canonical_hash(identity), stage, request, step)

        subrun = execute(bundle_root, scripts_path, cases, schedule["plan"]["methods"], remaining, dispatch)
        rounds.append(subrun)
        remaining -= subrun["ledger"]["attempts_scripted"]
        integrity_failure = subrun["round_integrity_failure"]
        rows.extend({"case_id": row["case_id"], "method": row["method"], "track": "A",
                     "repeat_index": repeat, "status": row["status"], "failure": row["failure"],
                     "attempts": len(row["attempts"]), "final_present": row["final"] is not None}
                    for row in subrun["jobs"])
    if len(rows) != len(schedule["plan"]["jobs"]):
        raise SharedIntegrityError("planned denominator mismatch")
    result = {"version": "s4_shared_repeat_offline_v1", "execution_kind": "offline_simulation",
              "plan": plan, "plan_sha256": plan_hash, "schedule": schedule, "rows": rows,
              "rounds": rounds, "round_integrity_failure": integrity_failure,
              "actual_model_calls": 0, "provider_billed_cost": None, "accuracy": None}
    return result, transport


def verify(result, transport):
    if (result.get("version") != "s4_shared_repeat_offline_v1" or
            result.get("execution_kind") != "offline_simulation" or result.get("actual_model_calls") != 0 or
            result.get("accuracy") is not None or result.get("round_integrity_failure") is not None or
            canonical_hash(result["plan"]) != result["plan_sha256"] or
            result["plan_sha256"] != transport.plan_hash or
            result["plan"].get("source_sha256") != source_hashes() or
            result["plan"]["schedule_sha256"] != canonical_hash(result["schedule"]["plan"])):
        raise SharedIntegrityError("shared round binding mismatch")
    expected_jobs = result["schedule"]["plan"]["jobs"]
    identity_keys = ("case_id", "method", "track", "repeat_index")
    if [{k: row[k] for k in identity_keys} for row in result["rows"]] != expected_jobs:
        raise SharedIntegrityError("shared round denominator mismatch")
    rows = {(r["case_id"], r["method"], r["repeat_index"]): r for r in result["rows"]}
    proof = transport.verify()
    attempts = []
    for repeat, subrun in enumerate(result["rounds"], 1):
        if subrun["source_binding"] != bindings() or subrun["scripts_sha256"] != result["plan"]["scripts_sha256"]:
            raise SharedIntegrityError("shared source binding mismatch")
        for job in subrun["jobs"]:
            identity = {"case_id": job["case_id"], "method": job["method"], "track": "A", "repeat_index": repeat}
            summary = rows.get((job["case_id"], job["method"], repeat))
            if (summary is None or summary["status"] != job["status"] or summary["failure"] != job["failure"] or
                    summary["attempts"] != len(job["attempts"]) or summary["final_present"] != (job["final"] is not None)):
                raise SharedIntegrityError("shared job summary mismatch")
            attempts.extend((identity, attempt) for attempt in job["attempts"])
    if any(row["repeat_index"] > len(result["rounds"]) and
           (row["status"] != "not_run" or row["attempts"] != 0 or row["final_present"]) for row in result["rows"]):
        raise SharedIntegrityError("shared unexecuted job mismatch")
    snapshot = transport.store.snapshot()
    if len(attempts) != proof["attempts"]:
        raise SharedIntegrityError("shared attempt count mismatch")
    for (identity, attempt), row in zip(attempts, snapshot["attempts"]):
        if row["job_id"] != canonical_hash(identity) or row["request_hash"] != canonical_hash(attempt["request"]):
            raise SharedIntegrityError("shared attempt identity mismatch")
        saved = _load(transport.root / f"attempt-{row['id']:06d}.json")
        if saved["stage"] != attempt["stage"] or _bytes(saved["request"]) != _bytes(attempt["request"]):
            raise SharedIntegrityError("shared attempt request mismatch")
        if attempt["response"] is not None:
            if (saved["http_status"] != 200 or saved["response"]["choices"][0]["message"]["content"] != attempt["response"]):
                raise SharedIntegrityError("shared attempt response mismatch")
        elif saved["http_status"] != 503 or saved["response"] != {"offline_surrogate": "scripted_error"}:
            raise SharedIntegrityError("shared error surrogate mismatch")
    return {**proof, "planned_jobs": len(expected_jobs), "accuracy": None}
