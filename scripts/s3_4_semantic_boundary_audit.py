"""Evaluator-only predicate adjudication and synthetic lifecycle boundaries."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from scripts import s3_4_state_alignment as alignment
from scripts.m3j_transition_batch import BatchStateEngine, TransitionBatch, replay_batches
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import SourceQuote, load_bundle, parse_json
from writscope.events import ConversationEvent, Provenance
from writscope.state import TransitionProposal, assess_impacts

VERSION = "s3_4_semantic_boundary_audit_v1"


def _read(path: Path):
    return parse_json(path.read_text(encoding="utf-8"))


def predicate_claims(predicate: dict, layout: str) -> dict:
    """Closed reviewed layouts; no field is silently discarded or inferred."""
    if set(predicate) != {"kind", "expected", "qualifiers"} or predicate["kind"] != "response_shape":
        raise ValueError("unsupported_semantic_projection: predicate shape")
    expected, qualifiers = predicate["expected"], predicate["qualifiers"]
    if layout == "nested_http_v1":
        if (set(expected) != {"http_status", "body", "status_type", "additional_keys_allowed"} or
                set(qualifiers) != {"value_meaning", "exact_response"} or
                expected["additional_keys_allowed"] is not False or qualifiers["exact_response"] is not True):
            raise ValueError("unsupported_semantic_projection: nested exactness or fields")
        body, status, field_type, meaning = (expected["body"], expected["http_status"],
                                              expected["status_type"], qualifiers["value_meaning"])
    elif layout in {"flat_canonical_v1", "flat_type_alias_v1"}:
        type_key, meaning_key = (("status_type", "value_meaning") if layout == "flat_canonical_v1"
                                 else ("value_type", "meaning"))
        if (set(qualifiers) != {"http_status", "exact_keys", type_key, meaning_key} or
                qualifiers["exact_keys"] != ["status"]):
            raise ValueError("unsupported_semantic_projection: flat exactness or fields")
        body, status, field_type, meaning = (expected, qualifiers["http_status"],
                                              qualifiers[type_key], qualifiers[meaning_key])
    else:
        raise ValueError("unsupported_semantic_projection: unreviewed layout")
    if (not isinstance(body, dict) or set(body) != {"status"} or
            not isinstance(body["status"], str) or field_type != "string" or
            type(status) is not int or not isinstance(meaning, str) or not meaning):
        raise ValueError("unsupported_semantic_projection: invalid reviewed claim types")
    return {"kind": "response_shape", "http_status": status, "exact_body": body,
            "field_type": field_type, "additional_keys_allowed": False, "value_meaning": meaning}


def adjudicate_predicates(review: dict, sources: dict, root: Path) -> list[dict]:
    if (set(review) != {"schema_version", "visibility", "review_status", "reviewer_ids",
                       "source_spec_sha256", "source_event_id", "source_quote", "expected_claims",
                       "cases", "limits"} or
            review["schema_version"] != "s3_4_predicate_review_v1" or
            review["visibility"] != "evaluator_only" or
            review["review_status"] != "reviewed_single_project_spec" or not review["reviewer_ids"] or
            review["source_spec_sha256"] != digest((root / "specs/first-case.md").read_bytes()) or
            [row["case_id"] for row in review["cases"]] != ["c001", "c002"]):
        raise ValueError("semantic review identity or schema mismatch")
    results = []
    for row in review["cases"]:
        if set(row) != {"case_id", "annotation_hash", "observed_layout", "reference_layout",
                        "observed_predicate_sha256", "reference_predicate_sha256", "judgement", "rationale"}:
            raise ValueError("semantic review row shape mismatch")
        report, trace = sources[row["case_id"]]
        observed = trace["observed_original_policy"][0]["state"]["constraints"]
        reference = trace["reference"]["prefixes"][0]["state"]["constraints"]
        if len(observed) != 1 or len(reference) != 1:
            raise ValueError("semantic review requires one unambiguous source constraint")
        observed, reference = observed[0], reference[0]
        if (report["annotation_hash"] != row["annotation_hash"] or
                observed["source_event_id"] != review["source_event_id"] or
                reference["source_event_id"] != review["source_event_id"] or
                observed["source_quote"] != review["source_quote"] or
                reference["source_quote"] != review["source_quote"] or
                observed["subjects"] != reference["subjects"] or
                canonical_hash(observed["predicate"]) != row["observed_predicate_sha256"] or
                canonical_hash(reference["predicate"]) != row["reference_predicate_sha256"] or
                row["judgement"] != "equivalent_on_reviewed_claims" or not row["rationale"]):
            raise ValueError("semantic review source or predicate hash mismatch")
        left = predicate_claims(observed["predicate"], row["observed_layout"])
        right = predicate_claims(reference["predicate"], row["reference_layout"])
        if left != right or left != review["expected_claims"]:
            raise ValueError("reviewed predicate claims mismatch")
        results.append({"case_id": row["case_id"], "event_id": review["source_event_id"],
                        "judgement": row["judgement"], "reviewed_claims": left,
                        "observed_predicate_sha256": row["observed_predicate_sha256"],
                        "reference_predicate_sha256": row["reference_predicate_sha256"],
                        "historical_diagnostic_rewritten": False})
    return results


def _extension_batch(row: dict, case: str, config: str, annotation: dict) -> TransitionBatch:
    constraint, authorization = None, None
    if row["draft_template"] == "candidate_grant":
        authorization = deepcopy(annotation["events"][-1]["proposals"][0]["authorization"])
        authorization["authorization_id"] = row["new_id"]
        authorization["supersedes"] = row["target_ids"] if row["operation"] == "supersede" else []
    elif row["draft_template"] == "response_constraint":
        constraint = deepcopy(annotation["events"][0]["proposals"][0]["constraint"])
        constraint["constraint_id"] = row["new_id"]
        constraint["supersedes"] = row["target_ids"]
    elif row["draft_template"] is not None or row["new_id"] is not None:
        raise ValueError("unknown boundary draft template")
    proposal = TransitionProposal(
        schema_version="0.1-draft", case_id=case, source_event_id=row["event_id"],
        source_seq=row["seq"], extractor_config_hash=config, extraction_call_id=None,
        operation=row["operation"], source=SourceQuote(event_id=row["event_id"], quote=row["content"]),
        target_ids=row["target_ids"], constraint=constraint, authorization=authorization)
    return TransitionBatch(schema_version="0.1-draft", case_id=case, source_event_id=row["event_id"],
                           source_seq=row["seq"], extractor_config_hash=config,
                           extraction_call_id=None, proposals=[proposal])


def _summary(state: dict, accepted: bool, bundle) -> dict:
    authorizations = {item["authorization_id"]: item for item in state["authorizations"]}
    items = [*state["constraints"], *state["authorizations"]]
    if accepted:
        from writscope.state import AuthorizationState
        assessment = assess_impacts(AuthorizationState.model_validate(state), bundle.evidence,
                                     bundle.review_input.candidate_patch.sha256)
        scope_check = {key: getattr(assessment, key) for key in ("impact", "authorization", "decision")}
    else:
        scope_check = None  # A stopped fixture branch never emits a terminal assessment.
    try:
        alignment.project_graph(state)
        projection = "supported_active_graph"
    except ValueError as exc:
        if not str(exc).startswith("alignment_unsupported:"):
            raise
        projection = "unsupported_lifecycle"
    return {"accepted": accepted,
            "constraint_lifecycles": {item["constraint_id"]: item["lifecycle"] for item in state["constraints"]},
            "authorization_lifecycles": {key: item["lifecycle"] for key, item in authorizations.items()},
            "authorization_interpretations": {key: item["interpretation"] for key, item in authorizations.items()},
            "exception_count": len(state["exceptions"]),
            "active_exception_authorizations": sorted({item["authorization_id"] for item in state["exceptions"]
                if authorizations[item["authorization_id"]]["lifecycle"] == "active"}),
            "updated_by": {item.get("constraint_id", item.get("authorization_id")): item["updated_by"]
                           for item in items if item["updated_by"]},
            "supersedes": {item.get("constraint_id", item.get("authorization_id")): item["supersedes"]
                           for item in items if item["supersedes"]},
            "scope_check": scope_check, "v2_projection": projection}


def run_boundaries(fixture: dict, annotation: dict, bundle) -> list[dict]:
    if (set(fixture) != {"schema_version", "visibility", "execution_kind", "prefix_case_id",
                        "prefix_annotation_hash", "reviewer_ids", "review_notes", "branches"} or
            fixture["schema_version"] != "s3_4_lifecycle_boundaries_v1" or
            fixture["visibility"] != "evaluator_only" or
            fixture["execution_kind"] != "synthetic_boundary_diagnostic" or
            fixture["prefix_case_id"] != "c002" or
            fixture["prefix_annotation_hash"] != annotation["annotation_hash"] or
            not fixture["reviewer_ids"] or not fixture["review_notes"] or
            len({row["branch_id"] for row in fixture["branches"]}) != len(fixture["branches"])):
        raise ValueError("boundary fixture identity mismatch")
    results = []
    for branch in fixture["branches"]:
        if set(branch) != {"branch_id", "purpose", "events"} or not branch["events"]:
            raise ValueError("boundary branch shape mismatch")
        extensions = []
        for index, row in enumerate(branch["events"], 6):
            if (set(row) != {"event_id", "seq", "role", "content", "operation", "target_ids",
                            "draft_template", "new_id", "expected"} or row["seq"] != index or
                    row["role"] not in {"user", "tool"}):
                raise ValueError("boundary event shape or order mismatch")
            extensions.append(ConversationEvent(
                schema_version="0.1-draft", event_id=row["event_id"], seq=row["seq"],
                role=row["role"], content=row["content"],
                provenance=Provenance(origin="synthetic_fixture",
                                      authority_level="user" if row["role"] == "user" else "none",
                                      artifact_refs=[])))
        events = [*bundle.review_input.events, *extensions]
        config = canonical_hash({"version": VERSION, "branch": branch})
        engine = BatchStateEngine(branch["branch_id"], events, config, set(bundle.registry.artifacts))
        for prefix in annotation["events"]:
            record = engine.submit_batch(alignment._batch(prefix, branch["branch_id"], config))
            if not record.accepted:
                raise ValueError("reviewed boundary prefix failed")
        rows = []
        for row in branch["events"]:
            if engine.stopped:
                break
            record = engine.submit_batch(_extension_batch(row, branch["branch_id"], config, annotation))
            actual = _summary(engine.snapshot().model_dump(), record.accepted, bundle)
            expected = row["expected"]
            if (expected is None or actual != {key: value for key, value in expected.items()
                                               if key != "rejection_contains"} or
                    (record.rejection_reason is None) != record.accepted or
                    (not record.accepted and expected["rejection_contains"] not in record.rejection_reason)):
                raise ValueError(f"boundary expectation mismatch: {branch['branch_id']}/{row['event_id']}")
            rows.append({"event_id": row["event_id"], "summary": actual,
                         "record": record.model_dump(), "state": engine.snapshot().model_dump()})
        replayed = replay_batches(branch["branch_id"], events, config, engine.batch_records,
                                   set(bundle.registry.artifacts))
        if replayed != engine.snapshot():
            raise ValueError("boundary deterministic replay mismatch")
        results.append({"branch_id": branch["branch_id"], "purpose": branch["purpose"],
                        "execution_kind": "synthetic_boundary_diagnostic", "new_model_calls": 0,
                        "extension_attempts": len(rows), "not_attempted_extensions": len(extensions) - len(rows),
                        "stopped_on_rejection": engine.stopped, "rows": rows,
                        "prefix_records": [record.model_dump() for record in engine.batch_records[:5]]})
    return results


def build_report(root: Path, review_path: Path, fixture_path: Path) -> dict:
    sources, bindings = {}, {}
    # Preserve prior versions: verify both saved v2 diagnostics before new review reads.
    for case, observation in (("c001", "m3c-live-20261021"), ("c002", "m3d-c002-v2-live-20261021")):
        directory = root / "reports/local" / f"s3-4-{case}-state-v2-20261025"
        annotation_path = root / "evaluation_private/case_001" / f"s3_4_{case}_annotation_v2.json"
        report = alignment.verify_diagnostic(root, root / "reports/local" / observation,
                                              annotation_path, case, directory)
        sources[case] = report, _read(directory / "evaluator_trace.json")
        bindings[case] = {"diagnostic_sha256": digest((directory / "diagnostic.json").read_bytes()),
                          "trace_sha256": digest((directory / "evaluator_trace.json").read_bytes()),
                          "original_artifact_set_sha256": report["source_artifact_set_sha256"]}
    semantic = adjudicate_predicates(_read(review_path), sources, root)
    annotation = _read(root / "evaluation_private/case_001/s3_4_c002_annotation_v2.json")
    bundle = load_bundle(root / "reports/m1-2026-10-18/inputs/verified_impact/c002")
    boundaries = run_boundaries(_read(fixture_path), annotation, bundle)
    return {"schema_version": "0.1-draft", "version": VERSION, "visibility": "evaluator_only",
            "execution_kind": "offline_semantic_and_boundary_audit", "new_model_calls": 0,
            "semantic_review_kind": "single_project_spec_review", "sources": bindings,
            "semantic_adjudications": semantic, "synthetic_boundary_branches": boundaries,
            "review_sha256": digest(review_path.read_bytes()), "fixture_sha256": digest(fixture_path.read_bytes()),
            "source_hashes": {path: digest((root / path).read_bytes()) for path in (
                "scripts/s3_4_semantic_boundary_audit.py", "scripts/s3_4_state_alignment.py",
                "scripts/m3j_transition_batch.py", "scripts/m3f_scope_policy.py", "src/writscope/state.py")},
            "diagnostic_cost": {"annotation_minutes": None, "offline_wall_ms": None,
                                "status": "unknown_separate_from_original_method_cost"},
            "limits": ["no_independent_gold", "only_reviewed_predicate_layouts",
                       "synthetic_extensions_not_new_independent_cases", "v2_lifecycle_alignment_still_unsupported",
                       "no_historical_rescoring", "no_model_or_method_comparison"]}


def run_audit(root: Path, review_path: Path, fixture_path: Path, output: Path) -> dict:
    root, review_path, fixture_path, output = (
        path.resolve() for path in (root, review_path, fixture_path, output))
    if output.exists() or output.parent != root / "reports/local":
        raise ValueError("audit output must be a fresh direct child of reports/local")
    report = build_report(root, review_path, fixture_path)
    output.mkdir()
    write_json(output / "audit.json", report)
    return report


def verify_audit(root: Path, review_path: Path, fixture_path: Path, output: Path) -> dict:
    report = build_report(root.resolve(), review_path.resolve(), fixture_path.resolve())
    if _read(output / "audit.json") != report:
        raise ValueError("saved semantic and lifecycle audit replay mismatch")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "verify"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--review", type=Path, default=Path("evaluation_private/case_001/s3_4_predicate_review_v1.json"))
    parser.add_argument("--fixture", type=Path, default=Path("evaluation_private/case_001/s3_4_lifecycle_boundaries_v1.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    action = run_audit if args.action == "run" else verify_audit
    report = action(args.root, args.review, args.fixture, args.output)
    print(f"{report['version']}: 2 semantic reviews, {len(report['synthetic_boundary_branches'])} synthetic branches, new_model_calls=0")


if __name__ == "__main__":
    main()
