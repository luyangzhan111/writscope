"""Read-only owner-pilot adapter to the independent R4 state/terminal replay.

Does not run methods, fixtures, HTTP, approval callbacks or credential readers.
Private references are opened only after the complete public trace reconciles.
Local files and hashes do not establish independent host/provider provenance.
"""
from contextlib import closing
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path

from scripts import s4_owner_pilot as pilot
from scripts import s4_r4_replay as r4
from scripts import s4_r4_evaluator as evaluator
from scripts.s4_accounting_core import aggregate
from scripts.s4_synthetic_cost_wire import mock_wire
from writscope.artifacts import canonical_hash, digest


class Cursor(r4.Cursor):
    def call(self, request):
        if self.ai == len(self.attempts):
            if self.partial:
                raise r4.Incomplete('request_not_durably_recorded')
            raise r4.ReplayError('missing_attempt')
        a = self.attempts[self.ai]
        r4.require(a['request'] == mock_wire(request) and a['stage'] == request['stage'] and
                   a['is_continuation'] == int('lookup_response' in request['public_input']),
                   'request_reconstruction_mismatch')
        self.ai += 1
        for key in ('created_at', 'sent_at', 'received_at'):
            if a[key] is not None:
                self.time(a[key])
        if a['status'] in ('reserved', 'dispatch_started', 'received'):
            r4.require(self.partial, 'pending_attempt_in_settled_run')
            raise r4.Incomplete('unresolved_'+a['status'])
        # Journal has already independently derived all usage and finish/error
        # fields from the captured body and receipt. Request is reconstructed here.
        usage = json.loads(a['usage'])
        with closing(self.journal.connect()) as db:
            receipt = json.loads(db.execute('SELECT value FROM receipt WHERE attempt=?', (a['id'],)).fetchone()[0])
        payload, finish, error, checked = pilot.interpret(a['response'], receipt, request, self.plan)
        r4.require(checked == usage and finish == a['finish_reason'] and error == a['error_kind'], 'response_derivation_mismatch')
        previous = self.before+self.attempts[:self.ai]
        service = ('timeout', 'connection_error', 'service_http_error')
        if usage['stop_round'] or len(previous) >= 3 and all(a['error_kind'] in service for a in previous[-3:]):
            r4.require(self.partial, 'round_fault_not_stopped')
            raise r4.Incomplete('round_stop:'+str(error))
        if error:
            raise ValueError(error)
        return payload


def artifacts(directory):
    paths = [Path(directory)/name for name in ('plan.json', 'journal.sqlite', 'origin.json')]
    paths += sorted(Path(directory).glob('job-*.json'))
    if (Path(directory)/'result.json').exists():
        paths.append(Path(directory)/'result.json')
    r4.require(not list(Path(directory).glob('journal.sqlite-*')), 'journal_not_closed')
    for p in paths:
        pilot.unlinked(p)
    return {p.name: digest(p.read_bytes()) for p in paths}


