"""`encumbrance.py` — what the account may still commit, and what it may not (ADR-0162).

`Balance` has carried `total` and `available` since §5.4, and every `available`
the package produced before this module was its `total`. That is the defect
this file pins: an account with a working buy for 4 at 10 against a balance of
1000 has 960 to commit, not 1000, and a snapshot that says otherwise is a
buying-power figure that asserts nothing.

Each assertion below can pass for only one reason.

* **`available` is DERIVED.** The subtraction has one owner
  (`EncumbrancePolicy.balances`), so the tests state the INPUTS — a balance, an
  order, a fill — and the one number a caller would act on.
* **Uncertainty holds.** `TERMINAL_STATUSES` excludes `unknown` because it is
  the absence of certainty, not an end. Every non-terminal status is
  parametrised, and the parametrisation is itself pinned equal to
  `STATUSES - TERMINAL_STATUSES`, so a policy that released funds on `unknown`
  or `pending_cancel` fails by name rather than by an example someone chose.
* **A venue fact is declared, never guessed.** `CashSettlement` refuses
  without `settlement_lag_ms` and refuses without `balance_basis`, each with
  its own message, so a test naming one gate cannot pass on the other.
* **Nothing is stored.** The restart test rebuilds the fold from the durable
  `snapshot` payload and demands the same book from the same instant. A policy
  that cached a reservation in memory fails it.
* **The null object is the compatibility guarantee.** `UndeclaredSettlement`
  returns `available == total` through the same subtraction — minus zero,
  twice — while a `Boom` history proves it never asked for a fill, and
  `PaperAccounting` is asserted unchanged WITH an order outstanding, which is
  the case a silent regression would show up in.

The audit's own acceptance test is quoted and executed verbatim at the end,
against a real `SeriesState` folded from real §6 envelopes — the chain
builders are imported from `test_state.py` rather than restated, because a
second copy of the envelope rules would drift from the fold they are meant to
exercise.

No wall clock, no network, no real executor: every instant is an int computed
here and the executor is a fake whose every attribute raises.
"""

import dataclasses
import inspect
import random
from decimal import Decimal
from types import MappingProxyType

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dskit.production import records, vocab
from dskit.production.accounting import PaperAccounting
from dskit.production.base import ProductionError
from dskit.production.encumbrance import (
    ENCUMBRANCE_POLICIES,
    CashSettlement,
    EncumberedAccounting,
    Encumbrance,
    EncumbrancePolicy,
    FundsRow,
    SettledFundsShortfall,
    UncommittedUnitsShortfall,
    UndeclaredSettlement,
    _amount,
)
from dskit.production.guards import MEASURE_KINDS, Limit, Window
from dskit.production.state import SeriesState, StateView, TickState
from tests.production.test_state import (
    BASE_MS as FOLD_BASE_MS,
)
from tests.production.test_state import (
    SERIES_ID,
    Chain,
    cash_flow_body,
    fold,
    intent_body,
    order_event_body,
    snapshot_env,
)
from tests.production.test_state import (
    fill_body as folded_fill_body,
)

# ---------------------------------------------------------------------------
# Fixed material — every instant and Decimal below is computed here
# ---------------------------------------------------------------------------

SECOND = 1_000
MINUTE = 60 * SECOND
HOUR = 60 * MINUTE
DAY = 24 * HOUR

BASE_MS = 1_767_268_800_000
T0 = BASE_MS + HOUR

USD = "USD"
EUR = "EUR"
INS1 = "INS1"
INS2 = "INS2"

#: §4.1's illustration: how old a mark may be and still value the book.
MAX_VALUATION_AGE_MS = 60_000

#: A settlement period is a venue choice, so the suite picks one for itself
#: and never reads one back from the package.
LAG_MS = DAY

#: The two core policy names, restated rather than read from the table: a
#: list sourced from its subject asserts nothing (CLAUDE.md).
CORE_POLICIES = ("cash", "undeclared")

#: Every status a working order can still be carrying, restated from §5.0's
#: rule — "`unknown` is the absence of certainty, not an end" — and pinned
#: below against `STATUSES - TERMINAL_STATUSES`.
HOLDING_STATUSES = ("pending", "open", "partial", "pending_cancel", "unknown")

#: The instant the folded series is judged at: after its cash flow and its
#: fill, and well inside the declared settlement lag.
FOLD_AT_MS = FOLD_BASE_MS + HOUR


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeClock:
    """The one `Clock` method accounting needs; no wall time."""

    def __init__(self, ms=T0):
        self._ms = int(ms)

    def now_ms(self):
        return self._ms

    def monotonic(self):
        return self._ms / 1000.0


class FakeHistory:
    """The injected fold source: §6 fill bodies since an instant.

    Records the `since_ms` it was asked for, which is how "asked once, no
    wider than the declared lag" is checkable.
    """

    def __init__(self, fills=()):
        self._fills = tuple(fills)
        self.calls = []

    def fills(self, since_ms):
        # `reconcile.LedgerHistory.fills` checks its bound with
        # `pipeline.node.check_int_param`, which refuses a bool, COERCES an
        # integral float, and only then range-checks. So `470.0` is accepted
        # there and must be accepted here: a double stricter than its subject
        # declares impossible a call the real collaborator would take
        # (round-2 Minor 2). Restated deliberately rather than imported — a
        # double that read its rule from the thing it stands in for would
        # assert nothing.
        bound = since_ms
        if isinstance(bound, float) and bound.is_integer():
            bound = int(bound)
        if isinstance(bound, bool) or not isinstance(bound, int) or bound < 0:
            raise ProductionError([f"since_ms must be an int >= 0, got {since_ms!r}"])
        self.calls.append(bound)
        return tuple(f for f in self._fills if f["ts_ms"] >= bound)

    def set_fills(self, fills):
        """Change what the source reports WITHOUT changing its identity."""
        self._fills = tuple(fills)

    def cash_flows(self, since_ms):
        return ()

    def marks(self, since_ms):
        return ()


class Boom:
    """A collaborator that must NOT be reached: touching it is the defect."""

    def __init__(self, what="collaborator"):
        object.__setattr__(self, "_what", what)

    def __getattr__(self, name):
        raise AssertionError(f"a policy that encumbers nothing touched {self._what}.{name}")


class FakeCalendar:
    """A calendar with no windows: these tests declare no evidence requirement."""

    def window(self, name, at_ms):
        raise ProductionError([f"calendar: unknown window kind {name!r}"])

    def is_open(self, at_ms):
        return True


NO_QUOTES = records.QuoteSet(quotes=(), quote_digest="3" * 64, min_asof_ms=T0)


def quotes_for(*instruments):
    """A fresh `QuoteSet` covering every instrument a snapshot has to mark.

    `PaperAccounting.snapshot` REFUSES a held instrument with no fresh mark —
    a snapshot is what a permit binds — so any view carrying a position needs
    one here. Prices are this suite's own; nothing reads them back.
    """
    quotes = tuple(
        records.Quote(
            instrument=instrument,
            bid=Decimal("9.99"),
            ask=Decimal("10.01"),
            mid=Decimal("10"),
            asof_ms=T0,
        )
        for instrument in instruments
    )
    return records.QuoteSet(quotes=quotes, quote_digest="3" * 64, min_asof_ms=T0)


# ---------------------------------------------------------------------------
# Builders — real records, never dicts pretending to be them
# ---------------------------------------------------------------------------


def order(
    ref="ref-1",
    instrument=INS1,
    side="buy",
    qty="4",
    filled="0",
    limit="10",
    status="open",
):
    """One working order, as the fold projects it."""
    quantity, done = Decimal(qty), Decimal(filled)
    return records.OrderState(
        client_ref=ref,
        venue_ref=f"v-{ref}",
        status=status,
        ts_ms=T0,
        filled_qty=done,
        avg_price=None if done == 0 else Decimal(limit),
        fee=Decimal("0"),
        reason="",
        native=None,
        instrument=instrument,
        side=side,
        qty=quantity,
        remaining_qty=quantity - done,
        limit=None if limit is None else Decimal(limit),
        tif="gtc",
        created_ms=T0 - MINUTE,
        updated_ms=T0,
    )


def position(instrument=INS1, qty="10", avg_cost="10"):
    """One held position, as the fold projects it."""
    return records.Position(
        instrument=instrument,
        qty=Decimal(qty),
        avg_cost=Decimal(avg_cost),
        source="derived",
        native=None,
    )


def fill_body(fill_id, side, qty, price, ts_ms, fee="0", instrument=INS1, status="final"):
    """One §6 `fill` body, built through the real record type."""
    return records.Fill(
        fill_id=fill_id,
        venue_ref=f"v-{fill_id}",
        client_ref=f"ref-{fill_id}",
        instrument=instrument,
        side=side,
        qty=Decimal(qty),
        price=Decimal(price),
        fee=Decimal(fee),
        fee_currency=USD,
        liquidity="taker",
        status=status,
        ts_ms=ts_ms,
        native=None,
    ).to_obj()


def view(positions=(), working=(), balances=None, pending=(), economic_seq=41):
    """A real frozen `StateView` — the type the seam takes as its first argument."""
    held = dict(balances if balances is not None else {USD: Decimal("1000")})
    return StateView(
        positions=tuple(positions),
        working=MappingProxyType({o.client_ref: o for o in working}),
        pending=tuple(pending),
        balances=MappingProxyType(held),
        decision_history=(),
        breaker="active",
        arming=None,
        readiness=None,
        guard_holds=MappingProxyType({}),
        reduction=None,
        pending_control=MappingProxyType({}),
        risk_version=records.RiskVersion(
            economic_seq=economic_seq, executor_token=None, accounting_tokens=None
        ),
        head_seq=economic_seq,
        head_hash="a" * 64,
    )


def proposal(side="buy", qty="10", limit="10", instrument=INS1, pid="cand-1"):
    """The worked proposal: a limit order a lead wants committed."""
    return records.Proposal(
        id=pid,
        instrument=instrument,
        side=side,
        qty=None if qty is None else Decimal(qty),
        notional=None,
        limit=None if limit is None else Decimal(limit),
        tif="gtc",
        expires_ms=T0 + 5 * MINUTE,
        reference_price=Decimal("10"),
        exposure=Decimal("100"),
        direction="long",
        confidence=0.61,
        prediction=0.58,
        baseline=0.50,
        expected_value=0.03,
        inputs_asof_ms=T0,
        inputs_digest="1" * 64,
        coverage_digest="2" * 64,
        quote_asof_ms=T0,
        quote_digest="3" * 64,
        extra={},
    )


def cash(basis="trade_date", lag_ms=LAG_MS):
    """The real settling policy, with the suite's own declared venue facts."""
    return CashSettlement({"settlement_lag_ms": lag_ms, "balance_basis": basis})


def selector(basis="trade_date", lag_ms=LAG_MS):
    """The `{uses, params}` block a document would write for that policy."""
    return {"uses": "cash", "params": {"settlement_lag_ms": lag_ms, "balance_basis": basis}}


def accounting(policy=None, history=None, clock=None):
    """An `EncumberedAccounting` over the declared policy selector."""
    return EncumberedAccounting(
        {"encumbrance": policy if policy is not None else selector()},
        clock=clock or FakeClock(),
        history=history if history is not None else FakeHistory(),
        max_valuation_age_ms=MAX_VALUATION_AGE_MS,
    )


def paper(history=None, clock=None):
    """The unchanged base strategy, for the compatibility assertions."""
    return PaperAccounting(
        {},
        clock=clock or FakeClock(),
        history=history if history is not None else FakeHistory(),
        max_valuation_age_ms=MAX_VALUATION_AGE_MS,
    )


def only(balances):
    """Return the one `Balance` a single-currency snapshot carries."""
    assert len(balances) == 1, balances
    return balances[0]


def tick_state(state_view):
    """The `TickState` `classify` reads, with this suite's account snapshot in it."""
    account = records.AccountState(
        risk_version=state_view.risk_version,
        asof_ms=T0,
        evidence_digest="4" * 64,
        balances=(),
        positions=tuple(state_view.positions),
        working=tuple(state_view.working.values()),
        measure_evidence={},
        source_digests={},
    )
    return TickState(
        view=state_view,
        account=account,
        feed_status="live",
        feed_ages=(),
        calendar=FakeCalendar(),
    )


# ---------------------------------------------------------------------------
# The seam itself
# ---------------------------------------------------------------------------


def test_the_policy_seam_refuses_to_construct_until_both_hooks_are_supplied():
    """§5.15: a hook that only raised `NotImplementedError` would let an
    incomplete policy construct and fail at the first tick. `encumber` and
    `borrow` are the two facts core cannot know for a real venue, so both
    are `@abstractmethod` and the refusal must name abstractness."""
    assert inspect.isabstract(EncumbrancePolicy)
    assert EncumbrancePolicy.__abstractmethods__ == {"encumber", "borrow"}
    with pytest.raises(TypeError, match="abstract"):
        EncumbrancePolicy()


def test_the_core_policies_are_the_two_the_decision_names():
    """The table is the seam's selector. Restated, never read back from the
    package: a list sourced from its subject asserts nothing."""
    assert sorted(ENCUMBRANCE_POLICIES) == sorted(CORE_POLICIES)
    for name in CORE_POLICIES:
        assert issubclass(ENCUMBRANCE_POLICIES[name], EncumbrancePolicy)


def test_a_policy_refuses_a_param_it_does_not_declare():
    """Default-deny: a typo is an error, not a silent default."""
    with pytest.raises(ProductionError, match="mark_to"):
        UndeclaredSettlement({"mark_to": "last"})


# ---------------------------------------------------------------------------
# 1. `available` is derived, never asserted
# ---------------------------------------------------------------------------


def test_an_outstanding_buy_holds_its_cash_out_of_available():
    """The defect this module exists for: a balance of 1000 with a working buy
    for 4 at 10 has 960 to commit. `total` is untouched — the cash has not
    left the account, it is spoken for."""
    book = cash().encumber(view(working=(order(qty="4", limit="10"),)), T0, FakeHistory())
    row = book.funds[USD]
    assert (row.total, row.committed, row.unsettled, row.available) == (
        Decimal("1000"),
        Decimal("40"),
        Decimal("0"),
        Decimal("960"),
    )


def test_available_equals_total_only_when_nothing_is_outstanding():
    """The other half: the derivation must not invent an encumbrance. With no
    working order and no fill, the derived figure IS the balance."""
    row = cash().encumber(view(), T0, FakeHistory()).funds[USD]
    assert (row.total, row.available) == (Decimal("1000"), Decimal("1000"))


