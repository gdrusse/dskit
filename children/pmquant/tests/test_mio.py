"""The fractional-Kelly MIO: the parent's oracles restated independently.

An independent brute-force single-contract Kelly is the oracle the solver
is held to; the scenario laws, the wealth bounds, the utility family and
the post-solve recompute are each pinned on their own. Nothing here reads
its expectation from the module under test.
"""

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pmquant.books import (
    IncompleteBookError,
    contract_inputs_from_book,
)
from pmquant.fees import DEFAULT_FILL_FEE_POLICY, FeeBook
from pmquant.mio import (
    DEFAULT_DEPTH_HAIRCUT,
    DEFAULT_MIN_LOT,
    DEFAULT_N_TANGENTS,
    DEFAULT_ON_EXACT_FEE_EXCEEDED,
    DEFAULT_RETIGHTEN_ROUNDS,
    DEFAULT_TAU,
    ON_EXACT_FEE_EXCEEDED,
    REFUSED_STATUS,
    RETIGHTEN_MIN_STEP,
    ROUND_UP_CENT,
    DegenerateScenarioLawError,
    EventInputs,
    ExactFeeBudgetExceeded,
    ExactFeeResolution,
    ScenarioSet,
    empty_allocation,
    event_program,
    gated_sides,
    mutually_exclusive_scenarios,
    read_allocation,
    refused_allocation,
    solve_event,
    threshold_scenarios,
    utility_at,
    wealth_bounds,
)

SERIES = "KXTEST"
POLY_SERIES = "POLYTEST"
W0, B = 1000.0, 500.0
DEPTH = 2000
RATE = 0.07
LAMBDA = 0.5

#: The doorway's HiGHS determinism pins, restated (the node injects them).
HIGHS_PINS = {"mip_rel_gap": 0, "threads": 1, "random_seed": 0}


@pytest.fixture(scope="module")
def solver():
    from pyomo.environ import SolverFactory

    highs = SolverFactory("appsi_highs")
    assert highs.available(exception_flag=False)
    for key, value in HIGHS_PINS.items():
        highs.options[key] = value
    return highs


class NeverSolve:
    """A solver stand-in that must not be woken."""

    def solve(self, model):
        raise AssertionError("the solver was invoked on an event with nothing to size")


def _contract(q, ask=0.30, bid=0.28, depth=DEPTH, rate=RATE, cid="C"):
    return contract_inputs_from_book(
        cid, q, yes_bids=[[bid, depth]], no_bids=[[1.0 - ask, depth]], fee_rate=rate
    )


def _inputs(contracts, scenarios=None, **over):
    kw = dict(
        event_id="E",
        contracts=contracts,
        scenarios=scenarios or mutually_exclusive_scenarios(contracts),
        bankroll=W0,
        deployable=B,
        kelly_fraction=LAMBDA,
        series=SERIES,
        n_tangents=512,
    )
    kw.update(over)
    return EventInputs(**kw)


# --- the oracle ------------------------------------------------------------


def _u(w, w0, lam):
    if lam >= 1.0:
        return math.log(w / w0)
    gamma = 1.0 / lam
    return ((w / w0) ** (1.0 - gamma) - 1.0) / (1.0 - gamma)


def oracle_yes(q, ask, rate, w0, budget, depth, lam, min_lot=1):
    """Brute-force single-contract fractional Kelly on the YES side.

    This oracle predicts WHAT THE SOLVER CHOOSES, so it optimizes the
    program's own LINEAR cost — ``phi = rate*ask*(1-ask)``,
    ``outlay(n) = n*ask + n*phi + 0.01`` — which is what the MILP
    maximizes against. The growth it REPORTS comes back from
    :func:`growth_yes`, which bills the exact venue fee instead: the two
    fee models have different jobs and the test holds each to its own.

    ``w_yes = w0 + n - outlay``; ``w_no = w0 - outlay``; the best ``n``
    maximizes ``q*u(w_yes) + (1-q)*u(w_no)`` over every feasible lot count
    (``n = 0`` scores ``u(w0) = 0``; a positive ``n`` starts at ``min_lot``).
    """
    phi = rate * ask * (1.0 - ask)
    best_n, best_val = 0, 0.0
    n_max = min(depth, int(math.floor((budget - 0.01) / (ask + phi))))
    for n in range(min_lot, n_max + 1):
        outlay = n * ask + n * phi + 0.01
        val = q * _u(w0 + n - outlay, w0, lam) + (1.0 - q) * _u(w0 - outlay, w0, lam)
        if val > best_val:
            best_n, best_val = n, val
    return best_n, growth_yes(best_n, q, ask, rate, w0)


def growth_yes(n, q, ask, rate, w0):
    """Expected log growth of ``n`` YES lots at one level, on the EXACT bill.

    The outlay is the independent oracle's — premium plus the venue's own
    rounded fee on the whole position — never the program's linear phi.
    """
    if n == 0:
        return 0.0
    outlay = oracle_cash("kalshi", ((ask, n),), rate)
    return q * math.log((w0 + n - outlay) / w0) + (1.0 - q) * math.log((w0 - outlay) / w0)


# --- constants ------------------------------------------------------------


def test_the_defaults_are_the_documented_ones():
    assert DEFAULT_N_TANGENTS == 128
    assert DEFAULT_TAU == 0.0
    assert DEFAULT_DEPTH_HAIRCUT == 1.0
    assert DEFAULT_MIN_LOT == 1
    assert ROUND_UP_CENT == 0.01
    # the exact-fee contract's own constants
    assert DEFAULT_FILL_FEE_POLICY == "order_vwap"
    assert ON_EXACT_FEE_EXCEEDED == ("refuse", "retighten")
    assert DEFAULT_ON_EXACT_FEE_EXCEEDED == "refuse"  # fail closed, never report the estimate
    assert DEFAULT_RETIGHTEN_ROUNDS == 4
    assert REFUSED_STATUS == "refused-exact-fee"
    # two facts that happen to share a value; the pin is what keeps them honest
    assert RETIGHTEN_MIN_STEP == 0.01
    assert RETIGHTEN_MIN_STEP == ROUND_UP_CENT


# --- scenario laws ----------------------------------------------------------


def test_scenario_set_validates_its_shape():
    law = ScenarioSet([0.4, 0.6], {"A": [1, 0], "B": [0, 1]})
    assert law.n_omega == 2 and law.contract_ids == ("A", "B")
    assert list(law.payoff_of("A")) == [1.0, 0.0]
    with pytest.raises(ValueError, match="sum"):
        ScenarioSet([0.4, 0.5], {"A": [1, 0]})
    with pytest.raises(ValueError, match="negative|>= 0"):
        ScenarioSet([1.2, -0.2], {"A": [1, 0]})
    with pytest.raises(ValueError, match="binary"):
        ScenarioSet([0.5, 0.5], {"A": [0.5, 1]})
    with pytest.raises(ValueError, match="length|shape"):
        ScenarioSet([0.5, 0.5], {"A": [1]})
    with pytest.raises(ValueError, match="payoffs"):
        ScenarioSet([1.0], {})


