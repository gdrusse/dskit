"""`guards.py` — one hook, a closed verdict lattice, every finding recorded (D9, §5.5).

A guard is the last thing between a number a model produced and money leaving
the process, so almost everything here is a refusal. The knob tests are
exhaustive on purpose: `Limit` is ONE class parameterised by measure × window ×
bound × scope, which means a typo in any of the six knobs is the whole
difference between a limit that binds and a limit that does not, and
default-deny is what turns that into an error instead of a silent default.

Four properties carry more weight than the rest and are asserted directly:

* **The lattice composes by `max`.** `allow < warn < amend < refuse < hold <
  halt` over `vocab.VERDICT_ORDER`; a leg's verdict is the strictest finding,
  never the last one.
* **An amendment can only reduce, and never lands beyond the bound.** Every
  guard first judges the ORIGINAL proposal; amendments on one field compose to
  the smaller value, amendments on two fields refuse, and the final candidate is
  re-run through the hard guards — which, because `hard` excludes `amend`, IS
  the "amendment disabled" second pass the plan asks for. A hypothesis property
  covers the arithmetic over random bounds and sizes.
* **Evidence is the account's, and absent or stale evidence refuses.** A
  `Measure` reads `state.account`, never `state.view.positions/working/balances`
  — pinned structurally by an AST scan, because the fold at head is missing the
  prior legs' reservations and reaching for it is invisible in a value test.
* **A `cash_flow` cannot move a `pnl` bound** (§6). `pnl`, `drawdown`,
  `consecutive_losses` and `error_vs_realised` read trading evidence only;
  `bankroll_fraction` and `exposure` read the capital base including an external
  flow. An adopted deposit that inflated a loss halt into headroom is the defect
  this partition exists to stop, so it is pinned both by value and by AST.

No test reads a wall clock: every instant is an int computed in this file, and
the calendar is a `FakeCalendar` of fixed `[start, end)` windows so a resolved
`{"calendar": "session"}` window is a number this file already knows.
"""

import ast
import dataclasses
import inspect
import math
import pathlib
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dskit.production import guards as guards_module
from dskit.production import records, vocab
from dskit.production.base import ProductionError
from dskit.production.guards import (
    BREACH_VERDICTS,
    GUARD_KINDS,
    MEASURE_KINDS,
    Bound,
    Guard,
    GuardChain,
    Limit,
    Measure,
    RangeGuard,
    Scope,
    Window,
    max_verdict,
)

# ---------------------------------------------------------------------------
# Fixed material — every instant and every Decimal below is computed here, so a
# hand-written expectation can be read against it.
# ---------------------------------------------------------------------------

SECOND = 1_000
MINUTE = 60 * SECOND
HOUR = 60 * MINUTE
DAY = 24 * HOUR

BASE_MS = 1_767_268_800_000
AT_MS = BASE_MS + HOUR

#: The `FakeCalendar`'s fixed windows: two sessions, one day, one event.
SESSION_A = (BASE_MS, BASE_MS + 6 * HOUR)
SESSION_B = (BASE_MS + DAY, BASE_MS + DAY + 6 * HOUR)
DAY_WINDOW = (BASE_MS - 12 * HOUR, BASE_MS + 12 * HOUR)
EVENT_WINDOW = (BASE_MS + 30 * MINUTE, BASE_MS + 90 * MINUTE)

D_INPUTS = "1" * 64
D_COVER = "2" * 64
D_QUOTE = "3" * 64
D_EVIDENCE = "4" * 64
D_SOURCE = "5" * 64

#: The seventeen stdlib measures of §5.5, restated here rather than read from
#: the registry: an assertion sourced from its subject asserts nothing.
STDLIB_MEASURES = (
    "bankroll_fraction",
    "confidence",
    "consecutive_losses",
    "decision_count",
    "direction_changes",
    "drawdown",
    "error_vs_realised",
    "exposure",
    "exposure_after",
    "feed_age_ms",
    "identical_count",
    "input_age_ms",
    "notional",
    "open_orders",
    "pnl",
    "price_deviation",
    "quantity",
)

#: §5.5: only dimensionless ratios may be float; everything else is Decimal.
FLOAT_MEASURES = ("bankroll_fraction", "confidence")

#: §5.5: an amendment reduces a declared scalable field; nothing else scales.
SCALABLE_MEASURES = ("bankroll_fraction", "exposure_after", "notional", "quantity")

#: §6's `cash_flow` row: these four read trading records only and never see an
#: external flow.
TRADING_ONLY_MEASURES = ("consecutive_losses", "drawdown", "error_vs_realised", "pnl")

#: §5.5: the measures whose answer accounting must snapshot before sizing.
EVIDENCE_MEASURES = TRADING_ONLY_MEASURES


# ---------------------------------------------------------------------------
# Fakes — a calendar of fixed windows, and a proposal-shaped object for the one
# case `records.Proposal` cannot express (a NaN field).
# ---------------------------------------------------------------------------


class FakeCalendar:
    """Fixed `[start, end)` windows, so every resolved bound is a number this file knows."""

    tz_name = "UTC"

    def __init__(self, sessions=(SESSION_A, SESSION_B), day=DAY_WINDOW, event=EVENT_WINDOW):
        self._by_kind = {"session": tuple(sessions), "day": (day,), "event": (event,)}
        self.calls = []

    def window(self, kind, at_ms):
        """The first window whose end is after `at_ms` — the current one, else the next."""
        self.calls.append((kind, at_ms))
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


@dataclasses.dataclass(frozen=True)
class LooseProposal:
    """A proposal-shaped value for the NaN case `records.Proposal` refuses to hold."""

    id: str
    instrument: str
    side: str
    qty: Decimal
    notional: Decimal
    limit: Decimal
    reference_price: Decimal
    confidence: float
    extra: dict


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def proposal(**over):
    """The worked proposal: buy 10 @ ref 10 (notional 100), limit 10.50."""
    values = {
        "id": "cand-1",
        "instrument": "INS1",
        "side": "buy",
        "qty": Decimal("10"),
        "notional": Decimal("100"),
        "limit": Decimal("10.50"),
        "tif": "ioc",
        "expires_ms": AT_MS + 5 * SECOND,
        "reference_price": Decimal("10"),
        "exposure": Decimal("100"),
        "direction": "long",
        "confidence": 0.61,
        "prediction": 0.58,
        "baseline": 0.50,
        "expected_value": 0.03,
        "inputs_asof_ms": AT_MS,
        "inputs_digest": D_INPUTS,
        "coverage_digest": D_COVER,
        "quote_asof_ms": AT_MS,
        "quote_digest": D_QUOTE,
        "extra": {},
    }
    values.update(over)
    return records.Proposal(**values)


def candidate(cid="cand-1", instrument="INS1", scope_keys=("INS1",)):
    return records.Candidate(id=cid, instrument=instrument, scope_keys=tuple(scope_keys))


def position(instrument="INS1", qty="5", avg_cost="20"):
    return records.Position(
        instrument=instrument,
        qty=Decimal(qty),
        avg_cost=Decimal(avg_cost),
        source="derived",
        native={},
    )


def working(instrument="INS1", remaining="2", limit="25", ref="ref-1"):
    return records.OrderState(
        client_ref=ref,
        venue_ref="v-" + ref,
        status="open",
        ts_ms=AT_MS,
        filled_qty=Decimal("0"),
        avg_price=None,
        fee=Decimal("0"),
        reason="",
        native={},
        instrument=instrument,
        side="buy",
        qty=Decimal(remaining),
        remaining_qty=Decimal(remaining),
        limit=Decimal(limit),
        tif="gtc",
        created_ms=AT_MS - MINUTE,
        updated_ms=AT_MS,
    )


def balance(currency="USD", total="1000", available="900"):
    return records.Balance(
        currency=currency, total=Decimal(total), available=Decimal(available), native={}
    )


def evidence(requirement, value, scope_key="*", known_at_ms=AT_MS, window=None):
    """A `MeasureEvidence` answering `requirement`; `window` overrides its bounds."""
    start, end = window or (requirement.window_start_ms, requirement.window_end_ms)
    return records.MeasureEvidence(
        requirement_digest=requirement.requirement_digest,
        value=Decimal(value),
        sample_count=7,
        window_start_ms=start,
        window_end_ms=end,
        scope_key=scope_key,
        effective_at_ms=end,
        known_at_ms=known_at_ms,
        source_digests={"fills": D_SOURCE},
    )


def account(
    balances=None,
    positions=None,
    orders=None,
    measure_evidence=None,
    asof_ms=AT_MS,
):
    """An `AccountState` at `asof_ms` — the sole economic authority a measure reads."""
    return records.AccountState(
        risk_version=records.RiskVersion(
            economic_seq=41, executor_token="etok-7", accounting_tokens=("atok-3",)
        ),
        asof_ms=asof_ms,
        evidence_digest=D_EVIDENCE,
        balances=tuple(balance() if balances is None else balances),
        positions=tuple((position(),) if positions is None else positions),
        working=tuple((working(),) if orders is None else orders),
        measure_evidence=dict(measure_evidence or {}),
        source_digests={"fills": D_SOURCE},
    )


class FakeView:
    """The `StateView` members a guard may read: history and holds, no economics."""

    def __init__(self, decision_history=(), guard_holds=None):
        self.decision_history = tuple(decision_history)
        self.guard_holds = dict(guard_holds or {})
        self.positions = ()
        self.working = {}
        self.balances = {}
        self.pending = ()
        self.breaker = "active"
        self.arming = None
        self.readiness = None
        self.reduction = None
        self.pending_control = {}
        self.head_seq = 41
        self.head_hash = "a" * 64


@dataclasses.dataclass(frozen=True)
class FakeTickState:
    """The five `TickState` members of §5.8.1, in order, and no rung."""

    view: object
    account: object
    feed_status: str
    feed_ages: tuple
    calendar: object


def feed_age(key="INS1", age_ms=1_000):
    return records.FeedAge(key=key, age_ms=age_ms, watermark_ms=AT_MS - age_ms)


def tick_state(
    view=None,
    acct=None,
    feed_status="live",
    feed_ages=None,
    calendar=CAL,
):
    return FakeTickState(
        view=view if view is not None else FakeView(),
        account=acct if acct is not None else account(),
        feed_status=feed_status,
        feed_ages=tuple((feed_age(),) if feed_ages is None else feed_ages),
        calendar=calendar,
    )


def leg(instrument="INS1", final="buy", leg_id="leg-1", tick_id="T1"):
    """One `decision_history` entry, as §6's `decision.legs[]` shapes it."""
    return {
        "tick_id": tick_id,
        "leg_id": leg_id,
        "instrument": instrument,
        "prediction": "0.01",
        "confidence": "0.5",
        "baseline": "0.0",
        "expected_value": "5",
        "reference_price": "10",
        "proposal": {},
        "findings": [],
        "final": final,
        "client_ref": "ref-" + leg_id,
    }


