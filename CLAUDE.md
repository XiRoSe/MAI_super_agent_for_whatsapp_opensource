# Mai — working notes for Claude Code

Product is **Mai** (self-hostable WhatsApp AI assistant); repo is
**MAI_super_agent_for_whatsapp_opensource** (`XiRoSe/MAI_super_agent_for_whatsapp_opensource`).
See `README.md` and `ARCHITECTURE.md`.

## Running tests

Run tests in the project environment (never a system Python), so adding a test that needs a new dependency forces keeping that environment synced with `pyproject.toml`.

## Eval design principles

The cost of an eval is the **LLM calls**, not infrastructure — so simulate production
as much as you want. The thing to optimize for is **minimizing drift** between what the
eval exercises and what runs in production.

1. **Substitute only across a contract you trust and are NOT testing.** Fine to swap:
   the SQL engine (in-memory SQLite for Postgres — we trust the SQL contract), the
   WhatsApp HTTP transport, Replicate, the e2b sandbox, Agno's external scheduler poller.
   These are boundaries, not the thing under test.
2. **Never substitute one of our own production modules.** `ScheduleService`,
   `ScheduleSpec`, the tool toolkits, the system prompt, the tool registry/router — run
   the REAL one. Faking it is drift: if the module regresses in prod, the eval stays
   green. (We learned this the hard way faking `ScheduleService` — it dropped the
   per-user limit check, the ORM row, and the commit.)
3. **Grade on the real persisted state (source of truth), not just the model's args.**
   Prefer asserting on the actual `Schedule` row the production service wrote over
   parsing the LLM's tool-call arguments — a tool call with right-looking args that then
   errored (limit/validation) persists nothing and must FAIL. See
   `evals/scenarios.py::_is_daily_8am_schedule`.
4. **Monitor every abstraction layer separately** — agno tool calls, mai.* SDK calls
   (inside `code_exec`), thinking/reasoning tokens, and persisted DB state. A run can
   pass one layer and fail another (e.g. generate a video but never call `send_video`).

## Typed contracts

We **always use hard types in contracts** — function/endpoint boundaries and any value
with a known schema get a `@dataclass` (or Pydantic), never a string-keyed `dict`. A
bare `dict` is allowed ONLY for genuinely open/heterogeneous payloads (e.g. arbitrary
LLM tool-call `args`).

**dataclass vs Pydantic:**
- **`dataclass`** (default for internal/domain types) — when WE construct the value from
  already-trusted data and don't need runtime validation/coercion. Use `frozen=True` for
  value objects. Examples: `evals` DTOs (`Grade`, `LayerVerdict`, `ScheduleSnapshot`),
  `ScheduleSpec`. `dataclasses.asdict()` serializes them (pass `default=str` to
  `json.dumps` for `datetime`).
- **Pydantic** — at **trust boundaries** where data arrives from outside and must be
  validated/coerced/parsed: API request/response bodies (FastAPI), config from env
  (`settings`), webhook payloads, and parsing an LLM's structured output. That's where
  validation earns its cost.

Rule of thumb: dataclass for trusted construction, Pydantic where untrusted input crosses
a boundary. (Matches existing usage: Pydantic for `settings`, dataclasses for domain
objects.)
