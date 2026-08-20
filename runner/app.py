"""mai-runner: a stateless service that runs sandbox code. Holds no secrets
(only its auth secret); persists nothing."""

from __future__ import annotations

import os

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from runner.executor import run_code

app = FastAPI(title="mai-runner")
_SECRET = os.environ.get("RUNNER_SHARED_SECRET", "")


class RunReq(BaseModel):
    code: str
    sdk_source: str
    token: str
    sdk_base_url: str
    timeout: int = 60


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/run")
async def run(req: RunReq, x_runner_secret: str | None = Header(default=None)):
    if not _SECRET or x_runner_secret != _SECRET:
        return JSONResponse({"ok": False, "output": "unauthorized"}, status_code=401)
    r = await run_code(req.code, sdk_source=req.sdk_source, token=req.token,
                       sdk_base_url=req.sdk_base_url, timeout=min(max(1, req.timeout), 300))
    return {"ok": r.ok, "output": r.output, "duration_ms": r.duration_ms}
