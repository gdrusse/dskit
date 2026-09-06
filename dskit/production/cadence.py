"""When the next tick is due, and what to do when the loop is late (D7, §5.1).

A :class:`Cadence` answers one question — ``next_tick(after_ms, calendar)``:
the first instant strictly after ``after_ms`` on which this cadence wants a
tick and the calendar is open — and the four kinds differ only in what
"on which this cadence wants a tick" means. Two properties are pinned:

* **No drift.** The grid cadences (``fixed-interval``, ``aligned-bar``)
  compute ``anchor + k * period`` from the instant asked about, never from
  their own last answer, so a slow handler cannot slide the grid; a shut
  span is skipped by jumping to the calendar's next open and taking the
  first grid instant at or after it, which keeps the grid where it was.
* **Agreement with the gate.** The calendar is passed in and honoured
  here, so a cadence never proposes an instant the gate would refuse — with
  one deliberate exception: ``at-times`` anchored on the session ``open`` or
  ``close`` fires at boundary offsets exactly (a zero offset before the
  close IS the flatten-at-the-bell tick, an instant the calendar calls
  shut), so boundary-anchored times are not filtered by ``is_open``;
  wall-clock times are, and are read in the CALENDAR's zone.

:class:`Overrun` is a strategy beside the cadences, not one of them: given
the ticks that fell due while the loop was busy it says which one to run
and which the tick record must name as absorbed, dispatching
``vocab.OVERRUN_POLICIES`` through a table of strategy objects. A tick is
never run twice and never concurrently (§5.13 does the serialising; this
module only decides).

Every knob is a document value validated at construction; the defaults and
bounds are the named module constants below, read by ``validate_params``
and the run alike. ``compose.py`` resolves ``schedule.cadence.uses``
through :data:`CADENCE_KINDS` and builds ``Overrun(document.schedule.overrun)``
from the block directly (it has no ``uses``).
"""

import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from datetime import time as time_of_day
from zoneinfo import ZoneInfo

from dskit.pipeline.node import check_int_param
from dskit.production.base import ProductionError, Registry, reject_unknown_params
from dskit.production.vocab import AT_TIMES_RELATIVE, OVERRUN_POLICIES

__all__ = [
    "CADENCE_KINDS",
    "DEFAULT_ANCHOR_MS",
    "DEFAULT_MAX_LAG_MS",
    "DEFAULT_OVERRUN_POLICY",
    "DEFAULT_PUBLISH_DELAY_MS",
    "MAX_SHUT_SPANS",
    "MIN_ANCHOR_MS",
    "MIN_MAX_LAG_MS",
    "MIN_OFFSET_S",
    "MIN_PERIOD_MS",
    "MIN_POLL_MS",
    "MIN_PUBLISH_DELAY_MS",
    "AlignedBar",
    "AtTimes",
    "Cadence",
    "FixedInterval",
    "OnData",
    "Overrun",
]

#: The finest grid a cadence may run: ``period_ms`` and ``bar_ms`` floor.
MIN_PERIOD_MS = 1000

#: ``anchor_ms`` floor (an epoch instant) and its default (the epoch).
MIN_ANCHOR_MS = 0
DEFAULT_ANCHOR_MS = 0

#: ``publish_delay_ms`` floor and default: publish at the bar boundary.
MIN_PUBLISH_DELAY_MS = 0
DEFAULT_PUBLISH_DELAY_MS = 0

#: ``poll_ms`` floor for ``on-data``.
MIN_POLL_MS = 100

#: Floor of a boundary-anchored ``at-times`` offset (seconds).
MIN_OFFSET_S = 0

#: How many shut spans (or sessions, or days) a cadence skips before
#: refusing: a grid that never lands inside an open span is a
#: misconfiguration, not a long wait.
MAX_SHUT_SPANS = 64

#: ``Overrun`` defaults and floor: coalesce, and drop everything when even
#: the freshest due tick is older than this.
DEFAULT_OVERRUN_POLICY = "coalesce"
DEFAULT_MAX_LAG_MS = 30_000
MIN_MAX_LAG_MS = 0

