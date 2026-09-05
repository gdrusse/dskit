"""Ours against theirs, and the one authenticated way to adopt a break (plan §5.9).

The reconciler is the only object that holds BOTH sides of the account:
what the fold believes — the frozen ``StateView`` the caller hands in, plus
the ledger's own fill history read through :class:`LedgerHistory` — and
what the venue answers when asked. Three rulings shape the module:

* **D13 — record before act, and the reconciler never acts.** A run
  appends exactly one ``recon`` record and barriers it. It synthesises no
  venue action: no ``order_event`` for an order it did not know, no
  ``fill``, no ``trip``, no cancel. :meth:`Reconciler.apply_policy` names
  what the LOOP should do — ``document.reconcile.on_mismatch``, which
  admits only ``halt`` or ``refuse`` — and tripping the breaker is the
  loop's call, made after the record is durable.
* **§5.9 — an unknown venue order is ``external``, never silently ours.**
  Every break carries an ``origin``; the only automatic resolutions are
  the document's halt-or-refuse. Adoption is :meth:`Reconciler.adopt`: a
  separate, authenticated operator command naming break ids and the
  release hash, and the only class it may resolve is ``cash``.
* **§6 / D21 — the money is recorded as a VALUE, once.** ``adopt``
  appends a ``cash_flow`` carrying the amount and both instants BEFORE
  the ``adoption`` receipt and inside the SAME barrier, under
  ``id = H("cash-flow-v1", release_hash, control_request_id, break_id)``
  so a crash-replayed adopt cannot bank the same money twice. Returns
  cannot be recomputed from a digest; this is the only moment the amount
  is knowable.

Classification is pure and table-driven: :func:`classify_breaks` walks the
five :data:`RECON_DOMAINS` through one strategy object each, every break
class has exactly one severity (:data:`BREAK_SEVERITY_BY_CLASS`) and one
origin (:data:`BREAK_ORIGIN_BY_CLASS`), and every compared field maps to
its class through :data:`ORDER_BREAK_CLASS` / :data:`FILL_BREAK_CLASS` —
a new field cannot arrive without a class, and there is no
``if break_class ==`` chain to grow. A break's id is
``H("break-v1", class, subject)`` and nothing else, so a break an
operator inspected keeps its id when only the amounts moved.

Two readings the plan leaves open are pinned here. A venue answer for an
order strictly NEWER than our last fold of it is ``timing`` — the ledger
not having caught up, the race ``lookback_ms`` exists for — while an
answer at or before our own instant is a real disagreement. And our side
of ``balances`` is the EXPECTED balance: the fold's cash flows plus what
every fill ever cost or paid, so a venue total can be compared with it
and a fill older than the window is not mistaken for unexplained cash.

Nothing here reads wall time: the clock is injected and ``lookback_ms`` is
measured from it. Nothing here folds the ledger: ours is the view the
caller hands in, and :class:`LedgerHistory` is the one reader of fill,
cash-flow and mark history — ``accounting.py`` reads it too, which is why
it is public.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation

from dskit.pipeline.node import check_int_param
from dskit.production.base import (
    ProductionError,
    _check_dict,
    _check_str,
    _check_unknown,
    canonical_hash,
    pin_members,
)
from dskit.production.records import (
    Balance,
    ExecutionScope,
    Fill,
    OrderState,
    Position,
    Settlement,
)
from dskit.production.redact import get_logger
from dskit.production.vocab import (
    BREAK_CLASSES,
    BREAK_ORIGINS,
    BREAK_SEVERITIES,
    CASH_FLOW_KINDS,
    FILL_STATUSES,
    MONEY_FIELDS,
    ON_MISMATCH,
    OUTCOME_KINDS,
    POSITION_SOURCES,
    RECON_ACTIONS,
    RECORD_KINDS,
    SIDES,
    STATUSES,
    TRIP_REASONS,
)

__all__ = [
    "BREAK_ID_TAG",
    "BREAK_ORIGIN_BY_CLASS",
    "BREAK_SEVERITY_BY_CLASS",
    "Break",
    "CASH_FLOW_ID_TAG",
    "FILL_BREAK_CLASS",
    "FILL_FIELDS",
    "LedgerHistory",
    "MAX_FILL_PAGES",
    "ORDER_BREAK_CLASS",
    "ORDER_FIELDS",
    "RECON_DOMAINS",
    "RECONCILE_TRIP_REASON",
    "RECON_ID_TAG",
    "ReconReport",
    "Reconciler",
    "SETTLEMENT_FIELDS",
    "classify_breaks",
    "enact",
]

_LOG = get_logger("reconcile")

#: The breaker's reason for a reconciliation that could not be explained.
#: One owner: both the loop's scheduled run and the operator ``reconcile``
#: verb trip under this name, and neither spells it again (§5.9, §5.6).
RECONCILE_TRIP_REASON = pin_members(
    "reconcile.py's mismatch trip", ("reconcile_mismatch",), TRIP_REASONS
)[0]

_HALT, _REFUSE, _NONE = pin_members(
    "reconcile.py's actions", ("halt", "refuse", "none"), RECON_ACTIONS, exact=True
)


def _enact_halt(breaker, verifier, actor, control_request_id, reason):
    """Trip: a halted series already refuses every submit, so nothing else is owed."""
    breaker.trip(RECONCILE_TRIP_REASON, actor, control_request_id=control_request_id)


def _enact_refuse(breaker, verifier, actor, control_request_id, reason):
    """Stop sends without halting — the whole difference between the two policies."""
    verifier.refuse_until_reconciled(reason)


def _enact_none(breaker, verifier, actor, control_request_id, reason):
    """Clear the disable: a clean run is what resolves an ambiguous reference."""
    verifier.reset_after_reconcile()


#: What each ``RECON_ACTIONS`` member DOES. A table rather than a branch, so
#: a member with no effect is a missing key and a test failure rather than a
#: silent fall-through — the defect that let ``refuse`` be computed and
#: dropped at two call sites.
_ENACT = {_HALT: _enact_halt, _REFUSE: _enact_refuse, _NONE: _enact_none}


def enact(action, *, breaker, verifier, actor, control_request_id=None, reason=None):
    """Do what ``Reconciler.apply_policy`` named — the ONE owner of that mapping.

    ``apply_policy`` names and never acts (D13), and two callers act on its
    answer: the loop's scheduled run and the operator ``reconcile`` verb.
    Stating the mapping in each of them is how ``refuse`` came to be computed
    and dropped in both, so it is stated here once and imported.

    Parameters
    ----------
    action : str
        A ``vocab.RECON_ACTIONS`` member, as ``apply_policy`` returned it.
    breaker : Breaker
        Tripped on ``halt``, under ``RECONCILE_TRIP_REASON``.
    verifier : SubmissionVerifier
        Disabled on ``refuse``; re-enabled on ``none``.
    actor : str
        Who the trip is recorded as — the serve loop or the control verb.
    control_request_id : str or None
        The operator command this answers, when it is one.
    reason : str or None
        Why sends stopped, for the operator reading the log; defaults to a
        line naming the action.

    Returns
    -------
    str
        The ``action`` it enacted, so a caller may record it.

    Raises
    ------
    ProductionError
        On an action outside ``vocab.RECON_ACTIONS``.
    """
    if action not in _ENACT:
        raise ProductionError(
            [f"enact takes a {list(RECON_ACTIONS)} action, got {action!r}"]
        )
    _ENACT[action](
        breaker,
        verifier,
        actor,
        control_request_id,
        reason or f"reconciliation answered {action!r}",
    )
    return action

#: The five compared domains, in the order a run walks them.
RECON_DOMAINS = ("balances", "fills", "orders", "positions", "settlements")

#: What one order contributes to a side, in compared order. ``updated_ms``
#: is the one field never classified: it drives the timing rule instead.
ORDER_FIELDS = (
    "instrument",
    "side",
    "qty",
    "limit",
    "tif",
    "status",
    "filled_qty",
    "remaining_qty",
    "avg_price",
    "fee",
    "updated_ms",
)
_ORDER_TIMESTAMP = "updated_ms"

#: Which break class a differing order field is — total over the compared
#: fields but the timestamp, so a new field cannot arrive without a class.
ORDER_BREAK_CLASS = {
    "instrument": "state",
    "side": "state",
    "qty": "quantity",
    "limit": "price",
    "tif": "state",
    "status": "state",
    "filled_qty": "quantity",
    "remaining_qty": "quantity",
    "avg_price": "price",
    "fee": "fee",
}

#: What one fill contributes to a side, keyed by ``fill_id``.
FILL_FIELDS = (
    "client_ref",
    "instrument",
    "side",
    "qty",
    "price",
    "fee",
    "fee_currency",
    "liquidity",
    "status",
    "ts_ms",
)

#: Which break class a differing fill field is — total over ``FILL_FIELDS``.
FILL_BREAK_CLASS = {
    "client_ref": "state",
    "instrument": "state",
    "side": "state",
    "qty": "quantity",
    "price": "price",
    "fee": "fee",
    "fee_currency": "fee",
    "liquidity": "fee",
    "status": "state",
    "ts_ms": "timing",
}

#: What one settlement contributes to a side, keyed ``"<instrument>:<settled_ms>"``.
SETTLEMENT_FIELDS = ("instrument", "outcome", "qty", "payout", "fee", "settled_ms")

#: Each break class's one severity. A ``timing`` race never blocks; an
#: unexplained balance, size or existence difference is exactly what
#: ``on_mismatch`` exists for; a price, fee or venue-only settlement
#: difference is worth an alert, not a halt.
BREAK_SEVERITY_BY_CLASS = {
    "timing": "info",
    "missing_in_ledger": "block",
    "missing_at_venue": "block",
    "quantity": "block",
    "price": "warn",
    "fee": "warn",
    "state": "block",
    "settlement": "warn",
    "cash": "block",
}

#: Each break class's one origin: ``external`` when the subject — or the
#: money — is the venue's alone, never silently made ours (§5.9).
BREAK_ORIGIN_BY_CLASS = {
    "timing": "ours",
    "missing_in_ledger": "external",
    "missing_at_venue": "ours",
    "quantity": "ours",
    "price": "ours",
    "fee": "ours",
    "state": "ours",
    "settlement": "external",
    "cash": "external",
}

#: The first term of each id derivation (the ``ids.py`` tagged-tuple idiom,
#: so two recipes cannot collide). ``cash-flow-v1`` is §6's.
BREAK_ID_TAG = "break-v1"
CASH_FLOW_ID_TAG = "cash-flow-v1"
RECON_ID_TAG = "recon-v1"

#: A bound on ``executor.fills`` paging, so a cursor that never runs out
#: refuses instead of hanging the run.
MAX_FILL_PAGES = 10_000

_ZERO = Decimal(0)

#: The vocabulary members this module spells, each pinned to its tuple at
#: import so a renamed member cannot leave a stale literal behind.
_RECON, _CASH_FLOW, _ADOPTION, _FILL, _OUTCOME = "recon", "cash_flow", "adoption", "fill", "outcome"
_TIMING, _MISSING_IN_LEDGER, _MISSING_AT_VENUE = "timing", "missing_in_ledger", "missing_at_venue"
_QUANTITY, _SETTLEMENT, _CASH = "quantity", "settlement", "cash"
_PENDING, _NOT_SENT, _MARKED = "pending", "not_sent", "marked"
_NO_ACTION = "none"
#: The clean status: the weakest severity, which is the first of the ladder.
_CLEAN = BREAK_SEVERITIES[0]
#: The ``cash_flow.source`` §6 fixes: the amount is the reconciler's delta.
_VENUE_SOURCE = "venue"
_CREDENTIAL_NAMES = ("control_request_id", "principal_digest", "proof_digest")
_BALANCES, _ORDERS, _FILLS, _POSITIONS, _SETTLEMENTS = RECON_DOMAINS[0], RECON_DOMAINS[2], RECON_DOMAINS[1], RECON_DOMAINS[3], RECON_DOMAINS[4]

for _member, _vocabulary in (
    (_RECON, RECORD_KINDS),
    (_CASH_FLOW, RECORD_KINDS),
    (_ADOPTION, RECORD_KINDS),
    (_FILL, RECORD_KINDS),
    (_OUTCOME, RECORD_KINDS),
    (_TIMING, BREAK_CLASSES),
    (_MISSING_IN_LEDGER, BREAK_CLASSES),
    (_MISSING_AT_VENUE, BREAK_CLASSES),
    (_QUANTITY, BREAK_CLASSES),
    (_SETTLEMENT, BREAK_CLASSES),
    (_CASH, BREAK_CLASSES),
    (_PENDING, STATUSES),
    (_NOT_SENT, STATUSES),
    (_MARKED, OUTCOME_KINDS),
    (_NO_ACTION, RECON_ACTIONS),
):
    if _member not in _vocabulary:
        raise ProductionError([f"reconcile.py: {_member!r} is not a vocabulary member"])

#: Whether a report status makes the document's automatic policy apply.
_POLICY_APPLIES = {"info": False, "warn": False, "block": True}
#: Whether an executor's ``positions`` capability makes that comparison meaningful (§5.9).
_POSITIONS_COMPARED = {"derived": False, "venue": True}
#: A fill's cash effect by status: applied fills count, a reversal undoes.
_FILL_SIGNS = {"pending": 1, "final": 1, "reversed": -1}
#: A fill's cash direction by side: a buy debits the notional, a sell credits it.
_CASH_DIRECTION = {"buy": -1, "sell": 1, "none": 0}

for _what, _table, _vocabulary in (
    ("BREAK_SEVERITY_BY_CLASS", BREAK_SEVERITY_BY_CLASS, BREAK_CLASSES),
    ("BREAK_ORIGIN_BY_CLASS", BREAK_ORIGIN_BY_CLASS, BREAK_CLASSES),
    ("ORDER_BREAK_CLASS", ORDER_BREAK_CLASS, set(ORDER_FIELDS) - {_ORDER_TIMESTAMP}),
    ("FILL_BREAK_CLASS", FILL_BREAK_CLASS, FILL_FIELDS),
    ("_POLICY_APPLIES", _POLICY_APPLIES, BREAK_SEVERITIES),
    ("_POSITIONS_COMPARED", _POSITIONS_COMPARED, POSITION_SOURCES),
    ("_FILL_SIGNS", _FILL_SIGNS, FILL_STATUSES),
    ("_CASH_DIRECTION", _CASH_DIRECTION, SIDES),
):
    if set(_table) != set(_vocabulary):
        raise ProductionError([f"reconcile.py: {_what} keys must cover exactly {sorted(_vocabulary)}"])
for _what, _values, _vocabulary in (
    ("BREAK_SEVERITY_BY_CLASS", BREAK_SEVERITY_BY_CLASS.values(), BREAK_SEVERITIES),
    ("BREAK_ORIGIN_BY_CLASS", BREAK_ORIGIN_BY_CLASS.values(), BREAK_ORIGINS),
    ("ORDER_BREAK_CLASS", ORDER_BREAK_CLASS.values(), BREAK_CLASSES),
    ("FILL_BREAK_CLASS", FILL_BREAK_CLASS.values(), BREAK_CLASSES),
    ("ON_MISMATCH", ON_MISMATCH, RECON_ACTIONS),
):
    if not set(_values) <= set(_vocabulary):
        raise ProductionError([f"reconcile.py: {_what} values must stay within {list(_vocabulary)}"])


# ---------------------------------------------------------------------------
# The two value objects: a break, and a run's report (the §6 recon body)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Break:
    """One discrepancy between our side and the venue's (§5.9).

    Parameters
    ----------
    break_id : str
        ``canonical_hash((BREAK_ID_TAG, break_class, subject))`` — stable
        while only the amounts move, so an operator's ``adopt`` names a
        break that still exists.
    break_class : str
        One of ``vocab.BREAK_CLASSES``.
    severity : str
        One of ``vocab.BREAK_SEVERITIES`` — :data:`BREAK_SEVERITY_BY_CLASS`.
    origin : str
        One of ``vocab.BREAK_ORIGINS`` — :data:`BREAK_ORIGIN_BY_CLASS`.
    subject : str
        ``"<domain>:<key>"``, e.g. ``"orders:ref-1"`` or ``"balances:USD"``.
    ours, theirs : dict or str or None
        Each side's projection of the subject; None where a side lacks it.
    delta : str or None
        ``theirs - ours`` as a decimal string when the subject is money,
        else None. Never a float (CLAUDE.md).
    detail : str
        A short human explanation.

    Examples
    --------
    A balance the fills do not explain, as :func:`classify_breaks` builds it::

        brk = Break(
            break_id=canonical_hash((BREAK_ID_TAG, "cash", "balances:USD")),
            break_class="cash", severity="block", origin="external",
            subject="balances:USD", ours="5000", theirs="5250", delta="250",
            detail="balances differ by 250",
        )
        brk.to_obj()["delta"]  # '250'
    """

    break_id: str
    break_class: str
    severity: str
    origin: str
    subject: str
    ours: object
    theirs: object
    delta: str | None
    detail: str

    def __post_init__(self):
        """Refuse a member outside its vocabulary, a float delta or a blank string."""
        problems = []
        for name in ("break_id", "break_class", "severity", "origin", "subject", "detail"):
            _check_str(problems, f"Break.{name}", getattr(self, name))
        for name, vocabulary in (
            ("break_class", BREAK_CLASSES),
            ("severity", BREAK_SEVERITIES),
            ("origin", BREAK_ORIGINS),
        ):
            value = getattr(self, name)
            if isinstance(value, str) and value not in vocabulary:
                problems.append(f"Break.{name}: {value!r} is not one of {list(vocabulary)}")
        if self.delta is not None and not isinstance(self.delta, str):
            problems.append(
                f"Break.delta must be a decimal string or None, got {self.delta!r} — "
                "money never touches float"
            )
        if problems:
            raise ProductionError(problems)

    def to_obj(self):
        """Return the break as a JSON-ready dict of its nine fields, in declared order.

        Returns
        -------
        dict
            Canonically hashable; the sides are copied, not shared.
        """
        return asdict(self)


@dataclass(frozen=True)
class ReconReport:
    """What one reconciliation found — §6's ``recon`` body, as a value.

    Parameters
    ----------
    scope : ExecutionScope
        The one scope the run reconciled.
    ours_digest, theirs_digest : str
        ``canonical_hash`` of each side exactly as :meth:`Reconciler.sides`
        built it; equal when the two sides are.
    breaks : tuple of Break
        Sorted by ``(break_class, subject)`` — a stable order, so two
        identical runs are one record shape.
    status : str
        The worst severity found, ``info`` when clean — a
        ``vocab.BREAK_SEVERITIES`` member.
    action : str
        A ``vocab.RECON_ACTIONS`` member: what the automatic policy asks
        the loop to do about it.

    Examples
    --------
    A clean run::

        report = ReconReport(
            scope=ExecutionScope(venue="paper", account="strategy-a"),
            ours_digest="a" * 64, theirs_digest="a" * 64,
            breaks=(), status="info", action="none",
        )
        report.to_obj()["breaks"]  # []
    """

    scope: ExecutionScope
    ours_digest: str
    theirs_digest: str
    breaks: tuple
    status: str
    action: str

    def __post_init__(self):
        """Check every member's type and vocabulary; freeze the breaks."""
        problems = []
        if not isinstance(self.scope, ExecutionScope):
            problems.append(f"ReconReport.scope must be an ExecutionScope, got {self.scope!r}")
        for name in ("ours_digest", "theirs_digest"):
            _check_str(problems, f"ReconReport.{name}", getattr(self, name))
        if isinstance(self.breaks, str) or not isinstance(self.breaks, (list, tuple)):
            problems.append(f"ReconReport.breaks must be a sequence of Break, got {self.breaks!r}")
        else:
            problems.extend(
                f"ReconReport.breaks[{position}] is {brk!r}, not a Break"
                for position, brk in enumerate(self.breaks)
                if not isinstance(brk, Break)
            )
        if self.status not in BREAK_SEVERITIES:
            problems.append(
                f"ReconReport.status must be one of {list(BREAK_SEVERITIES)}, got {self.status!r}"
            )
        if self.action not in RECON_ACTIONS:
            problems.append(
                f"ReconReport.action must be one of {list(RECON_ACTIONS)}, got {self.action!r}"
            )
        if problems:
            raise ProductionError(problems)
        object.__setattr__(self, "breaks", tuple(self.breaks))

    def to_obj(self):
        """Return the §6 ``recon`` body: the six members, in order, JSON-ready.

        Returns
        -------
        dict
            ``{scope, ours_digest, theirs_digest, breaks, status, action}``
            with the scope and every break serialized.
        """
        return {
            "scope": self.scope.to_obj(),
            "ours_digest": self.ours_digest,
            "theirs_digest": self.theirs_digest,
            "breaks": [brk.to_obj() for brk in self.breaks],
            "status": self.status,
            "action": self.action,
        }


