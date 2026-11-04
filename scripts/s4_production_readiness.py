"""Read-only production dependency audit; declarations never authorize execution.

Keeps the v10 runtime unchanged. Reads two public-metadata JSON declarations,
checks their completeness, and reports evidence and code gaps. It neither
installs a host root nor authenticates provider claims, reads keys, or sends.
"""
import argparse
import ast
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
import io
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from scripts.s4_r5_acceptance import offline_guard
from scripts.s4_r7_billing_boundary_v3 import checked_price,reservation
from writscope.artifacts import canonical_hash,digest,safe_path,write_json

ROOT=Path(__file__).resolve().parents[1]
SUITE=ROOT/'reports/s4-production-readiness-v1'
HOST_FIELDS={'service_id':None,'tenant':None,'conversation':None,'actor_id':None,
    'raw_event_api_version':None,'edit_revocation_contract':None,'counter_scope':None,
    'trust_anchor_fingerprint':None,'server_image_sha256':None,'local_pipe_contract':None,'evidence_refs':[]}
PROVIDER_FIELDS={'model_alias':'deepseek-flash','documented_version':None,'billing_currency':'CNY',
    'billable_input_max':None,'total_output_max':4096,'server_enforcement_contract':None,
    'includes_template_special_hidden_tokens':None,'non_thinking_billing_contract':None,
    'tools_disabled_billing_contract':None,'model_drift_contract':None,'evidence_refs':[]}


def template(kind):
    if kind not in ('host','provider'):raise ValueError('unsupported declaration kind')
    return {'version':'s4_public_dependency_declaration_v1','kind':kind,
            'not_an_execution_grant':True,'declaration':deepcopy(HOST_FIELDS if kind=='host' else PROVIDER_FIELDS)}


def strict_json(raw):
    if type(raw) is not bytes or len(raw)>65536:raise ValueError('declaration byte limit')
    def pairs(items):
        out={}
        for k,v in items:
            if k in out:raise ValueError('duplicate declaration field')
            out[k]=v
        return out
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite')))


