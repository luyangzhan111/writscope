"""Supplement size control: one oversized response, not an oversized fixture."""
import json
from pathlib import Path
import shutil
from unittest.mock import patch

from scripts import s4_r7_full_acceptance as acceptance
from scripts import s4_r7_worker_runtime as runtime
from writscope.artifacts import digest


def main():
    base = Path('reports/s4-r7-full-v1')
    suite = Path('reports/s4-r7-size-control-v2')
    suite.mkdir()
    for name in ('scripts.json', 'provider-fixtures.json', 'bindings.json'):
        shutil.copyfile(base/name, suite/name)
    original = json.loads((base/'provider-fixtures.json').read_text(encoding='utf-8'))
    write = acceptance.write
    def one_large(path, value):
        if Path(path).name == 'fixtures.json':
            for key in value['jobs']:
                if key != 's4_001/full_direct':
                    value['jobs'][key] = original['jobs'][key]
        return write(path, value)
    with patch.object(acceptance, 'write', one_large):
        acceptance.fault(suite, 'size_then_budget')
    run = suite/'faults/size_then_budget/run'
    plan = json.loads((run/'plan.json').read_text(encoding='utf-8'))
    journal = runtime.ReadOnlyJournal(run/'journal.sqlite', plan, plan['base']['policy'])
    rows = journal.snapshot()['attempts']
    assert rows[0]['error_kind'] == 'response_size_exceeded'
    assert len(rows[0]['response']) == 100001
    assert json.loads(rows[0]['usage'])['transport_receipt']['worker']['dispatch_permitted']
    assert len(rows) == 3
    acceptance.write(suite/'verification.json', {'status':'actual_response_size_boundary_exercised',
        'planned_jobs':270,'attempts':len(rows),'captured_prefix_bytes':100001,'error_kind':rows[0]['error_kind'],
        'fixture_bytes':(suite/'faults/size_then_budget/fixtures.json').stat().st_size,
        'source_sha256':digest(Path(__file__).read_bytes()),'runtime_sources':runtime.sources(),
        'actual_model_calls':0,'real_transport_allowed':False})


if __name__=='__main__': main()
