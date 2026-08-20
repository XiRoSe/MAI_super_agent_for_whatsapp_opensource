"""Mai domain models: the tenancy and billing schema.

Tenancy model: Account (paying subscriber + billing) -> User (a WhatsApp
identity) -> all per-user data. Every user-owned row carries BOTH `account_id`
and `user_id` so the fail-closed scope can filter on either.

`TenantOwned` / `AccountOwned` mixins are the contract the scope relies on:
- AccountOwned rows have `account_id`.
- TenantOwned rows have `account_id` AND `user_id`.
The scope inspects these to decide which filters are mandatory.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, created_at_column, utcnow


class SubscriptionStatus(str, enum.Enum):
    trial = "trial"
    active = "active"
    past_due = "past_due"
    canceled = "canceled"


class OAuthProvider(str, enum.Enum):
    google = "google"
    microsoft = "microsoft"


class WhatsAppConnectionStatus(str, enum.Enum):
    pending = "pending"          # created, awaiting QR scan
    connecting = "connecting"    # QR shown, not yet linked
    connected = "connected"      # linked and live
    disconnected = "disconnected"


class UsageKind(str, enum.Enum):
    agent_run = "agent_run"
    tokens_in = "tokens_in"
    tokens_out = "tokens_out"
    wa_message_in = "wa_message_in"
    wa_message_out = "wa_message_out"
    wa_template_out = "wa_template_out"  # billable proactive send


# --- Marker mixins the scope keys off (no columns; pure declaration) ---------
class AccountOwned:
    """Row belongs to an account; `account_id` filter is mandatory."""


class TenantOwned(AccountOwned):
    """Row belongs to a user within an account; both filters mandatory."""


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Billing (Polar.sh)
    polar_customer_id: Mapped[str | None] = mapped_column(String(128), index=True)
    polar_subscription_id: Mapped[str | None] = mapped_column(String(128), index=True)
    plan: Mapped[str] = mapped_column(String(64), default="beta")
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, native_enum=False, length=16),
        default=SubscriptionStatus.trial,
        nullable=False,
    )
    # When a past_due grace period ends (None unless in dunning).
    grace_until: Mapped[datetime | None] = mapped_column(nullable=True)
    auto_memory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = created_at_column()

    users: Mapped[list["User"]] = relationship(back_populates="account")

    def __init__(self, **kwargs):  # type: ignore[override]
        kwargs.setdefault("auto_memory", False)
        super().__init__(**kwargs)


class User(Base, AccountOwned):
    __tablename__ = "users"
    # A number is unique WITHIN an account, not globally: in the per-tenant
    # topology the same person may message several tenants' Mai numbers.
    __table_args__ = (
        UniqueConstraint("account_id", "whatsapp_number", name="uq_users_account_wa_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    whatsapp_number: Mapped[str] = mapped_column(String(32), nullable=False)
    # The id we pass to every Agno call. Stable, opaque, namespaced by account
    # so two tenants can never collide in Agno's memory/session stores.
    agno_user_id: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    timezone: Mapped[str | None] = mapped_column(String(64))  # IANA tz, captured on first scheduling
    created_at: Mapped[datetime] = created_at_column()

    account: Mapped[Account] = relationship(back_populates="users")

    @staticmethod
    def make_agno_user_id(account_id: int, whatsapp_number: str) -> str:
        # Namespacing the Agno user id with the account id is defense-in-depth:
        # even a bug that reused a number across accounts cannot cross memories.
        return f"acct{account_id}:wa{whatsapp_number}"


class AuthUser(Base, AccountOwned):
    """Dashboard login identity (email + password), tied to an account.

    Distinct from `User` (a WhatsApp identity). One AuthUser per account for MVP;
    the schema allows several so a team could share an account later.
    """

    __tablename__ = "auth_users"
    __table_args__ = (UniqueConstraint("email", name="uq_auth_users_email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = created_at_column()


class WhatsAppConnection(Base, AccountOwned):
    """A tenant's own WhatsApp number, linked via an unofficial gateway (QR).

    One per account for MVP. `session_id` identifies the gateway session; for a
    multi-session gateway it's the session name, for one-instance-per-tenant it
    keys the per-session base URL.
    """

    __tablename__ = "whatsapp_connections"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    session_id: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(32))  # set once linked
    status: Mapped[WhatsAppConnectionStatus] = mapped_column(
        Enum(WhatsAppConnectionStatus, native_enum=False, length=16),
        default=WhatsAppConnectionStatus.pending,
        nullable=False,
    )
    created_at: Mapped[datetime] = created_at_column()
    connected_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # First-run onboarding: gates the one-time welcome so it fires exactly once per link.
    welcome_sent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class OAuthToken(Base, TenantOwned):
    __tablename__ = "oauth_tokens"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_oauth_user_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    provider: Mapped[OAuthProvider] = mapped_column(
        Enum(OAuthProvider, native_enum=False, length=16), nullable=False
    )
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    scopes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()


class WaSeenMessage(Base):
    """Dedup/echo guard for WhatsApp. Keys: 'msg:<message_id>' (processed or
    Mai-sent ids) and 'rh:<reply_text_hash>' (Mai's own reply text), so a
    duplicate delivery or Mai's own echoed reply is never processed twice."""

    __tablename__ = "wa_seen_messages"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    created_at: Mapped[datetime] = created_at_column()


class MediaRef(Base):
    """Durable WhatsApp `message_id` -> decrypted media URL mapping.

    The gateway only hands us a usable (decrypted) `statics/...` media URL at
    webhook time; the `/messages` endpoint later returns just the ENCRYPTED
    reference. So we persist the message_id -> url mapping to survive process
    restarts (an in-memory map was wiped on every deploy). Stores ONLY the tiny
    string mapping — never the media bytes. No tenant columns: a WhatsApp stanza
    id is globally unique and the value is just a URL.
    """

    __tablename__ = "media_refs"

    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class SealedMessage(Base, AccountOwned):
    """Encrypted, time-windowed WhatsApp history — the ONLY message store Mai keeps.

    The message TEXT is sealed (NaCl sealed box) to the active epoch's public key
    and stored as base64 `ciphertext`; the epoch PRIVATE key is itself persisted
    only as KEK-wrapped ciphertext in the `epoch_keys` table (useless without the
    KEK, which lives ONLY in `MAI_KEK`). Everything else (chat_id, direction, epoch_id, ts,
    sender label, media pointer) is plaintext ROUTING METADATA, not content. Rows
    older than the 4-day window are purged; once their epoch key is crypto-shredded
    the ciphertext is permanently unrecoverable. NEVER store plaintext text here.
    """

    __tablename__ = "sealed_messages"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    chat_id: Mapped[str] = mapped_column(String(96), index=True, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)   # 'in' | 'out'
    epoch_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(128), index=True)
    sender: Mapped[str | None] = mapped_column(String(128))
    sender_number: Mapped[str | None] = mapped_column(String(32))
    media_type: Mapped[str | None] = mapped_column(String(16))
    media_url: Mapped[str | None] = mapped_column(Text)   # live statics path (2A); finalized in 2B
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)   # base64 sealed box of the text
    ts: Mapped[str | None] = mapped_column(String(40))   # original WhatsApp timestamp string
    created_at: Mapped[datetime] = created_at_column()