def limit_params(**over):
    """The §4.1 `size` guard: quantity, max 100, refuse."""
    values = {"measure": "quantity", "bound": {"max": "100"}, "on_breach": "refuse"}
    values.update(over)
    return values


def a_limit(name="size", **over):
    return Limit(limit_params(**over), name=name)


def measure_of(kind):
    """The registered `Measure` instance for a kind name."""
    return MEASURE_KINDS.resolve(kind)()


def only(findings, guard):
    """The single finding `guard` produced, so a lookup failure is loud."""
    matched = [f for f in findings if f.guard == guard]
    assert len(matched) == 1, f"expected one {guard!r} finding, got {matched}"
    return matched[0]


# ---------------------------------------------------------------------------
# The seam — abstraction (§5.15)
# ---------------------------------------------------------------------------


def test_guard_check_is_abstract_so_an_incomplete_guard_cannot_construct():
    assert "check" in Guard.__abstractmethods__
    with pytest.raises(TypeError):
        Guard({})


def test_measure_hooks_are_abstract_so_an_incomplete_measure_cannot_construct():
    assert {"requirements", "value"} <= Measure.__abstractmethods__
    with pytest.raises(TypeError):
        Measure()


def test_the_real_hierarchy_is_limit_and_range_under_guard():
    assert issubclass(Limit, Guard)
    assert issubclass(RangeGuard, Guard)
    assert not issubclass(Limit, RangeGuard)


def test_guard_kinds_lists_exactly_limit_and_range():
    assert GUARD_KINDS.kinds() == ("limit", "range")
    assert GUARD_KINDS.family == "guard"
    assert GUARD_KINDS.resolve("limit") is Limit
    assert GUARD_KINDS.resolve("range") is RangeGuard


def test_an_unregistered_guard_kind_refuses():
    with pytest.raises(ProductionError):
        GUARD_KINDS.resolve("stoploss")


def test_measure_kinds_lists_exactly_the_seventeen_stdlib_measures():
    assert MEASURE_KINDS.kinds() == tuple(sorted(STDLIB_MEASURES))
    assert len(STDLIB_MEASURES) == 17
    assert MEASURE_KINDS.family == "measure"


def test_every_registered_measure_names_itself_the_way_the_document_spells_it():
    # The kind is written twice — in the registry and on the class — so the
    # agreement is pinned rather than assumed (`Finding.measure` and
    # `EvidenceRequirement.measure` both read the class attribute).
    for kind in STDLIB_MEASURES:
        assert MEASURE_KINDS.resolve(kind).kind == kind


def test_an_unregistered_measure_refuses():
    with pytest.raises(ProductionError):
        MEASURE_KINDS.resolve("sharpe")


def test_a_measure_is_not_a_monitor_and_carries_no_stream_hook():
    # §5.15: two concepts that look like one. A Measure answers about one
    # proposal against a snapshotted account; it has no `observe`.
    assert not hasattr(Measure, "observe")
    for kind in STDLIB_MEASURES:
        assert not hasattr(MEASURE_KINDS.resolve(kind), "observe")


def test_a_child_measure_resolves_by_class_reference():
    ref = "tests.production.test_guards:ChildMeasure"
    assert MEASURE_KINDS.resolve(ref) is ChildMeasure
    assert "tests.production.test_guards:ChildMeasure" not in MEASURE_KINDS


def test_a_class_reference_that_is_not_a_measure_refuses():
    with pytest.raises(ProductionError):
        MEASURE_KINDS.resolve("tests.production.test_guards:FakeCalendar")


# ---------------------------------------------------------------------------
# The verdict lattice (D9)
# ---------------------------------------------------------------------------


def test_the_lattice_is_the_closed_vocabulary_in_strictness_order():
    assert vocab.VERDICTS == ("allow", "warn", "amend", "refuse", "hold", "halt")
    assert [vocab.VERDICT_ORDER[v] for v in vocab.VERDICTS] == [0, 1, 2, 3, 4, 5]


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    (
        ((), "allow"),
        (("allow",), "allow"),
        (("allow", "warn"), "warn"),
        (("warn", "allow"), "warn"),
        (("refuse", "warn", "amend"), "refuse"),
        (("halt", "allow"), "halt"),
        (("hold", "refuse"), "hold"),
        (("allow", "warn", "amend", "refuse", "hold", "halt"), "halt"),
    ),
)
def test_the_composite_verdict_is_the_maximum_not_the_last(verdicts, expected):
    findings = tuple(
        records.Finding(
            guard=f"g{i}",
            measure="quantity",
            value=Decimal("1"),
            bound=Decimal("2"),
            window="none",
            scope_key="*",
            verdict=verdict,
            reason=f"{verdict} because",
        )
        for i, verdict in enumerate(verdicts)
    )
    assert max_verdict(findings) == expected


def test_max_verdict_refuses_a_verdict_outside_the_lattice():
    with pytest.raises(ProductionError):
        max_verdict(({"verdict": "maybe"},))


def test_each_breach_response_maps_to_exactly_one_verdict_with_pause_becoming_hold():
    # D9: "`pause` produces `hold` with a recorded `resume_at`". The mapping is
    # a named table, not an `if on_breach ==` chain.
    assert set(BREACH_VERDICTS) == set(vocab.ON_BREACH)
    assert BREACH_VERDICTS == {
        "refuse": "refuse",
        "amend": "amend",
        "pause": "hold",
        "hold": "hold",
        "halt": "halt",
    }
    assert set(BREACH_VERDICTS.values()) <= set(vocab.VERDICTS)


# ---------------------------------------------------------------------------
# Window normalisation (§5.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("declared", "kind", "arg"),
    (
        ({}, "none", None),
        ({"duration": "PT1H"}, "duration", HOUR),
        ({"duration": "P1D"}, "duration", DAY),
        ({"duration": 30_000}, "duration", 30_000),
        ({"count": 50}, "count", 50),
        ({"calendar": "session"}, "calendar", "session"),
        ({"calendar": "day"}, "calendar", "day"),
        ({"calendar": "event"}, "calendar", "event"),
    ),
)
def test_a_window_normalises_to_a_frozen_kind_and_argument(declared, kind, arg):
    window = Window.from_params(declared)
    assert (window.kind, window.arg) == (kind, arg)
    assert window.kind in vocab.WINDOW_KINDS
    assert type(window).__dataclass_params__.frozen is True


def test_two_windows_declared_alike_compare_equal():
    assert Window.from_params({"duration": "PT1H"}) == Window.from_params({"duration": HOUR})
    assert Window.from_params({}) != Window.from_params({"count": 1})


@pytest.mark.parametrize(
    "declared",
    (
        {"duration": "PT1H", "count": 5},
        {"kind": "count", "n": 5},
        {"calendar": "week"},
        {"count": 0},
        {"count": -1},
        {"count": 1.5},
        {"count": True},
        {"duration": -1},
        {"duration": "1h"},
        {"duration": "P1M"},
        {"rolling": 5},
        [],
        "session",
        None,
    ),
)
def test_a_window_outside_the_grammar_refuses(declared):
    with pytest.raises(ProductionError):
        Window.from_params(declared)


@pytest.mark.parametrize(
    ("declared", "label"),
    (
        ({}, "none"),
        ({"duration": "PT1H"}, "duration:3600000"),
        ({"count": 50}, "count:50"),
        ({"calendar": "session"}, "session"),
    ),
)
def test_a_window_renders_the_label_a_finding_records(declared, label):
    assert Window.from_params(declared).label == label


@pytest.mark.parametrize(
    ("declared", "expected"),
    (
        ({}, (0, AT_MS, 0, None)),
        ({"duration": "PT1H"}, (AT_MS - HOUR, AT_MS, AT_MS - HOUR, HOUR)),
        ({"count": 50}, (0, AT_MS, 0, 50)),
        ({"calendar": "session"}, (SESSION_A[0], SESSION_A[1], SESSION_A[0], SESSION_A)),
        ({"calendar": "day"}, (DAY_WINDOW[0], DAY_WINDOW[1], DAY_WINDOW[0], DAY_WINDOW)),
        ({"calendar": "event"}, (EVENT_WINDOW[0], EVENT_WINDOW[1], EVENT_WINDOW[0], EVENT_WINDOW)),
    ),
)
def test_a_window_resolves_to_the_bounds_a_requirement_digests(declared, expected):
    # §5.5: `at_ms` and `calendar` are what let a measure resolve a window to
    # the `[start, end)` bounds its `requirement_digest` is computed over.
    window = Window.from_params(declared)
    start, end, baseline, arg = expected
    assert window.resolve(AT_MS, CAL) == (start, end, baseline, arg)


def test_resolving_a_calendar_window_asks_the_injected_calendar():
    calendar = FakeCalendar()
    Window.from_params({"calendar": "session"}).resolve(AT_MS, calendar)
    assert calendar.calls == [("session", AT_MS)]


# ---------------------------------------------------------------------------
# `Limit` — one refusal per knob (§8)
# ---------------------------------------------------------------------------


def test_the_limit_knobs_are_exactly_the_section_five_five_list():
    assert set(Limit._PARAMS) == {
        "measure",
        "window",
        "bound",
        "warn_at",
        "scope",
        "include_working",
        "on_breach",
        "pause",
        "hold",
        "notes",
    }


def test_a_note_on_a_limit_is_documentation_not_a_knob():
    assert a_limit(notes="why 100").bound == Bound(min=None, max=Decimal("100"))


def test_validate_params_reports_problems_without_raising():
    problems = Limit.validate_params({"measure": "nope", "bound": {"max": "1"}})
    assert isinstance(problems, list)
    assert problems
    assert any("nope" in p for p in problems)


def test_construction_raises_every_problem_at_once():
    with pytest.raises(ProductionError) as excinfo:
        Limit({"measure": "nope", "bound": {}, "warn_at": 2, "scope": "nowhere"}, name="bad")
    assert len(excinfo.value.problems) >= 4


