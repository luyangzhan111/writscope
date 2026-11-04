"""Compact model contract, explicit deterministic projection to frozen kernels.

No history search, scope completion, output repair, or historical regrading.
The model chooses one complete API source; all redundant fields are compiled.
"""
from copy import deepcopy
import json
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import Discriminator, Field, Tag, TypeAdapter, model_validator

from scripts import s4_grounded_meaning_v1 as old
from scripts import s4_effect_domain_v1 as domain
from scripts import s4_constraint_representation_v1 as closed
from scripts import s4_prompt_candidate as preview_base
from scripts.s3_interaction_v2 import NewConstraint, ReplacementConstraint
from writscope.artifacts import canonical_hash, digest
from writscope.contracts import Checklist, QuoteDerivedItem, SourceQuote, Subject, parse_json
from writscope.events import StrictModel, ConversationEvent

VERSION = 's4_compact_API_compiler_v2'
STAGES = old.STAGES
PROMPTS = Path(__file__).resolve().parents[1]/'prompts/s4_compiled_v2'


class MeaningQualifier(StrictModel):
    meaning_quote: str = Field(min_length=1)


class ValueSemantics(StrictModel):
    kind: Literal['value_semantics']
    expected: old.MeaningText
    qualifiers: MeaningQualifier

    @model_validator(mode='after')
    def literal_meaning(self):
        text, quote = self.expected.source_text, self.qualifiers.meaning_quote
        start = quote.find(text)
        if (not text.strip() or start < 0 or quote.find(text, start+1) >= 0 or not old.MEANING.search(quote)):
            raise ValueError('meaning_requires_unique_literal_and_meaning_cue')
        return self


Predicate = Annotated[Union[closed.FieldExists, closed.ResponseShape, ValueSemantics], Field(discriminator='kind')]


def expanded_predicate(predicate, source):
    data = predicate.model_dump(mode='json')
    if isinstance(predicate, ValueSemantics):
        data['qualifiers'] = {'scope_source': source.model_dump(mode='json'),
            'meaning_source': {'event_id': source.event_id, 'quote': predicate.qualifiers.meaning_quote}}
    return old.PREDICATE.validate_python(data)


def structural_source(predicate, subjects, source):
    # No borrowed API scope: the sole source must contain path, field and duty.
    return old.constraint_evidence(source.quote, subjects, expanded_predicate(predicate, source))


class TypedNewConstraint(NewConstraint):
    predicate: Predicate


class TypedReplacementConstraint(ReplacementConstraint):
    predicate: Predicate


class AddConstraint(domain.previous.AddConstraint):
    constraint: TypedNewConstraint

    @model_validator(mode='after')
    def complete_API_source(self):
        structural_source(self.constraint.predicate, self.constraint.subjects,
                          SourceQuote(event_id='executor_bound_current_event', quote=self.source_quote))
        return self


class ReplaceConstraint(domain.previous.ReplaceConstraint):
    constraint: TypedReplacementConstraint

    @model_validator(mode='after')
    def complete_API_source(self):
        return AddConstraint.complete_API_source(self)


def operation_tag(value):
    op = value.get('operation') if isinstance(value, dict) else getattr(value, 'operation', None)
    if op == 'supersede':
        constraint = value.get('constraint') if isinstance(value, dict) else value.constraint
        return 'supersede_constraint' if constraint is not None else 'supersede_authorization'
    return op


Operation = Annotated[Union[
    Annotated[AddConstraint, Tag('add_constraint')],
    Annotated[ReplaceConstraint, Tag('supersede_constraint')],
    Annotated[domain.Grant, Tag('grant')],
    Annotated[domain.ReplaceAuthorization, Tag('supersede_authorization')],
    Annotated[domain.previous.Revoke, Tag('revoke')],
    Annotated[domain.previous.MarkAmbiguous, Tag('mark_ambiguous')],
    Annotated[domain.RecordContext, Tag('record_context')],
    Annotated[old.NoChange, Tag('no_change')]], Discriminator(operation_tag)]


class Batch(StrictModel):
    kind: Literal['batch']
    items: list[Operation] = Field(min_length=1, max_length=8)

    @model_validator(mode='after')
    def no_change_is_sole_item(self):
        if any(isinstance(i, old.NoChange) for i in self.items) and len(self.items) != 1:
            raise ValueError('no_change_must_be_the_only_item')
        return self


class ConstraintItem(StrictModel):
    item_id: str = Field(min_length=1)
    kind: Literal['constraint']
    subjects: list[Subject] = Field(min_length=1)
    predicate: Predicate
    lifecycle: Literal['active', 'superseded', 'revoked']
    interpretation: Literal['resolved', 'ambiguous']
    source: SourceQuote

    @model_validator(mode='after')
    def complete_API_source(self):
        structural_source(self.predicate, self.subjects, self.source)
        return self


class AuthorizationItem(QuoteDerivedItem):
    kind: Literal['authorization']


DerivedItem = Annotated[Union[ConstraintItem, AuthorizationItem], Field(discriminator='kind')]


class Payload(domain.ScopePayload):
    derived_items: list[DerivedItem]


class ChecklistPayload(Payload):
    checklist: Checklist


