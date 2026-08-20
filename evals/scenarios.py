"""Eval scenarios — declarative descriptions of a single Mai turn to grade.

A Scenario is data: the @Mai command, the chat shape (so we can build the
production-shaped context input), and what success means across the layers:
  - expected_sdk_calls: mai.* calls inside the sandbox (the WORK)
  - expected_tool_calls: agno tools that must fire (the DELIVERY/action), optionally
    with an argument predicate via tool_arg_checks
  - state_check: a predicate over the REAL persisted DB rows the production modules
    wrote (the source of truth) — grade on stored state, not just the model's args
Canned SDK returns (stubs) keep the run hermetic across trusted boundaries (no
Replicate, no WhatsApp transport); production MODULES (ScheduleService, the toolkits)
run for real. Add a scenario by appending to SCENARIOS.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

from evals.harness import (ErrorReportSnapshot, ScheduleSnapshot, _read_error_reports,
                           _read_memories, _read_schedules, build_assistant_dm_input,
                           build_direct_chat_input, group_chat_context)

# A stub is either a literal canned return, or a callable taking the normalized
# args dict and returning a value.
Stub = Any | Callable[[dict], Any]


@dataclass
class Scenario:
    name: str
    command: str                       # the @Mai instruction the user typed
    # Success is graded across layers (a run must satisfy every one that's set):
    expected_sdk_calls: list[str] = field(default_factory=list)   # mai.* (the WORK)
    expected_tool_calls: list[str] = field(default_factory=list)  # agno tools (the ACTION)
    tool_arg_checks: dict[str, Callable[[dict], bool]] = field(default_factory=dict)
    state_check: Callable[[list[ScheduleSnapshot]], bool] | None = None  # over REAL persisted rows
    output_check: Callable[[str], bool] | None = None  # over the final reply text (the delivered answer)
    sends_check: Callable[[list[dict]], bool] | None = None  # over the provider's recorded sends
    # Chat shape:
    is_self_dm: bool = False           # True => user instructs Mai in their own DM
    contact_name: str = ""             # who the 1:1 chat is with (when not self-DM)
    contact_number: str = ""           # their phone (digits) — what get_profile_picture needs
    contact_jid: str = ""              # full JID, passed to CodeExecTools.current_chat
    stubs: dict[str, Stub] = field(default_factory=dict)
    allow_additional_tool_calls: bool = True
    # Per-scenario agentic-loop bound. None = the runner's default (6, a COST bound —
    # production codeact_mode=False runs UNBOUNDED). Set when the scenario's legitimate
    # flow needs more rounds (e.g. a skill that mandates a judge loop before sending).
    tool_call_limit: int | None = None
    timeout_s: int | None = None  # per-scenario wall-clock bound (None = runner default)
    # Optional explicit WHATSAPP CONTEXT block (e.g. group_chat_context(...)) — overrides
    # the 1:1/self-DM shapes so a single-turn scenario can put Mai in a GROUP chat.
    context: str | None = None
    # Knowledge base (Drive-backed docs): seed as {title: content}. {} = connected but
    # empty; None = the scenario doesn't involve the KB (Drive stays unconnected).
    docs: dict[str, str] | None = None
    docs_check: Callable[[dict[str, str]], bool] | None = None  # over the KB's FINAL files
    # Over-trigger guard: tools that must NOT fire this turn (any hit fails the run).
    forbidden_tool_calls: list[str] = field(default_factory=list)

    @property
    def current_chat(self) -> str:
        return self.contact_jid or "972500000000@s.whatsapp.net"  # owner's own DM

    def agent_input(self) -> str:
        if self.context:
            return f"{self.context}\n\nMy instruction (@Mai): {self.command}"
        if self.is_self_dm:
            return build_assistant_dm_input(command=self.command)
        return build_direct_chat_input(command=self.command, contact_name=self.contact_name,
                                       contact_number=self.contact_number)


# --- video hand-off: profile pic -> generated video -> SEND it --------------
FRIEND_PIC_URL = "https://pps.example/dana_avatar.jpg"
VIDEO_URL = "https://replicate.delivery/out/dana_video.mp4"

_VIDEO_HANDOFF = Scenario(
    name="profile_pic_to_video",
    command="Make a video of my friend Dana",
    contact_name="Dana",
    contact_number="972500000001",
    contact_jid="972500000001@s.whatsapp.net",
    # WORK layer (inside code_exec): no first-class video tool by design -> the model must compose
    # get_profile_picture + run_model in the SDK. That's the ONE intended way (the SDK function is
    # exposed for exactly this); we assert it, not a path-agnostic alternative.
    expected_sdk_calls=["get_profile_picture", "run_model"],
    # DELIVERY layer (agno tool): generating the video isn't enough — Mai must SEND it.
    expected_tool_calls=["send_video"],
    stubs={
        "find_chats": [{"name": "Dana", "id": "972500000001@s.whatsapp.net", "is_group": False}],
        "get_user_details": {"name": "Dana"},
        "get_profile_picture": FRIEND_PIC_URL,
        "search_models": ("Found (use run_model):\n- wan-video/wan-2.1-i2v: image-to-video\n"
                          "- stability-ai/stable-video-diffusion: image to short video"),
        "get_model_schema": {"input": {"image": "uri", "prompt": "string"}},
        "run_model": VIDEO_URL,
        "generate_image": "https://img.example/out.png",
        "show_image": {"shown": True},
    },
)


# --- recurring schedule: daily 8am FIFA World Cup summary --------------------
def _is_daily_8am_schedule(rows: list[ScheduleSnapshot]) -> bool:
    """The assignment is correct iff an ACTIVE, RECURRING schedule firing daily at 08:00
    was persisted. Reads the REAL Schedule rows (source of truth) — so a create_schedule
    that errored (limit/validation) and persisted nothing correctly FAILS. Robust to the
    equivalent cron spellings of 'every day' (dow '*', '0-6', '0-7')."""
    for s in rows:
        if not s.active or s.schedule_type.lower() != "recurring":
            continue
        parts = (s.cron or "").split()
        if len(parts) != 5:
            continue
        minute, hour, dom, month, dow = parts
        every_day = (dom in ("*", "*/1") and month in ("*", "*/1")
                     and dow in ("*", "0-6", "0-7", "*/1", "1-7"))
        if minute in ("0", "00") and hour in ("8", "08") and every_day:
            return True
    return False


_FIFA_DAILY_SUMMARY = Scenario(
    name="schedule_fifa_daily_summary",
    command="Every day at 8am, send me a summary of that day's FIFA World Cup results.",
    is_self_dm=True,
    # Pure scheduling turn — no sandbox work. The whole task is the ASSIGNMENT: Mai must
    # call the schedule tool AND a correct daily-8am recurring row must actually persist.
    expected_tool_calls=["create_schedule"],
    state_check=_is_daily_8am_schedule,
)


# --- summarize a 1:1 chat (code_exec: read the chat, then summarize) ---------
_CHAT_SUMMARY = Scenario(
    name="summarize_chat",
    command="Summarize my conversation with Dana.",
    contact_name="Dana",
    contact_number="972500000001",
    contact_jid="972500000001@s.whatsapp.net",
    # OUTCOME grade (path-agnostic): Mai must actually READ the chat before summarizing — via the
    # SDK or the direct read_chat tool (both serve the same stub data). The proof it read is that the
    # summary surfaces a SPECIFIC detail that lives only in the conversation (the place + the time).
    output_check=lambda r: any(w in (r or "").lower() for w in ("luigi", "8pm", "8 pm")),
    stubs={
        "find_chats": [{"name": "Dana", "id": "972500000001@s.whatsapp.net", "is_group": False}],
        "read_chat": [
            {"sender": "Dana", "text": "Can we move dinner to 8pm?", "from_me": False,
             "media_type": None, "media_url": None},
            {"sender": "Alex", "text": "Sure, 8pm works. Same place?", "from_me": True,
             "media_type": None, "media_url": None},
            {"sender": "Dana", "text": "Yes, see you at Luigi's", "from_me": False,
             "media_type": None, "media_url": None},
        ],
    },
)


# --- one-off reminder: a SINGLE future fire (not recurring) -------------------
def _is_oneoff_future(rows: list[ScheduleSnapshot]) -> bool:
    """Correct iff an ACTIVE, ONE-OFF schedule with a future run_at was persisted (the spec
    validation rejects a past run_at, so a persisted once-row IS in the future). Counterpart
    to the recurring eval — it must create a one-off, NOT a cron."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    for s in rows:
        if not s.active or s.schedule_type.lower() != "once" or s.run_at is None:
            continue
        run_at = s.run_at if s.run_at.tzinfo else s.run_at.replace(tzinfo=timezone.utc)
        if run_at > now:
            return True
    return False


_ONEOFF_REMINDER = Scenario(
    name="schedule_oneoff_reminder",
    command="Remind me tomorrow at 3pm to call the dentist.",
    is_self_dm=True,
    expected_tool_calls=["create_schedule"],
    state_check=_is_oneoff_future,
)


# --- recurring on a SPECIFIC WEEKDAY: every Monday at 9am --------------------
def _is_weekly_monday_9am(rows: list[ScheduleSnapshot]) -> bool:
    """Correct iff an ACTIVE, RECURRING schedule firing at 09:00 ONLY on Mondays persisted.
    Unlike the daily case the day-of-week field must be Monday (cron dow 1; 0/7 are Sunday) —
    so this catches a model that drops the weekday and schedules every day. Robust to the
    spellings 'mon' and the single-value range '1-1'."""
    for s in rows:
        if not s.active or s.schedule_type.lower() != "recurring":
            continue
        parts = (s.cron or "").split()
        if len(parts) != 5:
            continue
        minute, hour, dom, month, dow = parts
        monday_only = dow.lower() in ("1", "mon", "1-1")
        if (minute in ("0", "00") and hour in ("9", "09")
                and dom in ("*", "*/1") and month in ("*", "*/1") and monday_only):
            return True
    return False


_WEEKLY_WEEKDAY = Scenario(
    name="schedule_weekly_weekday",
    command="Every Monday at 9am, remind me to submit my weekly status report.",
    is_self_dm=True,
    expected_tool_calls=["create_schedule"],
    state_check=_is_weekly_monday_9am,
)


# --- daily 8am, instructed in HEBREW (parsing robustness across language) -----
# Same target as _FIFA_DAILY_SUMMARY, phrased in Hebrew. The grade is on the persisted cron
# (language-agnostic), so a pass proves the model parsed Hebrew time/recurrence phrasing
# ("כל יום בשעה 8 בבוקר") into the right schedule — not that any Hebrew text came back.
_HEBREW_DAILY_SUMMARY = Scenario(
    name="schedule_hebrew_daily",
    command="כל יום בשעה 8 בבוקר תשלח לי סיכום של תוצאות גביע העולם של אותו יום.",
    is_self_dm=True,
    expected_tool_calls=["create_schedule"],
    state_check=_is_daily_8am_schedule,
)


