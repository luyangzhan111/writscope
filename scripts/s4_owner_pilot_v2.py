"""Versioned contract wiring over the existing owner-pilot runner and R4.

No duplicate method runner, journal, HTTP worker or state kernel. Old failures
remain v1 failures. Every new live start requires a new exact-plan approval.
"""
import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import getpass
from pathlib import Path
import threading
from unittest.mock import patch

from pydantic import ValidationError

from scripts import s4_owner_pilot as base
from scripts import s4_owner_pilot_replay as replay
from scripts import s4_output_contract_v2 as contract
from scripts import s4_r7_response_executor as executor
from writscope.artifacts import canonical_hash, digest
from writscope.contracts import parse_json

ROOT = Path(__file__).resolve().parents[1]
VERSION = 's4_owner_pilot_candidate_v2'
OLD_SHA = '472fd6e5ef38dc420f4dcf9603ab3474b95e10e4af297d9d59b65b510bd252e1'
BASE_PREPARE, BASE_INTERPRET, BASE_EVALUATE = base.prepare, base.interpret, replay.evaluate
LOCK = threading.Lock()
EXTRA_FILES = ('scripts/s4_output_contract_v2.py', 'scripts/s4_owner_pilot_v2.py',
               'specs/s4-owner-pilot-v2.md', *('prompts/s4_candidate_v2/'+name+'.md'
                    for name in ('common', *contract.STAGES)))


def anchor():
    value = base.read(ROOT/'reports/s4-owner-pilot-v1/execution-candidate.json')
    if canonical_hash(value) != OLD_SHA:
        raise base.SharedIntegrityError('legacy_anchor_changed')
    return value


def file_hashes(names):
    """Check each parent once per scan; no cross-call cache can hide drift."""
    seen, result = set(), {}
    for name in sorted(names):
        path = ROOT/name
        if path.is_absolute() and not path.resolve().is_relative_to(ROOT.resolve()):
            raise base.SharedIntegrityError('source_outside_project')
        for part in (path, *path.parents):
            if part not in seen:
                if part.is_symlink() or part.is_junction():
                    raise base.SharedIntegrityError('linked_source')
                seen.add(part)
        result[name] = digest(path.read_bytes())
    return result


def legacy_sources():
    expected = anchor()['sources_sha256']
    actual = file_hashes(expected)
    if actual != expected:
        raise base.SharedIntegrityError('frozen_legacy_source_changed')
    return actual


def sources():
    return {**legacy_sources(), **file_hashes(EXTRA_FILES)}


def descriptor():
    return {'version': contract.VERSION, 'legacy_candidate_sha256': OLD_SHA,
        'prompt_sha256': {s: digest(contract.prompt(s).encode()) for s in contract.STAGES},
        'schema_sha256': {s: {str(a).lower(): canonical_hash(contract.adapter(s, a).json_schema())
                             for a in (True, False)} for s in contract.STAGES},
        'parser': 'operation_shape_validation_without_response_repair',
        'kernel': 'unchanged_source_scope_lifecycle_conditions_exact_value',
        'reference_loader': 'legacy_author_data_binding_not_v2_prompt_approval'}


def prepared(price, bundles=base.PUBLIC):
    # BASE_PREPARE resolves the bound source function within wiring().
    return {**BASE_PREPARE(price, bundles), 'version': VERSION, 'output_contract_v2': descriptor()}


def interpret(raw, receipt, request, plan):
    payload, finish, error, usage = BASE_INTERPRET(raw, receipt, request, plan)
    if error == 'invalid_output':
        if finish is not None and finish != 'stop':
            usage['output_contract_errors'] = [{'type': 'non_stop_finish_reason',
                'loc': ['choices', 0, 'finish_reason'], 'msg': 'stage output requires finish_reason stop'}]
            return payload, finish, 'truncated_or_unknown_finish', usage
        try:
            body = parse_json(raw.decode('utf8'))
            content = body['choices'][0]['message']['content']
            contract.adapter(request['stage'], request['lookup_allowed']).validate_python(parse_json(content))
        except ValidationError as exc:
            usage['output_contract_errors'] = [
                {'type': e['type'], 'loc': list(e['loc']), 'msg': e['msg']}
                for e in exc.errors(include_input=False, include_url=False)[:32]]
        except (ValueError, UnicodeError, TypeError, KeyError, IndexError):
            usage['output_contract_errors'] = [{'type': 'invalid_json_or_envelope', 'loc': [],
                                                'msg': 'response is not a valid stage JSON result'}]
        error = 'output_contract_invalid'
    return payload, finish, error, usage


