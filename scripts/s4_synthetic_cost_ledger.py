"""Transactional *synthetic* monetary reservation experiment; never a paid gate.

Prices, cap, and charges are integer virtual micro-units, not currency quotes.
No credentials, provider, network, or real-run authorization are accepted.
"""
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from writscope.artifacts import canonical_hash

LIMIT = 2**63 - 1
FIELDS = ("cap_micro", "max_attempts", "max_wire_bytes", "max_output_tokens",
          "framing_tokens", "input_rate_per_million", "output_rate_per_million")
ROW_FIELDS = ("id", "job_id", "stage", "request_hash", "wire_bytes", "reserved_micro", "status", "input_tokens", "output_tokens", "billed_micro")


class SyntheticBudgetExhausted(ValueError):
    """The offline virtual envelope has no room for another request."""


def validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) != set(FIELDS):
        raise ValueError("invalid synthetic policy fields")
    for key in FIELDS:
        value = policy[key]
        if type(value) is not int or value < (0 if key == "framing_tokens" else 1) or value > LIMIT:
            raise ValueError("invalid synthetic policy value")
    return dict(policy)


def quote(policy, wire_bytes):
    policy = validate_policy(policy)
    if type(wire_bytes) is not int or not 0 < wire_bytes <= policy["max_wire_bytes"]:
        raise ValueError("synthetic request size outside limit")
    # Ceiling avoids fractional virtual micro-units being rounded down.
    numerator = ((wire_bytes + policy["framing_tokens"]) * policy["input_rate_per_million"] +
                 policy["max_output_tokens"] * policy["output_rate_per_million"])
    amount = (numerator + 999_999) // 1_000_000
    if amount > LIMIT:
        raise ValueError("synthetic reservation overflow")
    return amount


