"""Contract/projection safety and joint controls; no model improvement claims."""
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
from unittest.mock import patch

import pytest

from scripts import s4_compiled_contract_v2 as c
from scripts import s4_contract_convergence_acceptance as h
from scripts import s4_contract_convergence_offline as runtime
from scripts import s4_owner_pilot as base
from scripts.s4_r5_acceptance import offline_guard
from writscope.artifacts import canonical_hash, digest


def request(stage='full_direct'):
    if stage=='incremental_extract':
        engine=c.DomainEngine(h.bundle(),'a'*64)
        return c.preview(base.PUBLIC/base.CASE,stage,event_index=0,prior_state=engine.engine.snapshot(),prior_value_scopes={})
    return c.preview(base.PUBLIC/base.CASE,stage)


def submit(engine,index,*items):
    return engine.submit(json.dumps({'items':list(items)},ensure_ascii=False),index,'synthetic_compiler_control')


@pytest.mark.parametrize('stage',['full_direct','full_checklist','incremental_final'])
def test_one_declaration_compiles_complete_identical_coverage_sources_and_meaning(stage):
    value=h.final(stage);before=deepcopy(value)
    req=request();req['stage']=stage
    compiled,proof=c.projection(value,req)
    assert value==before and 'api_constraints' not in value
    assert [i['change_types'] for i in compiled['payload']['derived_items']]==[
        ['add_alias','remove_field','replace_field','change_value_semantics'],['change_value_semantics']]
    refs=compiled['payload']['derived_items'][1]['source_refs']
    assert len(refs)==2 and refs[0]==value['payload']['derived_items'][1]['source']
    assert proof['compiled_payload_sha256']==canonical_hash(compiled)
    assert proof['logical_input_sha256']==canonical_hash(value)
    assert not proof['scope_inferred_from_history'] and not proof['general_semantic_proof']
    p=compiled['api_constraints'][1]['predicate']
    assert p['qualifiers']['scope_source']==refs[0] and p['qualifiers']['meaning_source']==refs[1]
    assert all('translation' not in p for p in proof['source_proofs']['meaning_grounding'])


@pytest.mark.parametrize('kind,expected',[
    ('presence',['remove_field','replace_field']),('open_shape',['remove_field','replace_field','change_value_semantics']),
    ('closed_shape',['add_alias','remove_field','replace_field','change_value_semantics']),('meaning',['change_value_semantics'])])
def test_coverage_depends_on_predicate_not_candidate(kind,expected):
    p={'presence':{'kind':'field_exists','expected':True,'qualifiers':{}},'open_shape':h.shape(False),
        'closed_shape':h.shape(),'meaning':h.meaning()}[kind]
    assert c.old.coverage(c.expanded_predicate(c.TypeAdapter(c.Predicate).validate_python(p),
        c.SourceQuote(event_id='e001',quote=h.public_parts()[1])))==expected


@pytest.mark.parametrize('field',['change_types','source_refs','conditions','effect_domain','api_constraints'])
def test_redundant_model_fields_are_forbidden_not_overridden(field):
    value=h.final('full_direct')
    if field=='api_constraints':value[field]=[]
    else:value['payload']['derived_items'][0][field]=[] if field!='effect_domain' else 'server_api'
    original=deepcopy(value)
    with pytest.raises(ValueError):c.adapter('full_direct',False).validate_python(value)
    assert value==original


@pytest.mark.parametrize('change',['partial','client','wrong_version','tool','future','duplicate_quote','meaning_event','meaning_outside','translation','overlap'])
def test_scope_cannot_be_borrowed_inferred_or_spoofed(change):
    value=h.final('full_direct');req=request();item=value['payload']['derived_items'][1]
    if change=='partial':item['source']['quote']=item['predicate']['qualifiers']['meaning_quote']
    elif change=='client':item['source']['quote']+=h.public_parts()[2]
    elif change=='wrong_version':item['subjects'][0]['path']='/api/v2/profile';item['subjects'][0]['api_version']='v2'
    elif change=='tool':item['source']['event_id']='e002'
    elif change=='future':item['source']['event_id']='future'
    elif change=='duplicate_quote':req['public_input']['review_input']['events'][0]['content']+=' '+item['source']['quote']
    elif change=='meaning_event':item['predicate']['qualifiers']['event_id']='e004'
    elif change=='meaning_outside':item['predicate']['qualifiers']['meaning_quote']='另一个接口表示账户启用'
    elif change=='translation':item['predicate']['expected']['source_text']='account enabled'
    else:
        item['predicate']['expected']['source_text']='aba'
        item['predicate']['qualifiers']['meaning_quote']='meaning ababa'
    with pytest.raises(ValueError):c.projection(value,req)


