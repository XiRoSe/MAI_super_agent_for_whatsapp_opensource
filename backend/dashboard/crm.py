"""Admin-only CRM — the platform owner's view of every customer.

A single read-only endpoint that aggregates, per account, who the customer is
and how they've been using Mai (messages, schedules, skills, memory depth,
token spend, last seen). Gated to the platform owner's email; everyone else
gets a 403. All aggregates use grouped queries (no N+1).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Integer, func, select

from backend.auth.deps import AuthContext, get_auth_context
from backend.config import settings
from backend.db.tenancy import (
    AgentBelief,
    Account,
    AuthUser,
    ErrorReport,
    Skill,
    UsageEvent,
    UsageKind,
    User,
)
from backend.scheduling.models import Schedule


def is_admin_email(email: str | None) -> bool:
    """True when `email` is on the MAI_ADMIN_EMAILS allowlist (platform operators).

    The allowlist comes from the MAI_ADMIN_EMAILS env var (comma-separated); empty
    means NO admins. Enforced server-side (403 for anyone else); the frontend only
    mirrors it for nav visibility via `is_admin` on GET /api/account.
    """
    return (email or "").strip().lower() in settings.admin_emails


class ReportMessageOut(BaseModel):
    role: str
    text: str
    ts: str = ""


class ReportToolCallOut(BaseModel):
    name: str
    args: dict = {}
    result: str | None = None
    error: bool = False


class ReportOut(BaseModel):
    """One user-approved error report, as the admin CRM sees it (readable fields only —
    the encrypted raw session is never returned, just flagged by `has_session`)."""

    id: int
    account_id: int
    customer_email: str | None
    surface: str | None
    chat_type: str | None
    contact_number: str | None
    chat_jid: str | None
    command: str
    problem: str                      # the model's read (stored as `summary`)
    mai_expected: str | None
    mai_notes: str | None
    transcript: list[ReportMessageOut]
    tool_calls: list[ReportToolCallOut]
    schedule: dict | None
    model_id: str | None
    git_rev: str | None
    has_session: bool                 # whether the encrypted replay fixture is attached
    status: str
    created_at: str | None


class ReportsOut(BaseModel):
    """The /crm/reports response: every report, newest first."""

    reports: list[ReportOut]


def require_admin_email(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if not is_admin_email(ctx.auth_user.email):
        raise HTTPException(status_code=403, detail="admin only")
    return ctx


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _as_aware(dt: datetime | None) -> datetime | None:
    """Normalize naive timestamps to UTC so comparisons never raise."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def build_crm_router() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["crm"])

    @router.get("/crm/customers")
    def list_customers(ctx: AuthContext = Depends(require_admin_email)) -> dict:
        s = ctx.session
        now = datetime.now(timezone.utc)
        token_since = now - timedelta(days=30)

        accounts = list(s.scalars(select(Account)).all())

        # AuthUser by account_id (login identity: email + name).
        auth_by_acct: dict[int, AuthUser] = {}
        for au in s.scalars(select(AuthUser)).all():
            # keep the earliest per account (one per account for MVP)
            cur = auth_by_acct.get(au.account_id)
            if cur is None or (au.created_at and cur.created_at and au.created_at < cur.created_at):
                auth_by_acct[au.account_id] = au

        # Primary User per account = earliest created_at.
        primary_user: dict[int, User] = {}
        for u in s.scalars(select(User).order_by(User.created_at.asc())).all():
            if u.account_id not in primary_user:
                primary_user[u.account_id] = u

        # Messages: grouped sum(quantity) + max(created_at) per (account, kind)
        # sourced from UsageEvent — no stored message text is read here.
        msgs_sent: dict[int, int] = {}
        replies: dict[int, int] = {}
        last_conv: dict[int, datetime] = {}
        rows = s.execute(
            select(
                UsageEvent.account_id,
                UsageEvent.kind,
                func.coalesce(func.sum(UsageEvent.quantity), 0),
                func.max(UsageEvent.created_at),
            )
            .where(UsageEvent.kind.in_([UsageKind.wa_message_in, UsageKind.wa_message_out]))
            .group_by(UsageEvent.account_id, UsageEvent.kind)
        ).all()
        for account_id, kind, cnt, last in rows:
            if kind == UsageKind.wa_message_in:
                msgs_sent[account_id] = int(cnt or 0)
            elif kind == UsageKind.wa_message_out:
                replies[account_id] = int(cnt or 0)
            last = _as_aware(last)
            if last is not None:
                prev = last_conv.get(account_id)
                if prev is None or last > prev:
                    last_conv[account_id] = last

        # Schedules: total + active per account.
        sched_total: dict[int, int] = {}
        sched_active: dict[int, int] = {}
        for account_id, total, active in s.execute(
            select(
                Schedule.account_id,
                func.count(),
                func.coalesce(func.sum(func.cast(Schedule.active, Integer)), 0),
            ).group_by(Schedule.account_id)
        ).all():
            sched_total[account_id] = int(total or 0)
            sched_active[account_id] = int(active or 0)

        # Skills: total per account.
        skills_total: dict[int, int] = {}
        for account_id, total in s.execute(
            select(Skill.account_id, func.count()).group_by(Skill.account_id)
        ).all():
            skills_total[account_id] = int(total or 0)

        # Beliefs: memory depth (msg_count) per account.
        belief_depth: dict[int, int] = {}
        for account_id, depth in s.execute(
            select(AgentBelief.account_id, func.max(AgentBelief.msg_count)).group_by(
                AgentBelief.account_id
            )
        ).all():
            belief_depth[account_id] = int(depth or 0)

        # Tokens in the last 30 days per account.
        tokens_30d: dict[int, int] = {}
        for account_id, total in s.execute(
            select(UsageEvent.account_id, func.coalesce(func.sum(UsageEvent.quantity), 0))
            .where(UsageEvent.kind.in_([UsageKind.tokens_in, UsageKind.tokens_out]))
            .where(UsageEvent.created_at >= token_since)
            .group_by(UsageEvent.account_id)
        ).all():
            tokens_30d[account_id] = int(total or 0)

        customers = []
        for acct in accounts:
            au = auth_by_acct.get(acct.id)
            pu = primary_user.get(acct.id)
            wa = pu.whatsapp_number if pu else None
            name = (
                (au.display_name if au else None)
                or (pu.display_name if pu else None)
                or wa
                or (au.email if au else None)
            )
            last = last_conv.get(acct.id)
            customers.append(
                {
                    "account_id": acct.id,
                    "email": au.email if au else None,
                    "name": name,
                    "whatsapp_number": wa,
                    "timezone": pu.timezone if pu else None,
                    "plan": acct.plan,
                    "status": acct.status.value if acct.status else None,
                    "joined": _iso(acct.created_at),
                    "messages_sent": msgs_sent.get(acct.id, 0),
                    "replies": replies.get(acct.id, 0),
                    "schedules": sched_total.get(acct.id, 0),
                    "schedules_active": sched_active.get(acct.id, 0),
                    "skills": skills_total.get(acct.id, 0),
                    "memory_depth": belief_depth.get(acct.id, 0),
                    "tokens_30d": tokens_30d.get(acct.id, 0),
                    "last_conversation": _iso(last),
                }
            )

        # Sort by last_conversation desc, nulls last.
        customers.sort(
            key=lambda c: c["last_conversation"] or "",
            reverse=True,
        )

        active_cutoff = now - timedelta(days=7)
        active_7d = sum(
            1
            for acct in accounts
            if (last_conv.get(acct.id) is not None and last_conv[acct.id] >= active_cutoff)
        )

        summary = {
            "total_customers": len(accounts),
            "active_7d": active_7d,
            "total_messages_sent": sum(msgs_sent.values()),
            "total_schedules": sum(sched_total.values()),
            "total_skills": sum(skills_total.values()),
        }

        return {"summary": summary, "customers": customers}

    @router.get("/crm/reports", response_model=ReportsOut)
    def list_reports(ctx: AuthContext = Depends(require_admin_email)) -> ReportsOut:
        """Every user-approved error report, newest first — the raw material for new evals.

        Pair with `python -m evals.from_report <id>` to turn one into a Scenario skeleton.
        """
        s = ctx.session
        # Exclude "pending" rows — those are previews staged but not yet approved by the user.
        reports = list(
            s.scalars(
                select(ErrorReport)
                .where(ErrorReport.status != "pending")
                .order_by(ErrorReport.created_at.desc())
                .limit(200)
            ).all()
        )
        # Customer email per account for context (one AuthUser per account for MVP).
        email_by_acct: dict[int, str] = {}
        for au in s.scalars(select(AuthUser)).all():
            email_by_acct.setdefault(au.account_id, au.email)

        def _loads(blob: str | None, default):
            try:
                return json.loads(blob) if blob else default
            except Exception:
                return default

        return ReportsOut(
            reports=[
                ReportOut(
                    id=r.id,
                    account_id=r.account_id,
                    customer_email=email_by_acct.get(r.account_id),
                    surface=r.surface,
                    chat_type=r.chat_type,
                    contact_number=r.contact_number,
                    chat_jid=r.chat_jid,
                    command=r.command,
                    problem=r.summary,
                    mai_expected=r.mai_expected,
                    mai_notes=r.mai_notes,
                    transcript=[ReportMessageOut(**m) for m in _loads(r.transcript_json, [])],
                    tool_calls=[ReportToolCallOut(**t) for t in _loads(r.tool_calls_json, [])],
                    schedule=_loads(r.schedule_json, None),
                    model_id=r.model_id,
                    git_rev=r.git_rev,
                    has_session=bool(r.raw_session_enc),
                    status=r.status,
                    created_at=_iso(r.created_at),
                )
                for r in reports
            ]
        )

    return router
