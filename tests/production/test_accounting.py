"""`accounting.py` — the seam that proves a reduction and evidences a bound (§5.7.1, D14).

Accounting is the economic authority behind every guard, every
`DecisionPlan.risk_effect` and every `ActPermit`'s risk binding, and D14 keeps
it a *separate* seam from execution: the executor moves orders, accounting says
what the account is. Three hooks carry that, and each is pinned here on what it
must PROVE rather than on what it may claim.

* **`classify` proves a reduction; it never believes one.** D12 says "the
  accounting strategy, not a model claim, must prove each proposal cannot
  increase absolute exposure". So the answer is computed against current
  positions AND working orders — a buy that reduces a short net of working buys
  is `reduce`, one that would flip the sign is `increase`, and a proposal
  carrying `direction: "reduce"` on a flat account is still `increase`. It
  reads `state.account`, the correction-aware snapshot with prior legs'
  reservations folded, never `state.view` (§5.8.1's `ECONOMIC_ATTRS` rule).
* **`snapshot` re-anchors every requirement at its own `at_ms` (ruling R8).**
  `GuardChain.requirements` builds the union at the tick's instant; the leg's
  later refresh snapshots at a *later* instant, and a measure rebuilds its
  digest from `state.account.asof_ms`. If accounting keyed the evidence by the
  digest it was handed, every refreshed leg would refuse for missing evidence.
  So the strongest test in this file runs a real `guards.Pnl` against an
  account built from requirements formed at an earlier instant.
* **A cash flow is not profit (§6).** An adopted external deposit must leave
  `pnl` evidence untouched even though it is in the capital base
  `bankroll_fraction` reads; the opposite would inflate a loss halt into
  headroom, which is the defect §6's partition exists to stop.

Two structural rules are asserted rather than assumed. Bitemporality: a
snapshot at `at_ms` is unchanged by any history record learned after `at_ms`,
and a superseding correction replaces what it corrects rather than adding to
it. Freshness: quotes older than `max_valuation_age_ms` leave `nav` null but
REFUSE a snapshot, because `nav` is an observation while a snapshot is what a
permit binds.

Positions, working orders and balances are the fold's — §5.8.1 gives them one
owner and a snapshot that re-derived them would be the second. What the view
cannot supply is HISTORY: it carries positions, not the fills that made them,
and no window baselines at all. So evidence is folded through an injected
`history` collaborator (`fills` / `cash_flows` / `marks`, each yielding the §6
record bodies since an instant). `accounting.py` may not scan the ledger
(`test_state.py` pins the scan owners), which is exactly why the seam exists.

No wall clock, no network, no real executor: every instant is an int computed
here, the calendar has fixed windows, and the executor is a fake whose every
attribute raises — a paper snapshot must never touch it.
"""

import dataclasses
import inspect
from decimal import Decimal
from types import MappingProxyType

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dskit.production import records, vocab
from dskit.production.accounting import (
    ACCOUNTING_KINDS,
    Accounting,
    PaperAccounting,
    RecordedAccounting,
)
from dskit.production.base import ProductionError, canonical_hash
from dskit.production.document import PAPER_ACCOUNTING_KIND
from dskit.production.guards import GuardChain, Limit, Pnl, Window
from dskit.production.state import StateView, TickState

# ---------------------------------------------------------------------------
# Fixed material — every instant and Decimal below is computed here
# ---------------------------------------------------------------------------

SECOND = 1_000
MINUTE = 60 * SECOND
HOUR = 60 * MINUTE
DAY = 24 * HOUR

BASE_MS = 1_767_268_800_000

#: The tick instant the requirements are built at, and the LATER instant the
#: leg's refresh snapshots at. R8 lives in the gap between them.
T0 = BASE_MS + HOUR
T1 = T0 + 5 * MINUTE

#: The `FakeCalendar`'s fixed windows. `SESSION` contains both T0 and T1, so a
#: calendar requirement resolves to identical bounds at either instant.
SESSION = (BASE_MS, BASE_MS + 6 * HOUR)
NEXT_SESSION = (BASE_MS + DAY, BASE_MS + DAY + 6 * HOUR)
DAY_WINDOW = (BASE_MS - 12 * HOUR, BASE_MS + 12 * HOUR)
EVENT_WINDOW = (BASE_MS + 30 * MINUTE, BASE_MS + 90 * MINUTE)

#: §4.1's illustration: how old a mark may be and still value the book.
MAX_VALUATION_AGE_MS = 60_000

#: §5.5's four history/account measures — the only ones that declare an
#: `EvidenceRequirement`, so the only ones a snapshot must answer. Restated,
#: never read back from `MEASURE_KINDS`: an assertion sourced from its subject
#: asserts nothing. `exposure`, `exposure_after` and `bankroll_fraction` are
#: answered by the snapshot's own `positions`/`working`/`balances`.
EVIDENCE_MEASURES = ("consecutive_losses", "drawdown", "error_vs_realised", "pnl")

#: The two core strategies §5.7.1 names; live is a child class.
CORE_ACCOUNTING_KINDS = ("paper", "recorded")

D_INPUTS = "1" * 64
D_COVER = "2" * 64
D_QUOTE = "3" * 64

USD = "USD"
INS1 = "INS1"
INS2 = "INS2"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCalendar:
    """Fixed `[start, end)` windows, so every resolved bound is a known number."""

    tz_name = "UTC"

    def __init__(self, sessions=(SESSION, NEXT_SESSION), day=DAY_WINDOW, event=EVENT_WINDOW):
        self._by_kind = {"session": tuple(sessions), "day": (day,), "event": (event,)}

    def window(self, kind, at_ms):
        windows = self._by_kind.get(kind)
        if windows is None:
            raise ProductionError([f"calendar: unknown window kind {kind!r}"])
        for start, end in windows:
            if at_ms < end:
                return (start, end)
        raise ProductionError([f"calendar: no {kind} window at {at_ms}"])

    def is_open(self, at_ms):
        return any(start <= at_ms < end for start, end in self._by_kind["session"])


CAL = FakeCalendar()


class FakeClock:
    """The one `Clock` method accounting needs; no wall time."""

    def __init__(self, ms=T1):
        self._ms = int(ms)

    def now_ms(self):
        return self._ms

    def monotonic(self):
        return self._ms / 1000.0

    def advance(self, ms):
        self._ms += int(ms)
        return self._ms


