"""Structured synthetic execution proposal; never an approval or live entry."""
from datetime import datetime, timezone

from writscope.artifacts import canonical_hash

MATERIALS = ('public_bundle','method_prompt_and_schema','visible_history',
             'method_maintained_state','authorized_public_lookup_result')


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed
    except (ValueError, TypeError):
        raise ValueError('invalid price expiry') from None


def prepare(checkpoint_plan, *, model, temperature, max_output_tokens, price, price_valid_until):
    if (not isinstance(checkpoint_plan,dict) or checkpoint_plan.get('version') != 's4_r3_checkpoint_plan_v1' or
            checkpoint_plan.get('execution_kind') != 'offline_mock' or checkpoint_plan.get('real_transport_allowed') is not False or
            checkpoint_plan.get('actual_model_calls') != 0 or checkpoint_plan.get('recipient') != 'mock.invalid' or
            checkpoint_plan.get('materials') != 'public_synthetic_only'):
        raise ValueError('only synthetic checkpoint plans accepted')
    policy = checkpoint_plan['policy']
    if (not isinstance(model,str) or not model.startswith('synthetic-') or len(model) > 100 or
            type(temperature) not in (int,float) or not 0 <= temperature <= 2 or
            type(max_output_tokens) is not int or max_output_tokens != policy['max_output_tokens'] or
            not isinstance(price,dict) or set(price) != {'input_units_per_million','output_units_per_million'} or
            any(type(v) is not int or v < 0 for v in price.values()) or
            price['input_units_per_million'] != policy['input_rate_per_million'] or
            price['output_units_per_million'] != policy['output_rate_per_million']):
        raise ValueError('synthetic configuration or price mismatch')
    _timestamp(price_valid_until)
    return {'version':'s4_r3_synthetic_proposal_v1','status':'draft_unapproved','execution_kind':'offline_mock',
            'checkpoint_plan_sha256':canonical_hash(checkpoint_plan),
            'checkpoint_plan':checkpoint_plan, 'provider':'mock.invalid', 'endpoint':'https://mock.invalid/chat',
            'model':{'id':model,'temperature':temperature,'max_output_tokens':max_output_tokens},
            'price':{'kind':'synthetic_units_only','rates':price,'valid_until':price_valid_until},
            'limits':{'max_attempts':policy['max_attempts'],'cap_virtual_micro':policy['cap_micro'],
                      'concurrency':1,'retries':0},'outbound_scope':list(MATERIALS),
            'evaluator_reference_allowed':False,'real_transport_allowed':False,'actual_model_calls':0}


def validate(proposal, checkpoint_plan, *, expected_hash, now=None):
    if not isinstance(proposal,dict):
        raise ValueError('synthetic proposal required')
    try:
        model, price = proposal['model'],proposal['price']
        expected = prepare(checkpoint_plan,model=model['id'],temperature=model['temperature'],
                           max_output_tokens=model['max_output_tokens'],price=price['rates'],
                           price_valid_until=price['valid_until'])
        current = now if now is not None else datetime.now(timezone.utc)
        if current.tzinfo is None or _timestamp(price['valid_until']) <= current:
            raise ValueError('synthetic price expired')
    except (KeyError,TypeError):
        raise ValueError('invalid synthetic proposal') from None
    if proposal != expected or expected_hash != canonical_hash(proposal):
        raise ValueError('synthetic proposal binding mismatch')
    return canonical_hash(proposal)


def authorize_real(*args, **kwargs):
    """No trusted approval source or real provider has been connected."""
    raise PermissionError('real approval and execution are unavailable')
