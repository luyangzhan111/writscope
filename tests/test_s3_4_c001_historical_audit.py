"""Narrow offline checks for the M3c c001 historical audit."""

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import s3_4_c001_historical_audit as audit
from writscope.artifacts import write_json
from writscope.contracts import load_bundle


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reports/local/m3c-live-20261021"
ANNOTATION = ROOT / "evaluation_private/case_001/s3_4_c001_historical_annotation_v1.json"


def test_c001_historical_audit_and_saved_replay_are_offline(monkeypatch):
    def forbidden_provider(*_args, **_kwargs):
        raise AssertionError("provider must not be constructed")

    monkeypatch.setattr(audit.m3c, "DeepSeekProvider", forbidden_provider)
    output = ROOT / "reports/local" / f"s3-4-c001-test-{uuid4().hex}"
    report = audit.run_audit(ROOT, SOURCE, ANNOTATION, output)
    assert report["new_model_calls"] == 0
    assert [row["observed_operation"] for row in report["rows"]] == [
        "add_constraint", "no_change", "no_change", "no_change", "no_change"]
    assert report["findings"]["structural_axes"] == {
        "impact": "breaking", "authorization": "not_covered", "decision": "revise"}
    assert report["findings"]["reviewed_task_context_quotes"] == 3
    assert not report["findings"]["observed_task_context_persisted"]
    assert report["costs"]["attempts"] == 10
    assert report["costs"]["by_case"]["c001"]["attempts"] == 5
    assert audit.verify_audit(ROOT, SOURCE, ANNOTATION, output) == report
    changed = deepcopy(report)
    changed["new_model_calls"] = 1
    write_json(output / "audit.json", changed)
    with pytest.raises(ValueError, match="saved c001 historical audit replay mismatch"):
        audit.verify_audit(ROOT, SOURCE, ANNOTATION, output)


def test_c001_annotation_rejects_wrong_time_or_forged_quote():
    bundle = load_bundle(ROOT / "reports/m1-2026-10-18/inputs/verified_impact/c001")
    original = audit._read(ANNOTATION)
    forged = deepcopy(original)
    forged["constraint_quote"] = "not a public event quote"
    with pytest.raises(ValueError, match="unique trusted source quote"):
        audit._validate_annotation(forged, bundle, ROOT)
    wrong_time = deepcopy(original)
    wrong_time["context_quotes"][0] = {
        "event_id": "e001", "quote": original["reaffirmation_quote"]}
    with pytest.raises(ValueError, match="unique trusted source quote"):
        audit._validate_annotation(wrong_time, bundle, ROOT)


def test_m3c_replay_precedes_private_annotation_read(monkeypatch):
    def fail_replay(*_args, **_kwargs):
        raise ValueError("original run integrity failure")

    monkeypatch.setattr(audit.m3c, "verify_run", fail_replay)
    with pytest.raises(ValueError, match="original run integrity failure"):
        audit.build_report(ROOT, SOURCE, ROOT / "evaluation_private/missing.json")
