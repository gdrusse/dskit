"""The joint-scenario generators (empirical copula over the forecast marginals) and their use by the Kelly climb."""

import copy

import numpy as np
import pytest

from dskit.pipeline.libs.pyomo import TransportActionScore, TransportPortfolioSolve
from index_options.joint_scenarios import (EmpiricalCopula, InsufficientHistory, JointScenarioGenerator, defects,
                                           generator)
from index_options.kelly_local import KellyLocalSearch
from index_options.kelly_multistart import KellyMultiStart
from index_options.kelly_sizer import joint_atoms, step_atoms

SPEC = {"method": "empirical_copula", "scenarios": 400, "window": 250, "block": 5, "seed": 20261117}
PARAMS = {"moves": 200, "adds_evaluated": 64, "swaps_evaluated": 64, "restarts": 0, "seed": 7}


def _returns(m=300, names=("A", "B", "C"), seed=0):
    rng = np.random.default_rng(seed)
    market = rng.standard_t(4, m)*.01
    return {name: market*(1.+.3*i)+rng.normal(0., .01, m) for i, name in enumerate(names)}


def _request(returns, groups, day="2020-02-03", min_returns=20):
    names = sorted(returns)
    return {"day": day, "names": names, "returns": np.column_stack([returns[n] for n in names]),
            "min_returns": min_returns, "groups": groups}


def _masses(k=9, seed=0):
    p = np.random.default_rng(seed).random(k)+.1
    return (p/p.sum()).tolist()


def test_the_spec_is_validated_by_name():
    assert defects(SPEC) == []
    for bad in ({**SPEC, "scenarios": 1}, {**SPEC, "scenarios": 300.}, {**SPEC, "seed": -1}, {**SPEC, "seed": True},
                {**SPEC, "window": 1}, {**SPEC, "block": 0}, {**SPEC, "block": 251}, {**SPEC, "extra": 1},
                {**SPEC, "window": 10, "block": 6},
                {k: v for k, v in SPEC.items() if k != "block"}, {**SPEC, "method": "gaussian"}, None, []):
        assert defects(bad), bad
        with pytest.raises(ValueError):
            generator(bad)
    assert isinstance(generator(SPEC), EmpiricalCopula)


def test_draw_is_the_familys_one_template():
    with pytest.raises(TypeError, match="may not override draw"):
        class Bad(JointScenarioGenerator):   # noqa: F841 - the class statement is the test
            def draw(self, request):
                return {}

            def uniforms(self, request, returns, column, needed):
                return {}, {}


def test_the_step_inverse_is_the_sizers_own():
    masses = {"g": _masses(), "h": _masses(seed=1)}
    sample = joint_atoms(masses, {"g": "A", "h": "B"}, {"A": .5, "B": .2}, 500, 3)
    for key, name in (("g", "A"), ("h", "B")):
        assert np.array_equal(step_atoms(masses[key], sample["uniforms"][name]), sample["atoms"][key])


def test_a_draw_is_deterministic_under_its_seed_and_date_and_moves_with_either():
    groups = [{"id": "g", "name": "A", "horizon": 10, "masses": _masses()}]
    req = _request(_returns(), groups)
    first, again = generator(SPEC).draw(req), generator(SPEC).draw(copy.deepcopy(req))
    assert first == again
    assert generator({**SPEC, "seed": 1}).draw(req)["atoms"] != first["atoms"]
    assert generator(SPEC).draw({**req, "day": "2020-02-10"})["atoms"] != first["atoms"]
    assert first["weights"] == [1./400]*400 and all(type(a) is int for a in first["atoms"]["g"])


def test_each_groups_sampled_law_is_its_forecast():
    masses = _masses(k=12, seed=4)
    groups = [{"id": "g", "name": "B", "horizon": 15, "masses": masses}]
    draw = generator({**SPEC, "scenarios": 4000}).draw(_request(_returns(), groups))
    frequency = np.bincount(draw["atoms"]["g"], minlength=12)/4000
    assert np.max(np.abs(frequency-np.asarray(masses))) < .02


