"""One fold, one owner: the derived state of a serve series (plan §5.8.1).

Everything a process knows about its series is a fold over the ledger, and
this module is the only place that fold happens. :class:`SeriesState.apply`
consumes one §6 envelope at a time — ``Ledger.append`` calls it, so the
fold is never behind the chain — and owns positions (through
:class:`PositionBook`), working orders, pending client refs, balances,
the decision history, the breaker, the arming, the readiness GO, guard
holds, the reduction projection and the pending-control set. Nothing else
folds the ledger: ``Breaker.current``, ``Arming.current``,
``Readiness.current``, ``Accounting``, every ``Guard`` and the reconciler
read the frozen :class:`StateView` this fold projects, and two AST tests
pin that no other module scans the ledger or assigns a folded attribute.

The envelope nests its body. The fold reads ``kind``, ``seq``,
``series_id``, ``prev_hash``, ``hash`` and ``release_hash`` off the
envelope and every record field off ``envelope["body"]``, so a body may
carry its own ``release_hash`` (a ``tick_start`` written under one release
and folded by a process running another) without colliding with the
ledger's. Bodies are read tolerantly — an unknown body field is ignored,
as §5.8 requires of every reader — while the value objects a body embeds
(an ``authority`` issue's ``arming``, its ``authorization``) are exact.

Three small frozen projections carry what the plan names as values owned
by modules built later: :class:`ArmingProjection` (the eleven
``ArmingState`` fields of §5.6, restored from the issue body's embedded
``arming``), :class:`ReadinessProjection` (the five ``ReadinessResult``
fields of §5.13) and :class:`ReductionProjection` (the current reduction
authority, its rights and which of them ``authority_use`` has reserved).
Each has an exact ``to_obj()`` so ``arming.py`` and ``readiness.py`` can
rebuild their own value objects from the view without this module
importing them.

``economic_seq`` advances on exactly three kinds — ``order_event``,
``fill`` and ``cash_flow`` (D14). An ``intent`` is pending, not economic;
an ``authority_use`` is a rights reservation; an ``outcome`` is a label
and an ``adoption`` a receipt for one (R4).

A ``cash_flow`` whose ``supersedes`` names an earlier one CORRECTS it:
the fold reverses the amount that record booked, in the currency it
booked it in, before adding its own, so a re-adopted reconciliation
break moves the balance to the corrected figure rather than to the sum
of both. Each record may be superseded once — a second correction of one
record would reverse an amount no longer in the balance — and a
``supersedes`` the fold never booked refuses rather than silently
booking gross.

The snapshot payload (:meth:`SeriesState.to_snapshot_obj`) carries every
``StateView`` member plus ``monitor_state`` (§6) and, so a mid-tick
snapshot can be recovered from, the open ticks, their plans and the
booked cash flows a later correction nets against.
``positions`` is the :class:`PositionBook`'s own form — the per-instrument
applied-fill log since the position was last flat (R3) — from which the
held positions are recomputed, which is what makes a ``reversed`` fill
undoable after a restart. Averages (a position's ``avg_cost``, a working
order's ``avg_price``) are kept as exact rationals and rendered to a
``Decimal`` once, at projection, so a third fill never averages in a
rounded quotient. ``risk_version``'s session tokens are never restored.

:class:`Recovery` replays the fold from the last ``snapshot`` forward,
closes every tick a crash left without its terminal ``tick`` or
``decision``, queries the venue for every ambiguous client ref — a
pending intent, or a submitted plan whose intent never landed, whose ref
it derives from the ``IdSource`` — records each answer as an
``order_event`` and never submits.

Import cost: stdlib plus ``dskit.pipeline.node`` and the package's own
``base``, ``records``, ``redact`` and ``vocab``.
"""

import dataclasses
from collections import deque
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from types import MappingProxyType

from dskit.pipeline.node import check_int_param
from dskit.production.base import (
    GENESIS_HASH,
    ProductionError,
    _check_dict,
    _check_str,
    _check_unknown,
    canonical_hash,
)
from dskit.production.records import (
    Fill,
    OrderState,
    Position,
    ReductionAuthorization,
    RiskVersion,
    TickStart,
)
from dskit.production.redact import get_logger, redact
from dskit.production.vocab import (
    AUTHORITY_EVENTS,
    AUTHORITY_ROLES,
    BREAKER_STATES,
    FILL_STATUSES,
    GUARD_STATE_KINDS,
    ORDER_EVENTS,
    PLAN_RESULTS,
    POSITION_SOURCES,
    PROCESS_EVENTS,
    READINESS_VERDICTS,
    RECORD_KINDS,
    SIDES,
    STATUSES,
    TERMINAL_STATUSES,
    TICK_STATUSES,
)

__all__ = [
    "ArmingProjection",
    "DEFAULT_MAX_HISTORY",
    "PositionBook",
    "ReadinessProjection",
    "Recovery",
    "RecoveryReport",
    "ReductionProjection",
    "SeriesState",
    "StateView",
    "TickState",
]

#: How many decision legs the fold keeps in ``StateView.decision_history``
#: when the caller names no bound — the newest win.
DEFAULT_MAX_HISTORY = 1000

_log = get_logger("state")

_ZERO = Decimal(0)

#: The vocabulary members this module spells, each pinned to its tuple at
#: import so a renamed member cannot leave a stale literal behind.
_ACTIVE, _DERIVED, _ISSUE, _SUBMIT = "active", "derived", "issue", "submit"
_FAILED, _RECOVERED, _STATUS_EVENT, _UNKNOWN = "failed", "recovered", "status", "unknown"
_SNAPSHOT = "snapshot"
for _member, _vocabulary in (
    (_ACTIVE, BREAKER_STATES),
    (_DERIVED, POSITION_SOURCES),
    (_ISSUE, AUTHORITY_EVENTS),
    (_SUBMIT, PLAN_RESULTS),
    (_FAILED, TICK_STATUSES),
    (_RECOVERED, PROCESS_EVENTS),
    (_STATUS_EVENT, ORDER_EVENTS),
    (_UNKNOWN, ORDER_EVENTS),
    (_UNKNOWN, STATUSES),
    (_SNAPSHOT, RECORD_KINDS),
):
    if _member not in _vocabulary:
        raise ProductionError([f"state.py: {_member!r} is not a vocabulary member"])

#: A fill's signed direction; ``none`` is the abstaining side and never fills.
_SIGNS = {"buy": 1, "sell": -1}
if not set(_SIGNS) < set(SIDES):
    raise ProductionError(["state.py: _SIGNS names a side outside SIDES"])

#: The keys a tick's plan bookkeeping entry carries (see ``SeriesState.tick_plans``).
_PLAN_KEYS = ("plan_id", "decision_plan_digest", "result", "client_ref")

#: What the fold keeps of each ``cash_flow`` it has booked, so a later
#: correction can net the amount it replaces back out (see
#: ``SeriesState._fold_cash_flow``). Booked in the record's OWN currency,
#: which is why the currency is kept beside the amount.
_CASH_FLOW_KEYS = ("currency", "amount", "superseded_by")

#: What the fold keeps of the latest ``trip`` (see ``SeriesState.last_trip``):
#: the envelope's identity and instant — a reset acknowledges the id and
#: cooling-off is measured from ``recorded_at_ms`` — plus the body fields
#: that say what the transition was.
_LAST_TRIP_KEYS = ("id", "seq", "recorded_at_ms", "from", "to", "reason", "acknowledged_trip_id")


def _money(problems, path, value):
    """Return ``value`` as a finite Decimal; a float, a bool or garbage refuses."""
    if isinstance(value, (bool, float)):
        problems.append(f"{path}: money never touches float, got {value!r}")
        return None
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        problems.append(f"{path}: {value!r} is not a decimal amount")
        return None
    if not amount.is_finite():
        problems.append(f"{path}: non-finite number {amount} refused")
        return None
    return amount


