"""Narrow offline checks for the evaluator-only S3.4 c003 diagnosis."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import s3_4_oracle_diagnostic as diagnostic
from writscope.artifacts import canonical_hash
from writscope.contracts import load_bundle


ROOT = Path(__file__).resolve().parents[1]
ANNOTATION = ROOT / "evaluation_private/case_001/s3_4_c003_annotation_v1.json"
PREFLIGHT = ROOT / "reports/local/m3r-c003-preflight-20261024-final"
SOURCE_RUN = ROOT / "reports/local/m3s-c003-real-20261024"
BUNDLE = ROOT / "reports/m1-2026-10-18/inputs/verified_impact/c003"


def _rehash(annotation: dict) -> None:
    annotation["annotation_hash"] = canonical_hash({
        key: value for key, value in annotation.items() if key != "annotation_hash"})


def test_c003_single_review_diagnosis_has_no_provider_call(monkeypatch):
    def forbidden_provider(*_args, **_kwargs):
        raise AssertionError("provider must not be constructed")

    monkeypatch.setattr(diagnostic.m3s_real_runner, "DeepSeekProvider", forbidden_provider)
    output = ROOT / "reports/local" / f"s3-4-test-{uuid4().hex}"
    report = diagnostic.run_diagnostic(ROOT, PREFLIGHT, SOURCE_RUN, ANNOTATION, output)
    assert report["new_model_calls"] == 0
    assert report["observed_model_calls"] == 6
    assert [row["event_id"] for row in report["prefixes"] if
            "extraction_semantic_error_candidate" in row["labels"]] == [
                "e001", "e004", "e005"]
    assert report["terminal"]["classification"] == "masked_state_error_candidate"
    assert report["terminal"]["oracle_conditioned_model_review"] == "not_run"
    assert diagnostic.verify_diagnostic(ROOT, PREFLIGHT, SOURCE_RUN, ANNOTATION,
                                        output)["status"] == "completed_single_case_diagnostic"
    saved = (output / "diagnostic.json").read_text(encoding="utf-8")
    assert "EVALUATOR_ONLY_M1_REFERENCE" not in saved
    assert "Single project-spec review" not in saved
    (output / "diagnostic.json").write_text(saved + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="saved diagnostic replay mismatch"):
        diagnostic.verify_diagnostic(ROOT, PREFLIGHT, SOURCE_RUN, ANNOTATION, output)


def test_future_fact_and_bad_quote_are_rejected():
    bundle = load_bundle(BUNDLE)
    original = diagnostic._read(ANNOTATION)
    future = deepcopy(original)
    future["events"][0]["expected_poststate_facts"].append("auth_future_v2")
    _rehash(future)
    with pytest.raises(ValueError, match="future"):
        diagnostic._validate_annotation(future, bundle, ROOT / "specs/first-case.md")

    false_quote = deepcopy(original)
    false_quote["fact_catalog"]["ctx_local_mapping"]["source_quote"] = "未出现的引文"
    _rehash(false_quote)
    with pytest.raises(ValueError, match="trusted source quote"):
        diagnostic._validate_annotation(false_quote, bundle, ROOT / "specs/first-case.md")

    unreviewed = deepcopy(original)
    unreviewed["annotation_status"] = "draft"
    _rehash(unreviewed)
    with pytest.raises(ValueError, match="review"):
        diagnostic._validate_annotation(unreviewed, bundle, ROOT / "specs/first-case.md")


def test_observed_replay_precedes_private_annotation_read(monkeypatch):
    def fail_verify(*_args, **_kwargs):
        raise ValueError("observed integrity failure")

    monkeypatch.setattr(diagnostic.m3s_real_runner, "verify_run", fail_verify)
    with pytest.raises(ValueError, match="observed integrity failure"):
        diagnostic.build_report(ROOT, PREFLIGHT, SOURCE_RUN,
                                ROOT / "evaluation_private/case_001/missing-annotation.json")


def test_cost_reconciliation_rejects_changed_usage():
    manifest = diagnostic._read(SOURCE_RUN / "run.json")
    calls = diagnostic._read(SOURCE_RUN / "calls.json")
    steps = diagnostic._read(SOURCE_RUN / "core_steps.json")
    changed = deepcopy(calls)
    changed[0]["usage"]["input_tokens"] += 1
    with pytest.raises(ValueError, match="cache token split"):
        diagnostic._cost_reconciliation(manifest, changed, steps)
