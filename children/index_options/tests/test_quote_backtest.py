"""ADR-0187: ``CondorQuoteBacktest`` over archived end-of-day quotes.

Every scenario is scripted: forecast rows whose draws have hand-known
quantiles, one chain per date with explicit (bid, ask, sizes) per strike,
and an underlying series with closes and ex-dates. Expected strikes,
credits and P&L are restated by hand in the assertions.
"""

import copy
import hashlib
import itertools
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path
from statistics import NormalDist
from types import SimpleNamespace

import pytest

from dskit.pipeline.base import ConfigError

from index_options import nodes
from index_options.contracts import american_short_charge
from index_options.nodes import (
    CondorBacktest,
    CondorQuoteBacktest,
    PayoffSelectQuoteBacktest,
    PutSpreadQuoteBacktest,
)

#: Draws whose 10%/90% quantiles are exactly -1 and +1.
DRAWS = [-1.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0]
Z10 = NormalDist().inv_cdf(0.1)

PARAMS = {"split": "val", "short_q": 0.1, "wing_z": 0.5, "multiplier": 100,
          "fee_per_leg": 0.65, "dte_min": 5, "dte_max": 10, "max_abs_log_moneyness": 0.2,
          "label_horizon": 5, "carry_rate": 0.055}


class _Split:
    def split_of(self, frame):
        return "val" if frame.asof_ms >= 100 else "train"


CTX = SimpleNamespace(splits=_Split())


def _ms(day):
    """A monotone integer stamp per date: val rows sit at or above 100."""
    return 100 + (date.fromisoformat(day) - date(2024, 3, 1)).days


def _row(day, close=100.0, outcome_z=0.0, instrument="SPY", **extra):
    return {"asof_ms": _ms(day), "instrument": instrument, "date": day, "close": close,
            "reference_scale": 0.05, "samples": list(DRAWS), "outcome": outcome_z, **extra}


def _weekdays(start, end):
    """Weekdays from ``start`` to ``end`` inclusive, ISO."""
    out, day = [], date.fromisoformat(start)
    while day <= date.fromisoformat(end):
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


#: Quotes by (right, strike) for the worked chain; everything else is (0.5, 0.6).
QUOTES = {("put", 95.0): (2.0, 2.2), ("put", 92.0): (1.0, 1.1), ("call", 106.0): (1.5, 1.6),
          ("call", 108.0): (0.7, 0.8), ("put", 96.0): (2.4, 2.6), ("call", 104.0): (2.1, 2.3)}


def _quote(day, expiry, right, strike, close=100.0, instrument="SPY", iv=0.2, bid=None,
           ask=None, bid_size=10, ask_size=10, settle=None):
    b, a = QUOTES.get((right, strike), (0.5, 0.6))
    settle_day = settle or expiry
    return {"instrument": instrument, "date": day, "expiry": expiry,
            "settle_date": settle_day,
            "dte": (date.fromisoformat(settle_day) - date.fromisoformat(day)).days,
            "right": right, "strike": strike, "bid": b if bid is None else bid,
            "ask": a if ask is None else ask, "bid_size": bid_size, "ask_size": ask_size,
            "iv": iv, "underlying_price": close, "asof_ms": _ms(day)}


def _chain(day, expiry, close=100.0, strikes=range(88, 113), **over):
    return [_quote(day, expiry, right, float(k), close, **over)
            for k in strikes for right in ("put", "call")]


def _series(closes, dividends=()):
    paid = dict(dividends)
    return [{"instrument": "SPY", "date": d, "close": c, "asof_ms": _ms(d),
             "dividend_amount": paid.get(d, 0.0)} for d, c in closes]


#: The condor's legs and the put spread's, restated here (never read from the node).
CONDOR = (("put", 1), ("put", -1), ("call", -1), ("call", 1))
PUT_SPREAD = CONDOR[:2]


def _assert_the_wing_split(sides, ledger):
    """ADR-0193 on EVERY entry of EVERY run: an entered cell's sides sum to its P&L
    within 1e-9 and name exactly the structure's sides; a cell that did not enter has
    neither a split nor a benchmark."""
    for entry in ledger:
        for book, cell in entry["books"].items():
            if cell["entered"]:
                assert tuple(cell["side_pnl_usd"]) == sides, (entry["date"], book)
                assert abs(sum(cell["side_pnl_usd"].values()) - cell["pnl_usd"]) <= 1e-9, (
                    entry["date"], book)
            else:
                assert cell["side_pnl_usd"] is None, (entry["date"], book)
                assert cell["delta"] is None and cell["equity_pnl_usd"] is None


def _run_node(node_cls, sides, forecasts, chain, underlying, **over):
    node = node_cls("bt", {**PARAMS, **over})
    out = node.run(CTX, {"forecasts": forecasts, "chain": chain, "underlying": underlying})
    _assert_the_wing_split(sides, out["report"].value["ledger"])
    return out["metrics"], out["report"].value


def _run(forecasts, chain, underlying, **over):
    return _run_node(CondorQuoteBacktest, ("put", "call"), forecasts, chain, underlying, **over)


def _run_put_spread(forecasts, chain, underlying, **over):
    return _run_node(PutSpreadQuoteBacktest, ("put",), forecasts, chain, underlying,
                     **over)


# -- the worked entry ---------------------------------------------------------------------

#: Entry Friday 2024-03-01 at 100, expiry Friday 2024-03-08 (DTE 7, five sessions).
ENTRY, EXPIRY = "2024-03-01", "2024-03-08"
SESSIONS = _weekdays("2024-03-01", "2024-03-12")
FLAT = _series([(d, 100.0) for d in SESSIONS[:-3]] + [("2024-03-08", 97.0), ("2024-03-11", 98.0),
                                                        ("2024-03-12", 99.0)])
MODEL_STRIKES = [92.0, 95.0, 106.0, 108.0]
MODEL_CREDIT = (2.0 + 1.5 - 1.1 - 0.8) * 100 - 4 * 0.65  # shorts at the bid, wings at the ask
IMPLIED_STRIKES = [95.0, 96.0, 104.0, 106.0]
IMPLIED_CREDIT = (2.4 + 2.1 - 2.2 - 1.6) * 100 - 4 * 0.65


def test_one_entry_hand_checked():
    forecasts = [_row(ENTRY), _row("2024-02-20", instrument="SPY")]  # the second is train
    metrics, report = _run(forecasts, _chain(ENTRY, EXPIRY), FLAT)
    assert report["kind"] == "archived_quote_condor_backtest"
    assert report["pricing"] == "archived_eod_quotes" and report["decision_eligible"] is False
    assert report["chain"] == {"first_date": ENTRY, "last_date": ENTRY, "n_rows": 50}
    (entry,) = report["ledger"]
    assert (entry["date"], entry["expiry"], entry["settle_date"], entry["dte"],
            entry["sessions"]) == (ENTRY, EXPIRY, EXPIRY, 7, 5)
    assert entry["horizon_scale"] == pytest.approx(0.05)  # sqrt(5 / 5)
    assert entry["settlement"] == 97.0 and entry["settlement_date"] == EXPIRY
    assert entry["atm_iv"] == pytest.approx(0.2)
    books = entry["books"]
    # targets 95.12 / 92.77 / 105.13 / 107.79 snap OUTWARD onto the 1-point grid
    assert books["model"]["strikes"] == MODEL_STRIKES == books["always"]["strikes"]
    assert books["model"]["credit_usd"] == pytest.approx(MODEL_CREDIT)
    # implied: s = 0.2 sqrt(7/365); ln(K/S) = -s^2/2 +- s z_q, wings 0.5 s further
    s = 0.2 * math.sqrt(7 / 365)
    targets = [100 * math.exp(-s * s / 2 + s * z) for z in (Z10 - 0.5, Z10, -Z10, -Z10 + 0.5)]
    assert [math.floor(targets[0]), math.floor(targets[1]), math.ceil(targets[2]),
            math.ceil(targets[3])] == IMPLIED_STRIKES
    assert books["implied"]["strikes"] == IMPLIED_STRIKES
    assert books["implied"]["credit_usd"] == pytest.approx(IMPLIED_CREDIT)
    # settled at 97: every short stays out of the money, no ex-date, no carry
    for book, credit in (("model", MODEL_CREDIT), ("always", MODEL_CREDIT),
                         ("implied", IMPLIED_CREDIT)):
        assert books[book]["entered"] is True and books[book]["reason"] is None
        assert books[book]["american_charge_usd"] == 0.0
        assert books[book]["pnl_usd"] == pytest.approx(credit)
        assert metrics[f"{book}_n_trades"] == 1
        assert metrics[f"{book}_total_pnl_usd"] == pytest.approx(credit)
        assert metrics[f"{book}_mean_credit_usd"] == pytest.approx(credit)
        assert metrics[f"{book}_american_charge_usd"] == 0.0
    assert entry["model_expected_pnl_usd"] == pytest.approx(MODEL_CREDIT)  # draws settle inside
    assert metrics["n_rows_in_split"] == 1 and metrics["n_entries"] == 1
    assert {k: v for k, v in metrics.items() if k.startswith("n_skipped_")} == {
        f"n_skipped_{r}": 0 for r in CondorQuoteBacktest.ROW_REASONS}
    assert set(CondorQuoteBacktest.ROW_REASONS) == {
        "no_outcome", "no_forecast", "no_forward", "no_scale", "no_chain", "unsettled"}
    assert report["params"]["label_horizon"] == 5 and report["params"]["cvar_alpha"] == 0.95


def _settled_at(level):
    """The worked series with the settlement close alone moved to ``level``."""
    return _series([(d, 100.0) for d in SESSIONS[:-3]] + [("2024-03-08", level),
                                                            ("2024-03-11", 100.0),
                                                            ("2024-03-12", 100.0)])


@pytest.mark.parametrize("level, model_loss, implied_loss", [
    (80.0, 300.0, 100.0),    # through both put wings: the whole 3- and 1-point spreads
    (93.5, 150.0, 100.0),    # between the model's short put and its wing: 1.5 points
    (110.0, 200.0, 200.0),   # through both call wings: 2-point spreads on each book
])
def test_a_losing_settlement_is_the_credit_less_the_spread_times_the_multiplier(
    level, model_loss, implied_loss
):
    metrics, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), _settled_at(level))
    books = report["ledger"][0]["books"]
    expected = {"model": MODEL_CREDIT - model_loss, "always": MODEL_CREDIT - model_loss,
                "implied": IMPLIED_CREDIT - implied_loss}
    for book, pnl in expected.items():
        assert books[book]["american_charge_usd"] == 0.0  # no ex-date, never in the money early
        assert books[book]["pnl_usd"] == pytest.approx(pnl)
        assert metrics[f"{book}_total_pnl_usd"] == pytest.approx(pnl)
        assert metrics[f"{book}_cvar_usd"] == pytest.approx(pnl)
        assert metrics[f"{book}_hit_rate"] == (1.0 if pnl > 0 else 0.0)
        assert metrics[f"{book}_max_drawdown_usd"] == pytest.approx(max(-pnl, 0.0))


def test_the_american_charge_is_taken_off_each_book():
    dividends = [("2024-03-07", 1.0)]
    closes = [(d, 100.0) for d in SESSIONS[:3]] + [("2024-03-06", 107.0), ("2024-03-07", 94.0),
                                                     ("2024-03-08", 97.0), ("2024-03-11", 98.0)]
    series = _series(closes, dividends)
    metrics, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), series)
    books = report["ledger"][0]["books"]
    expected = american_short_charge(series, 95.0, 106.0, ENTRY, EXPIRY, 0.055, 100)
    # pre-ex close 107 > 106: the dividend; 94 < 95 on 03-07: one day of carry
    assert expected["call_dividend_usd"] == 100.0 and expected["put_carry_usd"] > 0
    assert books["model"]["american_charge_usd"] == pytest.approx(expected["total_usd"])
    assert books["model"]["pnl_usd"] == pytest.approx(MODEL_CREDIT - expected["total_usd"])
    implied = american_short_charge(series, 96.0, 104.0, ENTRY, EXPIRY, 0.055, 100)
    assert books["implied"]["pnl_usd"] == pytest.approx(IMPLIED_CREDIT - implied["total_usd"])
    assert metrics["model_american_charge_usd"] == pytest.approx(expected["total_usd"])
    # a pre-ex close of 100 sits above the short PUT but below the short CALL: no assignment
    between = _series([(d, 100.0) for d in SESSIONS[:-3]] + [("2024-03-08", 97.0),
                                                              ("2024-03-11", 98.0)], dividends)
    _, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), between)
    assert report["ledger"][0]["books"]["model"]["american_charge_usd"] == 0.0


def test_a_missing_dividend_in_the_window_refuses():
    series = _series([(d, 100.0) for d in SESSIONS])
    series[3]["dividend_amount"] = None
    with pytest.raises(ValueError, match="dividend_amount"):
        _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), series)


# -- expiry, strikes and quotability ------------------------------------------------------


def test_the_nearest_listed_expiry_is_taken_and_a_day_without_chain_is_skipped():
    # the farther 03-11 expiry carries richer quotes and a higher iv, so pricing any of its
    # rows would move the credit (put 95 at 2.4), the ATM iv (0.4) and the implied strikes
    # rows, and it lists two half-strikes (94.5, 105.5) the near expiry lacks
    far = _set(_chain(ENTRY, "2024-03-11", iv=0.4, strikes=[94.5, 105.5] + list(range(88, 113))),
               put95={"bid": 2.4, "ask": 2.6}, call106={"bid": 1.0, "ask": 1.1})
    chain = far + _chain(ENTRY, "2024-03-08")  # the farther expiry's rows come FIRST
    metrics, report = _run([_row(ENTRY), _row("2024-03-04"), _row("2024-03-11")], chain, FLAT)
    assert [e["expiry"] for e in report["ledger"]] == ["2024-03-08"]
    entry = report["ledger"][0]
    assert entry["dte"] == 7 and entry["atm_iv"] == pytest.approx(0.2)
    assert entry["books"]["model"]["credit_usd"] == pytest.approx(MODEL_CREDIT)
    assert entry["books"]["implied"]["strikes"] == IMPLIED_STRIKES
    # 03-04 sits inside the open position (passed over, uncounted); 03-11 has no chain
    assert metrics["n_skipped_no_chain"] == 1 and metrics["n_rows_in_split"] == 3


def test_positions_never_overlap_and_the_next_entry_is_on_or_after_settlement():
    days = ["2024-03-01", "2024-03-04", "2024-03-08", "2024-03-11"]
    chain = (_chain("2024-03-01", "2024-03-08") + _chain("2024-03-04", "2024-03-11")
             + _chain("2024-03-08", "2024-03-15") + _chain("2024-03-11", "2024-03-18"))
    series = _series([(d, 100.0) for d in _weekdays("2024-03-01", "2024-03-20")])
    metrics, report = _run([_row(d) for d in days], chain, series)
    assert [e["date"] for e in report["ledger"]] == ["2024-03-01", "2024-03-08"]
    assert metrics["n_entries"] == 2 and metrics["n_rows_in_split"] == 4


def test_strikes_snap_outward_past_unquotable_legs():
    chain = _chain(ENTRY, EXPIRY)
    for q in chain:
        if q["right"] == "put" and q["strike"] == 95.0:
            q["bid"] = 0.0                      # no market to SELL the short put at 95
        if q["right"] == "call" and q["strike"] == 108.0:
            q["ask"], q["ask_size"] = 0.0, 0    # nothing to BUY at 108
        if q["right"] == "put" and q["strike"] == 92.0:
            q["ask"] = 0.0                      # nothing to BUY the long put at 92 either
        if q["right"] == "put" and q["strike"] == 91.0:
            q["bid_size"] = 0                   # a no-bid wing is still buyable
    _, report = _run([_row(ENTRY)], chain, FLAT)
    books = report["ledger"][0]["books"]
    assert books["model"]["strikes"] == [91.0, 94.0, 106.0, 109.0]


#: A hundred draws whose 10%/90% quantiles are still -1 and +1, with one draw beyond each
#: wing: under the worked strikes E[payoff] is (-300 - 200) / 100 = -5 USD per condor.
LOSING_DRAWS = [-3.0] + [-1.0] * 10 + [0.0] * 78 + [1.0] * 10 + [3.0]


def test_the_model_gate_reads_the_forecasts_expected_pnl_not_the_credit():
    rows = [_row(ENTRY, samples=list(LOSING_DRAWS))]
    metrics, report = _run(rows, _chain(ENTRY, EXPIRY), FLAT, min_edge_usd=MODEL_CREDIT - 2.5)
    entry = report["ledger"][0]
    assert entry["books"]["model"]["strikes"] == MODEL_STRIKES  # the quantiles did not move
    assert entry["model_expected_pnl_usd"] == pytest.approx(MODEL_CREDIT - 5.0)
    assert metrics["model_n_skipped_below_min_edge"] == 1 and metrics["always_n_trades"] == 1
    metrics, _ = _run(rows, _chain(ENTRY, EXPIRY), FLAT, min_edge_usd=MODEL_CREDIT - 7.5)
    assert metrics["model_n_trades"] == 1


def test_an_expiry_with_no_quotable_strike_records_a_zero_trade_fold():
    chain = _chain(ENTRY, EXPIRY, bid_size=0, ask_size=0)
    metrics, report = _run([_row(ENTRY)], chain, FLAT)
    assert metrics["n_entries"] == 1
    for book in CondorQuoteBacktest.BOOKS:
        assert metrics[f"{book}_n_trades"] == 0
        assert metrics[f"{book}_n_skipped_no_quotable_strike"] == 1
        assert metrics[f"{book}_total_pnl_usd"] == 0.0 and metrics[f"{book}_cvar_usd"] == 0.0
        # every per-book statistic of an empty book is exactly zero
        for stat in ("mean_pnl_usd", "hit_rate", "max_drawdown_usd", "mean_credit_usd",
                     "american_charge_usd"):
            assert metrics[f"{book}_{stat}"] == 0.0
    assert report["ledger"][0]["books"]["model"]["strikes"] is None


def test_a_target_outside_the_band_is_counted():
    metrics, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT,
                           max_abs_log_moneyness=0.06)  # the long call target sits at +0.075
    assert metrics["model_n_skipped_target_outside_band"] == 1
    assert metrics["always_n_skipped_target_outside_band"] == 1
    assert metrics["implied_n_trades"] == 1  # its targets stay within +-0.05
    # a skipped cell carries exactly the book fields, with the reason and nothing priced;
    # only the model book reports an expectation, and at the entry level
    cell = report["ledger"][0]["books"]["model"]
    assert cell == {"strikes": None, "credit_usd": None, "american_charge_usd": None,
                    "pnl_usd": None, "entered": False, "reason": "target_outside_band",
                    "side_pnl_usd": None, "delta": None, "equity_pnl_usd": None}
    assert report["ledger"][0]["books"]["always"] == cell
    assert set(report["ledger"][0]["books"]["implied"]) == set(cell)


def test_a_target_exactly_on_the_band_is_inside_it():
    # reference scale 1/16 and the +-1 draws put the wings at exactly 1.5/16 = 0.09375 in
    # log-moneyness (a dyadic product, exact in binary): a band of 0.09375 keeps them
    # (strict >), one tick below excludes them. Strikes 100 e^{-0.09375} = 91.05 -> 91,
    # e^{-0.0625} = 93.94 -> 93, e^{0.0625} = 106.45 -> 107, e^{0.09375} = 109.83 -> 110.
    chain = _set(_chain(ENTRY, EXPIRY), put93={"bid": 1.0, "ask": 1.1},
                 call107={"bid": 1.0, "ask": 1.1})
    rows = [_row(ENTRY, reference_scale=0.0625)]
    metrics, report = _run(rows, chain, FLAT, max_abs_log_moneyness=0.09375)
    assert report["ledger"][0]["horizon_scale"] == 0.0625
    assert report["ledger"][0]["books"]["model"]["strikes"] == [91.0, 93.0, 107.0, 110.0]
    assert metrics["model_n_trades"] == 1
    metrics, _ = _run(rows, chain, FLAT, max_abs_log_moneyness=0.09374)
    assert metrics["model_n_skipped_target_outside_band"] == 1


def test_the_band_is_classified_before_quotability_and_quotability_before_geometry():
    # A too-narrow band is not a data-coverage gap: on a chain with no size at all the
    # model's long call (+0.075) is outside a 0.06 band -> target_outside_band, while the
    # implied targets (inside +-0.05) reach the snap and find nothing -> no_quotable_strike.
    chain = _chain(ENTRY, EXPIRY, bid_size=0, ask_size=0)
    metrics, _ = _run([_row(ENTRY)], chain, FLAT, max_abs_log_moneyness=0.06)
    for book in ("model", "always"):
        assert metrics[f"{book}_n_skipped_target_outside_band"] == 1
        assert metrics[f"{book}_n_skipped_no_quotable_strike"] == 0
    assert metrics["implied_n_skipped_no_quotable_strike"] == 1
    assert metrics["implied_n_skipped_target_outside_band"] == 0
    # Both shorts of a constant forecast land on 100 (the puts stay sellable), but a put
    # wing with no ask SIZE is found first: no_quotable_strike, not degenerate_strikes.
    unbuyable = _chain(ENTRY, EXPIRY)
    for q in unbuyable:
        if q["right"] == "put":
            q["ask_size"] = 0
    metrics, _ = _run([_row(ENTRY, samples=[0.0] * 10)], unbuyable, FLAT)
    assert metrics["model_n_skipped_no_quotable_strike"] == 1
    assert metrics["model_n_skipped_degenerate_strikes"] == 0


def test_a_missing_min_edge_knob_gates_the_model_book_at_zero():
    # PARAMS omits min_edge_usd. Fees of 39 per leg leave a 4.00 credit
    # (160 - 156); the losing draws take 5.00 off it -> E = -1.00, below the default gate
    # of 0; the always book, never gated, trades the same 4.00 credit.
    rows = [_row(ENTRY, samples=list(LOSING_DRAWS))]
    metrics, report = _run(rows, _chain(ENTRY, EXPIRY), FLAT, fee_per_leg=39.0)
    assert "min_edge_usd" not in PARAMS and report["params"]["min_edge_usd"] == 0.0
    entry = report["ledger"][0]
    assert entry["books"]["model"]["credit_usd"] == pytest.approx(4.0)
    assert entry["model_expected_pnl_usd"] == pytest.approx(-1.0)
    assert metrics["model_n_skipped_below_min_edge"] == 1 and metrics["model_n_trades"] == 0
    assert metrics["always_n_trades"] == 1


def test_credit_bounds_are_classified_not_raised():
    costly, _ = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT, fee_per_leg=100.0)
    for book in CondorQuoteBacktest.BOOKS:
        assert costly[f"{book}_n_skipped_nonpositive_credit"] == 1
    rich = _chain(ENTRY, EXPIRY)
    for q in rich:
        if q["right"] == "put" and q["strike"] == 95.0:
            q["bid"], q["ask"] = 3.5, 3.6       # 3.5 + 1.5 - 1.1 - 0.8 = 3.1 > the 2-point call wing
    wide, report = _run([_row(ENTRY)], rich, FLAT)
    assert wide["model_n_skipped_credit_not_below_width"] == 1
    assert report["ledger"][0]["books"]["model"]["credit_usd"] == pytest.approx(310 - 2.6)
    # an EXACT zero is nonpositive: dyadic quotes summing to 1.5 per share (2.0 + 1.5 - 1.25
    # - 0.75) against fees of 37.5 per leg leave 150 - 150 = 0.0 on every book
    zero = _set(_chain(ENTRY, EXPIRY), put92={"ask": 1.25}, call108={"ask": 0.75})
    exact, report = _run([_row(ENTRY)], zero, FLAT, fee_per_leg=37.5)
    assert report["ledger"][0]["books"]["model"]["credit_usd"] == 0.0
    for book in ("model", "always"):
        assert exact[f"{book}_n_skipped_nonpositive_credit"] == 1
    # ... while half a dollar (fees 37.375 per leg: 150 - 149.5) is a credit that trades,
    # and a settled +0.5 is a hit; settled at 94.99 the same trade loses 1.00 - 0.5 = 0.50,
    # a miss
    half, report = _run([_row(ENTRY)], zero, FLAT, fee_per_leg=37.375)
    assert report["ledger"][0]["books"]["always"]["credit_usd"] == 0.5
    assert half["always_n_trades"] == 1 and half["model_n_trades"] == 1
    assert half["always_hit_rate"] == 1.0
    lost, report = _run([_row(ENTRY)], zero, _settled_at(94.99), fee_per_leg=37.375)
    assert report["ledger"][0]["books"]["always"]["pnl_usd"] == pytest.approx(-0.5)
    assert lost["always_hit_rate"] == 0.0
    # a P&L of exactly zero (12.5 credit, 12.5 lost at 94.875, both dyadic) is not a hit
    flat, report = _run([_row(ENTRY)], zero, _settled_at(94.875), fee_per_leg=34.375)
    assert report["ledger"][0]["books"]["always"]["pnl_usd"] == 0.0
    assert flat["always_hit_rate"] == 0.0 and flat["always_n_trades"] == 1


#: Every field a chain row must carry (restated here, never read from the node).
CHAIN_FIELDS = ("instrument", "date", "expiry", "settle_date", "dte", "right", "strike",
                "bid", "ask", "bid_size", "ask_size", "iv", "underlying_price")


@pytest.mark.parametrize("field", CHAIN_FIELDS)
def test_a_chain_row_lacking_any_required_field_refuses(field):
    chain = _chain(ENTRY, EXPIRY)
    del chain[0][field]
    with pytest.raises(ValueError, match=f"lacks.*{field}"):
        _run([_row(ENTRY)], chain, FLAT)


def test_zero_fee_carry_and_edge_are_accepted():
    node = CondorQuoteBacktest("bt", {**PARAMS, "fee_per_leg": 0.0, "carry_rate": 0.0,
                                      "min_edge_usd": 0.0})
    assert (node.params["fee_per_leg"], node.params["carry_rate"]) == (0.0, 0.0)
    # a one-day bucket with a one-day horizon and a multiplier of one is the smallest cell
    one = CondorQuoteBacktest("bt", {**PARAMS, "dte_min": 1, "dte_max": 1, "label_horizon": 1,
                                     "multiplier": 1})
    assert (one.params["dte_min"], one.params["dte_max"], one.params["multiplier"]) == (1, 1, 1)


def test_refusal_messages_name_the_bound_they_broke():
    with pytest.raises(ConfigError, match=r"dte_min 5 must not exceed dte_max 4"):
        CondorQuoteBacktest("bt", {**PARAMS, "dte_max": 4})
    for alpha in (0.0, -0.5, "0.95", None):  # a non-number is a config error, not a crash
        with pytest.raises(ConfigError, match="cvar_alpha"):
            CondorQuoteBacktest("bt", {**PARAMS, "cvar_alpha": alpha})
    with pytest.raises(ConfigError, match="short_q"):  # the open bound at 0 (0.5 is pinned)
        CondorQuoteBacktest("bt", {**PARAMS, "short_q": 0})
    with pytest.raises(ValueError, match=r"2024-03-01 2024-03-05\) has dte 4 outside the "
                                         r"declared \[5, 10\]"):
        _run([_row(ENTRY)], _chain(ENTRY, "2024-03-05"), FLAT)
    # the chain's underlying_price must match the forecast close within 1e-9 RELATIVE
    # (5e-8 on a price of 100 is inside that; an absolute 1e-9 alone would refuse it)
    close = _chain(ENTRY, EXPIRY)
    for q in close:
        q["underlying_price"] = 100.0 + 5e-8
    assert _run([_row(ENTRY)], close, FLAT)[0]["model_n_trades"] == 1
    for q in close:
        q["underlying_price"] = 100.0 + 1e-5
    with pytest.raises(ValueError, match="misaligned"):
        _run([_row(ENTRY)], close, FLAT)


def test_min_edge_gates_only_the_model_book():
    metrics, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT, min_edge_usd=1e6)
    assert metrics["model_n_skipped_below_min_edge"] == 1 and metrics["model_n_trades"] == 0
    assert metrics["always_n_trades"] == 1 and metrics["implied_n_trades"] == 1
    assert report["ledger"][0]["books"]["model"]["pnl_usd"] is None


def test_the_horizon_rescale_is_sqrt_sessions_over_label_horizon():
    _, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT, label_horizon=20)
    entry = report["ledger"][0]
    assert entry["horizon_scale"] == pytest.approx(0.05 * math.sqrt(5 / 20))
    assert entry["reference_scale"] == 0.05  # the ledger keeps the row's own scale too
    # tighter scale: 100 e^{-0.025} = 97.53 -> 97; 100 e^{-0.0375} = 96.32 -> 96 ...
    assert entry["books"]["model"]["strikes"] == [96.0, 97.0, 103.0, 104.0]
    # the always book shares the model's snap at the SAME horizon scale
    assert entry["books"]["always"]["strikes"] == [96.0, 97.0, 103.0, 104.0]


