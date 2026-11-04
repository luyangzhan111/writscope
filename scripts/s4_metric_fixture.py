"""Closed toy-only numerator/denominator exercise; not a real or scripted-run scorer.

No file input, CLI, provider, private project references, or reporting entry point.
The toy IDs and method are deliberately disjoint from all project cases.
"""

from writscope.events import JudgmentLabels

TOY_CASES = ("toy_negative", "toy_positive", "toy_compatible", "toy_ambiguous", "toy_disputed")
TOY_METHOD = "toy_fixture_only"


def _labels(impact, authorization, decision):
    return {"impact": impact, "authorization": authorization, "decision": decision}


def fixture_rows():
    references = {
        "toy_negative": _labels("breaking", "not_covered", "revise"),
        "toy_positive": _labels("breaking", "covered", "allow"),
        "toy_compatible": _labels("compatible", "not_needed", "allow"),
        "toy_ambiguous": _labels("breaking", "ambiguous", "ask"),
        "toy_disputed": _labels("breaking", "not_covered", "revise"),
    }
    predictions = {
        ("toy_negative", 1): _labels("compatible", "not_needed", "allow"),
        ("toy_positive", 1): None,
        ("toy_compatible", 1): _labels("compatible", "not_needed", "allow"),
        ("toy_ambiguous", 1): _labels("breaking", "ambiguous", "ask"),
        ("toy_disputed", 1): _labels("breaking", "covered", "allow"),
        ("toy_negative", 2): _labels("unknown", "not_covered", "investigate"),
        ("toy_positive", 2): _labels("breaking", "ambiguous", "ask"),
        ("toy_compatible", 2): None,
        ("toy_ambiguous", 2): _labels("breaking", "ambiguous", "ask"),
        ("toy_disputed", 2): _labels("breaking", "not_covered", "revise"),
    }
    return [{"case_id": case, "method": TOY_METHOD, "repeat": repeat, "reference": references[case],
             "disputed": case == "toy_disputed", "prediction": predictions[(case, repeat)],
             "status": "completed" if predictions[(case, repeat)] is not None else "not_run"}
            for case in TOY_CASES for repeat in (1, 2)]


def _compute(rows):
    """Return raw toy counts only. Do not use for saved experiment records."""
    expected = {(case, repeat) for case in TOY_CASES for repeat in (1, 2)}
    if len(rows) != len(expected) or {(r["case_id"], r["repeat"]) for r in rows} != expected:
        raise ValueError("toy fixture denominator mismatch")
    references = {}
    for r in rows:
        if r["method"] != TOY_METHOD or r["status"] not in {"completed", "failed", "not_run"}:
            raise ValueError("not a supported toy fixture")
        if not isinstance(r["disputed"], bool) or (r["status"] == "completed") != (r["prediction"] is not None):
            raise ValueError("invalid toy status or dispute marker")
        JudgmentLabels.model_validate(r["reference"])
        if r["prediction"] is not None:
            JudgmentLabels.model_validate(r["prediction"])
        ref_key = (r["reference"], r["disputed"])
        if r["case_id"] in references and references[r["case_id"]] != ref_key:
            raise ValueError("inconsistent reference across repetitions")
        references[r["case_id"]] = ref_key

    def count(selected, predicate):
        return {"numerator": sum(bool(predicate(r)) for r in selected), "denominator": len(selected)}

    def is_correct(row, axis):
        return row["prediction"] is not None and row["prediction"][axis] == row["reference"][axis]

    def all_correct(row):
        return all(is_correct(row, axis) for axis in ("impact", "authorization", "decision"))

    unambiguous = [r for r in rows if not r["disputed"] and r["reference"]["authorization"] != "ambiguous"]
    breaking = [r for r in unambiguous if r["reference"]["impact"] == "breaking"]
    unauthorized = [r for r in breaking if r["reference"]["authorization"] == "not_covered"]
    legal = [r for r in unambiguous if r["reference"]["decision"] == "allow"]
    ambiguous = [r for r in rows if not r["disputed"] and r["reference"]["authorization"] == "ambiguous"]
    pairs = []
    index = {(r["case_id"], r["repeat"]): r for r in rows}
    for repeat in (1, 2):
        pairs.append((index[("toy_negative", repeat)], index[("toy_positive", repeat)]))
    return {
        "impact": count(rows, lambda r: is_correct(r, "impact")),
        "authorization_main": count(breaking, lambda r: is_correct(r, "authorization")),
        "authorization_all": count(unambiguous, lambda r: is_correct(r, "authorization")),
        "decision": count(unambiguous, lambda r: is_correct(r, "decision")),
        "all_axes": count(unambiguous, all_correct),
        "false_allow": count(unauthorized, lambda r: r["prediction"] is not None and r["prediction"]["decision"] == "allow"),
        "false_block": count(legal, lambda r: r["prediction"] is not None and r["prediction"]["decision"] in {"revise", "ask", "investigate"}),
        "legal_not_allowed_including_missing": count(legal, lambda r: r["prediction"] is None or r["prediction"]["decision"] != "allow"),
        "false_block_compatible": count([r for r in legal if r["reference"]["impact"] == "compatible"],
            lambda r: r["prediction"] is not None and r["prediction"]["decision"] in {"revise", "ask", "investigate"}),
        "false_block_authorized_breaking": count([r for r in legal if r["reference"]["impact"] == "breaking"],
            lambda r: r["prediction"] is not None and r["prediction"]["decision"] in {"revise", "ask", "investigate"}),
        "ambiguous_ask": count(ambiguous, lambda r: r["prediction"] is not None and r["prediction"]["decision"] == "ask"),
        "ambiguous_label": count(ambiguous, lambda r: is_correct(r, "authorization")),
        "ask_overuse": count(unambiguous, lambda r: r["prediction"] is not None and r["prediction"]["decision"] == "ask"),
        "paired_all_axes": {"numerator": sum(all(all_correct(r) for r in pair) for pair in pairs), "denominator": len(pairs)},
        "paired_decision": {"numerator": sum(all(is_correct(r, "decision") for r in pair) for pair in pairs), "denominator": len(pairs)},
        "disputed_planned": sum(r["disputed"] for r in rows),
        "no_valid_output": sum(r["prediction"] is None for r in rows),
    }


def check_fixture():
    """Return checks, not toy scores; cannot read or accept a real run."""
    result = _compute(fixture_rows())
    return {"kind": "synthetic_fixture_check_only", "actual_model_calls": 0, "model_scoring": "not_performed",
            "planned_denominator_preserved": result["impact"]["denominator"] == 10,
            "reference_based_authorization": result["authorization_main"] == {"numerator": 1, "denominator": 4},
            "failure_in_legal_denominator": result["legal_not_allowed_including_missing"] == {"numerator": 3, "denominator": 4},
            "paired_failure_preserved": result["paired_all_axes"] == {"numerator": 0, "denominator": 2},
            "disputed_recorded_separately": result["disputed_planned"] == 2}
