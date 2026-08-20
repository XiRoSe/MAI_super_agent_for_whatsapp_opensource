"""The phone-number matching contract (backend/whatsapp/numbers.py) — the primitive every
contact/recipient lookup shares. These lock the two easy-to-get-wrong cases: the Israeli
972-vs-0 equivalence MUST hold, and a contact's NSN must NOT match by straddling two adjacent
numbers in a memory (the concatenated-blob false-positive that sent a message to the wrong person).
"""

from __future__ import annotations

from backend.whatsapp.numbers import extract_numbers, normalize_number, nsn, same_number


def test_normalize_strips_everything_but_digits():
    assert normalize_number("+972 50-123 4567") == "972501234567"
    assert normalize_number("972501234567@s.whatsapp.net") == "972501234567"
    assert normalize_number(None) == ""


def test_nsn_collapses_international_and_national():
    # The load-bearing case: same person, two spellings -> one key.
    assert nsn("+972501234567") == "501234567"
    assert nsn("0501234567") == "501234567"


def test_same_number_matches_972_vs_0_but_not_near_miss():
    # 972-vs-0 is the COMMON case and must match (regression guard: do not "tighten" to a longer suffix).
    assert same_number("+972501234567", "0501234567")
    assert same_number("972501234567@s.whatsapp.net", "0501234567")
    # One digit off is a DIFFERENT person — never match.
    assert not same_number("+972501234567", "+972501234568")
    # Degenerate inputs are never equal.
    assert not same_number("", "0501234567")
    assert not same_number("0501234567", None)


def test_extract_numbers_is_discrete():
    text = "Dana is my designer; her WhatsApp is 050-123-4567. Order #12345, tracking 972509999888777."
    nums = extract_numbers(text)
    assert "0501234567" in nums
    assert "972509999888777" in nums
    assert "12345" not in nums  # too short to be a phone number


def test_discrete_matching_kills_the_blob_false_positive():
    """The wrong-recipient bug: matching Dana's NSN (501234567) as a SUBSTRING of a memory's
    concatenated digit blob mislabels her, because a longer number contains her NSN by accident.
    Discrete extraction + same_number must match ONLY the real, discrete number."""
    dana = "+972501234567"
    # A tracking number whose digits happen to CONTAIN Dana's NSN as a substring.
    memory = "Package tracking: 99972501234567000. Separately, Dana's number is 050-123-4567."

    # The OLD approach: is Dana's NSN a substring of the whole digit blob? -> a false hit.
    blob = normalize_number("".join(ch for ch in memory))  # all digits, concatenated
    assert nsn(dana) in blob  # the false-positive the old code suffered

    # The NEW approach: match against DISCRETE numbers only -> exactly one true match (Dana).
    hits = [n for n in extract_numbers(memory) if same_number(n, dana)]
    assert len(hits) == 1
    assert same_number(hits[0], "0501234567")
