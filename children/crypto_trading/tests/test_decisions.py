"""Decision rows: one row per (settled market, declared lead before its close)."""

import pytest

from crypto_trading.decisions import DecisionRows

CLOSE = 1_788_307_200_000  # a close instant, epoch ms
OPEN = CLOSE - 15 * 60_000


def market(ticker="T1", close=CLOSE, opened=OPEN, label=1, lag_ms=30_000):
    return {"ticker": ticker, "event_ticker": "E", "series": "KXBTC15M", "payoff": "above",
            "strike_type": "greater_or_equal", "floor_strike": 60000.0, "cap_strike": None,
            "open_ms": opened, "close_ms": close, "label": label, "strike_known_ms": opened + lag_ms}


def run(rows, leads=(2, 5, 10)):
    node = DecisionRows("decisions", {"leads_minutes": list(leads)})
    return node, node.run(None, {"records": rows})


def test_one_row_per_market_and_lead_with_the_decision_instant_and_label():
    _, out = run([market()])
    rows = out["records"]
    assert [r["lead_minutes"] for r in rows] == [2, 5, 10]
    assert [r["decision_ms"] for r in rows] == [CLOSE - 2 * 60_000, CLOSE - 5 * 60_000,
                                                CLOSE - 10 * 60_000]
    assert [r["tau_s"] for r in rows] == [120.0, 300.0, 600.0]
    assert all(r["label"] == 1 and r["ticker"] == "T1" for r in rows)
    assert rows[0]["floor_strike"] == 60000.0, "every market field rides along"


def test_the_leads_are_a_param_not_a_list_in_the_code():
    _, out = run([market()], leads=[1, 14])
    assert [r["lead_minutes"] for r in out["records"]] == [1, 14]
    _, out = run([market()], leads=[0.5])
    assert out["records"][0]["decision_ms"] == CLOSE - 30_000
    assert out["records"][0]["tau_s"] == 30.0


def test_a_lead_longer_than_the_market_was_open_is_excluded_not_clamped():
    node, out = run([market(lag_ms=0)], leads=[5, 14, 20])
    assert [r["lead_minutes"] for r in out["records"]] == [5, 14]
    assert out["excluded"] == [{"ticker": "T1", "lead_minutes": 20, "reason": "before_open"}]


def test_a_decision_is_priced_only_strictly_after_its_strike_is_known():
    # open for 15 minutes, strike published 30 s after the open: lead 14.5 decides AT that instant
    node, out = run([market()], leads=[5, 14, 14.5, 15])
    assert [r["lead_minutes"] for r in out["records"]] == [5, 14]
    assert out["excluded"] == [
        {"ticker": "T1", "lead_minutes": 14.5, "reason": "strike_not_known"},
        {"ticker": "T1", "lead_minutes": 15, "reason": "strike_not_known"}], (
        "the strike is the previous window's settlement average: before it is out there is nothing to price against")
    # lead 15 decides exactly at the open: even with no lag the strike must be strictly before the decision
    _, out = run([market(lag_ms=0)], leads=[15])
    assert out["records"] == [] and out["excluded"][0]["reason"] == "strike_not_known"


def test_rows_are_ordered_by_market_then_lead_and_inputs_are_not_mutated():
    later = market("T2", close=CLOSE + 900_000, opened=OPEN + 900_000)
    rows = [later, market()]
    before = [dict(r) for r in rows]
    _, out = run(rows)
    assert rows == before
    assert [(r["ticker"], r["lead_minutes"]) for r in out["records"]] == [
        ("T1", 2), ("T1", 5), ("T1", 10), ("T2", 2), ("T2", 5), ("T2", 10)]


@pytest.mark.parametrize("leads", [[], [0], [-1], [2, 2], ["5"], [float("nan")], [True], 5])
def test_bad_leads_are_refused_at_construction(leads):
    with pytest.raises(Exception, match="leads_minutes"):
        DecisionRows("decisions", {"leads_minutes": leads})


def test_default_deny_and_required():
    with pytest.raises(Exception, match="surprise"):
        DecisionRows("decisions", {"leads_minutes": [1], "surprise": 1})
    with pytest.raises(Exception, match="leads_minutes"):
        DecisionRows("decisions", {})


def test_the_decision_instant_is_strictly_before_the_close():
    _, out = run([market()], leads=[1])
    assert all(r["decision_ms"] < r["close_ms"] for r in out["records"])
