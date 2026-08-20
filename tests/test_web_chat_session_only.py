"""Task 7 – Privacy Phase 1: web /chat is session-only.

Verifies that:
- POST /chat with a `history` list causes `respond` to receive the prior
  turns prepended as "Earlier in this conversation: ..." context.
- Malformed history items (missing 'role' or 'text') are skipped silently.
- Without history the message reaches `respond` unchanged.
- No chat_messages row is written regardless of history.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from starlette.testclient import TestClient


@pytest.fixture()
def client(session):
    from backend.auth.deps import get_db
    from backend.auth.router import build_auth_router
    from backend.dashboard.api import build_dashboard_router

    app = FastAPI()
    app.include_router(build_auth_router())
    app.include_router(build_dashboard_router())

    def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def _token(client, email: str = "wcs@e.com") -> str:
    r = client.post("/auth/register", json={"email": email, "password": "longenough"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_history_forwarded_to_respond(client, session, monkeypatch):
    """Prior turns appear in the message passed to respond; no DB row is written."""
    import backend.agent.mai_agent as mai_mod
    import backend.tools.registry as registry_mod
    from backend.db.tenancy import ChatMessage

    captured: dict = {}

    def fake_respond(user, msg, **kw):
        captured["msg"] = msg
        return "I remember!"

    monkeypatch.setattr(mai_mod, "respond", fake_respond)
    monkeypatch.setattr(registry_mod, "build_tools", lambda *a, **kw: [])

    tok = _token(client)

    before = session.scalar(select(func.count()).select_from(ChatMessage))

    history = [
        {"role": "user", "text": "Hello"},
        {"role": "assistant", "text": "Hi there!"},
        {"role": "user", "text": "How are you?"},
    ]
    r = client.post(
        "/api/chat",
        json={"message": "What did I say first?", "history": history},
        headers=_auth(tok),
    )
    assert r.status_code == 200, r.text
    assert r.json()["reply"] == "I remember!"

    # Prior turns must appear in the message passed to respond.
    msg = captured.get("msg", "")
    assert "Earlier in this conversation" in msg, f"Context header missing from: {msg!r}"
    assert "user: Hello" in msg, f"First user turn missing from: {msg!r}"
    assert "assistant: Hi there!" in msg, f"Assistant turn missing from: {msg!r}"

    # No rows should be written to the chat_messages table.
    after = session.scalar(select(func.count()).select_from(ChatMessage))
    assert after == before, f"Expected no new chat_messages rows; got {after - before} new"


def test_malformed_history_items_skipped(client, session, monkeypatch):
    """Items missing 'role' or 'text' are silently dropped."""
    import backend.agent.mai_agent as mai_mod
    import backend.tools.registry as registry_mod

    captured: dict = {}

    def fake_respond(user, msg, **kw):
        captured["msg"] = msg
        return "ok"

    monkeypatch.setattr(mai_mod, "respond", fake_respond)
    monkeypatch.setattr(registry_mod, "build_tools", lambda *a, **kw: [])

    tok = _token(client, "wcs2@e.com")

    history = [
        {"role": "user", "text": "Good turn"},
        {"oops": "no role or text"},      # missing both → skip
        {"role": "assistant"},             # missing text → skip
        {"text": "no role"},               # missing role → skip
        {"role": "user", "text": "Also good"},
    ]
    r = client.post(
        "/api/chat",
        json={"message": "test", "history": history},
        headers=_auth(tok),
    )
    assert r.status_code == 200, r.text

    msg = captured.get("msg", "")
    assert "user: Good turn" in msg, f"Valid turn missing from: {msg!r}"
    assert "user: Also good" in msg, f"Valid turn missing from: {msg!r}"
    # Malformed items should NOT leak into the context string.
    assert "oops" not in msg
    assert "no role or text" not in msg


def test_no_history_sends_plain_message(client, session, monkeypatch):
    """Without history the raw message reaches respond, with no context prefix."""
    import backend.agent.mai_agent as mai_mod
    import backend.tools.registry as registry_mod

    captured: dict = {}

    def fake_respond(user, msg, **kw):
        captured["msg"] = msg
        return "ok"

    monkeypatch.setattr(mai_mod, "respond", fake_respond)
    monkeypatch.setattr(registry_mod, "build_tools", lambda *a, **kw: [])

    tok = _token(client, "wcs3@e.com")

    r = client.post(
        "/api/chat",
        json={"message": "just a plain message"},
        headers=_auth(tok),
    )
    assert r.status_code == 200
    assert captured["msg"] == "just a plain message"
    assert "Earlier" not in captured["msg"]
