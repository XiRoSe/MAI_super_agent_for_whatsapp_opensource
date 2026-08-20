"""ServiceRunner POSTs code to the runner service and maps the reply to RunResult."""

from __future__ import annotations

import backend.code_exec.service_runner as sr
from backend.code_exec.service_runner import ServiceRunner


def test_posts_code_and_maps_ok(monkeypatch):
    captured = {}

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"ok": True, "output": "45", "duration_ms": 12}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return Resp()

    monkeypatch.setattr(sr.settings, "runner_url", "https://runner")
    monkeypatch.setattr(sr.settings, "runner_shared_secret", "s3cret")
    monkeypatch.setattr(sr.httpx, "post", fake_post)

    res = ServiceRunner().run("print(sum(range(10)))", token="tok", base_url="https://mai", timeout=30)
    assert res.ok and res.output == "45"
    assert captured["url"] == "https://runner/run"
    assert captured["headers"]["X-Runner-Secret"] == "s3cret"
    assert captured["json"]["code"] == "print(sum(range(10)))"
    assert captured["json"]["token"] == "tok"
    assert captured["json"]["sdk_base_url"] == "https://mai"
    assert "def find_chats" in captured["json"]["sdk_source"]  # real SDK source attached


def test_runner_down_is_graceful(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(sr.settings, "runner_url", "https://runner")
    monkeypatch.setattr(sr.settings, "runner_shared_secret", "s3cret")
    monkeypatch.setattr(sr.httpx, "post", boom)
    res = ServiceRunner().run("print(1)", token="t", base_url="https://mai", timeout=5)
    assert res.ok is False and "couldn" in res.output.lower()


def test_make_runner_prefers_service(monkeypatch):
    import backend.code_exec.e2b_runner as e2b
    monkeypatch.setattr(e2b.settings, "code_exec_enabled", True)
    monkeypatch.setattr(e2b.settings, "runner_url", "https://runner")
    monkeypatch.setattr(e2b.settings, "runner_shared_secret", "s3cret")
    r = e2b.make_runner()
    assert type(r).__name__ == "ServiceRunner"
