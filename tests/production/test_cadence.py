"""`cadence.py` — when the next tick is due, and what to do when it is late.

Two properties matter more than the rest. Drift: a cadence that computed the
next tick from "now" would slide by however long the last handler took, so
`FixedInterval` is asserted over a million slow ticks to land on `anchor +
k * period` exactly. And agreement with the gate: a cadence that proposed an
instant the calendar calls shut would spend the session waking up to be
refused, so the calendar is passed in and honoured here.

Every instant asserted is computed in this file from `datetime`; the calendars
these tests schedule against keep their sessions in UTC so the arithmetic is
visible, except the two `at-times` cases that exist to prove the wall-clock
times are read in the CALENDAR's zone rather than in UTC.
"""

from datetime import datetime, timedelta, timezone

import pytest

from dskit.production.base import ProductionError
from dskit.production.cadence import (
    CADENCE_KINDS,
    DEFAULT_MAX_LAG_MS,
    DEFAULT_OVERRUN_POLICY,
    AlignedBar,
    AtTimes,
    Cadence,
    FixedInterval,
    OnData,
    Overrun,
)
from dskit.production.sessions import AlwaysOpen, EventWindow, WeeklySessions
from dskit.production.vocab import AT_TIMES_RELATIVE, OVERRUN_POLICIES

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MS = timedelta(milliseconds=1)

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"]


def utc_ms(text):
    """A naive ISO stamp read as UTC -> epoch ms, computed here."""
    return (datetime.fromisoformat(text).replace(tzinfo=timezone.utc) - _EPOCH) // _MS


def utc_sessions():
    """09:30-16:00 UTC, Monday to Friday: 2026-03-09 is a Monday."""
    return WeeklySessions({"tz": "UTC", "sessions": [
        {"days": WEEKDAYS, "open": "09:30", "close": "16:00"}]})


def ny_sessions():
    return WeeklySessions({"tz": "America/New_York", "sessions": [
        {"days": WEEKDAYS, "open": "09:30", "close": "16:00"}]})


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


def test_next_tick_is_abstract_so_an_incomplete_cadence_cannot_construct():
    assert "next_tick" in Cadence.__abstractmethods__
    with pytest.raises(TypeError):
        Cadence()


def test_overrun_is_a_strategy_beside_the_cadences_not_one_of_them():
    assert not issubclass(Overrun, Cadence)


def test_registry_lists_exactly_the_four_cadence_kinds():
    assert CADENCE_KINDS.kinds() == ("aligned-bar", "at-times", "fixed-interval",
                                     "on-data")
    assert CADENCE_KINDS.family == "cadence"
    assert CADENCE_KINDS.resolve("aligned-bar") is AlignedBar
    assert CADENCE_KINDS.resolve("at-times") is AtTimes
    assert CADENCE_KINDS.resolve("fixed-interval") is FixedInterval
    assert CADENCE_KINDS.resolve("on-data") is OnData
    assert "cron" not in CADENCE_KINDS


def test_registry_refuses_an_unregistered_cadence_name():
    with pytest.raises(ProductionError):
        CADENCE_KINDS.resolve("cron")


# ---------------------------------------------------------------------------
# FixedInterval — the drift-free grid
# ---------------------------------------------------------------------------


def test_fixed_interval_returns_the_first_grid_point_strictly_after_the_instant():
    cadence = FixedInterval({"period_ms": 60_000, "anchor_ms": 1_000})
    assert cadence.next_tick(1_000, AlwaysOpen({})) == 61_000
    assert cadence.next_tick(60_999, AlwaysOpen({})) == 61_000
    assert cadence.next_tick(61_000, AlwaysOpen({})) == 121_000


def test_fixed_interval_anchors_at_the_epoch_unless_told_otherwise():
    cadence = FixedInterval({"period_ms": 60_000})
    assert cadence.next_tick(utc_ms("2026-03-09T10:00:03"), AlwaysOpen({})) == \
        utc_ms("2026-03-09T10:01:00")


def test_fixed_interval_has_zero_drift_over_a_million_slow_ticks():
    """Each 'handler' finishes half a period late, so a cadence that measured
    from its own return value would be half a period out after one tick and
    half a million periods out at the end."""
    period = 60_000
    anchor = utc_ms("2026-01-01T00:00:00")
    cadence = FixedInterval({"period_ms": period, "anchor_ms": anchor})
    calendar = AlwaysOpen({})
    after = anchor
    tick = None
    for _ in range(1_000_000):
        tick = cadence.next_tick(after, calendar)
        after = tick + period // 2 + 1  # a slow handler, still inside the period
    assert tick == anchor + 1_000_000 * period


