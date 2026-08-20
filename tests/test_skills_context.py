"""Skills auto-recall: enabled skills are rendered into the prompt context."""

from __future__ import annotations

from backend.db.scoped import TenantScope
from backend.skills_store import create_skill, skills_context


def test_skills_context_auto_injects(session, two_tenants):
    _, u1, *_ = two_tenants
    scope = TenantScope(session, account_id=u1.account_id, user_id=u1.id)
    create_skill(scope, name="duck-test", description="asked to add a duck to a photo",
                 content="Step 1: get the photo. Step 2: edit it.")
    ctx = skills_context(session, u1)
    assert "YOUR SKILLS" in ctx
    assert "duck-test" in ctx
    assert "asked to add a duck to a photo" in ctx
    assert "Step 1: get the photo" in ctx


def test_skills_context_semantic_selects_subset(session, two_tenants, monkeypatch):
    """With a query, only the skill(s) ``rank`` selects are rendered."""
    from backend.agent import semantic
    from backend.config import settings

    _, u1, *_ = two_tenants
    scope = TenantScope(session, account_id=u1.account_id, user_id=u1.id)
    create_skill(scope, name="duck-test", description="asked to add a duck to a photo",
                 content="DUCK STEPS HERE")
    create_skill(scope, name="tax-test", description="asked to file taxes",
                 content="TAX STEPS HERE")

    # rank is faked, so no embedder is needed — force the recall flag so the test is
    # hermetic (doesn't depend on SEMANTIC_RECALL_ENABLED / TOGETHER_API_KEY in the env).
    monkeypatch.setattr(settings, "semantic_recall_enabled", True)
    # rank picks only the duck skill (simulating a photo-editing message).
    monkeypatch.setattr(semantic, "rank",
                        lambda q, items, key, **kw: [s for s in items if s.name == "duck-test"])
    ctx = skills_context(session, u1, query="please add a duck to my picture")
    assert "duck-test" in ctx and "DUCK STEPS HERE" in ctx
    assert "tax-test" not in ctx and "TAX STEPS HERE" not in ctx


def test_skills_context_truncates_at_skill_boundaries(session, two_tenants):
    """Over budget, WHOLE skills are dropped (rank-order tail first) — never sliced
    mid-playbook: a half-injected skill misleads the model; an absent one is just absent."""
    _, u1, *_ = two_tenants
    scope = TenantScope(session, account_id=u1.account_id, user_id=u1.id)
    create_skill(scope, name="a-first", description="first trigger",
                 content="FIRST BODY " + "x" * 200 + " FIRST END")
    create_skill(scope, name="b-second", description="second trigger",
                 content="SECOND BODY " + "y" * 200)

    ctx = skills_context(session, u1, max_chars=350)  # fits the first block only
    assert "a-first" in ctx and "FIRST END" in ctx    # kept WHOLE, not sliced
    assert "b-second" not in ctx and "SECOND BODY" not in ctx  # dropped WHOLE


def test_skills_context_falls_back_to_all_when_rank_none(session, two_tenants, monkeypatch):
    """rank -> None (embedder unavailable) renders ALL enabled skills."""
    from backend.agent import semantic

    _, u1, *_ = two_tenants
    scope = TenantScope(session, account_id=u1.account_id, user_id=u1.id)
    create_skill(scope, name="duck-test", description="add a duck", content="DUCK STEPS")
    create_skill(scope, name="tax-test", description="file taxes", content="TAX STEPS")

    monkeypatch.setattr(semantic, "rank", lambda *a, **k: None)
    ctx = skills_context(session, u1, query="anything at all")
    assert "duck-test" in ctx and "tax-test" in ctx  # fallback = inject all
