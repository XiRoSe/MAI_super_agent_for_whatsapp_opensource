"""Structured schedule spec + validation.

The MODEL parses natural language ("every weekday at 8am") into a structured
spec via the scheduling tool's typed arguments. THIS module validates that spec
before anything is saved — a hallucinated or abusive schedule never reaches the
store. Timezone-aware throughout: a bare "8am" is rejected without a known tz.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from backend.config import settings

ScheduleKind = Literal["static", "dynamic"]
ScheduleType = Literal["once", "recurring"]


class ScheduleValidationError(ValueError):
    """Raised when a proposed schedule is invalid or abusive."""


@dataclass
class ScheduleSpec:
    name: str
    kind: ScheduleKind  # static = fixed message; dynamic = re-run agent at fire time
    action_text: str  # the message (static) or the instruction (dynamic)
    schedule_type: ScheduleType
    timezone: str
    run_at: datetime | None = None  # for one-off (interpreted in `timezone`)
    cron: str | None = None  # for recurring (5-field cron, evaluated in `timezone`)

    # -- validation ---------------------------------------------------------
    def validate(self, *, now: datetime | None = None) -> "ScheduleSpec":
        now = now or datetime.now(timezone.utc)
        if not self.name or not self.name.strip():
            raise ScheduleValidationError("schedule needs a short name")
        if not self.action_text or not self.action_text.strip():
            raise ScheduleValidationError("schedule needs a message or instruction")
        if self.kind not in ("static", "dynamic"):
            raise ScheduleValidationError("kind must be 'static' or 'dynamic'")
        tz = _require_timezone(self.timezone)

        if self.schedule_type == "once":
            if self.run_at is None:
                raise ScheduleValidationError("a one-off schedule needs a run_at time")
            run_at = self.run_at
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=tz)
                self.run_at = run_at
            if run_at <= now:
                raise ScheduleValidationError("run_at must be in the future")
        elif self.schedule_type == "recurring":
            if not self.cron:
                raise ScheduleValidationError("a recurring schedule needs a cron expression")
            _validate_cron(self.cron, tz)
        else:
            raise ScheduleValidationError("schedule_type must be 'once' or 'recurring'")
        return self

    def next_run(self, *, after: datetime | None = None) -> datetime:
        tz = ZoneInfo(self.timezone)
        after = after or datetime.now(tz)
        if self.schedule_type == "once":
            assert self.run_at is not None
            return self.run_at
        itr = croniter(self.cron, after.astimezone(tz))
        return itr.get_next(datetime)

    def human_summary(self) -> str:
        """A confirmation string echoed back to the user before saving."""
        what = "send" if self.kind == "static" else "run and send the result of"
        when = (
            f"once at {self.run_at:%a %d %b %Y, %H:%M} ({self.timezone})"
            if self.schedule_type == "once" and self.run_at
            else f"on schedule '{self.cron}' ({self.timezone})"
        )
        return f"I'll {what} “{self.action_text}” {when}."


def _require_timezone(tz_name: str | None) -> ZoneInfo:
    if not tz_name:
        raise ScheduleValidationError(
            "I need your timezone before I can schedule this — what timezone are you in?"
        )
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleValidationError(f"unknown timezone: {tz_name!r}") from exc


def _validate_cron(cron: str, tz: ZoneInfo) -> None:
    if not croniter.is_valid(cron):
        raise ScheduleValidationError(f"invalid cron expression: {cron!r}")
    # Reject abusive frequencies: compute the gap between the next two fires and
    # ensure it's >= the configured minimum interval.
    base = datetime(2024, 1, 1, tzinfo=tz)
    itr = croniter(cron, base)
    first = itr.get_next(datetime)
    second = itr.get_next(datetime)
    gap_minutes = (second - first).total_seconds() / 60.0
    if gap_minutes < settings.min_schedule_interval_minutes:
        raise ScheduleValidationError(
            f"that fires too often (every {gap_minutes:.0f} min); "
            f"minimum is every {settings.min_schedule_interval_minutes} min"
        )
