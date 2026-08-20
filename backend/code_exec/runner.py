"""Sandbox runner abstraction. FakeRunner (in-process) is used by tests; the real
E2BRunner lives in e2b_runner.py."""

from __future__ import annotations

import io
import traceback
from contextlib import redirect_stdout
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Protocol


@dataclass
class RunResult:
    ok: bool
    output: str


class SandboxRunner(Protocol):
    def run(self, code: str, token: str, base_url: str, timeout: int) -> RunResult: ...


class FakeRunner:
    """Executes code in-process with a `mai` shim whose functions call `call_fn`.
    For tests only — never used when E2B is configured."""

    def __init__(self, call_fn) -> None:
        self._call_fn = call_fn

    def run(self, code: str, token: str, base_url: str, timeout: int) -> RunResult:
        import sys

        import backend.code_exec.mai_sdk as real
        mai = SimpleNamespace(OUTBOUND=real.OUTBOUND)
        for name in dir(real):
            fn = getattr(real, name)
            if callable(fn) and not name.startswith("_"):
                setattr(mai, name, (lambda n: (lambda *args, **kw: self._call_fn(n, *args, **kw)))(name))
        buf = io.StringIO()
        # The real E2B sandbox uploads mai_sdk.py as mai.py and boots `import mai`
        # (e2b_runner._BOOT), so `import mai` is valid in production. Register the shim
        # in sys.modules so scripts that `import mai` behave the same here as in E2B —
        # the injected global keeps working too.
        prev = sys.modules.get("mai")
        sys.modules["mai"] = mai
        try:
            with redirect_stdout(buf):
                exec(code, {"mai": mai, "__name__": "__sandbox__"})
            return RunResult(ok=True, output=buf.getvalue())
        except Exception:
            return RunResult(ok=False, output=buf.getvalue() + "\n" + traceback.format_exc())
        finally:
            if prev is not None:
                sys.modules["mai"] = prev
            else:
                sys.modules.pop("mai", None)
