"""Per-tenant WhatsApp routing under the @mai model: session -> account, isolated."""

from __future__ import annotations

import backend.whatsapp.router as router_mod
from backend.db.tenancy import User, WhatsAppConnection, WhatsAppConnectionStatus
from backend.provisioning import (
    get_or_create_primary_user,
    get_or_create_user_in_account,
    resolve_account_by_session,
)
from backend.whatsapp.provider import InboundMessage


def _connect(session, account, session_id):
    session.add(WhatsAppConnection(account_id=account.id, session_id=session_id,
                                   status=WhatsAppConnectionStatus.connected))
    session.commit()


def _patch(monkeypatch):
    sent = []
    monkeypatch.setattr(router_mod, "respond", lambda user, text, **kw: "ok")
    monkeypatch.setattr(router_mod, "_send_to_chat",
                        lambda sess, user, msg, text, device_id=None: sent.append(user.id))
    monkeypatch.setattr(router_mod, "_chat_context", lambda msg, device_id=None, **kw: "")
    return sent


def _mai_msg(number, session_id):
    # Unique message id per (session, number) — real WhatsApp ids are unique.
    return InboundMessage(from_number=number, text="@mai hi", message_id=f"mid-{session_id}-{number}",
                          session_id=session_id, chat_id=number, from_me=True)


def test_resolve_account_by_session(session, two_tenants):
    a1, u1, a2, u2 = two_tenants
    _connect(session, a1, "sess-a1")
    assert resolve_account_by_session(session, "sess-a1").id == a1.id
    assert resolve_account_by_session(session, "nope") is None


def test_placeholder_owner_reconciled_on_first_inbound(session):
    from backend.provisioning import create_account

    acc = create_account(session)
    session.commit()
    _connect(session, acc, "sess-fresh")
    primary = get_or_create_primary_user(session, acc)
    assert primary.whatsapp_number.startswith("pending:")
    user, _ = get_or_create_user_in_account(session, account=acc, whatsapp_number="+15551112222")
    assert user.id == primary.id
    assert user.whatsapp_number == "+15551112222"


def test_mai_message_routes_to_session_account(session, two_tenants, monkeypatch):
    a1, u1, a2, u2 = two_tenants
    _connect(session, a1, "sess-a1")
    sent = _patch(monkeypatch)
    # A brand-new sender number, but it's the owner messaging their own Mai number.
    router_mod.handle_inbound_messages(session, [_mai_msg("+15557778888", "sess-a1")])
    assert len(sent) == 1
    routed = session.query(User).filter(User.whatsapp_number == "+15557778888").first()
    assert routed is not None and routed.account_id == a1.id


def test_unknown_session_dropped(session, two_tenants, monkeypatch):
    sent = _patch(monkeypatch)
    router_mod.handle_inbound_messages(session, [_mai_msg("+15550000001", "ghost-session")])
    assert sent == []


def test_two_sessions_isolated(session, two_tenants, monkeypatch):
    a1, u1, a2, u2 = two_tenants
    _connect(session, a1, "sess-a1")
    _connect(session, a2, "sess-a2")
    _patch(monkeypatch)
    router_mod.handle_inbound_messages(session, [_mai_msg("+100", "sess-a1")])
    router_mod.handle_inbound_messages(session, [_mai_msg("+100", "sess-a2")])
    users = session.query(User).filter(User.whatsapp_number == "+100").all()
    # Same sender number, two sessions -> two users in two accounts.
    accounts = {u.account_id for u in users}
    assert accounts == {a1.id, a2.id}
