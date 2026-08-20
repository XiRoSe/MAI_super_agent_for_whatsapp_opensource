# tests/test_seal_pubkey_api.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.crypto.api import build_seal_router


def _client(monkeypatch, token=""):
    from nacl.secret import SecretBox
    from nacl.utils import random as nacl_random

    from backend.config import settings
    from backend.crypto import api as crypto_api
    from backend.crypto import keyservice as ks_mod
    from backend.crypto.keystore import InMemoryKeyStore

    monkeypatch.setattr(settings, "seal_pubkey_token", token, raising=False)
    monkeypatch.setattr(settings, "mai_env", "production", raising=False)
    # Inject an in-memory KeyService with an explicit KEK so the endpoint does not
    # touch the app DB or require MAI_KEK (the router calls module-global
    # get_key_service, so patching it here is honest — same seam, no real keystore).
    svc = ks_mod.KeyService(InMemoryKeyStore(), kek=nacl_random(SecretBox.KEY_SIZE))
    monkeypatch.setattr(crypto_api, "get_key_service", lambda: svc)
    app = FastAPI()
    app.include_router(build_seal_router())
    return TestClient(app)


def test_returns_epoch_and_pubkey_with_token(monkeypatch):
    c = _client(monkeypatch, token="s3cret")
    r = c.get("/internal/seal-pubkey", headers={"X-Internal-Token": "s3cret"})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["epoch_id"], int)
    assert isinstance(body["public_key"], str) and len(body["public_key"]) > 0


def test_rejects_bad_token_in_production(monkeypatch):
    c = _client(monkeypatch, token="s3cret")
    r = c.get("/internal/seal-pubkey", headers={"X-Internal-Token": "wrong"})
    assert r.status_code == 403


def test_rejects_missing_token_in_production_when_unset(monkeypatch):
    c = _client(monkeypatch, token="")  # unset + production => forbidden
    r = c.get("/internal/seal-pubkey")
    assert r.status_code == 403
