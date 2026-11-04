"""Verify v10 IPC engineering artifacts and replay fixed evidence, offline only."""
from datetime import datetime,timezone
import argparse
import json
from pathlib import Path
from unittest.mock import patch

from scripts import s4_r7_host_ipc_runtime as runtime
from scripts.s4_r7_host_ipc import LocalPipeChannel
from scripts.s4_r7_bound_approval_v3 import BoundHostSource
from scripts.s4_r7_approval_source_v2 import HostTrust
from scripts.s4_r5_acceptance import offline_guard
from writscope.artifacts import digest,canonical_hash

ROOT=Path(__file__).resolve().parents[1]
SUITE=ROOT/'reports/s4-r7-host-ipc-v1'


def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def verify(replay=False):
    wrapped=load(SUITE/'engineering-candidate.json');plan=wrapped['plan']
    if (canonical_hash(plan)!=wrapped['plan_sha256'] or plan['real_transport_allowed'] is not False or
            plan['actual_execution_approval'] is not None or plan['owner_baseline_status']!='accepted_v8_offline_only'):
        raise ValueError('IPC candidate hash/authority invalid')
    for path,sha in plan['files_sha256'].items():
        if digest((ROOT/path).read_bytes())!=sha:raise ValueError('IPC dependency changed: '+path)
    for path,sha in load(SUITE/'preservation-before.json')['files'].items():
        if digest((ROOT/path).read_bytes())!=sha:raise ValueError('v9/historical file changed: '+path)
    normal=ROOT/plan['normal_evidence'];normal_plan=load(normal/'run/plan.json')
    runtime.check_plan(normal_plan,ROOT/'reports/s4-r6-candidate-v1/public',normal/'legacy-script-binding.json')
    if replay:
        test_anchor=load(normal/'host-public-test-anchor.json')
        if test_anchor.get('not_for_production') is not True or test_anchor.get('deployment')!='test_only':
            raise PermissionError('only explicit test fixture anchor allowed by demonstration')
        trust=HostTrust(test_anchor['source_id'],test_anchor['tenant'],test_anchor['conversation'],test_anchor['actor'],int(test_anchor['n']))
        def forbidden(*a,**k):raise AssertionError('archive verification attempted local IPC')
        # The pipe may no longer exist; replay verifies saved bytes, never connects.
        source=BoundHostSource(trust,LocalPipeChannel(normal_plan['host_ipc']['pin'],deadline=normal_plan['host_ipc']['deadline']),
                               lambda:datetime(2026,11,2,16,tzinfo=timezone.utc))
        with offline_guard(),patch.object(LocalPipeChannel,'__call__',forbidden):
            report=runtime.evaluate(normal/'run',ROOT/'reports/s4-r6-candidate-v1/public',normal/'legacy-script-binding.json',
                                    source,'ipc-event',source.clock)
        if report!=load(normal/'evaluation.json'):raise ValueError('independent IPC/R4 replay changed')
    return {'status':'v10_verified_and_replayed' if replay else 'v10_verified','candidate_sha256':wrapped['plan_sha256'],
            'normal_jobs':3,'fixed_attempts':11,'host_ipc_receipts':22,'real_transport_allowed':False,'actual_model_calls':0}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--replay',action='store_true')
    print(json.dumps(verify(p.parse_args().replay)))
