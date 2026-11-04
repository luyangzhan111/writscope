"""Transactional offline reservation prototype. Not a real-provider authorization gate."""
import sqlite3
from contextlib import closing
from pathlib import Path


class ReservationStore:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise ValueError("store does not exist")

    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @classmethod
    def create(cls, path, plan_hash, max_attempts, max_units):
        path = Path(path)
        if (len(plan_hash) != 64 or any(c not in "0123456789abcdef" for c in plan_hash)
                or type(max_attempts) is not int or max_attempts < 1
                or type(max_units) is not int or max_units < 1):
            raise ValueError("invalid synthetic plan")
        # Refuse to replace any existing user file.
        with path.open("xb"):
            pass
        store = cls(path)
        with closing(store._connect()) as db:
            db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE run (plan_hash TEXT NOT NULL, max_attempts INTEGER NOT NULL,
                                  max_units INTEGER NOT NULL, stopped TEXT);
                CREATE TABLE attempt (id INTEGER PRIMARY KEY, job_id TEXT NOT NULL,
                                      request_hash TEXT NOT NULL, reserved_units INTEGER NOT NULL,
                                      status TEXT NOT NULL, response_hash TEXT, usage TEXT);
                COMMIT;
            """)
            db.execute("INSERT INTO run VALUES (?, ?, ?, NULL)", (plan_hash, max_attempts, max_units))
        return store

    def reserve(self, plan_hash, job_id, request_hash, units):
        if (not job_id or len(request_hash) != 64 or any(c not in "0123456789abcdef" for c in request_hash)
                or type(units) is not int or units < 1):
            raise ValueError("invalid synthetic reservation")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT plan_hash,max_attempts,max_units,stopped FROM run").fetchone()
            if not row:
                raise ValueError("missing run configuration")
            if row[0] != plan_hash:
                db.execute("UPDATE run SET stopped='plan_binding_mismatch'")
                db.execute("COMMIT")
                raise ValueError("plan binding mismatch")
            if row[3]:
                raise ValueError("round stopped")
            if db.execute("SELECT 1 FROM attempt WHERE status IN ('reserved','dispatch_started')").fetchone():
                raise ValueError("unresolved attempt")
            count, reserved = db.execute("SELECT COUNT(*),COALESCE(SUM(reserved_units),0) FROM attempt").fetchone()
            if count >= row[1] or reserved + units > row[2]:
                db.execute("UPDATE run SET stopped='synthetic_budget_exhausted'")
                db.execute("COMMIT")
                raise ValueError("synthetic budget exhausted")
            cur = db.execute("INSERT INTO attempt(job_id,request_hash,reserved_units,status) VALUES(?,?,?,'reserved')",
                             (job_id, request_hash, units))
            db.execute("COMMIT")
            return cur.lastrowid
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def dispatch_started(self, attempt):
        with closing(self._connect()) as db:
            changed = db.execute("UPDATE attempt SET status='dispatch_started' WHERE id=? AND status='reserved' AND EXISTS (SELECT 1 FROM run WHERE stopped IS NULL)", (attempt,))
            if changed.rowcount != 1:
                raise ValueError("invalid dispatch transition")

    def finish(self, attempt, outcome, response_hash=None):
        if outcome not in {"completed", "failed"}:
            raise ValueError("invalid outcome")
        if response_hash is not None and (len(response_hash) != 64 or any(c not in "0123456789abcdef" for c in response_hash)):
            raise ValueError("invalid response hash")
        with closing(self._connect()) as db:
            changed = db.execute("UPDATE attempt SET status=?,response_hash=? WHERE id=? AND status='dispatch_started'",
                                 (outcome, response_hash, attempt))
            if changed.rowcount != 1:
                raise ValueError("invalid finish transition")

    def snapshot(self):
        with closing(self._connect()) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("sqlite integrity failure")
            plan_hash, max_attempts, max_units, stopped = db.execute("SELECT plan_hash,max_attempts,max_units,stopped FROM run").fetchone()
            rows = db.execute("SELECT id,job_id,request_hash,reserved_units,status,response_hash,usage FROM attempt ORDER BY id").fetchall()
        return {"execution_kind": "offline_simulation", "plan_hash": plan_hash,
                "max_attempts": max_attempts, "max_units": max_units, "stopped": stopped,
                "attempts": [{"id": r[0], "job_id": r[1], "request_hash": r[2], "reserved_units": r[3],
                              "status": r[4], "response_hash": r[5], "usage": r[6]} for r in rows],
                "actual_model_calls": 0}
