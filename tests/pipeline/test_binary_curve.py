"""Pricing a binary from any CDF curve (ADR-0249 part 2).

The equivalence test builds a curve that IS the averaged-lognormal law (window 0) sampled densely
and requires ``CurveBinaryFairValue`` to agree with ``BinaryFairValue`` to 1e-6.
"""

import math

import pytest

from dskit.pipeline import binary_curve
from dskit.pipeline.binary_curve import STATUS_OK, STATUSES, CurveBinaryFairValue, CurveSurvival
from dskit.pipeline.binary_pricing import BinaryFairValue
from dskit.pipeline.conformance import NodeProbe, conformance_suite

PARAMS = {
    "payoff_field": "payoff", "lower_field": "lo", "upper_field": "hi", "curve_key_field": "curve",
    "curve_id_field": "id", "values_field": "log_returns", "probabilities_field": "probabilities",
    "axis": "log_return", "reference_field": "spot", "fair_field": "fair",
}


def phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# -- CurveSurvival ---------------------------------------------------------------------------------


def test_survival_interpolates_linearly_between_level_knots():
    curve = CurveSurvival([90.0, 100.0, 110.0], [0.0, 0.4, 1.0])
    assert curve.survival(100.0) == pytest.approx(0.6)
    assert curve.survival(95.0) == pytest.approx(0.8)    # F = 0.2 halfway up the first cell
    assert curve.survival(107.5) == pytest.approx(0.15)  # F = 0.4 + 0.75 * 0.6 = 0.85
    assert curve(90.0) == pytest.approx(1.0) and curve(110.0) == pytest.approx(0.0)


def test_a_log_return_axis_is_read_against_its_reference_level():
    # OptionPriceCDF's knots are log(K / spot): at spot 200, log 0 is K = 200
    curve = CurveSurvival([-0.1, 0.0, 0.1], [0.0, 0.5, 1.0], axis="log_return", reference=200.0)
    assert curve.survival(200.0) == pytest.approx(0.5)
    assert curve.survival(200.0 * math.exp(0.05)) == pytest.approx(0.25)


def test_outside_the_knot_support_is_refused_never_extrapolated():
    curve = CurveSurvival([90.0, 100.0, 110.0], [0.0, 0.4, 1.0])
    assert not curve.covers(89.0) and not curve.covers(111.0) and curve.covers(90.0)
    with pytest.raises(ValueError, match="outside"):
        curve.survival(111.0)


def test_an_atom_counts_toward_survival_at_its_own_level():
    # a tied knot is a jump: F(100-) = 0.3, F(100) = 0.7, so P(S >= 100) = 1 - 0.3
    curve = CurveSurvival([90.0, 100.0, 100.0, 110.0], [0.0, 0.3, 0.7, 1.0])
    assert curve.survival(100.0) == pytest.approx(0.7)
    assert curve.survival(105.0) == pytest.approx(0.15)


@pytest.mark.parametrize("values, probabilities, kwargs", [
    ([1.0], [0.5], {}),
    ([1.0, 0.5], [0.0, 1.0], {}),
    ([1.0, 2.0], [0.6, 0.4], {}),
    ([1.0, 2.0], [0.0, 1.2], {}),
    ([1.0, float("nan")], [0.0, 1.0], {}),
    ([1.0, 2.0, 3.0], [0.0, 1.0], {}),
    ([0.0, 1.0], [0.0, 1.0], {"axis": "log_return"}),
    ([0.0, 1.0], [0.0, 1.0], {"axis": "log_return", "reference": -1.0}),
    ([0.0, 1.0], [0.0, 1.0], {"axis": "level", "reference": 5.0}),
    ([0.0, 1.0], [0.0, 1.0], {"axis": "sideways"}),
])
def test_a_malformed_curve_is_refused(values, probabilities, kwargs):
    assert CurveSurvival.problems(values, probabilities, **kwargs)
    with pytest.raises(ValueError):
        CurveSurvival(values, probabilities, **kwargs)


# -- the node equals BinaryFairValue when the curve IS the averaged lognormal ------------------------

SIGMA, LAG = 0.01, 5
FAIR = {"vol_field": "rv", "spot_field": "spot", "payoff_field": "payoff", "lower_field": "lo",
        "upper_field": "hi", "decision_field": "d", "settle_field": "s", "fair_field": "fair",
        "exec_lag_s": LAG, "averaging_window_s": 0}


def lognormal_curve(variance, n=40001, width=8.0):
    """The CDF of ln(A / spot) ~ Normal(-v/2, v), sampled on n knots over +-width sd."""
    sd = math.sqrt(variance)
    xs = [-variance / 2 + sd * width * (2 * i / (n - 1) - 1) for i in range(n)]
    return xs, [phi((x + variance / 2) / sd) for x in xs]


