# Configuration reference

Every setting is an environment variable, read (optionally from a `.env` file)
by the typed `Settings` class in [`backend/config.py`](../backend/config.py).
Names are case-insensitive; the tables below use the uppercase env-var form.
`.env.example` at the repo root is the annotated starting template.

Boot-time behavior worth knowing: if `SEMANTIC_RECALL_ENABLED=true` (the
default) and `TOGETHER_API_KEY` is empty, the embedder can't run, so recall is
**automatically disabled for that process with a loud warning in the logs**
(all memories/skills get injected instead of only the relevant ones). Set the
key to enable recall, or set `SEMANTIC_RECALL_ENABLED=false` to opt out
explicitly and silence the warning.

Note: `GOWA_USER`, `GOWA_PASSWORD`, `GOWA_WEBHOOK_SECRET`, and
`POSTGRES_PASSWORD` are **Docker-Compose-only** variables (see the top of
`.env.example`) — the compose file maps them onto the `WHATSAPP_GATEWAY_*`
variables below and the gateway/Postgres services, so the values always match.

## Core

| Env var | Default | What it does |
|---|---|---|
| `MAI_ENV` | `local` | `local` \| `beta` \| `production`. In `production`, several safety checks fail closed (missing `MAI_KEK`, missing webhook secret, missing seal-pubkey token). |
| `MAI_LOG_LEVEL` | `INFO` | Log level for the structured JSON logs. |
| `MAI_LOG_MESSAGE_BODIES` | `false` | If `true`, logs full message text. **Dev only.** |
| `PORT` | `7777` | HTTP port the server binds (Railway injects its own). |
| `PUBLIC_BASE_URL` | `http://localhost:7777` | The externally reachable base URL of this MAI instance (used for OAuth callbacks and sandbox callbacks). |

## Database

| Env var | Default | What it does |
|---|---|---|
| `MAI_DB_URL` | `sqlite:///tmp/mai.db` | SQLAlchemy URL. SQLite for local dev; for Postgres use `postgresql+psycopg://USER:PASS@HOST:5432/DBNAME` (plain `postgresql://` / `postgres://` URLs are normalized to the psycopg3 driver automatically). Agno's session/memory tables live on the same database. |

## Model provider

The whole app's "brain" switches with one variable — no code changes.

| Env var | Default | What it does |
|---|---|---|
| `MODEL_PROVIDER` | `together` | `together` \| `anthropic` \| `openrouter`. |
| `MODEL_ID` | `zai-org/GLM-5.2` | Model id at the chosen provider. |
| `MODEL_TEMPERATURE` | `0.4` | Sampling temperature. |
| `MODEL_MAX_TOKENS` | `16384` | Reply budget. Reasoning models spend "thinking" tokens from the same budget — keep this high or replies truncate. |
| `MODEL_ENABLE_PROMPT_CACHING` | `true` | Provider-side prompt caching when supported. |
| `MODEL_RETRIES` | `2` | Auto-retries for transient provider errors. |
| `FALLBACK_MODEL_PROVIDER` | *(empty)* | Fallback provider used automatically if the primary fails a turn. |
| `FALLBACK_MODEL_ID` | *(empty)* | Model id for the fallback. |
| `TOGETHER_API_KEY` | *(empty)* | Together AI key. Also powers the **embedder** for semantic recall, so you usually need it even with another chat provider. |
| `TOGETHER_BASE_URL` | `https://api.together.xyz/v1` | Together endpoint (OpenAI-compatible). |
| `OPENROUTER_API_KEY` | *(empty)* | OpenRouter key. |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter endpoint. |
| `ANTHROPIC_API_KEY` | *(empty)* | Anthropic key. |

## Crypto

| Env var | Default | What it does |
|---|---|---|
| `MAI_ENCRYPTION_KEY` | *(empty)* | Fernet key encrypting OAuth tokens and stored credentials at rest. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `MAI_KEK` | *(empty)* | Key-encryption-key: base64 of 32 random bytes (`python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"`). Wraps each epoch's X25519 private key before storage. Empty ⇒ **fail-closed in production**; outside production a per-run KEK is generated (encrypted history won't survive restarts — set it anyway). |
| `MAI_SEAL_RETENTION_DAYS` | `4` | Rolling window of the encrypted message store (current + previous epoch); older epochs are crypto-shredded. |
| `SEAL_PUBKEY_TOKEN` | *(empty)* | Token the gateway (or the on-receipt sealer) presents to `GET /internal/seal-pubkey`. Empty is allowed only outside production. |