def test_the_implied_book_needs_an_atm_iv_on_both_rights():
    chain = _chain(ENTRY, EXPIRY, iv=None)
    metrics, report = _run([_row(ENTRY)], chain, FLAT)
    assert metrics["implied_n_skipped_no_atm_iv"] == 1 and metrics["implied_n_trades"] == 0
    assert report["ledger"][0]["atm_iv"] is None
    assert metrics["model_n_trades"] == 1


def _smile(chain, wings=0.6, at=None):
    """Give every strike ``wings`` vol except the ``at`` map of strike -> iv (or per right)."""
    at = at or {99.0: 0.25, 100.0: {"put": 0.18, "call": 0.22}, 101.0: 0.3}
    for q in chain:
        iv = at.get(q["strike"], wings)
        q["iv"] = iv[q["right"]] if isinstance(iv, dict) else iv
    return chain


def test_the_atm_iv_is_the_nearest_valid_pairs_mean_not_any_strikes():
    _, report = _run([_row(ENTRY)], _smile(_chain(ENTRY, EXPIRY)), FLAT)
    entry = report["ledger"][0]
    assert entry["atm_iv"] == pytest.approx(0.2)  # the MEAN of 0.18 / 0.22 at the forward
    assert entry["books"]["implied"]["strikes"] == IMPLIED_STRIKES
    # the nearest pair invalid on one right (a missing or a zero iv): the next nearest
    # valid pair, lower strike on a tie
    for bad in (None, 0.0):
        invalid = _smile(_chain(ENTRY, EXPIRY))
        for q in invalid:
            if q["strike"] == 100.0 and q["right"] == "put":
                q["iv"] = bad
        assert _run([_row(ENTRY)], invalid, FLAT)[1]["ledger"][0]["atm_iv"] == pytest.approx(0.25)
    # a crossed quote at the nearest strike disqualifies that pair the same way
    crossed = _smile(_chain(ENTRY, EXPIRY))
    for q in crossed:
        if q["strike"] == 100.0:
            q["bid"], q["ask"] = 0.6, 0.5
    assert _run([_row(ENTRY)], crossed, FLAT)[1]["ledger"][0]["atm_iv"] == pytest.approx(0.25)
    # a strike quoted on one right only is no candidate at all
    one_sided = [q for q in _smile(_chain(ENTRY, EXPIRY))
                 if not (q["strike"] == 100.0 and q["right"] == "call")]
    assert _run([_row(ENTRY)], one_sided, FLAT)[1]["ledger"][0]["atm_iv"] == pytest.approx(0.25)


def test_credit_at_the_narrower_width_and_edge_at_the_gate_are_refused():
    at_width = _chain(ENTRY, EXPIRY)
    exact = {("put", 95.0): (2.25, 2.5), ("put", 92.0): (1.0, 1.25), ("call", 108.0): (0.25, 0.5)}
    for q in at_width:  # dyadic quotes: 2.25 + 1.5 - 1.25 - 0.5 is EXACTLY the 2-point call wing
        if (q["right"], q["strike"]) in exact:
            q["bid"], q["ask"] = exact[(q["right"], q["strike"])]
    metrics, report = _run([_row(ENTRY)], at_width, FLAT)
    assert report["ledger"][0]["books"]["model"]["credit_usd"] == 200.0 - 2.6
    assert metrics["model_n_skipped_credit_not_below_width"] == 1
    # every worked draw settles inside the shorts, so the edge IS the credit the node reports
    _, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT)
    edge = report["ledger"][0]["model_expected_pnl_usd"]
    assert edge == report["ledger"][0]["books"]["model"]["credit_usd"]
    metrics, _ = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT, min_edge_usd=edge)
    assert metrics["model_n_skipped_below_min_edge"] == 1  # the edge must EXCEED the gate


# -- settlement ---------------------------------------------------------------------------


def test_settlement_uses_the_last_close_on_or_before_the_settlement_date():
    # a Saturday OCC expiry settles on the Friday; a holiday Friday on the Thursday
    saturday = _chain(ENTRY, "2024-03-09", settle="2024-03-08")
    _, report = _run([_row(ENTRY)], saturday, FLAT)
    entry = report["ledger"][0]
    assert (entry["expiry"], entry["settle_date"], entry["settlement_date"],
            entry["settlement"]) == ("2024-03-09", "2024-03-08", "2024-03-08", 97.0)
    holiday = _series([(d, 100.0) for d in SESSIONS if d not in ("2024-03-08",)]
                      + [("2024-03-07", 96.0)])
    holiday = sorted({r["date"]: r for r in holiday}.values(), key=lambda r: r["date"])
    _, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), holiday)
    entry = report["ledger"][0]
    assert (entry["settlement_date"], entry["settlement"]) == ("2024-03-07", 96.0)
    assert entry["settle_date"] == "2024-03-08"  # the ledger keeps both dates apart
    # the sessions are counted to the settle DATE, not to the close that settled: still 5
    assert entry["sessions"] == 5 and entry["books"]["model"]["strikes"] == MODEL_STRIKES
    # so does the charge window: a put in the money from 03-04 carries to 03-08, four days,
    # although the last close is 03-07's
    itm = [dict(r, close=94.0) if r["date"] == "2024-03-04" else r for r in holiday]
    _, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), itm)
    charge = report["ledger"][0]["books"]["model"]["american_charge_usd"]
    assert charge == pytest.approx(95.0 * (math.exp(0.055 * 4 / 365) - 1) * 100)
    # and so does the cursor: a forecast row on the settlement CLOSE's date (03-07) is
    # still inside the position, which ends on the settle date (the series runs on so
    # that a 03-07 entry, were it admitted, could settle and show in the ledger)
    chain = _chain(ENTRY, EXPIRY) + _chain("2024-03-07", "2024-03-14")
    longer = _series([(d, 100.0) for d in _weekdays("2024-03-01", "2024-03-20")
                      if d not in ("2024-03-07", "2024-03-08")] + [("2024-03-07", 96.0)])
    longer.sort(key=lambda r: r["date"])
    _, report = _run([_row(ENTRY), _row("2024-03-07")], chain, longer)
    assert [e["date"] for e in report["ledger"]] == ["2024-03-01"]
    # a series whose FIRST row is the settlement close settles (one later row suffices)
    first = _series([("2024-03-08", 97.0), ("2024-03-11", 98.0)])
    _, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), first)
    assert report["ledger"][0]["settlement"] == 97.0 and report["ledger"][0]["books"]["model"]["entered"]


@pytest.mark.parametrize("closes", [
    [(d, 100.0) for d in _weekdays("2024-03-01", "2024-03-07")],          # ends before settlement
    [(d, 100.0) for d in _weekdays("2024-03-01", "2024-03-08")],          # no row after it
    [(d, 100.0) for d in ("2024-03-01", "2024-03-12")],                   # last close 7 days back
])
def test_an_entry_that_cannot_settle_is_unsettled_and_never_traded(closes):
    metrics, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), _series(closes))
    assert metrics["n_skipped_unsettled"] == 1 and metrics["n_entries"] == 0
    assert report["ledger"] == []


def test_the_settlement_gap_boundary_is_four_calendar_days():
    """A Monday settlement date: the Thursday before (4 days, Sandy's shape) settles,
    the Wednesday before (5 days) is too stale."""
    monday = _chain(ENTRY, "2024-03-11")  # DTE 10, the bucket's upper bound
    four = _series([("2024-03-01", 100.0), ("2024-03-07", 97.0), ("2024-03-12", 98.0)])
    metrics, report = _run([_row(ENTRY)], monday, four)
    entry = report["ledger"][0]
    assert (entry["settlement_date"], entry["settlement"]) == ("2024-03-07", 97.0)
    assert metrics["n_skipped_unsettled"] == 0 and metrics["always_n_trades"] == 1
    five = _series([("2024-03-01", 100.0), ("2024-03-06", 97.0), ("2024-03-12", 98.0)])
    metrics, report = _run([_row(ENTRY)], monday, five)
    assert metrics["n_skipped_unsettled"] == 1 and report["ledger"] == []


def test_rows_after_the_entry_change_only_the_outcome():
    chain = _chain(ENTRY, EXPIRY) + _chain("2024-03-04", "2024-03-11")
    base_metrics, base = _run([_row(ENTRY)], chain, FLAT)
    shaken = copy.deepcopy(chain)
    for q in shaken:
        if q["date"] > ENTRY:
            q.update(bid=q["bid"] * 3, ask=q["ask"] * 3, iv=0.9, expiry="2024-03-12",
                     settle_date="2024-03-12", dte=8)
    series = copy.deepcopy(FLAT)
    for r in series:
        if r["date"] > ENTRY:
            r["close"] = r["close"] * 0.8
    metrics, report = _run([_row(ENTRY)], shaken, series)
    before, after = base["ledger"][0], report["ledger"][0]
    for key in ("expiry", "settle_date", "sessions", "horizon_scale", "atm_iv",
                "model_expected_pnl_usd"):
        assert before[key] == after[key], key
    for book in CondorQuoteBacktest.BOOKS:
        assert before["books"][book]["strikes"] == after["books"][book]["strikes"]
        assert before["books"][book]["credit_usd"] == after["books"][book]["credit_usd"]
        assert before["books"][book]["entered"] is after["books"][book]["entered"]
    assert after["settlement"] == pytest.approx(97.0 * 0.8)
    # ... and the outcome IS what the perturbed closes say: 77.6 sits below every put wing,
    # and the 03-04 close (80) puts both short puts in the money, so carry runs from there
    model_charge = american_short_charge(series, 95.0, 106.0, ENTRY, EXPIRY, 0.055, 100)
    implied_charge = american_short_charge(series, 96.0, 104.0, ENTRY, EXPIRY, 0.055, 100)
    assert model_charge["put_carry_usd"] > 0 and model_charge["call_dividend_usd"] == 0.0
    for book, pnl in (("model", MODEL_CREDIT - 300.0 - model_charge["total_usd"]),
                      ("always", MODEL_CREDIT - 300.0 - model_charge["total_usd"]),
                      ("implied", IMPLIED_CREDIT - 100.0 - implied_charge["total_usd"])):
        assert after["books"][book]["pnl_usd"] == pytest.approx(pnl)
        assert metrics[f"{book}_total_pnl_usd"] == pytest.approx(pnl)
    assert base_metrics["model_total_pnl_usd"] == pytest.approx(MODEL_CREDIT)


# -- refusals and empty folds ---------------------------------------------------------------


def test_plumbing_refusals():
    with pytest.raises(ValueError, match="no forecast row"):
        _run([_row("2024-02-20")], _chain(ENTRY, EXPIRY), FLAT)  # train only
    with pytest.raises(ValueError, match="chain"):
        _run([_row(ENTRY)], [], FLAT)  # an empty chain across the whole snapshot
    misaligned = _chain(ENTRY, EXPIRY, close=101.0)
    with pytest.raises(ValueError, match="underlying_price"):
        _run([_row(ENTRY)], misaligned, FLAT)
    out_of_bucket = _chain(ENTRY, "2024-03-15")  # DTE 14 > dte_max 10
    with pytest.raises(ValueError, match="dte"):
        _run([_row(ENTRY)], out_of_bucket, FLAT)
    node = CondorQuoteBacktest("bt", dict(PARAMS))
    assert node.validate_inputs({"forecasts": [], "chain": [], "underlying": []}) == []
    assert node.validate_inputs({"forecasts": [], "chain": []}) != []
    assert node.validate_inputs({"forecasts": [], "chain": {}, "underlying": []}) != []


def test_a_fold_whose_rows_all_lack_a_chain_records_zero_trades():
    rows = [_row("2024-03-01"), _row("2024-03-04", outcome_z=None), _row("2024-03-05", close=0.0)]
    metrics, report = _run(rows, _chain("2024-03-06", "2024-03-15"), FLAT)
    assert metrics["n_entries"] == 0 and metrics["n_skipped_no_chain"] == 1
    assert metrics["n_skipped_no_outcome"] == 1 and metrics["n_skipped_no_forward"] == 1
    for book in CondorQuoteBacktest.BOOKS:
        assert metrics[f"{book}_n_trades"] == 0 and metrics[f"{book}_max_drawdown_usd"] == 0.0
    assert report["chain"]["first_date"] == "2024-03-06" and report["ledger"] == []


@pytest.mark.parametrize("change", [
    {"split": "nope"}, {"short_q": 0.5}, {"wing_z": 0}, {"wing_points": 25},
    {"multiplier": 0}, {"fee_per_leg": -1}, {"dte_min": 0}, {"dte_max": 4},
    {"max_abs_log_moneyness": 0}, {"label_horizon": 0}, {"carry_rate": -0.01},
    {"carry_rate": "0.055"}, {"min_edge_usd": float("nan")}, {"cvar_alpha": 1.0},
    {"hold_steps": 21}, {"atm_ratio": 0.8}, {"iv_index": "x"}, {"surprise": 1},
    {"dte_max": 10.5},
])
def test_knob_refusals(change):
    with pytest.raises(ConfigError, match=next(iter(change))):  # the message names the knob
        CondorQuoteBacktest("bt", {**PARAMS, **change})


@pytest.mark.parametrize("missing", list(PARAMS))
def test_every_cell_knob_is_required(missing):
    with pytest.raises(ConfigError, match=missing):
        CondorQuoteBacktest("bt", {k: v for k, v in PARAMS.items() if k != missing})


def test_the_proxy_backtest_keeps_its_own_contract():
    """The shared base changes nothing the VIX-proxy node declares (ADR-0187 item 5)."""
    assert CondorBacktest.ROW_REASONS == ("no_outcome", "no_forecast", "no_forward",
                                          "no_scale", "no_iv")
    assert CondorBacktest.BOOK_REASONS == ("degenerate_strikes", "nonpositive_credit",
                                           "below_min_edge")
    assert CondorBacktest.outputs == CondorQuoteBacktest.outputs == ("metrics", "report")
    assert CondorBacktest.role == CondorQuoteBacktest.role == "score"
    assert CondorQuoteBacktest.serving_effect(PARAMS, {}) == "forbidden"
    assert "hold_steps" in CondorBacktest._PARAMS and "hold_steps" not in CondorQuoteBacktest._PARAMS


# -- one separating fixture per rule ------------------------------------------------------
#
# Each scenario below makes a rule's value differ from its nearest substitute (the gate at
# the horizon scale vs the raw scale, the band on every leg, a wing beyond the SNAPPED
# short, dte/365 vs sessions/252, the -s^2/2 drift, two instruments on one date, ...).
# Every expected value is restated by hand; nothing is read back from the node.


def _set(chain, **quotes):
    """Overwrite (right, strike) -> dict of quote fields on a chain."""
    for q in chain:
        over = quotes.get(f"{q['right']}{int(q['strike'])}")
        if over:
            q.update(over)
    return chain


#: Q(0.1) = -1.2 (index 9 of the sorted hundred), Q(0.9) = +1 (index 89); five draws at -1.4.
M1_DRAWS = [-1.4] * 5 + [-1.2] * 5 + [0.0] * 79 + [1.0] * 11


def test_the_gate_prices_the_draws_at_the_horizon_scale_not_the_reference_scale():
    # label_horizon 4, n = 5 sessions: s' = 0.05 sqrt(5/4) = 0.055902
    # targets 100 e^{s' z}: 90.93 / 93.51 / 105.75 / 108.75 -> [90, 93, 106, 109]
    # credit: put 93 bid 0.5 + call 106 bid 1.5 - put 90 ask 0.6 - call 109 ask 0.6 = 0.8
    #         -> 80 - 2.6 = 77.4
    # the -1.4 draws settle at 100 e^{-1.4 s'} = 92.472 < 93 (0.528 below the short put)
    #   -> E = 77.4 - 100 * 5 * 0.5278 / 100 = 74.761; at the RAW scale they settle at
    #   93.239 > 93 and E would be the credit, 77.4.
    rows = [_row(ENTRY, samples=list(M1_DRAWS))]
    metrics, report = _run(rows, _chain(ENTRY, EXPIRY), FLAT, label_horizon=4, min_edge_usd=76.0)
    entry = report["ledger"][0]
    assert entry["horizon_scale"] == pytest.approx(0.05 * math.sqrt(5 / 4))
    assert entry["books"]["model"]["strikes"] == [90.0, 93.0, 106.0, 109.0]
    assert entry["books"]["always"]["strikes"] == [90.0, 93.0, 106.0, 109.0]
    assert entry["books"]["model"]["credit_usd"] == pytest.approx(77.4)
    expected = 77.4 - 5 * (93.0 - 100 * math.exp(-1.4 * 0.05 * math.sqrt(5 / 4)))
    assert expected == pytest.approx(74.761, abs=1e-3)
    assert entry["model_expected_pnl_usd"] == pytest.approx(expected)
    assert metrics["model_n_skipped_below_min_edge"] == 1 and metrics["always_n_trades"] == 1
    metrics, _ = _run(rows, _chain(ENTRY, EXPIRY), FLAT, label_horizon=4, min_edge_usd=74.0)
    assert metrics["model_n_trades"] == 1


PUT_HEAVY = [-2.0] * 2 + [0.0] * 6 + [1.0] * 2    # Q(0.1) = -2, Q(0.9) = +1
CALL_HEAVY = [-1.0] * 2 + [0.0] * 6 + [2.0] * 2   # Q(0.1) = -1, Q(0.9) = +2


@pytest.mark.parametrize("draws, band, strikes", [
    # exponents x 0.05: puts -0.125 / -0.1, calls 0.05 / 0.075: band 0.09 -> only the PUTS exceed
    (PUT_HEAVY, 0.09, None),
    # band 0.11 -> only the LONG put exceeds
    (PUT_HEAVY, 0.11, None),
    # puts -0.075 / -0.05, calls 0.1 / 0.125: band 0.09 -> only the CALLS exceed
    (CALL_HEAVY, 0.09, None),
    (CALL_HEAVY, 0.11, None),
    # inside the default band the asymmetric quantiles land on their own strikes:
    # 100 e^{-0.125} = 88.25 -> 88, e^{-0.1} = 90.48 -> 90, calls as the worked entry
    (PUT_HEAVY, 0.2, [88.0, 90.0, 106.0, 108.0]),
    # e^{0.1} = 110.5 -> 111, e^{0.125} = 113.3 -> 114 (grid extended to 116)
    (CALL_HEAVY, 0.2, [92.0, 95.0, 111.0, 114.0]),
])
def test_the_band_is_checked_on_each_leg_and_each_side(draws, band, strikes):
    chain = _chain(ENTRY, EXPIRY, strikes=range(88, 117))
    metrics, report = _run([_row(ENTRY, samples=list(draws))], chain, FLAT,
                           max_abs_log_moneyness=band)
    cell = report["ledger"][0]["books"]["model"]
    if strikes is None:
        assert cell["reason"] == "target_outside_band" and cell["strikes"] is None
        assert metrics["model_n_skipped_target_outside_band"] == 1
        assert metrics["always_n_skipped_target_outside_band"] == 1
    else:
        assert cell["strikes"] == strikes
    assert metrics["implied_n_trades"] == 1  # its targets stay within +-0.05


def test_the_long_put_sits_below_the_short_put_even_after_the_short_snapped_past_its_target():
    # puts 93..95 unsellable: short put target 95.12 -> 92; long put target 92.77 -> 92 is
    # the short, so 91 (ask 0.6). The put wing is ONE point wide, so the short put's bid is
    # set to 0.5 (the worked 1.0 would make the credit 1.1 >= the wing and skip the book):
    # credit 0.5 + 1.5 - 0.6 - 0.8 = 0.6 -> 57.4
    chain = _set(_chain(ENTRY, EXPIRY), put95={"bid": 0.0}, put94={"bid_size": 0},
                 put93={"bid": 0.0}, put92={"bid": 0.5, "ask": 0.6})
    metrics, report = _run([_row(ENTRY)], chain, FLAT)
    assert report["ledger"][0]["books"]["model"]["strikes"] == [91.0, 92.0, 106.0, 108.0]
    assert report["ledger"][0]["books"]["model"]["credit_usd"] == pytest.approx(60 - 2.6)
    assert metrics["model_n_trades"] == 1


def test_the_short_call_is_snapped_past_unsellable_calls_and_the_long_call_beyond_it():
    # call 106 has a bid but no bid SIZE, 107 no bid: short call target 105.13 -> 108
    # (bid 0.7); long call target 107.79 -> 108 is the short, so 109. The call wing is ONE
    # point wide, so the long call's ask is set to 0.8 (the default 0.6 would make the credit
    # exactly 1.0 = the wing, which the contract refuses): credit 2.0 + 0.7 - 1.1 - 0.8 = 0.8
    # -> 77.4
    chain = _set(_chain(ENTRY, EXPIRY), call106={"bid_size": 0}, call107={"bid": 0.0},
                 call109={"ask": 0.8})
    metrics, report = _run([_row(ENTRY)], chain, FLAT)
    assert report["ledger"][0]["books"]["model"]["strikes"] == [92.0, 95.0, 108.0, 109.0]
    assert report["ledger"][0]["books"]["model"]["credit_usd"] == pytest.approx(80 - 2.6)
    assert metrics["model_n_trades"] == 1


def test_a_size_of_exactly_one_contract_is_quotable():
    chain = _set(_chain(ENTRY, EXPIRY), put95={"bid_size": 1}, call108={"ask_size": 1})
    _, report = _run([_row(ENTRY)], chain, FLAT)
    assert report["ledger"][0]["books"]["model"]["strikes"] == MODEL_STRIKES


@pytest.mark.parametrize("right, field", [
    ("put", "bid"), ("call", "bid"), ("put", "ask"), ("call", "ask"),
    # a zero SIZE on one side leaves the quote valid, so only that side's leg fails:
    # no bid size stops the short, no ask size stops the long alone
    ("put", "bid_size"), ("call", "bid_size"), ("put", "ask_size"), ("call", "ask_size"),
])
def test_one_side_with_no_quotable_strike_is_counted_per_leg(right, field):
    chain = _chain(ENTRY, EXPIRY)
    for q in chain:
        if q["right"] == right:
            q[field] = 0 if field.endswith("size") else 0.0
    metrics, report = _run([_row(ENTRY)], chain, FLAT)
    assert metrics["model_n_skipped_no_quotable_strike"] == 1
    assert report["ledger"][0]["books"]["model"]["strikes"] is None


def test_a_constant_forecast_gives_degenerate_strikes_and_the_implied_book_still_trades():
    # z = 0 for both shorts: put 100, call 100 -> not strictly increasing
    metrics, report = _run([_row(ENTRY, samples=[0.0] * 10)], _chain(ENTRY, EXPIRY), FLAT)
    assert metrics["model_n_skipped_degenerate_strikes"] == 1
    assert metrics["always_n_skipped_degenerate_strikes"] == 1
    assert metrics["model_n_skipped_nonpositive_credit"] == 0
    assert report["ledger"][0]["books"]["implied"]["strikes"] == IMPLIED_STRIKES
    assert metrics["implied_n_trades"] == 1


def test_the_implied_scale_uses_calendar_dte_over_365():
    # Monday expiry 03-11: DTE 10 but only 6 sessions; iv 0.4: s = 0.4 sqrt(10/365) = 0.066208
    # ln(K/F) = -s^2/2 + s z: 88.68 / 91.66 / 108.62 / 112.27 -> [88, 91, 109, 113]
    # (sessions/252 would give s = 0.061721 -> [89, 92, 109, 112])
    chain = _set(_chain(ENTRY, "2024-03-11", strikes=range(85, 116), iv=0.4),
                 put91={"bid": 1.0, "ask": 1.1}, call109={"bid": 1.0, "ask": 1.1})
    series = _series([(d, 100.0) for d in _weekdays("2024-03-01", "2024-03-20")])
    metrics, report = _run([_row(ENTRY)], chain, series)
    entry = report["ledger"][0]
    assert (entry["dte"], entry["sessions"], entry["atm_iv"]) == (10, 6, pytest.approx(0.4))
    s = 0.4 * math.sqrt(10 / 365)
    targets = [100 * math.exp(-s * s / 2 + s * z) for z in (Z10 - 0.5, Z10, -Z10, -Z10 + 0.5)]
    assert [math.floor(targets[0]), math.floor(targets[1]), math.ceil(targets[2]),
            math.ceil(targets[3])] == [88, 91, 109, 113]
    assert entry["books"]["implied"]["strikes"] == [88.0, 91.0, 109.0, 113.0]
    assert entry["books"]["implied"]["credit_usd"] == pytest.approx(80 - 2.6)
    assert metrics["implied_n_trades"] == 1


def test_the_implied_targets_carry_the_minus_half_s_squared_drift():
    # S = 1000, iv 0.6, DTE 10: s = 0.099312, s^2/2 = 0.004931 (0.49% of spot = 4.9 points)
    # with the drift: 833.72 / 876.16 / 1130.14 / 1187.68 -> [833, 876, 1131, 1188]
    # without it:     837.84 / 880.49 / 1135.73 / 1193.55 -> [837, 880, 1136, 1194]
    chain = _set(_chain(ENTRY, "2024-03-11", close=1000.0, strikes=range(820, 1200), iv=0.6),
                 put876={"bid": 1.0, "ask": 1.1}, call1131={"bid": 1.0, "ask": 1.1})
    series = _series([(d, 1000.0) for d in _weekdays("2024-03-01", "2024-03-20")])
    metrics, report = _run([_row(ENTRY, close=1000.0)], chain, series)
    entry = report["ledger"][0]
    assert entry["atm_iv"] == pytest.approx(0.6)
    assert entry["books"]["implied"]["strikes"] == [833.0, 876.0, 1131.0, 1188.0]
    assert metrics["implied_n_trades"] == 1


@pytest.mark.parametrize("crossed_right", ["put", "call"])
def test_a_crossed_quote_on_either_right_disqualifies_the_atm_pair(crossed_right):
    chain = _smile(_chain(ENTRY, EXPIRY))
    for q in chain:
        if q["strike"] == 100.0 and q["right"] == crossed_right:
            q["bid"], q["ask"] = 0.6, 0.5
    assert _run([_row(ENTRY)], chain, FLAT)[1]["ledger"][0]["atm_iv"] == pytest.approx(0.25)


@pytest.mark.parametrize("missing_right", ["put", "call"])
def test_a_strike_listed_on_one_right_only_is_no_atm_candidate(missing_right):
    # without its put (or call) the 100 strike is no pair; 99 and 101 tie, the lower wins
    chain = [q for q in _smile(_chain(ENTRY, EXPIRY))
             if not (q["strike"] == 100.0 and q["right"] == missing_right)]
    assert _run([_row(ENTRY)], chain, FLAT)[1]["ledger"][0]["atm_iv"] == pytest.approx(0.25)


@pytest.mark.parametrize("field", ["bid_size", "ask_size"])
def test_an_atm_pair_with_an_unusable_size_on_either_side_is_no_candidate(field):
    # the row-level rule reads BOTH sizes: a None on the put at 100 disqualifies the pair
    chain = _smile(_chain(ENTRY, EXPIRY))
    for q in chain:
        if q["strike"] == 100.0 and q["right"] == "put":
            q[field] = None
    assert _run([_row(ENTRY)], chain, FLAT)[1]["ledger"][0]["atm_iv"] == pytest.approx(0.25)


def test_an_expiry_listing_a_single_row_is_no_quotable_strike_not_a_crash():
    metrics, _ = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY, strikes=[100])[:1], FLAT)
    assert metrics["model_n_skipped_no_quotable_strike"] == 1 and metrics["n_entries"] == 1


def test_the_settlement_gap_is_measured_from_the_settle_date_not_the_occ_expiry():
    # Saturday expiry 03-09 settles Friday 03-08; with no close after Monday 03-04 until
    # 03-11, Monday's close is 4 days before the settle date (settles) but 5 before the expiry
    chain = _chain(ENTRY, "2024-03-09", settle="2024-03-08")
    series = _series([("2024-03-01", 100.0), ("2024-03-04", 96.0), ("2024-03-11", 98.0),
                      ("2024-03-12", 99.0)])
    metrics, report = _run([_row(ENTRY)], chain, series)
    assert metrics["n_entries"] == 1 and metrics["n_skipped_unsettled"] == 0
    assert (report["ledger"][0]["settlement_date"], report["ledger"][0]["settlement"]) == \
        ("2024-03-04", 96.0)


