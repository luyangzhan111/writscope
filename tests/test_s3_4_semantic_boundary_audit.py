"""Closed predicate claims and synthetic lifecycle regression boundaries."""

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import s3_4_semantic_boundary_audit as audit
from writscope.artifacts import write_json
from writscope.contracts import load_bundle

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "evaluation_private/case_001/s3_4_predicate_review_v1.json"
FIXTURE = ROOT / "evaluation_private/case_001/s3_4_lifecycle_boundaries_v1.json"
ANNOTATION = ROOT / "evaluation_private/case_001/s3_4_c002_annotation_v2.json"


def _sources():
    result = {}
    for case in ("c001", "c002"):
        directory = ROOT / "reports/local" / f"s3-4-{case}-state-v2-20261025"
        result[case] = (audit._read(directory / "diagnostic.json"),
                        audit._read(directory / "evaluator_trace.json"))
    return result


def test_adjudication_and_six_boundary_branches_replay_without_provider(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("provider must not be constructed")

    monkeypatch.setattr(audit.alignment.m3c, "DeepSeekProvider", forbidden)
    monkeypatch.setattr(audit.alignment.m3d_gate, "DeepSeekProvider", forbidden)
    output = ROOT / "reports/local" / f"s3-4-semantic-test-{uuid4().hex}"
    report = audit.run_audit(ROOT, REVIEW, FIXTURE, output)
    assert report["new_model_calls"] == 0
    assert [row["judgement"] for row in report["semantic_adjudications"]] == [
        "equivalent_on_reviewed_claims", "equivalent_on_reviewed_claims"]
    branches = report["synthetic_boundary_branches"]
    assert [row["extension_attempts"] for row in branches] == [2, 1, 1, 1, 1, 1]
    assert [row["not_attempted_extensions"] for row in branches] == [0, 0, 0, 0, 1, 1]
    assert [row["summary"]["scope_check"]["authorization"] for row in branches[0]["rows"]] == [
        "not_covered", "covered"]
    assert branches[2]["rows"][0]["summary"]["scope_check"]["decision"] == "ask"
    assert branches[3]["rows"][0]["summary"]["scope_check"]["authorization"] == "not_covered"
    assert all(row["rows"][0]["summary"]["v2_projection"] == "unsupported_lifecycle"
               for row in branches[:4])
    assert all(row["rows"][0]["summary"]["scope_check"] is None for row in branches[4:])
    assert audit.verify_audit(ROOT, REVIEW, FIXTURE, output) == report
    changed = deepcopy(report)
    changed["semantic_adjudications"][0]["judgement"] = "unreviewed"
    write_json(output / "audit.json", changed)
    with pytest.raises(ValueError, match="saved semantic and lifecycle audit replay mismatch"):
        audit.verify_audit(ROOT, REVIEW, FIXTURE, output)


def test_closed_projection_does_not_drop_unknown_missing_or_changed_claims():
    sources = _sources()
    reference = sources["c001"][1]["reference"]["prefixes"][0]["state"]["constraints"][0]["predicate"]
    expected = audit.predicate_claims(reference, "flat_canonical_v1")
    for mutate in ("unknown", "missing", "conflicting_exact_keys"):
        changed = deepcopy(reference)
        if mutate == "unknown":
            changed["qualifiers"]["unreviewed_condition"] = "something important"
        elif mutate == "missing":
            del changed["qualifiers"]["value_meaning"]
        else:
            changed["qualifiers"]["exact_keys"] = ["status", "extra"]
        with pytest.raises(ValueError, match="unsupported_semantic_projection"):
            audit.predicate_claims(changed, "flat_canonical_v1")
    changed = deepcopy(reference)
    changed["qualifiers"]["http_status"] = 201
    assert audit.predicate_claims(changed, "flat_canonical_v1") != expected
    changed = deepcopy(reference)
    changed["qualifiers"]["value_meaning"] = "account disabled"
    assert audit.predicate_claims(changed, "flat_canonical_v1") != expected
    changed = deepcopy(reference)
    changed["expected"]["status"] = "disabled"
    assert audit.predicate_claims(changed, "flat_canonical_v1") != expected


def test_semantic_judgement_is_bound_to_raw_hashes_and_independent_claims():
    review = audit._read(REVIEW)
    changed = deepcopy(review)
    changed["cases"][0]["observed_predicate_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source or predicate hash mismatch"):
        audit.adjudicate_predicates(changed, _sources(), ROOT)
    changed = deepcopy(review)
    changed["expected_claims"]["http_status"] = 201
    with pytest.raises(ValueError, match="reviewed predicate claims mismatch"):
        audit.adjudicate_predicates(changed, _sources(), ROOT)


def test_boundary_expected_outcome_is_not_derived_from_engine_output():
    fixture = audit._read(FIXTURE)
    annotation = audit._read(ANNOTATION)
    bundle = load_bundle(ROOT / "reports/m1-2026-10-18/inputs/verified_impact/c002")
    changed = deepcopy(fixture)
    changed["branches"][0]["events"][0]["expected"]["scope_check"]["decision"] = "allow"
    with pytest.raises(ValueError, match="boundary expectation mismatch: b001/e006"):
        audit.run_boundaries(changed, annotation, bundle)
    changed = deepcopy(fixture)
    changed["branches"][1]["branch_id"] = changed["branches"][0]["branch_id"]
    with pytest.raises(ValueError, match="boundary fixture identity mismatch"):
        audit.run_boundaries(changed, annotation, bundle)


def test_prior_diagnostics_are_verified_before_new_private_review_reads(monkeypatch):
    def fail(*_args, **_kwargs):
        raise ValueError("prior diagnostic integrity failure")

    monkeypatch.setattr(audit.alignment, "verify_diagnostic", fail)
    with pytest.raises(ValueError, match="prior diagnostic integrity failure"):
        audit.build_report(ROOT, ROOT / "evaluation_private/absent-review.json",
                            ROOT / "evaluation_private/absent-fixture.json")
