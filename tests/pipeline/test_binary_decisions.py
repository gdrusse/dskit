"""Decision rows and the quote state at the decision instant (ADR-0256).

Ported from the first child that built them, with every column read through the
module's own constants: these nodes read rows the binary-market readers wrote, so
the vocabulary is the toolkit's, never a venue's.
"""

import pytest

from dskit.pipeline import binary_decisions as bd
from dskit.pipeline.binary_decisions import DecisionRows, QuoteState

CLOSE = 1_788_307_200_000  # a close instant, epoch ms
OPEN = CLOSE - 15 * 60_000
LAG_S = 5  # the execution lag in these tests
MINUTE = 60_000


def market(ticker="T1", close=CLOSE, opened=OPEN, label=1, lag_ms=30_000):
    return {bd.TICKER: ticker, bd.EVENT: "E", bd.SERIES: "S15", bd.PAYOFF: "above",
            bd.STRIKE_TYPE: "at_least", bd.FLOOR_STRIKE: 100.0, bd.CAP_STRIKE: None,
            bd.OPEN_MS: opened, bd.CLOSE_MS: close, bd.LABEL: label, bd.STRIKE_KNOWN_MS: opened + lag_ms}


def decide(rows, leads=(2, 5, 10), exec_lag_s=LAG_S):
    node = DecisionRows("decisions", {"leads_minutes": list(leads), "exec_lag_s": exec_lag_s})
    return node, node.run(None, {"records": rows})


# -- DecisionRows -------------------------------------------------------------------


def test_one_row_per_market_and_lead_with_the_decision_instant_and_label():
    _, out = decide([market()])
    rows = out["records"]
    assert [r[bd.LEAD] for r in rows] == [2, 5, 10]
    assert [r[bd.DECISION_MS] for r in rows] == [CLOSE - 2 * 60_000, CLOSE - 5 * 60_000, CLOSE - 10 * 60_000]
    assert [r[bd.EXEC_MS] for r in rows] == [r[bd.DECISION_MS] + 5_000 for r in rows]
    assert [r[bd.TAU_S] for r in rows] == [115.0, 295.0, 595.0], "the horizon starts at the EXECUTION instant"
    assert all(r[bd.LABEL] == 1 and r[bd.TICKER] == "T1" for r in rows)
    assert rows[0][bd.FLOOR_STRIKE] == 100.0, "every market field rides along"


def test_the_leads_are_a_param_not_a_list_in_the_code():
    _, out = decide([market()], leads=[1, 14])
    assert [r[bd.LEAD] for r in out["records"]] == [1, 14]
    _, out = decide([market()], leads=[0.5])
    assert out["records"][0][bd.DECISION_MS] == CLOSE - 30_000
    assert out["records"][0][bd.TAU_S] == 25.0


def test_a_lead_longer_than_the_market_was_open_is_excluded_not_clamped():
    _, out = decide([market(lag_ms=0)], leads=[5, 14, 20])
    assert [r[bd.LEAD] for r in out["records"]] == [5, 14]
    assert out["excluded"] == [{bd.TICKER: "T1", bd.LEAD: 20, "reason": "before_open"}]


def test_a_decision_is_priced_only_strictly_after_its_strike_is_known():
    # open for 15 minutes, strike published 30 s after the open: lead 14.5 decides AT that instant
    _, out = decide([market()], leads=[5, 14, 14.5, 15])
    assert [r[bd.LEAD] for r in out["records"]] == [5, 14]
    assert out["excluded"] == [
        {bd.TICKER: "T1", bd.LEAD: 14.5, "reason": "strike_not_known"},
        {bd.TICKER: "T1", bd.LEAD: 15, "reason": "strike_not_known"}]
    # lead 15 decides exactly at the open: even with no lag the strike must be strictly before the decision
    _, out = decide([market(lag_ms=0)], leads=[15])
    assert out["records"] == [] and out["excluded"][0]["reason"] == "strike_not_known"


def test_rows_are_ordered_by_market_then_lead_and_inputs_are_not_mutated():
    later = market("T2", close=CLOSE + 900_000, opened=OPEN + 900_000)
    rows = [later, market()]
    before = [dict(r) for r in rows]
    _, out = decide(rows)
    assert rows == before
    assert [(r[bd.TICKER], r[bd.LEAD]) for r in out["records"]] == [
        ("T1", 2), ("T1", 5), ("T1", 10), ("T2", 2), ("T2", 5), ("T2", 10)]