def test_the_snapshot_a_permit_binds_carries_the_derived_available():
    """`available` reaching a `Balance` is what makes the derivation usable:
    `AccountState.balances` is what a permit binds and what `risk_digest`
    covers, so a figure that stopped at the policy would change nothing."""
    account = accounting().snapshot(
        view(working=(order(qty="4", limit="10"),)),
        Boom("executor"),
        NO_QUOTES,
        T0,
        (),
        FakeCalendar(),
    )
    balance = only(account.balances)
    assert (balance.currency, balance.total, balance.available) == (
        USD,
        Decimal("1000"),
        Decimal("960"),
    )


def test_two_leads_cannot_both_commit_the_same_scarce_cash():
    """Two concurrent leads, one balance. The first lead's order is in the
    fold by the time the second is judged — the leg pipeline's fresh fold is
    what puts it there — so the second is refused for the funds the first
    already holds, rather than allowed because the balance still reads 100."""
    scarce = view(balances={USD: Decimal("100")})
    assert cash().admit(proposal(qty="10", limit="10"), scarce, T0, FakeHistory()) == ()

    after_first = view(
        balances={USD: Decimal("100")},
        working=(order(ref="lead-1", qty="10", limit="10"),),
    )
    second = cash().admit(
        proposal(qty="10", limit="10", pid="cand-2"), after_first, T0, FakeHistory()
    )
    assert second, "the second lead was funded out of cash the first already holds"
    assert "past the settled funds available" in second[0]


# ---------------------------------------------------------------------------
# 2. Inventory is reserved for outstanding sells
# ---------------------------------------------------------------------------


def test_units_promised_to_a_working_sell_are_not_available_again():
    """Held units committed to an outstanding sell cannot be committed twice."""
    book = cash().encumber(
        view(positions=(position(qty="10"),), working=(order(side="sell", qty="4"),)),
        T0,
        FakeHistory(),
    )
    row = book.inventory[INS1]
    assert (row.held, row.committed, row.available) == (
        Decimal("10"),
        Decimal("4"),
        Decimal("6"),
    )


def test_a_second_sell_of_the_same_units_is_refused_as_an_unavailable_borrow():
    """Ten held, six already promised: a further sell of six reaches two units
    the account does not have, and a cash account has no locate for them."""
    committed = view(
        positions=(position(qty="10"),),
        working=(order(ref="lead-1", side="sell", qty="6"),),
    )
    problems = cash().admit(proposal(side="sell", qty="6"), committed, T0, FakeHistory())
    assert problems
    assert "borrowed" in problems[0] and INS1 in problems[0]


def test_a_sell_inside_the_uncommitted_position_is_admitted():
    """The refusal must not be unconditional, or it would prove nothing."""
    committed = view(
        positions=(position(qty="10"),),
        working=(order(ref="lead-1", side="sell", qty="6"),),
    )
    assert cash().admit(proposal(side="sell", qty="4"), committed, T0, FakeHistory()) == ()


def test_a_sell_on_an_instrument_the_account_does_not_hold_is_refused():
    """No position at all is the same fact as an exhausted one."""
    problems = cash().admit(
        proposal(side="sell", qty="1", instrument=INS2), view(), T0, FakeHistory()
    )
    assert problems and INS2 in problems[0]


# ---------------------------------------------------------------------------
# 3. Uncertainty keeps funds encumbered
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", HOLDING_STATUSES)
def test_every_non_terminal_status_keeps_its_commitment(status):
    """§5.0: `unknown` is deliberately not terminal — "the absence of
    certainty, not an end". Applied to money, that means `pending`,
    `pending_cancel` and `unknown` hold their cash exactly as `open` does."""
    book = cash().encumber(
        view(working=(order(qty="4", limit="10", status=status),)), T0, FakeHistory()
    )
    assert book.funds[USD].committed == Decimal("40")


def test_the_statuses_that_hold_are_exactly_the_non_terminal_ones():
    """The parametrisation above is only a pin if it is the WHOLE set: a
    status that left `TERMINAL_STATUSES` without joining the list would be
    released by a policy no test covers."""
    assert set(HOLDING_STATUSES) == set(vocab.STATUSES) - set(vocab.TERMINAL_STATUSES)


def test_a_partial_fill_releases_only_the_filled_portion_from_the_commitment():
    """Ten at ten, four filled: the commitment falls to the SIX that are still
    outstanding. The four are not free cash — they are an obligation the venue
    has not settled, which the next test reads."""
    book = cash().encumber(
        view(working=(order(qty="10", filled="4", limit="10", status="partial"),)),
        T0,
        FakeHistory(),
    )
    assert book.funds[USD].committed == Decimal("60")


def test_the_filled_portion_becomes_a_settlement_obligation_not_free_cash():
    """The same partial, with its fill in the history: under a settlement-date
    basis the purchase cost has not left the folded balance yet, so it comes
    out of `available` rather than being spent again."""
    history = FakeHistory((fill_body("f-1", "buy", "4", "10", T0 - MINUTE, fee="1"),))
    book = cash(basis="settlement_date").encumber(
        view(working=(order(qty="10", filled="4", limit="10", status="partial"),)),
        T0,
        history,
    )
    row = book.funds[USD]
    assert (row.committed, row.unsettled, row.available) == (
        Decimal("60"),
        Decimal("41"),
        Decimal("899"),
    )


# ---------------------------------------------------------------------------
# 4. Settled versus unsettled, under a REQUIRED declared convention
# ---------------------------------------------------------------------------


def test_the_settlement_period_is_required_and_never_invented():
    """T+1 versus T+2 is an owner and venue choice. A policy that defaulted
    would be silently wrong on the venue that disagreed."""
    with pytest.raises(ProductionError, match="settlement_lag_ms is required"):
        CashSettlement({"balance_basis": "trade_date"})


def test_the_balance_basis_is_required_and_never_assumed():
    """Whether the folded balance already reflects a fill decides WHICH side's
    cash is unsettled, so assuming one silently double-counts the other."""
    with pytest.raises(ProductionError, match="balance_basis is required"):
        CashSettlement({"settlement_lag_ms": LAG_MS})


def test_an_unknown_balance_basis_is_refused_by_name():
    with pytest.raises(ProductionError, match="balance_basis must be one of"):
        CashSettlement({"settlement_lag_ms": LAG_MS, "balance_basis": "next_tuesday"})


def test_sale_proceeds_are_unavailable_until_the_declared_lag_has_passed():
    """Under a trade-date basis the proceeds are already inside `total`; they
    are simply not yet usable, so they come out of `available`."""
    history = FakeHistory((fill_body("f-1", "sell", "10", "10", T0 - HOUR, fee="1"),))
    row = cash().encumber(view(), T0, history).funds[USD]
    assert (row.total, row.unsettled, row.available) == (
        Decimal("1000"),
        Decimal("99"),
        Decimal("901"),
    )


def test_a_fill_exactly_the_declared_lag_old_has_settled():
    """The boundary is inclusive: credited AT `ts_ms + settlement_lag_ms`, so
    a fill exactly that old is spendable and one millisecond younger is not."""
    settled = FakeHistory((fill_body("f-1", "sell", "10", "10", T0 - LAG_MS),))
    young = FakeHistory((fill_body("f-1", "sell", "10", "10", T0 - LAG_MS + 1),))
    assert cash().encumber(view(), T0, settled).funds[USD].unsettled == Decimal("0")
    assert cash().encumber(view(), T0, young).funds[USD].unsettled == Decimal("100")


def test_the_basis_decides_which_side_is_unsettled():
    """One history, two declared bases, two different answers — which is the
    whole reason the basis is declared rather than assumed."""
    history = FakeHistory(
        (
            fill_body("f-1", "sell", "10", "10", T0 - HOUR),
            fill_body("f-2", "buy", "5", "10", T0 - HOUR),
        )
    )
    on_trade = cash(basis="trade_date").encumber(view(), T0, history)
    on_settlement = cash(basis="settlement_date").encumber(view(), T0, history)
    assert on_trade.funds[USD].unsettled == Decimal("100")
    assert on_settlement.funds[USD].unsettled == Decimal("50")


def test_the_history_is_asked_once_and_no_wider_than_the_declared_lag():
    """A policy that scanned the whole chain would make every snapshot pay for
    the venue's entire history."""
    history = FakeHistory()
    cash().encumber(view(), T0, history)
    assert history.calls == [T0 - LAG_MS]


def test_a_reversed_fill_settles_nothing():
    """The venue undid it, so it never moved cash: the ONE reading of "which
    fills count" is `accounting.effective_fills`, not a second copy here."""
    history = FakeHistory(
        (
            fill_body("f-1", "sell", "10", "10", T0 - HOUR),
            fill_body("f-1", "sell", "10", "10", T0 - HOUR, status="reversed"),
        )
    )
    assert cash().encumber(view(), T0, history).funds[USD].unsettled == Decimal("0")


# ---------------------------------------------------------------------------
# Refusals the derivation owes
# ---------------------------------------------------------------------------


def test_a_balance_set_spanning_currencies_refuses_rather_than_guessing():
    """An order carries no currency, so a commitment could not be attributed
    to one of two balances. `PaperAccounting.value` already refuses the same
    shape; guessing here would silently encumber the wrong pot."""
    spanning = view(balances={USD: Decimal("1000"), EUR: Decimal("500")})
    with pytest.raises(ProductionError, match="cannot encumber a balance set spanning"):
        cash().encumber(spanning, T0, FakeHistory())


def test_a_working_buy_with_no_limit_refuses_rather_than_being_valued_by_a_guess():
    """A market buy's cash requirement is not derivable from the order. A
    policy that reached for a reference price would be inventing a
    buying-power formula."""
    with pytest.raises(ProductionError, match="carries no limit, so the cash it holds"):
        cash().encumber(view(working=(order(limit=None),)), T0, FakeHistory())


def test_admission_refuses_while_an_intent_the_fold_cannot_size_is_outstanding():
    """`StateView.pending` carries client refs, not quantities. An unknown
    commitment must refuse, never round down to zero."""
    problems = cash().admit(proposal(), view(pending=("ref-9",)), T0, FakeHistory())
    assert problems and "ref-9" in problems[0]


def test_admission_refuses_anything_that_is_not_a_proposal():
    with pytest.raises(ProductionError, match="admit expects a Proposal"):
        cash().admit("buy ten", view(), T0, FakeHistory())


def test_a_market_buy_proposal_is_refused_for_having_no_declared_price():
    problems = cash().admit(proposal(limit=None), view(), T0, FakeHistory())
    assert problems and "declares no limit" in problems[0]


def test_a_sideless_proposal_commits_nothing():
    """`none` is the abstaining side; it reaches no resource."""
    assert cash().admit(proposal(side="none", qty="0"), view(), T0, FakeHistory()) == ()


class TwoPot(EncumbrancePolicy):
    """A policy that DOES report two currencies — the base contract, exercised.

    `CashSettlement` refuses a spanning balance set before it can build one,
    so the base's own rule would be unreachable and untested through it. A
    minimal implementation is what the seam is for.
    """

    def encumber(self, state_view, at_ms, history):
        rows = {
            currency: FundsRow(
                currency=currency,
                total=total,
                committed=Decimal("0"),
                unsettled=Decimal("0"),
                available=total,
            )
            for currency, total in sorted(state_view.balances.items())
        }
        return Encumbrance(
            funds=MappingProxyType(rows), inventory=MappingProxyType({}), unsized_refs=()
        )

    def borrow(self, instrument, qty, state_view, at_ms):
        return (f"{instrument}: TwoPot lends nothing",)


def test_a_buy_is_refused_when_more_than_one_pot_could_fund_it():
    """A `Proposal` carries no currency. Funding one out of whichever row came
    first would spend the wrong pot silently, so the base refuses instead."""
    spanning = view(balances={USD: Decimal("1000"), EUR: Decimal("500")})
    problems = TwoPot({}).admit(proposal(), spanning, T0, FakeHistory())
    assert problems and "a proposal carries no currency" in problems[0]


def test_a_buy_is_refused_when_no_pot_could_fund_it():
    """The other end of the same rule: an account with no folded balance has
    nothing to fund out of."""
    empty = view(balances={})
    problems = TwoPot({}).admit(proposal(), empty, T0, FakeHistory())
    assert problems and "cannot be funded out of []" in problems[0]


# ---------------------------------------------------------------------------
# The null object — today's behaviour, named, and the compatibility guarantee
# ---------------------------------------------------------------------------


def test_the_undeclared_policy_reports_available_equal_to_total_with_orders_outstanding():
    """The compatibility guarantee. It is NOT a claim about the venue: it is
    the statement that no convention was declared, so nothing was encumbered
    — and it comes out of the same subtraction, minus zero twice."""
    outstanding = view(working=(order(qty="4", limit="10"),), positions=(position(),))
    balance = only(UndeclaredSettlement({}).balances(outstanding, T0, Boom("history")))
    assert (balance.total, balance.available) == (Decimal("1000"), Decimal("1000"))


def test_the_undeclared_policy_never_asks_for_a_fill():
    """A policy that encumbers nothing has nothing to settle, so touching the
    history would be a cost paid for no answer."""
    UndeclaredSettlement({}).encumber(view(working=(order(),)), T0, Boom("history"))


def test_the_undeclared_policy_refuses_to_authorise_a_commitment():
    """The figure above asserts nothing about outstanding orders, so it must
    never be spent against. Reporting it and authorising against it are two
    different permissions."""
    # NOT a `Boom` collaborator: `admit` refuses before reading anything, so a
    # fixture that raises on attribute access would prove nothing here (round-1
    # Nit 6). The two `Boom` tests above are the ones where not-touching is
    # the claim.
    with pytest.raises(ProductionError, match="declares no settlement convention"):
        UndeclaredSettlement({}).admit(proposal(), view(), T0, FakeHistory())


def test_paper_accounting_still_reports_available_equal_to_total():
    """The regression a reviewer hunts for: the base strategy must be
    untouched, and untouched WITH an order outstanding — the case where a
    silent derivation would show up."""
    account = paper().snapshot(
        view(working=(order(qty="4", limit="10"),)),
        Boom("executor"),
        NO_QUOTES,
        T0,
        (),
        FakeCalendar(),
    )
    balance = only(account.balances)
    assert (balance.total, balance.available) == (Decimal("1000"), Decimal("1000"))


def test_paper_accounting_declares_no_encumbrance_knob():
    """The base's default-deny surface is unchanged: a document that declared
    a policy against `paper` is refused, not silently ignored."""
    assert PaperAccounting._PARAMS == ()
    with pytest.raises(ProductionError, match="encumbrance"):
        PaperAccounting(
            {"encumbrance": selector()},
            clock=FakeClock(),
            history=FakeHistory(),
            max_valuation_age_ms=MAX_VALUATION_AGE_MS,
        )


