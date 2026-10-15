"""StampSeriesStats (ADR-0265 Amendment 5): a stamped value stream reduced to one series per group, then tested.

Every expectation is written out by hand or by a direct call to the statistics owner
(``newey_west_mean``, ``sharpe_ratio``, ``CalendarBlockBootstrap``, ``sample_quantile``), never read back
from the node. The node is wired by import path and is not a registered kind.
"""

import math
import random
from datetime import date, timedelta

import pytest

from dskit.pipeline.kinds_series import StampSeriesStats
from dskit.pipeline.node import DEFAULT_NODE_KINDS, ConfigError, NodeContext
from dskit.pipeline.stats import (
    CalendarBlockBootstrap,
    dm_lags,
    newey_west_mean,
    sample_quantile,
    sharpe_ratio,
)

START = date(2020, 1, 6)


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="series", asof="2025-12-31", run_dir=str(tmp_path))


def day(i):
    return (START + timedelta(days=i)).isoformat()


BASE = {"stamp_field": "d", "value_field": "v"}


def run(ctx, rows, **over):
    return StampSeriesStats("series", {**BASE, **over}).run(ctx, {"records": rows})


def series_rows(values, step=1):
    return [{"d": day(i * step), "v": v} for i, v in enumerate(values)]


VALUES = [0.02, -0.01, 0.03, 0.01, -0.02, 0.04, 0.0, 0.02, 0.01, -0.01, 0.03, 0.02]


def test_the_mean_and_the_hac_statistics_match_the_owner(ctx):
    out = run(ctx, series_rows(VALUES), lags=2)
    block = out["summary"]["groups"]["all"]
    hac = newey_west_mean(VALUES, lags=2)
    assert block["n_stamps"] == 12 and block["n_rows"] == 12
    assert block["mean"] == pytest.approx(sum(VALUES) / 12)
    assert (block["se"], block["t"], block["p_value"]) == pytest.approx((hac["se"], hac["t"], hac["p_value"]))
    assert block["lags"] == 2 and block["frac_pos"] == pytest.approx(8 / 12) and block["hac_withheld"] == ""


def test_the_default_band_is_the_dm_rule_over_h_steps(ctx):
    block = run(ctx, series_rows(VALUES), h_steps=5)["summary"]["groups"]["all"]
    assert block["lags"] == dm_lags(12, 5) == 4


def test_sharpe_is_per_period_and_annualised_by_the_declared_periods(ctx):
    block = run(ctx, series_rows(VALUES), periods_per_year=12)["summary"]["groups"]["all"]
    owner = sharpe_ratio(VALUES)
    assert block["sharpe"] == pytest.approx(owner["sharpe"])
    assert block["sharpe_annualized"] == pytest.approx(owner["sharpe"] * math.sqrt(12))
    assert block["sharpe_analytic"]["se"] == pytest.approx(owner["se"])


def test_without_periods_per_year_no_annualised_sharpe_is_reported(ctx):
    block = run(ctx, series_rows(VALUES))["summary"]["groups"]["all"]
    assert block["sharpe"] is not None and block["sharpe_annualized"] is None


def test_a_flat_series_has_no_sharpe_and_no_t(ctx):
    block = run(ctx, series_rows([0.01] * 8), periods_per_year=12)["summary"]["groups"]["all"]
    assert block["sharpe"] is None and block["t"] is None and block["hac_withheld"] == "no_variance"


def test_rows_in_one_bucket_are_averaged_or_summed_before_testing(ctx):
    # two rows per day; the day's value is their mean (default) or their sum
    rows = [{"d": day(i), "v": v} for i, (a, b) in enumerate(zip(VALUES[:6], VALUES[6:])) for v in (a, b)]
    mean = run(ctx, rows, lags=0)["summary"]["groups"]["all"]
    total = run(ctx, rows, lags=0, bucket_agg="sum")["summary"]["groups"]["all"]
    assert mean["n_rows"] == 12 and mean["n_stamps"] == 6
    expected = [(a + b) / 2 for a, b in zip(VALUES[:6], VALUES[6:])]
    assert mean["mean"] == pytest.approx(sum(expected) / 6)
    assert total["mean"] == pytest.approx(sum(a + b for a, b in zip(VALUES[:6], VALUES[6:])) / 6)


