"""KalmanSpreadState: a random-walk level plus an AR(1) deviation, fitted on prior windows only (ADR-0274)."""

import math
import random

import pytest

from dskit.pipeline.libs.kalman_spread import KalmanSpreadState
from dskit.pipeline.node import NodeContext

pytest.importorskip("numpy")
pytest.importorskip("scipy")

PARAMS = {"horizons": [1, 5], "window": 400, "fit_dates": ["2021-01-04", "2022-01-03"], "max_iter": 200}


def sessions(n, start=(2019, 1, 2)):
    from datetime import date, timedelta

    out, day = [], date(*start)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def simulate(n, phi=0.9, sigma_x=1.0, sigma_mu=0.05, sigma_obs=0.1, seed=3):
    rng = random.Random(seed)
    mu, x, out = 10.0, 0.0, []
    for _ in range(n):
        mu += rng.gauss(0.0, sigma_mu)
        x = phi * x + rng.gauss(0.0, sigma_x)
        out.append(mu + x + rng.gauss(0.0, sigma_obs))
    return out


def spread_rows(values, days, pair=("A", "B")):
    return [{"date": d, "symbol_a": pair[0], "symbol_b": pair[1], "spread": v, "status": "ok"}
            for d, v in zip(days, values)]


def run(rows, **over):
    node = KalmanSpreadState("kf", {**PARAMS, **over})
    return node.run(NodeContext(name="t", asof="2026-01-01", run_dir="/nonexistent"), {"spreads": rows})


def test_recovers_simulated_parameters_and_forecasts_the_deviation_decay():
    days = sessions(1000)
    rows = spread_rows(simulate(1000), days)
    out = run(rows)
    r = {x["date"]: x for x in out["records"]}
    first_fit = next(x for x in out["records"] if x["status"] == "ok")
    assert first_fit["fit_date"] == "2021-01-04" and first_fit["date"] >= "2021-01-04"
    assert 0.8 < first_fit["kf_phi"] < 0.97
    assert 0.7 < first_fit["kf_sigma_x"] < 1.3
    assert first_fit["kf_halflife"] == pytest.approx(math.log(0.5) / math.log(first_fit["kf_phi"]))
    late = r[days[-1]]
    assert late["kf_fc_1"] == pytest.approx((late["kf_phi"] - 1.0) * late["kf_dev"])
    assert late["kf_fc_5"] == pytest.approx((late["kf_phi"] ** 5 - 1.0) * late["kf_dev"])
    # with a label that starts k sessions later, the expectation decays by phi**k first
    shifted = {x["date"]: x for x in run(rows, label_offset=1)["records"]}[days[-1]]
    assert shifted["kf_fc_5"] == pytest.approx(late["kf_phi"] * (late["kf_phi"] ** 5 - 1.0) * late["kf_dev"])
    assert shifted["kf_dev"] == pytest.approx(late["kf_dev"])
    assert abs(late["kf_level"] + late["kf_dev"] - late["spread"]) < 3 * late["kf_sigma_obs"] + 1.0
    assert out["summary"]["fits"][0]["converged"] is True and out["summary"]["fits"][0]["n"] == 400
    assert r[days[0]]["status"] == "before_first_fit" and r[days[0]]["kf_fc_1"] is None
    # the deviation forecast has the right sign: a high deviation forecasts a fall
    highs = [x for x in out["records"] if x["status"] == "ok" and x["kf_dev"] > 2.0]
    assert highs and all(x["kf_fc_5"] < 0 for x in highs)


def test_parameters_use_only_the_window_before_each_fit_date():
    days = sessions(1000)
    base = simulate(1000)
    out_a = run(spread_rows(base, days))
    shocked = base[:700] + [v + 50.0 for v in base[700:]]   # everything after mid-2021 changes
    out_b = run(spread_rows(shocked, days))
    fits_a = {f["fit_date"]: f for f in out_a["summary"]["fits"]}
    fits_b = {f["fit_date"]: f for f in out_b["summary"]["fits"]}
    assert fits_a["2021-01-04"]["phi"] == pytest.approx(fits_b["2021-01-04"]["phi"])   # window ends 2020-12-31
    assert fits_a["2021-01-04"]["trained_through"] == max(d for d in days if d < "2021-01-04")
    assert fits_a["2022-01-03"]["phi"] != pytest.approx(fits_b["2022-01-03"]["phi"])
    ra = {x["date"]: x for x in out_a["records"]}
    rb = {x["date"]: x for x in out_b["records"]}
    assert ra["2021-03-01"]["kf_dev"] == pytest.approx(rb["2021-03-01"]["kf_dev"])   # before the shock


def test_a_missing_spread_is_skipped_by_the_filter_and_a_degenerate_fit_is_a_status():
    days = sessions(1000)
    values = simulate(1000)
    rows = spread_rows(values, days)
    hole = 600   # after the first fit date in this synthetic calendar
    rows[hole]["spread"] = None
    rows[hole]["status"] = "incomplete_window"
    out = run(rows)
    r = {x["date"]: x for x in out["records"]}
    assert days[hole] > "2021-01-04"
    assert r[days[hole]]["status"] == "no_spread" and r[days[hole]]["kf_fc_1"] is None
    assert r[days[hole + 1]]["status"] == "ok"   # the filter carries the state across the hole
    flat = run(spread_rows([1.0] * 1000, days))
    assert flat["summary"]["fits"][0]["converged"] is False
    assert all(x["status"] in ("before_first_fit", "degenerate") for x in flat["records"])


