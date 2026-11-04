"""Read-only recovery and synthetic cost accounting for incomplete mock runs.

This is not a state-machine replay, execution provenance proof, or real scorer.
"""
import json
import sqlite3
from pathlib import Path

from scripts.s4_accounting_core import aggregate
from scripts.s4_candidate_cost_mock_runner import bindings
from scripts.s4_shared_transport_offline import SharedIntegrityError
from scripts.s4_unified_journal import Journal
from scripts.s4_unified_offline import unified_sources
from writscope.artifacts import canonical_hash


class ReadOnlyJournal(Journal):
    def connect(self):
        return sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10, isolation_level=None)


def inspect(directory):
    directory = Path(directory)
    plan_path = directory / 'plan.json'
    ledger_path = directory / 'journal.sqlite'
    if any(p.is_symlink() or not p.is_file() for p in (plan_path, ledger_path)):
        raise SharedIntegrityError('missing or linked recovery artifact')
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    schedule = plan['schedule']['plan']
    jobs = schedule['jobs']
    identities = [(j['case_id'], j['method'], j['track'], j['repeat_index']) for j in jobs]
    cases, methods, repeats = schedule['cases'], schedule['methods'], schedule['repetitions']
    if (not isinstance(cases, list) or not cases or len(cases) != len(set(cases)) or
            methods != ['full_direct', 'full_checklist', 'incremental'] or
            type(repeats) is not int or repeats < 1 or schedule['track'] != 'A'):
        raise SharedIntegrityError('invalid recovery schedule')
    expected = [(case, method, 'A', repeat) for repeat in range(1, repeats + 1)
                for case in cases for method in methods]
    if (plan.get('version') != 's4_unified_offline_plan_v1' or plan.get('execution_kind') != 'offline_mock' or
            plan.get('real_transport_allowed') is not False or plan.get('actual_model_calls') != 0 or
            plan['schedule']['plan_sha256'] != canonical_hash(schedule) or
            plan['source_binding'] != bindings() or plan['unified_sources'] != unified_sources() or
            not identities or identities != expected or len(identities) != len(set(identities))):
        raise SharedIntegrityError('recovery plan binding mismatch')
    lookup = {canonical_hash(j): j for j in jobs}
    if len(lookup) != len(jobs):
        raise SharedIntegrityError('recovery identity collision')
    snapshot = ReadOnlyJournal(ledger_path, plan, plan['policy']).snapshot()
    by_job = {key: [] for key in lookup}
    entries = []
    for attempt in snapshot['attempts']:
        if attempt['job'] not in by_job:
            raise SharedIntegrityError('unplanned journal attempt')
        by_job[attempt['job']].append(attempt)
        if attempt['status'] == 'reserved':
            continue
        usage = json.loads(attempt['usage']) if attempt['usage'] else {}
        elapsed = usage.get('provider_elapsed_ms', usage.get('elapsed_ms'))
        stage = ('lookup_continuation' if attempt['is_continuation'] else
                 'extract' if attempt['stage'] == 'incremental_extract' else 'final')
        entries.append({'entry_id': str(attempt['id']), 'stage': stage, 'elapsed_ms': elapsed,
                        'input_tokens': usage.get('input_tokens'), 'output_tokens': usage.get('output_tokens'),
                        'cache_hit_input_tokens': usage.get('cache_hit_input_tokens'),
                        'cache_miss_input_tokens': usage.get('cache_miss_input_tokens'),
                        'reasoning_tokens': usage.get('reasoning_tokens'), 'billed_cost_cny': None,
                        'estimated_cost_cny': None})
    # Repeat files are useful recovery evidence but are not individually bound
    # by the old entry. Never promote their self-reported status to verified.
    repeat_files = sorted(directory.glob('repeat-*.json'))
    if any(p.is_symlink() for p in repeat_files):
        raise SharedIntegrityError('linked repeat artifact')
    repeat_names = {p.name for p in repeat_files}
    rows = []
    for identity, job in zip(identities, jobs):
        attempts = by_job[canonical_hash(job)]
        statuses = [a['status'] for a in attempts]
        repeat_saved = f"repeat-{identity[3]:03d}.json" in repeat_names
        if any(s in {'reserved', 'dispatch_started', 'received'} for s in statuses):
            state = 'unresolved'
        elif repeat_saved:
            state = 'repeat_file_present_unverified'
        elif attempts:
            state = 'attempts_recorded_trajectory_unknown'
        else:
            state = 'no_journal_attempt_trajectory_unknown'
        rows.append({**job, 'recovery_status': state, 'journal_attempts': len(attempts),
                     'dispatch_started_or_later': sum(s != 'reserved' for s in statuses)})
    return {'version':'s4_unified_recovery_v1', 'execution_kind':'offline_mock',
            'plan_sha256':canonical_hash(plan), 'planned_jobs':len(jobs), 'rows':rows,
            'reserved_virtual_micro':snapshot['reserved_virtual_micro'], 'round_stop':snapshot['stopped'],
            'unresolved_attempt_ids':[a['id'] for a in snapshot['attempts'] if a['status'] in {'reserved','dispatch_started','received'}],
            'cost':aggregate(entries), 'local_update_elapsed_ms':None, 'local_validation_elapsed_ms':None,
            'shared_evidence_elapsed_ms':None, 'provider_billed_cost':None, 'actual_model_calls':0,
            'limitations':['repeat files unverified', 'state transitions not replayed', 'virtual reservation is not currency']}
