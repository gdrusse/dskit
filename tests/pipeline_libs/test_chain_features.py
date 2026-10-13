"""ChainDayFeatures (ADR-0265 node 2): per (symbol, date, target DTE) features from NBBO mids.

The chains are built from Black-76 prices of known volatility surfaces, so every expected number
comes from the pricing formula or from a brute-force search written independently here, never from
the node. Vendor ``implied_vol`` and greeks are planted as garbage and must change nothing.
"""

import math
from datetime import date, timedelta

import pytest

pd = pytest.importorskip("pandas")

from dskit.pipeline.base import ConfigError
from dskit.pipeline.libs import chain_features
from dskit.pipeline.libs.chain_features import FEATURES, ChainDayFeatures
from dskit.pipeline.node import NodeContext
from dskit.pipeline.option_pricing import black76, black76_delta

DAY = "2020-03-02"
ALL_FIELDS = {name: name for name in FEATURES}
FIELDS = {name: name for name in FEATURES if name != "term_slope"}

PARAMS = {
    "symbol_field": "symbol", "date_field": "date", "expiration_field": "expiration",
    "strike_field": "strike", "right_field": "right", "bid_field": "bid", "ask_field": "ask",
    "open_interest_field": "open_interest", "volume_field": "volume",
    "underlying_price_field": "underlying_price", "call_values": ["call"], "put_values": ["put"],
    "end_before": "2026-01-01", "target_field": "target_dte", "status_field": "status",
    "targets": [{"dte": 30, "tolerance": 5}], "atm_band": 0.05, "wing_delta": 0.25,
    "min_forward_pairs": 3, "oi_scope": "expiry", "oi_lag_sessions": 2, "oi_lag_max_days": 7,
    "fields": FIELDS,
}


def expiry_of(day, dte):
    return (date.fromisoformat(day) + timedelta(days=dte)).isoformat()


def chain(symbol="AAA", day=DAY, dte=28, spot=100.0, vol=lambda k: 0.30, put_vol=None,
          strikes=range(70, 131), half_spread=0.005, oi=10, volume=3, expiration=None):
    """Rows of one expiration priced by Black-76 on a forward of ``spot`` (zero rate)."""
    if expiration is not None:
        dte = (date.fromisoformat(expiration) - date.fromisoformat(day)).days
    years = (dte or 0.5) / 365
    rows = []
    for strike in strikes:
        for right in ("call", "put"):
            v = (put_vol or vol)(strike) if right == "put" else vol(strike)
            mid = black76(right, spot, float(strike), v, years)
            rows.append({"symbol": symbol, "date": day, "expiration": expiration or expiry_of(day, dte),
                         "strike": float(strike), "right": right, "bid": mid - half_spread,
                         "ask": mid + half_spread, "open_interest": oi, "volume": volume,
                         "underlying_price": spot, "implied_vol": 99.0, "delta": 7.0})
    return rows


def run(rows, **over):
    params = {k: v for k, v in dict(PARAMS, **over).items() if v is not ...}
    ctx = NodeContext(name="t", asof="2025-12-31", run_dir="/nonexistent")
    return ChainDayFeatures("features", params).run(ctx, {"records": rows})


def only(out):
    assert len(out["records"]) == 1, out["records"]
    return out["records"][0]


def strike_for_delta(right, vol, years, target, forward=100.0):
    """Brute-force the continuous strike whose own-IV forward delta magnitude is ``target``."""
    low, high = (30.0, forward) if right == "put" else (forward, 300.0)
    for _ in range(200):
        mid = 0.5 * (low + high)
        delta = abs(black76_delta(right, forward, mid, vol(mid), years))
        # put |delta| falls as the strike falls; call |delta| falls as the strike rises
        too_deep = delta < target
        if right == "put":
            low, high = (mid, high) if too_deep else (low, mid)
        else:
            low, high = (low, mid) if too_deep else (mid, high)
    return 0.5 * (low + high)


# -- a flat surface ------------------------------------------------------------------------


def test_a_flat_surface_recovers_its_vol_and_has_no_skew():
    row = only(run(chain()))
    years = 28 / 365
    assert row["forward"] == pytest.approx(100.0, abs=1e-9)
    assert row["atm_strike"] == 100.0
    assert row["atm_iv"] == pytest.approx(0.30, abs=1e-6)
    assert row["put_wing_iv"] == pytest.approx(0.30, abs=1e-5)
    assert row["call_wing_iv"] == pytest.approx(0.30, abs=1e-5)
    assert row["risk_reversal"] == pytest.approx(0.0, abs=1e-5)
    assert row["put_call_iv_spread"] == pytest.approx(0.0, abs=1e-6)
    assert row["spot"] == 100.0 and row["dte"] == 28 and row["target_dte"] == 30
    assert row["expiration"] == expiry_of(DAY, 28) and row["years"] == pytest.approx(years)
    assert (row["symbol"], row["date"]) == ("AAA", DAY)


