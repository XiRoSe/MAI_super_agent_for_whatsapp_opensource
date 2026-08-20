"""Polar billing + entitlement tests."""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from backend.billing.entitlement import is_entitled
from backend.billing.polar import apply_subscription_event, verify_webhook
from backend.config import settings
from backend.db.tenancy import Account, SubscriptionStatus


def _acct():
    return Account(plan="beta", status=SubscriptionStatus.trial)


def test_active_event_makes_entitled():
    acc = _acct()
    apply_subscription_event(acc, "subscription.active", {"id": "sub_1", "status": "active"})
    assert acc.status == SubscriptionStatus.active
    assert acc.polar_subscription_id == "sub_1"
    assert is_entitled(acc) is True


def test_canceled_event_pauses_service():
    acc = _acct()
    apply_subscription_event(acc, "subscription.canceled", {"status": "canceled"})
    assert acc.status == SubscriptionStatus.canceled
    assert is_entitled(acc) is False


def test_past_due_sets_grace_and_then_cuts_off():
    acc = _acct()
    now = datetime.now(timezone.utc)
    apply_subscription_event(acc, "subscription.updated", {"status": "past_due"}, now=now)
    assert acc.status == SubscriptionStatus.past_due
    assert acc.grace_until is not None
    # within grace -> entitled
    assert is_entitled(acc, now=now + timedelta(days=1)) is True
    # after grace -> not entitled
    assert is_entitled(acc, now=now + timedelta(days=10)) is False


def test_customer_id_captured_from_event():
    acc = _acct()
    apply_subscription_event(
        acc, "subscription.created",
        {"id": "sub_x", "status": "active", "customer_id": "cus_42"},
    )
    assert acc.polar_customer_id == "cus_42"


def test_webhook_signature_verification():
    settings.polar_webhook_secret = "whsec"
    try:
        body = b'{"type":"subscription.active"}'
        digest = hmac.new(b"whsec", body, hashlib.sha256).digest()
        good = "v1," + base64.b64encode(digest).decode()
        assert verify_webhook(body, good) is True
        assert verify_webhook(body, "v1,bad") is False
        assert verify_webhook(body, None) is False
    finally:
        settings.polar_webhook_secret = ""
