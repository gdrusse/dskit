"""bar_features: daily bar and option-trade bar features, hand-computed, leak-tested, refusals."""

import math

import pytest

from dskit.pipeline.libs.bar_features import (
    NODE_KINDS, AsTradedClose, DailyBarFeatures, TradeBarFeatures, register)
from dskit.pipeline.node import NodeContext, NodeKindRegistry

CTX = NodeContext(name="t", asof="2026-01-01", run_dir=".")

DAILY = {
    "entity_field": "sym", "date_field": "date", "close_field": "close", "volume_field": "vol",
    "benchmark_date_field": "date", "benchmark_close_field": "close",
    "volume_liquidity": {"window": 2, "fields": {
        "log_volume": "lv", "log_volume_ratio": "lvr", "log_dollar_volume": "ldv", "amihud": "ami"}},
    "market_relative": {"windows": [1, 2], "relative_field": "rel{window}", "beta_window": 3,
                        "beta_field": "beta", "corr_field": "corr"},
}


def _bars(sym, closes, vols):
    return [{"sym": sym, "date": f"2026-01-{i + 2:02d}", "close": c, "vol": v}
            for i, (c, v) in enumerate(zip(closes, vols))]


def _bench(closes):
    return [{"date": f"2026-01-{i + 2:02d}", "close": c} for i, c in enumerate(closes)]


def _daily(bars, bench, **over):
    node = DailyBarFeatures("d", {**DAILY, **over})
    return node.run(CTX, {"bars": bars, "benchmark": bench})["records"]


def test_volume_liquidity_hand_computed():
    out = _daily(_bars("A", [100, 110, 121], [10, 20, 40]), _bench([100, 100, 110]))
    assert [r["date"] for r in out] == ["2026-01-02", "2026-01-03", "2026-01-04"]
    assert out[0]["lv"] == pytest.approx(math.log(10))
    assert out[0]["lvr"] is None and out[1]["lvr"] == pytest.approx(math.log(20 / 15))
    assert out[2]["lvr"] == pytest.approx(math.log(40 / 30))
    assert out[2]["ldv"] == pytest.approx(math.log(121 * 40))
    assert out[0]["ami"] is None and out[1]["ami"] is None
    r = math.log(1.1)
    assert out[2]["ami"] == pytest.approx((r / 2200 + r / 4840) / 2)


def test_market_relative_hand_computed():
    out = _daily(_bars("A", [100, 110, 121], [10, 20, 40]), _bench([100, 100, 110]))
    assert out[0]["rel1"] is None
    assert out[1]["rel1"] == pytest.approx(math.log(1.1))
    assert out[2]["rel1"] == pytest.approx(0.0)
    assert out[2]["rel2"] == pytest.approx(math.log(1.21) - math.log(1.1))
    assert out[1]["rel2"] is None


def test_beta_and_corr_of_a_doubled_market():
    m = [0.01, -0.02, 0.03]
    mc, ec = [100.0], [50.0]
    for x in m:
        mc.append(mc[-1] * math.exp(x))
        ec.append(ec[-1] * math.exp(2 * x))
    out = _daily(_bars("A", ec, [1] * 4), _bench(mc))
    assert [r["beta"] for r in out[:3]] == [None] * 3
    assert out[3]["beta"] == pytest.approx(2.0) and out[3]["corr"] == pytest.approx(1.0)


def test_entities_do_not_bleed_and_missing_market_date_is_none():
    bars = _bars("A", [100, 110, 121], [10, 20, 40]) + _bars("B", [5, 5, 5], [1, 1, 1])
    out = _daily(bars, _bench([100, 100, 110])[:2])
    by = {(r["sym"], r["date"]): r for r in out}
    assert by[("B", "2026-01-02")]["lvr"] is None
    assert by[("B", "2026-01-03")]["lvr"] == pytest.approx(0.0)
    assert by[("A", "2026-01-04")]["rel1"] is None


def test_daily_leak_feature_at_t_ignores_later_rows():
    closes, vols = [100, 103, 101, 108, 107, 111], [5, 7, 6, 9, 4, 8]
    bench = [50, 51, 50, 53, 52, 55]
    full = _daily(_bars("A", closes, vols), _bench(bench))
    cut = _daily(_bars("A", closes[:4], vols[:4]), _bench(bench[:4]))
    altered = _daily(_bars("A", closes[:4] + [1, 1], vols[:4] + [99, 99]), _bench(bench[:4] + [1, 1]))
    assert full[:4] == cut == altered[:4]


