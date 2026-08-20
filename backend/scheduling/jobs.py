"""Schedule firing — executes a due schedule inside its owner's tenant context.

This is the code the AgentOS poller (and tests) call. It is:
- **Tenant-scoped**: it loads the owning user and runs entirely as that tenant;
  a job for User1 can only ever touch User1's data.
- **Idempotent**: a double-poll within the same minute does not double-fire;
  one-off jobs deactivate after firing so they never repeat.
- **Static or dynamic**: static sends the stored message; dynamic re-runs the
  agent with the stored instruction and sends the generated result.

`sender` and `agent_runner` are injectable so this is testable with no network.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.orm import Session

from backend.db.scoped import TenantScope
from backend.db.tenancy import User
from backend.logging_config import get_logger, new_trace_id, set_tenant
from backend.scheduling.models import Schedule

logger = get_logger(__name__)

# sender(session, user, text) -> None ; agent_runner(user, instruction, session) -> str
Sender = Callable[[Session, User, str], None]
AgentRunner = Callable[[User, str, Session], str]

# Delimiter the agent/action_text can insert to split the response into
# multiple outbound WhatsApp messages.  Surrounding newlines are required so
# the split never breaks in the middle of a paragraph.
MULTI_MSG_DELIMITER = "\n---MAI-MSG---\n"

# Hard cap: never send more than this many messages from one scheduled fire to
# guard against accidental or malicious runaway.
MULTI_MSG_CAP = 10


def _split_messages(text: str, cap: int = MULTI_MSG_CAP) -> list[str]:
    """Split *text* on MULTI_MSG_DELIMITER into 1-*cap* non-empty segments.

    If no delimiter is present the original text is returned as a single-item
    list, preserving the existing single-message behaviour exactly.  Segments
    beyond *cap* are dropped and a warning is logged.
    """
    if MULTI_MSG_DELIMITER not in text:
        return [text]
    parts = [p.strip() for p in text.split(MULTI_MSG_DELIMITER)]
    parts = [p for p in parts if p]  # drop blank segments
    if not parts:
        return [text]
    if len(parts) > cap:
        logger.warning(
            "schedule.multi_msg_capped",
            extra={"ctx_total": len(parts), "ctx_cap": cap},
        )
        parts = parts[:cap]
    return parts


_MEDIA_RE = re.compile(r"\[\[(audio|image|video|file):\s*([^\]|]+?)(?:\s*\|\s*([^\]]*))?\]\]")


def _parse_stream(text: str, cap: int = MULTI_MSG_CAP) -> list[dict]:
    """Ordered segments: {"type":"text","text":..} or {"type":"media","kind":..,"url":..,"caption":..}.

    Text between/around media markers is further split on MULTI_MSG_DELIMITER, stripped,
    and blank chunks are dropped. Preserves order. No markers and no delimiter => a single
    text segment (exact current behavior). Caps total segments at *cap*.
    """
    if not _MEDIA_RE.search(text) and MULTI_MSG_DELIMITER not in text:
        return [{"type": "text", "text": text}]

    segments: list[dict] = []
    last_index = 0

    for m in _MEDIA_RE.finditer(text):
        # Handle the text chunk before this marker.
        chunk = text[last_index:m.start()]
        for part in chunk.split(MULTI_MSG_DELIMITER):
            stripped = part.strip()
            if stripped:
                segments.append({"type": "text", "text": stripped})
        # Append the media segment.
        segments.append({
            "type": "media",
            "kind": m.group(1),
            "url": m.group(2).strip(),
            "caption": (m.group(3) or "").strip() or None,
        })
        last_index = m.end()

    # Handle any trailing text after the last marker.
    trailing = text[last_index:]
    for part in trailing.split(MULTI_MSG_DELIMITER):
        stripped = part.strip()
        if stripped:
            segments.append({"type": "text", "text": stripped})

    # Drop blank text segments (defensive).
    segments = [s for s in segments if not (s["type"] == "text" and not s["text"])]

    if not segments:
        return [{"type": "text", "text": text}]

    if len(segments) > cap:
        logger.warning(
            "schedule.multi_msg_capped",
            extra={"ctx_total": len(segments), "ctx_cap": cap},
        )
        segments = segments[:cap]

    return segments


def _deliver_to_target(session: Session, user: User, schedule, text: str) -> None:
    """Fire into the schedule's channel (the chat it was created in), or DM fallback."""
    if schedule.target_chat:
        from backend.db.tenancy import UsageKind, WhatsAppConnection
        from backend.metering.usage import record_usage
        from backend.whatsapp import get_provider

        conn = session.get(WhatsAppConnection, user.account_id)
        provider = get_provider()
        if conn is not None and hasattr(provider, "send_text"):
            try:
                provider.send_text(schedule.target_chat, text, session_id=conn.session_id)
                record_usage(session, account_id=user.account_id, user_id=user.id,
                             kind=UsageKind.wa_message_out, quantity=1)
                return
            except Exception as exc:
                logger.warning("schedule.target_send_failed", extra={"ctx_error": str(exc)})
    # No target (or send failed) -> the user's own DM.
    from backend.delivery import deliver

    deliver(session, user, text, proactive=True)


