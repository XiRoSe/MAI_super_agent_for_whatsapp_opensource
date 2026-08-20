"""WhatsApp self-capability tools: list/read/send across chats & groups."""

from __future__ import annotations

from backend.tools.whatsapp_tools import WhatsAppTools


class FakeProvider:
    def __init__(self):
        self.sent = []

    def list_chats(self, device_id, *, limit=50):
        return [
            {"jid": "123@g.us", "name": "Family", "is_group": True},
            {"jid": "628111", "name": "Bob", "is_group": False},
        ]

    def get_chat_messages(self, device_id, jid, *, limit=20):
        return [
            {"sender": "Bob", "text": "dinner at 8?", "from_me": False},
            {"sender": "Me", "text": "sure", "from_me": True},
        ]

    def send_text(self, to, text, *, session_id=None):
        self.sent.append((to, text, session_id))

    def get_group_participants(self, jid, *, session_id=None):
        return [{"name": "Alice", "number": "972540000012", "admin": True},
                {"name": "Bob", "number": "972540000013", "admin": False}]


def _tools(session, user):
    t = WhatsAppTools(user=user, session=session, device_id="mai_acct_1")
    t.provider = FakeProvider()
    return t


def test_find_chats(session, two_tenants):
    _, u1, _, _ = two_tenants
    out = _tools(session, u1).find_chats()
    assert "Family" in out and "[group]" in out and "Bob" in out


def test_find_chats_filters_by_kind(session, two_tenants):
    _, u1, _, _ = two_tenants
    out = _tools(session, u1).find_chats(kind="group")
    assert "Family" in out and "Bob" not in out  # groups only


def test_read_chat_by_name(session, two_tenants):
    _, u1, _, _ = two_tenants
    out = _tools(session, u1).read_chat("family")
    assert "dinner at 8?" in out
    assert "UNTRUSTED" in out  # untrusted-content labelling


def test_read_chat_surfaces_media_url(session, two_tenants):
    _, u1, _, _ = two_tenants
    t = _tools(session, u1)
    t.provider.get_chat_messages = lambda *a, **k: [
        {"sender": "Bob", "text": "", "from_me": False, "media_type": "audio",
         "media_url": "https://gw/statics/a.ogg"},
        {"sender": "Eve", "text": "see this", "from_me": False, "media_type": "image",
         "media_url": None},  # old/un-downloaded
    ]
    out = t.read_chat("family")
    assert "https://gw/statics/a.ogg" in out and "[audio:" in out
    assert "not retrievable" in out  # image with no usable url


def test_read_unknown_chat_points_to_find(session, two_tenants):
    out = _tools(session, two_tenants[1]).read_chat("nonexistent")
    assert "find_chats" in out  # tell the model to search first


def test_read_chat_by_id(session, two_tenants):
    # An id/jid (from find_chats) is used directly — no name matching needed.
    out = _tools(session, two_tenants[1]).read_chat("123@g.us")
    assert "dinner at 8?" in out


def test_send_to_chat_resolves_jid(session, two_tenants):
    _, u1, _, _ = two_tenants
    t = _tools(session, u1)
    msg = t.send_whatsapp_message("bob", "on my way")
    assert "Sent to Bob" in msg
    assert t.provider.sent[0] == ("628111", "on my way", "mai_acct_1")


def test_get_group_members_full_list(session, two_tenants):
    # Full roster of a group by name/id — not just recent senders.
    out = _tools(session, two_tenants[1]).get_group_members("family")
    assert "Alice (972540000012) [admin]" in out and "Bob (972540000013)" in out


# --- the confirm-before-send gate (cross-chat two-phase) -----------------------------------
def _gate_tools(session, user, invoke_id: str):
    """A per-turn toolkit exactly as the registry builds it in a GROUP chat: current_chat set,
    a fresh invoke id per turn. Rebuilt per turn like production."""
    t = WhatsAppTools(user=user, session=session, device_id="mai_acct_1",
                      current_chat="teamchat@g.us", invoke_message_id=invoke_id, turn_state={})
    t.provider = FakeProvider()
    return t


def test_gate_first_cross_chat_send_previews_not_sends(session, two_tenants):
    _, u1, _, _ = two_tenants
    t = _gate_tools(session, u1, "m1")
    out = t.send_whatsapp_message("bob", "hi")
    assert "NOT sent" in out and "Bob (628111)" in out  # identity echoed, nothing sent
    assert t.provider.sent == []


def test_gate_one_shot_confirmed_same_turn_is_blocked(session, two_tenants):
    # The model can't skip the confirmation by passing confirmed=True on the first call.
    _, u1, _, _ = two_tenants
    t = _gate_tools(session, u1, "m1")
    t.send_whatsapp_message("bob", "hi", confirmed=True)
    assert t.provider.sent == []


def test_gate_confirmed_next_turn_sends_previewed_text(session, two_tenants):
    _, u1, _, _ = two_tenants
    _gate_tools(session, u1, "m1").send_whatsapp_message("bob", "hi")   # turn 1: preview
    t2 = _gate_tools(session, u1, "m2")                                  # turn 2: user said yes
    out = t2.send_whatsapp_message("bob", "hi", confirmed=True)
    assert "Sent to Bob (628111)" in out
    assert t2.provider.sent == [("628111", "hi", "mai_acct_1")]


def test_gate_unconfirmed_recall_does_not_clobber_prior_turn_pending(session, two_tenants):
    """THE eval-caught bug (cross_chat_send_confirms went 0/3): on the confirm turn the model
    often calls send WITHOUT confirmed first. That call must not restamp the already-seen
    preview with this turn's id — else the follow-up confirmed=True looks same-turn and the
    gate becomes unwinnable."""
    _, u1, _, _ = two_tenants
    _gate_tools(session, u1, "m1").lookup_contact("bob")  # turn 1 surfaced the recipient
    t2 = _gate_tools(session, u1, "m2")
    t2.send_whatsapp_message("bob", "hi")                  # forgot confirmed -> preview again...
    assert t2.provider.sent == []
    out = t2.send_whatsapp_message("bob", "hi", confirmed=True)  # ...then retries properly
    assert "Sent to Bob" in out and t2.provider.sent == [("628111", "hi", "mai_acct_1")]


def test_gate_changed_message_resets_the_preview(session, two_tenants):
    # A NEW text the user never saw must re-preview — and a same-turn confirm can't send it.
    _, u1, _, _ = two_tenants
    _gate_tools(session, u1, "m1").send_whatsapp_message("bob", "hi")  # previewed "hi"
    t2 = _gate_tools(session, u1, "m2")
    t2.send_whatsapp_message("bob", "something else entirely")          # unseen text
    t2.send_whatsapp_message("bob", "something else entirely", confirmed=True)
    assert t2.provider.sent == []  # restaged this turn -> blocked until a later turn


def test_gate_same_chat_send_is_ungated(session, two_tenants):
    _, u1, _, _ = two_tenants
    t = _gate_tools(session, u1, "m1")
    out = t.send_whatsapp_message("teamchat@g.us", "hello all")
    assert "Sent" in out and t.provider.sent == [("teamchat@g.us", "hello all", "mai_acct_1")]
