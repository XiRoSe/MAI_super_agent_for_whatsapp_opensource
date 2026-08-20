"""Task 3 – Privacy Phase 1: stop persisting plaintext messages.

Verifies that `remember_turn` only writes to the RAM buffer when
`account.auto_memory` is True, and that `get_history` returns an empty page.
"""
from backend.agent import runtime_memory as rm
from backend.agent.seam import remember_turn  # new helper


class _Acct:
    pass


class _User:
    def __init__(self, uid, auto):
        self.id = uid
        self.account = _Acct()
        self.account.auto_memory = auto


def test_remember_turn_only_when_enabled():
    rm.clear(10)
    rm.clear(11)
    remember_turn(_User(10, False), "user", "secret")  # disabled → nothing
    assert rm.recent_text(10) == ""
    remember_turn(_User(11, True), "user", "hi")  # enabled → RAM only
    assert rm.recent_text(11) == "user: hi"


def test_get_history_returns_empty():
    """get_history no longer queries anything — it always returns an empty page."""
    from backend.chat_history import get_history

    result = get_history(None)  # scope arg is now ignored
    assert result == {"messages": [], "has_more": False, "next_cursor": None}
