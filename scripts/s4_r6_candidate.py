"""Build and verify an offline R6 proposal. No credential or transport interface."""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import shutil

from scripts.s4_r5_acceptance import ROOT, write, offline_guard
from scripts.s4_repeat_schedule import build as schedule
from scripts.s4_p2 import checked_bundle, PUBLIC_FILES
from scripts.s4_prompt_candidate import STAGES, prompt, adapter, preview
from scripts.s4_synthetic_cost_wire import mock_wire
from scripts.s4_r4_evaluator import load_reference_package
from writscope.artifacts import canonical_hash, digest

SOURCE = ROOT/'reports/local/s4-r5-final-20261102'
PRIVATE_BINDING = ROOT/'evaluation_private/s4_review/r4-evaluator-binding-v1.json'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def ceiling(attempts, input_limit=32768, output_limit=4096):
    # Conditional on a future validated input-token bound; bytes are not tokens.
    return str(Decimal(attempts)*(Decimal(input_limit)*2+Decimal(output_limit)*8)/1_000_000)


def require_disabled(plan):
    if plan['status'] != 'draft_unapproved' or plan['real_transport_allowed'] or plan['actual_user_approval'] is not None:
        raise ValueError('candidate must remain unapproved and disabled')
    if not plan['execution_blockers']:
        raise ValueError('candidate cannot erase unresolved execution blockers')


def build(output):
    output = Path(output).resolve()
    if output.exists(): raise ValueError('new candidate directory required')
    acceptance = load(ROOT/'evaluation_private/s4_review/r5-offline-acceptance-20261102.json')
    for p,h in acceptance['sources_sha256'].items():
        if digest((ROOT/p).read_bytes()) != h: raise ValueError('R5 source changed: '+p)
    load_reference_package(PRIVATE_BINDING)
    output.mkdir(parents=True)
    cases = [f's4_{n:03d}' for n in range(1,31)]
    private = load(PRIVATE_BINDING)
    for case in cases:
        shutil.copytree(SOURCE/'public'/case, output/'public'/case)
        assert checked_bundle(output/'public'/case).sha256 == checked_bundle(SOURCE/'public'/case).sha256
    sched = schedule(output/'public', cases, 3)['plan']
    # Explicitly derive by method instead of treating the previous 1080 as evidence.
    bounds = {'full_direct':180, 'full_checklist':180,
              'incremental':3*sum(v['event_count']+1+min(3,v['event_count']+1) for v in sched['bundle_info'].values())}
    assert sum(bounds.values()) == sched['max_attempts']
    initial = []
    for case in cases:
        original = load(output/'public'/case/'input.json')['events']
        for method in ('full_direct','full_checklist'):
            value = preview(output/'public'/case, method)
            assert value['public_input']['review_input']['events'] == original
            wire = mock_wire(value)
            path = output/'request-previews'/f'{case}-{method}.json'
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(wire)
            initial.append({'case_id':case,'method':method,'path':path.relative_to(output).as_posix(),
                            'bytes':len(wire),'sha256':digest(wire),'events':len(original),'truncated':False})
    method = {'prompts':{s:prompt(s) for s in STAGES},
              'schemas':{s:{str(a).lower():adapter(s,a).json_schema() for a in (True,False)} for s in STAGES}}
    write(output/'method-contracts.json',method)
    price = {'checked_at_utc':'2026-11-02T03:22:18Z', 'currency':'CNY','conversion':'none; official CNY pricing',
        'source':'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
        'model_source':'https://api-docs.deepseek.com/',
        'retrieval':'public official documentation via web tool; no project materials submitted',
        'peak_per_million':{'cache_hit':'0.04','cache_miss':'2','output':'8'},
        'off_peak_per_million':{'cache_hit':'0.02','cache_miss':'1','output':'4'},
        'model_alias':'deepseek-flash','documented_version':'DeepSeek-V4.1-Flash-1017',
        'documented_context':'1M','documented_max_output':'384K',
        'run_price_validity':'not_valid_for_execution; recheck within 24h before a new frozen execution plan'}
    write(output/'price-review.json',price)
    plan = {'version':'s4_r6_freeze_candidate_v1','status':'draft_unapproved','scope':'30-case project-development comparison only',
        'actual_user_approval':None,'real_transport_allowed':False,'actual_model_calls':0,'credentials_read':0,
        'material_transmission':False,'fees_incurred':0,'formal_dataset_freeze':False,
        'schedule':sched,'attempt_bounds_by_method':bounds,'optional_pilot':'not_included_separate_plan_and_approval',
        'order':'repeat ascending, case ascending, full_direct/full_checklist/incremental; no shuffle',
        'random_seed':None,'seed_reason':'deterministic schedule; no provider seed or reproducibility claim',
        'model':{'provider':'DeepSeek official API','endpoint':'https://api.deepseek.com/chat/completions',
            'alias':'deepseek-flash','expected_documented_version':'DeepSeek-V4.1-Flash-1017',
            'immutable_revision_available':False,'returned_model_and_fingerprint':'record exact values or unknown; never infer a pin',
            'thinking':'disabled','temperature':0.0,'max_tokens':4096,'stream':False,
            'response_format':'json_object','tool_choice':'none','server_search_fetch_allowed':False,
            'top_p':'omitted_provider_default; record omission','seed':'not_sent'},
        'limits':{'input_token_limit_proposed':32768,'total_context_target':36864,'max_request_bytes':500000,
            'max_response_bytes':100000,'request_deadline_seconds':90,'concurrency':1,'retries':0,
            'baseline_max_lookup_continuations':1,'incremental_max_lookup_continuations':3,
            'max_events_per_lookup':2,'max_continuations_per_stage':1,'truncate_or_compress':False},
        'failure_policy':{'ordinary':'stop trajectory; retain denominator',
            'round_stop':'budget, provenance, source/hash/isolation/ledger/persistence fault; interrupt; HTTP 401/403; model version drift',
            'service_failure_threshold':3,'service_failures':'consecutive timeout/connection/HTTP429/HTTP5xx attempts across jobs; reset only after HTTP200 received',
            'invalid_output':'schema/scope/source citation/state/length finish/size failure; stop job; no repair/retry',
            'input_size':'static evidence admission failure stops before round; dynamic state overflow fails job; never truncate',
            'reservation':'reserve before send; do not release after timeout/interruption; unknown usage cannot lower reservation',
            'restart':'no auto resume or reuse of a consumed approval; new plan required'},
        'budget':{'proposed_local_cap_cny':'120.00','approval_status':'not_requested_not_granted',
            'conditional_per_attempt_cny':ceiling(1),'conditional_all_attempts_cny':ceiling(sched['max_attempts']),
            'assumptions':'validated <=32768 billable input tokens and <=4096 output per attempt; all input cache miss at official peak rate',
            'verified_input_token_bound':False,'provider_bill_hard_cap':False,'unknown_is_zero':False,
            'billing_currency_required':'CNY; if account billed in another currency, reprice and issue a new plan',
            'price_snapshot_sha256':digest((output/'price-review.json').read_bytes())},
        'materials':{'static_public_files':sorted(PUBLIC_FILES),'static_wire_previews':initial,
            'dynamic_allowed':['own accepted state and value scopes','visible full history prefix; full history at final',
                'model lookup selection and exact original visible events (max two)','same-stage prior response for continuation'],
            'prompts_and_schemas':'method-contracts.json',
            'never_send':['reference labels/states','pair maps','dispositions','owner review','hidden tests','private repairs',
                          'expected answers','mock scripts/responses','evaluation reports','credentials in bodies or logs'],
            'future_wire_bytes':'unknown for generated state and lookup continuations; capture actual bytes before dispatch, never prefill response'},
        'evaluator':{'binding_sha256':digest(PRIVATE_BINDING.read_bytes()),'private_files':private,
            'reference_status':'provisional_author_draft_not_frozen','user_case_review':'not_received',
            'independent_review':'not_performed_skipped_by_user','strict_single_variable_claim':False,
            'per_method_denominators':{'planned':90,'impact':90,'authorization_main':78,'decision':84,'ambiguity':6,'paired_repeat':36},
            'conflict':'main failed/null; validated original terminal diagnostic only','comparison':'equal_configuration',
            'budget_matched':'deferred_separate_plan','analysis':'descriptive only; raw numerators/denominators and failure/unknown costs; no superiority threshold',
            'repeat_unit':'12 paired development scenarios; repeats not independent samples'},
        'execution_blockers':['final owner disposition of remaining semantic limitations, without fabricated case review',
            'authenticated production approval source and real response provenance adapter absent; R4 rejects real records',
            'validated tokenizer/context/accounting bounds and real transport limit/failure threshold enforcement not implemented',
            'immutable provider revision unavailable; verify alias/version policy and fresh prices before execution',
            'new joint offline tests for any real adapter and updated source bindings',
            'exact new executable-plan hash, CNY budget and listed external-material approval not granted'],
        'r5_receipt_sha256':digest((ROOT/'evaluation_private/s4_review/r5-offline-acceptance-20261102.json').read_bytes()),
        'implementation_sources_sha256':acceptance['sources_sha256'],
        'proposal_builder_sha256':digest(Path(__file__).read_bytes())}
    plan['candidate_files_sha256']={p.relative_to(output).as_posix():digest(p.read_bytes()) for p in sorted(output.rglob('*')) if p.is_file()}
    require_disabled(plan)
    wrapped={'plan':plan,'plan_sha256':canonical_hash(plan)}
    write(output/'candidate.json',wrapped)
    return verify(output)


