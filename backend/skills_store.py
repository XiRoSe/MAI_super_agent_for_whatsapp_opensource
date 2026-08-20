"""Per-user Agent Skills: seed defaults, CRUD, and materialize for Agno.

Skills live in the DB per user (seeded from our bundled defaults on first use, then
fully editable). At agent-build time, a user's ENABLED skills are written to a
per-user temp directory and handed to Agno's `LocalSkills(path=...)`, preserving
progressive disclosure. The temp dir is cached and only rewritten when the user's
skills change.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from backend.db.scoped import TenantScope
from backend.db.tenancy import Skill, User
from backend.logging_config import get_logger

logger = get_logger(__name__)

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_materialized: dict[str, tuple[str, str]] = {}  # agno_user_id -> (version, path)


# ---- SKILL.md parsing / rendering ----------------------------------------
def parse_skill_md(text: str) -> dict:
    """Return {name, description, content} from a SKILL.md string."""
    m = _FRONTMATTER.match(text.strip())
    name = description = ""
    body = text
    if m:
        fm, body = m.group(1), m.group(2)
        for line in fm.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                k, v = k.strip().lower(), v.strip()
                if k == "name":
                    name = v
                elif k == "description":
                    description = v
    return {"name": name, "description": description, "content": body.strip()}


def render_skill_md(name: str, description: str, content: str) -> str:
    # Keep description on one line (frontmatter); escape stray newlines.
    desc = " ".join(description.splitlines()).strip()
    return f"---\nname: {name}\ndescription: {desc}\n---\n\n{content.strip()}\n"


# ---- bundled defaults -----------------------------------------------------
def _defaults_dir() -> Path:
    from backend.config import settings

    p = Path(settings.skills_dir)
    if not p.is_absolute():
        for cand in (Path.cwd() / settings.skills_dir,
                     Path(__file__).resolve().parents[1] / settings.skills_dir):
            if cand.exists():
                return cand
    return p


def load_default_skills() -> list[dict]:
    out = []
    base = _defaults_dir()
    if not base.exists():
        return out
    for skill_md in sorted(base.glob("*/SKILL.md")):
        parsed = parse_skill_md(skill_md.read_text(encoding="utf-8"))
        if parsed["name"]:
            out.append(parsed)
    return out


# ---- seeding + CRUD (tenant-scoped) --------------------------------------
def seed_defaults_if_empty(session: Session, user: User) -> None:
    scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
    if scope.all(Skill):
        return
    for d in load_default_skills():
        scope.add(Skill(name=d["name"], description=d["description"],
                        content=d["content"], enabled=True, is_default=True))
    scope.commit()
    logger.info("skills.seeded", extra={"ctx_user_id": user.id})


def list_skills(scope: TenantScope) -> list[Skill]:
    return sorted(scope.all(Skill), key=lambda s: (not s.is_default, s.name))


def agno_skills(session: Session, user: User):
    """Agno-native Skills from the user's ENABLED DB skills (skill_mode="stub"): only a STUB
    (name + description) of each is injected into the prompt; the agent fetches the full body
    on demand via get_skill_instructions. The lean, no-embedder alternative to skills_context."""
    from agno.skills import Skills
    from agno.skills.loaders.base import SkillLoader
    from agno.skills.skill import Skill as AgnoSkill

    seed_defaults_if_empty(session, user)
    scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
    rows = [s for s in list_skills(scope) if s.enabled]

    class _DBLoader(SkillLoader):
        def load(self) -> list:
            # source_path="" is fine: get_skill_instructions serves the in-memory `instructions`;
            # only references/scripts (which DB skills don't have) touch the filesystem.
            return [AgnoSkill(name=s.name, description=s.description or "",
                              instructions=s.content or "", source_path="") for s in rows]

    return Skills(loaders=[_DBLoader()])


_SKILLS_HEADER = (
    "YOUR SKILLS (reusable playbooks). When the user's request matches a skill's "
    '"use when" — recognized by MEANING, in any language, never by keywords — FOLLOW '
    "that skill's steps automatically; you don't need to be told to use it."
)


def skills_context(
    session: Session, user: User, *, query: str | None = None, max_chars: int = 12000
) -> str:
    """The enabled skills rendered for the system prompt (auto-recall, like memory).

    Seeds defaults on first use, then lays out ENABLED skills so the relevant one
    is in context — the model applies it by meaning, no progressive-disclosure
    step required. Returns "" when the user has no skills.

    When ``query`` (the current user message) is given and semantic recall is on,
    only the skills whose "use when" trigger is semantically close to the message
    are rendered. If the embedder is unavailable it transparently falls back to
    rendering ALL enabled skills — resilience for a transient embedder hiccup;
    every deliberate regime (prod AND evals/tests) runs with the embedder on.

    ``max_chars`` is sized for the SEMANTIC regime: the worst realistic co-match
    today (both photo skills, ~8.7k chars) plus headroom for growth. Enforced at
    SKILL boundaries, first-fit in rank order (most relevant first): a block that
    doesn't fit is DROPPED with a warning and smaller later ones may still make it
    — never sliced mid-playbook. A half-injected skill misleads the model (it
    follows the visible half); an absent one is merely absent.
    """
    seed_defaults_if_empty(session, user)
    scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
    skills = [s for s in list_skills(scope) if s.enabled]
    if not skills:
        return ""

    from backend.config import settings

    if query and settings.semantic_recall_enabled:
        from backend.agent import semantic

        # Score each skill by its name + "use when" trigger (its activation signal).
        selected = semantic.rank(
            query,
            skills,
            key=lambda s: f"{s.name} {s.description}",
            threshold=settings.skill_recall_threshold,
            top_k=len(skills),
        )
        if selected is not None:  # None => embedder unavailable => fall back to all
            skills = selected
            if not skills:
                return ""

    kept: list[str] = []
    dropped: list[str] = []
    used = 0
    for s in skills:
        block = f"## {s.name}\nUse when: {s.description}\n{s.content}"
        cost = len(block) + (2 if kept else 0)  # the "\n\n" joiner
        if used + cost > max_chars:
            dropped.append(s.name)  # first-fit: skip what doesn't fit, keep trying smaller
            continue
        kept.append(block)
        used += cost
    if dropped:
        logger.warning("skills.context_truncated",
                       extra={"ctx_dropped": dropped, "ctx_kept": len(kept),
                              "ctx_budget": max_chars})
    if not kept:
        return ""
    return f"{_SKILLS_HEADER}\n\n" + "\n\n".join(kept)


def create_skill(scope: TenantScope, *, name: str, description: str, content: str) -> Skill:
    name = _slug(name)
    if any(s.name == name for s in scope.all(Skill)):
        raise ValueError(f"a skill named '{name}' already exists")
    skill = Skill(name=name, description=description, content=content,
                  enabled=True, is_default=False)
    scope.add(skill)
    scope.commit()
    return skill


def update_skill(scope: TenantScope, skill_id: int, **fields) -> Skill | None:
    skill = scope.get(Skill, skill_id)
    if skill is None:
        return None
    if "name" in fields and fields["name"]:
        skill.name = _slug(fields["name"])
    for f in ("description", "content", "enabled"):
        if f in fields and fields[f] is not None:
            setattr(skill, f, fields[f])
    scope.commit()
    return skill


def delete_skill(scope: TenantScope, skill_id: int) -> bool:
    skill = scope.get(Skill, skill_id)
    if skill is None:
        return False
    scope.session.delete(skill)
    scope.commit()
    return True


def reset_defaults(scope: TenantScope) -> None:
    """Re-create any missing default skills (does not touch user-added ones)."""
    existing = {s.name for s in scope.all(Skill)}
    for d in load_default_skills():
        if d["name"] not in existing:
            scope.add(Skill(name=d["name"], description=d["description"],
                            content=d["content"], enabled=True, is_default=True))
    scope.commit()


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "skill"


# ---- materialize for Agno -------------------------------------------------
def materialize_for_user(session: Session, user: User) -> str | None:
    """Write the user's ENABLED skills to a per-user dir; return its path (cached)."""
    scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
    enabled = [s for s in scope.all(Skill) if s.enabled]
    if not enabled:
        return None
    version = hashlib.sha256(
        "|".join(f"{s.name}:{s.updated_at.isoformat()}" for s in sorted(enabled, key=lambda x: x.name)).encode()
    ).hexdigest()[:16]

    cached = _materialized.get(user.agno_user_id)
    if cached and cached[0] == version and Path(cached[1]).exists():
        return cached[1]

    base = Path(tempfile.gettempdir()) / "mai_user_skills" / _slug(user.agno_user_id)
    if base.exists():
        for child in base.glob("*/SKILL.md"):
            child.unlink(missing_ok=True)
    for s in enabled:
        d = base / _slug(s.name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(render_skill_md(s.name, s.description, s.content), encoding="utf-8")
    _materialized[user.agno_user_id] = (version, str(base))
    return str(base)
