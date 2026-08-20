"""Chat history endpoint contract — intentionally empty since Privacy Phase 1.

Plaintext message storage has been removed: the agent reads WhatsApp live and
RAM-only buffering (`backend.agent.runtime_memory`, written via
`backend.agent.seam.remember_turn`) is used when `account.auto_memory` is
enabled.  `get_history` is kept so the dashboard's `/chat/history` endpoint
has a stable shape, but it always returns an empty page.
"""

from __future__ import annotations

from backend.db.scoped import TenantScope


def get_history(scope: TenantScope, *, limit: int = 20, before_id: int | None = None) -> dict:
    """Return an empty page — messages are no longer persisted to the database."""
    return {"messages": [], "has_more": False, "next_cursor": None}
