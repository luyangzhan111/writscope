"""Maintained R3 offline entry. Reuses checkpoint executor and provider; no live I/O."""
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from scripts import s4_r3_authority as authority
from scripts.s4_accounting_core import aggregate
from scripts.s4_r3_checkpoint_runtime_v1 import prepare as legacy_prepare
from scripts.s4_r3_checkpoint_executor_v1 import execute
from scripts.s4_r3_checkpoint_journal_v1 import CheckpointJournal
from scripts.s4_r3_provider_bridge_v1 import MockProviderBridge
from scripts.s4_shared_transport_offline import SharedIntegrityError
from scripts.s4_simulated_artifact_bundle import _bytes, _write
from scripts.s4_synthetic_cost_ledger import validate_policy
from scripts.s4_synthetic_cost_wire import mock_wire
from scripts.s4_unified_journal import now as utc_now
from writscope.artifacts import canonical_hash, digest

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = ['public_bundle','method_prompt_and_schema','visible_history','method_state','public_lookup_result']


class Journal(CheckpointJournal):
    """Budget stop forbids new work but permits exactly one safe terminal checkpoint."""
    def append_checkpoint(self, job, kind, payload):
        snapshot = self.snapshot()
        if snapshot['stopped'] is None:
            return super().append_checkpoint(job,kind,payload)
        events = self.checkpoints()
        own = [e for e in events if e['job']==job]
        if (snapshot['stopped'] != 'synthetic_budget_exhausted' or kind != 'job_finished' or
                payload.get('status') not in ('failed','not_run') or
                payload.get('failure',{}).get('detail') != 'round_budget_exhausted' or
                not own or own[0]['kind'] != 'job_started' or events[-1]['job'] != job or any(e['kind']=='job_finished' for e in own) or
                any(a['status'] in ('reserved','dispatch_started','received') for a in snapshot['attempts'])):
            raise SharedIntegrityError('stopped journal permits only budget terminal checkpoint')
        with closing(self.connect()) as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT plan_hash,policy_hash,stopped FROM run').fetchone() != (
                        self.plan_hash,canonical_hash(self.policy),'synthetic_budget_exhausted'):
                    raise SharedIntegrityError('terminal binding mismatch')
                if db.execute("SELECT 1 FROM checkpoint WHERE job=? AND kind='job_finished'",(job,)).fetchone():
                    raise SharedIntegrityError('duplicate terminal checkpoint')
                last = db.execute('SELECT seq,event_hash FROM checkpoint ORDER BY seq DESC LIMIT 1').fetchone()
                obj = {'seq':last[0]+1,'job':job,'kind':kind,
                       'payload':json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')),
                       'created_at':utc_now(),'previous_hash':last[1]}
                db.execute('INSERT INTO checkpoint VALUES (?,?,?,?,?,?,?)',(*obj.values(),canonical_hash(obj)))
                db.execute('COMMIT')
            except BaseException:
                if db.in_transaction: db.execute('ROLLBACK')
                raise


class ReadOnlyJournal(Journal):
    def connect(self):
        return sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,isolation_level=None)


def prepare(bundle_root,scripts_path,cases,repetitions,policy,*,price_valid_until):
    policy = validate_policy(policy)
    authority.timestamp(price_valid_until)
    base = legacy_prepare(bundle_root,scripts_path,cases,repetitions,policy)
    return {'version':'s4_r3_offline_v2','base':base,'execution_kind':'offline_mock',
            'model_config':{'model':'deepseek-flash','temperature':0.0,'max_tokens':4096},
            'endpoint':'https://mock.invalid/chat','materials':MATERIALS[:],
            'price':{'kind':'synthetic_only_not_currency','valid_until':price_valid_until,
                     'input_rate':policy['input_rate_per_million'],'output_rate':policy['output_rate_per_million']},
            'concurrency':1,'retries':0,'real_transport_allowed':False,'actual_model_calls':0,
            'sources':{p:digest((ROOT/p).read_bytes()) for p in ('scripts/s4_r3.py','scripts/s4_r3_authority.py',
                        'scripts/s4_repeat_schedule.py','scripts/s4_accounting_core.py','scripts/s4_simulated_artifact_bundle.py')}}


def check_plan(plan,bundle_root,scripts_path):
    base = plan['base']; schedule = base['schedule']['plan']
    expected = prepare(bundle_root,scripts_path,schedule['cases'],schedule['repetitions'],base['policy'],
                       price_valid_until=plan['price']['valid_until'])
    if plan != expected: raise ValueError('plan or source binding mismatch')


