"""When the process is allowed to be awake: calendars, and the windows guards resolve against.

A :class:`Calendar` is the gate every tick passes through and the object a
``{"calendar": "session"}`` guard window resolves through (D7, §5.1, §5.5).
Four kinds ship — ``always-open``, ``weekly-sessions``, ``event-window`` and
``composite`` (the intersection) — behind one ABC of three abstract hooks,
``is_open(ms)``, ``next_open(after_ms)`` and ``next_close(after_ms)``, plus
one concrete ``window(kind, at_ms)`` that turns a
``vocab.CALENDAR_WINDOWS`` name into ``[start, end)`` epoch-ms bounds.

Every interval here is half-open — open at ``start``, shut at ``end`` — and
``next_open`` / ``next_close`` return the next *transition* strictly after
the instant asked about, or ``None`` when there is none. Sessions are
written in local wall time (``HH:MM`` in an IANA zone) and every comparison
is done in UTC milliseconds after ``zoneinfo`` has placed each boundary on
its own date, which is what keeps 09:30 New York at 09:30 across both
daylight-saving transitions. A boundary that does not exist on a
transition date (02:30 on the spring-forward Sunday) is refused when the
calendar is built, and again — as a backstop for a date outside the
validation scan — when the loop reaches that day; it is never silently
shifted. In the fall-back repeat the first pass (``fold=0``) is taken.

``after_open_s`` / ``before_close_s`` shrink the *effective* session at
both ends; ``blackouts`` (UTC instants) shut an interval inside a session
without changing where the session window starts, so a day-loss guard
keeps accumulating across a maintenance break.

Constructed as ``cls(params)`` with default-deny over ``_PARAMS``
(``notes`` allowed beside them, as everywhere in §4.1); ``compose.py``
resolves ``schedule.calendar.uses`` through :data:`CALENDAR_KINDS`.
"""

import re
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta, timezone
from datetime import time as time_of_day
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dskit.pipeline.node import check_int_param
from dskit.production.base import (
    ProductionError,
    Registry,
    parse_utc_ms,
    reject_unknown_params,
)
from dskit.production.vocab import CALENDAR_WINDOWS

__all__ = [
    "CALENDAR_KINDS",
    "DAY_NAMES",
    "GAP_SCAN_YEARS",
    "MAX_COMPOSITE_ROUNDS",
    "MIN_BUFFER_S",
    "MIN_INSTANT_MS",
    "MIN_LEAD_MS",
    "AlwaysOpen",
    "Calendar",
    "Composite",
    "EventWindow",
    "WeeklySessions",
]

#: Session day names, indexed like ``date.weekday()`` (Monday is 0).
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: First and last year (inclusive) the DST-gap validation scans for zone
#: transitions. Fixed rather than "now" so a document's validity — and
#: hence its identity — never depends on when it is loaded; a gap on a date
#: outside the scan is still refused at run time by the day builder.
GAP_SCAN_YEARS = (2024, 2030)

#: Lower bound of ``after_open_s`` / ``before_close_s`` (seconds).
MIN_BUFFER_S = 0

#: Lower bound of an ``EventWindow``'s ``lead_ms``.
MIN_LEAD_MS = 0

#: Lower bound of an epoch-ms instant in a params block.
MIN_INSTANT_MS = 0

#: How many alignment rounds a :class:`Composite` tries before refusing:
#: each round jumps to the latest member's next open, so members whose open
#: sets never meet are refused rather than searched forever.
MAX_COMPOSITE_ROUNDS = 64

_MS_PER_S = 1000
_UTC = timezone.utc
_EPOCH = datetime(1970, 1, 1, tzinfo=_UTC)
_ONE_MS = timedelta(milliseconds=1)
_ONE_DAY = timedelta(days=1)
_HHMM = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])\Z")
_YMD = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_NOTES = ("notes",)


def _to_ms(stamp):
    """Return an aware datetime as epoch milliseconds (exact integer arithmetic)."""
    return (stamp - _EPOCH) // _ONE_MS


def _local(ms, zone):
    """Return the aware local datetime of an epoch-ms instant in ``zone``."""
    return (_EPOCH + ms * _ONE_MS).astimezone(zone)


def _hhmm(text):
    """Return a validated ``HH:MM`` string as a ``time``."""
    return time_of_day(int(text[:2]), int(text[3:]))


