"""Scheduling engine tests: validation, CRUD, firing, idempotency, isolation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.db.scoped import TenantScope
from backend.scheduling.jobs import fire_schedule_by_id
from backend.scheduling.models import Schedule
from backend.scheduling.service import ScheduleLimitError, ScheduleService
from backend.scheduling.spec import ScheduleSpec, ScheduleValidationError


class FakeMgr:
    """Stand-in for Agno's ScheduleManager (no server needed)."""

    def __init__(self):
        self.created = []
        self.deleted = []

    def create(self, **kw):
        self.created.append(kw)

    def delete(self, name):
        self.deleted.append(name)


def _service(session, user):
    scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
    return ScheduleService(scope, schedule_manager=FakeMgr())


def _future(hours=2):
    return datetime.now(timezone.utc) + timedelta(hours=hours)


# --- spec validation -------------------------------------------------------
def test_spec_requires_timezone():
    spec = ScheduleSpec("x", "static", "hi", "once", timezone="", run_at=_future())
    with pytest.raises(ScheduleValidationError):
        spec.validate()


def test_spec_rejects_past_runat():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    spec = ScheduleSpec("x", "static", "hi", "once", timezone="UTC", run_at=past)
    with pytest.raises(ScheduleValidationError):
        spec.validate()


def test_spec_rejects_invalid_cron():
    spec = ScheduleSpec("x", "static", "hi", "recurring", timezone="UTC", cron="not a cron")
    with pytest.raises(ScheduleValidationError):
        spec.validate()


def test_spec_rejects_sub_minute_frequency():
    # "* * * * *" fires every minute = at the 1-min floor; force abusive via seconds is N/A,
    # so simulate by raising the configured minimum.
    from backend.config import settings

    settings.min_schedule_interval_minutes = 5
    try:
        spec = ScheduleSpec("x", "static", "hi", "recurring", timezone="UTC", cron="* * * * *")
        with pytest.raises(ScheduleValidationError):
            spec.validate()
    finally:
        settings.min_schedule_interval_minutes = 1


def test_spec_accepts_valid_recurring():
    spec = ScheduleSpec("brief", "dynamic", "summarize my day", "recurring",
                        timezone="America/New_York", cron="0 8 * * 1-5")
    spec.validate()
    assert "summarize my day" in spec.human_summary()


# --- service CRUD ----------------------------------------------------------
def test_create_and_list(session, two_tenants):
    _, u1, _, _ = two_tenants
    svc = _service(session, u1)
    spec = ScheduleSpec("brief", "static", "good morning", "once",
                        timezone="UTC", run_at=_future())
    s = svc.create(spec)
    assert s.id and s.active and s.agno_schedule_name == f"mai-sched-{s.id}"
    assert len(svc.list_active()) == 1


def test_per_user_limit_enforced(session, two_tenants):
    from backend.config import settings

    _, u1, _, _ = two_tenants
    svc = _service(session, u1)
    settings.max_active_schedules_per_user = 2
    try:
        for i in range(2):
            svc.create(ScheduleSpec(f"s{i}", "static", "hi", "once",
                                    timezone="UTC", run_at=_future(i + 1)))
        with pytest.raises(ScheduleLimitError):
            svc.create(ScheduleSpec("over", "static", "hi", "once",
                                    timezone="UTC", run_at=_future(9)))
    finally:
        settings.max_active_schedules_per_user = 20


def test_cancel(session, two_tenants):
    _, u1, _, _ = two_tenants
    svc = _service(session, u1)
    s = svc.create(ScheduleSpec("brief", "static", "hi", "once",
                                timezone="UTC", run_at=_future()))
    assert svc.cancel(s.id) is True
    assert svc.list_active() == []
    assert svc.cancel(s.id) is False  # already cancelled


# --- firing ----------------------------------------------------------------
def test_fire_static_one_off_deactivates(session, two_tenants):
    _, u1, _, _ = two_tenants
    svc = _service(session, u1)
    s = svc.create(ScheduleSpec("ping", "static", "ping!", "once",
                                timezone="UTC", run_at=_future()))
    sent = []
    fired = fire_schedule_by_id(
        session, s.id,
        sender=lambda sess, user, text: sent.append((user.id, text)),
    )
    assert fired is True
    assert sent == [(u1.id, "ping!")]
    refreshed = session.get(Schedule, s.id)
    assert refreshed.active is False and refreshed.fire_count == 1


def test_fire_dynamic_runs_agent(session, two_tenants):
    _, u1, _, _ = two_tenants
    svc = _service(session, u1)
    s = svc.create(ScheduleSpec("daily", "dynamic", "summarize my day", "recurring",
                                timezone="UTC", cron="0 8 * * *"))
    sent = []
    fired = fire_schedule_by_id(
        session, s.id,
        sender=lambda sess, user, text: sent.append(text),
        agent_runner=lambda user, instruction, sess: f"[ran:{instruction}]",
    )
    assert fired is True
    assert sent == ["[ran:summarize my day]"]
    # recurring stays active
    assert session.get(Schedule, s.id).active is True


