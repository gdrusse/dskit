"""Greeks aggregation, stock-equivalent attribution and exposure (ADR-0310).

Expected values are worked out independently of the classes under test.
The two pins against the index_options child live in
``children/index_options/tests/test_option_exposure_pins.py`` (core tests do
not import a child).
"""

import math

import pytest

from dskit.pipeline import option_exposure as ox
from dskit.pipeline.base import ConfigError
from dskit.pipeline.option_exposure import (
    BookGreeks, CellAttribution, ExposureSnapshot, LegGreeks, ParityForward,
    StaticDeltaHedge, StockEquivalent, fit_betas)
from dskit.pipeline.option_pricing import black76, black76_delta, black76_gamma

HEDGE = {"hedge_fee_per_share": 0.005, "hedge_half_spread_bps": 5.0, "hedge_borrow_rate": 0.005}
PARITY = {"parity_strikes": 3, "min_pairs": 2, "max_pair_spread_frac": 0.5}
LEG = {"rate": 0.0, "multiplier": 100, "min_years": 0.001, "vol_floor": 0.01}


def chain(forward, strikes, vol=0.2, years=30 / 365, half=0.05):
    """Synthetic one-expiry chain priced by Black-76 at ``forward``."""
    rows = []
    for k in strikes:
        for right in ("call", "put"):
            mid = black76(right, forward, k, vol, years)
            rows.append({"right": right, "strike": k, "bid": mid - half, "ask": mid + half})
    return rows


def test_all_exports():
    assert sorted(ox.__all__) == sorted(
        ["ParityForward", "LegGreeks", "BookGreeks", "StockEquivalent", "CellAttribution", "DirectionEdge",
         "ExposureSnapshot", "fit_betas", "BookGreeksReport", "StaticDeltaHedge",
         "ExpiryDelta", "ForwardHedge", "Concentration", "HardToBorrowProxy",
         "ExposureLimits"])


# ---- parity forward -------------------------------------------------------------------
def test_parity_forward_recovers_a_known_forward():
    rows = chain(101.5, [96, 98, 100, 102, 104, 106])
    forward, source = ParityForward(PARITY).from_chain(rows, 100.3)
    assert source == "parity" and forward == pytest.approx(101.5, abs=1e-9)


def test_parity_forward_uses_the_nearest_strikes_by_median():
    rows = chain(101.5, [100, 102, 150])
    rows += [{"right": "call", "strike": 90, "bid": 20.0, "ask": 20.2},
             {"right": "put", "strike": 90, "bid": 0.1, "ask": 0.3}]
    forward, _ = ParityForward({**PARITY, "parity_strikes": 2}).from_chain(rows, 101.0)
    assert forward == pytest.approx(101.5, abs=1e-9)


@pytest.mark.parametrize("damage", ["missing_put", "crossed", "wide", "no_quote"])
def test_parity_forward_falls_back_to_spot(damage):
    rows = chain(101.5, [100, 102])
    if damage == "missing_put":
        rows = [r for r in rows if r["right"] == "call"]
    elif damage == "crossed":
        for r in rows:
            r["bid"], r["ask"] = r["ask"], r["bid"]
    elif damage == "wide":
        for r in rows:
            r["ask"] = r["bid"] * 10
    else:
        rows = []
    assert ParityForward(PARITY).from_chain(rows, 100.3) == (100.3, "spot")


def test_parity_forward_needs_min_pairs_and_a_positive_spot():
    rows = chain(101.5, [100])
    assert ParityForward(PARITY).from_chain(rows, 100.0)[1] == "spot"
    assert ParityForward({**PARITY, "min_pairs": 1}).from_chain(rows, 100.0)[1] == "parity"
    with pytest.raises(ValueError, match="spot"):
        ParityForward(PARITY).from_chain(rows, 0.0)


# ---- leg and book greeks --------------------------------------------------------------
def test_spot_delta_and_gamma_follow_the_chain_rule():
    spot, rate, div, vol, strike = 100.0, 0.03, 0.02, 0.3, 105.0
    expiry, day = "2024-04-01", "2024-03-01"          # 31 days
    years = 31 / 365
    forward = spot * math.exp((rate - div) * years)
    leg = {"right": "call", "strike": strike, "sign": 1, "quantity": 1, "expiry": expiry}
    quote = {"bid": 1.0, "ask": 1.2, "iv": vol}
    row, code = LegGreeks({**LEG, "rate": rate, "multiplier": 1}).of(leg, quote, forward, spot, day)
    assert code is None

    def price(s):
        return black76("call", s * math.exp((rate - div) * years), strike, vol, years, rate)
    step = 0.01
    delta = (price(spot + step) - price(spot - step)) / (2 * step)
    gamma = (price(spot + step) - 2 * price(spot) + price(spot - step)) / step ** 2
    assert row["delta_sh"] == pytest.approx(delta, rel=1e-6)
    assert row["gamma_sh"] == pytest.approx(gamma, rel=1e-4)
    ratio = forward / spot
    assert row["delta_sh"] == pytest.approx(
        black76_delta("call", forward, strike, vol, years, rate) * ratio)
    assert row["gamma_sh"] == pytest.approx(
        black76_gamma("call", forward, strike, vol, years, rate) * ratio ** 2)


def test_leg_greeks_scale_with_sign_quantity_multiplier_and_units():
    lg = LegGreeks(LEG)
    quote = {"bid": 2.0, "ask": 2.2, "iv": 0.25}
    base = {"right": "put", "strike": 100.0, "sign": 1, "quantity": 1, "expiry": "2024-03-15"}
    one, _ = lg.of(base, quote, 100.0, 100.0, "2024-03-01")
    short3, _ = lg.of({**base, "sign": -1, "quantity": 3}, quote, 100.0, 100.0, "2024-03-01")
    for key in ("delta_sh", "gamma_sh", "vega_usd_per_pt", "theta_usd_per_day"):
        assert short3[key] == pytest.approx(-3 * one[key])
    years = 14 / 365
    assert one["vega_usd_per_pt"] == pytest.approx(
        100 * (black76("put", 100.0, 100.0, 0.2501, years)
               - black76("put", 100.0, 100.0, 0.2499, years)) / 0.0002 * 0.01, rel=1e-5)
    assert one["theta_usd_per_day"] < 0 < one["gamma_sh"]
    assert one["iv"] == 0.25


def test_mid_iv_source_reimplies_the_vol():
    forward, years = 100.0, 30 / 365
    mid = black76("call", forward, 100.0, 0.22, years)
    leg = {"right": "call", "strike": 100.0, "sign": 1, "quantity": 1, "expiry": "2024-03-31"}
    quote = {"bid": mid - 0.01, "ask": mid + 0.01, "iv": 0.9}
    row, code = LegGreeks({**LEG, "iv_source": "mid", "iv_disagree_tol": 0.05}).of(leg, quote, forward, forward, "2024-03-01")
    assert code is None and row["iv"] == pytest.approx(0.22, abs=1e-6)


@pytest.mark.parametrize("quote,code", [
    (None, "no_fill_quote"),
    ({"bid": float("nan"), "ask": 1.0, "iv": 0.2}, "no_fill_quote"),
    ({"bid": 2.0, "ask": 1.0, "iv": 0.2}, "no_fill_quote"),
    ({"bid": 1.0, "ask": 1.2}, "leg_no_iv"),
    ({"bid": 1.0, "ask": 1.2, "iv": 0.0}, "leg_no_iv"),
    ({"bid": 1.0, "ask": 1.2, "iv": float("nan")}, "leg_no_iv"),
    ({"bid": 1.0, "ask": 1.2, "iv": 0.005}, "leg_no_iv"),
])
def test_leg_greeks_refusal_codes(quote, code):
    leg = {"right": "call", "strike": 100.0, "sign": 1, "quantity": 1, "expiry": "2024-03-15"}
    assert LegGreeks(LEG).of(leg, quote, 100.0, 100.0, "2024-03-01") == (None, code)


def test_leg_below_min_years_and_the_refusal_inventory():
    leg = {"right": "call", "strike": 100.0, "sign": 1, "quantity": 1, "expiry": "2024-03-01"}
    out = LegGreeks(LEG).of(leg, {"bid": 1.0, "ask": 1.2, "iv": 0.2}, 100.0, 100.0, "2024-03-01")
    assert out == (None, "leg_below_min_years")
    assert set(LegGreeks.REFUSALS) == {"no_fill_quote", "leg_no_iv", "leg_below_min_years"}


def test_leg_greeks_raise_on_malformed_input():
    lg = LegGreeks(LEG)
    quote = {"bid": 1.0, "ask": 1.2, "iv": 0.2}
    leg = {"right": "call", "strike": 100.0, "sign": 1, "quantity": 1, "expiry": "2024-03-15"}
    with pytest.raises(ValueError, match="forward"):
        lg.of(leg, quote, 0.0, 100.0, "2024-03-01")
    with pytest.raises(ValueError, match="malformed"):
        lg.of({**leg, "sign": 2}, quote, 100.0, 100.0, "2024-03-01")


def test_book_greeks_sum_and_status():
    a = {"delta_sh": 10.0, "gamma_sh": 1.0, "vega_usd_per_pt": 5.0, "theta_usd_per_day": -2.0,
         "delta_f_sh": 11.0}
    b = {"delta_sh": -4.0, "gamma_sh": 0.5, "vega_usd_per_pt": -1.0, "theta_usd_per_day": 1.0,
         "delta_f_sh": -4.5}
    book = BookGreeks.sum([a, b], 101.0, "parity")
    assert (book.delta_sh, book.gamma_sh, book.vega_usd_per_pt, book.theta_usd_per_day) == \
        (6.0, 1.5, 4.0, -1.0)
    assert (book.forward, book.forward_source, book.status) == (101.0, "parity", "ok")
    assert book.delta_f_sh == 6.5
    assert BookGreeks.sum([a], 100.0, "spot").status == "forward_spot"
    with pytest.raises(Exception):
        book.delta_sh = 0.0
    with pytest.raises(ValueError):
        BookGreeks.sum([], 100.0, "spot")
    with pytest.raises(ValueError, match="forward_source"):
        BookGreeks.sum([a], 100.0, "vendor")


# ---- stock equivalent -----------------------------------------------------------------
def test_stock_equivalent_hand_example_of_a_five_to_one_call_book():
    # bought 5 x 450 calls and 1 x 455 call, multiplier 100; delta 0.5 and 0.25 per share
    entry_delta = 100 * (5 * 0.5 + 1 * 0.25)               # 275 shares
    s_fill, s_settle, book_pnl = 450.0, 458.0, 1500.0
    stock, residual = StockEquivalent().split(book_pnl, entry_delta, s_fill, s_settle)
    assert stock == 275 * 8 == 2200.0
    assert residual == -700.0
    assert stock + residual == book_pnl


def test_stock_equivalent_sums_exactly_and_takes_dividends():
    se = StockEquivalent()
    for pnl, delta, s0, s1 in [(125.5, -37.25, 100.5, 96.75), (-0.5, 12.0, 8.0, 9.5)]:
        stock, residual = se.split(pnl, delta, s0, s1)
        assert stock + residual == pnl
    stock, residual = se.split(10.0, 200.0, 50.0, 49.0, dividends_per_share=0.5)
    assert stock == pytest.approx(200 * (-1.0 + 0.5)) and residual == pytest.approx(10.0 - stock)


def test_stock_equivalent_basis_and_refusals():
    se = StockEquivalent()
    assert se.basis_usd(-200.0, 100.0, 101.0) == 200.0
    with pytest.raises(ValueError, match="s_fill"):
        se.split(1.0, 1.0, 0.0, 1.0)
    with pytest.raises(ValueError, match="book_pnl"):
        se.split(float("nan"), 1.0, 1.0, 1.0)
    with pytest.raises(ConfigError):
        StockEquivalent({"x": 1})


# ---- cell attribution -----------------------------------------------------------------
def rows_for_cells():
    def r(sym, rank, pnl, stock):
        return {"symbol": sym, "rank": rank, "pnl": pnl, "static_delta_pnl": stock,
                "beyond_static_delta": pnl - stock}
    return [r("A", 1, 100.0, 40.0), r("A", 1, -30.0, -10.0), r("A", 2, 50.0, 80.0),
            r("B", 1, 60.0, 10.0), r("B", 1, -60.0, -20.0)]


def test_cell_sums_add_to_the_total_and_shares_are_exact():
    ca = CellAttribution({"cell_by": ["symbol", "rank"], "min_total_usd": 1.0})
    cells = ca.summarize(rows_for_cells())
    assert [(c["symbol"], c["rank"]) for c in cells] == [("A", 1), ("A", 2), ("B", 1)]
    rows = rows_for_cells()
    assert sum(c["pnl_usd"] for c in cells) == pytest.approx(sum(r["pnl"] for r in rows))
    assert sum(c["static_delta_usd"] + c["beyond_static_usd"] for c in cells) == \
        pytest.approx(sum(r["pnl"] for r in rows))
    a1 = cells[0]
    assert (a1["n"], a1["pnl_usd"], a1["static_delta_usd"], a1["beyond_static_usd"]) == (2, 70.0, 30.0, 40.0)
    assert a1["static_share_net"] == pytest.approx(30 / 70)
    assert a1["gross_pnl_usd"] == 130.0
    assert a1["static_share_gross"] == pytest.approx(30 / 130)
    assert a1["beyond_static_share_gross"] == pytest.approx(40 / 130)


def test_net_share_is_null_below_min_total_but_gross_share_is_kept():
    ca = CellAttribution({"cell_by": ["symbol", "rank"], "min_total_usd": 1.0})
    b1 = ca.summarize(rows_for_cells())[2]                 # total exactly 0
    assert b1["pnl_usd"] == 0.0
    assert b1["static_share_net"] is None and b1["beyond_static_share_net"] is None
    assert b1["gross_pnl_usd"] == 120.0 and b1["static_share_gross"] == pytest.approx(-10 / 120)
    high = CellAttribution({"cell_by": ["symbol", "rank"], "min_total_usd": 100.0})
    assert high.summarize(rows_for_cells())[0]["static_share_net"] is None
    assert CellAttribution({"cell_by": ["symbol"], "min_total_usd": 0.0}).summarize(
        [{"symbol": "Z", "pnl": 0.0, "static_delta_pnl": 0.0, "beyond_static_delta": 0.0}])[0][
        "static_share_gross"] is None


def test_cell_attribution_refusals():
    ca = CellAttribution({"cell_by": ["symbol"], "min_total_usd": 1.0})
    with pytest.raises(ValueError, match="^cell_field_missing"):
        ca.summarize([{"pnl": 1.0, "static_delta_pnl": 0.0, "beyond_static_delta": 1.0}])
    with pytest.raises(ValueError, match="^pnl_not_finite"):
        ca.summarize([{"symbol": "A", "pnl": float("nan"), "static_delta_pnl": 0.0, "beyond_static_delta": 0.0}])
    assert set(CellAttribution.REFUSALS) == {"cell_field_missing", "pnl_not_finite"}


# ---- exposure snapshot ----------------------------------------------------------------
def book(symbol, dte, delta, spot=100.0, gamma=0.0, vega=0.0, status="ok"):
    return {"symbol": symbol, "dte": dte, "spot": spot, "delta_sh": delta, "gamma_sh": gamma,
            "vega_usd_per_pt": vega, "status": status}


def test_buckets_are_half_open_at_the_edges():
    es = ExposureSnapshot({"dte_edges": [0, 7, 14], "top_k": 1})
    out = es.of({"A": 1.0}, [book("A", 0, 1.0), book("A", 6.9, 1.0), book("A", 7, 10.0),
                             book("A", 13, 10.0), book("A", 14, 99.0), book("A", -1, 99.0)])
    first, second = out["buckets"]
    assert (first["dte_min"], first["dte_max"], first["n_books"]) == (0, 7, 2)
    assert first["beta_delta_usd"] == 200.0
    assert (second["n_books"], second["beta_delta_usd"]) == (2, 2000.0)
    assert out["excluded"] == {"dte_outside_edges": 2}
    assert out["total"]["n_books"] == 4


def test_beta_weighted_delta_sign_scale_and_gamma_vega():
    es = ExposureSnapshot({"dte_edges": [0, 30], "top_k": 2})
    out = es.of({"A": 1.5, "B": 0.5},
                [book("A", 10, 200.0, spot=50.0, gamma=2.0, vega=30.0),
                 book("B", 10, -400.0, spot=20.0, gamma=1.0, vega=-10.0)])
    total = out["total"]
    assert total["beta_delta_usd"] == pytest.approx(200 * 50 * 1.5 - 400 * 20 * 0.5)  # 11000
    assert total["vega_usd_per_pt"] == 20.0
    assert total["gamma_usd_per_1pct"] == pytest.approx((2 * 50 ** 2 + 1 * 20 ** 2) * 0.01)


