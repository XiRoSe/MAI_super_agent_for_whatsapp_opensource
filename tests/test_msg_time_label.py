"""Focused unit tests for backend.whatsapp.timefmt.msg_time_label.

All cases pass a FIXED `now` so they're deterministic regardless of when they run.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.whatsapp.timefmt import msg_time_label

# A fixed reference "now": 2024-06-15 20:00 UTC (== 23:00 in Asia/Jerusalem, IDT/+3).
NOW = datetime(2024, 6, 15, 20, 0, tzinfo=timezone.utc)
JLM = "Asia/Jerusalem"


def _epoch(dt: datetime) -> float:
    return dt.timestamp()


def test_epoch_seconds_today_in_jerusalem():
    # 09:00 UTC on the reference day -> 12:00 IDT, same local day -> "today 12:00".
    ts = _epoch(datetime(2024, 6, 15, 9, 0, tzinfo=timezone.utc))
    assert msg_time_label(ts, JLM, now=NOW) == "today 12:00"


def test_epoch_milliseconds_matches_seconds():
    sec = _epoch(datetime(2024, 6, 15, 9, 0, tzinfo=timezone.utc))
    assert msg_time_label(int(sec * 1000), JLM, now=NOW) == "today 12:00"


def test_numeric_string_epoch():
    sec = _epoch(datetime(2024, 6, 15, 9, 0, tzinfo=timezone.utc))
    assert msg_time_label(str(int(sec)), JLM, now=NOW) == "today 12:00"


def test_iso_string_with_z():
    assert msg_time_label("2024-06-15T09:00:00Z", JLM, now=NOW) == "today 12:00"


def test_iso_string_naive_assumed_utc():
    assert msg_time_label("2024-06-15T09:00:00", JLM, now=NOW) == "today 12:00"


def test_previous_day_yesterday():
    # 09:00 UTC the day before -> 12:00 IDT on the 14th -> "yesterday 12:00".
    ts = _epoch(datetime(2024, 6, 14, 9, 0, tzinfo=timezone.utc))
    assert msg_time_label(ts, JLM, now=NOW) == "yesterday 12:00"


def test_older_same_year():
    # January (IST/+2): 09:00 UTC -> 11:00 local -> "10/01 11:00".
    ts = _epoch(datetime(2024, 1, 10, 9, 0, tzinfo=timezone.utc))
    assert msg_time_label(ts, JLM, now=NOW) == "10/01 11:00"


def test_older_different_year():
    # March 2022 (before DST, IST/+2): 09:00 UTC -> 11:00 local -> "05/03/22 11:00".
    ts = _epoch(datetime(2022, 3, 5, 9, 0, tzinfo=timezone.utc))
    assert msg_time_label(ts, JLM, now=NOW) == "05/03/22 11:00"


def test_tz_none_falls_back_to_utc():
    ts = _epoch(datetime(2024, 6, 15, 9, 0, tzinfo=timezone.utc))
    assert msg_time_label(ts, None, now=NOW) == "today 09:00"


def test_invalid_tz_falls_back_to_utc():
    ts = _epoch(datetime(2024, 6, 15, 9, 0, tzinfo=timezone.utc))
    assert msg_time_label(ts, "Not/AZone", now=NOW) == "today 09:00"


def test_datetime_passthrough():
    dt = datetime(2024, 6, 15, 9, 0, tzinfo=timezone.utc)
    assert msg_time_label(dt, JLM, now=NOW) == "today 12:00"


def test_none_returns_empty():
    assert msg_time_label(None, JLM, now=NOW) == ""


def test_empty_string_returns_empty():
    assert msg_time_label("", JLM, now=NOW) == ""


def test_garbage_returns_empty():
    assert msg_time_label("not-a-date", JLM, now=NOW) == ""


def test_naive_now_is_treated_as_utc():
    # A naive `now` must not raise and should behave like its UTC equivalent.
    ts = _epoch(datetime(2024, 6, 15, 9, 0, tzinfo=timezone.utc))
    naive_now = datetime(2024, 6, 15, 20, 0)
    assert msg_time_label(ts, JLM, now=naive_now) == "today 12:00"