class ChatMessage(Base, TenantOwned):
    """Display log of a user's conversation — web chat + WhatsApp in one place.

    This is the history the dashboard chat paginates over. Agno still owns the
    agent's working memory/session context; this table is the user-facing record
    so they can scroll their whole conversation regardless of channel.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)   # user | assistant
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # web | whatsapp | schedule
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class AgentBelief(Base, TenantOwned):
    """Mai's self-authored, evolving perspective for one user (its personality /
    principles / opinions — NOT facts about the user). Updated in the background
    every N messages, and only when something genuinely shifts."""

    __tablename__ = "agent_beliefs"
    __table_args__ = (UniqueConstraint("user_id", name="uq_belief_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    msg_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class WhatsAppChatGrant(Base, AccountOwned):
    """A chat/group where the owner has allowed OTHERS to invoke @Mai.

    Account-scoped (not per-user): the owner opens a channel; anyone in it may then
    tag @Mai. Guests run under the owner's account but with a restricted toolset.
    """

    __tablename__ = "whatsapp_chat_grants"
    __table_args__ = (UniqueConstraint("account_id", "chat_id", name="uq_grant_account_chat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    chat_id: Mapped[str] = mapped_column(String(96), nullable=False)
    label: Mapped[str | None] = mapped_column(String(120))  # human name of the chat/group
    created_at: Mapped[datetime] = created_at_column()


class Skill(Base, TenantOwned):
    """A per-user Agent Skill (SKILL.md). Seeded with our defaults on first use,
    then fully editable by the user (add / edit / disable / delete)."""

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_skill_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)        # kebab-case id
    description: Mapped[str] = mapped_column(Text, nullable=False)        # when-to-use trigger
    content: Mapped[str] = mapped_column(Text, nullable=False)           # SKILL.md body
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Credential(Base, TenantOwned):
    """Generic per-user credential vault for skill-based integrations.

    One row per (user, service). `data_enc` is an encrypted JSON blob holding
    whatever that service needs — an API key, an OAuth token set, a
    service-account JSON, etc. No per-service schema: skills know how to use it.
    """

    __tablename__ = "credentials"
    __table_args__ = (UniqueConstraint("user_id", "service", name="uq_cred_user_service"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    service: Mapped[str] = mapped_column(String(64), nullable=False)  # free-form key
    label: Mapped[str | None] = mapped_column(String(128))
    data_enc: Mapped[str] = mapped_column(Text, nullable=False)  # encrypted JSON
    created_at: Mapped[datetime] = created_at_column()


class UsageEvent(Base, TenantOwned):
    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[UsageKind] = mapped_column(
        Enum(UsageKind, native_enum=False, length=24), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    meta: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()


class WhatsAppWindow(Base, TenantOwned):
    """Tracks the 24h customer-service window per user (last inbound time)."""

    __tablename__ = "whatsapp_windows"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    last_inbound_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ErrorReport(Base, TenantOwned):
    """A user-approved error report: a Mai turn the user flagged as wrong, captured as an
    eval-ready, mostly-deterministic snapshot (see `evals/from_report.py`).

    Created ONLY after the user is shown EXACTLY the readable content and approves — Mai
    never files one silently. Design: the reproducible *spine* is captured deterministically
    from trusted sources (the Agno session, the chat, the Schedule rows), so it never depends
    on the model's self-report; Mai contributes only its interpretation (`summary` /
    `mai_expected` / `mai_notes`). The full session is held encrypted as the replay fixture.

    Migration note: this evolves the v1 row in place. `_sync_columns` adds the new (nullable)
    columns; the original NOT-NULL columns (`command`, `summary`, `status`) are kept and always
    populated, so an existing v1 table keeps working without a manual migration.
    """

    __tablename__ = "error_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)

    # ── deterministic capture — the trusted spine, shown in the preview ──
    command: Mapped[str] = mapped_column(Text, nullable=False)          # triggering @Mai message
    surface: Mapped[str | None] = mapped_column(String(16))            # "turn" | "schedule"
    chat_type: Mapped[str | None] = mapped_column(String(16))          # self_dm | direct | group
    contact_name: Mapped[str | None] = mapped_column(String(128))
    contact_number: Mapped[str | None] = mapped_column(String(32))
    chat_jid: Mapped[str | None] = mapped_column(String(96))
    invoke_message_id: Mapped[str | None] = mapped_column(String(128))  # the turn that staged the preview
    transcript_json: Mapped[str | None] = mapped_column(Text)           # list[CapturedMessage]
    tool_calls_json: Mapped[str | None] = mapped_column(Text)           # list[CapturedToolCall]
    schedule_json: Mapped[str | None] = mapped_column(Text)             # CapturedSchedule | None
    model_id: Mapped[str | None] = mapped_column(String(96))
    git_rev: Mapped[str | None] = mapped_column(String(40))

    # ── Mai's contribution — advisory, also shown ──
    summary: Mapped[str] = mapped_column(Text, nullable=False)          # Mai's read of the problem
    mai_expected: Mapped[str | None] = mapped_column(Text)  # what she expected to happen
    mai_notes: Mapped[str | None] = mapped_column(Text)        # open: hypotheses / context

    # ── evidence — encrypted full session (replay fixture), never shown ──
    raw_session_enc: Mapped[str | None] = mapped_column(Text)

    # "pending" = previewed, awaiting the user's approval (hidden from operators); a confirmed
    # call in a LATER turn promotes it to "open" (filed). open | exported | fixed.
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class WaPendingSend(Base, TenantOwned):
    """A cross-chat WhatsApp send staged for the user's confirmation — the send-safety gate.

    Mai must confirm the recipient (name + full number) before messaging a chat OTHER than the
    one she was tagged in — the "similar number" wrong-recipient bug. Enforced in code like
    ErrorReport's preview gate (the prompt alone doesn't hold — GLM sent on turn 1 in the eval):
    surfacing a cross-chat recipient (a send preview OR a lookup_contact hit) STAGES one of these,
    and a send releases only with confirmed=True in a LATER turn (different `invoke_message_id`)
    while the row is fresh (short TTL). When the preview staged a `message`, THAT text is sent
    (sent == shown — a paraphrase on the confirm turn can't change it); lookup-staged rows carry
    no text and the confirm call supplies it.

    Ephemeral: consumed on send, replaced by the next staged send to the same target, or expired
    by TTL. Auto-created by `Base.metadata.create_all` + `_sync_columns`; no manual migration.
    """

    __tablename__ = "wa_pending_sends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)

    target_jid: Mapped[str] = mapped_column(String(96), nullable=False)   # where the send will go
    target_label: Mapped[str | None] = mapped_column(String(255))         # "name (+number)" shown to the user
    message: Mapped[str] = mapped_column(Text, nullable=False)            # the previewed text — sent verbatim
    invoke_message_id: Mapped[str | None] = mapped_column(String(128))    # the turn that staged this preview
    created_at: Mapped[datetime] = created_at_column()


class EpochKey(Base):
    """KEK-wrapped epoch keypair — the ONLY key store, living in the APP DB.

    `wrapped_private` is the epoch's X25519 PRIVATE key encrypted (NaCl secretbox /
    XSalsa20-Poly1305) under the KEK; it is CIPHERTEXT, useless without the KEK,
    which lives ONLY in the `MAI_KEK` env var (a Railway Sealed Variable), never
    in this DB. `public_key` is the matching X25519 public key (base64), safe in
    the clear. A dump of this table therefore yields no usable keys. Crypto-shred =
    delete the row → the wrapped private key is gone → that epoch's sealed messages
    are permanently unrecoverable. (Not Account/Tenant-owned: epoch keys are
    process-global, not per-tenant.)
    """

    __tablename__ = "epoch_keys"

    epoch_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    wrapped_private: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_column()
