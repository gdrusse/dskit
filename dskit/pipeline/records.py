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
    "ASOF_FIELD",
    "CLUSTER_FIELD",
    "CONTRACT_FIELD",
    "BinaryAccounting",
    "MarkToMarketAccounting",
    "MarketRecord",
    "PositionOutcome",
    "cluster_of",
    "cluster_ok",
    "lead_frac_ok",
    "number_ok",
    "price_ok",
    "settle_position",
]

#: The envelope field naming an observation's DECISION INSTANT — the
#: causality anchor every downstream stage keys on. Named HERE because
#: more than one pack defaults to it (what a stream is ordered by, what
#: a fitted transform's split cuts on) and those are the same fact about
#: the same rows: two literals would be a scheduled bug the day one is
#: retuned.
ASOF_FIELD = "asof_ms"

#: The envelope field naming an observation's statistical-dependence
#: CLUSTER. Same reason: a row's cluster is what randomized splits and
#: cluster bootstraps key off, so its spelling belongs to one name.
CLUSTER_FIELD = "group"

#: The envelope field naming the TRADEABLE UNIT — and the identity a
#: row falls back to when it declares no cluster of its own. Named for
#: the same reason its two siblings are: the numpy pack carries it onto
#: every feature row and the fitted family cuts on it, so a rename must
#: move both together.
CONTRACT_FIELD = "contract"

#: Where a row's dependence cluster is read from, in order: the
#: envelope's own derived name (:attr:`MarketRecord.cluster`, a
#: property), then the raw cluster field a dict row carries, then the
#: contract. One tuple, because :func:`cluster_of` is its only reader.
_CLUSTER_SOURCES = ("cluster", CLUSTER_FIELD, CONTRACT_FIELD)


def number_ok(value):
    """Say whether ``value`` is a REAL NUMBER the toolkit can compute on.

    The base every other numeric rule here narrows: :func:`price_ok` adds
    a sign, :func:`lead_frac_ok` adds an interval, the numpy pack asks it
    of a lifted cell and of a stream's order key, and the fitted family
    asks it of a row's decision instant. All of them are the one question
    "is this cell a number", so it has one owner — a second copy drifts
    the day the bound widens, and then the same record lifts on one side
    of a wire and is refused on the other.

    Parameters
    ----------
    value : object
        The candidate. Anything at all: a non-number is simply not a
        number, so this answers ``False`` rather than raising.

    Returns
    -------
    bool
        True for a finite ``int`` or ``float``. ``bool`` is excluded
        explicitly — it is an ``int`` in Python, so without the check
        ``True`` would pass as the number 1.
    """
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def price_ok(value):
    """Say whether ``value`` is a price this envelope can hold.

    The envelope's price rule, PUBLIC so that nothing restates it. A
    tier-2 pack that re-derives this bound drifts the moment the envelope
    loosens one — audit HIGH-2, where ``libs/numpy.py`` carried its own
    copy and failed silently through a pass-through path.

    Parameters
    ----------
    value : object
        The candidate. Anything at all: a non-number is simply not a
        price, so this answers ``False`` rather than raising.

    Returns
    -------
    bool
        True for a finite, strictly positive ``int`` or ``float`` —
        :func:`number_ok` plus a sign, never a second copy of it.
    """
    return number_ok(value) and value > 0.0


def lead_frac_ok(value):
    """Say whether ``value`` is a ``lead_frac`` this envelope can hold.

    The sibling of :func:`price_ok`, public for the same reason.

    Parameters
    ----------
    value : object
        The candidate; a non-number answers ``False``.

    Returns
    -------
    bool
        True for a finite ``int`` or ``float`` strictly inside (0, 1) —
        :func:`number_ok` plus an interval, as in :func:`price_ok`.
    """
    return number_ok(value) and 0.0 < float(value) < 1.0


