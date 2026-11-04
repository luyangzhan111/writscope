"""Versioned local-host IPC wiring around v9; no method runner duplication."""
import base64
from contextlib import closing, contextmanager
import json
import math
from pathlib import Path
import sqlite3
import threading
from unittest.mock import patch

from scripts import s4_r7_bound_runtime as bound
from scripts.s4_r7_host_ipc import LocalPipeChannel, checked_pin, checked_query
from scripts.s4_r7_approval_source_v2 import strict_json
from scripts.s4_r7_release_journal import review_scope
from scripts.s4_synthetic_cost_wire import SyntheticBudgetStop
from scripts.s4_shared_transport_offline import SharedIntegrityError
from writscope.artifacts import digest,canonical_hash

ROOT=Path(__file__).resolve().parents[1]
BASE_CHECK,BASE_JOURNAL=bound.check_plan,bound.BoundJournal
LOCK=threading.Lock()


def sources():
    return {p:digest((ROOT/p).read_bytes()) for p in ('scripts/s4_r7_host_ipc.py','scripts/s4_r7_host_ipc_runtime.py')}


def extend(plan,pin,deadline=1.5):
    checked_pin(pin)
    return {**plan,'host_ipc':{'version':'s4_host_ipc_v1','sources':sources(),'pin':dict(pin),'deadline':deadline}}


def check_plan(plan,bundles,scripts):
    base=dict(plan);ext=base.pop('host_ipc',None)
    if type(ext) is not dict or set(ext)!={'version','sources','pin','deadline'}:
        raise SharedIntegrityError('host IPC binding absent')
    try:
        checked_pin(ext['pin']);LocalPipeChannel(ext['pin'],deadline=ext['deadline'])
    except (ValueError,PermissionError) as exc:raise SharedIntegrityError('host IPC pin/deadline invalid') from exc
    if ext['version']!='s4_host_ipc_v1' or ext['sources']!=sources():
        raise SharedIntegrityError('host IPC source drift')
    BASE_CHECK(base,bundles,scripts)


def query_for(plan,event_id,challenge,context):
    return {'version':'s4_host_bound_query_v3','event_id':event_id,'scope_sha256':canonical_hash(review_scope(plan)),
            'challenge':challenge,'context':context}


def validate_receipt(receipt,query,plan,proof=None):
    try:
        raw=base64.b64decode(receipt['captured_prefix_base64'],validate=True)
        ok=(receipt['version']=='s4_host_ipc_receipt_v1' and receipt['pin']==plan['host_ipc']['pin'] and
            receipt['deadline_seconds']==plan['host_ipc']['deadline'] and receipt['query_sha256']==digest(checked_query(query)) and
            receipt['challenge']==query['challenge'] and receipt['response_sha256']==digest(raw) and
            receipt['captured_bytes']==len(raw) and len(raw)<=32769 and receipt['actual_execution_approval'] is False and
            receipt['network_used'] is False and receipt['actual_credentials_read']==0 and
            type(receipt['elapsed_ms']) in (int,float) and math.isfinite(receipt['elapsed_ms']) and receipt['elapsed_ms']>=0 and
            type(receipt['pid']) is int and receipt['pid']>0 and type(receipt['exit_code']) is int and
            receipt['child_reaped'] is True and receipt['reader_stopped'] is True and receipt['writer_stopped'] is True and
            receipt['owner']=='windows_job_kill_on_close' and type(receipt['complete_response']) is bool)
        if not ok:raise ValueError('receipt fields')
        if receipt['complete_response']:
            expected={'server_pid':receipt['pin']['server_pid'],'image_sha256':receipt['pin']['image_sha256'],
                      'query_sha256':receipt['query_sha256']}
            if (receipt['error'] is not None or receipt['exit_code']!=0 or len(raw)>32768 or
                    receipt['metadata']!=[expected,{'declared_bytes':len(raw)}]):raise ValueError('complete frame')
        elif receipt['error'] not in ('host_total_deadline','host_response_size_limit',
                'host_worker_or_peer_failure','host_frame_integrity_failure','host_worker_protocol_failure'):
            raise ValueError('failure classification')
        if receipt['error']=='host_total_deadline' and receipt['elapsed_ms']+0.001<receipt['deadline_seconds']*1000:
            raise ValueError('unsubstantiated host deadline')
        if receipt['error']=='host_response_size_limit' and len(raw)!=32769:
            raise ValueError('unsubstantiated host size limit')
        if proof is not None and (not receipt['complete_response'] or strict_json(raw)!=proof['observation']):
            raise ValueError('signed observation/raw host byte mismatch')
    except (KeyError,TypeError,ValueError,PermissionError) as exc:
        raise SharedIntegrityError('host IPC receipt verification failed') from exc