# --- transcribe an inbound VOICE NOTE (no first-class tool: read_chat -> run_model) ----------
# Mai has no dedicated transcribe tool. read_chat surfaces the voice clip's media_url; the model
# must feed THAT into run_model (a speech-to-text model) itself. So this grades the emergent
# capability: did it (a) read the chat, (b) actually run a transcription model on the clip, and
# (c) surface what was said — vs shrugging "Dana sent a voice note" or inventing content.
def _voice_transcript(args: dict) -> str:
    """run_model stub that 'transcribes' ONLY when the model actually fed the voice clip's url into
    the call — so the eval verifies it CONNECTED read_chat's media_url to run_model, not merely that
    it called run_model for something. A wrong/empty input returns a Replicate-style error the model
    must react to, and keeps the transcript out of the reply (so output_check fails)."""
    if "dana_voice.ogg" in str(args).lower():
        return "Hey, can you grab milk and eggs on the way home? Thanks!"
    return "Replicate error: transcription model received no audio (the input file url was missing)."


_VOICE_NOTE = Scenario(
    name="transcribe_voice_note",
    command="What did Dana say in her voice message?",
    contact_name="Dana",
    contact_number="972500000001",
    contact_jid="972500000001@s.whatsapp.net",
    # OUTCOME grade (path-agnostic): the transcript words must surface. The model may read+transcribe
    # via the SDK OR the direct read_chat/run_model tools — both delegate to the same stubs, incl. the
    # gated transcription stub that only "hears" the clip if its media_url was actually passed.
    output_check=lambda r: any(w in (r or "").lower() for w in ("milk", "egg")),  # surfaced the words
    stubs={
        "find_chats": [{"name": "Dana", "id": "972500000001@s.whatsapp.net", "is_group": False}],
        # A voice message: no text, just an audio clip at a fetchable url (what production returns).
        "read_chat": [
            {"sender": "Dana", "text": "", "from_me": False,
             "media_type": "audio", "media_url": "https://media.example/dana_voice.ogg"},
        ],
        "search_models": ("Found (use run_model):\n- openai/whisper: speech-to-text transcription\n"
                          "- vaibhavs10/incredibly-fast-whisper: fast audio transcription"),
        "get_model_schema": {"input": {"audio": "uri"}},
        "run_model": _voice_transcript,   # callable: returns the transcript only if the clip url was passed
    },
)


# --- search WITHIN a conversation: "did we ever settle on X?" --------------------------------
# Mai has no message-search primitive — read_chat returns the recent window and the model must
# find the answer in it. This grades the in-window case: read the chat AND correctly extract the
# specific fact (the place they agreed on), not just summarize. A model that reads but answers
# wrong/vaguely fails the output_check.
_CONV_SEARCH = Scenario(
    name="search_conversation",
    command="Did Dana and I ever settle on where to have dinner?",
    contact_name="Dana",
    contact_number="972500000001",
    contact_jid="972500000001@s.whatsapp.net",
    # OUTCOME grade (path-agnostic): read via the SDK or the direct read_chat tool; the answer must
    # name the place they actually agreed on (a detail only in the conversation, not derivable).
    output_check=lambda r: "luigi" in (r or "").lower(),   # named the place they agreed on
    stubs={
        "find_chats": [{"name": "Dana", "id": "972500000001@s.whatsapp.net", "is_group": False}],
        "read_chat": [
            {"sender": "Dana", "text": "Where should we eat Friday?", "from_me": False,
             "media_type": None, "media_url": None},
            {"sender": "Alex", "text": "How about Luigi's?", "from_me": True,
             "media_type": None, "media_url": None},
            {"sender": "Dana", "text": "Perfect — Luigi's it is. 8pm?", "from_me": False,
             "media_type": None, "media_url": None},
            {"sender": "Alex", "text": "8pm works, see you then", "from_me": True,
             "media_type": None, "media_url": None},
        ],
    },
)


# --- ErrorReport #23: cross-script contact lookup (rev e54ba55, 2026-07-07) ----------
# Problem (Mai):   searching "yoav mizrahi" (English) doesn't find the contact saved as
#                  "יואב מזרחי" (Hebrew) — same person, different script.
# Expected (Mai):  the lookup should find the contact regardless of the query's script
#                  (transliteration-aware matching, or the model widening its search).
# Both lookup paths run REAL matching over one canned chat list: the direct WhatsAppTools
# path searches the RecordingProvider's chats (seeded from the callable stub's full list),
# and the code_exec path routes mai.find_chats through the PRODUCTION dispatch() matcher
# — never a reimplementation of the substring logic (that's the code whose behavior the
# report is about, so faking it would be drift). The eval passes whether the fix lands
# tool-side (transliteration matching) or model-side (search strategy).
_YOAV_NUMBER = "972541112233"
_CANNED_CHATS = [
    {"name": "יואב מזרחי", "jid": f"{_YOAV_NUMBER}@s.whatsapp.net", "is_group": False},
    {"name": "גיא פרץ", "jid": "972540000002@s.whatsapp.net", "is_group": False},
    {"name": "Dana Levy", "jid": "972540000003@s.whatsapp.net", "is_group": False},
    {"name": "חברים", "jid": "120363040000000001@g.us", "is_group": True},
]


def _find_chats_via_real_dispatch(args: dict):
    """The SDK-path find_chats stub: the real dispatch() matcher over the canned chats.
    Pins its own provider (independent of eval_runtime's ambient patch) so it behaves the
    same when _provider_from_scenario seeds the RecordingProvider from it with args={}."""
    import backend.whatsapp as whatsapp
    from backend.code_exec import dispatch as dispatch_mod

    canned = SimpleNamespace(
        recent_chats=lambda **kw: [dict(c) for c in _CANNED_CHATS],
        list_my_groups=lambda **kw: [dict(c) for c in _CANNED_CHATS if c["is_group"]],
    )
    orig = whatsapp.get_provider
    whatsapp.get_provider = lambda: canned
    try:
        clean = {k: v for k, v in args.items() if v is not None}  # mirror mai_sdk._call
        return dispatch_mod.dispatch("find_chats", clean,
                                     SimpleNamespace(device_id="dev1"), session=None, token="")
    finally:
        whatsapp.get_provider = orig


def _canned_user_details(args: dict) -> dict:
    """Details only for a real number from the canned data — a name-string query must NOT
    shortcut to the answer (prod's _resolve_person would fail the same way)."""
    phone = re.sub(r"\D", "", str(args.get("phone") or ""))
    return {"name": "יואב מזרחי"} if phone and phone in _YOAV_NUMBER else {}


def _reply_has_yoav_number(reply: str) -> bool:
    """Success = Mai actually tells the user the number (last 9 digits, any formatting)."""
    return _YOAV_NUMBER[-9:] in re.sub(r"\D", "", reply or "")


_REPORT_23_CROSS_SCRIPT = Scenario(
    name="report_23_cross_script_contact",
    command="מה המספר של yoav mizrahi?",
    is_self_dm=True,
    # No expected-call layers: the model may search via the direct WhatsApp tools OR via
    # code_exec — both run real matchers. The number appears ONLY in the canned jid, so
    # the OUTPUT check alone proves a successful cross-script lookup happened.
    stubs={
        "find_chats": _find_chats_via_real_dispatch,
        "get_user_details": _canned_user_details,
    },
    output_check=_reply_has_yoav_number,
)


# --- ErrorReport #24: skill must be READ before acting (rev e7c5fae, 2026-07-07) ------
# Problem (Mai):   asked to edit Eyal's profile pic (Trump hair), Mai jumped straight to
#                  run_model without loading the photo-edit skill — wrong model choice
#                  (flux-kontext-pro, the skill's #3 "light changes only" pick, instead of
#                  #1 openai/gpt-image-2) and none of the skill's best practices.
# Expected (Mai):  the matching skill's instructions must actually STEER the action.
# How this is measurable (mode-agnostic): the skill body — model priorities, input shape —
# reaches the model only through the REAL skill machinery (vector mode: injected by
# skills_context; stub mode: fetched via get_skill_instructions). The search_models stub
# is a TRAP — it offers flux-kontext-pro (what prod Mai wrongly used) and face-to-many,
# but NOT the skill's #1 models. So producing the accepted edit URL requires knowledge
# that exists ONLY in the skill body: call one of the priority-1 models with the skill's
# input shape. The gated run_model stub returns the real edited URL only then.
_EYAL_NUMBER = "972540000077"
# A data URI (valid 1x1 JPEG), not an https URL: the FakeRunner sandbox runs scripts
# in-process, so the skill template's `requests.get(pic_url)` on a fake host would fail
# and eat the tool budget — eval drift, since prod serves a fetchable CDN URL. A data URI
# is fetch-free and is exactly the input form the skill's models take.
_TINY_JPEG_DATA_URI = ("data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
                       "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
                       "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AVN//2Q==")
_MATAN_PIC_URL = _TINY_JPEG_DATA_URI
_MATAN_EDITED_URL = "https://replicate.delivery/eval/matan_trump_hair.png"
_WRONG_MODEL_URL = "https://replicate.delivery/eval/wrong_model_output.png"

# The skill's priority-1 models and the input key each expects (from skills/photo-edit).
_SKILL_EDIT_MODELS = {"openai/gpt-image-2": "input_images", "qwen/qwen-image-edit-plus": "image"}
_EVAL_VLM = "yorickvp/llava-13b"  # the judge model the vision search surfaces


def _make_gated_edit(edited_url: str) -> Callable[[dict], str]:
    """Build a run_model stub: the edit 'succeeds' (returns the URL the grader accepts)
    ONLY when called the way the photo-edit skill teaches — a priority-1 model with its
    input key carrying the image (a data URI or a source URL). Any other edit model (e.g.
    the flux-kontext-pro the trap search offers, and prod Mai wrongly used in #24)
    returns a plausible-but-wrong URL, so the run completes and cleanly fails the
    delivery check. The VLM (the skill's mandatory likeness judge) returns a passing
    verdict, so a skill-following run flows straight through judge → send."""
    def gate(args: dict) -> str:
        model = str(args.get("model") or "")
        inputs = args.get("inputs") or {}
        if model == _EVAL_VLM or "llava" in model or "vl" in model.split("/")[-1][:4]:
            return "Same person. Score: 9/10. No differences in face, age, or gender."
        key = _SKILL_EDIT_MODELS.get(model)
        if key and "data:image" in str(inputs.get(key, "")):
            return edited_url
        return _WRONG_MODEL_URL
    return gate


_gated_photo_edit = _make_gated_edit(_MATAN_EDITED_URL)


def _search_models_trap(args: dict) -> str:
    """search_models stub. Vision/judge queries get the VLM the skill's likeness check
    needs. EDIT queries are the TRAP: only flux-kontext-pro (prod Mai's wrong pick) and
    face-to-many — never a priority-1 edit model; the skill body is the only source."""
    q = str(args.get("query") or "").lower()
    if any(w in q for w in ("vision", "vlm", "llava", "visual", "q&a", "qa", "judge",
                            "compare", "same person", "image question")):
        return f"Found (use run_model):\n- {_EVAL_VLM}: visual question answering on images"
    return ("Found (use run_model):\n"
            "- black-forest-labs/flux-kontext-pro: instruction-based image editing\n"
            "- fofr/face-to-many: turn a face into 3D/toy/clay styles")


def _model_schema_for(args: dict) -> dict:
    model = str(args.get("model") or "")
    key = _SKILL_EDIT_MODELS.get(model)
    if key:
        return {"input": {key: "array", "prompt": "string", "number_of_images": "integer",
                          "input_fidelity": "string"}}
    return {"input": {"image": "uri", "prompt": "string"}}


