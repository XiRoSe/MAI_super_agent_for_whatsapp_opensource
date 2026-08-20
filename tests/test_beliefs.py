"""Self-evolving belief system: default, counting, threshold reflection, parsing."""

from __future__ import annotations

import agno.agent

import backend.agent.beliefs as b
import backend.models.factory as mf


def test_default_beliefs(session, two_tenants):
    _, u1, *_ = two_tenants
    assert "getting to know" in b.get_beliefs(session, u1)  # the seeded default


def test_record_turn_counts_and_triggers_at_threshold(session, two_tenants, monkeypatch):
    from backend.config import settings

    a1, u1, *_ = two_tenants
    a1.auto_memory = True  # record_turn is opt-in: it no-ops unless the account enabled auto_memory
    session.commit()
    started = []

    class _FakeThread:
        def __init__(self, *a, **k): pass
        def start(self): started.append(1)

    monkeypatch.setattr(b.threading, "Thread", _FakeThread)
    monkeypatch.setattr(settings, "belief_reflect_every", 3)

    b.record_turn(session, u1)
    b.record_turn(session, u1)
    assert b._row(session, u1).msg_count == 2 and started == []  # below threshold

    b.record_turn(session, u1)  # 3rd -> reflect
    assert b._row(session, u1).msg_count == 3 and started == [1]


def test_disabled_is_a_noop(session, two_tenants, monkeypatch):
    from backend.config import settings

    _, u1, *_ = two_tenants
    monkeypatch.setattr(settings, "belief_system_enabled", False)
    b.record_turn(session, u1)
    assert b._row(session, u1) is None  # nothing created when disabled


def _fake_agent(content):
    class FakeAgent:
        def __init__(self, **k): pass
        def run(self, msg):
            return type("R", (), {"content": content})()
    return FakeAgent


def test_reflect_keeps_on_no_change(monkeypatch):
    monkeypatch.setattr(mf, "get_model", lambda **k: object())
    monkeypatch.setattr(agno.agent, "Agent", _fake_agent("NO CHANGE"))
    assert b._reflect("current", "recent convo") is None


def test_reflect_updates_on_shift(monkeypatch):
    monkeypatch.setattr(mf, "get_model", lambda **k: object())
    monkeypatch.setattr(agno.agent, "Agent",
                        _fake_agent("- I now value directness over politeness.\n- I push back when asked."))
    out = b._reflect("current", "recent convo")
    assert out and "directness" in out
