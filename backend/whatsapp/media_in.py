"""Read inbound WhatsApp media via Replicate: describe/answer images, transcribe audio.

Best-effort — if there's no Replicate token, the model is unavailable, or the
download fails, callers degrade gracefully (Mai says it couldn't read the media).
"""

from __future__ import annotations

import io

from backend.config import settings
from backend.logging_config import get_logger

logger = get_logger(__name__)


def _join(out) -> str:
    if isinstance(out, dict):
        return str(out.get("transcription") or out.get("text") or out.get("output") or out)
    if isinstance(out, (list, tuple)):
        return "".join(str(x) for x in out)
    return str(out)


def describe_media(media_type: str, data: bytes, *, filename: str = "media",
                   question: str = "") -> str | None:
    """Turn media bytes into text: image description/answer, or audio transcript."""
    if not settings.replicate_api_token or not data:
        return None
    try:
        import replicate

        from backend.replicate_util import pin_version

        client = replicate.Client(api_token=settings.replicate_api_token)
        f = io.BytesIO(data)
        f.name = filename or "media"
        if media_type == "image":
            prompt = question.strip() or "Describe this image in detail."
            out = client.run(pin_version(client, settings.replicate_vision_model),
                             input={"image": f, "question": prompt})
            return _join(out).strip() or None
        if media_type == "audio":
            out = client.run(pin_version(client, settings.replicate_transcribe_model),
                             input={"audio": f})
            return _join(out).strip() or None
        if media_type == "video":
            if not settings.replicate_video_model:
                return None  # no video model configured
            prompt = question.strip() or "Describe what happens in this video."
            out = client.run(pin_version(client, settings.replicate_video_model),
                             input={"video": f, "prompt": prompt})
            return _join(out).strip() or None
    except Exception as exc:
        logger.warning("media.describe_failed",
                       extra={"ctx_error": str(exc), "ctx_type": media_type})
        return None
    return None  # video/document not read yet
