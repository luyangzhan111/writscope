"""Read-only consistency audit of completed synthetic checkpoint runs, not scoring."""
import json
import sqlite3
from pathlib import Path

from scripts.s4_r3_checkpoint_journal_v1 import CheckpointJournal
from scripts.s4_r3_checkpoint_runtime_v1 import prepare
from scripts.s4_synthetic_cost_wire import mock_wire
from writscope.artifacts import canonical_hash


class ReadOnlyCheckpointJournal(CheckpointJournal):
    def connect(self):
        return sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True, timeout=10, isolation_level=None)


def _read(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('missing or linked audit artifact')
    return json.loads(path.read_text(encoding='utf-8'))


def verify(directory, bundle_root, scripts_path):
    """Verify completed mock records against each other, without replay or scoring."""
    directory = Path(directory)
    plan = _read(directory/'plan.json')
    summary = _read(directory/'result.json')
    schedule = plan['schedule']['plan']
    if (plan != prepare(bundle_root,scripts_path,schedule['cases'],schedule['repetitions'],plan['policy']) or
            summary['plan_sha256'] != canonical_hash(plan) or summary['actual_model_calls'] != 0 or
            summary['accuracy'] is not None or summary['provider_billed_cost'] is not None or summary['stop_reason']):
        raise ValueError('plan or completed result binding mismatch')
    keys = ('case_id','method','track','repeat_index')
    jobs = schedule['jobs']
    if [{k:row[k] for k in keys} for row in summary['rows']] != jobs:
        raise ValueError('planned denominator mismatch')
    ledger_path = directory/'journal.sqlite'
    if ledger_path.is_symlink() or not ledger_path.is_file():
        raise ValueError('missing or linked journal')
    journal = ReadOnlyCheckpointJournal(ledger_path,plan,plan['policy'])
    snapshot, events = journal.snapshot(), journal.checkpoints()
    if snapshot['stopped'] or any(a['status'] != 'completed' for a in snapshot['attempts']):
        raise ValueError('unfinished or failed attempt')
    attempts_seen = 0
    ordered_attempt_ids = []
    expected_hashes = [canonical_hash(j) for j in jobs]
    event_job_indices = [expected_hashes.index(e['job']) if e['job'] in expected_hashes else -1 for e in events]
    if -1 in event_job_indices or event_job_indices != sorted(event_job_indices):
        raise ValueError('checkpoint job order mismatch')
    for index, (identity, row) in enumerate(zip(jobs, summary['rows']),1):
        record = _read(directory/f'job-{index:06d}.json')
        job = record['trajectory']
        if (record['identity'] != identity or record['round_integrity_failure'] is not None or
                job['case_id'] != identity['case_id'] or job['method'] != identity['method'] or
                job['status'] != 'completed' or row['status'] != 'completed' or
                row['attempts'] != len(job['attempts']) or not row['final_present'] or job['final'] is None):
            raise ValueError('job summary mismatch')
        job_hash = canonical_hash(identity)
        own_attempts = [a for a in snapshot['attempts'] if a['job'] == job_hash]
        if len(own_attempts) != len(job['attempts']):
            raise ValueError('job attempt count mismatch')
        for saved, attempt in zip(own_attempts,job['attempts']):
            if (saved['stage'] != attempt['stage'] or saved['request'] != mock_wire(attempt['request']) or
                    saved['is_continuation'] != int('lookup_response' in attempt['request']['public_input']) or
                    json.loads(saved['response'])['choices'][0]['message']['content'] != attempt['response']):
                raise ValueError('request or response mismatch')
        attempts_seen += len(own_attempts)
        ordered_attempt_ids.extend(a['id'] for a in own_attempts)
        own_events = [e for e in events if e['job'] == job_hash]
        if not own_events or own_events[0]['kind'] != 'job_started' or own_events[-1]['kind'] != 'job_finished':
            raise ValueError('incomplete job checkpoints')
        if json.loads(own_events[-1]['payload']) != {'status':'completed'}:
            raise ValueError('job completion mismatch')
        transitions, lookups, assessments, finals = [], [], [], []
        open_kind = None
        for event in own_events[1:-1]:
            kind = event['kind']
            if kind.endswith('_started'):
                if open_kind is not None:
                    raise ValueError('overlapping checkpoint operations')
                open_kind = kind.removesuffix('_started')
            elif kind.endswith('_finished'):
                if open_kind != kind.removesuffix('_finished'):
                    raise ValueError('checkpoint operation order mismatch')
                payload = json.loads(event['payload'])
                if type(payload.get('elapsed_ms')) not in (int,float) or payload['elapsed_ms'] < 0:
                    raise ValueError('checkpoint elapsed time invalid')
                if open_kind == 'transition': transitions.append(payload['transition'])
                elif open_kind == 'lookup': lookups.append(payload['lookup'])
                elif open_kind == 'assessment': assessments.append(payload['assessment'])
                elif open_kind == 'final_validation': finals.append(payload['final'])
                else: raise ValueError('unknown checkpoint operation')
                open_kind = None
            else:
                raise ValueError('unexpected checkpoint kind')
        if (open_kind is not None or transitions != job['transitions'] or lookups != job['lookups'] or
                assessments != ([job['assessment']] if job['assessment'] is not None else []) or finals != [job['final']]):
            raise ValueError('checkpoint trajectory mismatch')
    if attempts_seen != len(snapshot['attempts']) or ordered_attempt_ids != [a['id'] for a in snapshot['attempts']]:
        raise ValueError('unplanned journal entry')
    if len(list(directory.glob('job-*.json'))) != len(jobs):
        raise ValueError('extra job artifact')
    return {'status':'synthetic_records_consistent_not_replayed','planned_jobs':len(jobs),
            'attempts':attempts_seen,'checkpoints':len(events),'actual_model_calls':0,
            'provider_billed_cost':None,'accuracy':None}
