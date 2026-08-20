from datetime import timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.crypto import keyservice as ks_mod, purge as purge_mod
from backend.crypto.keystore import InMemoryKeyStore
from backend.db.base import Base, utcnow
from backend.db.tenancy import Account, SealedMessage
from backend.whatsapp import encstore


def test_purge_once_deletes_old_rows_and_shreds_keys(monkeypatch):
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, future=True)
    s = Maker()
    s.add(Account(id=1))
    s.commit()
    svc = ks_mod.KeyService(InMemoryKeyStore())
    monkeypatch.setattr(encstore, "get_key_service", lambda: svc)
    monkeypatch.setattr(purge_mod, "get_key_service", lambda: svc)
    monkeypatch.setattr(purge_mod, "SessionLocal", Maker, raising=False)

    encstore.store_sealed(s, 1, "+1", "in", "fresh", message_id="f1")
    encstore.store_sealed(s, 1, "+1", "in", "stale", message_id="s1")
    # Age the second row beyond the window.
    old = s.execute(select(SealedMessage).where(SealedMessage.message_id == "s1")).scalar_one()
    old.created_at = utcnow() - timedelta(days=10)
    s.commit()

    purged = purge_mod.purge_once()
    assert purged == 1
    left = s.execute(select(SealedMessage)).scalars().all()
    assert [r.message_id for r in left] == ["f1"]
