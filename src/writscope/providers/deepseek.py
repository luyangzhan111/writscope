"""One fixed DeepSeek Chat Completions backend; no tools or arbitrary URL."""

import json

import httpx

from ..budget import PriceCeiling
from ..contracts import parse_json
from . import ActualUsage, ModelRequest, ProviderError, ProviderResponse


ENDPOINT = "https://api.deepseek.com/chat/completions"
MODEL_ID = "deepseek-flash"


def wire_payload(request: ModelRequest) -> dict:
    if request.model_config_values.model_id != MODEL_ID:
        raise ValueError("DeepSeek provider requires deepseek-flash")
    if request.model_config_values.temperature is None:
        raise ValueError("non-thinking M2b requires an explicit temperature")
    return {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": request.instructions},
            {"role": "user", "content": json.dumps({"public_input": request.public_input,
                                                   "output_schema": request.output_schema},
                                                  ensure_ascii=False, separators=(",", ":"))},
        ],
        "thinking": {"type": "disabled"},
        "temperature": request.model_config_values.temperature,
        "max_tokens": request.model_config_values.max_output_tokens,
        "response_format": {"type": "json_object"},
        "tool_choice": "none",
        "stream": False,
    }


def wire_bytes(request: ModelRequest) -> bytes:
    return json.dumps(wire_payload(request), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _actual_usage(usage: dict | None, price: PriceCeiling) -> ActualUsage:
    if not isinstance(usage, dict):
        return ActualUsage(usage_source="unavailable", cost_source="unavailable")
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    hit = usage.get("prompt_cache_hit_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    if any(value is not None and (not isinstance(value, int) or value < 0) for value in (prompt, completion, hit, miss, reasoning)):
        raise ValueError("invalid provider token usage")
    if hit is not None and miss is not None and prompt is not None and hit + miss != prompt:
        raise ValueError("provider cache usage does not sum to prompt usage")
    estimate = None
    if prompt is not None and completion is not None:
        # Charge every input token at the highest published cache-miss/peak
        # rate. This is a conservative estimate, not the provider's bill.
        estimate = ((prompt * price.input_cny_per_million +
                     completion * price.output_cny_per_million) / 1_000_000)
    return ActualUsage(input_tokens=prompt, output_tokens=completion,
                       cache_hit_input_tokens=hit, cache_miss_input_tokens=miss,
                       reasoning_tokens=reasoning, estimated_cost_cny=estimate,
                       billed_cost_cny=None, usage_source="provider_response",
                       cost_source="official_peak_price_estimate" if estimate is not None else "unavailable")


class DeepSeekProvider:
    def __init__(self, api_key: str, price: PriceCeiling, *, client: httpx.AsyncClient | None = None):
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is required")
        self._api_key = api_key
        self.price = price
        self._client = client

    async def generate(self, request: ModelRequest) -> ProviderResponse:
        payload = wire_payload(request)
        client = self._client or httpx.AsyncClient(timeout=None, follow_redirects=False)
        try:
            response = await client.post(ENDPOINT, json=payload,
                                         headers={"Authorization": "Bearer " + self._api_key,
                                                  "Content-Type": "application/json"})
        except httpx.HTTPError as error:
            raise ProviderError("DeepSeek transport error: " + type(error).__name__) from None
        finally:
            if self._client is None:
                await client.aclose()
        if response.status_code != 200:
            # Never persist the provider body: it can reflect request material.
            raise ProviderError(f"DeepSeek HTTP {response.status_code}",
                                retryable=response.status_code in (429, 500, 502, 503, 504))
        try:
            data = parse_json(response.text)
            choice = data["choices"][0]
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("missing assistant text")
            usage = _actual_usage(data.get("usage"), self.price)
            return ProviderResponse(text=content, actual_usage=usage,
                                    provider_response_id=data.get("id"), served_model=data.get("model"),
                                    system_fingerprint=data.get("system_fingerprint"),
                                    finish_reason=choice.get("finish_reason"))
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ProviderError("DeepSeek response contract error: " + type(error).__name__, retryable=False) from None
