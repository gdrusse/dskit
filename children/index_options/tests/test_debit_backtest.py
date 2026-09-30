"""ADR-0197: the debit (positive-convexity) structures on archived end-of-day quotes.

``LongStraddleQuoteBacktest``, ``LongCallSpreadQuoteBacktest`` and ``LongPutSpreadQuoteBacktest`` are
``DebitStructureQuoteBacktest`` members: the condor backtest's walk, settlement, side split, delta
benchmark, gates and sizing, with the strikes chosen by each member's ``_target_strikes`` and the
entry priced as a DEBIT (long legs at the ask, short legs at the bid, plus fees). Every scenario is
scripted on the helpers of ``test_quote_backtest`` (one worked chain per date, hand-known
quantiles) and every expected number is restated by hand below; nothing is read back from a node.

The worked entry is the condor's: Friday 2024-03-01 at 100, expiry 2024-03-08 (DTE 7, five
sessions), horizon scale s' = 0.05 (``reference_scale`` 0.05 x sqrt(5 / label_horizon 5)), settling
at 97 (``FLAT``). Quotes are (bid, ask); every strike not named is (0.5, 0.6) with size 10.
"""

import math
from statistics import NormalDist

import pytest
from test_quote_backtest import (
    CTX,
    DRAWS,
    ENTRY,
    EXPIRY,
    FLAT,
    GATE_A,
    GATE_LEVELS,
    PARAMS,
    SESSIONS,
    SIZE_W,
    _assert_the_sized_book,
    _assert_the_wing_split,
    _chain,
    _forward_delta,
    _gated_rows,
    _row,
    _series,
    _set,
    _settled_at,
    _sized,
    _t,
    _three_trades,
    _trades,
)

from index_options import contracts, nodes
from index_options.contracts import american_call_dividend, american_put_carry
from index_options.nodes import (
    CondorQuoteBacktest,
    DebitStructureQuoteBacktest,
    LongCallSpreadQuoteBacktest,
    LongPutSpreadQuoteBacktest,
    LongStraddleQuoteBacktest,
)

#: The shared knobs, restated here; a spread adds long_q and wing_z, the straddle neither.
COMMON_KNOBS = {"split", "multiplier", "fee_per_leg", "dte_min", "dte_max", "max_abs_log_moneyness",
                "label_horizon", "carry_rate", "min_edge_usd", "cvar_alpha", "gate_fields",
                "size_fields"}
STRADDLE_BASE = {k: v for k, v in PARAMS.items() if k not in ("short_q", "wing_z")}
SPREAD_BASE = {k: v for k, v in PARAMS.items() if k != "short_q"}           # wing_z 0.5
#: member -> (class, sides, required-and-default params, the report kind, units, legs)
MEMBERS = {
    "straddle": (LongStraddleQuoteBacktest, ("put", "call"), STRADDLE_BASE,
                 "archived_quote_long_straddle_backtest",
                 "USD per long straddle, one contract per leg", (("put", 1), ("call", 1))),
    "call_spread": (LongCallSpreadQuoteBacktest, ("call",), SPREAD_BASE,
                    "archived_quote_long_call_spread_backtest",
                    "USD per long call spread, one contract per leg", (("call", 1), ("call", -1))),
    "put_spread": (LongPutSpreadQuoteBacktest, ("put",), SPREAD_BASE,
                   "archived_quote_long_put_spread_backtest",
                   "USD per long put spread, one contract per leg", (("put", -1), ("put", 1))),
}
SPREADS = ("call_spread", "put_spread")


def _run_member(member, forecasts, chain, underlying, **over):
    """Run one member on scripted inputs; the wing split is checked on every entry of every run."""
    cls, sides, base, *_ = MEMBERS[member]
    node = cls("bt", {**base, **over})
    out = node.run(CTX, {"forecasts": forecasts, "chain": chain, "underlying": underlying})
    _assert_the_wing_split(sides, out["report"].value["ledger"])
    return out["metrics"], out["report"].value


def _worked(member, draws, chain, series=FLAT, **over):
    """One entry on the worked date: ``(metrics, report, the entry)``."""
    metrics, report = _run_member(member, [_row(ENTRY, samples=list(draws))], chain, series, **over)
    return metrics, report, report["ledger"][0]


# -- the three worked entries, every number by hand ---------------------------------------------------
#
# LONG STRADDLE. One strike for both legs: the strike nearest the forward 100 where BOTH the put and
# the call can be bought: 100. Quotes put100 (2.3, 2.5), call100 (2.4, 2.6): debit (2.5 + 2.6) x 100 +
# 2 x 0.65 = 511.3. Settled at 97 the put is worth 3: P&L -511.3 + 300 = -211.3. The wide forecast,
# half its draws at z = -3 (86.07) and half at +3 (116.18), pays on average
# (100 - 100 e^-0.15 + 100 e^0.15 - 100) / 2 = 15.056 a share: E_P = -511.3 + 1505.6 = 994.3.
WIDE_DRAWS = [-3.0] * 5 + [3.0] * 5
STRADDLE_DEBIT = (2.5 + 2.6) * 100 + 2 * 0.65
STRADDLE_EXPECTED = -STRADDLE_DEBIT + 100 * 0.5 * ((100 - 100 * math.exp(-0.15))
                                                  + (100 * math.exp(0.15) - 100))
#
# LONG CALL SPREAD at long_q 0.5 (the default), wing_z 0.5. The draws below have Q(0.5) = +1, so the
# long call targets 100 e^0.05 = 105.13 and snaps UP to 106 (ask 1.6); the short call targets z = 1.5:
# 100 e^0.075 = 107.79 and snaps up to 108 (bid 0.7). Debit (1.6 - 0.7) x 100 + 1.3 = 91.3, width 2.
# Settled at 110 it is worth its width: +200 - 91.3 = 108.7; at 97 it expires: -91.3. Five of the ten
# draws sit at z = 3 (116.18, through both strikes: 2.0 a share), the rest below 106:
# E_P = -91.3 + 100 x 5 x 2 / 10 = 8.7.
CALL_DRAWS = [-1.0, -1.0, 0.0, 0.0, 1.0, 3.0, 3.0, 3.0, 3.0, 3.0]
CALL_SPREAD_DEBIT = (1.6 - 0.7) * 100 + 2 * 0.65
CALL_SPREAD_EXPECTED = -CALL_SPREAD_DEBIT + 100 * 5 * 2.0 / 10
#
# LONG PUT SPREAD at long_q 0.35 (the default), wing_z 0.5. The draws below have Q(0.35) = -1, so the
# long put targets 100 e^-0.05 = 95.12 and snaps DOWN to 95 (here ask 1.5); the short put targets
# z = -1.5: 100 e^-0.075 = 92.77 and snaps down to 92 (here bid 1.2). Debit (1.5 - 1.2) x 100 + 1.3
# = 31.3, width 3, listed low to high: (92, 95). Settled at 90 it is worth its width: +300 - 31.3 =
# 268.7; at 97 it expires: -31.3. Three of ten draws sit at z = -3 (86.07: 3.0 a share):
# E_P = -31.3 + 100 x 3 x 3 / 10 = 58.7.
PUT_DRAWS = [-3.0] * 3 + [-1.0] + [0.0] * 4 + [1.0] * 2
PUT_SPREAD_DEBIT = (1.5 - 1.2) * 100 + 2 * 0.65
PUT_SPREAD_EXPECTED = -PUT_SPREAD_DEBIT + 100 * 3 * 3.0 / 10


def _straddle_chain(day=ENTRY, expiry=EXPIRY, **over):
    return _set(_chain(day, expiry), put100={"bid": 2.3, "ask": 2.5},
                call100={"bid": 2.4, "ask": 2.6}, **over)


