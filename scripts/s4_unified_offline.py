"""Unified S4 offline entry using a fixed in-memory HTTP mock and one journal."""
import hashlib
import json
import time
from pathlib import Path

import httpx

from scripts.s4_candidate_cost_mock_runner import bindings, execute
from scripts.s4_prompt_candidate import adapter
from scripts.s4_repeat_schedule import build
from scripts.s4_shared_transport_offline import SharedIntegrityError
from scripts.s4_simulated_artifact_bundle import _bytes, _write
from scripts.s4_synthetic_cost_wire import SyntheticBudgetStop, mock_wire
from scripts.s4_unified_journal import Journal, now
from writscope.artifacts import canonical_hash, digest
from writscope.contracts import parse_json

ROOT = Path(__file__).resolve().parents[1]


def unified_sources():
    return {p: digest((ROOT / p).read_bytes()) for p in ('scripts/s4_unified_offline.py','scripts/s4_unified_journal.py')}


class MockDispatcher:
    def __init__(self, journal, max_response_bytes=100000):
        self.journal, self.max_response_bytes = journal, max_response_bytes

    def dispatch(self, job, stage, request, step):
        if stage != request.get('stage'):
            raise SharedIntegrityError('stage mismatch')
        try:
            wire = mock_wire(request)
            attempt = self.journal.reserve(job, stage, wire, 'lookup_response' in request['public_input'])
            self.journal.transition(attempt, 'reserved', 'dispatch_started', sent_at=now())
        except SyntheticBudgetStop:
            raise
        except Exception as exc:
            raise SharedIntegrityError('reservation or dispatch persistence failed') from exc
        started = time.perf_counter()
        try:
            def handler(incoming):
                if incoming.content != wire:
                    raise SharedIntegrityError('wire mismatch')
                if step is None or step.action == 'error':
                    if step is not None and step.text == 'synthetic_timeout':
                        raise httpx.ReadTimeout('synthetic timeout', request=incoming)
                    return httpx.Response(503, content=b'{"synthetic_error":true}')
                if step.text.startswith('S4_MOCK_RAW:'):
                    return httpx.Response(200, content=step.text.removeprefix('S4_MOCK_RAW:').encode('utf-8'))
                finish = 'length' if step.text.startswith('S4_MOCK_TRUNCATED:') else 'stop'
                content = step.text.removeprefix('S4_MOCK_TRUNCATED:')
                return httpx.Response(200, json={'choices':[{'message':{'content':content},'finish_reason':finish}], 'usage':None})
            with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
                response = client.post('https://mock.invalid/chat', content=wire)
            raw = response.content
            self.journal.transition(attempt, 'dispatch_started', 'received', received_at=now(), response=raw,
                response_hash=hashlib.sha256(raw).hexdigest(), http_status=response.status_code)
        except httpx.TimeoutException:
            try:
                self.journal.transition(attempt, 'dispatch_started', 'failed', received_at=now(), error_kind='synthetic_timeout',
                                        validation='not_performed', usage=json.dumps({'provider_tokens':None,'elapsed_ms':round((time.perf_counter()-started)*1000,3)}))
            except Exception as exc:
                raise SharedIntegrityError('timeout persistence failed') from exc
            raise ValueError('synthetic_timeout') from None
        except Exception as exc:
            raise SharedIntegrityError('response persistence failed') from exc
        error = None
        finish = None
        try:
            if response.status_code != 200:
                raise ValueError('synthetic_http_error')
            body = json.loads(raw)
            finish = body.get('choices', [{}])[0].get('finish_reason')
            if finish != 'stop':
                raise ValueError('truncated')
            content = body['choices'][0]['message']['content']
            if len(content.encode('utf-8')) > self.max_response_bytes:
                raise ValueError('response_size_exceeded')
            adapter(stage, request['lookup_allowed']).validate_python(parse_json(content))
        except (ValueError, KeyError, TypeError) as exc:
            error = str(exc)
        try:
            self.journal.transition(attempt, 'received', 'failed' if error else 'completed',
                error_kind=error, finish_reason=finish, validation='rejected' if error else 'accepted',
                usage=json.dumps({'input_tokens':None,'output_tokens':None,'cache_hit_input_tokens':None,
                                  'cache_miss_input_tokens':None,'reasoning_tokens':None,'billed_cost':None,
                                  'provider_elapsed_ms':round((time.perf_counter()-started)*1000,3)}))
        except Exception as exc:
            raise SharedIntegrityError('validation persistence failed') from exc
        if error and step is not None and step.action == 'response':
            raise ValueError(error)


