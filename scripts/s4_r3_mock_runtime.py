"""Per-job persisted offline runtime for the R3 mock gate; never live I/O."""
import json
from pathlib import Path

from scripts.s4_candidate_cost_mock_runner import bindings, execute
from scripts.s4_repeat_schedule import build
from scripts.s4_simulated_artifact_bundle import _bytes, _write
from scripts.s4_unified_journal import Journal
from scripts.s4_unified_offline import MockDispatcher, unified_sources
from scripts.s4_synthetic_cost_wire import mock_wire
from writscope.artifacts import canonical_hash, digest


def run(directory, bundle_root, scripts_path, cases, repetitions, policy):
    directory, bundle_root, scripts_path = Path(directory), Path(bundle_root), Path(scripts_path)
    if directory.exists() or directory.resolve().is_relative_to(bundle_root.resolve()) or policy.get('max_output_tokens') != 4096:
        raise ValueError('invalid mock runtime output or policy')
    schedule = build(bundle_root, cases, repetitions)
    plan = {'version':'s4_r3_mock_runtime_v1','execution_kind':'offline_mock','schedule':schedule,
            'scripts_sha256':digest(scripts_path.read_bytes()),'policy':policy,'source_binding':bindings(),
            'unified_sources':unified_sources(),'runtime_sha256':digest(Path(__file__).read_bytes()),
            'real_transport_allowed':False,'actual_model_calls':0}
    directory.mkdir(parents=False, exist_ok=False)
    _write(directory/'plan.json', _bytes(plan))
    journal = Journal(directory/'journal.sqlite', plan, policy, create=True)
    dispatcher = MockDispatcher(journal)
    rows, remaining, stopped = [], policy['max_attempts'], None
    for index, identity in enumerate(schedule['plan']['jobs'], 1):
        if not remaining or stopped:
            rows.append({**identity,'status':'not_run','attempts':0,'final_present':False})
            continue
        def dispatch(case, method, stage, request, step):
            if case != identity['case_id'] or method != identity['method']:
                raise ValueError('job identity mismatch')
            dispatcher.dispatch(canonical_hash(identity), stage, request, step)
        result = execute(bundle_root, scripts_path, [identity['case_id']], [identity['method']], remaining, dispatch)
        job = result['jobs'][0]
        snapshot = journal.snapshot()
        if result['round_integrity_failure']:
            dispatched = sum(a['job'] == canonical_hash(identity) and a['status'] != 'reserved' for a in snapshot['attempts'])
            if len(job['attempts']) > dispatched:
                removed = len(job['attempts']) - dispatched
                del job['attempts'][dispatched:]
                result['ledger']['attempts_scripted'] -= removed
                if not job['attempts']:
                    job['status'] = 'not_run'
        remaining -= result['ledger']['attempts_scripted']
        stopped = result['round_integrity_failure'] or snapshot['stopped']
        _write(directory/f'job-{index:06d}.json', _bytes({'identity':identity,'trajectory':job,
                                                           'round_integrity_failure':result['round_integrity_failure']}))
        rows.append({**identity,'status':job['status'],'attempts':len(job['attempts']),'final_present':job['final'] is not None})
    summary = {'version':'s4_r3_mock_runtime_result_v1','plan_sha256':canonical_hash(plan),'rows':rows,
               'stop_reason':stopped,'actual_model_calls':0,'provider_billed_cost':None,'accuracy':None}
    _write(directory/'result.json', _bytes(summary))
    return summary, journal


def verify(directory, bundle_root, scripts_path):
    directory = Path(directory)
    plan = json.loads((directory/'plan.json').read_text(encoding='utf-8'))
    result = json.loads((directory/'result.json').read_text(encoding='utf-8'))
    schedule = plan['schedule']['plan']
    if (plan.get('version') != 's4_r3_mock_runtime_v1' or plan.get('real_transport_allowed') is not False or
        plan.get('actual_model_calls') != 0 or result.get('actual_model_calls') != 0 or result.get('accuracy') is not None or
        plan['runtime_sha256'] != digest(Path(__file__).read_bytes()) or plan['source_binding'] != bindings() or
        plan['unified_sources'] != unified_sources() or plan['scripts_sha256'] != digest(Path(scripts_path).read_bytes()) or
        build(Path(bundle_root), schedule['cases'], schedule['repetitions']) != plan['schedule'] or
        result['plan_sha256'] != canonical_hash(plan)):
        raise ValueError('mock runtime binding mismatch')
    keys = ('case_id','method','track','repeat_index')
    if [{k:r[k] for k in keys} for r in result['rows']] != schedule['jobs']:
        raise ValueError('mock runtime denominator mismatch')
    snapshot = Journal(directory/'journal.sqlite', plan, plan['policy']).snapshot()
    if any(a['status'] in {'reserved','dispatch_started','received'} for a in snapshot['attempts']):
        raise ValueError('unresolved mock attempt')
    files = sorted(directory.glob('job-*.json'))
    expected_attempts = []
    for index, path in enumerate(files, 1):
        if path.is_symlink() or path.name != f'job-{index:06d}.json':
            raise ValueError('mock job file mismatch')
        record = json.loads(path.read_text(encoding='utf-8'))
        row = result['rows'][index-1]
        if record['identity'] != schedule['jobs'][index-1] or record['round_integrity_failure']:
            raise ValueError('mock job identity or integrity failure')
        job = record['trajectory']
        if (job['status'] != row['status'] or len(job['attempts']) != row['attempts'] or
                (job['final'] is not None) != row['final_present']):
            raise ValueError('mock job summary mismatch')
        expected_attempts.extend((canonical_hash(record['identity']), attempt) for attempt in job['attempts'])
    if any(row['status'] != 'not_run' or row['attempts'] for row in result['rows'][len(files):]):
        raise ValueError('missing mock job file')
    if len(expected_attempts) != len(snapshot['attempts']):
        raise ValueError('mock attempt count mismatch')
    for (job_id, attempt), saved in zip(expected_attempts, snapshot['attempts']):
        if (saved['job'] != job_id or saved['stage'] != attempt['stage'] or
                saved['request'] != mock_wire(attempt['request']) or
                saved['is_continuation'] != int('lookup_response' in attempt['request']['public_input'])):
            raise ValueError('mock attempt binding mismatch')
        if saved['status'] == 'completed':
            try:
                content = json.loads(saved['response'])['choices'][0]['message']['content']
            except (ValueError, TypeError, KeyError, IndexError):
                raise ValueError('mock response binding mismatch') from None
            if content != attempt['response']:
                raise ValueError('mock response binding mismatch')
    return {'status':'mock_job_records_checked','planned_jobs':len(result['rows']),
            'attempts':len(expected_attempts),'actual_model_calls':0,'accuracy':None}
