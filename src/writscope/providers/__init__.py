"""Provider contract. M2a only implements a network-free scripted mock."""

from decimal import Decimal
from typing import Literal, Protocol

from pydantic import Field, JsonValue

from ..events import StrictModel


class ModelConfig(StrictModel):
    model_id: str = Field(default="scripted-mock-v1", min_length=1)
    temperature: float | None = Field(default=0.0, allow_inf_nan=False)
    max_output_tokens: int = Field(default=4096, ge=1)


class ModelRequest(StrictModel):
    schema_version: Literal["0.1-draft"] = "0.1-draft"
    method: Literal["full_direct", "full_checklist"]
    model_config_values: ModelConfig
    instructions: str
    prompt_sha256: str
    evidence_sha256: str
    public_input: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]


class Usage(StrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ActualUsage(StrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_hit_input_tokens: int | None = Field(default=None, ge=0)
    cache_miss_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_cny: Decimal | None = Field(default=None, ge=0)
    billed_cost_cny: Decimal | None = Field(default=None, ge=0)
    usage_source: Literal["provider_response", "unavailable"]
    cost_source: Literal["official_peak_price_estimate", "unavailable"]


class ProviderResponse(StrictModel):
    text: str
    synthetic_usage: Usage | None = None
    actual_usage: ActualUsage | None = None
    provider_response_id: str | None = None
    served_model: str | None = None
    system_fingerprint: str | None = None
    finish_reason: str | None = None


class ProviderError(Exception):
    def __init__(self, message: str, usage: Usage | None = None, *, retryable: bool = True):
        super().__init__(message)
        self.synthetic_usage = usage
        self.retryable = retryable


class ScriptExhausted(ProviderError):
    pass


class Provider(Protocol):
    async def generate(self, request: ModelRequest) -> ProviderResponse: ...
