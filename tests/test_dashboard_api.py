"""Dashboard API tests: auth-gated, tenant-scoped account/settings/schedules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
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


@pytest.fixture()
def agno_db(monkeypatch, tmp_path):
    """Isolate the agno memory store: a fresh temp SQLite db per test.

    The dashboard memory endpoints read/write the agno DB (separate from the
    tenancy session), so we point `get_agno_db` at a throwaway file so tests
    never touch the developer's real tmp/mai.db.
    """
    from agno.db.sqlite import SqliteDb

    db = SqliteDb(db_file=str(tmp_path / "agno_test.db"))
    monkeypatch.setattr("backend.db.agno_db.get_agno_db", lambda: db)
    return db


def _token(client, email="d@e.com"):
    r = client.post("/auth/register", json={"email": email, "password": "longenough"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_requires_auth(client):
    assert client.get("/api/account").status_code == 401


def test_account_and_settings_flow(client):
    tok = _token(client)
    acc = client.get("/api/account", headers=_auth(tok)).json()
    assert acc["subscription_status"] == "trial"
    assert acc["usage"]["messages_today"] == 0

    r = client.patch("/api/settings", json={"timezone": "America/New_York", "display_name": "Dan"},
                     headers=_auth(tok))
    assert r.status_code == 200 and r.json()["timezone"] == "America/New_York"
    bad = client.patch("/api/settings", json={"timezone": "Not/Real"}, headers=_auth(tok))
    assert bad.status_code == 400


def test_schedule_crud_via_api(client):
    tok = _token(client, "sched@e.com")
    client.patch("/api/settings", json={"timezone": "UTC"}, headers=_auth(tok))
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
    r = client.post("/api/schedules", json={
        "name": "ping", "kind": "static", "action_text": "hi",
        "schedule_type": "once", "run_at": future,
    }, headers=_auth(tok))
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    lst = client.get("/api/schedules", headers=_auth(tok)).json()
    assert len(lst["schedules"]) == 1
    assert client.delete(f"/api/schedules/{sid}", headers=_auth(tok)).status_code == 200
    assert client.get("/api/schedules", headers=_auth(tok)).json()["schedules"] == []


def test_whatsapp_connection_status(client):
    tok = _token(client, "wa@e.com")
    r = client.get("/api/whatsapp/connection", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["session_id"].startswith("mai_acct_")


def test_credentials_flow(client):
    tok = _token(client, "intg@e.com")
    # Nothing connected yet.
    assert client.get("/api/credentials", headers=_auth(tok)).json()["services"] == []
    # Save a credential (JSON blob).
    r = client.post("/api/credentials", json={
        "service": "Google", "credentials": '{"api_key":"k"}', "label": "me@gmail.com",
    }, headers=_auth(tok))
    assert r.status_code == 200 and r.json()["saved"] == "google"
    services = client.get("/api/credentials", headers=_auth(tok)).json()["services"]
    assert services[0]["service"] == "google" and services[0]["label"] == "me@gmail.com"
    assert "api_key" not in str(services)  # listing never leaks the secret
    # Delete it.
    assert client.delete("/api/credentials/google", headers=_auth(tok)).json()["deleted"] is True


def test_tenant_isolation_via_api(client):
    """Two registered accounts never see each other's schedules."""
    t1 = _token(client, "iso1@e.com")
    t2 = _token(client, "iso2@e.com")
    client.patch("/api/settings", json={"timezone": "UTC"}, headers=_auth(t1))
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    client.post("/api/schedules", json={"name": "s", "kind": "static", "action_text": "x",
                "schedule_type": "once", "run_at": future}, headers=_auth(t1))
    # Account 2 sees none of account 1's schedules.
    assert client.get("/api/schedules", headers=_auth(t2)).json()["schedules"] == []


def test_skills_endpoints_flow(client):
    tok = _token(client, "skills@e.com")
    # First GET seeds the defaults.
    seeded = client.get("/api/skills", headers=_auth(tok)).json()["skills"]
    assert len(seeded) >= 3 and all(s["is_default"] for s in seeded)
    # Add a custom skill.
    r = client.post("/api/skills", json={"name": "My Helper", "description": "when X",
                    "content": "do Y"}, headers=_auth(tok))
    assert r.status_code == 200 and r.json()["name"] == "my-helper"
    sid = r.json()["id"]
    # Toggle it off.
    assert client.patch(f"/api/skills/{sid}", json={"enabled": False},
                        headers=_auth(tok)).json()["enabled"] is False
    # Delete it.
    assert client.delete(f"/api/skills/{sid}", headers=_auth(tok)).json()["deleted"] == sid