def test_a_books_american_charge_is_the_sum_over_its_trades():
    # two traded cells with two DIFFERENT charges: SPY's dividend (100) plus one day of carry,
    # QQQ's dividend (50); neither the larger nor the first alone is the book's charge
    dividends = [("2024-03-07", 1.0)]
    spy_closes = [(d, 100.0) for d in SESSIONS[:3]] + [("2024-03-06", 107.0), ("2024-03-07", 94.0),
                                                        ("2024-03-08", 97.0), ("2024-03-11", 98.0)]
    spy_series = _series(spy_closes, dividends)
    spy_charge = american_short_charge(spy_series, 95.0, 106.0, ENTRY, EXPIRY, 0.055, 100)["total_usd"]
    qqq_chain = _set(_chain(ENTRY, EXPIRY, close=50.0, strikes=range(44, 57), instrument="QQQ"),
                     put47={"bid": 1.0, "ask": 1.1}, call53={"bid": 1.0, "ask": 1.1})
    qqq_closes = [(d, 50.0) for d in SESSIONS[:3]] + [("2024-03-06", 55.0), ("2024-03-07", 50.0),
                                                       ("2024-03-08", 45.0), ("2024-03-11", 45.0)]
    qqq_series = [{"instrument": "QQQ", "date": d, "close": c, "asof_ms": _ms(d),
                   "dividend_amount": 0.5 if d == "2024-03-07" else 0.0} for d, c in qqq_closes]
    rows = [_row(ENTRY), _row(ENTRY, close=50.0, instrument="QQQ")]
    metrics, report = _run(rows, _chain(ENTRY, EXPIRY) + qqq_chain, spy_series + qqq_series)
    charges = [e["books"]["model"]["american_charge_usd"] for e in report["ledger"]]
    assert charges == pytest.approx([50.0, spy_charge]) and spy_charge > 100.0
    assert metrics["model_american_charge_usd"] == pytest.approx(50.0 + spy_charge)


def test_two_instruments_are_priced_and_settled_from_their_own_chain_and_closes():
    # QQQ at 50: targets 46.39 / 47.56 / 52.56 / 53.89 -> [46, 47, 53, 54]; shorts quoted 1.0
    # -> credit 1.0 + 1.0 - 0.6 - 0.6 = 0.8 -> 77.4; settles at 45 through both put wings
    # (-100); pre-ex close 55 > 53 with a 0.5 dividend on 03-07 -> 50 USD charge;
    # 03-08 is the settlement day so no carry -> pnl 77.4 - 100 - 50 = -72.6
    qqq_chain = _set(_chain(ENTRY, EXPIRY, close=50.0, strikes=range(44, 57), instrument="QQQ"),
                     put47={"bid": 1.0, "ask": 1.1}, call53={"bid": 1.0, "ask": 1.1})
    qqq_closes = [(d, 50.0) for d in SESSIONS[:3]] + [("2024-03-06", 55.0), ("2024-03-07", 50.0),
                                                       ("2024-03-08", 45.0), ("2024-03-11", 45.0)]
    qqq_series = [{"instrument": "QQQ", "date": d, "close": c, "asof_ms": _ms(d),
                   "dividend_amount": 0.5 if d == "2024-03-07" else 0.0} for d, c in qqq_closes]
    rows = [_row(ENTRY), _row(ENTRY, close=50.0, instrument="QQQ")]
    metrics, report = _run(rows, _chain(ENTRY, EXPIRY) + qqq_chain, FLAT + qqq_series)
    assert [(e["instrument"], e["forward"], e["settlement"]) for e in report["ledger"]] == [
        ("QQQ", 50.0, 45.0), ("SPY", 100.0, 97.0)]
    qqq, spy = report["ledger"][0]["books"]["model"], report["ledger"][1]["books"]["model"]
    assert qqq["strikes"] == [46.0, 47.0, 53.0, 54.0] and spy["strikes"] == MODEL_STRIKES
    assert qqq["american_charge_usd"] == pytest.approx(50.0) and spy["american_charge_usd"] == 0.0
    assert qqq["pnl_usd"] == pytest.approx(77.4 - 100.0 - 50.0)
    assert spy["pnl_usd"] == pytest.approx(MODEL_CREDIT)
    assert metrics["model_n_trades"] == 2 and metrics["n_rows_in_split"] == 2
    assert metrics["model_american_charge_usd"] == pytest.approx(50.0)  # the SUM over trades
    assert metrics["model_total_pnl_usd"] == pytest.approx(MODEL_CREDIT - 72.6)


def test_input_order_of_forecasts_and_closes_never_changes_the_ledger():
    days = ["2024-03-01", "2024-03-04", "2024-03-08", "2024-03-11"]
    chain = (_chain("2024-03-01", "2024-03-08") + _chain("2024-03-04", "2024-03-11")
             + _chain("2024-03-08", "2024-03-15") + _chain("2024-03-11", "2024-03-18"))
    series = _series([(d, 100.0) for d in _weekdays("2024-03-01", "2024-03-20")])
    forward_metrics, forward = _run([_row(d) for d in days], chain, series)
    metrics, report = _run([_row(d) for d in reversed(days)], list(reversed(chain)),
                           list(reversed(series)))
    assert [e["date"] for e in report["ledger"]] == ["2024-03-01", "2024-03-08"]
    assert report["ledger"] == forward["ledger"] and metrics == forward_metrics
    assert report["chain"] == {"first_date": "2024-03-01", "last_date": "2024-03-11", "n_rows": 200}
    _, shuffled = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), list(reversed(FLAT)))
    assert shuffled["ledger"][0]["settlement"] == 97.0


def test_a_bad_or_repeated_underlying_row_refuses():
    with pytest.raises(ValueError, match="underlying row"):
        _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT + [{"instrument": "SPY", "date": "2024-03-13",
                                                            "close": 0.0, "asof_ms": 1}])
    with pytest.raises(ValueError, match="repeats a date"):
        _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT + [dict(FLAT[0])])
    with pytest.raises(ValueError, match="lacks"):
        chain = _chain(ENTRY, EXPIRY)
        del chain[0]["iv"]
        _run([_row(ENTRY)], chain, FLAT)


def test_a_series_that_starts_after_the_settlement_date_is_unsettled():
    late = _series([("2024-03-11", 100.0), ("2024-03-12", 100.0)])
    metrics, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), late)
    assert metrics["n_skipped_unsettled"] == 1 and report["ledger"] == []


def test_a_chain_at_exactly_dte_min_is_accepted_and_one_below_refuses():
    _, report = _run([_row(ENTRY)], _chain(ENTRY, "2024-03-06"), FLAT)  # Wednesday: DTE 5
    entry = report["ledger"][0]
    assert (entry["dte"], entry["sessions"]) == (5, 3)
    assert entry["horizon_scale"] == pytest.approx(0.05 * math.sqrt(3 / 5))
    with pytest.raises(ValueError, match="dte"):
        _run([_row(ENTRY)], _chain(ENTRY, "2024-03-05"), FLAT)  # Tuesday: DTE 4


def test_the_charge_window_ends_on_the_settlement_date_not_the_occ_expiry():
    # Saturday expiry 03-09 settles Friday 03-08; the put goes in the money on 03-04, so the
    # carry runs 4 days (to the settlement date), not 5 (to the OCC expiry)
    closes = [("2024-03-01", 100.0), ("2024-03-04", 94.0), ("2024-03-05", 100.0),
              ("2024-03-06", 100.0), ("2024-03-07", 100.0), ("2024-03-08", 100.0),
              ("2024-03-11", 100.0)]
    series = _series(closes)
    _, report = _run([_row(ENTRY)], _chain(ENTRY, "2024-03-09", settle="2024-03-08"), series)
    charge = report["ledger"][0]["books"]["model"]["american_charge_usd"]
    assert charge == pytest.approx(95.0 * (math.exp(0.055 * 4 / 365) - 1) * 100)
    assert charge == american_short_charge(series, 95.0, 106.0, ENTRY, "2024-03-08", 0.055, 100)["total_usd"]
    assert charge != pytest.approx(
        american_short_charge(series, 95.0, 106.0, ENTRY, "2024-03-09", 0.055, 100)["total_usd"])


def test_mean_credit_averages_traded_cells_only():
    # SPY's model book (E = 157.4) clears a 100 USD gate, QQQ's (E = 77.4) does not; the
    # always book trades both: mean credit (157.4 + 77.4) / 2 = 117.4
    qqq_chain = _set(_chain(ENTRY, EXPIRY, close=50.0, strikes=range(44, 57), instrument="QQQ"),
                     put47={"bid": 1.0, "ask": 1.1}, call53={"bid": 1.0, "ask": 1.1})
    qqq_series = [{"instrument": "QQQ", "date": d, "close": 50.0, "asof_ms": _ms(d),
                   "dividend_amount": 0.0} for d in SESSIONS]
    rows = [_row(ENTRY), _row(ENTRY, close=50.0, instrument="QQQ")]
    metrics, _ = _run(rows, _chain(ENTRY, EXPIRY) + qqq_chain, FLAT + qqq_series,
                      min_edge_usd=100.0)
    assert metrics["model_n_trades"] == 1 and metrics["model_n_skipped_below_min_edge"] == 1
    assert metrics["model_mean_credit_usd"] == pytest.approx(MODEL_CREDIT)
    assert metrics["always_n_trades"] == 2
    assert metrics["always_mean_credit_usd"] == pytest.approx((MODEL_CREDIT + 77.4) / 2)
    # the mean P&L and hit rate are over TRADED cells too: both settle inside the shorts
    assert metrics["model_mean_pnl_usd"] == pytest.approx(MODEL_CREDIT)
    assert metrics["model_hit_rate"] == 1.0
    assert metrics["always_mean_pnl_usd"] == pytest.approx((MODEL_CREDIT + 77.4) / 2)
    assert metrics["always_hit_rate"] == 1.0


@pytest.mark.parametrize("draws, strikes", [
    # Q(0.1) = +0.5: the long put target is exactly S = 100, a listed strike -> 100 (at or
    # below); the short put e^{0.025} = 102.53 -> 102; calls as the worked entry
    ([0.5] * 2 + [1.0] * 8, [100.0, 102.0, 106.0, 108.0]),
    # Q(0.9) = -0.5: the long call target is exactly 100 -> 100 (at or above); the short call
    # e^{-0.025} = 97.53 -> 98
    ([-1.0] * 8 + [-0.5] * 2, [92.0, 95.0, 98.0, 100.0]),
])
def test_a_target_exactly_on_a_listed_strike_snaps_to_that_strike(draws, strikes):
    _, report = _run([_row(ENTRY, samples=list(draws))], _chain(ENTRY, EXPIRY), FLAT)
    assert report["ledger"][0]["books"]["model"]["strikes"] == strikes


def test_the_next_entry_may_start_on_the_settlement_date_of_a_saturday_expiry():
    chain = _chain(ENTRY, "2024-03-09", settle="2024-03-08") + _chain("2024-03-08", "2024-03-15")
    series = _series([(d, 100.0) for d in _weekdays("2024-03-01", "2024-03-20")])
    _, report = _run([_row(ENTRY), _row("2024-03-08")], chain, series)
    assert [e["date"] for e in report["ledger"]] == ["2024-03-01", "2024-03-08"]


def _three_trades(s1, s2, s3):
    """Three non-overlapping worked entries (five sessions each) settling at s1 / s2 / s3."""
    days = ["2024-03-01", "2024-03-11", "2024-03-19"]
    chain = (_chain("2024-03-01", "2024-03-08") + _chain("2024-03-11", "2024-03-18")
             + _chain("2024-03-19", "2024-03-26"))
    closes = {d: 100.0 for d in _weekdays("2024-03-01", "2024-03-29")}
    closes.update({"2024-03-08": s1, "2024-03-18": s2, "2024-03-26": s3})
    return [_row(d) for d in days], chain, _series(sorted(closes.items()))


def test_cvar_takes_the_declared_tail_and_the_drawdown_follows_time_order():
    # settlements 80 / 93.5 / 97 through the worked strikes: P&L -142.6 / +7.4 / +157.4
    # (the 157.4 credit less 300, 150 and 0; no close inside a window is below the short
    # put, so no carry). At cvar_alpha 0.5 the tail holds ceil(1.5) = 2 values:
    # (-142.6 + 7.4) / 2 = -67.6, not the worst trade. Mean 22.2 / 3 = 7.4, hit rate 2/3,
    # drawdown 142.6 from the start.
    rows, chain, series = _three_trades(80.0, 93.5, 97.0)
    metrics, report = _run(rows, chain, series, cvar_alpha=0.5)
    assert [e["books"]["model"]["pnl_usd"] for e in report["ledger"]] == pytest.approx(
        [-142.6, 7.4, 157.4])
    assert metrics["model_n_trades"] == 3
    assert metrics["model_cvar_usd"] == pytest.approx(-67.6)
    assert metrics["model_mean_pnl_usd"] == pytest.approx(7.4)
    assert metrics["model_hit_rate"] == pytest.approx(2 / 3)
    assert metrics["model_total_pnl_usd"] == pytest.approx(22.2)
    assert metrics["model_max_drawdown_usd"] == pytest.approx(142.6)
    # loss, small win, loss: the path falls 142.6, recovers 7.4 and falls again, 277.8 from
    # the start; a time-blind (sorted) path would report 285.2. The default 0.95 tail of
    # three trades is one value, the worst.
    rows, chain, series = _three_trades(80.0, 93.5, 80.0)
    metrics, _ = _run(rows, chain, series)
    assert metrics["model_max_drawdown_usd"] == pytest.approx(277.8)
    assert metrics["model_cvar_usd"] == pytest.approx(-142.6)
    assert metrics["model_hit_rate"] == pytest.approx(1 / 3)


# -- ADR-0193: the wing split, the put-spread book and the delta-matched benchmark -------------
#
# Every expected value is restated by hand. The worked chain: the model strikes are
# [92, 95, 106, 108] (put side 92/95, call side 106/108), the implied [95, 96, 104, 106].
#   model  put side  (2.0 - 1.1) * 100 - 2 * 0.65 = 88.7   call side (1.5 - 0.8) * 100 - 1.3 = 68.7
#   implied put side (2.4 - 2.2) * 100 - 1.3      = 18.7   call side (2.1 - 1.6) * 100 - 1.3 = 48.7

MODEL_PUT_CREDIT, MODEL_CALL_CREDIT = 88.7, 68.7
IMPLIED_PUT_CREDIT, IMPLIED_CALL_CREDIT = 18.7, 48.7


def _carry(strike, days):
    """The put carry on ``strike`` for ``days`` calendar days at the worked 5.5%, x 100."""
    return strike * (math.exp(0.055 * days / 365) - 1) * 100


def test_the_worked_entry_splits_into_its_two_wings():
    metrics, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT)
    books = report["ledger"][0]["books"]
    for book, put, call in (("model", MODEL_PUT_CREDIT, MODEL_CALL_CREDIT),
                            ("always", MODEL_PUT_CREDIT, MODEL_CALL_CREDIT),
                            ("implied", IMPLIED_PUT_CREDIT, IMPLIED_CALL_CREDIT)):
        assert books[book]["side_pnl_usd"] == pytest.approx({"put": put, "call": call})
        assert list(books[book]["side_pnl_usd"]) == ["put", "call"]
        assert metrics[f"{book}_put_total_pnl_usd"] == pytest.approx(put)
        assert metrics[f"{book}_call_total_pnl_usd"] == pytest.approx(call)
        assert metrics[f"{book}_put_mean_pnl_usd"] == pytest.approx(put)
        assert metrics[f"{book}_call_mean_pnl_usd"] == pytest.approx(call)
        assert metrics[f"{book}_put_hit_rate"] == 1.0 == metrics[f"{book}_call_hit_rate"]


@pytest.mark.parametrize("level, model, implied", [
    # (put side, call side) of each book, settled at `level`
    (80.0, (88.7 - 300.0, 68.7), (18.7 - 100.0, 48.7)),     # through every put wing
    (93.5, (88.7 - 150.0, 68.7), (18.7 - 100.0, 48.7)),     # short put 95 -1.5 (long 92 out); 96/95 -1.0
    (110.0, (88.7, 68.7 - 200.0), (18.7, 48.7 - 200.0)),    # through every call wing
])
def test_a_losing_settlement_lands_on_the_wing_that_lost(level, model, implied):
    _, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), _settled_at(level))
    books = report["ledger"][0]["books"]
    for book, (put, call) in (("model", model), ("always", model), ("implied", implied)):
        assert books[book]["side_pnl_usd"]["put"] == pytest.approx(put)
        assert books[book]["side_pnl_usd"]["call"] == pytest.approx(call)


def test_each_wing_carries_its_own_american_charge():
    # ex-date 03-07 (1.0); the 03-06 close 107 is above both short calls (106, 104) -> 100 on
    # the call side of every book; the 03-07 close 94 is below both short puts (95, 96) ->
    # one day of carry on that book's own short put; settled at 97 with nothing else owing
    dividends = [("2024-03-07", 1.0)]
    closes = [(d, 100.0) for d in SESSIONS[:3]] + [("2024-03-06", 107.0), ("2024-03-07", 94.0),
                                                     ("2024-03-08", 97.0), ("2024-03-11", 98.0)]
    _, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), _series(closes, dividends))
    books = report["ledger"][0]["books"]
    model_put = MODEL_PUT_CREDIT - _carry(95.0, 1)
    assert books["model"]["side_pnl_usd"]["put"] == pytest.approx(model_put)
    assert books["model"]["side_pnl_usd"]["call"] == pytest.approx(MODEL_CALL_CREDIT - 100.0)
    assert books["implied"]["side_pnl_usd"]["put"] == pytest.approx(
        IMPLIED_PUT_CREDIT - _carry(96.0, 1))
    assert books["implied"]["side_pnl_usd"]["call"] == pytest.approx(IMPLIED_CALL_CREDIT - 100.0)
    # the cell's own charge is still the composite: both wings, summed
    assert books["model"]["american_charge_usd"] == pytest.approx(100.0 + _carry(95.0, 1))


def test_the_wing_split_metrics_are_the_per_side_total_mean_and_hit_rate_over_the_trades():
    # settlements 80 / 93.5 / 97: the put side earns 88.7 less 300, 150 and 0, the call side
    # keeps 68.7 each time
    rows, chain, series = _three_trades(80.0, 93.5, 97.0)
    metrics, report = _run(rows, chain, series)
    puts = [88.7 - 300.0, 88.7 - 150.0, 88.7]
    for book in ("model", "always"):
        assert [e["books"][book]["side_pnl_usd"]["put"] for e in report["ledger"]] == \
            pytest.approx(puts)
        assert metrics[f"{book}_put_total_pnl_usd"] == pytest.approx(sum(puts))
        assert metrics[f"{book}_put_mean_pnl_usd"] == pytest.approx(sum(puts) / 3)
        assert metrics[f"{book}_put_hit_rate"] == pytest.approx(1 / 3)
        assert metrics[f"{book}_call_total_pnl_usd"] == pytest.approx(3 * 68.7)
        assert metrics[f"{book}_call_mean_pnl_usd"] == pytest.approx(68.7)
        assert metrics[f"{book}_call_hit_rate"] == 1.0
    # the sides add up to the book, side by side and in the totals
    for book in CondorQuoteBacktest.BOOKS:
        assert metrics[f"{book}_put_total_pnl_usd"] + metrics[f"{book}_call_total_pnl_usd"] == \
            pytest.approx(metrics[f"{book}_total_pnl_usd"])


def test_each_wing_hits_independently_of_the_other():
    # settlements 80 / 97 / 80: the put side loses twice (-211.3), the call side always wins
    rows, chain, series = _three_trades(80.0, 97.0, 80.0)
    metrics, _ = _run(rows, chain, series)
    assert metrics["model_put_hit_rate"] == pytest.approx(1 / 3)
    assert metrics["model_call_hit_rate"] == 1.0


def test_a_wing_that_earns_exactly_zero_is_not_a_hit_at_either_level():
    # ADR-0193 review B-M3: the model's put side is credit 1.0 (bid 2.0 less ask 1.0) x 100
    # with no fees, and a settlement at 94 takes the 95 short put 1 point in the money:
    # 100 - 100 = exactly 0.0 on the put wing. p > 0 is the one hit rule, side and book.
    chain = _set(_chain(ENTRY, EXPIRY), put92={"bid": 0.9, "ask": 1.0})
    metrics, report = _run([_row(ENTRY)], chain, _settled_at(94.0), fee_per_leg=0.0)
    cell = report["ledger"][0]["books"]["model"]
    assert cell["side_pnl_usd"]["put"] == 0.0
    assert metrics["model_put_hit_rate"] == 0.0
    assert metrics["model_put_mean_pnl_usd"] == 0.0 and metrics["model_put_total_pnl_usd"] == 0.0
    assert cell["side_pnl_usd"]["call"] > 0 and metrics["model_call_hit_rate"] == 1.0
    assert metrics["model_hit_rate"] == 1.0            # the book earns the call wing's credit
    # the same rule at book level: a whole book that earns exactly zero is no hit either
    spread_metrics, spread = _run_put_spread([_row(ENTRY)], chain, _settled_at(94.0),
                                             fee_per_leg=0.0)
    assert spread["ledger"][0]["books"]["model"]["pnl_usd"] == 0.0
    assert spread_metrics["model_hit_rate"] == 0.0 and spread_metrics["model_put_hit_rate"] == 0.0


def test_an_empty_book_reports_zero_for_every_side_and_benchmark_statistic():
    chain = _chain(ENTRY, EXPIRY, bid_size=0, ask_size=0)
    metrics, _ = _run([_row(ENTRY)], chain, FLAT)
    for book in CondorQuoteBacktest.BOOKS:
        assert metrics[f"{book}_n_trades"] == 0
        for stat in ("put_total_pnl_usd", "put_mean_pnl_usd", "put_hit_rate",
                     "call_total_pnl_usd", "call_mean_pnl_usd", "call_hit_rate",
                     "delta_equity_total_usd", "residual_mean_pnl_usd", "residual_t", "pnl_t",
                     "n_no_delta"):
            assert metrics[f"{book}_{stat}"] == 0.0, stat


@pytest.mark.parametrize("runner, sides", [(_run, ("put", "call")),
                                           (_run_put_spread, ("put",))])
def test_a_zero_trade_book_keeps_the_float_side_totals_and_the_int_book_total(runner, sides):
    # ADR-0195 (ADR-0194 review B-M1): ADR-0193 reported a side total as float 0.0 with no
    # trade; ADR-0194's shared helper made it int 0. Restored for the SIDES; the book-level total
    # keeps the int it has always been. Compared by type, since 0 == 0.0 hides the JSON change.
    chain = _chain(ENTRY, EXPIRY, bid_size=0, ask_size=0)
    metrics, _ = runner([_row(ENTRY)], chain, FLAT)
    for book in CondorQuoteBacktest.BOOKS:
        assert metrics[f"{book}_n_trades"] == 0
        for side in sides:
            assert type(metrics[f"{book}_{side}_total_pnl_usd"]) is float, (book, side)
            assert type(metrics[f"{book}_{side}_mean_pnl_usd"]) is float
        assert type(metrics[f"{book}_total_pnl_usd"]) is int and metrics[f"{book}_total_pnl_usd"] == 0
        assert type(metrics[f"{book}_mean_pnl_usd"]) is float


# -- the delta-matched benchmark ----------------------------------------------------------


def _forward_delta(right, strike, iv=0.2, dte=7, forward=100.0):
    """Black-76 forward delta at rate 0, written out independently of dskit."""
    sd = iv * math.sqrt(dte / 365)
    d1 = (math.log(forward / strike) + sd * sd / 2) / sd
    return NormalDist().cdf(d1) if right == "call" else -NormalDist().cdf(-d1)


def _structure_delta(legs, strikes, **kwargs):
    return sum(sign * _forward_delta(right, k, **kwargs)
               for (right, sign), k in zip(legs, strikes))


MODEL_DELTA = _structure_delta(CONDOR, MODEL_STRIKES)
IMPLIED_DELTA = _structure_delta(CONDOR, IMPLIED_STRIKES)
MODEL_PUT_SPREAD_DELTA = _structure_delta(PUT_SPREAD, MODEL_STRIKES[:2])


def test_the_benchmark_is_the_structures_delta_times_the_underlyings_move():
    # entered at 100, settled at 97, no ex-date: the stock leg earns delta x 100 x (97 - 100)
    _, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT)
    books = report["ledger"][0]["books"]
    for book, delta in (("model", MODEL_DELTA), ("always", MODEL_DELTA),
                        ("implied", IMPLIED_DELTA)):
        assert books[book]["delta"] == pytest.approx(delta)
        assert books[book]["equity_pnl_usd"] == pytest.approx(delta * 100 * -3.0)
    # a condor's short strangle is net short the stock only slightly: the sign is the legs'
    assert MODEL_DELTA != 0.0 and abs(MODEL_DELTA) < 0.1


def test_the_benchmark_adds_the_dividends_paid_after_entry_up_to_settlement():
    # a 1.0 ex-date on 03-07 lies in (03-01, 03-08]: the stock earns the move AND the dividend;
    # one on the entry day and one after settlement are not the holder's
    dividends = [("2024-03-01", 5.0), ("2024-03-07", 1.0), ("2024-03-11", 9.0)]
    closes = [(d, 100.0) for d in SESSIONS[:-3]] + [("2024-03-08", 97.0), ("2024-03-11", 98.0),
                                                     ("2024-03-12", 99.0)]
    _, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), _series(closes, dividends))
    cell = report["ledger"][0]["books"]["model"]
    assert cell["equity_pnl_usd"] == pytest.approx(MODEL_DELTA * 100 * (97.0 - 100.0 + 1.0))


def test_the_delta_uses_the_traded_legs_own_iv_and_calendar_days_over_365():
    chain = _chain(ENTRY, EXPIRY)
    for q in chain:
        q["iv"] = {("put", 92.0): 0.35, ("put", 95.0): 0.30, ("call", 106.0): 0.25,
                   ("call", 108.0): 0.28}.get((q["right"], q["strike"]), 0.2)
    _, report = _run([_row(ENTRY)], chain, FLAT)
    cell = report["ledger"][0]["books"]["model"]
    expected = (_forward_delta("put", 92.0, iv=0.35) - _forward_delta("put", 95.0, iv=0.30)
                - _forward_delta("call", 106.0, iv=0.25) + _forward_delta("call", 108.0, iv=0.28))
    assert cell["delta"] == pytest.approx(expected)
    # ... on dte 7, never the 5 sessions the strikes were scaled by
    on_sessions = _structure_delta(CONDOR, MODEL_STRIKES, dte=5)
    assert cell["delta"] != pytest.approx(on_sessions)


@pytest.mark.parametrize("bad", [None, 0.0, -0.2, float("nan")])
def test_a_traded_leg_without_a_usable_iv_leaves_no_benchmark_and_is_counted(bad):
    chain = _chain(ENTRY, EXPIRY)
    for q in chain:
        if q["right"] == "put" and q["strike"] == 92.0:   # the model's long put alone
            q["iv"] = bad
    metrics, report = _run([_row(ENTRY)], chain, FLAT)
    books = report["ledger"][0]["books"]
    for book in ("model", "always"):
        assert books[book]["entered"] is True
        assert books[book]["delta"] is None and books[book]["equity_pnl_usd"] is None
        assert metrics[f"{book}_n_no_delta"] == 1
        assert metrics[f"{book}_delta_equity_total_usd"] == 0.0
        assert metrics[f"{book}_residual_mean_pnl_usd"] == 0.0
        assert metrics[f"{book}_residual_t"] == 0.0
    # the implied book's legs (95, 96, 104, 106) all carry one
    assert books["implied"]["delta"] == pytest.approx(IMPLIED_DELTA)
    assert metrics["implied_n_no_delta"] == 0


def test_a_book_is_benchmarked_only_over_its_trades_with_a_delta():
    # two instruments; SPY's long put loses its iv, QQQ's legs are whole: the residual mean
    # and the equity total read QQQ alone while the P&L totals read both
    qqq_chain = _set(_chain(ENTRY, EXPIRY, close=50.0, strikes=range(44, 57), instrument="QQQ"),
                     put47={"bid": 1.0, "ask": 1.1}, call53={"bid": 1.0, "ask": 1.1})
    spy_chain = _chain(ENTRY, EXPIRY)
    for q in spy_chain:
        if q["right"] == "put" and q["strike"] == 92.0:
            q["iv"] = None
    qqq_series = [{"instrument": "QQQ", "date": d, "close": 50.0, "asof_ms": _ms(d),
                   "dividend_amount": 0.0} for d in SESSIONS]
    rows = [_row(ENTRY), _row(ENTRY, close=50.0, instrument="QQQ")]
    metrics, report = _run(rows, spy_chain + qqq_chain, FLAT + qqq_series)
    qqq, spy = report["ledger"]
    assert spy["books"]["model"]["equity_pnl_usd"] is None
    delta = _structure_delta(CONDOR, [46.0, 47.0, 53.0, 54.0], forward=50.0)
    equity = delta * 100 * (50.0 - 50.0)    # QQQ never moves
    assert qqq["books"]["model"]["equity_pnl_usd"] == pytest.approx(equity)
    assert metrics["model_n_trades"] == 2 and metrics["model_n_no_delta"] == 1
    assert metrics["model_delta_equity_total_usd"] == pytest.approx(equity)
    assert metrics["model_residual_mean_pnl_usd"] == pytest.approx(
        qqq["books"]["model"]["pnl_usd"] - equity)


