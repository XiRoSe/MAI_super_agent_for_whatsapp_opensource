"""SDK dispatch runs the real tool scoped to the token's tenant; cap enforced."""

from __future__ import annotations

import pytest

from backend.code_exec.dispatch import DispatchError, dispatch
from backend.code_exec.token_store import TokenContext


class FakeProvider:
    def __init__(self):
        self.sent = []

    def recent_chats(self, *, session_id=None, days=7, limit=100):
        return [{"jid": "120@g.us", "name": "Sofa", "is_group": True},
                {"jid": "+9725", "name": "Bob", "is_group": False}]

    def list_my_groups(self, *, session_id=None):
        return [{"jid": "120@g.us", "name": "Sofa", "is_group": True}]

    def get_chat_messages(self, dev, jid, *, limit=20):
        return [{"sender": "Bob", "text": "hi", "from_me": False, "media_type": None}]

    def send_text(self, to, text, *, session_id=None):
        self.sent.append((to, text, session_id))
        return "mid1"

    def get_avatar(self, phone, *, session_id=None):
        self.avatar_phone = phone
        return "https://pps.example/pic.jpg"


@pytest.fixture()
def ctx_and_session(session, two_tenants):
    _, u1, *_ = two_tenants
    ctx = TokenContext(account_id=u1.account_id, user_id=u1.id, device_id="dev1",
                       outbound_allowed=True, send_count=0, expires_at=9e18)
    return ctx, session


def test_find_chats_returns_structured(monkeypatch, ctx_and_session):
    ctx, session = ctx_and_session
    monkeypatch.setattr("backend.whatsapp.get_provider", lambda: FakeProvider())
    out = dispatch("find_chats", {"query": "sofa"}, ctx, session, token="t")
    assert any(c["name"] == "Sofa" for c in out)


def test_get_profile_picture_resolves_me_to_owner(monkeypatch, ctx_and_session):
    # "me" must resolve to the OWNER (from the token), not be passed literally.
    ctx, session = ctx_and_session
    fake = FakeProvider()
    monkeypatch.setattr("backend.whatsapp.get_provider", lambda: fake)
    out = dispatch("get_profile_picture", {"phone": "me"}, ctx, session, token="t")
    assert out == "https://pps.example/pic.jpg"
    assert fake.avatar_phone and fake.avatar_phone.lower() != "me"  # resolved to a real number


def test_unknown_fn_raises(ctx_and_session):
    ctx, session = ctx_and_session
    with pytest.raises(DispatchError):
        dispatch("rm_rf", {}, ctx, session, token="t")


def test_send_respects_cap(monkeypatch, ctx_and_session):
    ctx, session = ctx_and_session
    fake = FakeProvider()
    monkeypatch.setattr("backend.whatsapp.get_provider", lambda: fake)
    # token store says cap reached -> dispatch refuses
    import backend.code_exec.dispatch as dmod
    monkeypatch.setattr(dmod.token_store, "spend_send", lambda tok: False)
    with pytest.raises(DispatchError):
        dispatch("send_message", {"chat": "Sofa", "text": "hi"}, ctx, session, token="t")
    assert fake.sent == []  # never sent


def test_send_blocked_without_approval(monkeypatch, session, two_tenants):
    _, u1, *_ = two_tenants
    ctx = TokenContext(account_id=u1.account_id, user_id=u1.id, device_id="dev1",
                       outbound_allowed=False, send_count=0, expires_at=9e18)
    fake = FakeProvider()
    monkeypatch.setattr("backend.whatsapp.get_provider", lambda: fake)
    with pytest.raises(DispatchError):
        dispatch("send_message", {"chat": "Sofa", "text": "hi"}, ctx, session, token="t")
    assert fake.sent == []  # never sent
