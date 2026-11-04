"""Create evaluator-only project-author disposition from the saved P3 audit."""
import json
from pathlib import Path

from writscope.artifacts import digest

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "evaluation_private/s4_p3/references.json"
AUDIT = ROOT / "evaluation_private/s4_p3/audit-report.json"
OUT = ROOT / "evaluation_private/s4_review/project-disposition-v1.json"


def build():
    raw = REF.read_bytes()
    refs = json.loads(raw)
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    if refs.get("version") != "s4_p3_author_draft_v1" or audit["reference_sha256"] != digest(raw):
        raise ValueError("reference binding mismatch")
    expected = {f"s4_{i:03}" for i in range(1, 31)}
    if set(refs["cases"]) != expected or len(refs["pairs"]) != 12:
        raise ValueError("invalid inventory")
    cases = {}
    for case in sorted(expected):
        ref = refs["cases"][case]
        ambiguous = case in {"s4_018", "s4_020"}
        cases[case] = {"disposition": "retain_author_draft", "expected": ref["expected"],
            "source_refs": ref["source_refs"], "reason": ref["rationale"], "pair_id": ref.get("pair_id"),
            "main_authorization_and_decision": "excluded_ambiguity" if ambiguous else "include_author_draft",
            "impact": "include", "status": "project_ai_nonblind_provisional"}
    descriptions = {
        "P01":"explicit authorization, wording also changes compatibility statement",
        "P02":"version token differs; v2 absence and timing confound interpretation",
        "P03":"operation wording also changes compatibility statements",
        "P04":"subject token differs within the fixed dual-field case",
        "P05":"both revoke; regrant in same event follows written order",
        "P06":"urgency shared; compatibility wording differs",
        "P07":"explicit value authorization, wording includes client misinterpretation",
        "P08":"version token differs; v2 absence and timing confound interpretation",
        "P09":"operation and compatibility/interpretation wording differ",
        "P10":"subject token differs within the fixed dual-field case",
        "P11":"both revoke; regrant in same event follows written order",
        "P12":"urgency shared; compatibility wording differs",
    }
    pairs = {key: {"cases": ids, "disposition": "retain_development_pair_with_limitations",
                   "limitation": descriptions[key], "strict_single_variable_claim": False}
             for key, ids in sorted(refs["pairs"].items())}
    return {"version": "s4_project_disposition_v1", "status": "project_ai_nonblind_provisional_not_frozen",
            "reviewer": "project_ai_assistant", "user_case_review": "not_received",
            "independent_review": "not_performed_skipped_by_user", "reference_sha256": digest(raw),
            "audit_sha256": digest(AUDIT.read_bytes()), "cases": cases, "pairs": pairs,
            "notes": ["s4_018 and s4_020 alternatives remain unresolved; report separately",
                      "pair wording limitations do not automatically establish label disagreement",
                      "no data, reference or historical evidence rewritten"]}


if __name__ == "__main__":
    with OUT.open("x", encoding="utf-8", newline="\n") as f:
        json.dump(build(), f, ensure_ascii=False, indent=2)
        f.write("\n")
