"""AgentOS application — the production FastAPI app.

Wires together:
- Agno AgentOS (sessions/memory/db) with the scheduler poller enabled.
- The WhatsApp webhook routes (inbound -> per-tenant reply).
- An internal schedule-fire endpoint the scheduler calls at fire time, which
  runs the job inside its owner's tenant context (entitlement-gated).

A base "mai" agent is registered so AgentOS has an agent to serve; real
conversations run per-user via `respond()` with that user's id/memory.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request, Response

from backend.config import settings
from backend.db.agno_db import get_agno_db
from backend.db.base import SessionLocal, init_db
from backend.db.tenancy import Account
from backend.logging_config import get_logger
from backend.scheduling.jobs import fire_schedule_by_id
from backend.scheduling.models import Schedule

logger = get_logger(__name__)


def _build_base_agent() -> Any:
    from agno.agent import Agent

    from backend.models.factory import get_model
    from backend.skills_loader import get_skills

    return Agent(
        name="mai",
        id="mai",
        model=get_model(),
        db=get_agno_db(),
        skills=get_skills(),
        telemetry=False,
    )


def _entitled(session, schedule: Schedule) -> bool:
    from backend.billing.entitlement import is_entitled

    account = session.get(Account, schedule.account_id)
    return account is not None and is_entitled(account)


def _internal_router() -> APIRouter:
    router = APIRouter()

    @router.post("/internal/schedules/fire")
    async def fire(request: Request, x_internal_token: str | None = Header(default=None)) -> Response:
        # If an internal token is configured, require it (scheduler -> app auth).
        expected = settings.whatsapp_app_secret or None  # reuse a secret if set
        if expected and x_internal_token != expected:
            return Response(status_code=403, content="forbidden")
        body = await request.json()
        schedule_id = int(body.get("schedule_id", 0))
        session = SessionLocal()
        try:
            fired = fire_schedule_by_id(session, schedule_id, is_entitled=_entitled)
        finally:
            session.close()
        return Response(status_code=200, content="fired" if fired else "skipped")

    return router


def create_app():
    """Build and return the FastAPI app for uvicorn."""
    init_db()
    from agno.os import AgentOS

    from backend.whatsapp.webhook import build_whatsapp_router

    import os

    # The scheduler poller calls the fire endpoint over HTTP on the local port.
    # Railway injects $PORT; fall back to the configured port locally.
    port = int(os.environ.get("PORT", settings.port))
    # We run our OWN in-process scheduler poller (observable, no internal HTTP hop),
    # so Agno's scheduler is disabled to avoid a second, opaque firing path.
    agent_os = AgentOS(
        name="Mai",
        agents=[_build_base_agent()],
        db=get_agno_db(),
        scheduler=False,
        scheduler_base_url=f"http://127.0.0.1:{port}",
    )
    from fastapi.middleware.cors import CORSMiddleware

    from backend.admin import build_admin_router
    from backend.auth.router import build_auth_router
    from backend.billing.webhooks import build_polar_router
    from backend.dashboard.api import build_dashboard_router
    from backend.dashboard.crm import build_crm_router

    app = agent_os.get_app()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",")] if settings.cors_origins != "*" else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(build_auth_router())
    app.include_router(build_dashboard_router())
    app.include_router(build_crm_router())
    app.include_router(build_whatsapp_router())
    app.include_router(build_polar_router())
    app.include_router(build_admin_router())
    app.include_router(_internal_router())
    from backend.crypto.api import build_seal_router
    app.include_router(build_seal_router())
    from backend.code_exec.sdk_api import build_sdk_router
    app.include_router(build_sdk_router())
    from backend.oauth_google import build_google_oauth_router
    app.include_router(build_google_oauth_router())
    _mount_spa(app)

    # Start the in-process schedule poller (fires due schedules into their channel).
    from backend.scheduling.poller import start_poller

    start_poller()

    from backend.crypto.purge import start_purge_daemon
    start_purge_daemon()

    logger.info("app.ready", extra={"ctx_provider": settings.model_provider})
    return app


def _static_dir():
    from pathlib import Path

    # Built frontend is copied to ./static (CWD = /app in the container).
    for cand in (Path.cwd() / "static", Path(__file__).resolve().parents[1] / "static"):
        if cand.exists():
            return cand
    return None


def _mount_spa(app) -> None:
    """Serve the built React dashboard from the same origin (no CORS needed)."""
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    static_dir = _static_dir()
    if static_dir is None:
        logger.info("spa.none")
        return
    from starlette.routing import Route

    index = static_dir / "index.html"
    assets = static_dir / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    async def _index(_request):
        return FileResponse(str(index))

    # AgentOS owns "/" via a lazily-included router, so an exact "/" route added
    # at the FRONT wins for the root without shadowing any API path.
    app.router.routes.insert(0, Route("/", _index, methods=["GET"]))

    # Catch-all registered LAST: real API/docs/webhook routes match first; any
    # other path serves the SPA so client-side routing works on refresh.
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        candidate = static_dir / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(index))

    logger.info("spa.mounted", extra={"ctx_dir": str(static_dir)})


# uvicorn entrypoint: `uvicorn mai.app:app`
# Built lazily via a factory so importing this module never requires a model key.
def __getattr__(name: str):  # pragma: no cover - import-time convenience
    if name == "app":
        return create_app()
    raise AttributeError(name)
