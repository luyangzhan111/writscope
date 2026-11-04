"""Thin v9 integration: frozen worker executor plus independently bound receipts.

No method runner copy. The only new run path is fixed-byte, synthetic-host mode.
"""
from contextlib import closing, contextmanager
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import threading
import math
import time
from unittest.mock import patch

from scripts import s4_r7_worker_runtime as worker
from scripts.s4_r7_release_journal import ReviewJournal, review_scope
from scripts.s4_r7_bound_approval_v3 import verify_observation, BoundHostSource
from scripts.s4_shared_transport_offline import SharedIntegrityError
from writscope.artifacts import digest, canonical_hash

ROOT = Path(__file__).resolve().parents[1]
BASE_CHECK, BASE_READONLY, BASE_DISPATCH = worker.check_plan, worker.ReadOnlyJournal, worker.dispatch
LOCK = threading.Lock()


def sources():
    return {p:digest((ROOT/p).read_bytes()) for p in (
        'scripts/s4_r7_bound_approval_v3.py','scripts/s4_r7_bound_runtime.py')}


def extend(plan):
    return {**plan, 'bound_release':{
        'version':'s4_bound_release_v1','source_sha256':sources(),
        'accepted_v8_sha256':'24660991d0e86930b46d82131a8743008a4fb711416276a4e18a6b5949f4568f',
        'origin':'synthetic_host_and_fixed_HTTP_bytes','real_sending':False}}


def prepare(*args, **kwargs):
    return extend(worker.prepare(*args, **kwargs))


def check_plan(plan, bundles, scripts):
    base = dict(plan); ext = base.pop('bound_release',None)
    if ext != {'version':'s4_bound_release_v1','source_sha256':sources(),
               'accepted_v8_sha256':'24660991d0e86930b46d82131a8743008a4fb711416276a4e18a6b5949f4568f',
               'origin':'synthetic_host_and_fixed_HTTP_bytes','real_sending':False}:
        raise SharedIntegrityError('bound release source/version mismatch')
    BASE_CHECK(base,bundles,scripts)


class BoundJournal(ReviewJournal):
    def __init__(self,path,plan,policy,source,event_id,clock,create=False):
        if type(source) is not BoundHostSource or source.trust.deployment != 'test_only':
            raise PermissionError('only explicitly synthetic host may run offline integration')
        self._timing_ready=False
        super().__init__(path,plan,policy,source,event_id,review_scope(plan),clock,create)
        if create:
            with closing(self.connect()) as db:
                db.execute('CREATE TABLE bound_dispatch_cost (attempt INTEGER PRIMARY KEY,elapsed_ms REAL NOT NULL)')
                db.execute('CREATE TABLE bound_round_cost (singleton INTEGER PRIMARY KEY CHECK(singleton=1),elapsed_ms REAL NOT NULL)')
        self._timing_ready=True
        self.snapshot()

    def reserve(self,job,stage,wire,is_continuation=False):
        self.release_source.context = {'job':job,'stage':stage,'request_sha256':digest(wire),
            'is_continuation':is_continuation,'ordinal':len(self.snapshot()['attempts'])+1,'phase':'reserve'}
        return super().reserve(job,stage,wire,is_continuation)

    def transition(self,attempt,before,after,**fields):
        if after == 'dispatch_started':
            saved = next(a for a in self.snapshot()['attempts'] if a['id']==attempt)
            self.release_source.context = {'job':saved['job'],'stage':saved['stage'],
                'request_sha256':saved['request_hash'],'is_continuation':bool(saved['is_continuation']),
                'ordinal':attempt,'phase':'before_send'}
        return super().transition(attempt,before,after,**fields)

    def snapshot(self):
        snap = super().snapshot()
        if not self._release_ready:
            return snap
        attempts = {a['id']:a for a in snap['attempts']}
        with closing(self.connect()) as db:
            rows = db.execute("SELECT attempt,phase,receipt FROM release_host_check ORDER BY attempt,CASE phase WHEN 'reserve' THEN 0 ELSE 1 END").fetchall()
        last_counter, challenges = 0, set()
        for index,phase,raw in rows:
            saved=attempts[index]
            context={'job':saved['job'],'stage':saved['stage'],'request_sha256':saved['request_hash'],
                     'is_continuation':bool(saved['is_continuation']),'ordinal':index,'phase':phase}
            try:
                payload=verify_observation(self.release_source.trust,self.release_scope,self.event_id,json.loads(raw),context)
            except (PermissionError,ValueError,TypeError,KeyError) as exc:
                raise SharedIntegrityError('bound host archive verification failed') from exc
            if payload['counter']<=last_counter or payload['challenge'] in challenges:
                raise SharedIntegrityError('host counter/challenge reused across phases or attempts')
            last_counter=payload['counter']; challenges.add(payload['challenge'])
        snap['bound_host_counter']=last_counter
        if self._timing_ready:
            with closing(self.connect()) as db:
                times=db.execute('SELECT attempt,elapsed_ms FROM bound_dispatch_cost ORDER BY attempt').fetchall()
                round_times=db.execute('SELECT elapsed_ms FROM bound_round_cost').fetchall()
            if (any(i not in attempts or type(ms) not in (int,float) or not math.isfinite(ms) or ms<0 for i,ms in times) or
                    len(round_times)>1 or any(not math.isfinite(r[0]) or r[0]<0 for r in round_times)):
                raise SharedIntegrityError('bound local cost inventory/value invalid')
            snap['bound_dispatch_elapsed_ms']=dict(times)
            snap['bound_round_elapsed_ms']=round_times[0][0] if round_times else None
        return snap


