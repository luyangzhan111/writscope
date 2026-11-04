"""Run planned repetitions against existing zero-call scripts only."""
from scripts.s4_candidate_runner import execute
from scripts.s4_repeat_schedule import build
from writscope.artifacts import canonical_hash, digest


def run(bundle_root, scripts_path, cases, repetitions, max_attempts):
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
        raise ValueError("invalid scripted budget")
    schedule = build(bundle_root, cases, repetitions)
    remaining = max_attempts
    rows, rounds = [], []
    for repeat in range(1, repetitions + 1):
        if remaining:
            subrun = execute(bundle_root, scripts_path, cases, schedule["plan"]["methods"], remaining)
            remaining -= subrun["ledger"]["attempts_scripted"]
            rounds.append(subrun)
            rows.extend({"case_id": row["case_id"], "method": row["method"], "track": "A",
                         "repeat_index": repeat, "status": row["status"], "failure": row["failure"],
                         "attempts": len(row["attempts"]), "final_present": row["final"] is not None}
                        for row in subrun["jobs"])
        else:
            rows.extend({**job, "status": "not_run", "failure": {"kind": "round_budget_exhausted"},
                         "attempts": 0, "final_present": False}
                        for job in schedule["plan"]["jobs"] if job["repeat_index"] == repeat)
    if len(rows) != len(schedule["plan"]["jobs"]):
        raise ValueError("planned denominator mismatch")
    return {"version": "s4_repeat_offline_v1", "execution_kind": "offline_mock", "schedule": schedule,
            "scripts_sha256": digest(scripts_path.read_bytes()), "max_attempts": max_attempts,
            "attempts_scripted": max_attempts - remaining, "actual_model_calls": 0,
            "provider_billed_cost": None, "rows": rows, "rounds": rounds,
            "scoring": "not_scored_scripted_mock", "accuracy": None}


def verify(bundle_root, scripts_path, saved):
    if saved.get("execution_kind") != "offline_mock" or saved.get("actual_model_calls") != 0:
        raise ValueError("only offline mock records may be replayed")
    plan = saved["schedule"]["plan"]
    rebuilt = run(bundle_root, scripts_path, plan["cases"], plan["repetitions"], saved["max_attempts"])
    if rebuilt != saved:
        raise ValueError("repeat replay mismatch")
    return {"status": "verified", "run_sha256": canonical_hash(saved), "planned_jobs": len(saved["rows"])}