def test_top_k_share_and_excluded_statuses():
    es = ExposureSnapshot({"dte_edges": [0, 30], "top_k": 1})
    out = es.of({"A": 1.0, "B": 1.0, "C": 1.0},
                [book("A", 5, 30.0), book("A", 6, -10.0), book("B", 5, -10.0),
                 book("C", 5, 10.0), book("C", 5, 1e6, status="open_leg_unmarked"),
                 book("B", 5, 1.0, status="forward_spot")])
    # name deltas: A 2000, B -900, C 1000 -> top-1 share 2000 / 3900
    assert out["top_k_share"] == pytest.approx(2000 / 3900)
    assert out["excluded"] == {"open_leg_unmarked": 1}
    assert es.of({"A": 1.0}, [book("A", 5, 0.0)])["top_k_share"] is None


def test_exposure_refuses_a_name_without_a_beta():
    with pytest.raises(ValueError, match="^beta_missing"):
        ExposureSnapshot({"dte_edges": [0, 30], "top_k": 1}).of({}, [book("A", 5, 1.0)])
    assert set(ExposureSnapshot.REFUSALS) == {"dte_outside_edges", "beta_missing"}


# ---- fit_betas ------------------------------------------------------------------------
def test_fit_betas_hand_example_and_fallbacks():
    returns = {"A": [0.01, -0.01, 0.02], "B": [0.02, -0.02, 0.04]}
    betas = fit_betas(returns, ["A", "B"], 3, 2)
    assert betas["A"] == pytest.approx(2 / 3) and betas["B"] == pytest.approx(4 / 3)
    assert fit_betas(returns, ["A", "B"], 3, 4) == {"A": 1.0, "B": 1.0}
    assert fit_betas(returns, [], 3, 2) == {}
    # a flat basket has no variance
    assert fit_betas({"A": [0.01] * 3, "B": [0.01] * 3}, ["A", "B"], 3, 2) == {"A": 1.0, "B": 1.0}


# ---- params ---------------------------------------------------------------------------
CASES = [
    (ParityForward, PARITY), (LegGreeks, LEG), (CellAttribution,
     {"cell_by": ["symbol"], "min_total_usd": 1.0}),
    (ExposureSnapshot, {"dte_edges": [0, 7], "top_k": 1}),
    (StaticDeltaHedge, HEDGE),
]


@pytest.mark.parametrize("cls,good", CASES)
def test_params_reject_unknown_keys_and_missing_keys(cls, good):
    cls(good)
    assert any("unknown" in p for p in cls.validate_params({**good, "typo": 1}))
    with pytest.raises(ConfigError, match="typo"):
        cls({**good, "typo": 1})
    required = [k for k in cls._PARAMS if k not in ("iv_source", "iv_disagree_tol")]
    for key in required:
        trimmed = {k: v for k, v in good.items() if k != key}
        assert any("missing" in p for p in cls.validate_params(trimmed)), key
    assert cls.validate_params([]) != []


@pytest.mark.parametrize("cls,good,bad", [
    (ParityForward, PARITY, {"parity_strikes": 0}),
    (ParityForward, PARITY, {"min_pairs": True}),
    (ParityForward, PARITY, {"max_pair_spread_frac": -1.0}),
    (ParityForward, PARITY, {"max_pair_spread_frac": "x"}),
    (LegGreeks, LEG, {"rate": float("nan")}),
    (LegGreeks, LEG, {"rate": True}),
    (LegGreeks, LEG, {"iv_source": "greeks"}),
    (LegGreeks, LEG, {"multiplier": 0}),
    (LegGreeks, LEG, {"min_years": -1}),
    (LegGreeks, LEG, {"vol_floor": -0.1}),
    (CellAttribution, CASES[2][1], {"cell_by": []}),
    (CellAttribution, CASES[2][1], {"cell_by": ["a", "a"]}),
    (CellAttribution, CASES[2][1], {"cell_by": "symbol"}),
    (CellAttribution, CASES[2][1], {"min_total_usd": -1.0}),
    (ExposureSnapshot, CASES[3][1], {"dte_edges": [5]}),
    (ExposureSnapshot, CASES[3][1], {"dte_edges": [0, 7, 7]}),
    (ExposureSnapshot, CASES[3][1], {"dte_edges": [0, "7"]}),
    (ExposureSnapshot, CASES[3][1], {"top_k": 0}),
    (ExposureSnapshot, CASES[3][1], {"top_k": 1.5}),
    (StaticDeltaHedge, HEDGE, {"hedge_fee_per_share": -0.001}),
    (StaticDeltaHedge, HEDGE, {"hedge_fee_per_share": float("nan")}),
    (StaticDeltaHedge, HEDGE, {"hedge_half_spread_bps": -1.0}),
    (StaticDeltaHedge, HEDGE, {"hedge_half_spread_bps": float("inf")}),
    (StaticDeltaHedge, HEDGE, {"hedge_half_spread_bps": True}),
    (StaticDeltaHedge, HEDGE, {"hedge_borrow_rate": -0.01}),
    (StaticDeltaHedge, HEDGE, {"hedge_borrow_rate": 1.0}),
    (StaticDeltaHedge, HEDGE, {"hedge_borrow_rate": "x"}),
])
def test_params_reject_bad_types_and_values(cls, good, bad):
    assert cls.validate_params({**good, **bad}) != []
    with pytest.raises(ConfigError):
        cls({**good, **bad})


def test_leg_greeks_iv_source_defaults_to_vendor():
    assert LegGreeks.validate_params(LEG) == []
    assert ox.DEFAULT_IV_SOURCE == "vendor"


# ---- BookGreeksReport node (ADR-0310 stage 2) -----------------------------------------
from dskit.pipeline.option_exposure import BookGreeksReport  # noqa: E402

REPORT = {
    "rate": 0.0, "iv_source": "vendor", "multiplier": 100, "min_years": 0.001, "vol_floor": 0.01,
    "parity_strikes": 3, "min_pairs": 2, "max_pair_spread_frac": 0.5,
    "cell_by": ["symbol", "tag"], "min_total_usd": 1.0,
    "dte_edges": [0, 7, 14, 28], "top_k": 1, "exposure_days": "both",
    "beta_lookback": 3, "beta_min_pairs": 2, **HEDGE,
    "hedge_cost_source": "placeholder", "min_books": 5, "htb_lookback": 20,
    "htb_vol_threshold": 2.5, "htb_jump_threshold": 0.75,
}
FILL, DECISION, EXPIRY = "2021-03-02", "2021-03-01", "2021-03-19"
FORWARD, SPOT, VOL = 101.5, 100.0, 0.2


def priced_chain(forward=FORWARD, day=FILL, expiry=EXPIRY, strikes=(96, 98, 100, 102, 104, 106),
                 iv=VOL):
    years = (date_of(expiry) - date_of(day)).days / 365
    rows = chain(forward, strikes, vol=iv, years=years)
    return [{**r, "iv": iv} for r in rows]


def date_of(text):
    from datetime import date
    return date.fromisoformat(text)


class MemQuotes:
    def __init__(self, table):
        self.table = table

    def chain(self, symbol, day, expiry):
        return self.table.get((symbol, day, expiry), [])


class Mem(BookGreeksReport):
    """In-memory subclass: raw books are already normalized, raw quotes a dict."""

    def _load_books(self, raw):
        return raw

    def _load_quotes(self, raw):
        return MemQuotes(raw)

    def _cell_key(self, book, quotes):
        return {"symbol": book["symbol"], "tag": book["tags"]["tag"]}


def make_book(key="b1", symbol="AAA", pnl=150.0, legs=None, **over):
    legs = legs or [{"right": "call", "strike": 100.0, "sign": 1, "quantity": 2}]
    return {"key": key, "symbol": symbol, "decision_date": DECISION, "fill_date": FILL,
            "expiry": EXPIRY, "pnl": pnl, "factor": 1.0, "end_factor": 1.0, "legs": legs,
            "tags": {"tag": "t"}, **over}


def make_bars(extra=None):
    history = {f"2021-02-{d:02d}": 100.0 for d in range(22, 27)}
    bars = {"AAA": {**history, DECISION: 100.0, FILL: 100.0, EXPIRY: 104.0}}
    bars.update(extra or {})
    return bars


def with_bar(day, close=101.0):
    bars = make_bars()
    bars["AAA"][day] = close
    return bars


def run_report(books, quotes=None, bars=None, **params):
    quotes = {("AAA", FILL, EXPIRY): priced_chain()} if quotes is None else quotes
    node = Mem("report", {**REPORT, **params})
    return node.run(None, {"books": books, "quotes": quotes,
                           "bars": make_bars() if bars is None else bars})


def test_report_cannot_be_built_without_its_three_hooks():
    with pytest.raises(TypeError):
        BookGreeksReport("report", REPORT)


def test_report_declares_its_outputs_and_role():
    assert BookGreeksReport.outputs == ("books", "cells", "exposure", "summary")
    assert BookGreeksReport.kind == "book_greeks_report"


def test_report_splits_a_book_end_to_end():
    out = run_report([make_book()])
    assert sorted(out) == ["books", "cells", "exposure", "summary"]
    (row,) = out["books"]
    years = 17 / 365
    delta = 2 * 100 * black76_delta("call", FORWARD, 100.0, VOL, years) * (FORWARD / SPOT)
    assert row["status"] == "ok" and row["forward_source"] == "parity"
    assert row["forward"] == pytest.approx(FORWARD, abs=1e-9)
    assert row["delta_sh"] == pytest.approx(delta, rel=1e-12)
    assert row["s_fill"] == 100.0 and row["s_settle"] == 104.0
    assert row["static_delta_pnl"] == pytest.approx(delta * 4.0, rel=1e-12)
    assert row["static_delta_pnl"] + row["beyond_static_delta"] == pytest.approx(150.0, abs=1e-12)
    assert row["basis_usd"] == pytest.approx(delta * (SPOT - FORWARD), rel=1e-12)
    assert row["gamma_sh"] > 0 and row["vega_usd_per_pt"] > 0 and row["theta_usd_per_day"] < 0
    assert (row["symbol"], row["tag"], row["id"]) == ("AAA", "t", "b1")


def test_report_cells_sum_to_the_attributed_total_and_summary_counts():
    books = [make_book("b1", pnl=150.0), make_book("b2", pnl=-40.0)]
    out = run_report(books)
    (cell,) = out["cells"]
    assert cell["n"] == 2 and cell["pnl_usd"] == pytest.approx(110.0)
    assert cell["static_delta_usd"] + cell["beyond_static_usd"] == pytest.approx(110.0)
    summary = out["summary"]
    assert summary["books"] == 2 and summary["attributed"] == 2
    assert summary["attributed_pnl_usd"] == pytest.approx(110.0)
    assert summary["unattributed_pnl_usd"] == 0.0 and summary["refusals"] == {}


@pytest.mark.parametrize("code,change", [
    ("leg_no_iv", "iv"), ("no_fill_quote", "strike"), ("basis_changed", "factor"),
    ("no_settlement_bar", "bar"), ("leg_below_min_years", "late"),
])
def test_report_lists_a_refused_book_under_its_code_and_keeps_its_pnl(code, change):
    """The bad book is a second name, so the good book proves the damage stays local."""
    quotes = {("AAA", FILL, EXPIRY): priced_chain(), ("BBB", FILL, EXPIRY): priced_chain()}
    bars = make_bars({"BBB": {FILL: 100.0, EXPIRY: 104.0}})
    book = make_book("bad", symbol="BBB", pnl=77.0)
    if change == "iv":
        quotes["BBB", FILL, EXPIRY] = [{**r, "iv": None} for r in priced_chain()]
    elif change == "strike":
        book["legs"][0]["strike"] = 150.0
    elif change == "factor":
        book["end_factor"] = 2.0
    elif change == "bar":
        del bars["BBB"][EXPIRY]
    else:
        book["fill_date"] = EXPIRY            # zero days left: below min_years
        bars["BBB"][EXPIRY] = 104.0
        quotes["BBB", EXPIRY, EXPIRY] = priced_chain(day="2021-03-18")
    out = run_report([make_book("good"), book], quotes=quotes, bars=bars)
    by_id = {r["id"]: r for r in out["books"]}
    assert by_id["bad"]["status"] == code and by_id["good"]["status"] == "ok"
    assert by_id["bad"]["static_delta_pnl"] is None and by_id["bad"]["pnl"] == 77.0
    summary = out["summary"]
    assert summary["refusals"] == {code: 1}
    assert summary["unattributed_pnl_usd"] == pytest.approx(77.0)
    assert summary["attributed"] == 1 and summary["books"] == 2
    assert [c["symbol"] for c in out["cells"]] == ["AAA"]


def test_report_flags_forward_spot_but_still_attributes():
    calls = [r for r in priced_chain() if r["right"] == "call"]
    out = run_report([make_book()], quotes={("AAA", FILL, EXPIRY): calls})
    (row,) = out["books"]
    assert row["status"] == "forward_spot" and row["forward"] == SPOT
    assert row["static_delta_pnl"] is not None
    assert out["summary"]["forward_spot"] == 1 and out["summary"]["attributed"] == 1


def test_report_exposure_marks_open_books_on_each_decision_and_fill_day():
    mid = "2021-03-09"
    quotes = {("AAA", FILL, EXPIRY): priced_chain(),
              ("AAA", mid, EXPIRY): priced_chain(day=mid)}
    book = make_book(decision_date=DECISION)
    book["decision_date"] = mid
    out = run_report([make_book("b1"), book], quotes=quotes, exposure_days="both",
                     bars=with_bar(mid))
    days = sorted({r["date"] for r in out["exposure"]})
    assert days == [DECISION, FILL, mid]
    on_fill = [r for r in out["exposure"] if r["date"] == FILL and r["bucket"] == "total"][0]
    assert on_fill["n_books"] == 2                      # both fill on FILL, so both are open
    on_decision = [r for r in out["exposure"] if r["date"] == DECISION and r["bucket"] == "total"][0]
    assert on_decision["n_books"] == 0                  # nothing filled yet (fill_date > day)
    on_mid = [r for r in out["exposure"] if r["date"] == mid and r["bucket"] == "total"][0]
    assert on_mid["n_books"] == 2                       # still open mid-life, re-marked on 03-09
    assert out["summary"]["exposure_days"] == 3


def test_report_exposure_days_decision_only_and_fill_only():
    quotes = {("AAA", FILL, EXPIRY): priced_chain()}
    only_fill = run_report([make_book()], quotes=quotes, exposure_days="fill")
    assert {r["date"] for r in only_fill["exposure"]} == {FILL}
    only_decision = run_report([make_book()], quotes=quotes, exposure_days="decision")
    assert {r["date"] for r in only_decision["exposure"]} == {DECISION}


def test_report_exposure_buckets_dollar_delta_with_beta_one_when_history_is_short():
    out = run_report([make_book()], exposure_days="fill",
                     bars={"AAA": {FILL: 100.0, EXPIRY: 104.0}})
    (row,) = [r for r in out["exposure"] if r["bucket"] == "[14,28)"]
    (book,) = out["books"]
    assert row["dte_min"] == 14 and row["dte_max"] == 28 and row["n_books"] == 1
    assert row["beta_delta_usd"] == pytest.approx(book["delta_sh"] * SPOT * 1.0, rel=1e-12)
    assert row["gamma_usd_per_1pct"] == pytest.approx(book["gamma_sh"] * SPOT ** 2 * 0.01)
    assert row["vega_usd_per_pt"] == pytest.approx(book["vega_usd_per_pt"])
    total = [r for r in out["exposure"] if r["bucket"] == "total"][0]
    assert total["top_k_share"] == 1.0
    assert out["summary"]["beta_all_one_days"] == 1


def test_report_exposure_uses_betas_fitted_on_the_names_own_history():
    # B moves 3x A each session, so the equal-weight basket is 2x A: beta_A = 0.5.
    sessions = ["2021-02-22", "2021-02-23", "2021-02-24", "2021-02-25", "2021-02-26", DECISION]
    a, b = [100.0], [50.0]
    for r in (0.01, -0.02, 0.015, 0.005, -0.01):
        a.append(a[-1] * math.exp(r))
        b.append(b[-1] * math.exp(3 * r))
    bars = {"AAA": dict(zip(sessions, a), **{FILL: 100.0, EXPIRY: 104.0}),
            "BBB": dict(zip(sessions, b), **{FILL: 50.0, EXPIRY: 52.0})}
    out = run_report([make_book()], bars=bars, beta_lookback=4, exposure_days="fill")
    (row,) = [r for r in out["exposure"] if r["bucket"] == "[14,28)"]
    (book,) = out["books"]
    assert row["beta_delta_usd"] == pytest.approx(book["delta_sh"] * SPOT * 0.5, rel=1e-9)
    assert out["summary"]["beta_all_one_days"] == 0