def _seconds(text):
    """Return a validated ``HH:MM`` string as seconds after midnight."""
    return int(text[:2]) * 3600 + int(text[3:]) * 60


def _exists(zone, day, wall):
    """Say whether the wall time exists on ``day`` in ``zone`` (not in a DST gap)."""
    naive = datetime.combine(day, wall)
    local = naive.replace(tzinfo=zone)
    return local.astimezone(_UTC).astimezone(zone).replace(tzinfo=None) == naive


def _transition_days(zone):
    """Return the dates in ``GAP_SCAN_YEARS`` around which the zone's offset changes."""
    first, last = GAP_SCAN_YEARS
    day, stop = date(first, 1, 1), date(last, 12, 31)
    offset = datetime.combine(day, time_of_day(), tzinfo=zone).utcoffset()
    out = []
    while day < stop:
        following = day + _ONE_DAY
        next_offset = datetime.combine(following, time_of_day(), tzinfo=zone).utcoffset()
        if next_offset != offset:
            out.extend((day, following))
        day, offset = following, next_offset
    return tuple(out)


def _merged(spans):
    """Return sorted ``(start, end)`` spans with overlapping or touching ones joined."""
    out = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(end, out[-1][1]))
        else:
            out.append((start, end))
    return tuple(out)


def _cut(span, shut_from, shut_until):
    """Return the pieces of ``span`` left once ``[shut_from, shut_until)`` is removed."""
    start, end = span
    if shut_until <= start or shut_from >= end:
        return (span,)
    pieces = []
    if start < shut_from:
        pieces.append((start, shut_from))
    if shut_until < end:
        pieces.append((shut_until, end))
    return tuple(pieces)


class Calendar(ABC):
    """The gate a tick passes through, and the resolver of calendar windows (§5.1).

    Subclasses supply the three abstract hooks; ``window`` is concrete and
    dispatches a ``vocab.CALENDAR_WINDOWS`` name to a ``_window_<kind>``
    hook through a table built from the vocabulary, so a kind outside it
    refuses and none is ever compared in code. The default hooks give a
    calendar without sessions a ``day`` window (the local day in
    ``tz_name``), treat the day as its ``session``, and offer no ``event``
    window; subclasses override what they know better.

    Parameters
    ----------
    params : dict, optional
        The ``{uses, params}`` site's ``params``; default-deny over the
        subclass's ``_PARAMS`` plus ``notes``. ``None`` means ``{}``.

    Attributes
    ----------
    tz_name : str
        The IANA zone the calendar's wall times are written in; ``"UTC"``
        unless a subclass sets it. Cadences read it to place ``HH:MM``
        times.

    Examples
    --------
    A calendar open on even seconds only, complete enough to construct::

        class EvenSeconds(Calendar):
            def is_open(self, ms):
                return (ms // 1000) % 2 == 0

            def next_open(self, after_ms):
                return (after_ms // 2000 + 1) * 2000

            def next_close(self, after_ms):
                return (after_ms // 2000) * 2000 + 1000 + (2000 if after_ms % 2000 >= 1000 else 0)

        cal = EvenSeconds({})
        cal.is_open(2_500)  # True
        cal.window("day", 0)  # -> (0, 86400000)
    """

    _PARAMS = ()
    _WINDOW_HOOKS = {name: f"_window_{name}" for name in CALENDAR_WINDOWS}
    tz_name = "UTC"

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

    @abstractmethod
    def is_open(self, ms):
        """Say whether the process may act at ``ms``.

        Parameters
        ----------
        ms : int
            Epoch milliseconds.

        Returns
        -------
        bool
            ``True`` inside an open interval (``start <= ms < end``).
        """

    @abstractmethod
    def next_open(self, after_ms):
        """Return the next shut-to-open transition strictly after ``after_ms``.

        Parameters
        ----------
        after_ms : int
            Epoch milliseconds; the returned instant is greater than this.

        Returns
        -------
        int or None
            The instant the calendar next opens, or ``None`` when it never
            does again.
        """

    @abstractmethod
    def next_close(self, after_ms):
        """Return the next open-to-shut transition strictly after ``after_ms``.

        Parameters
        ----------
        after_ms : int
            Epoch milliseconds; the returned instant is greater than this.

        Returns
        -------
        int or None
            The instant the calendar next shuts, or ``None`` when it never
            does again.
        """

    def window(self, kind, at_ms):
        """Return the ``[start, end)`` bounds of the ``kind`` window around ``at_ms``.

        The window containing ``at_ms``, else the next one to start after
        it — the contract guards rely on when they resolve
        ``{"calendar": "session"}`` at a tick.

        Parameters
        ----------
        kind : str
            A ``vocab.CALENDAR_WINDOWS`` member: ``session``, ``day`` or
            ``event``.
        at_ms : int
            The instant to anchor on, epoch milliseconds.

        Returns
        -------
        tuple of int
            ``(start_ms, end_ms)``, half-open.

        Raises
        ------
        ProductionError
            If ``kind`` is outside the vocabulary, or this calendar has no
            such window at ``at_ms`` (a calendar without events asked for
            ``event``; an event already over).
        """
        hook = self._WINDOW_HOOKS.get(kind) if isinstance(kind, str) else None
        if hook is None:
            raise ProductionError(
                [f"calendar: unknown window kind {kind!r}; one of {list(CALENDAR_WINDOWS)}"]
            )
        bounds = getattr(self, hook)(at_ms)
        if bounds is None:
            raise ProductionError(
                [f"{type(self).__name__} has no {kind} window at {at_ms}"]
            )
        return bounds

    def _window_day(self, at_ms):
        """Return the local day of ``at_ms`` in ``tz_name`` as ``[midnight, midnight)``."""
        zone = ZoneInfo(self.tz_name)
        day = _local(at_ms, zone).date()
        return (
            _to_ms(datetime.combine(day, time_of_day(), tzinfo=zone)),
            _to_ms(datetime.combine(day + _ONE_DAY, time_of_day(), tzinfo=zone)),
        )

    def _window_session(self, at_ms):
        """Return the day as the session of a calendar that has none of its own."""
        return self._window_day(at_ms)

    def _window_event(self, at_ms):
        """Return ``None``: a calendar has no event window unless it says so."""
        return None