def test_single_contract_is_bernoulli():
    law = mutually_exclusive_scenarios([_contract(0.40)])
    assert list(law.weights) == pytest.approx([0.40, 0.60])
    assert list(law.payoff_of("C")) == [1.0, 0.0]
    # exhaustive or not, one rung is a coin, never a certainty
    law2 = mutually_exclusive_scenarios([_contract(0.40)], exhaustive=False)
    assert list(law2.weights) == pytest.approx([0.40, 0.60])


def test_exhaustive_partition_renormalizes():
    a, b = _contract(0.5, cid="A"), _contract(0.3, cid="B")
    law = mutually_exclusive_scenarios([a, b], exhaustive=True)
    assert list(law.weights) == pytest.approx([0.625, 0.375])
    assert list(law.payoff_of("A")) == [1.0, 0.0]
    assert list(law.payoff_of("B")) == [0.0, 1.0]


def test_non_exhaustive_partition_appends_a_none_cell():
    a, b = _contract(0.5, cid="A"), _contract(0.3, cid="B")
    law = mutually_exclusive_scenarios([a, b], exhaustive=False)
    assert list(law.weights) == pytest.approx([0.5, 0.3, 0.2])
    assert list(law.payoff_of("A")) == [1.0, 0.0, 0.0]
    assert list(law.payoff_of("B")) == [0.0, 1.0, 0.0]
    # a sum over one renormalizes even when not exhaustive
    over = mutually_exclusive_scenarios(
        [_contract(0.8, cid="A"), _contract(0.4, cid="B")], exhaustive=False
    )
    assert list(over.weights) == pytest.approx([2 / 3, 1 / 3])
    # a sum within dust of one gets no none cell
    tight = mutually_exclusive_scenarios(
        [_contract(0.6, cid="A"), _contract(0.4, cid="B")], exhaustive=False
    )
    assert tight.n_omega == 2


def test_degenerate_partition_refuses():
    a, b = _contract(0.0, cid="A"), _contract(0.0, cid="B")
    with pytest.raises(DegenerateScenarioLawError):
        mutually_exclusive_scenarios([a, b], exhaustive=True)
    with pytest.raises(ValueError, match="duplicate"):
        mutually_exclusive_scenarios([_contract(0.5), _contract(0.4)])
    with pytest.raises(ValueError, match="contract"):
        mutually_exclusive_scenarios([])


def test_upper_threshold_cuts_sum_to_one_and_nest():
    rungs = [_contract(0.6, cid="G10"), _contract(0.7, cid="G20"), _contract(0.2, cid="G30")]
    law = threshold_scenarios(rungs, ("greater",))
    # q projected onto non-increasing order: [0.6, 0.6, 0.2]
    assert list(law.weights) == pytest.approx([0.4, 0.0, 0.4, 0.2])
    assert list(law.payoff_of("G10")) == [0.0, 1.0, 1.0, 1.0]
    assert list(law.payoff_of("G20")) == [0.0, 0.0, 1.0, 1.0]
    assert list(law.payoff_of("G30")) == [0.0, 0.0, 0.0, 1.0]


def test_lower_threshold_is_the_mirror():
    rungs = [_contract(0.2, cid="L10"), _contract(0.5, cid="L20"), _contract(0.4, cid="L30")]
    law = threshold_scenarios(rungs, "less")
    # q projected onto non-decreasing order: [0.2, 0.5, 0.5]
    assert list(law.weights) == pytest.approx([0.2, 0.3, 0.0, 0.5])
    assert list(law.payoff_of("L10")) == [1.0, 0.0, 0.0, 0.0]
    assert list(law.payoff_of("L20")) == [1.0, 1.0, 0.0, 0.0]
    assert list(law.payoff_of("L30")) == [1.0, 1.0, 1.0, 0.0]


def test_threshold_refuses_two_tails_and_unknown_tails():
    rungs = [_contract(0.6, cid="A"), _contract(0.2, cid="B")]
    with pytest.raises(ValueError, match="two"):
        threshold_scenarios(rungs, ("less", "greater"))
    with pytest.raises(ValueError, match="tail"):
        threshold_scenarios(rungs, "between")
    with pytest.raises(ValueError, match="tail"):
        threshold_scenarios(rungs, ())


@given(
    qs=st.lists(st.floats(min_value=0.0, max_value=1.0), min_size=1, max_size=5),
    tail=st.sampled_from(["less", "greater"]),
)
@settings(max_examples=100, deadline=None)
def test_threshold_law_is_a_probability_law_with_nested_cuts(qs, tail):
    rungs = [_contract(q, cid=f"R{i}") for i, q in enumerate(qs)]
    law = threshold_scenarios(rungs, tail)
    assert law.n_omega == len(qs) + 1
    assert float(law.weights.sum()) == pytest.approx(1.0, abs=1e-9)
    assert all(w >= 0.0 for w in law.weights)
    # every scenario's paying set is a prefix (greater) / suffix (less) of the rungs
    for o in range(law.n_omega):
        pays = [law.payoff_of(f"R{i}")[o] for i in range(len(qs))]
        if tail == "greater":
            assert pays == sorted(pays, reverse=True)
        else:
            assert pays == sorted(pays)


@given(qs=st.lists(st.floats(min_value=0.001, max_value=1.0), min_size=2, max_size=5))
@settings(max_examples=100, deadline=None)
def test_partition_law_is_a_probability_law(qs):
    rungs = [_contract(q, cid=f"R{i}") for i, q in enumerate(qs)]
    for exhaustive in (True, False):
        law = mutually_exclusive_scenarios(rungs, exhaustive=exhaustive)
        assert float(law.weights.sum()) == pytest.approx(1.0, abs=1e-9)
        assert all(w >= 0.0 for w in law.weights)
        # exactly one rung pays in every rung scenario; none in a none cell
        for o in range(law.n_omega):
            assert sum(law.payoff_of(f"R{i}")[o] for i in range(len(qs))) in (0.0, 1.0)


# --- utility + bounds --------------------------------------------------------


def test_utility_family():
    import numpy as np

    w = np.array([800.0, 1000.0, 1250.0])
    u, du = utility_at(w, 1000.0, 1.0)
    assert list(u) == pytest.approx(list(np.log(w / 1000.0)))
    assert list(du) == pytest.approx(list(1.0 / w))
    u2, du2 = utility_at(w, 1000.0, 0.5)  # gamma = 2 -> 1 - W0/w
    assert list(u2) == pytest.approx(list(1.0 - 1000.0 / w))
    assert list(du2) == pytest.approx(list(1000.0 / w**2))
    assert u2[1] == pytest.approx(0.0)