def test_report_marks_open_leg_unmarked_per_day_and_excludes_only_that_day():
    quotes = {("AAA", FILL, EXPIRY): priced_chain()}   # nothing quoted on the decision day
    out = run_report([make_book()], quotes=quotes, exposure_days="both")
    by_date = {(r["date"], r["bucket"]): r for r in out["exposure"]}
    assert by_date[(FILL, "total")]["n_books"] == 1
    # the decision day precedes the fill, so the book is not open; a mid-life gap is the case:
    mid = "2021-03-09"
    book = make_book(decision_date=mid)
    out = run_report([book], quotes=quotes, exposure_days="decision", bars=with_bar(mid))
    total = [r for r in out["exposure"] if r["date"] == mid and r["bucket"] == "total"][0]
    assert total["n_books"] == 0 and total["excluded"] == {"open_leg_unmarked": 1}
    assert out["summary"]["open_leg_unmarked"] == 1
    assert out["books"][0]["status"] == "ok"            # the entry attribution is unaffected


def test_report_basis_changed_book_is_left_out_of_exposure_and_counted():
    book = make_book(end_factor=2.0)
    out = run_report([book], exposure_days="fill")
    total = [r for r in out["exposure"] if r["bucket"] == "total"][0]
    assert total["n_books"] == 0 and total["excluded"] == {"basis_changed": 1}


def test_report_cell_by_field_the_hook_does_not_give_refuses():
    with pytest.raises(ValueError, match="cell_field_missing"):
        run_report([make_book()], cell_by=["symbol", "rank"])


def test_report_hooks_receive_the_raw_inputs_untouched():
    seen = {}

    class Spy(Mem):
        def _load_books(self, raw):
            seen["books"] = raw
            return raw

        def _load_quotes(self, raw):
            seen["quotes"] = raw
            return MemQuotes(raw)

    books, quotes = [make_book()], {("AAA", FILL, EXPIRY): priced_chain()}
    Spy("report", REPORT).run(None, {"books": books, "quotes": quotes, "bars": make_bars()})
    assert seen["books"] is books and seen["quotes"] is quotes


@pytest.mark.parametrize("strike", [None, math.nan, math.inf, -5.0, 0.0])
def test_report_skips_chain_rows_whose_strike_is_not_positive_and_finite(strike):
    junk = {"right": "call", "strike": strike, "bid": 1.0, "ask": 1.2, "iv": VOL}
    quotes = {("AAA", FILL, EXPIRY): [junk, *priced_chain()]}
    out = run_report([make_book()], quotes=quotes)
    assert out["books"][0]["status"] == "ok"
    assert out["books"][0]["delta_sh"] == run_report([make_book()])["books"][0]["delta_sh"]


class _Order(Mem):
    """Records every (symbol, day) the chain provider is asked for, in order."""

    asked = []

    def _load_quotes(self, raw):
        outer = super()._load_quotes(raw)

        class Spy:
            def chain(_, symbol, day, expiry):
                _Order.asked.append((symbol, day))
                return outer.chain(symbol, day, expiry)
        return Spy()


def test_report_marks_one_days_open_books_symbol_by_symbol():
    mid = "2021-03-09"
    books = [make_book("a1", "AAA", decision_date=mid), make_book("b1", "BBB", decision_date=mid),
             make_book("a2", "AAA", decision_date=mid)]
    bars = {**with_bar(mid), "BBB": dict(with_bar(mid)["AAA"])}
    quotes = {(s, d, EXPIRY): priced_chain(day=d) for s in ("AAA", "BBB") for d in (FILL, mid)}
    _Order.asked = []
    out = _Order("report", {**REPORT, "exposure_days": "decision"}).run(
        None, {"books": books, "quotes": quotes, "bars": bars})
    on_day = [s for s, d in _Order.asked if d == mid]
    # two passes read the decision session (the forward hedge's sizing, then the exposure
    # snapshot), each AAA, AAA, BBB: the cache sees one run per name per pass
    assert on_day == ["AAA", "AAA", "BBB"] * 2
    total = [r for r in out["exposure"] if r["date"] == mid and r["bucket"] == "total"][0]
    assert total["n_books"] == 3


def test_report_builds_the_bar_calendar_once_per_run(monkeypatch):
    calls = []
    real = BookGreeksReport._calendar
    monkeypatch.setattr(BookGreeksReport, "_calendar",
                        staticmethod(lambda bars: calls.append(1) or real(bars)))
    run_report([make_book()], exposure_days="both")
    assert len(calls) == 1


def test_report_run_is_deterministic():
    first = run_report([make_book("b1"), make_book("b2", pnl=-3.0)])
    again = run_report([make_book("b1"), make_book("b2", pnl=-3.0)])
    assert first == again


@pytest.mark.parametrize("change,fragment", [
    ({"surprise": 1}, "unknown param"),
    ({"exposure_days": "weekly"}, "exposure_days"),
    ({"beta_lookback": 1}, "beta_lookback"),
    ({"beta_min_pairs": 0}, "beta_min_pairs"),
    ({"rate": "zero"}, "rate"),
    ({"dte_edges": [5, 1]}, "dte_edges"),
    ({"cell_by": []}, "cell_by"),
    ({"hedge_borrow_rate": 1.0}, "hedge_borrow_rate"),
    ({"hedge_fee_per_share": -1.0}, "hedge_fee_per_share"),
])
def test_report_params_are_default_deny(change, fragment):
    problems = Mem.validate_params({**REPORT, **change})
    assert any(fragment in p for p in problems)
    with pytest.raises(ConfigError):
        Mem("report", {**REPORT, **change})


def test_report_params_require_every_knob():
    for name in REPORT:
        short = {k: v for k, v in REPORT.items() if k != name}
        problems = Mem.validate_params(short)
        if name == "iv_source":
            assert problems == []            # the one defaulted knob
        else:
            assert any(name in p for p in problems), name


def test_report_is_exported_by_its_module_and_valid_params_pass():
    assert "BookGreeksReport" in ox.__all__
    assert Mem.validate_params(REPORT) == []


# ---- fix round 1: review findings -----------------------------------------------------
MID = {**LEG, "iv_source": "mid", "iv_disagree_tol": 0.05}


def test_theta_per_day_is_the_per_year_theta_over_365_by_hand():
    """ATM call, F=K=100, rate 0: theta/yr = -F pdf(d1) vol / (2 sqrt(T)), d1 = vol sqrt(T) / 2."""
    years, vol = 30 / 365, 0.2
    d1 = vol * math.sqrt(years) / 2
    per_year = -100.0 * math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi) * vol / (2 * math.sqrt(years))
    leg = {"right": "call", "strike": 100.0, "sign": -1, "quantity": 3, "expiry": "2024-03-31"}
    row, _ = LegGreeks(LEG).of(leg, {"bid": 2.0, "ask": 2.2, "iv": vol}, 100.0, 100.0, "2024-03-01")
    assert row["theta_usd_per_day"] == pytest.approx(-3 * 100 * per_year / 365, rel=1e-9)


def test_mid_iv_needs_a_disagreement_tolerance_and_vendor_does_not():
    assert any("iv_disagree_tol" in p for p in LegGreeks.validate_params({**LEG, "iv_source": "mid"}))
    assert LegGreeks.validate_params(MID) == []
    assert LegGreeks.validate_params(LEG) == []
    assert LegGreeks.validate_params({**MID, "iv_disagree_tol": 0}) != []


def test_leg_reports_the_vendor_iv_and_whether_it_disagrees_with_the_mid_iv():
    forward, years = 100.0, 30 / 365
    mid = black76("call", forward, 100.0, 0.22, years)
    leg = {"right": "call", "strike": 100.0, "sign": 1, "quantity": 1, "expiry": "2024-03-31"}
    lg = LegGreeks(MID)
    near, _ = lg.of(leg, {"bid": mid - 0.01, "ask": mid + 0.01, "iv": 0.24}, forward, forward, "2024-03-01")
    far, _ = lg.of(leg, {"bid": mid - 0.01, "ask": mid + 0.01, "iv": 0.30}, forward, forward, "2024-03-01")
    assert near["vendor_iv"] == 0.24 and not near["iv_disagree"]
    assert far["vendor_iv"] == 0.30 and far["iv_disagree"] and far["iv"] == pytest.approx(0.22, abs=1e-6)


def test_report_counts_mid_vs_vendor_iv_disagreements():
    quotes = {("AAA", FILL, EXPIRY): priced_chain(iv=0.5)}      # mids imply 0.5 (consistent)
    quotes["AAA", FILL, EXPIRY] = [{**r, "iv": 0.2} for r in quotes["AAA", FILL, EXPIRY]]
    out = run_report([make_book()], quotes=quotes, iv_source="mid", iv_disagree_tol=0.05)
    assert out["books"][0]["iv_disagree_legs"] == 1 and out["summary"]["iv_disagree_legs"] == 1
    agree = run_report([make_book()], iv_source="mid", iv_disagree_tol=0.05)
    assert agree["summary"]["iv_disagree_legs"] == 0
    assert run_report([make_book()])["summary"]["iv_disagree_legs"] == 0


def _betas_node():
    return Mem("report", REPORT)


def _closes_from(returns, start):
    out = [start]
    for r in returns:
        out.append(out[-1] * math.exp(r))
    return out


def _hand_beta(ra, rb):
    """Beta of A on the two-name equal-weight basket, population moments, by hand."""
    n = len(ra)
    market = [(a + b) / 2 for a, b in zip(ra, rb)]
    ma, mm = sum(ra) / n, sum(market) / n
    cov = sum((a - ma) * (m - mm) for a, m in zip(ra, market)) / n
    return cov / (sum((m - mm) ** 2 for m in market) / n)


def test_betas_use_exactly_lookback_returns_strictly_before_the_day():
    days = [f"2021-02-{d:02d}" for d in range(15, 27)]            # 12 sessions
    day = "2021-02-27"
    ra = [0.05, -0.04, 0.01, -0.02, 0.015, 0.03, -0.01, 0.02, -0.03, 0.01, 0.005]
    rb = [-0.02, 0.06, 0.02, 0.01, -0.03, 0.02, 0.04, -0.02, 0.01, -0.05, 0.03]
    bars = {"AAA": dict(zip(days, _closes_from(ra, 100.0))),
            "BBB": dict(zip(days, _closes_from(rb, 50.0)))}
    bars["AAA"][day], bars["BBB"][day] = 500.0, 5.0               # a bar ON the day is never used
    betas, _, _ = _betas_node()._betas(bars, day)                 # lookback 3: the last 3 returns
    assert betas["AAA"] == pytest.approx(_hand_beta(ra[-3:], rb[-3:]), rel=1e-9)
    assert betas["BBB"] == pytest.approx(_hand_beta(rb[-3:], ra[-3:]), rel=1e-9)


def test_betas_skip_names_with_gaps_and_nonpositive_closes_and_count_them():
    days = [f"2021-02-{d:02d}" for d in range(15, 27)]
    day = "2021-02-27"
    ra = [0.05, -0.04, 0.01, -0.02, 0.015, 0.03, -0.01, 0.02, -0.03, 0.01, 0.005]
    rb = [-0.02, 0.06, 0.02, 0.01, -0.03, 0.02, 0.04, -0.02, 0.01, -0.05, 0.03]
    late = {d: 20.0 + i for i, d in enumerate(days[10:])}          # starts inside the window
    bars = {"AAA": dict(zip(days, _closes_from(ra, 100.0))),
            "BBB": dict(zip(days, _closes_from(rb, 50.0))), "LATE": late,
            "ZERO": {**dict(zip(days, _closes_from(rb, 40.0))), days[-2]: 0.0}}
    betas, all_one, dropped = _betas_node()._betas(bars, day)
    assert sorted(dropped) == ["LATE", "ZERO"] and betas["LATE"] == betas["ZERO"] == 1.0
    assert betas["AAA"] == pytest.approx(_hand_beta(ra[-3:], rb[-3:]), rel=1e-9) and not all_one


def test_a_nonpositive_close_does_not_crash_the_report_and_is_counted():
    days = [f"2021-02-{d:02d}" for d in range(22, 27)]
    bars = make_bars({"BBB": {**dict.fromkeys(days, 10.0), DECISION: 0.0, FILL: 10.0}})
    out = run_report([make_book()], bars=bars, exposure_days="fill")
    assert out["summary"]["beta_dropped_name_days"] == 1


def test_a_missing_spot_is_no_spot_not_no_fill_quote():
    bars = make_bars()
    bars["AAA"][FILL] = 0.0
    out = run_report([make_book()], bars=bars)
    assert out["books"][0]["status"] == "no_spot" and out["summary"]["refusals"] == {"no_spot": 1}


def test_duplicate_quote_rows_are_one_refusal_for_leg_lookup_and_parity_forward():
    rows = priced_chain()
    dup = rows + [{**rows[0], "bid": rows[0]["bid"] + 0.01}]
    with pytest.raises(ValueError, match="duplicate_quote"):
        ParityForward(PARITY).from_chain(dup, SPOT)
    assert ParityForward.duplicates(rows) == [] and len(ParityForward.duplicates(dup)) == 1
    out = run_report([make_book()], quotes={("AAA", FILL, EXPIRY): dup})
    assert out["books"][0]["status"] == "duplicate_quote"
    assert out["summary"]["refusals"] == {"duplicate_quote": 1}
    assert out["summary"]["unattributed_pnl_usd"] == pytest.approx(150.0)


def test_declared_columns_match_the_rows_the_report_writes():
    out = run_report([make_book()], exposure_days="fill")
    assert set(BookGreeksReport.BOOK_COLUMNS) <= set(out["books"][0])
    assert list(out["exposure"][0]) == list(BookGreeksReport.EXPOSURE_COLUMNS)
    n = len(BookGreeksReport.CELL_COLUMNS)
    assert list(out["cells"][0])[-n:] == list(BookGreeksReport.CELL_COLUMNS)


# ---- static delta hedge (ADR-0310 Amendment 1, A2) ------------------------------------
# docs/explanations/mio-glossary.md BLK book: 5 calls at 450 bought at 1.65, 1 call at 455 bought
# at 1.10, both bought, book_pnl -938.90; illustrative deltas 0.50 and 0.40 -> 290 shares.
BLK_DELTA = 100 * (5 * 0.50 + 1 * 0.40)
BLK = dict(book_pnl=-938.90, entry_delta_sh=BLK_DELTA, s_fill=452.0, s_settle=448.0, hedge_days=10)
BLK_COSTS = 290 * (2 * 0.005 + 900 * 0.0005)                 # 290 x 0.46
BLK_BORROW = 290 * 452 * 0.005 * 10 / 365


def test_hedge_blk_book_by_hand_without_a_dividend():
    out = StaticDeltaHedge(HEDGE).of(**BLK, dividends_per_share=0.0)
    assert BLK_DELTA == 290.0 and BLK_COSTS == pytest.approx(133.40)
    assert out["stock_costs"] == pytest.approx(133.40, abs=1e-9)
    assert out["short_borrow"] == pytest.approx(17.956164, abs=1e-6)
    assert out["dividend_adjust"] == 0.0
    assert out["hedged_pnl"] == pytest.approx(69.743836, abs=1e-6)
    assert round(out["hedged_pnl"], 2) == 69.74


def test_hedge_blk_book_with_a_one_dollar_dividend_is_owed_by_the_short_hedge():
    out = StaticDeltaHedge(HEDGE).of(**BLK, dividends_per_share=1.0)
    assert out["dividend_adjust"] == pytest.approx(290.0)
    assert out["hedged_pnl"] == pytest.approx(-220.256164, abs=1e-6)
    assert round(out["hedged_pnl"], 2) == -220.26


def test_hedge_without_dividend_input_computes_with_zero_and_leaves_the_adjust_null():
    out = StaticDeltaHedge(HEDGE).of(**BLK, dividends_per_share=None)
    assert out["dividend_adjust"] is None
    assert out["hedged_pnl"] == pytest.approx(69.743836, abs=1e-6)


