"""Additive R4 adapter for received HTTP envelopes and the CNY journal.

Reuses the independent R4 state/lookup/terminal reconstruction and evaluator.
Compatibility injection is process-local, single-threaded and always restored.
No run record chooses a verifier, reader, callback, private reference or URL.
"""
from contextlib import contextmanager, closing
import json
from pathlib import Path
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from scripts import s4_r3 as r3, s4_r4_replay as replay, s4_r4_evaluator as evaluator
from scripts.s4_r7_cny_journal import CnyJournal, check_offline_plan, extend_plan
from scripts.s4_r7_response_adapter import FixtureBridge, interpret, round_stop
from scripts.s4_shared_transport_offline import SharedIntegrityError
from scripts.s4_synthetic_cost_wire import mock_wire
from scripts.s4_r5_acceptance import offline_guard
from scripts.s4_unified_journal import now
from scripts.s4_r7_host_approval import fixture_source, scope_for, check_fixture_receipt
from writscope.artifacts import digest

ROOT = Path(__file__).resolve().parents[1]
LOCK = threading.Lock()


def source_hashes():
    return {p: digest((ROOT/p).read_bytes()) for p in (
        'scripts/s4_r7_r4_adapter.py', 'scripts/s4_r7_response_adapter.py',
        'scripts/s4_r7_token_boundary.py', 'scripts/s4_r7_host_approval.py')}


def prepare(*args, **kwargs):
    return {**extend_plan(r3.prepare(*args, **kwargs)), 'received_adapter_sources': source_hashes()}


def check_plan(plan, bundles, scripts):
    original = dict(plan)
    if original.pop('received_adapter_sources', None) != source_hashes():
        raise SharedIntegrityError('response adapter source mismatch')
    check_offline_plan(original, bundles, scripts)


