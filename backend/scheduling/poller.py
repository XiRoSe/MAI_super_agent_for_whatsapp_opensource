"""In-process schedule poller.

Replaces reliance on Agno's scheduler (which called an internal HTTP endpoint and
was hard to observe). A daemon thread wakes every `scheduler_poll_interval` seconds,
finds DUE schedules across all tenants, and fires them via `fire_schedule_by_id`
(which re-scopes to the owning tenant and delivers via THAT tenant's own WhatsApp).
Idempotency inside `fire_schedule_by_id` prevents double-firing within a minute.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select

from backend.config import settings
from backend.db.base import SessionLocal
from backend.logging_config import get_logger, new_trace_id
from backend.scheduling.jobs import fire_schedule_by_id
from backend.scheduling.models import Schedule

logger = get_logger(__name__)
_started = False
_lock = threading.Lock()


def _entitled(session, schedule) -> bool:
    from backend.billing.entitlement import is_entitled
    from backend.db.tenancy import Account

    acc = session.get(Account, schedule.account_id)
    return acc is not None and is_entitled(acc)


def due_schedules(session, now: datetime) -> list[Schedule]:
    """Active schedules whose time has come (one-off past run_at, or cron match)."""
    out = []
    for s in session.execute(select(Schedule).where(Schedule.active.is_(True))).scalars():
        try:
            if s.schedule_type == "once":
                if s.run_at and s.run_at <= now:
                    out.append(s)
            elif s.cron:
                tz = ZoneInfo(s.timezone or "UTC")
                if croniter.match(s.cron, now.astimezone(tz)):
                    out.append(s)
        except Exception as exc:  # a bad cron/tz must not stall the whole poll
            logger.warning("scheduler.due_check_failed",
                           extra={"ctx_schedule_id": s.id, "ctx_error": str(exc)})
    return out


def poll_once(now: datetime | None = None) -> int:
    """One poll cycle. Returns how many schedules actually fired."""
    now = now or datetime.now(timezone.utc)
    session = SessionLocal()
    fired = 0
    try:
        due = due_schedules(session, now)
        if not due:
            return 0
        new_trace_id("poll")
        for s in due:
            try:
                if fire_schedule_by_id(session, s.id, now=now, is_entitled=_entitled):
                    fired += 1
            except Exception as exc:  # one bad schedule shouldn't break the cycle
                logger.exception("scheduler.fire_error",
                                 extra={"ctx_schedule_id": s.id, "ctx_error": str(exc)})
        logger.info("scheduler.poll", extra={"ctx_due": len(due), "ctx_fired": fired})
    finally:
        session.close()
    return fired


def start_poller() -> None:
    """Start the background poll loop once per process."""
    global _started
    with _lock:
        if _started or not settings.scheduler_enabled:
            return
        _started = True

    def _loop() -> None:
        interval = max(5, settings.scheduler_poll_interval)
        logger.info("scheduler.started", extra={"ctx_interval": interval})
        while True:
            try:
                poll_once()
            except Exception as exc:  # never let the loop die
                logger.exception("scheduler.loop_error", extra={"ctx_error": str(exc)})
            time.sleep(interval)

    threading.Thread(target=_loop, name="mai-scheduler", daemon=True).start()