def test_the_same_dates_tie_the_names_together():
    """A copy of a name moves with it (same atoms); its mirror moves against it (atoms reversed)."""
    base = _returns()["A"]
    returns = {"A": base, "B": base.copy(), "C": -base}
    masses = _masses(k=11, seed=2)
    groups = [{"id": n, "name": n, "horizon": 7, "masses": masses} for n in ("A", "B", "C")]
    draw = generator(SPEC).draw(_request(returns, groups))
    a, b, c = (np.asarray(draw["atoms"][n]) for n in ("A", "B", "C"))
    assert np.array_equal(a, b)
    assert np.corrcoef(a, c)[0, 1] < -.95
    # independent names are not tied
    rng = np.random.default_rng(9)
    loose = {"A": base, "B": rng.normal(0., .01, len(base))}
    draw = generator(SPEC).draw(_request(loose, [{"id": n, "name": n, "horizon": 7, "masses": masses} for n in "AB"]))
    assert abs(np.corrcoef(draw["atoms"]["A"], draw["atoms"]["B"])[0, 1]) < .2


def test_a_names_expiries_are_nested_windows_of_one_path():
    masses = _masses(k=15, seed=3)
    groups = [{"id": "short", "name": "A", "horizon": 3, "masses": masses},
              {"id": "long", "name": "A", "horizon": 30, "masses": masses},
              {"id": "same", "name": "A", "horizon": 30, "masses": masses}]
    draw = generator(SPEC).draw(_request(_returns(), groups))
    assert draw["atoms"]["long"] == draw["atoms"]["same"]
    assert np.corrcoef(draw["atoms"]["short"], draw["atoms"]["long"])[0, 1] > .1
    assert draw["record"]["horizon_max"] == 30 and draw["record"]["pairs"] == 2


def test_a_names_horizons_read_one_path_exactly():
    """One spike day: a scenario whose short horizon saw it has a long horizon that saw it too (nested windows)."""
    m = 120
    spiked = np.zeros(m)
    spiked[70] = .5
    req = _request({"A": spiked}, [])
    returns = np.asarray(req["returns"])
    for block in (10, 40):   # stitched blocks and one block per path
        gen = generator({**SPEC, "window": 120, "block": block})
        u, _record = gen.uniforms(req, returns, {"A": 0}, [("A", 3), ("A", 25)])
        short, long = u["A", 3] > u["A", 3].min(), u["A", 25] > u["A", 25].min()
        assert short.any() and short.sum() < long.sum() < 400
        assert np.all(long[short]), "every path that holds the spike within 3 sessions holds it within 25"
        assert np.all((u["A", 3] > 0) & (u["A", 3] < 1))


def test_the_window_bounds_what_is_read_and_short_history_refuses():
    groups = [{"id": "g", "name": "A", "horizon": 5, "masses": _masses()}]
    returns = _returns(m=300)
    spec = {**SPEC, "window": 60}
    base = generator(spec).draw(_request(returns, groups))
    assert base["record"]["returns"] == 60 and base["record"]["block_starts"] == 56
    # anything older than the window cannot move the draw
    older = {n: np.concatenate([np.full(240, 9.), r[240:]]) for n, r in returns.items()}
    assert generator(spec).draw(_request(older, groups)) == base
    with pytest.raises(InsufficientHistory):
        generator(spec).draw(_request({n: r[:15] for n, r in returns.items()}, groups))
    with pytest.raises(InsufficientHistory):
        generator(spec).draw(_request(returns, groups, min_returns=61))   # the study's floor above the window


