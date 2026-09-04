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
from pmquant.mio import (
    DEFAULT_DEPTH_HAIRCUT,
    DEFAULT_MIN_LOT,
    DEFAULT_N_TANGENTS,
    DEFAULT_TAU,
    ROUND_UP_CENT,
    DegenerateScenarioLawError,
    EventInputs,
    ScenarioSet,
    empty_allocation,
    event_program,
    gated_sides,
    mutually_exclusive_scenarios,
    read_allocation,
    solve_event,
    threshold_scenarios,
    utility_at,
    wealth_bounds,
)

SERIES = "KXTEST"
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

    ``phi = rate*ask*(1-ask)``; ``outlay(n) = n*ask + n*phi + 0.01``;
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
    """Exact expected log growth of ``n`` YES lots at one level."""
    if n == 0:
        return 0.0
    phi = rate * ask * (1.0 - ask)
    outlay = n * ask + n * phi + 0.01
    return q * math.log((w0 + n - outlay) / w0) + (1.0 - q) * math.log((w0 - outlay) / w0)


# --- constants ------------------------------------------------------------


def test_the_defaults_are_the_documented_ones():
    assert DEFAULT_N_TANGENTS == 128
    assert DEFAULT_TAU == 0.0
    assert DEFAULT_DEPTH_HAIRCUT == 1.0
    assert DEFAULT_MIN_LOT == 1
    assert ROUND_UP_CENT == 0.01


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
    phi = RATE * 0.30 * 0.70
    assert alloc.outlay == pytest.approx(n * (0.30 + phi) + ROUND_UP_CENT, abs=1e-9)
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