# ---------------------------------------------------------------------------
# Classification — one strategy object per domain, tables for the rest
# ---------------------------------------------------------------------------


def _as_decimal(value):
    """Return ``value`` as a finite Decimal when it is a decimal string or an int, else None."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return None
    return amount if amount.is_finite() else None


def _same(field, ours, theirs):
    """Say whether one compared field agrees: money by value, everything else exactly."""
    if field in MONEY_FIELDS:
        a, b = _as_decimal(ours), _as_decimal(theirs)
        if a is not None and b is not None:
            return a == b
    return ours == theirs


def _delta(field, ours, theirs):
    """Return ``theirs - ours`` as a string for a money field both sides carry, else None."""
    if field not in MONEY_FIELDS:
        return None
    a, b = _as_decimal(ours), _as_decimal(theirs)
    if a is None or b is None:
        return None
    return str(b - a)


def _rank(break_class):
    """Return where a class's severity sits on the escalation ladder."""
    return BREAK_SEVERITIES.index(BREAK_SEVERITY_BY_CLASS[break_class])


def _break(break_class, subject, ours, theirs, delta, detail):
    """Build a break with its id, severity and origin from the tables."""
    return Break(
        break_id=canonical_hash((BREAK_ID_TAG, break_class, subject)),
        break_class=break_class,
        severity=BREAK_SEVERITY_BY_CLASS[break_class],
        origin=BREAK_ORIGIN_BY_CLASS[break_class],
        subject=subject,
        ours=ours,
        theirs=theirs,
        delta=delta,
        detail=detail,
    )