class SyntheticCostLedger:
    def __init__(self, path, plan_hash, policy):
        self.path = Path(path)
        self.policy = validate_policy(policy)
        self.plan_hash = plan_hash
        if (not isinstance(plan_hash, str) or len(plan_hash) != 64 or
                any(c not in "0123456789abcdef" for c in plan_hash) or
                self.path.is_symlink() or not self.path.is_file()):
            raise ValueError("invalid synthetic ledger binding")
        with closing(self._connect()) as db:
            row = db.execute("SELECT plan_hash, policy_hash FROM run").fetchone()
            if row != (plan_hash, canonical_hash(self.policy)):
                raise ValueError("synthetic plan or policy mismatch")

    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _checked_rows(self, db):
        raw = db.execute("SELECT id,job_id,stage,request_hash,wire_bytes,reserved_micro,status,input_tokens,output_tokens,billed_micro FROM attempt ORDER BY id").fetchall()
        rows = [dict(zip(ROW_FIELDS, row)) for row in raw]
        pending = [r["id"] for r in rows if r["status"] in {"reserved", "dispatch_started"}]
        if (len(rows) > self.policy["max_attempts"] or sum(r["reserved_micro"] for r in rows) > self.policy["cap_micro"] or
                len(pending) > 1 or (pending and pending[0] != len(rows)) or
                any(r["id"] != i or r["reserved_micro"] != quote(self.policy, r["wire_bytes"]) or
                    r["status"] not in {"reserved", "dispatch_started", "completed", "failed"} or r["billed_micro"] is not None or
                    not r["job_id"] or not r["stage"] or len(r["request_hash"]) != 64 or
                    any(c not in "0123456789abcdef" for c in r["request_hash"]) or
                    any(v is not None and (type(v) is not int or v < 0) for v in (r["input_tokens"], r["output_tokens"])) or
                    (r["status"] in {"reserved", "dispatch_started"} and (r["input_tokens"] is not None or r["output_tokens"] is not None))
                    for i, r in enumerate(rows, 1))):
            raise ValueError("synthetic ledger invariant failure")
        return rows

    @classmethod
    def create(cls, path, plan_hash, policy):
        policy = validate_policy(policy)
        if not isinstance(plan_hash, str) or len(plan_hash) != 64 or any(c not in "0123456789abcdef" for c in plan_hash):
            raise ValueError("invalid synthetic plan hash")
        path = Path(path)
        with path.open("xb"):
            pass
        with closing(sqlite3.connect(str(path), isolation_level=None)) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE run (plan_hash TEXT NOT NULL, policy_hash TEXT NOT NULL, stopped TEXT);
                CREATE TABLE attempt (id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, stage TEXT NOT NULL,
                    request_hash TEXT NOT NULL, wire_bytes INTEGER NOT NULL, reserved_micro INTEGER NOT NULL,
                    status TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER, billed_micro INTEGER);
                COMMIT;
            """)
            db.execute("INSERT INTO run VALUES (?, ?, NULL)", (plan_hash, canonical_hash(policy)))
        return cls(path, plan_hash, policy)

    def reserve(self, job_id, stage, wire):
        if not isinstance(job_id, str) or not job_id or not isinstance(stage, str) or not stage or not isinstance(wire, bytes):
            raise ValueError("invalid synthetic request")
        amount = quote(self.policy, len(wire))
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT plan_hash, policy_hash, stopped FROM run").fetchone()
            if run is None or run[:2] != (self.plan_hash, canonical_hash(self.policy)):
                raise ValueError("synthetic plan or policy mismatch")
            if run[2]:
                raise ValueError("synthetic round stopped")
            rows = self._checked_rows(db)
            if any(r["status"] in {"reserved", "dispatch_started"} for r in rows):
                raise ValueError("unresolved synthetic attempt")
            count, total = len(rows), sum(r["reserved_micro"] for r in rows)
            if count >= self.policy["max_attempts"] or amount > self.policy["cap_micro"] - total:
                db.execute("UPDATE run SET stopped='synthetic_budget_exhausted'")
                db.execute("COMMIT")
                raise SyntheticBudgetExhausted("synthetic budget exhausted")
            row = db.execute("INSERT INTO attempt(job_id,stage,request_hash,wire_bytes,reserved_micro,status) VALUES(?,?,?,?,?,'reserved')",
                             (job_id, stage, hashlib.sha256(wire).hexdigest(), len(wire), amount))
            db.execute("COMMIT")
            return row.lastrowid
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def mark_dispatch(self, attempt):
        with closing(self._connect()) as db:
            row = db.execute("UPDATE attempt SET status='dispatch_started' WHERE id=? AND status='reserved' AND EXISTS (SELECT 1 FROM run WHERE stopped IS NULL AND plan_hash=? AND policy_hash=?)", (attempt, self.plan_hash, canonical_hash(self.policy)))
            if row.rowcount != 1:
                raise ValueError("invalid synthetic dispatch")

    def finish(self, attempt, outcome, *, input_tokens=None, output_tokens=None):
        if outcome not in {"completed", "failed"} or any(v is not None and (type(v) is not int or not 0 <= v <= LIMIT) for v in (input_tokens, output_tokens)):
            raise ValueError("invalid synthetic outcome")
        with closing(self._connect()) as db:
            row = db.execute("UPDATE attempt SET status=?,input_tokens=?,output_tokens=? WHERE id=? AND status='dispatch_started' AND EXISTS (SELECT 1 FROM run WHERE stopped IS NULL AND plan_hash=? AND policy_hash=?)",
                             (outcome, input_tokens, output_tokens, attempt, self.plan_hash, canonical_hash(self.policy)))
            if row.rowcount != 1:
                raise ValueError("invalid synthetic finish")

    def snapshot(self):
        with closing(self._connect()) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("synthetic ledger integrity failure")
            plan, policy_hash, stopped = db.execute("SELECT plan_hash,policy_hash,stopped FROM run").fetchone()
            if (plan, policy_hash) != (self.plan_hash, canonical_hash(self.policy)):
                raise ValueError("synthetic plan or policy mismatch")
            rows = self._checked_rows(db)
        return {"version": "s4_synthetic_cost_ledger_v1", "execution_kind": "offline_simulation",
                "attempts": rows, "reserved_micro": sum(r["reserved_micro"] for r in rows),
                "stopped": stopped, "actual_model_calls": 0, "provider_billed_cost": None}
