"""Fixed-only scoped-meaning/current-event integration; no paid/live entry."""
import argparse
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from scripts import s4_grounded_meaning_v1 as contract
from scripts import s4_owner_pilot_v4 as anchor
from scripts import s4_owner_pilot_v2 as parser
from scripts import s4_owner_pilot as base
from writscope.artifacts import canonical_hash, digest

ROOT = Path(__file__).resolve().parents[1]
VERSION = 's4_meaning_event_offline_v1'
OLD_SHA = '7fbb113b66da0ec3c1e38dfd2f84333affd84501c3eec73fe2e0de8a8c0837a4'
EXTRA_FILES = ('scripts/s4_grounded_meaning_v1.py', 'scripts/s4_meaning_event_offline.py',
    'specs/s4-meaning-event-v1.md', *('prompts/s4_meaning_event_v1/'+s+'.md' for s in ('common', *contract.STAGES)))


def sources():
    prior = base.read(ROOT/'reports/s4-owner-pilot-v4/execution-candidate.json')
    if canonical_hash(prior) != OLD_SHA or anchor.sources() != prior['sources_sha256']:
        raise base.SharedIntegrityError('frozen_v4_anchor_or_sources_changed')
    return {**prior['sources_sha256'], **parser.file_hashes(EXTRA_FILES)}


def prepared(price, bundles=base.PUBLIC):
    value = anchor.prepared(price, bundles)
    value['prior_pilot_revision'] = value.pop('pilot_revision')
    value.update(version=VERSION, execution_kind='offline_meaning_event_diagnostic',
        status='offline_engineering_candidate_not_paid_freeze', real_transport_allowed=False,
        approval_scope='offline_fixed_only_no_real_starts',
        execution_requires='fixed_offline_only_no_live_or_credential_interface',
        real_sending_currently_approved=False, actual_execution_approval=None,
        meaning_event_revision={'version': contract.VERSION, 'prior_v4_sha256': OLD_SHA,
            'meaning': 'explicit_containing_user_API_anchor_and_literal_meaning_source',
            'explanation': 'advisory_unverified_excluded_from_normative_identity',
            'no_change': 'quote_free_strict_shape_bound_to_verified_current_event_by_compiler',
            'prompt_sha256': {s: digest(contract.prompt(s).encode()) for s in contract.STAGES},
            'schema_sha256': {s: {str(a).lower(): canonical_hash(contract.adapter(s,a).json_schema()) for a in (True,False)} for s in contract.STAGES},
            'general_semantic_proof': False, 'price_use': 'historical_fixed_control_only_not_a_new_fee_quote'})
    return value


def interpret(raw, receipt, request, plan):
    payload, finish, error, usage = parser.interpret(raw, receipt, request, plan)
    if error is None:
        try:
            usage['meaning_event_sources'] = contract.check_response_sources(payload, request)
        except ValueError as exc:
            error = 'meaning_event_source_rejected'
            usage.update(meaning_event_source_error=str(exc), stop_round=True)
    if error is None:
        try:
            contract.check_maintained_summary(payload, request)
        except ValueError as exc:
            error = 'meaning_event_summary_mismatch'
            usage['meaning_event_summary_error'] = str(exc)
    return payload, finish, error, usage


@contextmanager
def wiring():
    with anchor.wiring():
        with ExitStack() as stack:
            for name, value in (('prepare',prepared), ('sources',sources), ('adapter',contract.adapter), ('interpret',interpret)):
                stack.enter_context(patch.object(base,name,value))
            stack.enter_context(patch.object(parser,'contract',contract))
            for module in (parser.executor,parser.replay.r4):
                for name,value in (('prompt',contract.prompt),('preview',contract.preview),('QualifiedEngine',contract.DomainEngine)):
                    stack.enter_context(patch.object(module,name,value))
            yield


def prepare(price, bundles=base.PUBLIC):
    with wiring():
        return base.prepare(price,bundles)


def run(plan, output, *, fixed_responses, bundles=base.PUBLIC, hook=None):
    if plan.get('version') != VERSION or plan.get('real_transport_allowed') is not False or fixed_responses is None:
        raise PermissionError('meaning_event_revision_is_fixed_offline_only')
    with wiring():
        return base.run(plan,output,bundles=bundles,fixed_responses=fixed_responses,hook=hook)


def evaluate(directory, bundles=base.PUBLIC):
    plan = base.read(Path(directory)/'plan.json')
    if plan.get('version') != VERSION:
        return anchor.evaluate(directory,bundles)
    if base.read(Path(directory)/'origin.json')['mode'] != 'fixed':
        raise PermissionError('meaning_event_offline_cannot_claim_real_provenance')
    with wiring():
        result = parser.BASE_EVALUATE(directory,bundles)
    result.update(meaning_event_revision=plan['meaning_event_revision'], real_execution_allowed=False)
    return result


def start_live(*args, **kwargs):
    raise PermissionError('meaning/event revision has no live entry or credential/approval intake')


def main():
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='command',required=True)
    make=sub.add_parser('prepare'); make.add_argument('--price',type=Path,required=True); make.add_argument('--output',type=Path,required=True)
    fixed=sub.add_parser('offline'); fixed.add_argument('--candidate',type=Path,required=True); fixed.add_argument('--responses',type=Path,required=True); fixed.add_argument('--output',type=Path,required=True)
    verify=sub.add_parser('verify'); verify.add_argument('--run',type=Path,required=True); verify.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.command=='prepare':
        plan=prepare(base.read(args.price)); base.write(args.output,plan); print(canonical_hash(plan))
    elif args.command=='offline':
        print([r['status'] for r in run(base.read(args.candidate),args.output,fixed_responses=base.read(args.responses))['rows']])
    else:
        report=evaluate(args.run); base.write(args.output,report); print(report['replay']['status'])


if __name__=='__main__':
    main()
