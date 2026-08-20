"""Short-lived per-turn tokens that map a sandbox run to ONE tenant.

In-process store (we run a single uvicorn worker). The sandbox holds only the
opaque token; the tenant identity is resolved here, server-side, so sandboxed
code can never name another tenant. Also tracks the per-run send count for the
hard send cap.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from backend.config import settings


@dataclass
class TokenContext:
    account_id: int
    user_id: int
    device_id: str
    outbound_allowed: bool
    send_count: int
    expires_at: float
    current_chat: str | None = None  # the chat this turn is in (for replies/media to "here")


class TokenStore:
    def __init__(self, ttl_seconds: int | None = None, send_cap: int | None = None) -> None:
        self._ttl = ttl_seconds if ttl_seconds is not None else settings.code_exec_token_ttl_seconds
        self._cap = send_cap if send_cap is not None else settings.code_exec_send_cap
        self._store: dict[str, TokenContext] = {}

    def mint(self, account_id: int, user_id: int, device_id: str,
             outbound_allowed: bool = False, current_chat: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        self._store[token] = TokenContext(
            account_id=account_id, user_id=user_id, device_id=device_id,
            outbound_allowed=outbound_allowed, current_chat=current_chat,
            send_count=0, expires_at=time.monotonic() + self._ttl,
        )
        return token

    def get(self, token: str) -> TokenContext | None:
        ctx = self._store.get(token)
        if ctx is None:
            return None
        if time.monotonic() > ctx.expires_at:
            self._store.pop(token, None)
            return None
        return ctx

    def spend_send(self, token: str) -> bool:
        ctx = self.get(token)
        if ctx is None or ctx.send_count >= self._cap:
            return False
        ctx.send_count += 1
        return True


token_store = TokenStore()
