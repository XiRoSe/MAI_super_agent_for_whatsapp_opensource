"""In-process, RAM-only rolling buffer of recent turns. NEVER persisted.
Used only when an account has auto_memory enabled, so the belief reflection
has recent context without storing any message to disk."""
from __future__ import annotations
from collections import deque

_BUF: dict[int, deque] = {}
_MAX = 40


def note(user_id: int, role: str, text: str) -> None:
    if not text:
        return
    _BUF.setdefault(user_id, deque(maxlen=_MAX)).append((role, text))


def recent_text(user_id: int, limit: int = 12) -> str:
    buf = _BUF.get(user_id)
    if not buf:
        return ""
    items = list(buf)[-limit:]
    return "\n".join(f"{r}: {t}" for r, t in items)


def clear(user_id: int) -> None:
    _BUF.pop(user_id, None)
