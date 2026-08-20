"""Recipient resolution contract (backend/whatsapp/resolution.py). These lock the exact
behaviors the "similar number" bug violated: never silently pick when the query is
ambiguous, resolve number->name (and 972-vs-0), and always surface a name+number to echo.
"""

from __future__ import annotations

from backend.whatsapp.resolution import (
    ChatRef,
    MatchKind,
    jid_to_number,
    resolve_chat,
)

# A small directory shaped like what the provider returns.
DANA = {"name": "Dana", "jid": "972501234567@s.whatsapp.net", "is_group": False}
DAN = {"name": "Dan", "jid": "972501234568@s.whatsapp.net", "is_group": False}
DANA2 = {"name": "Dana", "jid": "972520000000@s.whatsapp.net", "is_group": False}  # a 2nd Dana
FAMILY = {"name": "Family", "jid": "120363000000000000@g.us", "is_group": True}
DIR = [DANA, DAN, DANA2, FAMILY]


def test_jid_to_number():
    assert jid_to_number("972501234567@s.whatsapp.net") == "972501234567"
    assert jid_to_number("120363000000000000@g.us") == "120363000000000000"  # local part is numeric
    assert jid_to_number(None) is None


def test_number_lookup_resolves_name_both_prefixes():
    # number -> name, the bidirectional case that used to be impossible.
    r = resolve_chat("0501234567", [DANA, DAN, FAMILY])
    assert r.kind is MatchKind.NUMBER
    assert r.name == "Dana"
    assert r.resolved
    # international spelling of the SAME number resolves the same contact.
    assert resolve_chat("+972501234567", [DANA, DAN, FAMILY]).name == "Dana"


def test_number_one_digit_off_does_not_resolve():
    # Dan is 568, Dana is 567 — asking for 569 must match NEITHER (no near-miss send).
    r = resolve_chat("0501234569", [DANA, DAN])
    assert r.kind is MatchKind.NONE
    assert not r.resolved


def test_exact_name_unique():
    r = resolve_chat("Dan", [DANA, DAN])  # "Dan" is a substring of "Dana" but an EXACT match to Dan
    assert r.kind is MatchKind.EXACT_NAME
    assert r.name == "Dan"
    assert r.jid == DAN["jid"]


def test_two_contacts_same_name_is_ambiguous_never_silent():
    # The core anti-bug: two distinct Danas -> AMBIGUOUS, not exact[0].
    r = resolve_chat("Dana", DIR)
    assert r.kind is MatchKind.AMBIGUOUS
    assert not r.resolved
    assert {c.jid for c in r.candidates} == {DANA["jid"], DANA2["jid"]}


def test_unique_substring_resolves():
    r = resolve_chat("Fam", DIR)
    assert r.kind is MatchKind.UNIQUE_SUBSTRING
    assert r.name == "Family"
    assert r.is_group


def test_substring_matching_two_is_ambiguous():
    r = resolve_chat("Da", [DANA, DAN])  # matches Dana AND Dan
    assert r.kind is MatchKind.AMBIGUOUS
    assert {c.name for c in r.candidates} == {"Dana", "Dan"}


def test_jid_passthrough_still_carries_a_label():
    # A raw jid is unambiguous, but must STILL yield an identity to echo (name if known).
    r = resolve_chat("972501234567@s.whatsapp.net", DIR)
    assert r.kind is MatchKind.JID
    assert r.resolved
    assert r.name == "Dana"  # enriched from the directory
    assert r.echo_label == "Dana (972501234567)"


def test_jid_passthrough_unknown_number_still_echoes_number():
    r = resolve_chat("972509999999@s.whatsapp.net", [DANA])  # not in directory
    assert r.kind is MatchKind.JID
    assert r.echo_label == "unknown contact (972509999999)"


def test_echo_label_group_vs_dm():
    assert ChatRef.from_dict(DANA).label == "Dana (972501234567)"
    assert ChatRef.from_dict(FAMILY).label == "Family (group)"


def test_empty_query_is_none():
    assert resolve_chat("", DIR).kind is MatchKind.NONE
    assert resolve_chat("   ", DIR).kind is MatchKind.NONE


def test_no_match_is_none():
    assert resolve_chat("Zebra", DIR).kind is MatchKind.NONE
