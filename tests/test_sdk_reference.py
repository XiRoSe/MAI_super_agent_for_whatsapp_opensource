"""Auto-generated SDK reference stays in sync with the mai_sdk module."""

from __future__ import annotations

import backend.code_exec.mai_sdk as mai_sdk
from backend.code_exec.sdk_reference import build_reference, sdk_source


def test_reference_lists_every_public_fn_with_signature():
    ref = build_reference()
    publics = [n for n in dir(mai_sdk)
               if callable(getattr(mai_sdk, n)) and not n.startswith("_")]
    assert publics, "expected SDK functions"
    for name in publics:
        assert f"mai.{name}(" in ref            # named with call syntax
    assert "find_chats" in ref and "run_model" in ref


def test_reference_includes_purpose_from_docstring():
    ref = build_reference().lower()
    assert "recent whatsapp chats" in ref and "run any replicate model" in ref


def test_sdk_source_is_uploadable_text():
    src = sdk_source()
    assert "def find_chats" in src and "def _call" in src


def test_call_posts_to_internal_endpoint(monkeypatch):
    captured = {}

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"result": [{"name": "Sofa", "id": "1@g.us"}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return FakeResp()

    monkeypatch.setenv("MAI_SDK_BASE_URL", "https://app.example")
    monkeypatch.setenv("MAI_SDK_TOKEN", "tok123")
    monkeypatch.setattr(mai_sdk.httpx, "post", fake_post)
    out = mai_sdk.find_chats(query="sofa")
    assert out == [{"name": "Sofa", "id": "1@g.us"}]
    assert captured["url"] == "https://app.example/internal/sdk/find_chats"
    assert captured["json"] == {"query": "sofa"}
    assert captured["headers"]["Authorization"] == "Bearer tok123"
