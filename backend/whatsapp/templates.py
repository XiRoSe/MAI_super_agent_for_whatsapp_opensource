"""Approved WhatsApp utility templates for out-of-window proactive sends.

Outside the 24h service window, Meta only allows pre-approved templates, billed
per send. Mai ships a small set of utility templates; AI-generated proactive
content is rendered into a body variable (Meta limits variable length, so we
truncate). Free-form text is only used INSIDE the window.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Meta caps body variable length; keep well under to be safe.
MAX_VAR_LEN = 900


@dataclass
class Template:
    name: str
    language: str
    variables: list[str] = field(default_factory=list)


# The single utility template Mai uses for proactive briefs/reminders.
# (Register this exact name + format in Meta Business Manager before going live.)
DAILY_BRIEF_TEMPLATE = "mai_proactive_update"


def render_proactive_template(text: str, *, language: str = "en") -> Template:
    body = text.strip()
    if len(body) > MAX_VAR_LEN:
        body = body[: MAX_VAR_LEN - 1].rstrip() + "…"
    return Template(name=DAILY_BRIEF_TEMPLATE, language=language, variables=[body])