@pytest.mark.parametrize(
    ("knob", "params"),
    (
        ("unknown param", limit_params(threshold=5)),
        ("measure missing", {"bound": {"max": "1"}}),
        ("measure unknown", limit_params(measure="sharpe")),
        ("measure not a string", limit_params(measure=7)),
        ("measure bad class ref", limit_params(measure="tests.production.test_guards:NoSuch")),
        ("bound missing", {"measure": "quantity", "on_breach": "refuse"}),
        ("bound empty", limit_params(bound={})),
        ("bound not a dict", limit_params(bound="100")),
        ("bound unknown key", limit_params(bound={"max": "1", "target": "2"})),
        ("bound max not decimal", limit_params(bound={"max": "abc"})),
        ("bound max a float", limit_params(bound={"max": 1.5})),
        ("bound max None", limit_params(bound={"max": None})),
        ("bound min above max", limit_params(bound={"min": "10", "max": "1"})),
        ("window bad kind", limit_params(window={"rolling": 5})),
        ("window two keys", limit_params(window={"count": 5, "duration": 10})),
        ("window calendar unknown", limit_params(window={"calendar": "week"})),
        ("warn_at zero", limit_params(warn_at=0)),
        ("warn_at one", limit_params(warn_at=1)),
        ("warn_at above one", limit_params(warn_at=1.2)),
        ("warn_at negative", limit_params(warn_at=-0.5)),
        ("warn_at not a number", limit_params(warn_at="0.8")),
        ("scope unknown", limit_params(scope="everything")),
        ("scope group without field", limit_params(scope={"group": ""})),
        ("scope group unknown key", limit_params(scope={"bucket": "sector"})),
        ("scope not a string or dict", limit_params(scope=7)),
        ("include_working not a bool", limit_params(include_working="yes")),
        ("include_working an int", limit_params(include_working=1)),
        ("on_breach unknown", limit_params(on_breach="ignore")),
        ("on_breach missing", {"measure": "quantity", "bound": {"max": "1"}}),
        ("hold without ttl", limit_params(on_breach="hold")),
        ("hold ttl not an int", limit_params(on_breach="hold", hold={"ttl": "soon"})),
        ("hold ttl not positive", limit_params(on_breach="hold", hold={"ttl": 0})),
        ("hold unknown key", limit_params(on_breach="hold", hold={"ttl": 60, "until": 1})),
        ("hold on a non-hold breach", limit_params(hold={"ttl": 60})),
        ("pause without shape", limit_params(on_breach="pause")),
        ("pause both keys", limit_params(on_breach="pause", pause={"duration": 60, "calendar": "session"})),
        ("pause calendar unknown", limit_params(on_breach="pause", pause={"calendar": "week"})),
        ("pause duration malformed", limit_params(on_breach="pause", pause={"duration": "soon"})),
        ("pause on a non-pause breach", limit_params(pause={"duration": 60})),
    ),
)
def test_one_refusal_per_limit_knob(knob, params):
    with pytest.raises(ProductionError):
        Limit(params, name="g")


def test_a_refusal_names_the_knob_it_refused():
    with pytest.raises(ProductionError) as excinfo:
        Limit(limit_params(warn_at=1.5), name="g")
    assert "warn_at" in str(excinfo.value)


@pytest.mark.parametrize("value", (0.01, 0.5, 0.8, 0.99))
def test_warn_at_is_the_open_interval_between_zero_and_one(value):
    assert a_limit(warn_at=value).warn_at == pytest.approx(value)


@pytest.mark.parametrize("declared", ("aggregate", "per_key", {"group": "sector"}))
def test_every_declared_scope_normalises(declared):
    scope = a_limit(scope=declared).scope
    assert scope.kind in vocab.LIMIT_SCOPES
    assert scope == (
        Scope(kind="group", field="sector")
        if isinstance(declared, dict)
        else Scope(kind=declared, field=None)
    )


def test_scope_defaults_to_aggregate_and_include_working_to_true():
    limit = a_limit()
    assert limit.scope == Scope(kind="aggregate", field=None)
    assert limit.include_working is True
    assert limit.window == Window.from_params({})
    assert limit.warn_at is None


@pytest.mark.parametrize("bound", ({"max": "100"}, {"min": "-500"}, {"min": "-500", "max": "100"}))
def test_a_bound_may_be_one_sided_but_never_absent(bound):
    limit = Limit(limit_params(bound=bound, measure="pnl", window={"calendar": "session"}), name="g")
    assert isinstance(limit.bound, Bound)
    assert (limit.bound.min, limit.bound.max) == (
        Decimal(bound["min"]) if "min" in bound else None,
        Decimal(bound["max"]) if "max" in bound else None,
    )


def test_an_integer_bound_is_read_as_a_decimal():
    # §4.1's `stale` guard writes `{"max": 30000}` as an int.
    limit = Limit(limit_params(measure="input_age_ms", bound={"max": 30000}), name="stale")
    assert limit.bound.max == Decimal(30000)
    assert isinstance(limit.bound.max, Decimal)


# ---------------------------------------------------------------------------
# `Limit` — the measure/window/breach agreements (§5.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("measure", SCALABLE_MEASURES)
def test_a_scalable_measure_declares_the_one_field_an_amendment_reduces(measure):
    cls = MEASURE_KINDS.resolve(measure)
    assert cls.scalable is True
    assert cls.scalable_field in ("qty", "notional")


@pytest.mark.parametrize("measure", sorted(set(STDLIB_MEASURES) - set(SCALABLE_MEASURES)))
def test_a_measure_that_is_not_scalable_says_so(measure):
    cls = MEASURE_KINDS.resolve(measure)
    assert cls.scalable is False
    assert cls.scalable_field is None


@pytest.mark.parametrize("measure", sorted(set(STDLIB_MEASURES) - set(SCALABLE_MEASURES)))
def test_amend_refuses_on_a_measure_that_cannot_be_scaled(measure):
    with pytest.raises(ProductionError) as excinfo:
        Limit(
            {
                "measure": measure,
                "bound": {"max": "100"},
                "on_breach": "amend",
                "window": {"calendar": "session"} if measure in EVIDENCE_MEASURES else {},
            },
            name="g",
        )
    assert "amend" in str(excinfo.value)


@pytest.mark.parametrize("measure", SCALABLE_MEASURES)
def test_amend_is_accepted_on_a_scalable_measure(measure):
    limit = Limit({"measure": measure, "bound": {"max": "100"}, "on_breach": "amend"}, name="g")
    assert limit.on_breach == "amend"
    assert limit.amended_field == MEASURE_KINDS.resolve(measure).scalable_field


def test_amend_refuses_a_bound_with_only_a_minimum():
    # An amendment may only REDUCE, and a min breach means the value is too
    # small: reducing it makes the breach worse.
    with pytest.raises(ProductionError) as excinfo:
        Limit({"measure": "quantity", "bound": {"min": "1"}, "on_breach": "amend"}, name="g")
    assert "amend" in str(excinfo.value)


def test_a_measure_declares_which_windows_it_can_answer():
    # Default-deny reaches the measure × window pair: `value()` holds no clock,
    # so only evidence-backed measures can answer a duration or calendar window.
    assert set(MEASURE_KINDS.resolve("pnl").window_kinds) == set(vocab.WINDOW_KINDS)
    assert MEASURE_KINDS.resolve("quantity").window_kinds == ("none",)
    assert MEASURE_KINDS.resolve("decision_count").window_kinds == ("none", "count")
    for kind in STDLIB_MEASURES:
        assert set(MEASURE_KINDS.resolve(kind).window_kinds) <= set(vocab.WINDOW_KINDS)
        assert MEASURE_KINDS.resolve(kind).window_kinds


@pytest.mark.parametrize(
    ("measure", "window"),
    (
        ("quantity", {"count": 5}),
        ("quantity", {"duration": "PT1H"}),
        ("notional", {"calendar": "session"}),
        ("confidence", {"count": 5}),
        ("decision_count", {"duration": "PT1H"}),
        ("decision_count", {"calendar": "session"}),
        ("open_orders", {"calendar": "day"}),
    ),
)
def test_a_window_a_measure_cannot_answer_refuses(measure, window):
    with pytest.raises(ProductionError) as excinfo:
        Limit(limit_params(measure=measure, window=window), name="g")
    assert "window" in str(excinfo.value)


@pytest.mark.parametrize("measure", EVIDENCE_MEASURES)
def test_an_evidence_backed_measure_accepts_every_window(measure):
    for window in ({}, {"duration": "PT1H"}, {"count": 20}, {"calendar": "session"}):
        Limit(limit_params(measure=measure, bound={"min": "-500"}, window=window), name="g")


def test_the_five_example_guards_of_section_four_one_construct():
    chain = GuardChain(
        {
            "size": Limit(
                {"measure": "quantity", "bound": {"max": "100"}, "on_breach": "refuse"}, name="size"
            ),
            "exposure": Limit(
                {
                    "measure": "exposure_after",
                    "scope": "aggregate",
                    "include_working": True,
                    "bound": {"max": "20000"},
                    "warn_at": 0.8,
                    "on_breach": "refuse",
                },
                name="exposure",
            ),
            "day_loss": Limit(
                {
                    "measure": "pnl",
                    "window": {"calendar": "session"},
                    "bound": {"min": "-500"},
                    "on_breach": "halt",
                },
                name="day_loss",
            ),
            "stale": Limit(
                {"measure": "input_age_ms", "bound": {"max": 30000}, "on_breach": "refuse"},
                name="stale",
            ),
            "sane": RangeGuard(
                {"field": "confidence", "min": 0, "max": 1, "nan": "refuse"}, name="sane"
            ),
        }
    )
    assert tuple(chain.guards) == ("size", "exposure", "day_loss", "stale", "sane")


def test_a_limit_resolves_its_measure_through_an_injected_registry():
    from dskit.production.base import Registry

    registry = Registry("measure", Measure)
    registry.register("child", ChildMeasure)
    limit = Limit(limit_params(measure="child"), name="g", measure_registry=registry)
    assert isinstance(limit.measure, ChildMeasure)
    with pytest.raises(ProductionError):
        Limit(limit_params(measure="quantity"), name="g", measure_registry=registry)


def test_a_limit_resolves_a_child_measure_by_class_reference():
    limit = Limit(limit_params(measure="tests.production.test_guards:ChildMeasure"), name="g")
    assert isinstance(limit.measure, ChildMeasure)


# ---------------------------------------------------------------------------
# `hard` — which guards the final candidate is re-run through (D9)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("on_breach", "hard"),
    (("refuse", True), ("hold", True), ("halt", True), ("pause", True), ("amend", False)),
)
def test_a_guard_is_hard_when_its_breach_can_stop_the_leg(on_breach, hard):
    extra = {}
    if on_breach == "hold":
        extra["hold"] = {"ttl": 10 * MINUTE}
    if on_breach == "pause":
        extra["pause"] = {"duration": "PT30M"}
    limit = Limit(
        limit_params(measure="quantity", on_breach=on_breach, **extra),
        name="g",
    )
    assert limit.hard is hard


def test_a_range_guard_is_always_hard():
    assert RangeGuard({"field": "confidence", "min": 0, "max": 1}, name="sane").hard is True


def test_no_hard_guard_can_produce_an_amendment():
    # This is why the second pass needs no "amendment disabled" flag: `hard`
    # already excludes the only breach response that amends.
    for on_breach, verdict in BREACH_VERDICTS.items():
        limit = Limit(
            limit_params(
                on_breach=on_breach,
                **({"hold": {"ttl": 60}} if on_breach == "hold" else {}),
                **({"pause": {"duration": 60}} if on_breach == "pause" else {}),
            ),
            name="g",
        )
        assert limit.hard is (verdict != "amend")


# ---------------------------------------------------------------------------
# `Limit.check` — value, bound, reason on every finding (§8)
# ---------------------------------------------------------------------------


def test_a_finding_inside_the_bound_still_carries_value_bound_and_reason():
    finding = a_limit().check(proposal(qty=Decimal("10")), tick_state())
    assert isinstance(finding, records.Finding)
    assert finding.guard == "size"
    assert finding.measure == "quantity"
    assert finding.value == Decimal("10")
    assert finding.bound == Decimal("100")
    assert finding.window == "none"
    assert finding.scope_key == "*"
    assert finding.verdict == "allow"
    assert finding.reason != ""


