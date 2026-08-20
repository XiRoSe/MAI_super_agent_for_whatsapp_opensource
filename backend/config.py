"""Typed application configuration.

A single settings module reading environment variables (and an optional .env).
Everything the app needs to be wired differently per environment lives here:
model provider, model id, DB URL, WhatsApp provider, billing, quotas, feature
flags. Import `settings` anywhere; it is a process-wide singleton.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ModelProvider = Literal["openrouter", "anthropic", "together"]
WhatsAppProvider = Literal["meta", "twilio", "gowa"]
Environment = Literal["local", "beta", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # pydantic treats "model_" as a protected namespace; we use MODEL_* names.
        protected_namespaces=(),
    )

    # ---- Core ----
    mai_env: Environment = Field(
        default="local",
        validation_alias="MAI_ENV",
    )
    mai_log_level: str = Field(
        default="INFO",
        validation_alias="MAI_LOG_LEVEL",
    )
    mai_log_message_bodies: bool = Field(
        default=False,
        validation_alias="MAI_LOG_MESSAGE_BODIES",
    )

    # ---- Database ----
    mai_db_url: str = Field(
        default="sqlite:///tmp/mai.db",
        validation_alias="MAI_DB_URL",
    )

    # ---- Model provider / factory ----
    model_provider: ModelProvider = "together"
    model_id: str = "zai-org/GLM-5.2"
    model_temperature: float = 0.4
    # Reply-length budget. GLM-5.2's reasoning tokens spend from the same budget as
    # the visible text, so this must stay well above the longest expected reply
    # (agno's OpenRouter default of 1024 truncated replies mid-sentence in prod).
    model_max_tokens: int = 16384
    model_enable_prompt_caching: bool = True
    # Auto-retry transient provider errors (e.g. Together 500s) before giving up.
    model_retries: int = 2
    # Fallback provider/model: used automatically if the primary fails a turn.
    fallback_model_provider: str = ""
    fallback_model_id: str = ""

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    anthropic_api_key: str = ""

    # Together AI (OpenAI-compatible; hosts GLM-5 as `zai-org/GLM-5`)
    together_api_key: str = ""
    together_base_url: str = "https://api.together.xyz/v1"

    # ---- Crypto ----
    mai_encryption_key: str = Field(
        default="",  # Fernet key for OAuth token encryption
        validation_alias="MAI_ENCRYPTION_KEY",
    )
    # Key-encryption-key (KEK): base64 of 32 random bytes. Every epoch's X25519
    # private key is wrapped (NaCl secretbox) under this before it is stored, so
    # the app DB only ever holds ciphertext. In PRODUCTION set this via your
    # platform's secret manager (e.g. a Railway sealed variable — write-only,
    # never readable back). Empty default => FAIL-CLOSED in production
    # (KeyService refuses to start); off-prod a per-run KEK is generated.
    mai_kek: str = Field(
        default="",
        validation_alias="MAI_KEK",
    )
    mai_seal_retention_days: int = Field(
        default=4,   # rolling encrypted-store window (current + previous epoch)
        validation_alias="MAI_SEAL_RETENTION_DAYS",
    )

    # ---- WhatsApp ----
    whatsapp_provider: WhatsAppProvider = "meta"
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_api_version: str = "v21.0"

    # ---- Billing (Polar.sh) ----
    polar_access_token: str = ""
    polar_webhook_secret: str = ""
    polar_server: Literal["sandbox", "production"] = "sandbox"
    polar_success_url: str = "https://mai.example.com/billing/success"
    polar_product_id: str = ""  # the subscription product to check out

    # ---- Google OAuth ----
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""

    # ---- Quotas / limits ----
    quota_messages_per_day: int = 200
    quota_tokens_per_month: int = 2_000_000
    max_active_schedules_per_user: int = 20
    min_schedule_interval_minutes: int = 1

    # ---- Skills ----
    # Directory of SKILL.md packages. Drop a folder in here to add a skill — no
    # code change. Relative paths resolve from the repo root.
    skills_dir: str = "skills"

    # How skills reach the agent:
    #   "vector" — semantic-recall the relevant skills per turn and inject their FULL bodies
    #              into the prompt (needs the embedder; see semantic_recall_enabled). The
    #              production default: the model reliably follows what's in front of it.
    #   "stub"   — agno-native progressive disclosure: inject only name+description, the model
    #              fetches the body via get_skill_instructions. No embedder needed, leaner
    #              prompt — but the production model skips the fetch step ~1/3-2/3 of the time
    #              (ErrorReports #24/#26; evals report_24_*/report_26_*), so NOT for production.
    skill_mode: str = "vector"

    # ---- Semantic recall (inject only the RELEVANT memories/skills per turn) ----
    # When on, the current user message is embedded (Together hosted embeddings)
    # and scored against each saved memory / skill trigger; only the close matches
    # go into the system prompt. Falls back to injecting ALL if embeddings are
    # unavailable, so a turn never breaks. Multilingual model (Hebrew + English).
    semantic_recall_enabled: bool = True
    embed_model: str = "intfloat/multilingual-e5-large-instruct"
    memory_recall_top_k: int = 8
    # Cosine on e5-instruct (asymmetric query-prefix / raw-passage). Tuned to the
    # live separation: relevant 0.82-0.90 vs irrelevant 0.75-0.77; we sit slightly
    # toward inclusion so a relevant item is never dropped. Memory is slightly more
    # inclusive than skills because dropping a user-saved fact is a visible failure.
    memory_recall_threshold: float = 0.75
    skill_recall_threshold: float = 0.78

    @model_validator(mode="after")
    def _disable_recall_without_embedder(self) -> "Settings":
        """Semantic recall needs the Together-hosted embedder. When recall is ON but
        TOGETHER_API_KEY is empty the embedder can't run, and recall would silently
        fall back to injecting ALL memories/skills (truncated) instead of only the
        relevant subset. Rather than refusing to boot, auto-disable recall with a loud
        warning — the app must be able to run with a single model provider configured
        (e.g. only ANTHROPIC_API_KEY). Set TOGETHER_API_KEY to enable recall, or set
        SEMANTIC_RECALL_ENABLED=false to opt out explicitly and silence the warning."""
        if self.semantic_recall_enabled and not self.together_api_key:
            import logging

            logging.getLogger(__name__).warning(
                "SEMANTIC_RECALL_ENABLED is on but TOGETHER_API_KEY is empty — the "
                "embedder can't run, so semantic memory/skill recall is DISABLED for "
                "this process (all memories/skills get injected instead of only the "
                "relevant ones). Set TOGETHER_API_KEY to enable recall, or set "
                "SEMANTIC_RECALL_ENABLED=false to silence this warning."
            )
            self.semantic_recall_enabled = False
        return self

    # ---- Scheduler ----
    scheduler_enabled: bool = True
    scheduler_poll_interval: int = 15  # seconds between poll cycles

    # ---- Dashboard auth (JWT) ----
    mai_jwt_secret: str = Field(
        default="",  # HMAC secret for dashboard JWTs
        validation_alias="MAI_JWT_SECRET",
    )
    jwt_expiry_hours: int = 720        # 30 days
    cors_origins: str = "*"            # comma-separated FE origins (or *)

    # ---- Unofficial WhatsApp gateway (GOWA / WAHA / Evolution) ----
    # Session-aware. For a multi-session gateway set one base url; for
    # one-instance-per-tenant the provider can override per session.
    whatsapp_gateway_url: str = ""     # e.g. https://gowa.example.com
    whatsapp_gateway_user: str = ""    # basic-auth user (GOWA) or ""
    whatsapp_gateway_password: str = ""
    whatsapp_gateway_api_key: str = "" # api key header (WAHA/Evolution) or ""
    whatsapp_gateway_webhook_secret: str = ""
    # How many recent messages of the tagged chat to give Mai as context.
    whatsapp_context_messages: int = 10

    # ---- Replicate (platform-provided; runs AI models for every tenant) ----
    replicate_api_token: str = ""
    # Models used to READ inbound media (override via env if one is deprecated).
    replicate_vision_model: str = "andreasjansson/blip-2"  # answers questions about images
    replicate_transcribe_model: str = "openai/whisper"
    replicate_video_model: str = ""  # set to a video-understanding model to read videos

    # ---- Code-as-action (run_python in an E2B sandbox) ----
    code_exec_enabled: bool = False
    e2b_api_key: str = ""
    code_exec_timeout_seconds: int = 300  # sandbox script wall-clock (5 min)
    code_exec_send_cap: int = 10
    code_exec_token_ttl_seconds: int = 300
    code_exec_max_result_chars: int = 6000
    sdk_base_url: str = ""  # public base URL the sandbox calls back to; "" -> derive from PORT
    runner_url: str = ""            # separate sandbox runner service base URL
    runner_shared_secret: str = ""  # auth between mai and the runner
    guardrail_enabled: bool = True
    guardrail_model: str = "Qwen/Qwen2.5-7B-Instruct-Turbo"  # fast, accurate pre-flight screen
    # CodeAct mode: data/gen tools (chats, profile, models, media) become SDK-only — reachable
    # ONLY via code_exec — and tool_choice is constrained so the model can't call unregistered
    # functions (it must write code for them). Simple tools (memory/skills/schedule/web) stay direct.
    codeact_mode: bool = False
    agent_tool_call_limit: int = 6  # max tool calls per turn (so the loop ends with a text reply)
    agent_reasoning: bool = False  # Agno reasoning: think step-by-step before acting (slower, more tokens)

    # Hard cap on a single agent run so a stuck tool/model never hangs forever.
    # Must exceed code_exec_timeout_seconds (a 5-min sandbox run + model overhead).
    agent_run_timeout_seconds: int = 420

    # ---- Self-evolving "belief system" (Mai's perspective; background reflection) ----
    belief_system_enabled: bool = True
    belief_reflect_every: int = 5   # reflect once every N of the user's messages

    # ---- LLM tool router (which optional toolkits a turn needs; any language) ----
    # OFF by default: at our scale the model picks tools fine on its own, and a
    # pre-router can mis-classify and STRAND a tool (e.g. Replicate run_model) +
    # adds a round-trip. Re-enable via env only with a more robust design.
    tool_router_enabled: bool = False
    tool_router_model_id: str = ""        # empty -> use the main model
    tool_router_timeout_seconds: int = 20  # fall back to full toolset if slower

    # ---- Admin / ops ----
    mai_admin_token: str = Field(
        default="",  # bearer token gating the /admin endpoints
        validation_alias="MAI_ADMIN_TOKEN",
    )

    # Comma-separated allowlist of platform-operator emails. Accounts on this list
    # get the admin CRM endpoints and `is_admin: true` on GET /api/account.
    # Empty (the default) => no admins: the CRM returns 403 for everyone.
    mai_admin_emails: str = Field(
        default="",
        validation_alias="MAI_ADMIN_EMAILS",
    )

    # Shared token GOWA (2B) / Mai's on-receipt sealer present to fetch the
    # current epoch public key from GET /internal/seal-pubkey. Empty => allowed
    # only outside production.
    seal_pubkey_token: str = ""

    # ---- Server ----
    port: int = 7777
    public_base_url: str = "http://localhost:7777"

    # ---- Derived helpers ----
    @property
    def is_sqlite(self) -> bool:
        return self.mai_db_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.mai_env == "production"

    @property
    def admin_emails(self) -> set[str]:
        """MAI_ADMIN_EMAILS parsed: comma-split, trimmed, lowercased; empty => no admins."""
        return {e.strip().lower() for e in self.mai_admin_emails.split(",") if e.strip()}

    def require(self, *attrs: str) -> None:
        """Fail fast if required secrets are missing for a given subsystem."""
        missing = [a for a in attrs if not getattr(self, a, None)]
        if missing:
            raise RuntimeError(
                f"Missing required configuration: {', '.join(missing)}. "
                "Set them in the environment or .env (see .env.example)."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Process-wide singleton for convenient importing.
settings = get_settings()
