"""M3o offline model-facing interaction contract. No provider is invoked.

One response either requests a trusted history lookup or submits both task
context and an atomic state batch. Trusted event identity comes from the runner.
"""

from __future__ import annotations

from typing import Annotated, Literal
import argparse
import json
from pathlib import Path

from pydantic import Field, TypeAdapter

from scripts.m3i_task_context import ContextBatch, ContextQuote, ContextSnapshot, TaskContextLedger
from scripts.m3j_transition_batch import BatchExtractionPayload, BatchStateEngine, TransitionBatch, batch_from_response
from scripts.m3k_history_lookup import LookupPolicy, LookupRecord, LookupRequest, replay_lookups
from scripts.m3m_final_review import FinalReviewRequest
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.baselines import build_request as baseline_request
from writscope.contracts import PublicBundle, QuoteReviewPayload, load_bundle, parse_json
from writscope.events import DecisionPoint, StrictModel, validate_source
from writscope.m3b import ExtractionPayload, _artifacts
from writscope.providers import ModelConfig, ModelRequest
from writscope.state import AuthorizationState, verify_state_hash


VERSION = "m3o_interaction_quote_v1"
CASES = ("c001", "c002", "c003")


class InteractionRequest(ModelRequest):
    method: Literal["incremental_interaction"]
    output_contract: Literal["interaction_quote_v1"]


class LookupIntent(StrictModel):
    kind: Literal["lookup_request"]
    event_ids: list[str] = Field(min_length=1, max_length=20)


class ExtractCommit(StrictModel):
    kind: Literal["extract"]
    task_context: list[ContextQuote]
    state_items: list[ExtractionPayload] = Field(min_length=1, max_length=8)


Response = Annotated[LookupIntent | ExtractCommit, Field(discriminator="kind")]
RESPONSE_ADAPTER = TypeAdapter(Response)


class ParsedExtraction(StrictModel):
    context_batch: ContextBatch
    transition_batch: TransitionBatch


class LookupEvidence(StrictModel):
    event_id: str = Field(min_length=1)
    seq: int = Field(ge=1)
    record: LookupRecord


def _history_hash(events) -> str:
    return canonical_hash([event.model_dump() for event in events])


def _check_prior(bundle: PublicBundle, index: int, state: AuthorizationState,
                 context: ContextSnapshot) -> None:
    events = bundle.review_input.events
    if not 0 <= index <= len(events):
        raise ValueError("event index outside visible history")
    previous = events[index - 1].event_id if index else ""
    history_hash = _history_hash(events[:index])
    verify_state_hash(state)
    if (state.case_id != bundle.review_input.case_id or state.through_seq != index or
            state.through_event_id != previous or state.source_history_sha256 != history_hash):
        raise ValueError("prior authorization state cutoff mismatch")
    context_body = context.model_dump(exclude={"state_hash"})
    if (context.state_hash != canonical_hash(context_body) or
            context.through_seq != index or context.through_event_id != previous or
            context.source_history_sha256 != history_hash):
        raise ValueError("prior task context cutoff or hash mismatch")
    for item in context.task_context:
        if (item.requirement != item.source_ref.quote or
                item.source_ref.event_id not in {event.event_id for event in events[:index]} or
                not set(item.verification_refs) <= set(bundle.registry.artifacts)):
            raise ValueError("prior task context source or artifact mismatch")
        validate_source(item.source_ref, events[:index], authorization=True)


def build_request(bundle: PublicBundle, index: int, state: AuthorizationState,
                  context: ContextSnapshot, prompt: str, model: ModelConfig,
                  lookup_policy: LookupPolicy, *, prior_lookup_attempts: int = 0,
                  lookup_records: list[LookupRecord] | None = None) -> InteractionRequest:
    if not 0 <= index < len(bundle.review_input.events):
        raise ValueError("event index outside visible history")
    _check_prior(bundle, index, state, context)
    if not prompt.strip() or prior_lookup_attempts < 0:
        raise ValueError("empty prompt or negative lookup count")
    records = list(lookup_records or [])
    if prior_lookup_attempts + len(records) > lookup_policy.max_requests:
        raise ValueError("lookup budget exhausted")
    event = bundle.review_input.events[index]
    point = DecisionPoint(event_id=event.event_id, seq=event.seq)
    replay_lookups(bundle.review_input.events, point, lookup_policy, records,
                   initial_attempts=prior_lookup_attempts)
    if any(record.status != "succeeded" for record in records):
        raise ValueError("denied lookup cannot enter model request")
    review = bundle.review_input.model_dump()
    review["events"] = [item.model_dump() for item in bundle.review_input.events[:index + 1]]
    review["decision_point"] = point.model_dump()
    base = {"review_input": bundle.review_input.model_dump(),
            "artifacts": dict(bundle.contents),
            "artifact_registry": bundle.registry.model_dump()}
    public = {"review_input": review, "artifacts": dict(bundle.contents),
              "artifact_registry": bundle.registry.model_dump(),
              "prior_state": state.model_dump(),
              "prior_task_context": context.model_dump(),
              "history_lookup": {
                  "policy": lookup_policy.model_dump(),
                  "attempts_used": prior_lookup_attempts + len(records),
                  "remaining_requests": lookup_policy.max_requests - prior_lookup_attempts - len(records),
                  "current_event_results": [item.model_dump() for item in records]}}
    return InteractionRequest(
        method="incremental_interaction", output_contract="interaction_quote_v1",
        model_config_values=model.model_copy(deep=True), instructions=prompt,
        prompt_sha256=digest(prompt.encode("utf-8")),
        evidence_sha256=canonical_hash(base), public_input=public,
        output_schema=RESPONSE_ADAPTER.json_schema())


