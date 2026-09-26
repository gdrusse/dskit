"""ADR-0187: ``CondorQuoteBacktest`` over archived end-of-day quotes.

Every scenario is scripted: forecast rows whose draws have hand-known
quantiles, one chain per date with explicit (bid, ask, sizes) per strike,
and an underlying series with closes and ex-dates. Expected strikes,
credits and P&L are restated by hand in the assertions.
"""

import copy
import math
from datetime import date, timedelta
from statistics import NormalDist
from types import SimpleNamespace

import pytest

from dskit.pipeline.base import ConfigError

from index_options.contracts import american_short_charge
from index_options.nodes import CondorBacktest, CondorQuoteBacktest

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


def _run(forecasts, chain, underlying, **over):
    node = CondorQuoteBacktest("bt", {**PARAMS, **over})
    out = node.run(CTX, {"forecasts": forecasts, "chain": chain, "underlying": underlying})
    return out["metrics"], out["report"].value


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


def test_a_missing_dividend_in_the_window_refuses():
    series = _series([(d, 100.0) for d in SESSIONS])
    series[3]["dividend_amount"] = None
    with pytest.raises(ValueError, match="dividend_amount"):
        _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), series)


# -- expiry, strikes and quotability ------------------------------------------------------


def test_the_nearest_listed_expiry_is_taken_and_a_day_without_chain_is_skipped():
    chain = _chain(ENTRY, "2024-03-08") + _chain(ENTRY, "2024-03-11")
    metrics, report = _run([_row(ENTRY), _row("2024-03-04"), _row("2024-03-11")], chain, FLAT)
    assert [e["expiry"] for e in report["ledger"]] == ["2024-03-08"]
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
    assert report["ledger"][0]["books"]["model"]["strikes"] is None


def test_a_target_outside_the_band_is_counted():
    metrics, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT,
                           max_abs_log_moneyness=0.06)  # the long call target sits at +0.075
    assert metrics["model_n_skipped_target_outside_band"] == 1
    assert metrics["always_n_skipped_target_outside_band"] == 1
    assert metrics["implied_n_trades"] == 1  # its targets stay within +-0.05


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


def test_min_edge_gates_only_the_model_book():
    metrics, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT, min_edge_usd=1e6)
    assert metrics["model_n_skipped_below_min_edge"] == 1 and metrics["model_n_trades"] == 0
    assert metrics["always_n_trades"] == 1 and metrics["implied_n_trades"] == 1
    assert report["ledger"][0]["books"]["model"]["pnl_usd"] is None


def test_the_horizon_rescale_is_sqrt_sessions_over_label_horizon():
    _, report = _run([_row(ENTRY)], _chain(ENTRY, EXPIRY), FLAT, label_horizon=20)
    entry = report["ledger"][0]
    assert entry["horizon_scale"] == pytest.approx(0.05 * math.sqrt(5 / 20))
    # tighter scale: 100 e^{-0.025} = 97.53 -> 97; 100 e^{-0.0375} = 96.32 -> 96 ...
    assert entry["books"]["model"]["strikes"] == [96.0, 97.0, 103.0, 104.0]


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
])
def test_knob_refusals(change):
    with pytest.raises(ConfigError):
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
