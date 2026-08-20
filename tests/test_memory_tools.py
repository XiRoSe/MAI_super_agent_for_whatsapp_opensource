"""Dedicated memory tool: remember -> recall -> forget round-trip against the agno DB."""

from __future__ import annotations

from backend.tools.memory_tools import MemoryTools


def test_memory_roundtrip(two_tenants):
    _, u1, *_ = two_tenants
    t = MemoryTools(user=u1)
    fact = "Likes pineapple on pizza (test-marker-xyz)"
    assert "Saved" in t.remember(fact)
    assert "test-marker-xyz" in t.recall()
    assert "Forgotten" in t.forget("test-marker-xyz")
    assert "test-marker-xyz" not in t.recall()


def test_registry_includes_memory(session, two_tenants):
    from backend.tools.registry import build_tools

    _, u1, *_ = two_tenants
    names = {type(t).__name__ for t in build_tools(u1, session)}
    assert "MemoryTools" in names
