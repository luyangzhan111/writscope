"""Read-only incomplete-run inventory for the R3 per-job offline runtime."""
import json
from pathlib import Path

from scripts.s4_candidate_cost_mock_runner import bindings
from scripts.s4_repeat_schedule import build
from scripts.s4_r3_mock_runtime import __file__ as runtime_path
from scripts.s4_shared_transport_offline import SharedIntegrityError
from scripts.s4_unified_offline import unified_sources
from scripts.s4_unified_recovery import ReadOnlyJournal
from writscope.artifacts import canonical_hash, digest


def inspect(directory, bundle_root, scripts_path):
    directory = Path(directory)
    plan_path = directory/'plan.json'
    ledger_path = directory/'journal.sqlite'
    if any(p.is_symlink() or not p.is_file() for p in (plan_path, ledger_path)):
        raise SharedIntegrityError('missing or linked recovery artifact')
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    schedule = plan['schedule']['plan']
    if (plan.get('version') != 's4_r3_mock_runtime_v1' or plan.get('execution_kind') != 'offline_mock' or
            plan.get('real_transport_allowed') is not False or plan.get('actual_model_calls') != 0 or
            plan.get('runtime_sha256') != digest(Path(runtime_path).read_bytes()) or
            plan.get('source_binding') != bindings() or plan.get('unified_sources') != unified_sources() or
            plan.get('scripts_sha256') != digest(Path(scripts_path).read_bytes()) or
            plan['schedule'] != build(Path(bundle_root), schedule['cases'], schedule['repetitions'])):
        raise SharedIntegrityError('recovery plan binding mismatch')
    jobs = schedule['jobs']
    by_hash = {canonical_hash(job): job for job in jobs}
    if len(by_hash) != len(jobs):
        raise SharedIntegrityError('recovery identity collision')
    snapshot = ReadOnlyJournal(ledger_path, plan, plan['policy']).snapshot()
    grouped = {job_hash: [] for job_hash in by_hash}
    for attempt in snapshot['attempts']:
        if attempt['job'] not in grouped:
            raise SharedIntegrityError('unplanned journal attempt')
        grouped[attempt['job']].append(attempt)
    files = sorted(directory.glob('job-*.json'))
    for index, path in enumerate(files, 1):
        if index > len(jobs) or path.is_symlink() or path.name != f'job-{index:06d}.json':
            raise SharedIntegrityError('invalid job artifact sequence')
        record = json.loads(path.read_text(encoding='utf-8'))
        if record.get('identity') != jobs[index-1]:
            raise SharedIntegrityError('job artifact identity mismatch')
    rows = []
    for index, job in enumerate(jobs):
        attempts = grouped[canonical_hash(job)]
        pending = any(a['status'] in {'reserved','dispatch_started','received'} for a in attempts)
        status = ('unresolved_attempt' if pending else 'job_file_present_unverified' if index < len(files)
                  else 'attempts_recorded_trajectory_unknown' if attempts else 'no_attempt_trajectory_unknown')
        rows.append({**job, 'recovery_status':status,
                     'dispatch_started_or_later':sum(a['status'] != 'reserved' for a in attempts)})
    return {'version':'s4_r3_mock_recovery_v1','planned_jobs':len(jobs),'rows':rows,
            'unresolved_attempt_ids':[a['id'] for a in snapshot['attempts']
                                      if a['status'] in {'reserved','dispatch_started','received'}],
            'reserved_virtual_micro':snapshot['reserved_virtual_micro'],
            'provider_billed_cost':None,'actual_model_calls':0,
            'limitations':['job files not verified or replayed','unwritten state and lookups unknown',
                           'virtual reservations are not real charges']}
