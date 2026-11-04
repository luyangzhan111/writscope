"""One owner-supervised diagnostic entry, reusing the canonical S4 executor.

No real launch is authorized by prepare/verify/offline. `run` additionally needs
a concrete approval record AND a new human console confirmation before getpass.
This trusts the owner/local program; it is not a host-attested production gate.
"""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import getpass
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time

from scripts import s4_owner_pilot_transport as transport
from scripts.s4_p2 import PUBLIC_FILES, checked_bundle
from scripts.s4_prompt_candidate import adapter
from scripts.s4_r3_checkpoint_journal_v1 import CheckpointJournal
from scripts.s4_r7_billing_boundary_v3 import account_raw, checked_price
from scripts.s4_r7_response_executor import execute_job
from scripts.s4_r7_worker_runtime import single_entry
from scripts.s4_repeat_schedule import build
from scripts.s4_shared_transport_offline import SharedIntegrityError
from scripts.s4_synthetic_cost_wire import mock_wire
from scripts.s4_unified_journal import now
from writscope.artifacts import canonical_hash, digest
from writscope.contracts import parse_json

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT/'reports/s4-r6-candidate-v1/public'
CASE = 's4_027'
MODE = 'owner_initiated_development_diagnostic_not_host_attested'
POLICY = {'currency': 'CNY', 'cap_micro': 30000000, 'max_attempts': 13,
          'conditional_input_envelope': 1048576, 'output_limit': 4096,
          'post_response_input_stop': 32768, 'input_rate': '2', 'output_rate': '8',
          'per_attempt_reservation_micro': 2129920, 'reservation_release': 'never',
          'provider_bill_hard_cap': False, 'provider_token_bound_verified': False}


