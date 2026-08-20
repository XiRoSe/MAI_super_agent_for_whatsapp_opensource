"""Hardened subprocess executor: runs code, captures stdout, times out, scrubs env."""

from __future__ import annotations

import asyncio

from runner.executor import run_code

SDK = "import os\n"  # minimal 'mai.py' stand-in for tests that don't call back


def _run(code, **kw):
    return asyncio.run(
        run_code(code, sdk_source=SDK, token="t", sdk_base_url="http://x", timeout=kw.get("timeout", 10)))


def test_runs_code_and_captures_stdout():
    r = _run("print(sum(range(10)))")
    assert r.ok and r.output.strip() == "45"


def test_exception_is_reported_not_raised():
    r = _run("raise ValueError('boom')")
    assert not r.ok and "ValueError" in r.output and "boom" in r.output


def test_timeout_kills_run():
    r = _run("import time\nwhile True: time.sleep(0.1)", timeout=1)
    assert not r.ok and "tim" in r.output.lower()


def test_env_is_scrubbed_no_secrets():
    # A secret in the parent env must NOT be visible to the child.
    import os
    os.environ["MAI_TEST_SECRET"] = "topsecret"
    try:
        r = _run("import os; print('MAI_TEST_SECRET' in os.environ)")
        assert r.ok and r.output.strip() == "False"
    finally:
        os.environ.pop("MAI_TEST_SECRET", None)


def test_sdk_source_written_as_mai_module():
    r = _run("import mai; print(hasattr(mai, 'x'))")  # SDK stub sets nothing; import must work
    # The SDK stub is just "import os"; importing mai should succeed (module exists).
    assert r.ok and r.output.strip() == "False"
