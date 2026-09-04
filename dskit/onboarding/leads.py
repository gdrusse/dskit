"""Decision instants at declared fractions of an expiring instrument's life (ADR-0075).

Anything that expires — an option, a futures contract, an event contract,
a forecast with a fixed target date — has an observable life between its
listing (``open``) and its expiry (``close``), and a point-in-time study
reads its state at a schedule of instants inside that life. Spelling the
schedule as FRACTIONS of the life rather than as clock offsets lets one
declaration serve a two-day and a two-month instrument alike; two guards
keep it honest. A DURATION-CAP ladder bounds the observable span, so an
instrument listed months early is still read over the window that carries
information. An ABSOLUTE floor on the lead stops an ultra-short instrument
from placing a checkpoint seconds before expiry.

The recorder that captures state at those instants and the build that
later selects the captured rows must agree to the millisecond, or the
build looks for rows the recorder never wrote. Both therefore read ONE
class: :meth:`LeadGrid.due_periods` is what a recorder declares to the
coverage ledger (each fraction spelled as a period string, paired with its
instant), :meth:`LeadGrid.epochs` is what a build selects on, and the two
are one arithmetic over one declaration — byte-identical by construction.

No schedule is built in. The fractions, the caps and the floor are the
caller's declaration (a child's configuration), and a grid that cannot be
declared cannot exist: :meth:`LeadGrid.problems` is the plan-time half a
node kind calls from ``validate_params``, and the constructor refuses on
the same list.

Import cost: stdlib only.
"""

from __future__ import annotations

import math

__all__ = ["LEAD_ROUND_DP", "LeadGrid", "lead_key"]

#: Decimal places a lead fraction is rounded to wherever it is a KEY — the
#: float key (:func:`lead_key`) and the period spelling
#: (:meth:`LeadGrid.normalize`) alike — so a cell keyed by one reader is
#: found by another. Coverage ledgers PERSIST the spelling: moving this
#: orphans every period already marked.
LEAD_ROUND_DP = 6

_MS_PER_HOUR = 3_600_000
_MS_PER_SECOND = 1_000


def lead_key(lead_frac):
    """Round a lead fraction the one way every keyed lookup does.

    Parameters
    ----------
    lead_frac : float or str
        A fraction; a numeric string parses.

    Returns
    -------
    float
        ``round(float(lead_frac), LEAD_ROUND_DP)``.
    """
    return round(float(lead_frac), LEAD_ROUND_DP)


