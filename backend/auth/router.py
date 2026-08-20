"""Auth routes: register, login, me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from backend.auth.deps import AuthContext, get_auth_context, get_db
from backend.auth.security import create_token
from backend.auth.service import AuthError, authenticate, register


class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    display_name: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    account_id: int
    email: str


def build_auth_router() -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.post("/register", response_model=TokenOut)
    def register_route(body: RegisterIn, session: Session = Depends(get_db)) -> TokenOut:
        try:
            user = register(
                session, email=body.email, password=body.password, display_name=body.display_name
            )
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        token = create_token(account_id=user.account_id, auth_user_id=user.id, email=user.email)
        return TokenOut(access_token=token, account_id=user.account_id, email=user.email)

    @router.post("/login", response_model=TokenOut)
    def login_route(body: LoginIn, session: Session = Depends(get_db)) -> TokenOut:
        user = authenticate(session, email=body.email, password=body.password)
        if user is None:
            raise HTTPException(status_code=401, detail="invalid email or password")
        token = create_token(account_id=user.account_id, auth_user_id=user.id, email=user.email)
        return TokenOut(access_token=token, account_id=user.account_id, email=user.email)

    @router.get("/me")
    def me_route(ctx: AuthContext = Depends(get_auth_context)) -> dict:
        return {
            "account_id": ctx.account.id,
            "email": ctx.auth_user.email,
            "display_name": ctx.auth_user.display_name,
            "plan": ctx.account.plan,
            "subscription_status": ctx.account.status.value,
        }

    return router
