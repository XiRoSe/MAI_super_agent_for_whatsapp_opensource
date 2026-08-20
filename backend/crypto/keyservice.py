"""Epoch KeyService: a fresh RANDOM X25519 keypair per 2-day epoch, with each
private key wrapped under the KEK before it is stored.

Time only SELECTS the active epoch (epoch_id = floor(unix / EPOCH_SECONDS)); the
key itself is random, never derived from the clock. Each epoch's PRIVATE key is
wrapped (NaCl secretbox / XSalsa20-Poly1305) under the KEK and only the ciphertext
is persisted (KeyStore); the KEK lives ONLY in `MAI_KEK` (a Railway Sealed
Variable) and is held in RAM here. We keep the current + previous epoch (= 4 days)
and crypto-shred anything older — after which that epoch's ciphertext is
permanently unrecoverable.
"""

from __future__ import annotations

import hashlib
import base64
import time

from nacl.public import PrivateKey
from nacl.secret import SecretBox
from nacl.utils import random as nacl_random

from backend.config import settings
from backend.crypto import sealing
from backend.crypto.keystore import KeyStore, get_keystore
from backend.logging_config import get_logger

logger = get_logger(__name__)

EPOCH_SECONDS = 172800          # 2 days
RETENTION_EPOCHS = 2            # current + previous = 4 days


def epoch_id(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // EPOCH_SECONDS)


def _load_kek() -> bytes:
    """Resolve the 32-byte KEK from MAI_KEK. FAIL-CLOSED in production: if it is
    missing/blank we refuse to operate rather than run without envelope encryption.
    Off-prod we generate an ephemeral per-process KEK so tests and local dev work
    without secrets (epoch keys won't survive a restart, which is fine outside prod).

    Accepts ANY secret value so it works with a hand-generated base64 key OR a
    Railway `${{ secret(N) }}` generated variable: if the value is base64 that
    decodes to exactly 32 bytes we use those bytes directly (legacy format);
    otherwise we derive a 32-byte key deterministically via SHA-256 of the raw
    string. Either way the KEK never has to match a strict format."""
    raw = (settings.mai_kek or "").strip()
    if not raw:
        if settings.is_production:
            raise RuntimeError(
                "MAI_KEK is required in production (set it via your platform's "
                "secret manager, e.g. a Railway sealed variable) — refusing to run "
                "the KeyService without a KEK."
            )
        return nacl_random(SecretBox.KEY_SIZE)
    try:
        decoded = base64.b64decode(raw, validate=True)
        if len(decoded) == SecretBox.KEY_SIZE:
            return decoded
    except Exception:
        pass
    # Any other value (e.g. Railway secret(32), a passphrase) -> stretch to 32 bytes.
    return hashlib.sha256(raw.encode("utf-8")).digest()


class KeyService:
    def __init__(self, store: KeyStore, kek: bytes | None = None) -> None:
        self._store = store
        self._kek = kek if kek is not None else _load_kek()
        self._box = SecretBox(self._kek)
        self._cache: dict[int, PrivateKey] = {}

    def _wrap(self, priv: PrivateKey) -> bytes:
        # SecretBox.encrypt returns an EncryptedMessage (nonce ‖ ciphertext).
        return bytes(self._box.encrypt(bytes(priv)))

    def _unwrap(self, wrapped: bytes) -> PrivateKey:
        return PrivateKey(self._box.decrypt(wrapped))

    def _load_or_create(self, e: int) -> PrivateKey:
        cached = self._cache.get(e)
        if cached is not None:
            return cached
        wrapped = self._store.get(e)
        if wrapped is None:
            priv = sealing.generate_private_key()
            # Persist (wrapped) BEFORE caching/returning: if persistence fails we
            # must NOT seal to a key that won't survive a restart (orphans messages).
            self._store.put(e, self._wrap(priv), sealing.public_key_b64(priv))
            logger.info("keyservice.generated_epoch", extra={"ctx_epoch": e})
        else:
            priv = self._unwrap(wrapped)
        self._cache[e] = priv
        return priv

    def current_public_key(self) -> tuple[int, str]:
        e = epoch_id()
        pub = self._store.get_public(e)   # no unwrap needed when already published
        if pub is None:
            priv = self._load_or_create(e)  # raises if the store cannot persist (fail-closed)
            pub = sealing.public_key_b64(priv)
        return e, pub

    def private_for(self, e: int) -> PrivateKey | None:
        cached = self._cache.get(e)
        if cached is not None:
            return cached
        wrapped = self._store.get(e)
        if wrapped is None:
            return None
        priv = self._unwrap(wrapped)
        self._cache[e] = priv
        return priv

    def warm(self) -> None:
        cur = epoch_id()
        for e in (cur, cur - 1):
            if self._store.get(e) is not None:
                self.private_for(e)
        self._load_or_create(cur)  # ensure the current epoch key exists + is published

    def shred_expired(self, now: float | None = None) -> None:
        cur = epoch_id(now)
        for e in list(self._store.list_epochs()):
            if e < cur - RETENTION_EPOCHS:
                self._store.delete(e)
                self._cache.pop(e, None)
                logger.info("keyservice.shredded_epoch", extra={"ctx_epoch": e})

    def rotate(self) -> None:
        self._load_or_create(epoch_id())
        self.shred_expired()


_KEY_SERVICE: KeyService | None = None


def get_key_service() -> KeyService:
    global _KEY_SERVICE
    if _KEY_SERVICE is None:
        _KEY_SERVICE = KeyService(get_keystore())
    return _KEY_SERVICE