def test_wealth_bounds_follow_the_specification():
    inputs = _inputs([_contract(0.40)])
    sides = gated_sides(inputs)
    assert [s.key for s in sides] == [("C", "yes")]
    lo, hi = wealth_bounds(inputs, sides)
    assert lo == pytest.approx(W0 - B)
    # min(depth, B/min_price + 1) * (1 - min_price), one paying side at 0.30
    assert hi == pytest.approx(W0 + min(DEPTH, B / 0.30 + 1.0) * 0.70)


def test_event_inputs_refuse_bad_knobs():
    c = _contract(0.40)
    law = mutually_exclusive_scenarios([c])
    good = dict(event_id="E", contracts=[c], scenarios=law, bankroll=W0, deployable=B,
                kelly_fraction=0.5, series=SERIES)
    EventInputs(**good)
    for bad in (
        {"deployable": W0}, {"deployable": 0.0}, {"kelly_fraction": 0.0},
        {"kelly_fraction": 1.5}, {"min_lot": 0}, {"min_lot": True}, {"tau": -0.1},
        {"depth_haircut": 0.0}, {"depth_haircut": 1.5}, {"n_tangents": 1},
        {"event_cap": 0.0}, {"series": ""}, {"event_id": ""}, {"contracts": []},
        {"scenarios": ScenarioSet([1.0], {"OTHER": [1]})},
        {"fee_policy": "vwap"}, {"fee_policy": None}, {"fee_policy": 1},
        {"on_exact_fee_exceeded": "ignore"}, {"on_exact_fee_exceeded": None},
        {"max_retighten_rounds": -1}, {"max_retighten_rounds": True},
        {"max_retighten_rounds": 1.0},
        {"fee_allowance": -1.0}, {"fee_allowance": B}, {"fee_allowance": B + 1.0},
    ):
        with pytest.raises(ValueError):
            EventInputs(**{**good, **bad})
    inputs = EventInputs(**good)
    assert inputs.cap == B  # no event cap: the budget is the cap
    assert EventInputs(**{**good, "event_cap": 40.0}).cap == 40.0


# --- the gate and the surviving levels ---------------------------------------


def test_gated_sides_keep_only_levels_with_edge_after_the_exact_fee():
    # asks 0.30 (edge) and 0.39 (0.40 - 0.39 - 0.02 fee < 0): only the first survives
    c = contract_inputs_from_book(
        "C", 0.40, yes_bids=[[0.28, 10]], no_bids=[[0.70, 100], [0.61, 100]], fee_rate=RATE
    )
    inputs = _inputs([c])
    (side,) = gated_sides(inputs)
    assert side.side == "yes" and side.rho == 0.40
    assert [round(p, 6) for p, _phi, _f in side.levels] == [0.30]
    assert side.levels[0][2] == 100
    assert side.levels[0][1] == pytest.approx(RATE * 0.30 * 0.70)
    # the depth haircut floors the fillable lots; a sub-lot level vanishes
    thin = _inputs([c], depth_haircut=0.005)
    assert gated_sides(thin)[0].levels == ()


def test_no_edge_means_no_gated_side_and_no_solver_call():
    inputs = _inputs([_contract(0.29)])  # 0.29 sits inside the 0.28/0.30 quotes
    assert gated_sides(inputs) == []
    alloc = solve_event(inputs, NeverSolve())
    assert alloc.positions == {} and alloc.entered == () and alloc.outlay == 0.0
    assert alloc.expected_log_growth == 0.0 and alloc.status == "empty"
    assert list(alloc.wealth) == [W0, W0]


def test_a_gated_side_without_fillable_depth_is_entered_with_zero_lots():
    inputs = _inputs([_contract(0.40)], depth_haircut=0.0001)  # 2000 * 1e-4 < 1 lot
    alloc = solve_event(inputs, NeverSolve())
    assert alloc.entered == (("C", "yes"),) and alloc.positions == {}
    with pytest.raises(ValueError, match="fillable"):
        event_program(inputs)


def test_one_sided_book_is_refused_at_the_inputs_stage():
    with pytest.raises(IncompleteBookError):
        contract_inputs_from_book("C", 0.40, yes_bids=[[0.28, 10]], no_bids=[], fee_rate=RATE)
    with pytest.raises(IncompleteBookError):
        contract_inputs_from_book("C", 0.40, yes_bids=[], no_bids=[[0.70, 10]], fee_rate=RATE)


# --- the solve ---------------------------------------------------------------


def test_single_contract_yes_matches_the_brute_force_oracle(solver):
    q = 0.40
    inputs = _inputs([_contract(q)])
    alloc = solve_event(inputs, solver)
    n_oracle, g_oracle = oracle_yes(q, 0.30, RATE, W0, B, DEPTH, LAMBDA)
    n = alloc.positions[("C", "yes")]
    assert n_oracle > 0
    assert abs(n - n_oracle) <= max(5, 0.05 * n_oracle)
    assert alloc.expected_log_growth == pytest.approx(g_oracle, abs=1e-4)
    # the reported growth is the EXACT recompute at the solver's own n
    assert alloc.expected_log_growth == pytest.approx(growth_yes(n, q, 0.30, RATE, W0), abs=1e-9)
    # the reported outlay is the venue's own bill, matched to the oracle ...
    assert alloc.outlay == pytest.approx(oracle_cash("kalshi", ((0.30, n),), RATE), abs=1e-9)
    # ... and the program's linear cost is carried beside it, never in its place
    phi = RATE * 0.30 * 0.70
    assert alloc.approx_outlay == pytest.approx(n * (0.30 + phi) + ROUND_UP_CENT, abs=1e-9)
    assert alloc.outlay < alloc.approx_outlay  # one level: no Jensen gap, the cent is real
    ((price, filled),) = alloc.level_fills[("C", "yes")]
    assert price == pytest.approx(0.30) and filled == n  # the mirror of the 0.70 NO bid
    assert alloc.fee_reconciled == {("C", "yes"): True}
    assert alloc.entered == (("C", "yes"),) and alloc.status == "optimal"
    assert alloc.lots == n
    assert list(alloc.wealth) == pytest.approx([W0 + n - alloc.outlay, W0 - alloc.outlay])


def test_no_side_is_taken_when_the_belief_sits_below_the_bid(solver):
    inputs = _inputs([_contract(0.10)])  # NO ask at 0.72, rho 0.90
    alloc = solve_event(inputs, solver)
    assert set(alloc.positions) == {("C", "no")}
    assert alloc.positions[("C", "no")] > 0
    assert alloc.entered == (("C", "no"),)
    # NO pays in the scenario where the contract does NOT settle YES
    n = alloc.positions[("C", "no")]
    assert alloc.wealth[1] == pytest.approx(W0 + n - alloc.outlay)
    assert alloc.wealth[0] == pytest.approx(W0 - alloc.outlay)


def test_more_belief_means_more_lots_and_more_fee_means_fewer(solver):
    base = solve_event(_inputs([_contract(0.40)], n_tangents=128), solver)
    keen = solve_event(_inputs([_contract(0.45)], n_tangents=128), solver)
    pricey = solve_event(_inputs([_contract(0.40, rate=0.20)], n_tangents=128), solver)
    assert keen.positions[("C", "yes")] > base.positions[("C", "yes")]
    assert pricey.positions[("C", "yes")] < base.positions[("C", "yes")]