def test_straddle_ratio_and_half_spread_are_taken_at_the_atm_strike_from_the_mid():
    row = only(run(chain(half_spread=0.02)))
    years = 28 / 365
    premium = 2 * black76("call", 100.0, 100.0, 0.30, years)
    assert row["straddle_ratio"] == pytest.approx(premium / 100.0, rel=1e-9)
    assert row["half_spread_over_premium"] == pytest.approx(0.04 / premium, rel=1e-9)


def test_vendor_greeks_and_implied_vol_change_nothing():
    clean = [{k: v for k, v in r.items() if k not in ("implied_vol", "delta")} for r in chain()]
    assert only(run(chain())) == only(run(clean))


# -- a skewed surface ----------------------------------------------------------------------


def skew(k):
    return 0.30 - 0.40 * math.log(k / 100.0)


def test_wing_ivs_and_the_risk_reversal_match_a_brute_force_delta_search():
    out = only(run(chain(vol=skew, strikes=[x * 0.5 for x in range(140, 261)])))
    years = 28 / 365
    put_k = strike_for_delta("put", skew, years, 0.25)
    call_k = strike_for_delta("call", skew, years, 0.25)
    assert out["put_wing_iv"] == pytest.approx(skew(put_k), abs=5e-4)
    assert out["call_wing_iv"] == pytest.approx(skew(call_k), abs=5e-4)
    assert out["risk_reversal"] == pytest.approx(skew(put_k) - skew(call_k), abs=1e-3)
    assert out["risk_reversal"] > 0.03          # put minus call: the downside costs more
    assert out["atm_iv"] == pytest.approx(0.30, abs=2e-3)


def test_a_wing_without_a_bracketing_strike_is_none_not_extrapolated():
    narrow = chain(vol=skew, strikes=range(98, 103))
    row = only(run(narrow))
    assert row["put_wing_iv"] is None and row["call_wing_iv"] is None and row["risk_reversal"] is None
    assert row["atm_iv"] == pytest.approx(0.30, abs=2e-3)


def test_the_put_call_iv_spread_is_put_minus_call_at_the_same_strike():
    row = only(run(chain(vol=lambda k: 0.30, put_vol=lambda k: 0.32)))
    assert 0.005 < row["put_call_iv_spread"] < 0.025      # the parity forward absorbs part of the gap


# -- target DTE, term slope ----------------------------------------------------------------


def two_terms():
    return (chain(dte=28, vol=lambda k: 0.20) + chain(dte=91, vol=lambda k: 0.30))


def test_each_target_picks_the_nearest_expiry_within_its_tolerance():
    out = run(two_terms(), targets=[{"dte": 30, "tolerance": 5}, {"dte": 90, "tolerance": 5}])
    rows = {r["target_dte"]: r for r in out["records"]}
    assert rows[30]["dte"] == 28 and rows[90]["dte"] == 91
    assert rows[30]["atm_iv"] == pytest.approx(0.20, abs=1e-5)
    assert rows[90]["atm_iv"] == pytest.approx(0.30, abs=1e-5)


def test_the_term_slope_is_the_long_atm_iv_minus_the_short_one_on_every_row_of_the_day():
    out = run(two_terms(), targets=[{"dte": 30, "tolerance": 5}, {"dte": 90, "tolerance": 5}],
              term_pair=[30, 90], fields=ALL_FIELDS)
    assert [r["term_slope"] for r in out["records"]] == [pytest.approx(0.10, abs=1e-4)] * 2


def test_a_target_with_no_expiry_in_tolerance_emits_no_row_and_is_counted():
    out = run(chain(dte=28), targets=[{"dte": 30, "tolerance": 5}, {"dte": 90, "tolerance": 5}],
              term_pair=[30, 90], fields=ALL_FIELDS)
    assert [(r["target_dte"], r["status"]) for r in out["records"]] == [(30, "ok"), (90, "no_expiry")]
    assert out["records"][0]["term_slope"] is None and out["records"][1]["atm_iv"] is None
    assert out["records"][1]["expiration"] is None and out["records"][1]["dte"] is None
    assert out["summary"]["targets"]["90"]["no_expiry"] == 1
    assert out["summary"]["targets"]["30"]["emitted"] == 1


def test_a_tie_between_two_expiries_goes_to_the_nearer_then_the_earlier():
    rows = chain(dte=26) + chain(dte=34, vol=lambda k: 0.5)
    assert only(run(rows, targets=[{"dte": 30, "tolerance": 5}]))["dte"] == 26


def test_an_expired_contract_is_never_a_target():
    rows = chain(dte=0) + chain(dte=3)
    assert only(run(rows, targets=[{"dte": 1, "tolerance": 3}]))["dte"] == 3


# -- exclusions ---------------------------------------------------------------------------


