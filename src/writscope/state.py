"""M3a offline authorization-state kernel; proposals are inputs, not model predictions.

The kernel checks provenance and exact scope, but cannot certify that an
extractor interpreted natural language correctly. No labels or providers are
imported here.
"""

from __future__ import annotations

import time
from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from .artifacts import canonical_hash
from .contracts import Impact, ImpactEvidence, SourceQuote, Subject
from .events import ConversationEvent, DecisionPoint, SourceRef, StrictModel, validate_source


Text = Annotated[str, Field(min_length=1)]
SHA = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Operation = Literal["add_constraint", "grant", "revoke", "supersede", "mark_ambiguous", "no_change"]
ChangeType = Literal["add_alias", "remove_field", "replace_field", "change_value_semantics"]
Lifecycle = Literal["active", "superseded", "revoked"]
Interpretation = Literal["resolved", "ambiguous"]
Verdict = Literal["covered", "not_covered", "ambiguous"]


class Predicate(StrictModel):
    kind: Literal["field_exists", "value_semantics", "response_shape"]
    expected: JsonValue
    qualifiers: dict[str, JsonValue]


class Condition(StrictModel):
    kind: Text
    value: Text
    resolution: Literal["satisfied", "unsatisfied", "unknown"]
    evidence_refs: list[Text]

    @model_validator(mode="after")
    def valid_hash(self):
        if self.kind == "candidate_sha256" and (len(self.value) != 64 or
                                                  any(char not in "0123456789abcdef" for char in self.value)):
            raise ValueError("candidate_sha256 condition needs a lowercase SHA-256")
        return self


class ChangeDetails(StrictModel):
    old_field: Text
    new_field: str | None
    preserve_value_semantics: bool


class ConstraintDraft(StrictModel):
    constraint_id: Text
    subjects: list[Subject] = Field(min_length=1)
    predicate: Predicate
    interpretation: Interpretation
    supersedes: list[Text]


class AuthorizationDraft(StrictModel):
    authorization_id: Text
    subjects: list[Subject] = Field(min_length=1)
    change_type: ChangeType
    change_details: ChangeDetails
    conditions: list[Condition]
    interpretation: Interpretation
    constraint_refs: list[Text]
    supersedes: list[Text]

    @model_validator(mode="after")
    def operation_details(self):
        if self.change_type in {"replace_field", "add_alias"}:
            if not self.change_details.new_field or self.change_details.new_field == self.change_details.old_field:
                raise ValueError("replacement or alias grant needs a distinct new field")
        elif self.change_details.new_field is not None:
            raise ValueError("remove or semantic grant cannot name a new field")
        if len(set(self.constraint_refs)) != len(self.constraint_refs):
            raise ValueError("duplicate constraint reference")
        return self


class TransitionProposal(StrictModel):
    """One extractor proposal per event in M3a; no model or label is required."""

    schema_version: Literal["0.1-draft"]
    case_id: Text
    source_event_id: Text
    source_seq: int = Field(ge=1)
    extractor_config_hash: SHA
    extraction_call_id: str | None
    operation: Operation
    source: SourceQuote
    target_ids: list[Text]
    constraint: ConstraintDraft | None
    authorization: AuthorizationDraft | None

    @model_validator(mode="after")
    def shape(self):
        if self.source.event_id != self.source_event_id:
            raise ValueError("proposal source must be its current event")
        if len(set(self.target_ids)) != len(self.target_ids):
            raise ValueError("duplicate target ID")
        has_constraint = self.constraint is not None
        has_auth = self.authorization is not None
        if self.operation == "add_constraint":
            valid = has_constraint and not has_auth and not self.target_ids and not self.constraint.supersedes
        elif self.operation == "grant":
            valid = has_auth and not has_constraint and not self.target_ids and not self.authorization.supersedes
        elif self.operation == "supersede":
            draft = self.constraint if has_constraint else self.authorization
            valid = (has_constraint != has_auth and bool(self.target_ids) and
                     draft.supersedes == self.target_ids)
        elif self.operation in {"revoke", "mark_ambiguous"}:
            valid = not has_constraint and not has_auth and bool(self.target_ids)
        else:
            valid = not has_constraint and not has_auth and not self.target_ids
        if not valid:
            raise ValueError("proposal operation/payload/targets mismatch")
        return self


