"""What the account may still commit: funds and inventory encumbrance (ADR-0162).

``Balance`` has carried ``total`` and ``available`` since §5.4, and until this
module every ``available`` the package produced was its ``total``. Nothing
derived it. ``Accounting.classify`` computes signed exposure to LABEL a
proposal's risk effect, ``guards.Exposure`` values working orders for a LIMIT
check, and ``cashflows.py`` schedules EXTERNAL deposits and withdrawals on a
calendar — three different questions, none of them "how much of this balance is
still free to commit".

An **encumbrance** is a claim held against the account's own resources by
something that is not yet finished: cash committed to an outstanding buy, units
committed to an outstanding sell, and the cash a fill has moved but the venue
has not yet made usable. The noun is deliberate. "Reserve" is already spoken
for four times in this tree — a single-use reduction right, an idempotent
ledger id claim, a rate-limiter's cancel lane, and ADR-0157's admission
ledger — and none of them is brokerage cash.

Three rules shape everything here.

**Uncertainty holds.** ``TERMINAL_STATUSES`` deliberately excludes ``unknown``:
it is the absence of certainty, not an end. So an order in ``pending``,
``pending_cancel`` or ``unknown`` keeps its commitment, and only a terminal
status releases it. A partial fill releases exactly the filled portion out of
``remaining_qty``, where it reappears as a settlement obligation through the
fill history rather than as free cash.

**Nothing is stored.** An encumbrance has no id and no lifecycle: it is a pure
function of ``(StateView, at_ms, history)``, both of which a restart rebuilds
from the durable snapshot and the chain. Re-deriving at the same instant over
the same chain reproduces the same book, which is the whole of "survives
restart" — there is no in-memory reservation a crash could lose.

**Venue facts are declared, never guessed.** The settlement period, whether the
folded balance already reflects a fill, the borrow stance: each is an owner or
venue choice. :class:`CashSettlement` requires the first two and refuses without
them, and :meth:`EncumbrancePolicy.borrow` is the hook a margin child overrides
— core answers it with a refusal. Nothing in this module asserts broker
conformance.

:class:`UndeclaredSettlement` is the null object and the compatibility
guarantee: it encumbers nothing, so ``available == total`` falls out of the
SAME arithmetic rather than out of a special case, and it never touches the
history. Its :meth:`admit` refuses, because an account with no declared
convention may report that legacy figure but must never authorise a commitment
against it.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from dskit.pipeline.node import check_int_param
from dskit.production.accounting import PaperAccounting, effective_fills
from dskit.production.base import ProductionError, pin_members, reject_unknown_params
from dskit.production.records import Balance, Proposal
from dskit.production.vocab import BALANCE_BASES, SIDES

__all__ = [
    "ENCUMBRANCE_POLICIES",
    "CashSettlement",
    "EncumberedAccounting",
    "Encumbrance",
    "EncumbrancePolicy",
    "FundsRow",
    "InventoryRow",
    "UndeclaredSettlement",
]

_NOTES = ("notes",)
_ZERO = Decimal(0)

#: The document keys an ``encumbrance`` selector may carry — the ``{uses,
#: params}`` shape §4.1 gives every other selector, plus documentation.
_SELECTOR_KEYS = ("uses", "params", "notes")


@dataclass(frozen=True)
class FundsRow:
    """One currency's cash, split into what is committed, unsettled and free.

    Parameters
    ----------
    currency : str
    total : Decimal
        The fold's balance, untouched.
    committed : Decimal
        Cash held by outstanding buy orders.
    unsettled : Decimal
        Cash a fill has moved that the venue has not yet made usable, under
        the declared balance basis.
    available : Decimal
        ``total - committed - unsettled``. Never re-derived by a caller:
        :meth:`EncumbrancePolicy.balances` is the one owner of the subtraction.

    Examples
    --------
    ::

        row = FundsRow(
            currency="USD", total=Decimal("1000"), committed=Decimal("40"),
            unsettled=Decimal("0"), available=Decimal("960"),
        )
    """

    currency: str
    total: Decimal
    committed: Decimal
    unsettled: Decimal
    available: Decimal


@dataclass(frozen=True)
class InventoryRow:
    """One instrument's held units, split into what is committed and what is free.

    Parameters
    ----------
    instrument : str
    held : Decimal
        The fold's position quantity, signed as the fold carries it.
    committed : Decimal
        Units promised to outstanding sell orders.
    available : Decimal
        ``held - committed`` — what a further sell may still commit.

    Examples
    --------
    ::

        row = InventoryRow(
            instrument="INS1", held=Decimal("10"), committed=Decimal("4"),
            available=Decimal("6"),
        )
    """

    instrument: str
    held: Decimal
    committed: Decimal
    available: Decimal


@dataclass(frozen=True)
class Encumbrance:
    """The derived book: funds by currency, inventory by instrument, and what is unsized.

    Parameters
    ----------
    funds : mapping of str to FundsRow
        One row per currency the fold carries, in currency order.
    inventory : mapping of str to InventoryRow
        One row per instrument held or committed.
    unsized_refs : tuple of str
        Client refs whose intent has landed but whose ``order_event`` has
        not, so the fold carries no quantity for them. Their commitment is
        unknown, which is why :meth:`EncumbrancePolicy.admit` refuses while
        any is outstanding rather than reporting a figure that is too high.

    Examples
    --------
    ::

        book = Encumbrance(
            funds=MappingProxyType({"USD": row}), inventory=MappingProxyType({}),
            unsized_refs=(),
        )
        book.funds["USD"].available  # Decimal('960')
    """

    funds: MappingProxyType
    inventory: MappingProxyType
    unsized_refs: tuple


class EncumbrancePolicy(ABC):
    """The seam: what an account has committed, and whether it may commit more.

    Constructed as ``cls(params)``: default-deny over the subclass's
    ``_PARAMS`` plus ``notes``, the shape every other configurable class
    in this package takes. Two hooks are abstract, because core cannot
    know either for a real venue — :meth:`encumber`, which derives the
    book, and :meth:`borrow`, which says whether units the account does
    not hold can be found. The two methods a caller actually uses,
    :meth:`balances` and :meth:`admit`, are concrete and base-owned, so
    ``available = total - committed - unsettled`` has ONE owner and
    "may this be committed" has ONE answer.

    This is a seam with a TABLE (:data:`ENCUMBRANCE_POLICIES`) rather
    than a §4.3 registry: no document key selects an encumbrance policy,
    so a registry would add a family nothing selects — the
    ``readiness.Evidence`` and ``report.ReportEmitter`` precedent.

    Parameters
    ----------
    params : dict, optional
        The selector's ``params``; ``None`` means ``{}``.

    Raises
    ------
    ProductionError
        On a key outside ``_PARAMS`` and ``notes``, or on a knob the
        subclass refuses.

    Examples
    --------
    The smallest complete policy: one that encumbers nothing and lends
    nothing::

        class Flat(EncumbrancePolicy):
            def encumber(self, state_view, at_ms, history):
                return Encumbrance(
                    funds=MappingProxyType({}), inventory=MappingProxyType({}),
                    unsized_refs=(),
                )

            def borrow(self, instrument, qty, state_view, at_ms):
                return (f"{instrument}: Flat lends nothing",)

        flat = Flat({})
        flat.balances(view, 0, history)  # ()
    """

    #: The knobs a subclass accepts; ``notes`` is always allowed beside them.
    _PARAMS = ()

    #: Proposal side -> the method that judges it. A table keyed by the
    #: vocabulary, pinned at import, never a side branch.
    _ADMIT = pin_members(
        "encumbrance.py's _ADMIT table",
        {"buy": "_admit_buy", "sell": "_admit_sell", "none": "_admit_nothing"},
        SIDES,
        exact=True,
    )

    def __init__(self, params=None):
        params = dict(params or {})
        problems = self.validate_params(params)
        if problems:
            raise ProductionError(problems)

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when it is acceptable.

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
        return problems

    @abstractmethod
    def encumber(self, state_view, at_ms, history):
        """Return the :class:`Encumbrance` the fold implies as of ``at_ms``.

        Parameters
        ----------
        state_view : StateView
            The fold: positions, working orders, pending refs and balances.
        at_ms : int
            The instant the book is derived at.
        history : object
            The injected ``fills(since_ms)`` collaborator. A policy that
            needs no fill history must not touch it.

        Returns
        -------
        Encumbrance
        """

    @abstractmethod
    def borrow(self, instrument, qty, state_view, at_ms):
        """Return every reason ``qty`` units of ``instrument`` cannot be borrowed.

        The locate hook. Core models no borrow at all and answers with a
        refusal naming the instrument; a margin policy overrides this and
        nothing else.

        Parameters
        ----------
        instrument : str
        qty : Decimal
            The shortfall — how far past the available inventory a sell
            would reach.
        state_view : StateView
        at_ms : int

        Returns
        -------
        tuple of str
            Empty when the borrow is available.
        """

    def balances(self, state_view, at_ms, history):
        """Return one :class:`Balance` per folded currency, ``available`` derived.

        The ONE owner of ``available = total - committed - unsettled``. A
        caller never restates that subtraction.

        Parameters
        ----------
        state_view : StateView
        at_ms : int
        history : object

        Returns
        -------
        tuple of Balance
            In currency order, one per currency the fold carries.
        """
        book = self.encumber(state_view, at_ms, history)
        return tuple(
            Balance(currency=row.currency, total=row.total, available=row.available, native=None)
            for row in book.funds.values()
        )

    def admit(self, proposal, state_view, at_ms, history):
        """Return every reason ``proposal`` may not be committed.

        Parameters
        ----------
        proposal : Proposal
        state_view : StateView
        at_ms : int
        history : object

        Returns
        -------
        tuple of str
            Empty when the proposal fits inside what is still available.

        Raises
        ------
        ProductionError
            If ``proposal`` is not a ``Proposal``, if the book cannot be
            derived, or — for a policy that declares no convention — if
            admission is not a question it can answer at all.
        """
        if not isinstance(proposal, Proposal):
            raise ProductionError([f"admit expects a Proposal, got {proposal!r}"])
        book = self.encumber(state_view, at_ms, history)
        problems = []
        self._check_sized(problems, book)
        judge = getattr(self, self._ADMIT[proposal.side])
        problems.extend(judge(proposal, book, state_view, at_ms))
        return tuple(problems)

    @staticmethod
    def _check_sized(problems, book):
        """Append a refusal while an intent the fold cannot size is outstanding."""
        if book.unsized_refs:
            problems.append(
                "admission cannot be judged while client refs "
                f"{sorted(book.unsized_refs)} carry an intent and no order event: "
                "the fold holds no quantity for them, so their commitment is unknown"
            )

    def _admit_buy(self, proposal, book, state_view, at_ms):
        """Return why this buy cannot be funded out of what is still available."""
        problems = []
        if proposal.qty is None:
            problems.append(f"proposal {proposal.id!r} declares no qty to fund")
            return problems
        if proposal.limit is None:
            problems.append(
                f"proposal {proposal.id!r} declares no limit, so what it would commit "
                "is not derivable: this policy values a commitment at its declared price"
            )
            return problems
        if len(book.funds) != 1:
            problems.append(
                f"proposal {proposal.id!r} cannot be funded out of "
                f"{sorted(book.funds)}: a proposal carries no currency, so exactly "
                "one pot must be in play"
            )
            return problems
        row = next(iter(book.funds.values()))
        wanted = proposal.qty * proposal.limit
        if wanted > row.available:
            problems.append(
                f"proposal {proposal.id!r} needs {wanted} {row.currency} of settled funds "
                f"and {row.available} is available: {row.total} total, less "
                f"{row.committed} committed and {row.unsettled} unsettled"
            )
        return problems

    def _admit_sell(self, proposal, book, state_view, at_ms):
        """Return why this sell cannot be covered by uncommitted held units."""
        problems = []
        if proposal.qty is None:
            problems.append(f"proposal {proposal.id!r} declares no qty to cover")
            return problems
        row = book.inventory.get(proposal.instrument)
        available = _ZERO if row is None else row.available
        if proposal.qty > available:
            problems.extend(
                self.borrow(proposal.instrument, proposal.qty - available, state_view, at_ms)
            )
        return problems

    def _admit_nothing(self, proposal, book, state_view, at_ms):
        """Return no problem: a sideless proposal commits nothing."""
        return []


class UndeclaredSettlement(EncumbrancePolicy):
    """The null object: no convention is declared, so nothing is encumbered.

    This is today's behaviour, named. ``available`` comes back equal to
    ``total`` through the same subtraction every other policy uses —
    minus zero, twice — rather than through a branch, and the history is
    never asked for a fill. What it will NOT do is authorise a
    commitment: :meth:`admit` refuses, because a figure that asserts
    nothing about outstanding orders must never be spent against.

    Parameters
    ----------
    params : dict, optional
        No knobs; ``notes`` is allowed.

    Examples
    --------
    ::

        policy = UndeclaredSettlement({})
        [(b.total, b.available) for b in policy.balances(view, at_ms, history)]
        # -> [(Decimal('1000'), Decimal('1000'))]
    """

    def encumber(self, state_view, at_ms, history):
        """Return the empty book: every balance free, every held unit free.

        Parameters
        ----------
        state_view : StateView
        at_ms : int
        history : object
            Never touched.

        Returns
        -------
        Encumbrance
        """
        return Encumbrance(
            funds=MappingProxyType(
                {
                    currency: FundsRow(
                        currency=currency,
                        total=total,
                        committed=_ZERO,
                        unsettled=_ZERO,
                        available=total,
                    )
                    for currency, total in sorted(state_view.balances.items())
                }
            ),
            inventory=MappingProxyType(
                {
                    position.instrument: InventoryRow(
                        instrument=position.instrument,
                        held=position.qty,
                        committed=_ZERO,
                        available=position.qty,
                    )
                    for position in state_view.positions
                }
            ),
            unsized_refs=tuple(state_view.pending),
        )

    def admit(self, proposal, state_view, at_ms, history):
        """Refuse: with no declared convention there is nothing to admit against.

        Parameters
        ----------
        proposal : Proposal
        state_view : StateView
        at_ms : int
        history : object

        Returns
        -------
        tuple of str
            Never — this policy always raises.

        Raises
        ------
        ProductionError
            Always.
        """
        raise ProductionError(
            [
                "this account declares no settlement convention, so it cannot say what is "
                "available to commit; its `available` is its `total` and asserts nothing "
                "about outstanding orders"
            ]
        )

    def borrow(self, instrument, qty, state_view, at_ms):
        """Refuse: with no declared convention there is no borrow stance to apply.

        Parameters
        ----------
        instrument : str
        qty : Decimal
        state_view : StateView
        at_ms : int

        Returns
        -------
        tuple of str
            One refusal, naming the instrument.
        """
        return (
            f"{instrument}: no borrow stance is declared, so {qty} units cannot be found",
        )


class CashSettlement(EncumbrancePolicy):
    """A cash account: outstanding orders hold funds and units, proceeds settle late.

    Cash committed to an outstanding BUY leaves ``available`` at
    ``remaining_qty * limit`` and comes back only when the order reaches a
    ``TERMINAL_STATUSES`` member — ``pending``, ``pending_cancel`` and
    ``unknown`` all hold it, because ``unknown`` is the absence of
    certainty rather than an end. Units committed to an outstanding SELL
    leave the instrument's available inventory the same way. What a fill
    has already taken out of ``remaining_qty`` is no longer committed; it
    is an obligation the venue has not yet settled, and it stays out of
    ``available`` until ``fill.ts_ms + settlement_lag_ms`` (inclusive: a
    fill exactly that old has settled).

    Which side that obligation is on is what ``balance_basis`` declares.
    Under ``trade_date`` the folded balance already reflects a fill, so
    unsettled SALE PROCEEDS sit inside ``total`` and must come out of
    ``available``. Under ``settlement_date`` it does not, so the
    unsettled PURCHASE COST has not left ``total`` yet and must come out
    instead. The package cannot know which is true of a series —
    ``SeriesState`` moves balances only on ``cash_flow``, but a
    reconciliation adjustment can sync folded cash to venue cash — so it
    is declared rather than assumed.

    Borrow is not modelled: :meth:`borrow` refuses, which is how a sell
    beyond the held position is rejected. Fees and slippage are not
    estimated into a commitment.

    Parameters
    ----------
    params : dict
        ``settlement_lag_ms`` (int >= 0, REQUIRED) and ``balance_basis``
        (one of ``vocab.BALANCE_BASES``, REQUIRED). Neither has a default:
        a settlement period and a balance basis are venue facts, and a
        guess would be wrong silently.

    Raises
    ------
    ProductionError
        On an unknown param, an absent or malformed ``settlement_lag_ms``,
        or an absent or unknown ``balance_basis``.

    Examples
    --------
    ::

        policy = CashSettlement({"settlement_lag_ms": 86_400_000, "balance_basis": "trade_date"})
        book = policy.encumber(view, at_ms, history)
        book.funds["USD"].committed     # Decimal('40') — one working buy of 4 at 10
        book.inventory["INS1"].available  # Decimal('6') — 10 held, 4 promised to a sell
    """

    _PARAMS = ("balance_basis", "settlement_lag_ms")

    #: Working-order side -> the method that states what it commits. A
    #: table keyed by the vocabulary, pinned at import.
    _COMMIT = pin_members(
        "encumbrance.py's _COMMIT table",
        {"buy": "_commit_cash", "sell": "_commit_units", "none": "_commit_nothing"},
        SIDES,
        exact=True,
    )

    #: Declared basis -> (the fill side whose cash the folded balance has
    #: NOT yet made usable, how that side's obligation is valued). Keyed by
    #: the whole vocabulary, so a new basis cannot land unhandled.
    _UNSETTLED = pin_members(
        "encumbrance.py's _UNSETTLED table",
        {"trade_date": ("sell", "_proceeds"), "settlement_date": ("buy", "_cost")},
        BALANCE_BASES,
        exact=True,
    )

    def __init__(self, params=None):
        super().__init__(params)
        declared = dict(params or {})
        self._lag = declared["settlement_lag_ms"]
        self._basis = declared["balance_basis"]

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when it is acceptable.

        Parameters
        ----------
        params : dict

        Returns
        -------
        list of str
            Accumulated problems, each naming the offending key.
        """
        problems = super().validate_params(params)
        cls._check_lag(problems, params)
        cls._check_basis(problems, params)
        return problems

    @staticmethod
    def _check_lag(problems, params):
        """Append why the declared settlement lag is unusable, if it is."""
        if "settlement_lag_ms" not in params:
            problems.append(
                "settlement_lag_ms is required: how long a venue takes to settle is a "
                "venue fact, and this package never invents one"
            )
            return
        value = params["settlement_lag_ms"]
        if isinstance(value, bool) or not isinstance(value, int):
            problems.append(f"settlement_lag_ms must be a duration in ms as an int, got {value!r}")
            return
        check_int_param(problems, "settlement_lag_ms", value, ge=0)

    @staticmethod
    def _check_basis(problems, params):
        """Append why the declared balance basis is unusable, if it is."""
        if "balance_basis" not in params:
            problems.append(
                "balance_basis is required: whether a folded balance already reflects a "
                "fill is a venue fact, and this package never assumes one"
            )
            return
        value = params["balance_basis"]
        if value not in BALANCE_BASES:
            problems.append(
                f"balance_basis must be one of {list(BALANCE_BASES)}, got {value!r}"
            )

    def encumber(self, state_view, at_ms, history):
        """Return what outstanding orders hold and what the venue has not yet settled.

        Parameters
        ----------
        state_view : StateView
        at_ms : int
        history : object
            Asked once, for the fills that can still be unsettled.

        Returns
        -------
        Encumbrance

        Raises
        ------
        ProductionError
            If the fold carries more than one currency — an order names
            no currency, so a commitment could not be attributed — or if
            a working buy carries no limit to value it at.
        """
        self._one_currency(state_view)
        committed, units = self._committed(state_view)
        unsettled = self._unsettled(at_ms, history)
        return Encumbrance(
            funds=MappingProxyType(
                {
                    currency: FundsRow(
                        currency=currency,
                        total=total,
                        committed=committed,
                        unsettled=unsettled,
                        available=total - committed - unsettled,
                    )
                    for currency, total in sorted(state_view.balances.items())
                }
            ),
            inventory=MappingProxyType(self._inventory(state_view, units)),
            unsized_refs=tuple(state_view.pending),
        )

    def borrow(self, instrument, qty, state_view, at_ms):
        """Refuse: a cash account borrows nothing, so a short sale has no locate.

        Parameters
        ----------
        instrument : str
        qty : Decimal
        state_view : StateView
        at_ms : int

        Returns
        -------
        tuple of str
            One refusal, naming the instrument and the shortfall.
        """
        return (
            f"{instrument}: {qty} units beyond the uncommitted position would have to be "
            "borrowed, and borrow, locate and short sales are not modelled here",
        )

    @staticmethod
    def _one_currency(state_view):
        """Refuse a balance set spanning currencies, which no order could be attributed to."""
        if len(state_view.balances) > 1:
            raise ProductionError(
                [
                    "cannot encumber a balance set spanning "
                    f"{sorted(state_view.balances)}: an order carries no currency, so "
                    "its commitment could not be attributed to one of them"
                ]
            )

    def _committed(self, state_view):
        """Return ``(cash, {instrument: units})`` the outstanding orders hold."""
        cash, units = _ZERO, {}
        for order in state_view.working.values():
            cash_held, units_held = getattr(self, self._COMMIT[order.side])(order)
            cash += cash_held
            units[order.instrument] = units.get(order.instrument, _ZERO) + units_held
        return cash, units

    def _commit_cash(self, order):
        """Return a working buy's cash commitment; it holds no units."""
        if order.limit is None:
            raise ProductionError(
                [
                    f"working buy {order.client_ref!r} carries no limit, so the cash it "
                    "holds is not derivable: this policy values a commitment at its "
                    "order's own declared price"
                ]
            )
        return (order.remaining_qty * order.limit, _ZERO)

    def _commit_units(self, order):
        """Return a working sell's unit commitment; it holds no cash."""
        return (_ZERO, order.remaining_qty)

    def _commit_nothing(self, order):
        """Return no commitment: a sideless working order holds neither."""
        return (_ZERO, _ZERO)

    @staticmethod
    def _inventory(state_view, units):
        """Return one row per instrument held or committed, free units and all."""
        held = {position.instrument: position.qty for position in state_view.positions}
        rows = {}
        for instrument in sorted(set(held) | set(units)):
            owned = held.get(instrument, _ZERO)
            promised = units.get(instrument, _ZERO)
            rows[instrument] = InventoryRow(
                instrument=instrument,
                held=owned,
                committed=promised,
                available=owned - promised,
            )
        return rows

    def _unsettled(self, at_ms, history):
        """Return the cash a fill has moved that the venue has not yet made usable."""
        side, valuation = self._UNSETTLED[self._basis]
        value = getattr(self, valuation)
        total = _ZERO
        for fill in effective_fills(history.fills(at_ms - self._lag), at_ms):
            if fill.side != side:
                continue
            if fill.ts_ms + self._lag <= at_ms:
                continue
            total += value(fill)
        return total

    @staticmethod
    def _proceeds(fill):
        """Return what a sale paid in, net of its fee."""
        return fill.qty * fill.price - fill.fee

    @staticmethod
    def _cost(fill):
        """Return what a purchase took out, including its fee."""
        return fill.qty * fill.price + fill.fee