def test_hedge_short_delta_book_pays_no_borrow_and_receives_the_dividend():
    book = {**BLK, "book_pnl": 938.90, "entry_delta_sh": -290.0}
    out = StaticDeltaHedge(HEDGE).of(**book, dividends_per_share=1.0)
    assert out["short_borrow"] == 0.0
    assert out["dividend_adjust"] == pytest.approx(-290.0)
    assert out["stock_costs"] == pytest.approx(133.40)      # costs use |delta|
    # hedge is long 290: gains 290 x (448 - 452 + 1) = -870 on the stock leg
    assert out["hedged_pnl"] == pytest.approx(938.90 - (-290 * (-4 + 1)) - 133.40, abs=1e-9)


def test_hedge_long_delta_pays_borrow_and_short_delta_does_not_for_the_same_size():
    h = StaticDeltaHedge(HEDGE)
    assert h.of(**BLK, dividends_per_share=0.0)["short_borrow"] > 0.0
    assert h.of(**{**BLK, "entry_delta_sh": -290.0}, dividends_per_share=0.0)["short_borrow"] == 0.0


def test_hedge_zero_delta_book_is_unchanged_and_costs_nothing():
    out = StaticDeltaHedge(HEDGE).of(**{**BLK, "entry_delta_sh": 0.0}, dividends_per_share=2.0)
    assert out["hedged_pnl"] == BLK["book_pnl"]
    assert (out["stock_costs"], out["short_borrow"], out["dividend_adjust"]) == (0.0, 0.0, 0.0)


@pytest.mark.parametrize("delta,dividend", [(290.0, 0.0), (290.0, 1.0), (-290.0, 1.0),
                                              (-37.25, 0.3), (12.5, None)])
def test_hedge_parts_add_back_to_the_book_pnl(delta, dividend):
    book = {**BLK, "entry_delta_sh": delta}
    out = StaticDeltaHedge(HEDGE).of(**book, dividends_per_share=dividend)
    stock, _ = StockEquivalent().split(book["book_pnl"], delta, 452.0, 448.0)
    total = (out["hedged_pnl"] + stock + out["stock_costs"] + out["short_borrow"]
             + (out["dividend_adjust"] or 0.0))
    assert total == pytest.approx(book["book_pnl"], abs=1e-9)


def test_hedge_with_zero_costs_and_rate_reduces_to_the_residual():
    zero = {"hedge_fee_per_share": 0.0, "hedge_half_spread_bps": 0.0, "hedge_borrow_rate": 0.0}
    out = StaticDeltaHedge(zero).of(**BLK, dividends_per_share=0.0)
    assert out["hedged_pnl"] == StockEquivalent().split(-938.90, 290.0, 452.0, 448.0)[1]


def test_hedge_inputs_are_checked():
    h = StaticDeltaHedge(HEDGE)
    with pytest.raises(ValueError, match="hedge_days"):
        h.of(**{**BLK, "hedge_days": -1}, dividends_per_share=0.0)
    with pytest.raises(ValueError, match="s_fill"):
        h.of(**{**BLK, "s_fill": 0.0}, dividends_per_share=0.0)
    with pytest.raises(ValueError, match="dividends_per_share"):
        h.of(**BLK, dividends_per_share=float("nan"))


def test_hedge_params_are_required_without_defaults():
    for key in HEDGE:
        trimmed = {k: v for k, v in HEDGE.items() if k != key}
        assert any("missing" in p and key in p for p in StaticDeltaHedge.validate_params(trimmed))
    assert StaticDeltaHedge._PARAMS == tuple(HEDGE)
    assert StaticDeltaHedge in BookGreeksReport._OWNERS


# ---- the report's hedge columns --------------------------------------------------------
class DivMem(Mem):
    """Mem with a settable dividend hook result."""

    result = (None, "dividends_absent")

    def _hedge_dividend(self, book):
        return self.result


def run_div(result, books=None, **kw):
    node = DivMem("report", REPORT)
    node.result = result
    return node.run(None, {"books": books or [make_book()],
                           "quotes": {("AAA", FILL, EXPIRY): priced_chain()}, "bars": make_bars()})


def expected_hedge(row, dividend):
    delta, fill, settle = row["delta_sh"], row["s_fill"], row["s_settle"]
    costs = abs(delta) * (2 * 0.005 + (fill + settle) * 5.0 / 1e4)
    borrow = max(delta, 0.0) * fill * 0.005 * 17 / 365
    return row["pnl"] - delta * (settle - fill) - costs - borrow - delta * dividend, costs, borrow


def test_report_default_hook_flags_every_row_dividends_absent_and_uses_zero():
    out = run_report([make_book()])
    (row,) = out["books"]
    want, costs, borrow = expected_hedge(row, 0.0)
    assert row["hedge_status"] == "dividends_absent" and row["dividend_adjust"] is None
    assert row["hedge_sh"] == pytest.approx(-row["delta_sh"], rel=1e-15) and row["hedge_days"] == 17
    assert row["stock_costs"] == pytest.approx(costs, rel=1e-12)
    assert row["short_borrow"] == pytest.approx(borrow, rel=1e-12)
    assert want is not None and row["hedged_pnl"] is None     # D = 0 is not printed (A2.2)
    summary = out["summary"]
    assert summary["hedge_status"] == {"dividends_absent": 1}
    assert summary["hedge_dividend_source"] == "absent"
    assert summary["hedge_params"] == HEDGE
    assert summary["hedged_pnl_usd"] is None and summary["n_stock_hedged"] == 0
    (cell,) = out["cells"]
    assert cell["hedged_pnl_usd"] is None and cell["n_stock_hedged"] == 0
    assert cell["n_dividends_absent"] == 1 and cell["n_dividend_missing"] == 0


def test_report_with_a_dividend_adjusts_by_delta_times_the_per_share_over_the_factor():
    out = run_div((1.0, "ok"), books=[make_book(factor=2.0, end_factor=2.0)])
    (row,) = out["books"]
    # factor 2 halves spots (bars 100 -> 50), the dividend on the bars basis halves too
    want, _, _ = expected_hedge(row, 0.5)
    assert row["s_fill"] == 50.0 and row["hedge_status"] == "ok"
    assert row["dividend_adjust"] == pytest.approx(row["delta_sh"] * 0.5, rel=1e-12)
    assert row["hedged_pnl"] == pytest.approx(want, rel=1e-12)
    assert out["summary"]["hedge_status"] == {"ok": 1}


def test_report_dividend_missing_nulls_the_hedged_pnl_and_is_counted():
    out = run_div((None, "dividend_missing"))
    (row,) = out["books"]
    assert row["hedge_status"] == "dividend_missing"
    assert row["hedged_pnl"] is None and row["dividend_adjust"] is None
    assert out["summary"]["hedge_status"] == {"dividend_missing": 1}
    assert out["summary"]["hedged_pnl_usd"] is None and out["summary"]["n_stock_hedged"] == 0
    assert out["cells"][0]["n_dividend_missing"] == 1


def test_report_unattributed_book_has_null_hedge_columns_and_not_attributed_status():
    out = run_report([make_book(pnl=7.0, factor=2.0, end_factor=1.0)])
    (row,) = out["books"]
    assert row["status"] == "basis_changed" and row["hedge_status"] == "not_attributed"
    assert all(row[k] is None for k in ("hedge_sh", "hedge_days", "stock_costs", "short_borrow",
                                       "dividend_adjust", "hedged_pnl"))
    assert out["summary"]["hedge_status"] == {"not_attributed": 1}


def test_report_cell_hedged_sum_adds_up_over_the_ok_rows_and_is_not_nulled_by_one_book():
    books = [make_book("b1", pnl=150.0), make_book("b2", pnl=-40.0)]
    out = run_div((1.0, "ok"), books=books)
    (cell,) = out["cells"]
    rows = out["books"]
    assert cell["n_stock_hedged"] == 2
    assert cell["hedged_pnl_usd"] == pytest.approx(sum(r["hedged_pnl"] for r in rows))
    ca = CellAttribution({"cell_by": ["symbol"], "min_total_usd": 1.0})
    base = {"symbol": "A", "pnl": 1.0, "static_delta_pnl": 0.5, "beyond_static_delta": 0.5}
    (cell,) = ca.summarize([{**base, "hedged_pnl": 0.25, "hedge_status": "ok"},
                            {**base, "hedged_pnl": None, "hedge_status": "dividend_missing"},
                            {**base, "hedged_pnl": None, "hedge_status": "dividends_absent"}])
    assert cell["hedged_pnl_usd"] == 0.25 and cell["n_stock_hedged"] == 1 and cell["n"] == 3
    assert (cell["n_dividends_absent"], cell["n_dividend_missing"]) == (1, 1)
    (cell,) = ca.summarize([{**base, "hedged_pnl": 0.25}, {**base, "hedged_pnl": 0.5}])
    assert cell["hedged_pnl_usd"] == 0.75 and cell["n_stock_hedged"] == 2
    assert (cell["n_dividends_absent"], cell["n_dividend_missing"]) == (0, 0)
    (cell,) = ca.summarize([{**base, "hedged_pnl": None}])
    assert cell["hedged_pnl_usd"] is None and cell["n_stock_hedged"] == 0
    with pytest.raises(ValueError, match="pnl_not_finite"):
        ca.summarize([{**base, "hedged_pnl": float("nan")}])


def test_report_summary_hedged_sum_is_over_ok_rows_with_the_count_beside_it():
    out = run_div((1.0, "ok"), books=[make_book("b1"), make_book("b2", pnl=-3.0)])
    s = out["summary"]
    assert s["n_stock_hedged"] == 2
    assert s["hedged_pnl_usd"] == pytest.approx(math.fsum(r["hedged_pnl"] for r in out["books"]))
    mixed = [make_book("a", symbol="AAA"), make_book("b", symbol="AAA")]
    node = DivMem("report", REPORT)
    node.result = (1.0, "ok")
    seen = iter([(1.0, "ok"), (None, "dividend_missing")])
    node._hedge_dividend = lambda book: next(seen)
    out = node.run(None, {"books": mixed, "quotes": {("AAA", FILL, EXPIRY): priced_chain()},
                          "bars": make_bars()})
    ok = [r["hedged_pnl"] for r in out["books"] if r["hedge_status"] == "ok"]
    assert len(ok) == 1 and out["summary"]["hedged_pnl_usd"] == pytest.approx(ok[0])
    assert out["summary"]["n_stock_hedged"] == 1


def test_report_declares_the_hedge_columns():
    names = ("hedge_sh", "hedge_days", "stock_costs", "short_borrow", "dividend_adjust",
             "hedged_pnl", "hedge_status")
    assert set(names) <= set(BookGreeksReport.BOOK_COLUMNS)
    assert {"hedged_pnl_usd", "n_stock_hedged", "n_dividends_absent",
            "n_dividend_missing"} <= set(BookGreeksReport.CELL_COLUMNS)
    out = run_report([make_book()])
    assert set(BookGreeksReport.BOOK_COLUMNS) <= set(out["books"][0])
    assert BookGreeksReport.HEDGE_STATUSES == (
        "ok", "dividends_absent", "dividend_missing", "not_attributed")


# ---- ADR-0310 Amendment 2, stage 0: the interface freeze ---------------------------------
def test_frozen_column_tuples_use_the_a2_names_and_drop_the_a1_ones():
    books, cells = BookGreeksReport.BOOK_COLUMNS, BookGreeksReport.CELL_COLUMNS
    assert {"static_delta_pnl", "beyond_static_delta"} <= set(books)
    assert not {"stock_pnl", "residual"} & set(books)
    assert cells[:13] == ("n", "pnl_usd", "static_delta_usd", "beyond_static_usd", "hedged_pnl_usd",
                     "n_stock_hedged", "n_dividends_absent", "n_dividend_missing",
                     "static_share_net", "beyond_static_share_net", "gross_pnl_usd",
                     "static_share_gross", "beyond_static_share_gross")
    assert "n_hedged" in cells[13:] and "n_stock_hedged" in cells[:13]


def test_frozen_constants_and_enums():
    assert ox.HEDGE_COST_SOURCES == ("placeholder", "measured")
    assert ox.PLACEHOLDER_HEDGE_RATES == (0.005, 5.0, 0.005)
    assert ox.PLACEHOLDER_HEDGE_RATES == tuple(HEDGE[k] for k in StaticDeltaHedge._PARAMS)
    assert ox.DESCRIPTIVE_ONLY_NOTE == (
        "descriptive only: development quarters, books shared across formulations, no inference")
    assert ox.STOCK_HEDGE_NOTE == "diagnostic: stock hedge at S_fill, not the ADR-0312 instrument"
    assert BookGreeksReport.NEW_CORE_PARAMS == (
        "hedge_cost_source", "min_books", "htb_lookback", "htb_vol_threshold", "htb_jump_threshold")
    assert set(BookGreeksReport.NEW_CORE_PARAMS) <= set(BookGreeksReport._PARAMS)


@pytest.mark.parametrize("change,fragment", [
    ({"hedge_cost_source": "guess"}, "hedge_cost_source"),
    ({"hedge_cost_source": 1}, "hedge_cost_source"),
    ({"min_books": 0}, "min_books"),
    ({"min_books": 2.5}, "min_books"),
    ({"min_books": True}, "min_books"),
    ({"htb_lookback": 5}, "htb_lookback"),
    ({"htb_lookback": "20"}, "htb_lookback"),
    ({"htb_vol_threshold": 0.0}, "htb_vol_threshold"),
    ({"htb_vol_threshold": float("nan")}, "htb_vol_threshold"),
    ({"htb_jump_threshold": -0.1}, "htb_jump_threshold"),
    ({"htb_jump_threshold": "x"}, "htb_jump_threshold"),
])
def test_new_core_params_are_validated(change, fragment):
    problems = Mem.validate_params({**REPORT, **change})
    assert any(fragment in p for p in problems), problems
    with pytest.raises(ConfigError):
        Mem("report", {**REPORT, **change})


def test_measured_cost_source_is_refused_while_the_rates_are_the_placeholder_triple():
    problems = Mem.validate_params({**REPORT, "hedge_cost_source": "measured"})
    assert any("hedge_cost_source_mismatch" in p for p in problems)
    moved = {**REPORT, "hedge_cost_source": "measured", "hedge_fee_per_share": 0.004}
    assert Mem.validate_params(moved) == []
    assert Mem.validate_params({**REPORT, "hedge_fee_per_share": 0.004}) == []   # placeholder label is free


def test_summary_echoes_the_cost_source_and_the_stock_hedge_note():
    s = run_report([make_book()])["summary"]
    assert s["hedge_cost_source"] == "placeholder"
    assert s["stock_hedge_note"] == ox.STOCK_HEDGE_NOTE


# ---- ADR-0310 Amendment 2, stage 1A: forward hedge, shared cost rule, concentration, HTB -------
import statistics  # noqa: E402

from dskit.pipeline.stats import sample_quantile  # noqa: E402

BLK_LEGS = [{"right": "call", "strike": 450.0, "sign": 1, "quantity": 5},
            {"right": "call", "strike": 455.0, "sign": 1, "quantity": 1}]
FORWARD_RATES = {**HEDGE}


def test_leg_and_book_carry_the_forward_delta_and_spot_delta_is_forward_delta_times_f_over_s():
    forward, spot, vol, strike = 101.5, 100.0, 0.2, 100.0
    leg = {"right": "call", "strike": strike, "sign": -1, "quantity": 3, "expiry": EXPIRY}
    row, code = LegGreeks(LEG).of(leg, {"bid": 2.0, "ask": 2.2, "iv": vol}, forward, spot, FILL)
    assert code is None
    years = 17 / 365
    by_hand = -1 * 3 * 100 * black76_delta("call", forward, strike, vol, years, 0.0)
    assert row["delta_f_sh"] == pytest.approx(by_hand, rel=1e-12)
    assert row["delta_sh"] == pytest.approx(row["delta_f_sh"] * forward / spot, rel=1e-12)
    book = BookGreeks.sum([row, row], forward, "parity")
    assert book.delta_f_sh == pytest.approx(2 * by_hand, rel=1e-12)


