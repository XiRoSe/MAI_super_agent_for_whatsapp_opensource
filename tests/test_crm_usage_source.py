"""CRM aggregates (messages_sent, last_conversation) come from UsageEvent, not ChatMessage.

Privacy Phase 1, Task 6: The CRM must derive all per-customer message counts and
timestamps from usage_events (which stores counts/timestamps, not content).
No ChatMessage rows should be needed for the CRM to return correct aggregates.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from starlette.testclient import TestClient

from backend.db.tenancy import AuthUser, UsageEvent, UsageKind

ADMIN_EMAIL = "admin@example.com"


@pytest.fixture(autouse=True)
def _admin_allowlist(monkeypatch):
    """The CRM allowlist is env-driven (MAI_ADMIN_EMAILS); grant our test email."""
    from backend.config import settings

    monkeypatch.setattr(settings, "mai_admin_emails", ADMIN_EMAIL)


@pytest.fixture()
def crm_client(session):
    from backend.auth.deps import get_db
    from backend.auth.router import build_auth_router
    from backend.dashboard.crm import build_crm_router

    app = FastAPI()
    app.include_router(build_auth_router())
    app.include_router(build_crm_router())

    def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    return TestClient(app), session


def _register_and_login(client, email=ADMIN_EMAIL, password="longenough"):
    r = client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_crm_messages_sent_from_usage_events(crm_client):
    """messages_sent per account = sum(quantity) where kind=wa_message_in."""
    client, session = crm_client

    # Register the admin account.
    token = _register_and_login(client)

    # Resolve the account that was just registered (it has the admin email).
    au = session.query(AuthUser).filter_by(email=ADMIN_EMAIL).one()
    account_id = au.account_id

    # Seed usage events: 2 inbound messages (quantity 1 each) + 1 outbound reply.
    t1 = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2025, 6, 2, 12, 0, 0, tzinfo=timezone.utc)
    t3 = datetime(2025, 6, 3, 14, 0, 0, tzinfo=timezone.utc)

    session.add(UsageEvent(account_id=account_id, user_id=None,
                           kind=UsageKind.wa_message_in, quantity=1, created_at=t1))
    session.add(UsageEvent(account_id=account_id, user_id=None,
                           kind=UsageKind.wa_message_in, quantity=1, created_at=t2))
    session.add(UsageEvent(account_id=account_id, user_id=None,
                           kind=UsageKind.wa_message_out, quantity=1, created_at=t3))
    session.commit()

    r = client.get("/api/crm/customers", headers=_auth(token))
    assert r.status_code == 200, r.text
    data = r.json()

    customers = data["customers"]
    assert len(customers) == 1
    c = customers[0]

    # messages_sent = 2 inbound (wa_message_in × 2 rows of quantity=1)
    assert c["messages_sent"] == 2, f"Expected 2, got {c['messages_sent']}"

    # replies = 1 outbound (wa_message_out × 1 row of quantity=1)
    assert c["replies"] == 1, f"Expected 1, got {c['replies']}"

    # last_conversation = max across both kinds = t3
    assert c["last_conversation"] is not None
    # The datetime is ISO-formatted; the max timestamp should include t3's date.
    assert "2025-06-03" in c["last_conversation"], (
        f"Expected last_conversation near {t3}, got {c['last_conversation']}"
    )

    # Summary aggregate: total_messages_sent = sum of inbound across all accounts.
    assert data["summary"]["total_messages_sent"] == 2


def test_crm_last_conversation_max_across_directions(crm_client):
    """last_conversation = max(created_at) over both wa_message_in AND wa_message_out."""
    client, session = crm_client

    token = _register_and_login(client)
    au = session.query(AuthUser).filter_by(email=ADMIN_EMAIL).one()
    account_id = au.account_id

    # The outbound reply is the LATEST event — it must win.
    t_in = datetime(2025, 5, 10, 9, 0, 0, tzinfo=timezone.utc)
    t_out = datetime(2025, 5, 15, 18, 0, 0, tzinfo=timezone.utc)

    session.add(UsageEvent(account_id=account_id, user_id=None,
                           kind=UsageKind.wa_message_in, quantity=1, created_at=t_in))
    session.add(UsageEvent(account_id=account_id, user_id=None,
                           kind=UsageKind.wa_message_out, quantity=1, created_at=t_out))
    session.commit()

    r = client.get("/api/crm/customers", headers=_auth(token))
    assert r.status_code == 200, r.text
    c = r.json()["customers"][0]

    assert c["last_conversation"] is not None
    assert "2025-05-15" in c["last_conversation"], (
        f"Expected 2025-05-15 (the outbound date), got {c['last_conversation']}"
    )


def test_crm_zero_messages_when_no_usage_events(crm_client):
    """An account with no UsageEvent rows shows messages_sent=0, last_conversation=None."""
    client, session = crm_client

    token = _register_and_login(client)

    r = client.get("/api/crm/customers", headers=_auth(token))
    assert r.status_code == 200, r.text
    c = r.json()["customers"][0]

    assert c["messages_sent"] == 0
    assert c["replies"] == 0
    assert c["last_conversation"] is None


def test_crm_does_not_depend_on_chat_messages_table(crm_client):
    """CRM returns correct counts even when chat_messages table is empty.

    This verifies the aggregates are sourced from usage_events, not chat_messages.
    If the CRM still reads ChatMessage, this test would return 0 despite seeded
    UsageEvent rows.
    """
    client, session = crm_client

    token = _register_and_login(client)
    au = session.query(AuthUser).filter_by(email=ADMIN_EMAIL).one()
    account_id = au.account_id

    # Seed ONLY UsageEvent rows — no ChatMessage rows at all.
    session.add(UsageEvent(account_id=account_id, user_id=None,
                           kind=UsageKind.wa_message_in, quantity=3,
                           created_at=datetime(2025, 7, 1, 10, 0, tzinfo=timezone.utc)))
    session.commit()

    # Confirm chat_messages table is genuinely empty.
    count = session.execute(text("SELECT COUNT(*) FROM chat_messages")).scalar()
    assert count == 0, "chat_messages must be empty for this test to be meaningful"

    r = client.get("/api/crm/customers", headers=_auth(token))
    assert r.status_code == 200, r.text
    c = r.json()["customers"][0]

    # If CRM reads ChatMessage, this would be 0; if it reads UsageEvent correctly, it's 3.
    assert c["messages_sent"] == 3, (
        f"CRM still reads ChatMessage instead of UsageEvent! Got {c['messages_sent']}"
    )
