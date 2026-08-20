"""In-process schedule poller: due detection + firing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import backend.scheduling.poller as poller
from backend.db.scoped import TenantScope
from backend.scheduling.service import ScheduleService
from backend.scheduling.spec import ScheduleSpec


class FakeMgr:
    def create(self, **kw): pass
    def delete(self, name): pass


def _svc(session, user):
    scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
    return ScheduleService(scope, schedule_manager=FakeMgr())


def test_due_schedules(session, two_tenants):
    _, u1, *_ = two_tenants
    svc = _svc(session, u1)
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    one = svc.create(ScheduleSpec("p", "static", "hi", "once", timezone="UTC", run_at=future))
    rec = svc.create(ScheduleSpec("r", "static", "hi", "recurring", timezone="UTC", cron="* * * * *"))

    now = datetime.now(timezone.utc)
    due_now = {s.id for s in poller.due_schedules(session, now)}
    assert rec.id in due_now            # "every minute" cron is due now
    assert one.id not in due_now        # one-off not due until its run_at

    later = {s.id for s in poller.due_schedules(session, future + timedelta(seconds=1))}
    assert one.id in later              # one-off due once run_at has passed


def test_inactive_schedules_not_due(session, two_tenants):
    _, u1, *_ = two_tenants
    svc = _svc(session, u1)
    rec = svc.create(ScheduleSpec("r", "static", "hi", "recurring", timezone="UTC", cron="* * * * *"))
    svc.cancel(rec.id)
    assert rec.id not in {s.id for s in poller.due_schedules(session, datetime.now(timezone.utc))}


def test_poll_once_fires_due(session, two_tenants, monkeypatch):
    _, u1, *_ = two_tenants
    svc = _svc(session, u1)
    rec = svc.create(ScheduleSpec("r", "static", "hi", "recurring", timezone="UTC", cron="* * * * *"))

    class _NoClose:
        def execute(self, *a, **k): return session.execute(*a, **k)
        def get(self, *a, **k): return session.get(*a, **k)
        def close(self): pass

    fired = []
    monkeypatch.setattr(poller, "SessionLocal", lambda: _NoClose())
    monkeypatch.setattr(poller, "fire_schedule_by_id",
                        lambda sess, sid, **kw: fired.append(sid) or True)
    assert poller.poll_once() >= 1
    assert rec.id in fired