class AlwaysOpen(Calendar):
    """Open at every instant; never transitions; windows by UTC day.

    Parameters
    ----------
    params : dict, optional
        Must be empty (``notes`` aside); any knob refuses.

    Examples
    --------
    The calendar a backtest or a 24/7 venue uses::

        cal = AlwaysOpen({})
        cal.is_open(0)  # True
        cal.next_open(0) is None  # True
        cal.window("session", 1_767_225_600_000)
        # -> (1767225600000, 1767312000000)
    """

    def is_open(self, ms):
        """Return ``True`` at every instant.

        Parameters
        ----------
        ms : int
            Ignored.

        Returns
        -------
        bool
            Always ``True``.
        """
        return True

    def next_open(self, after_ms):
        """Return ``None``: an always-open calendar never opens again.

        Parameters
        ----------
        after_ms : int
            Ignored.

        Returns
        -------
        None
            There is no transition.
        """
        return None

    def next_close(self, after_ms):
        """Return ``None``: an always-open calendar never shuts.

        Parameters
        ----------
        after_ms : int
            Ignored.

        Returns
        -------
        None
            There is no transition.
        """
        return None


class WeeklySessions(Calendar):
    """Local-time sessions by weekday, with holidays, special closes, blackouts and buffers.

    Every knob is validated at construction and every problem accumulated
    into one ``ProductionError``: an unknown zone or weekday, a boundary not
    spelt ``HH:MM``, a close at or before its open, buffers that meet or
    cross, an unsorted or repeated holiday, a naive blackout instant, and a
    boundary that falls in a daylight-saving gap on any transition date of
    :data:`GAP_SCAN_YEARS`.

    Parameters
    ----------
    params : dict
        ``tz`` (str, IANA zone, required); ``sessions`` (non-empty list of
        ``{"days": [...], "open": "HH:MM", "close": "HH:MM"}``, required);
        ``holidays`` (sorted unique ``YYYY-MM-DD`` strings; the whole day is
        shut); ``special_closes`` (list of ``{"date", "close"}``: that day
        shuts at ``close`` instead); ``blackouts`` (list of
        ``{"from", "until"}`` UTC ISO instants shut inside a session);
        ``after_open_s`` / ``before_close_s`` (ints >= ``MIN_BUFFER_S``,
        default 0) shrinking the effective session at each end.

    Attributes
    ----------
    tz_name : str
        The zone the sessions are written in.

    Examples
    --------
    The §4.1 calendar: 09:30-16:00 New York on weekdays, one holiday,
    a minute after the open and two before the close held back::

        cal = WeeklySessions({
            "tz": "America/New_York",
            "sessions": [{"days": ["mon", "tue", "wed", "thu", "fri"],
                          "open": "09:30", "close": "16:00"}],
            "holidays": ["2026-11-26"],
            "after_open_s": 60,
            "before_close_s": 120,
        })
        cal.is_open(1_773_063_060_000)  # True  (2026-03-09 09:31 EDT)
        cal.window("session", 1_773_063_060_000)
        # -> (1773063060000, 1773086280000)
    """

    _PARAMS = (
        "tz",
        "sessions",
        "holidays",
        "special_closes",
        "blackouts",
        "after_open_s",
        "before_close_s",
    )
    _SESSION_KEYS = ("days", "open", "close")
    _SPECIAL_CLOSE_KEYS = ("date", "close")
    _BLACKOUT_KEYS = ("from", "until")

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
        zone = cls._check_zone(problems, params.get("tz"))
        buffers = cls._check_buffers(problems, params)
        cls._check_sessions(problems, params.get("sessions"), zone, buffers)
        cls._check_holidays(problems, params.get("holidays", []))
        cls._check_special_closes(problems, params.get("special_closes", []), zone)
        cls._check_blackouts(problems, params.get("blackouts", []))
        return problems

    @classmethod
    def _check_zone(cls, problems, tz):
        """Append problems with ``tz``; return its ``ZoneInfo`` or ``None``."""
        if not isinstance(tz, str) or not tz:
            problems.append("tz is required: an IANA zone name such as 'UTC'")
            return None
        try:
            return ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            problems.append(f"tz {tz!r} is not a known IANA zone")
            return None

    @classmethod
    def _check_buffers(cls, problems, params):
        """Append problems with the buffers; return ``(after_s, before_s)`` or ``None``."""
        values = []
        for key in ("after_open_s", "before_close_s"):
            value = params.get(key, MIN_BUFFER_S)
            check_int_param(problems, key, value, ge=MIN_BUFFER_S)
            values.append(value)
        if all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            return tuple(values)
        return None

    @classmethod
    def _check_sessions(cls, problems, sessions, zone, buffers):
        """Append every problem with the ``sessions`` list."""
        if not isinstance(sessions, list) or not sessions:
            problems.append("sessions must be a non-empty list of {days, open, close}")
            return
        for index, entry in enumerate(sessions):
            path = f"sessions[{index}]"
            if not isinstance(entry, dict):
                problems.append(f"{path} must be an object")
                continue
            reject_unknown_params(problems, entry, cls._SESSION_KEYS + _NOTES)
            days = cls._check_days(problems, path, entry.get("days"))
            bounds = cls._check_bounds(problems, path, entry.get("open"), entry.get("close"))
            if bounds is None:
                continue
            opened, closed = bounds
            if buffers is not None and sum(buffers) >= _seconds(closed) - _seconds(opened):
                problems.append(
                    f"{path}: after_open_s + before_close_s ({sum(buffers)} s) "
                    f"consume the {opened}-{closed} session"
                )
            if zone is not None:
                for key, wall in (("open", opened), ("close", closed)):
                    cls._check_gap(problems, f"{path}.{key}", zone, days, wall)

    @classmethod
    def _check_days(cls, problems, path, days):
        """Append problems with ``days``; return the valid weekday indexes."""
        if not isinstance(days, list) or not days:
            problems.append(f"{path}.days must be a non-empty list of {list(DAY_NAMES)}")
            return ()
        valid = []
        for day in days:
            if day in DAY_NAMES:
                valid.append(DAY_NAMES.index(day))
            else:
                problems.append(f"{path}.days: {day!r} is not one of {list(DAY_NAMES)}")
        return tuple(valid)

    @classmethod
    def _check_bounds(cls, problems, path, opened, closed):
        """Append problems with an open/close pair; return it when well-formed."""
        ok = True
        for key, value in (("open", opened), ("close", closed)):
            if not isinstance(value, str) or not _HHMM.match(value):
                problems.append(f"{path}.{key} must be 'HH:MM' (00:00-23:59), got {value!r}")
                ok = False
        if not ok:
            return None
        if _seconds(closed) <= _seconds(opened):
            problems.append(f"{path}: close {closed} must be after open {opened}")
            return None
        return (opened, closed)

    @classmethod
    def _check_gap(cls, problems, path, zone, weekdays, wall, dates=None):
        """Append a problem when ``wall`` falls in a DST gap on a scanned or given date."""
        candidates = _transition_days(zone) if dates is None else dates
        for day in candidates:
            if weekdays is not None and day.weekday() not in weekdays:
                continue
            if not _exists(zone, day, _hhmm(wall)):
                problems.append(
                    f"{path} {wall} does not exist in {zone.key} on {day} "
                    "(daylight-saving gap)"
                )
                return

    @classmethod
    def _check_holidays(cls, problems, holidays):
        """Append every problem with the ``holidays`` list."""
        if not isinstance(holidays, list):
            problems.append("holidays must be a list of 'YYYY-MM-DD' strings")
            return
        valid = [cls._check_date(problems, f"holidays[{i}]", h) for i, h in enumerate(holidays)]
        if all(valid) and holidays != sorted(set(holidays)):
            problems.append("holidays must be sorted and unique")

    @classmethod
    def _check_date(cls, problems, path, text):
        """Append a problem unless ``text`` is a real ``YYYY-MM-DD`` date; return the verdict."""
        if isinstance(text, str) and _YMD.match(text):
            try:
                date.fromisoformat(text)
                return True
            except ValueError:
                pass
        problems.append(f"{path} must be a 'YYYY-MM-DD' date, got {text!r}")
        return False

    @classmethod
    def _check_special_closes(cls, problems, closes, zone):
        """Append every problem with the ``special_closes`` list."""
        if not isinstance(closes, list):
            problems.append("special_closes must be a list of {date, close}")
            return
        for index, entry in enumerate(closes):
            path = f"special_closes[{index}]"
            if not isinstance(entry, dict):
                problems.append(f"{path} must be an object")
                continue
            reject_unknown_params(problems, entry, cls._SPECIAL_CLOSE_KEYS + _NOTES)
            dated = cls._check_date(problems, f"{path}.date", entry.get("date"))
            closed = entry.get("close")
            if not isinstance(closed, str) or not _HHMM.match(closed):
                problems.append(f"{path}.close must be 'HH:MM', got {closed!r}")
            elif dated and zone is not None:
                day = date.fromisoformat(entry["date"])
                cls._check_gap(problems, f"{path}.close", zone, None, closed, dates=(day,))

    @classmethod
    def _check_blackouts(cls, problems, blackouts):
        """Append every problem with the ``blackouts`` list."""
        if not isinstance(blackouts, list):
            problems.append("blackouts must be a list of {from, until}")
            return
        for index, entry in enumerate(blackouts):
            path = f"blackouts[{index}]"
            if not isinstance(entry, dict):
                problems.append(f"{path} must be an object")
                continue
            reject_unknown_params(problems, entry, cls._BLACKOUT_KEYS + _NOTES)
            bounds = []
            for key in cls._BLACKOUT_KEYS:
                try:
                    bounds.append(parse_utc_ms(entry.get(key)))
                except ProductionError as exc:
                    problems.extend(f"{path}.{key}: {p}" for p in exc.problems)
            if len(bounds) == 2 and bounds[1] <= bounds[0]:
                problems.append(f"{path}: until must be after from")

    def _configure(self, params):
        """Materialise the validated knobs as zone, parsed sessions and ms bounds."""
        self.tz_name = params["tz"]
        self._zone = ZoneInfo(self.tz_name)
        self._sessions = tuple(
            (
                frozenset(DAY_NAMES.index(d) for d in entry["days"]),
                _hhmm(entry["open"]),
                _hhmm(entry["close"]),
            )
            for entry in params["sessions"]
        )
        self._holidays = frozenset(date.fromisoformat(h) for h in params.get("holidays", ()))
        self._special_closes = {
            date.fromisoformat(entry["date"]): _hhmm(entry["close"])
            for entry in params.get("special_closes", ())
        }
        self._blackouts = tuple(
            sorted(
                (parse_utc_ms(entry["from"]), parse_utc_ms(entry["until"]))
                for entry in params.get("blackouts", ())
            )
        )
        self._after_ms = int(params.get("after_open_s", MIN_BUFFER_S)) * _MS_PER_S
        self._before_ms = int(params.get("before_close_s", MIN_BUFFER_S)) * _MS_PER_S
        exceptions = set(self._holidays) | set(self._special_closes)
        exceptions.update(_local(until, self._zone).date() for _, until in self._blackouts)
        self._last_exception = max(exceptions, default=None)

    def _local_ms(self, day, wall):
        """Return the epoch ms of ``wall`` on local ``day``, refusing a DST-gap phantom."""
        if not _exists(self._zone, day, wall):
            raise ProductionError(
                [
                    f"{wall:%H:%M} does not exist in {self.tz_name} on {day} "
                    "(daylight-saving gap)"
                ]
            )
        return _to_ms(datetime.combine(day, wall, tzinfo=self._zone))

    def _sessions_on(self, day):
        """Return the effective session spans of local ``day`` (buffers, no blackouts)."""
        if day in self._holidays:
            return ()
        cap = self._special_closes.get(day)
        spans = []
        for weekdays, opened, closed in self._sessions:
            if day.weekday() not in weekdays:
                continue
            shut = closed if cap is None or cap > closed else cap
            start = self._local_ms(day, opened) + self._after_ms
            end = self._local_ms(day, shut) - self._before_ms
            if end > start:
                spans.append((start, end))
        return _merged(spans)

    def _open_spans_on(self, day):
        """Return the open spans of local ``day``: the sessions with blackouts cut out."""
        spans = list(self._sessions_on(day))
        for shut_from, shut_until in self._blackouts:
            spans = [piece for span in spans for piece in _cut(span, shut_from, shut_until)]
        return tuple(spans)

    def _days_from(self, ms):
        """Yield local dates from the one holding ``ms`` through a regular week past the last exception."""
        day = _local(ms, self._zone).date()
        last = day if self._last_exception is None else max(day, self._last_exception)
        stop = last + timedelta(days=len(DAY_NAMES))
        while day <= stop:
            yield day
            day += _ONE_DAY

    def is_open(self, ms):
        """Say whether ``ms`` lies inside an effective session and outside every blackout.

        Parameters
        ----------
        ms : int
            Epoch milliseconds.

        Returns
        -------
        bool
            ``True`` when ``start <= ms < end`` for some open span.
        """
        day = _local(ms, self._zone).date()
        return any(start <= ms < end for start, end in self._open_spans_on(day))

    def next_open(self, after_ms):
        """Return the first open-span start strictly after ``after_ms``.

        Parameters
        ----------
        after_ms : int
            Epoch milliseconds.

        Returns
        -------
        int or None
            The next open transition — a session open, or the end of a
            blackout inside one. ``None`` only if no session day exists
            within a regular week past the last configured exception.
        """
        for day in self._days_from(after_ms):
            for start, _ in self._open_spans_on(day):
                if start > after_ms:
                    return start
        return None

    def next_close(self, after_ms):
        """Return the first open-span end strictly after ``after_ms``.

        Parameters
        ----------
        after_ms : int
            Epoch milliseconds.

        Returns
        -------
        int or None
            The next shut transition — a session close, or the start of a
            blackout inside one. ``None`` only if no session day exists
            within a regular week past the last configured exception.
        """
        for day in self._days_from(after_ms):
            for _, end in self._open_spans_on(day):
                if end > after_ms:
                    return end
        return None

    def _window_session(self, at_ms):
        """Return the effective session holding ``at_ms``, else the next one (blackouts ignored)."""
        for day in self._days_from(at_ms):
            for start, end in self._sessions_on(day):
                if at_ms < end:
                    return (start, end)
        return None


