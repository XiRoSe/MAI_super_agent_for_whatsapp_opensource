"""Model factory — the one place the chat model is constructed.

`get_model()` returns the configured Agno model. Switching `MODEL_PROVIDER`
between `openrouter` (GLM via the OpenAI-compatible endpoint) and `anthropic`
(Claude Sonnet) changes nothing else in the codebase — every agent calls
`get_model()`.

Prompt caching is enabled where the provider supports it, because Mai re-sends
its system prompt every turn.
"""

from __future__ import annotations

from typing import Any

from backend.config import settings
from backend.logging_config import get_logger

logger = get_logger(__name__)


def get_model(*, temperature: float | None = None, model_id: str | None = None,
              provider: str | None = None) -> Any:
    """Build and return a configured Agno chat model.

    Args:
        temperature: override the configured temperature for this model instance.
        model_id: override the model id (e.g. a smaller/faster model for routing).
        provider: override the provider (e.g. the fallback provider on failure).
    """
    provider = provider or settings.model_provider
    model_id = model_id or settings.model_id
    temp = settings.model_temperature if temperature is None else temperature

    if provider == "openrouter":
        return _openrouter_model(model_id, temp)
    if provider == "together":
        return _together_model(model_id, temp)
    if provider == "anthropic":
        return _anthropic_model(model_id, temp)
    raise RuntimeError(f"Unsupported MODEL_PROVIDER={provider!r}")


def _retry_kwargs() -> dict:
    """Retry transient provider failures (5xx/timeouts) before surfacing an error."""
    return {"retries": settings.model_retries, "delay_between_retries": 1}


# GLM is hosted by both providers we hold keys for — so when no explicit fallback is
# configured, "the OTHER provider, same model family" is a safe default that needs no
# new secrets (prod already carries TOGETHER_API_KEY for embeddings). A hard primary
# failure (credit exhaustion, provider outage) fails every same-provider retry; the
# cross-provider hop is the only thing that turns it into a served reply.
_DEFAULT_FALLBACKS: dict[str, tuple[str, str]] = {
    "openrouter": ("together", "zai-org/GLM-5.2"),
    "together": ("openrouter", "z-ai/glm-5.2"),
}


def _provider_key_present(provider: str) -> bool:
    return bool({"together": settings.together_api_key,
                 "openrouter": settings.openrouter_api_key}.get(provider))


def get_fallback_model() -> Any | None:
    """The secondary model used when the primary provider fails (or None if unset).

    A different provider (e.g. OpenRouter) so a provider-specific outage on the
    primary (e.g. Together 500s) doesn't take Mai down. When no explicit fallback
    is configured, defaults to the OTHER GLM host if we hold its key.
    """
    provider, model_id = settings.fallback_model_provider, settings.fallback_model_id
    if not provider or not model_id:
        provider, model_id = _DEFAULT_FALLBACKS.get(str(settings.model_provider), ("", ""))
        if not provider or not _provider_key_present(provider):
            return None
        logger.info("model.fallback_default", extra={"ctx_provider": provider,
                                                     "ctx_model": model_id})
    try:
        return get_model(provider=provider, model_id=model_id)
    except Exception as exc:  # pragma: no cover - misconfig shouldn't crash a turn
        logger.warning("model.fallback_unavailable", extra={"ctx_error": str(exc)})
        return None


def _together_model(model_id: str, temperature: float):
    settings.require("together_api_key")
    # Together's serverless 429s ask for exponential backoff from ~2s; a fixed 1s
    # retry lands back inside the same throttle window (2026-07-14 prod hiccups).
    kwargs = dict(id=model_id, api_key=settings.together_api_key, temperature=temperature,
                  **{**_retry_kwargs(), "delay_between_retries": 2, "exponential_backoff": True})
    try:
        from agno.models.together import Together

        logger.info("model.factory", extra={"ctx_provider": "together", "ctx_model": model_id})
        return Together(**kwargs)
    except Exception:  # pragma: no cover - fallback to generic OpenAI-compatible
        from agno.models.openai import OpenAILike

        logger.info(
            "model.factory.fallback",
            extra={"ctx_provider": "together:openai-like", "ctx_model": model_id},
        )
        return OpenAILike(base_url=settings.together_base_url, **kwargs)


def _openrouter_model(model_id: str, temperature: float) -> Any:
    settings.require("openrouter_api_key")
    # Prefer Agno's dedicated OpenRouter class; fall back to the generic
    # OpenAI-compatible client pointed at OpenRouter's base_url. Both speak the
    # OpenAI Chat Completions protocol, which is what the brief specifies.
    kwargs: dict[str, Any] = dict(
        id=model_id,
        api_key=settings.openrouter_api_key,
        temperature=temperature,
        # agno's OpenRouter class defaults max_tokens to 1024 (Together's doesn't) —
        # left unset, replies truncate mid-sentence and GLM's reasoning tokens can
        # consume the whole budget, yielding empty runs.
        max_tokens=settings.model_max_tokens,
        **_retry_kwargs(),
    )
    try:
        from agno.models.openrouter import OpenRouter

        logger.info("model.factory", extra={"ctx_provider": "openrouter", "ctx_model": model_id})
        return OpenRouter(**kwargs)
    except Exception:  # pragma: no cover - exercised only when class is absent
        from agno.models.openai import OpenAILike

        logger.info(
            "model.factory.fallback",
            extra={"ctx_provider": "openrouter:openai-like", "ctx_model": model_id},
        )
        return OpenAILike(base_url=settings.openrouter_base_url, **kwargs)


def _anthropic_model(model_id: str, temperature: float) -> Any:
    settings.require("anthropic_api_key")
    from agno.models.anthropic import Claude

    kwargs: dict[str, Any] = dict(
        id=model_id,
        api_key=settings.anthropic_api_key,
        temperature=temperature,
        **_retry_kwargs(),
    )
    # Anthropic supports prompt caching of the system prompt; let Agno apply it.
    if settings.model_enable_prompt_caching:
        kwargs["cache_system_prompt"] = True
    logger.info("model.factory", extra={"ctx_provider": "anthropic", "ctx_model": model_id})
    try:
        return Claude(**kwargs)
    except TypeError:
        # Older/newer Agno may not accept cache_system_prompt; retry without it.
        kwargs.pop("cache_system_prompt", None)
        return Claude(**kwargs)
