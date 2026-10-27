"""Versioned offline task-context ledger; no model inference or authorization.

Each event receives one atomic batch. A batch may contain several exact quotes
from the trusted user event. Empty batches still advance the history cursor.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from writscope.artifacts import canonical_hash
from writscope.events import ConversationEvent, SourceRef, StrictModel


POLICY_VERSION = "task_context_quote_v1"


class ContextQuote(StrictModel):
    source_quote: str = Field(min_length=1)
    verification_refs: list[str]


class ContextBatch(StrictModel):
    event_id: str = Field(min_length=1)
    seq: int = Field(ge=1)
    items: list[ContextQuote]

    @model_validator(mode="after")
    def unique_quotes(self):
        if len({item.source_quote for item in self.items}) != len(self.items):
            raise ValueError("duplicate task-context quote in event")
        return self


class ContextItem(StrictModel):
    context_id: str
    requirement: str
    source_ref: SourceRef
    verification_refs: list[str]


class ContextRecord(StrictModel):
    batch: ContextBatch
    before_hash: str
    after_hash: str
    added_ids: list[str]


class ContextSnapshot(StrictModel):
    schema_version: str
    policy_version: str
    through_event_id: str
    through_seq: int
    source_history_sha256: str
    task_context: list[ContextItem]
    state_hash: str


class TaskContextLedger:
    """Preserve quoted task requirements separately from authorization state."""

    def __init__(self, events: list[ConversationEvent], artifact_ids: set[str] | None = None):
        if [event.seq for event in events] != list(range(1, len(events) + 1)):
            raise ValueError("non-contiguous event history")
        if len({event.event_id for event in events}) != len(events):
            raise ValueError("duplicate event ID")
        self.events = list(events)
        self.artifact_ids = set(artifact_ids) if artifact_ids is not None else None
        self.through_seq = 0
        self.items: list[ContextItem] = []
        self.records: list[ContextRecord] = []

    def snapshot(self) -> ContextSnapshot:
        body = {
            "schema_version": "0.1-draft",
            "policy_version": POLICY_VERSION,
            "through_event_id": self.events[self.through_seq - 1].event_id if self.through_seq else "",
            "through_seq": self.through_seq,
            "source_history_sha256": canonical_hash([
                event.model_dump() for event in self.events[:self.through_seq]]),
            "task_context": [item.model_dump() for item in self.items],
        }
        return ContextSnapshot(**body, state_hash=canonical_hash(body))

    def submit(self, batch: ContextBatch) -> ContextRecord:
        if batch.seq != self.through_seq + 1 or batch.seq > len(self.events):
            raise ValueError("task-context event out of order")
        event = self.events[batch.seq - 1]
        if batch.event_id != event.event_id:
            raise ValueError("task-context event ID mismatch")
        if batch.items and (event.role != "user" or event.provenance.authority_level != "user"):
            raise ValueError("task context requires trusted user source")
        additions = []
        for item in batch.items:
            quote = item.source_quote
            start = event.content.find(quote)
            if start < 0 or event.content.find(quote, start + 1) >= 0:
                raise ValueError("task-context quote missing or non-unique")
            if self.artifact_ids is not None and any(
                    ref not in self.artifact_ids for ref in item.verification_refs):
                raise ValueError("unknown public verification reference")
            if len(set(item.verification_refs)) != len(item.verification_refs):
                raise ValueError("duplicate verification reference")
            source = SourceRef(event_id=event.event_id, quote=quote,
                               start=start, end=start + len(quote))
            additions.append(ContextItem(
                context_id="context_" + canonical_hash({"event_id": event.event_id,
                                                        "quote": quote})[:20],
                requirement=quote, source_ref=source,
                verification_refs=item.verification_refs))
        before = self.snapshot().state_hash
        self.items.extend(additions)
        self.through_seq = event.seq
        record = ContextRecord(batch=batch, before_hash=before,
                               after_hash=self.snapshot().state_hash,
                               added_ids=[item.context_id for item in additions])
        self.records.append(record)
        return record


def replay_context(events: list[ConversationEvent], records: list[ContextRecord],
                   artifact_ids: set[str] | None = None) -> ContextSnapshot:
    ledger = TaskContextLedger(events, artifact_ids)
    for saved in records:
        if ledger.submit(saved.batch) != saved:
            raise ValueError("task-context replay mismatch")
    return ledger.snapshot()
