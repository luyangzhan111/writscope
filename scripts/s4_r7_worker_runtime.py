"""Maintained worker/response entry. All live paths remain closed.

Uses the new response executor once; retains old R3 result *format* so the
independent R4 engine can replay without running provider fixtures or methods.
"""
from contextlib import closing
from functools import wraps
import json
import math
from pathlib import Path
import sqlite3
import threading
from unittest.mock import patch

from scripts import s4_r7_http_worker as worker, s4_r7_r4_adapter as old
from scripts.s4_r7_response_adapter import interpret as old_interpret, round_stop, SOURCE as OLD_SOURCE
from scripts.s4_r7_response_executor import execute_job
from scripts.s4_synthetic_cost_wire import mock_wire, SyntheticBudgetStop
from scripts.s4_shared_transport_offline import SharedIntegrityError
from scripts.s4_simulated_artifact_bundle import _bytes, _write
from scripts.s4_unified_journal import now
from scripts.s4_r5_acceptance import offline_guard
from writscope.artifacts import digest, canonical_hash

ROOT = Path(__file__).resolve().parents[1]
OLD_CHECK = old.check_plan
OLD_READONLY = old.ReadOnlyCnyJournal
ENTRY_LOCK = threading.Lock()


def single_entry(function):
    @wraps(function)
    def call(*args, **kwargs):
        if not ENTRY_LOCK.acquire(blocking=False):
            raise RuntimeError('single-concurrency worker entry already active')
        try:
            return function(*args, **kwargs)
        finally:
            ENTRY_LOCK.release()
    return call


def sources():
    return {p: digest((ROOT/p).read_bytes()) for p in ('scripts/s4_r7_http_worker.py',
            'scripts/s4_r7_response_executor.py', 'scripts/s4_r7_worker_runtime.py', 'scripts/s4_r7_process_guard.py',
            'scripts/s4_r7_worker_cli.py')}


def prepare(bundles, scripts, fixtures, cases, repeats, policy, *, deadline=90):
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or not 0 < deadline <= 90:
        raise ValueError('invalid worker deadline')
    return {**old.prepare(bundles, scripts, cases, repeats, policy, price_valid_until='2026-11-03T00:00:00+00:00'),
            'http_worker': {'version': 's4_response_worker_v1', 'source_sha256': sources(),
                            'fixture_sha256': digest(Path(fixtures).read_bytes()), 'deadline_seconds': deadline,
                            'backend': 'fixed_bytes_no_socket_no_cryptographic_tls', 'real_sending': False}}


def check_plan(plan, bundles, scripts):
    base = dict(plan)
    ext = base.pop('http_worker', None)
    if (not isinstance(ext, dict) or set(ext) != {'version', 'source_sha256', 'fixture_sha256', 'deadline_seconds', 'backend', 'real_sending'} or
            ext['version'] != 's4_response_worker_v1' or ext['source_sha256'] != sources() or
            ext['backend'] != 'fixed_bytes_no_socket_no_cryptographic_tls' or ext['real_sending'] is not False or
            type(ext['deadline_seconds']) not in (int, float) or not 0 < ext['deadline_seconds'] <= 90 or
            not isinstance(ext['fixture_sha256'], str) or len(ext['fixture_sha256']) != 64):
        raise SharedIntegrityError('worker version/boundary binding mismatch')
    OLD_CHECK(base, bundles, scripts)