def declaration_check(value,kind):
    expected=template(kind);missing=[];errors=[]
    if (type(value) is not dict or set(value)!=set(expected) or value.get('version')!=expected['version'] or
            value.get('kind')!=kind or value.get('not_an_execution_grant') is not True):
        return {'kind':kind,'status':'malformed','missing_fields':[],
                'errors':['envelope_fields_or_purpose'],'authenticated':False,'authorizes_execution':False}
    fields=value['declaration'];schema=HOST_FIELDS if kind=='host' else PROVIDER_FIELDS
    if type(fields) is not dict or set(fields)!=set(schema):
        return {'kind':kind,'status':'malformed','missing_fields':[],
                'errors':['declaration_field_whitelist'],'authenticated':False,'authorizes_execution':False}
    for name,current in fields.items():
        if current is None or current==[] or current=='':missing.append(name);continue
        if name=='evidence_refs':
            if type(current) is not list or len(current)>16:
                errors.append('evidence_refs_shape');continue
            for reference in current:
                if type(reference) is not dict or set(reference)!={'url','sha256'}:
                    errors.append('evidence_reference_fields');continue
                try:
                    u=urlsplit(reference['url'])
                    valid=(type(reference['url']) is str and len(reference['url'])<=2048 and u.scheme=='https' and
                           u.hostname and not u.username and not u.password and u.port in (None,443) and not u.fragment)
                except (ValueError,TypeError):valid=False
                if not valid:errors.append('evidence_reference_url')
                if type(reference['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}',reference['sha256']):
                    errors.append('evidence_reference_hash')
        elif kind=='provider' and name in ('billable_input_max','total_output_max'):
            if type(current) is not int or current!={'billable_input_max':32768,'total_output_max':4096}[name]:
                errors.append(name+'_incompatible_with_accepted_design')
        elif kind=='provider' and name=='includes_template_special_hidden_tokens':
            if current is not True:errors.append('input_bound_does_not_cover_all_billable_tokens')
        else:
            if type(current) is not str or not 1<=len(current)<=4096:
                errors.append(name+'_type_or_length');continue
            if name in ('trust_anchor_fingerprint','server_image_sha256') and not re.fullmatch('[0-9a-f]{64}',current):
                errors.append(name+'_hash')
            if name=='model_alias' and current!='deepseek-flash':errors.append('model_alias_changed')
            if name=='documented_version' and current!='DeepSeek-V4.1-Flash-1017':errors.append('documented_version_changed_refreeze_required')
            if name=='billing_currency' and current!='CNY':errors.append('currency_changed')
    return {'kind':kind,'status':'malformed' if errors else 'missing' if missing else 'complete_unverified',
            'missing_fields':sorted(missing),'errors':sorted(set(errors)),
            'authenticated':False,'authorizes_execution':False,
            'note':'shape and explicit claims only; URL/hash/public key do not authenticate an issuer'}


def closed_entry(path,name):
    node=next((n for n in ast.parse(Path(path).read_text(encoding='utf-8')).body
               if isinstance(n,ast.FunctionDef) and n.name==name),None)
    if node is None:return False
    body=node.body
    if body and isinstance(body[0],ast.Expr) and isinstance(body[0].value,ast.Constant) and isinstance(body[0].value.value,str):
        body=body[1:]
    return len(body)==1 and isinstance(body[0],ast.Raise) and isinstance(body[0].exc,ast.Call) and isinstance(body[0].exc.func,ast.Name) and body[0].exc.func.id=='PermissionError'


def assess(host,provider,price,now):
    host_result=declaration_check(host,'host');provider_result=declaration_check(provider,'provider')
    try:
        checked_price(price,now);price_status='fresh_public_quote_not_account_or_execution_proof'
    except (PermissionError,TypeError,AttributeError):price_status='invalid_or_expired_refreeze_required'
    blockers=[
        {'code':'HOST_SOURCE_NOT_PROVISIONED','owner':'host integration','requires':'authenticated raw user events, edits/revocation, independent trust root and deployed service'},
        {'code':'PROVIDER_BILLABLE_BOUND_UNVERIFIED','owner':'provider evidence','requires':'target model/template/server enforcement for <=32768 billable input including overhead'},
        {'code':'PRODUCTION_TRANSPORT_AND_R4_DISABLED','owner':'project engineering after dependencies','requires':'authenticated production receipts and network backend; separately authorized validation'},
        {'code':'FINAL_PRODUCTION_ACCEPTANCE_MISSING','owner':'project engineering after dependencies','requires':'joint acceptance of exact final production sources/config; v7/v10 evidence stays separate'},
        {'code':'EXECUTABLE_FREEZE_AND_APPROVAL_MISSING','owner':'project owner after readiness','requires':'exact new executable hash, fresh price, explicit budget and listed material authorization'}]
    if price_status.startswith('invalid'):blockers.append({'code':'PRICE_RECHECK_REQUIRED','owner':'project engineering','requires':'fresh official CNY pricing'})
    return {'version':'s4_production_readiness_v1','status':'not_executable_external_dependencies_pending',
            'declarations':{'host':host_result,'provider':provider_result},'public_price_status':price_status,
            'conditional_budget':reservation(32768),'budget_admission_verified':False,
            'billable_input_bound_verified':False,'account_currency_verified':False,
            'blockers':blockers,'real_transport_allowed':False,'actual_execution_approval':None,
            'actual_model_calls':0,'actual_credentials_read':0,'project_materials_transmitted':False,'fees_incurred':0,
            'reference_status':'provisional_author_draft_not_frozen','independent_review':'not_performed_skipped_by_user',
            'primary_comparison':'equal_configuration','budget_matched':'deferred_separate_plan',
            'declaration_completeness_is_not_trusted_provisioning':True}


def load_public_declarations(directory):
    directory=Path(directory).absolute()
    if not directory.is_relative_to(ROOT):raise ValueError('public intake must stay in workspace')
    # Walk every path component without resolving away junction/symlink evidence.
    relative=directory.relative_to(ROOT).as_posix()
    values={}
    for kind in ('host','provider'):
        path=safe_path(ROOT,relative+'/'+kind+'-declaration.json')
        try:values[kind]=strict_json(path.read_bytes())
        except FileNotFoundError:values[kind]=template(kind)
        except (ValueError,UnicodeError):values[kind]={}
    return values


def audit(intake=None):
    # Integrity checks only. No method/worker/host callback is invoked.
    from scripts.s4_r7_release_review import verify as v8
    from scripts.s4_r7_bound_verification import verify as v9
    from scripts.s4_r7_host_ipc_verification import verify as v10
    capture=io.StringIO()
    with offline_guard(),redirect_stdout(capture):
        v8();nine=v9();ten=v10()
    values=load_public_declarations(intake or SUITE/'intake')
    report=assess(values['host'],values['provider'],strict_json((SUITE/'public-price-review.json').read_bytes()),datetime.now(timezone.utc))
    entries=[('scripts/s4_r7_http_worker.py','start_live'),('scripts/s4_r7_worker_runtime.py','start_live'),
             ('scripts/s4_r7_bound_runtime.py','start_live'),('scripts/s4_r7_host_ipc_runtime.py','start_live'),
             ('scripts/s4_r7_bound_approval_v3.py','installed_production_source'),('scripts/s4_r7_host_ipc.py','installed_production_channel')]
    report['closed_entries']={p+'::'+name:closed_entry(ROOT/p,name) for p,name in entries}
    if not all(report['closed_entries'].values()):raise ValueError('a known closed entry changed; stop and review sources')
    report['engineering_integrity']={'v8':'verified_240_public_files_270_jobs_657_prior_attempts','v9':nine,'v10':ten}
    report['engineering_candidate_sha256']=ten['candidate_sha256']
    report['current_version_acceptance']={'tests_passed':15,'normal_jobs':3,'fixed_mock_attempts':11,'host_IPC_receipts':22,'new_full_270_run':False}
    report['input_declaration_hashes']={k:canonical_hash(v) for k,v in values.items()}
    support=SUITE/'external-support-status.json'
    if support.exists():
        metadata=strict_json(support.read_bytes())
        if (metadata.get('version')!='s4_external_support_status_v1' or metadata.get('not_execution_evidence') is not True or
                any(type(metadata.get(k)) is not bool for k in ('host_access_available','provider_bound_evidence_available'))):
            raise ValueError('external support status metadata invalid')
        report['external_support_availability']=metadata
        report['external_support_status_sha256']=digest(support.read_bytes())
    report['code_sha256']=digest(Path(__file__).read_bytes())
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--intake',type=Path);p.add_argument('--output',type=Path)
    args=p.parse_args();report=audit(args.intake)
    if args.output:
        if args.output.exists():raise ValueError('new output required; preserve previous audit')
        if not args.output.resolve().is_relative_to(ROOT):raise ValueError('audit output must stay in workspace')
        write_json(args.output,report)
    print(json.dumps({'status':report['status'],'blockers':[v['code'] for v in report['blockers']],
                      'real_transport_allowed':False,'actual_execution_approval':None},ensure_ascii=False))


if __name__=='__main__':main()
