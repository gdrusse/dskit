"""``libs/exchange_calendars.py`` — a published exchange schedule, materialised (§5.1.1).

:class:`~dskit.production.sessions.WeeklySessions` makes a document
restate an exchange's schedule by hand. That is correct for a venue
nobody has published and wasteful for one everybody has: the holidays,
the half-days and the local open and close of a listed exchange are a
maintained data set, and copying them into a serve document is a copy
that silently goes stale.

This pack reads the published one **once, at construction**, and turns it
into exactly the session list phase 1 already consumes. Everything after
that is the phase-1 owner's: ``is_open``, ``next_open``, ``next_close``,
``window(kind, at_ms)`` and ``tz_name`` are answered by a
``WeeklySessions`` this class builds and holds, so the half-open
``[open, close)`` bounds, the buffers, the blackout arithmetic, the
DST-gap refusal and the ``CALENDAR_WINDOWS`` dispatch are not re-derived
here and cannot drift from the calendar a document writes out by hand.

**The materialisation is lossy, and what it cannot express refuses.** A
weekly pattern plus holidays plus early closes covers the shape of nearly
every listed exchange, and covers it exactly. A day the exchange did not
publish becomes a holiday; a weekday it trades only occasionally becomes
a session day with the rest of that weekday's dates listed as holidays;
and since the ONE deviation the shape can express is an earlier close,
each weekday's pattern is its LATEST close and every shorter day of that
weekday becomes a ``special_close``. What is left over cannot be
expressed at all — a day that opened LATE, a weekday whose days disagree
on how many sessions they hold, a session that crosses midnight, a
boundary that is not a whole wall minute — and each refuses at
construction naming the dates, because rounding one of them to the weekly
pattern would put the loop in the market at an hour the exchange was
shut, which is the failure this pack exists to prevent rather than to
introduce.

**Outside ``bounds`` the calendar is shut.** The list is a fact about the
window it was read over: past ``until`` the weekly pattern would keep
opening the market with no holidays left to stop it, so the sessions are
intersected with the query window through phase 1's own ``Composite`` and
``EventWindow``. A loop that reaches the end of its calendar stops
visibly, and the operator answers by re-planning against a longer window.

**``bounds`` is required.** An unbounded library query is not
reproducible: the answer would change as the library's own horizon moves,
under a release hash that never moved. Bounding the query is what makes
the materialised list — and therefore
:meth:`ExchangeCalendar.data_fingerprint` — a fact about the document
rather than about the day it was loaded.

``data_fingerprint()`` is the digest of that materialised list, and it is
the one thing a code fingerprint cannot carry: after a library upgrade
that moves a holiday, the CODE is identical and the DATA is not. ``plan``
binds a non-``None`` answer into the release and composition refuses when
it has moved, so a trading day cannot change under a fixed release and a
live arm.

``exchange_calendars`` is named only inside :meth:`_materialise`, per §8's
tier-2 rule: importing this pack must not import the library, and a serve
document that names no ``exchange`` calendar never loads it.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from dskit.production.base import (
    ProductionError,
    _check_str,
    canonical_hash,
    reject_unknown_params,
)
from dskit.production.sessions import (
    CALENDAR_KINDS,
    DAY_NAMES,
    MIN_BUFFER_S,
    Calendar,
    Composite,
    WeeklySessions,
)

__all__ = [
    "BOUNDS_KEYS",
    "ExchangeCalendar",
]

#: The two ends of the required, inclusive query window.
BOUNDS_KEYS = ("from", "until")

#: The params a materialised session list passes straight to the phase-1
#: owner; the pack computes the rest of the block.
_PASSED_THROUGH = ("blackouts", "after_open_s", "before_close_s")

#: The one params key every seam site may carry beside its knobs.
_NOTES = ("notes",)

_ONE_DAY = timedelta(days=1)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _epoch_ms(stamp):
    """Return an aware datetime as epoch milliseconds (exact integer arithmetic)."""
    return (stamp - _EPOCH) // timedelta(milliseconds=1)


def _wall_ok(text):
    """Say whether ``text`` is an ``HH:MM`` wall time the session list can hold."""
    if not isinstance(text, str) or len(text) != 5 or text[2] != ":":
        return False
    try:
        datetime.strptime(text, "%H:%M")
    except ValueError:
        return False
    return True


def _dates(first, last):
    """Yield every date from ``first`` through ``last``, inclusive."""
    day = first
    while day <= last:
        yield day
        day += _ONE_DAY


def _shape(intervals):
    """Return everything about a day's sessions but the final close."""
    return (intervals[:-1], intervals[-1][0])