def test_daily_only_requested_block_is_emitted():
    keep = {k: v for k, v in DAILY.items() if k != "market_relative"}
    node = DailyBarFeatures("d", keep)
    out = node.run(CTX, {"bars": _bars("A", [1, 2], [1, 1])})["records"]
    assert set(out[0]) == {"sym", "date", "lv", "lvr", "ldv", "ami"}


@pytest.mark.parametrize("bad", [
    {"surprise": 1},
    {"close_field": ""},
    {"volume_liquidity": {"window": 1, "fields": {"log_volume": "lv"}}},
    {"volume_liquidity": {"window": 2, "fields": {"nope": "lv"}}},
    {"market_relative": {"windows": [], "relative_field": "r{window}"}},
    {"market_relative": {"windows": [1], "relative_field": "r"}},
    {"market_relative": {"beta_window": 3, "beta_field": "lv"}},
])
def test_daily_refusals(bad):
    assert DailyBarFeatures.validate_params({**DAILY, **bad})


def test_daily_requires_a_block_and_fields():
    base = {k: DAILY[k] for k in ("entity_field", "date_field", "close_field")}
    assert DailyBarFeatures.validate_params(base)
    assert DailyBarFeatures.validate_params(
        {**base, "volume_liquidity": DAILY["volume_liquidity"]})  # no volume_field


def test_daily_duplicate_rows_refused():
    bars = _bars("A", [1, 2], [1, 1]) + _bars("A", [1, 2], [1, 1])
    with pytest.raises(ValueError, match="duplicate"):
        _daily(bars, _bench([1, 2]))


# -- trade bars --------------------------------------------------------------

TRADE = {
    "entity_field": "sym", "date_field": "date", "right_field": "cp", "strike_field": "k",
    "size_field": "n", "price_field": "px", "expiry_field": "exp",
    "call_values": ["C"], "put_values": ["P"], "single_expiry": False,
    "underlying_entity_field": "sym", "underlying_date_field": "date", "underlying_close_field": "close",
    "moneyness_band": 0.05, "strict_prior": False, "clock_note": "toy bars are end-of-day, known at t",
    "fields": {"log_call_put_ratio": "lcp", "put_share": "ps", "log_total_volume": "ltv",
               "log_trade_count": "ltc", "n_strikes": "nk", "vw_log_moneyness": "vwm",
               "atm_straddle_ratio": "atm"},
}


def _t(d, exp, k, cp, n, px, sym="X"):
    return {"sym": sym, "date": d, "exp": exp, "k": k, "cp": cp, "n": n, "px": px}


D1, D2 = "2026-01-02", "2026-01-05"
TRADES = [
    _t(D1, "E1", 100, "C", 3, 5.0), _t(D1, "E1", 100, "P", 1, 4.0),
    _t(D1, "E1", 110, "C", 2, 1.0), _t(D1, "E1", 90, "P", 4, 0.5),
    _t(D2, "E1", 100, "C", 2, 6.0),
]
CLOSES = [{"sym": "X", "date": D1, "close": 100.0}, {"sym": "X", "date": D2, "close": 100.0}]


def _trade(rows, closes=CLOSES, **over):
    node = TradeBarFeatures("t", {**TRADE, **over})
    return node.run(CTX, {"trades": rows, "underlying": closes})["records"]


def test_trade_features_hand_computed():
    out = _trade(TRADES)
    a, b = out
    assert (a["sym"], a["date"]) == ("X", D1)
    assert a["lcp"] == pytest.approx(0.0) and a["ps"] == pytest.approx(0.5)
    assert a["ltv"] == pytest.approx(math.log(10)) and a["ltc"] == pytest.approx(math.log(4))
    assert a["nk"] == 3
    assert a["vwm"] == pytest.approx((2 * math.log(1.1) + 4 * math.log(0.9)) / 10)
    assert a["atm"] == pytest.approx(0.09)
    assert b["lcp"] is None and b["ps"] == 0.0 and b["atm"] is None and b["nk"] == 1
    assert b["vwm"] == pytest.approx(0.0)


def test_atm_needs_same_expiry_and_band():
    rows = [_t(D1, "E1", 100, "C", 1, 5.0), _t(D1, "E2", 100, "P", 1, 4.0)]
    assert _trade(rows)[0]["atm"] is None
    far = [_t(D1, "E1", 120, "C", 1, 5.0), _t(D1, "E1", 120, "P", 1, 4.0)]
    assert _trade(far)[0]["atm"] is None
    assert _trade(far, moneyness_band=0.25)[0]["atm"] == pytest.approx(0.09)


