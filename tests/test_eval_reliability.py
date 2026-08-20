"""Deterministic tests for the eval reliability module (no live model).

Two layers:
1. Pure unit tests of SdkReliabilityEval / ReliabilityResult over synthetic call
   lists -- no agno, no FakeRunner.
2. Harness tests with the LLM "mocked": the model's only job is to emit a
   code_exec script, so we supply the script directly and run it through the
   REAL CodeExecTools + FakeRunner recorder, then grade with SdkReliabilityEval.
   This proves intercept -> record -> grade without any model or network.
"""
from __future__ import annotations

import pytest

from backend.code_exec.runner import FakeRunner
from backend.config import settings
from backend.tools.code_exec_tools import CodeExecTools
from evals.reliability import ReliabilityResult, SdkReliabilityEval


# --- 1. pure unit tests of the reliability module ---------------------------

def test_passed_when_all_expected_present_extras_allowed():
    res = SdkReliabilityEval(
        recorded_calls=[("a", {}), ("b", {}), ("c", {})],
        expected_tool_calls=["a", "b"],
    ).run()
    assert isinstance(res, ReliabilityResult)
    assert res.passed and res.eval_status == "PASSED"
    assert res.passed_tool_calls == ["a", "b"]
    assert res.missing_tool_calls == []
    assert res.additional_tool_calls == ["c"]  # allowed by default


def test_failed_and_lists_missing():
    res = SdkReliabilityEval(
        recorded_calls=[("a", {})],
        expected_tool_calls=["a", "b"],
    ).run()
    assert not res.passed and res.eval_status == "FAILED"
    assert res.missing_tool_calls == ["b"]


def test_additional_disallowed_fails():
    res = SdkReliabilityEval(
        recorded_calls=[("a", {}), ("x", {})],
        expected_tool_calls=["a"],
        allow_additional_tool_calls=False,
    ).run()
    assert not res.passed
    assert res.additional_tool_calls == ["x"]


def test_additional_allowed_passes():
    res = SdkReliabilityEval(
        recorded_calls=[("a", {}), ("x", {})],
        expected_tool_calls=["a"],
        allow_additional_tool_calls=True,
    ).run()
    assert res.passed


def test_assert_passed_raises_on_fail_but_not_on_pass():
    SdkReliabilityEval(recorded_calls=[("a", {})], expected_tool_calls=["a"]).run().assert_passed()
    with pytest.raises(AssertionError):
        SdkReliabilityEval(recorded_calls=[], expected_tool_calls=["a"]).run().assert_passed()


# --- 2. harness tests with the LLM mocked (canned code_exec script) ---------

def _recording_runner():
    """FakeRunner that records mai.* calls and returns simple stubs (no network)."""
    calls: list[tuple[str, dict]] = []

    def call_fn(fn, *args, **kw):
        calls.append((fn, {"args": list(args), **kw}))
        return {"get_profile_picture": "https://pic.example/dana.jpg",
                "run_model": "https://vid.example/dana.mp4"}.get(fn, "")

    return FakeRunner(call_fn=call_fn), calls


def test_harness_passes_on_good_script(session, two_tenants, monkeypatch):
    monkeypatch.setattr(settings, "guardrail_enabled", False)
    runner, calls = _recording_runner()
    tools = CodeExecTools(user=two_tenants[1], device_id="dev1", runner=runner, base_url="x")
    # The script a competent model would write for "make a video of Dana":
    tools.code_exec("pic = mai.get_profile_picture('Dana')\n"
                    "mai.run_model('wan-video/wan-2.1-i2v', {'image': pic})")
    res = SdkReliabilityEval(
        recorded_calls=calls,
        expected_tool_calls=["get_profile_picture", "run_model"],
    ).run()
    res.assert_passed()
    assert res.actual_tool_calls == ["get_profile_picture", "run_model"]


def test_harness_fails_when_model_skips_video(session, two_tenants, monkeypatch):
    monkeypatch.setattr(settings, "guardrail_enabled", False)
    runner, calls = _recording_runner()
    tools = CodeExecTools(user=two_tenants[1], device_id="dev1", runner=runner, base_url="x")
    tools.code_exec("print(mai.get_profile_picture('Dana'))")  # fetched but never made the video
    res = SdkReliabilityEval(
        recorded_calls=calls,
        expected_tool_calls=["get_profile_picture", "run_model"],
    ).run()
    assert not res.passed
    assert res.missing_tool_calls == ["run_model"]
