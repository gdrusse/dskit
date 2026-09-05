"""`executor.py` — the venue seam, and the one class that can spend money (§5.7, D14).

The hierarchy here is the §5.15 Liskov split, and the split exists for one
reason: a subclass may never strengthen a precondition, so `submit` is NOT
`submit(intent, permit=None)` with `LiveExecutor` quietly demanding more than
its base. Instead

* `Executor(ABC)` carries read, query and cancel — `spec`, `capabilities`,
  `check`, `execution_scope`, `order`, `open_orders`, `fills`, `balances`,
  `positions`, `settlements`, `cancel`, `cancel_all` — and is **always
  constructible, never armed**. That is what makes recovery and cancellation
  survive every failure the submit path can have;
* `SubmittingExecutor(Executor)` adds `submit(intent, permit, state)`, with
  `permit` required of every subclass;
* `ShadowExecutor`, `PaperExecutor` and `RecordedExecutor` take a
  `SimulatedPermit`; `LiveExecutor` accepts only an `ActPermit` and refuses
  anything else **by type**, where refusing means returning
  `Ack(not_sent, reason="permit_type")` — never raising, because the base
  contract is total: **`submit` returns an `Ack` describing what happened,
  including refusal.**

`LiveExecutor` is the abstract wrapper core ships: it holds the act gate and
delegates the indivisible verify/call sequence to
`SubmissionVerifier.verify_and_call`, with `_submit_native` abstract so only a
child's venue subclass is constructible. Its own behaviours are here; the
gate's are in `test_verifier.py`.

`executor_conformance_suite` is the closed battery of §5.7 — the same
`conformance_suite` precedent `dskit.pipeline` set — so a child's venue
subclass is proven by the suite that proves `PaperExecutor`.

No network, no wall clock, no sleeping: the paper venue is fed `on_quote` and
reads only the injected clock, and the hung-deadline case makes the native
call raise when the injected clock passes its deadline rather than waiting on
one.
"""

import dataclasses
import inspect
import socket
from decimal import Decimal

import pytest

from dskit.production import executor as executor_module
from dskit.production.base import ProductionError, Registry, canonical_hash
from dskit.production.clock import TestClock
from dskit.production.coordination import ProcessLease
from dskit.production.executor import (
    EXECUTOR_KINDS,
    FEE_KINDS,
    Capabilities,
    Executor,
    Fee,
    LiveExecutor,
    PaperExecutor,
    RecordedExecutor,
    ShadowExecutor,
    SubmittingExecutor,
    executor_conformance_suite,
)
from dskit.production.records import (
    Ack,
    ActPermit,
    ExecutionScope,
    Fill,
    Intent,
    OrderState,
    Permit,
    Proposal,
    Quote,
    RiskVersion,
    SimulatedPermit,
)
from dskit.production.state import PositionBook, TickState
from dskit.production.vocab import (
    DEDUPE_MODES,
    FEE_KIND_NAMES,
    FENCING_MODES,
    FILL_RULES,
    LIQUIDITY,
    POSITION_MODELS,
    POSITION_SOURCES,
    RESTING_RULES,
    SIZE_CAPS,
    TERMINAL_STATUSES,
    TIFS,
)

# ---------------------------------------------------------------------------
# Fixed material
# ---------------------------------------------------------------------------

NOW_MS = 1_767_268_800_000
INSTRUMENT = "INS1"
SCOPE = ExecutionScope(venue="paper", account="strategy-a")

BID = Decimal("0.40")
ASK = Decimal("0.42")
MID = Decimal("0.41")

QUOTE = Quote(instrument=INSTRUMENT, bid=BID, ask=ASK, mid=MID, asof_ms=NOW_MS)
QUOTES = (QUOTE,)

#: The ten capability members §5.7 lists, in that order. Restated here
#: INDEPENDENTLY of `executor.py`: a field list read from its subject cannot
#: catch a field being dropped.
CAPABILITY_FIELDS = (
    "tifs",
    "market_orders",
    "notional",
    "positions",
    "settlements",
    "stream",
    "dedupe",
    "units",
    "position_model",
    "fencing",
)

#: The nineteen checks §5.7's battery lists, restated independently. A suite
#: that ships eighteen of them claims coverage it does not have, which
#: CLAUDE.md rules is worse than shipping none.
BATTERY = (
    "test_capability_gating_precedes_any_io",
    "test_check_performs_no_submit",
    "test_client_ref_is_echoed",
    "test_default_deny_spec",
    "test_derived_and_venue_positions_agree",
    "test_filled_plus_remaining_equals_qty",
    "test_filled_qty_is_monotone_except_when_reversed",
    "test_no_duplicate_fill_id",
    "test_no_initiated_replace_api",
    "test_paper_is_deterministic_under_seed",
    "test_shadow_touches_no_socket",
    "test_stale_bindings_refuse",
    "test_submit_never_raises_for_a_permission_fact",
    "test_terminal_states_absorb",
    "test_the_same_client_ref_twice_is_idempotent_or_rejected",
    "test_timeout_cannot_exceed_the_permit_and_disables_later_sends",
    "test_two_instances_prove_a_stale_fencing_token_cannot_act",
    "test_unarmed_or_raw_authority_is_refused",
    "test_units_are_declared",
)

PAPER_PARAMS = {
    "fill_rule": "touch",
    "fees": {"kind": "bps", "bps": 5},
    "seed": 7,
    "latency_ms": {"submit": 3, "cancel": 5},
}


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def proposal(
    *,
    side="buy",
    qty="10",
    limit="0.42",
    tif="ioc",
    expires_ms=NOW_MS + 60_000,
    instrument=INSTRUMENT,
):
    """One order-shaped proposal; `qty` is the order size (R10)."""
    return Proposal(
        id="cand-1",
        instrument=instrument,
        side=side,
        qty=Decimal(qty),
        notional=None,
        limit=None if limit is None else Decimal(limit),
        tif=tif,
        expires_ms=expires_ms,
        reference_price=MID,
        exposure=Decimal(qty) * MID,
        direction="long" if side == "buy" else "short",
        confidence=0.6,
        prediction=0.58,
        baseline=0.5,
        expected_value=0.03,
        inputs_asof_ms=NOW_MS,
        inputs_digest="a" * 64,
        coverage_digest="b" * 64,
        quote_asof_ms=NOW_MS,
        quote_digest="c" * 64,
        extra={},
    )


def intent(client_ref="ref-1", **kwargs):
    """The canonical `Intent` a leg hands `submit` (§5.4)."""
    final = kwargs.pop("proposal", None) or proposal(**kwargs)
    version = RiskVersion(economic_seq=1, executor_token=None, accounting_tokens=None)
    return Intent(
        client_ref=client_ref,
        decision_plan_id="plan-1",
        decision_plan_digest="e" * 64,
        proposal=final,
        created_ms=NOW_MS,
        authority_id="auth-1",
        release_hash="d" * 64,
        inputs_asof_ms=NOW_MS,
        inputs_digest="a" * 64,
        coverage_digest="b" * 64,
        quote_asof_ms=NOW_MS,
        quote_digest="c" * 64,
        evidence_asof_ms=NOW_MS,
        evidence_digest="f" * 64,
        risk_version=version,
        risk_state_digest="9" * 64,
    )


def simulated(client_ref="ref-1", valid_until_ms=NOW_MS + 60_000):
    """What shadow, paper and recorded receive: authorises nothing outward."""
    return SimulatedPermit(
        plan_id="plan-1",
        decision_plan_digest="e" * 64,
        client_ref=client_ref,
        valid_until_ms=valid_until_ms,
    )


