"""Bounded client/server scope checks and a non-authoritative context channel.

This deliberately abstains outside explicit Chinese/English surface forms. It
is not a general natural-language permission prover. Old state kernels stay
unchanged and receive only validated API operations, never context permissions.
"""
from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Annotated, Literal, Union

from pydantic import Field, TypeAdapter, model_validator

from scripts import s4_output_contract_v2 as previous
from scripts.m3i_task_context import TaskContextLedger, ContextBatch, ContextQuote
from scripts.s4_p2_scope import QualifiedEngine as Kernel
from writscope.artifacts import canonical_hash, digest
from writscope.contracts import QuoteReviewPayload, Checklist, SourceQuote, parse_json, _resolve_quote
from writscope.events import StrictModel, ConversationEvent

VERSION = 's4_effect_domain_context_v1'
PROMPTS = Path(__file__).resolve().parents[1]/'prompts/s4_effect_domain_v1'
STAGES = previous.STAGES
CLIENT = re.compile(r'客户端|前端|页面|适配层|\bclient\b|client[- ]side|front[- ]?end|\bpage\b|\bUI\b', re.I)
LOCAL = re.compile(r'本地|映射|适配|转换|\blocal\b|\bmap(?:ping)?\b|\badapt(?:er|ation)?\b|\bconvert\b', re.I)
SERVER = re.compile(r'服务端|服务器|后端|接口|\bserver\b|back[- ]?end|\bAPI\b|/api/|\bHTTP\b', re.I)
PERMIT = re.compile(r'允许|同意|授权|可以|准许|\ballow(?:ed)?\b|\bpermit(?:ted)?\b|\bauthoriz\w*\b|\bmay\b|\bcan\b', re.I)
NEGATIVE = re.compile(r'不允许|不可以|不能|不得|禁止|不准|不要|不同意|不授权|未授权|未允许|未同意|\bno\b|\bnot\b|\bnever\b|\bcannot\b|\bcan.t\b|\bdon.t\b', re.I)
UNCERTAIN = re.compile(r'可能|也许|不确定|待定|视情况|是否|能否|\bmaybe\b|\bmight\b|\bperhaps\b|\bif\b|\bunless\b|[?？]', re.I)
ACTIONS = {
    'add_alias': r'新增|增加|添加|别名|\badd\b|\balias\b',
    'replace_field': r'替换|替代|改为|改成|更名|\breplace\b|\brename\b',
    'remove_field': r'删除|移除|去掉|\bremove\b|\bdrop\b|\bdelete\b',
    'change_value_semantics': r'语义|含义|值|\bmeaning\b|\bsemantic\w*\b|\bvalue\b',
}


def client_effect(quote):
    return bool(CLIENT.search(quote) and LOCAL.search(quote))


def server_evidence(quote, operation=None, fields=()):
    """Reject contradictory/mixed evidence; unknown is not permission.

    A positive result only checks this bounded scope surface. Source authority,
    exact quote, lifecycle, subject, conditions and semantic intent are separate.
    """
    if client_effect(quote):
        raise ValueError('client_or_mixed_effect_quote_cannot_support_server_authorization')
    action = ACTIONS.get(operation, '|'.join(ACTIONS.values()))
    identifiers = all(re.search(r'(?<![A-Za-z0-9_])'+re.escape(value)+r'(?![A-Za-z0-9_])', quote)
                      for value in fields if value)
    if (NEGATIVE.search(quote) or UNCERTAIN.search(quote) or not SERVER.search(quote) or not PERMIT.search(quote) or
            not re.search(action, quote, re.I) or not identifiers):
        raise ValueError('server_effect_evidence_unresolved_select_an_explicit_minimal_permission_quote')
    return {'policy': VERSION, 'scope_surface': 'explicit_server_form', 'general_semantic_proof': False}


class Grant(previous.Grant):
    effect_domain: Literal['server_api']

    @model_validator(mode='after')
    def scope_evidence(self):
        a = self.authorization
        server_evidence(self.source_quote, a.change_type, (a.change_details.old_field, a.change_details.new_field))
        return self


class ReplaceAuthorization(previous.ReplaceAuthorization):
    effect_domain: Literal['server_api']

    @model_validator(mode='after')
    def scope_evidence(self):
        return Grant.scope_evidence(self)


