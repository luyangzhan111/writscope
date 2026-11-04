"""Transport-boundary rehearsal using the existing journal, never live I/O.

This is not a method runner. All socket traffic is forbidden and only a fixed
httpx.MockTransport can be constructed. Received bytes are persisted before
decoding. Token certificates here are test controls, not provider validation.
"""
from dataclasses import dataclass
import json
import time

import httpx

from scripts.s4_prompt_candidate import adapter
from scripts.s4_shared_transport_offline import SharedIntegrityError
from scripts.s4_synthetic_cost_wire import mock_wire
from scripts.s4_unified_journal import now
from scripts.s4_r5_acceptance import offline_guard
from writscope.artifacts import digest, canonical_hash
from writscope.contracts import parse_json

ENDPOINT = 'https://api.deepseek.com/chat/completions'
VERSION = 'DeepSeek-V4.1-Flash-1017'


@dataclass(frozen=True)
class TokenEvidence:
    request_sha256: str
    input_tokens: int
    kind: str = 'synthetic_test_control'


def token_admission(wire, evidence, *, live=False):
    if live:
        raise PermissionError('no validated provider tokenizer/template/overhead registry installed')
    if (type(evidence) is not TokenEvidence or evidence.kind != 'synthetic_test_control'
            or evidence.request_sha256 != digest(wire) or type(evidence.input_tokens) is not int
            or not 0 <= evidence.input_tokens <= 32768):
        raise SharedIntegrityError('missing, stale or excessive input token evidence')
    if len(wire) > 500000:
        raise SharedIntegrityError('request byte limit; never truncate')
    return {'input_tokens':evidence.input_tokens,'kind':'synthetic_test_control',
            'provider_token_bound_verified':False,'request_sha256':digest(wire)}


def start_live(*, credential_reader=None, transport_factory=None, approval=None):
    # Design approval and a self-declared certificate can never cross this gate.
    raise PermissionError('real sending closed; design confirmation is not execution approval')


def stop_reason(rows):
    streak=0
    for row in rows:
        if row['status'] in ('reserved','dispatch_started','received'):
            return 'unresolved_attempt'
        if row['error_kind'] in ('credential_or_permission','model_drift','token_usage_exceeds_bound','request_integrity'):
            return row['error_kind']
        if row['error_kind'] in ('timeout','connection_error','service_http_error'):
            streak+=1
            if streak>=3:return 'three_consecutive_service_failures'
        elif row['http_status']==200: streak=0
    return None


def normalized_usage(raw):
    raw=raw if isinstance(raw,dict) else {}
    def number(key):
        v=raw.get(key)
        return v if type(v) is int and v>=0 else None
    hit,miss=number('prompt_cache_hit_tokens'),number('prompt_cache_miss_tokens')
    total=number('prompt_tokens')
    if total is not None and hit is not None and miss is not None and hit+miss!=total:
        hit=miss=None
    return {'input_tokens':total,'output_tokens':number('completion_tokens'),
            'cache_hit_input_tokens':hit,'cache_miss_input_tokens':miss,
            'reasoning_tokens':number('reasoning_tokens'),'billed_cost':None,
            'usage_source':'synthetic_received_envelope_not_provider_measurement'}


def decode(raw, status, stage, lookup_allowed, input_bound):
    error=None; payload=None; finish=None; envelope={}
    if status in (401,403):error='credential_or_permission'
    elif status==429 or status>=500:error='service_http_error'
    elif 300<=status<400:error='redirect_rejected'
    elif status!=200:error='http_error'
    elif len(raw)>100000:error='response_size_exceeded'
    else:
        try:
            envelope=json.loads(raw)
            if not isinstance(envelope,dict):raise ValueError('invalid_envelope')
            model=envelope.get('model')
            # Alias-only or absent version remains unknown; never invent a pin.
            if model not in (None,'deepseek-flash',VERSION):error='model_drift'
            choices=envelope['choices']
            if not isinstance(choices,list) or len(choices)!=1:raise ValueError('invalid_choices')
            finish=choices[0]['finish_reason']
            if finish!='stop':raise ValueError('truncated_or_unknown_finish')
            payload=parse_json(choices[0]['message']['content'])
            adapter(stage,lookup_allowed).validate_python(payload)
        except (ValueError,TypeError,KeyError,IndexError) as exc:
            error=error or 'invalid_output:'+str(exc)
    usage=normalized_usage(envelope.get('usage') if isinstance(envelope,dict) else None)
    if ((usage['input_tokens'] is not None and usage['input_tokens']>input_bound) or
            (usage['output_tokens'] is not None and usage['output_tokens']>4096)):
        error='token_usage_exceeds_bound'
    usage['returned_model']=envelope.get('model') if isinstance(envelope,dict) else None
    usage['system_fingerprint']=envelope.get('system_fingerprint') if isinstance(envelope,dict) else None
    return payload,finish,error,usage