class EventWindow(Calendar):
    """Open from a lead before one event until it is over.

    Parameters
    ----------
    params : dict
        ``start_ms`` (int, epoch ms, required), ``until_ms`` (int, epoch
        ms, required, strictly after ``start_ms``), ``lead_ms`` (int >=
        ``MIN_LEAD_MS``, default 0): the calendar opens at
        ``start_ms - lead_ms`` and shuts at ``until_ms``.

    Examples
    --------
    A minute of lead before an event that lasts 200 s::

        cal = EventWindow({"start_ms": 1_000_000, "lead_ms": 60_000,
                           "until_ms": 1_200_000})
        cal.is_open(940_000)  # True
        cal.next_close(0)  # 1200000
        cal.window("event", 1_000_000)  # -> (940000, 1200000)
    """

    _PARAMS = ("start_ms", "lead_ms", "until_ms")

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
        for key in ("start_ms", "until_ms"):
            check_int_param(problems, key, params.get(key), ge=MIN_INSTANT_MS)
        check_int_param(problems, "lead_ms", params.get("lead_ms", MIN_LEAD_MS), ge=MIN_LEAD_MS)
        if not problems and params["until_ms"] <= params["start_ms"]:
            problems.append(
                f"until_ms {params['until_ms']} must be after start_ms {params['start_ms']}"
            )
        return problems

    def _configure(self, params):
        """Store the open instant (start less lead) and the shut instant."""
        self._open_ms = int(params["start_ms"]) - int(params.get("lead_ms", MIN_LEAD_MS))
        self._until_ms = int(params["until_ms"])

    def is_open(self, ms):
        """Say whether ``ms`` lies in ``[start - lead, until)``.

        Parameters
        ----------
        ms : int
            Epoch milliseconds.

        Returns
        -------
        bool
            ``True`` inside the window.
        """
        return self._open_ms <= ms < self._until_ms

    def next_open(self, after_ms):
        """Return the window's open instant when it is still ahead.

        Parameters
        ----------
        after_ms : int
            Epoch milliseconds.

        Returns
        -------
        int or None
            ``start_ms - lead_ms`` when strictly after ``after_ms``, else
            ``None`` — the event opens once.
        """
        return self._open_ms if self._open_ms > after_ms else None

    def next_close(self, after_ms):
        """Return the window's shut instant when it is still ahead.

        Parameters
        ----------
        after_ms : int
            Epoch milliseconds.

        Returns
        -------
        int or None
            ``until_ms`` when strictly after ``after_ms``, else ``None``.
        """
        return self._until_ms if self._until_ms > after_ms else None

    def _window_event(self, at_ms):
        """Return the whole window while it is not yet over; ``None`` afterwards."""
        return (self._open_ms, self._until_ms) if at_ms < self._until_ms else None

    def _window_session(self, at_ms):
        """Return the event window: an event is its own session."""
        return self._window_event(at_ms)


