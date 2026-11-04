"""Linux fixed-only process compatibility for the frozen Windows pilot.

Native receipts are persisted unchanged. Only the validator's in-memory view
maps the two disclosed transport metadata fields to the legacy vocabulary.
This module never enables a live backend or accepts a credential.
"""
from contextlib import contextmanager
import base64
import ctypes
from functools import wraps
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import threading
from unittest.mock import patch

VERSION = 's4_public_linux_fixed_v1'
RECEIPT_VERSION = 's4_public_linux_fixed_receipt_v1'
GUARD = 'linux_pdeathsig_process_group_fixed_v1'
LEGACY_VERSION = 's4_owner_pilot_transport_v1'
LEGACY_GUARD = 'windows_job_kill_on_close'
CONTEXT_LOCK = threading.Lock()
RECEIVE_LOCK = threading.Lock()


def _windows():
    return os.name == 'nt'


def _linux():
    return sys.platform.startswith('linux') and os.name == 'posix'


def _worker():
    # No caller-selected executable script is accepted by the bootstrap.
    return Path(__file__).resolve().with_name('s4_owner_pilot_transport.py')


class LinuxOwner:
    """Own the dedicated fixed worker group; old receive performs the reap."""
    def __init__(self, child):
        if not _linux():
            raise RuntimeError('linux_fixed_owner_requires_linux')
        if os.getpgid(child.pid) != child.pid:
            raise RuntimeError('linux_fixed_worker_group_not_owned')
        self.child, self.pid, self.closed = child, child.pid, False
        self.kind = GUARD

    def close(self):
        if self.closed:
            return
        self.closed = True
        # Once the original worker is reaped, do not signal a potentially
        # recycled PID. The trusted fixed worker never creates subprocesses.
        if self.child.poll() is not None:
            return
        try:
            if os.getpgid(self.pid) != self.pid:
                raise RuntimeError('linux_fixed_worker_group_changed')
            os.killpg(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _validate(delegate, error, raw, receipt, wire, mode, deadline):
    """Disclosed read-only metadata projection; raw receipt is never mutated."""
    if not isinstance(receipt, dict):
        raise error('linux_fixed_receipt_not_object')
    native = receipt.get('version') == RECEIPT_VERSION
    if native or receipt.get('parent_death_guard') == GUARD:
        if (not native or receipt.get('parent_death_guard') != GUARD or
                mode != 'fixed' or receipt.get('backend') != 'fixed'):
            raise error('linux_fixed_receipt_binding')
        projected = {**receipt, 'version': LEGACY_VERSION, 'parent_death_guard': LEGACY_GUARD}
        return delegate(raw, projected, wire, mode, deadline)
    # Historical Windows live/fixed receipts keep the original verifier and
    # original data. This is not permission to dispatch a new live request.
    return delegate(raw, receipt, wire, mode, deadline)


def _receive(delegate, wire, *, mode, on_send, credential=None, fixed=None, deadline=90):
    if mode != 'fixed' or credential is not None or type(fixed) is not dict:
        raise PermissionError('linux_public_transport_is_fixed_only_no_credentials')
    if not RECEIVE_LOCK.acquire(blocking=False):
        raise RuntimeError('linux_fixed_receive_single_concurrency')
    try:
        from scripts import s4_r7_process_guard as guard
        original_popen = subprocess.Popen
        original_argv = [sys.executable, '-I', str(_worker()), '--child']

        def fixed_popen(args, *positional, **kwargs):
            if isinstance(args, (list, tuple)) and list(args) == original_argv:
                if kwargs.get('preexec_fn') is not None:
                    raise PermissionError('linux_fixed_bootstrap_forbids_preexec_fn')
                kwargs['start_new_session'] = True
                args = [sys.executable, '-I', str(Path(__file__).resolve()), '--fixed-child', str(os.getpid())]
            return original_popen(args, *positional, **kwargs)

        with patch.object(subprocess, 'Popen', fixed_popen), patch.object(guard, 'WorkerJob', LinuxOwner):
            raw, receipt = delegate(wire, mode='fixed', on_send=on_send, credential=None,
                                    fixed=fixed, deadline=deadline)
        if receipt.get('parent_death_guard') != GUARD or receipt.get('backend') != 'fixed':
            raise RuntimeError('linux_fixed_native_guard_receipt_missing')
        return raw, {**receipt, 'version': RECEIPT_VERSION}
    finally:
        RECEIVE_LOCK.release()


@contextmanager
def compatibility():
    """Serial publication-only scope. Windows stays entirely unchanged."""
    if _windows():
        yield
        return
    if not _linux():
        raise PermissionError('public_fixed_platform_not_supported')
    if not CONTEXT_LOCK.acquire(blocking=False):
        raise RuntimeError('linux_fixed_compatibility_single_concurrency')
    try:
        from scripts import s4_owner_pilot as base
        receive, validate = base.transport.receive, base.validate_receipt
        @wraps(receive)
        def fixed_receive(wire, **kwargs):
            return _receive(receive, wire, **kwargs)
        @wraps(validate)
        def fixed_validate(raw, receipt, wire, mode, deadline):
            return _validate(validate, base.SharedIntegrityError, raw, receipt, wire, mode, deadline)
        with patch.object(base.transport, 'receive', fixed_receive), patch.object(base, 'validate_receipt', fixed_validate):
            yield
    finally:
        CONTEXT_LOCK.release()


def _strict_json(data):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('duplicate_fixed_configuration_key')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('nonfinite_fixed_configuration')
    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)


def _configuration(stream, check_wire):
    raw = stream.readline(1_500_001)
    if len(raw) > 1_500_000:
        raise ValueError('fixed_configuration_size_limit')
    config = _strict_json(raw)
    if (not isinstance(config, dict) or set(config) != {'mode', 'wire', 'fixed'} or
            config['mode'] != 'fixed' or type(config['fixed']) is not dict or type(config['wire']) is not str):
        raise PermissionError('fixed_bootstrap_rejects_live_credential_or_script_configuration')
    check_wire(base64.b64decode(config['wire'], validate=True))
    return config


def _parent_death_guard(expected_parent):
    if not _linux() or type(expected_parent) is not int or expected_parent <= 1:
        raise PermissionError('invalid_linux_fixed_parent')
    if os.getppid() != expected_parent:
        raise PermissionError('linux_fixed_parent_changed_before_guard')
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), 'linux_fixed_parent_death_guard_failed')
    if os.getppid() != expected_parent:
        raise PermissionError('linux_fixed_parent_changed_after_guard')


def fixed_child(expected_parent):
    _parent_death_guard(expected_parent)
    # run_name is not __main__: original CLI does not consume stdin again.
    original = runpy.run_path(str(_worker()), run_name='s4_frozen_fixed_worker')
    config = _configuration(sys.stdin.buffer, original['check_wire'])
    original['child_main'](config)


def main():
    if len(sys.argv) != 3 or sys.argv[1] != '--fixed-child':
        raise SystemExit('Only the Linux fixed-worker bootstrap is supported.')
    try:
        fixed_child(int(sys.argv[2]))
    except Exception:
        # Never echo config, HTTP headers, body, or potential credential text.
        raise SystemExit('Linux fixed-worker bootstrap rejected.') from None


if __name__ == '__main__':
    main()