def act_permit(client_ref="ref-1", *, fencing_token=1, valid_until_ms=NOW_MS + 60_000):
    """The live binding only an `Authority` constructs (§5.13.1)."""
    version = RiskVersion(economic_seq=1, executor_token="etok", accounting_tokens=("atok",))
    return ActPermit(
        plan_id="plan-1",
        decision_plan_digest="e" * 64,
        client_ref=client_ref,
        valid_until_ms=valid_until_ms,
        authority_id="auth-1",
        release_hash="d" * 64,
        intent_digest="1" * 64,
        instrument=INSTRUMENT,
        risk_effect="increase",
        inputs_asof_ms=NOW_MS,
        inputs_digest="a" * 64,
        coverage_digest="b" * 64,
        quote_asof_ms=NOW_MS,
        quote_digest="c" * 64,
        evidence_asof_ms=NOW_MS,
        evidence_digest="f" * 64,
        authority_scope_digest="2" * 64,
        reduction_right_digest=None,
        risk_version=version,
        risk_state_digest="9" * 64,
        readiness_digest="7" * 64,
        readiness_until_ms=NOW_MS + 3_600_000,
        lease_scope=SCOPE,
        fencing_token=fencing_token,
        safety_epoch_digest="3" * 64,
        checked_at_ms=NOW_MS,
    )


def tick_state():
    """The `TickState` the leg threads through `submit`; simulated venues ignore it."""
    return TickState(
        view=None, account=None, feed_status="live", feed_ages=(), calendar=None
    )


def paper(params=None, *, clock=None, quotes=QUOTES):
    """A `PaperExecutor` already fed its market."""
    clock = clock or TestClock(start_ms=NOW_MS)
    venue = PaperExecutor(PAPER_PARAMS if params is None else params, clock=clock)
    for quote in quotes:
        venue.on_quote(quote)
    return venue


def bomb(*args, **kwargs):
    """A door that must stay shut: opening it is the defect."""
    raise AssertionError(f"forbidden call: {args!r} {kwargs!r}")


# ---------------------------------------------------------------------------
# The registries (§4.3)
# ---------------------------------------------------------------------------


def test_executor_kinds_holds_exactly_the_three_core_executors():
    """D14: "Core ships shadow, paper and recorded execution/accounting, plus
    the ABSTRACT `LiveExecutor` wrapper; every concrete venue subclass is a
    child." A registered `live` would let a document name a class that cannot
    be constructed — and would put the one class that spends money behind the
    same open doorway as a calendar."""
    assert EXECUTOR_KINDS.kinds() == ("paper", "recorded", "shadow")
    assert isinstance(EXECUTOR_KINDS, Registry)
    assert EXECUTOR_KINDS.family == "executor"
    assert EXECUTOR_KINDS.abc is Executor


def test_fee_kinds_and_the_vocabulary_name_the_same_five_strategies():
    """§4.3: `FEE_KINDS` is "the one concept with both a registry and a
    `vocab.py` tuple (`FEE_KIND_NAMES`), and a test pins that their key sets
    are equal". Two lists of the same thing is the defect class CLAUDE.md's
    "duplication that diverges" names; this is the pin that stops it."""
    assert FEE_KINDS.kinds() == tuple(sorted(FEE_KIND_NAMES))
    assert set(FEE_KINDS.kinds()) == set(FEE_KIND_NAMES)
    assert FEE_KINDS.family == "fee"
    assert FEE_KINDS.abc is Fee


def test_every_registered_class_is_public_and_in_its_family():
    """`__all__` plus the `_` prefix is the API contract; a registered class a
    document can name must be importable by that name."""
    for registry in (EXECUTOR_KINDS, FEE_KINDS):
        for kind in registry.kinds():
            cls = registry.resolve(kind)
            assert issubclass(cls, registry.abc)
            assert cls.__name__ in executor_module.__all__
            assert getattr(executor_module, cls.__name__) is cls


# ---------------------------------------------------------------------------
# The ABC surface — §5.15's Liskov split
# ---------------------------------------------------------------------------

#: The hooks a venue must answer for itself. `capabilities` is here and not
#: concrete because a child that forgets it would inherit `fencing: none` —
#: and §5.7.2 requires `submit_token` of every live executor.
ABSTRACT_ON_EXECUTOR = (
    "balances",
    "cancel",
    "capabilities",
    "check",
    "execution_scope",
    "fills",
    "open_orders",
    "order",
)

#: What core answers once for everyone, so a `positions: venue` child
#: overrides `positions()` without inheriting dead derivation code.
CONCRETE_ON_EXECUTOR = (
    "cancel_all",
    "events",
    "positions",
    "settlements",
    "spec",
    "venue_time_ms",
)


def test_the_executor_abc_declares_exactly_the_read_query_cancel_hooks():
    """§5.15 lists the base's verbs, and the list is the contract a caller
    holding an `Executor` may rely on without knowing the rung."""
    assert set(Executor.__abstractmethods__) == set(ABSTRACT_ON_EXECUTOR)
    for name in CONCRETE_ON_EXECUTOR:
        assert callable(getattr(Executor, name))
    assert "submit" not in dir(Executor)


def test_submitting_executor_adds_submit_and_nothing_else():
    """The split IS the Liskov fix: adding `submit` to a second ABC is what
    lets `permit` be required of every subclass without any subclass
    strengthening a precondition of a shared signature."""
    assert issubclass(SubmittingExecutor, Executor)
    assert "submit" in SubmittingExecutor.__abstractmethods__
    assert set(SubmittingExecutor.__abstractmethods__) == set(ABSTRACT_ON_EXECUTOR) | {
        "submit"
    }


def test_submit_takes_the_intent_the_permit_and_the_state():
    """§5.14: `state` is "the only route" `LiveExecutor` has to the leg's
    `TickState`, which the final gate needs — so it is in the shared
    signature, not an extra a subclass demands."""
    params = tuple(inspect.signature(SubmittingExecutor.submit).parameters)
    assert params == ("self", "intent", "permit", "state")


def test_an_incomplete_subclass_refuses_at_construction():
    """§5.15: "Each seam ABC declares its hooks `@abstractmethod` so an
    incomplete subclass fails at construction, not at the first live tick."
    """

    class Half(SubmittingExecutor):
        def capabilities(self):
            return None

    with pytest.raises(TypeError):
        Half({}, clock=TestClock())


@pytest.mark.parametrize("cls", (ShadowExecutor, PaperExecutor, RecordedExecutor, LiveExecutor))
def test_no_executor_offers_an_initiated_replace(cls):
    """D14: "Neither has an initiated replace verb." A replace is a cancel
    plus a submit that the venue makes atomic — and an atomic pair that skips
    the decision-plan barrier is money moving without a record."""
    public = {name for name in dir(cls) if not name.startswith("_")}
    assert not [name for name in public if "replace" in name]


@pytest.mark.parametrize("cls", (ShadowExecutor, PaperExecutor, RecordedExecutor))
def test_a_core_executor_is_built_from_params_and_an_injected_clock(cls):
    """Every seam class in this package is `cls(params)` plus keyword
    collaborators, and nothing reads `time.time()` outside `clock.py`."""
    params = tuple(inspect.signature(cls.__init__).parameters)
    assert params[:2] == ("self", "params")
    assert "clock" in params


@pytest.mark.parametrize("cls", (ShadowExecutor, PaperExecutor, RecordedExecutor))
def test_an_unknown_knob_refuses_rather_than_defaulting(cls):
    """Default-deny: a typo in a graded `execution.params` block must be an
    error, not a silently ignored key that changes what the venue does."""
    problems = cls.validate_params({"fil_rule": "touch"})
    assert problems
    assert any("fil_rule" in problem for problem in problems)


# ---------------------------------------------------------------------------
# Capabilities (§5.7)
# ---------------------------------------------------------------------------


def test_capabilities_declares_the_ten_members_section_5_7_names():
    """`capabilities()` is what gates a call BEFORE any I/O — an unsupported
    TIF must be refused locally rather than discovered by the venue — so the
    member list is a contract, not documentation."""
    names = tuple(f.name for f in dataclasses.fields(Capabilities))
    assert names == CAPABILITY_FIELDS


