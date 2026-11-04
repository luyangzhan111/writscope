"""Check explicit immutable job list before evaluator arithmetic."""
from scripts.s4_metric_core import aggregate
from writscope.artifacts import canonical_hash


def aggregate_against_plan(plan_envelope, jobs, references, pairs):
    plan = plan_envelope["plan"]
    if canonical_hash(plan) != plan_envelope["plan_sha256"]:
        raise ValueError("plan hash mismatch")
    planned = [(j["case_id"], j["method"], j["track"], j["repeat_index"]) for j in plan["jobs"]]
    actual = [(j["case_id"], j["method"], j["track"], j["repeat_index"]) for j in jobs]
    if not planned or len(set(planned)) != len(planned) or len(actual) != len(planned) or set(actual) != set(planned):
        raise ValueError("planned job denominator mismatch")
    return aggregate(jobs, references, pairs)