def test_missing_underlying_close_nulls_only_moneyness_fields():
    a = _trade(TRADES, closes=[])[0]
    assert a["vwm"] is None and a["atm"] is None and a["nk"] == 3
    assert a["ps"] == pytest.approx(0.5)


def test_streaming_matches_list_input():
    expect = _trade(TRADES)
    node = TradeBarFeatures("t", {**TRADE, "presorted": True})
    gen = node.stream(iter(TRADES), {("X", D1): 100.0, ("X", D2): 100.0})
    assert list(gen) == expect
    assert node.run(CTX, {"trades": iter(TRADES), "underlying": CLOSES})["records"] == expect


def test_streaming_reads_only_through_the_first_key_change():
    node = TradeBarFeatures("t", {**TRADE, "presorted": True})
    taken = []

    def feed():
        for row in TRADES:
            taken.append(row)
            yield row

    next(node.stream(feed(), {}))
    assert len(taken) == 5  # first group is rows 0-3; row 4 is the key change that closes it


def test_presorted_refuses_a_reappearing_key():
    rows = [TRADES[0], TRADES[4], TRADES[1]]
    node = TradeBarFeatures("t", {**TRADE, "presorted": True})
    with pytest.raises(ValueError, match="presorted"):
        node.run(CTX, {"trades": rows, "underlying": CLOSES})


def test_unsorted_input_is_grouped_and_sorted():
    assert _trade(list(reversed(TRADES))) == _trade(TRADES)


def test_trade_leak_features_at_t_ignore_later_trades():
    full = _trade(TRADES)
    only_d1 = _trade([r for r in TRADES if r["date"] == D1])
    changed = [r if r["date"] == D1 else {**r, "n": 99, "k": 50, "cp": "P"} for r in TRADES]
    assert full[0] == only_d1[0] == _trade(changed)[0]


def test_unknown_right_and_bad_rows_refused():
    with pytest.raises(ValueError, match="right"):
        _trade([_t(D1, "E1", 100, "Z", 1, 1.0)])
    with pytest.raises(ValueError, match="size"):
        _trade([_t(D1, "E1", 100, "C", -1, 1.0)])
    with pytest.raises(ValueError, match="strike"):
        _trade([_t(D1, "E1", 0, "C", 1, 1.0)])


@pytest.mark.parametrize("bad", [
    {"surprise": 1},
    {"call_values": []},
    {"call_values": ["C"], "put_values": ["C"]},
    {"fields": {"nope": "x"}},
    {"fields": {"put_share": "ps", "n_strikes": "ps"}},
    {"fields": {"put_share": "sym"}},
    {"moneyness_band": 0},
    {"presorted": "yes"},
])
def test_trade_refusals(bad):
    assert TradeBarFeatures.validate_params({**TRADE, **bad})


def test_trade_dependencies_are_demanded_by_the_fields_declared():
    lean = {k: v for k, v in TRADE.items() if k not in (
        "underlying_entity_field", "underlying_date_field", "underlying_close_field",
        "moneyness_band", "price_field", "expiry_field", "single_expiry", "clock_note")}
    lean["strict_prior"] = False
    lean["clock_note"] = "n"
    lean["fields"] = {"put_share": "ps", "n_strikes": "nk"}
    assert TradeBarFeatures.validate_params(lean) == []
    assert TradeBarFeatures.validate_params({**lean, "strict_prior": True, "clock_note": "x"})  # note with strict
    assert TradeBarFeatures.validate_params({k: v for k, v in lean.items() if k != "clock_note"})
    lean["fields"] = {"vw_log_moneyness": "vwm"}
    assert TradeBarFeatures.validate_params(lean)
    lean["fields"] = {"atm_straddle_ratio": "atm"}
    assert TradeBarFeatures.validate_params(lean)


def test_pack_registers_both_kinds_idempotently():
    reg = NodeKindRegistry()
    register(reg)
    register(reg)
    assert {n for n, _ in NODE_KINDS} == set(reg.kinds())
    assert {c for _, c in NODE_KINDS} == {AsTradedClose, DailyBarFeatures, TradeBarFeatures}


# -- review fixes ----------------------------------------------------------------

