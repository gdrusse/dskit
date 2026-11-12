"""The one-stage Kelly local search (ADR-0307): the moves, the exact score, the budgets and the determinism."""

import numpy as np
import pytest

from dskit.pipeline.libs.pyomo import TransportActionScore
from index_options.kelly_local import KellyLocalSearch

PARAMS = {"moves": 200, "adds_evaluated": 64, "swaps_evaluated": 64, "restarts": 0, "seed": 7}
W0 = 10000.


def _program(n_groups=3, n_pairs=2, atoms=7, seed=0, capacity=4, reserve_cap=None, floor=100., joint=True,
             lin_upper=None, joint_upper=None, gamma=4.):
    """A synthetic one-stage Kelly program: each group is ``n_pairs`` short/long contract pairs on an atom grid.

    The short side of a pair earns a positive edge on most pairs; the long side is its mirror at a small cost, so
    the two sides are exclusive. Scenarios are a quiet cell and two shock moves with per-group profit terms.
    """
    rng = np.random.default_rng(seed)
    groups, ids = [], []
    for g in range(n_groups):
        grid = np.linspace(90., 110., atoms)
        masses = rng.random(atoms)+.2
        masses /= masses.sum()
        losses, credit, reserve, capacities, exclusive = [], [], [], [], []
        columns = []
        for pair in range(n_pairs):
            strike = 96.+3.*pair
            payoff = np.clip(strike-grid, 0., 6.)*10.
            edge = rng.uniform(-.1, .4)
            short_credit = float(masses @ payoff)*(1.+edge)+1.
            columns.append((payoff, short_credit))
            columns.append((-payoff, -short_credit*1.05))
            exclusive.append([2*pair, 2*pair+1])
        for payoff, c in columns:
            losses.append(payoff)
            credit.append(c)
            reserve.append(max(0., float(np.max(payoff))-c))
            capacities.append(capacity)
        groups.append({"id": f"G{g}", "grid": grid.tolist(), "masses": masses.tolist(), "radius": 0.,
                       "scale": 100., "credit": credit, "losses": np.array(losses).T.tolist(),
                       "capacity": capacities, "exclusive": exclusive, "reserve_rows": [reserve],
                       "linear_rows": [] if lin_upper is None else
                       [{"coefficients": [1.]*len(credit), "upper": lin_upper}]})
        ids.append(f"G{g}")
    scenarios = [{"id": "market:0", "weight": .6, "terms": {}}]
    for k, move in enumerate((-.1, .1)):
        scenarios.append({"id": f"market:{move}", "weight": .2,
                          "terms": {i: (rng.normal(0., 15., 2*n_pairs)*(1. if move > 0 else -1.)).tolist()
                                    for i in ids}})
    program = {"groups": groups,
               "caps": [{"groups": ids, "upper": W0*.5 if reserve_cap is None else reserve_cap}],
               "utility": {"w0": W0, "gamma": gamma, "n_tangents": 16, "core": 2., "wealth_lo": floor,
                           "aggregate": {i: ("A" if n % 2 == 0 else "B") for n, i in enumerate(ids)},
                           "joint": scenarios if joint else [{"id": "market:0", "weight": 1., "terms": {}}]}}
    if joint_upper is not None:
        program["joint_rows"] = [{"id": "shock", "terms": {i: [10.]*(2*n_pairs) for i in ids}, "upper": joint_upper}]
    return program


def _scorer(program):
    return TransportActionScore(program, {"parity_tolerance": 1e-3})


def _exact(scorer, nested):
    return scorer.evaluate(nested)["utility"]["exact"]