def test_expiry_delta_blk_book_at_448_458_and_exactly_450():
    ed = ox.ExpiryDelta({"multiplier": 100})
    assert ed.of(BLK_LEGS, 448.0) == (0, 0)
    assert ed.of(BLK_LEGS, 458.0) == (600, 0)
    assert ed.of(BLK_LEGS, 450.0) == (0, 5)           # the 450 leg is at the money (5 contracts)
    se = StockEquivalent()
    # book_pnl at 448: -938.90, both OTM: the whole loss is beyond the payoff delta
    assert se.split(-938.90, 0, 452.0, 448.0) == (0.0, -938.90)
    pnl = 4300.0 - 938.90                                # 5 x 8 x 100 + 1 x 3 x 100 - premium
    assert pnl == pytest.approx(3361.10)
    assert se.split(pnl, 600, 452.0, 458.0)[0] == pytest.approx(3600.0)
    assert se.split(pnl, 600, 452.0, 458.0)[1] == pytest.approx(-238.90)
    assert se.split(pnl, 290, 452.0, 458.0)[0] == pytest.approx(1740.0)
    assert se.split(pnl, 290, 452.0, 458.0)[1] == pytest.approx(1621.10)


def test_expiry_delta_signs_puts_shorts_and_flags_and_refusals():
    ed = ox.ExpiryDelta({"multiplier": 10})
    short_put = [{"right": "put", "strike": 100.0, "sign": -1, "quantity": 2}]
    assert ed.of(short_put, 95.0) == (2 * 10, 0)         # short put ITM: long 20 shares
    assert ed.of(short_put, 105.0) == (0, 0)
    short_call = [{"right": "call", "strike": 100.0, "sign": -1, "quantity": 1}]
    assert ed.of(short_call, 101.0) == (-10, 0)
    assert ed.of(short_put + short_call, 100.0) == (0, 3)
    with pytest.raises(ValueError, match="s_settle"):
        ed.of(short_put, 0.0)
    with pytest.raises(ValueError, match="malformed leg"):
        ed.of([{"right": "x", "strike": 1.0, "sign": 1, "quantity": 1}], 1.0)


def test_carry_costs_reproduces_the_a1_blk_numbers_and_the_forward_case():
    h = StaticDeltaHedge(HEDGE)
    stock = h.carry_costs(-290.0, 452.0, 448.0, 10)
    assert stock["trade_costs"] == pytest.approx(133.40, abs=1e-9)
    assert stock["borrow"] == pytest.approx(17.956164, abs=1e-6)
    fwd = h.carry_costs(-288, 451.0, 448.0, 10)
    assert fwd["trade_costs"] == pytest.approx(132.336, abs=1e-9)
    assert fwd["borrow"] == pytest.approx(17.792877, abs=1e-6)
    mirror = h.carry_costs(288, 451.0, 448.0, 10)
    assert mirror["trade_costs"] == pytest.approx(132.336, abs=1e-9) and mirror["borrow"] == 0.0
    assert h.carry_costs(0, 451.0, 448.0, 10) == {"trade_costs": 0.0, "borrow": 0.0}


def test_carry_costs_refuses_bad_inputs_and_of_is_unchanged_to_the_cent():
    h = StaticDeltaHedge(HEDGE)
    for bad in (dict(units=float("nan")), dict(p_fill=0.0), dict(p_settle=-1.0), dict(days=-1)):
        args = {"units": -1.0, "p_fill": 1.0, "p_settle": 1.0, "days": 1, **bad}
        with pytest.raises(ValueError):
            h.carry_costs(**args)
    out = h.of(**BLK, dividends_per_share=0.0)
    assert out["stock_costs"] == pytest.approx(133.40, abs=1e-9)
    assert out["short_borrow"] == pytest.approx(17.956164, abs=1e-6)
    assert out["hedged_pnl"] == pytest.approx(69.743836, abs=1e-6)
    short = h.of(**{**BLK, "entry_delta_sh": -290.0}, dividends_per_share=0.0)
    assert short["short_borrow"] == 0.0
    assert short["hedged_pnl"] == pytest.approx(-938.90 - 1160.0 - 133.40, abs=1e-9)


def test_of_calls_the_one_cost_rule(monkeypatch):
    h = StaticDeltaHedge(HEDGE)
    seen = []
    original = StaticDeltaHedge.carry_costs

    def spy(self, *args):
        seen.append(args)
        return original(self, *args)
    monkeypatch.setattr(StaticDeltaHedge, "carry_costs", spy)
    h.of(**BLK, dividends_per_share=0.0)
    assert seen == [(-290.0, 452.0, 448.0, 10)]


def test_forward_hedge_units_truncate_toward_zero_once():
    fh = ox.ForwardHedge({"hedge_cost_source": "placeholder"})
    assert fh.units(288.4) == -288 and fh.units(-288.4) == 288
    assert fh.units(0.9) == 0 and fh.units(-0.9) == 0
    assert fh.rounding(-288, 288.4) == pytest.approx(0.4)
    assert fh.rounding(288, -288.4) == pytest.approx(-0.4)
    with pytest.raises(ValueError):
        fh.units(float("nan"))


def test_forward_hedge_blk_worked_numbers():
    h = StaticDeltaHedge(HEDGE)
    fh = ox.ForwardHedge({"hedge_cost_source": "placeholder"})
    units = fh.units(288.4)
    costs = h.carry_costs(units, 451.0, 448.0, 10)
    out = fh.of(-938.90, units, 451.0, 448.0, costs)
    assert units == -288
    assert out["trade_costs_usd"] == pytest.approx(132.336, abs=1e-9)
    assert out["borrow_usd"] == pytest.approx(17.792877, abs=1e-6)
    assert out["forward_hedge_costs"] == out["trade_costs_usd"]            # no borrow charged
    assert out["forward_hedged_pnl"] == pytest.approx(-207.236, abs=1e-9)
    assert out["hedge_pnl"] == pytest.approx(731.664, abs=1e-9)
    identity = out["forward_hedged_pnl"] - units * (448.0 - 451.0) + out["forward_hedge_costs"]
    assert identity == pytest.approx(-938.90, abs=1e-9)
    # borrow rate 0: the hedged P&L does not move (borrow is reported, never charged)
    h0 = StaticDeltaHedge({**HEDGE, "hedge_borrow_rate": 0.0})
    out0 = fh.of(-938.90, units, 451.0, 448.0, h0.carry_costs(units, 451.0, 448.0, 10))
    assert out0["forward_hedged_pnl"] == out["forward_hedged_pnl"] and out0["borrow_usd"] == 0.0
    # the mirror book: long units, no borrow
    mirror = fh.of(-938.90, 288, 451.0, 448.0, h.carry_costs(288, 451.0, 448.0, 10))
    assert mirror["borrow_usd"] == 0.0
    assert mirror["trade_costs_usd"] == pytest.approx(132.336, abs=1e-9)


def test_forward_hedge_does_not_truncate_in_of_and_checks_inputs():
    fh = ox.ForwardHedge({"hedge_cost_source": "measured"})
    costs = {"trade_costs": 1.0, "borrow": 0.0}
    out = fh.of(10.0, -2.5, 100.0, 98.0, costs)
    assert out["forward_hedged_pnl"] == pytest.approx(10.0 + -2.5 * -2.0 - 1.0)
    with pytest.raises(ValueError):
        fh.of(10.0, -2, 0.0, 98.0, costs)
    with pytest.raises(ValueError):
        fh.of(float("nan"), -2, 100.0, 98.0, costs)


# ---- hard to borrow -------------------------------------------------------------------
HTB = {"htb_lookback": 6, "htb_vol_threshold": 1.0, "htb_jump_threshold": 0.5}


def test_htb_realized_vol_is_sample_stdev_of_log_returns_times_sqrt_252():
    closes = [100.0, 110.0, 100.0, 110.0, 100.0, 110.0]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    vol = statistics.stdev(rets) * math.sqrt(252)
    assert vol == pytest.approx(1.6575, abs=1e-3)
    assert ox.TRADING_DAYS_PER_YEAR == 252
    px = ox.HardToBorrowProxy(HTB)
    assert px.of(closes) is True                                     # vol 1.6575 > 1.0, jump 0.095 < 0.5
    just = ox.HardToBorrowProxy({**HTB, "htb_vol_threshold": vol})
    assert just.of(closes) is False                                  # strictly exceeds
    assert ox.HardToBorrowProxy({**HTB, "htb_vol_threshold": vol - 1e-9}).of(closes) is True


def test_htb_largest_five_session_log_move_flags_and_a_flat_name_does_not():
    jump = [100.0] * 5 + [100.0 * math.exp(0.6)]
    quiet_vol = {**HTB, "htb_vol_threshold": 100.0}
    assert ox.HardToBorrowProxy(quiet_vol).of(jump) is True          # 0.6 > 0.5
    assert ox.HardToBorrowProxy({**quiet_vol, "htb_jump_threshold": 0.6000001}).of(jump) is False
    assert ox.HardToBorrowProxy(HTB).of([100.0] * 6) is False
    fall = [100.0] * 5 + [100.0 * math.exp(-0.6)]
    assert ox.HardToBorrowProxy(quiet_vol).of(fall) is True          # the move counts in absolute value


def test_htb_window_is_the_lookback_closes_strictly_before_the_day_and_not_split_adjusted():
    px = ox.HardToBorrowProxy(HTB)
    series = {f"2021-02-{d:02d}": 100.0 + d for d in (15, 16, 17, 18, 19, 22, 23, 24)}
    series["2021-03-01"] = 999.0
    assert px.closes_before(series, "2021-02-24") == [116.0, 117.0, 118.0, 119.0, 122.0, 123.0]
    assert px.closes_before(series, "2021-03-02")[-1] == 999.0
    assert px.closes_before(series, "2021-03-01")[-1] == 124.0       # the day itself is excluded
    assert px.closes_before(series, "2021-02-17") is None            # fewer than the lookback
    bad = {**series, "2021-02-19": 0.0}
    assert px.closes_before(bad, "2021-02-24") is None               # a non-positive close
    with pytest.raises(ValueError):
        px.of([100.0] * 5)                                           # not the lookback length


# ---- concentration --------------------------------------------------------------------
def conc(values, min_books=5, field="v"):
    return ox.Concentration({"min_books": min_books}).of([{field: v} for v in values], field, [field])


def test_concentration_on_one_two_five_and_twelve_rows():
    one = conc([3.0])
    assert one["n_books"] == 1 and one["top1_share_gross"] == 1.0 and one["top3_share_gross"] == 1.0
    assert one["loo_sign_stable_v"] is None and one["small_cell"] is True
    two = conc([3.0, -1.0])
    assert two["top1_share_gross"] == pytest.approx(0.75) and two["top3_share_gross"] == 1.0
    assert two["loo_sign_stable_v"] is False                 # dropping the 3 flips the total to -1
    five = conc([1.0] * 5)
    assert five["small_cell"] is False and five["loo_sign_stable_v"] is True   # n == min_books
    assert five["top1_share_gross"] == pytest.approx(0.2) and five["top3_share_gross"] == pytest.approx(0.6)
    twelve = conc([4.0, 3.0, 2.0] + [1.0] * 9)
    assert twelve["loo_sign_stable_v"] is True and twelve["small_cell"] is False
    assert twelve["top3_share_gross"] == pytest.approx(9 / 18)
    flip = conc([10.0, -4.0, -3.0, -2.0, -1.0] + [0.5] * 7)
    assert flip["loo_sign_stable_v"] is False and flip["n_books"] == 12


def test_concentration_zero_gross_zero_total_and_empty_are_null_or_false():
    z = conc([0.0, 0.0])
    assert z["top1_share_gross"] is None and z["top3_share_gross"] is None
    assert z["loo_sign_stable_v"] is False                   # a zero counts as a change
    cancel = conc([1.0, -1.0])
    assert cancel["loo_sign_stable_v"] is False
    empty = conc([])
    assert empty["n_books"] == 0 and empty["top1_share_gross"] is None
    assert empty["loo_sign_stable_v"] is None and empty["small_cell"] is True
    with pytest.raises(ValueError):
        conc([float("nan"), 1.0])


def test_concentration_signs_each_sign_field():
    c = ox.Concentration({"min_books": 1}).of(
        [{"a": 1.0, "b": 5.0}, {"a": 1.0, "b": -9.0}], "a", ["a", "b"])
    assert c["loo_sign_stable_a"] is True and c["loo_sign_stable_b"] is False
    assert c["small_cell"] is False


# ---- new owners' params ---------------------------------------------------------------
NEW_OWNERS = [
    ("ExpiryDelta", {"multiplier": 100}),
    ("ForwardHedge", {"hedge_cost_source": "placeholder"}),
    ("Concentration", {"min_books": 5}),
    ("HardToBorrowProxy", {"htb_lookback": 20, "htb_vol_threshold": 2.5,
                            "htb_jump_threshold": 0.75}),
]


@pytest.mark.parametrize("cls,good", NEW_OWNERS)
def test_new_owners_are_default_deny_and_all_required(cls, good):
    cls = getattr(ox, cls)
    cls(good)
    assert tuple(good) == cls._PARAMS or set(good) == set(cls._PARAMS)
    assert any("unknown" in p for p in cls.validate_params({**good, "typo": 1}))
    for key in cls._PARAMS:
        assert any("missing" in p for p in cls.validate_params(
            {k: v for k, v in good.items() if k != key})), key
    assert cls.validate_params([]) != []


@pytest.mark.parametrize("cls,good,bad", [
    ("ExpiryDelta", NEW_OWNERS[0][1], {"multiplier": 0}),
    ("ExpiryDelta", NEW_OWNERS[0][1], {"multiplier": True}),
    ("ForwardHedge", NEW_OWNERS[1][1], {"hedge_cost_source": "guess"}),
    ("ForwardHedge", NEW_OWNERS[1][1], {"hedge_cost_source": 1}),
    ("Concentration", NEW_OWNERS[2][1], {"min_books": 0}),
    ("Concentration", NEW_OWNERS[2][1], {"min_books": 2.5}),
    ("Concentration", NEW_OWNERS[2][1], {"min_books": True}),
    ("HardToBorrowProxy", NEW_OWNERS[3][1], {"htb_lookback": 5}),
    ("HardToBorrowProxy", NEW_OWNERS[3][1], {"htb_lookback": "20"}),
    ("HardToBorrowProxy", NEW_OWNERS[3][1], {"htb_vol_threshold": 0.0}),
    ("HardToBorrowProxy", NEW_OWNERS[3][1], {"htb_vol_threshold": float("nan")}),
    ("HardToBorrowProxy", NEW_OWNERS[3][1], {"htb_jump_threshold": -0.1}),
    ("HardToBorrowProxy", NEW_OWNERS[3][1], {"htb_jump_threshold": "x"}),
])
def test_new_owners_reject_bad_values(cls, good, bad):
    cls = getattr(ox, cls)
    assert cls.validate_params({**good, **bad}) != []
    with pytest.raises(ConfigError):
        cls({**good, **bad})


def test_the_report_takes_its_new_params_through_the_owners():
    for name in ("ExpiryDelta", "ForwardHedge", "Concentration", "HardToBorrowProxy"):
        assert getattr(ox, name) in BookGreeksReport._OWNERS
    assert set(BookGreeksReport.NEW_CORE_PARAMS) <= set(BookGreeksReport._PARAMS)
    problems = Mem.validate_params({k: v for k, v in REPORT.items() if k != "min_books"})
    assert any("Concentration" in p and "missing" in p for p in problems)


# ---- the report: decision-day sizing, statuses, hooks -----------------------------------
class Pass(Mem):
    """A classifier stub that says every hedge is bounded (the Stage 2b call is not built)."""

    def _unbounded_above(self, book, units, f_fill):
        return False


class Unbounded(Mem):
    def _unbounded_above(self, book, units, f_fill):
        return True


def decision_quotes(forward=99.0, iv=0.3):
    return {("AAA", FILL, EXPIRY): priced_chain(),
            ("AAA", DECISION, EXPIRY): priced_chain(forward, DECISION, EXPIRY, iv=iv)}


def run_with(cls, books, quotes=None, bars=None, **params):
    node = cls("report", {**REPORT, **params})
    return node.run(None, {"books": books,
                           "quotes": decision_quotes() if quotes is None else quotes,
                           "bars": make_bars() if bars is None else bars})


