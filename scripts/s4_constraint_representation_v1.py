"""Closed API predicates and one explicit four-operation coverage policy.

Bounded structural/source checks, not a general semantic extractor. Historical
schemas, kernel code, saved model responses and scores remain unchanged.
"""
import json
from pathlib import Path
import re
from typing import Annotated, Literal, Union
from unittest.mock import patch

from pydantic import Field, JsonValue, TypeAdapter, field_validator, model_validator

from scripts import s4_effect_domain_v1 as previous
from scripts.s3_interaction_v2 import NewConstraint, ReplacementConstraint
from writscope import state as legacy_state
from writscope.artifacts import canonical_hash, digest
from writscope.contracts import SourceQuote, _resolve_quote, parse_json
from writscope.events import StrictModel, ConversationEvent

VERSION = 's4_closed_api_constraint_coverage_v1'
STAGES = previous.STAGES
PROMPTS = Path(__file__).resolve().parents[1]/'prompts/s4_constraint_v1'
OPERATIONS = ('add_alias', 'remove_field', 'replace_field', 'change_value_semantics')
RESTRICT = re.compile(r'必须|只能|仅能|只含|要求|不得|应当|保持|\bmust\b|\bshall\b|\brequired?\b|\bonly\b|\bexact\w*\b|\bkeep\b|\bremain\b', re.I)
EXACT = re.compile(r'只能|仅(?:能)?|只(?:能)?(?:含|有)|\bexact\w*\b|\bonly\b|no\s+(?:extra|additional)|不得(?:增加|新增)', re.I)
TYPE_CUES = {'string': r'字符串|\bstring\b', 'integer': r'整数|\binteger\b',
    'number': r'数字|数值|\bnumber\b', 'boolean': r'布尔|\bboolean\b',
    'object': r'对象|\bobject\b', 'array': r'数组|\barray\b', 'null': r'\bnull\b'}


class EmptyQualifiers(StrictModel):
    pass


class FieldExists(StrictModel):
    kind: Literal['field_exists']
    expected: Literal[True]
    qualifiers: EmptyQualifiers

    @field_validator('expected', mode='before')
    @classmethod
    def literal_true(cls, value):
        if value is not True:
            raise ValueError('field_exists supports explicit true only')
        return value


class ShapeQualifiers(StrictModel):
    exact_keys: bool
    http_status: int | None = Field(ge=100, le=599)
    field_types: dict[str, Literal['string', 'integer', 'number', 'boolean', 'object', 'array', 'null']]


def literal_type(value, name):
    return {'string': type(value) is str, 'integer': type(value) is int,
        'number': type(value) in (int, float), 'boolean': type(value) is bool,
        'object': type(value) is dict, 'array': type(value) is list, 'null': value is None}[name]


class ResponseShape(StrictModel):
    kind: Literal['response_shape']
    expected: dict[str, JsonValue] = Field(min_length=1)
    qualifiers: ShapeQualifiers

    @model_validator(mode='after')
    def consistent_types(self):
        if not set(self.qualifiers.field_types) <= set(self.expected):
            raise ValueError('field type keys must be in the declared response shape')
        if any(not literal_type(self.expected[k], v) for k, v in self.qualifiers.field_types.items()):
            raise ValueError('declared field type contradicts expected literal')
        return self


class ValueSemantics(StrictModel):
    kind: Literal['value_semantics']
    expected: str = Field(min_length=1)
    qualifiers: EmptyQualifiers


Predicate = Annotated[Union[FieldExists, ResponseShape, ValueSemantics], Field(discriminator='kind')]
PREDICATE = TypeAdapter(Predicate)


def coverage(predicate):
    p = PREDICATE.validate_python(predicate) if isinstance(predicate, dict) else predicate
    if isinstance(p, FieldExists):
        return ['remove_field', 'replace_field']
    if isinstance(p, ValueSemantics):
        return ['change_value_semantics']
    if not isinstance(p, ResponseShape):
        raise ValueError('unsupported constraint predicate')
    return [op for op in OPERATIONS if op != 'add_alias' or p.qualifiers.exact_keys]


def token_in(needle, quote):
    return bool(re.search(r'(?<![A-Za-z0-9_])'+re.escape(needle)+r'(?![A-Za-z0-9_])', quote))


