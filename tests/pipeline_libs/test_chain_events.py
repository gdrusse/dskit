"""ChainEventDetector and ChainEventCalibrator (ADR-0265 node 5, H2): events labelled from the chain itself.

The feature tables are synthetic and deterministic: a front and a back implied vol, a front straddle
ratio and a spot per symbol and business day, with crushes and gaps PLANTED on known days. Every
expected date and number is computed here from the plan, never read back from the node.
"""

import copy
import json
import random
from datetime import date, timedelta

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.libs import chain_events
from dskit.pipeline.libs.chain_events import RULE_NAMES, ChainEventCalibrator, ChainEventDetector
from dskit.pipeline.node import NodeContext

START = date(2018, 1, 1)
N = 260
FRONT, BACK = 7, 30

FIELDS = {"symbol_field": "symbol", "date_field": "date", "target_field": "target_dte", "iv_field": "atm_iv",
          "straddle_field": "straddle_ratio", "spot_field": "spot", "status_field": "status", "status_ok": "ok",
          "front_target": FRONT, "back_target": BACK, "end_before": "2026-01-01"}
RULES = {"window": 40, "min_history": 15, "crush_min": 0.15, "crush_z_min": 4.0, "spread_drop_min": 0.04,
         "refractory": 50, "max_market_share": 0.5, "min_symbols_for_guard": 5,
         "max_gap_days": 5, "entry_lead": 3, "exit_lag": 1, "scale_floor": 0.005}
PARAMS = {**FIELDS, **RULES}


def sessions(n=N, start=START):
    """The first ``n`` business days from ``start`` (ISO text)."""
    out, day = [], start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def table(symbol, plan=None, days=None, seed=0, move_sd=0.01, iv_noise=0.004):
    """Feature rows (front and back target per day) for one symbol.

    ``plan`` maps a day index to ``{"crush": bool, "gap": float, "term": bool}``: at index k the front vol
    falls from its run-up level (0.50) to 0.30, the back falls with it only when ``term`` is False (a parallel
    fall, so the front-minus-back spread does NOT collapse) and otherwise by just 0.04, and the spot moves by ``gap``.
    """
    days = days or sessions()
    rng = random.Random(f"{symbol}-{seed}")
    plan = plan or {}
    spot, rows = 100.0, []
    for i, day in enumerate(days):
        ev = plan.get(i)
        pre = any(k - 8 <= i < k and p.get("crush", True) for k, p in plan.items())
        front = 0.30 + rng.gauss(0, iv_noise) + (0.20 if pre else 0.0)
        bump = 0.0
        if pre:
            bump = 0.04 if plan[next(k for k in plan if k - 8 <= i < k)].get("term", True) else 0.20
        back = 0.28 + rng.gauss(0, iv_noise) + bump
        move = rng.gauss(0, move_sd)
        if ev is not None:
            move = ev.get("gap", 0.06)
        spot *= 1 + move
        rows.append({"symbol": symbol, "date": day, "target_dte": FRONT, "atm_iv": front,
                     "straddle_ratio": front * 0.1, "spot": spot, "status": "ok"})
        rows.append({"symbol": symbol, "date": day, "target_dte": BACK, "atm_iv": back,
                     "straddle_ratio": back * 0.2, "spot": spot, "status": "ok"})
    return rows


def run(rows, **over):
    params = {k: v for k, v in dict(PARAMS, **over).items() if v is not ...}
    ctx = NodeContext(name="t", asof="2025-12-31", run_dir="/nonexistent")
    return ChainEventDetector("events", params).run(ctx, {"records": rows})


# -- the planted crush ---------------------------------------------------------------------------------