# ---------------------------------------------------------------------------
# The accounting subclass and its selector
# ---------------------------------------------------------------------------


def test_the_encumbered_strategy_requires_a_declared_policy():
    """An account that derives buying power must say what derives it."""
    with pytest.raises(ProductionError, match="encumbrance is required"):
        EncumberedAccounting(
            {},
            clock=FakeClock(),
            history=FakeHistory(),
            max_valuation_age_ms=MAX_VALUATION_AGE_MS,
        )


def test_an_unknown_policy_name_is_refused_by_name():
    with pytest.raises(ProductionError, match="encumbrance.uses must name one of"):
        accounting(policy={"uses": "overdraft"})


def test_the_policys_own_params_are_validated_at_the_accounting_site():
    """A document must fail on a missing settlement period where it is
    written, not at the first tick that needs one."""
    with pytest.raises(ProductionError, match="settlement_lag_ms is required"):
        accounting(policy={"uses": "cash", "params": {"balance_basis": "trade_date"}})


def test_the_strategys_two_pass_throughs_answer_exactly_what_its_policy_answers():
    """A child holds the `Accounting` object, not the policy, so `encumbrance`
    and `admit` are the surface it wraps — and the round-1 Major 1 sweep found
    both reachable and untested, which is the same shape as the finding.
    Pinned against the policy directly so a pass-through that started
    answering for itself fails."""
    history = FakeHistory((fill_body("f-1", "sell", "10", "10", T0 - MINUTE),))
    strategy = accounting(history=history)
    busy = view(working=(order(qty="4", limit="10"),), positions=(position(),))
    policy = cash()
    assert strategy.encumbrance(busy, T0) == policy.encumber(busy, T0, history)
    wanted = proposal(qty="500", limit="10")
    assert strategy.admit(wanted, busy, T0) == policy.admit(wanted, busy, T0, history)
    assert strategy.admit(wanted, busy, T0)


def test_the_encumbered_strategy_keeps_every_other_paper_behaviour():
    """It changes ONE hook. `classify` still proves a reduction against
    positions and working orders rather than believing a model claim."""
    strategy = accounting()
    held = view(positions=(position(qty="10"),))
    assert strategy.classify(proposal(side="sell", qty="4"), tick_state(held)) == "reduce"
    assert strategy.classify(proposal(side="buy", qty="4"), tick_state(held)) == "increase"


# ---------------------------------------------------------------------------
# 5 and 6. The audit's acceptance test, over a real fold
# ---------------------------------------------------------------------------


def scarce_series():
    """Fold the acceptance scenario: scarce cash, one partially filled order whose
    remainder is `unknown`, and the history that fill is visible through.

    Returns
    -------
    tuple
        ``(SeriesState, Chain, FakeHistory)`` — a deposit of 2500 USD, a buy
        of 10 AAA at 100 acknowledged and then filled for 4, left in
        ``unknown``.
    """
    chain, state = Chain(), SeriesState(SERIES_ID)
    fold(state, chain, "cash_flow", cash_flow_body(amount="2500"))
    fold(state, chain, "intent", intent_body(client_ref="ref-1", qty="10"))
    fold(state, chain, "order_event", order_event_body(client_ref="ref-1", status="open"))
    filled = folded_fill_body(fill_id="f-1", client_ref="ref-1", qty="4", price="100")
    fold(state, chain, "fill", filled)
    fold(state, chain, "order_event", order_event_body(client_ref="ref-1", status="unknown"))
    return state, chain, FakeHistory((filled,))


def test_the_book_survives_a_restart_because_nothing_is_stored():
    """"…must stay within caps and survive restart."

    An encumbrance has no id and no store: it is a function of the fold and
    the chain. So the proof is that a series restored from its durable
    `snapshot` payload — a fresh process, a fresh object — derives the SAME
    book at the same instant. A policy holding a reservation in memory would
    come back with an empty one."""
    state, chain, history = scarce_series()
    before = cash(basis="settlement_date").encumber(state.snapshot(), FOLD_AT_MS, history)

    env = snapshot_env(state, chain)
    state.apply(env)
    restarted = SeriesState(SERIES_ID)
    restarted.restore(env)
    restarted.apply(env)

    # A FRESH policy, as a restarted process would build: comparing one
    # object against itself would be satisfied by a policy that ignored its
    # arguments and returned its first answer forever (round-1 Major 2).
    after = cash(basis="settlement_date").encumber(
        restarted.snapshot(), FOLD_AT_MS, history
    )
    assert after == before
    assert after.funds["USD"].committed == Decimal("600")
    assert after.funds["USD"].unsettled == Decimal("400")


def test_the_audit_acceptance_test_for_account_reality():
    """The audit's own words, executed:

        "Give two concurrent leads the same scarce cash or shares while one
        order is partially filled and its remainder uncertain. Aggregate
        exposure and reservations must stay within caps and survive restart.
        Reject unavailable borrow and insufficient settled funds under the
        selected account policy."

    Lead one holds 600 of the 2500 in an order whose remainder is `unknown`,
    and 400 more sits in an unsettled purchase. Lead two may commit what is
    left and no more, a sell beyond the four units actually filled is a
    borrow nobody can locate, and the restart above proves the same figures
    come back."""
    state, chain, history = scarce_series()
    policy = cash(basis="settlement_date")
    fold_view = state.snapshot()

    book = policy.encumber(fold_view, FOLD_AT_MS, history)
    funds = book.funds["USD"]
    assert (funds.total, funds.committed, funds.unsettled, funds.available) == (
        Decimal("2500"),
        Decimal("600"),
        Decimal("400"),
        Decimal("1500"),
    )

    # Lead two, exactly at the cap and then one unit past it — the same
    # scarce cash, judged against what lead one already holds.
    inside = proposal(pid="lead-2", qty="15", limit="100", instrument="AAA")
    beyond = proposal(pid="lead-3", qty="16", limit="100", instrument="AAA")
    assert policy.admit(inside, fold_view, FOLD_AT_MS, history) == ()
    refused = policy.admit(beyond, fold_view, FOLD_AT_MS, history)
    assert refused and "past the settled funds available" in refused[0]

    # The shares are as scarce as the cash: four were filled, and a fifth
    # would have to be borrowed.
    assert book.inventory["AAA"].available == Decimal("4")
    covered = policy.admit(
        proposal(pid="lead-4", side="sell", qty="4", instrument="AAA"),
        fold_view,
        FOLD_AT_MS,
        history,
    )
    naked = policy.admit(
        proposal(pid="lead-5", side="sell", qty="5", instrument="AAA"),
        fold_view,
        FOLD_AT_MS,
        history,
    )
    assert covered == ()
    assert naked and "borrowed" in naked[0]


# ---------------------------------------------------------------------------
# Round-1 Major 1 — the derived figure BINDS, through the guard chain
# ---------------------------------------------------------------------------

#: The two measures, by the reference a document writes. Restated rather
#: than built from `__name__`: a rename must break a document, and a test
#: that spelled the path from the class would hide that.
FUNDS_MEASURE = "dskit.production.encumbrance:SettledFundsShortfall"
UNITS_MEASURE = "dskit.production.encumbrance:UncommittedUnitsShortfall"

#: The params a document writes for each core policy, for the sweeps that
#: run over both.
POLICY_PARAMS = {
    "undeclared": {},
    "cash": {"settlement_lag_ms": LAG_MS, "balance_basis": "trade_date"},
}

WINDOW = Window.from_params({})


def limit_over(measure, name="funds"):
    """A real `Limit` holding `measure` to a shortfall of at most zero."""
    return Limit(
        {"measure": measure, "bound": {"max": "0"}, "on_breach": "refuse"},
        name=name,
    )


def guarded_state(state_view, history=None, basis="trade_date", strategy=None):
    """A `TickState` whose account is a real accounting snapshot of `state_view`."""
    strategy = strategy or accounting(
        policy=selector(basis=basis), history=history if history is not None else FakeHistory()
    )
    marks = quotes_for(*sorted({position.instrument for position in state_view.positions}))
    account = strategy.snapshot(
        state_view, Boom("executor"), marks, T0, (), FakeCalendar()
    )
    return TickState(
        view=state_view,
        account=account,
        feed_status="live",
        feed_ages=(),
        calendar=FakeCalendar(),
    )


def test_both_measures_resolve_from_the_registry_by_the_path_a_document_writes():
    """`MEASURE_KINDS` lives in `guards.py`, which this module imports, so a
    registration performed here would make the registry's contents depend on
    import order. The documented `pkg.module:Class` route is the one a
    document uses, so it is the one pinned."""
    assert MEASURE_KINDS.resolve(FUNDS_MEASURE) is SettledFundsShortfall
    assert MEASURE_KINDS.resolve(UNITS_MEASURE) is UncommittedUnitsShortfall
    assert FUNDS_MEASURE not in MEASURE_KINDS.kinds()
    assert UNITS_MEASURE not in MEASURE_KINDS.kinds()


def test_the_guard_chain_refuses_the_second_lead_that_reaches_past_the_first():
    """Round-1 Major 1, as a regression. Deriving `available` made it visible;
    a `Limit` over it is what makes it BINDING. The first lead's order is in
    the fold by the time the second is measured — every leg re-snapshots — so
    the second is held to what is left, not to the untouched balance."""
    guard = limit_over(FUNDS_MEASURE)
    scarce = view(balances={USD: Decimal("100")})
    first = guard.check(proposal(qty="10", limit="10"), guarded_state(scarce))
    assert first.verdict == "allow"

    after_first = view(
        balances={USD: Decimal("100")},
        working=(order(ref="lead-1", qty="10", limit="10"),),
    )
    second = guard.check(
        proposal(qty="10", limit="10", pid="cand-2"), guarded_state(after_first)
    )
    assert second.verdict == "refuse"
    assert second.value == Decimal("100")


def test_the_funds_guard_admits_a_proposal_that_spends_exactly_what_is_free():
    """`Bound` is inclusive, so a shortfall of exactly zero is not a breach —
    the refusal must bite at one unit more, or it would be unusable."""
    guard = limit_over(FUNDS_MEASURE)
    left = view(balances={USD: Decimal("100")}, working=(order(ref="w", qty="4", limit="10"),))
    exact = guard.check(proposal(qty="6", limit="10"), guarded_state(left))
    over = guard.check(proposal(qty="7", limit="10", pid="cand-2"), guarded_state(left))
    assert (exact.verdict, exact.value) == ("allow", Decimal("0"))
    assert (over.verdict, over.value) == ("refuse", Decimal("10"))


def test_the_units_guard_refuses_a_sell_beyond_the_uncommitted_position():
    """The shares are as scarce as the cash: ten held, six already promised to
    a working sell, so a further six reaches two the account does not have."""
    guard = limit_over(UNITS_MEASURE, name="units")
    committed = view(
        positions=(position(qty="10"),),
        working=(order(ref="lead-1", side="sell", qty="6"),),
    )
    naked = guard.check(proposal(side="sell", qty="6"), guarded_state(committed))
    covered = guard.check(
        proposal(side="sell", qty="4", pid="cand-2"), guarded_state(committed)
    )
    assert (naked.verdict, naked.value) == ("refuse", Decimal("2"))
    assert (covered.verdict, covered.value) == ("allow", Decimal("0"))


def test_a_sell_reaches_no_cash_and_a_buy_reaches_no_held_unit():
    """Each measure answers about ONE resource. A side table decides which
    proposals touch it, so neither guard refuses a proposal it has nothing to
    say about."""
    held = view(positions=(position(qty="10"),), balances={USD: Decimal("1")})
    state = guarded_state(held)
    assert SettledFundsShortfall().value(
        proposal(side="sell", qty="4"), state, WINDOW, "*", True
    ) == Decimal("0")
    assert UncommittedUnitsShortfall().value(
        proposal(side="buy", qty="99", limit="10"), state, WINDOW, "*", True
    ) == Decimal("0")


def test_the_funds_measure_is_only_as_strong_as_the_available_it_reads():
    """Stated rather than hidden: the measure reads `state.account.balances`,
    so under `PaperAccounting` — where `available` IS `total` — it holds a
    proposal to the whole balance and admits the over-commit that
    `EncumberedAccounting` refuses. That is the difference between a figure
    that is reported and one that is derived."""
    guard = limit_over(FUNDS_MEASURE)
    after_first = view(
        balances={USD: Decimal("100")},
        working=(order(ref="lead-1", qty="10", limit="10"),),
    )
    second = proposal(qty="10", limit="10", pid="cand-2")
    assert guard.check(second, guarded_state(after_first, strategy=paper())).verdict == "allow"
    assert guard.check(second, guarded_state(after_first)).verdict == "refuse"


def test_the_measures_refuse_rather_than_guess_what_they_cannot_measure():
    """A `Limit` turns a measure that cannot answer into a refusal with the
    reason recorded, which is the right outcome — but only if the measure
    raises instead of inventing a number."""
    guard = limit_over(FUNDS_MEASURE)
    spanning = guarded_state(view(), strategy=paper())
    market = guard.check(proposal(limit=None), spanning)
    assert market.verdict == "refuse"
    assert market.value is None
    assert "declares no limit" in market.reason


def test_every_resource_the_book_derives_has_something_that_binds_it():
    """The completeness half of round-1 Major 1: a figure nothing reads is
    computable, not enforced. `funds` and `inventory` are the two resources
    the book derives and each has a `Measure` a `Limit` holds it to.
    `unsized_refs` deliberately has none — a `Measure` reads
    `state.account` and never `state.view` (§5.8.1), and the pending client
    refs live on the view, so that refusal stays `admit`'s."""
    assert {field.name for field in dataclasses.fields(Encumbrance)} == {
        "funds",
        "inventory",
        "unsized_refs",
    }
    assert MEASURE_KINDS.resolve(FUNDS_MEASURE).kind == "settled_funds_shortfall"
    assert MEASURE_KINDS.resolve(UNITS_MEASURE).kind == "uncommitted_units_shortfall"