def test_a_value_exactly_on_the_bound_is_inside_it():
    finding = a_limit().check(proposal(qty=Decimal("100")), tick_state())
    assert finding.verdict == "allow"
    assert finding.value == Decimal("100")


def test_a_value_past_the_maximum_breaches():
    finding = a_limit().check(proposal(qty=Decimal("101")), tick_state())
    assert finding.verdict == "refuse"
    assert finding.value == Decimal("101")
    assert finding.bound == Decimal("100")


def test_a_value_exactly_on_the_minimum_is_inside_it():
    limit = Limit(
        limit_params(measure="pnl", window={"calendar": "session"}, bound={"min": "-500"}),
        name="day_loss",
    )
    state = pnl_state("-500", limit)
    assert limit.check(proposal(), state).verdict == "allow"


def test_a_value_below_the_minimum_breaches():
    limit = Limit(
        limit_params(measure="pnl", window={"calendar": "session"}, bound={"min": "-500"}),
        name="day_loss",
    )
    state = pnl_state("-500.01", limit)
    finding = limit.check(proposal(), state)
    assert finding.verdict == "refuse"
    assert finding.bound == Decimal("-500")


@pytest.mark.parametrize(
    ("qty", "verdict"),
    ((Decimal("79"), "allow"), (Decimal("80"), "warn"), (Decimal("100"), "warn"), (Decimal("101"), "refuse")),
)
def test_warn_at_marks_the_approach_to_a_maximum(qty, verdict):
    limit = a_limit(warn_at=0.8)
    assert limit.check(proposal(qty=qty), tick_state()).verdict == verdict


@pytest.mark.parametrize(
    ("value", "verdict"),
    (("-399", "allow"), ("-400", "warn"), ("-500", "warn"), ("-501", "refuse")),
)
def test_warn_at_marks_the_approach_to_a_minimum(value, verdict):
    limit = Limit(
        limit_params(
            measure="pnl", window={"calendar": "session"}, bound={"min": "-500"}, warn_at=0.8
        ),
        name="day_loss",
    )
    assert limit.check(proposal(), pnl_state(value, limit)).verdict == verdict


def test_without_warn_at_a_value_inside_the_bound_only_ever_allows():
    limit = a_limit()
    assert limit.check(proposal(qty=Decimal("99.9999")), tick_state()).verdict == "allow"


@pytest.mark.parametrize(
    ("on_breach", "verdict", "extra"),
    (
        ("refuse", "refuse", {}),
        ("halt", "halt", {}),
        ("hold", "hold", {"hold": {"ttl": 10 * MINUTE}}),
        ("pause", "hold", {"pause": {"duration": "PT30M"}}),
    ),
)
def test_a_breach_takes_the_verdict_its_breach_response_names(on_breach, verdict, extra):
    limit = Limit(limit_params(on_breach=on_breach, **extra), name="g")
    finding = limit.check(proposal(qty=Decimal("500")), tick_state())
    assert finding.verdict == verdict
    assert finding.reason


def test_an_amending_breach_names_the_field_and_the_amended_value():
    limit = Limit(limit_params(on_breach="amend"), name="g")
    finding = limit.check(proposal(qty=Decimal("250")), tick_state())
    assert finding.verdict == "amend"
    assert "qty" in finding.reason
    assert "100" in finding.reason


def test_a_finding_records_the_calendar_window_it_was_measured_over():
    limit = Limit(
        limit_params(measure="pnl", window={"calendar": "session"}, bound={"min": "-500"}),
        name="day_loss",
    )
    assert limit.check(proposal(), pnl_state("-10", limit)).window == "session"


# ---------------------------------------------------------------------------
# scope_key derivation (§5.5)
# ---------------------------------------------------------------------------


def test_an_aggregate_scope_measures_under_the_star_key():
    assert a_limit().check(proposal(), tick_state()).scope_key == "*"


def test_a_per_key_scope_measures_under_the_proposals_instrument():
    limit = a_limit(scope="per_key")
    assert limit.check(proposal(instrument="INS7"), tick_state()).scope_key == "INS7"


def test_a_group_scope_measures_under_the_named_field_of_the_proposals_extra():
    limit = a_limit(scope={"group": "sector"})
    finding = limit.check(proposal(extra={"sector": "tech"}), tick_state())
    assert finding.scope_key == "tech"


def test_a_group_scope_refuses_a_proposal_that_does_not_declare_the_field():
    limit = a_limit(scope={"group": "sector"})
    with pytest.raises(ProductionError) as excinfo:
        limit.check(proposal(extra={}), tick_state())
    assert "sector" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Evidence — declared, absent, stale (§5.5, §5.7.1)
# ---------------------------------------------------------------------------


def requirement_for(limit, scope_key="*", at_ms=AT_MS, cand=None):
    """The single requirement `limit`'s measure declares for one scope key."""
    declared = limit.measure.requirements(
        cand or candidate(), limit.window, scope_key, at_ms, CAL, limit.include_working
    )
    assert len(declared) == 1
    return declared[0]


def pnl_state(value, limit, scope_key="*", at_ms=AT_MS, **over):
    """A `TickState` whose account answers `limit`'s pnl requirement with `value`."""
    requirement = requirement_for(limit, scope_key, at_ms)
    return tick_state(
        acct=account(
            measure_evidence={
                requirement.requirement_digest: {
                    scope_key: evidence(requirement, value, scope_key=scope_key, **over)
                }
            },
            asof_ms=at_ms,
        )
    )


@pytest.mark.parametrize(
    "measure",
    sorted(set(STDLIB_MEASURES) - set(EVIDENCE_MEASURES)),
)
def test_a_measure_the_state_already_answers_declares_no_requirement(measure):
    cls = MEASURE_KINDS.resolve(measure)
    declared = cls().requirements(candidate(), Window.from_params({}), "*", AT_MS, CAL, True)
    assert declared == ()


@pytest.mark.parametrize("measure", EVIDENCE_MEASURES)
def test_an_evidence_backed_measure_declares_exactly_one_complete_requirement(measure):
    window = Window.from_params({"calendar": "session"})
    declared = MEASURE_KINDS.resolve(measure)().requirements(
        candidate(), window, "INS1", AT_MS, CAL, True
    )
    assert len(declared) == 1
    requirement = declared[0]
    assert isinstance(requirement, records.EvidenceRequirement)
    assert requirement.measure == measure
    assert requirement.window_kind == "calendar"
    assert requirement.window_arg == SESSION_A
    assert requirement.scope_key == "INS1"
    assert requirement.window_start_ms == SESSION_A[0]
    assert requirement.window_end_ms == SESSION_A[1]
    assert requirement.baseline_at_ms == SESSION_A[0]
    assert requirement.include_working is True
    assert len(requirement.requirement_digest) == 64


def test_a_requirement_carries_the_limits_include_working_not_a_default():
    window = Window.from_params({"duration": "PT1H"})
    with_working = measure_of("pnl").requirements(candidate(), window, "*", AT_MS, CAL, True)[0]
    without = measure_of("pnl").requirements(candidate(), window, "*", AT_MS, CAL, False)[0]
    assert with_working.include_working is True
    assert without.include_working is False
    assert with_working.requirement_digest != without.requirement_digest


def test_a_guard_refuses_when_the_evidence_it_declared_is_absent():
    limit = Limit(
        limit_params(measure="pnl", window={"calendar": "session"}, bound={"min": "-500"}),
        name="day_loss",
    )
    finding = limit.check(proposal(), tick_state(acct=account(measure_evidence={})))
    assert finding.verdict == "refuse"
    assert finding.value is None
    assert "pnl" in finding.reason
    assert "evidence" in finding.reason


def test_a_guard_refuses_when_the_evidence_answers_a_different_scope_key():
    limit = Limit(
        limit_params(
            measure="pnl", window={"calendar": "session"}, bound={"min": "-500"}, scope="per_key"
        ),
        name="day_loss",
    )
    requirement = requirement_for(limit, "INS1")
    state = tick_state(
        acct=account(
            measure_evidence={
                requirement.requirement_digest: {"INS9": evidence(requirement, "-10", "INS9")}
            }
        )
    )
    finding = limit.check(proposal(instrument="INS1"), state)
    assert finding.verdict == "refuse"
    assert finding.value is None


def test_evidence_whose_window_ends_before_the_requirement_is_stale_and_refuses():
    limit = Limit(
        limit_params(measure="pnl", window={"calendar": "session"}, bound={"min": "-500"}),
        name="day_loss",
    )
    requirement = requirement_for(limit)
    stale = evidence(
        requirement,
        "-10",
        window=(requirement.window_start_ms, requirement.window_end_ms - 1),
    )
    state = tick_state(
        acct=account(measure_evidence={requirement.requirement_digest: {"*": stale}})
    )
    finding = limit.check(proposal(), state)
    assert finding.verdict == "refuse"
    assert finding.value is None
    assert "stale" in finding.reason


def test_evidence_learned_after_the_account_was_snapshotted_refuses():
    # `known_at_ms` after the snapshot instant is impossible: the account
    # cannot have folded something it had not yet learned.
    limit = Limit(
        limit_params(measure="pnl", window={"calendar": "session"}, bound={"min": "-500"}),
        name="day_loss",
    )
    requirement = requirement_for(limit)
    ahead = evidence(requirement, "-10", known_at_ms=AT_MS + 1)
    state = tick_state(
        acct=account(measure_evidence={requirement.requirement_digest: {"*": ahead}}, asof_ms=AT_MS)
    )
    finding = limit.check(proposal(), state)
    assert finding.verdict == "refuse"
    assert finding.value is None


def test_evidence_at_the_snapshot_instant_is_accepted():
    limit = Limit(
        limit_params(measure="pnl", window={"calendar": "session"}, bound={"min": "-500"}),
        name="day_loss",
    )
    finding = limit.check(proposal(), pnl_state("-10", limit, known_at_ms=AT_MS))
    assert finding.verdict == "allow"
    assert finding.value == Decimal("-10")


# ---------------------------------------------------------------------------
# Holds and pauses (§5.5, §6 `guard_state`)
# ---------------------------------------------------------------------------


def held_view(guard="day_loss", scope_key="*", held_until_ms=AT_MS + HOUR, state_kind="hold"):
    return FakeView(
        guard_holds={
            (guard, scope_key): {
                "guard": guard,
                "scope_key": scope_key,
                "state_kind": state_kind,
                "reason": "loss bound breached",
                "held_until_ms": held_until_ms,
                "resume_at_ms": held_until_ms if state_kind == "pause" else None,
                "finding": {},
            }
        }
    )


def test_a_guard_under_an_unexpired_hold_refuses_before_it_measures():
    limit = a_limit(name="size")
    state = tick_state(view=held_view(guard="size", held_until_ms=AT_MS + 1))
    finding = limit.check(proposal(qty=Decimal("1")), state)
    assert finding.verdict == "refuse"
    assert finding.value is None
    assert finding.reason.startswith("held")


