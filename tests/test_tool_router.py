"""LLM tool router: enable flag, parsing, fail-open (no real model calls)."""

from __future__ import annotations

import backend.tools.tool_router as tr


def test_disabled_returns_none(monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "tool_router_enabled", False)
    tr.select_categories.cache_clear()
    assert tr.select_categories("draw a cat") is None


def test_returns_classified_categories(monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "tool_router_enabled", True)
    monkeypatch.setattr(tr, "_classify", lambda msg, t, m: {"generation"})
    tr.select_categories.cache_clear()
    assert tr.select_categories("צייר לי חתול") == frozenset({"generation"})


def test_failure_is_fail_open(monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "tool_router_enabled", True)

    def boom(*a):
        raise RuntimeError("router down")

    monkeypatch.setattr(tr, "_classify", boom)
    tr.select_categories.cache_clear()
    assert tr.select_categories("hello") is None  # None -> caller includes full set


def test_empty_message_returns_none():
    tr.select_categories.cache_clear()
    assert tr.select_categories("   ") is None
