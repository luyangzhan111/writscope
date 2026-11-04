"""Host-signed per-request observations. Only public verification; no live entry.

Extends v2 envelopes to bind request/job/stage/phase/ordinal in the signature.
Reuses the frozen RSA verifier and strict JSON parser. No production root ships.
"""
from datetime import timedelta
import math
import secrets
import time

from scripts.s4_r7_approval_source_v2 import (HostTrust, canonical_bytes, strict_json,
                                           verify_signature, stamp)
from scripts.s4_r7_host_approval import HostEvent, authorization_text
from writscope.artifacts import canonical_hash, digest


def check_context(context):
    if (type(context) is not dict or set(context) != {'job','stage','request_sha256','is_continuation','ordinal','phase'} or
            context['phase'] not in ('reserve','before_send') or
            type(context['is_continuation']) is not bool or type(context['ordinal']) is not int or context['ordinal'] <= 0 or
            type(context['stage']) is not str or not context['stage'] or
            any(type(context[k]) is not str or len(context[k]) != 64 or
                any(c not in '0123456789abcdef' for c in context[k]) for k in ('job','request_sha256'))):
        raise PermissionError('invalid signed request context')


def verify_observation(trust, scope, event_id, proof, context):
    """Static archive validation. Does not re-query or confer current authority."""
    check_context(context)
    if type(proof) is not dict or set(proof) != {'source_id','anchor_sha256','event','scope_sha256',
            'grant_sha256','observation','observation_sha256','source','deployment','actual_execution_approval',
            'checked_started_at','checked_finished_at','channel_elapsed_ms'}:
        raise PermissionError('bound approval receipt fields invalid')
    envelope = proof['observation']
    if type(envelope) is not dict or set(envelope) != {'payload','signature'}:
        raise PermissionError('bound signature envelope invalid')
    payload = envelope['payload']
    if type(payload) is not dict or set(payload) != {'version','source_id','challenge','scope_sha256',
                                                   'observed_at','counter','event','context'}:
        raise PermissionError('bound observation fields invalid')
    verify_signature(trust, canonical_bytes(payload), envelope['signature'])
    challenge = payload['challenge']
    if (payload['version'] != 's4_host_bound_observation_v3' or payload['source_id'] != trust.source_id or
            payload['context'] != context or payload['scope_sha256'] != canonical_hash(scope) or
            type(payload['counter']) is not int or payload['counter'] <= 0 or
            type(challenge) is not str or len(challenge) != 64 or any(c not in '0123456789abcdef' for c in challenge)):
        raise PermissionError('signed phase/request/scope binding mismatch')
    raw = payload['event']
    if type(raw) is not dict or set(raw) != set(HostEvent.__dataclass_fields__):
        raise PermissionError('raw user event required')
    event = HostEvent(**raw)
    observed = stamp(payload['observed_at'])
    started, finished = stamp(proof['checked_started_at']), stamp(proof['checked_finished_at'])
    elapsed = proof['channel_elapsed_ms']
    if (event.tenant != trust.tenant or event.conversation != trust.conversation or event.actor != trust.actor or
            event.event_id != event_id or event.role != 'user' or event.revoked is not False or
            type(event.revision) is not str or not event.revision or event.text != authorization_text(scope) or
            not stamp(event.issued_at) <= observed <= finished < stamp(event.expires_at) or
            not started-timedelta(seconds=10) <= observed or not started <= finished <= started+timedelta(seconds=2) or
            type(elapsed) not in (int,float) or not math.isfinite(elapsed) or not 0 <= elapsed <= 2000):
        raise PermissionError('signed event, revocation or observation time invalid')
    grant = {'source_id': trust.source_id, 'anchor_sha256': trust.fingerprint,
             'event': raw, 'scope_sha256': canonical_hash(scope)}
    if (any(proof[k] != v for k,v in grant.items()) or proof['grant_sha256'] != canonical_hash(grant) or
            proof['observation_sha256'] != digest(canonical_bytes(envelope)) or
            proof['source'] != 'signed_host_event_v3_bound_context' or proof['deployment'] != trust.deployment or
            proof['actual_execution_approval'] is not (trust.deployment == 'production_host_provisioned')):
        raise PermissionError('bound grant audit fields invalid')
    return payload


class BoundHostSource:
    def __init__(self, trust, exchange, clock, *, monotonic=time.monotonic):
        if type(trust) is not HostTrust or not all(callable(f) for f in (exchange,clock,monotonic)):
            raise PermissionError('trusted host binding required')
        self.trust, self.exchange, self.clock, self.monotonic = trust, exchange, clock, monotonic
        self.context, self._counter = None, 0

    def resolve(self, event_id, scope, now, *, previous=None):
        check_context(self.context)
        started, tick = self.clock(), self.monotonic()
        challenge = secrets.token_hex(32)
        query = {'version':'s4_host_bound_query_v3','event_id':event_id,'scope_sha256':canonical_hash(scope),
                 'challenge':challenge,'context':dict(self.context)}
        envelope = strict_json(self.exchange(query))
        finished, elapsed = self.clock(), (self.monotonic()-tick)*1000
        # Syntax validation below handles malformed envelopes without trusting
        # role/identity booleans or allowing a response to replace the trust root.
        if type(envelope) is not dict or type(envelope.get('payload')) is not dict:
            raise PermissionError('bound host envelope missing')
        payload = envelope['payload']
        grant = {'source_id':self.trust.source_id,'anchor_sha256':self.trust.fingerprint,
                 'event':payload.get('event'),'scope_sha256':canonical_hash(scope)}
        proof = {**grant,'grant_sha256':canonical_hash(grant),'observation':envelope,
                 'observation_sha256':digest(canonical_bytes(envelope)),'source':'signed_host_event_v3_bound_context',
                 'deployment':self.trust.deployment,'actual_execution_approval':self.trust.deployment == 'production_host_provisioned',
                 'checked_started_at':started.isoformat(),'checked_finished_at':finished.isoformat(),'channel_elapsed_ms':elapsed}
        verified = verify_observation(self.trust, scope, event_id, proof, self.context)
        if verified['challenge'] != challenge or verified['counter'] <= self._counter:
            raise PermissionError('host observation replayed')
        if previous is not None and proof['grant_sha256'] != previous.get('grant_sha256'):
            raise PermissionError('host grant revision changed')
        self._counter = verified['counter']
        return proof


def installed_production_source():
    raise PermissionError('authenticated host channel/trust anchor not installed; live remains closed')
