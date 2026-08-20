"""NaCl sealed-box primitives (X25519 + XSalsa20-Poly1305).

A sealed box encrypts to a recipient PUBLIC key with an anonymous ephemeral
sender; only the matching PRIVATE key opens it. The wire format (ephemeral
pubkey ‖ box) is identical to Go's `golang.org/x/crypto/nacl/box.SealAnonymous`,
so 2B's Go-sealed blobs will open here unchanged.
"""

from __future__ import annotations

import base64

from nacl.public import PrivateKey, PublicKey, SealedBox


def generate_private_key() -> PrivateKey:
    """A fresh random X25519 private key (its public key is `.public_key`)."""
    return PrivateKey.generate()


def public_key_b64(priv: PrivateKey) -> str:
    return base64.b64encode(bytes(priv.public_key)).decode("ascii")


def encode_private_key(priv: PrivateKey) -> str:
    return base64.b64encode(bytes(priv)).decode("ascii")


def decode_private_key(b64: str) -> PrivateKey:
    return PrivateKey(base64.b64decode(b64))


def seal(plaintext: str, public_key_b64: str) -> str:
    box = SealedBox(PublicKey(base64.b64decode(public_key_b64)))
    return base64.b64encode(box.encrypt(plaintext.encode("utf-8"))).decode("ascii")


def open_sealed(ciphertext_b64: str, priv: PrivateKey) -> str:
    box = SealedBox(priv)
    return box.decrypt(base64.b64decode(ciphertext_b64)).decode("utf-8")