def _put_spread_chain(day=ENTRY, expiry=EXPIRY):
    return _set(_chain(day, expiry), put95={"bid": 1.4, "ask": 1.5}, put92={"bid": 1.2, "ask": 1.3})


#: member -> (draws, the chain, strikes, credit, expected E_P, {settlement level: P&L})
WORKED = {
    "straddle": (WIDE_DRAWS, _straddle_chain, [100.0, 100.0], -STRADDLE_DEBIT, STRADDLE_EXPECTED,
                 {97.0: -STRADDLE_DEBIT + 300, 80.0: -STRADDLE_DEBIT + 2000,
                  110.0: -STRADDLE_DEBIT + 1000, 100.0: -STRADDLE_DEBIT}),
    "call_spread": (CALL_DRAWS, lambda: _chain(ENTRY, EXPIRY), [106.0, 108.0], -CALL_SPREAD_DEBIT,
                    CALL_SPREAD_EXPECTED, {97.0: -CALL_SPREAD_DEBIT, 110.0: -CALL_SPREAD_DEBIT + 200,
                                           107.0: -CALL_SPREAD_DEBIT + 100}),
    "put_spread": (PUT_DRAWS, _put_spread_chain, [92.0, 95.0], -PUT_SPREAD_DEBIT,
                   PUT_SPREAD_EXPECTED, {97.0: -PUT_SPREAD_DEBIT, 90.0: -PUT_SPREAD_DEBIT + 300,
                                         93.5: -PUT_SPREAD_DEBIT + 150}),
}


@pytest.mark.parametrize("member", WORKED)
def test_the_worked_entry_by_hand(member):
    draws, chain, strikes, credit, expected, levels = WORKED[member]
    metrics, report, entry = _worked(member, draws, chain())
    _cls, _sides, _base, kind, units, legs = MEMBERS[member]
    assert report["kind"] == kind and report["units"] == units
    assert report["pricing"] == "archived_eod_quotes" and report["decision_eligible"] is False
    assert (entry["date"], entry["expiry"], entry["settle_date"], entry["dte"],
            entry["sessions"]) == (ENTRY, EXPIRY, EXPIRY, 7, 5)
    assert entry["horizon_scale"] == pytest.approx(0.05)
    for book in ("model", "always"):
        cell = entry["books"][book]
        assert cell["strikes"] == strikes
        assert cell["credit_usd"] == pytest.approx(credit)           # a debit is a negative credit
        assert cell["entered"] is True and cell["reason"] is None
        assert cell["american_charge_usd"] == 0.0
        assert cell["pnl_usd"] == pytest.approx(levels[97.0])      # settled at 97: FLAT
    assert entry["model_expected_pnl_usd"] == pytest.approx(expected)
    assert expected > 0                                              # so the model book enters
    assert metrics["model_n_trades"] == metrics["always_n_trades"] == 1
    assert metrics["model_mean_credit_usd"] == pytest.approx(credit)
    assert len(strikes) == len(legs)


@pytest.mark.parametrize("member", WORKED)
def test_the_pnl_at_each_settlement_is_the_debit_paid_plus_the_payoff(member):
    draws, chain, strikes, credit, _expected, levels = WORKED[member]
    for level, pnl in levels.items():
        _, _, entry = _worked(member, draws, chain(), _settled_at(level))
        for book in ("model", "always"):
            cell = entry["books"][book]
            assert cell["pnl_usd"] == pytest.approx(pnl), (member, level, book)
            assert cell["american_charge_usd"] == 0.0     # no ex-date, no close through a short leg


def test_the_straddles_sides_split_the_debit_leg_by_leg():
    # put side: -(2.5 x 100 + 0.65) + 3 x 100 = 49.35; call side: -(2.6 x 100 + 0.65) = -260.65
    _, _, entry = _worked("straddle", WIDE_DRAWS, _straddle_chain())
    sides = entry["books"]["model"]["side_pnl_usd"]
    assert list(sides) == ["put", "call"]
    assert sides["put"] == pytest.approx(49.35) and sides["call"] == pytest.approx(-260.65)
    assert sum(sides.values()) == pytest.approx(-STRADDLE_DEBIT + 300)


def test_the_spreads_sides_are_the_one_wing_they_hold():
    _, _, call = _worked("call_spread", CALL_DRAWS, _chain(ENTRY, EXPIRY), _settled_at(110.0))
    assert list(call["books"]["model"]["side_pnl_usd"]) == ["call"]
    assert call["books"]["model"]["side_pnl_usd"]["call"] == call["books"]["model"]["pnl_usd"]
    _, _, put = _worked("put_spread", PUT_DRAWS, _put_spread_chain(), _settled_at(90.0))
    assert list(put["books"]["model"]["side_pnl_usd"]) == ["put"]
    assert put["books"]["model"]["side_pnl_usd"]["put"] == put["books"]["model"]["pnl_usd"]


# -- the implied book: the VIX-lognormal quantile and the chain's own ATM vol -------------------------
#
# s = 0.2 sqrt(7 / 365) = 0.027697 (the chain's ATM iv 0.2, calendar DTE). A target at probability p is
# 100 exp(s (z_p - s / 2)) with z_p the standard normal quantile: the -s^2 / 2 drift is the condor's.

S_IMPLIED = 0.2 * math.sqrt(7 / 365)


def _implied_target(p, extra=0.0, iv=0.2):
    s = iv * math.sqrt(7 / 365)
    return 100 * math.exp(s * (NormalDist().inv_cdf(p) - s / 2 + extra))


def test_the_implied_books_strikes_come_from_the_vix_lognormal_quantile():
    # call spread at p = 0.5: long target 99.96 snaps UP to 100; the short sits wing_z = 0.5 further:
    # 101.36, up to 102. Debit (0.6 - 0.5) x 100 + 1.3 = 11.3, on quotes neither model leg uses
    assert 99.9 < _implied_target(0.5) < 100 and 101 < _implied_target(0.5, 0.5) < 102
    _, _, entry = _worked("call_spread", CALL_DRAWS, _chain(ENTRY, EXPIRY))
    implied = entry["books"]["implied"]
    assert implied["strikes"] == [100.0, 102.0]
    assert implied["credit_usd"] == pytest.approx(-(0.1 * 100 + 1.3)) and implied["pnl_usd"] == \
        pytest.approx(-11.3)
    # put spread at p = 0.35: the long put targets 98.90 (snap DOWN to 98), the short 97.54 (97)
    assert 98 < _implied_target(0.35) < 99 and 97 < _implied_target(0.35, -0.5) < 98
    _, _, entry = _worked("put_spread", PUT_DRAWS, _put_spread_chain())
    implied = entry["books"]["implied"]
    assert implied["strikes"] == [97.0, 98.0]
    assert implied["credit_usd"] == pytest.approx(-11.3)
    assert implied["pnl_usd"] == pytest.approx(-11.3 + 100)          # settled 97: the long 98 put is 1
    # the straddle has no quantile: the implied book trades the forecast books' own strike
    _, _, entry = _worked("straddle", WIDE_DRAWS, _straddle_chain())
    assert entry["books"]["implied"]["strikes"] == [100.0, 100.0]
    assert entry["books"]["implied"]["pnl_usd"] == pytest.approx(-STRADDLE_DEBIT + 300)