def constraint_evidence(quote, subjects, predicate):
    """Recognize explicit API obligation forms; abstain outside this subset."""
    if previous.client_effect(quote):
        raise ValueError('client_task_quote_cannot_support_API_constraint')
    if previous.PERMIT.search(quote) or previous.UNCERTAIN.search(quote):
        raise ValueError('permission_or_conditional_quote_is_not_an_unconditional_API_obligation')
    fields = [s.field for s in subjects]
    if (not previous.SERVER.search(quote) or not RESTRICT.search(quote) or
            not all(s.path in quote and token_in(s.field, quote) for s in subjects)):
        raise ValueError('API_constraint_source_scope_unresolved')
    if isinstance(predicate, ResponseShape):
        if not set(fields) <= set(predicate.expected):
            raise ValueError('shape must explicitly include every constrained subject field')
        if bool(EXACT.search(quote)) != predicate.qualifiers.exact_keys:
            raise ValueError('quote_exactness_and_structured_shape_disagree_or_unrecognized')
        for key, value in predicate.expected.items():
            literal = value if type(value) is str else json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            if not token_in(key, quote) or not token_in(literal, quote):
                raise ValueError('shape literal not present in minimal source quote')
        q = predicate.qualifiers
        if q.http_status is not None and not token_in(str(q.http_status), quote):
            raise ValueError('HTTP status absent from source quote')
        if any(not re.search(TYPE_CUES[t], quote, re.I) for t in q.field_types.values()):
            raise ValueError('field type surface absent from source quote')
    elif isinstance(predicate, FieldExists):
        if (not re.search(r'存在|包含|保留|\bexist\w*\b|\bcontain\w*\b|\binclude\w*\b|\bretain\b|\bkeep\b', quote, re.I)
                or re.search(r'不得包含|不能包含|不包含|必须删除|must\s+(?:not\s+(?:contain|include)|remove)|\bwithout\b', quote, re.I)):
            raise ValueError('field_presence_not_supported_by_recognized_positive_form')
    elif isinstance(predicate, ValueSemantics):
        if predicate.expected not in quote or not re.search(r'语义|含义|表示|\bmeaning\b|\bsemantic\w*\b', quote, re.I):
            raise ValueError('value semantics description absent from source quote')
    return {'policy': VERSION, 'general_semantic_proof': False, 'coverage': coverage(predicate)}


class TypedNewConstraint(NewConstraint):
    predicate: Predicate


class TypedReplacementConstraint(ReplacementConstraint):
    predicate: Predicate


class AddConstraint(previous.previous.AddConstraint):
    constraint: TypedNewConstraint

    @model_validator(mode='after')
    def API_source(self):
        constraint_evidence(self.source_quote, self.constraint.subjects, self.constraint.predicate)
        return self


class ReplaceConstraint(previous.previous.ReplaceConstraint):
    constraint: TypedReplacementConstraint

    @model_validator(mode='after')
    def API_source(self):
        constraint_evidence(self.source_quote, self.constraint.subjects, self.constraint.predicate)
        return self


Mutation = Union[AddConstraint, ReplaceConstraint, previous.Grant, previous.ReplaceAuthorization,
                 previous.previous.Revoke, previous.previous.MarkAmbiguous, previous.RecordContext]


class Batch(StrictModel):
    kind: Literal['batch']
    items: Union[previous.NoChangeItems, Annotated[list[Mutation], Field(min_length=1, max_length=8)]]


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
            if previous.client_effect(item.predicate):
                raise ValueError('client_task_narrative_cannot_be_an_API_constraint')
            if len(item.change_types) != len(set(item.change_types)) or set(item.change_types) != set(coverage(p)):
                raise ValueError('constraint_operation_coverage_mismatch_no_silent_completion')
            # Supporting reaffirmations may accompany the minimal API clause.
            # Client implementation quotes cannot support any API constraint.
            if any(previous.client_effect(r.quote) for r in item.source_refs):
                raise ValueError('client_task_quote_cannot_support_API_constraint')
            checks = []
            for ref in item.source_refs:
                try:
                    checks.append(constraint_evidence(ref.quote, item.subjects, p))
                except ValueError:
                    pass
            if not checks:
                raise ValueError('API_constraint_lacks_explicit_structured_source_support')
        return self


class ChecklistFinal(Final):
    payload: previous.ChecklistPayload


def adapter(stage, allow_lookup):
    if stage not in STAGES or type(allow_lookup) is not bool:
        raise ValueError('invalid constraint stage')
    primary = Batch if stage == 'incremental_extract' else ChecklistFinal if stage == 'full_checklist' else Final
    return TypeAdapter(Annotated[Union[primary, previous.previous.legacy.Lookup], Field(discriminator='kind')] if allow_lookup else primary)