def test_the_declaration_is_validated():
    """Exactly the five keys, each a plain int in range (a bool is not an int here)."""
    assert KellyLocalSearch.validate_params(PARAMS) == []
    for bad in ({**PARAMS, "moves": 0}, {**PARAMS, "adds_evaluated": 0}, {**PARAMS, "swaps_evaluated": -1},
                {**PARAMS, "restarts": -1}, {**PARAMS, "seed": -1}, {**PARAMS, "moves": 1.5},
                {**PARAMS, "seed": True}, {**PARAMS, "extra": 1},
                {k: v for k, v in PARAMS.items() if k != "seed"}, [], None):
        assert KellyLocalSearch.validate_params(bad), bad
    with pytest.raises(ValueError, match="int >= 1"):
        KellyLocalSearch(_scorer(_program()), {**PARAMS, "moves": 0})
    program = _program()
    del program["utility"]
    with pytest.raises(ValueError, match="utility"):
        KellyLocalSearch(_scorer(program), PARAMS)


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4, 5])
def test_the_search_reaches_the_brute_force_optimum_of_a_tiny_universe(seed):
    """Two groups of one contract pair (three contracts each side): every feasible book is enumerated and scored by
    the scorer; the search from cash reaches the best of them (and never claims more than it)."""
    scorer = _scorer(_program(n_groups=2, n_pairs=1, seed=seed, capacity=3))
    books = scorer.catalogue(10000)
    best = max(_exact(scorer, book) for book in books)
    out = KellyLocalSearch(scorer, PARAMS).run(None)
    assert out["record"]["end_value"] <= best+1e-9
    assert out["record"]["local_optimum"] and not out["record"]["budget_exhausted"]
    assert out["record"]["end_value"] == pytest.approx(best, abs=1e-6)


def test_the_gap_to_the_optimum_on_a_larger_brute_force_fixture_is_recorded_not_assumed():
    """Three groups of two pairs, capacity two: the optimum is enumerated; the search is within a stated gap of it."""
    scorer = _scorer(_program(n_groups=3, n_pairs=2, seed=11, capacity=2))
    books = scorer.catalogue(10**7)
    best = max(_exact(scorer, book) for book in books)
    out = KellyLocalSearch(scorer, PARAMS).run(None)
    gap = best-out["record"]["end_value"]
    assert -1e-9 <= gap <= 0.02*max(best, 1e-9) + 1e-6, (best, out["record"]["end_value"])


def test_utility_never_decreases_across_moves_and_the_start_is_the_first_value():
    scorer = _scorer(_program(n_groups=6, n_pairs=3, seed=3, capacity=5))
    out = KellyLocalSearch(scorer, PARAMS).run(None)
    values = out["values"]
    assert values[0] == out["record"]["start_value"] == pytest.approx(0., abs=1e-9)
    assert len(values) == out["record"]["moves"]+1 and out["record"]["moves"] > 0
    assert all(b > a for a, b in zip(values, values[1:]))
    assert values[-1] == out["record"]["end_value"] > 0
    assert sum(out["record"]["move_counts"].values()) == out["record"]["moves"]


def test_two_runs_and_two_instances_reach_the_same_selection():
    program = _program(n_groups=6, n_pairs=3, seed=3, capacity=5)
    first = KellyLocalSearch(_scorer(program), PARAMS).run(None)
    second_search = KellyLocalSearch(_scorer(program), PARAMS)
    again = second_search.run(None)
    third = second_search.run(None)   # one instance, run twice: no state leaks between runs
    for other in (again, third):
        assert other["quantities"] == first["quantities"] and other["record"] == first["record"]
        assert other["values"] == first["values"]
    assert "seconds" not in first["record"]


def test_the_final_selection_is_feasible_under_every_account_rule():
    """A binding reserve cap, a binding linear row, a joint row and a high floor each hold in the returned book."""
    cases = [dict(reserve_cap=900.), dict(lin_upper=3.), dict(joint_upper=60.), dict(floor=9000.)]
    for case in cases:
        scorer = _scorer(_program(n_groups=4, n_pairs=3, seed=5, capacity=6, **case))
        out = KellyLocalSearch(scorer, PARAMS).run(None)
        assert scorer.check_feasible(out["quantities"]) == {"feasible": True, "reason": None}, case
        assert out["selection"]["quantities"] == out["quantities"]
        assert out["record"]["end_contracts"] == sum(map(sum, out["quantities"])) > 0, case