def test_a_deviation_variance_at_its_bound_is_a_degenerate_fit(monkeypatch):
    """The rule: sigma_x below the floor (relative to the window's difference sd) is counted, never carried as ok."""
    import scipy.optimize
    from types import SimpleNamespace

    def at_bound(fun, x0, **kwargs):
        x = list(x0)
        x[1] = -40.0   # sigma_x -> exp(-40): no mean-reverting component found
        return SimpleNamespace(x=x, fun=float(fun(x)), success=True, nit=3)

    monkeypatch.setattr(scipy.optimize, "minimize", at_bound)
    days = sessions(1000)
    out = run(spread_rows(simulate(1000), days))
    assert all(f["converged"] is False and f["phi"] is not None for f in out["summary"]["fits"])
    assert all(x["status"] in ("before_first_fit", "degenerate") for x in out["records"])


def test_pairs_are_filtered_independently_and_the_summary_counts_them():
    days = sessions(1000)
    rows = spread_rows(simulate(1000, seed=1), days) + spread_rows(simulate(1000, phi=0.5, seed=2), days, pair=("A", "C"))
    out = run(rows)
    fits = {(f["pair"], f["fit_date"]): f for f in out["summary"]["fits"]}
    assert fits[("A|B", "2022-01-03")]["phi"] > fits[("A|C", "2022-01-03")]["phi"]
    assert out["summary"]["pairs"] == 2 and len(out["records"]) == 2000
    with pytest.raises(ValueError, match="duplicate"):
        run(rows + rows[:1])


def test_kalman_default_denies_and_checks_knobs():
    assert KalmanSpreadState.validate_params(PARAMS) == []
    assert KalmanSpreadState.validate_params({**PARAMS, "label_offset": 1}) == []
    for bad in ({**PARAMS, "extra": 1}, {**PARAMS, "horizons": []}, {**PARAMS, "window": 10},
                {**PARAMS, "label_offset": -1}, {**PARAMS, "label_offset": 1.5},
                {**PARAMS, "fit_dates": []}, {**PARAMS, "fit_dates": ["2021-1-4"]},
                {**PARAMS, "fit_dates": ["2022-01-03", "2021-01-04"]}, {**PARAMS, "max_iter": 0},
                {k: v for k, v in PARAMS.items() if k != "window"}):
        assert KalmanSpreadState.validate_params(bad), bad
    assert KalmanSpreadState.role == "transform" and KalmanSpreadState.outputs == ("records", "summary")
    mod = __import__("dskit.pipeline.libs.kalman_spread", fromlist=["x"])
    assert "KalmanSpreadState" in mod.__all__ and "NODE_KINDS" in mod.__all__


def _optimizer(plan):
    """A fake optimizer: per call, either the honest fit or a sigma_x-at-bound fit, in the order given."""
    import scipy.optimize
    from types import SimpleNamespace

    real, calls = scipy.optimize.minimize, iter(plan)

    def fake(fun, x0, **kwargs):
        mode = next(calls)
        if mode == "ok":
            return real(fun, x0, **kwargs)
        x = list(x0)
        x[1] = -40.0
        return SimpleNamespace(x=x, fun=float(fun(x)), success=True, nit=1)

    return fake


def test_a_later_degenerate_fit_carries_the_previous_parameters(monkeypatch):
    import scipy.optimize

    monkeypatch.setattr(scipy.optimize, "minimize", _optimizer(["ok", "bad"]))
    days = sessions(1000)
    out = run(spread_rows(simulate(1000), days))
    fits = out["summary"]["fits"]
    assert fits[0]["converged"] is True and fits[1]["converged"] is False
    late = [x for x in out["records"] if x["date"] >= "2022-01-03"]
    assert late and all(x["status"] in ("ok", "no_spread") for x in late)
    assert all(x["fit_date"] == "2021-01-04" and x["kf_phi"] == pytest.approx(fits[0]["phi"]) for x in late if x["status"] == "ok")


def test_a_degenerate_first_fit_emits_nothing_until_a_valid_one(monkeypatch):
    import scipy.optimize

    monkeypatch.setattr(scipy.optimize, "minimize", _optimizer(["bad", "ok"]))
    days = sessions(1000)
    out = run(spread_rows(simulate(1000), days))
    first = [x for x in out["records"] if "2021-01-04" <= x["date"] < "2022-01-03"]
    assert first and all(x["status"] == "degenerate" and x["kf_fc_1"] is None for x in first)
    later = [x for x in out["records"] if x["date"] >= "2022-01-03" and x["status"] == "ok"]
    assert later and all(x["fit_date"] == "2022-01-03" for x in later)


def test_a_hole_propagates_the_state_instead_of_updating_on_zero():
    days = sessions(1000)
    values = simulate(1000)   # level near 10, deviations of order 1
    rows = spread_rows(values, days)
    hole = 600
    rows[hole]["spread"] = None
    rows[hole]["status"] = "incomplete_window"
    r = {x["date"]: x for x in run(rows)["records"]}
    before, after = r[days[hole - 1]], r[days[hole + 1]]
    assert abs(after["kf_level"] - before["kf_level"]) < 1.0   # a zero "observation" would drag the level toward 0
    assert abs(after["kf_dev"]) < 6.0
