"""Agno Agent Skills loader.

Loads SKILL.md packages from the configured `skills/` directory via Agno's
native `Skills` + `LocalSkills`. Progressive disclosure is Agno's job: a skill's
full body stays out of context until the model decides a task matches it, so the
starter set can grow without bloating every prompt.

Adding a skill = drop a folder containing SKILL.md into `skills/`. No code change.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.logging_config import get_logger

logger = get_logger(__name__)


def _skills_path() -> Path:
    p = Path(settings.skills_dir)
    if p.is_absolute():
        return p
    # Try, in order: the current working directory (e.g. /app in the container),
    # then the repo root relative to this file (editable/dev install).
    candidates = [
        Path.cwd() / settings.skills_dir,
        Path(__file__).resolve().parents[1] / settings.skills_dir,
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def _skills_from_path(path: str) -> Any | None:
    try:
        from agno.skills import LocalSkills, Skills

        return Skills(loaders=[LocalSkills(path=path)])
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("skills.load_failed", extra={"ctx_error": str(exc)})
        return None


@lru_cache(maxsize=1)
def get_skills() -> Any | None:
    """Global bundled skills (fallback when there's no per-user context)."""
    path = _skills_path()
    if not path.exists():
        logger.info("skills.none", extra={"ctx_path": str(path)})
        return None
    skills = _skills_from_path(str(path))
    if skills is not None:
        logger.info("skills.loaded", extra={"ctx_path": str(path)})
    return skills


def get_skills_for_user(session, user) -> Any | None:
    """Per-user skills: seed defaults on first use, materialize ENABLED ones, load.

    Falls back to the global bundled skills if anything goes wrong.
    """
    try:
        from backend.skills_store import materialize_for_user, seed_defaults_if_empty

        seed_defaults_if_empty(session, user)
        path = materialize_for_user(session, user)
        return _skills_from_path(path) if path else None
    except Exception as exc:
        logger.warning("skills.user_load_failed", extra={"ctx_error": str(exc)})
        return get_skills()
