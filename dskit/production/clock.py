"""Time as an injected object, and the one module allowed to read the wall clock.

Everything else in ``dskit.production`` takes a :class:`Clock` and calls
``now_ms()`` / ``monotonic()`` / ``sleep_until()``, which is what lets one
serve loop run against the wall, against a hand-advanced instant in a test
and against a replay tape without a branch anywhere (D2, §5.1). Three facts
the rest of the package relies on are pinned here:

* **Two fakes, one mechanism.** :class:`TestClock` and :class:`ReplayClock`
  each compose a :class:`ManualTime` — the settable instant — so a replay
  feed that drives one ``ManualTime`` moves both clocks together. The
  relationship is shared mechanism, not is-a: ``ReplayClock`` does not
  subclass ``TestClock``.
* **Pacing never rewinds.** D6 orders ticks by ``tick_at``, records wall
  time as ``observed_at_ms`` and paces with ``monotonic()``; that only holds
  if a wall-clock jump cannot rewind the pacing source, so :class:`WallClock`
  reads ``time.monotonic()`` and :class:`ManualTime` keeps a monotone
  counter of its own that ignores a backwards ``set``.
* **A stop flag is honoured within a second.** :class:`WallClock` sleeps in
  slices of at most :data:`MAX_SLEEP_SLICE_S` and re-checks ``wake()``
  between them (§5.11), so a target an hour away never pins the process
  behind one long sleep.

Clocks are built by keyword (``WallClock()``, ``TestClock(start_ms=…)``,
``ReplayClock(manual_time=…)``), not from a params dict: a clock's only
configuration is an instant, or a shared ``ManualTime`` object that
``compose.py`` hands to the replay feed as well — never JSON.
"""

import time
from abc import ABC, abstractmethod

from dskit.production.base import ProductionError, Registry

__all__ = [
    "CLOCK_KINDS",
    "MAX_SLEEP_SLICE_S",
    "Clock",
    "ManualTime",
    "ReplayClock",
    "TestClock",
    "WallClock",
]

#: The longest single ``time.sleep`` a :class:`WallClock` issues, in seconds:
#: the bound within which a stop flag is noticed (§5.1, §5.11).
MAX_SLEEP_SLICE_S = 1.0

_MS_PER_S = 1000