def _t(values):
    """The lags=0 t of a mean, written out: mean / (sd / sqrt(n)), sd with divisor n."""
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / n)
    return mean / (sd / math.sqrt(n))


def test_the_benchmark_metrics_are_the_residual_mean_and_the_two_t_statistics():
    rows, chain, series = _three_trades(80.0, 93.5, 97.0)
    metrics, report = _run(rows, chain, series)
    pnls = [e["books"]["model"]["pnl_usd"] for e in report["ledger"]]
    assert pnls == pytest.approx([-142.6, 7.4, 157.4])
    equity = [MODEL_DELTA * 100 * (level - 100.0) for level in (80.0, 93.5, 97.0)]
    residual = [p - e for p, e in zip(pnls, equity)]
    assert metrics["model_delta_equity_total_usd"] == pytest.approx(sum(equity))
    assert metrics["model_residual_mean_pnl_usd"] == pytest.approx(sum(residual) / 3)
    assert metrics["model_pnl_t"] == pytest.approx(_t(pnls))
    assert metrics["model_residual_t"] == pytest.approx(_t(residual))
    assert metrics["model_n_no_delta"] == 0
    # 7.4 / sqrt((150^2 + 0 + 150^2) / 3 / 3): the divisor-n standard deviation, not n - 1
    assert metrics["model_pnl_t"] == pytest.approx(7.4 / math.sqrt(15000 / 3))
    # every book is judged the same way, each on its own trades
    for book in ("always", "implied"):
        assert f"{book}_residual_t" in metrics and f"{book}_pnl_t" in metrics
    assert metrics["always_pnl_t"] == pytest.approx(metrics["model_pnl_t"])


def test_a_t_statistic_is_zero_with_fewer_than_two_trades_or_no_variance():
    metrics, _ = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT)   # one trade
    for book in CondorQuoteBacktest.BOOKS:
        assert metrics[f"{book}_n_trades"] == 1
        assert metrics[f"{book}_pnl_t"] == 0.0 and metrics[f"{book}_residual_t"] == 0.0
    rows, chain, series = _three_trades(97.0, 97.0, 97.0)   # three identical trades
    metrics, _ = _run(rows, chain, series)
    assert metrics["model_n_trades"] == 3
    assert metrics["model_pnl_t"] == 0.0 and metrics["model_residual_t"] == 0.0
    assert metrics["model_residual_mean_pnl_usd"] == pytest.approx(
        MODEL_CREDIT - MODEL_DELTA * 100 * -3.0)


def test_a_series_constant_to_float_noise_has_a_zero_t_not_a_rounding_artifact():
    # ADR-0193 review A-F1: three equal 1.7 credits summed in different orders differ in the
    # last bit, and mean / (sd / sqrt(n)) over that noise is ~1e16 — a sentinel, not a t.
    noisy = [1.7000000000000004, 1.7000000000000004, 1.7]
    assert max(noisy) - min(noisy) > 0.0      # the reproducer really is not exactly constant
    assert nodes.t_or_zero(noisy) == 0.0
    assert nodes.t_or_zero([-v for v in noisy]) == 0.0  # a losing book's noise, ADR-0195 B-M3
    assert nodes.t_or_zero([1.7, 1.7, 1.7]) == 0.0     # the exactly constant case stays zero
    assert nodes.t_or_zero([0.0, 0.0]) == 0.0
    # a spread far above float noise is real variance: one part in 1e6 of the magnitude
    real = [1.7 * (1 + 1e-6), 1.7, 1.7 * (1 - 1e-6)]
    assert nodes.t_or_zero(real) == pytest.approx(_t(real))
    assert abs(nodes.t_or_zero(real)) > 1.0
    assert nodes.t_or_zero([100.0, 100.01]) == pytest.approx(_t([100.0, 100.01]))   # small, not noise


def test_the_child_t_only_delegates_to_the_one_owner_of_the_no_variance_rule():
    # ADR-0195 (B-M3/B-M4): dskit.pipeline.stats owns the float-noise rule, so the child holds
    # no tolerance of its own; t_or_zero turns the owner's "no variance" (None) into the child's 0.0
    from dskit.pipeline.stats import newey_west_mean

    assert not hasattr(CondorQuoteBacktest, "CONSTANT_SERIES_RTOL")
    assert not hasattr(PutSpreadQuoteBacktest, "CONSTANT_SERIES_RTOL")
    for series in ([1.7000000000000004, 1.7000000000000004, 1.7], [-2.5, -2.5], [0.0, 0.0, 0.0],
                   [10.0, 12.0, 11.0, 30.0], [100.0, 100.01], [1e-9, 2e-9, 3e-9]):
        owner = newey_west_mean(list(series), lags=0)["t"]
        assert nodes.t_or_zero(series) == (0.0 if owner is None else owner)
    assert nodes.t_or_zero([5.0]) == 0.0 and nodes.t_or_zero([]) == 0.0


def test_the_mean_is_zero_when_undefined_and_the_two_rules_have_one_public_owner():
    # ADR-0196 review B-F1: the "mean, and lags-0 t, each 0.0 when undefined" rule is used by the
    # backtests' metrics AND by the ledger studies' tables, so it is ONE public pair of module
    # functions in nodes.py, never a protected method another module reaches into
    from index_options import ledger_studies

    assert nodes.mean_or_zero([]) == 0.0 and nodes.mean_or_zero([2.0, 4.0]) == 3.0
    assert nodes.mean_or_zero([True, False, True, True]) == 0.75           # a hit rate is this mean
    assert {"mean_or_zero", "t_or_zero"} <= set(nodes.__all__)
    assert ledger_studies.mean_or_zero is nodes.mean_or_zero
    assert ledger_studies.t_or_zero is nodes.t_or_zero
    for cls in (nodes._CondorBacktestBase, CondorQuoteBacktest, PutSpreadQuoteBacktest,
                PayoffSelectQuoteBacktest):
        assert not hasattr(cls, "_t") and not hasattr(cls, "_mean"), cls.__name__
    source = Path(ledger_studies.__file__).read_text(encoding="utf-8")
    assert "._t(" not in source and "._mean(" not in source


def test_the_readme_names_the_owners_tolerance_and_states_no_second_literal():
    # ADR-0195 (B-M3): a number written in prose that no test ties to the constant drifts; the
    # README names the constant and the rule, and never spells the value
    from dskit.pipeline.stats import NO_VARIANCE_RTOL

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    assert "NO_VARIANCE_RTOL" in readme and "CONSTANT_SERIES_RTOL" not in readme
    assert f"{NO_VARIANCE_RTOL:g}" not in readme and repr(NO_VARIANCE_RTOL) not in readme


def test_only_the_trades_with_a_delta_enter_the_residual_t():
    # the middle entry has no iv on its long put: the residual t is over the other two
    rows, chain, series = _three_trades(80.0, 93.5, 97.0)
    for q in chain:
        if q["date"] == "2024-03-11" and q["right"] == "put" and q["strike"] == 92.0:
            q["iv"] = None
    metrics, report = _run(rows, chain, series)
    pnls = [e["books"]["model"]["pnl_usd"] for e in report["ledger"]]
    equity = {0: MODEL_DELTA * 100 * -20.0, 2: MODEL_DELTA * 100 * -3.0}
    residual = [pnls[k] - equity[k] for k in (0, 2)]
    assert metrics["model_n_no_delta"] == 1
    assert metrics["model_residual_t"] == pytest.approx(_t(residual))
    assert metrics["model_residual_mean_pnl_usd"] == pytest.approx(sum(residual) / 2)
    assert metrics["model_pnl_t"] == pytest.approx(_t(pnls))  # the P&L t reads every trade


# -- PutSpreadQuoteBacktest ----------------------------------------------------------------


def test_the_put_spread_is_a_condor_quote_backtest_with_the_put_wing_as_its_legs():
    cls = PutSpreadQuoteBacktest
    assert issubclass(cls, CondorQuoteBacktest)
    assert CondorQuoteBacktest.LEGS == CONDOR and cls.LEGS == PUT_SPREAD
    # SIDES is the distinct rights of LEGS, in leg order: derived, never a second list
    for node_cls in (CondorQuoteBacktest, cls):
        assert node_cls.SIDES == tuple(dict.fromkeys(right for right, _ in node_cls.LEGS))
    assert CondorQuoteBacktest.SIDES == ("put", "call") and cls.SIDES == ("put",)
    # the same books, reasons and knobs: nothing the condor declares is restated
    assert cls.BOOKS == CondorQuoteBacktest.BOOKS
    assert cls.ROW_REASONS == CondorQuoteBacktest.ROW_REASONS
    assert cls.BOOK_REASONS == CondorQuoteBacktest.BOOK_REASONS
    assert cls._PARAMS == CondorQuoteBacktest._PARAMS
    assert cls.serving_effect(PARAMS, {}) == "forbidden" and cls.role == "score"


def test_the_put_spread_refuses_the_condors_bad_knobs_and_needs_its_required_ones():
    cls = PutSpreadQuoteBacktest
    for change in ({"split": "nope"}, {"short_q": 0.5}, {"wing_z": 0}, {"wing_points": 25},
                   {"multiplier": 0}, {"carry_rate": -0.01}, {"hold_steps": 21}):
        with pytest.raises(ConfigError, match=next(iter(change))):
            cls("bt", {**PARAMS, **change})
    for missing in PARAMS:
        with pytest.raises(ConfigError, match=missing):
            cls("bt", {k: v for k, v in PARAMS.items() if k != missing})
    assert cls.validate_params(dict(PARAMS)) == []


def test_the_put_spread_report_names_itself_and_is_never_decision_eligible():
    _, report = _run_put_spread([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT)
    assert report["kind"] == "archived_quote_put_spread_backtest"
    assert report["pricing"] == "archived_eod_quotes" and report["decision_eligible"] is False
    assert "put spread" in report["units"] and "condor" not in report["units"]
    assert report["params"]["short_q"] == 0.1
    _, condor = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT)
    assert condor["kind"] == "archived_quote_condor_backtest"


@pytest.mark.parametrize("series", [
    FLAT, _settled_at(80.0), _settled_at(93.5), _settled_at(110.0),
    _series([(d, 100.0) for d in SESSIONS[:3]] + [("2024-03-06", 107.0), ("2024-03-07", 94.0),
                                                   ("2024-03-08", 97.0), ("2024-03-11", 98.0)],
            [("2024-03-07", 1.0)]),
], ids=["flat", "put-wing-lost", "inside-the-put-wing", "call-wing-lost", "dividend-and-carry"])
def test_where_both_enter_at_the_same_strikes_the_put_spread_is_the_condors_put_side(series):
    _, condor = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), series)
    _, spread = _run_put_spread([_row(ENTRY)], _chain(ENTRY, EXPIRY), series)
    for book in CondorQuoteBacktest.BOOKS:
        c, p = condor["ledger"][0]["books"][book], spread["ledger"][0]["books"][book]
        assert c["entered"] and p["entered"]
        assert p["strikes"] == c["strikes"][:2]
        assert abs(p["pnl_usd"] - c["side_pnl_usd"]["put"]) <= 1e-9, book
        assert abs(p["side_pnl_usd"]["put"] - p["pnl_usd"]) <= 1e-9
        assert p["credit_usd"] == pytest.approx(
            IMPLIED_PUT_CREDIT if book == "implied" else MODEL_PUT_CREDIT)


def test_the_put_spread_charges_only_the_put_carry_never_the_call_dividend():
    dividends = [("2024-03-07", 1.0)]
    closes = [(d, 100.0) for d in SESSIONS[:3]] + [("2024-03-06", 107.0), ("2024-03-07", 94.0),
                                                     ("2024-03-08", 97.0), ("2024-03-11", 98.0)]
    metrics, report = _run_put_spread([_row(ENTRY)], _chain(ENTRY, EXPIRY),
                                      _series(closes, dividends))
    cell = report["ledger"][0]["books"]["model"]
    assert cell["american_charge_usd"] == pytest.approx(_carry(95.0, 1))   # no 100 dividend
    assert cell["pnl_usd"] == pytest.approx(MODEL_PUT_CREDIT - _carry(95.0, 1))
    assert metrics["model_american_charge_usd"] == pytest.approx(_carry(95.0, 1))


def _call_side_unquotable():
    chain = _chain(ENTRY, EXPIRY)
    for q in chain:
        if q["right"] == "call":
            q["bid_size"] = q["ask_size"] = 0
    return chain


def test_where_the_call_side_is_unquotable_the_condor_skips_and_the_put_spread_enters():
    metrics, report = _run([_row(ENTRY)], _call_side_unquotable(), FLAT)
    for book in CondorQuoteBacktest.BOOKS:
        assert metrics[f"{book}_n_trades"] == 0
        assert metrics[f"{book}_n_skipped_no_quotable_strike"] == 1
    metrics, report = _run_put_spread([_row(ENTRY)], _call_side_unquotable(), FLAT)
    books = report["ledger"][0]["books"]
    assert books["model"]["strikes"] == [92.0, 95.0] == books["always"]["strikes"]
    assert books["implied"]["strikes"] == [95.0, 96.0]   # the condor's implied short put
    assert books["model"]["credit_usd"] == pytest.approx(MODEL_PUT_CREDIT)
    assert books["implied"]["credit_usd"] == pytest.approx(IMPLIED_PUT_CREDIT)
    for book in CondorQuoteBacktest.BOOKS:
        assert metrics[f"{book}_n_trades"] == 1 and books[book]["side_pnl_usd"] is not None
        assert metrics[f"{book}_put_total_pnl_usd"] == metrics[f"{book}_total_pnl_usd"]
        assert f"{book}_call_total_pnl_usd" not in metrics   # a put spread has no call side


def test_the_put_spreads_band_is_checked_on_its_own_exponents_only():
    chain = _chain(ENTRY, EXPIRY, strikes=range(88, 117))
    for draws, band, spread_enters in (
        (CALL_HEAVY, 0.09, True), (CALL_HEAVY, 0.11, True),   # only the CALLS exceed
        (PUT_HEAVY, 0.09, False), (PUT_HEAVY, 0.11, False),   # the puts exceed
    ):
        _, condor = _run([_row(ENTRY, samples=list(draws))], chain, FLAT,
                         max_abs_log_moneyness=band)
        _, spread = _run_put_spread([_row(ENTRY, samples=list(draws))], chain, FLAT,
                                    max_abs_log_moneyness=band)
        assert condor["ledger"][0]["books"]["model"]["reason"] == "target_outside_band"
        cell = spread["ledger"][0]["books"]["model"]
        assert cell["entered"] is spread_enters
        if not spread_enters:
            assert cell["reason"] == "target_outside_band"
        else:
            assert cell["strikes"] == [92.0, 95.0]


def test_the_put_spread_classifies_quotability_and_geometry_on_its_own_legs():
    # the put side unquotable: no_quotable_strike (the call side never consulted)
    chain = _chain(ENTRY, EXPIRY)
    for q in chain:
        if q["right"] == "put":
            q["bid_size"] = q["ask_size"] = 0
    metrics, _ = _run_put_spread([_row(ENTRY)], chain, FLAT)
    assert metrics["model_n_skipped_no_quotable_strike"] == 1 and metrics["model_n_trades"] == 0
    # a constant forecast puts BOTH shorts at 100: degenerate for the condor, fine for the spread
    flat = _set(_chain(ENTRY, EXPIRY), put100={"bid": 2.0, "ask": 2.2}, put97={"bid": 1.0, "ask": 1.1})
    rows = [_row(ENTRY, samples=[0.0] * 10)]
    metrics, _ = _run([*rows], flat, FLAT)
    assert metrics["model_n_skipped_degenerate_strikes"] == 1
    metrics, report = _run_put_spread(rows, flat, FLAT)
    assert metrics["model_n_skipped_degenerate_strikes"] == 0 and metrics["model_n_trades"] == 1
    assert report["ledger"][0]["books"]["model"]["strikes"] == [97.0, 100.0]


def test_the_put_spread_pays_two_legs_of_fees_and_judges_credit_against_its_own_width():
    # fees of 40 per leg: 90 - 80 = 10 > 0 on two legs; the condor's 160 - 160 = 0 is not
    metrics, _ = _run_put_spread([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT, fee_per_leg=40.0)
    assert metrics["model_n_trades"] == 1
    metrics, _ = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT, fee_per_leg=40.0)
    assert metrics["model_n_skipped_nonpositive_credit"] == 1
    # a put credit of 2.4 is under the put width 3 but the condor's 3.1 is over its call wing 2
    rich = _chain(ENTRY, EXPIRY)
    for q in rich:
        if q["right"] == "put" and q["strike"] == 95.0:
            q["bid"], q["ask"] = 3.5, 3.6
    _, condor = _run([_row(ENTRY)], rich, FLAT)
    assert condor["ledger"][0]["books"]["model"]["reason"] == "credit_not_below_width"
    _, spread = _run_put_spread([_row(ENTRY)], rich, FLAT)
    cell = spread["ledger"][0]["books"]["model"]
    assert cell["entered"] is True and cell["credit_usd"] == pytest.approx(240 - 1.3)
    # ... and reaching the put width 3 refuses: 4.5 - 1.1 = 3.4
    for q in rich:
        if q["right"] == "put" and q["strike"] == 95.0:
            q["bid"], q["ask"] = 4.5, 4.6
    metrics, _ = _run_put_spread([_row(ENTRY)], rich, FLAT)
    assert metrics["model_n_skipped_credit_not_below_width"] == 1


def test_the_put_spreads_model_book_expects_the_put_wings_payoff_only():
    # under the losing draws one of a hundred settles through the puts (-3 per share) and one
    # through the calls; the put spread expects 88.7 - 3.0 = 85.7, the condor 157.4 - 5.0
    rows = [_row(ENTRY, samples=list(LOSING_DRAWS))]
    _, spread = _run_put_spread(rows, _chain(ENTRY, EXPIRY), FLAT)
    assert spread["ledger"][0]["model_expected_pnl_usd"] == pytest.approx(85.7)
    metrics, _ = _run_put_spread(rows, _chain(ENTRY, EXPIRY), FLAT, min_edge_usd=86.0)
    assert metrics["model_n_skipped_below_min_edge"] == 1 and metrics["always_n_trades"] == 1
    metrics, _ = _run_put_spread(rows, _chain(ENTRY, EXPIRY), FLAT, min_edge_usd=85.0)
    assert metrics["model_n_trades"] == 1