class Final(StrictModel):
    kind: Literal['final']
    payload: Payload
    task_context: list[domain.FinalContext] = Field(max_length=8)

    @model_validator(mode='after')
    def unique_items(self):
        ids = [i.item_id for i in self.payload.derived_items]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate_derived_item_id')
        return self


class ChecklistFinal(Final):
    payload: ChecklistPayload


def adapter(stage, allow_lookup):
    if stage not in STAGES or type(allow_lookup) is not bool:
        raise ValueError('invalid_compact_contract_stage')
    primary = Batch if stage == 'incremental_extract' else ChecklistFinal if stage == 'full_checklist' else Final
    return TypeAdapter(Annotated[Union[primary, preview_base.Lookup], Field(discriminator='kind')] if allow_lookup else primary)


def prompt(stage):
    if stage not in STAGES:
        raise ValueError('invalid_compact_contract_stage')
    # Shared research rules plus one current contract: no cumulative obsolete
    # instructions requiring the model to emit compiler-owned fields.
    common = (preview_base.PROMPT_DIR/'common.md').read_text(encoding='utf8').strip()
    return '\n\n'.join([common, (PROMPTS/'common.md').read_text(encoding='utf8').strip(),
        (PROMPTS/(stage+'.md')).read_text(encoding='utf8').strip()])+'\n'


def preview(*args, **kwargs):
    value = preview_base.preview(*args, **kwargs)
    value.update(instructions=prompt(value['stage']), prompt_sha256=digest(prompt(value['stage']).encode()),
        output_schema=adapter(value['stage'], value['lookup_allowed']).json_schema())
    return value


def events_in(request):
    return [ConversationEvent.model_validate(e) for e in request['public_input']['review_input']['events']]


def projection(payload, request):
    """Validate wire shape then derive declared fields; never mutate payload.

    Caller persists actual response bytes. This returns logical hashes only;
    independent R4 invokes the same pure projection on those recorded bytes.
    """
    model = adapter(request['stage'], request['lookup_allowed']).validate_python(payload)
    compiled = deepcopy(payload)
    event_list = events_in(request)
    if payload['kind'] == 'batch':
        for item, target in zip(model.items, compiled['items'], strict=True):
            if item.constraint is not None:
                source = SourceQuote(event_id=event_list[-1].event_id, quote=item.source_quote)
                target['constraint']['predicate'] = expanded_predicate(item.constraint.predicate, source).model_dump(mode='json')
        old.Batch.model_validate(compiled)
    elif payload['kind'] == 'final':
        compiled['api_constraints'] = []
        for item, target in zip(model.payload.derived_items, compiled['payload']['derived_items'], strict=True):
            if not isinstance(item, ConstraintItem):
                continue
            p = expanded_predicate(item.predicate, item.source)
            refs = [item.source.model_dump(mode='json')]
            if isinstance(p, old.ValueSemantics):
                meaning = p.qualifiers.meaning_source.model_dump(mode='json')
                if meaning not in refs:
                    refs.append(meaning)
            # Coverage is a published common computation, not an answer picked
            # by the model or a patch-conditioned subset. Conditions on grants
            # remain model declarations checked by the unchanged scope kernel.
            target.clear()
            target.update(item_id=item.item_id, kind='constraint', subjects=[s.model_dump(mode='json') for s in item.subjects],
                predicate=json.dumps(old.normative(p), ensure_ascii=False, sort_keys=True, separators=(',', ':')),
                change_types=old.coverage(p), conditions=[], lifecycle=item.lifecycle,
                interpretation=item.interpretation, source_refs=refs)
            compiled['api_constraints'].append({'item_id':item.item_id, 'effect_domain':'server_api',
                'predicate':p.model_dump(mode='json')})
        (old.ChecklistFinal if request['stage']=='full_checklist' else old.Final).model_validate(compiled)
    proof = old.check_response_sources(compiled, request)
    return compiled, {'policy':VERSION, 'logical_input_sha256':canonical_hash(payload),
        'compiled_payload_sha256':canonical_hash(compiled), 'source_proofs':proof,
        'actual_response_bytes_source':'immutable_transport_journal',
        'scope_inferred_from_history':False, 'general_semantic_proof':False}


class DomainEngine(old.DomainEngine):
    def snapshot(self):
        value = super().snapshot()
        return {**value, 'compact_compiler_policy':VERSION,
            'qualified_state_sha256':canonical_hash({'prior':value, 'compiler':VERSION})}

    def submit(self, raw, index, call_id):
        if (type(index) is not int or not 0 <= index < len(self.bundle.review_input.events) or
                index != self.kernel.engine.snapshot().through_seq):
            raise ValueError('compiler_current_event_cursor_mismatch')
        request = {'stage':'incremental_extract', 'lookup_allowed':False, 'public_input':{
            'review_input':{'events':[e.model_dump(mode='json') for e in self.bundle.review_input.events[:index+1]]},
            'artifact_registry':self.bundle.registry.model_dump(mode='json')}}
        compiled, proof = projection({'kind':'batch', **parse_json(raw)}, request)
        row = super().submit(json.dumps({'items':compiled['items']},ensure_ascii=False), index, call_id)
        row['compiler_projection']['compact_contract'] = proof
        return row