def _instant(value, what):
    """Return ``value`` as an epoch-ms int, refusing bools, floats and the rest."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProductionError([f"{what} must be an epoch-ms int, got {value!r}"])
    return value


class ManualTime:
    """The settable instant both fake clocks compose.

    Holds a wall value in epoch milliseconds and a pacing counter in
    seconds. ``set`` moves the wall value anywhere; the pacing counter only
    ever grows by the forward distance, so ``monotonic()`` never decreases
    even when the wall value is rewound — the same guarantee
    ``time.monotonic()`` gives a :class:`WallClock`.

    Parameters
    ----------
    now_ms : int, optional
        The starting wall instant, epoch milliseconds. Default ``0``.

    Examples
    --------
    One instant, advanced, then rewound without rewinding the pacing::

        manual = ManualTime(now_ms=1_767_225_600_000)
        manual.advance(1_500)
        manual.now_ms()  # 1767225601500
        paced = manual.monotonic()  # 1.5
        manual.set(0)
        manual.monotonic() >= paced  # True
    """

    def __init__(self, now_ms=0):
        self._now_ms = _instant(now_ms, "now_ms")
        self._paced_ms = 0

    def now_ms(self):
        """Return the wall instant.

        Returns
        -------
        int
            Epoch milliseconds.
        """
        return self._now_ms

    def monotonic(self):
        """Return the pacing counter.

        Returns
        -------
        float
            Seconds advanced so far; never decreases.
        """
        return self._paced_ms / _MS_PER_S

    def set(self, ms):
        """Move the wall instant to ``ms``; pacing grows only on a forward move.

        Parameters
        ----------
        ms : int
            The new wall instant, epoch milliseconds. May be earlier than
            the current one.

        Raises
        ------
        ProductionError
            If ``ms`` is not an int.
        """
        ms = _instant(ms, "set")
        if ms > self._now_ms:
            self._paced_ms += ms - self._now_ms
        self._now_ms = ms

    def advance(self, ms):
        """Move the wall instant forward by ``ms``.

        Parameters
        ----------
        ms : int
            Milliseconds to add; zero is allowed, a negative value refuses.

        Raises
        ------
        ProductionError
            If ``ms`` is not an int or is negative.
        """
        ms = _instant(ms, "advance")
        if ms < 0:
            raise ProductionError([f"advance must not be negative, got {ms}"])
        self.set(self._now_ms + ms)


class Clock(ABC):
    """The time seam every other class is handed (§5.1).

    Two readings and one wait: ``now_ms()`` is the wall value that stamps
    records, ``monotonic()`` is the pacing source for timeouts and
    ``sleep_until`` blocks — or does not — until an instant, giving up as
    soon as ``wake()`` says so. All three are abstract, so a clock missing
    one refuses to construct.

    Examples
    --------
    A clock frozen at one instant, complete enough to construct::

        class Frozen(Clock):
            def now_ms(self):
                return 1_767_225_600_000

            def monotonic(self):
                return 0.0

            def sleep_until(self, epoch_ms, wake):
                return not wake()

        Frozen().now_ms()  # 1767225600000
    """

    @abstractmethod
    def now_ms(self):
        """Return the wall instant.

        Returns
        -------
        int
            Epoch milliseconds; the value that stamps ``observed_at_ms``.
        """

    @abstractmethod
    def monotonic(self):
        """Return the pacing source.

        Returns
        -------
        float
            Seconds from an arbitrary origin; never decreases, whatever the
            wall clock does.
        """

    @abstractmethod
    def sleep_until(self, epoch_ms, wake):
        """Wait until ``epoch_ms`` unless ``wake()`` turns true first.

        Parameters
        ----------
        epoch_ms : int
            The instant to wait for; one already past returns at once.
        wake : callable
            Zero-argument predicate polled while waiting; true means stop
            waiting now.

        Returns
        -------
        bool
            ``True`` when the instant was reached, ``False`` when ``wake()``
            ended the wait first.
        """


class WallClock(Clock):
    """The real clock — the only reader of :mod:`time` in the package.

    ``now_ms`` is ``time.time()`` in integer milliseconds, ``monotonic`` is
    ``time.monotonic()``, and ``sleep_until`` waits through ``time.sleep``
    in slices no longer than :data:`MAX_SLEEP_SLICE_S`, polling ``wake()``
    between slices.

    Examples
    --------
    Wait for an instant unless a stop flag is raised first::

        import threading

        clock = WallClock()
        stop = threading.Event()
        reached = clock.sleep_until(clock.now_ms() + 2_000, stop.is_set)
        reached  # True after about two seconds (False had `stop` been set)
    """

    def now_ms(self):
        """Return ``time.time()`` as integer epoch milliseconds.

        Returns
        -------
        int
            Epoch milliseconds, truncated.
        """
        return int(time.time() * _MS_PER_S)

    def monotonic(self):
        """Return ``time.monotonic()``.

        Returns
        -------
        float
            Seconds from an arbitrary origin; immune to wall-clock jumps.
        """
        return time.monotonic()

    def sleep_until(self, epoch_ms, wake):
        """Sleep in slices of at most :data:`MAX_SLEEP_SLICE_S` until ``epoch_ms``.

        Parameters
        ----------
        epoch_ms : int
            The instant to wait for.
        wake : callable
            Polled before every slice; true abandons the wait.

        Returns
        -------
        bool
            ``True`` when the instant was reached, ``False`` when ``wake()``
            ended the wait first.
        """
        while not wake():
            remaining_s = (epoch_ms - self.now_ms()) / _MS_PER_S
            if remaining_s <= 0:
                return True
            time.sleep(min(MAX_SLEEP_SLICE_S, remaining_s))
        return False


class _ManualClock(Clock):
    """A clock reading a :class:`ManualTime` exposed as ``time``."""

    def __init__(self, manual_time):
        if not isinstance(manual_time, ManualTime):
            raise ProductionError(
                [f"manual_time must be a ManualTime, got {manual_time!r}"]
            )
        self.time = manual_time

    def now_ms(self):
        """Return the shared ``ManualTime``'s wall instant.

        Returns
        -------
        int
            Epoch milliseconds.
        """
        return self.time.now_ms()

    def monotonic(self):
        """Return the shared ``ManualTime``'s pacing counter.

        Returns
        -------
        float
            Seconds; never decreases.
        """
        return self.time.monotonic()


class TestClock(_ManualClock):
    """A hand-driven clock for tests: reads the same instant until moved.

    ``sleep_until`` jumps the shared :class:`ManualTime` to the target and
    reports arrival, so a test never waits on the wall; a target already
    past leaves time where it is.

    Parameters
    ----------
    start_ms : int, optional
        The starting wall instant, epoch milliseconds. Default ``0``.

    Attributes
    ----------
    time : ManualTime
        The composed instant; hand it to a :class:`ReplayClock` to share it.

    Examples
    --------
    Advance by hand, then let a wait jump time forward::

        clock = TestClock(start_ms=1_767_225_600_000)
        clock.advance(60_000)
        clock.now_ms()  # 1767225660000
        clock.sleep_until(clock.now_ms() + 5_000, lambda: False)  # True
        clock.now_ms()  # 1767225665000
    """

    __test__ = False  # not a pytest test class

    def __init__(self, start_ms=0):
        super().__init__(ManualTime(start_ms))

    def set(self, ms):
        """Move the instant to ``ms`` (see :meth:`ManualTime.set`).

        Parameters
        ----------
        ms : int
            The new wall instant, epoch milliseconds.

        Raises
        ------
        ProductionError
            If ``ms`` is not an int.
        """
        self.time.set(ms)

    def advance(self, ms):
        """Move the instant forward by ``ms`` (see :meth:`ManualTime.advance`).

        Parameters
        ----------
        ms : int
            Milliseconds to add; negative refuses.

        Raises
        ------
        ProductionError
            If ``ms`` is not an int or is negative.
        """
        self.time.advance(ms)

    def sleep_until(self, epoch_ms, wake):
        """Jump the instant to ``epoch_ms`` unless ``wake()`` is already true.

        Parameters
        ----------
        epoch_ms : int
            The instant to jump to; one already past leaves time unchanged.
        wake : callable
            Checked once; true means no jump.

        Returns
        -------
        bool
            ``True`` when the instant is now at or past ``epoch_ms``,
            ``False`` when ``wake()`` was true.
        """
        if wake():
            return False
        if epoch_ms > self.time.now_ms():
            self.time.set(epoch_ms)
        return True


class ReplayClock(_ManualClock):
    """A clock the replay feed drives; it never advances itself.

    Shares a :class:`ManualTime` with whatever replays the tape, so time
    moves exactly when the recorded events say it did. ``sleep_until``
    neither blocks nor moves time — the feed will — and reports whether the
    wait was abandoned.

    Parameters
    ----------
    manual_time : ManualTime, optional
        The instant to share; a fresh ``ManualTime()`` at the epoch when
        omitted.

    Attributes
    ----------
    time : ManualTime
        The composed instant; the same object the feed advances.

    Examples
    --------
    One instant visible from a test clock and a replay clock alike::

        clock = TestClock(start_ms=1_767_225_600_000)
        replay = ReplayClock(manual_time=clock.time)
        clock.advance(2_500)
        replay.now_ms()  # 1767225602500
        replay.sleep_until(replay.now_ms() + 86_400_000, lambda: False)  # True
        replay.now_ms()  # 1767225602500 — unmoved; the feed moves time
    """

    def __init__(self, manual_time=None):
        super().__init__(ManualTime() if manual_time is None else manual_time)

    def sleep_until(self, epoch_ms, wake):
        """Return at once; the replay feed, not the clock, advances time.

        Parameters
        ----------
        epoch_ms : int
            Ignored beyond the contract; the feed decides when it arrives.
        wake : callable
            Checked once.

        Returns
        -------
        bool
            ``False`` when ``wake()`` is true, else ``True``.
        """
        return not wake()


CLOCK_KINDS = Registry("clock", Clock)
CLOCK_KINDS.register("wall", WallClock)
CLOCK_KINDS.register("test", TestClock)
CLOCK_KINDS.register("replay", ReplayClock)