## WhatsApp

| Env var | Default | What it does |
|---|---|---|
| `WHATSAPP_PROVIDER` | `meta` | **Set `gowa` for self-hosting** — the whole @mai-on-your-own-number model requires it. (`meta` = official Cloud API, kept for a different product mode; `twilio` is a placeholder.) |
| `WHATSAPP_GATEWAY_URL` | *(empty)* | Base URL of your mai-gowa instance, e.g. `http://gowa:3000`. |
| `WHATSAPP_GATEWAY_USER` | *(empty)* | Basic-auth user for the gateway (GOWA `APP_BASIC_AUTH`). |
| `WHATSAPP_GATEWAY_PASSWORD` | *(empty)* | Basic-auth password. |
| `WHATSAPP_GATEWAY_API_KEY` | *(empty)* | `X-Api-Key` header for WAHA/Evolution-style gateways; leave blank for GOWA. |
| `WHATSAPP_GATEWAY_WEBHOOK_SECRET` | *(empty)* | HMAC-SHA256 secret verifying inbound webhooks (`X-Hub-Signature-256`). Must equal the gateway's `WHATSAPP_WEBHOOK_SECRET`. Empty ⇒ allowed in dev, **rejected in production**. |
| `WHATSAPP_CONTEXT_MESSAGES` | `10` | How many recent messages of the tagged chat are given to MAI as context. |
| `WHATSAPP_VERIFY_TOKEN` / `WHATSAPP_APP_SECRET` / `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID` / `WHATSAPP_API_VERSION` | *(empty / `v21.0`)* | Meta Cloud API settings — only used when `WHATSAPP_PROVIDER=meta`. |

## Dashboard auth

| Env var | Default | What it does |
|---|---|---|
| `MAI_JWT_SECRET` | *(empty)* | HMAC secret for dashboard JWTs. Set a long random string. |
| `JWT_EXPIRY_HOURS` | `720` | Token lifetime (30 days). |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins. Irrelevant in the default single-origin deployment (SPA served by the backend), but tighten it if you host the frontend elsewhere. |

## Semantic recall & skills

| Env var | Default | What it does |
|---|---|---|
| `SEMANTIC_RECALL_ENABLED` | `true` | Embed each user message and inject only the relevant memories/skills. Requires `TOGETHER_API_KEY` — without it, recall auto-disables at boot with a warning (see above). Falls back to injecting everything if the embedder errors mid-flight. |
| `EMBED_MODEL` | `intfloat/multilingual-e5-large-instruct` | Hosted embedding model (multilingual). |
| `MEMORY_RECALL_TOP_K` | `8` | Max memories injected per turn. |
| `MEMORY_RECALL_THRESHOLD` | `0.75` | Cosine threshold for memories (deliberately inclusive). |
| `SKILL_RECALL_THRESHOLD` | `0.78` | Cosine threshold for skills. |
| `SKILLS_DIR` | `skills` | Directory of bundled `SKILL.md` packages (seeded per user on first use). Drop a folder in to add a skill. |
| `SKILL_MODE` | `vector` | `vector` = recall relevant skills and inject full bodies (production default); `stub` = Agno progressive disclosure, name+description only (leaner, but models sometimes skip the fetch). |

## Scheduler

| Env var | Default | What it does |
|---|---|---|
| `SCHEDULER_ENABLED` | `true` | Run the in-process schedule poller. |
| `SCHEDULER_POLL_INTERVAL` | `15` | Seconds between poll cycles. |
| `MAX_ACTIVE_SCHEDULES_PER_USER` | `20` | Per-user schedule cap. |
| `MIN_SCHEDULE_INTERVAL_MINUTES` | `1` | Minimum recurrence interval. |

## Quotas

| Env var | Default | What it does |
|---|---|---|
| `QUOTA_MESSAGES_PER_DAY` | `200` | Per-user daily message quota. |
| `QUOTA_TOKENS_PER_MONTH` | `2000000` | Per-account monthly token quota. |

## Media (Replicate)

