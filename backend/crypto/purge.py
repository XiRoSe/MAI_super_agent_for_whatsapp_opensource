"""Periodic belt-and-suspenders cleanup for the encrypted store.

Deletes sealed rows older than the retention window AND crypto-shreds epoch keys
older than 4 days (after which that ciphertext is permanently unrecoverable).
Runs in a daemon thread, mirroring the schedule poller's lifecycle.
"""

from __future__ import annotations

import threading
import time

from backend.config import settings
from backend.crypto.keyservice import get_key_service
from backend.db.base import SessionLocal
from backend.logging_config import get_logger, new_trace_id
from backend.whatsapp import encstore

logger = get_logger(__name__)

_started = False
_lock = threading.Lock()
_INTERVAL_SECONDS = 3600  # hourly


def purge_once() -> int:
    new_trace_id("purge")
    session = SessionLocal()
    try:
        purged = encstore.purge_expired(session, within_days=settings.mai_seal_retention_days)
    finally:
        session.close()
    get_key_service().shred_expired()
    logger.info("encstore.purge", extra={"ctx_purged": purged})
    return purged


def start_purge_daemon() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True

    def _loop() -> None:
        try:
            get_key_service().warm()  # load current+previous keys; publish current pubkey
        except Exception as exc:
            logger.warning("keyservice.warm_failed", extra={"ctx_error": str(exc)})
        logger.info("encstore.purge_started", extra={"ctx_interval": _INTERVAL_SECONDS})
        while True:
            try:
                purge_once()
            except Exception as exc:  # never let the loop die
                logger.exception("encstore.purge_loop_error", extra={"ctx_error": str(exc)})
            time.sleep(_INTERVAL_SECONDS)

    threading.Thread(target=_loop, name="mai-encstore-purge", daemon=True).start()