class ExchangeCalendar(Calendar):
    """A published exchange schedule, materialised into a weekly session list.

    Registered as ``exchange`` in
    :data:`~dskit.production.sessions.CALENDAR_KINDS` (§4.3: import is
    registration). The library is read ONCE, at construction, inside
    :meth:`_materialise`; every question is then answered by the
    :class:`~dskit.production.sessions.WeeklySessions` built from what it
    returned, so nothing about windows, buffers or daylight saving is
    re-derived here.

    A schedule the weekly shape cannot express refuses at construction and
    names the dates: a late open, a close later than the weekday's
    pattern, a session crossing midnight, a boundary that is not a whole
    wall minute, or a query window the exchange never traded in.

    Parameters
    ----------
    params : dict
        ``exchange`` (str, required): the library's calendar code, e.g.
        ``"XNYS"``. ``bounds`` (``{"from", "until"}`` ISO dates, required
        and inclusive): the window materialised — required because an
        unbounded query answers differently as the library's horizon
        moves. ``tz`` (str, optional): an IANA zone the published wall
        times are re-interpreted in; default the library's own.
        ``after_open_s`` / ``before_close_s`` (int >= 0) and ``blackouts``
        (list of ``{"from", "until"}`` UTC instants) are passed to the
        session list untouched. ``notes`` is allowed, as everywhere.

    Attributes
    ----------
    tz_name : str
        The zone the materialised sessions are written in.

    Examples
    --------
    Serve on the New York Stock Exchange's own 2026 calendar, holding a
    minute back after each open::

        cal = ExchangeCalendar({
            "exchange": "XNYS",
            "bounds": {"from": "2026-01-02", "until": "2026-12-31"},
            "after_open_s": 60,
        })
        cal.tz_name                      # 'America/New_York'
        cal.is_open(1_767_364_200_000)   # False — 2026-01-02 09:30, still held back
        cal.data_fingerprint()           # the digest `plan` binds into the release
    """

    _PARAMS = ("exchange", "bounds", "tz") + _PASSED_THROUGH

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when it is acceptable.

        The library is never asked for anything here: a document is
        validated on hosts that do not have it installed, and a malformed
        block must refuse identically wherever it is read.

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
        _check_str(problems, "exchange", params.get("exchange"))
        cls._check_bounds(problems, params.get("bounds"))
        cls._check_session_knobs(problems, params)
        return problems

    @classmethod
    def _check_bounds(cls, problems, bounds):
        """Append every problem with the required inclusive ``{from, until}`` window."""
        if not isinstance(bounds, dict):
            problems.append(
                "bounds is required: {'from': 'YYYY-MM-DD', 'until': 'YYYY-MM-DD'} — "
                "an unbounded library query is not reproducible"
            )
            return
        reject_unknown_params(problems, bounds, BOUNDS_KEYS + _NOTES)
        edges = []
        for key in BOUNDS_KEYS:
            value = bounds.get(key)
            try:
                edges.append(date.fromisoformat(value))
            except (TypeError, ValueError):
                problems.append(f"bounds.{key} must be a 'YYYY-MM-DD' date, got {value!r}")
        if len(edges) == 2 and edges[1] <= edges[0]:
            problems.append(
                f"bounds.until {bounds['until']!r} must be after bounds.from {bounds['from']!r}"
            )

    @classmethod
    def _check_session_knobs(cls, problems, params):
        """Append problems with the knobs the session list is handed untouched."""
        block = {key: params[key] for key in _PASSED_THROUGH if key in params}
        if "tz" in params:
            block["tz"] = params["tz"]
        else:
            block["tz"] = "UTC"
        block["sessions"] = [{"days": list(DAY_NAMES), "open": "00:00", "close": "23:59"}]
        problems.extend(
            problem
            for problem in WeeklySessions.validate_params(block)
            if "sessions" not in problem
        )

    def _configure(self, params):
        """Read the schedule once, materialise it, and delegate everything after."""
        tz_name, rows = self._materialise(params)
        self._session_params = self._session_list(tz_name, rows, params)
        self._sessions = Composite({"members": [
            {"uses": "weekly-sessions", "params": self._session_params},
            {"uses": "event-window", "params": self._horizon(self._session_params, params)},
        ]})
        self.tz_name = self._sessions.tz_name
        self._data_digest = canonical_hash(self._session_params)

    @staticmethod
    def _horizon(session_params, params):
        """Return the ``EventWindow`` params that shut the calendar outside ``bounds``.

        A materialised session list is a fact about the window it was read
        over and nothing else: past ``until`` the weekly pattern would keep
        opening the market with no holidays to stop it, so the loop would
        trade on the first unlisted Christmas. Intersecting the list with
        the query window is what makes the calendar SHUT there instead —
        a visible, safe stop that an operator answers by re-planning
        against a longer window.
        """
        zone = ZoneInfo(session_params["tz"])
        first, last = (date.fromisoformat(params["bounds"][key]) for key in BOUNDS_KEYS)
        return {
            "start_ms": _epoch_ms(datetime.combine(first, time(), tzinfo=zone)),
            "until_ms": _epoch_ms(datetime.combine(last + _ONE_DAY, time(), tzinfo=zone)),
        }

    def _materialise(self, params):
        """Read the published schedule; the ONE method that names the library.

        Parameters
        ----------
        params : dict
            The validated params block.

        Returns
        -------
        tuple
            ``(tz_name, rows)`` — the library's own zone as an IANA name,
            and ``{'YYYY-MM-DD': ((open, close), ...)}`` with each bound
            an ``HH:MM`` local wall time, sorted, for every session the
            exchange published inside ``bounds``.

        Raises
        ------
        ProductionError
            If the code names no calendar the library knows, or the query
            window lies outside the calendar's own horizon.
        """
        import exchange_calendars

        code = params["exchange"]
        first, last = (date.fromisoformat(params["bounds"][key]) for key in BOUNDS_KEYS)
        try:
            calendar = exchange_calendars.get_calendar(
                code, start=first.isoformat(), end=last.isoformat()
            )
        except Exception as exc:
            raise ProductionError(
                [f"exchange {code!r}: the calendar library refused it — {exc}"]
            ) from exc
        zone = str(calendar.tz)
        rows = {}
        for session in calendar.sessions_in_range(first.isoformat(), last.isoformat()):
            day = session.date() if hasattr(session, "date") else session
            rows[day.isoformat()] = self._intervals(calendar, session, zone)
        return zone, rows

    @staticmethod
    def _intervals(calendar, session, zone):
        """Return one published session's local ``HH:MM`` intervals, break included."""
        from zoneinfo import ZoneInfo

        def wall(stamp):
            """Render a library instant as an ``HH:MM`` wall time in ``zone``."""
            moment = stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else stamp
            local = moment.astimezone(ZoneInfo(zone))
            return f"{local.hour:02d}:{local.minute:02d}" if local.second == 0 else (
                f"{local.hour:02d}:{local.minute:02d}:{local.second:02d}"
            )

        opened, closed = calendar.session_open(session), calendar.session_close(session)
        start = calendar.session_break_start(session)
        end = calendar.session_break_end(session)
        if start is None or end is None or start != start:  # NaT is not equal to itself
            return ((wall(opened), wall(closed)),)
        return ((wall(opened), wall(start)), (wall(end), wall(closed)))

    def _session_list(self, tz_name, rows, params):
        """Turn published rows into the ``WeeklySessions`` params block, or refuse."""
        first, last = (date.fromisoformat(params["bounds"][key]) for key in BOUNDS_KEYS)
        rows = self._checked_rows(rows, first, last)
        patterns = self._weekly_patterns(rows)
        holidays, special_closes, problems = [], [], []
        for day in _dates(first, last):
            pattern = patterns.get(day.weekday())
            if pattern is None:
                continue  # a weekday this exchange never traded in the window
            published = rows.get(day)
            if not published:
                holidays.append(day.isoformat())
            elif published != pattern:
                self._early_close(problems, special_closes, day, published, pattern)
        if problems:
            raise ProductionError(problems)
        block = {key: params[key] for key in _PASSED_THROUGH if key in params}
        block.setdefault("blackouts", [])
        block.setdefault("after_open_s", MIN_BUFFER_S)
        block.setdefault("before_close_s", MIN_BUFFER_S)
        block["tz"] = params.get("tz", tz_name)
        block["sessions"] = self._entries(patterns)
        block["holidays"] = holidays
        block["special_closes"] = special_closes
        return block

    @staticmethod
    def _checked_rows(rows, first, last):
        """Return the in-bounds published days as dates, refusing what cannot be held."""
        problems = []
        checked = {}
        for text, intervals in rows.items():
            try:
                day = date.fromisoformat(text)
            except (TypeError, ValueError):
                problems.append(f"the schedule names {text!r}, which is not a date")
                continue
            if day < first or day > last:
                continue  # the library answered wider than the document asked
            checked[day] = tuple(sorted(intervals))
            for opened, closed in checked[day]:
                if not (_wall_ok(opened) and _wall_ok(closed)):
                    problems.append(
                        f"{day}: the session {opened}-{closed} is not on a whole wall "
                        "minute, which a session list cannot hold"
                    )
                elif closed <= opened:
                    problems.append(
                        f"{day}: the session {opened}-{closed} crosses midnight, which "
                        "a weekly session list cannot express — write the calendar out "
                        "by hand, or bound the query to days that do not"
                    )
        if problems:
            raise ProductionError(problems)
        if not any(checked.values()):
            raise ProductionError(
                ["the exchange published no session inside bounds — a calendar that is "
                 "never open serves nothing"]
            )
        return checked

    @classmethod
    def _weekly_patterns(cls, rows):
        """Return ``{weekday: pattern}``, refusing a weekday that disagrees on more than its close.

        The ONE deviation a session list can express is an earlier close,
        so a weekday's pattern is its published shape with the LATEST
        final close: every other date of that weekday is then a
        ``special_close`` and the calendar reproduces the schedule
        exactly. A weekday whose dates disagree on an open, on an
        intermediate close or on how many sessions the day holds cannot be
        expressed at all, and refuses naming those dates.
        """
        traded, problems = {}, []
        for day, intervals in sorted(rows.items()):
            if intervals:
                traded.setdefault(day.weekday(), []).append((day, intervals))
        patterns = {}
        for weekday, published in traded.items():
            shapes = {_shape(intervals) for _day, intervals in published}
            if len(shapes) > 1:
                problems.append(
                    f"{DAY_NAMES[weekday]}: the published sessions differ by more than "
                    f"a close — {[(str(day), list(i)) for day, i in published]} — and a "
                    "weekly session list can express nothing else"
                )
                continue
            latest = max(intervals for _day, intervals in published)
            patterns[weekday] = latest
        if problems:
            raise ProductionError(problems)
        return patterns

    @staticmethod
    def _early_close(problems, special_closes, day, published, pattern):
        """Record one deviating day as an early close, or refuse naming it."""
        if _shape(published) == _shape(pattern) and published[-1][1] < pattern[-1][1]:
            special_closes.append({"date": day.isoformat(), "close": published[-1][1]})
            return
        problems.append(
            f"{day}: the published session {list(published)} differs from the "
            f"{DAY_NAMES[day.weekday()]} pattern {list(pattern)} by more than an "
            "earlier close, and a weekly session list can express nothing else"
        )

    @staticmethod
    def _entries(patterns):
        """Return the ``sessions`` list: weekdays grouped by the pattern they share."""
        grouped = {}
        for weekday, pattern in sorted(patterns.items()):
            grouped.setdefault(pattern, []).append(DAY_NAMES[weekday])
        return [
            {"days": days, "open": opened, "close": closed}
            for pattern, days in grouped.items()
            for opened, closed in pattern
        ]

    def is_open(self, ms):
        """Say whether the exchange is open at ``ms``, through the session list.

        Parameters
        ----------
        ms : int
            Epoch milliseconds.

        Returns
        -------
        bool
            What the materialised
            :class:`~dskit.production.sessions.WeeklySessions` answers.
        """
        return self._sessions.is_open(ms)

    def next_open(self, after_ms):
        """Return the next open transition, through the session list.

        Parameters
        ----------
        after_ms : int
            Epoch milliseconds; the answer is strictly after it.

        Returns
        -------
        int or None
            What the materialised session list answers.
        """
        return self._sessions.next_open(after_ms)

    def next_close(self, after_ms):
        """Return the next shut transition, through the session list.

        Parameters
        ----------
        after_ms : int
            Epoch milliseconds; the answer is strictly after it.

        Returns
        -------
        int or None
            What the materialised session list answers.
        """
        return self._sessions.next_close(after_ms)

    def _window_session(self, at_ms):
        """Return the materialised session window, through the phase-1 owner."""
        return self._sessions.window("session", at_ms)

    def _window_day(self, at_ms):
        """Return the local day window, through the phase-1 owner."""
        return self._sessions.window("day", at_ms)

    def data_fingerprint(self):
        """Return the digest of the materialised schedule.

        Returns
        -------
        str
            The canonical hash of the session list this calendar was
            built from — the fact a code fingerprint cannot carry, since a
            library upgrade that moves a holiday leaves the code
            identical.
        """
        return self._data_digest


CALENDAR_KINDS.register("exchange", ExchangeCalendar)
