"""The /internal/sdk endpoint: token auth + dispatch."""

from __future__ import annotations

from starlette.testclient import TestClient
from fastapi import FastAPI

from backend.code_exec.sdk_api import build_sdk_router


def _client():
    app = FastAPI()
    app.include_router(build_sdk_router())
    return TestClient(app)


def test_missing_token_is_401():
    r = _client().post("/internal/sdk/find_chats", json={})
    assert r.status_code == 401


def test_valid_token_dispatches(monkeypatch):
    import backend.code_exec.sdk_api as api
    from backend.code_exec.token_store import TokenContext

    ctx = TokenContext(account_id=5, user_id=2, device_id="dev1", outbound_allowed=True, send_count=0, expires_at=9e18)
    monkeypatch.setattr(api.token_store, "get", lambda t: ctx if t == "good" else None)
    monkeypatch.setattr(api, "dispatch",
                        lambda fn, args, c, session, token: [{"name": "Sofa"}])
    monkeypatch.setattr(api, "SessionLocal", lambda: _FakeSession())

    r = _client().post("/internal/sdk/find_chats", json={"query": "s"},
                       headers={"Authorization": "Bearer good"})
    assert r.status_code == 200 and r.json() == {"result": [{"name": "Sofa"}]}


def test_dispatch_error_is_400(monkeypatch):
    import backend.code_exec.sdk_api as api
    from backend.code_exec.dispatch import DispatchError
    from backend.code_exec.token_store import TokenContext

    ctx = TokenContext(account_id=5, user_id=2, device_id="dev1", outbound_allowed=True, send_count=0, expires_at=9e18)
    monkeypatch.setattr(api.token_store, "get", lambda t: ctx)
    def boom(*a, **k): raise DispatchError("nope")
    monkeypatch.setattr(api, "dispatch", boom)
    monkeypatch.setattr(api, "SessionLocal", lambda: _FakeSession())
    r = _client().post("/internal/sdk/x", json={}, headers={"Authorization": "Bearer t"})
    assert r.status_code == 400 and r.json()["error"] == "nope"


class _FakeSession:
    def close(self): pass
