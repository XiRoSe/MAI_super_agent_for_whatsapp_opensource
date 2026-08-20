import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.crypto import keyservice as ks_mod, sealing
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
def svc(monkeypatch):
    service = ks_mod.KeyService(InMemoryKeyStore())
    monkeypatch.setattr(encstore, "get_key_service", lambda: service)
    return service


def test_sealed_input_is_stored_as_is_not_resealed(session, svc):
    # Simulate a GOWA-sealed blob: seal once to the current epoch, then hand it in.
    epoch, pub = svc.current_public_key()
    blob = sealing.seal("from gowa", pub)
    encstore.store_sealed(session, 1, "+972540000010", "in", "",   # plaintext empty on sealed path
                          message_id="g1", sealed_ciphertext=blob, epoch_id=epoch)
    row = session.execute(select(SealedMessage)).scalar_one()
    assert row.ciphertext == blob          # stored EXACTLY the GOWA ciphertext (no re-seal)
    assert row.epoch_id == epoch
    # And it still decrypts via the normal read path.
    out = encstore.recent(session, 1, "+972540000010", limit=10)
    assert out[0]["text"] == "from gowa"


def test_clear_input_still_seals_on_receipt(session, svc):
    encstore.store_sealed(session, 1, "+1", "in", "hello clear", message_id="c1")
    row = session.execute(select(SealedMessage)).scalar_one()
    assert row.ciphertext != "hello clear" and "hello clear" not in row.ciphertext
    assert encstore.recent(session, 1, "+1", limit=10)[0]["text"] == "hello clear"


def test_sealed_input_does_not_call_current_public_key(session, svc, monkeypatch):
    epoch, pub = svc.current_public_key()
    blob = sealing.seal("x", pub)
    def _boom():
        raise AssertionError("sealed input must NOT fetch the current pubkey")
    monkeypatch.setattr(svc, "current_public_key", _boom)
    encstore.store_sealed(session, 1, "+1", "in", "", message_id="g2",
                          sealed_ciphertext=blob, epoch_id=epoch)  # must not raise
    assert session.execute(select(SealedMessage)).scalar_one().ciphertext == blob


def test_dedup_sealed_input(session, svc):
    """Same message_id on a sealed inbound should be stored only once."""
    epoch, pub = svc.current_public_key()
    blob = sealing.seal("dup test", pub)
    encstore.store_sealed(session, 1, "+1", "in", "", message_id="dup1",
                          sealed_ciphertext=blob, epoch_id=epoch)
    encstore.store_sealed(session, 1, "+1", "in", "", message_id="dup1",
                          sealed_ciphertext=blob, epoch_id=epoch)
    rows = list(session.execute(select(SealedMessage)).scalars().all())
    assert len(rows) == 1


def test_dedup_clear_input(session, svc):
    """Same message_id on a clear inbound should be stored only once."""
    encstore.store_sealed(session, 1, "+1", "in", "hello", message_id="dup2")
    encstore.store_sealed(session, 1, "+1", "in", "hello", message_id="dup2")
    rows = list(session.execute(select(SealedMessage)).scalars().all())
    assert len(rows) == 1


def test_sealed_row_never_contains_plaintext(session, svc):
    """Neither path must ever persist the plaintext text in the ciphertext column."""
    epoch, pub = svc.current_public_key()
    blob = sealing.seal("secret gowa text", pub)
    encstore.store_sealed(session, 1, "+1", "in", "", message_id="p1",
                          sealed_ciphertext=blob, epoch_id=epoch)
    row = session.execute(select(SealedMessage)).scalar_one()
    assert "secret gowa text" not in row.ciphertext

    from sqlalchemy import create_engine as _ce
    engine2 = _ce("sqlite://", future=True)
    Base.metadata.create_all(engine2)
    s2 = sessionmaker(bind=engine2, future=True)()
    s2.add(Account(id=1))
    s2.commit()
    encstore.store_sealed(s2, 1, "+2", "in", "secret clear text", message_id="p2")
    row2 = s2.execute(select(SealedMessage)).scalar_one()
    assert "secret clear text" not in row2.ciphertext
    s2.close()