_MS_PER_S = 1000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_MS = timedelta(milliseconds=1)
_ONE_DAY = timedelta(days=1)
_HHMM = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])\Z")
_NOTES = ("notes",)


def _to_ms(stamp):
    """Return an aware datetime as epoch milliseconds (exact integer arithmetic)."""
    return (stamp - _EPOCH) // _ONE_MS


def _local(ms, zone):
    """Return the aware local datetime of an epoch-ms instant in ``zone``."""
    return (_EPOCH + ms * _ONE_MS).astimezone(zone)


class _Configured(ABC):
    """``cls(params)`` construction: default-deny over ``_PARAMS``, validate, configure."""

    _PARAMS = ()

    def __init__(self, params=None):
        params = dict(params or {})
        problems = self.validate_params(params)
        if problems:
            raise ProductionError(problems)
        self._configure(params)

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when it is acceptable.

        The base refuses any key outside ``_PARAMS`` and ``notes``;
        subclasses extend the list with their own checks and never raise.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            Accumulated problems, each naming the offending key.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + _NOTES)
        return problems

    def _configure(self, params):
        """Read validated params; the base has none to read."""


class Cadence(_Configured):
    """The scheduling seam: when does this process next want a tick (§5.1).

    Parameters
    ----------
    params : dict, optional
        The ``{uses, params}`` site's ``params``; default-deny over the
        subclass's ``_PARAMS`` plus ``notes``. ``None`` means ``{}``.

    Examples
    --------
    A cadence that ticks on every second the calendar is open::

        class EverySecond(Cadence):
            def next_tick(self, after_ms, calendar):
                at = (after_ms // 1000 + 1) * 1000
                return at if calendar.is_open(at) else calendar.next_open(at)

        EverySecond({}).next_tick(1_500, AlwaysOpen({}))  # 2000
    """

    @abstractmethod
    def next_tick(self, after_ms, calendar):
        """Return the first tick instant strictly after ``after_ms`` the calendar allows.

        Parameters
        ----------
        after_ms : int
            Epoch milliseconds; the returned instant is greater than this.
        calendar : Calendar
            The gate to agree with: ``is_open`` / ``next_open`` / ``window``
            / ``tz_name`` are what a cadence may ask of it.

        Returns
        -------
        int or None
            The next instant to wake at, or ``None`` when the calendar
            never reopens.

        Raises
        ------
        ProductionError
            If :data:`MAX_SHUT_SPANS` open spans held no instant this
            cadence could use — a grid that never meets the calendar.
        """


