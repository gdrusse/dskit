"""The priced risk terms of the Kelly local search (ADR-0319): CVaR and reserve concentration in the objective."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from index_options.kelly_local import KellyLocalSearch
from index_options.kelly_multistart import KellyMultiStart
from test_kelly_local import PARAMS, W0, _exact, _program, _scorer

CVAR = {"alpha": .2, "weight": .8}
CONC = {"by": "name", "weight": 3., "power": 2}
LIGHT = {
    "cvar": {"cvar": {"alpha": .2, "weight": .05}},
    "name": {"concentration": {"by": "name", "weight": .3, "power": 2}},
    "event": {"concentration": {"by": "event", "weight": .3, "power": 2}},
    "both": {"cvar": {"alpha": .2, "weight": .05}, "concentration": {"by": "name", "weight": .3, "power": 2},
             "replace_name_charge": True},
}
RISKS = {
    "cvar": {"cvar": CVAR},
    "name": {"concentration": CONC},
    "event": {"concentration": {**CONC, "by": "event"}},
    "both": {"cvar": CVAR, "concentration": CONC, "replace_name_charge": True},
}


def _sampled_program(n_scenarios=40, seed=4, **kw):
    """The synthetic program with ``n_scenarios`` weighted joint scenarios (the shape of a sampled-scenario program)."""
    program = _program(**kw)
    rng = np.random.default_rng(seed)
    weights = rng.random(n_scenarios)+.1
    weights /= weights.sum()
    sizes = {g["id"]: len(g["capacity"]) for g in program["groups"]}
    program["utility"]["joint"] = [
        {"id": f"s{s}", "weight": float(weights[s]),
         "terms": {i: rng.normal(0., 40., n).tolist() for i, n in sizes.items()}} for s in range(n_scenarios)]
    return program


def _search(program, risk=None, **extra):
    params = {**PARAMS, **extra}
    if risk is not None:
        params["risk"] = risk
    return KellyLocalSearch(_scorer(program), params)


def _reserves_by(search, nested):
    """The scorer's own per-group reserves of a book, summed by the declared key (an independent route to ``rtot``)."""
    groups = search.scorer.groups
    reserves = search.scorer.evaluate(nested)["reserves"]
    key = search.conc_by
    aggregate = search.scorer.utility["aggregate"]
    out = {}
    for group, reserve in zip(groups, reserves):
        label = group["id"] if key == "event" else aggregate[group["id"]]
        out[label] = out.get(label, 0.)+reserve
    return out


def _brute_penalty(search, q):
    """The priced terms from first principles: the wealth from ``D`` and the reserves from the scorer."""
    wealth = search.w0+q.astype(float) @ search.D
    out = 0.
    if search.cvar_weight:
        out += search.cvar_weight*max(0., _brute_cvar(wealth, search.weights, search.w0, search.cvar_alpha))
    if search.conc_weight:
        shares = np.array(list(_reserves_by(search, search._nested(q)).values()))/search.w0
        out += search.conc_weight*search.w0*float((shares**search.conc_power).sum())
    return out


def _brute_cvar(wealth, weights, w0, alpha):
    """Rockafellar-Uryasev by definition: ``min_theta theta + E[(L-theta)+]/alpha``, the minimum over the losses."""
    loss = w0-wealth
    return min(t+float(weights @ np.maximum(loss-t, 0.))/alpha for t in loss)


# --- the declaration --------------------------------------------------------------------------------------------


def test_the_risk_declaration_is_validated():
    for good in RISKS.values():
        assert KellyLocalSearch.validate_risk(good) == []
        assert KellyLocalSearch.validate_params({**PARAMS, "risk": good}) == []
    for bad in ({}, [], None, {"extra": 1}, {"cvar": {"alpha": 0., "weight": 1.}}, {"cvar": {"alpha": 1.5, "weight": 1.}},
                {"cvar": {"alpha": .1, "weight": -1.}}, {"cvar": {"alpha": .1}}, {"cvar": {"alpha": True, "weight": 1.}},
                {"concentration": {"by": "sector", "weight": 1., "power": 2}},
                {"concentration": {"by": "name", "weight": -1., "power": 2}},
                {"concentration": {"by": "name", "weight": 1., "power": .5}},
                {"concentration": {"by": "name", "weight": 1.}}, {"replace_name_charge": 1}, {"replace_name_charge": True}, {"cvar": {"alpha": .1, "weight": 1.}, "replace_name_charge": True},
                {"cvar": {"alpha": float("nan"), "weight": 1.}}):
        assert KellyLocalSearch.validate_risk(bad), bad
        assert KellyLocalSearch.validate_params({**PARAMS, "risk": bad}), bad