def test_bad_quotes_are_excluded_and_counted_and_move_the_atm_strike():
    rows = chain()
    for r in rows:
        if r["strike"] == 100.0 and r["right"] == "call":
            r["bid"], r["ask"] = 5.0, 4.0                          # crossed at the money
    zero = dict(rows[0], strike=70.5, bid=0.0, ask=0.01)
    null = dict(rows[1], strike=70.5, bid=None)
    out = run(rows + [zero, null])
    summary = out["summary"]["excluded_quotes"]
    assert summary["crossed"] == 1 and summary["null"] == 1
    assert summary["zero"] >= 1                                   # the planted one plus the far wing
    row = only(out)
    assert row["atm_strike"] == 101.0                             # nearest in log terms with both sides usable
    assert row["straddle_ratio"] == pytest.approx(
        (black76("call", 100.0, 101.0, 0.30, 28 / 365) + black76("put", 100.0, 101.0, 0.30, 28 / 365)) / 100.0, rel=1e-9)


def test_locked_quotes_are_usable():
    rows = chain(half_spread=0.0)
    assert only(run(rows))["atm_iv"] == pytest.approx(0.30, abs=1e-6)
    assert run(rows)["summary"]["excluded_quotes"]["crossed"] == 0


def test_rows_before_a_roots_first_bid_date_are_excluded_and_counted():
    dead = [dict(r, date="2020-02-28", expiration=expiry_of("2020-02-28", 30), bid=0.0, ask=0.05)
            for r in chain(dte=30, day="2020-02-28")]
    out = run(dead + chain())
    assert [r["date"] for r in out["records"]] == [DAY]
    assert out["summary"]["rows_before_first_bid"] == len(dead)
    assert out["summary"]["groups_before_first_bid"] == 1


def test_first_bid_date_is_per_symbol():
    other = [dict(r, symbol="BBB", bid=0.0, ask=0.05) for r in chain()]
    out = run(chain() + other)
    assert [r["symbol"] for r in out["records"]] == ["AAA"]


def test_a_day_with_too_few_parity_pairs_keeps_its_row_with_nothing_filled():
    out = run(chain(strikes=[99, 100]) + chain(symbol="BBB"), min_forward_pairs=3)
    row = out["records"][0]
    assert row["symbol"] == "AAA" and out["records"][1]["status"] == "ok"
    assert row["forward"] is None and row["atm_iv"] is None and row["straddle_ratio"] is None
    assert row["status"] == "no_forward" and row["target_dte"] == 30
    assert all(v is None for k, v in row.items() if k not in ("symbol", "date", "target_dte", "status"))
    assert out["summary"]["targets"]["30"]["no_forward"] == 1


def test_a_missing_underlying_price_leaves_only_the_spot_dependent_features_empty():
    rows = [dict(r, underlying_price=None) for r in chain()]
    row = only(run(rows))
    assert row["spot"] is None and row["straddle_ratio"] is None
    assert row["atm_iv"] == pytest.approx(0.30, abs=1e-6)


# -- open interest and volume --------------------------------------------------------------


def days_of_oi():
    """Four sessions; the target expiry's OI changes, a second (farther) expiry rides along."""
    rows = []
    plan = [("2020-03-02", 10, 20), ("2020-03-03", 11, 21), ("2020-03-04", 12, 24), ("2020-03-05", 15, 30)]
    for day, call_oi, put_oi in plan:
        near = chain(day=day, expiration="2020-03-30", strikes=[99, 100, 101, 102, 103])
        for r in near:
            r["open_interest"] = call_oi if r["right"] == "call" else put_oi
            r["volume"] = 1 if r["right"] == "call" else 2
        far = chain(day=day, expiration="2020-06-01", strikes=[99, 100, 101, 102, 103], oi=1)
        rows += near + far
    return rows


def test_oi_and_volume_are_summed_by_side_over_the_targets_expiry():
    out = run(days_of_oi())
    row = out["records"][0]
    assert (row["oi_call"], row["oi_put"]) == (5 * 10, 5 * 20)
    assert (row["volume_call"], row["volume_put"]) == (5, 10)
    assert row["oi_put_call_ratio"] == pytest.approx(2.0)


def test_the_oi_change_looks_back_the_declared_sessions_on_the_same_expiry():
    by_day = {r["date"]: r for r in run(days_of_oi())["records"]}
    assert by_day["2020-03-02"]["oi_change"] is None and by_day["2020-03-03"]["oi_change"] is None
    # 2020-03-04 vs 2020-03-02: total 5*(12+24)=180 against 5*(10+20)=150
    assert by_day["2020-03-04"]["oi_change"] == pytest.approx(180 / 150 - 1)
    assert by_day["2020-03-04"]["oi_put_call_ratio_change"] == pytest.approx(24 / 12 - 20 / 10)
    # 2020-03-05 vs 2020-03-03: 5*(15+30)=225 against 5*(11+21)=160
    assert by_day["2020-03-05"]["oi_change"] == pytest.approx(225 / 160 - 1)
    assert by_day["2020-03-05"]["oi_put_call_ratio_change"] == pytest.approx(30 / 15 - 21 / 11)


def test_a_lag_longer_than_the_gap_guard_is_none():
    out = run(days_of_oi(), oi_lag_max_days=1)
    assert all(r["oi_change"] is None for r in out["records"])


