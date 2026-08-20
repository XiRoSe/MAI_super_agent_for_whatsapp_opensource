"""Meta WhatsApp Cloud API provider.

Implements signature validation (X-Hub-Signature-256), inbound parsing for the
Cloud API webhook shape, and outbound text/template sends via the Graph API.
"""

from __future__ import annotations

import hashlib
import hmac

import httpx

from backend.config import settings
from backend.logging_config import get_logger
from backend.whatsapp.provider import InboundMessage, WhatsAppProvider
from backend.whatsapp.templates import Template

logger = get_logger(__name__)


class MetaWhatsAppProvider(WhatsAppProvider):
    def __init__(self) -> None:
        self.app_secret = settings.whatsapp_app_secret
        self.access_token = settings.whatsapp_access_token
        self.phone_number_id = settings.whatsapp_phone_number_id
        self.api_version = settings.whatsapp_api_version

    @property
    def _base_url(self) -> str:
        return f"https://graph.facebook.com/{self.api_version}"

    # -- inbound ------------------------------------------------------------
    def verify_signature(self, raw_body: bytes, signature_header: str | None) -> bool:
        if not self.app_secret:
            # No secret configured (dev). Refuse in production; allow in dev.
            return not settings.is_production
        if not signature_header or not signature_header.startswith("sha256="):
            return False
        expected = hmac.new(self.app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        provided = signature_header.split("sha256=", 1)[1]
        return hmac.compare_digest(expected, provided)

    def parse_inbound(self, payload: dict) -> list[InboundMessage]:
        out: list[InboundMessage] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    if msg.get("type") != "text":
                        # MVP handles text only; ignore media/voice gracefully.
                        continue
                    out.append(
                        InboundMessage(
                            from_number=_normalize(msg.get("from", "")),
                            text=msg.get("text", {}).get("body", ""),
                            message_id=msg.get("id", ""),
                            timestamp=msg.get("timestamp"),
                        )
                    )
        return out

    # -- outbound -----------------------------------------------------------
    def _post(self, body: dict) -> None:
        if not self.access_token or not self.phone_number_id:
            logger.info("whatsapp.send_skipped_no_creds", extra={"ctx_to": body.get("to")})
            return
        url = f"{self._base_url}/{self.phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=15)
            if resp.status_code >= 400:
                logger.warning(
                    "whatsapp.send_failed",
                    extra={"ctx_status": resp.status_code, "ctx_body": resp.text[:200]},
                )
        except httpx.HTTPError as exc:  # graceful: never crash a turn on a send
            logger.warning("whatsapp.send_error", extra={"ctx_error": str(exc)})

    def send_text(self, to_number: str, text: str, *, session_id: str | None = None) -> None:
        # Meta is single-number (shared topology); session_id is ignored.
        self._post(
            {
                "messaging_product": "whatsapp",
                "to": _normalize(to_number),
                "type": "text",
                "text": {"body": text},
            }
        )

    def send_template(self, to_number: str, template: Template, *, session_id: str | None = None) -> None:
        components = []
        if template.variables:
            components = [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": v} for v in template.variables],
                }
            ]
        self._post(
            {
                "messaging_product": "whatsapp",
                "to": _normalize(to_number),
                "type": "template",
                "template": {
                    "name": template.name,
                    "language": {"code": template.language},
                    "components": components,
                },
            }
        )


def _normalize(number: str) -> str:
    """Normalize to a bare E.164-ish key: strip spaces; keep a single leading +."""
    n = (number or "").strip().replace(" ", "").replace("-", "")
    if n and not n.startswith("+"):
        n = "+" + n
    return n
