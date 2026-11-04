"""Publication portability controls; Linux-only process proofs are explicit."""
import base64
from copy import deepcopy
import inspect
from io import BytesIO
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

from scripts import s4_public_linux_fixed as c
from scripts import s4_owner_pilot as base
from scripts import s4_owner_pilot_transport as transport


def receipt(*, version=c.RECEIPT_VERSION, guard=c.GUARD, mode='fixed'):
    wire, raw = b'fixed-request', b'{}'
    value={'version':version,'backend':mode,'endpoint':transport.ENDPOINT,
        'request_sha256':base.digest(wire),'response_sha256':base.digest(raw),'captured_bytes':len(raw),
        'http_status':200,'headers':{},'complete_response':True,'transport_error':None,
        'dispatch_permitted':True,'elapsed_ms':1.0,'cleanup_elapsed_ms':0.1,'deadline_seconds':90,
        'child_reaped':True,'reader_stopped':True,'writer_stopped':True,'exit_code':0,'parent_death_guard':guard}
    return wire,raw,value


def wire():
    return json.dumps({'model':'deepseek-flash','thinking':{'type':'disabled'},'temperature':0.0,
        'max_tokens':4096,'response_format':{'type':'json_object'},'tool_choice':'none','stream':False,
        'messages':[{'role':'system','content':'Fixed test only.'},{'role':'user','content':'Synthetic control.'}]}).encode()


def test_live_credential_or_missing_fixed_rejected_before_any_spawn(monkeypatch):
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('spawn before fixed gate'))
    for mode,key,fixed in [('live',None,{}),('fixed','generated-dummy',{}),('fixed',None,None)]:
        with pytest.raises(PermissionError,match='fixed_only'):
            c._receive(lambda *a,**k:pytest.fail('delegate before gate'),wire(),mode=mode,
                credential=key,fixed=fixed,on_send=lambda:pytest.fail('send before gate'))


def test_native_receipt_projection_keeps_native_bytes_and_full_legacy_checks():
    request,raw,native=receipt();before=deepcopy(native)
    c._validate(inspect.unwrap(base.validate_receipt),base.SharedIntegrityError,raw,native,request,'fixed',90)
    assert native==before and native['parent_death_guard']==c.GUARD
    bad=deepcopy(native);bad['response_sha256']='0'*64
    with pytest.raises(base.SharedIntegrityError):
        c._validate(inspect.unwrap(base.validate_receipt),base.SharedIntegrityError,raw,bad,request,'fixed',90)


def test_wrong_native_version_guard_backend_or_live_mode_is_rejected():
    request,raw,native=receipt()
    variants=[]
    for key,value in [('version','wrong'),('parent_death_guard',c.LEGACY_GUARD),('backend','live')]:
        bad=deepcopy(native);bad[key]=value;variants.append((bad,'fixed'))
    variants.append((native,'live'))
    for bad,mode in variants:
        with pytest.raises(base.SharedIntegrityError):
            c._validate(inspect.unwrap(base.validate_receipt),base.SharedIntegrityError,raw,bad,request,mode,90)


def test_historical_windows_receipts_delegate_without_any_projection():
    for mode in ('fixed','live'):
        request,raw,old=receipt(version=c.LEGACY_VERSION,guard=c.LEGACY_GUARD,mode=mode)
        observed=[]
        def delegate(*args):observed.append(args)
        c._validate(delegate,base.SharedIntegrityError,raw,old,request,mode,90)
        assert observed[0][1] is old
        inspect.unwrap(base.validate_receipt)(raw,old,request,mode,90)


def test_global_patches_lock_and_exception_restoration_and_windows_noop(monkeypatch):
    original_receive,original_validate=base.transport.receive,base.validate_receipt
    monkeypatch.setattr(c,'_windows',lambda:True)
    with c.compatibility():
        assert base.transport.receive is original_receive and base.validate_receipt is original_validate
    monkeypatch.setattr(c,'_windows',lambda:False);monkeypatch.setattr(c,'_linux',lambda:True)
    monkeypatch.setattr(c,'CONTEXT_LOCK',threading.Lock())
    with pytest.raises(RuntimeError,match='synthetic exit'):
        with c.compatibility():
            assert base.transport.receive is not original_receive
            with pytest.raises(RuntimeError,match='single_concurrency'):
                with c.compatibility():pass
            raise RuntimeError('synthetic exit')
    assert base.transport.receive is original_receive and base.validate_receipt is original_validate


