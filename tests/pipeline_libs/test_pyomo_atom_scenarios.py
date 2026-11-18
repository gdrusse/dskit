"""The utility block's compact sampled scenarios (``atom_scenarios``): validation, equivalence and the refusals."""

import copy

import numpy as np
import pytest

from dskit.pipeline.libs.pyomo import TransportActionScore, TransportPortfolioSolve

W0 = 10000.
KEY = TransportPortfolioSolve.UTILITY_ATOM_KEY


def _groups(n_groups=3, n_pairs=2, atoms=7, seed=0, capacity=4):
    """Short/long put pairs on an atom grid (the shape of the weekly program's groups)."""
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
    return groups


def _atoms(groups, n=40, seed=1):
    rng = np.random.default_rng(seed)
    return {g["id"]: rng.integers(0, len(g["masses"]), n).tolist() for g in groups}


def _program(groups, atoms, weights=None, floor=100.):
    n = len(next(iter(atoms.values())))
    weights = weights or [1./n]*n
    return {"groups": groups, "caps": [{"groups": [g["id"] for g in groups], "upper": W0*.5}],
            "utility": {"w0": W0, "gamma": 4., "n_tangents": 16, "core": 2., "wealth_lo": floor,
                        "aggregate": {g["id"]: ("A" if i % 2 == 0 else "B") for i, g in enumerate(groups)},
                        "joint": [], KEY: {"weights": weights, "atoms": atoms}}}


def _expanded(program):
    """The same scenarios written out in the list form: each group's profit beyond the weighted sampled mean."""
    out = copy.deepcopy(program)
    spec = out["utility"].pop(KEY)
    weights = np.asarray(spec["weights"])
    joint = []
    centres = {}
    for g in out["groups"]:
        losses = np.asarray(g["losses"], dtype=float)
        centres[g["id"]] = weights @ losses[spec["atoms"][g["id"]]]
    for s, w in enumerate(spec["weights"]):
        joint.append({"id": f"sample:{s}", "weight": w,
                      "terms": {g["id"]: (centres[g["id"]]-np.asarray(g["losses"], dtype=float)[spec["atoms"][g["id"]][s]]
                                          ).tolist() for g in out["groups"]}})
    out["utility"]["joint"] = joint
    return out


def _books(groups, count=12, seed=3):
    rng = np.random.default_rng(seed)
    books = [[[0]*len(g["capacity"]) for g in groups]]
    for _ in range(count):
        book = []
        for g in groups:
            row = [0]*len(g["capacity"])
            for a, _b in g["exclusive"]:
                row[a] = int(rng.integers(0, 3))
            book.append(row)
        books.append(book)
    return books


def test_the_compact_form_scores_exactly_as_its_expanded_list_form():
    groups = _groups()
    program = _program(groups, _atoms(groups))
    compact = TransportActionScore(program, {"parity_tolerance": 1e-3})
    listed = TransportActionScore(_expanded(program), {"parity_tolerance": 1e-3})
    assert [name for name, _w, _t in compact.scenarios] == [name for name, _w, _t in listed.scenarios]
    assert compact.envelope.hi == pytest.approx(listed.envelope.hi, rel=1e-12)
    for book in _books(groups):
        a, b = compact.evaluate(book), listed.evaluate(book)
        assert a["feasible"] == b["feasible"]
        if not a["feasible"]:
            continue
        for key in ("exact", "envelope", "wealth_min", "wealth_max"):
            assert a["utility"][key] == pytest.approx(b["utility"][key], rel=1e-10, abs=1e-8), key
        for name, wealth in a["utility"]["wealth"].items():
            assert wealth == pytest.approx(b["utility"]["wealth"][name], rel=1e-12)


