"""Replicate tools: output stringify, run/search, registry gating (no network)."""

from __future__ import annotations

from backend.tools.replicate_tools import ReplicateTools, _stringify


class FakeFile:
    def __init__(self, url):
        self.url = url


class FakeVersion:
    openapi_schema = {"components": {"schemas": {"Input": {
        "properties": {"prompt": {"type": "string", "x-order": 0, "description": "the text"},
                       "strength": {"type": "number", "default": 0.8, "x-order": 1}},
        "required": ["prompt"]}}}}


class FakeModels:
    def search(self, query):
        class M:
            owner, name, description = "black-forest-labs", "flux-schnell", "fast text-to-image"
        return [M()]

    def get(self, ref):
        class M:
            latest_version = FakeVersion()
        return M()


class FakeClient:
    def __init__(self, ret=None):
        self.models = FakeModels()
        self.ret = ret if ret is not None else FakeFile("https://img/out.png")
        self.captured = {}

    def run(self, ref, input=None):
        self.captured.update(ref=ref, input=input)
        return self.ret


def test_stringify_shapes():
    assert _stringify(FakeFile("u")) == "u"
    assert _stringify(["a", "b"]) == "a, b"
    assert _stringify({"image": FakeFile("u")}) == '{"image": "u"}'


def test_get_model_schema(monkeypatch):
    t = ReplicateTools()
    monkeypatch.setattr(t, "_client", lambda: FakeClient())
    out = t.get_model_schema("owner/model")
    assert '"prompt"' in out and '"strength"' in out
    assert '"required": true' in out and '"default": 0.8' in out


def test_run_model_accepts_dict_input(monkeypatch):
    """The model usually passes a dict directly — must not crash (was the bug)."""
    t = ReplicateTools()
    fake = FakeClient(ret=["https://x/y.webp"])
    monkeypatch.setattr(t, "_client", lambda: fake)
    out = t.run_model("owner/model", {"prompt": "hi", "n": 1})
    assert fake.captured["input"] == {"prompt": "hi", "n": 1}
    assert "https://x/y.webp" in out


def test_run_model_accepts_json_string(monkeypatch):
    t = ReplicateTools()
    fake = FakeClient(ret=["https://x/y.webp"])
    monkeypatch.setattr(t, "_client", lambda: fake)
    t.run_model("owner/model", '{"prompt": "hi"}')
    assert fake.captured["input"] == {"prompt": "hi"}


def test_run_model_rejects_bad_json():
    t = ReplicateTools()
    assert "JSON object" in t.run_model("owner/model", "{not json}")


def test_run_model_rejects_non_object():
    t = ReplicateTools()
    assert "JSON object" in t.run_model("owner/model", "[1,2,3]")


def test_search_models(monkeypatch):
    t = ReplicateTools()
    monkeypatch.setattr(t, "_client", lambda: FakeClient())
    out = t.search_models("text to image")
    assert "black-forest-labs/flux-schnell" in out


def test_registry_gates_on_token(session, two_tenants, monkeypatch):
    from backend.config import settings
    from backend.tools.registry import build_tools

    _, u1, _, _ = two_tenants
    monkeypatch.setattr(settings, "replicate_api_token", "tok")
    assert any(isinstance(t, ReplicateTools) for t in build_tools(u1, session))
    monkeypatch.setattr(settings, "replicate_api_token", "")
    assert not any(isinstance(t, ReplicateTools) for t in build_tools(u1, session))