class _Domain(ABC):
    """One ``RECON_DOMAINS`` member's comparison: a check and a compare hook."""

    def __init__(self, name):
        self.name = name

    def breaks(self, ours, theirs):
        """Compare the two sides key by key — at most one break per subject."""
        found = []
        for key in sorted(set(ours) | set(theirs)):
            brk = self.compare(f"{self.name}:{key}", ours.get(key), theirs.get(key))
            if brk is not None:
                found.append(brk)
        return found

    @abstractmethod
    def check(self, problems, where, side):
        """Append a problem for every value of ``side`` this domain cannot compare."""

    @abstractmethod
    def compare(self, subject, ours, theirs):
        """Return the one break for ``subject``, or None when the sides agree."""


class _ScalarDomain(_Domain):
    """A decimal per key (balances, positions): an absent key is zero; one class."""

    def __init__(self, name, break_class):
        super().__init__(name)
        self.break_class = break_class

    def check(self, problems, where, side):
        """Require every value to be a decimal amount."""
        for key, value in side.items():
            if _as_decimal(value) is None:
                problems.append(f"{where}.{self.name}[{key!r}]: {value!r} is not a decimal amount")

    def compare(self, subject, ours, theirs):
        """Return the size difference, signed ``theirs - ours``, or None when the sides agree."""
        a = _ZERO if ours is None else _as_decimal(ours)
        b = _ZERO if theirs is None else _as_decimal(theirs)
        if a == b:
            return None
        return _break(
            self.break_class, subject, ours, theirs, str(b - a), f"{self.name} differ by {b - a}"
        )


