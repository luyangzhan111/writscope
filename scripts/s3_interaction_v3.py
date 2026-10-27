"""Versioned incremental interaction with on-demand trusted history lookup.

Earlier raw event contents are withheld from the model request and returned only
after a model-selected lookup. The parser still validates against the trusted full
local bundle. No response is repaired and no reference labels are read.
"""
from __future__ import annotations

from typing import Literal

from scripts import m3o_interaction_contract as legacy
from scripts import s3_interaction_v2 as v2
from scripts.m3k_history_lookup import LookupPolicy, LookupRecord
from scripts.m3i_task_context import ContextSnapshot
from scripts.s3_interaction_v2 import RESPONSE_ADAPTER
from writscope.artifacts import canonical_hash
from writscope.contracts import PublicBundle
from writscope.providers import ModelConfig

VERSION = "interaction_quote_v3_on_demand_history"


class InteractionRequest(legacy.InteractionRequest):
    output_contract: Literal["interaction_quote_v3"]


def build_request(bundle: PublicBundle, index: int, state, context: ContextSnapshot,
                  prompt: str, model: ModelConfig, lookup_policy: LookupPolicy, *,
                  prior_lookup_attempts: int = 0,
                  lookup_records: list[LookupRecord] | None = None) -> InteractionRequest:
    """Hide prior event text; retain a nonsemantic index and current event."""
    original = v2.build_request(
        bundle, index, state, context, prompt, model, lookup_policy,
        prior_lookup_attempts=prior_lookup_attempts, lookup_records=lookup_records)
    value = original.model_dump()
    visible = value["public_input"]["review_input"]["events"]
    if len(visible) != index + 1:
        raise ValueError("source interaction did not contain the exact visible prefix")
    earlier = visible[:-1]
    current = visible[-1]
    value["public_input"]["review_input"]["events"] = [current]
    value["public_input"]["history_index"] = [
        {"event_id": event["event_id"], "seq": event["seq"], "role": event["role"]}
        for event in earlier
    ]
    value["output_contract"] = "interaction_quote_v3"
    value["output_schema"] = RESPONSE_ADAPTER.json_schema()
    result = InteractionRequest.model_validate(value)
    # The full evidence identity remains bound to the original complete public bundle.
    if result.evidence_sha256 != original.evidence_sha256:
        raise ValueError("history visibility policy changed evidence identity")
    current_event = bundle.review_input.events[index]
    if result.public_input["review_input"]["events"] != [current_event.model_dump()]:
        raise ValueError("current event visibility mismatch")
    return result


def parse_response(raw: str, bundle: PublicBundle, index: int, config_hash: str,
                   call_id: str, lookup_policy: LookupPolicy, *,
                   lookup_attempts_used: int = 0):
    # v2 rejects invalid shapes and delegates provenance, time and scope checks to v1.
    return v2.parse_response(raw, bundle, index, config_hash, call_id, lookup_policy,
                             lookup_attempts_used=lookup_attempts_used)


def config_hash(prompt, final_prompt, model, policy):
    return canonical_hash({"contract": VERSION, "interaction_prompt": prompt,
                           "final_prompt": final_prompt, "model": model.model_dump(),
                           "lookup_policy": policy.model_dump(),
                           "history_visibility": "current_event_only_until_lookup"})
