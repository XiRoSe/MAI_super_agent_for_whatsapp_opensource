"""Per-user skills: parse/render, seed defaults, CRUD, materialize."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.db.scoped import TenantScope
from backend.db.tenancy import Skill
from backend.skills_store import (
    create_skill,
    delete_skill,
    list_skills,
    materialize_for_user,
    parse_skill_md,
    render_skill_md,
    seed_defaults_if_empty,
    update_skill,
)


def _scope(session, user):
    return TenantScope(session, account_id=user.account_id, user_id=user.id)


def test_parse_render_roundtrip():
    md = "---\nname: my-skill\ndescription: use when X happens\n---\n\nDo the thing."
    p = parse_skill_md(md)
    assert p["name"] == "my-skill"
    assert p["description"] == "use when X happens"
    assert p["content"] == "Do the thing."
    rendered = render_skill_md(p["name"], p["description"], p["content"])
    assert parse_skill_md(rendered)["name"] == "my-skill"


def test_seed_defaults(session, two_tenants):
    _, u1, _, _ = two_tenants
    seed_defaults_if_empty(session, u1)
    skills = list_skills(_scope(session, u1))
    assert len(skills) >= 3  # we ship several defaults
    assert all(s.is_default for s in skills)
    names = {s.name for s in skills}
    assert "scheduling-etiquette" in names
    # Idempotent — second call doesn't duplicate.
    seed_defaults_if_empty(session, u1)
    assert len(list_skills(_scope(session, u1))) == len(skills)


def test_crud_and_isolation(session, two_tenants):
    _, u1, _, u2 = two_tenants
    s1 = _scope(session, u1)
    sk = create_skill(s1, name="My Custom Skill", description="when asked about X", content="steps")
    assert sk.name == "my-custom-skill" and sk.is_default is False
    with pytest.raises(ValueError):  # dup name
        create_skill(s1, name="my-custom-skill", description="d", content="c")
    update_skill(s1, sk.id, enabled=False, content="new steps")
    got = _scope(session, u1).get(Skill, sk.id)
    assert got.enabled is False and got.content == "new steps"
    # Other tenant can't see or delete it.
    s2 = _scope(session, u2)
    assert s2.get(Skill, sk.id) is None
    assert delete_skill(s2, sk.id) is False
    assert delete_skill(s1, sk.id) is True


def test_materialize_writes_enabled_only(session, two_tenants):
    _, u1, _, _ = two_tenants
    s1 = _scope(session, u1)
    create_skill(s1, name="on-skill", description="d", content="body A")
    off = create_skill(s1, name="off-skill", description="d", content="body B")
    update_skill(s1, off.id, enabled=False)
    path = materialize_for_user(session, u1)
    assert path is not None
    files = list(Path(path).glob("*/SKILL.md"))
    names = {f.parent.name for f in files}
    assert "on-skill" in names and "off-skill" not in names
