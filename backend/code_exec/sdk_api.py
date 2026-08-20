"""Internal endpoint the sandbox's mai SDK calls back into."""

from __future__ import annotations

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse

from backend.code_exec.dispatch import DispatchError, dispatch
from backend.code_exec.token_store import token_store
from backend.db.base import SessionLocal
from backend.logging_config import get_logger

logger = get_logger(__name__)


def build_sdk_router() -> APIRouter:
    router = APIRouter()

    @router.post("/internal/sdk/{fn}")
    async def call(fn: str, request: Request,
                   authorization: str | None = Header(default=None)) -> Response:
        token = (authorization or "").removeprefix("Bearer ").strip()
        ctx = token_store.get(token)
        if ctx is None:
            return JSONResponse({"error": "invalid token"}, status_code=401)
        try:
            args = await request.json()
        except Exception:
            args = {}
        session = SessionLocal()
        try:
            result = dispatch(fn, args or {}, ctx, session, token)
            return JSONResponse({"result": result})
        except DispatchError as exc:
            return JSONResponse({"error": exc.message}, status_code=400)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("sdk.error", extra={"ctx_fn": fn, "ctx_error": str(exc)})
            return JSONResponse({"error": "internal error"}, status_code=500)
        finally:
            session.close()

    return router
