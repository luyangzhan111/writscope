"""The full c003 runner remains replayable without a paid provider call."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from scripts import m3q_fault_evidence_audit as fixtures
from scripts.m3s_real_runner import (SimulatedActualProvider, run_with_provider,
                                     score_run, verify_run)
from scripts import m3r_real_preflight as preflight
from scripts import m3s_real_runner as runner
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import load_bundle, parse_json
from writscope.m3b import _artifacts
from writscope.providers.mock import Script, Step


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "reports/local/m3r-c003-preflight-20261024-final"
INPUT = ROOT / "reports/m1-2026-10-18/inputs/verified_impact/c003"


def _output():
    return ROOT / "reports/local" / ("m3s-test-" + uuid4().hex)


def _run(script: Script, output: Path):
    return asyncio.run(run_with_provider(ROOT, PREFLIGHT, output,
        SimulatedActualProvider(script), mode="offline_simulation"))


def test_complete_simulation_has_eight_accounted_attempts_and_replays():
    output = _output()
    try:
        script = fixtures._basic_script(load_bundle(INPUT))
        result = _run(script, output)
        assert result["status"] == "completed"
        assert result["actual_model_calls"] == 0
        assert result["simulated_provider_attempts"] == 8
        assert result["accepted_events"] == 5
        assert result["lookup_attempts"] == 2
        assert result["final_review_valid"]
        assert result["totals"]["provider_billed_cny"]["complete_total"] is None
        assert result["totals"]["provider_billed_cny"]["unknown_attempts"] == 8
        assert [result["totals"]["by_stage"][stage]["attempts"] for stage in
                ("extract", "lookup_continuation", "final_review")] == [5, 2, 1]
        assert sum(result["totals"]["by_stage"][stage]["input_tokens"]["known_subtotal"]
                   for stage in ("extract", "lookup_continuation", "final_review")) == \
               result["totals"]["input_tokens"]["known_subtotal"]
        assert verify_run(ROOT, PREFLIGHT, output) == result
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_replay_accepts_saved_decimal_cost_from_provider_usage():
    output = _output()
    delegate = SimulatedActualProvider(fixtures._basic_script(load_bundle(INPUT)))

    class CostedProvider:
        async def generate(self, request):
            response = await delegate.generate(request)
            usage = response.actual_usage.model_copy(update={
                "estimated_cost_cny": Decimal("0.01"),
                "cost_source": "official_peak_price_estimate"})
            return response.model_copy(update={"actual_usage": usage})

    try:
        result = asyncio.run(run_with_provider(ROOT, PREFLIGHT, output,
            CostedProvider(), mode="offline_simulation"))
        assert result["totals"]["estimated_cost_cny"]["complete_total"] == "0.08"
        assert verify_run(ROOT, PREFLIGHT, output) == result
    finally:
        if output.exists():
            shutil.rmtree(output)


@pytest.mark.parametrize("action,status", [
    ("error", "provider_error"),
    ("missing_usage", "usage_missing"),
    ("bad_json", "invalid_output"),
])
def test_first_failure_stops_without_final_result(action, status):
    output = _output()
    first = fixtures._basic_script(load_bundle(INPUT)).steps[0].text
    step = (Step(action="error", text="simulated failure") if action == "error"
            else Step(action="response", text="{" if action == "bad_json" else first,
                      synthetic_usage=None if action == "missing_usage" else fixtures.TOKEN_USAGE))
    script = Script(schema_version="0.1-draft", execution_kind="offline_mock",
                    description="M3s failure check", steps=[step])
    try:
        result = _run(script, output)
        assert result["status"] == "stopped_on_failure"
        assert result["simulated_provider_attempts"] == 1
        assert result["failure"]["status"] == status
        assert not (output / "final_result.json").exists()
        assert result["totals"]["provider_billed_cny"]["unknown_attempts"] == 1
        assert verify_run(ROOT, PREFLIGHT, output) == result
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_rehashed_request_tamper_is_rejected_by_replay():
    output = _output()
    try:
        result = _run(fixtures._basic_script(load_bundle(INPUT)), output)
        assert result["status"] == "completed"
        calls_path = output / "calls.json"
        calls = parse_json(calls_path.read_text(encoding="utf-8"))
        calls[0]["request"]["public_input"]["review_input"]["events"][0]["content"] = "forged"
        write_json(calls_path, calls)
        manifest_path = output / "run.json"
        manifest = parse_json(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"] = _artifacts(output)
        manifest["artifact_set_sha256"] = canonical_hash(manifest["artifacts"])
        write_json(manifest_path, manifest)
        with pytest.raises(ValueError, match="request or reservation replay mismatch"):
            verify_run(ROOT, PREFLIGHT, output)
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_c003_cross_version_scope_failure_stops_before_final():
    output = _output()
    try:
        result = _run(fixtures._rich_script(load_bundle(INPUT), wrong_scope=True), output)
        assert result["status"] == "stopped_on_failure"
        assert result["failure"]["status"] == "batch_rejected"
        assert result["accepted_events"] == 4
        assert result["simulated_provider_attempts"] == 7
        assert not (output / "final_result.json").exists()
        assert verify_run(ROOT, PREFLIGHT, output) == result
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_simulation_cannot_read_reference_labels_for_scoring():
    output = _output()
    try:
        _run(fixtures._basic_script(load_bundle(INPUT)), output)
        with pytest.raises(ValueError, match="offline simulation cannot be scored"):
            score_run(ROOT, PREFLIGHT, output,
                      ROOT / "evaluation_private/case_001/labels.json",
                      output.parent / (output.name + "-score"))
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_timeout_stops_after_one_reserved_attempt():
    output = _output()

    class TimeoutProvider:
        async def generate(self, request):
            raise TimeoutError("simulated")

    try:
        result = asyncio.run(run_with_provider(ROOT, PREFLIGHT, output,
            TimeoutProvider(), mode="offline_simulation"))
        assert result["failure"]["status"] == "timeout"
        assert result["simulated_provider_attempts"] == 1
        assert result["totals"]["provider_billed_cny"]["unknown_attempts"] == 1
        assert verify_run(ROOT, PREFLIGHT, output) == result
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_real_launch_scope_requires_final_runner_and_fresh_price_recheck():
    plan, _ = preflight.prepare(ROOT)
    receipt = {"status": "approved", "source_kind": "direct_user_message",
               "scope": "M3s_c003_complete_incremental",
               "plan_sha256": canonical_hash(plan), "case_ids": ["c003"],
               "local_cap_cny": "3.00", "max_provider_attempts": 8,
               "user_authorization_ref": "test-only",
               "runner_sha256": digest(Path(runner.__file__).read_bytes()),
               "price_source_url": preflight.PRICE_URL,
               "price_rechecked_at_utc": datetime.now(timezone.utc).isoformat()}
    runner._check_authorization(plan, receipt)
    with pytest.raises(ValueError, match="runner or price recheck"):
        runner._check_authorization(plan, {**receipt, "runner_sha256": "0" * 64})
    with pytest.raises(ValueError, match="price recheck is not fresh"):
        runner._check_authorization(plan, {**receipt,
            "price_rechecked_at_utc": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()})


def test_invalid_final_response_keeps_all_attempts_but_no_result():
    output = _output()
    script = fixtures._basic_script(load_bundle(INPUT)).model_copy(deep=True)
    script.steps[-1].text = "{}"
    try:
        result = _run(script, output)
        assert result["status"] == "stopped_on_failure"
        assert result["failure"]["stage"] == "final_review"
        assert result["failure"]["status"] == "invalid_output"
        assert result["simulated_provider_attempts"] == 8
        assert result["accepted_events"] == 5
        assert not (output / "final_result.json").exists()
        assert verify_run(ROOT, PREFLIGHT, output) == result
    finally:
        if output.exists():
            shutil.rmtree(output)