class Span(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.start >= self.end:
            raise ValueError("empty source span")
        return self


class Constraint(StrictModel):
    constraint_id: Text
    source_event_id: Text
    source_quote: Text
    source_span: Span
    subjects: list[Subject] = Field(min_length=1)
    predicate: Predicate
    lifecycle: Lifecycle
    interpretation: Interpretation
    valid_from: DecisionPoint
    updated_by: list[Text]
    supersedes: list[Text]

    @property
    def source_ref(self) -> SourceRef:
        return SourceRef(event_id=self.source_event_id, quote=self.source_quote,
                         start=self.source_span.start, end=self.source_span.end)


class Authorization(StrictModel):
    authorization_id: Text
    source_event_id: Text
    source_quote: Text
    source_span: Span
    subjects: list[Subject] = Field(min_length=1)
    change_type: ChangeType
    change_details: ChangeDetails
    conditions: list[Condition]
    lifecycle: Lifecycle
    interpretation: Interpretation
    constraint_refs: list[Text]
    valid_from: DecisionPoint
    updated_by: list[Text]
    supersedes: list[Text]

    @property
    def source_ref(self) -> SourceRef:
        return SourceRef(event_id=self.source_event_id, quote=self.source_quote,
                         start=self.source_span.start, end=self.source_span.end)


class ExceptionEntry(StrictModel):
    exception_id: Text
    constraint_ids: list[Text] = Field(min_length=1)
    authorization_id: Text
    subjects: list[Subject] = Field(min_length=1)
    change_types: list[ChangeType] = Field(min_length=1)
    conditions: list[Condition]
    valid_from: DecisionPoint


class AuthorizationState(StrictModel):
    schema_version: Literal["0.1-draft"]
    case_id: Text
    state_id: Text
    through_event_id: str
    through_seq: int = Field(ge=0)
    source_history_sha256: SHA
    constraints: list[Constraint]
    authorizations: list[Authorization]
    exceptions: list[ExceptionEntry]
    task_context: list[dict[str, JsonValue]]
    transition_refs: list[Text]
    state_hash: SHA


class StateTransition(StrictModel):
    transition_id: Text
    source_event_id: Text
    source_seq: int = Field(ge=1)
    operation: Operation
    target_ids: list[Text]
    scope: list[Subject]
    before_hash: SHA
    after_hash: SHA
    accepted: bool
    rejection_reason: str | None
    extraction_call_id: str | None


class TransitionLogEntry(StrictModel):
    schema_version: Literal["0.1-draft"]
    proposal: TransitionProposal
    transition: StateTransition
    consumed_event: bool
    update_elapsed_ms: float = Field(ge=0, allow_inf_nan=False)


class ImpactCoverage(StrictModel):
    impact_id: Text
    verdict: Verdict
    authorization_refs: list[Text]
    constraint_refs: list[Text]
    source_refs: list[SourceRef]
    reason: Text


class ScopeAssessment(StrictModel):
    schema_version: Literal["0.1-draft"]
    case_id: Text
    state_hash: SHA
    evidence_id: Text
    impact: Literal["compatible", "breaking", "unknown"]
    authorization: Literal["covered", "not_covered", "ambiguous", "not_needed"]
    decision: Literal["allow", "revise", "ask", "investigate"]
    per_impact: list[ImpactCoverage]
    interpretation_limit: Literal["structural_scope_only_extraction_semantics_unverified"]


def _resolved_source(source: SourceQuote, event: ConversationEvent, *, mutation: bool) -> SourceRef:
    if source.event_id != event.event_id:
        raise ValueError("source outside current event")
    start = event.content.find(source.quote)
    if start < 0:
        raise ValueError("source quote mismatch")
    if event.content.find(source.quote, start + 1) >= 0:
        raise ValueError("source quote is not unique within event")
    ref = SourceRef(event_id=source.event_id, quote=source.quote,
                    start=start, end=start + len(source.quote))
    validate_source(ref, [event], authorization=mutation)
    return ref


def _new_constraint(draft: ConstraintDraft, event: ConversationEvent, ref: SourceRef) -> Constraint:
    return Constraint(constraint_id=draft.constraint_id, source_event_id=event.event_id,
                      source_quote=ref.quote, source_span=Span(start=ref.start, end=ref.end),
                      subjects=draft.subjects, predicate=draft.predicate, lifecycle="active",
                      interpretation=draft.interpretation,
                      valid_from=DecisionPoint(event_id=event.event_id, seq=event.seq),
                      updated_by=[], supersedes=draft.supersedes)


def _new_authorization(draft: AuthorizationDraft, event: ConversationEvent,
                       ref: SourceRef) -> Authorization:
    return Authorization(authorization_id=draft.authorization_id, source_event_id=event.event_id,
                         source_quote=ref.quote, source_span=Span(start=ref.start, end=ref.end),
                         subjects=draft.subjects, change_type=draft.change_type,
                         change_details=draft.change_details, conditions=draft.conditions,
                         lifecycle="active", interpretation=draft.interpretation,
                         constraint_refs=draft.constraint_refs,
                         valid_from=DecisionPoint(event_id=event.event_id, seq=event.seq),
                         updated_by=[], supersedes=draft.supersedes)


def _exception_for(auth: Authorization) -> ExceptionEntry | None:
    if not auth.constraint_refs:
        return None
    return ExceptionEntry(exception_id="exception_" + auth.authorization_id,
                          constraint_ids=auth.constraint_refs, authorization_id=auth.authorization_id,
                          subjects=auth.subjects, change_types=[auth.change_type],
                          conditions=auth.conditions, valid_from=auth.valid_from)


def _same_subjects(left: list[Subject], right: list[Subject]) -> bool:
    return ({canonical_hash(subject.model_dump()) for subject in left} ==
            {canonical_hash(subject.model_dump()) for subject in right})


class StateEngine:
    """One transition per visible event; each rejected attempt remains in the log."""

    def __init__(self, case_id: str, events: list[ConversationEvent], extractor_config_hash: str,
                 artifact_ids: set[str] | None = None):
        if not case_id or [event.seq for event in events] != list(range(1, len(events) + 1)):
            raise ValueError("invalid case ID or non-contiguous event history")
        if len({event.event_id for event in events}) != len(events):
            raise ValueError("duplicate event ID")
        if len(extractor_config_hash) != 64 or any(c not in "0123456789abcdef" for c in extractor_config_hash):
            raise ValueError("invalid extractor config hash")
        self.case_id, self.events, self.extractor_config_hash = case_id, list(events), extractor_config_hash
        self.artifact_ids = set(artifact_ids) if artifact_ids is not None else None
        self.constraints: dict[str, Constraint] = {}
        self.authorizations: dict[str, Authorization] = {}
        self.exceptions: dict[str, ExceptionEntry] = {}
        self.through_seq = 0
        self.transition_refs: list[str] = []
        self.records: list[TransitionLogEntry] = []
        self.seen_keys: set[tuple[str, str, str]] = set()

    def snapshot(self) -> AuthorizationState:
        through = self.through_seq
        body = {
            "schema_version": "0.1-draft", "case_id": self.case_id,
            "through_event_id": self.events[through - 1].event_id if through else "",
            "through_seq": through,
            "source_history_sha256": canonical_hash([event.model_dump() for event in self.events[:through]]),
            "constraints": [self.constraints[key].model_dump() for key in sorted(self.constraints)],
            "authorizations": [self.authorizations[key].model_dump() for key in sorted(self.authorizations)],
            "exceptions": [self.exceptions[key].model_dump() for key in sorted(self.exceptions)],
            "task_context": [],
            "transition_refs": list(self.transition_refs),
        }
        state_hash = canonical_hash(body)
        return AuthorizationState(state_id="state_" + state_hash[:16], state_hash=state_hash, **body)

    def _apply(self, proposal: TransitionProposal, event: ConversationEvent, ref: SourceRef,
               constraints: dict[str, Constraint], authorizations: dict[str, Authorization],
               exceptions: dict[str, ExceptionEntry]):
        operation = proposal.operation
        if operation == "no_change":
            return
        if operation in {"add_constraint", "supersede"} and proposal.constraint is not None:
            draft = proposal.constraint
            if draft.constraint_id in constraints or draft.constraint_id in authorizations:
                raise ValueError("duplicate state item ID")
            contract_ref = draft.predicate.qualifiers.get("contract_ref")
            if (self.artifact_ids is not None and contract_ref is not None and
                    (not isinstance(contract_ref, str) or contract_ref not in self.artifact_ids)):
                raise ValueError("unknown public contract reference")
            for target in proposal.target_ids:
                old = constraints.get(target)
                if old is None or old.lifecycle != "active":
                    raise ValueError("constraint supersede target is missing or inactive")
                if not _same_subjects(old.subjects, draft.subjects):
                    raise ValueError("partial scope cannot supersede a broad constraint")
                constraints[target] = old.model_copy(update={"lifecycle": "superseded",
                                                      "updated_by": [*old.updated_by, event.event_id]})
            constraints[draft.constraint_id] = _new_constraint(draft, event, ref)
            return
        if operation in {"grant", "supersede"} and proposal.authorization is not None:
            draft = proposal.authorization
            if draft.authorization_id in constraints or draft.authorization_id in authorizations:
                raise ValueError("duplicate state item ID")
            if any(target not in constraints for target in draft.constraint_refs):
                raise ValueError("unknown constraint reference")
            if (self.artifact_ids is not None and any(
                    ref not in self.artifact_ids for condition in draft.conditions
                    for ref in condition.evidence_refs)):
                raise ValueError("unknown public condition evidence reference")
            for target in proposal.target_ids:
                old = authorizations.get(target)
                if old is None or old.lifecycle != "active":
                    raise ValueError("authorization supersede target is missing or inactive")
                if not _same_subjects(old.subjects, draft.subjects):
                    raise ValueError("authorization supersede scope mismatch")
                authorizations[target] = old.model_copy(update={"lifecycle": "superseded",
                                                         "updated_by": [*old.updated_by, event.event_id]})
            auth = _new_authorization(draft, event, ref)
            authorizations[draft.authorization_id] = auth
            exception = _exception_for(auth)
            if exception is not None:
                exceptions[exception.exception_id] = exception
            return
        if operation in {"revoke", "mark_ambiguous"}:
            for target in proposal.target_ids:
                old = authorizations.get(target)
                if old is None or old.lifecycle != "active":
                    raise ValueError("authorization target is missing or inactive")
                updates = {"updated_by": [*old.updated_by, event.event_id]}
                updates["lifecycle" if operation == "revoke" else "interpretation"] = (
                    "revoked" if operation == "revoke" else "ambiguous")
                authorizations[target] = old.model_copy(update=updates)
            return
        raise ValueError("unsupported state operation")

    def submit(self, proposal: TransitionProposal) -> TransitionLogEntry:
        started = time.perf_counter()
        before = self.snapshot()
        proposal_hash = canonical_hash(proposal.model_dump())
        attempt = len(self.records) + 1
        transition_id = "transition_" + canonical_hash({
            "case_id": self.case_id, "attempt": attempt, "before_hash": before.state_hash,
            "proposal_hash": proposal_hash,
        })[:20]
        key = (proposal.case_id, proposal.source_event_id, proposal.extractor_config_hash)
        consumed = False
        accepted = False
        error = None
        scope: list[Subject] = []
        if key in self.seen_keys:
            error = "duplicate event/config proposal"
        elif proposal.case_id != self.case_id or proposal.extractor_config_hash != self.extractor_config_hash:
            error = "case or extractor config mismatch"
        elif proposal.source_seq != self.through_seq + 1 or proposal.source_seq > len(self.events):
            error = "proposal event is out of order or outside visible history"
        elif proposal.source_event_id != self.events[proposal.source_seq - 1].event_id:
            error = "proposal event ID disagrees with trusted history"
        else:
            event = self.events[proposal.source_seq - 1]
            consumed = True
            self.seen_keys.add(key)
            try:
                ref = _resolved_source(proposal.source, event,
                                       mutation=proposal.operation != "no_change")
                constraints = dict(self.constraints)
                authorizations = dict(self.authorizations)
                exceptions = dict(self.exceptions)
                self._apply(proposal, event, ref, constraints, authorizations, exceptions)
                self.constraints, self.authorizations, self.exceptions = constraints, authorizations, exceptions
                accepted = True
                draft = proposal.constraint or proposal.authorization
                if draft is not None:
                    scope = list(draft.subjects)
                elif proposal.operation in {"revoke", "mark_ambiguous"}:
                    subjects = {canonical_hash(subject.model_dump()): subject
                                for target in proposal.target_ids
                                for subject in self.authorizations[target].subjects}
                    scope = [subjects[key] for key in sorted(subjects)]
            except ValueError as exc:
                error = str(exc)
            self.through_seq = event.seq
            self.transition_refs.append(transition_id)
        after = self.snapshot()
        transition = StateTransition(
            transition_id=transition_id, source_event_id=proposal.source_event_id,
            source_seq=proposal.source_seq, operation=proposal.operation,
            target_ids=proposal.target_ids, scope=scope, before_hash=before.state_hash,
            after_hash=after.state_hash, accepted=accepted, rejection_reason=error,
            extraction_call_id=proposal.extraction_call_id,
        )
        entry = TransitionLogEntry(schema_version="0.1-draft", proposal=proposal,
                                   transition=transition, consumed_event=consumed,
                                   update_elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
        self.records.append(entry)
        return entry


def replay_entries(case_id: str, events: list[ConversationEvent], extractor_config_hash: str,
                   entries: list[TransitionLogEntry], artifact_ids: set[str] | None = None) -> AuthorizationState:
    engine = StateEngine(case_id, events, extractor_config_hash, artifact_ids)
    for saved in entries:
        generated = engine.submit(saved.proposal)
        if (generated.transition != saved.transition or
                generated.consumed_event != saved.consumed_event):
            raise ValueError("transition replay mismatch")
    return engine.snapshot()


def verify_state_hash(state: AuthorizationState):
    body = state.model_dump(exclude={"state_id", "state_hash"})
    expected = canonical_hash(body)
    if state.state_hash != expected or state.state_id != "state_" + expected[:16]:
        raise ValueError("authorization state hash mismatch")


def _effect_fields(impact: Impact) -> tuple[str, str | None] | None:
    before, after = impact.before, impact.after
    old = impact.subject.field
    if not isinstance(before, dict) or not isinstance(after, dict) or old not in before:
        return None
    added = set(after) - set(before)
    if impact.change_type == "replace_field":
        if old in after or len(added) != 1:
            return None
        return old, next(iter(added))
    if impact.change_type == "add_alias":
        if old not in after or len(added) != 1:
            return None
        return old, next(iter(added))
    if impact.change_type == "remove_field":
        return (old, None) if old not in after and not added else None
    if impact.change_type == "change_value_semantics":
        return (old, None) if old in after else None
    return None


def _affected_constraints(state: AuthorizationState, impact: Impact) -> list[Constraint]:
    kinds = ({"field_exists", "response_shape"} if impact.change_type != "change_value_semantics"
             else {"value_semantics"})
    return [item for item in state.constraints if item.lifecycle == "active" and
            impact.subject in item.subjects and item.predicate.kind in kinds]


def _condition_verdict(conditions: list[Condition], candidate_sha256: str) -> Verdict:
    unknown = False
    for condition in conditions:
        if condition.kind == "candidate_sha256":
            computed = "satisfied" if condition.value == candidate_sha256 else "unsatisfied"
            if computed == "unsatisfied":
                return "not_covered"
            if condition.resolution not in {"unknown", computed}:
                unknown = True  # Never accept a contradictory extractor claim.
        elif condition.resolution == "unsatisfied":
            return "not_covered"
        else:
            unknown = True  # Other conditions have no independent verifier in M3a.
    return "ambiguous" if unknown else "covered"


def _dedupe_refs(refs: list[SourceRef]) -> list[SourceRef]:
    unique = {}
    for ref in refs:
        unique[(ref.event_id, ref.start, ref.end, ref.quote)] = ref
    return list(unique.values())


def assess_impacts(state: AuthorizationState, evidence: ImpactEvidence,
                   candidate_sha256: str) -> ScopeAssessment:
    """Deterministic structural coverage; input extraction quality remains unverified."""
    verify_state_hash(state)
    if evidence.patch_sha256 != candidate_sha256:
        raise ValueError("impact and candidate patch hash mismatch")
    rows = []
    for impact in evidence.impacts:
        constraints = _affected_constraints(state, impact)
        needed = {item.constraint_id for item in constraints}
        uncertain_constraint = any(item.interpretation == "ambiguous" for item in constraints)
        base_refs = [item.source_ref for item in constraints]
        effect = _effect_fields(impact)
        covered = None
        ambiguous = None
        for auth in state.authorizations:
            if auth.lifecycle != "active" or impact.subject not in auth.subjects or auth.change_type != impact.change_type:
                continue
            if effect is None:
                ambiguous = auth
                continue
            old, new = effect
            if auth.change_details.old_field != old or auth.change_details.new_field != new:
                continue
            if (impact.change_type == "change_value_semantics" and
                    auth.change_details.preserve_value_semantics):
                continue
            if not needed.issubset(auth.constraint_refs):
                continue
            if needed and not any(
                exception.authorization_id == auth.authorization_id and
                needed.issubset(exception.constraint_ids) and
                impact.subject in exception.subjects and impact.change_type in exception.change_types
                for exception in state.exceptions
            ):
                continue
            if (auth.change_details.preserve_value_semantics and new is not None and
                    isinstance(impact.before, dict) and isinstance(impact.after, dict) and
                    impact.before[old] != impact.after[new]):
                continue
            conditions = _condition_verdict(auth.conditions, candidate_sha256)
            if conditions == "not_covered":
                continue
            if auth.interpretation == "ambiguous" or conditions == "ambiguous":
                ambiguous = auth
            else:
                covered = auth
                break
        selected = covered or ambiguous
        verdict: Verdict = ("ambiguous" if covered and uncertain_constraint else
                            "covered" if covered else "ambiguous" if ambiguous else "not_covered")
        refs = _dedupe_refs(base_refs + ([selected.source_ref] if selected else []))
        rows.append(ImpactCoverage(
            impact_id=impact.impact_id, verdict=verdict,
            authorization_refs=[selected.authorization_id] if selected else [],
            constraint_refs=sorted(needed), source_refs=refs,
            reason=("constraint_interpretation_ambiguous" if covered and uncertain_constraint else
                    "exact_active_grant_and_conditions" if covered else
                    "grant_or_condition_ambiguous" if ambiguous else
                    "no_active_grant_covers_scope_operation_constraints_and_conditions"),
        ))
    if evidence.assessment == "compatible":
        authorization, decision = "not_needed", "allow"
    elif evidence.assessment == "unknown":
        authorization, decision = ("ambiguous" if rows else "not_needed"), "investigate"
    else:
        if not rows:
            raise ValueError("breaking impact evidence has no impact rows")
        verdicts = {row.verdict for row in rows}
        authorization = "not_covered" if "not_covered" in verdicts else "ambiguous" if "ambiguous" in verdicts else "covered"
        decision = {"not_covered": "revise", "ambiguous": "ask", "covered": "allow"}[authorization]
    return ScopeAssessment(schema_version="0.1-draft", case_id=state.case_id,
                           state_hash=state.state_hash, evidence_id=evidence.evidence_id,
                           impact=evidence.assessment, authorization=authorization,
                           decision=decision, per_impact=rows,
                           interpretation_limit="structural_scope_only_extraction_semantics_unverified")
