"""Internal endpoint serving Mai's CURRENT epoch public key.

In 2A this is used by Mai's own on-receipt sealer (and lets us verify rotation
live). In 2B the forked GOWA fetches it each epoch to seal-on-exit. The PRIVATE
key is never exposed; only (epoch_id, public_key_b64).
"""

from __future__ import annotations

from fastapi import APIRouter, Header, Response

from backend.config import settings
from backend.crypto.keyservice import get_key_service


def build_seal_router() -> APIRouter:
    router = APIRouter()

    @router.get("/internal/seal-pubkey")
    async def seal_pubkey(x_internal_token: str | None = Header(default=None)):
        token = settings.seal_pubkey_token
        if token:
            if x_internal_token != token:
                return Response(status_code=403, content="forbidden")
        elif settings.is_production:
            # No token configured + production => refuse rather than leak the seam.
            return Response(status_code=403, content="forbidden")
        epoch_id, public_key = get_key_service().current_public_key()
        return {"epoch_id": epoch_id, "public_key": public_key}

    return router