def write(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n'); f.flush(); os.fsync(f.fileno())


def read(path):
    path = Path(path)
    unlinked(path)
    if not path.is_file() or path.stat().st_size > 5000000:
        raise SharedIntegrityError('missing_or_oversized_record')
    return parse_json(path.read_text(encoding='utf-8'))


def unlinked(path):
    if any(p.is_symlink() or p.is_junction() for p in (Path(path), *Path(path).parents)):
        raise SharedIntegrityError('linked_path')


def sources():
    paths = sorted(p for folder in ('scripts', 'src', 'prompts') for p in (ROOT/folder).rglob('*')
                   if p.is_file() and p.suffix in ('.py', '.txt', '.md', '.json'))
    paths += [ROOT/'specs/s4-owner-pilot-v1.md', ROOT/'pyproject.toml', ROOT/'uv.lock']
    for p in paths:
        unlinked(p)
    return {p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in paths}


def environment():
    return {'python': '.'.join(map(str, sys.version_info[:3])),
            'packages': {p: importlib.metadata.version(p) for p in ('httpx', 'httpcore', 'h11', 'pydantic', 'certifi')}}


def prepare(price, bundles=PUBLIC):
    checked_price(price, datetime.fromisoformat(price['checked_at']))
    bundle = checked_bundle(Path(bundles)/CASE)
    files = {p: digest((Path(bundles)/CASE/p).read_bytes()) for p in sorted(PUBLIC_FILES)}
    schedule = build(bundles, [CASE], 1)['plan']
    if len(bundle.review_input.events) != 5 or schedule['max_attempts'] != 13 or len(schedule['jobs']) != 3:
        raise SharedIntegrityError('pilot_scope_changed')
    return {'version': 's4_owner_pilot_candidate_v1', 'status': 'frozen_candidate_pending_execution_approval',
        'execution_kind': MODE, 'endpoint': transport.ENDPOINT, 'base': {'policy': POLICY.copy()},
        'schedule': schedule, 'public_files_sha256': files, 'sources_sha256': sources(),
        'environment': environment(), 'price': price, 'deadline_seconds': 90,
        'concurrency': 1, 'retries': 0, 'actual_execution_approval': None,
        'materials': ['single_case_public_bundle', 'frozen_prompt_and_schema', 'visible_history',
                      'own_accepted_state', 'permitted_lookup_result'],
        'never_send': ['private_reference', 'pair_map', 'hidden_tests', 'private_repair', 'mock_answers', 'evaluation_report'],
        'reference_status': 'provisional_author_draft_not_frozen',
        'independent_review': 'not_performed_skipped_by_user',
        'comparison': 'equal_configuration_development_diagnostic', 'budget_matched': 'deferred',
        'scope': 'one_previously_debugged_case_not_ranking_or_full270',
        'approval_scope': 'one_start_no_resume_no_retry', 'real_sending_currently_approved': False}


def check_plan(plan, bundles=PUBLIC):
    try:
        if plan == prepare(plan['price'], bundles):
            return
    except Exception:
        pass
    raise SharedIntegrityError('plan_source_environment_or_public_binding_mismatch')


def validate_receipt(raw, receipt, wire, mode, deadline):
    expected = {'version', 'backend', 'endpoint', 'request_sha256', 'response_sha256', 'captured_bytes',
        'http_status', 'headers', 'complete_response', 'transport_error', 'dispatch_permitted', 'elapsed_ms',
        'cleanup_elapsed_ms', 'deadline_seconds', 'child_reaped', 'reader_stopped', 'writer_stopped',
        'exit_code', 'parent_death_guard'}
    if (set(receipt) != expected or receipt['version'] != 's4_owner_pilot_transport_v1' or
            receipt['backend'] != mode or receipt['endpoint'] != transport.ENDPOINT or
            receipt['request_sha256'] != digest(wire) or receipt['response_sha256'] != digest(raw) or
            receipt['captured_bytes'] != len(raw) or len(raw) > 100001 or
            receipt['deadline_seconds'] != deadline or receipt['parent_death_guard'] != 'windows_job_kill_on_close' or
            any(receipt[k] is not True for k in ('child_reaped', 'reader_stopped', 'writer_stopped')) or
            any(type(receipt[k]) is not bool for k in ('complete_response', 'dispatch_permitted')) or
            any(type(receipt[k]) not in (float, int) or not math.isfinite(receipt[k]) or receipt[k] < 0
                for k in ('elapsed_ms', 'cleanup_elapsed_ms')) or
            receipt['http_status'] is not None and (type(receipt['http_status']) is not int or not 100 <= receipt['http_status'] <= 599) or
            type(receipt['exit_code']) is not int or not isinstance(receipt['headers'], dict) or
            set(receipt['headers']) - {'content-type', 'date', 'x-request-id', 'request-id'} or
            any(type(v) is not str or len(v) > 8192 for v in receipt['headers'].values())):
        raise SharedIntegrityError('transport_receipt_binding')
    err = receipt['transport_error']
    if (err not in (None, 'timeout', 'connection_error', 'response_size_exceeded', 'unsupported_content_encoding',
                    'worker_protocol_failure', 'credential_reflection_withheld') or
            not receipt['dispatch_permitted'] and (raw or receipt['http_status'] is not None or receipt['complete_response']) or
            receipt['complete_response'] and (err is not None or receipt['http_status'] is None or
                 receipt['exit_code'] != 0 or len(raw) > 100000 or receipt['elapsed_ms'] >= deadline*1000) or
            not receipt['complete_response'] and err is None or
            err == 'response_size_exceeded' and len(raw) != 100001):
        raise SharedIntegrityError('transport_receipt_invariant')


def interpret(raw, receipt, request, plan):
    payload = finish = error = None
    usage = {k: None for k in ('input_tokens', 'output_tokens', 'cache_hit_input_tokens',
                              'cache_miss_input_tokens', 'reasoning_tokens')}
    accounting = None
    stop = False
    if receipt['transport_error']:
        error = receipt['transport_error']
        stop = error in ('worker_protocol_failure', 'credential_reflection_withheld')
    elif receipt['http_status'] != 200:
        status = receipt['http_status']
        error = 'credential_or_permission' if status in (401, 403) else 'service_http_error' if status == 429 or status >= 500 else 'http_error'
        stop = status in (401, 403) or 300 <= status < 400
    else:
        accounting = account_raw(raw, input_bound=plan['base']['policy']['post_response_input_stop'])
        usage.update(accounting['usage'])
        if accounting['errors']:
            error, stop = 'token_accounting_fault', True
        try:
            envelope = parse_json(raw.decode('utf-8'))
            if envelope.get('model') not in ('deepseek-flash', 'DeepSeek-V4.1-Flash-1017'):
                error, stop = 'model_drift', True
            choices = envelope['choices']
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError('invalid_choices')
            finish = choices[0]['finish_reason']
            if finish != 'stop':
                raise ValueError('truncated_or_unknown_finish')
            payload = parse_json(choices[0]['message']['content'])
            adapter(request['stage'], request['lookup_allowed']).validate_python(payload)
        except (ValueError, UnicodeError, KeyError, TypeError, IndexError):
            error = error or 'invalid_output'
    estimate = accounting['token_cost_cny_micro'] if accounting else None
    usage.update(billed_cost=None, estimated_cost_cny=str(Decimal(estimate)/1000000) if estimate is not None else None,
         usage_source='provider_response' if receipt['backend'] == 'live' else 'fixed_test_response',
         cost_source=accounting['token_cost_source'] if accounting else 'unknown', accounting=accounting,
         provider_elapsed_ms=receipt['elapsed_ms']+receipt['cleanup_elapsed_ms'],
         returned_model=(parse_json(raw.decode('utf-8')).get('model') if accounting and not
                          ('unparseable_or_partial_body' in accounting['errors']) else None),
         stop_round=stop, provider_bill_hard_cap=False)
    return payload, finish, error, usage


class Journal(CheckpointJournal):
    """One CNY ledger; checkpoint implementation reused, no virtual shadow budget."""
    def __init__(self, path, plan, mode, create=False):
        self.path, self.plan, self.mode = Path(path), plan, mode
        self.policy = plan['base']['policy']; self.plan_hash = canonical_hash(plan)
        if mode not in ('fixed', 'live') or self.policy != POLICY:
            raise SharedIntegrityError('journal_policy_or_mode')
        if create:
            with self.path.open('xb'):
                pass
            with closing(self.connect()) as db:
                db.executescript('''BEGIN IMMEDIATE;
                CREATE TABLE run(plan_hash TEXT NOT NULL,policy_hash TEXT NOT NULL,stopped TEXT,mode TEXT NOT NULL);
                CREATE TABLE attempt(id INTEGER PRIMARY KEY,job TEXT NOT NULL,stage TEXT NOT NULL,is_continuation INTEGER NOT NULL,
                  request BLOB NOT NULL,request_hash TEXT NOT NULL,reserved INTEGER NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,
                  sent_at TEXT,received_at TEXT,response BLOB,response_hash TEXT,http_status INTEGER,finish_reason TEXT,error_kind TEXT,
                  validation TEXT,usage TEXT,cost_source TEXT);
                CREATE TABLE context(attempt INTEGER PRIMARY KEY,preview TEXT NOT NULL,proof TEXT NOT NULL);
                CREATE TABLE receipt(attempt INTEGER PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE checkpoint(seq INTEGER PRIMARY KEY,job TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,
                  created_at TEXT NOT NULL,previous_hash TEXT NOT NULL,event_hash TEXT NOT NULL);
                COMMIT;''')
                db.execute('INSERT INTO run VALUES (?,?,NULL,?)', (self.plan_hash, canonical_hash(self.policy), mode))
        unlinked(self.path)
        self.snapshot(); self.checkpoints()

    def reserve(self, job, request):
        self.snapshot()
        wire = mock_wire(request); transport.check_wire(wire)
        proof = {'request_sha256': digest(wire), 'wire_bytes': len(wire), 'local_token_count': None,
                 'conditional_input_envelope': POLICY['conditional_input_envelope'],
                 'provider_token_bound_verified': False, 'kind': 'conditional_context_envelope_not_token_certificate'}
        with closing(self.connect()) as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT plan_hash,policy_hash,stopped,mode FROM run').fetchone() != (
                        self.plan_hash, canonical_hash(self.policy), None, self.mode):
                    raise SharedIntegrityError('stopped_or_changed_journal')
                prior = db.execute('SELECT status,reserved FROM attempt').fetchall()
                if any(r[0] in ('reserved', 'dispatch_started', 'received') for r in prior):
                    raise SharedIntegrityError('unresolved_attempt')
                if len(prior) >= POLICY['max_attempts'] or sum(r[1] for r in prior)+POLICY['per_attempt_reservation_micro'] > POLICY['cap_micro']:
                    raise SharedIntegrityError('conditional_budget_exhausted')
                cur = db.execute("INSERT INTO attempt(job,stage,is_continuation,request,request_hash,reserved,status,created_at,cost_source) VALUES (?,?,?,?,?,?,'reserved',?,?)",
                     (job, request['stage'], int('lookup_response' in request['public_input']), wire, digest(wire),
                      POLICY['per_attempt_reservation_micro'], now(), 'conditional_CNY_context_envelope'))
                db.execute('INSERT INTO context VALUES (?,?,?)', (cur.lastrowid, json.dumps(request, ensure_ascii=False), json.dumps(proof)))
                db.execute('COMMIT')
                return cur.lastrowid, wire
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def persist_received(self, index, raw, receipt):
        with closing(self.connect()) as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                before = 'dispatch_started' if receipt['dispatch_permitted'] else 'reserved'
                cur = db.execute("UPDATE attempt SET status=?,response=?,response_hash=?,received_at=?,http_status=? WHERE id=? AND status=?",
                      ('received' if receipt['dispatch_permitted'] else 'reserved', raw if receipt['dispatch_permitted'] else None,
                       digest(raw) if receipt['dispatch_permitted'] else None, now() if receipt['dispatch_permitted'] else None,
                       receipt['http_status'], index, before))
                if cur.rowcount != 1:
                    raise SharedIntegrityError('receive_transition')
                db.execute('INSERT INTO receipt VALUES (?,?)', (index, json.dumps(receipt)))
                db.execute('COMMIT')
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def snapshot(self):
        with closing(self.connect()) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('SELECT * FROM run').fetchall() != [
                    (self.plan_hash, canonical_hash(self.policy), None, self.mode)]:
                raise SharedIntegrityError('journal_header_or_integrity')
            db.row_factory = sqlite3.Row
            attempts = [dict(r) for r in db.execute('SELECT * FROM attempt ORDER BY id')]
            contexts = {r['attempt']: (parse_json(r['preview']), parse_json(r['proof'])) for r in db.execute('SELECT * FROM context')}
            receipts = {r['attempt']: parse_json(r['value']) for r in db.execute('SELECT * FROM receipt')}
        if set(contexts) != {a['id'] for a in attempts} or set(receipts)-set(contexts):
            raise SharedIntegrityError('journal_inventory')
        for index, a in enumerate(attempts, 1):
            preview, proof = contexts[a['id']]
            expected_proof = {'request_sha256': digest(a['request']), 'wire_bytes': len(a['request']),
                'local_token_count': None, 'conditional_input_envelope': POLICY['conditional_input_envelope'],
                'provider_token_bound_verified': False, 'kind': 'conditional_context_envelope_not_token_certificate'}
            if (a['id'] != index or a['request'] != mock_wire(preview) or a['request_hash'] != digest(a['request']) or
                    proof != expected_proof or a['reserved'] != POLICY['per_attempt_reservation_micro'] or
                    a['stage'] != preview['stage'] or a['is_continuation'] != int('lookup_response' in preview['public_input']) or
                    a['cost_source'] != 'conditional_CNY_context_envelope' or
                    a['status'] not in ('reserved', 'dispatch_started', 'received', 'completed', 'failed') or
                    a['status'] == 'reserved' and any(a[k] is not None for k in ('sent_at', 'response', 'received_at')) or
                    a['status'] != 'reserved' and a['sent_at'] is None or
                    a['response'] is not None and digest(a['response']) != a['response_hash'] or
                    a['response'] is None and a['response_hash'] is not None):
                raise SharedIntegrityError('journal_attempt_invariant')
            receipt = receipts.get(index)
            if receipt is not None:
                validate_receipt(a['response'] or b'', receipt, a['request'], self.mode, self.plan['deadline_seconds'])
                if receipt['dispatch_permitted'] != (a['status'] != 'reserved') or receipt['http_status'] != a['http_status']:
                    raise SharedIntegrityError('receipt_send_state_mismatch')
            if a['status'] in ('received', 'completed', 'failed') and (receipt is None or a['response'] is None or a['received_at'] is None):
                raise SharedIntegrityError('missing_received_bytes_or_receipt')
            if a['status'] in ('completed', 'failed'):
                _, finish, error, usage = interpret(a['response'], receipt, preview, self.plan)
                if (a['finish_reason'] != finish or a['error_kind'] != error or parse_json(a['usage']) != usage or
                        a['status'] != ('failed' if error else 'completed') or a['validation'] != ('rejected' if error else 'accepted')):
                    raise SharedIntegrityError('response_usage_or_validation_derivation')
            elif any(a[k] is not None for k in ('finish_reason', 'error_kind', 'validation', 'usage')):
                raise SharedIntegrityError('invented_pending_validation')
        total = sum(a['reserved'] for a in attempts)
        if len(attempts) > 13 or total > POLICY['cap_micro'] or sum(a['status'] in ('reserved', 'dispatch_started', 'received') for a in attempts) > 1:
            raise SharedIntegrityError('journal_budget_or_pending_invariant')
        return {'stopped': None, 'attempts': attempts, 'reserved_cny_micro': total}


