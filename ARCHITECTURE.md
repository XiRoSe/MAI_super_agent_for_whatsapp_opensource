# Mai — architecture

One Python service (FastAPI + [Agno](https://github.com/agno-agi/agno) 2.x), one
database (Postgres; SQLite for local dev), one WhatsApp gateway
([mai-gowa](https://github.com/XiRoSe/mai-gowa)). The React dashboard is built at
Docker-build time and served by the same backend from `./static`, so SPA and API
share one origin and one port (`PORT`, default 7777).

## Component map

```mermaid
flowchart TB
  subgraph gateway["mai-gowa (Go)"]
    GW["WhatsApp multi-device client<br/>REST + webhooks, device_id routing"]
  end
  subgraph app["mai container (backend/app.py)"]
    WH["whatsapp/<br/>webhook + router + provider"]
    AG["agent/<br/>build_agent + respond"]
    TO["tools/<br/>registry + toolkits"]
    SC["scheduling/<br/>poller + service"]
    DA["dashboard/ + auth/<br/>JWT REST API"]
    CR["crypto/<br/>KEK, epoch keys, sealing"]
    CE["code_exec/<br/>E2B or runner dispatch"]
    SPA["static/ (built React SPA)"]
  end
  DB[(Postgres / SQLite)]
  LLM[LLM provider]
  RUN["runner/ (optional sandbox service)"]

  GW -->|"POST /webhooks/whatsapp"| WH
  WH --> AG
  AG --> TO
  TO -->|send/read chats| GW
  SC --> AG
  DA --> AG
  AG <--> LLM
  app <--> DB
  CE --> RUN
```

## Backend modules (`backend/`)

**`app.py`** — the composition root. `create_app()` initializes the DB, builds an
Agno `AgentOS` app (with Agno's own scheduler disabled), then mounts: auth,
dashboard + CRM, WhatsApp webhooks, billing (Polar) webhooks, admin, an internal
schedule-fire endpoint, the seal-pubkey endpoint, the code-exec SDK API, Google
OAuth, and finally the SPA (exact `/` route first, catch-all last so API routes
win). It also starts two daemons: the schedule poller and the encrypted-store
purge. `app` is exposed lazily via module `__getattr__`, so importing the module
never requires a model key.

**`config.py`** — a single pydantic-settings `Settings` class; every knob is an
env var (see [docs/configuration.md](docs/configuration.md)). Catches the one
config mistake that would otherwise degrade silently — semantic recall on
without an embedder key — by auto-disabling recall with a loud warning at boot.

**`db/`** — `base.py` (engine/session), `tenancy.py` (Account → User → owned
rows; every user-owned row carries both `account_id` and `user_id`),
`scoped.py` — **`TenantScope`, the fail-closed enforcement point**: there is no
API to get an unfiltered query over a tenant-owned model; a missing tenant filter
raises instead of leaking. `agno_db.py` bridges `MAI_DB_URL` to Agno's own
Postgres/SQLite db (sessions, memory) on the same database.

**`agent/`** — `mai_agent.py` builds a per-user Agno `Agent` (model + memory +
skills + tools) and exposes `respond()`, the single model-calling seam (tests
mock it). `prompts.py` builds the system prompt, including the untrusted-content
boundary for forwarded chat text. `beliefs.py` is a per-user "understanding" doc
refreshed by background reflection every N messages. `semantic.py` does embedding
recall (Together hosted embeddings + cosine): only relevant memories/skills are
injected each turn, falling back to inject-everything if the embedder is down.
`runtime_memory.py` is a RAM-only rolling buffer, never persisted.

**`tools/`** — one file per toolkit: WhatsApp actions (send/read other chats,
media, reactions), memory, schedules, skills, profile, credentials, access
grants, web search (DuckDuckGo), Replicate generation, code exec, docs/knowledge
base. `registry.py` assembles the per-turn toolset; `is_owner=False` (a guest
invoking @mai in a granted chat) is a security boundary — guests only get
stateless tools. `tool_router.py` is an optional LLM pre-router that picks tool
categories per message (off by default; it fails open to the full toolset).

**`whatsapp/`** — `provider.py` defines the provider interface;
`gowa.py` implements it against the mai-gowa fork (`device_id` per tenant,
HMAC-verified webhooks, sealed-payload decryption). `webhook.py` mounts
`GET/POST /webhooks/whatsapp`: signature is checked before anything else, then
processing happens in a background task so the gateway gets an instant 200.
`router.py` implements the core product rule: **react only to the owner's own
messages containing `@mai`**, everything else is ignored; guests can trigger it
only in chats the owner granted (`access.py`). `media_in.py` reads inbound
images/audio via Replicate; `encstore.py` is the encrypted, time-windowed message
store — Mai's only chat history; `connections.py` handles QR login / status;
`dedupe.py` stops Mai reacting to its own replies. A `meta.py` provider for the
official Cloud API exists but the product model targets `gowa`.

**`scheduling/`** — `spec.py` (validated, tz-aware `ScheduleSpec`), `models.py`,
`service.py` (CRUD + per-user limits), `jobs.py` (`fire_schedule_by_id`,
tenant-scoped and idempotent), `poller.py` — an in-process daemon thread that
wakes every `SCHEDULER_POLL_INTERVAL` seconds and fires due schedules into their
owner's WhatsApp.

**`dashboard/` + `auth/`** — JWT-protected REST API for the SPA (register/login
in `auth/`, everything else in `dashboard/api.py` + `crm.py`), tenant-scoped like
every other entry point.

**`crypto/`** — `keyservice.py` keeps a fresh random X25519 keypair per 2-day
epoch; each private key is wrapped (NaCl secretbox) under the KEK (`MAI_KEK`)
before storage, and old epochs are crypto-shredded (`purge.py`). `sealing.py`
implements NaCl sealed boxes wire-compatible with Go, so the gateway can seal
message bodies to Mai's current public key (`api.py` serves it at
`GET /internal/seal-pubkey`, token-gated).

**Skills** — `skills_loader.py` loads bundled `SKILL.md` packages from `skills/`
(drop a folder in, no code change); `skills_store.py` makes them per-user: seeded
into the DB on first use, editable from chat/dashboard, materialized back to a
temp dir for Agno's `LocalSkills`.

**`code_exec/` + `runner/`** — optional "code as action": the agent writes Python
that runs in a sandbox (E2B cloud, or the self-hosted stateless `runner/`
service) and calls back into Mai through a short-lived token-scoped SDK
(`sdk_api.py`, `dispatch.py`) with guardrails and send caps.

**Everything else** — `chat_history.py` (unified web + WhatsApp transcript for
the dashboard), `delivery.py` (single outbound seam), `provisioning.py` /
`onboarding.py` (account/user creation + first-run welcome), `billing/`
(Polar.sh entitlement — self-hosters just stay entitled by default settings),
`metering/` (usage counters + quotas), `credentials.py` (encrypted per-tenant
credential vault for skills), `admin.py` (token-gated ops endpoints),
`logging_config.py` (structured logs with trace ids; message bodies logged only
if explicitly enabled).

## Frontend (`frontend/`)

React 18 + TypeScript + Vite + Ant Design. Pages for chat, schedules, skills,
memory, WhatsApp connection (QR), and settings. `npm run dev` for development;
the multi-stage `Dockerfile` builds it with an empty `VITE_API_URL` so the SPA
calls the origin it is served from.

## Request flow: a `@mai` turn

```mermaid
sequenceDiagram
  participant O as Owner (WhatsApp)
  participant G as mai-gowa
  participant W as webhook + router
  participant A as agent (Agno)
  participant T as tools
  O->>G: "@mai summarize this chat"
  G->>W: POST /webhooks/whatsapp (from_me event, HMAC-signed)
  W->>W: verify signature, 200 immediately, process in background
  W->>A: owner + @mai matched → build per-user agent (memory, skills, beliefs, recent chat context)
  A->>T: tool calls (read chat, search, schedule, generate…)
  T->>G: send reply / media into the same chat
  G->>O: message appears from the owner's own number
```

Scheduled jobs enter the same pipe from the other side: the poller finds a due
`Schedule`, re-scopes to its owner, runs the agent, and delivers through that
tenant's gateway device.

## Invariants worth knowing before you patch anything

1. **Tenant isolation is fail-closed.** Go through `TenantScope`; never hand-roll
   a query on tenant-owned tables.
2. **Silent by default.** The router acts only on owner `from_me` messages with
   `@mai` (or granted guests). Don't add auto-reply paths.
3. **Guests never get stateful tools.** `is_owner=False` in the tool registry is
   a security boundary.
4. **One outbound seam** (`delivery.py`) and **one model seam** (`respond()`) —
   keep them single so tests and metering stay honest.