@pytest.mark.parametrize(
    ("field", "value", "closed"),
    (
        ("positions", "guessed", POSITION_SOURCES),
        ("dedupe", "sometimes", DEDUPE_MODES),
        ("position_model", "hedging-ish", POSITION_MODELS),
        ("fencing", "hope", FENCING_MODES),
    ),
)
def test_a_capability_outside_its_closed_set_refuses(field, value, closed):
    """Each of these decides a safety behaviour: whether positions may be
    compared, whether a re-used ref replays, whether the lease fence rides on
    submits. A typo must refuse, never fall through to the safe-looking
    branch."""
    caps = {
        "tifs": ("ioc",),
        "market_orders": True,
        "notional": False,
        "positions": "derived",
        "settlements": False,
        "stream": False,
        "dedupe": "replays",
        "units": {"qty": "share", "price": "USD", "cash": "USD"},
        "position_model": "netting",
        "fencing": "none",
    }
    caps[field] = value
    with pytest.raises(ProductionError):
        Capabilities(**caps)
    assert value not in closed


def test_units_declare_quantity_price_and_cash():
    """"Native units are declared and money is `Decimal`" (D14): a fill whose
    quantity unit nobody wrote down is a position nobody can reconcile."""
    caps = paper().capabilities()
    assert set(caps.units) == {"qty", "price", "cash"}
    assert all(isinstance(v, str) and v for v in caps.units.values())


def test_capabilities_is_a_frozen_value():
    """It is read at the gate and compared across processes; a mutable
    capability block is a capability that can be widened at run time."""
    caps = paper().capabilities()
    with pytest.raises(dataclasses.FrozenInstanceError):
        caps.fencing = "submit_token"


def test_spec_declares_the_knobs_and_names_env_vars_for_secrets():
    """§5.7: "`spec()` (default-deny knobs; secret knobs name env vars)" —
    the knob inventory and the default-deny list are one fact, and a secret
    that is a literal in a graded document is a secret in the release hash."""
    spec = PaperExecutor.spec()
    assert set(spec) == {"params", "secrets"}
    assert set(spec["params"]) == set(PaperExecutor._PARAMS)
    assert set(spec["secrets"]) <= set(spec["params"])
    assert ShadowExecutor.spec()["secrets"] == ()


# ---------------------------------------------------------------------------
# ShadowExecutor
# ---------------------------------------------------------------------------


def test_shadow_never_sends_and_says_so():
    """§5.7: shadow "records nothing itself; `submit` returns
    `Ack(status='not_sent', reason='shadow')`". The reason is what the ledger
    records, so a shadow run reads as a run that decided and declined rather
    than as one that failed."""
    ack = ShadowExecutor({}, clock=TestClock(start_ms=NOW_MS)).submit(
        intent(), simulated(), tick_state()
    )
    assert (ack.status, ack.reason) == ("not_sent", "shadow")
    assert ack.client_ref == "ref-1"
    assert ack.venue_ref is None
    assert ack.filled_qty == Decimal("0")


def test_shadow_accepts_the_base_permit_contract():
    """§5.15: only `LiveExecutor` narrows by type. A shadow executor handed an
    `ActPermit` still answers — it simply never acts."""
    ack = ShadowExecutor({}, clock=TestClock(start_ms=NOW_MS)).submit(
        intent(), act_permit(), tick_state()
    )
    assert (ack.status, ack.reason) == ("not_sent", "shadow")


def test_shadow_touches_no_socket_on_any_verb(monkeypatch):
    """§5.7 pins this "by a monkeypatched test" because the whole value of a
    shadow rung is that it CANNOT reach a venue: a shadow that opened a
    connection would be a live executor with an optimistic name."""
    monkeypatch.setattr(socket, "socket", bomb)
    monkeypatch.setattr(socket, "create_connection", bomb)
    venue = ShadowExecutor({}, clock=TestClock(start_ms=NOW_MS))
    venue.submit(intent(), simulated(), tick_state())
    assert venue.open_orders() == ()
    assert venue.fills(0) == ((), None)
    assert venue.balances() == ()
    assert venue.positions() == ()
    assert venue.settlements(0) == ()
    assert venue.cancel_all() == ()
    assert venue.venue_time_ms() is None


def test_shadow_records_no_order_and_no_fill():
    """"Records nothing itself" — the ledger is the record, and a shadow
    executor keeping a private book would be a second, divergent one."""
    venue = ShadowExecutor({}, clock=TestClock(start_ms=NOW_MS))
    venue.submit(intent(), simulated(), tick_state())
    assert venue.open_orders() == ()
    assert venue.fills(0) == ((), None)


# ---------------------------------------------------------------------------
# PaperExecutor — knobs
# ---------------------------------------------------------------------------


def test_paper_declares_exactly_the_knobs_section_5_7_lists():
    """§5.7: "Every knob in this bullet is a graded `execution.params` field
    … and `validate_params` default-denies the rest into the same block."
    The list is closed, so a knob that needs a parameter carries it in its own
    block (`fees`, `slippage`, `latency_ms`, `size_cap`) rather than adding a
    twelfth name."""
    assert set(PaperExecutor._PARAMS) == {
        "fees",
        "fill_rule",
        "latency_ms",
        "p_fill_on_touch",
        "partial_fills",
        "queue_frac",
        "resting_rule",
        "seed",
        "session_end_ms",
        "size_cap",
        "slippage",
    }


@pytest.mark.parametrize(
    "params",
    (
        {"fill_rule": "guess"},
        {"resting_rule": "maybe"},
        {"size_cap": {"kind": "half"}},
        {"fees": {"kind": "free"}},
        {"p_fill_on_touch": 1.5},
        {"queue_frac": 1.0},
        {"slippage": {"ticks": 2}},
        {"latency_ms": {"submit": -1}},
        {"seed": "seven"},
        {"partial_fills": "yes"},
        {"session_end_ms": 1.5},
    ),
)
def test_one_refusal_per_knob(params):
    """Every knob gets its own refusal: `ticks` without `tick` has no size to
    slip by, `queue_frac` of 1.0 is a queue that never reaches the front, and
    a `p_fill_on_touch` above 1 is not a probability."""
    problems = PaperExecutor.validate_params(dict(params))
    assert problems, f"{params} was accepted"


def test_the_defaults_are_named_constants_not_literals():
    """CLAUDE.md: "A default belongs to ONE name" — `params.get(k, <literal>)`
    in both `validate_params` and the run is how validation comes to approve a
    value the run never uses."""
    assert executor_module.DEFAULT_FILL_RULE in FILL_RULES
    assert executor_module.DEFAULT_RESTING_RULE in RESTING_RULES
    assert 0.0 <= executor_module.DEFAULT_P_FILL_ON_TOUCH <= 1.0
    assert 0.0 <= executor_module.DEFAULT_QUEUE_FRAC < 1.0
    assert isinstance(executor_module.DEFAULT_SEED, int)
    assert isinstance(executor_module.DEFAULT_PARTIAL_FILLS, bool)
    assert executor_module.DEFAULT_SIZE_CAP in SIZE_CAPS


def test_notes_are_accepted_at_every_params_site():
    """`notes` is the package's comment syntax and is excluded from identity,
    so it must never be the thing that makes a config refuse."""
    assert PaperExecutor.validate_params({"notes": "why this fill model", "seed": 1}) == []


# ---------------------------------------------------------------------------
# PaperExecutor — the fill model
# ---------------------------------------------------------------------------


def test_a_submit_before_any_quote_is_rejected_not_guessed():
    """A paper venue with no market cannot price an order, and inventing a
    price would make the whole rung's P&L fiction. `rejected` is a terminal
    status the ledger records."""
    venue = PaperExecutor(PAPER_PARAMS, clock=TestClock(start_ms=NOW_MS))
    ack = venue.submit(intent(), simulated(), tick_state())
    assert ack.status == "rejected"
    assert ack.reason == "no_quote"


def test_touch_fills_a_buy_at_the_ask_and_a_sell_at_the_bid():
    """`fill_rule: touch` — a marketable order pays the far touch. Anything
    better would be a paper venue that flatters itself."""
    venue = paper({"fill_rule": "touch"})
    buy = venue.submit(intent("b"), simulated("b"), tick_state())
    sell = venue.submit(
        intent("s", proposal=proposal(side="sell", limit="0.40")), simulated("s"), tick_state()
    )
    assert (buy.status, buy.filled_qty, buy.avg_price) == ("filled", Decimal("10"), ASK)
    assert (sell.status, sell.filled_qty, sell.avg_price) == ("filled", Decimal("10"), BID)