def test_the_guard_chain_and_admit_never_disagree_about_one_proposal():
    """The two entry points share the shortfall rules, so a proposal the guard
    refuses is one `admit` refuses and the other way round. Two copies of the
    comparison would drift the first time one was tuned."""
    guard, units = limit_over(FUNDS_MEASURE), limit_over(UNITS_MEASURE, name="units")
    policy = cash()
    quiet = FakeHistory()
    # 1000 total, 40 held by a working buy and 500 sitting in an unsettled
    # sale, leaves 460. A 500 proposal fits the TOTAL and not what is free, so
    # this is the row where reading the wrong field flips the verdict —
    # round 2 found every earlier row had `committed == unsettled == 0`, which
    # made the two fields indistinguishable and the claim unobservable.
    encumbered = view(
        balances={USD: Decimal("1000")}, working=(order(ref="w-1", qty="4", limit="10"),)
    )
    selling = FakeHistory((fill_body("f-1", "sell", "50", "10", T0 - MINUTE),))
    # Ten held with six promised to a working sell leaves four: a sell of five
    # is covered by the POSITION and not by what is uncommitted, which is the
    # same trap one resource over.
    promised = view(
        positions=(position(qty="10"),),
        working=(order(ref="s-1", side="sell", qty="6"),),
    )
    cases = (
        (view(balances={USD: Decimal("100")}), proposal(qty="10", limit="10"), quiet),
        (view(balances={USD: Decimal("99")}), proposal(qty="10", limit="10"), quiet),
        (encumbered, proposal(qty="50", limit="10"), selling),
        (encumbered, proposal(qty="46", limit="10"), selling),
        (view(positions=(position(qty="3"),)), proposal(side="sell", qty="4"), quiet),
        (view(positions=(position(qty="10"),)), proposal(side="sell", qty="4"), quiet),
        (promised, proposal(side="sell", qty="5"), quiet),
        (promised, proposal(side="sell", qty="4"), quiet),
    )
    for state_view, candidate, history in cases:
        refused_by_admit = bool(policy.admit(candidate, state_view, T0, history))
        state = guarded_state(state_view, history=history)
        verdicts = {
            guard.check(candidate, state).verdict,
            units.check(candidate, state).verdict,
        }
        assert refused_by_admit == ("refuse" in verdicts), (state_view, candidate.id)


# ---------------------------------------------------------------------------
# Round-1 Major 2 — the "nothing is stored" invariant, actually pinned
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CORE_POLICIES)
def test_one_policy_instance_answers_from_its_arguments_not_from_memory(name):
    """`compose.py` builds the accounting object — and therefore its policy —
    ONCE per serve process, not once per tick. A policy that remembered an
    answer would freeze `available` at the first tick's value for the process
    lifetime and silently permit unlimited over-commitment afterwards. So the
    same instance must answer a DIFFERENT fold differently, and must come back
    to the first answer when handed the first fold again."""
    policy = ENCUMBRANCE_POLICIES[name](POLICY_PARAMS[name])
    empty = view()
    busy = view(working=(order(qty="4", limit="10"),), balances={USD: Decimal("500")})

    first = policy.encumber(empty, T0, FakeHistory())
    second = policy.encumber(busy, T0, FakeHistory())
    assert second != first
    assert second.funds[USD].total == Decimal("500")
    assert policy.encumber(empty, T0, FakeHistory()) == first


@pytest.mark.parametrize("name", CORE_POLICIES)
def test_two_independent_instances_of_one_policy_agree(name):
    """The other half: statelessness is worth nothing if two instances built
    from the same params disagree."""
    busy = view(working=(order(qty="4", limit="10"),))
    left = ENCUMBRANCE_POLICIES[name](POLICY_PARAMS[name])
    right = ENCUMBRANCE_POLICIES[name](POLICY_PARAMS[name])
    assert left.encumber(busy, T0, FakeHistory()) == right.encumber(busy, T0, FakeHistory())


def test_one_instance_settles_what_was_pending_when_asked_at_a_later_instant():
    """Time is an argument, not a property of the policy. A memoised answer
    would keep reporting yesterday's unsettled proceeds forever."""
    policy = cash()
    history = FakeHistory((fill_body("f-1", "sell", "10", "10", T0),))
    early = policy.encumber(view(), T0 + HOUR, history)
    late = policy.encumber(view(), T0 + LAG_MS, history)
    assert early.funds[USD].unsettled == Decimal("100")
    assert late.funds[USD].unsettled == Decimal("0")


def test_one_instance_follows_a_moving_history():
    """The third argument, pinned the same way: the fill history moves under a
    serve process, and the book has to move with it."""
    policy = cash()
    quiet = policy.encumber(view(), T0, FakeHistory())
    noisy = policy.encumber(
        view(), T0, FakeHistory((fill_body("f-1", "sell", "10", "10", T0 - MINUTE),))
    )
    assert quiet.funds[USD].unsettled == Decimal("0")
    assert noisy.funds[USD].unsettled == Decimal("100")


def test_balances_and_admit_are_stateless_on_one_instance_too():
    """`encumber` is not the only public answer. Both of the methods a caller
    actually uses run through it, so both are pinned on one instance in an
    order where a remembered answer would pass the second case wrongly."""
    policy = cash()
    rich = view(balances={USD: Decimal("1000")})
    poor = view(balances={USD: Decimal("10")})
    wanted = proposal(qty="10", limit="10")
    assert policy.admit(wanted, rich, T0, FakeHistory()) == ()
    assert policy.admit(wanted, poor, T0, FakeHistory())
    assert only(policy.balances(rich, T0, FakeHistory())).available == Decimal("1000")
    assert only(policy.balances(poor, T0, FakeHistory())).available == Decimal("10")


def test_one_measure_instance_answers_from_the_state_it_is_handed():
    """A `Limit` builds its measure once, at configuration, and reuses it for
    every proposal of every tick — the same freezing hazard one layer up, and
    both measures carry it."""
    funds = SettledFundsShortfall()
    rich = guarded_state(view(balances={USD: Decimal("1000")}))
    poor = guarded_state(view(balances={USD: Decimal("10")}))
    wanted = proposal(qty="10", limit="10")
    assert funds.value(wanted, rich, WINDOW, "*", True) == Decimal("-900")
    assert funds.value(wanted, poor, WINDOW, "*", True) == Decimal("90")
    assert funds.value(wanted, rich, WINDOW, "*", True) == Decimal("-900")

    units = UncommittedUnitsShortfall()
    deep = guarded_state(view(positions=(position(qty="10"),)))
    thin = guarded_state(view(positions=(position(qty="1"),)))
    sell = proposal(side="sell", qty="4")
    assert units.value(sell, deep, WINDOW, "*", True) == Decimal("-6")
    assert units.value(sell, thin, WINDOW, "*", True) == Decimal("3")
    assert units.value(sell, deep, WINDOW, "*", True) == Decimal("-6")


@pytest.mark.parametrize("name", CORE_POLICIES)
def test_a_policy_grows_no_attribute_across_a_call(name):
    """The mechanical half of the same invariant, and the one that does not
    depend on this suite having picked a fold where a remembered answer would
    be visibly wrong: a policy that stored ANYTHING across a call is caught
    here, whatever it stored and whatever it did with it."""
    policy = ENCUMBRANCE_POLICIES[name](POLICY_PARAMS[name])
    before = dict(vars(policy))
    policy.encumber(view(working=(order(),), positions=(position(),)), T0, FakeHistory())
    policy.balances(view(), T0, FakeHistory())
    assert vars(policy) == before


@pytest.mark.parametrize(
    "measure_cls", (SettledFundsShortfall, UncommittedUnitsShortfall),
    ids=lambda cls: cls.kind,
)
def test_a_measure_grows_no_attribute_across_a_call(measure_cls):
    """And the same for the two measures, which a `Limit` holds for the life of
    the process."""
    measure = measure_cls()
    before = dict(vars(measure))
    state = guarded_state(view(positions=(position(qty="10"),)))
    measure.value(proposal(qty="1", limit="1"), state, WINDOW, "*", True)
    measure.value(proposal(side="sell", qty="1"), state, WINDOW, "*", True)
    assert vars(measure) == before


def test_one_accounting_instance_answers_from_the_fold_it_is_handed():
    """And one layer up again: `compose.py` builds ONE
    `EncumberedAccounting` per process, so its snapshots must move too."""
    strategy = accounting()
    empty = only(
        strategy.snapshot(view(), Boom("executor"), NO_QUOTES, T0, (), FakeCalendar()).balances
    )
    busy = only(
        strategy.snapshot(
            view(working=(order(qty="4", limit="10"),)),
            Boom("executor"),
            NO_QUOTES,
            T0,
            (),
            FakeCalendar(),
        ).balances
    )
    assert empty.available == Decimal("1000")
    assert busy.available == Decimal("960")


# ---------------------------------------------------------------------------
# Round-1 Minor 4 and Nit 5
# ---------------------------------------------------------------------------


def test_a_lag_wider_than_the_clocks_reading_still_asks_for_a_real_instant():
    """`at_ms - settlement_lag_ms` underflows early in a `TestClock`-driven
    run, whose default start is 0. The collaborator refuses a negative bound,
    and letting its refusal escape would surface an encumbrance bug as
    somebody else's error."""
    history = FakeHistory()
    policy = CashSettlement({"settlement_lag_ms": 50_000, "balance_basis": "trade_date"})
    policy.encumber(view(), 1_000, history)
    assert history.calls == [0]


def test_the_history_double_refuses_a_negative_bound_like_the_real_one():
    """The gate above is worth what the double catches: a fake that accepted a
    negative `since_ms` is why round 1 shipped the underflow."""
    with pytest.raises(ProductionError, match="since_ms must be an int"):
        FakeHistory().fills(-1)


def test_an_over_committed_account_reports_a_negative_available_and_is_read_conservatively():
    """No floor. An account that has committed more than it holds is a fact an
    operator has to see, and flooring at zero would report it as merely empty.
    What matters is that every reader stays conservative under it — so both
    the admission check and the guard measure are asserted on the same book."""
    history = FakeHistory((fill_body("f-1", "sell", "50", "10", T0 - MINUTE),))
    over = view(balances={USD: Decimal("100")})
    row = cash().encumber(over, T0, history).funds[USD]
    assert (row.total, row.unsettled, row.available) == (
        Decimal("100"),
        Decimal("500"),
        Decimal("-400"),
    )
    tiny = proposal(qty="1", limit="1")
    assert cash().admit(tiny, over, T0, history)
    guard = limit_over(FUNDS_MEASURE)
    assert guard.check(tiny, guarded_state(over, history=history)).verdict == "refuse"


# ---------------------------------------------------------------------------
# Round-2 Major — statelessness pinned BEHAVIOURALLY, not structurally
# ---------------------------------------------------------------------------


def moving_view(balances=None, working=None, positions=(), pending=()):
    """A real `StateView` whose fold can move without its identity changing.

    Faithful in TYPE: `balances` and `working` come back as
    `MappingProxyType`s and `positions`/`pending` as TUPLES, exactly the
    shape `SeriesState.snapshot` yields and `test_state.py
    ::test_state_view_members_are_immutable_containers` pins. A harness that
    claimed to be a real view while handing out bare lists would be
    exercising a `StateView` that cannot occur.

    Faithful in HAZARD: the two proxies are live over the dicts the caller
    passed, and :func:`move_view` rebinds the two tuple members on the SAME
    object. `SeriesState.snapshot` wraps copies, so a view taken from the
    real fold never moves — but `id()` is reused the moment one is
    collected, and a cache keyed on an identity then hands the next tick the
    previous tick's answer. This is how that is said deterministically.
    """
    balances = {USD: Decimal("1000")} if balances is None else balances
    working = {} if working is None else working
    return StateView(
        positions=tuple(positions),
        working=MappingProxyType(working),
        pending=tuple(pending),
        balances=MappingProxyType(balances),
        decision_history=(),
        breaker="active",
        arming=None,
        readiness=None,
        guard_holds=MappingProxyType({}),
        reduction=None,
        pending_control=MappingProxyType({}),
        risk_version=records.RiskVersion(
            economic_seq=41, executor_token=None, accounting_tokens=None
        ),
        head_seq=41,
        head_hash="a" * 64,
    )


def move_view(state_view, positions=None, pending=None, economic=True):
    """Rebind a view's TUPLE members: the same object, a later fold.

    The mapping members move by mutating the dicts their proxies were built
    over; these two cannot, because a tuple is a tuple. Rebinding them
    through `object.__setattr__` keeps both the declared type and the
    object's identity, which is the pair the hazard needs.

    `economic` says whether the record the real fold took was an ECONOMIC one;
    either way the head moves, because the real fold moves it for every record
    it takes. See :func:`advance`.
    """
    if positions is not None:
        object.__setattr__(state_view, "positions", tuple(positions))
    if pending is not None:
        object.__setattr__(state_view, "pending", tuple(pending))
    return advance(state_view, economic=economic)


def advance(state_view, by=1, economic=True):
    """Advance the fold's version counters on the SAME view object, as the fold does.

    `SeriesState._fold` moves `head_seq` and `head_hash` UNCONDITIONALLY for
    every record it takes, and moves `economic_seq` only when that record is
    an economic one — §5.8.1: `order_event`, `fill` and `cash_flow` are, an
    `intent` is pending rather than economic. Round-5 review found this
    harness freezing ALL THREE for a non-economic move, which is a combination
    no real fold produces: a test asserting against it asserts against a state
    that cannot occur. `test_the_harness_moves_the_counters_the_way_the_real_fold_does`
    pins this against a real `SeriesState`, folding a real record of each kind,
    rather than restating §5.8.1 here.
    """
    head_seq = state_view.head_seq + by
    object.__setattr__(state_view, "head_seq", head_seq)
    object.__setattr__(state_view, "head_hash", f"{head_seq:064x}")
    if economic:
        object.__setattr__(
            state_view,
            "risk_version",
            records.RiskVersion(
                economic_seq=state_view.risk_version.economic_seq + by,
                executor_token=None,
                accounting_tokens=None,
            ),
        )
    return state_view


def move_account(account, balances=None, positions=None):
    """Rebind an `AccountState`'s members on the SAME object.

    `MovingTickState` swaps a whole account, which changes
    `id(state.account)` — so a cache keyed on THAT identity would invalidate
    correctly and never go stale. This holds it fixed too.
    """
    if balances is not None:
        object.__setattr__(account, "balances", tuple(balances))
    if positions is not None:
        object.__setattr__(account, "positions", tuple(positions))
    return account


class MovingTickState:
    """One `state` identity whose account changes underneath.

    `Measure.value` reads `state.account` and nothing else — §5.8.1 bars it
    from `state.view` — so this is a faithful stand-in, and it is the only way
    to say "the same address now carries a different account", which is what
    `id()` reuse does to a cache between two ticks.
    """

    def __init__(self, account):
        self.account = account


