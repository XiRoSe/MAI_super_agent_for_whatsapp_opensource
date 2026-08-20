"""Chat history contract since Privacy Phase 1: always an empty page.

Plaintext messages are no longer persisted (see backend/chat_history.py);
the dashboard endpoint keeps a stable shape but never returns content.
Real-turn buffering is covered in test_no_plaintext_persist.py.
"""

from __future__ import annotations

from backend.chat_history import get_history
from backend.db.scoped import TenantScope


def _scope(session, user):
    return TenantScope(session, account_id=user.account_id, user_id=user.id)


def test_history_is_empty_for_any_tenant(session, two_tenants):
    _, u1, _, u2 = two_tenants
    empty = {"messages": [], "has_more": False, "next_cursor": None}
    assert get_history(_scope(session, u1)) == empty
    assert get_history(_scope(session, u2)) == empty


def test_pagination_args_accepted_and_still_empty(session, two_tenants):
    _, u1, *_ = two_tenants
    page = get_history(_scope(session, u1), limit=5, before_id=123)
    assert page == {"messages": [], "has_more": False, "next_cursor": None}