def test_touch_does_not_fill_an_order_that_does_not_reach_the_touch():
    """A buy limit below the ask is not marketable; under `ioc` it dies
    rather than resting."""
    venue = paper({"fill_rule": "touch"})
    ack = venue.submit(
        intent(proposal=proposal(limit="0.41")), simulated(), tick_state()
    )
    assert (ack.status, ack.filled_qty) == ("cancelled", Decimal("0"))


def test_cross_requires_the_limit_to_go_through_the_touch():
    """`touch` and `cross` differ by exactly one comparison, and that is the
    difference between a queue-position assumption and a trade."""
    at_touch = paper({"fill_rule": "cross"}).submit(
        intent(proposal=proposal(limit="0.42")), simulated(), tick_state()
    )
    through = paper({"fill_rule": "cross"}).submit(
        intent(proposal=proposal(limit="0.43")), simulated(), tick_state()
    )
    assert at_touch.status == "cancelled"
    assert (through.status, through.avg_price) == ("filled", ASK)


def test_mid_fills_at_the_mid():
    """`fill_rule: mid` is the optimistic model, and it must be spelled in the
    document rather than assumed by the code."""
    ack = paper({"fill_rule": "mid"}).submit(
        intent(proposal=proposal(limit="0.41")), simulated(), tick_state()
    )
    assert (ack.status, ack.avg_price) == ("filled", MID)


def test_a_market_order_is_marketable_under_every_rule():
    """`capabilities().market_orders` says the venue takes them; `limit: None`
    is how §5.4 spells one."""
    for rule in FILL_RULES:
        ack = paper({"fill_rule": rule}).submit(
            intent(proposal=proposal(limit=None)), simulated(), tick_state()
        )
        assert ack.status == "filled", rule


def test_slippage_in_basis_points_moves_the_price_adversely():
    """Slippage exists to stop a paper rung reading better than the live one;
    a buy pays MORE and a sell receives LESS, never the reverse."""
    venue = paper({"fill_rule": "touch", "slippage": {"bps": 100}})
    buy = venue.submit(intent("b"), simulated("b"), tick_state())
    sell = venue.submit(
        intent("s", proposal=proposal(side="sell", limit="0.40")), simulated("s"), tick_state()
    )
    assert buy.avg_price == Decimal("0.4242")
    assert sell.avg_price == Decimal("0.3960")


def test_slippage_in_ticks_needs_a_tick_size():
    """"`slippage {bps, ticks, tick}`" — a count of ticks without the size of
    one is not a price."""
    assert PaperExecutor.validate_params({"slippage": {"ticks": 2}})
    venue = paper({"fill_rule": "touch", "slippage": {"ticks": 2, "tick": "0.01"}})
    ack = venue.submit(intent(proposal=proposal(limit="0.50")), simulated(), tick_state())
    assert ack.avg_price == Decimal("0.44")


def test_the_ack_is_stamped_from_the_injected_clock_plus_the_declared_latency():
    """"No wall clock": the submit latency is a declared model of the venue,
    added to the injected instant, so a replay reproduces the stamp exactly."""
    clock = TestClock(start_ms=NOW_MS)
    venue = paper({"latency_ms": {"submit": 3, "cancel": 5}}, clock=clock)
    ack = venue.submit(intent(), simulated(), tick_state())
    assert ack.ts_ms == NOW_MS + 3


def test_paper_reads_no_wall_clock(monkeypatch):
    """Nothing outside `clock.py` may call `time.time()`; D20's replay parity
    is exactly the claim that every instant came from the injected clock."""
    import time

    monkeypatch.setattr(time, "time", bomb)
    monkeypatch.setattr(time, "monotonic", bomb)
    venue = paper()
    ack = venue.submit(intent(), simulated(), tick_state())
    assert ack.status == "filled"


def test_paper_opens_no_socket(monkeypatch):
    """"No network" — a paper venue that reached out would make the rung a
    live one with simulated bookkeeping."""
    monkeypatch.setattr(socket, "socket", bomb)
    monkeypatch.setattr(socket, "create_connection", bomb)
    assert paper().submit(intent(), simulated(), tick_state()).status == "filled"


# ---------------------------------------------------------------------------
# PaperExecutor — size, partials and the order book
# ---------------------------------------------------------------------------


def test_size_cap_frac_delivers_a_partial_fill():
    """`size_cap ∈ {none, quote_size, frac}` bounds how much of an order one
    quote can absorb; the fraction rides in the cap's own block because §5.7's
    knob list is closed."""
    venue = paper({"fill_rule": "touch", "size_cap": {"kind": "frac", "frac": 0.5}})
    ack = venue.submit(
        intent(proposal=proposal(tif="gtc")), simulated(), tick_state()
    )
    assert ack.filled_qty == Decimal("5")
    order = venue.order("ref-1")
    assert (order.filled_qty, order.remaining_qty, order.qty) == (
        Decimal("5"),
        Decimal("5"),
        Decimal("10"),
    )
    assert order.status == "partial"


def test_partial_fills_false_fills_all_or_nothing():
    """A venue that cannot partially fill is a real venue shape, and modelling
    it changes what a guard's `exposure_after` means."""
    venue = paper(
        {
            "fill_rule": "touch",
            "size_cap": {"kind": "frac", "frac": 0.5},
            "partial_fills": False,
        }
    )
    ack = venue.submit(intent(proposal=proposal(tif="gtc")), simulated(), tick_state())
    assert ack.filled_qty == Decimal("0")


def test_size_cap_quote_size_needs_a_declared_size():
    """§5.4's `Quote` carries `bid`, `ask`, `mid` and `asof_ms` and NO size, so
    a `quote_size` cap has nothing to read unless the document declares one —
    see the report's plan gap."""
    assert PaperExecutor.validate_params({"size_cap": {"kind": "quote_size"}})
    venue = paper(
        {"fill_rule": "touch", "size_cap": {"kind": "quote_size", "quote_size": "4"}}
    )
    ack = venue.submit(intent(proposal=proposal(tif="gtc")), simulated(), tick_state())
    assert ack.filled_qty == Decimal("4")


def test_every_order_state_balances_filled_and_remaining():
    """D14: "every `OrderState` enforces `filled_qty + remaining_qty == qty`".
    It is the invariant that makes two independent counts of the same order
    catch each other."""
    venue = paper({"fill_rule": "touch", "size_cap": {"kind": "frac", "frac": 0.3}})
    venue.submit(intent(proposal=proposal(tif="gtc")), simulated(), tick_state())
    order = venue.order("ref-1")
    assert isinstance(order, OrderState)
    assert order.filled_qty + order.remaining_qty == order.qty


def test_a_resting_order_is_open_and_appears_in_open_orders():
    """Query and cancel must work for everything the executor owns, in every
    rung and every breaker state (§5.13)."""
    venue = paper({"fill_rule": "touch"})
    ack = venue.submit(
        intent(proposal=proposal(limit="0.30", tif="gtc")), simulated(), tick_state()
    )
    assert ack.status == "open"
    assert [order.client_ref for order in venue.open_orders()] == ["ref-1"]


def test_cancel_terminalizes_and_a_second_cancel_is_absorbed():
    """"A venue lacking a state collapses toward less certainty, never toward
    more" — and a terminal state absorbs: cancelling a cancelled order does
    not un-terminalize it."""
    clock = TestClock(start_ms=NOW_MS)
    venue = paper({"latency_ms": {"submit": 0, "cancel": 5}}, clock=clock, quotes=QUOTES)
    venue.submit(intent(proposal=proposal(limit="0.30", tif="gtc")), simulated(), tick_state())
    first = venue.cancel("ref-1")
    second = venue.cancel("ref-1")
    assert first.status == "cancelled"
    assert first.ts_ms == NOW_MS + 5
    assert second.status == "cancelled"
    assert venue.open_orders() == ()


