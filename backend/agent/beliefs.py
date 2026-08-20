"""Mai's self-evolving 'belief system' — a small understanding doc per user.

It captures an evolving, per-user UNDERSTANDING of the user: how they communicate,
their context and preferences — so Mai can attune itself to them. It is NOT Mai's
own personality / principles / opinions, and it never constrains what Mai will do;
it only helps Mai fit this particular person. It's injected into the system prompt,
and every N messages a background reflection decides whether the understanding
genuinely shifted. Most of the time it changes nothing; occasionally it grows. Kept
short and bounded on purpose.
"""

from __future__ import annotations

import threading

from backend.config import settings
from backend.db.scoped import TenantScope
from backend.db.tenancy import AgentBelief, User
from backend.logging_config import get_logger

logger = get_logger(__name__)

_MAX_LEN = 1000

DEFAULT_BELIEFS = (
    "Still getting to know this user — I'll learn his style, what matters to him, and how he "
    "likes to work as we talk, and grow more attuned to him over time."
)

_REFLECT_SYSTEM = (
    "You maintain an AI assistant's living UNDERSTANDING of ONE specific user — so it knows "
    "him well, grows more attuned to him, and feels like a natural fit for him. Every few "
    "messages you get the current understanding plus a recent slice of conversation; refine "
    "it: ADD what you newly learned about him, UPDATE what changed, REMOVE what no longer "
    "fits. Capture who he is and how to serve him best — how he communicates and likes to be "
    "helped, his tone and humor, what he cares about and prioritizes, his context / work / "
    "projects, his preferences — and how the assistant should ATTUNE ITS OWN STYLE to match "
    "his, so talking to it feels natural and familiar to him. Keep it a short, warm, coherent "
    "picture (one-off facts live in memory). The more the two converse, the better it should "
    "know and mirror him. If nothing meaningful was learned this round, reply EXACTLY "
    "'NO CHANGE'. Otherwise output the FULL updated understanding as concise bullets (max 8)."
)


def _row(session, user: User) -> AgentBelief | None:
    scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
    rows = scope.all(AgentBelief)
    return rows[0] if rows else None


def get_beliefs(session, user: User) -> str:
    """The current perspective text (default if none yet)."""
    row = _row(session, user)
    return (row.content if row and row.content else DEFAULT_BELIEFS)


def belief_state(session, user: User) -> dict:
    """For the dashboard: current perspective + when it last changed + turn count."""
    row = _row(session, user)
    return {
        "content": get_beliefs(session, user),
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        "msg_count": row.msg_count if row else 0,
    }


def set_beliefs(session, user: User, content: str) -> dict:
    row = _get_or_create(session, user)
    row.content = (content or "").strip()[:_MAX_LEN] or DEFAULT_BELIEFS
    session.commit()
    return belief_state(session, user)


def reset_beliefs(session, user: User) -> dict:
    row = _get_or_create(session, user)
    row.content = DEFAULT_BELIEFS
    row.msg_count = 0
    session.commit()
    return belief_state(session, user)


def _get_or_create(session, user: User) -> AgentBelief:
    row = _row(session, user)
    if row is None:
        scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
        row = AgentBelief(content=DEFAULT_BELIEFS, msg_count=0)
        scope.add(row)
        scope.commit()
    return row


def record_turn(session, user: User) -> None:
    """Count one of the user's turns; every N, reflect (in the background).

    No-ops unless the user's account has ``auto_memory`` enabled — this keeps
    reflection opt-in and avoids any DB reads/writes for opted-out accounts.
    """
    if not getattr(getattr(user, "account", None), "auto_memory", False):
        return
    if not settings.belief_system_enabled:
        return
    row = _get_or_create(session, user)
    row.msg_count += 1
    session.commit()
    if settings.belief_reflect_every > 0 and row.msg_count % settings.belief_reflect_every == 0:
        threading.Thread(target=_reflect_bg, args=(user.id,), daemon=True).start()


def _recent_text(session, user: User, limit: int = 12) -> str:  # noqa: ARG001
    """Return recent turns from the in-process RAM buffer (never reads the DB)."""
    from backend.agent import runtime_memory

    return runtime_memory.recent_text(user.id, limit)


def _reflect_bg(user_id: int) -> None:
    """Background: re-read recent chat + current beliefs, maybe rewrite the perspective."""
    from backend.db.base import SessionLocal

    session = SessionLocal()
    try:
        user = session.get(User, user_id)
        if user is None:
            return
        # Defensive re-check: the flag may have been turned off since we were scheduled.
        if not getattr(getattr(user, "account", None), "auto_memory", False):
            return
        row = _row(session, user)
        current = row.content if row and row.content else DEFAULT_BELIEFS
        recent = _recent_text(session, user)
        if not recent.strip():
            return
        updated = _reflect(current, recent)
        if updated and row is not None:
            row.content = updated
            session.commit()
            logger.info("beliefs.updated", extra={"ctx_user_id": user_id})
    except Exception as exc:
        logger.warning("beliefs.reflect_failed", extra={"ctx_error": str(exc)})
    finally:
        session.close()


def _reflect(current: str, recent: str) -> str | None:
    """Ask the model whether to evolve the perspective. None => keep as-is."""
    try:
        from agno.agent import Agent

        from backend.models.factory import get_model

        agent = Agent(model=get_model(temperature=0.5), system_message=_REFLECT_SYSTEM,
                      telemetry=False, markdown=False, add_history_to_context=False)
        out = getattr(agent.run(
            f"CURRENT PERSPECTIVE:\n{current}\n\nRECENT CONVERSATION:\n{recent}"
        ), "content", "") or ""
    except Exception as exc:
        logger.warning("beliefs.model_failed", extra={"ctx_error": str(exc)})
        return None
    out = out.strip()
    if not out or "NO CHANGE" in out.upper()[:40] or len(out) < 20:
        return None
    return out[:_MAX_LEN]