_REPORT_24_SKILL_FIRST = Scenario(
    name="report_24_skill_read_before_action",
    command="תערכי את תמונת הפרופיל של אייל — תוסיפי לו שיער בסגנון טראמפ",
    contact_name="Eyal",
    contact_number=_EYAL_NUMBER,
    contact_jid=f"{_EYAL_NUMBER}@s.whatsapp.net",
    # The report's ask, graded on the OUTCOME: only skill-shaped run_model args yield the
    # URL send_image must deliver, so a pass proves the skill steered the action —
    # regardless of skill_mode (injected body or fetched via get_skill_instructions).
    expected_tool_calls=["send_image"],
    tool_arg_checks={
        "send_image": lambda a: _MATAN_EDITED_URL in str(a),
    },
    # The skill's full flow (read → fetch → edit → find a VLM → judge → send) legitimately
    # needs more rounds than the 6-call cost default; prod runs unbounded (codeact off).
    tool_call_limit=12,
    timeout_s=240,
    stubs={
        "find_chats": [{"name": "Eyal", "id": f"{_EYAL_NUMBER}@s.whatsapp.net",
                        "is_group": False}],
        "get_user_details": {"name": "Eyal"},
        "get_profile_picture": _MATAN_PIC_URL,
        "search_models": _search_models_trap,
        "get_model_schema": _model_schema_for,
        "run_model": _gated_photo_edit,
        "generate_image": _WRONG_MODEL_URL,
    },
)


# --- ErrorReport #26: edit a chat image; NEVER send raw base64 as text (rev cfbe463) ---
# Problem (Mai):   editing an image from the chat whose media URL expired (401): the
#                  code_exec run_model fallback hit 404 on /internal/sdk/run_model, and
#                  Mai then SENT THE RAW BASE64 as a text message into the group
#                  ("שלחת כאן ג'יבריש במקום את התמונה").
# Expected (Mai):  1) a data URI through run_model must work as the edit input;
#                  2) NEVER send raw base64 as a text message.
# The infra half (the SDK endpoint 404) is a transport bug the stubbed harness can't
# guard. What this eval locks in is the behavioral contract: the chat image arrives as a
# data URI (the form the download-fallback yields — a long base64 blob sitting in the
# model's context, maximal temptation to paste it), the edit must go through a skill
# priority-1 model with that data URI (same gate as #24), the result must be DELIVERED
# with send_image, and no delivered text — reply or send — may carry a base64 blob.
_NOA_JID = "972540000088@s.whatsapp.net"
_NOA_EDITED_URL = "https://replicate.delivery/eval/noa_hearts_friends.png"
_BASE64_BLOB = re.compile(r"[A-Za-z0-9+/=]{120,}")


def _no_base64_text(text: str) -> bool:
    return ";base64," not in (text or "") and not _BASE64_BLOB.search(text or "")


def _sends_have_no_base64_text(sends: list[dict]) -> bool:
    """The delivered-state contract: no TEXT that went out (message or media caption)
    carries a raw base64 blob. Image/file sends themselves are fine."""
    for s in sends:
        if not _no_base64_text(str(s.get("text") or "")) or not _no_base64_text(str(s.get("caption") or "")):
            return False
    return True


_REPORT_26_NO_RAW_BASE64 = Scenario(
    name="report_26_chat_image_edit_no_base64",
    command="קחי את התמונה שנועה שלחה כאן, תקיפי אותה בלבבות ותוסיפי דמות או שתיים מ-Friends",
    contact_name="נועה",
    contact_number="972540000088",
    contact_jid=_NOA_JID,
    expected_tool_calls=["send_image"],
    tool_arg_checks={"send_image": lambda a: _NOA_EDITED_URL in str(a)},
    stubs={
        "find_chats": [{"name": "נועה", "id": _NOA_JID, "is_group": False}],
        "get_user_details": {"name": "נועה"},
        # The image Noa sent, already in data-URI form (what the expired-URL download
        # fallback yields; also seeds the provider's messages for the direct read path).
        "read_chat": [
            {"sender": "נועה", "text": "", "from_me": False,
             "media_type": "image", "media_url": _TINY_JPEG_DATA_URI},
        ],
        "search_models": _search_models_trap,
        "get_model_schema": _model_schema_for,
        "run_model": _make_gated_edit(_NOA_EDITED_URL),
        "generate_image": _WRONG_MODEL_URL,
    },
    # The reply is posted into the chat in prod, so it must be blob-free too.
    output_check=_no_base64_text,
    sends_check=_sends_have_no_base64_text,
    tool_call_limit=12,   # same skill judge-loop budget as #24
    timeout_s=240,        # the judge loop runs long; 150s misreads slow tails as failures
)


# --- ErrorReport #33: profile-pic edit via gpt-image-2 returned RAW BASE64 (rev 61c322e) ---
# Problem (Mai):  editing דן's profile pic with Trump hair — run_model(openai/gpt-image-2,
#                 input_images=[…]) returned a long raw-base64-looking string (truncated),
#                 not a URL; separately, `import mai` failed in the sandbox.
# Expected (Mai): run_model should yield a sendable URL, delivered via send_image.
# The sandbox-import half is infra the stubbed harness can't guard (same carve-out as
# #26). The behavioral contract locked in — a CLONE of #24 (same profile-pic edit flow)
# + #26 (no delivered base64), with the #33-specific trap layered on: calling a
# gpt-image-* model returns a RAW BASE64 BLOB, exactly what prod dumped into Mai's
# context. A pass = a REAL PICTURE is delivered (any https image via send_image) and the
# blob never reaches the user in any delivered text. Deliberately NOT re-graded here:
# which model produced the image (#24's contract) and output quality (the skill's VLM
# judge verifies that in prod).
_DAN_NUMBER = "972540000099"  # canned — never the reporter's real number
_DAN_JID = f"{_DAN_NUMBER}@s.whatsapp.net"
_SHAI_EDITED_URL = "https://replicate.delivery/eval/shai_trump_hair.png"
_RAW_B64_RETURN = "iVBORw0KGgoAAAANSUhEUg" + "A" * 400  # prod's gpt-image-2 run output


def _gated_shai_edit(args: dict) -> str:
    if "gpt-image" in str(args.get("model") or ""):
        return _RAW_B64_RETURN  # the #33 hazard: a blob, not a URL
    return _make_gated_edit(_SHAI_EDITED_URL)(args)


_REPORT_33_B64_RETURN = Scenario(
    name="report_33_profile_edit_base64_return",
    command="תערכי את תמונת הפרופיל של דן — תוסיפי לו שיער של טראמפ",
    contact_name="דן",
    contact_number=_DAN_NUMBER,
    contact_jid=_DAN_JID,
    expected_tool_calls=["send_image"],
    tool_arg_checks={"send_image": lambda a: "https://" in str(a) and _no_base64_text(str(a))},
    stubs={
        "find_chats": [{"name": "דן", "id": _DAN_JID, "is_group": False}],
        "get_user_details": {"name": "דן"},
        "get_profile_picture": _TINY_JPEG_DATA_URI,
        "search_models": _search_models_trap,
        "get_model_schema": _model_schema_for,
        "run_model": _gated_shai_edit,
        "generate_image": _WRONG_MODEL_URL,
    },
    output_check=_no_base64_text,
    sends_check=_sends_have_no_base64_text,
    tool_call_limit=12,   # the skill's judge-loop budget (same as #24/#26)
    timeout_s=240,
)


# === Gen-AI use-case evals ===================================================
# Ten scenarios covering the media/gen-AI use cases. NONE grades output QUALITY (too
# hard/costly to judge); each grades the layers that prove the pipeline actually ran:
#   - GATED run_model stubs: the accepted URL/answer is only produced when the model
#     fed the right inputs (the actual image/audio/text) into the model call, so a
#     pass proves the data flowed chat -> model, not that run_model merely fired.
#   - DELIVERY: the result must go out via the right media tool (send_image/video/
#     audio) carrying the gated URL — generating without sending fails.
#   - OUTPUT (Q&A shapes): the reply must surface a fact derivable ONLY from the
#     gated stub return (a name on a badge, a total on a receipt).

_TINY_PNG_DATA_URI = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
                      "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _make_media_gate(accepted: str, groups: list[tuple[str, ...]], miss: str,
                     vlm_verdict: str | None = None) -> Callable[[dict], str]:
    """A run_model stub factory: returns `accepted` only when EVERY group has at least
    one token present in the call's args (model + inputs, lowercased) — i.e. the model
    actually passed the required data. Otherwise `miss` (a Replicate-style error the
    model can react to, or a plausible-but-wrong URL the delivery check rejects).

    `vlm_verdict`: for gates whose accepted value is a media URL, a VLM call (the
    photo-edit skill's likeness-judge step) must get a TEXT verdict back — serving it
    the miss-URL reads as a broken image and stalls the run in verify-retry (seen in
    merge_profile_pics run2). Set it to a passing verdict so a skill-following
    verify -> send flow flows through; leave None when the accepted value itself is
    the VLM's text (describe/OCR gates)."""
    def gate(args: dict) -> str:
        model = str(args.get("model") or "")
        if vlm_verdict is not None and (model == _EVAL_VLM or "llava" in model):
            return vlm_verdict
        blob = str(args).lower()
        return accepted if all(any(t in blob for t in g) for g in groups) else miss
    return gate


_MEDIA_VLM_OK = ("The image matches the request: composition, subjects, and details look "
                 "correct and natural. Score: 9/10.")


def _search_media_models(args: dict) -> str:
    """search_models stub for the gen-AI scenarios: a small honest catalog, keyed by
    query intent. (Transcription before TTS — 'speech' appears in both.)"""
    q = str(args.get("query") or "").lower()
    if any(w in q for w in ("transcri", "whisper", "speech-to-text", "stt", "speech recognition")):
        return ("Found (use run_model):\n- openai/whisper: speech-to-text transcription\n"
                "- vaibhavs10/incredibly-fast-whisper: fast audio transcription")
    if "video" in q and any(w in q for w in ("underst", "describe", "summar", "question",
                                             "qa", "analy", "caption", "what", "content")):
        return ("Found (use run_model):\n- lucataco/video-llava: video question answering "
                "(pass video url + prompt)")
    if any(w in q for w in ("vision", "vlm", "llava", "visual", "describe", "ocr", "caption",
                            "image question", "q&a", "qa", "recogni")):
        return f"Found (use run_model):\n- {_EVAL_VLM}: visual question answering on images"
    if any(w in q for w in ("video", "animate", "i2v", "motion")):
        return ("Found (use run_model):\n- kwaivgi/kling-v2.1: image-to-video generation\n"
                "- bytedance/seedance-1-pro: image-to-video")
    if any(w in q for w in ("tts", "text-to-speech", "text to speech", "speech", "voice", "read aloud")):
        return ("Found (use run_model):\n- minimax/speech-02-hd: text-to-speech, natural voices\n"
                "- jaaari/kokoro-82m: fast text-to-speech")
    if any(w in q for w in ("music", "jingle", "song", "melody", "tune")):
        return ("Found (use run_model):\n- meta/musicgen: music from a text prompt\n"
                "- riffusion/riffusion: music generation")
    if any(w in q for w in ("upscale", "enhance", "restore", "deblur", "sharpen", "resolution")):
        return ("Found (use run_model):\n- nightmareai/real-esrgan: image upscaling\n"
                "- philz1337x/clarity-upscaler: enhance and sharpen photos")
    if any(w in q for w in ("edit", "kontext", "inpaint", "overlay", "combine", "merge",
                            "composite", "add text", "text on")):
        return ("Found (use run_model):\n"
                "- openai/gpt-image-2: instruction image editing & multi-image composition (input_images)\n"
                "- qwen/qwen-image-edit-plus: instruction-based image editing")
    return ("Found (use run_model):\n- black-forest-labs/flux-1.1-pro: text-to-image\n"
            "- stability-ai/sdxl: text-to-image")


