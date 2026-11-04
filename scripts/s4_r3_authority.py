"""Approval-source boundary. Records alone are never a source of authority.

The application supplies an authenticated source out of band. This repository
ships no production source; offline tests supply synthetic source adapters.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from writscope.artifacts import canonical_hash, digest


@dataclass(frozen=True)
class ApprovalEvidence:
    source: str
    event_id: str
    actor_id: str
    role: str
    text: str
    grants: dict
    issued_at: str
    expires_at: str
    synthetic: bool
    revoked: bool = False


class ApprovalSource(Protocol):
    def resolve(self, event_id: str) -> ApprovalEvidence: ...


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('timezone required')
    return parsed


def grants_for(plan):
    return {'plan_sha256':canonical_hash(plan),'recipient':plan['endpoint'],
            'materials':plan['materials'],'cap_virtual_micro':plan['base']['policy']['cap_micro'],
            'max_attempts':plan['base']['policy']['max_attempts'],'purpose':'offline_test_only'}


def verify(plan, event_id, source, expected_actor, now):
    if source is None or not isinstance(event_id,str) or not event_id or not expected_actor:
        raise PermissionError('authenticated approval source not configured')
    evidence = source.resolve(event_id)
    if (not isinstance(evidence,ApprovalEvidence) or evidence.event_id != event_id or
            evidence.role != 'user' or evidence.actor_id != expected_actor or not evidence.source or
            not evidence.text or evidence.revoked or evidence.synthetic is not True or
            evidence.grants != grants_for(plan)):
        raise PermissionError('approval evidence mismatch; only synthetic tests may launch')
    try:
        issued, expiry = timestamp(evidence.issued_at),timestamp(evidence.expires_at)
        if now.tzinfo is None or not issued <= now < expiry:
            raise ValueError('invalid interval')
    except (TypeError,ValueError):
        raise PermissionError('approval evidence expired or not yet valid') from None
    return {'source':evidence.source,'event_id':event_id,'actor_id':evidence.actor_id,
            'text':evidence.text,'text_sha256':digest(evidence.text.encode('utf-8')),
            'grants':evidence.grants,'issued_at':evidence.issued_at,'expires_at':evidence.expires_at,
            'synthetic':True,'warning':'test attestation, not actual user approval'}
