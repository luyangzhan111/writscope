"""Append-only synthetic local-operation checkpoints in the shared SQLite journal."""
import json
from contextlib import closing

from scripts.s4_shared_transport_offline import SharedIntegrityError
from scripts.s4_unified_journal import Journal, now
from writscope.artifacts import canonical_hash


class CheckpointJournal(Journal):
    def __init__(self, path, plan, policy, create=False):
        super().__init__(path, plan, policy, create=create)
        if create:
            with closing(self.connect()) as db:
                db.execute('CREATE TABLE checkpoint (seq INTEGER PRIMARY KEY, job TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL)')
        self.checkpoints()

    def checkpoints(self):
        with closing(self.connect()) as db:
            db.row_factory = __import__('sqlite3').Row
            rows = [dict(row) for row in db.execute('SELECT * FROM checkpoint ORDER BY seq')]
        previous = self.plan_hash
        for index, row in enumerate(rows, 1):
            obj = {k:row[k] for k in ('seq','job','kind','payload','created_at','previous_hash')}
            if row['seq'] != index or row['previous_hash'] != previous or row['event_hash'] != canonical_hash(obj):
                raise SharedIntegrityError('checkpoint hash chain mismatch')
            try:
                json.loads(row['payload'])
            except (TypeError, ValueError) as exc:
                raise SharedIntegrityError('invalid checkpoint payload') from exc
            previous = row['event_hash']
        return rows

    def append_checkpoint(self, job, kind, payload):
        self.snapshot()
        self.checkpoints()
        with closing(self.connect()) as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                run = db.execute('SELECT plan_hash,policy_hash,stopped FROM run').fetchone()
                if run != (self.plan_hash, canonical_hash(self.policy), None):
                    raise SharedIntegrityError('checkpoint plan mismatch or stopped journal')
                last = db.execute('SELECT seq,event_hash FROM checkpoint ORDER BY seq DESC LIMIT 1').fetchone()
                obj = {'seq':last[0]+1 if last else 1,'job':job,'kind':kind,
                       'payload':json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')),
                       'created_at':now(),'previous_hash':last[1] if last else self.plan_hash}
                db.execute('INSERT INTO checkpoint VALUES (?,?,?,?,?,?,?)',
                           (*obj.values(),canonical_hash(obj)))
                db.execute('COMMIT')
            except Exception:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise
