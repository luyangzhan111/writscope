"""Scoped meaning quotations and compiler-bound no_change, offline revision.

Grounding is structural, never proof of extraction or translation correctness.
The frozen state/scope/exception kernels and old responses are not rewritten.
"""
import json
from pathlib import Path
import re
from typing import Annotated, Literal, Union
from unittest.mock import patch

from pydantic import Field, TypeAdapter, model_validator

from scripts import s4_constraint_representation_v1 as previous
from scripts.s3_interaction_v2 import NewConstraint, ReplacementConstraint
from writscope import state as legacy_state
from writscope.artifacts import canonical_hash, digest
from writscope.contracts import SourceQuote, _resolve_quote, parse_json
from writscope.events import StrictModel, ConversationEvent

VERSION = 's4_scoped_meaning_current_event_v1'
STAGES = previous.STAGES
PROMPTS = Path(__file__).resolve().parents[1]/'prompts/s4_meaning_event_v1'
MEANING = re.compile(r'语义|含义|表示|\bmeaning\b|\bsemantic\w*\b', re.I)


class MeaningText(StrictModel):
    source_text: str = Field(min_length=1)
    explanation: str | None


class MeaningSources(StrictModel):
    scope_source: SourceQuote
    meaning_source: SourceQuote


class ValueSemantics(StrictModel):
    kind: Literal['value_semantics']
    expected: MeaningText
    qualifiers: MeaningSources

    @model_validator(mode='after')
    def contained_literal(self):
        scope, meaning = self.qualifiers.scope_source, self.qualifiers.meaning_source
        text = self.expected.source_text
        if scope.event_id != meaning.event_id or meaning.quote not in scope.quote:
            raise ValueError('meaning_must_be_contained_in_same_event_API_anchor')
        start = meaning.quote.find(text)
        if start < 0 or meaning.quote.find(text, start+1) >= 0 or not text.strip() or not MEANING.search(meaning.quote):
            raise ValueError('meaning_source_requires_unique_verbatim_source_text_and_meaning_cue')
        return self


Predicate = Annotated[Union[previous.FieldExists, previous.ResponseShape, ValueSemantics], Field(discriminator='kind')]
PREDICATE = TypeAdapter(Predicate)


def predicate(value):
    return PREDICATE.validate_python(value) if isinstance(value, dict) else value


def normative(value):
    p = predicate(value)
    data = p.model_dump(mode='json')
    if isinstance(p, ValueSemantics):
        # Advisory wording stays in raw/state hashes but cannot affect matching.
        del data['expected']['explanation']
    return data


def coverage(value):
    p = predicate(value)
    return ['change_value_semantics'] if isinstance(p, ValueSemantics) else previous.coverage(p)


def constraint_evidence(quote, subjects, p):
    if isinstance(p, ValueSemantics):
        if quote != p.qualifiers.scope_source.quote:
            raise ValueError('meaning_constraint_requires_its_declared_API_anchor')
        literal = previous.ValueSemantics(kind='value_semantics', expected=p.expected.source_text,
                                          qualifiers=previous.EmptyQualifiers())
        previous.constraint_evidence(quote, subjects, literal)
        return {'policy': VERSION, 'general_semantic_proof': False, 'coverage': coverage(p)}
    return previous.constraint_evidence(quote, subjects, p)


def ground_meaning(p, subjects, events, *, current_event=None):
    if not isinstance(p, ValueSemantics):
        return None
    scope, meaning = p.qualifiers.scope_source, p.qualifiers.meaning_source
    if current_event is not None and scope.event_id != current_event.event_id:
        raise ValueError('meaning_mutation_must_use_current_user_event')
    outer = _resolve_quote(scope, events, authorization=True)
    inner = _resolve_quote(meaning, events, authorization=True)
    if not outer['start'] <= inner['start'] < inner['end'] <= outer['end']:
        raise ValueError('meaning_span_outside_declared_API_anchor')
    constraint_evidence(scope.quote, subjects, p)
    start = inner['start'] + meaning.quote.index(p.expected.source_text)
    return {'policy': VERSION, 'scope_source': outer, 'meaning_source': inner,
            'source_text_span': {'event_id': scope.event_id, 'start': start,
                                 'end': start+len(p.expected.source_text)},
            'source_text_sha256': digest(p.expected.source_text.encode()),
            'explanation_status': 'advisory_unverified_not_authority_or_matching_input',
            'general_semantic_proof': False}


class TypedNewConstraint(NewConstraint):
    predicate: Predicate


class TypedReplacementConstraint(ReplacementConstraint):
    predicate: Predicate


class AddConstraint(previous.AddConstraint):
    constraint: TypedNewConstraint

    @model_validator(mode='after')
    def API_source(self):
        constraint_evidence(self.source_quote, self.constraint.subjects, self.constraint.predicate)
        return self


