"""WhatsApp dedup + self-echo guard.

Prevents Mai from replying multiple times to one tag:
- duplicate webhook deliveries of the same message id, and
- Mai reacting to its OWN replies (which come back as from_me messages and may
  even contain "@mai" when they quote the conversation).
"""

from __future__ import annotations

import hashlib

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.tenancy import WaSeenMessage


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").strip().encode()).hexdigest()[:40]


def claim(session: Session, key: str) -> bool:
    """Atomically claim a key. True = newly claimed (proceed); False = already seen."""
    if session.get(WaSeenMessage, key) is not None:
        return False
    session.add(WaSeenMessage(key=key))
    try:
        session.commit()
        return True
    except IntegrityError:  # lost a race with a concurrent delivery
        session.rollback()
        return False


def seen(session: Session, key: str) -> bool:
    return session.get(WaSeenMessage, key) is not None


def mark(session: Session, key: str) -> None:
    """Record a key (idempotent) — e.g. Mai's own sent message id / reply hash."""
    if session.get(WaSeenMessage, key) is None:
        session.add(WaSeenMessage(key=key))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
