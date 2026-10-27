"""Evaluator-only audit of saved M3c c001; no provider or model request path."""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts import s3_4_c002_historical_audit
from scripts.s3_4_c002_historical_audit import _cost
from writscope import m3c
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import load_bundle, parse_json

VERSION = "s3_4_c001_historical_audit_v1"


def _read(path: Path):
    return parse_json(path.read_text(encoding="utf-8"))


def _validate_annotation(annotation: dict, bundle, root: Path) -> None:
    expected = {"schema_version", "case_id", "review_status", "reviewer",
                "source_spec", "source_spec_sha256", "public_bundle_hash",
                "expected_operations", "context_quotes", "constraint_quote",
                "reaffirmation_quote", "subject", "review_note"}
    if set(annotation) != expected or annotation["schema_version"] != "s3_4_c001_historical_annotation_v1":
        raise ValueError("annotation contract mismatch")
    if (annotation["case_id"] != "c001" or
            annotation["review_status"] != "single_project_spec_review" or
            not annotation["reviewer"] or not annotation["review_note"] or
            annotation["source_spec"] != "specs/first-case.md" or
            annotation["source_spec_sha256"] != digest((root / annotation["source_spec"]).read_bytes()) or
            annotation["public_bundle_hash"] != canonical_hash(bundle.review_input.model_dump()) or
            annotation["expected_operations"] != ["add_constraint", "no_change",
                                                  "no_change", "no_change", "no_change"] or
            annotation["subject"] != {"protocol": "http", "method": "GET",
                                       "path": "/api/v1/profile", "api_version": "v1",
                                       "location": "response.body", "field": "status"}):
        raise ValueError("annotation identity or reviewed scope mismatch")
    if len(annotation["context_quotes"]) != 3:
        raise ValueError("reviewed task context count mismatch")
    events = {item.event_id: item for item in bundle.review_input.events}
    quotes = [*annotation["context_quotes"],
              {"event_id": "e001", "quote": annotation["constraint_quote"]},
              {"event_id": "e005", "quote": annotation["reaffirmation_quote"]}]
    for row in quotes:
        event = events.get(row.get("event_id"))
        quote = row.get("quote")
        if (event is None or event.role != "user" or
                event.provenance.authority_level != "user" or
                not isinstance(quote, str) or not quote or
                event.content.count(quote) != 1):
            raise ValueError("annotation lacks unique trusted source quote")


def build_report(root: Path, source_run: Path, annotation_path: Path) -> dict:
    # The original versioned replay completes before opening private review data.
    original = m3c.verify_run(root, source_run)
    if (original["status"] != "stopped_on_failure" or
            original["cases"]["c001"]["status"] != "completed" or
            original["cases"]["c002"]["status"] != "incomplete" or
            "c003" in original["cases"]):
        raise ValueError("M3c original status boundary mismatch")
    bundle = load_bundle(root / "reports/m1-2026-10-18/inputs/verified_impact/c001")
    annotation = _read(annotation_path)
    _validate_annotation(annotation, bundle, root)
    case_dir = source_run / "c001"
    entries = [parse_json(line) for line in
               (case_dir / "transitions.jsonl").read_text(encoding="utf-8").splitlines()]
    if len(entries) != len(bundle.review_input.events):
        raise ValueError("c001 prefix count mismatch")
    rows = []
    for index, (event, entry) in enumerate(zip(bundle.review_input.events, entries)):
        proposal = entry["proposal"]
        transition = entry["transition"]
        operation = annotation["expected_operations"][index]
        if (proposal["case_id"] != "c001" or proposal["source_event_id"] != event.event_id or
                proposal["source_seq"] != event.seq or proposal["operation"] != operation or
                not transition["accepted"]):
            raise ValueError(f"c001 reviewed operation mismatch at {event.event_id}")
        rows.append({"event_id": event.event_id, "expected_operation": operation,
                     "observed_operation": proposal["operation"],
                     "accepted": transition["accepted"]})
    first = entries[0]["proposal"]
    constraint = first["constraint"]
    if (first["source"]["quote"] != annotation["constraint_quote"] or
            constraint["subjects"] != [annotation["subject"]] or
            constraint["predicate"]["kind"] != "response_shape" or
            constraint["predicate"]["expected"] != {
                "http_status": 200, "body": {"status": "active"},
                "status_type": "string", "additional_keys_allowed": False} or
            constraint["predicate"]["qualifiers"].get("value_meaning") != "account enabled"):
        raise ValueError("c001 initial response contract differs from reviewed core")
    if (entries[3]["proposal"]["source"]["quote"] !=
            next(row["quote"] for row in annotation["context_quotes"] if row["event_id"] == "e004") or
            entries[4]["proposal"]["source"]["quote"] != annotation["reaffirmation_quote"]):
        raise ValueError("c001 later user quote mismatch")
    state = _read(case_dir / "state.json")
    if (len(state["constraints"]) != 1 or
            state["constraints"][0]["constraint_id"] != constraint["constraint_id"] or
            state["constraints"][0]["lifecycle"] != "active" or
            state["authorizations"] or state["exceptions"]):
        raise ValueError("c001 observed final state differs from reviewed core")
    assessment = _read(case_dir / "assessment.json")
    costs = _cost(original, source_run)
    return {"schema_version": "0.1-draft", "version": VERSION,
            "execution_kind": "oracle_diagnostic_historical_audit", "case_id": "c001",
            "new_model_calls": 0, "annotation_review_kind": annotation["review_status"],
            "source_run_artifact_set_sha256": original["artifact_set_sha256"],
            "audit_runner_sha256": digest(Path(__file__).read_bytes()),
            "cost_helper_sha256": digest(Path(s3_4_c002_historical_audit.__file__).read_bytes()),
            "annotation_sha256": digest(annotation_path.read_bytes()),
            "public_bundle_hash": annotation["public_bundle_hash"],
            "source_spec_sha256": annotation["source_spec_sha256"],
            "rows": rows,
            "findings": {"reviewed_task_context_quotes": len(annotation["context_quotes"]),
                         "observed_task_context_persisted": bool(state["task_context"]),
                         "v1_constraint_remains_active": True,
                         "no_replace_field_authorization": True,
                         "structural_axes": {key: assessment[key] for key in
                                             ("impact", "authorization", "decision")},
                         "final_model_review": "not_run"},
            "costs": costs,
            "limits": ["single_synthetic_dev_case", "project_spec_review_not_independent_gold",
                       "legacy_extraction_only_no_final_model_review",
                       "no_full_oracle_state_compilation", "not_a_method_comparison"]}


def run_audit(root: Path, source_run: Path, annotation_path: Path, output: Path) -> dict:
    root, source_run, annotation_path, output = (
        path.resolve() for path in (root, source_run, annotation_path, output))
    if (output.exists() or output.is_relative_to(source_run) or
            output.is_relative_to(annotation_path.parent)):
        raise ValueError("output must be a fresh directory outside source and private annotation")
    report = build_report(root, source_run, annotation_path)
    output.mkdir(parents=True)
    write_json(output / "audit.json", report)
    return report


def verify_audit(root: Path, source_run: Path, annotation_path: Path, output: Path) -> dict:
    expected = build_report(root.resolve(), source_run.resolve(), annotation_path.resolve())
    if _read(output / "audit.json") != expected:
        raise ValueError("saved c001 historical audit replay mismatch")
    return expected


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "verify"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--annotation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    action = run_audit if args.action == "run" else verify_audit
    report = action(args.root, args.source_run, args.annotation, args.output)
    print(f"{report['version']}: c001 verified, new_model_calls={report['new_model_calls']}")


if __name__ == "__main__":
    main()
