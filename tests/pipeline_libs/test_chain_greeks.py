"""ChainDayPositioning: vendor Greeks and open interest summed per symbol-day (ADR-0273)."""

import pytest

from dskit.pipeline.libs.chain_greeks import ChainDayPositioning
from dskit.pipeline.node import NodeContext

PARAMS = {
    "symbol_field": "symbol", "date_field": "date", "expiration_field": "expiration",
    "strike_field": "strike", "right_field": "right", "call_values": ["call"], "put_values": ["put"],
    "open_interest_field": "open_interest", "volume_field": "volume", "count_field": "count",
    "bid_size_field": "bid_size", "ask_size_field": "ask_size", "underlying_price_field": "underlying_price",
    "iv_field": "implied_vol", "iv_error_field": "iv_error", "greek_fields": ["delta", "gamma", "vega", "vanna"],
    "end_before": "2026-01-01", "min_dte": 1, "max_dte": 60, "moneyness_band": 0.15, "iv_error_max": 0.01,
    "status_field": "status",
}


def contract(day="2022-03-01", exp="2022-03-25", strike=100.0, right="call", oi=10, vol=5, count=2,
             bid_size=3, ask_size=1, spot=100.0, iv=0.3, iv_error=0.0, **greeks):
    row = {"symbol": "AAA", "date": day, "expiration": exp, "strike": strike, "right": right,
           "open_interest": oi, "volume": vol, "count": count, "bid_size": bid_size, "ask_size": ask_size,
           "underlying_price": spot, "implied_vol": iv, "iv_error": iv_error,
           "delta": 0.5 if right == "call" else -0.5, "gamma": 0.02, "vega": 0.1, "vanna": -0.01}
    row.update(greeks)
    return row


def run(rows, **over):
    node = ChainDayPositioning("pos", {**PARAMS, **over})
    return node.run(NodeContext(name="t", asof="2026-01-01", run_dir="/nonexistent"), {"records": rows})


def test_sums_are_open_interest_weighted_per_right_and_net_is_call_minus_put():
    rows = [contract(right="call", oi=10, gamma=0.02, vega=0.1, vanna=-0.01, delta=0.6, vol=4, count=1,
                     bid_size=5, ask_size=2),
            contract(right="put", oi=4, gamma=0.03, vega=0.2, vanna=0.02, delta=-0.4, vol=6, count=3,
                     bid_size=1, ask_size=4)]
    out = run(rows)
    r = out["records"][0]
    assert (r["symbol"], r["date"], r["status"]) == ("AAA", "2022-03-01", "ok")
    assert r["oi_call"] == 10 and r["oi_put"] == 4 and r["oi_put_call_ratio"] == pytest.approx(0.4)
    assert r["volume_call"] == 4 and r["volume_put"] == 6 and r["volume_put_call_ratio"] == pytest.approx(1.5)
    assert r["count"] == 4 and r["contracts"] == 2 and r["excluded"] == 0
    assert r["size_imbalance"] == pytest.approx((5 + 1) - (2 + 4))
    assert r["oi_gamma_call"] == pytest.approx(10 * 0.02) and r["oi_gamma_put"] == pytest.approx(4 * 0.03)
    assert r["oi_gamma_net"] == pytest.approx(0.2 - 0.12)
    assert r["oi_vega_sum"] == pytest.approx(10 * 0.1 + 4 * 0.2)
    assert r["oi_vanna_net"] == pytest.approx(10 * -0.01 - 4 * 0.02)
    assert r["oi_delta_net"] == pytest.approx(10 * 0.6 - 4 * -0.4)
    assert r["oi_delta_sum"] == pytest.approx(10 * 0.6 + 4 * -0.4)  # the signed total: delta is already signed
    assert r["oi_vega_iv"] == pytest.approx((10 * 0.1 * 0.3 + 4 * 0.2 * 0.3) / (10 * 0.1 + 4 * 0.2))
    assert out["summary"]["rows"] == 2 and out["summary"]["symbol_days"] == 1