class ReadOnlyBoundJournal(BoundJournal):
    def connect(self):
        return sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,isolation_level=None)


@contextmanager
def compatibility(source,event_id,clock):
    if not LOCK.acquire(blocking=False):
        raise RuntimeError('single bound-release entry already running')
    try:
        def journal(path,plan,policy,create=False):
            return BoundJournal(path,plan,policy,source,event_id,clock,create)
        def readonly(path,plan,policy,create=False):
            if create:raise PermissionError('read-only evaluator cannot create journal')
            return ReadOnlyBoundJournal(path,plan,policy,source,event_id,clock,False)
        def dispatch(journal,*args,**kwargs):
            started=time.perf_counter()
            before=len(journal.snapshot()['attempts'])
            try:
                return BASE_DISPATCH(journal,*args,**kwargs)
            finally:
                elapsed=(time.perf_counter()-started)*1000
                with closing(journal.connect()) as db:
                    ids=db.execute('SELECT id FROM attempt WHERE id>? ORDER BY id',(before,)).fetchall()
                    if len(ids)>1:raise SharedIntegrityError('dispatch created multiple attempts')
                    if ids:db.execute('INSERT INTO bound_dispatch_cost VALUES (?,?)',(ids[0][0],elapsed))
        with patch.object(worker,'check_plan',check_plan), patch.object(worker,'Journal',journal), \
             patch.object(worker,'ReadOnlyJournal',readonly), patch.object(worker,'dispatch',dispatch):
            yield
    finally:
        LOCK.release()


def run(plan,output,bundles,scripts,fixtures,source,event_id,clock):
    started=time.perf_counter(); existed=Path(output).exists()
    try:
        with compatibility(source,event_id,clock):
            return worker.run(plan,output,bundles,scripts,fixtures)
    finally:
        path=Path(output)/'journal.sqlite'
        if not existed and path.exists():
            with closing(sqlite3.connect(path,isolation_level=None)) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='bound_round_cost'").fetchone():
                    db.execute('INSERT INTO bound_round_cost VALUES (1,?)',((time.perf_counter()-started)*1000,))


def evaluate(directory,bundles,scripts,source,event_id,clock):
    # Resolve no source and run no worker during replay. The trust root comes
    # from the caller, not the run's public key or approval booleans.
    if type(source) is not BoundHostSource:
        raise PermissionError('explicit verifier trust root required')
    plan=json.loads((Path(directory)/'plan.json').read_text(encoding='utf-8'))
    check_plan(plan,bundles,scripts)
    journal=ReadOnlyBoundJournal(Path(directory)/'journal.sqlite',plan,plan['base']['policy'],source,event_id,clock)
    before=digest(journal.path.read_bytes()); snap=journal.snapshot()
    with closing(journal.connect()) as db:
        billing=[json.loads(r[0]) for r in db.execute('SELECT report FROM release_billing_check ORDER BY attempt')]
        by_attempt={i:json.loads(raw) for i,raw in db.execute('SELECT attempt,report FROM release_billing_check')}
    known=[Decimal(v['token_cost_cny_micro']) for v in billing if v['token_cost_cny_micro'] is not None]
    entries=worker.old.replay.cost_entries(snap['attempts'],journal.checkpoints())
    for entry in entries:
        if entry['entry_id'].startswith('attempt/'):
            index=int(entry['entry_id'].split('/')[1]);checked=by_attempt.get(index,{})
            micro=checked.get('token_cost_cny_micro')
            entry['estimated_cost_cny']=str(Decimal(micro)/1000000) if micro is not None else None
            # Whole dispatch includes host verification, all in-call persistence,
            # worker and response checks. Replace, do not add to provider elapsed.
            entry['elapsed_ms']=snap['bound_dispatch_elapsed_ms'].get(index)
    measured=[e['elapsed_ms'] for e in entries]
    round_ms=snap['bound_round_elapsed_ms']
    overhead=round_ms-sum(measured) if round_ms is not None and all(v is not None for v in measured) else None
    if overhead is not None and overhead < -0.001:
        raise SharedIntegrityError('overlapping local/provider cost intervals')
    entries.append({'entry_id':'round/local_overhead','stage':'local_validation','operation':'setup_prompt_checks_scheduling',
                    'elapsed_ms':max(0,overhead) if overhead is not None else None})
    # Costs and interval consistency must also be checked before opening labels.
    common_cost=worker.old.replay.aggregate_cost(entries)
    with compatibility(source,event_id,clock):
        report=worker.evaluate(directory,bundles,scripts)
    report['bound_release']={'source_sha256':sources(),'host_checks':snap['release_host_checks'],
        'billing_checks':len(billing),'reference_channel':'original_R4_after_independent_bound_receipt_validation',
        'known_fixture_token_estimate_cny':str(sum(known,Decimal(0))/1000000),
        'unknown_token_estimate_entries':sum(v['token_cost_cny_micro'] is None for v in billing),
        'unknown_tool_usage_entries':sum(v['unknown_tool_usage'] for v in billing),
        'combined_cost_cny':None,'actual_provider_bill':None,'actual_execution_approval':False,
        'common_cost':common_cost,'round_elapsed_ms':round_ms,
        'local_time_policy':'whole_dispatch_replaces_provider_time; remainder_is_round_local_overhead',
        'real_transport_allowed':False}
    if digest(journal.path.read_bytes())!=before:
        raise SharedIntegrityError('read-only release replay changed journal')
    return report


def start_live(*args,**kwargs):
    raise PermissionError('v9 offline only; closed before approval, credential, transport or process callbacks')
