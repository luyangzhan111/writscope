"""Offline checks for the paid pilot gate; no network or credentials."""

import asyncio
import json
from decimal import Decimal
from pathlib import Path
import shutil
from uuid import uuid4

from writscope.m3c import prepare, run, score_run, verify_run
from writscope.providers import ActualUsage, ProviderResponse


class PublicNoChangeProvider:
    def __init__(self, *, fail_at=None):
        self.calls = 0
        self.fail_at = fail_at

    async def generate(self, request):
        self.calls += 1
        if self.calls == self.fail_at:
            return ProviderResponse(text="{bad", finish_reason="stop",
                                    actual_usage=ActualUsage(input_tokens=100,
                                                             output_tokens=10,
                                                             estimated_cost_cny=Decimal("0.00028"),
                                                             usage_source="provider_response",
                                                             cost_source="official_peak_price_estimate"))
        event = request.public_input["review_input"]["events"][-1]
        return ProviderResponse(
            text=json.dumps({"operation": "no_change", "source_quote": event["content"],
                             "target_ids": [], "constraint": None, "authorization": None},
                            ensure_ascii=False),
            finish_reason="stop",
            actual_usage=ActualUsage(input_tokens=100, output_tokens=10,
                                     estimated_cost_cny=Decimal("0.00028"),
                                     usage_source="provider_response",
                                     cost_source="official_peak_price_estimate"))


def _output(root: Path) -> Path:
    return root / "reports" / "local" / ("m3c-test-" + uuid4().hex)


def test_preflight_and_real_path_simulation(root):
    preflight = prepare(root, Decimal("5"))
    assert preflight["planned_attempts"] == 15
    assert preflight["worst_case_reservation_cny"] == "3.737280"
    provider = PublicNoChangeProvider()
    output = _output(root)
    try:
        result = asyncio.run(run(root, output, preflight, provider))
        assert result["status"] == "completed"
        assert result["attempts"] == provider.calls == 15
        assert result["usage"]["input_tokens"]["known_subtotal"] == "1500"
        assert result["provider_billed_cny"] is None
        assert all(value["status"] == "completed" for value in result["cases"].values())
        assert all((output / case / "assessment.json").exists() for case in result["cases"])
        assert "evaluation_private" not in (output / "c001/step_001/request.json").read_text(encoding="utf-8")
        assert verify_run(root, output)["artifact_set_sha256"] == result["artifact_set_sha256"]
        score_output = _output(root)
        try:
            summary = score_run(root, output, root / "evaluation_private/case_001/labels.json", score_output)
            assert summary["valid_cases"] == 3
            assert summary["axis_correct"]["decision"] == 2
            assert summary["comparison_status"] == "no_method_comparison"
        finally:
            shutil.rmtree(score_output)
    finally:
        shutil.rmtree(output)


def test_invalid_first_response_stops_all_future_calls(root):
    preflight = prepare(root, Decimal("5"))
    provider = PublicNoChangeProvider(fail_at=1)
    output = _output(root)
    try:
        result = asyncio.run(run(root, output, preflight, provider))
        assert result["status"] == "stopped_on_failure"
        assert result["attempts"] == provider.calls == 1
        assert result["cases"]["c001"]["status"] == "incomplete"
        assert not (output / "c001/assessment.json").exists()
        assert (output / "c001/step_001/response.txt").exists()
        assert not (output / "c002").exists()
        assert verify_run(root, output)["attempts"] == 1
    finally:
        shutil.rmtree(output)
