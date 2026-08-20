"""Error-report toolkit — the user's opt-in "this was wrong" button.

Two-phase, ENFORCED IN CODE (the prompt isn't enough — GLM one-shots `confirmed=True` and
paraphrases previews):

  Phase 1 — `report_problem(..., confirmed=False)`: capture the turn deterministically, stage a
            *pending* ErrorReport (hidden from operators), and **send the full preview into the
            chat itself** so the user sees exactly what will be saved (the model can't shorten it).
  Phase 2 — `report_problem(..., confirmed=True)`: files ONLY if a pending preview exists that was
            staged in an EARLIER turn (a different inbound message ⇒ the user actually approved).
            A confirm with no prior preview, or in the same turn, just re-shows the preview.

The filed report is the staged snapshot (saved == shown). The reproducible spine is captured by
`backend.reports.capture_turn`; the model contributes only its interpretation. The full Agno
session is stored ENCRYPTED as the replay fixture, never shown.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from sqlalchemy.orm import Session

from agno.tools import Toolkit

from backend.crypto import encrypt
from backend.db.scoped import TenantScope
from backend.db.tenancy import ErrorReport, User
from backend.logging_config import get_logger
from backend.reports import Capture, capture_turn

logger = get_logger(__name__)


def _safe_encrypt(plaintext: str | None) -> str | None:
    """Encrypt for storage; never block a report if the key is unavailable."""
    try:
        return encrypt(plaintext) if plaintext else None
    except Exception as exc:  # pragma: no cover
        logger.warning("error_report.encrypt_failed", extra={"ctx_error": str(exc)})
        return None


def _preview(cap: Capture, *, problem: str, expected: str, notes: str) -> str:
    """The exact readable content to be saved — shown to the user before they approve.

    The fixed chrome is in English; the captured messages and Mai's read are kept
    verbatim in whatever language the conversation is.
    """
    lines = ["🐞 Error report — this is everything that will be saved:", ""]
    lines.append(f"── Messages (the last {len(cap.transcript)} in this chat) ──")
    if cap.transcript:
        for m in cap.transcript:
            who = "Mai" if m.role == "assistant" else "User"
            lines.append(f"[{who}] {m.text}")
    else:
        lines.append("(I couldn't read the recent messages — they won't be attached)")
    if cap.schedule:
        lines += ["", "── Schedule & run ──",
                  f'• Schedule: "{cap.schedule.action_text}" '
                  f"(cron {cap.schedule.cron}, {cap.schedule.kind})"]
    if cap.tool_calls:
        names = ", ".join(t.name for t in cap.tool_calls if t.name) or "—"
        lines.append(f"• Tools run: {names}")
    lines += ["", "── My understanding ──", f"• Problem: {problem}"]
    if expected:
        lines.append(f"• Expected: {expected}")
    if notes:
        lines.append(f"• Notes: {notes}")
    lines += ["", "Reply *yes* to save the report, or tell me what to change."]
    return "\n".join(lines)


class ErrorReportTools(Toolkit):
    """Files a user-approved error report; see the module docstring. Closes over the caller's
    `TenantScope`, so it can only ever read/write reports for that user. Sends the preview and
    the filed confirmation into the chat directly, so they appear verbatim."""

    def __init__(self, *, user: User, session: Session, current_chat: str | None = None,
                 invoke_message_id: str | None = None, device_id: str | None = None,
                 turn_state: dict | None = None) -> None:
        self.user = user
        self.session = session
        self.current_chat = current_chat
        self.invoke_message_id = invoke_message_id
        self.device_id = device_id
        self.turn_state = turn_state if turn_state is not None else {}
        self.scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
        super().__init__(
            name="error_report_tools",
            tools=[self.report_problem],
            instructions=(
                "Use report_problem when the user tells you that you got something WRONG and wants "
                "it reported / logged / flagged.\n"
                "Also nudge proactively: if YOU realize you clearly got something wrong — gave bad "
                "advice, failed a task the user reacted to — OFFER once to log it for the team (in "
                "the user's language), and file only after they approve via the same flow. Don't "
                "over-offer on every small uncertainty.\n"
                "You only describe the PROBLEM — the recent messages, which tools ran, the schedule "
                "and the full session are captured AUTOMATICALLY, and I show the user the full "
                "preview MYSELF, so don't restate the conversation or write your own preview. If the "
                "bug is about a reminder/schedule, pass its name as schedule_name.\n"
                "FLOW (the system enforces it): call report_problem WITHOUT confirmed → I show the "
                "user the preview and ask them to approve. After they reply 'yes' IN A NEW MESSAGE, "
                "call report_problem again with the SAME fields and confirmed=True — that's the only "
                "way it gets filed. Don't write anything else after calling it; I handle the chat "
                "messages. If they decline, drop it.\n"
                "Fields: problem = your read of what went wrong; expected = what should have happened "
                "(optional); notes = any extra useful observation (optional); schedule_name = the "
                "schedule's name if it's a schedule bug (optional)."
            ),
            add_instructions=True,
        )

    # -- session / capture --------------------------------------------------
    def _session_id(self) -> str:
        from backend.agent.mai_agent import wa_session_id

        return wa_session_id(self.user, self.current_chat)

    def _capture(self, schedule_hint: str = "") -> Capture:
        try:
            return capture_turn(user=self.user, session=self.session,
                                current_chat=self.current_chat, session_id=self._session_id(),
                                schedule_hint=schedule_hint, device_id=self.device_id)
        except Exception as exc:  # capture must never break the turn
            logger.warning("error_report.capture_failed", extra={"ctx_error": str(exc)})
            return Capture(surface="turn", chat_type="", contact_number=None,
                           chat_jid=self.current_chat, command="", transcript=[], tool_calls=[],
                           schedule=None, model_id=None, git_rev=None, raw_session_json=None)

    # -- direct delivery (so the user sees the preview/result verbatim) ------
    def _send(self, text: str) -> bool:
        """Send `text` straight into the chat; True if delivered. Falls back to the model relaying."""
        if not (self.device_id and self.current_chat):
            return False
        try:
            from backend.whatsapp import get_provider

            p = get_provider()
            if not hasattr(p, "send_text"):
                return False
            p.send_text(self.current_chat, text, session_id=self.device_id)
            self.turn_state["replied_in_chat"] = True  # router skips the model's redundant reply
            return True
        except Exception as exc:
            logger.warning("error_report.send_failed", extra={"ctx_error": str(exc)})
            return False

    def _deliver_preview(self, preview: str, *, model_note: str) -> str:
        """Send the preview once per turn; tell the model what happened (don't let it re-send)."""
        if self.turn_state.get("_report_preview_sent"):
            return model_note
        if self._send(preview):
            self.turn_state["_report_preview_sent"] = True
            return model_note
        return preview  # fallback: the model relays it (per the toolkit instructions)

    # -- pending snapshot store (the gate) ----------------------------------
    def _pending_select(self):
        return self.scope.select(ErrorReport).where(
            ErrorReport.status == "pending", ErrorReport.chat_jid == self.current_chat)

    def _find_pending(self) -> ErrorReport | None:
        try:
            return self.session.scalars(
                self._pending_select().order_by(ErrorReport.created_at.desc())).first()
        except Exception:
            return None

    def _clear_pending(self, *, keep_id: int | None = None) -> None:
        try:
            for r in self.session.scalars(self._pending_select()).all():
                if keep_id is None or r.id != keep_id:
                    self.session.delete(r)
        except Exception:
            pass

    def _build_row(self, cap: Capture, *, problem: str, expected: str, notes: str,
                   status: str) -> ErrorReport:
        return ErrorReport(
            command=cap.command or "(unknown)",
            summary=problem,
            surface=cap.surface,
            chat_type=cap.chat_type or None,
            contact_number=cap.contact_number,
            chat_jid=cap.chat_jid,
            invoke_message_id=self.invoke_message_id,
            transcript_json=json.dumps([asdict(m) for m in cap.transcript], default=str),
            tool_calls_json=json.dumps([asdict(t) for t in cap.tool_calls], default=str),
            schedule_json=(json.dumps(asdict(cap.schedule), default=str) if cap.schedule else None),
            model_id=cap.model_id,
            git_rev=cap.git_rev,
            mai_expected=expected or None,
            mai_notes=notes or None,
            raw_session_enc=_safe_encrypt(cap.raw_session_json),
            status=status,
        )

    def _stage_pending(self, cap: Capture, *, problem: str, expected: str, notes: str) -> None:
        """Replace any prior pending preview for this chat with the current snapshot."""
        self._clear_pending()
        try:
            self.scope.add(self._build_row(cap, problem=problem, expected=expected, notes=notes,
                                           status="pending"))
            self.scope.commit()
        except Exception as exc:
            logger.warning("error_report.stage_failed", extra={"ctx_error": str(exc)})

    def _same_turn(self, pending: ErrorReport) -> bool:
        """True when the pending preview was staged by THIS same inbound message (no approval yet)."""
        return bool(pending.invoke_message_id and self.invoke_message_id
                    and pending.invoke_message_id == self.invoke_message_id)

    def _file(self, pending: ErrorReport) -> str:
        pending.status = "open"
        self._clear_pending(keep_id=pending.id)
        try:
            self.scope.commit()
        except Exception as exc:
            logger.warning("error_report.save_failed", extra={"ctx_error": str(exc)})
            return f"I couldn't file that report just now ({exc}). We can try again."
        logger.info("error_report.filed",
                    extra={"ctx_user_id": self.user.id, "ctx_report_id": pending.id,
                           "ctx_surface": pending.surface})
        confirm = (f"✅ Saved — report #{pending.id}. I saved exactly what you approved. "
                   "The team can replay it to make sure the bug gets fixed. Thanks 🙏")
        if self._send(confirm):
            return "Filed and confirmed to the user in the chat. Don't write anything else."
        return confirm

    # -- the tool -----------------------------------------------------------
    def report_problem(self, problem: str, expected: str = "", notes: str = "",
                       schedule_name: str = "", confirmed: bool = False) -> str:
        """File an opt-in error report about a Mai turn that went wrong (user-approved only).

        Two phases, enforced by the system: call WITHOUT confirmed and I show the user the full
        preview; after they approve in a NEW message, call again with confirmed=True. A confirmed
        call with no prior approved preview only re-shows the preview — it can't file. You fill
        only your interpretation; the conversation, tools, schedule and session are captured
        automatically, and I deliver the preview to the chat myself.

        Args:
            problem: your read of what went wrong (required).
            expected: what should have happened instead (optional).
            notes: any extra useful observation — a hypothesis, the suspect step (optional).
            schedule_name: if the bug is about a reminder/schedule, its name (optional).
        """
        problem = (problem or "").strip()
        expected = (expected or "").strip()
        notes = (notes or "").strip()
        if not problem:
            return "Tell me in one line what went wrong and I'll prepare the report."

        # Phase 2: a confirm only files a pending preview staged in an EARLIER turn.
        if confirmed:
            pending = self._find_pending()
            if pending is not None and not self._same_turn(pending):
                return self._file(pending)
            # No prior preview (or same-turn) → fall through and (re)show the preview instead.

        # Phase 1 (or a confirm that wasn't yet approvable): stage + show the full preview.
        cap = self._capture(schedule_name)
        self._stage_pending(cap, problem=problem, expected=expected, notes=notes)
        preview = _preview(cap, problem=problem, expected=expected, notes=notes)
        note = ("I showed the user the full report preview and asked them to approve. WAIT for their "
                "'yes' in a NEW message, then call report_problem with the SAME fields and "
                "confirmed=True. Don't write anything else now.")
        return self._deliver_preview(preview, model_note=note)