class ReplaceConstraint(previous.ReplaceConstraint):
    constraint: TypedReplacementConstraint

    @model_validator(mode='after')
    def API_source(self):
        return AddConstraint.API_source(self)


class NoChange(StrictModel):
    operation: Literal['no_change']
    target_ids: list[str] = Field(max_length=0)
    constraint: None
    authorization: None
    value_scope: None


Mutation = Union[AddConstraint, ReplaceConstraint, previous.previous.Grant, previous.previous.ReplaceAuthorization,
                 previous.previous.previous.Revoke, previous.previous.previous.MarkAmbiguous, previous.previous.RecordContext]


class Batch(StrictModel):
    kind: Literal['batch']
    items: Union[Annotated[list[NoChange], Field(min_length=1, max_length=1)],
                 Annotated[list[Mutation], Field(min_length=1, max_length=8)]]


class APIConstraint(StrictModel):
    item_id: str = Field(min_length=1)
    effect_domain: Literal['server_api']
    predicate: Predicate


class Final(previous.Final):
    api_constraints: list[APIConstraint]

    @model_validator(mode='after')
    def canonical_constraints(self):
        items = [i for i in self.payload.derived_items if i.kind == 'constraint']
        basis = {i.item_id: i for i in self.api_constraints}
        if len(basis) != len(self.api_constraints) or len({i.item_id for i in items}) != len(items) or set(basis) != {i.item_id for i in items}:
            raise ValueError('typed_API_constraints_must_match_all_derived_constraints_exactly')
        for item in items:
            p = basis[item.item_id].predicate
            if previous.previous.client_effect(item.predicate) or any(previous.previous.client_effect(r.quote) for r in item.source_refs):
                raise ValueError('client_task_quote_cannot_support_API_constraint')
            if len(item.change_types) != len(set(item.change_types)) or set(item.change_types) != set(coverage(p)):
                raise ValueError('constraint_operation_coverage_mismatch_no_silent_completion')
            refs = {(r.event_id, r.quote) for r in item.source_refs}
            if isinstance(p, ValueSemantics):
                required = {(r.event_id, r.quote) for r in (p.qualifiers.scope_source, p.qualifiers.meaning_source)}
                if not required <= refs:
                    raise ValueError('meaning_item_must_cite_its_own_scope_and_meaning_sources')
                constraint_evidence(p.qualifiers.scope_source.quote, item.subjects, p)
            else:
                checks = []
                for r in item.source_refs:
                    try:
                        checks.append(constraint_evidence(r.quote, item.subjects, p))
                    except ValueError:
                        pass
                if not checks:
                    raise ValueError('API_constraint_lacks_explicit_structured_source_support')
        return self


class ChecklistFinal(Final):
    payload: previous.previous.ChecklistPayload


def adapter(stage, allow_lookup):
    if stage not in STAGES or type(allow_lookup) is not bool:
        raise ValueError('invalid meaning/event stage')
    primary = Batch if stage == 'incremental_extract' else ChecklistFinal if stage == 'full_checklist' else Final
    lookup = previous.previous.previous.legacy.Lookup
    return TypeAdapter(Annotated[Union[primary, lookup], Field(discriminator='kind')] if allow_lookup else primary)


def prompt(stage):
    if stage not in STAGES:
        raise ValueError('invalid meaning/event stage')
    return previous.prompt(stage)+(PROMPTS/'common.md').read_text(encoding='utf8').strip()+'\n\n'+(
        PROMPTS/(stage+'.md')).read_text(encoding='utf8').strip()+'\n'


def preview(*args, **kwargs):
    result = previous.preview(*args, **kwargs)
    result.update(instructions=prompt(result['stage']), prompt_sha256=digest(prompt(result['stage']).encode()),
                  output_schema=adapter(result['stage'], result['lookup_allowed']).json_schema())
    return result


def event_binding(event):
    return {'policy': VERSION, 'event_id': event.event_id, 'seq': event.seq, 'role': event.role,
            'event_content_sha256': digest(event.content.encode()),
            'source': 'verified_executor_current_event_not_model_selected',
            'semantic_no_change_judgment': 'not_verified', 'creates_authority': False}


def batch_sources(draft, events):
    current = events[-1]
    proofs = []
    for item in draft.items:
        if isinstance(item, NoChange):
            continue
        _resolve_quote(SourceQuote(event_id=current.event_id, quote=item.source_quote), events, authorization=True)
        if item.constraint is not None:
            proof = ground_meaning(item.constraint.predicate, item.constraint.subjects, events, current_event=current)
            if proof is not None:
                proofs.append({'constraint_id': item.constraint.constraint_id, **proof})
    return proofs