def run(directory, bundle_root, scripts_path, cases, repetitions, policy, *, max_response_bytes=100000):
    directory, bundle_root, scripts_path = Path(directory), Path(bundle_root), Path(scripts_path)
    if directory.resolve().is_relative_to(bundle_root.resolve()) or directory.resolve() == scripts_path.resolve():
        raise ValueError('output overlaps method input')
    if type(max_response_bytes) is not int or max_response_bytes < 1:
        raise ValueError('invalid response limit')
    if policy.get('max_output_tokens') != 4096:
        raise ValueError('mock output limit must match reservation')
    schedule = build(bundle_root, cases, repetitions)
    plan = {'version':'s4_unified_offline_plan_v1','execution_kind':'offline_mock', 'schedule':schedule,
            'scripts_sha256':digest(scripts_path.read_bytes()), 'policy':policy, 'max_response_bytes':max_response_bytes,
            'source_binding':bindings(), 'unified_sources':unified_sources(), 'actual_model_calls':0, 'real_transport_allowed':False}
    directory.mkdir(parents=False, exist_ok=False)
    _write(directory / 'plan.json', _bytes(plan))
    journal = Journal(directory / 'journal.sqlite', plan, policy, create=True)
    dispatcher = MockDispatcher(journal, max_response_bytes)
    rows, rounds, remaining, stopped = [], [], policy['max_attempts'], None
    for repeat in range(1, repetitions + 1):
        if not remaining or stopped:
            rows.extend({**j,'status':'not_run','attempts':0,'final_present':False} for j in schedule['plan']['jobs'] if j['repeat_index'] == repeat)
            continue
        def dispatch(case, method, stage, request, step):
            identity = {'case_id':case,'method':method,'track':'A','repeat_index':repeat}
            dispatcher.dispatch(canonical_hash(identity), stage, request, step)
        subrun = execute(bundle_root, scripts_path, cases, schedule['plan']['methods'], remaining, dispatch)
        snap = journal.snapshot()
        if subrun['round_integrity_failure']:
            # The reused runner creates an in-memory attempt before calling us.
            # If persistence failed before dispatch, remove that unsent placeholder.
            for job in subrun['jobs']:
                ident = canonical_hash({'case_id':job['case_id'],'method':job['method'],'track':'A','repeat_index':repeat})
                dispatched = sum(a['job'] == ident and a['status'] != 'reserved' for a in snap['attempts'])
                if len(job['attempts']) > dispatched:
                    removed = len(job['attempts']) - dispatched
                    del job['attempts'][dispatched:]
                    subrun['ledger']['attempts_scripted'] -= removed
                    if not job['attempts'] and job['status'] == 'failed':
                        job['status'] = 'not_run'
        rounds.append(subrun)
        remaining -= subrun['ledger']['attempts_scripted']
        stopped = subrun['round_integrity_failure'] or snap['stopped']
        rows.extend({'case_id':j['case_id'],'method':j['method'],'track':'A','repeat_index':repeat,
                     'status':j['status'],'attempts':len(j['attempts']),'final_present':j['final'] is not None} for j in subrun['jobs'])
        try:
            _write(directory / f'repeat-{repeat:03d}.json', _bytes(subrun))
        except Exception as exc:
            raise SharedIntegrityError('trajectory persistence failed') from exc
    result = {'version':'s4_unified_offline_run_v1','plan_sha256':canonical_hash(plan), 'rows':rows,
              'stop_reason':stopped, 'actual_model_calls':0,'provider_billed_cost':None,'accuracy':None}
    _write(directory / 'result.json', _bytes(result))
    return result, journal