def test_a_hold_expires_exactly_at_its_instant():
    limit = a_limit(name="size")
    expired = tick_state(view=held_view(guard="size", held_until_ms=AT_MS))
    assert limit.check(proposal(qty=Decimal("1")), expired).verdict == "allow"


def test_a_hold_binds_only_its_own_guard_and_scope_key():
    limit = a_limit(name="size", scope="per_key")
    other_guard = tick_state(view=held_view(guard="day_loss", scope_key="INS1"))
    other_scope = tick_state(view=held_view(guard="size", scope_key="INS9"))
    assert limit.check(proposal(instrument="INS1"), other_guard).verdict == "allow"
    assert limit.check(proposal(instrument="INS1"), other_scope).verdict == "allow"


def test_a_pause_holds_the_guard_until_its_resume_instant():
    limit = a_limit(name="size")
    state = tick_state(
        view=held_view(guard="size", held_until_ms=AT_MS + HOUR, state_kind="pause")
    )
    assert limit.check(proposal(qty=Decimal("1")), state).verdict == "refuse"


def test_a_hold_body_carries_the_section_six_fields_and_its_finding():
    limit = Limit(
        limit_params(on_breach="hold", hold={"ttl": 10 * MINUTE}), name="size"
    )
    finding = limit.check(proposal(qty=Decimal("500")), tick_state())
    body = limit.guard_state_body(finding, AT_MS, CAL)
    assert set(body) == {
        "guard",
        "scope_key",
        "state_kind",
        "reason",
        "held_until_ms",
        "resume_at_ms",
        "finding",
    }
    assert body["guard"] == "size"
    assert body["scope_key"] == "*"
    assert body["state_kind"] == "hold"
    assert body["state_kind"] in vocab.GUARD_STATE_KINDS
    assert body["held_until_ms"] == AT_MS + 10 * MINUTE
    assert body["resume_at_ms"] is None
    assert body["reason"] == finding.reason
    assert body["finding"] == finding.to_obj()


def test_a_pause_body_resumes_after_a_duration():
    limit = Limit(limit_params(on_breach="pause", pause={"duration": "PT30M"}), name="size")
    finding = limit.check(proposal(qty=Decimal("500")), tick_state())
    body = limit.guard_state_body(finding, AT_MS, CAL)
    assert body["state_kind"] == "pause"
    assert body["resume_at_ms"] == AT_MS + 30 * MINUTE
    assert body["held_until_ms"] == body["resume_at_ms"]


def test_a_pause_body_resumes_at_the_calendars_next_session():
    limit = Limit(limit_params(on_breach="pause", pause={"calendar": "session"}), name="size")
    finding = limit.check(proposal(qty=Decimal("500")), tick_state())
    body = limit.guard_state_body(finding, AT_MS, CAL)
    assert body["resume_at_ms"] == SESSION_B[0]
    assert body["held_until_ms"] == SESSION_B[0]


def test_a_guard_state_body_refuses_a_finding_that_is_not_a_hold():
    limit = a_limit()
    finding = limit.check(proposal(qty=Decimal("500")), tick_state())
    assert finding.verdict == "refuse"
    with pytest.raises(ProductionError):
        limit.guard_state_body(finding, AT_MS, CAL)


# ---------------------------------------------------------------------------
# Amendment (D9)
# ---------------------------------------------------------------------------


def test_an_amendment_lands_the_measured_value_exactly_on_the_bound():
    limit = Limit(limit_params(on_breach="amend"), name="size")
    state = tick_state()
    original = proposal(qty=Decimal("250"))
    amended = limit.amend(original, state, Decimal("250"))
    assert amended.qty == Decimal("100")
    assert limit.measure.value(amended, state, limit.window, "*", limit.include_working) == Decimal(
        "100"
    )


def test_an_amendment_changes_exactly_one_field_and_leaves_the_rest_alone():
    limit = Limit(limit_params(on_breach="amend"), name="size")
    original = proposal(qty=Decimal("250"))
    amended = limit.amend(original, tick_state(), Decimal("250"))
    changed = [
        f.name
        for f in dataclasses.fields(records.Proposal)
        if getattr(original, f.name) != getattr(amended, f.name)
    ]
    assert changed == ["qty"]
    assert amended is not original


def test_an_amendment_on_notional_reduces_the_notional_field():
    limit = Limit(
        {"measure": "notional", "bound": {"max": "40"}, "on_breach": "amend"}, name="value_cap"
    )
    original = proposal(notional=Decimal("100"))
    amended = limit.amend(original, tick_state(), Decimal("100"))
    assert amended.notional == Decimal("40")
    assert amended.qty == original.qty


def test_an_amendment_on_exposure_after_solves_for_the_account_it_adds_to():
    # exposure_after is affine, not proportional: positions 5 @ 20 = 100 plus a
    # working order 2 @ 25 = 50 gives 150 before this proposal contributes.
    limit = Limit(
        {"measure": "exposure_after", "bound": {"max": "200"}, "on_breach": "amend"}, name="expo"
    )
    state = tick_state()
    original = proposal(qty=Decimal("10"))
    before = limit.amend(original, state, Decimal("250"))
    assert before.qty == Decimal("5")
    assert limit.measure.value(before, state, limit.window, "*", True) == Decimal("200")


def test_an_amendment_never_raises_a_value():
    limit = Limit(limit_params(on_breach="amend"), name="size")
    with pytest.raises(ProductionError):
        limit.amend(proposal(qty=Decimal("10")), tick_state(), Decimal("10"))


def test_an_amendment_that_would_zero_the_field_refuses():
    limit = Limit(
        {"measure": "exposure_after", "bound": {"max": "150"}, "on_breach": "amend"}, name="expo"
    )
    with pytest.raises(ProductionError):
        limit.amend(proposal(qty=Decimal("10")), tick_state(), Decimal("250"))


@settings(max_examples=200, deadline=None)
@given(
    bound=st.integers(min_value=1, max_value=100_000),
    qty=st.integers(min_value=1, max_value=100_000),
    price=st.integers(min_value=1, max_value=1_000),
)
def test_an_amendment_never_lands_beyond_the_bound(bound, qty, price):
    limit = Limit(
        {"measure": "notional", "bound": {"max": str(bound)}, "on_breach": "amend"}, name="value"
    )
    state = tick_state()
    original = proposal(
        qty=Decimal(qty), notional=Decimal(qty * price), reference_price=Decimal(price)
    )
    value = limit.measure.value(original, state, limit.window, "*", True)
    if value <= Decimal(bound):
        assert limit.check(original, state).verdict in ("allow", "warn")
        return
    amended = limit.amend(original, state, value)
    assert limit.measure.value(amended, state, limit.window, "*", True) <= Decimal(bound)
    assert amended.notional < original.notional


@settings(max_examples=200, deadline=None)
@given(
    headroom=st.integers(min_value=1, max_value=10_000),
    qty=st.integers(min_value=1, max_value=10_000),
    price=st.integers(min_value=1, max_value=97),
)
def test_an_affine_amendment_never_lands_beyond_the_bound(headroom, qty, price):
    # positions 5 @ 20 = 100, working 2 @ 25 = 50 -> 150 before the proposal.
    bound = 150 + headroom
    limit = Limit(
        {"measure": "exposure_after", "bound": {"max": str(bound)}, "on_breach": "amend"},
        name="expo",
    )
    state = tick_state()
    original = proposal(qty=Decimal(qty), reference_price=Decimal(price))
    value = limit.measure.value(original, state, limit.window, "*", True)
    if value <= Decimal(bound):
        return
    amended = limit.amend(original, state, value)
    assert limit.measure.value(amended, state, limit.window, "*", True) <= Decimal(bound)
    assert Decimal(0) < amended.qty < original.qty


# ---------------------------------------------------------------------------
# `GuardChain.check_all` — the composition rules (D9)
# ---------------------------------------------------------------------------


def chain_of(*guards):
    return GuardChain({guard.name: guard for guard in guards})


def test_the_chain_evaluates_every_guard_in_document_order_and_records_each():
    chain = chain_of(
        a_limit(name="size"),
        a_limit(name="second", bound={"max": "1000"}),
        RangeGuard({"field": "confidence", "min": 0, "max": 1}, name="sane"),
    )
    final, findings = chain.check_all(proposal(qty=Decimal("10")), tick_state())
    assert [f.guard for f in findings] == ["size", "second", "sane"]
    assert final == proposal(qty=Decimal("10"))
    assert max_verdict(findings) == "allow"


def test_the_legs_verdict_is_the_strictest_finding_not_the_last():
    chain = chain_of(
        a_limit(name="size", bound={"max": "1"}, on_breach="halt"),
        a_limit(name="wide", bound={"max": "1000"}),
    )
    _, findings = chain.check_all(proposal(qty=Decimal("10")), tick_state())
    assert [f.verdict for f in findings] == ["halt", "allow"]
    assert max_verdict(findings) == "halt"


def test_every_guard_judges_the_original_proposal_first():
    # An amendment must never hide a breach from a guard that would have seen
    # it: the reducing guard is declared FIRST and the strict guard still
    # records a finding against the original 250.
    amender = Limit(limit_params(name=None, on_breach="amend", bound={"max": "100"}), name="cap")
    strict = a_limit(name="hard_cap", bound={"max": "60"})
    chain = chain_of(amender, strict)
    final, findings = chain.check_all(proposal(qty=Decimal("250")), tick_state())
    first_pass = [f for f in findings if f.guard == "hard_cap"][0]
    assert first_pass.value == Decimal("250")
    assert final.qty == Decimal("100")
    assert max_verdict(findings) == "refuse"


def test_two_amendments_on_one_field_compose_to_the_stricter_reduction():
    chain = chain_of(
        Limit(limit_params(on_breach="amend", bound={"max": "100"}), name="cap_a"),
        Limit(limit_params(on_breach="amend", bound={"max": "40"}), name="cap_b"),
    )
    final, findings = chain.check_all(proposal(qty=Decimal("250")), tick_state())
    assert final.qty == Decimal("40")
    assert [f.verdict for f in findings if f.guard.startswith("cap")] == ["amend", "amend"]
    assert max_verdict(findings) == "amend"


def test_the_stricter_reduction_wins_whichever_order_it_is_declared_in():
    strict_first = chain_of(
        Limit(limit_params(on_breach="amend", bound={"max": "40"}), name="cap_b"),
        Limit(limit_params(on_breach="amend", bound={"max": "100"}), name="cap_a"),
    )
    final, _ = strict_first.check_all(proposal(qty=Decimal("250")), tick_state())
    assert final.qty == Decimal("40")