def test_every_items_weighted_deviation_is_zero_so_the_expectation_is_the_forecasts():
    groups = _groups(seed=4)
    weights = list(np.random.default_rng(2).dirichlet(np.ones(40)))
    weights[-1] = 1.-sum(weights[:-1])
    scorer = TransportActionScore(_program(groups, _atoms(groups, seed=5), weights), {"parity_tolerance": 1e-3})
    for i in range(len(groups)):
        rows = np.array([terms[i] for _name, _w, terms in scorer.scenarios])
        assert np.allclose(np.asarray(weights) @ rows, 0., atol=1e-10)
    # a held book's weighted mean wealth is w0 plus its forecast expected profit
    book = _books(groups, count=1)[1]
    wealth = scorer.evaluate(book)["utility"]["wealth"]
    expected = sum(float(np.dot(e, q)) for e, q in zip(scorer.expected, book))
    assert float(np.dot(weights, list(wealth.values()))) == pytest.approx(W0+expected, rel=1e-12)


def test_scenario_rows_are_views_of_one_table_per_group():
    groups = _groups()
    scorer = TransportActionScore(_program(groups, _atoms(groups, n=200)), {"parity_tolerance": 1e-3})
    _weights, tables = scorer.atom_tables
    for _name, _w, terms in scorer.scenarios[:5]:
        for i, row in terms.items():
            assert row.base is tables[i][0]


@pytest.mark.parametrize("mutate,message", [
    (lambda p: p["utility"].update(joint=[{"id": "q", "weight": 1., "terms": {}}]), "invalid utility block"),
    (lambda p: p["utility"][KEY]["atoms"].pop("G0"), "invalid atom scenarios"),
    (lambda p: p["utility"][KEY]["atoms"]["G0"].__setitem__(0, 7), "invalid atom scenarios"),
    (lambda p: p["utility"][KEY]["atoms"]["G0"].__setitem__(0, -1), "invalid atom scenarios"),
    (lambda p: p["utility"][KEY]["atoms"]["G0"].__setitem__(0, True), "invalid atom scenarios"),
    (lambda p: p["utility"][KEY]["atoms"]["G0"].__setitem__(0, 1.), "invalid atom scenarios"),
    (lambda p: p["utility"][KEY]["atoms"]["G0"].pop(), "invalid atom scenarios"),
    (lambda p: p["utility"][KEY]["weights"].__setitem__(0, .5), "sum to one"),
    (lambda p: p["utility"][KEY]["weights"].__setitem__(0, -.025), "invalid atom scenarios"),
    (lambda p: p["utility"][KEY].update(weights=[1.]), "invalid atom scenarios"),
    (lambda p: p["utility"][KEY].update(extra=1), "invalid atom scenarios"),
    (lambda p: p["utility"].update({KEY: None}), "invalid atom scenarios"),
])
def test_a_malformed_compact_form_is_refused(mutate, message):
    groups = _groups()
    program = _program(groups, _atoms(groups))
    mutate(program)
    with pytest.raises(ValueError, match=message):
        TransportActionScore(program, {"parity_tolerance": 1e-3})


def test_the_list_form_still_needs_scenarios_and_the_compact_form_is_never_built():
    groups = _groups()
    program = _program(groups, _atoms(groups))
    bare = copy.deepcopy(program)
    del bare["utility"][KEY]
    with pytest.raises(ValueError, match="invalid utility block"):
        TransportActionScore(bare, {"parity_tolerance": 1e-3})
    solve = TransportPortfolioSolve("joint", {
        "absolute_gap": 1e-7, "relative_gap": 0., "parity_tolerance": 1e-3,
        "solver_options": {"mip_abs_gap": 1e-8, "mip_rel_gap": 0., "threads": 1}})
    with pytest.raises(ValueError, match="never built"):
        solve.run(None, program)
    expanded = _expanded(program)   # the list form of the same scenarios still builds and solves
    assert "selection" in solve.run(None, expanded)


def test_the_floor_is_checked_in_every_sampled_scenario():
    groups = _groups(capacity=50)
    loose = TransportActionScore(_program(groups, _atoms(groups)), {"parity_tolerance": 1e-3})
    tight = TransportActionScore(_program(groups, _atoms(groups), floor=W0-1.), {"parity_tolerance": 1e-3})
    book = [[0]*len(g["capacity"]) for g in groups]
    book[0][0] = 50
    assert loose.check_feasible(book)["feasible"]
    verdict = tight.check_feasible(book)
    assert not verdict["feasible"] and "wealth floor in scenario sample:" in verdict["reason"]
