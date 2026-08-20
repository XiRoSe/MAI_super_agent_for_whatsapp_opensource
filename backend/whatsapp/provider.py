"""WhatsApp provider interface + inbound message model.

Mai talks to WhatsApp through this small interface so the transport (Meta Cloud
API now, Twilio later) is swappable via `WHATSAPP_PROVIDER`. The interface is
deliberately tiny: send text, send a template, validate a webhook signature, and
parse inbound payloads into a normalized form.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from backend.whatsapp.templates import Template


@dataclass
class InboundMessage:
    from_number: str  # E.164 sender — the user within the owning tenant
    text: str
    message_id: str
    timestamp: str | None = None
    # For per-tenant (unofficial) topology: which gateway session received this,
    # i.e. which tenant's own number. None for the shared-number (Meta) topology.
    session_id: str | None = None
    # Was this sent BY the linked account itself (the owner typing @Mai)?
    from_me: bool = False
    # The conversation this belongs to (individual number or group jid) — where a
    # reply should go. For the owner's own messages this is the recipient/chat.
    chat_id: str | None = None
    # Sender's WhatsApp display name (pushName), when the gateway provides it.
    from_name: str | None = None
    # If this message carried media: "image" | "audio" | "video" | "document".
    media_type: str | None = None
    # A fetchable PUBLIC url for the media bytes (gateway-served, decrypted), so the
    # agent can transcribe/edit it ON DEMAND. None for old/un-downloaded media whose
    # only reference is an encrypted WhatsApp CDN url.
    media_url: str | None = None
    # Privacy Phase 2B: when the forked GOWA seals the body, it stamps the epoch it
    # sealed to and ships the sealed blob. `text` is then the DECRYPTED plaintext
    # (for the turn), `sealed_ciphertext` is the original blob (stored as-is, not
    # re-sealed), and `epoch_id` selects the key. All None on the legacy clear path.
    epoch_id: int | None = None
    sealed_ciphertext: str | None = None
    # If this message REPLIED to / quoted another message: the quoted text + its id, so
    # the agent acts on the message the user actually pointed at (not just the @Mai text).
    quoted_text: str | None = None
    quoted_id: str | None = None


class WhatsAppProvider(ABC):
    #: True for unofficial gateways (no 24h window / templates; per-tenant sessions).
    unofficial: bool = False

    @abstractmethod
    def verify_signature(self, raw_body: bytes, signature_header: str | None) -> bool:
        """Validate the inbound webhook signature. Reject if it doesn't match."""

    @abstractmethod
    def parse_inbound(self, payload: dict) -> list[InboundMessage]:
        """Extract normalized inbound messages from a webhook payload."""

    @abstractmethod
    def send_text(self, to_number: str, text: str, *, session_id: str | None = None) -> None:
        """Send a free-form text message. `session_id` selects the sending number
        (the tenant's own session) for per-tenant gateways; ignored otherwise."""

    @abstractmethod
    def send_template(self, to_number: str, template: Template, *, session_id: str | None = None) -> None:
        """Send an approved template (Meta out-of-window). Unofficial gateways
        have no templates and fall back to plain text."""
