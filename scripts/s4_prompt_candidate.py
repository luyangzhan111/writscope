"""Offline-only S4 prompt and wrapped response-contract preview; no provider."""

from pathlib import Path
from typing import Literal

from pydantic import Field, TypeAdapter

from scripts.m3k_history_lookup import LookupRequest
from scripts.s4_p2 import checked_bundle
from scripts.s4_p2_scope import QualifiedBatch
from writscope.artifacts import digest
from writscope.baselines import build_request as baseline_request
from writscope.contracts import QuoteReviewPayload
from writscope.events import StrictModel
from writscope.m3b import build_request as incremental_request
from writscope.providers import ModelConfig

ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIR = ROOT / "prompts/s4_candidate"
STAGES = ("full_direct", "full_checklist", "incremental_extract", "incremental_final")


class Lookup(LookupRequest):
    kind: Literal["lookup"]
    event_ids: list[str] = Field(min_length=1, max_length=2)


class Terminal(StrictModel):
    kind: Literal["final"]
    payload: QuoteReviewPayload


class Batch(QualifiedBatch):
    kind: Literal["batch"]


def adapter(stage, allow_lookup):
    if stage not in STAGES:
        raise ValueError("unsupported stage")
    primary = Batch if stage == "incremental_extract" else Terminal
    return TypeAdapter(primary | Lookup if allow_lookup else primary)


def prompt(stage):
    if stage not in STAGES:
        raise ValueError("unsupported stage")
    return ((PROMPT_DIR / "common.md").read_text(encoding="utf-8").strip() + "\n\n" +
            (PROMPT_DIR / f"{stage}.md").read_text(encoding="utf-8").strip() + "\n")


def preview(bundle_path, stage, *, event_index=None, prior_state=None, prior_value_scopes=None,
            maintained_state=None, allow_lookup=True):
    """Construct only a local preview, not a runnable request or authorization."""
    if stage not in STAGES or not isinstance(allow_lookup, bool):
        raise ValueError("invalid preview options")
    bundle = checked_bundle(bundle_path)
    instructions = prompt(stage)
    model = ModelConfig()  # placeholder; not an experiment choice
    if stage == "incremental_extract":
        if event_index is None or prior_state is None or prior_value_scopes is None or maintained_state is not None:
            raise ValueError("extraction requires an event index and prior state")
        request = incremental_request(bundle, event_index, prior_state, instructions, model)
        through = bundle.review_input.events[event_index]
    else:
        if event_index is not None or prior_state is not None or prior_value_scopes is not None:
            raise ValueError("terminal preview does not take extraction state")
        if (stage == "incremental_final") != (maintained_state is not None):
            raise ValueError("only incremental final requires maintained state")
        request = baseline_request(bundle, "full_checklist" if stage == "full_checklist" else "full_direct",
                                   instructions, model)
        through = bundle.review_input.events[-1]
    public = dict(request.public_input)
    if stage == "incremental_extract":
        public["prior_value_scopes"] = prior_value_scopes
    if stage == "incremental_final":
        public["maintained_state"] = maintained_state
    # Reflect the current P2 tool policy and visibility; no execution tool is supplied.
    review = dict(public["review_input"])
    review["tool_policy"] = {**review["tool_policy"], "available_tools": ["history_lookup"]}
    public["review_input"] = review
    public["history_lookup_policy"] = {"available_to_all_methods": True, "max_requests_per_trajectory": 3,
        "max_events_per_request": 2, "max_continuations_per_stage": 1,
        "through_event_id": through.event_id, "through_seq": through.seq}
    return {"version": "s4_prompt_contract_preview_v1", "status": "draft_offline_only",
            "stage": stage, "instructions": instructions, "prompt_sha256": digest(instructions.encode("utf-8")),
            "evidence_sha256": request.evidence_sha256, "public_input": public,
            "output_schema": adapter(stage, allow_lookup).json_schema(), "lookup_allowed": allow_lookup,
            "model": None, "paid_execution_allowed": False, "external_transmission_allowed": False}