def build_final_request(bundle: PublicBundle, state: AuthorizationState,
                        context: ContextSnapshot, prompt: str, model: ModelConfig,
                        lookup_policy: LookupPolicy,
                        lookup_history: list[LookupEvidence]) -> FinalReviewRequest:
    """Construct a terminal request from runner-owned complete state and lookups."""
    events = bundle.review_input.events
    _check_prior(bundle, len(events), state, context)
    if not prompt.strip():
        raise ValueError("empty final review prompt")
    previous_seq = 0
    for index, item in enumerate(lookup_history):
        if item.seq < previous_seq or item.seq > len(events):
            raise ValueError("lookup history out of event order")
        previous_seq = item.seq
        if events[item.seq - 1].event_id != item.event_id:
            raise ValueError("lookup history event identity mismatch")
        replay_lookups(events, DecisionPoint(event_id=item.event_id, seq=item.seq),
                       lookup_policy, [item.record], initial_attempts=index)
        if item.record.status != "succeeded":
            raise ValueError("denied lookup cannot enter final request")
    if len(lookup_history) > lookup_policy.max_requests:
        raise ValueError("lookup history exceeds budget")
    base = {"review_input": bundle.review_input.model_dump(),
            "artifacts": dict(bundle.contents),
            "artifact_registry": bundle.registry.model_dump()}
    public = {**base, "current_state": state.model_dump(),
              "task_context": context.model_dump(),
              "history_lookup": {"policy": lookup_policy.model_dump(),
                                 "attempts": [item.model_dump()
                                              for item in lookup_history]}}
    return FinalReviewRequest(
        method="incremental_review", output_contract="source_quote_v1",
        model_config_values=model.model_copy(deep=True), instructions=prompt,
        prompt_sha256=digest(prompt.encode("utf-8")),
        evidence_sha256=canonical_hash(base), public_input=public,
        output_schema=QuoteReviewPayload.model_json_schema())


def parse_response(raw: str, bundle: PublicBundle, index: int, config_hash: str,
                   call_id: str, lookup_policy: LookupPolicy, *,
                   lookup_attempts_used: int = 0) -> LookupIntent | ParsedExtraction:
    if not 0 <= index < len(bundle.review_input.events):
        raise ValueError("event index outside visible history")
    if lookup_attempts_used < 0 or not call_id:
        raise ValueError("invalid trusted call identity or lookup count")
    draft = RESPONSE_ADAPTER.validate_python(parse_json(raw))
    event = bundle.review_input.events[index]
    if isinstance(draft, LookupIntent):
        request = LookupRequest(event_ids=draft.event_ids)
        if lookup_attempts_used >= lookup_policy.max_requests:
            raise ValueError("lookup request budget exhausted")
        if len(request.event_ids) > lookup_policy.max_events_per_request:
            raise ValueError("lookup event count exceeds limit")
        visible = {item.event_id for item in bundle.review_input.events[:index + 1]}
        if not set(request.event_ids) <= visible:
            raise ValueError("lookup event outside visible history")
        return draft
    if draft.task_context and (
            event.role != "user" or event.provenance.authority_level != "user"):
        raise ValueError("task context requires trusted user source")
    if len({item.source_quote for item in draft.task_context}) != len(draft.task_context):
        raise ValueError("duplicate task-context quote in event")
    for item in draft.task_context:
        quote = item.source_quote
        start = event.content.find(quote)
        if start < 0 or event.content.find(quote, start + 1) >= 0:
            raise ValueError("task-context quote missing or non-unique")
        if not set(item.verification_refs) <= set(bundle.registry.artifacts):
            raise ValueError("unknown public verification reference")
        if len(item.verification_refs) != len(set(item.verification_refs)):
            raise ValueError("duplicate verification reference")
    for item in draft.state_items:
        quote = item.source_quote
        start = event.content.find(quote)
        if start < 0 or event.content.find(quote, start + 1) >= 0:
            raise ValueError("state source quote missing or non-unique")
        if item.operation != "no_change" and (
                event.role != "user" or event.provenance.authority_level != "user"):
            raise ValueError("state mutation requires trusted user source")
    context = ContextBatch(event_id=event.event_id, seq=event.seq,
                           items=draft.task_context)
    batch = batch_from_response(
        json.dumps(BatchExtractionPayload(items=draft.state_items).model_dump(),
                   ensure_ascii=False), bundle, index, config_hash, call_id)
    return ParsedExtraction(context_batch=context, transition_batch=batch)