def test_planted_crush_is_labelled_on_the_crush_day_with_entry_and_exit():
    days = sessions()
    rows = table("AAA", {100: {"gap": 0.06}})
    out = run(rows)
    assert [e["event_date"] for e in out["records"]] == [days[100]]
    event = out["records"][0]
    assert event["symbol"] == "AAA"
    assert event["entry_date"] == days[97] and event["exit_date"] == days[101]
    assert event["front_iv_before"] > 0.45 and event["front_iv_after"] < 0.36
    assert event["back_iv_before"] > event["back_iv_after"]
    assert event["implied_move"] == pytest.approx(event["front_iv_before"] * 0.1)
    assert event["realized_move"] == pytest.approx(0.06)
    assert event["crush"] < -0.15 and event["crush_z"] < -4 and event["spread_drop"] > 0.04
    assert set(event) == {"symbol", "event_date", "entry_date", "exit_date", "front_iv_before", "front_iv_after",
                          "back_iv_before", "back_iv_after", "implied_move", "realized_move", "crush", "crush_z",
                          "spread_drop"}                    # no selection score, no move z: outcomes select nothing
    assert out["summary"]["events"] == 1 and out["symbols_without_events"] == []


def test_the_event_date_is_the_session_whose_open_holds_the_move():
    """The crush is first visible at the close of the day the announcement's gap trades: event_date is that day."""
    out = run(table("AAA", {120: {}}))
    days = sessions()
    assert out["records"][0]["event_date"] == days[120]
    assert out["records"][0]["entry_date"] == days[117]


def test_quiet_series_has_no_event_and_is_listed_as_a_zero_symbol():
    out = run(table("AAA") + table("BBB", {100: {}}))
    assert [e["symbol"] for e in out["records"]] == ["BBB"]
    assert out["summary"]["symbols"] == 2 and out["symbols_without_events"] == ["AAA"]


def test_detection_does_not_read_the_realized_move():
    """A crush with no underlying move is an event, and the spot column may be absent without changing detection."""
    flat = run(table("AAA", {100: {"gap": 0.0}}))
    big = run(table("AAA", {100: {"gap": 0.20}}))
    assert [e["event_date"] for e in flat["records"]] == [e["event_date"] for e in big["records"]] == [sessions()[100]]
    assert flat["records"][0]["realized_move"] == pytest.approx(0.0)
    assert flat["records"][0]["crush_z"] == big["records"][0]["crush_z"]
    rows = table("AAA", {100: {"gap": 0.20}})
    for r in rows:
        r["spot"] = None
    blind = run(rows)
    assert [e["event_date"] for e in blind["records"]] == [sessions()[100]]
    assert blind["records"][0]["realized_move"] is None and "no_spot" not in blind["summary"]["missing"]


def test_crush_whose_term_spread_does_not_collapse_is_rejected_by_the_term_rule():
    out = run(table("AAA", {100: {"term": False}}))
    assert out["records"] == [] and out["summary"]["rejected_by"]["term"] == 1


def test_a_small_iv_drop_is_rejected_by_the_crush_rule():
    out = run(table("AAA", {100: {}}), crush_min=0.9)
    assert out["records"] == [] and out["summary"]["rejected_by"]["crush"] >= 1


def test_a_drop_that_is_ordinary_for_the_symbol_is_rejected_by_the_z_rule():
    out = run(table("AAA", {100: {}}), crush_z_min=1e6)
    assert out["records"] == [] and out["summary"]["rejected_by"]["crush_z"] >= 1


# -- the cross-sectional guard -----------------------------------------------------------------------------

def test_a_market_wide_crush_is_rejected_for_every_symbol():
    rows = [r for s in "ABCDEFGHIJ" for r in table(s, {100: {}})]
    out = run(rows)
    assert out["records"] == []
    assert out["summary"]["rejected_by"]["market_wide"] == 10
    assert out["symbols_without_events"] == list("ABCDEFGHIJ")


def test_a_lone_crush_among_many_symbols_passes_the_guard():
    rows = [r for s in "ABCDEFGHIJ" for r in table(s, {100: {}} if s == "A" else None)]
    out = run(rows)
    assert [e["symbol"] for e in out["records"]] == ["A"]


def test_the_share_cap_is_a_param_and_the_guard_needs_enough_symbols():
    rows = [r for s in "ABCDEFGHIJ" for r in table(s, {100: {}} if s in "ABC" else None)]
    assert len(run(rows)["records"]) == 3                       # 3 of 10 = 0.3 <= 0.5
    assert run(rows, max_market_share=0.25)["records"] == []    # 0.3 > 0.25
    assert len(run(rows, max_market_share=0.25, min_symbols_for_guard=11)["records"]) == 3


