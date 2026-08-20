"""Task 7 — parse + fail-soft decrypt the sealed inbound.

Three scenarios:
1. Sealed payload: decrypts to plaintext; preserves sealed_ciphertext + epoch_id.
2. Sealed payload with shredded key: text == "", sealed_ciphertext still preserved.
3. Clear payload (no epoch_id): legacy clear path unchanged, no sealed fields.
"""

from backend.crypto import keyservice as ks_mod, sealing
from backend.crypto.keystore import InMemoryKeyStore
from backend.whatsapp import gowa
from backend.whatsapp.gowa import GowaWhatsAppProvider


def _svc_and_sealed(monkeypatch, plaintext="@mai ping"):
    svc = ks_mod.KeyService(InMemoryKeyStore())
    monkeypatch.setattr(gowa, "get_key_service", lambda: svc)
    epoch, pub = svc.current_public_key()
    return svc, epoch, sealing.seal(plaintext, pub)


def test_parse_inbound_decrypts_sealed_body(monkeypatch):
    _, epoch, ct = _svc_and_sealed(monkeypatch, "@mai summarize this")
    payload = {"event": "message", "device_id": "mai_acct_7", "payload": {
        "id": "m1", "from": "972500000001@s.whatsapp.net", "from_name": "Eyal",
        "body": ct, "epoch_id": epoch,
    }}
    msgs = GowaWhatsAppProvider().parse_inbound(payload)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.text == "@mai summarize this"          # decrypted for the turn
    assert m.sealed_ciphertext == ct                 # original ciphertext preserved
    assert m.epoch_id == epoch


def test_parse_inbound_failsoft_when_key_shredded(monkeypatch):
    svc, epoch, ct = _svc_and_sealed(monkeypatch, "secret")
    svc._store.delete(epoch)
    svc._cache.pop(epoch, None)  # crypto-shred the epoch
    payload = {"event": "message", "device_id": "d", "payload": {
        "id": "m2", "from": "972500000002@s.whatsapp.net", "body": ct, "epoch_id": epoch,
    }}
    msgs = GowaWhatsAppProvider().parse_inbound(payload)
    # Unreadable now, but still surfaced (so the ciphertext gets stored); empty text.
    assert len(msgs) == 1
    assert msgs[0].text == ""
    assert msgs[0].sealed_ciphertext == ct and msgs[0].epoch_id == epoch


def test_parse_inbound_clear_body_unchanged(monkeypatch):
    # No epoch_id -> legacy clear path (pre-cutover): text stays plaintext, no sealed fields.
    payload = {"event": "message", "device_id": "d", "payload": {
        "id": "m3", "from": "972500000003@s.whatsapp.net", "body": "@mai hi",
    }}
    m = GowaWhatsAppProvider().parse_inbound(payload)[0]
    assert m.text == "@mai hi"
    assert m.sealed_ciphertext is None and m.epoch_id is None