# --- the key absent is the original search, byte for byte -------------------------------------------------------


def _original_module():
    """The kelly_local.py of the base commit, loaded under another name, as the byte-for-byte reference."""
    here = Path(__file__).resolve().parents[1]
    try:
        text = subprocess.run(["git", "show", "b40a3785:children/index_options/index_options/kelly_local.py"],
                              cwd=here, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("the base commit is not reachable")
    spec = importlib.util.spec_from_loader("kelly_local_base", loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(text, "kelly_local_base.py", "exec"), module.__dict__)
    sys.modules.pop("kelly_local_base", None)
    return module


@pytest.mark.parametrize("seed", [0, 3, 5])
def test_with_the_key_absent_the_search_is_the_base_commits_search_exactly(seed):
    program = _program(n_groups=6, n_pairs=3, seed=seed, capacity=5)
    base = _original_module().KellyLocalSearch(_scorer(program), {**PARAMS, "restarts": 2}).run(None)
    mine = KellyLocalSearch(_scorer(program), {**PARAMS, "restarts": 2}).run(None)
    assert "risk" not in mine["record"]
    assert mine["quantities"] == base["quantities"] and mine["values"] == base["values"]
    assert mine["record"] == base["record"]


def test_a_declaration_that_prices_nothing_changes_no_decision():
    """Zero weights and the name charge kept: the same moves and values, only the record gains its ``risk`` block."""
    program = _program(n_groups=6, n_pairs=3, seed=3, capacity=5)
    plain = _search(program).run(None)
    zero = _search(program, {"cvar": {"alpha": .1, "weight": 0.}, "concentration": {"by": "name", "weight": 0.,
                                                                                    "power": 2}}).run(None)
    assert zero["quantities"] == plain["quantities"] and zero["values"] == plain["values"]
    assert {k: v for k, v in zero["record"].items() if k != "risk"} == plain["record"]
    assert zero["record"]["risk"]["penalty"] == 0.


# --- CVaR against a brute force ---------------------------------------------------------------------------------


@pytest.mark.parametrize("alpha", [.05, .1, .2, .5, 1.])
def test_cvar_of_uniform_scenarios_is_the_mean_of_the_worst_sorted_losses(alpha):
    """Uniform weights, alpha a multiple of 1/S: the mean of the worst alpha S losses, by an explicit sort."""
    program = _sampled_program(n_scenarios=20, n_groups=2, n_pairs=1, seed=1)
    for scenario in program["utility"]["joint"]:
        scenario["weight"] = .05
    search = _search(program, {"cvar": {"alpha": alpha, "weight": 1.}})
    rng = np.random.default_rng(int(alpha*100))
    for _ in range(10):
        wealth = W0+rng.normal(0., 800., search.S)
        worst = np.sort(W0-wealth)[::-1][:round(alpha*search.S)]
        assert search._cvar_tail(wealth)[0] == pytest.approx(float(worst.mean()), rel=1e-12, abs=1e-9)


@pytest.mark.parametrize("alpha", [.03, .07, .2, .33, .9])
def test_cvar_of_weighted_scenarios_matches_the_rockafellar_uryasev_minimum(alpha):
    """Unequal weights, the boundary scenario taken in part: the sort equals the definition's minimum over theta."""
    search = _search(_sampled_program(n_scenarios=40, seed=8), {"cvar": {"alpha": alpha, "weight": 1.}})
    rng = np.random.default_rng(7)
    for _ in range(25):
        wealth = W0+rng.normal(0., 800., search.S)
        tail, weights = search._cvar_tail(wealth)
        assert tail == pytest.approx(_brute_cvar(wealth, search.weights, search.w0, alpha), rel=1e-12, abs=1e-9)
        assert weights.sum() == pytest.approx(1.) and (weights >= 0).all()
        assert float(weights @ (W0-wealth)) == pytest.approx(tail, rel=1e-12, abs=1e-9)
        rows = search._cvar_rows(np.stack([wealth, wealth+50.]))
        assert rows[0] == pytest.approx(tail, rel=1e-12, abs=1e-9)


def test_cvar_handles_tied_losses_and_a_single_scenario():
    search = _search(_program(joint=False), {"cvar": {"alpha": .1, "weight": 1.}})    # one scenario, weight 1
    assert search.S == 1 and search._cvar_tail(np.array([W0-30.]))[0] == pytest.approx(30.)
    many = _search(_sampled_program(n_scenarios=10, seed=2), {"cvar": {"alpha": .25, "weight": 1.}})
    tied = np.full(many.S, W0-5.)
    assert many._cvar_tail(tied)[0] == pytest.approx(5.)


# --- the incremental trial against a full rescoring ------------------------------------------------------------


@pytest.mark.parametrize("risk", list(RISKS))
@pytest.mark.parametrize("sampled", [False, True])
def test_a_trial_equals_a_full_rescoring_and_the_scorers_exact_value_less_the_penalty(risk, sampled):
    program = (_sampled_program(n_groups=5, n_pairs=3, seed=9, capacity=4) if sampled
               else _program(n_groups=5, n_pairs=3, seed=9, capacity=4))
    scorer = _scorer(program)
    search = KellyLocalSearch(scorer, {**PARAMS, "risk": RISKS[risk]})
    rng = np.random.default_rng(2)
    checked = 0
    for _ in range(80):
        q = np.where(rng.random(search.N) < .2, rng.integers(0, 3, search.N), 0)
        if not scorer.check_feasible(search._nested(q))["feasible"]:
            continue
        search._load(q)
        expected = (_exact(scorer, search._nested(q)) + float(search.slopes_true @ search.keytot**2)
                    - float(search.slopes @ search.keytot**2) - _brute_penalty(search, q))
        assert search.value() == pytest.approx(expected, rel=1e-9, abs=1e-8)
        flat = int(rng.integers(search.N))
        other = int(rng.integers(search.N))
        for changes in ({flat: 1}, {flat: -1}, {flat: -1, other: 1} if other != flat else {flat: 1}):
            got = search._trial(changes)
            moved = q.copy()
            for f, dq in changes.items():
                moved[f] += dq
            full = _search(program, RISKS[risk])
            full._load(moved)
            feasible = (moved >= 0).all() and (moved <= full.cap).all() and scorer.check_feasible(
                full._nested(moved))["feasible"]
            assert (got is not None) == bool(feasible)
            if feasible:
                assert got == pytest.approx(full.value(), rel=1e-9, abs=1e-8)
                checked += 1
    assert checked > 20


def test_the_replaced_name_charge_drops_out_and_the_exact_recompute_still_reconciles():
    program = _program(n_groups=6, n_pairs=3, seed=3, capacity=5)
    kept = _search(program, {"cvar": CVAR}).run(None)
    dropped = _search(program, {"cvar": CVAR, "concentration": CONC, "replace_name_charge": True}).run(None)
    risk = dropped["record"]["risk"]
    assert risk["name_charge_applied"] == 0. and risk["name_charge_exact"] > 0.
    assert kept["record"]["risk"]["name_charge_applied"] == kept["record"]["risk"]["name_charge_exact"] > 0.
    # run() already refused a mismatch; restate it from the record and the scorer
    exact = dropped["selection"]["utility"]["exact"]
    assert dropped["record"]["end_value"] == pytest.approx(
        exact+risk["name_charge_exact"]-risk["penalty"], rel=1e-9, abs=1e-8)


# --- the climb with the terms on --------------------------------------------------------------------------------


@pytest.mark.parametrize("risk", list(RISKS))
@pytest.mark.parametrize("sampled", [False, True])
def test_the_climb_never_accepts_a_move_that_lowers_the_penalized_objective(risk, sampled):
    program = (_sampled_program(n_groups=6, n_pairs=3, seed=3, capacity=5) if sampled
               else _program(n_groups=6, n_pairs=3, seed=3, capacity=5))
    search = _search(program, LIGHT[risk])
    search._load(np.zeros(search.N, dtype=np.int64))
    accepted = 0
    for _ in range(PARAMS["moves"]):
        before = search.value()
        found = search._best_move()
        if found is None:
            break
        gain, changes = found
        search._apply(changes)
        fresh = _search(program, LIGHT[risk])
        fresh._load(search.q.copy())            # a full rescoring of the book the move produced
        assert fresh.value() == pytest.approx(search.value(), rel=1e-12, abs=1e-9)
        assert fresh.value() > before
        assert gain == pytest.approx(fresh.value()-before, rel=1e-9, abs=1e-8)
        accepted += 1
    assert accepted > 0
    out = _search(program, LIGHT[risk]).run(None)
    assert all(b > a for a, b in zip(out["values"], out["values"][1:]))
    assert out["record"]["end_value"] >= out["record"]["start_value"]


def test_restarts_cash_floor_and_repair_work_with_the_terms_on():
    program = _sampled_program(n_groups=6, n_pairs=3, seed=3, capacity=5)
    out = _search(program, LIGHT["both"], restarts=2, cash_floor=True, repair=True).run(None)
    assert out["record"]["risk"]["objective"] == pytest.approx(out["record"]["end_value"])
    assert out["record"]["end_value"] >= 0.
    start = [[0]*len(g["capacity"]) for g in _scorer(program).groups]
    start[0][0] = 2
    _search(program, LIGHT["both"], repair=True).run(start)


def test_the_priced_terms_move_the_book_the_way_they_are_priced():
    """A heavy CVaR price cuts the tail loss, a heavy concentration price cuts the largest key's reserve share."""
    program = _sampled_program(n_groups=6, n_pairs=3, seed=3, capacity=5)
    plain = _search(program).run(None)
    cvar = _search(program, {"cvar": {"alpha": .1, "weight": 6.}}).run(None)
    conc = _search(program, {"concentration": {"by": "event", "weight": 40., "power": 2}}).run(None)

    def tail(out):
        search = _search(program, {"cvar": {"alpha": .1, "weight": 1.}})
        search._load(search._flat(out["quantities"]))
        return max(0., search._cvar_tail(search.W)[0])

    def share(out):
        search = _search(program, {"concentration": {"by": "event", "weight": 1., "power": 2}})
        search._load(search._flat(out["quantities"]))
        return float(search.rtot.max())

    assert tail(cvar) <= tail(plain)+1e-9
    assert share(conc) <= share(plain)+1e-9
    assert conc["record"]["end_contracts"] <= plain["record"]["end_contracts"]


def test_the_record_carries_the_term_values():
    program = _sampled_program(n_groups=6, n_pairs=3, seed=3, capacity=5)
    out = _search(program, LIGHT["both"]).run(None)
    risk = out["record"]["risk"]
    assert risk["declared"] == LIGHT["both"]
    assert {"cvar_alpha", "cvar_tail_loss", "cvar_penalty", "concentration_by", "concentration_power",
            "concentration_sum", "concentration_penalty", "reserve_share_max", "reserve_share_max_key",
            "reserve_share_by_key", "penalty", "objective", "objective_unpenalized", "start_penalty",
            "name_charge_exact", "name_charge_applied"} <= set(risk)
    assert risk["penalty"] == pytest.approx(risk["cvar_penalty"]+risk["concentration_penalty"])
    assert risk["cvar_penalty"] == pytest.approx(.05*risk["cvar_tail_loss"])
    assert risk["objective"] == pytest.approx(out["record"]["end_value"])
    assert risk["objective_unpenalized"] == pytest.approx(risk["objective"]+risk["penalty"])
    assert risk["start_penalty"] == 0.
    assert risk["reserve_share_max"] == pytest.approx(max(risk["reserve_share_by_key"].values()))
    import json
    json.dumps(out["record"])       # plain numbers and strings only


# --- the multi-start --------------------------------------------------------------------------------------------


def test_the_multistart_scores_and_records_the_terms_too():
    multi = {"top_groups": 4, "starts": 16, "climbed": 3, "pairs_evaluated": 16, "scales": [1., .5],
             "feasible_starts": {"singles": 4, "units": True, "repair_contracts": 4}}
    program = _sampled_program(n_groups=6, n_pairs=3, seed=3, capacity=5)
    scorer = _scorer(program)
    params = {**PARAMS, "risk": LIGHT["both"]}
    assert KellyMultiStart.validate_multistart(params, multi) == []
    out = KellyMultiStart(scorer, params, multi).run_multistart()
    assert out["record"]["risk"]["objective"] == pytest.approx(out["record"]["end_value"])
    plain = KellyMultiStart(scorer, PARAMS, multi).run_multistart()
    assert "risk" not in plain["record"]
    assert KellyMultiStart.validate_multistart({**PARAMS, "cash_floor": True}, multi)     # still refused
