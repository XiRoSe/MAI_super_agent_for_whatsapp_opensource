"""An agno-shaped reliability eval for the CodeAct / mai-SDK layer.

agno's `ReliabilityEval` inspects an agent RunOutput's `tool_calls`. On the
CodeAct path agno only ever sees ONE tool call (`code_exec`); the real actions
are the `mai.*` SDK calls made inside the sandboxed script, captured by the
FakeRunner recorder. `SdkReliabilityEval` evaluates THAT call list, with the same
result shape / `assert_passed()` API as agno so cases read the same way:

    result = SdkReliabilityEval(
        recorded_calls=calls,
        expected_tool_calls=["get_profile_picture", "run_model"],
    ).run(print_results=True)
    result.assert_passed()

For now this checks tool-call presence (binary). Argument-value checks -- the
`expected_tool_call_arguments` equivalent (e.g. the friend's URL handed to the
video model) -- are the next addition.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence


@dataclass
class ReliabilityResult:
    eval_status: str  # "PASSED" | "FAILED"
    expected_tool_calls: list[str] = field(default_factory=list)
    actual_tool_calls: list[str] = field(default_factory=list)
    passed_tool_calls: list[str] = field(default_factory=list)
    missing_tool_calls: list[str] = field(default_factory=list)
    additional_tool_calls: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.eval_status == "PASSED"

    def assert_passed(self) -> None:
        assert self.eval_status == "PASSED", str(self)

    def __str__(self) -> str:
        return (
            f"ReliabilityResult: {self.eval_status}\n"
            f"  expected:   {self.expected_tool_calls}\n"
            f"  actual:     {self.actual_tool_calls}\n"
            f"  passed:     {self.passed_tool_calls}\n"
            f"  missing:    {self.missing_tool_calls}\n"
            f"  additional: {self.additional_tool_calls}"
        )


@dataclass(frozen=True)
class LayerVerdict:
    """One abstraction layer's outcome: which expected calls were present (passed) and
    which weren't (missing). Typed, not a dict — the schema is fixed and read by name."""

    expected: list[str]
    actual: list[str]
    passed: list[str]
    missing: list[str]

    @property
    def ok(self) -> bool:
        return not self.missing


@dataclass(frozen=True)
class LayeredResult:
    """A two-layer reliability verdict: a run PASSES only when it made BOTH the expected
    mai.* SDK calls (the WORK layer) AND the expected agno tool calls (the ACTION layer,
    e.g. the send_video that actually DELIVERS). Grading one layer alone is how a run that
    generates a video but never sends it scores a false PASS."""

    work: LayerVerdict    # mai.* SDK layer
    action: LayerVerdict  # agno tool layer

    @property
    def passed(self) -> bool:
        return self.work.ok and self.action.ok

    @property
    def n_expected(self) -> int:
        return len(self.work.expected) + len(self.action.expected)

    @property
    def n_passed(self) -> int:
        return len(self.work.passed) + len(self.action.passed)

    def __str__(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        return (f"LayeredResult: {status}\n"
                f"  work   expected={self.work.expected} missing={self.work.missing}\n"
                f"  action expected={self.action.expected} missing={self.action.missing}")


def _check_layer(expected: list[str], actual: list[str]) -> LayerVerdict:
    actual_set = set(actual)
    return LayerVerdict(
        expected=expected,
        actual=actual,
        passed=[t for t in expected if t in actual_set],
        missing=[t for t in expected if t not in actual_set],
    )


ArgCheck = Callable[[dict], bool]


class ReliabilityEval:
    """Grade a run on BOTH abstraction layers at once. PASSES iff every expected SDK
    call AND every expected agno tool call is present. `expected_tool_calls` may be
    empty (then only the SDK layer gates) — so existing SDK-only scenarios still work.

    A tool can additionally require an ARGUMENT predicate via `tool_arg_checks`: the
    tool counts as satisfied only if SOME successful call to it passes the predicate.
    That's how 'a schedule was created' becomes 'a *daily-at-8am recurring* schedule
    was created' — testing the assignment, not just that the tool fired."""

    def __init__(
        self,
        *,
        recorded_sdk_calls: Sequence[tuple[str, dict]],
        actual_tool_calls: Sequence[Mapping],   # [{name, args, error}, ...]
        expected_sdk_calls: Iterable[str],
        expected_tool_calls: Iterable[str] = (),
        tool_arg_checks: Mapping[str, ArgCheck] | None = None,
        name: str | None = None,
    ) -> None:
        self.name = name
        self.actual_sdk = [fn for fn, _args in recorded_sdk_calls]
        self.actual_tool_calls = [dict(c) for c in actual_tool_calls]
        self.expected_sdk = list(expected_sdk_calls)
        self.expected_tools = list(expected_tool_calls)
        self.tool_arg_checks = dict(tool_arg_checks or {})

    def _check_tool_layer(self) -> LayerVerdict:
        names = [c.get("name") for c in self.actual_tool_calls]
        passed, missing = [], []
        for t in self.expected_tools:
            candidates = [c for c in self.actual_tool_calls
                          if c.get("name") == t and not c.get("error")]
            check = self.tool_arg_checks.get(t)
            ok = (any(check(c.get("args") or {}) for c in candidates)
                  if check else bool(candidates))
            (passed if ok else missing).append(t)
        return LayerVerdict(expected=self.expected_tools, actual=names,
                            passed=passed, missing=missing)

    def run(self, *, print_results: bool = False) -> LayeredResult:
        result = LayeredResult(work=_check_layer(self.expected_sdk, self.actual_sdk),
                               action=self._check_tool_layer())
        if print_results:
            if self.name:
                print(f"[{self.name}]")
            print(result)
        return result


class SdkReliabilityEval:
    """Did the CodeAct run make the expected mai.* SDK calls? (binary)

    Mirrors agno's ReliabilityEval, but `recorded_calls` (the FakeRunner trace of
    `mai.*` calls) stands in for agno's `agent_response.tool_calls`.
    """

    def __init__(
        self,
        *,
        recorded_calls: Sequence[tuple[str, dict]],
        expected_tool_calls: Iterable[str],
        name: str | None = None,
        allow_additional_tool_calls: bool = True,
    ) -> None:
        self.name = name
        self.recorded_calls = list(recorded_calls)
        self.expected_tool_calls = list(expected_tool_calls)
        self.allow_additional_tool_calls = allow_additional_tool_calls
        self.result: ReliabilityResult | None = None

    def run(self, *, print_results: bool = False) -> ReliabilityResult:
        actual = [fn for fn, _args in self.recorded_calls]
        expected_set = set(self.expected_tool_calls)
        actual_set = set(actual)

        passed = [t for t in self.expected_tool_calls if t in actual_set]
        missing = [t for t in self.expected_tool_calls if t not in actual_set]
        additional = [t for t in actual if t not in expected_set]

        ok = not missing and (self.allow_additional_tool_calls or not additional)
        self.result = ReliabilityResult(
            eval_status="PASSED" if ok else "FAILED",
            expected_tool_calls=self.expected_tool_calls,
            actual_tool_calls=actual,
            passed_tool_calls=passed,
            missing_tool_calls=missing,
            additional_tool_calls=additional,
        )
        if print_results:
            if self.name:
                print(f"[{self.name}]")
            print(self.result)
        return self.result