def validate_receipt(raw, receipt, plan):
    info = receipt.get('worker', {})
    if (receipt.get('source') != worker.SOURCE or info.get('version') != 's4_http_process_receipt_v1' or
            info.get('backend') != 'fixed_bytes_no_socket_no_cryptographic_tls' or
            info.get('parent_death_guard') != 'windows_job_kill_on_close' or
            info.get('child_reaped') is not True or info.get('reader_stopped') is not True or info.get('writer_stopped') is not True or
            type(info.get('pid')) is not int or info['pid'] <= 0 or type(info.get('exit_code')) is not int or
            info.get('deadline_seconds') != plan['http_worker']['deadline_seconds'] or
            type(info.get('cleanup_elapsed_ms')) not in (float, int) or not math.isfinite(info['cleanup_elapsed_ms']) or info['cleanup_elapsed_ms'] < 0 or
            receipt.get('response_sha256') != digest(raw) or receipt.get('captured_bytes') != len(raw)):
        raise SharedIntegrityError('worker receipt integrity failure')
    failure = info.get('failure')
    if failure not in (None, 'parent_total_deadline', 'response_size_limit', 'worker_protocol_failure', 'worker_exit_without_done', 'worker_exit_nonzero'):
        raise SharedIntegrityError('unknown worker failure')
    if receipt.get('complete_response') and (info['exit_code'] != 0 or failure is not None):
        raise SharedIntegrityError('incomplete worker claimed success')
    proof = info.get('request_proof')
    if type(info.get('dispatch_permitted')) is not bool or (not info['dispatch_permitted'] and
            (proof is not None or receipt.get('http_status') is not None or raw or receipt.get('complete_response'))):
        raise SharedIntegrityError('unsent worker contains response or request proof')
    expected = {'request_sha256': receipt.get('request_sha256'), 'tls_server_hostname': 'api.deepseek.com',
                'request_line': 'POST /chat/completions HTTP/1.1', 'authorization_present': False}
    if proof is not None and proof != expected or receipt.get('http_status') is not None and proof != expected:
        raise SharedIntegrityError('worker request proof mismatch')
    if failure == 'parent_total_deadline' and (receipt.get('transport_error') != 'timeout' or
            receipt.get('elapsed_ms', 0) < info['deadline_seconds']*1000 or receipt.get('complete_response')):
        raise SharedIntegrityError('unsubstantiated total deadline')
    if failure == 'response_size_limit' and (len(raw) != 100001 or receipt.get('transport_error') != 'response_size_exceeded'):
        raise SharedIntegrityError('unsubstantiated size limit')


def interpret(raw, receipt, request, input_bound):
    # Validate the worker separately; reuse the exact pinned HTTP/output/usage parser.
    plan = {'http_worker': {'deadline_seconds': receipt.get('worker', {}).get('deadline_seconds')}}
    validate_receipt(raw, receipt, plan)
    base = dict(receipt); base.pop('worker'); base['source'] = OLD_SOURCE
    payload, finish, error, usage = old_interpret(raw, base, request, input_bound)
    usage['transport_receipt'] = receipt
    usage['provider_elapsed_ms'] = receipt['elapsed_ms'] + receipt['worker']['cleanup_elapsed_ms']
    return payload, finish, error, usage


class Journal(old.ReceivedJournal):
    def __init__(self, path, plan, policy, create=False):
        self._worker_ready = False
        super().__init__(path, plan, policy, create)
        if create:
            with closing(self.connect()) as db:
                db.execute('CREATE TABLE worker_start_failure (attempt INTEGER PRIMARY KEY, receipt TEXT NOT NULL)')
        self._worker_ready = True
        self.snapshot()

    def save_unsent(self, attempt, receipt):
        if receipt['worker']['dispatch_permitted']:
            raise SharedIntegrityError('sent process cannot be recorded as unsent')
        with closing(self.connect()) as db:
            changed = db.execute('INSERT INTO worker_start_failure SELECT ?,? WHERE EXISTS (SELECT 1 FROM attempt WHERE id=? AND status=?)',
                       (attempt, json.dumps(receipt), attempt, 'reserved'))
            if changed.rowcount != 1:
                raise SharedIntegrityError('unsent worker status mismatch')

    def snapshot(self):
        snap = super().snapshot()
        if self._receipt_ready:
            with closing(self.connect()) as db:
                receipts = dict(db.execute('SELECT attempt,receipt FROM transport_receipt'))
            for attempt in snap['attempts']:
                if attempt['id'] in receipts:
                    validate_receipt(attempt['response'], json.loads(receipts[attempt['id']]), self.plan)
        if self._worker_ready:
            with closing(self.connect()) as db:
                starts = db.execute('SELECT attempt,receipt FROM worker_start_failure').fetchall()
            attempts = {a['id']: a for a in snap['attempts']}
            for index, raw in starts:
                receipt = json.loads(raw)
                if (index not in attempts or attempts[index]['status'] != 'reserved' or
                        receipt['request_sha256'] != attempts[index]['request_hash'] or receipt['worker']['dispatch_permitted']):
                    raise SharedIntegrityError('unsent worker binding mismatch')
                validate_receipt(b'', receipt, self.plan)
        return snap