def test_the_implied_targets_carry_the_minus_s_squared_over_two_drift():
    # at iv 0.6 the horizon scale is s = 0.6 sqrt(7 / 365) = 0.0831 and the drift s / 2 = 0.0415 is
    # big enough to move a snap. Call spread, p = 0.5: the short call at z = 0.5 - s / 2 targets
    # 103.88 and snaps up to 104 (WITHOUT the drift 104.24, which would snap to 105); the long call
    # 99.66 up to 100. Put spread at long_q 0.5: the long put targets 99.66 and snaps DOWN to 99
    # (without the drift: exactly 100 -> 100), the short 95.60 down to 95
    assert 103 < _implied_target(0.5, 0.5, iv=0.6) < 104 and _implied_target(0.5, iv=0.6) < 100
    _, _, entry = _worked("call_spread", CALL_DRAWS, _chain(ENTRY, EXPIRY, iv=0.6))
    assert entry["atm_iv"] == pytest.approx(0.6)
    assert entry["books"]["implied"]["strikes"] == [100.0, 104.0]
    assert 95 < _implied_target(0.5, -0.5, iv=0.6) < 96 and 99 < _implied_target(0.5, iv=0.6) < 100
    _, _, entry = _worked("put_spread", PUT_DRAWS, _chain(ENTRY, EXPIRY, iv=0.6), long_q=0.5)
    assert entry["books"]["implied"]["strikes"] == [95.0, 99.0]
    # the forecast books do not read the chain's iv at all
    assert entry["books"]["always"]["strikes"] == [
        _worked("put_spread", PUT_DRAWS, _chain(ENTRY, EXPIRY, iv=0.2), long_q=0.5)[2]
        ["books"]["always"]["strikes"]][0]


@pytest.mark.parametrize("member", WORKED)
def test_the_implied_book_needs_an_atm_iv_and_the_forecast_books_do_not(member):
    draws, chain, _strikes, _credit, _expected, _levels = WORKED[member]
    rows = chain()
    for q in rows:
        q["iv"] = None
    metrics, _, entry = _worked(member, draws, rows)
    assert entry["books"]["implied"]["reason"] == "no_atm_iv" and entry["atm_iv"] is None
    assert entry["books"]["model"]["entered"] and entry["books"]["always"]["entered"]
    assert metrics["implied_n_skipped_no_atm_iv"] == 1 and metrics["implied_n_trades"] == 0


# -- the model book's entry gate and the always book ---------------------------------------------------


@pytest.mark.parametrize("member", WORKED)
def test_the_model_book_enters_when_the_expected_pnl_clears_min_edge_and_always_enters_anyway(member):
    draws, chain, _strikes, _credit, expected, _levels = WORKED[member]
    _, _, entry = _worked(member, draws, chain(), min_edge_usd=expected - 1.0)
    assert entry["books"]["model"]["entered"] is True
    metrics, _, entry = _worked(member, draws, chain(), min_edge_usd=expected + 1.0)
    assert entry["books"]["model"]["entered"] is False
    assert entry["books"]["model"]["reason"] == "below_min_edge"
    assert entry["books"]["always"]["entered"] is True and entry["books"]["implied"]["entered"]
    assert metrics["model_n_skipped_below_min_edge"] == 1 and metrics["model_n_trades"] == 0
    assert entry["model_expected_pnl_usd"] == pytest.approx(expected)    # recorded either way


def test_a_forecast_that_loses_on_average_is_below_min_edge_by_default():
    # the plain DRAWS pay 2.0008 a share on the straddle: 100 x 2.0008 - 511.3 < 0
    _, _, entry = _worked("straddle", DRAWS, _straddle_chain())
    assert entry["model_expected_pnl_usd"] == pytest.approx(
        -STRADDLE_DEBIT + 100 * 0.2 * ((100 - 100 * math.exp(-0.05)) + (100 * math.exp(0.05) - 100)))
    assert entry["books"]["model"]["reason"] == "below_min_edge"
    assert entry["books"]["always"]["entered"] is True


# -- where each spread's strikes go: outward, strictly beyond the long leg -----------------------------


def _unquotable(chain, right, strike, **fields):
    for q in chain:
        if q["right"] == right and q["strike"] == strike:
            q.update(fields)
    return chain


def test_the_long_call_snaps_up_to_a_buyable_strike_and_the_short_call_up_to_a_sellable_one():
    # targets 105.13 and 107.79 on the 1-point grid: 106 and 108. Take 106 out of the market for
    # BUYING (no ask size): the long leg moves up to 107; its short leg still finds 108
    chain = _unquotable(_chain(ENTRY, EXPIRY), "call", 106.0, ask_size=0)
    _, _, entry = _worked("call_spread", CALL_DRAWS, chain)
    assert entry["books"]["model"]["strikes"] == [107.0, 108.0]
    # a long call at 106 that can be BOUGHT does not need a bid: only the short leg is judged sellable
    chain = _unquotable(_chain(ENTRY, EXPIRY), "call", 106.0, bid=0.0)
    _, _, entry = _worked("call_spread", CALL_DRAWS, chain)
    assert entry["books"]["model"]["strikes"] == [106.0, 108.0]
    # the short leg needs a bid and a bid size: 108 without one moves it up to 109
    for fields in ({"bid": 0.0}, {"bid_size": 0}):
        chain = _unquotable(_chain(ENTRY, EXPIRY), "call", 108.0, **fields)
        _, _, entry = _worked("call_spread", CALL_DRAWS, chain)
        assert entry["books"]["model"]["strikes"] == [106.0, 109.0], fields
    # and a short call needs no ask: 108 sells at its bid however the ask looks
    chain = _unquotable(_chain(ENTRY, EXPIRY), "call", 108.0, ask=0.75)
    _, _, entry = _worked("call_spread", CALL_DRAWS, chain)
    assert entry["books"]["model"]["strikes"] == [106.0, 108.0]


def test_the_short_call_sits_strictly_above_a_long_call_that_snapped_past_its_target():
    # 106 and 107 cannot be bought, so the long call lands on 108: past the short's own target of
    # 107.79. The short must still lie ABOVE it, so it takes the next sellable strike, 109
    chain = _chain(ENTRY, EXPIRY)
    for strike in (106.0, 107.0):
        _unquotable(chain, "call", strike, ask_size=0)
    _, _, entry = _worked("call_spread", CALL_DRAWS, chain)
    assert entry["books"]["model"]["strikes"] == [108.0, 109.0]


def test_the_long_put_snaps_down_to_a_buyable_strike_and_the_short_put_down_to_a_sellable_one():
    # targets 95.12 and 92.77: 95 and 92. 95 not buyable: the long put moves DOWN to 94
    chain = _unquotable(_put_spread_chain(), "put", 95.0, ask=0.0)
    _, _, entry = _worked("put_spread", PUT_DRAWS, chain)
    assert entry["books"]["model"]["strikes"] == [92.0, 94.0]
    # 92 not sellable: the short put moves down to 91
    for fields in ({"bid": 0.0}, {"bid_size": 0}):
        chain = _unquotable(_put_spread_chain(), "put", 92.0, **fields)
        _, _, entry = _worked("put_spread", PUT_DRAWS, chain)
        assert entry["books"]["model"]["strikes"] == [91.0, 95.0], fields


def test_the_short_put_sits_strictly_below_a_long_put_that_snapped_past_its_target():
    # 95, 94 and 93 cannot be bought: the long put lands on 92 (below the short's own target of
    # 92.77); the short must still be BELOW it: 91. Listed low to high: (91, 92)
    chain = _put_spread_chain()
    for strike in (95.0, 94.0, 93.0):
        _unquotable(chain, "put", strike, ask_size=0)
    _, _, entry = _worked("put_spread", PUT_DRAWS, chain)
    assert entry["books"]["model"]["strikes"] == [91.0, 92.0]


@pytest.mark.parametrize("member, right, strikes", [
    ("call_spread", "call", range(88, 113)), ("put_spread", "put", range(88, 113))])
