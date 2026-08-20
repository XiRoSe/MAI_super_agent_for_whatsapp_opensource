from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.crypto.keystore import DbKeyStore, InMemoryKeyStore
from backend.db.base import Base


def _db_store():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, future=True)
    return DbKeyStore(session_factory=Maker)


def test_inmemory_get_put_delete_list():
    ks = InMemoryKeyStore()
    assert ks.get(100) is None
    assert ks.get_public(100) is None
    assert ks.list_epochs() == []
    ks.put(100, b"\x01\x02wrapped", "PUB100")
    ks.put(101, b"\x03\x04wrapped", "PUB101")
    assert ks.get(100) == b"\x01\x02wrapped"
    assert ks.get_public(100) == "PUB100"
    assert sorted(ks.list_epochs()) == [100, 101]
    ks.delete(100)
    assert ks.get(100) is None
    assert ks.list_epochs() == [101]


def test_dbkeystore_roundtrip_in_app_db():
    ks = _db_store()
    assert ks.get(7) is None
    ks.put(7, b"wrapped-ct", "PUB7")
    assert ks.get(7) == b"wrapped-ct"
    assert ks.get_public(7) == "PUB7"
    ks.put(7, b"wrapped-ct-2", "PUB7b")   # idempotent upsert
    assert ks.get(7) == b"wrapped-ct-2"
    assert ks.get_public(7) == "PUB7b"
    assert ks.list_epochs() == [7]
    ks.delete(7)
    assert ks.get(7) is None


def test_stored_wrapped_private_is_ciphertext_not_raw_key():
    # The point of envelope encryption: what's persisted is the KEK-wrapped private
    # key (ciphertext), never the raw 32-byte X25519 private key. Wrap a known key
    # under a KEK and assert the stored bytes differ from it (and need the KEK back).
    from nacl.secret import SecretBox
    from nacl.utils import random as nacl_random

    from backend.crypto import sealing

    kek = nacl_random(SecretBox.KEY_SIZE)            # 32-byte KEK
    priv = sealing.generate_private_key()
    raw_private = bytes(priv)                          # the secret we must never store raw
    wrapped = bytes(SecretBox(kek).encrypt(raw_private))   # ciphertext (carries its nonce)

    ks = _db_store()
    ks.put(42, wrapped, sealing.public_key_b64(priv))
    stored = ks.get(42)
    assert stored != raw_private                       # NOT the raw key
    assert raw_private not in stored                   # raw key bytes appear nowhere
    # Opaque without the KEK; round-trips with it.
    assert SecretBox(kek).decrypt(stored) == raw_private
