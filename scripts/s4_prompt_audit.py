"""Audit candidate request previews using public bundles and empty synthetic state."""

import argparse
import json
from pathlib import Path

from scripts.s4_p2 import checked_bundle
from scripts.s4_p2_scope import QualifiedEngine
from scripts.s4_prompt_candidate import STAGES, preview
from writscope.artifacts import canonical_hash, safe_path


PRIVATE_KEYS = {"expected", "references", "reference_labels", "pair_id", "private_validation"}


def reject_private_keys(value):
    if isinstance(value, dict):
        if PRIVATE_KEYS.intersection(value):
            raise ValueError("private evaluator key in preview")
        for child in value.values():
            reject_private_keys(child)
    elif isinstance(value, list):
        for child in value:
            reject_private_keys(child)


def audit(bundle_root):
    case_reports = []
    prompt_hashes = {}
    schema_hashes = {}
    for number in range(1, 31):
        case = f"s4_{number:03d}"
        path = safe_path(bundle_root, case)
        bundle = checked_bundle(path)
        engine = QualifiedEngine(bundle, "0" * 64)
        snapshot = engine.snapshot()
        previews = {
            "full_direct": preview(path, "full_direct"),
            "full_checklist": preview(path, "full_checklist"),
            "incremental_extract": preview(path, "incremental_extract", event_index=0,
                prior_state=engine.engine.snapshot(), prior_value_scopes=snapshot["value_scopes"]),
            "incremental_final": preview(path, "incremental_final", maintained_state=snapshot),
        }
        evidence = {item["evidence_sha256"] for item in previews.values()}
        if len(evidence) != 1:
            raise ValueError("common evidence mismatch")
        events = [e.model_dump(mode="json") for e in bundle.review_input.events]
        for stage, item in previews.items():
            reject_private_keys(item["public_input"])
            visible = item["public_input"]["review_input"]["events"]
            if visible != (events[:1] if stage == "incremental_extract" else events):
                raise ValueError("unexpected history visibility")
            if item["public_input"]["review_input"]["tool_policy"]["available_tools"] != ["history_lookup"]:
                raise ValueError("inconsistent tool policy")
            ph, sh = item["prompt_sha256"], canonical_hash(item["output_schema"])
            if stage in prompt_hashes and (prompt_hashes[stage] != ph or schema_hashes[stage] != sh):
                raise ValueError("prompt or schema drift across cases")
            prompt_hashes[stage], schema_hashes[stage] = ph, sh
        case_reports.append({"case_id": case, "public_bundle_sha256": bundle.sha256,
                             "common_evidence_sha256": evidence.pop(), "event_count": len(events)})
    return {"version": "s4_prompt_contract_audit_v1", "status": "draft_offline_only",
            "cases": case_reports, "case_count": len(case_reports), "stage_previews": len(case_reports) * len(STAGES),
            "incremental_extract_coverage": "first_event_only_empty_synthetic_state",
            "incremental_final_coverage": "empty_synthetic_state_not_a_completed_trajectory",
            "prompt_sha256": prompt_hashes, "response_schema_sha256": schema_hashes,
            "isolation_scope": "public_bundle_whitelist_and_preview_key_scan_not_os_sandbox",
            "actual_model_calls": 0, "paid_execution_allowed": False, "external_transmission_allowed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, output = args.bundle_root.resolve(), args.output.resolve()
    if output.is_relative_to(root):
        raise ValueError("output overlaps method bundles")
    result = audit(root)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"audited {result['stage_previews']} previews; real model calls 0")


if __name__ == "__main__":
    main()
