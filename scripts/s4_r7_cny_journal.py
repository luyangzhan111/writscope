"""CNY reservation extension in the SAME R3 SQLite transaction, offline gated."""
from contextlib import closing
import json
import sqlite3

from scripts.s4_r3 import Journal
from scripts.s4_r3 import check_plan as original_check_plan
from scripts.s4_unified_journal import now
from scripts.s4_synthetic_cost_ledger import quote
from scripts.s4_synthetic_cost_wire import SyntheticBudgetStop
from scripts.s4_shared_transport_offline import SharedIntegrityError
from scripts.s4_r6_transport_contract import TokenEvidence, token_admission, OfflineBridge
from scripts.s4_synthetic_cost_wire import mock_wire
from writscope.artifacts import digest, canonical_hash
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CNY_POLICY={'currency':'CNY','cap_micro':120000000,'input_rate':2,'output_rate':8,'output_limit':4096,'bound_kind':'synthetic_only_unvalidated_for_live'}


def extension_sources():
    return {p:digest((ROOT/p).read_bytes()) for p in ('scripts/s4_r7_cny_journal.py','scripts/s4_r7_io_boundary.py','scripts/s4_r6_transport_contract.py')}


def extend_plan(base):
    return {**base,'cny_reservation':dict(CNY_POLICY),'cny_extension_sources':extension_sources()}


def check_offline_plan(plan,bundles,scripts):
    base=dict(plan)
    if base.pop('cny_reservation',None)!=CNY_POLICY or base.pop('cny_extension_sources',None)!=extension_sources():
        raise SharedIntegrityError('CNY extension binding mismatch')
    original_check_plan(base,bundles,scripts)


def cny_micro(input_tokens):
    if type(input_tokens) is not int or not 0<=input_tokens<=32768:
        raise ValueError('invalid token bound')
    return input_tokens*2+4096*8  # CNY micro-units, official peak rates


class CnyJournal(Journal):
    def __init__(self,path,plan,policy,create=False):
        self._cny_ready=False;self._admissions={}
        self.cny=plan['cny_reservation']
        if self.cny!={'currency':'CNY','cap_micro':120000000,'input_rate':2,'output_rate':8,
                       'output_limit':4096,'bound_kind':'synthetic_only_unvalidated_for_live'}:
            raise SharedIntegrityError('unsupported CNY candidate policy')
        super().__init__(path,plan,policy,create)
        if create:
            with closing(self.connect()) as db:
                db.execute('CREATE TABLE cny_reservation (attempt INTEGER PRIMARY KEY, request_hash TEXT NOT NULL, input_bound INTEGER NOT NULL, reserved_cny_micro INTEGER NOT NULL, evidence TEXT NOT NULL)')
        self._cny_ready=True;self.snapshot()

    def admit_test_bound(self,wire,count):
        proof=token_admission(wire,TokenEvidence(digest(wire),count))
        self._admissions[digest(wire)]=proof

    def snapshot(self):
        value=super().snapshot()
        if not self._cny_ready:return value
        with closing(self.connect()) as db:
            rows=db.execute('SELECT attempt,request_hash,input_bound,reserved_cny_micro,evidence FROM cny_reservation ORDER BY attempt').fetchall()
        if len(rows)!=len(value['attempts']):raise SharedIntegrityError('CNY/attempt inventory mismatch')
        for row,attempt in zip(rows,value['attempts']):
            proof=json.loads(row[4])
            if (row[0]!=attempt['id'] or row[1]!=attempt['request_hash'] or row[3]!=cny_micro(row[2])
                    or proof!={'input_tokens':row[2],'kind':'synthetic_test_control','provider_token_bound_verified':False,'request_sha256':row[1]}):
                raise SharedIntegrityError('CNY reservation binding mismatch')
        total=sum(r[3] for r in rows)
        if total>self.cny['cap_micro']:raise SharedIntegrityError('CNY cap exceeded')
        return {**value,'reserved_cny_micro':total,'billed_cny':None,'cny_bound_verified_for_live':False}

    def reserve(self,job,stage,wire,is_continuation=False):
        self.snapshot();h=digest(wire);proof=self._admissions.get(h)
        if proof is None:raise SharedIntegrityError('no request-bound token admission')
        if type(is_continuation) is not bool:raise SharedIntegrityError('invalid continuation')
        amount=cny_micro(proof['input_tokens']);virtual=quote(self.policy,len(wire))
        with closing(self.connect()) as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT plan_hash,policy_hash,stopped FROM run').fetchone()!=(self.plan_hash,canonical_hash(self.policy),None):
                    raise SharedIntegrityError('stopped or mismatched plan')
                prior=db.execute('SELECT status,reserved FROM attempt').fetchall()
                if any(r[0] in ('reserved','dispatch_started','received') for r in prior):raise SharedIntegrityError('unresolved attempt')
                cny_total=db.execute('SELECT coalesce(sum(reserved_cny_micro),0) FROM cny_reservation').fetchone()[0]
                if (len(prior)>=self.policy['max_attempts'] or sum(r[1] for r in prior)+virtual>self.policy['cap_micro']
                        or cny_total+amount>self.cny['cap_micro']):
                    db.execute("UPDATE run SET stopped='synthetic_budget_exhausted'");db.execute('COMMIT')
                    raise SyntheticBudgetStop('attempt/CNY/compatibility cap exhausted')
                cur=db.execute("INSERT INTO attempt(job,stage,is_continuation,request,request_hash,reserved,status,created_at,cost_source) VALUES (?,?,?,?,?,?,'reserved',?,?)",
                               (job,stage,int(is_continuation),wire,h,virtual,now(),'synthetic_policy'))
                db.execute('INSERT INTO cny_reservation VALUES (?,?,?,?,?)',(cur.lastrowid,h,proof['input_tokens'],amount,json.dumps(proof)))
                db.execute('COMMIT');return cur.lastrowid
            except BaseException:
                if db.in_transaction:db.execute('ROLLBACK')
                raise


class CnyOfflineBridge(OfflineBridge):
    def __call__(self,case,method,stage,request,step):
        self.journal.admit_test_bound(mock_wire(request),32768)
        return super().__call__(case,method,stage,request,step)
