"""The coverage ledger — sparse-backfill idempotency (ADR-0030).

The checkpoint cursor (:mod:`.state`) answers "how far has this stream
advanced" — one watermark per (source, stream, mode). A BACKFILL over
units × periods (tickers × days, stations × months, files × versions)
needs a finer primitive: which (unit, period) cells are already
answered, which still need pulling, which slow-moving units have gone
stale, and whether the ledger still agrees with what is actually on
disk. This module is that primitive — a SQLite done-set keyed
``(source, stream, unit, period)`` with a CLOSED status vocabulary:

* ``fetched`` — the pull landed data for this cell;
* ``no_data`` — the vendor answered and there is genuinely nothing
  (a tombstone, so an empty answer is not re-pulled forever). An empty
  answer that might be transient (a rate limit) should simply not be
  marked — unmarked IS the retry state.

Two design rulings, both anti-bug by construction:

* **Expected periods are DECLARED, never inferred.** ``missing`` takes
  the caller's period list (the child knows its trading days, its
  months, its versions); the ledger never guesses a calendar from the
  marks it happens to hold — the observation-range inference that
  guessed "inside the min/max is covered" is exactly the blind spot
  this module exists to make unrepresentable.
* **Staleness is period-based, not wall-clock.** ``stale_units`` asks
  "whose newest marked period is older than this cutoff" — a pure
  function of the ledger's content, deterministic on any machine.
  (``marked_at`` is still recorded per cell, for humans reading the
  file — state, never an input to any query.)

Truth checks close the loop: ``audit(observed)`` is the symmetric diff
between the ledger's ``fetched`` claims and reality (an iterable of
``(unit, period)`` pairs the caller derives from its store), and
``reconcile(observed)`` adopts store truth for cells the ledger missed.
Neither ever silently CLEARS a ledger claim — a claim reality cannot
back is a finding (``ledger_only``), and clearing it is the operator's
deliberate :meth:`~CoverageLedger.clear`.

One writer per ledger file — the store-seam concurrency doctrine
(ADR-0018 scope): calls are transactional and durable, coordination
above that is the caller's. Acquisition never consults the ledger
implicitly; the backfill loop drives it.

Import cost: stdlib + this package (sqlite3 is stdlib; connections open
lazily so importing the module touches no disk).
"""

from __future__ import annotations

import os

from .base import AssetError, _check_segment, _raise_if, utc_now

__all__ = ["CoverageLedger", "STATUSES"]

#: The closed status vocabulary — anything else is refused by name.
STATUSES = ("fetched", "no_data")

