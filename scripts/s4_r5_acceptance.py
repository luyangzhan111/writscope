"""R5 offline acceptance harness. Delegates execution to R3 and evaluation to R4.

This is a fault-injection/control harness, not another method runner. Every
child forbids socket connections; no real credentials or approval are accepted.
"""
import argparse
from collections import Counter
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
from unittest.mock import patch

from scripts import s4_r3 as r3, s4_r4_evaluator as evaluator
from scripts.s4_r3_authority import ApprovalEvidence, grants_for
from scripts.s4_r4_replay import read_json, require, source_hashes
from scripts.s4_p2 import checked_bundle
from scripts.s4_p3 import export_bundle
from scripts.s4_p4_scripts import generate
from scripts.s4_prompt_candidate import preview
from scripts.s4_synthetic_cost_wire import mock_wire
from scripts.s4_synthetic_cost_ledger import quote
from scripts.s4_shared_transport_offline import SharedIntegrityError
from writscope.artifacts import canonical_hash, digest

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ('normal', 'ordinary_errors', 'attempt_budget', 'amount_budget', 'source_guard',
             'hash_chain', 'isolation', 'ledger', 'reserved', 'dispatch_started', 'received',
             'validated', 'transition_started', 'transition_finished', 'job_write', 'summary_write')
REJECTED = {'hash_chain', 'isolation', 'ledger'}
SETTLED = {'normal', 'ordinary_errors', 'attempt_budget', 'amount_budget'}
CRASHED = {'reserved', 'dispatch_started', 'received', 'validated', 'transition_started', 'transition_finished'}
TAMPERS = ('duplicate', 'omitted', 'fake_real')
POLICY = {'cap_micro': 1_000_000_000, 'max_attempts': 1080, 'max_wire_bytes': 500000,
          'max_output_tokens': 4096, 'framing_tokens': 100, 'input_rate_per_million': 1000000,
          'output_rate_per_million': 1000000}
# A fixed synthetic clock is part of the test configuration, not live price validation.
NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def no_network(*args, **kwargs):
    raise AssertionError('R5 forbids actual socket connections')


def offline_guard():
    stack = ExitStack()
    for target in ('socket.create_connection', 'socket.socket.connect', 'socket.socket.connect_ex'):
        stack.enter_context(patch(target, no_network))
    return stack


class SyntheticSource:
    def __init__(self, plan):
        self.plan = plan

    def resolve(self, event_id):
        require(event_id == 'r5-synthetic-event', 'unexpected synthetic event')
        return ApprovalEvidence('r5-fixed-test-control', event_id, 'synthetic-owner', 'user',
            'Synthetic control only; not human approval or real execution permission.', grants_for(self.plan),
            '2026-11-01T00:00:00+00:00', '2026-11-03T00:00:00+00:00', True)


def code_bindings(plan):
    source = dict(plan['sources'])
    for key in ('legacy_sources', 'unified_sources', 'checkpoint_sources'):
        source.update(plan['base'][key])
    source.update(source_hashes())
    for name in ('scripts/s4_r5_acceptance.py', 'scripts/s4_r5_delivery.py',
                 'scripts/s4_r5_integrity_guard.py', 'specs/s4-r5-acceptance-v1.md'):
        source[name] = digest((ROOT/name).read_bytes())
    return source


def check_version(suite):
    descriptor = read_json(Path(suite)/'acceptance-plan.json')
    for name, sha in descriptor['sources_sha256'].items():
        require(digest((ROOT/name).read_bytes()) == sha, 'R5 candidate source changed: '+name)
    return descriptor