def test_two_solves_are_identical(solver):
    first = solve_event(_inputs([_contract(0.40)]), solver)
    second = solve_event(_inputs([_contract(0.40)]), solver)
    assert first.positions == second.positions
    assert first.outlay == second.outlay
    assert first.expected_log_growth == second.expected_log_growth
    assert list(first.wealth) == list(second.wealth)


def test_the_budget_binds_and_wealth_stays_positive(solver):
    # a huge edge wants far more than the deployable; the budget must bind
    inputs = _inputs([_contract(0.90)], deployable=50.0)
    alloc = solve_event(inputs, solver)
    assert 0.0 < alloc.outlay <= 50.0 * (1 + 1e-9)
    assert alloc.outlay > 45.0  # the budget is what stopped it
    assert all(w > 0.0 for w in alloc.wealth)
    assert alloc.wealth.min() >= W0 - 50.0 - 1e-9


def test_the_event_cap_binds_below_the_budget(solver):
    alloc = solve_event(_inputs([_contract(0.90)], event_cap=20.0), solver)
    assert 0.0 < alloc.outlay <= 20.0 * (1 + 1e-9)


def test_cheaper_levels_fill_first(solver):
    c = contract_inputs_from_book(
        "C", 0.40, yes_bids=[[0.28, 10]], no_bids=[[0.70, 50], [0.68, 2000]], fee_rate=RATE
    )
    alloc = solve_event(_inputs([c]), solver)
    fills = {round(price, 6): lots for price, lots in alloc.level_fills[("C", "yes")]}
    assert alloc.positions[("C", "yes")] > 50
    assert fills[0.30] == 50  # the cheap level is exhausted before the dearer one
    assert sum(fills.values()) == alloc.positions[("C", "yes")]


def test_the_kelly_fraction_scales_the_stake_against_the_oracle(solver):
    lots = {}
    for lam in (0.25, 0.5, 1.0):
        alloc = solve_event(_inputs([_contract(0.40)], kelly_fraction=lam), solver)
        n = alloc.positions[("C", "yes")]
        n_oracle, _g = oracle_yes(0.40, 0.30, RATE, W0, B, DEPTH, lam)
        assert abs(n - n_oracle) <= max(5, 0.05 * n_oracle), (lam, n, n_oracle)
        lots[lam] = n
    assert lots[0.25] < lots[0.5] < lots[1.0], lots


def test_min_lot_is_all_or_nothing(solver):
    alloc = solve_event(_inputs([_contract(0.40)], min_lot=250), solver)
    n = alloc.positions.get(("C", "yes"), 0)
    assert n == 0 or n >= 250
    # the floor BINDS on this bet (the free optimum sits below it), so the
    # solver must land on the oracle's floored optimum, not on zero
    n_free, _g = oracle_yes(0.40, 0.30, RATE, W0, B, DEPTH, LAMBDA)
    n_floor, _g = oracle_yes(0.40, 0.30, RATE, W0, B, DEPTH, LAMBDA, min_lot=250)
    assert n_free < 250 <= n_floor, (n_free, n_floor)
    assert abs(n - n_floor) <= max(5, 0.05 * n_floor), (n, n_floor)


def test_depth_haircut_caps_the_fill(solver):
    alloc = solve_event(_inputs([_contract(0.40)], depth_haircut=0.05), solver)  # 100 lots
    assert alloc.positions[("C", "yes")] <= 100


def test_a_partition_sizes_the_same_bet_across_its_rungs_jointly(solver):
    # two rungs of one partition: YES on A and NO on B pay together
    a = contract_inputs_from_book("A", 0.40, yes_bids=[[0.28, 2000]], no_bids=[[0.70, 2000]],
                                  fee_rate=RATE)
    b = contract_inputs_from_book("B", 0.60, yes_bids=[[0.68, 2000]], no_bids=[[0.30, 2000]],
                                  fee_rate=RATE)
    inputs = _inputs([a, b])
    alloc = solve_event(inputs, solver)
    assert alloc.entered == (("A", "yes"), ("B", "no"))
    total = sum(alloc.positions.values())
    n_single, _g = oracle_yes(0.40, 0.30, RATE, W0, B, DEPTH, LAMBDA)
    # the joint program sees one bet priced twice: the total is Kelly for it
    assert abs(total - n_single) <= max(5, 0.05 * n_single)
    assert all(w > 0 for w in alloc.wealth)
    assert len({c for c, _s in alloc.positions}) == len(alloc.positions)


def test_read_allocation_refuses_a_non_optimal_status():
    from types import SimpleNamespace

    results = SimpleNamespace(solver=SimpleNamespace(termination_condition="maxTimeLimit"))
    with pytest.raises(RuntimeError, match="optimal"):
        read_allocation(None, results)


def test_empty_allocation_shape():
    inputs = _inputs([_contract(0.40)])
    alloc = empty_allocation(inputs, entered=[("C", "yes")])
    assert alloc.event_id == "E" and alloc.entered == (("C", "yes"),)
    assert alloc.lots == 0 and alloc.outlay == 0.0 and alloc.status == "empty"
    assert list(alloc.wealth) == [W0, W0] and alloc.fee_reconciled == {}


def test_the_module_imports_without_numpy_or_pyomo():
    # The node modules import this at plan time; numpy/pyomo live inside functions.
    import importlib
    import subprocess
    import sys

    child_root = __import__("os").path.dirname(__import__("os").path.dirname(
        __import__("os").path.abspath(__file__)))
    script = (
        "import sys\n"
        f"sys.path.insert(0, {child_root!r})\n"
        "for n in ('numpy', 'pyomo', 'highspy'):\n"
        "    sys.modules[n] = None\n"
        "import pmquant.mio\n"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    importlib.import_module("pmquant.mio")


# --- PM-01: the reported money must be the venue's EXACT bill ---------------
#
# The independent fee/cash oracle. It restates the two venues' published
# arithmetic in EXACT DECIMAL and imports nothing from ``pmquant.fees``:
# the production path guards binary dust with a nine-decimal snap, this
# one never meets dust at all, and the two must agree on every dollar
# this child reports (CLAUDE.md's blessed "deliberate independent
# restatement"). Prices and rates come in as decimal STRINGS so the
# oracle prices the number the test MEANS, never a float's tail.

AUDIT_FILLS = (("0.30", 500), ("0.40", 500))
AUDIT_PREMIUM = 350.0
AUDIT_LINEAR_OUTLAY = 365.76  # 350 + 15.75 linear + the one-cent allowance
AUDIT_EXACT_OUTLAY = 365.93  # 350 + ceil-to-cent(1000 * 0.07 * 0.35 * 0.65)


def oracle_fee(venue, contracts, price, rate):
    """One venue fee on one billable quantity, in exact decimal."""
    from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

    c, p, r = Decimal(int(contracts)), Decimal(str(price)), Decimal(str(rate))
    raw = r * c * p * (Decimal(1) - p)
    if venue == "kalshi":
        return float(raw.quantize(Decimal("0.01"), rounding=ROUND_CEILING))
    if venue == "polymarket":
        return float(raw.quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP))
    raise AssertionError(f"the oracle prices no venue {venue!r}")


