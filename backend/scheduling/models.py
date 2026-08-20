"""The Mai-owned Schedule record.

Agno's ScheduleManager is the firing mechanism (cron poller); this table is the
tenant-facing source of truth for *ownership*, kind, and the stored
instruction — things Agno's schedule payload doesn't model with isolation. The
two are linked by `agno_schedule_name`. Tenant-scoped access goes through
`TenantScope`, so list/edit/cancel can never cross tenants.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base, created_at_column
from backend.db.tenancy import TenantOwned


class Schedule(Base, TenantOwned):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # static | dynamic
    action_text: Mapped[str] = mapped_column(Text, nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(16), nullable=False)  # once | recurring
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cron: Mapped[str | None] = mapped_column(String(120))
    # WhatsApp chat/group to fire into (jid/number). None => the user's own DM.
    target_chat: Mapped[str | None] = mapped_column(String(96))
    # Human label of the target confirmed at creation ("Family (group)") — for list/echo only;
    # delivery uses target_chat. Nullable => auto-added by _sync_columns, no manual migration.
    target_chat_name: Mapped[str | None] = mapped_column(String(255))

    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    agno_schedule_name: Mapped[str | None] = mapped_column(String(160), unique=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fire_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    @property
    def agno_name(self) -> str:
        return self.agno_schedule_name or f"mai-sched-{self.id}"