def dispatch_offline(journal,job,request,fixture,evidence,*,clock=time.monotonic):
    """Reuse reserve/send/receive/validate ledger; do not relabel virtual money."""
    if set(fixture)!={'kind','status','body','fault'} or fixture['kind']!='fixed_mock_v1':
        raise ValueError('only explicit fixed mock fixture accepted')
    if type(fixture['body']) is not bytes or type(fixture['status']) is not int or not 100<=fixture['status']<=599:
        raise ValueError('invalid mock response')
    if fixture['fault'] not in (None,'timeout','connection_error'):
        raise ValueError('invalid mock fault')
    wire=mock_wire(request)
    admitted=token_admission(wire,evidence)
    reason=stop_reason(journal.snapshot()['attempts'])
    if reason:raise SharedIntegrityError(reason)
    started=clock()
    try:
        attempt=journal.reserve(job,request['stage'],wire,'lookup_response' in request['public_input'])
        journal.transition(attempt,'reserved','dispatch_started',sent_at=now())
    except Exception as exc:
        from scripts.s4_synthetic_cost_wire import SyntheticBudgetStop
        if isinstance(exc,SyntheticBudgetStop):raise
        raise SharedIntegrityError('pre-send persistence failure') from exc
    # A fixture becomes a received record only after the simulated send begins.
    raw=None;status=None;network_error=None
    def handler(incoming):
        if str(incoming.url)!=ENDPOINT or incoming.content!=wire or 'authorization' in incoming.headers:
            raise SharedIntegrityError('request integrity')
        if fixture['fault']=='timeout':raise httpx.ReadTimeout('fixed timeout',request=incoming)
        if fixture['fault']=='connection_error':raise httpx.ConnectError('fixed error',request=incoming)
        return httpx.Response(fixture['status'],content=fixture['body'])
    try:
        with offline_guard(), httpx.Client(transport=httpx.MockTransport(handler),timeout=90,follow_redirects=False,trust_env=False) as client:
            response=client.post(ENDPOINT,content=wire)
        raw,status=response.content,response.status_code
    except httpx.TimeoutException:network_error='timeout'
    except httpx.ConnectError:network_error='connection_error'
    elapsed=clock()-started
    if raw is not None:
        try:
            journal.transition(attempt,'dispatch_started','received',received_at=now(),response=raw,
                               response_hash=digest(raw),http_status=status)
        except Exception as exc:raise SharedIntegrityError('received persistence failure') from exc
        payload,finish,error,usage=decode(raw,status,request['stage'],request['lookup_allowed'],admitted['input_tokens'])
        if elapsed>=90:error='timeout'
        before='received'
    else:
        payload,finish,error,usage=None,None,network_error,normalized_usage(None);before='dispatch_started'
    usage.update(provider_elapsed_ms=elapsed*1000,token_admission=admitted)
    try:
        journal.transition(attempt,before,'failed' if error else 'completed',received_at=now(),
                           finish_reason=finish,error_kind=error,validation='rejected' if error else 'accepted',
                           usage=json.dumps(usage,ensure_ascii=False))
    except Exception as exc:raise SharedIntegrityError('validation persistence failure') from exc
    reason=stop_reason(journal.snapshot()['attempts'])
    if reason:raise SharedIntegrityError(reason)
    if error:raise ValueError(error)
    return payload


class OfflineBridge:
    """Injected into the existing R3 executor for a joint rehearsal only."""
    def __init__(self,journal,repeat_index):self.journal,self.repeat_index=journal,repeat_index

    def __call__(self,case,method,stage,request,step):
        identity={'case_id':case,'method':method,'track':'A','repeat_index':self.repeat_index}
        if step is None or step.action=='error':
            fixture={'kind':'fixed_mock_v1','status':503,'body':b'{}','fault':None}
        else:
            fixture={'kind':'fixed_mock_v1','status':200,'body':json.dumps({'choices':[{'message':{'content':step.text},'finish_reason':'stop'}],'usage':None}).encode(), 'fault':None}
        # Fixed synthetic count exercises admission only; never a tokenizer.
        return dispatch_offline(self.journal,canonical_hash(identity),request,fixture,
                                TokenEvidence(digest(mock_wire(request)),32768))
