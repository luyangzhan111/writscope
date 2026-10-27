"""Evaluator-only audit of the saved M3c/M3d c002 histories. No provider path."""

from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from scripts import m3d_gate
from writscope import m3c
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import load_bundle, parse_json

VERSION = "s3_4_c002_historical_audit_v1"


def _read(path: Path):
    return parse_json(path.read_text(encoding="utf-8"))


def _validate_annotation(annotation: dict, bundle, root: Path) -> None:
    expected = {"schema_version", "case_id", "review_status", "reviewer",
                "source_spec", "source_spec_sha256", "public_bundle_hash",
                "expected_operations", "context_quotes", "constraint_quote",
                "grant_quote", "subject", "grant_change", "review_note"}
    if set(annotation) != expected or annotation["schema_version"] != "s3_4_c002_historical_annotation_v1":
        raise ValueError("annotation contract mismatch")
    if (annotation["case_id"] != "c002" or
            annotation["review_status"] != "single_project_spec_review" or
            not annotation["reviewer"] or not annotation["review_note"] or
            annotation["source_spec"] != "specs/first-case.md" or
            annotation["source_spec_sha256"] != digest((root / annotation["source_spec"]).read_bytes()) or
            annotation["public_bundle_hash"] != canonical_hash(bundle.review_input.model_dump()) or
            annotation["expected_operations"] != ["add_constraint", "no_change",
                                                  "no_change", "no_change", "grant"]):
        raise ValueError("annotation identity or source mismatch")
    events = {event.event_id: event for event in bundle.review_input.events}
    quotes = [*annotation["context_quotes"],
              {"event_id": "e001", "quote": annotation["constraint_quote"]},
              {"event_id": "e005", "quote": annotation["grant_quote"]}]
    if len(annotation["context_quotes"]) != 3:
        raise ValueError("context review count mismatch")
    for item in quotes:
        event = events.get(item.get("event_id"))
        quote = item.get("quote")
        if (event is None or event.role != "user" or
                event.provenance.authority_level != "user" or
                not isinstance(quote, str) or not quote or
                event.content.count(quote) != 1):
            raise ValueError("annotation lacks unique trusted source quote")
    if (annotation["subject"] != {"protocol": "http", "method": "GET",
                                   "path": "/api/v1/profile", "api_version": "v1",
                                   "location": "response.body", "field": "status"} or
            annotation["grant_change"] != {"old_field": "status", "new_field": "state",
                                           "preserve_value_semantics": True}):
        raise ValueError("reviewed grant scope mismatch")


def _cost(manifest: dict, run_dir: Path) -> dict:
    by_case = {}
    all_calls = []
    for case_id, case in manifest["cases"].items():
        calls = [_read(run_dir / case_id / ref) for ref in case["calls"]]
        transitions = [parse_json(line) for line in
                       (run_dir / case_id / "transitions.jsonl").read_text(encoding="utf-8").splitlines()]
        if len(transitions) != len(calls):
            raise ValueError("historical transition/call count mismatch")
        all_calls.extend(calls)
        usage = [row["actual_usage"] for row in calls]
        def total(field):
            values = [row.get(field) if row else None for row in usage]
            return sum(values) if all(value is not None for value in values) else None
        estimated = [row.get("estimated_cost_cny") if row else None for row in usage]
        by_case[case_id] = {
            "attempts": len(calls), "statuses": [row["status"] for row in calls],
            "reserved_cny": str(sum((Decimal(row["reserved_estimated_cny"]) for row in calls),
                                    Decimal("0"))),
            "input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"),
            "cache_hit_input_tokens": total("cache_hit_input_tokens"),
            "cache_miss_input_tokens": total("cache_miss_input_tokens"),
            "reasoning_tokens": total("reasoning_tokens"),
            "estimated_cost_cny": str(sum((Decimal(value) for value in estimated),
                                           Decimal("0"))) if all(value is not None for value in estimated) else None,
            "provider_billed_cny": None,
            "provider_elapsed_ms": round(sum(row["elapsed_ms"] for row in calls), 3),
            "local_state_update_elapsed_ms": round(sum(row["update_elapsed_ms"] for row in transitions), 3),
        }
        row = by_case[case_id]
        if (row["input_tokens"] is not None and
                row["cache_hit_input_tokens"] is not None and
                row["cache_miss_input_tokens"] is not None and
                row["input_tokens"] != row["cache_hit_input_tokens"] + row["cache_miss_input_tokens"]):
            raise ValueError("cache usage does not reconcile")
        if any(item is None or item.get("billed_cost_cny") is not None for item in usage):
            raise ValueError("historical provider billing provenance changed")
    if (len(all_calls) != manifest["attempts"] or
            str(sum((Decimal(row["reserved_cny"]) for row in by_case.values()),
                    Decimal("0"))) != manifest["reserved_estimated_cny"]):
        raise ValueError("historical attempt or reservation mismatch")
    for field in ("input_tokens", "output_tokens", "estimated_cost_cny"):
        rows = [row[field] for row in by_case.values()]
        subtotal = (str(sum((Decimal(value) for value in rows), Decimal("0")))
                    if field == "estimated_cost_cny" else sum(rows)) if all(
                        value is not None for value in rows) else None
        saved = manifest["usage"][field]
        if saved["unknown_attempts"] or subtotal is None or Decimal(str(subtotal)) != Decimal(saved["known_subtotal"]):
            raise ValueError(f"historical {field} usage mismatch")
    return {"attempts": manifest["attempts"], "by_case": by_case,
            "reserved_cny": manifest["reserved_estimated_cny"],
            "estimated_cost_cny": manifest["usage"]["estimated_cost_cny"]["known_subtotal"],
            "provider_billed_cny": None, "provider_bill_status": "unknown",
            "reasoning_tokens": None, "reconciled": True}