def test_the_hedge_is_sized_from_the_decision_session_chain_and_struck_at_f_fill():
    out = run_with(Pass, [make_book(pnl=150.0)])
    row = out["books"][0]
    years_decision = (date_of(EXPIRY) - date_of(DECISION)).days / 365
    decision_delta = 2 * 100 * black76_delta("call", 99.0, 100.0, 0.3, years_decision, 0.0)
    assert row["delta_f_sh_decision"] == pytest.approx(decision_delta, rel=1e-9)
    years_fill = (date_of(EXPIRY) - date_of(FILL)).days / 365
    fill_delta_f = 2 * 100 * black76_delta("call", 101.5, 100.0, 0.2, years_fill, 0.0)
    assert row["delta_f_sh"] == pytest.approx(fill_delta_f, rel=1e-9)
    assert row["delta_f_sh_decision"] != pytest.approx(row["delta_f_sh"], rel=1e-3)
    assert row["delta_sh"] == pytest.approx(row["delta_f_sh"] * 101.5 / 100.0, rel=1e-9)
    units = math.trunc(-decision_delta)
    assert row["forward_hedge_units"] == units and units < 0
    assert row["hedge_rounding_sh"] == pytest.approx(units + decision_delta)
    trade = abs(units) * (2 * 0.005 + (101.5 + 104.0) * 5.0 / 1e4)
    borrow = abs(units) * 101.5 * 0.005 * 17 / 365
    assert row["trade_costs_usd"] == pytest.approx(trade, rel=1e-9)
    assert row["borrow_usd"] == pytest.approx(borrow, rel=1e-9)
    assert row["forward_hedge_costs"] == pytest.approx(trade, rel=1e-9)
    hedged = 150.0 + units * (104.0 - 101.5) - trade
    assert row["forward_hedged_pnl"] == pytest.approx(hedged, rel=1e-9)
    assert row["hedge_pnl"] == pytest.approx(hedged - 150.0, rel=1e-9)
    assert row["forward_hedge_status"] == "ok" and row["hard_to_borrow"] is False
    assert row["hedge_cost_source"] == "placeholder"


def test_forward_spot_book_hedges_at_f_equals_s():
    out = run_with(Pass, [make_book(pnl=150.0)], quotes={
        ("AAA", FILL, EXPIRY): [r for r in priced_chain() if r["right"] == "call"],
        ("AAA", DECISION, EXPIRY): decision_quotes()[("AAA", DECISION, EXPIRY)]})
    row = out["books"][0]
    assert row["status"] == "forward_spot" and row["forward"] == 100.0
    expected = 150.0 + row["forward_hedge_units"] * (104.0 - 100.0) - row["trade_costs_usd"]
    assert row["forward_hedged_pnl"] == pytest.approx(expected, rel=1e-9)


def _book_of(*legs, key="k"):
    return make_book(key=key, legs=[{"right": r, "strike": k, "sign": 1 if q > 0 else -1,
                                     "quantity": abs(q)} for r, k, q in legs])


# ADR-0312 Amendment 1's table, called as structure_max_loss(..., hedge=(U_sh, F_ref)), U_sh in held shares.
@pytest.mark.parametrize("legs, units, f_ref, unbounded", [
    ((("call", 105.0, 1),), -35, 100.0, False),                              # long call: reserve 375
    ((("call", 105.0, 1), ("call", 110.0, -1)), -15, 100.0, True),           # bull call spread
    ((("put", 95.0, -1),), -30, 100.0, True),                                # short put
    ((("put", 95.0, -1), ("put", 90.0, 1)), -12, 100.0, True),               # bull put spread
    ((("call", 450.0, 5), ("call", 455.0, 1)), -288, 451.0, False),          # the 5:1 glossary book, 650.90
    ((("call", 100.0, 1),), -100, 100.0, False),                             # zero slope is bounded
    ((("call", 100.0, 1),), -101, 100.0, True),                              # negative slope is not
    ((("put", 95.0, 1),), 30, 100.0, False),                                 # long put, long hedge
])
def test_the_default_classifier_is_the_held_shares_structure_max_loss_rule(legs, units, f_ref, unbounded):
    node = Mem("report", REPORT)
    assert node._unbounded_above(_book_of(*legs), units, f_ref) is unbounded


def test_the_default_classifier_calls_structure_max_loss_with_held_shares_and_the_fill_forward(monkeypatch):
    seen = []

    def spy(legs, strikes, credit, multiplier, hedge=None):
        seen.append((list(legs), list(strikes), multiplier, hedge))
        return 650.90
    monkeypatch.setattr(ox, "structure_max_loss", spy)
    book = _book_of(("call", 450.0, 5), ("call", 455.0, 1))
    assert Mem("report", REPORT)._unbounded_above(book, -288, 451.0) is False
    assert seen == [([("call", 5), ("call", 1)], [450.0, 455.0], 100, (-288, 451.0))]


def test_the_default_classifier_marks_a_bounded_book_ok_and_an_unbounded_one_unhedged():
    ok = run_with(Mem, [make_book()])["books"][0]                    # long calls, short-share hedge
    assert ok["forward_hedge_status"] == "ok" and ok["forward_hedged_pnl"] is not None
    put = [{"right": "put", "strike": 100.0, "sign": -1, "quantity": 2}]    # short put: delta positive
    bad = run_with(Mem, [make_book(legs=put)])["books"][0]
    assert bad["forward_hedge_status"] == "hedge_unbounded_above"
    assert bad["forward_hedge_units"] < 0 and bad["hedge_pnl"] is None and bad["forward_hedged_pnl"] is None


def test_hard_to_borrow_takes_precedence_over_the_unbounded_check():
    put = [{"right": "put", "strike": 100.0, "sign": -1, "quantity": 2}]
    row = run_with(Mem, [make_book(legs=put)], bars=htb_bars(), **HTB_PARAMS)["books"][0]
    assert row["forward_hedge_units"] < 0 and row["hard_to_borrow"] is True
    assert row["forward_hedge_status"] == "hedge_hard_to_borrow"


def test_classifier_pending_is_gone_from_the_codes_and_the_counts():
    assert not hasattr(ox, "CLASSIFIER_PENDING")
    assert "classifier_pending" not in ox.FORWARD_HEDGE_STATUSES
    assert "n_classifier_pending" not in BookGreeksReport.CELL_COLUMNS


def test_an_unbounded_hedge_keeps_the_units_and_nulls_the_three_hedge_columns():
    ok = run_with(Pass, [make_book()])["books"][0]
    row = run_with(Unbounded, [make_book()])["books"][0]
    assert row["forward_hedge_status"] == "hedge_unbounded_above"
    assert row["forward_hedge_units"] == ok["forward_hedge_units"]
    assert (row["forward_hedge_costs"], row["forward_hedged_pnl"], row["hedge_pnl"]) == (None,) * 3


def test_the_classifier_gets_the_truncated_units_and_the_fill_forward():
    seen = []

    class Spy(Mem):
        def _unbounded_above(self, book, units, f_fill):
            seen.append((book["key"], units, f_fill))
            return False
    row = run_with(Spy, [make_book()])["books"][0]
    assert seen == [("b1", row["forward_hedge_units"], pytest.approx(101.5))]
    assert isinstance(seen[0][1], int)


def test_a_book_the_decision_chain_cannot_mark_is_decision_unmarked_and_stays_attributed():
    quotes = {("AAA", FILL, EXPIRY): priced_chain()}
    out = run_with(Pass, [make_book()], quotes=quotes)
    row = out["books"][0]
    assert row["forward_hedge_status"] == "decision_unmarked" and row["static_delta_pnl"] is not None
    assert row["delta_f_sh_decision"] is None and row["forward_hedge_units"] is None
    assert out["summary"]["decision_greeks_refusals"] == {"no_fill_quote": 1}
    assert out["summary"]["forward_hedge_status"] == {"decision_unmarked": 1}


def test_an_unattributed_book_is_not_attributed_with_null_forward_columns():
    row = run_with(Pass, [make_book()], quotes={})["books"][0]
    assert row["static_delta_pnl"] is None and row["forward_hedge_status"] == "not_attributed"
    for name in ("delta_f_sh", "delta_f_sh_decision", "forward_hedge_units", "hedge_pnl",
                 "payoff_delta_sh", "hard_to_borrow"):
        assert row[name] is None, name


def test_the_payoff_split_columns_use_the_expiry_delta_and_sum_to_the_pnl():
    row = run_with(Pass, [make_book(pnl=150.0)])["books"][0]
    # settles at 104 > 100: 2 calls in the money -> 200 payoff-delta shares
    assert row["payoff_delta_sh"] == 200 and row["atm_legs"] == 0
    assert row["payoff_delta_pnl"] == pytest.approx(200 * (104.0 - 100.0))
    assert row["payoff_delta_pnl"] + row["beyond_payoff_delta"] == pytest.approx(150.0)
    flat = make_bars()
    flat["AAA"][EXPIRY] = 100.0
    atm = run_with(Pass, [make_book(pnl=150.0)], bars=flat)["books"][0]
    assert atm["payoff_delta_sh"] == 0 and atm["atm_legs"] == 2


HTB_BARS = {"2021-02-19": 100.0, "2021-02-22": 100.0, "2021-02-23": 100.0, "2021-02-24": 100.0,
            "2021-02-25": 100.0, "2021-02-26": 100.0 * math.exp(0.6)}


def htb_bars():
    bars = make_bars()
    bars["AAA"].update(HTB_BARS)
    return bars


HTB_PARAMS = {"htb_lookback": 6, "htb_vol_threshold": 100.0, "htb_jump_threshold": 0.5}


def test_a_hard_to_borrow_short_hedge_is_left_unhedged_and_a_long_hedge_is_not():
    put = [{"right": "put", "strike": 100.0, "sign": 1, "quantity": 2}]
    books = [make_book("short_hedge"), make_book("long_hedge", legs=put)]
    out = run_with(Pass, books, bars=htb_bars(), **HTB_PARAMS)
    short, long = out["books"]
    assert short["hard_to_borrow"] is True and long["hard_to_borrow"] is True
    assert short["forward_hedge_units"] < 0 < long["forward_hedge_units"]
    assert short["forward_hedge_status"] == "hedge_hard_to_borrow"
    assert (short["forward_hedge_costs"], short["forward_hedged_pnl"], short["hedge_pnl"]) == (None,) * 3
    assert long["forward_hedge_status"] == "ok" and long["hedge_pnl"] is not None
    assert out["cells"][0]["n_hard_to_borrow"] == 1 and out["cells"][0]["n_hedged"] == 1
    quiet = run_with(Pass, [make_book()], bars=make_bars(), **HTB_PARAMS)["books"][0]
    assert quiet["hard_to_borrow"] is False       # fewer than 6 closes before the decision


def test_parity_rows_hook_is_the_identity_by_default_and_feeds_the_forward_only():
    node = Mem("report", REPORT)
    rows = priced_chain()
    assert node._parity_rows(make_book(), FILL, rows) is rows
    calls = []

    class Filtered(Pass):
        def _parity_rows(self, book, day, rows):
            calls.append(day)
            return []                                 # no usable pair: the forward falls back to spot
    plain = run_with(Pass, [make_book()])["books"][0]
    out = run_with(Filtered, [make_book()])["books"][0]
    assert calls.count(FILL) >= 1 and calls.count(DECISION) == 1
    assert plain["status"] == "ok" and out["status"] == "forward_spot"
    assert out["forward"] == 100.0 and out["delta_sh"] != plain["delta_sh"]
    assert out["static_delta_pnl"] is not None        # the leg quotes still came from the full rows
    assert plain["forward"] == pytest.approx(101.5)


# ---- cells, sign table, direction read ------------------------------------------------------
def frow(pnl, status="ok", fhp=None, hedge_pnl=None, costs=None, basis=0.0, static=0.0,
         payoff=0.0, **extra):
    return {"pnl": pnl, "static_delta_pnl": static, "beyond_static_delta": pnl - static,
            "payoff_delta_pnl": payoff, "beyond_payoff_delta": pnl - payoff,
            "forward_hedge_status": status, "forward_hedged_pnl": fhp, "hedge_pnl": hedge_pnl,
            "forward_hedge_costs": costs, "basis_usd": basis, "hedged_pnl": None, **extra}


def test_cell_hedge_effect_and_concentration_by_hand_on_three_books():
    rows = [frow(100.0, fhp=40.0, hedge_pnl=-60.0, costs=10.0, sym="A"),
            frow(-300.0, fhp=-100.0, hedge_pnl=200.0, costs=20.0, sym="A"),
            frow(50.0, status="hedge_unbounded_above", sym="A")]
    ca = CellAttribution({"cell_by": ["sym"], "min_total_usd": 1.0})
    cell = ca.summarize(rows, concentration=ox.Concentration({"min_books": 5}))[0]
    assert cell["n_hedged"] == 2 and cell["n_unbounded_above"] == 1
    assert cell["n_hard_to_borrow"] == 0 and cell["n_not_attributed"] == 0
    assert cell["forward_hedged_pnl_usd"] == pytest.approx(-60.0)
    assert cell["hedge_pnl_usd"] == pytest.approx(-60.0 - (100.0 - 300.0)) == pytest.approx(140.0)
    assert cell["hedge_loss_reduction_usd"] == pytest.approx(300.0 - 100.0)   # a loss cut, a win cut
    assert cell["hedge_costs_usd"] == pytest.approx(30.0)
    assert cell["hedgeable_share_gross"] == pytest.approx(400.0 / 450.0)
    assert cell["top1_share_gross"] == pytest.approx(300.0 / 450.0) and cell["top3_share_gross"] == 1.0
    assert cell["small_cell"] is True
    assert cell["loo_sign_stable_pnl"] is False                # dropping the loss flips -150
    assert cell["loo_sign_stable_hedge_pnl"] is False
    assert cell["payoff_delta_usd"] == 0.0 and cell["beyond_payoff_usd"] == pytest.approx(-150.0)


def test_cell_without_a_concentration_or_hedge_rows_has_nulls_not_errors():
    ca = CellAttribution({"cell_by": ["sym"], "min_total_usd": 1.0})
    cell = ca.summarize([frow(5.0, status="hedge_unbounded_above", sym="A")])[0]
    assert cell["n_hedged"] == 0 and cell["forward_hedged_pnl_usd"] is None
    assert cell["hedge_pnl_usd"] is None and cell["hedge_loss_reduction_usd"] is None
    assert cell["hedgeable_share_gross"] == 0.0
    plain = ca.summarize([{"sym": "A", "pnl": 1.0, "static_delta_pnl": 0.5,
                           "beyond_static_delta": 0.5}])[0]
    assert plain["payoff_delta_usd"] is None and plain["n_hedged"] == 0


def test_the_cell_columns_tail_matches_the_cells_the_report_writes():
    out = run_with(Pass, [make_book()])
    cols = BookGreeksReport.CELL_COLUMNS
    assert cols[13:] == (
        "payoff_delta_usd", "beyond_payoff_usd", "n_hedged", "n_unbounded_above",
        "n_hard_to_borrow", "n_not_attributed", "n_decision_unmarked",
        "n_htb_unreadable", "hedgeable_share_gross", "forward_hedged_pnl_usd", "hedge_pnl_usd", "hedge_loss_reduction_usd", "hedge_costs_usd", "top1_share_gross",
        "top3_share_gross", "loo_sign_stable_pnl", "loo_sign_stable_hedge_pnl", "small_cell",
        "descriptive_only", "hedge_cost_source")
    assert list(out["cells"][0])[-len(cols):] == list(cols)
    assert out["cells"][0]["descriptive_only"] == ox.DESCRIPTIVE_ONLY_NOTE
    assert out["cells"][0]["hedge_cost_source"] == "placeholder"
    assert BookGreeksReport.BOOK_COLUMNS[-17:-1] == (
        "payoff_delta_sh", "payoff_delta_pnl", "beyond_payoff_delta", "atm_legs", "delta_f_sh",
        "delta_f_sh_decision", "forward_hedge_units", "hedge_rounding_sh", "trade_costs_usd",
        "borrow_usd", "forward_hedge_costs", "forward_hedged_pnl", "hedge_pnl",
        "forward_hedge_status", "hard_to_borrow", "hedge_cost_source")
    assert set(BookGreeksReport.BOOK_COLUMNS) <= set(out["books"][0])


def test_sign_table_quadrants_and_zero_sum_to_n_and_pnl():
    rows = [frow(10.0, static=5.0), frow(-20.0, static=5.0), frow(7.0, static=-3.0),
            frow(-1.0, static=-3.0), frow(-9.0, static=0.0), frow(0.0, static=4.0)]
    table = BookGreeksReport.sign_table(rows, "static_delta_pnl")
    assert set(table) == {"pos_pos", "pos_neg", "neg_pos", "neg_neg", "zero"}
    assert table["pos_pos"] == {"n": 1, "pnl_usd": 10.0, "gross_pnl_usd": 10.0}
    assert table["pos_neg"] == {"n": 1, "pnl_usd": -20.0, "gross_pnl_usd": 20.0}
    assert table["neg_pos"] == {"n": 1, "pnl_usd": 7.0, "gross_pnl_usd": 7.0}
    assert table["neg_neg"] == {"n": 1, "pnl_usd": -1.0, "gross_pnl_usd": 1.0}
    assert table["zero"] == {"n": 2, "pnl_usd": -9.0, "gross_pnl_usd": 9.0}
    assert sum(v["n"] for v in table.values()) == len(rows)
    assert math.fsum(v["pnl_usd"] for v in table.values()) == pytest.approx(-13.0)