def test_a_market_rejected_day_does_not_start_the_refractory_window():
    rows = [r for s in "ABCDEFGHIJ" for r in table(s, {100: {}, 110: {}} if s == "A" else {100: {}})]
    out = run(rows, refractory=50)
    # day 100 fires for all ten and is rejected market-wide; day 110 fires for A alone and is kept
    assert [(e["symbol"], e["event_date"]) for e in out["records"]] == [("A", sessions()[110])]
    assert out["summary"]["rejected_by"]["market_wide"] == 10 and out["summary"]["rejected_by"]["refractory"] == 0


# -- refractory ---------------------------------------------------------------------------------------------

def test_refractory_keeps_the_first_of_two_close_events():
    out = run(table("AAA", {100: {}, 125: {}}))
    days = sessions()
    assert [e["event_date"] for e in out["records"]] == [days[100]]
    assert out["summary"]["rejected_by"]["refractory"] == 1


def test_events_a_full_window_apart_are_both_kept():
    out = run(table("AAA", {80: {}, 150: {}}))
    days = sessions()
    assert [e["event_date"] for e in out["records"]] == [days[80], days[150]]


def test_refractory_is_counted_in_sessions_per_symbol():
    rows = table("AAA", {100: {}}) + table("BBB", {110: {}})
    assert len(run(rows)["records"]) == 2


# -- missing data -------------------------------------------------------------------------------------------

def test_missing_front_back_and_history_are_counted_never_filled():
    rows = table("AAA", {100: {}})
    for r in rows:
        if r["date"] == sessions()[50] and r["target_dte"] == FRONT:
            r["atm_iv"] = None
        if r["date"] == sessions()[60] and r["target_dte"] == BACK:
            r["status"] = "no_expiry"
            r["atm_iv"] = None
    out = run(rows)
    s = out["summary"]
    assert s["missing"]["no_front"] >= 1 and s["missing"]["no_back"] == 2          # the day itself and the day after (its previous session)
    assert s["missing"]["no_prev"] >= 1                    # the day after the missing front
    assert s["missing"]["insufficient_history"] > 0       # the first sessions
    assert [e["event_date"] for e in out["records"]] == [sessions()[100]]


def test_a_missing_event_day_front_iv_yields_no_event_and_no_exception():
    rows = table("AAA", {100: {}})
    for r in rows:
        if r["date"] == sessions()[100] and r["target_dte"] == FRONT:
            r["atm_iv"] = None
    assert run(rows)["records"] == []


def test_a_calendar_gap_before_the_event_day_is_refused_as_a_gap():
    days = sessions()
    keep = [d for i, d in enumerate(days) if not 95 <= i <= 99]          # a week of sessions is absent
    rows = [r for r in table("AAA", {100: {}}, days=days) if r["date"] in keep]
    out = run(rows)
    assert out["records"] == [] and out["summary"]["missing"]["gap"] == 1


def test_entry_and_exit_are_none_when_the_table_does_not_reach_them():
    rows = table("AAA", {N - 1: {}})
    out = run(rows)
    assert out["records"][0]["exit_date"] is None and out["summary"]["exit_unavailable"] == 1
    out = run(table("AAA", {100: {}}), entry_lead=500)
    assert out["records"][0]["entry_date"] is None and out["summary"]["entry_unavailable"] == 1


def test_a_non_ok_status_is_a_missing_value():
    rows = table("AAA", {100: {}})
    for r in rows:
        if r["date"] == sessions()[100] and r["target_dte"] == FRONT:
            r["status"] = "no_forward"
    assert run(rows)["records"] == []


# -- counters, determinism, causality ---------------------------------------------------------------------

def test_the_counters_account_for_every_session_exactly_once():
    rows = [r for s in "ABCDEFGH" for r in table(s, {100: {}, 130: {}} if s in "AB" else {100: {}} if s == "C" else None)]
    s = run(rows)["summary"]
    sessions_total = 8 * N
    assert sessions_total == s["sessions"]
    assert s["sessions"] == sum(s["missing"].values()) + s["candidate_days"]
    assert s["candidate_days"] == s["fired"] + sum(s["rejected_by"][k] for k in ("crush", "crush_z", "term"))
    assert s["fired"] == s["events"] + s["rejected_by"]["market_wide"] + s["rejected_by"]["refractory"]
    assert s["events"] == 3 and s["rejected_by"]["refractory"] == 2


