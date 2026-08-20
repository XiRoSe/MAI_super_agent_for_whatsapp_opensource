"""Entitlement — the single gate for whether a tenant gets service.

`is_entitled(account)` gates BOTH the agent loop and scheduled jobs. No active
subscription => Mai stops responding and that tenant's jobs are paused, so we
never keep spending model + WhatsApp money on non-payers. `past_due` keeps
service during a grace window (dunning), then cuts off.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.db.tenancy import Account, SubscriptionStatus


def is_entitled(account: Account, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    status = account.status
    if status in (SubscriptionStatus.active, SubscriptionStatus.trial):
        return True
    if status == SubscriptionStatus.past_due:
        # Grace period: entitled until grace_until passes.
        if account.grace_until is None:
            return True
        grace = account.grace_until
        if grace.tzinfo is None:
            grace = grace.replace(tzinfo=timezone.utc)
        return now <= grace
    # canceled (or anything else) -> not entitled.
    return False


def entitlement_message() -> str:
    return (
        "Your Mai subscription isn't active right now, so I've paused. "
        "Reactivate any time and I'll pick right back up where we left off."
    )