def verify(directory, bundles=pilot.PUBLIC):
    directory = Path(directory)
    hashes = artifacts(directory)
    plan, origin = pilot.read(directory/'plan.json'), pilot.read(directory/'origin.json')
    pilot.check_plan(plan, bundles)
    mode = origin['mode']
    r4.require(origin['plan_sha256'] == canonical_hash(plan) and mode in ('fixed', 'live') and
               origin['independent_provenance_attestation'] is False, 'origin_binding')
    if mode == 'fixed':
        r4.require(origin['origin'] == 'fixed_test_no_model_calls' and origin['launch'] is None, 'fixed_origin_mismatch')
    else:
        launch = origin['launch']
        r4.require(origin['origin'] == 'owner_console_live_http_not_host_attested' and
                   launch['plan_sha256'] == canonical_hash(plan) and
                   launch['approval_record_sha256'] == canonical_hash(launch['approval']) and
                   launch['source'] == 'owner_local_console_not_host_attested', 'owner_launch_binding')
        pilot.checked_price(plan['price'], datetime.fromisoformat(launch['confirmed_at']))
    journal = pilot.ReadOnlyJournal(directory/'journal.sqlite', plan, mode)
    snap, events = journal.snapshot(), journal.checkpoints()
    jobs = plan['schedule']['jobs']; ids = [canonical_hash(j) for j in jobs]
    for group in (snap['attempts'], events):
        order = [ids.index(e['job']) if e['job'] in ids else -1 for e in group]
        r4.require(-1 not in order and order == sorted(order), 'unplanned_or_reordered_work')
    result = pilot.read(directory/'result.json') if 'result.json' in hashes else None
    partial = result is None or result['stop_reason'] is not None
    if result is not None:
        r4.require(result['version'] == 's4_owner_pilot_result_v1' and result['plan_sha256'] == canonical_hash(plan) and
                   result['mode'] == mode and result['actual_model_calls'] == (0 if mode == 'fixed' else None) and
                   result['accuracy'] is None and result['provider_billed_cost'] is None and
                   [{k: row[k] for k in jobs[0]} for row in result['rows']] == jobs, 'summary_identity_or_origin')
    rows, costs, before, halted, last_time = [], [], [], False, None
    for i, (identity, job_id) in enumerate(zip(jobs, ids), 1):
        attempts = [a for a in snap['attempts'] if a['job'] == job_id]
        own = [e for e in events if e['job'] == job_id]
        path = directory/f'job-{i:06d}.json'
        record = pilot.read(path) if path.exists() else None
        r4.require(not halted or not (attempts or own or record), 'work_after_round_stop')
        if not attempts and not own and record is None:
            r4.require(partial or halted, 'missing_planned_job')
            row = {**identity, 'status': 'not_run', 'prediction': None, 'failure': None, 'incomplete': 'not_started',
                   'diagnostic': None, 'sent_or_uncertain': 0, 'unsent_reservations': 0}
            halted = True
        else:
            cursor = Cursor(identity, Path(bundles)/pilot.CASE, plan, attempts, own, before, None, partial, last_time)
            cursor.journal = journal
            row = cursor.replay()
            last_time = cursor.last_time
            if record is not None:
                job = record['trajectory']
                r4.require(record['identity'] == identity and job['case_id'] == identity['case_id'] and
                           job['method'] == identity['method'], 'saved_identity_mismatch')
                if row['incomplete'] is None:
                    r4.require(job['status'] == row['status'] and job['final'] == cursor.final and
                        job['failure'] == row['failure'] and job['transitions'] == cursor.transitions and
                        job['assessment'] == cursor.assessment and job['lookups'] == cursor.lookup_records,
                        'saved_job_differs_from_reconstructed_responses')
                elif record['round_integrity_failure'] is None:
                    r4.require(result is None, 'unexplained_incomplete_job')
            else:
                r4.require(partial, 'missing_executed_job_file')
                row.update(incomplete=row['incomplete'] or 'missing_job_file', prediction=None,
                           status='failed' if row['sent_or_uncertain'] else 'not_run')
            halted = row['incomplete'] is not None
        if result is not None:
            saved = result['rows'][i-1]
            r4.require(saved['attempts'] in (None, row['sent_or_uncertain']) and
                       (row['incomplete'] is not None or saved['status'] == row['status']) and
                       saved['final_present'] == (row['prediction'] is not None), 'summary_result_mismatch')
        entries = r4.cost_entries(attempts, own)
        for entry in entries:
            if entry['entry_id'].startswith('attempt/'):
                a = next(a for a in attempts if a['id'] == int(entry['entry_id'].split('/')[1]))
                usage = json.loads(a['usage']) if a['usage'] else {}
                entry['estimated_cost_cny'] = usage.get('estimated_cost_cny')
                entry['usage_source'] = usage.get('usage_source', 'unknown')
            entry['entry_id'] = job_id+'/'+entry['entry_id']
        costs.extend(entries)
        row['cost'] = aggregate(entries)
        rows.append(row); before += attempts
    r4.require(len(list(directory.glob('job-*.json'))) <= len(jobs), 'extra_job_file')
    if result is not None:
        r4.require(partial == halted, 'unsubstantiated_or_unrecorded_round_stop')
    wall = result.get('round_elapsed_ms') if result else None
    known_elapsed = sum(e['elapsed_ms'] for e in costs if e.get('elapsed_ms') is not None)
    # The remainder covers request preparation, source/isolation checks, DB work,
    # checkpoints and output writes. Unknown after a process crash stays unknown.
    overhead = max(0, wall-known_elapsed) if wall is not None else None
    r4.require(wall is None or type(wall) in (float, int) and wall >= known_elapsed, 'local_wall_time_inconsistent')
    costs.append({'entry_id': 'round/local_overhead', 'stage': 'local_validation', 'elapsed_ms': overhead})
    settled = not partial
    r4.require(artifacts(directory) == hashes, 'records_changed_during_replay')
    return {'version': 's4_owner_pilot_replay_v1', 'status': 'verified_settled' if settled else 'verified_prefix_only',
        'mode': mode, 'provenance': 'fixed_test' if mode == 'fixed' else 'owner_local_record_not_independently_attested',
        'plan_sha256': canonical_hash(plan), 'artifact_sha256': hashes, 'planned_jobs': 3, 'rows': rows,
        'cost': aggregate(costs), 'reserved_cny_micro': snap['reserved_cny_micro'],
        'sent_or_uncertain': sum(r['sent_or_uncertain'] for r in rows), 'provider_billed_cost': None,
        'shared_evidence_creation_elapsed_ms': None, 'unknown_shared_evidence_creation_cost': True,
        'actual_model_calls': 0 if mode == 'fixed' else None, 'model_metrics': None, 'accuracy': None}


def evaluate(directory, bundles=pilot.PUBLIC):
    replay = verify(directory, bundles)
    result = {'version': 's4_owner_pilot_evaluation_v1', 'replay': replay, 'reference_read': False,
              'accuracy': None, 'model_metrics': None, 'author_draft_diagnostic_counts': None,
              'real_execution_approved_by_this_report': False}
    if replay['status'] != 'verified_settled':
        return result
    binding, private = evaluator.load_reference_package(evaluator.DEFAULT_BINDING)
    audit = next(a for a in private['audit']['cases'] if a['case_id'] == pilot.CASE)
    r4.require(pilot.checked_bundle(Path(bundles)/pilot.CASE).sha256 == audit['bundle_sha256'], 'reference_public_bundle_mismatch')
    expected = private['reference']['cases'][pilot.CASE]['expected']
    result.update(reference_read=True, reference_status='provisional_author_draft_not_frozen',
        independent_review='not_performed_skipped_by_user', user_case_by_case_review='not_received',
        comparison='equal_configuration_development_diagnostic', budget_matched='deferred',
        private_sha256=binding['sha256'],
        author_draft_diagnostic_counts=evaluator.aggregate(replay['rows'], {pilot.CASE: {'expected': expected, 'disputed': False}}, {}),
        scope='fixed_pipeline_check' if replay['mode'] == 'fixed' else 'owner_supervised_single_case_diagnostic_not_ranking')
    r4.require(artifacts(directory) == replay['artifact_sha256'], 'records_changed_during_evaluation')
    return result
