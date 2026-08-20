"""Tool registry — assembles the per-turn toolset for an agent run.

Selection is NOT hardcoded: an LLM router (`tool_router.select_categories`) reads
the user's message in any language and returns which optional tool CATEGORIES are
relevant. This is Agno's recommended "many tools" pattern (a per-turn tool factory)
made language-agnostic. It fails open — if routing is off/errors, the full toolset
is returned, so a capability is never silently stranded.

`is_owner=False` (an authorized guest) is a SECURITY boundary, not an optimization:
guests only ever get stateless tools (web search, generation), never the owner's
private tools (chats, schedules, credentials, access, profiles).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.db.tenancy import User
from backend.logging_config import get_logger

logger = get_logger(__name__)


def build_tools(user: User, session: Session, *, channel: str | None = None,
                is_owner: bool = True, device_id: str | None = None,
                invoke_message_id: str | None = None, message: str | None = None,
                turn_state: dict | None = None) -> list[Any]:
    """Return the Agno toolset for this turn, with optional toolkits chosen by the
    LLM router. `message=None` (or routing disabled/failed) => full toolset."""
    from backend.config import settings
    from backend.tools.tool_router import select_categories

    tools: list[Any] = []
    selected: list[str] = []
    cats = select_categories(message) if message is not None else None  # None => all

    def want(category: str) -> bool:
        return cats is None or category in cats

    def add(name: str, fn) -> None:
        try:
            t = fn()
            if t is not None:
                tools.append(t)
                selected.append(name)
        except Exception:
            pass

    # Additive: the model calls DIRECT tools (which GLM-5.1 does reliably) and also gets
    # code_exec as an extra tool for genuine multi-step composition it writes as a script.
    runner = None
    if (settings.code_exec_enabled and is_owner and device_id
            and settings.whatsapp_gateway_url and settings.sdk_base_url):
        from backend.code_exec.e2b_runner import make_runner

        runner = make_runner()
    # CodeAct mode: the data/gen tools live ONLY in code_exec's SDK (removed from direct);
    # tool_choice (set in build_agent) keeps the model from calling them as bare tools.
    codeact = settings.codeact_mode and runner is not None

    # ---- CORE (always on) ----
    from backend.tools.web_search import WebSearchTools

    add("web_search", lambda: WebSearchTools())
    from backend.tools.web_tools import WebReadTools

    add("web_read", lambda: WebReadTools())

    # Memory + skills are the OWNER's stored state — a guest must neither read the owner's
    # saved facts/playbooks nor write into them (is_owner is the security boundary).
    if is_owner:
        from backend.tools.memory_tools import MemoryTools

        add("memory", lambda: MemoryTools(user=user))

        # Skills (reusable playbooks) — distinct from memory (facts); the tool teaches the difference.
        from backend.tools.skill_tools import SkillTools

        add("skills", lambda: SkillTools(user=user, session=session))

    # Error reporting: the user's opt-in "this was wrong" -> a report the team turns into an
    # eval. Available to EVERYONE incl. guests — the whole point is to harvest failures wherever
    # they happen, and a guest in a chat is exactly whose bad answers we'd otherwise never see.
    # A guest's report files under THIS Mai's owner (the tenant the turn already runs as). Always
    # on (not router-gated) so it's there the moment anyone complains.
    from backend.tools.error_tools import ErrorReportTools

    add("error_report", lambda: ErrorReportTools(user=user, session=session,
                                                 current_chat=channel,
                                                 invoke_message_id=invoke_message_id,
                                                 device_id=device_id, turn_state=turn_state))

    # ---- Generation + media ----
    want_gen = want("generation") or want("profile")
    if not codeact and want_gen and settings.replicate_api_token:
        from backend.tools.replicate_tools import ReplicateTools

        add("replicate", lambda: ReplicateTools())
    if not codeact and want_gen and device_id and channel and settings.whatsapp_gateway_url:
        from backend.tools.media_tools import MediaTools

        add("media", lambda: MediaTools(device_id=device_id, current_chat=channel,
                                        invoke_message_id=invoke_message_id,
                                        turn_state=turn_state))

    # ---- Owner-only (touch the owner's private data / account) ----
    # `is_owner` is enforced HERE, not just documented: a guest (an authorized non-owner in a
    # granted chat) never gets the owner's schedules, credentials, cross-chat reach, profile
    # lookups, or the grant tools themselves.
    conn = None
    if settings.whatsapp_gateway_url:
        from backend.db.tenancy import WhatsAppConnection

        conn = session.get(WhatsAppConnection, user.account_id)

    if is_owner and want("scheduling"):
        from backend.tools.schedule_tools import ScheduleTools

        # device_id lets create_schedule resolve a NAMED target chat ("the Family group").
        add("schedule", lambda: ScheduleTools(user=user, session=session, default_channel=channel,
                                              device_id=conn.session_id if conn else None))

    if is_owner and want("integrations"):
        from backend.tools.credential_tools import CredentialTools

        add("credentials", lambda: CredentialTools(user=user, session=session))

    if is_owner and want("docs"):
        from backend.tools.docs_tools import DocsTools

        add("docs", lambda: DocsTools(user=user, session=session))

    if conn and is_owner:
        if not codeact and want("profile"):
            from backend.tools.profile_tools import ProfileTools

            add("profile", lambda: ProfileTools(device_id=conn.session_id,
                                                owner_phone=user.whatsapp_number,
                                                current_chat=channel))
        if not codeact and want("crosschat"):
            from backend.tools.whatsapp_tools import WhatsAppTools

            add("whatsapp", lambda: WhatsAppTools(user=user, session=session,
                                                  device_id=conn.session_id,
                                                  current_chat=channel,
                                                  invoke_message_id=invoke_message_id,
                                                  turn_state=turn_state))
        if want("access"):
            from backend.tools.access_tools import AccessTools

            add("access", lambda: AccessTools(user=user, session=session,
                                              device_id=conn.session_id, current_chat=channel))

    # code_exec: an EXTRA tool for multi-step composition (owner-only, sandbox-gated).
    if runner is not None:
        from backend.tools.code_exec_tools import CodeExecTools

        add("code_exec", lambda: CodeExecTools(user=user, device_id=device_id, runner=runner,
                                               base_url=settings.sdk_base_url,
                                               current_chat=channel))

    logger.info("tools.selected", extra={"ctx_tools": selected,
                                         "ctx_routed": cats is not None})
    return tools
