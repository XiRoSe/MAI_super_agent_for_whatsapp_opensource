"""First-run onboarding: the moment a tenant links their WhatsApp, Mai sends a one-time
welcome to the owner's OWN number — proving it works without the user having to ask.

Drives the REAL connection lifecycle (`refresh_status` -> `maybe_send_welcome` -> `deliver`)
against a fake unofficial gateway, so the only substitution is the WhatsApp transport. Grades
the actual send + the once-only gate."""

from __future__ import annotations

import pytest

from backend.db.tenancy import (Account, SubscriptionStatus, WhatsAppConnection,
                                WhatsAppConnectionStatus)
from backend.provisioning import get_or_create_primary_user
from backend.whatsapp.connections import refresh_status

OWNER_NUMBER = "+972500000000"


class _FakeGateway:
    """A linked unofficial gateway: reports `connected` + the device's number, records text sends.
    `unofficial=True` + `get_login` is what connections._gateway() requires to treat it as real."""

    unofficial = True

    def __init__(self, number: str = OWNER_NUMBER) -> None:
        self.number = number
        self.sent: list[tuple[str, str]] = []  # (to, text)

    def get_login(self, session_id):
        return {"qr": None}

    def session_status(self, session_id):
        return "connected"

    def device_phone(self, session_id):
        return self.number

    def create_session(self, session_id):
        ...

    def send_text(self, to, text, session_id=None):
        self.sent.append((to, text))


@pytest.fixture()
def account(session):
    acct = Account(plan="beta", status=SubscriptionStatus.active)
    session.add(acct)
    session.flush()
    get_or_create_primary_user(session, acct)  # the dashboard's placeholder owner (pending:)
    session.commit()
    return acct


def test_first_connect_sends_welcome_to_owner_once(session, account, monkeypatch):
    gw = _FakeGateway()
    monkeypatch.setattr("backend.whatsapp.get_provider", lambda: gw)

    # First poll after the user scans the QR: status -> connected, welcome fires.
    refresh_status(session, account)

    assert len(gw.sent) == 1, "exactly one welcome on first connect"
    to, text = gw.sent[0]
    assert "972500000000" in to, "welcome went to the owner's OWN number (self-chat)"
    assert "Mai" in text

    conn = session.get(WhatsAppConnection, account.id)
    assert conn.status == WhatsAppConnectionStatus.connected
    assert conn.welcome_sent is True

    # Every later poll is still 'connected' — the welcome must NOT re-send.
    refresh_status(session, account)
    refresh_status(session, account)
    assert len(gw.sent) == 1, "welcome is idempotent across polls"


def test_no_welcome_when_never_connected(session, account, monkeypatch):
    class _NeverConnects(_FakeGateway):
        def session_status(self, session_id):
            return "connecting"

    gw = _NeverConnects()
    monkeypatch.setattr("backend.whatsapp.get_provider", lambda: gw)

    refresh_status(session, account)

    assert gw.sent == [], "no welcome until the link actually completes"
    conn = session.get(WhatsAppConnection, account.id)
    assert conn.welcome_sent is False
