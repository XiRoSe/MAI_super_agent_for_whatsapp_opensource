"""Semantic recall: ``rank`` keeps relevant items, drops the rest, falls back on None."""

from __future__ import annotations

from dataclasses import dataclass

from backend.agent import semantic


@dataclass
class _Item:
    text: str


# Three orthogonal-ish unit vectors plus a near-duplicate of the query, so cosine
# similarity is fully determined by which text we map to which vector.
_VECS = {
    "query": [1.0, 0.0, 0.0],
    "close": [0.99, 0.10, 0.0],   # ~aligned with query -> high cosine
    "also-close": [0.95, 0.0, 0.30],
    "far": [0.0, 1.0, 0.0],        # orthogonal -> ~0 cosine
    "opposite": [-1.0, 0.0, 0.0],  # negative cosine
}


def _fake_embed(texts):
    # rank() prefixes the query with the e5 instruct prefix; strip it so the
    # fake still maps "query" -> its vector. Passages are passed raw.
    return [_VECS[t.removeprefix(semantic._E5_QUERY_PREFIX)] for t in texts]


def test_rank_embeds_query_with_prefix_and_items_raw(monkeypatch):
    """rank must embed the query WITH the e5 prefix and the item keys verbatim."""
    captured: list[list[str]] = []

    def _capture(texts):
        captured.append(list(texts))
        return [_VECS[t.removeprefix(semantic._E5_QUERY_PREFIX)] for t in texts]

    monkeypatch.setattr(semantic, "embed", _capture)
    items = [_Item("close"), _Item("far")]
    semantic.rank("query", items, key=lambda i: i.text, threshold=0.3, top_k=10)

    sent = captured[0]
    assert sent[0] == semantic._E5_QUERY_PREFIX + "query"
    assert sent[0].startswith(semantic._E5_QUERY_PREFIX)
    assert sent[1:] == ["close", "far"]  # passages embedded raw, no prefix


def test_rank_keeps_relevant_drops_irrelevant(monkeypatch):
    monkeypatch.setattr(semantic, "embed", _fake_embed)
    items = [_Item("close"), _Item("far"), _Item("opposite"), _Item("also-close")]
    out = semantic.rank("query", items, key=lambda i: i.text, threshold=0.3, top_k=10)
    kept = {i.text for i in out}
    assert kept == {"close", "also-close"}  # the two aligned ones clear the bar
    # Sorted most-relevant first: "close" (~0.995) before "also-close" (~0.95).
    assert out[0].text == "close"


def test_rank_respects_top_k(monkeypatch):
    monkeypatch.setattr(semantic, "embed", _fake_embed)
    items = [_Item("close"), _Item("also-close")]
    out = semantic.rank("query", items, key=lambda i: i.text, threshold=0.0, top_k=1)
    assert len(out) == 1 and out[0].text == "close"


def test_rank_returns_none_when_embed_fails(monkeypatch):
    monkeypatch.setattr(semantic, "embed", lambda texts: None)
    items = [_Item("close"), _Item("far")]
    out = semantic.rank("query", items, key=lambda i: i.text, threshold=0.3, top_k=10)
    assert out is None  # signals the caller to fall back to inject-all


def test_rank_empty_items_returns_empty(monkeypatch):
    monkeypatch.setattr(semantic, "embed", _fake_embed)
    assert semantic.rank("query", [], key=lambda i: i.text, threshold=0.3, top_k=5) == []


def test_embed_without_key_returns_none(monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "together_api_key", "")
    assert semantic.embed(["anything"]) is None


def test_embed_caches_by_text(monkeypatch):
    """A repeated text is embedded once: the HTTP layer sees only the new string."""
    from backend.config import settings

    monkeypatch.setattr(settings, "together_api_key", "k")
    semantic._CACHE.clear()
    calls: list[list[str]] = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2, 0.3]} for _ in calls[-1]]}

    import httpx

    def _fake_post(url, **kwargs):
        calls.append(kwargs["json"]["input"])
        return _Resp()

    monkeypatch.setattr(httpx, "post", _fake_post)

    semantic.embed(["a", "b"])
    semantic.embed(["a", "c"])  # "a" cached -> only "c" hits the API
    assert calls == [["a", "b"], ["c"]]
    semantic._CACHE.clear()