def prepare_suite(suite, cases=None, repetitions=3):
    suite = Path(suite)
    require(not suite.exists(), 'new suite directory required')
    cases = cases or [f's4_{n:03d}' for n in range(1, 31)]
    suite.mkdir(parents=True)
    public = suite/'public'; public.mkdir()
    # Restore already measured public facts, never execute a candidate or feed
    # reference judgments to a method. The original audit binds whole bundles.
    fixtures = read_json(ROOT/'evaluation_private/s4_p3/fixture-report.json')
    manifest = read_json(ROOT/'fixtures/s4_p3/manifest.json')
    audit = read_json(ROOT/'evaluation_private/s4_p3/audit-report.json')
    audited = {row['case_id']: row['bundle_sha256'] for row in audit['cases']}
    for case in cases:
        export_bundle(ROOT, public/case, case, fixtures['variants'][manifest['cases'][case]]['impact'])
        require(checked_bundle(public/case).sha256 == audited[case], 'restored P3 bundle mismatch')
    scripts = generate(public, cases)
    write(suite/'scripts.json', scripts)
    plan = r3.prepare(public, suite/'scripts.json', cases, repetitions, POLICY,
                      price_valid_until='2026-11-03T00:00:00+00:00')
    descriptor = {'version': 's4_r5_acceptance_plan_v1', 'status': 'frozen_offline_engineering_candidate_only',
        'cases': cases, 'repetitions': repetitions, 'jobs': plan['base']['schedule']['plan']['jobs'],
        'max_design_attempts': plan['base']['schedule']['plan']['max_attempts'],
        'scenarios': list(SCENARIOS), 'tamper_controls': list(TAMPERS),
        'sources_sha256': code_bindings(plan),
        'seed_sha256': {p: digest((ROOT/p).read_bytes()) for p in (
            'evaluation_private/s4_p3/fixture-report.json', 'evaluation_private/s4_p3/audit-report.json',
            'fixtures/s4_p3/manifest.json', 'evaluation_private/s4_review/r4-evaluator-binding-v1.json')},
        'actual_model_calls': 0, 'real_transport_allowed': False, 'formal_dataset_freeze': False}
    write(suite/'acceptance-plan.json', descriptor)
    return descriptor


def prepare_case(suite, name):
    descriptor = check_version(suite)
    require(name in SCENARIOS, 'unknown scenario')
    target = Path(suite)/name
    target.mkdir(exist_ok=False)
    public = Path(suite)/'public'
    if name == 'isolation':
        public = target/'public'
        shutil.copytree(Path(suite)/'public', public)
    scripts = read_json(Path(suite)/'scripts.json')
    if name == 'ordinary_errors':
        case = descriptor['cases'][0]
        scripts['jobs'][case+'/full_direct']['steps'][0].update(action='error', text='synthetic_timeout')
        scripts['jobs'][case+'/full_checklist']['steps'][0]['text'] = '{}'
        step = scripts['jobs'][case+'/incremental']['steps'][1]
        batch = json.loads(step['text'])
        batch['items'][0]['source_quote'] = 'synthetic nonexistent current-event quote'
        step['text'] = json.dumps(batch)
    write(target/'scripts.json', scripts)
    policy = dict(POLICY)
    if name in ('attempt_budget', 'summary_write'):
        policy['max_attempts'] = 1
    if name == 'amount_budget':
        policy['cap_micro'] = quote(policy, len(mock_wire(preview(public/descriptor['cases'][0], 'full_direct'))))
    plan = r3.prepare(public, target/'scripts.json', descriptor['cases'], descriptor['repetitions'], policy,
                      price_valid_until='2026-11-03T00:00:00+00:00')
    require(code_bindings(plan) == descriptor['sources_sha256'], 'scenario versions differ')
    write(target/'candidate-plan.json', plan)
    return target, public, plan


