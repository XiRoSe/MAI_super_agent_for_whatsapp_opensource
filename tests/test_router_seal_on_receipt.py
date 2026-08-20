from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.crypto import keyservice as ks_mod
from backend.crypto.keystore import InMemoryKeyStore
from backend.db.base import Base
from backend.db.tenancy import Account, SealedMessage
from backend.whatsapp import encstore
from backend.whatsapp.provider import InboundMessage
import backend.whatsapp.router as router


def _session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    s.add(Account(id=3))
    s.commit()
    return s


def test_store_history_seals_inbound(monkeypatch):
    s = _session()
    monkeypatch.setattr(encstore, "get_key_service",
                        lambda: ks_mod.KeyService(InMemoryKeyStore()))
    msg = InboundMessage(from_number="+972500000001", text="hi there", message_id="m1",
                         timestamp="1782604800", session_id="mai_acct_3", from_me=False,
                         chat_id="+972500000001", from_name="Eyal")
    router._store_history(s, 3, msg)
    row = s.execute(select(SealedMessage)).scalar_one()
    assert row.ciphertext != "hi there" and "hi there" not in row.ciphertext
    assert row.direction == "in" and row.sender == "Eyal"


def test_store_history_never_raises_on_seal_error(monkeypatch):
    s = _session()
    class _Boom:
        def current_public_key(self):
            raise RuntimeError("keystore down")
    monkeypatch.setattr(encstore, "get_key_service", lambda: _Boom())
    msg = InboundMessage(from_number="+1", text="x", message_id="m9", timestamp="0",
                         session_id="d", from_me=True, chat_id="+1")
    router._store_history(s, 3, msg)  # must not raise (fail-closed, no plaintext written)
    assert s.execute(select(SealedMessage)).first() is None
