"""`clock.py` — the one module allowed to read the wall clock.

Everything else in the package takes an injected `Clock`, so these tests
pin the three things the rest of the package assumes: a `ManualTime` two
clocks can share (determinism, and a replay feed that can drive time),
a `WallClock` whose wait is interruptible within a second (a stop flag
must never be blocked behind a long sleep), and a `monotonic()` that does
not follow the wall clock backwards — D6 pins ordering to `tick_at` and
pacing to `time.monotonic()`, which only holds if a wall-clock jump
cannot rewind the pacing source.

Every test is deterministic. `test_wall_clock_sleep_until_honours_the_
stop_flag` is the single test in this file that touches real time, and it
is bounded to well under two seconds; the slicing test that would
otherwise need five real seconds fakes `time.sleep` instead and inspects
the slices it was asked for.
"""

import threading
import time

import pytest

from dskit.production.base import ProductionError
from dskit.production.clock import (
    CLOCK_KINDS,
    Clock,
    ManualTime,
    ReplayClock,
    WallClock,
)
from dskit.production.clock import TestClock as _TestClock  # not a pytest class

#: A fixed instant, so nothing here depends on when the suite runs.
START_MS = 1_767_225_600_000  # 2026-01-01T00:00:00Z


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


def test_clock_hooks_are_abstract_so_an_incomplete_clock_cannot_construct():
    assert {"now_ms", "monotonic", "sleep_until"} <= Clock.__abstractmethods__
    with pytest.raises(TypeError):
        Clock()


def test_every_shipped_clock_is_a_clock():
    for cls in (WallClock, _TestClock, ReplayClock):
        assert issubclass(cls, Clock)


def test_replay_clock_shares_mechanism_with_the_test_clock_but_is_not_one():
    """§5.1: the relationship is a shared `ManualTime`, not is-a."""
    assert not issubclass(ReplayClock, _TestClock)
    assert not issubclass(_TestClock, ReplayClock)


def test_registry_lists_exactly_the_three_clock_kinds():
    assert CLOCK_KINDS.kinds() == ("replay", "test", "wall")
    assert CLOCK_KINDS.family == "clock"
    assert CLOCK_KINDS.resolve("wall") is WallClock
    assert CLOCK_KINDS.resolve("test") is _TestClock
    assert CLOCK_KINDS.resolve("replay") is ReplayClock
    assert "wall" in CLOCK_KINDS
    assert "system" not in CLOCK_KINDS


def test_registry_resolves_a_clock_by_class_reference():
    assert CLOCK_KINDS.resolve("dskit.production.clock:WallClock") is WallClock


def test_registry_refuses_an_unregistered_clock_name():
    with pytest.raises(ProductionError):
        CLOCK_KINDS.resolve("cuckoo")


# ---------------------------------------------------------------------------
# ManualTime — the settable instant both fake clocks compose
# ---------------------------------------------------------------------------


def test_manual_time_starts_at_the_epoch_by_default():
    assert ManualTime().now_ms() == 0


def test_manual_time_sets_and_advances_exactly():
    manual = ManualTime(now_ms=START_MS)
    assert manual.now_ms() == START_MS
    manual.advance(1_500)
    assert manual.now_ms() == START_MS + 1_500
    manual.set(START_MS + 90_000)
    assert manual.now_ms() == START_MS + 90_000


def test_manual_time_monotonic_advances_with_it_in_float_seconds():
    manual = ManualTime(now_ms=START_MS)
    before = manual.monotonic()
    assert isinstance(before, float)
    manual.advance(1_500)
    assert manual.monotonic() - before == pytest.approx(1.5)


def test_manual_time_monotonic_never_follows_a_backwards_set():
    """The pacing source is monotone even when the wall value is rewound."""
    manual = ManualTime(now_ms=START_MS)
    manual.advance(2_000)
    paced = manual.monotonic()
    manual.set(START_MS - 3_600_000)
    assert manual.now_ms() == START_MS - 3_600_000
    assert manual.monotonic() >= paced


def test_manual_time_refuses_a_negative_advance():
    with pytest.raises(ProductionError):
        ManualTime(now_ms=START_MS).advance(-1)


# ---------------------------------------------------------------------------
# TestClock — determinism
# ---------------------------------------------------------------------------


def test_test_clock_reads_the_same_instant_twice_and_never_the_wall_clock():
    clock = _TestClock(start_ms=START_MS)
    assert clock.now_ms() == START_MS
    assert clock.now_ms() == START_MS
    assert clock.monotonic() == clock.monotonic()


def test_test_clock_set_and_advance_are_exact():
    clock = _TestClock(start_ms=START_MS)
    clock.advance(60_000)
    assert clock.now_ms() == START_MS + 60_000
    clock.set(START_MS)
    assert clock.now_ms() == START_MS


def test_test_clock_sleep_until_jumps_the_manual_time_and_reports_arrival():
    clock = _TestClock(start_ms=START_MS)
    target = START_MS + 5_000
    assert clock.sleep_until(target, lambda: False) is True
    assert clock.now_ms() == target