def _entries(run_dir: Path):
    return [parse_json(line) for line in
            (run_dir / "c002/transitions.jsonl").read_text(encoding="utf-8").splitlines()]


def build_report(root: Path, old_run: Path, new_run: Path, annotation_path: Path) -> dict:
    # The two original, versioned replay validators run before any private read.
    old = m3c.verify_run(root, old_run)
    new = m3d_gate.verify_run(root, new_run)
    if (old["status"] != "stopped_on_failure" or
            old["cases"]["c002"]["status"] != "incomplete" or
            new["status"] != "completed" or
            new["cases"]["c002"]["status"] != "completed"):
        raise ValueError("historical run status mismatch")
    bundle = load_bundle(root / "reports/m1-2026-10-18/inputs/verified_impact/c002")
    annotation = _read(annotation_path)
    _validate_annotation(annotation, bundle, root)
    old_entries, new_entries = _entries(old_run), _entries(new_run)
    if len(old_entries) != 5 or len(new_entries) != 5:
        raise ValueError("c002 event count mismatch")
    rows = []
    for i, event in enumerate(bundle.review_input.events):
        old_proposal = old_entries[i]["proposal"]
        new_proposal = new_entries[i]["proposal"]
        expected = annotation["expected_operations"][i]
        rows.append({"event_id": event.event_id, "expected_operation": expected,
                     "old_operation": old_proposal["operation"],
                     "old_accepted": old_entries[i]["transition"]["accepted"],
                     "new_operation": new_proposal["operation"],
                     "new_accepted": new_entries[i]["transition"]["accepted"],
                     "old_operation_matches": old_proposal["operation"] == expected,
                     "new_operation_matches": new_proposal["operation"] == expected})
    first = new_entries[0]["proposal"]
    if (first["operation"] != "add_constraint" or
            first["source"]["quote"] != annotation["constraint_quote"] or
            first["constraint"]["subjects"] != [annotation["subject"]] or
            first["constraint"]["predicate"]["expected"] != {"status": "active"} or
            first["constraint"]["predicate"]["kind"] != "response_shape"):
        raise ValueError("new c002 initial contract differs from reviewed core")
    grant = new_entries[4]["proposal"]
    constraint_id = first["constraint"]["constraint_id"]
    candidate_hash = bundle.review_input.candidate_patch.sha256
    expected_condition = {"kind": "candidate_sha256", "value": candidate_hash,
                          "resolution": "unknown", "evidence_refs": ["patch"]}
    if (grant["operation"] != "grant" or grant["source"]["quote"] != annotation["grant_quote"] or
            grant["target_ids"] or grant["authorization"]["subjects"] != [annotation["subject"]] or
            grant["authorization"]["change_type"] != "replace_field" or
            grant["authorization"]["change_details"] != annotation["grant_change"] or
            grant["authorization"]["conditions"] != [expected_condition] or
            grant["authorization"]["constraint_refs"] != [constraint_id] or
            grant["authorization"]["supersedes"]):
        raise ValueError("new c002 grant differs from reviewed local scope")
    state = _read(new_run / "c002/state.json")
    if (len(state["constraints"]) != 1 or len(state["authorizations"]) != 1 or
            len(state["exceptions"]) != 1 or state["constraints"][0]["lifecycle"] != "active" or
            state["exceptions"][0]["constraint_ids"] != [constraint_id] or
            state["exceptions"][0]["authorization_id"] != grant["authorization"]["authorization_id"]):
        raise ValueError("new c002 local exception state mismatch")
    old_last = old_entries[4]["proposal"]
    old_id = old_entries[0]["proposal"]["constraint"]["constraint_id"]
    old_failure = (old_last["operation"] == "supersede" and old_last["target_ids"] == [old_id] and
                   old_last["authorization"]["conditions"] == [] and
                   not old_entries[4]["transition"]["accepted"])
    if not old_failure or (old_run / "c002/assessment.json").exists():
        raise ValueError("old c002 failure boundary changed")
    new_assessment = _read(new_run / "c002/assessment.json")
    costs = {"m3c_original_run": _cost(old, old_run),
             "m3d_original_run": _cost(new, new_run)}
    return {"schema_version": "0.1-draft", "version": VERSION,
            "execution_kind": "oracle_diagnostic_historical_audit", "case_id": "c002",
            "new_model_calls": 0, "annotation_review_kind": annotation["review_status"],
            "source_artifact_hashes": {"m3c": old["artifact_set_sha256"],
                                       "m3d": new["artifact_set_sha256"]},
            "annotation_sha256": digest(annotation_path.read_bytes()),
            "public_bundle_hash": annotation["public_bundle_hash"],
            "rows": rows,
            "findings": {"m3c_e005_operation_id_domain_error": old_failure,
                         "m3c_e005_missing_candidate_condition": True,
                         "m3c_c002_terminal": "not_attempted",
                         "m3d_local_grant_matches_reviewed_scope": True,
                         "reviewed_task_context_quotes": len(annotation["context_quotes"]),
                         "m3d_task_context_persisted": bool(state["task_context"]),
                         "m3d_structural_axes": {key: new_assessment[key] for key in
                                                 ("impact", "authorization", "decision")},
                         "m3d_final_model_review": "not_run"},
            "costs": costs,
            "limits": ["single_synthetic_dev_case", "project_spec_review_not_independent_gold",
                       "legacy_extraction_only_no_final_model_review",
                       "no_full_oracle_state_compilation", "not_a_method_comparison"]}


