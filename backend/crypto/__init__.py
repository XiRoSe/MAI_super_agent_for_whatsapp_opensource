"""Cryptography for Mai's privacy layer: sealed-box primitives, the epoch
KeyService, and the KEK-wrapping KeyStore. No message plaintext ever leaves this
layer to disk, and epoch private keys are persisted only as KEK-wrapped
ciphertext; only ciphertext + metadata are stored.

Backward-compat re-export: the Fernet-based ``encrypt``/``decrypt`` helpers
(OAuth token-at-rest encryption) that previously lived in ``backend/crypto.py``
are preserved here so existing callers continue to work without changes.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from backend.config import settings


def _fernet() -> Fernet:
    key = settings.mai_encryption_key
    if not key:
        if settings.is_production:
            raise RuntimeError("MAI_ENCRYPTION_KEY is required in production")
        # Dev-only deterministic key so the app runs without configuration.
        digest = hashlib.sha256(b"mai-dev-insecure-key").digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str | None) -> str | None:
    if plaintext is None:
        return None
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str | None) -> str | None:
    if ciphertext is None:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:  # pragma: no cover - corrupted/rotated key
        raise RuntimeError("failed to decrypt secret (wrong MAI_ENCRYPTION_KEY?)") from exc
