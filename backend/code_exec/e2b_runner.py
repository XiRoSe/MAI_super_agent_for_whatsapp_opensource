"""Real E2B sandbox runner: upload the mai SDK, run the model's code, capture output."""

from __future__ import annotations

from backend.code_exec.runner import RunResult
from backend.code_exec.sdk_reference import sdk_source
from backend.config import settings
from backend.logging_config import get_logger

logger = get_logger(__name__)

_BOOT = "import mai\n"  # mai_sdk.py is uploaded as mai.py


class E2BRunner:
    def run(self, code: str, token: str, base_url: str, timeout: int) -> RunResult:
        try:
            from e2b_code_interpreter import Sandbox
        except Exception as exc:  # pragma: no cover - import guard
            return RunResult(ok=False, output=f"sandbox unavailable: {exc}")
        try:
            sbx = Sandbox.create(
                api_key=settings.e2b_api_key,
                envs={
                    "MAI_SDK_BASE_URL": base_url, "MAI_SDK_TOKEN": token,
                },
                timeout=timeout + 10,
            )
        except Exception as exc:
            logger.warning("e2b.create_failed", extra={"ctx_error": str(exc)})
            return RunResult(ok=False, output="couldn't start the code sandbox")
        try:
            sbx.files.write("mai.py", sdk_source())
            exec_ = sbx.run_code(_BOOT + code, timeout=timeout)
            if exec_.error:
                return RunResult(ok=False, output=f"{exec_.error.name}: {exec_.error.value}\n"
                                 + "\n".join(exec_.logs.stdout))
            return RunResult(ok=True, output="\n".join(exec_.logs.stdout))
        except Exception as exc:
            return RunResult(ok=False, output=f"sandbox run failed: {exc}")
        finally:
            try:
                sbx.kill()
            except Exception:
                pass


def make_runner():
    if not settings.code_exec_enabled:
        return None
    if settings.runner_url and settings.runner_shared_secret:
        from backend.code_exec.service_runner import ServiceRunner
        return ServiceRunner()
    if settings.e2b_api_key:
        return E2BRunner()
    return None
