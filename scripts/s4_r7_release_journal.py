"""Additive approval/billing checks in the existing worker SQLite, test-only.

No runner fork and no production source installation. Original approvals and
cost records stay intact; the new checks have their own versioned audit rows.
"""
from contextlib import closing
import json

from scripts import s4_r7_worker_runtime as runtime
from scripts.s4_r7_approval_source_v2 import canonical_bytes, verify_signature
from scripts.s4_r7_billing_boundary_v3 import account_raw
from scripts.s4_r7_host_approval import scope_for, authorization_text
from scripts.s4_shared_transport_offline import SharedIntegrityError
from writscope.artifacts import canonical_hash, digest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def review_scope(plan):
    original = scope_for(plan)
    return {**original, 'plan_sha256': canonical_hash({'worker_plan': plan,
        'release_sources': {p: digest((ROOT/p).read_bytes()) for p in (
            'scripts/s4_r7_release_journal.py', 'scripts/s4_r7_approval_source_v2.py',
            'scripts/s4_r7_billing_boundary_v3.py')}})}


class ReviewJournal(runtime.Journal):
    def __init__(self, path, plan, policy, source, event_id, scope, clock, create=False):
        if source.trust.deployment != 'test_only':
            raise PermissionError('review journal accepts synthetic host source only; production still closed')
        if scope != review_scope(plan):
            raise PermissionError('signed scope must bind worker plan and release source versions')
        self.release_source, self.event_id, self.release_scope, self.release_clock = source, event_id, scope, clock
        self._release_ready = False
        super().__init__(path, plan, policy, create)
        if create:
            with closing(self.connect()) as db:
                db.execute('CREATE TABLE release_host_check (attempt INTEGER, phase TEXT, request_hash TEXT, receipt TEXT, PRIMARY KEY(attempt,phase))')
                db.execute('CREATE TABLE release_billing_check (attempt INTEGER PRIMARY KEY, response_hash TEXT, report TEXT)')
        self._release_ready = True
        self.snapshot()

    def _save_host(self, index, phase, request_hash, receipt):
        with closing(self.connect()) as db:
            db.execute('INSERT INTO release_host_check VALUES (?,?,?,?)',
                       (index, phase, request_hash, json.dumps(receipt)))

    def reserve(self, job, stage, wire, is_continuation=False):
        receipt = self.release_source.resolve(self.event_id, self.release_scope, self.release_clock())
        index = super().reserve(job, stage, wire, is_continuation)
        # A failed audit write leaves an unsent reservation; never releases money.
        try:
            self._save_host(index, 'reserve', digest(wire), receipt)
        except Exception as exc:
            raise SharedIntegrityError('release approval persistence failed; reservation retained') from exc
        return index

    def transition(self, attempt, before, after, **fields):
        if after == 'dispatch_started':
            self.snapshot()
            with closing(self.connect()) as db:
                row = db.execute("SELECT request_hash,receipt FROM release_host_check WHERE attempt=? AND phase='reserve'", (attempt,)).fetchone()
            if row is None:
                raise SharedIntegrityError('send lacks signed reserve approval')
            try:
                receipt = self.release_source.resolve(self.event_id, self.release_scope, self.release_clock(),
                                                      previous=json.loads(row[1]))
                self._save_host(attempt, 'before_send', row[0], receipt)
            except Exception as exc:
                raise SharedIntegrityError('release approval revoked/changed/write failed before send') from exc
        return super().transition(attempt, before, after, **fields)

    def persist_received(self, attempt, raw, receipt):
        super().persist_received(attempt, raw, receipt)
        report = account_raw(raw)
        try:
            with closing(self.connect()) as db:
                db.execute('INSERT INTO release_billing_check VALUES (?,?,?)',
                           (attempt, digest(raw), json.dumps(report)))
        except Exception as exc:
            raise SharedIntegrityError('billing check write failed; received bytes/reservation retained') from exc
        if report['stop_round']:
            raise SharedIntegrityError('release billing fault; received evidence retained; stop round')

    def snapshot(self):
        snap = super().snapshot()
        if not self._release_ready:
            return snap
        attempts = {a['id']: a for a in snap['attempts']}
        with closing(self.connect()) as db:
            host_rows = db.execute('SELECT attempt,phase,request_hash,receipt FROM release_host_check ORDER BY attempt,phase').fetchall()
            billing_rows = db.execute('SELECT attempt,response_hash,report FROM release_billing_check ORDER BY attempt').fetchall()
        phases = {}
        for index, phase, request_hash, raw in host_rows:
            if index not in attempts or phase not in ('reserve', 'before_send') or request_hash != attempts[index]['request_hash']:
                raise SharedIntegrityError('release host/request binding mismatch')
            proof = json.loads(raw); observation = proof['observation']; payload = observation['payload']
            try:
                verify_signature(self.release_source.trust, canonical_bytes(payload), observation['signature'])
            except PermissionError as exc:
                raise SharedIntegrityError('saved host signature invalid') from exc
            grant = {'source_id': self.release_source.trust.source_id, 'anchor_sha256': self.release_source.trust.fingerprint,
                     'event': payload['event'], 'scope_sha256': canonical_hash(self.release_scope)}
            event = payload['event']
            if (any(proof.get(k) != v for k, v in grant.items()) or proof.get('grant_sha256') != canonical_hash(grant) or
                    proof.get('observation_sha256') != digest(canonical_bytes(observation)) or
                    proof.get('actual_execution_approval') is not False or proof.get('deployment') != 'test_only' or
                    payload.get('scope_sha256') != grant['scope_sha256'] or
                    event.get('role') != 'user' or event.get('revoked') is not False or
                    event.get('event_id') != self.event_id or event.get('text') != authorization_text(self.release_scope) or
                    event.get('tenant') != self.release_source.trust.tenant or
                    event.get('actor') != self.release_source.trust.actor or
                    event.get('conversation') != self.release_source.trust.conversation):
                raise SharedIntegrityError('saved signed grant mismatch')
            phases.setdefault(index, {})[phase] = proof
        billed = {}
        for index, response_hash, raw in billing_rows:
            if (index not in attempts or attempts[index]['response'] is None or response_hash != attempts[index]['response_hash'] or
                    json.loads(raw) != account_raw(attempts[index]['response'])):
                raise SharedIntegrityError('release billing/raw-response mismatch')
            billed[index] = json.loads(raw)
        for index, attempt in attempts.items():
            own = phases.get(index, {})
            if attempt['status'] != 'reserved' and set(own) != {'reserve','before_send'}:
                raise SharedIntegrityError('sent attempt lacks two signed host observations')
            if len(own) == 2 and own['reserve']['grant_sha256'] != own['before_send']['grant_sha256']:
                raise SharedIntegrityError('grant changed across send boundary')
            if attempt['status'] in ('completed','failed') and index not in billed:
                raise SharedIntegrityError('terminal attempt lacks release billing check')
            if attempt['status'] == 'completed' and billed[index]['stop_round']:
                raise SharedIntegrityError('completed attempt bypassed release billing fault')
        return {**snap, 'release_host_checks': len(host_rows), 'release_billing_checks': len(billing_rows),
                'release_actual_approval': False}


def verify_before_reference(journal, evaluator):
    """Independent byte/signature checks precede any caller-supplied R4 scoring."""
    snapshot = journal.snapshot()
    if any(a['status'] in ('reserved','dispatch_started','received') for a in snapshot['attempts']):
        return {'status': 'incomplete_release_evidence_no_reference_read', 'reference_read': False}
    return evaluator()
