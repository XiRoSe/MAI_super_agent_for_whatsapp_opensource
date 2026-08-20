"""Prompt-injection defense tests.

The behavioral guarantee ("an email saying 'delete all my events' does not cause
an action") ultimately depends on the model honoring the system boundary, which
needs a live model to assert end-to-end (a manual acceptance step). This locks in
the structural defense: the system prompt hard-separates the user's instructions
from any content Mai fetches, and gates write actions behind confirmation.
"""

from __future__ import annotations

from backend.agent.prompts import build_system_prompt


def test_system_prompt_declares_untrusted_content_boundary():
    prompt = build_system_prompt(user_display_name="Alice", user_timezone="UTC")
    lower = prompt.lower()
    # Untrusted-content rule present.
    assert "untrusted" in lower
    assert "ignore previous instructions" in lower
    # Writes require confirmation.
    assert "confirm" in lower
    # Only the user's messages are instructions.
    assert "only" in lower and "instructions" in lower