def test_the_week_and_month_buckets_cluster_by_iso_week_and_calendar_month(ctx):
    rows = series_rows(VALUES, step=5)                         # 55 days from Mon 2020-01-06
    weekly = run(ctx, rows, bucket="week", lags=0)
    monthly = run(ctx, rows, bucket="month", lags=0)
    weeks = {}
    for r in rows:
        weeks.setdefault(date.fromisoformat(r["d"]).isocalendar()[:2], []).append(r["v"])
    months = {}
    for r in rows:
        months.setdefault(r["d"][:7], []).append(r["v"])
    assert weekly["summary"]["groups"]["all"]["n_stamps"] == len(weeks)
    assert monthly["summary"]["groups"]["all"]["n_stamps"] == len(months) == 3
    assert [r["value"] for r in monthly["records"]] == pytest.approx(
        [sum(v) / len(v) for _, v in sorted(months.items())])


def test_the_bucket_stamp_is_its_earliest_date_and_records_are_in_stamp_order(ctx):
    rows = list(reversed(series_rows(VALUES, step=5)))         # arrives out of order
    out = run(ctx, rows, bucket="month")
    assert [r["stamp"] for r in out["records"]] == ["2020-01-06", "2020-02-05", "2020-03-01"]
    assert all(r["group"] == "all" for r in out["records"])


def test_groups_split_by_the_declared_fields(ctx):
    rows = [{**r, "kind": "a" if i % 2 == 0 else "b"} for i, r in enumerate(series_rows(VALUES))]
    out = run(ctx, rows, by=["kind"], lags=0)["summary"]["groups"]
    assert set(out) == {"all", "kind=a", "kind=b"}
    assert out["kind=a"]["mean"] == pytest.approx(sum(VALUES[0::2]) / 6)
    assert out["kind=b"]["n_stamps"] == 6


def test_a_null_group_field_is_its_own_group(ctx):
    rows = [{**r, "kind": None if i < 3 else "a"} for i, r in enumerate(series_rows(VALUES))]
    assert "kind=None" in run(ctx, rows, by=["kind"], lags=0)["summary"]["groups"]


def test_a_missing_or_non_numeric_value_is_dropped_and_counted_by_reason(ctx):
    rows = series_rows(VALUES) + [{"d": day(40), "v": None}, {"d": day(41), "v": float("nan")},
                                  {"d": day(42), "v": True}]
    out = run(ctx, rows, lags=0)
    assert out["summary"]["dropped"] == {"value_not_a_number": 3}
    assert out["summary"]["groups"]["all"]["n_rows"] == 12


def test_a_row_without_the_stamp_or_a_non_iso_stamp_refuses_by_index(ctx):
    with pytest.raises(ValueError, match="row 1"):
        run(ctx, [{"d": day(0), "v": 1.0}, {"v": 1.0}])
    with pytest.raises(ValueError, match="ISO"):
        run(ctx, [{"d": "2020/01/06", "v": 1.0}])


def test_a_series_whose_band_spans_it_keeps_the_mean_and_withholds_the_statistics(ctx):
    block = run(ctx, series_rows(VALUES[:4]), lags=3)["summary"]["groups"]["all"]
    assert block["mean"] == pytest.approx(sum(VALUES[:4]) / 4)
    assert block["t"] is None and "spans" in block["hac_withheld"]


def test_fewer_than_min_stamps_reports_the_mean_only(ctx):
    block = run(ctx, series_rows(VALUES[:3]), min_stamps=5)["summary"]["groups"]["all"]
    assert block["n_stamps"] == 3 and block["t"] is None and "min_stamps" in block["hac_withheld"]


