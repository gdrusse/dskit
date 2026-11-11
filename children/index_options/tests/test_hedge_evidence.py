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


# --- ADR-0312 program mode 4: the program twin read against its unhedged twin, re-levering reads, the timing check ------

def _twin(equity, pnl, trades=None, contracts=10, peak_reserve=1000.0, close="2019-02-01"):
    out = _result(equity, pnl, close=close, trades=trades)
    out.update(contracts=contracts, peak_reserve=peak_reserve)
    return out


PROGRAM_BOOKS = [{"id": "p", "pnl": 120.0, "close_date": "2019-02-01", "hedge": {"units": 4}}]


def test_the_program_twin_reads_b_q_in_the_form_of_e2_and_differing_books_still_count():
    control = _twin([0, -5000, -3000, -1000, -2000, 0], -100.0)
    program = _twin([0, -2000, -1500, -500, -800, 100], 100.0, trades=PROGRAM_BOOKS)
    read = he.program_quarter_read("2019Q1", control, program)
    assert read["dDD"] == pytest.approx(3000.0) and read["dPnl"] == pytest.approx(200.0) and read["B"] == pytest.approx(3000.0)
    assert read["books_differ"] is True and read["counts"] is True, "the program re-chooses books: that is the point"
    worse = _twin([0, -2000, -1500, -500, -800, -1000], -1000.0, trades=PROGRAM_BOOKS)
    assert he.program_quarter_read("2019Q1", control, worse)["B"] == pytest.approx(he.quarter_read("2019Q1", control, worse)["dDD"] - 900.0)


def test_the_re_levering_reads_are_ratios_of_contracts_and_peak_reserve_and_never_gate_by_themselves():
    control = _twin([0, -5000, -3000, -1000, -2000, 0], -100.0, contracts=10, peak_reserve=1000.0)
    calm = _twin([0, -2000, -1500, -500, -800, 100], 100.0, trades=PROGRAM_BOOKS, contracts=15, peak_reserve=2000.0)
    read = he.program_quarter_read("2019Q1", control, calm)
    assert read["contracts_ratio"] == pytest.approx(1.5) and read["peak_reserve_ratio"] == pytest.approx(2.0)
    assert read["re_levered"] is False, "more contracts with the drawdown cut is information, not a failure"
    assert read["counts"] is True


def test_a_program_twin_whose_drawdown_is_not_cut_while_contracts_rose_has_re_levered_and_fails_whatever_its_pnl():
    control = _twin([0, -1000, -500, 0, 0, 0], -100.0, contracts=10)
    levered = _twin([0, -3000, -500, 9000, 9000, 9000], 9000.0, trades=PROGRAM_BOOKS, contracts=30)
    read = he.program_quarter_read("2019Q1", control, levered)
    assert read["dDD"] < 0 and read["dPnl"] > 0 and read["re_levered"] is True
    reads = [read, {**read, "quarter": "2019Q2"}, {**read, "quarter": "2019Q3"}, {**read, "quarter": "2020Q1"}]
    for r in reads:
        r["B"] = 9000.0                                         # a big B from profit alone must not rescue it
    verdict = he.program_verdict(reads)
    assert verdict["verdict"] == "fail" and any("re_levered" in why for why in verdict["reasons"])


def test_the_program_verdict_is_the_e2_rule_with_the_same_numbers():
    ok = [_read("2019Q1", 2500), _read("2019Q2", 2200), _read("2020Q1", 3000)]
    ok = [{**r, "re_levered": False} for r in ok]
    assert he.program_verdict(ok)["verdict"] == "pass" == he.e2_verdict(ok)["verdict"]
    assert he.program_verdict(ok[:2])["verdict"] == "insufficient"
    low = [{**r, "B": 900} for r in ok]
    assert he.program_verdict(low)["verdict"] == "fail"


def test_a_program_read_registers_before_it_reads_and_a_second_ratio_is_a_new_attempt(tmp_path):
    path = tmp_path / "attempts.jsonl"
    assert he.register_attempts(path, "w1") == 14
    assert he.register_program_attempt(path, "w1", ratio=None) == 14, "the declared program twin is not counted twice"
    assert he.register_program_attempt(path, "w1", ratio=0.5) == 15, "a second program ratio is one more declared attempt"
    assert he.register_program_attempt(path, "w1", ratio=0.5) == 15


def test_the_program_twin_goes_through_the_same_bar_with_its_own_cell(tmp_path):
    days = _days(60)
    series = {"program_twin": {"acc": [(d, 40.0 + (i % 4)) for i, d in enumerate(days)]},
              "e2_r100": {"acc": [(d, 0.0) for d in days]}}
    out = he.bar_reads(series, "program_twin", k_declared=14, n_boot=200)
    assert out["session"]["adopted_row"]["cell"] == "program_twin" and out["session"]["k_declared"] == 14


