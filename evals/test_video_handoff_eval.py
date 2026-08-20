"""Live-model eval: profile-pic -> video hand-off (CodeAct path).

Scenario: "Make a video of my friend Dana" should (1) fetch Dana's profile
picture, then (2) inject that image + params into a Replicate video model via
run_model -- the video URL returns through code_exec (there is no dedicated
video tool/skill, by design).

The agent runs for REAL (needs a model API key), but every mai.* SDK call is
stubbed in-process by FakeRunner -- no sandbox, no Replicate, no WhatsApp. The
recorded mai.* calls are the reliability signal (agno's tool_calls only ever
show `code_exec` on this path, so we assert one level deeper).

Run:  pytest evals/test_video_handoff_eval.py -m live -s
  or: python -m evals.test_video_handoff_eval
Not collected by the default `pytest` (testpaths=["tests"]). Needs the same
model env the app uses (e.g. OPENROUTER_API_KEY); auto-skips if none.
"""
from __future__ import annotations

import inspect
import os

import pytest
from agno.agent import Agent

import backend.code_exec.mai_sdk as sdk
from backend.agent.prompts import build_system_prompt
from backend.code_exec.runner import FakeRunner
from backend.config import settings
from backend.models.factory import get_model
from backend.tools.code_exec_tools import CodeExecTools
from evals.reliability import SdkReliabilityEval

FRIEND_PIC_URL = "https://pps.example/dana_avatar.jpg"
VIDEO_URL = "https://replicate.delivery/out/dana_video.mp4"
EXPECTED_TOOL_CALLS = ["get_profile_picture", "run_model"]  # no first-class video tool -> run_model
N_RUNS = int(os.environ.get("EVAL_RUNS", "3"))
PASS_THRESHOLD = float(os.environ.get("EVAL_PASS_RATE", "0.66"))


class _EvalUser:  # token_store.mint only reads .account_id/.id -> no DB needed
    account_id = 1
    id = 1
    whatsapp_number = "+972500000000"
    display_name = "Alex"
    timezone = "Asia/Jerusalem"


def _bind(fn_name, args, kw):
    """Normalize positional+keyword SDK args to a name->value dict via the real signature."""
    try:
        return dict(inspect.signature(getattr(sdk, fn_name)).bind_partial(*args, **kw).arguments)
    except Exception:
        return {"_args": list(args), **kw}


def _make_recorder():
    """FakeRunner whose call_fn records every mai.* call and returns canned data."""
    calls: list[tuple[str, dict]] = []

    def call_fn(fn, *args, **kw):
        calls.append((fn, _bind(fn, args, kw)))
        if fn == "find_chats":
            return [{"name": "Dana", "id": "972500000001@s.whatsapp.net", "is_group": False}]
        if fn == "get_user_details":
            return {"name": "Dana"}
        if fn == "get_profile_picture":
            return FRIEND_PIC_URL
        if fn == "search_models":
            return ("Found (use run_model):\n- wan-video/wan-2.1-i2v: image-to-video\n"
                    "- stability-ai/stable-video-diffusion: image to short video")
        if fn == "run_model":
            return VIDEO_URL
        if fn == "generate_image":
            return "https://img.example/out.png"
        if fn == "show_image":
            return {"shown": True}
        if fn in ("send_message", "send_media"):
            return {"sent": True}
        return ""

    return FakeRunner(call_fn=call_fn), calls


def _build_agent(model, runner):
    """Mai with ONLY code_exec, so we know we're exercising the CodeAct path."""
    u = _EvalUser()
    tools = [CodeExecTools(user=u, device_id="dev1", runner=runner,
                           base_url="http://local.invalid",
                           current_chat="972500000001@s.whatsapp.net")]
    return Agent(name="Mai", model=model, tools=tools,
                 system_message=build_system_prompt(user_display_name=u.display_name,
                                                     user_timezone=u.timezone),
                 markdown=False, telemetry=False, add_history_to_context=False)


def _model_or_skip():
    try:
        m = get_model()
    except Exception as exc:  # provider/key not configured
        pytest.skip(f"no model configured: {exc}")
    if m is None:
        pytest.skip("no model configured")
    return m


def run_once(model):
    settings.guardrail_enabled = False  # skip the pre-flight LLM screen in eval
    runner, calls = _make_recorder()
    _build_agent(model, runner).run("Make a video of my friend Dana")
    result = SdkReliabilityEval(
        name="profile_pic_to_video",
        recorded_calls=calls,
        expected_tool_calls=EXPECTED_TOOL_CALLS,
        allow_additional_tool_calls=True,
    ).run()
    return result, calls


@pytest.mark.live
def test_profile_pic_to_video_handoff():
    model = _model_or_skip()
    passes = 0
    for i in range(N_RUNS):
        result, _ = run_once(model)
        print(f"[run {i + 1}/{N_RUNS}] {result.eval_status} "
              f"missing={result.missing_tool_calls} actual={result.actual_tool_calls}")
        passes += result.passed
    rate = passes / N_RUNS
    assert rate >= PASS_THRESHOLD, f"pass rate {rate:.0%} < {PASS_THRESHOLD:.0%}"


if __name__ == "__main__":
    _m = get_model()
    _total = 0
    for _i in range(N_RUNS):
        _result, _calls = run_once(_m)
        print(f"[run {_i + 1}/{N_RUNS}] {_result}")
        for _f, _a in _calls:
            print(f"    mai.{_f}({_a})")
        _total += _result.passed
    print(f"\npass rate: {_total}/{N_RUNS}")