def start(plan,event_id,source,expected_actor,claim_directory,output,bundle_root,scripts_path,*,now=None):
    now = now or datetime.now(timezone.utc)
    check_plan(plan,bundle_root,scripts_path)
    if now.tzinfo is None or authority.timestamp(plan['price']['valid_until']) <= now:
        raise ValueError('synthetic price expired')
    evidence = authority.verify(plan,event_id,source,expected_actor,now)
    claims, output, bundles = Path(claim_directory),Path(output),Path(bundle_root)
    for path in (claims,output.parent):
        if not path.is_dir() or any(p.is_symlink() or p.is_junction() for p in (path,*path.parents)):
            raise ValueError('invalid or linked output directory')
    if output.exists() or output.resolve().is_relative_to(bundles.resolve()) or claims.resolve().is_relative_to(bundles.resolve()):
        raise ValueError('output overlaps method input or already exists')
    # Validate policy and scripts before consuming the once-only execution opportunity.
    raw = json.loads(Path(scripts_path).read_text(encoding='utf-8'))
    from writscope.providers.mock import Script
    if raw.get('version') != 's4_p2_scripts_v1': raise ValueError('invalid synthetic script version')
    for identity in plan['base']['schedule']['plan']['jobs']:
        script = Script.model_validate(raw['jobs'][identity['case_id']+'/'+identity['method']])
        if script.execution_kind != 'offline_mock' or any(s.delay_seconds or s.response_ref is not None for s in script.steps):
            raise ValueError('only inline offline scripts allowed')
    claim_path = claims/(canonical_hash(plan)+'.r3-claim.json')
    try: fd = os.open(claim_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError: raise ValueError('plan already claimed') from None
    with os.fdopen(fd,'wb') as file:
        file.write(_bytes({'version':'s4_r3_test_claim_v2','plan_sha256':canonical_hash(plan),
                           'evidence':evidence,'output':str(output.resolve())}))
        file.flush(); os.fsync(file.fileno())
    return _run(plan,output,bundles,Path(scripts_path))


def _run(plan,directory,bundle_root,scripts_path):
    check_plan(plan,bundle_root,scripts_path)
    directory.mkdir(exist_ok=False)
    _write(directory/'plan.json',_bytes(plan))
    policy = plan['base']['policy']
    journal = Journal(directory/'journal.sqlite',plan,policy,create=True)
    rows, stopped = [], None
    for index,identity in enumerate(plan['base']['schedule']['plan']['jobs'],1):
        if stopped:
            rows.append({**identity,'status':'not_run','attempts':0,'final_present':False}); continue
        def checkpoint(case,method,kind,payload):
            if case != identity['case_id'] or method != identity['method']:
                raise SharedIntegrityError('checkpoint identity mismatch')
            journal.append_checkpoint(canonical_hash(identity),kind,payload)
        bridge = MockProviderBridge(journal,identity['repeat_index'])
        def dispatch(case,method,stage,request,step):
            try:
                check_plan(plan,bundle_root,scripts_path)
                journal.checkpoints()
            except Exception as exc:
                raise SharedIntegrityError('pre-send source or isolation binding failure') from exc
            if case!=identity['case_id'] or method!=identity['method']:
                raise SharedIntegrityError('dispatch identity mismatch')
            bridge(case,method,stage,request,step)
        result = execute(bundle_root,scripts_path,[identity['case_id']],[identity['method']],policy['max_attempts'],
                         dispatch,checkpoint)
        job = result['jobs'][0]; snapshot = journal.snapshot()
        dispatched = sum(a['job']==canonical_hash(identity) and a['status']!='reserved' for a in snapshot['attempts'])
        if len(job['attempts'])>dispatched:
            del job['attempts'][dispatched:]
            if not dispatched: job['status']='not_run'
        stopped = result['round_integrity_failure'] or snapshot['stopped']
        _write(directory/f'job-{index:06d}.json',_bytes({'identity':identity,'trajectory':job,
                                                       'round_integrity_failure':result['round_integrity_failure']}))
        rows.append({**identity,'status':job['status'],'attempts':dispatched,'final_present':job['final'] is not None})
    summary = {'version':'s4_r3_result_v2','plan_sha256':canonical_hash(plan),'rows':rows,'stop_reason':stopped,
               'journal_stop':journal.snapshot()['stopped'],'actual_model_calls':0,'accuracy':None,'provider_billed_cost':None}
    _write(directory/'result.json',_bytes(summary))
    return summary


def start_real(*args,**kwargs):
    raise PermissionError('real sending and credential access disabled; production approval source not installed')


def inspect(directory,bundle_root,scripts_path):
    """Read-only persistence/cost handoff, including incomplete runs; not semantic scoring."""
    directory = Path(directory)
    def read(path):
        if path.is_symlink() or not path.is_file(): raise ValueError('missing or linked artifact')
        return json.loads(path.read_text(encoding='utf-8'))
    if any(p.is_symlink() or p.is_junction() for p in (directory,*directory.parents)):
        raise ValueError('linked artifact directory')
    plan = read(directory/'plan.json'); check_plan(plan,bundle_root,scripts_path)
    journal = ReadOnlyJournal(directory/'journal.sqlite',plan,plan['base']['policy'])
    snapshot, events = journal.snapshot(),journal.checkpoints()
    jobs = plan['base']['schedule']['plan']['jobs']; ids = [canonical_hash(j) for j in jobs]
    for entries in (snapshot['attempts'],events):
        order = [ids.index(e['job']) if e['job'] in ids else -1 for e in entries]
        if -1 in order or order != sorted(order): raise ValueError('unplanned or reordered journal')
    summary = read(directory/'result.json') if (directory/'result.json').exists() else None
    if summary is not None:
        keys = ('case_id','method','track','repeat_index')
        if (summary['plan_sha256']!=canonical_hash(plan) or summary['actual_model_calls']!=0 or
                [{k:r[k] for k in keys} for r in summary['rows']]!=jobs or
                summary['journal_stop']!=snapshot['stopped']): raise ValueError('summary binding mismatch')
    paths = sorted(directory.glob('job-*.json'))
    if any(i>len(jobs) or p.name!=f'job-{i:06d}.json' for i,p in enumerate(paths,1)):
        raise ValueError('job file sequence mismatch')
    rows, costs = [], []
    for index,(identity,job_id) in enumerate(zip(jobs,ids),1):
        attempts = [a for a in snapshot['attempts'] if a['job']==job_id]
        own = [e for e in events if e['job']==job_id]
        open_operation = None; finished = {}; local_seq = 0
        for event in own:
            kind = event['kind']; payload = json.loads(event['payload'])
            if kind=='job_started': continue
            if kind=='job_finished': continue
            if kind.endswith('_started'):
                if open_operation: raise ValueError('overlapping local operations')
                open_operation = kind.removesuffix('_started')
            elif kind.endswith('_finished'):
                if open_operation!=kind.removesuffix('_finished'): raise ValueError('local operation order mismatch')
                finished.setdefault(open_operation,[]).append(payload)
                local_seq += 1
                costs.append({'entry_id':f'{job_id}/local/{local_seq}','stage':'local_update' if open_operation=='transition' else 'local_validation',
                              'elapsed_ms':payload.get('elapsed_ms'),'operation':open_operation})
                open_operation = None
            else: raise ValueError('unknown checkpoint')
        if open_operation:
            costs.append({'entry_id':f'{job_id}/local/pending','stage':'local_update' if open_operation=='transition' else 'local_validation',
                          'elapsed_ms':None,'operation':open_operation})
        for attempt in attempts:
            if attempt['status']=='reserved': continue
            usage = json.loads(attempt['usage']) if attempt['usage'] else {}
            costs.append({'entry_id':f"attempt/{attempt['id']}",
                'stage':'lookup_continuation' if attempt['is_continuation'] else 'extract' if attempt['stage']=='incremental_extract' else 'final',
                **{k:usage.get(k) for k in ('input_tokens','output_tokens','cache_hit_input_tokens','cache_miss_input_tokens','reasoning_tokens')},
                'elapsed_ms':usage.get('provider_elapsed_ms'),'billed_cost_cny':None,'estimated_cost_cny':None})
        record = read(paths[index-1]) if index<=len(paths) else None
        if record:
            job = record['trajectory']; sent = [a for a in attempts if a['status']!='reserved']
            if record['identity']!=identity or len(job['attempts'])!=len(sent): raise ValueError('job binding mismatch')
            for attempt,saved in zip(job['attempts'],sent):
                if (mock_wire(attempt['request'])!=saved['request'] or attempt['stage']!=saved['stage'] or
                        int('lookup_response' in attempt['request']['public_input'])!=saved['is_continuation']):
                    raise ValueError('request binding mismatch')
                if saved['status']=='completed' and json.loads(saved['response'])['choices'][0]['message']['content']!=attempt['response']:
                    raise ValueError('response binding mismatch')
            for kind,key in (('transition','transitions'),('lookup','lookups')):
                if [p[kind] for p in finished.get(kind,[])]!=job[key]:
                    raise ValueError('checkpoint trajectory mismatch')
            if job['status']=='completed' and ([p['final'] for p in finished.get('final_validation',[])]!=[job['final']] or open_operation):
                raise ValueError('final checkpoint mismatch')
            if summary:
                row = summary['rows'][index-1]
                if row['status']!=job['status'] or row['attempts']!=len(sent) or row['final_present']!=(job['final'] is not None):
                    raise ValueError('job summary mismatch')
        elif summary and (summary['rows'][index-1]['status']!='not_run' or attempts):
            raise ValueError('missing executed job record')
        pending = [a['id'] for a in attempts if a['status'] in ('reserved','dispatch_started','received')]
        rows.append({**identity,'saved_status':record['trajectory']['status'] if record else None,
                     'open_operation':open_operation,'unresolved_attempts':pending,
                     'sent_or_uncertain':sum(a['status']!='reserved' for a in attempts),
                     'unsent_reservations':sum(a['status']=='reserved' for a in attempts)})
    return {'status':'offline_persistence_checked_not_semantically_replayed','planned_jobs':len(jobs),'rows':rows,
            'stop_reason':summary['stop_reason'] if summary else None,'journal_stop':snapshot['stopped'],
            'reserved_virtual_micro':snapshot['reserved_virtual_micro'],'cost':aggregate(costs),
            'shared_evidence_elapsed_ms':None,'actual_model_calls':0,'provider_billed_cost':None,'accuracy':None}
