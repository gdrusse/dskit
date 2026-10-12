"""QuantileSortBook (ADR-0265 node 6): per-stamp quantile long-short book and its series statistics.

Every expectation is written out by hand or by a direct call to the statistics owner
(``newey_west_mean``, ``CalendarBlockBootstrap``, ``benjamini_hochberg``), never read back
from the node. The node is wired by import path and is not a registered kind.
"""

import random
from datetime import date, timedelta

import pytest

from dskit.pipeline.kinds_stats import QuantileSortBook
from dskit.pipeline.node import DEFAULT_NODE_KINDS, ConfigError, NodeContext
from dskit.pipeline.stats import (
    CalendarBlockBootstrap,
    benjamini_hochberg,
    bonferroni,
    dm_lags,
    newey_west_mean,
    sample_quantile,
)

ASOF = "2025-12-31"
START = date(2020, 1, 6)


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="book", asof=ASOF, run_dir=str(tmp_path))


def day(i):
    return (START + timedelta(days=i)).isoformat()


BASE = {"stamp_field": "d", "series_field": "name", "quantiles": 3, "min_names": 9, "alpha": 0.05,
        "signals": {"s": {"signal_field": "sig", "outcome_field": "ret"}}}


def make_rows(n_stamps=6, names=9, slope=0.01, noise=None, signal="sig", outcome="ret"):
    """Per stamp ``names`` names, signal 1..names, outcome ``slope * signal`` (+ seeded noise)."""
    rng = random.Random(5)
    rows = []
    for i in range(n_stamps):
        for k in range(1, names + 1):
            extra = 0.0 if noise is None else rng.gauss(0.0, noise)
            rows.append({"d": day(i), "name": f"n{k}", signal: float(k), outcome: slope * k + extra})
    return rows


def run(ctx, rows, **over):
    return QuantileSortBook("book", {**BASE, **over}).run(ctx, {"records": rows})


def test_the_spread_and_each_quantile_mean_by_hand(ctx):
    out = run(ctx, make_rows(n_stamps=4))
    rec = out["records"][0]
    # 9 names, 3 buckets: signals {1,2,3} {4,5,6} {7,8,9} -> means 0.02 0.05 0.08
    assert rec["quantile_means"] == pytest.approx([0.02, 0.05, 0.08])
    assert rec["gross"] == pytest.approx(0.06) and rec["cost"] == 0.0 and rec["net"] == pytest.approx(0.06)
    assert rec["n_names"] == 9 and rec["signal"] == "s" and rec["stamp"] == day(0)
    s = out["summary"]["signals"]["s"]
    assert s["quantile_means"] == pytest.approx([0.02, 0.05, 0.08])
    assert s["net"]["mean"] == pytest.approx(0.06)
    assert s["n_stamps"] == 4 and s["thin_stamps"] == 0 and s["rows_scored"] == 36
    assert len(out["records"]) == 4


def test_a_declared_negative_sign_longs_the_low_bucket(ctx):
    rows = make_rows(n_stamps=4)
    plus = run(ctx, rows)["summary"]["signals"]["s"]["net"]["mean"]
    minus = run(ctx, rows, signals={"s": {"signal_field": "sig", "outcome_field": "ret",
                                          "sign": -1}})["summary"]["signals"]["s"]["net"]["mean"]
    assert plus == pytest.approx(0.06) and minus == pytest.approx(-0.06)
    # and a signal that is truly inverse to the outcome earns the declared sign
    inverse = make_rows(n_stamps=4, slope=-0.01)
    got = run(ctx, inverse, signals={"s": {"signal_field": "sig", "outcome_field": "ret",
                                           "sign": -1}})
    assert got["summary"]["signals"]["s"]["net"]["mean"] == pytest.approx(0.06)


def test_a_constant_cost_per_side_is_charged_on_both_legs_and_every_side(ctx):
    rows = make_rows(n_stamps=4)
    out = run(ctx, rows, cost_per_side=0.001)
    rec = out["records"][0]
    assert rec["cost"] == pytest.approx(2 * 2 * 0.001)        # 2 legs x 2 sides (round trip)
    assert rec["net"] == pytest.approx(0.06 - 0.004)
    assert rec["gross"] == pytest.approx(0.06)
    s = out["summary"]["signals"]["s"]
    assert s["gross"]["mean"] == pytest.approx(0.06) and s["net"]["mean"] == pytest.approx(0.056)
    one_way = run(ctx, rows, cost_per_side=0.001, sides_per_leg=1)["records"][0]
    assert one_way["cost"] == pytest.approx(0.002)