def test_the_numeric_counters_hold_no_lists_and_the_quiet_symbols_are_reported_apart():
    out = run(table("AAA") + table("BBB", {100: {}}))
    flat = [v for k, v in out["summary"].items() if k not in ("missing", "rejected_by", "rules")]
    assert all(isinstance(v, int) for v in flat)
    assert out["symbols_without_events"] == ["AAA"] and out["summary"]["symbols_without_events_count"] == 1


def test_the_result_is_independent_of_input_order():
    rows = [r for s in "ABC" for r in table(s, {100: {}} if s == "A" else {120: {}})]
    shuffled = rows[:]
    random.Random(7).shuffle(shuffled)
    assert run(rows) == run(shuffled)
    assert run(rows) == run(copy.deepcopy(rows))


def test_the_input_is_not_mutated():
    rows = table("AAA", {100: {}})
    before = copy.deepcopy(rows)
    run(rows)
    assert rows == before


def test_detection_uses_only_data_up_to_the_event_day():
    days = sessions()
    rows = table("AAA", {100: {}})
    full = run(rows)["records"][0]
    cut = [r for r in rows if r["date"] <= days[100]]
    early = run(cut)["records"][0]
    assert early["exit_date"] is None
    assert {k: v for k, v in early.items() if k != "exit_date"} == {k: v for k, v in full.items() if k != "exit_date"}
    scrambled = [dict(r, atm_iv=9.0, spot=1e6) if r["date"] > days[100] else r for r in rows]
    assert run(scrambled)["records"][0]["event_date"] == days[100]
    assert run(scrambled)["records"][0]["crush_z"] == full["crush_z"]


def test_a_repeated_row_is_refused():
    rows = table("AAA", {100: {}})
    with pytest.raises(ValueError, match="repeats"):
        run(rows + [rows[0]])


def test_a_row_at_or_after_end_before_is_refused():
    with pytest.raises(ValueError, match="end_before"):
        run(table("AAA", {100: {}}), end_before="2018-06-01")


def test_empty_input_is_refused():
    with pytest.raises(ValueError, match="empty"):
        run([])


def test_a_malformed_row_is_refused_naming_the_field():
    rows = table("AAA", {100: {}})
    rows[3]["atm_iv"] = "high"
    with pytest.raises(ValueError, match="atm_iv"):
        run(rows)
    rows = table("AAA", {100: {}})
    del rows[3]["spot"]
    with pytest.raises(ValueError, match="spot"):
        run(rows)


# -- validation ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    {"nonsense": 1}, {"window": 1}, {"min_history": 0}, {"crush_min": -0.1}, {"crush_z_min": 0},
    {"refractory": 0}, {"min_symbols_for_guard": 1}, {"max_market_share": 0}, {"max_market_share": 1.5},
    {"entry_lead": -1}, {"exit_lag": 0}, {"front_target": 30, "back_target": 30}, {"front_target": 30, "back_target": 7},
    {"end_before": "soon"}, {"iv_field": ""}, {"min_history": 100, "window": 40}, {"window": True},
])
def test_bad_params_are_refused_together(bad):
    with pytest.raises(ConfigError):
        ChainEventDetector("events", {**PARAMS, **bad})


def test_missing_required_vocabulary_is_refused():
    params = {k: v for k, v in PARAMS.items() if k not in ("symbol_field", "front_target")}
    with pytest.raises(ConfigError) as err:
        ChainEventDetector("events", params)
    assert "symbol_field" in str(err.value) and "front_target" in str(err.value)


@pytest.mark.parametrize("rule", RULE_NAMES)
def test_every_rule_value_is_a_required_param(rule):
    params = {k: v for k, v in PARAMS.items() if k != rule}
    with pytest.raises(ConfigError, match=rule):
        ChainEventDetector("events", params)


