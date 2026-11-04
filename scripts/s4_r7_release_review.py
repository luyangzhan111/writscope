"""Read-only audit and unified freeze-review builder. Never a model runner.

Only saved request/response bytes are counted; no evaluator references, expected
answers, fixtures or host signing credentials enter the counting channel.
"""
import argparse
from contextlib import closing
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import time

from scripts.s4_r5_acceptance import offline_guard
from scripts.s4_r7_token_boundary import LocalCounter
from scripts.s4_r7_billing_boundary_v3 import account_raw, reservation
from writscope.artifacts import canonical_hash, digest, write_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'reports/s4-r7-release-review-v1'
RUN = ROOT/'reports/s4-r7-full-v1/normal'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def audit():
    if (OUT/'saved-wire-audit.json').exists():
        raise ValueError('audit already exists; preserve evidence')
    started = time.perf_counter()
    audit_sources = {p: digest((ROOT/p).read_bytes()) for p in (
        'scripts/s4_r7_release_review.py','scripts/s4_r7_billing_boundary_v3.py','scripts/s4_r7_token_boundary.py')}
    before = digest((RUN/'journal.sqlite').read_bytes())
    counter = LocalCounter(ROOT/'reports/s4-r7-preparation-v1/official-tokenizer.zip',
                           ROOT/'.work/s4-r7-tokenizer-runtime')
    rows, grouped = [], {}
    with offline_guard(), closing(sqlite3.connect((RUN/'journal.sqlite').as_uri()+'?mode=ro', uri=True)) as db:
        attempts = db.execute('SELECT id,job,stage,is_continuation,request,request_hash,response,response_hash,status FROM attempt ORDER BY id').fetchall()
        for index, job, stage, continuation, wire, request_hash, raw, response_hash, status in attempts:
            if digest(wire) != request_hash or digest(raw) != response_hash or status != 'completed':
                raise ValueError('saved-byte binding/status changed')
            proof = counter.count(wire)
            cost = account_raw(raw)
            if cost['errors']:
                raise ValueError('saved normal response billing fault: '+str(index))
            group = stage+('/continuation' if continuation else '/initial')
            grouped.setdefault(group, []).append(proof['local_input_tokens'])
            rows.append({'attempt': index, 'job': job, 'stage': stage, 'continuation': bool(continuation),
                         'request_sha256': request_hash, 'response_sha256': response_hash,
                         'wire_bytes': len(wire), 'local_input_tokens': proof['local_input_tokens'],
                         'rendered_sha256': proof['rendered_sha256'], 'within_proposed_input_cap': proof['within_proposed_input_cap'],
                         'billing': cost})
    if digest((RUN/'journal.sqlite').read_bytes()) != before:
        raise ValueError('read-only journal changed')
    if any(digest((ROOT/p).read_bytes()) != sha for p, sha in audit_sources.items()):
        raise ValueError('audit source changed while running')
    if len(rows) != 657 or len(set(r['job'] for r in rows)) != 270:
        raise ValueError('full saved denominator mismatch')
    value = {'version': 's4_saved_wire_token_audit_v1', 'kind': 'fixed_mock_history_local_estimates',
             'attempts': len(rows), 'jobs': 270, 'journal_sha256': before, 'tokenizer_binding': counter.binding,
             'audit_sources_sha256': audit_sources, 'audit_elapsed_ms': (time.perf_counter()-started)*1000,
             'local_cost_scope': 'shared_offline_preparation_not_method_trace_cost',
             'grouped': {k: {'count': len(v), 'min': min(v), 'max': max(v)} for k, v in sorted(grouped.items())},
             'local_input_min': min(r['local_input_tokens'] for r in rows),
             'local_input_max': max(r['local_input_tokens'] for r in rows),
             'all_within_local_cap': all(r['within_proposed_input_cap'] for r in rows),
             'provider_token_bound_verified': False, 'server_template_match': 'not_verified',
             'target_version_match': 'not_verified', 'billable_overhead_tokens': None,
             'fixture_token_estimate_cny': str(sum(Decimal(r['billing']['token_cost_cny_micro']) for r in rows)/1000000),
             'actual_provider_bill': None, 'fixture_usage_is_calibration': False,
             'limitations': ['covers these saved dynamic states/continuations only, not future model states',
                            'fixture token counts are constructed, not provider measurements',
                            'missing server-tool counters remain unknown; outbound tools disabled'],
             'rows': rows, 'actual_model_calls': 0, 'actual_credentials_read': 0,
             'materials_transmitted': False, 'fees_incurred': 0}
    write_json(OUT/'saved-wire-audit.json', value)
    print(json.dumps({k: v for k, v in value.items() if k in ('attempts','jobs','grouped','local_input_min','local_input_max','fixture_token_estimate_cny')}))


