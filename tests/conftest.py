"""Shared test fixtures.

Tests run against an in-memory SQLite database created fresh per test, so they
need no DB server and never touch the developer's real `tmp/mai.db`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.db.tenancy import Account, SubscriptionStatus, User


@pytest.fixture(autouse=True)
def _no_llm_router():
    """Never make a real router LLM call in tests; default to the full toolset.

    Gating tests monkeypatch `tool_router.select_categories` directly to override.
    """
    from backend.config import settings

    prev = settings.tool_router_enabled
    settings.tool_router_enabled = False
    try:
        yield
    finally:
        settings.tool_router_enabled = prev


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite://",  # in-memory
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared connection so TestClient threads see the tables
    )
    # Import models so they are registered on Base.metadata.
    import backend.db.tenancy  # noqa: F401
    import backend.scheduling.models  # noqa: F401

    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    s = maker()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def two_tenants(session: Session):
    """Two fully separate tenants, each with one user. Returns (a1, u1, a2, u2)."""
    a1 = Account(plan="beta", status=SubscriptionStatus.active)
    a2 = Account(plan="beta", status=SubscriptionStatus.active)
    session.add_all([a1, a2])
    session.flush()

    u1 = User(
        account_id=a1.id,
        whatsapp_number="+15550000001",
        agno_user_id=User.make_agno_user_id(a1.id, "+15550000001"),
        display_name="Alice",
        timezone="America/New_York",
    )
    u2 = User(
        account_id=a2.id,
        whatsapp_number="+15550000002",
        agno_user_id=User.make_agno_user_id(a2.id, "+15550000002"),
        display_name="Bob",
        timezone="Europe/London",
    )
    session.add_all([u1, u2])
    session.commit()
    return a1, u1, a2, u2