def test_a_binding_cap_is_respected_exactly():
    scorer = _scorer(_program(n_groups=4, n_pairs=3, seed=5, capacity=6, reserve_cap=900.))
    out = KellyLocalSearch(scorer, PARAMS).run(None)
    assert sum(out["selection"]["reserves"]) <= 900.+1e-3
    wide = KellyLocalSearch(_scorer(_program(n_groups=4, n_pairs=3, seed=5, capacity=6)), PARAMS).run(None)
    assert wide["record"]["end_value"] >= out["record"]["end_value"]


def test_incremental_scoring_equals_the_scorers_feasibility_and_exact_value():
    """The search's shortcut for a trial agrees with the scorer's own check and value on random books and moves."""
    for case in (dict(), dict(reserve_cap=1500., lin_upper=4.), dict(joint_upper=80.)):
        scorer = _scorer(_program(n_groups=5, n_pairs=3, seed=9, capacity=4, **case))
        search = KellyLocalSearch(scorer, PARAMS)
        rng = np.random.default_rng(1)
        for _ in range(60):
            q = np.where(rng.random(search.N) < .15, rng.integers(0, 3, search.N), 0)
            if not scorer.check_feasible(search._nested(q))["feasible"]:
                continue
            search._load(q)
            assert search.value() == pytest.approx(_exact(scorer, search._nested(q)), rel=1e-10, abs=1e-9)
            flat = int(rng.integers(search.N))
            for dq in (1, -1):
                moved = q.copy()
                moved[flat] += dq
                inside = 0 <= moved[flat] <= search.cap[flat]
                try:
                    feasible = inside and scorer.check_feasible(search._nested(moved))["feasible"]
                except ValueError:
                    feasible = False
                got = search._trial({flat: dq})
                assert (got is not None) == feasible, (case, flat, dq)
                if feasible:
                    assert got == pytest.approx(_exact(scorer, search._nested(moved)), rel=1e-9, abs=1e-8)


def test_a_swap_changes_two_items_at_once_and_is_scored_exactly():
    scorer = _scorer(_program(n_groups=3, n_pairs=2, seed=2, capacity=3))
    search = KellyLocalSearch(scorer, PARAMS)
    q = np.zeros(search.N, dtype=np.int64)
    q[0] = 2
    search._load(q)
    moved = q.copy()
    moved[0] -= 1
    moved[search.off[1]+2] += 1
    got = search._trial({0: -1, int(search.off[1]+2): 1})
    assert got == pytest.approx(_exact(scorer, search._nested(moved)), rel=1e-9, abs=1e-8)
    # The exclusive partner of a held item cannot be added, but a swap onto it is fine: the held side leaves first.
    assert search._trial({1: 1}) is None
    q1 = np.zeros(search.N, dtype=np.int64)
    q1[0] = 1
    search._load(q1)
    assert search._trial({0: -1, 1: 1}) is not None


def test_a_feasible_start_is_climbed_from_and_never_ends_lower():
    scorer = _scorer(_program(n_groups=4, n_pairs=3, seed=6, capacity=5))
    cash = KellyLocalSearch(scorer, PARAMS).run(None)
    start = [[0]*len(g["capacity"]) for g in scorer.groups]
    start[0][0] = 1
    out = KellyLocalSearch(scorer, PARAMS).run(start)
    assert out["record"]["start_kind"] == "stage1" and out["record"]["start_contracts"] == 1
    assert out["record"]["end_value"] >= out["record"]["start_value"]
    assert cash["record"]["start_kind"] == "cash"
    # Starting at the cash run's end is already a local optimum: no move, no budget used.
    again = KellyLocalSearch(scorer, PARAMS).run(cash["quantities"])
    assert again["record"]["moves"] == 0 and again["record"]["local_optimum"] and not again["record"]["budget_exhausted"]
    assert again["quantities"] == cash["quantities"]


