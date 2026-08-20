"""Outbound media tools: send image/video/file + react to the current chat."""

from __future__ import annotations

from backend.tools.media_tools import MediaTools


class FakeProvider:
    def __init__(self):
        self.calls = []

    def send_image(self, to, url, *, caption=None, session_id=None):
        self.calls.append(("image", to, url, caption, session_id))
        return "mid1"

    def send_video(self, to, url, *, caption=None, session_id=None):
        self.calls.append(("video", to, url, caption, session_id))
        return "mid2"

    def send_file(self, to, url, *, caption=None, session_id=None):
        self.calls.append(("file", to, url, caption, session_id))
        return "mid3"

    def react(self, to, message_id, emoji, *, session_id=None):
        self.calls.append(("react", to, message_id, emoji, session_id))


def _tools(monkeypatch, fake, **kw):
    t = MediaTools(device_id="dev1", current_chat="group@g.us",
                   invoke_message_id=kw.get("mid", "m1"))
    monkeypatch.setattr(t, "_provider", lambda: fake)
    return t


def test_send_image(monkeypatch):
    fake = FakeProvider()
    t = _tools(monkeypatch, fake)
    assert "Sent the image" in t.send_image("https://x/y.png", "a koi")
    assert fake.calls[0] == ("image", "group@g.us", "https://x/y.png", "a koi", "dev1")


def test_send_video_and_file(monkeypatch):
    fake = FakeProvider()
    t = _tools(monkeypatch, fake)
    t.send_video("https://x/v.mp4")
    t.send_file("https://x/d.pdf", "report")
    assert fake.calls[0][0] == "video" and fake.calls[1][0] == "file"


def test_react(monkeypatch):
    fake = FakeProvider()
    t = _tools(monkeypatch, fake)
    assert "👍" in t.react("👍")
    assert fake.calls[0] == ("react", "group@g.us", "m1", "👍", "dev1")


def test_react_without_target():
    t = MediaTools(device_id="dev1", current_chat="group@g.us", invoke_message_id=None)
    assert "Nothing to react" in t.react("👍")


def test_registry_includes_media_when_device_and_channel(session, two_tenants, monkeypatch):
    from backend.config import settings
    from backend.tools.registry import build_tools

    _, u1, _, _ = two_tenants
    monkeypatch.setattr(settings, "whatsapp_gateway_url", "https://gw.example")
    tools = build_tools(u1, session, channel="g@g.us", device_id="dev1", invoke_message_id="m1")
    assert any(isinstance(t, MediaTools) for t in tools)
    # Guests get media too (post generated images in their chat).
    gtools = build_tools(u1, session, channel="g@g.us", device_id="dev1",
                         invoke_message_id="m1", is_owner=False)
    assert any(isinstance(t, MediaTools) for t in gtools)


# ---- inbound media (phase 2) ---------------------------------------------
def test_parse_inbound_detects_image_with_caption():
    from backend.whatsapp.gowa import GowaWhatsAppProvider

    p = GowaWhatsAppProvider()
    msgs = p.parse_inbound({"event": "message", "device_id": "d", "payload": {
        "from": "9725@s.whatsapp.net", "id": "m1",
        "image": {"caption": "@mai what is this?"}}})
    assert len(msgs) == 1
    assert msgs[0].media_type == "image" and "@mai" in msgs[0].text


def test_describe_media_image(monkeypatch):
    import sys
    import types

    import backend.whatsapp.media_in as mi
    from backend.config import settings

    monkeypatch.setattr(settings, "replicate_api_token", "tok")
    captured = {}

    class FakeClient:
        def run(self, model, input=None):
            captured.update(model=model, keys=list(input.keys()))
            return ["a red ", "sports car"]

    monkeypatch.setitem(sys.modules, "replicate",
                        types.SimpleNamespace(Client=lambda api_token=None: FakeClient()))
    out = mi.describe_media("image", b"bytes", question="what is it")
    assert "red" in out and captured["model"] == settings.replicate_vision_model
    assert "image" in captured["keys"]


def test_media_note_surfaces_url(monkeypatch):
    import backend.whatsapp.router as r
    from backend.whatsapp.provider import InboundMessage

    msg = InboundMessage(from_number="+1", text="@mai transcribe", message_id="m1",
                         media_type="audio", media_url="https://gw/statics/a.ogg")
    out = r._media_note(msg)
    assert "https://gw/statics/a.ogg" in out and "audio" in out


def test_media_note_not_retrievable_without_url():
    import backend.whatsapp.router as r
    from backend.whatsapp.provider import InboundMessage

    msg = InboundMessage(from_number="+1", text="@mai what is it", message_id="m1",
                         media_type="image")
    out = r._media_note(msg)
    assert "isn't retrievable" in out


def test_media_note_none_when_no_media():
    import backend.whatsapp.router as r
    from backend.whatsapp.provider import InboundMessage

    msg = InboundMessage(from_number="+1", text="@mai hi", message_id="m1")
    assert r._media_note(msg) is None


def test_send_image_sets_turn_state(monkeypatch):
    """Sending media records that Mai already replied in-chat (router suppresses text)."""
    fake = FakeProvider()
    state: dict = {}
    t = MediaTools(device_id="dev1", current_chat="g@g.us", invoke_message_id="m1",
                   turn_state=state)
    monkeypatch.setattr(t, "_provider", lambda: fake)
    t.send_image("https://x/y.png", "a koi")
    assert state.get("replied_in_chat") is True


def test_failed_send_does_not_set_turn_state(monkeypatch):
    class Boom:
        def send_image(self, *a, **k):
            raise RuntimeError("nope")

    state: dict = {}
    t = MediaTools(device_id="dev1", current_chat="g@g.us", turn_state=state)
    monkeypatch.setattr(t, "_provider", lambda: Boom())
    t.send_image("https://x/y.png")
    assert "replied_in_chat" not in state  # failure -> text reply still sent


# ---- earlier-message media (#6) ------------------------------------------
def test_read_recent_media_returns_url(monkeypatch):
    class P:
        def get_chat_messages(self, dev, chat, *, limit=20):
            return [
                {"id": "m1", "sender": "Me", "sender_number": "+1", "text": "hi",
                 "media_type": None, "media_url": None, "from_me": True},
                {"id": "m2", "sender": "Dana", "sender_number": "+97250", "text": "look",
                 "media_type": "image", "media_url": "https://gw/statics/p.jpg",
                 "from_me": False},
            ]

    t = MediaTools(device_id="dev1", current_chat="g@g.us")
    monkeypatch.setattr("backend.whatsapp.get_provider", lambda: P())
    out = t.read_recent_media()
    assert "https://gw/statics/p.jpg" in out and "Dana" in out
    assert "look" in out  # caption surfaced


def test_read_recent_media_when_none(monkeypatch):
    class P:
        def get_chat_messages(self, dev, chat, *, limit=20):
            return [{"id": "m1", "text": "hi", "media_type": None}]

    t = MediaTools(device_id="dev1", current_chat="g@g.us")
    monkeypatch.setattr("backend.whatsapp.get_provider", lambda: P())
    assert "don't see" in t.read_recent_media()


def test_video_needs_a_model(monkeypatch):
    import backend.whatsapp.media_in as mi
    from backend.config import settings

    monkeypatch.setattr(settings, "replicate_api_token", "tok")
    monkeypatch.setattr(settings, "replicate_video_model", "")
    assert mi.describe_media("video", b"x") is None  # no video model configured -> None
