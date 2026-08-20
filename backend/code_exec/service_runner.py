"""Drive the separate runner service: POST the code, map the reply to RunResult."""

from __future__ import annotations

import httpx

from backend.code_exec.runner import RunResult
from backend.code_exec.sdk_reference import sdk_source
from backend.config import settings
from backend.logging_config import get_logger

logger = get_logger(__name__)


class ServiceRunner:
    def run(self, code: str, token: str, base_url: str, timeout: int) -> RunResult:
        try:
            resp = httpx.post(
                f"{settings.runner_url.rstrip('/')}/run",
                json={"code": code, "sdk_source": sdk_source(), "token": token,
                      "sdk_base_url": base_url, "timeout": timeout},
                headers={"X-Runner-Secret": settings.runner_shared_secret},
                timeout=timeout + 15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("service_runner.failed", extra={"ctx_error": str(exc)})
            return RunResult(ok=False, output="I couldn't reach the code sandbox just now.")
        return RunResult(ok=bool(data.get("ok")), output=str(data.get("output", "")))