def test_the_chain_scope_sums_every_expiry():
    row = run(days_of_oi(), oi_scope="chain")["records"][0]
    assert (row["oi_call"], row["oi_put"]) == (5 * 10 + 5, 5 * 20 + 5)
    assert row["volume_call"] == 5 + 15                          # far expiry: volume 3 on each of 5 strikes


def test_oi_counts_contracts_whose_quote_is_unusable():
    rows = chain(strikes=[99, 100, 101])
    for r in rows:
        if r["strike"] == 99.0:
            r["bid"], r["ask"] = 0.0, 0.0
    assert only(run(rows, min_forward_pairs=2))["oi_call"] == 30


def test_a_null_oi_is_missing_not_zero():
    rows = [dict(r, open_interest=None) for r in chain()]
    row = only(run(rows))
    assert row["oi_call"] is None and row["oi_put_call_ratio"] is None


# -- ordering, memory mode, projection ----------------------------------------------------


def test_input_order_does_not_matter_and_presorted_agrees():
    rows = days_of_oi()
    reference = run(rows)["records"]
    assert run(list(reversed(rows)))["records"] == reference
    ordered = sorted(rows, key=lambda r: (r["symbol"], r["date"]))
    assert run(ordered, presorted=True)["records"] == reference


def test_presorted_input_out_of_order_refuses():
    rows = days_of_oi()
    shuffled = rows[len(rows) // 2:] + rows[: len(rows) // 2]
    with pytest.raises(ValueError, match="presorted"):
        run(shuffled, presorted=True)


def test_symbols_do_not_share_history_or_forward():
    both = days_of_oi() + [dict(r, symbol="ZZZ", open_interest=1) for r in days_of_oi()]
    out = {(r["symbol"], r["date"]): r for r in run(both)["records"]}
    assert out[("AAA", "2020-03-04")]["oi_change"] == pytest.approx(180 / 150 - 1)
    assert out[("ZZZ", "2020-03-04")]["oi_change"] == pytest.approx(0.0)


def test_fields_selects_and_renames_the_outputs():
    row = only(run(chain(), fields={"atm_iv": "iv30", "forward": "fwd"}))
    assert set(row) == {"symbol", "date", "target_dte", "status", "iv30", "fwd"}


def test_the_input_is_read_once():
    seen = []

    def once():
        for r in chain():
            seen.append(1)
            yield r

    assert len(run(once())["records"]) == 1 and len(seen) == len(chain())


# -- loud refusals -----------------------------------------------------------------------


def test_an_empty_input_refuses():
    with pytest.raises(ValueError, match="empty"):
        run([])


def test_a_row_at_or_after_end_before_refuses():
    with pytest.raises(ValueError, match="end_before"):
        run(chain(day="2026-01-02"))


def test_nothing_usable_at_all_refuses_rather_than_emit_an_empty_table():
    dead = [dict(r, bid=0.0, ask=0.05) for r in chain()]
    with pytest.raises(ValueError, match="no root has a bid"):
        run(dead)


def test_the_empty_result_refusals_are_one_catchable_class_carrying_the_counts():
    from dskit.pipeline.libs.chain_features import NoFeatureRows
    dead = [dict(r, bid=0.0, ask=0.05) for r in chain()]
    with pytest.raises(NoFeatureRows) as quiet:
        run(dead)
    assert quiet.value.counts["rows"] == len(dead) and quiet.value.counts["groups_before_first_bid"] == 1
    with pytest.raises(NoFeatureRows) as nothing:
        run(chain(dte=28), targets=[{"dte": 200, "tolerance": 5}])
    assert nothing.value.counts["targets"]["200"]["no_expiry"] == 1 and issubclass(NoFeatureRows, ValueError)


def test_no_target_matching_any_expiry_refuses():
    with pytest.raises(ValueError, match="no feature row"):
        run(chain(dte=28), targets=[{"dte": 200, "tolerance": 5}])


def test_a_malformed_row_refuses_by_row():
    rows = chain()
    rows[3] = dict(rows[3], right="straddle")
    with pytest.raises(ValueError, match=r"row 3.*right"):
        run(rows)
    broken = chain()
    del broken[5]["bid"]
    with pytest.raises(ValueError, match=r"row 5.*bid"):
        run(broken)
    with pytest.raises(ValueError, match="strike"):
        run([dict(chain()[0], strike=-1.0)])
    with pytest.raises(ValueError, match="expiration"):
        run([dict(chain()[0], expiration="soon")])
    with pytest.raises(ValueError, match="open_interest"):
        run([dict(chain()[0], open_interest="many")])
    with pytest.raises(ValueError, match="duplicate"):
        run(chain() + [chain()[0]])


@pytest.mark.parametrize("over, fragment", [
    ({"surprise": 1}, "surprise"),
    ({"symbol_field": ...}, "symbol_field"),
    ({"status_field": ...}, "status_field"),
    ({"status_field": "atm_iv"}, "distinct"),
    ({"symbol_contiguous": "yes"}, "symbol_contiguous"),
    ({"symbol_contiguous": True, "presorted": True}, "symbol_contiguous"),
    ({"rate": "high"}, "rate"),
    ({"rate": 0.04, "rate_field": "r"}, "rate"),
    ({"rate_field": ""}, "rate_field"),
    ({"max_symbol_rows": 0}, "max_symbol_rows"),
    ({"allow_unit_mismatch": "yes"}, "allow_unit_mismatch"),
    ({"rate_scale": 0}, "rate_scale"),
    ({"rate_date_field": "date"}, "rate_date_field"),
    ({"rate_max_age_days": -1, "rate_date_field": "d", "rate_value_field": "v"}, "rate_max_age_days"),
    ({"targets": []}, "targets"),
    ({"targets": [{"dte": 30}]}, "targets"),
    ({"targets": [{"dte": 30, "tolerance": 5}, {"dte": 30, "tolerance": 2}]}, "targets"),
    ({"targets": [{"dte": 0, "tolerance": 5}]}, "targets"),
    ({"wing_delta": 0.5}, "wing_delta"),
    ({"wing_delta": ...}, "wing_delta"),
    ({"atm_band": 0}, "atm_band"),
    ({"min_forward_pairs": 0}, "min_forward_pairs"),
    ({"call_values": ["call"], "put_values": ["call"]}, "call_values"),
    ({"fields": {}}, "fields"),
    ({"fields": {"nonsense": "x"}}, "nonsense"),
    ({"fields": {"atm_iv": "a", "forward": "a"}}, "distinct"),
    ({"fields": {"symbol": "symbol"}}, "symbol"),
    ({"fields": {"term_slope": "ts"}}, "term_pair"),
    ({"term_pair": [30, 60]}, "term_pair"),
    ({"term_pair": [30]}, "term_pair"),
    ({"oi_scope": "everything"}, "oi_scope"),
    ({"oi_scope": ...}, "oi_scope"),
    ({"oi_lag_sessions": 0}, "oi_lag_sessions"),
    ({"oi_lag_max_days": ...}, "oi_lag_max_days"),
    ({"open_interest_field": ...}, "open_interest_field"),
    ({"underlying_price_field": ...}, "underlying_price_field"),
    ({"end_before": "2026-1-1"}, "end_before"),
    ({"iv_min": 1.0, "iv_max": 0.5}, "iv_max"),
    ({"days_per_year": 0}, "days_per_year"),
    ({"presorted": "yes"}, "presorted"),
])
def test_bad_params_refuse_by_name(over, fragment):
    params = {k: v for k, v in dict(PARAMS, **over).items() if v is not ...}
    problems = ChainDayFeatures.validate_params(params)
    assert any(fragment in p for p in problems), problems
    with pytest.raises(ConfigError):
        ChainDayFeatures("c", params)


def test_the_reference_params_validate_clean():
    assert ChainDayFeatures.validate_params(PARAMS) == []
    assert ChainDayFeatures.validate_params(dict(PARAMS, presorted=True, iv_min=0.01, iv_max=4.0,
                                                 days_per_year=365.0)) == []
    assert ChainDayFeatures.validate_params(dict(PARAMS, symbol_contiguous=True, rate=0.04)) == []
    assert ChainDayFeatures.validate_params(dict(PARAMS, rate_field="r", rate_scale=0.01)) == []
    assert ChainDayFeatures.validate_params(dict(PARAMS, rate_date_field="d", rate_value_field="v",
                                                 rate_max_age_days=5, rate_scale=0.01)) == []


def test_the_pack_surface():
    assert ChainDayFeatures.role == "transform"
    assert ChainDayFeatures.outputs == ("records", "summary")
    assert chain_features.NODE_KINDS == ()
    for name in ("ChainDayFeatures", "FEATURES", "NODE_KINDS"):
        assert name in chain_features.__all__
    assert "atm_iv" in FEATURES and "oi_change" in FEATURES


# -- scale: symbol-contiguous reading, rates, counts, the real reader ---------------------------


def run_with(rows, rates=None, **over):
    params = {k: v for k, v in dict(PARAMS, **over).items() if v is not ...}
    ctx = NodeContext(name="t", asof="2025-12-31", run_dir="/nonexistent")
    inputs = {"records": rows}
    if rates is not None:
        inputs["rates"] = rates
    return ChainDayFeatures("features", params).run(ctx, inputs)


def newest_first_members():
    """Two roots, one member each, each member newest date first (the pull's order)."""
    members = []
    for symbol, oi_scale in (("AAA", 1), ("ZZZ", 3)):
        rows = []
        for day, call_oi, put_oi in reversed([("2020-03-02", 10, 20), ("2020-03-03", 11, 21),
                                              ("2020-03-04", 12, 24), ("2020-03-05", 15, 30)]):
            for r in chain(symbol=symbol, day=day, expiration="2020-03-30", strikes=[99, 100, 101, 102, 103]):
                r["open_interest"] = (call_oi if r["right"] == "call" else put_oi) * oi_scale
                rows.append(r)
        members.append(rows)
    return members


def test_symbol_contiguous_flushes_per_symbol_and_computes_oi_change_in_true_date_order():
    members = newest_first_members()
    rows = [r for member in members for r in member]
    reference = run_with(list(reversed(rows)))["records"]            # all-in-memory mode, any order
    out = run_with(rows, symbol_contiguous=True)["records"]
    assert out == reference
    by = {(r["symbol"], r["date"]): r for r in out}
    assert by[("AAA", "2020-03-04")]["oi_change"] == pytest.approx(180 / 150 - 1)
    assert by[("ZZZ", "2020-03-05")]["oi_change"] == pytest.approx(225 * 3 / (160 * 3) - 1)
    assert [r["date"] for r in out if r["symbol"] == "AAA"] == sorted(r["date"] for r in out if r["symbol"] == "AAA")


def test_symbol_contiguous_refuses_a_symbol_that_reappears():
    a, z = newest_first_members()
    with pytest.raises(ValueError, match="reappear"):
        run_with(a[:10] + z + a[10:], symbol_contiguous=True)


def test_symbol_contiguous_flushes_a_symbol_before_the_next_one_is_read_past():
    a, z = newest_first_members()
    seen = []

    def stream():
        for r in a + z:
            seen.append(r["symbol"])
            yield r

    node = ChainDayFeatures("f", dict(PARAMS, symbol_contiguous=True))
    first = next(node._groups(stream()))
    assert first[0] == "AAA" and first[1] == "2020-03-02"            # the oldest date first, whatever the arrival order
    assert len(seen) == len(a) + 1                                    # released on the first ZZZ row, not at the end


RATE = 0.045


def rated_chain(**over):
    rows = chain(**over)
    years = 28 / 365
    for r in rows:
        mid = black76(r["right"], 100.0, r["strike"], 0.30, years, RATE)
        r["bid"], r["ask"] = mid - 0.005, mid + 0.005
    return rows


def test_a_constant_rate_recovers_the_vol_the_zero_rate_misses():
    rows = rated_chain()
    zero = only(run_with(rows))
    rated = only(run_with(rows, rate=RATE))
    assert rated["atm_iv"] == pytest.approx(0.30, abs=1e-4)
    assert rated["forward"] == pytest.approx(100.0, abs=1e-6)
    assert abs(zero["atm_iv"] - 0.30) > 5e-4 or abs(zero["forward"] - 100.0) > 1e-3


def test_the_rate_field_is_a_per_row_rate_scaled_to_a_decimal():
    rows = [dict(r, r=RATE * 100) for r in rated_chain()]
    row = only(run_with(rows, rate_field="r", rate_scale=0.01))
    assert row["atm_iv"] == pytest.approx(0.30, abs=1e-4)


def test_a_rates_table_is_joined_as_of_strictly_before_the_day():
    rows = rated_chain()
    table = [{"d": "2020-02-28", "v": "4.5"}, {"d": DAY, "v": "9.9"}, {"d": "2020-03-03", "v": "."}]
    row = only(run_with(rows, rates=table, rate_date_field="d", rate_value_field="v", rate_scale=0.01,
                        rate_max_age_days=5))
    assert row["atm_iv"] == pytest.approx(0.30, abs=1e-4)            # the 4.5 of 2020-02-28, not same-day 9.9


def test_a_day_with_no_rate_inside_the_age_is_a_no_rate_row_not_a_zero_rate():
    rows = rated_chain() + rated_chain(day="2020-03-09")
    table = [{"d": "2020-02-28", "v": "4.5"}]
    out = run_with(rows, rates=table, rate_date_field="d", rate_value_field="v", rate_scale=0.01, rate_max_age_days=5)
    assert [r["status"] for r in out["records"]] == ["ok", "no_rate"]
    assert all(v is None for k, v in out["records"][1].items() if k not in ("symbol", "date", "target_dte", "status"))
    assert out["records"][1]["target_dte"] == 30
    assert out["summary"]["targets"]["30"]["no_rate"] == 1


def test_rates_input_refusals():
    rows = rated_chain()
    common = dict(rate_date_field="d", rate_value_field="v", rate_max_age_days=5)
    with pytest.raises(ValueError, match="end_before"):
        run_with(rows, rates=[{"d": "2026-02-03", "v": "4"}], **common)
    with pytest.raises(ValueError, match="duplicate"):
        run_with(rows, rates=[{"d": "2020-02-28", "v": "4"}, {"d": "2020-02-28", "v": "4"}], **common)
    with pytest.raises(ValueError, match="rates"):
        run_with(rows, rates=[{"d": "2020-02-28", "v": "4"}])                 # no rate_* params declared
    with pytest.raises(ValueError, match="rate"):
        run_with(rows, rates=[{"d": "2020-02-28", "v": "x"}], **common)
    with pytest.raises(ValueError, match="rates"):
        run_with(rows, rates=[{"d": "2020-02-28", "v": "4"}], rate_field="r", **common)


def test_no_expiry_and_no_forward_are_counted_the_same_way_and_both_leave_a_row():
    thin = chain(strikes=[99, 100]) + chain(symbol="BBB", dte=91)
    out = run_with(thin, targets=[{"dte": 30, "tolerance": 5}, {"dte": 90, "tolerance": 5}], min_forward_pairs=3)
    statuses = {(r["symbol"], r["target_dte"]): r["status"] for r in out["records"]}
    assert statuses == {("AAA", 30): "no_forward", ("AAA", 90): "no_expiry", ("BBB", 30): "no_expiry",
                        ("BBB", 90): "ok"}
    counts = out["summary"]["targets"]
    assert counts["30"]["no_forward"] == 1 and counts["90"]["no_expiry"] == 1 and counts["30"]["emitted"] == 0
    assert counts["90"]["emitted"] == 1 and counts["30"]["no_expiry"] == 1


def test_unsolved_implied_vols_are_counted():
    rows = chain(strikes=range(95, 106))
    for r in rows:
        if r["strike"] == 100.0:
            r["bid"], r["ask"] = 1e-9, 2e-9                          # a mid no vol prices at the money
    out = run_with(rows, iv_max=0.5)
    assert out["summary"]["iv_unsolved"] >= 2 and out["summary"]["iv_solved"] > 0


def test_an_oi_lag_past_the_calendar_guard_is_counted():
    out = run_with(days_of_oi(), oi_lag_max_days=1)
    assert out["summary"]["oi_lag_too_old"] >= 1


def test_the_reader_counters_are_copied_into_the_summary():
    class Reporting(list):
        def report(self):
            return {"skipped_rows": 7}

    assert run_with(Reporting(chain()))["summary"]["reader"] == {"skipped_rows": 7}
    assert "reader" not in run_with(chain())["summary"]


def test_features_through_the_real_unit_reader_over_newest_first_members(tmp_path):
    import json
    from dskit.onboarding import OnboardingRoot, run_acquisition
    from dskit.pipeline.libs.observations import ObservationUnitRows
    members = newest_first_members()
    for r in members[1]:                         # the second root lands later in time, as a pull would
        r["date"] = (date.fromisoformat(r["date"]) + timedelta(days=31)).isoformat()
        r["expiration"] = expiry_of("2020-03-30", 31)
    data = tmp_path / "data"
    data.mkdir()
    ob = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = ob.registry()
    vid = registry.register("source_config", {
        "name": "theta", "catalog_source": "theta-src", "connector": "localfiles",
        "config": {"path": str(data), "effective_field": "date"}}, origin="test")
    registry.transition(vid, "active", origin="test")
    for member in members:                                   # one acquisition per root, as the pull lands them
        with open(data / "option_eod.jsonl", "w", encoding="utf-8") as fh:
            for r in member:
                fh.write(json.dumps({k: v for k, v in r.items()}, sort_keys=True) + "\n")
        run_acquisition(ob, registry, "theta", "option_eod", "backfill")
    reader = ObservationUnitRows("chain", {
        "root": ob.root, "source": "theta", "stream": "option_eod", "unit_fields": ["symbol", "date"],
        "fields": ["symbol", "date", "expiration", "strike", "right", "bid", "ask", "open_interest", "volume",
                   "underlying_price"]})
    ctx = NodeContext(name="t", asof="2025-12-31", run_dir=str(tmp_path / "run"))
    reader.fingerprint()
    rows = reader.run(ctx, {})["records"]
    out = ChainDayFeatures("features", dict(PARAMS, symbol_contiguous=True)).run(ctx, {"records": rows})
    reference = run_with([r for member in members for r in member])["records"]
    ordered = lambda rows: sorted(rows, key=lambda r: (r["symbol"], r["date"], r["target_dte"]))
    assert ordered(out["records"]) == ordered(reference) and len(reference) == 8
    assert out["summary"]["reader"]["units"] == 8 and out["summary"]["reader"]["skipped_rows"] == 0


def test_a_constant_rate_is_scaled_like_the_table():
    rows = rated_chain()
    percent = only(run_with(rows, rate=RATE * 100, rate_scale=0.01))
    assert percent["atm_iv"] == pytest.approx(0.30, abs=1e-4)
    assert percent["forward"] == pytest.approx(100.0, abs=1e-6)


def test_max_symbol_rows_refuses_a_root_that_would_exhaust_memory_naming_it():
    rows = chain(symbol="BIGROOT")
    with pytest.raises(ValueError, match=r"BIGROOT.*max_symbol_rows|max_symbol_rows.*BIGROOT"):
        run_with(rows, symbol_contiguous=True, max_symbol_rows=len(rows) - 1)
    assert len(run_with(rows, symbol_contiguous=True, max_symbol_rows=len(rows))["records"]) == 1
    with pytest.raises(ValueError, match="max_symbol_rows"):
        run_with(rows, max_symbol_rows=len(rows) - 1)                      # also guards the hold-everything mode


def test_a_reader_reporting_split_units_refuses_unless_allowed():
    class Splitting(list):
        def report(self):
            return {"unit_row_mismatch": 2, "skipped_rows": 3}

    with pytest.raises(ValueError, match="unit_row_mismatch"):
        run_with(Splitting(chain()))
    out = run_with(Splitting(chain()), allow_unit_mismatch=True)
    assert out["summary"]["reader"]["unit_row_mismatch"] == 2


# -- the real unit reader: spanning, revising, splitting -------------------------------------


READ_FIELDS = ["symbol", "date", "expiration", "strike", "right", "bid", "ask", "open_interest", "volume",
               "underlying_price"]


def land(tmp_path, members):
    """Write ``members`` ([(acquisition name, rows, acquired_at)]) as onboarded stream members."""
    import json
    root = tmp_path / "ob"
    for name, rows, acquired in members:
        directory = root / "observations" / "theta" / name
        directory.mkdir(parents=True)
        with open(directory / "option_eod.jsonl", "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps({"acquired_at": acquired, "data": r, "kind": "observation",
                                     "mode": "backfill", "stream": "option_eod",
                                     "effective_date": r["date"]}, sort_keys=True) + "\n")
    return str(root)


