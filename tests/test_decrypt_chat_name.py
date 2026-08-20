"""Chat-name decryption — the read side of GOWA's `NSEAL:<epoch>:<ct>` names.

GOWA seals each stored chat name to Mai's epoch key with the SAME sealed-box
primitive as message bodies; Mai decrypts it when it reads the `/chats` list.

Three scenarios mirror the inbound-body tests:
1. A name sealed to the current pubkey decrypts back to the original.
2. A shredded/None epoch -> '[expired]'.
3. A plaintext (non-NSEAL) name passes through unchanged.
"""

from backend.crypto import keyservice as ks_mod, sealing
from backend.crypto.keystore import InMemoryKeyStore
from backend.whatsapp import gowa


def _svc(monkeypatch):
    svc = ks_mod.KeyService(InMemoryKeyStore())
    monkeypatch.setattr(gowa, "get_key_service", lambda: svc)
    return svc


def _seal_name(svc, name: str) -> str:
    epoch, pub = svc.current_public_key()
    return f"NSEAL:{epoch}:{sealing.seal(name, pub)}"


def test_sealed_name_round_trips(monkeypatch):
    svc = _svc(monkeypatch)
    raw = _seal_name(svc, "Eyal Cohen")
    assert gowa._decrypt_chat_name(raw, "972500000001@s.whatsapp.net") == "Eyal Cohen"


def test_shredded_epoch_yields_expired(monkeypatch):
    svc = _svc(monkeypatch)
    raw = _seal_name(svc, "Secret Group")
    epoch = int(raw.split(":", 2)[1])
    svc._store.delete(epoch)            # crypto-shred the epoch (private key gone)
    svc._cache.pop(epoch, None)
    assert gowa._decrypt_chat_name(raw, "120@g.us") == "[expired]"


def test_plaintext_name_passthrough(monkeypatch):
    _svc(monkeypatch)
    # Legacy / non-NSEAL names are returned verbatim.
    assert gowa._decrypt_chat_name("Marketing Team", "120@g.us") == "Marketing Team"
    assert gowa._decrypt_chat_name("", "x@s.whatsapp.net") == ""
    assert gowa._decrypt_chat_name(None, "x@s.whatsapp.net") == ""
