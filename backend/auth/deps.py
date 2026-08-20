"""FastAPI auth dependencies — resolve the calling account from a JWT.

Yields an `AuthContext` that carries the account, the auth user, an open DB
session, and helpers to get tenant-scoped accessors. The dashboard operates at
the account level; user-scoped data uses the account's primary User.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from backend.auth.security import decode_token
from backend.db.base import SessionLocal
from backend.db.scoped import TenantScope
from backend.db.tenancy import Account, AuthUser, User
from backend.provisioning import get_or_create_primary_user


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@dataclass
class AuthContext:
    account: Account
    auth_user: AuthUser
    session: Session

    def account_scope(self) -> TenantScope:
        return TenantScope(self.session, account_id=self.account.id)

    def primary_user(self) -> User:
        return get_or_create_primary_user(self.session, self.account)

    def user_scope(self) -> TenantScope:
        u = self.primary_user()
        return TenantScope(self.session, account_id=self.account.id, user_id=u.id)


def get_auth_context(
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
) -> AuthContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1]
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    account = session.get(Account, int(payload["sub"]))
    auth_user = session.get(AuthUser, int(payload["auth_user_id"]))
    if account is None or auth_user is None or auth_user.account_id != account.id:
        raise HTTPException(status_code=401, detail="account not found")
    return AuthContext(account=account, auth_user=auth_user, session=session)
