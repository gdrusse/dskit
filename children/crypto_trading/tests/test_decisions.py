"""Decision rows: one row per (settled market, declared lead before its close)."""

import pytest

from crypto_trading.decisions import DecisionRows

CLOSE = 1_788_307_200_000  # a close instant, epoch ms
OPEN = CLOSE - 15 * 60_000


def market(ticker="T1", close=CLOSE, opened=OPEN, label=1):
    return {"ticker": ticker, "event_ticker": "E", "series": "KXBTC15M", "payoff": "above",
            "strike_type": "greater_or_equal", "floor_strike": 60000.0, "cap_strike": None,
            "open_ms": opened, "close_ms": close, "label": label}


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
    node, out = run([market()], leads=[5, 15, 20])
    # open for 15 minutes: lead 15 decides exactly at the open (allowed), lead 20 is before it
    assert [r["lead_minutes"] for r in out["records"]] == [5, 15]
    assert out["excluded"] == [{"ticker": "T1", "lead_minutes": 20, "reason": "before_open"}]


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
