"""Inbound media URL index: remember on webhook, resolve in read_chat.

The index is DB-backed (table `media_refs`) with an in-memory cache in front, so
a mapping survives process restarts. These tests point `media_index.SessionLocal`
at a fresh in-memory SQLite (with the table created from `Base.metadata`) and
clear the cache between tests, mirroring how other modules' SessionLocal is
monkeypatched in the suite.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.whatsapp import media_index


@pytest.fixture(autouse=True)
def _db(monkeypatch):
    """Back media_index by a fresh in-memory DB and a clean cache per test."""
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared connection so the table persists
    )
    # Importing the model registers MediaRef on Base.metadata; create_all then
    # builds the `media_refs` table on this engine.
    import backend.db.tenancy  # noqa: F401

    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(media_index, "SessionLocal", maker)
    media_index._cache.clear()
    yield
    media_index._cache.clear()


def test_remember_and_lookup():
    media_index.remember("MID1", "https://gw/statics/media/x.ogg")
    assert media_index.lookup("MID1") == "https://gw/statics/media/x.ogg"
    assert media_index.lookup("missing") is None


def test_remember_ignores_blanks():
    media_index.remember("", "https://gw/x")
    media_index.remember("MID2", "")
    media_index.remember(None, None)
    assert media_index.lookup("MID2") is None
    assert media_index.lookup("") is None


def test_lookup_blank_is_none():
    assert media_index.lookup(None) is None
    assert media_index.lookup("") is None


def test_remember_upserts_url():
    media_index.remember("MID3", "https://gw/old.ogg")
    media_index.remember("MID3", "https://gw/new.ogg")
    assert media_index.lookup("MID3") == "https://gw/new.ogg"


def test_survives_restart():
    media_index.remember("MID4", "https://gw/statics/media/y.ogg")
    # Simulate a fresh process: the in-memory cache is gone, but the DB row
    # remains, so lookup must still resolve the URL from persistence.
    media_index._cache.clear()
    assert media_index.lookup("MID4") == "https://gw/statics/media/y.ogg"
    # And it's now warmed back into the cache.
    assert media_index._cache["MID4"] == "https://gw/statics/media/y.ogg"