def test_the_harness_moves_the_counters_the_way_the_real_fold_does():
    """The harness's `advance` must be faithful, or every test above it is.

    Round-5 review: `advance` moved `head_seq`, `head_hash` AND `economic_seq`
    together, and `move_view(..., economic=False)` moved NONE of them. The real
    fold has no such state — `SeriesState._fold` assigns the head for EVERY
    record it takes and increments `economic_seq` only for an economic one — so
    a "non-economic move" with a frozen head is a combination no fold produces,
    and a cache keyed on `head_seq` alone was being credited with a miss it
    would never have.

    Measured against a real `SeriesState` folding real records, never by
    restating §5.8.1 here: a rule read back from its own subject asserts
    nothing, but a rule OBSERVED on the subject is what the harness must match.
    """
    state = SeriesState(SERIES_ID)
    chain = Chain()
    fold(state, chain, "tick_start", {"tick_id": "T1", "tick_at_ms": FOLD_BASE_MS})

    def counters():
        view = state.snapshot()
        return view.head_seq, view.head_hash, view.risk_version.economic_seq

    # A non-economic record: the head moves, the economic counter does not.
    before = counters()
    fold(state, chain, "intent", intent_body())
    after_intent = counters()
    assert after_intent[0] != before[0] and after_intent[1] != before[1]
    assert after_intent[2] == before[2]

    # An economic record: all three move.
    fold(state, chain, "order_event", order_event_body())
    after_event = counters()
    assert after_event[0] != after_intent[0] and after_event[1] != after_intent[1]
    assert after_event[2] != after_intent[2]

    # The harness reproduces BOTH relationships on its own view object.
    harness = moving_view()
    start = (harness.head_seq, harness.head_hash, harness.risk_version.economic_seq)
    advance(harness, economic=False)
    noneconomic = (harness.head_seq, harness.head_hash, harness.risk_version.economic_seq)
    assert noneconomic[0] != start[0] and noneconomic[1] != start[1]
    assert noneconomic[2] == start[2]
    advance(harness, economic=True)
    economic = (harness.head_seq, harness.head_hash, harness.risk_version.economic_seq)
    assert economic[0] != noneconomic[0] and economic[1] != noneconomic[1]
    assert economic[2] != noneconomic[2]


@pytest.mark.parametrize("name", CORE_POLICIES)
def test_the_book_tracks_a_fold_that_moves_under_one_held_identity(name):
    """Round-2 Major, as a regression.

    `vars(instance)` pins catch caching ON the object and nothing else: a
    module-level dict keyed `(id(self), at_ms, id(state_view), id(history))`
    survived every statelessness test this file had. So the pin has to be
    behavioural. Here the policy, the view, the history AND the instant all
    keep their identities while the fold moves underneath — every component
    of that key is unchanged — so any identity-keyed cache returns the first
    answer and fails.
    """
    policy = ENCUMBRANCE_POLICIES[name](POLICY_PARAMS[name])
    balances, working = {USD: Decimal("1000")}, {}
    state_view = moving_view(balances=balances, working=working)
    history = FakeHistory()

    empty = policy.encumber(state_view, T0, history)
    assert empty.funds[USD].total == Decimal("1000")
    assert dict(empty.inventory) == {}

    move_view(state_view, positions=(position(qty="10"),))
    buy = order(ref="w-1", qty="4", limit="10")
    working[buy.client_ref] = buy
    balances[USD] = Decimal("900")
    advance(state_view)

    moved = policy.encumber(state_view, T0, history)
    assert moved.funds[USD].total == Decimal("900")
    assert moved.inventory[INS1].held == Decimal("10")


def test_the_committed_and_unsettled_figures_track_a_moving_fold_and_history():
    """The two figures only `CashSettlement` derives, under the same held
    identities — including a history object that reports new fills without
    becoming a new object."""
    policy = cash()
    balances, working = {USD: Decimal("1000")}, {}
    state_view = moving_view(balances=balances, working=working)
    history = FakeHistory()

    before = policy.encumber(state_view, T0, history)
    buy = order(ref="w-1", qty="4", limit="10")
    working[buy.client_ref] = buy
    history.set_fills((fill_body("f-1", "sell", "10", "10", T0 - MINUTE),))
    advance(state_view, by=2)
    after = policy.encumber(state_view, T0, history)

    assert (before.funds[USD].committed, before.funds[USD].unsettled) == (
        Decimal("0"),
        Decimal("0"),
    )
    assert (after.funds[USD].committed, after.funds[USD].unsettled) == (
        Decimal("40"),
        Decimal("100"),
    )


def test_an_unsized_intent_appearing_under_one_identity_is_seen():
    """`unsized_refs` is read from the same view, so it carries the same
    hazard: a cached book would keep admitting after an intent landed."""
    policy = cash()
    state_view = moving_view()
    history = FakeHistory()
    assert policy.admit(proposal(qty="1", limit="1"), state_view, T0, history) == ()
    # NOT economic: §5.8.1 keeps `economic_seq` still for an intent, which is
    # exactly when a cache keyed on the counters goes stale.
    move_view(state_view, pending=("ref-9",), economic=False)
    problems = policy.admit(proposal(qty="1", limit="1"), state_view, T0, history)
    assert problems and "ref-9" in problems[0]


def test_admit_and_balances_track_a_fold_that_moves_under_one_held_identity():
    """The two methods a caller actually uses, at fixed identity."""
    policy = cash()
    balances, working = {USD: Decimal("100")}, {}
    state_view = moving_view(balances=balances, working=working)
    history = FakeHistory()
    wanted = proposal(qty="10", limit="10")

    assert policy.admit(wanted, state_view, T0, history) == ()
    assert only(policy.balances(state_view, T0, history)).available == Decimal("100")

    lead = order(ref="lead-1", qty="10", limit="10")
    working[lead.client_ref] = lead
    advance(state_view)

    smaller = proposal(qty="5", limit="10", pid="cand-2")
    assert policy.admit(wanted, state_view, T0, history)
    assert policy.admit(smaller, state_view, T0, history)
    assert only(policy.balances(state_view, T0, history)).available == Decimal("0")

    # And once more moving in VALUE only — no container changes length or
    # keys, and the order keeps its own identity — so a shape digest cannot
    # invalidate either (round-3 Major). The SAME proposal that was refused
    # a line ago is admitted now, which no stale answer can reproduce.
    object.__setattr__(lead, "limit", Decimal("5"))
    advance(state_view)
    assert only(policy.balances(state_view, T0, history)).available == Decimal("50")
    assert policy.admit(smaller, state_view, T0, history) == ()


def test_the_snapshot_tracks_a_fold_that_moves_under_one_held_identity():
    """`compose.py` builds ONE `EncumberedAccounting` per serve PROCESS, so
    the same freezing hazard reaches the `AccountState` a permit binds."""
    history = FakeHistory()
    strategy = accounting(history=history)
    balances, working = {USD: Decimal("1000")}, {}
    state_view = moving_view(balances=balances, working=working)

    first = only(
        strategy.snapshot(
            state_view, Boom("executor"), NO_QUOTES, T0, (), FakeCalendar()
        ).balances
    )
    buy = order(ref="w-1", qty="4", limit="10")
    working[buy.client_ref] = buy
    advance(state_view)
    second = only(
        strategy.snapshot(
            state_view, Boom("executor"), NO_QUOTES, T0, (), FakeCalendar()
        ).balances
    )
    balances[USD] = Decimal("500")
    advance(state_view)
    third = only(
        strategy.snapshot(
            state_view, Boom("executor"), NO_QUOTES, T0, (), FakeCalendar()
        ).balances
    )
    assert first.available == Decimal("1000")
    assert second.available == Decimal("960")
    # The third move changes no container's length, keys or object identity.
    assert (third.total, third.available) == (Decimal("500"), Decimal("460"))


def test_each_measure_tracks_an_account_that_moves_under_one_held_identity():
    """A `Limit` builds its measure once and holds it for the process, and the
    `state` it is handed is an ordinary object whose address is reused."""
    funds, units = SettledFundsShortfall(), UncommittedUnitsShortfall()
    wanted = proposal(qty="10", limit="10")
    sell = proposal(side="sell", qty="4")

    state = MovingTickState(guarded_state(view(balances={USD: Decimal("1000")})).account)
    assert funds.value(wanted, state, WINDOW, "*", True) == Decimal("-900")
    # First the whole account is swapped, then the SAME account is moved in
    # place — so neither `id(state)` nor `id(state.account)` can serve as a
    # key that never goes stale.
    state.account = guarded_state(view(balances={USD: Decimal("10")})).account
    assert funds.value(wanted, state, WINDOW, "*", True) == Decimal("90")
    move_account(
        state.account, balances=guarded_state(view(balances={USD: Decimal("500")})).account.balances
    )
    assert funds.value(wanted, state, WINDOW, "*", True) == Decimal("-400")

    stock = MovingTickState(guarded_state(view(positions=(position(qty="10"),))).account)
    assert units.value(sell, stock, WINDOW, "*", True) == Decimal("-6")
    stock.account = guarded_state(view(positions=(position(qty="1"),))).account
    assert units.value(sell, stock, WINDOW, "*", True) == Decimal("3")
    move_account(stock.account, positions=(position(qty="6"),))
    assert units.value(sell, stock, WINDOW, "*", True) == Decimal("-2")


# ---------------------------------------------------------------------------
# Round-2 family sweep — claims that could only be observed passing
# ---------------------------------------------------------------------------


def test_the_balance_a_caller_reads_is_the_row_the_book_derived():
    """`available = total - committed - unsettled` is claimed to have ONE
    owner. A second subtraction inside `balances` would be invisible while
    `committed` and `unsettled` are both zero, so the agreement is asserted on
    a fold where both are nonzero and the three numbers are all distinct."""
    history = FakeHistory((fill_body("f-1", "sell", "50", "10", T0 - MINUTE),))
    encumbered = view(
        balances={USD: Decimal("1000")}, working=(order(ref="w-1", qty="4", limit="10"),)
    )
    row = cash().encumber(encumbered, T0, history).funds[USD]
    balance = only(cash().balances(encumbered, T0, history))
    assert (row.total, row.committed, row.unsettled, row.available) == (
        Decimal("1000"),
        Decimal("40"),
        Decimal("500"),
        Decimal("460"),
    )
    assert (balance.total, balance.available) == (row.total, row.available)


@pytest.mark.parametrize("measure", (FUNDS_MEASURE, UNITS_MEASURE))
def test_neither_measure_may_be_amended(measure):
    """Both are documented as not scalable — shrinking an order to fit the
    cash is an execution decision this package has no mandate to invent. That
    claim was only observable by reading the class attribute, so it is
    asserted where it bites: `Limit` refuses `on_breach: amend` for a measure
    that cannot be reduced."""
    with pytest.raises(ProductionError, match="needs a scalable measure"):
        Limit(
            {"measure": measure, "bound": {"max": "0"}, "on_breach": "amend"},
            name="amending",
        )


def test_the_history_double_accepts_an_integral_float_like_the_real_one():
    """Round-2 Minor 2. `check_int_param` coerces an integral float before
    range-checking, so `LedgerHistory` accepts `470.0`; a double that refused
    it would declare impossible a call the real collaborator would take."""
    assert FakeHistory().fills(470.0) == ()
    for refused in (470.5, True, -1, -1.0):
        with pytest.raises(ProductionError, match="since_ms must be an int"):
            FakeHistory().fills(refused)


#: What each core policy's `available` reads before and after the value-only
#: move below — 1000 with a 4-at-10 buy outstanding, then 999 with the same
#: order at a limit of 20. Both numbers move for both policies, so neither
#: half of the pair can hold while a cached answer is being returned. (An
#: assertion on `committed` would be decorative for the null object, which
#: answers zero unconditionally.)
VALUE_ONLY_AVAILABLE = {
    "undeclared": (Decimal("1000"), Decimal("999")),
    "cash": (Decimal("960"), Decimal("919")),
}


@pytest.mark.parametrize("name", CORE_POLICIES)
def test_the_book_tracks_a_fold_that_moves_in_value_only(name):
    """Round-3 Major, as a regression.

    The rest of this family moves a fold by ADDING to it, so every container
    changes length — and a cache keyed on a shape digest
    (`(id(self), at_ms, id(history), len(balances), len(working), ...)`)
    invalidates correctly and never returns a stale answer. Seven of these
    tests passed with exactly that bug present.

    The condition they cannot express is a fold that moves in VALUE only.
    Here nothing a key could be built from moves except the values: the
    policy, the view, the history and the instant keep their identities;
    every container keeps its object, its length AND its key set; and the
    working order keeps its own identity too, mutated in place the way `id()`
    reuse presents a different order at one address. A key over the actual
    VALUES is the only one left, and a key over the actual values is the one
    kind that cannot go stale.
    """
    policy = ENCUMBRANCE_POLICIES[name](POLICY_PARAMS[name])
    buy = order(ref="w-1", qty="4", limit="10")
    balances, working = {USD: Decimal("1000")}, {buy.client_ref: buy}
    state_view = moving_view(balances=balances, working=working)
    history = FakeHistory()

    before = policy.encumber(state_view, T0, history)
    shape = (len(before.funds), set(before.funds), len(state_view.working))

    # `OrderState` is frozen, which is what makes this the right way to say
    # "the same address now holds a different order".
    object.__setattr__(buy, "limit", Decimal("20"))
    balances[USD] = Decimal("999")
    advance(state_view)

    after = policy.encumber(state_view, T0, history)

    assert (len(after.funds), set(after.funds), len(state_view.working)) == shape
    assert (before.funds[USD].total, after.funds[USD].total) == (
        Decimal("1000"),
        Decimal("999"),
    )
    assert (before.funds[USD].available, after.funds[USD].available) == (
        VALUE_ONLY_AVAILABLE[name]
    )


def test_the_book_tracks_a_history_whose_CONTENT_moves_under_one_held_identity():
    """Round-4 Major (a), as a regression.

    `encumber` takes THREE arguments and this family only ever moved one of
    them on its own. A cache thorough on the fold and identity-only on the
    history — `(id(self), at_ms, id(history), <content hash of the view>)` —
    passed all ten moving tests, because the single test that moved history
    content always moved a container shape along with it.

    Here the view is untouched, the instant is untouched, and the history
    keeps its identity while its CONTENT moves. A stale answer disagrees with
    the truth by exactly the size of an unsettled fill, which is the
    over-commitment this whole decision exists to prevent.
    """
    policy = cash()
    state_view = moving_view()
    history = FakeHistory()

    quiet = policy.encumber(state_view, T0, history)
    history.set_fills((fill_body("f-1", "sell", "10", "10", T0 - MINUTE),))
    noisy = policy.encumber(state_view, T0, history)

    assert (quiet.funds[USD].unsettled, quiet.funds[USD].available) == (
        Decimal("0"),
        Decimal("1000"),
    )
    assert (noisy.funds[USD].unsettled, noisy.funds[USD].available) == (
        Decimal("100"),
        Decimal("900"),
    )