def _timed(week, status, seconds):
    return {"decision_weeks": [week], "decisions": [
        {"date": week, "status": status, "certificates": [{"seconds": seconds}] if seconds is not None else []}]}


def test_the_timing_check_is_the_median_slowdown_and_no_new_timeout_by_hand():
    pairs = [{"week": "2019-01-07", "unhedged": _timed("2019-01-07", "selected", 10.0), "hedged": _timed("2019-01-07", "selected", 14.0)},
             {"week": "2019-01-14", "unhedged": _timed("2019-01-14", "selected", 20.0), "hedged": _timed("2019-01-14", "selected", 29.0)},
             {"week": "2019-01-21", "unhedged": _timed("2019-01-21", "timeout_skipped", 60.0), "hedged": _timed("2019-01-21", "timeout_skipped", 60.0)}]
    out = he.timing_check(pairs)
    assert out["median_unhedged_s"] == 15.0 and out["median_hedged_s"] == 21.5
    assert out["slowdown"] == pytest.approx(21.5 / 15.0) and out["new_timeouts"] == [] and out["verdict"] == "pass"
    edge = [{**pairs[0], "hedged": _timed("2019-01-07", "selected", 16.0)}, pairs[1]]
    assert he.timing_check(edge)["slowdown"] == pytest.approx(22.5 / 15.0) and he.timing_check(edge)["verdict"] == "pass"   # 1.5x is allowed
    slow = [{**pairs[0], "hedged": _timed("2019-01-07", "selected", 17.0)}, pairs[1]]
    assert he.timing_check(slow)["verdict"] == "fail"                    # median(17, 29) = 23 > 1.5 x 15
    new = [*pairs[:2], {"week": "2019-01-28", "unhedged": _timed("2019-01-28", "selected", 5.0),
                        "hedged": _timed("2019-01-28", "timeout_skipped", 60.0)}]
    out = he.timing_check(new)
    assert out["new_timeouts"] == ["2019-01-28"] and out["verdict"] == "fail"


def test_the_timing_check_needs_check_week_documents_and_a_solved_pair():
    plain = _result([0, 1], 1.0)
    with pytest.raises(ValueError, match="timing_needs_check_documents"):
        he.timing_check([{"week": "w", "unhedged": plain, "hedged": plain}])
    only_timeouts = [{"week": "w", "unhedged": _timed("w", "timeout_skipped", 60.0), "hedged": _timed("w", "timeout_skipped", 60.0)}]
    assert he.timing_check(only_timeouts)["verdict"] == "insufficient"


def test_read_program_end_to_end_registers_first_and_carries_every_read(tmp_path):
    quarters = ["2019Q1", "2019Q2", "2019Q3", "2020Q1"]
    controls = {q: _twin([0, -5000, -3000, -1000, -2000, 0], -100.0) for q in quarters}
    programs = {q: _twin([0, -2000, -1500, -500, -800, 100], 100.0, trades=PROGRAM_BOOKS, contracts=12, peak_reserve=1500.0)
                for q in quarters}
    path = tmp_path / "attempts.jsonl"
    pair = {"week": "2019-01-07", "unhedged": _timed("2019-01-07", "selected", 10.0), "hedged": _timed("2019-01-07", "selected", 12.0)}
    out = he.read_program(controls, programs, "w1", path, timing=[pair], n_boot=200)
    assert path.exists() and out["k_declared"] == 1, "the program twin was declared before the read (this registry had none)"
    assert [r["quarter"] for r in out["reads"]] == quarters and all(r["B"] == pytest.approx(3000.0) for r in out["reads"])
    assert out["program"]["verdict"] == "pass" and out["reads"][0]["contracts_ratio"] == pytest.approx(1.2)
    assert out["timing"]["verdict"] == "pass" and out["one_program_ratio_only"] is True
    assert out["bar"]["session"]["adopted_row"]["cell"] == "program_twin" if "adopted_row" in out["bar"]["session"] else True
    with pytest.raises(ValueError, match="check_document_not_evidence"):
        he.read_program(controls, {**programs, "2019Q1": {**programs["2019Q1"], "decision_weeks": ["x"]}}, "w1", path)


def _write(path, result):
    path.write_text(json.dumps({"result": result}))
    return str(path)


