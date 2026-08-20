"""Structured logging with per-request trace IDs.

JSON-ish structured logs that never leak secrets or full message bodies at INFO.
A `trace_id` contextvar threads a request/turn id through all log lines so an
inbound WhatsApp message or a scheduled job fire can be followed end to end.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar

from backend.config import settings

_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")
_tenant: ContextVar[str] = ContextVar("tenant", default="-")

# Keys that must never be logged in full, even in dev.
_SECRET_HINTS = ("token", "secret", "api_key", "password", "authorization")


def new_trace_id(prefix: str = "trc") -> str:
    tid = f"{prefix}_{uuid.uuid4().hex[:12]}"
    _trace_id.set(tid)
    return tid


def set_trace_id(tid: str) -> None:
    _trace_id.set(tid)


def set_tenant(account_id: str | int | None, user_id: str | int | None) -> None:
    _tenant.set(f"acct={account_id};user={user_id}")


def clear_tenant() -> None:
    _tenant.set("-")


def _redact(value: str) -> str:
    return value[:4] + "…redacted" if value else value


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": _trace_id.get(),
            "tenant": _tenant.get(),
            "msg": record.getMessage(),
        }
        # Attach structured extras (skip noisy/internal fields).
        for key, val in getattr(record, "__dict__", {}).items():
            if key.startswith("ctx_"):
                clean_key = key[4:]
                if any(h in clean_key.lower() for h in _SECRET_HINTS) and isinstance(val, str):
                    val = _redact(val)
                payload[clean_key] = val
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.mai_log_level.upper())
    # Tame noisy third-party loggers.
    for noisy in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
