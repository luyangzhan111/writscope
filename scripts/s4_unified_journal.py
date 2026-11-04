"""Single SQLite journal for synthetic S4 execution. No real transport or credentials."""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from scripts.s4_synthetic_cost_ledger import quote, validate_policy
from scripts.s4_synthetic_cost_wire import SyntheticBudgetStop
from scripts.s4_shared_transport_offline import SharedIntegrityError
from writscope.artifacts import canonical_hash


def now():
    return datetime.now(timezone.utc).isoformat()


class Journal:
    def __init__(self, path, plan, policy, create=False):
        self.path, self.plan, self.policy = Path(path), plan, validate_policy(policy)
        self.plan_hash = canonical_hash(plan)
        if create:
            with self.path.open('xb'):
                pass
            with closing(self.connect()) as db:
                db.executescript("""BEGIN IMMEDIATE;
                CREATE TABLE run (plan_hash TEXT NOT NULL, policy_hash TEXT NOT NULL, stopped TEXT);
                CREATE TABLE attempt (id INTEGER PRIMARY KEY, job TEXT NOT NULL, stage TEXT NOT NULL, is_continuation INTEGER NOT NULL,
                    request BLOB NOT NULL, request_hash TEXT NOT NULL, reserved INTEGER NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, sent_at TEXT, received_at TEXT,
                    response BLOB, response_hash TEXT, http_status INTEGER, finish_reason TEXT,
                    error_kind TEXT, validation TEXT, usage TEXT, cost_source TEXT);
                COMMIT;""")
                db.execute('INSERT INTO run VALUES (?,?,NULL)', (self.plan_hash, canonical_hash(self.policy)))
        if self.path.is_symlink() or not self.path.is_file():
            raise SharedIntegrityError('missing or linked journal')
        self.snapshot()

    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        db.execute('PRAGMA synchronous=FULL')
        return db

    def reserve(self, job, stage, wire, is_continuation=False):
        self.snapshot()
        if type(is_continuation) is not bool:
            raise SharedIntegrityError('invalid continuation marker')
        amount = quote(self.policy, len(wire))
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            run = db.execute('SELECT plan_hash,policy_hash,stopped FROM run').fetchone()
            if run[:2] != (self.plan_hash, canonical_hash(self.policy)) or run[2]:
                raise SharedIntegrityError('plan binding or stopped journal')
            rows = db.execute('SELECT status,reserved FROM attempt').fetchall()
            if any(status in ('reserved','dispatch_started','received') for status, _ in rows):
                raise SharedIntegrityError('unresolved attempt')
            total = sum(value for _, value in rows)
            if len(rows) >= self.policy['max_attempts'] or amount > self.policy['cap_micro'] - total:
                db.execute("UPDATE run SET stopped='synthetic_budget_exhausted'")
                db.execute('COMMIT')
                raise SyntheticBudgetStop('synthetic budget exhausted')
            cur = db.execute('INSERT INTO attempt(job,stage,is_continuation,request,request_hash,reserved,status,created_at,cost_source) VALUES (?,?,?,?,?,?,\'reserved\',?,?)',
                (job, stage, int(is_continuation), wire, hashlib.sha256(wire).hexdigest(), amount, now(), 'synthetic_policy'))
            db.execute('COMMIT')
            return cur.lastrowid
        except Exception:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise
        finally:
            db.close()

    def transition(self, attempt, before, after, **fields):
        allowed = {'sent_at','received_at','response','response_hash','http_status','finish_reason','error_kind','validation','usage'}
        if set(fields) - allowed:
            raise SharedIntegrityError('invalid journal field')
        assignments = ','.join(['status=?'] + [f'{key}=?' for key in fields])
        with closing(self.connect()) as db:
            row = db.execute(f'UPDATE attempt SET {assignments} WHERE id=? AND status=? AND EXISTS (SELECT 1 FROM run WHERE plan_hash=? AND policy_hash=? AND stopped IS NULL)',
                             (after, *fields.values(), attempt, before, self.plan_hash, canonical_hash(self.policy)))
            if row.rowcount != 1:
                raise SharedIntegrityError('invalid journal transition')

    def snapshot(self):
        with closing(self.connect()) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise SharedIntegrityError('journal corruption')
            run = db.execute('SELECT plan_hash,policy_hash,stopped FROM run').fetchone()
            if run is None or run[:2] != (self.plan_hash, canonical_hash(self.policy)):
                raise SharedIntegrityError('plan binding mismatch')
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute('SELECT * FROM attempt ORDER BY id')]
        total = 0
        pending = 0
        for index, row in enumerate(rows, 1):
            pending += row['status'] in {'reserved','dispatch_started','received'}
            if (row['id'] != index or row['is_continuation'] not in (0, 1) or hashlib.sha256(row['request']).hexdigest() != row['request_hash'] or
                row['reserved'] != quote(self.policy, len(row['request'])) or
                row['status'] not in {'reserved','dispatch_started','received','completed','failed'} or
                (row['response'] is not None and hashlib.sha256(row['response']).hexdigest() != row['response_hash']) or
                (row['response'] is None and row['response_hash'] is not None) or
                (row['status'] == 'reserved' and (row['sent_at'] is not None or row['response'] is not None)) or
                (row['status'] != 'reserved' and row['sent_at'] is None) or
                (row['status'] in {'received','completed'} and row['response'] is None) or
                (row['status'] == 'completed' and row['validation'] != 'accepted') or
                (row['status'] == 'failed' and not row['error_kind'])):
                raise SharedIntegrityError('journal invariant failure')
            total += row['reserved']
        if pending > 1 or total > self.policy['cap_micro'] or len(rows) > self.policy['max_attempts']:
            raise SharedIntegrityError('journal budget invariant failure')
        return {'stopped': run[2], 'attempts': rows, 'reserved_virtual_micro': total}
