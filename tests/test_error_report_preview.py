"""Deterministic checks for the error-report PREVIEW — the report's reproducible spine.

The preview is built from trusted sources and rendered by `_preview` with NO model
involvement, so it must be predictable: fixed English chrome with the captured
messages + Mai's read kept verbatim, and the transcript capped at the last 10.
(The live-model eval covers WHEN the model reports + the gate; this covers WHAT is shown.)
"""
from __future__ import annotations

from backend.reports import Capture, CapturedMessage, _chat_transcript
from backend.tools.error_tools import _preview


def _cap(transcript):
    return Capture(surface="turn", chat_type="self_dm", contact_number=None, chat_jid="x",
                   command="", transcript=transcript, tool_calls=[], schedule=None,
                   model_id=None, git_rev=None, raw_session_json=None)


def test_preview_renders_fixed_chrome_with_verbatim_messages():
    """_preview is pure: fixed English chrome + verbatim messages/read, deterministic per input."""
    out = _preview(_cap([CapturedMessage("user", "A"), CapturedMessage("assistant", "B"),
                         CapturedMessage("user", "C")]), problem="P", expected="E", notes="")
    assert "Error report" in out                  # 🐞 header
    assert "the last 3" in out                    # "── Messages (the last 3 in this chat) ──"
    assert "[User] A" in out
    assert "[Mai] B" in out                       # Mai (assistant)
    assert "[User] C" in out
    assert "• Problem: P" in out
    assert "• Expected: E" in out
    assert "Reply *yes*" in out                   # approval footer


def test_preview_preserves_hebrew_message_text_verbatim():
    """Hebrew (RTL/unicode) message + read text passes through byte-for-byte — the saved
    snapshot is what the team replays."""
    out = _preview(_cap([CapturedMessage("user", "קבעת לי תזכורת לשעה הלא נכונה"),
                         CapturedMessage("assistant", "מצטער, אתקן את זה")]),
                   problem="התזמון היה שגוי", expected="", notes="")
    assert "[User] קבעת לי תזכורת לשעה הלא נכונה" in out
    assert "[Mai] מצטער, אתקן את זה" in out
    assert "• Problem: התזמון היה שגוי" in out


def test_chat_transcript_keeps_only_the_last_10(monkeypatch):
    """12 messages in the chat -> capture keeps exactly the last 10, verbatim and oldest-first
    (the first two are dropped). Tests the real primary path (the chat provider)."""
    class _FakeProvider:
        def get_chat_messages(self, device_id, chat_jid, limit=None):
            return [{"text": f"msg {i}", "from_me": i % 2 == 0, "ts": i} for i in range(1, 13)]

    monkeypatch.setattr("backend.whatsapp.get_provider", lambda: _FakeProvider())
    texts = [m.text for m in _chat_transcript("dev1", "x@s.whatsapp.net")]
    assert len(texts) == 10
    assert texts[0] == "msg 3"        # msg 1 / msg 2 truncated away
    assert texts[-1] == "msg 12"
    assert "msg 1" not in texts and "msg 2" not in texts