def _jsonable(value):
    """Return ``value`` JSON-ready: mappings to dicts, tuples to lists, Decimal to str."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (dict, MappingProxyType)):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _ratio(exact):
    """Render an exact rational as a Decimal — one rounding, at projection time only."""
    return Decimal(exact.numerator) / Decimal(exact.denominator)


def _fraction(problems, path, text):
    """Parse the ``str(Fraction)`` form back; anything but such a string refuses."""
    try:
        if not isinstance(text, str):
            raise ValueError("not a string")
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        problems.append(f"{path}: {text!r} is not an exact rational")
        return None


def _exact(problems, where, obj, keys):
    """Require ``obj`` to be a dict carrying exactly ``keys``."""
    _check_dict(problems, where, obj)
    if problems:
        return
    _check_unknown(problems, obj, keys, where)
    missing = [key for key in keys if key not in obj]
    if missing:
        problems.append(f"{where}: missing key(s) {missing}")


def _projection_from_obj(cls, obj):
    """Build a projection dataclass from exactly its fields, default-deny."""
    problems = []
    _exact(problems, cls.__name__, obj, tuple(f.name for f in dataclasses.fields(cls)))
    if problems:
        raise ProductionError(problems)
    return cls(**obj)


class _Projection:
    """The shared half of the three projections: an exact JSON form."""

    def to_obj(self):
        """Return the projection as a JSON-ready dict of exactly its fields.

        Returns
        -------
        dict
            Tuples as lists, read-only mappings as dicts — the form
            ``from_obj`` accepts and the snapshot payload carries.
        """
        return {f.name: _jsonable(getattr(self, f.name)) for f in dataclasses.fields(self)}

    @classmethod
    def from_obj(cls, obj):
        """Rebuild the projection from its ``to_obj()`` form, default-deny.

        Parameters
        ----------
        obj : dict
            Exactly the declared fields.

        Returns
        -------
        _Projection
            An instance of ``cls`` equal to the one that produced ``obj``.

        Raises
        ------
        ProductionError
            On a non-dict, an unknown or missing key, or a malformed field.
        """
        return _projection_from_obj(cls, obj)


# ---------------------------------------------------------------------------
# The projections the view carries for modules built later (§5.6, §5.13)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArmingProjection(_Projection):
    """The current ordinary arm as the fold holds it — §5.6's eleven ``ArmingState`` fields.

    Folded from an ``authority`` issue body's embedded ``arming``
    (``ArmingState.to_obj()``) and cleared by a disarm, revoke or expire
    of that authority or by any breaker transition (D10). ``arming.py``
    rebuilds its own value object from :meth:`to_obj`.

    Parameters
    ----------
    authority_id, release_hash, rung, maker, checker : str
    armed_at_ms, armed_until_ms : int
    allowlist : tuple of str
        Coerced from any sequence.
    limits_overlay : mapping
        Coerced to a read-only mapping.
    request_proof_digest, approval_proof_digest : str

    Examples
    --------
    ::

        arming = ArmingProjection(
            authority_id="auth-1", release_hash="b" * 64, rung="live_limited",
            maker="principal-maker", checker="principal-checker",
            armed_at_ms=1_767_268_800_000, armed_until_ms=1_767_272_400_000,
            allowlist=["INS1"], limits_overlay={}, request_proof_digest="6" * 64,
            approval_proof_digest="4" * 64,
        )
        arming.allowlist  # ('INS1',)
        arming == ArmingProjection.from_obj(arming.to_obj())  # True
    """

    authority_id: str
    release_hash: str
    rung: str
    maker: str
    checker: str
    armed_at_ms: int
    armed_until_ms: int
    allowlist: tuple
    limits_overlay: MappingProxyType
    request_proof_digest: str
    approval_proof_digest: str

    def __post_init__(self):
        """Freeze the two containers; refuse anything that is not one."""
        problems = []
        if isinstance(self.allowlist, str) or not isinstance(self.allowlist, (list, tuple)):
            problems.append(f"ArmingProjection.allowlist must be a sequence, got {self.allowlist!r}")
        _check_dict(problems, "ArmingProjection.limits_overlay", dict(self.limits_overlay)
                    if isinstance(self.limits_overlay, MappingProxyType) else self.limits_overlay)
        if problems:
            raise ProductionError(problems)
        object.__setattr__(self, "allowlist", tuple(self.allowlist))
        object.__setattr__(self, "limits_overlay", MappingProxyType(dict(self.limits_overlay)))


@dataclass(frozen=True)
class ReadinessProjection(_Projection):
    """The current readiness evaluation as the fold holds it — §5.13's ``ReadinessResult`` fields.

    Folded from the latest ``readiness`` record; ``readiness.py`` decides
    whether it is still a GO at a given instant.

    Parameters
    ----------
    verdict : str
        One of ``READINESS_VERDICTS``.
    items : tuple of mapping
        The checklist items, each frozen read-only.
    readiness_digest : str
    evaluated_at_ms, valid_until_ms : int

    Examples
    --------
    ::

        ready = ReadinessProjection(
            verdict="go", items=[{"item": "reconciled", "required": True,
                                   "evidence": "recon-1", "waiver": None, "passed": True}],
            readiness_digest="7" * 64, evaluated_at_ms=1_767_268_800_000,
            valid_until_ms=1_767_269_700_000,
        )
        ready.items[0]["passed"]  # True
    """

    verdict: str
    items: tuple
    readiness_digest: str
    evaluated_at_ms: int
    valid_until_ms: int

    def __post_init__(self):
        """Check the verdict and freeze every item."""
        problems = []
        if self.verdict not in READINESS_VERDICTS:
            problems.append(
                f"ReadinessProjection.verdict must be one of {list(READINESS_VERDICTS)}, "
                f"got {self.verdict!r}"
            )
        if isinstance(self.items, str) or not isinstance(self.items, (list, tuple)):
            problems.append(f"ReadinessProjection.items must be a sequence, got {self.items!r}")
        else:
            for position, item in enumerate(self.items):
                _check_dict(problems, f"ReadinessProjection.items[{position}]", dict(item)
                            if isinstance(item, MappingProxyType) else item)
        if problems:
            raise ProductionError(problems)
        object.__setattr__(
            self, "items", tuple(MappingProxyType(dict(item)) for item in self.items)
        )

    @classmethod
    def from_body(cls, body):
        """Project a §6 ``readiness`` body, tolerating fields this view does not carry.

        Parameters
        ----------
        body : dict
            The record body; ``release_hash`` and any unknown field are
            ignored, the five projected fields are required.

        Returns
        -------
        ReadinessProjection
            The projected evaluation.

        Raises
        ------
        ProductionError
            On a missing projected field or a malformed value.
        """
        names = tuple(f.name for f in dataclasses.fields(cls))
        missing = [name for name in names if name not in body]
        if missing:
            raise ProductionError([f"readiness: missing key(s) {missing}"])
        return cls(**{name: body[name] for name in names})


@dataclass(frozen=True)
class ReductionProjection(_Projection):
    """The current reduction authority, its rights and the rights already reserved (§5.8.1).

    Folded from a ``reduction`` ``authority`` issue (the embedded
    ``ReductionAuthorization``); each ``authority_use`` reserves one right
    exactly once (D12); a revoke, disarm or expire clears the projection.

    Parameters
    ----------
    authority_id : str
    rights : tuple of str
        Every granted ``reduction_intent_digest``.
    reserved : tuple of str
        The rights an ``authority_use`` has consumed, in reservation order.
    expires_ms : int

    Examples
    --------
    ::

        grant = ReductionProjection(authority_id="auth-2", rights=("a1" * 32, "a2" * 32),
                                    reserved=(), expires_ms=1_767_269_100_000)
        grant.reserve("a1" * 32).reserved  # ('a1a1…',)
    """

    authority_id: str
    rights: tuple
    reserved: tuple
    expires_ms: int

    def __post_init__(self):
        """Freeze the two digest sequences."""
        problems = []
        for name in ("rights", "reserved"):
            value = getattr(self, name)
            if isinstance(value, str) or not isinstance(value, (list, tuple)):
                problems.append(f"ReductionProjection.{name} must be a sequence, got {value!r}")
        if problems:
            raise ProductionError(problems)
        object.__setattr__(self, "rights", tuple(self.rights))
        object.__setattr__(self, "reserved", tuple(self.reserved))

    def reserve(self, digest):
        """Return the projection with ``digest`` reserved.

        Parameters
        ----------
        digest : str
            A granted, not yet reserved ``reduction_intent_digest``.

        Returns
        -------
        ReductionProjection
            A new value; this one is unchanged.

        Raises
        ------
        ProductionError
            If ``digest`` was never granted or is already reserved — a
            right is single-use and a reservation is never erased.
        """
        problems = []
        if digest not in self.rights:
            problems.append(f"reduction right {digest!r} was not granted by {self.authority_id!r}")
        elif digest in self.reserved:
            problems.append(f"reduction right {digest!r} is already reserved")
        if problems:
            raise ProductionError(problems)
        return dataclasses.replace(self, reserved=self.reserved + (digest,))


# ---------------------------------------------------------------------------
# The frozen views (§5.8.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StateView:
    """The frozen projection of the fold, and only of the fold (§5.8.1).

    Only :meth:`SeriesState.snapshot` builds one. Every container is
    immutable — tuples, or read-only mappings over copies — so a later
    fold never moves a view already taken and a guard cannot mutate what
    it judges. ``Accounting.snapshot`` and ``Reconciler.run`` take it as
    their first argument.

    Parameters
    ----------
    positions : tuple of Position
        Our derived side, sorted by instrument; flat instruments absent.
    working : mapping of str to OrderState
        Acknowledged, non-terminal orders by client ref.
    pending : tuple of str
        Client refs whose intent has no ``order_event`` yet.
    balances : mapping of str to Decimal
        Per currency, folded from ``cash_flow`` records — net of every
        correction, since a record that ``supersedes`` another replaces
        the amount it booked rather than adding to it.
    decision_history : tuple of mapping
        The newest decision legs, each with its ``tick_id`` added, bounded
        by ``max_history``.
    breaker : str
        One of ``BREAKER_STATES``.
    arming : ArmingProjection or None
    readiness : ReadinessProjection or None
    guard_holds : mapping of (str, str) to mapping
        The latest ``guard_state`` body per ``(guard, scope_key)``.
    reduction : ReductionProjection or None
    pending_control : mapping of str to str
        ``request_id -> purpose`` for control requests without a result.
    risk_version : RiskVersion
        ``economic_seq`` from the fold; the session tokens are always None
        here — accounting re-acquires them.
    head_seq : int
    head_hash : str

    Examples
    --------
    ::

        state = SeriesState("018f0f4e-7b21-7d3a-9c31-6d8f36d806a1")
        view = state.snapshot()
        (view.breaker, view.positions, view.head_seq)  # ('active', (), 0)
        view.risk_version.economic_seq  # 0
    """

    positions: tuple
    working: MappingProxyType
    pending: tuple
    balances: MappingProxyType
    decision_history: tuple
    breaker: str
    arming: ArmingProjection | None
    readiness: ReadinessProjection | None
    guard_holds: MappingProxyType
    reduction: ReductionProjection | None
    pending_control: MappingProxyType
    risk_version: RiskVersion
    head_seq: int
    head_hash: str


@dataclass(frozen=True)
class TickState:
    """What a ``Guard`` receives as ``state`` — one declared type, no rung (§5.8.1).

    The tick assembles the first one and each leg rebuilds it from a fresh
    ``SeriesState.snapshot()``. ``feed_status`` and ``feed_ages`` are this
    tick's fetch result and ``calendar`` an injected collaborator, which
    is why the three are here and not in :class:`StateView`.

    Parameters
    ----------
    view : StateView
        The fold at head — provenance and non-economic history.
    account : AccountState
        The sole economic authority for a measure (§5.8.1).
    feed_status : str
        One of ``FEED_STATUSES``, from this tick's ``fetch``.
    feed_ages : tuple of FeedAge
        Per-key ages from this tick's ``coverage``.
    calendar : Calendar
        The schedule's calendar.

    Examples
    --------
    ::

        state = TickState(view=fold.snapshot(), account=account, feed_status="live",
                          feed_ages=(FeedAge(key="INS1", age_ms=1_000, watermark_ms=0),),
                          calendar=calendar)
        state.view.breaker  # 'active'
    """

    view: StateView
    account: object
    feed_status: str
    feed_ages: tuple
    calendar: object


# ---------------------------------------------------------------------------
# PositionBook — our side of the two-sided comparison (§5.7, §5.8.1, R3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LogEntry:
    """One applied fill as the book remembers it: id, side, qty, price, fee."""

    fill_id: str
    side: str
    qty: Decimal
    price: Decimal
    fee: Decimal

    @property
    def signed_qty(self):
        """The quantity with its side's sign."""
        return _SIGNS[self.side] * self.qty

    def to_obj(self):
        """Render the entry JSON-ready."""
        return {f.name: _jsonable(getattr(self, f.name)) for f in dataclasses.fields(self)}

    @classmethod
    def from_obj(cls, obj, where):
        """Rebuild an entry from its JSON form, default-deny, money as Decimal."""
        problems = []
        _exact(problems, where, obj, tuple(f.name for f in dataclasses.fields(cls)))
        if problems:
            raise ProductionError(problems)
        _check_str(problems, f"{where}.fill_id", obj["fill_id"])
        if obj["side"] not in _SIGNS:
            problems.append(f"{where}.side must be one of {sorted(_SIGNS)}, got {obj['side']!r}")
        money = {
            name: _money(problems, f"{where}.{name}", obj[name]) for name in ("qty", "price", "fee")
        }
        if problems:
            raise ProductionError(problems)
        return cls(obj["fill_id"], obj["side"], money["qty"], money["price"], money["fee"])


