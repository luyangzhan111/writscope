"""Versioned provider boundary for synthetic transport only; no live client."""
import hashlib
import json
import time

import httpx

from scripts.s4_prompt_candidate import adapter
from scripts.s4_shared_transport_offline import SharedIntegrityError
from scripts.s4_synthetic_cost_wire import mock_wire
from scripts.s4_unified_journal import now
from writscope.contracts import parse_json

ENDPOINT = 'https://mock.invalid/chat'


def dispatch(journal, job, request, fixture, *, endpoint=ENDPOINT, max_response_bytes=100000):
    """Reserve and record one synthetic request, preserving response before parsing.

    A fixture is synthetic data, not an observed provider response. This function
    always creates its own MockTransport; it cannot accept a real transport.
    """
    if endpoint != ENDPOINT or type(max_response_bytes) is not int or max_response_bytes < 1:
        raise ValueError('invalid mock endpoint or response limit')
    if (not isinstance(fixture, dict) or fixture.get('kind') != 'synthetic_response_v1' or
            set(fixture) != {'kind', 'status', 'body', 'timeout'} or
            type(fixture['status']) is not int or not 100 <= fixture['status'] <= 599 or
            not isinstance(fixture['body'], bytes) or type(fixture['timeout']) is not bool):
        raise ValueError('invalid synthetic response fixture')
    stage = request['stage']
    wire = mock_wire(request)
    try:
        attempt = journal.reserve(job, stage, wire, 'lookup_response' in request['public_input'])
        journal.transition(attempt, 'reserved', 'dispatch_started', sent_at=now())
    except Exception as exc:
        # Budget errors retain their type for the shared round's stop policy.
        from scripts.s4_synthetic_cost_wire import SyntheticBudgetStop
        if isinstance(exc, SyntheticBudgetStop):
            raise
        raise SharedIntegrityError('pre-dispatch persistence failed') from exc
    started = time.perf_counter()
    try:
        def handler(incoming):
            if str(incoming.url) != ENDPOINT or incoming.content != wire:
                raise SharedIntegrityError('synthetic wire or destination mismatch')
            if fixture['timeout']:
                raise httpx.ReadTimeout('synthetic timeout', request=incoming)
            return httpx.Response(fixture['status'], content=fixture['body'],
                                  headers={'location': 'https://other.invalid/'})
        with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            response = client.post(ENDPOINT, content=wire)
        raw = response.content
        journal.transition(attempt, 'dispatch_started', 'received', received_at=now(), response=raw,
                           response_hash=hashlib.sha256(raw).hexdigest(), http_status=response.status_code)
    except httpx.TimeoutException:
        try:
            journal.transition(attempt, 'dispatch_started', 'failed', received_at=now(),
                               error_kind='synthetic_timeout', validation='not_performed',
                               usage=json.dumps({'provider_elapsed_ms':(time.perf_counter()-started)*1000,
                                                 'input_tokens':None, 'output_tokens':None, 'billed_cost':None}))
        except Exception as exc:
            raise SharedIntegrityError('timeout persistence failed') from exc
        raise ValueError('synthetic_timeout') from None
    except Exception as exc:
        raise SharedIntegrityError('synthetic response persistence failed') from exc
    error, finish, payload = None, None, None
    try:
        if 300 <= response.status_code < 400:
            raise ValueError('redirect_rejected')
        if response.status_code != 200:
            raise ValueError('synthetic_http_error')
        if len(raw) > max_response_bytes:
            raise ValueError('response_size_exceeded')
        body = json.loads(raw)
        finish = body['choices'][0]['finish_reason']
        if finish != 'stop':
            raise ValueError('truncated_or_unknown_finish')
        payload = parse_json(body['choices'][0]['message']['content'])
        adapter(stage, request['lookup_allowed']).validate_python(payload)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        error = str(exc)
    try:
        journal.transition(attempt, 'received', 'failed' if error else 'completed',
                           error_kind=error, finish_reason=finish,
                           validation='rejected' if error else 'accepted',
                           usage=json.dumps({'provider_elapsed_ms':(time.perf_counter()-started)*1000,
                                             'input_tokens':None, 'output_tokens':None,
                                             'cache_hit_input_tokens':None, 'cache_miss_input_tokens':None,
                                             'reasoning_tokens':None, 'billed_cost':None}))
    except Exception as exc:
        raise SharedIntegrityError('validation persistence failed') from exc
    if error:
        raise ValueError(error)
    return payload
