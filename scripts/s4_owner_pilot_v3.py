"""Owner-pilot v3: gated single-run candidate over the frozen effect-domain revision.

Prepare/offline/verify never read credentials. Only the owner console launch
accepts a NEW exact approval, then creates a one-use claim before asking a key.
No approval is supplied or inferred by this module.
"""
import argparse
from contextlib import ExitStack, contextmanager
import getpass
from pathlib import Path
from unittest.mock import patch

from scripts import s4_effect_domain_offline as offline
from scripts import s4_owner_pilot as base
from writscope.artifacts import canonical_hash

ROOT = Path(__file__).resolve().parents[1]
VERSION = 's4_owner_pilot_candidate_v3'
OFFLINE_SHA = '74bdc373582dc7daae7b21a3e0d8feefe22cbb2e72e107873ba2b727a220ea02'
EXTRA_FILES = ('scripts/s4_owner_pilot_v3.py', 'specs/s4-owner-pilot-v3.md')
MATERIALS = ['single_case_s4_027_public_bundle', 'frozen_prompt_and_schema', 'visible_history',
    'own_accepted_authorization_state_and_non_authoritative_task_context', 'permitted_lookup_result']


def sources():
    anchor = base.read(ROOT/'reports/s4-effect-domain-offline-v1/controls/offline-candidate.json')
    if canonical_hash(anchor) != OFFLINE_SHA:
        raise base.SharedIntegrityError('effect_domain_offline_anchor_changed')
    actual = offline.sources()
    if actual != anchor['sources_sha256']:
        raise base.SharedIntegrityError('effect_domain_offline_sources_changed')
    return {**actual, **offline.previous.file_hashes(EXTRA_FILES)}


def prepared(price, bundles=base.PUBLIC):
    value = offline.prepared(price, bundles)
    del value['real_transport_allowed']  # Offline-only entry remains unchanged.
    value.update(version=VERSION, execution_kind=base.MODE,
        status='frozen_candidate_pending_execution_approval', approval_scope='one_start_no_resume_no_retry',
        real_sending_currently_approved=False, actual_execution_approval=None,
        execution_requires='new_exact_single_run_owner_approval_and_local_console_before_key',
        materials=MATERIALS.copy(), never_send=[*value['never_send'], 'historical_model_responses',
            'semantic_audit_or_counterfactual_controls', 'approval_or_credentials'],
        pilot_revision={'offline_candidate_sha256': OFFLINE_SHA, 'offline_contract_version': offline.domain.VERSION,
            'live_entry': 'scripts.s4_owner_pilot_v3', 'semantic_improvement': 'not_tested',
            'general_semantic_proof': False, 'formal_270_trajectory_comparison': False},
        conditional_reservation={'currency': 'CNY', 'proposed_cap': '30.00', 'per_attempt': '2.129920',
            'maximum_13_attempts': '27.688960', 'expected_bill': None, 'provider_bill_hard_cap': False,
            'billable_input_bound_verified': False, 'unknown_cost_is_not_zero': True})
    value['effect_domain'] = {**value['effect_domain'],
        'price_use': 'new_public_snapshot_recheck_required_before_console_and_each_live_request'}
    return value


@contextmanager
def wiring():
    # Reuse the existing serial lock, scope/compiler, canonical executor and R4.
    with offline.wiring():
        with ExitStack() as stack:
            stack.enter_context(patch.object(base, 'prepare', prepared))
            stack.enter_context(patch.object(base, 'sources', sources))
            yield


def prepare(price, bundles=base.PUBLIC):
    with wiring():
        return base.prepare(price, bundles)


def check_version(plan):
    if plan.get('version') != VERSION:
        raise PermissionError('v3_entry_requires_new_exact_candidate')


def run(plan, output, *, fixed_responses, bundles=base.PUBLIC, hook=None):
    check_version(plan)
    if fixed_responses is None:
        raise PermissionError('use_owner_console_launch_for_live_transport')
    with wiring():
        return base.run(plan, output, bundles=bundles, fixed_responses=fixed_responses, hook=hook)


def start_live(plan, approval, claims, output, *, prompt=input, key_reader=None):
    """Owner-only local console path, never invoked by candidate preparation."""
    check_version(plan)
    # Reject stale/mismatched approval before key access using the unchanged
    # trusted-owner (not host-attested) console and durable one-use claim.
    with wiring():
        launch = base.console_gate(plan, approval, claims, prompt=prompt)
        reader = key_reader or getpass.getpass
        key = reader('DeepSeek API key (local process memory only; not saved): ')
        if not key or len(key) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise PermissionError('invalid_credential_claim_retained_no_send')
        return base.run(plan, output, credential=key, launch=launch)


def evaluate(directory, bundles=base.PUBLIC):
    plan = base.read(Path(directory)/'plan.json')
    if plan.get('version') != VERSION:
        return offline.evaluate(directory, bundles)
    with wiring():
        result = offline.previous.BASE_EVALUATE(directory, bundles)
    result.update(pilot_revision=plan['pilot_revision'], effect_domain=plan['effect_domain'],
        candidate_is_execution_approval=False,
        semantic_scope='bounded_guard_and_context_compiler_not_general_natural_language_validation')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('prepare'); make.add_argument('--price', type=Path, required=True); make.add_argument('--output', type=Path, required=True)
    verify = sub.add_parser('verify'); verify.add_argument('--run', type=Path, required=True); verify.add_argument('--output', type=Path, required=True)
    for command in ('run', 'offline'):
        entry = sub.add_parser(command); entry.add_argument('--candidate', type=Path, required=True)
        entry.add_argument('--output', type=Path, required=True)
        if command == 'run':
            entry.add_argument('--approval', type=Path, required=True); entry.add_argument('--claims', type=Path, required=True)
        else:
            entry.add_argument('--responses', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        plan = prepare(base.read(args.price)); base.write(args.output, plan); print(canonical_hash(plan))
    elif args.command == 'verify':
        report = evaluate(args.run); base.write(args.output, report); print(report['replay']['status'])
    elif args.command == 'offline':
        result = run(base.read(args.candidate), args.output, fixed_responses=base.read(args.responses))
        print([r['status'] for r in result['rows']])
    else:
        result = start_live(base.read(args.candidate), base.read(args.approval), args.claims, args.output)
        print([r['status'] for r in result['rows']])
        print('Single start used. Preserve records; do not restart or resume.')


if __name__ == '__main__':
    main()