def test_a_history_that_moves_without_changing_its_fill_count_is_seen():
    """A fill COUNT is the obvious half-fix to the test above, so the axis is
    pinned with the count held at one: the same fill id, at the same instant,
    for twice the size."""
    policy = cash()
    state_view = moving_view()
    history = FakeHistory((fill_body("f-1", "sell", "10", "10", T0 - MINUTE),))

    small = policy.encumber(state_view, T0, history)
    history.set_fills((fill_body("f-1", "sell", "20", "10", T0 - MINUTE),))
    large = policy.encumber(state_view, T0, history)

    assert len(history.fills(T0 - LAG_MS)) == 1
    assert (small.funds[USD].unsettled, large.funds[USD].unsettled) == (
        Decimal("100"),
        Decimal("200"),
    )


def test_a_held_position_that_moves_in_value_only_is_seen():
    """The same, one resource over: the units a sell may still commit move
    without the positions tuple changing length or the fold gaining a row."""
    policy = cash()
    state_view = moving_view(positions=(position(qty="10"),))
    history = FakeHistory()
    deep = policy.encumber(state_view, T0, history)
    move_view(state_view, positions=(position(qty="2"),))
    thin = policy.encumber(state_view, T0, history)
    assert len(thin.inventory) == len(deep.inventory)
    assert (deep.inventory[INS1].available, thin.inventory[INS1].available) == (
        Decimal("10"),
        Decimal("2"),
    )


# ---------------------------------------------------------------------------
# Round-5 Major — enumerate the INPUTS, not the defences
#
# Five rounds wrote one hand-written statelessness axis per layer, and each
# closed one layer and left the next exposed. Round 5 found the next one: a
# cache on `EncumberedAccounting.admit` keyed on `(id(self), id(proposal),
# id(state_view), at_ms)` — every argument the method DECLARES — passed all
# 6617 production tests, because `history` is reached through `self._history`
# and appears in no signature. `loop.py` and `leg.py` reach the accounting object through
# `.snapshot()`, which calls `_balances` -- NOT through `.admit()`, which this
# comment used to claim and which has no caller in dskit at all (round-6
# review). That makes `_balances` the live path, not the side one.
#
# So the axis list is the wrong artefact. What follows enumerates the INPUT
# SPACE instead: every component the derived book's answer depends on, moved
# ONE AT A TIME through the caller-facing entry points, with every object
# identity held fixed. A cache keyed on any proper subset of these fails at
# least one case, whichever subset an implementer picks, without anyone having
# written that cache down. `test_the_dependency_table_names_every_field_read`
# then refuses a record field that is neither in the table nor declared unread,
# so a field added to `Fill`, `OrderState`, `Position` or `Proposal` cannot
# enter the money path without a row here.
# ---------------------------------------------------------------------------

#: The settlement lag every rig below declares.
GATE_LAG = LAG_MS

#: The instant every rig is judged at, and a fill stamped ONE MILLISECOND
#: short of settled at it: `_unsettled` skips a fill once
#: `ts_ms + lag <= at_ms`, so this fill counts and one millisecond earlier
#: does not. That boundary is what makes `ts_ms` load-bearing, and a content
#: key over every other fill field passed the whole suite without it.
GATE_AT_MS = T0
GATE_FILL_TS = GATE_AT_MS - GATE_LAG + 1


def _gate_history(**overrides):
    """A history whose single unsettled sell can be moved field by field."""
    body = dict(
        fill_id="f-1", side="sell", qty="10", price="10",
        ts_ms=GATE_FILL_TS, fee="0", instrument=INS1, status="final",
    )
    body.update(overrides)
    return FakeHistory((fill_body(**body),))


def _gate_accounting(history):
    """A real `EncumberedAccounting` — the object `loop.py` and `leg.py` hold.

    Its `history` is a constructor collaborator reached through
    `self._history`, which is the whole point: the three entry points below
    take it in no signature.
    """
    return EncumberedAccounting(
        {"encumbrance": {"uses": "cash", "params": {
            "settlement_lag_ms": GATE_LAG, "balance_basis": "trade_date"}}},
        clock=FakeClock(GATE_AT_MS),
        history=history,
        max_valuation_age_ms=MAX_VALUATION_AGE_MS,
    )


def _gate_view(balances, working, positions=(), pending=()):
    """A view whose mapping members stay LIVE over the dicts handed in."""
    return moving_view(
        balances=balances, working=working, positions=positions, pending=pending
    )


class Rig:
    """One scenario, and the caller-facing reads taken against it.

    Every mutable the moves reach — the balances dict, the working dict, the
    view, the history, the accounting object — is held on this rig, so a move
    changes CONTENT while every identity stays exactly where it was. The
    identities are asserted unchanged after the move.
    """

    def __init__(self, positions=(), pending=(), working_order=None, **fill):
        self.balances = {USD: Decimal("1000")}
        self.orders = [order(ref="w-1", side="buy", qty="10", limit="10")
                       if working_order is None else working_order]
        self.working = {o.client_ref: o for o in self.orders}
        self.history = _gate_history(**fill)
        self.view = _gate_view(self.balances, self.working, positions, pending)
        self.accounting = _gate_accounting(self.history)
        self.at_ms = GATE_AT_MS
        self.proposal = proposal(side="buy", qty="80", limit="10")

    def identities(self):
        """Every object a move must NOT replace, including the order records.

        Round-6 review, Nit: the order objects were absent, so a future row
        using ``dataclasses.replace`` instead of ``_set`` would swap the record
        and the identity guard would not notice — which is precisely the
        identity-keyed-cache miss the guard exists to rule out.
        """
        return (id(self.accounting), id(self.view), id(self.history),
                id(self.proposal), id(self.balances), id(self.working),
                tuple(id(o) for o in self.orders))

    # --- the three caller-facing entry points -----------------------------
    def available(self):
        return self.accounting.encumbrance(self.view, self.at_ms).funds[USD].available

    def inventory(self):
        book = self.accounting.encumbrance(self.view, self.at_ms)
        return tuple(sorted((k, str(v.available)) for k, v in book.inventory.items()))

    def unsized(self):
        return self.accounting.encumbrance(self.view, self.at_ms).unsized_refs

    def admits(self):
        return self.accounting.admit(self.proposal, self.view, self.at_ms)

    def reported_available(self):
        """`_balances` as `PaperAccounting.snapshot` reaches it."""
        return tuple(
            (b.currency, str(b.available))
            for b in self.accounting._balances(self.view, self.at_ms)
        )


def _set(record, **fields):
    """Rebind fields on a frozen record IN PLACE — same object, new content."""
    for name, value in fields.items():
        object.__setattr__(record, name, value)
    return record


def _two_instrument_rig():
    """A rig holding two instruments live at once, with hand-derived answers.

    Round-6 review, Major: every row ran against ONE position and ONE working
    order, so ``_held_units``/``_committed_units`` could drop their
    ``instrument ==`` filter and sum across everything — and all 6776 tests
    still passed. With 0 or 1 live positions "sum filtered by instrument" and
    "sum everything" are indistinguishable BY CONSTRUCTION.

    The deeper point, which is the one worth keeping: ``before != after``
    proves the answer was SENSED, not that it was COMPUTED CORRECTLY. An
    instrument-blind sum still moves when an instrument changes, so the row's
    own assertion was satisfied by the WRONG new answer. Correctness needs the
    value, so the rows below carry one.
    """
    rig = Rig(positions=(position(instrument=INS1, qty="10"),
                         position(instrument=INS2, qty="4")))
    rig.orders.append(order(ref="w-2", side="sell", qty="3", limit="10",
                            instrument=INS2))
    rig.working[rig.orders[-1].client_ref] = rig.orders[-1]
    return rig


def test_the_units_book_is_scoped_to_its_instrument():
    """Held and committed units are per-instrument, with the numbers written out.

    Derived by hand, not read back from the code: INS1 holds 10 units and its
    only working order is a BUY, which commits no units, so 10 are available.
    INS2 holds 4 and carries a working SELL of 3, so 1 is available. An
    instrument-blind sum would report 14 held for both, and 14 - 3 = 11
    available on INS1 — enough to admit a naked sell.
    """
    rig = _two_instrument_rig()
    assert rig.inventory() == ((INS1, "10"), (INS2, "1"))


def test_two_position_rows_in_one_instrument_are_added_not_chosen_between():
    """``_held_units`` SUMS its matching rows, and that is a deliberate choice.

    Round-7 sweep, survivor: replacing the sum with "take the first match"
    survived all 143 tests, because no rig ever carried two rows for one
    instrument. The docstring says the sum is equivalent to picking one only
    BECAUSE ``PositionBook.positions()`` is keyed by instrument today, and is
    written as a sum so a source that can duplicate is read safely. That is a
    dependency on another component's shape, so it is pinned here rather than
    left as prose: hand the book two rows for INS1 and the answer is their
    total. First-match would say 10, last-match 4, max 10; the sum says 14.
    """
    rig = Rig(positions=(position(instrument=INS1, qty="10"),
                         position(instrument=INS1, qty="4")))
    assert rig.inventory() == ((INS1, "14"),)
    # And it BINDS: a sell of 14 is exactly covered, 15 is not.
    _set(rig.proposal, side="sell", instrument=INS1, qty=Decimal("14"),
         limit=Decimal("10"))
    assert rig.admits() == ()
    _set(rig.proposal, qty=Decimal("15"))
    assert rig.admits()


def test_a_sell_cannot_borrow_units_held_in_another_instrument():
    """The consequence the numbers above prevent, stated as an admission.

    INS2 has ONE uncommitted unit. A sell of 4 must refuse, and it must refuse
    for the units reason — under an instrument-blind sum it would see 11 and
    admit.
    """
    rig = _two_instrument_rig()
    _set(rig.proposal, side="sell", instrument=INS2, qty=Decimal("4"),
         limit=Decimal("10"))
    problems = rig.admits()
    assert problems, "a sell of 4 against 1 uncommitted unit must refuse"
    assert any("INS2" in problem for problem in problems), problems
    _set(rig.proposal, qty=Decimal("1"))
    assert rig.admits() == (), "exactly the uncommitted unit must still admit"


def _two_orders_per_key_rig():
    """Two live working orders on ONE instrument and ONE currency, hand-derived.

    Round-7 review, Major: every rig above held at most one order per key, and
    ``sum``, ``max``, ``first`` and ``last`` are all the same function over a
    one-element sequence. So ``_committed_units``' sum could become a max and
    ``_committed_cash``' sum could become last-wins with all 136 tests still
    green — and last-wins is the dangerous one, because it UNDERSTATES what is
    committed and therefore OVERSTATES buying power at an admission.

    The numbers, derived here and not read back from the code:

    ==============  =========================  ==================
    resource        the working orders          the right answer
    ==============  =========================  ==================
    USD cash        buy 10 @ 10 = 100,          1000 - 160 = 840
                    buy 3 @ 20 = 60
    INS1 units      sell 3, sell 2, held 10     10 - 5 = 5
    ==============  =========================  ==================

    What each wrong aggregation would say instead — every one of them distinct
    from the right answer, which is what makes the assertions below bite:

    ==============  ========  =========  =========  =========
    resource        sum       max        first      last
    ==============  ========  =========  =========  =========
    USD available   **840**   900        900        940
    INS1 available  **5**     7          7          8
    ==============  ========  =========  =========  =========

    The history is EMPTY on purpose: an unsettled fill would put a third term
    into the cash arithmetic, and a value pinned through two moving parts does
    not say which one is wrong.
    """
    rig = Rig(positions=(position(instrument=INS1, qty="10"),),
              working_order=order(ref="b-1", side="buy", qty="10", limit="10",
                                  instrument=INS2))
    rig.history = FakeHistory(())
    rig.accounting = _gate_accounting(rig.history)
    for extra in (order(ref="b-2", side="buy", qty="3", limit="20", instrument=INS2),
                  order(ref="s-1", side="sell", qty="3", limit="10", instrument=INS1),
                  order(ref="s-2", side="sell", qty="2", limit="10", instrument=INS1)):
        rig.orders.append(extra)
        rig.working[extra.client_ref] = extra
    return rig


def test_two_working_buys_in_one_currency_are_both_committed():
    """The committed cash is their SUM, with the number written out."""
    rig = _two_orders_per_key_rig()
    assert str(rig.available()) == "840"


def test_two_working_sells_in_one_instrument_are_both_committed():
    """The committed units are their SUM, with the number written out.

    INS2 carries the two buys and no held position, so it is a row holding
    nothing — stated here because a buy committing units would move it.
    """
    rig = _two_orders_per_key_rig()
    assert rig.inventory() == ((INS1, "5"), (INS2, "0"))


def test_a_buy_may_not_spend_what_a_second_working_buy_already_holds():
    """The consequence the cash number prevents, stated as an admission.

    Exactly 840 admits, one more than 840 refuses, and 900 — which last-wins
    aggregation would have believed was affordable — refuses.
    """
    rig = _two_orders_per_key_rig()
    _set(rig.proposal, side="buy", instrument=INS2, qty=Decimal("84"),
         limit=Decimal("10"))
    assert rig.admits() == (), "a buy landing exactly on available must admit"
    _set(rig.proposal, qty=Decimal("85"))
    assert rig.admits(), "a buy one unit past available must refuse"
    _set(rig.proposal, qty=Decimal("90"))
    assert rig.admits(), "last-wins would have admitted this; the sum refuses it"


def test_a_sell_may_not_promise_what_a_second_working_sell_already_promised():
    """The consequence the units number prevents, stated as an admission.

    Exactly 5 admits, 6 refuses, and 7 — which max aggregation would have
    believed was uncommitted — refuses.
    """
    rig = _two_orders_per_key_rig()
    _set(rig.proposal, side="sell", instrument=INS1, qty=Decimal("5"),
          limit=Decimal("10"))
    assert rig.admits() == (), "a sell landing exactly on the free units must admit"
    _set(rig.proposal, qty=Decimal("6"))
    assert rig.admits(), "a sell one unit past the free units must refuse"
    _set(rig.proposal, qty=Decimal("7"))
    assert rig.admits(), "max would have admitted this; the sum refuses it"


