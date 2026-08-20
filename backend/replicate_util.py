"""Shared Replicate helpers."""

from __future__ import annotations


def pin_version(client, ref: str) -> str:
    """Pin a model ref to its latest version id.

    `replicate.run("owner/model")` (no version) 404s for some models; resolving the
    latest version first avoids that. Already-versioned refs pass through.
    """
    if not ref or ":" in ref:
        return ref
    try:
        owner, name = ref.split("/", 1)
        return f"{ref}:{client.models.get(owner, name).latest_version.id}"
    except Exception:
        return ref