class _RecordDomain(_Domain):
    """A projection per key (orders, fills, settlements): existence first, then the fields."""

    def __init__(self, name, fields, classes, absent_at_venue, absent_in_ledger, timestamp=None):
        super().__init__(name)
        self.fields = fields
        self.classes = classes
        self.absent_at_venue = absent_at_venue
        self.absent_in_ledger = absent_in_ledger
        self.timestamp = timestamp

    def check(self, problems, where, side):
        """Require every value to be a projection (a dict)."""
        for key, value in side.items():
            _check_dict(problems, f"{where}.{self.name}[{key!r}]", value)

    def compare(self, subject, ours, theirs):
        """Existence, then the timing rule, then the worst differing field."""
        if theirs is None:
            return _break(self.absent_at_venue, subject, ours, None, None, "the venue does not hold it")
        if ours is None:
            return _break(self.absent_in_ledger, subject, None, theirs, None, "the ledger does not hold it")
        differing = [
            field
            for field in self.fields
            if field != self.timestamp and not _same(field, ours.get(field), theirs.get(field))
        ]
        if not differing:
            return None
        if self._venue_moved_later(ours, theirs):
            return _break(
                _TIMING, subject, ours, theirs, None,
                f"the venue moved at {theirs[self.timestamp]} after our last update at "
                f"{ours[self.timestamp]}; {', '.join(differing)} differ",
            )
        field = max(differing, key=lambda name: (_rank(self.classes[name]), -self.fields.index(name)))
        return _break(
            self.classes[field], subject, ours, theirs,
            _delta(field, ours.get(field), theirs.get(field)), f"{field} differs",
        )

    def _venue_moved_later(self, ours, theirs):
        """Say whether the venue's answer is strictly newer than our last fold of the subject."""
        if self.timestamp is None:
            return False
        a, b = ours.get(self.timestamp), theirs.get(self.timestamp)
        return isinstance(a, int) and isinstance(b, int) and b > a