def test_a_spread_with_no_quotable_strike_on_a_leg_is_counted_not_entered(member, right, strikes):
    draws, chain, *_ = WORKED[member]
    rows = chain()
    for k in strikes:                                        # nothing on that right can be bought
        _unquotable(rows, right, float(k), ask_size=0)
    metrics, _, entry = _worked(member, draws, rows)
    for book in ("model", "always"):
        assert entry["books"][book]["reason"] == "no_quotable_strike"
        assert metrics[f"{book}_n_skipped_no_quotable_strike"] == 1
    rows = chain()
    for k in strikes:                                        # ... and nothing can be SOLD
        _unquotable(rows, right, float(k), bid=0.0)
    _, _, entry = _worked(member, draws, rows)
    assert entry["books"]["model"]["reason"] == "no_quotable_strike"


def test_the_short_leg_beyond_the_top_of_the_chain_is_no_quotable_strike():
    # the short call's target 107.79 needs a sellable strike at or above it AND above the long leg:
    # with the chain cut at 106 there is none
    _, _, entry = _worked("call_spread", CALL_DRAWS, _chain(ENTRY, EXPIRY, strikes=range(88, 107)))
    assert entry["books"]["model"]["reason"] == "no_quotable_strike"
    # the put spread's short target 92.77 with nothing listed at or below 94
    _, _, entry = _worked("put_spread", PUT_DRAWS, _set(_chain(ENTRY, EXPIRY, strikes=range(94, 113)),
                                                        put95={"bid": 1.4, "ask": 1.5}))
    assert entry["books"]["model"]["reason"] == "no_quotable_strike"


@pytest.mark.parametrize("member", SPREADS)
def test_a_target_beyond_the_band_is_counted_and_one_on_the_inside_is_not(member):
    draws, chain, *_ = WORKED[member]
    # the short leg's target is 1.5 standardized units out: a log-moneyness of 0.05 x 1.5 = 0.075
    _, _, inside = _worked(member, draws, chain(), max_abs_log_moneyness=0.0751)
    assert inside["books"]["model"]["entered"] is True
    metrics, _, outside = _worked(member, draws, chain(), max_abs_log_moneyness=0.0749)
    for book in ("model", "always"):
        assert outside["books"][book]["reason"] == "target_outside_band"
        assert metrics[f"{book}_n_skipped_target_outside_band"] == 1
    assert outside["books"]["implied"]["entered"] is True        # the implied targets are nearer


def test_the_put_spread_refuses_a_degenerate_geometry():
    # only strikes 0 and 50 list puts: the long put snaps down to 50 and the short to 0, which is
    # not a strike. The geometry is refused, never entered
    chain = [q for q in _chain(ENTRY, EXPIRY) if q["right"] == "call"]
    chain += [dict(q, strike=k) for k in (0.0, 50.0) for q in _chain(ENTRY, EXPIRY) if
              q["right"] == "put" and q["strike"] == 95.0]
    metrics, _, entry = _worked("put_spread", PUT_DRAWS, chain)
    assert entry["books"]["model"]["reason"] == "degenerate_strikes"
    assert metrics["model_n_skipped_degenerate_strikes"] == 1


# -- the debit gate ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("member", SPREADS)
def test_a_spread_whose_short_leg_outbids_the_long_ask_has_no_positive_debit(member):
    # one share, dyadic quotes so the boundary is exact. The worked legs, long ask 0.5 and short bid
    # 1.0 with two 0.25 fees: -0.5 + 0.5 = 0, not positive
    draws, chain, strikes, *_ = WORKED[member]
    rows = chain()
    long_strike, short_strike = strikes[0], strikes[1]
    if member == "put_spread":
        long_strike, short_strike = strikes[1], strikes[0]
    right = "call" if member == "call_spread" else "put"
    _unquotable(rows, right, long_strike, bid=0.25, ask=0.5)
    _unquotable(rows, right, short_strike, bid=1.0, ask=1.25)
    over = {"multiplier": 1, "fee_per_leg": 0.25}
    metrics, _, entry = _worked(member, draws, rows, **over)
    for book in ("model", "always"):
        assert entry["books"][book]["reason"] == "nonpositive_debit", book
        assert entry["books"][book]["credit_usd"] == 0.0               # the cell prices it
        assert metrics[f"{book}_n_skipped_nonpositive_debit"] == 1
    # an eighth of a point more debit: -0.375 + 0.5 = 0.125 > 0, so it enters
    _unquotable(rows, right, short_strike, bid=0.875)
    _, _, entry = _worked(member, draws, rows, min_edge_usd=-1000.0, **over)
    assert entry["books"]["always"]["entered"] is True
    assert entry["books"]["always"]["credit_usd"] == -0.125


@pytest.mark.parametrize("member", SPREADS)
def test_a_spread_whose_debit_reaches_its_width_can_only_lose_and_is_refused(member):
    draws, chain, strikes, *_ = WORKED[member]
    width = strikes[1] - strikes[0]                     # 2 points for the calls, 3 for the puts
    rows = chain()
    right = "call" if member == "call_spread" else "put"
    long_strike = strikes[0] if member == "call_spread" else strikes[1]
    short_strike = strikes[1] if member == "call_spread" else strikes[0]
    _unquotable(rows, right, long_strike, bid=2.0, ask=width + 0.5)
    _unquotable(rows, right, short_strike, bid=0.5, ask=0.75)           # debit width + 0.5 - 0.5
    metrics, _, entry = _worked(member, draws, rows)
    for book in ("model", "always"):
        assert entry["books"][book]["reason"] == "debit_not_below_width"
        assert metrics[f"{book}_n_skipped_debit_not_below_width"] == 1
    _unquotable(rows, right, short_strike, bid=0.625)                   # one eighth under the width
    _, _, entry = _worked(member, draws, rows, min_edge_usd=-1.0e6)
    assert entry["books"]["always"]["entered"] is True


def test_the_straddle_has_no_width_and_so_no_width_refusal():
    # a debit of any size is a straddle's; only a non-positive one could be refused, and two
    # positive asks make that impossible
    rows = _straddle_chain()
    _unquotable(rows, "put", 100.0, bid=40.0, ask=50.0)
    _unquotable(rows, "call", 100.0, bid=40.0, ask=50.0)
    _, _, entry = _worked("straddle", WIDE_DRAWS, rows)
    assert entry["books"]["always"]["entered"] is True
    assert entry["books"]["always"]["credit_usd"] == pytest.approx(-(100.0 * 100 + 1.3))


@pytest.mark.parametrize("member", WORKED)
def test_the_debit_is_the_one_owners_max_loss_not_a_restated_rule(member, monkeypatch):
    # a debit structure's debit is structure_max_loss's (the payoff's own worst point); a stub that
    # says "it cannot lose" makes every book refuse, so the gate reads the owner and nothing else
    draws, chain, *_ = WORKED[member]
    calls = []

    def stub(legs, strikes, credit_usd, multiplier):
        calls.append((tuple(legs), tuple(strikes), credit_usd, multiplier))
        return 0.0

    monkeypatch.setattr(nodes, "structure_max_loss", stub)
    _, _, entry = _worked(member, draws, chain())
    assert calls and all(c[0] == MEMBERS[member][5] for c in calls)
    for book in ("model", "always", "implied"):
        assert entry["books"][book]["reason"] == "nonpositive_debit", book


@pytest.mark.parametrize("member", WORKED)
def test_the_books_reasons_are_the_debit_vocabulary(member):
    cls = MEMBERS[member][0]
    assert set(cls.BOOK_REASONS) == {"target_outside_band", "no_quotable_strike",
                                     "degenerate_strikes", "no_atm_iv", "nonpositive_debit",
                                     "debit_not_below_width", "below_min_edge"}
    assert not {"nonpositive_credit", "credit_not_below_width"} & set(cls.BOOK_REASONS)


