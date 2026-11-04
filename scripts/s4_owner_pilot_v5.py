"""Exact owner-pilot v5 over the frozen scoped-meaning/current-event revision.

Preparation/offline/replay never read a key. Future live execution requires a
separate exact approval and the owner's local single-use console confirmation.
"""
import argparse
from contextlib import ExitStack, contextmanager
import getpass
from pathlib import Path
from unittest.mock import patch

from scripts import s4_meaning_event_offline as offline
from scripts import s4_owner_pilot as base
from writscope.artifacts import canonical_hash

ROOT = Path(__file__).resolve().parents[1]
VERSION = 's4_owner_pilot_candidate_v5'
OFFLINE_SHA = '3df08c015b78958c6ebae857e8942b8612965ff762c3ce5743939047875c37c9'
EXTRA_FILES = ('scripts/s4_owner_pilot_v5.py','specs/s4-owner-pilot-v5.md')
MATERIALS = ['single_case_s4_027_public_bundle','frozen_prompt_and_schema','visible_history',
    'own_accepted_state_context_scoped_meaning_and_constraint_coverage','permitted_lookup_result']


def sources():
    prior = base.read(ROOT/'reports/s4-meaning-event-offline-v1/final/offline-candidate.json')
    if canonical_hash(prior) != OFFLINE_SHA or offline.sources() != prior['sources_sha256']:
        raise base.SharedIntegrityError('final_meaning_event_offline_anchor_or_sources_changed')
    return {**prior['sources_sha256'], **offline.parser.file_hashes(EXTRA_FILES)}


def prepared(price,bundles=base.PUBLIC):
    value = offline.prepared(price,bundles)
    del value['real_transport_allowed']  # The old fixed-only entry stays closed.
    value.update(version=VERSION, status='frozen_candidate_pending_execution_approval',
        execution_kind=base.MODE, approval_scope='one_start_no_resume_no_retry',
        real_sending_currently_approved=False, actual_execution_approval=None,
        execution_requires='new_exact_single_run_owner_approval_and_local_console_before_key',
        materials=MATERIALS.copy(), pilot_revision={
            'offline_candidate_sha256':OFFLINE_SHA,'contract_version':offline.contract.VERSION,
            'live_entry':'scripts.s4_owner_pilot_v5','model_improvement':'not_tested',
            'general_semantic_proof':False,'formal_270_trajectory_comparison':False})
    value['meaning_event_revision'] = {**value['meaning_event_revision'],
        'price_use':'new_public_snapshot_recheck_before_console_and_each_live_request'}
    return value


@contextmanager
def wiring():
    with offline.wiring():
        with ExitStack() as stack:
            stack.enter_context(patch.object(base,'prepare',prepared))
            stack.enter_context(patch.object(base,'sources',sources))
            yield


def prepare(price,bundles=base.PUBLIC):
    with wiring():
        return base.prepare(price,bundles)


def check_version(plan):
    if plan.get('version') != VERSION:
        raise PermissionError('v5_requires_new_exact_candidate')


def run(plan,output,*,fixed_responses,bundles=base.PUBLIC,hook=None):
    check_version(plan)
    if fixed_responses is None:
        raise PermissionError('use_new_owner_console_approval_before_live_transport')
    with wiring():
        return base.run(plan,output,bundles=bundles,fixed_responses=fixed_responses,hook=hook)


def start_live(plan,approval,claims,output,*,prompt=input,key_reader=None):
    """Future owner launch only; never used by preparation or fixed controls."""
    check_version(plan)
    with wiring():
        launch = base.console_gate(plan,approval,claims,prompt=prompt)
        key = (key_reader or getpass.getpass)('DeepSeek API key (local process memory only; not saved): ')
        if not key or len(key)>512 or any(ord(ch)<33 or ord(ch)>126 for ch in key):
            raise PermissionError('invalid_credential_claim_retained_no_send')
        return base.run(plan,output,credential=key,launch=launch)


def evaluate(directory,bundles=base.PUBLIC):
    plan = base.read(Path(directory)/'plan.json')
    if plan.get('version') != VERSION:
        return offline.evaluate(directory,bundles)
    with wiring():
        result = offline.parser.BASE_EVALUATE(directory,bundles)
    result.update(pilot_revision=plan['pilot_revision'],meaning_event_revision=plan['meaning_event_revision'],
        candidate_is_execution_approval=False,
        semantic_scope='bounded_source_and_current_event_binding_not_NL_or_translation_proof')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='command',required=True)
    make=sub.add_parser('prepare'); make.add_argument('--price',type=Path,required=True); make.add_argument('--output',type=Path,required=True)
    verify=sub.add_parser('verify'); verify.add_argument('--run',type=Path,required=True); verify.add_argument('--output',type=Path,required=True)
    for command in ('offline','run'):
        entry=sub.add_parser(command); entry.add_argument('--candidate',type=Path,required=True); entry.add_argument('--output',type=Path,required=True)
        if command=='run':
            entry.add_argument('--approval',type=Path,required=True); entry.add_argument('--claims',type=Path,required=True)
        else:
            entry.add_argument('--responses',type=Path,required=True)
    args=p.parse_args()
    if args.command=='prepare':
        plan=prepare(base.read(args.price)); base.write(args.output,plan); print(canonical_hash(plan))
    elif args.command=='verify':
        report=evaluate(args.run); base.write(args.output,report); print(report['replay']['status'])
    elif args.command=='offline':
        print([r['status'] for r in run(base.read(args.candidate),args.output,fixed_responses=base.read(args.responses))['rows']])
    else:
        result=start_live(base.read(args.candidate),base.read(args.approval),args.claims,args.output)
        print([r['status'] for r in result['rows']]); print('Single start used. Preserve records; do not restart or resume.')


if __name__=='__main__':
    main()
