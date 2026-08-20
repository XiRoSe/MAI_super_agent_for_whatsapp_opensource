"""Schedule-management toolkit — lets the agent create/list/edit/cancel
schedules from natural language.

The MODEL turns the user's phrasing into the typed arguments here; this toolkit
validates and persists via the tenant-scoped `ScheduleService`. Every method
returns a human-readable string (success, confirmation, or a clear error) so a
bad parse degrades into a helpful message instead of a crashed turn.

The toolkit is built per-user and closes over that user's `TenantScope`, so it
can only ever read or change the calling user's schedules.
"""

from __future__ import annotations

from datetime import datetime

from dateutil import parser as dtparser
from sqlalchemy.orm import Session

from agno.tools import Toolkit

from backend.db.scoped import TenantScope
from backend.db.tenancy import User
from backend.provisioning import set_user_timezone
from backend.scheduling.service import ScheduleService
from backend.scheduling.spec import ScheduleSpec, ScheduleValidationError


class ScheduleTools(Toolkit):
    def __init__(self, *, user: User, session: Session, default_channel: str | None = None,
                 device_id: str | None = None):
        self.user = user
        self.session = session
        # The chat this conversation is happening in (a WhatsApp group/chat).
        # New schedules fire here by default; None => the user's own DM.
        self.default_channel = default_channel
        # Gateway device — needed only to resolve a NAMED target chat ("the Family group")
        # to a jid at creation. None => targets other than the current chat can't be named.
        self.device_id = device_id
        self.provider = None
        if device_id:
            from backend.whatsapp import get_provider

            self.provider = get_provider()
        self.scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
        self.service = ScheduleService(self.scope)
        super().__init__(
            name="schedule_tools",
            tools=[
                self.set_timezone,
                self.create_schedule,
                self.list_schedules,
                self.edit_schedule,
                self.cancel_schedule,
            ],
            instructions=(
                "Use these to manage the user's reminders/schedules. You must know "
                "the user's timezone first (call set_timezone). Confirm ambiguous "
                "times with the user before creating.\n"
                "WHERE it fires: a schedule delivers into the chat it was created in by "
                "default. If the user names a different chat/group ('in the Family "
                "group'), pass it as target_chat. ALWAYS state the destination in your "
                "confirmation — e.g. 'every day at 12:00, in this chat' or 'in Family "
                "(group)' — exactly as the tool result echoes it, so the user can catch "
                "a wrong target at creation. Schedules are only confirmed at creation, "
                "never at fire time. When you list schedules, show WHERE each one fires "
                "(the '→ …' from the tool result) on every item — never drop it."
            ),
            add_instructions=True,
        )

    # -- target resolution ----------------------------------------------------
    def _resolve_target(self, target_chat: str):
        """Resolve a user-named target ('Family', a number, a jid) to a single chat via the
        shared resolver — same no-silent-pick contract as sends: ambiguity refuses."""
        from backend.whatsapp.resolution import resolve_chat

        chats: list[dict] = []
        if self.provider is not None and "@" not in target_chat:
            try:  # same shape WhatsAppTools resolves against
                if hasattr(self.provider, "recent_chats"):
                    chats = self.provider.recent_chats(session_id=self.device_id, days=365) or []
                if not chats:
                    chats = self.provider.list_chats(self.device_id, limit=100) or []
            except Exception:
                chats = []
        return resolve_chat(target_chat, chats)

    # -- timezone -----------------------------------------------------------
    def set_timezone(self, timezone: str) -> str:
        """Store the user's IANA timezone (e.g. 'America/New_York'). Required before scheduling."""
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(timezone)
        except Exception:
            return f"'{timezone}' is not a valid timezone. Use an IANA name like 'Europe/London'."
        set_user_timezone(self.session, self.user, timezone)
        return f"Got it — your timezone is set to {timezone}."

    # -- create -------------------------------------------------------------
    def create_schedule(
        self,
        name: str,
        kind: str,
        action_text: str,
        schedule_type: str,
        run_at: str | None = None,
        cron: str | None = None,
        target_chat: str = "",
    ) -> str:
        """Create a schedule.

        Args:
            name: short title, e.g. "morning brief".
            kind: "static" to send a fixed message, or "dynamic" to re-run a task
                  at fire time and send the result.
            action_text: the message (static) or the instruction to run (dynamic).
            schedule_type: "once" or "recurring".
            run_at: ISO datetime for a one-off, in the user's local time
                    (e.g. "2026-06-19T15:00"). Omit for recurring.
            cron: 5-field cron for recurring (e.g. "0 8 * * 1-5"). Omit for one-off.
            target_chat: WHERE it fires — ONLY when the user names a chat/group other
                    than this one (name, number, or id, e.g. "Family"). Omit to fire
                    in the current chat. The result echoes the resolved destination —
                    repeat it in your confirmation to the user.
        """
        if not self.user.timezone:
            return "I need your timezone first — what timezone are you in?"
        parsed_run_at: datetime | None = None
        if run_at:
            try:
                parsed_run_at = dtparser.parse(run_at)
            except (ValueError, OverflowError):
                return f"I couldn't read the time '{run_at}'. Try an explicit date and time."

        # Resolve WHERE it fires. Default: the chat we're in. A named target resolves through
        # the shared resolver — ambiguous/unknown REFUSES (never a silent near-match target).
        target, target_label = self.default_channel, None
        if (target_chat or "").strip():
            from backend.whatsapp.resolution import MatchKind

            res = self._resolve_target(target_chat.strip())
            if res.kind is MatchKind.AMBIGUOUS:
                opts = "; ".join(c.label for c in res.candidates)
                return (f"'{target_chat}' matches more than one chat: {opts}. Ask the user "
                        f"which one they mean — do NOT guess.")
            if not res.resolved:
                return (f"I couldn't pin down the chat '{target_chat}'. Ask the user for its "
                        f"exact name (or find it with find_chats) and try again.")
            target, target_label = res.jid, res.echo_label

        spec = ScheduleSpec(
            name=name,
            kind=kind,
            action_text=action_text,
            schedule_type=schedule_type,
            timezone=self.user.timezone,
            run_at=parsed_run_at,
            cron=cron,
        )
        try:
            schedule = self.service.create(spec, target_chat=target,
                                           target_chat_name=target_label)
        except ScheduleValidationError as exc:
            return f"I can't set that up: {exc}"
        where = f"It will fire in {target_label}." if target_label else "It will fire in this chat."
        return f"Done (#{schedule.id}). {spec.human_summary()} {where}"

    # -- list ---------------------------------------------------------------
    def _target_label(self, s, jid_names: dict[str, str]) -> str:
        """WHERE `s` fires, human-readable. Priority: the label confirmed at creation; a live
        chat-name lookup (rows created before labels existed); 'your DM' for the user's own
        chat; the raw jid as a last resort. Never blank — every schedule fires SOMEWHERE, and
        the whole point is that the user can see (and veto) where."""
        if s.target_chat_name:
            return s.target_chat_name
        if not s.target_chat:
            return "your DM"
        from backend.whatsapp.numbers import same_number

        if "@g.us" not in s.target_chat and same_number(s.target_chat, self.user.whatsapp_number):
            return "your DM"
        return jid_names.get(s.target_chat) or s.target_chat

    def _jid_names(self) -> dict[str, str]:
        """Live jid -> display-name map from the provider's chat list (best-effort)."""
        if self.provider is None:
            return {}
        chats: list[dict] = []
        try:
            if hasattr(self.provider, "recent_chats"):
                chats = self.provider.recent_chats(session_id=self.device_id, days=365) or []
            if not chats:
                chats = self.provider.list_chats(self.device_id, limit=100) or []
        except Exception:
            return {}
        return {c["jid"]: f"{c['name']}{' (group)' if c.get('is_group') else ''}"
                for c in chats if c.get("jid") and c.get("name")}

    def list_schedules(self) -> str:
        """List the user's active schedules, each with WHERE it fires ('→ …'). When you relay
        this list to the user, KEEP the destination on every item — it's how they catch a
        reminder pointed at the wrong chat."""
        items = self.service.list_active()
        if not items:
            return "You have no active schedules."
        # Live names only needed for legacy rows that carry a jid but no stored label — and we
        # BACKFILL each resolved label onto its row, so a legacy row pays the (one, batched,
        # 90s-TTL-cached) provider lookup at most once ever; once all rows are labeled this
        # path never runs again. Self-extinguishing migration, not a per-list cost.
        jid_names = (self._jid_names()
                     if any(s.target_chat and not s.target_chat_name for s in items) else {})
        if jid_names:
            backfilled = False
            for s in items:
                if s.target_chat and not s.target_chat_name and s.target_chat in jid_names:
                    s.target_chat_name = jid_names[s.target_chat]
                    backfilled = True
            if backfilled:
                try:
                    self.scope.commit()
                except Exception:
                    pass  # display still works from jid_names; we'll backfill next time
        lines = []
        for s in items:
            when = (
                f"once at {s.run_at:%a %d %b %H:%M}"
                if s.schedule_type == "once" and s.run_at
                else f"cron '{s.cron}'"
            )
            where = self._target_label(s, jid_names)
            lines.append(f"#{s.id} — {s.name} ({s.kind}, {when}, {s.timezone}) → {where}")
        return ("Your schedules (the → is where each one fires — include it when telling "
                "the user):\n" + "\n".join(lines))

    # -- edit ---------------------------------------------------------------
    def edit_schedule(
        self,
        schedule_id: int,
        name: str,
        kind: str,
        action_text: str,
        schedule_type: str,
        run_at: str | None = None,
        cron: str | None = None,
    ) -> str:
        """Replace a schedule's settings. Same fields as create_schedule plus schedule_id."""
        if not self.user.timezone:
            return "I need your timezone first."
        parsed_run_at: datetime | None = None
        if run_at:
            try:
                parsed_run_at = dtparser.parse(run_at)
            except (ValueError, OverflowError):
                return f"I couldn't read the time '{run_at}'."
        spec = ScheduleSpec(
            name=name, kind=kind, action_text=action_text, schedule_type=schedule_type,
            timezone=self.user.timezone, run_at=parsed_run_at, cron=cron,
        )
        try:
            updated = self.service.edit(schedule_id, new_spec=spec)
        except ScheduleValidationError as exc:
            return f"I can't update that: {exc}"
        if updated is None:
            return f"I couldn't find an active schedule #{schedule_id}."
        return f"Updated #{updated.id}. {spec.human_summary()}"

    # -- cancel -------------------------------------------------------------
    def cancel_schedule(self, schedule_id: int) -> str:
        """Cancel (deactivate) one of the user's schedules by id."""
        ok = self.service.cancel(schedule_id)
        return f"Cancelled #{schedule_id}." if ok else f"No active schedule #{schedule_id} to cancel."
