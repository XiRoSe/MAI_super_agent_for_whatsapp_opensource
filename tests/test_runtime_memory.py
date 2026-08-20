from backend.agent import runtime_memory as rm


def test_note_and_recent_roundtrip():
    rm.clear(1)
    rm.note(1, "user", "hi")
    rm.note(1, "assistant", "hello")
    assert rm.recent_text(1) == "user: hi\nassistant: hello"


def test_skips_empty_and_caps(monkeypatch):
    rm.clear(2)
    rm.note(2, "user", "")  # skipped
    for i in range(60):
        rm.note(2, "user", f"m{i}")
    out = rm.recent_text(2, limit=5).splitlines()
    assert len(out) == 5 and out[-1] == "user: m59"


def test_isolated_per_user():
    rm.clear(3)
    rm.clear(4)
    rm.note(3, "user", "a")
    assert rm.recent_text(4) == ""