def test_an_all_otm_loser_lands_in_the_payoff_zero_entry():
    rows = [frow(-938.90, payoff=0.0)]
    assert BookGreeksReport.sign_table(rows, "payoff_delta_pnl")["zero"]["n"] == 1


def eight_rows():
    pnls = [100.0, -50.0, 30.0, -200.0, 10.0, 20.0, -5.0, 60.0]
    rows = [frow(p, basis=b, static=p / 2, payoff=0.0) for p, b in
            zip(pnls, [1.0, -2.0, 3.0, 0.5, -0.5, 4.0, -1.0, 2.0])]
    rows[0].update(forward_hedged_pnl=40.0, hedge_pnl=-60.0, forward_hedge_costs=10.0)
    rows[3].update(forward_hedged_pnl=-120.0, hedge_pnl=80.0, forward_hedge_costs=12.0)
    for i in (1, 2):
        rows[i]["forward_hedge_status"] = "hedge_unbounded_above"
    rows[4]["forward_hedge_status"] = "hedge_hard_to_borrow"
    rows[5]["forward_hedge_status"] = "decision_unmarked"
    rows[6]["forward_hedge_status"] = "not_attributed"
    rows[7]["forward_hedge_status"] = "hedge_unbounded_above"
    return rows


def test_direction_read_concentration_is_over_the_hedged_rows_only():
    rows = eight_rows()
    unattributed = {"pnl": 999.0, "static_delta_pnl": None, "beyond_static_delta": None,
                    "forward_hedge_status": "not_attributed", "basis_usd": None}
    dr = Pass("report", REPORT).direction_read(rows + [unattributed])
    assert dr["n_books"] == 8 and dr["n_books_total"] == 9
    assert dr["concentration"]["n_books"] == 2 and dr["concentration"]["small_cell"] is True
    assert dr["concentration"]["min_books"] == 5
    assert dr["concentration_pnl"]["n_books"] == 8 and dr["concentration_pnl"]["small_cell"] is False
    assert dr["concentration"]["top1_share_gross"] == pytest.approx(80.0 / 140.0)
    assert dr["concentration"]["loo_sign_stable_hedge_pnl"] is False
    assert dr["concentration_pnl"]["loo_sign_stable_pnl"] is False   # dropping the -50 flips -35
    assert dr["pnl_usd"] == pytest.approx(math.fsum([100, -50, 30, -200, 10, 20, -5, 60]))
    assert dr["gross_pnl_usd"] == pytest.approx(475.0)
    assert dr["descriptive_only"] == ox.DESCRIPTIVE_ONLY_NOTE


def test_direction_read_forward_hedge_block_and_basis_distribution():
    rows = eight_rows()
    dr = Pass("report", REPORT).direction_read(rows)
    fh = dr["forward_hedge"]
    assert (fh["n_hedged"], fh["n_unbounded_above"], fh["n_hard_to_borrow"],
            fh["n_decision_unmarked"], fh["n_not_attributed"]) == (2, 3, 1, 1, 1)
    assert "n_classifier_pending" not in fh
    assert fh["min_books"] == 5 and fh["hedge_cost_source"] == "placeholder"
    assert fh["forward_hedged_pnl_usd"] == pytest.approx(40.0 - 120.0)
    assert fh["hedge_pnl_usd"] == pytest.approx(-80.0 - (100.0 - 200.0)) == pytest.approx(20.0)
    assert fh["hedge_loss_reduction_usd"] == pytest.approx(200.0 - 120.0)
    assert fh["hedge_costs_usd"] == pytest.approx(22.0)
    assert fh["hedgeable_share_gross"] == pytest.approx(300.0 / 470.0)   # the not_attributed -5 is out
    basis = [1.0, -2.0, 3.0, 0.5, -0.5, 4.0, -1.0, 2.0]
    b = dr["basis_usd"]
    assert b["n"] == 8 and b["sum"] == pytest.approx(sum(basis))
    assert (b["min"], b["max"]) == (-2.0, 4.0)
    assert b["p25"] == sample_quantile(basis, 0.25) and b["median"] == sample_quantile(basis, 0.5)
    assert b["p75"] == sample_quantile(basis, 0.75)
    assert b["abs_sum_over_gross"] == pytest.approx(sum(abs(x) for x in basis) / 475.0)
    assert dr["static"]["delta_part_usd"] == pytest.approx(sum([100, -50, 30, -200, 10, 20, -5, 60]) / 2)
    assert dr["payoff"]["delta_part_usd"] == 0.0 and dr["payoff"]["sign_table"]["zero"]["n"] == 8


def test_direction_read_with_no_rows_is_null_not_an_error():
    dr = Pass("report", REPORT).direction_read([])
    assert dr["n_books"] == 0 and dr["pnl_usd"] == 0.0
    assert dr["basis_usd"]["n"] == 0 and dr["basis_usd"]["median"] is None
    assert dr["forward_hedge"]["hedgeable_share_gross"] is None
    assert dr["concentration"]["small_cell"] is True


# ---- stage 0-1 review minors ------------------------------------------------------------------
def test_an_unreadable_htb_window_is_counted_not_flagged_and_behaviour_is_unchanged():
    out = run_with(Pass, [make_book()], bars=make_bars(), **HTB_PARAMS)     # 5 closes before decision < 6
    row = out["books"][0]
    assert row["hard_to_borrow"] is False and row["htb_unreadable"] is True
    assert row["forward_hedge_status"] == "ok"                             # fail-open, as before
    assert out["summary"]["htb_unreadable"] == 1
    assert out["cells"][0]["n_htb_unreadable"] == 1
    fh = Pass("report", {**REPORT, **HTB_PARAMS}).direction_read(out["books"])["forward_hedge"]
    assert fh["n_htb_unreadable"] == 1
    readable = run_with(Pass, [make_book()], bars=htb_bars(), **HTB_PARAMS)
    assert readable["books"][0]["htb_unreadable"] is False and readable["summary"]["htb_unreadable"] == 0
    assert run_with(Pass, [make_book()], quotes={})["books"][0]["htb_unreadable"] is None


def test_a_cells_status_counts_sum_to_its_n():
    rows = [dict(r, sym="A") for r in eight_rows()]
    rows.append(frow(1.0, status="not_attributed", sym="A"))
    cell = CellAttribution({"cell_by": ["sym"], "min_total_usd": 1.0}).summarize(rows)[0]
    counts = ("n_hedged", "n_unbounded_above", "n_hard_to_borrow", "n_decision_unmarked",
              "n_not_attributed")
    assert sum(cell[k] for k in counts) == cell["n"] == 9
    assert (cell["n_decision_unmarked"], cell["n_not_attributed"]) == (1, 2)


def test_the_new_count_columns_are_declared_and_written_by_the_report():
    out = run_with(Pass, [make_book()])
    for name in ("n_decision_unmarked", "n_htb_unreadable"):
        assert name in BookGreeksReport.CELL_COLUMNS and name in out["cells"][0]
    assert "htb_unreadable" in BookGreeksReport.BOOK_COLUMNS
    assert list(out["cells"][0])[-len(BookGreeksReport.CELL_COLUMNS):] == list(
        BookGreeksReport.CELL_COLUMNS)


def test_direction_read_has_no_lambda():
    import inspect
    assert "lambda" not in inspect.getsource(BookGreeksReport.direction_read)


# ---- ADR-0311 Amendment 1 stage A2: shared parity pairs and DirectionEdge ----------------
import inspect  # noqa: E402

from dskit.pipeline.option_exposure import DirectionEdge  # noqa: E402

DE_DIRECTION = {**LEG, **{**PARITY, "min_pairs": 2, "parity_strikes": 5},
             "forward_se_floor_frac": 0.001}


def de_pair_rows(forwards):
    """Rows whose strike-``k`` pair has parity forward exactly ``forwards[k]``."""
    rows = []
    for k, f in forwards.items():
        for right, sgn in (("call", 1), ("put", -1)):
            mid = 5.0 + sgn * (f - k) / 2
            rows.append({"right": right, "strike": k, "bid": mid - 0.05, "ask": mid + 0.05})
    return rows


DE_FIVE = {100: 100.0, 99: 100.2, 101: 99.8, 98: 100.4, 102: 99.6}


def test_pairs_nearest_first_and_from_chain_is_their_median():
    pf = ParityForward({**PARITY, "parity_strikes": 5})
    rows = de_pair_rows(DE_FIVE)
    assert pf.pairs(rows, 100.0) == pytest.approx([100.0, 100.2, 99.8, 100.4, 99.6])
    assert pf.pairs(rows, 100.0, exclude_strikes=()) == pf.pairs(rows, 100.0)
    assert pf.from_chain(rows, 100.0) == (pytest.approx(100.0), "parity")
    assert pf.from_chain(rows, 100.0) == pf.from_chain(rows, 100.0, exclude_strikes=())


def test_pairs_keeps_only_parity_strikes_nearest():
    pf = ParityForward({**PARITY, "parity_strikes": 2})
    assert pf.pairs(de_pair_rows(DE_FIVE), 100.0) == pytest.approx([100.0, 100.2])


def test_exclude_strikes_removes_exactly_those_pairs():
    pf = ParityForward({**PARITY, "parity_strikes": 5})
    rows = de_pair_rows(DE_FIVE)
    got = pf.pairs(rows, 100.0, exclude_strikes=(100, 98.0))
    assert got == pytest.approx([100.2, 99.8, 99.6])
    assert pf.pairs(rows, 100.0, exclude_strikes=[777]) == pf.pairs(rows, 100.0)
    # the next-nearest pair takes an excluded one's place within parity_strikes
    two = ParityForward({**PARITY, "parity_strikes": 2})
    assert two.pairs(rows, 100.0, exclude_strikes=(100,)) == pytest.approx([100.2, 99.8])


def test_exclusion_leaving_too_few_pairs_falls_back_to_spot():
    pf = ParityForward(PARITY)  # min_pairs 2
    rows = de_pair_rows({100: 100.0, 101: 100.4})
    assert pf.from_chain(rows, 100.3)[1] == "parity"
    assert pf.from_chain(rows, 100.3, exclude_strikes=(100,)) == (100.3, "spot")
    assert pf.pairs(rows, 100.3, exclude_strikes=(100, 101)) == []


def test_pairs_shares_the_spot_and_duplicate_refusals():
    pf = ParityForward(PARITY)
    rows = de_pair_rows(DE_FIVE)
    with pytest.raises(ValueError, match="spot"):
        pf.pairs(rows, 0.0)
    with pytest.raises(ValueError, match="duplicate_quote"):
        pf.pairs(rows + rows[:1], 100.0)
    with pytest.raises(ValueError, match="duplicate_quote"):
        pf.from_chain(rows + rows[:1], 100.0, exclude_strikes=(100,))


def test_from_chain_default_matches_the_pre_pairs_rule_on_noisy_chains():
    rows = chain(101.5, [96, 98, 100, 102, 104, 106])
    for r in rows:
        r["bid"] += 0.01 * r["strike"] % 0.07
    for n in (1, 2, 3, 6):
        pf = ParityForward({**PARITY, "parity_strikes": n, "min_pairs": 1})
        fwd, src = pf.from_chain(rows, 100.3)
        assert (fwd, src) == (statistics.median(pf.pairs(rows, 100.3)), "parity")


DE_GRID = [98.0, 100.0, 102.0, 104.0]
DE_MASSES = [0.1, 0.3, 0.4, 0.2]
DE_DAY, DE_EXPIRY = "2024-03-01", "2024-03-31"
DE_ITEM = {"right": "call", "strike": 100.0, "sign": 1, "expiry": DE_EXPIRY}
DE_QUOTE = {"bid": 2.0, "ask": 2.2, "iv": 0.25}


def de_norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def de_forward_delta(right, f, k, vol, years):
    d1 = (math.log(f / k) + 0.5 * vol * vol * years) / (vol * math.sqrt(years))
    return de_norm_cdf(d1) if right == "call" else de_norm_cdf(d1) - 1


def de_edge(item=DE_ITEM, quote=DE_QUOTE, rows=None, **kw):
    rows = chain(101.0, [96, 98, 100, 102, 104, 106], vol=0.25) if rows is None else rows
    de = DirectionEdge(DE_DIRECTION)
    m_f = de.forecast_mean(DE_MASSES, DE_GRID)
    return de.of(item, quote, rows, 100.5, m_f, DE_DAY, **kw)


def test_forecast_mean_by_hand():
    de = DirectionEdge(DE_DIRECTION)
    assert de.forecast_mean(DE_MASSES, DE_GRID) == pytest.approx(101.4, abs=1e-12)
    with pytest.raises(ValueError, match="same length"):
        de.forecast_mean([1.0], [1.0, 2.0])
    with pytest.raises(ValueError, match="finite"):
        de.forecast_mean([float("nan")], [1.0])


def test_e_dir_hand_worked_example():
    out, code = de_edge()
    assert code is None
    delta = de_forward_delta("call", 101.0, 100.0, 0.25, 30 / 365)
    assert out["forward"] == pytest.approx(101.0, abs=1e-9) and out["forward_source"] == "parity"
    assert out["delta_f_sh"] == pytest.approx(100 * delta, rel=1e-9)
    assert out["e_dir"] == pytest.approx(100 * delta * (101.4 - 101.0), rel=1e-9)
    assert out["sigma2_usd"] == pytest.approx((100 * delta * out["forward_se"]) ** 2, rel=1e-12)


@pytest.mark.parametrize("right,sign", [("call", 1), ("call", -1), ("put", 1), ("put", -1)])
def test_e_dir_sign_follows_side_and_right(right, sign):
    item = {**DE_ITEM, "right": right, "sign": sign}
    out, _ = de_edge(item)
    delta = de_forward_delta(right, 101.0, 100.0, 0.25, 30 / 365)
    assert out["e_dir"] == pytest.approx(sign * 100 * delta * 0.4, rel=1e-9)
    assert (out["e_dir"] > 0) == (sign * (1 if right == "call" else -1) > 0)
    flipped, _ = de_edge({**item, "sign": -sign})
    assert flipped["e_dir"] == pytest.approx(-out["e_dir"], rel=1e-12)


def test_rate_must_be_zero():
    with pytest.raises(ConfigError, match="direction_rate_nonzero"):
        DirectionEdge({**DE_DIRECTION, "rate": 0.01})
    assert DirectionEdge.validate_params({**DE_DIRECTION, "rate": 0.0}) == []


def test_forward_se_from_pair_dispersion_by_hand():
    out, _ = de_edge(rows=de_pair_rows(DE_FIVE))
    # median 100, MAD 0.2, five pairs
    assert out["forward"] == pytest.approx(100.0)
    assert out["forward_se"] == pytest.approx(1.4826 * 0.2 / math.sqrt(5), rel=1e-9)
    assert out["forward_se_fallback"] is False


def test_forward_se_floor_binds_on_a_tight_chain():
    out, _ = de_edge(rows=de_pair_rows({100: 100.0, 99: 100.0, 101: 100.0}))
    assert out["forward_se"] == pytest.approx(0.001 * 100.0, rel=1e-9)
    assert out["forward_se_fallback"] is False


def test_forward_se_with_two_pairs_uses_the_name_week_se_or_the_floor():
    rows = de_pair_rows({100: 100.0, 101: 100.4})
    with_week, _ = de_edge(rows=rows, name_week_se=0.5)
    assert with_week["forward_se"] == pytest.approx(0.5) and with_week["forward_se_fallback"]
    floor_only, _ = de_edge(rows=rows)
    assert floor_only["forward_se"] == pytest.approx(0.001 * 100.2, rel=1e-9)
    assert floor_only["forward_se_fallback"] is True
    tiny, _ = de_edge(rows=rows, name_week_se=1e-9)
    assert tiny["forward_se"] == pytest.approx(0.001 * 100.2, rel=1e-9)


def test_spot_fallback_keeps_the_row_and_flags_it():
    out, code = de_edge(rows=[])
    assert code is None and out["forward_source"] == "spot" and out["forward"] == 100.5
    assert out["forward_se"] is None and out["sigma2_usd"] is None
    delta = de_forward_delta("call", 100.5, 100.0, 0.25, 30 / 365)
    assert out["e_dir"] == pytest.approx(100 * delta * (101.4 - 100.5), rel=1e-9)


