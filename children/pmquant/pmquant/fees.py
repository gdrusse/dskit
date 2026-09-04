"""Exact venue fee models and the dated fee book (proposal §8.3).

The thesis of this child is that a correctly structured allocation can
harvest a small edge that survives transaction costs — which makes the
fee model load-bearing: a fraction of a cent per contract, across a
portfolio, is the difference between an edge and a backtest mirage. So
this module reproduces each venue's published rule exactly, including
its rounding and the floating-point guard that rounding requires, and
it holds NO rate constants: a rate is threaded from the fee book, never
defaulted, and an absent rate is a refusal by name, not a zero.

Two rounding models, one dispatch:

* **Kalshi** — ``fee = ceil_to_cent(rate · C · P · (1 − P))``. Pure binary
  floating point cannot represent ``0.07 · 100 · 0.25`` exactly (it lands
  a hair above 1.75), so the cents value is snapped to nine decimals
  BEFORE the ceiling (:data:`CENT_ROUNDING_DECIMALS`) — a real, recurring
  bug otherwise. The ceiling floors any positive fee at one cent.
* **Polymarket** — the same convex formula rounded to the NEAREST 1e-5
  (half-up, a RELATIVE tie tolerance :data:`POLY_TIE_RELATIVE` so the tie
  decides the same way at every fee size), with NO per-order floor: a
  sub-half-quantum fee legitimately rounds to zero.

:func:`trading_fee_for_series` picks the model from the ticker's venue
(:func:`pmquant.ladder.protocols.venue_of`) — the single fill-time entry
point ``walk_book`` and the MIO call.

The fee BOOK keys rates on the market's own CLOSE instant, never the
fill instant: a venue's schedule attaches to the market when it is
deployed, so a daily market closing after a rate switch bills the new
schedule for its whole life, fills placed before the switch included.
Cases use the document's own clause DSL — imported from
``dskit.pipeline.kinds_flow`` (``clause_holds``), never mirrored — with
the same rules: all of a case's clauses must hold, the first match wins,
an empty ``when`` is the explicit catch-all and must be last, and an
unmatched close instant RAISES rather than taking the nearest regime.

Import cost: stdlib + dskit — this module is imported by node modules at
plan time, so it never imports numpy.
"""

from __future__ import annotations

import datetime as _dt
import math
from collections.abc import Mapping

from dskit.pipeline.kinds_flow import CLAUSE_OPS, clause_holds, clause_problems

from .ladder.protocols import VENUES, venue_of

__all__ = [
    "CENT_ROUNDING_DECIMALS",
    "CLOSE_TS_FIELD",
    "FEE_ROUNDING",
    "POLY_FEE_DECIMALS",
    "POLY_TIE_RELATIVE",
    "FeeBook",
    "FeeCase",
    "FeeRateUnresolved",
    "close_ts_of",
    "fill_cost_for_series",
    "kalshi_trading_fee",
    "poly_trading_fee",
    "resolve_fee_rates",
    "trading_fee_for_series",
]

#: Decimal places the CENTS value is rounded to before the ceiling — the
#: guard that absorbs binary representation dust (``0.07·100·0.25``) without
#: masking a genuine fraction-of-a-cent overage.
CENT_ROUNDING_DECIMALS = 9

#: Polymarket's fee grid: fees are quoted to five decimals (1e-5 USDC).
POLY_FEE_DECIMALS = 5

#: Relative tie tolerance for the half-up rounding on the 1e-5 grid. It
#: must be RELATIVE: an absolute 1e-9 guard works in cents (small numbers)
#: but stops absorbing anything once a fee is a few dollars in quanta, so
#: an exact half-quantum tie would round up on small fills and down on
#: large ones. 1e-12 is ~1e3× the float error of the expression and ~1e-6
#: of one quantum at any fee this project can produce.
POLY_TIE_RELATIVE = 1e-12

