"""Auth tests: register/login/JWT + the auth router end to end."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from backend.auth.security import create_token, decode_token, hash_password, verify_password
from backend.auth.service import AuthError, authenticate, register
from backend.db.tenancy import Account


def test_password_hash_roundtrip():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True
    assert verify_password("wrong", h) is False


def test_jwt_roundtrip_and_tamper():
    tok = create_token(account_id=7, auth_user_id=3, email="a@b.com")
    payload = decode_token(tok)
    assert payload["sub"] == "7" and payload["auth_user_id"] == 3
    assert decode_token(tok + "x") is None


def test_register_creates_account_and_user(session):
    user = register(session, email="Alice@Example.com ", password="supersecret")
    assert user.email == "alice@example.com"  # normalized
    assert session.get(Account, user.account_id) is not None
    assert authenticate(session, email="alice@example.com", password="supersecret") is not None
    assert authenticate(session, email="alice@example.com", password="nope") is None


def test_register_rejects_dupe_and_weak(session):
    register(session, email="bob@example.com", password="longenough")
    with pytest.raises(AuthError):
        register(session, email="bob@example.com", password="longenough")  # dupe
    with pytest.raises(AuthError):
        register(session, email="new@example.com", password="short")  # weak


@pytest.fixture()
def auth_client(session):
    import backend.auth.router as router_mod
    from backend.auth.deps import get_db

    app = FastAPI()
    app.include_router(router_mod.build_auth_router())

    def _override_db():
        yield session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


def test_auth_router_register_login_me(auth_client):
    r = auth_client.post("/auth/register", json={"email": "carol@example.com", "password": "longenough"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]

    r2 = auth_client.post("/auth/login", json={"email": "carol@example.com", "password": "longenough"})
    assert r2.status_code == 200
    r3 = auth_client.post("/auth/login", json={"email": "carol@example.com", "password": "bad"})
    assert r3.status_code == 401

    me = auth_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "carol@example.com"
    assert auth_client.get("/auth/me").status_code == 401
