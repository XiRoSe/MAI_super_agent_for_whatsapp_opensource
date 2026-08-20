"""Per-tenant usage metering and quota enforcement.

Meters agent runs, model tokens (in/out), and WhatsApp messages (especially
billable templates). `record_usage` appends an immutable `UsageEvent`; quota
checks aggregate the current window and decide whether to degrade gracefully.

Quotas protect unit economics: at the limit we tell the user and stop spending
rather than silently overspending.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.config import settings
from backend.db.tenancy import UsageEvent, UsageKind
from backend.logging_config import get_logger

logger = get_logger(__name__)


def record_usage(
    session: Session,
    *,
    account_id: int,
    user_id: int | None,
    kind: UsageKind,
    quantity: int = 1,
    meta: dict | None = None,
) -> None:
    session.add(
        UsageEvent(
            account_id=account_id,
            user_id=user_id,
            kind=kind,
            quantity=quantity,
            meta=json.dumps(meta) if meta else None,
        )
    )
    session.commit()


def _sum_usage(
    session: Session, *, account_id: int, kind: UsageKind, since: datetime
) -> int:
    stmt = (
        select(func.coalesce(func.sum(UsageEvent.quantity), 0))
        .where(UsageEvent.account_id == account_id)
        .where(UsageEvent.kind == kind)
        .where(UsageEvent.created_at >= since)
    )
    return int(session.scalar(stmt) or 0)


def messages_today(session: Session, account_id: int, *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=1)
    return sum(
        _sum_usage(session, account_id=account_id, kind=k, since=since)
        for k in (UsageKind.wa_message_out, UsageKind.wa_template_out)
    )


def tokens_this_month(session: Session, account_id: int, *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=30)
    return sum(
        _sum_usage(session, account_id=account_id, kind=k, since=since)
        for k in (UsageKind.tokens_in, UsageKind.tokens_out)
    )


class QuotaStatus:
    def __init__(self, ok: bool, reason: str = ""):
        self.ok = ok
        self.reason = reason

    def __bool__(self) -> bool:
        return self.ok


def check_quota(session: Session, account_id: int, *, now: datetime | None = None) -> QuotaStatus:
    """Return whether the account is under its plan limits."""
    if messages_today(session, account_id, now=now) >= settings.quota_messages_per_day:
        return QuotaStatus(False, "daily message limit reached")
    if tokens_this_month(session, account_id, now=now) >= settings.quota_tokens_per_month:
        return QuotaStatus(False, "monthly token budget reached")
    return QuotaStatus(True)