def prompt(stage):
    if stage not in STAGES:
        raise ValueError('invalid constraint stage')
    return previous.prompt(stage)+(PROMPTS/'common.md').read_text(encoding='utf8').strip()+'\n\n'+(
        PROMPTS/(stage+'.md')).read_text(encoding='utf8').strip()+'\n'


def preview(*args, **kwargs):
    result = previous.preview(*args, **kwargs)
    result.update(instructions=prompt(result['stage']), prompt_sha256=digest(prompt(result['stage']).encode()),
                  output_schema=adapter(result['stage'], result['lookup_allowed']).json_schema())
    return result


def check_response_sources(payload, request):
    previous.check_response_sources(payload, request)
    events = [ConversationEvent.model_validate(e) for e in request['public_input']['review_input']['events']]
    if payload['kind'] == 'batch':
        refs = [SourceQuote(event_id=events[-1].event_id, quote=i['source_quote']) for i in payload['items'] if i.get('constraint') is not None]
    elif payload['kind'] == 'final':
        refs = [SourceQuote.model_validate(r) for i in payload['payload']['derived_items'] if i['kind'] == 'constraint' for r in i['source_refs']]
    else:
        refs = []
    for ref in refs:
        _resolve_quote(ref, events, authorization=True)


def check_maintained_summary(payload, request):
    if request['stage'] != 'incremental_final' or payload['kind'] != 'final':
        return
    stored = request['public_input']['maintained_state']['state']['constraints']
    items = {i['item_id']: i for i in payload['payload']['derived_items'] if i['kind'] == 'constraint'}
    models = {i['item_id']: i for i in payload['api_constraints']}
    all_stored = {i['constraint_id']: i for i in stored}
    active = {i['constraint_id'] for i in stored if i['lifecycle'] == 'active'}
    if not active <= set(items) or not set(items) <= set(all_stored):
        raise ValueError('final_constraint_inventory_differs_from_maintained_state')
    for key, item in items.items():
        original = all_stored[key]
        if (PREDICATE.validate_python(models[key]['predicate']) != PREDICATE.validate_python(original['predicate']) or
                item['subjects'] != original['subjects'] or item['lifecycle'] != original['lifecycle'] or
                item['interpretation'] != original['interpretation']):
            raise ValueError('final_constraint_contract_differs_from_maintained_state')


def state_coverage(state):
    return {i['constraint_id']: {'coverage': coverage(i['predicate']), 'lifecycle': i['lifecycle'],
            'predicate_sha256': canonical_hash(i['predicate'])} for i in state['constraints']}


def affected_constraints(state, impact):
    # Exact same shared rule as final change_types; never classify by free text.
    return [c for c in state.constraints if c.lifecycle == 'active' and impact.subject in c.subjects
            and impact.change_type in coverage(c.predicate.model_dump(mode='json'))]


class PriorState(previous.PriorState):
    def model_dump(self, *args, **kwargs):
        result = super().model_dump(*args, **kwargs)
        return {**result, 'api_constraint_coverage': state_coverage(result)}


class StateView(previous.StateView):
    def snapshot(self):
        return PriorState(self.owner.kernel.engine.snapshot(), self.owner.context.snapshot())


class DomainEngine(previous.DomainEngine):
    def __init__(self, bundle, config_hash):
        super().__init__(bundle, config_hash)
        self.engine = StateView(self)

    def snapshot(self):
        original = super().snapshot()
        covered = state_coverage(original['state'])
        return {**original, 'constraint_representation_policy': VERSION, 'api_constraint_coverage': covered,
                'qualified_state_sha256': canonical_hash({'domain_state': original, 'coverage': covered, 'policy': VERSION})}

    def submit(self, raw, index, call_id):
        draft = parse_json(raw)
        if set(draft) != {'items'}:
            raise ValueError('constraint compiler expects only items')
        Batch.model_validate({'kind': 'batch', **draft})
        result = super().submit(raw, index, call_id)
        result['api_constraint_coverage'] = self.snapshot()['api_constraint_coverage']
        return result

    def assess(self):
        # Only the selector changes. Frozen grant/exception/condition/value
        # checks are reused. Entry lock forbids concurrent version wiring;
        # the historical selector is restored even after an exception.
        with patch.object(legacy_state, '_affected_constraints', affected_constraints):
            result = super().assess()
        return {**result, 'constraint_coverage_policy': VERSION}