def oracle_vwap(fills):
    """The volume-weighted average price of ``fills``, in exact decimal."""
    from decimal import Decimal

    n = sum(int(lots) for _p, lots in fills)
    return sum(Decimal(str(p)) * int(lots) for p, lots in fills) / Decimal(n)


def oracle_order_fee(venue, fills, rate):
    """ONE fee on the total at VWAP — the order-level rule ``walk_book`` bills."""
    return oracle_fee(venue, sum(int(n) for _p, n in fills), oracle_vwap(fills), rate)


def oracle_per_fill_fee(venue, fills, rate):
    """One fee per FILL, summed — the per-fill alternative."""
    return float(sum(oracle_fee(venue, n, p, rate) for p, n in fills if int(n) > 0))


def oracle_premium(fills):
    """The premium ``fills`` consume, in exact decimal."""
    from decimal import Decimal

    return float(sum(Decimal(str(p)) * int(n) for p, n in fills))


def oracle_cash(venue, fills, rate):
    """Premium plus the order-level fee: the cash one side's fills consume."""
    return oracle_premium(fills) + oracle_order_fee(venue, fills, rate)


def _audit_contract(q=0.70, rate=RATE, cid="C", depth=500):
    """The audit's book: YES asks at 0.30 and 0.40, ``depth`` lots each."""
    return contract_inputs_from_book(
        cid, q, yes_bids=[[0.28, 10]], no_bids=[[0.70, depth], [0.60, depth]], fee_rate=rate
    )


def _audit_inputs(series=SERIES, **over):
    over.setdefault("kelly_fraction", 1.0)
    return _inputs([_audit_contract()], series=series, **over)


def _load(model, lots):
    """Load an integer solution into a built program; fake an optimal result.

    ``lots`` maps ``(side index, level index) -> lots``. This hands the
    reader the exact fill set the LINEAR program calls feasible, which is
    the whole point: the defect is what the reader then bills for it.
    """
    from types import SimpleNamespace

    sides = model._mio["sides"]
    for k, side in enumerate(sides):
        total = 0
        for i in range(len(side.levels)):
            n = int(lots.get((k, i), 0))
            model.q[k, i].value = n
            total += n
        model.n[k].value = total
        model.y[k].value = 1 if total else 0
    model.z.value = 1 if any(int(v) for v in lots.values()) else 0
    for o in range(model._mio["inputs"].scenarios.n_omega):
        model.W[o].value = float(model._mio["w_lo"])
        model.t[o].value = 0.0
    return SimpleNamespace(solver=SimpleNamespace(termination_condition="optimal"))


def test_the_oracle_reproduces_the_audits_own_arithmetic():
    # A guard on the guard: the oracle must land on the audit's published
    # numbers before it is allowed to judge the implementation.
    assert oracle_premium(AUDIT_FILLS) == pytest.approx(350.0, abs=1e-12)
    assert oracle_per_fill_fee("kalshi", AUDIT_FILLS, "0.07") == pytest.approx(15.75, abs=1e-12)
    assert oracle_order_fee("kalshi", AUDIT_FILLS, "0.07") == pytest.approx(15.93, abs=1e-12)
    assert oracle_cash("kalshi", AUDIT_FILLS, "0.07") == pytest.approx(
        AUDIT_EXACT_OUTLAY, abs=1e-12
    )


def test_the_audited_worked_example_is_billed_at_the_encoded_exact_fee():
    inputs = _audit_inputs()
    model = event_program(inputs)
    results = _load(model, {(0, 0): 500, (0, 1): 500})
    alloc = read_allocation(model, results)
    assert alloc.positions == {("C", "yes"): 1000}
    # the program's own linear cost — the approximation, still what it solved on
    assert model.outlay() == pytest.approx(AUDIT_LINEAR_OUTLAY, abs=1e-9)
    # ... but the REPORTED outlay is the exact bill, matched to the oracle
    assert alloc.outlay == pytest.approx(oracle_cash("kalshi", AUDIT_FILLS, "0.07"), abs=1e-9)
    assert alloc.outlay == pytest.approx(AUDIT_EXACT_OUTLAY, abs=1e-9)


def test_a_budget_the_approximation_clears_but_the_exact_bill_does_not_is_refused():
    # The audit's consequence: 365.76 <= 365.80 < 365.93. The linear program
    # calls this fill feasible; the encoded exact bill does not fit the budget.
    inputs = _audit_inputs(deployable=365.80)
    model = event_program(inputs)
    results = _load(model, {(0, 0): 500, (0, 1): 500})
    assert model.outlay() == pytest.approx(AUDIT_LINEAR_OUTLAY, abs=1e-9)
    assert model.outlay() <= 365.80
    with pytest.raises(ValueError, match="exact"):
        read_allocation(model, results)


def test_scenario_wealth_and_growth_follow_the_exact_outlay():
    inputs = _audit_inputs()
    model = event_program(inputs)
    alloc = read_allocation(model, _load(model, {(0, 0): 500, (0, 1): 500}))
    exact = oracle_cash("kalshi", AUDIT_FILLS, "0.07")
    assert list(alloc.wealth) == pytest.approx([W0 + 1000 - exact, W0 - exact], abs=1e-9)
    weights = list(inputs.scenarios.weights)
    expected = sum(
        w * math.log(x / W0) for w, x in zip(weights, [W0 + 1000 - exact, W0 - exact])
    )
    assert alloc.expected_log_growth == pytest.approx(expected, abs=1e-12)


def test_the_polymarket_rounding_branch_is_not_an_unconditional_cent():
    inputs = _audit_inputs(series=POLY_SERIES)
    model = event_program(inputs)
    alloc = read_allocation(model, _load(model, {(0, 0): 500, (0, 1): 500}))
    poly = oracle_order_fee("polymarket", AUDIT_FILLS, "0.07")
    assert poly == pytest.approx(15.925, abs=1e-12)  # on the 1e-5 grid, no cent floor
    assert alloc.fees[("C", "yes")] == pytest.approx(poly, abs=1e-9)
    assert alloc.outlay == pytest.approx(350.0 + 15.925, abs=1e-9)
    # three different numbers over ONE fill set: the grid is not Kalshi's cent,
    # and neither is the program's linear estimate
    assert abs(alloc.outlay - AUDIT_EXACT_OUTLAY) > 1e-6
    assert abs(alloc.outlay - AUDIT_LINEAR_OUTLAY) > 1e-6