@pytest.mark.parametrize("bad", ["01/02/2026", "Jan 2 2026", "2026-1-2", "2026-01-02 junk", None, 20260102.5])
def test_non_iso_dates_refused_everywhere(bad):
    bars = _bars("A", [1, 2], [1, 1])
    bars[0]["date"] = bad
    with pytest.raises(ValueError):
        _daily(bars, _bench([1, 2]))
    bench = _bench([1, 2])
    bench[0]["date"] = bad
    with pytest.raises(ValueError):
        _daily(_bars("A", [1, 2], [1, 1]), bench)
    with pytest.raises(ValueError):
        _trade([{**TRADES[0], "date": bad}])
    with pytest.raises(ValueError):
        _trade(TRADES, closes=[{"sym": "X", "date": bad, "close": 1.0}])


def test_float_windows_are_coerced_and_named_from_the_int():
    out = _daily(_bars("A", [100, 110, 121], [10, 20, 40]), _bench([100, 100, 110]),
                 volume_liquidity={"window": 2.0, "fields": {"log_volume_ratio": "lvr"}},
                 market_relative={"windows": [1.0], "relative_field": "rel{window}"})
    assert "rel1" in out[0] and "rel1.0" not in out[0] and out[1]["lvr"] is not None


@pytest.mark.parametrize("col,val", [("close", float("inf")), ("vol", float("inf")), ("vol", "7"),
                                     ("close", True)])
def test_daily_refuses_non_numbers(col, val):
    bars = _bars("A", [1, 2, 3], [1, 1, 1])
    bars[1][col] = val
    with pytest.raises(ValueError):
        _daily(bars, _bench([1, 2, 3]))


@pytest.mark.parametrize("col,val", [("close", 0), ("close", -5), ("vol", 0), ("vol", -1),
                                     ("vol", float("nan")), ("close", None), ("vol", None)])
def test_daily_undefined_values_are_none_not_imputed(col, val):
    bars = _bars("A", [100, 110, 121, 130], [10, 20, 40, 50])
    bars[1][col] = val
    out = _daily(bars, _bench([100, 100, 110, 110]))
    assert out[1]["lv"] is None or col == "close"
    assert out[1]["lvr"] is None or col == "close"
    if col == "close":
        assert out[1]["rel1"] is None and out[2]["rel1"] is None
    else:
        assert out[1]["rel1"] is not None
    assert len(out) == 4


def test_empty_inputs_behave_alike():
    assert _daily([], _bench([1])) == []
    assert _trade([]) == []


def test_duplicate_benchmark_dates_refused():
    with pytest.raises(ValueError, match="benchmark"):
        _daily(_bars("A", [1, 2], [1, 1]), _bench([1, 2]) + _bench([1]))


def test_unwired_and_duplicate_underlying_refused():
    node = TradeBarFeatures("t", dict(TRADE))
    with pytest.raises(ValueError, match="underlying"):
        node.run(CTX, {"trades": TRADES})
    with pytest.raises(ValueError, match="duplicate"):
        _trade(TRADES, closes=CLOSES + CLOSES[:1])


def test_counts_and_prices_must_be_real_numbers():
    for field, bad in (("n", "3"), ("n", True), ("px", None), ("k", "100"), ("n", float("nan"))):
        with pytest.raises(ValueError):
            _trade([{**TRADES[0], field: bad}])


@pytest.mark.parametrize("bad", [None, 1.5, True])
def test_bad_entity_keys_refused(bad):
    with pytest.raises(ValueError, match="entity"):
        _trade([{**TRADES[0], "sym": bad}])
    bars = _bars("A", [1, 2], [1, 1])
    bars[0]["sym"] = bad
    with pytest.raises(ValueError, match="entity"):
        _daily(bars, _bench([1, 2]))


def test_mixed_entity_types_refused():
    with pytest.raises(ValueError, match="entity"):
        _trade([TRADES[0], {**TRADES[0], "sym": 7}])
    bars = _bars("A", [1, 2], [1, 1]) + _bars("B", [1, 2], [1, 1])
    bars[3]["sym"] = 7
    with pytest.raises(ValueError, match="entity"):
        _daily(bars, _bench([1, 2]))


def test_presorted_refuses_dates_going_backwards_within_an_entity():
    node = TradeBarFeatures("t", {**TRADE, "presorted": True})
    rows = [TRADES[4], TRADES[0]]  # D2 then D1
    with pytest.raises(ValueError, match="presorted"):
        node.run(CTX, {"trades": rows, "underlying": CLOSES})


