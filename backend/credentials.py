"""Generic, encrypted, per-tenant credential vault for skill-based integrations.

The user gives Mai credentials for whatever service they want (an API key, an
OAuth token set, a service-account JSON). We store it as one encrypted JSON blob
per (user, service). Skills describe how to USE a service; this just remembers
the creds. Everything is tenant-scoped, so a user's creds are never visible to
another tenant.
"""

from __future__ import annotations

import json

from backend.crypto import decrypt, encrypt
from backend.db.scoped import TenantScope
from backend.db.tenancy import Credential
from backend.logging_config import get_logger

logger = get_logger(__name__)


def _normalize(service: str) -> str:
    return service.strip().lower()


def save_credential(
    scope: TenantScope, *, service: str, data: dict, label: str | None = None
) -> Credential:
    service = _normalize(service)
    blob = encrypt(json.dumps(data))
    existing = next((c for c in scope.all(Credential) if c.service == service), None)
    if existing is not None:
        existing.data_enc = blob  # type: ignore[assignment]
        if label is not None:
            existing.label = label
        scope.commit()
        logger.info("cred.updated", extra={"ctx_service": service})
        return existing
    cred = Credential(service=service, data_enc=blob, label=label)  # type: ignore[arg-type]
    scope.add(cred)
    scope.commit()
    logger.info("cred.saved", extra={"ctx_service": service})
    return cred


def get_credential(scope: TenantScope, service: str) -> dict | None:
    service = _normalize(service)
    row = next((c for c in scope.all(Credential) if c.service == service), None)
    if row is None:
        return None
    raw = decrypt(row.data_enc)
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:  # pragma: no cover - corrupted blob
        return None


def list_services(scope: TenantScope) -> list[dict]:
    """Public listing — service names + labels only, never the secret data."""
    return [
        {"service": c.service, "label": c.label, "created_at": c.created_at.isoformat()}
        for c in scope.all(Credential)
    ]


def has_credential(scope: TenantScope, service: str) -> bool:
    service = _normalize(service)
    return any(c.service == service for c in scope.all(Credential))


def update_credential(
    scope: TenantScope,
    service: str,
    *,
    data: dict | None = None,
    label: str | None = None,
) -> bool:
    service = _normalize(service)
    row = next((c for c in scope.all(Credential) if c.service == service), None)
    if row is None:
        return False
    if data is not None:
        row.data_enc = encrypt(json.dumps(data))
    if label is not None:
        row.label = label
    scope.commit()
    logger.info("cred.updated_partial", extra={"ctx_service": service})
    return True


def delete_credential(scope: TenantScope, service: str) -> bool:
    service = _normalize(service)
    row = next((c for c in scope.all(Credential) if c.service == service), None)
    if row is None:
        return False
    scope.session.delete(row)
    scope.commit()
    logger.info("cred.deleted", extra={"ctx_service": service})
    return True
