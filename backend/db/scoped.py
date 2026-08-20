"""Fail-closed tenant-scoped data access.

This is the enforcement point for Mai's #1 invariant: hard data isolation.

Every read and write of a tenant-owned row goes through a `TenantScope`. The
scope carries an `account_id` (always) and a `user_id` (for user-owned rows).
There is deliberately **no API** to obtain an unfiltered query for an owned
model: `select()` always injects the tenant filter, and if a required filter is
unavailable (e.g. a `TenantOwned` model queried from an account-only scope), it
**raises** rather than returning rows. Missing tenant filter => error, never leak.

Writes are equally fail-closed: `add()` stamps the scope's ids onto the row and
refuses to persist a row whose ids contradict the scope.
"""

from __future__ import annotations

from typing import Iterable, Type, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from backend.db.tenancy import Account, AccountOwned, TenantOwned

T = TypeVar("T")


class TenantIsolationError(RuntimeError):
    """Raised when an operation would breach tenant isolation."""


class TenantScope:
    def __init__(self, session: Session, account_id: int, user_id: int | None = None):
        if account_id is None:
            # Fail closed: you cannot construct a scope without an account.
            raise TenantIsolationError("TenantScope requires an account_id")
        self.session = session
        self.account_id = account_id
        self.user_id = user_id

    # -- query construction -------------------------------------------------
    def _filtered_select(self, model: Type[T]) -> Select:
        stmt = select(model)
        # The Account row itself is scoped by primary key == this account.
        if model is Account:
            return stmt.where(model.id == self.account_id)

        is_tenant_owned = isinstance(model, type) and issubclass(model, TenantOwned)
        is_account_owned = isinstance(model, type) and issubclass(model, AccountOwned)

        if not is_account_owned:
            # A model with no ownership marker must not be queried through a
            # tenant scope — that would be an unscoped read. Fail closed.
            raise TenantIsolationError(
                f"{model.__name__} is not tenant-owned; refusing unscoped access"
            )

        stmt = stmt.where(model.account_id == self.account_id)

        if is_tenant_owned:
            if self.user_id is None:
                # Required user filter is unavailable. Do NOT fall back to an
                # account-wide read — that could leak across users in the
                # (future) multi-user-per-account case. Fail closed.
                raise TenantIsolationError(
                    f"{model.__name__} requires a user_id but the scope has none"
                )
            stmt = stmt.where(model.user_id == self.user_id)
        return stmt

    def select(self, model: Type[T]) -> Select:
        """A SELECT for `model` with mandatory tenant filters already applied."""
        return self._filtered_select(model)

    def all(self, model: Type[T]) -> list[T]:
        return list(self.session.scalars(self._filtered_select(model)).all())

    def first(self, model: Type[T]) -> T | None:
        return self.session.scalars(self._filtered_select(model).limit(1)).first()

    def count(self, model: Type[T]) -> int:
        stmt = self._filtered_select(model).with_only_columns(func.count()).order_by(None)
        return int(self.session.scalar(stmt) or 0)

    def get(self, model: Type[T], pk: int) -> T | None:
        """Fetch by primary key, returning None if it isn't in this tenant.

        Cross-tenant rows are reported as not-found rather than raising, so a
        caller cannot probe for the existence of another tenant's ids.
        """
        obj = self.session.get(model, pk)
        if obj is None or not self._is_owned(obj):
            return None
        return obj

    def get_or_raise(self, model: Type[T], pk: int) -> T:
        obj = self.get(model, pk)
        if obj is None:
            raise TenantIsolationError(f"{model.__name__}#{pk} not found in this tenant")
        return obj

    # -- writes -------------------------------------------------------------
    def add(self, obj: T) -> T:
        """Stamp tenant ids onto `obj` and persist, refusing cross-tenant writes."""
        self._stamp_and_assert(obj)
        self.session.add(obj)
        return obj

    def add_all(self, objs: Iterable[object]) -> None:
        for o in objs:
            self.add(o)

    # -- ownership checks ---------------------------------------------------
    def _is_owned(self, obj: object) -> bool:
        try:
            self._assert_owned(obj)
            return True
        except TenantIsolationError:
            return False

    def _assert_owned(self, obj: object) -> None:
        if isinstance(obj, Account):
            if obj.id != self.account_id:
                raise TenantIsolationError("cross-tenant Account access blocked")
            return
        acc = getattr(obj, "account_id", None)
        if acc is not None and acc != self.account_id:
            raise TenantIsolationError(
                f"{type(obj).__name__} belongs to account {acc}, scope is {self.account_id}"
            )
        if isinstance(obj, TenantOwned):
            uid = getattr(obj, "user_id", None)
            if self.user_id is not None and uid is not None and uid != self.user_id:
                raise TenantIsolationError(
                    f"{type(obj).__name__} belongs to user {uid}, scope is {self.user_id}"
                )

    def _stamp_and_assert(self, obj: object) -> None:
        # Stamp account_id if the model has it and it's unset.
        if isinstance(obj, AccountOwned) or hasattr(obj, "account_id"):
            current = getattr(obj, "account_id", None)
            if current is None:
                setattr(obj, "account_id", self.account_id)
            elif current != self.account_id:
                raise TenantIsolationError("refusing to write row into another account")
        if isinstance(obj, TenantOwned):
            if self.user_id is None:
                raise TenantIsolationError(
                    f"cannot write {type(obj).__name__} without a user-scoped TenantScope"
                )
            current = getattr(obj, "user_id", None)
            if current is None:
                setattr(obj, "user_id", self.user_id)
            elif current != self.user_id:
                raise TenantIsolationError("refusing to write row for another user")

    # -- transaction passthrough -------------------------------------------
    def flush(self) -> None:
        self.session.flush()

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
