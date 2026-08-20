"""Account/User provisioning — the system-level operations that run BEFORE a
tenant scope exists (you can't scope to a tenant you haven't created yet).

Everything here is deliberately narrow: create an account, create/resolve a user
by WhatsApp number, set a user's timezone. All *per-user data* access after this
point must go through `TenantScope`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.tenancy import Account, SubscriptionStatus, User, WhatsAppWindow
from backend.logging_config import get_logger

logger = get_logger(__name__)


def create_account(
    session: Session,
    *,
    plan: str = "beta",
    status: SubscriptionStatus = SubscriptionStatus.trial,
    polar_customer_id: str | None = None,
) -> Account:
    account = Account(plan=plan, status=status, polar_customer_id=polar_customer_id)
    session.add(account)
    session.flush()
    logger.info("account.created", extra={"ctx_account_id": account.id, "ctx_plan": plan})
    return account


def resolve_user_by_whatsapp(session: Session, whatsapp_number: str) -> User | None:
    """Route an inbound message to its tenant by sender number."""
    return session.scalars(
        select(User).where(User.whatsapp_number == whatsapp_number)
    ).first()


def get_or_create_user(
    session: Session,
    *,
    whatsapp_number: str,
    account: Account | None = None,
    display_name: str | None = None,
    timezone: str | None = None,
) -> tuple[User, bool]:
    """Resolve a user by WhatsApp number, creating an account+user if new.

    For MVP each new WhatsApp identity gets its own account (1 user/account).
    Returns (user, created).
    """
    existing = resolve_user_by_whatsapp(session, whatsapp_number)
    if existing:
        return existing, False

    if account is None:
        account = create_account(session)

    user = User(
        account_id=account.id,
        whatsapp_number=whatsapp_number,
        agno_user_id=User.make_agno_user_id(account.id, whatsapp_number),
        display_name=display_name,
        timezone=timezone,
    )
    session.add(user)
    session.flush()
    session.add(WhatsAppWindow(user_id=user.id, account_id=account.id))
    session.commit()
    logger.info(
        "user.created",
        extra={"ctx_account_id": account.id, "ctx_user_id": user.id},
    )
    return user, True


def get_or_create_primary_user(session: Session, account: Account) -> User:
    """The account's primary WhatsApp identity (owner).

    In the per-tenant-number model the dashboard needs a User to own user-scoped
    data (schedules, integrations) before a real number is linked. We create one
    with a placeholder number that is reconciled to the real number on connect.
    """
    existing = session.scalars(
        select(User).where(User.account_id == account.id)
    ).first()
    if existing:
        return existing
    placeholder = f"pending:acct{account.id}"
    user = User(
        account_id=account.id,
        whatsapp_number=placeholder,
        agno_user_id=User.make_agno_user_id(account.id, placeholder),
        display_name="Owner",
    )
    session.add(user)
    session.flush()
    session.add(WhatsAppWindow(user_id=user.id, account_id=account.id))
    session.commit()
    return user


def _bare_number(s: str) -> str:
    """Reduce a number/JID to just its digits, e.g. '972...@s.whatsapp.net' -> '972...'."""
    return "".join(ch for ch in s.split("@", 1)[0] if ch.isdigit())


def resolve_account_by_session(session: Session, session_id: str) -> Account | None:
    """Map an inbound gateway identifier to its owning account.

    The gateway reports the webhook's device id as the LINKED PHONE NUMBER (a JID),
    not the custom device id we assigned. So we match by: (1) the custom device id,
    (2) the connection's stored linked phone, then (3) a gateway lookup that maps the
    JID to its device and backfills the phone so later messages match cheaply.
    """
    from backend.db.tenancy import WhatsAppConnection

    # 1. exact custom device id ("mai_acct_N")
    conn = session.scalars(
        select(WhatsAppConnection).where(WhatsAppConnection.session_id == session_id)
    ).first()

    # 2. by linked phone number
    if conn is None:
        num = _bare_number(session_id)
        if num:
            for c in session.scalars(select(WhatsAppConnection)).all():
                if c.phone_number and _bare_number(c.phone_number) == num:
                    conn = c
                    break

    # 3. gateway fallback: find the device whose JID matches, backfill its phone
    if conn is None:
        conn = _resolve_via_gateway(session, session_id)

    return session.get(Account, conn.account_id) if conn else None


def _resolve_via_gateway(session: Session, session_id: str):
    from backend.db.tenancy import WhatsAppConnection

    num = _bare_number(session_id)
    if not num:
        return None
    try:
        from backend.whatsapp import get_provider

        provider = get_provider()
        if not hasattr(provider, "list_devices"):
            return None
        for dev in provider.list_devices():
            if _bare_number(str(dev.get("jid", ""))) == num:
                c = session.scalars(
                    select(WhatsAppConnection).where(
                        WhatsAppConnection.session_id == dev.get("id")
                    )
                ).first()
                if c is not None:
                    c.phone_number = "+" + num
                    session.commit()
                    logger.info("wa.session_backfilled",
                                extra={"ctx_account_id": c.account_id})
                    return c
    except Exception as exc:  # pragma: no cover - gateway optional
        logger.warning("wa.resolve_gateway_failed", extra={"ctx_error": str(exc)})
    return None


def get_or_create_user_in_account(
    session: Session, *, account: Account, whatsapp_number: str
) -> tuple[User, bool]:
    """Resolve/create a sender (User) WITHIN a given account (per-tenant topology).

    Reconciles the account's pending placeholder owner to the real number on the
    first inbound, so the dashboard's primary user and the chatting user are one.
    """
    existing = session.scalars(
        select(User).where(User.account_id == account.id)
        .where(User.whatsapp_number == whatsapp_number)
    ).first()
    if existing:
        return existing, False

    placeholder = session.scalars(
        select(User).where(User.account_id == account.id)
        .where(User.whatsapp_number.like("pending:%"))
    ).first()
    if placeholder is not None:
        placeholder.whatsapp_number = whatsapp_number
        placeholder.agno_user_id = User.make_agno_user_id(account.id, whatsapp_number)
        session.commit()
        return placeholder, False

    user = User(
        account_id=account.id,
        whatsapp_number=whatsapp_number,
        agno_user_id=User.make_agno_user_id(account.id, whatsapp_number),
    )
    session.add(user)
    session.flush()
    session.add(WhatsAppWindow(user_id=user.id, account_id=account.id))
    session.commit()
    return user, True


def set_user_timezone(session: Session, user: User, tz: str) -> None:
    user.timezone = tz
    session.commit()
    logger.info("user.timezone_set", extra={"ctx_user_id": user.id, "ctx_tz": tz})