def test_the_put_spreads_benchmark_is_its_own_delta_and_it_reads_the_dividends():
    _, report = _run_put_spread([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT)
    cell = report["ledger"][0]["books"]["model"]
    assert cell["delta"] == pytest.approx(MODEL_PUT_SPREAD_DELTA)
    assert MODEL_PUT_SPREAD_DELTA > 0     # a short put is long the stock
    assert cell["equity_pnl_usd"] == pytest.approx(MODEL_PUT_SPREAD_DELTA * 100 * -3.0)
    # a leg without an iv on the CALL side is nobody's business but the condor's
    chain = _chain(ENTRY, EXPIRY)
    for q in chain:
        if q["right"] == "call" and q["strike"] == 106.0:
            q["iv"] = None
    metrics, _ = _run_put_spread([_row(ENTRY)], chain, FLAT)
    assert metrics["model_n_no_delta"] == 0
    metrics, _ = _run([_row(ENTRY)], chain, FLAT)
    assert metrics["model_n_no_delta"] == 1
    # the benchmark reads the ex-date cash even where the put charge would not: a session
    # without a dividend_amount refuses, as it does for the condor's call charge
    series = _series([(d, 100.0) for d in SESSIONS])
    series[3]["dividend_amount"] = None
    with pytest.raises(ValueError, match="dividend_amount"):
        _run_put_spread([_row(ENTRY)], _chain(ENTRY, EXPIRY), series)


def test_a_put_spreads_ledger_and_metrics_match_a_condor_on_everything_they_share():
    rows, chain, series = _three_trades(80.0, 93.5, 97.0)
    c_metrics, condor = _run(rows, chain, series)
    p_metrics, spread = _run_put_spread(rows, chain, series)
    assert [e["date"] for e in condor["ledger"]] == [e["date"] for e in spread["ledger"]]
    for key in ("n_rows_in_split", "n_entries"):
        assert c_metrics[key] == p_metrics[key]
    for book in CondorQuoteBacktest.BOOKS:
        assert p_metrics[f"{book}_put_total_pnl_usd"] == pytest.approx(
            c_metrics[f"{book}_put_total_pnl_usd"])
        assert p_metrics[f"{book}_total_pnl_usd"] == pytest.approx(
            p_metrics[f"{book}_put_total_pnl_usd"])
        assert p_metrics[f"{book}_put_hit_rate"] == c_metrics[f"{book}_put_hit_rate"]


# -- ADR-0194: the entry-gate annotation --------------------------------------------------------
#
# gate_fields names forecast-row fields (bool or None) that the backtest READS at each entry and
# records; it never skips an entry. Per book and gate the traded cells split three ways: the gate
# CLOSED (True: it would have stood aside), OPEN (False) or UNKNOWN (None).

#: One worked entry a fortnight apart per level (Friday entry, Friday expiry), settling at it.
GATE_LEVELS = [80.0, 97.0, 93.5, 97.0, 110.0, 97.0]
GATE_A = [True, True, False, False, None, True]
GATE_B = [None] * 6
SUFFIXES = ("closed_n", "closed_mean_pnl_usd", "closed_t", "open_n", "open_mean_pnl_usd",
            "open_t", "unknown_n")
#: Each book's P&L per settlement level, restated by hand (no early exercise in these paths).
CONDOR_PNL = {"model": {80.0: -142.6, 93.5: 7.4, 97.0: 157.4, 110.0: -42.6},
              "implied": {80.0: -32.6, 93.5: -32.6, 97.0: 67.4, 110.0: -132.6}}
PUT_SPREAD_PNL = {"model": {80.0: -211.3, 93.5: -61.3, 97.0: 88.7, 110.0: 88.7},
                  "implied": {80.0: -81.3, 93.5: -81.3, 97.0: 18.7, 110.0: 18.7}}


def _trades(levels):
    """``(days, chain, closes)`` for one worked entry per level, a fortnight apart."""
    days = [date(2024, 3, 1) + timedelta(days=14 * k) for k in range(len(levels))]
    chain = [q for d in days
             for q in _chain(d.isoformat(), (d + timedelta(days=7)).isoformat())]
    closes = {d: 100.0 for d in _weekdays("2024-03-01",
                                          (days[-1] + timedelta(days=12)).isoformat())}
    closes.update({(d + timedelta(days=7)).isoformat(): level for d, level in zip(days, levels)})
    return [d.isoformat() for d in days], chain, _series(sorted(closes.items()))


def _gated_rows(days, **gates):
    return [_row(d, **{name: values[k] for name, values in gates.items()})
            for k, d in enumerate(days)]


def _group(pnls, gates, state):
    return [p for p, g in zip(pnls, gates) if g is state]


@pytest.mark.parametrize("cls, sides, table", [
    (CondorQuoteBacktest, ("put", "call"), CONDOR_PNL),
    (PutSpreadQuoteBacktest, ("put",), PUT_SPREAD_PNL),
])
def test_gate_fields_bucket_each_books_trades_by_the_gate_at_entry(cls, sides, table):
    days, chain, series = _trades(GATE_LEVELS)
    rows = _gated_rows(days, g_a=GATE_A, g_b=GATE_B)
    metrics, report = _run_node(cls, sides, rows, chain, series, gate_fields=["g_a", "g_b"])
    assert [e["gates"] for e in report["ledger"]] == [
        {"g_a": a, "g_b": None} for a in GATE_A]
    assert all(list(e["gates"]) == ["g_a", "g_b"] for e in report["ledger"])   # declared order
    assert report["params"]["gate_fields"] == ["g_a", "g_b"]
    for book, pnl_of in (("model", table["model"]), ("always", table["model"]),
                         ("implied", table["implied"])):
        pnls = [pnl_of[level] for level in GATE_LEVELS]
        assert metrics[f"{book}_n_trades"] == 6
        for state, label in ((True, "closed"), (False, "open")):
            group = _group(pnls, GATE_A, state)
            assert metrics[f"{book}_g_a_{label}_n"] == len(group)
            assert metrics[f"{book}_g_a_{label}_mean_pnl_usd"] == pytest.approx(
                sum(group) / len(group))
            assert metrics[f"{book}_g_a_{label}_t"] == pytest.approx(_t(group))
        assert metrics[f"{book}_g_a_unknown_n"] == 1
        # a gate that is unknown everywhere: every trade is unknown, both sides are empty
        assert metrics[f"{book}_g_b_unknown_n"] == 6
        for label in ("closed", "open"):
            assert metrics[f"{book}_g_b_{label}_n"] == 0
            assert metrics[f"{book}_g_b_{label}_mean_pnl_usd"] == 0.0
            assert metrics[f"{book}_g_b_{label}_t"] == 0.0
    # every gate metric carries the book and the field; nothing else is added
    added = {k for k in metrics if "_g_a_" in k or "_g_b_" in k}
    assert added == {f"{b}_{g}_{s}" for b in CondorQuoteBacktest.BOOKS
                     for g in ("g_a", "g_b") for s in SUFFIXES}


def test_the_hand_computed_gate_groups_of_the_condor_model_book():
    # the model book: levels 80 / 97 / 93.5 / 97 / 110 / 97 earn -142.6 / 157.4 / 7.4 / 157.4 /
    # -42.6 / 157.4. g_a closed on entries 0, 1, 5: (-142.6 + 157.4 + 157.4) / 3 = 57.4; open on
    # entries 2, 3: (7.4 + 157.4) / 2 = 82.4; entry 4 is unknown
    days, chain, series = _trades(GATE_LEVELS)
    metrics, _ = _run(_gated_rows(days, g_a=GATE_A), chain, series, gate_fields=["g_a"])
    assert metrics["model_g_a_closed_n"] == 3 and metrics["model_g_a_open_n"] == 2
    assert metrics["model_g_a_closed_mean_pnl_usd"] == pytest.approx(57.4)
    assert metrics["model_g_a_open_mean_pnl_usd"] == pytest.approx(82.4)
    assert metrics["model_g_a_unknown_n"] == 1
    # closed t: mean 57.4, sd with divisor n = sqrt(((-200)^2 + 100^2 + 100^2) / 3) = sqrt(20000)
    assert metrics["model_g_a_closed_t"] == pytest.approx(57.4 / (math.sqrt(20000.0) / math.sqrt(3)))
    # open t: [7.4, 157.4], mean 82.4, sd 75 -> 82.4 / (75 / sqrt(2))
    assert metrics["model_g_a_open_t"] == pytest.approx(82.4 / (75.0 / math.sqrt(2)))


def test_a_gate_group_with_no_variance_or_one_trade_has_a_zero_t():
    days, chain, series = _trades([97.0, 97.0, 97.0, 80.0])
    rows = _gated_rows(days, g=[True, True, True, False])
    metrics, _ = _run(rows, chain, series, gate_fields=["g"])
    assert metrics["model_g_closed_n"] == 3 and metrics["model_g_closed_t"] == 0.0
    assert metrics["model_g_open_n"] == 1 and metrics["model_g_open_t"] == 0.0
    assert metrics["model_g_open_mean_pnl_usd"] == pytest.approx(-142.6)


@pytest.mark.parametrize("cls, sides", [(CondorQuoteBacktest, ("put", "call")),
                                        (PutSpreadQuoteBacktest, ("put",))])
def test_annotating_never_changes_the_trades_and_absent_gate_fields_change_nothing(cls, sides):
    days, chain, series = _trades(GATE_LEVELS)
    plain_metrics, plain = _run_node(cls, sides, _gated_rows(days, g_a=GATE_A), chain, series)
    assert all("gates" not in e for e in plain["ledger"])
    assert "gate_fields" not in plain["params"]           # emitted only when present
    assert not any("_g_a_" in k for k in plain_metrics)
    metrics, gated = _run_node(cls, sides, _gated_rows(days, g_a=GATE_A), chain, series,
                               gate_fields=["g_a"])
    assert [{k: v for k, v in e.items() if k != "gates"} for e in gated["ledger"]] == \
        plain["ledger"]                                     # no entry skipped, none added
    assert {k: v for k, v in metrics.items() if "_g_a_" not in k} == plain_metrics
    assert {k: v for k, v in gated["params"].items() if k != "gate_fields"} == plain["params"]
    assert gated["chain"] == plain["chain"] and gated["kind"] == plain["kind"]


def test_a_book_that_did_not_trade_an_entry_does_not_count_its_gate():
    days, chain, series = _trades([80.0, 97.0, 97.0])
    rows = _gated_rows(days, g=[True, False, None])
    # the model book skips every entry (expected 157.4 does not beat 200); always trades them all
    metrics, report = _run(rows, chain, series, gate_fields=["g"], min_edge_usd=200.0)
    assert metrics["model_n_trades"] == 0 and metrics["always_n_trades"] == 3
    for suffix in SUFFIXES:
        assert metrics[f"model_g_{suffix}"] == 0, suffix       # every count, mean and t is zero
    assert [metrics[f"always_g_{s}"] for s in ("closed_n", "open_n", "unknown_n")] == [1, 1, 1]
    assert [e["gates"] for e in report["ledger"]] == [{"g": True}, {"g": False}, {"g": None}]


def test_each_entry_reads_the_gates_of_its_own_row_and_instrument():
    days, chain, series = _trades([97.0])
    qqq_chain = _set(_chain(days[0], "2024-03-08", close=50.0, strikes=range(44, 57),
                            instrument="QQQ"),
                     put47={"bid": 1.0, "ask": 1.1}, call53={"bid": 1.0, "ask": 1.1})
    qqq_series = [{"instrument": "QQQ", "date": d, "close": 50.0, "asof_ms": _ms(d),
                   "dividend_amount": 0.0} for d in SESSIONS]
    rows = [_row(days[0], g=True), _row(days[0], close=50.0, instrument="QQQ", g=False)]
    _, report = _run(rows, chain + qqq_chain, series + qqq_series, gate_fields=["g"])
    assert {e["instrument"]: e["gates"] for e in report["ledger"]} == {
        "SPY": {"g": True}, "QQQ": {"g": False}}


@pytest.mark.parametrize("bad", [1, 0, 1.0, "True", "", [True], {"g": True}])
def test_a_gate_value_that_is_not_a_bool_or_none_refuses_naming_the_field_and_the_row(bad):
    days, chain, series = _trades([97.0, 97.0])
    rows = _gated_rows(days, g=[True, bad])
    with pytest.raises(ValueError, match=rf"'g'.*SPY {days[1]}"):
        _run(rows, chain, series, gate_fields=["g"])


def test_a_gate_field_absent_from_an_entry_row_refuses_as_a_typo_and_a_none_is_unknown():
    days, chain, series = _trades([97.0])
    with pytest.raises(ValueError, match=rf"'g_typo'.*absent.*SPY {days[0]}"):
        _run(_gated_rows(days, g=[True]), chain, series, gate_fields=["g_typo"])
    metrics, _ = _run(_gated_rows(days, g=[None]), chain, series, gate_fields=["g"])
    assert metrics["model_g_unknown_n"] == 1


def test_a_gate_on_a_row_that_never_becomes_an_entry_is_not_read():
    # 03-04 lies inside the open position (passed over) and 03-11 has no chain (no_chain): a
    # malformed gate on either is no entry's gate, so nothing reads it
    days, chain, series = _trades([97.0])
    rows = _gated_rows(days, g=[True]) + [_row("2024-03-04", g="junk"), _row("2024-03-11", g="junk")]
    metrics, report = _run(rows, chain, series, gate_fields=["g"])
    assert metrics["n_skipped_no_chain"] == 1 and len(report["ledger"]) == 1
    assert metrics["model_g_closed_n"] == 1


#: The proxy backtest's declared knobs (the ADR-0182 smile constants, restated for this check).
_PROXY = {"split": "val", "hold_steps": 3, "short_q": 0.1, "wing_points": 25,
          "strike_increment": 5, "multiplier": 100, "fee_per_leg": 0.65, "atm_ratio": 0.794,
          "put_skew_per_z": 0.176, "call_skew_per_z": -0.064, "smile_curvature": 0.045,
          "iv_floor": 0.05, "iv_ceiling": 2.0, "half_spread_min": 0.0, "half_spread_frac": 0.0,
          "trading_days_per_year": 36}


@pytest.mark.parametrize("cls", [CondorQuoteBacktest, PutSpreadQuoteBacktest, CondorBacktest])
def test_every_backtest_accepts_gate_fields_and_refuses_a_malformed_declaration(cls):
    assert "gate_fields" in cls._PARAMS
    base = {**PARAMS} if cls is not CondorBacktest else dict(_PROXY)
    assert cls.validate_params({**base, "gate_fields": ["g"]}) == []
    assert cls.validate_params({**base, "gate_fields": ["g", "h"]}) == []
    for bad in ([], "g", ["g", "g"], [""], [1], ["g", None], None, ("g",), {"g": 1}, [True]):
        problems = cls.validate_params({**base, "gate_fields": bad})
        assert any("gate_fields" in p for p in problems), bad
        with pytest.raises(ConfigError, match="gate_fields"):
            cls("bt", {**base, "gate_fields": bad})


def test_the_proxy_backtest_annotates_its_entries_the_same_way():
    def row(i, **extra):
        return {"asof_ms": 100 + i, "instrument": "SPX", "date": f"d{i}", "close": 1000.0,
                "iv_index": 20.0, "reference_scale": 0.05, "samples": list(DRAWS),
                "outcome": 0.0, **extra}

    rows = [row(0, g=True), row(1), row(2), row(3, g=False), row(4), row(5), row(6, g=None)]
    node = CondorBacktest("bt", {**_PROXY, "gate_fields": ["g"]})
    out = node.run(CTX, {"forecasts": rows})
    ledger = out["report"].value["ledger"]
    assert [e["date"] for e in ledger] == ["d0", "d3", "d6"]
    assert [e["gates"] for e in ledger] == [{"g": True}, {"g": False}, {"g": None}]
    m = out["metrics"]
    assert (m["always_g_closed_n"], m["always_g_open_n"], m["always_g_unknown_n"]) == (1, 1, 1)
    plain = CondorBacktest("bt", dict(_PROXY)).run(CTX, {"forecasts": [
        {k: v for k, v in r.items() if k != "g"} for r in rows]})
    assert [{k: v for k, v in e.items() if k != "gates"} for e in ledger] == \
        plain["report"].value["ledger"]


# -- ADR-0195: one per-trade leg set changes nothing for the condor and the put spread ---------
#
# The refactor passes each trade's legs through _book / _gate / _settle / _side_pnl / _benchmark
# and folds the two _american_charge implementations into one rule; the condor and the put spread
# must come out exactly as ADR-0194 shipped them. The hand-derived tests above stay; these pin
# what the PRE-refactor code computed.

#: Frozen from the ADR-0194 code (da00b83) by running these very scenarios BEFORE the refactor:
#: per scenario/structure, a digest of the whole metrics dict and of the whole ledger (floats
#: rounded to 6 places, key order included) and, for the hand-readable ones, each cell's
#: (date, book, strikes, credit, charge, pnl, side split, delta, equity pnl, reason). One digest
#: (below_min_edge/put_spread, whose model book has no trade) is frozen with the float 0.0 side
#: totals of ADR-0193 that this ADR's B-M1 restores; every other value is da00b83's own.
FROZEN = {
    "three_trades/condor": {
        "metrics": "d12c7837836ec3074f6209a70cc0a5cc827edbb3359bc2a4e8f3b6e3a36ddeaf",
        "ledger": "0e95724a4b518ea04420533302e236fc6bb59e405744ee130360ef4679cf2d26",
        "cells": [
            ('2024-03-01', 'model', [92.0, 95.0, 106.0, 108.0], 157.4, 0.0, -142.6, {'put': -211.3, 'call': 68.7}, 0.014325789521853316, -28.651579043706633, None),
            ('2024-03-01', 'always', [92.0, 95.0, 106.0, 108.0], 157.4, 0.0, -142.6, {'put': -211.3, 'call': 68.7}, 0.014325789521853316, -28.651579043706633, None),
            ('2024-03-01', 'implied', [95.0, 96.0, 104.0, 106.0], 67.39999999999998, 0.0, -32.60000000000002, {'put': -81.30000000000003, 'call': 48.7}, -0.024738009771270664, 49.47601954254132, None),
            ('2024-03-11', 'model', [92.0, 95.0, 106.0, 108.0], 157.4, 0.0, 7.400000000000006, {'put': -61.30000000000001, 'call': 68.7}, 0.014325789521853316, -9.311763189204655, None),
            ('2024-03-11', 'always', [92.0, 95.0, 106.0, 108.0], 157.4, 0.0, 7.400000000000006, {'put': -61.30000000000001, 'call': 68.7}, 0.014325789521853316, -9.311763189204655, None),
            ('2024-03-11', 'implied', [95.0, 96.0, 104.0, 106.0], 67.39999999999998, 0.0, -32.60000000000002, {'put': -81.30000000000003, 'call': 48.7}, -0.024738009771270664, 16.07970635132593, None),
            ('2024-03-19', 'model', [92.0, 95.0, 106.0, 108.0], 157.4, 0.0, 157.4, {'put': 88.69999999999999, 'call': 68.7}, 0.014325789521853316, -4.297736856555995, None),
            ('2024-03-19', 'always', [92.0, 95.0, 106.0, 108.0], 157.4, 0.0, 157.4, {'put': 88.69999999999999, 'call': 68.7}, 0.014325789521853316, -4.297736856555995, None),
            ('2024-03-19', 'implied', [95.0, 96.0, 104.0, 106.0], 67.39999999999998, 0.0, 67.39999999999998, {'put': 18.69999999999997, 'call': 48.7}, -0.024738009771270664, 7.421402931381198, None),
        ],
    },
    "three_trades/put_spread": {
        "metrics": "b3ff8182049bd0057de9d7030b0e9a31bdc44631300b56bdd3da5d1c13c8e5ba",
        "ledger": "ad3dd9e9bfba2d38c52933c236479444d032fa14b44a50e00c47e78327d5b4d2",
        "cells": [
            ('2024-03-01', 'model', [92.0, 95.0], 88.69999999999999, 0.0, -211.3, {'put': -211.3}, 0.02978928060361996, -59.57856120723992, None),
            ('2024-03-01', 'always', [92.0, 95.0], 88.69999999999999, 0.0, -211.3, {'put': -211.3}, 0.02978928060361996, -59.57856120723992, None),
            ('2024-03-01', 'implied', [95.0, 96.0], 18.69999999999997, 0.0, -81.30000000000003, {'put': -81.30000000000003}, 0.037376320008478625, -74.75264001695726, None),
            ('2024-03-11', 'model', [92.0, 95.0], 88.69999999999999, 0.0, -61.30000000000001, {'put': -61.30000000000001}, 0.02978928060361996, -19.363032392352974, None),
            ('2024-03-11', 'always', [92.0, 95.0], 88.69999999999999, 0.0, -61.30000000000001, {'put': -61.30000000000001}, 0.02978928060361996, -19.363032392352974, None),
            ('2024-03-11', 'implied', [95.0, 96.0], 18.69999999999997, 0.0, -81.30000000000003, {'put': -81.30000000000003}, 0.037376320008478625, -24.294608005511108, None),
            ('2024-03-19', 'model', [92.0, 95.0], 88.69999999999999, 0.0, 88.69999999999999, {'put': 88.69999999999999}, 0.02978928060361996, -8.936784181085988, None),
            ('2024-03-19', 'always', [92.0, 95.0], 88.69999999999999, 0.0, 88.69999999999999, {'put': 88.69999999999999}, 0.02978928060361996, -8.936784181085988, None),
            ('2024-03-19', 'implied', [95.0, 96.0], 18.69999999999997, 0.0, 18.69999999999997, {'put': 18.69999999999997}, 0.037376320008478625, -11.212896002543587, None),
        ],
    },
    "dividend_and_carry/condor": {
        "metrics": "a650e4a5fa6ba45003587471116b080832941f9a4db9944c50792823b15c0135",
        "ledger": "a9d7b60457233d8dfde56b403f7cc41ee39c6c8985ee14abb59937bd6126e196",
        "cells": [
            ('2024-03-01', 'model', [92.0, 95.0, 106.0, 108.0], 157.4, 101.43161470798914, 55.968385292010865, {'put': 87.26838529201085, 'call': -31.299999999999997}, 0.014325789521853316, -2.865157904370663, None),
            ('2024-03-01', 'always', [92.0, 95.0, 106.0, 108.0], 157.4, 101.43161470798914, 55.968385292010865, {'put': 87.26838529201085, 'call': -31.299999999999997}, 0.014325789521853316, -2.865157904370663, None),
            ('2024-03-01', 'implied', [95.0, 96.0, 104.0, 106.0], 67.39999999999998, 101.44668433649429, -34.04668433649431, {'put': 17.253315663505685, 'call': -51.3}, -0.024738009771270664, 4.947601954254132, None),
        ],
    },
    "dividend_and_carry/put_spread": {
        "metrics": "e7a7655046ad7eed5debada72a923edfa89c611e8780536500abc63700ca4b60",
        "ledger": "3490762556d7a67b3a36f6d435636c9cfbf79bfbd92f18bf0f82eb65dd386fa6",
        "cells": [
            ('2024-03-01', 'model', [92.0, 95.0], 88.69999999999999, 1.4316147079891373, 87.26838529201085, {'put': 87.26838529201085}, 0.02978928060361996, -5.957856120723992, None),
            ('2024-03-01', 'always', [92.0, 95.0], 88.69999999999999, 1.4316147079891373, 87.26838529201085, {'put': 87.26838529201085}, 0.02978928060361996, -5.957856120723992, None),
            ('2024-03-01', 'implied', [95.0, 96.0], 18.69999999999997, 1.446684336494286, 17.253315663505685, {'put': 17.253315663505685}, 0.037376320008478625, -7.4752640016957255, None),
        ],
    },
    "gated_six/condor": {
        "metrics": "123861ea57f7793e72f4fec7d7fc00077871b059710decb508c6f28c7b210325",
        "ledger": "5fe6b92e2c95e1fd94efaae7b48a00c6759466b2d259bfd3f60caaac4d6bfef0",
    },
    "gated_six/put_spread": {
        "metrics": "880d132ecfff6503a4570d98e338a28df2994e76ffccb75bc09732dc3fde2457",
        "ledger": "a14da74f93ace071faa27e32ca922748f0902f47767224027a8f73772e145800",
    },
    "below_min_edge/condor": {
        "metrics": "82eab36b990142ffcb4887ff41437e9416306e591730d005ea410a8bd790fb33",
        "ledger": "0a00ea25404cfa4d31a6fba88278199799870d695051f11874705d68a124bdcf",
    },
    "below_min_edge/put_spread": {
        "metrics": "ced51aa1d21ba86256b90f13566b0f4dcc7f2be6cf06a97a9dc3bde9dabbe48d",
        "ledger": "51254cc95250b6b5bf3685f8601a0584688d7c73bd71040c507eff99f28b2b66",
        "cells": [
            ('2024-03-01', 'model', [92.0, 95.0], 88.69999999999999, None, None, None, None, None, 'below_min_edge'),
            ('2024-03-01', 'always', [92.0, 95.0], 88.69999999999999, 0.0, 88.69999999999999, {'put': 88.69999999999999}, 0.02978928060361996, -8.936784181085988, None),
            ('2024-03-01', 'implied', [95.0, 96.0], 18.69999999999997, 0.0, 18.69999999999997, {'put': 18.69999999999997}, 0.037376320008478625, -11.212896002543587, None),
        ],
    },
}


def _frozen_scenarios():
    """The scenarios FROZEN was computed from: name -> (forecasts, chain, closes, extra knobs)."""
    scenarios = {}
    rows, chain, series = _three_trades(80.0, 93.5, 97.0)
    scenarios["three_trades"] = (rows, chain, series, {})
    closes = [(d, 100.0) for d in SESSIONS[:3]] + [("2024-03-06", 107.0), ("2024-03-07", 94.0),
                                                     ("2024-03-08", 97.0), ("2024-03-11", 98.0)]
    scenarios["dividend_and_carry"] = ([_row(ENTRY)], _chain(ENTRY, EXPIRY),
                                       _series(closes, [("2024-03-07", 1.0)]), {})
    days, chain, series = _trades(GATE_LEVELS)
    scenarios["gated_six"] = (_gated_rows(days, g_a=GATE_A, g_b=GATE_B), chain, series,
                              {"gate_fields": ["g_a", "g_b"]})
    scenarios["below_min_edge"] = ([_row(ENTRY, samples=list(LOSING_DRAWS))],
                                   _chain(ENTRY, EXPIRY), FLAT, {"min_edge_usd": 86.0})
    return scenarios


def _rounded(value):
    """Floats to 6 places, recursively: a digest that survives a last-bit libm difference."""
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(v) for v in value]
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(_rounded(value), separators=(",", ":")).encode()).hexdigest()


def _same(got, want):
    """Structural equality with floats to 1e-12 and everything else exact."""
    if isinstance(want, float):
        return isinstance(got, float) and math.isclose(got, want, rel_tol=1e-12, abs_tol=1e-12)
    if isinstance(want, dict):
        return isinstance(got, dict) and list(got) == list(want) and all(
            _same(got[k], want[k]) for k in want)
    if isinstance(want, (list, tuple)):
        return len(got) == len(want) and all(_same(g, w) for g, w in zip(got, want))
    return got == want


@pytest.mark.parametrize("key", list(FROZEN))
def test_the_condor_and_the_put_spread_are_exactly_what_adr_0194_shipped(key):
    scenario, structure = key.split("/")
    rows, chain, series, over = _frozen_scenarios()[scenario]
    runner = _run if structure == "condor" else _run_put_spread
    metrics, report = runner(rows, chain, series, **over)
    frozen = FROZEN[key]
    assert _digest(metrics) == frozen["metrics"]
    assert _digest(report["ledger"]) == frozen["ledger"]
    if "cells" in frozen:
        cells = [(e["date"], book, c["strikes"], c["credit_usd"], c["american_charge_usd"],
                  c["pnl_usd"], c["side_pnl_usd"], c["delta"], c["equity_pnl_usd"], c["reason"])
                 for e in report["ledger"] for book, c in e["books"].items()]
        assert len(cells) == len(frozen["cells"])
        for got, want in zip(cells, frozen["cells"]):
            assert _same(got, want), (got, want)
    assert report["kind"].startswith("archived_quote_") and "select" not in report["kind"]
    assert all("selected" not in cell for e in report["ledger"] for cell in e["books"].values())


# -- ADR-0195: forecast-scored payoff selection --------------------------------------------------
#
# At each entry PayoffSelectQuoteBacktest snaps EVERY candidate (structure, short_q, wing_z) the
# forecast could trade, prices it at bid/ask, drops what the existing gates refuse and ranks the
# rest by score = E_P[pnl] / max_loss (E_P over the forecast draws at the horizon scale; max_loss =
# multiplier x the widest vertical less the credit). The worked chain, short_q 0.1, wing_z 0.5,
# draws whose Q(0.1) = -1 and Q(0.9) = +1 and a 5-session horizon (scale 0.05) give the strikes
# [92, 95] (put spread), [106, 108] (call spread) and [92, 95, 106, 108] (condor), so by hand:
#
#   structure     credit (USD)                          max loss (USD)
#   put spread    (2.0 - 1.1) x 100 - 2 x 0.65 = 88.7   3 x 100 - 88.7          = 211.3
#   call spread   (1.5 - 0.8) x 100 - 2 x 0.65 = 68.7   2 x 100 - 68.7          = 131.3
#   condor        88.7 + 68.7               = 157.4     max(3, 2) x 100 - 157.4 = 142.6
#
# With every draw settling between the shorts E_P = credit, and the condor wins (157.4 / 142.6):
# it borrows the other wing's credit for free, since only its wider wing can lose. Two variants
# make a SINGLE wing the better bet, by making the other wing thin AND the forecast's tail heavy
# on that thin wing (the 3.7 is a 0.05 per-share credit: 0.05 x 100 - 1.3):
#
#   THIN_CALL, RIGHT_HEAVY   E_call = 3.7 - 3 x 2.0 = -2.3    E_condor = 92.4 - 6 = 86.4
#   THIN_PUT,  LEFT_HEAVY    E_put  = 3.7 - 3 x 3.0 = -5.3    E_condor = 72.4 - 9 = 63.4

CALL_SPREAD = CONDOR[2:]
#: The three structures' legs, restated here (never read from contracts) and their sides.
STRUCTURE_LEGS = {"put_spread": PUT_SPREAD, "call_spread": CALL_SPREAD, "condor": CONDOR}
STRUCTURE_SIDES = {"put_spread": ("put",), "call_spread": ("call",), "condor": ("put", "call")}
SELECT_KNOBS = {"candidate_structures": ["put_spread", "call_spread", "condor"],
                "candidate_short_q": [0.1], "candidate_wing_z": [0.5]}
#: Both keep Q(0.1) = -1 and Q(0.9) = +1 (so the strikes stay put) but put 3 % of the draws
#: beyond one wing: z = +3 settles at 100 e^{0.15} = 116.2 (through the call wing, -2.0 a share)
#: and z = -3 at 86.1 (through the put wing, -3.0 a share).
RIGHT_HEAVY = [-1.0] * 10 + [0.0] * 79 + [1.0] * 8 + [3.0] * 3
LEFT_HEAVY = [-3.0] * 3 + [-1.0] * 7 + [0.0] * 79 + [1.0] * 11
THIN_CALL = {"call106": {"bid": 0.85}}      # call spread credit 0.85 - 0.8 = 0.05 a share
THIN_PUT = {"put95": {"bid": 1.15}}         # put spread credit 1.15 - 1.1 = 0.05 a share

#: (label, quote overrides, draws, {structure: (credit, E_P, max loss)}, winner), all by hand.
SELECT_CASES = [
    ("condor", {}, DRAWS,
     {"put_spread": (88.7, 88.7, 211.3), "call_spread": (68.7, 68.7, 131.3),
      "condor": (157.4, 157.4, 142.6)}, "condor"),
    ("put_spread", THIN_CALL, RIGHT_HEAVY,
     {"put_spread": (88.7, 88.7, 211.3), "call_spread": (3.7, 3.7 - 6.0, 196.3),
      "condor": (92.4, 92.4 - 6.0, 207.6)}, "put_spread"),
    ("call_spread", THIN_PUT, LEFT_HEAVY,
     {"put_spread": (3.7, 3.7 - 9.0, 296.3), "call_spread": (68.7, 68.7, 131.3),
      "condor": (72.4, 72.4 - 9.0, 227.6)}, "call_spread"),
]
SELECT_STRIKES = {"put_spread": [92.0, 95.0], "call_spread": [106.0, 108.0],
                  "condor": [92.0, 95.0, 106.0, 108.0]}


def _assert_the_selected_split(ledger):
    """ADR-0193 + ADR-0195 on EVERY select run: an entered cell's sides are exactly its own
    structure's, in leg order, and sum to its P&L; the implied book is the condor and has no
    ``selected``; a cell that did not enter has no split and no benchmark."""
    for entry in ledger:
        for book, cell in entry["books"].items():
            assert "selected" in cell, (entry["date"], book)
            assert (cell["selected"] is None) == (book == "implied" or cell["strikes"] is None)
            if cell["entered"]:
                structure = "condor" if cell["selected"] is None else cell["selected"]["structure"]
                assert tuple(cell["side_pnl_usd"]) == STRUCTURE_SIDES[structure]
                assert len(cell["strikes"]) == len(STRUCTURE_LEGS[structure])
                assert abs(sum(cell["side_pnl_usd"].values()) - cell["pnl_usd"]) <= 1e-9
            else:
                assert cell["side_pnl_usd"] is None
                assert cell["delta"] is None and cell["equity_pnl_usd"] is None


def _run_select(forecasts, chain, underlying, **over):
    node = PayoffSelectQuoteBacktest("bt", {**PARAMS, **SELECT_KNOBS, **over})
    out = node.run(CTX, {"forecasts": forecasts, "chain": chain, "underlying": underlying})
    _assert_the_selected_split(out["report"].value["ledger"])
    return out["metrics"], out["report"].value


def _quoted(quotes, day=ENTRY, expiry=EXPIRY, **chain_kwargs):
    """The worked chain with ``_set`` overrides applied."""
    return _set(_chain(day, expiry, **chain_kwargs), **quotes)


def _select_row(day=ENTRY, samples=DRAWS, **extra):
    return _row(day, samples=list(samples), **extra)


@pytest.mark.parametrize("label, quotes, draws, table, winner", SELECT_CASES,
                         ids=[c[0] for c in SELECT_CASES])
def test_each_structure_wins_in_turn_by_its_hand_computed_score(label, quotes, draws, table, winner):
    scores = {name: expected / max_loss for name, (_c, expected, max_loss) in table.items()}
    assert max(scores, key=scores.get) == winner == label     # the table itself picks the winner
    chain = _quoted(quotes)
    # every candidate ALONE reports its own hand-computed credit, E_P and score ...
    for name, (credit, expected, max_loss) in table.items():
        _, report = _run_select([_select_row(samples=draws)], chain, FLAT,
                                candidate_structures=[name])
        entry = report["ledger"][0]
        cell = entry["books"]["model"]
        assert cell["strikes"] == SELECT_STRIKES[name]
        assert cell["credit_usd"] == pytest.approx(credit)
        assert entry["model_expected_pnl_usd"] == pytest.approx(expected)
        assert cell["selected"] == {"structure": name, "short_q": 0.1, "wing_z": 0.5,
                                    "score": pytest.approx(scores[name]),
                                    "n_candidates_scored": 1}
        assert cell["entered"] is (expected > 0.0)          # the model book needs E_P > 0
        assert report["ledger"][0]["books"]["always"]["entered"] is True
    # ... and given all three the best score enters, in both the model and the always book
    metrics, report = _run_select([_select_row(samples=draws)], chain, FLAT)
    (entry,) = report["ledger"]
    for book in ("model", "always"):
        cell = entry["books"][book]
        assert cell["strikes"] == SELECT_STRIKES[winner] and cell["entered"] is True
        assert cell["selected"] == {"structure": winner, "short_q": 0.1, "wing_z": 0.5,
                                    "score": pytest.approx(scores[winner]),
                                    "n_candidates_scored": 3}
        assert cell["credit_usd"] == pytest.approx(table[winner][0])
        assert cell["pnl_usd"] == pytest.approx(table[winner][0])    # settled at 97: all worthless
        assert list(cell["side_pnl_usd"]) == list(STRUCTURE_SIDES[winner])
        assert metrics[f"{book}_n_trades"] == 1
        assert metrics[f"{book}_n_{winner}"] == 1
        assert sum(metrics[f"{book}_n_{name}"] for name in STRUCTURE_LEGS) == 1
    assert entry["model_expected_pnl_usd"] == pytest.approx(table[winner][1])
    assert entry["books"]["implied"]["selected"] is None            # the benchmark is unchanged
    assert entry["books"]["implied"]["strikes"] == IMPLIED_STRIKES
    assert metrics["implied_n_condor"] == 1
    assert metrics["implied_n_put_spread"] == metrics["implied_n_call_spread"] == 0


def test_the_same_chain_under_two_forecasts_selects_different_structures():
    # the forecast, not the market, decides: THIN_CALL prices are identical, only the tail differs.
    # Under DRAWS: put 88.7 / 211.3 = 0.4198, call 3.7 / 196.3 = 0.0188, condor 92.4 / 207.6 =
    # 0.4451 -> the condor. Under RIGHT_HEAVY the 3 % beyond the call wing costs the call side 6.0:
    # call -2.3 / 196.3, condor 86.4 / 207.6 = 0.4162 -> the put spread (0.4198).
    chain = _quoted(THIN_CALL)
    _, symmetric = _run_select([_select_row(samples=DRAWS)], chain, FLAT)
    _, skewed = _run_select([_select_row(samples=RIGHT_HEAVY)], chain, FLAT)
    a, b = symmetric["ledger"][0]["books"]["model"], skewed["ledger"][0]["books"]["model"]
    assert (a["selected"]["structure"], b["selected"]["structure"]) == ("condor", "put_spread")
    assert a["selected"]["score"] == pytest.approx(92.4 / 207.6)
    assert b["selected"]["score"] == pytest.approx(88.7 / 211.3)
    assert a["strikes"] == SELECT_STRIKES["condor"] and b["strikes"] == SELECT_STRIKES["put_spread"]