def build():
    if (OUT/'freeze-candidate.json').exists():
        raise ValueError('freeze candidate already exists; create a new version')
    old = load(ROOT/'reports/s4-r7-full-final-v1/execution-candidate.json')
    if canonical_hash(old['plan']) != old['plan_sha256']:
        raise ValueError('prior candidate hash mismatch')
    preservation = load(OUT/'preservation-before.json')
    for p, sha in preservation['files'].items():
        if digest((ROOT/p).read_bytes()) != sha:
            raise ValueError('preserved file changed: '+p)
    base = load(ROOT/'reports/s4-r6-candidate-v1/candidate.json')['plan']
    audit_value = load(OUT/'saved-wire-audit.json')
    price = load(OUT/'official-evidence.json')
    run_plan = load(RUN/'plan.json')
    # The old full result independently enumerates 270 identities. The plan itself
    # remains a bound file; no assumptions about nested version paths are needed.
    result = load(RUN/'result.json')
    identities = [{k: row[k] for k in ('case_id','method','track','repeat_index')} for row in result['rows']]
    if len(identities) != 270 or len({canonical_hash(v) for v in identities}) != 270:
        raise ValueError('full identities mismatch')
    sources = {p: digest((ROOT/p).read_bytes()) for p in (
        'scripts/s4_r7_approval_source_v2.py', 'scripts/s4_r7_billing_boundary_v3.py',
        'scripts/s4_r7_release_journal.py',
        'scripts/s4_r7_release_review.py', 'tests/test_s4_r7_release_gate.py',
        'specs/s4-r7-release-review-v1.md')}
    plan = {'version': 's4_r6_unified_freeze_review_v8', 'status': 'owner_review_candidate_not_executable',
            'prior_candidate_sha256': old['plan_sha256'], 'existing_runtime_unchanged': True,
            'runtime_sources_sha256': old['plan']['sources_sha256'], 'review_sources_sha256': sources,
            'normal_execution_plan_sha256': digest((RUN/'plan.json').read_bytes()),
            'jobs': identities, 'case_count': 30, 'pair_count': 12, 'repetitions': 3,
            'attempt_bounds_by_method': base['attempt_bounds_by_method'], 'max_attempts': 1080,
            'model': base['model'], 'limits': base['limits'], 'failure_policy': base['failure_policy'],
            'ordering': base['order'], 'analysis': base['evaluator'],
            'materials': base['materials'], 'proposal_base_sha256': digest((ROOT/'reports/s4-r6-candidate-v1/candidate.json').read_bytes()),
            'public_files_sha256': {p.relative_to(ROOT).as_posix(): digest(p.read_bytes())
                                    for p in sorted((ROOT/'reports/s4-r6-candidate-v1/public').rglob('*')) if p.is_file()},
            'method_contract_sha256': digest((ROOT/'reports/s4-r6-candidate-v1/method-contracts.json').read_bytes()),
            'private_binding_sha256': digest((ROOT/'evaluation_private/s4_review/r4-evaluator-binding-v1.json').read_bytes()),
            'budget': {'proposed_cap_cny': '120.00', 'currency': 'CNY', 'approval': 'not_granted',
                       'retained_32768_input_case': reservation(32768),
                       'context_envelope_sensitivity_not_selected': reservation(1048576),
                       'bound_status': 'unverified_do_not_admit_production',
                       'provider_bill_hard_cap': False, 'release_reservations_on_unknown': False},
            'approval_source': {'adapter': 'signed_host_event_v2', 'installed_production_source': None,
                                'trust_anchor': None, 'query_contains': ['event_id','scope_sha256','challenge'],
                                'event_summary_or_artifact_fallback': False,
                                'deployment_tests': 'synthetic_in_memory_RSA_only',
                                'runtime_receipt_migration': 'same_SQLite_test_adapter_added_production_provisioning_and_receipts_still_pending'},
            'billing': {'policy': 'strict_usage_cache_reasoning_server_tool_checks_v3',
                        'saved_wire_audit_sha256': digest((OUT/'saved-wire-audit.json').read_bytes()),
                        'official_evidence_sha256': digest((OUT/'official-evidence.json').read_bytes()),
                        'price_checked_at': price['checked_at'], 'price_valid_until': price['valid_until'],
                        'target_tokenizer_bound_verified': False,
                        'runtime_policy_migration': 'same_SQLite_test_adapter_added_production_token_admission_and_receipts_still_pending'},
            'targeted_test_receipt_sha256': digest((OUT/'tests-final.xml').read_bytes()),
            'owner_decision_requested': 'freeze this review configuration and retain closed gates; no execution approval',
            'execution_blockers': [
                'host signer/event API and public trust anchor must be provisioned outside agent-writable artifacts',
                'provider model/template/billable input bound <=32768 remains unverified; observational calibration alone is not a proof',
                'new production approval/accounting receipts must be jointly integrated with same runtime journal and R4 before live',
                'actual network backend and DNS/TCP/TLS remain disabled and unvalidated',
                'recheck prices/model and bind final executable source/plan after all gates; obtain exact new budget and material authorization'],
            'real_transport_allowed': False, 'actual_execution_approval': None,
            'actual_model_calls': 0, 'actual_credentials_read': 0, 'materials_transmitted': False,
            'fees_incurred': 0, 'github_updated': False,
            'reference_status': 'provisional_author_draft_not_frozen',
            'independent_review': 'not_performed_skipped_by_user'}
    write_json(OUT/'freeze-candidate.json', {'plan': plan, 'plan_sha256': canonical_hash(plan)})
    write_json(OUT/'preservation-after.json', {'unchanged': True, 'checked_files': len(preservation['files']),
               'prior_candidate_sha256': old['plan_sha256'], 'new_candidate_sha256': canonical_hash(plan)})
    print('Review candidate SHA256: '+canonical_hash(plan))


