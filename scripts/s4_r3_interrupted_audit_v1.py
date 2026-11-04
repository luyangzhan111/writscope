"""Read-only inventory of synthetic failed or interrupted checkpoint runs."""
import json
from pathlib import Path

from scripts.s4_r3_checkpoint_audit_v1 import ReadOnlyCheckpointJournal, _read
from scripts.s4_r3_checkpoint_runtime_v1 import prepare
from scripts.s4_synthetic_cost_wire import mock_wire
from writscope.artifacts import canonical_hash


def inspect(directory, bundle_root, scripts_path):
    """Inspect known persisted evidence without reconstructing unsaved operations."""
    directory = Path(directory)
    plan = _read(directory/'plan.json')
    schedule = plan['schedule']['plan']
    if plan != prepare(bundle_root, scripts_path, schedule['cases'], schedule['repetitions'], plan['policy']):
        raise ValueError('plan binding mismatch')
    ledger = directory/'journal.sqlite'
    if ledger.is_symlink() or not ledger.is_file():
        raise ValueError('missing or linked journal')
    journal = ReadOnlyCheckpointJournal(ledger, plan, plan['policy'])
    snapshot, events = journal.snapshot(), journal.checkpoints()
    jobs = schedule['jobs']
    hashes = [canonical_hash(j) for j in jobs]
    if len(set(hashes)) != len(jobs):
        raise ValueError('duplicate planned identity')
    for records in (snapshot['attempts'],events):
        order = [hashes.index(r['job']) if r['job'] in hashes else -1 for r in records]
        if -1 in order or order != sorted(order):
            raise ValueError('unplanned or out-of-order journal entry')
    result_path = directory/'result.json'
    summary = _read(result_path) if result_path.exists() else None
    if summary is not None:
        keys = ('case_id','method','track','repeat_index')
        if (summary['plan_sha256'] != canonical_hash(plan) or summary['actual_model_calls'] != 0 or
                [{k:r[k] for k in keys} for r in summary['rows']] != jobs):
            raise ValueError('summary binding or denominator mismatch')
    paths = sorted(directory.glob('job-*.json'))
    if any(i > len(jobs) or p.name != f'job-{i:06d}.json' for i,p in enumerate(paths,1)):
        raise ValueError('job artifact sequence mismatch')
    rows = []
    for index,(job,job_hash) in enumerate(zip(jobs,hashes),1):
        attempts = [a for a in snapshot['attempts'] if a['job']==job_hash]
        own_events = [e for e in events if e['job']==job_hash]
        opened = None
        for event in own_events:
            kind = event['kind']
            if kind.endswith('_started') and kind != 'job_started':
                if opened is not None:
                    raise ValueError('overlapping local operations')
                opened = kind.removesuffix('_started')
            elif kind.endswith('_finished') and kind != 'job_finished':
                if opened != kind.removesuffix('_finished'):
                    raise ValueError('local operation order mismatch')
                opened = None
            elif kind not in ('job_started','job_finished'):
                raise ValueError('unknown checkpoint kind')
        record = _read(paths[index-1]) if index <= len(paths) else None
        if record is not None:
            trajectory = record['trajectory']
            if (record['identity'] != job or trajectory['case_id'] != job['case_id'] or
                    trajectory['method'] != job['method']):
                raise ValueError('job identity mismatch')
            sent = [a for a in attempts if a['status'] != 'reserved']
            if len(trajectory['attempts']) != len(sent):
                raise ValueError('job attempt count mismatch')
            for saved, attempt in zip(sent,trajectory['attempts']):
                if (saved['stage'] != attempt['stage'] or saved['request'] != mock_wire(attempt['request']) or
                        saved['is_continuation'] != int('lookup_response' in attempt['request']['public_input'])):
                    raise ValueError('job request mismatch')
            if summary is not None:
                row = summary['rows'][index-1]
                if (row['status'] != trajectory['status'] or row['attempts'] != len(sent) or
                        row['final_present'] != (trajectory['final'] is not None)):
                    raise ValueError('job summary mismatch')
        elif summary is not None:
            row = summary['rows'][index-1]
            if row['status'] != 'not_run' or row['attempts'] != 0 or row['final_present']:
                raise ValueError('missing nonempty job artifact')
        pending = [a['id'] for a in attempts if a['status'] in ('reserved','dispatch_started','received')]
        if pending or opened:
            status = 'unresolved'
        elif record is not None:
            status = 'job_file_present_unverified'
        elif attempts or own_events:
            status = 'started_without_job_file'
        else:
            status = 'no_persisted_activity'
        rows.append({**job,'evidence_status':status,'open_local_operation':opened,
                     'unresolved_attempt_ids':pending,'reserved_not_dispatched':sum(a['status']=='reserved' for a in attempts),
                     'dispatch_started_or_later':sum(a['status']!='reserved' for a in attempts),
                     'known_response_count':sum(a['response'] is not None for a in attempts),
                     'saved_job_status_unverified':record['trajectory']['status'] if record else None})
    summary_stop = summary['stop_reason'] if summary else None
    reason_conflict = bool(snapshot['stopped'] and summary_stop and snapshot['stopped'] != summary_stop)
    return {'version':'s4_r3_interrupted_audit_v1','planned_jobs':len(jobs),'rows':rows,
            'journal_stop':snapshot['stopped'],'summary_stop_unverified':summary['stop_reason'] if summary else None,
            'stop_reason_disagreement':reason_conflict,
            'reserved_virtual_micro':snapshot['reserved_virtual_micro'],'actual_model_calls':0,
            'provider_billed_cost':None,'accuracy':None,
            'limitations':['partial evidence only','no semantic replay or provider source proof',
                           'unknown is not zero','virtual reservation is not money']}
