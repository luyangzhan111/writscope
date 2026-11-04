"""Pure evaluator arithmetic. Caller must verify run provenance before using it.

No file access, provider access, real-run verification, or reporting entry point.
"""
from collections import Counter
from writscope.events import JudgmentLabels

AXES = ("impact", "authorization", "decision")


def aggregate(jobs, references, pairs):
    """Aggregate explicit planned jobs and evaluator references, without filtering failures."""
    keys = [(j["case_id"], j["method"], j["track"], j["repeat_index"]) for j in jobs]
    if not keys or len(keys) != len(set(keys)):
        raise ValueError("empty or duplicate planned job")
    pair_members = []
    for ids in pairs.values():
        if len(ids) != 2 or ids[0] == ids[1] or any(i not in references for i in ids):
            raise ValueError("invalid pair")
        pair_members.extend(ids)
    if len(pair_members) != len(set(pair_members)):
        raise ValueError("case reused in pairs")
    for case, ref in references.items():
        JudgmentLabels.model_validate(ref["expected"])
        if not isinstance(ref["disputed"], bool):
            raise ValueError("invalid disputed flag")
    for job in jobs:
        if job["case_id"] not in references or job["status"] not in {"completed", "failed", "not_run"}:
            raise ValueError("invalid job")
        if not isinstance(job["repeat_index"], int) or isinstance(job["repeat_index"], bool) or job["repeat_index"] < 1:
            raise ValueError("invalid repeat")
        if (job["status"] == "completed") != (job["prediction"] is not None):
            raise ValueError("invalid final output status")
        if job["prediction"] is not None:
            JudgmentLabels.model_validate(job["prediction"])
    # Reject omitted tasks rather than silently changing planned denominators.
    groups = {(j["method"], j["track"]) for j in jobs}
    repeats = {j["repeat_index"] for j in jobs}
    cases = set(references)
    if set(keys) != {(case, method, track, repeat) for case in cases for method, track in groups for repeat in repeats}:
        raise ValueError("planned job denominator mismatch")

    def metric(rows, pred):
        return {"numerator": sum(bool(pred(r)) for r in rows), "denominator": len(rows),
                "no_valid_output": sum(r["prediction"] is None for r in rows)}

    def ref(row):
        return references[row["case_id"]]["expected"]

    def matches(row, axis):
        return row["prediction"] is not None and row["prediction"][axis] == ref(row)[axis]

    def all_axes(row):
        return all(matches(row, a) for a in AXES)

    output = {}
    for method, track in sorted(groups):
        rows = [j for j in jobs if (j["method"], j["track"]) == (method, track)]
        clear = [j for j in rows if not references[j["case_id"]]["disputed"] and ref(j)["authorization"] != "ambiguous"]
        breaking = [j for j in clear if ref(j)["impact"] == "breaking"]
        unpermitted = [j for j in breaking if ref(j)["authorization"] == "not_covered"]
        legal = [j for j in clear if ref(j)["decision"] == "allow"]
        amb = [j for j in rows if not references[j["case_id"]]["disputed"] and ref(j)["authorization"] == "ambiguous"]
        index = {(j["case_id"], j["repeat_index"]): j for j in rows}
        paired = [(index[(a, repeat)], index[(b, repeat)]) for a, b in pairs.values() for repeat in sorted(repeats)]
        counts = Counter(j["status"] for j in rows)
        output[f"{method}/{track}"] = {
            "coverage": {"planned": len(rows), "completed": counts["completed"], "failed": counts["failed"], "not_run": counts["not_run"]},
            "impact": metric(rows, lambda r: matches(r, "impact")),
            "authorization_main": metric(breaking, lambda r: matches(r, "authorization")),
            "authorization_all": metric(clear, lambda r: matches(r, "authorization")),
            "decision": metric(clear, lambda r: matches(r, "decision")),
            "all_axes": metric(clear, all_axes),
            "false_allow": metric(unpermitted, lambda r: r["prediction"] is not None and r["prediction"]["decision"] == "allow"),
            "false_block": metric(legal, lambda r: r["prediction"] is not None and r["prediction"]["decision"] in {"revise", "ask", "investigate"}),
            "legal_not_allowed": metric(legal, lambda r: r["prediction"] is None or r["prediction"]["decision"] != "allow"),
            "ambiguous_ask": metric(amb, lambda r: matches(r, "decision")),
            "ambiguous_authorization": metric(amb, lambda r: matches(r, "authorization")),
            "ask_overuse": metric(clear, lambda r: r["prediction"] is not None and r["prediction"]["decision"] == "ask"),
            "paired_all_axes": {"numerator": sum(all(all_axes(r) for r in pair) for pair in paired), "denominator": len(paired),
                                "no_valid_output": sum(any(r["prediction"] is None for r in pair) for pair in paired)},
            "paired_decision": {"numerator": sum(all(matches(r, "decision") for r in pair) for pair in paired),
                                "denominator": len(paired),
                                "no_valid_output": sum(any(r["prediction"] is None for r in pair) for pair in paired)},
        }
    return output
