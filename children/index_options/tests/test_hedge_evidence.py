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
        return {"quarter": None, "forward_hedge": {"n_hedged": n, "min_books": 5, "hedge_pnl_usd": h},
                "concentration": {"loo_sign_stable_hedge_pnl": stable}}
    def labelled(blocks):
        return {q: {**b, "quarter": q} for q, b in blocks.items()}

    good = labelled({"2019Q1": blk(1500), "2019Q2": blk(1200), "2019Q3": blk(2000), "2019Q4": blk(-50, stable=False)})
    assert he.e1_read(None, good)["verdict"] == "pass"
    assert he.e1_read(None, {**good, **labelled({"2019Q2": blk(400), "2019Q3": blk(500)})})["verdict"] == "fail"
    assert he.e1_read(None, labelled({"2019Q1": blk(1500), "2019Q2": blk(1500)}))["verdict"] == "insufficient"
    assert he.e1_read(None, labelled({q: blk(-5) for q in ("a", "b", "c")}))["verdict"] == "not_a_profit_lever"
    with pytest.raises(ValueError, match="e1_quarter_mismatch"):
        he.e1_read(None, {"2019Q1": {**blk(1500), "quarter": "2019Q2"}})


# The shape the edge walk writes (dskit/pipeline/libs/edge_walk.py `_e4_input`, commit afced1f): level keys f"{lvl:g}".
def _walk_e4(lcb, ucb, warm=True):
    return {"warm": warm, "g_eff_blocks_13": 9.0, "n_eff": 120.0, "levels": [0.9],
            "w_dir": {"value": 0.4, "se": 0.1, "df": 8.0, "p_boot_0": 0.2, "bands": {"0.9": {"lcb": 0.2, "ucb": 0.6}}},
            "w_dir_shrunk": {"value": 0.1, "se": 0.1, "df": 8.0, "p_boot_0": None, "bands": {"0.9": {"lcb": lcb, "ucb": ucb}}}}


def test_e4_consumes_the_walks_e4_input_shape_exactly():
    assert he.e4_ratio(None, 0.5) == {"status": "E4 not available: pool not warm", "ratio": 0.5}
    cold = {**_walk_e4(0.0, 0.0, warm=False), "w_dir_shrunk": None}
    assert he.e4_ratio(cold, 0.5)["status"] == "E4 not available: pool not warm"
    assert he.e4_ratio({"direction": {"e4_input": None}}, 0.5)["status"].startswith("E4 not available")
    assert he.e4_ratio(_walk_e4(0.3, 0.9), 0.5)["ratio"] == pytest.approx(0.7)
    assert he.e4_ratio(_walk_e4(1.5, 2.0), 0.5)["ratio"] == 0.0                       # clipped
    assert he.e4_ratio(_walk_e4(-0.2, 0.1), 0.5) == {"status": "upper_band_small", "ratio": 1.0}
    assert he.e4_ratio(_walk_e4(-0.2, 0.6), 0.5) == {"status": "inconclusive", "ratio": 0.5}
    assert he.e4_ratio({"direction": {"e4_input": _walk_e4(0.3, 0.9)}}, 0.5)["ratio"] == pytest.approx(0.7), \
        "the walk summary holding it is read the same"


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
    assert he.register_attempts(path, "w1_010_shock10_lp") == 14      # 4 ratios x 3 conditions, the program twin, E1
    assert he.register_attempts(path, "w1_010_shock10_lp") == 14
    assert he.register_attempts(path, "another") == 14


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
    assert verdict["k_declared"] == 14 and verdict["adopted_ratio"] == 0.25
    assert verdict["e4"]["status"].startswith("E4 not available") and verdict["e3"]["status"] == "not_run"
    json.dumps(verdict, allow_nan=False, default=str)
    put("c1", "control", {**UNHEDGED, "decision_weeks": [1]})
    with pytest.raises(ValueError, match="check_document_not_evidence"):
        he.read(root, form, quarters, tmp_path / "reg2.jsonl", n_boot=200)


def test_main_refuses_an_existing_verdict_path(tmp_path):
    out = tmp_path / "v.json"
    out.write_text("{}")
    assert he.main(["x", "f", "c=2019Q1", "--registry", str(tmp_path / "r"), "--out", str(out)]) == 1


# --- the statistic, pinned by hand (the reviewer's mutations: reset peak, increments, ruin scope, coin, lower, key) ---

