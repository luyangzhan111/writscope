"""Finish accepted worker-v1 full batch, then replay faults and restore delivery.

No live execution path. Safe to wait on the normal report; any failed assertion
leaves evidence and never produces a completion receipt.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

from scripts import s4_r7_full_acceptance as acceptance
from scripts import s4_r7_worker_runtime as runtime
from writscope.artifacts import canonical_hash, digest

ROOT = Path(__file__).resolve().parents[1]


def finish(suite):
    while True:
        try:
            normal = json.loads((suite/'normal-evaluation.json').read_text(encoding='utf-8'))
            break
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(10)
    assert len(normal['replay']['rows']) == 270 and all(r['status']=='completed' for r in normal['replay']['rows'])
    acceptance.check(suite)
    print('normal batch verified; starting sequential fault controls', flush=True)
    for name in acceptance.SCENARIOS:
        if not (suite/'faults'/name/'evaluation.json').exists():
            acceptance.fault(suite, name)
    if not all((suite/'tamper'/n/'rejection.json').exists() for n in ('fake_real','receipt','usage','checkpoint','omitted_summary')):
        acceptance.tamper(suite)
    faults = {name: json.loads((suite/'faults'/name/'evaluation.json').read_text(encoding='utf-8')) for name in acceptance.SCENARIOS}
    assert all(v['replay']['planned_jobs']==270 for v in faults.values())
    assert len(normal['selected_complete_pairs'])==12 and normal['accuracy'] is normal['model_metrics'] is None
    attempts = sum(r['sent_or_uncertain'] for r in normal['replay']['rows'])
    matrix = {n: {'status':r['status'],'planned_jobs':270,'reference_read':r['reference_read'],
                 'attempts':sum(x['sent_or_uncertain'] for x in r['replay']['rows']),
                 'reserved_cny_micro':r['received_adapter']['reserved_cny_micro']} for n,r in faults.items()}
    prior = json.loads((ROOT/'reports/s4-r7-worker-v1/verification.json').read_text(encoding='utf-8'))
    for p,h in prior['source_sha256'].items(): assert digest((ROOT/p).read_bytes())==h,p
    for p,h in prior['files_sha256'].items(): assert digest((ROOT/'reports/s4-r7-worker-v1'/p).read_bytes())==h,p
    sources = {**runtime.sources(), **{p:digest((ROOT/p).read_bytes()) for p in
              ('scripts/s4_r7_full_acceptance.py','scripts/s4_r7_full_delivery.py')}}
    remaining = ['authenticated production approval source','target provider tokenizer/server-template/billable overhead evidence',
                 'fresh pricing and specific new execution budget/material-transmission authorization',
                 'actual DNS/TCP/TLS/provider validation after separate authorization']
    old = json.loads((ROOT/'reports/s4-r7-worker-v1/execution-candidate.json').read_text(encoding='utf-8'))
    plan = {'version':'s4_r6_execution_candidate_v6','status':'offline_full_batch_accepted_production_dependencies_pending',
            'prior_candidate_sha256':old['plan_sha256'],'normal_plan_sha256':normal['replay']['plan_sha256'],
            'sources_sha256':sources,'jobs':270,'cases':30,'pairs':12,'repetitions':3,'max_attempts':1080,
            'proposed_cap_cny':'120.00','normal_fixed_attempts':attempts,'faults':matrix,'remaining':remaining,
            'real_transport_allowed':False,'actual_execution_approval':None,'actual_model_calls':0,
            'independent_review':'not_performed_skipped_by_user','reference_status':'provisional_author_draft_not_frozen'}
    acceptance.write(suite/'execution-candidate.json',{'plan':plan,'plan_sha256':canonical_hash(plan)})
    report = ROOT/'docs/s4-r6-r7-full-acceptance-20261102.md'
    report.write_text(f'''# R6/R7 当前响应驱动版全批离线验收

当前 worker-v1 原始源码未改，30 例×三方法×R=3 的 **270 轨全部完成**，实际 {attempts} 次固定模拟尝试，12 对均保留。正常运行、{len(matrix)} 个故障场景及 5 个篡改控制分别保存；故障场景每个都保留 270 项计划分母，篡改控制使用旧 9 轨已保存记录并在读参考前拒绝，不能称为额外全批运行。

沿用 `s4_r7_worker_runtime` 和独立 R4；本次只新增验收编排和交付脚本。所有阶段通过真实本机子进程与 HTTP 协议栈执行，网络流为固定离线字节后端。没有真实 DNS/TCP/TLS、模型或账单证据。模拟 token/成本明确标注；accuracy/model_metrics 均为空，未知账单保持 unknown。

故障包括次数/虚拟额度不足、截断或超大响应后共享预算停止、连续服务错误、reserved/dispatch_started/received/validated/transition_started/transition_finished 中断、发送/接收写库失败和启动超时。截断/超大场景是三次额度的组合控制，不冒充完整正常全批。旧 27 项测试及旧源码/证据哈希复核，不累计成新跑测试。恢复结果以交付目录 completion.json 和 restore-verification.json 为准。

工件：`reports/s4-r7-full-v1/`；交付：`reports/s4-r7-full-delivery-v1/`。复验命令：`.venv\\Scripts\\python.exe -m scripts.s4_r7_full_acceptance replay --suite reports/s4-r7-full-v1`（输出 normal-replay.json 须不存在）。本报告的全批已完成状态必须与机器清单和恢复收据一起审查。

当前版本的全批离线验收不再列为未执行。生产剩余：可信批准来源、目标 tokenizer/服务端模板/附加计费边界、新鲜核价及具体新计划的费用/外发授权；实际网络后端保持关闭。30 例作者参考仍 provisional，未冒充逐例签收；不宣称严格单变量；独立审核为 not_performed_skipped_by_user，预算匹配后置。

本任务密钥读取、实际模型请求、项目材料外发、费用和 GitHub 更新均为 0。旧 S3 授权和设计选择没有被复用为执行批准。
''',encoding='utf-8')
    acceptance.write(suite/'verification.json',{'status':'full_batch_and_fault_replay_passed_restore_pending',
       'cases':30,'pairs':12,'jobs':270,'repetitions':3,'fixed_attempts':attempts,'fault_matrix':matrix,
       'sources_sha256':sources,'candidate_sha256':canonical_hash(plan),'remaining':remaining,
       'actual_model_calls':0,'real_transport_allowed':False,'actual_credentials_read':0,'fees_incurred':0})
    package = ROOT/'reports/s4-r7-full-delivery-v1';package.mkdir()
    paths = {ROOT/p for p in sources};paths.add(report)
    paths.update(p for p in suite.rglob('*') if p.is_file())
    rows=[]
    with zipfile.ZipFile(package/'offline-full.zip','x',compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(paths):
            name=p.relative_to(ROOT).as_posix();raw=p.read_bytes();z.writestr(name,raw)
            rows.append({'path':name,'sha256':digest(raw),'bytes':len(raw)})
    acceptance.write(package/'manifest.json',{'version':'s4_r7_full_delivery_v1','files':rows,
       'archive_sha256':digest((package/'offline-full.zip').read_bytes()),
       'base_layers':json.loads((suite/'restore-base.json').read_text(encoding='utf-8'))['layers'],
       'real_transport_allowed':False})
    destination=Path(json.loads((suite/'restore-base.json').read_text(encoding='utf-8'))['destination'])
    assert destination.resolve().is_relative_to((ROOT/'.work').resolve())
    for row in rows:
        target=destination/row['path'];assert target.resolve().is_relative_to(destination.resolve())
        target.parent.mkdir(parents=True,exist_ok=True);raw=(ROOT/row['path']).read_bytes();assert digest(raw)==row['sha256'];target.write_bytes(raw)
    code = '''import sys,json
from pathlib import Path
sys.path.insert(0,str(Path('src').resolve()))
from scripts import s4_r7_worker_runtime as runtime
from scripts.s4_r7_full_acceptance import PUBLIC
suite=Path('reports/s4-r7-full-v1')
assert runtime.ROOT==Path('.').resolve()
for name in ['normal',*['faults/'+p.name for p in sorted((suite/'faults').iterdir())]]:
 run=suite/name if name=='normal' else suite/name/'run'
 expected=suite/'normal-evaluation.json' if name=='normal' else suite/name/'evaluation.json'
 actual=runtime.evaluate(run,PUBLIC,suite/'scripts.json')
 assert actual==json.loads(expected.read_text(encoding='utf-8')),name
 print('restored replay equal: '+name,flush=True)
Path('restore-verification.json').write_text(json.dumps({'status':'all_15_reports_equal','reports':15,'actual_model_calls':0,'real_transport_allowed':False}),encoding='utf-8')
'''
    restored=subprocess.run([sys.executable,'-c',code],cwd=destination,capture_output=True,text=True)
    acceptance.write(package/'restore-process.json',{'exit_code':restored.returncode,'stdout':restored.stdout,'stderr':restored.stderr})
    assert restored.returncode==0,restored.stderr
    shutil.copyfile(destination/'restore-verification.json',package/'restore-verification.json')
    acceptance.write(package/'completion.json',{'status':'full_batch_fault_replay_and_fresh_restore_passed',
        'jobs':270,'cases':30,'pairs':12,'repetitions':3,'fixed_attempts':attempts,'restored_reports':15,
        'candidate_sha256':canonical_hash(plan),'remaining':remaining,'actual_execution_approval':None,
        'actual_model_calls':0,'actual_credentials_read':0,'materials_transmitted':False,'fees_incurred':0,
        'github_updated':False,'real_transport_allowed':False})
    print('FULL ACCEPTANCE AND RESTORE COMPLETE',flush=True)


if __name__=='__main__':
    finish(ROOT/'reports/s4-r7-full-v1')
