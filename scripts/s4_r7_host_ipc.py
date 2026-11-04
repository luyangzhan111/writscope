"""Bounded local Windows host IPC. No host service, key store or live model path.

Synchronous Win32 pipe I/O runs in a fixed, owned child process. Its parent
enforces total time/body limits and reaps it, including indefinitely blocked I/O.
Peer PID/image pinning complements (does not replace) the host signature.
"""
import argparse
import base64
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time

from scripts.s4_r7_approval_source_v2 import canonical_bytes
from scripts.s4_r7_bound_approval_v3 import check_context
from scripts.s4_r7_process_guard import WorkerJob
from writscope.artifacts import digest

MAX_BODY=32768


def api():
    if os.name!='nt':raise PermissionError('Windows local pipe required')
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    for name,args,result in (
        ('CreateFileW',[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE],wintypes.HANDLE),
        ('WaitNamedPipeW',[wintypes.LPCWSTR,wintypes.DWORD],wintypes.BOOL),
        ('GetNamedPipeServerProcessId',[wintypes.HANDLE,ctypes.POINTER(wintypes.ULONG)],wintypes.BOOL),
        ('PeekNamedPipe',[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p,ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p],wintypes.BOOL),
        ('ReadFile',[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p],wintypes.BOOL),
        ('WriteFile',[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p],wintypes.BOOL),
        ('CloseHandle',[wintypes.HANDLE],wintypes.BOOL),
        ('OpenProcess',[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD],wintypes.HANDLE),
        ('QueryFullProcessImageNameW',[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)],wintypes.BOOL)):
        f=getattr(k,name);f.argtypes=args;f.restype=result
    return k