class ReadOnlyJournal(Journal):
    def connect(self):
        return sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True, isolation_level=None)


def dispatch(journal, identity, request, *, credential=None, fixed=None, hook=None):
    index, wire = journal.reserve(canonical_hash(identity), request)
    def point(name):
        if hook:
            hook(name, journal, index)
    try:
        point('reserved')
        def permit():
            point('before_send')
            journal.transition(index, 'reserved', 'dispatch_started', sent_at=now())
            point('dispatch_started')
        raw, receipt = transport.receive(wire, mode=journal.mode, on_send=permit, credential=credential,
                                        fixed=fixed, deadline=journal.plan['deadline_seconds'])
        journal.persist_received(index, raw, receipt)
        point('received')
        validate_receipt(raw, receipt, wire, journal.mode, journal.plan['deadline_seconds'])
        if not receipt['dispatch_permitted']:
            raise SharedIntegrityError('unsent_worker_failure_reservation_retained')
        payload, finish, error, usage = interpret(raw, receipt, request, journal.plan)
        journal.transition(index, 'received', 'failed' if error else 'completed', finish_reason=finish,
             error_kind=error, validation='rejected' if error else 'accepted', usage=json.dumps(usage))
        point('validated')
        attempts = journal.snapshot()['attempts']
        service = ('timeout', 'connection_error', 'service_http_error')
        if usage['stop_round'] or len(attempts) >= 3 and all(a['error_kind'] in service for a in attempts[-3:]):
            raise SharedIntegrityError(error or 'three_consecutive_service_failures')
        if error:
            raise ValueError(error)
        return payload
    except (ValueError, SharedIntegrityError):
        raise
    except Exception as exc:
        raise SharedIntegrityError('transport_or_persistence_failure:'+type(exc).__name__) from None


