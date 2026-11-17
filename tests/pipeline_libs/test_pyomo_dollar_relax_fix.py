"""Opt-in relax-and-fix for a DOLLAR program (``dollar_solve`` ``relax_fix``): synthetic-program tests."""
import copy

import numpy as np
import pytest

from dskit.pipeline.libs.pyomo import TransportActionScore, TransportPortfolioSolve

TOL = 1e-5
SEARCH = {"iterations": 4, "neighbourhood": 3, "node_limit": 200, "seed": 7}


def _program(groups=10, actions=6, seed=3, tight=.35):
    """A multi-group dollar knapsack: random credits and expected losses, shared reserve caps (LP bound > integer value)."""
    rng = np.random.default_rng(seed)
    gs = []
    for k in range(groups):
        grid = [0., 1., 2.]
        gs.append({"id": f"g{k}", "grid": grid, "masses": [.25, .5, .25], "radius": 0., "scale": 1.,
                   "credit": [float(v) for v in rng.uniform(1., 4., actions)],
                   "losses": [[float(v) for v in rng.uniform(0., 3., actions)] for _ in grid],
                   "capacity": [int(v) for v in rng.integers(1, 5, actions)], "exclusive": [],
                   "reserve_rows": [[float(v) for v in rng.uniform(1., 7., actions)]], "linear_rows": []})
    total = sum(sum(c*r for c, r in zip(g["capacity"], g["reserve_rows"][0])) for g in gs)
    caps = [{"groups": [g["id"] for g in gs], "upper": round(total*tight, 3)},
            {"groups": [g["id"] for g in gs[:groups//2]], "upper": round(total*tight/3, 3)}]
    return {"groups": gs, "caps": caps}


def _params(**extra):
    return {"absolute_gap": 1e-7, "relative_gap": 0., "parity_tolerance": TOL, "timeout_policy": "return",
            "solver_options": {"mip_abs_gap": 1e-8, "mip_rel_gap": 0., "threads": 1, "time_limit": 60.}, **extra}


def _solve(inputs, **extra):
    node = TransportPortfolioSolve("w", _params(**extra))
    return node, node.run(None, copy.deepcopy(inputs))["selection"]


def _candidate(selection):
    return selection["candidate"] if "timeout" in selection else selection


def _feasible(inputs, selection):
    """The independent scorer accepts the quantities (capacity, caps) and reproduces the value."""
    quantities = _candidate(selection)["quantities"]
    scored = TransportActionScore(inputs, {"parity_tolerance": TOL}).evaluate(quantities)
    assert scored["value"] == pytest.approx(_candidate(selection)["value"], abs=1e-6)
    return scored["value"]


def test_the_key_validates():
    assert not TransportPortfolioSolve.validate_params(_params())
    assert not TransportPortfolioSolve.validate_params(_params(dollar_solve="milp"))
    assert not TransportPortfolioSolve.validate_params(_params(dollar_solve="relax_fix"))
    assert not TransportPortfolioSolve.validate_params(_params(dollar_solve="relax_fix", utility_search=SEARCH))
    for bad in ("direct", "RELAX_FIX", 1, None):
        assert TransportPortfolioSolve.validate_params(_params(dollar_solve=bad))
    raising = {k: v for k, v in _params(dollar_solve="relax_fix").items() if k != "timeout_policy"}
    assert any("timeout_policy return" in d for d in TransportPortfolioSolve.validate_params(raising))


def test_an_absent_key_and_milp_are_the_same_solve():
    inputs = _program(groups=4)
    plain_node, plain = _solve(inputs)
    milp_node, milp = _solve(inputs, dollar_solve="milp")
    for node in (plain_node, milp_node):
        assert node.last_dollar_solve is None

    def stable(selection):   # the certificate's seconds are a measurement
        out = copy.deepcopy(selection)
        for certificate in out["certificates"]:
            certificate["seconds"] = 0.
        return out
    assert stable(plain) == stable(milp)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_relax_fix_is_feasible_within_the_lp_bound_and_below_the_full_milp(seed):
    inputs = _program(seed=seed)
    _, full = _solve(inputs)
    assert "timeout" not in full
    node, fast = _solve(inputs, dollar_solve="relax_fix")
    record = node.last_dollar_solve
    value = _feasible(inputs, fast)
    assert value <= full["value"]+1e-6 and value >= 0.
    assert record["strategy"] == "relax_fix" and record["lp_bound"] >= full["value"]-1e-6
    assert record["restricted_objective"] <= record["lp_bound"]+1e-6
    assert _candidate(fast)["bound"] == pytest.approx(record["lp_bound"])    # the relaxation bounds the whole program
    assert _candidate(fast)["value"] <= _candidate(fast)["bound"]+1e-6
    assert 0 < record["support_actions"] <= record["actions"]


def test_the_record_is_complete():
    inputs = _program()
    node, selection = _solve(inputs, dollar_solve="relax_fix", utility_search=SEARCH)
    record = node.last_dollar_solve
    assert tuple(record) == TransportPortfolioSolve.DOLLAR_SOLVE_RECORD_KEYS
    for key in ("strategy", "outcome", "actions", "support_actions", "lp_bound", "restricted_objective",
                "restricted_termination", "final_objective", "lp_seconds", "restricted_seconds", "search_seconds",
                "seconds", "search"):
        assert record[key] is not None, key
    assert set(record["search"]) == set(TransportPortfolioSolve.SEARCH_RECORD_KEYS)
    assert record["final_objective"] >= record["restricted_objective"]-1e-9
    assert record["outcome"] in ("incumbent", "relaxation_certified")
    assert record["seconds"] >= record["lp_seconds"]


def test_the_search_never_lowers_the_incumbent():
    inputs = _program(seed=5)
    _, bare = _solve(inputs, dollar_solve="relax_fix")
    node, searched = _solve(inputs, dollar_solve="relax_fix", utility_search=SEARCH)
    assert _feasible(inputs, searched) >= _feasible(inputs, bare)-1e-9
    assert node.last_dollar_solve["search"]["value_after"] >= node.last_dollar_solve["search"]["value_before"]


def test_an_unproved_start_is_the_gap_envelope_and_a_loose_gap_certifies():
    inputs = _program(seed=2)
    node, tight = _solve(inputs, dollar_solve="relax_fix")
    if node.last_dollar_solve["outcome"] == "incumbent":
        assert tight["timeout"] == "gap" and tight["candidate"]["gap"] > 0
        assert tight["certificates"][0]["termination"] == "maxTimeLimit"
    loose = _params(dollar_solve="relax_fix", absolute_gap=1e9)
    node = TransportPortfolioSolve("l", loose)
    selection = node.run(None, copy.deepcopy(inputs))["selection"]
    assert "timeout" not in selection and node.last_dollar_solve["outcome"] == "relaxation_certified"
    assert selection["bound"] == pytest.approx(node.last_dollar_solve["lp_bound"])


def test_the_recorded_replay_accepts_what_the_live_solve_recorded():
    inputs = _program(seed=2)
    for extra in ({}, {"absolute_gap": 1e9}):
        node, selection = _solve(inputs, dollar_solve="relax_fix", **extra)
        certificates = [{k: v for k, v in c.items() if k != "binding"} for c in selection["certificates"]]
        replay = TransportPortfolioSolve("r", _params(dollar_solve="relax_fix", **extra))
        claimed = {k: v for k, v in selection.items() if k != "certificates"}
        again = replay._recorded(copy.deepcopy(inputs), claimed, copy.deepcopy(certificates))
        assert _candidate(again)["quantities"] == _candidate(selection)["quantities"]


@pytest.mark.parametrize("search", [None, SEARCH])
def test_the_time_limit_is_shared_inside_the_document_budget(monkeypatch, search):
    """Every solve is handed a limit inside the document's: the clock is the stage-one budget, not an add-on."""
    budget, seen = 100., {"solves": [], "search": []}
    once, searched = TransportPortfolioSolve._solve_once, TransportPortfolioSolve._search_incumbent

    def spy_once(self, solver, model, time_limit=None):
        seen["solves"].append(time_limit)
        return once(self, solver, model, time_limit)

    def spy_search(self, solver, model, bound, stakes, seconds=None):
        seen["search"].append(seconds)
        return searched(self, solver, model, bound, stakes, seconds)
    monkeypatch.setattr(TransportPortfolioSolve, "_solve_once", spy_once)
    monkeypatch.setattr(TransportPortfolioSolve, "_search_incumbent", spy_search)
    inputs = _program(seed=2)
    options = {"mip_abs_gap": 1e-8, "mip_rel_gap": 0., "threads": 1, "time_limit": budget}
    extra = {} if search is None else {"utility_search": search}
    node, _selection = _solve(inputs, dollar_solve="relax_fix", solver_options=options, **extra)
    restricted = seen["solves"][0]
    assert restricted <= budget*(.4 if search else 1.)+1e-9
    if search:
        assert seen["search"] and seen["search"][0] <= .25*budget+1e-9
        assert restricted+seen["search"][0] <= budget
    assert node.last_dollar_solve["seconds"] < budget




def test_a_relaxation_that_does_not_solve_falls_back_to_the_integer_program(monkeypatch):
    monkeypatch.setattr(TransportPortfolioSolve, "_relaxation", lambda self, solver, model, limit: None)
    inputs = _program(groups=4)
    node, selection = _solve(inputs, dollar_solve="relax_fix")
    assert node.last_dollar_solve["outcome"] == "relaxation_failed"
    _, full = _solve(inputs)
    assert _feasible(inputs, selection) == pytest.approx(full["value"], abs=1e-6)


def test_bounds_and_domains_are_restored_after_the_solve():
    inputs = _program(groups=3)
    node = TransportPortfolioSolve("w", _params(dollar_solve="relax_fix"))
    node.run(None, copy.deepcopy(inputs))
    model = node.build_model(copy.deepcopy(inputs), node.params)
    again = TransportPortfolioSolve("v", _params(dollar_solve="relax_fix"))
    again.run(None, copy.deepcopy(inputs))
    assert all(v.is_integer() for v in model.x.values())
