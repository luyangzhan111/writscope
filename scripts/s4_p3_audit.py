"""Evaluator-only audit of P3 cases and controlled pairs; no model calls."""

import argparse
import difflib
from pathlib import Path

from scripts.s4_p2 import PUBLIC_FILES, checked_bundle
from writscope.artifacts import canonical_hash, digest, safe_path, write_json
from writscope.contracts import parse_json
from writscope.events import JudgmentLabels


def audit(bundle_root, reference_path, p0_path):
    refs_raw = reference_path.read_bytes()
    refs = parse_json(refs_raw.decode("utf-8"))
    p0 = parse_json(p0_path.read_text(encoding="utf-8"))
    if refs["version"] != "s4_p3_author_draft_v1" or len(refs["cases"]) != 30 or len(refs["pairs"]) != 12:
        raise ValueError("invalid author reference inventory")
    p0_cases = {}
    for pair in p0["pairs"]:
        for case in pair["cases"]:
            p0_cases[case["case_id"]] = case["expected"]
    for case in p0["controls"]:
        p0_cases[case["case_id"]] = case["expected"]
    bundles = {}
    cases = []
    expected_ids = {f"s4_{n:03d}" for n in range(1, 31)}
    if set(refs["cases"]) != expected_ids or set(p0_cases) != expected_ids:
        raise ValueError("incomplete case inventory")
    for case in sorted(expected_ids):
        directory = safe_path(bundle_root, case)
        bundle = checked_bundle(directory)
        if bundle.review_input.case_id != case:
            raise ValueError("case identity mismatch")
        expected = refs["cases"][case]
        JudgmentLabels.model_validate(expected["expected"])
        if expected["expected"] != p0_cases[case]:
            raise ValueError("P0 author reference changed")
        if expected["review"] != "author_draft_not_independently_reviewed":
            raise ValueError("unsupported independent review claim")
        event = next((e for e in bundle.review_input.events if e.event_id == expected["source_event"]), None)
        if (event is None or event.role != "user" or event.provenance.authority_level != "user"
                or not expected["source_quote"] or expected["source_quote"] not in event.content):
            raise ValueError("reference is not grounded in visible user text")
        sources = expected.get("source_refs", [])
        if not sources or len({s["event_id"] for s in sources}) != len(sources):
            raise ValueError("missing or duplicate author reference source chain")
        for source in sources:
            cited = next((e for e in bundle.review_input.events if e.event_id == source["event_id"]), None)
            if (cited is None or cited.role != "user" or cited.provenance.authority_level != "user"
                    or not source["quote"] or source["quote"] not in cited.content):
                raise ValueError("reference source chain is not trusted visible user text")
        if expected.get("variable") == "lifecycle" and {s["event_id"] for s in sources} != {"e001", "e004", "e005"}:
            raise ValueError("lifecycle reference must include the earlier grant and update")
        if bundle.evidence.assessment != expected["expected"]["impact"]:
            raise ValueError("executed impact differs from author reference")
        paths = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
        if paths != PUBLIC_FILES:
            raise ValueError("unexpected file in method bundle")
        bundles[case] = bundle
        cases.append({"case_id": case, "impact": bundle.evidence.assessment,
                      "impact_count": len(bundle.evidence.impacts),
                      "decision_point": bundle.review_input.decision_point.model_dump(),
                      "bundle_sha256": bundle.sha256, "reference_source": expected["source_event"],
                      "reference_status": expected["review"], "semantic_review": "pending"})
    pairs = []
    grouped = []
    p0_pairs = {p["pair_id"]: p for p in p0["pairs"]}
    for pair_id, ids in sorted(refs["pairs"].items()):
        pair = p0_pairs[pair_id]
        if ids != [c["case_id"] for c in pair["cases"]] or len(ids) != 2:
            raise ValueError("pair inventory mismatch")
        a, b = (bundles[case] for case in ids)
        grouped.extend(ids)
        if (a.review_input.events[:-1] != b.review_input.events[:-1]
                or a.review_input.events[-1].role != "user" or b.review_input.events[-1].role != "user"
                or a.review_input.events[-1].content == b.review_input.events[-1].content):
            raise ValueError("paired public histories differ outside the last user event")
        if (a.review_input.snapshot != b.review_input.snapshot or a.review_input.candidate_patch != b.review_input.candidate_patch
                or a.evidence != b.evidence or a.review_input.contract_refs != b.review_input.contract_refs
                or a.contents != b.contents):
            raise ValueError("paired code, candidate, contract or effect differs")
        left, right = a.review_input.events[-1].content, b.review_input.events[-1].content
        differences = [{"operation": tag, "left": left[i:j], "right": right[k:l]}
                       for tag, i, j, k, l in difflib.SequenceMatcher(a=left, b=right).get_opcodes() if tag != "equal"]
        if pair["variable"] == "version" and left.replace("/v2/", "/v1/") != right:
            raise ValueError("version pair changes more than the version token")
        if pair["variable"] == "subject" and left.replace("legacy_status", "status") != right:
            raise ValueError("subject pair changes more than the subject token")
        if pair["variable"] == "lifecycle" and not (
                "撤销" in left and "撤销" in right and "重新允许" not in left and "重新允许" in right):
            raise ValueError("lifecycle pair must compare revoke and revoke-then-regrant")
        # For other variables the differing natural-language operation cannot
        # be certified semantically by string diff alone.
        pairs.append({"pair_id": pair_id, "cases": ids, "variable": pair["variable"],
                      "same_history_snapshot_candidate_contract_effect": True,
                      "event_text_differences": differences,
                      "semantic_single_variable_review": "pending_author_and_independent_review"})
    if len(grouped) != 24 or len(set(grouped)) != 24:
        raise ValueError("case reused across paired groups")
    return {"version": "s4_p3_audit_v1", "status": "structural_and_effect_checks_passed",
            "case_count": len(cases), "pair_count": len(pairs), "controls": len(cases)-len(grouped),
            "reference_sha256": digest(refs_raw), "p0_sha256": digest(p0_path.read_bytes()),
            "model_calls": 0, "cases": cases, "pairs": pairs,
            "limitations": ["Author labels and ambiguity remain unreviewed independently.",
                            "Pair semantic isolation is not proven by string or hash checks.",
                            "Whitelisted copies are not an operating-system sandbox."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--references", required=True, type=Path)
    parser.add_argument("--p0", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("audit output already exists")
    result = audit(args.bundle_root, args.references, args.p0)
    write_json(args.output, result)
    print(f"P3 audit: {result['case_count']} cases, {result['pair_count']} pairs; semantic review pending")


if __name__ == "__main__":
    main()