#: The core encumbrance policies, by the name an ``encumbrance`` selector
#: uses. A TABLE rather than a §4.3 registry: no document key selects an
#: encumbrance policy, so a registry would add a family nothing selects
#: (ADR-0162) — the `readiness.EVIDENCE_RULES` precedent.
ENCUMBRANCE_POLICIES = MappingProxyType(
    {"undeclared": UndeclaredSettlement, "cash": CashSettlement}
)


class EncumberedAccounting(PaperAccounting):
    """Paper accounting whose ``available`` is derived, not asserted (ADR-0162).

    Everything :class:`PaperAccounting` does it still does: the same
    ``value``, the same ``classify``, the same re-anchored evidence, the
    same token discipline. The one thing it changes is the hook that
    builds the snapshot's balances — where the base writes
    ``available=total``, this asks its policy what is still free.

    It is a deliberate SUBCLASS rather than a knob on the base. An
    existing document selects ``paper`` and keeps ``available == total``
    exactly; a document that wants the derived figure names this class
    and MUST declare a policy, because an account that cannot say what
    its convention is has no business reporting buying power.

    Parameters
    ----------
    params : dict
        ``encumbrance`` (REQUIRED): a ``{uses, params}`` selector naming
        one of :data:`ENCUMBRANCE_POLICIES`. ``notes`` is allowed beside it.
    clock : Clock
    history : object
        ``fills(since_ms)`` / ``cash_flows(since_ms)`` / ``marks(since_ms)``
        — the same collaborator the base folds evidence from, handed on to
        the policy.
    max_valuation_age_ms : int

    Raises
    ------
    ProductionError
        On an unknown param, an absent or malformed ``encumbrance``
        selector, or a problem the named policy reports with its own params.

    Examples
    --------
    ::

        accounting = EncumberedAccounting(
            {"encumbrance": {"uses": "cash", "params": {
                "settlement_lag_ms": 86_400_000, "balance_basis": "trade_date"}}},
            clock=clock, history=history, max_valuation_age_ms=60_000,
        )
        account = accounting.snapshot(view, executor, quotes, at_ms, (), calendar)
        account.balances[0].available  # Decimal('960') — 1000 less a working buy of 4 at 10
    """

    _PARAMS = ("encumbrance",)

    def __init__(self, params=None, *, clock, history, max_valuation_age_ms):
        super().__init__(
            params, clock=clock, history=history, max_valuation_age_ms=max_valuation_age_ms
        )
        declared = dict(params or {})["encumbrance"]
        self._policy = ENCUMBRANCE_POLICIES[declared["uses"]](declared.get("params") or {})

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when it is acceptable.

        Parameters
        ----------
        params : dict

        Returns
        -------
        list of str
            Accumulated problems, each naming the offending key.
        """
        problems = super().validate_params(params)
        cls._check_policy(problems, dict(params or {}))
        return problems

    @staticmethod
    def _check_policy(problems, params):
        """Append why the declared encumbrance selector is unusable, if it is."""
        declared = params.get("encumbrance")
        if declared is None:
            problems.append(
                "encumbrance is required: an account that derives `available` must name "
                "the policy that derives it"
            )
            return
        if not isinstance(declared, dict):
            problems.append(
                f"encumbrance must be a selector object with a `uses`, got {declared!r}"
            )
            return
        stray = sorted(set(declared) - set(_SELECTOR_KEYS))
        if stray:
            problems.append(f"encumbrance carries keys outside {list(_SELECTOR_KEYS)}: {stray}")
        policy = ENCUMBRANCE_POLICIES.get(declared.get("uses"))
        if policy is None:
            problems.append(
                f"encumbrance.uses must name one of {sorted(ENCUMBRANCE_POLICIES)}, got "
                f"{declared.get('uses')!r}"
            )
            return
        problems.extend(policy.validate_params(declared.get("params") or {}))

    def _balances(self, state_view, at_ms):
        """Return the balances the policy derives, ``available`` and all."""
        return self._policy.balances(state_view, at_ms, self._history)

    def encumbrance(self, state_view, at_ms):
        """Return the derived book — what is committed, unsettled and still free.

        Parameters
        ----------
        state_view : StateView
        at_ms : int

        Returns
        -------
        Encumbrance
        """
        return self._policy.encumber(state_view, at_ms, self._history)

    def admit(self, proposal, state_view, at_ms):
        """Return every reason ``proposal`` may not be committed.

        Parameters
        ----------
        proposal : Proposal
        state_view : StateView
        at_ms : int

        Returns
        -------
        tuple of str
            Empty when the proposal fits inside what is still available.

        Raises
        ------
        ProductionError
            If the policy cannot answer admission at all.
        """
        return self._policy.admit(proposal, state_view, at_ms, self._history)