def test_removals_and_swaps_are_taken_when_the_start_holds_a_losing_contract():
    """Starting on the single worst contract, the climb must leave it (a removal or a swap) to improve."""
    scorer = _scorer(_program(n_groups=4, n_pairs=3, seed=6, capacity=5))
    search = KellyLocalSearch(scorer, PARAMS)
    worst, worst_value = None, 0.
    for flat in range(search.N):
        start = [[0]*len(g["capacity"]) for g in scorer.groups]
        start[int(search.gid[flat])][int(search.loc[flat])] = 1
        value = _exact(scorer, start)
        if value < worst_value:
            worst, worst_value = start, value
    assert worst is not None and worst_value < 0
    out = search.run(worst)
    counts = out["record"]["move_counts"]
    assert counts["remove"]+counts["swap"] >= 1 and out["record"]["end_value"] > 0 > out["record"]["start_value"]
    assert all(b > a for a, b in zip(out["values"], out["values"][1:]))


def test_a_swap_is_taken_when_the_reserve_cap_binds():
    """With the cap at 900 the climb on this fixture ends on a swap: a held contract is exchanged for a better one."""
    scorer = _scorer(_program(n_groups=6, n_pairs=3, seed=5, capacity=6, reserve_cap=900.))
    out = KellyLocalSearch(scorer, PARAMS).run(None)
    assert out["record"]["move_counts"]["swap"] >= 1 and scorer.check_feasible(out["quantities"])["feasible"]


def test_an_infeasible_start_begins_from_cash_and_says_so():
    scorer = _scorer(_program(n_groups=3, n_pairs=2, seed=2, capacity=3))
    start = [[0]*len(g["capacity"]) for g in scorer.groups]
    start[0][0] = start[0][1] = 1   # both sides of one pair: exclusive
    out = KellyLocalSearch(scorer, PARAMS).run(start)
    assert out["record"]["start_kind"] == "infeasible_start_cash" and out["record"]["start_contracts"] == 0
    with pytest.raises(ValueError, match="layout"):
        KellyLocalSearch(scorer, PARAMS).run([[0]])


def test_the_move_budget_stops_the_climb_and_is_recorded():
    scorer = _scorer(_program(n_groups=6, n_pairs=3, seed=3, capacity=5))
    out = KellyLocalSearch(scorer, {**PARAMS, "moves": 3}).run(None)
    assert out["record"]["moves"] == 3 and out["record"]["budget_exhausted"] and not out["record"]["local_optimum"]
    full = KellyLocalSearch(scorer, PARAMS).run(None)
    assert full["record"]["end_value"] > out["record"]["end_value"]


def test_a_smaller_shortlist_evaluates_fewer_moves_and_never_beats_the_exact_optimum_of_its_neighbourhood():
    scorer = _scorer(_program(n_groups=8, n_pairs=4, seed=4, capacity=3))
    wide = KellyLocalSearch(scorer, PARAMS).run(None)
    narrow = KellyLocalSearch(scorer, {**PARAMS, "adds_evaluated": 2, "swaps_evaluated": 0}).run(None)
    assert narrow["record"]["evaluations"] <= wide["record"]["evaluations"] or narrow["record"]["moves"] > wide["record"]["moves"]
    assert narrow["record"]["move_counts"]["swap"] == 0
    assert scorer.check_feasible(narrow["quantities"])["feasible"]