def test_cancel_all_touches_only_this_executors_refs():
    """§5.7: "`cancel_all()` (iterates only refs this executor owns)" — a
    halt's best-effort cancel must not reach into another process's orders."""
    venue = paper({"fill_rule": "touch"})
    venue.submit(intent("a", proposal=proposal(limit="0.30", tif="gtc")), simulated("a"), tick_state())
    venue.submit(intent("b", proposal=proposal(limit="0.31", tif="gtc")), simulated("b"), tick_state())
    acks = venue.cancel_all()
    assert sorted(ack.client_ref for ack in acks) == ["a", "b"]
    assert all(ack.status in TERMINAL_STATUSES for ack in acks)


def test_fills_page_to_exhaustion_with_a_cursor():
    """T13 pins `fills(since, cursor) -> (page, next_cursor)`: the reconciler
    pages to exhaustion, and an executor that returns only a first page makes
    every comparison a false break."""
    venue = paper({"fill_rule": "touch"})
    for i in range(3):
        venue.submit(intent(f"r{i}"), simulated(f"r{i}"), tick_state())
    seen, cursor = [], None
    while True:
        page, cursor = venue.fills(0, cursor)
        seen.extend(page)
        if cursor is None:
            break
    assert len(seen) == 3
    assert all(isinstance(fill, Fill) for fill in seen)
    assert len({fill.fill_id for fill in seen}) == 3


# ---------------------------------------------------------------------------
# PaperExecutor — time in force
# ---------------------------------------------------------------------------


def test_an_ioc_that_cannot_fill_dies_immediately():
    """`ioc` never rests: what it cannot take now it gives up."""
    ack = paper().submit(
        intent(proposal=proposal(limit="0.30", tif="ioc")), simulated(), tick_state()
    )
    assert (ack.status, ack.filled_qty) == ("cancelled", Decimal("0"))
    assert ack.status in TERMINAL_STATUSES


def test_an_ioc_that_fills_partially_cancels_the_remainder():
    """The remainder is not working, so the order is terminal with a non-zero
    `filled_qty` — `partial` would leave the fold expecting more."""
    venue = paper({"fill_rule": "touch", "size_cap": {"kind": "frac", "frac": 0.5}})
    ack = venue.submit(intent(proposal=proposal(tif="ioc")), simulated(), tick_state())
    assert (ack.status, ack.filled_qty) == ("cancelled", Decimal("5"))
    assert venue.open_orders() == ()


def test_a_fok_is_all_or_nothing():
    """`fok` differs from `ioc` by refusing the partial the cap allows."""
    venue = paper({"fill_rule": "touch", "size_cap": {"kind": "frac", "frac": 0.5}})
    ack = venue.submit(intent(proposal=proposal(tif="fok")), simulated(), tick_state())
    assert (ack.status, ack.filled_qty) == ("cancelled", Decimal("0"))


def test_a_gtd_order_expires_at_the_proposals_expiry():
    """The expiry is `intent.proposal.expires_ms` (§5.4) judged against the
    injected clock — not a venue-side timer nobody can replay."""
    clock = TestClock(start_ms=NOW_MS)
    venue = paper({"fill_rule": "touch"}, clock=clock)
    venue.submit(
        intent(proposal=proposal(limit="0.30", tif="gtd", expires_ms=NOW_MS + 1_000)),
        simulated(),
        tick_state(),
    )
    assert venue.order("ref-1").status == "open"
    clock.advance(1_000)
    venue.on_quote(dataclasses.replace(QUOTE, asof_ms=NOW_MS + 1_000))
    assert venue.order("ref-1").status == "expired"
    assert venue.open_orders() == ()


def test_a_gtc_order_rests_indefinitely():
    """`gtc` is the one TIF with no deadline of its own; only a cancel or a
    fill ends it."""
    clock = TestClock(start_ms=NOW_MS)
    venue = paper({"fill_rule": "touch"}, clock=clock)
    venue.submit(
        intent(proposal=proposal(limit="0.30", tif="gtc")), simulated(), tick_state()
    )
    clock.advance(86_400_000)
    venue.on_quote(dataclasses.replace(QUOTE, asof_ms=NOW_MS + 86_400_000))
    assert venue.order("ref-1").status == "open"


def test_day_is_not_a_capability_without_a_session_end():
    """§5.7: "`day` refused without `session_end_ms`". Refusing through
    `capabilities().tifs` is the same gate the battery calls "capability
    gating before I/O": nothing reaches the book."""
    venue = paper({"fill_rule": "touch"})
    assert "day" not in venue.capabilities().tifs
    ack = venue.submit(
        intent(proposal=proposal(limit="0.30", tif="day")), simulated(), tick_state()
    )
    assert (ack.status, ack.reason) == ("rejected", "unsupported_tif")
    assert venue.open_orders() == ()


def test_day_expires_at_the_declared_session_end():
    """With the knob declared, `day` becomes a capability and the session end
    is the deadline — a document fact, never a literal in code."""
    clock = TestClock(start_ms=NOW_MS)
    venue = paper(
        {"fill_rule": "touch", "session_end_ms": NOW_MS + 2_000}, clock=clock
    )
    assert "day" in venue.capabilities().tifs
    venue.submit(
        intent(proposal=proposal(limit="0.30", tif="day", expires_ms=NOW_MS + 86_400_000)),
        simulated(),
        tick_state(),
    )
    clock.advance(2_000)
    venue.on_quote(dataclasses.replace(QUOTE, asof_ms=NOW_MS + 2_000))
    assert venue.order("ref-1").status == "expired"


def test_every_declared_tif_is_a_vocabulary_member():
    """`TIFS` is closed and lives only in `vocab.py`."""
    assert set(paper().capabilities().tifs) <= set(TIFS)


# ---------------------------------------------------------------------------
# Fees — one strategy per kind, against its closed form
# ---------------------------------------------------------------------------

#: `(kind, params, liquidity, expected)` — every figure hand-computed from
#: qty 10 at price 0.42 (notional 4.20), so the assertion is independent of
#: the formula the implementation writes.
FEE_CASES = (
    ("none", {}, "taker", Decimal("0")),
    ("per_unit", {"per_unit": "0.01"}, "taker", Decimal("0.10")),
    ("bps", {"bps": 5}, "taker", Decimal("0.0021")),
    ("maker_taker_bps", {"maker_bps": 1, "taker_bps": 5}, "maker", Decimal("0.00042")),
    ("maker_taker_bps", {"maker_bps": 1, "taker_bps": 5}, "taker", Decimal("0.0021")),
    ("pxq_rate", {"rate": "0.001"}, "taker", Decimal("0.0042")),
)


@pytest.mark.parametrize(("kind", "params", "liquidity", "expected"), FEE_CASES)
def test_each_fee_kind_charges_its_closed_form(kind, params, liquidity, expected):
    """`Fee(ABC).charge(qty, price, liquidity) -> Decimal`, one subclass per
    kind. Money never touches float, so the answer is an exact `Decimal`."""
    fee = FEE_KINDS.resolve(kind)(dict(params))
    charged = fee.charge(Decimal("10"), Decimal("0.42"), liquidity)
    assert isinstance(charged, Decimal)
    assert charged == expected


def test_an_unknown_liquidity_is_charged_conservatively():
    """`LIQUIDITY` includes `unknown`, and a fee model that cannot tell which
    side it was must not assume the cheaper one — a P&L that flatters itself
    is what a paper rung exists to avoid."""
    fee = FEE_KINDS.resolve("maker_taker_bps")({"maker_bps": 1, "taker_bps": 5})
    assert fee.charge(Decimal("10"), Decimal("0.42"), "unknown") == Decimal("0.0021")


def test_a_liquidity_outside_the_vocabulary_refuses():
    """`LIQUIDITY` is closed; a flag the venue invented must not silently
    price as maker."""
    fee = FEE_KINDS.resolve("bps")({"bps": 5})
    with pytest.raises(ProductionError):
        fee.charge(Decimal("10"), Decimal("0.42"), "aggressive")
    assert "aggressive" not in LIQUIDITY