class ReceivedJournal(CnyJournal):
    """Atomically persist raw response/prefix and transport receipt in the same DB."""
    def __init__(self, path, plan, policy, create=False):
        self._receipt_ready = False
        self.approval_source = fixture_source(plan)
        self._previous_approval = None
        super().__init__(path, plan, policy, create)
        if create:
            with closing(self.connect()) as db:
                db.execute('CREATE TABLE transport_receipt (attempt INTEGER PRIMARY KEY, receipt TEXT NOT NULL)')
                db.execute('CREATE TABLE approval_check (attempt INTEGER PRIMARY KEY, request_hash TEXT NOT NULL, receipt TEXT NOT NULL)')
        self._receipt_ready = True
        self.snapshot()

    def reserve(self, job, stage, wire, is_continuation=False):
        try:
            proof = self.approval_source.resolve('fixed-user-event', scope_for(self.plan),
                datetime(2026, 11, 2, tzinfo=timezone.utc), previous=self._previous_approval)
            check_fixture_receipt(proof, self.plan)
        except Exception as exc:
            raise SharedIntegrityError('approval source unavailable, revoked or mismatched') from exc
        index = super().reserve(job, stage, wire, is_continuation)
        # A crash between the atomic CNY reserve and this write leaves RESERVED.
        # No dispatch can occur until this proof is durable; never refund on failure.
        with closing(self.connect()) as db:
            db.execute('INSERT INTO approval_check VALUES (?,?,?)', (index, digest(wire), json.dumps(proof)))
        self._previous_approval = proof
        return index

    def transition(self, attempt, before, after, **fields):
        if after == 'dispatch_started':
            self.snapshot()
            with closing(self.connect()) as db:
                row = db.execute('SELECT receipt FROM approval_check WHERE attempt=?', (attempt,)).fetchone()
            if row is None:
                raise SharedIntegrityError('send forbidden without durable approval check')
            check_fixture_receipt(json.loads(row[0]), self.plan)
            try:
                self.approval_source.resolve('fixed-user-event', scope_for(self.plan),
                    datetime(2026, 11, 2, tzinfo=timezone.utc), previous=json.loads(row[0]))
            except Exception as exc:
                raise SharedIntegrityError('approval revoked at dispatch boundary') from exc
        return super().transition(attempt, before, after, **fields)

    def persist_received(self, attempt, raw, receipt):
        with closing(self.connect()) as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                cur = db.execute("UPDATE attempt SET status='received', response=?,response_hash=?,received_at=?,http_status=? "
                                 "WHERE id=? AND status='dispatch_started' AND EXISTS (SELECT 1 FROM run WHERE stopped IS NULL AND plan_hash=?)",
                                 (raw, digest(raw), now(), receipt['http_status'], attempt, self.plan_hash))
                if cur.rowcount != 1:
                    raise SharedIntegrityError('receipt transition mismatch')
                db.execute('INSERT INTO transport_receipt VALUES (?,?)', (attempt, json.dumps(receipt)))
                db.execute('COMMIT')
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def snapshot(self):
        value = super().snapshot()
        if not self._receipt_ready:
            return value
        with closing(self.connect()) as db:
            rows = db.execute('SELECT attempt,receipt FROM transport_receipt ORDER BY attempt').fetchall()
            proofs = dict(db.execute('SELECT attempt,evidence FROM cny_reservation').fetchall())
            approvals = db.execute('SELECT attempt,request_hash,receipt FROM approval_check ORDER BY attempt').fetchall()
        attempts_by_id = {a['id']: a for a in value['attempts']}
        approval_ids = set()
        for index, request_hash, text in approvals:
            if index not in attempts_by_id or request_hash != attempts_by_id[index]['request_hash']:
                raise SharedIntegrityError('approval/request binding mismatch')
            try:
                check_fixture_receipt(json.loads(text), self.plan)
            except (ValueError, PermissionError) as exc:
                raise SharedIntegrityError('invalid persisted approval check') from exc
            approval_ids.add(index)
        if any(a['status'] != 'reserved' and a['id'] not in approval_ids for a in value['attempts']):
            raise SharedIntegrityError('sent attempt lacks durable approval check')
        received = [a for a in value['attempts'] if a['status'] in ('received', 'completed', 'failed')]
        if len(rows) != len(received):
            raise SharedIntegrityError('receipt/attempt inventory mismatch')
        for (index, text), attempt in zip(rows, received):
            receipt = json.loads(text)
            if (index != attempt['id'] or receipt.get('request_sha256') != attempt['request_hash'] or
                    receipt.get('response_sha256') != attempt['response_hash'] or
                    receipt.get('http_status') != attempt['http_status'] or
                    receipt.get('captured_bytes') != len(attempt['response']) or
                    (attempt['usage'] is not None and (json.loads(attempt['usage']).get('transport_receipt') != receipt or
                     json.loads(attempt['usage']).get('token_admission') != json.loads(proofs[index])))):
                raise SharedIntegrityError('durable receipt binding mismatch')
        return value


class ReadOnlyCnyJournal(ReceivedJournal):
    def connect(self):
        return sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True, isolation_level=None)


class ReceivedCursor(replay.Cursor):
    def call(self, request):
        replay.require(round_stop(self.before + self.attempts[:self.ai]) is None,
                       'work continued after transport/accounting stop')
        # The original path handles local/shared budget exhaustion and absent attempts.
        if self.ai >= self.plan['base']['policy']['max_attempts'] or self.ai == len(self.attempts):
            return super().call(request)
        saved = self.attempts[self.ai]
        replay.require(saved['request'] == mock_wire(request) and saved['stage'] == request['stage'] and
                       saved['is_continuation'] == int('lookup_response' in request['public_input']) and
                       saved['cost_source'] == 'synthetic_policy', 'request reconstruction mismatch')
        self.ai += 1
        self.time(saved['created_at'])
        for field in ('sent_at', 'received_at'):
            if saved[field] is not None:
                self.time(saved[field])
        if saved['status'] in ('reserved', 'dispatch_started', 'received'):
            replay.require(self.partial, 'unresolved attempt in settled result')
            replay.require(all(saved[k] is None for k in ('finish_reason', 'error_kind', 'validation', 'usage')),
                           'unresolved attempt contains fabricated validation')
            if saved['status'] != 'received':
                replay.require(all(saved[k] is None for k in ('response', 'received_at', 'http_status')),
                               'pending send contains invented response')
            else:
                replay.require(saved['response'] is not None and saved['received_at'] is not None,
                               'received prefix missing raw bytes/time')
            raise replay.Incomplete('unresolved_' + saved['status'])
        usage = json.loads(saved['usage']) if saved['usage'] else {}
        proof = usage.get('token_admission', {})
        # CnyJournal separately verifies this exact proof against the same-transaction table.
        replay.require(proof == {'input_tokens': 32768, 'kind': 'synthetic_test_control',
                                 'provider_token_bound_verified': False, 'request_sha256': saved['request_hash']},
                       'invalid offline token evidence')
        replay.require(saved['response'] is not None and saved['received_at'] is not None, 'missing received bytes')
        payload, finish, error, expected = interpret(saved['response'], usage.get('transport_receipt', {}), request, 32768)
        expected['token_admission'] = proof
        replay.require(usage == expected and saved['http_status'] == usage['transport_receipt']['http_status'] and
                       saved['finish_reason'] == finish and saved['error_kind'] == error and
                       saved['validation'] == ('rejected' if error else 'accepted') and
                       saved['status'] == ('failed' if error else 'completed'), 'response/usage derivation mismatch')
        if error:
            raise ValueError(error)
        return payload


