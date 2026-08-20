"""Durable map: WhatsApp message_id -> decrypted media URL.

Inbound media arrives DECRYPTED (a gateway `statics/...` URL) only via the
webhook; the `/messages` endpoint later returns just the ENCRYPTED reference,
which we can't use. So we remember the usable URL keyed by message id, letting
`read_chat` resolve media for messages Mai has seen.

The mapping is PERSISTED to the DB (table `media_refs`) because the decrypted
statics URL is only knowable at webhook time — keeping it in memory only meant
every deploy/restart wiped it and Mai could no longer resolve previously
received media. A small in-memory dict sits in front of the DB as a fast cache.

Note on staleness: the gateway's underlying media file is itself ephemeral, so a
persisted URL may eventually 404. That's acceptable — the caller already treats a
missing/None URL as "not retrievable", and an old-but-dead row is no worse than
no row. An age-based prune (TTL) keeps the table from growing without bound.
"""

from __future__ import annotations

import logging
import threading

from backend.db.base import SessionLocal
from backend.db.base import utcnow
from backend.db.tenancy import MediaRef

logger = logging.getLogger(__name__)

# TTL after which a stored mapping is considered prunable. The gateway's media
# files outlive this only rarely, and a dead URL resolves to "not retrievable"
# anyway, so 30 days is a generous-but-bounded retention.
_TTL_DAYS = 30
# How often (in terms of cache growth) to opportunistically prune old rows. We
# avoid running a DELETE on every remember(); once every ~50 inserts is plenty.
_PRUNE_EVERY = 50

_lock = threading.Lock()
# Fast in-memory layer in front of the DB. Not a source of truth — just spares
# us a query for hot/recent ids. Survives within a process; the DB survives
# across processes.
_cache: dict[str, str] = {}


def remember(message_id: str | None, url: str | None) -> None:
    """Record a usable media URL for a message id (no-op if either is blank).

    Updates the in-memory cache, then UPSERTs the row. Best-effort: a DB failure
    here must never break inbound processing, so we log and move on.
    """
    if not message_id or not url:
        return

    with _lock:
        _cache[message_id] = url
        should_prune = len(_cache) % _PRUNE_EVERY == 0

    try:
        with SessionLocal() as s:
            row = s.get(MediaRef, message_id)
            if row is None:
                s.add(MediaRef(message_id=message_id, url=url))
            else:
                row.url = url
            if should_prune:
                _prune(s)
            s.commit()
    except Exception:  # noqa: BLE001 — persistence is best-effort, never fatal
        logger.warning("media_index.remember failed to persist %s", message_id, exc_info=True)


def lookup(message_id: str | None) -> str | None:
    """Return the remembered URL for a message id, or None if unknown.

    Checks the in-memory cache first; on a miss, reads the DB (and warms the
    cache). Never raises — any error resolves to None ("not retrievable").
    """
    if not message_id:
        return None

    with _lock:
        cached = _cache.get(message_id)
    if cached is not None:
        return cached

    try:
        with SessionLocal() as s:
            row = s.get(MediaRef, message_id)
    except Exception:  # noqa: BLE001 — a lookup failure just means "not found"
        logger.warning("media_index.lookup failed for %s", message_id, exc_info=True)
        return None

    if row is None:
        return None

    with _lock:
        _cache[message_id] = row.url
    return row.url


def _prune(s) -> None:
    """Delete mappings older than the TTL (called within an open session)."""
    from datetime import timedelta

    cutoff = utcnow() - timedelta(days=_TTL_DAYS)
    s.query(MediaRef).filter(MediaRef.created_at < cutoff).delete(synchronize_session=False)