def test_test_clock_sleep_until_a_past_instant_never_moves_time_backwards():
    clock = _TestClock(start_ms=START_MS)
    assert clock.sleep_until(START_MS - 5_000, lambda: False) is True
    assert clock.now_ms() == START_MS


def test_test_clock_sleep_until_returns_early_and_still_when_wake_is_set():
    clock = _TestClock(start_ms=START_MS)
    assert clock.sleep_until(START_MS + 5_000, lambda: True) is False
    assert clock.now_ms() == START_MS


# ---------------------------------------------------------------------------
# ReplayClock — driven by the replay feed, never by itself
# ---------------------------------------------------------------------------


def test_replay_clock_starts_at_a_fresh_manual_time_when_given_none():
    assert ReplayClock().now_ms() == 0


def test_one_manual_time_shared_by_both_clocks_is_visible_from_either():
    clock = _TestClock(start_ms=START_MS)
    replay = ReplayClock(manual_time=clock.time)
    assert replay.time is clock.time
    clock.advance(2_500)
    assert replay.now_ms() == START_MS + 2_500
    replay.time.set(START_MS + 9_000)
    assert clock.now_ms() == START_MS + 9_000


def test_replay_clock_sleep_until_neither_blocks_nor_advances_time():
    replay = ReplayClock(manual_time=ManualTime(now_ms=START_MS))
    assert replay.sleep_until(START_MS + 86_400_000, lambda: False) is True
    assert replay.now_ms() == START_MS
    assert replay.sleep_until(START_MS + 86_400_000, lambda: True) is False
    assert replay.now_ms() == START_MS


# ---------------------------------------------------------------------------
# WallClock — the only reader of `time`
# ---------------------------------------------------------------------------


def test_wall_clock_now_ms_is_the_wall_clock_in_integer_milliseconds(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1_767_225_600.5)
    now = WallClock().now_ms()
    assert isinstance(now, int)
    assert now == 1_767_225_600_500


def test_wall_clock_monotonic_is_float_seconds():
    assert isinstance(WallClock().monotonic(), float)


def test_wall_clock_monotonic_pacing_survives_a_wall_clock_jump(monkeypatch):
    """A wall clock rewound an hour must not rewind the pacing source."""
    clock = WallClock()
    monkeypatch.setattr(time, "time", lambda: 1_767_225_600.0)
    assert clock.now_ms() == 1_767_225_600_000
    paced = clock.monotonic()
    monkeypatch.setattr(time, "time", lambda: 1_767_222_000.0)  # an hour earlier
    assert clock.now_ms() == 1_767_222_000_000  # the wall value follows
    assert clock.monotonic() >= paced  # the pacing source does not


def test_wall_clock_sleeps_in_slices_of_at_most_one_second(monkeypatch):
    """§5.1/§5.11: a stop flag is honoured within a second, so no single
    sleep may be longer than that however far away the target is."""
    slices = []
    fake = [1_767_225_600.0]

    def fake_time():
        fake[0] += 0.001  # any spin still terminates
        if fake[0] > 1_767_225_600.0 + 3_600:
            raise AssertionError("sleep_until did not reach its target")
        return fake[0]

    def fake_sleep(seconds):
        slices.append(seconds)
        if len(slices) > 64:
            raise AssertionError("sleep_until sliced a 5 s wait more than 64 times")
        fake[0] += seconds

    def fake_monotonic():
        return fake[0] - 1_767_225_600.0

    monkeypatch.setattr(time, "time", fake_time)
    monkeypatch.setattr(time, "sleep", fake_sleep)
    monkeypatch.setattr(time, "monotonic", fake_monotonic)

    clock = WallClock()
    target = clock.now_ms() + 5_000
    assert clock.sleep_until(target, lambda: False) is True
    assert clock.now_ms() >= target
    assert slices, "sleep_until must wait through time.sleep, not spin"
    assert max(slices) <= 1.0
    assert len(slices) >= 4  # five seconds cannot be one slice


def test_wall_clock_sleep_until_returns_at_once_when_wake_is_already_set():
    clock = WallClock()
    started = time.monotonic()
    assert clock.sleep_until(clock.now_ms() + 3_600_000, lambda: True) is False
    assert time.monotonic() - started < 0.5


def test_wall_clock_sleep_until_honours_the_stop_flag():
    """The one real-time test in this file: a target an hour away must be
    abandoned within a second of the flag turning true."""
    clock = WallClock()
    stop = threading.Event()
    timer = threading.Timer(0.05, stop.set)
    timer.daemon = True
    timer.start()
    try:
        started = time.monotonic()
        reached = clock.sleep_until(clock.now_ms() + 3_600_000, stop.is_set)
        elapsed = time.monotonic() - started
    finally:
        timer.cancel()
    assert reached is False
    assert elapsed < 2.0
