"""Deterministic capture for error reports.

Reads trusted sources — the Agno session store (per-chat history + the failing run's tool
calls), the Schedule rows, and the chat metadata — and builds typed values. The report's
reproducible spine therefore never depends on the model's self-report; the model only adds
its interpretation (and may *point* at the relevant schedule by name, which we resolve here).
Everything is defensive: capture must never break a turn, so a failed read degrades to a
partial `Capture` rather than raising.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from backend.db.scoped import TenantScope
from backend.db.tenancy import User
from backend.logging_config import get_logger

logger = get_logger(__name__)

TRANSCRIPT_LIMIT = 10
_MSG_MAX = 240                    # truncate each transcript message so one can't balloon the preview
_RESULT_MAX = 600                 # truncate tool results in the readable capture
_INSTRUCTION_MARK = "(@Mai):"    # the trusted-context wrapper ends here; the rest is the command
# Inbound deploy-rev env vars (Railway/Heroku set these; the container has no .git).
_REV_ENV = ("RAILWAY_GIT_COMMIT_SHA", "SOURCE_VERSION", "GIT_COMMIT", "RAILWAY_GIT_COMMIT")
# Substrings that mark a message as a "report this" meta-request, not the bug-triggering
# command. Input matchers, not UI strings — the non-English entries are intentional
# (users write in any language; extend with your users' languages as needed).
_REPORT_HINTS = ("report a bug", "report an error", "report this", "report the", "report a problem",
                 "log a bug", "file a bug", "דווח", "תדווח", "תקלה", "שגיאה")


@dataclass(frozen=True)
class CapturedMessage:
    role: str            # user | assistant
    text: str
    ts: str = ""


@dataclass(frozen=True)
class CapturedToolCall:
    name: str
    args: dict = field(default_factory=dict)   # open by design — arbitrary LLM tool-call args
    result: str | None = None
    error: bool = False


@dataclass(frozen=True)
class CapturedSchedule:
    action_text: str
    cron: str | None
    kind: str
    schedule_type: str


@dataclass(frozen=True)
class Capture:
    """The deterministic spine of an error report, assembled from trusted sources."""

    surface: str                       # "turn" | "schedule"
    chat_type: str                     # "self_dm" | "direct" | "group"
    contact_number: str | None
    chat_jid: str | None
    command: str                       # the triggering request (schedule action_text, or last msg)
    transcript: list[CapturedMessage]
    tool_calls: list[CapturedToolCall]
    schedule: CapturedSchedule | None
    model_id: str | None
    git_rev: str | None
    raw_session_json: str | None       # full session to_dict() JSON (the caller encrypts it)


def _text(content) -> str:
    """Coerce a message/tool content (str, list of parts, None) into text."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                t = p.get("text") or p.get("content")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts).strip()
    return ""