class ReadOnlyJournal(Journal):
    def connect(self):
        return sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True, isolation_level=None)


def dispatch(journal, identity, request, fixtures, position, before_send=None):
    reason = round_stop(journal.snapshot()['attempts'])
    if reason:
        raise SharedIntegrityError(reason)
    wire = mock_wire(request)
    journal.admit_test_bound(wire, 32768)
    attempt = journal.reserve(canonical_hash(identity), request['stage'], wire, 'lookup_response' in request['public_input'])
    try:
        def permit():
            if sources() != journal.plan['http_worker']['source_sha256'] or digest(Path(fixtures).read_bytes()) != journal.plan['http_worker']['fixture_sha256']:
                raise SharedIntegrityError('pre-dispatch worker source drift')
            if before_send is not None:
                before_send()
            journal.transition(attempt, 'reserved', 'dispatch_started', sent_at=now())
        raw, receipt = worker.receive(wire, fixtures, journal.plan['http_worker']['fixture_sha256'],
                    identity['case_id']+'/'+identity['method'], position, deadline=journal.plan['http_worker']['deadline_seconds'], on_send=permit)
        if not receipt['worker']['dispatch_permitted']:
            journal.save_unsent(attempt, receipt)
            raise SharedIntegrityError('worker_start_failed_unsent_reservation_retained')
        journal.persist_received(attempt, raw, receipt)
        validate_receipt(raw, receipt, journal.plan)
        payload, finish, error, usage = interpret(raw, receipt, request, 32768)
        usage['token_admission'] = journal._admissions[digest(wire)]
        journal.transition(attempt, 'received', 'failed' if error else 'completed', error_kind=error,
                           finish_reason=finish, validation='rejected' if error else 'accepted', usage=json.dumps(usage))
    except Exception as exc:
        raise SharedIntegrityError('worker transport/persistence failure') from exc
    if receipt['worker']['failure'] in ('worker_protocol_failure', 'worker_exit_without_done', 'worker_exit_nonzero'):
        raise SharedIntegrityError('worker_source_or_protocol_failure')
    reason = round_stop(journal.snapshot()['attempts'])
    if reason:
        raise SharedIntegrityError(reason)
    if error:
        raise ValueError(error)
    return payload


@single_entry
def run(plan, output, bundles, scripts, fixtures):
    """Single candidate entry; fixtures stay exclusively behind the worker boundary."""
    output, bundles, scripts, fixtures = map(Path, (output, bundles, scripts, fixtures))
    check_plan(plan, bundles, scripts)
    if digest(fixtures.read_bytes()) != plan['http_worker']['fixture_sha256']:
        raise SharedIntegrityError('provider fixture source drift')
    if output.exists() or output.resolve().is_relative_to(bundles.resolve()):
        raise ValueError('new separate output required')
    output.mkdir()
    _write(output/'plan.json', _bytes(plan))
    journal = Journal(output/'journal.sqlite', plan, plan['base']['policy'], create=True)
    rows, stopped = [], None
    with offline_guard():
        for index, identity in enumerate(plan['base']['schedule']['plan']['jobs'], 1):
            if stopped:
                rows.append({**identity, 'status': 'not_run', 'attempts': 0, 'final_present': False})
                continue
            position = 0
            def send(request):
                nonlocal position
                check_plan(plan, bundles, scripts)
                if digest(fixtures.read_bytes()) != plan['http_worker']['fixture_sha256']:
                    raise SharedIntegrityError('provider fixture source drift')
                current = position; position += 1
                return dispatch(journal, identity, request, fixtures, current,
                                before_send=lambda: check_plan(plan, bundles, scripts))
            job, failure = execute_job(identity, bundles/identity['case_id'], plan, journal, send)
            snap = journal.snapshot()
            stopped = failure or snap['stopped']
            _write(output/f'job-{index:06d}.json', _bytes({'identity': identity, 'trajectory': job, 'round_integrity_failure': failure}))
            rows.append({**identity, 'status': job['status'], 'attempts': len(job['attempts']), 'final_present': job['final'] is not None})
    result = {'version': 's4_r3_result_v2', 'plan_sha256': canonical_hash(plan), 'rows': rows,
              'stop_reason': stopped, 'journal_stop': journal.snapshot()['stopped'], 'actual_model_calls': 0,
              'accuracy': None, 'provider_billed_cost': None, 'executor': 's4_response_worker_v1'}
    _write(output/'result.json', _bytes(result))
    return result