#: The strategy per domain, in ``RECON_DOMAINS`` order.
_DOMAINS = {
    _BALANCES: _ScalarDomain(_BALANCES, _CASH),
    _FILLS: _RecordDomain(_FILLS, FILL_FIELDS, FILL_BREAK_CLASS, _MISSING_AT_VENUE, _MISSING_IN_LEDGER),
    _ORDERS: _RecordDomain(
        _ORDERS, ORDER_FIELDS, ORDER_BREAK_CLASS, _MISSING_AT_VENUE, _MISSING_IN_LEDGER,
        timestamp=_ORDER_TIMESTAMP,
    ),
    _POSITIONS: _ScalarDomain(_POSITIONS, _QUANTITY),
    _SETTLEMENTS: _RecordDomain(
        _SETTLEMENTS, SETTLEMENT_FIELDS, {field: _SETTLEMENT for field in SETTLEMENT_FIELDS},
        _MISSING_AT_VENUE, _SETTLEMENT,
    ),
}
if tuple(_DOMAINS) != RECON_DOMAINS:
    raise ProductionError(["reconcile.py: _DOMAINS must cover RECON_DOMAINS in order"])


def _check_side(problems, where, side):
    """Append a problem for every way ``side`` departs from the declared shape."""
    before = len(problems)
    _check_dict(problems, where, side)
    if len(problems) > before:
        return
    _check_unknown(problems, side, RECON_DOMAINS, where=where)
    missing = [name for name in RECON_DOMAINS if name not in side]
    if missing:
        problems.append(f"{where}: missing domain(s) {missing}")
    for name, domain in _DOMAINS.items():
        if name not in side:
            continue
        inner = len(problems)
        _check_dict(problems, f"{where}.{name}", side[name])
        if len(problems) == inner:
            domain.check(problems, where, side[name])


def classify_breaks(ours, theirs):
    """Classify every discrepancy between two sides into breaks (§5.9).

    Pure: no clock, no ledger, no executor. Each side is
    ``{domain: {key: value}}`` over exactly :data:`RECON_DOMAINS` — a
    decimal string per key for ``balances`` and ``positions`` (absent is
    zero), a projection dict per key for ``orders`` (:data:`ORDER_FIELDS`),
    ``fills`` (:data:`FILL_FIELDS`) and ``settlements``
    (:data:`SETTLEMENT_FIELDS`). One subject yields at most one break.

    Parameters
    ----------
    ours : dict
        What the ledger believes, as :meth:`Reconciler.sides` builds it.
    theirs : dict
        What the venue answered, in the same shape.

    Returns
    -------
    tuple of Break
        Sorted by ``(break_class, subject)``; empty when the sides agree.

    Raises
    ------
    ProductionError
        If either side is not the declared shape — a missing or unknown
        domain, a non-dict projection, a non-decimal amount.
    """
    problems = []
    _check_side(problems, "ours", ours)
    _check_side(problems, "theirs", theirs)
    if problems:
        raise ProductionError(problems)
    found = []
    for name, domain in _DOMAINS.items():
        found.extend(domain.breaks(ours[name], theirs[name]))
    return tuple(sorted(found, key=lambda brk: (brk.break_class, brk.subject)))


# ---------------------------------------------------------------------------
# The ledger's history — the one reader of fills, cash flows and marks
# ---------------------------------------------------------------------------


def _with_id(envelope):
    """Return a record's body plus its envelope ``id`` (``supersedes`` names a record)."""
    return dict(envelope["body"], id=envelope["id"])


class LedgerHistory:
    """The fill, cash-flow and mark history a ledger holds, by instant (§5.7.1, §5.9).

    ``StateView`` carries positions, not the fills that made them, and
    only the fold's named readers may scan the ledger — so this is the one
    reader ``accounting.py`` and the reconciler share. Every bound is
    INCLUSIVE on the record's own instant: a fill's ``ts_ms``, a flow's or
    a mark's ``effective_at_ms`` (D21 — what a flow explains is when it
    happened, not when it was found).

    Parameters
    ----------
    ledger : Ledger
        Read through ``scan(kind=...)``; never written.

    Examples
    --------
    Every fill since an instant, as value objects::

        history = LedgerHistory(ledger)
        fills = history.fills(1_767_268_800_000)
        fills[0].qty  # Decimal('10')
        history.cash_flows(0)[0]["amount"]  # '5000'
    """

    def __init__(self, ledger):
        self._ledger = ledger

    def fills(self, since_ms):
        """Return every ``fill`` record with ``ts_ms >= since_ms``, in ledger order.

        Parameters
        ----------
        since_ms : int
            Epoch-ms lower bound, inclusive; ``0`` for all time.

        Returns
        -------
        tuple of Fill
            Each body rebuilt as the ``Fill`` value object (§6: the record
            IS the ``Fill``, so a body with any other shape refuses).

        Raises
        ------
        ProductionError
            If ``since_ms`` is not a non-negative int, or a body is not a
            ``Fill``.
        """
        _check_since(since_ms)
        return tuple(
            Fill.from_obj(envelope["body"])
            for envelope in self._ledger.scan(kind=_FILL)
            if envelope["body"]["ts_ms"] >= since_ms
        )

    def cash_flows(self, since_ms):
        """Return every ``cash_flow`` body with ``effective_at_ms >= since_ms``.

        Parameters
        ----------
        since_ms : int
            Epoch-ms lower bound, inclusive.

        Returns
        -------
        tuple of dict
            §6 bodies, each carrying its envelope ``id`` under ``"id"``.

        Raises
        ------
        ProductionError
            If ``since_ms`` is not a non-negative int.
        """
        _check_since(since_ms)
        return tuple(
            _with_id(envelope)
            for envelope in self._ledger.scan(kind=_CASH_FLOW)
            if envelope["body"]["effective_at_ms"] >= since_ms
        )

    def marks(self, since_ms):
        """Return every ``marked`` outcome body with ``effective_at_ms >= since_ms``.

        Parameters
        ----------
        since_ms : int
            Epoch-ms lower bound, inclusive.

        Returns
        -------
        tuple of dict
            §6 ``outcome`` bodies whose ``outcome_kind`` is ``marked``, each
            carrying its envelope ``id`` under ``"id"``; settled, voided,
            partial and corrected outcomes are not marks.

        Raises
        ------
        ProductionError
            If ``since_ms`` is not a non-negative int.
        """
        _check_since(since_ms)
        return tuple(
            _with_id(envelope)
            for envelope in self._ledger.scan(kind=_OUTCOME)
            if envelope["body"]["outcome_kind"] == _MARKED
            and envelope["body"]["effective_at_ms"] >= since_ms
        )


def _check_since(since_ms):
    """Refuse a lower bound that is not a non-negative epoch-ms int."""
    problems = []
    check_int_param(problems, "since_ms", since_ms, ge=0)
    if problems:
        raise ProductionError(problems)


# ---------------------------------------------------------------------------
# The reconciler
# ---------------------------------------------------------------------------