_MEDIA_SCHEMA = {"input": {"image": "uri", "input_images": "array", "start_image": "uri",
                           "audio": "uri", "prompt": "string", "text": "string"}}

# -- 1. image tagging: who from THIS GROUP appears in the photo? --------------------
# The intersection task: VLM output (names on badges) x the group's member roster. The
# photo shows two members + one non-member; a third member isn't in the photo. Grading
# is set-membership on the reply — pure facts, no quality judgment.
_TRIP_GROUP_JID = "120363099999000001@g.us"
_TRIP_MEMBERS = [
    {"name": "Yossi Cohen", "number": "972540000111"},
    {"name": "Maya Levi", "number": "972540000112"},
    {"name": "Tomer Katz", "number": "972540000113"},
    {"name": "Rina Bar", "number": "972540000114"},
]

_GROUP_PHOTO_VLM = _make_media_gate(
    ("Two people sitting at a table, wearing name badges that read 'Maya Levi' and "
     "'Tomer Katz'. A third, unattended badge on the table reads 'Dana Weiss'."),
    groups=[("data:image",)],
    miss="Replicate error: the vision model received no image input.",
)


def _tags_members_in_photo(reply: str) -> bool:
    r = (reply or "").lower()
    if "maya" not in r or "tomer" not in r:
        return False
    # The member NOT in the photo must not be claimed PRESENT — but correctly noting
    # "Rina isn't in it" is a right answer, so a Rina mention fails only when her
    # sentence carries no negation. (The first version failed any mention, and graded
    # a correct 'Yossi and Rina don't seem to be in it' reply as a FAIL.)
    negations = ("not", "n't", "isn", "doesn", "don", "aren", "wasn", "without",
                 "except", "לא", "אינ", "בלי", "חוץ")
    for sentence in re.split(r"[.!?\n]", r):
        if "rina" in sentence and not any(n in sentence for n in negations):
            return False
    return True


_IMAGE_TAG_GROUP = Scenario(
    name="image_tag_group_members",
    command="who from this group appears in the photo Yossi just sent here?",
    contact_name="Trip Buddies",
    contact_jid=_TRIP_GROUP_JID,
    context=group_chat_context(group_name="Trip Buddies"),
    output_check=_tags_members_in_photo,
    tool_call_limit=10,
    timeout_s=240,
    stubs={
        "find_chats": [{"name": "Trip Buddies", "id": _TRIP_GROUP_JID, "is_group": True}],
        "read_chat": [
            {"sender": "Yossi Cohen", "text": "check this out from the offsite", "from_me": False,
             "media_type": None, "media_url": None},
            {"sender": "Yossi Cohen", "text": "", "from_me": False,
             "media_type": "image", "media_url": _TINY_JPEG_DATA_URI},
        ],
        "get_group_members": _TRIP_MEMBERS,
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        "run_model": _GROUP_PHOTO_VLM,
    },
)

# -- 2. voice transcription in a GROUP, Hebrew, with a decoy clip -------------------
# Variant surface of transcribe_voice_note: group chat, Hebrew content, TWO voice notes
# from different senders — the gate serves each clip its own transcript, so answering
# with Alon's content (the wrong clip) fails the output check.
_BALLGAME_GROUP_JID = "120363099999000002@g.us"


def _group_voice_gate(args: dict) -> str:
    blob = str(args).lower()
    if "guy_voice.ogg" in blob:
        return "המשחק הועבר ליום חמישי בשמונה בערב במגרש של בית הספר, תעדכנו את כולם"
    if "alon_voice.ogg" in blob:
        return "מישהו יכול להביא מים וכדור רזרבי למשחק?"
    return "Replicate error: transcription model received no audio input."


_GROUP_VOICE_NOTE = Scenario(
    name="group_voice_note_hebrew",
    command="מה גיא אמר בהודעה הקולית ששלח כאן?",
    contact_name="כדורגל",
    contact_jid=_BALLGAME_GROUP_JID,
    context=group_chat_context(group_name="כדורגל"),
    output_check=lambda r: "חמישי" in (r or "") and any(w in (r or "") for w in ("שמונה", "20:00", "8")),
    tool_call_limit=10,
    timeout_s=240,
    stubs={
        "find_chats": [{"name": "כדורגל", "id": _BALLGAME_GROUP_JID, "is_group": True}],
        "read_chat": [
            {"sender": "אלון", "text": "", "from_me": False,
             "media_type": "audio", "media_url": "https://media.example/alon_voice.ogg"},
            {"sender": "גיא", "text": "", "from_me": False,
             "media_type": "audio", "media_url": "https://media.example/guy_voice.ogg"},
        ],
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        "run_model": _group_voice_gate,
    },
)

# -- 3. image -> video from a CHAT image (not a profile pic) ------------------------
# profile_pic_to_video covers the profile-pic route; this covers the other prod-reported
# source: an image someone SENT in the chat. Gate: the video model must receive the
# actual image; delivery must be send_video with the gated URL.
_CHAT_VIDEO_URL = "https://replicate.delivery/eval/dana_photo_video.mp4"

_CHAT_IMAGE_TO_VIDEO = Scenario(
    name="chat_image_to_video",
    command="Make a short video from the photo Dana sent here.",
    contact_name="Dana",
    contact_number="972500000001",
    contact_jid="972500000001@s.whatsapp.net",
    expected_tool_calls=["send_video"],
    tool_arg_checks={"send_video": lambda a: _CHAT_VIDEO_URL in str(a)},
    tool_call_limit=10,
    timeout_s=240,
    stubs={
        "find_chats": [{"name": "Dana", "id": "972500000001@s.whatsapp.net", "is_group": False}],
        "read_chat": [
            {"sender": "Dana", "text": "", "from_me": False,
             "media_type": "image", "media_url": _TINY_JPEG_DATA_URI},
        ],
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        "run_model": _make_media_gate(
            _CHAT_VIDEO_URL, groups=[("data:image",)],
            miss="Replicate error: the video model received no start image.",
            vlm_verdict=_MEDIA_VLM_OK),
    },
)

# -- 4. add text ONTO an image ------------------------------------------------------
# We can't judge the rendered pixels; the gate is the calls-layer proxy: the edit call
# must carry BOTH the actual image AND the exact requested text. (True text-rendering
# verification would need OCR on the output — out of scope for now, discussed in review.)
_TEXT_IMG_URL = "https://replicate.delivery/eval/dana_photo_birthday_text.png"

_ADD_TEXT_TO_IMAGE = Scenario(
    name="add_text_to_image",
    command='Take the photo Dana sent here, write "Happy Birthday Maya!" on it, and send it back.',
    contact_name="Dana",
    contact_number="972500000001",
    contact_jid="972500000001@s.whatsapp.net",
    expected_tool_calls=["send_image"],
    tool_arg_checks={"send_image": lambda a: _TEXT_IMG_URL in str(a)},
    tool_call_limit=10,
    timeout_s=240,
    stubs={
        "find_chats": [{"name": "Dana", "id": "972500000001@s.whatsapp.net", "is_group": False}],
        "read_chat": [
            {"sender": "Dana", "text": "", "from_me": False,
             "media_type": "image", "media_url": _TINY_JPEG_DATA_URI},
        ],
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        # Image but wrong/missing text -> wrong URL (delivery check rejects); no image -> error.
        "run_model": _make_media_gate(
            _TEXT_IMG_URL, groups=[("data:image",), ("happy birthday maya",)],
            miss=_WRONG_MODEL_URL, vlm_verdict=_MEDIA_VLM_OK),
    },
)

# -- 5. text message -> VOICE ("read it to me") --------------------------------------
# TTS: the gate requires the MESSAGE'S OWN words in the model call (proving the chat
# text flowed into the TTS input), and the clip must be DELIVERED as playable audio.
_TTS_AUDIO_URL = "https://replicate.delivery/eval/dana_message_tts.mp3"

_MESSAGE_TO_VOICE = Scenario(
    name="message_to_voice_tts",
    command="Read Dana's last message to me out loud — send it here as a voice note.",
    contact_name="Dana",
    contact_number="972500000001",
    contact_jid="972500000001@s.whatsapp.net",
    expected_tool_calls=["send_audio"],
    tool_arg_checks={"send_audio": lambda a: _TTS_AUDIO_URL in str(a)},
    tool_call_limit=10,
    timeout_s=240,
    stubs={
        "find_chats": [{"name": "Dana", "id": "972500000001@s.whatsapp.net", "is_group": False}],
        "read_chat": [
            {"sender": "Dana", "text": "הפגישה מחר עברה לעשר בבוקר במשרד של שרה",
             "from_me": False, "media_type": None, "media_url": None},
        ],
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        "run_model": _make_media_gate(
            _TTS_AUDIO_URL, groups=[("עשר",), ("שרה",)],
            miss="Replicate error: the TTS model received no text input."),
    },
)

# -- 6. upscale/enhance a blurry chat photo ------------------------------------------
_UPSCALED_URL = "https://replicate.delivery/eval/dana_photo_upscaled.png"

_UPSCALE_PHOTO = Scenario(
    name="upscale_blurry_photo",
    command="The photo Dana just sent is really blurry — enhance it and send the improved version back here.",
    contact_name="Dana",
    contact_number="972500000001",
    contact_jid="972500000001@s.whatsapp.net",
    expected_tool_calls=["send_image"],
    tool_arg_checks={"send_image": lambda a: _UPSCALED_URL in str(a)},
    tool_call_limit=10,
    timeout_s=240,
    stubs={
        "find_chats": [{"name": "Dana", "id": "972500000001@s.whatsapp.net", "is_group": False}],
        "read_chat": [
            {"sender": "Dana", "text": "", "from_me": False,
             "media_type": "image", "media_url": _TINY_JPEG_DATA_URI},
        ],
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        "run_model": _make_media_gate(
            _UPSCALED_URL, groups=[("data:image",)],
            miss="Replicate error: the upscaler received no image input.",
            vlm_verdict=_MEDIA_VLM_OK),
    },
)

# -- 7. plain text -> image, instructed in Hebrew ------------------------------------
# The bread-and-butter gen: Hebrew request -> t2i model -> delivered as a real image.
# The gate accepts the subject in either language (translating the prompt is fine),
# so the grade is "the requested SUBJECT reached the model", not prompt style.
_GEN_IMG_URL = "https://replicate.delivery/eval/astronaut_cat.png"

_TEXT_TO_IMAGE = Scenario(
    name="text_to_image_hebrew",
    command="צור תמונה של חתול אסטרונאוט שט על הירח ותשלח לי אותה",
    is_self_dm=True,
    contact_jid="972500000000@s.whatsapp.net",
    expected_tool_calls=["send_image"],
    tool_arg_checks={"send_image": lambda a: _GEN_IMG_URL in str(a)},
    tool_call_limit=8,
    timeout_s=240,
    stubs={
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        "run_model": _make_media_gate(
            _GEN_IMG_URL, groups=[("חתול", "cat"), ("אסטרונאוט", "astronaut")],
            miss=_WRONG_MODEL_URL, vlm_verdict=_MEDIA_VLM_OK),
    },
)

