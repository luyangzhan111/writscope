"""Streaming HTTP receipt and R3 bridge, exercised ONLY with MockTransport.

Same endpoint, bytes, status and body parsing as the future HTTP boundary.
No real client factory, credential parameter, retry or redirect path is exposed.
"""
import json
import math
import time

import httpx

from scripts.s4_r5_acceptance import offline_guard
from scripts.s4_r6_transport_contract import ENDPOINT, decode, stop_reason
from scripts.s4_r7_token_boundary import usage_boundary
from scripts.s4_shared_transport_offline import SharedIntegrityError
from scripts.s4_synthetic_cost_wire import mock_wire, SyntheticBudgetStop
from scripts.s4_unified_journal import now
from writscope.artifacts import canonical_hash, digest

SOURCE = 'fixed_mock_transport_received_bytes_v2'
HEADERS = {'content-type', 'x-request-id', 'request-id', 'date'}


def receive(wire, transport, *, clock=time.monotonic):
    if type(transport) is not httpx.MockTransport:
        raise PermissionError('live HTTP transport unavailable')
    start = clock()
    raw, status, error, headers, complete = bytearray(), None, None, {}, False
    try:
        with offline_guard(), httpx.Client(transport=transport, timeout=90, trust_env=False,
                                          follow_redirects=False) as client:
            with client.stream('POST', ENDPOINT, content=wire,
                               headers={'Content-Type': 'application/json', 'Accept-Encoding': 'identity'}) as response:
                status = response.status_code
                headers = {k: v for k, v in response.headers.items() if k in HEADERS}
                if response.headers.get('content-encoding', 'identity') != 'identity':
                    error = 'unsupported_content_encoding'
                else:
                    for chunk in response.iter_raw():
                        raw.extend(chunk[:100001-len(raw)])
                        if len(raw) > 100000:
                            error = 'response_size_exceeded'
                            break
                        if clock()-start >= 90:
                            error = 'timeout'
                            break
                    else:
                        complete = True
    except httpx.TimeoutException:
        error = 'timeout'
    except httpx.HTTPError:
        error = 'connection_error'
    elapsed = (clock()-start)*1000
    if elapsed >= 90000:
        error, complete = 'timeout', False
    raw = bytes(raw)
    receipt = {'source': SOURCE, 'endpoint': ENDPOINT, 'request_sha256': digest(wire),
               'response_sha256': digest(raw), 'captured_bytes': len(raw), 'complete_response': complete,
               'http_status': status, 'transport_error': error, 'headers': headers,
               'elapsed_ms': elapsed, 'actual_model_calls': 0}
    return raw, receipt


def interpret(raw, receipt, request, input_bound):
    wire = mock_wire(request)
    if (set(receipt) != {'source', 'endpoint', 'request_sha256', 'response_sha256', 'captured_bytes',
                       'complete_response', 'http_status', 'transport_error', 'headers', 'elapsed_ms', 'actual_model_calls'} or
            receipt['source'] != SOURCE or receipt['endpoint'] != ENDPOINT or
            receipt['actual_model_calls'] != 0 or receipt['request_sha256'] != digest(wire) or
            receipt['response_sha256'] != digest(raw) or receipt['captured_bytes'] != len(raw) or
            len(raw) > 100001 or type(receipt['complete_response']) is not bool or
            type(receipt['elapsed_ms']) not in (int, float) or not math.isfinite(receipt['elapsed_ms']) or receipt['elapsed_ms'] < 0 or
            not isinstance(receipt['headers'], dict) or set(receipt['headers']) - HEADERS):
        raise SharedIntegrityError('received provenance/byte binding invalid')
    status, transport_error = receipt['http_status'], receipt['transport_error']
    if (status is not None and (type(status) is not int or not 100 <= status <= 599)) or transport_error not in (
            None, 'timeout', 'connection_error', 'response_size_exceeded', 'unsupported_content_encoding'):
        raise SharedIntegrityError('invalid receipt status/error')
    if receipt['complete_response']:
        if status is None or transport_error is not None or len(raw) > 100000 or receipt['elapsed_ms'] >= 90000:
            raise SharedIntegrityError('invalid complete receipt')
        parsed, finish, error, legacy_usage = decode(raw, status, request['stage'], request['lookup_allowed'], input_bound)
        try:
            envelope = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            envelope = {}
        envelope = envelope if isinstance(envelope, dict) else {}
        usage = usage_boundary(envelope.get('usage'), input_bound)
        if usage['accounting_errors']:
            error = 'token_accounting_fault'
        usage.update(returned_model=envelope.get('model'), system_fingerprint=envelope.get('system_fingerprint'))
    else:
        if transport_error is None:
            raise SharedIntegrityError('partial bytes lack failure reason')
        if transport_error == 'response_size_exceeded' and len(raw) != 100001:
            raise SharedIntegrityError('size failure without limit crossing')
        parsed, finish, error = None, None, transport_error
        usage = usage_boundary(None, input_bound)
        usage.update(returned_model=None, system_fingerprint=None)
    usage.update(provider_elapsed_ms=receipt['elapsed_ms'], transport_receipt=receipt)
    return parsed, finish, error, usage


