"""Per-chat session isolation + participant roster in context."""

from __future__ import annotations

import backend.whatsapp.router as r
from backend.agent.mai_agent import default_session_id, wa_session_id
from backend.whatsapp.provider import InboundMessage


class _U:
    agno_user_id = "u-7"


def test_per_chat_sessions_are_distinct():
    u = _U()
    a = wa_session_id(u, "123@g.us")
    b = wa_session_id(u, "456@g.us")
    assert a != b                          # different chats -> different sessions
    assert a != default_session_id(u)      # WhatsApp chat != the web/main session
    assert wa_session_id(u, None) == wa_session_id(u, None)  # stable for DMs


def test_participants_group_is_lightweight():
    """Group roster is NOT preloaded every turn (slow) — Mai fetches on demand."""
    msg = InboundMessage(from_number="+1", text="x", message_id="m", chat_id="120@g.us")
    out = r._participants(object(), "dev1", msg, "Work", True, [])
    assert "Work" in out and "list_participants" in out


def test_participants_direct_uses_saved_name():
    msg = InboundMessage(from_number="+97250", text="x", message_id="m", chat_id="+97259999")
    out = r._participants(object(), "dev1", msg, "Dotan Menachem", False, [])
    assert "Dotan Menachem" in out and "97259999" in out


def test_direct_name_falls_back_to_recent_sender():
    msg = InboundMessage(from_number="+97250", text="x", message_id="m", chat_id="+97259999")
    msgs = [{"from_me": True, "sender": "Me", "text": "hi"},
            {"from_me": False, "sender": "Dotan Menachem", "text": "yo"}]
    out = r._participants(object(), "dev1", msg, None, False, msgs)  # no saved name
    assert "Dotan Menachem" in out


def test_direct_name_falls_back_to_user_info():
    class P:
        def get_user_info(self, phone, *, session_id=None):
            return {"verified_name": "Dotan M."}

    msg = InboundMessage(from_number="+97250", text="x", message_id="m", chat_id="+97259999")
    out = r._participants(P(), "dev1", msg, None, False, [])  # no name, no messages
    assert "Dotan M." in out


# --- Owner-mislabel rejection (GOWA returns the owner's own display name for DMs) ---

_OWNER_NUMBER = "+972500000000"   # the account owner's WhatsApp number
_OWNER_LABEL = "Alex Cohen"     # the fuller label the GATEWAY reports for the owner


class _MislabelProvider:
    """GOWA-like gateway: stamps the OWNER's own label on DMs it has no contact name
    for, but reports genuine names for properly-saved contacts. get_user_info ALSO
    re-introduces the owner label for the mislabeled numbers."""

    def list_chats(self, device_id, *, limit=100):
        return [
            # The owner's OWN number, labeled with the owner's name (the source of truth).
            {"jid": _OWNER_NUMBER, "name": _OWNER_LABEL, "is_group": False},
            # A friend's DM — mislabeled by the gateway with the owner's name.
            {"jid": "+972511111111", "name": _OWNER_LABEL, "is_group": False},
            # A correctly-named contact — must be preserved.
            {"jid": "+972522222222", "name": "Bob", "is_group": False},
        ]

    def get_user_info(self, phone, *, session_id=None):
        return {"name": _OWNER_LABEL}  # gateway re-introduces the owner label here too


def test_mislabeled_dm_resolves_to_number_not_owner_name():
    """A DM the gateway wrongly labels with the owner's name falls back to the bare
    number — Mai must NOT think it's talking to the owner."""
    msg = InboundMessage(from_number="+1", text="x", message_id="m", chat_id="+972511111111")
    name, is_group, owner_label = r._chat_label(
        _MislabelProvider(), "dev1", msg.chat_id, owner_number=_OWNER_NUMBER)
    assert owner_label == _OWNER_LABEL and not is_group
    out = r._participants(_MislabelProvider(), "dev1", msg, name, is_group, [],
                          owner_label, _OWNER_NUMBER)
    assert _OWNER_LABEL not in out and "972511111111" in out


def test_correctly_named_dm_keeps_real_name():
    """A genuinely-named contact (differs from the owner label) is preserved."""
    msg = InboundMessage(from_number="+1", text="x", message_id="m", chat_id="+972522222222")
    name, is_group, owner_label = r._chat_label(
        _MislabelProvider(), "dev1", msg.chat_id, owner_number=_OWNER_NUMBER)
    assert name == "Bob"
    out = r._participants(_MislabelProvider(), "dev1", msg, name, is_group, [],
                          owner_label, _OWNER_NUMBER)
    assert "Bob" in out


