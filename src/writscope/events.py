"""Strict event loading and source validation, without semantic extraction."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "0.1-draft"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Provenance(StrictModel):
    origin: Literal["synthetic_fixture"]
    authority_level: Literal["user", "none"]
    artifact_refs: list[str]


class ConversationEvent(StrictModel):
    schema_version: Literal["0.1-draft"]
    event_id: str = Field(min_length=1)
    seq: int = Field(ge=1)
    role: Literal["user", "assistant", "tool", "system"]
    content: str = Field(min_length=1)
    provenance: Provenance

    @model_validator(mode="after")
    def check_authority(self):
        expected = "user" if self.role == "user" else "none"
        if self.provenance.authority_level != expected:
            raise ValueError("role and trusted fixture provenance disagree")
        return self


class DecisionPoint(StrictModel):
    event_id: str = Field(min_length=1)
    seq: int = Field(ge=1)


class SourceRef(StrictModel):
    event_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


def load_events(path: Path, point: DecisionPoint) -> list[ConversationEvent]:
    events = [
        ConversationEvent.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    if [e.seq for e in events] != list(range(1, len(events) + 1)):
        raise ValueError("event sequence must be contiguous starting at 1")
    if len({e.event_id for e in events}) != len(events):
        raise ValueError("duplicate event ID")
    visible = [e for e in events if e.seq <= point.seq]
    if not visible or visible[-1].event_id != point.event_id or visible[-1].seq != point.seq:
        raise ValueError("decision point does not identify the final visible event")
    return visible


def validate_source(ref: SourceRef, events: list[ConversationEvent], *, authorization=False):
    event = next((e for e in events if e.event_id == ref.event_id), None)
    if event is None or ref.end > len(event.content) or ref.start >= ref.end:
        raise ValueError("source outside visible history or invalid span")
    if event.content[ref.start:ref.end] != ref.quote:
        raise ValueError("source quote mismatch")
    if authorization and (event.role != "user" or event.provenance.authority_level != "user"):
        raise ValueError("only a user source can authorize a change")
    return event


class JudgmentLabels(StrictModel):
    """Validate label combinations; never infer labels from a history."""

    impact: Literal["compatible", "breaking", "unknown"]
    authorization: Literal["covered", "not_covered", "ambiguous", "not_needed"]
    decision: Literal["allow", "revise", "ask", "investigate"]

    @model_validator(mode="after")
    def consistent(self):
        if self.impact == "unknown":
            expected = "investigate"
        else:
            combinations = {
                ("compatible", "not_needed"): "allow",
                ("breaking", "covered"): "allow",
                ("breaking", "not_covered"): "revise",
                ("breaking", "ambiguous"): "ask",
            }
            expected = combinations.get((self.impact, self.authorization))
        if expected is None or self.decision != expected:
            raise ValueError("inconsistent three-axis label combination")
        return self
