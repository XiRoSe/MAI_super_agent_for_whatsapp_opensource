"""Delegated @mai access: channel grants, guest invocation, restricted toolset."""

from __future__ import annotations

import backend.whatsapp.router as router_mod
from backend.db.tenancy import SubscriptionStatus, WhatsAppConnection, WhatsAppConnectionStatus
from backend.provisioning import create_account
from backend.whatsapp.access import grant_chat, is_chat_granted, list_grants, revoke_chat
from backend.whatsapp.provider import InboundMessage


def _connect(session, account, session_id):
    session.add(WhatsAppConnection(account_id=account.id, session_id=session_id,
                                   status=WhatsAppConnectionStatus.connected))
    session.commit()


def test_grant_revoke_list(session):
    acc = create_account(session)
    session.commit()
    assert grant_chat(session, acc.id, "123@g.us", "Work") is True
    assert grant_chat(session, acc.id, "123@g.us") is False  # already open
    assert is_chat_granted(session, acc.id, "123@g.us") is True
    assert is_chat_granted(session, acc.id, "999@g.us") is False
    assert len(list_grants(session, acc.id)) == 1
    assert revoke_chat(session, acc.id, "123@g.us") is True
    assert is_chat_granted(session, acc.id, "123@g.us") is False


def _patch(monkeypatch):
    captured, sent = {}, []
    monkeypatch.setattr(router_mod, "respond", lambda user, text, **kw: captured.update(text=text) or "ok reply")
    monkeypatch.setattr(router_mod, "_send_to_chat",
                        lambda sess, user, msg, text, device_id=None: sent.append(text))
    monkeypatch.setattr(router_mod, "_chat_context", lambda msg, device_id=None, **kw: "")
    monkeypatch.setattr(router_mod, "_presence", lambda *a, **k: None)
    return captured, sent


def test_guest_in_granted_chat_triggers_with_privacy_note(session, monkeypatch):
    acc = create_account(session, status=SubscriptionStatus.active)
    session.commit()
    _connect(session, acc, "sess-g")
    grant_chat(session, acc.id, "group@g.us", "Group")
    captured, sent = _patch(monkeypatch)
    router_mod.handle_inbound_messages(session, [
        InboundMessage(from_number="+15558887777", text="@mai hi", message_id="g1",
                       session_id="sess-g", chat_id="group@g.us", from_me=False, from_name="Alex"),
    ])
    assert sent == ["ok reply"]
    assert "Alex" in captured["text"]
    assert "guest" in captured["text"].lower() and "private" in captured["text"].lower()


def test_guest_in_ungranted_chat_is_silent(session, monkeypatch):
    acc = create_account(session, status=SubscriptionStatus.active)
    session.commit()
    _connect(session, acc, "sess-u")
    captured, sent = _patch(monkeypatch)
    router_mod.handle_inbound_messages(session, [
        InboundMessage(from_number="+15558887777", text="@mai hi", message_id="u1",
                       session_id="sess-u", chat_id="ungranted@g.us", from_me=False, from_name="Alex"),
    ])
    assert sent == [] and "text" not in captured


def test_guests_do_not_get_owner_private_toolkits(session, two_tenants):
    """REVERSES the earlier "guests get the full toolset" choice (owner's call, 2026-07): a
    guest in a granted chat runs as the owner's assistant but must never reach the owner's
    private state — the registry enforces is_owner as a security boundary. Full guest-boundary
    coverage: tests/test_registry_guest.py."""
    from backend.tools.registry import build_tools
    from backend.tools.schedule_tools import ScheduleTools

    _, u1, _, _ = two_tenants
    owner = build_tools(u1, session, is_owner=True)
    guest = build_tools(u1, session, is_owner=False)
    assert any(isinstance(t, ScheduleTools) for t in owner)
    assert not any(isinstance(t, ScheduleTools) for t in guest)  # restricted again
