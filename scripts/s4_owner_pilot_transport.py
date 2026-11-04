"""Owner-pilot HTTP process boundary. No credential lookup or approval policy.

The parent owns the deadline and Windows kill-on-close job. The child sees
only one wire and, after a durable send marker, one in-memory credential.
Fixed responses are a separate test backend, never an input to live mode.
"""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

ENDPOINT = 'https://api.deepseek.com/chat/completions'
MAX_BODY = 100000
ROOT = Path(__file__).resolve().parents[1]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def check_wire(wire):
    if type(wire) is not bytes or len(wire) > 500000:
        raise ValueError('request_byte_limit_no_truncation')
    body = json.loads(wire)
    messages = body.pop('messages', None)
    if (body != {'model': 'deepseek-flash', 'thinking': {'type': 'disabled'}, 'temperature': 0.0,
                 'max_tokens': 4096, 'response_format': {'type': 'json_object'},
                 'tool_choice': 'none', 'stream': False} or
            not isinstance(messages, list) or len(messages) != 2 or
            [m.get('role') for m in messages] != ['system', 'user'] or
            any(set(m) != {'role', 'content'} or type(m['content']) is not str for m in messages)):
        raise ValueError('unbound_wire_configuration')


def frame(kind, **fields):
    raw = json.dumps({'kind': kind, **fields}, separators=(',', ':')).encode()+b'\n'
    if len(raw) > 16384:
        raise ValueError('worker_frame_limit')
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def http_client(mode, fixed=None):
    import httpx
    if mode == 'fixed':
        if not isinstance(fixed, dict):
            raise ValueError('fixed_fixture_missing')
        def handler(request):
            if str(request.url) != ENDPOINT or 'authorization' in request.headers:
                raise ValueError('fixed_request_boundary')
            time.sleep(fixed.get('delay', 0))
            if fixed.get('fault') == 'timeout':
                raise httpx.ReadTimeout('fixed timeout')
            if fixed.get('fault') == 'connection_error':
                raise httpx.ConnectError('fixed connection failure')
            body = base64.b64decode(fixed['body_base64'], validate=True)
            class FixedStream(httpx.SyncByteStream):
                def __iter__(self):
                    yield body
                    if fixed.get('after_prefix_timeout'):
                        raise httpx.ReadTimeout('fixed partial response timeout')
            return httpx.Response(fixed.get('status', 200), headers=fixed.get('headers', {}), stream=FixedStream())
        transport = httpx.MockTransport(handler)
    elif mode == 'live' and fixed is None:
        transport = httpx.HTTPTransport(verify=True, trust_env=False, retries=0, http2=False,
                                       limits=httpx.Limits(max_connections=1, max_keepalive_connections=0))
    else:
        raise PermissionError('unknown_or_mixed_transport')
    return httpx.Client(transport=transport, trust_env=False, follow_redirects=False, timeout=90)


def child_main(config):
    import httpx
    mode = config['mode']
    expected = {'mode', 'wire'} | ({'fixed'} if mode == 'fixed' else set())
    if set(config) != expected or mode not in ('fixed', 'live'):
        raise PermissionError('worker_mode_boundary')
    wire = base64.b64decode(config['wire'], validate=True)
    check_wire(wire)
    frame('ready', request_sha256=sha(wire))
    go = json.loads(sys.stdin.buffer.readline(4097))
    if set(go) != {'go', 'credential'} or go['go'] is not True:
        raise PermissionError('no_dispatch_permit')
    key = go['credential']
    if (mode == 'fixed' and key is not None or mode == 'live' and
            (type(key) is not str or not 1 <= len(key) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in key))):
        raise PermissionError('credential_boundary')
    headers = {'Content-Type': 'application/json', 'Accept-Encoding': 'identity'}
    if mode == 'live':
        headers['Authorization'] = 'Bearer '+key
    error = None
    try:
        with http_client(mode, config.get('fixed')) as client:
            with client.stream('POST', ENDPOINT, content=wire, headers=headers) as response:
                frame('headers', status=response.status_code,
                      headers={k: v for k, v in response.headers.items() if k in
                               ('content-type', 'date', 'x-request-id', 'request-id')})
                if response.headers.get('content-encoding', 'identity') != 'identity':
                    error = 'unsupported_content_encoding'
                else:
                    for chunk in response.iter_raw():
                        for offset in range(0, len(chunk), 4096):
                            frame('chunk', data=base64.b64encode(chunk[offset:offset+4096]).decode())
    except httpx.TimeoutException:
        error = 'timeout'
    except httpx.HTTPError:
        error = 'connection_error'
    # No exception text, URL parameters, headers, configuration or key is logged.
    frame('done', error=error)