# -- the long straddle's strike: nearest the forward where both legs can be BOUGHT -----------------------


def _straddle_strike(rows, close=100.0, **over):
    forecasts = [_row(ENTRY, close=close, samples=list(WIDE_DRAWS))]
    _, report = _run_member("straddle", forecasts, rows, FLAT, **over)
    return report["ledger"][0]["books"]["always"]


def test_the_straddle_takes_the_strike_nearest_the_forward_and_the_lower_one_on_a_tie():
    assert _straddle_strike(_chain(ENTRY, EXPIRY))["strikes"] == [100.0, 100.0]
    # forward 100.5 is 0.5 from both 100 and 101: the LOWER strike
    assert _straddle_strike(_chain(ENTRY, EXPIRY, close=100.5), close=100.5)["strikes"] == [
        100.0, 100.0]
    # forward 100.4 is nearer 100 than 101
    assert _straddle_strike(_chain(ENTRY, EXPIRY, close=100.4), close=100.4)["strikes"] == [
        100.0, 100.0]
    assert _straddle_strike(_chain(ENTRY, EXPIRY, close=100.6), close=100.6)["strikes"] == [
        101.0, 101.0]


def test_the_straddle_needs_both_the_put_and_the_call_buyable_at_its_strike():
    # the 100 put has no ask size: 99 and 101 are then equally far, and the lower one wins
    rows = _unquotable(_chain(ENTRY, EXPIRY), "put", 100.0, ask_size=0)
    assert _straddle_strike(rows)["strikes"] == [99.0, 99.0]
    # the 99 call has no ask either: 101 is the nearest strike with both
    rows = _unquotable(rows, "call", 99.0, ask=0.0)
    assert _straddle_strike(rows)["strikes"] == [101.0, 101.0]
    # a strike listed on one right only is no candidate
    rows = [q for q in _chain(ENTRY, EXPIRY) if not (q["right"] == "call" and q["strike"] == 100.0)]
    assert _straddle_strike(rows)["strikes"] == [99.0, 99.0]
    # a bid is irrelevant to buying: 100's put and call quote no bid at all and are still bought
    rows = _unquotable(_unquotable(_chain(ENTRY, EXPIRY), "put", 100.0, bid=0.0), "call", 100.0,
                       bid=0.0)
    assert _straddle_strike(rows)["strikes"] == [100.0, 100.0]


def test_the_straddle_strike_must_lie_within_the_band_or_there_is_no_quotable_strike():
    # only strikes within ln(K / 100) <= the band count. With 100 listed and buyable it alone is at
    # distance 0, inside 0.5 %; take its put out of the market and nothing inside 0.5 % is left
    chain = _chain(ENTRY, EXPIRY, strikes=[90, 100, 110])
    assert _straddle_strike(chain, max_abs_log_moneyness=0.005)["strikes"] == [100.0, 100.0]
    rows = _unquotable(chain, "put", 100.0, ask_size=0)
    cell = _straddle_strike(rows, max_abs_log_moneyness=0.005)
    assert cell["reason"] == "no_quotable_strike" and cell["strikes"] is None
    # the band is on log-moneyness: 110 is 0.0953 out (inside 0.1) and 90 is 0.1054 out (outside it)
    assert _straddle_strike(rows, max_abs_log_moneyness=0.1)["strikes"] == [110.0, 110.0]
    assert _straddle_strike(rows, max_abs_log_moneyness=0.09)["reason"] == "no_quotable_strike"
    # both inside 0.11 and 10 points from the forward: the lower strike
    assert _straddle_strike(rows, max_abs_log_moneyness=0.11)["strikes"] == [90.0, 90.0]


def test_a_straddle_chain_with_no_strike_quotable_on_both_rights_is_counted():
    rows = [q for q in _chain(ENTRY, EXPIRY, strikes=range(95, 106))]
    for q in rows:
        if q["right"] == "call":
            q["ask_size"] = 0
    metrics, _ = _run_member("straddle", [_row(ENTRY, samples=list(WIDE_DRAWS))], rows, FLAT)
    assert metrics["always_n_skipped_no_quotable_strike"] == 1 and metrics["always_n_trades"] == 0
    # the implied book sees the same chain and skips for the same reason
    assert metrics["implied_n_skipped_no_quotable_strike"] == 1


# -- the forecast's quantile picks a spread's long leg -----------------------------------------------------


def test_long_q_moves_the_long_leg_along_the_forecasts_quantiles():
    # DRAWS: Q(0.1) = -1, Q(0.5) = 0, Q(0.9) = +1. Call spread, wing 0.5, snapped UP:
    #   long_q 0.1: 100 e^-0.05 = 95.12 -> 96, short z = -0.5 -> 97.53 -> 98
    #   long_q 0.5: 100 -> 100, short 102.53 -> 103
    #   long_q 0.9: 105.13 -> 106, short z = 1.5 -> 107.79 -> 108
    for long_q, want in ((0.1, [96.0, 98.0]), (0.5, [100.0, 103.0]), (0.9, [106.0, 108.0])):
        _, _, entry = _worked("call_spread", DRAWS, _chain(ENTRY, EXPIRY), long_q=long_q)
        assert entry["books"]["always"]["strikes"] == want, long_q
    # Put spread, snapped DOWN:
    #   long_q 0.1: 95.12 -> 95, short z = -1.5 -> 92.77 -> 92
    #   long_q 0.5: 100 -> 100, short z = -0.5 -> 97.53 -> 97
    #   long_q 0.9: 105.13 -> 105, short z = 0.5 -> 102.53 -> 102
    for long_q, want in ((0.1, [92.0, 95.0]), (0.5, [97.0, 100.0]), (0.9, [102.0, 105.0])):
        _, _, entry = _worked("put_spread", DRAWS, _chain(ENTRY, EXPIRY), long_q=long_q)
        assert entry["books"]["always"]["strikes"] == want, long_q


def test_wing_z_widens_the_spread_by_standardized_units():
    # call spread long_q 0.5 -> long 100; the short is wing_z further: 0.25 -> 101.26 -> 102,
    # 1.0 -> 105.13 -> 106, 2.0 -> 110.52 -> 111
    for wing, want in ((0.25, [100.0, 102.0]), (1.0, [100.0, 106.0]), (2.0, [100.0, 111.0])):
        _, _, entry = _worked("call_spread", DRAWS, _chain(ENTRY, EXPIRY), wing_z=wing)
        assert entry["books"]["always"]["strikes"] == want, wing
    # put spread long_q 0.35 -> Q = 0 -> long 100; the short is wing_z further DOWN:
    # 98.76 -> 98, 95.12 -> 95, 90.48 -> 90
    for wing, want in ((0.25, [98.0, 100.0]), (1.0, [95.0, 100.0]), (2.0, [90.0, 100.0])):
        _, _, entry = _worked("put_spread", DRAWS, _chain(ENTRY, EXPIRY), wing_z=wing)
        assert entry["books"]["always"]["strikes"] == want, wing


def test_the_horizon_rescale_moves_the_targets_by_sqrt_sessions_over_label_horizon():
    # label_horizon 10 halves the variance of a 5-session entry: s' = 0.05 sqrt(5 / 10) = 0.03536.
    # call spread long_q 0.9: 100 e^0.03536 = 103.6 -> 104; short z = 1.5: 105.45 -> 106
    _, _, entry = _worked("call_spread", DRAWS, _chain(ENTRY, EXPIRY), long_q=0.9,
                          label_horizon=10)
    assert entry["horizon_scale"] == pytest.approx(0.05 * math.sqrt(0.5))
    assert entry["books"]["always"]["strikes"] == [104.0, 106.0]


