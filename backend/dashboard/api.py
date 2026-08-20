"""Dashboard REST API — everything the web frontend needs.

All routes are JWT-protected (via `get_auth_context`) and tenant-scoped: an
account can only read/write its own data. Mirrors the chat capabilities so users
can manage Mai from the browser as well as WhatsApp.
"""

from __future__ import annotations

import json
from datetime import datetime

from dateutil import parser as dtparser
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth.deps import AuthContext, get_auth_context
from backend.logging_config import get_logger

logger = get_logger(__name__)


# ----- schemas -------------------------------------------------------------
class ScheduleIn(BaseModel):
    name: str
    kind: str  # static | dynamic
    action_text: str
    schedule_type: str  # once | recurring
    run_at: str | None = None
    cron: str | None = None


class ChatIn(BaseModel):
    message: str
    history: list[dict] | None = None


class SettingsIn(BaseModel):
    timezone: str | None = None
    display_name: str | None = None


class AccountIn(BaseModel):
    auto_memory: bool | None = None


class CredentialIn(BaseModel):
    service: str            # free-form key: "google", "outlook", "notion", …
    credentials: str        # a raw key/token OR a JSON object as a string
    label: str | None = None


class CredentialUpdateIn(BaseModel):
    credentials: str | None = None   # omit/blank => keep existing secret
    label: str | None = None


class SkillIn(BaseModel):
    name: str
    description: str
    content: str


class BeliefIn(BaseModel):
    content: str


class MemoryIn(BaseModel):
    text: str
    topics: list[str] | None = None


class MemoryUpdateIn(BaseModel):
    text: str | None = None
    topics: list[str] | None = None


class SkillUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    content: str | None = None
    enabled: bool | None = None