def test_amendments_on_two_different_fields_refuse_rather_than_pick():
    chain = chain_of(
        Limit(limit_params(on_breach="amend", bound={"max": "40"}), name="qty_cap"),
        Limit(
            {"measure": "notional", "bound": {"max": "50"}, "on_breach": "amend"},
            name="notional_cap",
        ),
    )
    original = proposal(qty=Decimal("250"), notional=Decimal("2500"))
    final, findings = chain.check_all(original, tick_state())
    assert final == original
    assert max_verdict(findings) == "refuse"
    conflict = [f for f in findings if f.reason.startswith("conflicting_amendments")]
    assert len(conflict) == 1
    assert "qty" in conflict[0].reason
    assert "notional" in conflict[0].reason
    assert conflict[0].verdict == "refuse"


def test_the_final_candidate_is_re_run_through_every_hard_guard():
    amender = Limit(limit_params(on_breach="amend", bound={"max": "100"}), name="cap")
    strict = a_limit(name="hard_cap", bound={"max": "60"})
    chain = chain_of(amender, strict)
    _, findings = chain.check_all(proposal(qty=Decimal("250")), tick_state())
    hard = [f for f in findings if f.guard == "hard_cap"]
    assert [f.value for f in hard] == [Decimal("250"), Decimal("100")]
    assert [f.verdict for f in hard] == ["refuse", "refuse"]


def test_the_second_pass_can_clear_a_breach_the_amendment_cured():
    amender = Limit(limit_params(on_breach="amend", bound={"max": "100"}), name="cap")
    strict = a_limit(name="hard_cap", bound={"max": "120"})
    chain = chain_of(amender, strict)
    final, findings = chain.check_all(proposal(qty=Decimal("250")), tick_state())
    assert final.qty == Decimal("100")
    hard = [f for f in findings if f.guard == "hard_cap"]
    assert [f.verdict for f in hard] == ["refuse", "allow"]
    assert max_verdict(findings) == "refuse"


def test_a_remaining_breach_on_a_halting_guard_halts():
    amender = Limit(limit_params(on_breach="amend", bound={"max": "100"}), name="cap")
    halter = a_limit(name="killer", bound={"max": "60"}, on_breach="halt")
    _, findings = chain_of(amender, halter).check_all(proposal(qty=Decimal("250")), tick_state())
    assert max_verdict(findings) == "halt"


def test_the_second_pass_never_produces_an_amendment():
    chain = chain_of(
        Limit(limit_params(on_breach="amend", bound={"max": "100"}), name="cap"),
        Limit(limit_params(on_breach="amend", bound={"max": "80"}), name="cap_b"),
    )
    final, findings = chain.check_all(proposal(qty=Decimal("250")), tick_state())
    assert final.qty == Decimal("80")
    assert [f.guard for f in findings].count("cap") == 1


def test_no_second_pass_runs_when_nothing_was_amended():
    chain = chain_of(a_limit(name="size"), a_limit(name="second", bound={"max": "1000"}))
    _, findings = chain.check_all(proposal(qty=Decimal("10")), tick_state())
    assert len(findings) == 2


def test_the_chain_returns_findings_and_never_touches_the_frozen_state():
    state = tick_state()
    before = state.account.risk_digest()
    chain = chain_of(a_limit(name="killer", bound={"max": "1"}, on_breach="halt"))
    final, findings = chain.check_all(proposal(qty=Decimal("10")), state)
    assert isinstance(findings, tuple)
    assert all(isinstance(f, records.Finding) for f in findings)
    assert state.account.risk_digest() == before
    assert final is not None


# ---------------------------------------------------------------------------
# `GuardChain` — construction, surface, and the cancel rule (§5.5)
# ---------------------------------------------------------------------------


def test_the_chain_is_an_ordered_mapping_of_name_to_guard():
    size = a_limit(name="size")
    sane = RangeGuard({"field": "confidence", "min": 0, "max": 1}, name="sane")
    chain = GuardChain({"size": size, "sane": sane})
    assert tuple(chain.guards) == ("size", "sane")
    assert chain.guards["size"] is size


def test_the_chain_refuses_a_key_that_disagrees_with_its_guards_name():
    with pytest.raises(ProductionError):
        GuardChain({"exposure": a_limit(name="size")})


def test_the_chain_refuses_an_unnamed_guard():
    with pytest.raises(ProductionError):
        GuardChain({"size": Limit(limit_params())})


def test_an_empty_chain_allows_and_asks_for_nothing():
    chain = GuardChain({})
    final, findings = chain.check_all(proposal(), tick_state())
    assert findings == ()
    assert max_verdict(findings) == "allow"
    assert chain.requirements((candidate(),), AT_MS, CAL) == ()


def test_the_chains_public_surface_is_exactly_four_names():
    public = {name for name in dir(GuardChain) if not name.startswith("_")}
    assert public == {"requirements", "check_all", "check_authority_scope", "guards"}


def test_no_chain_verb_accepts_an_operation_or_a_cancel():
    # D9: cancels bypass proposal guards entirely. There is no seam for one —
    # not a parameter, not a verb — so a cancel cannot be routed here by
    # mistake.
    for name in ("requirements", "check_all", "check_authority_scope"):
        parameters = set(inspect.signature(getattr(GuardChain, name)).parameters)
        assert not parameters & {"operation", "cancel", "ack", "client_ref", "venue_ref"}


def test_handing_the_chain_a_cancel_instead_of_a_proposal_refuses():
    cancel = records.Ack(
        client_ref="cref-1",
        venue_ref="vref-1",
        status="cancelled",
        ts_ms=AT_MS,
        filled_qty=Decimal("0"),
        avg_price=None,
        fee=Decimal("0"),
        reason="operator",
        native={},
    )
    chain = chain_of(a_limit(name="size"))
    with pytest.raises((ProductionError, TypeError)):
        chain.check_all(cancel, tick_state())


def test_handing_the_chain_a_bare_client_ref_refuses():
    chain = chain_of(a_limit(name="size"))
    with pytest.raises((ProductionError, TypeError)):
        chain.check_all("cref-1", tick_state())


# ---------------------------------------------------------------------------
# `GuardChain.requirements` — the union accounting snapshots (§5.5, §5.7.1)
# ---------------------------------------------------------------------------


def session_limit(name="day_loss", **over):
    params = {
        "measure": "pnl",
        "window": {"calendar": "session"},
        "bound": {"min": "-500"},
        "on_breach": "halt",
    }
    params.update(over)
    return Limit(params, name=name)


def test_the_requirement_union_covers_every_candidate_scope_key():
    chain = chain_of(session_limit(scope="per_key"))
    candidates = (
        candidate("c1", "INS1", ("INS1",)),
        candidate("c2", "INS2", ("INS2", "sector:tech")),
    )
    declared = chain.requirements(candidates, AT_MS, CAL)
    assert sorted(r.scope_key for r in declared) == ["INS1", "INS2", "sector:tech"]
    assert all(isinstance(r, records.EvidenceRequirement) for r in declared)


def test_an_aggregate_limit_asks_once_however_many_candidates_there_are():
    chain = chain_of(session_limit(scope="aggregate"))
    declared = chain.requirements(
        (candidate("c1", "INS1", ("INS1",)), candidate("c2", "INS2", ("INS2",))), AT_MS, CAL
    )
    assert len(declared) == 1
    assert declared[0].scope_key == "*"


def test_two_limits_asking_the_same_question_deduplicate_to_one_requirement():
    chain = chain_of(session_limit(name="day_loss"), session_limit(name="day_loss_warn"))
    declared = chain.requirements((candidate(),), AT_MS, CAL)
    assert len(declared) == 1


def test_limits_that_differ_only_in_include_working_ask_twice():
    chain = chain_of(
        session_limit(name="with_working", include_working=True),
        session_limit(name="without_working", include_working=False),
    )
    declared = chain.requirements((candidate(),), AT_MS, CAL)
    assert len(declared) == 2
    assert sorted(r.include_working for r in declared) == [False, True]
    assert len({r.requirement_digest for r in declared}) == 2


def test_limits_that_differ_by_window_ask_twice():
    chain = chain_of(
        session_limit(name="session_loss"),
        session_limit(name="hour_loss", window={"duration": "PT1H"}),
    )
    declared = chain.requirements((candidate(),), AT_MS, CAL)
    assert len(declared) == 2
    assert sorted(r.window_kind for r in declared) == ["calendar", "duration"]


def test_a_range_guard_contributes_no_requirement():
    chain = chain_of(RangeGuard({"field": "confidence", "min": 0, "max": 1}, name="sane"))
    assert chain.requirements((candidate(),), AT_MS, CAL) == ()


def test_a_proposal_local_limit_contributes_no_requirement():
    assert chain_of(a_limit(name="size")).requirements((candidate(),), AT_MS, CAL) == ()


def test_the_requirement_union_is_a_deduplicated_tuple_in_a_stable_order():
    chain = chain_of(
        session_limit(name="a", scope="per_key"),
        session_limit(name="b", scope="per_key", include_working=False),
    )
    candidates = (candidate("c1", "INS1", ("INS1",)), candidate("c2", "INS2", ("INS2",)))
    first = chain.requirements(candidates, AT_MS, CAL)
    second = chain.requirements(candidates, AT_MS, CAL)
    assert isinstance(first, tuple)
    assert len(first) == 4
    assert len({r.requirement_digest for r in first}) == 4
    assert [r.requirement_digest for r in first] == [r.requirement_digest for r in second]


def test_the_requirement_union_resolves_calendar_windows_at_the_given_instant():
    chain = chain_of(session_limit())
    inside_b = SESSION_B[0] + HOUR
    declared = chain.requirements((candidate(),), inside_b, CAL)
    assert (declared[0].window_start_ms, declared[0].window_end_ms) == SESSION_B


# ---------------------------------------------------------------------------
# `check_authority_scope` — the last gate before a permit (§5.5, D11)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class FakeScope:
    """The arming scope a `GuardChain` re-applies: allowlist plus limits overlay."""

    allowlist: tuple = ()
    limits_overlay: dict = dataclasses.field(default_factory=dict)


def test_an_empty_allowlist_admits_every_instrument():
    chain = chain_of(a_limit(name="size"))
    verdict = chain.check_authority_scope(proposal(), tick_state(), FakeScope())
    assert isinstance(verdict, records.ScopeVerdict)
    assert verdict.allowed is True
    assert verdict.scope_key == "INS1"


def test_an_instrument_in_the_allowlist_is_admitted():
    chain = chain_of(a_limit(name="size"))
    verdict = chain.check_authority_scope(
        proposal(), tick_state(), FakeScope(allowlist=("INS1", "INS2"))
    )
    assert verdict.allowed is True


def test_an_instrument_outside_a_non_empty_allowlist_is_refused():
    chain = chain_of(a_limit(name="size"))
    verdict = chain.check_authority_scope(
        proposal(instrument="INS9"), tick_state(), FakeScope(allowlist=("INS1",))
    )
    assert verdict.allowed is False
    assert verdict.scope_key == "INS9"
    assert "INS9" in verdict.reason


