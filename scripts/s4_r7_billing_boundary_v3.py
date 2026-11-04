"""Frozen-price arithmetic and strict raw-response accounting; no transport.

Local counts remain estimates. This module cannot certify a provider bound or
lower a reservation. Missing token/tool/bill evidence is retained as unknown.
"""
from datetime import datetime, timedelta
from decimal import Decimal
import json

from scripts.s4_r7_token_boundary import usage_boundary
from writscope.artifacts import digest

RATES = {'cache_hit': Decimal('0.04'), 'cache_miss': Decimal('2'), 'output': Decimal('8')}


def checked_price(snapshot, now):
    try:
        checked = datetime.fromisoformat(snapshot['checked_at'])
        valid = datetime.fromisoformat(snapshot['valid_until'])
        valid_time = (now.tzinfo is not None and checked.tzinfo is not None and valid.tzinfo is not None
                      and checked <= now < valid <= checked+timedelta(hours=24))
        rates = {k: Decimal(v) for k, v in snapshot['peak_cny_per_million'].items()}
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise PermissionError('invalid price snapshot') from None
    if (not valid_time or snapshot.get('currency') != 'CNY' or snapshot.get('model') != 'deepseek-flash'
            or rates != RATES or snapshot.get('source') != 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'):
        raise PermissionError('expired, changed or unsupported price; refreeze required')
    return rates


def reservation(input_bound, output_bound=4096, *, attempts=1080, cap_micro=120000000):
    if any(type(x) is not int or x <= 0 for x in (input_bound, output_bound, attempts, cap_micro)):
        raise ValueError('positive integer limits required')
    per = input_bound*2 + output_bound*8
    return {'per_attempt_cny_micro': per, 'all_attempts_cny_micro': per*attempts,
            'fits_proposed_cap': per*attempts <= cap_micro, 'attempts_with_no_release': cap_micro//per,
            'provider_bill_hard_cap': False, 'kind': 'conditional_peak_rate_arithmetic'}


def account_raw(raw, *, input_bound=32768):
    """Check actual captured bytes. JSON fields cannot authenticate their origin."""
    errors = []
    def pairs(items):
        obj = {}
        for k, v in items:
            if k in obj:
                raise ValueError('duplicate JSON key')
            obj[k] = v
        return obj
    try:
        if type(raw) is not bytes or len(raw) > 100000:
            raise ValueError('body boundary')
        body = json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        if type(body) is not dict:
            raise ValueError('body type')
    except (ValueError, UnicodeError):
        body = {}; errors.append('unparseable_or_partial_body')
    usage = body.get('usage')
    checked = usage_boundary(usage, input_bound)
    errors += checked['accounting_errors']
    usage = usage if type(usage) is dict else {}
    for required in ('input_tokens', 'output_tokens'):
        if checked[required] is None:
            errors.append('missing_'+required)
    total = usage.get('total_tokens')
    if total is None:
        errors.append('missing_total_tokens')
    details = usage.get('prompt_tokens_details')
    cached = details.get('cached_tokens') if type(details) is dict else None
    if details is not None and type(details) is not dict:
        errors.append('invalid_prompt_details')
    if cached is not None:
        if type(cached) is not int or cached < 0:
            errors.append('invalid_nested_cached_tokens')
        elif (checked['input_tokens'] is not None and cached > checked['input_tokens'] or
              checked['cache_hit_input_tokens'] is not None and cached != checked['cache_hit_input_tokens']):
            errors.append('nested_cache_disagreement')
    elif type(details) is dict and 'cached_tokens' in details:
        errors.append('unknown_nested_cached_tokens')
    if checked['reasoning_tokens'] not in (None, 0):
        errors.append('unexpected_reasoning_in_non_thinking')
    for choice in body.get('choices', []) if type(body.get('choices')) is list else []:
        message = choice.get('message', {}) if type(choice) is dict else {}
        if type(message) is dict and any(message.get(k) for k in ('tool_calls', 'function_call', 'reasoning_content')):
            errors.append('unexpected_tool_or_reasoning_content')
    # Server tools are separately billed. Missing counters remain unknown; tools
    # disabled in the outbound wire is recorded separately, never inferred here.
    tools = usage.get('server_tool_use', body.get('server_tool_use'))
    if ('server_tool_use' in usage and 'server_tool_use' in body and
            usage['server_tool_use'] != body['server_tool_use']):
        errors.append('server_tool_usage_disagreement')
    tool_values = {'tool_search': None, 'tool_fetch': None}
    for location in (usage, body):
        if any(k in location for k in tool_values):
            errors.append('unsupported_tool_counter_layout')
    if tools is not None:
        if type(tools) is not dict or set(tools) != set(tool_values):
            errors.append('invalid_server_tool_usage')
        else:
            for k in tool_values:
                value = tools[k]
                if type(value) is not int or value < 0:
                    errors.append('invalid_'+k)
                else:
                    tool_values[k] = value
                    if value:
                        errors.append('unexpected_paid_server_tool')
    hit, miss = checked['cache_hit_input_tokens'], checked['cache_miss_input_tokens']
    inp, out = checked['input_tokens'], checked['output_tokens']
    exact = hit is not None and miss is not None
    token_cost = ((Decimal(hit)*RATES['cache_hit']+Decimal(miss)*RATES['cache_miss']+Decimal(out)*RATES['output'])
                  if exact else Decimal(inp)*RATES['cache_miss']+Decimal(out)*RATES['output']) if inp is not None and out is not None else None
    if errors:
        token_cost = None
    # Decimal has micro-CNY units at per-million-token prices. Keep fractional
    # micro-units; reservation separately rounds conservatively to integer micro.
    return {'response_sha256': digest(raw) if type(raw) is bytes else None,
            'usage': {k: checked[k] for k in ('input_tokens', 'output_tokens', 'cache_hit_input_tokens',
                      'cache_miss_input_tokens', 'reasoning_tokens')},
            'server_tool_use': tool_values, 'errors': sorted(set(errors)),
            'token_cost_cny_micro': str(token_cost) if token_cost is not None else None,
            'token_cost_source': ('response_usage_peak_rate_estimate' if exact else 'all_input_miss_conditional_estimate')
                                 if token_cost is not None else 'unknown',
            'combined_token_and_tool_cost': str(token_cost) if token_cost is not None and
                tool_values == {'tool_search': 0, 'tool_fetch': 0} else None,
            'combined_cost_unit': 'micro_CNY', 'provider_bill': None,
            'stop_round': bool(errors), 'origin_authentication': 'caller_must_verify_transport',
            'unknown_tool_usage': tools is None}


def production_admission(*, wire, local_count, provider_bound=None, price=None, now=None):
    """No artifact boolean can promote an estimate to a verified provider bound."""
    if local_count.get('request_sha256') != digest(wire):
        raise PermissionError('token evidence request mismatch')
    if price is not None:
        checked_price(price, now)
    raise PermissionError('provider model/template/billing proof not installed; production tokens closed')