def verify(output):
    output=Path(output); wrapped=load(output/'candidate.json'); plan=wrapped['plan']
    require_disabled(plan)
    if canonical_hash(plan)!=wrapped['plan_sha256']: raise ValueError('candidate hash mismatch')
    for p,h in plan['candidate_files_sha256'].items():
        if digest((output/p).read_bytes())!=h: raise ValueError('candidate file changed: '+p)
    for p,h in plan['implementation_sources_sha256'].items():
        if digest((ROOT/p).read_bytes())!=h:raise ValueError('source changed: '+p)
    for key,p in plan['evaluator']['private_files']['paths'].items():
        if digest((ROOT/p).read_bytes())!=plan['evaluator']['private_files']['sha256'][key]:raise ValueError('private binding changed')
    regenerated=schedule(output/'public',plan['schedule']['cases'],3)['plan']
    if regenerated!=plan['schedule']:raise ValueError('schedule changed')
    if ceiling(regenerated['max_attempts'])!=plan['budget']['conditional_all_attempts_cny']:raise ValueError('budget arithmetic mismatch')
    return {'status':'candidate_verified_not_executable','plan_sha256':wrapped['plan_sha256'],
        'jobs':len(regenerated['jobs']),'max_attempts':regenerated['max_attempts'],
        'static_full_history_requests':len(plan['materials']['static_wire_previews']),
        'max_static_wire_bytes':max(r['bytes'] for r in plan['materials']['static_wire_previews']),
        'conditional_ceiling_cny':plan['budget']['conditional_all_attempts_cny'],'actual_model_calls':0}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('build','verify'));p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    with offline_guard(): print(json.dumps(build(a.output) if a.command=='build' else verify(a.output)))