def test_the_ceil_to_cent_boundary_bills_a_cent_only_when_one_is_needed():
    # one level at exactly 0.50, so the VWAP is on the cent grid too
    c = contract_inputs_from_book(
        "C", 0.70, yes_bids=[[0.45, 10]], no_bids=[[0.50, 300]], fee_rate=RATE
    )
    model = event_program(_inputs([c], kelly_fraction=1.0))
    # 0.07 * 100 * 0.50 * 0.50 = 1.75 exactly on the cent: the ceiling adds nothing
    on_grid = read_allocation(model, _load(model, {(0, 0): 100}))
    assert on_grid.fees[("C", "yes")] == oracle_fee("kalshi", 100, "0.50", "0.07")
    assert on_grid.fees[("C", "yes")] == pytest.approx(1.75, abs=1e-12)
    assert on_grid.outlay == pytest.approx(50.0 + 1.75, abs=1e-9)
    # 0.07 * 101 * 0.25 = 1.7675: one lot more crosses the boundary
    over = read_allocation(model, _load(model, {(0, 0): 101}))
    assert over.fees[("C", "yes")] == oracle_fee("kalshi", 101, "0.50", "0.07")
    assert over.fees[("C", "yes")] == pytest.approx(1.77, abs=1e-12)
    assert over.outlay == pytest.approx(50.5 + 1.77, abs=1e-9)


def test_a_partial_fill_is_billed_on_what_filled_not_on_what_was_offered():
    model = event_program(_audit_inputs())
    part = read_allocation(model, _load(model, {(0, 0): 500, (0, 1): 137}))
    partial_fills = (("0.30", 500), ("0.40", 137))
    assert part.positions == {("C", "yes"): 637}
    assert part.level_fills[("C", "yes")][1][1] == 137  # 137 of the 500 offered
    assert part.outlay == pytest.approx(oracle_cash("kalshi", partial_fills, "0.07"), abs=1e-9)
    full = read_allocation(model, _load(model, {(0, 0): 500, (0, 1): 500}))
    assert part.fees[("C", "yes")] < full.fees[("C", "yes")]
    assert part.outlay < full.outlay
    # a fill of nothing at all bills nothing at all
    none = read_allocation(model, _load(model, {}))
    assert none.positions == {} and none.fees == {} and none.outlay == 0.0


def test_a_fee_regime_change_mid_window_bills_each_market_at_its_own_rate():
    switch = "2026-03-30T12:00:00Z"
    book = FeeBook.from_document({
        SERIES: {"cases": [
            {"when": [{"field": "close_ts", "op": "<", "value": switch}], "value": 0.0},
            {"when": [{"field": "close_ts", "op": ">=", "value": switch}], "value": 0.07},
        ]},
    })
    before = book.rate_for(SERIES, close_ms=1_774_000_000_000)  # 2026-03-20
    after = book.rate_for(SERIES, close_ms=1_774_872_000_000)  # the switch instant
    assert (before, after) == (0.0, 0.07)
    billed = {}
    for rate in (before, after):
        inputs = _inputs([_audit_contract(rate=rate)], kelly_fraction=1.0)
        model = event_program(inputs)
        alloc = read_allocation(model, _load(model, {(0, 0): 500, (0, 1): 500}))
        assert alloc.outlay == pytest.approx(oracle_cash("kalshi", AUDIT_FILLS, str(rate)), abs=1e-9)
        billed[rate] = alloc.fees[("C", "yes")]
    # a SOURCED zero rate is a real zero fee, and the regime change is the
    # whole difference between the two bills
    assert billed[0.0] == 0.0
    assert billed[0.07] == pytest.approx(15.93, abs=1e-12)


def test_fee_reconciliation_bills_the_same_exact_fee_the_outlay_does():
    model = event_program(_audit_inputs())
    alloc = read_allocation(model, _load(model, {(0, 0): 500, (0, 1): 500}))
    key = ("C", "yes")
    edge = 1000 * 0.70 - oracle_premium(AUDIT_FILLS) - oracle_order_fee(
        "kalshi", AUDIT_FILLS, "0.07"
    )
    assert alloc.fee_reconciled[key] is (edge > 0.0)
    # the exported flag and the reported money now come from ONE number
    assert alloc.outlay == pytest.approx(alloc.premium + sum(alloc.fees.values()), abs=1e-12)
    assert alloc.fee_policy == DEFAULT_FILL_FEE_POLICY


def test_an_exact_bill_that_will_not_fit_is_refused_end_to_end(solver):
    # 365.76 (linear) <= 365.77 < 365.93 (exact): the program calls the full
    # depth feasible and the venue's own bill does not fit.
    alloc = solve_event(_audit_inputs(deployable=365.77), solver)
    assert alloc.status == REFUSED_STATUS
    assert alloc.positions == {} and alloc.level_fills == {}
    assert alloc.outlay == 0.0 and alloc.lots == 0 and alloc.expected_log_growth == 0.0
    assert list(alloc.wealth) == [W0, W0]
    assert alloc.entered == (("C", "yes"),)  # a refusal still says what it considered
    assert "exact" in alloc.refusal and "365.9" in alloc.refusal


def test_retighten_re_solves_under_reserved_headroom_until_the_exact_bill_fits(solver):
    inputs = _audit_inputs(deployable=365.77, on_exact_fee_exceeded="retighten")
    alloc = solve_event(inputs, solver)
    assert alloc.status == "optimal" and alloc.lots > 0
    assert alloc.outlay <= 365.77 + 1e-9
    # every level price here is on the cent grid, so the oracle prices the
    # decimals the book actually carries
    fills = tuple((f"{price:.2f}", lots) for price, lots in alloc.level_fills[("C", "yes")])
    assert alloc.outlay == pytest.approx(oracle_cash("kalshi", fills, "0.07"), abs=1e-9)
    # it bought strictly less than the approximation would have allowed
    assert alloc.lots < 1000


def test_the_resolution_refuses_an_unknown_mode_and_a_bad_round_count():
    with pytest.raises(ValueError, match="mode"):
        ExactFeeResolution("ignore")
    with pytest.raises(ValueError, match="max_rounds"):
        ExactFeeResolution("retighten", -1)
    with pytest.raises(ValueError, match="max_rounds"):
        ExactFeeResolution("retighten", True)
    assert ExactFeeResolution().mode == DEFAULT_ON_EXACT_FEE_EXCEEDED
    assert ExactFeeResolution().max_rounds == DEFAULT_RETIGHTEN_ROUNDS


def _always_over(seen):
    def solve_once(current):
        seen.append(current.fee_allowance)
        raise ExactFeeBudgetExceeded("E", "deployable", 10.0, 10.5, 10.0, "order_vwap")

    return solve_once


