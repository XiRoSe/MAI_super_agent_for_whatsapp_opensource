"""Run sandbox code in a hardened, ephemeral subprocess. Stateless: temp dir is
always deleted. POSIX gets rlimits + drop-privs + killpg; other OSes fall back to
a plain subprocess + timeout so local e2e still works."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
import tempfile
import time
from dataclasses import dataclass

_POSIX = os.name == "posix"
_MAX_CONCURRENT = int(os.environ.get("RUNNER_MAX_CONCURRENT", "8"))
_MEM_BYTES = int(os.environ.get("RUNNER_MEM_MB", "512")) * 1024 * 1024
_sem = asyncio.Semaphore(_MAX_CONCURRENT)


@dataclass
class RunOutput:
    ok: bool
    output: str
    duration_ms: int


def _nobody_uid():
    if not _POSIX or os.geteuid() != 0:
        return None
    try:
        import pwd
        e = pwd.getpwnam("nobody")
        return (e.pw_uid, e.pw_gid)
    except Exception:
        return None


def _preexec(timeout: int, drop_uid):
    import resource

    def fn():
        os.setsid()  # own process group → killpg on timeout
        resource.setrlimit(resource.RLIMIT_CPU, (timeout + 1, timeout + 2))
        resource.setrlimit(resource.RLIMIT_AS, (_MEM_BYTES, _MEM_BYTES))
        resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        except Exception:
            pass
        if drop_uid is not None:
            uid, gid = drop_uid
            os.setgid(gid)
            os.setuid(uid)
    return fn


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


async def run_code(code: str, *, sdk_source: str, token: str, sdk_base_url: str,
                   timeout: int = 60) -> RunOutput:
    async with _sem:
        return await _run(code, sdk_source, token, sdk_base_url, timeout)


async def _run(code, sdk_source, token, sdk_base_url, timeout) -> RunOutput:
    t0 = time.monotonic()
    workdir = tempfile.mkdtemp(prefix="sbx_")
    try:
        with open(os.path.join(workdir, "mai.py"), "w", encoding="utf-8") as f:
            f.write(sdk_source)
        with open(os.path.join(workdir, "user.py"), "w", encoding="utf-8") as f:
            f.write(code)
        # Bootstrap: expose `mai` as a global (no import needed in user code, matching
        # the tool's contract) and run the user code under its own filename so tracebacks
        # report correct line numbers.
        boot = (
            "import mai\n"
            "_g = {'mai': mai, '__name__': '__main__'}\n"
            "with open('user.py', encoding='utf-8') as _f:\n"
            "    _src = _f.read()\n"
            "exec(compile(_src, 'user.py', 'exec'), _g)\n"
        )
        with open(os.path.join(workdir, "main.py"), "w", encoding="utf-8") as f:
            f.write(boot)
        env = {
            "MAI_SDK_BASE_URL": sdk_base_url,
            "MAI_SDK_TOKEN": token,
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": workdir,
            "LANG": "C.UTF-8",
            "PYTHONPATH": workdir,          # ADD workdir; site-packages still found → libs load, backend doesn't
            "PYTHONDONTWRITEBYTECODE": "1",
            # Single-thread BLAS so numpy/pandas don't reserve huge per-core buffers
            # (which blow past RLIMIT_AS and trigger "OpenBLAS memory allocation" errors).
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
        if "SYSTEMROOT" in os.environ:
            env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        drop = _nobody_uid()
        if drop is not None:
            os.chmod(workdir, 0o777)  # let 'nobody' read its files
        preexec = _preexec(timeout, drop) if _POSIX else None
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "main.py", cwd=workdir, env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                preexec_fn=preexec,
            )
        except Exception as exc:
            return RunOutput(False, f"failed to start sandbox: {exc}", _ms(t0))
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            _kill(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except Exception:
                pass
            return RunOutput(False, f"timed out after {timeout}s", _ms(t0))
        text = (out or b"").decode("utf-8", "replace")
        return RunOutput(proc.returncode == 0, text, _ms(t0))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _kill(proc):
    try:
        if _POSIX:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
