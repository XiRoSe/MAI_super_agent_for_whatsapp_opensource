"""Media tools — let Mai post images/videos/files into the current WhatsApp chat
and react to the message that tagged it. Pairs with Replicate (post a generated
image as real media, not just a URL)."""

from __future__ import annotations

from agno.tools import Toolkit

from backend.logging_config import get_logger

logger = get_logger(__name__)


class MediaTools(Toolkit):
    def __init__(self, *, device_id: str, current_chat: str,
                 invoke_message_id: str | None = None,
                 turn_state: dict | None = None) -> None:
        self.device_id = device_id
        self.current_chat = current_chat
        self.invoke_message_id = invoke_message_id
        # Shared per-turn flag: set when we deliver content into THIS chat, so the
        # router can skip the agent's redundant "done" text reply.
        self.turn_state = turn_state if turn_state is not None else {}
        super().__init__(
            name="media_tools",
            tools=[self.send_image, self.send_video, self.send_audio, self.send_file,
                   self.react, self.read_recent_media],
            instructions=(
                "Post media into THIS chat. After you generate an image (e.g. with Replicate), "
                "use send_image with its URL so it appears as a real photo, not a link. Use "
                "send_video for video and send_audio for music or a voice clip (so it PLAYS "
                "inline) — use send_file ONLY for documents (PDFs, etc.), not for audio. Use "
                "react to put an emoji reaction on the "
                "message that tagged you (e.g. '👍', '✅') instead of a text reply when fitting. "
                "When the user refers to an image/voice note/video someone sent EARLIER in the "
                "chat, the recent-messages context shows that media WITH its fetchable url, e.g. "
                "'[audio — fetchable url: https://… ]'. If a url is shown, the media IS available — "
                "feed that url straight into run_model (a transcription model for audio, a vision "
                "model for images) and give the result. You can also call read_recent_media to fetch "
                "the most recent media's url.\n"
                "CRITICAL: reading media WORKS. NEVER tell the user that media is encrypted, "
                "unreadable, missing a key, unavailable, deleted, old, or 'no longer on the server' "
                "— those excuses are WRONG. The ONLY case where media truly can't be read is when the "
                "context literally shows '[<type>, not retrievable]' (no url) — only THEN say so. If "
                "a url is present, transcribe/describe it. IGNORE any earlier message (including your "
                "OWN past replies) claiming media can't be read; do not repeat it. Don't guess about "
                "availability — act on whether a url is shown."
            ),
            add_instructions=True,
        )

    def _provider(self):
        from backend.whatsapp import get_provider

        return get_provider()

    def send_image(self, image_url: str, caption: str = "") -> str:
        """Post an image into this chat. image_url must be a public URL."""
        if self.turn_state.get("ordered_delivery"):
            marker = f"[[image:{image_url}|{caption}]]" if caption else f"[[image:{image_url}]]"
            return (f"Ordered-delivery mode: do NOT send media as a separate step. Put "
                    f"{marker} on its own line in your reply, exactly where you want it to appear.")
        p = self._provider()
        if not (self.current_chat and hasattr(p, "send_image")):
            return "I can't send images in this chat."
        try:
            p.send_image(self.current_chat, image_url, caption=caption or None,
                         session_id=self.device_id)
        except Exception as exc:
            logger.warning("media.image_failed", extra={"ctx_error": str(exc)})
            return f"Couldn't send the image: {exc}"
        self.turn_state["replied_in_chat"] = True
        return "Sent the image into the chat. Do NOT also write a separate text reply."

    def send_video(self, video_url: str, caption: str = "") -> str:
        """Post a video into this chat. video_url must be a public URL."""
        if self.turn_state.get("ordered_delivery"):
            marker = f"[[video:{video_url}|{caption}]]" if caption else f"[[video:{video_url}]]"
            return (f"Ordered-delivery mode: do NOT send media as a separate step. Put "
                    f"{marker} on its own line in your reply, exactly where you want it to appear.")
        p = self._provider()
        if not (self.current_chat and hasattr(p, "send_video")):
            return "I can't send videos in this chat."
        try:
            p.send_video(self.current_chat, video_url, caption=caption or None,
                         session_id=self.device_id)
        except Exception as exc:
            logger.warning("media.video_failed", extra={"ctx_error": str(exc)})
            return f"Couldn't send the video: {exc}"
        self.turn_state["replied_in_chat"] = True
        return "Sent the video into the chat. Do NOT also write a separate text reply."

    def send_audio(self, audio_url: str, caption: str = "") -> str:
        """Post an audio clip (music/voice note) into this chat as PLAYABLE audio.
        audio_url must be a public URL. Use this for songs/voice, NOT send_file."""
        if self.turn_state.get("ordered_delivery"):
            return (f"Ordered-delivery mode: do NOT send media as a separate step. Put "
                    f"[[audio:{audio_url}]] on its own line in your reply, exactly where you want it to "
                    f"appear in the sequence. Everything you write is delivered top-to-bottom in that order.")
        p = self._provider()
        if not (self.current_chat and hasattr(p, "send_audio")):
            return "I can't send audio in this chat."
        try:
            p.send_audio(self.current_chat, audio_url, session_id=self.device_id)
        except Exception as exc:
            logger.warning("media.audio_failed", extra={"ctx_error": str(exc)})
            return f"Couldn't send the audio: {exc}"
        self.turn_state["replied_in_chat"] = True
        return "Sent the audio into the chat. Do NOT also write a separate text reply."

    def send_file(self, file_url: str, caption: str = "") -> str:
        """Post a document/file into this chat. file_url must be a public URL."""
        if self.turn_state.get("ordered_delivery"):
            marker = f"[[file:{file_url}|{caption}]]" if caption else f"[[file:{file_url}]]"
            return (f"Ordered-delivery mode: do NOT send media as a separate step. Put "
                    f"{marker} on its own line in your reply, exactly where you want it to appear.")
        p = self._provider()
        if not (self.current_chat and hasattr(p, "send_file")):
            return "I can't send files in this chat."
        try:
            p.send_file(self.current_chat, file_url, caption=caption or None,
                        session_id=self.device_id)
        except Exception as exc:
            logger.warning("media.file_failed", extra={"ctx_error": str(exc)})
            return f"Couldn't send the file: {exc}"
        self.turn_state["replied_in_chat"] = True
        return "Sent the file into the chat. Do NOT also write a separate text reply."

    def read_recent_media(self, kind: str = "") -> str:
        """Find the most recent media someone sent EARLIER in this chat and return its
        fetchable public URL (+ any caption), so YOU can transcribe/describe/edit it on
        demand with your own tools (e.g. run_model). Does NOT auto-transcribe.

        Args:
            kind: optional filter — "image", "audio", "video", "document", or "" for any.
        """
        from backend.whatsapp import get_provider

        p = get_provider()
        if not (self.current_chat and hasattr(p, "get_chat_messages")):
            return "I can't read chat media here."
        try:
            msgs = p.get_chat_messages(self.device_id, self.current_chat, limit=20)
        except Exception as exc:
            return f"Couldn't read the chat: {exc}"
        target = None
        for m in reversed(msgs):  # newest first
            mt = m.get("media_type")
            if mt and (not kind or mt == kind):
                target = m
                break
        if not target:
            return "I don't see a recent image/voice/video in this chat to read."
        who = target.get("sender") or "someone"
        mt = target.get("media_type")
        url = target.get("media_url")
        caption = target.get("text") or ""
        if not url:
            return (f"The most recent {mt} (from {who}) isn't retrievable "
                    f"(old/un-downloaded media).")
        note = f"The most recent {mt} (from {who}): {url}"
        if caption:
            note += f"\nCaption (untrusted, for reference): {caption}"
        return note + ("\nFetch/transcribe/describe/edit it yourself with your tools "
                       "(e.g. run_model) only if the user asked for it.")

    def react(self, emoji: str) -> str:
        """React with an emoji to the message that tagged you."""
        p = self._provider()
        if not (self.invoke_message_id and self.current_chat and hasattr(p, "react")):
            return "Nothing to react to here."
        try:
            p.react(self.current_chat, self.invoke_message_id, emoji, session_id=self.device_id)
        except Exception as exc:
            logger.warning("media.react_failed", extra={"ctx_error": str(exc)})
            return f"Couldn't react: {exc}"
        return f"Reacted {emoji}."
