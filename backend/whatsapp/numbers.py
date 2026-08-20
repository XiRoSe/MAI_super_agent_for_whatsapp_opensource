"""Phone-number matching primitives — the ONE contract every contact/recipient lookup shares.

Why this module exists: Mai compares phone numbers from DIFFERENT sources — a WhatsApp
gateway JID (`972501234567@s.whatsapp.net`, always international), a number the user typed
(`0501234567`, national trunk-0 form), and numbers embedded in the owner's saved memories.
The same person shows up in all three forms, so equality can't be string equality.

The load-bearing primitive is the **NSN (national significant number) = the last 9 digits**.
Israeli mobile `+972 50 123 4567` and its national form `050 123 4567` both collapse to
`501234567`, so `same_number` treats them as the same contact. This is deliberate and must
NOT be "tightened" to a longer suffix — doing that silently breaks the 972-vs-0 case, which
is the common one (the router's `_contact_name_from_memory` was built around it).

The bug this module is written to kill (`router.py::_contact_name_from_memory`): matching a
9-digit NSN as a SUBSTRING of a memory's *concatenated digit blob* lets one contact's NSN
straddle two adjacent numbers (a tracking id + a real number) and mislabel the contact — which
is how a message once went to an unrelated person. The fix is `extract_numbers` + `same_number`:
compare against DISCRETE numbers, never a blob.
"""

from __future__ import annotations

import re

# A run that looks like a phone number: an optional '+', then >= 8 digits, with spaces/dashes
# allowed between them. Long enough to exclude order ids / short codes ("#12345"); we normalize
# each hit to digits-only afterward. Discreteness is the whole point — see the module docstring.
_NUMBER_RE = re.compile(r"\+?\d[\d\s\-]{6,}\d")


def normalize_number(s: str | None) -> str:
    """Reduce any phone-number-ish string to bare digits (drops '+', spaces, dashes, suffixes).
    `'+972 50-123 4567'` and `'972501234567@s.whatsapp.net'` both -> `'972501234567'`."""
    return "".join(c for c in str(s or "") if c.isdigit())


def nsn(s: str | None) -> str:
    """The national significant number: the last 9 digits of the normalized form. Collapses the
    international (`972…`) and national trunk-0 (`0…`) spellings of the SAME number to one key,
    so cross-source comparison works. Shorter numbers return their full digit string."""
    return normalize_number(s)[-9:]


def same_number(a: str | None, b: str | None) -> bool:
    """True iff two numbers denote the same contact by NSN (last-9) equality — NOT full-string
    and NOT a variable-length suffix. `same_number('+972501234567', '0501234567')` is True;
    a one-digit-off number is False. Empty/degenerate inputs are never equal."""
    na = nsn(a)
    return bool(na) and na == nsn(b)


def extract_numbers(text: str | None) -> list[str]:
    """Pull the DISCRETE phone numbers out of free text (e.g. a saved memory), each normalized to
    bare digits. Discrete is the point: matching against these one-by-one (via `same_number`) can't
    let an NSN straddle two adjacent numbers the way a concatenated-digit-blob substring test can."""
    return [n for n in (normalize_number(m) for m in _NUMBER_RE.findall(str(text or ""))) if n]
