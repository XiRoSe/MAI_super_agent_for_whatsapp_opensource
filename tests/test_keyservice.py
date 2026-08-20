# tests/test_keyservice.py
import base64
import pytest
from freezegun import freeze_time
from nacl.exceptions import CryptoError

from backend.crypto import keyservice as ks_mod
from backend.crypto import sealing
from backend.crypto.keystore import InMemoryKeyStore


def _svc():
    # Off-prod (mai_env defaults to "local"), KeyService generates a per-run KEK.
    return ks_mod.KeyService(InMemoryKeyStore())


def test_epoch_id_is_floor_unix_over_172800():
    # 2026-06-26 00:00:00 UTC = 1782432000 unix -> //172800 = 10315
    with freeze_time("2026-06-26T00:00:00+00:00"):
        assert ks_mod.epoch_id() == 1782432000 // 172800


def test_current_pubkey_persists_and_is_stable_within_epoch():
    svc = _svc()
    with freeze_time("2026-06-26 00:00:00"):
        e1, pub1 = svc.current_public_key()
        # A fresh service over the SAME store + SAME KEK must NOT regenerate (would
        # orphan messages already sealed earlier this epoch); it reloads the key.
        svc2 = ks_mod.KeyService(svc._store, kek=svc._kek)
        e2, pub2 = svc2.current_public_key()
    assert e1 == e2
    assert pub1 == pub2


def test_store_holds_wrapped_ciphertext_not_raw_private():
    svc = _svc()
    with freeze_time("2026-06-26 00:00:00"):
        e, _ = svc.current_public_key()
    raw_private = bytes(svc.private_for(e))   # the live X25519 private key
    wrapped = svc._store.get(e)                # what actually sits in the store
    assert wrapped != raw_private              # stored value is ciphertext, not the key
    assert raw_private not in wrapped          # raw key bytes appear nowhere


def test_private_for_previous_opens_older_is_none_after_shred():
    svc = _svc()
    with freeze_time("2026-06-26 00:00:00"):
        cur, _ = svc.current_public_key()
    # Seed wrapped keys three, two, and one epochs back, using THIS service's KEK so they
    # unwrap correctly (the store only ever holds KEK-wrapped bytes).
    from backend.crypto import sealing
    for e in (cur - 3, cur - 2, cur - 1):
        p = sealing.generate_private_key()
        svc._store.put(e, svc._wrap(p), sealing.public_key_b64(p))
    # Before shred: all keys exist
    assert svc.private_for(cur - 3) is not None
    assert svc.private_for(cur - 2) is not None
    assert svc.private_for(cur - 1) is not None
    with freeze_time("2026-06-26 00:00:00"):
        svc.shred_expired()
    # After shred: keep cur, cur-1, cur-2; shred cur-3 and older
    assert svc.private_for(cur) is not None           # current epoch survives
    assert svc.private_for(cur - 1) is not None       # one epoch back survives (4-day window)
    assert svc.private_for(cur - 2) is not None       # two epochs back survives (within 4-day window)
    assert svc.private_for(cur - 3) is None           # three epochs back is crypto-shredded


def test_private_for_absent_is_none():
    svc = _svc()
    assert svc.private_for(999999) is None


def test_fail_closed_when_kek_missing_in_production(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "mai_kek", "", raising=False)
    monkeypatch.setattr(settings, "mai_env", "production", raising=False)
    with pytest.raises(RuntimeError):
        ks_mod.KeyService(InMemoryKeyStore())   # refuses to run without a KEK


def test_short_base64_kek_is_stretched(monkeypatch):
    """A base64 key that decodes to != 32 bytes is treated as a passphrase
    and stretched to 32 bytes via SHA-256 — it must NOT raise."""
    from backend.config import settings
    import hashlib
    # base64-encode a 16-byte key (too short, SecretBox.KEY_SIZE is 32)
    short_key = base64.b64encode(b"sixteenbyteshere").decode()
    monkeypatch.setattr(settings, "mai_kek", short_key, raising=False)
    # Should succeed: short base64 falls back to SHA-256 stretch of the raw string.
    svc = ks_mod.KeyService(InMemoryKeyStore())
    expected_kek = hashlib.sha256(short_key.encode("utf-8")).digest()
    assert svc._kek == expected_kek


def test_wrong_kek_fails_to_unwrap():
    """Unwrapping with a different KEK raises CryptoError, not silent corruption."""
    store = InMemoryKeyStore()
    # Create a key and wrap it under KEK-A
    from nacl.secret import SecretBox
    from nacl.utils import random as nacl_random
    kek_a = nacl_random(SecretBox.KEY_SIZE)
    svc_a = ks_mod.KeyService(store, kek=kek_a)

    # Generate and wrap a private key under KEK-A
    priv = sealing.generate_private_key()
    wrapped = svc_a._wrap(priv)
    store.put(1, wrapped, sealing.public_key_b64(priv))

    # Create a different 32-byte KEK-B and try to unwrap the same wrapped blob
    kek_b = nacl_random(SecretBox.KEY_SIZE)
    svc_b = ks_mod.KeyService(store, kek=kek_b)

    # Attempting to unwrap with the wrong KEK must raise CryptoError
    with pytest.raises(CryptoError):
        svc_b.private_for(1)