def test_matching_operation_errors_do_not_include_unrelated_no_change_branch():
    value=h.mutation(h.meaning(),'c_meaning','值为字符串 active，表示账户启用。')
    with pytest.raises(ValueError) as raised:
        c.Batch.model_validate({'kind':'batch','items':[h.mutation(),value,h.context(h.public_parts()[2])]})
    errors=raised.value.errors(include_input=False)
    assert any('API_constraint_source_scope_unresolved' in e['msg'] for e in errors)
    assert all('no_change' not in str(e['loc']) and 'max_length' not in e['type'] for e in errors)


def test_literal_quote_in_tool_event_is_still_not_user_authority():
    value=h.final('full_direct');req=request()
    req['public_input']['review_input']['events'][0]['role']='tool'
    req['public_input']['review_input']['events'][0]['provenance']['authority_level']='none'
    with pytest.raises(ValueError,match='only a user'):c.projection(value,req)


def test_derived_refs_are_deduplicated_when_scope_equals_meaning():
    value=h.final('full_direct');item=value['payload']['derived_items'][1]
    item['predicate']['qualifiers']['meaning_quote']=item['source']['quote']
    compiled,_=c.projection(value,request())
    assert compiled['payload']['derived_items'][1]['source_refs']==[item['source']]


@pytest.mark.parametrize('target',['constraint','authorization'])
def test_supersede_dispatch_chooses_one_target_type(target):
    item=h.mutation();item.update(operation='supersede',target_ids=['old'])
    if target=='constraint':item['constraint']['supersedes']=['old']
    else:
        item.update(constraint=None,authorization={'invalid':'intentionally lacks required auth fields'})
    tag=c.operation_tag(item)
    assert tag=='supersede_'+target
    if target=='constraint':assert isinstance(c.Batch.model_validate({'kind':'batch','items':[item]}).items[0],c.ReplaceConstraint)
    else:
        with pytest.raises(ValueError) as raised:c.Batch.model_validate({'kind':'batch','items':[item]})
        assert all('supersede_constraint' not in str(e['loc']) for e in raised.value.errors())


@pytest.mark.parametrize('change',['source_quote','event_id','seq','mixed','two'])
def test_no_change_strict_shape_and_sole_operation(change):
    item=h.no_change();items=[item]
    if change=='mixed':items.append(h.mutation())
    elif change=='two':items.append(h.no_change())
    else:item[change]=2 if change=='seq' else 'stale_event'
    with pytest.raises(ValueError):c.Batch.model_validate({'kind':'batch','items':items})


def test_five_current_events_have_distinct_verified_bindings_without_authority():
    engine=c.DomainEngine(h.bundle(),'b'*64)
    rows=[submit(engine,i,h.no_change()) for i in range(5)]
    for event,row in zip(h.bundle().review_input.events,rows,strict=True):
        proof=row['compiler_projection']['no_change_event_binding']
        assert (proof['event_id'],proof['seq'],proof['role'])==(event.event_id,event.seq,event.role)
        assert proof['event_content_sha256']==digest(event.content.encode())
        assert not proof['creates_authority'] and proof['semantic_no_change_judgment']=='not_verified'
        assert row['compiler_projection']['compact_contract']['policy']==c.VERSION
    state=engine.snapshot()
    assert state['state']['through_seq']==5
    assert state['state']['constraints']==state['state']['authorizations']==state['state']['exceptions']==[]
    assert state['task_context']['task_context']==[] and state['value_scopes']=={}


def test_invalid_current_source_prevents_any_state_or_context_commit():
    engine=c.DomainEngine(h.bundle(),'c'*64);before=engine.snapshot()
    bad=h.mutation(h.meaning(),'c_meaning','值为字符串 active，表示账户启用。')
    with pytest.raises(ValueError):submit(engine,0,h.mutation(),bad,h.context(h.public_parts()[2]))
    assert engine.snapshot()==before
    with pytest.raises(ValueError,match='cursor_mismatch'):submit(engine,1,h.no_change())
    assert engine.snapshot()==before