def test_fire_is_idempotent_within_minute(session, two_tenants):
    _, u1, _, _ = two_tenants
    svc = _service(session, u1)
    s = svc.create(ScheduleSpec("daily", "static", "hi", "recurring",
                                timezone="UTC", cron="0 8 * * *"))
    now = datetime.now(timezone.utc)
    calls = []
    def sender(sess, user, text):
        calls.append(text)
    assert fire_schedule_by_id(session, s.id, now=now, sender=sender) is True
    # second poll a few seconds later must not double-fire
    assert fire_schedule_by_id(session, s.id, now=now + timedelta(seconds=10), sender=sender) is False
    assert len(calls) == 1


def test_fire_respects_entitlement(session, two_tenants):
    _, u1, _, _ = two_tenants
    svc = _service(session, u1)
    s = svc.create(ScheduleSpec("daily", "static", "hi", "recurring",
                                timezone="UTC", cron="0 8 * * *"))
    sent = []
    fired = fire_schedule_by_id(
        session, s.id,
        sender=lambda sess, user, text: sent.append(text),
        is_entitled=lambda sess, sched: False,  # unsubscribed -> paused
    )
    assert fired is False and sent == []


def test_schedule_isolation_between_tenants(session, two_tenants):
    _, u1, _, u2 = two_tenants
    svc1 = _service(session, u1)
    s1 = svc1.create(ScheduleSpec("u1", "static", "secret", "once",
                                  timezone="UTC", run_at=_future()))
    # User2's scope can't see or cancel user1's schedule.
    scope2 = TenantScope(session, account_id=u2.account_id, user_id=u2.id)
    svc2 = ScheduleService(scope2, schedule_manager=FakeMgr())
    assert svc2.list_active() == []
    assert svc2.cancel(s1.id) is False
    assert scope2.get(Schedule, s1.id) is None


def test_schedule_fires_to_target_chat(session, two_tenants, monkeypatch):
    """A schedule created in a group fires INTO that group; else DM fallback."""
    import backend.scheduling.jobs as jobs
    from backend.db.tenancy import WhatsAppConnection, WhatsAppConnectionStatus

    _, u1, _, _ = two_tenants
    session.add(WhatsAppConnection(account_id=u1.account_id, session_id="dev1",
                                   status=WhatsAppConnectionStatus.connected))
    session.commit()
    svc = _service(session, u1)
    s = svc.create(ScheduleSpec("g", "static", "morning, team", "recurring",
                                timezone="UTC", cron="0 8 * * *"), target_chat="123@g.us")
    assert s.target_chat == "123@g.us"

    sent = {}

    class FakeProvider:
        def send_text(self, to, text, *, session_id=None):
            sent.update(to=to, text=text, device=session_id)

    monkeypatch.setattr("backend.whatsapp.get_provider", lambda: FakeProvider())
    # No custom sender -> routes via target_chat using the account's device.
    assert jobs.fire_schedule_by_id(session, s.id) is True
    assert sent == {"to": "123@g.us", "text": "morning, team", "device": "dev1"}


# --- multi-message scheduled tasks ----------------------------------------


def test_fire_multi_message_splits_on_delimiter(session, two_tenants):
    """A dynamic schedule whose agent output contains MULTI_MSG_DELIMITER is
    delivered as MULTIPLE separate messages, not a single concatenated one."""
    from backend.scheduling.jobs import MULTI_MSG_DELIMITER

    _, u1, _, _ = two_tenants
    svc = _service(session, u1)
    # The agent will return three messages joined by the delimiter.
    three_part = f"Good morning!{MULTI_MSG_DELIMITER}Here are your tasks:{MULTI_MSG_DELIMITER}Have a great day!"
    s = svc.create(ScheduleSpec("multi", "dynamic", "send morning briefing", "recurring",
                                timezone="UTC", cron="0 8 * * *"))
    sent = []
    fired = fire_schedule_by_id(
        session, s.id,
        sender=lambda sess, user, text: sent.append(text),
        agent_runner=lambda user, instruction, sess: three_part,
    )
    assert fired is True
    assert sent == ["Good morning!", "Here are your tasks:", "Have a great day!"]


