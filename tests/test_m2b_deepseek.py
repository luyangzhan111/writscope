import asyncio
from decimal import Decimal
import json

import httpx
import pytest

from writscope.budget import ReservationLedger
from writscope.evaluation import score_run
from writscope.m2b import main, policy, prepare
from writscope.providers import ActualUsage, ProviderError, ProviderResponse
from writscope.providers.deepseek import DeepSeekProvider, ENDPOINT, wire_bytes, wire_payload
from writscope.runner import RunPolicy, run_suite


def test_deepseek_wire_and_usage_without_network(root):
    requests, manifest, _ = prepare(root, policy(Decimal("20")))
    request = requests[0]
    body = wire_payload(request)
    assert body["model"] == "deepseek-flash"
    assert body["thinking"] == {"type": "disabled"}
    assert body["response_format"] == {"type": "json_object"}
    assert body["tool_choice"] == "none" and "tools" not in body
    assert body["messages"][0]["content"] == request.instructions
    assert "expected_authorization" not in wire_bytes(request).decode("utf-8")
    assert len(wire_bytes(request)) == manifest["request_wire_bytes"][0]
    seen = []

    def transport(message):
        seen.append(message)
        return httpx.Response(200, json={"id": "resp-test", "model": "deepseek-flash",
                                         "system_fingerprint": "fp-test",
                                         "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                                         "usage": {"prompt_tokens": 1000, "completion_tokens": 100,
                                                   "prompt_cache_hit_tokens": 200,
                                                   "prompt_cache_miss_tokens": 800}})

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await DeepSeekProvider("test-secret", policy(Decimal("20")).price_ceiling,
                                          client=client).generate(request)

    response = asyncio.run(exercise())
    assert response.actual_usage.input_tokens == 1000
    assert response.actual_usage.cache_hit_input_tokens == 200
    assert response.actual_usage.estimated_cost_cny == Decimal("0.0028")
    assert response.actual_usage.billed_cost_cny is None
    assert seen[0].url == ENDPOINT
    assert seen[0].headers["Authorization"] == "Bearer test-secret"
    assert json.loads(seen[0].content)["response_format"]["type"] == "json_object"


def test_deepseek_http_error_does_not_echo_body(root):
    request = prepare(root, policy(Decimal("20")))[0][0]

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _: httpx.Response(401, text="secret-reflection"))) as client:
            return await DeepSeekProvider("test-secret", policy(Decimal("20")).price_ceiling,
                                          client=client).generate(request)

    with pytest.raises(ProviderError, match="HTTP 401") as info:
        asyncio.run(exercise())
    assert not info.value.retryable
    assert "secret-reflection" not in str(info.value)


def test_real_runner_with_injected_transport_only(root, tmp_path):
    requests, preflight, model = prepare(root, policy(Decimal("20")))
    direct_text = (root / "examples" / "m2a" / "direct_response.json").read_text(encoding="utf-8")
    checklist_text = (root / "examples" / "m2a" / "checklist_response.json").read_text(encoding="utf-8")
    calls = []

    class InjectedProvider:
        async def generate(self, request):
            calls.append(request.method)
            return ProviderResponse(text=direct_text if request.method == "full_direct" else checklist_text,
                                    actual_usage=ActualUsage(input_tokens=1000, output_tokens=100,
                                                             estimated_cost_cny=Decimal("0.0028"),
                                                             usage_source="provider_response",
                                                             cost_source="official_peak_price_estimate"),
                                    finish_reason="stop", served_model="deepseek-flash")

    base = root / "reports" / "m1-2026-10-18" / "inputs" / "verified_impact"
    prompts = {method: (root / "prompts" / (method + ".md")).read_text(encoding="utf-8")
               for method in ("full_direct", "full_checklist")}
    budget = policy(Decimal("20"))
    output = tmp_path / "real-run"
    manifest = asyncio.run(run_suite([base / case for case in ("c001", "c002", "c003")],
                                      output, prompts, lambda _: InjectedProvider(),
                                      policy=RunPolicy(execution_kind="real_model", timeout_seconds=90,
                                                       max_attempts_per_job=1, max_total_attempts=6),
                                      model=model, ledger=ReservationLedger(budget),
                                      wire_size=lambda request: len(wire_bytes(request)),
                                      expected_request_hashes=preflight["request_sha256"]))
    assert len(calls) == 6
    assert manifest["totals"]["actual_model_calls"] == 6
    assert Decimal(manifest["budget"]["reserved_estimated_cny"]) < Decimal("20")
    assert manifest["totals"]["provider_reported_usage_and_estimates"]["input_tokens"]["complete_total"] == "6000"
    assert manifest["totals"]["provider_reported_usage_and_estimates"]["billed_cost_cny"]["complete_total"] is None
    summary = score_run(output, root / "evaluation_private" / "case_001" / "labels.json", tmp_path / "score")
    assert summary["execution_kind"] == "real_model"
    assert summary["methods"]["full_direct"]["axes"]["authorization"]["correct"] == 2


