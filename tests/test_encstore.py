import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.crypto import keyservice as ks_mod
from backend.crypto.keystore import InMemoryKeyStore
from backend.db.base import Base
from backend.db.tenancy import Account, SealedMessage
from backend.whatsapp import encstore


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    s.add(Account(id=1))
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def fresh_keyservice(monkeypatch):
    svc = ks_mod.KeyService(InMemoryKeyStore())
    monkeypatch.setattr(encstore, "get_key_service", lambda: svc)
    return svc


def test_store_sealed_writes_ciphertext_not_plaintext(session, fresh_keyservice):
    encstore.store_sealed(session, 1, "+972540000010", "in", "meeting at 5pm",
                          message_id="m1", sender="Dana")
    row = session.execute(select(SealedMessage)).scalar_one()
    assert row.ciphertext != "meeting at 5pm"
    assert "meeting at 5pm" not in row.ciphertext      # no plaintext anywhere in the row
    assert row.direction == "in" and row.epoch_id >= 0


def test_recent_decrypts_in_order(session, fresh_keyservice):
    encstore.store_sealed(session, 1, "+972540000010", "in", "first", message_id="m1", sender="Dana")
    encstore.store_sealed(session, 1, "+972540000010", "out", "second", message_id="m2")
    out = encstore.recent(session, 1, "+972540000010", limit=10)
    assert [m["text"] for m in out] == ["first", "second"]
    assert out[0]["from_me"] is False and out[1]["from_me"] is True


def test_idempotent_on_message_id(session, fresh_keyservice):
    encstore.store_sealed(session, 1, "+1", "in", "hi", message_id="dup")
    encstore.store_sealed(session, 1, "+1", "in", "hi", message_id="dup")
    assert len(session.execute(select(SealedMessage)).scalars().all()) == 1


def test_shredded_key_renders_expired(session, fresh_keyservice):
    encstore.store_sealed(session, 1, "+1", "in", "ghost", message_id="g1")
    row = session.execute(select(SealedMessage)).scalar_one()
    fresh_keyservice._store.delete(row.epoch_id)   # crypto-shred this epoch
    fresh_keyservice._cache.pop(row.epoch_id, None)
    out = encstore.recent(session, 1, "+1", limit=10)
    assert out[0]["text"] == "[expired]"


def test_chat_id_normalization_matches_jid_and_number(session, fresh_keyservice):
    encstore.store_sealed(session, 1, "+972540000010", "in", "yo", message_id="n1")
    # Reading with the full s.whatsapp.net jid must resolve to the same chat.
    out = encstore.recent(session, 1, "972540000010@s.whatsapp.net", limit=10)
    assert [m["text"] for m in out] == ["yo"]
