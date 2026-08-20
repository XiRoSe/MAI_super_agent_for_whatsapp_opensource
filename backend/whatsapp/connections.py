"""Per-tenant WhatsApp connection lifecycle (QR link / status / disconnect).

Each account owns one `WhatsAppConnection` (its own number, linked via the
unofficial gateway). These functions drive the QR onboarding the dashboard
shows. The connection is REAL: it creates a device on the gateway, serves the
gateway's live QR, and reconciles status (and the linked phone number) from the
gateway. QR codes expire (~30s) — the dashboard calls `start_link` again to get
a fresh one until the device reports connected.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.db.tenancy import Account, WhatsAppConnection, WhatsAppConnectionStatus
from backend.logging_config import get_logger

logger = get_logger(__name__)


def _session_id_for(account: Account) -> str:
    # = the GOWA device_id. Underscores (like upsailor's "upsailor_{id}").
    return f"mai_acct_{account.id}"


def get_or_create_connection(session: Session, account: Account) -> WhatsAppConnection:
    conn = session.get(WhatsAppConnection, account.id)
    if conn is None:
        conn = WhatsAppConnection(
            account_id=account.id,
            session_id=_session_id_for(account),
            status=WhatsAppConnectionStatus.pending,
        )
        session.add(conn)
        session.commit()
    return conn


def _gateway():
    """Return the unofficial gateway provider, or None if not applicable."""
    from backend.whatsapp import get_provider

    provider = get_provider()
    if not getattr(provider, "unofficial", False) or not hasattr(provider, "get_login"):
        return None
    return provider


def start_link(session: Session, account: Account) -> dict:
    """Ensure the device exists and return a FRESH QR for the user to scan.

    If the device is already connected, report that instead. Never fabricates a
    connecting state — if the gateway is unreachable it returns an error.
    """
    conn = get_or_create_connection(session, account)
    provider = _gateway()
    if provider is None:
        return {"session_id": conn.session_id, "status": "error",
                "error": "WhatsApp gateway is not configured."}

    # If already linked, reconcile and short-circuit.
    if provider.session_status(conn.session_id) == "connected":
        return refresh_status(session, account)

    provider.create_session(conn.session_id)  # idempotent
    login = provider.get_login(conn.session_id)
    if not login or not login.get("qr"):
        return {"session_id": conn.session_id, "status": "error",
                "error": "Couldn't reach the WhatsApp gateway to generate a QR. Try again."}

    conn.status = WhatsAppConnectionStatus.connecting
    session.commit()
    logger.info("wa.link_started", extra={"ctx_account_id": account.id})
    return {
        "session_id": conn.session_id,
        "status": conn.status.value,
        "qr": login["qr"],
        "qr_duration": login.get("qr_duration", 30),
    }


def refresh_status(session: Session, account: Account) -> dict:
    """Poll the gateway and reconcile connection status + linked number."""
    conn = get_or_create_connection(session, account)
    provider = _gateway()
    if provider is None:
        return {"session_id": conn.session_id, "status": conn.status.value,
                "phone_number": conn.phone_number}

    status = provider.session_status(conn.session_id)
    if status == "connected":
        if conn.status != WhatsAppConnectionStatus.connected:
            conn.status = WhatsAppConnectionStatus.connected
            conn.connected_at = datetime.now(timezone.utc)
        phone = provider.device_phone(conn.session_id)
        if phone and conn.phone_number != phone:
            conn.phone_number = phone
        session.commit()
        # First-run onboarding: a one-time welcome to the freshly-linked owner (best-effort,
        # gated by conn.welcome_sent so it fires exactly once across status polls).
        from backend.onboarding import maybe_send_welcome

        maybe_send_welcome(session, account, conn)
    elif status == "disconnected" and conn.status == WhatsAppConnectionStatus.connected:
        conn.status = WhatsAppConnectionStatus.disconnected
        session.commit()

    return {"session_id": conn.session_id, "status": conn.status.value,
            "phone_number": conn.phone_number}


def disconnect(session: Session, account: Account) -> dict:
    conn = get_or_create_connection(session, account)
    provider = _gateway()
    if provider is not None:
        provider.logout(conn.session_id)
    conn.status = WhatsAppConnectionStatus.disconnected
    conn.phone_number = None
    session.commit()
    return {"session_id": conn.session_id, "status": conn.status.value}
