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


# -- ADR-0275: strike-by-expiry profiles and size totals ---------------------------------------------

PROFILES = {"moneyness_edges": [-0.05, -0.025, 0.0, 0.025, 0.05], "dte_edges": [1, 31, 61], "greeks": ["gamma"]}


def test_profiles_bucket_open_interest_volume_count_and_greeks_by_moneyness_and_dte():
    rows = [contract(strike=98.0, right="put", oi=10, vol=4, count=1, gamma=0.02),      # m1 (log 0.98 = -0.0202), d0
            contract(strike=101.0, right="call", oi=6, vol=2, count=3, gamma=0.03),     # m2, d0
            contract(strike=101.0, right="call", exp="2022-04-20", oi=5, vol=1, count=1, gamma=0.01),  # m2, d1 (50 dte)
            contract(strike=96.0, right="put", oi=7, gamma=0.05),                        # m0 (-0.0408), d0
            contract(strike=110.0, right="call", oi=9, gamma=0.04)]                      # outside the profile, still in the totals
    r = run(rows, profiles=PROFILES, spot_power={"gamma": 2})["records"][0]
    assert r["pf_m1_d0_oi_put"] == 10 and r["pf_m1_d0_oi_call"] == 0 and r["pf_m1_d0_volume_put"] == 4 and r["pf_m1_d0_count"] == 1
    assert r["pf_m2_d0_oi_call"] == 6 and r["pf_m2_d0_volume_call"] == 2 and r["pf_m2_d0_count"] == 3
    assert r["pf_m2_d1_oi_call"] == 5 and r["pf_m2_d1_oi_gamma_call"] == pytest.approx(5 * 0.01 * 100 ** 2)
    assert r["pf_m0_d0_oi_put"] == 7 and r["pf_m0_d0_oi_gamma_put"] == pytest.approx(7 * 0.05 * 100 ** 2)
    assert r["pf_m1_d0_oi_gamma_put"] == pytest.approx(10 * 0.02 * 100 ** 2) and r["pf_m1_d0_oi_gamma_call"] == 0.0
    assert r["pf_m3_d0_oi_call"] == 0 and r["pf_m3_d1_oi_put"] == 0
    assert r["oi_call"] == 6 + 5 + 9 and r["oi_gamma_sum"] == pytest.approx((10 * 0.02 + 6 * 0.03 + 5 * 0.01 + 7 * 0.05 + 9 * 0.04) * 100 ** 2)
    assert "pf_m0_d0_oi_vega_call" not in r  # only the declared profile Greeks are bucketed
    assert r["bid_size_sum"] == 5 * 3 and r["ask_size_sum"] == 5 * 1 and r["size_imbalance"] == 15 - 5


def test_profile_buckets_are_half_open_and_an_empty_day_leaves_them_none():
    # strike 100 at spot 100: log-moneyness 0 lands in [0, 0.025), dte 30 lands in [1, 31)
    r = run([contract(strike=100.0, exp="2022-03-31", oi=3)], profiles=PROFILES)["records"][0]
    assert r["pf_m2_d0_oi_call"] == 3 and r["pf_m1_d0_oi_call"] == 0
    r = run([contract(strike=100.0, exp="2022-04-01", oi=3)], profiles=PROFILES)["records"][0]   # 31 dte -> d1
    assert r["pf_m2_d1_oi_call"] == 3 and r["pf_m2_d0_oi_call"] == 0
    empty = run([contract(iv=0.0)], profiles=PROFILES)["records"][0]
    assert empty["status"] == "no_contracts" and empty["pf_m2_d0_oi_call"] is None and empty["bid_size_sum"] is None


def test_profiles_default_deny_and_shape():
    assert ChainDayPositioning.validate_params({**PARAMS, "profiles": PROFILES}) == []
    for bad in ({**PARAMS, "profiles": {**PROFILES, "extra": 1}},
                {**PARAMS, "profiles": {**PROFILES, "moneyness_edges": [0.05, -0.05]}},
                {**PARAMS, "profiles": {**PROFILES, "moneyness_edges": [0.0]}},
                {**PARAMS, "profiles": {**PROFILES, "dte_edges": [1, 1]}},
                {**PARAMS, "profiles": {**PROFILES, "dte_edges": [0, 31]}},
                {**PARAMS, "profiles": {**PROFILES, "greeks": ["rho"]}},
                {**PARAMS, "profiles": {**PROFILES, "greeks": []}},
                {**PARAMS, "profiles": {k: v for k, v in PROFILES.items() if k != "greeks"}},
                {**PARAMS, "profiles": []}):
        assert ChainDayPositioning.validate_params(bad), bad