class RecordContext(StrictModel):
    operation: Literal['record_context']
    source_quote: str = Field(min_length=1)
    verification_refs: list[str]
    target_ids: list[str] = Field(max_length=0)
    constraint: None
    authorization: None
    value_scope: None

    @model_validator(mode='after')
    def not_server_permission(self):
        if SERVER.search(self.source_quote) and PERMIT.search(self.source_quote):
            raise ValueError('possible_server_authority_must_not_be_hidden_as_task_context_select_a_narrower_quote')
        return self


Mutation = Union[previous.AddConstraint, Grant, previous.ReplaceConstraint, ReplaceAuthorization,
                 previous.Revoke, previous.MarkAmbiguous, RecordContext]
NoChangeItems = Annotated[list[previous.NoChange], Field(min_length=1, max_length=1)]
MutationItems = Annotated[list[Mutation], Field(min_length=1, max_length=8)]


class Batch(StrictModel):
    kind: Literal['batch']
    items: Union[NoChangeItems, MutationItems]


def authorization_quotes(refs, operation=None, fields=()):
    # Constraint citations may accompany the authority quote. Any client-mapping
    # citation in a positive authority block is unresolved; choose narrower refs.
    if any(client_effect(r.quote) for r in refs):
        raise ValueError('client_mapping_reference_is_not_server_permission')
    for ref in refs:
        try:
            server_evidence(ref.quote, operation, fields)
            return
        except ValueError:
            pass
    raise ValueError('positive_server_authority_lacks_explicit_scope_evidence')


class ScopePayload(QuoteReviewPayload):
    @model_validator(mode='after')
    def authority_scope(self):
        for item in self.derived_items:
            if item.kind == 'authorization':
                authorization_quotes(item.source_refs)
        for item in self.per_impact_authorization:
            if item.verdict == 'covered':
                authorization_quotes(item.source_refs)
        if self.authorization == 'covered' and not any(x.verdict == 'covered' for x in self.per_impact_authorization):
            raise ValueError('covered_verdict_requires_per_impact_authority')
        return self


class ChecklistPayload(ScopePayload):
    checklist: Checklist


class FinalContext(StrictModel):
    source_ref: SourceQuote
    verification_refs: list[str]

    @model_validator(mode='after')
    def not_server_permission(self):
        if SERVER.search(self.source_ref.quote) and PERMIT.search(self.source_ref.quote):
            raise ValueError('possible_server_authority_must_not_be_hidden_as_task_context_select_a_narrower_quote')
        return self


class Final(StrictModel):
    kind: Literal['final']
    payload: ScopePayload
    task_context: list[FinalContext] = Field(max_length=8)


class ChecklistFinal(Final):
    payload: ChecklistPayload


def adapter(stage, allow_lookup):
    if stage not in STAGES or type(allow_lookup) is not bool:
        raise ValueError('invalid effect-domain stage')
    primary = Batch if stage == 'incremental_extract' else ChecklistFinal if stage == 'full_checklist' else Final
    return TypeAdapter(Annotated[Union[primary, previous.legacy.Lookup], Field(discriminator='kind')] if allow_lookup else primary)


def prompt(stage):
    if stage not in STAGES:
        raise ValueError('invalid effect-domain stage')
    return previous.prompt(stage)+(PROMPTS/'common.md').read_text(encoding='utf8').strip()+'\n\n'+(
        PROMPTS/(stage+'.md')).read_text(encoding='utf8').strip()+'\n'


def preview(*args, **kwargs):
    result = previous.preview(*args, **kwargs)
    instructions = prompt(result['stage'])
    result.update(instructions=instructions, prompt_sha256=digest(instructions.encode()),
                  output_schema=adapter(result['stage'], result['lookup_allowed']).json_schema())
    return result


