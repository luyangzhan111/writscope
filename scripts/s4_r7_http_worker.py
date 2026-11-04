"""Bounded subprocess running HTTPX/HTTPCore/H11, with a fixed byte backend.

Production entry is CLOSED. Test backend substitutes the network stream, not
the HTTP request serializer/parser. No sockets, credential lookup or TLS claim.
"""
import base64
from contextlib import ExitStack
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

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = 'https://api.deepseek.com/chat/completions'
SOURCE = 'offline_http_stack_worker_v1'
MAX_BODY = 100000
MAX_FRAME = 16384


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def start_live(*args, **kwargs):
    raise PermissionError('production HTTP worker closed before credentials or process launch')


def _frame(kind, **fields):
    raw = json.dumps({'kind': kind, **fields}, separators=(',', ':')).encode()+b'\n'
    if len(raw) > MAX_FRAME:
        raise ValueError('worker frame limit')
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def _child(config):
    if config.get('mode') != 'offline_http_stack':
        start_live()
    sys.path[:0] = [str(ROOT), str(ROOT/'src')]
    import httpx
    import httpcore
    from unittest.mock import patch
    if (httpx.__version__, httpcore.__version__) != ('0.28.1', '1.0.9'):
        raise ValueError('HTTP stack version drift')
    wire = base64.b64decode(config['wire'], validate=True)
    if len(wire) > 500000 or sha(wire) != config['request_sha256']:
        raise ValueError('worker request binding')
    path = Path(config['fixture'])
    raw_fixture = path.read_bytes()
    if len(raw_fixture) > 10000000 or sha(raw_fixture) != config['fixture_sha256']:
        raise ValueError('fixture binding drift')
    dataset = json.loads(raw_fixture)
    if dataset['version'] != 's4_http_fixture_v1':
        raise ValueError('unknown fixture format')
    try:
        fixture = dataset['jobs'][config['job']][config['position']]
    except (KeyError, IndexError):
        fixture = {'status': 503, 'body_base64': base64.b64encode(b'{}').decode()}
    def delay(phase):
        seconds = fixture.get(phase+'_delay', 0)
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 <= seconds <= 120:
            raise ValueError('invalid fixed delay')
        time.sleep(seconds)
    delay('startup')
    _frame('ready', request_sha256=sha(wire), fixture_sha256=sha(raw_fixture))
    if sys.stdin.buffer.readline(32) != b'go\n':
        raise ValueError('parent did not authorize dispatch')

    class Stream(httpcore.NetworkStream):
        def __init__(self):
            self.written = bytearray()
            self.parts = None
            self.index = 0
            self.sni = None
            self.request_proved = False

        def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            if server_hostname != 'api.deepseek.com' or ssl_context.verify_mode != 2 or not ssl_context.check_hostname:
                raise ValueError('TLS configuration mismatch')
            self.sni = server_hostname
            delay('tls')
            return self  # Test stream; does NOT perform cryptographic TLS.

        def write(self, buffer, timeout=None):
            self.written.extend(buffer)
            if len(self.written) > len(wire)+16384:
                raise ValueError('unexpected HTTP request length')
            if b'\r\n\r\n' not in self.written or self.request_proved:
                return
            headers, body = bytes(self.written).split(b'\r\n\r\n', 1)
            if len(body) < len(wire):
                return
            if (body != wire or not headers.startswith(b'POST /chat/completions HTTP/1.1\r\n') or
                    b'host: api.deepseek.com' not in headers.lower() or b'authorization:' in headers.lower()):
                raise ValueError('serialized HTTP request mismatch')
            self.request_proved = True
            _frame('request', request_sha256=sha(body), tls_server_hostname=self.sni,
                   request_line='POST /chat/completions HTTP/1.1', authorization_present=False)

        def read(self, max_bytes, timeout=None):
            if not self.request_proved:
                raise ValueError('response requested before full request written')
            if self.parts is None:
                delay('headers')
                data = base64.b64decode(fixture['body_base64'], validate=True)
                if len(data) > 2000000:
                    raise ValueError('fixture body too large')
                size = fixture.get('chunk_size', 4096)
                if type(size) is not int or not 1 <= size <= 8192:
                    raise ValueError('fixture chunk size')
                status = fixture.get('status', 200)
                if type(status) is not int or not 100 <= status <= 599:
                    raise ValueError('fixture HTTP status')
                length = len(data)+fixture.get('extra_declared_bytes', 0)
                fields = {'Content-Length': str(length), 'Content-Type': 'application/json', **fixture.get('headers', {})}
                if any('\r' in str(v) or '\n' in str(v) for v in [*fields.keys(), *fields.values()]):
                    raise ValueError('invalid fixture HTTP header')
                head = f'HTTP/1.1 {status} Fixed\r\n'+''.join(f'{k}: {v}\r\n' for k, v in fields.items())+'\r\n'
                self.parts = [head.encode('ascii')] + [data[n:n+size] for n in range(0, len(data), size)]
            if self.index == fixture.get('block_before_part', -1):
                delay('body')
            if self.index >= len(self.parts):
                return b''
            part = self.parts[self.index]
            if len(part) > max_bytes:
                self.parts[self.index] = part[max_bytes:]
                return part[:max_bytes]
            self.index += 1
            return part

        def close(self):
            pass

    class Backend(httpcore.NetworkBackend):
        def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            if host != 'api.deepseek.com' or port != 443:
                raise ValueError('network destination mismatch')
            delay('connect')
            return Stream()

    error = None
    def no_socket(*args, **kwargs):
        raise AssertionError('worker forbids socket connections')
    with ExitStack() as guards:
        for target in ('socket.create_connection', 'socket.socket.connect', 'socket.socket.connect_ex'):
            guards.enter_context(patch(target, no_socket))
        transport = httpx.HTTPTransport(verify=True, trust_env=False, retries=0, http1=True, http2=False,
                                       limits=httpx.Limits(max_connections=1, max_keepalive_connections=0))
        transport._pool._network_backend = Backend()  # Version-pinned private seam; never a real socket backend.
        try:
            with httpx.Client(transport=transport, trust_env=False, follow_redirects=False, timeout=90) as client:
                with client.stream('POST', ENDPOINT, content=wire,
                        headers={'Content-Type': 'application/json', 'Accept-Encoding': 'identity'}) as response:
                    headers = {k: v for k, v in response.headers.items() if k in ('content-type', 'date', 'x-request-id', 'request-id')}
                    _frame('headers', status=response.status_code, headers=headers)
                    if response.headers.get('content-encoding', 'identity') != 'identity':
                        error = 'unsupported_content_encoding'
                    else:
                        for chunk in response.iter_raw():
                            for n in range(0, len(chunk), 4096):
                                _frame('chunk', data=base64.b64encode(chunk[n:n+4096]).decode())
        except httpx.TimeoutException:
            error = 'timeout'
        except httpx.HTTPError:
            error = 'connection_error'
        _frame('done', error=error)
        delay('exit')


