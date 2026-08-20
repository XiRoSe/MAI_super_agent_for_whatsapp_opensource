"""Semantic memory recall in build_agent: only relevant memories are injected,
with a transparent fall back to ALL when the embedder is unavailable."""

from __future__ import annotations

from dataclasses import dataclass

import backend.agent.mai_agent as na
from backend.agent import semantic


@dataclass
class _Mem:
    memory: str


class _FakeDB:
    """Stands in for the agno DB: only get_user_memories is exercised here."""

    def __init__(self, mems):
        self._mems = mems

    def get_user_memories(self, *, user_id):
        return self._mems


class _CapturingAgent:
    """Captures the system_message build_agent assembles, in place of agno's Agent."""

    last_system_message = ""

    def __init__(self, *args, **kwargs):
        _CapturingAgent.last_system_message = kwargs.get("system_message", "")


def _wire(monkeypatch, mems):
    monkeypatch.setattr(na, "get_agno_db", lambda: _FakeDB(mems))
    monkeypatch.setattr(na, "Agent", _CapturingAgent)


def test_memory_semantic_injects_only_relevant(two_tenants, monkeypatch):
    from backend.config import settings

    _, u1, *_ = two_tenants
    mems = [_Mem("Likes pineapple on pizza"), _Mem("Drives a red car")]
    _wire(monkeypatch, mems)
    # rank is faked, so no embedder is needed — force the recall flag so the test is
    # hermetic (doesn't depend on SEMANTIC_RECALL_ENABLED / TOGETHER_API_KEY in the env).
    monkeypatch.setattr(settings, "semantic_recall_enabled", True)
    # rank selects only the pizza memory.
    monkeypatch.setattr(semantic, "rank",
                        lambda q, items, key, **kw: [m for m in items if "pizza" in m.memory])

    na.build_agent(u1, model=object(), query="what topping do I like?")
    msg = _CapturingAgent.last_system_message
    assert "pineapple on pizza" in msg
    assert "red car" not in msg


def test_memory_falls_back_to_all_when_rank_none(two_tenants, monkeypatch):
    _, u1, *_ = two_tenants
    mems = [_Mem("Likes pineapple on pizza"), _Mem("Drives a red car")]
    _wire(monkeypatch, mems)
    monkeypatch.setattr(semantic, "rank", lambda *a, **k: None)  # embedder unavailable

    na.build_agent(u1, model=object(), query="anything")
    msg = _CapturingAgent.last_system_message
    assert "pineapple on pizza" in msg and "red car" in msg  # all injected


def test_memory_injects_all_without_query(two_tenants, monkeypatch):
    """No query (e.g. non-chat callers) -> prior behavior: inject all memories."""
    _, u1, *_ = two_tenants
    mems = [_Mem("Likes pineapple on pizza"), _Mem("Drives a red car")]
    _wire(monkeypatch, mems)
    called = {"rank": False}
    monkeypatch.setattr(semantic, "rank",
                        lambda *a, **k: called.__setitem__("rank", True) or [])

    na.build_agent(u1, model=object())  # query defaults to None
    msg = _CapturingAgent.last_system_message
    assert "pineapple on pizza" in msg and "red car" in msg
    assert called["rank"] is False  # selection skipped entirely