def test_owner_self_chat_keeps_owner_name():
    """The owner's OWN self-chat legitimately carries the owner label — don't strip it."""
    msg = InboundMessage(from_number="+1", text="x", message_id="m", chat_id=_OWNER_NUMBER)
    name, is_group, owner_label = r._chat_label(
        _MislabelProvider(), "dev1", msg.chat_id, owner_number=_OWNER_NUMBER)
    out = r._participants(_MislabelProvider(), "dev1", msg, name, is_group, [],
                          owner_label, _OWNER_NUMBER)
    assert _OWNER_LABEL in out


def test_chat_context_header_drops_owner_mislabel(monkeypatch):
    """End-to-end through _chat_context: the header says 'direct chat' (no owner name)
    for a mislabeled DM, and the roster resolves to the number."""
    import backend.whatsapp as wa_pkg
    import backend.whatsapp.router as router_mod

    monkeypatch.setattr(wa_pkg, "get_provider", lambda: _MislabelProvider())
    msg = InboundMessage(from_number="+1", text="@Mai hi", message_id="m",
                         chat_id="+972511111111")
    out = router_mod._chat_context(msg, "dev1", limit=5, owner_number=_OWNER_NUMBER)
    assert _OWNER_LABEL not in out and "a direct chat" in out and "972511111111" in out


# --- Contact name resolved from the owner's saved long-term MEMORY -------------

class _Mem:
    def __init__(self, memory):
        self.memory = memory


class _MemDB:
    def __init__(self, mems):
        self._mems = mems

    def get_user_memories(self, *, user_id):
        return self._mems


def _wire_memory(monkeypatch, mems):
    monkeypatch.setattr("backend.db.agno_db.get_agno_db", lambda: _MemDB(mems))


def test_contact_name_from_memory_matches_number(monkeypatch):
    _wire_memory(monkeypatch, [
        _Mem("Dana Levi is Alex's cousin; her WhatsApp number is +972540000029."),
        _Mem("Likes pineapple on pizza"),
    ])
    assert r._contact_name_from_memory("u-7", "+972540000029") == "Dana Levi"


def test_contact_name_from_memory_non_match_returns_none(monkeypatch):
    _wire_memory(monkeypatch, [
        _Mem("Dana Levi is Alex's cousin; her WhatsApp number is +972540000029."),
    ])
    assert r._contact_name_from_memory("u-7", "+972500000001") is None


def test_contact_name_from_memory_normalizes_prefixes(monkeypatch):
    """Last-9-digit match: a 0-prefixed local number resolves a +972 memory and vice-versa."""
    _wire_memory(monkeypatch, [
        _Mem("Dana Levi is Alex's cousin; her WhatsApp number is +972540000029."),
    ])
    assert r._contact_name_from_memory("u-7", "0540000029") == "Dana Levi"
    _wire_memory(monkeypatch, [_Mem("Bob — call him on 0540000029")])
    assert r._contact_name_from_memory("u-7", "+972540000029") == "Bob"


def test_contact_name_from_memory_db_hiccup_returns_none(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("backend.db.agno_db.get_agno_db", _boom)
    assert r._contact_name_from_memory("u-7", "+972540000029") is None


def test_direct_1to1_names_both_owner_and_memory_contact(monkeypatch):
    """1:1 line names YOU (owner + number) AND the contact, resolved from memory."""
    _wire_memory(monkeypatch, [
        _Mem("Dana Levi is Alex's cousin; her WhatsApp number is +972540000029."),
    ])
    msg = InboundMessage(from_number="+1", text="x", message_id="m", chat_id="+972540000029")
    out = r._participants(_MislabelProvider(), "dev1", msg, _OWNER_LABEL, False, [],
                          _OWNER_LABEL, _OWNER_NUMBER, "Alex Cohen", "u-7")
    assert "YOU (Alex Cohen, +972500000000)" in out
    assert "Dana Levi" in out and "972540000029" in out
    assert "between two people" in out


def test_direct_1to1_falls_back_to_number_without_memory(monkeypatch):
    """No memory hit -> contact shown as the bare number, owner still named."""
    _wire_memory(monkeypatch, [_Mem("Likes pineapple on pizza")])
    msg = InboundMessage(from_number="+1", text="x", message_id="m", chat_id="+972511111111")
    out = r._participants(_MislabelProvider(), "dev1", msg, _OWNER_LABEL, False, [],
                          _OWNER_LABEL, _OWNER_NUMBER, "Alex Cohen", "u-7")
    assert "YOU (Alex Cohen, +972500000000)" in out and "972511111111" in out
    # The owner-mislabel must NOT leak in as the contact's name (named only as YOU).
    assert out.count("Alex Cohen") == 1