# -- 8. OCR a receipt the USER sent ("how much did I pay?") ---------------------------
# The readable direction of image-text: extract a fact FROM an image. The total exists
# only in the gated VLM return, so the reply containing it proves image -> VLM -> answer.
_RECEIPT_OCR = Scenario(
    name="receipt_ocr_total",
    command="כמה שילמתי בסך הכל בקבלה ששלחתי כאן עכשיו?",
    is_self_dm=True,
    contact_jid="972500000000@s.whatsapp.net",
    output_check=lambda r: "487" in (r or ""),
    tool_call_limit=10,
    timeout_s=240,
    stubs={
        "read_chat": [
            {"sender": "Alex", "text": "", "from_me": True,
             "media_type": "image", "media_url": _TINY_JPEG_DATA_URI},
        ],
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        "run_model": _make_media_gate(
            ("Receipt from Super-Pharm, yesterday 18:42. Items: shampoo 24.90, "
             "vitamins 89.50, ... TOTAL: 487.30 ILS, paid by Visa ****1234."),
            groups=[("data:image",)],
            miss="Replicate error: the vision model received no image input."),
    },
)

# -- 9. music generation: a birthday jingle, delivered as audio ----------------------
_JINGLE_URL = "https://replicate.delivery/eval/maya_birthday_jingle.mp3"

_BIRTHDAY_JINGLE = Scenario(
    name="birthday_jingle_gen",
    command="Make a short happy-birthday jingle for my friend Maya and send it to me here.",
    is_self_dm=True,
    contact_jid="972500000000@s.whatsapp.net",
    expected_tool_calls=["send_audio"],
    tool_arg_checks={"send_audio": lambda a: _JINGLE_URL in str(a)},
    tool_call_limit=8,
    timeout_s=240,
    stubs={
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        "run_model": _make_media_gate(
            _JINGLE_URL, groups=[("birthday",)],
            miss="Replicate error: the music model received no prompt."),
    },
)

# -- 10. compose TWO people's pictures into one image ---------------------------------
# Multi-input composition: the gate demands BOTH source images (the owner's JPEG and
# Dana's PNG — distinguishable data URIs) in ONE model call, so fetching just one (or
# faking it text-to-image) can't pass.
_MERGED_URL = "https://replicate.delivery/eval/ido_and_dana_together.png"


def _pic_by_phone(args: dict) -> str:
    phone = re.sub(r"\D", "", str(args.get("phone") or ""))
    if phone.endswith("500000001"):
        return _TINY_PNG_DATA_URI          # Dana
    return _TINY_JPEG_DATA_URI             # the owner ('me' or their number)


_MERGE_PROFILE_PICS = Scenario(
    name="merge_profile_pics",
    command="Make one picture of me and Dana together and send it here.",
    contact_name="Dana",
    contact_number="972500000001",
    contact_jid="972500000001@s.whatsapp.net",
    expected_tool_calls=["send_image"],
    tool_arg_checks={"send_image": lambda a: _MERGED_URL in str(a)},
    tool_call_limit=10,
    timeout_s=240,
    stubs={
        "find_chats": [{"name": "Dana", "id": "972500000001@s.whatsapp.net", "is_group": False}],
        "get_profile_picture": _pic_by_phone,
        "get_user_details": {"name": "Dana"},
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        "run_model": _make_media_gate(
            _MERGED_URL, groups=[("data:image/jpeg",), ("data:image/png",)],
            miss=_WRONG_MODEL_URL, vlm_verdict=_MEDIA_VLM_OK),
    },
)


# -- 11. the judge-REJECTS flow: Mai must obey its own VLM verdict -------------------
# The photo-edit skill's likeness-judge (06537d7) has only ever been evaled with a
# canned PASSING verdict — proving Mai runs the check, never that the check DOES
# anything. Here the verdict depends on WHICH candidate the VLM is shown (stateless —
# no call-order bookkeeping): the edit returns two candidates; A judges 3/10, B judges
# 9/10. Pass iff the APPROVED candidate is what goes out and the rejected one never
# does. A run that skips judging fails naturally — the rejected candidate is FIRST.
_ADI_NUMBER = "972540000099"
_ADI_REJECTED_URL = "https://replicate.delivery/eval/adi_candidate_a.png"
_ADI_APPROVED_URL = "https://replicate.delivery/eval/adi_candidate_b.png"


def _two_candidate_edit(args: dict) -> Any:
    model = str(args.get("model") or "")
    blob = str(args)
    if model == _EVAL_VLM or "llava" in model:
        if _ADI_REJECTED_URL in blob:
            return "Different person — the face shape and age changed completely. Score: 3/10."
        if _ADI_APPROVED_URL in blob:
            return "Same person, sunglasses added, face/age/gender preserved. Score: 9/10."
        return "I can't see a valid image to judge."
    if "data:image" in blob.lower():
        return [_ADI_REJECTED_URL, _ADI_APPROVED_URL]   # two candidates, bad one first
    return "Replicate error: the edit model received no image input."


def _no_rejected_candidate_sent(sends: list[dict]) -> bool:
    return not any(_ADI_REJECTED_URL in str(s) for s in sends)


_JUDGE_REJECTS = Scenario(
    name="judge_rejects_bad_candidate",
    command="שימי משקפי שמש על תמונת הפרופיל של דנה ותשלחי לי",
    contact_name="דנה",
    contact_number=_ADI_NUMBER,
    contact_jid=f"{_ADI_NUMBER}@s.whatsapp.net",
    expected_tool_calls=["send_image"],
    tool_arg_checks={"send_image": lambda a: _ADI_APPROVED_URL in str(a)
                     and _ADI_REJECTED_URL not in str(a)},
    sends_check=_no_rejected_candidate_sent,
    tool_call_limit=12,
    timeout_s=240,
    stubs={
        "find_chats": [{"name": "דנה", "id": f"{_ADI_NUMBER}@s.whatsapp.net", "is_group": False}],
        "get_user_details": {"name": "דנה"},
        "get_profile_picture": _TINY_JPEG_DATA_URI,
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        "run_model": _two_candidate_edit,
    },
)


# === Gen-AI x product combinations, multi-target ====================================
# Second wave: each scenario crosses gen-AI with another subsystem (schedules, group
# delivery, memory) and involves MULTIPLE contacts — several targets where the tools
# allow it (schedules carry a target each; media tools are current-chat-only, so
# multi-person delivery happens by being tagged in the GROUP that holds the targets),
# and decoy senders/media everywhere else so picking the right source is graded.

# A miss-URL for gates where the model may legitimately READ the url it gets back:
# _WRONG_MODEL_URL literally says "wrong_model_output", which leaks the eval's verdict
# into the model's context (a birthday_invite run read it as a technical failure and
# spiraled into retries). This one is neutral — the delivery check still rejects it.
_GEN_MISS_URL = "https://replicate.delivery/eval/out_7f3a91.png"


def _padded_jpeg_data_uri(pad: int = 12000) -> str:
    """A byte-realistic fake photo: the tiny 1x1 JPEG with a JPEG COM (comment) segment
    of `pad` bytes spliced in after SOI — still a VALID jpeg, but its base64 length now
    reads like a real compressed photo. Needed because the model (reasonably!) inspects
    what it's sent: a menu_photo run saw the ~130-byte original and refused to trust the
    VLM's reading ('the image is too small to contain a menu')."""
    import base64
    raw = base64.b64decode(_TINY_JPEG_DATA_URI.split(",", 1)[1])
    com = b"\xff\xfe" + (pad + 2).to_bytes(2, "big") + b"E" * pad
    return "data:image/jpeg;base64," + base64.b64encode(raw[:2] + com + raw[2:]).decode()


# -- 12. scheduled generative content -> TWO groups (one schedule row per target) -----
_FAMILY2_JID = "120363088888000001@g.us"
_NEIGHBORS_JID = "120363088888000002@g.us"


def _two_friday_image_schedules(rows: list[ScheduleSnapshot]) -> bool:
    """Both groups must each get an ACTIVE, RECURRING, DYNAMIC Friday-8am schedule —
    a Schedule row carries ONE target_chat, so 'send to both groups' persists as two
    rows. A single row (or one aimed at the creator's DM) fails."""
    targets: set[str] = set()
    for s in rows:
        if not s.active or s.schedule_type.lower() != "recurring" or s.kind != "dynamic":
            continue
        parts = (s.cron or "").split()
        if len(parts) != 5:
            continue
        minute, hour, _dom, _month, dow = parts
        if minute in ("0", "00") and hour in ("8", "08") and dow.lower() in ("5", "fri"):
            targets.add(s.target_chat or "")
    return {_FAMILY2_JID, _NEIGHBORS_JID} <= targets


# (the schedule_genimage_two_groups scenario itself is a ConvScenario — defined in the
# conversation section below, where ConvScenario/_snap_schedules exist)

# -- 13. TTS announcement INTO a group (the multi-person delivery surface) ------------
_CLOSE_FRIENDS_JID = "120363077777000001@g.us"
_LATE_TTS_URL = "https://replicate.delivery/eval/late_announcement_tts.mp3"


def _audio_reached_group(sends: list[dict]) -> bool:
    return any(s.get("kind") == "audio" and _LATE_TTS_URL in str(s)
               and "120363077777000001" in str(s.get("to") or "") for s in sends)


_GROUP_TTS_ANNOUNCEMENT = Scenario(
    name="group_tts_announcement",
    command="תשלחי כאן הודעה קולית שאומרת שאני מאחר בעשרים דקות לארוחת הערב",
    contact_name="החברים",
    contact_jid=_CLOSE_FRIENDS_JID,
    context=group_chat_context(group_name="החברים"),
    expected_tool_calls=["send_audio"],
    tool_arg_checks={"send_audio": lambda a: _LATE_TTS_URL in str(a)},
    sends_check=_audio_reached_group,
    tool_call_limit=10,
    timeout_s=240,
    stubs={
        "find_chats": [{"name": "החברים", "id": _CLOSE_FRIENDS_JID, "is_group": True}],
        "get_group_members": [
            {"name": "Dana", "number": "972500000001"},
            {"name": "Tom", "number": "972500000002"},
            {"name": "Danny", "number": "972539876543"},
        ],
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        "run_model": _make_media_gate(
            _LATE_TTS_URL, groups=[("עשרים", "20", "twenty"), ("מאחר", "late")],
            miss="Replicate error: the TTS model received no text input."),
    },
)

# -- 14. voice-note TRANSLATION, with a decoy speaker ---------------------------------
_EUROTRIP_JID = "120363066666000001@g.us"


def _foreign_voice_gate(args: dict) -> str:
    blob = str(args).lower()
    if "claire_voice.ogg" in blob:
        return "On se retrouve demain à 15h à la gare centrale, j'apporte les billets de train."
    if "marco_voice.ogg" in blob:
        return "Ci vediamo stasera alle otto in piazza."   # the decoy clip
    return "Replicate error: transcription model received no audio input."


