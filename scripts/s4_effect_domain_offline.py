"""Offline-only effect-domain integration over the maintained S4 executor/R4.

No live command, credential reader or paid approval intake exists here. This
engineering candidate is not a new executable paid freeze.
"""
import argparse
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from scripts import s4_owner_pilot as base
from scripts import s4_owner_pilot_v2 as previous
from scripts import s4_effect_domain_v1 as domain
from writscope.artifacts import canonical_hash

ROOT = Path(__file__).resolve().parents[1]
VERSION = 's4_effect_domain_offline_v1'
V2_SHA = '903d83e86aaacff67de8d8c7394faa2383fe0705d9db9cb197b56255dd68c21b'
EXTRA_FILES = ('scripts/s4_effect_domain_v1.py', 'scripts/s4_effect_domain_offline.py',
    'specs/s4-effect-domain-v1.md', *('prompts/s4_effect_domain_v1/'+s+'.md' for s in ('common', *domain.STAGES)))


def sources():
    old = base.read(ROOT/'reports/s4-owner-pilot-v2/execution-candidate.json')
    if canonical_hash(old) != V2_SHA:
        raise base.SharedIntegrityError('frozen_v2_anchor_changed')
    actual = previous.file_hashes(old['sources_sha256'])
    if actual != old['sources_sha256']:
        raise base.SharedIntegrityError('frozen_v2_sources_changed')
    return {**actual, **previous.file_hashes(EXTRA_FILES)}


def prepared(price, bundles=base.PUBLIC):
    return {**previous.BASE_PREPARE(price, bundles), 'version': VERSION,
        'execution_kind': 'offline_effect_domain_diagnostic', 'status': 'offline_engineering_candidate_not_paid_freeze',
        'real_transport_allowed': False, 'approval_scope': 'offline_fixed_only_no_real_starts', 'effect_domain': {
            'version': domain.VERSION, 'prior_candidate_sha256': V2_SHA,
            'context_policy': 'task_context_quote_v1_never_authority',
            'compiler': 'typed_context_operations_and_validated_api_operations',
            'scope_policy': 'bounded_zh_en_surface_contradiction_guard_abstain_on_unknown_not_general_semantic_proof',
            'prompt_sha256': {s: base.digest(domain.prompt(s).encode()) for s in domain.STAGES},
            'schema_sha256': {s: {str(a).lower(): canonical_hash(domain.adapter(s, a).json_schema()) for a in (True, False)}
                              for s in domain.STAGES},
            'price_use': 'historical_frozen_snapshot_for_offline_controls_not_new_fee_quote'}}


def interpret(raw, receipt, request, plan):
    payload, finish, error, usage = previous.interpret(raw, receipt, request, plan)
    if error is None:
        try:
            domain.check_response_sources(payload, request)
            usage['effect_domain_source_check'] = 'accepted_structural_grounding_not_semantic_proof'
        except ValueError as exc:
            error = 'effect_domain_source_rejected'
            usage['effect_domain_source_error'] = str(exc)
            usage['stop_round'] = True
    return payload, finish, error, usage


@contextmanager
def wiring():
    if not previous.LOCK.acquire(blocking=False):
        raise RuntimeError('single_concurrency_version_wiring_active')
    try:
        with ExitStack() as stack:
            for name, value in (('sources', sources), ('prepare', prepared), ('adapter', domain.adapter), ('interpret', interpret)):
                stack.enter_context(patch.object(base, name, value))
            # Reuse the existing serializable error/finish/usage parser with the
            # new schema; this changes neither raw bytes nor accounting policy.
            stack.enter_context(patch.object(previous, 'contract', domain))
            for module in (previous.executor, previous.replay.r4):
                stack.enter_context(patch.object(module, 'prompt', domain.prompt))
                stack.enter_context(patch.object(module, 'preview', domain.preview))
                stack.enter_context(patch.object(module, 'QualifiedEngine', domain.DomainEngine))
            yield
    finally:
        previous.LOCK.release()


def prepare(price, bundles=base.PUBLIC):
    with wiring():
        return base.prepare(price, bundles)


def run(plan, output, *, fixed_responses, bundles=base.PUBLIC, hook=None):
    if plan.get('version') != VERSION or plan.get('real_transport_allowed') is not False or fixed_responses is None:
        raise PermissionError('effect_domain_entry_is_fixed_offline_only')
    with wiring():
        return base.run(plan, output, bundles=bundles, fixed_responses=fixed_responses, hook=hook)


def evaluate(directory, bundles=base.PUBLIC):
    plan = base.read(Path(directory)/'plan.json')
    if plan.get('version') != VERSION:
        return previous.evaluate(directory, bundles)
    if base.read(Path(directory)/'origin.json')['mode'] != 'fixed':
        raise PermissionError('effect_domain_offline_plan_cannot_claim_live_provenance')
    with wiring():
        result = previous.BASE_EVALUATE(directory, bundles)
    result.update(effect_domain=plan['effect_domain'], real_execution_allowed=False,
                  semantic_scope='bounded_guard_and_context_compiler_not_general_natural_language_validation')
    return result


def start_live(*args, **kwargs):
    raise PermissionError('effect-domain revision has no live entry; no keys, sending or paid approval')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('prepare'); make.add_argument('--price', type=Path, required=True); make.add_argument('--output', type=Path, required=True)
    run_args = sub.add_parser('offline'); run_args.add_argument('--candidate', type=Path, required=True)
    run_args.add_argument('--responses', type=Path, required=True); run_args.add_argument('--output', type=Path, required=True)
    verify = sub.add_parser('verify'); verify.add_argument('--run', type=Path, required=True); verify.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        plan = prepare(base.read(args.price)); base.write(args.output, plan); print(canonical_hash(plan))
    elif args.command == 'offline':
        result = run(base.read(args.candidate), args.output, fixed_responses=base.read(args.responses)); print(result['rows'])
    else:
        result = evaluate(args.run); base.write(args.output, result); print(result['replay']['status'])


if __name__ == '__main__':
    main()
