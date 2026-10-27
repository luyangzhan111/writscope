"""Versioned interaction contract with typed candidate-condition evidence refs."""
from __future__ import annotations

from copy import deepcopy
from typing import Literal

from scripts import s3_interaction_v3 as v3
from scripts.m3i_task_context import ContextSnapshot
from scripts.m3k_history_lookup import LookupPolicy, LookupRecord
from scripts.m3o_interaction_contract import ParsedExtraction
from writscope.artifacts import canonical_hash
from writscope.contracts import PublicBundle, parse_json
from writscope.providers import ModelConfig

VERSION = "interaction_quote_v4_typed_condition_evidence_refs"
SUPPORTED_CONDITION = "candidate_sha256"


class InteractionRequest(v3.InteractionRequest):
    output_contract: Literal["interaction_quote_v4"]


def build_request(bundle: PublicBundle, index: int, state, context: ContextSnapshot,
                  prompt: str, model: ModelConfig, lookup_policy: LookupPolicy, *,
                  prior_lookup_attempts: int = 0,
                  lookup_records: list[LookupRecord] | None = None) -> InteractionRequest:
    original = v3.build_request(bundle, index, state, context, prompt, model, lookup_policy,
                                prior_lookup_attempts=prior_lookup_attempts,
                                lookup_records=lookup_records)
    value = original.model_dump()
    schema = deepcopy(value["output_schema"])
    condition = schema.get("$defs", {}).get("Condition")
    if not isinstance(condition, dict):
        raise ValueError("condition schema definition missing")
    condition["properties"]["kind"] = {
        "title": "Kind", "type": "string", "const": SUPPORTED_CONDITION}
    condition["properties"]["value"] = {
        "title": "Value", "type": "string", "pattern": "^[0-9a-f]{64}$"}
    condition["properties"]["evidence_refs"]["items"]["enum"] = sorted(bundle.registry.artifacts)
    value.update(output_contract="interaction_quote_v4", output_schema=schema)
    result = InteractionRequest.model_validate(value)
    if result.evidence_sha256 != original.evidence_sha256:
        raise ValueError("typed condition schema changed evidence identity")
    return result


def parse_response(raw: str, bundle: PublicBundle, index: int, config_hash: str,
                   call_id: str, lookup_policy: LookupPolicy, *,
                   lookup_attempts_used: int = 0):
    parsed = v3.parse_response(raw, bundle, index, config_hash, call_id, lookup_policy,
                               lookup_attempts_used=lookup_attempts_used)
    if isinstance(parsed, ParsedExtraction):
        for proposal in parsed.transition_batch.proposals:
            if proposal.authorization is None:
                continue
            for condition in proposal.authorization.conditions:
                if condition.kind != SUPPORTED_CONDITION:
                    raise ValueError("unsupported condition kind for typed interaction contract")
                if not set(condition.evidence_refs) <= set(bundle.registry.artifacts):
                    raise ValueError("condition evidence refs must be public artifact IDs")
    return parsed


def config_hash(prompt, final_prompt, model, policy):
    return canonical_hash({"contract": VERSION, "prompt": prompt,
                           "final_prompt": final_prompt, "model": model.model_dump(),
                           "lookup_policy": policy.model_dump(),
                           "history_visibility": "current_event_only_until_lookup"})