def worker(suite, name):
    descriptor = check_version(suite)
    target = Path(suite)/name
    public = target/'public' if name == 'isolation' else Path(suite)/'public'
    plan = read_json(target/'candidate-plan.json')
    fired = False

    def trigger(point):
        nonlocal fired
        require(not fired, 'fault fired twice')
        fired = True
        write(target/'fault-fired.json', {'scenario': name, 'point': point, 'synthetic_only': True})

    original_transition, original_reserve = r3.Journal.transition, r3.Journal.reserve
    original_checkpoint, original_write = r3.Journal.append_checkpoint, r3._write
    from scripts.s4_r5_integrity_guard import ByteEquivalenceGuard
    original_check = ByteEquivalenceGuard(plan, public, target/'scripts.json', descriptor['sources_sha256'])
    first_case = descriptor['cases'][0]

    def reserve(self, *args, **kwargs):
        result = original_reserve(self, *args, **kwargs)
        if name == 'reserved' and not fired:
            trigger('reservation_durable_before_send'); os._exit(77)
        return result

    def transition(self, attempt, before, after, **fields):
        result = original_transition(self, attempt, before, after, **fields)
        if not fired and (name == after or name == 'validated' and after == 'completed'):
            trigger('attempt_'+after); os._exit(77)
        if after == 'completed' and not fired and name in {'source_guard', 'hash_chain', 'isolation', 'ledger'}:
            trigger('after_first_validation')
            if name == 'isolation':
                (public/first_case/'forbidden-extra.json').write_text('{"synthetic_sentinel":true}', encoding='utf-8')
            elif name in {'hash_chain', 'ledger'}:
                with closing(self.connect()) as db:
                    if name == 'hash_chain': db.execute("UPDATE checkpoint SET event_hash='synthetic_corruption' WHERE seq=1")
                    else: db.execute("UPDATE attempt SET request_hash='synthetic_corruption' WHERE id=1")
        return result

    def check(*args, **kwargs):
        original_check(*args, **kwargs)
        if name == 'source_guard' and fired:
            raise ValueError('synthetic source binding guard failure')

    def checkpoint(self, job, kind, payload):
        result = original_checkpoint(self, job, kind, payload)
        if not fired and name == kind and name in {'transition_started', 'transition_finished'}:
            trigger(kind+'_durable'); os._exit(77)
        return result

    def persist(path, data):
        if not fired and ((name == 'job_write' and Path(path).name.startswith('job-')) or
                          (name == 'summary_write' and Path(path).name == 'result.json')):
            trigger('before_'+Path(path).name)
            raise OSError('synthetic persistence failure')
        return original_write(path, data)

    with offline_guard(), patch.object(r3.Journal, 'reserve', reserve), patch.object(r3.Journal, 'transition', transition), \
         patch.object(r3.Journal, 'append_checkpoint', checkpoint), patch.object(r3, '_write', persist), \
         patch.object(r3, 'check_plan', check):
        try:
            r3.start(plan, 'r5-synthetic-event', SyntheticSource(plan), 'synthetic-owner', target,
                     target/'run', public, target/'scripts.json', now=NOW)
        except (OSError, SharedIntegrityError) as exc:
            require(fired and name in {'ledger', 'job_write', 'summary_write'}, 'unexpected worker failure')
            write(target/'worker-error.json', {'kind': type(exc).__name__, 'detail': str(exc)})
    require(name in SETTLED or fired, 'planned injection did not fire')


def check_case(suite, name, output='evaluation.json'):
    descriptor = check_version(suite)
    target = Path(suite)/name
    public = target/'public' if name == 'isolation' else Path(suite)/'public'
    plan = read_json(target/'candidate-plan.json')
    require(plan['base']['schedule']['plan']['jobs'] == descriptor['jobs'], 'planned denominator changed')
    require(read_json(target/'run/plan.json') == plan, 'saved plan differs from candidate')
    reads = []
    original = evaluator.load_reference_package
    def load(*args):
        reads.append(True)
        return original(*args)
    with offline_guard(), patch.object(evaluator, 'load_reference_package', load):
        try:
            result = evaluator.evaluate(target/'run', public, target/'scripts.json')
        except (ValueError, SharedIntegrityError) as exc:
            require(name in REJECTED and not reads, 'unexpected verification rejection: '+str(exc))
            result = {'status': 'verification_rejected', 'error_kind': type(exc).__name__, 'error': str(exc),
                      'reference_read': False, 'model_metrics': None, 'actual_model_calls': 0}
    if name in SETTLED:
        require(result['status'] == 'offline_evaluator_wiring_verified' and len(reads) == 1, 'expected settled evaluation')
    elif name not in REJECTED:
        require(result['status'] == 'incomplete_evidence_no_reference_read' and not reads, 'expected prefix-only evidence')
    if 'replay' in result:
        rows = result['replay']['rows']
        require(len(rows) == len(descriptor['jobs']), 'lost planned denominator')
        counts = Counter(row['status'] for row in rows)
        sent = sum(row['sent_or_uncertain'] for row in rows)
        if name == 'normal': require(counts['completed'] == len(rows), 'normal batch failed')
        if name == 'ordinary_errors':
            require(counts['failed'] == 3*descriptor['repetitions'] and counts['not_run'] == 0,
                    'ordinary errors did not stop only their tracks')
        if name in {'attempt_budget', 'amount_budget', 'summary_write'}: require(sent == 1, 'budget allowed extra sends')
        if name == 'reserved': require(sent == 0 and counts['not_run'] == len(rows), 'unsent reservation counted as send')
        if name in {'dispatch_started', 'received', 'validated', 'source_guard'}: require(sent == 1, 'continued after stop')
        require(result['replay']['cost']['overall']['input_tokens']['total'] is None and
                result['replay']['cost']['overall']['billed_cost_cny']['total'] is None, 'unknown cost became known')
    write(target/output, result)
    return result


