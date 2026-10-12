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
    "end_before": "2026-01-01", "target_field": "target_dte",
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
    assert [r["target_dte"] for r in out["records"]] == [30]
    assert out["records"][0]["term_slope"] is None
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
    out = run(chain(strikes=[99, 100]), min_forward_pairs=3)
    row = only(out)
    assert row["forward"] is None and row["atm_iv"] is None and row["straddle_ratio"] is None
    assert row["oi_call"] == 20                                    # aggregates do not need a forward
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
    assert set(row) == {"symbol", "date", "target_dte", "iv30", "fwd"}


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


def test_the_pack_surface():
    assert ChainDayFeatures.role == "transform"
    assert ChainDayFeatures.outputs == ("records", "summary")
    assert chain_features.NODE_KINDS == ()
    for name in ("ChainDayFeatures", "FEATURES", "NODE_KINDS"):
        assert name in chain_features.__all__
    assert "atm_iv" in FEATURES and "oi_change" in FEATURES
