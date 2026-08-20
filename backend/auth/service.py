"""Registration & login — creates the Account + AuthUser pair on signup.

Signup is the top of the onboarding funnel: register -> (subscribe) ->
connect WhatsApp -> connect Google. Registration provisions a fresh Account
(trial) and its first AuthUser.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth.security import hash_password, verify_password
from backend.db.tenancy import Account, AuthUser, SubscriptionStatus
from backend.logging_config import get_logger

logger = get_logger(__name__)


class AuthError(Exception):
    pass


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def get_auth_user_by_email(session: Session, email: str) -> AuthUser | None:
    return session.scalars(
        select(AuthUser).where(AuthUser.email == _normalize_email(email))
    ).first()


def register(
    session: Session, *, email: str, password: str, display_name: str | None = None
) -> AuthUser:
    email = _normalize_email(email)
    if not email or "@" not in email:
        raise AuthError("a valid email is required")
    if len(password) < 8:
        raise AuthError("password must be at least 8 characters")
    if get_auth_user_by_email(session, email):
        raise AuthError("an account with that email already exists")

    account = Account(plan="beta", status=SubscriptionStatus.trial)
    session.add(account)
    session.flush()

    auth_user = AuthUser(
        account_id=account.id,
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
    )
    session.add(auth_user)
    session.commit()
    logger.info("auth.registered", extra={"ctx_account_id": account.id})
    return auth_user


def authenticate(session: Session, *, email: str, password: str) -> AuthUser | None:
    user = get_auth_user_by_email(session, email)
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user
