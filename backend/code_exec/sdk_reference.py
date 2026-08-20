"""Build the SDK reference (for the tool description) + the source (for the sandbox)
from the mai_sdk module, so they can never drift from the real functions."""

from __future__ import annotations

import inspect

from backend.code_exec import mai_sdk


def _public_functions():
    for name in sorted(dir(mai_sdk)):
        obj = getattr(mai_sdk, name)
        if callable(obj) and not name.startswith("_") and inspect.isfunction(obj):
            yield name, obj


def build_reference() -> str:
    lines = ["mai.* SDK — these are NOT callable tools; use them ONLY inside "
             "code_exec(code='...mai.FUNC(...)...'):"]
    for name, fn in _public_functions():
        sig = str(inspect.signature(fn))
        doc = (inspect.getdoc(fn) or "").splitlines()
        purpose = doc[0] if doc else ""
        tag = "  [OUTBOUND — needs user approval]" if name in mai_sdk.OUTBOUND else ""
        lines.append(f"  mai.{name}{sig} — {purpose}{tag}")
    return "\n".join(lines)


def sdk_source() -> str:
    return inspect.getsource(mai_sdk)
