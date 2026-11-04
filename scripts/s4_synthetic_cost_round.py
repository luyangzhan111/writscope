"""Execute repeated scripted jobs using an offline synthetic money envelope."""
import hashlib
from pathlib import Path

from scripts.s4_candidate_cost_mock_runner import bindings, execute
from scripts.s4_repeat_schedule import build
from scripts.s4_shared_transport_offline import SharedTransport
from scripts.s4_simulated_artifact_bundle import _bytes, _load
from scripts.s4_synthetic_cost_ledger import SyntheticCostLedger, validate_policy
from scripts.s4_synthetic_cost_wire import SyntheticCostGate, mock_wire
from writscope.artifacts import canonical_hash, digest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("scripts/s4_synthetic_cost_round.py", "scripts/s4_synthetic_cost_ledger.py",
           "scripts/s4_synthetic_cost_wire.py", "scripts/s4_candidate_cost_mock_runner.py")


def hashes():
    return {path: digest((ROOT / path).read_bytes()) for path in SOURCES}


def run(directory, bundle_root, scripts_path, cases, repetitions, policy):
    policy = validate_policy(policy)
    directory, bundle_root, scripts_path = Path(directory), Path(bundle_root), Path(scripts_path)
    if directory.resolve().is_relative_to(bundle_root.resolve()) or directory.resolve() == scripts_path.resolve():
        raise ValueError("output overlaps method inputs")
    schedule = build(bundle_root, cases, repetitions)
    plan = {"version": "s4_synthetic_cost_round_v1", "execution_kind": "offline_simulation",
            "schedule_sha256": schedule["plan_sha256"], "scripts_sha256": digest(scripts_path.read_bytes()),
            "policy": policy, "source_sha256": hashes()}
    plan_hash = canonical_hash(plan)
    directory.mkdir(parents=False, exist_ok=False)
    cost = SyntheticCostLedger.create(directory / "cost.sqlite", plan_hash, policy)
    transport = SharedTransport(directory / "transport", plan_hash, policy["max_attempts"])
    gate = SyntheticCostGate(cost, transport)
    rows, rounds, remaining, stop_reason = [], [], policy["max_attempts"], None
    for repeat in range(1, repetitions + 1):
        if remaining == 0 or stop_reason is not None:
            rows.extend({**job, "status": "not_run", "attempts": 0, "final_present": False}
                        for job in schedule["plan"]["jobs"] if job["repeat_index"] == repeat)
            continue

        def dispatch(case, method, stage, request, step):
            identity = {"case_id": case, "method": method, "track": "A", "repeat_index": repeat}
            gate.dispatch(canonical_hash(identity), stage, request, step)

        subrun = execute(bundle_root, scripts_path, cases, schedule["plan"]["methods"], remaining, dispatch)
        rounds.append(subrun)
        remaining -= subrun["ledger"]["attempts_scripted"]
        stop_reason = subrun["round_integrity_failure"] or cost.snapshot()["stopped"]
        rows.extend({"case_id": row["case_id"], "method": row["method"], "track": "A", "repeat_index": repeat,
                     "status": row["status"], "attempts": len(row["attempts"]), "final_present": row["final"] is not None}
                    for row in subrun["jobs"])
    result = {"version": "s4_synthetic_cost_run_v1", "execution_kind": "offline_simulation", "plan": plan,
              "plan_sha256": plan_hash, "schedule": schedule, "rows": rows, "rounds": rounds,
              "stop_reason": stop_reason, "actual_model_calls": 0, "provider_billed_cost": None, "accuracy": None}
    return result, cost, transport


def verify(result, cost, transport):
    if (result.get("version") != "s4_synthetic_cost_run_v1" or result.get("execution_kind") != "offline_simulation" or
            result.get("actual_model_calls") != 0 or result.get("accuracy") is not None or
            result["plan_sha256"] != canonical_hash(result["plan"]) or result["plan_sha256"] != cost.plan_hash or
            result["plan_sha256"] != transport.plan_hash or result["plan"]["source_sha256"] != hashes() or
            result["plan"]["policy"] != cost.policy or
            result["plan"]["schedule_sha256"] != canonical_hash(result["schedule"]["plan"])):
        raise ValueError("synthetic cost run binding mismatch")
    identity_keys = ("case_id", "method", "track", "repeat_index")
    if [{k: row[k] for k in identity_keys} for row in result["rows"]] != result["schedule"]["plan"]["jobs"]:
        raise ValueError("synthetic planned denominator mismatch")
    summaries = {(r["case_id"], r["method"], r["repeat_index"]): r for r in result["rows"]}
    attempts = []
    for repeat, subrun in enumerate(result["rounds"], 1):
        if subrun["source_binding"] != bindings() or subrun["scripts_sha256"] != result["plan"]["scripts_sha256"]:
            raise ValueError("synthetic source binding mismatch")
        if subrun["round_integrity_failure"]:
            raise ValueError("synthetic integrity failure")
        expected = [j for j in result["schedule"]["plan"]["jobs"] if j["repeat_index"] == repeat]
        if [{"case_id": j["case_id"], "method": j["method"], "track": "A", "repeat_index": repeat}
                for j in subrun["jobs"]] != expected:
            raise ValueError("synthetic subrun denominator mismatch")
        for job in subrun["jobs"]:
            row = summaries[(job["case_id"], job["method"], repeat)]
            if (row["status"] != job["status"] or row["attempts"] != len(job["attempts"]) or
                    row["final_present"] != (job["final"] is not None)):
                raise ValueError("synthetic job summary mismatch")
            identity = {"case_id": job["case_id"], "method": job["method"], "track": "A", "repeat_index": repeat}
            attempts.extend((canonical_hash(identity), a) for a in job["attempts"])
    if any(r["repeat_index"] > len(result["rounds"]) and (r["status"] != "not_run" or r["attempts"] != 0 or r["final_present"]) for r in result["rows"]):
        raise ValueError("synthetic unexecuted job mismatch")
    cost_snapshot = cost.snapshot()
    if result["stop_reason"] != cost_snapshot["stopped"]:
        raise ValueError("synthetic stop reason mismatch")
    transport_proof = transport.verify()
    if len(attempts) != len(cost_snapshot["attempts"]) or len(attempts) != transport_proof["attempts"]:
        raise ValueError("synthetic cost and transport count mismatch")
    for (job, attempt), c, t in zip(attempts, cost_snapshot["attempts"], transport.store.snapshot()["attempts"]):
        saved = _load(transport.root / f"attempt-{t['id']:06d}.json")
        if (c["job_id"] != job or t["job_id"] != job or c["stage"] != attempt["stage"] or
                t["request_hash"] != canonical_hash(attempt["request"]) or saved["stage"] != attempt["stage"] or
                c["request_hash"] != hashlib.sha256(mock_wire(attempt["request"])).hexdigest() or
                _bytes(saved["request"]) != _bytes(attempt["request"])):
            raise ValueError("synthetic cost attempt binding mismatch")
        if attempt["response"] is not None:
            if saved["http_status"] != 200 or saved["response"]["choices"][0]["message"]["content"] != attempt["response"]:
                raise ValueError("synthetic response binding mismatch")
        elif saved["http_status"] != 503 or saved["response"] != {"offline_surrogate": "scripted_error"}:
            raise ValueError("synthetic error binding mismatch")
    return {"status": "synthetic_cost_round_checked", "planned_jobs": len(result["rows"]), "attempts": len(attempts),
            "reserved_virtual_micro": cost_snapshot["reserved_micro"], "stop_reason": result["stop_reason"],
            "actual_model_calls": 0, "provider_billed_cost": None, "accuracy": None}