def test_presorted_refuses_an_entity_returning():
    node = TradeBarFeatures("t", {**TRADE, "presorted": True})
    rows = [TRADES[0], _t(D1, "E1", 100, "C", 1, 1.0, sym="Y"), TRADES[1]]
    with pytest.raises(ValueError, match="presorted"):
        node.run(CTX, {"trades": rows, "underlying": CLOSES})


def test_atm_needs_an_expiry_declaration():
    lean = {k: v for k, v in TRADE.items() if k not in ("expiry_field", "single_expiry")}
    assert TradeBarFeatures.validate_params(lean)
    assert TradeBarFeatures.validate_params({**lean, "single_expiry": True}) == []
    assert TradeBarFeatures.validate_params({**lean, "single_expiry": True, "expiry_field": "exp"})


def test_params_tuples_match_their_docstrings():
    for cls in (DailyBarFeatures, TradeBarFeatures):
        assert [p for p in cls._PARAMS if f"``{p}``" not in cls.__doc__] == []


# -- strict_prior / clock_note ---------------------------------------------------

STRICT = {k: v for k, v in TRADE.items() if k not in ("strict_prior", "clock_note")}
CAL = [{"sym": "X", "date": d, "close": 100.0} for d in (D1, D2, "2026-01-06")]


def test_strict_prior_is_the_default_and_clock_note_gates_same_day():
    assert TradeBarFeatures.validate_params(STRICT) == []
    assert TradeBarFeatures.validate_params({**STRICT, "strict_prior": False})
    assert TradeBarFeatures.validate_params({**STRICT, "strict_prior": False, "clock_note": " "})
    assert TradeBarFeatures.validate_params({**STRICT, "strict_prior": False, "clock_note": "ok"}) == []
    assert TradeBarFeatures.validate_params({**STRICT, "clock_note": "ok"})  # note without same-day
    assert TradeBarFeatures.validate_params({**STRICT, "strict_prior": "no"})


def _strict(rows, cal=CAL):
    return TradeBarFeatures("t", dict(STRICT)).run(CTX, {"trades": rows, "underlying": cal})["records"]


def test_strict_prior_labels_trade_day_features_with_the_next_calendar_date():
    out = _strict(TRADES)
    assert [r["date"] for r in out] == [D2, "2026-01-06"]
    assert out[0]["ps"] == pytest.approx(0.5) and out[0]["atm"] == pytest.approx(0.09)


def test_strict_prior_drops_a_last_day_with_no_later_entry_and_needs_the_calendar():
    assert [r["date"] for r in _strict(TRADES, cal=CAL[:2])] == [D2]
    with pytest.raises(ValueError, match="underlying"):
        TradeBarFeatures("t", dict(STRICT)).run(CTX, {"trades": TRADES})


def test_strict_prior_leak_row_at_t_ignores_trades_dated_t_or_later():
    base = _strict([r for r in TRADES if r["date"] == D1])[0]
    changed = TRADES[:4] + [{**TRADES[4], "n": 99, "cp": "P"}, _t("2026-01-06", "E1", 50, "P", 9, 1.0)]
    assert _strict(changed)[0] == base
    assert base["date"] == D2


def test_same_day_leak_with_presorted_true():
    node = TradeBarFeatures("t", {**TRADE, "presorted": True})
    full = node.run(CTX, {"trades": TRADES, "underlying": CLOSES})["records"]
    changed = TRADES[:4] + [{**TRADES[4], "n": 99, "cp": "P", "k": 50}]
    cut = node.run(CTX, {"trades": changed, "underlying": CLOSES})["records"]
    assert full[0] == cut[0]


# -- second review ---------------------------------------------------------------

def test_owner_exports_a_public_date_parser():
    from dskit.pipeline.libs import observation_tables as owner
    assert "iso_day" in owner.__all__
    assert owner.iso_day("2026-01-02T05:00", "w").isoformat() == "2026-01-02"
    with pytest.raises(ValueError, match="w"):
        owner.iso_day("nope", "w")


