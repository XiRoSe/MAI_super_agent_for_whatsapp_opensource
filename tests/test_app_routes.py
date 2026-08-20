"""Integration test: the full AgentOS app boots and Mai's routes respond.

Uses a dummy model key (no network — the client is only constructed) and
disables the scheduler poller so the test is fast and hermetic.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from backend.config import settings


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "model_provider", "together")
    monkeypatch.setattr(settings, "together_api_key", "dummy-key")
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(settings, "whatsapp_verify_token", "verifyme")
    from backend.app import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


def test_whatsapp_verification_handshake(client):
    r = client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "verifyme", "hub.challenge": "999"},
    )
    assert r.status_code == 200 and r.text == "999"

    bad = client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "999"},
    )
    assert bad.status_code == 403


def test_admin_requires_token(client):
    assert client.get("/admin/tenants").status_code == 403


def test_internal_fire_endpoint_exists(client):
    # Unknown schedule id -> handled gracefully (skipped), route is wired.
    r = client.post("/internal/schedules/fire", json={"schedule_id": 999999})
    assert r.status_code == 200
