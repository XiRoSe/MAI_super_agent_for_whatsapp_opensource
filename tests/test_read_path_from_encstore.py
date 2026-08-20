import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.crypto import keyservice as ks_mod
from backend.crypto.keystore import InMemoryKeyStore
from backend.db.base import Base
from backend.db.tenancy import Account, WhatsAppConnection, WhatsAppConnectionStatus
from backend.whatsapp import encstore
from backend.whatsapp.gowa import GowaWhatsAppProvider


@pytest.fixture()
def wired(monkeypatch):
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, future=True)
    s = Maker()
    s.add(Account(id=5))
    s.add(WhatsAppConnection(account_id=5, session_id="mai_acct_5",
                             status=WhatsAppConnectionStatus.connected))
    s.commit()
    # Use a single KeyService instance so seal + open share the same key material.
    _svc = ks_mod.KeyService(InMemoryKeyStore())
    monkeypatch.setattr(encstore, "get_key_service", lambda: _svc)
    # gowa.get_chat_messages opens its own SessionLocal — point it at our engine.
    monkeypatch.setattr("backend.whatsapp.gowa.SessionLocal", Maker, raising=False)
    encstore.store_sealed(s, 5, "+972540000010", "in", "are we still on for 5?",
                          message_id="a1", sender="Dana")
    encstore.store_sealed(s, 5, "+972540000010", "out", "yes", message_id="a2")
    s.close()
    return Maker


def test_get_chat_messages_reads_decrypted_store(wired, monkeypatch):
    prov = GowaWhatsAppProvider()
    # If the read path tried GOWA HTTP, this would explode — proving we don't.
    monkeypatch.setattr(prov, "_request",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no GOWA HTTP")))
    out = prov.get_chat_messages("mai_acct_5", "972540000010@s.whatsapp.net", limit=10)
    assert [m["text"] for m in out] == ["are we still on for 5?", "yes"]
    assert out[0]["sender"] == "Dana" and out[1]["from_me"] is True
