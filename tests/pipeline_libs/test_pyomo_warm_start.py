"""Opt-in MIP start of a dollar program (stage one of the weekly Kelly local search): accepted, repaired, or cash."""

from __future__ import annotations

import pytest

pytest.importorskip("pyomo")
pytest.importorskip("highspy")

from dskit.pipeline.libs.pyomo import TransportPortfolioSolve  # noqa: E402

PARAMS = {"absolute_gap": 1e-3, "relative_gap": 0., "parity_tolerance": 1e-3, "solver_options": {"threads": 1}}


def _program(upper=1700.):
    """One group, two exclusive items (3 contracts each), a shared reserve cap that binds the better item."""
    group = {"id": "g", "grid": [90., 100., 110.], "masses": [.25, .5, .25], "radius": .01, "scale": 100.,
             "credit": [200., 100.], "losses": [[1000., 500.], [0., 0.], [-1000., -500.]], "capacity": [3, 3],
             "exclusive": [[0, 1]], "reserve_rows": [[800., 400.]], "linear_rows": []}
    return {"groups": [group], "caps": [{"groups": ["g"], "upper": upper}]}


def _run(start=None, **params):
    node = TransportPortfolioSolve("w", {**PARAMS, **params})
    node.warm_quantities = start
    return node, node.run(None, _program())["selection"]


def test_without_a_start_nothing_changes():
    node, selection = _run()
    assert node.last_start is None and selection["quantities"] != [[0, 0]]
    other, again = _run(None)
    assert again["quantities"] == selection["quantities"] and again["value"] == selection["value"]


def test_a_feasible_start_is_completed_accepted_and_never_beaten_by_the_returned_incumbent():
    cold_node, cold = _run()
    node, warm = _run([[1, 0]])
    record = node.last_start
    assert record["used"] is True and record["reason"] is None and record["scale"] == 1.
    assert record["objective"] == pytest.approx(record["scored"], abs=1e-3)
    assert warm["value"] >= record["scored"]-1e-6                     # no worse than the incumbent offered
    assert warm["value"] == pytest.approx(cold["value"], abs=1e-3)    # the same optimum, this program being solved


def test_a_start_the_rows_refuse_is_scaled_down_to_a_feasible_one():
    node, selection = _run([[3, 0]])          # 3 x 800 = 2400 over the 1700 cap; floor(3 x .5) = 1 fits
    record = node.last_start
    assert record["used"] is True and record["scale"] == .5 and record["objective"] > 0
    assert selection["value"] >= record["scored"]-1e-6


def test_an_exclusive_pair_is_netted_to_its_larger_side_and_quantities_are_clipped():
    node, selection = _run([[2, 9]])          # item 1 larger (clipped to 3): item 0 dropped, 3 x 400 = 1200 fits
    assert node.last_start["used"] is True and node.last_start["scale"] == 1.
    assert node.last_start["scored"] == pytest.approx(
        TransportPortfolioSolveScore([[0, 3]])["value"], abs=1e-6)
    assert selection["value"] >= node.last_start["scored"]-1e-6


def TransportPortfolioSolveScore(quantities):
    from dskit.pipeline.libs.pyomo import TransportActionScore
    return TransportActionScore(_program(), {"parity_tolerance": 1e-3}).evaluate(quantities)


@pytest.mark.parametrize("start,reason", [([[0, 0]], "all_zero"), ([[1]], "shape"), ([[1, 0], [1, 0]], "shape"),
                                          ("x", "shape")])
def test_a_start_that_cannot_be_used_leaves_the_cash_start_and_the_same_answer(start, reason):
    _cold_node, cold = _run()
    node, selection = _run(start)
    assert node.last_start["used"] is False and node.last_start["reason"] == reason
    assert selection["quantities"] == cold["quantities"] and selection["value"] == cold["value"]


def test_a_start_whose_completion_is_infeasible_falls_back_to_cash(monkeypatch):
    _cold_node, cold = _run()
    monkeypatch.setattr(TransportPortfolioSolve, "_complete_start", lambda self, model, quantities: False)
    node, selection = _run([[1, 0]])
    assert node.last_start["used"] is False and node.last_start["reason"] == "completion_infeasible"
    assert selection["quantities"] == cold["quantities"] and selection["value"] == cold["value"]


def test_a_start_that_is_too_large_even_scaled_down_falls_back():
    node = TransportPortfolioSolve("w", PARAMS)
    node.warm_quantities = [[3, 0]]
    node.START_SCALES = (1.,)                 # no ladder: the over-cap start is simply refused
    selection = node.run(None, _program())["selection"]
    assert node.last_start["used"] is False and node.last_start["reason"] == "infeasible"
    assert selection["quantities"] == _run()[1]["quantities"]


def test_a_completion_the_solver_refuses_restores_every_variable_to_cash_and_leaves_none_fixed(monkeypatch):
    from pyomo.environ import Var

    seen = []
    build = TransportPortfolioSolve.build_model
    monkeypatch.setattr(TransportPortfolioSolve, "build_model",
                        lambda self, inputs, params: seen.append(build(self, inputs, params)) or seen[-1])

    def refuse(self):
        class Boom:
            options = {}
            def solve(self, model):
                raise RuntimeError("infeasible")
        return Boom()
    resolve = TransportPortfolioSolve._resolve_solver
    calls = {"n": 0}

    def once_refusing(self):
        calls["n"] += 1
        return refuse(self) if calls["n"] == 2 else resolve(self)   # call 1 is run's own solver
    monkeypatch.setattr(TransportPortfolioSolve, "_resolve_solver", once_refusing)
    node = TransportPortfolioSolve("w", PARAMS)
    node.warm_quantities = [[1, 0]]
    node.START_SCALES = (1.,)
    node.run(None, _program())
    assert node.last_start["used"] is False and node.last_start["reason"] == "completion_infeasible"
    model = seen[0]
    assert not any(v.fixed for v in model.component_data_objects(Var))


def test_the_start_works_under_the_return_timeout_policy_and_the_uncertified_audit():
    node, selection = _run([[1, 0]], timeout_policy="return", incumbent_audit="uncertified")
    assert node.last_start["used"] is True and selection["quantities"] == _run()[1]["quantities"]