def test_no_rule_value_is_hard_coded_in_the_module():
    assert not hasattr(chain_events, "DEFAULT_RULES") and set(RULES) == set(RULE_NAMES)


def test_no_node_kind_is_registered():
    assert chain_events.NODE_KINDS == ()


# -- the calibrator -----------------------------------------------------------------------------------------

def labels(symbol, indexes, confidence="high", offset=0):
    days = sessions()
    return [{"symbol": symbol, "earnings_date": days[i - offset], "confidence": confidence} for i in indexes]


CAL = {**FIELDS, "label_symbol_field": "symbol", "label_date_field": "earnings_date",
       "label_confidence_field": "confidence", "confidences": ["high"], "fit_end_before": "2019-01-01",
       "match_before": 0, "match_after": 1, "base": RULES, "grid": {"crush_min": [0.1, 0.15, 0.9]}}


def calibrate(rows, labs, **over):
    params = {k: v for k, v in dict(CAL, **over).items() if v is not ...}
    ctx = NodeContext(name="t", asof="2025-12-31", run_dir="/nonexistent")
    return ChainEventCalibrator("cal", params).run(ctx, {"records": rows, "labels": labs})


def test_calibration_reports_precision_and_recall_per_grid_cell():
    rows = table("AAA", {80: {}, 150: {}, 210: {}})
    labs = labels("AAA", [80, 150]) + labels("AAA", [190])          # one announced day with no crush
    out = calibrate(rows, labs, fit_end_before="2026-01-01")
    by = {r["params"]["crush_min"]: r for r in out["records"]}
    assert by[0.15]["events"] == 3 and by[0.15]["matched"] == 2
    assert by[0.15]["precision"] == pytest.approx(2 / 3) and by[0.15]["recall"] == pytest.approx(2 / 3)
    assert by[0.9]["events"] == 0 and by[0.9]["recall"] == 0.0 and by[0.9]["precision"] is None


def test_a_label_the_day_before_the_crush_matches_after_hours_announcements():
    rows = table("AAA", {100: {}})
    out = calibrate(rows, labels("AAA", [100], offset=1), fit_end_before="2026-01-01", grid={"crush_min": [0.15]})
    assert out["records"][0]["matched"] == 1                         # announced day 99, crush seen day 100
    out = calibrate(rows, labels("AAA", [100], offset=3), fit_end_before="2026-01-01", grid={"crush_min": [0.15]})
    assert out["records"][0]["matched"] == 0


def test_rows_and_labels_at_or_after_fit_end_are_dropped_and_counted():
    days = sessions()
    rows = table("AAA", {100: {}, 230: {}})
    cut = days[200]
    out = calibrate(rows, labels("AAA", [100, 230]), fit_end_before=cut, grid={"crush_min": [0.15]})
    row = out["records"][0]
    assert row["events"] == 1 and row["labels"] == 1 and row["matched"] == 1
    s = out["summary"]
    assert s["labels_dropped_after_fit_end"] == 1 and s["rows_dropped_after_fit_end"] > 0


def test_low_confidence_labels_are_ignored_and_counted():
    rows = table("AAA", {100: {}})
    labs = labels("AAA", [100]) + labels("AAA", [150], confidence="low")
    out = calibrate(rows, labs, fit_end_before="2026-01-01", grid={"crush_min": [0.15]})
    assert out["records"][0]["labels"] == 1 and out["summary"]["labels_ignored_confidence"] == 1


def test_calibration_never_selects_by_itself_but_names_the_best_f1_cell():
    rows = table("AAA", {80: {}, 150: {}})
    out = calibrate(rows, labels("AAA", [80, 150]), fit_end_before="2026-01-01")
    best = out["summary"]["best_by_f1"]
    assert best["params"]["crush_min"] in (0.1, 0.15) and best["f1"] == pytest.approx(1.0)


def test_calibration_grid_is_validated_against_the_rule_names():
    with pytest.raises(ConfigError):
        ChainEventCalibrator("cal", {**CAL, "grid": {"not_a_rule": [1]}})
    with pytest.raises(ConfigError):
        ChainEventCalibrator("cal", {**CAL, "grid": {"crush_min": []}})
    with pytest.raises(ConfigError):
        ChainEventCalibrator("cal", {**CAL, "grid": {}})