def test_d_t_is_the_session_gap_between_the_two_depths_by_hand():
    # unhedged depths 0, 5000, 3000, 1000, 2000, 0 (peak 0 never reset); hedged 0, 2000, 1500, 500, 800, 0 (peak 100 at the end)
    read = he.quarter_read("2019Q1", UNHEDGED, HEDGED)
    assert [d for _, d in read["d_t"]] == [0.0, 3000.0, 1500.0, 500.0, 1200.0, 0.0]
    assert [day for day, _ in read["d_t"]] == _days(6)
    assert read["dDD"] == 3000.0 and read["dPnl"] == 200.0 and read["B"] == 3000.0
    # not the increments (which telescope to the terminal gap): the series is a LEVEL, the mean is positive
    assert sum(d for _, d in read["d_t"]) == 6200.0


def test_the_peak_runs_from_the_accounts_first_session_and_never_resets_on_a_later_high():
    series = [("a", 0.0), ("b", -100.0), ("c", -40.0), ("d", 0.0), ("e", -10.0), ("f", -300.0), ("g", -250.0)]
    assert [d for _, d in he.depths(series)] == [0.0, 100.0, 40.0, 0.0, 10.0, 300.0, 250.0]
    up = [("a", 0.0), ("b", 50.0), ("c", 20.0), ("d", 60.0), ("e", 10.0)]
    assert [d for _, d in he.depths(up)] == [0.0, 0.0, 30.0, 0.0, 50.0]


def test_the_ruin_guard_reads_every_quarter_not_only_the_counting_ones():
    reads = [_read("2019Q1", 2500), _read("2019Q2", 2200), _read("2019Q3", 2300), _read("2019Q4", 0, d_pnl=-4001, counts=False)]
    verdict = he.e2_verdict(reads)
    assert verdict["verdict"] == "fail" and verdict["ruin_breaches"] == ["2019Q4"]
    assert he.e2_verdict(reads[:3])["verdict"] == "pass"
    assert he.e2_verdict(reads[:3] + [_read("2019Q4", 0, d_pnl=-4000, counts=False)])["verdict"] == "pass", "the line is strict"


def _spy_max_bar(monkeypatch, row_for):
    calls = []

    def fake(cells, n_boot, seed, alpha, k_declared):
        calls.append(cells)
        return {"pass_mark": 3.0, "c_star": 2.5, "k": len(cells), "k_declared": k_declared, "notes": [],
                "rows": [row_for(len(calls), name) for name in sorted(cells)]}

    monkeypatch.setattr(he, "max_bar", fake)
    return calls


def _row(name, **over):
    return {"cell": name, "t": 4.0, "adj_p": 0.01, "mean": 10.0, "lower": 2.0, **over}


def test_the_bar_is_called_on_both_coins_with_the_account_in_every_key(monkeypatch):
    calls = _spy_max_bar(monkeypatch, lambda call, name: _row(name))
    two = {"acc1": [(d, 10.0 + i) for i, d in enumerate(_days(14))], "acc2": [(d, 5.0) for d in _days(14)]}
    out = he.bar_reads({"x": two}, "x", k_declared=14, n_boot=100)
    assert len(calls) == 2, "per session AND per (account, iso week)"
    per_session, per_week = calls[0]["x"], calls[1]["x"]
    assert len(per_session) == 28 and {k[0] for k in per_session} == {"acc1", "acc2"}, "two accounts share dates, two keys"
    weeks = {(a, w) for a, w in per_week}
    assert len(per_week) == len(weeks) == 4 and all(isinstance(w, tuple) for _, w in per_week)
    assert sum(v[0] for v in per_week.values()) == pytest.approx(sum(v[0] for v in per_session.values()))
    assert sum(v[1] for v in per_week.values()) == 28
    assert out["passed"] is True and out["session"]["k_declared"] == 14


def test_the_adopted_attempt_must_clear_the_week_coin_as_well_as_the_session_coin(monkeypatch):
    _spy_max_bar(monkeypatch, lambda call, name: _row(name) if call == 1 else _row(name, t=2.0))
    gaps = {"acc": [(d, 1.0) for d in _days(7)]}
    out = he.bar_reads({"x": gaps}, "x", k_declared=1, n_boot=100)
    assert out["session"]["passed"] is True and out["week"]["passed"] is False and out["passed"] is False


@pytest.mark.parametrize("over", [{"t": 2.99}, {"adj_p": 0.0501}, {"mean": 0.0}, {"lower": 0.0}, {"lower": -1.0}, {"adj_p": None}])
def test_each_term_of_the_explicit_verdict_blocks_a_pass(over):
    assert he._clears(_row("x"), 3.0, 0.05) is True
    assert he._clears(_row("x", **over), 3.0, 0.05) is False
    assert he._clears(None, 3.0, 0.05) is False