def test_only_the_frozen_worker_argv_is_redirected_and_popen_restores(monkeypatch):
    calls=[]
    def popen(args,*positional,**kwargs):calls.append((args,kwargs));return object()
    monkeypatch.setattr(subprocess,'Popen',popen)
    request,raw,native=receipt();native['version']=c.LEGACY_VERSION
    def delegate(*args,**kwargs):
        subprocess.Popen([sys.executable,'-I',str(c._worker()),'--child'],stdout=subprocess.PIPE)
        subprocess.Popen([sys.executable,'-c','pass'])
        return raw,native
    _,actual=c._receive(delegate,request,mode='fixed',fixed={},on_send=lambda:None)
    assert calls[0][0][2]==str(Path(c.__file__).resolve()) and calls[0][0][-2:] == ['--fixed-child',str(os.getpid())]
    assert calls[0][1]['start_new_session'] is True and 'preexec_fn' not in calls[0][1]
    assert calls[1]==([sys.executable,'-c','pass'],{}) and subprocess.Popen is popen
    assert actual['version']==c.RECEIPT_VERSION and actual['parent_death_guard']==c.GUARD


def test_bootstrap_config_rejects_live_credentials_scripts_and_oversize():
    valid={'mode':'fixed','wire':base64.b64encode(wire()).decode(),'fixed':{'body_base64':'e30='}}
    assert c._configuration(BytesIO(json.dumps(valid).encode()+b'\n'),transport.check_wire)==valid
    variants=[{**valid,'mode':'live'},{**valid,'credential':'generated-dummy'},
        {**valid,'script':'not-allowed.py'},{**valid,'fixed':None}]
    for value in variants:
        with pytest.raises(PermissionError):c._configuration(BytesIO(json.dumps(value).encode()),transport.check_wire)
    with pytest.raises(ValueError):c._configuration(BytesIO(b'x'*1_500_001),transport.check_wire)


@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='actual Linux fixed-process proof, not exercised on Windows')
def test_linux_real_fixed_worker_native_receipt_and_reap():
    sent=[];request=wire()
    raw,native=c._receive(inspect.unwrap(transport.receive),request,mode='fixed',fixed={'body_base64':'e30='},
        on_send=lambda:sent.append(True),deadline=10)
    assert raw==b'{}' and sent==[True] and native['child_reaped'] and native['reader_stopped'] and native['writer_stopped']
    assert native['version']==c.RECEIPT_VERSION and native['parent_death_guard']==c.GUARD
    c._validate(inspect.unwrap(base.validate_receipt),base.SharedIntegrityError,raw,native,request,'fixed',10)


@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='actual Linux timeout/process-group proof')
def test_linux_real_total_timeout_kills_and_reaps_fixed_worker():
    request=wire();started=time.monotonic()
    raw,native=c._receive(inspect.unwrap(transport.receive),request,mode='fixed',
        fixed={'body_base64':'e30=','delay':3},on_send=lambda:None,deadline=1)
    assert native['transport_error']=='timeout' and native['child_reaped'] and native['reader_stopped'] and native['writer_stopped']
    assert time.monotonic()-started<5 and native['parent_death_guard']==c.GUARD
    c._validate(inspect.unwrap(base.validate_receipt),base.SharedIntegrityError,raw,native,request,'fixed',1)


