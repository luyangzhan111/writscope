"""Verify a v9 engineering snapshot; optionally replay its fixed test evidence.

The only public anchor this demo loads is explicitly test_only. Production
verification requires an independently provisioned host trust root and is closed.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from scripts.s4_r5_acceptance import offline_guard
from scripts.s4_r7_approval_source_v2 import HostTrust
from scripts.s4_r7_bound_approval_v3 import BoundHostSource
from scripts import s4_r7_bound_runtime as runtime
from writscope.artifacts import digest, canonical_hash

ROOT=Path(__file__).resolve().parents[1]
SUITE=ROOT/'reports/s4-r7-bound-receipts-v1'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def verify(replay=False):
    wrapped=load(SUITE/'engineering-candidate.json');plan=wrapped['plan']
    if (canonical_hash(plan)!=wrapped['plan_sha256'] or plan['real_transport_allowed'] is not False or
            plan['actual_execution_approval'] is not None or plan['owner_baseline_status']!='accepted_v8_offline_only'):
        raise ValueError('candidate hash or authority changed')
    for path,sha in plan['files_sha256'].items():
        if digest((ROOT/path).read_bytes())!=sha:raise ValueError('candidate dependency changed: '+path)
    preserved=load(SUITE/'preservation-before.json')['files']
    for path,sha in preserved.items():
        if digest((ROOT/path).read_bytes())!=sha:raise ValueError('preserved v8 byte changed: '+path)
    normal=ROOT/plan['normal_evidence']
    runtime.check_plan(load(normal/'run/plan.json'),ROOT/'reports/s4-r6-candidate-v1/public',normal/'legacy-script-binding.json')
    if replay:
        public=load(normal/'host-public-test-anchor.json')
        if public.get('not_for_production') is not True or public.get('deployment')!='test_only':
            raise PermissionError('only packaged synthetic test anchor may be used by demo')
        trust=HostTrust(public['source_id'],public['tenant'],public['conversation'],public['actor'],int(public['n']))
        def forbidden(*args,**kwargs):raise AssertionError('read-only replay queried host')
        source=BoundHostSource(trust,forbidden,lambda:datetime(2026,11,2,16,tzinfo=timezone.utc))
        with offline_guard():
            report=runtime.evaluate(normal/'run',ROOT/'reports/s4-r6-candidate-v1/public',
                                    normal/'legacy-script-binding.json',source,'bound-event',source.clock)
        if report!=load(normal/'evaluation.json'):raise ValueError('restored independent R4 report changed')
    return {'status':'v9_snapshot_verified_and_replayed' if replay else 'v9_snapshot_verified',
            'candidate_sha256':wrapped['plan_sha256'],'planned_sample_jobs':9,'fixed_attempts':23,
            'real_transport_allowed':False,'actual_model_calls':0,'actual_credentials_read':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--replay',action='store_true')
    print(json.dumps(verify(parser.parse_args().replay)))
