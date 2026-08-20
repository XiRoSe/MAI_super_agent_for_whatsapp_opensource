"""The @mai trigger is case-insensitive: @mai, @Mai, @MAI, @mAi all invoke MAI.

Pins the brand promise (MAI — Modular AI) at the router gate so a regex change
can never silently break one casing.
"""

from __future__ import annotations

import pytest

from backend.whatsapp.provider import InboundMessage
from backend.whatsapp.router import _strip_mention, is_mai_invocation


def _own_msg(text: str) -> InboundMessage:
    return InboundMessage(
        from_number="972540000001",
        text=text,
        message_id="m1",
        from_me=True,
        chat_id="972540000001",
    )


@pytest.mark.parametrize(
    "text",
    [
        "@mai summarize this chat",
        "@Mai summarize this chat",
        "@MAI summarize this chat",
        "@mAi summarize this chat",
        "hey @MAI what did I miss?",  # mid-sentence
        "@mai",  # bare tag
    ],
)
def test_all_casings_trigger(text: str) -> None:
    assert is_mai_invocation(_own_msg(text))


@pytest.mark.parametrize(
    "text",
    [
        "mai without the at-sign",
        "email me at hello@mail.com",  # @mail — 'l' continues the word, no boundary
        "the @maintainer pinged me",
        "",
    ],
)
def test_non_tags_do_not_trigger(text: str) -> None:
    assert not is_mai_invocation(_own_msg(text))


def test_not_from_me_never_triggers() -> None:
    msg = InboundMessage(
        from_number="972540000002",
        text="@MAI hello",
        message_id="m2",
        from_me=False,
        chat_id="972540000002",
    )
    assert not is_mai_invocation(msg)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("@mai do the thing", "do the thing"),
        ("@MAI do the thing", "do the thing"),
        ("@mAi do the thing", "do the thing"),
    ],
)
def test_strip_mention_is_case_insensitive(text: str, expected: str) -> None:
    assert _strip_mention(text) == expected.strip()