def test_restarts_are_seeded_deterministic_and_never_lower_the_value():
    scorer = _scorer(_program(n_groups=6, n_pairs=3, seed=3, capacity=5))
    plain = KellyLocalSearch(scorer, PARAMS).run(None)
    params = {**PARAMS, "restarts": 3}
    first = KellyLocalSearch(scorer, params).run(None)
    again = KellyLocalSearch(scorer, params).run(None)
    assert first["quantities"] == again["quantities"] and first["record"] == again["record"]
    assert first["record"]["end_value"] >= plain["record"]["end_value"]
    assert first["record"]["restarts"]+first["record"]["restarts_skipped"] == 3
    assert first["record"]["restarts"] >= 1
    other = KellyLocalSearch(scorer, {**params, "seed": 8}).run(None)
    assert scorer.check_feasible(other["quantities"])["feasible"]


def test_the_search_refuses_a_score_the_scorer_does_not_confirm(monkeypatch):
    scorer = _scorer(_program(n_groups=3, n_pairs=2, seed=2, capacity=3))
    monkeypatch.setattr(KellyLocalSearch, "PARITY", -1.)
    with pytest.raises(ValueError, match="exact recompute"):
        KellyLocalSearch(scorer, PARAMS).run(None)


def test_a_program_whose_best_book_is_cash_returns_cash():
    """Every contract costs more than it earns (negative edges, no hedge value): the search holds nothing."""
    program = _program(n_groups=2, n_pairs=1, seed=0, capacity=2, joint=False)
    for group in program["groups"]:
        group["credit"] = [-abs(c)-5. for c in group["credit"]]
    scorer = _scorer(program)
    out = KellyLocalSearch(scorer, PARAMS).run(None)
    assert out["quantities"] == [[0, 0], [0, 0]] and out["record"]["moves"] == 0 and out["record"]["local_optimum"]
    assert out["record"]["start_value"] == out["record"]["end_value"] == pytest.approx(0., abs=1e-9)
    assert max(_exact(scorer, book) for book in scorer.catalogue(10000)) == pytest.approx(0., abs=1e-9)


# --- the FAST variant's opt-in options (ADR-0307 Amendment 1) -------------------------------------------------------

def test_the_fast_options_are_opt_in_booleans_and_absent_ones_write_no_record_field():
    assert KellyLocalSearch.validate_params({**PARAMS, "cash_floor": True, "repair": False}) == []
    for bad in ({**PARAMS, "cash_floor": 1}, {**PARAMS, "repair": "yes"}, {**PARAMS, "repair": None}):
        assert KellyLocalSearch.validate_params(bad), bad
    scorer = _scorer(_program(n_groups=4, n_pairs=3, seed=6, capacity=5))
    plain = KellyLocalSearch(scorer, PARAMS).run(None)
    assert "cash_floor" not in plain["record"] and "repaired_contracts" not in plain["record"]
    off = KellyLocalSearch(scorer, {**PARAMS, "cash_floor": False, "repair": False}).run(None)
    assert off["quantities"] == plain["quantities"] and off["values"] == plain["values"]
    assert {k: v for k, v in off["record"].items() if k not in ("cash_floor", "repaired_contracts")} == plain["record"]
    assert off["record"]["cash_floor"] is False and off["record"]["repaired_contracts"] == 0


def _losing_start(scorer, search):
    """Every contract of the single worst item: a book well below cash that one move cannot lift above it."""
    worst, worst_value = None, 0.
    for flat in range(search.N):
        start = [[0]*len(g["capacity"]) for g in scorer.groups]
        start[int(search.gid[flat])][int(search.loc[flat])] = int(search.cap[flat])
        if scorer.check_feasible(start)["feasible"] and _exact(scorer, start) < worst_value:
            worst, worst_value = start, _exact(scorer, start)
    return worst


