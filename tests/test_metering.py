"""Usage metering + quota enforcement tests."""

from __future__ import annotations

from backend.config import settings
from backend.db.tenancy import UsageKind
from backend.metering.usage import (
    check_quota,
    messages_today,
    record_usage,
    tokens_this_month,
)


def test_message_and_token_metering_accumulates(session, two_tenants):
    a1, u1, a2, u2 = two_tenants
    record_usage(session, account_id=a1.id, user_id=u1.id, kind=UsageKind.wa_message_out, quantity=3)
    record_usage(session, account_id=a1.id, user_id=u1.id, kind=UsageKind.tokens_in, quantity=100)
    record_usage(session, account_id=a1.id, user_id=u1.id, kind=UsageKind.tokens_out, quantity=50)
    # Other tenant unaffected (per-account aggregation).
    record_usage(session, account_id=a2.id, user_id=u2.id, kind=UsageKind.wa_message_out, quantity=9)

    assert messages_today(session, a1.id) == 3
    assert tokens_this_month(session, a1.id) == 150
    assert messages_today(session, a2.id) == 9


def test_quota_blocks_when_over_message_limit(session, two_tenants):
    a1, u1, _, _ = two_tenants
    settings.quota_messages_per_day = 2
    try:
        record_usage(session, account_id=a1.id, user_id=u1.id, kind=UsageKind.wa_message_out, quantity=2)
        status = check_quota(session, a1.id)
        assert not status
        assert "message" in status.reason
    finally:
        settings.quota_messages_per_day = 200


def test_quota_ok_when_under_limit(session, two_tenants):
    a1, u1, _, _ = two_tenants
    record_usage(session, account_id=a1.id, user_id=u1.id, kind=UsageKind.wa_message_out, quantity=1)
    assert bool(check_quota(session, a1.id)) is True
