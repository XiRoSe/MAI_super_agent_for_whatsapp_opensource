"""WhatsApp webhook routes (mounted on the AgentOS FastAPI app).

GET  /webhooks/whatsapp  — Meta subscription verification (hub.challenge).
POST /webhooks/whatsapp  — inbound messages; signature is validated before any
                           processing, and a bad signature is rejected with 403.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Request, Response

from backend.config import settings
from backend.db.base import SessionLocal
from backend.logging_config import get_logger
from backend.whatsapp import get_provider
from backend.whatsapp.router import handle_inbound_messages

logger = get_logger(__name__)


def _process_inbound(messages: list) -> None:
    """Run the turn(s) in the background so the webhook can ack instantly."""
    session = SessionLocal()
    try:
        handle_inbound_messages(session, messages)
    except Exception:  # pragma: no cover - a turn failure must not crash the worker
        logger.exception("wa.background_failed")
    finally:
        session.close()


def build_whatsapp_router() -> APIRouter:
    router = APIRouter()

    @router.get("/webhooks/whatsapp")
    async def verify(request: Request) -> Response:
        params = request.query_params
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token")
        challenge = params.get("hub.challenge", "")
        if mode == "subscribe" and token and token == settings.whatsapp_verify_token:
            return Response(content=challenge, media_type="text/plain")
        return Response(status_code=403, content="verification failed")

    @router.post("/webhooks/whatsapp")
    async def inbound(request: Request, background: BackgroundTasks) -> Response:
        raw = await request.body()
        signature = request.headers.get("X-Hub-Signature-256")
        provider = get_provider()
        if not provider.verify_signature(raw, signature):
            logger.warning("wa.bad_signature")
            return Response(status_code=403, content="invalid signature")

        try:
            payload = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            return Response(status_code=400, content="bad json")

        messages = provider.parse_inbound(payload)
        if messages:
            # Process AFTER responding so the gateway gets an instant 200 and never
            # times out / retries while a turn (model + tools) is running.
            background.add_task(_process_inbound, messages)
        return Response(status_code=200, content="ok")

    return router