def test_the_cash_floor_trades_cash_when_the_climb_ends_below_it():
    scorer = _scorer(_program(n_groups=4, n_pairs=3, seed=6, capacity=5))
    start = _losing_start(scorer, KellyLocalSearch(scorer, PARAMS))
    budget = {**PARAMS, "moves": 1}
    below = KellyLocalSearch(scorer, budget).run(start)
    assert below["record"]["end_value"] < 0 and below["record"]["end_contracts"] > 0
    floored = KellyLocalSearch(scorer, {**budget, "cash_floor": True}).run(start)
    record = floored["record"]
    assert record["cash_floor"] is True and record["end_contracts"] == 0
    assert record["end_value"] == pytest.approx(0., abs=1e-9) == _exact(scorer, floored["quantities"])
    assert not any(q for row in floored["quantities"] for q in row)
    assert record["start_value"] == below["record"]["start_value"] and record["moves"] == below["record"]["moves"]
    # A climb that ends above cash is untouched by the floor.
    up = KellyLocalSearch(scorer, {**PARAMS, "cash_floor": True}).run(None)
    assert up["record"]["cash_floor"] is False and up["record"]["end_value"] > 0


def test_the_breach_measure_is_zero_exactly_when_the_scorer_accepts_the_book():
    rng = np.random.default_rng(4)
    for shape in (dict(reserve_cap=900.), dict(lin_upper=3.), dict(joint_upper=60.), dict(floor=9000.)):
        scorer = _scorer(_program(n_groups=4, n_pairs=2, seed=9, capacity=4, **shape))
        search = KellyLocalSearch(scorer, PARAMS)
        for _ in range(25):
            q = rng.integers(0, 3, search.N)
            search._load(q)
            assert (search._excess() <= 0.) == scorer.check_feasible(search._nested(search.q))["feasible"]


def test_repair_drops_the_breaching_contract_and_climbs_from_the_rest():
    scorer = _scorer(_program(n_groups=3, n_pairs=2, seed=2, capacity=3))
    search = KellyLocalSearch(scorer, PARAMS)
    cash = search.run(None)
    start = [[0]*len(g["capacity"]) for g in scorer.groups]
    start[0][0] = start[0][1] = 1   # both sides of one pair: exclusive
    start[1][0] = 2                 # a legal holding the repair must keep
    assert not scorer.check_feasible(start)["feasible"]
    out = KellyLocalSearch(scorer, {**PARAMS, "repair": True}).run(start)
    record = out["record"]
    assert record["start_kind"] == "repaired_stage1" and record["repaired_contracts"] == 1
    assert record["start_contracts"] == 3 and record["end_value"] >= record["start_value"]
    assert scorer.check_feasible(out["quantities"])["feasible"]
    again = KellyLocalSearch(scorer, {**PARAMS, "repair": True}).run(start)
    assert again["quantities"] == out["quantities"] and again["record"] == record
    incumbent = KellyLocalSearch(scorer, {**PARAMS, "repair": True}).run(start, "stage1_incumbent")
    assert incumbent["record"]["start_kind"] == "repaired_stage1_incumbent"
    # Without the option the original rule holds: cash.
    plain = KellyLocalSearch(scorer, PARAMS).run(start)
    assert plain["record"]["start_kind"] == "infeasible_start_cash" and plain["quantities"] == cash["quantities"]


@pytest.mark.parametrize("shape", [dict(reserve_cap=900.), dict(joint_upper=60.), dict(lin_upper=3.)])
def test_repair_of_a_shared_breach_removes_until_the_book_is_accepted(shape):
    scorer = _scorer(_program(n_groups=4, n_pairs=2, seed=9, capacity=4, **shape))
    search = KellyLocalSearch(scorer, PARAMS)
    # One side of each pair, every contract: far past the cap, the joint row or the linear row.
    full = [[int(c) if j % 2 == 0 else 0 for j, c in enumerate(g["capacity"])] for g in scorer.groups]
    assert not scorer.check_feasible(full)["feasible"]
    repaired, removed = search._repaired(search._flat(full))
    assert removed > 0 and scorer.check_feasible(search._nested(repaired))["feasible"]
    out = KellyLocalSearch(scorer, {**PARAMS, "repair": True}).run(full)
    assert out["record"]["repaired_contracts"] == removed
    assert scorer.check_feasible(out["quantities"])["feasible"]