def test_a_cost_field_charges_each_leg_its_own_names_cost(ctx):
    rows = make_rows(n_stamps=4)
    for row in rows:
        row["half_spread"] = 0.0001 * row["sig"]
    sig = {"s": {"signal_field": "sig", "outcome_field": "ret", "cost_field": "half_spread"}}
    rec = run(ctx, rows, signals=sig)["records"][0]
    # long names 7,8,9 -> mean 8e-4; short names 1,2,3 -> mean 2e-4; two sides each
    assert rec["cost"] == pytest.approx(2 * 8e-4 + 2 * 2e-4)
    assert rec["net"] == pytest.approx(0.06 - 0.002)


def test_unusable_rows_are_dropped_and_counted_by_field(ctx):
    rows = make_rows(n_stamps=4)
    rows[0]["sig"] = None
    rows[1]["ret"] = float("nan")
    rows[2]["ret"] = "x"
    out = run(ctx, rows)
    s = out["summary"]["signals"]["s"]
    assert s["dropped"] == {"sig": 1, "ret": 2}
    assert s["rows_scored"] == 33
    assert out["summary"]["rows_in"] == 36
    # the first stamp lost three names, so it is thin (6 < 9) and counted, not scored
    assert s["thin_stamps"] == 1 and s["n_stamps"] == 3


def test_a_stamp_with_too_few_names_is_counted_thin_and_not_scored(ctx):
    rows = make_rows(n_stamps=3) + [{"d": day(9), "name": f"m{k}", "sig": float(k), "ret": 0.0}
                                    for k in range(1, 5)]
    s = run(ctx, rows)["summary"]["signals"]["s"]
    assert s["thin_stamps"] == 1 and s["n_stamps"] == 3


def test_a_stamp_whose_extreme_bucket_is_empty_is_withheld_not_zero_filled(ctx):
    # every signal tied: all names land in one bucket, so the extremes are empty
    rows = [{"d": day(i), "name": f"n{k}", "sig": 1.0, "ret": 0.01 * k}
            for i in range(3) for k in range(1, 10)]
    s = run(ctx, rows)["summary"]["signals"]["s"]
    assert s["empty_bucket_stamps"] == 3 and s["n_stamps"] == 0
    assert s["net"]["mean"] is None and s["net"]["n"] == 0


def test_the_hac_statistic_is_the_newey_west_mean_of_the_net_series(ctx):
    rows = make_rows(n_stamps=12, noise=0.01)
    out = run(ctx, rows, cost_per_side=0.0005, lags=2)
    net = [r["net"] for r in out["records"]]
    want = newey_west_mean(net, lags=2)
    got = out["summary"]["signals"]["s"]["net"]
    for key in ("n", "mean", "se", "t", "p_value", "lags"):
        assert got[key] == pytest.approx(want[key])
    assert got["hac_withheld"] == ""
    assert got["frac_pos"] == sum(1 for v in net if v > 0) / len(net)


def test_the_default_band_is_the_owner_dm_lags_over_the_declared_horizon(ctx):
    rows = make_rows(n_stamps=30, noise=0.01)
    out = run(ctx, rows, h_steps=5)
    assert out["summary"]["signals"]["s"]["net"]["lags"] == dm_lags(30, 5)


def test_a_band_spanning_the_series_withholds_the_hac_statistics_but_keeps_the_mean(ctx):
    rows = make_rows(n_stamps=3, noise=0.01)
    net = run(ctx, rows, lags=2)["summary"]["signals"]["s"]["net"]
    assert net["mean"] is not None and net["n"] == 3
    assert net["se"] is None and net["t"] is None and net["p_value"] is None
    assert "spans" in net["hac_withheld"]
    one = run(ctx, make_rows(n_stamps=1))["summary"]["signals"]["s"]["net"]
    assert one["t"] is None and one["hac_withheld"] and one["mean"] == pytest.approx(0.06)


def two_signal_rows(n_stamps=40):
    """A real signal, a pure-noise one and an inverse one on the same outcomes."""
    rng = random.Random(11)
    rows = []
    for i in range(n_stamps):
        for k in range(1, 10):
            ret = 0.01 * k + rng.gauss(0.0, 0.02)
            rows.append({"d": day(i), "name": f"n{k}", "good": float(k), "ret": ret,
                         "junk": rng.random(), "bad": -float(k)})
    return rows


def test_bh_and_bonferroni_run_across_the_signals_with_the_owners(ctx):
    sigs = {name: {"signal_field": name, "outcome_field": "ret"} for name in ("good", "junk", "bad")}
    out = run(ctx, two_signal_rows(), signals=sigs, lags=1)
    summary = out["summary"]
    p = {name: summary["signals"][name]["net"]["p_value"] for name in sigs}
    assert summary["family"]["p"] == pytest.approx(p)
    assert summary["family"]["alpha"] == 0.05
    assert summary["family"]["decisions"]["bh"] == benjamini_hochberg(p, 0.05)
    assert summary["family"]["decisions"]["bonferroni"] == bonferroni(p, 0.05)
    assert summary["family"]["decisions"]["bh"]["good"] is True
    assert summary["family"]["decisions"]["bh"]["bad"] is False
    assert summary["family"]["withheld"] == []