def check_response_sources(payload, request):
    events = [ConversationEvent.model_validate(e) for e in request['public_input']['review_input']['events']]
    artifacts = set(request['public_input']['artifact_registry']['artifacts'])
    if payload['kind'] == 'batch':
        refs = [(SourceQuote(event_id=events[-1].event_id, quote=x['source_quote']), x.get('verification_refs', []))
                for x in payload['items'] if x['operation'] == 'record_context' or x.get('authorization') is not None]
    elif payload['kind'] == 'final':
        refs = [(SourceQuote.model_validate(x['source_ref']), x['verification_refs']) for x in payload['task_context']]
        refs += [(ref, []) for item in ScopePayload.model_validate(payload['payload']).derived_items
                 if item.kind == 'authorization' for ref in item.source_refs]
    else:
        refs = []
    for ref, evidence in refs:
        _resolve_quote(ref, events, authorization=True)
        if len(evidence) != len(set(evidence)) or not set(evidence) <= artifacts:
            raise ValueError('context_verification_refs_must_be_unique_public_artifact_ids')


class PriorState:
    """Public-input projection; never passed to the authorization kernel."""
    def __init__(self, state, context):
        self.state, self.context = state, context

    @property
    def through_seq(self):
        return self.state.through_seq

    def model_dump(self, *args, **kwargs):
        return {**self.state.model_dump(*args, **kwargs), 'non_authoritative_task_context': self.context.model_dump(mode='json')}


class StateView:
    def __init__(self, owner):
        self.owner = owner

    def snapshot(self):
        return PriorState(self.owner.kernel.engine.snapshot(), self.owner.context.snapshot())


class DomainEngine:
    """Atomic composition of the existing state kernel and quote-only ledger."""
    def __init__(self, bundle, config_hash):
        self.bundle, self.config_hash = bundle, config_hash
        self.kernel = Kernel(bundle, config_hash)
        self.context = TaskContextLedger(bundle.review_input.events, set(bundle.registry.artifacts))
        self.engine = StateView(self)
        self.records = []

    def snapshot(self):
        state = self.kernel.snapshot(); context = self.context.snapshot().model_dump(mode='json')
        return {**state, 'task_context': context,
                'qualified_state_sha256': canonical_hash({'authorization': state, 'non_authoritative_context': context})}

    def submit(self, raw, index, call_id):
        value = parse_json(raw)
        if set(value) != {'items'}:
            raise ValueError('scope compiler expects only items')
        draft = Batch.model_validate({'kind': 'batch', **value})
        event = self.bundle.review_input.events[index]
        context_items, authority_items, scope_checks = [], [], []
        for item in draft.items:
            if item.operation == 'record_context':
                context_items.append(ContextQuote(source_quote=item.source_quote, verification_refs=item.verification_refs))
            else:
                if item.authorization is not None:
                    _resolve_quote(SourceQuote(event_id=event.event_id, quote=item.source_quote),
                                   self.bundle.review_input.events[:index+1], authorization=True)
                    a = item.authorization
                    scope_checks.append(server_evidence(item.source_quote, a.change_type,
                                                        (a.change_details.old_field, a.change_details.new_field)))
                authority_items.append(item.model_dump(mode='json', exclude={'effect_domain'}))
        pending_context = deepcopy(self.context)
        context_record = pending_context.submit(ContextBatch(event_id=event.event_id, seq=event.seq, items=context_items))
        synthesized = not authority_items
        if synthesized:
            # Explicit compiler rule for a context-only event, never a repair of
            # an invalid grant. The original model response stays in the journal.
            authority_items = [{'operation': 'no_change', 'source_quote': event.content, 'target_ids': [],
                                'constraint': None, 'authorization': None, 'value_scope': None}]
        pending_kernel = deepcopy(self.kernel)
        result = pending_kernel.submit(json.dumps({'items': authority_items}, ensure_ascii=False), index, call_id)
        if result['batch']['accepted']:
            self.kernel, self.context = pending_kernel, pending_context
        else:
            # Preserve the unchanged kernel's rejection/consumed-event record;
            # the context additions are not committed.
            self.kernel = pending_kernel
        result.update(task_context_update=context_record.model_dump(mode='json') if result['batch']['accepted'] else None,
            task_context_committed=result['batch']['accepted'], scope_checks=scope_checks,
            compiler_projection={'context_items': len(context_items), 'context_only_no_authority_advance': synthesized},
            qualified_state_sha256=self.snapshot()['qualified_state_sha256'])
        self.records.append(result)
        return result

    def assess(self):
        return {**self.kernel.assess(), 'qualified_state_sha256': self.snapshot()['qualified_state_sha256']}