#: The field every fee case tests: the market's own close INSTANT as an
#: ISO-8601 UTC string. An instant, not a calendar day — the acquired
#: Polymarket book has regime boundaries at 12:00Z and 20:00Z inside a
#: day, and ISO strings sort lexicographically so the ordinary comparison
#: operators express a boundary directly.
CLOSE_TS_FIELD = "close_ts"


class FeeRateUnresolved(ValueError):
    """No fee rate could be resolved, and none may be invented.

    Raised when a series is absent from the fee book, when a dated series
    is looked up without the market's close instant, when no declared
    case claims that instant, when the matching case is declared
    unpriceable, or when a fill is priced with no rate threaded at all.
    Every one of these is a refusal by name; a default, an interpolation
    or a nearest-regime guess corrupts every downstream number silently.

    Examples
    --------
    An absent series refuses rather than pricing at zero::

        book = FeeBook.from_document({"KXA": 0.07})
        try:
            book.rate_for("KXB")
        except FeeRateUnresolved as exc:
            str(exc)
        # -> "no fee rate declared for series 'KXB' ..."
    """


def _check_order(contracts, price, rate):
    """Refuse a fill that no fee formula may price; return (C, P, rate) as floats."""
    if isinstance(contracts, bool) or not isinstance(contracts, (int, float)):
        raise ValueError(f"contracts must be a non-negative integer, got {contracts!r}")
    if not math.isfinite(contracts) or contracts < 0 or contracts != math.floor(contracts):
        raise ValueError(f"contracts must be a non-negative integer, got {contracts!r}")
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        raise ValueError(f"price must be a number in [0, 1] dollars, got {price!r}")
    if not math.isfinite(price) or not 0.0 <= price <= 1.0:
        raise ValueError(f"price must lie in [0, 1] dollars, got {price!r}")
    if rate is None:
        raise FeeRateUnresolved(
            "rate is missing (None): a fee rate must be threaded from the fee "
            "book, never defaulted. A SOURCED zero is a legal rate — pass 0.0 "
            "for it — but an absent rate is not a zero rate"
        )
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate):
        raise ValueError(f"rate must be a finite number, got {rate!r}")
    if rate < 0.0:
        raise ValueError(f"rate must be non-negative, got {rate!r}")
    return float(contracts), float(price), float(rate)


def kalshi_trading_fee(contracts, price, rate):
    """Compute Kalshi's trading fee in dollars, rounded UP to the next cent.

    Parameters
    ----------
    contracts : int
        Number of contracts in the fill, ``C`` (non-negative).
    price : float
        Trade price per contract in dollars, in ``[0, 1]``.
    rate : float
        The series' fee rate (e.g. 0.07 for a general taker). Required —
        ``None`` refuses; ``0.0`` is a legal sourced rate.

    Returns
    -------
    float
        ``ceil_to_cent(rate · C · P · (1 − P))``; zero at ``P ∈ {0, 1}``,
        at least one cent for any other positive quantity.

    Raises
    ------
    ValueError
        On a negative or fractional contract count, a price outside
        ``[0, 1]``, or a negative rate.
    FeeRateUnresolved
        When ``rate`` is ``None``.
    """
    c, p, r = _check_order(contracts, price, rate)
    raw_cents = r * c * p * (1.0 - p) * 100.0
    return math.ceil(round(raw_cents, CENT_ROUNDING_DECIMALS)) / 100.0


def poly_trading_fee(contracts, price, rate):
    """Compute Polymarket's trading fee in dollars, rounded to the nearest 1e-5.

    Parameters
    ----------
    contracts : int
        Number of shares in the fill (non-negative).
    price : float
        Price per share in dollars, in ``[0, 1]``.
    rate : float
        The market's fee rate (weather 0.05, crypto 0.07, a venue-declared
        0.0 before fees were enabled, …). Required — ``None`` refuses.

    Returns
    -------
    float
        The fee on the 1e-5 grid, half-up at a tie; no per-order floor.

    Raises
    ------
    ValueError
        On a negative or fractional share count, a price outside
        ``[0, 1]``, or a negative rate.
    FeeRateUnresolved
        When ``rate`` is ``None``.
    """
    c, p, r = _check_order(contracts, price, rate)
    scale = 10.0**POLY_FEE_DECIMALS
    raw_units = r * c * p * (1.0 - p) * scale
    tie_tol = max(0.5 * 10.0**-CENT_ROUNDING_DECIMALS, abs(raw_units) * POLY_TIE_RELATIVE)
    return math.floor(raw_units + 0.5 + tie_tol) / scale