def read_through(tmp_path, root, **over):
    from dskit.pipeline.libs.observations import ObservationUnitRows
    reader = ObservationUnitRows("chain", {"root": root, "source": "theta", "stream": "option_eod",
                                           "unit_fields": ["symbol", "date"], "fields": READ_FIELDS})
    ctx = NodeContext(name="t", asof="2025-12-31", run_dir=str(tmp_path / "run"))
    reader.fingerprint()
    rows = reader.run(ctx, {})["records"]
    params = dict(PARAMS, symbol_contiguous=True, **over)
    return ChainDayFeatures("features", params).run(ctx, {"records": rows})


def oi_days(symbol, plan, **kw):
    rows = []
    for day, call_oi, put_oi in plan:
        for r in chain(symbol=symbol, day=day, expiration="2020-03-30", strikes=[99, 100, 101, 102, 103], **kw):
            r["open_interest"] = call_oi if r["right"] == "call" else put_oi
            rows.append(r)
    return rows


PLAN = [("2020-03-05", 15, 30), ("2020-03-04", 12, 24), ("2020-03-03", 11, 21), ("2020-03-02", 10, 20)]


def test_one_root_spanning_two_consecutive_members_is_one_symbol_in_true_date_order(tmp_path):
    newer, older = oi_days("AAA", PLAN[:2]), oi_days("AAA", PLAN[2:])
    other = oi_days("BBB", PLAN)
    root = land(tmp_path, [("20200101T000000Z-a", older, "2026-01-01T00:00:00+00:00"),
                           ("20200101T000100Z-b", newer, "2026-01-02T00:00:00+00:00"),
                           ("20200101T000200Z-c", other, "2026-01-03T00:00:00+00:00")])
    out = read_through(tmp_path, root)
    aaa = [r for r in out["records"] if r["symbol"] == "AAA"]
    assert [r["date"] for r in aaa] == ["2020-03-02", "2020-03-03", "2020-03-04", "2020-03-05"]
    assert aaa[2]["oi_change"] == pytest.approx(180 / 150 - 1)             # needs 03-02 from the OTHER member
    assert out["summary"]["reader"]["skipped_rows"] == 0 and out["summary"]["reader"]["members"] == 3