class Cursor(old.ReceivedCursor):
    def call(self, request):
        if self.ai == len(self.attempts) and self.stopped == 'synthetic_budget_exhausted':
            from scripts.s4_synthetic_cost_ledger import quote
            previous = self.before+self.attempts
            policy = self.plan['base']['policy']
            exhausted = (len(previous) >= policy['max_attempts'] or
                sum(a['reserved'] for a in previous)+quote(policy, len(mock_wire(request))) > policy['cap_micro'] or
                (len(previous)+1)*98304 > self.plan['cny_reservation']['cap_micro'])
            if not exhausted:
                raise SharedIntegrityError('unsubstantiated shared budget stop')
            self.shared_budget_stop = True
            raise RuntimeError('round_budget_exhausted')
        return super().call(request)


@single_entry
def evaluate(directory, bundles, scripts, *, mode='synthetic_diagnostic'):
    # old.evaluate already locks compatibility and forbids method/script reexecution.
    with patch.object(old, 'check_plan', check_plan), patch.object(old, 'ReadOnlyCnyJournal', ReadOnlyJournal), \
            patch.object(old, 'interpret', interpret), patch.object(old, 'ReceivedCursor', Cursor):
        result = old.evaluate(directory, bundles, scripts, mode=mode)
        plan = old.replay.read_json(Path(directory)/'plan.json')
        journal = ReadOnlyJournal(Path(directory)/'journal.sqlite', plan, plan['base']['policy'])
        with closing(journal.connect()) as db:
            starts = [json.loads(r[0]) for r in db.execute('SELECT receipt FROM worker_start_failure ORDER BY attempt')]
        result['unsent_worker_starts'] = starts
        if starts:
            from decimal import Decimal
            snapshot = journal.snapshot()
            entries = old.replay.cost_entries(snapshot['attempts'], journal.checkpoints())
            values = {a['id']: json.loads(a['usage']) if a['usage'] else {} for a in snapshot['attempts']}
            for entry in entries:
                if entry['entry_id'].startswith('attempt/'):
                    micro = values[int(entry['entry_id'].split('/')[1])].get('estimated_cny_micro')
                    entry['estimated_cost_cny'] = str(Decimal(micro)/1000000) if micro is not None else None
            entries += [{'entry_id': f'unsent_worker/{i}', 'stage': 'local_validation', 'operation': 'worker_start',
                         'elapsed_ms': r['elapsed_ms']+r['worker']['cleanup_elapsed_ms']} for i, r in enumerate(starts)]
            result['received_adapter']['common_cost'] = old.replay.aggregate_cost(entries)
        old.replay.require(old.replay.artifact_hashes(directory) == result['replay']['artifact_sha256'], 'run changed during worker audit')
    result['worker_sources_sha256'] = sources()
    result['execution_origin'] = 'response_driven_offline_HTTP_stack_worker'
    return result


def start_live(*args, **kwargs):
    raise PermissionError('production executor closed before approval, credential or transport callbacks')
