"""code_exec: fetch/compute scripts run; sending is OUTSIDE code_exec; runner wiring."""

from __future__ import annotations

from backend.code_exec.runner import FakeRunner
from backend.tools.code_exec_tools import CodeExecTools, _has_outbound


def _user(two_tenants):
    return two_tenants[1]


def test_no_outbound_in_code_exec():
    # Sending happens via direct tools, not in a script — nothing is "outbound".
    assert not _has_outbound("mai.send_message('a','b')")
    assert not _has_outbound("print(mai.read_chat('Sofa'))")


def test_fake_runner_runs_code_and_captures_stdout():
    calls = []
    runner = FakeRunner(call_fn=lambda fn, **a: calls.append((fn, a)) or [{"name": "Sofa"}])
    res = runner.run("print(mai.find_chats(query='s'))", token="t", base_url="x", timeout=5)
    assert res.ok and "Sofa" in res.output and calls == [("find_chats", {"query": "s"})]


def test_fake_runner_reports_exceptions():
    res = FakeRunner(call_fn=lambda *a, **k: None).run("raise ValueError('boom')",
                                                       token="t", base_url="x", timeout=5)
    assert not res.ok and "ValueError" in res.output and "boom" in res.output


def test_run_python_read_script_runs(session, two_tenants):
    runner = FakeRunner(call_fn=lambda fn, **a: [{"name": "Sofa", "id": "1@g.us"}])
    tools = CodeExecTools(user=_user(two_tenants), device_id="dev1", runner=runner,
                          base_url="https://app")
    out = tools.code_exec("print(mai.find_chats())")
    assert "Sofa" in out


def test_code_exec_runs_without_confirmation(session, two_tenants):
    # No outbound in code_exec -> scripts run straight away (no confirmation gate).
    runner = FakeRunner(call_fn=lambda *a, **k: [{"name": "Sofa"}])
    tools = CodeExecTools(user=_user(two_tenants), device_id="dev1", runner=runner,
                          base_url="https://app")
    out = tools.code_exec("print(mai.find_chats())")
    assert "CONFIRMATION NEEDED" not in out and "Script output:" in out