def test_a_later_member_that_revises_a_unit_wins(tmp_path):
    original = oi_days("AAA", PLAN)
    revised = oi_days("AAA", [("2020-03-03", 22, 44)])
    root = land(tmp_path, [("20200101T000000Z-a", original, "2026-01-01T00:00:00+00:00"),
                           ("20200101T000100Z-b", revised, "2026-01-02T00:00:00+00:00")])
    out = read_through(tmp_path, root)
    by_day = {r["date"]: r for r in out["records"]}
    assert (by_day["2020-03-03"]["oi_call"], by_day["2020-03-03"]["oi_put"]) == (5 * 22, 5 * 44)    # not 5*11, 5*21
    assert (by_day["2020-03-04"]["oi_call"], by_day["2020-03-04"]["oi_put"]) == (5 * 12, 5 * 24)
    assert out["summary"]["reader"]["skipped_rows"] == 10 and out["summary"]["reader"]["unit_row_mismatch"] == 0


def test_a_split_unit_refuses_and_is_allowed_by_a_param(tmp_path):
    full = oi_days("AAA", PLAN[3:])
    half = full[:5]                                   # the same (AAA, 2020-03-02) unit, half of it, in the newer member
    root = land(tmp_path, [("20200101T000000Z-a", full, "2026-01-01T00:00:00+00:00"),
                           ("20200101T000100Z-b", half + oi_days("AAA", PLAN[:1]), "2026-01-02T00:00:00+00:00")])
    with pytest.raises(ValueError, match="unit_row_mismatch"):
        read_through(tmp_path, root)
    out = read_through(tmp_path, root, allow_unit_mismatch=True)
    assert out["summary"]["reader"]["unit_row_mismatch"] == 1
