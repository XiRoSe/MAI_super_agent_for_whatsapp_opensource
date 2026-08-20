"""Minimal admin/ops visibility — list tenants with subscription + usage.

Not a full admin UI/RBAC (out of scope), but enough to operate: who's signed up,
their subscription status, and current usage. Gated by a bearer token.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Header, Response
from sqlalchemy import select

from backend.config import settings
from backend.db.base import SessionLocal
from backend.db.tenancy import Account, User
from backend.metering.usage import messages_today, tokens_this_month


def tenant_overview(session, *, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    from backend.db.tenancy import AuthUser

    accounts = session.scalars(select(Account)).all()
    rows = []
    for acc in accounts:
        users = session.scalars(select(User).where(User.account_id == acc.id)).all()
        auths = session.scalars(select(AuthUser).where(AuthUser.account_id == acc.id)).all()
        rows.append(
            {
                "account_id": acc.id,
                "plan": acc.plan,
                "status": acc.status.value,
                "auth_emails": [a.email for a in auths],
                "users": [
                    {"id": u.id, "whatsapp": u.whatsapp_number,
                     "agno_user_id": u.agno_user_id, "tz": u.timezone}
                    for u in users
                ],
                "messages_today": messages_today(session, acc.id, now=now),
                "tokens_30d": tokens_this_month(session, acc.id, now=now),
            }
        )
    return rows


def build_admin_router() -> APIRouter:
    router = APIRouter()

    @router.get("/admin/tenants")
    async def tenants(authorization: str | None = Header(default=None)):
        token = settings.mai_admin_token
        if not token:
            return Response(status_code=403, content="admin disabled (set MAI_ADMIN_TOKEN)")
        if authorization != f"Bearer {token}":
            return Response(status_code=403, content="forbidden")
        session = SessionLocal()
        try:
            return {"tenants": tenant_overview(session)}
        finally:
            session.close()

    return router
