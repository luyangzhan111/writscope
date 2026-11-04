"""Only offline compact-contract entry; delegates methods, ledger and R4.

No credential reader, approval import or live launch is exposed here.
"""
import argparse
from copy import deepcopy
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from scripts import s4_compiled_contract_v2 as contract
from scripts import s4_owner_pilot_v5 as anchor
from scripts import s4_owner_pilot_v2 as parser
from scripts import s4_owner_pilot as base
from writscope.artifacts import canonical_hash, digest

ROOT = base.ROOT
VERSION = 's4_contract_convergence_offline_v2'
OLD_SHA = 'cb8e107401102f00cba2b67623d9286b492ea062a631b0db76535b6d2c8665bd'
EXTRA_FILES = ('scripts/s4_compiled_contract_v2.py', 'scripts/s4_contract_convergence_offline.py',
    'scripts/s4_contract_convergence_acceptance.py', 'tests/test_s4_contract_convergence.py',
    'specs/s4-compiled-contract-v2.md', *('prompts/s4_compiled_v2/'+s+'.md' for s in ('common', *contract.STAGES)))


def sources():
    prior = base.read(ROOT/'reports/s4-owner-pilot-v5/execution-candidate.json')
    if canonical_hash(prior) != OLD_SHA or anchor.sources() != prior['sources_sha256']:
        raise base.SharedIntegrityError('frozen_v5_anchor_or_source_changed')
    return {**prior['sources_sha256'], **parser.file_hashes(EXTRA_FILES)}


def prepared(price, bundles=base.PUBLIC):
    # One current descriptor, not every obsolete prompt/schema generation.
    # BASE_PREPARE still verifies the shared scheduling, budget and environment;
    # sources() binds all 253 historical dependencies without caching hashes.
    value = parser.BASE_PREPARE(price,bundles)
    prior = base.read(ROOT/'reports/s4-owner-pilot-v5/execution-candidate.json')
    value.update(conditional_reservation=deepcopy(prior['conditional_reservation']),
        materials=deepcopy(prior['materials']),never_send=deepcopy(prior['never_send']))
    value.update(version=VERSION, status='offline_engineering_candidate_not_paid_freeze',
        execution_kind='fixed_compact_contract_diagnostic', real_transport_allowed=False,
        real_sending_currently_approved=False, actual_execution_approval=None,
        approval_scope='offline_only_no_authorized_real_starts',
        execution_requires='fixed_offline_only_no_credentials_or_approval_interface',
        compiled_contract_revision={'version':contract.VERSION, 'prior_v5_sha256':OLD_SHA,
            'prompt_sha256':{s:digest(contract.prompt(s).encode()) for s in contract.STAGES},
            'schema_sha256':{s:{str(a).lower():canonical_hash(contract.adapter(s,a).json_schema())
                for a in (True,False)} for s in contract.STAGES},
            'compiler':'explicit_source_once_and_common_deterministic_coverage_not_response_repair',
            'historical_price':'fixed_control_only_not_current_quote_or_budget_approval',
            'model_improvement':'not_tested', 'formal_270_trajectory_comparison':False})
    return value


def interpret(raw, receipt, request, plan):
    payload, finish, error, usage = parser.interpret(raw,receipt,request,plan)
    if error is None:
        try:
            compiled, proof = contract.projection(payload,request)
            usage['compact_contract_projection'] = proof
        except ValueError as exc:
            error = 'compact_contract_source_rejected'
            usage.update(compact_contract_source_error=str(exc),stop_round=True)
    if error is None:
        try:
            contract.old.check_maintained_summary(compiled,request)
        except ValueError as exc:
            error = 'compact_contract_summary_mismatch'
            usage['compact_contract_summary_error'] = str(exc)
    # The executor receives wire batches (engine compiles them), compiled finals
    # (legacy final verifier), or unmodified lookup intents. R4 recomputes both.
    if error is None and payload['kind']=='final':
        payload = compiled
    return payload, finish, error, usage


@contextmanager
def wiring():
    if not parser.LOCK.acquire(blocking=False):
        raise RuntimeError('single_concurrency_version_wiring_active')
    try:
        with ExitStack() as stack:
            for name,value in (('prepare',prepared),('sources',sources),('adapter',contract.adapter),('interpret',interpret)):
                stack.enter_context(patch.object(base,name,value))
            stack.enter_context(patch.object(parser,'contract',contract))
            for module in (parser.executor,parser.replay.r4):
                for name,value in (('prompt',contract.prompt),('preview',contract.preview),('QualifiedEngine',contract.DomainEngine)):
                    stack.enter_context(patch.object(module,name,value))
            yield
    finally:
        parser.LOCK.release()


def prepare(price,bundles=base.PUBLIC):
    with wiring():
        return base.prepare(price,bundles)


def run(plan,output,*,fixed_responses,bundles=base.PUBLIC,hook=None):
    if plan.get('version')!=VERSION or plan.get('real_transport_allowed') is not False or fixed_responses is None:
        raise PermissionError('compact_contract_is_fixed_offline_only')
    with wiring():
        return base.run(plan,output,bundles=bundles,fixed_responses=fixed_responses,hook=hook)


def evaluate(directory,bundles=base.PUBLIC):
    plan = base.read(Path(directory)/'plan.json')
    if plan.get('version')!=VERSION:
        return anchor.evaluate(directory,bundles)
    if base.read(Path(directory)/'origin.json')['mode']!='fixed':
        raise PermissionError('compact_offline_cannot_claim_live_provenance')
    with wiring():
        report = parser.BASE_EVALUATE(directory,bundles)
    report.update(compiled_contract_revision=plan['compiled_contract_revision'],real_execution_allowed=False)
    return report


def start_live(*args,**kwargs):
    raise PermissionError('compact_contract_has_no_live_entry_or_credential_reader')


def main():
    cli=argparse.ArgumentParser(description=__doc__); sub=cli.add_subparsers(dest='command',required=True)
    make=sub.add_parser('prepare'); make.add_argument('--price',type=Path,required=True); make.add_argument('--output',type=Path,required=True)
    fixed=sub.add_parser('offline'); fixed.add_argument('--candidate',type=Path,required=True); fixed.add_argument('--responses',type=Path,required=True); fixed.add_argument('--output',type=Path,required=True)
    verify=sub.add_parser('verify'); verify.add_argument('--run',type=Path,required=True); verify.add_argument('--output',type=Path,required=True)
    args=cli.parse_args()
    if args.command=='prepare':
        plan=prepare(base.read(args.price)); base.write(args.output,plan); print(canonical_hash(plan))
    elif args.command=='offline':
        print([r['status'] for r in run(base.read(args.candidate),args.output,fixed_responses=base.read(args.responses))['rows']])
    else:
        report=evaluate(args.run); base.write(args.output,report); print(report['replay']['status'])


if __name__=='__main__':
    main()
