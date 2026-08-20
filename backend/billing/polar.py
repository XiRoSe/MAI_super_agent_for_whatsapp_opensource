"""Polar.sh billing integration (replaces Stripe).

Webhooks are the source of truth for subscription state. `apply_subscription_event`
is a pure mapping from a Polar webhook event to an `Account`'s status — unit
tested without network. Checkout/portal/webhook-verify use the Polar API and are
exercised live with real keys.

Lifecycle -> entitlement:
  active / trialing  -> active / trial   (entitled)
  past_due           -> past_due         (grace period, then cut off)
  canceled / revoked -> canceled         (not entitled)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import httpx

from backend.config import settings
from backend.db.tenancy import Account, SubscriptionStatus
from backend.logging_config import get_logger

logger = get_logger(__name__)

GRACE_DAYS = 3

_API_BASE = {
    "sandbox": "https://sandbox-api.polar.sh",
    "production": "https://api.polar.sh",
}


def _api_base() -> str:
    return _API_BASE.get(settings.polar_server, _API_BASE["sandbox"])


# --- pure lifecycle mapping (unit tested) ----------------------------------
def _status_from(event_type: str, sub: dict) -> SubscriptionStatus | None:
    polar_status = (sub.get("status") or "").lower()
    et = event_type.lower()

    if et in ("subscription.canceled", "subscription.revoked") or polar_status in (
        "canceled",
        "revoked",
    ):
        return SubscriptionStatus.canceled
    if polar_status == "past_due" or et == "subscription.past_due":
        return SubscriptionStatus.past_due
    if polar_status in ("active",) or et in ("subscription.active", "subscription.created"):
        return SubscriptionStatus.active
    if polar_status in ("trialing", "trial"):
        return SubscriptionStatus.trial
    if polar_status in ("incomplete", "incomplete_expired", "unpaid"):
        return SubscriptionStatus.past_due
    return None


def apply_subscription_event(
    account: Account,
    event_type: str,
    sub: dict,
    *,
    now: datetime | None = None,
) -> Account:
    """Apply a Polar subscription event to an account. Returns the account."""
    now = now or datetime.now(timezone.utc)
    new_status = _status_from(event_type, sub)
    if new_status is not None:
        account.status = new_status
        account.grace_until = (
            now + timedelta(days=GRACE_DAYS)
            if new_status == SubscriptionStatus.past_due
            else None
        )
    if sub.get("id"):
        account.polar_subscription_id = sub["id"]
    cust = sub.get("customer_id") or (sub.get("customer") or {}).get("id")
    if cust:
        account.polar_customer_id = cust
    logger.info(
        "billing.event_applied",
        extra={"ctx_event": event_type, "ctx_status": account.status.value,
               "ctx_account_id": account.id},
    )
    return account


# --- webhook signature (Standard Webhooks / svix-style HMAC) ---------------
def verify_webhook(raw_body: bytes, signature_header: str | None) -> bool:
    secret = settings.polar_webhook_secret
    if not secret:
        return not settings.is_production  # allow in dev, refuse in prod
    if not signature_header:
        return False
    # Standard Webhooks: header may carry multiple space-separated "v1,<sig>".
    key = secret.encode()
    expected = base64.b64encode(hmac.new(key, raw_body, hashlib.sha256).digest()).decode()
    for part in signature_header.split():
        sig = part.split(",", 1)[1] if "," in part else part
        if hmac.compare_digest(expected, sig):
            return True
    return False


# --- live API calls (need POLAR_ACCESS_TOKEN) ------------------------------
def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.polar_access_token}"}


def create_checkout(*, product_id: str, customer_email: str | None = None,
                    metadata: dict | None = None) -> str:
    """Create a Polar Checkout session; returns the hosted checkout URL."""
    settings.require("polar_access_token")
    body: dict = {"products": [product_id], "success_url": settings.polar_success_url}
    if customer_email:
        body["customer_email"] = customer_email
    if metadata:
        body["metadata"] = metadata
    resp = httpx.post(f"{_api_base()}/v1/checkouts/", json=body, headers=_headers(), timeout=20)
    resp.raise_for_status()
    return resp.json()["url"]


def customer_portal_url(customer_id: str) -> str:
    """Create a Customer Portal session for self-serve management."""
    settings.require("polar_access_token")
    resp = httpx.post(
        f"{_api_base()}/v1/customer-sessions/",
        json={"customer_id": customer_id},
        headers=_headers(),
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("customer_portal_url", "")
