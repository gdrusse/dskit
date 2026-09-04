"""ONE book stack: ladders, the mirror rule, fills, and the decision-epoch record.

The parent project grew two order-book stacks that had to be kept from
becoming three. This module is the one: every consumer that touches a
ladder — the point-in-time reader, the panel featurizer, the entry
gate, the MIO's contract inputs, the fill walk — reads it from here.

Three conventions are load-bearing and stated once:

* **Resting bids on both sides.** A :class:`DecisionEpochRecord` carries
  ``yes_levels`` and ``no_levels`` as RESTING BID ladders, best-first
  (price descending). Kalshi's book is bids-only on both sides by
  construction; Polymarket stores each token's own asks, which
  :func:`ladders_from_book_json` complements into NO bids at
  ``1 − ask`` so every downstream reader sees one shape.
* **The mirror rule.** A NO bid at ``p`` IS a YES ask at ``1 − p`` (and a
  YES bid at ``p`` IS a NO ask at ``1 − p``). :func:`asks_from_bids` is
  the single implementation — the parent had five divergent copies. A
  sub-lot rest (depth floors to zero) and a 0/1-boundary bid are the
  only silent drops; everything else malformed raises.
* **One fee per order, on the total at VWAP**, through the venue
  dispatch in :mod:`pmquant.fees` — the rate is threaded, never
  defaulted, so an :class:`Order` REQUIRES one.

The mid is ``0.5 · (best YES bid + (1 − best NO bid))`` and is ``None``
when either side is empty: a one-sided book has no mid, and a caller
that needs a price must say so rather than read a bid as one.

Import cost: stdlib + dskit + :mod:`pmquant.fees` — this module is
imported by node modules at plan time and never imports numpy.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from dskit.pipeline.records import MarketRecord

from .fees import trading_fee_for_series

__all__ = [
    "CROSS_EPS",
    "EPOCH_KINDS",
    "NET_EDGE_EPS",
    "BookSnapshot",
    "ContractInputs",
    "CrossedBookError",
    "DecisionEpochRecord",
    "FillResult",
    "IncompleteBookError",
    "LevelFill",
    "Order",
    "asks_from_bids",
    "book_quality_ok",
    "contract_inputs_from_book",
    "entry_gate",
    "ladders_from_book_json",
    "market_record_from_epoch",
    "mid_from_ladders",
    "net_edge",
    "parse_ladder",
    "records_from_pit_rows",
    "walk_book",
]

#: The three kinds of decision epoch a ladder is read at.
EPOCH_KINDS = ("open", "lead", "settle")

#: A book is crossed when the best YES ask and best NO ask sum to less
#: than one dollar by more than representation dust; a TOUCHING book
#: (exactly one) is legal.
CROSS_EPS = 1e-9

#: The smallest net edge that counts as an edge — the entry gate's floor
#: under a zero ``tau``, so float dust never enters a position.
NET_EDGE_EPS = 1e-9


class IncompleteBookError(ValueError):
    """A book that cannot be priced: a side is empty after the mirror.

    Examples
    --------
    A one-sided book refuses rather than pricing the missing side::

        try:
            contract_inputs_from_book("KX-A", 0.4, yes_bids=[[0.3, 10]],
                                      no_bids=[], fee_rate=0.07)
        except IncompleteBookError as exc:
            str(exc)
        # -> "contract 'KX-A': no NO bids -> no executable YES asks ..."
    """


class CrossedBookError(IncompleteBookError):
    """A book whose executable asks sum below one dollar — an arbitrage, not a price.

    Subclasses :class:`IncompleteBookError` so a caller catching the
    broader class still sees it; catch THIS one first to route the book
    to the consistency-arbitrage scan instead of the sizer.

    Examples
    --------
    YES bid 0.85 and NO bid 0.90 mirror to asks 0.10 + 0.15 < 1::

        try:
            contract_inputs_from_book("KX-A", 0.5, yes_bids=[[0.85, 50]],
                                      no_bids=[[0.90, 50]], fee_rate=0.07)
        except CrossedBookError:
            pass
    """


def _finite(value):
    """Say whether ``value`` is a finite real number (bool excluded)."""
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _coerce_levels(levels, where):
    """Accept a JSON string or a sequence of ``[price, size]`` pairs; return pairs."""
    if isinstance(levels, str):
        try:
            levels = json.loads(levels)
        except ValueError as exc:
            raise ValueError(f"{where}: ladder is not valid JSON: {exc}") from exc
    if levels is None:
        return []
    if not isinstance(levels, (list, tuple)):
        raise ValueError(f"{where}: ladder must be a list of [price, size] pairs, got {levels!r}")
    out = []
    for i, level in enumerate(levels):
        try:
            price, size = level
            price, size = float(price), float(size)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{where}[{i}]: level must be a [price, size] pair, got {level!r}") from exc
        if not (math.isfinite(price) and math.isfinite(size)):
            raise ValueError(f"{where}[{i}]: level must be finite, got {level!r}")
        if not 0.0 <= price <= 1.0:
            raise ValueError(f"{where}[{i}]: price must lie in [0, 1], got {price!r}")
        if size < 0.0:
            raise ValueError(f"{where}[{i}]: size must be >= 0, got {size!r}")
        out.append((price, size))
    return out


def parse_ladder(levels, where="ladder"):
    """Normalize a resting-bid ladder: interior prices, whole positive lots, best first.

    Parameters
    ----------
    levels : str or sequence
        ``[[price_dollars, size], ...]`` or its JSON text.
    where : str
        Names the ladder in a refusal.

    Returns
    -------
    tuple of tuple
        ``((price, size), ...)`` with ``0 < price < 1``, ``size`` a
        positive int (floored), sorted by price DESCENDING — the resting
        bid order. Boundary prices (0 or 1) and sub-lot rests are
        dropped silently: they are not executable levels.

    Raises
    ------
    ValueError
        On a malformed level (non-pair, non-finite, price outside
        ``[0, 1]``, negative size) or invalid JSON.
    """
    out = []
    for price, size in _coerce_levels(levels, where):
        depth = int(math.floor(size))
        if depth <= 0 or not 0.0 < price < 1.0:
            continue
        out.append((price, depth))
    out.sort(key=lambda level: -level[0])
    return tuple(out)


def asks_from_bids(bids, where="bids"):
    """Mirror a resting-bid ladder into the executable asks it implies.

    A NO bid at ``p`` IS a YES ask at ``1 − p`` (and symmetrically), so
    the executable asks a buyer lifts are the OPPOSITE side's resting
    bids complemented. The one implementation.

    Parameters
    ----------
    bids : str or sequence
        The opposite side's resting bids, ``[[price, size], ...]``.
    where : str
        Names the ladder in a refusal.

    Returns
    -------
    tuple of tuple
        ``((ask_price, depth), ...)`` sorted ASCENDING — cheapest first,
        the executable order. A sub-lot rest and a 0/1-boundary bid are
        the only silent drops.

    Raises
    ------
    ValueError
        On any malformed level.
    """
    out = []
    for price, size in _coerce_levels(bids, where):
        depth = int(math.floor(size))
        ask = 1.0 - price
        if depth <= 0 or not 0.0 < ask < 1.0:
            continue
        out.append((ask, depth))
    out.sort(key=lambda level: level[0])
    return tuple(out)


def mid_from_ladders(yes_levels, no_levels):
    """Compute the mid from two resting-bid ladders, or ``None`` when one-sided.

    Parameters
    ----------
    yes_levels : sequence of tuple
        Resting YES bids, best first.
    no_levels : sequence of tuple
        Resting NO bids, best first.

    Returns
    -------
    float or None
        ``0.5 · (best_yes_bid + (1 − best_no_bid))``; ``None`` if either
        ladder is empty.
    """
    if not yes_levels or not no_levels:
        return None
    return 0.5 * (float(yes_levels[0][0]) + (1.0 - float(no_levels[0][0])))


def ladders_from_book_json(book_json):
    """Read a stored book into the two resting-bid ladders.

    Two encodings exist and are told apart by their keys: Kalshi-native
    ``{"orderbook_fp": {"yes_dollars": [...], "no_dollars": [...]}}``
    (bids on both sides), and Polymarket's ``{"orderbook_fp":
    {"yes_dollars": [...], "yes_asks": [...]}}`` (the token's own asks),
    whose asks are complemented into NO bids at ``1 − ask`` so every
    consumer sees the same shape. Source differences END here.

    Parameters
    ----------
    book_json : str or dict or None
        The stored book; ``None`` (a ``no_book`` row) yields two empty
        ladders.

    Returns
    -------
    tuple
        ``(yes_levels, no_levels)`` as :func:`parse_ladder` returns them.

    Raises
    ------
    ValueError
        On invalid JSON, a book without ``orderbook_fp``, or a malformed
        level.
    """
    if book_json is None:
        return (), ()
    book = json.loads(book_json) if isinstance(book_json, str) else book_json
    if not isinstance(book, dict) or not isinstance(book.get("orderbook_fp"), dict):
        raise ValueError(f"book must carry an 'orderbook_fp' object, got {book!r}")
    fp = book["orderbook_fp"]
    yes_levels = parse_ladder(fp.get("yes_dollars"), "yes_dollars")
    if "yes_asks" in fp:
        # Polymarket: the token's own executable asks -> the NO bids they are.
        no_levels = parse_ladder(
            [[1.0 - price, size] for price, size in _coerce_levels(fp.get("yes_asks"), "yes_asks")],
            "yes_asks",
        )
    else:
        no_levels = parse_ladder(fp.get("no_dollars"), "no_dollars")
    return yes_levels, no_levels


def book_quality_ok(yes_levels, no_levels, max_spread):
    """Say whether a book is tradeable: two-sided, not converged, not crossed, not wide.

    Parameters
    ----------
    yes_levels : sequence of tuple
        Resting YES bids, best first.
    no_levels : sequence of tuple
        Resting NO bids, best first.
    max_spread : float
        The widest executable spread (dollars) still counted tradeable.

    Returns
    -------
    bool
        False when a side is empty, when the touch sits at a 0/1
        boundary, when the book is crossed or locked (``ask <= bid``), or
        when ``ask − bid > max_spread``.
    """
    if not yes_levels or not no_levels:
        return False
    best_bid = float(yes_levels[0][0])
    best_ask = 1.0 - float(no_levels[0][0])
    if best_bid <= 0.0 or best_ask >= 1.0:
        return False
    if best_ask <= best_bid:
        return False
    return (best_ask - best_bid) <= float(max_spread)


@dataclass(frozen=True, slots=True)
class DecisionEpochRecord:
    """One contract's point-in-time book at one decision epoch.

    The venue-native record the child's :class:`~dskit.pipeline.records.MarketRecord`
    envelopes carry as ``native`` — every venue code path reads THIS,
    never the envelope.

    Parameters
    ----------
    series : str
        Series ticker (the instrument).
    event_ticker : str
        The event — the statistical-dependence cluster.
    contract_ticker : str
        The rung's market ticker (the tradeable unit).
    epoch_kind : str
        One of :data:`EPOCH_KINDS`.
    lead_frac : float or None
        Fraction of the observable span still ahead, in (0, 1) for a
        ``lead`` epoch and ``None`` otherwise.
    epoch_ts_ms : int
        The decision instant, epoch milliseconds.
    source : str
        Which store the book came from (provenance).
    yes_levels : tuple of tuple
        Resting YES bids ``((price, size), ...)``, best first.
    no_levels : tuple of tuple
        Resting NO bids, best first.
    p_mid : float or None
        The mid, ``None`` when one-sided.
    staleness_ms : int or None
        How old the carried-forward snapshot was (diagnostic, never a gate).
    admissible : bool
        A book existed at or before the epoch.
    quality_ok : bool
        The book passed :func:`book_quality_ok` when written.
    usable : bool
        ``admissible and quality_ok`` — the invariant construction refuses
        to break.
    reason : str
        Why the record is or is not usable (``"ok"``, ``"no_book"``,
        ``"low_quality"``, ``"settle"``, ``"bad_book"``); never empty.

    Examples
    --------
    A usable lead epoch, two-sided::

        rec = DecisionEpochRecord(
            series="KXA", event_ticker="KXA-1", contract_ticker="KXA-1-T50",
            epoch_kind="lead", lead_frac=0.5, epoch_ts_ms=1_000, source="pit",
            yes_levels=((0.40, 10),), no_levels=((0.55, 12),), p_mid=0.425,
            staleness_ms=0, admissible=True, quality_ok=True, usable=True,
            reason="ok",
        )
        rec.usable   # True
    """

    series: str
    event_ticker: str
    contract_ticker: str
    epoch_kind: str
    lead_frac: object
    epoch_ts_ms: int
    source: str
    yes_levels: tuple
    no_levels: tuple
    p_mid: object
    staleness_ms: object
    admissible: bool
    quality_ok: bool
    usable: bool
    reason: str

    def __post_init__(self):
        """Refuse the first shape problem, loudly."""
        if self.epoch_kind not in EPOCH_KINDS:
            raise ValueError(f"epoch_kind must be one of {EPOCH_KINDS}, got {self.epoch_kind!r}")
        if self.epoch_kind == "lead":
            if not _finite(self.lead_frac) or not 0.0 < float(self.lead_frac) < 1.0:
                raise ValueError(f"a lead epoch needs lead_frac in (0, 1), got {self.lead_frac!r}")
        elif self.lead_frac is not None:
            raise ValueError(f"a {self.epoch_kind!r} epoch carries no lead_frac, got {self.lead_frac!r}")
        if isinstance(self.epoch_ts_ms, bool) or not isinstance(self.epoch_ts_ms, int):
            raise ValueError(f"epoch_ts_ms must be an int, got {self.epoch_ts_ms!r}")
        if self.usable != (self.admissible and self.quality_ok):
            raise ValueError(
                f"usable={self.usable!r} contradicts admissible={self.admissible!r} and "
                f"quality_ok={self.quality_ok!r} — usable IS admissible and quality_ok"
            )
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("reason must be a non-empty string, even when usable")


def records_from_pit_rows(rows, venue, source):
    """Project point-in-time ledger rows into :class:`DecisionEpochRecord` objects.

    The reader trusts ``admissible`` and ``quality_ok`` and RECOMPUTES
    ``usable`` from them; a row whose ``book_json`` will not parse becomes
    ``quality_ok=False, reason="bad_book"`` rather than a crash, because a
    corrupt row is data about the store, not a shape error. Anything not
    spelled ``settle`` is a lead.

    Parameters
    ----------
    rows : iterable of dict
        Ledger rows carrying ``event_ticker``, ``contract_ticker``,
        ``kind``, ``lead_frac``, ``epoch_ts_ms``, ``staleness_ms``,
        ``admissible``, ``quality_ok``, ``reason``, ``book_json`` and,
        optionally, ``series`` (else the contract's first dash segment).
    venue : str
        Provenance carried into the envelope by the caller; unused here
        beyond documentation of the pairing.
    source : str
        The store name stamped on every record.

    Returns
    -------
    list of DecisionEpochRecord
        In input order.

    Raises
    ------
    ValueError
        On a row missing a required field.
    """
    out = []
    for row in rows:
        try:
            contract = str(row["contract_ticker"])
            event = str(row["event_ticker"])
            ts = int(row["epoch_ts_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"PIT row lacks a required field: {exc} in {row!r}") from exc
        series = row.get("series") or contract.split("-", 1)[0]
        kind = "settle" if row.get("kind") == "settle" else "lead"
        try:
            yes_levels, no_levels = ladders_from_book_json(row.get("book_json"))
            bad_book = False
        except (ValueError, TypeError):
            yes_levels, no_levels, bad_book = (), (), True
        admissible = bool(row.get("admissible", False))
        quality_ok = bool(row.get("quality_ok", False)) and not bad_book
        lead_frac = row.get("lead_frac") if kind == "lead" else None
        if kind == "lead" and not _finite(lead_frac):
            raise ValueError(f"lead row without a numeric lead_frac: {row!r}")
        staleness = row.get("staleness_ms")
        out.append(
            DecisionEpochRecord(
                series=series,
                event_ticker=event,
                contract_ticker=contract,
                epoch_kind=kind,
                lead_frac=float(lead_frac) if lead_frac is not None else None,
                epoch_ts_ms=ts,
                source=source,
                yes_levels=yes_levels,
                no_levels=no_levels,
                p_mid=mid_from_ladders(yes_levels, no_levels),
                staleness_ms=int(staleness) if _finite(staleness) else None,
                admissible=admissible,
                quality_ok=quality_ok,
                usable=admissible and quality_ok,
                reason="bad_book" if bad_book else str(row.get("reason") or "ok"),
            )
        )
    return out


def market_record_from_epoch(venue, rec):
    """Wrap a :class:`DecisionEpochRecord` in the toolkit's venue-neutral envelope.

    Parameters
    ----------
    venue : str
        The venue tag (``"kalshi"`` | ``"polymarket"``).
    rec : DecisionEpochRecord
        The native record, carried verbatim as ``native``.

    Returns
    -------
    MarketRecord
        ``instrument`` = series, ``contract`` = contract ticker,
        ``asof_ms`` = the epoch, ``group`` = the event, ``bid`` = best YES
        bid, ``ask`` = ``1 − best NO bid``, ``mid`` = ``p_mid``.
    """
    bid = float(rec.yes_levels[0][0]) if rec.yes_levels else None
    ask = (1.0 - float(rec.no_levels[0][0])) if rec.no_levels else None
    return MarketRecord(
        venue=venue,
        instrument=rec.series,
        contract=rec.contract_ticker,
        asof_ms=rec.epoch_ts_ms,
        usable=rec.usable,
        reason=rec.reason,
        group=rec.event_ticker,
        bid=bid if bid is not None and bid > 0.0 else None,
        ask=ask if ask is not None and ask > 0.0 else None,
        mid=rec.p_mid,
        lead_frac=rec.lead_frac,
        native=rec,
    )


@dataclass(frozen=True, slots=True)
class ContractInputs:
    """What the sizer needs to know about one contract: belief and executable asks.

    Parameters
    ----------
    contract_id : str
        The contract ticker.
    q_hat : float
        The model's belief the contract settles YES, in ``[0, 1]``.
    yes_levels : tuple of tuple
        Executable YES asks ``((price, depth), ...)``, cheapest first.
    no_levels : tuple of tuple
        Executable NO asks, cheapest first.
    fee_rate : float
        The threaded rate for the contract's series, in ``[0, 1)``;
        ``0.0`` is a legal sourced rate, ``None`` refuses.
    cell_lo_c : float or None
        The lower confidence bound on the cell's realized net edge, for the
        CI-robust entry gate; ``None`` when no bound exists.

    Examples
    --------
    A contract priced at 0.30 ask / 0.28 bid the model believes at 0.40::

        ci = ContractInputs("KX-A", 0.40, ((0.30, 2000),), ((0.72, 2000),), 0.07)
        ci.yes_levels[0]   # (0.3, 2000)
    """

    contract_id: str
    q_hat: float
    yes_levels: tuple
    no_levels: tuple
    fee_rate: object
    cell_lo_c: object = None

    def __post_init__(self):
        """Refuse the first shape problem, loudly."""
        if not isinstance(self.contract_id, str) or not self.contract_id:
            raise ValueError("contract_id must be a non-empty string")
        if not _finite(self.q_hat) or not 0.0 <= float(self.q_hat) <= 1.0:
            raise ValueError(f"q_hat must lie in [0, 1], got {self.q_hat!r}")
        if self.fee_rate is None:
            raise ValueError(
                f"contract {self.contract_id!r}: fee_rate is missing (None) — a rate is threaded "
                "from the fee book, never defaulted; a sourced 0.0 is legal"
            )
        if not _finite(self.fee_rate) or not 0.0 <= float(self.fee_rate) < 1.0:
            raise ValueError(f"fee_rate must lie in [0, 1), got {self.fee_rate!r}")
        if self.cell_lo_c is not None and not _finite(self.cell_lo_c):
            raise ValueError(f"cell_lo_c must be finite or None, got {self.cell_lo_c!r}")
        for name in ("yes_levels", "no_levels"):
            levels = getattr(self, name)
            if not levels:
                raise IncompleteBookError(f"contract {self.contract_id!r}: {name} is empty")
            for price, depth in levels:
                if not _finite(price) or not 0.0 < float(price) < 1.0:
                    raise ValueError(f"{name}: prices must lie strictly in (0, 1), got {price!r}")
                if isinstance(depth, bool) or not isinstance(depth, int) or depth <= 0:
                    raise ValueError(f"{name}: depths must be positive ints, got {depth!r}")


def contract_inputs_from_book(contract_id, q_hat, *, yes_bids, no_bids, fee_rate, cell_lo_c=None):
    """Build :class:`ContractInputs` from a contract's two resting-bid ladders.

    Parameters
    ----------
    contract_id : str
        The contract ticker.
    q_hat : float
        The belief, in ``[0, 1]``.
    yes_bids : sequence or str
        Resting YES bids (any order; JSON text accepted).
    no_bids : sequence or str
        Resting NO bids.
    fee_rate : float
        The threaded rate; ``None`` refuses.
    cell_lo_c : float or None
        Optional CI-robust bound.

    Returns
    -------
    ContractInputs
        With ``yes_levels`` = the mirror of ``no_bids`` and ``no_levels``
        = the mirror of ``yes_bids``, both executable-order.

    Raises
    ------
    CrossedBookError
        When the two best asks sum below ``1 − CROSS_EPS`` — route it to
        the arbitrage scan, never the sizer.
    IncompleteBookError
        When either side is empty after the mirror.
    ValueError
        On a malformed ladder, belief, or rate.
    """
    if not _finite(q_hat):
        raise ValueError(f"contract {contract_id!r}: q_hat must be a finite number, got {q_hat!r}")
    if fee_rate is None:
        raise ValueError(
            f"contract {contract_id!r}: fee_rate is missing (None) — thread it from the fee book"
        )
    yes_levels = asks_from_bids(no_bids, where=f"{contract_id}.no_bids")
    no_levels = asks_from_bids(yes_bids, where=f"{contract_id}.yes_bids")
    if not yes_levels:
        raise IncompleteBookError(
            f"contract {contract_id!r}: no NO bids -> no executable YES asks (one-sided book)"
        )
    if not no_levels:
        raise IncompleteBookError(
            f"contract {contract_id!r}: no YES bids -> no executable NO asks (one-sided book)"
        )
    if yes_levels[0][0] + no_levels[0][0] < 1.0 - CROSS_EPS:
        raise CrossedBookError(
            f"contract {contract_id!r}: best YES ask {yes_levels[0][0]:.4f} + best NO ask "
            f"{no_levels[0][0]:.4f} < 1 — a crossed book is an arbitrage, not a price"
        )
    return ContractInputs(contract_id, float(q_hat), yes_levels, no_levels, fee_rate, cell_lo_c)


def net_edge(q_hat, bid, ask, rate, series):
    """Compute the better side's fee-adjusted edge per contract, in dollars.

    Parameters
    ----------
    q_hat : float
        The belief the contract settles YES.
    bid : float
        Best executable YES bid (what a NO buyer effectively pays ``1 −``).
    ask : float
        Best executable YES ask.
    rate : float
        The threaded fee rate.
    series : str
        The series ticker, for the venue's rounding model.

    Returns
    -------
    dict
        ``{"side", "net_edge", "gross_edge", "fee"}`` for the better of
        YES (``q − ask − fee(ask)``) and NO (``bid − q − fee(1 − bid)``);
        ties go to YES.

    Raises
    ------
    ValueError
        When the quotes are crossed (``bid > ask + CROSS_EPS``) or not
        in ``[0, 1]``.
    """
    for name, value in (("bid", bid), ("ask", ask)):
        if not _finite(value) or not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"{name} must lie in [0, 1], got {value!r}")
    if bid > ask + CROSS_EPS:
        raise ValueError(f"crossed quotes: bid {bid!r} > ask {ask!r}")
    gross_yes = float(q_hat) - float(ask)
    fee_yes = trading_fee_for_series(series, 1, float(ask), rate)
    gross_no = float(bid) - float(q_hat)
    fee_no = trading_fee_for_series(series, 1, 1.0 - float(bid), rate)
    yes_net, no_net = gross_yes - fee_yes, gross_no - fee_no
    if yes_net >= no_net:
        return {"side": "yes", "net_edge": yes_net, "gross_edge": gross_yes, "fee": fee_yes}
    return {"side": "no", "net_edge": no_net, "gross_edge": gross_no, "fee": fee_no}


def entry_gate(contract, series, tau=0.0, cell_lo_c_floor=None):
    """Decide which side, if any, of a contract clears the entry gate B1.

    Parameters
    ----------
    contract : ContractInputs
        The contract's belief and executable asks.
    series : str
        Its series ticker (venue rounding).
    tau : float
        The net-edge threshold in dollars; the effective floor is
        ``max(tau, NET_EDGE_EPS)``.
    cell_lo_c_floor : float or None
        When not ``None``, the CI-robust gate is ON: the contract's
        ``cell_lo_c`` must exist and exceed this floor (``None``/NaN
        fail closed).

    Returns
    -------
    tuple
        ``(side or None, info)`` where ``info`` is :func:`net_edge`'s dict.
    """
    ask = contract.yes_levels[0][0]
    bid = 1.0 - contract.no_levels[0][0]
    info = net_edge(contract.q_hat, bid, ask, contract.fee_rate, series)
    side = info["side"] if info["net_edge"] > max(float(tau), NET_EDGE_EPS) else None
    if side is not None and cell_lo_c_floor is not None:
        lo = contract.cell_lo_c
        if lo is None or not (_finite(lo) and float(lo) > float(cell_lo_c_floor)):
            side = None
    return side, info


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    """One executable ladder to walk: asks ascending, or bids descending.

    Parameters
    ----------
    ticker : str
        The contract ticker (its series decides the fee rounding).
    side : str
        ``"ask"`` (lifting asks, prices ascending) or ``"bid"`` (hitting
        bids, prices descending).
    levels : tuple of tuple
        ``((price, size), ...)`` in executable order.
    mid : float or None
        The reference mid for slippage; the best price when ``None``.

    Examples
    --------
    Two ask levels to lift::

        book = BookSnapshot("KXA-1-T50", "ask", ((0.25, 100), (0.30, 50)))
        book.best_price   # 0.25
    """

    ticker: str
    side: str
    levels: tuple
    mid: object = None

    def __post_init__(self):
        """Refuse a ladder that is not in executable order."""
        if self.side not in ("ask", "bid"):
            raise ValueError(f"side must be 'ask' or 'bid', got {self.side!r}")
        prices = []
        for price, size in self.levels:
            if not _finite(price) or not 0.0 <= float(price) <= 1.0:
                raise ValueError(f"level price must lie in [0, 1], got {price!r}")
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                raise ValueError(f"level size must be a positive int, got {size!r}")
            prices.append(float(price))
        ordered = sorted(prices) if self.side == "ask" else sorted(prices, reverse=True)
        if prices != ordered:
            raise ValueError(f"{self.side} levels must be in executable order, got {prices}")

    @property
    def best_price(self):
        """Give the first executable price, or ``None`` on an empty ladder."""
        return float(self.levels[0][0]) if self.levels else None


@dataclass(frozen=True, slots=True)
class Order:
    """A taker order against one :class:`BookSnapshot`.

    Parameters
    ----------
    size : int
        Lots wanted, a positive int.
    limit_price : float
        The worst price accepted (an ask above it, or a bid below it,
        ends the walk); ``1.0``/``0.0`` are the permissive limits.
    fee_rate : float
        The threaded rate — REQUIRED; there is no default rate anywhere
        in this child.

    Examples
    --------
    Lift up to 100 lots at any price::

        order = Order(100, 1.0, 0.07)
    """

    size: int
    limit_price: float
    fee_rate: object

    def __post_init__(self):
        """Refuse a malformed order."""
        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size <= 0:
            raise ValueError(f"size must be a positive int, got {self.size!r}")
        if not _finite(self.limit_price) or not 0.0 <= float(self.limit_price) <= 1.0:
            raise ValueError(f"limit_price must lie in [0, 1], got {self.limit_price!r}")
        if self.fee_rate is None:
            raise ValueError("fee_rate is required on an Order — thread it from the fee book")
        if not _finite(self.fee_rate) or float(self.fee_rate) < 0.0:
            raise ValueError(f"fee_rate must be a number >= 0, got {self.fee_rate!r}")


@dataclass(frozen=True, slots=True)
class LevelFill:
    """Lots taken at one price level.

    Parameters
    ----------
    price : float
        The level's price.
    contracts : int
        Lots taken there.

    Examples
    --------
    ::

        LevelFill(0.25, 40)
    """

    price: float
    contracts: int


@dataclass(frozen=True, slots=True)
class FillResult:
    """What a book walk delivered.

    Parameters
    ----------
    filled : int
        Total lots filled.
    is_partial : bool
        Fewer lots than the order wanted (an empty book is partial).
    vwap : float
        Volume-weighted average price; ``nan`` when nothing filled.
    slippage_vs_mid : float
        ``vwap − mid`` for an ask walk, ``mid − vwap`` for a bid walk
        (positive = worse); ``nan`` when nothing filled.
    fee : float
        ONE fee on the total at VWAP under the venue's rounding.
    net_cost : float
        Premium plus fee for an ask walk; ``filled · (1 − vwap) + fee``
        for a bid walk (the NO premium); ``nan`` when nothing filled.
    levels : tuple of LevelFill
        The levels hit, best first.

    Examples
    --------
    ::

        FillResult(0, True, float("nan"), float("nan"), 0.0, float("nan"), ())
    """

    filled: int
    is_partial: bool
    vwap: float
    slippage_vs_mid: float
    fee: float
    net_cost: float
    levels: tuple


def walk_book(book, order):
    """Walk an executable ladder best-first and fill an order against it.

    Parameters
    ----------
    book : BookSnapshot
        The ladder, in executable order.
    order : Order
        Lots wanted, the limit, and the threaded fee rate.

    Returns
    -------
    FillResult
        Best-first partial fills up to the limit, VWAP, slippage against
        the mid, and ONE fee on the total at VWAP through the venue
        dispatch. An empty fill is ``is_partial=True`` with ``nan``
        prices and a zero fee.
    """
    mid = float(book.mid) if book.mid is not None else book.best_price
    remaining = order.size
    hits = []
    for price, available in book.levels:
        if remaining == 0:
            break
        price = float(price)
        if book.side == "ask" and price > float(order.limit_price):
            break
        if book.side == "bid" and price < float(order.limit_price):
            break
        take = min(remaining, int(available))
        hits.append(LevelFill(price, take))
        remaining -= take
    filled = sum(h.contracts for h in hits)
    nan = float("nan")
    if filled == 0:
        return FillResult(0, True, nan, nan, 0.0, nan, ())
    vwap = sum(h.price * h.contracts for h in hits) / filled
    slip = (vwap - mid) if book.side == "ask" else (mid - vwap)
    fee = trading_fee_for_series(book.ticker, filled, vwap, order.fee_rate)
    premium = filled * vwap if book.side == "ask" else filled * (1.0 - vwap)
    return FillResult(filled, filled < order.size, vwap, slip, fee, premium + fee, tuple(hits))