def test_fire_multi_message_static_splits_on_delimiter(session, two_tenants):
    """A static schedule can also embed MULTI_MSG_DELIMITER in its action_text to
    send multiple messages without any agent involvement."""
    from backend.scheduling.jobs import MULTI_MSG_DELIMITER

    _, u1, _, _ = two_tenants
    svc = _service(session, u1)
    action = f"First message{MULTI_MSG_DELIMITER}Second message"
    s = svc.create(ScheduleSpec("multi-static", "static", action, "once",
                                timezone="UTC", run_at=_future()))
    sent = []
    fired = fire_schedule_by_id(
        session, s.id,
        sender=lambda sess, user, text: sent.append(text),
    )
    assert fired is True
    assert sent == ["First message", "Second message"]
    # one-off should deactivate after firing
    assert session.get(Schedule, s.id).active is False


def test_fire_multi_message_cap_holds(session, two_tenants):
    """When the agent returns more segments than MULTI_MSG_CAP, only the first
    MULTI_MSG_CAP messages are delivered; excess segments are silently dropped."""
    from backend.scheduling.jobs import MULTI_MSG_CAP, MULTI_MSG_DELIMITER

    _, u1, _, _ = two_tenants
    svc = _service(session, u1)
    # Build 15 segments — well above the cap of 10.
    over_cap = MULTI_MSG_DELIMITER.join(f"msg{i}" for i in range(15))
    s = svc.create(ScheduleSpec("cap-test", "dynamic", "send lots", "recurring",
                                timezone="UTC", cron="0 9 * * *"))
    sent = []
    fired = fire_schedule_by_id(
        session, s.id,
        sender=lambda sess, user, text: sent.append(text),
        agent_runner=lambda user, instruction, sess: over_cap,
    )
    assert fired is True
    assert len(sent) == MULTI_MSG_CAP
    # The first MULTI_MSG_CAP segments are delivered in order.
    assert sent[0] == "msg0"
    assert sent[MULTI_MSG_CAP - 1] == f"msg{MULTI_MSG_CAP - 1}"


def test_fire_single_message_unchanged(session, two_tenants):
    """A message without the delimiter is still sent as a single message —
    existing behaviour is fully preserved."""
    from backend.scheduling.jobs import MULTI_MSG_DELIMITER

    _, u1, _, _ = two_tenants
    svc = _service(session, u1)
    s = svc.create(ScheduleSpec("single", "dynamic", "just one thing", "recurring",
                                timezone="UTC", cron="0 10 * * *"))
    sent = []
    fired = fire_schedule_by_id(
        session, s.id,
        sender=lambda sess, user, text: sent.append(text),
        agent_runner=lambda user, instruction, sess: "Just one message, no delimiter.",
    )
    assert fired is True
    assert sent == ["Just one message, no delimiter."]
    assert MULTI_MSG_DELIMITER not in sent[0]


# --- ordered media delivery -----------------------------------------------


def test_fire_ordered_media_interleave(session, two_tenants):
    """Markers in the agent reply are delivered IN ORDER with text segments."""
    _, u1, _, _ = two_tenants
    svc = _service(session, u1)
    s = svc.create(ScheduleSpec("lesson", "dynamic", "teach Hebrew", "recurring",
                                timezone="UTC", cron="0 8 * * *"))
    reply = (
        "Phrase: boker tov\n"
        "[[audio:https://x/a.mp3]]\n"
        "---MAI-MSG---\n"
        "Now repeat it\n"
        "[[audio:https://x/b.mp3]]"
    )
    sent = []
    fired = fire_schedule_by_id(
        session, s.id,
        sender=lambda sess, user, text: sent.append(text),
        agent_runner=lambda user, instruction, sess: reply,
    )
    assert fired is True
    assert sent == [
        "Phrase: boker tov",
        "[[audio:https://x/a.mp3]]",
        "Now repeat it",
        "[[audio:https://x/b.mp3]]",
    ]


def test_fire_no_markers_single_message(session, two_tenants):
    """A plain reply with no markers and no delimiter is sent as a single message."""
    _, u1, _, _ = two_tenants
    svc = _service(session, u1)
    s = svc.create(ScheduleSpec("plain", "dynamic", "say hi", "recurring",
                                timezone="UTC", cron="0 9 * * *"))
    sent = []
    fired = fire_schedule_by_id(
        session, s.id,
        sender=lambda sess, user, text: sent.append(text),
        agent_runner=lambda user, instruction, sess: "Hello there!",
    )
    assert fired is True
    assert len(sent) == 1
    assert sent[0] == "Hello there!"


def test_parse_stream_image_caption(session, two_tenants):
    """_parse_stream parses [[image:URL|caption]] into a media segment."""
    from backend.scheduling.jobs import _parse_stream

    segments = _parse_stream("[[image:https://x/p.png|hello]]")
    assert len(segments) == 1
    seg = segments[0]
    assert seg["type"] == "media"
    assert seg["kind"] == "image"
    assert seg["url"] == "https://x/p.png"
    assert seg["caption"] == "hello"
