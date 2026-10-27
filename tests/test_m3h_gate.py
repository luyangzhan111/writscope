"""M3h real-path preparation: all providers here are local simulations."""

import asyncio
from decimal import Decimal
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from scripts import m3e_gate, m3h_gate as gate
from test_m3c_real_pilot import PublicNoChangeProvider
from test_m3g_offline_runner import WrongScopeProvider
from writscope.artifacts import write_json
from writscope.contracts import parse_json
from writscope.providers import ProviderError
from writscope.providers.deepseek import DeepSeekProvider


def _path(root: Path) -> Path:
    return root / "reports" / "local" / ("m3h-test-" + uuid4().hex)


def _draft(root: Path) -> dict:
    return gate.prepare(root, Decimal("2"), m3e_gate.PRICE)


def test_draft_preflight_and_real_mode_guard(root, monkeypatch):
    preflight = _draft(root)
    assert preflight["authorization_status"] == "draft_unapproved"
    assert preflight["policy_source_sha256"]
    assert preflight["planned_attempts"] == 5
    assert preflight["worst_case_reservation_cny"] == "1.245760"
    with pytest.raises(ValueError, match="reservation floor"):
        gate.prepare(root, Decimal("1"), m3e_gate.PRICE)
    with pytest.raises(ValueError, match="authorization reference required"):
        asyncio.run(gate.run(root, _path(root), preflight, PublicNoChangeProvider(),
                             mode="real_model"))
    with pytest.raises(ValueError, match="real provider cannot be recorded"):
        asyncio.run(gate.run(root, _path(root), preflight,
                             DeepSeekProvider("test-key-must-not-be-used", m3e_gate.PRICE),
                             mode="offline_simulation"))
    preflight_dir = _path(root)
    try:
        preflight_dir.mkdir()
        write_json(preflight_dir / "preflight.json", preflight)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-must-not-be-used")
        with pytest.raises(SystemExit, match="authorization absent"):
            gate.main(["run", "--root", str(root), "--preflight", str(preflight_dir),
                       "--output", str(_path(root))])
    finally:
        shutil.rmtree(preflight_dir)


def test_simulated_complete_and_replay(root):
    output = _path(root)
    try:
        provider = PublicNoChangeProvider()
        result = asyncio.run(gate.run(root, output, _draft(root), provider,
                                      mode="offline_simulation"))
        assert result["status"] == "completed"
        assert result["attempts"] == result["simulated_provider_attempts"] == provider.calls == 5
        assert result["actual_model_calls"] == 0
        assert result["usage_kind"] == "synthetic_mock"
        assert (output / "c003" / "assessment.json").exists()
        assert gate.verify_run(root, output)["artifact_set_sha256"] == result["artifact_set_sha256"]
        with pytest.raises(ValueError, match="cannot be scored"):
            gate.score_run(root, output, root / "evaluation_private/case_001/labels.json", _path(root))
        manifest_path = output / "run.json"
        tampered = parse_json(manifest_path.read_text(encoding="utf-8"))
        tampered["synthetic_usage"]["input_tokens"]["known_subtotal"] = "999999"
        write_json(manifest_path, tampered)
        with pytest.raises(ValueError, match="usage summary mismatch"):
            gate.verify_run(root, output)
    finally:
        if output.exists():
            shutil.rmtree(output)


@pytest.mark.parametrize("provider,error_status", [
    (PublicNoChangeProvider(fail_at=2), "invalid_output"),
])
def test_invalid_response_stops_without_retry(root, provider, error_status):
    output = _path(root)
    try:
        result = asyncio.run(gate.run(root, output, _draft(root), provider,
                                      mode="offline_simulation"))
        assert result["status"] == "stopped_on_failure"
        assert result["attempts"] == provider.calls == 2
        assert parse_json((output / "c003/step_002/call.json").read_text(encoding="utf-8"))["status"] == error_status
        assert not (output / "c003/step_003").exists()
        assert not (output / "c003/assessment.json").exists()
        assert gate.verify_run(root, output)["status"] == "stopped_on_failure"
    finally:
        if output.exists():
            shutil.rmtree(output)


class FailingProvider(PublicNoChangeProvider):
    async def generate(self, request):
        if self.calls == 1:
            self.calls += 1
            raise ProviderError("scripted transport failure")
        return await super().generate(request)


def test_provider_error_stops_and_replays(root):
    output = _path(root)
    try:
        provider = FailingProvider()
        result = asyncio.run(gate.run(root, output, _draft(root), provider,
                                      mode="offline_simulation"))
        assert result["attempts"] == provider.calls == 2
        assert result["status"] == "stopped_on_failure"
        assert parse_json((output / "c003/step_002/call.json").read_text(encoding="utf-8"))["status"] == "provider_error"
        assert not (output / "c003/step_003").exists()
        assert gate.verify_run(root, output)["attempts"] == 2
    finally:
        if output.exists():
            shutil.rmtree(output)


class SlowProvider(PublicNoChangeProvider):
    async def generate(self, request):
        self.calls += 1
        await asyncio.sleep(0.05)
        raise AssertionError("provider should have timed out")


def test_timeout_stops_without_retry(root, monkeypatch):
    monkeypatch.setattr(gate, "TIMEOUT_SECONDS", 0.001)
    output = _path(root)
    try:
        provider = SlowProvider()
        result = asyncio.run(gate.run(root, output, _draft(root), provider,
                                      mode="offline_simulation"))
        assert result["attempts"] == provider.calls == 1
        assert parse_json((output / "c003/step_001/call.json").read_text(encoding="utf-8"))["status"] == "timeout"
        assert not (output / "c003/step_002").exists()
        assert gate.verify_run(root, output)["status"] == "stopped_on_failure"
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_scope_rejection_stops_and_replays(root):
    output = _path(root)
    try:
        provider = WrongScopeProvider()
        result = asyncio.run(gate.run(root, output, _draft(root), provider,
                                      mode="offline_simulation"))
        assert result["attempts"] == provider.calls == 5
        assert result["status"] == "stopped_on_failure"
        call = parse_json((output / "c003/step_005/call.json").read_text(encoding="utf-8"))
        assert call["status"] == "invalid_output"
        assert "constraint reference scope mismatch" in call["error"]
        assert not (output / "c003/assessment.json").exists()
        assert gate.verify_run(root, output)["status"] == "stopped_on_failure"
    finally:
        if output.exists():
            shutil.rmtree(output)