# -- ADR-0275: ChainDayDeltaSurface, vendor IV at fixed vendor deltas ------------------------------

from dskit.pipeline.libs.chain_greeks import ChainDayDeltaSurface  # noqa: E402

DELTA_PARAMS = {
    "symbol_field": "symbol", "date_field": "date", "expiration_field": "expiration", "right_field": "right",
    "call_values": ["call"], "put_values": ["put"], "delta_field": "delta", "iv_field": "implied_vol",
    "iv_error_field": "iv_error", "open_interest_field": "open_interest", "vega_field": "vega",
    "theta_field": "theta", "end_before": "2026-01-01", "target_dte": 30, "tolerance": 7,
    "deltas": [0.1, 0.25, 0.5], "iv_error_max": 0.01, "status_field": "status",
}


def quote(right, delta, iv, oi=10, vega=0.2, theta=-0.02, day="2022-03-01", exp="2022-03-31", iv_error=0.0, **extra):
    row = {"symbol": "AAA", "date": day, "expiration": exp, "right": right, "delta": delta, "implied_vol": iv,
           "iv_error": iv_error, "open_interest": oi, "vega": vega, "theta": theta}
    row.update(extra)
    return row


def run_delta(rows, **over):
    node = ChainDayDeltaSurface("ds", {**DELTA_PARAMS, **over})
    return node.run(NodeContext(name="t", asof="2026-01-01", run_dir="/nonexistent"), {"records": rows})


def test_delta_surface_interpolates_vendor_iv_in_absolute_delta_per_right():
    rows = [quote("call", 0.60, 0.30), quote("call", 0.40, 0.26), quote("call", 0.20, 0.24), quote("call", 0.05, 0.28),
            quote("put", -0.60, 0.34), quote("put", -0.30, 0.31), quote("put", -0.20, 0.33), quote("put", -0.08, 0.40)]
    out = run_delta(rows)
    r = out["records"][0]
    assert (r["symbol"], r["date"], r["status"], r["expiration"], r["dte"]) == ("AAA", "2022-03-01", "ok", "2022-03-31", 30)
    assert r["iv_call_50"] == pytest.approx(0.28)                      # halfway between delta 0.40 and 0.60
    assert r["iv_call_25"] == pytest.approx(0.24 + (0.26 - 0.24) * 0.25)  # between 0.20 and 0.40
    assert r["iv_call_10"] == pytest.approx(0.28 + (0.24 - 0.28) * (0.05 / 0.15))
    assert r["iv_put_50"] == pytest.approx(0.31 + (0.34 - 0.31) * (0.20 / 0.30))
    assert r["iv_put_25"] == pytest.approx(0.33 + (0.31 - 0.33) * 0.5)
    assert r["iv_put_10"] == pytest.approx(0.40 + (0.33 - 0.40) * (0.02 / 0.12))
    assert r["contracts"] == 8 and r["excluded"] == 0
    assert out["summary"]["symbol_days"] == 1


def test_delta_surface_leaves_an_unbracketed_delta_none_and_weighs_vega_iv_and_carry_by_open_interest():
    rows = [quote("call", 0.45, 0.30, oi=10, vega=0.2, theta=-0.02), quote("call", 0.30, 0.26, oi=30, vega=0.1, theta=-0.03),
            quote("put", -0.45, 0.34, oi=0, vega=0.2, theta=-0.01)]
    r = run_delta(rows)["records"][0]
    assert r["iv_call_50"] is None and r["iv_call_10"] is None and r["iv_put_25"] is None
    assert r["iv_call_25"] is None  # 0.25 below the smallest call delta 0.30
    assert r["vega_iv"] == pytest.approx((10 * 0.2 * 0.30 + 30 * 0.1 * 0.26) / (10 * 0.2 + 30 * 0.1))
    assert r["theta_vega"] == pytest.approx((10 * -0.02 + 30 * -0.03) / (10 * 0.2 + 30 * 0.1))


