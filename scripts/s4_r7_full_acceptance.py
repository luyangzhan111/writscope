"""Full-batch acceptance orchestration only; delegates all methods to worker v1."""
import argparse
import base64
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
from unittest.mock import patch

from scripts import s4_r7_worker_runtime as runtime
from scripts.s4_p4_scripts import generate
from scripts.s4_r5_acceptance import POLICY, offline_guard
from scripts.s4_shared_transport_offline import SharedIntegrityError
from writscope.artifacts import digest, canonical_hash

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT/'reports/s4-r6-candidate-v1/public'
CASES = [f's4_{n:03d}' for n in range(1, 31)]


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def prepare(suite):
    suite.mkdir()
    scripts = generate(PUBLIC, CASES)
    write(suite/'scripts.json', scripts)
    responses = {}
    for job, script in scripts['jobs'].items():
        responses[job] = []
        for step in script['steps']:
            envelope = {'model': 'deepseek-flash', 'choices': [{'message': {'content': step['text']}, 'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 120, 'completion_tokens': 30, 'total_tokens': 150,
                                  'prompt_cache_hit_tokens': 20, 'prompt_cache_miss_tokens': 100,
                                  'completion_tokens_details': {'reasoning_tokens': 0}}}
            responses[job].append({'body_base64': base64.b64encode(json.dumps(envelope).encode()).decode()})
    write(suite/'provider-fixtures.json', {'version': 's4_http_fixture_v1', 'jobs': responses})
    plan = runtime.prepare(PUBLIC, suite/'scripts.json', suite/'provider-fixtures.json', CASES, 3, POLICY)
    assert len(plan['base']['schedule']['plan']['jobs']) == 270
    write(suite/'plan.json', plan)
    write(suite/'bindings.json', {'runtime_sources': runtime.sources(), 'orchestrator_sha256': digest(Path(__file__).read_bytes()),
          'plan_sha256': canonical_hash(plan), 'actual_model_calls': 0, 'live_allowed': False})


def check(suite):
    binding = json.loads((suite/'bindings.json').read_text(encoding='utf-8'))
    assert binding['runtime_sources'] == runtime.sources()
    assert binding['orchestrator_sha256'] == digest(Path(__file__).read_bytes())


def replay(suite, directory, output):
    check(suite)
    result = runtime.evaluate(directory, PUBLIC, suite/'scripts.json')
    assert result['replay']['planned_jobs'] == 270
    assert result['model_metrics'] is result['accuracy'] is None
    write(output, result)
    print(json.dumps({'scenario': directory.name, 'status': result['status'],
                      'jobs': Counter(r['status'] for r in result['replay']['rows']),
                      'attempts': sum(r['sent_or_uncertain'] for r in result['replay']['rows'])}), flush=True)
    return result


def normal(suite):
    check(suite)
    plan = json.loads((suite/'plan.json').read_text(encoding='utf-8'))
    original = runtime.execute_job
    count = 0
    def progress(*args, **kwargs):
        nonlocal count
        result = original(*args, **kwargs)
        count += 1
        if count % 5 == 0:
            print(json.dumps({'jobs_durable': count, 'total': 270}), flush=True)
        return result
    with patch.object(runtime, 'execute_job', progress):
        runtime.run(plan, suite/'normal', PUBLIC, suite/'scripts.json', suite/'provider-fixtures.json')
    result = replay(suite, suite/'normal', suite/'normal-evaluation.json')
    assert all(r['status'] == 'completed' for r in result['replay']['rows'])
    assert len(result['selected_complete_pairs']) == 12


SCENARIOS = ('attempt_budget', 'amount_budget', 'truncation_then_budget', 'size_then_budget', 'service_failures',
             'reserved', 'dispatch_started', 'received', 'validated', 'transition_started', 'transition_finished',
             'send_write', 'receive_write', 'startup_timeout')


def fault(suite, name):
    check(suite)
    folder = suite/'faults'/name
    folder.mkdir(parents=True)
    fixtures = json.loads((suite/'provider-fixtures.json').read_text(encoding='utf-8'))
    policy = dict(POLICY)
    if name == 'attempt_budget': policy['max_attempts'] = 1
    if name == 'amount_budget': policy['cap_micro'] = 1
    if name in ('truncation_then_budget', 'size_then_budget'): policy['max_attempts'] = 3
    for responses in fixtures['jobs'].values():
        if name == 'truncation_then_budget':
            body = json.loads(base64.b64decode(responses[0]['body_base64']))
            body['choices'][0]['finish_reason'] = 'length'
            responses[0]['body_base64'] = base64.b64encode(json.dumps(body).encode()).decode()
        if name == 'size_then_budget': responses[0]['body_base64'] = base64.b64encode(b'x'*200000).decode()
        if name == 'service_failures': responses[0] = {'status': 503, 'body_base64': 'e30='}
        if name == 'startup_timeout': responses[0]['startup_delay'] = 10
    write(folder/'fixtures.json', fixtures)
    plan = runtime.prepare(PUBLIC, suite/'scripts.json', folder/'fixtures.json', CASES, 3, policy,
                           deadline=5 if name == 'startup_timeout' else 90)
    from contextlib import ExitStack
    original_transition, original_reserve = runtime.Journal.transition, runtime.Journal.reserve
    original_checkpoint, original_receive = runtime.Journal.append_checkpoint, runtime.Journal.persist_received
    def reserve(self, *a, **k):
        ident = original_reserve(self, *a, **k)
        if name == 'reserved': raise KeyboardInterrupt('fixed reserved window')
        return ident
    def transition(self, ident, before, after, **fields):
        if name == 'send_write' and after == 'dispatch_started': raise OSError('fixed send write failure')
        value = original_transition(self, ident, before, after, **fields)
        if (name == after == 'dispatch_started') or (name == 'validated' and after == 'completed'):
            raise KeyboardInterrupt('fixed '+name+' window')
        return value
    def received(self, *a, **k):
        if name == 'receive_write': raise OSError('fixed receive write failure')
        value = original_receive(self, *a, **k)
        if name == 'received': raise KeyboardInterrupt('fixed received window')
        return value
    def checkpoint(self, job, kind, payload):
        value = original_checkpoint(self, job, kind, payload)
        if name == kind: raise KeyboardInterrupt('fixed '+kind+' window')
        return value
    interrupted = False
    with ExitStack() as stack:
        for attr, value in (('reserve', reserve), ('transition', transition), ('persist_received', received), ('append_checkpoint', checkpoint)):
            stack.enter_context(patch.object(runtime.Journal, attr, value))
        try: runtime.run(plan, folder/'run', PUBLIC, suite/'scripts.json', folder/'fixtures.json')
        except KeyboardInterrupt: interrupted = True
    write(folder/'control.json', {'injected': name, 'interrupted': interrupted, 'actual_model_calls': 0})
    result = replay(suite, folder/'run', folder/'evaluation.json')
    if name in SCENARIOS[5:11] or name in ('send_write', 'receive_write', 'startup_timeout'):
        assert result['reference_read'] is False


def tamper(suite):
    # Reuse saved worker-v1 small run; these controls prove rejection, not 270-job coverage.
    source = ROOT/'reports/s4-r7-worker-v1/test-evidence/response_worker0/source'
    import scripts.s4_r4_evaluator as evaluator
    for name in ('fake_real', 'receipt', 'usage', 'checkpoint', 'omitted_summary'):
        target = suite/'tamper'/name
        target.mkdir(parents=True)
        shutil.copytree(source/'run', target/'run')
        if name == 'fake_real':
            path = target/'run/plan.json'; p = json.loads(path.read_text(encoding='utf-8')); p['execution_kind'] = 'real_model'; path.write_text(json.dumps(p))
        elif name == 'omitted_summary':
            path = target/'run/result.json'; p = json.loads(path.read_text(encoding='utf-8')); p['rows'].pop(); path.write_text(json.dumps(p))
        else:
            with sqlite3.connect(target/'run/journal.sqlite') as db:
                if name == 'checkpoint': db.execute("UPDATE checkpoint SET event_hash='bad' WHERE seq=1")
                elif name == 'usage':
                    p = json.loads(db.execute('SELECT usage FROM attempt WHERE id=1').fetchone()[0]);p['input_tokens']=0
                    db.execute('UPDATE attempt SET usage=? WHERE id=1',(json.dumps(p),))
                else:
                    p = json.loads(db.execute('SELECT receipt FROM transport_receipt WHERE attempt=1').fetchone()[0]);p['worker']['child_reaped']=False
                    db.execute('UPDATE transport_receipt SET receipt=? WHERE attempt=1',(json.dumps(p),))
        with patch.object(evaluator, 'load_reference_package', side_effect=AssertionError('private read before rejection')):
            try: runtime.evaluate(target/'run', PUBLIC, source/'legacy-script-binding.json')
            except (ValueError, SharedIntegrityError, IndexError, KeyError) as exc:
                write(target/'rejection.json', {'rejected': True, 'kind': type(exc).__name__, 'detail': str(exc), 'actual_model_calls': 0})
            else: raise AssertionError('tamper accepted: '+name)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('prepare','normal','faults','replay'))
    p.add_argument('--suite', type=Path, required=True)
    args = p.parse_args()
    if args.command == 'prepare': prepare(args.suite)
    elif args.command == 'normal': normal(args.suite)
    elif args.command == 'faults':
        for name in SCENARIOS: fault(args.suite, name)
        tamper(args.suite)
    else:
        replay(args.suite, args.suite/'normal', args.suite/'normal-replay.json')


if __name__ == '__main__': main()