def test_a_partially_filled_sell_commits_only_what_remains_to_fill():
    """`_order_units` reads `remaining_qty`, not the original `qty`.

    Round-12 review: `_order_units` correctly returns `remaining_qty`, but no
    test built a partially-filled SELL — every partial-fill row was a buy, and
    the laws build no partial fills either — so a mutation to `qty` (the
    original size) survived the whole suite. A sell filled 4 of 10 must commit
    the remaining 6, not the original 10.
    """
    rig = Rig(positions=(position(instrument=INS1, qty="10"),),
              working_order=order(ref="s-1", side="sell", qty="10", filled="4",
                                  limit="10", instrument=INS1))
    assert rig.inventory() == ((INS1, "4"),)  # 10 held - 6 remaining = 4
    _set(rig.proposal, side="sell", instrument=INS1, qty=Decimal("4"),
          limit=Decimal("10"))
    assert rig.admits() == (), "a sell of the 4 free units must admit"
    _set(rig.proposal, qty=Decimal("5"))
    assert rig.admits(), "a sell of 5 must refuse: only 4 are free, not 0"


@pytest.mark.parametrize("read", ("available", "reported_available"))
def test_a_second_currency_in_the_view_is_refused_before_anything_is_derived(read):
    """``EncumberedAccounting`` will not build a book over two pots at all.

    This is ``_one_currency``, which sits EARLIER than ``_sole_balance``: with
    the shipped accounting the second row never reaches the book, which is why
    the row below has to reach the measure a different way.
    """
    rig = _two_orders_per_key_rig()
    rig.balances[EUR] = Decimal("500")
    with pytest.raises(ProductionError) as raised:
        getattr(rig, read)()
    assert "EUR" in str(raised.value) and "USD" in str(raised.value)


def test_a_measure_handed_two_balances_refuses_rather_than_funding_from_the_first():
    """``_sole_balance`` refuses a set it cannot attribute. Round-7 sweep survivor.

    Relaxing ``len(rows) != 1`` to ``len(rows) < 1`` survived all 136 tests,
    because nothing built a second balance row anywhere it was READ. Through
    ``EncumberedAccounting`` nothing can: ``_one_currency`` refuses first. But
    ``SettledFundsShortfall`` is a ``Measure`` over whatever ``TickState`` the
    guard chain is handed, and the account in it is not required to have come
    from this policy — a strategy that permits several pots hands it straight
    through. So the account is built here instead of derived, which is the
    shape that reaches the helper.

    Funding a proposal out of row zero would spend 1000 USD or 500 EUR
    depending only on which the fold emitted first, and a proposal carries no
    currency to say which was meant.
    """
    rig = _two_orders_per_key_rig()
    state = guarded_state(rig.view)
    two_pots = dataclasses.replace(
        state.account,
        balances=(
            records.Balance(currency=USD, total=Decimal("1000"),
                            available=Decimal("1000"), native=None),
            records.Balance(currency=EUR, total=Decimal("500"),
                            available=Decimal("500"), native=None),
        ),
    )
    handed = dataclasses.replace(state, account=two_pots)
    with pytest.raises(ProductionError) as raised:
        SettledFundsShortfall().value(
            proposal(side="buy", qty="1", limit="1"), handed, WINDOW, "*", True
        )
    assert "exactly one pot must be in play" in str(raised.value)
    assert "EUR" in str(raised.value) and "USD" in str(raised.value)


def _sell_rig():
    """The rig a SELL proposal is judged against: units, not cash.

    `admit` routes a buy to the cash judge and a sell to the inventory one, so
    the instrument a buy names changes no answer — it is only ever priced. The
    row that moves `proposal.instrument` therefore needs the side whose judge
    reads it, which is exactly why the table carries a rig per row rather than
    one shared scenario that quietly cannot exercise half of it.
    """
    rig = Rig(positions=(position(instrument=INS1, qty="10"),))
    _set(rig.proposal, side="sell", qty=Decimal("10"), limit=Decimal("10"))
    return rig


#: The caller-facing reads, and which `Rig` method takes each one. These are
#: the three methods `loop.py`/`leg.py` and a child reach the derived book
#: through, so a gate that covers the INPUTS but not the ENTRY POINTS is only
#: as strong as its thinnest column.
ENTRY_POINT_READS = ("available", "reported_available", "inventory", "unsized", "admits")

#: One row per input the answer depends on:
#: ``(name, reads, move, make_rig)``. ``reads`` is EVERY entry point whose
#: answer that move must change; ``move`` changes exactly one component;
#: ``make_rig`` builds the scenario in which it is load-bearing.
#:
#: ROUND-6 REVIEW, MAJOR: the first version gave each row ONE read, and the
#: result was lopsided — 13 rows through `encumbrance`, 4 through `admit`, and
#: exactly ONE through `_balances`. So a cache on `_balances` keyed on the
#: working-order KEY SET rather than their field VALUES survived all 6776
#: tests, and `_balances` is the hook `PaperAccounting.snapshot` calls to build
#: the balances `SettledFundsShortfall` reads — dskit's own live guard path. A
#: cache omitting `state_view.pending`, or `state_view.balances`, survived on
#: `admit` the same way. The table enumerated INPUTS; the space is ENTRY
#: POINTS x INPUTS, and `_NOT_MOVED` below makes every cell of it answered.
DEPENDENCIES = (
    ("at_ms", ("available", "reported_available"),
     lambda rig: setattr(rig, "at_ms", rig.at_ms + 1), Rig),
    ("view.balances", ("available", "reported_available", "admits"),
     lambda rig: rig.balances.__setitem__(USD, Decimal("900")), Rig),
    ("view.working.count", ("available", "reported_available", "admits"),
     lambda rig: rig.working.__setitem__("w-2", order(ref="w-2", side="buy",
                                                      qty="5", limit="10")), Rig),
    ("order.limit", ("available", "reported_available", "admits"),
     lambda rig: _set(rig.orders[0], limit=Decimal("20")), Rig),
    ("order.remaining_qty", ("available", "reported_available", "admits"),
     lambda rig: _set(rig.orders[0], remaining_qty=Decimal("20")), Rig),
    ("order.side", ("available", "reported_available", "inventory"),
     lambda rig: _set(rig.orders[0], side="sell"), Rig),
    ("order.instrument", ("inventory",),
     lambda rig: _set(rig.orders[0], instrument=INS2), Rig),
    ("view.positions.qty", ("inventory",),
     lambda rig: move_view(rig.view, positions=(position(qty="2"),)), Rig),
    ("view.positions.instrument", ("inventory",),
     lambda rig: move_view(rig.view, positions=(position(instrument=INS2, qty="10"),)), Rig),
    ("view.pending", ("unsized", "admits"),
     lambda rig: move_view(rig.view, pending=("ref-9",), economic=False), Rig),
    ("fill.qty", ("available", "reported_available", "admits"),
     lambda rig: rig.history.set_fills((fill_body(
         "f-1", "sell", "20", "10", GATE_FILL_TS),)), Rig),
    ("fill.price", ("available", "reported_available", "admits"),
     lambda rig: rig.history.set_fills((fill_body(
         "f-1", "sell", "10", "20", GATE_FILL_TS),)), Rig),
    ("fill.fee", ("available", "reported_available"),
     lambda rig: rig.history.set_fills((fill_body(
         "f-1", "sell", "10", "10", GATE_FILL_TS, fee="5"),)), Rig),
    ("fill.ts_ms", ("available", "reported_available"),
     lambda rig: rig.history.set_fills((fill_body(
         "f-1", "sell", "10", "10", GATE_FILL_TS - 1),)), Rig),
    ("fill.side", ("available", "reported_available"),
     lambda rig: rig.history.set_fills((fill_body(
         "f-1", "buy", "10", "10", GATE_FILL_TS),)), Rig),
    # HONEST LIMIT (round-6 review, Minor): a cache keyed on the OUTPUT of
    # `effective_fills` and omitting `status` survives this row, because
    # `effective_fills` already drops a reversed fill from its output, so the
    # list membership carries the change. The row still proves status is
    # load-bearing END TO END; it does not prove a status-blind key is unsafe
    # at that one placement. Round-7 review corrected the sentence that used
    # to follow: a key built BEFORE `effective_fills` and omitting `status`
    # IS covered — built and run, it fails this row at both entry points plus
    # two more, because the pre-filter fill list differs only in `status`.
    ("fill.status", ("available", "reported_available"),
     lambda rig: rig.history.set_fills((fill_body(
         "f-1", "sell", "10", "10", GATE_FILL_TS, status="reversed"),)), Rig),
    ("fill.fill_id", ("available", "reported_available"),
     lambda rig: rig.history.set_fills((
         fill_body("f-1", "sell", "10", "10", GATE_FILL_TS),
         fill_body("f-1", "sell", "10", "10", GATE_FILL_TS, status="reversed"),
     )), Rig),
    ("proposal.qty", ("admits",),
     lambda rig: _set(rig.proposal, qty=Decimal("90")), Rig),
    ("proposal.limit", ("admits",),
     lambda rig: _set(rig.proposal, limit=Decimal("20")), Rig),
    ("proposal.side", ("admits",),
     lambda rig: _set(rig.proposal, side="sell"), Rig),
    ("proposal.instrument", ("admits",),
     lambda rig: _set(rig.proposal, instrument=INS2), _sell_rig),
)

#: Every (input, entry point) cell the table deliberately does NOT assert, with
#: the reason. A cell is in `reads` or here; there is no third place, which is
#: what makes the coverage check below mean something. The reasons are the
#: shape of the computation, not an excuse: `available` is
#: ``total - committed - unsettled`` and `inventory` is units, so an input to
#: one is genuinely not an input to the other.
_NOT_MOVED = {
    ("at_ms", "admits"): "moving the instant SETTLES a fill, which RAISES "
        "available; the rig admits exactly, so more room still admits",
    ("order.side", "admits"): "a buy becoming a sell releases its cash, which "
        "raises available; the rig admits exactly, so more room still admits",
    ("fill.ts_ms", "admits"): "settling the fill raises available, as above",
    ("fill.side", "admits"): "a sell becoming a buy stops counting as "
        "unsettled under trade_date basis, raising available",
    ("fill.fee", "admits"): "a sell's fee reduces its PROCEEDS, so a larger "
        "fee lowers unsettled and RAISES available; the rig admits exactly, so "
        "more room still admits (found by this gate, not by inspection)",
    ("fill.status", "admits"): "a reversed fill stops counting, raising available",
    ("fill.fill_id", "admits"): "the reversal pairs off, raising available",
}
for _row in DEPENDENCIES:
    for _point in ENTRY_POINT_READS:
        if _point in ("inventory", "unsized") and _point not in _row[1]:
            _NOT_MOVED.setdefault(
                (_row[0], _point),
                "funds and proposal inputs do not move the units book or the "
                "unsized-ref list; those are different computations",
            )
        if _point in ("available", "reported_available", "admits") and _point not in _row[1]:
            _NOT_MOVED.setdefault(
                (_row[0], _point),
                "an inventory, pending or proposal input does not move the "
                "cash book",
            )


@pytest.mark.parametrize(
    "name,point", sorted(_NOT_MOVED), ids=lambda v: v if isinstance(v, str) else str(v)
)
def test_every_excused_cell_is_excused_TRUTHFULLY(name, point):
    """Each excuse in ``_NOT_MOVED`` is EXECUTED, not just spelled.

    Round-7 review, Minor: the matrix above checks that every cell is either
    asserted or excused, and that the two sets are disjoint — pure FORM. An
    excuse that was simply wrong ("this input does not move that answer" when
    it does) would pass it and hide a real dependency behind a sentence. So
    every excused cell now performs its row's move and asserts the reading
    really is unchanged. An excuse that turns out to be false fails here, and
    the cell belongs in ``DEPENDENCIES`` instead.

    The excuses that say a move RAISES available are still answered exactly:
    the rig sits on the admission boundary, so more room admits, and ``admits``
    reads ``()`` on both sides.
    """
    rows = {row[0]: row for row in DEPENDENCIES}
    if name not in rows:
        pytest.skip(f"{name} carries no move of its own")
    _, _, move, make_rig = rows[name]
    rig = make_rig()
    before = getattr(rig, point)()
    move(rig)
    assert getattr(rig, point)() == before, (
        f"{name} x {point} is excused as not moving {point}, but it does: "
        f"{before!r} -> {getattr(rig, point)()!r}. Move the cell into "
        "DEPENDENCIES and assert the new value."
    )


def test_every_entry_point_by_input_cell_is_answered():
    """The coverage claim, as a MATRIX and not a list.

    Round-6 review: the table enumerated inputs and let each row pick one entry
    point, so `_balances` had a single row and a cache confined to it survived
    the whole suite. Every cell of ENTRY_POINTS x INPUTS is now either asserted
    by a row or excused here WITH A REASON, and the two are disjoint — so a new
    row cannot be added without saying what it does at every entry point.
    """
    for name, reads, _, _ in DEPENDENCIES:
        assert reads, f"{name} asserts nothing"
        for point in reads:
            assert point in ENTRY_POINT_READS, (name, point)
            assert (name, point) not in _NOT_MOVED, (
                f"{name} x {point} is both asserted and excused"
            )
        for point in ENTRY_POINT_READS:
            if point not in reads:
                assert (name, point) in _NOT_MOVED, (
                    f"{name} x {point} is neither asserted nor excused"
                )
    # And the thin column that started this: `_balances` is now exercised by
    # every funds input, not by one.
    through_balances = {n for n, reads, _, _ in DEPENDENCIES if "reported_available" in reads}
    assert len(through_balances) >= 10, sorted(through_balances)


@pytest.mark.parametrize(
    "name,read,move,make_rig",
    [(n, point, move, rig) for n, reads, move, rig in DEPENDENCIES for point in reads],
    ids=[f"{n}-{point}" for n, reads, _, _ in DEPENDENCIES for point in reads],
)
def test_every_input_the_book_depends_on_moves_the_answer_at_the_real_entry_point(
    name, read, move, make_rig
):
    """Move one input; the caller-facing answer must move with it.

    This is the gate. `history` is reached through `self._history` and is in
    no signature here, the view and the proposal keep their identities, and the
    instant is an `int` — so a cache keyed on ANY proper subset of these inputs
    returns the first answer and fails whichever row names the omitted one. No
    cache is written down anywhere; the space is covered by construction.
    """
    rig = make_rig()
    before_ids = rig.identities()
    before = getattr(rig, read)()
    move(rig)
    assert rig.identities() == before_ids, (
        f"{name}: the move changed an object IDENTITY, so this row would pass "
        "for an identity-keyed cache too"
    )
    after = getattr(rig, read)()
    assert before != after, (
        f"{name}: moving it left the answer at `{read}` unchanged ({before!r}) "
        "— either it is not an input, or the entry point is not reading it"
    )