class _Grid(Cadence):
    """A drift-free grid ``offset + k * period``; subclasses name the knobs."""

    def next_tick(self, after_ms, calendar):
        """Return the first open grid instant strictly after ``after_ms``.

        Parameters
        ----------
        after_ms : int
            Epoch milliseconds.
        calendar : Calendar
            The gate; shut spans are skipped without moving the grid.

        Returns
        -------
        int or None
            The next grid instant the calendar is open at, or ``None`` when
            it never reopens.

        Raises
        ------
        ProductionError
            If :data:`MAX_SHUT_SPANS` open spans held no grid instant.
        """
        at = self._grid_after(after_ms)
        for _ in range(MAX_SHUT_SPANS):
            if calendar.is_open(at):
                return at
            opens = calendar.next_open(at)
            if opens is None:
                return None
            at = self._grid_at_or_after(opens)
        raise ProductionError(
            [
                f"{type(self).__name__}: no grid instant fell inside the next "
                f"{MAX_SHUT_SPANS} open spans after {after_ms}"
            ]
        )

    def _grid_after(self, at_ms):
        """Return the first grid instant strictly after ``at_ms``."""
        steps = (at_ms - self._offset) // self._period + 1
        return self._offset + steps * self._period

    def _grid_at_or_after(self, at_ms):
        """Return the first grid instant at or after ``at_ms``."""
        steps = -((self._offset - at_ms) // self._period)
        return self._offset + steps * self._period


class FixedInterval(_Grid):
    """Ticks at ``anchor_ms + k * period_ms``, whatever the last handler took.

    Parameters
    ----------
    params : dict
        ``period_ms`` (int >= ``MIN_PERIOD_MS``, required); ``anchor_ms``
        (int >= ``MIN_ANCHOR_MS``, default ``DEFAULT_ANCHOR_MS`` — the
        epoch, so a 60 s period ticks on the minute).

    Examples
    --------
    A minute grid anchored one second past the epoch::

        cadence = FixedInterval({"period_ms": 60_000, "anchor_ms": 1_000})
        cadence.next_tick(1_000, AlwaysOpen({}))  # 61000
        cadence.next_tick(61_000, AlwaysOpen({}))  # 121000
    """

    _PARAMS = ("period_ms", "anchor_ms")

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when acceptable.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            Accumulated problems, each naming the offending key.
        """
        problems = super().validate_params(params)
        check_int_param(problems, "period_ms", params.get("period_ms"), ge=MIN_PERIOD_MS)
        check_int_param(
            problems, "anchor_ms", params.get("anchor_ms", DEFAULT_ANCHOR_MS), ge=MIN_ANCHOR_MS
        )
        return problems

    def _configure(self, params):
        """Take the period and the anchor as the grid."""
        self._period = int(params["period_ms"])
        self._offset = int(params.get("anchor_ms", DEFAULT_ANCHOR_MS))


class AlignedBar(_Grid):
    """Ticks ``publish_delay_ms`` after every epoch-aligned bar boundary.

    Parameters
    ----------
    params : dict
        ``bar_ms`` (int >= ``MIN_PERIOD_MS``, required): bars are
        ``k * bar_ms`` from the epoch, never from the first call;
        ``publish_delay_ms`` (int >= ``MIN_PUBLISH_DELAY_MS``, default
        ``DEFAULT_PUBLISH_DELAY_MS``): how long after the boundary the bar
        is published and the tick fires.

    Examples
    --------
    One-minute bars published five seconds after they close::

        cadence = AlignedBar({"bar_ms": 60_000, "publish_delay_ms": 5_000})
        cadence.next_tick(1_773_050_403_000, AlwaysOpen({}))  # 1773050405000
    """

    _PARAMS = ("bar_ms", "publish_delay_ms")

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when acceptable.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            Accumulated problems, each naming the offending key.
        """
        problems = super().validate_params(params)
        check_int_param(problems, "bar_ms", params.get("bar_ms"), ge=MIN_PERIOD_MS)
        check_int_param(
            problems,
            "publish_delay_ms",
            params.get("publish_delay_ms", DEFAULT_PUBLISH_DELAY_MS),
            ge=MIN_PUBLISH_DELAY_MS,
        )
        return problems

    def _configure(self, params):
        """Take the bar as the period and the publish delay as the grid offset."""
        self._period = int(params["bar_ms"])
        self._offset = int(params.get("publish_delay_ms", DEFAULT_PUBLISH_DELAY_MS))


class _Anchor(ABC):
    """What an ``at-times`` ``relative`` means: how times are spelt and placed."""

    @abstractmethod
    def check_times(self, problems, times):
        """Append a problem for every time not spelt the way this anchor reads it."""

    @abstractmethod
    def next_tick(self, after_ms, times, calendar):
        """Return the first configured instant strictly after ``after_ms``, or ``None``."""


class _BoundaryAnchor(_Anchor):
    """Second offsets from a session boundary; fires at the boundary exactly, unfiltered."""

    def check_times(self, problems, times):
        """Require ints >= ``MIN_OFFSET_S``."""
        for index, value in enumerate(times):
            check_int_param(problems, f"times[{index}]", value, ge=MIN_OFFSET_S)

    def next_tick(self, after_ms, times, calendar):
        """Walk sessions via ``calendar.window("session", …)`` until an offset is due."""
        cursor = after_ms
        for _ in range(MAX_SHUT_SPANS):
            if not calendar.is_open(cursor) and calendar.next_open(cursor) is None:
                return None
            start, end = calendar.window("session", cursor)
            due = [at for at in (self._instant(start, end, s) for s in times) if at > after_ms]
            if due:
                return min(due)
            cursor = end
        raise ProductionError(
            [
                f"at-times: no configured offset fell after {after_ms} within the "
                f"next {MAX_SHUT_SPANS} sessions"
            ]
        )

    @abstractmethod
    def _instant(self, start_ms, end_ms, offset_s):
        """Place one offset against the session bounds."""


class _OpenAnchor(_BoundaryAnchor):
    """Offsets AFTER the effective session open."""

    def _instant(self, start_ms, end_ms, offset_s):
        """Return the open plus the offset."""
        return start_ms + int(offset_s) * _MS_PER_S


class _CloseAnchor(_BoundaryAnchor):
    """Offsets BEFORE the effective session close."""

    def _instant(self, start_ms, end_ms, offset_s):
        """Return the close less the offset."""
        return end_ms - int(offset_s) * _MS_PER_S


class _ClockAnchor(_Anchor):
    """Wall-clock ``HH:MM`` times in the calendar's zone, only where it is open."""

    def check_times(self, problems, times):
        """Require ``HH:MM`` strings."""
        for index, value in enumerate(times):
            if not isinstance(value, str) or not _HHMM.match(value):
                problems.append(
                    f"times[{index}] must be 'HH:MM' (00:00-23:59) for relative "
                    f"'clock', got {value!r}"
                )

    def next_tick(self, after_ms, times, calendar):
        """Walk local days in ``calendar.tz_name``, jumping past shut spans."""
        zone = ZoneInfo(calendar.tz_name)
        walls = [time_of_day(int(text[:2]), int(text[3:])) for text in times]
        day = _local(after_ms, zone).date()
        for _ in range(MAX_SHUT_SPANS):
            instants = sorted(_to_ms(datetime.combine(day, wall, tzinfo=zone)) for wall in walls)
            for at in instants:
                if at > after_ms and calendar.is_open(at):
                    return at
            opens = calendar.next_open(max(instants[-1], after_ms))
            if opens is None:
                return None
            day = max(day + _ONE_DAY, _local(opens, zone).date())
        raise ProductionError(
            [
                f"at-times: no configured wall time fell inside an open span within "
                f"the next {MAX_SHUT_SPANS} days after {after_ms}"
            ]
        )


class AtTimes(Cadence):
    """Ticks at configured times: offsets from the open or close, or wall-clock times.

    ``relative`` selects an anchor strategy through a table keyed by
    ``vocab.AT_TIMES_RELATIVE``: ``open`` / ``close`` take integer second
    offsets after the effective session open / before the effective close
    (with buffers applied, so a zero ``close`` offset fires when the
    process must be done, not at the bell) and fire at those instants
    exactly, even where the calendar is shut; ``clock`` takes ``HH:MM``
    strings read in ``calendar.tz_name`` and fires only where the calendar
    is open. The anchor is the session under way when one is, else the
    next.

    Parameters
    ----------
    params : dict
        ``times`` (non-empty list, required): ints >= ``MIN_OFFSET_S`` for
        ``open`` / ``close``, ``HH:MM`` strings for ``clock``; ``relative``
        (one of ``AT_TIMES_RELATIVE``, required).

    Examples
    --------
    Five minutes after each session open, on a 09:30-16:00 UTC weekday calendar::

        calendar = WeeklySessions({"tz": "UTC", "sessions": [
            {"days": ["mon", "tue", "wed", "thu", "fri"],
             "open": "09:30", "close": "16:00"}]})
        cadence = AtTimes({"times": [300], "relative": "open"})
        cadence.next_tick(1_773_046_800_000, calendar)  # 1773048900000 (09:35Z)
    """

    _PARAMS = ("times", "relative")
    _ANCHORS = {"open": _OpenAnchor(), "close": _CloseAnchor(), "clock": _ClockAnchor()}

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when acceptable.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            Accumulated problems, each naming the offending key.
        """
        problems = super().validate_params(params)
        relative = params.get("relative")
        anchor = cls._ANCHORS.get(relative) if isinstance(relative, str) else None
        if anchor is None:
            problems.append(f"relative must be one of {list(AT_TIMES_RELATIVE)}, got {relative!r}")
        times = params.get("times")
        if not isinstance(times, list) or not times:
            problems.append("times must be a non-empty list")
        elif anchor is not None:
            anchor.check_times(problems, times)
        return problems

    def _configure(self, params):
        """Pick the anchor strategy and keep the times in order."""
        self._anchor = self._ANCHORS[params["relative"]]
        self._times = tuple(sorted(params["times"]))

    def next_tick(self, after_ms, calendar):
        """Return the first configured instant strictly after ``after_ms``.

        Parameters
        ----------
        after_ms : int
            Epoch milliseconds.
        calendar : Calendar
            Supplies the session windows (boundary anchors), or the zone
            and the gate (wall-clock anchor).

        Returns
        -------
        int or None
            The next instant, or ``None`` when the calendar never reopens.

        Raises
        ------
        ProductionError
            If :data:`MAX_SHUT_SPANS` sessions or days held no usable
            instant.
        """
        return self._anchor.next_tick(after_ms, self._times, calendar)


class OnData(Cadence):
    """Polls: one ``poll_ms`` after the instant asked about, or at the next open.

    Parameters
    ----------
    params : dict
        ``poll_ms`` (int >= ``MIN_POLL_MS``, required).

    Examples
    --------
    A quarter-second poll::

        cadence = OnData({"poll_ms": 250})
        cadence.next_tick(1_000_000, AlwaysOpen({}))  # 1000250
    """

    _PARAMS = ("poll_ms",)

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when acceptable.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            Accumulated problems, each naming the offending key.
        """
        problems = super().validate_params(params)
        check_int_param(problems, "poll_ms", params.get("poll_ms"), ge=MIN_POLL_MS)
        return problems

    def _configure(self, params):
        """Take the poll period."""
        self._poll_ms = int(params["poll_ms"])

    def next_tick(self, after_ms, calendar):
        """Return ``after_ms + poll_ms`` if open there, else the calendar's next open.

        Parameters
        ----------
        after_ms : int
            Epoch milliseconds.
        calendar : Calendar
            The gate.

        Returns
        -------
        int or None
            The next poll instant, or ``None`` when the calendar never
            reopens.
        """
        at = after_ms + self._poll_ms
        return at if calendar.is_open(at) else calendar.next_open(at)


class _Policy(ABC):
    """One ``vocab.OVERRUN_POLICIES`` member: which due tick runs, which are absorbed."""

    @abstractmethod
    def resolve(self, due, now_ms, max_lag_ms):
        """Return ``(tick_at or None, absorbed)`` over ascending non-empty ``due``."""


class _Skip(_Policy):
    """Run the freshest due tick however late it is; the rest are absorbed."""

    def resolve(self, due, now_ms, max_lag_ms):
        """Return the latest tick and everything before it."""
        return (due[-1], due[:-1])


class _Coalesce(_Policy):
    """Run the freshest due tick unless even it is past ``max_lag_ms``; then run nothing."""

    def resolve(self, due, now_ms, max_lag_ms):
        """Return the latest tick, or ``None`` with everything absorbed when too late."""
        if now_ms - due[-1] > max_lag_ms:
            return (None, due)
        return (due[-1], due[:-1])


class _Queue(_Policy):
    """Run the oldest due tick; nothing is absorbed, the rest stay due."""

    def resolve(self, due, now_ms, max_lag_ms):
        """Return the earliest tick and an empty absorbed tuple."""
        return (due[0], ())


class Overrun(_Configured):
    """What the loop does with ticks that fell due while it was busy (§5.1).

    ``policy`` selects a strategy through a table keyed by
    ``vocab.OVERRUN_POLICIES``: ``coalesce`` (the default) runs the freshest
    due tick and names the older ones as absorbed, or runs nothing when even
    the freshest is more than ``max_lag_ms`` late (equal is still run);
    ``skip`` runs the freshest whatever the lag; ``queue`` runs the oldest
    and absorbs none, so the rest come back next time. A tick is never run
    twice.

    Parameters
    ----------
    params : dict, optional
        ``policy`` (one of ``OVERRUN_POLICIES``, default
        ``DEFAULT_OVERRUN_POLICY``); ``max_lag_ms`` (int >=
        ``MIN_MAX_LAG_MS``, default ``DEFAULT_MAX_LAG_MS``).

    Attributes
    ----------
    policy : str
        The selected policy name.
    max_lag_ms : int
        The lag bound.

    Examples
    --------
    Three ticks fell due; coalesce runs the last and names the two it absorbed::

        overrun = Overrun({"policy": "coalesce", "max_lag_ms": 30_000})
        overrun.resolve((1_000_000, 1_060_000, 1_120_000), 1_140_000)
        # -> (1120000, (1000000, 1060000))
        overrun.resolve((1_000_000,), 2_000_000)  # -> (None, (1000000,))
    """

    _PARAMS = ("policy", "max_lag_ms")
    _POLICIES = {"skip": _Skip(), "coalesce": _Coalesce(), "queue": _Queue()}

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when acceptable.

        Parameters
        ----------
        params : dict
            The ``schedule.overrun`` block as written in the document.

        Returns
        -------
        list of str
            Accumulated problems, each naming the offending key.
        """
        problems = super().validate_params(params)
        policy = params.get("policy", DEFAULT_OVERRUN_POLICY)
        if not isinstance(policy, str) or policy not in OVERRUN_POLICIES:
            problems.append(f"policy must be one of {list(OVERRUN_POLICIES)}, got {policy!r}")
        check_int_param(
            problems, "max_lag_ms", params.get("max_lag_ms", DEFAULT_MAX_LAG_MS), ge=MIN_MAX_LAG_MS
        )
        return problems

    def _configure(self, params):
        """Expose the policy and lag bound; pick the strategy object."""
        self.policy = params.get("policy", DEFAULT_OVERRUN_POLICY)
        self.max_lag_ms = int(params.get("max_lag_ms", DEFAULT_MAX_LAG_MS))
        self._strategy = self._POLICIES[self.policy]

    def resolve(self, due_ticks, now_ms):
        """Decide which due tick runs now and which the tick record names as absorbed.

        Parameters
        ----------
        due_ticks : iterable of int
            The ``tick_at`` instants that fell due, all at or before
            ``now_ms``; sorted here, so order is not relied upon.
        now_ms : int
            The current instant.

        Returns
        -------
        tuple
            ``(tick_at, absorbed)``: the instant to run — ``None`` when the
            policy runs nothing — and an ascending tuple of the due
            instants that will never run because of this decision.

        Raises
        ------
        ProductionError
            If any tick in ``due_ticks`` is later than ``now_ms``: a tick
            that is not yet due cannot be overrun.
        """
        due = tuple(sorted(due_ticks))
        if not due:
            return (None, ())
        early = [at for at in due if at > now_ms]
        if early:
            raise ProductionError(
                [f"overrun: tick(s) {early} are not due at {now_ms}"]
            )
        return self._strategy.resolve(due, now_ms, self.max_lag_ms)


CADENCE_KINDS = Registry("cadence", Cadence)
CADENCE_KINDS.register("fixed-interval", FixedInterval)
CADENCE_KINDS.register("aligned-bar", AlignedBar)
CADENCE_KINDS.register("at-times", AtTimes)
CADENCE_KINDS.register("on-data", OnData)