def test_underlying_fields_are_all_or_none():
    lean = {k: v for k, v in TRADE.items() if k not in (
        "underlying_entity_field", "underlying_date_field", "underlying_close_field",
        "moneyness_band", "price_field", "expiry_field", "single_expiry")}
    lean.update(strict_prior=False, clock_note="n", fields={"put_share": "ps"})
    assert TradeBarFeatures.validate_params(lean) == []
    assert TradeBarFeatures.validate_params({**lean, "underlying_close_field": "close"})
    assert TradeBarFeatures.validate_params({
        **lean, "underlying_close_field": "close", "underlying_entity_field": "sym"})


def test_strict_mode_reports_dropped_trade_dates():
    cal = CAL[:2]  # D1, D2; a trade on a day outside the calendar, and on the last day, are dropped
    rows = TRADES + [_t("2026-01-03", "E1", 100, "C", 1, 1.0)]
    node = TradeBarFeatures("t", dict(STRICT))
    out = node.run(CTX, {"trades": rows, "underlying": cal})
    assert [r["date"] for r in out["records"]] == [D2]
    assert out["dropped_dates"] == [["X", "2026-01-03"], ["X", D2]]
    same = TradeBarFeatures("t", dict(TRADE)).run(CTX, {"trades": rows, "underlying": cal})
    assert same["dropped_dates"] == []


def test_volume_field_without_volume_liquidity_is_not_demanded():
    cfg = {k: v for k, v in DAILY.items() if k != "volume_liquidity"}
    bars = [{k: v for k, v in b.items() if k != "vol"} for b in _bars("A", [1, 2], [1, 1])]
    out = DailyBarFeatures("d", cfg).run(CTX, {"bars": bars, "benchmark": _bench([1, 2])})["records"]
    assert len(out) == 2


# -- as-traded close (point in time, strike basis) ------------------------------------

ATC = {"entity_field": "sym", "date_field": "date", "close_field": "close",
       "split_field": "split", "output_field": "raw_close",
       "irregular_field": "approx", "irregular_tolerance": 1e-6}


def _atc(rows, **over):
    return AsTradedClose("a", {**ATC, **over}).run(CTX, {"bars": rows})["records"]


def _split_bars():
    # a 10:1 split dated d3 (coefficient 10 on the split day), adjusted closes already divided
    rows = [("d1", 10.0, 1.0), ("d2", 11.0, 1.0), ("d3", 12.0, 10.0), ("d4", 13.0, 1.0)]
    return [{"sym": "A", "date": f"2026-01-0{i + 1}", "close": c, "split": k} for i, (_, c, k) in enumerate(rows)]


def test_as_traded_close_multiplies_by_later_splits_only():
    out = _atc(_split_bars())
    assert [r["raw_close"] for r in out] == [100.0, 110.0, 12.0, 13.0]    # split day itself is post-split
    assert [r["approx"] for r in out] == [0, 0, 0, 0]


def test_as_traded_close_is_point_in_time():
    """Rows before a later split change when it is appended; rows after never do."""
    base = _atc(_split_bars()[:2])
    later = _atc(_split_bars())
    assert [r["raw_close"] for r in base] == [10.0, 11.0]            # no split known yet
    assert later[3]["raw_close"] == 13.0 and later[0]["raw_close"] == 100.0


def test_as_traded_close_chains_splits_and_flags_irregular_ratios():
    rows = [{"sym": "A", "date": f"2026-01-0{i + 1}", "close": 1.0, "split": k}
            for i, k in enumerate([1.0, 1.25, 4.0, 1.0])]
    out = _atc(rows)
    assert out[0]["raw_close"] == pytest.approx(5.0) and out[1]["raw_close"] == pytest.approx(4.0)
    assert [r["approx"] for r in out] == [1, 0, 0, 0]           # only rows BEFORE the 1.25 (not whole) are approximate


def test_as_traded_close_per_entity_and_nulls_and_refusals():
    rows = _split_bars() + [{"sym": "B", "date": "2026-01-01", "close": None, "split": 1.0}]
    out = {(r["sym"], r["date"]): r for r in _atc(rows)}
    assert out[("B", "2026-01-01")]["raw_close"] is None and out[("A", "2026-01-01")]["raw_close"] == 100.0
    with pytest.raises(ValueError, match="split"):
        _atc([{"sym": "A", "date": "2026-01-01", "close": 1.0, "split": 0.0}])
    with pytest.raises(ValueError, match="duplicate"):
        _atc(_split_bars() + _split_bars()[:1])
    assert AsTradedClose.validate_params({**ATC, "irregular_tolerance": None})
    assert AsTradedClose.validate_params({k: v for k, v in ATC.items() if k != "split_field"})
