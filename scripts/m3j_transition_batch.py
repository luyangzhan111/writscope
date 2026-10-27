"""Versioned offline atomic multi-transition batches for one visible event.

No provider is called. Historical single-transition engines and runs are intact.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from scripts.m3f_scope_policy import ScopeValidatedStateEngine
from writscope.artifacts import canonical_hash
from writscope.contracts import SourceQuote, parse_json
from writscope.events import SourceRef, StrictModel
from writscope.m3b import ExtractionPayload
from writscope.state import AuthorizationState, TransitionProposal, _resolved_source


POLICY_VERSION = "transition_batch_v1_offline"
OUTPUT_CONTRACT = "transition_batch_quote_v1"


class BatchExtractionPayload(StrictModel):
    """Only model-produced fields; the runner supplies all trusted identity."""

    items: list[ExtractionPayload] = Field(min_length=1, max_length=8)


class TransitionBatch(StrictModel):
    schema_version: str
    case_id: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)
    source_seq: int = Field(ge=1)
    extractor_config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    extraction_call_id: str | None
    proposals: list[TransitionProposal] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def same_trusted_identity(self):
        if self.schema_version != "0.1-draft":
            raise ValueError("unsupported batch schema")
        for proposal in self.proposals:
            if (proposal.case_id != self.case_id or
                    proposal.source_event_id != self.source_event_id or
                    proposal.source_seq != self.source_seq or
                    proposal.extractor_config_hash != self.extractor_config_hash or
                    proposal.extraction_call_id != self.extraction_call_id):
                raise ValueError("batch proposal identity mismatch")
        if len(self.proposals) > 1 and any(
                proposal.operation == "no_change" for proposal in self.proposals):
            raise ValueError("no_change must be the only batch proposal")
        return self


def batch_from_response(raw: str, bundle, event_index: int,
                        config_hash: str, call_id: str) -> TransitionBatch:
    payload = BatchExtractionPayload.model_validate(parse_json(raw))
    if not 0 <= event_index < len(bundle.review_input.events):
        raise ValueError("event index outside visible history")
    event = bundle.review_input.events[event_index]
    proposals = [TransitionProposal(
        schema_version="0.1-draft", case_id=bundle.review_input.case_id,
        source_event_id=event.event_id, source_seq=event.seq,
        extractor_config_hash=config_hash, extraction_call_id=call_id,
        operation=item.operation, source=SourceQuote(event_id=event.event_id,
                                                    quote=item.source_quote),
        target_ids=item.target_ids, constraint=item.constraint,
        authorization=item.authorization) for item in payload.items]
    return TransitionBatch(
        schema_version="0.1-draft", case_id=bundle.review_input.case_id,
        source_event_id=event.event_id, source_seq=event.seq,
        extractor_config_hash=config_hash, extraction_call_id=call_id,
        proposals=proposals)


class BatchRecord(StrictModel):
    schema_version: str
    policy_version: str
    transition_id: str
    batch: TransitionBatch
    before_hash: str
    after_hash: str
    accepted: bool
    consumed_event: bool
    failed_item_index: int | None
    rejection_reason: str | None
    source_refs: list[SourceRef]


class BatchStateEngine(ScopeValidatedStateEngine):
    """Commit all proposals for an event together, or none of them."""

    def __init__(self, case_id, events, extractor_config_hash, artifact_ids=None):
        super().__init__(case_id, events, extractor_config_hash, artifact_ids)
        self.batch_records: list[BatchRecord] = []
        self.stopped = False

    def submit(self, proposal):
        raise ValueError("batch engine requires submit_batch")

    def submit_batch(self, batch: TransitionBatch) -> BatchRecord:
        before = self.snapshot().state_hash
        transition_id = "batch_" + canonical_hash({
            "policy_version": POLICY_VERSION, "case_id": self.case_id,
            "attempt": len(self.batch_records) + 1, "before_hash": before,
            "batch_hash": canonical_hash(batch.model_dump()),
        })[:20]
        consumed = False
        accepted = False
        failed_index = None
        error = None
        refs: list[SourceRef] = []
        if self.stopped:
            error = "prior batch failure stops trajectory"
        elif batch.case_id != self.case_id or batch.extractor_config_hash != self.extractor_config_hash:
            error = "case or extractor config mismatch"
        elif batch.source_seq != self.through_seq + 1 or batch.source_seq > len(self.events):
            error = "batch event out of order or outside visible history"
        elif batch.source_event_id != self.events[batch.source_seq - 1].event_id:
            error = "batch event ID disagrees with trusted history"
        else:
            event = self.events[batch.source_seq - 1]
            consumed = True
            constraints = dict(self.constraints)
            authorizations = dict(self.authorizations)
            exceptions = dict(self.exceptions)
            for index, proposal in enumerate(batch.proposals):
                try:
                    ref = _resolved_source(proposal.source, event,
                                           mutation=proposal.operation != "no_change")
                    self._apply(proposal, event, ref, constraints, authorizations, exceptions)
                    refs.append(ref)
                except ValueError as exc:
                    failed_index, error = index, str(exc)
                    break
            if error is None:
                self.constraints, self.authorizations, self.exceptions = (
                    constraints, authorizations, exceptions)
                accepted = True
            else:
                refs = []  # A rejected batch contributes no new state item or accepted source.
                self.stopped = True
            self.through_seq = event.seq
            self.transition_refs.append(transition_id)
        record = BatchRecord(
            schema_version="0.1-draft", policy_version=POLICY_VERSION,
            transition_id=transition_id, batch=batch, before_hash=before,
            after_hash=self.snapshot().state_hash, accepted=accepted,
            consumed_event=consumed, failed_item_index=failed_index,
            rejection_reason=error, source_refs=refs)
        self.batch_records.append(record)
        return record


def replay_batches(case_id: str, events, extractor_config_hash: str,
                   records: list[BatchRecord], artifact_ids=None) -> AuthorizationState:
    engine = BatchStateEngine(case_id, events, extractor_config_hash, artifact_ids)
    for saved in records:
        if engine.submit_batch(saved.batch) != saved:
            raise ValueError("batch replay mismatch")
    return engine.snapshot()
