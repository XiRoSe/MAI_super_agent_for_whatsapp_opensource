"""End-to-end CLI turn with the model mocked.

Proves the harness plumbing: provision a tenant, route a message through the
same turn function the REPL uses, and get a reply — all without a network model.
The model boundary (`respond`) is mocked; real memory-across-sessions is a manual
acceptance step that needs a live model key (see README).
"""

from __future__ import annotations

import backend.cli as cli
from backend.provisioning import get_or_create_user


def test_full_cli_turn_with_mocked_model(session, monkeypatch):
    captured = {}

    def fake_respond(user, text, **kwargs):
        captured["user_id"] = user.id
        captured["text"] = text
        return f"echo: {text}"

    monkeypatch.setattr(cli, "respond", fake_respond)

    user, _ = get_or_create_user(session, whatsapp_number="+15550009999", display_name="You")
    reply = cli.generate_cli_reply(session, user, "hello mai")

    assert reply == "echo: hello mai"
    assert captured["user_id"] == user.id
    assert captured["text"] == "hello mai"


def test_two_cli_users_are_isolated(session, monkeypatch):
    """The CLI's two tenants never share the user object passed to the model."""
    seen = []
    monkeypatch.setattr(cli, "respond", lambda user, text, **kw: seen.append(user.agno_user_id) or "ok")

    u1, _ = get_or_create_user(session, whatsapp_number="+15550000111")
    u2, _ = get_or_create_user(session, whatsapp_number="+15550000222")
    cli.generate_cli_reply(session, u1, "hi")
    cli.generate_cli_reply(session, u2, "hi")

    assert seen[0] != seen[1]
    assert u1.account_id != u2.account_id  # separate accounts in MVP
