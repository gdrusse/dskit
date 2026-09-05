"""`sessions.py` — when the process is allowed to be awake.

The calendar is the gate every tick passes through and the object a
`{"calendar": "session"}` guard window resolves against, so an off-by-an-hour
DST error here is an hour of trading nobody authorised. Every instant these
tests assert is computed HERE from `datetime` + `zoneinfo` — never by asking
the calendar under test — which is why they can fail.

The dates are the 2026 US transitions: 2026-03-08 (spring forward, 02:00 local
does not exist) and 2026-11-01 (fall back, 01:00-02:00 local happens twice).
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from dskit.production.base import ProductionError
from dskit.production.sessions import (
    CALENDAR_KINDS,
    AlwaysOpen,
    Calendar,
    Composite,
    EventWindow,
    WeeklySessions,
)
from dskit.production.vocab import CALENDAR_WINDOWS

NY = "America/New_York"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MS = timedelta(milliseconds=1)


def utc_ms(text):
    """A naive ISO stamp read as UTC -> epoch ms, computed here."""
    return (datetime.fromisoformat(text).replace(tzinfo=timezone.utc) - _EPOCH) // _MS


def ny_ms(text, fold=0):
    """A naive ISO stamp read as New York local time -> epoch ms, computed here."""
    local = datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(NY), fold=fold)
    return (local - _EPOCH) // _MS


def weekly(**overrides):
    """The §4.1 example calendar: 09:30-16:00 New York, Monday to Friday."""
    params = {
        "tz": NY,
        "sessions": [{"days": ["mon", "tue", "wed", "thu", "fri"],
                      "open": "09:30", "close": "16:00"}],
    }
    params.update(overrides)
    return params


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


def test_calendar_hooks_are_abstract_so_an_incomplete_calendar_cannot_construct():
    assert {"is_open", "next_open", "next_close"} <= Calendar.__abstractmethods__
    with pytest.raises(TypeError):
        Calendar()


def test_registry_lists_exactly_the_four_calendar_kinds():
    assert CALENDAR_KINDS.kinds() == (
        "always-open", "composite", "event-window", "weekly-sessions",
    )
    assert CALENDAR_KINDS.family == "calendar"
    assert CALENDAR_KINDS.resolve("weekly-sessions") is WeeklySessions
    assert CALENDAR_KINDS.resolve("always-open") is AlwaysOpen
    assert CALENDAR_KINDS.resolve("event-window") is EventWindow
    assert CALENDAR_KINDS.resolve("composite") is Composite
    assert "always-open" in CALENDAR_KINDS
    assert "nyse" not in CALENDAR_KINDS


def test_registry_refuses_an_unregistered_calendar_name():
    with pytest.raises(ProductionError):
        CALENDAR_KINDS.resolve("nyse")


# ---------------------------------------------------------------------------
# AlwaysOpen
# ---------------------------------------------------------------------------


def test_always_open_is_open_at_every_instant_and_never_transitions():
    cal = AlwaysOpen({})
    assert cal.is_open(0) is True
    assert cal.is_open(utc_ms("2026-03-09T13:30:00")) is True
    assert cal.next_open(utc_ms("2026-03-09T13:30:00")) is None
    assert cal.next_close(utc_ms("2026-03-09T13:30:00")) is None


def test_always_open_reports_utc_as_its_zone():
    assert AlwaysOpen({}).tz_name == "UTC"


def test_always_open_refuses_an_unknown_param():
    with pytest.raises(ProductionError):
        AlwaysOpen({"tz": NY})


# ---------------------------------------------------------------------------
# WeeklySessions — construction
# ---------------------------------------------------------------------------


def test_weekly_sessions_declares_its_seven_knobs_and_refuses_the_rest():
    assert set(WeeklySessions._PARAMS) == {
        "tz", "sessions", "holidays", "special_closes", "blackouts",
        "after_open_s", "before_close_s",
    }
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(timezone=NY))


def test_weekly_sessions_requires_a_zone_and_at_least_one_session():
    with pytest.raises(ProductionError):
        WeeklySessions({"sessions": weekly()["sessions"]})
    with pytest.raises(ProductionError):
        WeeklySessions({"tz": NY, "sessions": []})
    with pytest.raises(ProductionError):
        WeeklySessions({"tz": NY})


def test_weekly_sessions_refuses_an_unknown_zone():
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(tz="Mars/Phobos"))


def test_weekly_sessions_refuses_a_day_outside_mon_to_sun():
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(sessions=[{"days": ["funday"], "open": "09:30",
                                         "close": "16:00"}]))


def test_weekly_sessions_refuses_a_malformed_boundary():
    for bad in ("9:30", "09:60", "24:00", "0930", "09:30:00"):
        with pytest.raises(ProductionError):
            WeeklySessions(weekly(sessions=[{"days": ["mon"], "open": bad,
                                             "close": "16:00"}]))


def test_weekly_sessions_refuses_a_close_at_or_before_its_open():
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(sessions=[{"days": ["mon"], "open": "16:00",
                                         "close": "09:30"}]))
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(sessions=[{"days": ["mon"], "open": "09:30",
                                         "close": "09:30"}]))


def test_weekly_sessions_refuses_an_unknown_key_inside_a_session():
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(sessions=[{"days": ["mon"], "open": "09:30",
                                         "close": "16:00", "venue": "xnys"}]))


def test_weekly_sessions_refuses_unsorted_or_duplicated_holidays():
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(holidays=["2026-11-26", "2026-01-01"]))
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(holidays=["2026-11-26", "2026-11-26"]))
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(holidays=["26 Nov 2026"]))


def test_weekly_sessions_refuses_a_negative_buffer_and_a_non_integer_one():
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(after_open_s=-1))
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(before_close_s=-1))
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(after_open_s="60"))


def test_weekly_sessions_refuses_buffers_that_consume_the_whole_session():
    """09:30-16:00 is 23400 s; buffers may not meet or cross."""
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(after_open_s=12_000, before_close_s=12_000))


def test_weekly_sessions_refuses_a_blackout_that_is_not_utc_or_not_forward():
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(blackouts=[{"from": "2026-03-09T14:00:00",
                                          "until": "2026-03-09T15:00:00Z"}]))
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(blackouts=[{"from": "2026-03-09T15:00:00Z",
                                          "until": "2026-03-09T14:00:00Z"}]))
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(blackouts=[{"from": "2026-03-09T14:00:00Z",
                                          "until": "2026-03-09T15:00:00Z",
                                          "why": "maintenance"}]))


def test_weekly_sessions_refuses_a_malformed_special_close():
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(special_closes=[{"date": "2026-11-27",
                                               "close": "1pm"}]))
    with pytest.raises(ProductionError):
        WeeklySessions(weekly(special_closes=[{"date": "2026-11-27",
                                               "close": "13:00",
                                               "open": "10:00"}]))


def test_weekly_sessions_accumulates_every_problem_into_one_raise():
    with pytest.raises(ProductionError) as exc:
        WeeklySessions({"tz": "Mars/Phobos", "sessions": [], "after_open_s": -1})
    assert len(exc.value.problems) >= 3


def test_a_boundary_inside_the_spring_forward_gap_refuses_at_validation():
    """02:30 does not exist on 2026-03-08 in New York, so it can never be
    a session boundary — refused when the calendar is built, not when the
    loop reaches that Sunday."""
    with pytest.raises(ProductionError) as exc:
        WeeklySessions(weekly(sessions=[{"days": ["sun"], "open": "02:30",
                                         "close": "12:00"}]))
    assert any("02:30" in problem for problem in exc.value.problems)


def test_a_close_inside_the_gap_refuses_too():
    with pytest.raises(ProductionError) as exc:
        WeeklySessions(weekly(sessions=[{"days": ["sun"], "open": "01:00",
                                         "close": "02:30"}]))
    assert any("02:30" in problem for problem in exc.value.problems)


def test_a_boundary_in_the_fall_back_repeat_is_accepted_at_its_first_pass():
    """01:30 happens twice on 2026-11-01; the calendar takes the first
    (fold 0, still on daylight time) rather than refusing."""
    cal = WeeklySessions(weekly(sessions=[{"days": ["sun"], "open": "01:30",
                                           "close": "12:00"}]))
    first = ny_ms("2026-11-01T01:30", fold=0)
    assert first == utc_ms("2026-11-01T05:30:00")
    assert cal.is_open(first) is True
    assert cal.is_open(first - 1) is False
    assert cal.is_open(ny_ms("2026-11-01T01:30", fold=1)) is True  # still inside


# ---------------------------------------------------------------------------
# WeeklySessions — the DST boundary in both directions
# ---------------------------------------------------------------------------


def test_the_open_holds_its_local_time_across_spring_forward():
    cal = WeeklySessions(weekly())
    friday = utc_ms("2026-03-06T14:30:00")   # 09:30 EST, UTC-5
    monday = utc_ms("2026-03-09T13:30:00")   # 09:30 EDT, UTC-4
    assert friday == ny_ms("2026-03-06T09:30")
    assert monday == ny_ms("2026-03-09T09:30")
    assert cal.is_open(friday) is True
    assert cal.is_open(friday - 1) is False
    assert cal.is_open(monday) is True
    assert cal.is_open(monday - 1) is False


def test_the_open_holds_its_local_time_across_fall_back():
    cal = WeeklySessions(weekly())
    friday = utc_ms("2026-10-30T13:30:00")   # 09:30 EDT
    monday = utc_ms("2026-11-02T14:30:00")   # 09:30 EST
    assert friday == ny_ms("2026-10-30T09:30")
    assert monday == ny_ms("2026-11-02T09:30")
    assert cal.is_open(friday) is True
    assert cal.is_open(friday - 1) is False
    assert cal.is_open(monday) is True
    assert cal.is_open(monday - 1) is False


def test_the_session_is_half_open_at_its_close():
    cal = WeeklySessions(weekly())
    close = utc_ms("2026-03-09T20:00:00")  # 16:00 EDT
    assert cal.is_open(close - 1) is True
    assert cal.is_open(close) is False


def test_the_weekend_is_shut():
    cal = WeeklySessions(weekly())
    assert cal.is_open(utc_ms("2026-03-07T14:30:00")) is False  # Saturday
    assert cal.is_open(utc_ms("2026-03-08T14:30:00")) is False  # Sunday


# ---------------------------------------------------------------------------
# WeeklySessions — next_open / next_close
# ---------------------------------------------------------------------------


def test_next_open_and_next_close_are_strictly_after_the_instant_asked_about():
    cal = WeeklySessions(weekly())
    friday_open = utc_ms("2026-03-06T14:30:00")
    friday_close = utc_ms("2026-03-06T21:00:00")   # 16:00 EST
    assert cal.next_open(friday_open - 1) == friday_open
    assert cal.next_open(friday_open) == utc_ms("2026-03-09T13:30:00")
    assert cal.next_close(friday_close - 1) == friday_close
    assert cal.next_close(friday_close) == utc_ms("2026-03-09T20:00:00")


def test_next_open_crosses_the_weekend_and_the_spring_forward_transition():
    cal = WeeklySessions(weekly())
    assert cal.next_open(utc_ms("2026-03-06T21:00:00")) == utc_ms("2026-03-09T13:30:00")


# ---------------------------------------------------------------------------
# Holidays, special closes, blackouts, buffers
# ---------------------------------------------------------------------------


def test_a_holiday_shuts_the_whole_day():
    cal = WeeklySessions(weekly(holidays=["2026-11-26"]))  # Thanksgiving, a Thursday
    assert cal.is_open(utc_ms("2026-11-26T14:30:00")) is False
    assert cal.is_open(utc_ms("2026-11-26T18:00:00")) is False
    # Wednesday closes at 16:00 EST; the next open skips Thursday entirely.
    assert cal.next_open(utc_ms("2026-11-25T21:00:00")) == utc_ms("2026-11-27T14:30:00")


def test_a_special_close_shortens_that_day_only():
    cal = WeeklySessions(weekly(special_closes=[{"date": "2026-11-27",
                                                 "close": "13:00"}]))
    early = utc_ms("2026-11-27T18:00:00")   # 13:00 EST
    assert early == ny_ms("2026-11-27T13:00")
    assert cal.is_open(early - 1) is True
    assert cal.is_open(early) is False
    assert cal.next_close(utc_ms("2026-11-27T14:30:00")) == early
    # The following Monday is untouched.
    assert cal.is_open(utc_ms("2026-11-30T20:00:00")) is True


def test_a_blackout_shuts_an_interval_inside_an_open_session():
    cal = WeeklySessions(weekly(blackouts=[{"from": "2026-03-09T14:00:00Z",
                                            "until": "2026-03-09T15:00:00Z"}]))
    assert cal.is_open(utc_ms("2026-03-09T13:59:59.999")) is True
    assert cal.is_open(utc_ms("2026-03-09T14:00:00")) is False
    assert cal.is_open(utc_ms("2026-03-09T14:59:59.999")) is False
    assert cal.is_open(utc_ms("2026-03-09T15:00:00")) is True
    assert cal.next_open(utc_ms("2026-03-09T14:10:00")) == utc_ms("2026-03-09T15:00:00")
    assert cal.next_close(utc_ms("2026-03-09T13:00:00")) == utc_ms("2026-03-09T14:00:00")


def test_the_buffers_shrink_the_open_interval_at_both_ends():
    cal = WeeklySessions(weekly(after_open_s=60, before_close_s=120))
    open_ms = utc_ms("2026-03-09T13:31:00")   # 09:31 EDT
    close_ms = utc_ms("2026-03-09T19:58:00")  # 15:58 EDT
    assert cal.is_open(open_ms - 1) is False
    assert cal.is_open(open_ms) is True
    assert cal.is_open(close_ms - 1) is True
    assert cal.is_open(close_ms) is False
    assert cal.next_open(utc_ms("2026-03-09T00:00:00")) == open_ms
    assert cal.next_close(utc_ms("2026-03-09T14:00:00")) == close_ms


def test_the_calendar_reports_the_zone_its_sessions_are_written_in():
    assert WeeklySessions(weekly()).tz_name == NY


# ---------------------------------------------------------------------------
# Guard windows — `{"calendar": "session"}` resolves through this same object
# ---------------------------------------------------------------------------


def test_the_window_kinds_exercised_here_are_the_whole_vocabulary():
    assert set(CALENDAR_WINDOWS) == {"session", "day", "event"}


def test_the_session_window_is_the_effective_session_around_the_instant():
    cal = WeeklySessions(weekly(after_open_s=60, before_close_s=120))
    inside = utc_ms("2026-03-09T15:00:00")
    assert cal.window("session", inside) == (utc_ms("2026-03-09T13:31:00"),
                                             utc_ms("2026-03-09T19:58:00"))


def test_the_session_window_outside_a_session_is_the_next_one():
    cal = WeeklySessions(weekly())
    sunday = utc_ms("2026-03-08T12:00:00")
    assert cal.window("session", sunday) == (utc_ms("2026-03-09T13:30:00"),
                                             utc_ms("2026-03-09T20:00:00"))


def test_the_day_window_is_the_local_day_and_is_23_hours_at_spring_forward():
    cal = WeeklySessions(weekly())
    start, end = cal.window("day", ny_ms("2026-03-08T12:00"))
    assert (start, end) == (ny_ms("2026-03-08T00:00"), ny_ms("2026-03-09T00:00"))
    assert end - start == 23 * 3_600_000


def test_the_day_window_is_25_hours_at_fall_back():
    cal = WeeklySessions(weekly())
    start, end = cal.window("day", ny_ms("2026-11-01T12:00"))
    assert (start, end) == (ny_ms("2026-11-01T00:00"), ny_ms("2026-11-02T00:00"))
    assert end - start == 25 * 3_600_000


def test_a_calendar_with_no_events_refuses_an_event_window():
    cal = WeeklySessions(weekly())
    with pytest.raises(ProductionError):
        cal.window("event", utc_ms("2026-03-09T15:00:00"))


def test_a_window_kind_outside_the_vocabulary_refuses():
    cal = WeeklySessions(weekly())
    with pytest.raises(ProductionError):
        cal.window("week", utc_ms("2026-03-09T15:00:00"))


def test_an_always_open_calendar_windows_by_utc_day():
    cal = AlwaysOpen({})
    day = (utc_ms("2026-03-09T00:00:00"), utc_ms("2026-03-10T00:00:00"))
    assert cal.window("day", utc_ms("2026-03-09T15:00:00")) == day
    assert cal.window("session", utc_ms("2026-03-09T15:00:00")) == day


# ---------------------------------------------------------------------------
# EventWindow
# ---------------------------------------------------------------------------


def test_an_event_window_opens_its_lead_before_the_event_and_shuts_at_until():
    cal = EventWindow({"start_ms": 1_000_000, "lead_ms": 60_000,
                       "until_ms": 1_200_000})
    assert cal.is_open(939_999) is False
    assert cal.is_open(940_000) is True
    assert cal.is_open(1_199_999) is True
    assert cal.is_open(1_200_000) is False
    assert cal.next_open(0) == 940_000
    assert cal.next_open(940_000) is None
    assert cal.next_close(0) == 1_200_000
    assert cal.next_close(1_200_000) is None
    assert cal.window("event", 1_000_000) == (940_000, 1_200_000)


def test_an_event_window_leads_by_nothing_unless_told_to():
    cal = EventWindow({"start_ms": 1_000_000, "until_ms": 1_200_000})
    assert cal.is_open(999_999) is False
    assert cal.is_open(1_000_000) is True


def test_an_event_window_refuses_an_empty_or_negative_shape():
    with pytest.raises(ProductionError):
        EventWindow({"start_ms": 1_000_000, "until_ms": 1_000_000})
    with pytest.raises(ProductionError):
        EventWindow({"start_ms": 1_000_000, "until_ms": 900_000})
    with pytest.raises(ProductionError):
        EventWindow({"start_ms": 1_000_000, "until_ms": 1_200_000, "lead_ms": -1})
    with pytest.raises(ProductionError):
        EventWindow({"until_ms": 1_200_000})


# ---------------------------------------------------------------------------
# Composite — the intersection
# ---------------------------------------------------------------------------


def two_events():
    return Composite({"members": [
        {"uses": "event-window", "params": {"start_ms": 100_000,
                                            "until_ms": 200_000}},
        {"uses": "event-window", "params": {"start_ms": 150_000,
                                            "until_ms": 300_000}},
    ]})


def test_a_composite_is_open_only_where_every_member_is():
    cal = two_events()
    assert cal.is_open(149_999) is False
    assert cal.is_open(150_000) is True
    assert cal.is_open(199_999) is True
    assert cal.is_open(200_000) is False


def test_a_composite_opens_when_the_last_member_opens_and_shuts_with_the_first():
    cal = two_events()
    assert cal.next_open(0) == 150_000
    assert cal.next_close(0) == 200_000
    assert cal.window("event", 160_000) == (150_000, 200_000)


def test_a_member_that_never_transitions_constrains_nothing():
    cal = Composite({"members": [
        {"uses": "always-open"},
        {"uses": "event-window", "params": {"start_ms": 100_000,
                                            "until_ms": 200_000}},
    ]})
    assert cal.is_open(99_999) is False
    assert cal.is_open(100_000) is True
    assert cal.next_open(0) == 100_000
    assert cal.next_close(0) == 200_000


def test_a_composite_reports_the_zone_of_its_first_member():
    cal = Composite({"members": [
        {"uses": "weekly-sessions", "params": weekly()},
        {"uses": "always-open"},
    ]})
    assert cal.tz_name == NY
    assert cal.is_open(utc_ms("2026-03-09T13:30:00")) is True
    assert cal.is_open(utc_ms("2026-03-07T13:30:00")) is False


def test_a_composite_refuses_an_empty_or_malformed_membership():
    with pytest.raises(ProductionError):
        Composite({"members": []})
    with pytest.raises(ProductionError):
        Composite({})
    with pytest.raises(ProductionError):
        Composite({"members": [{"uses": "nyse"}]})
    with pytest.raises(ProductionError):
        Composite({"members": [{"uses": "always-open"}], "mode": "union"})
    with pytest.raises(ProductionError):
        Composite({"members": ["always-open"]})
