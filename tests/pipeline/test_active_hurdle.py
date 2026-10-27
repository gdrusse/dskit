"""ActiveHurdle (ADR-0279 Stage 3): a backtest's return series into a deflated annual hurdle.

mean / sd are worked with ``statistics`` (independent of dskit.pipeline.stats);
the expected maximum null Sharpe for 5 and 20 trials is pinned from one
recorded evaluation of ``deflated_sharpe_ratio`` on the same twelve returns.
"""

import statistics

import pytest

from dskit.pipeline.active_hurdle import ActiveHurdle
from dskit.pipeline.liquidation import HoldVsRedeploy
from dskit.pipeline.node import ConfigError, NodeContext

RETURNS = [0.01, -0.005, 0.02, 0.0, 0.015, -0.01, 0.005, 0.01, 0.012, -0.002, 0.008, 0.003]
MEAN = statistics.mean(RETURNS)            # 0.0055
SD = statistics.stdev(RETURNS)             # 0.0087021
EMAX_5, EMAX_20 = 0.39777384449675074, 0.6339558209781
DSR_5, DSR_20 = 0.7587648460960104, 0.4976945217146815

HURDLE = {
    "return_field": "net_return",
    "periods_per_year": 52,
    "trials": 5,
    "min_dsr": 0.5,
    "min_periods": 12,
}


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="ah", asof="2026-10-27", run_dir=str(tmp_path))


def _rows(returns=RETURNS):
    return [{"week": index, "net_return": value} for index, value in enumerate(returns)]


def _run(ctx, rows=None, **over):
    return ActiveHurdle("h", {**HURDLE, **over}).run(ctx, {"records": rows or _rows()})


def test_one_trial_is_the_plain_annualized_mean(ctx):
    out = _run(ctx, trials=1)
    assert out["rate"] == pytest.approx(MEAN * 52)
    assert out["admissible"] is True
    assert out["summary"]["expected_max_sharpe"] == 0.0
    assert out["summary"]["annual_mean"] == pytest.approx(MEAN * 52)
    assert out["summary"]["annual_sd"] == pytest.approx(SD * 52 ** 0.5)


def test_five_trials_haircut_the_mean_by_the_null_maximum(ctx):
    out = _run(ctx, trials=5)
    assert out["rate"] == pytest.approx((MEAN - EMAX_5 * SD) * 52)
    assert out["summary"]["dsr"] == pytest.approx(DSR_5)
    assert out["summary"]["haircut"] == pytest.approx(EMAX_5 * SD * 52)
    assert out["admissible"] is True


def test_min_dsr_is_inclusive(ctx):
    # the boundary is the node's own dsr, so the test holds to the last bit whatever the
    # platform's float rounding; DSR_5 is the recorded value it must be close to
    dsr = _run(ctx, trials=5)["summary"]["dsr"]
    assert dsr == pytest.approx(DSR_5)
    assert _run(ctx, trials=5, min_dsr=dsr)["admissible"] is True
    assert _run(ctx, trials=5, min_dsr=dsr + 1e-9)["admissible"] is False
    assert _run(ctx, trials=5, min_dsr=dsr - 1e-9)["admissible"] is True


def test_periods_per_year_annualizes_and_does_not_touch_the_sharpe(ctx):
    # the same twelve returns read as MONTHLY: mean 0.066 / 12 periods = 0.0055 a period, so the
    # one-trial annual rate is 0.0055 x 12 = 0.066 (x 52 was 0.286), and sd 0.0087021 grows by
    # sqrt(12) = 3.4641 to 0.030145 (not by sqrt(52))
    out = _run(ctx, trials=1, periods_per_year=12)
    assert out["rate"] == pytest.approx(0.066)
    assert out["summary"]["periods_per_year"] == 12
    assert out["summary"]["annual_mean"] == pytest.approx(0.066)
    assert out["summary"]["annual_sd"] == pytest.approx(0.030145, abs=1e-6)
    # five trials: the null-Sharpe haircut is per period, so it scales by 12 too
    five = _run(ctx, trials=5, periods_per_year=12)
    assert five["rate"] == pytest.approx((MEAN - EMAX_5 * SD) * 12)
    assert five["summary"]["haircut"] == pytest.approx(EMAX_5 * SD * 12)
    # the Sharpe, its deflated probability and the admissibility verdict are per period
    weekly = _run(ctx, trials=5)
    assert five["summary"]["sharpe"] == weekly["summary"]["sharpe"]
    assert five["summary"]["dsr"] == weekly["summary"]["dsr"]
    assert five["admissible"] is True and weekly["admissible"] is True


def test_twenty_trials_fall_under_min_dsr_and_the_rate_is_withheld(ctx):
    out = _run(ctx, trials=20)
    assert out["admissible"] is False
    assert out["rate"] is None
    assert out["summary"]["dsr"] == pytest.approx(DSR_20)
    assert out["summary"]["deflated_rate"] == pytest.approx((MEAN - EMAX_20 * SD) * 52)


