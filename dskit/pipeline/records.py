"""The venue-neutral record schema — envelopes, not replacements.

Design ruling (2026-08-13 design session, question 1): the toolkit does NOT
define a record that venue code migrates onto. :class:`MarketRecord` is an
ENVELOPE around the venue's own record — an adapter wraps its native type
(a native decision-epoch record, a daily bar) into the envelope as
``native`` and hands the native back, untouched, to every venue code path.
The toolkit's generic stages read only the envelope. That is what keeps
this package a wrapper: no venue's record type is forked, shimmed, or
migrated.

What unifies venue records is exactly the envelope's fields:

* identity — ``instrument`` (the config-universe id) and ``contract``
  (the tradeable unit; ``contract == instrument`` for venues without
  sub-contracts),
* clustering — ``group`` (the statistical-dependence cluster: an
  event, a trading day; ``None`` = each contract is its own cluster).
  Cluster-aware resampling and randomized splits both key off it,
* time — ``asof_ms``, the decision instant the observation is valid AT
  (the causality anchor every downstream stage keys on),
* price — ``bid``/``ask``/``mid`` as plain positive numbers. Deliberately
  NO (0, 1) bound and NO bid<=ask check: binary-contract bounds are venue
  physics (the adapter's business), and a crossed book is a market STATE
  to observe, not a shape error to refuse,
* admissibility — ``usable``/``reason``: only usable records may be acted
  on, and a skip always carries its reason,
* horizon — ``lead_frac`` in (0, 1), fraction of life remaining for
  expiring instruments (``None`` for open-ended ones).

Where binary settle-to-$1 vs. mark-to-market accounting splits (question
1b): in exactly ONE mapping — what a settled/closed unit pays. That
mapping is the :class:`~dskit.pipeline.protocols.Accounting` seam;
:class:`BinaryAccounting` (outcome ``bool`` -> $1/$0) and
:class:`MarkToMarketAccounting` (outcome = the mark itself) are the two
canonical implementations, and each REFUSES the other's outcome type — a
bool reaching a mark-to-market book, or a price reaching a binary one, is
a venue-wiring bug that must fail loudly, not coerce. Everything after
the mapping is venue-independent arithmetic, implemented once in
:func:`settle_position` and re-validated inside :class:`PositionOutcome`.

Records are hot-path values, so validation is fail-loud-on-first-problem
with a plain ``ValueError`` — the accumulate-everything ``ConfigError``
protocol is for configs someone edits, not objects a loop constructs by
the thousand.

Import cost: stdlib only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "BinaryAccounting",
    "MarkToMarketAccounting",
    "MarketRecord",
    "PositionOutcome",
    "settle_position",
]


def _require_str(name, value):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")


def _require_price(name, value):
    """A price field: None, or a finite positive number (bool excluded)."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number or None, got {value!r}")
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be finite and > 0, got {value!r}")


@dataclass(frozen=True, slots=True)
class MarketRecord:
    """One venue-neutral market observation at one decision instant.

    Parameters
    ----------
    venue : str
        Backend tag the observation came through ("markets", "equities", ...).
    instrument : str
        Config-universe id (series ticker, symbol).
    contract : str
        Tradeable-unit id. Equal to ``instrument`` for venues without
        sub-contracts.
    asof_ms : int
        Decision instant, Unix milliseconds — the causality anchor. Every
        signal query about this record must use exactly this instant.
    usable : bool
        May the loop act on this observation? (a market venue:
        ``admissible and quality_ok``; an equities venue: inside regular
        session, quote fresh.)
    reason : str
        Short admissibility tag ("ok", "no_book", "halted", ...). Required
        even when usable — a skip must always carry its reason.
    group : str or None
        Statistical-dependence cluster id (an event ticker, a trading
        day). ``None`` means the contract is its own cluster.
    bid, ask, mid : float or None
        Best executable quotes in venue currency, > 0 when present. No
        bid<=ask check — a crossed book is market state, not a shape error.
    lead_frac : float or None
        Fraction of instrument life remaining, in (0, 1); ``None`` for
        open-ended instruments.
    native : object
        The venue's own record, carried verbatim so venue stages never
        lose information to the envelope. ``None`` when there is nothing
        richer than the envelope.

    Raises
    ------
    ValueError
        On the first shape problem (fail loud, no accumulation).
    """

    venue: str
    instrument: str
    contract: str
    asof_ms: int
    usable: bool
    reason: str
    group: object = None
    bid: object = None
    ask: object = None
    mid: object = None
    lead_frac: object = None
    native: object = None

    def __post_init__(self):
        _require_str("venue", self.venue)
        _require_str("instrument", self.instrument)
        _require_str("contract", self.contract)
        if isinstance(self.asof_ms, bool) or not isinstance(self.asof_ms, int):
            raise ValueError(f"asof_ms must be an int, got {self.asof_ms!r}")
        if self.asof_ms < 0:
            raise ValueError(f"asof_ms must be >= 0, got {self.asof_ms!r}")
        if not isinstance(self.usable, bool):
            raise ValueError(f"usable must be a bool, got {self.usable!r}")
        _require_str("reason", self.reason)
        if self.group is not None:
            _require_str("group", self.group)
        for name in ("bid", "ask", "mid"):
            _require_price(name, getattr(self, name))
        if self.lead_frac is not None:
            lf = self.lead_frac
            if isinstance(lf, bool) or not isinstance(lf, (int, float)):
                raise ValueError(f"lead_frac must be a number or None, got {lf!r}")
            if not (0.0 < float(lf) < 1.0):
                raise ValueError(f"lead_frac must lie in (0, 1), got {lf!r}")

    @property
    def cluster(self) -> str:
        """The dependence cluster this record belongs to (``group`` or, when
        no grouping exists, the contract itself). Randomized splits and
        cluster bootstraps key off this."""
        return self.group if self.group is not None else self.contract

    @property
    def crossed(self) -> bool:
        """True when both quotes exist and bid exceeds ask (a state worth
        observing, which is exactly why construction did not refuse it)."""
        return self.bid is not None and self.ask is not None and self.bid > self.ask


