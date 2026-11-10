"""ADR-0312 S3.4: the hedge evidence reads, on synthetic result documents only."""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import hedge_evidence as he  # noqa: E402

START = date(2019, 1, 7)


def _days(n):
    return [(START + timedelta(days=i)).isoformat() for i in range(n)]


def _result(equity, pnl, close="2019-02-01", trades=None, hedge=None):
    days = _days(len(equity))
    curve = [{"date": d, "equity": e, "contributions": 0.0} for d, e in zip(days, equity)]
    trades = trades if trades is not None else [{"id": "a", "pnl": pnl, "close_date": close, "hedge": {"units": 5}}]
    out = {"curve": curve, "trades": trades, "pnl": pnl}
    if hedge:
        out["hedge"] = hedge
    return out


UNHEDGED = _result([0, -5000, -3000, -1000, -2000, 0], -100.0)
HEDGED = _result([0, -2000, -1500, -500, -800, 100], 100.0)


def test_depth_is_below_the_running_peak_of_the_contribution_free_curve():
    curve = [{"date": "d1", "equity": 110.0, "contributions": 100.0},
             {"date": "d2", "equity": 105.0, "contributions": 100.0},
             {"date": "d3", "equity": 305.0, "contributions": 300.0}]
    assert he.depths(he.contribution_free(curve)) == [("d1", 0.0), ("d2", 5.0), ("d3", 5.0)]


def test_b_is_ddd_plus_the_negative_part_of_dpnl():
    read = he.quarter_read("2019Q1", UNHEDGED, HEDGED)
    assert read["dDD"] == pytest.approx(3000.0) and read["dPnl"] == pytest.approx(200.0)
    assert read["B"] == pytest.approx(3000.0)
    worse = _result([0, -2000, -1500, -500, -800, -1000], -1000.0)
    read = he.quarter_read("2019Q1", UNHEDGED, worse)
    assert read["B"] == pytest.approx(read["dDD"] - 900.0)


def test_seal_crossing_settlement_refuses():
    late = _result([0, -1, 0], 0.0, close="2021-07-01")
    with pytest.raises(ValueError, match="seal_crossing"):
        he.quarter_read("2021Q2", late, HEDGED)


def test_a_check_week_document_is_refused_by_the_reader():
    with pytest.raises(ValueError, match="check_document_not_evidence"):
        he.quarter_read("2019Q1", {**UNHEDGED, "decision_weeks": [1]}, HEDGED)


def test_mark_sensitive_on_sign_flip_or_large_difference():
    carry_same = _result([0, -2000, -1500, -500, -800, 100], 100.0)
    carry_flip = _result([0, -5200, -3000, -1000, -2000, 100], 100.0)
    assert he.quarter_read("q", UNHEDGED, HEDGED, carry_same)["mark_sensitive"] is False
    read = he.quarter_read("q", UNHEDGED, HEDGED, carry_flip)
    assert read["mark_sensitive"] is True and read["counts"] is False


def test_books_differ_and_thin_hedgeable_share_do_not_count():
    other = _result([0, -2000, -1500, -500, -800, 100], 100.0, trades=[{"id": "z", "pnl": 1.0, "close_date": "2019-02-01"}])
    assert he.quarter_read("q", UNHEDGED, other)["books_differ"] is True
    unhedged_leg = _result([0, -2000, -1500, -500, -800, 100], 100.0,
                           trades=[{"id": "a", "pnl": 100.0, "close_date": "2019-02-01", "hedge": {"units": 0}}])
    assert he.quarter_read("q", UNHEDGED, unhedged_leg)["under_half_hedgeable"] is True


def _read(q, b, d_pnl=0.0, counts=True):
    return {"quarter": q, "B": b, "dPnl": d_pnl, "counts": counts}


def test_e2_gate_rule():
    ok = [_read("2019Q1", 2500), _read("2019Q2", 2200), _read("2020Q1", 3000)]
    assert he.e2_verdict(ok)["verdict"] == "pass"
    assert he.e2_verdict(ok[:2])["verdict"] == "insufficient"
    assert he.e2_verdict([_read("2019Q1", 2500), _read("2019Q2", 2200), _read("2019Q3", 100)])["verdict"] == "pass"
    low_rest = [_read("2019Q1", 900), _read("2019Q2", 900), _read("2020Q1", 9000)]
    assert he.e2_verdict(low_rest)["verdict"] == "fail"
    ruin = ok[:2] + [_read("2019Q3", 4000, d_pnl=-4001)]
    assert he.e2_verdict(ruin)["verdict"] == "fail"
    only_crash_plus = [_read("2020Q1", 5000), _read("2019Q1", 5000), _read("2019Q2", 5000, counts=False)]
    assert he.e2_verdict(only_crash_plus)["verdict"] == "insufficient"


