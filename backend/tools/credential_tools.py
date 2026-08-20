"""Credential tools — how Mai remembers and uses per-service credentials.

Integrations are SKILLS (SKILL.md), not native code. A skill describes how to use
a service (Google Calendar, Outlook, Notion, …); these tools let Mai store the
user's credentials for that service and read them back when a skill needs them.
Everything is tenant-scoped to the calling user.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from agno.tools import Toolkit

from backend.credentials import (
    delete_credential,
    get_credential,
    list_services,
    save_credential,
)
from backend.db.scoped import TenantScope
from backend.db.tenancy import User


class CredentialTools(Toolkit):
    def __init__(self, *, user: User, session: Session):
        self.user = user
        self.scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
        super().__init__(
            name="credentials",
            tools=[
                self.list_connected_services,
                self.save_service_credentials,
                self.get_service_credentials,
                self.forget_service,
            ],
            instructions=(
                "Integrations work via skills + stored credentials. Use "
                "list_connected_services to see what the user has connected. When a "
                "skill needs a service's credentials, call get_service_credentials. If "
                "the user pastes credentials (a key or JSON), save them with "
                "save_service_credentials. Never reveal raw credentials back to the user."
            ),
            add_instructions=True,
        )

    def list_connected_services(self) -> str:
        """List the services the user has saved credentials for."""
        items = list_services(self.scope)
        if not items:
            return "No services connected yet. The user can paste credentials for any service."
        return "Connected services: " + ", ".join(
            f"{i['service']}" + (f" ({i['label']})" if i["label"] else "") for i in items
        )

    def save_service_credentials(self, service: str, credentials: str, label: str = "") -> str:
        """Save credentials for a service.

        Args:
            service: a short service key, e.g. "google", "outlook", "notion".
            credentials: the credential value — a raw key/token, or a JSON object as
                a string (e.g. '{"api_key": "...", "calendar_id": "..."}').
            label: optional human label (e.g. the account email).
        """
        try:
            data = json.loads(credentials)
            if not isinstance(data, dict):
                data = {"value": data}
        except json.JSONDecodeError:
            data = {"value": credentials}  # plain key/token
        save_credential(self.scope, service=service, data=data, label=label or None)
        return f"Saved your {service} credentials. I'll use them when a {service} task comes up."

    def get_service_credentials(self, service: str) -> str:
        """Return the stored credentials JSON for a service (for use inside a skill)."""
        data = get_credential(self.scope, service)
        if data is None:
            return f"No credentials saved for '{service}'. Ask the user to connect it first."
        return json.dumps(data)

    def forget_service(self, service: str) -> str:
        """Delete stored credentials for a service."""
        ok = delete_credential(self.scope, service)
        return f"Removed {service} credentials." if ok else f"Nothing saved for '{service}'."