def test_fixed_interval_keeps_its_grid_across_a_closed_span():
    """Skipping a shut span never shifts the grid: the tick after the weekend
    is still on the hour."""
    cadence = FixedInterval({"period_ms": 3_600_000})
    assert cadence.next_tick(utc_ms("2026-03-09T16:30:00"), utc_sessions()) == \
        utc_ms("2026-03-10T10:00:00")


def test_a_cadence_reports_no_next_tick_when_the_calendar_never_reopens():
    cadence = FixedInterval({"period_ms": 1_000})
    finished = EventWindow({"start_ms": 0, "until_ms": 10_000})
    assert cadence.next_tick(20_000, finished) is None


def test_fixed_interval_refuses_a_period_under_a_second_and_a_bad_anchor():
    with pytest.raises(ProductionError):
        FixedInterval({"period_ms": 999})
    with pytest.raises(ProductionError):
        FixedInterval({"period_ms": 0})
    with pytest.raises(ProductionError):
        FixedInterval({"period_ms": 60_000, "anchor_ms": -1})
    with pytest.raises(ProductionError):
        FixedInterval({})
    with pytest.raises(ProductionError):
        FixedInterval({"period_ms": 60_000, "period_s": 60})


# ---------------------------------------------------------------------------
# AlignedBar — bar boundary plus publish delay
# ---------------------------------------------------------------------------


def test_aligned_bar_fires_a_fixed_delay_after_each_bar_boundary():
    cadence = AlignedBar({"bar_ms": 60_000, "publish_delay_ms": 5_000})
    calendar = AlwaysOpen({})
    assert cadence.next_tick(utc_ms("2026-03-09T10:00:03"), calendar) == \
        utc_ms("2026-03-09T10:00:05")
    assert cadence.next_tick(utc_ms("2026-03-09T10:00:05"), calendar) == \
        utc_ms("2026-03-09T10:01:05")
    assert cadence.next_tick(utc_ms("2026-03-09T10:00:06"), calendar) == \
        utc_ms("2026-03-09T10:01:05")


def test_aligned_bar_publishes_at_the_boundary_when_no_delay_is_configured():
    cadence = AlignedBar({"bar_ms": 60_000})
    assert cadence.next_tick(utc_ms("2026-03-09T10:00:03"), AlwaysOpen({})) == \
        utc_ms("2026-03-09T10:01:00")


def test_aligned_bar_bars_are_aligned_to_the_epoch_not_to_the_first_call():
    cadence = AlignedBar({"bar_ms": 900_000})  # quarter hours
    assert cadence.next_tick(utc_ms("2026-03-09T10:07:13"), AlwaysOpen({})) == \
        utc_ms("2026-03-09T10:15:00")


def test_aligned_bar_skips_the_instants_the_calendar_calls_shut():
    """Gate and cadence must agree: 16:00 Monday to 09:30 Tuesday is shut, and
    the first publish instant inside Tuesday's session is 09:30:05."""
    cadence = AlignedBar({"bar_ms": 60_000, "publish_delay_ms": 5_000})
    assert cadence.next_tick(utc_ms("2026-03-09T16:30:00"), utc_sessions()) == \
        utc_ms("2026-03-10T09:30:05")


def test_aligned_bar_refuses_a_bar_under_a_second_or_a_negative_delay():
    with pytest.raises(ProductionError):
        AlignedBar({"bar_ms": 999})
    with pytest.raises(ProductionError):
        AlignedBar({"bar_ms": 60_000, "publish_delay_ms": -1})
    with pytest.raises(ProductionError):
        AlignedBar({})
    with pytest.raises(ProductionError):
        AlignedBar({"bar_ms": 60_000, "delay_ms": 5_000})


# ---------------------------------------------------------------------------
# AtTimes — anchored to the open, to the close, or to the wall clock
# ---------------------------------------------------------------------------


TIMES_FOR = {"open": [300], "close": [600], "clock": ["09:35"]}


def test_every_relative_in_the_vocabulary_is_implemented():
    assert set(TIMES_FOR) == set(AT_TIMES_RELATIVE)
    calendar = utc_sessions()
    for relative, times in TIMES_FOR.items():
        cadence = AtTimes({"times": times, "relative": relative})
        assert isinstance(cadence.next_tick(utc_ms("2026-03-09T09:31:00"),
                                            calendar), int)