#: SQLite bind-parameter ceiling headroom: chunk every IN (...) query.
_MAX_IN = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS coverage (
    source    TEXT NOT NULL,
    stream    TEXT NOT NULL,
    unit      TEXT NOT NULL,
    period    TEXT NOT NULL,
    status    TEXT NOT NULL,
    marked_at TEXT NOT NULL,
    PRIMARY KEY (source, stream, unit, period)
)
"""


def _check_cell(errors, name, value):
    """Units and periods are opaque NON-EMPTY strings — the ledger owns
    no calendar and no universe, so shape is all it can (and must)
    check."""
    if not isinstance(value, str) or not value or "\x00" in value:
        errors.append(f"{name} must be a non-empty string, got {value!r}")


def _check_periods(errors, name, periods):
    if not isinstance(periods, (list, tuple, set, frozenset)) or not periods:
        errors.append(
            f"{name} must be a non-empty collection of period strings, "
            f"got {periods!r}"
        )
        return ()
    ordered = sorted(periods) if isinstance(periods, (set, frozenset)) else periods
    for period in ordered:
        _check_cell(errors, f"{name}[]", period)
    return tuple(ordered)


def _chunks(values, size=_MAX_IN):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start : start + size]


class CoverageLedger:
    """The (source, stream, unit, period) done-set over one SQLite file.

    Parameters
    ----------
    path : str
        The ledger file; created (with WAL journaling) on first use. The
        parent directory must exist — an :class:`~dskit.onboarding.
        layout.OnboardingRoot` supplies it via ``coverage_path()``.

    Examples
    --------
    >>> import tempfile, os
    >>> led = CoverageLedger(os.path.join(tempfile.mkdtemp(), "cov.sqlite"))
    >>> led.mark("vendor", "prices", "AAPL", ["2026-01-05"], status="fetched")
    1
    >>> led.missing("vendor", "prices", ["AAPL"], ["2026-01-05", "2026-01-06"])
    {'AAPL': ['2026-01-06']}
    """

    def __init__(self, path):
        errors = []
        if not isinstance(path, str) or not path:
            errors.append(f"path must be a non-empty string, got {path!r}")
        _raise_if(errors)
        self.path = os.path.abspath(os.path.expanduser(path))
        self._conn = None

    @classmethod
    def from_root(cls, root) -> "CoverageLedger":
        """The ledger at an :class:`~dskit.onboarding.layout.
        OnboardingRoot`'s ``coverage_path()`` — the sanctioned home."""
        return cls(root.coverage_path())

    # -- plumbing ----------------------------------------------------------

    def _connection(self):
        import sqlite3

        if self._conn is None:
            try:
                self._conn = sqlite3.connect(self.path)
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute(_SCHEMA)
                self._conn.commit()
            except sqlite3.Error as exc:
                raise AssetError(
                    [f"cannot open coverage ledger {self.path!r}: {exc}"]
                ) from exc
        return self._conn

    def close(self) -> None:
        """Close the underlying connection (idempotent)."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    def _execute(self, sql, args=()):
        import sqlite3

        try:
            return self._connection().execute(sql, args)
        except sqlite3.Error as exc:
            raise AssetError(
                [f"coverage ledger {self.path!r}: {exc}"]
            ) from exc

    def _scope(self, source, stream, extra=None):
        errors = []
        _check_segment(errors, "source", source)
        _check_segment(errors, "stream", stream)
        if extra:
            extra(errors)
        _raise_if(errors)

    # -- mutations ---------------------------------------------------------

    def mark(self, source, stream, unit, periods, status="fetched") -> int:
        """Record ``status`` for every ``(unit, period)`` cell; upsert —
        re-marking is idempotent, and a ``no_data`` tombstone lawfully
        becomes ``fetched`` when a later pull finds data (vendors
        backfill too). Returns the number of cells written."""
        checked = []

        def _extra(errors):
            _check_cell(errors, "unit", unit)
            checked.extend(_check_periods(errors, "periods", periods))
            if status not in STATUSES:
                errors.append(
                    f"status must be one of {sorted(STATUSES)}, got {status!r} "
                    "(a closed vocabulary — an unsure answer stays UNMARKED, "
                    "which is the retry state)"
                )

        self._scope(source, stream, _extra)
        import sqlite3

        stamp = utc_now()
        conn = self._connection()
        try:
            with conn:  # one transaction per call
                conn.executemany(
                    "INSERT INTO coverage (source, stream, unit, period, status, "
                    "marked_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (source, stream, unit, period) "
                    "DO UPDATE SET status = excluded.status, "
                    "marked_at = excluded.marked_at",
                    [(source, stream, unit, p, status, stamp) for p in checked],
                )
        except sqlite3.Error as exc:
            raise AssetError([f"coverage ledger {self.path!r}: {exc}"]) from exc
        return len(checked)

    def clear(self, source, stream, unit, periods=None) -> int:
        """The operator's unmark — for correcting a wrong claim (the
        ``audit`` finding's remedy). ``periods=None`` clears the unit's
        whole row set. Returns the number of cells removed."""
        checked = []

        def _extra(errors):
            _check_cell(errors, "unit", unit)
            if periods is not None:
                checked.extend(_check_periods(errors, "periods", periods))

        self._scope(source, stream, _extra)
        removed = 0
        if periods is None:
            cur = self._execute(
                "DELETE FROM coverage WHERE source = ? AND stream = ? AND unit = ?",
                (source, stream, unit),
            )
            removed = cur.rowcount
        else:
            for chunk in _chunks(checked):
                holes = ",".join("?" * len(chunk))
                cur = self._execute(
                    "DELETE FROM coverage WHERE source = ? AND stream = ? "
                    f"AND unit = ? AND period IN ({holes})",
                    (source, stream, unit, *chunk),
                )
                removed += cur.rowcount
        self._connection().commit()
        return removed

    # -- queries -----------------------------------------------------------

    def covered(self, source, stream, unit) -> dict:
        """Every marked cell for one unit: ``{period: status}``."""

        def _extra(errors):
            _check_cell(errors, "unit", unit)

        self._scope(source, stream, _extra)
        cur = self._execute(
            "SELECT period, status FROM coverage "
            "WHERE source = ? AND stream = ? AND unit = ?",
            (source, stream, unit),
        )
        return dict(cur.fetchall())

    def missing(self, source, stream, units, periods) -> dict:
        """``{unit: [periods with NO mark]}`` against the caller's
        DECLARED expectation — the pull-list query. Both statuses count
        as answered (``no_data`` is an answer); only genuinely unmarked
        cells return. Units fully covered are absent from the result."""
        expected = []
        checked_units = []

        def _extra(errors):
            if not isinstance(units, (list, tuple)) or not units:
                errors.append(
                    f"units must be a non-empty list of unit strings, got {units!r}"
                )
            else:
                for u in units:
                    _check_cell(errors, "units[]", u)
                    checked_units.append(u)
            expected.extend(_check_periods(errors, "periods", periods))

        self._scope(source, stream, _extra)
        out = {}
        want = set(expected)
        for unit in checked_units:
            have = set()
            for chunk in _chunks(sorted(want)):
                holes = ",".join("?" * len(chunk))
                cur = self._execute(
                    "SELECT period FROM coverage WHERE source = ? AND stream = ? "
                    f"AND unit = ? AND period IN ({holes})",
                    (source, stream, unit, *chunk),
                )
                have.update(row[0] for row in cur.fetchall())
            gaps = sorted(want - have)
            if gaps:
                out[unit] = gaps
        return out

    def stale_units(self, source, stream, units, cutoff) -> list:
        """Units whose NEWEST marked period is older than ``cutoff`` —
        including units with no marks at all. Period comparison is
        lexicographic (ISO dates order correctly); the cutoff is a
        period string, never a wall clock: the answer is a pure function
        of the ledger's content."""
        checked_units = []

        def _extra(errors):
            _check_cell(errors, "cutoff", cutoff)
            if not isinstance(units, (list, tuple)) or not units:
                errors.append(
                    f"units must be a non-empty list of unit strings, got {units!r}"
                )
            else:
                for u in units:
                    _check_cell(errors, "units[]", u)
                    checked_units.append(u)

        self._scope(source, stream, _extra)
        stale = []
        for unit in checked_units:
            cur = self._execute(
                "SELECT MAX(period) FROM coverage "
                "WHERE source = ? AND stream = ? AND unit = ?",
                (source, stream, unit),
            )
            newest = cur.fetchone()[0]
            if newest is None or newest < cutoff:
                stale.append(unit)
        return stale

    def units(self, source, stream) -> list:
        """Every unit the ledger holds marks for, sorted."""
        self._scope(source, stream)
        cur = self._execute(
            "SELECT DISTINCT unit FROM coverage WHERE source = ? AND stream = ? "
            "ORDER BY unit",
            (source, stream),
        )
        return [row[0] for row in cur.fetchall()]

    # -- truth checks ------------------------------------------------------

    def audit(self, source, stream, observed) -> dict:
        """The symmetric ledger-vs-reality diff.

        ``observed`` is an iterable of ``(unit, period)`` pairs the
        caller derives from its store — what is ACTUALLY there. Returns
        ``{"ledger_only": [...], "store_only": [...]}``, both sorted:
        ``ledger_only`` are ``fetched`` claims reality cannot back
        (``no_data`` tombstones claim absence and are exempt);
        ``store_only`` is data the ledger never heard about. An empty
        pair of lists is the healthy answer."""
        self._scope(source, stream)
        reality = set()
        for pair in observed:
            if (
                not isinstance(pair, (list, tuple))
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or not pair[0]
                or not isinstance(pair[1], str)
                or not pair[1]
            ):
                raise AssetError(
                    [
                        "audit observed pairs must be (unit, period) string "
                        f"2-tuples, got {pair!r}"
                    ]
                )
            reality.add((pair[0], pair[1]))
        cur = self._execute(
            "SELECT unit, period FROM coverage "
            "WHERE source = ? AND stream = ? AND status = 'fetched'",
            (source, stream),
        )
        claimed = set(cur.fetchall())
        return {
            "ledger_only": sorted(claimed - reality),
            "store_only": sorted(reality - claimed),
        }

    def reconcile(self, source, stream, observed) -> int:
        """Adopt store truth: mark every ``store_only`` cell ``fetched``.
        Returns how many were adopted. ``ledger_only`` findings are NOT
        cleared — a claim reality cannot back is the operator's to
        investigate, never something anti-entropy quietly erases."""
        gaps = self.audit(source, stream, observed)["store_only"]
        adopted = 0
        by_unit = {}
        for unit, period in gaps:
            by_unit.setdefault(unit, []).append(period)
        for unit, periods in by_unit.items():
            adopted += self.mark(source, stream, unit, periods, status="fetched")
        return adopted