def test_the_execution_lag_is_a_param_and_moves_the_trade_not_the_information_instant():
    _, out = decide([market()], leads=[5], exec_lag_s=20)
    row = out["records"][0]
    assert row[bd.DECISION_MS] == CLOSE - 300_000 and row[bd.EXEC_MS] == row[bd.DECISION_MS] + 20_000
    assert row[bd.TAU_S] == 280.0


def test_an_execution_at_or_after_the_close_is_excluded_by_name():
    _, out = decide([market()], leads=[1], exec_lag_s=60)
    assert out["records"] == [] and out["excluded"][0]["reason"] == "exec_not_before_close"


def test_the_decision_instant_is_strictly_before_the_close():
    _, out = decide([market()], leads=[1])
    assert all(r[bd.DECISION_MS] < r[bd.CLOSE_MS] for r in out["records"])


def test_the_excluded_decisions_are_written_beside_the_run(tmp_path):
    import json
    import os

    class Ctx:
        run_dir = str(tmp_path)

    node = DecisionRows("decisions", {"leads_minutes": [20], "exec_lag_s": LAG_S})
    out = node.run(Ctx(), {"records": [market()]})
    path = os.path.join(str(tmp_path), "artifacts", "decisions", "excluded.json")
    assert json.load(open(path, encoding="utf-8")) == out["excluded"]


@pytest.mark.parametrize("leads", [[], [0], [-1], [2, 2], ["5"], [float("nan")], [True], 5])
def test_bad_leads_are_refused_at_construction(leads):
    with pytest.raises(Exception, match="leads_minutes"):
        DecisionRows("decisions", {"leads_minutes": leads, "exec_lag_s": 5})


@pytest.mark.parametrize("lag", [0, -1, "5", None, True, float("nan")])
def test_a_zero_or_bad_execution_lag_is_refused_because_a_fill_cannot_precede_its_information(lag):
    with pytest.raises(Exception, match="exec_lag_s"):
        DecisionRows("decisions", {"leads_minutes": [2], "exec_lag_s": lag})


def test_decisions_default_deny_and_required():
    with pytest.raises(Exception, match="surprise"):
        DecisionRows("decisions", {"leads_minutes": [1], "exec_lag_s": 5, "surprise": 1})
    with pytest.raises(Exception, match="leads_minutes"):
        DecisionRows("decisions", {"exec_lag_s": 5})
    with pytest.raises(Exception, match="exec_lag_s"):
        DecisionRows("decisions", {"leads_minutes": [1]})


def test_a_port_that_is_not_a_list_is_refused():
    node = DecisionRows("decisions", {"leads_minutes": [1], "exec_lag_s": 5})
    assert node.validate_inputs({"records": iter([])}) == ["records must be a list of rows, got list_iterator"]
    assert node.validate_inputs({"records": []}) == []


# -- QuoteState ---------------------------------------------------------------------

DECISION = 1_788_307_200_000  # a whole-minute instant, epoch ms
PARAMS = {"max_candle_age_ms": 120_000, "quote_floor": 0.0, "quote_ceiling": 1.0}


def candle(end, bid=0.40, ask=0.44, price=0.42, volume=10.0, interest=99.0, ticker="T"):
    return {bd.TICKER: ticker, bd.END_MS: end, bd.YES_BID: bid, bd.YES_ASK: ask, bd.PRICE: price,
            bd.VOLUME: volume, bd.OPEN_INTEREST: interest}


def row(decision=DECISION, ticker="T"):
    return {bd.TICKER: ticker, bd.DECISION_MS: decision, bd.LABEL: 1}


def state(rows, candles, **over):
    node = QuoteState("state", {**PARAMS, **over})
    return node.run(None, {"records": rows, "candles": candles})["records"]