@contextmanager
def compatibility(*, running=False):
    if not LOCK.acquire(blocking=False):
        raise RuntimeError('single-concurrency adapter already active')
    try:
        with offline_guard(), patch.object(r3, 'check_plan', check_plan), patch.object(r3, 'ReadOnlyJournal', ReadOnlyCnyJournal), \
                patch.object(replay, 'Cursor', ReceivedCursor):
            if running:
                with patch.object(r3, 'Journal', ReceivedJournal), patch.object(r3, 'MockProviderBridge', FixtureBridge):
                    yield
            else:
                yield
    finally:
        LOCK.release()


def run_offline(plan, output, bundles, scripts):
    with compatibility(running=True):
        return r3._run(plan, Path(output), Path(bundles), Path(scripts))


def evaluate(directory, bundles, scripts, *, mode='synthetic_diagnostic'):
    if mode != 'synthetic_diagnostic':
        raise PermissionError('live provenance/scoring unavailable')
    with compatibility():
        result = evaluator.evaluate(directory, bundles, scripts)
        plan = replay.read_json(Path(directory)/'plan.json')
        journal = ReadOnlyCnyJournal(Path(directory)/'journal.sqlite', plan, plan['base']['policy'])
        snapshot = journal.snapshot()
        # These are candidate estimates from fixture usage, not actual billable totals.
        entries = [json.loads(a['usage']) if a['usage'] else {} for a in snapshot['attempts'] if a['status'] != 'reserved']
        estimates = [u.get('estimated_cny_micro') for u in entries]
        cost_entries = replay.cost_entries(snapshot['attempts'], journal.checkpoints())
        usage_by_id = {a['id']: json.loads(a['usage']) if a['usage'] else {} for a in snapshot['attempts']}
        for entry in cost_entries:
            if entry['entry_id'].startswith('attempt/'):
                micro = usage_by_id[int(entry['entry_id'].split('/')[1])].get('estimated_cny_micro')
                entry['estimated_cost_cny'] = str(Decimal(micro)/1000000) if micro is not None else None
        result['received_adapter'] = {'sources_sha256': source_hashes(),
            'provenance': 'offline_fixture_not_authenticated_provider',
            'reserved_cny_micro': snapshot['reserved_cny_micro'],
            'known_fixture_estimate_cny_micro': sum(v for v in estimates if v is not None),
            'unknown_estimate_entries': sum(v is None for v in estimates),
            'total_fixture_estimate_cny_micro': sum(estimates) if all(v is not None for v in estimates) else None,
            'common_cost': replay.aggregate_cost(cost_entries),
            'provider_billed_cny': None, 'real_transport_allowed': False}
        return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--bundle-root', type=Path, required=True)
    parser.add_argument('--scripts', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if (any(output.is_relative_to(p.resolve()) for p in (args.run, args.bundle_root, ROOT/'evaluation_private')) or
            output == args.scripts.resolve()):
        raise ValueError('output overlaps bound evidence or references')
    result = evaluate(args.run, args.bundle_root, args.scripts)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