def verify(directory, bundle_root, scripts_path):
    directory, bundle_root, scripts_path = Path(directory), Path(bundle_root), Path(scripts_path)
    plan = json.loads((directory/'plan.json').read_text(encoding='utf-8'))
    result = json.loads((directory/'result.json').read_text(encoding='utf-8'))
    schedule = plan['schedule']['plan']
    if (plan.get('execution_kind') != 'offline_mock' or plan.get('real_transport_allowed') is not False or
        plan.get('actual_model_calls') != 0 or result.get('actual_model_calls') != 0 or result.get('accuracy') is not None or
        result.get('plan_sha256') != canonical_hash(plan) or plan['scripts_sha256'] != digest(scripts_path.read_bytes()) or
        plan['source_binding'] != bindings() or plan['unified_sources'] != unified_sources() or
        build(bundle_root, schedule['cases'], schedule['repetitions']) != plan['schedule']):
        raise SharedIntegrityError('offline plan binding mismatch')
    keys = ('case_id','method','track','repeat_index')
    if [{k:r[k] for k in keys} for r in result['rows']] != schedule['jobs']:
        raise SharedIntegrityError('planned denominator mismatch')
    journal = Journal(directory/'journal.sqlite', plan, plan['policy'])
    snapshot = journal.snapshot()
    if any(r['status'] in {'reserved','dispatch_started','received'} for r in snapshot['attempts']):
        raise SharedIntegrityError('unresolved attempt')
    expected = []
    rows = {(r['case_id'],r['method'],r['repeat_index']):r for r in result['rows']}
    repeats = sorted(directory.glob('repeat-*.json'))
    for number, path in enumerate(repeats, 1):
        if path.name != f'repeat-{number:03d}.json' or path.is_symlink():
            raise SharedIntegrityError('repeat order mismatch')
        subrun = json.loads(path.read_text(encoding='utf-8'))
        if subrun['round_integrity_failure']:
            raise SharedIntegrityError('round integrity failure')
        if ([{'case_id':j['case_id'],'method':j['method'],'track':'A','repeat_index':number} for j in subrun['jobs']] !=
                [j for j in schedule['jobs'] if j['repeat_index'] == number]):
            raise SharedIntegrityError('repeat denominator mismatch')
        for job in subrun['jobs']:
            summary = rows[(job['case_id'],job['method'],number)]
            if (summary['status'] != job['status'] or summary['attempts'] != len(job['attempts']) or
                summary['final_present'] != (job['final'] is not None)):
                raise SharedIntegrityError('job summary mismatch')
            ident = {'case_id':job['case_id'],'method':job['method'],'track':'A','repeat_index':number}
            expected.extend((canonical_hash(ident), a) for a in job['attempts'])
    if any(r['repeat_index'] > len(repeats) and (r['status'] != 'not_run' or r['attempts'] or r['final_present']) for r in result['rows']):
        raise SharedIntegrityError('unexecuted job mismatch')
    if len(expected) != len(snapshot['attempts']):
        raise SharedIntegrityError('attempt count mismatch')
    for (job, attempt), saved in zip(expected, snapshot['attempts']):
        if (saved['job'] != job or saved['stage'] != attempt['stage'] or saved['request'] != mock_wire(attempt['request']) or
            saved['is_continuation'] != int('lookup_response' in attempt['request']['public_input'])):
            raise SharedIntegrityError('attempt binding mismatch')
        if saved['status'] == 'completed':
            body = json.loads(saved['response'])
            if body['choices'][0]['message']['content'] != attempt['response']:
                raise SharedIntegrityError('response binding mismatch')
    return {'status':'offline_journal_verified','planned_jobs':len(result['rows']),
            'attempts':len(expected),'reserved_virtual_micro':snapshot['reserved_virtual_micro'],
            'actual_model_calls':0,'provider_billed_cost':None,'accuracy':None}
