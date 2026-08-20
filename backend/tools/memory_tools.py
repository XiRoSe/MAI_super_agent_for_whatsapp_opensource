"""Memory tools — Mai's long-term memory about the user.

A dedicated, reliable toolkit that writes/reads the SAME agno DB the dashboard's
Memory page reads (`get_agno_db()`), keyed by the user's `agno_user_id`. We use
this instead of Agno's LearningMachine/agentic-memory wiring, which didn't actually
expose memory tools to the agent (the model hallucinated a save that never landed).
"""

from __future__ import annotations

from agno.tools import Toolkit

from backend.db.tenancy import User
from backend.logging_config import get_logger

logger = get_logger(__name__)


class MemoryTools(Toolkit):
    def __init__(self, *, user: User) -> None:
        self.user_id = user.agno_user_id
        super().__init__(
            name="memory_tools",
            tools=[self.remember, self.recall, self.forget],
            instructions=(
                "Long-term memory = durable FACTS about the user (a preference, a detail, an "
                "ongoing project, how they like things). When you learn one OR they ask you to "
                "remember something, call remember(fact) — don't just say you'll remember. Use "
                "recall to look things up; forget to remove.\n"
                "NOT for procedures: if the user is teaching a repeatable WAY to do something, or "
                "how to behave in a situation, that's a SKILL (use the skills tool's create_skill), "
                "not a memory."
            ),
            add_instructions=True,
        )

    def _db(self):
        from backend.db.agno_db import get_agno_db

        return get_agno_db()

    def remember(self, fact: str, topics: str = "") -> str:
        """Save a durable fact/preference about the user to long-term memory.

        Args:
            fact: the thing to remember, e.g. "Prefers morning meetings".
            topics: optional comma-separated tags, e.g. "preferences, work".
        """
        fact = (fact or "").strip()
        if not fact:
            return "Nothing to remember."
        tlist = [t.strip() for t in topics.split(",") if t.strip()] if topics else []
        try:
            from agno.memory import UserMemory

            self._db().upsert_user_memory(
                UserMemory(memory=fact, user_id=self.user_id, topics=tlist)
            )
        except Exception as exc:
            logger.warning("memory.save_failed", extra={"ctx_error": str(exc)})
            return f"Couldn't save that to memory: {exc}"
        logger.info("memory.saved", extra={"ctx_user": self.user_id})
        return f"Saved to memory: {fact}"

    def recall(self, query: str = "") -> str:
        """Recall what you remember about the user (optionally matching a query)."""
        try:
            db = self._db()
            mems = (db.get_user_memories(user_id=self.user_id, search_content=query)
                    if query else db.get_user_memories(user_id=self.user_id))
        except Exception as exc:
            return f"Couldn't recall: {exc}"
        if not mems:
            return "I don't have anything saved about them yet."
        return "What I remember:\n" + "\n".join(
            f"- {getattr(m, 'memory', '')}" for m in mems[:25] if getattr(m, "memory", "")
        )

    def forget(self, fact_snippet: str) -> str:
        """Delete a saved memory that matches the given text snippet."""
        try:
            db = self._db()
            for m in db.get_user_memories(user_id=self.user_id) or []:
                if fact_snippet.lower() in (getattr(m, "memory", "") or "").lower():
                    db.delete_user_memory(memory_id=m.memory_id, user_id=self.user_id)
                    return "Forgotten."
        except Exception as exc:
            return f"Couldn't forget: {exc}"
        return "I couldn't find a matching memory to forget."
