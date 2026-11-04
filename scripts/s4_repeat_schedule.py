"""Deterministic S4 repeat scheduling and attempt bounds; offline planning only."""
from scripts.s4_p2 import METHODS, checked_bundle
from writscope.artifacts import safe_path, canonical_hash


def build(bundle_root, cases, repetitions, track="A"):
    if (track != "A" or not cases or len(cases) != len(set(cases))
            or not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 1):
        raise ValueError("invalid schedule")
    lengths = {}
    for case in cases:
        if len(case) != 6 or not case.startswith("s4_") or not case[3:].isdigit():
            raise ValueError("invalid case ID")
        bundle = checked_bundle(safe_path(bundle_root, case))
        if bundle.review_input.case_id != case:
            raise ValueError("case identity mismatch")
        lengths[case] = {"event_count": len(bundle.review_input.events), "bundle_sha256": bundle.sha256}
    jobs = [{"case_id": case, "method": method, "track": track, "repeat_index": repeat}
            for repeat in range(1, repetitions + 1) for case in cases for method in METHODS]
    per_repeat = sum(4 + v["event_count"] + 1 + min(3, v["event_count"] + 1) for v in lengths.values())
    plan = {"version": "s4_repeat_schedule_v1", "status": "offline_draft_unapproved", "cases": list(cases),
            "methods": list(METHODS), "track": track, "repetitions": repetitions, "jobs": jobs,
            "bundle_info": lengths, "max_attempts": per_repeat * repetitions,
            "assumptions": "each baseline 1 final + max 1 continuation; incremental N events + final + max 3 continuations; no retry",
            "actual_model_calls": 0, "paid_execution_allowed": False, "external_transmission_allowed": False}
    return {"plan": plan, "plan_sha256": canonical_hash(plan)}
