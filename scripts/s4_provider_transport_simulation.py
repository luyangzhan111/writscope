"""Exercise the existing provider wire parser through an in-memory transport only.

No environment access, real credentials, live clients, network, or paid entry point.
"""
import json
from decimal import Decimal

import httpx

from scripts.s4_prompt_candidate import STAGES, adapter
from writscope.artifacts import digest
from writscope.budget import PriceCeiling
from writscope.contracts import parse_json
from writscope.providers import ModelConfig, ModelRequest
from writscope.providers.deepseek import DeepSeekProvider, wire_bytes


async def simulate(preview, response_data, *, max_response_bytes=100000, max_output_tokens=4096, http_status=200):
    if (preview.get("version") != "s4_prompt_contract_preview_v1" or
            preview.get("status") != "draft_offline_only" or preview.get("stage") not in STAGES or
            preview.get("paid_execution_allowed") is not False or
            preview.get("external_transmission_allowed") is not False or preview.get("model") is not None):
        raise ValueError("invalid offline preview")
    if not isinstance(max_response_bytes, int) or isinstance(max_response_bytes, bool) or max_response_bytes < 1:
        raise ValueError("invalid response limit")
    stage = preview["stage"]
    request = ModelRequest(method="incremental_extract" if stage == "incremental_extract" else
                           "full_checklist" if stage == "full_checklist" else "full_direct",
                           output_contract="transition_quote_v1" if stage == "incremental_extract" else "source_quote_v1",
                           model_config_values=ModelConfig(model_id="deepseek-flash", temperature=0.0,
                                                          max_output_tokens=max_output_tokens),
                           instructions=preview["instructions"], prompt_sha256=preview["prompt_sha256"],
                           evidence_sha256=preview["evidence_sha256"], public_input=preview["public_input"],
                           output_schema=preview["output_schema"])
    if digest(request.instructions.encode("utf-8")) != request.prompt_sha256:
        raise ValueError("prompt binding mismatch")
    wire_hash = digest(wire_bytes(request))
    seen = []

    def handler(request_in):
        # httpx.MockTransport never opens a socket; captured body stays in memory.
        seen.append(digest(request_in.content))
        return httpx.Response(http_status, json=response_data)

    # Synthetic prices permit parsing only. They are not quotations, estimates
    # for real execution, or authorization for expenditure.
    price = PriceCeiling(input_cny_per_million=Decimal("1"), output_cny_per_million=Decimal("1"),
                         source_url="offline-synthetic", checked_at_utc="synthetic")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        provider = DeepSeekProvider("offline-placeholder-not-a-secret", price, client=client)
        response = await provider.generate(request)
    if len(seen) != 1 or seen[0] != wire_hash:
        raise ValueError("simulated transport wire mismatch")
    if response.finish_reason != "stop":
        raise ValueError("truncated or unknown finish reason")
    if len(response.text.encode("utf-8")) > max_response_bytes:
        raise ValueError("response exceeds configured byte limit")
    payload = parse_json(response.text)
    adapter(stage, preview["lookup_allowed"]).validate_python(payload)
    usage = response.actual_usage
    # Synthetic usage is not provider usage or an actual bill.
    return {"version": "s4_provider_transport_simulation_v1", "execution_kind": "offline_mock_transport",
            "stage": stage, "request_sha256": wire_hash, "response_sha256": digest(response.text.encode("utf-8")),
            "parsed_kind": payload["kind"], "synthetic_input_tokens": usage.input_tokens,
            "synthetic_output_tokens": usage.output_tokens, "actual_provider_tokens": None,
            "provider_billed_cost": None, "actual_model_calls": 0,
            "warning": "transport/schema only; source and state validation not performed"}
