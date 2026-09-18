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
from decimal import Decimal
from types import MappingProxyType

import pytest

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
        # `reconcile.LedgerHistory.fills` refuses a bound that is not a
        # non-negative int. A double that accepted one would hide an
        # underflow in its caller, which is exactly what it did in round 1.
        # Restated deliberately rather than imported: a double that read its
        # rule from the thing it stands in for would assert nothing.
        if isinstance(since_ms, bool) or not isinstance(since_ms, int) or since_ms < 0:
            raise ProductionError([f"since_ms must be an int >= 0, got {since_ms!r}"])
        self.calls.append(since_ms)
        return tuple(f for f in self._fills if f["ts_ms"] >= since_ms)

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
    cases = (
        (view(balances={USD: Decimal("100")}), proposal(qty="10", limit="10")),
        (view(balances={USD: Decimal("99")}), proposal(qty="10", limit="10")),
        (view(positions=(position(qty="3"),)), proposal(side="sell", qty="4")),
        (view(positions=(position(qty="10"),)), proposal(side="sell", qty="4")),
    )
    for state_view, candidate in cases:
        refused_by_admit = bool(policy.admit(candidate, state_view, T0, FakeHistory()))
        state = guarded_state(state_view)
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