def test_a_tie_goes_to_the_earlier_declared_candidate():
    # q 0.10 and 0.12 both read Q = -1 from ten draws (the lower quantile takes index
    # ceil(10 q) - 1, i.e. 0 and 1, and the two lowest draws are both -1), so the two put spreads
    # are the SAME [92, 95] at the same quotes: an exact tie, and the earlier declared value is
    # the one recorded
    chain = _chain(ENTRY, EXPIRY)
    for order in ([0.12, 0.10], [0.10, 0.12]):
        _, report = _run_select([_select_row()], chain, FLAT, candidate_structures=["put_spread"],
                                candidate_short_q=order)
        cell = report["ledger"][0]["books"]["model"]
        assert cell["strikes"] == [92.0, 95.0]
        assert cell["selected"]["short_q"] == order[0]
        assert cell["selected"]["n_candidates_scored"] == 2
    # ... likewise the wing: 0.5 and 0.5001 snap to the same long put
    for order in ([0.5001, 0.5], [0.5, 0.5001]):
        _, report = _run_select([_select_row()], chain, FLAT, candidate_structures=["put_spread"],
                                candidate_wing_z=order)
        assert report["ledger"][0]["books"]["model"]["selected"]["wing_z"] == order[0]


def test_a_tie_between_structures_goes_to_the_structure_declared_first():
    # a put spread [92, 95] and a call spread [106, 109] (108 unquotable, so the long call snaps
    # out to 109): both width 3, both credit (2.0 - 1.0) = 1.0 a share in exact binary numbers,
    # both E_P = credit: the scores are EXACTLY equal
    chain = _quoted({"put95": {"bid": 2.0, "ask": 2.1}, "put92": {"bid": 0.9, "ask": 1.0},
                     "call106": {"bid": 2.0, "ask": 2.1}, "call109": {"bid": 0.9, "ask": 1.0},
                     "call108": {"bid_size": 0, "ask_size": 0}})
    for order in (["put_spread", "call_spread"], ["call_spread", "put_spread"]):
        _, report = _run_select([_select_row()], chain, FLAT, candidate_structures=order)
        cell = report["ledger"][0]["books"]["model"]
        assert cell["selected"]["structure"] == order[0]
        assert cell["strikes"] == ([92.0, 95.0] if order[0] == "put_spread" else [106.0, 109.0])
        assert cell["credit_usd"] == pytest.approx(98.7) and cell["selected"]["n_candidates_scored"] == 2
        assert cell["selected"]["score"] == pytest.approx(98.7 / (300 - 98.7))


def _smooth_market(day=ENTRY, expiry=EXPIRY, strikes=range(80, 121)):
    """A chain whose every vertical pays: mid 3.0 - 0.08 |K - 100| on both rights, +-0.05 wide.

    A short leg nearer the money than its long by g points earns 0.08 g - 0.1 a share, so any
    vertical two or more points wide has a positive credit, far under its width.
    """
    chain = _chain(day, expiry, strikes=strikes)
    for q in chain:
        mid = 3.0 - 0.08 * abs(q["strike"] - 100.0)
        q["bid"], q["ask"] = round(mid - 0.05, 4), round(mid + 0.05, 4)
    return chain


#: A hundred draws at the mid-quantiles of the standard normal: Q(0.1) = -1.28, Q(0.2) = -0.84 ...
NORMAL_DRAWS = [NormalDist().inv_cdf((i + 0.5) / 100) for i in range(100)]


def test_n_candidates_scored_counts_every_declared_candidate_that_survives_the_gates():
    # q {0.1, 0.2} x wing {0.5, 1.0} x three structures = 12 candidates on a market where every
    # vertical pays, so all twelve are scored; the quantiles differ so their strikes do too
    chain = _smooth_market()
    _, report = _run_select([_select_row(samples=NORMAL_DRAWS)], chain, FLAT,
                            candidate_short_q=[0.1, 0.2], candidate_wing_z=[0.5, 1.0])
    assert report["ledger"][0]["books"]["model"]["selected"]["n_candidates_scored"] == 12
    # narrowing the declaration narrows the count: 3 structures x 1 x 1
    _, report = _run_select([_select_row(samples=NORMAL_DRAWS)], chain, FLAT)
    assert report["ledger"][0]["books"]["model"]["selected"]["n_candidates_scored"] == 3


def test_a_tie_between_short_q_and_wing_z_combinations_follows_the_nesting_q_then_wing():
    # the chain lists no put below 91, so a long put targeted below 91 has no strike: with
    # NORMAL_DRAWS Q(0.10) = -1.311 and Q(0.11) = -1.254 (shorts at 93), wing 0.6 puts the long
    # put of q 0.10 at 100 e^{0.05 (-1.311 - 0.6)} = 90.89, unlistable, while the other three
    # candidates all snap to [91, 93]: an exact three-way tie whose FIRST member depends on
    # which list is outer. Declared q [0.10, 0.11] and wing [0.6, 0.5]: q outer picks
    # (0.10, 0.5); a wing-outer order would pick (0.11, 0.6)
    chain = _smooth_market(strikes=range(91, 113))
    _, report = _run_select([_select_row(samples=NORMAL_DRAWS)], chain, FLAT,
                            candidate_structures=["put_spread"], candidate_short_q=[0.10, 0.11],
                            candidate_wing_z=[0.6, 0.5])
    cell = report["ledger"][0]["books"]["model"]
    assert cell["strikes"] == [91.0, 93.0]
    assert (cell["selected"]["short_q"], cell["selected"]["wing_z"]) == (0.10, 0.5)
    assert cell["selected"]["n_candidates_scored"] == 3       # the tied three, each counted


def test_a_tie_between_structures_follows_the_nesting_structure_outermost():
    # a constant forecast settles every draw at the forward, so E_P = credit. Wing 0.5 gives the
    # put spread [97, 100] (put97's ask 2.5 makes its credit negative: refused) and the call spread
    # [100, 105] (103 and 104 unquotable, so the long call snaps out to 105); wing 1.0 gives the
    # put spread [95, 100] and the call spread [100, 106] (refused the same way). The scored pair,
    # put [95, 100] at wing 1.0 and call [100, 105] at wing 0.5, have width 5 and credit 1.0: an
    # exact tie. Declared wing [0.5, 1.0]: structure outermost picks the put; a wing-outer order
    # would meet the call at wing 0.5 first
    chain = _quoted({
        "put100": {"bid": 2.0, "ask": 2.1}, "put95": {"bid": 0.9, "ask": 1.0},
        "put97": {"bid": 2.4, "ask": 2.5},
        "call100": {"bid": 2.0, "ask": 2.1}, "call105": {"bid": 0.9, "ask": 1.0},
        "call106": {"bid": 2.4, "ask": 2.5},
        "call103": {"bid_size": 0, "ask_size": 0}, "call104": {"bid_size": 0, "ask_size": 0}})
    _, report = _run_select([_select_row(samples=[0.0] * 10)], chain, FLAT,
                            candidate_structures=["put_spread", "call_spread"],
                            candidate_wing_z=[0.5, 1.0])
    cell = report["ledger"][0]["books"]["model"]
    assert cell["selected"]["n_candidates_scored"] == 2
    assert (cell["selected"]["structure"], cell["selected"]["wing_z"]) == ("put_spread", 1.0)
    assert cell["strikes"] == [95.0, 100.0]
    _, report = _run_select([_select_row(samples=[0.0] * 10)], chain, FLAT,
                            candidate_structures=["call_spread", "put_spread"],
                            candidate_wing_z=[0.5, 1.0])
    cell = report["ledger"][0]["books"]["model"]
    assert (cell["selected"]["structure"], cell["selected"]["wing_z"]) == ("call_spread", 0.5)
    assert cell["strikes"] == [100.0, 105.0]
    assert cell["selected"]["score"] == pytest.approx(98.7 / (500.0 - 98.7))


#: Sorted: Q(0.1) = -1.0 and Q(0.2) = -0.4 below, Q(0.8) = Q(0.9) = +0.4 above; the extremes
#: -1.0 and +1.0 settle at 95.1 and 105.1, inside every short strike the market below offers.
TIE_DRAWS = [-1.0, -0.4, 0.0, 0.0, 0.0, 0.0, 0.0, 0.4, 0.4, 1.0]


def _two_quotable_puts_and_calls():
    """Only put 95 / 94 and call 106 / 107 can trade: each structure has exactly one spread."""
    chain = _chain(ENTRY, EXPIRY)
    for q in chain:
        if int(q["strike"]) not in (94, 95, 106, 107):
            q["bid_size"] = q["ask_size"] = 0
    return _set(chain, put95={"bid": 1.5, "ask": 1.6}, put94={"bid": 0.9, "ask": 1.0},
                call106={"bid": 1.5, "ask": 1.6}, call107={"bid": 0.9, "ask": 1.0})


def test_a_tie_between_structures_across_short_q_follows_structure_outermost_then_short_q():
    # B-F3 (ADR-0195 review). s' = 0.05. Both short puts are 95 whatever q (96-98 cannot be
    # sold), but the long put of q = 0.1 targets 100 e^{0.05 (-1.0 - 0.5)} = 92.8 and nothing at
    # or under it is quotable, so (put_spread, 0.1) is unscorable; (put_spread, 0.2) targets 95.6
    # and gets [94, 95]. Both short calls are 106 and both long calls 107 (Q(0.8) = Q(0.9)), so the
    # calls are scored at either q. Every payoff is zero (the draws stay inside the shorts), so
    # the put spread [94, 95] and the call spread [106, 107], both credit 0.5 a share over a
    # width of 1, have E_P = 48.7 = credit and max loss 51.3: an EXACT tie between
    # (put_spread, 0.2) and (call_spread, 0.1), which only the nesting can break. Structure
    # outermost reaches (put_spread, 0.2) first; a short_q-outer order would meet
    # (call_spread, 0.1) first.
    chain = _two_quotable_puts_and_calls()
    row = [_select_row(samples=TIE_DRAWS)]
    alone = {}
    for structure, q in (("put_spread", 0.1), ("put_spread", 0.2), ("call_spread", 0.1),
                         ("call_spread", 0.2)):
        _, report = _run_select(row, chain, FLAT, candidate_structures=[structure],
                                candidate_short_q=[q])
        alone[structure, q] = report["ledger"][0]["books"]["model"]
    assert alone["put_spread", 0.1]["selected"] is None      # no quotable long put that far out
    assert alone["put_spread", 0.2]["strikes"] == [94.0, 95.0]
    assert alone["call_spread", 0.1]["strikes"] == alone["call_spread", 0.2]["strikes"] == [
        106.0, 107.0]
    tied = alone["put_spread", 0.2]["selected"]["score"]
    assert tied == alone["call_spread", 0.1]["selected"]["score"] == pytest.approx(48.7 / 51.3)
    _, report = _run_select(row, chain, FLAT, candidate_structures=["put_spread", "call_spread"],
                            candidate_short_q=[0.1, 0.2])
    cell = report["ledger"][0]["books"]["model"]
    assert (cell["selected"]["structure"], cell["selected"]["short_q"]) == ("put_spread", 0.2)
    assert cell["strikes"] == [94.0, 95.0] and cell["selected"]["n_candidates_scored"] == 3
    # the declared order of the structures, not of the quantiles, settles it
    _, report = _run_select(row, chain, FLAT, candidate_structures=["call_spread", "put_spread"],
                            candidate_short_q=[0.1, 0.2])
    cell = report["ledger"][0]["books"]["model"]
    assert (cell["selected"]["structure"], cell["selected"]["short_q"]) == ("call_spread", 0.1)
    assert cell["strikes"] == [106.0, 107.0]


@pytest.mark.parametrize("fee", [41.0, 44.0])
def test_a_candidate_the_credit_gate_refuses_is_never_scored(fee):
    # fees of 41 a leg: the put spread keeps 90 - 82 = 8, the call spread 70 - 82 and the condor
    # 160 - 164 are not positive (44: 2, -18, -16), so only the put spread is scored
    _, report = _run_select([_select_row()], _chain(ENTRY, EXPIRY), FLAT, fee_per_leg=fee)
    cell = report["ledger"][0]["books"]["model"]
    credit = 90.0 - 2 * fee
    assert cell["selected"]["structure"] == "put_spread"
    assert cell["selected"]["n_candidates_scored"] == 1
    assert cell["credit_usd"] == pytest.approx(credit)
    assert cell["selected"]["score"] == pytest.approx(credit / (300.0 - credit))


def test_a_credit_at_or_above_the_narrowest_width_is_never_scored():
    # put95 bid 4.5: the put spread's 4.5 - 1.1 = 3.4 reaches its width 3 and the condor's
    # 3.4 + 0.7 = 4.1 reaches its narrower call width 2: both refused; the call spread stands
    chain = _quoted({"put95": {"bid": 4.5, "ask": 4.6}})
    _, report = _run_select([_select_row()], chain, FLAT)
    cell = report["ledger"][0]["books"]["model"]
    assert cell["selected"]["structure"] == "call_spread"
    assert cell["selected"]["n_candidates_scored"] == 1
    assert cell["selected"]["score"] == pytest.approx(68.7 / 131.3)
    # a credit just under the width (3.4 -> 2.9 a share against 3) is scored
    _, report = _run_select([_select_row()], _quoted({"put95": {"bid": 4.0, "ask": 4.1}}), FLAT,
                            candidate_structures=["put_spread"])
    cell = report["ledger"][0]["books"]["model"]
    assert cell["selected"]["n_candidates_scored"] == 1
    assert cell["credit_usd"] == pytest.approx(290.0 - 1.3)


def test_a_target_outside_the_band_is_never_scored_and_the_others_still_are():
    # wing 3.2 puts the long put at z = -1 - 3.2 (and the long call at +1 + 3.2): 0.05 x 4.2 =
    # 0.21 > the 0.2 band, so every wing-3.2 candidate is refused (all three structures reach out
    # a wing); the wing-0.5 candidates are the case-A ones
    _, report = _run_select([_select_row()], _chain(ENTRY, EXPIRY), FLAT,
                            candidate_wing_z=[3.2, 0.5])
    cell = report["ledger"][0]["books"]["model"]
    assert cell["selected"]["structure"] == "condor" and cell["selected"]["wing_z"] == 0.5
    assert cell["selected"]["n_candidates_scored"] == 3


def test_a_side_with_no_quotable_strike_removes_only_the_structures_that_use_it():
    metrics, report = _run_select([_select_row()], _call_side_unquotable(), FLAT)
    cell = report["ledger"][0]["books"]["model"]
    assert cell["selected"]["structure"] == "put_spread" and cell["strikes"] == [92.0, 95.0]
    assert cell["selected"]["n_candidates_scored"] == 1          # call spread and condor refused
    assert cell["selected"]["score"] == pytest.approx(88.7 / 211.3)
    assert metrics["model_n_put_spread"] == 1 and metrics["model_n_condor"] == 0


def test_a_constant_forecast_leaves_the_structures_whose_strikes_do_not_degenerate():
    # both shorts land at 100: the condor's strikes are not increasing (degenerate) and the call
    # spread's 100 / 103 earns 0.5 - 0.6 < 0 (a nonpositive credit); the put spread [97, 100] is
    # scored (2.0 - 1.1 = 0.9 -> 88.7)
    chain = _quoted({"put100": {"bid": 2.0, "ask": 2.2}, "put97": {"bid": 1.0, "ask": 1.1}})
    _, report = _run_select([_select_row(samples=[0.0] * 10)], chain, FLAT)
    cell = report["ledger"][0]["books"]["model"]
    assert cell["strikes"] == [97.0, 100.0] and cell["selected"]["structure"] == "put_spread"
    assert cell["selected"]["n_candidates_scored"] == 1
    assert cell["credit_usd"] == pytest.approx(88.7)


@pytest.mark.parametrize("over", [
    {"samples": [-3.0] * 2 + [0.0] * 6 + [3.0] * 2},   # Q = -3 / +3: 86.1 / 116.2, off the 88-112 grid
    {"fee_per_leg": 200.0},                            # every credit is eaten by fees
])
def test_no_scorable_candidate_is_a_book_reason_for_model_and_always_and_implied_still_trades(over):
    samples = over.pop("samples", DRAWS)
    metrics, report = _run_select([_select_row(samples=samples)], _chain(ENTRY, EXPIRY), FLAT,
                                  **over)
    (entry,) = report["ledger"]
    for book in ("model", "always"):
        cell = entry["books"][book]
        assert (cell["strikes"], cell["reason"], cell["entered"]) == (None, "no_scorable_candidate",
                                                                      False)
        assert cell["selected"] is None and cell["credit_usd"] is None
        assert metrics[f"{book}_n_trades"] == 0
        assert metrics[f"{book}_n_skipped_no_scorable_candidate"] == 1
        for name in STRUCTURE_LEGS:
            assert metrics[f"{book}_n_{name}"] == 0
    assert entry["model_expected_pnl_usd"] is None
    assert metrics["implied_n_skipped_no_scorable_candidate"] == 0
    if "fee_per_leg" not in over:                     # the implied book pays the same fees
        assert entry["books"]["implied"]["entered"] is True
        assert metrics["implied_n_trades"] == 1 and metrics["implied_n_condor"] == 1
    assert "no_scorable_candidate" in PayoffSelectQuoteBacktest.BOOK_REASONS


def test_the_model_book_needs_the_winners_expected_pnl_above_min_edge_but_always_enters_it():
    chain = _quoted(THIN_CALL)
    for edge, model_enters in ((88.0, True), (89.0, False)):     # the put spread's E_P is 88.7
        metrics, report = _run_select([_select_row(samples=RIGHT_HEAVY)], chain, FLAT,
                                      min_edge_usd=edge)
        entry = report["ledger"][0]
        assert entry["model_expected_pnl_usd"] == pytest.approx(88.7)
        assert entry["books"]["model"]["entered"] is model_enters
        assert entry["books"]["model"]["strikes"] == [92.0, 95.0]     # the winner, entered or not
        assert entry["books"]["model"]["selected"]["structure"] == "put_spread"
        assert entry["books"]["always"]["entered"] is True             # regardless of edge
        assert metrics["always_n_put_spread"] == 1
        assert metrics["model_n_put_spread"] == int(model_enters)
        assert metrics["model_n_skipped_below_min_edge"] == int(not model_enters)
        assert entry["books"]["model"]["reason"] == (None if model_enters else "below_min_edge")


def test_the_score_prices_the_draws_at_the_horizon_scale_not_the_reference_scale():
    # the existing hand check (label_horizon 4, 5 sessions, s' = 0.05 sqrt(5/4)): the condor
    # [90, 93, 106, 109] earns 77.4 and E_P = 77.4 - 5 (93 - 100 e^{-1.4 s'}) = 74.761 (the -1.4
    # draws settle through the short put); its widths are 3 and 3 so the max loss is 300 - 77.4
    _, report = _run_select([_select_row(samples=M1_DRAWS)], _chain(ENTRY, EXPIRY), FLAT,
                            label_horizon=4, candidate_structures=["condor"])
    entry = report["ledger"][0]
    cell = entry["books"]["model"]
    assert cell["strikes"] == [90.0, 93.0, 106.0, 109.0] and cell["credit_usd"] == pytest.approx(77.4)
    assert entry["model_expected_pnl_usd"] == pytest.approx(74.761, abs=1e-3)
    assert cell["selected"]["score"] == pytest.approx(74.761 / 222.6, abs=1e-5)


def _mixed_winners(gates=None):
    """Three entries a fortnight apart whose winners differ: condor, call spread, put spread."""
    rows, chain, series = _three_trades(80.0, 110.0, 97.0)
    rows[1]["samples"], rows[2]["samples"] = list(LEFT_HEAVY), list(RIGHT_HEAVY)
    for q in chain:
        if q["date"] == "2024-03-11" and q["right"] == "put" and q["strike"] == 95.0:
            q["bid"] = 1.15                                    # THIN_PUT on the second entry
        if q["date"] == "2024-03-19" and q["right"] == "call" and q["strike"] == 106.0:
            q["bid"] = 0.85                                    # THIN_CALL on the third
    if gates is not None:
        for row, gate in zip(rows, gates):
            row["g"] = gate
    return rows, chain, series


def test_the_selected_structures_sides_sum_to_each_trade_and_the_side_metrics_cover_their_cells():
    metrics, report = _run_select(*_mixed_winners())
    ledger = report["ledger"]
    model = [e["books"]["model"] for e in ledger]
    assert [c["selected"]["structure"] for c in model] == ["condor", "call_spread", "put_spread"]
    # settled at 80 through both put wings, 110 through the call spread's wing, 97 inside:
    # condor 157.4 - 300 = -142.6 (put -211.3, call +68.7); call spread 68.7 - 200 = -131.3;
    # put spread 88.7
    assert [c["pnl_usd"] for c in model] == pytest.approx([-142.6, -131.3, 88.7])
    assert [c["side_pnl_usd"] for c in model] == [
        pytest.approx({"put": -211.3, "call": 68.7}), pytest.approx({"call": -131.3}),
        pytest.approx({"put": 88.7})]
    for book in ("model", "always"):
        assert metrics[f"{book}_n_trades"] == 3
        assert (metrics[f"{book}_n_condor"], metrics[f"{book}_n_call_spread"],
                metrics[f"{book}_n_put_spread"]) == (1, 1, 1)
        assert metrics[f"{book}_total_pnl_usd"] == pytest.approx(-185.2)
        # a side is summarized over the traded cells that CONTAIN it: put = condor + put spread
        # (-211.3, 88.7), call = condor + call spread (68.7, -131.3)
        assert metrics[f"{book}_put_total_pnl_usd"] == pytest.approx(-122.6)
        assert metrics[f"{book}_put_mean_pnl_usd"] == pytest.approx(-61.3)
        assert metrics[f"{book}_put_hit_rate"] == pytest.approx(0.5)
        assert metrics[f"{book}_call_total_pnl_usd"] == pytest.approx(-62.6)
        assert metrics[f"{book}_call_mean_pnl_usd"] == pytest.approx(-31.3)
        assert metrics[f"{book}_call_hit_rate"] == pytest.approx(0.5)
        # the sides still add up to the book here, because every trade's sides sum to its P&L
        assert metrics[f"{book}_put_total_pnl_usd"] + metrics[f"{book}_call_total_pnl_usd"] == \
            pytest.approx(metrics[f"{book}_total_pnl_usd"])
    # the implied book is the condor every time (put -81.3 / -81.3 / +18.7 ... by hand: 80 -> -32.6,
    # 110 -> -132.6, 97 -> 67.4)
    assert [e["books"]["implied"]["pnl_usd"] for e in ledger] == pytest.approx(
        [-32.6, -132.6, 67.4])
    assert metrics["implied_n_condor"] == 3 and metrics["implied_n_put_spread"] == 0


def test_a_side_only_a_selected_structure_lacks_is_summarized_over_the_cells_that_have_it():
    # only call spreads traded: the put side has NO cell, so its statistics are the empty ones
    # (float zeros) instead of a KeyError on a cell with no put wing
    metrics, _ = _run_select([_select_row(samples=LEFT_HEAVY)], _quoted(THIN_PUT), FLAT)
    assert metrics["model_n_call_spread"] == 1 and metrics["model_n_trades"] == 1
    assert metrics["model_put_total_pnl_usd"] == 0.0 and metrics["model_put_hit_rate"] == 0.0
    assert type(metrics["model_put_total_pnl_usd"]) is float
    assert metrics["model_call_total_pnl_usd"] == pytest.approx(68.7)


def test_the_american_charge_follows_the_selected_structures_short_legs():
    # 03-07 ex-date 1.0 (pre-ex close 107 above the 106 short call) and a 94 close below the 95
    # short put: a condor pays both, a call spread only the 100 dividend, a put spread one day
    # of carry on its short put
    dividends = [("2024-03-07", 1.0)]
    closes = [(d, 100.0) for d in SESSIONS[:3]] + [("2024-03-06", 107.0), ("2024-03-07", 94.0),
                                                     ("2024-03-08", 97.0), ("2024-03-11", 98.0)]
    series = _series(closes, dividends)
    expected = {"condor": (100.0 + _carry(95.0, 1), 157.4), "call_spread": (100.0, 68.7),
                "put_spread": (_carry(95.0, 1), 88.7)}
    for (_, quotes, draws, _table, winner) in SELECT_CASES:
        _, report = _run_select([_select_row(samples=draws)], _quoted(quotes), series)
        cell = report["ledger"][0]["books"]["model"]
        charge, credit = expected[winner]
        assert cell["selected"]["structure"] == winner
        assert cell["american_charge_usd"] == pytest.approx(charge)
        assert cell["pnl_usd"] == pytest.approx(credit - charge)
        sides = cell["side_pnl_usd"]
        if winner == "call_spread":
            assert sides == pytest.approx({"call": 68.7 - 100.0})
        if winner == "put_spread":
            assert sides == pytest.approx({"put": 88.7 - _carry(95.0, 1)})


def test_the_delta_benchmark_is_the_selected_call_spreads_own_delta():
    call_spread_delta = _structure_delta(CALL_SPREAD, (106.0, 108.0))
    assert call_spread_delta < 0          # a short call under a long call is short the stock
    metrics, report = _run_select([_select_row(samples=LEFT_HEAVY)], _quoted(THIN_PUT), FLAT)
    cell = report["ledger"][0]["books"]["model"]
    assert cell["selected"]["structure"] == "call_spread"
    assert cell["delta"] == pytest.approx(call_spread_delta)
    assert cell["equity_pnl_usd"] == pytest.approx(call_spread_delta * 100 * -3.0)  # 100 -> 97
    assert metrics["model_delta_equity_total_usd"] == pytest.approx(call_spread_delta * -300.0)
    # the dividends paid after entry belong to the stock leg: 1.0 on 03-07 adds a dollar a share
    closes = [(d, 100.0) for d in SESSIONS[:-3]] + [("2024-03-08", 97.0), ("2024-03-11", 98.0)]
    _, report = _run_select([_select_row(samples=LEFT_HEAVY)], _quoted(THIN_PUT),
                            _series(closes, [("2024-03-07", 1.0)]))
    assert report["ledger"][0]["books"]["model"]["equity_pnl_usd"] == pytest.approx(
        call_spread_delta * 100 * (97.0 - 100.0 + 1.0))
    # a selected leg with no iv leaves no benchmark and is counted; an unselected leg's iv is nobody's
    lacking = _quoted({**THIN_PUT, "call108": {"iv": None}})
    metrics, report = _run_select([_select_row(samples=LEFT_HEAVY)], lacking, FLAT)
    cell = report["ledger"][0]["books"]["model"]
    assert cell["delta"] is None and cell["equity_pnl_usd"] is None
    assert metrics["model_n_no_delta"] == 1 and cell["entered"] is True
    metrics, _ = _run_select([_select_row(samples=LEFT_HEAVY)],
                             _quoted({**THIN_PUT, "put92": {"iv": None}}), FLAT)
    assert metrics["model_n_no_delta"] == 0     # the put legs are not the winner's


def test_the_gate_annotation_splits_the_selected_structures_pnl():
    rows, chain, series = _mixed_winners(gates=[True, False, None])
    metrics, report = _run_select(rows, chain, series, gate_fields=["g"])
    assert [e["gates"] for e in report["ledger"]] == [{"g": True}, {"g": False}, {"g": None}]
    # the model book's P&L by gate: closed = the condor's -142.6, open = the call spread's -131.3
    assert (metrics["model_g_closed_n"], metrics["model_g_open_n"],
            metrics["model_g_unknown_n"]) == (1, 1, 1)
    assert metrics["model_g_closed_mean_pnl_usd"] == pytest.approx(-142.6)
    assert metrics["model_g_open_mean_pnl_usd"] == pytest.approx(-131.3)
    assert metrics["model_g_closed_t"] == 0.0 and metrics["model_g_open_t"] == 0.0   # one trade each
    plain, _ = _run_select(*_mixed_winners())
    assert {k: v for k, v in metrics.items() if "_g_" not in k} == plain   # annotation only


def test_a_one_candidate_select_is_the_condor_backtest_and_a_put_spread_select_the_put_spread():
    # on chains where the credit gate refuses nothing (a refused candidate is "no_scorable_
    # candidate" here, not the plain node's "nonpositive_credit": see the gate tests)
    rows, chain, series = _three_trades(80.0, 93.5, 97.0)
    _, condor = _run(rows, chain, series)
    _, select = _run_select(rows, chain, series, candidate_structures=["condor"])
    for a, b in zip(condor["ledger"], select["ledger"]):
        for book in ("model", "always", "implied"):
            cell = {k: v for k, v in b["books"][book].items() if k != "selected"}
            assert cell == a["books"][book], book
        assert {k: v for k, v in b.items() if k != "books"} == {
            k: v for k, v in a.items() if k != "books"}
    # ... and at the put spread's own strikes the model and always books are the PutSpread's
    _, spread = _run_put_spread(rows, chain, series, short_q=0.16, wing_z=0.65)
    _, select = _run_select(rows, chain, series, candidate_structures=["put_spread"],
                            candidate_short_q=[0.16], candidate_wing_z=[0.65])
    for a, b in zip(spread["ledger"], select["ledger"]):
        for book in ("model", "always"):
            assert {k: v for k, v in b["books"][book].items() if k != "selected"} == \
                a["books"][book], book


