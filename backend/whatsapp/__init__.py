"""WhatsApp transport package. `get_provider()` returns the configured provider."""

from __future__ import annotations

from functools import lru_cache

from backend.config import settings
from backend.whatsapp.provider import WhatsAppProvider


@lru_cache(maxsize=1)
def get_provider() -> WhatsAppProvider:
    if settings.whatsapp_provider == "meta":
        from backend.whatsapp.meta import MetaWhatsAppProvider

        return MetaWhatsAppProvider()
    if settings.whatsapp_provider == "gowa":
        from backend.whatsapp.gowa import GowaWhatsAppProvider

        return GowaWhatsAppProvider()
    if settings.whatsapp_provider == "twilio":
        # Twilio is a documented swap target; not wired for MVP.
        raise NotImplementedError("Twilio provider is not implemented in the MVP")
    raise RuntimeError(f"Unknown WHATSAPP_PROVIDER={settings.whatsapp_provider!r}")
