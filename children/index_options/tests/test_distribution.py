"""Standardized condor geometry and its report node (ADR-0168)."""

import math
from decimal import Decimal
from types import SimpleNamespace

import pytest

from dskit.pipeline.node import ConfigError

from index_options.contracts import leg_intrinsic
from index_options.distribution import CondorGeometry, strike_z
from index_options.nodes import CondorDistributionReport

GEOM = ([-2.0, -1.0, 1.0, 2.0], 0.3, 0.95)


def test_leg_intrinsic_owns_both_numeric_types():
    assert leg_intrinsic("put", 100.0, 90.0) == 10.0 and leg_intrinsic("put", 100.0, 110.0) == 0.0
    assert leg_intrinsic("call", Decimal("100"), Decimal("104.5")) == Decimal("4.5")
    assert leg_intrinsic("call", Decimal("100"), Decimal("90")) == Decimal("0")
    out_of_money = leg_intrinsic("put", Decimal("100"), Decimal("104.25"))
    assert isinstance(out_of_money, Decimal) and str(out_of_money) == "0"


def test_strike_z_inverts_the_geometry():
    geometry = CondorGeometry(*GEOM)
    strikes = geometry.strikes(100.0, 0.05)
    assert [strike_z(k, 100.0, 0.05) for k in strikes] == pytest.approx(list(GEOM[0]))
    with pytest.raises(ValueError):
        strike_z(-1.0, 100.0, 0.05)


def test_pnl_regions_match_the_condor_payoff():
    geometry = CondorGeometry(*GEOM)
    strikes = geometry.strikes(100.0, 0.05)
    narrower = min(strikes[1] - strikes[0], strikes[3] - strikes[2])
    assert geometry.pnl_per_width(100.0, strikes) == pytest.approx(0.3)
    put_loss = geometry.pnl_per_width(strikes[0] - 5, strikes)
    assert put_loss == pytest.approx(0.3 - (strikes[1] - strikes[0]) / narrower)
    mid = (strikes[0] + strikes[1]) / 2
    assert geometry.pnl_per_width(mid, strikes) == pytest.approx(
        0.3 - (strikes[1] - mid) / narrower)


def test_evaluate_probabilities_and_realized_outcome():
    geometry = CondorGeometry(*GEOM)
    draws = [-3.0, -1.5, 0.0, 0.5, 1.5, 3.0] + [0.0] * 4
    out = geometry.evaluate(draws, 0.2, 100.0, 0.05)
    assert out["p_full_credit"] == pytest.approx(0.6)
    assert out["p_beyond_wings"] == pytest.approx(0.2)
    assert out["realized_pnl"] == pytest.approx(0.3)
    assert out["cvar"] <= out["expected_pnl"] <= 0.3
    assert geometry.evaluate(draws, None, 100.0, 0.05)["realized_pnl"] is None


def test_region_bounds_are_inclusive_at_the_strikes():
    out = CondorGeometry(*GEOM).evaluate([-2.0, 2.0, 0.0, -1.0], None, 100.0, 0.05)
    assert out["p_beyond_wings"] == pytest.approx(0.5)
    assert out["p_full_credit"] == pytest.approx(0.5)


def test_evaluate_prices_an_asymmetric_forecast_on_the_right_tail():
    geometry = CondorGeometry(*GEOM)
    draws = [-2.5, -1.5, 0.0, 0.0]
    out = geometry.evaluate(draws, None, 100.0, 0.05)
    k = geometry.strikes(100.0, 0.05)
    narrower = min(k[1] - k[0], k[3] - k[2])
    lo = 100.0 * math.exp(0.05 * -1.5)
    pnls = [0.3 - (k[1] - k[0]) / narrower, 0.3 - (k[1] - lo) / narrower, 0.3, 0.3]
    assert out["expected_pnl"] == pytest.approx(sum(pnls) / 4)
    assert out["cvar"] == pytest.approx(pnls[0])


def test_cvar_counts_the_tail_exactly_at_the_shipped_defaults():
    geometry = CondorGeometry(GEOM[0], 0.3, 0.95)
    assert geometry.cvar(list(range(200))) == pytest.approx(4.5)  # worst 10, not 11


