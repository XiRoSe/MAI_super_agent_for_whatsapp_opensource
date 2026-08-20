"""First-run onboarding: a one-time welcome the moment a tenant links their WhatsApp.

Linking (the QR scan completing) is a SYSTEM event, not an agent task — so the welcome is
fixed product copy delivered through the normal proactive send path (`backend.delivery.deliver`),
gated to fire exactly once via `WhatsAppConnection.welcome_sent`. Keeping it in this small seam
(rather than inline in the connection-status code) means the connection layer stays about
connections and onboarding stays about onboarding.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.db.tenancy import Account, WhatsAppConnection
from backend.logging_config import get_logger

logger = get_logger(__name__)

WELCOME_MESSAGE = (
    "👋 Hey! I'm Mai — your personal assistant, now live in your WhatsApp. This message is me, "
    "sent to your own chat to prove it works. Tag @Mai in any chat to put me to work — set "
    "reminders, search the web, make or edit images, summarize a conversation, and more. "
    "Try it whenever you like!"
)


def maybe_send_welcome(session: Session, account: Account, conn: WhatsAppConnection) -> bool:
    """Send the one-time welcome to the freshly-linked owner — exactly once. Returns True iff it
    sent this call. Best-effort: a failure is logged and never breaks the caller's connection flow,
    and `welcome_sent` stays False so a later poll retries. Targets the owner's OWN number (their
    self-chat): we reconcile the account's placeholder owner to the just-linked number (the same
    reconciliation the first inbound does), so `deliver` sends to a real number, not `pending:`."""
    if conn.welcome_sent or not conn.phone_number:
        return False

    from backend.delivery import deliver
    from backend.provisioning import get_or_create_user_in_account

    try:
        user, _ = get_or_create_user_in_account(
            session, account=account, whatsapp_number=conn.phone_number
        )
        deliver(session, user, WELCOME_MESSAGE, proactive=True)
    except Exception as exc:  # pragma: no cover - send is best-effort
        logger.warning("onboarding.welcome_failed", extra={"ctx_error": str(exc)})
        return False

    conn.welcome_sent = True
    session.commit()
    logger.info("onboarding.welcome_sent", extra={"ctx_account_id": account.id})
    return True
