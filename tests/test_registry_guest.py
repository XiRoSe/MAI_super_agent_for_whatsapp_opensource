"""The guest toolset boundary — registry.build_tools(is_owner=False).

`is_owner=False` (an authorized non-owner in a granted chat) is a SECURITY boundary: the guest
runs as the owner's assistant but must never touch the owner's stored state — memory, skills,
schedules, credentials, cross-chat reach, profile lookups, or the grant tools. These pin the
boundary in code (it used to be documentation-only: every private toolkit leaked to guests).
"""

from __future__ import annotations

from backend.tools.registry import build_tools

PRIVATE = {"MemoryTools", "SkillTools", "ScheduleTools", "CredentialTools",
           "WhatsAppTools", "ProfileTools", "AccessTools", "CodeExecTools"}


def _names(tools) -> set[str]:
    return {type(t).__name__ for t in tools}


def test_guest_gets_no_private_toolkits(session, two_tenants):
    _, u1, _, _ = two_tenants
    names = _names(build_tools(u1, session, channel="123@g.us", is_owner=False))
    assert names & PRIVATE == set()
    # And the boundary isn't "no tools at all" — stateless helpers stay available.
    assert "WebSearchTools" in names


def test_owner_keeps_private_toolkits(session, two_tenants):
    """The same call with is_owner=True gets the private set (those not needing a gateway
    connection) — proving the guest gap above is the is_owner flag, not the environment."""
    _, u1, _, _ = two_tenants
    names = _names(build_tools(u1, session, channel="123@g.us", is_owner=True))
    assert {"MemoryTools", "SkillTools", "ScheduleTools", "CredentialTools"} <= names


def test_guest_keeps_error_report(session, two_tenants):
    """Deliberate exception: error reporting stays for guests — a guest's bad answer is exactly
    the failure we'd otherwise never hear about (see the registry comment)."""
    _, u1, _, _ = two_tenants
    names = _names(build_tools(u1, session, channel="123@g.us", is_owner=False))
    assert "ErrorReportTools" in names
