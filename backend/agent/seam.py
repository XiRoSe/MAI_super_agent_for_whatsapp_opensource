"""The single seam where a turn is 'remembered'.

Persists NOTHING to the database; only mirrors into the RAM buffer when the
user's account has opted into auto_memory.  All five write seams in the app
(web chat, WhatsApp user/assistant, scheduled assistant) call this helper.
"""
from __future__ import annotations

from backend.agent import runtime_memory


def remember_turn(user, role: str, text: str) -> None:
    """Mirror *one* turn into the in-process RAM buffer — only when enabled.

    Defensive: safe even if ``user.account`` is ``None`` or the relationship
    has not been loaded yet (lazy-load will run if the session is open; if the
    attribute is absent we simply skip).
    """
    if getattr(getattr(user, "account", None), "auto_memory", False):
        runtime_memory.note(user.id, role, text)
