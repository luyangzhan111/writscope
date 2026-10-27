"""Operation-specific model schema; delegates provenance and semantics to v1."""
from __future__ import annotations

import json
from typing import Annotated, Literal, Union

from pydantic import Field, TypeAdapter, model_validator

from scripts import m3o_interaction_contract as legacy
from scripts.m3i_task_context import ContextQuote
from writscope.artifacts import canonical_hash
from writscope.events import StrictModel
from writscope.state import AuthorizationDraft, ConstraintDraft

VERSION = "interaction_quote_v2_operation_shapes"
Text = Annotated[str, Field(min_length=1)]


class NewConstraint(ConstraintDraft):
    supersedes: list[Text] = Field(max_length=0)


class NewAuthorization(AuthorizationDraft):
    supersedes: list[Text] = Field(max_length=0)


class ReplacementConstraint(ConstraintDraft):
    supersedes: list[Text] = Field(min_length=1)


class ReplacementAuthorization(AuthorizationDraft):
    supersedes: list[Text] = Field(min_length=1)


class Item(StrictModel):
    source_quote: Text
    target_ids: list[Text] = Field(max_length=0)
    constraint: None
    authorization: None

    @model_validator(mode="after")
    def target_identity(self):
        if len(set(self.target_ids)) != len(self.target_ids):
            raise ValueError("duplicate target ID")
        if getattr(self, "operation", None) == "supersede":
            draft = self.constraint if self.constraint is not None else self.authorization
            if draft.supersedes != self.target_ids:
                raise ValueError("replacement supersedes must exactly match target_ids")
        return self


class AddConstraint(Item):
    operation: Literal["add_constraint"]
    constraint: NewConstraint


class Grant(Item):
    operation: Literal["grant"]
    authorization: NewAuthorization


class ReplaceConstraint(Item):
    operation: Literal["supersede"]
    target_ids: list[Text] = Field(min_length=1)
    constraint: ReplacementConstraint


class ReplaceAuthorization(Item):
    operation: Literal["supersede"]
    target_ids: list[Text] = Field(min_length=1)
    authorization: ReplacementAuthorization


class Revoke(Item):
    operation: Literal["revoke"]
    target_ids: list[Text] = Field(min_length=1)


class MarkAmbiguous(Item):
    operation: Literal["mark_ambiguous"]
    target_ids: list[Text] = Field(min_length=1)


class NoChange(Item):
    operation: Literal["no_change"]


class NoChangeCommit(StrictModel):
    kind: Literal["extract"]
    task_context: list[ContextQuote]
    state_items: list[NoChange] = Field(min_length=1, max_length=1)


class MutationCommit(StrictModel):
    kind: Literal["extract"]
    task_context: list[ContextQuote]
    state_items: list[Union[AddConstraint, Grant, ReplaceConstraint, ReplaceAuthorization,
                           Revoke, MarkAmbiguous]] = Field(min_length=1, max_length=8)


RESPONSE_ADAPTER = TypeAdapter(Union[legacy.LookupIntent, NoChangeCommit, MutationCommit])


class InteractionRequest(legacy.InteractionRequest):
    output_contract: Literal["interaction_quote_v2"]


def config_hash(prompt, final_prompt, model, policy):
    return canonical_hash({"contract": VERSION, "interaction_prompt": prompt,
                           "final_prompt": final_prompt, "model": model.model_dump(),
                           "lookup_policy": policy.model_dump()})


def build_request(*args, **kwargs):
    original = legacy.build_request(*args, **kwargs)
    value = original.model_dump()
    value.update(output_contract="interaction_quote_v2", output_schema=RESPONSE_ADAPTER.json_schema())
    return InteractionRequest.model_validate(value)


def parse_response(raw, *args, **kwargs):
    # Reject, never repair: the exact same response is passed to all old gates.
    RESPONSE_ADAPTER.validate_json(raw)
    return legacy.parse_response(raw, *args, **kwargs)