class Composite(Calendar):
    """The intersection of several calendars: open only where every member is.

    ``next_open`` aligns the members by repeatedly jumping to the latest
    member's next open until all are open at once (at most
    :data:`MAX_COMPOSITE_ROUNDS` rounds, then it refuses); ``next_close``
    is the earliest member close once they are. A member that never
    transitions constrains nothing. ``session`` and ``event`` windows are
    the intersection of the members' windows (members without one are
    skipped; none at all refuses); the ``day`` window is the local day in
    the first member's zone, which is also the composite's ``tz_name``.

    Parameters
    ----------
    params : dict
        ``members`` (non-empty list of ``{"uses", "params"}`` sites, each
        resolved through :data:`CALENDAR_KINDS` and validated by its own
        class; problems are reported under ``members[i]``).

    Attributes
    ----------
    tz_name : str
        The first member's zone.

    Examples
    --------
    Weekday sessions, but only while an event is live::

        cal = Composite({"members": [
            {"uses": "weekly-sessions", "params": {
                "tz": "UTC",
                "sessions": [{"days": ["mon", "tue", "wed", "thu", "fri"],
                              "open": "09:30", "close": "16:00"}]}},
            {"uses": "event-window", "params": {"start_ms": 1_773_050_400_000,
                                                "until_ms": 1_773_309_600_000}},
        ]})
        cal.tz_name  # 'UTC'
        cal.is_open(1_773_054_000_000)  # True  (2026-03-09 11:00Z)
    """

    _PARAMS = ("members",)
    _MEMBER_KEYS = ("uses", "params")

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params`` and its members; empty when acceptable.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            Accumulated problems; a member's own problems are prefixed
            ``members[i]:``.
        """
        problems = super().validate_params(params)
        members = params.get("members")
        if not isinstance(members, list) or not members:
            problems.append("members must be a non-empty list of {uses, params} sites")
            return problems
        for index, member in enumerate(members):
            path = f"members[{index}]"
            if not isinstance(member, dict):
                problems.append(f"{path} must be a {{uses, params}} object")
                continue
            reject_unknown_params(problems, member, cls._MEMBER_KEYS + _NOTES)
            try:
                member_cls = CALENDAR_KINDS.resolve(member.get("uses"))
            except ProductionError as exc:
                problems.extend(f"{path}: {p}" for p in exc.problems)
                continue
            member_params = member.get("params")
            if member_params is None:
                member_params = {}
            if not isinstance(member_params, dict):
                problems.append(f"{path}.params must be an object")
                continue
            problems.extend(f"{path}: {p}" for p in member_cls.validate_params(member_params))
        return problems

    def _configure(self, params):
        """Build the members and take the first one's zone."""
        self._members = tuple(
            CALENDAR_KINDS.resolve(member["uses"])(member.get("params"))
            for member in params["members"]
        )
        self.tz_name = self._members[0].tz_name

    def is_open(self, ms):
        """Say whether every member is open at ``ms``.

        Parameters
        ----------
        ms : int
            Epoch milliseconds.

        Returns
        -------
        bool
            ``True`` only when all members are open.
        """
        return all(member.is_open(ms) for member in self._members)

    def next_open(self, after_ms):
        """Return the first instant strictly after ``after_ms`` at which all members open.

        Parameters
        ----------
        after_ms : int
            Epoch milliseconds.

        Returns
        -------
        int or None
            The next composite open transition; ``None`` when some member
            never opens again, or when the composite is open now and never
            shuts.

        Raises
        ------
        ProductionError
            If the members' open sets fail to meet within
            :data:`MAX_COMPOSITE_ROUNDS` alignment rounds.
        """
        if self.is_open(after_ms):
            after_ms = self.next_close(after_ms)
            if after_ms is None:
                return None
            return self._first_open_at_or_after(after_ms)
        return self._first_open_at_or_after(after_ms + 1)

    def next_close(self, after_ms):
        """Return the earliest member close of the composite interval after ``after_ms``.

        Parameters
        ----------
        after_ms : int
            Epoch milliseconds.

        Returns
        -------
        int or None
            The next composite shut transition; ``None`` when no member
            ever shuts, or the composite never opens again.
        """
        start = after_ms if self.is_open(after_ms) else self.next_open(after_ms)
        if start is None:
            return None
        ends = [member.next_close(start) for member in self._members]
        ends = [end for end in ends if end is not None]
        return min(ends) if ends else None

    def _first_open_at_or_after(self, at_ms):
        """Return the least instant >= ``at_ms`` open for every member, or ``None``."""
        for _ in range(MAX_COMPOSITE_ROUNDS):
            latest = at_ms
            for member in self._members:
                if member.is_open(at_ms):
                    continue
                opens = member.next_open(at_ms)
                if opens is None:
                    return None
                latest = max(latest, opens)
            if latest == at_ms:
                return at_ms
            at_ms = latest
        raise ProductionError(
            [
                f"composite: members found no common open instant within "
                f"{MAX_COMPOSITE_ROUNDS} rounds after {at_ms}"
            ]
        )

    def _intersect(self, spans, at_ms):
        """Return the intersection of the members' spans, or ``None`` when none offers one."""
        offered = [span for span in spans if span is not None]
        if not offered:
            return None
        start = max(span[0] for span in offered)
        end = min(span[1] for span in offered)
        if start >= end:
            raise ProductionError(
                [f"composite: the members' windows do not overlap at {at_ms}"]
            )
        return (start, end)

    def _window_session(self, at_ms):
        """Return the intersection of the members' session windows."""
        return self._intersect([m._window_session(at_ms) for m in self._members], at_ms)

    def _window_event(self, at_ms):
        """Return the intersection of the members' event windows."""
        return self._intersect([m._window_event(at_ms) for m in self._members], at_ms)


CALENDAR_KINDS = Registry("calendar", Calendar)
CALENDAR_KINDS.register("always-open", AlwaysOpen)
CALENDAR_KINDS.register("weekly-sessions", WeeklySessions)
CALENDAR_KINDS.register("event-window", EventWindow)
CALENDAR_KINDS.register("composite", Composite)