def test_the_state_is_the_last_candle_that_ended_by_the_information_instant():
    candles = [candle(DECISION - 3 * MINUTE, bid=0.10, ask=0.12),
            candle(DECISION, bid=0.40, ask=0.44, price=0.42, volume=7.0, interest=120.0)]
    out = state([row()], candles)[0]
    assert (out[bd.YES_BID], out[bd.YES_ASK]) == (0.40, 0.44)
    assert out[bd.MID] == pytest.approx(0.42) and out[bd.SPREAD] == pytest.approx(0.04)
    assert out[bd.CANDLE_PRICE] == 0.42 and out[bd.CANDLE_VOLUME] == 7.0
    assert out[bd.CANDLE_OPEN_INTEREST] == 120.0 and out[bd.CANDLE_AGE_MS] == 0
    assert out[bd.QUOTE_MISSING] is False and out[bd.TWO_SIDED] is True


def test_the_candle_ending_at_the_instant_is_the_one_read_not_the_minute_before():
    out = state([row()], [candle(DECISION - MINUTE, bid=0.10, ask=0.12), candle(DECISION, bid=0.50, ask=0.54)])[0]
    assert out[bd.YES_BID] == 0.50 and out[bd.CANDLE_AGE_MS] == 0


def test_a_candle_ending_after_the_information_instant_never_reaches_the_row():
    candles = [candle(DECISION, bid=0.40, ask=0.44),
            candle(DECISION + 1, bid=0.99, ask=0.995, price=0.99, volume=1e6, interest=1e6),
            candle(DECISION + 5_000, bid=0.99, ask=0.995),
            candle(DECISION + MINUTE, bid=0.99, ask=0.995)]
    clean = state([row()], candles[:1])[0]
    dirty = state([row()], candles)[0]
    assert clean == dirty, "anything stamped after the information instant (so any trade instant) is invisible"
    assert dirty[bd.YES_BID] == 0.40


def test_control_the_same_extreme_candle_at_the_instant_is_used():
    out = state([row()], [candle(DECISION, bid=0.97, ask=0.99)])[0]
    assert out[bd.YES_BID] == 0.97, "so the leak test above is not passing vacuously"


