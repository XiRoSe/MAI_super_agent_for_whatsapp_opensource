"""The env-driven admin allowlist (MAI_ADMIN_EMAILS) and the `is_admin` account flag.

The CRM is gated server-side by `require_admin_email`, which reads the allowlist
from settings (comma-separated env var; empty = NO admins). GET /api/account
exposes `is_admin` so the frontend can mirror the gate for nav visibility.
Also covers the boot-friendliness rule: semantic recall auto-disables (instead of
refusing to boot) when TOGETHER_API_KEY is empty.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from backend.config import Settings, settings


@pytest.fixture()
def client(session):
    from backend.auth.deps import get_db
    from backend.auth.router import build_auth_router
    from backend.dashboard.api import build_dashboard_router
    from backend.dashboard.crm import build_crm_router

    app = FastAPI()
    app.include_router(build_auth_router())
    app.include_router(build_dashboard_router())
    app.include_router(build_crm_router())

    def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def _token(client, email):
    r = client.post("/auth/register", json={"email": email, "password": "longenough"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---- parsing ----------------------------------------------------------------

def test_admin_emails_parse_lowercase_trimmed(monkeypatch):
    monkeypatch.setattr(settings, "mai_admin_emails", " Admin@Example.com , second@example.com ,")
    assert settings.admin_emails == {"admin@example.com", "second@example.com"}


def test_admin_emails_empty_means_no_admins(monkeypatch):
    monkeypatch.setattr(settings, "mai_admin_emails", "")
    assert settings.admin_emails == set()


def test_admin_emails_read_from_env(monkeypatch):
    monkeypatch.setenv("MAI_ADMIN_EMAILS", "Owner@Example.com")
    s = Settings(_env_file=None)
    assert s.admin_emails == {"owner@example.com"}


# ---- CRM gate ---------------------------------------------------------------

def test_crm_403_when_allowlist_empty(client, monkeypatch):
    monkeypatch.setattr(settings, "mai_admin_emails", "")
    tok = _token(client, "user@example.com")
    r = client.get("/api/crm/customers", headers=_auth(tok))
    assert r.status_code == 403


def test_crm_allows_allowlisted_email_case_insensitively(client, monkeypatch):
    monkeypatch.setattr(settings, "mai_admin_emails", "ADMIN@Example.com")
    tok = _token(client, "admin@example.com")
    r = client.get("/api/crm/customers", headers=_auth(tok))
    assert r.status_code == 200


def test_crm_403_for_email_not_on_allowlist(client, monkeypatch):
    monkeypatch.setattr(settings, "mai_admin_emails", "admin@example.com")
    tok = _token(client, "someone-else@example.com")
    r = client.get("/api/crm/customers", headers=_auth(tok))
    assert r.status_code == 403


# ---- is_admin on GET /api/account --------------------------------------------

def test_account_is_admin_true_for_allowlisted(client, monkeypatch):
    monkeypatch.setattr(settings, "mai_admin_emails", "admin@example.com")
    tok = _token(client, "admin@example.com")
    acc = client.get("/api/account", headers=_auth(tok)).json()
    assert acc["is_admin"] is True


def test_account_is_admin_false_otherwise(client, monkeypatch):
    monkeypatch.setattr(settings, "mai_admin_emails", "admin@example.com")
    tok = _token(client, "regular@example.com")
    acc = client.get("/api/account", headers=_auth(tok)).json()
    assert acc["is_admin"] is False


# ---- boot without the embedder ------------------------------------------------

def test_recall_auto_disables_without_together_key(monkeypatch):
    """No TOGETHER_API_KEY must NOT prevent boot — recall turns itself off with a warning."""
    monkeypatch.delenv("TOGETHER_API_KEY", raising=False)
    monkeypatch.setenv("SEMANTIC_RECALL_ENABLED", "true")
    s = Settings(_env_file=None)  # would have raised before; must construct cleanly
    assert s.semantic_recall_enabled is False


def test_recall_stays_on_with_together_key(monkeypatch):
    monkeypatch.setenv("TOGETHER_API_KEY", "tk-test")
    monkeypatch.setenv("SEMANTIC_RECALL_ENABLED", "true")
    s = Settings(_env_file=None)
    assert s.semantic_recall_enabled is True
