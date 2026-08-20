"""Unofficial GOWA gateway provider tests (HTTP mocked)."""

from __future__ import annotations

import hashlib
import hmac
import time

import httpx
import pytest

import backend.whatsapp.gowa as gowa_mod
from backend.config import settings
from backend.whatsapp.gowa import GowaWhatsAppProvider, _jid_to_number, _number_to_jid
from backend.whatsapp.templates import Template


@pytest.fixture()
def gowa(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_gateway_url", "https://gw.example.com")
    monkeypatch.setattr(settings, "whatsapp_gateway_webhook_secret", "shh")
    return GowaWhatsAppProvider()


def test_jid_number_helpers():
    assert _jid_to_number("628123456@s.whatsapp.net") == "+628123456"
    assert _jid_to_number("628123:12@s.whatsapp.net") == "+628123"
    assert _number_to_jid("+628123456") == "628123456"


def test_signature_validation(gowa):
    body = b'{"x":1}'
    good = "sha256=" + hmac.new(b"shh", body, hashlib.sha256).hexdigest()
    assert gowa.verify_signature(body, good) is True
    assert gowa.verify_signature(body, "sha256=bad") is False
    assert gowa.verify_signature(body, None) is False


def test_parse_inbound_gowa_event_shape(gowa):
    # upsailor fork: {event, device_id, payload:{from, from_name, body}}
    payload = {
        "event": "message",
        "device_id": "mai_acct_5",
        "payload": {"id": "m1", "from": "628999@s.whatsapp.net", "from_name": "Bob", "body": "hi mai"},
    }
    msgs = gowa.parse_inbound(payload)
    assert len(msgs) == 1
    assert msgs[0].text == "hi mai"
    assert msgs[0].from_number == "+628999"
    assert msgs[0].session_id == "mai_acct_5"


def test_parse_inbound_skips_non_message_events(gowa):
    assert gowa.parse_inbound({"event": "message.ack", "payload": {}}) == []


def test_parse_inbound_includes_own_messages_with_flag(gowa):
    # Own (from_me) messages ARE parsed now — that's how the @Mai trigger arrives.
    own = {"event": "message", "device_id": "d",
           "payload": {"from": "628111@s.whatsapp.net", "body": "@Mai hi", "is_from_me": True}}
    msgs = gowa.parse_inbound(own)
    assert len(msgs) == 1 and msgs[0].from_me is True and msgs[0].text == "@Mai hi"


def test_send_text_builds_request(gowa, monkeypatch):
    captured = {}

    class FakeResp:
        status_code = 200
        text = "ok"

    def fake_request(method, url, **kw):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kw.get("json")
        captured["params"] = kw.get("params")
        return FakeResp()

    monkeypatch.setattr("backend.whatsapp.gowa.httpx.request", fake_request)
    gowa.send_text("+628123", "hello there", session_id="mai_acct_5")
    assert captured["method"] == "POST"
    assert captured["url"] == "https://gw.example.com/send/message"
    assert captured["params"] == {"device_id": "mai_acct_5"}
    assert captured["json"] == {"phone": "628123", "message": "hello there"}


def test_template_falls_back_to_text(gowa, monkeypatch):
    sent = {}
    monkeypatch.setattr(gowa, "send_text",
                        lambda to, text, session_id=None: sent.update(to=to, text=text, s=session_id))
    gowa.send_template("+628123", Template(name="t", language="en", variables=["body text"]),
                       session_id="s9")
    assert sent == {"to": "+628123", "text": "body text", "s": "s9"}


def test_provider_marked_unofficial(gowa):
    assert gowa.unofficial is True


def test_chat_jid_reconstruction():
    from backend.whatsapp.gowa import _chat_jid

    assert _chat_jid("+628123") == "628123@s.whatsapp.net"
    assert _chat_jid("12345@g.us") == "12345@g.us"          # group kept
    assert _chat_jid("628123@s.whatsapp.net") == "628123@s.whatsapp.net"


def test_media_public_url_builds_for_statics_path(gowa):
    # A local auto-downloaded path -> fetchable public gateway url.
    assert (gowa._media_public_url("statics/media/178-abc.ogg")
            == "https://gw.example.com/statics/media/178-abc.ogg")
    # Dict shape (video/image-with-caption) carrying a path.
    assert (gowa._media_public_url({"path": "statics/x.jpg", "caption": "hi"})
            == "https://gw.example.com/statics/x.jpg")


def test_media_public_url_none_for_mmg_or_empty(gowa):
    # Encrypted WhatsApp CDN url has no usable bytes -> None.
    assert gowa._media_public_url("https://mmg.whatsapp.net/d/f/abc.enc") is None
    assert gowa._media_public_url({"url": "https://mmg.whatsapp.net/x.enc"}) is None
    assert gowa._media_public_url("") is None
    assert gowa._media_public_url(None) is None


def test_parse_inbound_audio_sets_media_url(gowa):
    # Inbound audio arrives as a plain statics path string -> media_url is the public url.
    msgs = gowa.parse_inbound({"event": "message", "device_id": "d", "payload": {
        "from": "9725@s.whatsapp.net", "id": "m1", "body": "@mai transcribe",
        "audio": "statics/media/178-abc.ogg"}})
    assert len(msgs) == 1
    assert msgs[0].media_type == "audio"
    assert msgs[0].media_url == "https://gw.example.com/statics/media/178-abc.ogg"


def test_get_chat_messages_reads_from_store_not_gowa(gowa, monkeypatch):
    """get_chat_messages now reads the encrypted store — NOT GOWA HTTP.
    An unknown session_id returns [] without ever calling _request.
    Full round-trip is covered in test_read_path_from_encstore.py."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.db.base import Base

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, future=True)
    monkeypatch.setattr("backend.whatsapp.gowa.SessionLocal", Maker, raising=False)
    # _request must NOT be called; if it were, this lambda would surface the bug.
    monkeypatch.setattr(gowa, "_request",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("GOWA HTTP called")))
    result = gowa.get_chat_messages("no_such_session", "+628111", limit=20)
    assert result == []


# ---------------------------------------------------------------------------
# Chat-list TTL cache tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def clear_chats_caches():
    """Wipe module-level chat + contacts caches before and after each test that opts in."""
    gowa_mod._LIST_CHATS_CACHE.clear()
    gowa_mod._RECENT_CHATS_CACHE.clear()
    gowa_mod._CONTACTS_CACHE.clear()
    yield
    gowa_mod._LIST_CHATS_CACHE.clear()
    gowa_mod._RECENT_CHATS_CACHE.clear()
    gowa_mod._CONTACTS_CACHE.clear()


class _FakeChatResp:
    """Minimal httpx-response stub returned by the mocked httpx.request."""
    status_code = 200
    text = "ok"

    def json(self):
        return {"results": {"data": [
            {"jid": "628111@s.whatsapp.net", "name": "Alice"},
        ]}}


def test_list_chats_cache_hit_skips_request(gowa, monkeypatch, clear_chats_caches):
    """A second list_chats call within TTL must NOT call httpx.request again.
    live_contacts is stubbed here so only the /chats cache behaviour is under test."""
    call_count = 0

    def fake_req(method, url, **kw):
        nonlocal call_count
        call_count += 1
        return _FakeChatResp()

    monkeypatch.setattr("backend.whatsapp.gowa.httpx.request", fake_req)
    # Stub live_contacts so it doesn't add its own HTTP call to the count.
    monkeypatch.setattr(gowa, "live_contacts", lambda session_id: {})

    result1 = gowa.list_chats("dev1")
    result2 = gowa.list_chats("dev1")  # second call — must be served from cache

    assert call_count == 1, f"Expected 1 HTTP call, got {call_count}"
    assert result1 == result2
    assert result1[0]["jid"] == "628111@s.whatsapp.net"


def test_list_chats_timeout_falls_back_to_stale(gowa, monkeypatch, clear_chats_caches):
    """list_chats returns the previously cached list when _request times out."""
    stale = [{"jid": "628222@s.whatsapp.net", "name": "Bob", "is_group": False}]
    # Inject an already-expired cache entry (200 s ago > 90 s TTL).
    gowa_mod._LIST_CHATS_CACHE["dev2"] = (time.monotonic() - 200, stale)

    def raise_timeout(*a, **kw):
        raise httpx.ReadTimeout("read timeout")

    monkeypatch.setattr("backend.whatsapp.gowa.httpx.request", raise_timeout)

    # Must not raise; must return the stale list rather than [].
    result = gowa.list_chats("dev2")
    assert result == stale


def test_list_chats_timeout_empty_when_no_stale(gowa, monkeypatch, clear_chats_caches):
    """list_chats returns [] when _request errors and there is no prior cache."""
    def raise_timeout(*a, **kw):
        raise httpx.ReadTimeout("read timeout")

    monkeypatch.setattr("backend.whatsapp.gowa.httpx.request", raise_timeout)
    assert gowa.list_chats("no_such_device") == []


def test_recent_chats_cache_hit_skips_request(gowa, monkeypatch, clear_chats_caches):
    """A second recent_chats call within TTL must not re-fetch /chats from GOWA."""
    chats_calls = 0

    def fake_req(method, url, **kw):
        nonlocal chats_calls
        if "/chats" in url:
            chats_calls += 1
        return _FakeChatResp()

    monkeypatch.setattr("backend.whatsapp.gowa.httpx.request", fake_req)
    # list_my_groups also calls httpx; short-circuit it so only /chats counts.
    monkeypatch.setattr(gowa, "list_my_groups", lambda session_id=None: [])

    gowa.recent_chats(session_id="sess1", days=0)
    gowa.recent_chats(session_id="sess1", days=0)  # second call within TTL

    assert chats_calls == 1, f"Expected 1 /chats call, got {chats_calls}"


def test_recent_chats_timeout_falls_back_to_stale(gowa, monkeypatch, clear_chats_caches):
    """recent_chats returns the previously cached list when _request times out."""
    stale = [{"jid": "628444@s.whatsapp.net", "name": "Dave", "is_group": False, "ts": None}]
    gowa_mod._RECENT_CHATS_CACHE["sess2"] = (time.monotonic() - 200, stale)

    def raise_timeout(*a, **kw):
        raise httpx.ReadTimeout("read timeout")

    monkeypatch.setattr("backend.whatsapp.gowa.httpx.request", raise_timeout)

    result = gowa.recent_chats(session_id="sess2")
    assert result == stale


def test_recent_chats_timeout_empty_when_no_stale(gowa, monkeypatch, clear_chats_caches):
    """recent_chats returns [] when _request errors and there is no prior cache."""
    def raise_timeout(*a, **kw):
        raise httpx.ReadTimeout("read timeout")

    monkeypatch.setattr("backend.whatsapp.gowa.httpx.request", raise_timeout)
    assert gowa.recent_chats(session_id="no_sess") == []


# ---------------------------------------------------------------------------
# live_contacts + name-overlay tests
# ---------------------------------------------------------------------------

class _FakeContactsResp:
    """Stub response for GET /user/my/contacts with three contacts across name-key variants."""
    status_code = 200
    text = "ok"

    def json(self):
        return {"results": {"data": [
            {"jid": "628111@s.whatsapp.net", "full_name": "Alice Smith"},
            {"jid": "628222@s.whatsapp.net", "name": "Bob"},           # 'name' key
            {"jid": "628333@s.whatsapp.net", "push_name": "Charlie"},  # 'push_name' key
        ]}}


def test_live_contacts_parses_jid_name_map(gowa, monkeypatch, clear_chats_caches):
    """live_contacts returns a JID->name dict; each common name-key variant is handled."""
    monkeypatch.setattr("backend.whatsapp.gowa.httpx.request",
                        lambda *a, **kw: _FakeContactsResp())
    result = gowa.live_contacts("dev1")
    assert result == {
        "628111@s.whatsapp.net": "Alice Smith",
        "628222@s.whatsapp.net": "Bob",
        "628333@s.whatsapp.net": "Charlie",
    }


def test_live_contacts_cached_second_call_no_request(gowa, monkeypatch, clear_chats_caches):
    """A second live_contacts call within TTL must NOT hit _request again."""
    call_count = 0

    def fake_req(method, url, **kw):
        nonlocal call_count
        call_count += 1
        return _FakeContactsResp()

    monkeypatch.setattr("backend.whatsapp.gowa.httpx.request", fake_req)
    gowa.live_contacts("dev1")  # first call — hits HTTP
    gowa.live_contacts("dev1")  # second call — must use cache
    assert call_count == 1, f"Expected 1 HTTP call, got {call_count}"


def test_list_chats_overlays_live_contact_name(gowa, monkeypatch, clear_chats_caches):
    """list_chats fills empty GOWA chat names with live contact names by JID."""

    def fake_req(method, url, **kw):
        if "/user/my/contacts" in url:
            return _FakeContactsResp()
        # /chats response: one 1:1 chat with an empty name (GOWA privacy mode)
        class _R:
            status_code = 200
            text = "ok"
            def json(self):
                return {"results": {"data": [
                    {"jid": "628111@s.whatsapp.net", "name": ""},
                ]}}
        return _R()

    monkeypatch.setattr("backend.whatsapp.gowa.httpx.request", fake_req)
    result = gowa.list_chats("dev1")
    assert len(result) == 1
    assert result[0]["name"] == "Alice Smith", f"Expected 'Alice Smith', got {result[0]['name']!r}"
    assert result[0]["jid"] == "628111@s.whatsapp.net"
    assert result[0]["is_group"] is False


def test_find_contact_substring_match(session, two_tenants, clear_chats_caches):
    """find_contact returns contacts whose name contains the query substring."""
    from backend.tools.whatsapp_tools import WhatsAppTools

    _, u1, _, _ = two_tenants

    class _FakeProvider:
        def live_contacts(self, session_id):
            return {
                "628111@s.whatsapp.net": "Alice Smith",
                "628222@s.whatsapp.net": "Bob Jones",
                "628333@s.whatsapp.net": "Alice Cooper",
            }

        # _all_chats fallback (not needed for this test but avoids AttributeError)
        def list_chats(self, device_id, *, limit=50):
            return []

        def recent_chats(self, *, session_id=None, days=7, limit=100):
            return []

    t = WhatsAppTools(user=u1, session=session, device_id="dev1")
    t.provider = _FakeProvider()

    result = t.find_contact("alice")
    assert "Alice Smith" in result
    assert "Alice Cooper" in result
    assert "Bob Jones" not in result
    # JID should be rendered as a readable number
    assert "+628111" in result
    assert "+628333" in result