@single_entry
def run(plan, output, *, bundles=PUBLIC, fixed_responses=None, credential=None, launch=None, hook=None):
    """Internal common loop. Live use requires a console launch object, not a flag."""
    check_plan(plan, bundles)
    mode = 'fixed' if fixed_responses is not None else 'live'
    if mode == 'live' and (type(launch) is not ConsoleLaunch or launch.plan_sha256 != canonical_hash(plan) or launch.used):
        raise PermissionError('owner_console_launch_required_before_real_transport')
    if mode == 'fixed' and (credential is not None or launch is not None):
        raise PermissionError('fixed_mode_cannot_carry_credentials_or_approval')
    if mode == 'live':
        launch.used = True
    output = Path(output)
    unlinked(output)
    if output.exists() or output.resolve().is_relative_to(Path(bundles).resolve()) or output.resolve().is_relative_to(ROOT/'evaluation_private'):
        raise ValueError('new_separate_output_required')
    output.mkdir()
    write(output/'plan.json', plan)
    write(output/'origin.json', {'mode': mode, 'plan_sha256': canonical_hash(plan),
         'origin': 'fixed_test_no_model_calls' if mode == 'fixed' else 'owner_console_live_http_not_host_attested',
         'launch': launch.record if launch else None, 'started_at': now(), 'independent_provenance_attestation': False})
    journal = Journal(output/'journal.sqlite', plan, mode, create=True)
    rows, stopped, start = [], None, time.perf_counter()
    for i, identity in enumerate(plan['schedule']['jobs'], 1):
        if stopped:
            rows.append({**identity, 'status': 'not_run', 'attempts': 0, 'final_present': False})
            continue
        position = 0
        def send(request):
            nonlocal position
            check_plan(plan, bundles)
            if mode == 'live':
                checked_price(plan['price'], datetime.now(timezone.utc))
            fixed = None
            if mode == 'fixed':
                fixed = fixed_responses[identity['case_id']+'/'+identity['method']][position]
            position += 1
            return dispatch(journal, identity, request, credential=credential, fixed=fixed,
                            hook=lambda name, j, n: (check_plan(plan, bundles) if name == 'before_send' else None,
                                                     hook(name, j, n) if hook else None))
        try:
            job, failure = execute_job(identity, Path(bundles)/CASE, plan, journal, send)
            stopped = failure
            snap = journal.snapshot()
            write(output/f'job-{i:06d}.json', {'identity': identity, 'trajectory': job, 'round_integrity_failure': failure})
            sent = sum(a['job'] == canonical_hash(identity) and a['status'] != 'reserved' for a in snap['attempts'])
            rows.append({**identity, 'status': job['status'], 'attempts': sent, 'final_present': job['final'] is not None})
        except (Exception, KeyboardInterrupt) as exc:
            # A dead/unwritable DB cannot be made trustworthy by inventing a job.
            # plan.json retains all three identities; verifier audits durable prefix.
            stopped = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'persistence_or_integrity_failure:'+type(exc).__name__
            rows.append({**identity, 'status': 'failed', 'attempts': None, 'final_present': False})
    result = {'version': 's4_owner_pilot_result_v1', 'plan_sha256': canonical_hash(plan), 'mode': mode,
        'rows': rows, 'stop_reason': stopped, 'round_elapsed_ms': (time.perf_counter()-start)*1000,
        'actual_model_calls': 0 if mode == 'fixed' else None, 'provider_billed_cost': None, 'accuracy': None}
    write(output/'result.json', result)
    return result