def image_sha256(pid):
    k=api();handle=k.OpenProcess(0x1000,False,pid)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    try:
        text=ctypes.create_unicode_buffer(32768);size=wintypes.DWORD(len(text))
        if not k.QueryFullProcessImageNameW(handle,0,text,ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return digest(Path(text.value).read_bytes())
    finally:k.CloseHandle(handle)


def checked_pin(pin):
    if (type(pin) is not dict or set(pin)!={'pipe','server_pid','image_sha256','deployment'} or
            type(pin['pipe']) is not str or not re.fullmatch(r'\\\\\.\\pipe\\writscope-host-[0-9a-f]{32}',pin['pipe']) or
            type(pin['server_pid']) is not int or pin['server_pid']<=0 or
            type(pin['image_sha256']) is not str or not re.fullmatch('[0-9a-f]{64}',pin['image_sha256']) or
            pin['deployment']!='test_only'):
        raise PermissionError('explicit test-only local host pin required; production not installed')


def checked_query(query):
    if (type(query) is not dict or set(query)!={'version','event_id','scope_sha256','challenge','context'} or
            query['version']!='s4_host_bound_query_v3' or type(query['event_id']) is not str or
            not 0<len(query['event_id'])<=128 or
            any(type(query[k]) is not str or not re.fullmatch('[0-9a-f]{64}',query[k]) for k in ('scope_sha256','challenge'))):
        raise PermissionError('host query metadata whitelist violation')
    check_context(query['context'])
    raw=canonical_bytes(query)
    if len(raw)>8192:raise PermissionError('host query byte limit')
    return raw


def read_exact(k,handle,count):
    data=bytearray()
    while len(data)<count:
        buf=ctypes.create_string_buffer(min(4096,count-len(data)));n=wintypes.DWORD()
        if not k.ReadFile(handle,buf,len(buf),ctypes.byref(n),None) or not n.value:
            raise EOFError('host pipe truncated')
        data.extend(buf.raw[:n.value])
    return bytes(data)


def write_all(k,handle,raw):
    pos=0
    while pos<len(raw):
        buf=ctypes.create_string_buffer(raw[pos:]);n=wintypes.DWORD()
        if not k.WriteFile(handle,buf,len(raw)-pos,ctypes.byref(n),None) or not n.value:
            raise OSError('host pipe write failed')
        pos+=n.value


def child():
    # Fixed grammar and local named pipe only; no arbitrary plugin, shell or URL.
    config=json.loads(sys.stdin.buffer.read(12289))
    if set(config)!={'pin','query'}:raise PermissionError('worker config invalid')
    pin,query=config['pin'],config['query'];checked_pin(pin);wire=checked_query(query);k=api()
    handle=wintypes.HANDLE(-1).value
    while handle==wintypes.HANDLE(-1).value:
        handle=k.CreateFileW(pin['pipe'],0xC0000000,0,None,3,0x00110000,None)
        if handle==wintypes.HANDLE(-1).value:
            if ctypes.get_last_error() not in (2,231):raise ctypes.WinError(ctypes.get_last_error())
            k.WaitNamedPipeW(pin['pipe'],50)
    try:
        pid=wintypes.ULONG()
        if not k.GetNamedPipeServerProcessId(handle,ctypes.byref(pid)):raise OSError('peer PID unavailable')
        peer={'server_pid':pid.value,'image_sha256':image_sha256(pid.value),'query_sha256':digest(wire)}
        if pid.value!=pin['server_pid'] or peer['image_sha256']!=pin['image_sha256']:
            raise PermissionError('host peer pin mismatch')
        sys.stdout.buffer.write(canonical_bytes(peer)+b'\n');sys.stdout.buffer.flush()
        write_all(k,handle,len(wire).to_bytes(4,'big')+wire)
        length=int.from_bytes(read_exact(k,handle,4),'big')
        sys.stdout.buffer.write(canonical_bytes({'declared_bytes':length})+b'\n');sys.stdout.buffer.flush()
        remaining=min(length,MAX_BODY+1)
        while remaining:
            # A partial pipe read is flushed immediately; preserve it on timeout.
            available=wintypes.DWORD()
            if not k.PeekNamedPipe(handle,None,0,None,ctypes.byref(available),None):
                raise EOFError('host response pipe closed')
            buf=ctypes.create_string_buffer(min(4096,remaining,max(1,available.value)));n=wintypes.DWORD()
            if not k.ReadFile(handle,buf,len(buf),ctypes.byref(n),None) or not n.value:
                raise EOFError('host response truncated')
            sys.stdout.buffer.write(buf.raw[:n.value]);sys.stdout.buffer.flush();remaining-=n.value
        if length>MAX_BODY:raise ValueError('host response size limit')
    finally:k.CloseHandle(handle)


class LocalPipeChannel:
    def __init__(self,pin,*,deadline=1.5):
        checked_pin(pin)
        if type(deadline) not in (int,float) or not math.isfinite(deadline) or not 0<deadline<=1.5:
            raise ValueError('host IPC deadline must be in (0,1.5]')
        self.pin,self.deadline=dict(pin),deadline
        self.last_receipt=None

    def __call__(self,query):
        self.last_receipt=None
        wire=checked_query(query);started=time.monotonic();stop=threading.Event();events=queue.Queue(maxsize=1)
        env={k:os.environ[k] for k in ('SystemRoot','WINDIR','TEMP','TMP') if k in os.environ}
        # -I excludes caller Python paths; only this checked module is invoked.
        code="import sys;sys.path[:0]="+repr([str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'src')])+";from scripts.s4_r7_host_ipc import child;child()"
        process=subprocess.Popen([sys.executable,'-I','-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,bufsize=0,env=env,creationflags=0x08000000)
        owner=None;body=bytearray();header=bytearray();metadata=[];error=None
        def writer():
            try:
                data=canonical_bytes({'pin':self.pin,'query':query});pos=0
                while pos<len(data):
                    n=process.stdin.write(data[pos:])
                    if not n:raise OSError('host worker stdin closed')
                    pos+=n
            except (OSError,ValueError):pass
            finally:
                try:process.stdin.close()
                except (OSError,ValueError):pass
        def reader():
            try:
                while not stop.is_set():
                    chunk=process.stdout.read(4096)
                    while not stop.is_set():
                        try:events.put(chunk,timeout=.02);break
                        except queue.Full:pass
                    if not chunk:break
            except (OSError,ValueError):pass
        read_thread=threading.Thread(target=reader,daemon=True);write_thread=threading.Thread(target=writer,daemon=True)
        try:
            owner=WorkerJob(process);write_thread.start();read_thread.start()
            while True:
                remaining=self.deadline-(time.monotonic()-started)
                if remaining<=0:error='host_total_deadline';break
                try:chunk=events.get(timeout=remaining)
                except queue.Empty:error='host_total_deadline';break
                if not chunk:break
                if len(metadata)<2:
                    header.extend(chunk)
                    while len(metadata)<2 and b'\n' in header:
                        line,_,rest=header.partition(b'\n');header=bytearray(rest)
                        if len(line)>1024:raise ValueError('host worker header limit')
                        metadata.append(json.loads(line))
                    if len(metadata)<2:
                        if len(header)>1024:raise ValueError('host worker header limit')
                        continue
                    chunk=bytes(header);header.clear()
                body.extend(chunk[:MAX_BODY+1-len(body)])
                if len(body)>MAX_BODY:error='host_response_size_limit';break
            if error is None:
                try:process.wait(timeout=max(.001,self.deadline-(time.monotonic()-started)))
                except subprocess.TimeoutExpired:error='host_total_deadline'
            if error is None and (process.returncode!=0 or len(metadata)!=2):error='host_worker_or_peer_failure'
            if error is None and (metadata[0]!={'server_pid':self.pin['server_pid'],'image_sha256':self.pin['image_sha256'],'query_sha256':digest(wire)} or
                    metadata[1]!={'declared_bytes':len(body)}):error='host_frame_integrity_failure'
        except BaseException as exc:
            error=error or 'host_worker_protocol_failure'
            if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
        finally:
            stop.set()
            if process.poll() is None:process.kill()
            process.wait(timeout=5)
            if read_thread.ident is not None:read_thread.join(timeout=1)
            if write_thread.ident is not None:write_thread.join(timeout=1)
            else:process.stdin.close()
            if owner is not None:owner.close()
            if not read_thread.is_alive():process.stdout.close()
            raw=bytes(body)
            self.last_receipt={'version':'s4_host_ipc_receipt_v1','pin':self.pin,'query_sha256':digest(wire),
                'challenge':query['challenge'],'captured_prefix_base64':base64.b64encode(raw).decode(),
                'response_sha256':digest(raw),'captured_bytes':len(raw),'complete_response':error is None,
                'metadata':metadata,'error':error,'elapsed_ms':(time.monotonic()-started)*1000,
                'deadline_seconds':self.deadline,'pid':process.pid,'exit_code':process.returncode,
                'child_reaped':process.poll() is not None,'reader_stopped':not read_thread.is_alive(),
                'writer_stopped':not write_thread.is_alive(),'owner':'windows_job_kill_on_close' if owner else 'unavailable',
                'actual_execution_approval':False,'network_used':False,'actual_credentials_read':0}
        if error is not None or not self.last_receipt['reader_stopped'] or not self.last_receipt['writer_stopped']:
            raise PermissionError('bounded host IPC failed: '+str(error))
        return raw


def installed_production_channel():
    raise PermissionError('production host pipe/service/pin not installed; live closed')