def test_refusing_never_re_solves_and_retightening_always_terminates():
    inputs = _audit_inputs()
    seen = []
    refused = ExactFeeResolution("refuse", 3).resolve(
        inputs, _always_over(seen), entered=[("C", "yes")]
    )
    assert refused.status == REFUSED_STATUS and seen == [0.0]  # one solve, no retry
    seen = []
    gave_up = ExactFeeResolution("retighten", 3).resolve(
        inputs, _always_over(seen), entered=[("C", "yes")]
    )
    assert gave_up.status == REFUSED_STATUS
    assert len(seen) == 4  # the first solve plus exactly three tightened re-solves
    assert seen[0] == 0.0
    # strictly increasing reserved headroom is what makes the loop terminate
    assert all(b > a for a, b in zip(seen, seen[1:]))
    # and the refusal never quietly relaxes the event's real budget
    assert gave_up.outlay == 0.0 and gave_up.positions == {}


def test_retightening_stops_rather_than_reserving_the_whole_budget():
    # a shortfall larger than the budget can never be reserved away
    inputs = _audit_inputs(deployable=10.0)
    seen = []

    def solve_once(current):
        seen.append(current.fee_allowance)
        raise ExactFeeBudgetExceeded("E", "deployable", 10.0, 999.0, 10.0, "order_vwap")

    alloc = ExactFeeResolution("retighten", 9).resolve(inputs, solve_once, entered=())
    assert alloc.status == REFUSED_STATUS and seen == [0.0]


def test_a_refused_allocation_is_an_empty_one_that_says_why():
    inputs = _audit_inputs()
    alloc = refused_allocation(inputs, entered=[("C", "yes")], reason="because")
    assert alloc.status == REFUSED_STATUS and alloc.refusal == "because"
    assert alloc.lots == 0 and alloc.outlay == 0.0 and alloc.fees == {}
    assert empty_allocation(inputs).refusal is None


# --- PM-01 round 2: the ceilings, the tolerance, and the policy knob --------

#: A two-level book whose fills are small enough that Kalshi's per-ORDER cent
#: floor reverses the Jensen ordering: per-fill bills 0.01 twice, the single
#: order-level charge rounds to one cent in total.
TINY_FILLS = (("0.02", 1), ("0.03", 1))


def _tiny_contract(rate=RATE):
    return contract_inputs_from_book(
        "C", 0.30, yes_bids=[[0.01, 10]], no_bids=[[0.98, 1], [0.97, 1]], fee_rate=rate
    )


def _billed(inputs, lots=None):
    """Build, hand-load the full depth (or ``lots``) and read one allocation."""
    model = event_program(inputs)
    return read_allocation(model, _load(model, lots or {(0, 0): 500, (0, 1): 500}))


def test_a_cap_tighter_than_the_budget_refuses_on_the_exact_bill_alone():
    # The DEFAULT ceiling is a per-event cap, not the deployable: the node
    # divides the budget across gated events. So the cap must be enforced on
    # the exact bill in its own right, with the budget never in question.
    inputs = _audit_inputs(deployable=B, event_cap=365.80)
    model = event_program(inputs)
    results = _load(model, {(0, 0): 500, (0, 1): 500})
    assert model.outlay() == pytest.approx(AUDIT_LINEAR_OUTLAY, abs=1e-9)
    assert model.outlay() <= 365.80  # the program calls this fill feasible under the cap
    assert AUDIT_EXACT_OUTLAY < B  # ... and the deployable is nowhere near broken
    with pytest.raises(ExactFeeBudgetExceeded) as exc:
        read_allocation(model, results)
    assert exc.value.limit_name == "event cap" and exc.value.limit == 365.80
    assert exc.value.shortfall == pytest.approx(AUDIT_EXACT_OUTLAY - 365.80, abs=1e-9)


def test_the_exact_bill_names_the_tightest_ceiling_it_broke():
    # Both ceilings broken: the binding one is the SMALLER, and it carries the
    # true shortfall the retighten loop must reserve against.
    inputs = _audit_inputs(deployable=100.0, event_cap=50.0)
    with pytest.raises(ExactFeeBudgetExceeded) as exc:
        _billed(inputs)
    assert exc.value.limit_name == "event cap" and exc.value.limit == 50.0
    assert exc.value.shortfall == pytest.approx(AUDIT_EXACT_OUTLAY - 50.0, abs=1e-9)
    # naming the deployable instead would have understated it by 50 dollars
    assert exc.value.shortfall - (AUDIT_EXACT_OUTLAY - 100.0) == pytest.approx(50.0, abs=1e-9)
    # and with only the deployable broken, that is what it names
    with pytest.raises(ExactFeeBudgetExceeded) as loose:
        _billed(_audit_inputs(deployable=100.0))
    assert loose.value.limit_name == "deployable" and loose.value.limit == 100.0


def test_a_tight_event_cap_is_enforced_end_to_end_and_retightened_against(solver):
    # cap 365.77: linear 365.76 fits it, exact 365.93 does not, budget is 500
    refused = solve_event(_audit_inputs(deployable=B, event_cap=365.77), solver)
    assert refused.status == REFUSED_STATUS
    assert refused.positions == {} and refused.outlay == 0.0
    assert "event cap" in refused.refusal
    keen = solve_event(
        _audit_inputs(deployable=B, event_cap=365.77, on_exact_fee_exceeded="retighten"), solver
    )
    assert keen.status == "optimal" and keen.lots > 0
    assert keen.outlay <= 365.77 + 1e-9  # the CAP bound it, with the budget slack
    assert keen.outlay < AUDIT_EXACT_OUTLAY and keen.lots < 1000
    fills = tuple((f"{price:.2f}", lots) for price, lots in keen.level_fills[("C", "yes")])
    assert keen.outlay == pytest.approx(oracle_cash("kalshi", fills, "0.07"), abs=1e-9)


def test_the_outlay_tolerance_absorbs_float_dust_but_not_a_real_overrun():
    # _OUTLAY_TOL claims to sit four orders below a cent: big enough that
    # representation dust never refuses an honest solve, small enough that a
    # real overrun always does. These two cases bracket it; it cannot be
    # loosened to a cent or tightened to a float epsilon and stay green.
    exact = _billed(_audit_inputs()).outlay
    assert exact == pytest.approx(AUDIT_EXACT_OUTLAY, abs=1e-9)
    hair = _billed(_audit_inputs(deployable=exact - 1e-9))
    assert hair.outlay == pytest.approx(exact, abs=1e-12)
    with pytest.raises(ExactFeeBudgetExceeded) as exc:
        _billed(_audit_inputs(deployable=exact - 1e-3))
    assert exc.value.shortfall == pytest.approx(1e-3, abs=1e-9)


@pytest.mark.parametrize(
    ("policy", "wide_fee", "tiny_fee"),
    [("order_vwap", 15.93, 0.01), ("per_fill", 15.75, 0.02), ("conservative", 15.93, 0.02)],
)
def test_every_declared_fee_policy_bills_its_own_money(policy, wide_fee, tiny_fee):
    # The knob is not a string the validator approves: it decides the dollars.
    wide = _billed(_audit_inputs(fee_policy=policy))
    assert wide.fee_policy == policy
    assert wide.fees[("C", "yes")] == pytest.approx(wide_fee, abs=1e-9)
    assert wide.outlay == pytest.approx(AUDIT_PREMIUM + wide_fee, abs=1e-9)
    tiny = _billed(
        _inputs([_tiny_contract()], fee_policy=policy, kelly_fraction=1.0),
        lots={(0, 0): 1, (0, 1): 1},
    )
    assert tiny.fees[("C", "yes")] == pytest.approx(tiny_fee, abs=1e-9)
    assert tiny.outlay == pytest.approx(oracle_premium(TINY_FILLS) + tiny_fee, abs=1e-9)