def test_at_times_relative_to_the_open_fires_at_its_offset_after_it():
    cadence = AtTimes({"times": [300], "relative": "open"})
    calendar = utc_sessions()
    assert cadence.next_tick(utc_ms("2026-03-09T08:00:00"), calendar) == \
        utc_ms("2026-03-09T09:35:00")
    assert cadence.next_tick(utc_ms("2026-03-09T09:35:00"), calendar) == \
        utc_ms("2026-03-10T09:35:00")


def test_at_times_relative_to_the_open_still_fires_once_the_session_has_begun():
    """The anchor is the session already under way, not the next one: asking
    at 09:34 must return today's 09:35, not tomorrow's."""
    cadence = AtTimes({"times": [300], "relative": "open"})
    assert cadence.next_tick(utc_ms("2026-03-09T09:34:00"), utc_sessions()) == \
        utc_ms("2026-03-09T09:35:00")


def test_at_times_relative_to_the_close_fires_at_its_offset_before_it():
    cadence = AtTimes({"times": [600], "relative": "close"})
    calendar = utc_sessions()
    assert cadence.next_tick(utc_ms("2026-03-09T12:00:00"), calendar) == \
        utc_ms("2026-03-09T15:50:00")
    assert cadence.next_tick(utc_ms("2026-03-09T15:50:00"), calendar) == \
        utc_ms("2026-03-10T15:50:00")


def test_at_times_can_fire_exactly_on_the_close():
    """A zero offset against the close is the flatten-at-the-bell tick; the
    calendar is shut at that instant by definition, so a boundary-anchored
    time is not filtered by `is_open`."""
    cadence = AtTimes({"times": [0], "relative": "close"})
    assert cadence.next_tick(utc_ms("2026-03-09T12:00:00"), utc_sessions()) == \
        utc_ms("2026-03-09T16:00:00")


def test_at_times_takes_every_configured_time_in_order():
    cadence = AtTimes({"times": [300, 600], "relative": "open"})
    calendar = utc_sessions()
    assert cadence.next_tick(utc_ms("2026-03-09T09:34:00"), calendar) == \
        utc_ms("2026-03-09T09:35:00")
    assert cadence.next_tick(utc_ms("2026-03-09T09:35:00"), calendar) == \
        utc_ms("2026-03-09T09:40:00")


def test_at_times_on_the_wall_clock_skips_the_days_the_calendar_is_shut():
    cadence = AtTimes({"times": ["09:35"], "relative": "clock"})
    calendar = utc_sessions()
    assert cadence.next_tick(utc_ms("2026-03-09T09:00:00"), calendar) == \
        utc_ms("2026-03-09T09:35:00")
    # Friday, after the time has passed: the next one is Monday.
    assert cadence.next_tick(utc_ms("2026-03-06T09:40:00"), calendar) == \
        utc_ms("2026-03-09T09:35:00")


def test_at_times_on_the_wall_clock_reads_the_calendars_zone_not_utc():
    """09:35 in New York is 13:35Z on daylight time and 14:35Z on standard
    time; a cadence holding its own zone would drift by an hour twice a year."""
    cadence = AtTimes({"times": ["09:35"], "relative": "clock"})
    calendar = ny_sessions()
    assert cadence.next_tick(utc_ms("2026-03-09T13:00:00"), calendar) == \
        utc_ms("2026-03-09T13:35:00")
    assert cadence.next_tick(utc_ms("2026-11-02T14:00:00"), calendar) == \
        utc_ms("2026-11-02T14:35:00")


def test_at_times_refuses_an_unknown_relative_and_an_empty_time_list():
    with pytest.raises(ProductionError):
        AtTimes({"times": ["09:35"], "relative": "sideways"})
    with pytest.raises(ProductionError):
        AtTimes({"times": [], "relative": "clock"})
    with pytest.raises(ProductionError):
        AtTimes({"times": ["09:35"]})
    with pytest.raises(ProductionError):
        AtTimes({"relative": "clock"})
    with pytest.raises(ProductionError):
        AtTimes({"times": ["09:35"], "relative": "clock", "tz": "UTC"})


def test_at_times_refuses_a_time_of_the_wrong_kind_for_its_anchor():
    with pytest.raises(ProductionError):
        AtTimes({"times": [300], "relative": "clock"})     # seconds, not HH:MM
    with pytest.raises(ProductionError):
        AtTimes({"times": ["09:35"], "relative": "open"})  # HH:MM, not seconds
    with pytest.raises(ProductionError):
        AtTimes({"times": [-1], "relative": "open"})
    with pytest.raises(ProductionError):
        AtTimes({"times": ["9:35"], "relative": "clock"})
    with pytest.raises(ProductionError):
        AtTimes({"times": ["24:00"], "relative": "clock"})


