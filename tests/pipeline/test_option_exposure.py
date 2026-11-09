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
        ["ParityForward", "LegGreeks", "BookGreeks", "StockEquivalent", "CellAttribution",
         "ExposureSnapshot", "fit_betas", "BookGreeksReport", "StaticDeltaHedge"])


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
    a = {"delta_sh": 10.0, "gamma_sh": 1.0, "vega_usd_per_pt": 5.0, "theta_usd_per_day": -2.0}
    b = {"delta_sh": -4.0, "gamma_sh": 0.5, "vega_usd_per_pt": -1.0, "theta_usd_per_day": 1.0}
    book = BookGreeks.sum([a, b], 101.0, "parity")
    assert (book.delta_sh, book.gamma_sh, book.vega_usd_per_pt, book.theta_usd_per_day) == \
        (6.0, 1.5, 4.0, -1.0)
    assert (book.forward, book.forward_source, book.status) == (101.0, "parity", "ok")
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
        return {"symbol": sym, "rank": rank, "pnl": pnl, "stock_pnl": stock,
                "residual": pnl - stock}
    return [r("A", 1, 100.0, 40.0), r("A", 1, -30.0, -10.0), r("A", 2, 50.0, 80.0),
            r("B", 1, 60.0, 10.0), r("B", 1, -60.0, -20.0)]


def test_cell_sums_add_to_the_total_and_shares_are_exact():
    ca = CellAttribution({"cell_by": ["symbol", "rank"], "min_total_usd": 1.0})
    cells = ca.summarize(rows_for_cells())
    assert [(c["symbol"], c["rank"]) for c in cells] == [("A", 1), ("A", 2), ("B", 1)]
    rows = rows_for_cells()
    assert sum(c["pnl_usd"] for c in cells) == pytest.approx(sum(r["pnl"] for r in rows))
    assert sum(c["stock_pnl_usd"] + c["residual_usd"] for c in cells) == \
        pytest.approx(sum(r["pnl"] for r in rows))
    a1 = cells[0]
    assert (a1["n"], a1["pnl_usd"], a1["stock_pnl_usd"], a1["residual_usd"]) == (2, 70.0, 30.0, 40.0)
    assert a1["stock_share_net"] == pytest.approx(30 / 70)
    assert a1["gross_pnl_usd"] == 130.0
    assert a1["stock_share_gross"] == pytest.approx(30 / 130)
    assert a1["residual_share_gross"] == pytest.approx(40 / 130)


def test_net_share_is_null_below_min_total_but_gross_share_is_kept():
    ca = CellAttribution({"cell_by": ["symbol", "rank"], "min_total_usd": 1.0})
    b1 = ca.summarize(rows_for_cells())[2]                 # total exactly 0
    assert b1["pnl_usd"] == 0.0
    assert b1["stock_share_net"] is None and b1["residual_share_net"] is None
    assert b1["gross_pnl_usd"] == 120.0 and b1["stock_share_gross"] == pytest.approx(-10 / 120)
    high = CellAttribution({"cell_by": ["symbol", "rank"], "min_total_usd": 100.0})
    assert high.summarize(rows_for_cells())[0]["stock_share_net"] is None
    assert CellAttribution({"cell_by": ["symbol"], "min_total_usd": 0.0}).summarize(
        [{"symbol": "Z", "pnl": 0.0, "stock_pnl": 0.0, "residual": 0.0}])[0][
        "stock_share_gross"] is None


def test_cell_attribution_refusals():
    ca = CellAttribution({"cell_by": ["symbol"], "min_total_usd": 1.0})
    with pytest.raises(ValueError, match="^cell_field_missing"):
        ca.summarize([{"pnl": 1.0, "stock_pnl": 0.0, "residual": 1.0}])
    with pytest.raises(ValueError, match="^pnl_not_finite"):
        ca.summarize([{"symbol": "A", "pnl": float("nan"), "stock_pnl": 0.0, "residual": 0.0}])
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
    assert row["stock_pnl"] == pytest.approx(delta * 4.0, rel=1e-12)
    assert row["stock_pnl"] + row["residual"] == pytest.approx(150.0, abs=1e-12)
    assert row["basis_usd"] == pytest.approx(delta * (SPOT - FORWARD), rel=1e-12)
    assert row["gamma_sh"] > 0 and row["vega_usd_per_pt"] > 0 and row["theta_usd_per_day"] < 0
    assert (row["symbol"], row["tag"], row["id"]) == ("AAA", "t", "b1")