def _deliver_media(session: Session, user: User, schedule, kind: str, url: str,
                   caption: str | None) -> None:
    """Deliver a media segment from _parse_stream directly via the WhatsApp provider."""
    from backend.db.tenancy import UsageKind, WhatsAppConnection
    from backend.metering.usage import record_usage
    from backend.whatsapp import get_provider

    target = schedule.target_chat or user.whatsapp_number
    conn = session.get(WhatsAppConnection, user.account_id)
    session_id = conn.session_id if conn is not None else None
    provider = get_provider()

    try:
        if kind == "audio":
            if hasattr(provider, "send_audio"):
                provider.send_audio(target, url, session_id=session_id)
        elif kind == "image":
            if hasattr(provider, "send_image"):
                provider.send_image(target, url, caption=caption or None, session_id=session_id)
        elif kind == "video":
            if hasattr(provider, "send_video"):
                provider.send_video(target, url, caption=caption or None, session_id=session_id)
        elif kind == "file":
            if hasattr(provider, "send_file"):
                provider.send_file(target, url, caption=caption or None, session_id=session_id)
    except Exception as exc:
        logger.warning("schedule.media_deliver_failed",
                       extra={"ctx_kind": kind, "ctx_error": str(exc)})
        return

    record_usage(session, account_id=user.account_id, user_id=user.id,
                 kind=UsageKind.wa_message_out, quantity=1)


def _default_agent_runner(user: User, instruction: str, session: Session,
                          target_chat: str | None = None) -> str:
    from backend.agent.mai_agent import respond
    from backend.db.tenancy import WhatsAppConnection
    from backend.tools.registry import build_tools

    # Give the scheduled run the SAME chat context an interactive turn has, so the media
    # send tools (send_audio/send_image/...) load and target the schedule's chat. Without
    # channel+device_id the registry skips MediaTools and the agent can only paste
    # (expiring) links instead of sending real media messages.
    conn = session.get(WhatsAppConnection, user.account_id)
    device_id = conn.session_id if conn is not None else None

    # Ordered delivery: media tools return markers instead of sending immediately, so text
    # and media can be interleaved in the order the agent writes them.
    turn_state: dict = {"ordered_delivery": True}

    augmented = (
        f"{instruction}\n\n"
        f"(Delivery: if this task genuinely needs to send SEVERAL separate WhatsApp messages, put a line\n"
        f"containing exactly ---MAI-MSG--- between each message. For media (audio/images/video/files),\n"
        f"do NOT paste raw URLs into text and do NOT rely on a separate send step — instead place a marker\n"
        f"on its own line exactly where it should appear in the sequence: [[audio:URL]], [[image:URL]],\n"
        f"[[image:URL|caption]], [[video:URL]], [[file:URL|caption]] — using the URL your generation tool\n"
        f"returned. Use a line with exactly ---MAI-MSG--- between separate TEXT messages. Everything — text\n"
        f"messages and media — is delivered top-to-bottom in the exact order you write it, so order it\n"
        f"however THIS task asks (e.g. a phrase, then its audio, then the next phrase).)"
    )
    return respond(user, augmented,
                   tools=build_tools(user, session, message=instruction,
                                     channel=target_chat, device_id=device_id,
                                     turn_state=turn_state),
                   session=session)