def test_labels_from_a_symbol_with_no_table_rows_are_counted_not_scored():
    rows = table("AAA", {100: {}})
    out = calibrate(rows, labels("AAA", [100]) + labels("ZZZ", [100]), fit_end_before="2026-01-01",
                    grid={"crush_min": [0.15]})
    assert out["summary"]["labels_without_symbol_rows"] == 1 and out["records"][0]["labels"] == 1


def test_the_calibrator_output_is_json_serialisable_and_deterministic():
    rows = table("AAA", {100: {}})
    a = calibrate(rows, labels("AAA", [100]), fit_end_before="2026-01-01")
    b = calibrate(list(reversed(rows)), labels("AAA", [100]), fit_end_before="2026-01-01")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_labels_may_arrive_as_a_mapping_naming_its_list():
    rows = table("AAA", {100: {}})
    labs = {"notes": "n", "labels": labels("AAA", [100])}
    out = calibrate(rows, labs, fit_end_before="2026-01-01", labels_key="labels", grid={"crush_min": [0.15]})
    assert out["records"][0]["matched"] == 1
    with pytest.raises(ValueError, match="labels_key"):
        calibrate(rows, labs, fit_end_before="2026-01-01", grid={"crush_min": [0.15]})


# -- calendar gaps break the trailing window ---------------------------------------------------------------

def without(rows, lo, hi):
    days = sessions()
    gone = set(days[lo:hi])
    return [r for r in rows if r["date"] not in gone]


def test_a_gap_inside_the_history_restarts_the_trailing_window_and_is_counted():
    rows = without(table("AAA", {110: {}, 150: {}}), 95, 100)       # a week of sessions is absent
    out = run(rows)
    assert out["summary"]["window_breaks"] == 1
    # 110 has only 10 changes since the gap (< min_history 15): not evaluated; 150 has plenty: an event
    assert [e["event_date"] for e in out["records"]] == [sessions()[150]]
    assert out["summary"]["missing"]["insufficient_history"] > 18


def test_a_change_across_a_gap_is_never_part_of_the_history():
    base = table("AAA", {150: {}})
    wide = run(without(base, 95, 100), max_gap_days=30)
    tight = run(without(base, 95, 100))
    assert wide["summary"]["window_breaks"] == 0 and tight["summary"]["window_breaks"] == 1
    assert [e["event_date"] for e in tight["records"]] == [e["event_date"] for e in wide["records"]]


# -- labelled_only: the robustness set, chosen by the labels and not by the outcome ------------------------------

LAB = {"label_symbol_field": "symbol", "label_date_field": "earnings_date", "label_confidence_field": "confidence",
       "confidences": ["high"], "match_before": 0, "match_after": 1, "label_event_offset": 1}


def run_labelled(rows, labs, **over):
    params = {k: v for k, v in dict(PARAMS, labelled_only=True, **LAB, **over).items() if v is not ...}
    ctx = NodeContext(name="t", asof="2025-12-31", run_dir="/nonexistent")
    return ChainEventDetector("events", params).run(ctx, {"records": rows, "labels": labs})


def test_labelled_only_emits_one_record_per_label_with_a_detected_flag():
    days = sessions()
    rows = table("AAA", {100: {}, 160: {}})
    labs = labels("AAA", [99, 160]) + labels("AAA", [130])           # 99: announced the day before the crush
    out = run_labelled(rows, labs)
    recs = out["records"]
    assert [(r["announcement_date"], r["event_date"], r["detected"]) for r in recs] == [
        (days[99], days[100], True), (days[130], days[131], False), (days[160], days[161], True)]
    assert recs[0]["entry_date"] == days[97] and recs[0]["exit_date"] == days[101]
    assert recs[0]["front_iv_before"] > recs[0]["front_iv_after"] and recs[0]["realized_move"] == pytest.approx(0.06)
    assert out["summary"]["labels_scored"] == 3 and out["summary"]["labelled_detected"] == 2