@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='kernel parent-death SIGKILL proof requires Linux')
def test_linux_kernel_kills_fixed_worker_when_its_parent_dies(tmp_path):
    pidfile=tmp_path/'worker-pid.txt'
    config={'mode':'fixed','wire':base64.b64encode(wire()).decode(),'fixed':{'body_base64':'e30=','delay':30}}
    code="""import json,os,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-I',sys.argv[1],'--fixed-child',str(os.getpid())],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
child.stdin.write((sys.argv[3]+'\\n').encode());child.stdin.flush()
assert json.loads(child.stdout.readline())['kind']=='ready'
child.stdin.write(b'{"go":true,"credential":null}\\n');child.stdin.flush()
open(sys.argv[2],'w').write(str(child.pid))
time.sleep(30)
"""
    parent=subprocess.Popen([sys.executable,'-I','-c',code,str(Path(c.__file__).resolve()),str(pidfile),json.dumps(config)],
        stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    pid=None
    def alive(value):
        try:return Path('/proc/'+str(value)+'/stat').read_text().split(') ',1)[1].split()[0] not in ('Z','X')
        except FileNotFoundError:return False
    try:
        until=time.monotonic()+10
        while not pidfile.exists() and parent.poll() is None and time.monotonic()<until:time.sleep(.02)
        assert pidfile.exists(),'fixed bootstrap did not reach ready/send phase'
        pid=int(pidfile.read_text());assert alive(pid)
        parent.kill();parent.wait(timeout=3)
        until=time.monotonic()+5
        while alive(pid) and time.monotonic()<until:time.sleep(.02)
        assert not alive(pid),'worker survived its parent despite PDEATHSIG'
    finally:
        if parent.poll() is None:parent.kill();parent.wait(timeout=3)
        if pid is not None and alive(pid):
            if os.getpgid(pid)==pid:os.killpg(pid,signal.SIGKILL)
        if parent.stderr is not None:parent.stderr.close()


def test_overlay_descriptor_or_hash_tamper_rejected_before_destination(tmp_path,monkeypatch):
    from scripts import s4_public_release as release
    root=tmp_path/'package';root.mkdir();(root/'overlay.py').write_bytes(b'new source')
    monkeypatch.setattr(release,'RUNTIME_OVERLAY_FILES',('overlay.py',))
    monkeypatch.setattr(release,'checked',lambda _:({'files':{},'frozen_files':{}},{}))
    file=root/release.BUNDLE/'publish-files.json';file.parent.mkdir(parents=True)
    overlay={'version':release.RUNTIME_OVERLAY_VERSION,'real_transport_allowed':False,'purpose':'synthetic fixed test','files':{'overlay.py':'0'*64}}
    file.write_text(json.dumps({'runtime_overlay':overlay}),encoding='utf8')
    destination=tmp_path/'restored'
    with pytest.raises(ValueError,match='hash_mismatch'):release.restore(destination,root)
    assert not destination.exists()
    overlay['version']='wrong';file.write_text(json.dumps({'runtime_overlay':overlay}),encoding='utf8')
    with pytest.raises(ValueError,match='invalid_fixed_platform_overlay'):release.restore(destination,root)
    assert not destination.exists()


def test_overlay_cannot_replace_any_frozen_original_before_destination(tmp_path,monkeypatch):
    from scripts import s4_public_release as release
    root=tmp_path/'package';root.mkdir();raw=b'original';(root/'original.py').write_bytes(raw)
    monkeypatch.setattr(release,'RUNTIME_OVERLAY_FILES',('original.py',))
    monkeypatch.setattr(release,'checked',lambda _:({'files':{},'frozen_files':{'original.py':release.digest(raw)}},{}))
    file=root/release.BUNDLE/'publish-files.json';file.parent.mkdir(parents=True)
    overlay={'version':release.RUNTIME_OVERLAY_VERSION,'real_transport_allowed':False,'purpose':'synthetic fixed test',
        'files':{'original.py':release.digest(raw)}}
    file.write_text(json.dumps({'runtime_overlay':overlay}),encoding='utf8');destination=tmp_path/'restored'
    with pytest.raises(ValueError,match='cannot_replace_frozen_originals'):release.restore(destination,root)
    assert not destination.exists() and (root/'original.py').read_bytes()==raw