# ---------------------------------------------------------------------------
# OnData — polling
# ---------------------------------------------------------------------------


def test_on_data_polls_one_period_after_the_instant_it_is_given():
    cadence = OnData({"poll_ms": 250})
    assert cadence.next_tick(1_000_000, AlwaysOpen({})) == 1_000_250


def test_on_data_waits_for_the_open_when_its_poll_lands_in_a_shut_span():
    cadence = OnData({"poll_ms": 1_000})
    assert cadence.next_tick(utc_ms("2026-03-09T16:00:00"), utc_sessions()) == \
        utc_ms("2026-03-10T09:30:00")


def test_on_data_refuses_a_poll_faster_than_a_tenth_of_a_second():
    with pytest.raises(ProductionError):
        OnData({"poll_ms": 99})
    with pytest.raises(ProductionError):
        OnData({})
    with pytest.raises(ProductionError):
        OnData({"poll_ms": 250, "poll_s": 1})


# ---------------------------------------------------------------------------
# Overrun — a late tick is never run twice and never runs concurrently
# ---------------------------------------------------------------------------


DUE = (1_000_000, 1_060_000, 1_120_000)


def test_overrun_defaults_to_coalescing_and_to_one_named_lag_bound():
    overrun = Overrun({})
    assert DEFAULT_OVERRUN_POLICY == "coalesce"
    assert overrun.policy == DEFAULT_OVERRUN_POLICY
    assert overrun.max_lag_ms == DEFAULT_MAX_LAG_MS
    assert isinstance(DEFAULT_MAX_LAG_MS, int)
    assert DEFAULT_MAX_LAG_MS > 0
    assert set(Overrun._PARAMS) == {"policy", "max_lag_ms"}


def test_every_policy_in_the_vocabulary_is_implemented():
    for policy in OVERRUN_POLICIES:
        overrun = Overrun({"policy": policy, "max_lag_ms": 30_000})
        due_at, absorbed = overrun.resolve(DUE, 1_140_000)
        assert due_at in DUE
        assert isinstance(absorbed, tuple)
        assert set(absorbed) <= set(DUE)


def test_coalesce_runs_the_latest_due_tick_and_names_the_ones_it_absorbed():
    overrun = Overrun({"policy": "coalesce", "max_lag_ms": 30_000})
    assert overrun.resolve(DUE, 1_140_000) == (1_120_000, (1_000_000, 1_060_000))


def test_coalesce_runs_nothing_when_even_the_latest_tick_is_past_the_lag_bound():
    overrun = Overrun({"policy": "coalesce", "max_lag_ms": 30_000})
    assert overrun.resolve(DUE, 1_120_000 + 30_000) == (1_120_000,
                                                        (1_000_000, 1_060_000))
    assert overrun.resolve(DUE, 1_120_000 + 30_001) == (None, DUE)


def test_skip_always_runs_the_freshest_due_tick_however_late_it_is():
    overrun = Overrun({"policy": "skip", "max_lag_ms": 1_000})
    assert overrun.resolve(DUE, 9_000_000) == (1_120_000, (1_000_000, 1_060_000))


def test_queue_runs_the_oldest_due_tick_and_absorbs_none_of_them():
    overrun = Overrun({"policy": "queue", "max_lag_ms": 1_000})
    assert overrun.resolve(DUE, 9_000_000) == (1_000_000, ())
    assert overrun.resolve(DUE[1:], 9_000_000) == (1_060_000, ())


def test_a_single_due_tick_is_run_by_every_policy_with_nothing_absorbed():
    for policy in OVERRUN_POLICIES:
        overrun = Overrun({"policy": policy, "max_lag_ms": 30_000})
        assert overrun.resolve((1_120_000,), 1_120_001) == (1_120_000, ())


def test_nothing_due_runs_nothing():
    assert Overrun({}).resolve((), 9_000_000) == (None, ())


def test_overrun_refuses_a_tick_that_is_not_yet_due():
    with pytest.raises(ProductionError):
        Overrun({}).resolve((1_000_000, 2_000_000), 1_500_000)


def test_overrun_refuses_an_unknown_policy_and_a_negative_lag_bound():
    with pytest.raises(ProductionError):
        Overrun({"policy": "later"})
    with pytest.raises(ProductionError):
        Overrun({"max_lag_ms": -1})
    with pytest.raises(ProductionError):
        Overrun({"policy": "coalesce", "lag_ms": 30_000})


def test_overrun_accumulates_every_problem_into_one_raise():
    with pytest.raises(ProductionError) as exc:
        Overrun({"policy": "later", "max_lag_ms": -1})
    assert len(exc.value.problems) >= 2
