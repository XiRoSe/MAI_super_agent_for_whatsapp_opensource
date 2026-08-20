"""Recipient resolution — turn a user-supplied name / number / jid into a concrete chat,
plus the identity Mai MUST echo before sending to it.

This is deliberately pure and I/O-free: it takes the candidate chats (dicts the provider
already returned) + the raw query the user gave, and returns a typed `ChatResolution`.
The toolkit owns the side effects (fetching chats, sending); this owns the *decision* of
who the query points at — so the decision is unit-testable without a provider.

Why it exists (the "similar number" bug): `WhatsAppTools._resolve` used to (a) pass a raw
jid through verbatim, (b) return `exact[0]` even with several exact-name matches, and (c)
pick a lone substring match — all silently, with no identity surfaced to confirm. The spec
is: resolve to a candidate, but hand back the name + FULL number so the caller can echo it
and wait for a yes, and NEVER auto-resolve when the query is ambiguous. Number matching goes
through `backend.whatsapp.numbers` (NSN / discrete), so `05x` and `+9725x` are one contact
and no match is made by digit-blob straddling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from backend.whatsapp.numbers import normalize_number, same_number


class MatchKind(Enum):
    """How the query resolved — the caller uses this to decide whether it may send."""

    JID = "jid"                       # query was a raw jid/id — used verbatim
    EXACT_NAME = "exact_name"         # unique exact (case-insensitive) name match
    NUMBER = "number"                 # matched a DM by phone number (NSN equality)
    UNIQUE_SUBSTRING = "unique_substring"  # exactly one name contains the query
    AMBIGUOUS = "ambiguous"           # >1 candidate — caller must ask, never auto-send
    NONE = "none"                     # nothing matched


def jid_to_number(jid: str | None) -> str | None:
    """The phone number embedded in a DM jid (`972…@s.whatsapp.net` -> `972…`), or None for
    a group jid (`…@g.us`) / anything without a numeric local part."""
    if not jid:
        return None
    local = str(jid).split("@", 1)[0]
    digits = normalize_number(local)
    return digits or None


@dataclass(frozen=True)
class ChatRef:
    """A single candidate chat, normalized from a provider dict."""

    name: str | None
    jid: str
    is_group: bool = False
    number: str | None = None  # full digits for a DM; None for a group

    @classmethod
    def from_dict(cls, c: dict) -> "ChatRef":
        jid = str(c.get("jid") or "")
        is_group = bool(c.get("is_group")) or "@g.us" in jid
        number = None if is_group else (normalize_number(c.get("number")) or jid_to_number(jid))
        return cls(name=c.get("name"), jid=jid, is_group=is_group, number=number)

    @property
    def label(self) -> str:
        """`name (+number)` for a DM, `name (group)` for a group — what Mai echoes."""
        if self.is_group:
            return f"{self.name or self.jid} (group)"
        who = self.name or "unknown contact"
        return f"{who} ({self.number})" if self.number else who


@dataclass(frozen=True)
class ChatResolution:
    """The outcome of resolving one query — typed so the send path can't skip the echo.

    `resolved` is True only when exactly one chat was pinned down. `echo_label` is the
    name + full number the caller must show the user before sending. On AMBIGUOUS,
    `candidates` carries the competing chats so Mai can ask which one.
    """

    kind: MatchKind
    chat: ChatRef | None = None
    candidates: tuple[ChatRef, ...] = field(default_factory=tuple)

    @property
    def resolved(self) -> bool:
        return self.chat is not None and self.kind not in (MatchKind.AMBIGUOUS, MatchKind.NONE)

    @property
    def jid(self) -> str | None:
        return self.chat.jid if self.chat else None

    @property
    def name(self) -> str | None:
        return self.chat.name if self.chat else None

    @property
    def is_group(self) -> bool:
        return bool(self.chat and self.chat.is_group)

    @property
    def echo_label(self) -> str | None:
        """The identity to confirm before sending — `name (+number)` / `name (group)`."""
        return self.chat.label if self.chat else None


def _looks_like_number(query: str) -> bool:
    """A query is a number lookup if it's mostly digits (a typed phone number), not a name."""
    digits = normalize_number(query)
    return len(digits) >= 7 and len(digits) >= len(query.strip()) - 3


def resolve_chat(query: str, chats: list[dict]) -> ChatResolution:
    """Resolve `query` (a name, a phone number, or a raw jid) against `chats`.

    Precedence: raw jid > phone-number match > exact name > unique substring. Any tier that
    yields more than one distinct chat is AMBIGUOUS (never silently pick the first) — the one
    exception is a jid, which is unambiguous by construction.
    """
    q = (query or "").strip()
    if not q:
        return ChatResolution(MatchKind.NONE)

    if "@" in q:  # already a jid/id — verbatim, but still surface a label to echo
        ref = ChatRef.from_dict({"jid": q})
        # Prefer a known chat's real name/number if we happen to have it.
        for c in chats:
            if str(c.get("jid") or "") == q:
                ref = ChatRef.from_dict(c)
                break
        return ChatResolution(MatchKind.JID, chat=ref)

    refs = [ChatRef.from_dict(c) for c in chats if (c.get("name") or c.get("jid"))]

    # 1) Phone-number lookup (number -> name, the bidirectional case).
    if _looks_like_number(q):
        hits = _dedupe([r for r in refs if r.number and same_number(r.number, q)])
        if len(hits) == 1:
            return ChatResolution(MatchKind.NUMBER, chat=hits[0])
        if len(hits) > 1:
            return ChatResolution(MatchKind.AMBIGUOUS, candidates=tuple(hits))
        return ChatResolution(MatchKind.NONE)

    ql = q.lower()

    # 2) Exact name (case-insensitive). Multiple DISTINCT chats sharing a name is ambiguous.
    exact = _dedupe([r for r in refs if (r.name or "").lower() == ql])
    if len(exact) == 1:
        return ChatResolution(MatchKind.EXACT_NAME, chat=exact[0])
    if len(exact) > 1:
        return ChatResolution(MatchKind.AMBIGUOUS, candidates=tuple(exact))

    # 3) Unique substring — exactly one name contains the query.
    contains = _dedupe([r for r in refs if ql and ql in (r.name or "").lower()])
    if len(contains) == 1:
        return ChatResolution(MatchKind.UNIQUE_SUBSTRING, chat=contains[0])
    if len(contains) > 1:
        return ChatResolution(MatchKind.AMBIGUOUS, candidates=tuple(contains))

    return ChatResolution(MatchKind.NONE)


def _dedupe(refs: list[ChatRef]) -> list[ChatRef]:
    """Collapse candidates that are the SAME chat (same jid) — so one contact listed twice
    doesn't read as an ambiguous pair."""
    seen: dict[str, ChatRef] = {}
    for r in refs:
        seen.setdefault(r.jid, r)
    return list(seen.values())
