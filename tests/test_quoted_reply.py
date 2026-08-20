"""Replies/quotes: the quoted message is extracted and anchored in the agent input."""

from __future__ import annotations

from backend.whatsapp.gowa import GowaWhatsAppProvider
from backend.whatsapp.router import _build_agent_input


def test_agent_input_anchors_quoted():
    out = _build_agent_input("some context", "summarize it", None, quoted="Hello, how are you?")
    assert "replying to this specific message" in out.lower()
    assert "Hello, how are you?" in out
    assert "summarize it" in out


def test_agent_input_bare_when_no_context_no_quote():
    # Unchanged behavior: a bare command when there's nothing to attach.
    assert _build_agent_input("", "do x", None) == "do x"


def test_agent_input_quote_without_context_still_anchors():
    out = _build_agent_input("", "translate", None, quoted="שלום")
    assert "replying to this specific message" in out.lower() and "translate" in out


def test_parse_inbound_extracts_quote():
    p = GowaWhatsAppProvider()
    msgs = p.parse_inbound({"device_id": "dev", "payload": {
        "from": "972500000000@s.whatsapp.net", "body": "@mai summarize", "id": "X",
        "quoted_body": "the quoted text", "replied_to_id": "Q1"}})
    assert msgs and msgs[0].quoted_text == "the quoted text" and msgs[0].quoted_id == "Q1"


def test_parse_inbound_no_quote_is_none():
    p = GowaWhatsAppProvider()
    msgs = p.parse_inbound({"device_id": "dev", "payload": {
        "from": "972500000000@s.whatsapp.net", "body": "@mai hi", "id": "Y"}})
    assert msgs and msgs[0].quoted_text is None and msgs[0].quoted_id is None