def test_a_signal_with_no_hac_statistic_stays_in_the_family_at_p_one(ctx):
    rows = two_signal_rows(n_stamps=30)
    for row in rows:                       # the second signal exists only on the first 2 stamps
        if row["d"] > day(1):
            row["junk"] = None
    sigs = {"good": {"signal_field": "good", "outcome_field": "ret"},
            "junk": {"signal_field": "junk", "outcome_field": "ret"}}
    summary = run(ctx, rows, signals=sigs, lags=1, min_names=9)["summary"]
    assert summary["family"]["withheld"] == ["junk"]
    assert summary["family"]["p"]["junk"] == 1.0
    assert summary["family"]["decisions"]["bh"]["junk"] is False
    assert summary["signals"]["junk"]["dropped"]["junk"] == 9 * 28


def test_the_named_corrections_only(ctx):
    summary = run(ctx, make_rows(n_stamps=10, noise=0.01), corrections=["bonferroni"])["summary"]
    assert list(summary["family"]["decisions"]) == ["bonferroni"]


def test_the_block_bootstrap_interval_is_the_owner_resample_of_the_net_series(ctx):
    rows = make_rows(n_stamps=40, noise=0.02)
    boot = {"block_days": 5, "replicates": 200, "seed": 7, "level": 0.9}
    out = run(ctx, rows, bootstrap=boot, cost_per_side=0.001)
    net = [r["net"] for r in out["records"]]
    sampler = CalendarBlockBootstrap([date.fromisoformat(r["stamp"]) for r in out["records"]], 5)
    means = sorted(sum(net[i] for i in idx) / len(net) for idx in sampler.sample_indices(200, 7))
    ci = out["summary"]["signals"]["s"]["ci"]
    assert ci["supported"] is True
    assert ci["lo"] == pytest.approx(sample_quantile(means, 0.05))
    assert ci["hi"] == pytest.approx(sample_quantile(means, 0.95))
    assert ci["lower_one_sided"] == pytest.approx(sample_quantile(means, 0.10))
    assert (ci["level"], ci["block_days"], ci["replicates"], ci["seed"]) == (0.9, 5, 200, 7)


def test_a_series_too_short_for_the_blocks_reports_the_interval_unsupported(ctx):
    boot = {"block_days": 30, "replicates": 50, "seed": 1, "level": 0.9}
    ci = run(ctx, make_rows(n_stamps=4), bootstrap=boot)["summary"]["signals"]["s"]["ci"]
    assert ci["supported"] is False and ci["lo"] is None and ci["hi"] is None


def test_the_bootstrap_refuses_stamps_that_are_not_iso_dates(ctx):
    rows = make_rows(n_stamps=6)
    for row in rows:
        row["d"] = row["d"].replace("-", "/")
    boot = {"block_days": 2, "replicates": 10, "seed": 1, "level": 0.9}
    with pytest.raises(ValueError, match="ISO"):
        run(ctx, rows, bootstrap=boot)


def test_splits_summarize_each_label_with_its_own_cross_sections(ctx):
    rows = make_rows(n_stamps=8, noise=0.01)
    for row in rows:
        row["period"] = "dev" if row["d"] < day(4) else "val"
    out = run(ctx, rows, splits=["period"], lags=0)
    dev = [r["net"] for r in out["records"] if r["stamp"] < day(4)]
    got = out["summary"]["splits"]["s|period=dev"]
    assert got["n"] == 4 and got["mean"] == pytest.approx(sum(dev) / 4)
    assert got["t"] == pytest.approx(newey_west_mean(dev, lags=0)["t"])
    assert "s|period=val" in out["summary"]["splits"]


def test_the_result_is_deterministic_and_independent_of_row_order(ctx):
    rows = make_rows(n_stamps=20, noise=0.02)
    boot = {"block_days": 3, "replicates": 50, "seed": 3, "level": 0.9}
    first = run(ctx, rows, bootstrap=boot, cost_per_side=0.0003)
    again = run(ctx, list(rows), bootstrap=boot, cost_per_side=0.0003)
    shuffled = list(rows)
    random.Random(1).shuffle(shuffled)
    other = run(ctx, shuffled, bootstrap=boot, cost_per_side=0.0003)
    assert first == again
    assert other["summary"] == first["summary"]
    assert other["records"] == first["records"]