def test_milliseconds_and_seconds_are_not_confused():
    out = state([row()], [candle(DECISION // 1000)])[0]
    assert out[bd.YES_BID] is None and out[bd.QUOTE_MISSING] is True


def test_a_candle_older_than_the_age_cap_is_missing_not_stale():
    out = state([row()], [candle(DECISION - 3 * MINUTE)])[0]
    assert out[bd.YES_BID] is None and out[bd.CANDLE_AGE_MS] is None and out[bd.QUOTE_MISSING] is True
    assert out[bd.TWO_SIDED] is False and out[bd.MID] is None
    kept = state([row()], [candle(DECISION - 3 * MINUTE)], max_candle_age_ms=3 * MINUTE)[0]
    assert kept[bd.YES_BID] == 0.40


def test_another_markets_candle_is_never_used():
    out = state([row()], [candle(DECISION - MINUTE, ticker="OTHER")])[0]
    assert out[bd.QUOTE_MISSING] is True


@pytest.mark.parametrize("bid, ask", [(None, 0.44), (0.40, None), (None, None)])
def test_a_missing_side_is_none_and_flagged_with_no_mid(bid, ask):
    out = state([row()], [candle(DECISION - MINUTE, bid=bid, ask=ask)])[0]
    assert out[bd.QUOTE_MISSING] is True and out[bd.MID] is None and out[bd.SPREAD] is None
    assert out[bd.TWO_SIDED] is False
    assert out[bd.YES_BID] == bid and out[bd.YES_ASK] == ask, "what exists is kept as it is"


@pytest.mark.parametrize("bid, ask, two_sided", [
    (0.40, 0.44, True), (0.01, 0.99, True), (0.0, 0.44, False), (0.40, 1.0, False),
    (0.50, 0.40, False), (0.45, 0.45, True)])
def test_two_sided_means_a_real_bid_and_ask_inside_the_declared_bounds_and_not_crossed(bid, ask, two_sided):
    out = state([row()], [candle(DECISION - MINUTE, bid=bid, ask=ask)])[0]
    assert out[bd.TWO_SIDED] is two_sided
    assert out[bd.QUOTE_MISSING] is False
    assert out[bd.SPREAD] == pytest.approx(ask - bid), "a crossed book shows a negative spread"


def test_bounds_are_params():
    out = state([row()], [candle(DECISION - MINUTE, bid=0.02, ask=0.98)], quote_floor=0.05)[0]
    assert out[bd.TWO_SIDED] is False


def test_rows_keep_their_order_and_other_fields_and_inputs_are_not_mutated():
    rows = [row(DECISION + MINUTE, "A"), row(DECISION, "B")]
    candles = [candle(DECISION, ticker="A"), candle(DECISION - MINUTE, ticker="B")]
    before = [dict(r) for r in rows]
    out = state(rows, candles)
    assert [r[bd.TICKER] for r in out] == ["A", "B"] and all(r[bd.LABEL] == 1 for r in out)
    assert rows == before


def test_quote_state_default_deny_and_required_knobs():
    with pytest.raises(Exception, match="surprise"):
        QuoteState("state", {**PARAMS, "surprise": 1})
    for knob in PARAMS:
        with pytest.raises(Exception, match=knob):
            QuoteState("state", {k: v for k, v in PARAMS.items() if k != knob})
    with pytest.raises(Exception, match="quote_ceiling"):
        QuoteState("state", {**PARAMS, "quote_ceiling": 0.0})
    with pytest.raises(Exception, match="max_candle_age_ms"):
        QuoteState("state", {**PARAMS, "max_candle_age_ms": 0})


def test_candles_out_of_order_still_give_the_latest_bar_ended_by_the_instant():
    candles = [candle(DECISION - 30_000, bid=0.50, ask=0.54), candle(DECISION + MINUTE, bid=0.99, ask=0.995),
               candle(DECISION - 2 * MINUTE, bid=0.10, ask=0.12), candle(DECISION - MINUTE, bid=0.30, ask=0.34)]
    out = state([row()], candles)[0]
    assert (out[bd.YES_BID], out[bd.YES_ASK]) == (0.50, 0.54)
    assert out[bd.CANDLE_AGE_MS] == 30_000


def test_the_candle_age_is_how_long_before_the_instant_the_bar_ended():
    assert state([row()], [candle(DECISION - 45_000)])[0][bd.CANDLE_AGE_MS] == 45_000


def test_the_candle_age_cap_is_inclusive_to_the_millisecond():
    cap = PARAMS["max_candle_age_ms"]
    assert state([row()], [candle(DECISION - cap)])[0][bd.YES_BID] == 0.40, "age == cap is still fresh"
    assert state([row()], [candle(DECISION - cap - 1)])[0][bd.QUOTE_MISSING] is True, "one millisecond over is stale"


def test_the_quote_state_and_the_series_asof_rule_choose_the_same_bar_at_every_boundary():
    # A bar's END is exclusive: bar k covers [end - 1 min, end), so its last covered instant is end - 1 and the
    # bar is known at `end`. The platform's series as-of rule (the last row STRICTLY BEFORE the instant) applied to
    # each bar's last covered instant must therefore pick what QuoteState picks, at end - 1, end and end + 1.
    import numpy as np

    from dskit.pipeline.libs.parquet_series import prior_index

    ends = [DECISION + k * MINUTE for k in range(4)]
    bars = [candle(end, bid=0.10 + 0.01 * k, ask=0.20 + 0.01 * k) for k, end in enumerate(ends)]
    last_covered = np.array([end - 1 for end in ends])
    instants = [end + step for end in ends for step in (-1, 0, 1)]
    chosen = prior_index(last_covered, np.array(instants))
    out = state([row(i) for i in instants], bars, max_candle_age_ms=10 * MINUTE)
    for instant, got, index in zip(instants, out, chosen):
        want = None if index < 0 else bars[index][bd.YES_BID]
        assert got[bd.YES_BID] == want, f"decision at {instant - DECISION} ms"
    picks = [int(i) for i in chosen]
    assert picks == [-1, 0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3], "end - 1 still sees the previous bar, end sees this one"


def test_both_ports_must_be_lists():
    node = QuoteState("state", PARAMS)
    assert node.validate_inputs({"records": [], "candles": []}) == []
    assert node.validate_inputs({"records": [], "candles": iter([])}) == ["candles must be a list of rows, got list_iterator"]


def test_the_vocabulary_has_one_home_and_no_two_columns_share_a_name():
    names = [getattr(bd, n) for n in bd.__all__ if n.isupper() and isinstance(getattr(bd, n), str)]
    assert len(names) == len(set(names))
    assert bd.BOUND_FIELDS == {"lower": bd.FLOOR_STRIKE, "upper": bd.CAP_STRIKE}
