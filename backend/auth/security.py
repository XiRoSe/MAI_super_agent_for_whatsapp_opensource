"""Password hashing (bcrypt) and JWT issue/verify for the dashboard."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from backend.config import settings

ALGO = "HS256"


def _jwt_secret() -> str:
    secret = settings.mai_jwt_secret
    if not secret:
        if settings.is_production:
            raise RuntimeError("MAI_JWT_SECRET is required in production")
        secret = "mai-dev-insecure-jwt-secret-do-not-use-in-production-0123456789"
    return secret


def hash_password(password: str) -> str:
    # bcrypt limits input to 72 bytes; pre-hash so long passwords are safe.
    digest = hashlib.sha256(password.encode()).digest()
    return bcrypt.hashpw(digest, bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    digest = hashlib.sha256(password.encode()).digest()
    try:
        return bcrypt.checkpw(digest, password_hash.encode())
    except (ValueError, TypeError):
        return False


def create_token(*, account_id: int, auth_user_id: int, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(account_id),
        "auth_user_id": auth_user_id,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.jwt_expiry_hours)).timestamp()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=ALGO)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=[ALGO])
    except jwt.PyJWTError:
        return None
