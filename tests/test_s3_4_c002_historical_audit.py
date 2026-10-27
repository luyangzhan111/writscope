"""Narrow no-network checks for the c002 historical diagnostic."""

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import s3_4_c002_historical_audit as audit
from writscope.contracts import load_bundle


ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "reports/local/m3c-live-20261021"
NEW = ROOT / "reports/local/m3d-c002-v2-live-20261021"
ANNOTATION = ROOT / "evaluation_private/case_001/s3_4_c002_historical_annotation_v1.json"


def test_historical_audit_replays_both_original_versions(monkeypatch):
    def forbidden_provider(*_args, **_kwargs):
        raise AssertionError("provider must not be constructed")

    monkeypatch.setattr(audit.m3c, "DeepSeekProvider", forbidden_provider)
    monkeypatch.setattr(audit.m3d_gate, "DeepSeekProvider", forbidden_provider)
    output = ROOT / "reports/local" / f"s3-4-c002-test-{uuid4().hex}"
    report = audit.run_audit(ROOT, OLD, NEW, ANNOTATION, output)
    assert report["new_model_calls"] == 0
    assert report["findings"]["m3c_c002_terminal"] == "not_attempted"
    assert report["findings"]["m3d_final_model_review"] == "not_run"
    assert report["findings"]["m3d_local_grant_matches_reviewed_scope"]
    assert report["findings"]["reviewed_task_context_quotes"] == 3
    assert report["costs"]["m3c_original_run"]["attempts"] == 10
    assert report["costs"]["m3d_original_run"]["attempts"] == 5
    assert audit.verify_audit(ROOT, OLD, NEW, ANNOTATION, output) == report
    saved = output / "audit.json"
    saved.write_text(saved.read_text(encoding="utf-8") + " ", encoding="utf-8")
    # Whitespace alone leaves parsed JSON unchanged; alter a value to prove replay rejects it.
    changed = audit._read(saved)
    changed["new_model_calls"] = 1
    from writscope.artifacts import write_json
    write_json(saved, changed)
    with pytest.raises(ValueError, match="saved historical audit replay mismatch"):
        audit.verify_audit(ROOT, OLD, NEW, ANNOTATION, output)


def test_annotation_rejects_future_or_forged_quote():
    bundle = load_bundle(ROOT / "reports/m1-2026-10-18/inputs/verified_impact/c002")
    original = audit._read(ANNOTATION)
    forged = deepcopy(original)
    forged["context_quotes"][0]["quote"] = "not in trusted event"
    with pytest.raises(ValueError, match="unique trusted source quote"):
        audit._validate_annotation(forged, bundle, ROOT)
    future = deepcopy(original)
    future["context_quotes"][0] = {"event_id": "e001", "quote": original["grant_quote"]}
    with pytest.raises(ValueError, match="unique trusted source quote"):
        audit._validate_annotation(future, bundle, ROOT)


def test_original_replay_precedes_private_annotation_read(monkeypatch):
    def fail_replay(*_args, **_kwargs):
        raise ValueError("original integrity failure")

    monkeypatch.setattr(audit.m3c, "verify_run", fail_replay)
    with pytest.raises(ValueError, match="original integrity failure"):
        audit.build_report(ROOT, OLD, NEW, ROOT / "evaluation_private/missing.json")
