"""Delivery + 24h window + proactive template metering tests."""

from __future__ import annotations

from backend.db.tenancy import UsageKind
from backend.delivery import deliver, mark_inbound, within_service_window
from backend.metering.usage import _sum_usage
from datetime import datetime, timedelta, timezone


def _count(session, account_id, kind):
    return _sum_usage(session, account_id=account_id, kind=kind,
                      since=datetime.now(timezone.utc) - timedelta(days=2))


def test_proactive_outside_window_uses_billable_template(session, two_tenants):
    _, u1, _, _ = two_tenants
    # No inbound recorded -> outside the 24h window.
    assert within_service_window(session, u1) is False
    deliver(session, u1, "Your morning brief ☀️", proactive=True)
    # Billable template send is metered.
    assert _count(session, u1.account_id, UsageKind.wa_template_out) == 1
    assert _count(session, u1.account_id, UsageKind.wa_message_out) == 0


def test_proactive_inside_window_is_freeform(session, two_tenants):
    _, u1, _, _ = two_tenants
    mark_inbound(session, u1)  # opens the window
    assert within_service_window(session, u1) is True
    deliver(session, u1, "hello again", proactive=True)
    assert _count(session, u1.account_id, UsageKind.wa_message_out) == 1
    assert _count(session, u1.account_id, UsageKind.wa_template_out) == 0


def test_reply_is_always_freeform(session, two_tenants):
    _, u1, _, _ = two_tenants
    deliver(session, u1, "a reply", proactive=False)
    assert _count(session, u1.account_id, UsageKind.wa_message_out) == 1
