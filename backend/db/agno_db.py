"""Bridge Mai's configured database URL to an Agno db instance.

Agno owns its own tables (sessions, memory/learning, schedules) on the SAME
database Mai uses for its domain tables. This returns the right Agno db object
for the configured `MAI_DB_URL` scheme.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from backend.config import settings
from backend.db.base import _normalize_url


@lru_cache(maxsize=1)
def get_agno_db() -> Any:
    url = _normalize_url(settings.mai_db_url)
    if url.startswith("sqlite"):
        from agno.db.sqlite import SqliteDb

        # sqlite:///tmp/mai.db -> file path tmp/mai.db ; sqlite:// -> in-memory
        path = url.split("sqlite:///")[-1] if "sqlite:///" in url else ":memory:"
        return SqliteDb(db_file=path)

    from agno.db.postgres import PostgresDb

    return PostgresDb(db_url=url)