def test_lifecycle_replacement_and_normative_advisory_identity():
    b=h.bundle().model_copy(deep=True)
    b.review_input.events[3]=b.review_input.events[3].model_copy(update={'content':h.public_parts()[1]})
    e=c.DomainEngine(b,'d'*64);assert submit(e,0,h.mutation(h.meaning(),'c_meaning'))['batch']['accepted']
    submit(e,1,h.no_change());submit(e,2,h.no_change())
    item=h.mutation(h.meaning(),'c_next');item.update(operation='supersede',target_ids=['c_meaning'])
    item['constraint']['supersedes']=['c_meaning']
    assert submit(e,3,item)['batch']['accepted']
    state=e.snapshot()['api_constraint_coverage']
    assert state['c_meaning']['lifecycle']=='superseded' and state['c_next']['lifecycle']=='active'
    p=deepcopy(item['constraint']['predicate']);p['expected']['explanation']='Unverified different prose.'
    source=c.SourceQuote(event_id='e004',quote=h.public_parts()[1])
    assert c.old.normative(c.expanded_predicate(c.ValueSemantics.model_validate(p),source))==c.old.normative(
        c.expanded_predicate(c.ValueSemantics.model_validate(item['constraint']['predicate']),source))


def controlled_grant(operation):
    b=h.bundle().model_copy(deep=True)
    quote={'add_alias':'Allow the server API GET /api/v1/profile to add a state alias for status.',
        'remove_field':'Allow the server API GET /api/v1/profile to remove status.',
        'replace_field':'Allow the server API GET /api/v1/profile to replace status with state.',
        'change_value_semantics':'Allow the server API GET /api/v1/profile to change the value meaning of status from active to enabled.'}[operation]
    b.review_input.events[0]=b.review_input.events[0].model_copy(update={'content':b.review_input.events[0].content+' '+quote})
    impact=b.evidence.impacts[0].model_copy(update={'change_type':operation,'before':{'status':'active'},
        'after':{'add_alias':{'status':'active','state':'active'},'remove_field':{},'replace_field':{'state':'active'},
            'change_value_semantics':{'status':'enabled'}}[operation]})
    b.evidence=b.evidence.model_copy(update={'impacts':[impact]})
    grant={'operation':'grant','source_quote':quote,'target_ids':[],'constraint':None,'effect_domain':'server_api',
        'authorization':{'authorization_id':'a','subjects':[impact.subject.model_dump(mode='json')],'change_type':operation,
            'change_details':{'old_field':'status','new_field':'state' if operation in ('add_alias','replace_field') else None,
                'preserve_value_semantics':operation!='change_value_semantics'},'conditions':[],
            'interpretation':'resolved','constraint_refs':['c_shape'],'supersedes':[]},'value_scope':None}
    if operation=='change_value_semantics':grant['value_scope']={'subject':impact.subject.model_dump(mode='json'),
        'change_type':operation,'old_value':'active','new_value':'enabled'}
    return b,grant


@pytest.mark.parametrize('op',['add_alias','remove_field','replace_field','change_value_semantics'])
def test_frozen_permission_scope_still_covers_only_explicit_operation(op):
    b,grant=controlled_grant(op);e=c.DomainEngine(b,'e'*64)
    assert submit(e,0,h.mutation(),grant)['batch']['accepted']
    assert e.assess()['authorization']=='covered'


@pytest.mark.parametrize('change',['wrong_subject','condition','value'])
def test_permission_failure_and_atomic_context_rollback(change):
    b,grant=controlled_grant('change_value_semantics');e=c.DomainEngine(b,'f'*64)
    if change=='wrong_subject':grant['authorization']['subjects'][0]['field']='state';grant['value_scope']=None
    elif change=='condition':grant['authorization']['conditions']=[{'kind':'candidate_sha256','value':'0'*64,'resolution':'satisfied','evidence_refs':[]}]
    else:grant['value_scope']['new_value']='different_unquoted_value'
    if change=='value':
        before=e.snapshot()
        with pytest.raises(ValueError):submit(e,0,h.mutation(),grant)
        assert e.snapshot()==before
    else:
        row=submit(e,0,h.mutation(),h.context(h.public_parts()[2]),grant)
        if change=='wrong_subject':
            assert not row['batch']['accepted'] and not row['task_context_committed']
            assert e.snapshot()['state']['constraints']==e.snapshot()['task_context']['task_context']==[]
        else:assert row['batch']['accepted'] and e.assess()['authorization']=='not_covered'


@pytest.fixture(scope='module')
def plan():
    with offline_guard():return runtime.prepare(base.read(base.ROOT/'reports/s4-owner-pilot-v5/public-price-review.json'))