def test_the_three_policies_disagree_in_both_directions_through_the_sizer():
    # Jensen makes order-level the dearer one on a wide fill; the per-ORDER
    # cent floor reverses it on a tiny one. Conservative is the max of both,
    # so no single policy is a universal upper bound and the knob matters.
    wide = {p: _billed(_audit_inputs(fee_policy=p)).outlay
            for p in ("order_vwap", "per_fill", "conservative")}
    tiny = {p: _billed(_inputs([_tiny_contract()], fee_policy=p, kelly_fraction=1.0),
                       lots={(0, 0): 1, (0, 1): 1}).outlay
            for p in ("order_vwap", "per_fill", "conservative")}
    assert wide["order_vwap"] > wide["per_fill"]
    assert tiny["per_fill"] > tiny["order_vwap"]
    for billed in (wide, tiny):
        assert billed["conservative"] == pytest.approx(
            max(billed["order_vwap"], billed["per_fill"]), abs=1e-12
        )


def test_the_configured_retighten_round_budget_is_obeyed(solver):
    # rounds=0 means "solve once": retighten degenerates to refusing, and the
    # SAME event with the default round budget fits.
    none = solve_event(
        _audit_inputs(deployable=365.77, on_exact_fee_exceeded="retighten",
                      max_retighten_rounds=0),
        solver,
    )
    assert none.status == REFUSED_STATUS and none.lots == 0
    some = solve_event(
        _audit_inputs(deployable=365.77, on_exact_fee_exceeded="retighten"), solver
    )
    assert some.status == "optimal" and some.lots > 0


def test_a_shortfall_below_the_minimum_step_still_makes_progress():
    # A two-microdollar overrun would reserve two microdollars and re-solve the
    # identical integer program forever; the floor is what makes each round
    # move. This is the constant's whole job, so it is asserted, not assumed.
    inputs = _audit_inputs()
    seen = []

    def barely_over(current):
        seen.append(current.fee_allowance)
        raise ExactFeeBudgetExceeded("E", "deployable", 10.0, 10.0 + 2e-6, 10.0, "order_vwap")

    alloc = ExactFeeResolution("retighten", 3).resolve(inputs, barely_over, entered=())
    assert alloc.status == REFUSED_STATUS and len(seen) == 4
    steps = [b - a for a, b in zip(seen, seen[1:])]
    assert steps == pytest.approx([RETIGHTEN_MIN_STEP] * 3, abs=1e-12)


def test_the_partition_slack_tolerance_is_bracketed_within_one_order():
    # LAW_SLACK_TOL decides when leftover belief becomes a NONE cell. These
    # two probes lock it into [1e-10, 1e-8): ONE order either side of its
    # declared 1e-9. A looser pair (1e-7 against 1e-10) would have let it
    # drift a hundredfold unnoticed.
    def law(slack):
        rungs = [
            _contract(0.5, cid="A"),
            _contract(0.5 - slack, cid="B"),
        ]
        return mutually_exclusive_scenarios(rungs, exhaustive=False)

    assert law(1e-8).n_omega == 3  # a real hole in the partition
    assert law(1e-10).n_omega == 2  # representation dust, renormalized away


# --- the fee-policy asymmetry, closed -------------------------------------

#: Lens A's counterexample: the fills on which the two aggregations differ by
#: more than a dollar, in the DANGEROUS direction (a ``per_fill`` sizer would
#: reserve LESS than the encoded invoice bills).
DIVERGENCE_LEVELS = ((0.20, 300), (0.45, 300))
DIVERGENCE_FILLS = (("0.20", 300), ("0.45", 300))
DIVERGENCE_RATE = 0.12


def _divergence_contract(rate=DIVERGENCE_RATE):
    return contract_inputs_from_book(
        "C", 0.70, yes_bids=[[0.15, 10]], no_bids=[[0.80, 300], [0.55, 300]], fee_rate=rate
    )


def test_the_sizer_and_the_fill_path_agree_when_handed_the_same_policy():
    """The invariant the ``fee_policy`` knob must never break.

    ``walk_book`` takes a policy, so the sizer's reserve and the fill
    path's bill are the same number whenever the caller hands the walk the
    document's own policy — and the default still simulates the invoice.
    """
    from pmquant.books import BookSnapshot, Order, walk_book
    from pmquant.fees import fill_fee_policy

    book = BookSnapshot(f"{SERIES}-C", "ask", DIVERGENCE_LEVELS)
    order = Order(600, 1.0, DIVERGENCE_RATE)
    billed = {}
    for policy in ("order_vwap", "per_fill", "conservative"):
        inputs = _inputs([_divergence_contract()], fee_policy=policy, kelly_fraction=1.0)
        model = event_program(inputs)
        alloc = read_allocation(model, _load(model, {(0, 0): 300, (0, 1): 300}))
        walked = walk_book(book, order, policy=fill_fee_policy(policy))
        assert alloc.fees[("C", "yes")] == pytest.approx(walked.fee, abs=1e-12), policy
        billed[policy] = walked.fee
    assert billed["order_vwap"] == pytest.approx(
        oracle_order_fee("kalshi", DIVERGENCE_FILLS, "0.12"), abs=1e-12
    )
    assert billed["order_vwap"] == pytest.approx(15.80, abs=1e-12)
    assert billed["per_fill"] == pytest.approx(
        oracle_per_fill_fee("kalshi", DIVERGENCE_FILLS, "0.12"), abs=1e-12
    )
    assert billed["per_fill"] == pytest.approx(14.67, abs=1e-12)
    # the divergence is real money, not float dust, and points DOWNWARD
    assert billed["order_vwap"] - billed["per_fill"] == pytest.approx(1.13, abs=1e-12)
    # ... and a walk with no policy still simulates the venue's invoice
    assert walk_book(book, order).fee == pytest.approx(billed["order_vwap"], abs=1e-12)


def test_two_ceilings_at_the_same_dollar_name_the_first_declared_one():
    # ``event_cap=None`` makes cap == deployable, which is the DEFAULT shape.
    # Either name carries the same shortfall, so the choice is arbitrary — but
    # it is STATED, so it cannot quietly flip under an operator reading logs.
    inputs = _audit_inputs(deployable=100.0)
    assert inputs.cap == inputs.deployable == 100.0
    with pytest.raises(ExactFeeBudgetExceeded) as exc:
        _billed(inputs)
    assert exc.value.limit_name == "deployable"
    assert exc.value.limit == 100.0
    assert exc.value.shortfall == pytest.approx(AUDIT_EXACT_OUTLAY - 100.0, abs=1e-9)
