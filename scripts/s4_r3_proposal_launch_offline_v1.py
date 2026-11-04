"""Bind a synthetic proposal to the existing one-shot mock launch; no live path."""
from pathlib import Path

from scripts import s4_r3_proposal_offline_v1 as proposal_module
from scripts.s4_r3_checkpoint_gate_v1 import start as start_checkpoint
from writscope.artifacts import canonical_hash, digest


def bind(proposal):
    return {'version':'s4_r3_synthetic_launch_v1','proposal':proposal,
            'proposal_validator_sha256':digest(Path(proposal_module.__file__).read_bytes()),
            'launcher_sha256':digest(Path(__file__).read_bytes()),
            'execution_kind':'offline_mock','real_transport_allowed':False}


def start(envelope, fixture, claim_directory, output, bundle_root, scripts_path, *, now=None):
    if (not isinstance(envelope,dict) or envelope != bind(envelope.get('proposal')) or
            not isinstance(fixture,dict) or set(fixture) != {'kind','envelope_sha256','checkpoint_fixture'} or
            fixture['kind'] != 'synthetic_launch_fixture' or fixture['envelope_sha256'] != canonical_hash(envelope)):
        raise ValueError('synthetic launch binding mismatch')
    proposal = envelope['proposal']
    checkpoint_plan = proposal['checkpoint_plan']
    proposal_module.validate(proposal,checkpoint_plan,expected_hash=canonical_hash(proposal),now=now)
    # The existing gate independently verifies current bytes, scope, expiry and claim before the mock runtime.
    return start_checkpoint(checkpoint_plan,fixture['checkpoint_fixture'],claim_directory,output,
                            bundle_root,scripts_path,now=now)


def start_real(*args, **kwargs):
    raise PermissionError('real execution disabled')
