"""/run requires the shared secret and returns the executor's result."""

from __future__ import annotations

import os

os.environ["RUNNER_SHARED_SECRET"] = "s3cret"

from starlette.testclient import TestClient  # noqa: E402

from runner.app import app  # noqa: E402

client = TestClient(app)
BODY = {"code": "print(6*7)", "sdk_source": "x=1\n", "token": "t",
        "sdk_base_url": "http://x", "timeout": 10}


def test_health():
    assert client.get("/health").json()["ok"] is True


def test_run_requires_secret():
    assert client.post("/run", json=BODY).status_code == 401
    assert client.post("/run", json=BODY, headers={"X-Runner-Secret": "wrong"}).status_code == 401


def test_run_executes_with_secret():
    r = client.post("/run", json=BODY, headers={"X-Runner-Secret": "s3cret"})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["output"].strip() == "42"
