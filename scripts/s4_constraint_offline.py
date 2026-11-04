"""Fixed-response-only integration for closed API constraints; no live entry."""
import argparse
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from scripts import s4_constraint_representation_v1 as contract
from scripts import s4_owner_pilot_v3 as anchor
from scripts import s4_owner_pilot as base
from writscope.artifacts import canonical_hash, digest

ROOT = Path(__file__).resolve().parents[1]
VERSION = 's4_constraint_representation_offline_v1'
OLD_SHA = '903c99f45914c1e0e12dbc19d545aa99dfd9c6d7b4c0a72aee89154e2a116695'
EXTRA_FILES = ('scripts/s4_constraint_representation_v1.py', 'scripts/s4_constraint_offline.py',
    'specs/s4-constraint-representation-v1.md', *('prompts/s4_constraint_v1/'+s+'.md' for s in ('common', *contract.STAGES)))


def sources():
    prior = base.read(ROOT/'reports/s4-owner-pilot-v3/execution-candidate.json')
    if canonical_hash(prior) != OLD_SHA:
        raise base.SharedIntegrityError('frozen_v3_anchor_changed')
    actual = anchor.sources()
    if actual != prior['sources_sha256']:
        raise base.SharedIntegrityError('frozen_v3_sources_changed')
    return {**actual, **anchor.offline.previous.file_hashes(EXTRA_FILES)}


def prepared(price, bundles=base.PUBLIC):
    value = anchor.prepared(price, bundles)
    value['prior_pilot_revision'] = value.pop('pilot_revision')
    value.update(version=VERSION, execution_kind='offline_constraint_representation_diagnostic',
        status='offline_engineering_candidate_not_paid_freeze', approval_scope='offline_fixed_only_no_real_starts',
        execution_requires='fixed_offline_only_no_live_or_credential_interface',
        real_transport_allowed=False, actual_execution_approval=None, real_sending_currently_approved=False,
        constraint_representation={'version': contract.VERSION, 'prior_v3_sha256': OLD_SHA,
            'coverage_policy': 'one_closed_predicate_to_four_operation_map_same_for_all_methods',
            'final_summary': 'all_active_maintained_constraints_and_exact_typed_predicates',
            'prompt_sha256': {s: digest(contract.prompt(s).encode()) for s in contract.STAGES},
            'schema_sha256': {s: {str(a).lower(): canonical_hash(contract.adapter(s,a).json_schema()) for a in (True,False)} for s in contract.STAGES},
            'general_semantic_proof': False, 'price_use': 'historical_snapshot_for_fixed_controls_not_new_fee_quote'})
    return value


def interpret(raw, receipt, request, plan):
    # Reuse the actual-body/finish/usage parser and earlier domain source checks.
    payload, finish, error, usage = anchor.offline.interpret(raw, receipt, request, plan)
    if error is None:
        try:
            contract.check_response_sources(payload, request)
            usage['API_constraint_source_check'] = 'accepted_structural_grounding_not_semantic_proof'
        except ValueError as exc:
            error = 'API_constraint_source_rejected'
            usage.update(API_constraint_source_error=str(exc), stop_round=True)
    if error is None:
        try:
            contract.check_maintained_summary(payload, request)
        except ValueError as exc:
            error = 'API_constraint_summary_mismatch'
            usage['API_constraint_summary_error'] = str(exc)
    return payload, finish, error, usage


@contextmanager
def wiring():
    with anchor.wiring():
        with ExitStack() as stack:
            for name, value in (('prepare',prepared), ('sources',sources), ('adapter',contract.adapter), ('interpret',interpret)):
                stack.enter_context(patch.object(base,name,value))
            stack.enter_context(patch.object(anchor.offline.previous,'contract',contract))
            for module in (anchor.offline.previous.executor,anchor.offline.previous.replay.r4):
                for name,value in (('prompt',contract.prompt),('preview',contract.preview),('QualifiedEngine',contract.DomainEngine)):
                    stack.enter_context(patch.object(module,name,value))
            yield


def prepare(price, bundles=base.PUBLIC):
    with wiring():
        return base.prepare(price,bundles)


def run(plan, output, *, fixed_responses, bundles=base.PUBLIC, hook=None):
    if plan.get('version') != VERSION or plan.get('real_transport_allowed') is not False or fixed_responses is None:
        raise PermissionError('constraint_revision_is_fixed_offline_only')
    with wiring():
        return base.run(plan,output,bundles=bundles,fixed_responses=fixed_responses,hook=hook)


def evaluate(directory, bundles=base.PUBLIC):
    plan = base.read(Path(directory)/'plan.json')
    if plan.get('version') != VERSION:
        return anchor.evaluate(directory,bundles)
    if base.read(Path(directory)/'origin.json')['mode'] != 'fixed':
        raise PermissionError('constraint_offline_cannot_claim_real_provenance')
    with wiring():
        result = anchor.offline.previous.BASE_EVALUATE(directory,bundles)
    result.update(constraint_representation=plan['constraint_representation'], real_execution_allowed=False)
    return result


def start_live(*args, **kwargs):
    raise PermissionError('constraint revision has no live entry, key reader or fee approval')


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    make=sub.add_parser('prepare');make.add_argument('--price',type=Path,required=True);make.add_argument('--output',type=Path,required=True)
    fixed=sub.add_parser('offline');fixed.add_argument('--candidate',type=Path,required=True);fixed.add_argument('--responses',type=Path,required=True);fixed.add_argument('--output',type=Path,required=True)
    verify=sub.add_parser('verify');verify.add_argument('--run',type=Path,required=True);verify.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='prepare':
        plan=prepare(base.read(args.price));base.write(args.output,plan);print(canonical_hash(plan))
    elif args.command=='offline':
        print([r['status'] for r in run(base.read(args.candidate),args.output,fixed_responses=base.read(args.responses))['rows']])
    else:
        report=evaluate(args.run);base.write(args.output,report);print(report['replay']['status'])


if __name__=='__main__':
    main()