#: venue -> its rounding model. A third venue is a row here (beside its
#: prefix row in ``VENUE_PREFIXES``); ``trading_fee_for_series`` refuses
#: a venue with no row rather than guessing a rounding rule.
FEE_ROUNDING = {
    "kalshi": kalshi_trading_fee,
    "polymarket": poly_trading_fee,
}


def trading_fee_for_series(series, contracts, price, rate):
    """Compute the venue-dispatched trading fee — the single fill-time entry point.

    Parameters
    ----------
    series : str
        Series or market ticker; the venue comes from its prefix.
    contracts : int
        Number of contracts.
    price : float
        Price per contract in dollars.
    rate : float
        The threaded rate. Required for every venue.

    Returns
    -------
    float
        The fee under the ticker's venue rounding model.

    Raises
    ------
    ValueError
        On an unattributable ticker, or any fill the model refuses.
    FeeRateUnresolved
        When no rate was threaded.
    """
    venue = venue_of(series)
    model = FEE_ROUNDING.get(venue)
    if model is None:
        raise ValueError(
            f"venue {venue!r} of {series!r} has no fee rounding model — "
            f"FEE_ROUNDING declares {sorted(FEE_ROUNDING)} (VENUES: {list(VENUES)})"
        )
    return model(contracts, price, rate)


def fill_cost_for_series(series, contracts, price, rate):
    """Compute the total cash outlay for a fill: premium plus the venue's fee.

    Parameters
    ----------
    series : str
        Series or market ticker.
    contracts : int
        Number of contracts.
    price : float
        Price per contract in dollars.
    rate : float
        The threaded rate.

    Returns
    -------
    float
        ``contracts · price + trading_fee_for_series(...)``.
    """
    fee = trading_fee_for_series(series, contracts, price, rate)
    return float(contracts) * float(price) + fee