@pytest.mark.parametrize("rows,match", [
    ([], "no records"),
    ([{"name": "a", "sig": 1.0, "ret": 0.0}], "lacks the field 'd'"),
    ([{"d": day(0), "sig": 1.0, "ret": 0.0}], "lacks the field 'name'"),
    (["x"], "must be a mapping"),
    ([{"d": day(0), "name": "a", "sig": 1.0, "ret": 0.0},
      {"d": day(0), "name": "a", "sig": 2.0, "ret": 0.0}], "repeats")])
def test_a_malformed_stream_refuses_by_row(ctx, rows, match):
    with pytest.raises(ValueError, match=match):
        run(ctx, rows)


@pytest.mark.parametrize("over", [
    {"bogus": 1}, {"quantiles": 1}, {"quantiles": 2.5}, {"min_names": 2}, {"alpha": 0.0},
    {"alpha": 1.0}, {"stamp_field": ""}, {"signals": {}}, {"signals": {"a|b": {
        "signal_field": "s", "outcome_field": "r"}}},
    {"signals": {"s": {"signal_field": "sig"}}},
    {"signals": {"s": {"signal_field": "sig", "outcome_field": "ret", "sign": 0}}},
    {"signals": {"s": {"signal_field": "sig", "outcome_field": "ret", "extra": 1}}},
    {"cost_per_side": -1.0}, {"cost_per_side": "x"}, {"sides_per_leg": 0},
    {"cost_per_side": 0.001, "signals": {"s": {"signal_field": "sig", "outcome_field": "ret",
                                               "cost_field": "c"}}},
    {"corrections": ["nope"]}, {"corrections": ["weighted-bh"]}, {"corrections": []},
    {"lags": -1}, {"h_steps": 0}, {"splits": []}, {"splits": ["a", "a"]},
    {"bootstrap": {"block_days": 5}},
    {"bootstrap": {"block_days": 0, "replicates": 5, "seed": 1, "level": 0.9}},
    {"bootstrap": {"block_days": 5, "replicates": 5, "seed": 1, "level": 1.0}},
    {"bootstrap": {"block_days": 5, "replicates": 5, "seed": 1, "level": 0.9, "x": 1}}])
def test_default_deny_params(over):
    assert QuantileSortBook.validate_params({**BASE, **over})
    with pytest.raises(ConfigError):
        QuantileSortBook("book", {**BASE, **over})


def test_the_required_params_are_required():
    for name in ("stamp_field", "series_field", "quantiles", "min_names", "signals", "alpha"):
        assert QuantileSortBook.validate_params({k: v for k, v in BASE.items() if k != name})
    assert QuantileSortBook.validate_params(BASE) == []


def test_it_is_wired_by_import_path_and_not_registered():
    assert "QuantileSortBook" not in DEFAULT_NODE_KINDS
    assert QuantileSortBook.role == "transform"
    assert QuantileSortBook.outputs == ("summary", "records")


# -- review findings 2, 6, 7 -------------------------------------------------------------------

def test_a_no_variance_series_reports_no_p_value_and_a_named_reason(ctx):
    rows = make_rows(n_stamps=12, names=10, slope=0.001)
    out = run(ctx, rows, min_names=10)
    net = out["summary"]["signals"]["s"]["net"]
    assert net["n"] == 12 and net["mean"] > 0
    assert net["t"] is None and net["p_value"] is None
    assert net["hac_withheld"] == "no_variance"
    family = out["summary"]["family"]
    assert family["withheld"] == ["s"] and family["p"]["s"] == 1.0
    assert family["decisions"]["bh"]["s"] is False


def test_the_outcome_must_start_after_the_stamp_when_the_column_is_declared(ctx):
    rows = make_rows(n_stamps=4)
    for row in rows:
        row["start"] = row["d"]                      # outcome starts AT the stamp: not strictly after
    with pytest.raises(ValueError, match="outcome_after_field"):
        run(ctx, rows, outcome_after_field="start")
    for row in rows:
        row["start"] = (date.fromisoformat(row["d"]) + timedelta(days=1)).isoformat()
    assert run(ctx, rows, outcome_after_field="start")["summary"]["signals"]["s"]["n_stamps"] == 4
    del rows[3]["start"]
    with pytest.raises(ValueError, match="lacks the field 'start'"):
        run(ctx, rows, outcome_after_field="start")
    rows[3]["start"] = 5
    with pytest.raises(ValueError, match="outcome_after_field"):
        run(ctx, rows, outcome_after_field="start")


def test_outcome_after_field_is_a_validated_name():
    assert QuantileSortBook.validate_params({**BASE, "outcome_after_field": ""})
    assert QuantileSortBook.validate_params({**BASE, "outcome_after_field": "start"}) == []


def test_an_unhashable_name_or_stamp_refuses_as_a_value_error(ctx):
    rows = make_rows(n_stamps=2)
    rows[0]["name"] = ["a"]
    with pytest.raises(ValueError, match="hashable"):
        run(ctx, rows)