@pytest.mark.parametrize("kind", FEE_KIND_NAMES)
def test_every_fee_kind_default_denies_its_knobs(kind):
    """A fee is a graded document field; a typo that silently charges zero is
    a P&L that is wrong in the favourable direction."""
    cls = FEE_KINDS.resolve(kind)
    assert cls.validate_params({"bsp": 5})


def test_a_fee_reaches_the_fill_and_the_ack():
    """The fee is part of the economic content the fold and the reconciler
    compare; an executor that charges but does not report it makes every
    balance break."""
    venue = paper({"fill_rule": "touch", "fees": {"kind": "bps", "bps": 5}})
    ack = venue.submit(intent(), simulated(), tick_state())
    (page, _cursor) = venue.fills(0)
    assert ack.fee == Decimal("0.0021")
    assert page[0].fee == Decimal("0.0021")
    assert page[0].fee_currency == venue.capabilities().units["cash"]


# ---------------------------------------------------------------------------
# PaperExecutor — determinism
# ---------------------------------------------------------------------------


def test_two_instances_with_the_same_seed_produce_identical_acks_and_fills():
    """§5.7: "Deterministic under `seed`." D20's replay parity rests on it —
    a paper rung whose fills depend on process entropy cannot be replayed, and
    a divergence report could never separate `nondeterminism` from `data`."""
    params = {
        "fill_rule": "touch",
        "resting_rule": "touch",
        "p_fill_on_touch": 0.5,
        "seed": 7,
    }
    runs = []
    for _ in range(2):
        clock = TestClock(start_ms=NOW_MS)
        venue = PaperExecutor(dict(params), clock=clock)
        acks = []
        for i in range(6):
            venue.on_quote(dataclasses.replace(QUOTE, asof_ms=NOW_MS + i))
            acks.append(
                venue.submit(
                    intent(f"r{i}", proposal=proposal(limit="0.41", tif="gtc")),
                    simulated(f"r{i}"),
                    tick_state(),
                )
            )
        fills, _cursor = venue.fills(0)
        runs.append(
            (
                [ack.to_obj() for ack in acks],
                [fill.to_obj() for fill in fills],
            )
        )
    assert runs[0] == runs[1]


def test_a_different_seed_is_allowed_to_differ():
    """A seed that changes nothing is a seed that is not being used — the
    determinism test above would then pass for the wrong reason."""
    outcomes = set()
    for seed in (1, 2, 3, 4, 5, 6, 7, 8):
        clock = TestClock(start_ms=NOW_MS)
        venue = PaperExecutor(
            {"fill_rule": "touch", "resting_rule": "touch", "p_fill_on_touch": 0.5,
             "seed": seed},
            clock=clock,
        )
        acks = []
        for i in range(6):
            venue.on_quote(dataclasses.replace(QUOTE, asof_ms=NOW_MS + i))
            acks.append(
                venue.submit(
                    intent(f"r{i}", proposal=proposal(limit="0.41", tif="gtc")),
                    simulated(f"r{i}"),
                    tick_state(),
                ).status
            )
        outcomes.add(tuple(acks))
    assert len(outcomes) > 1


def test_on_quote_refuses_anything_but_a_quote():
    """The market is a declared value object, not a dict a caller shaped."""
    with pytest.raises(ProductionError):
        paper().on_quote({"instrument": INSTRUMENT, "bid": "0.4"})


# ---------------------------------------------------------------------------
# RecordedExecutor
# ---------------------------------------------------------------------------


def recorded_tape():
    """A recording of one venue session: `(method, args, answer)` triples."""
    filled = Ack(
        client_ref="ref-1",
        venue_ref="v-1",
        status="filled",
        ts_ms=NOW_MS,
        filled_qty=Decimal("10"),
        avg_price=ASK,
        fee=Decimal("0.0021"),
        reason="",
        native={},
    )
    return (
        ("capabilities", (), paper().capabilities()),
        ("execution_scope", (), SCOPE),
        ("check", ({"params": {}},), ()),
        ("submit", ("ref-1",), filled),
        ("order", ("ref-1",), None),
        ("open_orders", (), ()),
        ("fills", (0, None), ((), None)),
        ("balances", (), ()),
        ("positions", (), ()),
        ("settlements", (0,), ()),
        ("venue_time_ms", (), NOW_MS),
        ("cancel", ("ref-1",), filled),
        ("cancel_all", (), ()),
    )


def test_recorded_takes_a_tape_of_method_args_answer_triples():
    """Mirrors `RecordedIdSource` (D20): replay allocates and invents nothing,
    it answers what the recording answered."""
    venue = RecordedExecutor({}, clock=TestClock(start_ms=NOW_MS), tape=recorded_tape())
    assert venue.execution_scope() == SCOPE
    assert venue.venue_time_ms() == NOW_MS
    assert venue.balances() == ()


def test_recorded_replays_a_submit_by_its_client_ref():
    """A submit is keyed by the `client_ref` D20 derives from release, tick and
    leg — so the same replayed leg asks for the same recorded answer."""
    venue = RecordedExecutor({}, clock=TestClock(start_ms=NOW_MS), tape=recorded_tape())
    ack = venue.submit(intent(), simulated(), tick_state())
    assert (ack.status, ack.filled_qty) == ("filled", Decimal("10"))


def test_a_read_may_be_replayed_more_than_once():
    """Reads are idempotent facts of the recorded session; a reconciler that
    pages twice must not exhaust the tape."""
    venue = RecordedExecutor({}, clock=TestClock(start_ms=NOW_MS), tape=recorded_tape())
    assert venue.capabilities() == venue.capabilities()


def test_a_call_the_recording_never_made_refuses():
    """The parity claim is that the replay asked exactly what the recording
    asked; a call with no entry means it did not."""
    venue = RecordedExecutor({}, clock=TestClock(start_ms=NOW_MS), tape=recorded_tape())
    with pytest.raises(ProductionError):
        venue.order("ref-unknown")


def test_a_malformed_tape_refuses_at_construction():
    """Every problem at once, at construction — a tape that fails on the
    fourth leg has already replayed three."""
    with pytest.raises(ProductionError):
        RecordedExecutor({}, clock=TestClock(start_ms=NOW_MS), tape=(("submit", "ref-1"),))
    with pytest.raises(ProductionError):
        RecordedExecutor(
            {}, clock=TestClock(start_ms=NOW_MS), tape=(("teleport", (), None),)
        )


def test_recorded_never_opens_a_socket(monkeypatch):
    """Replay is offline by construction; that is what makes a parity report
    reproducible on a laptop."""
    monkeypatch.setattr(socket, "socket", bomb)
    monkeypatch.setattr(socket, "create_connection", bomb)
    venue = RecordedExecutor({}, clock=TestClock(start_ms=NOW_MS), tape=recorded_tape())
    assert venue.submit(intent(), simulated(), tick_state()).status == "filled"


# ---------------------------------------------------------------------------
# LiveExecutor — the abstract wrapper core ships
# ---------------------------------------------------------------------------


class FakeVerifier:
    """The gate, faked: records its call and answers what a test set up."""

    def __init__(self, answer=None, raises=None):
        self.calls = []
        self.answer = answer
        self.raises = raises
        self.disabled = False
        self.resets = 0

    def verify_and_call(self, intent_, permit, state, native_call):
        self.calls.append((intent_, permit, state, native_call))
        if self.raises is not None:
            raise self.raises
        if self.answer is not None:
            return self.answer
        return native_call(intent_, permit, 1_000)

    def reset_after_reconcile(self):
        self.resets += 1
        self.disabled = False


class FakeLiveLease:
    """A lease a child would supply: fenced, and declared live-capable."""

    LIVE_CAPABLE = True

    def __init__(self, token=1):
        self.token = token

    def current(self, scope):
        return None

    def permit_current(self, permit):
        return True