def _project(record, names):
    """Return a record's JSON form restricted to ``names``."""
    obj = record.to_obj()
    return {name: obj[name] for name in names}


def _typed(items, cls, what):
    """Return ``items`` as a tuple, refusing any element that is not a ``cls``."""
    items = tuple(items)
    problems = [
        f"executor.{what}[{position}] is {item!r}, not a {cls.__name__}"
        for position, item in enumerate(items)
        if not isinstance(item, cls)
    ]
    if problems:
        raise ProductionError(problems)
    return items


#: What an absent capability member reads as — never a legal value.
_NO_CAPABILITY = object()


def _capability(caps, *path):
    """Read a nested capability, refusing an executor that does not declare it (§5.7).

    ``capabilities()`` answers the frozen ``Capabilities`` value of §5.7,
    whose members are attributes, while a nested member (``units.cash``)
    may be a plain mapping. Both shapes read the same way here, so the
    reconciler never learns which executor it is talking to.
    """
    value = caps
    for key in path:
        if isinstance(value, Mapping):
            if key not in value:
                raise ProductionError(
                    [f"executor capabilities lack {'.'.join(path)!r} (§5.7)"]
                )
            value = value[key]
            continue
        value = getattr(value, key, _NO_CAPABILITY)
        if value is _NO_CAPABILITY:
            raise ProductionError(
                [f"executor capabilities lack {'.'.join(path)!r} (§5.7)"]
            )
    return value


def _worst(breaks):
    """Return the worst severity among ``breaks`` — the clean status when there are none."""
    return max((brk.severity for brk in breaks), key=BREAK_SEVERITIES.index, default=_CLEAN)


@dataclass(frozen=True)
class _Run:
    """What the last run left for ``adopt``: its report, its record id and its instant."""

    report: ReconReport
    record_id: str
    at_ms: int


#: How a pending ref's venue answer is read, by its status: the venue
#: never received it, or it holds it and the answer resolves our ref.
_PENDING_RESOLUTIONS = {status: "_resolved_pending" for status in STATUSES}
_PENDING_RESOLUTIONS[_NOT_SENT] = "_unsent_pending"