# -- the delta benchmark: the structure's own sign ---------------------------------------------------------


def test_the_straddles_delta_is_near_zero_at_the_money_and_is_the_two_legs_sum():
    _, _, entry = _worked("straddle", WIDE_DRAWS, _straddle_chain())
    cell = entry["books"]["model"]
    delta = _forward_delta("put", 100.0) + _forward_delta("call", 100.0)
    assert cell["delta"] == pytest.approx(delta)
    # 2 N(sd / 2) - 1 with sd = 0.2 sqrt(7 / 365): a hair above zero, the stock a long straddle is not
    assert delta == pytest.approx(2 * NormalDist().cdf(S_IMPLIED / 2) - 1)
    assert 0 < delta < 0.02
    assert cell["equity_pnl_usd"] == pytest.approx(delta * 100 * (97.0 - 100.0))


def test_the_call_spread_is_long_the_stock_and_the_put_spread_short_it():
    _, _, call = _worked("call_spread", CALL_DRAWS, _chain(ENTRY, EXPIRY))
    want = _forward_delta("call", 106.0) - _forward_delta("call", 108.0)
    assert call["books"]["model"]["delta"] == pytest.approx(want) and want > 0
    assert call["books"]["model"]["equity_pnl_usd"] == pytest.approx(want * 100 * -3.0)
    _, _, put = _worked("put_spread", PUT_DRAWS, _put_spread_chain())
    want = -_forward_delta("put", 92.0) + _forward_delta("put", 95.0)
    assert put["books"]["model"]["delta"] == pytest.approx(want) and want < 0
    assert put["books"]["model"]["equity_pnl_usd"] == pytest.approx(want * 100 * -3.0)


def test_the_benchmark_reads_the_dividends_and_a_leg_without_an_iv_leaves_none_and_is_counted():
    closes = [(d, 100.0) for d in SESSIONS[:-3]] + [("2024-03-08", 97.0), ("2024-03-11", 98.0),
                                                     ("2024-03-12", 99.0)]
    series = _series(closes, [("2024-03-07", 1.0)])
    _, _, entry = _worked("call_spread", CALL_DRAWS, _chain(ENTRY, EXPIRY), series)
    delta = _forward_delta("call", 106.0) - _forward_delta("call", 108.0)
    assert entry["books"]["model"]["equity_pnl_usd"] == pytest.approx(delta * 100 * (97 - 100 + 1.0))
    rows = _unquotable(_chain(ENTRY, EXPIRY), "call", 108.0, iv=None)
    metrics, _, entry = _worked("call_spread", CALL_DRAWS, rows)
    assert entry["books"]["model"]["delta"] is None and metrics["model_n_no_delta"] == 1


# -- the American charge: short legs only ---------------------------------------------------------------------


def _with_path(closes, dividends=()):
    base = [(d, 100.0) for d in SESSIONS[:-3]] + [("2024-03-08", 97.0), ("2024-03-11", 98.0),
                                                   ("2024-03-12", 99.0)]
    merged = dict(base)
    merged.update(closes)
    return _series(sorted(merged.items()), dividends)


def test_the_call_spread_is_charged_the_dividend_on_its_short_call_only():
    # a 1.0 ex-date on 03-07 whose pre-ex close (03-06) is 110 is above the SHORT call 108: 100 USD.
    # The long call at 106 is never charged; the put side does not exist
    series = _with_path({"2024-03-06": 110.0}, [("2024-03-07", 1.0)])
    _, _, entry = _worked("call_spread", CALL_DRAWS, _chain(ENTRY, EXPIRY), series)
    cell = entry["books"]["model"]
    want = american_call_dividend(series, 108.0, ENTRY, EXPIRY, 100)
    assert want == 100.0 and cell["american_charge_usd"] == pytest.approx(100.0)
    assert cell["pnl_usd"] == pytest.approx(-CALL_SPREAD_DEBIT - 100.0)
    assert cell["side_pnl_usd"] == {"call": pytest.approx(cell["pnl_usd"])}


def test_the_put_spread_is_charged_the_carry_on_its_short_put_only():
    # the close on 03-06 is 90, below the SHORT put at 92: carry from that session to settlement. The
    # long put at 95 is never charged (it was below 95 a session earlier in no path here)
    series = _with_path({"2024-03-06": 90.0})
    _, _, entry = _worked("put_spread", PUT_DRAWS, _put_spread_chain(), series)
    cell = entry["books"]["model"]
    want = american_put_carry(series, 92.0, ENTRY, EXPIRY, 0.055, 100)
    assert want > 0 and cell["american_charge_usd"] == pytest.approx(want)
    assert cell["pnl_usd"] == pytest.approx(-PUT_SPREAD_DEBIT - want)


def test_the_straddle_holds_no_short_leg_and_is_charged_nothing_whatever_the_path():
    series = _with_path({"2024-03-06": 90.0, "2024-03-05": 120.0}, [("2024-03-07", 1.0)])
    _, _, entry = _worked("straddle", WIDE_DRAWS, _straddle_chain(), series)
    for book in ("model", "always", "implied"):
        assert entry["books"][book]["american_charge_usd"] == 0.0


@pytest.mark.parametrize("member", WORKED)
def test_every_member_refuses_a_session_without_a_dividend_amount_in_its_window(member):
    # the charge alone needs dividend data only for a short call, but the delta benchmark reads
    # dividend_amount for every structure, so a None inside (entry, settlement] refuses them all
    draws, chain, *_ = WORKED[member]
    bad = _series([(d, 100.0) for d in SESSIONS])
    bad[3]["dividend_amount"] = None
    with pytest.raises(ValueError, match="dividend_amount"):
        _worked(member, draws, chain(), bad)


# -- the walk: aggregate metrics over three entries --------------------------------------------------------
#
# Three entries a fortnight apart (Mar 1, 11, 19) on the default chain, settling at 80 / 93.5 / 97,
# the plain DRAWS at s' = 0.05.
#   straddle: (0.6 + 0.6) x 100 + 1.3 = 121.3 a trade; payoffs 20 / 6.5 / 3 at 100: P&L 1878.7 /
#     528.7 / 178.7; E_P = -121.3 + 100 x 0.2 x ((100 - 100 e^-0.05) + (100 e^0.05 - 100)) = 78.8 > 0
#   call spread (long_q 0.5): Q = 0, long 100, short z = 0.5 -> 102.53 -> 103: debit 0.1 x 100 + 1.3 =
#     11.3, settled below 100 every time: -11.3 x 3; E_P = -11.3 + 100 x 2 x 3 / 10 = 48.7
#   put spread (long_q 0.35): Q = 0, long put 100, short z = -0.5 -> 97.53 -> 97: debit 11.3; the
#     long 100 put is worth 3 at 97 and the spread its width 3 at 80 and 93.5: +288.7 x 3


def test_the_straddles_three_trades_by_hand():
    rows, chain, series = _three_trades(80.0, 93.5, 97.0)
    metrics, report = _run_member("straddle", rows, chain, series)
    pnls = [1878.7, 528.7, 178.7]
    assert [e["books"]["model"]["pnl_usd"] for e in report["ledger"]] == pytest.approx(pnls)
    assert metrics["model_n_trades"] == 3
    assert metrics["model_total_pnl_usd"] == pytest.approx(sum(pnls))
    assert metrics["model_mean_pnl_usd"] == pytest.approx(sum(pnls) / 3)
    assert metrics["model_hit_rate"] == 1.0 and metrics["model_max_drawdown_usd"] == 0.0
    assert metrics["model_cvar_usd"] == pytest.approx(178.7)             # the single worst of three
    assert metrics["model_mean_credit_usd"] == pytest.approx(-121.3)
    assert metrics["model_pnl_t"] == pytest.approx(_t(pnls))
    # the wings: the put side earns the payoff net of its 60.65 debit, the call side only loses it
    assert metrics["model_put_total_pnl_usd"] == pytest.approx(
        (2000 + 650 + 300) - 3 * 60.65)
    assert metrics["model_call_total_pnl_usd"] == pytest.approx(-3 * 60.65)
    # the implied book trades the same strike on the same quotes
    assert metrics["implied_total_pnl_usd"] == pytest.approx(sum(pnls))
    assert metrics["model_american_charge_usd"] == 0.0