def fire_schedule_by_id(
    session: Session,
    schedule_id: int,
    *,
    now: datetime | None = None,
    sender: Sender | None = None,
    agent_runner: AgentRunner | None = None,
    is_entitled: Callable[[Session, Schedule], bool] | None = None,
) -> bool:
    """Fire one schedule. Returns True if it actually fired."""
    now = now or datetime.now(timezone.utc)
    custom_runner = agent_runner

    schedule = session.get(Schedule, schedule_id)
    if schedule is None:
        logger.warning("schedule.fire_missing", extra={"ctx_schedule_id": schedule_id})
        return False

    # Run as the owning tenant. Re-fetch through a TenantScope so we assert
    # ownership and refuse to operate on anything outside this tenant.
    scope = TenantScope(session, account_id=schedule.account_id, user_id=schedule.user_id)
    owned = scope.get(Schedule, schedule_id)
    if owned is None:  # pragma: no cover - defensive; scope mismatch
        return False
    set_tenant(schedule.account_id, schedule.user_id)
    new_trace_id("fire")

    if not owned.active:
        return False

    # Entitlement gate: a non-paying tenant's jobs are paused, not fired.
    if is_entitled is not None and not is_entitled(session, owned):
        logger.info("schedule.fire_skipped_unentitled", extra={"ctx_schedule_id": schedule_id})
        return False

    # Idempotency: skip a duplicate fire within the same minute (double-poll).
    if owned.last_fired_at is not None:
        last = owned.last_fired_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if now - last < timedelta(seconds=59):
            logger.info("schedule.fire_deduped", extra={"ctx_schedule_id": schedule_id})
            return False

    user = session.get(User, owned.user_id)
    if user is None:  # pragma: no cover - defensive
        return False

    # Build the message. The default runner also gets the schedule's chat so the
    # media send tools load; injected runners keep the plain 3-arg contract.
    if owned.kind == "dynamic":
        try:
            if custom_runner is not None:
                message = custom_runner(user, owned.action_text, session)
            else:
                message = _default_agent_runner(user, owned.action_text, session,
                                                target_chat=owned.target_chat)
        except Exception as exc:
            logger.exception("schedule.dynamic_failed", extra={"ctx_error": str(exc)})
            message = "I tried to prepare your scheduled update but hit a problem."
    else:
        message = owned.action_text

    # Mirror into RAM buffer when auto_memory is enabled (no DB write).
    try:
        from backend.agent.seam import remember_turn

        remember_turn(user, "assistant", message)
    except Exception:  # pragma: no cover - memory must never block a fire
        pass

    # Deliver: to the schedule's channel (where it was created), or the user's DM.
    # A custom `sender` (tests) overrides routing.
    # The agent (or a static action_text) may embed MULTI_MSG_DELIMITER to split the
    # output into several WhatsApp messages capped at MULTI_MSG_CAP for safety.
    segments = _parse_stream(message)
    for seg in segments:
        if seg["type"] == "text":
            if sender is not None:
                sender(session, user, seg["text"])
            else:
                _deliver_to_target(session, user, owned, seg["text"])
        else:  # media
            if sender is not None:
                sender(session, user, f'[[{seg["kind"]}:{seg["url"]}]]')
            else:
                _deliver_media(session, user, owned, seg["kind"], seg["url"], seg["caption"])

    # Record the fire; deactivate one-offs so they never repeat.
    owned.last_fired_at = now
    owned.fire_count += 1
    if owned.schedule_type == "once":
        owned.active = False
    session.commit()
    logger.info(
        "schedule.fired",
        extra={"ctx_schedule_id": owned.id, "ctx_kind": owned.kind, "ctx_count": owned.fire_count},
    )
    return True