def test_the_implied_book_is_the_unchanged_vix_implied_condor_at_short_q_and_wing_z():
    rows, chain, series = _mixed_winners()
    c_metrics, condor = _run(rows, chain, series, short_q=0.2, wing_z=1.0)
    s_metrics, select = _run_select(rows, chain, series, short_q=0.2, wing_z=1.0)
    for a, b in zip(condor["ledger"], select["ledger"]):
        assert {k: v for k, v in b["books"]["implied"].items() if k != "selected"} == \
            a["books"]["implied"]
    shared = [k for k in c_metrics if k.startswith("implied_")]
    assert shared and all(s_metrics[k] == c_metrics[k] for k in shared)


def test_the_report_names_the_selector_and_is_never_decision_eligible():
    cls = PayoffSelectQuoteBacktest
    assert issubclass(cls, CondorQuoteBacktest) and cls.role == "score"
    assert cls.serving_effect(PARAMS, {}) == "forbidden"
    assert cls.LEGS == CONDOR and cls.SIDES == ("put", "call")     # the implied book's structure
    _, report = _run_select([_select_row()], _chain(ENTRY, EXPIRY), FLAT)
    assert report["kind"] == "archived_quote_payoff_select_backtest"
    assert report["units"] == "USD per selected structure, one contract per leg"
    assert report["pricing"] == "archived_eod_quotes" and report["decision_eligible"] is False
    assert report["params"]["candidate_structures"] == SELECT_KNOBS["candidate_structures"]
    assert report["params"]["candidate_short_q"] == [0.1]
    assert report["params"]["short_q"] == 0.1 and report["params"]["wing_z"] == 0.5


def test_every_structure_count_metric_exists_for_every_book_even_with_no_trade():
    chain = _chain(ENTRY, EXPIRY, bid_size=0, ask_size=0)
    metrics, _ = _run_select([_select_row()], chain, FLAT)
    for book in PayoffSelectQuoteBacktest.BOOKS:
        assert metrics[f"{book}_n_trades"] == 0
        for name in STRUCTURE_LEGS:
            assert metrics[f"{book}_n_{name}"] == 0, (book, name)
        assert type(metrics[f"{book}_put_total_pnl_usd"]) is float
    assert metrics["model_n_skipped_no_scorable_candidate"] == 1


def _random_market(seed):
    """A chain of random (but ordered, positive) quotes and a random forecast, for the oracle.

    Even seeds draw a continuous forecast; odd seeds a coarse one (five values), whose quantiles
    collapse onto a few levels so that MANY candidates snap to the same strikes and tie exactly.
    """
    rng = random.Random(seed)
    chain = _chain(ENTRY, EXPIRY, strikes=range(80, 121))
    for q in chain:
        mid = rng.uniform(0.15, 6.0)
        q["bid"], q["ask"] = round(mid * 0.94, 2) + 0.01, round(mid * 1.06, 2) + 0.02
    if seed % 2:
        samples = [rng.choice([-2.0, -1.0, 0.0, 1.0, 2.0]) for _ in range(40)]
    else:
        samples = [rng.gauss(0.0, 1.0) * rng.choice([0.7, 1.0, 1.4]) for _ in range(120)]
    return [_select_row(samples=samples)], chain


@pytest.mark.parametrize("seed", range(24))
def test_the_selection_is_the_best_single_candidate_and_the_first_of_equals(seed):
    # the oracle: run every candidate ALONE (each alone-run is the hand-checked scoring above) and
    # take the highest score, the earliest in declaration order on a tie
    grid = (["put_spread", "call_spread", "condor"], [0.10, 0.16, 0.20, 0.25, 0.30],
            [0.35, 0.65, 1.0])
    rows, chain = _random_market(seed)
    alone = []
    for structure, q, wing in itertools.product(*grid):
        _, report = _run_select(rows, chain, FLAT, candidate_structures=[structure],
                                candidate_short_q=[q], candidate_wing_z=[wing])
        selected = report["ledger"][0]["books"]["always"]["selected"]
        if selected is not None:
            alone.append((selected["score"], structure, q, wing))
    _, report = _run_select(rows, chain, FLAT, candidate_structures=grid[0],
                            candidate_short_q=grid[1], candidate_wing_z=grid[2])
    entry = report["ledger"][0]
    selected = entry["books"]["always"]["selected"]
    assert entry["books"]["model"]["selected"] == selected
    assert selected["n_candidates_scored"] == len(alone)
    if not alone:
        assert selected is None
        return
    best = max(score for score, *_ in alone)
    first = next(candidate for candidate in alone if candidate[0] == best)
    assert (selected["score"], selected["structure"], selected["short_q"],
            selected["wing_z"]) == first
    # the recorded score is E_P / max loss of the traded cell, recomputed from the ledger alone
    cell = entry["books"]["always"]
    k = cell["strikes"]
    widest = max(k[1] - k[0], k[3] - k[2]) if selected["structure"] == "condor" else k[1] - k[0]
    assert selected["score"] == pytest.approx(
        entry["model_expected_pnl_usd"] / (100 * widest - cell["credit_usd"]))
    assert 100 * widest - cell["credit_usd"] > 0          # a candidate's max loss is positive


_SELECT_PARAM_REFUSALS = [
    ({"candidate_structures": []}, "candidate_structures"),
    ({"candidate_structures": ["condor", "condor"]}, "candidate_structures"),
    ({"candidate_structures": ["butterfly"]}, "candidate_structures"),
    ({"candidate_structures": ["condor", 3]}, "candidate_structures"),
    ({"candidate_structures": [["condor"]]}, "candidate_structures"),
    ({"candidate_structures": "condor"}, "candidate_structures"),
    ({"candidate_structures": ("condor",)}, "candidate_structures"),
    ({"candidate_structures": None}, "candidate_structures"),
    ({"candidate_short_q": []}, "candidate_short_q"),
    ({"candidate_short_q": [0.1, 0.1]}, "candidate_short_q"),
    ({"candidate_short_q": [0.0]}, "candidate_short_q"),
    ({"candidate_short_q": [0.5]}, "candidate_short_q"),
    ({"candidate_short_q": [0.1, -0.1]}, "candidate_short_q"),
    ({"candidate_short_q": [0.1, "0.2"]}, "candidate_short_q"),
    ({"candidate_short_q": [True]}, "candidate_short_q"),
    ({"candidate_short_q": [float("nan")]}, "candidate_short_q"),
    ({"candidate_short_q": 0.1}, "candidate_short_q"),
    ({"candidate_wing_z": []}, "candidate_wing_z"),
    ({"candidate_wing_z": [0.5, 0.5]}, "candidate_wing_z"),
    ({"candidate_wing_z": [0.0]}, "candidate_wing_z"),
    ({"candidate_wing_z": [-1.0]}, "candidate_wing_z"),
    ({"candidate_wing_z": ["1"]}, "candidate_wing_z"),
    ({"candidate_wing_z": [float("inf")]}, "candidate_wing_z"),
    ({"candidate_wing_z": 0.5}, "candidate_wing_z"),
    ({"wing_points": 25}, "wing_points"),                 # default-deny: an unknown knob is named
    ({"short_q": 0.5}, "short_q"), ({"wing_z": 0}, "wing_z"), ({"multiplier": 0}, "multiplier"),
    ({"carry_rate": -0.01}, "carry_rate"), ({"split": "nope"}, "split"),
]


@pytest.mark.parametrize("change, name", _SELECT_PARAM_REFUSALS)
def test_the_selector_refuses_a_bad_knob_and_names_it(change, name):
    cls = PayoffSelectQuoteBacktest
    problems = cls.validate_params({**PARAMS, **SELECT_KNOBS, **change})
    assert problems and any(name in p for p in problems), problems
    if name in SELECT_KNOBS:      # a candidate knob is a KNOWN knob with a bad value, not a typo
        assert not any("unknown param" in p for p in problems), problems
    with pytest.raises(ConfigError, match=name):
        cls("bt", {**PARAMS, **SELECT_KNOBS, **change})


def test_the_selector_needs_every_condor_knob_and_every_candidate_knob():
    cls = PayoffSelectQuoteBacktest
    full = {**PARAMS, **SELECT_KNOBS}
    assert cls.validate_params(dict(full)) == []
    assert set(SELECT_KNOBS) <= set(cls._REQUIRED) and set(PARAMS) <= set(cls._REQUIRED)
    assert set(SELECT_KNOBS) <= set(cls._PARAMS) and set(CondorQuoteBacktest._PARAMS) <= set(
        cls._PARAMS)
    for missing in full:
        with pytest.raises(ConfigError, match=missing):
            cls("bt", {k: v for k, v in full.items() if k != missing})
    # the condor takes no candidate knob: the two contracts stay apart
    with pytest.raises(ConfigError, match="candidate_structures"):
        CondorQuoteBacktest("bt", full)
    # short_q and wing_z still define the implied book, so both stay required and validated
    assert any("short_q" in p for p in cls.validate_params({**full, "short_q": 0.7}))
    for structures in (["put_spread"], ["call_spread"], ["condor"],
                       ["condor", "put_spread", "call_spread"]):
        assert cls.validate_params({**full, "candidate_structures": structures}) == []
    assert cls.validate_params({**full, "gate_fields": ["g"], "min_edge_usd": 1.0,
                                "cvar_alpha": 0.9}) == []


# -- ADR-0196: size_fields annotate each entry with weights and report the weighted books -------
#
# size_fields names forecast-row fields (a positive finite weight, or None) that the backtest
# READS at each entry and records; it never changes a trade and never skips one. Per book and
# field, over the TRADED cells that carry a weight, it reports the book as if each trade had been
# taken at that many contracts: the weighted P&L w x pnl and its total, t, CVaR and drawdown.

#: Every per-field size metric's suffix, restated here (never read from the node).
SIZE_SUFFIXES = ("n", "mean_weight", "total_pnl_usd", "pnl_per_weight", "t", "cvar_usd",
                 "max_drawdown_usd", "unknown_n")
#: Weights per entry of GATE_LEVELS' six worked entries; None is an unknown weight.
SIZE_W = [1.0, 2.0, None, 0.5, 4.0, 1.0]
SIZE_V = [0.5, None, 3.0, 1.0, 2.0, None]
#: The model book's P&L at each entry (levels 80, 97, 93.5, 97, 110, 97), from the hand tables.
CONDOR_MODEL_PNL = [CONDOR_PNL["model"][level] for level in GATE_LEVELS]
CONDOR_IMPLIED_PNL = [CONDOR_PNL["implied"][level] for level in GATE_LEVELS]
PUT_SPREAD_MODEL_PNL = [PUT_SPREAD_PNL["model"][level] for level in GATE_LEVELS]


def _sized(pnls, weights, alpha=0.95):
    """One book's size metrics, written out from the definitions (never read from the node)."""
    pairs = [(w, p) for w, p in zip(weights, pnls) if w is not None]
    sized = [w * p for w, p in pairs]
    n = len(sized)
    total_weight = sum(w for w, _p in pairs)
    tail = max(1, math.ceil(round((1 - alpha) * n, 9)))
    path, peak, worst = 0.0, 0.0, 0.0
    for value in sized:
        path += value
        peak = max(peak, path)
        worst = max(worst, peak - path)
    spread = max(sized) - min(sized) if sized else 0.0
    return {"n": n, "mean_weight": total_weight / n if n else 0.0,
            "total_pnl_usd": sum(sized), "pnl_per_weight": sum(sized) / total_weight if n else 0.0,
            "t": _t(sized) if n >= 2 and spread > 0 else 0.0,
            "cvar_usd": sum(sorted(sized)[:tail]) / tail if n else 0.0,
            "max_drawdown_usd": worst,
            "unknown_n": len([w for w in weights if w is None])}


def _assert_the_sized_book(metrics, book, field, want):
    for suffix in SIZE_SUFFIXES:
        assert metrics[f"{book}_{field}_{suffix}"] == pytest.approx(want[suffix]), (book, field,
                                                                                    suffix)


def test_the_hand_computed_sized_condor_model_book():
    # weights 1 / 2 / - / 0.5 / 4 / 1 on P&L -142.6 / 157.4 / 7.4 / 157.4 / -42.6 / 157.4: the
    # entry with no weight (7.4) is left out, the rest weigh in as w x pnl
    #   [-142.6, 314.8, 78.7, -170.4, 157.4]: total 237.9, weights sum 8.5 (mean 1.7)
    #   worst one (CVaR at 0.95 over five is the single worst) -170.4; the path -142.6, 172.2,
    #   250.9, 80.5, 237.9 peaks at 250.9 and falls 170.4 to 80.5 (more than the first 142.6 dip)
    days, chain, series = _trades(GATE_LEVELS)
    metrics, report = _run(_gated_rows(days, w=SIZE_W), chain, series, size_fields=["w"])
    assert CONDOR_MODEL_PNL == pytest.approx([-142.6, 157.4, 7.4, 157.4, -42.6, 157.4])
    assert metrics["model_w_n"] == 5 and metrics["model_w_unknown_n"] == 1
    assert metrics["model_w_mean_weight"] == pytest.approx(1.7)
    assert metrics["model_w_total_pnl_usd"] == pytest.approx(237.9)
    assert metrics["model_w_pnl_per_weight"] == pytest.approx(237.9 / 8.5)
    assert metrics["model_w_cvar_usd"] == pytest.approx(-170.4)
    assert metrics["model_w_max_drawdown_usd"] == pytest.approx(170.4)
    assert metrics["model_w_t"] == pytest.approx(_t([-142.6, 314.8, 78.7, -170.4, 157.4]))
    _assert_the_sized_book(metrics, "model", "w", _sized(CONDOR_MODEL_PNL, SIZE_W))
    assert [e["sizes"] for e in report["ledger"]] == [{"w": w} for w in SIZE_W]
    assert report["params"]["size_fields"] == ["w"]


@pytest.mark.parametrize("alpha, tail", [(0.6, 3), (0.7, 2), (0.83, 2), (0.84, 1)])
def test_the_sized_cvar_takes_the_declared_tail_over_more_than_one_trade(alpha, tail):
    # ADR-0196 review B-F4: every other sized test runs at the default 0.95, where the tail of
    # fewer than twenty values is ONE trade. Six weighted trades at weights 1 / 2 / 1 / 0.5 / 4 / 1 on
    # P&L -142.6 / 157.4 / 7.4 / 157.4 / -42.6 / 157.4 are
    #   [-142.6, 314.8, 7.4, 78.7, -170.4, 157.4], worst first: -170.4, -142.6, 7.4, 78.7, 157.4, 314.8.
    # The tail holds ceil(round((1 - alpha) x 6, 9)): 3 at 0.6 (2.4), 2 at 0.7 (1.8) and 0.83 (1.02),
    # 1 at 0.84 (0.96). So the sized CVaR is the mean of the worst 3 / 2 / 2 / 1 of those, and the
    # UNSIZED cvar of the same book, at the same alpha, takes its own tail of the raw P&L.
    weights = [1.0, 2.0, 1.0, 0.5, 4.0, 1.0]
    days, chain, series = _trades(GATE_LEVELS)
    metrics, _ = _run(_gated_rows(days, w=weights), chain, series, size_fields=["w"],
                      cvar_alpha=alpha)
    worst_sized = [-170.4, -142.6, 7.4, 78.7, 157.4, 314.8]
    assert metrics["model_w_cvar_usd"] == pytest.approx(sum(worst_sized[:tail]) / tail)
    assert sum(worst_sized[:3]) / 3 == pytest.approx(-101.86666666666667)       # the 0.6 value by hand
    worst_raw = [-142.6, -42.6, 7.4, 157.4, 157.4, 157.4]
    assert metrics["model_cvar_usd"] == pytest.approx(sum(worst_raw[:tail]) / tail)
    _assert_the_sized_book(metrics, "model", "w", _sized(CONDOR_MODEL_PNL, weights, alpha=alpha))
    # and the default tail really is different (one trade, the worst): the knob is what moved it
    default, _ = _run(_gated_rows(days, w=weights), chain, series, size_fields=["w"])
    assert default["model_w_cvar_usd"] == pytest.approx(-170.4)


@pytest.mark.parametrize("cls, sides, pnl_of", [
    (CondorQuoteBacktest, ("put", "call"), {"model": CONDOR_MODEL_PNL,
                                            "always": CONDOR_MODEL_PNL,
                                            "implied": CONDOR_IMPLIED_PNL}),
    (PutSpreadQuoteBacktest, ("put",), {"model": PUT_SPREAD_MODEL_PNL,
                                        "always": PUT_SPREAD_MODEL_PNL,
                                        "implied": [PUT_SPREAD_PNL["implied"][lv]
                                                    for lv in GATE_LEVELS]}),
])
def test_each_book_and_each_size_field_is_summarized_over_its_own_trades(cls, sides, pnl_of):
    days, chain, series = _trades(GATE_LEVELS)
    rows = _gated_rows(days, w=SIZE_W, v=SIZE_V)
    metrics, report = _run_node(cls, sides, rows, chain, series, size_fields=["w", "v"])
    assert all(list(e["sizes"]) == ["w", "v"] for e in report["ledger"])      # declared order
    for book, pnls in pnl_of.items():
        for field, weights in (("w", SIZE_W), ("v", SIZE_V)):
            _assert_the_sized_book(metrics, book, field, _sized(pnls, weights))
    # the two fields are independent: v's unknowns are not w's
    assert (metrics["model_w_unknown_n"], metrics["model_v_unknown_n"]) == (1, 2)
    assert (metrics["model_w_n"], metrics["model_v_n"]) == (5, 4)


@pytest.mark.parametrize("cls, sides", [(CondorQuoteBacktest, ("put", "call")),
                                        (PutSpreadQuoteBacktest, ("put",))])
def test_sizing_never_changes_the_trades_and_absent_size_fields_change_nothing(cls, sides):
    days, chain, series = _trades(GATE_LEVELS)
    rows = _gated_rows(days, w=SIZE_W, g=GATE_A)
    plain_metrics, plain = _run_node(cls, sides, rows, chain, series)
    assert all("sizes" not in e for e in plain["ledger"])
    assert "size_fields" not in plain["params"]              # emitted only when present
    assert not any("_w_" in k for k in plain_metrics)
    metrics, sized = _run_node(cls, sides, rows, chain, series, size_fields=["w"])
    assert [{k: v for k, v in e.items() if k != "sizes"} for e in sized["ledger"]] == \
        plain["ledger"]                                       # no entry skipped, none added
    assert {k: v for k, v in metrics.items() if "_w_" not in k} == plain_metrics
    assert {k: v for k, v in sized["params"].items() if k != "size_fields"} == plain["params"]
    assert sized["chain"] == plain["chain"] and sized["kind"] == plain["kind"]
    # alongside gate_fields: each annotation is its own, both ride the same entries
    both_metrics, both = _run_node(cls, sides, rows, chain, series, size_fields=["w"],
                                   gate_fields=["g"])
    assert all(list(e) [-2:] == ["gates", "sizes"] for e in both["ledger"])
    assert {k: v for k, v in both_metrics.items() if "_g_" not in k} == metrics


def test_a_book_that_did_not_trade_an_entry_does_not_weigh_it():
    days, chain, series = _trades([80.0, 97.0, 97.0])
    rows = _gated_rows(days, w=[2.0, 1.0, 3.0])
    # the model book skips every entry (expected 157.4 does not beat 200); always trades them all
    metrics, _ = _run(rows, chain, series, size_fields=["w"], min_edge_usd=200.0)
    assert metrics["model_n_trades"] == 0 and metrics["always_n_trades"] == 3
    for suffix in SIZE_SUFFIXES:
        assert metrics[f"model_w_{suffix}"] == 0, suffix       # every count, total and t is zero
    _assert_the_sized_book(metrics, "always", "w", _sized([-142.6, 157.4, 157.4], [2.0, 1.0, 3.0]))
    assert isinstance(metrics["model_w_total_pnl_usd"], float)
    assert isinstance(metrics["model_w_cvar_usd"], float)


def test_a_sized_book_with_no_weighted_trade_reports_float_zeros():
    days, chain, series = _trades([97.0, 97.0])
    metrics, _ = _run(_gated_rows(days, w=[None, None]), chain, series, size_fields=["w"])
    assert metrics["model_n_trades"] == 2 and metrics["model_w_unknown_n"] == 2
    for suffix in SIZE_SUFFIXES[:-1]:
        assert metrics[f"model_w_{suffix}"] == 0.0 and isinstance(metrics[f"model_w_{suffix}"],
                                                                  (int, float)), suffix
    assert isinstance(metrics["model_w_total_pnl_usd"], float)
    assert isinstance(metrics["model_w_mean_weight"], float)


def test_a_constant_weight_of_one_is_the_unsized_book():
    days, chain, series = _trades(GATE_LEVELS)
    metrics, _ = _run(_gated_rows(days, one=[1.0] * 6), chain, series, size_fields=["one"])
    for book in CondorQuoteBacktest.BOOKS:
        assert metrics[f"{book}_one_n"] == metrics[f"{book}_n_trades"] == 6
        assert metrics[f"{book}_one_total_pnl_usd"] == pytest.approx(
            metrics[f"{book}_total_pnl_usd"])
        assert metrics[f"{book}_one_pnl_per_weight"] == pytest.approx(
            metrics[f"{book}_mean_pnl_usd"])
        assert metrics[f"{book}_one_mean_weight"] == 1.0
        assert metrics[f"{book}_one_cvar_usd"] == pytest.approx(metrics[f"{book}_cvar_usd"])
        assert metrics[f"{book}_one_max_drawdown_usd"] == pytest.approx(
            metrics[f"{book}_max_drawdown_usd"])
        assert metrics[f"{book}_one_t"] == pytest.approx(metrics[f"{book}_pnl_t"])


def test_a_uniform_scale_scales_the_money_and_leaves_the_per_weight_and_the_t_alone():
    days, chain, series = _trades(GATE_LEVELS)
    rows = _gated_rows(days, one=[1.0] * 6, three=[3.0] * 6)
    metrics, _ = _run(rows, chain, series, size_fields=["one", "three"])
    for suffix in ("total_pnl_usd", "cvar_usd", "max_drawdown_usd"):
        assert metrics[f"model_three_{suffix}"] == pytest.approx(3 * metrics[f"model_one_{suffix}"])
    for suffix in ("pnl_per_weight", "t"):
        assert metrics[f"model_three_{suffix}"] == pytest.approx(metrics[f"model_one_{suffix}"])
    assert metrics["model_three_mean_weight"] == 3.0


def test_each_entry_reads_the_weights_of_its_own_row_and_instrument():
    days, chain, series = _trades([97.0])
    qqq_chain = _set(_chain(days[0], "2024-03-08", close=50.0, strikes=range(44, 57),
                            instrument="QQQ"),
                     put47={"bid": 1.0, "ask": 1.1}, call53={"bid": 1.0, "ask": 1.1})
    qqq_series = [{"instrument": "QQQ", "date": d, "close": 50.0, "asof_ms": _ms(d),
                   "dividend_amount": 0.0} for d in SESSIONS]
    rows = [_row(days[0], w=2.0), _row(days[0], close=50.0, instrument="QQQ", w=0.5)]
    _, report = _run(rows, chain + qqq_chain, series + qqq_series, size_fields=["w"])
    assert {e["instrument"]: e["sizes"] for e in report["ledger"]} == {
        "SPY": {"w": 2.0}, "QQQ": {"w": 0.5}}


@pytest.mark.parametrize("bad", [0, 0.0, -1.0, True, False, "1", "", [1.0], {"w": 1.0},
                                 float("nan"), float("inf"), -float("inf")])
def test_a_weight_that_is_not_a_positive_finite_number_or_none_refuses_naming_the_field_and_row(bad):
    days, chain, series = _trades([97.0, 97.0])
    rows = _gated_rows(days, w=[1.5, bad])
    with pytest.raises(ValueError, match=rf"size field 'w'.*SPY {days[1]}.*positive"):
        _run(rows, chain, series, size_fields=["w"])


def test_an_integer_weight_is_a_number_and_a_none_is_an_unknown_weight():
    days, chain, series = _trades([97.0, 97.0])
    metrics, _ = _run(_gated_rows(days, w=[2, None]), chain, series, size_fields=["w"])
    assert (metrics["model_w_n"], metrics["model_w_unknown_n"]) == (1, 1)
    assert metrics["model_w_total_pnl_usd"] == pytest.approx(2 * 157.4)


def test_a_size_field_absent_from_an_entry_row_refuses_as_a_typo():
    days, chain, series = _trades([97.0])
    with pytest.raises(ValueError, match=rf"size field 'w_typo'.*absent.*SPY {days[0]}"):
        _run(_gated_rows(days, w=[1.0]), chain, series, size_fields=["w_typo"])


def test_a_weight_on_a_row_that_never_becomes_an_entry_is_not_read():
    days, chain, series = _trades([97.0])
    rows = _gated_rows(days, w=[2.0]) + [_row("2024-03-04", w="junk"), _row("2024-03-11", w="junk")]
    metrics, report = _run(rows, chain, series, size_fields=["w"])
    assert metrics["n_skipped_no_chain"] == 1 and len(report["ledger"]) == 1
    assert metrics["model_w_n"] == 1


def test_the_selector_sizes_whatever_structure_it_picked():
    rows, chain, series = _mixed_winners()
    for row, weight in zip(rows, (2.0, None, 0.5)):
        row["w"] = weight
    metrics, report = _run_select(rows, chain, series, size_fields=["w"])
    # the picks' P&L: condor -142.6, call spread -131.3, put spread 88.7; weights 2 / - / 0.5
    want = _sized([-142.6, -131.3, 88.7], [2.0, None, 0.5])
    assert want["total_pnl_usd"] == pytest.approx(-285.2 + 44.35)
    for book in ("model", "always"):
        _assert_the_sized_book(metrics, book, "w", want)
    _assert_the_sized_book(metrics, "implied", "w", _sized([-32.6, -132.6, 67.4], [2.0, None, 0.5]))
    assert [e["sizes"] for e in report["ledger"]] == [{"w": 2.0}, {"w": None}, {"w": 0.5}]
    plain_metrics, plain = _run_select(*_mixed_winners())
    assert [{k: v for k, v in e.items() if k != "sizes"} for e in report["ledger"]] == \
        plain["ledger"]


def test_the_proxy_backtest_sizes_its_entries_the_same_way():
    def row(i, **extra):
        return {"asof_ms": 100 + i, "instrument": "SPX", "date": f"d{i}", "close": 1000.0,
                "iv_index": 20.0, "reference_scale": 0.05, "samples": list(DRAWS),
                "outcome": 0.0, **extra}

    rows = [row(0, w=2.0), row(1), row(2), row(3, w=None), row(4), row(5), row(6, w=0.5)]
    out = CondorBacktest("bt", {**_PROXY, "size_fields": ["w"]}).run(CTX, {"forecasts": rows})
    ledger = out["report"].value["ledger"]
    assert [e["date"] for e in ledger] == ["d0", "d3", "d6"]
    assert [e["sizes"] for e in ledger] == [{"w": 2.0}, {"w": None}, {"w": 0.5}]
    pnls = [e["books"]["always"]["pnl_usd"] for e in ledger]
    _assert_the_sized_book(out["metrics"], "always", "w", _sized(pnls, [2.0, None, 0.5]))
    assert out["metrics"]["always_w_unknown_n"] == 1


_SELECT_BASE = {**PARAMS, **SELECT_KNOBS}


@pytest.mark.parametrize("cls, base", [
    (CondorQuoteBacktest, PARAMS), (PutSpreadQuoteBacktest, PARAMS),
    (PayoffSelectQuoteBacktest, _SELECT_BASE), (CondorBacktest, _PROXY)])
def test_every_backtest_accepts_size_fields_and_refuses_a_malformed_declaration(cls, base):
    assert "size_fields" in cls._PARAMS and "gate_fields" in cls._PARAMS
    assert cls.validate_params({**base, "size_fields": ["w"]}) == []
    assert cls.validate_params({**base, "size_fields": ["w", "v"]}) == []
    assert cls.validate_params({**base, "size_fields": ["w"], "gate_fields": ["g"]}) == []
    for bad in ([], "w", ["w", "w"], [""], [1], ["w", None], None, ("w",), {"w": 1}, [True]):
        problems = cls.validate_params({**base, "size_fields": bad})
        assert any("size_fields must be a non-empty list of distinct non-empty strings" in p
                   for p in problems), bad
        with pytest.raises(ConfigError, match="size_fields"):
            cls("bt", {**base, "size_fields": bad})