def test_the_program_subcommand_reads_the_program_twin_end_to_end(tmp_path, capsys):
    quarters = ["2019Q1", "2019Q2", "2019Q3", "2020Q1"]
    controls = [f"{q}={_write(tmp_path / f'c{q}.json', _twin([0, -5000, -3000, -1000, -2000, 0], -100.0))}" for q in quarters]
    programs = [f"{q}={_write(tmp_path / f'p{q}.json', _twin([0, -2000, -1500, -500, -800, 100], 100.0, trades=PROGRAM_BOOKS, contracts=12, peak_reserve=1500.0))}"
                for q in quarters]
    unhedged = _write(tmp_path / "tu.json", _timed("2019-01-07", "selected", 10.0))
    hedged = _write(tmp_path / "th.json", _timed("2019-01-07", "selected", 12.0))
    registry, out = tmp_path / "attempts.jsonl", tmp_path / "verdict.json"
    argv = ["program", "w1", "--controls", *controls, "--programs", *programs, "--timing", f"2019-01-07={unhedged},{hedged}",
            "--registry", str(registry), "--out", str(out)]
    assert he.main(argv) == 0
    verdict = json.loads(out.read_text())
    assert verdict["mode"] == "program" and verdict["program"]["verdict"] == "pass" and verdict["timing"]["verdict"] == "pass"
    assert [r["quarter"] for r in verdict["reads"]] == quarters and registry.exists()
    assert he.main(argv) == 1, "an existing verdict file is never overwritten"
    mismatched = ["program", "w1", "--controls", controls[0], "--programs", programs[1], "--registry", str(registry),
                  "--out", str(tmp_path / "other.json")]
    assert he.main(mismatched) == 1 and not (tmp_path / "other.json").exists()


def test_a_mark_sensitive_quarter_is_uncounted_and_never_re_levers_the_verdict():
    control = _twin([0, -1000, -500, 0, 0, 0], -100.0, contracts=10)
    levered = _twin([0, -3000, -500, 9000, 9000, 9000], 9000.0, trades=PROGRAM_BOOKS, contracts=30)
    carry = _twin([0, -1000, -500, 9000, 9000, 9000], 9000.0, trades=PROGRAM_BOOKS, contracts=30)
    read = he.program_quarter_read("2019Q1", control, levered, carry)
    assert read["mark_sensitive"] is True and read["counts"] is False and read["re_levered"] is False


def test_main_with_an_explicit_empty_argv_is_a_usage_error_not_sys_argv(monkeypatch):
    import sys

    from tools import hedge_evidence

    monkeypatch.setattr(sys, "argv", ["hedge_evidence.py", "SHOULD", "NOT", "BE", "READ"])
    with pytest.raises(SystemExit) as exc:
        hedge_evidence.main([])
    assert exc.value.code == 2

_G5_IDS = ("w1_010_shock10_lp", "w1_003_shock10_lp")


def _g5_receipt():
    def rows():
        return [
            {"date": "2020-01-06", "status": "cash"},
            {"date": "2020-01-13", "status": "cash", "selection": {}, "certificates": [{}]},
            {"date": "2020-01-20", "status": "selected", "selection": {}, "certificates": [{}]},
            {"date": "2020-01-27", "status": "cash", "selection": {}, "certificates": [{}]},
            {"date": "2020-02-03", "status": "selected", "selection": {}, "certificates": [{}]},
            {"date": "2020-02-10", "status": "timeout_skipped"},
            {"date": "2020-02-17", "status": "timeout_skipped"},
        ]

    return {"complete": True, "results": [
        {"id": method, "status": "completed", "replay_parity": True,
         "result": {"decisions": rows()}}
        for method in _G5_IDS
    ]}


def test_g5_selects_earliest_three_joint_solves_and_union_of_timeouts():
    packet = _g5_receipt()
    first, second = [item["result"]["decisions"] for item in packet["results"]]
    second[5] = {"date": "2020-02-10", "status": "cash"}
    first.reverse()
    for row in first + second:
        row["economic_poison"] = {"value": "must never appear"}
    output = he.g5_status_weeks(packet, "2020Q1")
    assert output["weeks"] == [
        "2020-01-13", "2020-01-20", "2020-01-27", "2020-02-10", "2020-02-17",
    ]
    assert all(set(row) == {"method_id", "date", "status", "solve_present"} for row in output["rows"])
    assert all(row["solve_present"] is False for row in output["rows"] if row["date"] == "2020-01-06")


