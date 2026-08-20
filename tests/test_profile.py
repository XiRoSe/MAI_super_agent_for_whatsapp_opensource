"""Profile lookup: avatar/details by phone, owner default, registry gating."""

from __future__ import annotations

from backend.tools.profile_tools import ProfileTools


class FakeProvider:
    def __init__(self):
        self.calls = []

    def get_avatar(self, phone, *, session_id=None, preview=False):
        self.calls.append(("avatar", phone, session_id))
        return f"https://pps.example/{phone}.jpg"

    def get_user_info(self, phone, *, session_id=None):
        return {"verified_name": "Bob", "status": "busy", "secret": "x"}


def _tools(monkeypatch, owner="+97250", current=None):
    t = ProfileTools(device_id="dev1", owner_phone=owner, current_chat=current)
    fake = FakeProvider()
    monkeypatch.setattr(t, "_provider", lambda: fake)
    return t, fake


def test_get_my_profile_picture_defaults_to_owner(monkeypatch):
    t, fake = _tools(monkeypatch)
    url = t.get_profile_picture("me")
    assert url == "https://pps.example/+97250.jpg"
    assert fake.calls[0] == ("avatar", "+97250", "dev1")


def test_get_profile_picture_explicit_number(monkeypatch):
    t, fake = _tools(monkeypatch)
    assert "+15551112222" in t.get_profile_picture("+15551112222")


def test_get_user_details_filters_fields(monkeypatch):
    t, _ = _tools(monkeypatch)
    out = t.get_user_details("me")
    assert "Bob" in out and "busy" in out and "secret" not in out


def test_registry_includes_profile_when_routed(session, two_tenants, monkeypatch):
    import backend.tools.tool_router as tr
    from backend.config import settings
    from backend.db.tenancy import WhatsAppConnection, WhatsAppConnectionStatus
    from backend.tools.registry import build_tools

    monkeypatch.setattr(settings, "whatsapp_gateway_url", "https://gw")
    _, u1, *_ = two_tenants
    session.add(WhatsAppConnection(account_id=u1.account_id, session_id="dev1",
                                   status=WhatsAppConnectionStatus.connected))
    session.commit()

    monkeypatch.setattr(tr, "select_categories", lambda msg: frozenset({"profile", "generation"}))
    names = {type(t).__name__ for t in build_tools(
        u1, session, message="make my profile pic anime", channel="g@g.us", device_id="dev1")}
    assert "ProfileTools" in names

    monkeypatch.setattr(tr, "select_categories", lambda msg: frozenset())
    plain = {type(t).__name__ for t in build_tools(
        u1, session, message="what is 2+2", channel="g@g.us", device_id="dev1")}
    assert "ProfileTools" not in plain


def test_name_in_direct_chat_resolves_to_contact(monkeypatch):
    """'Alex's pic' in a 1:1 with Alex -> use the current chat's number, not the name."""
    t, fake = _tools(monkeypatch, owner="+97250", current="+97259999")
    t.get_profile_picture("Alex")
    assert fake.calls[0] == ("avatar", "+97259999", "dev1")  # resolved to the contact


def test_this_chat_resolves_to_current(monkeypatch):
    t, fake = _tools(monkeypatch, owner="+97250", current="+97259999")
    t.get_profile_picture("this chat")
    assert fake.calls[0][1] == "+97259999"


def test_explicit_number_still_wins(monkeypatch):
    t, fake = _tools(monkeypatch, owner="+97250", current="+97259999")
    t.get_profile_picture("+15551112222")
    assert fake.calls[0][1] == "+15551112222"