_VOICE_TRANSLATE = Scenario(
    name="voice_note_translate",
    command="מה קלייר אמרה בהודעה הקולית ששלחה כאן? תסבירי לי בעברית",
    contact_name="טיול",
    contact_jid=_EUROTRIP_JID,
    context=group_chat_context(group_name="טיול"),
    # Facts-only: tomorrow-15:00 at the train station / tickets — derivable ONLY from
    # Claire's French clip. Answering from Marco's Italian decoy (tonight, 8, piazza)
    # fails. Translation QUALITY isn't judged; surfacing the right facts in Hebrew is.
    output_check=lambda r: (("15" in (r or "")) or ("שלוש" in (r or ""))) and
                           any(k in (r or "") for k in ("תחנ", "רכבת", "כרטיס")),
    tool_call_limit=10,
    timeout_s=240,
    stubs={
        "find_chats": [{"name": "טיול", "id": _EUROTRIP_JID, "is_group": True}],
        "read_chat": [
            {"sender": "מרקו", "text": "", "from_me": False,
             "media_type": "audio", "media_url": "https://media.example/marco_voice.ogg"},
            {"sender": "קלייר", "text": "", "from_me": False,
             "media_type": "audio", "media_url": "https://media.example/claire_voice.ogg"},
        ],
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        "run_model": _foreign_voice_gate,
    },
)

# -- 15. translate text IN an image (menu), two senders' photos in the chat -----------
_JAPAN_TRIP_JID = "120363066666000002@g.us"


def _japan_photos_gate(args: dict) -> str:
    blob = str(args).lower()
    if "data:image/jpeg" in blob:   # Maya's menu photo
        return ("Japanese menu (translated): Tonkotsu Ramen — ¥1,200; Gyoza — ¥450; "
                "Green tea — ¥300.")
    if "data:image/png" in blob:    # Tomer's street-sign photo (decoy)
        return "Street sign: 'Shibuya Station 300m ahead'."
    return "Replicate error: the vision model received no image input."


_MENU_TRANSLATE = Scenario(
    name="menu_photo_translate",
    command="מה כתוב בתפריט שמאיה שלחה כאן? כמה עולה הראמן?",
    contact_name="טיול יפן",
    contact_jid=_JAPAN_TRIP_JID,
    context=group_chat_context(group_name="טיול יפן"),
    output_check=lambda r: any(t in (r or "") for t in ("1,200", "1200", "1 200")),
    tool_call_limit=10,
    timeout_s=240,
    stubs={
        "find_chats": [{"name": "טיול יפן", "id": _JAPAN_TRIP_JID, "is_group": True}],
        "read_chat": [
            {"sender": "תומר", "text": "", "from_me": False,
             "media_type": "image", "media_url": _TINY_PNG_DATA_URI},
            # padded to a realistic byte size: a 130-byte "menu photo" made the model
            # (rightly) distrust the VLM's reading and withhold the answer.
            {"sender": "מאיה", "text": "", "from_me": False,
             "media_type": "image", "media_url": _padded_jpeg_data_uri()},
        ],
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        "run_model": _japan_photos_gate,
    },
)

# -- 16. video UNDERSTANDING (we generate videos elsewhere; here we READ one) ---------
_HAVERE_JID = "120363066666000003@g.us"


def _videos_gate(args: dict) -> str:
    blob = str(args).lower()
    if "tomer_trip.mp4" in blob:
        return "A person paragliding over a beach at sunset, then landing next to a red van."
    if "dana_cake.mp4" in blob:
        return "Someone decorating a chocolate birthday cake with strawberries."  # decoy
    return "Replicate error: the video model received no video input."


_VIDEO_QA = Scenario(
    name="video_qa_group",
    command="מה רואים בסרטון שתומר שלח כאן?",
    contact_name="החבר'ה",
    contact_jid=_HAVERE_JID,
    context=group_chat_context(group_name="החבר'ה"),
    # incl. the Hebrew TRANSLITERATION of paragliding ("פראגליידינג") — a correct run1
    # reply used it and the first token set missed it.
    output_check=lambda r: any(k in (r or "").lower()
                               for k in ("רחיפ", "מצנח", "paraglid", "פראגל", "גלייד")),
    tool_call_limit=10,
    timeout_s=240,
    stubs={
        "find_chats": [{"name": "החבר'ה", "id": _HAVERE_JID, "is_group": True}],
        "read_chat": [
            {"sender": "Dana", "text": "", "from_me": False,
             "media_type": "video", "media_url": "https://media.example/dana_cake.mp4"},
            {"sender": "תומר", "text": "", "from_me": False,
             "media_type": "video", "media_url": "https://media.example/tomer_trip.mp4"},
        ],
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        "run_model": _videos_gate,
    },
)

# -- 17. invitation with HARD DETAILS, delivered to the group of invitees -------------
_SURPRISE_JID = "120363066666000004@g.us"
_INVITE_URL = "https://replicate.delivery/eval/maya_birthday_invite.png"


def _invite_reached_group(sends: list[dict]) -> bool:
    return any(s.get("kind") == "image" and _INVITE_URL in str(s)
               and "120363066666000004" in str(s.get("to") or "") for s in sends)


_BIRTHDAY_INVITE = Scenario(
    name="birthday_invite_group",
    command=("תכיני הזמנה מעוצבת ליום ההולדת של מאיה — יום שישי בשבע בערב אצלנו — "
             "ותשלחי אותה כאן לקבוצה"),
    contact_name="ההפתעה של מאיה",
    contact_jid=_SURPRISE_JID,
    context=group_chat_context(group_name="ההפתעה של מאיה"),
    # The real failure mode of invitation gen is DROPPED DETAILS, not bad design: the
    # gate demands all three (who / when-day / when-hour) in the generation prompt.
    expected_tool_calls=["send_image"],
    tool_arg_checks={"send_image": lambda a: _INVITE_URL in str(a)},
    sends_check=_invite_reached_group,
    tool_call_limit=10,
    timeout_s=240,
    stubs={
        "find_chats": [{"name": "ההפתעה של מאיה", "id": _SURPRISE_JID, "is_group": True}],
        "get_group_members": [
            {"name": "Tomer Katz", "number": "972540000113"},
            {"name": "Rina Bar", "number": "972540000114"},
            {"name": "Yossi Cohen", "number": "972540000111"},
        ],
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        "run_model": _make_media_gate(
            _INVITE_URL,
            groups=[("מאיה", "maya"), ("שישי", "friday"), ("שבע", "seven", "7", "19:")],
            miss=_GEN_MISS_URL, vlm_verdict=_MEDIA_VLM_OK),
    },
)


GENAI_SCENARIOS: list[Scenario] = [
    _IMAGE_TAG_GROUP, _GROUP_VOICE_NOTE, _CHAT_IMAGE_TO_VIDEO, _ADD_TEXT_TO_IMAGE,
    _MESSAGE_TO_VOICE, _UPSCALE_PHOTO, _TEXT_TO_IMAGE, _RECEIPT_OCR,
    _BIRTHDAY_JINGLE, _MERGE_PROFILE_PICS, _JUDGE_REJECTS,
    _GROUP_TTS_ANNOUNCEMENT, _VOICE_TRANSLATE,
    _MENU_TRANSLATE, _VIDEO_QA, _BIRTHDAY_INVITE,
]

# === Knowledge base (Drive docs) activation =================================
# The user refers to a DOCUMENT without naming a storage backend — "the document
# about X", "it's in the docs", "it's in my drive", "my notes under X" all mean the
# SAME thing: go through the docs tools (the skill owns the specifics). Graded
# source-agnostically on the docs tools + the answer/persisted KB state, plus one
# negative so a plain memory fact doesn't over-trigger the KB.

_DOCS_TOOLS = ["list_documents", "read_document", "save_document", "delete_document"]

_KB_DOC_REFERENCE = Scenario(
    name="kb_doc_reference",
    command=("What's the landlord's phone number? It's in the document about the "
             "apartment hunt."),
    is_self_dm=True,
    docs={"apartment hunt": ("# Apartment hunt\n- 3br in Florentin, 8200/mo\n"
                             "- landlord: Yossi 054-1234567\n- viewing Sunday 18:00")},
    expected_tool_calls=["read_document"],
    output_check=lambda reply: "054-1234567" in reply,
)

_KB_GENERIC_DOCS = Scenario(
    name="kb_generic_docs_phrasing",
    command="When is the ryokan booked for? it's in the docs",
    is_self_dm=True,
    docs={"trip to japan": ("# Trip to Japan\nFlights: TLV->NRT Nov 9.\n"
                            "Ryokan in Hakone booked for Nov 12, two nights.")},
    expected_tool_calls=["read_document"],
    output_check=lambda reply: "12" in reply and "nov" in reply.lower(),
)

_KB_DRIVE_PHRASING = Scenario(
    name="kb_drive_phrasing",
    command="Which books did I put on my reading list? it's in my drive",
    is_self_dm=True,
    docs={"reading list": "# Reading list\n- Dune\n- Blindsight"},
    expected_tool_calls=["read_document"],
    output_check=lambda reply: "dune" in reply.lower() and "blindsight" in reply.lower(),
)

_KB_NOTES_APPEND = Scenario(
    name="kb_notes_append_under",
    command="add tahini to my notes under groceries",
    is_self_dm=True,
    docs={"groceries": "# Groceries\n- milk\n- eggs"},
    # Edit-safely: read the current text, save the WHOLE updated doc (skill rule).
    expected_tool_calls=["read_document", "save_document"],
    # Graded on the KB's final persisted state: the old items must SURVIVE the append —
    # a save that replaced the doc with just "tahini" fails even though save fired.
    docs_check=lambda files: (lambda g: "tahini" in g and "milk" in g and "eggs" in g)(
        files.get("groceries", "").lower()),
)

_KB_NO_OVERTRIGGER = Scenario(
    name="kb_no_overtrigger_memory_fact",
    command="remember that I take my coffee black",
    is_self_dm=True,
    docs={},  # Drive connected and empty — availability must not be the discriminator
    forbidden_tool_calls=_DOCS_TOOLS,  # a one-line fact is MEMORY, never a document
)

KB_SCENARIOS: list[Scenario] = [_KB_DOC_REFERENCE, _KB_GENERIC_DOCS, _KB_DRIVE_PHRASING,
                                _KB_NOTES_APPEND, _KB_NO_OVERTRIGGER]

SCENARIOS: list[Scenario] = [_VIDEO_HANDOFF, _FIFA_DAILY_SUMMARY, _CHAT_SUMMARY, _ONEOFF_REMINDER,
                             _WEEKLY_WEEKDAY, _HEBREW_DAILY_SUMMARY, _VOICE_NOTE, _CONV_SEARCH,
                             _REPORT_23_CROSS_SCRIPT, _REPORT_24_SKILL_FIRST,
                             _REPORT_26_NO_RAW_BASE64, _REPORT_33_B64_RETURN,
                             *GENAI_SCENARIOS, *KB_SCENARIOS]


# === Execution scenarios ====================================================
# These FIRE a dynamic schedule through the real execution path and grade the delivered
# MESSAGE'S CONTENT (not tool calls) — testing what the schedule produces when it runs.
@dataclass
class ExecutionScenario:
    name: str
    action_text: str                       # the dynamic instruction the schedule stores
    output_check: Callable[[str], bool]     # verdict over the delivered message
    kind: str = "dynamic"
    cron: str = "0 8 * * *"

    def run(self, model, **kw):
        from evals.harness import run_execution_scenario
        return run_execution_scenario(self, model)


