"""SQLAlchemy engine + session for Mai's own (tenancy/billing) tables.

Agno owns its session/memory/schedule tables on the same database; this module
owns Mai's domain tables (accounts, users, oauth tokens, usage, windows).
Both point at `MAI_DB_URL`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import DateTime, create_engine
from sqlalchemy.orm import DeclarativeBase, mapped_column, sessionmaker

from backend.config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_url(url: str) -> str:
    """Ensure a sync SQLAlchemy URL with an explicit driver."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):  # Railway sometimes emits this form
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


class Base(DeclarativeBase):
    """Declarative base for all Mai domain models."""


def _make_engine():
    url = _normalize_url(settings.mai_db_url)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    if url.startswith("sqlite:///"):
        # sqlite refuses to CREATE a db file in a missing directory — make sure the
        # parent dir exists so a fresh clone (no tmp/) boots with the default URL.
        db_path = Path(url.removeprefix("sqlite:///"))
        if db_path.parent != Path("."):
            db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create Mai's domain tables. Agno creates its own tables on first use."""
    # Import models so they register on Base.metadata before create_all.
    from backend.db import tenancy  # noqa: F401
    from backend.scheduling import models as _sched_models  # noqa: F401

    Base.metadata.create_all(engine)
    _sync_columns()


def _sync_columns() -> None:
    """Add model columns missing from existing tables (lightweight migration).

    `create_all()` only creates missing TABLES, never missing COLUMNS — so adding
    a column to an existing model needs this. Only safely-addable columns
    (nullable, or with a default) are added; works on SQLite and Postgres.
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                if not col.nullable and col.default is None and col.server_default is None:
                    continue  # can't add NOT NULL w/o default to populated rows
                coltype = col.type.compile(dialect=engine.dialect)
                sql = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}'
                if col.server_default is not None:
                    sd = col.server_default.arg
                    default_sql = sd.text if hasattr(sd, "text") else str(sd)
                    sql += f" DEFAULT {default_sql}"
                conn.execute(text(sql))


# A reusable "created_at" column factory for models.
def created_at_column():
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
