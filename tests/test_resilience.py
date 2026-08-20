"""Provider resilience: the retry ladder (primary -> primary retry -> fallback
provider), dead-run detection, and the implicit cross-provider fallback default."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import backend.agent.mai_agent as na
import backend.models.factory as mf
from backend.config import settings


def test_transient_failure_recovers_on_primary_retry(two_tenants, monkeypatch):
    """A one-off dead stream (the ~10% GLM case) is retried on the SAME provider and
    never reaches the fallback or the user-facing apology."""
    _, u1, *_ = two_tenants
    calls = {"n": 0}

    def flaky(user, text, *, tools, session_id, model, session, sid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("stream died")
        return "recovered reply"

    monkeypatch.setattr(na, "_attempt", flaky)
    monkeypatch.setattr(mf, "get_fallback_model", lambda: object())
    out = na.respond(u1, "hi", session=None)
    assert out == "recovered reply" and calls["n"] == 2


def test_falls_back_to_secondary_on_hard_primary_failure(two_tenants, monkeypatch):
    """A HARD primary failure (credit exhaustion / outage) fails both primary attempts;
    the cross-provider fallback serves the reply."""
    _, u1, *_ = two_tenants
    calls = {"n": 0}

    def fake_attempt(user, text, *, tools, session_id, model, session, sid):
        calls["n"] += 1
        if model is None:           # both primary attempts
            raise RuntimeError("402 insufficient credits")
        return "fallback reply"     # fallback attempt (model is the fallback object)

    monkeypatch.setattr(na, "_attempt", fake_attempt)
    monkeypatch.setattr(mf, "get_fallback_model", lambda: object())
    out = na.respond(u1, "hi", session=None)
    assert out == "fallback reply" and calls["n"] == 3


def test_graceful_when_no_fallback_configured(two_tenants, monkeypatch):
    _, u1, *_ = two_tenants

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(na, "_attempt", boom)
    monkeypatch.setattr(mf, "get_fallback_model", lambda: None)
    out = na.respond(u1, "hi", session=None)
    assert "try again" in out.lower()


def test_no_fallback_when_caller_pinned_a_model(two_tenants, monkeypatch):
    """Router/other callers that pin a model shouldn't trigger provider fallback
    (but still get the in-provider retry)."""
    _, u1, *_ = two_tenants
    fb = {"called": False}
    monkeypatch.setattr(na, "_attempt", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(mf, "get_fallback_model", lambda: fb.__setitem__("called", True) or object())
    na.respond(u1, "hi", model=object(), session=None)
    assert fb["called"] is False


def test_dead_completed_run_raises_and_gets_retried(two_tenants, monkeypatch):
    """A COMPLETED run with no content and nothing recoverable is a failure (GLM dead
    stream), not a served '(Mai had nothing to say.)' — _attempt raises so the ladder
    retries it."""
    _, u1, *_ = two_tenants
    dead = SimpleNamespace(status="COMPLETED", content=None, messages=[], tools=[])
    monkeypatch.setattr(na, "build_agent", lambda *a, **k: object())
    monkeypatch.setattr(na, "_run_bounded", lambda *a, **k: dead)
    with pytest.raises(na.EmptyRunError):
        na._attempt(u1, "hi", tools=None, session_id=None, model=None, session=None, sid="s")


def test_default_cross_provider_fallback_when_unconfigured(monkeypatch):
    """No explicit FALLBACK_MODEL_* but the other GLM host's key is present -> the
    fallback resolves to that provider instead of None (the prod credit-exhaustion fix)."""
    monkeypatch.setattr(settings, "fallback_model_provider", "")
    monkeypatch.setattr(settings, "fallback_model_id", "")
    monkeypatch.setattr(settings, "model_provider", "openrouter")
    monkeypatch.setattr(settings, "together_api_key", "test-key")
    assert mf.get_fallback_model() is not None

    monkeypatch.setattr(settings, "together_api_key", "")
    assert mf.get_fallback_model() is None


def test_openrouter_model_not_capped_at_1024_tokens(monkeypatch):
    """agno's OpenRouter class defaults max_tokens to 1024, which truncated Mai's
    replies mid-sentence (and let GLM's reasoning tokens eat the whole budget ->
    empty runs). The factory must always pass our own, much higher cap."""
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    model = mf.get_model(provider="openrouter", model_id="z-ai/glm-5.2")
    assert model.max_tokens == settings.model_max_tokens
    assert model.max_tokens >= 8192
