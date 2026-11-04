"""Combine transactional synthetic reservations with in-memory provider transport.

Not a live provider entry point: no credentials, network or monetary budget.
"""
from scripts.s4_provider_transport_simulation import simulate
from writscope.artifacts import canonical_hash


async def call(store, plan_hash, job_id, preview, response_data, *, units=1, http_status=200):
    # Bind the complete local preview before the simulated dispatch begins.
    request_hash = canonical_hash(preview)
    attempt = store.reserve(plan_hash, job_id, request_hash, units)
    store.dispatch_started(attempt)
    try:
        result = await simulate(preview, response_data, http_status=http_status)
    except Exception as exc:
        # An ordinary error is recorded without storing provider/error bodies.
        store.finish(attempt, "failed", canonical_hash({"error_kind": type(exc).__name__}))
        return {"version": "s4_recorded_transport_simulation_v1", "execution_kind": "offline_simulation",
                "plan_sha256": plan_hash, "job_id": job_id, "attempt": attempt,
                "status": "failed", "error_kind": type(exc).__name__,
                "request_sha256": request_hash, "result": None, "actual_model_calls": 0}
    store.finish(attempt, "completed", canonical_hash(result))
    return {"version": "s4_recorded_transport_simulation_v1", "execution_kind": "offline_simulation",
            "plan_sha256": plan_hash, "job_id": job_id, "attempt": attempt, "status": "completed", "error_kind": None,
            "request_sha256": request_hash, "result": result, "actual_model_calls": 0}


def verify_record(store, record):
    if record.get("execution_kind") != "offline_simulation" or record.get("actual_model_calls") != 0:
        raise ValueError("only offline simulated records accepted")
    snapshot = store.snapshot()
    if snapshot["plan_hash"] != record.get("plan_sha256"):
        raise ValueError("plan binding mismatch")
    rows = [r for r in snapshot["attempts"] if r["id"] == record["attempt"]]
    if len(rows) != 1:
        raise ValueError("missing reservation")
    row = rows[0]
    expected_result = (canonical_hash(record["result"]) if record["result"] is not None else
                       canonical_hash({"error_kind": record["error_kind"]}))
    if (row["job_id"] != record["job_id"] or row["request_hash"] != record["request_sha256"] or row["status"] != record["status"]
            or row["response_hash"] != expected_result):
        raise ValueError("simulated record binding mismatch")
    return {"status": "bound_to_simulated_ledger", "actual_model_calls": 0}


async def replay(store, record, preview, response_data, *, http_status=200):
    """Re-execute using supplied synthetic response, never a provider call."""
    verify_record(store, record)
    if canonical_hash(preview) != record["request_sha256"]:
        raise ValueError("preview binding mismatch")
    try:
        result = await simulate(preview, response_data, http_status=http_status)
    except Exception as exc:
        if record["status"] != "failed" or type(exc).__name__ != record["error_kind"]:
            raise ValueError("synthetic replay mismatch") from None
    else:
        if record["status"] != "completed" or result != record["result"]:
            raise ValueError("synthetic replay mismatch")
    return {"status": "simulated_replay_verified", "actual_model_calls": 0}