def _mentions_today(message: str) -> bool:
    """The fired output must reference TODAY's real date — proving the agent used the
    injected current date (system prompt's now-line), not a hallucinated/stale one.
    Requires the current day, month name, and year all present (tz = the eval user's)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Jerusalem"))
    low = message.lower()
    return (str(now.day) in message and str(now.year) in message
            and now.strftime("%B").lower() in low)


_SCHEDULE_DATE_CHECK = ExecutionScenario(
    name="schedule_exec_date_correct",
    action_text="In one short sentence, tell me today's full date (day of week, day, month, year).",
    output_check=_mentions_today,
)


EXECUTION_SCENARIOS: list[ExecutionScenario] = [_SCHEDULE_DATE_CHECK]


# === Conversation scenarios (MULTI-TURN) ====================================
# Every multi-turn eval is the SAME shape: drive `turns` on the full production surface (real agno
# history threading), snapshot the REAL persisted/delivered state after each turn, then grade it.
# Schedule-lifecycle, memory, error-report and onboarding differ ONLY in (a) the per-turn snapshot
# fn and (b) the grader — so they are INSTANCES of one ConvScenario, not four types + four runners.
@dataclass
class ConvScenario:
    name: str
    turns: list                                      # str OR (message, invoke_id) per turn
    snapshot: Callable[[Any, Any, Any], Any] | None  # (session, user, agno_db) -> per-turn state, or None
    grade: Callable[[list, list], bool]              # (raw per-turn snapshots, per-turn records) -> pass
    state_checked: bool = False                      # True => grade is the persisted-state axis
    log_key: str | None = None                       # key under which to log the serialized snapshot
    expected_tool_calls: list[str] = field(default_factory=list)  # shown in the log (not gated)
    # --- cross-chat / contact-resolution scenarios (P5) ---------------------------------------
    chats: list = field(default_factory=list)        # extra provider chats (contacts/groups) Mai can find/resolve
    current_chat: str = ""                            # JID Mai is tagged in ("" => the owner's own DM)
    context: str = ""                                 # trusted WHATSAPP CONTEXT block prepended to turn 1
    # --- gen-AI conversation scenarios ---------------------------------------------------------
    stubs: dict = field(default_factory=dict)        # SDK/tool stubs (same contract as Scenario.stubs)
    messages: dict = field(default_factory=dict)     # provider canned messages: jid -> [msg dicts]
    # --- gen-AI conversation scenarios ---------------------------------------------------------
    stubs: dict = field(default_factory=dict)        # SDK/tool stubs (same contract as Scenario.stubs)
    messages: dict = field(default_factory=dict)     # provider canned messages: jid -> [msg dicts]

    def run(self, model, **kw):
        from evals.harness import run_conv_scenario
        return run_conv_scenario(self, model)


# -- error-report: the two-phase preview -> approve -> persist gate -----------
def _snap_reports(session: Any, user: Any, agno_db: Any) -> list[ErrorReportSnapshot]:
    return _read_error_reports(session, user)


def _gate_then_file(by_turn: list[list[ErrorReportSnapshot]], _records: list) -> bool:
    """SOME turn before the last STAGES a preview (>=1 pending, 0 open) — the gate held, nothing
    captured silently — and the FINAL turn (approval in a new message) FILES it (>=1 open)."""
    if len(by_turn) < 2:
        return False
    staged_without_filing = any(
        any(r.status == "pending" for r in t) and not any(r.status == "open" for r in t)
        for t in by_turn[:-1])
    return staged_without_filing and any(r.status == "open" for r in by_turn[-1])


_ERROR_REPORT_FLOW = ConvScenario(
    name="error_report_two_phase",
    turns=[
        # prior context (so the captured transcript is real); benign -> no tool, no misfire.
        ("Hey Mai, thanks for your help this week — you've been great!", "m1"),
        # the report -> must STAGE a pending (file nothing)
        ("Earlier you set my standup reminder for 9pm instead of 9am — that's a bug. "
         "Please report this problem to the team.", "m2"),
        ("yes, file it", "m3"),   # approval in a NEW message -> must FILE it
    ],
    snapshot=_snap_reports, grade=_gate_then_file, log_key="reports_after",
    expected_tool_calls=["report_problem"],
)

# Same flow in Hebrew — language-agnostic gate (graded on the persisted rows).
_ERROR_REPORT_FLOW_HE = ConvScenario(
    name="error_report_two_phase_he",
    turns=[
        ("היי נובה, תודה על כל העזרה השבוע — היית מעולה!", "m1"),
        ("קודם קבעת לי את תזכורת הסטנדאפ ל-21:00 במקום 09:00 — זו תקלה. "
         "אנא דווח על הבעיה לצוות.", "m2"),
        ("כן, תגיש את זה", "m3"),
    ],
    snapshot=_snap_reports, grade=_gate_then_file, log_key="reports_after",
    expected_tool_calls=["report_problem"],
)


# -- memory: SAVE a fact, then RECALL it -------------------------------------
def _snap_memories(session: Any, user: Any, agno_db: Any) -> list[str]:
    return _read_memories(agno_db, user.agno_user_id)


def _saved_then_recalled(by_turn: list[list[str]], records: list) -> bool:
    """After the SAVE turn a memory about the allergy is persisted (real store), and the RECALL
    turn's reply surfaces it. Both halves: store it, then use it."""
    if len(by_turn) < 2:
        return False
    saved = any("peanut" in m.lower() or "allerg" in m.lower() for m in by_turn[0])
    return saved and "peanut" in (records[1]["reply"] or "").lower()


_MEMORY_SAVE_RECALL = ConvScenario(
    name="memory_save_and_recall",
    turns=["Remember that I'm allergic to peanuts.", "Quick — what am I allergic to?"],
    snapshot=_snap_memories, grade=_saved_then_recalled, log_key="memories_after",
    expected_tool_calls=["remember"],
)


# -- schedule lifecycle: create, then edit/cancel a follow-up ----------------
def _snap_schedules(session: Any, user: Any, agno_db: Any) -> list[ScheduleSnapshot]:
    return _read_schedules(session, user)


def _modified_in_place_to_9am(by_turn: list[list[ScheduleSnapshot]], _records: list) -> bool:
    """After the follow-up there is EXACTLY ONE active schedule, firing daily at 09:00. 'Exactly one'
    is the crux: edit-in-place OR cancel+recreate both end at one active row; creating a SECOND
    schedule (the failure) ends at two and fails. The remaining row must be the moved (9am) one."""
    if len(by_turn) < 2:
        return False
    active = [s for s in by_turn[-1] if s.active]
    if len(active) != 1 or active[0].schedule_type.lower() != "recurring":
        return False
    parts = (active[0].cron or "").split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts
    every_day = (dom in ("*", "*/1") and month in ("*", "*/1")
                 and dow in ("*", "0-6", "0-7", "*/1", "1-7"))
    return minute in ("0", "00") and hour in ("9", "09") and every_day


def _created_then_cancelled(by_turn: list[list[ScheduleSnapshot]], _records: list) -> bool:
    """Turn 1 CREATED an active schedule (>=1 active), and after the cancel follow-up NONE remain
    active — it existed, then was truly deactivated (not that the model just never made one)."""
    if len(by_turn) < 2:
        return False
    return any(s.active for s in by_turn[0]) and not any(s.active for s in by_turn[-1])


_FIFA_8AM = "Every day at 8am, send me a summary of that day's FIFA World Cup results."
_SCHEDULE_MODIFY = ConvScenario(
    name="schedule_modify", turns=[_FIFA_8AM, "Actually, change that to 9am instead."],
    snapshot=_snap_schedules, grade=_modified_in_place_to_9am, state_checked=True,
    log_key="schedules_after", expected_tool_calls=["create_schedule"],
)
_SCHEDULE_CANCEL = ConvScenario(
    name="schedule_cancel", turns=[_FIFA_8AM, "On second thought, cancel that reminder."],
    snapshot=_snap_schedules, grade=_created_then_cancelled, state_checked=True,
    log_key="schedules_after", expected_tool_calls=["create_schedule"],
)


# -- onboarding: the proof-of-life first contact -----------------------------
# Distilled from a real clunky transcript where Mai demanded a phone number and fumbled — when it
# could just message the user's OWN number (we already have it). The proof is one objective thing:
# did Mai send a WhatsApp message to that number? Graded on the real send transport — no judge.
# Three turns, like schedule_target_group: the system prompt's rule 4 makes "send me something"
# a confirm-first write for many runs ("here's what I'll send — go ahead?"), which is CORRECT
# behavior; the closing "yes" covers both styles (eager send at turn 2, confirm-then-send at 3).
OWNER_NUMBER = "972500000000"  # the eval user's own WhatsApp number (harness._make_sqlite_user)


def _onboarding_proof(_snapshots: list, records: list) -> bool:
    return any(OWNER_NUMBER in line for r in records for line in r.get("sent", []))


_ONBOARDING_PROOF = ConvScenario(
    name="onboarding_proof_of_life",
    turns=[
        "hey mai, will you actually reply from my whatsapp?",
        "go on then — send me something on whatsapp to prove it.",
        "yes, go ahead — send it.",
    ],
    snapshot=None, grade=_onboarding_proof, expected_tool_calls=["send_whatsapp_message"],
)


# -- schedule fires into a NAMED group (confirm-at-creation) ---------------------------------
# Spec: schedules are confirmed ONCE, at creation, and carry WHERE they fire. Graded on the
# persisted Schedule row (target_chat = the group's jid) — the source of truth — plus the reply
# naming the group (the creation-time confirmation the user can veto).
_FAMILY_GROUP = {"name": "Family", "jid": "120363000000000777@g.us", "is_group": True}


def _schedule_targets_family(by_turn: list, records: list) -> bool:
    active = [s for s in (by_turn[-1] or []) if s.active]
    if len(active) != 1 or active[0].target_chat != _FAMILY_GROUP["jid"]:
        return False
    # The destination must be named to the user at creation AND survive into the LIST —
    # a schedule list that drops where each reminder fires is the failure this eval pins.
    named_at_creation = any("family" in (r["reply"] or "").lower() for r in records[:-1])
    return named_at_creation and "family" in (records[-1]["reply"] or "").lower()


_SCHEDULE_TARGET_GROUP = ConvScenario(
    name="schedule_target_group",
    turns=[
        "Every day at 8am, send a good-morning message to the Family group.",
        "yes, go ahead.",  # covers both model styles: one-shot create OR confirm-then-create
        "What reminders do I have?",
    ],
    chats=[_FAMILY_GROUP],
    snapshot=_snap_schedules, grade=_schedule_targets_family, state_checked=True,
    log_key="schedules_after", expected_tool_calls=["create_schedule"],
)


# -- contact resolution + safe cross-chat send (the "similar number" bug) ------------------
# A small directory the provider serves so lookup_contact / resolve have real people to work with.
# We grade on provider.sends (the REAL send transport) and the reply — the numbers are the source
# of truth, never the model's phrasing.
_DANA = {"name": "Dana", "jid": "972501234567@s.whatsapp.net", "is_group": False}
_DANNY = {"name": "Danny", "jid": "972539876543@s.whatsapp.net", "is_group": False}
_DANA_A = {"name": "Dana", "jid": "972501111111@s.whatsapp.net", "is_group": False}
_DANA_B = {"name": "Dana", "jid": "972502222222@s.whatsapp.net", "is_group": False}


def _reply_of(records: list, turn: int = -1) -> str:
    return (records[turn]["reply"] or "").lower() if records else ""


def _sent_lines(records: list) -> list[str]:
    return [line for r in records for line in r.get("sent", [])]