def _number(value):
    """Say whether ``value`` is a real number (a bool is not)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class LeadGrid:
    """The decision instants at which one expiring instrument is read.

    The observable span of an instrument is ``close − max(open, close −
    cap)``, where ``cap`` is the duration cap matched to its life: the
    smallest of ``dur_caps_h`` at or above ``close − open`` in hours, else
    the largest. Each fraction ``f`` names the instant ``close −
    max(f · span, min_abs_lead_s)``. The lead is FLOORED, so an
    ultra-short instrument never places a checkpoint seconds before
    expiry, and an instant at or before the raw ``open`` is dropped — a
    state that does not exist yet cannot be read. Instants are emitted
    earliest first, which is why the fractions are declared strictly
    decreasing.

    Parameters
    ----------
    lead_fracs : sequence of float
        The share of the span still ahead at each instant, each in
        (0, 1), strictly decreasing (chronological order), distinct at
        :data:`LEAD_ROUND_DP` decimals.
    dur_caps_h : sequence of int or float
        The duration-cap ladder in hours, ascending and distinct — one
        rung per nominal life the caller distinguishes (daily, weekly,
        monthly, say).
    min_abs_lead_s : int or float
        The absolute floor on the lead, in seconds, ``>= 0``; ``0``
        declares no floor.

    Raises
    ------
    ValueError
        Every problem :meth:`problems` finds, joined — a grid that cannot
        be declared cannot exist.

    Examples
    --------
    Three instants over a day-long instrument, under a two-rung ladder
    and a one-minute floor::

        grid = LeadGrid((0.9, 0.5, 0.1), (48, 168), 60)
        grid.epochs(open_ms=0, close_ms=86_400_000)
        # -> [(0.9, 8640000), (0.5, 43200000), (0.1, 77760000)]
        grid.due_periods(0, 86_400_000)[0]     # ('0.900000', 8640000)
        grid.position(0.5)                     # 1
    """

    def __init__(self, lead_fracs, dur_caps_h, min_abs_lead_s):
        problems = self.problems(lead_fracs, dur_caps_h, min_abs_lead_s)
        if problems:
            raise ValueError("; ".join(problems))
        self.lead_fracs = tuple(float(f) for f in lead_fracs)
        self.dur_caps_h = tuple(float(c) for c in dur_caps_h)
        self.min_abs_lead_s = float(min_abs_lead_s)

    @staticmethod
    def problems(lead_fracs, dur_caps_h, min_abs_lead_s):
        """List problems with a grid declaration, empty when none.

        The plan-time half: a node kind carrying these knobs calls this
        from ``validate_params`` so a document is refused before anything
        runs, and the constructor calls it too. It RETURNS on any input —
        a validator that raises turns a typo into a stack trace.

        Parameters
        ----------
        lead_fracs : object
            The declared fractions.
        dur_caps_h : object
            The declared duration caps, hours.
        min_abs_lead_s : object
            The declared absolute lead floor, seconds.

        Returns
        -------
        list of str
            One message per problem, each naming its knob first.
        """
        problems = []
        if not isinstance(lead_fracs, (list, tuple)) or not lead_fracs:
            problems.append(f"lead_fracs must be a non-empty list, got {lead_fracs!r}")
        else:
            bad = [f for f in lead_fracs if not _number(f) or not 0.0 < f < 1.0]
            keys = [] if bad else [lead_key(f) for f in lead_fracs]
            if bad:
                problems.append(f"lead_fracs entries must lie in (0, 1), got {bad[0]!r}")
            elif list(lead_fracs) != sorted(lead_fracs, reverse=True):
                problems.append("lead_fracs must be strictly decreasing")
            elif len(set(keys)) != len(keys):
                problems.append(
                    "lead_fracs must not repeat a fraction "
                    f"(compared at {LEAD_ROUND_DP} decimals)"
                )
        if not isinstance(dur_caps_h, (list, tuple)) or not dur_caps_h:
            problems.append(f"dur_caps_h must be a non-empty list, got {dur_caps_h!r}")
        else:
            bad = [
                c for c in dur_caps_h
                if not _number(c) or not math.isfinite(c) or c <= 0
            ]
            if bad:
                problems.append(
                    f"dur_caps_h entries must be finite numbers > 0, got {bad[0]!r}"
                )
            elif list(dur_caps_h) != sorted(dur_caps_h):
                problems.append("dur_caps_h must be ascending")
            elif len(set(dur_caps_h)) != len(dur_caps_h):
                problems.append("dur_caps_h must not repeat a cap")
        if (
            not _number(min_abs_lead_s)
            or not math.isfinite(min_abs_lead_s)
            or min_abs_lead_s < 0
        ):
            problems.append(
                f"min_abs_lead_s must be a finite number >= 0, got {min_abs_lead_s!r}"
            )
        return problems

    @staticmethod
    def normalize(lead_frac):
        """Spell a fraction as the period string a coverage ledger keys on.

        Parameters
        ----------
        lead_frac : float or str
            A fraction, or a period string an earlier call spelled.

        Returns
        -------
        str
            The fraction at :data:`LEAD_ROUND_DP` decimals (``"0.980000"``):
            :func:`lead_key` rounding, then a fixed-point spelling, so every
            reader spells one fraction one way and the spelling reads back
            through ``float``.

        Raises
        ------
        ValueError
            When the value is not a finite number (NaN, an infinity, a
            non-numeric string).
        """
        key = lead_key(lead_frac)
        if not math.isfinite(key):
            raise ValueError(f"a lead fraction must be finite, got {lead_frac!r}")
        return f"{key:.{LEAD_ROUND_DP}f}"

    def cap_ms(self, open_ms, close_ms):
        """Choose the duration cap for one instrument, in milliseconds.

        Parameters
        ----------
        open_ms : int
            The listing instant, epoch ms.
        close_ms : int
            The expiry instant, epoch ms; must exceed ``open_ms``.

        Returns
        -------
        int
            The smallest declared cap at or above the instrument's life, or
            the largest cap when every cap is shorter than the life.
        """
        life_h = (close_ms - open_ms) / _MS_PER_HOUR
        for cap in self.dur_caps_h:
            if life_h <= cap:
                return int(round(cap * _MS_PER_HOUR))
        return int(round(self.dur_caps_h[-1] * _MS_PER_HOUR))

    def span(self, open_ms, close_ms):
        """Bound the observable window ``(start_ms, close_ms)`` of one instrument.

        Parameters
        ----------
        open_ms : int
            The listing instant, epoch ms.
        close_ms : int
            The expiry instant, epoch ms.

        Returns
        -------
        tuple of int
            ``(max(open_ms, close_ms − cap), close_ms)``.

        Raises
        ------
        ValueError
            When the close does not follow the open.
        """
        if close_ms <= open_ms:
            raise ValueError(
                "an instrument must close after it opens, "
                f"got open={open_ms} close={close_ms}"
            )
        return max(open_ms, close_ms - self.cap_ms(open_ms, close_ms)), close_ms

    def epochs(self, open_ms, close_ms):
        """List the decision instants of one instrument, earliest first.

        Parameters
        ----------
        open_ms : int
            The listing instant, epoch ms.
        close_ms : int
            The expiry instant, epoch ms.

        Returns
        -------
        list of tuple
            ``[(lead_frac, epoch_ms), ...]`` for every fraction whose
            floored instant falls strictly after the raw open, in
            chronological order. Empty for an instrument no longer than
            the floor.

        Raises
        ------
        ValueError
            When the close does not follow the open.
        """
        start, close = self.span(open_ms, close_ms)
        span = close - start
        floor_ms = self.min_abs_lead_s * _MS_PER_SECOND
        out = []
        for f in self.lead_fracs:
            lead_ms = max(f * span, floor_ms)
            instant = int(round(close - lead_ms))
            if instant > open_ms:
                out.append((f, instant))
        return out

    def due_periods(self, open_ms, close_ms):
        """List the decision instants as ``(period, epoch_ms)`` pairs, earliest first.

        The coverage-ledger spelling of :meth:`epochs`: a recorder marks
        each ``period`` fetched once the instant's capture is durable, and
        ``missing`` against this list is exactly the captures still owed.

        Parameters
        ----------
        open_ms : int
            The listing instant, epoch ms.
        close_ms : int
            The expiry instant, epoch ms.

        Returns
        -------
        list of tuple
            ``[(normalize(lead_frac), epoch_ms), ...]`` — the same
            instants :meth:`epochs` lists, the fraction spelled by
            :meth:`normalize`.

        Raises
        ------
        ValueError
            When the close does not follow the open.
        """
        return [
            (self.normalize(f), instant) for f, instant in self.epochs(open_ms, close_ms)
        ]

    def position(self, lead_frac):
        """Locate a fraction on this grid, or ``None`` when off-grid.

        Parameters
        ----------
        lead_frac : float or str
            A fraction (or its period spelling), compared after
            :func:`lead_key` rounding.

        Returns
        -------
        int or None
            The chronological position; ``None`` for a fraction the grid
            does not carry (a loader skips such a row, never aborts).
        """
        key = lead_key(lead_frac)
        for i, f in enumerate(self.lead_fracs):
            if lead_key(f) == key:
                return i
        return None
