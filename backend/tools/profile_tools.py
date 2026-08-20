"""Profile tools — look up a WhatsApp user's avatar + details by phone number.

Owner-only (privacy). Lets Mai fetch a profile picture URL (to transform with an
image-to-image model, or send) and basic public details (name, about/status).
"""

from __future__ import annotations

import json

from agno.tools import Toolkit

from backend.logging_config import get_logger

logger = get_logger(__name__)

# Self-references the model may pass through verbatim; the non-English entries are
# intentional (users write in any language) — input matchers, not UI strings.
_SELF = {"", "me", "my", "mine", "myself", "i", "self", "שלי", "אני"}
_HERE = {"this chat", "here", "this contact", "this person", "them"}


class ProfileTools(Toolkit):
    def __init__(self, *, device_id: str, owner_phone: str | None,
                 current_chat: str | None = None) -> None:
        self.device_id = device_id
        self.owner_phone = owner_phone
        self.current_chat = current_chat
        super().__init__(
            name="profile_tools",
            tools=[self.get_profile_picture, self.get_user_details, self.list_participants],
            instructions=(
                "Look up a WhatsApp user by phone number. get_profile_picture returns a "
                "PHOTO URL — to restyle someone's profile pic (e.g. 'make my pic anime'), "
                "fetch it, then run an image-to-image model on Replicate (e.g. "
                "'fofr/face-to-many' with the image URL + a style) and post the result. "
                "Default target is the owner ('me'). In a 1:1 chat, the contact's name or "
                "'this chat' resolves to the person you're talking to (their number is in "
                "the context). In a group, use a participant's number from the roster. "
                "Only the owner can use these."
            ),
            add_instructions=True,
        )

    def _provider(self):
        from backend.whatsapp import get_provider

        return get_provider()

    def _resolve(self, phone: str) -> str | None:
        p = (phone or "").strip().lower()
        if p in _SELF:
            return self.owner_phone
        if p in _HERE and self.current_chat:
            return self.current_chat
        # A bare NAME (no digits) in a 1:1 chat means the person you're talking to —
        # their number is the current chat. (Groups: use the participant roster.)
        has_digit = any(c.isdigit() for c in p)
        if p and not has_digit and self.current_chat and "@g.us" not in self.current_chat:
            return self.current_chat
        return phone or self.owner_phone

    def get_profile_picture(self, phone: str = "") -> str:
        """Get the profile-picture URL of a WhatsApp user.

        Args:
            phone: the user's number, or "me" for the owner (default). Returns a URL
                   you can transform with an image-to-image model, or send as media.
        """
        p = self._provider()
        if not hasattr(p, "get_avatar"):
            return "I can't fetch profile pictures here."
        target = self._resolve(phone)
        if not target:
            return "I don't know whose picture to fetch — give me a phone number."
        try:
            url = p.get_avatar(target, session_id=self.device_id)
        except Exception as exc:
            logger.warning("profile.avatar_failed", extra={"ctx_error": str(exc)})
            return f"Couldn't fetch that profile picture: {exc}"
        return url or "No profile picture available (it may be private or unset)."

    def list_participants(self) -> str:
        """List the members (names + numbers) of the CURRENT group chat."""
        if not (self.current_chat and "@g.us" in self.current_chat):
            return "This isn't a group chat."
        p = self._provider()
        if not hasattr(p, "get_group_participants"):
            return "I can't list participants here."
        try:
            members = p.get_group_participants(self.current_chat, session_id=self.device_id)
        except Exception as exc:
            return f"Couldn't list participants: {exc}"
        if not members:
            return "No participants found."
        return "Group members:\n" + "\n".join(
            f"- {m['name']} ({m['number']})" + (" [admin]" if m.get("admin") else "")
            for m in members[:100] if m.get("number") or m.get("name")
        )

    def get_user_details(self, phone: str = "") -> str:
        """Get a WhatsApp user's public details (name, about/status) by phone."""
        p = self._provider()
        if not hasattr(p, "get_user_info"):
            return "I can't fetch user details here."
        target = self._resolve(phone)
        if not target:
            return "Give me a phone number to look up."
        try:
            info = p.get_user_info(target, session_id=self.device_id)
        except Exception as exc:
            logger.warning("profile.info_failed", extra={"ctx_error": str(exc)})
            return f"Couldn't fetch details: {exc}"
        if not info:
            return "No public details found for that number."
        keep = {k: info[k] for k in ("verified_name", "name", "status", "about", "devices")
                if k in info}
        return json.dumps(keep or info)