def test_unusable_series_and_declarations_refuse(ctx):
    with pytest.raises(ValueError, match="row 1.*net_return"):
        _run(ctx, rows=[{"net_return": 0.1}, {"net_return": "x"}])
    with pytest.raises(ValueError, match="no usable Sharpe"):
        _run(ctx, rows=[{"net_return": 0.01}] * 6, min_periods=2)
    with pytest.raises(ValueError, match="row 1.*mapping"):
        _run(ctx, rows=[{"net_return": 0.1}, [0.2]])
    with pytest.raises(ConfigError, match="trials"):
        ActiveHurdle("h", {**HURDLE, "trials": 0})
    with pytest.raises(ConfigError, match="min_dsr"):
        ActiveHurdle("h", {**HURDLE, "min_dsr": 1.5})
    with pytest.raises(ConfigError, match="periods_per_year"):
        ActiveHurdle("h", {**HURDLE, "periods_per_year": 0})
    with pytest.raises(ConfigError, match="unknown"):
        ActiveHurdle("h", {**HURDLE, "alpha": 0.0})


def test_a_series_shorter_than_min_periods_refuses_by_name(ctx):
    rows = _rows([0.01, -0.005, 0.02])
    with pytest.raises(ValueError, match="min_periods.*4.*got 3"):
        _run(ctx, rows=rows, min_periods=4)
    # exactly min_periods runs; the declared floor is the only floor
    assert _run(ctx, rows=rows, min_periods=3)["summary"]["periods"] == 3
    assert _run(ctx, rows=rows, min_periods=2)["summary"]["periods"] == 3
    with pytest.raises(ConfigError, match="min_periods"):
        ActiveHurdle("h", {**HURDLE, "min_periods": 1})
    with pytest.raises(ConfigError, match="min_periods"):
        ActiveHurdle("h", {**HURDLE, "min_periods": True})
    with pytest.raises(ConfigError, match="missing.*min_periods"):
        ActiveHurdle("h", {k: v for k, v in HURDLE.items() if k != "min_periods"})


def test_a_percent_unit_series_refuses_with_the_fractions_message(ctx):
    percent = [value * 100 for value in RETURNS]          # 0.55 a week = 28.6 a year
    with pytest.raises(ValueError, match="deflated rate.*outside.*fractions.*percent"):
        _run(ctx, rows=_rows(percent), trials=1)
    # five trials shrink it to (0.55 - 0.3978 x 0.8702) x 52 = 10.6, still over 2; and the
    # strategy is inadmissible at min_dsr 1.0 (dsr 0.76): the bound is checked before admissibility
    with pytest.raises(ValueError, match="fractions"):
        _run(ctx, rows=_rows(percent), trials=5, min_dsr=1.0)


def test_integral_floats_are_accepted_and_run_as_the_same_ints(ctx):
    plain = _run(ctx)
    floats = _run(ctx, periods_per_year=52.0, trials=5.0, min_periods=12.0)
    assert floats["rate"] == plain["rate"]
    summary = floats["summary"]
    assert (summary["periods_per_year"], summary["trials"]) == (52, 5)
    assert isinstance(summary["periods_per_year"], int) and isinstance(summary["trials"], int)
    with pytest.raises(ConfigError, match="trials"):
        ActiveHurdle("h", {**HURDLE, "trials": 5.5})


DECIDE = {
    "horizon_years": 1, "hold_return": 0.05, "redeploy_cost": 0.01,
    "risk_premium": 0.0, "value_field": "v", "net_field": "n", "symbol_field": "symbol",
}
ROWS = [{"symbol": "A", "v": 100.0, "n": 90.0}]


def test_decision_takes_a_wired_hurdle_over_the_param(ctx):
    out = HoldVsRedeploy("d", {**DECIDE, "redeploy_return": 0.5}).run(
        ctx, {"records": ROWS, "redeploy_return": 0.20})
    assert out["records"][0]["redeploy_value"] == pytest.approx(90.0 * 1.19)
    assert out["summary"]["redeploy_return_source"] == "wired"
    out = HoldVsRedeploy("d", {**DECIDE, "redeploy_return": 0.5}).run(ctx, {"records": ROWS})
    assert out["summary"]["redeploy_return_source"] == "param"
    with pytest.raises(ValueError, match="no redeploy_return"):
        HoldVsRedeploy("d", DECIDE).run(ctx, {"records": ROWS})


def test_an_inadmissible_hurdle_holds_everything(ctx):
    out = HoldVsRedeploy("d", DECIDE).run(ctx, {"records": ROWS, "redeploy_return": None})
    row = out["records"][0]
    assert row["decision"] == "hold"
    assert row["redeploy_value"] is None and row["advantage"] is None
    assert row["hold_value"] == pytest.approx(105.0)
    assert out["summary"]["redeploy_return_source"] == "inadmissible"
    assert out["summary"]["decisions"] == {"hold": 1, "exit": 0}
    assert out["summary"]["redeploy_value"] is None