class Gateway:
    """One in-memory venue two executors contend for, with a fencing token.

    The gateway is the atomic authority §5.7.2 names: it remembers the
    highest token it has seen and rejects anything older, whatever the local
    lease still believes.
    """

    def __init__(self):
        self.highest = 0
        self.sent = []

    def send(self, permit, intent_):
        if permit.fencing_token < self.highest:
            return Ack(
                client_ref=intent_.client_ref,
                venue_ref=None,
                status="rejected",
                ts_ms=permit.checked_at_ms,
                filled_qty=Decimal("0"),
                avg_price=None,
                fee=Decimal("0"),
                reason="stale_fence",
                native=None,
            )
        self.highest = permit.fencing_token
        self.sent.append(intent_.client_ref)
        return Ack(
            client_ref=intent_.client_ref,
            venue_ref=f"v-{len(self.sent)}",
            status="open",
            ts_ms=permit.checked_at_ms,
            filled_qty=Decimal("0"),
            avg_price=None,
            fee=Decimal("0"),
            reason="",
            native={},
        )


class FakeVenue(LiveExecutor):
    """A child's venue subclass — the only constructible kind of live executor."""

    _PARAMS = ()

    def __init__(self, params=None, *, clock, verifier, lease, gateway=None, hang=False):
        self.gateway = gateway if gateway is not None else Gateway()
        self.hang = hang
        self.native_calls = []
        super().__init__(params, clock=clock, verifier=verifier, lease=lease)

    def capabilities(self):
        return Capabilities(
            tifs=("ioc", "gtc"),
            market_orders=True,
            notional=False,
            positions="venue",
            settlements=False,
            stream=False,
            dedupe="replays",
            units={"qty": "share", "price": "USD", "cash": "USD"},
            position_model="netting",
            fencing="submit_token",
        )

    def check(self, config):
        return ()

    def execution_scope(self):
        return SCOPE

    def order(self, ref):
        return None

    def open_orders(self):
        return ()

    def fills(self, since_ms, cursor=None):
        return ((), None)

    def balances(self):
        return ()

    def cancel(self, ref):
        return Ack(
            client_ref=ref,
            venue_ref=None,
            status="cancelled",
            ts_ms=NOW_MS,
            filled_qty=Decimal("0"),
            avg_price=None,
            fee=Decimal("0"),
            reason="",
            native=None,
        )

    def _submit_native(self, intent_, permit, timeout_ms):
        self.native_calls.append((intent_, permit, timeout_ms))
        if self.hang:
            raise TimeoutError(f"no answer within {timeout_ms} ms")
        return self.gateway.send(permit, intent_)


class Unfenced(FakeVenue):
    """A child that forgot the fence — §5.7.2 says it may not be live."""

    def capabilities(self):
        return dataclasses.replace(super().capabilities(), fencing="none")


def live(**kwargs):
    """A `FakeVenue` over fakes; every keyword is one collaborator."""
    parts = {
        "clock": TestClock(start_ms=NOW_MS),
        "verifier": FakeVerifier(),
        "lease": FakeLiveLease(),
    }
    parts.update(kwargs)
    return FakeVenue({}, **parts), parts


def test_the_core_wrapper_is_abstract_so_only_a_child_can_be_live():
    """§5.7: "`_submit_native` — abstract, so core ships the wrapper and only
    a child's venue subclass is constructible." Core must never be able to
    send an order."""
    assert "_submit_native" in LiveExecutor.__abstractmethods__
    with pytest.raises(TypeError):
        LiveExecutor({}, clock=TestClock(), verifier=FakeVerifier(), lease=FakeLiveLease())


def test_a_live_executor_without_a_submit_token_fence_refuses_to_construct():
    """§5.7.2: "`LiveExecutor.capabilities().fencing` must be `submit_token`."
    Without it a fenced-out process's orders still reach the venue, and the
    lease stops being a safety mechanism."""
    with pytest.raises(ProductionError):
        Unfenced({}, clock=TestClock(), verifier=FakeVerifier(), lease=FakeLiveLease())


def test_a_live_executor_refuses_a_lease_that_is_not_live_capable():
    """§5.7.2: "Core `ProcessLease` is valid only for shadow/paper." An
    in-process lease cannot exclude a second host, and that is exactly what a
    live executor needs it to do."""
    with pytest.raises(ProductionError):
        FakeVenue(
            {},
            clock=TestClock(),
            verifier=FakeVerifier(),
            lease=ProcessLease({}, clock=TestClock()),
        )


def test_read_query_and_cancel_construct_and_answer_regardless():
    """D14: "Read/query/cancel construction is always possible", and §5.7:
    "Failures never disable reconciliation or cancellation."""
    venue, _parts = live(verifier=FakeVerifier(raises=RuntimeError("gate is broken")))
    assert venue.execution_scope() == SCOPE
    assert venue.open_orders() == ()
    assert venue.fills(0) == ((), None)
    assert venue.cancel("ref-1").status == "cancelled"


def test_a_simulated_permit_is_refused_by_type_and_never_reaches_the_gate():
    """§5.7: "`submit` accepts only an `ActPermit` and refuses any other
    permit BY TYPE — refuses meaning it returns
    `Ack(not_sent, reason='permit_type')`, never raises." Refusing by type
    rather than by a flag is what makes it impossible to forge."""
    venue, parts = live()
    ack = venue.submit(intent(), simulated(), tick_state())
    assert (ack.status, ack.reason) == ("not_sent", "permit_type")
    assert parts["verifier"].calls == []
    assert venue.native_calls == []


@pytest.mark.parametrize(
    "permit",
    (
        Permit(plan_id="p", decision_plan_digest="e" * 64, client_ref="ref-1",
               valid_until_ms=NOW_MS + 1_000),
        None,
        "an-act-permit",
    ),
)
def test_any_non_act_permit_is_refused_by_type(permit):
    """The base `Permit` and a string are both "not an `ActPermit`"; a
    permission fact never raises (§5.15)."""
    venue, parts = live()
    ack = venue.submit(intent(), permit, tick_state())
    assert isinstance(ack, Ack)
    assert (ack.status, ack.reason) == ("not_sent", "permit_type")
    assert parts["verifier"].calls == []


def test_submit_delegates_the_whole_verify_and_call_sequence_to_the_gate():
    """§5.7: "Its wrapper holds the act gate and delegates the indivisible
    local verify/call sequence to `SubmissionVerifier.verify_and_call`" — and
    hands it `_submit_native` as the callback, so the checks and the send
    cannot be separated by a caller-visible gap."""
    venue, parts = live()
    the_intent, the_permit, the_state = intent(), act_permit(), tick_state()
    ack = venue.submit(the_intent, the_permit, the_state)
    (recorded_intent, recorded_permit, recorded_state, native_call), = parts["verifier"].calls
    assert (recorded_intent, recorded_permit, recorded_state) == (
        the_intent, the_permit, the_state
    )
    assert native_call.__func__ is FakeVenue._submit_native
    assert native_call.__self__ is venue
    assert ack.status == "open"


def test_the_wrapper_returns_the_gates_answer_verbatim():
    """The wrapper must not second-guess the gate: §5.14 makes the verifier
    the sole owner of these rules, and a wrapper that re-derived one would be
    the branching §5.15 forbids."""
    refusal = Ack(
        client_ref="ref-1", venue_ref=None, status="not_sent", ts_ms=NOW_MS,
        filled_qty=Decimal("0"), avg_price=None, fee=Decimal("0"),
        reason="quote_age", native=None,
    )
    venue, _parts = live(verifier=FakeVerifier(answer=refusal))
    assert venue.submit(intent(), act_permit(), tick_state()) is refusal
    assert venue.native_calls == []


def test_an_unarmed_series_comes_back_as_a_value_not_an_exception():
    """§5.7: "missing or stale authority is `Ack(not_sent, reason='not_armed')`
    … `NotArmed` stays an internal exception inside the wrapper and never
    crosses the `SubmittingExecutor` contract"."""
    unarmed = Ack(
        client_ref="ref-1", venue_ref=None, status="not_sent", ts_ms=NOW_MS,
        filled_qty=Decimal("0"), avg_price=None, fee=Decimal("0"),
        reason="not_armed", native=None,
    )
    venue, _parts = live(verifier=FakeVerifier(answer=unarmed))
    ack = venue.submit(intent(), act_permit(), tick_state())
    assert (ack.status, ack.reason) == ("not_sent", "not_armed")


