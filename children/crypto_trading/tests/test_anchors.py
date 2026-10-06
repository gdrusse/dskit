"""Strike anchors: a 15-minute up/down market's strike is an observation of the settlement index.

Kalshi sets that strike to the previous window's settlement value, the index's 60-second average ending at
this market's open, and publishes it a few seconds after. It is therefore the only observation of the
settlement index in the data, in the units the contracts settle in.
"""

import pytest

from crypto_trading.anchors import StrikeAnchors

OPEN = 1_788_307_200_000


def market(ticker="T1", series="KXBTC15M", floor=60000.0, opened=OPEN, lag_ms=30_000, payoff="above"):
    return {"ticker": ticker, "series": series, "payoff": payoff, "floor_strike": floor,
            "cap_strike": None, "open_ms": opened, "close_ms": opened + 900_000,
            "strike_known_ms": opened + lag_ms, "label": 1}


def run(rows, series=("KXBTC15M",)):
    return StrikeAnchors("anchors", {"anchor_series": list(series)}).run(None, {"records": rows})


def test_each_market_of_an_anchor_series_gives_the_strike_its_window_end_and_when_it_was_known():
    out = run([market()])["records"]
    assert out == [{"ticker": "T1", "series": "KXBTC15M", "anchor_ms": OPEN, "known_ms": OPEN + 30_000,
                    "anchor_value": 60000.0}]


def test_the_anchor_never_carries_the_label_or_anything_after_its_own_window():
    row = run([market()])["records"][0]
    assert set(row) == {"ticker", "series", "anchor_ms", "known_ms", "anchor_value"}


def test_only_the_declared_series_are_anchors_and_hourly_strikes_are_not():
    rows = [market("A"), market("B", series="KXETH15M"), market("C", series="KXBTCD")]
    assert [a["ticker"] for a in run(rows)["records"]] == ["A"]
    assert [a["ticker"] for a in run(rows, series=("KXBTC15M", "KXETH15M"))["records"]] == ["A", "B"]


def test_a_market_whose_payoff_has_no_floor_is_not_an_anchor():
    assert run([market(payoff="below", floor=None)])["records"] == []


def test_anchors_are_ordered_by_when_they_became_known_and_inputs_are_not_mutated():
    rows = [market("late", opened=OPEN + 900_000), market("early")]
    before = [dict(r) for r in rows]
    assert [a["ticker"] for a in run(rows)["records"]] == ["early", "late"]
    assert rows == before


def test_params_are_validated():
    for bad in ([], [""], "KXBTC15M", [1]):
        with pytest.raises(Exception, match="anchor_series"):
            StrikeAnchors("anchors", {"anchor_series": bad})
    with pytest.raises(Exception, match="anchor_series"):
        StrikeAnchors("anchors", {})
    with pytest.raises(Exception, match="surprise"):
        StrikeAnchors("anchors", {"anchor_series": ["KXBTC15M"], "surprise": 1})


def test_the_anchor_value_is_the_floor_strike_even_when_a_cap_is_also_present():
    row = {**market(floor=60000.0), "cap_strike": 61000.0}
    assert run([row])["records"][0]["anchor_value"] == 60000.0