class ConsoleLaunch:
    """Created only by the local interactive gate; no serialized token import."""
    def __init__(self, *args, **kwargs):
        raise PermissionError('use_console_gate')


def console_gate(plan, approval, claims, *, prompt=input):
    check_plan(plan)
    checked_price(plan['price'], datetime.now(timezone.utc))
    h = canonical_hash(plan)
    required = {'version', 'plan_sha256', 'status', 'raw_user_message', 'recorded_source',
                'budget_cny', 'external_materials', 'billing_uncertainty_accepted', 'account_currency'}
    if (set(approval) != required or approval['version'] != 's4_owner_pilot_execution_approval_v1' or
            approval['plan_sha256'] != h or approval['status'] != 'approved_exact_single_run' or
            type(approval['raw_user_message']) is not str or not approval['raw_user_message'].strip() or
            approval['recorded_source'] != 'actual_user_message_not_host_attested' or
            approval['budget_cny'] != '30.00' or approval['external_materials'] != plan['materials'] or
            approval['billing_uncertainty_accepted'] is not True or approval['account_currency'] != 'CNY'):
        raise PermissionError('concrete_single_run_approval_missing_or_mismatched')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise PermissionError('owner_interactive_terminal_required')
    phrase = 'AUTHORIZE '+h+' CNY30 DEEPSEEK ACCEPT_BILLING_UNCERTAINTY'
    print('一例 / 三方法 / 最多13次 / 单并发无重试。公开材料将发往 DeepSeek。')
    print('30元仅为本地条件预留上限；服务商硬账单上界未验证。来源为本机负责人确认，不是宿主认证。')
    print('同意该单轮、CNY账户及计费不确定性时，逐字输入：\n'+phrase)
    if prompt('确认：') != phrase:
        raise PermissionError('owner_console_confirmation_declined')
    claims = Path(claims)
    unlinked(claims)
    claims.mkdir(parents=True, exist_ok=True)
    record = {'plan_sha256': h, 'approval_record_sha256': canonical_hash(approval), 'approval': approval,
              'console_confirmation': phrase, 'confirmed_at': now(), 'source': 'owner_local_console_not_host_attested'}
    write(claims/(h+'.json'), record)  # exclusive, flushed, never released or reused
    launch = object.__new__(ConsoleLaunch)
    launch.plan_sha256, launch.record, launch.used = h, record, False
    return launch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('prepare'); make.add_argument('--price', type=Path, required=True); make.add_argument('--output', type=Path, required=True)
    run_args = sub.add_parser('run')
    run_args.add_argument('--candidate', type=Path, required=True); run_args.add_argument('--approval', type=Path, required=True)
    run_args.add_argument('--output', type=Path, required=True); run_args.add_argument('--claims', type=Path, required=True)
    verify = sub.add_parser('verify'); verify.add_argument('--run', type=Path, required=True); verify.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        plan = prepare(read(args.price)); write(args.output, plan)
        print(canonical_hash(plan))
    elif args.command == 'verify':
        from scripts.s4_owner_pilot_replay import evaluate
        write(args.output, evaluate(args.run))
    else:
        plan = read(args.candidate)
        # No environment variable lookup, credential file, or provider preflight call.
        launch = console_gate(plan, read(args.approval), args.claims)
        key = getpass.getpass('DeepSeek API key（仅本进程内存，不保存）：')
        if not key or len(key) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise PermissionError('invalid_credential_claim_retained_no_send')
        run(plan, args.output, credential=key, launch=launch)


if __name__ == '__main__':
    main()