def run_audit(root: Path, old_run: Path, new_run: Path,
              annotation_path: Path, output: Path) -> dict:
    root, old_run, new_run, annotation_path, output = (
        path.resolve() for path in (root, old_run, new_run, annotation_path, output))
    if output.exists() or output.is_relative_to(old_run) or output.is_relative_to(new_run) or output.is_relative_to(annotation_path.parent):
        raise ValueError("output must be a fresh directory outside source and private annotation")
    report = build_report(root, old_run, new_run, annotation_path)
    output.mkdir(parents=True)
    write_json(output / "audit.json", report)
    return report


def verify_audit(root: Path, old_run: Path, new_run: Path,
                 annotation_path: Path, output: Path) -> dict:
    expected = build_report(root.resolve(), old_run.resolve(), new_run.resolve(),
                            annotation_path.resolve())
    if _read(output / "audit.json") != expected:
        raise ValueError("saved historical audit replay mismatch")
    return expected


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "verify"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--old-run", type=Path, required=True)
    parser.add_argument("--new-run", type=Path, required=True)
    parser.add_argument("--annotation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    action = run_audit if args.action == "run" else verify_audit
    report = action(args.root, args.old_run, args.new_run, args.annotation, args.output)
    print(f"{report['version']}: c002 verified, new_model_calls={report['new_model_calls']}")


if __name__ == "__main__":
    main()