def check_response_sources(payload, request):
    events = [ConversationEvent.model_validate(e) for e in request['public_input']['review_input']['events']]
    previous.previous.check_response_sources(payload, request)
    if payload['kind'] == 'batch':
        draft = Batch.model_validate(payload)
        return {'meaning_grounding': batch_sources(draft, events),
                'no_change_event_binding': event_binding(events[-1]) if isinstance(draft.items[0], NoChange) else None}
    proofs = []
    if payload['kind'] == 'final':
        final = Final.model_validate(payload) if request['stage'] != 'full_checklist' else ChecklistFinal.model_validate(payload)
        models = {i.item_id: i.predicate for i in final.api_constraints}
        for item in final.payload.derived_items:
            if item.kind == 'constraint':
                for ref in item.source_refs:
                    _resolve_quote(ref, events, authorization=True)
                proof = ground_meaning(models[item.item_id], item.subjects, events)
                if proof is not None:
                    proofs.append({'constraint_id': item.item_id, **proof})
    return {'meaning_grounding': proofs, 'no_change_event_binding': None}


def check_maintained_summary(payload, request):
    if request['stage'] != 'incremental_final' or payload['kind'] != 'final':
        return
    stored = {i['constraint_id']: i for i in request['public_input']['maintained_state']['state']['constraints']}
    items = {i['item_id']: i for i in payload['payload']['derived_items'] if i['kind'] == 'constraint'}
    models = {i['item_id']: i for i in payload['api_constraints']}
    active = {k for k, v in stored.items() if v['lifecycle'] == 'active'}
    if not active <= set(items) or not set(items) <= set(stored):
        raise ValueError('final_constraint_inventory_differs_from_maintained_state')
    for key, item in items.items():
        original = stored[key]
        if (normative(models[key]['predicate']) != normative(original['predicate']) or
                any(item[name] != original[name] for name in ('subjects', 'lifecycle', 'interpretation')) or
                (original['source_event_id'], original['source_quote']) not in
                    {(r['event_id'], r['quote']) for r in item['source_refs']}):
            raise ValueError('final_constraint_contract_differs_from_maintained_state')


def state_coverage(state):
    return {i['constraint_id']: {'coverage': coverage(i['predicate']), 'lifecycle': i['lifecycle'],
            'predicate_sha256': canonical_hash(normative(i['predicate']))} for i in state['constraints']}


def affected_constraints(state, impact):
    return [c for c in state.constraints if c.lifecycle == 'active' and impact.subject in c.subjects
            and impact.change_type in coverage(c.predicate.model_dump(mode='json'))]


class PriorState(previous.previous.PriorState):
    def model_dump(self, *args, **kwargs):
        result = super().model_dump(*args, **kwargs)
        return {**result, 'api_constraint_coverage': state_coverage(result)}


class StateView(previous.previous.StateView):
    def snapshot(self):
        return PriorState(self.owner.kernel.engine.snapshot(), self.owner.context.snapshot())


class DomainEngine(previous.previous.DomainEngine):
    def __init__(self, bundle, config_hash):
        super().__init__(bundle, config_hash)
        self.engine = StateView(self)

    def snapshot(self):
        original = super().snapshot()
        covered = state_coverage(original['state'])
        return {**original, 'constraint_representation_policy': VERSION, 'api_constraint_coverage': covered,
                'qualified_state_sha256': canonical_hash({'domain_state': original, 'coverage': covered, 'policy': VERSION})}

    def submit(self, raw, index, call_id):
        value = parse_json(raw)
        if set(value) != {'items'}:
            raise ValueError('meaning/event compiler expects only items')
        if type(index) is not int or not 0 <= index < len(self.bundle.review_input.events) or index != self.kernel.engine.snapshot().through_seq:
            raise ValueError('compiler_current_event_cursor_mismatch')
        draft = Batch.model_validate({'kind': 'batch', **value})
        proofs = batch_sources(draft, self.bundle.review_input.events[:index+1])
        compiled = {'items': [i.model_dump(mode='json') for i in draft.items]}
        binding = None
        if isinstance(draft.items[0], NoChange):
            event = self.bundle.review_input.events[index]
            binding = event_binding(event)
            compiled['items'][0]['source_quote'] = event.content
        result = super().submit(json.dumps(compiled, ensure_ascii=False), index, call_id)
        result['compiler_projection'].update(no_change_event_binding=binding,
            logical_input_sha256=digest(raw.encode()), compiled_input_sha256=canonical_hash(compiled),
            projection_policy=VERSION, actual_response_bytes_source='immutable_transport_journal')
        result.update(meaning_grounding=proofs, api_constraint_coverage=self.snapshot()['api_constraint_coverage'])
        return result

    def assess(self):
        with patch.object(legacy_state, '_affected_constraints', affected_constraints):
            result = super().assess()
        return {**result, 'constraint_coverage_policy': VERSION}