def _step(qty, avg_cost, delta, price):
    """Return ``(qty, avg_cost)`` after a signed fill of ``delta`` at ``price``.

    ``avg_cost`` is an exact ``Fraction`` so averaging in a third fill never
    re-divides a rounded quotient; :func:`_ratio` renders it once.
    """
    new = qty + delta
    if qty == 0:
        return new, Fraction(price)
    if (delta > 0) == (qty > 0):
        weighted = Fraction(abs(qty)) * avg_cost + Fraction(abs(delta)) * Fraction(price)
        return new, weighted / Fraction(abs(new))
    if new == 0 or (new > 0) == (qty > 0):
        return new, avg_cost
    return new, Fraction(price)


class PositionBook:
    """Positions derived from folded fills — ours, as opposed to the venue's (§5.7).

    Cost basis averages in on an increase, holds on a reduction and
    rebases to the crossing fill's price when a fill carries the position
    through flat. Per instrument the book keeps the applied-fill log since
    the position was last flat (R3): ``reverse(fill_id)`` recomputes the
    position exactly from the log minus that fill, so undoing the FIRST of
    two fills leaves what applying the second alone would have built.
    Reaching flat realises the log, after which those fills cannot be
    reversed. Owned by :class:`SeriesState`; ``Reconciler`` compares its
    :meth:`positions` with the executor's venue report.

    Examples
    --------
    ::

        book = PositionBook()
        book.apply(Fill(fill_id="f-1", venue_ref="v-1", client_ref="ref-1",
                        instrument="INS1", side="buy", qty=Decimal("10"),
                        price=Decimal("100"), fee=Decimal("0"), fee_currency="USD",
                        liquidity="taker", status="final", ts_ms=0, native=None))
        book.net_qty("INS1")  # Decimal('10')
        book.positions()[0].avg_cost  # Decimal('100')
        book.reverse("f-1")
        book.positions()  # ()
    """

    def __init__(self):
        self._logs = {}
        self._held = {}

    def apply(self, fill):
        """Fold one fill into its instrument's position.

        Parameters
        ----------
        fill : Fill
            The venue's report; ``side`` is ``buy`` or ``sell``.

        Raises
        ------
        ProductionError
            If ``fill`` is not a ``Fill``, its side cannot fill, or its
            ``fill_id`` is already in the instrument's live log.
        """
        problems = []
        if not isinstance(fill, Fill):
            problems.append(f"PositionBook.apply expects a Fill, got {fill!r}")
        elif fill.side not in _SIGNS:
            problems.append(f"a fill with side {fill.side!r} cannot move a position")
        elif any(entry.fill_id == fill.fill_id for entry in self._logs.get(fill.instrument, ())):
            problems.append(f"fill {fill.fill_id!r} is already applied to {fill.instrument!r}")
        if problems:
            raise ProductionError(problems)
        self._push(fill.instrument, _LogEntry(fill.fill_id, fill.side, fill.qty, fill.price, fill.fee))

    def _push(self, instrument, entry):
        """Apply one log entry; reaching flat realises the instrument's log."""
        qty, avg_cost = self._held.get(instrument, (_ZERO, Fraction(0)))
        qty, avg_cost = _step(qty, avg_cost, entry.signed_qty, entry.price)
        self._logs.setdefault(instrument, []).append(entry)
        if qty == 0:
            del self._logs[instrument]
            self._held.pop(instrument, None)
        else:
            self._held[instrument] = (qty, avg_cost)

    def reverse(self, fill_id):
        """Undo one applied fill by recomputing its instrument from the remaining log.

        Parameters
        ----------
        fill_id : str
            The ``fill_id`` of a fill in some instrument's live log.

        Raises
        ------
        ProductionError
            If no live log holds ``fill_id`` — it was never applied,
            already reversed, or realised when the position went flat.
        """
        for instrument, log in self._logs.items():
            keep = [entry for entry in log if entry.fill_id != fill_id]
            if len(keep) != len(log):
                break
        else:
            raise ProductionError(
                [f"fill {fill_id!r} is unknown or already realised — nothing to reverse"]
            )
        del self._logs[instrument]
        self._held.pop(instrument, None)
        for entry in keep:
            self._push(instrument, entry)

    def positions(self):
        """Return every non-flat position, sorted by instrument.

        Returns
        -------
        tuple of Position
            ``source`` is ``derived`` and ``native`` None on each.
        """
        return tuple(
            Position(
                instrument=instrument, qty=qty, avg_cost=_ratio(avg_cost), source=_DERIVED, native=None
            )
            for instrument, (qty, avg_cost) in sorted(self._held.items())
        )

    def net_qty(self, instrument):
        """Return the signed quantity held in ``instrument``.

        Parameters
        ----------
        instrument : str

        Returns
        -------
        Decimal
            Zero for an instrument the book does not hold.
        """
        return self._held.get(instrument, (_ZERO, Fraction(0)))[0]

    def to_obj(self):
        """Return the book's JSON-ready form: each instrument's live log.

        Returns
        -------
        dict
            ``instrument -> [entry, ...]`` in applied order, sorted by
            instrument; the held positions are implied by the logs.
        """
        return {
            instrument: [entry.to_obj() for entry in log]
            for instrument, log in sorted(self._logs.items())
        }

    @classmethod
    def from_obj(cls, obj):
        """Rebuild a book from :meth:`to_obj`, recomputing every position.

        Parameters
        ----------
        obj : dict
            ``instrument -> [entry, ...]``.

        Returns
        -------
        PositionBook
            Holding what replaying every log builds.

        Raises
        ------
        ProductionError
            On a malformed log or entry.
        """
        problems = []
        _check_dict(problems, "positions", obj)
        if problems:
            raise ProductionError(problems)
        book = cls()
        for instrument, log in obj.items():
            if not isinstance(log, list):
                raise ProductionError([f"positions.{instrument} must be a list of fills, got {log!r}"])
            for position, entry in enumerate(log):
                book._push(instrument, _LogEntry.from_obj(entry, f"positions.{instrument}[{position}]"))
        return book


