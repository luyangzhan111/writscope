"""Unified checkpointed S4 runtime, synthetic provider only; legacy kept for replay."""
import json
from pathlib import Path

from scripts.s4_candidate_cost_mock_runner import bindings
from scripts.s4_repeat_schedule import build
from scripts.s4_r3_checkpoint_executor_v1 import execute
from scripts.s4_r3_checkpoint_journal_v1 import CheckpointJournal
from scripts.s4_r3_provider_bridge_v1 import MockProviderBridge
from scripts.s4_simulated_artifact_bundle import _bytes, _write
from scripts.s4_unified_offline import unified_sources
from writscope.artifacts import canonical_hash, digest

ROOT = Path(__file__).resolve().parents[1]
FILES = ('scripts/s4_r3_checkpoint_executor_v1.py','scripts/s4_r3_checkpoint_journal_v1.py',
         'scripts/s4_r3_checkpoint_runtime_v1.py','scripts/s4_r3_provider_bridge_v1.py',
         'scripts/s4_r3_provider_mock_v1.py','scripts/s4_r3_checkpoint_gate_v1.py')


def prepare(bundle_root, scripts_path, cases, repetitions, policy):
    if policy.get('max_output_tokens') != 4096:
        raise ValueError('mock output limit mismatch')
    return {'version':'s4_r3_checkpoint_plan_v1','execution_kind':'offline_mock',
            'schedule':build(Path(bundle_root), cases, repetitions),
            'scripts_sha256':digest(Path(scripts_path).read_bytes()),'policy':policy,
            'legacy_sources':bindings(),'unified_sources':unified_sources(),
            'checkpoint_sources':{p:digest((ROOT/p).read_bytes()) for p in FILES},
            'recipient':'mock.invalid','materials':'public_synthetic_only',
            'real_transport_allowed':False,'actual_model_calls':0}


def run(plan, directory, bundle_root, scripts_path):
    directory = Path(directory)
    schedule = plan['schedule']['plan']
    if plan != prepare(bundle_root, scripts_path, schedule['cases'], schedule['repetitions'], plan['policy']):
        raise ValueError('checkpoint plan mismatch')
    if directory.exists() or directory.resolve().is_relative_to(Path(bundle_root).resolve()):
        raise ValueError('invalid output directory')
    directory.mkdir(parents=False, exist_ok=False)
    _write(directory/'plan.json', _bytes(plan))
    journal = CheckpointJournal(directory/'journal.sqlite', plan, plan['policy'], create=True)
    remaining, stopped, rows = plan['policy']['max_attempts'], None, []
    for index, identity in enumerate(schedule['jobs'], 1):
        if not remaining or stopped:
            rows.append({**identity,'status':'not_run','attempts':0,'final_present':False})
            continue
        bridge = MockProviderBridge(journal, identity['repeat_index'])
        def checkpoint(case, method, kind, payload):
            if case != identity['case_id'] or method != identity['method']:
                raise ValueError('checkpoint identity mismatch')
            journal.append_checkpoint(canonical_hash(identity), kind, payload)
        result = execute(Path(bundle_root), Path(scripts_path), [identity['case_id']],
                         [identity['method']], remaining, bridge, checkpoint)
        job = result['jobs'][0]
        snap = journal.snapshot()
        if result['round_integrity_failure']:
            dispatched = sum(a['job'] == canonical_hash(identity) and a['status'] != 'reserved' for a in snap['attempts'])
            if len(job['attempts']) > dispatched:
                removed = len(job['attempts'])-dispatched
                del job['attempts'][dispatched:]
                result['ledger']['attempts_scripted'] -= removed
                if not job['attempts']:
                    job['status'] = 'not_run'
        remaining -= result['ledger']['attempts_scripted']
        stopped = result['round_integrity_failure'] or snap['stopped']
        _write(directory/f'job-{index:06d}.json', _bytes({'identity':identity,'trajectory':job,
                                                          'round_integrity_failure':result['round_integrity_failure']}))
        rows.append({**identity,'status':job['status'],'attempts':len(job['attempts']),
                     'final_present':job['final'] is not None})
    summary = {'version':'s4_r3_checkpoint_result_v1','plan_sha256':canonical_hash(plan),'rows':rows,
               'stop_reason':stopped,'actual_model_calls':0,'provider_billed_cost':None,'accuracy':None}
    _write(directory/'result.json', _bytes(summary))
    return summary


def inspect(directory, bundle_root, scripts_path):
    """Inspect saved evidence; does not independently replay model or state semantics."""
    directory = Path(directory)
    plan = json.loads((directory/'plan.json').read_text(encoding='utf-8'))
    schedule = plan['schedule']['plan']
    if plan != prepare(bundle_root, scripts_path, schedule['cases'], schedule['repetitions'], plan['policy']):
        raise ValueError('checkpoint plan mismatch')
    journal = CheckpointJournal(directory/'journal.sqlite', plan, plan['policy'])
    snapshot, events = journal.snapshot(), journal.checkpoints()
    planned = {canonical_hash(job):job for job in schedule['jobs']}
    if any(e['job'] not in planned for e in events) or any(a['job'] not in planned for a in snapshot['attempts']):
        raise ValueError('unplanned journal identity')
    rows = []
    for job_id, identity in planned.items():
        attempts = [a for a in snapshot['attempts'] if a['job'] == job_id]
        job_events = [e for e in events if e['job'] == job_id]
        open_operations = []
        for operation in ('lookup','transition','assessment','final_validation'):
            starts = sum(e['kind'] == operation+'_started' for e in job_events)
            finishes = sum(e['kind'] == operation+'_finished' for e in job_events)
            if finishes > starts:
                raise ValueError('checkpoint operation order mismatch')
            if starts > finishes:
                open_operations.append(operation)
        pending = any(a['status'] in {'reserved','dispatch_started','received'} for a in attempts)
        finished = [e for e in job_events if e['kind'] == 'job_finished']
        state = ('unresolved' if pending or open_operations else
                 'finished_checkpoint_unverified' if finished else
                 'started_unfinished' if job_events or attempts else 'not_started')
        rows.append({**identity,'recovery_status':state,'open_operations':open_operations,
                     'dispatch_started_or_later':sum(a['status'] != 'reserved' for a in attempts)})
    result_path = directory/'result.json'
    if result_path.exists():
        result = json.loads(result_path.read_text(encoding='utf-8'))
        keys = ('case_id','method','track','repeat_index')
        if result['plan_sha256'] != canonical_hash(plan) or [{k:r[k] for k in keys} for r in result['rows']] != schedule['jobs']:
            raise ValueError('result denominator mismatch')
    return {'planned_jobs':len(planned),'rows':rows,'attempts':len(snapshot['attempts']),'checkpoints':len(events),
            'unresolved_attempts':[a['id'] for a in snapshot['attempts'] if a['status'] in {'reserved','dispatch_started','received'}],
            'checkpoint_kinds':[e['kind'] for e in events], 'actual_model_calls':0,
            'limitation':'checkpoint chain is not independent semantic replay or provenance proof'}
