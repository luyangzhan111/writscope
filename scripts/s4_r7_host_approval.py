"""Host event adapter contract. No installed host reader and no live launch path.

The reader must be injected by the trusted application, never by a run artifact.
Fixture readers exercise identical validation but cannot produce real approval.
"""
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
import json

from writscope.artifacts import canonical_hash, digest


@dataclass(frozen=True)
class HostEvent:
    tenant: str
    conversation: str
    event_id: str
    actor: str
    role: str
    text: str
    revision: str
    issued_at: str
    expires_at: str
    revoked: bool


def authorization_text(scope):
    """Concrete machine-bindable execution statement; design consent cannot match."""
    required = {'plan_sha256', 'endpoint', 'materials_sha256', 'cap_cny_micro', 'max_attempts'}
    if (set(scope) != required or scope['endpoint'] != 'https://api.deepseek.com/chat/completions' or
            type(scope['cap_cny_micro']) is not int or scope['cap_cny_micro'] <= 0 or
            type(scope['max_attempts']) is not int or scope['max_attempts'] <= 0 or
            any(type(scope[k]) is not str or len(scope[k]) != 64 or
                any(c not in '0123456789abcdef' for c in scope[k]) for k in ('plan_sha256', 'materials_sha256'))):
        raise ValueError('invalid exact execution scope')
    return '我批准此单轮执行、所列材料外发和本地费用额度：\n' + json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class HostApprovalAdapter:
    def __init__(self, reader, *, tenant, conversation, actor, fixture_only=True):
        if not fixture_only:
            raise PermissionError('authenticated production host integration not installed')
        self.reader, self.tenant, self.conversation, self.actor = reader, tenant, conversation, actor

    def resolve(self, event_id, scope, now, *, previous=None):
        # Reader is called on every admission; saved receipts never grant authority.
        event = self.reader(event_id)
        if (type(event) is not HostEvent or event.tenant != self.tenant or event.conversation != self.conversation or
                event.actor != self.actor or event.event_id != event_id or event.role != 'user' or
                event.revoked is not False or not event.revision or event.text != authorization_text(scope)):
            raise PermissionError('host event identity, revocation or explicit scope mismatch')
        try:
            issued, expiry = datetime.fromisoformat(event.issued_at), datetime.fromisoformat(event.expires_at)
            if now.tzinfo is None or issued.tzinfo is None or expiry.tzinfo is None or not issued <= now < expiry:
                raise ValueError('expired')
        except (ValueError, TypeError):
            raise PermissionError('host approval time window invalid') from None
        receipt = {'event_id': event_id, 'revision': event.revision, 'tenant': event.tenant,
                   'conversation': event.conversation, 'actor': event.actor, 'text': event.text,
                   'text_sha256': digest(event.text.encode()), 'scope_sha256': canonical_hash(scope),
                   'issued_at': event.issued_at, 'expires_at': event.expires_at,
                   'source': 'injected_host_reader_test_fixture', 'actual_execution_approval': False}
        if previous is not None and previous != receipt:
            raise PermissionError('approval revision changed; new review required')
        return receipt


def production_reader(event_id):
    raise PermissionError('no authenticated desktop user-event API installed; no file fallback')


def scope_for(plan):
    return {'plan_sha256': canonical_hash(plan), 'endpoint': 'https://api.deepseek.com/chat/completions',
            'materials_sha256': canonical_hash({'materials': plan.get('materials'), 'public_binding': plan.get('base')}),
            'cap_cny_micro': 120000000, 'max_attempts': plan.get('base', {}).get('policy', {}).get('max_attempts', 1080)}


def fixture_source(plan):
    event = HostEvent('offline-test', 'offline-rehearsal', 'fixed-user-event', 'synthetic-owner', 'user',
                      authorization_text(scope_for(plan)), '1', '2026-11-01T00:00:00+00:00',
                      '2026-11-03T00:00:00+00:00', False)
    return HostApprovalAdapter(lambda _: event, tenant='offline-test', conversation='offline-rehearsal', actor='synthetic-owner')


def check_fixture_receipt(receipt, plan):
    expected = fixture_source(plan).resolve('fixed-user-event', scope_for(plan), datetime(2026, 11, 2, tzinfo=timezone.utc))
    if receipt != expected:
        raise PermissionError('saved test approval binding mismatch; hashes do not authenticate a human')


def start_live(*, credential_reader=None, transport_factory=None, approval_reader=None, **kwargs):
    # Close before ANY callback, including approval source; no accidental key read.
    raise PermissionError('R7 real execution disabled; production sources and specific authorization pending')
