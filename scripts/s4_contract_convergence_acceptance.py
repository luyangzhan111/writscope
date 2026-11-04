"""Public-only fixed controls and offline acceptance; never model answers.

This is a control harness over the existing executor and R4, not a new runner.
"""
import argparse
import base64
import json
from pathlib import Path

from scripts import s4_owner_pilot as base
from scripts import s4_contract_convergence_offline as runtime
from scripts.s4_r5_acceptance import offline_guard
from writscope.artifacts import canonical_hash


def bundle():
    return base.checked_bundle(base.PUBLIC/base.CASE)


def public_parts():
    b=bundle(); event=b.review_input.events[0]
    client='可以在客户端本地映射成页面需要的 state。'
    quote=event.content[event.content.index('GET /api/v1/profile'):event.content.index(client)]
    return b,quote,client


def shape(exact=True):
    return {'kind':'response_shape','expected':{'status':'active'},
        'qualifiers':{'exact_keys':exact,'http_status':200,'field_types':{'status':'string'}}}


def meaning():
    return {'kind':'value_semantics','expected':{'source_text':'账户启用','explanation':'Advisory only: account enabled.'},
        'qualifiers':{'meaning_quote':'值为字符串 active，表示账户启用'}}


def no_change():
    return {'operation':'no_change','target_ids':[],'constraint':None,'authorization':None,'value_scope':None}


def mutation(predicate=None, key='c_shape', quote=None):
    b,anchor,_=public_parts()
    return {'operation':'add_constraint','source_quote':quote or anchor,'target_ids':[],
        'constraint':{'constraint_id':key,'subjects':[b.evidence.impacts[0].subject.model_dump(mode='json')],
            'predicate':predicate or shape(),'interpretation':'resolved','supersedes':[]},
        'authorization':None,'value_scope':None}


def context(quote):
    return {'operation':'record_context','source_quote':quote,'target_ids':[],'constraint':None,
        'authorization':None,'value_scope':None,'verification_refs':[]}


def final(method):
    b,quote,client=public_parts(); source={'event_id':'e001','quote':quote}
    subject=b.evidence.impacts[0].subject.model_dump(mode='json')
    constraints=[{'item_id':key,'kind':'constraint','subjects':[subject], 'predicate':p,
        'lifecycle':'active','interpretation':'resolved','source':source}
        for key,p in (('c_shape',shape()),('c_meaning',meaning()))]
    return {'kind':'final','task_context':[{'source_ref':{'event_id':'e001','quote':client},'verification_refs':[]}],
        'payload':{'impact':'breaking','authorization':'not_covered','decision':'revise','affected_subjects':[subject],
            'per_impact_authorization':[{'impact_id':b.evidence.impacts[0].impact_id,'verdict':'not_covered',
                'constraint_refs':['c_shape','c_meaning'],'authorization_refs':[],'source_refs':[source],
                'reason':'Authored public-input control; no explicit server permission.'}],
            'constraint_refs':['c_shape','c_meaning'],'authorization_refs':[],'source_refs':[source],
            'evidence_refs':['impact'],'reason':'Synthetic pipeline check, not a model prediction.',
            'limitations':['fixed public-only control, no model accuracy'], 'derived_items':constraints,
            'checklist':{k:'Synthetic reconstruction from public history only.' for k in
                ('reconstruction','subject_check','version_check','operation_check','conditions_check','lifecycle_check')}
                if method=='full_checklist' else None}}


def encode(payload, finish='stop'):
    raw={'model':'deepseek-flash','choices':[{'message':{'content':json.dumps(payload,ensure_ascii=False)},'finish_reason':finish}],
        'usage':{'prompt_tokens':120,'completion_tokens':30,'total_tokens':150,
            'prompt_cache_hit_tokens':20,'prompt_cache_miss_tokens':100,'completion_tokens_details':{'reasoning_tokens':0}}}
    return {'body_base64':base64.b64encode(json.dumps(raw,ensure_ascii=False).encode()).decode()}


def fixtures(lookup=True):
    b,_,client=public_parts(); fixed={}
    for method in ('full_direct','full_checklist'):
        fixed[base.CASE+'/'+method]=([encode({'kind':'lookup','event_ids':['e001']})] if lookup else [])+[encode(final(method))]
    updates=[{'kind':'batch','items':[mutation(),mutation(meaning(),'c_meaning'),context(client)]},
        {'kind':'batch','items':[no_change()]}, {'kind':'batch','items':[no_change()]},
        {'kind':'batch','items':[context(b.review_input.events[3].content)]}, {'kind':'batch','items':[no_change()]}]
    # Endpoint advisory wording may differ without changing normative source.
    endpoint=final('incremental');endpoint['payload']['derived_items'][1]['predicate']['expected']['explanation']='Other advisory wording.'
    fixed[base.CASE+'/incremental']=([encode({'kind':'lookup','event_ids':['e001']})] if lookup else [])+[encode(v) for v in updates]+[encode(endpoint)]
    return fixed


SCENARIOS=('normal','no-lookup','bad-anchor','old-no-change','source-fault','summary-conflict','truncated','timeout','interrupted')


def scenario(name):
    fixed=fixtures(lookup=name!='no-lookup')
    if name=='bad-anchor':
        value={'kind':'batch','items':[mutation(meaning(),'c_meaning','值为字符串 active，表示账户启用。')]}
        fixed[base.CASE+'/incremental'][1]=encode(value)
    elif name=='old-no-change':
        item=no_change();item['source_quote']=bundle().review_input.events[1].content
        fixed[base.CASE+'/incremental'][3]=encode({'kind':'batch','items':[item]})
    elif name=='source-fault':
        value=final('full_direct');value['payload']['derived_items'][0]['source']['event_id']='e002'
        fixed[base.CASE+'/full_direct'][1]=encode(value)
    elif name=='summary-conflict':
        value=final('incremental');value['payload']['derived_items'][1]['predicate']['expected']['source_text']='启用'
        fixed[base.CASE+'/incremental'][-1]=encode(value)
    elif name=='truncated':
        fixed[base.CASE+'/full_direct'][0]=encode(final('full_direct'),finish='length')
    elif name=='timeout':
        fixed[base.CASE+'/full_direct'][0]={'fault':'timeout'}
    elif name not in SCENARIOS:
        raise ValueError('unknown control scenario')
    return fixed


def run_suite(output, price):
    output=Path(output); output.mkdir(exist_ok=False)
    with offline_guard():
        plan=runtime.prepare(price)
        base.write(output/'offline-candidate.json',plan)
        summaries={}
        for name in SCENARIOS:
            responses=scenario(name);base.write(output/(name+'-responses.json'),responses)
            def hook(point, job, attempt):
                if name=='interrupted' and point=='received' and attempt==1:
                    raise KeyboardInterrupt('fixed interruption after durable receipt')
            result=runtime.run(plan,output/name,fixed_responses=responses,hook=hook)
            report=runtime.evaluate(output/name);base.write(output/(name+'-evaluation.json'),report)
            summaries[name]={'statuses':[r['status'] for r in result['rows']],
                'attempts':report['replay']['sent_or_uncertain'], 'replay':report['replay']['status'],
                'reference_read':report['reference_read'], 'stop_reason':result['stop_reason']}
    base.write(output/'controls-summary.json',{'candidate_sha256':canonical_hash(plan),'scenarios':summaries,
        'actual_model_calls':0,'real_transport_allowed':False})
    return summaries


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--price',type=Path,required=True)
    args=p.parse_args(); print(json.dumps(run_suite(args.output,base.read(args.price)),ensure_ascii=False))


if __name__=='__main__':
    main()
