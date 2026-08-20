"""Channel grants: which chats the owner has opened to non-owner @Mai invokers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.tenancy import WhatsAppChatGrant


def is_chat_granted(session: Session, account_id: int, chat_id: str) -> bool:
    if not chat_id:
        return False
    return session.execute(
        select(WhatsAppChatGrant.id).where(
            WhatsAppChatGrant.account_id == account_id,
            WhatsAppChatGrant.chat_id == chat_id,
        )
    ).first() is not None


def grant_chat(session: Session, account_id: int, chat_id: str, label: str | None = None) -> bool:
    """Open a chat to guest invokers. Returns False if it was already open."""
    if is_chat_granted(session, account_id, chat_id):
        return False
    session.add(WhatsAppChatGrant(account_id=account_id, chat_id=chat_id, label=label))
    session.commit()
    return True


def revoke_chat(session: Session, account_id: int, chat_id: str) -> bool:
    grant = session.execute(
        select(WhatsAppChatGrant).where(
            WhatsAppChatGrant.account_id == account_id,
            WhatsAppChatGrant.chat_id == chat_id,
        )
    ).scalar_one_or_none()
    if grant is None:
        return False
    session.delete(grant)
    session.commit()
    return True


def list_grants(session: Session, account_id: int) -> list[WhatsAppChatGrant]:
    return list(session.execute(
        select(WhatsAppChatGrant).where(WhatsAppChatGrant.account_id == account_id)
    ).scalars())
