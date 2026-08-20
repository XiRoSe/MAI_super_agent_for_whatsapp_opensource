"""Lightweight semantic recall: which saved memories/skills matter THIS turn.

We embed the user's message and each candidate's trigger text with Together's
hosted multilingual embeddings (the user writes Hebrew + English), then keep the
candidates whose cosine similarity clears a threshold.

Errors should never pass silently here — they should pass *gracefully*: every
public function returns ``None`` on any failure (missing key, HTTP error, bad
payload) so callers transparently fall back to injecting everything, the prior
behavior. A turn must never crash because the embedder is missing or unhappy.

No torch, no local models, no downloads — just httpx for the REST call and numpy
for the cosine math.
"""

from __future__ import annotations

from typing import Callable, Sequence, TypeVar

import numpy as np

from backend.config import settings
from backend.logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

_EMBED_URL = "https://api.together.xyz/v1/embeddings"
_HTTP_TIMEOUT = 15.0  # seconds; a slow embedder must not stall a turn

# e5-instruct is an ASYMMETRIC retrieval model: the *query* must carry a task
# instruction prefix while the *passages* (skill/memory trigger texts) stay RAW.
# Without the prefix its cosine baseline is so high (~0.81 even for unrelated
# pairs) that any sane threshold selects everything; with it, relevant pairs
# separate cleanly (~0.82-0.90) from irrelevant ones (~0.75-0.77).
_E5_QUERY_PREFIX = ("Instruct: Given a user's message, retrieve the skills and saved "
                    "facts relevant to it.\nQuery: ")

# In-memory, process-wide cache keyed by the exact text. Skill/memory trigger
# texts are stable across turns, so this saves re-embedding the same strings
# every message. Bounded so a long-lived process can't grow it without limit.
_CACHE: dict[str, list[float]] = {}
_CACHE_MAX = 2048

# e5 caps at 512 tokens. Hebrew tokenizes at ~2 tokens/char, so a ~440-char Hebrew
# memory overflows and 400s the WHOLE batch (killing recall for the turn). Truncate
# every embed input to a safe lead — similarity only needs the gist, and the full
# memory/skill text is still injected into the prompt regardless.
_MAX_EMBED_CHARS = 256


def _cache_put(text: str, vec: list[float]) -> None:
    """Store one embedding, evicting oldest entries past the bound (FIFO-ish)."""
    if len(_CACHE) >= _CACHE_MAX:
        # Drop the oldest ~10% so we amortize eviction cost.
        for stale in list(_CACHE)[: _CACHE_MAX // 10 or 1]:
            _CACHE.pop(stale, None)
    _CACHE[text] = vec


def embed(texts: Sequence[str]) -> list[list[float]] | None:
    """Embed ``texts`` via Together; return one vector per input, or None on failure.

    Cached per text so repeated skill/memory strings aren't re-embedded each turn.
    Returns None (never raises) when the feature can't run — no API key, an HTTP
    error, or an unexpected payload — which signals callers to fall back.
    """
    if not texts:
        return []
    if not settings.together_api_key:
        return None  # no key -> feature simply off; caller injects all

    # Cap each input so a long (esp. Hebrew) string can't overflow e5's 512-token limit.
    texts = [t[:_MAX_EMBED_CHARS] for t in texts]

    # Only call the API for texts we haven't embedded before.
    missing = [t for t in texts if t not in _CACHE]
    if missing:
        try:
            import httpx

            resp = httpx.post(
                _EMBED_URL,
                headers={"Authorization": f"Bearer {settings.together_api_key}"},
                json={"model": settings.embed_model, "input": list(missing)},
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if len(data) != len(missing):
                raise ValueError("embedding count mismatch")
            for text, row in zip(missing, data):
                _cache_put(text, row["embedding"])
        except Exception as exc:
            logger.warning("semantic.embed_failed", extra={"ctx_error": str(exc)})
            return None

    return [_CACHE[t] for t in texts]


def _unit(matrix: np.ndarray) -> np.ndarray:
    """L2-normalize each row so a dot product is the cosine similarity."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0  # guard the zero vector (avoid divide-by-zero)
    return matrix / norms


def rank(
    query: str,
    items: Sequence[T],
    key: Callable[[T], str],
    *,
    threshold: float,
    top_k: int,
) -> list[T] | None:
    """Return the items whose trigger text is semantically close to ``query``.

    Embeds the query and each ``key(item)``, then keeps items with cosine
    similarity >= ``threshold``, sorted most-relevant first and capped at
    ``top_k``. Returns None (caller should fall back to injecting all) if the
    embedder is unavailable; returns [] if nothing clears the threshold.
    """
    if not items:
        return []

    # e5-instruct needs the asymmetric query-prefix / raw-passage format: prefix
    # ONLY the query with the task instruction; embed item triggers verbatim.
    vectors = embed([_E5_QUERY_PREFIX + query] + [key(it) for it in items])
    if vectors is None:
        return None  # embedder unavailable -> signal fallback

    mat = _unit(np.asarray(vectors, dtype=np.float32))
    query_vec, item_vecs = mat[0], mat[1:]
    scores = item_vecs @ query_vec  # cosine, since rows are unit-length

    # Pair each item with its score, keep those above the bar, best first.
    scored = [(float(s), it) for s, it in zip(scores, items) if float(s) >= threshold]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [it for _, it in scored[:top_k]]