def test_labelled_only_detected_means_the_rules_accepted_an_event_in_the_match_window():
    days = sessions()
    rows = table("AAA", {100: {}})
    hit = run_labelled(rows, labels("AAA", [100], offset=1))["records"][0]    # announced day 99, crush day 100
    miss = run_labelled(rows, labels("AAA", [100], offset=3))["records"][0]   # announced day 97
    assert hit["detected"] is True and miss["detected"] is False
    assert hit["event_date"] == days[100] and miss["event_date"] == days[98]


def test_labelled_only_reports_coverage_per_year_and_the_plain_detector_has_no_labels():
    out = run_labelled(table("AAA", {100: {}}), labels("AAA", [100]) + labels("AAA", [140]))
    assert out["summary"]["labelled_by_year"] == {"2018": {"labels": 2, "detected": 1}}
    plain = run(table("AAA", {100: {}}))
    assert "detected" not in plain["records"][0] and "labelled_by_year" not in plain["summary"]


def test_label_counters_reconcile_and_duplicates_are_merged():
    rows = table("AAA", {100: {}})
    days = sessions()
    labs = (labels("AAA", [100]) + [{"symbol": "AAA", "earnings_date": days[100], "confidence": "high"}]
            + labels("AAA", [150], confidence="low") + labels("ZZZ", [100])
            + [{"symbol": "AAA", "earnings_date": "2015-01-05", "confidence": "high"}])
    s = run_labelled(rows, labs)["summary"]
    assert s["labels_total"] == 5 and s["labels_merged_duplicates"] == 1 and s["labels_scored"] == 1
    assert s["labels_total"] == sum(s[k] for k in ("labels_ignored_confidence", "labels_without_symbol_rows",
                                                  "labels_outside_table", "labels_merged_duplicates", "labels_scored"))


def test_labelled_only_needs_labels_and_label_params():
    with pytest.raises(ConfigError):
        ChainEventDetector("events", {**PARAMS, "labelled_only": True})
    with pytest.raises(ValueError, match="labels"):
        ctx = NodeContext(name="t", asof="2025-12-31", run_dir="/nonexistent")
        ChainEventDetector("events", {**PARAMS, "labelled_only": True, **LAB}).run(ctx, {"records": table("AAA")})


def test_a_label_on_or_after_end_before_is_refused():
    with pytest.raises(ValueError, match="end_before"):
        run_labelled(table("AAA", {100: {}}), [{"symbol": "AAA", "earnings_date": "2026-02-01", "confidence": "high"}])


# -- the calibrator: counters, validation ------------------------------------------------------------------------

def test_calibrator_label_counters_reconcile_with_merged_duplicates():
    rows = table("AAA", {100: {}})
    days = sessions()
    labs = (labels("AAA", [100]) + [{"symbol": "AAA", "earnings_date": days[100], "confidence": "high"}]
            + labels("AAA", [230]) + labels("ZZZ", [100]) + labels("AAA", [150], confidence="low"))
    s = calibrate(rows, labs, fit_end_before=days[200], grid={"crush_min": [0.15]})["summary"]
    parts = ("labels_ignored_confidence", "labels_dropped_after_fit_end", "labels_without_symbol_rows",
             "labels_outside_table", "labels_merged_duplicates", "labels_scored")
    assert s["labels_total"] == 5
    assert s["labels_total"] == sum(s[k] for k in parts) and s["labels_merged_duplicates"] == 1


def test_every_invalid_grid_value_is_reported_not_truncated():
    with pytest.raises(ConfigError) as err:
        ChainEventCalibrator("cal", {**CAL, "grid": {"crush_min": [-1, -2, -3, -4, -5, -6]}})
    assert all(f"got {v}" in str(err.value) for v in (-1, -2, -3, -4, -5, -6))


def test_the_calibrator_requires_every_rule_from_base_or_grid():
    base = {k: v for k, v in RULES.items() if k != "window"}
    with pytest.raises(ConfigError, match="window"):
        ChainEventCalibrator("cal", {**CAL, "base": base})
    ChainEventCalibrator("cal", {**CAL, "base": base, "grid": {"crush_min": [0.1], "window": [40]}})