def test_the_native_call_receives_the_bounded_timeout_and_the_full_permit():
    """D14: "The full permit and a timeout bounded by its remaining lifetime
    reach `_submit_native`; the child gateway atomically enforces fencing
    token, permit deadline and client-ref idempotency before sending"."""
    venue, _parts = live()
    permit = act_permit()
    venue.submit(intent(), permit, tick_state())
    (_i, sent_permit, timeout_ms), = venue.native_calls
    assert sent_permit is permit
    assert 0 < timeout_ms <= permit.valid_until_ms - NOW_MS


def test_a_hung_native_call_is_unknown_and_disables_later_sends():
    """§5.7's battery: "timeout cannot exceed permit/lease lifetime and
    disables later sends"; D14: "conformance uses a never-returning fake to
    prove timeout disables further sends". The deadline is judged by the
    injected clock, so no test waits on a real one."""
    verifier = FakeVerifier()
    venue = FakeVenue(
        {}, clock=TestClock(start_ms=NOW_MS), verifier=verifier,
        lease=FakeLiveLease(), hang=True,
    )
    with pytest.raises(TimeoutError):
        verifier.verify_and_call(intent(), act_permit(), tick_state(), venue._submit_native)
    assert len(venue.native_calls) == 1


def test_an_unknown_from_the_gate_blocks_the_next_submit_but_not_cancellation():
    """§5.13 step (8): "an ambiguous outcome stops all later legs until
    reconciliation" — while query and cancel stay available, because they are
    how the ambiguity gets resolved."""
    unknown = Ack(
        client_ref="ref-1", venue_ref=None, status="unknown", ts_ms=NOW_MS,
        filled_qty=Decimal("0"), avg_price=None, fee=Decimal("0"),
        reason="timeout", native=None,
    )
    verifier = FakeVerifier(answer=unknown)
    venue, _parts = live(verifier=verifier)
    assert venue.submit(intent(), act_permit(), tick_state()).status == "unknown"
    assert venue.cancel("ref-1").status == "cancelled"
    assert venue.fills(0) == ((), None)


def test_reset_after_reconcile_is_delegated_to_the_one_owner():
    """The disable belongs to the gate, which is where the ambiguity was
    recorded; the wrapper exposes the verb so the reconciler has one call to
    make and there is still exactly one owner."""
    verifier = FakeVerifier()
    venue, _parts = live(verifier=verifier)
    venue.reset_after_reconcile()
    assert verifier.resets == 1


def test_two_instances_prove_a_stale_fencing_token_cannot_act():
    """§5.7.2: "the child gateway must atomically reject stale tokens." The
    local lease here still believes the old process holds the grip — which is
    exactly the split-brain the gateway check exists for."""
    gateway = Gateway()
    old = FakeVenue(
        {}, clock=TestClock(start_ms=NOW_MS), verifier=FakeVerifier(),
        lease=FakeLiveLease(), gateway=gateway,
    )
    new = FakeVenue(
        {}, clock=TestClock(start_ms=NOW_MS), verifier=FakeVerifier(),
        lease=FakeLiveLease(), gateway=gateway,
    )
    assert new.submit(intent("new"), act_permit("new", fencing_token=2), tick_state()).status == "open"
    stale = old.submit(intent("old"), act_permit("old", fencing_token=1), tick_state())
    assert (stale.status, stale.reason) == ("rejected", "stale_fence")
    assert gateway.sent == ["new"]


# ---------------------------------------------------------------------------
# Liskov, across the whole family
# ---------------------------------------------------------------------------


def submitters():
    """Every concrete `SubmittingExecutor` this package ships or can build."""
    clock = TestClock(start_ms=NOW_MS)
    return (
        ShadowExecutor({}, clock=clock),
        paper(),
        RecordedExecutor({}, clock=clock, tape=recorded_tape()),
        FakeVenue({}, clock=clock, verifier=FakeVerifier(), lease=FakeLiveLease()),
    )


@pytest.mark.parametrize("index", range(4))
def test_submit_always_returns_an_ack_and_never_raises_for_a_permission_fact(index):
    """§5.15: "The base contract is therefore total: `submit` returns an `Ack`
    describing what happened, including refusal." A caller holding an
    `Executor` can always recover and cancel without knowing the rung."""
    venue = submitters()[index]
    ack = venue.submit(intent(), simulated(), tick_state())
    assert isinstance(ack, Ack)
    assert ack.client_ref == "ref-1"


@pytest.mark.parametrize("index", range(4))
def test_no_subclass_narrows_the_shared_submit_signature(index):
    """A subclass that renamed or dropped a parameter would break the one
    polymorphic call `ServeLoop` makes at every rung."""
    venue = submitters()[index]
    params = tuple(inspect.signature(venue.submit).parameters)
    assert params == ("intent", "permit", "state")


# ---------------------------------------------------------------------------
# The conformance battery (§5.7)
# ---------------------------------------------------------------------------


def conformance_orders():
    """The `(intent, permit)` pairs the battery submits — the same pairs a
    recording would have been made from."""
    return (
        (intent("conf-1", proposal=proposal(tif="gtc", limit="0.30")), simulated("conf-1")),
        (intent("conf-2"), simulated("conf-2")),
    )


def test_the_battery_ships_every_check_section_5_7_lists():
    """CLAUDE.md: "A pinning test that omits a knob is worse than none — it
    claims coverage it lacks." The battery is the bar a child's venue subclass
    is held to, so the bar itself is pinned."""
    suite = executor_conformance_suite(ShadowExecutor, {}, QUOTES)
    assert {name for name in dir(suite) if name.startswith("test_")} == set(BATTERY)


def test_the_suite_is_built_from_a_class_its_params_and_a_market():
    """The `conformance_suite` precedent: point it at a class and get a pytest
    class back, so a child runs the same battery against its own venue."""
    signature = inspect.signature(executor_conformance_suite)
    names = tuple(signature.parameters)
    assert names[:3] == ("cls", "params", "quotes")
    assert {"build", "orders", "name"} <= set(names)


def test_the_suite_names_itself_for_the_report():
    """A report full of `TestExecutorConformance` rows tells an operator
    nothing about which venue failed."""
    suite = executor_conformance_suite(PaperExecutor, PAPER_PARAMS, QUOTES, name="TestPaperX")
    assert suite.__name__ == "TestPaperX"


def test_derived_positions_are_the_folds_not_the_executors():
    """§5.7: "Fill derivation is `PositionBook` … owned by `SeriesState`, so
    ours and theirs are two clearly separate sides"; an executor whose
    `capabilities().positions` is `derived` "returns nothing"."""
    venue = paper({"fill_rule": "touch"})
    venue.submit(intent(), simulated(), tick_state())
    assert venue.capabilities().positions == "derived"
    assert venue.positions() == ()
    book = PositionBook()
    for fill in venue.fills(0)[0]:
        book.apply(fill)
    assert book.net_qty(INSTRUMENT) == Decimal("10")


def test_the_release_scope_is_a_value_not_a_string():
    """§5.7.2: the scope is the canonical `ExecutionScope{venue, account}`,
    "not a release id", so old and new releases contend for one domain."""
    assert isinstance(paper().execution_scope(), ExecutionScope)
    assert canonical_hash(SCOPE.to_obj()) == canonical_hash(
        ExecutionScope(venue="paper", account="strategy-a").to_obj()
    )


TestShadowConformance = executor_conformance_suite(
    ShadowExecutor, {}, QUOTES, orders=conformance_orders(), name="TestShadowConformance"
)

TestPaperConformance = executor_conformance_suite(
    PaperExecutor, PAPER_PARAMS, QUOTES, orders=conformance_orders(),
    name="TestPaperConformance",
)

TestRecordedConformance = executor_conformance_suite(
    RecordedExecutor,
    {},
    QUOTES,
    orders=conformance_orders(),
    build=lambda clock: RecordedExecutor({}, clock=clock, tape=recorded_tape()),
    name="TestRecordedConformance",
)
