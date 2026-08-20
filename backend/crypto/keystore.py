"""Storage for KEK-WRAPPED epoch keys — a small table in the APP DB.

What lands here is only CIPHERTEXT: each epoch's X25519 private key is wrapped
(NaCl secretbox under the KEK, in the KeyService) before it ever reaches this
layer, plus the public key (safe in the clear). The KEK itself lives ONLY in
`MAI_KEK` (a Railway Sealed Variable), never in the DB — so a dump of the app DB
yields no usable keys. The `KeyStore` interface is the Phase-3 seam: a real KMS /
enclave key-release service swaps in later without touching KeyService.
"""

from __future__ import annotations

from sqlalchemy import delete, select

from backend.db.base import SessionLocal
from backend.db.tenancy import EpochKey


class KeyStore:
    def get(self, epoch_id: int) -> bytes | None:
        raise NotImplementedError

    def get_public(self, epoch_id: int) -> str | None:
        raise NotImplementedError

    def put(self, epoch_id: int, wrapped_private: bytes, public_b64: str) -> None:
        raise NotImplementedError

    def delete(self, epoch_id: int) -> None:
        raise NotImplementedError

    def list_epochs(self) -> list[int]:
        raise NotImplementedError


class InMemoryKeyStore(KeyStore):
    def __init__(self) -> None:
        self._d: dict[int, tuple[bytes, str]] = {}

    def get(self, epoch_id: int) -> bytes | None:
        row = self._d.get(epoch_id)
        return row[0] if row else None

    def get_public(self, epoch_id: int) -> str | None:
        row = self._d.get(epoch_id)
        return row[1] if row else None

    def put(self, epoch_id: int, wrapped_private: bytes, public_b64: str) -> None:
        self._d[epoch_id] = (wrapped_private, public_b64)

    def delete(self, epoch_id: int) -> None:
        self._d.pop(epoch_id, None)

    def list_epochs(self) -> list[int]:
        return list(self._d.keys())


class DbKeyStore(KeyStore):
    """Plain CRUD on the `epoch_keys` table in the APP DB. Stores only the
    already-KEK-wrapped private key (ciphertext) + the public key; the KEK never
    touches this layer. Opens its own short-lived session per call so the interface
    stays session-free (matching gowa.get_chat_messages)."""

    def __init__(self, session_factory=SessionLocal) -> None:
        self._Session = session_factory

    def get(self, epoch_id: int) -> bytes | None:
        with self._Session() as s:
            row = s.get(EpochKey, epoch_id)
            return bytes(row.wrapped_private) if row else None

    def get_public(self, epoch_id: int) -> str | None:
        with self._Session() as s:
            row = s.get(EpochKey, epoch_id)
            return row.public_key if row else None

    def put(self, epoch_id: int, wrapped_private: bytes, public_b64: str) -> None:
        with self._Session() as s:
            row = s.get(EpochKey, epoch_id)
            if row is None:
                s.add(EpochKey(epoch_id=epoch_id, wrapped_private=wrapped_private,
                               public_key=public_b64))
            else:  # idempotent upsert (e.g. re-publishing the same epoch)
                row.wrapped_private = wrapped_private
                row.public_key = public_b64
            s.commit()

    def delete(self, epoch_id: int) -> None:
        with self._Session() as s:
            s.execute(delete(EpochKey).where(EpochKey.epoch_id == epoch_id))
            s.commit()

    def list_epochs(self) -> list[int]:
        with self._Session() as s:
            rows = s.execute(select(EpochKey.epoch_id)).all()
        return [int(r[0]) for r in rows]


_KEYSTORE: KeyStore | None = None


def get_keystore() -> KeyStore:
    global _KEYSTORE
    if _KEYSTORE is None:
        _KEYSTORE = DbKeyStore()
    return _KEYSTORE
