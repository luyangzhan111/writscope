"""Offline checks for the versioned c003 gate; no paid provider access."""

import asyncio
from decimal import Decimal
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from scripts.m3e_gate import prepare, run, score_run, verify_run
from test_m3c_real_pilot import PublicNoChangeProvider


def _output(root: Path) -> Path:
    return root / "reports" / "local" / ("m3e-test-" + uuid4().hex)


def test_c003_v2_gate_preflight_run_and_replay(root):
    with pytest.raises(ValueError, match="reservation floor"):
        prepare(root, Decimal("1"))
    preflight = prepare(root, Decimal("2"))
    assert preflight["stage"] == "m3e_c003_real_diagnostic"
    assert preflight["planned_attempts"] == 5
    assert preflight["worst_case_reservation_cny"] == "1.245760"
    assert set(preflight["cases"]) == {"c003"}
    assert preflight["runner_sha256"]
    provider = PublicNoChangeProvider()
    output = _output(root)
    try:
        result = asyncio.run(run(root, output, preflight, provider))
        assert result["status"] == "completed"
        assert result["attempts"] == provider.calls == 5
        assert set(result["cases"]) == {"c003"}
        assert "evaluation_private" not in (output / "c003/step_001/request.json").read_text(encoding="utf-8")
        assert verify_run(root, output)["artifact_set_sha256"] == result["artifact_set_sha256"]
        score_output = _output(root)
        try:
            score = score_run(root, output, root / "evaluation_private/case_001/labels.json", score_output)
            assert score["planned_cases"] == score["valid_cases"] == 1
            assert score["comparison_status"] == "no_method_comparison"
        finally:
            shutil.rmtree(score_output)
    finally:
        shutil.rmtree(output)


def test_c003_v2_gate_stops_on_first_invalid_response(root):
    preflight = prepare(root, Decimal("2"))
    provider = PublicNoChangeProvider(fail_at=5)
    output = _output(root)
    try:
        result = asyncio.run(run(root, output, preflight, provider))
        assert result["status"] == "stopped_on_failure"
        assert result["attempts"] == provider.calls == 5
        assert result["cases"]["c003"]["status"] == "incomplete"
        assert (output / "c003/step_005/response.txt").exists()
        assert not (output / "c003/assessment.json").exists()
        assert verify_run(root, output)["attempts"] == 5
    finally:
        shutil.rmtree(output)
