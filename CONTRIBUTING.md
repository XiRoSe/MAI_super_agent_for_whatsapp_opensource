# Contributing to Mai

Thanks for your interest. Mai is maintained by one person in spare time, so this
guide is short and the process is lightweight.

## Before you start

- **Bugs**: open an issue with the bug template first, unless the fix is a
  one-liner.
- **Features or anything larger than ~100 lines**: open an issue or a
  [Discussion](https://github.com/XiRoSe/MAI_super_agent_for_whatsapp_opensource/discussions)
  first so we agree on scope before you write code. PRs that appear without prior
  discussion may be closed if they don't fit the direction.
- **Small PRs merge fast.** Split large changes.

## Dev setup

Requirements: Python 3.11+, Node 20+ (for the dashboard), and optionally Docker
for the full stack.

```bash
git clone https://github.com/XiRoSe/MAI_super_agent_for_whatsapp_opensource.git mai
cd mai

# backend
py -3.11 -m venv .venv           # Windows; macOS/Linux: python3.11 -m venv .venv
.venv/Scripts/activate            # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env              # SQLite by default; no keys needed for tests

# frontend
cd frontend && npm install && npm run dev
```

Useful entry points:

- `pytest -q` — the offline test suite (the model is mocked; no network, no API
  keys). It covers the things that silently break: tenant isolation, the
  schedule engine, WhatsApp routing/signatures, crypto, the injection boundary.
- `ruff check .` — lint (line length 100, py311).
- `uvicorn backend.app:app --port 7777` — run the full app locally on SQLite.
- `mai` — a REPL harness that drives the agent without WhatsApp.

CI runs exactly: `ruff check .`, `pytest -q`, the frontend type-check + build,
and a Docker build smoke test. Green locally means green in CI.

## PR style

- One logical change per PR; keep the diff focused, no drive-by reformatting.
- Add or update tests for behavior you change — especially anything touching
  tenant scoping, routing, or scheduling.
- Fill in the PR template and link the issue (`Closes #123`).
- No secrets, real phone numbers, or real chat content in code, tests, or logs.

## Developer Certificate of Origin (DCO)

Every commit must be signed off:

```bash
git commit -s -m "fix: ..."
```

The sign-off (`Signed-off-by: Your Name <you@example.com>`) certifies the
[DCO 1.1](https://developercertificate.org/): you wrote the change or otherwise
have the right to submit it under this project's MIT license. That's the whole
ceremony — **there is no CLA**. A CI check verifies the sign-off on each PR; if
you forgot it, `git commit --amend -s` (or `git rebase --signoff main`) and
force-push your branch.

## What to expect

Solo maintainer, best-effort responses — reviews may take days, sometimes
longer. Pinging once after a week is fine. See [SUPPORT.md](SUPPORT.md) for
where questions go, and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for conduct.