def build_dashboard_router() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["dashboard"])

    # ----- account / usage -------------------------------------------------
    @router.get("/account")
    def account(ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.dashboard.crm import is_admin_email
        from backend.metering.usage import messages_today, tokens_this_month

        return {
            "account_id": ctx.account.id,
            "email": ctx.auth_user.email,
            "is_admin": is_admin_email(ctx.auth_user.email),
            "plan": ctx.account.plan,
            "subscription_status": ctx.account.status.value,
            "auto_memory": ctx.account.auto_memory,
            "usage": {
                "messages_today": messages_today(ctx.session, ctx.account.id),
                "tokens_30d": tokens_this_month(ctx.session, ctx.account.id),
            },
        }

    @router.patch("/account")
    def patch_account(body: AccountIn, ctx: AuthContext = Depends(get_auth_context)) -> dict:
        if body.auto_memory is not None:
            ctx.account.auto_memory = bool(body.auto_memory)
            ctx.session.commit()
        return {"auto_memory": ctx.account.auto_memory}

    @router.get("/settings")
    def get_settings_route(ctx: AuthContext = Depends(get_auth_context)) -> dict:
        u = ctx.primary_user()
        return {"timezone": u.timezone, "display_name": u.display_name}

    @router.patch("/settings")
    def patch_settings(body: SettingsIn, ctx: AuthContext = Depends(get_auth_context)) -> dict:
        u = ctx.primary_user()
        if body.timezone is not None:
            from zoneinfo import ZoneInfo

            try:
                ZoneInfo(body.timezone)
            except Exception:
                raise HTTPException(400, "invalid timezone")
            u.timezone = body.timezone
        if body.display_name is not None:
            u.display_name = body.display_name
        ctx.session.commit()
        return {"timezone": u.timezone, "display_name": u.display_name}

    # ----- WhatsApp connection (QR link) -----------------------------------
    @router.get("/whatsapp/connection")
    def wa_connection(ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.whatsapp.connections import refresh_status

        return refresh_status(ctx.session, ctx.account)

    @router.post("/whatsapp/connect")
    def wa_connect(ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.whatsapp.connections import start_link

        return start_link(ctx.session, ctx.account)

    @router.post("/whatsapp/disconnect")
    def wa_disconnect(ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.whatsapp.connections import disconnect

        return disconnect(ctx.session, ctx.account)

    # ----- schedules -------------------------------------------------------
    @router.get("/schedules")
    def list_schedules(ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.scheduling.service import ScheduleService

        from zoneinfo import ZoneInfo

        def _local_run_at(s) -> str | None:
            if not s.run_at:
                return None
            r = s.run_at
            try:
                if r.tzinfo is not None:
                    r = r.astimezone(ZoneInfo(s.timezone))
            except Exception:
                pass
            return r.isoformat()

        svc = ScheduleService(ctx.user_scope())
        return {
            "schedules": [
                {
                    "id": s.id, "name": s.name, "kind": s.kind,
                    "schedule_type": s.schedule_type, "cron": s.cron,
                    "run_at": _local_run_at(s),
                    "timezone": s.timezone, "action_text": s.action_text,
                }
                for s in svc.list_active()
            ]
        }

    @router.post("/schedules")
    def create_schedule(body: ScheduleIn, ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.scheduling.service import ScheduleService
        from backend.scheduling.spec import ScheduleSpec, ScheduleValidationError

        user = ctx.primary_user()
        if not user.timezone and not body.cron and not body.run_at:
            raise HTTPException(400, "set your timezone first")
        run_at: datetime | None = None
        if body.run_at:
            try:
                run_at = dtparser.parse(body.run_at)
            except (ValueError, OverflowError):
                raise HTTPException(400, "could not parse run_at")
        spec = ScheduleSpec(
            name=body.name, kind=body.kind, action_text=body.action_text,
            schedule_type=body.schedule_type, timezone=user.timezone or "UTC",
            run_at=run_at, cron=body.cron,
        )
        try:
            s = ScheduleService(ctx.user_scope()).create(spec)
        except ScheduleValidationError as exc:
            raise HTTPException(400, str(exc))
        return {"id": s.id, "summary": spec.human_summary()}

    @router.patch("/schedules/{schedule_id}")
    def edit_schedule(schedule_id: int, body: ScheduleIn,
                      ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.scheduling.service import ScheduleService
        from backend.scheduling.spec import ScheduleSpec, ScheduleValidationError

        user = ctx.primary_user()
        run_at: datetime | None = None
        if body.run_at:
            try:
                run_at = dtparser.parse(body.run_at)
            except (ValueError, OverflowError):
                raise HTTPException(400, "could not parse run_at")
        spec = ScheduleSpec(
            name=body.name, kind=body.kind, action_text=body.action_text,
            schedule_type=body.schedule_type, timezone=user.timezone or "UTC",
            run_at=run_at, cron=body.cron,
        )
        try:
            s = ScheduleService(ctx.user_scope()).edit(schedule_id, new_spec=spec)
        except ScheduleValidationError as exc:
            raise HTTPException(400, str(exc))
        if s is None:
            raise HTTPException(404, "no such active schedule")
        return {"id": s.id, "summary": spec.human_summary()}

    @router.delete("/schedules/{schedule_id}")
    def cancel_schedule(schedule_id: int, ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.scheduling.service import ScheduleService

        ok = ScheduleService(ctx.user_scope()).cancel(schedule_id)
        if not ok:
            raise HTTPException(404, "no such active schedule")
        return {"cancelled": schedule_id}

    # ----- credentials (skill-based integrations) --------------------------
    @router.get("/credentials")
    def list_credentials_route(ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.credentials import list_services

        return {"services": list_services(ctx.user_scope())}

    @router.post("/credentials")
    def save_credential_route(body: CredentialIn, ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.credentials import save_credential

        # Accept a JSON object or a raw key/token string.
        try:
            data = json.loads(body.credentials)
            if not isinstance(data, dict):
                data = {"value": data}
        except (json.JSONDecodeError, TypeError):
            data = {"value": body.credentials}
        save_credential(ctx.user_scope(), service=body.service, data=data, label=body.label or None)
        return {"saved": body.service.strip().lower()}

    @router.patch("/credentials/{service}")
    def update_credential_route(service: str, body: CredentialUpdateIn, ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.credentials import update_credential

        data = None
        if body.credentials is not None and body.credentials.strip() != "":
            try:
                parsed = json.loads(body.credentials)
                data = parsed if isinstance(parsed, dict) else {"value": parsed}
            except (json.JSONDecodeError, TypeError):
                data = {"value": body.credentials}
        ok = update_credential(ctx.user_scope(), service, data=data, label=body.label)
        if not ok:
            raise HTTPException(status_code=404, detail="no such service")
        return {"updated": ok}

    @router.delete("/credentials/{service}")
    def delete_credential_route(service: str, ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.credentials import delete_credential

        return {"deleted": delete_credential(ctx.user_scope(), service)}

    # ----- billing ---------------------------------------------------------
    @router.post("/billing/checkout")
    def billing_checkout(ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.config import settings
        from backend.billing.polar import create_checkout

        if not settings.polar_product_id:
            raise HTTPException(400, "billing not configured (POLAR_PRODUCT_ID)")
        url = create_checkout(
            product_id=settings.polar_product_id,
            customer_email=ctx.auth_user.email,
            metadata={"account_id": str(ctx.account.id)},
        )
        return {"checkout_url": url}

    @router.post("/billing/portal")
    def billing_portal(ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.billing.polar import customer_portal_url

        if not ctx.account.polar_customer_id:
            raise HTTPException(400, "no billing customer yet")
        return {"portal_url": customer_portal_url(ctx.account.polar_customer_id)}

    # ----- web chat playground --------------------------------------------
    @router.post("/chat")
    def chat(body: ChatIn, ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.agent.mai_agent import respond
        from backend.agent.seam import remember_turn
        from backend.billing.entitlement import entitlement_message, is_entitled
        from backend.tools.registry import build_tools

        user = ctx.primary_user()
        # Attach the already-loaded account so remember_turn never needs a lazy load.
        user.account = ctx.account
        remember_turn(user, "user", body.message)

        # Build ephemeral context from client-supplied history (last 12 valid turns).
        # Items missing 'role' or 'text' are skipped defensively.
        valid_history = [
            h for h in (body.history or [])[-12:]
            if isinstance(h, dict) and "role" in h and "text" in h
        ]
        if valid_history:
            ctx_str = "Earlier in this conversation:\n" + "\n".join(
                f"{h['role']}: {h['text']}" for h in valid_history
            )
            agent_input = f"{ctx_str}\n\n{body.message}"
        else:
            agent_input = body.message

        if not is_entitled(ctx.account):
            reply = entitlement_message()
        else:
            tools = build_tools(user, ctx.session, message=body.message)
            reply = respond(user, agent_input, tools=tools, session=ctx.session)
        remember_turn(user, "assistant", reply)
        return {"reply": reply}

    # ----- unified chat history (web + WhatsApp), paginated ----------------
    @router.get("/chat/history")
    def chat_history(
        before: int | None = None,
        limit: int = 20,
        ctx: AuthContext = Depends(get_auth_context),
    ) -> dict:
        from backend.chat_history import get_history

        return get_history(ctx.user_scope(), limit=limit, before_id=before)

    # ----- skills (per-user SKILL.md, seeded from defaults) ----------------
    def _skill_dict(s) -> dict:
        return {"id": s.id, "name": s.name, "description": s.description,
                "content": s.content, "enabled": s.enabled, "is_default": s.is_default,
                "updated_at": s.updated_at.isoformat()}

    @router.get("/skills")
    def list_skills_route(ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.skills_store import list_skills, seed_defaults_if_empty

        seed_defaults_if_empty(ctx.session, ctx.primary_user())
        return {"skills": [_skill_dict(s) for s in list_skills(ctx.user_scope())]}

    @router.post("/skills")
    def create_skill_route(body: SkillIn, ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.skills_store import create_skill

        try:
            s = create_skill(ctx.user_scope(), name=body.name, description=body.description,
                             content=body.content)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return _skill_dict(s)

    @router.patch("/skills/{skill_id}")
    def update_skill_route(skill_id: int, body: SkillUpdateIn,
                           ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.skills_store import update_skill

        s = update_skill(ctx.user_scope(), skill_id, name=body.name, description=body.description,
                         content=body.content, enabled=body.enabled)
        if s is None:
            raise HTTPException(404, "no such skill")
        return _skill_dict(s)

    @router.delete("/skills/{skill_id}")
    def delete_skill_route(skill_id: int, ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.skills_store import delete_skill

        if not delete_skill(ctx.user_scope(), skill_id):
            raise HTTPException(404, "no such skill")
        return {"deleted": skill_id}

    @router.post("/skills/reset")
    def reset_skills_route(ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.skills_store import list_skills, reset_defaults

        reset_defaults(ctx.user_scope())
        return {"skills": [_skill_dict(s) for s in list_skills(ctx.user_scope())]}

    # ----- memories CRUD (managed from the dashboard) ----------------------
    def _mem_dict(m) -> dict:
        return {
            "id": getattr(m, "memory_id", None),
            "text": getattr(m, "memory", "") or "",
            "topics": getattr(m, "topics", None) or [],
        }

    def _find_user_memory(db, uid: str, memory_id: str):
        """Return the user's memory with this id, or None — the tenant guard.

        We resolve the id ONLY within the authed user's memories, so a request
        can never read/edit/delete a memory belonging to another user.
        """
        for m in (db.get_user_memories(user_id=uid) or []):
            if str(getattr(m, "memory_id", None)) == str(memory_id):
                return m
        return None

    @router.get("/memories")
    def list_memories_route(ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.db.agno_db import get_agno_db

        uid = ctx.primary_user().agno_user_id
        try:
            mems = get_agno_db().get_user_memories(user_id=uid)
        except Exception as exc:
            logger.warning("memories.list_failed", extra={"ctx_error": str(exc)})
            return {"memories": []}
        return {"memories": [_mem_dict(m) for m in (mems or [])]}

    @router.post("/memories")
    def create_memory_route(body: MemoryIn, ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from agno.memory import UserMemory

        from backend.db.agno_db import get_agno_db

        text = (body.text or "").strip()
        if not text:
            raise HTTPException(400, "text is required")
        uid = ctx.primary_user().agno_user_id
        topics = [t.strip() for t in (body.topics or []) if t and t.strip()]
        db = get_agno_db()
        try:
            db.upsert_user_memory(UserMemory(memory=text, user_id=uid, topics=topics))
        except Exception as exc:
            raise HTTPException(400, f"couldn't save: {exc}")
        # Return the created row (resolve its id from the freshly-listed memories).
        for m in (db.get_user_memories(user_id=uid) or []):
            if (getattr(m, "memory", "") or "") == text:
                return _mem_dict(m)
        return {"id": None, "text": text, "topics": topics}

    @router.patch("/memories/{memory_id}")
    def update_memory_route(memory_id: str, body: MemoryUpdateIn,
                            ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from agno.memory import UserMemory

        from backend.db.agno_db import get_agno_db

        uid = ctx.primary_user().agno_user_id
        db = get_agno_db()
        existing = _find_user_memory(db, uid, memory_id)
        if existing is None:
            raise HTTPException(404, "no such memory")
        text = body.text.strip() if body.text is not None else (getattr(existing, "memory", "") or "")
        if not text:
            raise HTTPException(400, "text cannot be empty")
        topics = ([t.strip() for t in (body.topics or []) if t and t.strip()]
                  if body.topics is not None else (getattr(existing, "topics", None) or []))
        # Upsert in place by passing the existing memory_id (verified to update,
        # not duplicate — see agno UserMemory(memory_id=...)).
        try:
            db.upsert_user_memory(
                UserMemory(memory=text, user_id=uid, topics=topics, memory_id=memory_id)
            )
        except Exception as exc:
            raise HTTPException(400, f"couldn't update: {exc}")
        updated = _find_user_memory(db, uid, memory_id)
        return _mem_dict(updated) if updated is not None else {
            "id": memory_id, "text": text, "topics": topics}

    @router.delete("/memories/{memory_id}")
    def delete_memory_item_route(memory_id: str,
                                 ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.db.agno_db import get_agno_db

        uid = ctx.primary_user().agno_user_id
        db = get_agno_db()
        if _find_user_memory(db, uid, memory_id) is None:
            raise HTTPException(404, "no such memory")
        try:
            db.delete_user_memory(memory_id=memory_id, user_id=uid)
        except Exception as exc:
            raise HTTPException(400, f"couldn't delete: {exc}")
        return {"ok": True}

    # ----- personality (Mai's self-evolving perspective) ------------------
    @router.get("/beliefs")
    def get_beliefs_route(ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.agent.beliefs import belief_state

        return belief_state(ctx.session, ctx.primary_user())

    @router.put("/beliefs")
    def set_beliefs_route(body: BeliefIn, ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.agent.beliefs import set_beliefs

        return set_beliefs(ctx.session, ctx.primary_user(), body.content)

    @router.post("/beliefs/reset")
    def reset_beliefs_route(ctx: AuthContext = Depends(get_auth_context)) -> dict:
        from backend.agent.beliefs import reset_beliefs

        return reset_beliefs(ctx.session, ctx.primary_user())

    return router