def test_a_tighter_overlay_bound_is_re_measured_against_the_final_proposal():
    chain = chain_of(a_limit(name="size", bound={"max": "100"}))
    scope = FakeScope(limits_overlay={"size": {"bound": {"max": "5"}}})
    refused = chain.check_authority_scope(proposal(qty=Decimal("10")), tick_state(), scope)
    assert refused.allowed is False
    assert "size" in refused.reason
    allowed = chain.check_authority_scope(proposal(qty=Decimal("4")), tick_state(), scope)
    assert allowed.allowed is True


def test_an_overlay_naming_a_guard_the_chain_does_not_have_refuses():
    chain = chain_of(a_limit(name="size"))
    with pytest.raises(ProductionError):
        chain.check_authority_scope(
            proposal(), tick_state(), FakeScope(limits_overlay={"nosuch": {"bound": {"max": "1"}}})
        )


def test_an_overlay_looser_than_the_document_refuses_rather_than_widening():
    # D11: an overlay may only tighten. Applying a looser bound at the last
    # gate would silently widen a limit the document set.
    chain = chain_of(a_limit(name="size", bound={"max": "100"}))
    with pytest.raises(ProductionError):
        chain.check_authority_scope(
            proposal(), tick_state(), FakeScope(limits_overlay={"size": {"bound": {"max": "500"}}})
        )


def test_the_scope_check_reads_the_exact_final_proposal():
    chain = chain_of(a_limit(name="size", bound={"max": "1000"}))
    scope = FakeScope(limits_overlay={"size": {"bound": {"max": "100"}}})
    amender = Limit(limit_params(on_breach="amend", bound={"max": "100"}), name="cap")
    full = chain_of(amender, a_limit(name="size", bound={"max": "1000"}))
    final, _ = full.check_all(proposal(qty=Decimal("250")), tick_state())
    assert final.qty == Decimal("100")
    assert chain.check_authority_scope(final, tick_state(), scope).allowed is True


# ---------------------------------------------------------------------------
# `RangeGuard` (§5.5)
# ---------------------------------------------------------------------------


def test_the_range_knobs_are_exactly_field_min_max_and_nan():
    assert set(RangeGuard._PARAMS) == {"field", "min", "max", "nan", "notes"}


@pytest.mark.parametrize(
    "params",
    (
        {"field": "confidence", "min": 0, "max": 1, "bins": 3},
        {"min": 0, "max": 1},
        {"field": "not_a_proposal_field", "min": 0, "max": 1},
        {"field": "confidence"},
        {"field": "confidence", "min": 1, "max": 0},
        {"field": "confidence", "min": 0, "max": 1, "nan": "ignore"},
        {"field": "confidence", "min": "low", "max": 1},
        {"field": 7, "min": 0, "max": 1},
    ),
)
def test_one_refusal_per_range_knob(params):
    with pytest.raises(ProductionError):
        RangeGuard(params, name="sane")


def test_the_nan_policy_defaults_to_refuse():
    assert RangeGuard({"field": "confidence", "min": 0, "max": 1}, name="sane").nan == "refuse"
    assert set(vocab.NAN_POLICY) == {"refuse", "allow"}


@pytest.mark.parametrize(
    ("confidence", "verdict"),
    ((0.0, "allow"), (0.5, "allow"), (1.0, "allow"), (1.5, "refuse"), (-0.1, "refuse")),
)
def test_a_range_guard_holds_a_field_inside_its_inclusive_bounds(confidence, verdict):
    guard = RangeGuard({"field": "confidence", "min": 0, "max": 1}, name="sane")
    finding = guard.check(proposal(confidence=confidence), tick_state())
    assert finding.verdict == verdict
    assert finding.guard == "sane"
    assert finding.measure == "confidence"
    assert finding.value == Decimal(str(confidence))
    assert finding.window == "none"
    assert finding.scope_key == "INS1"
    assert finding.reason


def loose(**over):
    values = {
        "id": "cand-1",
        "instrument": "INS1",
        "side": "buy",
        "qty": Decimal("10"),
        "notional": Decimal("100"),
        "limit": Decimal("10.50"),
        "reference_price": Decimal("10"),
        "confidence": 0.61,
        "extra": {},
    }
    values.update(over)
    return LooseProposal(**values)


def test_a_nan_field_refuses_under_the_refuse_policy():
    guard = RangeGuard({"field": "confidence", "min": 0, "max": 1, "nan": "refuse"}, name="sane")
    finding = guard.check(loose(confidence=float("nan")), tick_state())
    assert finding.verdict == "refuse"
    assert finding.value is None
    assert "nan" in finding.reason.lower()


def test_a_nan_field_passes_under_the_allow_policy():
    guard = RangeGuard({"field": "confidence", "min": 0, "max": 1, "nan": "allow"}, name="sane")
    finding = guard.check(loose(confidence=float("nan")), tick_state())
    assert finding.verdict == "allow"


def test_a_range_guard_may_bound_a_money_field_as_a_decimal():
    guard = RangeGuard({"field": "qty", "min": 1, "max": 100}, name="qty_range")
    assert guard.check(proposal(qty=Decimal("50")), tick_state()).verdict == "allow"
    assert guard.check(proposal(qty=Decimal("0.5")), tick_state()).verdict == "refuse"


def test_a_range_guard_never_amends():
    guard = RangeGuard({"field": "confidence", "min": 0, "max": 1}, name="sane")
    assert not hasattr(guard, "amend")
    assert guard.check(proposal(confidence=1.5), tick_state()).verdict != "amend"


# ---------------------------------------------------------------------------
# The seventeen measures — value on a hand-built state (§5.5)
# ---------------------------------------------------------------------------

NONE_WINDOW = Window.from_params({})


def value_of(kind, prop=None, state=None, window=None, scope_key="*", include_working=True):
    return measure_of(kind).value(
        prop if prop is not None else proposal(),
        state if state is not None else tick_state(),
        window or NONE_WINDOW,
        scope_key,
        include_working,
    )


def test_quantity_is_the_proposals_own_size():
    assert value_of("quantity", proposal(qty=Decimal("7.5"))) == Decimal("7.5")


def test_notional_is_the_proposals_declared_value():
    assert value_of("notional", proposal(notional=Decimal("100"))) == Decimal("100")


def test_notional_falls_back_to_size_times_reference_price():
    prop = proposal(notional=None, qty=Decimal("3"), reference_price=Decimal("2.5"))
    assert value_of("notional", prop) == Decimal("7.5")


def test_exposure_is_the_accounts_absolute_position_value():
    # positions 5 @ 20 = 100; working 2 @ 25 = 50.
    assert value_of("exposure", include_working=False) == Decimal("100")
    assert value_of("exposure", include_working=True) == Decimal("150")


def test_exposure_counts_a_short_position_at_its_absolute_size():
    state = tick_state(acct=account(positions=(position(qty="-5", avg_cost="20"),), orders=()))
    assert value_of("exposure", state=state, include_working=False) == Decimal("100")


def test_exposure_under_a_per_key_scope_covers_only_that_instrument():
    state = tick_state(
        acct=account(
            positions=(position("INS1", "5", "20"), position("INS2", "3", "10")), orders=()
        )
    )
    assert value_of("exposure", state=state, scope_key="INS1", include_working=False) == Decimal(
        "100"
    )
    assert value_of("exposure", state=state, scope_key="INS2", include_working=False) == Decimal(
        "30"
    )


def test_exposure_after_adds_this_proposals_signed_notional():
    buy = proposal(side="buy", qty=Decimal("10"), reference_price=Decimal("10"))
    sell = proposal(side="sell", qty=Decimal("10"), reference_price=Decimal("10"))
    assert value_of("exposure_after", buy) == Decimal("250")
    assert value_of("exposure_after", sell) == Decimal("50")


def test_price_deviation_is_the_gap_from_the_reference_as_a_fraction():
    prop = proposal(limit=Decimal("10.50"), reference_price=Decimal("10"))
    assert value_of("price_deviation", prop) == Decimal("0.05")


def test_price_deviation_is_absolute():
    prop = proposal(limit=Decimal("9.50"), reference_price=Decimal("10"))
    assert value_of("price_deviation", prop) == Decimal("0.05")


def test_price_deviation_of_a_market_order_is_zero():
    assert value_of("price_deviation", proposal(limit=None)) == Decimal("0")


def test_open_orders_counts_the_accounts_working_orders():
    state = tick_state(
        acct=account(orders=(working(ref="a"), working(ref="b"), working("INS2", ref="c")))
    )
    assert value_of("open_orders", state=state) == Decimal("3")
    assert value_of("open_orders", state=state, scope_key="INS2") == Decimal("1")


def test_input_age_ms_is_the_oldest_required_key_not_the_freshest():
    # D6: one fresh instrument cannot hide a stale input.
    state = tick_state(
        feed_ages=(feed_age("INS1", 1_000), feed_age("INS2", 45_000), feed_age("INS3", 7_000))
    )
    assert value_of("input_age_ms", state=state) == Decimal("45000")


def test_feed_age_ms_reads_the_key_the_scope_names():
    state = tick_state(feed_ages=(feed_age("INS1", 1_000), feed_age("INS2", 45_000)))
    assert value_of("feed_age_ms", state=state, scope_key="INS2") == Decimal("45000")


def test_feed_age_ms_refuses_a_key_the_coverage_does_not_carry():
    state = tick_state(feed_ages=(feed_age("INS1", 1_000),))
    with pytest.raises(ProductionError):
        value_of("feed_age_ms", state=state, scope_key="INS9")


def test_confidence_is_the_proposals_own_number():
    assert value_of("confidence", proposal(confidence=0.61)) == pytest.approx(0.61)


def test_bankroll_fraction_is_this_proposal_against_the_cash_base():
    state = tick_state(acct=account(balances=(balance(total="1000"),)))
    assert value_of("bankroll_fraction", proposal(notional=Decimal("100")), state) == pytest.approx(
        0.1
    )


HISTORY = (
    leg("INS1", "buy", "leg-1"),
    leg("INS1", "sell", "leg-2"),
    leg("INS2", "buy", "leg-3"),
    leg("INS1", "buy", "leg-4"),
)


def test_decision_count_reads_the_views_decision_history():
    state = tick_state(view=FakeView(decision_history=HISTORY))
    assert value_of("decision_count", state=state) == Decimal("4")
    assert value_of("decision_count", state=state, scope_key="INS1") == Decimal("3")


def test_decision_count_over_a_count_window_reads_only_the_last_entries():
    state = tick_state(view=FakeView(decision_history=HISTORY))
    window = Window.from_params({"count": 2})
    assert value_of("decision_count", state=state, window=window) == Decimal("2")


def test_identical_count_counts_the_same_instrument_and_side():
    state = tick_state(view=FakeView(decision_history=HISTORY))
    prop = proposal(instrument="INS1", side="buy")
    assert value_of("identical_count", prop, state) == Decimal("2")
    assert value_of("identical_count", proposal(instrument="INS1", side="sell"), state) == Decimal(
        "1"
    )


def test_direction_changes_counts_the_flips_in_the_window():
    state = tick_state(view=FakeView(decision_history=HISTORY))
    assert value_of("direction_changes", state=state, scope_key="INS1") == Decimal("2")


