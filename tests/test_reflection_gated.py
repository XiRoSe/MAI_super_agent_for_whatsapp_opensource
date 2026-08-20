"""Gate belief reflection on account.auto_memory (Task 4 — Privacy Phase 1)."""
import backend.agent.beliefs as b


def test_record_turn_noops_when_memory_off(monkeypatch):
    """No thread is spawned when auto_memory is False."""
    called = {"reflect": False}
    monkeypatch.setattr(
        b.threading,
        "Thread",
        lambda *a, **k: type("T", (), {"start": lambda s: called.__setitem__("reflect", True)})(),
    )

    class Account:
        auto_memory = False

    class User:
        id = 1
        account_id = 1
        account = Account()

    b.record_turn(None, User())
    assert called["reflect"] is False


def test_record_turn_spawns_thread_when_memory_on(monkeypatch):
    """A reflection thread IS spawned at the Nth turn when auto_memory is True."""
    called = {"reflect": False}
    monkeypatch.setattr(
        b.threading,
        "Thread",
        lambda *a, **k: type("T", (), {"start": lambda s: called.__setitem__("reflect", True)})(),
    )
    monkeypatch.setattr(b.settings, "belief_system_enabled", True)
    monkeypatch.setattr(b.settings, "belief_reflect_every", 1)  # reflect every turn

    class FakeRow:
        msg_count = 0

    fake_row = FakeRow()
    monkeypatch.setattr(b, "_get_or_create", lambda session, user: fake_row)

    class FakeSession:
        def commit(self):
            pass

    class Account:
        auto_memory = True

    class User:
        id = 1
        account_id = 1
        account = Account()

    b.record_turn(FakeSession(), User())
    assert called["reflect"] is True