@pytest.mark.parametrize("field,value", [
    ("close", float("nan")), ("close", 0.0), ("reference_scale", 0.0),
    ("reference_scale", -0.05), ("reference_scale", None),
])
def test_report_node_skips_a_row_without_a_positive_forward_and_scale(field, value):
    rows = _rows()
    rows[11][field] = value
    out = CondorDistributionReport("condor", dict(PARAMS)).run(
        SimpleNamespace(splits=_Split()), {"forecasts": rows})
    assert out["metrics"]["n"] == 9 and out["metrics"]["n_skipped_unscorable"] == 2


def test_report_node_skips_a_nan_outcome_like_the_scorer():
    rows = _rows()
    rows[11]["outcome"] = float("nan")
    out = CondorDistributionReport("condor", dict(PARAMS)).run(
        SimpleNamespace(splits=_Split()), {"forecasts": rows})
    assert out["metrics"]["n"] == 9 and out["metrics"]["n_skipped_unscorable"] == 2


def test_cvar_is_the_worst_tail_mean():
    geometry = CondorGeometry([-2.0, -1.0, 1.0, 2.0], 0.3, 0.8)
    assert geometry.cvar([5, 1, 2, 3, 4, -1, 0, 6, 7, 8]) == pytest.approx(-0.5)


@pytest.mark.parametrize("args", [
    ([-1.0, -2.0, 1.0, 2.0], 0.3, 0.95), ([-2.0, -1.0, 1.0], 0.3, 0.95),
    ([-2.0, -1.0, 1.0, 2.0], 1.0, 0.95), ([-2.0, -1.0, 1.0, 2.0], 0.3, 1.0),
    ([-2.0, -1.0, 1.0, float("nan")], 0.3, 0.95),
])
def test_invalid_geometry_refuses(args):
    with pytest.raises(ValueError):
        CondorGeometry(*args)


class _Split:
    def split_of(self, frame):
        return "val" if frame.asof_ms >= 10 else "train"


def _rows():
    return [{"asof_ms": i, "contract": f"C{i}", "close": 100.0, "reference_scale": 0.05,
             "samples": [-1.5, 0.0, 0.5, 2.5], "outcome": 0.0 if i % 2 else 3.0}
            for i in range(20)] + [{"asof_ms": 30, "contract": "x", "close": 100.0,
                                    "reference_scale": 0.05, "samples": [0.0], "outcome": None}]


PARAMS = {"split": "val", "strikes_z": GEOM[0], "credit_fraction": 0.3}


def test_report_node_aggregates_the_split():
    out = CondorDistributionReport("condor", dict(PARAMS)).run(
        SimpleNamespace(splits=_Split()), {"forecasts": _rows()})
    metrics = out["metrics"]
    assert metrics["n"] == 10 and metrics["n_skipped_unscorable"] == 1
    assert metrics["forecast_p_full_credit"] == pytest.approx(0.5)
    assert metrics["forecast_p_beyond_wings"] == pytest.approx(0.25)
    assert metrics["realized_full_credit_rate"] == pytest.approx(0.5)
    k = [100.0 * math.exp(0.05 * z) for z in GEOM[0]]
    call_loss = 0.3 - (k[3] - k[2]) / min(k[1] - k[0], k[3] - k[2])
    assert metrics["realized_cvar"] == pytest.approx(call_loss)
    assert metrics["realized_mean_pnl"] == pytest.approx((0.3 + call_loss) / 2)
    assert out["report"].value["decision_eligible"] is False
    assert all(math.isfinite(v) for v in metrics.values())


def test_report_node_refuses_empty_split_and_bad_params():
    with pytest.raises(ValueError, match="forecast and an outcome"):
        CondorDistributionReport("c", dict(PARAMS, split="test")).run(
            SimpleNamespace(splits=_Split()), {"forecasts": _rows()})
    for bad in ({"split": "x"}, {"strikes_z": [1, 2]}, {"credit_fraction": 0},
                {"cvar_alpha": 2}, {"forward_field": ""}, {"typo": 1}):
        with pytest.raises(ConfigError):
            CondorDistributionReport("c", dict(PARAMS, **bad))
    with pytest.raises(ConfigError):
        CondorDistributionReport("c", {"split": "val"})
