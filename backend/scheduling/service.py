"""Scheduling service — tenant-scoped CRUD over schedules.

Create validates the spec, enforces per-user limits, persists a tenant-scoped
`Schedule`, and (best-effort) registers it with Agno's ScheduleManager so the
AgentOS poller fires it. The Mai record is the source of truth for ownership;
Agno is the trigger. All reads/writes go through `TenantScope`, so a user can
only ever see or change their own schedules.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.config import settings
from backend.db.scoped import TenantScope
from backend.logging_config import get_logger
from backend.scheduling.models import Schedule
from backend.scheduling.spec import ScheduleSpec, ScheduleValidationError

logger = get_logger(__name__)

FIRE_ENDPOINT = "/internal/schedules/fire"


class ScheduleLimitError(ScheduleValidationError):
    pass


class ScheduleService:
    def __init__(self, scope: TenantScope, *, schedule_manager: Any | None = None):
        self.scope = scope
        # Injectable for tests; lazily built from Agno otherwise.
        self._mgr = schedule_manager
        self._mgr_loaded = schedule_manager is not None

    # -- Agno trigger (best-effort) ----------------------------------------
    @property
    def manager(self) -> Any | None:
        if not self._mgr_loaded:
            try:
                from agno.scheduler import ScheduleManager

                from backend.db.agno_db import get_agno_db

                self._mgr = ScheduleManager(get_agno_db())
            except Exception as exc:  # pragma: no cover - missing server/db
                logger.warning("scheduler.manager_unavailable", extra={"ctx_error": str(exc)})
                self._mgr = None
            self._mgr_loaded = True
        return self._mgr

    def _register_with_agno(self, schedule: Schedule) -> None:
        mgr = self.manager
        if mgr is None:
            return
        try:
            mgr.create(
                name=schedule.agno_name,
                cron=self._cron_for_schedule(schedule),
                endpoint=FIRE_ENDPOINT,
                payload={"schedule_id": schedule.id},
                timezone=schedule.timezone,
                description=f"Mai schedule {schedule.id} for user {schedule.user_id}",
                if_exists="update",
            )
        except Exception as exc:  # pragma: no cover - server may be down in CLI
            logger.warning(
                "scheduler.register_failed",
                extra={"ctx_error": str(exc), "ctx_schedule_id": schedule.id},
            )

    def _cron_for_schedule(self, s: Schedule) -> str:
        if s.schedule_type == "recurring" and s.cron:
            return s.cron
        assert s.run_at is not None
        r = s.run_at
        # The DB may return run_at in UTC; the cron's wall-clock must be in the
        # schedule's own timezone (which we pass to the scheduler), or it fires
        # off by the tz offset.
        if r.tzinfo is not None:
            from zoneinfo import ZoneInfo

            r = r.astimezone(ZoneInfo(s.timezone))
        return f"{r.minute} {r.hour} {r.day} {r.month} *"

    def _deregister_from_agno(self, schedule: Schedule) -> None:
        mgr = self.manager
        if mgr is None:
            return
        try:
            mgr.delete(schedule.agno_name)
        except Exception as exc:  # pragma: no cover
            logger.warning("scheduler.delete_failed", extra={"ctx_error": str(exc)})

    # -- CRUD ---------------------------------------------------------------
    def active_count(self) -> int:
        return sum(1 for s in self.scope.all(Schedule) if s.active)

    def create(self, spec: ScheduleSpec, *, now: datetime | None = None,
               target_chat: str | None = None, target_chat_name: str | None = None) -> Schedule:
        spec.validate(now=now)
        if self.active_count() >= settings.max_active_schedules_per_user:
            raise ScheduleLimitError(
                f"you already have {settings.max_active_schedules_per_user} active "
                "schedules (the max). Cancel one first."
            )
        schedule = Schedule(
            name=spec.name.strip(),
            kind=spec.kind,
            action_text=spec.action_text.strip(),
            schedule_type=spec.schedule_type,
            timezone=spec.timezone,
            run_at=spec.run_at,
            cron=spec.cron,
            target_chat=target_chat,
            target_chat_name=target_chat_name,
            active=True,
        )
        self.scope.add(schedule)
        self.scope.flush()  # assign id
        schedule.agno_schedule_name = f"mai-sched-{schedule.id}"
        self.scope.commit()
        self._register_with_agno(schedule)
        logger.info(
            "schedule.created",
            extra={"ctx_schedule_id": schedule.id, "ctx_kind": schedule.kind},
        )
        return schedule

    def list_active(self) -> list[Schedule]:
        return [s for s in self.scope.all(Schedule) if s.active]

    def cancel(self, schedule_id: int) -> bool:
        s = self.scope.get(Schedule, schedule_id)
        if s is None or not s.active:
            return False
        s.active = False
        self.scope.commit()
        self._deregister_from_agno(s)
        logger.info("schedule.cancelled", extra={"ctx_schedule_id": s.id})
        return True

    def edit(
        self,
        schedule_id: int,
        *,
        new_spec: ScheduleSpec | None = None,
        now: datetime | None = None,
    ) -> Schedule | None:
        s = self.scope.get(Schedule, schedule_id)
        if s is None or not s.active:
            return None
        if new_spec is not None:
            new_spec.validate(now=now)
            s.name = new_spec.name.strip()
            s.kind = new_spec.kind
            s.action_text = new_spec.action_text.strip()
            s.schedule_type = new_spec.schedule_type
            s.timezone = new_spec.timezone
            s.run_at = new_spec.run_at
            s.cron = new_spec.cron
        self.scope.commit()
        self._register_with_agno(s)  # if_exists=update keeps Agno in sync
        logger.info("schedule.edited", extra={"ctx_schedule_id": s.id})
        return s
