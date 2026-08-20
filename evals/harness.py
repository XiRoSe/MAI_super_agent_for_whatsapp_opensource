"""Eval harness — runs a scenario against the LIVE model the way PRODUCTION does,
records the entire turn, grades it, and persists a full log for later inspection.

Why this exists: the first eval hand-built its agent and sent the model a NAKED
prompt ("Make a video of my friend Dana"). Production never does that — the router
hands the model a trusted CONTEXT block naming the chat participants + their phone
numbers (so "Dana" resolves to a number `get_profile_picture` can use), and caps the
agentic tool-loop with `tool_call_limit`. Without those, the model either stalls
asking "who's Dana?" or loops `code_exec` indefinitely. This harness reproduces the
production-shaped turn so the eval measures the real thing.

What it captures per run (-> evals/logs/<ts>__<scenario>.json + .txt):
  - inputs:  the system prompt + the exact agent input (the context-block turn)
  - log:     every code_exec SCRIPT the model wrote, each script's OUTPUT, and every
             mai.* SDK call inside it WITH ARGS AND RETURN VALUE
  - outputs: the model's final reply, token usage, timing, and the grade

CodeAct accounting note: the model's TOOL call is `code_exec`; the mai.* calls the
grader checks (get_profile_picture, run_model) happen INSIDE one script. An ideal
CodeAct run is ONE code_exec call. `tool_call_limit` bounds the number of code_exec
invocations (default 2 here; production uses 6).
"""
from __future__ import annotations

import inspect
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from pathlib import Path

from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from evals.scenarios import ConvScenario, ExecutionScenario, Scenario

from agno.agent import Agent
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import backend.code_exec.mai_sdk as sdk
from backend.agent.prompts import build_system_prompt
from backend.code_exec.runner import FakeRunner, RunResult
from backend.config import settings
from backend.db.base import Base
from backend.db.scoped import TenantScope
from backend.db.tenancy import Account, SubscriptionStatus, User
from backend.scheduling.models import Schedule
from backend.scheduling.service import ScheduleService
from evals.reliability import LayerVerdict, LayeredResult, ReliabilityEval

# Per-PROCESS agno store path: under subprocess parallelism each run is its own process, so
# the file (and the get_agno_db cache that points at it) is naturally private — no collision.
_EVAL_AGNO_FILE = f"tmp/eval_agno_{os.getpid()}.db"
_EVAL_AGNO_URL = f"sqlite:///{_EVAL_AGNO_FILE}"

LOGS_DIR = Path(__file__).resolve().parent / "logs"
# Match production (mai_agent.py: agent_tool_call_limit=6). CodeAct is ITERATIVE —
# the model writes code, reads the error, and corrects. GLM-5.2 doesn't have the mai
# SDK memorized, so it hallucinates the API on the first try (mai.whatsapp.X) and
# self-corrects to the flat mai.X after the AttributeError; that recovery needs ~4-5
# rounds. At limit=2 the loop is strangled (0% pass); at 6 it passes 100%. Efficiency
# (did it one-shot it?) is tracked SEPARATELY via n_code_exec_calls, not by capping low.
DEFAULT_TOOL_CALL_LIMIT = 6
DEFAULT_TIMEOUT_S = 150       # per-run wall-clock bound (6 rounds * ~7s + reply turns)


class EvalUser:
    """Minimal stand-in for backend.db.tenancy.User — token_store.mint only reads
    .account_id / .id, so no DB is needed."""

    account_id = 1
    id = 1
    whatsapp_number = "+972500000000"
    display_name = "Alex"
    timezone = "Asia/Jerusalem"


def bind_args(fn_name: str, args: tuple, kw: dict) -> dict:
    """Normalize positional+keyword SDK args to a name->value dict via the real signature."""
    try:
        bound = inspect.signature(getattr(sdk, fn_name)).bind_partial(*args, **kw).arguments
        return dict(bound)
    except Exception:
        return {"_args": list(args), **kw}


def _jsonable(v: Any) -> Any:
    """Best-effort JSON-serializable view of an SDK arg / return value."""
    try:
        json.dumps(v)
        return v
    except (TypeError, ValueError):
        return repr(v)


class RecordingRunner:
    """Wraps FakeRunner. Records, per run: every code_exec SCRIPT + its output, and
    every mai.* SDK call (name, normalized args, return value). `stubs` maps an SDK
    function name to a canned value (or a callable (args_dict)->value)."""

    def __init__(self, stubs: dict[str, Any]) -> None:
        self.stubs = stubs
        self.sdk_calls: list[dict] = []   # flat, ordered: {fn, args, returned}
        self.scripts: list[dict] = []     # {code, ok, output, sdk_calls:[...]}
        self._fake = FakeRunner(call_fn=self._call)

    def _resolve(self, fn: str, args_dict: dict) -> Any:
        stub = self.stubs.get(fn, "")
        return stub(args_dict) if callable(stub) else stub

    def _call(self, fn: str, *args: Any, **kw: Any) -> Any:
        args_dict = bind_args(fn, args, kw)
        ret = self._resolve(fn, args_dict)
        self.sdk_calls.append({"fn": fn, "args": _jsonable(args_dict), "returned": _jsonable(ret)})
        return ret

    def run(self, code: str, token: str, base_url: str, timeout: int) -> RunResult:
        start = len(self.sdk_calls)
        res = self._fake.run(code, token=token, base_url=base_url, timeout=timeout)
        self.scripts.append({"code": code, "ok": res.ok, "output": res.output,
                             "sdk_calls": self.sdk_calls[start:]})
        return res

    def recorded_calls(self) -> list[tuple[str, dict]]:
        return [(c["fn"], c["args"]) for c in self.sdk_calls]


def direct_chat_context(*, contact_name: str, contact_number: str,
                        owner_name: str = "Alex", owner_number: str = "972500000000") -> str:
    """The TRUSTED context block for a 1:1 chat (just the block — no command). Mirrors
    router._chat_context + _participants for a direct chat."""
    return (
        f'WHATSAPP CONTEXT (trusted): You were tagged in the chat "{contact_name}".\n'
        f'This is a direct (1:1) chat between two people: YOU ({owner_name}, {owner_number}) '
        f'and "{contact_name}" ({contact_number}). Act as the owner toward the contact; use '
        f"the contact's number to fetch their profile picture/details."
    )


def group_chat_context(*, group_name: str, owner_name: str = "Alex",
                       owner_number: str = "972500000000") -> str:
    """The TRUSTED context block for a GROUP chat Mai was tagged in. Anything Mai sends HERE
    is visible to the whole group — so a message meant for someone else must go to THEIR chat,
    which is the crux of the cross-chat send tests."""
    return (
        f'WHATSAPP CONTEXT (trusted): You were tagged in the GROUP "{group_name}".\n'
        f'You act as the owner, {owner_name} ({owner_number}). This group has multiple members '
        f'and anything you send in this chat is visible to ALL of them. To message one person '
        f'privately, send to THAT person’s own chat, not here.'
    )


def build_direct_chat_input(*, command: str, contact_name: str, contact_number: str,
                            owner_name: str = "Alex", owner_number: str = "972500000000") -> str:
    """Reproduce what router._build_agent_input() hands the model for a 1:1 WhatsApp
    chat: a TRUSTED context block naming both parties (+ numbers) and the @Mai command.
    Mirrors backend/whatsapp/router.py (_chat_context + _participants + _build_agent_input)."""
    context = direct_chat_context(contact_name=contact_name, contact_number=contact_number,
                                  owner_name=owner_name, owner_number=owner_number)
    return f"{context}\n\nMy instruction (@Mai): {command}"


def build_assistant_dm_input(*, command: str, owner_name: str = "Alex",
                             owner_number: str = "972500000000") -> str:
    """The shape for a user instructing Mai directly in their OWN DM (no third party) —
    e.g. setting up a schedule. "me"/"my" resolves to the owner."""
    context = (
        f'WHATSAPP CONTEXT (trusted): This is your private chat with the owner, '
        f'{owner_name} ({owner_number}). "me"/"my" refers to {owner_name}.'
    )
    return f"{context}\n\nMy instruction (@Mai): {command}"


class _FakeScheduleManager:
    """The ONLY substitution in the scheduling path: Agno's ScheduleManager registers the
    cron TRIGGER with the AgentOS poller (needs a running server). It is NOT our save
    logic — prod already treats registration as best-effort. Faking it (exactly as the
    production tests do) keeps ScheduleService — validation, per-user limit, the Schedule
    ORM row, flush/commit — 100% REAL. We substitute across a trusted boundary, not a
    production module."""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.deleted: list[str] = []

    def create(self, **kw: Any) -> None:
        self.created.append(kw)

    def delete(self, name: str) -> None:
        self.deleted.append(name)


