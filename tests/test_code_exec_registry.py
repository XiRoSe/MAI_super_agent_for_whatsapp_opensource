"""run_python is attached only when enabled, owner-only."""

from __future__ import annotations

from backend.tools.registry import build_tools


def _names(tools):
    return {type(t).__name__ for t in tools}


def test_not_attached_when_disabled(session, two_tenants):
    _, u1, *_ = two_tenants
    assert "CodeExecTools" not in _names(build_tools(u1, session))


def test_attached_for_owner_when_enabled(session, two_tenants, monkeypatch):
    from backend.config import settings
    import backend.code_exec.e2b_runner as e2b

    monkeypatch.setattr(settings, "code_exec_enabled", True)
    monkeypatch.setattr(settings, "whatsapp_gateway_url", "https://gw")
    monkeypatch.setattr(settings, "sdk_base_url", "https://app")
    monkeypatch.setattr(e2b, "make_runner", lambda: object())
    _, u1, *_ = two_tenants
    assert "CodeExecTools" in _names(build_tools(u1, session, device_id="dev1"))
    # guests never get it
    assert "CodeExecTools" not in _names(
        build_tools(u1, session, device_id="dev1", is_owner=False))