@pytest.mark.parametrize("kind", EVIDENCE_MEASURES)
def test_an_evidence_backed_measure_reads_the_accounts_snapshot(kind):
    window = Window.from_params({"calendar": "session"})
    requirement = measure_of(kind).requirements(candidate(), window, "*", AT_MS, CAL, True)[0]
    state = tick_state(
        acct=account(
            measure_evidence={requirement.requirement_digest: {"*": evidence(requirement, "-12.5")}}
        )
    )
    assert value_of(kind, state=state, window=window) == Decimal("-12.5")


@pytest.mark.parametrize("kind", EVIDENCE_MEASURES)
def test_an_evidence_backed_measure_refuses_when_the_answer_is_missing(kind):
    window = Window.from_params({"calendar": "session"})
    with pytest.raises(ProductionError):
        value_of(kind, state=tick_state(acct=account(measure_evidence={})), window=window)


@pytest.mark.parametrize("kind", STDLIB_MEASURES)
def test_only_the_two_dimensionless_ratios_are_floats(kind):
    window = Window.from_params({"calendar": "session"}) if kind in EVIDENCE_MEASURES else NONE_WINDOW
    state = tick_state(view=FakeView(decision_history=HISTORY))
    if kind in EVIDENCE_MEASURES:
        requirement = measure_of(kind).requirements(candidate(), window, "*", AT_MS, CAL, True)[0]
        state = tick_state(
            acct=account(
                measure_evidence={
                    requirement.requirement_digest: {"*": evidence(requirement, "-1")}
                }
            ),
            view=FakeView(decision_history=HISTORY),
        )
    scope_key = "INS1" if kind == "feed_age_ms" else "*"
    value = value_of(kind, state=state, window=window, scope_key=scope_key)
    if kind in FLOAT_MEASURES:
        assert isinstance(value, float)
    else:
        assert isinstance(value, Decimal)


def test_a_finding_records_a_ratio_as_a_decimal_even_though_the_measure_returns_a_float():
    # `records.Finding` admits no float: a Decimal is what the ledger carries.
    limit = Limit({"measure": "confidence", "bound": {"max": "0.9"}, "on_breach": "refuse"}, name="conf")
    finding = limit.check(proposal(confidence=0.61), tick_state())
    assert isinstance(finding.value, Decimal)
    assert finding.value == Decimal("0.61")


def test_a_bankroll_finding_is_a_decimal_too():
    limit = Limit(
        {"measure": "bankroll_fraction", "bound": {"max": "0.25"}, "on_breach": "refuse"},
        name="bankroll",
    )
    state = tick_state(acct=account(balances=(balance(total="1000"),)))
    finding = limit.check(proposal(notional=Decimal("100")), state)
    assert isinstance(finding.value, Decimal)
    assert finding.value == Decimal("0.1")
    assert finding.verdict == "allow"


# ---------------------------------------------------------------------------
# §6's cash_flow partition — an external deposit is not profit
# ---------------------------------------------------------------------------


def test_a_cash_flow_cannot_move_a_pnl_bound():
    limit = session_limit()
    requirement = requirement_for(limit)
    evidence_map = {requirement.requirement_digest: {"*": evidence(requirement, "-480")}}
    before = tick_state(
        acct=account(balances=(balance(total="1000"),), measure_evidence=evidence_map)
    )
    # The same account after folding an external deposit: the capital base
    # grows, the trading evidence does not.
    after = tick_state(
        acct=account(balances=(balance(total="6000", available="5900"),), measure_evidence=evidence_map)
    )
    assert value_of("pnl", state=before, window=limit.window) == Decimal("-480")
    assert value_of("pnl", state=after, window=limit.window) == Decimal("-480")
    assert limit.check(proposal(), before).verdict == limit.check(proposal(), after).verdict


def test_a_cash_flow_does_move_the_capital_base_measures():
    small = tick_state(acct=account(balances=(balance(total="1000"),)))
    large = tick_state(acct=account(balances=(balance(total="6000", available="5900"),)))
    prop = proposal(notional=Decimal("100"))
    assert value_of("bankroll_fraction", prop, small) != value_of("bankroll_fraction", prop, large)
    assert value_of("bankroll_fraction", prop, small) == pytest.approx(0.1)


def test_a_deposit_cannot_buy_headroom_under_a_loss_halt():
    # The defect §6 names: an adopted deposit inflating a `pnl` halt guard into
    # headroom. The halt must fire on both accounts.
    limit = session_limit()
    requirement = requirement_for(limit)
    evidence_map = {requirement.requirement_digest: {"*": evidence(requirement, "-501")}}
    for total in ("1000", "1000000"):
        state = tick_state(
            acct=account(balances=(balance(total=total, available=total),), measure_evidence=evidence_map)
        )
        assert limit.check(proposal(), state).verdict == "halt"


# ---------------------------------------------------------------------------
# Structure — what a measure may read (§5.8.1 ECONOMIC_ATTRS, AST)
# ---------------------------------------------------------------------------

GUARDS_SOURCE = pathlib.Path(guards_module.__file__).read_text(encoding="utf-8")
GUARDS_TREE = ast.parse(GUARDS_SOURCE)


def attribute_paths(tree):
    """Every dotted attribute chain in `tree`, as a tuple of names ending at the root."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        chain = []
        current = node
        while isinstance(current, ast.Attribute):
            chain.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            chain.append(current.id)
        found.append((tuple(reversed(chain)), node.lineno))
    return found


def reads_view_economics(tree):
    """Lines where a `.view.<economic attr>` is read — the fold at head, not the account."""
    return [
        (path, line)
        for path, line in attribute_paths(tree)
        if len(path) >= 2 and path[-2] == "view" and path[-1] in vocab.ECONOMIC_ATTRS
    ]


def class_named(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"guards.py defines no class {name!r}")


def test_the_ast_scanner_catches_what_it_forbids():
    planted = ast.parse("class Bad:\n    def value(self, p, s, w, k, iw):\n        return s.view.positions\n")
    assert reads_view_economics(planted)
    assert not reads_view_economics(ast.parse("x = s.account.positions\n"))


def test_no_guard_or_measure_reads_economics_off_the_fold_at_head():
    # §5.8.1: `working` and `balances` matter as much as `positions` —
    # `exposure_after`, `open_orders` and `bankroll_fraction` are exactly the
    # measures that would reach for the fold and miss prior legs' reservations.
    assert vocab.ECONOMIC_ATTRS == ("positions", "working", "balances")
    assert reads_view_economics(GUARDS_TREE) == []


def test_the_view_is_still_read_for_the_things_it_owns():
    # The scan above must not be passing because nothing reads the view at all.
    paths = {path[-1] for path, _ in attribute_paths(GUARDS_TREE) if "view" in path}
    assert {"guard_holds", "decision_history"} <= paths


@pytest.mark.parametrize("kind", TRADING_ONLY_MEASURES)
def test_a_trading_measure_reads_evidence_and_never_a_balance(kind):
    node = class_named(GUARDS_TREE, MEASURE_KINDS.resolve(kind).__name__)
    attrs = {path[-1] for path, _ in attribute_paths(node)}
    assert "measure_evidence" in attrs
    assert not attrs & set(vocab.ECONOMIC_ATTRS)


def test_the_capital_base_measures_do_read_balances():
    # The complement of the test above: the partition is real only if the other
    # side of it actually reads what the trading measures may not.
    node = class_named(GUARDS_TREE, MEASURE_KINDS.resolve("bankroll_fraction").__name__)
    assert "balances" in {path[-1] for path, _ in attribute_paths(node)}


def test_guards_never_reaches_for_a_rung():
    # §5.8.1: `TickState` carries no rung, and D2 keeps it out of decision code.
    assert "rung" not in {path[-1] for path, _ in attribute_paths(GUARDS_TREE)}
    assert "rung" not in GUARDS_SOURCE


def test_guards_reads_no_clock_of_its_own():
    for banned in ("time.time", "time.monotonic", "datetime.now", "datetime.utcnow"):
        assert banned not in GUARDS_SOURCE


def test_every_public_name_guards_exports_is_declared():
    exported = set(guards_module.__all__)
    assert not any(name.startswith("_") for name in exported)
    assert {
        "Guard",
        "GuardChain",
        "Limit",
        "RangeGuard",
        "Measure",
        "Window",
        "Bound",
        "Scope",
        "GUARD_KINDS",
        "MEASURE_KINDS",
        "max_verdict",
        "BREACH_VERDICTS",
    } <= exported


# ---------------------------------------------------------------------------
# The property D9 exists for: a loss halt fires before the bound is overshot by
# more than one loss (§8).
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(
    losses=st.lists(st.integers(min_value=1, max_value=120), min_size=1, max_size=40),
    max_single_loss=st.integers(min_value=1, max_value=120),
)
def test_a_day_loss_halt_fires_before_the_bound_is_overshot_by_more_than_one_loss(
    losses, max_single_loss
):
    losses = [min(loss, max_single_loss) for loss in losses]
    limit = session_limit()
    assert limit.bound.min == Decimal("-500")
    cumulative = Decimal("0")
    first_halt = None
    for loss in losses:
        cumulative -= Decimal(loss)
        finding = limit.check(proposal(), pnl_state(str(cumulative), limit))
        if finding.verdict == "halt":
            first_halt = cumulative
            break
        assert finding.verdict in ("allow", "warn")
    if first_halt is not None:
        assert first_halt < limit.bound.min
        assert first_halt >= limit.bound.min - Decimal(max_single_loss)
    else:
        assert cumulative >= limit.bound.min


@settings(max_examples=100, deadline=None)
@given(value=st.integers(min_value=-100_000, max_value=100_000))
def test_a_limit_verdict_is_always_a_member_of_the_lattice(value):
    limit = session_limit(warn_at=0.8)
    finding = limit.check(proposal(), pnl_state(str(value), limit))
    assert finding.verdict in vocab.VERDICTS
    assert finding.value == Decimal(value)
    assert finding.bound == Decimal("-500")
    assert finding.reason


# ---------------------------------------------------------------------------
# A child measure, referenced by path (§4.3) — defined last so the AST scans
# above see only the package's own classes.
# ---------------------------------------------------------------------------


class ChildMeasure(Measure):
    """A child's own measure, reachable only as `pkg.module:Class`."""

    kind = "child_edge"
    scalable = False
    scalable_field = None
    window_kinds = ("none",)

    def requirements(self, candidate, window, scope_key, at_ms, calendar, include_working):
        return ()

    def value(self, proposal, state, window, scope_key, include_working):
        return Decimal(str(proposal.expected_value))


def test_a_child_measure_answers_through_the_same_limit():
    limit = Limit(
        {
            "measure": "tests.production.test_guards:ChildMeasure",
            "bound": {"max": "0.10"},
            "on_breach": "refuse",
        },
        name="edge",
    )
    finding = limit.check(proposal(expected_value=0.03), tick_state())
    assert finding.verdict == "allow"
    assert finding.measure == "child_edge"
    assert finding.value == Decimal("0.03")
    assert not math.isnan(float(finding.value))
