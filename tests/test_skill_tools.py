"""Skill tool: create + list a user's skills (distinct from memory)."""

from __future__ import annotations

from backend.tools.skill_tools import SkillTools


def test_create_and_list_skill(session, two_tenants):
    _, u1, *_ = two_tenants
    st = SkillTools(user=u1, session=session)
    out = st.create_skill("sofa-summaries", "when summarizing the Sofa group", "Focus on plans.")
    assert "Saved" in out and "sofa-summaries" in out
    assert "sofa-summaries" in st.list_skills()


def test_create_duplicate_is_friendly(session, two_tenants):
    _, u1, *_ = two_tenants
    st = SkillTools(user=u1, session=session)
    st.create_skill("dup-skill", "x", "y")
    again = st.create_skill("dup-skill", "x", "y")
    assert "already exists" in again