def test_memory_endpoint_shape(client):
    tok = _token(client, "mem@e.com")
    r = client.get("/api/memories", headers=_auth(tok))
    assert r.status_code == 200 and "memories" in r.json()


def test_memories_crud_via_api(client, agno_db):
    tok = _token(client, "memcrud@e.com")
    # Empty to start.
    assert client.get("/api/memories", headers=_auth(tok)).json()["memories"] == []
    # Create.
    r = client.post("/api/memories", json={"text": "Prefers morning meetings",
                    "topics": ["preferences", "work"]}, headers=_auth(tok))
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["text"] == "Prefers morning meetings"
    assert created["topics"] == ["preferences", "work"]
    mid = created["id"]
    assert mid
    # List shows it.
    mems = client.get("/api/memories", headers=_auth(tok)).json()["memories"]
    assert len(mems) == 1 and mems[0]["id"] == mid
    # Update (in place — not a duplicate).
    up = client.patch(f"/api/memories/{mid}", json={"text": "Prefers afternoon meetings",
                      "topics": ["preferences"]}, headers=_auth(tok))
    assert up.status_code == 200
    assert up.json()["text"] == "Prefers afternoon meetings"
    assert up.json()["id"] == mid
    mems = client.get("/api/memories", headers=_auth(tok)).json()["memories"]
    assert len(mems) == 1 and mems[0]["text"] == "Prefers afternoon meetings"
    # Delete.
    assert client.delete(f"/api/memories/{mid}", headers=_auth(tok)).json()["ok"] is True
    assert client.get("/api/memories", headers=_auth(tok)).json()["memories"] == []


def test_memories_validation_and_missing(client, agno_db):
    tok = _token(client, "memval@e.com")
    # Empty text rejected.
    assert client.post("/api/memories", json={"text": "  "},
                       headers=_auth(tok)).status_code == 400
    # Patch/delete unknown id -> 404.
    assert client.patch("/api/memories/nope", json={"text": "x"},
                        headers=_auth(tok)).status_code == 404
    assert client.delete("/api/memories/nope", headers=_auth(tok)).status_code == 404


def test_memories_tenant_isolation(client, agno_db):
    """A user can't see, edit, or delete another user's memory."""
    t1 = _token(client, "memiso1@e.com")
    t2 = _token(client, "memiso2@e.com")
    mid = client.post("/api/memories", json={"text": "secret of user one"},
                      headers=_auth(t1)).json()["id"]
    # User 2 sees nothing of user 1's.
    assert client.get("/api/memories", headers=_auth(t2)).json()["memories"] == []
    # User 2 cannot patch or delete user 1's memory id.
    assert client.patch(f"/api/memories/{mid}", json={"text": "hijacked"},
                        headers=_auth(t2)).status_code == 404
    assert client.delete(f"/api/memories/{mid}", headers=_auth(t2)).status_code == 404
    # User 1's memory is untouched.
    mems = client.get("/api/memories", headers=_auth(t1)).json()["memories"]
    assert len(mems) == 1 and mems[0]["text"] == "secret of user one"


def test_edit_schedule_via_api(client):
    tok = _token(client, "edit-sched@e.com")
    client.patch("/api/settings", json={"timezone": "UTC"}, headers=_auth(tok))
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
    sid = client.post("/api/schedules", json={"name": "old", "kind": "static",
        "action_text": "hi", "schedule_type": "once", "run_at": future},
        headers=_auth(tok)).json()["id"]
    # Edit it -> recurring dynamic.
    r = client.patch(f"/api/schedules/{sid}", json={"name": "new", "kind": "dynamic",
        "action_text": "summarize my day", "schedule_type": "recurring", "cron": "0 8 * * 1-5"},
        headers=_auth(tok))
    assert r.status_code == 200
    s = client.get("/api/schedules", headers=_auth(tok)).json()["schedules"][0]
    assert s["name"] == "new" and s["kind"] == "dynamic" and s["cron"] == "0 8 * * 1-5"
    # Editing a non-existent schedule -> 404.
    assert client.patch("/api/schedules/99999", json={"name": "x", "kind": "static",
        "action_text": "y", "schedule_type": "recurring", "cron": "0 9 * * *"},
        headers=_auth(tok)).status_code == 404


def test_beliefs_endpoints(client):
    tok = _token(client, "belief@e.com")
    got = client.get("/api/beliefs", headers=_auth(tok)).json()
    assert "content" in got and "getting to know" in got["content"]  # default seeded
    # Edit it.
    r = client.put("/api/beliefs", json={"content": "- I am bold and direct."},
                   headers=_auth(tok)).json()
    assert "bold and direct" in r["content"]
    # Reset.
    back = client.post("/api/beliefs/reset", headers=_auth(tok)).json()
    assert "getting to know" in back["content"] and back["msg_count"] == 0
