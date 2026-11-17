"""The incremental climb (C5) is the full-refresh climb: same picks, moves and end value, on programs of S = 3, 17, 300."""

import numpy as np
import pytest

from dskit.pipeline.libs.pyomo import TransportActionScore
from index_options.kelly_local import KellyLocalSearch

W0 = 10000.
PARAMS = {"moves": 200, "adds_evaluated": 64, "swaps_evaluated": 64, "restarts": 0, "seed": 7}


class _FullRefresh(KellyLocalSearch):
    """The reference: the pre-C5 arithmetic (full ``q @ D`` after every move, full floor check)."""
    INCREMENTAL = False


def program(n_scenarios, n_groups=6, n_pairs=4, atoms=7, seed=0, capacity=5, floor=100., joint_upper=None,
            reserve_cap=None):
    """A synthetic one-stage Kelly program with ``n_scenarios`` shock scenarios (quiet cell plus the rest)."""
    rng = np.random.default_rng(seed)
    groups, ids = [], []
    for g in range(n_groups):
        grid = np.linspace(90., 110., atoms)
        masses = rng.random(atoms)+.2
        masses /= masses.sum()
        losses, credit, reserve, capacities, exclusive = [], [], [], [], []
        for pair in range(n_pairs):
            payoff = np.clip(96.+3.*pair-grid, 0., 6.)*10.
            short_credit = float(masses @ payoff)*(1.+rng.uniform(-.1, .4))+1.
            for sign, c in ((1., short_credit), (-1., -short_credit*1.05)):
                losses.append(sign*payoff)
                credit.append(c)
                reserve.append(max(0., float(np.max(sign*payoff))-c))
                capacities.append(capacity)
            exclusive.append([2*pair, 2*pair+1])
        groups.append({"id": f"G{g}", "grid": grid.tolist(), "masses": masses.tolist(), "radius": 0.,
                       "scale": 100., "credit": credit, "losses": np.array(losses).T.tolist(),
                       "capacity": capacities, "exclusive": exclusive, "reserve_rows": [reserve],
                       "linear_rows": []})
        ids.append(f"G{g}")
    weights = np.full(n_scenarios, .4/(n_scenarios-1))
    scenarios = [{"id": "market:0", "weight": .6, "terms": {}}]
    for k in range(1, n_scenarios):
        sign = 1. if k % 2 else -1.
        scenarios.append({"id": f"market:{k}", "weight": float(weights[k]),
                          "terms": {i: (sign*rng.normal(0., 15., 2*n_pairs)).tolist() for i in ids}})
    out = {"groups": groups, "caps": [{"groups": ids, "upper": W0*.5 if reserve_cap is None else reserve_cap}],
           "utility": {"w0": W0, "gamma": 4., "n_tangents": 16, "core": 2., "wealth_lo": floor,
                       "aggregate": {i: ("A" if n % 2 == 0 else "B") for n, i in enumerate(ids)},
                       "joint": scenarios}}
    if joint_upper is not None:
        out["joint_rows"] = [{"id": "shock", "terms": {i: [10.]*(2*n_pairs) for i in ids}, "upper": joint_upper}]
    return out


def _scorer(spec):
    return TransportActionScore(spec, {"parity_tolerance": 1e-3})


def _run(cls, spec, params, start=None):
    return cls(_scorer(spec), params).run(start)


def _assert_same(new, old):
    assert new["quantities"] == old["quantities"]
    assert new["record"] == {**old["record"], "end_value": new["record"]["end_value"],
                             "start_value": new["record"]["start_value"]}
    assert new["record"]["end_value"] == old["record"]["end_value"]   # the final state is reloaded in full
    assert new["record"]["start_value"] == old["record"]["start_value"]
    assert len(new["values"]) == len(old["values"])
    assert new["values"] == pytest.approx(old["values"], rel=1e-12, abs=1e-9)


@pytest.mark.parametrize("scenarios", [3, 17, 300])
@pytest.mark.parametrize("case", [{}, {"floor": 9000.}, {"joint_upper": 60.}, {"reserve_cap": 900.}])
def test_incremental_climb_matches_the_full_refresh(scenarios, case):
    spec = program(scenarios, seed=scenarios, **case)
    new, old = _run(KellyLocalSearch, spec, PARAMS), _run(_FullRefresh, spec, PARAMS)
    assert new["record"]["moves"] > 3
    _assert_same(new, old)


@pytest.mark.parametrize("scenarios", [17, 300])
def test_incremental_climb_matches_with_restarts_and_repair(scenarios):
    spec = program(scenarios, seed=9, floor=4000.)
    params = {**PARAMS, "restarts": 2, "repair": True, "cash_floor": True}
    infeasible = [[5]*8 for _ in range(6)]       # over the reserve cap: repaired by greedy removal
    for start in (None, infeasible):
        _assert_same(_run(KellyLocalSearch, spec, params, start), _run(_FullRefresh, spec, params, start))


def test_the_floor_prefilter_agrees_with_the_full_check_on_every_item():
    search = KellyLocalSearch(_scorer(program(17, seed=2, floor=9500.)), PARAMS)
    search._load(np.zeros(search.N, dtype=np.int64))
    ids = np.arange(search.N)
    for _ in range(4):
        full = (search.W[None, :]+search.D[ids] >= search.lo-search.tol).all(axis=1)
        assert (search._floor_ok(ids) == full).all()
        found = search._best_move()
        if found is None:
            break
        search._apply(found[1])


def test_the_applied_wealth_is_the_full_recompute_to_rounding():
    search = KellyLocalSearch(_scorer(program(17, seed=4)), PARAMS)
    search._load(np.zeros(search.N, dtype=np.int64))
    for _ in range(30):
        found = search._best_move()
        if found is None:
            break
        search._apply(found[1])
        assert search.W == pytest.approx(W0+search.q.astype(float) @ search.D, rel=1e-12, abs=1e-8)