def _make_sqlite_user() -> tuple[Session, User]:
    """A fresh in-memory SQLite DB with a real Account + User — the real persistence the
    production ScheduleService writes through. Per-run, so schedules never leak between
    runs and the dev's real tmp/mai.db is never touched (same setup as tests/conftest)."""
    engine = create_engine("sqlite://", future=True,
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    import backend.db.tenancy  # noqa: F401  (register models on Base.metadata)
    import backend.scheduling.models  # noqa: F401
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    acct = Account(plan="beta", status=SubscriptionStatus.active)
    session.add(acct)
    session.flush()
    user = User(account_id=acct.id, whatsapp_number="+972500000000",
                agno_user_id=User.make_agno_user_id(acct.id, "+972500000000"),
                display_name="Alex", timezone="Asia/Jerusalem")
    session.add(user)
    session.flush()
    # A linked WhatsApp connection — required for build_tools to add the owner's WhatsApp
    # toolkits (whatsapp/profile/access). session_id "dev1" matches the device_id we pass.
    from backend.db.tenancy import WhatsAppConnection, WhatsAppConnectionStatus
    session.add(WhatsAppConnection(account_id=acct.id, session_id="dev1",
                                   phone_number="+972500000000",
                                   status=WhatsAppConnectionStatus.connected))
    session.commit()
    return session, user


# ---------------------------------------------------------------------------
# Production-faithful toolset: the eval drives its tools through the REAL registry
# (backend.tools.registry.build_tools) — the SAME assembly every production entry
# point (router, dashboard, jobs, CLI) uses — so the eval's tool SURFACE can't drift
# from production. We substitute ONLY the external BOUNDARIES (the things CLAUDE.md
# sanctions swapping): the WhatsApp gateway, the e2b sandbox (-> the scenario's
# RecordingRunner), Replicate, web, and Agno's external scheduler. The toolkits
# themselves — WhatsAppTools, ScheduleTools, MediaTools — run for real. Nothing
# leaves the process, and we never hit a paid/external service unless a scenario
# deliberately wires a real return.
# ---------------------------------------------------------------------------
OWNER_JID = "972500000000@s.whatsapp.net"


class RecordingProvider:
    """Hermetic WhatsApp transport: RECORDS every send (so a scenario can grade the real
    send call + its resolved target — the delivered-state source of truth) and serves canned
    chats/messages for reads. No network. `sends` is the ordered list of what went out."""

    def __init__(self, *, chats: list[dict] | None = None,
                 messages: dict[str, list] | None = None,
                 participants: dict[str, list] | None = None,
                 owner_number: str = "972500000000") -> None:
        self.sends: list[dict] = []
        self.chats = chats or []
        self.messages = messages or {}
        self.participants = participants or {}  # group jid -> [{name, number, admin}]
        self.owner_number = owner_number

    # -- sends (recorded; return a fake message id like the real provider) --
    def send_text(self, to_number, text, *, session_id=None, **kw):
        self.sends.append({"kind": "text", "to": to_number, "text": text})
        return "evalmsg"

    def send_image(self, to_number, image_url=None, *, caption=None, session_id=None, **kw):
        self.sends.append({"kind": "image", "to": to_number, "url": image_url, "caption": caption})
        return "evalmsg"

    def send_video(self, to_number, video_url=None, *, caption=None, session_id=None, **kw):
        self.sends.append({"kind": "video", "to": to_number, "url": video_url, "caption": caption})
        return "evalmsg"

    def send_audio(self, to_number, audio_url=None, *, session_id=None, **kw):
        self.sends.append({"kind": "audio", "to": to_number, "url": audio_url})
        return "evalmsg"

    def send_file(self, to_number, file_url=None, *, caption=None, session_id=None, **kw):
        self.sends.append({"kind": "file", "to": to_number, "url": file_url, "caption": caption})
        return "evalmsg"

    def react(self, *a, **k): return None
    def send_presence(self, *a, **k): return None
    def mark_read(self, *a, **k): return None

    # -- reads (canned) --
    def recent_chats(self, *, session_id=None, days=7):
        return list(self.chats)

    def list_chats(self, device_id=None, *, limit=50):
        return list(self.chats)[:limit]

    def list_my_groups(self, *, session_id=None):
        return [c for c in self.chats if c.get("is_group")]

    def get_chat_messages(self, session_id, chat_id, *, limit=20):
        return list(self.messages.get(chat_id, []))[-limit:]

    def get_group_participants(self, group_id, *, session_id=None):
        return list(self.participants.get(group_id, []))

    def get_avatar(self, phone, *, session_id=None, **k):
        return None

    def get_user_info(self, phone, *, session_id=None, **k):
        return None

    def device_phone(self, session_id=None):
        return self.owner_number


class FakeDrive:
    """Hermetic Google Drive boundary for the knowledge base (backend.gdrive), the same
    substitution rule as RecordingProvider: the transport is faked, the toolkit/vault/
    scope above it run for real. Holds files in memory; `files_by_title()` is the
    persisted-state view scenarios grade on."""

    _FOLDER_MIME = "application/vnd.google-apps.folder"

    def __init__(self, docs: Mapping[str, str] | None = None) -> None:
        import backend.gdrive as gdrive

        self._g = gdrive
        self.files: dict[str, dict] = {}
        self.seq = 0
        if docs:
            self.seq += 1
            folder = f"fold{self.seq}"
            self.files[folder] = {"id": folder, "name": gdrive.FOLDER_NAME,
                                  "mimeType": self._FOLDER_MIME, "parents": [], "content": ""}
            for title, content in docs.items():
                self.seq += 1
                fid = f"f{self.seq}"
                self.files[fid] = {"id": fid, "name": f"{title}.md",
                                   "mimeType": "text/markdown", "parents": [folder],
                                   "content": content}

    def files_by_title(self) -> dict[str, str]:
        """The KB's final persisted state: {title: content} for every markdown doc."""
        return {f["name"].removesuffix(".md"): f["content"]
                for f in self.files.values() if f["mimeType"] != self._FOLDER_MIME}

    def _resp(self, payload=None, text="", status=200):
        from types import SimpleNamespace

        return SimpleNamespace(status_code=status, text=text,
                               json=lambda: payload, raise_for_status=lambda: None)

    def __call__(self, method, url, **kw):
        g = self._g
        if url == g._TOKEN_URL:
            return self._resp({"access_token": "eval-token", "expires_in": 3600})
        if url.startswith(g._UPLOAD_URL):
            fid = url.rsplit("/", 1)[1]
            self.files[fid]["content"] = kw["content"].decode()
            return self._resp({"id": fid})
        if url == g._FILES_URL and method == "GET":
            return self._resp({"files": self._query(kw["params"]["q"])})
        if url == g._FILES_URL and method == "POST":
            self.seq += 1
            fid = f"f{self.seq}"
            meta = kw["json"]
            self.files[fid] = {"id": fid, "name": meta["name"], "mimeType": meta["mimeType"],
                               "parents": meta.get("parents", []), "content": ""}
            return self._resp({"id": fid})
        if url.startswith(g._FILES_URL + "/"):
            fid = url.rsplit("/", 1)[1]
            if method == "DELETE":
                self.files.pop(fid, None)
                return self._resp({})
            return self._resp(text=self.files[fid]["content"])  # alt=media read
        raise AssertionError(f"FakeDrive: unexpected call {method} {url}")

    def _query(self, q):
        out = []
        for f in self.files.values():
            if "name = " in q and f"name = '{f['name']}'" not in q:
                continue
            if "in parents" in q and not any(f"'{p}' in parents" in q for p in f["parents"]):
                continue
            if "mimeType = " in q and f"mimeType = '{f['mimeType']}'" not in q:
                continue
            out.append({"id": f["id"], "name": f["name"], "modifiedTime": "2026-07-15T00:00:00Z"})
        return out


def _connect_drive(session: Session, user: User) -> None:
    """Mark the eval user's Drive as connected (vault credential with a live token),
    so docs-tool availability is never the thing a scenario accidentally measures."""
    from backend.credentials import save_credential
    from backend.gdrive import SERVICE

    scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
    save_credential(scope, service=SERVICE, data={
        "access_token": "eval-token", "refresh_token": "eval-rt",
        "expires_at": time.time() + 3600,
    })


def _provider_from_scenario(sc: "Scenario") -> RecordingProvider:
    """Feed the provider the SAME chat data the scenario gives the SDK stubs, so a read via
    the DIRECT WhatsAppTools path returns what a read via the code_exec SDK path would — the
    scenario stays path-agnostic (the model may legitimately use either, additively)."""
    chats: list[dict] = [{"name": "Alex", "jid": OWNER_JID, "is_group": False}]  # the owner's self-chat
    fc = sc.stubs.get("find_chats")
    if callable(fc):  # dynamic stub (e.g. the real dispatch matcher) — seed with its full list
        try:
            fc = fc({})
        except Exception:
            fc = None
    if isinstance(fc, list):
        for c in fc:
            chats.append({"name": c.get("name"), "jid": c.get("id") or c.get("jid"),
                          "is_group": c.get("is_group", False)})
    messages: dict[str, list] = {}
    rc = sc.stubs.get("read_chat")
    if isinstance(rc, list) and sc.contact_jid:
        messages[sc.contact_jid] = [
            {"sender": m.get("sender"), "text": m.get("text"), "from_me": m.get("from_me"),
             "media_type": m.get("media_type"), "media_url": m.get("media_url"),
             "ts": None, "id": None} for m in rc]
    # Group membership: the get_group_members stub also seeds the provider, so ALL
    # participant paths (list_participants, get_group_members tool, SDK dispatch) serve
    # the same canned roster for the scenario's current chat.
    participants: dict[str, list] = {}
    gm = sc.stubs.get("get_group_members")
    if isinstance(gm, list) and sc.contact_jid:
        participants[sc.contact_jid] = [dict(m) for m in gm]
    return RecordingProvider(chats=chats, messages=messages, participants=participants)


import contextlib  # noqa: E402


@contextlib.contextmanager
def eval_runtime(*, runner: "RecordingRunner", provider: RecordingProvider,
                 drive: "FakeDrive | None" = None):
    """Activate the production tool path with every EXTERNAL boundary faked, then restore.
    Sets the deploy flags we're emulating (code_exec ON, additive: codeact OFF, router OFF =>
    full surface) and swaps: WhatsApp provider -> `provider`; e2b make_runner -> the scenario's
    `runner` (becomes the code_exec sandbox); Agno ScheduleManager -> _FakeScheduleManager;
    Replicate + web -> inert recorders. build_eval_tools MUST run inside this so the toolkits
    bind the fakes at construction."""
    import agno.scheduler as agno_sched
    from agno.tools.websearch import WebSearchTools as _AgnoWeb

    import backend.code_exec.e2b_runner as e2b
    import backend.db.agno_db as agno_db_mod
    import backend.whatsapp as whatsapp
    from backend.tools.profile_tools import ProfileTools
    from backend.tools.replicate_tools import ReplicateTools
    from backend.tools.web_tools import WebReadTools

    flags = ("code_exec_enabled", "codeact_mode", "tool_router_enabled", "guardrail_enabled",
             "whatsapp_gateway_url", "sdk_base_url", "replicate_api_token", "mai_db_url")
    saved = {k: getattr(settings, k) for k in flags}
    settings.code_exec_enabled = True
    settings.codeact_mode = False          # additive: direct tools AND code_exec
    settings.tool_router_enabled = False   # => full toolset, deterministic (no router LLM call)
    settings.guardrail_enabled = False
    settings.whatsapp_gateway_url = "http://eval.invalid"
    settings.sdk_base_url = "http://local.invalid"
    settings.replicate_api_token = "eval-token"
    # Agno history/memory store: a per-process SQLITE file (NOT production) — _fresh_agno_db points
    # mai_db_url at it, resets it, and returns a WORKING db. We do NOT null get_agno_db: that dead
    # handle silently breaks MemoryTools.remember() and multi-turn history threading.
    agno_db = _fresh_agno_db()

    patches: list[tuple] = []

    def patch(obj, name, val):
        patches.append((obj, name, getattr(obj, name)))
        setattr(obj, name, val)

    def method(name, fn):
        # agno registers a toolkit method as a tool BY ITS __name__; an anonymous lambda
        # would register as '<lambda>' and collide across toolkits (agno drops the dupes,
        # corrupting the faked tool's name). Stamp the real method name so the SURFACE holds.
        fn.__name__ = name
        return fn

    def from_stub(name: str, args: dict, default: Any) -> Any:
        # Resolve a DIRECT tool's return from the SAME per-scenario stub the SDK path uses, so the
        # two additive paths see identical data (e.g. run_model -> the scenario's transcript stub,
        # get_profile_picture -> its pic URL). No stub => `default` (keeps the surface offline).
        stub = runner.stubs.get(name)
        if stub is None:
            return default
        return stub(args) if callable(stub) else stub

    patch(whatsapp, "get_provider", lambda: provider)
    patch(e2b, "make_runner", lambda: runner)
    patch(agno_sched, "ScheduleManager", lambda *a, **k: _FakeScheduleManager())
    # Replicate + profile: never reach the API. The DIRECT tools delegate to the scenario's stubs
    # (same as the code_exec SDK path), so additive runs are path-agnostic; absent a stub they return
    # an offline default. These are recorded as agno tool calls (not sdk_calls) — layers stay distinct.
    patch(ReplicateTools, "run_model", method("run_model",
          lambda self, model, inputs=None: from_stub("run_model", {"model": model, "inputs": inputs},
                                                     "https://replicate.delivery/eval/out.png")))
    patch(ReplicateTools, "search_models", method("search_models",
          lambda self, query: from_stub("search_models", {"query": query},
                                        "Found (use run_model):\n- eval/model: canned eval model")))
    patch(ReplicateTools, "get_model_schema", method("get_model_schema",
          lambda self, model: from_stub("get_model_schema", {"model": model}, '{"model":"eval","inputs":{}}')))
    patch(ProfileTools, "get_profile_picture", method("get_profile_picture",
          lambda self, phone="": from_stub("get_profile_picture", {"phone": phone},
                                           "(no profile picture available)")))
    patch(ProfileTools, "get_user_details", method("get_user_details",
          lambda self, phone="": from_stub("get_user_details", {"phone": phone},
                                           "(no public details available)")))
    # Web: deterministic + offline.
    patch(_AgnoWeb, "web_search", method("web_search", lambda self, *a, **k: "[]"))
    patch(_AgnoWeb, "search_news", method("search_news", lambda self, *a, **k: "[]"))
    patch(WebReadTools, "read_url", method("read_url",
          lambda self, url: f"(eval: web read disabled for {url})"))
    # Knowledge base: Drive's HTTP transport -> the scenario's FakeDrive (vault/scope/
    # toolkit above it run for real). Without a drive, gdrive still fails closed on the
    # empty vault (DriveNotConnected) before any network call.
    if drive is not None:
        import backend.gdrive as gdrive_mod

        patch(gdrive_mod, "_request", drive)
    try:
        yield agno_db
    finally:
        for obj, name, old in reversed(patches):
            setattr(obj, name, old)
        for k, v in saved.items():
            setattr(settings, k, v)
        agno_db_mod.get_agno_db.cache_clear()  # so a later non-eval get_agno_db rebuilds on restored url


def build_eval_tools(user: User, session: Session, *, runner: "RecordingRunner",
                     current_chat: str, device_id: str = "dev1",
                     invoke_message_id: str | None = None, turn_state: dict | None = None) -> list[Any]:
    """The production toolset for the eval — assembled by the REAL registry.build_tools (full
    surface, router off). MUST be called inside eval_runtime so the external boundaries are faked.
    `invoke_message_id`/`turn_state` flow to the per-turn tools (e.g. ErrorReportTools' two-phase gate)."""
    from backend.tools.registry import build_tools

    return build_tools(user, session, channel=current_chat, is_owner=True,
                       device_id=device_id, message=None,
                       invoke_message_id=invoke_message_id, turn_state=turn_state)


@dataclass(frozen=True)
class RunMeta:
    """Provider-reported metrics for one run (best-effort — any field may be None).
    `cost` is real USD from the provider's usage accounting; `error` is set only if the
    metric extraction itself failed."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost: float | None = None
    status: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ReasoningStepRecord:
    """One Agno *structured* reasoning step (only populated when agent_reasoning=True, which
    prod leaves off). Kept verbatim so the log shows the model's stated plan, not just a count."""

    title: str = ""
    reasoning: str = ""
    action: str = ""
    result: str = ""
    confidence: float | None = None


@dataclass(frozen=True)
class AssistantStep:
    """One ASSISTANT turn inside the agentic loop — the model's native thinking + its
    natural-language narration + the tools it called that step. This is the 'why' the
    tool-use log alone can't show: GLM emits thinking *per step*, which the aggregate
    reasoning_content collapses. Recovered by walking RunOutput.messages."""

    index: int
    reasoning: str = ""                                   # native thinking (message.reasoning_content)
    content: str = ""                                     # model's narration (message.content)
    tool_calls: list[str] = field(default_factory=list)   # tool names invoked this step


@dataclass(frozen=True)
class Reasoning:
    """The thinking layer: how much explicit reasoning the model did (0 when off) AND the
    actual TEXT it produced — not just its size. `content` is agno's aggregate native
    thinking; `steps` are the (flag-gated) structured reasoning steps."""

    n_reasoning_steps: int = 0
    reasoning_chars: int = 0
    content: str = ""
    steps: list[ReasoningStepRecord] = field(default_factory=list)


@dataclass(frozen=True)
class RunDetail:
    """One execution's headline numbers — the child rows the report shows when a scenario
    row is expanded."""

    index: int
    passed: bool
    duration_s: float
    n_tool_calls: int
    n_sdk_calls: int
    n_wasted_calls: int
    n_reasoning_steps: int
    tokens_out: int
    cost_usd: float
    log_file: str = ""   # the .txt transcript's filename (report links to it; same logs/ dir)


@dataclass(frozen=True)
class ScenarioAggregate:
    """Per-scenario metrics over N runs — the typed row the table + HTML report read.
    `executions` carries each individual run for the report's expandable child rows."""

    scenario: str
    runs: int
    passes: int
    pass_rate: float
    expected_call_coverage: float
    latency_p50_s: float
    latency_p95_s: float
    latency_mean_s: float
    avg_tool_calls: float
    tool_calls_p95: float
    tool_calls_stdev: float
    avg_code_exec_calls: float
    avg_sdk_calls: float
    avg_wasted_calls: float
    avg_reasoning_steps: float
    timeouts: int
    errors: int
    mean_tokens_in: int
    mean_tokens_out: int
    cost_usd: float
    executions: list[RunDetail] = field(default_factory=list)


@dataclass(frozen=True)
class RunSummary:
    """The whole invocation's result — written to summary.json and rendered to HTML."""

    model_id: str
    git_rev: str
    runs_per_scenario: int
    tool_call_limit: int
    timeout_s: int
    threshold: float
    generated_at: str
    total_cost_usd: float
    scenarios: list[ScenarioAggregate]


@dataclass(frozen=True)
class Grade:
    """The full typed verdict for a run: the call layers (work + action, from the grader)
    plus the STATE layer (did the REAL persisted rows match). `passed` ANDs them all.
    Typed end-to-end (not a string-keyed dict) so the schema is enforced and read by name;
    `asdict()` serializes it — with its nested LayeredResult/LayerVerdict — for the log."""

    passed: bool
    calls: LayeredResult
    state_checked: bool
    state_ok: bool
    n_persisted_schedules: int
    # OUTPUT layer — for execution evals that grade the delivered message's content.
    output_checked: bool = False
    output_ok: bool = True
    # FORBIDDEN layer — tools that must NOT have fired (over-trigger guard).
    forbidden_hit: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "PASSED" if self.passed else "FAILED"


@dataclass(frozen=True)
class ScheduleSnapshot:
    """A typed, detached view of a persisted Schedule row — the eval's source-of-truth
    contract for grading scheduling turns. Typed (not a bare dict) because the schema is
    known and `state_check` reads it by attribute; detached so no ORM/session object
    leaks past the run. `asdict()` serializes it for the JSON log."""

    id: int
    name: str
    kind: str            # "static" | "dynamic"
    schedule_type: str   # "once" | "recurring"
    cron: str | None
    run_at: datetime | None
    timezone: str
    active: bool
    target_chat: str | None = None       # WHERE it fires (jid); None => creator's chat/DM
    target_chat_name: str | None = None  # the label confirmed at creation ("Family (group)")


def _read_schedules(session: Session, user: User) -> list[ScheduleSnapshot]:
    """Read back the REAL persisted Schedule rows (the production source of truth) as
    typed snapshots, so a scenario can grade on STORED STATE, not just tool-call args."""
    try:
        scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
        return [ScheduleSnapshot(id=s.id, name=s.name, kind=s.kind,
                                 schedule_type=s.schedule_type, cron=s.cron,
                                 run_at=s.run_at, timezone=s.timezone, active=s.active,
                                 target_chat=s.target_chat, target_chat_name=s.target_chat_name)
                for s in scope.all(Schedule)]
    except Exception:  # reporting must never break a run
        return []


@dataclass(frozen=True)
class ErrorReportSnapshot:
    """A typed, detached view of a persisted ErrorReport row — the source of truth for
    grading the report flow. status is 'pending' (staged preview, nothing filed) or
    'open' (filed after approval)."""

    id: int
    status: str
    summary: str
    command: str
    surface: str


def _read_error_reports(session: Session, user: User) -> list[ErrorReportSnapshot]:
    """Read back the REAL persisted ErrorReport rows (the production source of truth)."""
    try:
        from backend.db.tenancy import ErrorReport

        scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
        return [ErrorReportSnapshot(id=r.id, status=r.status, summary=r.summary or "",
                                    command=r.command or "", surface=r.surface or "")
                for r in scope.all(ErrorReport)]
    except Exception:
        return []


class _NoMemoryDB:
    """The agno memory store, emptied. The eval user is freshly created and has no saved
    long-term memories, so prod's memory-recall layer injects nothing for them. The store
    is a trusted persistence boundary (like the SQL engine), so substituting an empty one
    keeps the eval hermetic WITHOUT faking the recall logic — build_runtime_system_prompt
    still runs the real beliefs/memory/skills assembly against it."""

    def get_user_memories(self, *a: Any, **k: Any) -> list:
        return []


def build_eval_system_prompt(*, user: User, session: Session, query: str) -> str:
    """The eval's system prompt IS the production assembly — it calls the REAL
    backend.agent.mai_agent.build_runtime_system_prompt (base + beliefs + memory +
    auto-recalled skills), so there is no eval-side copy of the prompt logic to drift.
    The only substitution is the memory STORE (emptied via _NoMemoryDB — the eval user's
    true state); beliefs + skills run for real against the eval `session`, seeding the
    bundled SKILL.md defaults and recalling them against `query` exactly as prod does.
    build_eval_agent always registers code_exec (the CodeAct work path), so we force the
    mai.* SDK reference on regardless of the codeact_mode flag."""
    from backend.agent.mai_agent import build_runtime_system_prompt

    return build_runtime_system_prompt(user, session=session, db=_NoMemoryDB(), query=query,
                                       include_sdk_reference=True)
def build_eval_agent(model: Any, runner: RecordingRunner, *, user: User, session: Session,
                     current_chat: str, tool_call_limit: int = DEFAULT_TOOL_CALL_LIMIT,
                     tool_choice: str | None = None, system_message: str | None = None) -> Agent:
    """Mai with the REAL production toolset (registry.build_tools — code_exec, the WhatsApp
    send/read tools, media, schedule, web, etc.), the real system prompt, and the production
    tool-loop cap. Tool SURFACE == production by construction; external boundaries are faked by
    the enclosing eval_runtime. MUST be called inside eval_runtime. `system_message` carries the
    prod-shaped prompt (base + recalled skills); if omitted, falls back to the bare base prompt."""
    tools = build_eval_tools(user, session, runner=runner, current_chat=current_chat)
    # In stub mode the prompt carries no skill bodies (build_runtime_system_prompt skips them);
    # the Agent gets agno-native Skills instead — exactly like production build_agent. So the
    # turn eval exercises whichever skill path prod is running.
    skills_obj = None
    if settings.skill_mode == "stub":
        try:
            from backend.skills_store import agno_skills

            skills_obj = agno_skills(session, user)
        except Exception:
            skills_obj = None
    return Agent(
        name="Mai", model=model, tools=tools, skills=skills_obj,
        system_message=system_message or build_system_prompt(
            user_display_name=user.display_name, user_timezone=user.timezone),
        tool_call_limit=tool_call_limit,
        tool_choice=tool_choice,
        markdown=False, telemetry=False, add_history_to_context=False,
    )


def _run_meta(out: Any) -> RunMeta:
    """Best-effort token/cost/status from the agno RunOutput (defensive — never raise)."""
    try:
        m = getattr(out, "metrics", None)
        status = getattr(out, "status", None)
        return RunMeta(
            input_tokens=getattr(m, "input_tokens", None) if m is not None else None,
            output_tokens=getattr(m, "output_tokens", None) if m is not None else None,
            # Thinking layer: model-internal reasoning tokens (None unless reasoning is on).
            reasoning_tokens=getattr(m, "reasoning_tokens", None) if m is not None else None,
            # Real per-call cost (USD) — None unless the provider's usage accounting is on.
            cost=getattr(m, "cost", None) if m is not None else None,
            status=str(getattr(status, "value", status)) if status is not None else None,
        )
    except Exception as exc:  # pragma: no cover
        return RunMeta(error=str(exc))


def _extract_tool_calls(out: Any) -> list[dict]:
    """The AGNO-layer tool calls (generic across tools, so this also covers a direct-mode
    send tool, not just code_exec). Each: {name, error, args}. `error` is agno's
    tool_call_error — a tool that RAISED. NOTE: a failed code_exec script does NOT raise
    (the toolkit returns a 'Script error…' string), so script failures are counted as
    waste separately from this flag (see run_scenario)."""
    calls: list[dict] = []
    for t in (getattr(out, "tools", None) or []):
        calls.append({
            "name": getattr(t, "tool_name", None),
            "error": bool(getattr(t, "tool_call_error", None)),
            "args": _jsonable(getattr(t, "tool_args", None)),
        })
    return calls


def _msg_text(content: Any) -> str:
    """Coerce an agno Message.content (str | list of blocks | None) to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                parts.append(str(b.get("text") or b.get("content") or ""))
            else:
                parts.append(str(getattr(b, "text", "") or ""))
        return "".join(parts).strip()
    return str(content).strip()


def _tool_names(tool_calls: Any) -> list[str]:
    """Pull tool names from an agno Message.tool_calls list (dicts or objects)."""
    names: list[str] = []
    for tc in (tool_calls or []):
        fn = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
        name = (fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)) if fn else None
        if name:
            names.append(str(name))
    return names


def _reasoning_summary(out: Any) -> Reasoning:
    """The THINKING layer: the model's reasoning this turn — counts AND the actual text.
    Native thinking (reasoning_content) is emitted even with agent_reasoning off; the
    structured `steps` only appear when the flag is on (prod leaves it off)."""
    raw_steps = getattr(out, "reasoning_steps", None) or []
    content = getattr(out, "reasoning_content", None) or ""
    steps = [ReasoningStepRecord(title=getattr(s, "title", "") or "",
                                 reasoning=getattr(s, "reasoning", "") or "",
                                 action=getattr(s, "action", "") or "",
                                 result=getattr(s, "result", "") or "",
                                 confidence=getattr(s, "confidence", None))
             for s in raw_steps]
    return Reasoning(n_reasoning_steps=len(raw_steps), reasoning_chars=len(content),
                     content=content, steps=steps)


def _assistant_trace(out: Any) -> list[AssistantStep]:
    """Walk RunOutput.messages and pull each ASSISTANT step's native thinking + narration +
    the tools it called. This recovers the per-step chain of thought the aggregate
    reasoning_content drops — for a CodeAct/reasoning model that's the real 'why'. Skips
    system/user/tool messages (already captured elsewhere) and wholly-empty frames."""
    trace: list[AssistantStep] = []
    for m in (getattr(out, "messages", None) or []):
        if getattr(m, "role", None) != "assistant":
            continue
        reasoning = (getattr(m, "reasoning_content", None) or "").strip()
        content = _msg_text(getattr(m, "content", None))
        names = _tool_names(getattr(m, "tool_calls", None))
        if not (reasoning or content or names):
            continue
        trace.append(AssistantStep(index=len(trace), reasoning=reasoning,
                                   content=content, tool_calls=names))
    return trace


def _resolved_system(out: Any) -> str:
    """The system message agno ACTUALLY sent the model (ground truth from RunOutput.messages),
    not just what we passed in. These can differ — agno appends tool/skills instructions to the
    prompt ONLY when no explicit system_message is set; when one IS set it returns early and those
    are dropped. Capturing the resolved message is what makes 'did the model see X?' answerable."""
    for m in (getattr(out, "messages", None) or []):
        if getattr(m, "role", None) == "system":
            return _msg_text(getattr(m, "content", None))
    return ""


def _tool_roster(agent: Any) -> list[dict]:
    """Every CALLABLE tool the model was offered: name + one-line description. Walks the agent's
    toolkits (Toolkit.functions) + any standalone callables + the skills fetch tool. This is the
    direct-tool surface — NOT the mai.* SDK (which is reachable only inside code_exec, and only
    if its reference reached the prompt; see _resolved_system)."""
    roster: list[dict] = []
    seen: set[str] = set()

    def _first_line(s: Any) -> str:
        return str(s or "").strip().split("\n", 1)[0]

    def _add(name: Any, desc: str) -> None:
        name = str(name) if name else ""
        if name and name not in seen:
            seen.add(name)
            roster.append({"name": name, "description": desc})

    try:
        for t in (getattr(agent, "tools", None) or []):
            funcs = getattr(t, "functions", None)   # Toolkit -> {name: Function}
            if isinstance(funcs, dict):
                for fn in funcs.values():
                    # agno populates Function.description lazily (at schema-build time); fall back
                    # to the entrypoint's docstring so the roster is never blank.
                    desc = _first_line(getattr(fn, "description", "")) or _first_line(
                        getattr(getattr(fn, "entrypoint", None), "__doc__", ""))
                    _add(getattr(fn, "name", None), desc)
            elif callable(t):
                _add(getattr(t, "__name__", None), _first_line(t.__doc__))
        # agno injects a get_skill_instructions fetch tool when skills are attached (stub mode).
        if getattr(agent, "skills", None) is not None:
            _add("get_skill_instructions", "Fetch a skill's full instructions by name (stub mode).")
    except Exception:
        pass
    return roster


@dataclass
class RunRecord:
    scenario: str
    model_id: str
    tool_call_limit: int
    timeout_s: int
    agent_input: str
    system_prompt: str                 # the prompt we PASS agno (base + recalled skills)
    expected_sdk_calls: list[str]      # work layer (mai.*)
    expected_tool_calls: list[str]     # delivery layer (agno tools, e.g. send_video)
    # filled after the run:
    context_system: str = ""           # the system message agno RESOLVED + sent (ground truth; see _resolved_system)
    tool_roster: list[dict] = field(default_factory=list)  # callable tools offered: [{name, description}]
    git_rev: str = ""                  # commit the eval ran against (set by run.py)
    log_file: str = ""                 # this run's .txt transcript filename (set by run.py)
    timed_out: bool = False
    error: str | None = None
    duration_s: float = 0.0
    scripts: list[dict] = field(default_factory=list)
    sdk_calls: list[dict] = field(default_factory=list)
    # --- the three layers of abstraction we monitor, each counted separately ---
    tool_calls: list[dict] = field(default_factory=list)  # agno layer (every tool, any mode)
    n_tool_calls: int = 0          # agentic loop length — what tool_call_limit actually bounds
    n_code_exec_calls: int = 0     # of the tool calls, how many were code_exec
    n_sdk_calls: int = 0           # sandbox layer — mai.* calls inside the scripts
    n_wasted_calls: int = 0        # tool calls that errored OR scripts that failed (the waste tax)
    reasoning: Reasoning = field(default_factory=Reasoning)  # thinking layer (counts + text)
    assistant_trace: list[AssistantStep] = field(default_factory=list)  # per-step thinking+narration
    persisted_schedules: list[ScheduleSnapshot] = field(default_factory=list)  # REAL rows written
    turns: list[dict] = field(default_factory=list)  # per-turn detail for multi-turn (conversation) evals
    sends: list[dict] = field(default_factory=list)  # every provider send (text/image/...) this run
    final_reply: str | None = None
    run_meta: RunMeta = field(default_factory=RunMeta)
    grade: "Grade | None" = None
    passed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def run_scenario(scenario: "Scenario", model: Any, *,
                 tool_call_limit: int = DEFAULT_TOOL_CALL_LIMIT,
                 timeout_s: int = DEFAULT_TIMEOUT_S,
                 tool_choice: str | None = None) -> RunRecord:
    """Run one scenario once against the live model, bounded by `timeout_s`. Captures
    the full transcript and grades the recorded mai.* calls. Never raises."""
    settings.guardrail_enabled = False  # skip the pre-flight LLM screen in eval
    # NOTE: production (codeact_mode=False) runs tool_call_limit=None — UNBOUNDED
    # (mai_agent.py sets the limit only in codeact mode). The eval default of 6 is a
    # cost bound, not prod parity; a scenario whose legitimate flow needs more rounds
    # (e.g. a skill with a judge loop) declares its own tool_call_limit.
    if scenario.tool_call_limit is not None:
        tool_call_limit = scenario.tool_call_limit
    if scenario.timeout_s is not None:
        timeout_s = scenario.timeout_s
    runner = RecordingRunner(scenario.stubs)
    provider = _provider_from_scenario(scenario)
    agent_input = scenario.agent_input()
    session, user = _make_sqlite_user()
    # Knowledge base: scenario.docs is the seeded KB ({} = connected but empty; None = the
    # scenario doesn't involve the KB and Drive stays unconnected).
    drive = FakeDrive(scenario.docs) if scenario.docs is not None else None
    if drive is not None:
        _connect_drive(session, user)
    # Assemble the prompt the way prod does (base + auto-recalled skills) so the eval
    # exercises the SAME context prod runs. Built ONCE — used for both the log record and
    # the live agent. Recall query = the raw user command (what prod embeds), not the
    # context-wrapped agent_input.
    system_message = build_eval_system_prompt(user=user, session=session,
                                              query=scenario.command)
    rec = RunRecord(
        scenario=scenario.name,
        model_id=str(getattr(model, "id", "?")),
        tool_call_limit=tool_call_limit,
        timeout_s=timeout_s,
        agent_input=agent_input,
        system_prompt=system_message,
        expected_sdk_calls=list(scenario.expected_sdk_calls),
        expected_tool_calls=list(scenario.expected_tool_calls),
    )
    # Production-faithful toolset (registry.build_tools) with external boundaries faked.
    with eval_runtime(runner=runner, provider=provider, drive=drive):
        agent = build_eval_agent(model, runner, user=user, session=session,
                                 current_chat=scenario.current_chat,
                                 tool_call_limit=tool_call_limit, tool_choice=tool_choice,
                                 system_message=system_message)
        rec.tool_roster = _tool_roster(agent)  # callable tools offered (available even on timeout)
        t0 = time.time()
        ex = ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(agent.run, agent_input)
        try:
            out = fut.result(timeout=timeout_s)
            rec.final_reply = getattr(out, "content", None)
            rec.run_meta = _run_meta(out)
            rec.tool_calls = _extract_tool_calls(out)
            rec.reasoning = _reasoning_summary(out)
            rec.assistant_trace = _assistant_trace(out)
            rec.context_system = _resolved_system(out)  # what the model ACTUALLY received
        except FuturesTimeout:
            rec.timed_out = True
            rec.error = f"timed out after {timeout_s}s"
        except Exception as exc:  # provider/agent error
            rec.error = f"{type(exc).__name__}: {exc}"
        finally:
            ex.shutdown(wait=False)
            rec.duration_s = round(time.time() - t0, 2)
            rec.persisted_schedules = _read_schedules(session, user)
            session.close()

    rec.scripts = runner.scripts
    rec.sdk_calls = runner.sdk_calls
    rec.n_code_exec_calls = len(runner.scripts)
    rec.n_sdk_calls = len(runner.sdk_calls)
    # Prefer agno's own tool list for the loop length; fall back to the runner's
    # code_exec count if agno didn't surface tools (defensive).
    rec.n_tool_calls = len(rec.tool_calls) or rec.n_code_exec_calls
    # Waste = code_exec scripts that failed (invisible to agno) + any OTHER tool that raised.
    failed_scripts = sum(1 for s in runner.scripts if not s["ok"])
    failed_other = sum(1 for c in rec.tool_calls if c["error"] and c["name"] != "code_exec")
    rec.n_wasted_calls = failed_scripts + failed_other

    result: LayeredResult = ReliabilityEval(
        name=scenario.name,
        recorded_sdk_calls=runner.recorded_calls(),
        actual_tool_calls=rec.tool_calls,            # full {name, args, error} dicts
        expected_sdk_calls=scenario.expected_sdk_calls,
        expected_tool_calls=scenario.expected_tool_calls,
        tool_arg_checks=scenario.tool_arg_checks,    # e.g. create_schedule must be daily-8am
    ).run()
    # STATE layer: grade on the REAL persisted rows (source of truth), not just the args.
    state_ok = (scenario.state_check(rec.persisted_schedules)
                if scenario.state_check is not None else True)
    # KB state is the same layer over the Drive boundary's final files — a save_document
    # call with right-looking args that then errored persists nothing and must FAIL.
    if scenario.docs_check is not None:
        try:
            state_ok = state_ok and bool(scenario.docs_check(
                drive.files_by_title() if drive is not None else {}))
        except Exception:
            state_ok = False
    # FORBIDDEN layer: over-triggering. A scenario may declare tools that must NOT fire
    # (e.g. a plain memory fact must not touch the docs tools).
    forbidden_hit = [c["name"] for c in rec.tool_calls
                     if c["name"] in scenario.forbidden_tool_calls]
    # OUTPUT layer: grade the delivered REPLY text — for Q&A turns where the answer itself is the
    # deliverable (did the transcript/the searched fact actually surface), not just that a tool fired.
    # `sends_check` grades the same layer over the provider's recorded SENDS (the messages that
    # actually went out) — e.g. "no text send may carry a base64 blob". Both AND into output_ok.
    rec.sends = list(provider.sends)
    output_checked = scenario.output_check is not None or scenario.sends_check is not None
    try:
        output_ok = (scenario.output_check is None
                     or bool(scenario.output_check(rec.final_reply or "")))
        output_ok = output_ok and (scenario.sends_check is None
                                   or bool(scenario.sends_check(rec.sends)))
    except Exception:
        output_ok = False
    rec.grade = Grade(
        passed=result.passed and state_ok and output_ok and not forbidden_hit,
        calls=result,
        state_checked=scenario.state_check is not None or scenario.docs_check is not None,
        state_ok=state_ok,
        n_persisted_schedules=len(rec.persisted_schedules),
        output_checked=output_checked,
        output_ok=output_ok,
        forbidden_hit=forbidden_hit,
    )
    rec.passed = rec.grade.passed and not rec.timed_out and not rec.error
    return rec


def run_execution_scenario(scenario: "ExecutionScenario", model: Any, **_kw: Any) -> RunRecord:
    """Fire a dynamic schedule through the REAL execution path (`fire_schedule_by_id` ->
    the real agent via `respond` + `build_tools`) and grade the DELIVERED MESSAGE'S CONTENT
    (e.g. is the date correct), not tool calls. Hermetic only at trusted boundaries:
    in-memory DBs, router off, an injected sender (no WhatsApp). NOTE: production
    `respond()` returns text (not a RunOutput), so cost/tokens aren't captured here."""
    from backend.scheduling.jobs import fire_schedule_by_id
    from backend.scheduling.spec import ScheduleSpec

    settings.guardrail_enabled = False
    settings.tool_router_enabled = False      # don't make a router LLM call; use the full toolset
    settings.mai_db_url = _EVAL_AGNO_URL      # per-process isolated agno store (tmp/ is gitignored)
    # get_agno_db is process-cached (@lru_cache) and IGNORES mai_db_url after its first
    # call — another scenario earlier in the same run may have cached the default
    # (tmp/mai.db). Force it to rebuild OURS, or respond() runs against a stale DB.
    from backend.db.agno_db import get_agno_db
    get_agno_db.cache_clear()

    session, user = _make_sqlite_user()
    rec = RunRecord(
        scenario=scenario.name,
        model_id=str(getattr(model, "id", "?")),
        tool_call_limit=0,
        timeout_s=0,
        agent_input=scenario.action_text,
        system_prompt=build_system_prompt(user_display_name=user.display_name,
                                           user_timezone=user.timezone),
        expected_sdk_calls=[],
        expected_tool_calls=[],
    )
    captured: dict[str, str] = {}
    t0 = time.time()
    try:
        scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
        service = ScheduleService(scope, schedule_manager=_FakeScheduleManager())
        spec = ScheduleSpec(name=scenario.name, kind=scenario.kind, action_text=scenario.action_text,
                            schedule_type="recurring", timezone=user.timezone, cron=scenario.cron)
        sched = service.create(spec, target_chat=None)
        # Real fire; inject a sender to capture the delivered message (no WhatsApp).
        fire_schedule_by_id(session, sched.id,
                            sender=lambda _s, _u, text: captured.__setitem__("text", text))
        rec.final_reply = captured.get("text")
    except Exception as exc:
        rec.error = f"{type(exc).__name__}: {exc}"
    finally:
        rec.duration_s = round(time.time() - t0, 2)
        rec.persisted_schedules = _read_schedules(session, user)
        session.close()

    try:
        output_ok = bool(scenario.output_check(rec.final_reply or ""))
    except Exception:
        output_ok = False
    empty = LayeredResult(work=LayerVerdict([], [], [], []), action=LayerVerdict([], [], [], []))
    rec.grade = Grade(passed=output_ok, calls=empty, state_checked=False, state_ok=True,
                      n_persisted_schedules=len(rec.persisted_schedules),
                      output_checked=True, output_ok=output_ok)
    rec.passed = output_ok and not rec.error
    return rec


def _fresh_agno_db() -> Any:
    """Per-process agno HISTORY store, reset so each multi-turn run threads its OWN history with
    no cross-run bleed. Shared by every conversation runner: points mai_db_url at our isolated
    file, clears get_agno_db's @lru_cache (which otherwise ignores the url after its first call),
    deletes the file so the store starts empty, and returns the fresh db."""
    from backend.db.agno_db import get_agno_db

    settings.guardrail_enabled = False
    settings.mai_db_url = _EVAL_AGNO_URL
    get_agno_db.cache_clear()
    try:
        os.remove(_EVAL_AGNO_FILE)
    except OSError:
        pass
    return get_agno_db()


def _run_conv_turn(rec: RunRecord, *, model: Any, agno_db: Any, user: User, session: Session,
                   session_id: str, tools: list[Any], message: str) -> tuple[Any, list[dict], RunMeta]:
    """Run ONE turn of a multi-turn conversation and fold the UNIVERSAL captures into `rec` (the
    offered tool roster, the resolved system message, this turn's tool calls). The Agent is rebuilt
    each turn with the FULL production toolset (`tools`, from build_eval_tools) + agno-native skills
    + the prod system prompt — the production pattern (the router rebuilds tools per inbound) — with
    agno history threaded (add_history_to_context) so a later turn's 'it'/'yes' resolves to the
    earlier one. Returns (out, tool_calls, meta) so the caller reads its scenario-specific state."""
    skills_obj = None
    if settings.skill_mode == "stub":
        try:
            from backend.skills_store import agno_skills

            skills_obj = agno_skills(session, user)
        except Exception:
            skills_obj = None
    agent = Agent(name="Mai", model=model, tools=tools, skills=skills_obj, db=agno_db,
                  system_message=rec.system_prompt, user_id=user.agno_user_id,
                  session_id=session_id, add_history_to_context=True, num_history_runs=6,
                  tool_call_limit=DEFAULT_TOOL_CALL_LIMIT, markdown=False, telemetry=False)
    rec.tool_roster = _tool_roster(agent)
    out = agent.run(message, user_id=user.agno_user_id, session_id=session_id)
    rec.context_system = _resolved_system(out) or rec.context_system  # last turn = fullest
    tcs = _extract_tool_calls(out)
    rec.tool_calls.extend(tcs)
    return out, tcs, _run_meta(out)


def run_conv_scenario(scenario: "ConvScenario", model: Any, **_kw: Any) -> RunRecord:
    """The ONE multi-turn driver. Rebuilds the FULL production toolset per turn (build_eval_tools —
    the router's per-inbound pattern) under the prod system prompt, threading real agno history and
    grading the REAL persisted/delivered state. Substitutes only trusted boundaries (sqlite, faked
    WhatsApp transport, faked scheduler/e2b/replicate) via eval_runtime. EVERY conversation eval —
    schedule lifecycle, memory, error-report, onboarding — is a ConvScenario INSTANCE, differing only
    in `snapshot` (the per-turn source-of-truth state) and `grade`. The full surface is what kills the
    old single-tool drift: the model navigates the same toolset as production, so a wrong-tool /
    tool-selection failure is VISIBLE. ErrorReport's two-phase gate keys off the per-turn invoke_id,
    forwarded through build_eval_tools.

    Per turn we capture the real sends (provider.sends) + reply + tool names, plus the serialized
    snapshot under `log_key`. The grader gets (raw per-turn snapshots, the per-turn log records)."""
    from backend.agent.mai_agent import wa_session_id

    turns = [(t if isinstance(t, tuple) else (t, f"m{i}"))
             for i, t in enumerate(scenario.turns, 1)]
    # A scenario may put Mai in a group/contact chat (not the owner's DM) and hand it a directory
    # of contacts/groups to resolve against; both default to the owner's own DM (the common case).
    current_chat = scenario.current_chat or OWNER_JID
    session, user = _make_sqlite_user()
    session_id = wa_session_id(user, current_chat)
    # A conversation scenario may carry SDK/tool stubs (gen-AI gates) and canned chat
    # messages — the same contracts as single-turn Scenario, so gates work across turns.
    runner = RecordingRunner(dict(scenario.stubs))
    messages = {jid: [{"sender": m.get("sender"), "text": m.get("text"),
                       "from_me": m.get("from_me"), "media_type": m.get("media_type"),
                       "media_url": m.get("media_url"), "ts": None, "id": None}
                      for m in msgs]
                for jid, msgs in (scenario.messages or {}).items()}
    provider = RecordingProvider(
        chats=[{"name": user.display_name, "jid": OWNER_JID, "is_group": False}, *scenario.chats],
        messages=messages)
    # The trusted WHATSAPP CONTEXT block (which chat Mai was tagged in) rides on the FIRST turn only,
    # exactly as router._build_agent_input prepends it to the inbound command. Absent => bare message.
    if scenario.context and turns:
        first_msg, first_id = turns[0]
        turns[0] = (f"{scenario.context}\n\nMy instruction (@Mai): {first_msg}", first_id)
    rec = RunRecord(
        scenario=scenario.name, model_id=str(getattr(model, "id", "?")),
        tool_call_limit=DEFAULT_TOOL_CALL_LIMIT, timeout_s=0,
        agent_input=" / ".join(m for m, _ in turns),
        system_prompt="", expected_sdk_calls=[],
        expected_tool_calls=list(scenario.expected_tool_calls),
    )
    snapshots: list[Any] = []
    cost = 0.0
    out_tok = 0
    t0 = time.time()
    with eval_runtime(runner=runner, provider=provider) as agno_db:
        # The prod system prompt (base + beliefs + memory + skills + SDK ref), as single-turn does.
        rec.system_prompt = build_eval_system_prompt(user=user, session=session, query=turns[0][0])
        try:
            for i, (message, invoke_id) in enumerate(turns, 1):
                sends_start = len(provider.sends)
                tools = build_eval_tools(user, session, runner=runner, current_chat=current_chat,
                                         invoke_message_id=invoke_id, turn_state={})
                out, tcs, meta = _run_conv_turn(rec, model=model, agno_db=agno_db, user=user,
                                                session=session, session_id=session_id,
                                                tools=tools, message=message)
                snap = scenario.snapshot(session, user, agno_db) if scenario.snapshot else None
                snapshots.append(snap)
                cost += meta.cost or 0.0
                out_tok += meta.output_tokens or 0
                record = {
                    "turn": i, "message": message, "reply": getattr(out, "content", None),
                    "tool_calls": [c["name"] for c in tcs],
                    "sent": [f"to {s.get('to')}: {(s.get('text') or s.get('kind') or '').strip()}"
                             for s in provider.sends[sends_start:]],
                }
                if scenario.log_key and snap is not None:
                    record[scenario.log_key] = [asdict(x) if is_dataclass(x) else x for x in snap]
                rec.turns.append(record)
            rec.final_reply = rec.turns[-1]["reply"] if rec.turns else None
            if scenario.state_checked:  # final persisted state -> STATE block in the log
                rec.persisted_schedules = snapshots[-1] if snapshots else []
        except Exception as exc:
            rec.error = f"{type(exc).__name__}: {exc}"
        finally:
            rec.duration_s = round(time.time() - t0, 2)
            session.close()

    rec.n_tool_calls = len(rec.tool_calls)
    rec.run_meta = RunMeta(output_tokens=out_tok or None, cost=cost or None)
    try:
        passed = bool(scenario.grade(snapshots, rec.turns))
    except Exception:
        passed = False
    empty = LayeredResult(work=LayerVerdict([], [], [], []), action=LayerVerdict([], [], [], []))
    n_persisted = len(snapshots[-1]) if (scenario.state_checked and snapshots and snapshots[-1]) else 0
    rec.grade = Grade(passed=passed, calls=empty, state_checked=scenario.state_checked,
                      state_ok=passed, n_persisted_schedules=n_persisted,
                      output_checked=not scenario.state_checked, output_ok=passed)
    rec.passed = passed and not rec.error
    return rec


def _read_memories(agno_db: Any, agno_user_id: str) -> list[str]:
    """The REAL persisted user memories (the source of truth for memory evals) as text."""
    try:
        mems = agno_db.get_user_memories(user_id=agno_user_id) or []
        return [getattr(m, "memory", "") for m in mems if getattr(m, "memory", "")]
    except Exception:
        return []


def _render_text(rec: RunRecord) -> str:
    """A human-readable transcript of the run for eyeballing in the logs dir."""
    L: list[str] = []
    L.append(f"SCENARIO: {rec.scenario}")
    L.append(f"MODEL: {rec.model_id}   tool_call_limit={rec.tool_call_limit}   "
             f"timeout={rec.timeout_s}s")
    status = rec.grade.status if rec.grade else "?"
    L.append(f"RESULT: {'PASS' if rec.passed else 'FAIL'}   "
             f"status={status}   duration={rec.duration_s}s"
             + ("   TIMED_OUT" if rec.timed_out else "")
             + (f"   ERROR={rec.error}" if rec.error else ""))
    # The three layers of abstraction, each counted on its own.
    L.append(f"LAYERS:  tool_calls={rec.n_tool_calls} (code_exec={rec.n_code_exec_calls}, "
             f"wasted={rec.n_wasted_calls})   sdk_calls={rec.n_sdk_calls}   "
             f"reasoning_steps={rec.reasoning.n_reasoning_steps} "
             f"(chars={rec.reasoning.reasoning_chars}, tokens={rec.run_meta.reasoning_tokens})")
    L.append(f"TOKENS: {rec.run_meta.input_tokens} in / {rec.run_meta.output_tokens} out"
             + (f"   COST=${rec.run_meta.cost:.4f}" if rec.run_meta.cost is not None else ""))
    if rec.grade and rec.grade.state_checked:
        L.append(f"STATE:  persisted_schedules={rec.grade.n_persisted_schedules}  "
                 f"state_ok={rec.grade.state_ok}")
        for s in rec.persisted_schedules:
            if isinstance(s, ScheduleSnapshot):
                L.append(f"    #{s.id} {s.name!r} kind={s.kind} type={s.schedule_type} "
                         f"cron={s.cron!r} run_at={s.run_at} tz={s.timezone} active={s.active}")
            else:  # conv scenarios may snapshot non-schedule state (e.g. memory strings)
                L.append(f"    {s!r}")
    if rec.grade and rec.grade.output_checked:
        L.append(f"OUTPUT: output_ok={rec.grade.output_ok}  (graded on the delivered message)")
    L.append("")
    if rec.turns:
        L.append("=== CONVERSATION (multi-turn) ===")

        def _tc(c):  # tool_calls are dicts (error-report) or plain names (memory)
            if isinstance(c, dict):
                return f"{c['name']}(confirmed={c['confirmed']})" if "confirmed" in c else str(c["name"])
            return str(c)

        for t in rec.turns:
            tcs = ", ".join(_tc(c) for c in t.get("tool_calls", [])) or "—"
            mid = f"  (msg_id={t['invoke_message_id']})" if "invoke_message_id" in t else ""
            L.append(f"--- turn {t['turn']}{mid} ---")
            L.append(f"  USER: {t['message']}")
            L.append(f"  tools: {tcs}")
            if "reports_after" in t:
                L.append(f"  reports after: {[r['status'] for r in t['reports_after']] or '(none)'}")
            if "memories_after" in t:
                L.append(f"  memories after: {t['memories_after'] or '(none)'}")
            if "schedules_after" in t:
                active = [f"#{s['id']} {s['cron'] or s['run_at']}"
                          for s in t["schedules_after"] if s["active"]]
                L.append(f"  schedules after (active): {active or '(none)'}")
            for s in t.get("sent", []):  # the EXACT text the tool delivered (preview / confirm)
                L.append("  TOOL DELIVERED (verbatim):")
                L.extend(f"    | {ln}" for ln in s.splitlines())
            L.append(f"  MAI: {(t['reply'] or '').strip()[:200]}")
        L.append("")
    L.append("=== INPUT (agent_input) ===")
    L.append(rec.agent_input)
    L.append("")
    if rec.assistant_trace:
        L.append("=== MODEL THINKING + NARRATION (per assistant step) ===")
        for st in rec.assistant_trace:
            header = f"--- step {st.index + 1}"
            if st.tool_calls:
                header += f"  → calls: {', '.join(st.tool_calls)}"
            L.append(header + " ---")
            if st.reasoning:
                L.append("  THINKING: " + st.reasoning.replace("\n", "\n            "))
            if st.content:
                L.append("  SAYS:     " + st.content.replace("\n", "\n            "))
        L.append("")
    if rec.reasoning.steps:
        L.append("=== AGNO REASONING STEPS (structured) ===")
        for j, s in enumerate(rec.reasoning.steps, 1):
            L.append(f"  {j}. {s.title}"
                     + (f"  (confidence={s.confidence})" if s.confidence is not None else ""))
            if s.reasoning:
                L.append(f"     reasoning: {s.reasoning}")
            if s.action:
                L.append(f"     action:    {s.action}")
            if s.result:
                L.append(f"     result:    {s.result}")
        L.append("")
    for i, s in enumerate(rec.scripts, 1):
        L.append(f"=== code_exec #{i} ({'ok' if s['ok'] else 'ERROR'}) ===")
        L.append("--- script ---")
        L.append(s["code"])
        L.append("--- mai.* SDK calls ---")
        for c in s["sdk_calls"]:
            L.append(f"    mai.{c['fn']}({c['args']}) -> {c['returned']!r}")
        L.append("--- script output (what the model saw back) ---")
        L.append((s["output"] or "").strip())
        L.append("")
    L.append("=== FINAL REPLY ===")
    L.append(rec.final_reply or "(none)")
    L.append("")
    L.append("=== GRADE ===")
    grade_dict = asdict(rec.grade) if rec.grade else {}
    L.append(json.dumps(grade_dict, indent=2, ensure_ascii=False, default=str))
    L.append("")
    # Everything the agent actually got, captured so 'did the model see X?' is grep-able, not
    # guesswork. `system` is the RESOLVED message agno sent (ground truth) — falls back to what
    # we passed only if the run never returned (timeout/error). `tools` is the direct-tool surface;
    # the mai.* SDK lives ONLY inside the system prompt's reference (if it reached it).
    L.append("=== CONTEXT THE MODEL RECEIVED ===")
    sys_text = rec.context_system or rec.system_prompt or "(none)"
    resolved = "resolved (sent)" if rec.context_system else "AS-PASSED (run did not return a system message)"
    L.append(f"TOOLS OFFERED ({len(rec.tool_roster)}):")
    for t in rec.tool_roster:
        desc = t.get("description") or ""
        L.append(f"  - {t.get('name')}" + (f" — {desc}" if desc else ""))
    if not rec.tool_roster:
        L.append("  (not captured for this run path)")
    L.append(f"SYSTEM PROMPT [{resolved}, {len(sys_text)} chars]:")
    L.append(sys_text)
    return "\n".join(L)


def write_log(rec: RunRecord, run_index: int | None = None,
              logs_dir: Path = LOGS_DIR) -> Path:
    """Persist the run as JSON (machine) + .txt (human) under evals/logs/. Returns the
    JSON path. Uses a real wall-clock timestamp for the filename."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    suffix = f"__run{run_index}" if run_index is not None else ""
    base = logs_dir / f"{ts}__{rec.scenario}{suffix}"
    json_path = base.with_suffix(".json")
    # default=str serializes the typed snapshot's datetime fields (run_at).
    json_path.write_text(json.dumps(rec.to_dict(), indent=2, ensure_ascii=False, default=str),
                         encoding="utf-8")
    base.with_suffix(".txt").write_text(_render_text(rec), encoding="utf-8")
    return json_path