def test_e1_counts_only_stable_hedged_quarters():
    def blk(h, n=10, stable=True):
        return {"forward_hedge": {"n_hedged": n, "min_books": 5, "hedge_pnl_usd": h},
                "concentration": {"loo_sign_stable_hedge_pnl": stable}}
    good = {"2019Q1": blk(1500), "2019Q2": blk(1200), "2019Q3": blk(2000), "2019Q4": blk(-50, stable=False)}
    assert he.e1_read(None, good)["verdict"] == "pass"
    assert he.e1_read(None, {**good, "2019Q2": blk(400), "2019Q3": blk(500)})["verdict"] == "fail"
    assert he.e1_read(None, {"2019Q1": blk(1500), "2019Q2": blk(1500)})["verdict"] == "insufficient"
    assert he.e1_read(None, {q: blk(-5) for q in ("a", "b", "c")})["verdict"] == "not_a_profit_lever"


def test_e4_is_only_read_from_a_supplied_input():
    assert he.e4_ratio(None, 0.5) == {"status": "not available", "ratio": 0.5}
    low = {"w_dir_shrunk": {"0.90": {"lower": 0.3, "upper": 0.9}}}
    assert he.e4_ratio(low, 0.5)["ratio"] == pytest.approx(0.7)
    small = {"w_dir_shrunk": {"0.90": {"lower": -0.2, "upper": 0.1}}}
    assert he.e4_ratio(small, 0.5)["ratio"] == 1.0
    assert he.e4_ratio({"w_dir_shrunk": {"0.90": {"lower": -0.2, "upper": 0.6}}}, 0.5)["ratio"] == 0.5


def test_bar_runs_on_both_coins_with_the_explicit_verdict():
    gap = {"acc": [(d, 50.0 + (i % 3)) for i, d in enumerate(_days(60))]}
    other = {"acc": [(d, 0.0) for d, _ in gap["acc"]]}
    out = he.bar_reads({"e2_r025": gap, "e2_r100": other}, "e2_r025", k_declared=5, n_boot=200)
    assert out["session"].get("unavailable") is None
    assert out["session"]["k_declared"] == 5 and out["week"]["k_declared"] == 5
    assert out["session"]["adopted_row"]["n_sessions"] == 60
    assert out["week"]["adopted_row"]["n_sessions"] < 60
    assert out["passed"] is (out["session"]["passed"] and out["week"]["passed"])
    flat = he.bar_reads({"e2_r100": other}, "e2_r100", k_declared=1, n_boot=100)
    assert flat["passed"] is False and "unavailable" in flat["session"]


def test_registry_counts_every_attempt_once(tmp_path):
    path = tmp_path / "attempts.jsonl"
    assert he.register_attempts(path, "w1_010_shock10_lp") == 5
    assert he.register_attempts(path, "w1_010_shock10_lp") == 5


def test_read_end_to_end_writes_a_verdict_and_refuses_a_check_week(tmp_path):
    root = tmp_path / "stress"
    form = "w1_010_shock10_lp"

    def put(cohort, scenario, result):
        folder = root / cohort / scenario
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{form}.json").write_text(json.dumps({"result": result}))

    quarters = {"c1": "2019Q1", "c2": "2019Q2", "c3": "2020Q1"}
    for cohort in quarters:
        put(cohort, "control", UNHEDGED)
        for ratio in he.RATIOS:
            name = he.SCENARIO_PREFIX + f"{round(ratio * 100):03d}"
            put(cohort, name, HEDGED)
            put(cohort, name + "_carry", HEDGED)
    verdict = he.read(root, form, quarters, tmp_path / "reg.jsonl", n_boot=200)
    assert verdict["k_declared"] == 5 and verdict["adopted_ratio"] == 0.25
    assert verdict["e4"]["status"] == "not available" and verdict["e3"]["status"] == "not_run"
    json.dumps(verdict, allow_nan=False, default=str)
    put("c1", "control", {**UNHEDGED, "decision_weeks": [1]})
    with pytest.raises(ValueError, match="check_document_not_evidence"):
        he.read(root, form, quarters, tmp_path / "reg2.jsonl", n_boot=200)


def test_main_refuses_an_existing_verdict_path(tmp_path):
    out = tmp_path / "v.json"
    out.write_text("{}")
    assert he.main(["x", "f", "c=2019Q1", "--registry", str(tmp_path / "r"), "--out", str(out)]) == 1
