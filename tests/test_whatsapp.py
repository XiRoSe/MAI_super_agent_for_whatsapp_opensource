"""WhatsApp adapter tests: signature, inbound parsing, route-by-sender isolation."""

from __future__ import annotations

import hashlib
import hmac

import backend.whatsapp.router as router_mod
from backend.config import settings
from backend.db.tenancy import SubscriptionStatus
from backend.whatsapp.meta import MetaWhatsAppProvider
from backend.whatsapp.provider import InboundMessage


def _payload(from_number: str, text: str) -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": from_number,
                                    "id": "wamid.123",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }


def test_signature_validation():
    settings.whatsapp_app_secret = "topsecret"
    try:
        p = MetaWhatsAppProvider()
        body = b'{"hello":"world"}'
        good = "sha256=" + hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
        assert p.verify_signature(body, good) is True
        assert p.verify_signature(body, "sha256=deadbeef") is False
        assert p.verify_signature(body, None) is False
    finally:
        settings.whatsapp_app_secret = ""


def test_parse_inbound_extracts_text():
    p = MetaWhatsAppProvider()
    msgs = p.parse_inbound(_payload("15551112222", "hi mai"))
    assert len(msgs) == 1
    assert msgs[0].text == "hi mai"
    assert msgs[0].from_number == "+15551112222"  # normalized


def _account_with_session(session, session_id="s1", status=SubscriptionStatus.active):
    from backend.db.tenancy import WhatsAppConnection, WhatsAppConnectionStatus
    from backend.provisioning import create_account

    acc = create_account(session, status=status)
    session.add(WhatsAppConnection(account_id=acc.id, session_id=session_id,
                                   status=WhatsAppConnectionStatus.connected))
    session.commit()
    return acc


def _patch_router(monkeypatch):
    sent, ran = [], []
    monkeypatch.setattr(router_mod, "respond", lambda user, text, **kw: ran.append(text) or "ok reply")
    monkeypatch.setattr(router_mod, "_send_to_chat",
                        lambda sess, user, msg, text, device_id=None: sent.append((user.id, text)))
    monkeypatch.setattr(router_mod, "_chat_context", lambda msg, device_id=None, **kw: "")
    return sent, ran


def test_mai_mention_from_owner_triggers(session, monkeypatch):
    _account_with_session(session)
    sent, ran = _patch_router(monkeypatch)
    router_mod.handle_inbound_messages(session, [
        InboundMessage(from_number="+15551110000", text="@mai summarize this chat",
                       message_id="a", session_id="s1", chat_id="+15559990000", from_me=True),
    ])
    assert ran == ["summarize this chat"]   # @mai stripped, command passed to agent
    assert len(sent) == 1 and sent[0][1] == "ok reply"


def test_non_mention_is_ignored(session, monkeypatch):
    _account_with_session(session)
    sent, ran = _patch_router(monkeypatch)
    router_mod.handle_inbound_messages(session, [
        InboundMessage(from_number="+15551110000", text="just a normal message",
                       message_id="a", session_id="s1", from_me=True),
    ])
    assert ran == [] and sent == []  # no @mai -> Mai stays silent


def test_contact_mention_is_ignored(session, monkeypatch):
    """A contact tagging @mai must NOT trigger the owner's Mai."""
    _account_with_session(session)
    sent, ran = _patch_router(monkeypatch)
    router_mod.handle_inbound_messages(session, [
        InboundMessage(from_number="+15558887777", text="@mai do something",
                       message_id="a", session_id="s1", from_me=False),
    ])
    assert ran == [] and sent == []  # only the owner's own @mai triggers


def test_unentitled_owner_gets_notice_not_agent(session, monkeypatch):
    _account_with_session(session, status=SubscriptionStatus.canceled)
    sent, ran = _patch_router(monkeypatch)
    router_mod.handle_inbound_messages(session, [
        InboundMessage(from_number="+15551110000", text="@mai hi",
                       message_id="a", session_id="s1", chat_id="+1", from_me=True),
    ])
    assert ran == []  # no model spend for non-payers
    assert len(sent) == 1 and "paused" in sent[0][1]
