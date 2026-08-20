"""LLM-routed tool selection: categories gate the optional toolkits (mocked router)."""

from __future__ import annotations

import backend.tools.tool_router as tr
from backend.tools.registry import build_tools


def _names(tools):
    return {type(t).__name__ for t in tools}


def _route(monkeypatch, *categories):
    monkeypatch.setattr(tr, "select_categories", lambda msg: frozenset(categories))


def test_generation_category_gates_replicate(session, two_tenants, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "replicate_api_token", "tok")
    _, u1, *_ = two_tenants
    _route(monkeypatch, "generation")
    n = _names(build_tools(u1, session, message="draw me a koi fish"))
    assert "ReplicateTools" in n
    assert "ScheduleTools" not in n  # not routed to scheduling


def test_scheduling_category(session, two_tenants, monkeypatch):
    _, u1, *_ = two_tenants
    _route(monkeypatch, "scheduling")
    n = _names(build_tools(u1, session, message="remind me at 8am"))
    assert "ScheduleTools" in n and "CredentialTools" not in n


def test_integrations_category(session, two_tenants, monkeypatch):
    _, u1, *_ = two_tenants
    _route(monkeypatch, "integrations")
    assert "CredentialTools" in _names(build_tools(u1, session, message="connect my gmail"))


def test_empty_categories_leaves_only_core(session, two_tenants, monkeypatch):
    _, u1, *_ = two_tenants
    _route(monkeypatch)  # no categories
    n = _names(build_tools(u1, session, message="hey, how are you?"))
    # The always-on core (error_report joined it: available the moment anyone complains).
    assert n == {"WebSearchTools", "WebReadTools", "MemoryTools", "SkillTools", "ErrorReportTools"}


def test_router_failure_returns_full_set(session, two_tenants, monkeypatch):
    """select_categories -> None (disabled/error) must include the full owner set."""
    _, u1, *_ = two_tenants
    monkeypatch.setattr(tr, "select_categories", lambda msg: None)
    n = _names(build_tools(u1, session, message="anything"))
    assert {"WebSearchTools", "ScheduleTools", "CredentialTools"} <= n


def test_guests_get_stateless_tools_only(session, two_tenants, monkeypatch):
    """is_owner=False is a SECURITY boundary (registry docstring): a guest gets the stateless
    helpers (web, generation, error report) but NEVER the owner's private tools — schedules,
    credentials, memory, skills. Full guest-boundary coverage: tests/test_registry_guest.py."""
    from backend.config import settings

    monkeypatch.setattr(settings, "replicate_api_token", "tok")
    _, u1, *_ = two_tenants
    monkeypatch.setattr(tr, "select_categories", lambda msg: None)  # full set
    n = _names(build_tools(u1, session, message="draw a cat", is_owner=False))
    assert "ReplicateTools" in n
    assert n & {"ScheduleTools", "CredentialTools", "MemoryTools", "SkillTools"} == set()


def test_profile_and_crosschat_categories(session, two_tenants, monkeypatch):
    from backend.config import settings
    from backend.db.tenancy import WhatsAppConnection, WhatsAppConnectionStatus

    monkeypatch.setattr(settings, "whatsapp_gateway_url", "https://gw")
    _, u1, *_ = two_tenants
    session.add(WhatsAppConnection(account_id=u1.account_id, session_id="dev1",
                                   status=WhatsAppConnectionStatus.connected))
    session.commit()
    _route(monkeypatch, "profile")
    assert "ProfileTools" in _names(build_tools(u1, session, message="x",
                                                channel="g@g.us", device_id="dev1"))
    _route(monkeypatch, "crosschat")
    n = _names(build_tools(u1, session, message="x", channel="g@g.us", device_id="dev1"))
    assert "WhatsAppTools" in n and "ProfileTools" not in n