def test_real_runner_stops_after_missing_usage(root, tmp_path):
    requests, preflight, model = prepare(root, policy(Decimal("20")))
    text = (root / "examples" / "m2a" / "direct_response.json").read_text(encoding="utf-8")

    class MissingUsage:
        async def generate(self, request):
            return ProviderResponse(text=text, finish_reason="stop")

    base = root / "reports" / "m1-2026-10-18" / "inputs" / "verified_impact"
    prompts = {method: (root / "prompts" / (method + ".md")).read_text(encoding="utf-8")
               for method in ("full_direct", "full_checklist")}
    output = tmp_path / "run"
    manifest = asyncio.run(run_suite([base / case for case in ("c001", "c002", "c003")], output,
                                      prompts, lambda _: MissingUsage(),
                                      policy=RunPolicy(execution_kind="real_model", max_attempts_per_job=1,
                                                       max_total_attempts=6), model=model,
                                      ledger=ReservationLedger(policy(Decimal("20"))),
                                      wire_size=lambda request: len(wire_bytes(request)),
                                      expected_request_hashes=preflight["request_sha256"]))
    assert manifest["totals"]["actual_model_calls"] == 1
    assert json.loads((output / "job_000" / "job.json").read_text(encoding="utf-8"))["status"] == "invalid_output"
    assert json.loads((output / "job_001" / "job.json").read_text(encoding="utf-8"))["status"] == "stopped"


def test_real_runner_stops_after_provider_error(root, tmp_path):
    _, preflight, model = prepare(root, policy(Decimal("20")))
    calls = []

    class FailingProvider:
        async def generate(self, request):
            calls.append(request.method)
            raise ProviderError("DeepSeek HTTP 429", retryable=True)

    base = root / "reports" / "m1-2026-10-18" / "inputs" / "verified_impact"
    prompts = {method: (root / "prompts" / (method + ".md")).read_text(encoding="utf-8")
               for method in ("full_direct", "full_checklist")}
    output = tmp_path / "run"
    manifest = asyncio.run(run_suite([base / case for case in ("c001", "c002", "c003")], output,
                                     prompts, lambda _: FailingProvider(),
                                     policy=RunPolicy(execution_kind="real_model", max_attempts_per_job=2,
                                                      max_total_attempts=6), model=model,
                                     ledger=ReservationLedger(policy(Decimal("20"))),
                                     wire_size=lambda request: len(wire_bytes(request)),
                                     expected_request_hashes=preflight["request_sha256"]))
    assert calls == ["full_direct"]
    assert manifest["budget"]["reservation_attempts"] == 1
    assert json.loads((output / "job_000" / "job.json").read_text(encoding="utf-8"))["status"] == "provider_error"
    assert json.loads((output / "job_001" / "job.json").read_text(encoding="utf-8"))["status"] == "stopped"


def test_run_cli_without_key_exits_before_output(root, tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    output = tmp_path / "would-run"
    with pytest.raises(SystemExit, match="DEEPSEEK_API_KEY is not set"):
        main(["run", "--root", str(root), "--output", str(output)])
    assert not output.exists()
