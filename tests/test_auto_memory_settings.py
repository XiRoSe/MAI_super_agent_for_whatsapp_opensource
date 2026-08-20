"""Tests for auto_memory toggle via the /account settings API."""

from __future__ import annotations

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


def _token(client, email="automem@e.com"):
    r = client.post("/auth/register", json={"email": email, "password": "longenough"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_get_account_includes_auto_memory_default_false(client):
    """GET /account includes auto_memory field defaulting to False."""
    tok = _token(client)
    r = client.get("/api/account", headers=_auth(tok))
    assert r.status_code == 200
    data = r.json()
    assert "auto_memory" in data
    assert data["auto_memory"] is False


def test_patch_account_sets_auto_memory_true(client):
    """PATCH /account with auto_memory=true persists and GET returns true."""
    tok = _token(client, "automem_true@e.com")
    r = client.patch("/api/account", json={"auto_memory": True}, headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["auto_memory"] is True

    # Confirm GET reflects the change.
    get_r = client.get("/api/account", headers=_auth(tok))
    assert get_r.status_code == 200
    assert get_r.json()["auto_memory"] is True


def test_patch_account_clears_auto_memory(client):
    """PATCH /account with auto_memory=false clears the flag."""
    tok = _token(client, "automem_clear@e.com")
    # Set it true first.
    client.patch("/api/account", json={"auto_memory": True}, headers=_auth(tok))
    # Now clear it.
    r = client.patch("/api/account", json={"auto_memory": False}, headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["auto_memory"] is False

    get_r = client.get("/api/account", headers=_auth(tok))
    assert get_r.json()["auto_memory"] is False


def test_patch_account_non_bool_rejected(client):
    """PATCH /account with a non-bool auto_memory value is rejected (422)."""
    tok = _token(client, "automem_invalid@e.com")
    r = client.patch("/api/account", json={"auto_memory": "yes_please"}, headers=_auth(tok))
    assert r.status_code in (400, 422)


def test_patch_account_omitting_auto_memory_is_noop(client):
    """PATCH /account with no auto_memory field leaves the value unchanged."""
    tok = _token(client, "automem_noop@e.com")
    # Set it true.
    client.patch("/api/account", json={"auto_memory": True}, headers=_auth(tok))
    # PATCH with an empty body — should not reset auto_memory.
    r = client.patch("/api/account", json={}, headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["auto_memory"] is True