def _preview(root: Path, prompt: str, model: ModelConfig,
             lookup_policy: LookupPolicy) -> dict[str, dict]:
    result = {}
    for case in CASES:
        bundle = load_bundle(root / "reports/m1-2026-10-18/inputs/verified_impact" / case)
        if bundle.review_input.case_id != case:
            raise ValueError("case identity mismatch")
        state = BatchStateEngine(case, bundle.review_input.events, "0" * 64,
                                 set(bundle.registry.artifacts)).snapshot()
        context = TaskContextLedger(bundle.review_input.events,
                                    set(bundle.registry.artifacts)).snapshot()
        request = build_request(bundle, 0, state, context, prompt, model, lookup_policy)
        baseline = baseline_request(bundle, "full_checklist", "public parity check", model)
        if request.evidence_sha256 != baseline.evidence_sha256:
            raise ValueError("baseline evidence mismatch")
        result[case] = request.model_dump()
    return result


def _manifest(preview: dict[str, dict], prompt: str, model: ModelConfig,
              policy: LookupPolicy, artifacts: dict[str, str]) -> dict:
    return {"schema_version": "0.1-draft", "version": VERSION,
            "execution_kind": "offline_request_preview", "status": "prepared",
            "case_ids": list(CASES), "actual_model_calls": 0,
            "prompt_sha256": digest(prompt.encode("utf-8")),
            "model_config": model.model_dump(), "lookup_policy": policy.model_dump(),
            "request_sha256": {case: canonical_hash(value)
                               for case, value in preview.items()},
            "runner_sha256": digest(Path(__file__).read_bytes()),
            "artifacts": artifacts, "artifact_set_sha256": canonical_hash(artifacts)}


def run_preview(root: Path, output: Path, prompt: str,
                model: ModelConfig | None = None,
                lookup_policy: LookupPolicy | None = None) -> dict:
    root, output = root.resolve(), output.resolve()
    if output.is_relative_to(root / "reports/m1-2026-10-18/inputs"):
        raise ValueError("preview cannot modify public input")
    model = model or ModelConfig()
    lookup_policy = lookup_policy or LookupPolicy(max_requests=2,
                                                  max_events_per_request=2)
    preview = _preview(root, prompt, model, lookup_policy)
    output.mkdir(parents=True, exist_ok=False)
    (output / "prompt.md").write_text(prompt, encoding="utf-8", newline="\n")
    write_json(output / "model_config.json", model.model_dump())
    write_json(output / "lookup_policy.json", lookup_policy.model_dump())
    for case, request in preview.items():
        write_json(output / f"{case}-request.json", request)
    manifest = _manifest(preview, prompt, model, lookup_policy, _artifacts(output))
    write_json(output / "run.json", manifest)
    return manifest


def verify_preview(root: Path, output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    prompt = (output / "prompt.md").read_text(encoding="utf-8")
    model = ModelConfig.model_validate(parse_json(
        (output / "model_config.json").read_text(encoding="utf-8")))
    policy = LookupPolicy.model_validate(parse_json(
        (output / "lookup_policy.json").read_text(encoding="utf-8")))
    preview = _preview(root, prompt, model, policy)
    for case, request in preview.items():
        saved = parse_json((output / f"{case}-request.json").read_text(encoding="utf-8"))
        if saved != request:
            raise ValueError("interaction request replay mismatch")
    expected = _manifest(preview, prompt, model, policy, _artifacts(output))
    saved_manifest = parse_json((output / "run.json").read_text(encoding="utf-8"))
    if saved_manifest != expected:
        raise ValueError("interaction preview manifest mismatch")
    return expected


def main(argv=None):
    parser = argparse.ArgumentParser(description="M3o offline interaction contract preview")
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt", type=Path)
    args = parser.parse_args(argv)
    if args.command == "run":
        if args.prompt is None:
            parser.error("run requires --prompt")
        result = run_preview(args.root, args.output,
                             args.prompt.read_text(encoding="utf-8"))
    else:
        result = verify_preview(args.root, args.output)
    print(result["status"])


if __name__ == "__main__":
    main()