@pytest.mark.parametrize("damage, expected", [
    ("too_few", "fewer_than_three_joint_solves"),
    ("duplicate", "g5_duplicate_decision"),
    ("mismatch", "g5_calendar_mismatch"),
    ("bad_date", "g5_decision_date"),
    ("malformed", "g5_solve_evidence"),
    ("replay", "g5_incomplete_replay"),
    ("incomplete", "g5_incomplete_close"),
    ("absent", "g5_method_missing"),
])
def test_g5_refuses_bad_status_evidence(damage, expected):
    packet = _g5_receipt()
    first = packet["results"][0]
    rows = first["result"]["decisions"]
    if damage == "too_few":
        for row in rows[1:4]:
            row.update(status="cash", selection=None, certificates=None)
    elif damage == "duplicate":
        rows.append(rows[0])
    elif damage == "mismatch":
        rows.pop()
    elif damage == "bad_date":
        rows[0]["date"] = "2021-07-01"
    elif damage == "malformed":
        rows[1]["certificates"] = ["POISON"]
    elif damage == "replay":
        first["replay_parity"] = 1
    elif damage == "incomplete":
        packet["complete"] = False
    elif damage == "absent":
        packet["results"].pop()
    with pytest.raises(ValueError, match=expected) as exc:
        he.g5_status_weeks(packet, "2020Q1")
    assert "POISON" not in str(exc.value)


@pytest.mark.parametrize("damage, expected", [
    (None, None),
    ("document", "g5_document_pin"),
    ("identity", "g5_close_attestation"),
    ("digest", "g5_close_attestation"),
    ("bytes", "g5_close_artifact"),
    ("incomplete", "g5_close_attestation"),
    ("carry", "g5_close_attestation"),
])
def test_g5_uses_only_the_attested_close(tmp_path, damage, expected):
    from hashlib import sha256

    from dskit.pipeline.document import NodeSpec, PipelineDocument

    document = PipelineDocument(name="g5-test", pipeline={"close_packet": NodeSpec(uses="synthetic")})
    frozen = json.dumps(document.to_obj()).encode()
    run = tmp_path / "run"
    (run / "nodes").mkdir(parents=True)
    (run / "artifacts/json").mkdir(parents=True)
    receipt = json.dumps(_g5_receipt()).encode()
    digest = sha256(receipt).hexdigest()
    relative = f"artifacts/json/{digest}.json"
    manifest = {"path": relative, "sha256": digest, "bytes": len(receipt), "media_type": "application/json"}
    (run / relative).write_bytes(receipt if damage != "bytes" else b"secret")
    (run / "config.json").write_bytes(frozen)
    (tmp_path / "source.json").write_bytes(frozen if damage != "document" else b"secret")
    (run / "result.json").write_text(json.dumps({"state": "error" if damage == "incomplete" else "ran"}))
    (run / "resolved.json").write_text(json.dumps({"document_hash": document.hash}))
    (run / "nodes/01-close_packet.json").write_text(json.dumps({
        "node": "close_packet", "status": "ok", "document_hash": document.hash,
        "outputs": {"receipt": manifest},
    }))
    (run / "carry.json").write_text(json.dumps(
        {} if damage == "carry" else {"close_packet": {"receipt": manifest}},
    ))
    pin = ("source.json", sha256(frozen).hexdigest(), "run",
           "0" * 64 if damage == "identity" else document.hash,
           "0" * 64 if damage == "digest" else digest)
    if expected:
        with pytest.raises(ValueError, match=expected):
            he._g5_attested_weeks(tmp_path, pin, "2020Q1")
    else:
        output = he._g5_attested_weeks(tmp_path, pin, "2020Q1")
        assert output["close_sha256"] == digest
        assert output["weeks"] == ["2020-01-13", "2020-01-20", "2020-01-27", "2020-02-10", "2020-02-17"]

def test_g5_source_pins_match_the_pre_observation_adr():
    adr = Path(he.__file__).resolve().parents[3] / "docs/architecture/decision-log.md"
    declaration = adr.read_text().split("**ADR-0313 G5 status-only week selection declaration", 1)[1]
    declaration = declaration.split("**ADR-0313 G5 status-only declaration receipt", 1)[0]
    for quarter, source in he._G5_SOURCES.items():
        line, = [line for line in declaration.splitlines() if line.startswith(f"- {quarter} ")]
        assert all(f"`{pin}`" in line for pin in source)


def test_g5_main_refusal_prints_no_partial_rows(monkeypatch, capsys):
    def reader(root, source, quarter):
        if quarter == "2020Q1":
            raise ValueError("g5_close_attestation")
        return {"rows": ["poison"], "weeks": []}

    monkeypatch.setattr(he, "_g5_attested_weeks", reader)
    assert he.main(["g5-status"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "2020Q1: g5_close_attestation\n"