def test_delta_surface_picks_the_nearest_expiry_in_tolerance_filters_contracts_and_refuses_bad_rows():
    rows = [quote("call", 0.40, 0.20, exp="2022-03-25"), quote("call", 0.60, 0.22, exp="2022-03-25"),   # 24 dte
            quote("call", 0.40, 0.30, exp="2022-04-04"), quote("call", 0.60, 0.32, exp="2022-04-04"),   # 34 dte: nearer 30
            quote("call", 0.40, 0.90, exp="2022-06-30"),                                              # outside tolerance
            quote("put", -0.5, 0.0, exp="2022-04-04"),                                                # placeholder IV
            quote("put", -0.5, 0.5, exp="2022-04-04", iv_error=100.0),                                # sentinel residual
            quote("put", -0.5, 0.3, exp="2022-04-04", oi=None),                                       # no open interest
            quote("put", None, 0.3, exp="2022-04-04")]                                                # no vendor delta
    r = run_delta(rows)["records"][0]
    assert r["expiration"] == "2022-04-04" and r["dte"] == 34 and r["iv_call_50"] == pytest.approx(0.31)
    assert r["contracts"] == 2 and r["excluded_reasons"] == {"iv": 1, "iv_error": 1, "open_interest": 1, "delta": 1}
    none = run_delta([quote("call", 0.5, 0.3, exp="2022-06-30")])["records"][0]
    assert none["status"] == "no_expiry" and none["iv_call_50"] is None and none["vega_iv"] is None
    with pytest.raises(ValueError, match="end_before"):
        run_delta([quote("call", 0.5, 0.3, day="2026-01-02", exp="2026-02-01")])
    with pytest.raises(ValueError, match="right"):
        run_delta([quote("straddle", 0.5, 0.3)])


def test_delta_surface_default_denies_and_checks_knobs():
    assert ChainDayDeltaSurface.validate_params(DELTA_PARAMS) == []
    assert ChainDayDeltaSurface.validate_params({k: v for k, v in DELTA_PARAMS.items() if k not in ("vega_field", "theta_field")}) == []
    for bad in ({**DELTA_PARAMS, "extra": 1}, {**DELTA_PARAMS, "deltas": []}, {**DELTA_PARAMS, "deltas": [0.0, 0.5]},
                {**DELTA_PARAMS, "deltas": [1.0]}, {**DELTA_PARAMS, "deltas": [0.5, 0.5]}, {**DELTA_PARAMS, "tolerance": -1},
                {**DELTA_PARAMS, "target_dte": 0}, {**DELTA_PARAMS, "iv_error_max": -1}, {**DELTA_PARAMS, "end_before": "2026-1-1"},
                {k: v for k, v in DELTA_PARAMS.items() if k != "vega_field"},
                {k: v for k, v in DELTA_PARAMS.items() if k != "delta_field"},
                {**DELTA_PARAMS, "call_values": ["call"], "put_values": ["call"]}):
        assert ChainDayDeltaSurface.validate_params(bad), bad
    assert ChainDayDeltaSurface.role == "transform" and ChainDayDeltaSurface.outputs == ("records", "summary")
    assert "ChainDayDeltaSurface" in __import__("dskit.pipeline.libs.chain_greeks", fromlist=["x"]).__all__
    r = run_delta([quote("call", 0.4, 0.2), quote("call", 0.6, 0.3)], **{k: v for k, v in {}.items()})["records"][0]
    assert r["iv_call_50"] == pytest.approx(0.25)
    without = ChainDayDeltaSurface("ds", {k: v for k, v in DELTA_PARAMS.items() if k not in ("vega_field", "theta_field")})
    r = without.run(NodeContext(name="t", asof="2026-01-01", run_dir="/nonexistent"), {"records": [quote("call", 0.4, 0.2)]})["records"][0]
    assert "vega_iv" not in r and "theta_vega" not in r