def test_report_cells_sum_to_the_attributed_total_and_summary_counts():
    books = [make_book("b1", pnl=150.0), make_book("b2", pnl=-40.0)]
    out = run_report(books)
    (cell,) = out["cells"]
    assert cell["n"] == 2 and cell["pnl_usd"] == pytest.approx(110.0)
    assert cell["stock_pnl_usd"] + cell["residual_usd"] == pytest.approx(110.0)
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
    assert by_id["bad"]["stock_pnl"] is None and by_id["bad"]["pnl"] == 77.0
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
    assert row["stock_pnl"] is not None
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
    assert row["hedged_pnl"] == pytest.approx(want, rel=1e-12)
    summary = out["summary"]
    assert summary["hedge_status"] == {"dividends_absent": 1}
    assert summary["hedge_dividend_source"] == "absent"
    assert summary["hedge_params"] == HEDGE
    assert summary["hedged_pnl_usd"] == pytest.approx(want, rel=1e-12)


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
    assert out["summary"]["hedged_pnl_usd"] is None


def test_report_unattributed_book_has_null_hedge_columns_and_not_attributed_status():
    out = run_report([make_book(pnl=7.0, factor=2.0, end_factor=1.0)])
    (row,) = out["books"]
    assert row["status"] == "basis_changed" and row["hedge_status"] == "not_attributed"
    assert all(row[k] is None for k in ("hedge_sh", "hedge_days", "stock_costs", "short_borrow",
                                       "dividend_adjust", "hedged_pnl"))
    assert out["summary"]["hedge_status"] == {"not_attributed": 1}


def test_report_cell_hedged_sum_adds_up_and_is_null_when_any_row_is_null():
    books = [make_book("b1", pnl=150.0), make_book("b2", pnl=-40.0)]
    out = run_report(books)
    (cell,) = out["cells"]
    rows = out["books"]
    assert cell["n_hedged"] == 2
    assert cell["hedged_pnl_usd"] == pytest.approx(sum(r["hedged_pnl"] for r in rows))
    ca = CellAttribution({"cell_by": ["symbol"], "min_total_usd": 1.0})
    base = {"symbol": "A", "pnl": 1.0, "stock_pnl": 0.5, "residual": 0.5}
    (cell,) = ca.summarize([{**base, "hedged_pnl": 0.25}, {**base, "hedged_pnl": None}])
    assert cell["hedged_pnl_usd"] is None and cell["n_hedged"] == 1 and cell["n"] == 2
    (cell,) = ca.summarize([{**base, "hedged_pnl": 0.25}, {**base, "hedged_pnl": 0.5}])
    assert cell["hedged_pnl_usd"] == 0.75 and cell["n_hedged"] == 2
    with pytest.raises(ValueError, match="pnl_not_finite"):
        ca.summarize([{**base, "hedged_pnl": float("nan")}])


def test_report_declares_the_hedge_columns():
    names = ("hedge_sh", "hedge_days", "stock_costs", "short_borrow", "dividend_adjust",
             "hedged_pnl", "hedge_status")
    assert set(names) <= set(BookGreeksReport.BOOK_COLUMNS)
    assert {"hedged_pnl_usd", "n_hedged"} <= set(BookGreeksReport.CELL_COLUMNS)
    out = run_report([make_book()])
    assert set(BookGreeksReport.BOOK_COLUMNS) <= set(out["books"][0])
    assert BookGreeksReport.HEDGE_STATUSES == (
        "ok", "dividends_absent", "dividend_missing", "not_attributed")
