"""The Mai agent: assembles model + memory/learning + tools per user.

`build_agent()` returns an Agno `Agent` scoped to one Mai user — its
`user_id`/`session_id` key Agno's per-user memory and session stores, giving
hard isolation at the agent layer too. `respond()` is the single model-calling
seam (tests mock it for offline runs).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any, Sequence

from agno.agent import Agent
from agno.learn import LearningMachine, LearningMode, UserMemoryConfig, UserProfileConfig

from backend.agent.prompts import build_system_prompt
from backend.db.agno_db import get_agno_db
from backend.db.tenancy import User
from backend.logging_config import get_logger, set_tenant
from backend.models.factory import get_model

logger = get_logger(__name__)


def default_session_id(user: User) -> str:
    """Stable per-user session id so chat history threads across messages."""
    return f"{user.agno_user_id}:main"


def wa_session_id(user: User, chat_id: str | None) -> str:
    """Per-CHAT session id so history never bleeds between WhatsApp conversations
    (or into the web session). Each group/DM threads on its own."""
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", (chat_id or "dm").lower()).strip("-") or "dm"
    return f"{user.agno_user_id}:wa:{slug}"


def _build_learning(db: Any) -> LearningMachine | None:
    """Agentic memory + learned user profile, the Agno Learning Machine.

    AGENTIC mode gives Mai explicit memory tools so it decides what's worth
    remembering (no memory-spam), and a structured user profile it refines over
    time. Falls back to None if the installed Agno can't build it, so a turn
    never crashes for lack of memory.
    """
    try:
        return LearningMachine(
            db=db,
            # AGENTIC: Mai decides what to remember and SAVES it with a memory tool
            # (no auto-spam). Enable the agent-facing memory tools explicitly so the
            # tool is actually available, and the prompt nudges Mai to use it.
            user_memory=UserMemoryConfig(
                mode=LearningMode.AGENTIC,
                enable_agent_tools=True,
                enable_add_memory=True,
                enable_update_memory=True,
                agent_can_update_memories=True,
            ),
            user_profile=UserProfileConfig(mode=LearningMode.AGENTIC),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("learning.disabled", extra={"ctx_error": str(exc)})
        return None


def build_runtime_system_prompt(
    user: User,
    *,
    session: Any | None = None,
    db: Any | None = None,
    query: str | None = None,
    include_sdk_reference: bool | None = None,
) -> str:
    """The FULL per-turn system prompt production runs: the base prompt + the user's
    beliefs + auto-recalled long-term memory + auto-recalled skills + (in CodeAct mode) the
    mai.* SDK reference. Extracted from build_agent so OTHER call sites (notably the eval
    harness) build the EXACT same prompt — one source of truth, no forked copy to drift.
    Every layer is best-effort and self-guarding: a turn never breaks if beliefs/memory/skills
    are unavailable. `db` is the agno memory store (defaults to the process db); pass an empty
    one for a hermetic, memory-less context (e.g. evals). `include_sdk_reference` forces the
    mai.* SDK block on/off; None => follow codeact_mode (the eval forces the code_exec path,
    so it passes True)."""
    from backend.config import settings

    if db is None:
        db = get_agno_db()
    beliefs = None
    if session is not None:
        try:
            from backend.agent.beliefs import get_beliefs

            beliefs = get_beliefs(session, user)
        except Exception:
            beliefs = None
    system_message = build_system_prompt(
        user_display_name=user.display_name,
        user_timezone=user.timezone,
        beliefs=beliefs,
    )
    # Inject saved long-term memory for reliable recall (same agno DB the dashboard
    # reads + the MemoryTools writes). Doesn't depend on Agno's memory-context wiring.
    try:
        mems = db.get_user_memories(user_id=user.agno_user_id) or []
        # Semantic recall: when we have the current message, inject only the memories
        # relevant to it. If the embedder is unavailable (rank -> None) we keep ALL
        # memories, the prior behavior — a turn never breaks for lack of embeddings.
        if settings.semantic_recall_enabled and query and mems:
            from backend.agent import semantic

            picked = semantic.rank(
                query, list(mems),
                key=lambda m: getattr(m, "memory", ""),
                threshold=settings.memory_recall_threshold,
                top_k=settings.memory_recall_top_k,
            )
            if picked is not None:
                mems = picked
        facts = "\n".join(f"- {getattr(m, 'memory', '')}" for m in mems
                          if getattr(m, "memory", ""))[:1500]
        if facts:
            system_message += ("\n\nWHAT YOU REMEMBER ABOUT THIS USER (use naturally; "
                               "save/update with your memory tool when it changes):\n" + facts)
    except Exception:
        pass

    # Skills reach the model differently per mode:
    #   "vector" — recall the relevant skills and inject their FULL bodies here.
    #   "stub"   — inject agno's stub list (name+desc + the get_skill_instructions protocol)
    #              here, AND attach agno Skills to the Agent (the fetch tool) in build_agent.
    # We append the stub snippet ourselves on purpose: agno only injects it when IT builds the
    # system message, but Mai always passes an explicit system_message — so agno returns early
    # and never adds the snippet. Without it the model has the fetch tool but no skill list.
    if session is not None:
        try:
            if settings.skill_mode == "stub":
                from backend.skills_store import agno_skills

                snippet = agno_skills(session, user).get_system_prompt_snippet()
                if snippet:
                    system_message += "\n\n" + snippet
            else:
                from backend.skills_store import skills_context

                sk = skills_context(session, user, query=query)
                if sk:
                    system_message += "\n\n" + sk
        except Exception:
            pass

    # The mai.* SDK reference — the action vocabulary for code_exec. It's wired into the
    # code_exec toolkit's `instructions`, but agno only folds toolkit instructions into the
    # prompt when IT builds the system message; Mai always passes an explicit one, so agno
    # returns early and drops them (same swallow as the skills snippet above). Without it the
    # CodeAct model can't see what mai.* functions exist — it can't summarize a chat if it
    # never learns mai.read_chat. Only relevant in CodeAct mode; off-codeact those functions
    # are registered as DIRECT tools, so we skip the ~1k tokens.
    use_sdk = settings.codeact_mode if include_sdk_reference is None else include_sdk_reference
    if use_sdk:
        try:
            from backend.code_exec.sdk_reference import build_reference

            ref = build_reference()
            if ref:
                system_message += "\n\n" + ref
        except Exception:
            pass
    return system_message


def build_agent(
    user: User,
    *,
    tools: Sequence[Any] | None = None,
    session_id: str | None = None,
    model: Any | None = None,
    session: Any | None = None,
    query: str | None = None,
) -> Agent:
    db = get_agno_db()
    system_message = build_runtime_system_prompt(user, session=session, db=db, query=query)
    from backend.config import settings

    # CodeAct mode: constrain the model to the REGISTERED tools (so it can't call the
    # data/gen functions as bare tools — it must reach them via code_exec). tool_call_limit
    # lets the turn end with a natural-language reply after the tool calls.
    codeact = settings.codeact_mode and bool(tools)
    # STUB mode: hand the Agent agno-native Skills (name+description stub injected, full body
    # fetched on demand via get_skill_instructions). VECTOR mode injects bodies into the prompt.
    skills_obj = None
    if session is not None and settings.skill_mode == "stub":
        try:
            from backend.skills_store import agno_skills

            skills_obj = agno_skills(session, user)
        except Exception:
            skills_obj = None
    agent = Agent(
        name="Mai",
        model=model or get_model(),
        db=db,
        learning=None,  # replaced by our reliable MemoryTools (LM exposed no tools)
        tools=list(tools) if tools else None,
        skills=skills_obj,
        tool_choice="required" if codeact else None,
        tool_call_limit=settings.agent_tool_call_limit if codeact else None,
        reasoning=settings.agent_reasoning,  # think step-by-step before acting (flag-gated)
        system_message=system_message,
        user_id=user.agno_user_id,
        session_id=session_id or default_session_id(user),
        add_history_to_context=True,
        # Keep context lean: only the last few exchanges of THIS conversation load
        # per turn (the live chat messages are also re-supplied each turn anyway).
        num_history_runs=6,
        markdown=False,  # WhatsApp renders plain text; CLI is fine without md
        telemetry=False,
    )
    return agent


def _as_text(value: Any) -> str | None:
    """Coerce a message/tool content into a usable, non-empty string, or None.

    Tolerates None, strings, and lists of parts (e.g. content blocks with a
    ``text`` field or bare strings). Never raises.
    """
    if isinstance(value, str):
        s = value.strip()
        return s or None
    if isinstance(value, list):  # content may be a list of parts/blocks
        parts: list[str] = []
        for part in value:
            if isinstance(part, str):
                if part.strip():
                    parts.append(part.strip())
            elif isinstance(part, dict):
                txt = part.get("text") or part.get("content")
                if isinstance(txt, str) and txt.strip():
                    parts.append(txt.strip())
        joined = "\n".join(parts).strip()
        return joined or None
    return None


def _is_bare_media_url(text: str) -> bool:
    """A lone media URL isn't a user-facing answer (the media is delivered
    separately). A transcript or any prose that merely *contains* a URL is fine."""
    if not text or " " in text or "\n" in text:
        return False
    low = text.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        return False
    return low.endswith((".mp3", ".wav", ".ogg", ".m4a", ".mp4", ".mov",
                         ".png", ".jpg", ".jpeg", ".gif", ".webp", ".webm", ".pdf"))


def _recover_reply(result: Any) -> str | None:
    """Best-effort salvage of a meaningful reply when ``result.content`` is empty.

    GLM-5.1 sometimes ends a tool-heavy turn with an empty final assistant
    message even though the turn succeeded (e.g. a transcription tool produced
    the answer). Priority:
      a) the last ASSISTANT message with non-empty text (text can land one
         message earlier than the empty final one);
      b) else the last TOOL result/output (for a transcription, that IS the
         answer the user wants);
      c) else None.
    Defensive throughout: missing attrs, non-string contents, lists, None all
    tolerated — never raises.
    """
    try:
        # Only THIS run's messages are salvage candidates: agno also packs the
        # session history into result.messages (from_history=True), and salvaging
        # from it re-sends a previous reply instead of failing into the retry ladder.
        messages = [m for m in (getattr(result, "messages", None) or [])
                    if not getattr(m, "from_history", False)]
        # (a) last assistant message with real text.
        for msg in reversed(messages):
            if getattr(msg, "role", None) != "assistant":
                continue
            text = _as_text(getattr(msg, "content", None))
            if text and not _is_bare_media_url(text):
                return text

        # (b) last usable tool output. Prefer the structured `tools` list, then
        # fall back to tool-role messages.
        tools = getattr(result, "tools", None) or []
        for tool in reversed(tools):
            if getattr(tool, "tool_call_error", None):
                continue
            text = _as_text(getattr(tool, "result", None))
            if text and not _is_bare_media_url(text):
                return text
        for msg in reversed(messages):
            if getattr(msg, "role", None) != "tool":
                continue
            text = _as_text(getattr(msg, "content", None))
            if text and not _is_bare_media_url(text):
                return text
    except Exception:  # pragma: no cover - recovery must never break a turn
        return None
    return None


def _record_tokens(session: Any, user: User, result: Any) -> None:
    """Meter model token usage from the run result (best-effort)."""
    metrics = getattr(result, "metrics", None)
    if metrics is None or session is None:
        return
    try:
        from backend.db.tenancy import UsageKind
        from backend.metering.usage import record_usage

        in_tokens = int(getattr(metrics, "input_tokens", 0) or 0)
        out_tokens = int(getattr(metrics, "output_tokens", 0) or 0)
        if in_tokens:
            record_usage(session, account_id=user.account_id, user_id=user.id,
                         kind=UsageKind.tokens_in, quantity=in_tokens)
        if out_tokens:
            record_usage(session, account_id=user.account_id, user_id=user.id,
                         kind=UsageKind.tokens_out, quantity=out_tokens)
    except Exception as exc:  # pragma: no cover - metering must never break a turn
        logger.warning("metering.tokens_failed", extra={"ctx_error": str(exc)})


def respond(
    user: User,
    text: str,
    *,
    tools: Sequence[Any] | None = None,
    session_id: str | None = None,
    model: Any | None = None,
    session: Any | None = None,
) -> str:
    """Run one conversational turn and return Mai's reply text.

    This is the model boundary. A failed model/tool call degrades gracefully:
    we log the detail and return a useful message rather than crashing the turn.
    If `session` is provided, model token usage is metered for the tenant.
    """
    set_tenant(user.account_id, user.id)
    sid = session_id or default_session_id(user)

    # The retry ladder: primary, primary again (transient ERROR-status runs / dead
    # streams — measured ~10% of heavy media turns — usually land on a second try),
    # then the FALLBACK PROVIDER (a hard primary failure like credit exhaustion or a
    # provider outage fails every same-provider retry; only crossing providers helps).
    # Fallback only when the caller didn't pin a model.
    attempts: list[tuple[str, Any]] = [("primary", model), ("primary_retry", model)]
    if model is None:
        from backend.models.factory import get_fallback_model

        fb = get_fallback_model()
        if fb is not None:
            attempts.append(("fallback", fb))

    for label, m in attempts:
        try:
            if label != "primary":
                logger.info("agent.retry_attempt", extra={"ctx_user_id": user.id,
                                                          "ctx_attempt": label})
            reply = _attempt(user, text, tools=tools, session_id=session_id, model=m,
                             session=session, sid=sid)
            if session is not None:  # advance Mai's evolving perspective (bg reflection)
                try:
                    from backend.agent.beliefs import record_turn

                    record_turn(session, user)
                except Exception:
                    pass
            return reply
        except FuturesTimeout:
            # A timeout is a slow turn, not a broken provider — retrying doubles the
            # user's wait for the same likely outcome, so stop here (existing behavior).
            logger.warning("agent.turn_timeout", extra={"ctx_user_id": user.id,
                                                        "ctx_attempt": label})
            return ("That one took too long, so I stopped to avoid getting stuck. "
                    "Please try again — maybe a bit simpler.")
        except Exception as exc:
            log = logger.exception if label == "fallback" else logger.warning
            log(f"agent.{label}_failed", extra={"ctx_error": str(exc)})

    return ("My model provider hiccupped just now (it happens occasionally). "
            "Please try again in a moment.")


class EmptyRunError(RuntimeError):
    """The run 'completed' but produced nothing — no content, no salvageable text.

    GLM streams sometimes die mid-turn yet report COMPLETED status; without this the
    user would get silence. Raising makes a dead run retryable like an ERROR run."""


def _attempt(user: User, text: str, *, tools, session_id, model, session, sid) -> str:
    """One model attempt. Raises on an ERROR-status run OR a completely dead
    (empty, unrecoverable) run so the caller can retry / fall back."""
    agent = build_agent(user, tools=tools, session_id=session_id, model=model,
                        session=session, query=text)
    result = _run_bounded(agent, text, user.agno_user_id, sid)
    status = getattr(getattr(result, "status", None), "value", getattr(result, "status", None))
    if status and str(status).upper() == "ERROR":
        raise RuntimeError("provider returned an ERROR-status run")
    _record_tokens(session, user, result)
    content = getattr(result, "content", None)
    if isinstance(content, str) and content.strip():
        return content
    recovered = _recover_reply(result)
    if recovered:
        logger.info("agent.empty_recovered", extra={"ctx_user_id": user.id})
        return recovered
    raise EmptyRunError("run completed with no content and nothing to recover")


def _run_bounded(agent: Agent, text: str, user_id: str, session_id: str) -> Any:
    """Run the agent with a hard timeout so a stuck tool/model never hangs forever."""
    from backend.config import settings

    timeout = settings.agent_run_timeout_seconds
    if not timeout or timeout <= 0:
        return agent.run(text, user_id=user_id, session_id=session_id)
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(agent.run, text, user_id=user_id, session_id=session_id)
    try:
        return future.result(timeout=timeout)
    finally:
        # Don't block on an abandoned (timed-out) run — let it wind down on its own.
        executor.shutdown(wait=False)