def test_curve_fair_value_equals_the_closed_form_on_a_densely_sampled_lognormal():
    contracts = [
        {"payoff": "above", "lo": 101.0, "hi": None},
        {"payoff": "below", "lo": None, "hi": 98.5},
        {"payoff": "between", "lo": 99.0, "hi": 102.0},
        {"payoff": "above", "lo": 100.0, "hi": None},
    ]
    base = [{**c, "rv": SIGMA, "spot": 100.0, "d": 0, "s": 405_000, "curve": "k"} for c in contracts]
    closed = BinaryFairValue("f", FAIR).run(None, {"records": base})["records"]
    variance = closed[0]["fair_var"]
    assert variance == pytest.approx(SIGMA**2 * 400.0)
    xs, ps = lognormal_curve(variance)
    curves = [{"id": "k", "log_returns": xs, "probabilities": ps, "spot": 100.0}]
    priced = CurveBinaryFairValue("c", PARAMS).run(None, {"records": base, "curves": curves})["records"]
    for a, b in zip(closed, priced):
        assert b["fair_status"] == STATUS_OK
        assert b["fair"] == pytest.approx(a["fair"], abs=1e-6)


# -- statuses ------------------------------------------------------------------------------------------

CURVE = {"id": "k", "log_returns": [-0.1, 0.0, 0.1], "probabilities": [0.0, 0.5, 1.0], "spot": 100.0,
         "eligible": True}


def run(contracts, curves=(CURVE,), **overrides):
    return CurveBinaryFairValue("c", {**PARAMS, **overrides}).run(None, {"records": contracts, "curves": list(curves)})


def test_rows_say_why_they_were_not_priced():
    bad = {**CURVE, "id": "bad", "probabilities": [0.0, 0.7, 0.5]}
    off = {**CURVE, "id": "off", "eligible": False}
    out = run([
        {"payoff": "above", "lo": 100.0, "curve": "k"},
        {"payoff": "above", "lo": 100.0, "curve": "missing"},
        {"payoff": "above", "lo": 100.0, "curve": "bad"},
        {"payoff": "sideways", "lo": 100.0, "curve": "k"},
        {"payoff": "between", "lo": 101.0, "hi": 99.0, "curve": "k"},
        {"payoff": "above", "lo": 150.0, "curve": "k"},
        {"payoff": "above", "lo": 100.0, "curve": "off"},
    ], curves=(CURVE, bad, off), eligible_field="eligible")
    got = [r["fair_status"] for r in out["records"]]
    assert got == ["ok", "no_curve", "bad_curve", "unknown_payoff", "bad_bounds", "outside_support",
                   "ineligible_curve"]
    assert out["records"][0]["fair"] == pytest.approx(0.5)
    assert all(r["fair"] is None for r in out["records"][1:])
    assert out["census"]["priced"] == 1 and set(out["census"]["by_status"]) == set(STATUSES)


def test_a_level_axis_needs_no_reference_and_refuses_one():
    level = {"id": "k", "levels": [90.0, 100.0, 110.0], "probabilities": [0.0, 0.4, 1.0]}
    params = {**PARAMS, "values_field": "levels", "axis": "level"}
    params.pop("reference_field")
    out = CurveBinaryFairValue("c", params).run(None, {"records": [{"payoff": "below", "hi": 100.0, "curve": "k"}],
                                                       "curves": [level]})
    assert out["records"][0]["fair"] == pytest.approx(0.4)
    assert any("reference_field" in p for p in CurveBinaryFairValue.validate_params({**params, "reference_field": "s"}))
    assert any("reference_field" in p for p in CurveBinaryFairValue.validate_params({**PARAMS, "reference_field": None}))


def test_duplicate_curve_ids_are_refused_at_the_input():
    node = CurveBinaryFairValue("c", PARAMS)
    assert node.validate_inputs({"records": [], "curves": [CURVE, dict(CURVE)]})
    assert node.validate_inputs({"records": [], "curves": [CURVE]}) == []


@pytest.mark.parametrize("name", sorted(PARAMS))
def test_every_required_knob_is_named_when_missing(name):
    assert any(name in p for p in CurveBinaryFairValue.validate_params({k: v for k, v in PARAMS.items() if k != name}))


def test_unknown_knobs_and_overwrites_are_refused():
    assert any("unknown" in p for p in CurveBinaryFairValue.validate_params({**PARAMS, "extrapolate": True}))
    assert any("overwrite" in p for p in CurveBinaryFairValue.validate_params({**PARAMS, "fair_field": "lo"}))


def test_the_module_declares_its_public_api():
    assert CurveBinaryFairValue.role == "transform"
    assert CurveBinaryFairValue.outputs == ("records", "census")
    assert not [n for n in binary_curve.__all__ if n.startswith("_")]


def _probes(tmp_path):
    return {"curve-binary-fair-value": NodeProbe(
        params=dict(PARAMS), required=tuple(PARAMS),
        inputs={"records": [{"payoff": "above", "lo": 100.0, "curve": "k"}], "curves": [dict(CURVE)]},
        stream_ports=("records", "curves"), runnable=True)}


TestCurveBinaryFairValueConformance = conformance_suite(
    registry=(("curve-binary-fair-value", CurveBinaryFairValue),), module="dskit.pipeline.binary_curve",
    probes=_probes, expected_roles={"curve-binary-fair-value": "transform"},
    name="TestCurveBinaryFairValueConformance")