# ---------------------------------------------------------------------------
# SeriesState — the fold (§5.8.1, §6, D14)
# ---------------------------------------------------------------------------

#: Record kind -> (fold method, advances ``economic_seq``). Every member of
#: ``RECORD_KINDS`` folds; the check below refuses a table that drifts.
_FOLDS = {
    "process": ("_fold_nothing", False),
    "tick_start": ("_fold_tick_start", False),
    "tick": ("_fold_tick", False),
    "decision": ("_fold_decision", False),
    "decision_plan": ("_fold_decision_plan", False),
    "intent": ("_fold_intent", False),
    "authorization": ("_fold_nothing", False),
    "control_request": ("_fold_control_request", False),
    "control_approval": ("_fold_nothing", False),
    "authority": ("_fold_authority", False),
    "authority_use": ("_fold_authority_use", False),
    "order_event": ("_fold_order_event", True),
    "fill": ("_fold_fill", True),
    "cash_flow": ("_fold_cash_flow", True),
    "outcome": ("_fold_nothing", False),
    "guard_state": ("_fold_guard_state", False),
    "readiness": ("_fold_readiness", False),
    "recon": ("_fold_nothing", False),
    "trip": ("_fold_trip", False),
    "cancel_outcome": ("_fold_nothing", False),
    "adoption": ("_fold_nothing", False),
    "command_result": ("_fold_command_result", False),
    "monitor": ("_fold_monitor", False),
    "alert": ("_fold_nothing", False),
    "health": ("_fold_nothing", False),
    _SNAPSHOT: ("_fold_nothing", False),
}
if set(_FOLDS) != set(RECORD_KINDS):
    raise ProductionError(["state.py: the fold table does not cover RECORD_KINDS exactly"])

#: ``authority`` role -> the method that folds its issue / its ending.
_AUTHORITY_ISSUES = {"ordinary": "_issue_arming", "reduction": "_issue_rights"}
_AUTHORITY_CLEARS = {"ordinary": "_clear_arming", "reduction": "_clear_rights"}
if set(_AUTHORITY_ISSUES) != set(AUTHORITY_ROLES) or set(_AUTHORITY_CLEARS) != set(AUTHORITY_ROLES):
    raise ProductionError(["state.py: the authority tables do not cover AUTHORITY_ROLES"])

#: ``fill`` status -> the book verb; ``reversed`` undoes, the others apply.
_FILL_FOLDS = {"pending": "_apply_fill", "final": "_apply_fill", "reversed": "_reverse_fill"}
if set(_FILL_FOLDS) != set(FILL_STATUSES):
    raise ProductionError(["state.py: the fill table does not cover FILL_STATUSES"])

#: The snapshot payload's keys: every ``StateView`` member plus the five
#: the plan, recovery, the breaker's reset and a cash-flow correction need
#: beyond the view.
_SNAPSHOT_KEYS = tuple(f.name for f in dataclasses.fields(StateView)) + (
    "monitor_state",
    "open_ticks",
    "tick_plans",
    "last_trip",
    "cash_flows",
)


def _record_from_body(cls, body):
    """Build a records value from a body, ignoring fields the record does not declare."""
    names = {f.name for f in dataclasses.fields(cls)}
    return cls.from_obj({key: value for key, value in body.items() if key in names})