class Reconciler:
    """The composite that holds both sides, and the one authenticated adoption (§5.9, D13).

    A run builds ours from the caller's ``StateView`` and the ledger's
    fill history, theirs from the executor's read/query surface, classifies
    the breaks, appends one ``recon`` record, barriers it and answers the
    report. It never submits, cancels or folds. ``adopt`` is the only
    writer of ``cash_flow`` and ``adoption`` records and resolves ``cash``
    breaks alone; ``due`` is the one owner of the document's cadence.

    Parameters
    ----------
    document : ServeDocument
        Read for ``reconcile.on_start``, ``every_s``, ``on_mismatch`` and
        ``lookback_ms``.
    release : ReleaseManifest
        Binds ``release_hash`` (what ``adopt`` must name) and
        ``execution_scope`` (the one scope a run may reconcile).
    ledger : Ledger
        Where ``recon``, ``cash_flow`` and ``adoption`` records are
        appended and barriered; what :class:`LedgerHistory` reads.
    state : SeriesState
        The fold. ``run`` refuses a view whose economic state is not the
        fold's current one, so a report never describes a belief the
        ledger has already moved past.
    clock : Clock
        ``now_ms()`` anchors ``lookback_ms`` and stamps every instant a
        record carries.

    Examples
    --------
    Reconcile once and ask what the loop should do about it::

        reconciler = Reconciler(document, release, ledger=ledger, state=state, clock=clock)
        report = reconciler.run(state.snapshot(), executor, release.execution_scope)
        reconciler.apply_policy(report)  # 'none' when clean, else document.reconcile.on_mismatch
        reconciler.due(clock.now_ms() + 300_000, last_run_ms=clock.now_ms())  # True at every_s
    """

    def __init__(self, document, release, *, ledger, state, clock):
        block = document.reconcile
        self._on_start = bool(block.on_start)
        self._every_ms = int(block.every_s) * 1000
        self._on_mismatch = block.on_mismatch
        self._lookback_ms = int(block.lookback_ms)
        self._release_hash = release.release_hash
        self._scope = release.execution_scope
        self._ledger = ledger
        self._state = state
        self._clock = clock
        self._history = LedgerHistory(ledger)
        self._last = None

    # -- cadence and policy: one owner each ---------------------------------------

    def due(self, now_ms, last_run_ms=None):
        """Say whether a reconciliation is due — the one owner of ``on_start`` / ``every_s``.

        Parameters
        ----------
        now_ms : int
            The loop's instant.
        last_run_ms : int or None
            When the last run happened; None before any run in this process.

        Returns
        -------
        bool
            ``document.reconcile.on_start`` before the first run; afterwards
            true once ``every_s`` has elapsed since ``last_run_ms``.
        """
        if last_run_ms is None:
            return self._on_start
        return now_ms - last_run_ms >= self._every_ms

    def apply_policy(self, report):
        """Name what the loop should do about a report — never do it (D13).

        Parameters
        ----------
        report : ReconReport

        Returns
        -------
        str
            A ``vocab.RECON_ACTIONS`` member: ``none`` unless the report
            blocks, then ``document.reconcile.on_mismatch``. Tripping the
            breaker on ``halt`` is the loop's call, after the record is
            durable.

        Raises
        ------
        ProductionError
            If ``report`` is not a ``ReconReport``.
        """
        if not isinstance(report, ReconReport):
            raise ProductionError([f"apply_policy expects a ReconReport, got {report!r}"])
        return self._action_for(report.status)

    def _action_for(self, status):
        """Apply the one rule behind the recorded ``action`` and ``apply_policy``."""
        return self._on_mismatch if _POLICY_APPLIES[status] else _NO_ACTION

    @property
    def last_report(self):
        """The report of the last run in this process, or None — what ``adopt`` resolves against."""
        return None if self._last is None else self._last.report

    # -- the two sides -----------------------------------------------------------

    def sides(self, view, executor, scope):
        """Build ours and theirs without recording anything.

        Parameters
        ----------
        view : StateView
            The fold's projection: working orders, pending refs, balances,
            derived positions.
        executor : Executor
            Read and query only: ``execution_scope``, ``capabilities``,
            ``order``, ``open_orders``, ``balances``, ``fills``,
            ``positions``, ``settlements``.
        scope : ExecutionScope
            Must equal both the release's scope and the executor's.

        Returns
        -------
        tuple of dict
            ``(ours, theirs)``, each ``{domain: {key: value}}`` over
            :data:`RECON_DOMAINS`, in the shape :func:`classify_breaks`
            takes.

        Raises
        ------
        ProductionError
            On a scope disagreement, an executor answer of the wrong type,
            or a capability the executor does not declare.
        """
        return self._sides(view, executor, scope, self._clock.now_ms())

    def _sides(self, view, executor, scope, at_ms):
        """Build both sides as of ``at_ms``; the lookback is anchored there."""
        self._check_scope(executor, scope)
        caps = executor.capabilities()
        since = at_ms - self._lookback_ms
        ours = {name: {} for name in RECON_DOMAINS}
        theirs = {name: {} for name in RECON_DOMAINS}
        fills = self._history.fills(0)
        self._orders(view, executor, ours, theirs)
        self._balances(view, caps, executor, fills, ours, theirs)
        ours[_FILLS] = {
            fill.fill_id: _project(fill, FILL_FIELDS) for fill in fills if fill.ts_ms >= since
        }
        theirs[_FILLS] = self._venue_fills(executor, since)
        self._positions(view, caps, executor, ours, theirs)
        theirs[_SETTLEMENTS] = {
            f"{item.instrument}:{item.settled_ms}": _project(item, SETTLEMENT_FIELDS)
            for item in _typed(executor.settlements(since), Settlement, "settlements")
        }
        return ours, theirs

    def _check_scope(self, executor, scope):
        """Refuse a scope that is not the release's, or an executor bound to another (§5.7.2)."""
        problems = []
        if not isinstance(scope, ExecutionScope):
            problems.append(f"scope must be an ExecutionScope, got {scope!r}")
        else:
            if scope != self._scope:
                problems.append(
                    f"scope {scope.to_obj()} is not the release's {self._scope.to_obj()}"
                )
            actual = executor.execution_scope()
            if actual != scope:
                problems.append(
                    f"the executor's scope {getattr(actual, 'to_obj', repr)()} is not the one "
                    f"asked for, {scope.to_obj()}: comparing another account is not reconciliation"
                )
        if problems:
            raise ProductionError(problems)

    def _orders(self, view, executor, ours, theirs):
        """Working orders against the venue's open ones; pending refs resolved through ``order``."""
        for ref, order in view.working.items():
            ours[_ORDERS][ref] = _project(order, ORDER_FIELDS)
        for order in _typed(executor.open_orders(), OrderState, "open_orders"):
            theirs[_ORDERS][order.client_ref] = _project(order, ORDER_FIELDS)
        for ref in view.pending:
            answer = _typed((executor.order(ref),), OrderState, f"order({ref!r})")[0]
            getattr(self, _PENDING_RESOLUTIONS[answer.status])(ref, answer, ours, theirs)

    def _unsent_pending(self, ref, answer, ours, theirs):
        """Place a pending ref the venue never received: ours holds the intent, theirs nothing."""
        ours[_ORDERS][ref] = dict.fromkeys(ORDER_FIELDS) | {"status": _PENDING}

    def _resolved_pending(self, ref, answer, ours, theirs):
        """Resolve a pending ref the venue holds through the one sanctioned answer (D13)."""
        resolved = _project(answer, ORDER_FIELDS)
        ours[_ORDERS][ref] = resolved
        theirs[_ORDERS].setdefault(ref, resolved)

    def _balances(self, view, caps, executor, fills, ours, theirs):
        """Our EXPECTED balance — the fold plus every fill's cash effect — against the venue's."""
        cash = _capability(caps, "units", "cash")
        expected = dict(view.balances)
        for fill in fills:
            sign = _FILL_SIGNS[fill.status]
            notional = _CASH_DIRECTION[fill.side] * fill.qty * fill.price
            expected[cash] = expected.get(cash, _ZERO) + sign * notional
            expected[fill.fee_currency] = expected.get(fill.fee_currency, _ZERO) - sign * fill.fee
        ours[_BALANCES] = {currency: str(amount) for currency, amount in expected.items()}
        theirs[_BALANCES] = {
            item.currency: str(item.total)
            for item in _typed(executor.balances(), Balance, "balances")
        }

    def _venue_fills(self, executor, since):
        """Page ``executor.fills`` from the lookback boundary until its cursor runs out."""
        found = {}
        cursor = None
        for _page in range(MAX_FILL_PAGES):
            answer = executor.fills(since, cursor)
            if not isinstance(answer, tuple) or len(answer) != 2:
                raise ProductionError(
                    [f"executor.fills must answer (page, next_cursor), got {answer!r}"]
                )
            page, cursor = answer
            for fill in _typed(page, Fill, "fills"):
                found[fill.fill_id] = _project(fill, FILL_FIELDS)
            if cursor is None:
                return found
        raise ProductionError(
            [f"executor.fills paged {MAX_FILL_PAGES} times without exhausting its cursor"]
        )

    def _positions(self, view, caps, executor, ours, theirs):
        """Fill-derived against venue positions — only when the venue reports them (§5.9)."""
        source = _capability(caps, "positions")
        if source not in _POSITIONS_COMPARED:
            raise ProductionError(
                [f"executor capabilities.positions must be one of {list(POSITION_SOURCES)}, "
                 f"got {source!r}"]
            )
        if not _POSITIONS_COMPARED[source]:
            return
        ours[_POSITIONS] = {item.instrument: str(item.qty) for item in view.positions}
        theirs[_POSITIONS] = {
            item.instrument: str(item.qty)
            for item in _typed(executor.positions(), Position, "positions")
        }

    # -- the run ---------------------------------------------------------------

    def run(self, view, executor, scope):
        """Reconcile once: compare, classify, record one ``recon``, barrier, report (D13).

        Parameters
        ----------
        view : StateView
            A FRESH snapshot of the fold — its ``economic_seq`` must be the
            fold's current one.
        executor : Executor
            As for :meth:`sides`; never asked to submit or cancel.
        scope : ExecutionScope
            As for :meth:`sides`.

        Returns
        -------
        ReconReport
            Also reachable as :attr:`last_report` until the next run.

        Raises
        ------
        ProductionError
            On a stale view, a scope disagreement, or a malformed executor
            answer — nothing is appended.
        """
        self._require_current(view)
        at_ms = self._clock.now_ms()
        ours, theirs = self._sides(view, executor, scope, at_ms)
        breaks = classify_breaks(ours, theirs)
        status = _worst(breaks)
        report = ReconReport(
            scope=scope,
            ours_digest=canonical_hash(ours),
            theirs_digest=canonical_hash(theirs),
            breaks=breaks,
            status=status,
            action=self._action_for(status),
        )
        record_id = f"{_RECON}:" + canonical_hash(
            (RECON_ID_TAG, self._release_hash, scope.to_obj(), at_ms, self._ledger.head()[0] + 1)
        )
        self._ledger.append({"kind": _RECON, "id": record_id, "body": report.to_obj()})
        self._ledger.barrier()
        self._last = _Run(report, record_id, at_ms)
        _LOG.info(
            "reconciled %s: %d break(s), status %s, action %s",
            record_id, len(breaks), status, report.action,
        )
        return report

    def _require_current(self, view):
        """Refuse a view the fold has economically moved past: a stale belief is not ours."""
        fold = self._state.snapshot().risk_version.economic_seq
        if view.risk_version.economic_seq != fold:
            raise ProductionError(
                [f"reconcile: the view's economic_seq {view.risk_version.economic_seq} is not the "
                 f"fold's {fold}; reconcile against a fresh snapshot"]
            )

    # -- adoption: the one authenticated resolution of a cash break ------------------

    def adopt(
        self,
        view,
        break_ids,
        control_request_id,
        principal_digest,
        proof_digest,
        release_hash,
        flow_kind,
        external,
        known_at_ms=None,
    ):
        """Adopt ``cash`` breaks of the last run: bank each amount, then the receipt, one barrier.

        For every named break one ``cash_flow`` is appended carrying the
        amount and both instants as values — ``effective_at_ms`` is when
        the run that found it looked, ``known_at_ms`` is when the operator's
        command was durably queued (D21) — under
        the §6 id, so a replay appends nothing twice; then one ``adoption``
        receipt; then ``ledger.barrier()``. The fold moves through the
        ledger, so the next run no longer sees the break. Nothing is
        reconciled here: the follow-up run is the caller's.

        Parameters
        ----------
        view : StateView
            The fold as the caller has it.
        break_ids : tuple of str
            Break ids reported by the last run, each of class ``cash``.
        control_request_id, principal_digest, proof_digest : str
            The operator's request and its verified proof (D13: adoption
            is authenticated and ledgered, never a flag).
        release_hash : str
            Must be this release's.
        flow_kind : str
            A ``vocab.CASH_FLOW_KINDS`` member, from the operator's proof.
        external : bool
            From the operator's proof; never defaulted (§6).

        Returns
        -------
        tuple of str
            The ids of the records appended (or re-appended), cash flows
            first, the adoption last.

        Raises
        ------
        ProductionError
            Every problem at once: no run yet, an unknown or non-cash
            break id, a missing credential, another release's hash, a flow
            kind outside the closed set, a non-boolean ``external`` —
            nothing is appended.
        """
        problems = []
        breaks = self._adoptable(problems, break_ids)
        for name, value in (
            ("control_request_id", control_request_id),
            ("principal_digest", principal_digest),
            ("proof_digest", proof_digest),
        ):
            _check_str(problems, f"adopt: {name}", value)
        if release_hash != self._release_hash:
            problems.append(
                f"adopt: release_hash {release_hash!r} is not this release's "
                f"{self._release_hash!r} (D24)"
            )
        if flow_kind not in CASH_FLOW_KINDS:
            problems.append(
                f"adopt: flow_kind must be one of {list(CASH_FLOW_KINDS)}, got {flow_kind!r}"
            )
        if not isinstance(external, bool):
            problems.append(f"adopt: external must be a bool, got {external!r}")
        if problems:
            raise ProductionError(problems)
        # The caller's queued instant, never the clock: a crash-replayed
        # adopt must rebuild the SAME payload, or the ledger refuses it as a
        # different one under the same id (§6, D21).
        if known_at_ms is None:
            known_at_ms = self._clock.now_ms()
        flows = [
            self._cash_flow(brk, control_request_id, known_at_ms, flow_kind, external)
            for brk in breaks
        ]
        receipt = self._adoption(
            flows, control_request_id, principal_digest, proof_digest, break_ids
        )
        self._ledger.append_many(flows + [receipt])
        self._ledger.barrier()
        _LOG.info("adopted %d cash break(s) under %s", len(flows), control_request_id)
        return tuple(record["id"] for record in flows + [receipt])

    def _adoptable(self, problems, break_ids):
        """Resolve ``break_ids`` against the last report; every id must name a cash break."""
        if self._last is None:
            problems.append("adopt: no reconciliation has run in this process; reconcile first")
            return ()
        if (
            isinstance(break_ids, str)
            or not isinstance(break_ids, (list, tuple))
            or not break_ids
        ):
            problems.append(f"adopt: break_ids must name at least one break, got {break_ids!r}")
            return ()
        known = {brk.break_id: brk for brk in self._last.report.breaks}
        found = []
        for break_id in break_ids:
            brk = known.get(break_id)
            if brk is None:
                problems.append(f"adopt: break {break_id!r} was not reported by the last run")
            elif brk.break_class != _CASH:
                problems.append(
                    f"adopt: break {break_id!r} is {brk.break_class}, not {_CASH} — the only "
                    "class with a resolution other than halt-or-refuse (§5.9)"
                )
            elif brk in found:
                problems.append(f"adopt: break {break_id!r} is named twice")
            else:
                found.append(brk)
        return tuple(found)

    def _cash_flow(self, brk, control_request_id, known_at_ms, flow_kind, external):
        """Build the §6 ``cash_flow`` record for one adopted break: the amount as a value."""
        currency = brk.subject[len(_BALANCES) + 1:]
        body = {
            "effective_at_ms": self._last.at_ms,
            "known_at_ms": known_at_ms,
            "supersedes": None,
            "currency": currency,
            "amount": brk.delta,
            "flow_kind": flow_kind,
            "external": external,
            "source": _VENUE_SOURCE,
            "evidence": {
                "recon_id": self._last.record_id,
                "break_id": brk.break_id,
                "delta": brk.delta,
            },
        }
        record_id = f"{_CASH_FLOW}:" + canonical_hash(
            (CASH_FLOW_ID_TAG, self._release_hash, control_request_id, brk.break_id)
        )
        return {"kind": _CASH_FLOW, "id": record_id, "body": body}

    def _adoption(self, flows, control_request_id, principal_digest, proof_digest, break_ids):
        """Build the §6 ``adoption`` receipt naming the breaks, the delta digest and the run before."""
        delta_digest = canonical_hash(
            [
                {
                    "break_id": record["body"]["evidence"]["break_id"],
                    "currency": record["body"]["currency"],
                    "amount": record["body"]["amount"],
                }
                for record in flows
            ]
        )
        body = {
            "control_request_id": control_request_id,
            "principal_digest": principal_digest,
            "proof_digest": proof_digest,
            "break_ids": list(break_ids),
            "delta_digest": delta_digest,
            "before_recon_id": self._last.record_id,
            "after_recon_id": None,
        }
        return {"kind": _ADOPTION, "id": f"{_ADOPTION}:{control_request_id}", "body": body}