class FakeHistory:
    """The injected fold source: §6 record bodies since an instant.

    `StateView` carries positions, not the fills that made them, and
    `accounting.py` may not scan the ledger (`test_state.py` pins the scan
    owners), so the history is a collaborator. It records the `since_ms` it was
    asked for, which is how "one call per snapshot, wide enough for the widest
    window" is checkable.
    """

    #: Each yielded record is its §6 body plus the envelope `id`, since
    #: `supersedes` names a record and a bare body could not resolve it.
    def __init__(self, fills=(), cash_flows=(), marks=()):
        self._fills = tuple(fills)
        self._cash_flows = tuple(cash_flows)
        self._marks = tuple(marks)
        self.calls = []

    def fills(self, since_ms):
        self.calls.append(("fills", since_ms))
        return tuple(f for f in self._fills if f["ts_ms"] >= since_ms)

    def cash_flows(self, since_ms):
        self.calls.append(("cash_flows", since_ms))
        return tuple(c for c in self._cash_flows if c["effective_at_ms"] >= since_ms)

    def marks(self, since_ms):
        self.calls.append(("marks", since_ms))
        return tuple(m for m in self._marks if m["effective_at_ms"] >= since_ms)

    def asked(self, name):
        return [since for method, since in self.calls if method == name]

    def set_fills(self, fills):
        """Change what the source reports — a source moving under a token."""
        self._fills = tuple(fills)


class Boom:
    """A collaborator that must NOT be reached: touching it is the defect."""

    def __init__(self, what="collaborator"):
        object.__setattr__(self, "_what", what)

    def __getattr__(self, name):
        raise AssertionError(f"a deterministic snapshot touched {self._what}.{name}")


class FakeLiveAccounting(PaperAccounting):
    """A child that DOES carry session tokens — the live discipline, testably.

    §5.7.1: "Live snapshots also return monotonic executor/accounting source
    tokens; absence, regression or reuse with changed contents refuses." The
    rule cannot live in a child or every child would restate it, so the base
    owns it and a child supplies only the tokens.
    """

    def __init__(self, params=None, *, clock, history, max_valuation_age_ms, tokens=()):
        super().__init__(
            params, clock=clock, history=history, max_valuation_age_ms=max_valuation_age_ms
        )
        self.answers = list(tokens)

    def source_tokens(self, executor, at_ms):
        """Answer the next recorded token pair."""
        return self.answers.pop(0)


# ---------------------------------------------------------------------------
# Builders — §6 record bodies, built through the real record types
# ---------------------------------------------------------------------------


def fill_body(fill_id, side, qty, price, ts_ms, fee="1", instrument=INS1, status="final"):
    """One §6 `fill` body: the `Fill` record, JSON-shaped as the ledger holds it."""
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


def round_trip(prefix, buy_price, sell_price, at_ms, qty="10", fee="1", instrument=INS1):
    """A flat-to-flat pair of fills; its profit is `qty * (sell - buy) - 2 * fee`."""
    return [
        fill_body(f"{prefix}-buy", "buy", qty, buy_price, at_ms, fee=fee, instrument=instrument),
        fill_body(
            f"{prefix}-sell", "sell", qty, sell_price, at_ms + SECOND, fee=fee,
            instrument=instrument,
        ),
    ]


def reversal_of(body):
    """The venue's bust of an applied fill: the same id, status `reversed`."""
    return {**body, "status": "reversed"}


def cash_flow_body(
    flow_id,
    amount,
    effective_at_ms,
    known_at_ms=None,
    external=True,
    kind="deposit",
    supersedes=None,
    currency=USD,
):
    """One §6 `cash_flow` body — signed amount, bitemporal, superseding."""
    return {
        "id": flow_id,
        "effective_at_ms": effective_at_ms,
        "known_at_ms": effective_at_ms if known_at_ms is None else known_at_ms,
        "supersedes": supersedes,
        "currency": currency,
        "amount": str(Decimal(amount)),
        "flow_kind": kind,
        "external": external,
        "source": "venue",
        "evidence": {"note": "adopted from a cash break"},
    }


def mark_body(
    record_id, leg_id, value, effective_at_ms, known_at_ms=None, supersedes=None, kind="settled"
):
    """One §6 `outcome` body — label arrival, mark or correction.

    Carries the envelope `id` under `"id"`, the way §6 gives a `cash_flow`
    body its own id: `supersedes` names a RECORD, so a history that yielded
    bare bodies could not resolve a correction chain.
    """
    return {
        "id": record_id,
        "leg_id": leg_id,
        "outcome_kind": kind,
        "effective_at_ms": effective_at_ms,
        "known_at_ms": effective_at_ms if known_at_ms is None else known_at_ms,
        "value": str(Decimal(value)),
        "weight": "1",
        "terminal": True,
        "supersedes": supersedes,
        # A `vocab.OUTCOME_SOURCES` member: `records.Outcome` closes the
        # field, and a fixture that spelled it otherwise would not be a
        # §6 body at all.
        "source": "settlement",
    }


def quote(instrument=INS1, mid="10", asof_ms=T1):
    return records.Quote(
        instrument=instrument,
        bid=Decimal(mid) - Decimal("0.01"),
        ask=Decimal(mid) + Decimal("0.01"),
        mid=Decimal(mid),
        asof_ms=asof_ms,
    )


def quote_set(*quotes):
    quotes = quotes or (quote(),)
    return records.QuoteSet(
        quotes=tuple(quotes),
        quote_digest=D_QUOTE,
        min_asof_ms=min(q.asof_ms for q in quotes),
    )


def position(instrument=INS1, qty="10", avg_cost="10"):
    return records.Position(
        instrument=instrument,
        qty=Decimal(qty),
        avg_cost=Decimal(avg_cost),
        source="derived",
        native=None,
    )


def working_order(instrument=INS1, side="buy", remaining="4", limit="10", ref="ref-w1"):
    return records.OrderState(
        client_ref=ref,
        venue_ref=f"v-{ref}",
        status="open",
        ts_ms=T0,
        filled_qty=Decimal("0"),
        avg_price=None,
        fee=Decimal("0"),
        reason="",
        native=None,
        instrument=instrument,
        side=side,
        qty=Decimal(remaining),
        remaining_qty=Decimal(remaining),
        limit=None if limit is None else Decimal(limit),
        tif="gtc",
        created_ms=T0 - MINUTE,
        updated_ms=T0,
    )


