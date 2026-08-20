"""Skill tool — lets Mai create/edit the user's reusable Skills (playbooks) from chat.

A SKILL is a repeatable procedure ("how to behave when X happens"), distinct from a
MEMORY (a fact about the user). The instructions teach the agent when to use which.
"""

from __future__ import annotations

from agno.tools import Toolkit

from backend.db.scoped import TenantScope
from backend.db.tenancy import Skill, User
from backend.logging_config import get_logger

logger = get_logger(__name__)


class SkillTools(Toolkit):
    def __init__(self, *, user: User, session) -> None:
        self.scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
        super().__init__(
            name="skills",
            tools=[self.create_skill, self.list_skills, self.update_skill],
            instructions=(
                "SKILLS are reusable PLAYBOOKS — instructions for HOW to behave in a recurring "
                "situation (a procedure). They are DIFFERENT from MEMORY, which stores FACTS about "
                "the user. Decide by MEANING (any language or phrasing — never by keywords):\n"
                "- If the user is teaching you a repeatable WAY to act, or how to behave when some "
                "situation comes up, that's a procedure → create_skill. (e.g. 'in the Sofa group, "
                "summarize only the plans' is a skill.)\n"
                "- If the user is just telling you something that is TRUE — a fact or preference, "
                "with no procedure — that's memory; use the memory tool's remember instead. (e.g. "
                "'the Sofa group is my close friends' is a memory.)\n"
                "A skill has: name (short kebab-case id), description (one line — when it applies), "
                "content (the instructions to follow). If the user explicitly asks for a 'skill', "
                "make a skill. When you genuinely can't tell which they want, ask."
            ),
            add_instructions=True,
        )

    def create_skill(self, name: str, description: str, content: str) -> str:
        """Create a reusable Skill (a playbook Mai follows when the trigger matches).

        Args:
            name: a short kebab-case id, e.g. 'sofa-group-summaries'.
            description: the ONE-line 'when to use' trigger (when this skill applies).
            content: the instructions/steps Mai should follow when it applies.
        """
        from backend.skills_store import create_skill
        try:
            skill = create_skill(self.scope, name=name, description=description, content=content)
        except ValueError as exc:
            return str(exc)
        except Exception as exc:
            logger.warning("skill.create_failed", extra={"ctx_error": str(exc)})
            return "I couldn't save that skill just now."
        logger.info("skill.created", extra={"ctx_name": skill.name})
        return f"Saved the skill '{skill.name}'. I'll use it when: {skill.description}"

    def list_skills(self) -> str:
        """List the user's existing Skills (name + when-to-use)."""
        from backend.skills_store import list_skills
        skills = list_skills(self.scope)
        if not skills:
            return "No skills yet."
        return "Skills:\n" + "\n".join(
            f"- {s.name}: {s.description}" + ("" if s.enabled else " [disabled]") for s in skills
        )

    def update_skill(self, name: str, description: str | None = None,
                     content: str | None = None, enabled: bool | None = None) -> str:
        """Edit an existing Skill by name (change its trigger, instructions, or enable/disable it)."""
        from backend.skills_store import update_skill
        match = next((s for s in self.scope.all(Skill) if s.name == name), None)
        if match is None:
            return f"No skill named '{name}'. Use list_skills to see them."
        update_skill(self.scope, match.id, description=description, content=content, enabled=enabled)
        return f"Updated the skill '{name}'."
