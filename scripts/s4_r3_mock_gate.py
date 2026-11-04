"""R3 authorization and one-use launch rehearsal. Strictly mock-only.

Synthetic fixtures are not user consent. There is deliberately no real launch,
credential reader, network client, or switch that enables them.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from scripts.s4_repeat_schedule import build
from scripts.s4_r3_mock_runtime import run
from scripts import s4_r3_mock_runtime as mock_runtime
from scripts.s4_unified_offline import unified_sources
from scripts.s4_candidate_cost_mock_runner import bindings
from scripts.s4_synthetic_cost_ledger import validate_policy
from writscope.artifacts import canonical_hash, digest


def prepare(bundle_root, scripts_path, cases, repetitions, policy):
    policy = validate_policy(policy)
    if policy['max_output_tokens'] != 4096:
        raise ValueError('mock output limit mismatch')
    schedule = build(Path(bundle_root), cases, repetitions)
    plan = {'version':'s4_r3_mock_plan_v1', 'execution_kind':'offline_mock', 'status':'draft_unapproved',
            'schedule':schedule, 'scripts_sha256':digest(Path(scripts_path).read_bytes()),
            'runtime_sources':bindings(), 'unified_sources':unified_sources(),
            'gate_sha256':digest(Path(__file__).read_bytes()),
            'job_runtime_sha256':digest(Path(mock_runtime.__file__).read_bytes()), 'policy':policy,
            'recipient':'mock.invalid', 'materials':'public_synthetic_only', 'price_kind':'synthetic_only',
            'real_transport_allowed':False, 'actual_model_calls':0}
    return plan


def validate(plan, fixture, bundle_root, scripts_path, *, now=None):
    if not isinstance(plan, dict) or not isinstance(fixture, dict):
        raise ValueError('synthetic plan and fixture required')
    schedule = plan.get('schedule', {}).get('plan', {})
    expected = prepare(bundle_root, scripts_path, schedule.get('cases'), schedule.get('repetitions'), plan.get('policy'))
    if plan != expected:
        raise ValueError('synthetic plan or source mismatch')
    if set(fixture) != {'kind','plan_sha256','expires_at','recipient','materials','cap_micro','max_attempts'} or fixture['kind'] != 'synthetic_test_fixture':
        raise ValueError('synthetic fixture required; self-reported user approvals are not accepted')
    if (fixture['plan_sha256'] != canonical_hash(plan) or fixture['recipient'] != plan['recipient'] or
        fixture['materials'] != plan['materials'] or type(fixture['cap_micro']) is not int or
        fixture['cap_micro'] != plan['policy']['cap_micro'] or type(fixture['max_attempts']) is not int or
        fixture['max_attempts'] != plan['policy']['max_attempts']):
        raise ValueError('synthetic scope mismatch')
    try:
        expires = datetime.fromisoformat(fixture['expires_at'])
    except (ValueError, TypeError):
        raise ValueError('invalid synthetic expiry') from None
    current = now if now is not None else datetime.now(timezone.utc)
    if expires.tzinfo is None or current.tzinfo is None or expires <= current:
        raise ValueError('synthetic fixture expired')
    return canonical_hash(plan)


def start(plan, fixture, claim_directory, output, bundle_root, scripts_path, *, now=None):
    """One synthetic claim, then run the fixed mock; never reads credentials."""
    plan_hash = validate(plan, fixture, bundle_root, scripts_path, now=now)
    claim_directory, output = Path(claim_directory), Path(output)
    if claim_directory.is_symlink() or not claim_directory.is_dir():
        raise ValueError('claim directory must be an existing local directory')
    if output.exists() or output.resolve().is_relative_to(Path(bundle_root).resolve()):
        raise ValueError('invalid mock output directory')
    claim = claim_directory / (plan_hash + '.mock-claim.json')
    payload = {'version':'s4_r3_mock_claim_v1', 'kind':'synthetic_test_only', 'plan_sha256':plan_hash,
               'fixture_sha256':canonical_hash(fixture), 'output':str(output.resolve())}
    try:
        fd = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('mock plan already claimed') from None
    with os.fdopen(fd, 'wb') as stream:
        stream.write((json.dumps(payload, ensure_ascii=False, separators=(',', ':'))+'\n').encode('utf-8'))
        stream.flush()
        os.fsync(stream.fileno())
    # A failed/interrupted mock run leaves the claim in place and is not retried.
    schedule = plan['schedule']['plan']
    return run(output, bundle_root, scripts_path, schedule['cases'], schedule['repetitions'], plan['policy'])


def start_real(*args, **kwargs):
    raise PermissionError('real execution is disabled; no credential or provider access exists here')
