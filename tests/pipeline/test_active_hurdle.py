"""ActiveHurdle (ADR-0279 Stage 3): a backtest's return series into a deflated annual hurdle.

mean / sd are worked with ``statistics`` (independent of dskit.pipeline.stats);
the expected maximum null Sharpe for 5 and 20 trials is pinned from one
recorded evaluation of ``deflated_sharpe_ratio`` on the same twelve returns.
"""

import math
import re
import statistics

import pytest

from dskit.pipeline import active_hurdle, liquidation
from dskit.pipeline.active_hurdle import ActiveHurdle
from dskit.pipeline.liquidation import HoldVsRedeploy
from dskit.pipeline import stats
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


def test_a_constant_series_refuses_whatever_float_noise_its_mean_carries(ctx):
    # 52 copies of 0.0123 have NO dispersion, but their float mean is not exactly 0.0123, so a
    # naive sd is ~1e-17 and the Sharpe a 7e15 artifact that once read as an admissible hurdle
    # of 64% a year. The series is refused before any Sharpe is computed.
    for constant in (0.0123, 0.01, 0.1, 1 / 3, -0.004):
        with pytest.raises(ValueError, match="no usable Sharpe"):
            _run(ctx, rows=_rows([constant] * 52), min_periods=52)
    # all zeros: no spread and no magnitude to be relative to
    with pytest.raises(ValueError, match="no usable Sharpe"):
        _run(ctx, rows=_rows([0.0] * 12))
    # two values one ulp apart are the same value to float noise
    noisy = [0.0123, math.nextafter(0.0123, 1.0)] * 26
    with pytest.raises(ValueError, match="no usable Sharpe"):
        _run(ctx, rows=_rows(noisy), min_periods=52)


def test_the_no_variance_boundary_is_the_shared_relative_tolerance(ctx):
    # stats.NO_VARIANCE_RTOL is 1e-12 of the largest magnitude: around 0.01 that is 1e-14, so
    # a spread of 5e-15 is noise and a spread of 2e-14 is a real (tiny) dispersion that runs
    assert stats.NO_VARIANCE_RTOL == 1e-12
    for spread, refused in ((5e-15, True), (2e-14, False)):
        rows = _rows([0.01, 0.01 + spread] * 26)
        if refused:
            with pytest.raises(ValueError, match="no usable Sharpe"):
                _run(ctx, rows=rows, min_periods=52)
        else:
            assert _run(ctx, rows=rows, min_periods=52)["rate"] == pytest.approx(0.52, abs=1e-3)


def test_the_no_variance_boundary_is_inclusive(ctx, monkeypatch):
    # the tolerance is stats' ONE constant (the module imports it, never restates it) ...
    assert active_hurdle.NO_VARIANCE_RTOL is stats.NO_VARIANCE_RTOL
    # ... and "at most that fraction" includes equality. A power of two makes the float
    # arithmetic exact: tolerance 2**-10 of the largest magnitude 1.0 is exactly the spread
    # of [1 - 2**-10, 1.0]; one more binary step (2**-9) is dispersed enough to run on to
    # the rate bound (a mean near 1 annualizes far outside [-1, 2])
    monkeypatch.setattr(active_hurdle, "NO_VARIANCE_RTOL", 2.0 ** -10)
    with pytest.raises(ValueError, match="no usable Sharpe"):
        _run(ctx, rows=_rows([1.0 - 2.0 ** -10, 1.0] * 26), min_periods=52)
    with pytest.raises(ValueError, match="deflated rate"):
        _run(ctx, rows=_rows([1.0 - 2.0 ** -9, 1.0] * 26), min_periods=52)


def test_records_must_be_a_list(ctx):
    for bad in (None, {"net_return": 0.1}, "0.1"):
        with pytest.raises(ValueError, match="records must be a list, got "):
            ActiveHurdle("h", HURDLE).run(ctx, {"records": bad})


def test_an_undefined_deflation_refuses_with_the_series_sd_not_a_type_error(ctx, monkeypatch):
    # stats returns dsr None when the Sharpe's standard error is undefined; the arithmetic after
    # it needs both numbers, so the hurdle names the cause instead of failing on None * float
    undefined = {"dsr": None, "expected_max_sharpe": None, "trials": 5, "sharpe_variance": None}
    monkeypatch.setattr(active_hurdle, "deflated_sharpe_ratio", lambda series, trials: undefined)
    with pytest.raises(ValueError, match=r"h: the series has no usable Sharpe \(sd 0.0087021"):
        _run(ctx)


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


def test_a_renamed_return_field_gives_the_same_hurdle(ctx):
    # test_five_trials_haircut_the_mean_by_the_null_maximum with the column called "r"
    rows = [{"week": index, "r": value} for index, value in enumerate(RETURNS)]
    out = ActiveHurdle("h", {**HURDLE, "return_field": "r"}).run(ctx, {"records": rows})
    assert out["rate"] == pytest.approx((MEAN - EMAX_5 * SD) * 52)
    assert out["summary"]["dsr"] == pytest.approx(DSR_5)
    assert out["summary"]["haircut"] == pytest.approx(EMAX_5 * SD * 52)
    assert out["admissible"] is True
    # the default spelling is not read when another is declared
    with pytest.raises(ValueError, match="row 0 field 'r' is not a number"):
        ActiveHurdle("h", {**HURDLE, "return_field": "r"}).run(ctx, {"records": _rows()})


BAD_HURDLE = [
    ({"alpha": 0.0}, "unknown"),
    *[({"return_field": bad}, "return_field must be a non-empty field name")
      for bad in ("", 5, None)],
    *[({"periods_per_year": bad}, "periods_per_year must be an int >= 1")
      for bad in (0, 52.5, True, "52", None)],
    *[({"trials": bad}, "trials must be an int >= 1") for bad in (0, 5.5, True, "5")],
    *[({"min_periods": bad}, "min_periods must be an int >= 2") for bad in (1, 12.5, True, None)],
    *[({"min_dsr": bad}, "min_dsr must be a number in")
      for bad in (-0.01, 1.01, True, "x", float("nan"))],
    *[({name: bad}, f"{name} must be at most 1000000")
      for name in ("periods_per_year", "trials") for bad in (10 ** 6 + 1, 10 ** 400, 1e300)],
]


@pytest.mark.parametrize("over, match", BAD_HURDLE)
def test_each_bad_hurdle_declaration_is_one_named_problem(over, match):
    problems = ActiveHurdle.validate_params({**HURDLE, **over})
    assert len(problems) == 1 and re.search(match, problems[0]), problems


@pytest.mark.parametrize("name", list(HURDLE))
def test_each_hurdle_param_is_named_when_missing(name):
    problems = ActiveHurdle.validate_params({k: v for k, v in HURDLE.items() if k != name})
    assert problems == [f"missing required params ['{name}']"]


def test_a_huge_count_is_a_named_refusal_not_an_overflow_at_run(ctx):
    # an int no float can hold used to raise OverflowError from the annualization / deflation
    for name in ("periods_per_year", "trials"):
        with pytest.raises(ConfigError, match=f"{name} must be at most 1000000"):
            ActiveHurdle("h", {**HURDLE, name: 10 ** 400})
        # the ceiling itself is accepted (liquidation.MAX_COUNT is the one owner)
        assert ActiveHurdle.validate_params({**HURDLE, name: liquidation.MAX_COUNT}) == []
    # a million trials is a legitimate (very harsh) deflation: it runs
    out = _run(ctx, trials=liquidation.MAX_COUNT)
    assert out["summary"]["expected_max_sharpe"] > 1.5
    assert out["summary"]["trials"] == liquidation.MAX_COUNT
