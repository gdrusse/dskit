"""Binary-contract fair value on a settlement average: the law, the payoff geometries, the node.

Every number below is a closed form or a hand calculation written down here independently of the
code (an assertion read from its subject asserts nothing). The pricer is generic: a contract that
pays 1 when the average of a price over the last ``window`` seconds before a settlement instant
lands in a region.
"""

import math

import pytest

from dskit.pipeline import binary_pricing
from dskit.pipeline.base import ConfigError
from dskit.pipeline.binary_pricing import (
    PAYOFFS,
    STATUS_OK,
    STATUSES,
    AveragedLognormal,
    BinaryFairValue,
    BinaryPayoff,
    payoff,
)

SIGMA, TAU, WINDOW = 0.01, 400.0, 60.0  # per sqrt(second), seconds, seconds


def phi(x):
    """Standard normal CDF, restated here via erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# -- the law ---------------------------------------------------------------------------------


def test_atm_without_averaging_is_the_known_normal_value():
    model = AveragedLognormal(SIGMA, TAU, 0.0)
    assert model.variance == pytest.approx(0.04)
    # sigma * sqrt(tau) = 0.2, martingale drift: d = -0.1, and Phi(-0.1) = 0.46017216272
    assert model.survival(100.0, 100.0) == pytest.approx(0.46017216272, abs=1e-9)


def test_averaging_window_removes_two_thirds_of_its_own_point_variance():
    plain = AveragedLognormal(SIGMA, TAU, 0.0).variance
    averaged = AveragedLognormal(SIGMA, TAU, WINDOW).variance
    assert averaged == pytest.approx(plain - (2.0 / 3.0) * WINDOW * SIGMA**2)
    # the same statement as "unaveraged lead-in plus one third of the window"
    assert averaged == pytest.approx(SIGMA**2 * (TAU - WINDOW) + SIGMA**2 * WINDOW / 3.0)
    assert averaged == pytest.approx(SIGMA**2 * (TAU - 2.0 * WINDOW / 3.0))


def test_a_horizon_exactly_at_the_window_start_is_priceable():
    # tau == window: nothing of the average is observed yet, the variance is window / 3 * sigma^2
    assert AveragedLognormal(SIGMA, WINDOW, WINDOW).variance == pytest.approx(SIGMA**2 * WINDOW / 3.0)


def test_the_continuous_variance_matches_a_discrete_sixty_print_average():
    # 60 one-second prints t = tau-59 .. tau. For Brownian motion the exact variance of that mean
    # is sum_ij min(t_i, t_j) / 60^2, which the continuous tau - 2W/3 matches to 0.2 percent.
    prints = [TAU - 60.0 + i for i in range(1, 61)]
    exact = sum(min(a, b) for a in prints for b in prints) / 60.0**2
    assert exact == pytest.approx(TAU - 2.0 * WINDOW / 3.0, rel=2e-3)


def test_survival_at_a_strike_matches_the_closed_form():
    model = AveragedLognormal(SIGMA, TAU, WINDOW)
    v = SIGMA**2 * (TAU - 2.0 * WINDOW / 3.0)
    strike = 100.0 * math.exp(0.1)
    d = (math.log(100.0 / strike) - v / 2.0) / math.sqrt(v)
    assert model.survival(100.0, strike) == pytest.approx(phi(d), abs=1e-12)
    assert model.survival(100.0, strike) < model.survival(100.0, 100.0)


def test_survival_falls_as_the_strike_rises_and_stays_a_probability():
    model = AveragedLognormal(SIGMA, TAU, WINDOW)
    values = [model.survival(100.0, k) for k in (50.0, 90.0, 99.0, 100.0, 101.0, 110.0, 200.0)]
    assert values == sorted(values, reverse=True)
    assert all(0.0 <= v <= 1.0 for v in values)


def test_the_model_is_a_martingale_so_the_mean_of_the_average_is_the_spot():
    # E[A] = S0 pins the lognormal location -v/2: integrate the survival function, E[A] = int_0^inf P(A >= k) dk
    model = AveragedLognormal(SIGMA, TAU, WINDOW)
    step = 0.01
    total = sum(model.survival(100.0, step * (i + 0.5)) * step for i in range(0, 30_000))
    assert total == pytest.approx(100.0, rel=1e-3)


@pytest.mark.parametrize("sigma, tau, window", [
    (0.0, 400.0, 60.0), (-0.01, 400.0, 60.0), (0.01, 30.0, 60.0),
    (float("nan"), 400.0, 60.0), (float("inf"), 400.0, 60.0), (0.01, 400.0, -1.0),
    (0.01, 0.0, 0.0), (True, 400.0, 60.0), (None, 400.0, 60.0), (0.01, 400.0, None)])
def test_a_degenerate_model_refuses_to_construct(sigma, tau, window):
    with pytest.raises(ValueError):
        AveragedLognormal(sigma, tau, window)


@pytest.mark.parametrize("spot, strike", [(0.0, 100.0), (100.0, 0.0), (-1.0, 100.0), (None, 100.0),
                                           (100.0, float("nan")), (100.0, True)])
def test_survival_refuses_a_spot_or_strike_that_is_not_a_positive_number(spot, strike):
    with pytest.raises(ValueError):
        AveragedLognormal(SIGMA, TAU, WINDOW).survival(spot, strike)


# -- the payoff geometries ---------------------------------------------------------------------


def test_above_below_between_are_one_distribution():
    model = AveragedLognormal(SIGMA, TAU, WINDOW)

    def survival(strike):
        return model.survival(100.0, strike)

    above = payoff("above").yes_probability(survival, lower=101.0, upper=None)
    below = payoff("below").yes_probability(survival, lower=None, upper=101.0)
    between = payoff("between").yes_probability(survival, lower=99.0, upper=101.0)
    assert above + below == pytest.approx(1.0)
    assert between == pytest.approx(survival(99.0) - survival(101.0))
    assert 0.0 < between < 1.0


def test_a_geometry_reads_only_the_bounds_it_names():
    calls = []

    def survival(strike):
        calls.append(strike)
        return 0.5

    payoff("above").yes_probability(survival, lower=10.0, upper=None)
    payoff("below").yes_probability(survival, lower=None, upper=20.0)
    payoff("between").yes_probability(survival, lower=10.0, upper=20.0)
    assert calls == [10.0, 20.0, 10.0, 20.0]


def test_the_payoff_vocabulary_is_closed_and_names_its_bounds():
    assert set(PAYOFFS) == {"above", "below", "between"}
    assert PAYOFFS["above"].bounds == ("lower",)
    assert PAYOFFS["below"].bounds == ("upper",)
    assert PAYOFFS["between"].bounds == ("lower", "upper")
    with pytest.raises(ValueError, match="mystery"):
        payoff("mystery")
    with pytest.raises(ValueError):
        payoff(None)
    with pytest.raises(TypeError):
        PAYOFFS["mine"] = PAYOFFS["above"]


def test_a_missing_or_unordered_bound_is_refused_by_the_geometry():
    def survival(strike):
        return 0.5

    for name, lower, upper in (("above", None, 5.0), ("below", 5.0, None), ("between", 5.0, None),
                               ("between", None, 5.0), ("above", float("nan"), None),
                               ("between", 9.0, 5.0)):
        with pytest.raises(ValueError):
            payoff(name).yes_probability(survival, lower, upper)
    # a degenerate range is empty, not an error: lower == upper pays on no value
    assert payoff("between").yes_probability(survival, 5.0, 5.0) == 0.0


def test_bounds_problem_is_the_one_rule_the_node_and_the_geometry_share():
    assert PAYOFFS["above"].bounds_problem(1.0, None) is None
    assert PAYOFFS["between"].bounds_problem(1.0, 2.0) is None
    assert "lower" in PAYOFFS["above"].bounds_problem(None, 2.0)
    assert PAYOFFS["between"].bounds_problem(2.0, 1.0)


def test_binary_payoff_is_abstract_and_a_new_geometry_is_a_subclass():
    with pytest.raises(TypeError):
        BinaryPayoff()

    class Outside(BinaryPayoff):
        name = "outside"
        bounds = ("lower", "upper")

        def _probability(self, survival, lower, upper):
            return 1.0 - (survival(lower) - survival(upper))

    assert Outside().yes_probability(lambda k: 0.6 if k < 100 else 0.3, 99.0, 101.0) == pytest.approx(0.7)
    with pytest.raises(ValueError):
        Outside().yes_probability(lambda k: 0.5, 99.0, None)


# -- the node ----------------------------------------------------------------------------------

SETTLE = 1_800_000_000_000  # an arbitrary settlement instant, epoch ms
LAG_S = 5
PARAMS = {"vol_field": "rv", "spot_field": "spot", "fair_field": "fair", "payoff_field": "payoff",
          "lower_field": "lo", "upper_field": "hi", "decision_field": "decision_ms",
          "settle_field": "settle_ms", "exec_lag_s": LAG_S, "averaging_window_s": 60}


def decision_for(tau_s, lag_s=LAG_S):
    """The information instant that puts a fill ``lag_s`` later exactly ``tau_s`` before settlement."""
    return SETTLE - round((tau_s + lag_s) * 1000)


def row(tau_s=TAU, **over):
    base = {"id": "T", "payoff": "above", "lo": 100.0, "hi": None, "spot": 100.0, "rv": SIGMA,
            "decision_ms": decision_for(tau_s), "settle_ms": SETTLE}
    base.update(over)
    return base


def run(rows, **params):
    node = BinaryFairValue("fair", {**PARAMS, **params})
    return node.run(None, {"records": rows})["records"]


def test_node_writes_the_fair_value_beside_its_variance_horizon_and_status():
    out = run([row()])[0]
    assert out["fair"] == pytest.approx(AveragedLognormal(SIGMA, TAU, 60.0).survival(100.0, 100.0))
    assert out["fair_var"] == pytest.approx(SIGMA**2 * (TAU - 40.0))
    assert out["fair_tau"] == pytest.approx(TAU)
    assert out["fair_status"] == STATUS_OK == "ok"
    assert out["id"] == "T", "every other field rides through"


def test_the_horizon_runs_from_the_fill_not_from_the_information_instant():
    # one information instant, three declared fill latencies: the horizon shrinks by exactly the lag
    info = decision_for(TAU, lag_s=5)  # 405 s before settlement
    for lag, tau in ((5, 400.0), (15, 390.0), (0.5, 404.5)):
        out = run([row(decision_ms=info)], exec_lag_s=lag)[0]
        assert out["fair_tau"] == pytest.approx(tau)
        assert out["fair_var"] == pytest.approx(SIGMA**2 * (tau - 40.0))


def test_a_fill_at_or_after_settlement_is_marked_never_priced_and_the_census_lists_it():
    # the information instant is 5 s before settlement, so a 5 s lag fills AT settlement, 9 s lag after it
    at = row(decision_ms=SETTLE - 5000)
    after = row(decision_ms=SETTLE - 1000, id="U")
    ok = row(id="V")
    node = BinaryFairValue("fair", PARAMS)
    out = node.run(None, {"records": [at, after, ok]})
    assert [r["fair_status"] for r in out["records"]] == ["exec_not_before_settle"] * 2 + ["ok"]
    assert out["records"][0]["fair"] is None and out["records"][0]["fair_var"] is None
    assert out["records"][0]["fair_tau"] == pytest.approx(0.0)
    assert out["records"][1]["fair_tau"] == pytest.approx(-4.0), "the diagnosis is visible"
    assert out["census"]["rows"] == 3 and out["census"]["priced"] == 1
    assert out["census"]["by_status"]["exec_not_before_settle"] == 2
    assert set(out["census"]["by_status"]) == set(STATUSES)


def test_node_handles_all_three_payoffs_and_does_not_mutate_its_input():
    rows = [row(), row(payoff="below", lo=None, hi=101.0), row(payoff="between", lo=99.0, hi=101.0)]
    before = [dict(r) for r in rows]
    out = run(rows)
    assert rows == before
    model = AveragedLognormal(SIGMA, TAU, 60.0)
    assert out[1]["fair"] == pytest.approx(1.0 - model.survival(100.0, 101.0))
    assert out[2]["fair"] == pytest.approx(model.survival(100.0, 99.0) - model.survival(100.0, 101.0))


@pytest.mark.parametrize("over, status", [
    ({"spot": None}, "no_spot"), ({"spot": 0.0}, "no_spot"), ({"spot": float("nan")}, "no_spot"),
    ({"rv": None}, "no_vol"), ({"rv": 0.0}, "no_vol"), ({"rv": -0.1}, "no_vol"),
    ({"decision_ms": None}, "no_instant"), ({"settle_ms": None}, "no_instant"),
    ({"decision_ms": "x"}, "no_instant"),
    ({"decision_ms": decision_for(59.0)}, "tau_inside_window"),
    ({"decision_ms": decision_for(30.0)}, "tau_inside_window"),
    ({"payoff": "mystery"}, "unknown_payoff"), ({"payoff": None}, "unknown_payoff"),
    ({"lo": None}, "bad_bounds"), ({"lo": 0.0}, "bad_bounds"), ({"lo": -5.0}, "bad_bounds"),
    ({"lo": float("nan")}, "bad_bounds"),
    ({"payoff": "below", "lo": None, "hi": None}, "bad_bounds"),
    ({"payoff": "between", "lo": 101.0, "hi": 99.0}, "bad_bounds"),
    ({"payoff": "between", "lo": 99.0, "hi": None}, "bad_bounds")])
def test_node_marks_what_it_cannot_price_and_never_guesses(over, status):
    out = run([row(**over)])[0]
    assert out["fair"] is None and out["fair_status"] == status
    assert out["fair_var"] is None


def test_a_horizon_exactly_the_window_is_priced_and_the_step_inside_is_not():
    on = run([row(decision_ms=decision_for(60.0))])[0]
    assert on["fair_status"] == "ok" and on["fair_var"] == pytest.approx(SIGMA**2 * 20.0)
    inside = run([row(decision_ms=decision_for(59.999))])[0]
    assert inside["fair_status"] == "tau_inside_window"


def test_node_refuses_unknown_missing_and_unusable_params():
    with pytest.raises(ConfigError, match="surprise"):
        BinaryFairValue("fair", {**PARAMS, "surprise": 1})
    for knob in PARAMS:
        with pytest.raises(ConfigError, match=knob):
            BinaryFairValue("fair", {k: v for k, v in PARAMS.items() if k != knob})
    for knob, bad in (("averaging_window_s", -5), ("averaging_window_s", True), ("exec_lag_s", 0),
                      ("exec_lag_s", -1), ("exec_lag_s", 0.0004), ("exec_lag_s", True),
                      ("exec_lag_s", None), ("vol_field", ""), ("fair_field", 3)):
        with pytest.raises(ConfigError, match=knob):
            BinaryFairValue("fair", {**PARAMS, knob: bad})
    BinaryFairValue("fair", {**PARAMS, "exec_lag_s": 0.001, "averaging_window_s": 0})


def test_the_output_column_may_not_overwrite_an_input_field_the_node_reads():
    for knob in ("vol_field", "spot_field", "payoff_field", "lower_field", "upper_field",
                 "decision_field", "settle_field"):
        with pytest.raises(ConfigError, match="fair_field"):
            BinaryFairValue("fair", {**PARAMS, "fair_field": PARAMS[knob]})


def test_window_length_is_a_param_not_a_literal():
    wide = run([row()], averaging_window_s=120)[0]
    assert wide["fair_var"] == pytest.approx(SIGMA**2 * (400.0 - 80.0))
    none = run([row()], averaging_window_s=0)[0]
    assert none["fair_var"] == pytest.approx(SIGMA**2 * 400.0)


def test_the_spot_column_is_a_param_so_a_unit_corrected_spot_can_be_priced():
    # the same strike against two spots prices differently; neither column name is a literal
    raw = run([row(adjusted=99.96)], spot_field="spot")[0]["fair"]
    adjusted = run([row(adjusted=99.96)], spot_field="adjusted")[0]["fair"]
    model = AveragedLognormal(SIGMA, TAU, 60.0)
    assert raw == pytest.approx(model.survival(100.0, 100.0))
    assert adjusted == pytest.approx(model.survival(99.96, 100.0)) and adjusted < raw
    assert run([row(adjusted=None)], spot_field="adjusted")[0]["fair_status"] == "no_spot"


def test_two_models_priced_in_turn_keep_their_own_columns():
    rows = run([row()], fair_field="fair_a")
    rows = BinaryFairValue("b", {**PARAMS, "fair_field": "fair_b", "vol_field": "rv2"}).run(
        None, {"records": [{**r, "rv2": 0.02} for r in rows]})["records"]
    assert {"fair_a", "fair_b", "fair_a_var", "fair_b_var"} <= set(rows[0])
    assert rows[0]["fair_b"] != rows[0]["fair_a"]


def test_node_declares_its_contract():
    assert BinaryFairValue.role == "transform"
    assert BinaryFairValue.outputs == ("records", "census")
    assert BinaryFairValue.serving_effect(PARAMS, {}) == "pure"
    node = BinaryFairValue("fair", PARAMS)
    assert node.validate_inputs({"records": [row()]}) == []
    assert node.validate_inputs({"records": iter([row()])})
    assert node.validate_inputs({})


def test_the_module_declares_its_public_api_and_leaks_no_underscore_name():
    assert not [n for n in binary_pricing.__all__ if n.startswith("_")]
    assert {"AveragedLognormal", "BinaryFairValue", "BinaryPayoff", "PAYOFFS", "payoff"} <= set(binary_pricing.__all__)