class SeriesState:
    """The single fold over the ledger and the sole owner of derived state (§5.8.1).

    ``Ledger.append`` hands every envelope to :meth:`apply` exactly as it
    was written, so the fold is level with the chain; :meth:`snapshot`
    projects it as a frozen :class:`StateView`; :meth:`head` reports the
    ``(seq, hash)`` folded so far. The fold refuses anything it cannot
    place — an unknown kind, a non-dense ``seq``, a ``prev_hash`` that is
    not its head, a record of another series — and refuses a body it
    cannot fold (a second reservation of one reduction right, a second
    reversal of one fill, a second correction of one cash flow), leaving
    itself unchanged. It advances ``economic_seq`` on ``order_event``,
    ``fill`` and ``cash_flow`` only (D14, R4).

    Parameters
    ----------
    series_id : str
        The series this fold belongs to; a record of another refuses.
    max_history : int, default DEFAULT_MAX_HISTORY
        How many decision legs ``StateView.decision_history`` keeps
        (``>= 1``); the newest win.

    Raises
    ------
    ProductionError
        On a malformed ``series_id`` or ``max_history``.

    Examples
    --------
    A fold attached to the ledger that feeds it::

        from dskit.production.clock import WallClock
        from dskit.production.ledger import JsonlLedger, ServeRoot

        state = SeriesState("018f0f4e-7b21-7d3a-9c31-6d8f36d806a1")
        state.head()  # (0, '000…000')
        serve = ServeRoot("./serve", "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1")
        ledger = JsonlLedger(serve, "proc-1", "a" * 64, clock=WallClock(), state=state)
        ledger.append({"kind": "tick_start", "id": "tick-1",
                       "body": {"tick_id": "tick-1", "tick_at_ms": 0}})
        state.head()[0]  # 1
        state.open_ticks()[0].tick_id  # 'tick-1'
        state.snapshot().breaker  # 'active'
    """

    def __init__(self, series_id, max_history=DEFAULT_MAX_HISTORY):
        problems = []
        _check_str(problems, "series_id", series_id)
        check_int_param(problems, "max_history", max_history, ge=1)
        if problems:
            raise ProductionError(problems)
        self._series_id = series_id
        self._max_history = max_history
        self._head_seq, self._head_hash = 0, GENESIS_HASH
        self._economic_seq = 0
        self._book = PositionBook()
        self._working = {}
        self._filled_notional = {}
        self._pending = {}
        self._balances = {}
        self._cash_flows = {}
        self._history = deque(maxlen=max_history)
        self._breaker = _ACTIVE
        self._last_trip = None
        self._arming = None
        self._readiness = None
        self._guard_holds = {}
        self._reduction = None
        self._pending_control = {}
        self._monitor_state = {}
        self._open_ticks = {}
        self._tick_plans = {}
        self._folds = {
            kind: (getattr(self, name), economic) for kind, (name, economic) in _FOLDS.items()
        }
        self._authority_folds = {
            (role, _ISSUE): getattr(self, name) for role, name in _AUTHORITY_ISSUES.items()
        }
        for role, name in _AUTHORITY_CLEARS.items():
            for event in AUTHORITY_EVENTS:
                self._authority_folds.setdefault((role, event), getattr(self, name))
        self._fill_folds = {status: getattr(self, name) for status, name in _FILL_FOLDS.items()}

    @property
    def series_id(self):
        """The series this fold belongs to."""
        return self._series_id

    # -- the fold -----------------------------------------------------------

    def apply(self, envelope):
        """Fold one ledger envelope.

        Parameters
        ----------
        envelope : dict
            The full §6 envelope as the ledger wrote it: a ``kind`` from
            ``RECORD_KINDS``, the next dense ``seq``, this fold's
            ``series_id``, a ``prev_hash`` equal to the current head, and
            the record fields under ``body``. Unknown body fields are
            ignored.

        Raises
        ------
        ProductionError
            If the envelope cannot be placed on this fold or its body
            cannot be folded; the fold is unchanged.
        """
        problems = []
        _check_dict(problems, "record", envelope)
        if problems:
            raise ProductionError(problems)
        kind = envelope.get("kind")
        fold = self._folds.get(kind)
        if fold is None:
            problems.append(f"record.kind must be one of {list(RECORD_KINDS)}, got {kind!r}")
        seq, expected = envelope.get("seq"), self._head_seq + 1
        if isinstance(seq, bool) or not isinstance(seq, int) or seq != expected:
            problems.append(f"record.seq must be the next dense seq {expected}, got {seq!r}")
        if envelope.get("series_id") != self._series_id:
            problems.append(
                f"record.series_id {envelope.get('series_id')!r} is not this fold's "
                f"{self._series_id!r}"
            )
        if envelope.get("prev_hash") != self._head_hash:
            problems.append(
                f"record.prev_hash {envelope.get('prev_hash')!r} is not the folded head "
                f"{self._head_hash!r}"
            )
        _check_str(problems, "record.hash", envelope.get("hash"))
        _check_str(problems, "record.release_hash", envelope.get("release_hash"))
        _check_dict(problems, "record.body", envelope.get("body"))
        if problems:
            raise ProductionError(problems)
        method, economic = fold
        method(envelope["body"], envelope)
        self._head_seq, self._head_hash = seq, envelope["hash"]
        if economic:
            self._economic_seq += 1

    def _fold_nothing(self, body, envelope):
        """Fold a kind that projects nothing beyond the head."""

    def _fold_tick_start(self, body, envelope):
        """Open a tick; the body's ``release_hash`` falls back on the envelope's."""
        start = TickStart(
            tick_id=body.get("tick_id"),
            tick_at_ms=body.get("tick_at_ms"),
            release_hash=body.get("release_hash", envelope["release_hash"]),
        )
        self._open_ticks[start.tick_id] = start
        self._tick_plans.setdefault(start.tick_id, [])

    def _fold_tick(self, body, envelope):
        """Close the tick the terminal record names."""
        self._open_ticks.pop(body.get("tick_id"), None)

    def _fold_decision(self, body, envelope):
        """Append the decision's legs to the history and settle the tick's plans."""
        problems = []
        tick_id = body.get("tick_id")
        _check_str(problems, "decision.tick_id", tick_id)
        legs = body.get("legs")
        if not isinstance(legs, list):
            problems.append(f"decision.legs must be a list, got {legs!r}")
        else:
            for position, leg in enumerate(legs):
                _check_dict(problems, f"decision.legs[{position}]", leg)
        if problems:
            raise ProductionError(problems)
        self._history.extend(dict(leg, tick_id=tick_id) for leg in legs)
        self._tick_plans.pop(tick_id, None)

    def _fold_decision_plan(self, body, envelope):
        """Note the plan under the tick under way, keyed by id and digest."""
        problems = []
        _check_str(problems, "decision_plan.plan_id", body.get("plan_id"))
        if body.get("result") not in PLAN_RESULTS:
            problems.append(
                f"decision_plan.result must be one of {list(PLAN_RESULTS)}, got {body.get('result')!r}"
            )
        if problems:
            raise ProductionError(problems)
        if self._tick_plans:
            self._tick_plans[next(reversed(self._tick_plans))].append(
                {
                    "plan_id": body["plan_id"],
                    "decision_plan_digest": canonical_hash(body),
                    "result": body["result"],
                    "client_ref": None,
                }
            )

    def _fold_intent(self, body, envelope):
        """Record a pending order for the intent and match it to its plan."""
        problems = []
        _check_dict(problems, "intent.proposal", body.get("proposal"))
        if problems:
            raise ProductionError(problems)
        proposal = body["proposal"]
        order = OrderState(
            client_ref=body.get("client_ref"),
            venue_ref=None,
            status="pending",
            ts_ms=body.get("created_ms"),
            filled_qty=_ZERO,
            avg_price=None,
            fee=_ZERO,
            reason="",
            native=None,
            instrument=proposal.get("instrument"),
            side=proposal.get("side"),
            qty=proposal.get("qty"),
            remaining_qty=proposal.get("qty"),
            limit=proposal.get("limit"),
            tif=proposal.get("tif"),
            created_ms=body.get("created_ms"),
            updated_ms=body.get("created_ms"),
        )
        self._pending[order.client_ref] = order
        self._match_plan(
            body.get("decision_plan_id"), body.get("decision_plan_digest"), order.client_ref
        )

    def _match_plan(self, plan_id, digest, client_ref):
        """Mark the plan an intent binds — by id AND digest — as having its intent."""
        for plans in self._tick_plans.values():
            for entry in plans:
                if (
                    entry["client_ref"] is None
                    and entry["plan_id"] == plan_id
                    and entry["decision_plan_digest"] == digest
                ):
                    entry["client_ref"] = client_ref
                    return

    def _fold_order_event(self, body, envelope):
        """Move the ref out of pending; a terminal status leaves working."""
        problems = []
        _check_str(problems, "order_event.client_ref", body.get("client_ref"))
        _check_str(problems, "order_event.status", body.get("status"))
        if problems:
            raise ProductionError(problems)
        ref, status = body["client_ref"], body["status"]
        order = self._working.get(ref) or self._pending.get(ref)
        if order is None:
            return
        at_ms = body.get("recv_at_ms")
        order = dataclasses.replace(
            order,
            venue_ref=body.get("venue_ref"),
            status=status,
            ts_ms=at_ms,
            updated_ms=at_ms,
            reason=body.get("reason") or "",
        )
        self._pending.pop(ref, None)
        if status in TERMINAL_STATUSES:
            self._working.pop(ref, None)
            self._filled_notional.pop(ref, None)
        else:
            self._working[ref] = order

    def _fold_fill(self, body, envelope):
        """Move the book and the working order by the fill's status."""
        fill = _record_from_body(Fill, body)
        self._fill_folds[fill.status](fill)

    def _apply_fill(self, fill):
        """Apply a pending or final fill."""
        self._book.apply(fill)
        self._fill_working(fill, 1)

    def _reverse_fill(self, fill):
        """Undo a fill the venue reversed, exactly once."""
        self._book.reverse(fill.fill_id)
        self._fill_working(fill, -1)

    def _fill_working(self, fill, sign):
        """Move the working order's filled quantity, exact average price and fee."""
        order = self._working.get(fill.client_ref)
        if order is None:
            return
        filled = order.filled_qty + sign * fill.qty
        notional = self._filled_notional.get(fill.client_ref, Fraction(0))
        notional += sign * Fraction(fill.price) * Fraction(fill.qty)
        self._filled_notional[fill.client_ref] = notional
        self._working[fill.client_ref] = dataclasses.replace(
            order,
            filled_qty=filled,
            remaining_qty=order.qty - filled,
            avg_price=None if filled == 0 else _ratio(notional / Fraction(filled)),
            fee=order.fee + sign * fill.fee,
        )

    def _fold_cash_flow(self, body, envelope):
        """Book the signed amount, netting out the record it supersedes."""
        problems = []
        _check_str(problems, "cash_flow.currency", body.get("currency"))
        amount = _money(problems, "cash_flow.amount", body.get("amount"))
        record_id = envelope.get("id")
        _check_str(problems, "cash_flow.id", record_id)
        superseded = body.get("supersedes")
        if superseded is not None:
            self._check_supersedable(problems, superseded)
        if problems:
            raise ProductionError(problems)
        if superseded is not None:
            self._unbook(superseded, record_id)
        currency = body["currency"]
        self._balances[currency] = self._balances.get(currency, _ZERO) + amount
        self._cash_flows[record_id] = (currency, amount, None)

    def _check_supersedable(self, problems, superseded):
        """Why ``supersedes`` cannot be netted out, if it cannot."""
        if not isinstance(superseded, str):
            problems.append(
                f"cash_flow.supersedes must be a record id, got {superseded!r}"
            )
            return
        booked = self._cash_flows.get(superseded)
        if booked is None:
            problems.append(
                f"cash_flow.supersedes names {superseded!r}, which this fold never booked"
            )
        elif booked[2] is not None:
            problems.append(
                f"cash_flow {superseded!r} was already superseded by {booked[2]!r}"
            )

    def _unbook(self, superseded, record_id):
        """Reverse a booked cash flow in its own currency and mark who replaced it."""
        currency, amount, _ = self._cash_flows[superseded]
        self._balances[currency] = self._balances.get(currency, _ZERO) - amount
        self._cash_flows[superseded] = (currency, amount, record_id)

    def _fold_authority(self, body, envelope):
        """Dispatch an authority event by role and event."""
        problems = []
        role, event = body.get("role"), body.get("event")
        if role not in AUTHORITY_ROLES:
            problems.append(f"authority.role must be one of {list(AUTHORITY_ROLES)}, got {role!r}")
        if event not in AUTHORITY_EVENTS:
            problems.append(
                f"authority.event must be one of {list(AUTHORITY_EVENTS)}, got {event!r}"
            )
        _check_str(problems, "authority.authority_id", body.get("authority_id"))
        if problems:
            raise ProductionError(problems)
        self._authority_folds[(role, event)](body)

    def _issue_arming(self, body):
        """Fold the embedded ``ArmingState`` of an ordinary issue."""
        self._arming = ArmingProjection.from_obj(body.get("arming"))

    def _clear_arming(self, body):
        """End the ordinary arm the event names, if it is the current one."""
        if self._arming is not None and self._arming.authority_id == body["authority_id"]:
            self._arming = None

    def _issue_rights(self, body):
        """Fold the embedded ``ReductionAuthorization`` of a reduction issue."""
        grant = ReductionAuthorization.from_obj(body.get("authorization"))
        self._reduction = ReductionProjection(
            authority_id=grant.authority_id,
            rights=grant.reduction_intent_digests,
            reserved=(),
            expires_ms=grant.expires_ms,
        )

    def _clear_rights(self, body):
        """End the reduction authority the event names, if it is the current one."""
        if self._reduction is not None and self._reduction.authority_id == body["authority_id"]:
            self._reduction = None

    def _fold_authority_use(self, body, envelope):
        """Reserve one granted, unreserved reduction right."""
        problems = []
        _check_str(problems, "authority_use.authority_id", body.get("authority_id"))
        _check_str(
            problems, "authority_use.reduction_intent_digest", body.get("reduction_intent_digest")
        )
        if problems:
            raise ProductionError(problems)
        current = self._reduction
        if current is None or current.authority_id != body["authority_id"]:
            raise ProductionError(
                [
                    f"authority_use names {body['authority_id']!r}, which is not the current "
                    "reduction authority"
                ]
            )
        self._reduction = current.reserve(body["reduction_intent_digest"])

    def _hold_key(self, body, where):
        """Return the ``(guard, scope_key)`` a ``guard_state`` body is held under."""
        problems = []
        _check_str(problems, f"{where}.guard", body.get("guard"))
        _check_str(problems, f"{where}.scope_key", body.get("scope_key"))
        if body.get("state_kind") not in GUARD_STATE_KINDS:
            problems.append(
                f"{where}.state_kind must be one of {list(GUARD_STATE_KINDS)}, "
                f"got {body.get('state_kind')!r}"
            )
        if problems:
            raise ProductionError(problems)
        return (body["guard"], body["scope_key"])

    def _fold_guard_state(self, body, envelope):
        """Hold or pause the guard's scope until a later record replaces it."""
        self._guard_holds[self._hold_key(body, "guard_state")] = dict(body)

    def _fold_readiness(self, body, envelope):
        """Replace the readiness evaluation."""
        self._readiness = ReadinessProjection.from_body(body)

    def _fold_trip(self, body, envelope):
        """Move the breaker and remember the trip; every transition revokes the ordinary arm (D10)."""
        problems = []
        target = body.get("to")
        if target not in BREAKER_STATES:
            problems.append(f"trip.to must be one of {list(BREAKER_STATES)}, got {target!r}")
        _check_str(problems, "trip record.id", envelope.get("id"))
        check_int_param(problems, "trip record.recorded_at_ms", envelope.get("recorded_at_ms"), ge=0)
        if problems:
            raise ProductionError(problems)
        self._breaker = target
        self._arming = None
        self._last_trip = {
            "id": envelope["id"],
            "seq": envelope["seq"],
            "recorded_at_ms": envelope["recorded_at_ms"],
            "from": body.get("from"),
            "to": target,
            "reason": body.get("reason"),
            "acknowledged_trip_id": body.get("acknowledged_trip_id"),
        }

    def _fold_control_request(self, body, envelope):
        """Queue the request until its ``command_result``."""
        problems = []
        _check_str(problems, "control_request.request_id", body.get("request_id"))
        _check_str(problems, "control_request.purpose", body.get("purpose"))
        if problems:
            raise ProductionError(problems)
        self._pending_control[body["request_id"]] = body["purpose"]

    def _fold_command_result(self, body, envelope):
        """Consume the request the result answers."""
        self._pending_control.pop(body.get("request_id"), None)

    def _fold_monitor(self, body, envelope):
        """Keep the latest verdict per monitor and slice."""
        problems = []
        _check_str(problems, "monitor.monitor", body.get("monitor"))
        _check_str(problems, "monitor.slice", body.get("slice"))
        if problems:
            raise ProductionError(problems)
        self._monitor_state.setdefault(body["monitor"], {})[body["slice"]] = dict(body)

    # -- the projections ----------------------------------------------------

    def head(self):
        """Return what the fold has folded.

        Returns
        -------
        tuple
            ``(seq, hash)``; ``(0, GENESIS_HASH)`` before the first record.
        """
        return (self._head_seq, self._head_hash)

    def snapshot(self):
        """Project the fold as a frozen view.

        Returns
        -------
        StateView
            Immutable; a later ``apply`` never moves it.
        """
        return StateView(
            positions=self._book.positions(),
            working=MappingProxyType(dict(self._working)),
            pending=tuple(self._pending),
            balances=MappingProxyType(dict(self._balances)),
            decision_history=tuple(MappingProxyType(entry) for entry in self._history),
            breaker=self._breaker,
            arming=self._arming,
            readiness=self._readiness,
            guard_holds=MappingProxyType(
                {key: MappingProxyType(hold) for key, hold in self._guard_holds.items()}
            ),
            reduction=self._reduction,
            pending_control=MappingProxyType(dict(self._pending_control)),
            risk_version=RiskVersion(
                economic_seq=self._economic_seq, executor_token=None, accounting_tokens=None
            ),
            head_seq=self._head_seq,
            head_hash=self._head_hash,
        )

    def monitor_state(self):
        """Return the latest ``monitor`` verdict body per monitor and slice.

        Returns
        -------
        mapping
            Read-only ``monitor -> slice -> body``; what the snapshot
            carries so a restart does not forget the last verdicts.
        """
        return MappingProxyType(
            {monitor: MappingProxyType(dict(slices)) for monitor, slices in self._monitor_state.items()}
        )

    def last_trip(self):
        """Return the latest breaker transition the fold holds.

        The breaker's ``reset`` acknowledges this trip's ``id`` and measures
        cooling-off from its ``recorded_at_ms`` — read here, from the fold,
        because nothing but the fold walks the ledger (§5.8.1).

        Returns
        -------
        mapping or None
            Read-only ``{id, seq, recorded_at_ms, from, to, reason,
            acknowledged_trip_id}`` of the latest ``trip`` record; None
            before any transition.
        """
        return None if self._last_trip is None else MappingProxyType(dict(self._last_trip))

    def open_ticks(self):
        """Return the ticks started but not yet given a terminal ``tick``.

        Returns
        -------
        tuple of TickStart
            In start order; what recovery closes.
        """
        return tuple(self._open_ticks.values())

    def undecided_ticks(self):
        """Return the ids of ticks started but not yet given a ``decision``.

        Returns
        -------
        tuple of str
            In start order.
        """
        return tuple(self._tick_plans)

    def tick_plans(self, tick_id):
        """Return the plans recorded under an undecided tick, in leg order.

        Parameters
        ----------
        tick_id : str

        Returns
        -------
        tuple of mapping
            One read-only entry per ``decision_plan``: ``plan_id``,
            ``decision_plan_digest`` (the canonical hash of its body),
            ``result`` and ``client_ref`` — the intent that bound the plan
            by id and digest, or None while none has. Empty once the
            tick's ``decision`` is folded.
        """
        return tuple(
            MappingProxyType(dict(entry)) for entry in self._tick_plans.get(tick_id, ())
        )

    # -- the snapshot payload and its restore -------------------------------

    def to_snapshot_obj(self):
        """Render the fold as the JSON-able ``state`` of a §6 ``snapshot``.

        Returns
        -------
        dict
            Every ``StateView`` member plus ``monitor_state``,
            ``open_ticks``, ``tick_plans``, ``last_trip`` and
            ``cash_flows`` (the booked amount a later correction nets
            against, one :data:`_CASH_FLOW_KEYS` entry per record id).
            ``positions`` is :meth:`PositionBook.to_obj` (the fill logs);
            each ``working``
            entry pairs the ``OrderState`` form with the exact
            ``filled_notional`` its average price is rendered from;
            ``pending`` holds ``OrderState`` forms, ``guard_holds`` a list
            of bodies, and ``risk_version`` carries no session token.
        """
        view = self.snapshot()
        return {
            "positions": self._book.to_obj(),
            "working": {
                ref: {
                    "order": order.to_obj(),
                    "filled_notional": str(self._filled_notional.get(ref, Fraction(0))),
                }
                for ref, order in self._working.items()
            },
            "pending": [order.to_obj() for order in self._pending.values()],
            "balances": _jsonable(self._balances),
            "decision_history": [dict(entry) for entry in self._history],
            "breaker": self._breaker,
            "arming": None if self._arming is None else self._arming.to_obj(),
            "readiness": None if self._readiness is None else self._readiness.to_obj(),
            "guard_holds": [dict(hold) for hold in self._guard_holds.values()],
            "reduction": None if self._reduction is None else self._reduction.to_obj(),
            "pending_control": dict(self._pending_control),
            "risk_version": view.risk_version.to_obj(),
            "head_seq": self._head_seq,
            "head_hash": self._head_hash,
            "monitor_state": _jsonable(self._monitor_state),
            "open_ticks": [start.to_obj() for start in self._open_ticks.values()],
            "tick_plans": _jsonable(self._tick_plans),
            "last_trip": None if self._last_trip is None else dict(self._last_trip),
            "cash_flows": {
                record_id: dict(zip(_CASH_FLOW_KEYS, _jsonable(booked)))
                for record_id, booked in self._cash_flows.items()
            },
        }

    def restore(self, snapshot_env):
        """Rebuild a fresh fold from a ``snapshot`` envelope.

        Session tokens in ``risk_version`` are dropped (they are
        re-acquired, never restored); ``economic_seq`` is kept. The head
        becomes the snapshot's ``at_seq`` and the payload's ``head_hash``,
        so the snapshot record itself is the next envelope to ``apply``.

        Parameters
        ----------
        snapshot_env : dict
            A §6 ``snapshot`` envelope whose body is ``{at_seq,
            state_digest, state}``.

        Raises
        ------
        ProductionError
            If the record is not a snapshot, this fold has already folded
            a record, the payload lacks or adds a member, ``at_seq``
            disagrees with the head the payload carries, or a member is
            malformed. The fold is unchanged.
        """
        problems = []
        _check_dict(problems, "snapshot", snapshot_env)
        if problems:
            raise ProductionError(problems)
        if snapshot_env.get("kind") != _SNAPSHOT:
            problems.append(f"restore needs a snapshot record, got kind {snapshot_env.get('kind')!r}")
        if self._head_seq != 0:
            problems.append("restore needs a fresh fold; this one has already folded records")
        _check_dict(problems, "snapshot.body", snapshot_env.get("body"))
        if problems:
            raise ProductionError(problems)
        body = snapshot_env["body"]
        at_seq, state = body.get("at_seq"), body.get("state")
        check_int_param(problems, "snapshot.at_seq", at_seq, ge=0)
        _exact(problems, "snapshot.state", state, _SNAPSHOT_KEYS)
        if problems:
            raise ProductionError(problems)
        check_int_param(problems, "snapshot.state.head_seq", state["head_seq"], ge=0)
        _check_str(problems, "snapshot.state.head_hash", state["head_hash"])
        if not problems and state["head_seq"] != at_seq:
            problems.append(
                f"snapshot.at_seq {at_seq} disagrees with the head_seq {state['head_seq']} "
                "the payload carries"
            )
        if problems:
            raise ProductionError(problems)
        members = self._restore_members(state)
        for name, value in members.items():
            setattr(self, name, value)
        self._head_seq, self._head_hash = at_seq, state["head_hash"]

    def _restore_members(self, state):
        """Rebuild every internal structure from the payload; nothing is assigned here."""
        problems = []
        for name in ("working", "pending_control", "monitor_state", "tick_plans",
                     "balances", "cash_flows"):
            _check_dict(problems, f"snapshot.state.{name}", state[name])
        for name in ("pending", "decision_history", "guard_holds", "open_ticks"):
            if not isinstance(state[name], list):
                problems.append(f"snapshot.state.{name} must be a list, got {state[name]!r}")
        if state["breaker"] not in BREAKER_STATES:
            problems.append(
                f"snapshot.state.breaker must be one of {list(BREAKER_STATES)}, "
                f"got {state['breaker']!r}"
            )
        _check_dict(problems, "snapshot.state.risk_version", state["risk_version"])
        if state["last_trip"] is not None:
            _exact(problems, "snapshot.state.last_trip", state["last_trip"], _LAST_TRIP_KEYS)
        if problems:
            raise ProductionError(problems)
        check_int_param(
            problems, "snapshot.state.risk_version.economic_seq",
            state["risk_version"].get("economic_seq"), ge=0,
        )
        balances = {
            currency: _money(problems, f"snapshot.state.balances.{currency}", amount)
            for currency, amount in state["balances"].items()
        }
        for request_id, purpose in state["pending_control"].items():
            _check_str(problems, f"snapshot.state.pending_control.{request_id}", purpose)
        for position, entry in enumerate(state["decision_history"]):
            _check_dict(problems, f"snapshot.state.decision_history[{position}]", entry)
        for monitor, slices in state["monitor_state"].items():
            _check_dict(problems, f"snapshot.state.monitor_state.{monitor}", slices)
            for name, verdict in (slices.items() if isinstance(slices, dict) else ()):
                _check_dict(problems, f"snapshot.state.monitor_state.{monitor}.{name}", verdict)
        for tick_id, plans in state["tick_plans"].items():
            if not isinstance(plans, list):
                problems.append(f"snapshot.state.tick_plans.{tick_id} must be a list, got {plans!r}")
                continue
            for position, entry in enumerate(plans):
                _exact(problems, f"snapshot.state.tick_plans.{tick_id}[{position}]", entry, _PLAN_KEYS)
        for ref, entry in state["working"].items():
            _exact(problems, f"snapshot.state.working.{ref}", entry, ("order", "filled_notional"))
        for record_id, entry in state["cash_flows"].items():
            _exact(problems, f"snapshot.state.cash_flows.{record_id}", entry, _CASH_FLOW_KEYS)
        if problems:
            raise ProductionError(problems)
        cash_flows = {
            record_id: self._booked_flow(problems, record_id, entry)
            for record_id, entry in state["cash_flows"].items()
        }
        if problems:
            raise ProductionError(problems)
        filled_notional = {
            ref: _fraction(
                problems, f"snapshot.state.working.{ref}.filled_notional", entry["filled_notional"]
            )
            for ref, entry in state["working"].items()
        }
        if problems:
            raise ProductionError(problems)
        history = deque(maxlen=self._max_history)
        history.extend(dict(entry) for entry in state["decision_history"])
        pending = [OrderState.from_obj(obj) for obj in state["pending"]]
        return {
            "_book": PositionBook.from_obj(state["positions"]),
            "_working": {
                ref: OrderState.from_obj(entry["order"]) for ref, entry in state["working"].items()
            },
            "_filled_notional": filled_notional,
            "_pending": {order.client_ref: order for order in pending},
            "_balances": balances,
            "_history": history,
            "_breaker": state["breaker"],
            "_arming": None if state["arming"] is None else ArmingProjection.from_obj(state["arming"]),
            "_readiness": (
                None if state["readiness"] is None else ReadinessProjection.from_obj(state["readiness"])
            ),
            "_guard_holds": {
                self._hold_key(hold, f"snapshot.state.guard_holds[{position}]"): dict(hold)
                for position, hold in enumerate(state["guard_holds"])
            },
            "_reduction": (
                None if state["reduction"] is None else ReductionProjection.from_obj(state["reduction"])
            ),
            "_pending_control": dict(state["pending_control"]),
            "_economic_seq": state["risk_version"]["economic_seq"],
            "_monitor_state": {
                monitor: {name: dict(verdict) for name, verdict in slices.items()}
                for monitor, slices in state["monitor_state"].items()
            },
            "_open_ticks": {
                start.tick_id: start
                for start in (TickStart.from_obj(obj) for obj in state["open_ticks"])
            },
            "_tick_plans": {
                tick_id: [dict(entry) for entry in plans]
                for tick_id, plans in state["tick_plans"].items()
            },
            "_last_trip": None if state["last_trip"] is None else dict(state["last_trip"]),
            "_cash_flows": cash_flows,
        }

    def _booked_flow(self, problems, record_id, entry):
        """One restored ``_cash_flows`` value from its payload entry."""
        where = f"snapshot.state.cash_flows.{record_id}"
        _check_str(problems, f"{where}.currency", entry["currency"])
        amount = _money(problems, f"{where}.amount", entry["amount"])
        superseded_by = entry["superseded_by"]
        if superseded_by is not None:
            _check_str(problems, f"{where}.superseded_by", superseded_by)
        return (entry["currency"], amount, superseded_by)


