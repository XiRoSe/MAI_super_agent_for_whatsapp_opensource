"""The encrypted, time-windowed message store — Mai's ONLY history.

Writes seal the message TEXT to the current epoch public key (KeyService) and
persist only ciphertext + plaintext routing metadata. Reads decrypt in RAM with
the matching epoch private key; a shredded/absent key renders '[expired]'. This
module is the single seam the read path (gowa.get_chat_messages) goes through,
so its dicts match that function's shape exactly.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select

from backend.crypto import sealing
from backend.crypto.keyservice import get_key_service
from backend.db.base import utcnow
from backend.db.tenancy import SealedMessage
from backend.logging_config import get_logger

logger = get_logger(__name__)


def _norm_chat_id(raw: str | None) -> str:
    """Match parse_inbound's _chat_id: keep @g.us jids, reduce individuals to +number."""
    raw = str(raw or "")
    if "@g.us" in raw:
        return raw
    num = raw.split("@", 1)[0].split(":", 1)[0].strip()
    if not num:
        return raw
    return num if num.startswith("+") else "+" + num


def store_sealed(session, account_id: int, chat_id: str, direction: str, plaintext: str, *,
                 message_id: str | None = None, sender: str | None = None,
                 sender_number: str | None = None, media_type: str | None = None,
                 media_url: str | None = None, ts: str | None = None,
                 sealed_ciphertext: str | None = None, epoch_id: int | None = None) -> None:
    plaintext = plaintext or ""
    if not plaintext and not sealed_ciphertext and not media_type:
        return  # nothing to store
    chat = _norm_chat_id(chat_id)
    if message_id:  # idempotent: same stanza id (incl. our own reply's echo) stored once
        exists = session.execute(
            select(SealedMessage.id).where(
                SealedMessage.account_id == account_id,
                SealedMessage.message_id == message_id,
            )
        ).first()
        if exists:
            return
    if sealed_ciphertext is not None and epoch_id is not None:
        # Sealed-input path (2B): GOWA already sealed it — store the ciphertext +
        # its epoch AS-IS. Never re-seal, never touch the current pubkey.
        ct = sealed_ciphertext
        eid = int(epoch_id)
    else:
        # Clear-input path (2A): seal-on-receipt to the CURRENT epoch. Fail-closed:
        # if the key/keystore is unavailable, current_public_key() raises and we
        # store NOTHING (never plaintext).
        eid, pub_b64 = get_key_service().current_public_key()
        ct = sealing.seal(plaintext, pub_b64)
    session.add(SealedMessage(
        account_id=account_id, chat_id=chat, direction=direction, epoch_id=eid,
        message_id=message_id or None, sender=sender, sender_number=sender_number,
        media_type=media_type, media_url=media_url, ciphertext=ct, ts=ts,
    ))
    session.commit()


def _decrypt(row: SealedMessage) -> str:
    priv = get_key_service().private_for(row.epoch_id)
    if priv is None:
        return "[expired]"   # crypto-shredded — never an error
    try:
        return sealing.open_sealed(row.ciphertext, priv)
    except Exception as exc:  # corrupt/undecryptable -> treat as expired, never crash
        logger.warning("encstore.decrypt_failed", extra={"ctx_epoch": row.epoch_id, "ctx_error": str(exc)})
        return "[expired]"


def recent(session, account_id: int, chat_id: str, *, limit: int = 20,
           within_days: int = 4) -> list[dict]:
    chat = _norm_chat_id(chat_id)
    cutoff = utcnow() - timedelta(days=within_days)
    rows = list(session.execute(
        select(SealedMessage).where(
            SealedMessage.account_id == account_id,
            SealedMessage.chat_id == chat,
            SealedMessage.created_at >= cutoff,
        ).order_by(SealedMessage.created_at.desc(), SealedMessage.id.desc()).limit(limit)
    ).scalars().all())
    rows.reverse()  # oldest -> newest (matches gowa.get_chat_messages)
    out = []
    for r in rows:
        out.append({
            "id": r.message_id or "",
            "sender": r.sender or "",
            "sender_number": r.sender_number or "",
            "text": _decrypt(r),
            "media_type": r.media_type,
            "media_url": r.media_url,
            "from_me": r.direction == "out",
            "ts": r.ts,
        })
    return out


def purge_expired(session, *, within_days: int = 4) -> int:
    cutoff = utcnow() - timedelta(days=within_days)
    result = session.execute(delete(SealedMessage).where(SealedMessage.created_at < cutoff))
    session.commit()
    return int(result.rowcount or 0)
