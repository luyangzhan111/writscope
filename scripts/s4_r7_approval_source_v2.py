"""Public verification of a fresh, host-signed user event; no signing or live launch.

The trust anchor and channel MUST be injected by a trusted host outside run data.
This repository installs neither a production anchor nor a host event service.
Signatures authenticate that host's statement, not its event-import semantics.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import base64
import hashlib
import hmac
import json
import secrets

from scripts.s4_r7_host_approval import HostEvent, authorization_text
from writscope.artifacts import canonical_hash, digest


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def strict_json(raw):
    if type(raw) is not bytes or len(raw) > 32768:
        raise PermissionError('host response byte limit')
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('duplicate key')
            out[key] = value
        return out
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    except (ValueError, UnicodeError):
        raise PermissionError('invalid host response JSON') from None


@dataclass(frozen=True)
class HostTrust:
    """Public parameters provisioned out of band, never loaded from an artifact."""
    source_id: str
    tenant: str
    conversation: str
    actor: str
    modulus: int
    exponent: int = 65537
    deployment: str = 'test_only'

    def __post_init__(self):
        if (any(type(x) is not str or not x for x in
                (self.source_id, self.tenant, self.conversation, self.actor)) or
                type(self.modulus) is not int or not 2048 <= self.modulus.bit_length() <= 4096 or
                self.modulus % 2 != 1 or type(self.exponent) is not int or self.exponent != 65537 or
                self.deployment not in ('test_only', 'production_host_provisioned')):
            raise ValueError('unsupported host trust anchor')

    @property
    def fingerprint(self):
        return canonical_hash({'n': str(self.modulus), 'e': self.exponent})


def verify_signature(trust, message, encoded):
    """Strict RSASSA-PKCS1-v1_5/SHA256 verification (RFC 8017 8.2.2/9.2).

    Only a fixed SHA256 DigestInfo and full-length encoding are accepted. No
    private parameters, decryption, signing, BER variations or algorithm input.
    """
    if type(encoded) is not str:
        raise PermissionError('missing host signature')
    try:
        signature = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise PermissionError('invalid host signature encoding') from None
    length = (trust.modulus.bit_length()+7)//8
    if len(signature) != length or int.from_bytes(signature, 'big') >= trust.modulus:
        raise PermissionError('invalid host signature size or representative')
    info = bytes.fromhex('3031300d060960864801650304020105000420') + hashlib.sha256(message).digest()
    expected = b'\x00\x01' + b'\xff'*(length-len(info)-3) + b'\x00' + info
    recovered = pow(int.from_bytes(signature, 'big'), trust.exponent, trust.modulus).to_bytes(length, 'big')
    if not hmac.compare_digest(recovered, expected):
        raise PermissionError('host signature verification failed')


def stamp(text):
    try:
        value = datetime.fromisoformat(text)
        if value.tzinfo is None:
            raise ValueError('timezone required')
        return value
    except (TypeError, ValueError):
        raise PermissionError('invalid host timestamp') from None


class SignedHostSource:
    """A thin approval adapter, not an alternative method runner.

    exchange accepts only event/scope/challenge metadata, never case materials.
    It must return the latest raw user event, including edits and revocation.
    A fresh challenge is required at reserve AND immediately before send. The
    host/channel is not called by any live entry in this repository.
    """
    def __init__(self, trust, exchange):
        if type(trust) is not HostTrust or not callable(exchange):
            raise PermissionError('trusted host injection required')
        self.trust, self.exchange = trust, exchange
        self._counters = {}

    def resolve(self, event_id, scope, now, *, previous=None):
        statement = authorization_text(scope)
        if type(event_id) is not str or not event_id or now.tzinfo is None:
            raise PermissionError('event identity and aware clock required')
        challenge = secrets.token_hex(32)
        query = {'version': 's4_host_query_v2', 'event_id': event_id,
                 'scope_sha256': canonical_hash(scope), 'challenge': challenge}
        response = strict_json(self.exchange(dict(query)))
        if type(response) is not dict or set(response) != {'payload', 'signature'}:
            raise PermissionError('host envelope fields invalid')
        payload = response['payload']
        if type(payload) is not dict or set(payload) != {
                'version', 'source_id', 'challenge', 'scope_sha256', 'observed_at', 'counter', 'event'}:
            raise PermissionError('host payload fields invalid')
        verify_signature(self.trust, canonical_bytes(payload), response['signature'])
        if (payload['version'] != 's4_host_observation_v2' or payload['source_id'] != self.trust.source_id or
                payload['challenge'] != challenge or payload['scope_sha256'] != query['scope_sha256'] or
                type(payload['counter']) is not int or payload['counter'] <= self._counters.get(event_id, -1) or
                not now-timedelta(seconds=10) <= stamp(payload['observed_at']) <= now):
            raise PermissionError('stale, replayed or mismatched host observation')
        raw = payload['event']
        if type(raw) is not dict or set(raw) != set(HostEvent.__dataclass_fields__):
            raise PermissionError('raw user event required; summaries are insufficient')
        event = HostEvent(**raw)
        if (event.tenant != self.trust.tenant or event.conversation != self.trust.conversation or
                event.actor != self.trust.actor or event.event_id != event_id or event.role != 'user' or
                event.revoked is not False or type(event.revision) is not str or not event.revision or
                event.text != statement or not stamp(event.issued_at) <= now < stamp(event.expires_at)):
            raise PermissionError('user event identity, scope, time or revocation mismatch')
        grant = {'source_id': self.trust.source_id, 'anchor_sha256': self.trust.fingerprint,
                 'event': asdict(event), 'scope_sha256': canonical_hash(scope)}
        if previous is not None and previous.get('grant_sha256') != canonical_hash(grant):
            raise PermissionError('grant changed; new owner decision required')
        self._counters[event_id] = payload['counter']
        return {**grant, 'grant_sha256': canonical_hash(grant), 'observation': response,
                'observation_sha256': digest(canonical_bytes(response)),
                'source': 'signed_host_event_v2', 'deployment': self.trust.deployment,
                'actual_execution_approval': self.trust.deployment == 'production_host_provisioned'}


def installed_production_source():
    raise PermissionError('no independently provisioned host signer/channel/trust anchor installed')


def start_live(*args, **kwargs):
    raise PermissionError('release review only; live closed before all callbacks')
