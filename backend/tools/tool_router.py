"""LLM tool router — pick which optional tool categories a message needs.

Replaces hardcoded keyword gating: the model reads the user's message (in ANY
language) and returns the relevant categories. Bounded + cached + fail-open:
on any error/timeout it returns None, and the caller includes the full toolset.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from functools import lru_cache

from backend.logging_config import get_logger

logger = get_logger(__name__)

# Category -> what it covers (also shown to the router model).
CATEGORIES = {
    "generation": "create or transform images/video/audio, transcribe, restyle a photo, sticker/meme/avatar",
    "scheduling": "reminders, recurring tasks, anything for later or on a schedule, timezones",
    "integrations": "connect or use an external account (Gmail, Google Calendar, Outlook, Notion, Slack, GitHub…) or stored credentials",
    "docs": "save, read, edit, or list the user's documents / notes / knowledge base",
    "crosschat": "read, summarize, or forward messages from OTHER chats or groups",
    "access": "let other people use Mai, or manage who is allowed to invoke it",
    "profile": "look up a WhatsApp contact's profile picture or public details by phone number",
}

_SYSTEM = (
    "You route an AI assistant's OPTIONAL tools. Read the user's message (it may be in "
    "any language) and decide which tool CATEGORIES could help. Be INCLUSIVE — include a "
    "category if there's any reasonable chance it's needed.\n\nCategories:\n"
    + "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())
    + "\n\nReply with ONLY a JSON array of the applicable category keys "
    '(e.g. ["generation","profile"]). Use [] if it is just conversation needing none.'
)


@lru_cache(maxsize=512)
def select_categories(message: str) -> frozenset[str] | None:
    """Return the relevant tool categories for `message`, or None to include all."""
    from backend.config import settings

    msg = (message or "").strip()
    if not msg or not settings.tool_router_enabled:
        return None
    try:
        cats = _classify(msg, settings.tool_router_timeout_seconds,
                         settings.tool_router_model_id)
    except FuturesTimeout:
        logger.warning("tool_router.timeout")
        return None
    except Exception as exc:
        logger.warning("tool_router.failed", extra={"ctx_error": str(exc)})
        return None
    logger.info("tool_router.selected", extra={"ctx_categories": sorted(cats)})
    return frozenset(cats)


def _classify(msg: str, timeout: int, model_id: str) -> set[str]:
    from agno.agent import Agent

    from backend.models.factory import get_model

    def run() -> str:
        agent = Agent(
            model=get_model(temperature=0, model_id=model_id or None),
            system_message=_SYSTEM, telemetry=False, markdown=False,
            add_history_to_context=False,
        )
        return getattr(agent.run(msg), "content", "") or ""

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        text = executor.submit(run).result(timeout=timeout)
    finally:
        executor.shutdown(wait=False)
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if not match:
        return set(CATEGORIES)  # unparseable -> be safe, include all
    raw = json.loads(match.group(0))
    return {str(c).strip().lower() for c in raw} & set(CATEGORIES)