def test_the_call_spreads_three_trades_by_hand_and_a_flat_loss_has_a_zero_t():
    rows, chain, series = _three_trades(80.0, 93.5, 97.0)
    metrics, report = _run_member("call_spread", rows, chain, series)
    assert [e["books"]["model"]["strikes"] for e in report["ledger"]] == [[100.0, 103.0]] * 3
    assert metrics["model_n_trades"] == 3
    assert metrics["model_total_pnl_usd"] == pytest.approx(-3 * 11.3)
    assert metrics["model_hit_rate"] == 0.0
    assert metrics["model_cvar_usd"] == pytest.approx(-11.3)
    assert metrics["model_max_drawdown_usd"] == pytest.approx(33.9)
    assert metrics["model_pnl_t"] == 0.0                        # three equal trades: no variance
    assert metrics["model_mean_credit_usd"] == pytest.approx(-11.3)
    assert metrics["model_call_total_pnl_usd"] == pytest.approx(-3 * 11.3)
    assert "model_put_total_pnl_usd" not in metrics


def test_the_put_spreads_three_trades_by_hand():
    rows, chain, series = _three_trades(80.0, 93.5, 97.0)
    metrics, report = _run_member("put_spread", rows, chain, series)
    assert [e["books"]["model"]["strikes"] for e in report["ledger"]] == [[97.0, 100.0]] * 3
    assert metrics["model_total_pnl_usd"] == pytest.approx(3 * 288.7)
    assert metrics["model_hit_rate"] == 1.0 and metrics["model_max_drawdown_usd"] == 0.0
    assert metrics["model_put_total_pnl_usd"] == pytest.approx(3 * 288.7)
    assert "model_call_total_pnl_usd" not in metrics


def test_positions_do_not_overlap_and_a_day_without_a_chain_is_skipped():
    # the second row (03-04) lies inside the first position; the third has no chain
    rows = [_row(ENTRY, samples=list(WIDE_DRAWS)), _row("2024-03-04", samples=list(WIDE_DRAWS)),
            _row("2024-03-11", samples=list(WIDE_DRAWS))]
    metrics, report = _run_member("straddle", rows, _straddle_chain(), FLAT)
    assert [e["date"] for e in report["ledger"]] == [ENTRY]
    assert metrics["n_skipped_no_chain"] == 1 and metrics["n_entries"] == 1


# -- annotation: gates and sizing flow through unchanged -------------------------------------------------------


def test_gate_fields_split_the_straddles_traded_pnl():
    # default chain, levels 80 / 97 / 93.5 / 97 / 110 / 97: the straddle's P&L is the payoff less
    # 121.3: 1878.7 / 178.7 / 528.7 / 178.7 / 878.7 / 178.7. GATE_A closed on entries 0, 1, 5:
    # (1878.7 + 178.7 + 178.7) / 3; open on 2, 3: (528.7 + 178.7) / 2; entry 4 unknown
    days, chain, series = _trades(GATE_LEVELS)
    metrics, report = _run_member("straddle", _gated_rows(days, g_a=GATE_A), chain, series,
                                  gate_fields=["g_a"])
    pnls = [1878.7, 178.7, 528.7, 178.7, 878.7, 178.7]
    assert [e["books"]["model"]["pnl_usd"] for e in report["ledger"]] == pytest.approx(pnls)
    assert [e["gates"] for e in report["ledger"]] == [{"g_a": g} for g in GATE_A]
    assert metrics["model_g_a_closed_n"] == 3 and metrics["model_g_a_open_n"] == 2
    assert metrics["model_g_a_unknown_n"] == 1
    assert metrics["model_g_a_closed_mean_pnl_usd"] == pytest.approx((1878.7 + 178.7 + 178.7) / 3)
    assert metrics["model_g_a_open_mean_pnl_usd"] == pytest.approx((528.7 + 178.7) / 2)
    assert metrics["model_g_a_open_t"] == pytest.approx(_t([528.7, 178.7]))


def test_size_fields_weigh_the_straddles_traded_pnl():
    days, chain, series = _trades(GATE_LEVELS)
    metrics, report = _run_member("straddle", _gated_rows(days, w=SIZE_W), chain, series,
                                  size_fields=["w"])
    pnls = [1878.7, 178.7, 528.7, 178.7, 878.7, 178.7]
    assert [e["sizes"] for e in report["ledger"]] == [{"w": w} for w in SIZE_W]
    _assert_the_sized_book(metrics, "model", "w", _sized(pnls, SIZE_W))
    # weights 1, 2, -, 0.5, 4, 1 on all but the third: [1878.7, 357.4, 89.35, 3514.8, 178.7]
    assert metrics["model_w_total_pnl_usd"] == pytest.approx(
        1878.7 + 2 * 178.7 + 0.5 * 178.7 + 4 * 878.7 + 178.7)
    assert metrics["model_w_n"] == 5 and metrics["model_w_unknown_n"] == 1


@pytest.mark.parametrize("member", WORKED)
def test_annotating_never_changes_the_trades(member):
    draws, chain, *_ = WORKED[member]
    rows = [_row(ENTRY, samples=list(draws), g=True, w=2.0)]
    plain_m, plain = _run_member(member, rows, chain(), FLAT)
    noted_m, noted = _run_member(member, rows, chain(), FLAT, gate_fields=["g"], size_fields=["w"])
    assert [{k: v for k, v in e.items() if k not in ("gates", "sizes")} for e in noted["ledger"]] \
        == plain["ledger"]
    assert {k: v for k, v in noted_m.items() if "_g_" not in k and "_w_" not in k} == plain_m


# -- the class family and its knobs ----------------------------------------------------------------------------


def test_the_debit_backtest_is_abstract_over_one_hook_and_a_member_that_supplies_it_builds():
    assert DebitStructureQuoteBacktest.__abstractmethods__ == frozenset({"_target_strikes"})
    with pytest.raises(TypeError, match="_target_strikes"):
        DebitStructureQuoteBacktest("bt", SPREAD_BASE)

    class Incomplete(DebitStructureQuoteBacktest):
        LEGS = contracts.LONG_STRADDLE_LEGS

    with pytest.raises(TypeError, match="_target_strikes"):
        Incomplete("bt", STRADDLE_BASE)

    class Complete(Incomplete):
        def _target_strikes(self, listed, forward, scale, quantile):
            return None, "no_quotable_strike"

    assert Complete("bt", STRADDLE_BASE).key == "bt"
    for member, (cls, *_rest) in MEMBERS.items():
        assert issubclass(cls, DebitStructureQuoteBacktest) and not cls.__abstractmethods__
        assert issubclass(DebitStructureQuoteBacktest, CondorQuoteBacktest), member