def view(positions=(), working=(), balances=None, economic_seq=41, decision_history=()):
    """A real frozen `StateView` — the type §5.7.1 names as the first argument."""
    return StateView(
        positions=tuple(positions),
        working=MappingProxyType({order.client_ref: order for order in working}),
        pending=(),
        balances=MappingProxyType(dict(balances or {})),
        decision_history=tuple(decision_history),
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


def proposal(**over):
    """The worked proposal: buy 10 @ ref 10 (notional 100)."""
    values = {
        "id": "cand-1",
        "instrument": INS1,
        "side": "buy",
        "qty": Decimal("10"),
        "notional": Decimal("100"),
        "limit": Decimal("10.50"),
        "tif": "ioc",
        "expires_ms": T1 + 5 * SECOND,
        "reference_price": Decimal("10"),
        "exposure": Decimal("100"),
        "direction": "long",
        "confidence": 0.61,
        "prediction": 0.58,
        "baseline": 0.50,
        "expected_value": 0.03,
        "inputs_asof_ms": T1,
        "inputs_digest": D_INPUTS,
        "coverage_digest": D_COVER,
        "quote_asof_ms": T1,
        "quote_digest": D_QUOTE,
        "extra": {},
    }
    values.update(over)
    return records.Proposal(**values)


def candidate(cid="cand-1", instrument=INS1, scope_keys=(INS1,)):
    return records.Candidate(id=cid, instrument=instrument, scope_keys=tuple(scope_keys))


def requirements_for(window, at_ms=T0, scope="per_key", measure="pnl", candidates=None):
    """The union `GuardChain` hands `snapshot`, built by the real chain."""
    limit = Limit(
        {
            "measure": measure,
            "window": window,
            "bound": {"min": "-500"},
            "on_breach": "halt",
            "scope": scope,
        },
        name="day_loss",
    )
    chain = GuardChain({"day_loss": limit})
    return chain.requirements(candidates or (candidate(),), at_ms, CAL)


def anchored(requirement, at_ms, window):
    """The same question re-anchored at `at_ms` — what R8 says accounting keys by."""
    start, end, baseline, arg = Window.from_params(window).resolve(at_ms, CAL)
    return records.EvidenceRequirement(
        measure=requirement.measure,
        window_kind=requirement.window_kind,
        window_arg=arg,
        scope_key=requirement.scope_key,
        window_start_ms=start,
        window_end_ms=end,
        baseline_at_ms=baseline,
        include_working=requirement.include_working,
    )


def paper(history=None, clock=None, params=None, max_valuation_age_ms=MAX_VALUATION_AGE_MS):
    return PaperAccounting(
        params if params is not None else {},
        clock=clock or FakeClock(),
        history=history or FakeHistory(),
        max_valuation_age_ms=max_valuation_age_ms,
    )


def snapshot(accounting=None, state=None, quotes=None, at_ms=T1, requirements=(), calendar=CAL):
    """`Accounting.snapshot` with the six §5.7.1 arguments in order."""
    return (accounting or paper()).snapshot(
        state if state is not None else view(),
        Boom("executor"),
        quotes if quotes is not None else quote_set(),
        at_ms,
        requirements,
        calendar,
    )


def evidence_for(account, requirement):
    """The one `MeasureEvidence` answering a requirement at its scope key."""
    return account.measure_evidence[requirement.requirement_digest][requirement.scope_key]


def tick_state(account, state_view=None):
    return TickState(
        view=state_view if state_view is not None else view(),
        account=account,
        feed_status="live",
        feed_ages=(),
        calendar=CAL,
    )


def refusal(exc):
    return "; ".join(exc.value.problems)


# ===========================================================================
# The seam: an ABC with abstract hooks and a registry
# ===========================================================================


def test_accounting_is_abstract_and_refuses_instantiation():
    with pytest.raises(TypeError):
        Accounting()


@pytest.mark.parametrize("hook", ["value", "classify", "snapshot"])
def test_a_subclass_missing_a_hook_refuses_to_construct(hook):
    """§5.15: abstract means `@abstractmethod`, so an incomplete child fails at
    construction rather than at the first tick that needed the hook."""
    body = {
        name: (lambda self, *a, **k: None)
        for name in ("value", "classify", "snapshot")
        if name != hook
    }
    incomplete = type("Incomplete", (Accounting,), body)
    with pytest.raises(TypeError):
        incomplete()


def test_the_registry_is_the_accounting_family_over_the_abc():
    assert ACCOUNTING_KINDS.family == "accounting"
    assert ACCOUNTING_KINDS.abc is Accounting


def test_the_core_kinds_are_paper_and_recorded():
    assert ACCOUNTING_KINDS.kinds() == CORE_ACCOUNTING_KINDS
    assert ACCOUNTING_KINDS.resolve("paper") is PaperAccounting
    assert ACCOUNTING_KINDS.resolve("recorded") is RecordedAccounting


def test_an_unknown_kind_refuses():
    with pytest.raises(ProductionError):
        ACCOUNTING_KINDS.resolve("live")


def test_a_class_that_is_not_an_accounting_refuses_to_register():
    with pytest.raises(ProductionError):
        ACCOUNTING_KINDS.register("bogus", dict)


def test_the_documents_paper_kind_names_a_registered_accounting():
    """D9's live gate refuses `accounting.uses == PAPER_ACCOUNTING_KIND`; the
    name is `document.py`'s and the registry is here, so the two must agree."""
    assert PAPER_ACCOUNTING_KIND in ACCOUNTING_KINDS
    assert PAPER_ACCOUNTING_KIND == "paper"


def test_the_base_reports_no_session_tokens():
    """A simulated session has none; a live child overrides this hook and the
    base owns the monotonic discipline over what it answers."""
    assert paper().source_tokens(Boom("executor"), T1) == (None, None)


# ===========================================================================
# Construction — default-deny, injected collaborators
# ===========================================================================


def test_paper_accounting_takes_params_then_keyword_collaborators():
    params = list(inspect.signature(PaperAccounting.__init__).parameters)
    assert params[:2] == ["self", "params"]
    assert set(params[2:]) == {"clock", "history", "max_valuation_age_ms"}


def test_notes_is_accepted_at_the_params_site():
    assert paper(params={"notes": "why this strategy"}) is not None


def test_an_unknown_param_refuses():
    with pytest.raises(ProductionError) as exc:
        paper(params={"mark_to": "last"})
    assert "mark_to" in refusal(exc)


def test_the_valuation_age_is_a_document_sibling_not_a_param():
    """§4.1 puts `max_valuation_age_ms` beside `uses`/`params`, not inside
    them; accepting it in both places is a knob with two names."""
    with pytest.raises(ProductionError) as exc:
        paper(params={"max_valuation_age_ms": 1_000})
    assert "max_valuation_age_ms" in refusal(exc)


def test_the_valuation_age_is_required():
    with pytest.raises((TypeError, ProductionError)):
        PaperAccounting({}, clock=FakeClock(), history=FakeHistory())


@pytest.mark.parametrize("age", [0, -1, 1.5, "60000", None])
def test_a_valuation_age_that_is_not_a_positive_int_refuses(age):
    with pytest.raises(ProductionError):
        paper(max_valuation_age_ms=age)


def test_the_clock_and_the_history_are_required():
    with pytest.raises(TypeError):
        PaperAccounting({}, max_valuation_age_ms=MAX_VALUATION_AGE_MS)


# ===========================================================================
# classify — a reduction is proven, not claimed (D10, D12)
# ===========================================================================

#: `(position qty, working orders, side, qty, expected)` — hand-computed from
#: the net signed exposure before and after. A working buy of 4 against a short
#: of 10 leaves a net short of 6, which is what the proposal is measured
#: against; §5.7.1 states exactly that case.
CLASSIFY_CASES = [
    ("flat account, a buy opens risk", "0", (), "buy", "5", "increase"),
    ("long, a buy adds", "10", (), "buy", "5", "increase"),
    ("long, a sell reduces", "10", (), "sell", "5", "reduce"),
    ("long, a sell to flat reduces", "10", (), "sell", "10", "reduce"),
    ("long, an over-sell flips the sign", "10", (), "sell", "15", "increase"),
    ("short, a buy reduces", "-10", (), "buy", "5", "reduce"),
    ("short net of working buys, a buy reduces", "-10", (("buy", "4"),), "buy", "3", "reduce"),
    ("short net of working buys, a big buy flips", "-10", (("buy", "4"),), "buy", "10", "increase"),
    ("long fully offset by a working sell, a buy opens", "10", (("sell", "10"),), "buy", "5",
     "increase"),
    ("working sells already reducing, another sell reduces further", "10",
     (("sell", "2"),), "sell", "3", "reduce"),
]


@pytest.mark.parametrize(
    "label,held,orders,side,qty,expected",
    CLASSIFY_CASES,
    ids=[case[0] for case in CLASSIFY_CASES],
)
def test_classify_answers_the_proven_effect(label, held, orders, side, qty, expected):
    account = snapshot(
        state=view(
            positions=() if Decimal(held) == 0 else (position(qty=held),),
            working=tuple(
                working_order(side=s, remaining=r, ref=f"ref-w{n}")
                for n, (s, r) in enumerate(orders)
            ),
        )
    )
    got = paper().classify(proposal(side=side, qty=Decimal(qty)), tick_state(account))
    assert got == expected
    assert got in vocab.RISK_EFFECTS


def test_an_abstaining_proposal_is_neutral():
    account = snapshot(state=view(positions=(position(),)))
    assert paper().classify(
        proposal(side="none", qty=None, notional=None), tick_state(account)
    ) == "neutral"


def test_a_zero_sized_proposal_is_neutral():
    account = snapshot(state=view(positions=(position(),)))
    assert paper().classify(
        proposal(side="buy", qty=Decimal("0")), tick_state(account)
    ) == "neutral"


def test_a_position_in_another_instrument_does_not_make_a_proposal_a_reduction():
    """Risk effect is per instrument; netting across instruments would let a
    long in one name license an unbounded new position in another."""
    account = snapshot(
        state=view(positions=(position(instrument=INS2, qty="-50"),)),
        quotes=quote_set(quote(INS1), quote(INS2)),
    )
    assert paper().classify(proposal(side="buy"), tick_state(account)) == "increase"


def test_a_proposal_that_claims_to_reduce_is_still_measured():
    """D12: the accounting strategy, not a model claim, proves the reduction."""
    account = snapshot(state=view())
    claimed = proposal(side="buy", direction="reduce", extra={"risk_effect": "reduce"})
    assert paper().classify(claimed, tick_state(account)) == "increase"


def test_classify_reads_the_account_not_the_fold_at_head():
    """§5.8.1: `state.account` is the economic authority — the snapshot with
    prior legs' reservations folded. The fold at head is a different number,
    and a guard that read it would size against stale exposure."""
    account = snapshot(state=view(positions=(position(qty="-10"),)))
    contradicting = view(positions=(position(qty="10"),))
    got = paper().classify(
        proposal(side="buy", qty=Decimal("5")), tick_state(account, contradicting)
    )
    assert got == "reduce"


def test_a_sized_proposal_without_a_quantity_refuses():
    """R10: `qty` is the order size and is required when side != none, so a
    classification cannot be asked of a proposal that has no size."""
    account = snapshot(state=view())
    with pytest.raises(ProductionError):
        paper().classify(proposal(side="buy", qty=None), tick_state(account))


def test_classify_touches_neither_the_history_nor_the_clock():
    """It is a question about one proposal against a snapshotted account."""
    accounting = PaperAccounting(
        {}, clock=Boom("clock"), history=Boom("history"),
        max_valuation_age_ms=MAX_VALUATION_AGE_MS,
    )
    account = snapshot(state=view(positions=(position(),)))
    assert accounting.classify(proposal(side="sell", qty=Decimal("5")), tick_state(account)) == (
        "reduce"
    )


@settings(max_examples=200, deadline=None)
@given(
    held=st.integers(min_value=-50, max_value=50),
    work=st.integers(min_value=-20, max_value=20),
    size=st.integers(min_value=0, max_value=60),
    side=st.sampled_from(["buy", "sell"]),
)
def test_classify_is_total_and_its_answer_matches_the_change_in_absolute_exposure(
    held, work, size, side
):
    """The plan's own rule, asserted as a property: exactly one member, and
    `reduce` if and only if absolute net exposure strictly fell without
    flipping sign."""
    orders = ()
    if work:
        orders = (working_order(side="buy" if work > 0 else "sell", remaining=str(abs(work))),)
    account = snapshot(
        state=view(
            positions=() if held == 0 else (position(qty=str(held)),),
            working=orders,
        )
    )
    before = Decimal(held + work)
    delta = Decimal(size if side == "buy" else -size)
    after = before + delta
    got = paper().classify(
        proposal(side=side, qty=Decimal(size)), tick_state(account)
    )
    assert got in vocab.RISK_EFFECTS
    if delta == 0:
        assert got == "neutral"
    elif abs(after) < abs(before) and (after == 0 or (after > 0) == (before > 0)):
        assert got == "reduce"
    else:
        assert got == "increase"


# ===========================================================================
# value — the marked portfolio value that becomes `tick.nav`
# ===========================================================================


def test_nav_is_the_cash_balance_plus_the_marked_positions():
    """1000 cash + 10 units marked at 12.50 = 1125."""
    nav = paper().value(
        view(positions=(position(qty="10"),), balances={USD: Decimal("1000")}),
        quote_set(quote(mid="12.50")),
        T1,
    )
    assert nav == Decimal("1125")
    assert isinstance(nav, Decimal)


def test_nav_with_no_positions_is_the_balance():
    assert paper().value(view(balances={USD: Decimal("250")}), quote_set(), T1) == Decimal("250")


def test_nav_marks_a_short_position_negatively():
    nav = paper().value(
        view(positions=(position(qty="-4"),), balances={USD: Decimal("1000")}),
        quote_set(quote(mid="25")),
        T1,
    )
    assert nav == Decimal("900")


def test_nav_is_null_when_a_required_mark_is_missing():
    """§5.7.1: recorded rather than guessed."""
    assert paper().value(
        view(positions=(position(instrument=INS2),), balances={USD: Decimal("1000")}),
        quote_set(quote(instrument=INS1)),
        T1,
    ) is None


def test_nav_is_null_when_balances_span_currencies():
    """There is no FX seam in this ADR, so a two-currency account has no single
    number to report — and inventing one would be the guess §5.7.1 forbids."""
    assert paper().value(
        view(balances={USD: Decimal("1000"), "EUR": Decimal("10")}),
        quote_set(),
        T1,
    ) is None


def test_a_quote_exactly_at_the_valuation_age_still_marks():
    nav = paper().value(
        view(positions=(position(),), balances={USD: Decimal("0")}),
        quote_set(quote(mid="10", asof_ms=T1 - MAX_VALUATION_AGE_MS)),
        T1,
    )
    assert nav == Decimal("100")


def test_a_quote_one_millisecond_too_old_leaves_nav_null():
    assert paper().value(
        view(positions=(position(),), balances={USD: Decimal("0")}),
        quote_set(quote(mid="10", asof_ms=T1 - MAX_VALUATION_AGE_MS - 1)),
        T1,
    ) is None


def test_a_stale_quote_on_an_instrument_nothing_is_held_in_does_not_null_nav():
    """Only marks the valuation actually needs are required to be fresh."""
    nav = paper().value(
        view(positions=(position(),), balances={USD: Decimal("0")}),
        quote_set(
            quote(instrument=INS1, mid="10", asof_ms=T1),
            quote(instrument=INS2, mid="99", asof_ms=T1 - 10 * MAX_VALUATION_AGE_MS),
        ),
        T1,
    )
    assert nav == Decimal("100")


def test_value_touches_neither_the_history_nor_the_executor():
    accounting = PaperAccounting(
        {}, clock=Boom("clock"), history=Boom("history"),
        max_valuation_age_ms=MAX_VALUATION_AGE_MS,
    )
    assert accounting.value(view(balances={USD: Decimal("5")}), quote_set(), T1) == Decimal("5")


# ===========================================================================
# snapshot — R8 re-anchoring and the evidence contract
# ===========================================================================


def test_the_snapshot_is_as_of_the_instant_it_was_asked_for():
    assert snapshot(at_ms=T1).asof_ms == T1


def test_evidence_is_keyed_by_the_requirement_re_anchored_at_the_snapshot_instant():
    """Ruling R8. The chain builds the union at the tick instant; the leg's
    refresh snapshots later and a measure rebuilds its digest from
    `state.account.asof_ms`. Keying by the digest handed in would make every
    refreshed leg refuse for missing evidence."""
    window = {"duration": "PT1H"}
    (requirement,) = requirements_for(window, at_ms=T0)
    account = snapshot(at_ms=T1, requirements=(requirement,))
    expected = anchored(requirement, T1, window)
    assert expected.requirement_digest != requirement.requirement_digest
    assert set(account.measure_evidence) == {expected.requirement_digest}
    assert evidence_for(account, expected).window_end_ms == T1


def test_a_measure_finds_the_evidence_the_snapshot_left_for_it():
    """The R8 round trip end to end: requirements built at T0, snapshot at T1,
    and the real `Pnl` measure reads its answer back."""
    window = {"duration": "PT1H"}
    (requirement,) = requirements_for(window, at_ms=T0)
    account = snapshot(at_ms=T1, requirements=(requirement,))
    got = Pnl().value(
        proposal(), tick_state(account), Window.from_params(window), requirement.scope_key, True
    )
    assert isinstance(got, Decimal)


def test_one_fresh_evidence_per_requirement_and_scope_key():
    requirements = requirements_for(
        {"duration": "PT1H"},
        at_ms=T1,
        candidates=(candidate("c1", INS1, (INS1,)), candidate("c2", INS2, (INS2,))),
    )
    assert len(requirements) == 2
    account = snapshot(at_ms=T1, requirements=requirements)
    assert set(account.measure_evidence) == {r.requirement_digest for r in requirements}
    for requirement in requirements:
        answers = account.measure_evidence[requirement.requirement_digest]
        assert set(answers) == {requirement.scope_key}
        assert answers[requirement.scope_key].scope_key == requirement.scope_key


def test_every_evidence_is_learned_at_the_snapshot_instant():
    """"One FRESH `MeasureEvidence`" — carrying an older answer forward is how
    a guard would size against evidence from a previous tick."""
    (requirement,) = requirements_for({"count": 20}, at_ms=T1)
    account = snapshot(at_ms=T1, requirements=(requirement,))
    evidence = evidence_for(account, requirement)
    assert evidence.known_at_ms == T1
    assert evidence.effective_at_ms <= T1


def test_every_evidence_repeats_the_bounds_of_the_question_it_answers():
    (requirement,) = requirements_for({"calendar": "session"}, at_ms=T1)
    evidence = evidence_for(snapshot(at_ms=T1, requirements=(requirement,)), requirement)
    assert evidence.requirement_digest == requirement.requirement_digest
    assert (evidence.window_start_ms, evidence.window_end_ms) == (
        requirement.window_start_ms,
        requirement.window_end_ms,
    )
    assert isinstance(evidence.value, Decimal)
    assert isinstance(evidence.sample_count, int)


@pytest.mark.parametrize(
    "window", [{}, {"duration": "PT1H"}, {"count": 20}, {"calendar": "session"},
               {"calendar": "day"}, {"calendar": "event"}]
)
@pytest.mark.parametrize("measure", EVIDENCE_MEASURES)
def test_every_window_family_of_every_evidence_measure_is_answered(window, measure):
    """§5.7.1: the union includes all duration/count/session/day/event
    boundaries; a family the strategy cannot answer must refuse at plan, not
    leave a guard measuring nothing."""
    requirements = requirements_for(window, at_ms=T1, measure=measure)
    account = snapshot(at_ms=T1, requirements=requirements)
    for requirement in requirements:
        assert isinstance(evidence_for(account, requirement).value, Decimal)


@pytest.mark.parametrize("scope,expected_key", [("aggregate", "*"), ("per_key", INS1)])
def test_the_scope_key_of_the_question_is_the_scope_key_of_the_answer(scope, expected_key):
    (requirement,) = requirements_for({"duration": "PT1H"}, at_ms=T1, scope=scope)
    assert requirement.scope_key == expected_key
    assert set(snapshot(at_ms=T1, requirements=(requirement,))
               .measure_evidence[requirement.requirement_digest]) == {expected_key}


def test_two_measures_asking_the_same_question_are_answered_once():
    (requirement,) = requirements_for({"duration": "PT1H"}, at_ms=T1)
    account = snapshot(at_ms=T1, requirements=(requirement, requirement))
    assert len(account.measure_evidence) == 1


def test_a_requirement_this_strategy_cannot_answer_refuses():
    """§5.7.1: unsupported requirements refuse at plan/tick — silently
    answering zero is what a bound would then be measured against."""
    unsupported = records.EvidenceRequirement(
        measure="child_exposure",
        window_kind="duration",
        window_arg=HOUR,
        scope_key=INS1,
        window_start_ms=T1 - HOUR,
        window_end_ms=T1,
        baseline_at_ms=T1 - HOUR,
        include_working=True,
    )
    with pytest.raises(ProductionError) as exc:
        snapshot(at_ms=T1, requirements=(unsupported,))
    assert "child_exposure" in refusal(exc)


def test_a_requirement_that_is_not_an_evidence_requirement_refuses():
    with pytest.raises(ProductionError):
        snapshot(at_ms=T1, requirements=({"measure": "pnl"},))


def test_an_empty_requirement_union_snapshots_no_evidence():
    assert snapshot(at_ms=T1, requirements=()).measure_evidence == {}


# ===========================================================================
# snapshot — the fold: fills, cash flows, corrections, as-of
# ===========================================================================


def worked_pnl_history(**over):
    """Two fills inside the window: buy 10 @ 10 and sell 10 @ 11, fee 1 each."""
    return FakeHistory(fills=round_trip("a", "10", "11", T1 - 30 * MINUTE), **over)


def pnl_value(history, window=None, at_ms=T1, scope="aggregate"):
    """The `pnl` evidence value over one window, folded from `history`."""
    window = {"duration": "PT1H"} if window is None else window
    (requirement,) = requirements_for(window, at_ms=at_ms, scope=scope)
    account = snapshot(
        accounting=paper(history=history), at_ms=at_ms, requirements=(requirement,)
    )
    return evidence_for(account, requirement).value


def test_pnl_over_a_window_is_the_trading_profit_of_the_fills_it_covers():
    """10 units bought at 10 and sold at 11, one unit of fee each way:
    10 * (11 - 10) - 2 = 8. Flat at both ends, so no mark enters it."""
    assert pnl_value(worked_pnl_history()) == Decimal("8")


def test_the_sample_count_counts_the_fills_the_window_covers():
    (requirement,) = requirements_for({"duration": "PT1H"}, at_ms=T1, scope="aggregate")
    account = snapshot(
        accounting=paper(history=worked_pnl_history()), at_ms=T1, requirements=(requirement,)
    )
    assert evidence_for(account, requirement).sample_count == 2


def test_a_fill_before_the_window_does_not_enter_it():
    """The baseline is the account as the window opened, not the series start;
    a window that swept in older profit would never bind."""
    history = FakeHistory(
        fills=round_trip("old", "10", "12", T1 - 6 * HOUR) + round_trip("a", "10", "11", T1 - MINUTE)
    )
    assert pnl_value(history) == Decimal("8")


def test_a_reversed_fill_is_undone_and_its_profit_does_not_count():
    """§5.7.1: implementations explicitly reverse busted fills. Two round trips
    worth 8 and 12; the venue busts the second, leaving 8."""
    good = round_trip("a", "10", "11", T1 - 40 * MINUTE)
    busted = round_trip("b", "10", "12", T1 - 20 * MINUTE, fee="4")
    assert pnl_value(FakeHistory(fills=good + busted)) == Decimal("20")
    reversed_history = FakeHistory(fills=good + busted + [reversal_of(f) for f in busted])
    assert pnl_value(reversed_history) == Decimal("8")


def test_a_reversal_is_applied_once_however_often_it_is_reported():
    good = round_trip("a", "10", "11", T1 - 40 * MINUTE)
    busted = round_trip("b", "10", "12", T1 - 20 * MINUTE, fee="4")
    twice = FakeHistory(fills=good + busted + [reversal_of(f) for f in busted] * 2)
    assert pnl_value(twice) == Decimal("8")


def test_an_external_deposit_leaves_pnl_evidence_alone_and_the_balance_larger():
    """§6's partition, and the defect it exists to stop: an adopted deposit
    that inflated a `pnl` halt guard into headroom."""
    (requirement,) = requirements_for({"duration": "PT1H"}, at_ms=T1, scope="aggregate")
    traded = paper(history=worked_pnl_history())
    with_deposit = paper(
        history=FakeHistory(
            fills=round_trip("a", "10", "11", T1 - 30 * MINUTE),
            cash_flows=(cash_flow_body("cf-1", "1000", T1 - 20 * MINUTE, external=True),),
        )
    )
    before = snapshot(accounting=traded, at_ms=T1, requirements=(requirement,))
    # The deposit reaches balances through the FOLD (the ledger folded the
    # cash_flow record); history only digests it — adding it again would
    # double-count every adopted deposit.
    after = snapshot(
        accounting=with_deposit,
        at_ms=T1,
        requirements=(requirement,),
        state=view(balances={"USD": Decimal("1000")}),
    )
    assert evidence_for(after, requirement).value == evidence_for(before, requirement).value
    assert [b.total for b in after.balances] == [Decimal("1000")]
    assert before.balances == ()


def outcome_evidence_digest(marks, at_ms=T1):
    """`risk_digest` of a snapshot whose only evidence is outcome-fed."""
    (requirement,) = requirements_for(
        {"duration": "PT1H"}, at_ms=at_ms, scope="aggregate", measure="error_vs_realised"
    )
    return snapshot(
        accounting=paper(history=FakeHistory(marks=marks)),
        at_ms=at_ms,
        requirements=(requirement,),
    ).risk_digest()


def test_a_superseding_correction_replaces_what_it_corrects():
    """D21: a corrected value supersedes, never mutates and never doubles. So
    a fold over `[wrong, corrected-supersedes-wrong]` must equal a fold over
    `[corrected]` alone, and must differ from one where nothing superseded."""
    wrong = mark_body("out-1", "leg-1", "10", T1 - 40 * MINUTE)
    corrected = mark_body("out-2", "leg-1", "4", T1 - 40 * MINUTE, known_at_ms=T1 - MINUTE,
                          supersedes="out-1", kind="corrected")
    uncorrected = mark_body("out-3", "leg-2", "4", T1 - 40 * MINUTE, known_at_ms=T1 - MINUTE)
    assert outcome_evidence_digest((wrong, corrected)) == outcome_evidence_digest((corrected,))
    assert outcome_evidence_digest((wrong, corrected)) != outcome_evidence_digest(
        (wrong, uncorrected)
    )


def test_a_record_learned_after_the_snapshot_instant_is_not_in_it():
    """Bitemporality (D21): a snapshot is what was KNOWN at `at_ms`. An outcome
    that lands days later must not retroactively change a decision already
    taken, or a replay of that tick would reach a different account."""
    known = mark_body("out-1", "leg-1", "4", T1 - 40 * MINUTE)
    later = mark_body("out-2", "leg-2", "99", T1 - MINUTE, known_at_ms=T1 + 1)
    assert outcome_evidence_digest((known, later)) == outcome_evidence_digest((known,))


def test_a_record_learned_at_the_snapshot_instant_is_in_it():
    """The mirror, so the as-of cut is inclusive and the test above can fail."""
    known = mark_body("out-1", "leg-1", "4", T1 - 40 * MINUTE)
    now = mark_body("out-2", "leg-2", "99", T1 - MINUTE, known_at_ms=T1)
    assert outcome_evidence_digest((known, now)) != outcome_evidence_digest((known,))


def test_a_fill_timestamped_after_the_snapshot_instant_is_not_in_it():
    """A `Fill` carries one instant; a fill the venue reports for a moment that
    has not happened yet cannot be part of the account at `at_ms`."""
    settled = round_trip("a", "10", "11", T1 - 30 * MINUTE)
    ahead = round_trip("b", "10", "50", T1 + SECOND)
    assert pnl_value(FakeHistory(fills=settled + ahead)) == Decimal("8")


def test_an_external_cash_flow_never_enters_the_trading_measures():
    """§6's partition, and the defect it exists to stop: an adopted deposit
    that inflated a `pnl` halt guard into headroom."""
    traded = round_trip("a", "10", "11", T1 - 30 * MINUTE)
    with_deposit = FakeHistory(
        fills=traded,
        cash_flows=(cash_flow_body("cf-1", "1000", T1 - 20 * MINUTE, external=True),),
    )
    assert pnl_value(FakeHistory(fills=traded)) == Decimal("8")
    assert pnl_value(with_deposit) == Decimal("8")


def test_the_balances_of_the_snapshot_are_the_folds_capital_base():
    """§5.8.1 gives balances one owner. `bankroll_fraction` reads them from the
    account, and §6 requires that base to INCLUDE an external flow — which the
    fold already applied when it folded the `cash_flow` record."""
    account = snapshot(state=view(balances={USD: Decimal("1000")}), at_ms=T1)
    (balance,) = account.balances
    assert (balance.currency, balance.total) == (USD, Decimal("1000"))


def test_a_paper_balance_is_wholly_available():
    (balance,) = snapshot(state=view(balances={USD: Decimal("1000")}), at_ms=T1).balances
    assert balance.available == balance.total


def test_the_history_is_asked_once_and_widely_enough_for_every_window():
    """A history call per requirement would fold the same fills N times; a
    window wider than what was fetched would be answered from a truncated
    history, which is worse."""
    history = worked_pnl_history()
    requirements = requirements_for({"duration": "PT1H"}, at_ms=T1) + requirements_for(
        {"calendar": "day"}, at_ms=T1
    )
    snapshot(accounting=paper(history=history), at_ms=T1, requirements=requirements)
    assert len(history.asked("fills")) == 1
    assert history.asked("fills")[0] <= min(r.window_start_ms for r in requirements)


def test_the_cash_flows_and_marks_are_folded_too():
    history = worked_pnl_history()
    snapshot(
        accounting=paper(history=history),
        at_ms=T1,
        requirements=requirements_for({"duration": "PT1H"}, at_ms=T1),
    )
    assert len(history.asked("cash_flows")) == 1
    assert len(history.asked("marks")) == 1


def test_a_paper_snapshot_never_touches_the_executor():
    """D14 keeps the seams separate: what the account IS does not depend on
    what the executor can DO, and a deterministic strategy must not do I/O."""
    assert snapshot(at_ms=T1).asof_ms == T1


def test_the_working_orders_of_the_snapshot_are_the_folds():
    """There is no working-order history seam; the fold owns them (§5.8.1)."""
    order = working_order()
    account = snapshot(state=view(working=(order,)), at_ms=T1)
    assert account.working == (order,)


def test_the_positions_of_the_snapshot_are_the_folds():
    """§5.8.1 names ONE owner for positions. A snapshot that re-derived them
    from its own fill history would be the second way §5.8.1 exists to stop,
    and would miss this tick's earlier legs, which the fold already holds."""
    held = position(qty="7", avg_cost="9")
    account = snapshot(
        accounting=paper(history=FakeHistory(fills=round_trip("a", "1", "2", T1 - MINUTE))),
        state=view(positions=(held,)),
        quotes=quote_set(quote(mid="9")),
        at_ms=T1,
    )
    assert account.positions == (held,)


def test_the_economic_sequence_comes_from_the_fold():
    account = snapshot(state=view(economic_seq=97), at_ms=T1)
    assert account.risk_version.economic_seq == 97


# ===========================================================================
# snapshot — freshness, determinism and the digests a permit binds
# ===========================================================================


def test_a_snapshot_refuses_a_quote_older_than_the_valuation_age():
    """`nav` may be null, but a snapshot is what an `ActPermit` binds: sizing
    against a stale mark is the failure D14's freshness rule exists to stop."""
    with pytest.raises(ProductionError) as exc:
        snapshot(
            state=view(positions=(position(),)),
            quotes=quote_set(quote(asof_ms=T1 - MAX_VALUATION_AGE_MS - 1)),
            at_ms=T1,
        )
    assert "max_valuation_age_ms" in refusal(exc) or "stale" in refusal(exc)


def test_a_snapshot_accepts_a_quote_exactly_at_the_valuation_age():
    account = snapshot(
        state=view(positions=(position(),)),
        quotes=quote_set(quote(asof_ms=T1 - MAX_VALUATION_AGE_MS)),
        at_ms=T1,
    )
    assert account.asof_ms == T1


def test_a_snapshot_refuses_when_a_held_instrument_has_no_mark():
    with pytest.raises(ProductionError):
        snapshot(
            state=view(positions=(position(instrument=INS2),)),
            quotes=quote_set(quote(instrument=INS1)),
            at_ms=T1,
        )


def test_two_snapshots_of_the_same_inputs_are_identical():
    """"`PaperAccounting` and `RecordedAccounting` are deterministic" — a
    replay that produced a different account could not prove parity."""
    requirements = requirements_for({"calendar": "session"}, at_ms=T1)
    first = snapshot(
        accounting=paper(history=worked_pnl_history()), at_ms=T1, requirements=requirements
    )
    second = snapshot(
        accounting=paper(history=worked_pnl_history()), at_ms=T1, requirements=requirements
    )
    assert first == second
    assert first.risk_digest() == second.risk_digest()


def test_re_observing_the_same_account_is_not_an_economic_change():
    """§5.4: `risk_digest` excludes observation-only timestamps. Both instants
    sit inside one session, so the calendar window resolves alike and only the
    observation instant differs."""
    requirements = requirements_for({"calendar": "session"}, at_ms=T0)
    history = worked_pnl_history()
    early = snapshot(accounting=paper(history=history), at_ms=T0, requirements=requirements)
    late = snapshot(accounting=paper(history=history), at_ms=T1, requirements=requirements)
    assert early.asof_ms != late.asof_ms
    assert early.risk_digest() == late.risk_digest()


def test_a_new_fill_moves_the_risk_digest():
    requirements = requirements_for({"calendar": "session"}, at_ms=T1)
    quiet = snapshot(
        accounting=paper(history=worked_pnl_history()), at_ms=T1, requirements=requirements
    )
    busy = snapshot(
        accounting=paper(
            history=FakeHistory(
                fills=round_trip("a", "10", "11", T1 - 30 * MINUTE)
                + round_trip("b", "10", "13", T1 - 10 * MINUTE)
            )
        ),
        at_ms=T1,
        requirements=requirements,
    )
    assert quiet.risk_digest() != busy.risk_digest()


def test_the_evidence_digest_covers_the_evidence():
    requirements = requirements_for({"calendar": "session"}, at_ms=T1)
    quiet = snapshot(
        accounting=paper(history=worked_pnl_history()), at_ms=T1, requirements=requirements
    )
    assert len(quiet.evidence_digest) == 64
    assert quiet.evidence_digest == canonical_hash(
        {
            digest: {scope: answer.to_obj() for scope, answer in answers.items()}
            for digest, answers in quiet.measure_evidence.items()
        }
    )


def test_the_source_digests_name_the_folded_sources_and_move_with_them():
    requirements = requirements_for({"calendar": "session"}, at_ms=T1)
    quiet = snapshot(
        accounting=paper(history=worked_pnl_history()), at_ms=T1, requirements=requirements
    )
    busy = snapshot(
        accounting=paper(
            history=FakeHistory(fills=round_trip("a", "10", "99", T1 - 30 * MINUTE))
        ),
        at_ms=T1,
        requirements=requirements,
    )
    assert set(quiet.source_digests) == {"fills", "cash_flows", "marks"}
    assert all(len(v) == 64 for v in quiet.source_digests.values())
    assert quiet.source_digests["fills"] != busy.source_digests["fills"]


# ===========================================================================
# snapshot — monotonic session tokens (§5.7.1, D14)
# ===========================================================================


def live(tokens, history=None):
    return FakeLiveAccounting(
        {},
        clock=FakeClock(),
        history=history or FakeHistory(),
        max_valuation_age_ms=MAX_VALUATION_AGE_MS,
        tokens=tokens,
    )


def test_a_live_snapshot_binds_the_tokens_its_sources_reported():
    accounting = live([("etok-0001", ("atok-0001",))])
    account = snapshot(accounting=accounting, at_ms=T1)
    assert account.risk_version.executor_token == "etok-0001"
    assert account.risk_version.accounting_tokens == ("atok-0001",)


def test_advancing_tokens_are_accepted():
    accounting = live([("etok-0001", ("atok-0001",)), ("etok-0002", ("atok-0002",))])
    snapshot(accounting=accounting, at_ms=T1)
    later = snapshot(accounting=accounting, at_ms=T1 + SECOND)
    assert later.risk_version.executor_token == "etok-0002"


def test_a_token_that_disappears_refuses():
    """"Absence ... refuses": a source that stops reporting a version is a
    source that can no longer prove it did not go backwards."""
    accounting = live([("etok-0002", ("atok-0002",)), (None, None)])
    snapshot(accounting=accounting, at_ms=T1)
    with pytest.raises(ProductionError):
        snapshot(accounting=accounting, at_ms=T1 + SECOND)


def test_a_token_that_goes_backwards_refuses():
    accounting = live([("etok-0002", ("atok-0002",)), ("etok-0001", ("atok-0003",))])
    snapshot(accounting=accounting, at_ms=T1)
    with pytest.raises(ProductionError):
        snapshot(accounting=accounting, at_ms=T1 + SECOND)


def test_an_accounting_token_that_goes_backwards_refuses():
    accounting = live([("etok-0002", ("atok-0002",)), ("etok-0003", ("atok-0001",))])
    snapshot(accounting=accounting, at_ms=T1)
    with pytest.raises(ProductionError):
        snapshot(accounting=accounting, at_ms=T1 + SECOND)


def test_reusing_a_token_while_the_economics_changed_refuses():
    """"Reuse with changed contents refuses": if the version did not move, the
    account did not move, and a permit binding that version would be a lie."""
    history = FakeHistory(fills=round_trip("a", "10", "11", T1 - 30 * MINUTE))
    accounting = live(
        [("etok-0002", ("atok-0002",)), ("etok-0002", ("atok-0002",))], history=history
    )
    requirements = requirements_for({"calendar": "session"}, at_ms=T1)
    snapshot(accounting=accounting, at_ms=T1, requirements=requirements)
    history.set_fills(
        round_trip("a", "10", "11", T1 - 30 * MINUTE)
        + round_trip("b", "10", "20", T1 - 10 * MINUTE)
    )
    with pytest.raises(ProductionError):
        snapshot(accounting=accounting, at_ms=T1 + SECOND, requirements=requirements)


def test_reusing_a_token_over_unchanged_economics_is_accepted():
    accounting = live([("etok-0002", ("atok-0002",)), ("etok-0002", ("atok-0002",))])
    first = snapshot(accounting=accounting, at_ms=T1)
    second = snapshot(accounting=accounting, at_ms=T1 + SECOND)
    assert first.risk_digest() == second.risk_digest()


def test_a_paper_snapshot_reports_no_session_tokens():
    """§6: the snapshot record never restores them, because they are live
    session tokens — a simulated session has none to restore."""
    version = snapshot(at_ms=T1).risk_version
    assert (version.executor_token, version.accounting_tokens) == (None, None)


# ===========================================================================
# RecordedAccounting — the replay strategy (D20)
# ===========================================================================


def test_recorded_accounting_is_an_accounting():
    assert issubclass(RecordedAccounting, Accounting)


def test_the_tape_answers_in_the_order_it_was_recorded():
    account = snapshot(at_ms=T1)
    tape = (
        ("classify", ("cand-1",), "reduce"),
        ("value", (T1, D_QUOTE), Decimal("1125")),
    )
    replay = RecordedAccounting({}, tape=tape)
    assert replay.classify(proposal(), tick_state(account)) == "reduce"
    assert replay.value(view(), quote_set(), T1) == Decimal("1125")


def test_a_call_that_diverges_from_the_tape_refuses():
    """D20's parity claim is precisely that the replay did not diverge."""
    replay = RecordedAccounting({}, tape=(("value", (T1, D_QUOTE), Decimal("1")),))
    with pytest.raises(ProductionError):
        replay.classify(proposal(), tick_state(snapshot(at_ms=T1)))


def test_a_call_past_the_end_of_the_tape_refuses():
    replay = RecordedAccounting({}, tape=(("value", (T1, D_QUOTE), Decimal("1")),))
    replay.value(view(), quote_set(), T1)
    with pytest.raises(ProductionError):
        replay.value(view(), quote_set(), T1)


@pytest.mark.parametrize(
    "tape",
    [
        (("value", (T1, D_QUOTE)),),
        (("mark", (T1,), Decimal("1")),),
        ("value",),
        ((("value",), (T1,), Decimal("1")),),
    ],
)
def test_a_malformed_tape_refuses_at_construction(tape):
    with pytest.raises(ProductionError):
        RecordedAccounting({}, tape=tape)


def test_two_replays_of_one_tape_answer_alike():
    tape = (("value", (T1, D_QUOTE), Decimal("42")),)
    assert RecordedAccounting({}, tape=tape).value(view(), quote_set(), T1) == (
        RecordedAccounting({}, tape=tape).value(view(), quote_set(), T1)
    )


def test_a_replay_recomputes_nothing_from_a_history():
    """Replay reads the tape; a strategy that recomputed could disagree with
    the recording and still call itself a parity run."""
    tape = (("value", (T1, D_QUOTE), Decimal("7")),)
    replay = RecordedAccounting({}, tape=tape)
    assert not [name for name in vars(replay) if "history" in name.lower()]
    assert replay.value(view(), quote_set(), T1) == Decimal("7")


def test_the_recorded_snapshot_is_the_recorded_account_state():
    account = snapshot(at_ms=T1)
    (requirement,) = requirements_for({"duration": "PT1H"}, at_ms=T1)
    tape = (("snapshot", (T1, (requirement.requirement_digest,)), account),)
    replay = RecordedAccounting({}, tape=tape)
    got = replay.snapshot(view(), Boom("executor"), quote_set(), T1, (requirement,), CAL)
    assert got is account


def test_the_account_state_is_frozen():
    """Everything downstream binds it; a mutable account is a permit that can
    change after it was minted."""
    account = snapshot(at_ms=T1)
    assert dataclasses.is_dataclass(account)
    with pytest.raises(dataclasses.FrozenInstanceError):
        account.asof_ms = T1 + 1