def test_window_band_and_vendor_quality_filters_exclude_and_count():
    rows = [contract(),                                   # kept
            contract(exp="2022-03-02", strike=100.0),     # 1 dte: kept (min_dte 1)
            contract(exp="2022-06-30"),                   # beyond max_dte
            contract(strike=130.0),                       # beyond the moneyness band
            contract(iv=0.0),                             # placeholder vendor IV
            contract(iv_error=0.05),                      # solver residual too large
            contract(oi=None)]                            # no open interest
    r = run(rows)["records"][0]
    assert r["contracts"] == 2 and r["excluded"] == 5
    assert r["excluded_reasons"] == {"dte": 1, "moneyness": 1, "iv": 1, "iv_error": 1, "open_interest": 1}
    assert r["oi_gamma_call"] == pytest.approx(2 * 10 * 0.02)


def test_a_day_with_no_usable_contract_is_emitted_with_a_status_and_none_values():
    r = run([contract(iv=0.0), contract(exp="2022-09-30")])["records"][0]
    assert r["status"] == "no_contracts" and r["oi_gamma_net"] is None and r["oi_vega_iv"] is None
    assert r["oi_put_call_ratio"] is None and r["contracts"] == 0 and r["excluded"] == 2


def test_rows_are_grouped_by_symbol_and_date_in_order_and_future_dates_refuse():
    rows = [contract(day="2022-03-02"), {**contract(day="2022-03-01"), "symbol": "BBB"}, contract(day="2022-03-01")]
    r = run(rows)["records"]
    assert [(x["symbol"], x["date"]) for x in r] == [("AAA", "2022-03-01"), ("AAA", "2022-03-02"), ("BBB", "2022-03-01")]
    with pytest.raises(ValueError, match="end_before"):
        run([contract(day="2026-01-03", exp="2026-02-20")])
    with pytest.raises(ValueError, match="right"):
        run([contract(right="straddle")])


def test_spot_power_scales_a_greek_by_the_vendor_spot_so_a_split_cancels():
    before = run([contract(spot=200.0, strike=200.0, oi=10, gamma=0.02, delta=0.5)], spot_power={"gamma": 2, "delta": 1})["records"][0]
    # a 2:1 split: spot halves, per-share gamma doubles, contracts double, delta per share unchanged
    after = run([contract(spot=100.0, oi=20, gamma=0.04, delta=0.5)], spot_power={"gamma": 2, "delta": 1})["records"][0]
    assert before["oi_gamma_call"] == pytest.approx(10 * 0.02 * 200 ** 2) == pytest.approx(after["oi_gamma_call"])
    assert before["oi_delta_sum"] == pytest.approx(10 * 0.5 * 200) == pytest.approx(after["oi_delta_sum"])
    assert before["oi_vega_sum"] == pytest.approx(10 * 0.1)  # unscaled Greeks stay as summed
    assert before["oi_vega_iv"] == pytest.approx(0.3)


def test_positioning_default_denies_and_checks_knobs():
    assert ChainDayPositioning.validate_params(PARAMS) == []
    assert ChainDayPositioning.validate_params({**PARAMS, "spot_power": {"gamma": 2, "delta": 1}}) == []
    for bad in ({**PARAMS, "extra": 1}, {**PARAMS, "min_dte": 0}, {**PARAMS, "max_dte": 0},
                {**PARAMS, "moneyness_band": -1}, {**PARAMS, "iv_error_max": -0.1},
                {**PARAMS, "greek_fields": []}, {**PARAMS, "greek_fields": ["gamma", "gamma"]},
                {**PARAMS, "spot_power": {"rho": 1}}, {**PARAMS, "spot_power": {"gamma": -1}},
                {**PARAMS, "spot_power": [2]},
                {**PARAMS, "call_values": ["call"], "put_values": ["call"]},
                {k: v for k, v in PARAMS.items() if k != "end_before"},
                {k: v for k, v in PARAMS.items() if k != "greek_fields"}):
        assert ChainDayPositioning.validate_params(bad), bad
    assert ChainDayPositioning.role == "transform" and ChainDayPositioning.outputs == ("records", "summary")
    assert "ChainDayPositioning" in __import__("dskit.pipeline.libs.chain_greeks", fromlist=["x"]).__all__
    assert "NODE_KINDS" in __import__("dskit.pipeline.libs.chain_greeks", fromlist=["x"]).__all__