def cluster_ok(value):
    """Say whether ``value`` is a cluster id this envelope can hold.

    The third of the public envelope predicates, for the same reason as
    :func:`price_ok`: a pack that copies a record's ``group`` onto a row
    must normalize it by the ENVELOPE's rule, not by a restatement of
    it. :class:`MarketRecord` refuses anything else at construction, so
    a dict record carrying a non-string cluster must land the way the
    envelope would have had it — absent.

    Parameters
    ----------
    value : object
        The candidate. Anything at all; a non-string is simply not a
        cluster id, so this answers ``False`` rather than raising.

    Returns
    -------
    bool
        True for a non-empty ``str``. ``None`` (each contract its own
        cluster) is not a cluster ID and answers ``False``.
    """
    return isinstance(value, str) and bool(value)


def cluster_of(row):
    """Name the dependence cluster a ROW belongs to, or ``None``.

    :attr:`MarketRecord.cluster` answers this for an envelope; this is
    the same rule for anything a generic stage is handed, envelope or
    dict, read attr-or-key (the ``kinds_flow`` convention). It exists
    because the two vocabularies differ and a caller that picked one
    silently mis-cut the other: an envelope publishes the derived
    ``cluster`` as a property, while a feature row from a pack carries
    the RAW :data:`CLUSTER_FIELD` and :data:`CONTRACT_FIELD` it was
    built from and no ``cluster`` key at all. Every candidate is held to
    :func:`cluster_ok`, so an id the envelope could not hold — an empty
    string, an int — falls through rather than becoming a bucket of its
    own: an unusable identity hashes exactly like a missing one, which
    is how a whole stream lands in a single split.

    Parameters
    ----------
    row : object
        A record or row: a mapping, or anything with attributes.

    Returns
    -------
    str or None
        The first usable identity of ``cluster``, :data:`CLUSTER_FIELD`,
        :data:`CONTRACT_FIELD`; ``None`` when the row carries no
        identity a cluster-keyed split could honestly assign.
    """
    for name in _CLUSTER_SOURCES:
        value = row.get(name) if isinstance(row, dict) else getattr(row, name, None)
        if cluster_ok(value):
            return value
    return None


def _require_str(name, value):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")


def _require_price(name, value):
    """Refuse a price field that is neither None nor a positive number."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number or None, got {value!r}")
    if not price_ok(value):
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
        """Refuse the first shape problem, loudly (see ``Raises``)."""
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
        if self.group is not None and not cluster_ok(self.group):
            raise ValueError(f"group must be a non-empty string, got {self.group!r}")
        for name in ("bid", "ask", "mid"):
            _require_price(name, getattr(self, name))
        if self.lead_frac is not None:
            lf = self.lead_frac
            if isinstance(lf, bool) or not isinstance(lf, (int, float)):
                raise ValueError(f"lead_frac must be a number or None, got {lf!r}")
            if not lead_frac_ok(lf):
                raise ValueError(f"lead_frac must lie in (0, 1), got {lf!r}")

    @property
    def cluster(self) -> str:
        """Name the dependence cluster this record belongs to.

        ``group`` when it has one, else the contract itself. Randomized
        splits and cluster bootstraps key off this. Construction already
        held both to :func:`cluster_ok`, which is why this needs no
        further check; :func:`cluster_of` answers the same question for
        a row that is NOT an envelope, and reads this property first.
        """
        return self.group if self.group is not None else self.contract

    @property
    def crossed(self) -> bool:
        """Say whether the book is crossed: both quotes present, bid > ask.

        A state worth observing, which is exactly why construction did
        not refuse it.
        """
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
        """Turn a settled-YES bool into currency per unit.

        Parameters
        ----------
        outcome : bool
            Whether the contract settled YES.

        Returns
        -------
        float
            ``1.0`` for YES, ``0.0`` for NO.

        Raises
        ------
        ValueError
            When ``outcome`` is not a bool — a mark price arriving here
            means a mark-to-market venue was wired to binary accounting.
        """
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
        """Turn a closing mark into currency per unit — it IS the mark.

        Parameters
        ----------
        outcome : int or float
            The closing mark, finite and ``>= 0`` (0 is a real mark:
            bankruptcy).

        Returns
        -------
        float
            The mark itself.

        Raises
        ------
        ValueError
            When ``outcome`` is a bool or is not a finite number ``>= 0``
            — a bool here means a binary venue was wired to
            mark-to-market accounting.
        """
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
        """Refuse the first shape problem, loudly (see ``Raises``)."""
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
    """Settle one position with the arithmetic every venue shares.

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