def test_the_bootstrap_interval_of_the_mean_and_of_the_sharpe_match_a_direct_resample(ctx):
    rng = random.Random(3)
    vals = [rng.gauss(0.01, 0.03) for _ in range(40)]
    spec = {"block_days": 5, "replicates": 200, "seed": 11, "level": 0.9}
    block = run(ctx, series_rows(vals), periods_per_year=12, bootstrap=spec)["summary"]["groups"]["all"]
    stamps = [START + timedelta(days=i) for i in range(40)]
    draws = CalendarBlockBootstrap(stamps, 5).sample_indices(200, 11)
    means = sorted(sum(vals[i] for i in d) / 40 for d in draws)

    def annual_sharpe(sample):
        m = sum(sample) / len(sample)
        sd = math.sqrt(sum((x - m) ** 2 for x in sample) / (len(sample) - 1))
        return m / sd * math.sqrt(12)

    sharpes = sorted(annual_sharpe([vals[i] for i in d]) for d in draws)
    ci, sci = block["ci"], block["sharpe_ci"]
    assert ci["supported"] and ci["lo"] == pytest.approx(sample_quantile(means, 0.05))
    assert ci["hi"] == pytest.approx(sample_quantile(means, 0.95))
    assert sci["lo"] == pytest.approx(sample_quantile(sharpes, 0.05))
    assert sci["lower_one_sided"] == pytest.approx(sample_quantile(sharpes, 0.10))


def test_the_hurdle_is_passed_only_when_the_interval_lower_bound_clears_it(ctx):
    rng = random.Random(9)
    vals = [rng.gauss(0.05, 0.01) for _ in range(40)]          # a Sharpe far above any hurdle
    spec = {"block_days": 5, "replicates": 100, "seed": 1, "level": 0.9}
    high = run(ctx, series_rows(vals), periods_per_year=12, bootstrap=spec, hurdle=0.5)
    low = run(ctx, series_rows(VALUES * 3), periods_per_year=12, bootstrap=spec, hurdle=50.0)
    assert high["summary"]["groups"]["all"]["hurdle"] == {
        "sharpe": 0.5, "lower_bound": high["summary"]["groups"]["all"]["sharpe_ci"]["lo"], "passes": True}
    assert low["summary"]["groups"]["all"]["hurdle"]["passes"] is False


def test_an_unsupported_bootstrap_says_so_and_fails_the_hurdle(ctx):
    spec = {"block_days": 400, "replicates": 20, "seed": 1, "level": 0.9}
    block = run(ctx, series_rows(VALUES), periods_per_year=12, bootstrap=spec, hurdle=0.5)["summary"]["groups"]["all"]
    assert block["ci"]["supported"] is False and block["sharpe_ci"]["supported"] is False
    assert block["hurdle"]["passes"] is False and block["hurdle"]["lower_bound"] is None


def test_a_paired_contrast_is_the_difference_series_on_the_stamps_both_sides_have(ctx):
    plus = [{"d": day(i), "v": v, "g": "on"} for i, v in enumerate(VALUES)]
    minus = [{"d": day(i), "v": v / 2, "g": "off"} for i, v in enumerate(VALUES[:10])]   # two stamps missing
    out = run(ctx, plus + minus, lags=1,
              contrast={"field": "g", "plus": "on", "minus": "off", "paired": True})
    block = out["summary"]["contrast"]
    diff = [v - v / 2 for v in VALUES[:10]]
    hac = newey_west_mean(diff, lags=1)
    assert block["paired"] is True and block["n_stamps"] == 10 and block["stamps_unmatched"] == 2
    assert block["mean"] == pytest.approx(sum(diff) / 10) and block["t"] == pytest.approx(hac["t"])


def test_an_unpaired_contrast_is_the_difference_of_means_with_independent_standard_errors(ctx):
    plus = [{"d": day(i), "v": v, "g": "on"} for i, v in enumerate(VALUES)]
    minus = [{"d": day(i + 100), "v": 0.5 * v, "g": "off"} for i, v in enumerate(VALUES)]
    out = run(ctx, plus + minus, lags=1,
              contrast={"field": "g", "plus": "on", "minus": "off", "paired": False})
    block = out["summary"]["contrast"]
    a, b = newey_west_mean(VALUES, lags=1), newey_west_mean([0.5 * v for v in VALUES], lags=1)
    se = math.sqrt(a["se"] ** 2 + b["se"] ** 2)
    assert block["paired"] is False
    assert block["mean"] == pytest.approx(a["mean"] - b["mean"])
    assert block["se"] == pytest.approx(se) and block["t"] == pytest.approx((a["mean"] - b["mean"]) / se)


