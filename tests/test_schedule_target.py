"""create_schedule's target-chat resolution — WHERE a schedule fires, confirmed at creation.

The locked spec: a schedule carries the group/chat it delivers into; the user confirms it ONCE,
at creation ("every day at 12:00, in this chat / in Family (group)"), never at fire time. These
lock the resolution contract: default = the chat it was created in; a NAMED target resolves via
the shared resolver and persists jid + human label; ambiguity/unknown REFUSES (persists nothing).
"""

from __future__ import annotations

from backend.db.scoped import TenantScope
from backend.scheduling.models import Schedule
from backend.scheduling.service import ScheduleService
from backend.tools.schedule_tools import ScheduleTools


class FakeMgr:
    def create(self, **kw):
        pass

    def delete(self, name):
        pass


class FakeProvider:
    def list_chats(self, device_id, *, limit=50):
        return [
            {"jid": "123@g.us", "name": "Family", "is_group": True},
            {"jid": "456@g.us", "name": "Family Trip", "is_group": True},
            {"jid": "972501234567@s.whatsapp.net", "name": "Dana", "is_group": False},
        ]


def _tools(session, user, *, default_channel="owner@s.whatsapp.net", device_id="dev1"):
    t = ScheduleTools(user=user, session=session, default_channel=default_channel,
                      device_id=device_id)
    t.provider = FakeProvider() if device_id else None
    # No Agno server in unit tests — registration is best-effort in prod too.
    scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
    t.service = ScheduleService(scope, schedule_manager=FakeMgr())
    return t


def _rows(session, user) -> list[Schedule]:
    scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
    return scope.all(Schedule)


def test_default_target_is_current_chat(session, two_tenants):
    _, u1, _, _ = two_tenants
    out = _tools(session, u1).create_schedule(
        name="brief", kind="static", action_text="hi", schedule_type="recurring", cron="0 8 * * *")
    assert "Done" in out and "in this chat" in out
    (row,) = _rows(session, u1)
    assert row.target_chat == "owner@s.whatsapp.net"
    assert row.target_chat_name is None  # no named target confirmed


def test_named_group_target_persists_jid_and_label(session, two_tenants):
    _, u1, _, _ = two_tenants
    out = _tools(session, u1).create_schedule(
        name="family brief", kind="static", action_text="hi", schedule_type="recurring",
        cron="0 12 * * *", target_chat="Family")
    # The confirmation names the destination — the user can catch a wrong target NOW.
    assert "Family (group)" in out
    (row,) = _rows(session, u1)
    assert row.target_chat == "123@g.us"           # graded on persisted state
    assert row.target_chat_name == "Family (group)"


def test_ambiguous_target_refuses_and_persists_nothing(session, two_tenants):
    _, u1, _, _ = two_tenants
    out = _tools(session, u1).create_schedule(
        name="x", kind="static", action_text="hi", schedule_type="recurring",
        cron="0 8 * * *", target_chat="Fam")  # matches Family AND Family Trip
    assert "more than one" in out
    assert _rows(session, u1) == []  # a refused resolution creates NO schedule


def test_unknown_target_refuses_and_persists_nothing(session, two_tenants):
    _, u1, _, _ = two_tenants
    out = _tools(session, u1).create_schedule(
        name="x", kind="static", action_text="hi", schedule_type="recurring",
        cron="0 8 * * *", target_chat="Zebra Club")
    assert "couldn't pin down" in out.lower()
    assert _rows(session, u1) == []


def test_target_by_number_resolves_dm(session, two_tenants):
    _, u1, _, _ = two_tenants
    out = _tools(session, u1).create_schedule(
        name="dana ping", kind="static", action_text="hi", schedule_type="recurring",
        cron="0 9 * * *", target_chat="0501234567")  # national form of Dana's number
    assert "Dana" in out
    (row,) = _rows(session, u1)
    assert row.target_chat == "972501234567@s.whatsapp.net"


def test_list_shows_where_it_fires(session, two_tenants):
    _, u1, _, _ = two_tenants
    t = _tools(session, u1)
    t.create_schedule(name="family brief", kind="static", action_text="hi",
                      schedule_type="recurring", cron="0 12 * * *", target_chat="Family")
    assert "→ Family (group)" in t.list_schedules()


def test_list_resolves_legacy_rows_live_and_backfills_once(session, two_tenants):
    """Rows created BEFORE labels existed carry a jid but no target_chat_name — the FIRST list
    resolves their name live from the provider (one batched call) and WRITES IT BACK, so the
    lookup never repeats: the second list shows the name even with the provider dead."""
    _, u1, _, _ = two_tenants
    t = _tools(session, u1)
    row = t.service.create(  # simulate a pre-change row: jid persisted, no label
        _spec("legacy brief"), target_chat="123@g.us")
    out = t.list_schedules()
    assert "→ Family (group)" in out
    assert "123@g.us" not in out       # never show the raw jid when a name is known
    assert row.target_chat_name == "Family (group)"  # backfilled onto the row

    class DeadProvider:                 # the lookup must not be needed again
        def list_chats(self, *a, **k):
            raise AssertionError("legacy label already backfilled — no live lookup expected")

    t.provider = DeadProvider()
    assert "→ Family (group)" in t.list_schedules()


def test_list_labels_own_dm(session, two_tenants):
    """A schedule firing into the user's own chat says 'your DM', not their jid."""
    _, u1, _, _ = two_tenants
    own_jid = "15550000001@s.whatsapp.net"  # u1's own number as a chat jid
    t = _tools(session, u1, default_channel=own_jid)
    t.create_schedule(name="me brief", kind="static", action_text="hi",
                      schedule_type="recurring", cron="0 8 * * *")
    out = t.list_schedules()
    assert "→ your DM" in out


def _spec(name: str):
    from backend.scheduling.spec import ScheduleSpec

    return ScheduleSpec(name=name, kind="static", action_text="hi",
                        schedule_type="recurring", timezone="Asia/Jerusalem", cron="0 8 * * *")
