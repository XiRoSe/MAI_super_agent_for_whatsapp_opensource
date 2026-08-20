"""Empty-reply recovery: GLM-5.1 sometimes ends a tool-heavy turn with an empty
final assistant message even though useful work (e.g. a transcription) happened.
`_recover_reply` salvages the real text instead of the dead-end apology.

The fakes below mimic the shape of agno's `RunOutput` (agno/run/agent.py):
`.content`, `.messages` (list of objects with `.role`/`.content`), and `.tools`
(list of `ToolExecution`-like objects with `.result`/`.tool_call_error`).
"""

from __future__ import annotations

from types import SimpleNamespace

import backend.agent.mai_agent as na


def _msg(role, content):
    return SimpleNamespace(role=role, content=content)


def _tool(result, tool_call_error=None):
    return SimpleNamespace(result=result, tool_call_error=tool_call_error,
                           tool_name="run_model")


def _result(content=None, messages=None, tools=None, status="COMPLETED"):
    return SimpleNamespace(
        content=content,
        messages=messages or [],
        tools=tools or [],
        status=SimpleNamespace(value=status),
        metrics=None,
    )


# --- _recover_reply directly -------------------------------------------------

def test_recover_uses_prior_assistant_text():
    """Empty final content but an earlier assistant message had the real text."""
    r = _result(
        content="",
        messages=[
            _msg("user", "transcribe this"),
            _msg("assistant", "Here is the transcript: hello world"),
            _msg("assistant", ""),  # empty final message GLM emitted
        ],
    )
    assert na._recover_reply(r) == "Here is the transcript: hello world"


def test_recover_falls_back_to_tool_result():
    """No assistant text at all, but a tool produced the answer (transcription)."""
    r = _result(
        content=None,
        messages=[
            _msg("user", "transcribe this"),
            _msg("assistant", None),
        ],
        tools=[_tool("the spoken words were: meet me at noon")],
    )
    assert na._recover_reply(r) == "the spoken words were: meet me at noon"


def test_recover_uses_tool_role_message_when_no_tools_list():
    """Tool output can also live as a role=='tool' message."""
    r = _result(
        content="",
        messages=[
            _msg("user", "transcribe"),
            _msg("tool", "transcribed audio text"),
            _msg("assistant", "   "),
        ],
    )
    assert na._recover_reply(r) == "transcribed audio text"


def test_recover_skips_errored_tool():
    r = _result(
        content="",
        messages=[_msg("assistant", "")],
        tools=[_tool("boom", tool_call_error=True)],
    )
    assert na._recover_reply(r) is None


def test_recover_ignores_bare_media_url():
    """A lone media URL is delivered separately, not a user-facing reply."""
    r = _result(
        content="",
        messages=[_msg("assistant", "")],
        tools=[_tool("https://cdn.example.com/out.mp3")],
    )
    assert na._recover_reply(r) is None


def test_recover_handles_list_content_blocks():
    r = _result(
        content="",
        messages=[_msg("assistant", [{"type": "text", "text": "block text"}])],
    )
    assert na._recover_reply(r) == "block text"


def test_recover_never_resurrects_history_messages():
    """Session history rides along in result.messages (agno flags it
    from_history=True). A dead run must salvage NOTHING from it — returning a
    previous turn's reply made Mai re-send old responses on every dead stream."""
    r = _result(
        content="",
        messages=[
            SimpleNamespace(role="assistant", content="old reply from last turn",
                            from_history=True),
            SimpleNamespace(role="tool", content="old tool output",
                            from_history=True),
            _msg("user", "new question"),
            _msg("assistant", ""),
        ],
    )
    assert na._recover_reply(r) is None


def test_recover_returns_none_when_nothing_usable():
    r = _result(content="", messages=[_msg("assistant", "")], tools=[])
    assert na._recover_reply(r) is None


def test_recover_never_raises_on_garbage():
    assert na._recover_reply(SimpleNamespace()) is None
    assert na._recover_reply(object()) is None


# --- _attempt wiring ---------------------------------------------------------

def test_attempt_returns_recovered_text_when_content_empty(two_tenants, monkeypatch):
    _, u1, *_ = two_tenants
    fake = _result(
        content="",
        messages=[_msg("assistant", "recovered transcript")],
    )
    monkeypatch.setattr(na, "build_agent", lambda *a, **k: object())
    monkeypatch.setattr(na, "_run_bounded", lambda *a, **k: fake)
    out = na._attempt(u1, "hi", tools=None, session_id=None, model=None,
                      session=None, sid="s")
    assert out == "recovered transcript"


def test_attempt_raises_when_nothing_recoverable(two_tenants, monkeypatch):
    """A dead run (no content, nothing to salvage) RAISES so respond()'s retry ladder
    treats it like any provider failure — the user never gets served silence.
    (Old contract returned '(Mai had nothing to say.)' — changed with the ladder.)"""
    import pytest

    _, u1, *_ = two_tenants
    fake = _result(content="", messages=[_msg("assistant", "")])
    monkeypatch.setattr(na, "build_agent", lambda *a, **k: object())
    monkeypatch.setattr(na, "_run_bounded", lambda *a, **k: fake)
    with pytest.raises(na.EmptyRunError):
        na._attempt(u1, "hi", tools=None, session_id=None, model=None,
                    session=None, sid="s")


def test_attempt_returns_content_unchanged_when_present(two_tenants, monkeypatch):
    """Normal turn: content present -> returned as-is, recovery not consulted."""
    _, u1, *_ = two_tenants
    fake = _result(
        content="normal reply",
        # If recovery were (wrongly) invoked it would yield this instead:
        messages=[_msg("assistant", "SHOULD NOT BE USED")],
    )
    monkeypatch.setattr(na, "build_agent", lambda *a, **k: object())
    monkeypatch.setattr(na, "_run_bounded", lambda *a, **k: fake)
    called = {"recover": False}
    orig = na._recover_reply
    monkeypatch.setattr(na, "_recover_reply",
                        lambda r: called.__setitem__("recover", True) or orig(r))
    out = na._attempt(u1, "hi", tools=None, session_id=None, model=None,
                      session=None, sid="s")
    assert out == "normal reply" and called["recover"] is False