def launch_case(suite, name):
    target, public, plan = prepare_case(suite, name)
    write(target/'launch.json', {'scenario': name, 'plan_sha256': canonical_hash(plan), 'synthetic_only': True})
    proc = subprocess.run([sys.executable, '-m', 'scripts.s4_r5_acceptance', 'worker', '--suite', str(Path(suite).resolve()),
                           '--scenario', name], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    write(target/'process.json', {'exit_code': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr})
    require(proc.returncode == (77 if name in CRASHED else 0), 'unexpected child exit: '+proc.stderr)
    result = check_case(suite, name)
    print(name, result['status'], flush=True)
    return result


def tamper_controls(suite):
    descriptor = check_version(suite)
    outcomes = {}
    for name in TAMPERS:
        target = Path(suite)/('tamper_'+name)
        target.mkdir(exist_ok=False)
        shutil.copytree(Path(suite)/'normal/run', target/'run')
        path = target/'run'/('plan.json' if name == 'fake_real' else 'result.json')
        record = read_json(path)
        if name == 'fake_real': record['execution_kind'] = 'real_model'
        elif name == 'duplicate': record['rows'].append(record['rows'][0])
        else: record['rows'].pop()
        path.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        with offline_guard(), patch.object(evaluator, 'load_reference_package', side_effect=AssertionError('private read before rejection')):
            try:
                evaluator.evaluate(target/'run', Path(suite)/'public', Path(suite)/'normal/scripts.json')
            except (ValueError, SharedIntegrityError) as exc:
                outcomes[name] = {'status': 'rejected_before_reference', 'error': str(exc),
                                  'planned_jobs': len(descriptor['jobs']), 'actual_model_calls': 0}
            else: raise AssertionError('tampering accepted')
        write(target/'evaluation.json', outcomes[name])
    return outcomes


def summarize(suite):
    descriptor = check_version(suite)
    rows = []
    for name in SCENARIOS:
        report = read_json(Path(suite)/name/'evaluation.json')
        replay = report.get('replay')
        rows.append({'scenario': name, 'verification': report['status'], 'planned_jobs': len(descriptor['jobs']),
            'statuses': dict(Counter(r['status'] for r in replay['rows'])) if replay else None,
            'sent_or_uncertain': sum(r['sent_or_uncertain'] for r in replay['rows']) if replay else None,
            'unsent_reservations': sum(r['unsent_reservations'] for r in replay['rows']) if replay else None,
            'reference_read': report['reference_read'], 'report_sha256': digest((Path(suite)/name/'evaluation.json').read_bytes())})
    return {'version': 's4_r5_matrix_v1', 'status': 'scenario_matrix_passed_delivery_pending',
            'acceptance_plan_sha256': digest((Path(suite)/'acceptance-plan.json').read_bytes()), 'rows': rows,
            'tamper_controls': {n: read_json(Path(suite)/('tamper_'+n)/'evaluation.json') for n in TAMPERS},
            'actual_model_calls': 0, 'real_transport_allowed': False, 'model_metrics': None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run-one', 'worker', 'check-one', 'tamper', 'matrix'))
    parser.add_argument('--suite', type=Path, required=True)
    parser.add_argument('--scenario', choices=SCENARIOS)
    parser.add_argument('--output', default='recomputed-evaluation.json')
    args = parser.parse_args()
    if args.command in {'run-one', 'worker', 'check-one'} and args.scenario is None: parser.error('scenario required')
    with offline_guard():
        if args.command == 'prepare': print(json.dumps({'planned_jobs': len(prepare_suite(args.suite)['jobs'])}))
        elif args.command == 'run-one': launch_case(args.suite, args.scenario)
        elif args.command == 'worker': worker(args.suite, args.scenario)
        elif args.command == 'check-one': check_case(args.suite, args.scenario, args.output)
        elif args.command == 'tamper': tamper_controls(args.suite)
        else: write(args.suite/'matrix.json', summarize(args.suite))


if __name__ == '__main__': main()