def round_stop(attempts):
    if any(a['error_kind'] == 'token_accounting_fault' for a in attempts):
        return 'token_accounting_fault'
    return stop_reason(attempts)


def dispatch(journal, job, request, transport):
    if type(transport) is not httpx.MockTransport:
        raise PermissionError('live transport disabled before reservation')
    reason = round_stop(journal.snapshot()['attempts'])
    if reason:
        raise SharedIntegrityError(reason)
    wire = mock_wire(request)
    proof = journal._admissions.get(digest(wire))
    if proof is None:
        raise SharedIntegrityError('missing token admission')
    try:
        attempt = journal.reserve(job, request['stage'], wire, 'lookup_response' in request['public_input'])
        journal.transition(attempt, 'reserved', 'dispatch_started', sent_at=now())
        raw, receipt = receive(wire, transport)
        # Receipt persisted with raw bytes before parsing. A crash here leaves received,
        # never invented validated usage or a refunded reservation.
        journal.persist_received(attempt, raw, receipt)
        payload, finish, error, usage = interpret(raw, receipt, request, proof['input_tokens'])
        usage['token_admission'] = proof
        journal.transition(attempt, 'received', 'failed' if error else 'completed', finish_reason=finish,
                           error_kind=error, validation='rejected' if error else 'accepted', usage=json.dumps(usage))
    except SyntheticBudgetStop:
        raise
    except Exception as exc:
        raise SharedIntegrityError('transport or persistence boundary failed') from exc
    reason = round_stop(journal.snapshot()['attempts'])
    if reason:
        raise SharedIntegrityError(reason)
    if error:
        raise ValueError(error)
    return payload


class FixtureBridge:
    def __init__(self, journal, repeat_index):
        self.journal, self.repeat_index = journal, repeat_index

    def __call__(self, case, method, stage, request, step):
        wire = mock_wire(request)
        self.journal.admit_test_bound(wire, 32768)
        def handler(incoming):
            if incoming.content != wire or str(incoming.url) != ENDPOINT or 'authorization' in incoming.headers:
                raise SharedIntegrityError('outbound request mismatch')
            body = {'id': 'fixed-response-not-provider-id', 'model': 'deepseek-flash',
                    'choices': [{'message': {'content': step.text if step else ''}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 120, 'completion_tokens': 30, 'total_tokens': 150,
                              'prompt_cache_hit_tokens': 20, 'prompt_cache_miss_tokens': 100,
                              'completion_tokens_details': {'reasoning_tokens': 0}}}
            # Form the fixture only INSIDE dispatch, after the journal send marker.
            return httpx.Response(200 if step and step.action == 'response' else 503,
                                  stream=httpx.ByteStream(json.dumps(body, ensure_ascii=False).encode()))
        identity = {'case_id': case, 'method': method, 'track': 'A', 'repeat_index': self.repeat_index}
        return dispatch(self.journal, canonical_hash(identity), request, httpx.MockTransport(handler))
