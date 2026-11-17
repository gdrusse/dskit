"""The priced risk terms (ADR-0319) on the compact sampled-scenario (atom_scenarios) program: the same as its list form."""

import numpy as np
import pytest

from dskit.pipeline.libs.pyomo import TransportActionScore
from index_options.kelly_local import KellyLocalSearch
from index_options.kelly_multistart import KellyMultiStart
from test_joint_scenarios import PARAMS, _listed, _program

RISK = {"cvar": {"alpha": .05, "weight": .05}, "concentration": {"by": "name", "weight": .3, "power": 2},
        "replace_name_charge": True}


def _search(program, cls=KellyLocalSearch, risk=RISK, **kw):
    return cls(TransportActionScore(program, {"parity_tolerance": 1e-3}), {**PARAMS, "risk": risk}, *kw.values())


def test_the_climb_on_the_compact_form_equals_the_climb_on_its_list_form_with_the_terms_on():
    program = _program()
    a, b = _search(program).run(None), _search(_listed(program)).run(None)
    assert a["quantities"] == b["quantities"] and a["record"]["moves"] == b["record"]["moves"] > 0
    assert a["record"]["end_value"] == pytest.approx(b["record"]["end_value"], rel=1e-10)
    assert a["record"]["risk"]["cvar_tail_loss"] == pytest.approx(b["record"]["risk"]["cvar_tail_loss"], rel=1e-9)
    assert a["record"]["risk"]["concentration_penalty"] == pytest.approx(
        b["record"]["risk"]["concentration_penalty"], rel=1e-9)
    assert a["record"]["end_contracts"] > 0


def test_a_trial_equals_a_full_rescoring_on_the_compact_form():
    program = _program(seed=3)
    search = _search(program)
    rng = np.random.default_rng(5)
    checked = 0
    for _ in range(60):
        q = np.where(rng.random(search.N) < .2, rng.integers(0, 3, search.N), 0)
        if not search.scorer.check_feasible(search._nested(q))["feasible"]:
            continue
        search._load(q)
        flat = int(rng.integers(search.N))
        got = search._trial({flat: 1})
        moved = q.copy()
        moved[flat] += 1
        fresh = _search(program)
        fresh._load(moved)
        if got is not None:
            assert got == pytest.approx(fresh.value(), rel=1e-9, abs=1e-8)
            checked += 1
    assert checked > 10


def test_the_climb_never_lowers_the_penalized_objective_on_the_compact_form():
    program = _program(seed=1)
    search = _search(program)
    search._load(np.zeros(search.N, dtype=np.int64))
    moves = 0
    for _ in range(PARAMS["moves"]):
        before = search.value()
        found = search._best_move()
        if found is None:
            break
        search._apply(found[1])
        fresh = _search(program)
        fresh._load(search.q.copy())
        assert fresh.value() == pytest.approx(search.value(), rel=1e-10, abs=1e-8) and fresh.value() > before
        moves += 1
    assert moves > 0


def test_the_multistart_with_the_terms_on_matches_between_the_two_forms():
    from test_kelly_multistart import MULTI2

    program = _program(seed=2)
    a = _search(program, KellyMultiStart, multi=MULTI2).run_multistart()
    b = _search(_listed(program), KellyMultiStart, multi=MULTI2).run_multistart()
    assert a["quantities"] == b["quantities"]
    assert a["record"]["risk"]["objective"] == pytest.approx(a["record"]["end_value"])
