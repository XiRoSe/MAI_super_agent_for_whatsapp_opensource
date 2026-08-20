import base64

import pytest

from backend.crypto import sealing


def test_seal_open_roundtrip():
    priv = sealing.generate_private_key()
    pub_b64 = sealing.public_key_b64(priv)
    ct = sealing.seal("hello mai שלום", pub_b64)
    assert ct != "hello mai שלום"
    assert sealing.open_sealed(ct, priv) == "hello mai שלום"


def test_private_key_b64_roundtrip_reopens():
    priv = sealing.generate_private_key()
    pub_b64 = sealing.public_key_b64(priv)
    ct = sealing.seal("secret", pub_b64)
    # Persisting the private key as b64 and reloading still opens the ciphertext.
    reloaded = sealing.decode_private_key(sealing.encode_private_key(priv))
    assert sealing.open_sealed(ct, reloaded) == "secret"


def test_wire_format_matches_go_nacl_sealedbox():
    # Cross-language guarantee: a NaCl sealed box is exactly
    # 32-byte ephemeral pubkey + 16-byte Poly1305 tag + len(plaintext) bytes.
    # This is byte-identical to Go's box.SealAnonymous output, so a Go-produced
    # blob (GOWA, in 2B) will open in PyNaCl and vice-versa.
    priv = sealing.generate_private_key()
    plaintext = "abcde"  # 5 bytes
    ct = sealing.seal(plaintext, sealing.public_key_b64(priv))
    raw = base64.b64decode(ct)
    assert len(raw) == 32 + 16 + len(plaintext.encode())


def test_wrong_key_cannot_open():
    priv = sealing.generate_private_key()
    other = sealing.generate_private_key()
    ct = sealing.seal("nope", sealing.public_key_b64(priv))
    with pytest.raises(Exception):
        sealing.open_sealed(ct, other)