def receive(wire, fixture, fixture_sha256, job, position, *, deadline=90, on_send=None):
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or not 0 < deadline <= 90:
        raise ValueError('invalid total transport deadline')
    if type(wire) is not bytes or len(wire) > 500000:
        raise ValueError('request byte limit')
    config = {'mode': 'offline_http_stack', 'wire': base64.b64encode(wire).decode(),
              'request_sha256': sha(wire), 'fixture': str(Path(fixture).resolve()),
              'fixture_sha256': fixture_sha256, 'job': job, 'position': position}
    input_bytes = json.dumps(config).encode()+b'\n'
    # Do not copy the parent environment (it might contain credentials).
    env = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'TEMP', 'TMP') if key in os.environ}
    started = time.monotonic()
    child = subprocess.Popen([sys.executable, '-I', str(Path(__file__).resolve()), '--worker'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             bufsize=0, env=env, cwd=str(ROOT),
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    from scripts.s4_r7_process_guard import WorkerJob
    try:
        ownership = WorkerJob(child)
    except BaseException:
        child.kill(); child.wait(timeout=5)
        child.stdin.close(); child.stdout.close()
        raise
    messages = queue.Queue(maxsize=1)
    stop = threading.Event()
    def read():
        try:
            while not stop.is_set():
                data = child.stdout.readline(MAX_FRAME+1)
                while not stop.is_set():
                    try:
                        messages.put(data, timeout=.05)
                        break
                    except queue.Full:
                        pass
                if not data or len(data) > MAX_FRAME:
                    return
        except (OSError, ValueError):
            pass
    def write():
        try:
            child.stdin.write(input_bytes)
            child.stdin.flush()
        except (OSError, ValueError):
            pass
    reader, writer = threading.Thread(target=read, daemon=True), threading.Thread(target=write, daemon=True)
    reader.start(); writer.start()
    raw, headers, status, request_proof = bytearray(), {}, None, None
    done = False; error = None; failure = None; permitted = False
    try:
        while True:
            remaining = deadline-(time.monotonic()-started)
            if remaining <= 0:
                failure, error = 'parent_total_deadline', 'timeout'
                break
            try:
                line = messages.get(timeout=remaining)
            except queue.Empty:
                failure, error = 'parent_total_deadline', 'timeout'
                break
            if not line:
                if not done:
                    failure, error = 'worker_exit_without_done', 'connection_error'
                break
            if len(line) > MAX_FRAME or not line.endswith(b'\n') or done:
                raise ValueError('invalid worker protocol frame')
            message = json.loads(line)
            kind = message.get('kind')
            if kind == 'ready' and not permitted:
                if message != {'kind': 'ready', 'request_sha256': sha(wire), 'fixture_sha256': fixture_sha256}:
                    raise ValueError('worker ready binding mismatch')
                if on_send is not None:
                    on_send()  # Durable source/approval/send transition BEFORE the go byte.
                permitted = True
                if time.monotonic()-started >= deadline:
                    failure, error = 'parent_total_deadline', 'timeout'
                    break  # Never release a worker after a slow local admission.
                child.stdin.write(b'go\n'); child.stdin.flush(); child.stdin.close()
            elif kind == 'request' and permitted and request_proof is None and status is None:
                request_proof = {k: v for k, v in message.items() if k != 'kind'}
                if request_proof != {'request_sha256': sha(wire), 'tls_server_hostname': 'api.deepseek.com',
                                     'request_line': 'POST /chat/completions HTTP/1.1', 'authorization_present': False}:
                    raise ValueError('HTTP request proof mismatch')
            elif kind == 'headers' and request_proof and status is None:
                status, headers = message['status'], message['headers']
            elif kind == 'chunk' and status is not None:
                chunk = base64.b64decode(message['data'], validate=True)
                if len(chunk) > 4096:
                    raise ValueError('body frame overflow')
                raw.extend(chunk[:MAX_BODY+1-len(raw)])
                if len(raw) > MAX_BODY:
                    failure, error = 'response_size_limit', 'response_size_exceeded'
                    break
            elif kind == 'done' and permitted:
                done, error = True, message['error']
            else:
                raise ValueError('out-of-order worker frame')
        if failure is None:
            try:
                child.wait(timeout=max(.001, deadline-(time.monotonic()-started)))
            except subprocess.TimeoutExpired:
                failure, error = 'parent_total_deadline', 'timeout'
    except (ValueError, KeyError, TypeError):
        failure, error = 'worker_protocol_failure', 'connection_error'
    finally:
        stop.set()
        elapsed_ms = (time.monotonic()-started)*1000
        try:
            ownership.close()  # Also kills on cleanup exceptions; never leak the job handle.
            child.wait(timeout=5)
        finally:
            reader.join(timeout=1); writer.join(timeout=1)
            child.stdout.close()
            if not child.stdin.closed:
                child.stdin.close()
    if child.returncode != 0 and failure is None:
        failure, error = 'worker_exit_nonzero', 'connection_error'
    if elapsed_ms >= deadline*1000 and failure is None:
        failure, error = 'parent_total_deadline', 'timeout'
    raw = bytes(raw)
    return raw, {'source': SOURCE, 'endpoint': ENDPOINT, 'request_sha256': sha(wire),
        'response_sha256': sha(raw), 'captured_bytes': len(raw),
        'complete_response': done and error is None and failure is None,
        'http_status': status, 'transport_error': error, 'headers': headers,
        'elapsed_ms': elapsed_ms, 'actual_model_calls': 0,
        'worker': {'version': 's4_http_process_receipt_v1', 'pid': child.pid, 'exit_code': child.returncode,
                   'child_reaped': child.poll() is not None, 'reader_stopped': not reader.is_alive(),
                   'writer_stopped': not writer.is_alive(), 'deadline_seconds': deadline,
                   'failure': failure, 'request_proof': request_proof,
                   'dispatch_permitted': permitted,
                   'parent_death_guard': ownership.kind,
                   'cleanup_elapsed_ms': (time.monotonic()-started)*1000-elapsed_ms,
                   'backend': 'fixed_bytes_no_socket_no_cryptographic_tls'}}


if __name__ == '__main__':
    if sys.argv[1:] != ['--worker']:
        start_live()
    try:
        data = sys.stdin.buffer.readline(1000001)
        if len(data) > 1000000:
            raise ValueError('worker input limit')
        _child(json.loads(data))
    except BaseException:
        sys.exit(2)