@pytest.mark.parametrize("kw,code", [
    ({"quote": None}, "no_direction_edge:no_quote"),
    ({"quote": {"bid": 2.2, "ask": 2.0, "iv": 0.25}}, "no_direction_edge:no_quote"),
    ({"quote": {"bid": 2.0, "ask": 2.2}}, "no_direction_edge:leg_no_iv"),
    ({"item": {**DE_ITEM, "expiry": DE_DAY}}, "no_direction_edge:leg_below_min_years"),
    ({"rows": de_pair_rows(DE_FIVE) + de_pair_rows(DE_FIVE)[:1]}, "no_direction_edge:duplicate_quote"),
])
def test_no_direction_edge_codes(kw, code):
    out, got = de_edge(**kw)
    assert out is None and got == code and got.startswith(DirectionEdge.NO_DIRECTION_EDGE)


def test_direction_edge_malformed_input_raises():
    de = DirectionEdge(DE_DIRECTION)
    with pytest.raises(ValueError, match="spot"):
        de.of(DE_ITEM, DE_QUOTE, [], 0.0, 101.0, DE_DAY)
    with pytest.raises(ValueError, match="forecast_mean"):
        de.of(DE_ITEM, DE_QUOTE, [], 100.5, float("nan"), DE_DAY)
    with pytest.raises(ValueError, match="malformed"):
        de.of({**DE_ITEM, "sign": 2}, DE_QUOTE, [], 100.5, 101.0, DE_DAY)


def test_direction_edge_has_no_fill_argument_and_reads_only_the_decision_day():
    names = list(inspect.signature(DirectionEdge.of).parameters)
    assert names == ["self", "item", "quote", "usable_rows", "spot", "forecast_mean", "day",
                     "name_week_se"]
    assert not any("fill" in n for n in names)
    base, _ = de_edge()
    # a chain row is only ever read through the rows handed in: poison nothing else
    assert de_edge()[0] == base


def test_direction_edge_params_compose_and_export():
    assert DirectionEdge._PARAMS == (
        LegGreeks._PARAMS + ParityForward._PARAMS + ("forward_se_floor_frac",))
    assert "DirectionEdge" in ox.__all__ and len(set(ox.__all__)) == len(ox.__all__)
    DirectionEdge(DE_DIRECTION)
    assert any("unknown" in p for p in DirectionEdge.validate_params({**DE_DIRECTION, "typo": 1}))
    for key in ("rate", "multiplier", "min_years", "vol_floor", "parity_strikes", "min_pairs",
                "max_pair_spread_frac", "forward_se_floor_frac"):
        trimmed = {k: v for k, v in DE_DIRECTION.items() if k != key}
        assert any("missing" in p for p in DirectionEdge.validate_params(trimmed)), key
    for bad in (0, -1.0, "x", float("nan"), True):
        assert DirectionEdge.validate_params({**DE_DIRECTION, "forward_se_floor_frac": bad}), bad
    assert DirectionEdge.validate_params({**DE_DIRECTION, "parity_strikes": 0})
    assert DirectionEdge.validate_params({**DE_DIRECTION, "iv_source": "mid"})  # tol required


# ---- ExposureLimits (ADR-0313 I6 core) -------------------------------------------------
LIMITS = {"beta_delta_fraction": 0.5, "vega_fraction_per_pt": 0.02, "dte_edges": [8, 22, 60],
          "beta_lookback": 60, "scope": "account"}


def xgroup(gid, delta, vega=0.0, units=0.0, dte=10, symbol="A", forward=100.0):
    """One candidate group with a single item (delta_f_sh and vega already signed)."""
    return {"id": gid, "symbol": symbol, "dte": dte, "forward": forward,
            "items": [{"delta_f_sh": delta, "vega_usd_per_pt": vega, "units": units}]}


def test_exposure_limits_short_call_counts_one_minus_h_not_one_plus_h():
    # short call, forward delta 0.35 x 100 shares: delta_f_sh = -35; beta 1.2, F0 100
    el = ox.ExposureLimits(LIMITS)
    betas = {"A": 1.2}
    k = lambda u: el.coefficients([xgroup("g", -35.0, units=u)], betas)["delta"]["g"][0]
    assert k(0.0) == pytest.approx(-35 * 1.2 * 100)
    assert k(35.0) == pytest.approx(0.0, abs=1e-9)           # h = 1: fully hedged
    assert k(17.5) == pytest.approx(-(1 - 0.5) * 35 * 1.2 * 100)  # h = 0.5, never (1+h)


def test_exposure_limits_long_put_is_negative_and_a_hedged_item_adds_its_units():
    el = ox.ExposureLimits(LIMITS)
    betas = {"A": 0.8}
    put = el.coefficients([xgroup("p", -40.0)], betas)["delta"]["p"][0]
    assert put == pytest.approx(-40 * 0.8 * 100)
    hedged = el.coefficients([xgroup("p", -40.0, units=20.0)], betas)["delta"]["p"][0]
    assert hedged == pytest.approx((-40 + 20) * 0.8 * 100)


def test_exposure_limits_vega_buckets_are_half_open_and_match_the_snapshot():
    el = ox.ExposureLimits(LIMITS)
    snap = ExposureSnapshot({"dte_edges": LIMITS["dte_edges"], "top_k": 1})
    groups = [xgroup("lo", 1.0, vega=-5.0, dte=8), xgroup("mid", 1.0, vega=7.0, dte=21.9),
              xgroup("edge", 1.0, vega=3.0, dte=22), xgroup("out", 1.0, vega=9.0, dte=7.9)]
    out = el.coefficients(groups, {"A": 1.0})
    assert out["bucket"] == {"lo": 0, "mid": 0, "edge": 1, "out": None}
    for g in groups:
        books = [{"symbol": "A", "dte": g["dte"], "spot": 100.0, "delta_sh": 0.0,
                  "gamma_sh": 0.0, "vega_usd_per_pt": 1.0, "status": "ok"}]
        placed = [i for i, b in enumerate(snap.of({"A": 1.0}, books)["buckets"]) if b["n_books"]]
        assert (placed or [None])[0] == out["bucket"][g["id"]]
    assert out["vega"]["lo"] == [-5.0]                          # a sold option is negative
    assert out["excluded"] == {"dte_outside_edges": 1}


def test_exposure_limits_rows_are_two_rows_per_limit_and_per_bucket():
    el = ox.ExposureLimits(LIMITS)
    groups = [xgroup("a", -35.0, vega=-5.0, dte=10), xgroup("b", 20.0, vega=4.0, dte=30)]
    rows = el.rows(groups, {"A": 1.0}, funding=10_000.0)
    assert [r["id"] for r in rows] == [
        "exposure:delta:+", "exposure:delta:-", "exposure:vega:8_22:+", "exposure:vega:8_22:-",
        "exposure:vega:22_60:+", "exposure:vega:22_60:-"]
    assert all(set(r) == {"id", "terms", "upper"} for r in rows)
    up, dn = rows[0], rows[1]
    assert up["terms"] == {"a": [-3500.0], "b": [2000.0]}
    assert dn["terms"] == {"a": [3500.0], "b": [-2000.0]}
    assert up["upper"] == dn["upper"] == pytest.approx(0.5 * 10_000.0)
    assert rows[2]["terms"] == {"a": [-5.0]} and rows[3]["terms"] == {"a": [5.0]}
    assert rows[4]["terms"] == {"b": [4.0]}
    assert rows[2]["upper"] == pytest.approx(0.02 * 10_000.0)


def test_exposure_limits_rows_encode_the_absolute_value_exactly():
    el = ox.ExposureLimits(LIMITS)
    rows = el.rows([xgroup("a", -35.0)], {"A": 1.0}, funding=1_000.0)
    up, dn = rows[0], rows[1]
    for x, ok in ((0.1, True), (0.2, False), (-0.1, True), (-0.2, False)):
        value = lambda r: sum(c * x for c in r["terms"]["a"])
        assert (value(up) <= up["upper"] and value(dn) <= dn["upper"]) is ok


def test_exposure_limits_coefficients_sum_to_the_snapshots_beta_delta():
    leg = LegGreeks({**LEG, "iv_source": "vendor"})
    quote = {"bid": 1.0, "ask": 1.2, "iv": 0.25}
    spot, forward, betas = 100.0, 101.0, {"A": 1.3, "B": 0.7}
    legs = [("A", "call", -1, 3), ("B", "put", 1, 2)]
    groups, books = [], []
    for n, (sym, right, sign, qty) in enumerate(legs):
        one, _ = leg.of({"right": right, "strike": 100.0, "sign": sign, "quantity": 1,
                         "expiry": "2020-02-14"}, quote, forward, spot, "2020-01-15")
        full, _ = leg.of({"right": right, "strike": 100.0, "sign": sign, "quantity": qty,
                          "expiry": "2020-02-14"}, quote, forward, spot, "2020-01-15")
        groups.append({"id": f"g{n}", "symbol": sym, "dte": 30, "forward": forward,
                       "items": [{"delta_f_sh": one["delta_f_sh"],
                                  "vega_usd_per_pt": one["vega_usd_per_pt"]}]})
        books.append({"symbol": sym, "dte": 30, "spot": spot, "delta_sh": full["delta_sh"],
                      "gamma_sh": full["gamma_sh"], "vega_usd_per_pt": full["vega_usd_per_pt"],
                      "status": "ok"})
    snap = ExposureSnapshot({"dte_edges": [8, 60], "top_k": 1}).of(betas, books)["total"]
    out = ox.ExposureLimits(LIMITS).coefficients(groups, betas)
    qty = [3, 2]
    assert sum(out["delta"][f"g{n}"][0] * q for n, q in enumerate(qty)) == pytest.approx(
        snap["beta_delta_usd"])
    assert sum(out["vega"][f"g{n}"][0] * q for n, q in enumerate(qty)) == pytest.approx(
        snap["vega_usd_per_pt"])


def test_exposure_limits_held_exposure_moves_the_bounds():
    el = ox.ExposureLimits(LIMITS)
    held = {"beta_delta_usd": 1_000.0, "vega_usd_per_pt": [-50.0, 20.0]}
    rows = {r["id"]: r for r in el.rows([xgroup("a", 1.0, dte=10), xgroup("b", 1.0, dte=30)],
                                        {"A": 1.0}, funding=10_000.0, held=held)}
    assert rows["exposure:delta:+"]["upper"] == pytest.approx(5_000.0 - 1_000.0)
    assert rows["exposure:delta:-"]["upper"] == pytest.approx(5_000.0 + 1_000.0)
    assert rows["exposure:vega:8_22:+"]["upper"] == pytest.approx(200.0 + 50.0)
    assert rows["exposure:vega:8_22:-"]["upper"] == pytest.approx(200.0 - 50.0)
    assert rows["exposure:vega:22_60:+"]["upper"] == pytest.approx(180.0)


def test_exposure_limits_held_at_exactly_the_limit_gives_a_zero_bound_not_negative():
    el = ox.ExposureLimits(LIMITS)
    rows = el.rows([xgroup("a", 1.0)], {"A": 1.0}, funding=10_000.0,
                   held={"beta_delta_usd": 5_000.0, "vega_usd_per_pt": [0.0, 0.0]})
    assert rows[0]["upper"] == 0.0 and rows[1]["upper"] == pytest.approx(10_000.0)


@pytest.mark.parametrize("held", [
    {"beta_delta_usd": 5_000.01, "vega_usd_per_pt": [0.0, 0.0]},
    {"beta_delta_usd": -5_001.0, "vega_usd_per_pt": [0.0, 0.0]},
    {"beta_delta_usd": 0.0, "vega_usd_per_pt": [200.5, 0.0]},
    {"beta_delta_usd": 0.0, "vega_usd_per_pt": [0.0, -200.5]}])
def test_exposure_limits_a_held_breach_refuses_with_its_code(held):
    with pytest.raises(ValueError, match="^exposure_held_breach"):
        ox.ExposureLimits(LIMITS).rows([xgroup("a", 1.0)], {"A": 1.0}, 10_000.0, held=held)


@pytest.mark.parametrize("held", [
    {"beta_delta_usd": float("nan"), "vega_usd_per_pt": [0.0, 0.0]},
    {"beta_delta_usd": 0.0, "vega_usd_per_pt": [0.0]},
    {"beta_delta_usd": 0.0},
    "x"])
def test_exposure_limits_unreadable_held_greeks_refuse_as_risk_input(held):
    with pytest.raises(ValueError, match="^exposure_risk_input"):
        ox.ExposureLimits(LIMITS).rows([xgroup("a", 1.0)], {"A": 1.0}, 10_000.0, held=held)


def test_exposure_limits_bad_candidate_inputs_refuse_as_risk_input():
    el = ox.ExposureLimits(LIMITS)
    for bad in (xgroup("a", float("nan")), xgroup("a", 1.0, vega=float("inf")),
                xgroup("a", 1.0, forward=0.0), xgroup("a", 1.0, symbol="Z")):
        with pytest.raises(ValueError, match="^exposure_risk_input"):
            el.coefficients([bad], {"A": 1.0})
    with pytest.raises(ValueError, match="^exposure_risk_input"):
        el.rows([xgroup("a", 1.0)], {"A": 1.0}, funding=float("nan"))


def test_exposure_limits_nonpositive_funding_gives_zero_limits_and_a_bucket_without_groups_has_no_row():
    rows = ox.ExposureLimits(LIMITS).rows([xgroup("a", 1.0, dte=10)], {"A": 1.0}, funding=-5.0)
    assert [r["id"] for r in rows if "22_60" in r["id"]] == []
    assert all(r["upper"] == 0.0 for r in rows)


def test_exposure_limits_refuses_a_kelly_method():
    ox.ExposureLimits.check_method({"id": "m", "exposure_limits": LIMITS})
    with pytest.raises(ConfigError, match="exposure_limits_with_kelly"):
        ox.ExposureLimits.check_method({"id": "m", "kelly": {}, "exposure_limits": LIMITS})


def test_exposure_limits_params_are_the_declared_tuple_and_default_deny():
    assert ox.ExposureLimits._PARAMS == (
        "beta_delta_fraction", "vega_fraction_per_pt", "dte_edges", "beta_lookback", "scope")
    ox.ExposureLimits(LIMITS)
    bad = [{**LIMITS, "extra": 1}, {**LIMITS, "beta_delta_fraction": 0},
           {**LIMITS, "beta_delta_fraction": True}, {**LIMITS, "vega_fraction_per_pt": -1},
           {**LIMITS, "vega_fraction_per_pt": float("nan")}, {**LIMITS, "dte_edges": [8]},
           {**LIMITS, "dte_edges": [22, 8]}, {**LIMITS, "beta_lookback": 0},
           {**LIMITS, "beta_lookback": 1.5}, {**LIMITS, "scope": "book"}, [], "x"]
    for params in bad:
        assert ox.ExposureLimits.validate_params(params), params
    for name in ox.ExposureLimits._PARAMS:
        assert ox.ExposureLimits.validate_params({k: v for k, v in LIMITS.items() if k != name})


def test_exposure_limits_held_delta_usd_counts_outside_edge_books_against_the_delta_cap():
    # a held book below the first edge (dte < 8) is in no vega bucket but is still delta
    held = {"beta_delta_usd": 0.0, "vega_usd_per_pt": [0.0, 0.0]}
    rows = ox.ExposureLimits(LIMITS).rows(
        [xgroup("a", 1.0)], {"A": 1.0}, 10_000.0, held=held, held_delta_usd=4_000.0)
    upper = {r["id"]: r["upper"] for r in rows}
    assert upper["exposure:delta:+"] == pytest.approx(1_000.0)
    assert upper["exposure:delta:-"] == pytest.approx(9_000.0)
    with pytest.raises(ValueError, match="^exposure_held_breach"):
        ox.ExposureLimits(LIMITS).rows([xgroup("a", 1.0)], {"A": 1.0}, 10_000.0, held=held,
                                       held_delta_usd=5_000.5)
    with pytest.raises(ValueError, match="^exposure_risk_input"):
        ox.ExposureLimits(LIMITS).rows([xgroup("a", 1.0)], {"A": 1.0}, 10_000.0, held=held,
                                       held_delta_usd=float("nan"))


def test_exposure_limits_no_groups_emit_no_rows():
    assert ox.ExposureLimits(LIMITS).rows([], {"A": 1.0}, 10_000.0) == []
    assert ox.ExposureLimits(LIMITS).rows(
        [{"id": "g", "symbol": "A", "dte": 10, "forward": 100.0, "items": []}],
        {"A": 1.0}, 10_000.0) == []
