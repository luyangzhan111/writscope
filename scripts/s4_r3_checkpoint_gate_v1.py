"""One-shot offline-only entry for the checkpointed synthetic runtime."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from scripts.s4_r3_checkpoint_runtime_v1 import prepare, run
from writscope.artifacts import canonical_hash


def start(plan, fixture, claim_directory, output, bundle_root, scripts_path, *, now=None):
    if not isinstance(plan, dict) or not isinstance(fixture, dict):
        raise ValueError('synthetic plan and fixture required')
    schedule = plan['schedule']['plan']
    if plan != prepare(bundle_root, scripts_path, schedule['cases'], schedule['repetitions'], plan['policy']):
        raise ValueError('checkpoint plan or source mismatch')
    if (set(fixture) != {'kind','plan_sha256','expires_at','recipient','materials','cap_micro','max_attempts'} or
            fixture['kind'] != 'synthetic_test_fixture' or fixture['plan_sha256'] != canonical_hash(plan) or
            fixture['recipient'] != plan['recipient'] or fixture['materials'] != plan['materials'] or
            type(fixture['cap_micro']) is not int or fixture['cap_micro'] != plan['policy']['cap_micro'] or
            type(fixture['max_attempts']) is not int or fixture['max_attempts'] != plan['policy']['max_attempts']):
        raise ValueError('synthetic fixture scope mismatch')
    try:
        expires = datetime.fromisoformat(fixture['expires_at'])
        current = now if now is not None else datetime.now(timezone.utc)
        if expires.tzinfo is None or current.tzinfo is None or expires <= current:
            raise ValueError('expired')
    except (ValueError, TypeError):
        raise ValueError('synthetic fixture expiry invalid or expired') from None
    claim_directory, output = Path(claim_directory), Path(output)
    if (claim_directory.is_symlink() or not claim_directory.is_dir() or output.exists() or
            output.resolve().is_relative_to(Path(bundle_root).resolve())):
        raise ValueError('invalid synthetic claim or output directory')
    plan_hash = canonical_hash(plan)
    path = claim_directory/(plan_hash+'.checkpoint-mock-claim.json')
    try:
        fd = os.open(str(path), os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('synthetic plan already claimed') from None
    with os.fdopen(fd,'wb') as file:
        file.write((json.dumps({'kind':'synthetic_only','plan_sha256':plan_hash,
                                'fixture_sha256':canonical_hash(fixture)},separators=(',',':'))+'\n').encode())
        file.flush()
        os.fsync(file.fileno())
    return run(plan, output, bundle_root, scripts_path)


def start_real(*args, **kwargs):
    raise PermissionError('real execution disabled')