def _dedupe(text: str) -> str:
    """Collapse an exactly-doubled message ("X X" / "XX") to a single copy — the gateway/router
    sometimes delivers the command twice."""
    t = (text or "").strip()
    parts = t.split()
    if len(parts) >= 2 and len(parts) % 2 == 0:
        h = len(parts) // 2
        if parts[:h] == parts[h:]:
            return " ".join(parts[:h])
    n = len(t)
    if n >= 2 and n % 2 == 0 and t[: n // 2].strip() == t[n // 2:].strip():
        return t[: n // 2].strip()
    return t


def _clean_user(text: str) -> str:
    """Drop the trusted WHATSAPP CONTEXT wrapper, keeping the user's actual instruction."""
    if _INSTRUCTION_MARK in text:
        text = text.rsplit(_INSTRUCTION_MARK, 1)[-1]
    return _dedupe(text.strip())


def _is_report_request(text: str) -> bool:
    low = (text or "").lower()
    return len(low) < 60 and any(h in low for h in _REPORT_HINTS)


def _truncate(text: str, limit: int = _MSG_MAX) -> str:
    t = (text or "").strip().replace("\n", " ")
    return t if len(t) <= limit else t[:limit].rstrip() + "…"


def _tidy_transcript(msgs: list[CapturedMessage]) -> list[CapturedMessage]:
    """One-line + truncate each message and collapse consecutive repeats. We deliberately do NOT
    drop Mai's own report previews/confirmations: truncation already stops a nested preview from
    ballooning, and dropping them made the last-N window skip back to OLD messages whenever reports
    were filed back-to-back (the recent chat is mostly previews) — which read as staleness even
    though the gateway fetch was perfectly fresh."""
    out: list[CapturedMessage] = []
    for m in msgs:
        text = _truncate(m.text)
        if not text:
            continue
        if out and out[-1].role == m.role and out[-1].text == text:
            continue  # collapse a message repeated back-to-back
        out.append(CapturedMessage(role=m.role, text=text, ts=m.ts))
    return out[-TRANSCRIPT_LIMIT:]


def _git_rev() -> str | None:
    for var in _REV_ENV:
        v = os.environ.get(var)
        if v:
            return v[:12]
    try:
        root = Path(__file__).resolve().parents[1]
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=3)
        return (out.stdout or "").strip() or None
    except Exception:
        return None


def _chat_type(chat_jid: str | None, owner_number: str | None) -> str:
    if not chat_jid:
        return "self_dm"
    if chat_jid.endswith("@g.us"):
        return "group"
    digits = chat_jid.split("@", 1)[0].lstrip("+")
    owner = (owner_number or "").lstrip("+")
    return "self_dm" if digits and digits == owner else "direct"


def _contact_number(chat_jid: str | None) -> str | None:
    if not chat_jid or chat_jid.endswith("@g.us"):
        return None
    return chat_jid.split("@", 1)[0] or None


def _relevant_schedule(scope: TenantScope, chat_jid: str | None,
                       hint: str = "") -> CapturedSchedule | None:
    """The schedule this report is about. Mai can name it (`hint`) — resolved against the user's
    real Schedule rows; otherwise fall back to an active schedule firing into THIS chat."""
    try:
        from backend.scheduling.models import Schedule

        rows = [s for s in scope.all(Schedule) if getattr(s, "active", False)]
    except Exception:
        return None
    if not rows:
        return None
    pick = None
    h = (hint or "").strip().lower()
    if h:
        for s in rows:
            name = (getattr(s, "name", "") or "").lower()
            action = (getattr(s, "action_text", "") or "").lower()
            if (name and (h in name or name in h)) or (action and h in action):
                pick = s
                break
    if pick is None and chat_jid:
        for s in rows:
            if getattr(s, "target_chat", None) == chat_jid:
                pick = s
                break
    if pick is None:
        return None
    return CapturedSchedule(action_text=getattr(pick, "action_text", "") or "",
                            cron=getattr(pick, "cron", None),
                            kind=getattr(pick, "kind", "") or "",
                            schedule_type=getattr(pick, "schedule_type", "") or "")


def _read_agno_session(session_id: str, agno_user_id: str) -> dict | None:
    """Pull per-chat history + the failing run's tool calls from the Agno session store.
    Returns a dict, or None on any failure (the caller then falls back)."""
    try:
        from backend.db.agno_db import get_agno_db

        db = get_agno_db()
        sess = None
        try:
            sess = db.get_session(session_id=session_id, user_id=agno_user_id, deserialize=True)
        except Exception:
            sess = None
        if sess is None:
            sess = db.get_session(session_id=session_id, deserialize=True)
        if sess is None:
            return None

        runs = list(getattr(sess, "runs", None) or [])
        # The current (report) turn isn't a persisted run yet, so the last run is the failing one.
        last = runs[-1] if runs else None

        tool_calls: list[CapturedToolCall] = []
        for t in (getattr(last, "tools", None) or []):
            res = _text(getattr(t, "result", None))
            tool_calls.append(CapturedToolCall(
                name=getattr(t, "tool_name", "") or "",
                args=getattr(t, "tool_args", None) or {},
                result=(res[:_RESULT_MAX] or None),
                error=bool(getattr(t, "tool_call_error", None)),
            ))

        # The triggering command: the most recent user message that ISN'T a "report this" request.
        command, fallback = "", ""
        for m in reversed(getattr(last, "messages", None) or []):
            if getattr(m, "role", None) != "user":
                continue
            cand = _clean_user(_text(getattr(m, "content", None)))
            if not cand:
                continue
            fallback = fallback or cand
            if not _is_report_request(cand):
                command = cand
                break
        command = command or fallback

        # Use the LAST COMPLETED run's messages: it already carries the recent conversation in
        # order (add_history_to_context), without the cross-run duplication you'd get from
        # concatenating every run — which is what dragged in old/repeated messages. The current
        # (report) turn isn't persisted yet, so this ends at the turn the bug happened in.
        transcript: list[CapturedMessage] = []
        for m in (getattr(last, "messages", None) or []):
            role = getattr(m, "role", None)
            if role not in ("user", "assistant"):
                continue
            txt = _text(getattr(m, "content", None))
            if not txt:
                continue
            transcript.append(CapturedMessage(
                role=role, text=(_clean_user(txt) if role == "user" else txt),
                ts=str(getattr(m, "created_at", "") or "")))
        transcript = _tidy_transcript(transcript)

        model_id = getattr(last, "model", None) or getattr(last, "model_id", None)
        try:
            raw = json.dumps(sess.to_dict(), default=str)
        except Exception:
            raw = None

        return {"transcript": transcript, "tool_calls": tool_calls, "command": command,
                "model_id": model_id if isinstance(model_id, str) else None, "raw": raw}
    except Exception as exc:
        logger.warning("error_report.session_read_failed", extra={"ctx_error": str(exc)})
        return None


def _chat_transcript(device_id: str | None, chat_jid: str | None) -> list[CapturedMessage]:
    """The ACTUAL last-N messages of the chat — exactly what read_chat / Mai's @-tag context get.
    Order-AGNOSTIC: we SORT by timestamp and take the newest N, so it does not matter whether the
    gateway returns oldest->newest or newest->oldest (the latter would otherwise make a tail slice
    grab the OLDEST N — a second cause of the stale window). We only truncate each message — no
    dropping, dedup, or noise filtering — so the report never skips back. [] if the gateway is down."""
    if not (device_id and chat_jid):
        return []
    try:
        from backend.whatsapp import get_provider

        p = get_provider()
        if not hasattr(p, "get_chat_messages"):
            return []
        msgs = list(p.get_chat_messages(device_id, chat_jid, limit=TRANSCRIPT_LIMIT) or [])
    except Exception:
        return []

    def _ts_key(m: dict):
        s = str(m.get("ts") or "")
        return (0, int(s)) if s.isdigit() else (1, s)  # epoch ints OR rfc3339 strings, consistent per chat

    try:
        msgs.sort(key=_ts_key)  # -> oldest first, regardless of the gateway's order
    except Exception:
        pass
    out: list[CapturedMessage] = []
    for m in msgs[-TRANSCRIPT_LIMIT:]:  # the newest N, oldest-first; KEEP every message
        text = (m.get("text") or "").strip()
        if not text and m.get("media_type"):
            text = f"[{m['media_type']}]"
        if not text:
            continue
        out.append(CapturedMessage(role=("assistant" if m.get("from_me") else "user"),
                                   text=_truncate(text), ts=str(m.get("ts") or "")))
    return out


def _fallback_transcript(scope: TenantScope) -> list[CapturedMessage]:
    """Only if the chat can't be read: the user-facing ChatMessage log — last N, truncated, no filter."""
    try:
        from backend.chat_history import get_history

        page = get_history(scope, limit=TRANSCRIPT_LIMIT)
        return [CapturedMessage(role=m["role"], text=_truncate(m["text"]), ts=m.get("created_at", ""))
                for m in page.get("messages", [])]
    except Exception:
        return []


def capture_turn(*, user: User, session: Session, current_chat: str | None,
                 session_id: str, schedule_hint: str = "", device_id: str | None = None) -> Capture:
    """Assemble the deterministic spine of a report from trusted sources. Never raises."""
    scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
    agno = _read_agno_session(session_id, user.agno_user_id)
    # Transcript = the ACTUAL last-N chat messages (same as read_chat), taken as-is. We deliberately
    # do NOT fall back to the Agno session: that snapshot is captured at TURN START and doesn't
    # update mid-conversation, so it read ~30 min stale. Gateway, else the DB log, else empty.
    transcript = _chat_transcript(device_id, current_chat) or _fallback_transcript(scope)
    if agno:
        tool_calls, command, model_id, raw = (agno["tool_calls"], agno["command"],
                                              agno["model_id"], agno["raw"])
    else:
        tool_calls, command, model_id, raw = [], "", None, None
    sched = _relevant_schedule(scope, current_chat, schedule_hint)
    if sched and sched.action_text:
        # For a schedule bug the action_text IS the reproducible command.
        command = sched.action_text
    return Capture(
        surface="schedule" if sched else "turn",
        chat_type=_chat_type(current_chat, user.whatsapp_number),
        contact_number=_contact_number(current_chat),
        chat_jid=current_chat,
        command=command,
        transcript=transcript,
        tool_calls=tool_calls,
        schedule=sched,
        model_id=model_id,
        git_rev=_git_rev(),
        raw_session_json=raw,
    )