def test_the_distinct_list_rule_has_one_home_so_gates_sizes_and_candidates_cannot_drift():
    # B-N1 (ADR-0195 review): gate_fields, size_fields and the selector's three candidate lists
    # are all "a non-empty list of distinct <items>", worded and checked in ONE place
    import index_options.nodes as nodes

    source = Path(nodes.__file__).read_text(encoding="utf-8")
    assert source.count("must be a non-empty list of distinct") == 1
    base = {**PARAMS, **SELECT_KNOBS}
    for knob, bad in (("candidate_structures", []), ("candidate_short_q", [0.1, 0.1]),
                      ("candidate_wing_z", [0.5, "x"]), ("gate_fields", [""]),
                      ("size_fields", ["w", "w"])):
        (problem,) = [p for p in PayoffSelectQuoteBacktest.validate_params({**base, knob: bad})
                      if knob in p]
        assert problem.startswith(f"{knob} must be a non-empty list of distinct "), problem


def test_signals_then_sizing_then_the_backtest_carry_both_annotations_to_each_entry():
    # the shipped wiring: model rows -> VolRegimeSignals -> VolSizingWeights -> backtest. The
    # weights are the previous session's (hand-worked in test_nodes: size_implied 1.5, 0.96,
    # 1.6667, 0.8148 from the third row on), the gates survive the second node, and the ledger
    # records each entry's own row's values
    from index_options.nodes import VolRegimeSignals, VolSizingWeights

    days, chain, series = _trades(GATE_LEVELS)
    ivs, rvs = [20.0, 30.0, 24.0, 40.0, 22.0, 25.0], [0.010, 0.020, 0.015, 0.030, 0.010, 0.012]
    rows = [{**_row(d), "iv_index": iv, "rv_22": rv} for d, iv, rv in zip(days, ivs, rvs)]
    term = [{"instrument": "VIX3M", "date": d, "close": 25.0} for d in days]
    signalled = VolRegimeSignals("signals", {"min_history": 1}).run(
        None, {"rows": rows, "term": term})["rows"]
    sized = VolSizingWeights("sizing", {"min_history": 1}).run(None, {"rows": signalled})["rows"]
    metrics, report = _run(sized, chain, series, gate_fields=["gate_any"],
                           size_fields=["size_implied", "size_inv_implied_var"])
    got = [e["sizes"]["size_implied"] for e in report["ledger"]]
    assert got[:2] == [None, None]
    assert got[2:] == pytest.approx([1.5, 0.96, 40 / 24, 22 / 27], abs=1e-12)
    assert [e["sizes"]["size_inv_implied_var"] for e in report["ledger"]][2] == pytest.approx(
        400 / 900, abs=1e-12)
    assert all("gate_any" in e["gates"] for e in report["ledger"])
    weighted = _sized(CONDOR_MODEL_PNL, got)
    _assert_the_sized_book(metrics, "model", "size_implied", weighted)
    assert metrics["model_size_implied_n"] == 4 and metrics["model_size_implied_unknown_n"] == 2


# -- ADR-0197: the selector's outputs do not move when the max-loss rule gets its one owner -------
#
# PayoffSelectQuoteBacktest._score divided by ``multiplier x max(widths) - credit`` until ADR-0197
# made that contracts.structure_max_loss (the payoff's own worst point). The two must agree to the
# bit on every candidate, so the digests below were FROZEN from the ADR-0196 base (520a7d0) by
# running these very scenarios BEFORE the change (floats rounded to 6 places, as FROZEN above).

FROZEN_SELECT = {
    "mixed_winners": {
        "metrics": "cd6d4e70b98d3e5287b0c152c25df72dc594acfa828a0fefaadc08e2f990433e",
        "ledger": "62cb7f0600d5b30537bd138bfa45420653bd5565b0efdd2f0be762701b016519"},
    "random_market_0": {
        "metrics": "8d68a0f9ac3bdc556f4cbb5bacd5d5960294230193d28d44c2551ad6707cdc3e",
        "ledger": "1935073309fda4fd0f3bb4f375185b79aa76d115dd1b4520fe9af84bc7742f9c"},
    "random_market_1": {
        "metrics": "bb4dd29aba23055a8b0cf0114bfe00e63711d8da35bd700564ccaf3ccc9e77bf",
        "ledger": "03f9e54478f9a93db986e05c3d8174ae9897667eee6b98cc2e14b2dfa8835cc3"},
    "random_market_2": {
        "metrics": "767dad6354ed86c1d3dae4c1833cf82fe9fd97efe7e124dc842fbca68fb58d98",
        "ledger": "617643eab531ef3f8163a80f0b42bdbd2a9da0c80c489f4597ce50e55e166668"},
    "random_market_3": {
        "metrics": "2e871990e0cbc4af9b63d76b6e52275326a325bb0f848f0466b62cfc2fcb0a41",
        "ledger": "bd5d871b355058a1178e5e9809b991e9b78c24a7d9a8170525a232141aa71d21"},
    "random_market_4": {
        "metrics": "3bcb7b59a73fbeea79e79f46f69bc690d55645118e6bdedfec7804192f1cdbcd",
        "ledger": "5354de63d8f9e104d978cde391ac3d04048156caa6af5eb656016e8e4a1892fa"},
    "random_market_5": {
        "metrics": "493c5f33b2512b13b686dcf19d68cd5ac43af1e774ad6d8d9820c5672ff4de9d",
        "ledger": "e7ca6155c31e2c129092d3e9d7edf8ae801ed953b04499245e7a4c005298dbf7"},
    "smooth_market_full_grid": {
        "metrics": "cf7be1f24a72f82ce4e81e9adc7d967e952151b20b45fcfad29ded48dd9fb38b",
        "ledger": "ce27bf976a98b9cc9b30790670b1a1c7942287f6c79652419a765be6d98490d0"},
    "winner_call_spread": {
        "metrics": "a92573689f7b93752ef1fe14a5833f119e2c419216e14a17571b0fbfdd5be526",
        "ledger": "531def53a64293cee24dce8307c09de48ca6a147c1c5ca61f4e1315796d6dd19"},
    "winner_condor": {
        "metrics": "1a6ad0dc217d9ea4742859c0d05b96020339a0542476a985bde97cda0890e159",
        "ledger": "b16eaeb40dbfe244fb2954c60a3966a20a246ae40bfd174c68e4e404da60425e"},
    "winner_put_spread": {
        "metrics": "1465c72537a7b8ebc023008fe6abaa378418ad8872ebb8269ca9e90e5c14c871",
        "ledger": "133cfce5fa930cc50c3e554a24294ecf8faad817f095a5d8a53ff3e4989ab12f"},
}


def _frozen_select_scenarios():
    """name -> (forecasts, chain, closes, extra knobs): the three winners, a mix, random markets."""
    full = {"candidate_structures": ["put_spread", "call_spread", "condor"],
            "candidate_short_q": [0.10, 0.16, 0.20, 0.25, 0.30],
            "candidate_wing_z": [0.35, 0.65, 1.0]}
    scenarios = {}
    for label, quotes, draws, _table, _winner in SELECT_CASES:
        scenarios[f"winner_{label}"] = ([_select_row(samples=draws)], _quoted(quotes), FLAT, {})
    rows, chain, series = _mixed_winners()
    scenarios["mixed_winners"] = (rows, chain, series, {})
    scenarios["smooth_market_full_grid"] = ([_select_row(samples=NORMAL_DRAWS)], _smooth_market(),
                                            FLAT, full)
    for seed in range(6):
        rows, chain = _random_market(seed)
        scenarios[f"random_market_{seed}"] = (rows, chain, FLAT, full)
    return scenarios


@pytest.mark.parametrize("key", sorted(_frozen_select_scenarios()))
def test_the_selectors_metrics_and_ledger_are_what_adr_0196_shipped(key):
    rows, chain, series, over = _frozen_select_scenarios()[key]
    metrics, report = _run_select(rows, chain, series, **over)
    assert _digest(metrics) == FROZEN_SELECT[key]["metrics"]
    assert _digest(report["ledger"]) == FROZEN_SELECT[key]["ledger"]


# -- speedups pinned to the scans they replace -----------------------------------------------

def _random_listed(rng):
    """One expiry's ``{right: {strike: row}}`` with odd strikes and unquotable rows mixed in."""
    listed = {"put": {}, "call": {}}
    for right in listed:
        for _ in range(rng.randint(0, 25)):
            k = rng.choice([float(rng.randint(80, 120)), rng.uniform(80, 120), 0.0, -0.0,
                            math.inf, -math.inf, math.nan])
            listed[right][k] = {"bid": rng.choice([0.0, 1.0, 2.0, -1.0, math.nan, None, 3]),
                                "ask": rng.choice([0.0, 1.5, 2.5, math.nan, 4]),
                                "bid_size": rng.choice([0, 1, 5, -1, 1.5, True, None]),
                                "ask_size": rng.choice([0, 1, 5, 0.5]), "iv": 0.2}
    return listed


def test_the_bisect_snaps_equal_the_full_scans_on_randomized_chains():
    node, rng = CondorQuoteBacktest("bt", PARAMS), random.Random(17)
    for _ in range(5000):
        listed = _random_listed(rng)
        node._reset_entry_caches()
        forward, scale = rng.uniform(90, 110), rng.uniform(0.01, 0.2)
        z_put, z_call, wing = rng.uniform(-3, 0), rng.uniform(0, 3), rng.uniform(0.1, 1)
        long_t, short_t = (forward * math.exp(scale * z) for z in (z_put - wing, z_put))
        assert repr(node._snap_put(listed, forward, scale, z_put, wing)) == repr(
            node._scan_put(listed["put"], long_t, short_t))
        short_t, long_t = (forward * math.exp(scale * z) for z in (z_call, z_call + wing))
        assert repr(node._snap_call(listed, forward, scale, z_call, wing)) == repr(
            node._scan_call(listed["call"], short_t, long_t))


def test_a_row_only_the_full_scan_could_skip_falls_back_to_it():
    # an int size beyond float range raises in quote_problems; the scan never reaches the
    # strike above the put target, so the snap must not raise there either
    node = CondorQuoteBacktest("bt", PARAMS)
    listed = {"put": {90.0: {"bid": 1.0, "ask": 1.1, "bid_size": 5, "ask_size": 5},
                      95.0: {"bid": 2.0, "ask": 2.1, "bid_size": 5, "ask_size": 5},
                      120.0: {"bid": 1.0, "ask": 1.1, "bid_size": 10 ** 400, "ask_size": 5}},
              "call": {}}
    node._reset_entry_caches()
    assert node._snap_put(listed, 100.0, 0.05, -1.0, 1.0) == (90.0, 95.0)


def test_the_sliced_charges_equal_the_charges_on_the_whole_series():
    node = CondorQuoteBacktest("bt", PARAMS)
    days = _weekdays("2023-01-02", "2024-06-28")
    rng = random.Random(3)
    closes = [(d, 100.0 + rng.uniform(-8, 8)) for d in days]
    series = _series(closes, [(d, 0.5) for d in days[::40]])
    node._prepare({"chain": _chain(ENTRY, EXPIRY), "underlying": series})
    whole = node._closes["SPY"]
    for entry, settle in (("2023-03-01", "2023-04-14"), ("2024-01-02", "2024-02-16"),
                          ("2022-12-30", "2023-01-06"), ("2024-06-24", "2024-06-28")):
        window = node._charge_window("SPY", entry, settle)
        assert len(window) < len(whole)
        for short_put, short_call in ((97.0, 103.0), (110.0, 90.0)):
            assert american_short_charge(window, short_put, short_call, entry, settle, 0.055,
                                         100) == american_short_charge(
                whole, short_put, short_call, entry, settle, 0.055, 100)
        assert nodes.dividends_paid(window, entry, settle) == nodes.dividends_paid(
            whole, entry, settle)
    # a date contracts._day refuses is never sliced past: the owner still sees and refuses it
    assert node._charge_window("SPY", "2024-1-2", "2024-02-16") is whole


# ADR-0255 single-condor settlement: prices are per share, fees per contract.
def _expiry_selection(decision="a", arm="base"):
    return {
        "decision_id": decision, "arm_id": arm, "symbol": "XYZ",
        "quote_date": "2025-01-02", "expiry": "2025-02-02", "multiplier": 100,
        "legs": [
            {"role": role, "contract": role + "-XYZ", "right": right,
             "side": side, "strike": strike, "price_usd_per_share": premium,
             "fee_usd": 0.65}
            for role, right, side, strike, premium in [
                ("LP", "put", "buy", 90, 1), ("SP", "put", "sell", 95, 2),
                ("SC", "call", "sell", 105, 2), ("LC", "call", "buy", 110, 1)]
        ],
    }


def _expiry_node(**over):
    cls = getattr(nodes, "CondorExpirySettle", None)
    assert cls is not None, "ADR-0255 settlement adapter is missing"
    return cls("expiry", {
        "settlement_field": "as_traded_close", "max_settlement_gap_days": 4,
        "end_before": "2026-01-01",
        "labels": {"fill": "assumed same-day average fill",
                   "exercise": "early exercise and assignment ignored"}, **over})


def _expiry_bars(close=100):
    return [{"symbol": "XYZ", "date": "2025-01-31", "as_traded_close": close},
            {"symbol": "XYZ", "date": "2025-02-03", "as_traded_close": 100}]


@pytest.mark.parametrize("close,pnl", [(100, 197.4), (93, -2.6), (85, -302.6), (120, -302.6)])
def test_expiry_condor_reconciles_zero_intrinsic_and_per_share_fees(close, pnl):
    out = _expiry_node().run(None, {"selections": [_expiry_selection()],
                                  "bars": _expiry_bars(close), "skips": []})
    assert len(out["outcomes"]) == 1
    result = out["outcomes"][0]
    assert result["pnl_usd"] == pytest.approx(pnl)
    assert result["fees_usd"] == pytest.approx(2.6)
    assert result["end_flat"] is True
    assert result["settlement_date"] == "2025-01-31"
    assert result["settlement_confirmed_date"] == "2025-02-03"
    ev = out["evidence"]
    assert "fills" not in ev and "trades" not in ev
    assert len(ev["raw_fills"]) == len(ev["raw_orders"]) == 8
    assert [row["date"] for row in ev["raw_fills"]] == sorted(row["date"] for row in ev["raw_fills"])
    assert ev["totals"]["n_condors"] == 1
    assert all(row["qty"] == 100 for row in ev["raw_fills"])
    expiry = [row for row in ev["raw_fills"] if row["phase"] == "expiry"]
    assert all(row["fee_usd"] == 0 for row in expiry)
    assert any(row["price_usd_per_share"] == 0 for row in expiry)
    assert all(row["exercise_label"] == "early exercise and assignment ignored" for row in expiry)
    assert all(row["fill_label"] == "assumed same-day average fill" for row in ev["raw_fills"])


def test_expiry_positions_do_not_cross_net_between_decisions_or_arms():
    selections = [_expiry_selection("a"), _expiry_selection("b"), _expiry_selection("a", "other")]
    out = _expiry_node().run(None, {"selections": selections, "bars": _expiry_bars(), "skips": []})
    assert len(out["outcomes"]) == 3
    assert out["evidence"]["totals"]["net_pnl_usd"] == pytest.approx(592.2)
    positions = {row["position_id"] for row in out["evidence"]["raw_fills"]}
    assert len(positions) == 12


@pytest.mark.parametrize("bars,reason", [
    ([], "missing_settlement"),
    ([{"symbol": "XYZ", "date": "2025-01-31", "as_traded_close": 100}], "missing_settlement"),
    ([{"symbol": "XYZ", "date": "2025-01-27", "as_traded_close": 100},
      {"symbol": "XYZ", "date": "2025-02-03", "as_traded_close": 100}], "missing_settlement"),
])
def test_expiry_requires_bounded_close_and_later_confirmation(bars, reason):
    out = _expiry_node().run(None, {"selections": [_expiry_selection()], "bars": bars,
                                  "skips": [{"reason": "upstream"}]})
    assert out["outcomes"] == []
    assert [row["reason"] for row in out["skips"]] == ["upstream", reason]
    assert out["evidence"]["raw_fills"] == []


@pytest.mark.parametrize("mutate", [
    lambda rows, bars: rows.append(copy.deepcopy(rows[0])),
    lambda rows, bars: rows[0]["legs"][0].update(price_usd_per_share=float("nan")),
    lambda rows, bars: rows[0]["legs"][0].update(side="sell"),
    lambda rows, bars: rows[0]["legs"][0].update(fee_usd=-1),
    lambda rows, bars: rows[0]["legs"][0].update(fee_usd=float("inf")),
    lambda rows, bars: rows[0]["legs"][0].update(contracts=2),
    lambda rows, bars: rows[0]["legs"][0].update(multiplier=10),
    lambda rows, bars: rows[0].update(expiry="2026-01-01"),
    lambda rows, bars: bars.append({"symbol": "XYZ", "date": "2026-01-01", "as_traded_close": 100}),
    lambda rows, bars: bars.append(copy.deepcopy(bars[0])),
])
def test_expiry_malformed_or_protected_inputs_refuse(mutate):
    selections, bars = [_expiry_selection()], _expiry_bars()
    mutate(selections, bars)
    with pytest.raises(ValueError):
        _expiry_node().run(None, {"selections": selections, "bars": bars, "skips": []})


def test_expiry_boundary_and_gap_are_explicit_not_silent_defaults():
    cls = getattr(nodes, "CondorExpirySettle", None)
    assert cls is not None, "ADR-0255 settlement adapter is missing"
    for params in ({}, {"settlement_field": "close", "max_settlement_gap_days": 4,
                       "labels": {"fill": "x", "exercise": "y"}}):
        assert cls.validate_params(params)
    assert _expiry_node(max_settlement_gap_days=0).run(
        None, {"selections": [_expiry_selection()], "bars": _expiry_bars(), "skips": []}
    )["outcomes"] == []

def test_expiry_decimal_prices_and_nonhundred_multiplier():
    selection = _expiry_selection()
    selection["multiplier"] = 10
    selection["legs"][0]["price_usd_per_share"] = 1.03
    out = _expiry_node().run(None, {"selections": [selection], "bars": _expiry_bars(93.17),
                                  "skips": []})
    assert out["outcomes"][0]["pnl_usd"] == pytest.approx(-1.2)
    assert out["outcomes"][0]["closed_form_pnl_usd"] == pytest.approx(-1.2)


def test_expiry_preserves_inputs_and_reports_only_condor_statistics(tmp_path):
    from dskit.pipeline.kinds_report import RunReport
    from dskit.pipeline.node import NodeContext
    inputs = {"selections": [_expiry_selection()], "bars": _expiry_bars(), "skips": []}
    original = copy.deepcopy(inputs)
    out = _expiry_node().run(None, inputs)
    assert inputs == original
    report = RunReport("report", {"sections": ["stages"]})
    ctx = NodeContext(name="test", asof="2025-02-03", run_dir=str(tmp_path))
    result = report.run(ctx, {"replay": out["evidence"]})
    payload = json.loads(Path(result["path"]).read_text())
    assert "trades" not in payload and "summary_metrics" not in payload
    assert len(payload["stages"]["replay"]["raw_fills"]) == 8
    assert payload["stages"]["replay"]["totals"]["n_condors"] == 1


def test_expiry_cannot_use_a_close_before_its_entry():
    selection = _expiry_selection()
    selection["quote_date"] = "2025-02-01"
    out = _expiry_node().run(None, {"selections": [selection], "bars": _expiry_bars(),
                                  "skips": []})
    assert out["outcomes"] == []
    assert out["skips"][0]["reason"] == "missing_settlement"


@pytest.mark.parametrize("rho,status,robust", [(0., "trade", 197.4),
                                             (.001, "trade", 187.4),
                                             (.02, "no_trade", 0.)])
def test_projection_selection_and_expiry_share_exact_units(rho, status, robust):
    from dskit.pipeline.libs.predictive_cdf import MeanPreservingCDFGrid
    from index_options.nodes import RobustCondorSelect

    source = _expiry_selection()
    grid = [leg["strike"] for leg in source["legs"]]
    projection = MeanPreservingCDFGrid(
        grid, lambda x: min(1., max(0., (x-95.)/10.)), spot=100.,
        quadrature_tolerance=1e-8, payoff_tolerance=1e-7,
        cdf_tolerance=.51, w1_tolerance=.1, integration_limit=100)
    context = {key: source[key] for key in
               ("decision_id", "arm_id", "symbol", "quote_date", "expiry")}
    context.update(grid=grid, masses=projection.masses.tolist(), spot=100., rho=rho,
                   legs={leg["role"]: [{"index": i,
                                        "price": leg["price_usd_per_share"],
                                        "haircut": 0., "contract_id": leg["contract"]}]
                         for i, leg in enumerate(source["legs"])})
    selector = RobustCondorSelect("select", {
        "solver": "appsi_highs", "multiplier": 100, "fee_per_contract_usd": .65,
        "tie_tolerance_usd": 1e-6, "max_absolute_gap_usd": 1e-7,
        "max_relative_gap": 1e-8})
    selected = selector.run(None, {"context": context})["decision"]
    assert selected["status"] == status
    assert selected["robust_value_usd"] == pytest.approx(robust, abs=1e-6)
    settled = _expiry_node().run(None, {
        "selections": [selected] if status == "trade" else [],
        "bars": _expiry_bars(93), "skips": []})
    assert len(settled["outcomes"]) == (status == "trade")
    assert len(settled["evidence"]["raw_fills"]) == (8 if status == "trade" else 0)
    if status == "trade":
        assert settled["outcomes"][0]["pnl_usd"] == pytest.approx(-2.6)
        assert settled["outcomes"][0]["end_flat"] is True


@pytest.mark.parametrize("status", ["skipped", "no_trade", "nonsense", None, False])
def test_expiry_explicit_nontrade_status_refuses_before_any_accounting(status, monkeypatch):
    valid = _expiry_selection()
    invalid = _expiry_selection("invalid")
    invalid["status"] = status
    monkeypatch.setattr(nodes.WindowBook, "apply",
                        lambda *args: pytest.fail("invalid decision reached accounting"))
    with pytest.raises(ValueError, match="status"):
        _expiry_node().run(None, {"selections": [valid, invalid],
                                 "bars": _expiry_bars(), "skips": []})


def test_expiry_explicit_trade_and_statusless_compatibility():
    row = _expiry_selection()
    expected = _expiry_node().run(None, {"selections": [row], "bars": _expiry_bars(), "skips": []})
    row["status"] = "trade"
    actual = _expiry_node().run(None, {"selections": [row], "bars": _expiry_bars(), "skips": []})
    assert actual == expected

def test_expiry_condor_preserves_forecast_label_date_when_supplied():
    selection=_expiry_selection()
    selection["forecast_settlement_date"]="2025-01-30"
    out=_expiry_node().run(None,dict(selections=[selection],
                                   bars=_expiry_bars(),skips=[]))
    assert not out["outcomes"]
    assert out["skips"][0]["reason"] == "forecast_settlement_mismatch"


# -- ADR-0256 S3: entry dated at the fill, unfilled selections, expiry disclosure flags ------------
def _filled(**over):
    row = _expiry_selection()
    row.update(status="trade", fill_date="2025-01-03", **over)
    return row


def _settle(selections, close=100, **params):
    return _expiry_node(**params).run(None, {"selections": selections,
                                             "bars": _expiry_bars(close), "skips": []})


def test_expiry_condor_entry_fills_are_dated_at_fill_date_when_present():
    out = _settle([_filled()])
    entry = [row for row in out["evidence"]["raw_fills"] if row["phase"] == "entry"]
    assert {row["date"] for row in entry} == {"2025-01-03"}
    assert {row["date"] for row in out["evidence"]["raw_fills"] if row["phase"] == "expiry"} \
        == {"2025-02-02"}
    legacy = _settle([_expiry_selection()])
    assert {row["date"] for row in legacy["evidence"]["raw_fills"] if row["phase"] == "entry"} \
        == {"2025-01-02"}
    # only the date moves: the P&L is the same intrinsic settlement
    assert out["outcomes"][0]["pnl_usd"] == legacy["outcomes"][0]["pnl_usd"]
    assert out["outcomes"][0]["quote_date"] == "2025-01-02"


@pytest.mark.parametrize("day", ["2025-01-02", "2025-01-01", "2025-02-02", "2025-02-03", "x", 5])
def test_expiry_condor_fill_date_must_fall_between_decision_and_expiry(day):
    row = _filled()
    row["fill_date"] = day
    with pytest.raises(ValueError, match="fill_date"):
        _settle([row])


def _unfilled(decision="u", reason="missing_fill_bar"):
    row = _expiry_selection(decision)
    row.update(status="unfilled", unfilled_reason=reason)
    return row


def test_expiry_condor_unfilled_selections_are_skipped_and_counted_never_settled():
    out = _settle([_filled(), _unfilled("u1"), _unfilled("u2", "fill_liquidity"),
                   _unfilled("u3")])
    assert len(out["outcomes"]) == 1 and len(out["evidence"]["raw_fills"]) == 8
    assert [(s["decision_id"], s["reason"], s["unfilled_reason"]) for s in out["skips"]] == [
        ("u1", "unfilled", "missing_fill_bar"), ("u2", "unfilled", "fill_liquidity"),
        ("u3", "unfilled", "missing_fill_bar")]
    assert out["evidence"]["unfilled_by_reason"] == {"fill_liquidity": 1, "missing_fill_bar": 2}
    assert out["evidence"]["totals"]["n_condors"] == 1
    # no unfilled selections: the legacy evidence has no such key
    assert "unfilled_by_reason" not in _settle([_filled()])["evidence"]


def test_expiry_condor_unfilled_still_needs_identity_and_a_reason():
    bad = _unfilled()
    del bad["unfilled_reason"]
    with pytest.raises(ValueError, match="unfilled"):
        _settle([bad])
    with pytest.raises(ValueError, match="duplicate"):
        _settle([_unfilled(), _unfilled()])
    with pytest.raises(ValueError, match="status"):
        _settle([{**_expiry_selection("z"), "status": "pending"}])


@pytest.mark.parametrize("close,itm,pin,shares", [
    (100, [], [], 0),
    (93, ["SP"], [], 100),
    (87, ["LP", "SP"], [], 0),
    (107, ["SC"], [], -100),
    (120, ["SC", "LC"], [], 0),
    (94.99, ["SP"], [], 100),
    (94.995, [], ["SP"], 0),
    (95, [], ["SP"], 0),
    (95.01, [], [], 0),
    (104.99, [], [], 0),
    (105, [], ["SC"], 0),
    (105.005, [], ["SC"], 0),
    (105.01, ["SC"], [], -100),
    (110.01, ["SC", "LC"], [], 0),
])
def test_expiry_condor_discloses_itm_pin_zone_and_would_deliver_shares(close, itm, pin, shares):
    out = _settle([_filled()], close, exercise_threshold_usd=0.01)
    outcome = out["outcomes"][0]
    flags = {flag["role"]: flag for flag in outcome["exercise_flags"]}
    assert [flag["role"] for flag in outcome["exercise_flags"]] == ["LP", "SP", "SC", "LC"]
    assert sorted(r for r, f in flags.items() if f["itm"]) == sorted(itm)
    assert sorted(r for r, f in flags.items() if f["pin_zone"]) == sorted(pin)
    assert outcome["would_deliver_shares"] == shares
    assert outcome["pnl_usd"] == _settle([_filled()], close)["outcomes"][0]["pnl_usd"]
    disclosure = out["evidence"]["exercise_disclosure"]
    assert disclosure == {"exercise_threshold_usd": 0.01, "n_itm_legs": len(itm),
                          "n_pin_zone_legs": len(pin),
                          "n_would_deliver_shares": int(shares != 0)}


def test_expiry_condor_without_a_threshold_omits_every_flag():
    out = _settle([_filled()], 93)
    assert "exercise_flags" not in out["outcomes"][0]
    assert "would_deliver_shares" not in out["outcomes"][0]
    assert "exercise_disclosure" not in out["evidence"]


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "0.01", None])
def test_expiry_condor_exercise_threshold_must_be_a_positive_number(value):
    cls = nodes.CondorExpirySettle
    params = {"settlement_field": "x", "max_settlement_gap_days": 4, "end_before": "2026-01-01",
              "labels": {"fill": "a", "exercise": "b"}}
    assert cls.validate_params(params) == []
    assert cls.validate_params({**params, "exercise_threshold_usd": value})
    assert cls.validate_params({**params, "exercise_threshold_usd": 0.01}) == []
