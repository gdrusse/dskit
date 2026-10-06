"""The baseline fair value: a driftless lognormal settled on a 60-second average.

Every number below is a closed form or a hand calculation, written down here
independently of the code (an assertion read from its subject asserts nothing).
"""

import math

import pytest

from crypto_trading.fair_value import AveragedLognormal, FairValue
from crypto_trading.payoffs import PAYOFFS, payoff

SIGMA, TAU, WINDOW = 0.01, 400.0, 60.0  # per sqrt(s), seconds, seconds


def phi(x):
    """Standard normal CDF, restated here via erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def test_atm_without_averaging_is_the_known_normal_value():
    model = AveragedLognormal(SIGMA, TAU, 0.0)
    assert model.variance == pytest.approx(0.04)
    # sigma*sqrt(tau) = 0.2, martingale drift: d = -0.1, and Phi(-0.1) = 0.46017216272
    assert model.survival(100.0, 100.0) == pytest.approx(0.46017216272, abs=1e-9)


def test_averaging_window_removes_two_thirds_of_its_own_point_variance():
    plain = AveragedLognormal(SIGMA, TAU, 0.0).variance
    averaged = AveragedLognormal(SIGMA, TAU, WINDOW).variance
    assert averaged == pytest.approx(plain - (2.0 / 3.0) * WINDOW * SIGMA**2)
    # the same statement as "unaveraged lead-in plus one third of the window"
    assert averaged == pytest.approx(SIGMA**2 * (TAU - WINDOW) + SIGMA**2 * WINDOW / 3.0)


def test_a_decision_exactly_at_the_window_start_is_priceable():
    # tau == window: nothing of the average is observed yet, the variance is window/3 sigma^2
    assert AveragedLognormal(SIGMA, WINDOW, WINDOW).variance == pytest.approx(SIGMA**2 * WINDOW / 3.0)


def test_the_continuous_variance_matches_a_discrete_sixty_print_average():
    # BRTI averages 60 one-second prints t = tau-59 .. tau. For Brownian motion the
    # exact variance of that mean is sum_ij min(t_i, t_j) / 60^2.
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


@pytest.mark.parametrize("sigma, tau, window", [
    (0.0, 400.0, 60.0), (-0.01, 400.0, 60.0), (0.01, 30.0, 60.0),
    (float("nan"), 400.0, 60.0), (0.01, 400.0, -1.0), (0.01, 0.0, 0.0)])
def test_a_degenerate_model_refuses_to_construct(sigma, tau, window):
    with pytest.raises(ValueError):
        AveragedLognormal(sigma, tau, window)


def test_above_below_between_are_one_distribution():
    model = AveragedLognormal(SIGMA, TAU, WINDOW)

    def survival(strike):
        return model.survival(100.0, strike)

    above = payoff("above").yes_probability(survival, floor=101.0, cap=None)
    below = payoff("below").yes_probability(survival, floor=None, cap=101.0)
    between = payoff("between").yes_probability(survival, floor=99.0, cap=101.0)
    assert above + below == pytest.approx(1.0)
    assert between == pytest.approx(survival(99.0) - survival(101.0))
    assert 0.0 < between < 1.0


def test_the_payoff_vocabulary_is_closed_and_names_its_strikes():
    assert set(PAYOFFS) == {"above", "below", "between"}
    assert PAYOFFS["above"].strike_fields == ("floor_strike",)
    assert PAYOFFS["below"].strike_fields == ("cap_strike",)
    assert PAYOFFS["between"].strike_fields == ("floor_strike", "cap_strike")
    with pytest.raises(ValueError, match="mystery"):
        payoff("mystery")


# -- the node --------------------------------------------------------------------

PARAMS = {"vol_field": "rv", "fair_field": "fair", "spot_field": "spot", "averaging_window_s": 60}


def row(**over):
    base = {"ticker": "T", "payoff": "above", "floor_strike": 100.0, "cap_strike": None,
            "spot": 100.0, "tau_s": TAU, "rv": SIGMA}
    base.update(over)
    return base


def run(rows, **params):
    node = FairValue("fair", {**PARAMS, **params})
    return node.run(None, {"records": rows})["records"]


def test_node_writes_the_fair_value_beside_its_variance_and_status():
    out = run([row()])[0]
    assert out["fair"] == pytest.approx(
        AveragedLognormal(SIGMA, TAU, 60.0).survival(100.0, 100.0))
    assert out["fair_var"] == pytest.approx(SIGMA**2 * (TAU - 40.0))
    assert out["fair_status"] == "ok"
    assert out["ticker"] == "T", "every other field rides through"


def test_node_handles_all_three_payoffs_and_does_not_mutate_its_input():
    rows = [row(), row(payoff="below", floor_strike=None, cap_strike=101.0),
            row(payoff="between", floor_strike=99.0, cap_strike=101.0)]
    before = [dict(r) for r in rows]
    out = run(rows)
    assert rows == before
    model = AveragedLognormal(SIGMA, TAU, 60.0)
    assert out[1]["fair"] == pytest.approx(1.0 - model.survival(100.0, 101.0))
    assert out[2]["fair"] == pytest.approx(model.survival(100.0, 99.0) - model.survival(100.0, 101.0))


@pytest.mark.parametrize("over, status", [
    ({"spot": None}, "no_spot"), ({"rv": None}, "no_vol"), ({"rv": 0.0}, "no_vol"),
    ({"tau_s": 59.0}, "tau_inside_window"), ({"tau_s": 30.0}, "tau_inside_window")])
def test_node_marks_what_it_cannot_price_and_never_guesses(over, status):
    out = run([row(**over)])[0]
    assert out["fair"] is None and out["fair_status"] == status
    assert out["fair_var"] is None


def test_node_refuses_unknown_and_missing_params():
    with pytest.raises(Exception, match="surprise"):
        FairValue("fair", {**PARAMS, "surprise": 1})
    for knob in PARAMS:
        with pytest.raises(Exception, match=knob):
            FairValue("fair", {k: v for k, v in PARAMS.items() if k != knob})
    with pytest.raises(Exception, match="averaging_window_s"):
        FairValue("fair", {**PARAMS, "averaging_window_s": -5})


def test_window_length_is_a_param_not_a_literal():
    wide = run([row(tau_s=400.0)], averaging_window_s=120)[0]
    assert wide["fair_var"] == pytest.approx(SIGMA**2 * (400.0 - 80.0))


def test_the_spot_column_is_a_param_so_a_unit_corrected_spot_can_be_priced():
    # the same strike against the raw and the index-unit spot prices differently; neither is a literal
    raw = run([row(spot=100.0, spot_brti=99.96)], spot_field="spot")[0]["fair"]
    adjusted = run([row(spot=100.0, spot_brti=99.96)], spot_field="spot_brti")[0]["fair"]
    model = AveragedLognormal(SIGMA, TAU, 60.0)
    assert raw == pytest.approx(model.survival(100.0, 100.0))
    assert adjusted == pytest.approx(model.survival(99.96, 100.0)) and adjusted < raw
    missing = run([row(spot_brti=None)], spot_field="spot_brti")[0]
    assert missing["fair"] is None and missing["fair_status"] == "no_spot"
