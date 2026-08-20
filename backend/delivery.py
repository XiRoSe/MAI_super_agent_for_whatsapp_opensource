"""Outbound delivery — the single place a message goes out to a user.

Enforces WhatsApp's 24-hour customer-service window for PROACTIVE sends:
- Inside the window (user messaged within 24h): free-form text is fine.
- Outside the window: a proactive send MUST use an approved utility template,
  which is a metered, billable event (see metering + templates slices).
Replies to a user's own message are never "proactive" and are always free-form.

If no WhatsApp provider is configured (e.g. CLI/dev), it logs the message so
flows are fully exercisable offline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.db.tenancy import User, WhatsAppWindow
from backend.logging_config import get_logger

logger = get_logger(__name__)

WINDOW = timedelta(hours=24)


def within_service_window(session: Session, user: User, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    win = session.get(WhatsAppWindow, user.id)
    if win is None or win.last_inbound_at is None:
        return False
    last = win.last_inbound_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last) <= WINDOW


def mark_inbound(session: Session, user: User, *, now: datetime | None = None) -> None:
    """Open/refresh the 24h window when the user messages Mai."""
    now = now or datetime.now(timezone.utc)
    win = session.get(WhatsAppWindow, user.id)
    if win is None:
        win = WhatsAppWindow(user_id=user.id, account_id=user.account_id, last_inbound_at=now)
        session.add(win)
    else:
        win.last_inbound_at = now
    session.commit()


def _account_session_id(session: Session, user: User) -> str | None:
    """The tenant's own gateway session (per-tenant/unofficial topology)."""
    from backend.db.tenancy import WhatsAppConnection

    conn = session.get(WhatsAppConnection, user.account_id)
    return conn.session_id if conn else None


def deliver(
    session: Session,
    user: User,
    text: str,
    *,
    proactive: bool = False,
    now: datetime | None = None,
) -> None:
    """Send `text` to `user`.

    Unofficial gateways (GOWA/WAHA): no 24h window or templates — always plain
    text from the tenant's own session. Meta (official): proactive out-of-window
    sends use an approved billable template.
    """
    from backend.metering.usage import record_usage  # local import avoids cycle
    from backend.db.tenancy import UsageKind

    provider = _provider()
    session_id = _account_session_id(session, user)

    # Unofficial gateway: no window/template machinery.
    if provider is not None and getattr(provider, "unofficial", False):
        _send_freeform(session, user, text, session_id=session_id)
        record_usage(session, account_id=user.account_id, user_id=user.id,
                     kind=UsageKind.wa_message_out, quantity=1)
        return

    in_window = within_service_window(session, user, now=now)
    if proactive and not in_window:
        # Out-of-window proactive send -> approved template, billable.
        _send_template(session, user, text, session_id=session_id)
        record_usage(session, account_id=user.account_id, user_id=user.id,
                     kind=UsageKind.wa_template_out, quantity=1)
        return

    _send_freeform(session, user, text, session_id=session_id)
    record_usage(session, account_id=user.account_id, user_id=user.id,
                 kind=UsageKind.wa_message_out, quantity=1)


def _provider():
    """Return a configured WhatsApp provider, or None for CLI/dev."""
    try:
        from backend.whatsapp import get_provider

        return get_provider()
    except Exception:
        return None


def _send_freeform(session: Session, user: User, text: str, *, session_id: str | None = None) -> None:
    provider = _provider()
    if provider is None:
        logger.info("deliver.freeform", extra={"ctx_user_id": user.id, "ctx_preview": text[:40]})
        return
    provider.send_text(user.whatsapp_number, text, session_id=session_id)


def _send_template(session: Session, user: User, text: str, *, session_id: str | None = None) -> None:
    from backend.whatsapp.templates import render_proactive_template

    provider = _provider()
    template = render_proactive_template(text)
    if provider is None:
        logger.info(
            "deliver.template",
            extra={"ctx_user_id": user.id, "ctx_template": template.name, "ctx_preview": text[:40]},
        )
        return
    provider.send_template(user.whatsapp_number, template, session_id=session_id)
