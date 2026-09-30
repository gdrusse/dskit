"""Exact, synthetic European cash-index contracts and intact condor cashflows.

This is domain validation, not a pricing, ingestion or execution engine.
Immutable snapshots prevent caller mutation from changing a validated position.

The quote, credit and American-exercise rules the archived-quote backtest
shares with the synthetic track are module functions (ADR-0187):
:func:`quote_problems`, :func:`condor_credit` and
:func:`american_short_charge`. :class:`DefinedRiskCondor` calls the first
two and keeps raising on a bad quote or credit; a backtest classifies the
same problems instead.

ADR-0193 generalizes them from the condor to a leg set. :func:`structure_credit`
owns the bid/ask credit rule for any leg tuple and :func:`condor_credit` is
its call on :data:`CONDOR_LEGS`; :data:`PUT_SPREAD_LEGS` is the condor's put
wing, :data:`CALL_SPREAD_LEGS` its call wing (ADR-0195) and :data:`STRUCTURES`
names the three. The American charge splits into :func:`american_put_carry` (needs no
dividend data) and :func:`american_call_dividend`, which
:func:`american_short_charge` composes; :func:`dividends_paid` is the
dividend rule a hedge benchmark shares with the call charge.

ADR-0197 adds the debit structures' leg sets (:data:`LONG_STRADDLE_LEGS`,
:data:`LONG_CALL_SPREAD_LEGS`, :data:`LONG_PUT_SPREAD_LEGS`: what the credit
structures sell, bought) and the two payoff-side owners they need:
:func:`structure_payoff`, the leg-sum moved here beside :func:`leg_intrinsic` and
:func:`structure_credit` (``index_options.distribution`` re-exports it), and
:func:`structure_max_loss`, the one rule for the most a structure can lose.
"""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, Inexact, InvalidOperation, localcontext
from itertools import pairwise
import re
from types import MappingProxyType

from dskit.onboarding.base import parse_utc
from dskit.pipeline.node import check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok, price_ok

__all__ = [
    "CALL_SPREAD_LEGS",
    "CONDOR_LEGS",
    "LONG_CALL_SPREAD_LEGS",
    "LONG_PUT_SPREAD_LEGS",
    "LONG_STRADDLE_LEGS",
    "PUT_SPREAD_LEGS",
    "STRUCTURES",
    "CashIndexContract",
    "DefinedRiskCondor",
    "american_call_dividend",
    "american_put_carry",
    "american_short_charge",
    "condor_credit",
    "dividends_paid",
    "leg_intrinsic",
    "quote_problems",
    "structure_credit",
    "structure_max_loss",
    "structure_payoff",
]

#: The one owner of an iron condor's leg order and signed quantity: long
#: put, short put, short call, long call. The standardized geometry, the
#: cashflow owner and both backtests read it; none restates it.
CONDOR_LEGS = (("put", 1), ("put", -1), ("call", -1), ("call", 1))
#: The condor's put wing, a long put under a short put (ADR-0193): a prefix of
#: :data:`CONDOR_LEGS`, so the two structures share one leg order.
PUT_SPREAD_LEGS = CONDOR_LEGS[:2]
#: The condor's call wing, a short call under a long call (ADR-0195): the last two legs of
#: :data:`CONDOR_LEGS`.
CALL_SPREAD_LEGS = CONDOR_LEGS[2:]
#: The defined-risk structures a payoff selector chooses among, by name (ADR-0195): the one
#: owner of the names and of their leg tuples, read-only. Its order is the default tie-break.
STRUCTURES = MappingProxyType({"put_spread": PUT_SPREAD_LEGS, "call_spread": CALL_SPREAD_LEGS,
                               "condor": CONDOR_LEGS})
#: A long straddle, a long put and a long call at ONE strike (ADR-0197): both legs carry the same
#: strike, so a strike list for it is ``(K, K)``. It has no vertical pair, hence no width.
LONG_STRADDLE_LEGS = (("put", 1), ("call", 1))
#: A long call spread, a long call under a short call (ADR-0197), strikes low to high like
#: :data:`CONDOR_LEGS`: the bought call is the LOWER strike.
LONG_CALL_SPREAD_LEGS = (("call", 1), ("call", -1))
#: A long put spread, a short put under a long put (ADR-0197), strikes low to high like
#: :data:`CONDOR_LEGS`: the bought put is the HIGHER strike.
LONG_PUT_SPREAD_LEGS = (("put", -1), ("put", 1))
_LEG_SIGNS = tuple(sign for _right, sign in CONDOR_LEGS)
#: The side a quote is judged for: both sizes, a sale (bid) or a purchase (ask).
_SIDES = (None, "sell", "buy")