def test_a_malformed_request_is_refused():
    returns = _returns()
    good = {"id": "g", "name": "A", "horizon": 5, "masses": _masses()}
    for bad in ({**good, "name": "Z"}, {**good, "horizon": 0}, {**good, "horizon": 2.}, {**good, "masses": [.5, .4]},
                {**good, "masses": [1.5, -.5]}):
        with pytest.raises(ValueError, match="invalid joint-scenario group"):
            generator(SPEC).draw(_request(returns, [bad]))
    req = _request(returns, [good])
    req["returns"][3, 0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        generator(SPEC).draw(req)


# --- the climb on the compact scenarios -------------------------------------------------------------------------------

def _program(n_groups=4, n_pairs=2, atoms=9, seed=0, capacity=4):
    """Weekly-shaped groups, two names, scenarios from the empirical copula."""
    rng = np.random.default_rng(seed)
    groups = []
    for g in range(n_groups):
        grid = np.linspace(90., 110., atoms)
        masses = rng.random(atoms)+.2
        masses /= masses.sum()
        losses, credit, reserve, exclusive = [], [], [], []
        for pair in range(n_pairs):
            payoff = np.clip(96.+3.*pair-grid, 0., 6.)*10.
            short = float(masses @ payoff)*(1.+rng.uniform(-.1, .4))+1.
            for column, c in ((payoff, short), (-payoff, -short*1.05)):
                losses.append(column)
                credit.append(c)
                reserve.append(max(0., float(np.max(column))-c))
            exclusive.append([2*pair, 2*pair+1])
        groups.append({"id": f"G{g}", "grid": grid.tolist(), "masses": masses.tolist(), "radius": 0., "scale": 100.,
                       "credit": credit, "losses": np.array(losses).T.tolist(), "capacity": [capacity]*len(credit),
                       "exclusive": exclusive, "reserve_rows": [reserve], "linear_rows": []})
    draw = generator({**SPEC, "scenarios": 300}).draw(_request(
        _returns(names=("A", "B")), [{"id": g["id"], "name": "AB"[i % 2], "horizon": 5+3*i, "masses": g["masses"]}
                                     for i, g in enumerate(groups)]))
    return {"groups": groups, "caps": [{"groups": [g["id"] for g in groups], "upper": 5000.}],
            "utility": {"w0": 10000., "gamma": 4., "n_tangents": 16, "core": 2., "wealth_lo": 100.,
                        "aggregate": {g["id"]: "AB"[i % 2] for i, g in enumerate(groups)},
                        "joint": [], TransportPortfolioSolve.UTILITY_ATOM_KEY: {"weights": draw["weights"],
                                                                                "atoms": draw["atoms"]}}}


def _listed(program):
    out = copy.deepcopy(program)
    spec = out["utility"].pop(TransportPortfolioSolve.UTILITY_ATOM_KEY)
    w = np.asarray(spec["weights"])
    rows = {g["id"]: np.asarray(g["losses"], dtype=float) for g in out["groups"]}
    centre = {k: w @ rows[k][spec["atoms"][k]] for k in rows}
    out["utility"]["joint"] = [{"id": f"sample:{s}", "weight": weight,
                                "terms": {k: (centre[k]-rows[k][spec["atoms"][k][s]]).tolist() for k in rows}}
                               for s, weight in enumerate(spec["weights"])]
    return out


def test_the_climb_on_the_compact_form_equals_the_climb_on_its_list_form():
    program = _program()
    compact = KellyLocalSearch(TransportActionScore(program, {"parity_tolerance": 1e-3}), PARAMS)
    listed = KellyLocalSearch(TransportActionScore(_listed(program), {"parity_tolerance": 1e-3}), PARAMS)
    assert np.allclose(compact.D, listed.D, rtol=0., atol=1e-9)
    a, b = compact.run(None), listed.run(None)
    assert a["quantities"] == b["quantities"] and a["record"]["moves"] == b["record"]["moves"]
    assert a["record"]["end_value"] == pytest.approx(b["record"]["end_value"], rel=1e-10)
    assert a["record"]["end_contracts"] > 0


def test_the_blocked_floor_check_is_the_one_shot_check():
    search = KellyLocalSearch(TransportActionScore(_program(), {"parity_tolerance": 1e-3}), PARAMS)
    search._load(np.zeros(search.N, dtype=np.int64))
    search.lo = 9990.   # a floor some adds breach
    ids = np.arange(search.N)
    whole = (search.W[None, :]+search.D[ids] >= search.lo-search.tol).all(axis=1)
    search.FLOOR_CHUNK_ELEMENTS = search.S   # one row per block
    assert np.array_equal(search._floor_ok(ids), whole) and not whole.all() and whole.any()
    search.FLOOR_CHUNK_ELEMENTS = 7*search.S+1
    assert np.array_equal(search._floor_ok(ids), whole)
    assert search._floor_ok(ids[:0]).shape == (0,)


def test_the_multistart_singles_on_the_compact_form_equal_those_on_its_list_form():
    from test_kelly_multistart import MULTI2

    program = _program(seed=2)
    compact = KellyMultiStart(TransportActionScore(program, {"parity_tolerance": 1e-3}), PARAMS, MULTI2)
    listed = KellyMultiStart(TransportActionScore(_listed(program), {"parity_tolerance": 1e-3}), PARAMS, MULTI2)
    for search in (compact, listed):
        search._load(np.zeros(search.N, dtype=np.int64))
    singles = compact._singles()
    assert singles and [s for s in singles] == [s for s in listed._singles()]
    compact.FLOOR_CHUNK_ELEMENTS = compact.S   # one row per block: the same starts
    compact._load(np.zeros(compact.N, dtype=np.int64))
    assert compact._singles() == singles
    a, b = compact.run_multistart(), listed.run_multistart()
    assert a["quantities"] == b["quantities"]
