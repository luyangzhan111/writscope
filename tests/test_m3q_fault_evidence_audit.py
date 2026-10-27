"""M3q checks failure stopping, public evidence parity, and independent replay."""

from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from scripts.m3q_fault_evidence_audit import _report
from writscope.artifacts import canonical_hash, write_json
from writscope.contracts import parse_json
from writscope.m3b import _artifacts


ROOT = Path(__file__).resolve().parents[1]


def test_fault_evidence_audit_replays_and_rejects_rehashed_report_tamper():
    output = ROOT / "reports/local" / ("m3q-test-" + uuid4().hex)
    try:
        report = _report(ROOT, output, create=True)
        assert _report(ROOT, output, create=False) == report
        assert report["actual_model_calls"] == 0
        assert report["fault_scenarios"] == 9
        assert report["alignment_cases"] == 3
        assert report["historical_replays"] == 3
        assert report["tamper_rejected"]

        faults = parse_json((output / "faults.json").read_text(encoding="utf-8"))
        assert {row["scenario"] for row in faults["rows"]} == {
            "timeout", "provider_error", "usage_missing", "bad_json",
            "future_lookup", "unknown_lookup", "scope_mismatch",
            "budget_insufficient", "invalid_final"}
        assert all(row["final_result_absent"] for row in faults["rows"])
        assert all(row["reserved_cny"] != "0" for row in faults["rows"]
                   if row["simulated_provider_attempts"])
        assert all(row["unknown_billed_attempts"] == row["simulated_provider_attempts"]
                   for row in faults["rows"])

        alignment_path = output / "alignment.json"
        alignment = parse_json(alignment_path.read_text(encoding="utf-8"))
        assert [row["case_id"] for row in alignment["rows"]] == ["c001", "c002", "c003"]
        assert alignment["private_labels_read"] is False
        assert all(row["expected"] is None and row["correct"] is None
                   and row["cost"]["provider_billed_cost_cny"] == "unknown"
                   and row["cost"]["synthetic_test_usage"]["cost_usd"]["complete_total"] is None
                   for row in alignment["rows"])

        alignment["rows"][0]["observed"]["decision"] = "allow"
        write_json(alignment_path, alignment)
        manifest_path = output / "run.json"
        manifest = parse_json(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"] = _artifacts(output)
        manifest["artifact_set_sha256"] = canonical_hash(manifest["artifacts"])
        write_json(manifest_path, manifest)
        with pytest.raises(ValueError, match="evidence alignment or historical replay mismatch"):
            _report(ROOT, output, create=False)
    finally:
        if output.exists():
            shutil.rmtree(output)