_SCHEMA = "index-options-synthetic-v1"
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_COMMON = (
    "schema_version", "corpus_id", "row_version", "provenance",
    "effective_at", "known_at", "known_at_basis",
)


def _decimal(value, name):
    """Read a finite base-ten option amount without float coercion."""
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise ValueError(f"{name} must be a decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a finite decimal string") from exc
    if not amount.is_finite():
        raise ValueError(f"{name} must be finite")
    return amount


def _integer(value, name, minimum):
    """Apply the shared bound, with the domain's stricter JSON integer type."""
    problems = []
    check_int_param(problems, name, value, ge=minimum)
    if not isinstance(value, int):
        problems.append(f"{name} must use an integer, not a float or string")
    if problems:
        raise ValueError("; ".join(problems))
    return value


def _instant(value, name):
    """Require a qualified instant before using the shared UTC parser."""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a timezone-qualified ISO instant")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("timezone is required")
        return parse_utc(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a timezone-qualified ISO instant") from exc


def _day(value, name):
    """Require an explicit ISO calendar date, not a guessed expiry."""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must use YYYY-MM-DD")
    return parsed


def _text(value, name):
    """Require a nonempty identity component."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def leg_intrinsic(right, strike, level):
    """Return one European cash-settled leg's payoff per unit at ``level``.

    The one owner of the put/call intrinsic rule: exact Decimal cashflows
    and float scenario payoffs both call it.

    Parameters
    ----------
    right : str
        ``"put"`` or ``"call"``.
    strike, level : Decimal or float
        Strike and settlement level, of one numeric type.

    Returns
    -------
    Decimal or float
        ``max(strike - level, 0)`` for a put, ``max(level - strike, 0)``
        for a call, in the inputs' type.
    """
    gap = strike - level if right == "put" else level - strike
    return max(type(gap)(0), gap)


def structure_payoff(legs, level, strikes):
    """Return a leg set's settlement payoff per unit, credit excluded.

    The one owner of the leg-sum (ADR-0193, generalized from the condor):
    each leg contributes ``sign x`` its intrinsic value at ``level``. It lives
    here, beside :func:`leg_intrinsic` and :func:`structure_credit`, so that
    :func:`structure_max_loss` can call it (ADR-0197);
    ``index_options.distribution`` re-exports it.

    Parameters
    ----------
    legs : sequence of (str, int)
        ``(right, signed quantity)`` per leg, e.g. :data:`CONDOR_LEGS`.
    level : float
        Settlement level.
    strikes : sequence of float
        One strike per leg, in leg order.

    Returns
    -------
    float
        The signed sum: ``0`` when every leg expires worthless, negative
        when the short legs finish in the money.

    Raises
    ------
    ValueError
        When ``strikes`` do not match ``legs`` in number.

    Examples
    --------
    A put credit spread settling between its strikes::

        structure_payoff(PUT_SPREAD_LEGS, 93.0, (90.0, 95.0))
        # -> -2.0
    """
    return sum(sign * leg_intrinsic(right, k, level)
               for (right, sign), k in zip(legs, strikes, strict=True))


def structure_max_loss(legs, strikes, credit_usd, multiplier):
    """Return the most a leg set can lose at settlement, in USD, credit included.

    The one owner of the maximum-loss rule (ADR-0197). The settlement P&L is
    ``credit_usd + multiplier x`` :func:`structure_payoff`, piecewise linear in
    the settlement level with its kinks at ``0`` and at every strike, so its
    minimum over levels ``>= 0`` lies on one of those points unless it keeps
    falling above the top strike, which it does exactly when the calls' signed
    quantities sum negative (a naked short call, a short strangle, a ratio):
    then the loss is unbounded. For the credit structures it equals
    ``multiplier x`` the widest vertical less the credit, and for a long
    structure it is the debit.

    Parameters
    ----------
    legs : sequence of (str, int)
        ``(right, signed quantity)`` per leg, at least one.
    strikes : sequence of Decimal or float
        One positive strike per leg, in leg order.
    credit_usd : Decimal or float
        The cash received at entry in USD, net of fees; negative for a debit.
    multiplier : int
        Shares per contract, >= 1.

    Returns
    -------
    Decimal, float or float("inf")
        ``-min`` of the settlement P&L, in the strikes' type; negative when
        the structure cannot lose (a free lunch), ``math.inf`` when the
        loss is unbounded.

    Raises
    ------
    ValueError
        When there are no legs, ``strikes`` do not match them, a strike is not
        a positive number, ``credit_usd`` is not finite or ``multiplier`` is not
        an int >= 1.

    Examples
    --------
    An iron condor with a 5-point put wing and a 10-point call wing, credit 150::

        structure_max_loss(CONDOR_LEGS, (90.0, 95.0, 105.0, 115.0), 150.0, 100)
        # -> 850.0
    """
    if not legs:
        raise ValueError("a structure needs at least one leg")
    _integer(multiplier, "multiplier", 1)
    if not _amount(credit_usd):
        raise ValueError(f"credit_usd must be a finite number, got {credit_usd!r}")
    if not all(_amount(k) and k > 0 for k in strikes):
        raise ValueError(f"strikes must be positive finite numbers, got {strikes!r}")
    if sum(sign for right, sign in legs if right == "call") < 0:
        return math.inf
    return -min(credit_usd + multiplier * structure_payoff(legs, level, strikes)
                for level in (0, *strikes))


def _amount(value):
    """Say whether ``value`` is a finite Decimal or a finite non-bool number."""
    if isinstance(value, Decimal):
        return value.is_finite()
    return number_ok(value)


def _size(value):
    """Say whether ``value`` is a usable quote size: a finite non-bool number >= 0."""
    return number_ok(value) and value >= 0


def quote_problems(bid, ask, bid_size, ask_size, count, side=None):
    """List what stops a quote from filling ``count`` contracts on ``side``.

    The one owner of the quote rules (ADR-0187): a quote is nonnegative and
    uncrossed; with ``side=None`` both sizes must cover ``count``; a sale
    (``"sell"``) needs a positive bid and a bid size that covers it; a
    purchase (``"buy"``) needs a positive ask and an ask size that covers
    it. A provider ``0`` on the side traded means no market, never a free
    fill — while a far-strike wing with no bid stays buyable.

    Parameters
    ----------
    bid, ask : Decimal or float
        The quote, in one numeric family.
    bid_size, ask_size : int or float
        The sizes as the source sends them.
    count : int
        Contracts to fill, >= 0; ``0`` is the row-level rule (a valid
        quote, nothing to cover).
    side : None, "sell" or "buy"
        Which side is traded; ``None`` judges both sizes.

    Returns
    -------
    list of str
        Every problem found; empty when the quote fills.

    Raises
    ------
    ValueError
        When ``side`` or ``count`` is not one of the above.
    """
    if side not in _SIDES:
        raise ValueError(f"side must be one of {_SIDES}, got {side!r}")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError(f"count must be an int >= 0, got {count!r}")
    problems = []
    for name, value in (("bid", bid), ("ask", ask)):
        if not _amount(value):
            problems.append(f"{name} must be a finite number, got {value!r}")
    if not problems:
        if bid < 0 or ask < 0:
            problems.append(f"quote must be nonnegative, got bid {bid} ask {ask}")
        elif ask < bid:
            problems.append(f"quote must be uncrossed, got bid {bid} above ask {ask}")
        if side == "sell" and not bid > 0:
            problems.append(f"bid must be positive to sell, got {bid}")
        if side == "buy" and not ask > 0:
            problems.append(f"ask must be positive to buy, got {ask}")
    for name, value in (("bid_size", bid_size), ("ask_size", ask_size)):
        if side is not None and name != f"{'bid' if side == 'sell' else 'ask'}_size":
            continue
        if not _size(value):
            problems.append(f"{name} must be a number >= 0, got {value!r}")
        elif value < count:
            problems.append(f"{name} {value} does not cover count {count}")
    return problems


def structure_credit(legs, strikes, quotes):
    """Return a leg set's per-share entry credit and its vertical widths.

    Short legs sell at the bid, long legs buy at the ask — the one owner of
    that rule (ADR-0187, generalized from the condor to any leg tuple by
    ADR-0193). It never raises on the answer: the cashflow owner refuses a
    credit outside ``(0, narrower width)`` and a backtest classifies it.

    A *vertical pair* is two consecutive legs of one right with opposite
    signs (one long, one short, in either order); its width is the later
    leg's strike less the earlier leg's, in leg order, so it is positive
    when the strikes ascend as the tuple lists them. Legs of two rights, or
    of one sign, pair with nothing.

    Parameters
    ----------
    legs : sequence of (str, int)
        ``(right, signed quantity)`` per leg, e.g. :data:`CONDOR_LEGS`.
    strikes : sequence of Decimal or float
        One strike per leg, in leg order.
    quotes : sequence of (bid, ask)
        One pair per leg, in leg order and the strikes' numeric family.

    Returns
    -------
    tuple
        ``(credit, widths)``: the credit, and a tuple with one width per
        vertical pair in leg order (empty when there is none).

    Raises
    ------
    ValueError
        When ``strikes`` or ``quotes`` do not match ``legs`` in number.

    Examples
    --------
    A put credit spread, short the 100 at its bid, long the 95 at its ask::

        credit, widths = structure_credit(PUT_SPREAD_LEGS, (95.0, 100.0),
                                          ((1.0, 1.1), (2.0, 2.2)))
        # -> credit 0.9 (2.0 - 1.1), widths (5.0,)
    """
    placed = list(zip(legs, strikes, quotes, strict=True))
    credit = sum(-sign * (ask if sign > 0 else bid)
                 for (_right, sign), _strike, (bid, ask) in placed)
    widths = tuple(later - earlier
                   for ((right, sign), earlier, _quote), ((next_right, next_sign), later, _next)
                   in pairwise(placed) if right == next_right and sign * next_sign < 0)
    return credit, widths


def condor_credit(strikes, quotes):
    """Return a condor's per-share entry credit and both wing widths.

    :func:`structure_credit` on :data:`CONDOR_LEGS`: shorts at the bid, longs
    at the ask (ADR-0187).

    Parameters
    ----------
    strikes : sequence of Decimal or float
        Long put, short put, short call, long call.
    quotes : sequence of (bid, ask)
        One pair per leg, in the same order and numeric family.

    Returns
    -------
    tuple
        ``(credit, (put_width, call_width))``.

    Raises
    ------
    ValueError
        When ``strikes`` or ``quotes`` are not four long.
    """
    return structure_credit(CONDOR_LEGS, strikes, quotes)


def _charge_dates(entry_date, settle_date):
    """Parse the charge window's ISO dates and require it to run forward: ``(entry, settle)``."""
    entry, settle = _day(entry_date, "entry_date"), _day(settle_date, "settle_date")
    if settle <= entry:
        raise ValueError(f"settle_date {settle_date} must follow entry_date {entry_date}")
    return entry, settle


def _charge_strike(name, strike):
    """Require a short strike to be a positive number."""
    if not price_ok(strike):
        raise ValueError(f"{name} must be a positive number, got {strike!r}")


def _charge_rows(rows, entry, settle):
    """Return the ``[entry, settle]`` sessions oldest first as ``(date, row)``, closes checked."""
    window = []
    for row in rows:
        day = _day(row["date"], "date")
        if entry <= day <= settle:
            if not price_ok(row.get("close")):
                raise ValueError(f"close on {row['date']} must be a positive number, "
                                 f"got {row.get('close')!r}")
            window.append((day, row))
    window.sort(key=lambda pair: pair[0])
    if not window:
        raise ValueError(f"no closes between {entry.isoformat()} and {settle.isoformat()}")
    return window


def _session_dividend(row):
    """Return one session's ex-date cash per share, refusing an unusable amount."""
    dividend = row.get("dividend_amount")
    if not number_ok(dividend) or dividend < 0:
        raise ValueError(f"dividend_amount on {row['date']} must be a finite "
                         f"number >= 0, got {dividend!r}")
    return dividend


def american_put_carry(rows, short_put, entry_date, settle_date, carry_rate, multiplier):
    """Return the conservative early-exercise carry on a short put.

    ETF options are American and physically settled (ADR-0187). From the
    first close ``s`` in ``[entry_date, settle_date)`` below ``short_put``,
    the position is assumed long the stock at ``short_put``, and
    ``short_put x (exp(carry_rate x tau) - 1)`` with
    ``tau = (settle_date - s) / 365`` is charged. It ignores what assignment
    would gain (forfeited extrinsic value, our own long legs, dividends on
    assigned stock), so the bound is conservative. It reads closes only:
    no dividend data is needed (ADR-0193), so a put-only structure runs
    where none exists.

    Parameters
    ----------
    rows : list of dict
        ``date`` (ISO) and ``close`` (positive) per session; rows outside
        ``[entry_date, settle_date]`` are ignored.
    short_put : float
        The short put strike.
    entry_date, settle_date : str
        ISO dates; the window is ``[entry_date, settle_date]``.
    carry_rate : float
        A constant upper bound on the cash rate, >= 0.
    multiplier : int
        Shares per contract.

    Returns
    -------
    float
        The carry in USD per contract, ``0.0`` when no close is below the
        strike.

    Raises
    ------
    ValueError
        When the window is empty or inverted, the rate or strike is
        unusable, or a close in the window is not a positive number.

    Examples
    --------
    A put that closes in the money four days before settlement::

        carry = american_put_carry(rows, 95.0, "2024-03-01", "2024-03-08", 0.055, 100)
        # -> 95.0 * (exp(0.055 * 4 / 365) - 1) * 100 when 03-04 is the first close below 95
    """
    entry, settle = _charge_dates(entry_date, settle_date)
    if not number_ok(carry_rate) or carry_rate < 0:
        raise ValueError(f"carry_rate must be a finite number >= 0, got {carry_rate!r}")
    _charge_strike("short_put", short_put)
    _integer(multiplier, "multiplier", 1)
    for day, row in _charge_rows(rows, entry, settle):
        if day < settle and row["close"] < short_put:
            tau = (settle - day).days / 365
            return short_put * (math.exp(carry_rate * tau) - 1) * multiplier
    return 0.0


def american_call_dividend(rows, short_call, entry_date, settle_date, multiplier):
    """Return the conservative early-assignment dividend cost of a short call.

    ETF options are American and physically settled (ADR-0187). The dividend
    is charged for the first ex-date in ``(entry_date, settle_date]`` whose
    pre-ex close (the previous session's close) is above ``short_call``, and
    for every later ex-date in the window, because from then on the position
    is assumed short the stock. It ignores what assignment would gain, so
    the bound is conservative.

    Parameters
    ----------
    rows : list of dict
        ``date`` (ISO), ``close`` (positive) and ``dividend_amount`` (the
        ex-date cash, ``0`` otherwise) per session; rows outside the window
        are ignored.
    short_call : float
        The short call strike.
    entry_date, settle_date : str
        ISO dates; the window is ``[entry_date, settle_date]``.
    multiplier : int
        Shares per contract.

    Returns
    -------
    float
        The dividends charged, in USD per contract.

    Raises
    ------
    ValueError
        When the window is empty or inverted, the strike is unusable, a
        close is not a positive number, or a session after the entry and up
        to settlement carries no usable ``dividend_amount`` (``None``
        refuses, it is never skipped).

    Examples
    --------
    A 1.50 dividend whose pre-ex close is above the 102 strike::

        cost = american_call_dividend(rows, 102.0, "2024-03-01", "2024-03-08", 100)
        # -> 150.0
    """
    entry, settle = _charge_dates(entry_date, settle_date)
    _charge_strike("short_call", short_call)
    _integer(multiplier, "multiplier", 1)
    call, assigned, previous_close = 0.0, False, None
    for day, row in _charge_rows(rows, entry, settle):
        if day > entry:
            dividend = _session_dividend(row)
            if dividend > 0 and (assigned or (previous_close is not None
                                              and previous_close > short_call)):
                assigned = True
                call += dividend
        previous_close = row["close"]
    return call * multiplier


def dividends_paid(rows, entry_date, settle_date):
    """Return the ex-date cash per share paid in ``(entry_date, settle_date]``.

    What a holder of the stock across the window receives: the same session
    rule and the same refusal of an unusable ``dividend_amount`` as
    :func:`american_call_dividend`, with no assignment logic.

    Parameters
    ----------
    rows : list of dict
        ``date`` (ISO), ``close`` (positive) and ``dividend_amount`` per
        session; rows outside ``[entry_date, settle_date]`` are ignored.
    entry_date, settle_date : str
        ISO dates; the entry day's own ex-date is not paid to a holder from
        that close on.

    Returns
    -------
    float
        The sum, ``0.0`` when there is none.

    Raises
    ------
    ValueError
        When the window is empty or inverted, a close is not a positive
        number, or a session after the entry carries no usable
        ``dividend_amount``.

    Examples
    --------
    One ex-date inside the window::

        paid = dividends_paid(rows, "2024-03-01", "2024-03-08")
        # -> 0.5 when only 03-04 paid 0.5
    """
    entry, settle = _charge_dates(entry_date, settle_date)
    return sum((_session_dividend(row) for day, row in _charge_rows(rows, entry, settle)
                if day > entry), 0.0)


def american_short_charge(rows, short_put, short_call, entry_date, settle_date,
                          carry_rate, multiplier):
    """Return the conservative early-exercise charge on a condor's two short legs.

    ETF options are American and physically settled (ADR-0187). Each short
    leg is valued at its European payoff on ``settle_date`` minus a one-sided
    bound on what early assignment can cost; ADR-0193 split the two bounds
    into :func:`american_call_dividend` (the short call, dividends) and
    :func:`american_put_carry` (the short put, carry), which this composes.
    Both ignore what assignment would gain, so the bound is conservative.

    Parameters
    ----------
    rows : list of dict
        ``date`` (ISO), ``close`` (positive) and ``dividend_amount`` (the
        ex-date cash, ``0`` otherwise) per session; rows outside the window
        are ignored.
    short_put, short_call : float
        The short strikes.
    entry_date, settle_date : str
        ISO dates; the window is ``[entry_date, settle_date]``.
    carry_rate : float
        A constant upper bound on the cash rate, >= 0.
    multiplier : int
        Shares per contract.

    Returns
    -------
    dict
        ``call_dividend_usd``, ``put_carry_usd`` and ``total_usd`` per condor.

    Raises
    ------
    ValueError
        When the window is empty or inverted, a rate or strike is unusable,
        a close is not a positive number, or a session after the entry and
        up to settlement carries no ``dividend_amount`` (``None`` refuses,
        it is never skipped).
    """
    put = american_put_carry(rows, short_put, entry_date, settle_date, carry_rate, multiplier)
    call = american_call_dividend(rows, short_call, entry_date, settle_date, multiplier)
    return {"call_dividend_usd": call, "put_carry_usd": put, "total_usd": call + put}


def _amount_text(value):
    """Render exact USD/point decimals without context-sensitive normalization."""
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if value == 0 else result


@dataclass(frozen=True, init=False)
class _SyntheticIndexRow(ABC):
    """Immutable canonical index-option observation, never a vendor adapter."""

    data: object
    _FIELDS = ()
    _IDENTITIES = ()

    def __init__(self, record):
        if not isinstance(record, dict):
            raise ValueError("index-option record must be a dict")
        problems = []
        reject_unknown_params(problems, record, _COMMON + self._FIELDS + ("observation_ms",))
        missing = [key for key in _COMMON + self._FIELDS if key not in record]
        if missing:
            problems.append(f"missing fields: {missing}")
        if problems:
            raise ValueError("; ".join(problems))
        data = dict(record)
        for key in ("corpus_id", "row_version") + self._IDENTITIES:
            _text(data[key], key)
        if data["schema_version"] != _SCHEMA:
            raise ValueError("unsupported schema_version")
        if data["provenance"] != "synthetic" or data["known_at_basis"] != "synthetic":
            raise ValueError("S0 requires synthetic provenance and known_at_basis")
        _instant(data["effective_at"], "effective_at")
        _instant(data["known_at"], "known_at")
        if "observation_ms" in data:
            _integer(data["observation_ms"], "observation_ms", 0)
        self._validate(data)
        object.__setattr__(self, "data", MappingProxyType(data))

    @abstractmethod
    def _validate(self, data):
        """Validate this particular index-option observation."""

    def _same_instant(self, data, field):
        """Require the domain timestamp to equal the observation timestamp."""
        if _instant(data[field], field) != _instant(data["effective_at"], "effective_at"):
            raise ValueError(f"{field} must equal effective_at")


class CashIndexContract(_SyntheticIndexRow):
    """Validate immutable synthetic cash-index option reference data.

    Parameters
    ----------
    record : dict
        Complete canonical contract row; no missing product terms are inferred.

    Examples
    --------
    From the child's working directory::

        import json
        from pathlib import Path
        row = json.loads(Path("fixtures/contracts.jsonl").read_text().splitlines()[0])
        contract = CashIndexContract(row)
    """

    _FIELDS = (
        "contract_id", "reference_version", "underlying_id", "product", "right",
        "strike", "multiplier", "currency", "expiry", "last_trade_at",
        "settlement_at", "exercise_style", "settlement_type",
        "settlement_style", "settlement_id",
    )
    _IDENTITIES = (
        "contract_id", "reference_version", "underlying_id", "product", "settlement_id",
    )

    def _validate(self, data):
        """Validate supported exercise, settlement and monetary scale."""
        required = {
            "currency": "USD", "exercise_style": "european",
            "settlement_type": "cash", "settlement_style": "pm",
        }
        for key, value in required.items():
            if data[key] != value:
                raise ValueError(f"unsupported {key}: expected {value}")
        if data["right"] not in ("put", "call"):
            raise ValueError("right must be put or call")
        _decimal(data["strike"], "strike")
        _integer(data["multiplier"], "multiplier", 1)
        _day(data["expiry"], "expiry")
        if _instant(data["last_trade_at"], "last_trade_at") > _instant(
            data["settlement_at"], "settlement_at"
        ):
            raise ValueError("last_trade_at must not follow settlement_at")


class _IndexQuote(_SyntheticIndexRow):
    """One synthetic NBBO observation, not a claim that a spread could fill."""

    _FIELDS = (
        "contract_id", "reference_version", "quote_at",
        "bid", "ask", "bid_size", "ask_size", "condition_valid",
    )
    _IDENTITIES = ("contract_id", "reference_version")

    def _validate(self, data):
        """Refuse invalid, crossed, negative or ill-typed quotes."""
        self._same_instant(data, "quote_at")
        bid, ask = (_decimal(data[key], key) for key in ("bid", "ask"))
        for key in ("bid_size", "ask_size"):
            _integer(data[key], key, 0)
        problems = quote_problems(bid, ask, data["bid_size"], data["ask_size"], 0)
        if problems:
            raise ValueError("; ".join(problems))
        if data["condition_valid"] is not True:
            raise ValueError("condition_valid must be true")


class _IndexSettlement(_SyntheticIndexRow):
    """Synthetic analogue of one version of an official PM settlement value."""

    _FIELDS = (
        "settlement_id", "underlying_id", "expiry", "settlement_style",
        "value", "official_value_at",
    )
    _IDENTITIES = ("settlement_id", "underlying_id")

    def _validate(self, data):
        """Validate settlement metadata without inventing a closing-price proxy."""
        self._same_instant(data, "official_value_at")
        _day(data["expiry"], "expiry")
        _decimal(data["value"], "value")
        if data["settlement_style"] != "pm":
            raise ValueError("settlement_style must be pm")


@dataclass(frozen=True, init=False)
class DefinedRiskCondor:
    """Own four intact European cash-index legs and their expiry cashflows.

    Parameters
    ----------
    contracts, quotes : list of dict
        Ordered long put, short put, short call, long call; exact versions.
    settlement : dict
        Selected synthetic official-settlement analogue.
    count : int
        Positive number of complete spreads.
    fees_usd : str
        Nonnegative all-in fee for the whole hypothetical outcome.
    quantities : list of int
        Per-spread signed leg quantities, exactly [1, -1, -1, 1].

    Examples
    --------
    With validated fixture streams and the chosen settlement row::

        condor = DefinedRiskCondor(contracts, quotes, settlement, 1, "8.00",
                                  [1, -1, -1, 1])
        report = condor.evaluate()
    """

    contracts: tuple
    quotes: tuple
    settlement: object
    count: int
    fees: object
    quantities: tuple

    def __init__(self, contracts, quotes, settlement, count, fees_usd, quantities):
        if not isinstance(contracts, list) or len(contracts) != 4:
            raise ValueError("exactly four contract rows are required")
        if not isinstance(quotes, list) or len(quotes) != 4:
            raise ValueError("exactly four quote rows are required")
        if not isinstance(quantities, (list, tuple)) or len(quantities) != 4:
            raise ValueError("exactly four signed quantities are required")
        for quantity in quantities:
            _integer(quantity, "quantity", -1)
        if tuple(quantities) != _LEG_SIGNS:
            raise ValueError(f"quantities must be {list(_LEG_SIGNS)}")
        _integer(count, "count", 1)
        fees = _decimal(fees_usd, "fees_usd")
        if fees < 0:
            raise ValueError("fees_usd must be nonnegative")
        object.__setattr__(self, "contracts", tuple(CashIndexContract(r) for r in contracts))
        object.__setattr__(self, "quotes", tuple(_IndexQuote(r) for r in quotes))
        object.__setattr__(self, "settlement", _IndexSettlement(settlement))
        object.__setattr__(self, "count", count)
        object.__setattr__(self, "fees", fees)
        object.__setattr__(self, "quantities", tuple(quantities))
        self._check_identity()
        with localcontext() as context:
            self._precision(context)
            self._credit()

    def _check_identity(self):
        """Match every leg, quote and settlement before computing any cashflow."""
        contracts = [r.data for r in self.contracts]
        quotes = [r.data for r in self.quotes]
        settlement = self.settlement.data
        first = contracts[0]
        common = (
            "corpus_id", "underlying_id", "product", "expiry", "settlement_id",
            "settlement_style", "currency", "multiplier", "reference_version",
        )
        if tuple(c["right"] for c in contracts) != ("put", "put", "call", "call"):
            raise ValueError("leg rights/order must be put, put, call, call")
        strikes = [_decimal(c["strike"], "strike") for c in contracts]
        if not all(a < b for a, b in zip(strikes, strikes[1:])):
            raise ValueError("strikes must be strictly ordered")
        if len({c["contract_id"] for c in contracts}) != 4:
            raise ValueError("four distinct contract identities are required")
        for contract, quote in zip(contracts, quotes):
            if any(contract[k] != first[k] for k in common):
                raise ValueError("condor contract identities/terms must match")
            if any(quote[k] != contract[k] for k in (
                "corpus_id", "contract_id", "reference_version",
            )):
                raise ValueError("quote must match its contract/reference version")
            problems = quote_problems(
                _decimal(quote["bid"], "bid"), _decimal(quote["ask"], "ask"),
                quote["bid_size"], quote["ask_size"], self.count,
            )
            if problems:
                raise ValueError("quote sizes must cover every leg: " + "; ".join(problems))
            if _instant(quote["quote_at"], "quote_at") != _instant(
                quotes[0]["quote_at"], "quote_at"
            ):
                raise ValueError("quote instants must match")
            if _instant(quote["quote_at"], "quote_at") > _instant(
                contract["last_trade_at"], "last_trade_at"
            ):
                raise ValueError("quote_at must not follow last_trade_at")
            if _instant(contract["settlement_at"], "settlement_at") != _instant(
                settlement["official_value_at"], "official_value_at"
            ):
                raise ValueError("settlement instant must match the reference terms")
        for key in ("corpus_id", "underlying_id", "settlement_id", "expiry", "settlement_style"):
            if settlement[key] != first[key]:
                raise ValueError(f"settlement {key} must match contracts")

    def _precision(self, context):
        """Keep decimal cashflow operations exact, independent of ambient precision."""
        values = [self.fees, _decimal(self.settlement.data["value"], "value")]
        values += [_decimal(c.data["strike"], "strike") for c in self.contracts]
        values += [_decimal(q.data[k], k) for q in self.quotes for k in ("bid", "ask")]
        integer = max(max(0, value.adjusted() + 1) for value in values)
        fractional = max(max(0, -value.as_tuple().exponent) for value in values)
        scale_digits = len(str(self.count)) + len(str(self.contracts[0].data["multiplier"]))
        context.prec = max(28, integer + fractional + scale_digits + 8)
        context.traps[Inexact] = True

    def _credit(self):
        """Check the conservative quote-side credit against both wing widths."""
        credit, widths = condor_credit(
            [_decimal(c.data["strike"], "strike") for c in self.contracts],
            [(_decimal(q.data["bid"], "premium"), _decimal(q.data["ask"], "premium"))
             for q in self.quotes],
        )
        if not 0 < credit < min(widths):
            raise ValueError("credit must be positive and below the narrower wing width")
        return credit, widths

    def evaluate(self):
        """Compute exact signed cashflows, never expected returns or filled orders.

        Returns
        -------
        dict
            JSON-safe synthetic diagnostic with decimal-string USD amounts.

        Raises
        ------
        ArithmeticError
            If an extreme input cannot be represented exactly by Decimal.
        """
        with localcontext() as context:
            self._precision(context)
            credit, widths = self._credit()
            multiplier = self.contracts[0].data["multiplier"]
            scale = self.count * multiplier
            level = _decimal(self.settlement.data["value"], "value")
            legs = []
            for contract, quote, sign in zip(self.contracts, self.quotes, self.quantities):
                data = contract.data
                strike = _decimal(data["strike"], "strike")
                intrinsic = leg_intrinsic(data["right"], strike, level)
                entry = -sign * scale * _decimal(
                    quote.data["ask" if sign > 0 else "bid"], "premium"
                )
                terminal = sign * scale * intrinsic
                legs.append({
                    "contract_id": data["contract_id"],
                    "contract_version": data["row_version"],
                    "quote_at": quote.data["quote_at"],
                    "quote_version": quote.data["row_version"],
                    "quantity": sign * self.count,
                    "entry_cashflow_usd": _amount_text(entry),
                    "settlement_cashflow_usd": _amount_text(terminal),
                })
            entry = scale * credit
            terminal = sum(Decimal(leg["settlement_cashflow_usd"]) for leg in legs)
            gross = entry + terminal
            loss = scale * (max(widths) - credit)
            return {
                "kind": "synthetic_ex_post_diagnostic", "decision_eligible": False,
                "corpus_id": self.contracts[0].data["corpus_id"], "currency": "USD",
                "count": self.count, "multiplier": multiplier, "legs": legs,
                "settlement": dict(self.settlement.data),
                "entry_credit_points": _amount_text(credit),
                "entry_cashflow_usd": _amount_text(entry),
                "settlement_cashflow_usd": _amount_text(terminal),
                "fees_usd": _amount_text(self.fees),
                "gross_pnl_usd": _amount_text(gross),
                "net_pnl_usd": _amount_text(gross - self.fees),
                "max_loss_before_fees_usd": _amount_text(loss),
                "max_loss_after_fees_usd": _amount_text(loss + self.fees),
            }