# ---------------------------------------------------------------------------
# Accounting: the ONE per-venue mapping, then shared arithmetic
# ---------------------------------------------------------------------------


class BinaryAccounting:
    """Settle-to-$1 accounting (a binary settle-to-$1 venue): outcome is a bool.

    ``payout_per_unit(True) == 1.0``, ``payout_per_unit(False) == 0.0``.
    Anything that is not a bool is refused: a mark price arriving here
    means a mark-to-market venue was wired to binary accounting — a bug,
    not a coercion opportunity.
    """

    def payout_per_unit(self, outcome) -> float:
        if not isinstance(outcome, bool):
            raise ValueError(
                f"BinaryAccounting expects a bool outcome (settled YES?), got "
                f"{outcome!r} — a non-bool here means a mark-to-market venue "
                "was wired to binary accounting"
            )
        return 1.0 if outcome else 0.0


class MarkToMarketAccounting:
    """Mark-to-market accounting (equities): the outcome IS the mark.

    ``payout_per_unit(mark) == mark`` — a finite price >= 0 (0 is a real
    mark: bankruptcy). A bool is refused explicitly (``bool`` is an ``int``
    in Python, so without the check ``True`` would silently mark at $1).
    """

    def payout_per_unit(self, outcome) -> float:
        if isinstance(outcome, bool) or not isinstance(outcome, (int, float)):
            raise ValueError(
                f"MarkToMarketAccounting expects a numeric mark, got {outcome!r} "
                "— a bool here means a binary venue was wired to mark-to-market "
                "accounting"
            )
        mark = float(outcome)
        if not math.isfinite(mark) or mark < 0.0:
            raise ValueError(f"mark must be finite and >= 0, got {outcome!r}")
        return mark


@dataclass(frozen=True, slots=True)
class PositionOutcome:
    """What one closed/settled position was ultimately worth.

    Constructed via :func:`settle_position` (which derives ``proceeds`` and
    ``pnl``); direct construction re-validates the arithmetic, so an
    inconsistent outcome cannot exist.

    Parameters
    ----------
    contract : str
        Tradeable-unit id the position was held in.
    qty : float
        Units held at settlement (signed; non-zero — a flat position has
        no outcome).
    cost : float
        Total signed cash paid to build the position, fees excluded
        (negative = net proceeds received, e.g. a short).
    fee : float
        Total fees paid on the position, >= 0.
    payout_per_unit : float
        What one unit paid at settlement — the venue's ``Accounting``
        mapping applied to the outcome. >= 0.
    proceeds : float
        ``qty * payout_per_unit``.
    pnl : float
        ``proceeds - cost - fee``.
    """

    contract: str
    qty: float
    cost: float
    fee: float
    payout_per_unit: float
    proceeds: float
    pnl: float

    def __post_init__(self):
        _require_str("contract", self.contract)
        for name in ("qty", "cost", "fee", "payout_per_unit", "proceeds", "pnl"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"{name} must be a number, got {v!r}")
            if not math.isfinite(v):
                raise ValueError(f"{name} must be finite, got {v!r}")
        if self.qty == 0:
            raise ValueError("qty must be non-zero — a flat position has no outcome")
        if self.fee < 0:
            raise ValueError(f"fee must be >= 0, got {self.fee!r}")
        if self.payout_per_unit < 0:
            raise ValueError(
                f"payout_per_unit must be >= 0, got {self.payout_per_unit!r}"
            )
        tol = 1e-9 * max(1.0, abs(self.qty) * max(1.0, self.payout_per_unit))
        if abs(self.proceeds - self.qty * self.payout_per_unit) > tol:
            raise ValueError(
                f"proceeds {self.proceeds!r} inconsistent with qty * "
                f"payout_per_unit = {self.qty * self.payout_per_unit!r}"
            )
        if abs(self.pnl - (self.proceeds - self.cost - self.fee)) > tol:
            raise ValueError(
                f"pnl {self.pnl!r} inconsistent with proceeds - cost - fee = "
                f"{self.proceeds - self.cost - self.fee!r}"
            )


def settle_position(contract, qty, cost, fee, payout_per_unit) -> PositionOutcome:
    """The shared settlement arithmetic — identical for every venue.

    The venue-specific step happened BEFORE this call: the backend's
    ``Accounting.payout_per_unit(outcome)`` turned the venue outcome (a
    settled-YES bool, a closing mark) into currency per unit. From there,
    binary and mark-to-market books are the same formula, implemented and
    tested exactly once::

        proceeds = qty * payout_per_unit
        pnl      = proceeds - cost - fee

    Returns
    -------
    PositionOutcome
        With ``proceeds``/``pnl`` derived; construction re-validates.
    """
    proceeds = qty * payout_per_unit
    return PositionOutcome(
        contract=contract,
        qty=qty,
        cost=cost,
        fee=fee,
        payout_per_unit=payout_per_unit,
        proceeds=proceeds,
        pnl=proceeds - cost - fee,
    )