# E1 — number -> name lookup: "who is 0501234567?" must resolve to Dana (bidirectional search).
# Was impossible before (name-only resolve); lookup_contact + numbers.py make it work.
def _named_dana(_snaps: list, records: list) -> bool:
    return "dana" in _reply_of(records)


_LOOKUP_BY_NUMBER = ConvScenario(
    name="contact_lookup_by_number",
    turns=["Who is the contact with the number 0501234567? Just tell me their name."],
    chats=[_DANA, _DANNY],
    snapshot=None, grade=_named_dana,
    expected_tool_calls=["lookup_contact"],
)


# E2 — ambiguity must REFUSE, never silent-send: two contacts named Dana, "message Dana" must send
# NOTHING (ask which one) rather than silently taking the first — the wrong-recipient bug's core.
def _no_send_on_ambiguous(_snaps: list, records: list) -> bool:
    # Graded on the real send transport: with two Danas, nothing went to EITHER.
    return not _sent_lines(records)


_AMBIGUOUS_NAME_ASKS = ConvScenario(
    name="contact_ambiguous_name_asks",
    turns=["Message Dana: 'running 10 minutes late'."],
    chats=[_DANA_A, _DANA_B],
    snapshot=None, grade=_no_send_on_ambiguous,
    expected_tool_calls=["lookup_contact"],
)


# E3 — cross-chat send from a GROUP must CONFIRM the recipient before sending (two-phase, like the
# error-report gate). Mai is tagged in a group; Dana is a DIFFERENT private chat. Turn 1 must NOT
# send to Dana; the final turn, after an explicit yes, DOES. Measures whether confirm-before-send
# holds (prompt-only today — expected to be the acceptance test the hard send-gate turns green).
def _confirmed_before_cross_chat_send(_snaps: list, records: list) -> bool:
    def to_dana(r: dict) -> bool:
        return any("972501234567" in line for line in r.get("sent", []))
    if len(records) < 2:
        return False
    nothing_sent_before_yes = not any(to_dana(r) for r in records[:-1])
    return nothing_sent_before_yes and to_dana(records[-1])


_CROSS_CHAT_CONFIRM = ConvScenario(
    name="cross_chat_send_confirms",
    turns=[
        "Send Dana a message: 'can we push our call to 4pm?'",
        "yes that's her — go ahead and send it.",
    ],
    chats=[_DANA],
    current_chat="120363000000000001@g.us",
    context=group_chat_context(group_name="Team Standup"),
    snapshot=None, grade=_confirmed_before_cross_chat_send,
    expected_tool_calls=["send_whatsapp_message"],
)


# -- gen-AI x schedules: generated content on a schedule, ONE ROW PER TARGET group ----
# Two turns (schedule_target_group pattern): the model may legitimately resolve the two
# groups and CONFIRM before creating ("אלו שתי הקבוצות הנכונות?") — the closing "yes"
# covers both styles. Graded on the REAL persisted rows after the last turn.
def _two_friday_schedules_conv(by_turn: list, _records: list) -> bool:
    return _two_friday_image_schedules(by_turn[-1] or [])


_GENIMAGE_GROUPS_DIR = [
    {"name": "המשפחה", "jid": _FAMILY2_JID, "is_group": True},
    {"name": "השכונה", "jid": _NEIGHBORS_JID, "is_group": True},
    {"name": "עבודה", "jid": "120363088888000009@g.us", "is_group": True},          # decoy
    {"name": "Dana", "jid": "972500000001@s.whatsapp.net", "is_group": False},      # decoy
]

_SCHEDULE_GENIMAGE_GROUPS = ConvScenario(
    name="schedule_genimage_two_groups",
    turns=[
        ("כל יום שישי ב-8 בבוקר תיצרי תמונת 'שבת שלום' חדשה ותשלחי אותה "
         "גם לקבוצת המשפחה וגם לקבוצת השכונה שלי."),
        "כן, אלו הקבוצות — תפעילי.",
    ],
    chats=_GENIMAGE_GROUPS_DIR,
    stubs={"find_chats": [{"name": c["name"], "id": c["jid"], "is_group": c["is_group"]}
                          for c in _GENIMAGE_GROUPS_DIR]},
    snapshot=_snap_schedules, grade=_two_friday_schedules_conv, state_checked=True,
    log_key="schedules_after", expected_tool_calls=["create_schedule"],
)


# -- gen-AI read -> STATE write: OCR a card the user sent, persist the fact ----------
# Chains an image extraction into the memory store and grades the REAL persisted
# memory (source of truth), then the recall turn. A receipt photo (PNG) sits in the
# chat as a decoy; the policy number exists ONLY in the gated OCR return for the
# policy-card photo (JPEG), so a pass proves image -> VLM -> remember -> recall.
_POLICY_NUMBER = "4437819"


def _card_photos_gate(args: dict) -> str:
    blob = str(args).lower()
    if "data:image/jpeg" in blob:   # the policy card
        return f"Acme Insurance card. Policy No. HP-{_POLICY_NUMBER}-2, holder: Alex."
    if "data:image/png" in blob:    # older receipt photo (decoy)
        return "Receipt: groceries, total 84.20 ILS."
    return "Replicate error: the vision model received no image input."


def _policy_saved_then_recalled(by_turn: list, records: list) -> bool:
    remembered = any(_POLICY_NUMBER in m for m in (by_turn[0] or []))
    recalled = _POLICY_NUMBER in (records[-1]["reply"] or "")
    return remembered and recalled


_IMAGE_FACT_TO_MEMORY = ConvScenario(
    name="image_fact_to_memory",
    turns=[
        "שמרי אצלך את מספר הפוליסה שלי מתמונת הכרטיס ששלחתי כאן עכשיו",
        "מה מספר הפוליסה של הביטוח שלי?",
    ],
    # state_checked stays False like memory_save_and_recall: the grade itself reads the
    # persisted memories via the snapshots; state_checked only relabels the report axis
    # (and the renderer's STATE block expects schedule-shaped rows).
    snapshot=_snap_memories, grade=_policy_saved_then_recalled,
    log_key="memories_after",
    messages={"972500000000@s.whatsapp.net": [
        {"sender": "Alex", "text": "", "from_me": True,
         "media_type": "image", "media_url": _TINY_PNG_DATA_URI},   # receipt decoy
        {"sender": "Alex", "text": "", "from_me": True,
         "media_type": "image", "media_url": _TINY_JPEG_DATA_URI},  # the policy card
    ]},
    stubs={
        "search_models": _search_media_models,
        "get_model_schema": _MEDIA_SCHEMA,
        "run_model": _card_photos_gate,
    },
)


CONV_SCENARIOS: list[ConvScenario] = [
    _ERROR_REPORT_FLOW, _ERROR_REPORT_FLOW_HE, _MEMORY_SAVE_RECALL,
    _SCHEDULE_MODIFY, _SCHEDULE_CANCEL, _SCHEDULE_TARGET_GROUP, _ONBOARDING_PROOF,
    _LOOKUP_BY_NUMBER, _AMBIGUOUS_NAME_ASKS, _CROSS_CHAT_CONFIRM,
    _IMAGE_FACT_TO_MEMORY, _SCHEDULE_GENIMAGE_GROUPS,
]


_ALL = (*SCENARIOS, *EXECUTION_SCENARIOS, *CONV_SCENARIOS)


def get_scenario(name: str):
    for s in _ALL:
        if s.name == name:
            return s
    raise KeyError(f"unknown scenario {name!r}; have {[s.name for s in _ALL]}")


# === Tags ====================================================================
# Multi-tag taxonomy for per-capability analysis in the report. Kept as ONE map
# (not per-scenario fields) so the whole taxonomy is reviewable in one screen and
# renaming a tag is a single edit. Capability tags say WHAT the scenario exercises:
#   generation     — creating new media (t2i, TTS, music, image->video)
#   editing        — transforming an existing image (edit/upscale/merge/text-on)
#   vision         — reading image/video content (VQA, OCR, tagging)
#   transcription  — audio -> text (incl. translate-transcripts)
#   scheduling     — schedule CRUD + fire-time execution
#   contacts       — resolution/lookup/safe cross-chat sends
#   memory         — long-term memory save/recall
#   error-report   — the two-phase report flow
#   chat-qa        — answering from conversation content
#   docs           — the Drive knowledge base (documents) save/read/activation
#   skills         — behavior gated on skill-following (incl. the VLM judge loop)
#   onboarding     — first-contact proof of life
# Cross-cutting: hebrew (Hebrew-language turn), group (group-chat surface).
SCENARIO_TAGS: dict[str, list[str]] = {
    "profile_pic_to_video": ["generation"],
    "schedule_fifa_daily_summary": ["scheduling"],
    "summarize_chat": ["chat-qa"],
    "schedule_oneoff_reminder": ["scheduling"],
    "schedule_weekly_weekday": ["scheduling"],
    "schedule_hebrew_daily": ["scheduling", "hebrew"],
    "transcribe_voice_note": ["transcription"],
    "search_conversation": ["chat-qa"],
    "report_23_cross_script_contact": ["contacts", "hebrew"],
    "report_24_skill_read_before_action": ["editing", "skills", "hebrew"],
    "report_26_chat_image_edit_no_base64": ["editing", "skills", "hebrew"],
    "image_tag_group_members": ["vision", "group"],
    "group_voice_note_hebrew": ["transcription", "hebrew", "group"],
    "chat_image_to_video": ["generation"],
    "add_text_to_image": ["editing"],
    "message_to_voice_tts": ["generation", "hebrew"],
    "upscale_blurry_photo": ["editing"],
    "text_to_image_hebrew": ["generation", "hebrew"],
    "receipt_ocr_total": ["vision", "hebrew"],
    "birthday_jingle_gen": ["generation"],
    "merge_profile_pics": ["editing"],
    "judge_rejects_bad_candidate": ["editing", "skills", "hebrew"],
    "group_tts_announcement": ["generation", "hebrew", "group"],
    "voice_note_translate": ["transcription", "hebrew", "group"],
    "menu_photo_translate": ["vision", "hebrew", "group"],
    "video_qa_group": ["vision", "hebrew", "group"],
    "birthday_invite_group": ["generation", "hebrew", "group"],
    "schedule_exec_date_correct": ["scheduling"],
    "error_report_two_phase": ["error-report"],
    "error_report_two_phase_he": ["error-report", "hebrew"],
    "memory_save_and_recall": ["memory"],
    "schedule_modify": ["scheduling"],
    "schedule_cancel": ["scheduling"],
    "schedule_target_group": ["scheduling", "group"],
    "onboarding_proof_of_life": ["onboarding"],
    "contact_lookup_by_number": ["contacts"],
    "contact_ambiguous_name_asks": ["contacts"],
    "cross_chat_send_confirms": ["contacts", "group"],
    "image_fact_to_memory": ["vision", "memory", "hebrew"],
    "schedule_genimage_two_groups": ["scheduling", "generation", "hebrew"],
    "kb_doc_reference": ["docs"],
    "kb_generic_docs_phrasing": ["docs"],
    "kb_drive_phrasing": ["docs"],
    "kb_notes_append_under": ["docs", "skills"],
    "kb_no_overtrigger_memory_fact": ["docs", "memory"],
    "report_33_profile_edit_base64_return": ["editing", "skills", "hebrew"],
}


def tags_for(name: str) -> list[str]:
    return SCENARIO_TAGS.get(name, [])