def receive(wire, *, mode, on_send, credential=None, fixed=None, deadline=90):
    check_wire(wire)
    if (type(deadline) not in (float, int) or not math.isfinite(deadline) or not 0 < deadline <= 90 or
            mode not in ('fixed', 'live') or (mode == 'fixed' and (credential is not None or fixed is None)) or
            (mode == 'live' and (fixed is not None or type(credential) is not str or not credential))):
        raise PermissionError('transport_boundary_or_deadline')
    config = {'mode': mode, 'wire': base64.b64encode(wire).decode()}
    if mode == 'fixed':
        config['fixed'] = fixed
    data = json.dumps(config).encode()+b'\n'
    if len(data) > 1500000:
        raise ValueError('worker_configuration_limit')
    env = {k: os.environ[k] for k in ('SystemRoot', 'WINDIR', 'TEMP', 'TMP') if k in os.environ}
    started = time.monotonic()
    child = subprocess.Popen([sys.executable, '-I', str(Path(__file__).resolve()), '--child'],
             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
             cwd=str(ROOT), env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    from scripts.s4_r7_process_guard import WorkerJob
    try:
        ownership = WorkerJob(child)
    except BaseException:
        child.kill(); child.wait(timeout=5)
        child.stdin.close(); child.stdout.close()
        raise
    messages, commands, stop = queue.Queue(2), queue.Queue(2), threading.Event()
    commands.put(data)
    def read():
        try:
            while not stop.is_set():
                line = child.stdout.readline(16385)
                while not stop.is_set():
                    try:
                        messages.put(line, timeout=.05); break
                    except queue.Full:
                        pass
                if not line or len(line) > 16384:
                    return
        except (OSError, ValueError):
            pass
    def write():
        try:
            while not stop.is_set():
                try:
                    part = commands.get(timeout=.05)
                except queue.Empty:
                    continue
                child.stdin.write(part); child.stdin.flush()
        except (OSError, ValueError):
            pass
    reader, writer = threading.Thread(target=read, daemon=True), threading.Thread(target=write, daemon=True)
    reader.start(); writer.start()
    raw, status, headers = bytearray(), None, {}
    permitted = ready = done = False
    error = None
    try:
        while not done:
            remaining = deadline-(time.monotonic()-started)
            if remaining <= 0:
                error = 'timeout'; break
            try:
                line = messages.get(timeout=remaining)
            except queue.Empty:
                error = 'timeout'; break
            if not line or len(line) > 16384 or not line.endswith(b'\n'):
                error = 'worker_protocol_failure'; break
            msg = json.loads(line)
            kind = msg.pop('kind')
            if kind == 'ready' and not ready and msg == {'request_sha256': sha(wire)}:
                ready = True
                on_send()  # Durable dispatch_started BEFORE the credential/go frame.
                permitted = True
                if time.monotonic()-started >= deadline:
                    error = 'timeout'; break
                commands.put(json.dumps({'go': True, 'credential': credential}).encode()+b'\n')
            elif kind == 'headers' and permitted and status is None:
                status, headers = msg['status'], msg['headers']
                if credential and any(credential in value for value in headers.values()):
                    headers = {}; error = 'credential_reflection_withheld'; break
            elif kind == 'chunk' and status is not None:
                chunk = base64.b64decode(msg['data'], validate=True)
                if len(chunk) > 4096:
                    raise ValueError('worker_chunk_limit')
                raw.extend(chunk[:MAX_BODY+1-len(raw)])
                if credential and credential.encode() in raw:
                    raw.clear(); error = 'credential_reflection_withheld'; break
                if len(raw) > MAX_BODY:
                    error = 'response_size_exceeded'; break
            elif kind == 'done' and permitted:
                done, error = True, msg['error']
            else:
                error = 'worker_protocol_failure'; break
        if done:
            try:
                child.wait(timeout=max(.001, deadline-(time.monotonic()-started)))
            except subprocess.TimeoutExpired:
                error = 'timeout'
    except (ValueError, KeyError, TypeError):
        error = 'worker_protocol_failure'
    finally:
        stop.set()
        elapsed = (time.monotonic()-started)*1000
        ownership.close()
        child.wait(timeout=5)
        reader.join(timeout=1); writer.join(timeout=1)
        child.stdout.close(); child.stdin.close()
    if elapsed >= deadline*1000:
        error = 'timeout'
    if child.returncode != 0 and error is None:
        error = 'worker_protocol_failure'
    raw = bytes(raw)
    return raw, {'version': 's4_owner_pilot_transport_v1', 'backend': mode, 'endpoint': ENDPOINT,
        'request_sha256': sha(wire), 'response_sha256': sha(raw), 'captured_bytes': len(raw),
        'http_status': status, 'headers': headers, 'complete_response': done and error is None,
        'transport_error': error, 'dispatch_permitted': permitted, 'elapsed_ms': elapsed,
        'cleanup_elapsed_ms': (time.monotonic()-started)*1000-elapsed, 'deadline_seconds': deadline,
        'child_reaped': child.poll() is not None, 'reader_stopped': not reader.is_alive(),
        'writer_stopped': not writer.is_alive(), 'exit_code': child.returncode,
        'parent_death_guard': ownership.kind}


if __name__ == '__main__':
    if sys.argv[1:] != ['--child']:
        raise SystemExit('Use the owner-pilot entry; this worker does not read keys.')
    try:
        raw_config = sys.stdin.buffer.readline(1500001)
        if len(raw_config) > 1500000:
            raise ValueError('configuration_limit')
        child_main(json.loads(raw_config))
    except BaseException:
        sys.exit(2)