def test_the_baseline_proposal_sits_exactly_on_the_admission_boundary():
    """The control the `admits` rows rest on.

    If the rig admitted with room to spare, a row could move an input and
    still admit, and the assertion above would be measuring nothing. So the
    baseline admits EXACTLY, and one unit more refuses.
    """
    rig = Rig()
    assert rig.available() == Decimal("800")
    assert rig.admits() == ()
    _set(rig.proposal, qty=Decimal("81"))
    assert rig.admits() != ()


#: Record fields the cash policy genuinely does not read, each with the reason.
#: A field lands here or in `DEPENDENCIES`; there is no third place, which is
#: what makes the completeness check below mean something.
NOT_READ = {
    records.Fill: {
        "venue_ref": "venue bookkeeping; the policy keys on fill_id",
        "client_ref": "links a fill to an intent, not to a balance",
        "fee_currency": "single-currency rig; the fee is netted in its own currency",
        "liquidity": "a venue attribution tag, not an amount",
        "instrument": "cash settlement nets by currency, never by instrument",
        "native": "the raw venue payload, deliberately unread",
    },
    records.OrderState: {
        "venue_ref": "venue bookkeeping", "status": "holding vs terminal is "
        "decided by the fold before the view is built",
        "ts_ms": "ordering only", "filled_qty": "`remaining_qty` is the "
        "outstanding figure and is read directly",
        "avg_price": "an execution fact; a working order is valued at its limit",
        "fee": "billed on the fill, not reserved on the order",
        "reason": "message text", "native": "raw venue payload",
        "qty": "`remaining_qty` is what is still outstanding",
        "tif": "expiry is the fold's business", "created_ms": "ordering only",
        "updated_ms": "ordering only",
        "client_ref": "names the order in a message; the working map is keyed by it",
    },
    records.Position: {
        "avg_cost": "a cost basis, not an availability",
        "source": "provenance tag", "native": "raw venue payload",
    },
    records.Proposal: {
        "id": "names the candidate in a message",
        "notional": "this policy sizes on qty x limit",
        "tif": "not an amount", "expires_ms": "not an amount",
        "reference_price": "a quote, not the committed price",
        "exposure": "a lead's own figure", "direction": "a lead's own figure",
        "confidence": "a lead's own figure", "prediction": "a lead's own figure",
        "baseline": "a lead's own figure", "expected_value": "a lead's own figure",
        "inputs_asof_ms": "evidence provenance", "inputs_digest": "evidence provenance",
        "coverage_digest": "evidence provenance", "quote_asof_ms": "evidence provenance",
        "quote_digest": "evidence provenance", "extra": "carrier for adapters",
    },
}


#: `StateView`'s own fields. The completeness check walked the four RECORD
#: types and not the view itself (round-6 review, Nit), so a future read of
#: `breaker` or `reduction` inside the policy would be caught by nothing here.
#: None is a plausible cash or units input today, and the three version fields
#: are pinned separately by the counters test — but "not plausible" is the
#: reasoning the escape clause used, so they are declared rather than omitted.
_VIEW_NOT_READ = {
    "breaker": "a halt state; the policy derives a book, it does not gate on one",
    "arming": "an authorization state, read by the arming family",
    "readiness": "a checklist state, read by readiness",
    "guard_holds": "read by Limit and GuardChain, which are Guards not Measures",
    "reduction": "a reduce-only mode, enforced above this seam",
    "pending_control": "in-flight control requests, not an amount",
    "decision_history": "read by the decision-count measures",
    "risk_version": "fold versioning; pinned by the counters test",
    "head_seq": "fold versioning; pinned by the counters test",
    "head_hash": "fold versioning; pinned by the counters test",
}


def test_the_view_fields_are_declared_too():
    """Every `StateView` field is exercised by a row or declared unread."""
    exercised = {row[0] for row in DEPENDENCIES}
    for field in dataclasses.fields(StateView):
        row = f"view.{field.name}"
        touched = any(name == row or name.startswith(f"{row}.") for name in exercised)
        assert touched or field.name in _VIEW_NOT_READ, (
            f"StateView.{field.name} is neither exercised by a row nor declared "
            "unread in _VIEW_NOT_READ"
        )
        assert not (touched and field.name in _VIEW_NOT_READ), (
            f"StateView.{field.name} is both exercised and declared unread"
        )
    stray = sorted(set(_VIEW_NOT_READ) - {f.name for f in dataclasses.fields(StateView)})
    assert not stray, f"_VIEW_NOT_READ names absent fields: {stray}"


def test_the_dependency_table_names_every_field_read():
    """A field added to a money record cannot enter without a row above.

    A pinning test that omits a knob is worse than none (CLAUDE.md), and the
    round-5 Major was precisely an omitted knob: `ts_ms`. So the table is
    checked against `dataclasses.fields` of the four record types the policy
    reads, and every field is either exercised by a `DEPENDENCIES` row or
    declared unread WITH A REASON.
    """
    exercised = {row[0] for row in DEPENDENCIES}
    prefixes = {records.Fill: "fill", records.OrderState: "order",
                records.Position: "view.positions", records.Proposal: "proposal"}
    # NO per-type escape clause. The first version let every `Position` field
    # pass because the table held a bare ``view.positions`` row, so the record
    # whose fields the inventory book is BUILT from was the one record checked
    # least. Found by self-review before a lens had to; the rows are named by
    # field now, like every other record's.
    for record_type, prefix in prefixes.items():
        declared = NOT_READ[record_type]
        for field in dataclasses.fields(record_type):
            row = f"{prefix}.{field.name}"
            covered = row in exercised or field.name in declared
            assert covered, (
                f"{record_type.__name__}.{field.name} is neither exercised by a "
                f"DEPENDENCIES row named {row!r} nor declared unread in NOT_READ"
            )
        stray = sorted(set(declared) - {f.name for f in dataclasses.fields(record_type)})
        assert not stray, f"NOT_READ[{record_type.__name__}] names absent fields: {stray}"
        # The two sets must be DISJOINT. Without this the check is an OR that
        # either side satisfies, so `NOT_READ` could claim a field is unread
        # while a row exercises it — which is how a real dependency gets
        # retired by an edit to the wrong table (found by mutation).
        both = sorted(
            name for name in declared if f"{prefix}.{name}" in exercised
        )
        assert not both, (
            f"NOT_READ[{record_type.__name__}] claims {both} unread while "
            "DEPENDENCIES exercises them — a field belongs to exactly one table"
        )


# ---------------------------------------------------------------------------
# Properties, not axes — the strategy this seam was asked for
# ---------------------------------------------------------------------------
#
# Eight rounds of hand-written rows each closed one layer and left the next
# exposed, because each row names ONE input and asserts ONE reading. The two
# properties below are not rows: they hold for EVERY state a generator can
# build, and between them they kill the whole family of aggregation defects at
# once rather than one rig per aggregation.
#
#   PARTITION INVARIANCE. Splitting one working order, or one held position,
#   into two halves that add up to it changes nothing a caller can read. A
#   `sum` has this property; `max`, `first`, `last` and `next` do not.
#
#   PERMUTATION INVARIANCE. The order rows arrive in changes nothing. A `sum`
#   has this property; `first`, `last` and any early-return scan do not.
#
# Neither names an input, so neither goes stale when an input is added: a new
# field that breaks either one fails here without anyone writing a row for it.

_WHOLE_QTYS = st.lists(
    st.integers(min_value=2, max_value=40), min_size=1, max_size=4
)
_LIMITS = st.lists(st.integers(min_value=1, max_value=30), min_size=1, max_size=4)
_SIDES = st.lists(st.sampled_from(("buy", "sell")), min_size=1, max_size=4)
_INSTRUMENTS = st.lists(st.sampled_from((INS1, INS2)), min_size=1, max_size=4)


def _readings(orders, positions, balance="10000"):
    """Every caller-facing figure for one fold, compared by VALUE.

    `Decimal`s, never their `str`: ``Decimal("0.5") + Decimal("0.5")`` prints
    ``"1.0"`` where the undivided row prints ``"1"``, so a string comparison
    fails a law that holds. `Decimal.__eq__` is numeric, so a figure that
    really moved still fails.
    """
    working = {order.client_ref: order for order in orders}
    view = _gate_view({USD: Decimal(balance)}, working, tuple(positions))
    accounting = _gate_accounting(FakeHistory(()))
    book = accounting.encumbrance(view, GATE_AT_MS)
    # All FOUR caller-facing reads, not just the book. A law that held for the
    # derived figures while `admit` disagreed would be a law about the wrong
    # thing, and `_balances` is the read `PaperAccounting.snapshot` takes — the
    # thin column round 6 had to widen because one row was all it had.
    return (
        book.funds[USD].available,
        book.funds[USD].committed,
        tuple(sorted(
            (key, row.available, row.held, row.committed)
            for key, row in book.inventory.items()
        )),
        book.unsized_refs,
        tuple(sorted(accounting.admit(p, view, GATE_AT_MS)
                     for p in _LAW_PROPOSALS)),
        tuple((b.currency, b.total, b.available)
              for b in accounting._balances(view, GATE_AT_MS)),
    )


#: The proposals every law judges the fold against, held constant while the
#: fold is split or permuted: one buy and one sell, each sized to straddle
#: plausible boundaries so a wrong aggregation changes a verdict and not only
#: a number. The buy commits 98 @ 100 = 9800 cash; the generator's committed
#: cash is small (median ~22, p90 ~624, measured) against the 10000 balance,
#: so 9800 refuses the moment a fold commits more than 200 and admits otherwise
#: — round-12 review measured a buy of 80 @ 10 = 800 (never refused, because no
#: fold commits 9200) and found its `admit` read contributed nothing.
_LAW_PROPOSALS = (
    proposal(side="buy", qty="98", limit="100", instrument=INS1),
    proposal(side="sell", qty="6", limit="10", instrument=INS1, pid="cand-2"),
)


def _orders_from(sides, qtys, limits, instruments):
    """One working order per drawn tuple, refs distinct so the dict keeps them all."""
    rows = zip(sides, qtys, limits, instruments)
    return [
        order(ref=f"w-{index}", side=side, qty=str(qty), limit=str(limit),
              instrument=instrument)
        for index, (side, qty, limit, instrument) in enumerate(rows)
    ]


@pytest.mark.parametrize(
    "value,rendered",
    [
        ("9", "9"),
        ("9.0", "9"),          # what `Decimal(4.5) + Decimal(4.5)` produces
        ("0.50", "0.5"),
        ("100", "100"),        # `normalize()` alone answers "1E+2" here
        ("1E+2", "100"),
        ("0", "0"),
        ("-12.500", "-12.5"),
    ],
)
def test_one_number_has_one_rendering_in_an_operator_facing_message(value, rendered):
    """`_amount`, pinned by VALUE — the laws cannot reach the exponent case.

    Round-9 sweep: dropping `normalize()` dies to the partition law, but
    dropping the `"f"` format spec survives it, because no generated fold
    produces a figure large enough for `Decimal.normalize` to answer in
    exponent notation. A hundred is not an unusual shortfall, and "1E+2 units
    cannot be found" is not a message anyone should be handed.
    """
    assert _amount(Decimal(value)) == rendered

@settings(max_examples=150, deadline=None)
@given(
    sides=_SIDES, qtys=_WHOLE_QTYS, limits=_LIMITS, instruments=_INSTRUMENTS,
    split=st.integers(min_value=0, max_value=3),
)
def test_splitting_one_working_order_in_half_changes_no_reading(
    sides, qtys, limits, instruments, split
):
    """PARTITION INVARIANCE over working orders.

    A commitment is a total, so one order for 10 and two orders for 5 must
    encumber the same thing. `max`, `first` and `last` all disagree the moment
    a key carries two rows, which is the round-7 Major stated as a law instead
    of as a rig.
    """
    width = min(len(sides), len(qtys), len(limits), len(instruments))
    whole = _orders_from(sides[:width], qtys[:width], limits[:width],
                         instruments[:width])
    index = split % width
    victim = whole[index]
    half = victim.qty / 2
    pieces = [
        order(ref=f"{victim.client_ref}-a", side=victim.side, qty=str(half),
              limit=str(victim.limit), instrument=victim.instrument),
        order(ref=f"{victim.client_ref}-b", side=victim.side, qty=str(half),
              limit=str(victim.limit), instrument=victim.instrument),
    ]
    divided = whole[:index] + pieces + whole[index + 1:]
    assert _readings(whole, ()) == _readings(divided, ())


@settings(max_examples=150, deadline=None)
@given(
    sides=_SIDES, qtys=_WHOLE_QTYS, limits=_LIMITS, instruments=_INSTRUMENTS,
    seed=st.integers(min_value=0, max_value=2**16),
)
def test_the_order_the_fold_lists_its_rows_in_changes_no_reading(
    sides, qtys, limits, instruments, seed
):
    """PERMUTATION INVARIANCE over working orders and held positions.

    Nothing a caller reads may depend on which row the fold emitted first.
    `first`- and `last`-wins aggregations, and any scan that returns early,
    all fail this for every state carrying two rows on one key.
    """
    width = min(len(sides), len(qtys), len(limits), len(instruments))
    rows = _orders_from(sides[:width], qtys[:width], limits[:width],
                        instruments[:width])
    held = [position(instrument=INS1, qty="10"), position(instrument=INS1, qty="4"),
            position(instrument=INS2, qty="7")]
    shuffled, held_shuffled = list(rows), list(held)
    random.Random(seed).shuffle(shuffled)
    random.Random(seed + 1).shuffle(held_shuffled)
    assert _readings(rows, held) == _readings(shuffled, held_shuffled)


@settings(max_examples=150, deadline=None)
@given(
    sides=_SIDES, qtys=_WHOLE_QTYS, limits=_LIMITS, instruments=_INSTRUMENTS,
    held=st.integers(min_value=0, max_value=40),
)
def test_splitting_one_held_position_in_half_changes_no_reading(
    sides, qtys, limits, instruments, held
):
    """PARTITION INVARIANCE over held positions.

    `_held_units` SUMS its matching rows deliberately, against a source that
    cannot duplicate today. The law says what the deliberate choice IS, so a
    future source that can duplicate is read the way this function already
    promises to read it.
    """
    width = min(len(sides), len(qtys), len(limits), len(instruments))
    rows = _orders_from(sides[:width], qtys[:width], limits[:width],
                        instruments[:width])
    whole = [position(instrument=INS1, qty=str(held))]
    halves = [position(instrument=INS1, qty=str(Decimal(held) / 2)),
              position(instrument=INS1, qty=str(Decimal(held) / 2))]
    assert _readings(rows, whole) == _readings(rows, halves)