# ---------------------------------------------------------------------------
# Recovery (§5.8.1, §5.13, D13)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryReport:
    """What one recovery did.

    Parameters
    ----------
    replayed : int
        Envelopes folded from the last snapshot forward, the snapshot
        record included.
    closed_ticks : tuple of str
        Tick ids given a terminal ``tick`` and/or ``decision``.
    queried_refs : tuple of str
        Client refs asked of the executor, each answered by one
        ``order_event``.

    Examples
    --------
    ::

        report = RecoveryReport(replayed=7, closed_ticks=("tick-1",), queried_refs=("ref-2",))
        report.replayed  # 7
    """

    replayed: int
    closed_ticks: tuple
    queried_refs: tuple


class Recovery:
    """Replay the fold and close what a crash left open — before the scheduler exists (§5.8.1).

    ``run`` restores the state from the ledger's last ``snapshot`` (a fresh
    fold only) and replays every later envelope; appends an empty-legged
    ``decision`` for each undecided tick and a ``failed`` ``tick`` for each
    open one — in that order, the LIVE one, so a recovered tick reads on
    the chain exactly as a tick that ran does — preserving recorded
    findings without rerunning them. The terminal tick states what nobody
    observed as ``null``: a tick that never ran has no ``feed``,
    ``calendar``, ``health`` or ``rung``, and inventing one would hand a
    monitor a reading it never took;
    queries ``executor.order(ref)`` for every ambiguous client ref — each
    pending intent, and each submitted plan whose intent never landed,
    whose ref is derived from the ``IdSource`` as the leg would have —
    and records the answer as an ``order_event`` (``status``/the answer's
    status, or ``unknown``/``unknown`` when the venue cannot say); then
    appends a ``recovered`` ``process`` record and barriers. It never
    submits and never cancels. Its own appends fold through the ledger,
    as every append does.

    Parameters
    ----------
    ledger : Ledger
        Provides ``latest_snapshot``, ``scan``, ``append``, ``barrier``,
        ``head``.
    state : SeriesState
        The fold to rebuild; attached to ``ledger`` by the caller.
    id_source : IdSource
        ``client_ref(tick_id, leg_index, attempt)`` derives the ref a
        plan's intent would have carried.
    executor : Executor
        ``order(ref)`` is the only verb used.

    Raises
    ------
    ProductionError
        If a collaborator lacks a method recovery calls.

    Examples
    --------
    ::

        state = SeriesState(serve.series_id)
        ledger = JsonlLedger(serve, "proc-2", release_hash, clock=clock, state=state)
        report = Recovery(ledger, state, ReleaseIdSource(release_hash), executor).run(clock)
        (report.replayed, report.closed_ticks, report.queried_refs)
        # -> (7, ('tick-1',), ('ref-2',))
    """

    def __init__(self, ledger, state, id_source, executor):
        problems = []
        needs = (
            ("ledger", ledger, ("latest_snapshot", "scan", "append", "barrier", "head")),
            ("state", state, ("apply", "restore", "head", "snapshot", "open_ticks",
                              "undecided_ticks", "tick_plans")),
            ("id_source", id_source, ("client_ref",)),
            ("executor", executor, ("order",)),
        )
        for name, collaborator, methods in needs:
            for method in methods:
                if not callable(getattr(collaborator, method, None)):
                    problems.append(f"{name} must provide {method}(), got {collaborator!r}")
        if problems:
            raise ProductionError(problems)
        self._ledger = ledger
        self._state = state
        self._ids = id_source
        self._executor = executor

    def run(self, clock):
        """Recover the series.

        Parameters
        ----------
        clock : Clock
            ``now_ms()`` stamps the closing records.

        Returns
        -------
        RecoveryReport
            What was replayed, closed and queried.
        """
        replayed = self._replay()
        now = clock.now_ms()
        closed, derived = self._close_ticks(now)
        pending = self._state.snapshot().pending
        queried = []
        for ref in derived + [ref for ref in pending if ref not in derived]:
            self._query(ref, now)
            queried.append(ref)
        report = RecoveryReport(replayed, tuple(closed), tuple(queried))
        self._ledger.append(
            {
                "kind": "process",
                "id": f"recovered-process-{self._state.head()[0]}",
                "body": {
                    "event": _RECOVERED,
                    "series_id": self._state.series_id,
                    "replayed": report.replayed,
                    "closed_ticks": list(report.closed_ticks),
                    "queried_refs": list(report.queried_refs),
                },
            }
        )
        self._ledger.barrier()
        _log.info(
            "recovered series %s: replayed %d, closed %s, queried %s",
            self._state.series_id, replayed, list(closed), list(queried),
        )
        return report

    def _replay(self):
        """Restore a fresh fold from the last snapshot, then fold everything after the head."""
        snapshot = self._ledger.latest_snapshot()
        if snapshot is not None and self._state.head()[0] == 0:
            self._state.restore(snapshot)
        replayed = 0
        for envelope in self._ledger.scan(since_seq=self._state.head()[0]):
            self._state.apply(envelope)
            replayed += 1
        return replayed

    def _close_ticks(self, now):
        """Terminalise every open or undecided tick; return the ids closed and the refs to derive."""
        opens = {start.tick_id: start for start in self._state.open_ticks()}
        undecided = list(self._state.undecided_ticks())
        closed, derived = [], []
        for tick_id in undecided + [tick_id for tick_id in opens if tick_id not in undecided]:
            plans = self._state.tick_plans(tick_id)
            if tick_id in undecided:
                self._ledger.append(self._empty_decision(tick_id, plans))
            if tick_id in opens:
                self._ledger.append(self._failed_tick(opens[tick_id], now))
            closed.append(tick_id)
            for index, plan in enumerate(plans):
                if plan["result"] == _SUBMIT and plan["client_ref"] is None:
                    derived.append(self._ids.client_ref(tick_id, index, 0))
        return closed, derived

    def _failed_tick(self, start, now):
        """Build the terminal ``tick`` a crash denied ``start``."""
        return {
            "kind": "tick",
            "id": f"recovered-tick-{start.tick_id}",
            "body": {
                "tick_id": start.tick_id,
                "tick_at": start.tick_at_ms,
                "data_asof_ms": None,
                "observed_at_ms": now,
                "status": _FAILED,
                "feed": None,
                "inputs_digest": None,
                "nav": None,
                "calendar": None,
                "overrun_absorbed": [],
                "latency_ms": {},
                "leg_latency_ms": {},
                "health": None,
                "breaker": self._state.snapshot().breaker,
                "rung": None,
                "refusal_reason": None,
                "error": {
                    "class": _RECOVERED,
                    "text": "the process ended before this tick reached a terminal record",
                },
            },
        }

    def _empty_decision(self, tick_id, plans):
        """Build the zero-leg ``decision`` closing ``tick_id``, naming the plans it recorded."""
        return {
            "kind": "decision",
            "id": f"recovered-decision-{tick_id}",
            "body": {
                "tick_id": tick_id,
                "decision_plan_ids": [plan["plan_id"] for plan in plans],
                "decision_plan_digests": [plan["decision_plan_digest"] for plan in plans],
                "legs": [],
                "reason": "recovered: the process ended before this tick decided",
            },
        }

    def _query(self, ref, now):
        """Ask the venue about ``ref`` and record the answer; never resend."""
        try:
            answer = self._executor.order(ref)
            body = {
                "client_ref": ref,
                "venue_ref": answer.venue_ref,
                "event": _STATUS_EVENT,
                "status": answer.status,
                "venue_ts_ms": answer.ts_ms,
                "recv_at_ms": now,
                "reason": None,
            }
        except Exception as exc:  # the venue could not say: record the ambiguity, not a guess
            body = {
                "client_ref": ref,
                "venue_ref": None,
                "event": _UNKNOWN,
                "status": _UNKNOWN,
                "venue_ts_ms": None,
                "recv_at_ms": now,
                "reason": redact(f"{type(exc).__name__}: {exc}"),
            }
        self._ledger.append({"kind": "order_event", "id": f"recovered-order_event-{ref}", "body": body})