def verify():
    wrapped = load(OUT/'freeze-candidate.json'); plan = wrapped['plan']
    if canonical_hash(plan) != wrapped['plan_sha256'] or plan['real_transport_allowed'] is not False or plan['actual_execution_approval'] is not None:
        raise ValueError('candidate hash/gate changed')
    for category in ('runtime_sources_sha256', 'review_sources_sha256', 'public_files_sha256'):
        for p, sha in plan[category].items():
            if digest((ROOT/p).read_bytes()) != sha:
                raise ValueError('candidate dependency changed: '+p)
    dependencies = {
        'normal_execution_plan_sha256': RUN/'plan.json',
        'method_contract_sha256': ROOT/'reports/s4-r6-candidate-v1/method-contracts.json',
        'private_binding_sha256': ROOT/'evaluation_private/s4_review/r4-evaluator-binding-v1.json',
        'proposal_base_sha256': ROOT/'reports/s4-r6-candidate-v1/candidate.json',
        'targeted_test_receipt_sha256': OUT/'tests-final.xml'}
    for field, p in dependencies.items():
        if digest(p.read_bytes()) != plan[field]:
            raise ValueError('bound file changed: '+str(p))
    for field, filename in [('saved_wire_audit_sha256','saved-wire-audit.json'), ('official_evidence_sha256','official-evidence.json')]:
        if digest((OUT/filename).read_bytes()) != plan['billing'][field]:
            raise ValueError('billing review changed')
    private = load(dependencies['private_binding_sha256'])
    for name, p in private['paths'].items():
        if digest((ROOT/p).read_bytes()) != private['sha256'][name]:
            raise ValueError('private reference binding changed')
    preserved = load(OUT/'preservation-before.json')['files']
    for p, sha in preserved.items():
        if digest((ROOT/p).read_bytes()) != sha:
            raise ValueError('historical file changed: '+p)
    audit_value = load(OUT/'saved-wire-audit.json')
    if digest((RUN/'journal.sqlite').read_bytes()) != audit_value['journal_sha256']:
        raise ValueError('audited journal changed')
    if len(plan['public_files_sha256']) != 240 or len(plan['jobs']) != 270 or audit_value['attempts'] != 657:
        raise ValueError('case/job/attempt denominator changed')
    print('Verified review candidate, 240 public files, 270 jobs, 657 saved attempts; live closed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['audit', 'build', 'verify'])
    args = parser.parse_args()
    with offline_guard():
        {'audit': audit, 'build': build, 'verify': verify}[args.action]()


if __name__ == '__main__':
    main()
