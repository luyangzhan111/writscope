"""Cancellable subprocess/size boundary. Fixed offline worker only; no provider."""
import argparse
import base64
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


def receive_fixed(fixture,*,deadline=90,max_bytes=100000):
    if deadline<=0 or type(max_bytes) is not int or max_bytes<1:raise ValueError('invalid boundary')
    # Only this fixed worker is invocable; not an arbitrary command/shell runner.
    started=time.monotonic()
    child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker',str(Path(fixture).resolve())],
                           stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,bufsize=0)
    data=bytearray();events=queue.Queue(maxsize=1);stop=threading.Event()
    def read():
        try:
            while not stop.is_set():
                chunk=child.stdout.read(min(8192,max_bytes+1))
                while not stop.is_set():
                    try:events.put(chunk,timeout=.05);break
                    except queue.Full:pass
                if not chunk:break
        except (OSError,ValueError):pass
    reader=threading.Thread(target=read,daemon=True);reader.start();error=None
    try:
        while True:
            remaining=deadline-(time.monotonic()-started)
            if remaining<=0:error='total_deadline';break
            try:chunk=events.get(timeout=remaining)
            except queue.Empty:error='total_deadline';break
            if not chunk:break
            data.extend(chunk[:max_bytes+1-len(data)])
            if len(data)>max_bytes:error='response_size_limit';break
        if error is None:
            try:child.wait(timeout=max(.001,deadline-(time.monotonic()-started)))
            except subprocess.TimeoutExpired:error='total_deadline'
    finally:
        stop.set()
        if child.poll() is None:child.kill()
        child.wait(timeout=5)
        reader.join(timeout=1);child.stdout.close()
    if child.returncode and error is None:error='worker_failed'
    return {'version':'s4_r7_bounded_receive_v1','execution_kind':'fixed_offline_worker',
            'captured_prefix_base64':base64.b64encode(data).decode(),'captured_bytes':len(data),
            'complete_response':error is None,'error':error,'child_reaped':child.poll() is not None,
            'elapsed_seconds':time.monotonic()-started,'deadline_seconds':deadline,
            'actual_model_calls':0,'real_transport_allowed':False}


def dispatch_fixed(journal,job,request,fixture,*,deadline=90,max_bytes=100000):
    from scripts.s4_synthetic_cost_wire import mock_wire
    from scripts.s4_r6_transport_contract import decode, normalized_usage, stop_reason
    from scripts.s4_unified_journal import now
    from scripts.s4_shared_transport_offline import SharedIntegrityError
    from writscope.artifacts import digest
    reason=stop_reason(journal.snapshot()['attempts'])
    if reason:raise SharedIntegrityError(reason)
    wire=mock_wire(request)
    # CnyJournal requires a separately registered request-bound admission.
    ident=journal.reserve(job,request['stage'],wire,'lookup_response' in request['public_input'])
    try:journal.transition(ident,'reserved','dispatch_started',sent_at=now())
    except Exception as exc:raise SharedIntegrityError('send persistence') from exc
    try:receipt=receive_fixed(fixture,deadline=deadline,max_bytes=max_bytes)
    except BaseException:
        # Dispatch remains unresolved; no reservation is released.
        raise
    raw=base64.b64decode(receipt['captured_prefix_base64'])
    payload=finish=None;usage=normalized_usage(None)
    error={'total_deadline':'timeout','response_size_limit':'response_size_exceeded','worker_failed':'connection_error'}.get(receipt['error'])
    if receipt['complete_response']:
        bound=journal._admissions[digest(wire)]['input_tokens']
        payload,finish,error,usage=decode(raw,200,request['stage'],request['lookup_allowed'],bound)
    usage['bounded_receive']=receipt
    try:
        journal.transition(ident,'dispatch_started','received',received_at=now(),response=raw,
                           response_hash=digest(raw),http_status=200 if receipt['complete_response'] else None)
        journal.transition(ident,'received','failed' if error else 'completed',error_kind=error,
                           finish_reason=finish,validation='rejected' if error else 'accepted',usage=json.dumps(usage))
    except Exception as exc:raise SharedIntegrityError('response persistence') from exc
    reason=stop_reason(journal.snapshot()['attempts'])
    if reason:raise SharedIntegrityError(reason)
    if error:raise ValueError(error)
    return payload


def worker(path):
    # Reviewed test-data grammar only. No code execution or credential reading.
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    if value.get('kind')!='fixed_offline_bytes':raise ValueError('fixed fixture required')
    delay=value.get('delay_seconds',0)
    if not 0<=delay<=120:raise ValueError('bad delay')
    time.sleep(delay)
    data=base64.b64decode(value['body_base64'],validate=True)
    if len(data)>2000000:raise ValueError('fixture too large')
    sys.stdout.buffer.write(data);sys.stdout.buffer.flush()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--worker',type=Path,required=True)
    worker(parser.parse_args().worker)