def close_ts_of(close_ms):
    """Spell the ISO-8601 UTC close instant a fee case is tested against.

    Parameters
    ----------
    close_ms : int
        The market's close (settle) instant, Unix milliseconds.

    Returns
    -------
    str
        ``"2026-03-30T12:00:00Z"`` — second precision, ``Z``-suffixed, so
        it compares lexicographically against the bounds a document declares.
    """
    return _dt.datetime.fromtimestamp(int(close_ms) / 1000.0, tz=_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


class FeeCase:
    """One declared case of a series' fee history.

    Parameters
    ----------
    when : sequence of dict
        Clauses (``{"field", "op", "value"}``) that must ALL hold. Empty is
        the explicit catch-all and must be the last case.
    rate : float or None
        The rate this case declares; ``0.0`` is a legal sourced value.
        ``None`` means UNPRICEABLE — matching it raises.
    fee_type : str or None
        The venue's own name for the schedule (``"weather_fees"``) — the
        audit trail of a regime change.
    unpriceable : str or None
        Why ``rate`` is ``None``, quoted verbatim into the refusal.
        Required exactly when ``rate`` is ``None``.

    Examples
    --------
    A weather series free before its switch::

        case = FeeCase(
            [{"field": "close_ts", "op": "<", "value": "2026-03-30T12:00:00Z"}],
            0.0, fee_type=None,
        )
        case.holds({"close_ts": "2026-03-01T00:00:00Z"})   # True
    """

    __slots__ = ("when", "rate", "fee_type", "unpriceable")

    def __init__(self, when, rate, fee_type=None, unpriceable=None):
        self.when = tuple(when)
        self.rate = None if rate is None else float(rate)
        self.fee_type = fee_type
        self.unpriceable = unpriceable
        if self.rate is not None and not 0.0 <= self.rate < 1.0:
            raise ValueError(
                f"fee case rate must lie in [0, 1) (got {self.rate!r}); "
                "0.0 is legal, negative and >= 1 are not"
            )
        if self.rate is None and not self.unpriceable:
            raise ValueError(
                "a fee case with value=null must say WHY via 'unpriceable' — "
                "an unexplained hole is indistinguishable from a typo"
            )

    def holds(self, row):
        """Say whether every clause of this case holds for ``row``.

        Parameters
        ----------
        row : dict
            The row under test — ``{CLOSE_TS_FIELD: <iso instant>}`` or
            ``{}`` when no close instant is known (every dated clause then
            fails; only a catch-all matches).

        Returns
        -------
        bool
            True when all clauses hold (vacuously for a catch-all).
        """
        return all(clause_holds(row, clause) for clause in self.when)

    def __repr__(self):
        """Spell the case for a log line."""
        shown = "UNPRICEABLE" if self.rate is None else self.rate
        return f"FeeCase(when={list(self.when)}, rate={shown}, {self.fee_type})"


class FeeBook(Mapping):
    """The merged fee table: per series, a scalar or a list of dated cases.

    A book built from a plain ``{series: rate}`` document behaves as that
    dict — ``book[series]``, ``series in book``, ``dict(book)``, iteration
    and length are identical — so a scalar document is untouched. A dated
    series has no single value, so ``book[series]`` REFUSES for it and
    points at :meth:`rate_for`; the alternative is silently answering
    with one of several rates.

    Parameters
    ----------
    scalars : dict
        ``series -> rate`` for the scalar entries.
    cases : dict
        ``series -> tuple of FeeCase`` for the dated entries.
    provenance : dict
        ``series -> {source, retrieved, ...}`` as declared beside the cases.

    Examples
    --------
    Both shapes in one book, resolved at a market's close::

        book = FeeBook.from_document({
            "KXHIGHDEN": 0.07,
            "POLYWXHINYC": {"cases": [
                {"when": [{"field": "close_ts", "op": "<",
                           "value": "2026-03-30T12:00:00Z"}], "value": 0.0},
                {"when": [{"field": "close_ts", "op": ">=",
                           "value": "2026-03-30T12:00:00Z"}], "value": 0.05},
            ]},
        })
        book.rate_for("KXHIGHDEN")                                   # 0.07
        book.rate_for("POLYWXHINYC", close_ms=1_774_872_000_000)     # 0.05
    """

    __slots__ = ("_scalars", "_cases", "_provenance")

    def __init__(self, scalars, cases, provenance):
        self._scalars = dict(scalars)
        self._cases = {k: tuple(v) for k, v in cases.items()}
        self._provenance = dict(provenance)

    @classmethod
    def from_document(cls, declared):
        """Parse a ``fee_rate_by_series`` value into a :class:`FeeBook`.

        Parameters
        ----------
        declared : Mapping or FeeBook
            Scalar entries (a number), dated entries (a mapping carrying a
            ``cases`` list plus provenance keys, or a bare list of cases),
            or an existing book (returned unchanged, so wiring can call
            this idempotently).

        Returns
        -------
        FeeBook
            The parsed book.

        Raises
        ------
        ValueError
            On a non-mapping, a rate outside ``[0, 1)``, an empty case list,
            a malformed clause, an unexplained null value, or a catch-all
            that is not the last case.
        """
        if isinstance(declared, FeeBook):
            return declared
        if not isinstance(declared, Mapping):
            raise ValueError(
                "fee_rate_by_series must be a mapping of series -> rate or "
                f"series -> dated cases (got {type(declared).__name__})"
            )
        scalars, cases, provenance = {}, {}, {}
        for series, entry in declared.items():
            if isinstance(entry, (int, float)) and not isinstance(entry, bool):
                rate = float(entry)
                if not 0.0 <= rate < 1.0:
                    raise ValueError(
                        f"fee rate for {series!r} must lie in [0, 1) (got {rate})"
                    )
                scalars[series] = rate
                continue
            if isinstance(entry, Mapping):
                rows = entry.get("cases")
                provenance[series] = {k: v for k, v in entry.items() if k != "cases"}
            elif isinstance(entry, (list, tuple)):
                rows, provenance[series] = entry, {}
            else:
                raise ValueError(
                    f"fee_rate_by_series[{series!r}] must be a number or a list "
                    f"of dated cases (got {type(entry).__name__})"
                )
            if not rows:
                raise ValueError(
                    f"fee_rate_by_series[{series!r}] declares no cases — an "
                    "empty table prices nothing; remove the series or fill it in"
                )
            cases[series] = cls._parse_cases(series, rows)
        return cls(scalars, cases, provenance)

    @staticmethod
    def _parse_cases(series, rows):
        """Parse and shape-check one series' case list, in declaration order."""
        where = f"fee_rate_by_series[{series!r}].cases"
        out = []
        for i, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise ValueError(f"{where}[{i}] must be a mapping (got {type(row).__name__})")
            if "when" not in row or not isinstance(row["when"], (list, tuple)):
                raise ValueError(
                    f"{where}[{i}].when must be a list of clauses (empty = the "
                    "explicit catch-all)"
                )
            if "value" not in row:
                raise ValueError(
                    f"{where}[{i}] has no 'value' — refusing to guess (use "
                    "value:null + 'unpriceable' to declare a span this model "
                    "cannot price)"
                )
            for j, clause in enumerate(row["when"]):
                problems = clause_problems(f"{where}[{i}].when[{j}]", clause)
                if problems:
                    raise ValueError("; ".join(problems))
            if not row["when"] and i != len(rows) - 1:
                raise ValueError(
                    f"{where}[{i}] has an empty 'when', so it matches EVERY "
                    f"close date, but {len(rows) - 1 - i} case(s) follow it and "
                    "can never fire — a catch-all must be the LAST case"
                )
            out.append(
                FeeCase(
                    row["when"],
                    row["value"],
                    fee_type=row.get("fee_type"),
                    unpriceable=row.get("unpriceable"),
                )
            )
        return out

    def rate_for(self, series, close_ms=None):
        """Resolve the rate for one series at one market's CLOSE instant.

        Parameters
        ----------
        series : str
            Series ticker.
        close_ms : int or None
            The close (settle) instant of the market being priced, Unix
            ms. Ignored for a scalar series; REQUIRED for a dated one
            (only a catch-all can answer without it).

        Returns
        -------
        float
            The rate; ``0.0`` is a legal, sourced answer.

        Raises
        ------
        FeeRateUnresolved
            Series absent; a dated series with no ``close_ms``; no case
            claiming that instant; or a case declared unpriceable.
        """
        if series in self._scalars:
            return self._scalars[series]
        cases = self._cases.get(series)
        if cases is None:
            raise FeeRateUnresolved(
                f"no fee rate declared for series {series!r} — every priced "
                "series needs its own entry in fee_rate_by_series; a missing "
                "rate is never a default"
            )
        row = {} if close_ms is None else {CLOSE_TS_FIELD: close_ts_of(close_ms)}
        for case in cases:
            if not case.holds(row):
                continue
            if case.rate is None:
                raise FeeRateUnresolved(
                    f"series {series!r}: the fee schedule in force for a market "
                    f"closing {row.get(CLOSE_TS_FIELD)} (fee_type="
                    f"{case.fee_type!r}) is not priceable by this model: "
                    f"{case.unpriceable}"
                )
            return case.rate
        if close_ms is None:
            raise FeeRateUnresolved(
                f"series {series!r} has a TIME-VARYING fee schedule "
                f"({len(cases)} cases) and cannot be resolved without the "
                "market's close instant — pass close_ms=<market close, Unix ms>"
            )
        raise FeeRateUnresolved(
            f"series {series!r}: no declared fee case claims a market closing "
            f"{row[CLOSE_TS_FIELD]}. The book is FAIL-CLOSED — it takes neither "
            "the nearest case nor an extrapolation past the window it was "
            "acquired over; re-acquire the book over a window that covers "
            "this market, or declare the case"
        )

    def at(self, close_ms):
        """Resolve the whole book to a plain ``{series: rate}`` dict at one close instant.

        Series no case claims at this instant — or whose case is
        unpriceable — are OMITTED, so a consumer's own missing-rate guard
        refuses them by name rather than this method inventing a number.

        Parameters
        ----------
        close_ms : int
            The close instant, Unix ms.

        Returns
        -------
        dict
            ``series -> rate`` for every series resolvable at ``close_ms``.
        """
        out = dict(self._scalars)
        for series in self._cases:
            try:
                out[series] = self.rate_for(series, close_ms)
            except FeeRateUnresolved:
                continue
        return out

    def is_time_varying(self, series):
        """Say whether ``series`` needs a close instant to resolve (bool)."""
        return series in self._cases

    @property
    def time_varying_series(self):
        """List (sorted tuple) the series that cannot resolve without a close."""
        return tuple(sorted(self._cases))

    def cases_for(self, series):
        """Give the parsed case tuple for ``series`` (empty for a scalar entry)."""
        return self._cases.get(series, ())

    def provenance_for(self, series):
        """Give the declared provenance (``source``, ``retrieved``, …) for ``series``."""
        return dict(self._provenance.get(series, {}))

    def __getitem__(self, series):
        """Answer a scalar series' rate; refuse a dated one by name."""
        if series in self._scalars:
            return self._scalars[series]
        if series in self._cases:
            raise FeeRateUnresolved(
                f"series {series!r} has a time-varying fee schedule; a bare "
                "lookup would silently pick one of several rates — use "
                "rate_for(series, close_ms) or at(close_ms)"
            )
        raise KeyError(series)

    def __iter__(self):
        """Iterate the series, scalars first."""
        yield from self._scalars
        yield from self._cases

    def __len__(self):
        """Count the series in the book."""
        return len(self._scalars) + len(self._cases)

    def __repr__(self):
        """Spell the book for a log line."""
        return f"FeeBook({len(self._scalars)} scalar, {len(self._cases)} time-varying)"


def resolve_fee_rates(declared, series_list, *, close_ms_by=None, where="params"):
    """Resolve a declared fee table for exactly ``series_list``, or refuse.

    Parameters
    ----------
    declared : Mapping or FeeBook
        The document's ``fee_rate_by_series`` / merged ``fee_book`` table.
    series_list : iterable of str
        Series that must be priced.
    close_ms_by : Mapping of str -> int, optional
        ``series -> close instant (Unix ms)`` for the market being priced;
        consulted only for dated series.
    where : str
        Names the document block in the refusal.

    Returns
    -------
    dict
        ``series -> rate``.

    Raises
    ------
    FeeRateUnresolved
        Naming every series that could not be resolved and why.
    """
    book = FeeBook.from_document(declared)
    closes = dict(close_ms_by or {})
    out, problems = {}, []
    for series in series_list:
        if series in out:
            continue
        try:
            out[series] = book.rate_for(series, closes.get(series))
        except FeeRateUnresolved as exc:
            problems.append(f"  {series}: {exc}")
    if problems:
        raise FeeRateUnresolved(
            f"{where}['fee_rate_by_series'] cannot price {len(problems)} "
            "series:\n" + "\n".join(problems)
        )
    return out


#: The clause operators a fee case may use — the document DSL's own table,
#: re-exported so a config author reading this module sees the vocabulary.
FEE_CLAUSE_OPS = tuple(sorted(CLAUSE_OPS))