@pytest.fixture(scope='module')
def normal(tmp_path_factory,plan):
    folder=tmp_path_factory.mktemp('compact-normal')/'run'
    with offline_guard():
        result=runtime.run(plan,folder,fixed_responses=h.fixtures())
        report=runtime.evaluate(folder)
    return folder,result,report


def test_joint_pipeline_three_methods_independent_replay_and_actual_response_hashes(normal,plan):
    folder,result,report=normal
    assert [r['status'] for r in result['rows']]==['completed']*3
    assert sum(r['attempts'] for r in result['rows'])==11
    assert report['replay']['status']=='verified_settled' and report['reference_read']
    assert report['accuracy'] is report['model_metrics'] is None
    assert plan['real_transport_allowed'] is False and len(plan['sources_sha256'])==263
    trace=base.read(folder/'job-000003.json')['trajectory']
    assert len(trace['transitions'])==5 and trace['final'] is not None
    for index in (1,2,4):assert trace['transitions'][index]['compiler_projection']['no_change_event_binding']['seq']==index+1
    with closing(sqlite3.connect('file:'+str(folder/'journal.sqlite').replace('\\','/')+'?mode=ro',uri=True)) as db:
        for raw,sha,request_bytes,usage in db.execute('SELECT response,response_hash,request,usage FROM attempt'):
            assert digest(raw)==sha
            assert all(token not in request_bytes for token in (b'private_reference',b'expected_state',b'evaluator_only_never_method_input'))
            assert 'compact_contract_projection' in json.loads(usage)
    assert report['replay']['provider_billed_cost'] is None


@pytest.mark.parametrize('name,statuses,attempts,settled',[
    ('bad-anchor',['completed','completed','failed'],6,True),
    ('old-no-change',['completed','completed','failed'],8,True),
    ('source-fault',['failed','not_run','not_run'],2,False),
    ('summary-conflict',['completed','completed','failed'],11,True)])
def test_durable_error_partition_and_complete_denominators(tmp_path,plan,name,statuses,attempts,settled):
    with offline_guard():
        result=runtime.run(plan,tmp_path/name,fixed_responses=h.scenario(name))
        report=runtime.evaluate(tmp_path/name)
    assert [r['status'] for r in result['rows']]==statuses
    assert report['replay']['sent_or_uncertain']==attempts
    assert len(report['replay']['rows'])==3 and report['reference_read'] is settled
    assert report['replay']['status']==('verified_settled' if settled else 'verified_prefix_only')


def test_source_hash_fault_cannot_open_reference(normal,monkeypatch):
    folder,*_=normal
    monkeypatch.setattr(runtime,'sources',lambda:{})
    monkeypatch.setattr(runtime.parser.replay.evaluator,'load_reference_package',lambda *a:pytest.fail('reference before source check'))
    with pytest.raises(base.SharedIntegrityError):runtime.evaluate(folder)


def test_live_and_old_approval_have_no_entry_or_console_key_access(tmp_path,plan):
    calls=[]
    with pytest.raises(PermissionError):runtime.start_live(plan,{},tmp_path/'claims',tmp_path/'run',key_reader=lambda:calls.append('key'))
    with pytest.raises(PermissionError):runtime.run(plan,tmp_path/'run',fixed_responses=None)
    old=base.read(base.ROOT/'reports/s4-owner-pilot-v5/execution-candidate.json')
    with pytest.raises(PermissionError):runtime.run(old,tmp_path/'run',fixed_responses=h.fixtures())
    assert not calls and not (tmp_path/'claims').exists() and not (tmp_path/'run').exists()


def test_wiring_restore_after_failure():
    original=base.prepare
    with pytest.raises(RuntimeError):
        with runtime.wiring():
            with runtime.anchor.wiring():pass
    assert base.prepare is original


def test_original_v5_failures_and_scores_replay_without_new_projection():
    folder=base.ROOT/'reports/local/s4-owner-pilot-real-v5'
    before={p.name:digest(p.read_bytes()) for p in folder.iterdir() if p.is_file()}
    with offline_guard():report=runtime.evaluate(folder)
    assert report==base.read(base.ROOT/'reports/s4-owner-pilot-real-v5-review-v1/evaluation.json')
    assert [r['status'] for r in report['replay']['rows']]==['failed']*3
    assert before=={p.name:digest(p.read_bytes()) for p in folder.iterdir() if p.is_file()}
