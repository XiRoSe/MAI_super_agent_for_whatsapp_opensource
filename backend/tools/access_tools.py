"""Access tools — the owner opens/closes chats to other people invoking @Mai.

Only the owner gets these (see registry). A granted chat lets anyone in it tag
@Mai; those guests run with a restricted toolset and a privacy guard.
"""

from __future__ import annotations

from agno.tools import Toolkit
from sqlalchemy.orm import Session

from backend.db.tenancy import User
from backend.logging_config import get_logger
from backend.whatsapp.access import grant_chat, list_grants, revoke_chat

logger = get_logger(__name__)


class AccessTools(Toolkit):
    def __init__(self, *, user: User, session: Session, device_id: str | None = None,
                 current_chat: str | None = None) -> None:
        self.user = user
        self.session = session
        self.device_id = device_id
        self.current_chat = current_chat
        super().__init__(
            name="access_tools",
            tools=[self.allow_others_here, self.disallow_others_here,
                   self.allow_others_in_chat, self.list_open_chats],
            instructions=(
                "Manage who else can use Mai. By default only the owner can tag @Mai. "
                "Use allow_others_here when the owner wants people in the CURRENT chat to be "
                "able to tag and use Mai; allow_others_in_chat(name) for a different named "
                "group; disallow_others_here to close it again. Guests get a limited, "
                "privacy-safe Mai (no access to the owner's private data)."
            ),
            add_instructions=True,
        )

    def _label_for(self, chat_id: str) -> str | None:
        if not (self.device_id and chat_id):
            return None
        try:
            from backend.whatsapp import get_provider
            from backend.whatsapp.router import _same_chat

            provider = get_provider()
            if hasattr(provider, "list_chats"):
                for c in provider.list_chats(self.device_id):
                    if _same_chat(c.get("jid", ""), chat_id):
                        return c.get("name")
        except Exception:
            pass
        return None

    def allow_others_here(self) -> str:
        """Allow everyone in the CURRENT chat/group to tag and use Mai."""
        if not self.current_chat:
            return "I can only open the current chat when you're messaging me inside a chat."
        label = self._label_for(self.current_chat)
        opened = grant_chat(self.session, self.user.account_id, self.current_chat, label)
        where = f'"{label}"' if label else "this chat"
        return (f"Done — people in {where} can now tag and use me."
                if opened else f"{where} was already open to others.")

    def disallow_others_here(self) -> str:
        """Stop others in the CURRENT chat from using Mai (owner-only again)."""
        if not self.current_chat:
            return "Tell me from inside the chat you want to close."
        closed = revoke_chat(self.session, self.user.account_id, self.current_chat)
        return ("Done — only you can use me in this chat now."
                if closed else "This chat wasn't open to others.")

    def allow_others_in_chat(self, chat_name: str) -> str:
        """Allow everyone in a named group/chat to use Mai.

        Args:
            chat_name: the group or contact name to open (matched against your chats).
        """
        if not self.device_id:
            return "No WhatsApp device linked, so I can't look up that chat."
        try:
            from backend.whatsapp import get_provider

            provider = get_provider()
            matches = [c for c in provider.list_chats(self.device_id)
                       if chat_name.lower() in (c.get("name") or "").lower()]
        except Exception as exc:
            return f"Couldn't look up your chats: {exc}"
        if not matches:
            return f"I couldn't find a chat called '{chat_name}'."
        if len(matches) > 1:
            names = ", ".join(c.get("name", "?") for c in matches[:5])
            return f"That matches several chats ({names}). Which one?"
        c = matches[0]
        grant_chat(self.session, self.user.account_id, c["jid"], c.get("name"))
        return f'Done — people in "{c.get("name")}" can now tag and use me.'

    def list_open_chats(self) -> str:
        """List the chats currently open to other people using Mai."""
        grants = list_grants(self.session, self.user.account_id)
        if not grants:
            return "No chats are open to others — only you can use me right now."
        lines = [f"- {g.label or g.chat_id}" for g in grants]
        return "These chats are open to others:\n" + "\n".join(lines)
