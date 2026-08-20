"""Pre-flight guardrail: one fast LLM call screens sandbox code before it runs."""

from __future__ import annotations

import backend.code_exec.guardrails as g
from backend.code_exec.guardrails import screen_code


def _mock_verdict(monkeypatch, payload):
    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": payload}}]}
    monkeypatch.setattr(g.settings, "guardrail_enabled", True)
    monkeypatch.setattr(g.settings, "together_api_key", "k")
    monkeypatch.setattr(g.httpx, "post", lambda *a, **k: Resp())


def test_safe_code_allowed(monkeypatch):
    _mock_verdict(monkeypatch, '{"safe": true, "reason": ""}')
    allowed, reason = screen_code("print(mai.find_chats())")
    assert allowed is True


def test_unsafe_code_blocked(monkeypatch):
    _mock_verdict(monkeypatch, '{"safe": false, "reason": "reads /proc to exfiltrate secrets"}')
    allowed, reason = screen_code("import os; print(open('/proc/1/environ').read())")
    assert allowed is False and "secret" in reason.lower()


def test_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(g.settings, "guardrail_enabled", False)
    assert screen_code("anything") == (True, "")


def test_infra_error_fails_open(monkeypatch):
    monkeypatch.setattr(g.settings, "guardrail_enabled", True)
    monkeypatch.setattr(g.settings, "together_api_key", "k")
    def boom(*a, **k): raise RuntimeError("timeout")
    monkeypatch.setattr(g.httpx, "post", boom)
    assert screen_code("print(1)") == (True, "")  # fail open on infra error
