"""Google OAuth for the Drive knowledge base (drive.file scope).

WhatsApp can't host an OAuth dance, so Mai sends the user a link:
    /oauth/google/start?s=<signed state>  ->  Google consent  ->  /oauth/google/callback

The state is a short-lived signed JWT naming the tenant, so the callback knows
whose vault to store the token set in — the URL itself carries no identity the
user could tamper with.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.auth.security import _jwt_secret
from backend.config import settings
from backend.credentials import save_credential
from backend.db.scoped import TenantScope
from backend.db.tenancy import User
from backend.gdrive import SCOPE, SERVICE
from backend.logging_config import get_logger

logger = get_logger(__name__)

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_STATE_TTL_SECONDS = 900


def is_configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def _redirect_uri() -> str:
    return settings.google_redirect_uri or f"{settings.public_base_url}/oauth/google/callback"


def _encode_state(user: User) -> str:
    payload = {"acct": user.account_id, "uid": user.id, "purpose": SERVICE,
               "exp": int(time.time()) + _STATE_TTL_SECONDS}
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def _decode_state(state: str) -> dict | None:
    try:
        payload = jwt.decode(state, _jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return payload if payload.get("purpose") == SERVICE else None


def connect_url(user: User) -> str:
    """The link Mai hands the user in chat to connect their Drive."""
    return f"{settings.public_base_url}/oauth/google/start?s={_encode_state(user)}"


def _exchange_code(code: str) -> dict[str, Any]:
    resp = httpx.post(_TOKEN_URL, timeout=20, data={
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": _redirect_uri(),
    })
    resp.raise_for_status()
    return resp.json()


def build_google_oauth_router() -> APIRouter:
    from backend.db.base import SessionLocal

    router = APIRouter()

    @router.get("/oauth/google/start", include_in_schema=False)
    async def start(s: str) -> Any:
        if not is_configured() or _decode_state(s) is None:
            return HTMLResponse("This connect link is invalid or expired — ask Mai "
                                "for a fresh one.", status_code=400)
        params = httpx.QueryParams({
            "client_id": settings.google_client_id,
            "redirect_uri": _redirect_uri(),
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",   # we need a refresh token
            "prompt": "consent",        # re-consent re-issues the refresh token
            "state": s,
        })
        return RedirectResponse(f"{_AUTH_URL}?{params}")

    @router.get("/oauth/google/callback", include_in_schema=False)
    async def callback(state: str = "", code: str = "", error: str = "") -> Any:
        claims = _decode_state(state)
        if claims is None or error or not code:
            logger.warning("gdrive.oauth_rejected", extra={"ctx_error": error or "bad state"})
            return HTMLResponse("Google connect didn't complete — ask Mai for a fresh "
                                "link and try again.", status_code=400)
        tokens = _exchange_code(code)
        data = {
            "access_token": tokens.get("access_token", ""),
            "refresh_token": tokens.get("refresh_token", ""),
            "expires_at": time.time() + int(tokens.get("expires_in", 0)),
        }
        if not data["refresh_token"]:
            return HTMLResponse("Google didn't grant offline access — ask Mai for a "
                                "fresh link and approve all requested access.",
                                status_code=400)
        with SessionLocal() as session:
            scope = TenantScope(session, account_id=claims["acct"], user_id=claims["uid"])
            save_credential(scope, service=SERVICE, data=data, label="Google Drive KB")
        logger.info("gdrive.connected", extra={"ctx_user_id": claims["uid"]})
        return HTMLResponse("<h3>Google Drive connected ✔</h3><p>You can close this "
                            "tab and go back to WhatsApp.</p>")

    return router