@contextmanager
def wiring(*, legacy=False):
    if not LOCK.acquire(blocking=False):
        raise RuntimeError('single_concurrency_version_wiring_active')
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(base, 'sources', legacy_sources if legacy else sources))
            if not legacy:
                stack.enter_context(patch.object(base, 'prepare', prepared))
                stack.enter_context(patch.object(base, 'adapter', contract.adapter))
                stack.enter_context(patch.object(base, 'interpret', interpret))
                for module in (executor, replay.r4):
                    stack.enter_context(patch.object(module, 'prompt', contract.prompt))
                    stack.enter_context(patch.object(module, 'preview', contract.preview))
            yield
    finally:
        LOCK.release()


def prepare(price, bundles=base.PUBLIC):
    with wiring():
        return base.prepare(price, bundles)


def run(plan, output, **kwargs):
    if plan.get('version') != VERSION:
        raise PermissionError('v2_entry_rejects_old_execution_plan')
    with wiring():
        return base.run(plan, output, **kwargs)


def evaluate(directory, bundles=base.PUBLIC):
    plan = base.read(Path(directory)/'plan.json')
    old = plan.get('version') == 's4_owner_pilot_candidate_v1'
    if old and canonical_hash(plan) != OLD_SHA or not old and plan.get('version') != VERSION:
        raise base.SharedIntegrityError('unsupported_replay_plan')
    with wiring(legacy=old):
        result = BASE_EVALUATE(directory, bundles)
    if not old:
        result['output_contract_v2'] = plan['output_contract_v2']
        result['v2_method_policy'] = 'new_prompt_schema_candidate_not_formal_research_freeze'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('prepare')
    make.add_argument('--price', type=Path, required=True); make.add_argument('--output', type=Path, required=True)
    verify = sub.add_parser('verify')
    verify.add_argument('--run', type=Path, required=True); verify.add_argument('--output', type=Path, required=True)
    for command in ('run', 'offline'):
        entry = sub.add_parser(command)
        entry.add_argument('--candidate', type=Path, required=True); entry.add_argument('--output', type=Path, required=True)
        if command == 'run':
            entry.add_argument('--approval', type=Path, required=True); entry.add_argument('--claims', type=Path, required=True)
        else:
            entry.add_argument('--responses', type=Path, required=True, help='fixed test responses only; no credential access')
    args = parser.parse_args()
    if args.command == 'prepare':
        plan = prepare(base.read(args.price)); base.write(args.output, plan); print(canonical_hash(plan))
    elif args.command == 'verify':
        result = evaluate(args.run); base.write(args.output, result)
        print(result['replay']['status'])
    elif args.command == 'offline':
        result = run(base.read(args.candidate), args.output, fixed_responses=base.read(args.responses))
        print({s: sum(r['status'] == s for r in result['rows']) for s in ('completed', 'failed', 'not_run')})
    else:
        plan, approval = base.read(args.candidate), base.read(args.approval)
        if plan.get('version') != VERSION:
            raise PermissionError('v2_entry_rejects_old_execution_plan')
        with wiring():
            launch = base.console_gate(plan, approval, args.claims)
            key = getpass.getpass('DeepSeek API key（仅本进程内存，不保存）：')
            if not key or len(key) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in key):
                raise PermissionError('invalid_credential_claim_retained_no_send')
            result = base.run(plan, args.output, credential=key, launch=launch)
        print({s: sum(r['status'] == s for r in result['rows']) for s in ('completed', 'failed', 'not_run')})
        print('本轮已结束；请离线核对保存记录，不要直接重跑。')


if __name__ == '__main__':
    main()