def test_a_real_bar_passes_a_steady_gain_and_fails_a_noise_cell():
    # two accounts, 40 sessions each: a steady depth gap near 1000 against a cell whose mean is zero
    days = _days(40)
    steady = {a: [(d, 1000.0 + 25.0 * (i % 5)) for i, d in enumerate(days)] for a in ("a1", "a2")}
    noise = {a: [(d, 100.0 * (-1) ** i) for i, d in enumerate(days)] for a in ("a1", "a2")}
    out = he.bar_reads({"steady": steady, "noise": noise}, "steady", k_declared=14, n_boot=500, also=("noise",))
    assert out["session"]["passed"] and out["week"]["passed"] and out["passed"]
    assert out["session"]["adopted_row"]["lower"] > 0 and out["session"]["pass_mark"] >= 3.0
    assert out["also"]["noise"]["session"]["passed"] is False
    assert he.bar_reads({"steady": steady, "noise": noise}, "noise", k_declared=14, n_boot=500)["passed"] is False


# --- E1 end to end through the books.csv route -------------------------------------------------------------------

BOOK_HEADER = "method,quarter,id,symbol,expiry,pnl,forward_hedge_status,hedge_pnl,forward_hedged_pnl,delta_f_sh,forward,s_fill,s_settle"


def _books_csv(path, rows):
    path.write_text("\n".join([BOOK_HEADER, *rows]) + "\n")
    return path


def test_e1_reads_the_books_csv_and_greeks_summary_through_the_bar(tmp_path):
    form = "w1_010_shock10_lp"
    root = tmp_path / "stress"
    quarters = {"c1": "2019Q1", "c2": "2019Q2", "c3": "2020Q1"}
    for cohort in quarters:
        for scenario in ["control", *[he.SCENARIO_PREFIX + f"{round(r * 100):03d}{t}" for r in he.RATIOS for t in ("", "_carry")]]:
            folder = root / cohort / scenario
            folder.mkdir(parents=True)
            (folder / f"{form}.json").write_text(json.dumps({"result": UNHEDGED if scenario == "control" else HEDGED}))
    greeks, books = {}, {}
    for i, quarter in enumerate(quarters.values()):
        summary = {"methods": {form: {"direction_read": {"quarter": quarter, "forward_hedge": {
            "n_hedged": 12, "min_books": 5, "hedge_pnl_usd": 1500.0 + 100 * i},
            "concentration": {"loo_sign_stable_hedge_pnl": True}}}}}
        greeks[quarter] = tmp_path / f"{quarter}.json"
        greeks[quarter].write_text(json.dumps(summary))
        books[quarter] = _books_csv(tmp_path / f"{quarter}.csv", [
            f"{form},{quarter},a,X,2019-01-10,-10,ok,300.0,290.0,5.0,100.0,100.0,103.0",
            f"{form},{quarter},b,X,2019-01-11,-10,ok,-50.0,-60.0,5.0,100.0,100.0,99.0",
            f"{form},{quarter},c,X,2019-01-11,-10,htb,,,,,,",
            f"other,{quarter},z,X,2019-01-11,-10,ok,999.0,0,5.0,100.0,100.0,99.0"])
    verdict = he.read(root, form, quarters, tmp_path / "reg.jsonl", greeks={q: p for q, p in greeks.items()},
                      books=books, n_boot=200)
    assert verdict["e1"]["verdict"] == "pass" and verdict["e1"]["counting"] == list(quarters.values())
    assert verdict["bar"]["also"]["e1"]["session"]["row"]["n_rows"] == 18, "3 accounts x 6 sessions, zeros included"
    assert verdict["e1b"] == {"status": "E1b unattached: needs beta, basket_return from mio_greeks"}
    series = he.e1_series(he.books_from_csv(books["2019Q1"], form), _days(6))
    assert dict(series)["2019-01-10"] == 300.0 and dict(series)["2019-01-11"] == -50.0 and dict(series)["2019-01-07"] == 0.0


def test_a_failed_scenario_is_a_coded_refusal_not_a_type_error(tmp_path):
    form = "f"
    for scenario in ("control", he.SCENARIO_PREFIX + "025", he.SCENARIO_PREFIX + "025_carry"):
        folder = tmp_path / "c" / scenario
        folder.mkdir(parents=True)
        (folder / f"{form}.json").write_text(json.dumps({"result": None, "error": "boom"}))
    with pytest.raises(ValueError, match="scenario_failed"):
        he.read(tmp_path, form, {"c": "2019Q1"}, tmp_path / "r.jsonl", n_boot=100)


def test_the_carried_mark_share_is_reported_and_e3_says_why():
    hedged = {**HEDGED, "hedge": {"counts": {"mark_forward_carried": 2, "hedge_marks": 8}}}
    read = he.quarter_read("q", UNHEDGED, hedged)
    assert read["mark_forward_carried_share"] == 0.25
    assert he.quarter_read("q", UNHEDGED, HEDGED)["mark_forward_carried_share"] is None