class PipeJournal(BASE_JOURNAL):
    def __init__(self,path,plan,policy,source,event_id,clock,create=False):
        if (type(source.exchange) is not LocalPipeChannel or source.exchange.pin!=plan['host_ipc']['pin'] or
                source.exchange.deadline!=plan['host_ipc']['deadline']):
            raise PermissionError('bound local pipe channel required')
        self._ipc_ready=False
        super().__init__(path,plan,policy,source,event_id,clock,create)
        if create:
            with closing(self.connect()) as db:
                db.execute('CREATE TABLE host_ipc_observation (attempt INTEGER,phase TEXT,receipt TEXT,PRIMARY KEY(attempt,phase))')
                db.execute('CREATE TABLE host_ipc_failure (seq INTEGER PRIMARY KEY,context TEXT,receipt TEXT,error_kind TEXT)')
        self._ipc_ready=True;self.snapshot()

    def _save_host(self,index,phase,request_hash,proof):
        receipt=self.release_source.exchange.last_receipt
        context=proof['observation']['payload']['context']
        query=query_for(self.plan,self.event_id,proof['observation']['payload']['challenge'],context)
        validate_receipt(receipt,query,self.plan,proof)
        super()._save_host(index,phase,request_hash,proof)
        with closing(self.connect()) as db:
            db.execute('INSERT INTO host_ipc_observation VALUES (?,?,?)',(index,phase,json.dumps(receipt)))

    def save_failure(self,exc):
        # Failure is durable even if approval failed BEFORE any monetary reserve.
        receipt=self.release_source.exchange.last_receipt
        with closing(self.connect()) as db:
            db.execute('INSERT INTO host_ipc_failure(context,receipt,error_kind) VALUES (?,?,?)',
                (json.dumps(self.release_source.context),json.dumps(receipt),type(exc).__name__))

    def reserve(self,*args,**kwargs):
        try:return super().reserve(*args,**kwargs)
        except SyntheticBudgetStop:raise
        except BaseException as exc:
            self.save_failure(exc)
            if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
            raise SharedIntegrityError('host approval boundary failed before reserve/send; stop round') from exc

    def transition(self,attempt,before,after,**fields):
        try:return super().transition(attempt,before,after,**fields)
        except BaseException as exc:
            if after=='dispatch_started':self.save_failure(exc)
            raise

    def snapshot(self):
        snap=super().snapshot()
        if not self._ipc_ready:return snap
        with closing(self.connect()) as db:
            rows=db.execute('SELECT attempt,phase,receipt FROM host_ipc_observation ORDER BY attempt,phase').fetchall()
            proofs={(i,p):json.loads(v) for i,p,v in db.execute('SELECT attempt,phase,receipt FROM release_host_check')}
            failures=db.execute('SELECT context,receipt,error_kind FROM host_ipc_failure ORDER BY seq').fetchall()
        seen=set()
        for index,phase,text in rows:
            if (index,phase) not in proofs:raise SharedIntegrityError('orphan host IPC receipt')
            proof=proofs[(index,phase)];payload=proof['observation']['payload']
            validate_receipt(json.loads(text),query_for(self.plan,self.event_id,payload['challenge'],payload['context']),self.plan,proof)
            seen.add((index,phase))
        for attempt in snap['attempts']:
            if attempt['status']!='reserved' and not {(attempt['id'],'reserve'),(attempt['id'],'before_send')}<=seen:
                raise SharedIntegrityError('sent attempt lacks host IPC receipts')
        for context,text,kind in failures:
            context=json.loads(context);receipt=json.loads(text)
            if not kind or context is None:raise SharedIntegrityError('host failure metadata absent')
            if receipt is not None:
                validate_receipt(receipt,query_for(self.plan,self.event_id,receipt['challenge'],context),self.plan)
        return {**snap,'host_ipc_observations':len(rows),'host_ipc_failures':len(failures)}


class ReadOnlyPipeJournal(PipeJournal):
    def connect(self):return sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,isolation_level=None)


@contextmanager
def compatibility():
    if not LOCK.acquire(blocking=False):raise RuntimeError('single IPC integration entry already running')
    try:
        with patch.object(bound,'check_plan',check_plan),patch.object(bound,'BoundJournal',PipeJournal), \
             patch.object(bound,'ReadOnlyBoundJournal',ReadOnlyPipeJournal):yield
    finally:LOCK.release()


def run(*args,**kwargs):
    with compatibility():return bound.run(*args,**kwargs)


def evaluate(directory,bundles,scripts,source,event_id,clock):
    with compatibility():report=bound.evaluate(directory,bundles,scripts,source,event_id,clock)
    plan=json.loads((Path(directory)/'plan.json').read_text(encoding='utf-8'))
    journal=ReadOnlyPipeJournal(Path(directory)/'journal.sqlite',plan,plan['base']['policy'],source,event_id,clock)
    snap=journal.snapshot()
    report['host_ipc']={'sources':sources(),'observations':snap['host_ipc_observations'],
        'failures':snap['host_ipc_failures'],'network_used':False,'actual_execution_approval':False}
    return report


def start_live(*args,**kwargs):
    raise PermissionError('IPC offline integration only; live closed before all callbacks')
