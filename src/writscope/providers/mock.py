"""Responses follow an explicit script, never case IDs, keywords or labels."""

import asyncio
from pathlib import Path
from typing import Literal

from pydantic import Field

from ..contracts import parse_json
from ..events import StrictModel
from . import ModelRequest, ProviderError, ProviderResponse, ScriptExhausted, Usage


class Step(StrictModel):
    action: Literal["response", "error"]
    text: str = ""
    response_ref: str | None = None
    delay_seconds: float = Field(default=0.0, ge=0, le=60, allow_inf_nan=False)
    synthetic_usage: Usage | None = None


class Script(StrictModel):
    schema_version: Literal["0.1-draft"]
    execution_kind: Literal["offline_mock"]
    description: str
    steps: list[Step] = Field(min_length=1)


def load_script(path: Path) -> Script:
    script = Script.model_validate(parse_json(path.read_text(encoding="utf-8")))
    for step in script.steps:
        if step.response_ref is not None:
            from ..artifacts import safe_path
            if step.action != "response" or step.text:
                raise ValueError("response_ref requires an empty response step")
            step.text = safe_path(path.parent, step.response_ref).read_text(encoding="utf-8")
    return script


class ScriptedMock:
    def __init__(self, script: Script):
        self.script = script.model_copy(deep=True)
        self.position = 0

    async def generate(self, request: ModelRequest) -> ProviderResponse:
        # Deliberately do not inspect the request, including case_id and messages.
        if self.position >= len(self.script.steps):
            raise ScriptExhausted("mock script exhausted")
        step = self.script.steps[self.position]
        self.position += 1
        await asyncio.sleep(step.delay_seconds)
        if step.action == "error":
            raise ProviderError(step.text, step.synthetic_usage)
        return ProviderResponse(text=step.text, synthetic_usage=step.synthetic_usage)
