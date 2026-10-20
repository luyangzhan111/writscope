"""Provider contract. M2a only implements a network-free scripted mock."""

from typing import Literal, Protocol

from pydantic import Field, JsonValue

from ..events import StrictModel


class ModelConfig(StrictModel):
    model_id: Literal["scripted-mock-v1"] = "scripted-mock-v1"
    temperature: float = 0.0
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


class ProviderResponse(StrictModel):
    text: str
    synthetic_usage: Usage | None = None


class ProviderError(Exception):
    def __init__(self, message: str, usage: Usage | None = None):
        super().__init__(message)
        self.synthetic_usage = usage


class ScriptExhausted(ProviderError):
    pass


class Provider(Protocol):
    async def generate(self, request: ModelRequest) -> ProviderResponse: ...