@pytest.mark.parametrize("member", MEMBERS)
def test_a_members_class_facts(member):
    cls, sides, _base, kind, units, legs = MEMBERS[member]
    assert cls.LEGS == legs and cls.SIDES == sides
    assert cls.REPORT_KIND == kind and cls.UNITS == units
    assert cls.REPORT_PRICING == "archived_eod_quotes" and cls.FORECAST_BOOKS == ("model", "always")
    assert cls.serving_effect({}, {}) == "forbidden"
    assert cls.LEGS == {"straddle": contracts.LONG_STRADDLE_LEGS,
                        "call_spread": contracts.LONG_CALL_SPREAD_LEGS,
                        "put_spread": contracts.LONG_PUT_SPREAD_LEGS}[member]
    assert {"LongStraddleQuoteBacktest", "LongCallSpreadQuoteBacktest",
            "LongPutSpreadQuoteBacktest", "DebitStructureQuoteBacktest"} <= set(nodes.__all__)


def test_the_params_each_member_declares_are_exactly_these_and_the_rest_are_refused():
    assert set(LongStraddleQuoteBacktest._PARAMS) == COMMON_KNOBS
    assert set(LongCallSpreadQuoteBacktest._PARAMS) == COMMON_KNOBS | {"long_q", "wing_z"}
    assert set(LongPutSpreadQuoteBacktest._PARAMS) == COMMON_KNOBS | {"long_q", "wing_z"}
    for cls in (LongStraddleQuoteBacktest, LongCallSpreadQuoteBacktest, LongPutSpreadQuoteBacktest):
        assert list(cls._PARAMS) == sorted(cls._PARAMS)
    # the condor's short_q is nobody's here, and the straddle has neither a wing nor a quantile
    for member, (cls, _s, base, *_r) in MEMBERS.items():
        assert cls.validate_params(base) == [], member
        assert any("short_q" in p and "unknown" in p for p in
                   cls.validate_params({**base, "short_q": 0.1})), member
    for knob, value in (("wing_z", 0.5), ("long_q", 0.5)):
        assert any(knob in p and "unknown" in p for p in
                   LongStraddleQuoteBacktest.validate_params({**STRADDLE_BASE, knob: value}))


def test_the_long_q_defaults_are_the_members_own_and_written_once():
    # 0.5 for a call spread (ATM), 0.35 for a put spread, restated here; the class's DEFAULTS and
    # the running node's knob and the report all read the one name
    assert LongCallSpreadQuoteBacktest.DEFAULT_LONG_Q == 0.5
    assert LongPutSpreadQuoteBacktest.DEFAULT_LONG_Q == 0.35
    for cls in (LongCallSpreadQuoteBacktest, LongPutSpreadQuoteBacktest):
        assert cls.DEFAULTS["long_q"] is cls.DEFAULT_LONG_Q
        assert cls.DEFAULTS["min_edge_usd"] == 0.0 and cls.DEFAULTS["cvar_alpha"] == 0.95
        node = cls("bt", SPREAD_BASE)
        assert node._knob("long_q") == cls.DEFAULT_LONG_Q
        assert "long_q" not in node.params            # the document did not write it out
    assert "long_q" not in LongStraddleQuoteBacktest.DEFAULTS
    _, report, _ = _worked("call_spread", CALL_DRAWS, _chain(ENTRY, EXPIRY))
    assert report["params"]["long_q"] == 0.5 and "short_q" not in report["params"]
    _, report, _ = _worked("put_spread", PUT_DRAWS, _put_spread_chain())
    assert report["params"]["long_q"] == 0.35
    _, report, _ = _worked("call_spread", CALL_DRAWS, _chain(ENTRY, EXPIRY), long_q=0.9)
    assert report["params"]["long_q"] == 0.9
    _, report, _ = _worked("straddle", WIDE_DRAWS, _straddle_chain())
    assert "long_q" not in report["params"] and "wing_z" not in report["params"]


@pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.5, "0.5", True, float("nan"), float("inf"), None])
@pytest.mark.parametrize("cls", [LongCallSpreadQuoteBacktest, LongPutSpreadQuoteBacktest])
def test_long_q_must_be_a_probability_strictly_inside_zero_and_one(cls, bad):
    problems = cls.validate_params({**SPREAD_BASE, "long_q": bad})
    assert any(p.startswith("long_q must be in (0, 1)") for p in problems), problems


@pytest.mark.parametrize("ok", [0.001, 0.35, 0.5, 0.7, 0.999])
@pytest.mark.parametrize("cls", [LongCallSpreadQuoteBacktest, LongPutSpreadQuoteBacktest])
def test_a_long_q_inside_the_unit_interval_is_accepted(cls, ok):
    assert cls.validate_params({**SPREAD_BASE, "long_q": ok}) == []


_KNOB_REFUSALS = [
    ({"split": "nope"}, "split"), ({"multiplier": 0}, "multiplier"),
    ({"multiplier": 1.5}, "multiplier"), ({"fee_per_leg": -0.01}, "fee_per_leg"),
    ({"carry_rate": -0.01}, "carry_rate"), ({"dte_min": 11}, "dte_min"),
    ({"max_abs_log_moneyness": 0}, "max_abs_log_moneyness"),
    ({"max_abs_log_moneyness": -0.2}, "max_abs_log_moneyness"),
    ({"label_horizon": 0}, "label_horizon"), ({"cvar_alpha": 1.0}, "cvar_alpha"),
    ({"min_edge_usd": float("nan")}, "min_edge_usd"), ({"gate_fields": []}, "gate_fields"),
    ({"size_fields": ["w", "w"]}, "size_fields"), ({"wing_points": 25}, "wing_points"),
]


@pytest.mark.parametrize("member", MEMBERS)
@pytest.mark.parametrize("change, name", _KNOB_REFUSALS)
def test_a_bad_knob_is_refused_and_named(member, change, name):
    cls, _s, base, *_r = MEMBERS[member]
    assert any(name in p for p in cls.validate_params({**base, **change})), (member, change)


@pytest.mark.parametrize("member", SPREADS)
@pytest.mark.parametrize("change", [{"wing_z": 0}, {"wing_z": -0.5}, {"wing_z": "0.5"},
                                    {"wing_z": float("inf")}])
def test_a_spreads_wing_must_be_a_positive_number(member, change):
    cls, _s, base, *_r = MEMBERS[member]
    assert any("wing_z" in p for p in cls.validate_params({**base, **change}))


@pytest.mark.parametrize("member", MEMBERS)
def test_every_required_knob_is_required(member):
    cls, _s, base, *_r = MEMBERS[member]
    required = set(base) - {"min_edge_usd", "cvar_alpha", "long_q", "gate_fields", "size_fields"}
    for knob in required:
        problems = cls.validate_params({k: v for k, v in base.items() if k != knob})
        assert any("missing params" in p and knob in p for p in problems), (member, knob)
    assert set(cls._REQUIRED) == required


def test_a_straddle_document_leaves_the_bucket_knobs_the_condors_own_and_needs_no_wing():
    assert set(LongStraddleQuoteBacktest._REQUIRED) == set(STRADDLE_BASE)
    assert "wing_z" not in LongStraddleQuoteBacktest._REQUIRED
    assert set(LongCallSpreadQuoteBacktest._REQUIRED) == set(SPREAD_BASE)
    assert set(CondorQuoteBacktest._REQUIRED) - set(SPREAD_BASE) == {"short_q"}


@pytest.mark.parametrize("member", MEMBERS)
def test_the_inputs_contract_is_the_condors(member):
    cls, _s, base, *_r = MEMBERS[member]
    node = cls("bt", base)
    assert node.validate_inputs({"forecasts": [], "chain": [], "underlying": []}) == []
    assert any("forecasts" in p for p in node.validate_inputs({"chain": [], "underlying": []}))
    assert any("chain" in p for p in node.validate_inputs({"forecasts": [], "underlying": []}))
    with pytest.raises(ValueError, match="no forecast row lies in split"):
        node.run(CTX, {"forecasts": [], "chain": _chain(ENTRY, EXPIRY), "underlying": FLAT})