def test_a_contrast_side_with_no_rows_leaves_the_contrast_empty_and_says_which(ctx):
    rows = [{"d": day(i), "v": v, "g": "on"} for i, v in enumerate(VALUES)]
    block = run(ctx, rows, contrast={"field": "g", "plus": "on", "minus": "off", "paired": True})["summary"]["contrast"]
    assert block["mean"] is None and block["t"] is None and block["n_stamps"] == [12, 0]
    assert "'off'" in block["hac_withheld"]


@pytest.mark.parametrize("bad, text", [
    ({"stamp_field": ""}, "stamp_field"), ({"value_field": 3}, "value_field"),
    ({"bucket": "year"}, "bucket"), ({"bucket_agg": "median"}, "bucket_agg"),
    ({"by": []}, "by"), ({"by": ["x", "x"]}, "distinct"), ({"periods_per_year": 0}, "periods_per_year"),
    ({"lags": -1}, "lags"), ({"h_steps": 0}, "h_steps"), ({"min_stamps": 1}, "min_stamps"),
    ({"hurdle": 0.5}, "hurdle needs"), ({"bootstrap": {"block_days": 5}}, "replicates"),
    ({"contrast": {"field": "g", "plus": "a"}}, "minus"),
    ({"contrast": {"field": "g", "plus": "a", "minus": "a", "paired": True}}, "differ"),
    ({"zzz": 1}, "zzz")])
def test_a_bad_declaration_is_refused_by_name(bad, text):
    problems = StampSeriesStats.validate_params({**BASE, **bad})
    assert any(text in p for p in problems), problems


def test_it_is_not_a_registered_kind_and_a_bad_declaration_refuses_at_construction():
    assert "stamp-series-stats" not in DEFAULT_NODE_KINDS.kinds()
    with pytest.raises(ConfigError):
        StampSeriesStats("series", {**BASE, "bucket": "year"})


def test_a_paired_contrast_on_week_and_month_buckets_pairs_on_the_bucket_not_the_opening_date(ctx):
    rows = []
    for w in range(6):
        rows.append({"d": day(7 * w), "v": 0.02 + 0.001 * w, "g": "P"})          # Mondays
        rows.append({"d": day(7 * w + 1), "v": 0.01, "g": "M"})                  # Tuesdays of the same weeks
    block = run(ctx, rows, bucket="week", lags=0, contrast={"field": "g", "plus": "P", "minus": "M", "paired": True})["summary"]["contrast"]
    assert block["n_stamps"] == 6 and block["stamps_unmatched"] == 0
    assert block["mean"] == pytest.approx(sum(0.02 + 0.001 * w - 0.01 for w in range(6)) / 6)
    months = [{"d": day(31 * m + off), "v": 0.03 if g == "P" else 0.01, "g": g} for m in range(5) for g, off in (("P", 0), ("M", 9))]
    monthly = run(ctx, months,
                  bucket="month", lags=0, contrast={"field": "g", "plus": "P", "minus": "M", "paired": True})["summary"]["contrast"]
    assert monthly["n_stamps"] >= 4 and monthly["mean"] == pytest.approx(0.02)


def test_week_and_month_series_get_a_supported_bootstrap_with_the_edge_padded_blocks(ctx):
    rng = random.Random(2)
    rows = [{"d": (date(2020, 1, 6) + timedelta(days=30 * i)).isoformat(), "v": rng.gauss(0.01, 0.02)} for i in range(60)]
    spec = {"block_days": 90, "replicates": 100, "seed": 3, "level": 0.9}
    block = run(ctx, rows, bucket="month", periods_per_year=12, bootstrap=spec, hurdle=-100.0)["summary"]["groups"]["all"]
    assert block["ci"]["supported"] and block["sharpe_ci"]["supported"] and block["hurdle"]["passes"] is True
