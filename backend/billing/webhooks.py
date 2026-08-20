"""Polar webhook route — the source of truth for subscription state.

Verifies the signature, resolves the owning account (by metadata, subscription
id, or customer id), and applies the lifecycle change. Unknown accounts/events
are acknowledged with 200 so Polar doesn't retry forever.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from backend.billing.polar import apply_subscription_event, verify_webhook
from backend.db.base import SessionLocal
from backend.db.tenancy import Account
from backend.logging_config import get_logger

logger = get_logger(__name__)

_SUBSCRIPTION_EVENTS = {
    "subscription.created",
    "subscription.updated",
    "subscription.active",
    "subscription.canceled",
    "subscription.revoked",
    "subscription.past_due",
    "subscription.uncanceled",
}


def _find_account(session, sub: dict, metadata: dict) -> Account | None:
    account_id = metadata.get("account_id") or sub.get("metadata", {}).get("account_id")
    if account_id:
        acc = session.get(Account, int(account_id))
        if acc:
            return acc
    if sub.get("id"):
        acc = session.scalars(
            select(Account).where(Account.polar_subscription_id == sub["id"])
        ).first()
        if acc:
            return acc
    cust = sub.get("customer_id") or (sub.get("customer") or {}).get("id")
    if cust:
        return session.scalars(
            select(Account).where(Account.polar_customer_id == cust)
        ).first()
    return None


def build_polar_router() -> APIRouter:
    router = APIRouter()

    @router.post("/webhooks/polar")
    async def polar_webhook(request: Request) -> Response:
        raw = await request.body()
        sig = request.headers.get("webhook-signature") or request.headers.get("Polar-Signature")
        if not verify_webhook(raw, sig):
            logger.warning("billing.bad_signature")
            return Response(status_code=403, content="invalid signature")

        try:
            event = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            return Response(status_code=400, content="bad json")

        event_type = event.get("type", "")
        if event_type not in _SUBSCRIPTION_EVENTS:
            return Response(status_code=200, content="ignored")

        data = event.get("data", {})
        metadata = data.get("metadata", {}) or {}
        session = SessionLocal()
        try:
            account = _find_account(session, data, metadata)
            if account is None:
                logger.warning("billing.account_not_found", extra={"ctx_event": event_type})
                return Response(status_code=200, content="no account")
            apply_subscription_event(account, event_type, data)
            session.commit()
        finally:
            session.close()
        return Response(status_code=200, content="ok")

    return router
