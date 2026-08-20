"""WhatsApp dedup + self-echo loop guard."""

from __future__ import annotations

import backend.whatsapp.router as router_mod
from backend.db.tenancy import WhatsAppConnection, WhatsAppConnectionStatus
from backend.provisioning import create_account
from backend.whatsapp.dedupe import claim, seen
from backend.whatsapp.provider import InboundMessage


def test_claim_is_once_only(session):
    assert claim(session, "msg:abc") is True
    assert claim(session, "msg:abc") is False  # second time -> already seen
    assert seen(session, "msg:abc") is True


def _setup(session):
    acc = create_account(session)
    session.add(WhatsAppConnection(account_id=acc.id, session_id="mai_acct_x",
                                   status=WhatsAppConnectionStatus.connected))
    session.commit()
    return acc


def _patch(monkeypatch, reply="ok reply"):
    calls = {"respond": 0, "sent": []}

    def fake_respond(user, text, **kw):
        calls["respond"] += 1
        return reply

    monkeypatch.setattr(router_mod, "respond", fake_respond)
    monkeypatch.setattr(router_mod, "_chat_context", lambda msg, device_id=None, **kw: "")

    def fake_send(session_, user, msg, text, device_id):
        calls["sent"].append(text)
        return "REPLYID1"

    monkeypatch.setattr(router_mod, "_send_to_chat", fake_send)
    return calls


def test_duplicate_delivery_processed_once(session, monkeypatch):
    _setup(session)
    calls = _patch(monkeypatch)
    m = InboundMessage(from_number="+9725", text="@mai hi", message_id="MID1",
                       session_id="mai_acct_x", chat_id="+9725", from_me=True)
    router_mod.handle_inbound_messages(session, [m])
    router_mod.handle_inbound_messages(session, [m])  # delivered again, same id
    assert calls["respond"] == 1  # only handled once


def test_own_reply_echo_does_not_loop(session, monkeypatch):
    _setup(session)
    calls = _patch(monkeypatch, reply="here is the summary, from Mai")
    trigger = InboundMessage(from_number="+9725", text="@mai summarize", message_id="MID2",
                             session_id="mai_acct_x", chat_id="+9725", from_me=True)
    router_mod.handle_inbound_messages(session, [trigger])
    assert calls["respond"] == 1

    # The reply echoes back as a from_me message (and imagine it contains @mai).
    echo = InboundMessage(from_number="+9725", text="@mai here is the summary, from Mai",
                          message_id="REPLYID1", session_id="mai_acct_x",
                          chat_id="+9725", from_me=True)
    router_mod.handle_inbound_messages(session, [echo])
    assert calls["respond"] == 1  # echo skipped (same id pre-marked) -> no loop


def test_self_echo_by_text_hash(session, monkeypatch):
    _setup(session)
    # A reply that itself contains @mai (e.g. it quoted the conversation).
    reply = "Sure — I told the group you tagged @mai about this."
    calls = _patch(monkeypatch, reply=reply)
    router_mod.handle_inbound_messages(session, [
        InboundMessage(from_number="+9725", text="@mai summarize", message_id="A",
                       session_id="mai_acct_x", chat_id="+9725", from_me=True)])
    assert calls["respond"] == 1

    # The reply echoes back VERBATIM with a DIFFERENT id -> caught by text hash.
    router_mod.handle_inbound_messages(session, [
        InboundMessage(from_number="+9725", text=reply, message_id="DIFFERENT_ID",
                       session_id="mai_acct_x", chat_id="+9725", from_me=True)])
    assert calls["respond"] == 1  # echo skipped via reply-text hash, no loop