| Env var | Default | What it does |
|---|---|---|
| `REPLICATE_API_TOKEN` | *(empty)* | Enables reading inbound media and generating images/video. Without it, media tools are unavailable. |
| `REPLICATE_VISION_MODEL` | `andreasjansson/blip-2` | Model that answers questions about inbound images. |
| `REPLICATE_TRANSCRIBE_MODEL` | `openai/whisper` | Voice-note transcription. |
| `REPLICATE_VIDEO_MODEL` | *(empty)* | Set to a video-understanding model to read inbound videos. |

## Code execution (optional)

| Env var | Default | What it does |
|---|---|---|
| `CODE_EXEC_ENABLED` | `false` | Let the agent run Python in a sandbox. |
| `E2B_API_KEY` | *(empty)* | E2B cloud sandbox key (one of the two sandbox backends). |
| `RUNNER_URL` | *(empty)* | Base URL of a self-hosted `runner/` service (the other backend). |
| `RUNNER_SHARED_SECRET` | *(empty)* | Auth between MAI and the runner. |
| `CODE_EXEC_TIMEOUT_SECONDS` | `300` | Sandbox wall-clock limit. |
| `CODE_EXEC_SEND_CAP` | `10` | Max outbound sends a single sandbox run may trigger. |
| `CODE_EXEC_TOKEN_TTL_SECONDS` | `300` | Lifetime of the token the sandbox uses to call back into MAI. |
| `CODE_EXEC_MAX_RESULT_CHARS` | `6000` | Truncation limit for sandbox results. |
| `SDK_BASE_URL` | *(empty)* | Public base URL the sandbox calls back to; empty derives from `PORT`. |
| `GUARDRAIL_ENABLED` | `true` | Pre-flight LLM screen of generated code. |
| `GUARDRAIL_MODEL` | `Qwen/Qwen2.5-7B-Instruct-Turbo` | Model used for the guardrail screen. |
| `CODEACT_MODE` | `false` | Make data/generation tools reachable only via code execution. |

## Agent behavior

| Env var | Default | What it does |
|---|---|---|
| `AGENT_TOOL_CALL_LIMIT` | `6` | Max tool calls per turn. |
| `AGENT_REASONING` | `false` | Agno step-by-step reasoning (slower, more tokens). |
| `AGENT_RUN_TIMEOUT_SECONDS` | `420` | Hard cap on a single agent run. Must exceed `CODE_EXEC_TIMEOUT_SECONDS`. |
| `BELIEF_SYSTEM_ENABLED` | `true` | Background reflection maintaining a per-user "understanding" doc. |
| `BELIEF_REFLECT_EVERY` | `5` | Reflect once every N user messages. |
| `TOOL_ROUTER_ENABLED` | `false` | LLM pre-router selecting tool categories per message. Off by default (can strand a tool; adds a round-trip). |
| `TOOL_ROUTER_MODEL_ID` | *(empty)* | Router model; empty = main model. |
| `TOOL_ROUTER_TIMEOUT_SECONDS` | `20` | Router timeout; falls back to the full toolset. |

## Integrations (optional)

| Env var | Default | What it does |
|---|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | *(empty)* | Google OAuth for the Drive/Docs knowledge-base features. |
| `GOOGLE_REDIRECT_URI` | *(empty)* | OAuth callback URL; empty derives `PUBLIC_BASE_URL/oauth/google/callback`. |

## Billing (hosted-mode only)

Self-hosters can ignore this block: new accounts are created in `trial` status,
which is entitled — without Polar configured nothing ever downgrades them.

| Env var | Default | What it does |
|---|---|---|
| `POLAR_ACCESS_TOKEN` / `POLAR_WEBHOOK_SECRET` | *(empty)* | Polar.sh API + webhook verification. |
| `POLAR_SERVER` | `sandbox` | `sandbox` \| `production`. |
| `POLAR_SUCCESS_URL` | `https://mai.example.com/billing/success` | Post-checkout redirect. |
| `POLAR_PRODUCT_ID` | *(empty)* | Subscription product for checkout. |

## Admin / ops

| Env var | Default | What it does |
|---|---|---|
| `MAI_ADMIN_TOKEN` | *(empty)* | Bearer token gating the `/admin` endpoints. Empty disables them entirely (requests get 403). |
| `MAI_ADMIN_EMAILS` | *(empty)* | Comma-separated emails allowed into the admin CRM (and flagged `is_admin` on `GET /api/account`). Empty = no admins. |
