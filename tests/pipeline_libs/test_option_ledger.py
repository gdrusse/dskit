"""OptionStructureLedger (ADR-0265 node 4): NBBO fills, mid marks, declared closes, optional hedge.

The worlds are synthetic and small enough to price by hand; every P&L figure below is written
out from the quotes in the fixture, never read back from the node. The hedge tests price the
chain with Black-76 itself on a known path.
"""

import math
import random
from datetime import date, timedelta

import pytest

from dskit.pipeline.libs.option_ledger import NODE_KINDS, OptionStructureLedger
from dskit.pipeline.node import DEFAULT_NODE_KINDS, ConfigError, NodeContext
from dskit.pipeline.option_pricing import black76, black76_delta

START = date(2020, 3, 2)

CHAIN_FIELDS = {"symbol": "symbol", "date": "date", "expiration": "expiration", "strike": "strike",
                "right": "right", "bid": "bid", "ask": "ask", "underlying": "underlying_price"}
ORDER_FIELDS = {"date": "open_date", "symbol": "symbol", "expiration": "expiration",
                "strikes": "strikes", "structure": "structure"}
STRUCTURES = {"put_spread": [["put", 1], ["put", -1]], "straddle": [["put", 1], ["call", 1]],
              "naked_call": [["call", -1]]}
BASE = {"chain_fields": CHAIN_FIELDS, "order_fields": ORDER_FIELDS, "structures": STRUCTURES,
        "multiplier": 100, "contracts": 2, "fee_per_contract": 0.65}


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="ledger", asof="2025-12-31", run_dir=str(tmp_path))


def day(i):
    return START + timedelta(days=i)


def row(symbol, i, strike, right, bid, ask, under, expiration=4):
    return {"symbol": symbol, "date": day(i).isoformat(), "expiration": day(expiration).isoformat(),
            "strike": strike, "right": right, "bid": bid, "ask": ask, "underlying_price": under}


def order(symbol="ABC", strikes=(95, 100), structure="put_spread", open_day=0, expiration=4, **extra):
    return {"open_date": day(open_day).isoformat(), "symbol": symbol,
            "expiration": day(expiration).isoformat(), "strikes": list(strikes),
            "structure": structure, **extra}


def spread_chain(symbol="ABC"):
    """A put spread world: open day 0, expiry day 4; day 3 has no quote for the traded strikes."""
    rows = []
    quotes = {0: ((0.9, 1.1), (2.0, 2.2), 101.0), 1: ((0.7, 0.9), (1.4, 1.6), 99.0),
              2: ((0.5, 0.7), (1.9, 2.1), 98.0), 4: ((0.0, 0.05), (3.0, 3.1), 97.0)}
    for i, (low, high, under) in quotes.items():
        rows += [row(symbol, i, 95, "put", *low, under), row(symbol, i, 100, "put", *high, under)]
    rows.append(row(symbol, 3, 50, "put", 0.0, 0.05, 97.0))      # day 3: only an unrelated strike
    return rows


def run(ctx, chain, orders, **over):
    return OptionStructureLedger("ledger", {**BASE, **over}).run(
        ctx, {"chain": chain, "orders": orders})


# -- the worked put spread, to expiry ----------------------------------------------------------

def test_a_put_spread_opens_at_the_nbbo_marks_at_the_mid_and_settles_intrinsic(ctx):
    out = run(ctx, spread_chain(), [order()])
    (trade,) = out["trades"]
    # long 95 put bought at its ask 1.1, short 100 put sold at its bid 2.0: 0.9 a share, 2 x 100
    assert trade["premium_usd"] == pytest.approx(180.0)
    assert trade["open_fees_usd"] == pytest.approx(0.65 * 2 * 2)
    assert trade["credit_usd"] == pytest.approx(180.0 - 2.6)
    # settles at 97: the 100 put is 3 in the money, the 95 put expires
    assert trade["close_cash_usd"] == pytest.approx(-600.0)
    assert trade["close_fees_usd"] == 0.0
    assert trade["pnl_usd"] == pytest.approx(180.0 - 600.0 - 2.6)
    assert trade["pnl_over_premium"] == pytest.approx((180.0 - 600.0 - 2.6) / 180.0)
    assert trade["max_loss_usd"] == pytest.approx(2 * (500.0 - 88.7))
    assert (trade["open_date"], trade["close_date"]) == (day(0).isoformat(), day(4).isoformat())
    assert (trade["reason_closed"], trade["status"], trade["exit_refused"]) == ("expiry", "closed", False)
    assert trade["fees_usd"] == pytest.approx(2.6)
    assert trade["strikes"] == [95.0, 100.0] and trade["contracts"] == 2


def test_the_mark_series_is_the_mid_never_the_close_and_carries_a_missing_day(ctx):
    chain = spread_chain()
    for r in chain:
        r["close"] = 999.0                     # a column the ledger must never read
    out = run(ctx, chain, [order()])
    marks = out["marks"]
    assert [m["date"] for m in marks] == [day(i).isoformat() for i in range(5)]
    assert [m["phase"] for m in marks] == ["open", "mark", "mark", "mark", "settle"]
    # value of closing at the mid = 200 * (mid95 - mid100)
    assert [m["mark_value_usd"] for m in marks[:3]] == pytest.approx([-220.0, -140.0, -280.0])
    assert [m["pnl_usd"] for m in marks[:3]] == pytest.approx([-42.6, 37.4, -102.6])
    # day 3 has no quote for the traded strikes: the last mark carries, flagged
    assert marks[3]["carried"] is True and marks[3]["carry_reason"] == "leg_not_quoted"
    assert marks[3]["mark_value_usd"] == pytest.approx(-280.0)
    assert not any(m["carried"] for i, m in enumerate(marks) if i != 3)
    assert marks[4]["mark_value_usd"] == pytest.approx(-600.0)
    assert marks[4]["pnl_usd"] == pytest.approx(out["trades"][0]["pnl_usd"])
    assert out["trades"][0]["mark_days"] == 4 and out["trades"][0]["carried_days"] == 1
    assert out["summary"]["marks_carried"] == {"leg_not_quoted": 1}
    assert marks[0]["underlying"] == 101.0


def test_a_crossed_quote_on_a_mark_day_carries_with_its_reason(ctx):
    chain = spread_chain()
    for r in chain:
        if r["date"] == day(2).isoformat() and r["strike"] == 100:
            r["bid"], r["ask"] = 2.1, 1.9
    out = run(ctx, chain, [order()])
    assert out["marks"][2]["carried"] and out["marks"][2]["carry_reason"] == "quote_crossed"
    assert out["marks"][2]["mark_value_usd"] == pytest.approx(-140.0)


def test_a_mid_fill_is_the_sensitivity_switch_not_the_default(ctx):
    nbbo = run(ctx, spread_chain(), [order()])["trades"][0]["premium_usd"]
    mid = run(ctx, spread_chain(), [order()], fill="mid")["trades"][0]["premium_usd"]
    assert nbbo == pytest.approx(180.0)
    assert mid == pytest.approx(200 * (2.1 - 1.0))


# -- a declared exit ---------------------------------------------------------------------------

def exit_chain():
    chain = [r for r in spread_chain() if r["date"] != day(3).isoformat()]
    chain += [row("ABC", 3, 95, "put", 0.4, 0.6, 96.0), row("ABC", 3, 100, "put", 1.0, 1.2, 96.0)]
    return chain


EXIT = {"close_rule": "exit", "order_fields": {**ORDER_FIELDS, "exit": "exit_date"}}


def test_an_exit_closes_at_the_nbbo_on_the_declared_date(ctx):
    out = run(ctx, exit_chain(), [order(exit_date=day(3).isoformat())], **EXIT)
    (trade,) = out["trades"]
    # sell the long 95 at its bid .4, buy back the short 100 at its ask 1.2: -0.8 a share
    assert trade["close_cash_usd"] == pytest.approx(-160.0)
    assert trade["close_fees_usd"] == pytest.approx(2.6)
    assert trade["pnl_usd"] == pytest.approx(180.0 - 160.0 - 2.6 - 2.6)
    assert (trade["reason_closed"], trade["close_date"]) == ("exit", day(3).isoformat())
    assert [m["phase"] for m in out["marks"]] == ["open", "mark", "mark", "exit"]
    assert out["summary"]["closed"] == {"exit": 1}


def test_an_exit_that_cannot_be_quoted_is_counted_and_the_trade_runs_to_expiry(ctx):
    out = run(ctx, spread_chain(), [order(exit_date=day(3).isoformat())], **EXIT)
    (trade,) = out["trades"]
    assert trade["exit_refused"] is True and trade["reason_closed"] == "expiry"
    assert trade["pnl_usd"] == pytest.approx(180.0 - 600.0 - 2.6)
    assert out["summary"]["exit_refused"] == {"leg_not_quoted": 1}


def test_an_exit_date_the_chain_never_shows_is_counted_not_silent(ctx):
    chain = [r for r in spread_chain() if r["date"] != day(3).isoformat()]
    out = run(ctx, chain, [order(exit_date=day(3).isoformat())], **EXIT)
    assert out["summary"]["exit_refused"] == {"exit_date_not_in_chain": 1}
    assert out["trades"][0]["reason_closed"] == "expiry"


@pytest.mark.parametrize("exit_day,reason", [(0, "exit_not_after_open"), (5, "exit_after_expiry")])
def test_an_exit_date_outside_the_trade_life_is_refused_and_counted(ctx, exit_day, reason):
    good = order(exit_date=day(3).isoformat())
    out = run(ctx, exit_chain(), [order(exit_date=day(exit_day).isoformat()), good], **EXIT)
    assert len(out["trades"]) == 1 and out["summary"]["refused_at_open"] == {reason: 1}


# -- refusals at the open ----------------------------------------------------------------------

def refusal_world():
    """One order per refusal reason, on its own symbol; ``G`` fills."""
    good = ((0.9, 1.1), (2.0, 2.2))
    worlds = {"A": ((0.9, 1.1), (0.0, 0.2)),          # sold leg with no bid
              "B": ((0.9, 1.1), (2.3, 2.2)),          # crossed
              "C": ((0.9, 1.1), (2.0, 2.0)),          # locked
              "F": ((0.9, 1.1), (float("nan"), 2.2)),  # not finite
              "H": ((0.0, 0.0), (2.0, 2.2)),          # bought leg with no ask
              "G": good}
    chain = []
    for symbol, (low, high) in worlds.items():
        chain += [row(symbol, 0, 95, "put", *low, 100.0), row(symbol, 0, 100, "put", *high, 100.0),
                  row(symbol, 4, 95, "put", 0.0, 0.1, 98.0), row(symbol, 4, 100, "put", 2.0, 2.1, 98.0)]
    chain.append(row("D", 0, 100, "put", 2.0, 2.2, 100.0))        # D: the 95 put has no row
    chain.append(row("D", 4, 100, "put", 2.0, 2.1, 98.0))
    chain.append(row("E", 1, 95, "put", 0.9, 1.1, 100.0))        # E: nothing on the open date
    return chain, [order(s) for s in "ABCFHGDE"]


def test_every_refusal_is_counted_under_one_reason(ctx):
    chain, orders = refusal_world()
    out = run(ctx, chain, orders + [order("G", expiration=0), order("G", open_day=4, expiration=2)])
    assert [t["symbol"] for t in out["trades"]] == ["G"]
    summary = out["summary"]
    assert summary["refused_at_open"] == {
        "sold_leg_no_bid": 1, "crossed": 1, "locked": 1, "not_finite": 1, "bought_leg_no_ask": 1,
        "leg_not_quoted": 1, "no_rows_on_open_date": 1, "expiry_not_after_open": 2}
    assert summary["orders_in"] == 10 and summary["orders_refused"] == 9
    assert summary["orders_opened"] == 1


# -- ending without a close --------------------------------------------------------------------

def test_a_trade_the_data_ends_before_is_open_at_the_end_with_no_realized_pnl(ctx):
    chain = [r for r in spread_chain() if r["date"] <= day(2).isoformat()]
    out = run(ctx, chain, [order()])
    (trade,) = out["trades"]
    assert trade["status"] == "open_at_end" and trade["pnl_usd"] is None
    assert trade["reason_closed"] is None and trade["close_date"] is None
    assert trade["marked_pnl_usd"] == pytest.approx(-102.6)
    assert out["summary"]["open_at_end"] == 1 and out["summary"]["totals"]["closed_trades"] == 0


def test_an_expiry_with_no_underlying_is_unsettled_not_guessed(ctx):
    chain = [r for r in spread_chain() if r["date"] != day(4).isoformat()]
    chain.append(row("ZZZ", 5, 10, "put", 0.1, 0.2, 10.0, expiration=9))   # the data runs past expiry
    out = run(ctx, chain, [order(), order("ZZZ", (10, 10), "straddle", open_day=5, expiration=9)])
    abc = next(t for t in out["trades"] if t["symbol"] == "ABC")
    assert abc["status"] == "unsettled" and abc["unsettled_reason"] == "no_rows_on_expiry"
    assert abc["pnl_usd"] is None
    assert out["summary"]["unsettled"] == {"no_rows_on_expiry": 1}


def test_an_expiry_row_with_no_underlying_is_a_different_unsettled_reason(ctx):
    chain = spread_chain()
    for r in chain:
        if r["date"] == day(4).isoformat():
            r["underlying_price"] = None
    out = run(ctx, chain, [order()])
    assert out["trades"][0]["unsettled_reason"] == "no_underlying_on_expiry"
    assert out["summary"]["unsettled"] == {"no_underlying_on_expiry": 1}


# -- other structures --------------------------------------------------------------------------

def straddle_chain(final_under):
    return [row("ABC", 0, 100, "put", 2.0, 2.2, 100.0), row("ABC", 0, 100, "call", 3.0, 3.4, 100.0),
            row("ABC", 4, 100, "put", 0.0, 0.1, final_under), row("ABC", 4, 100, "call", 9.9, 10.1, final_under)]


def test_a_long_straddle_pays_the_ask_and_its_max_loss_is_the_debit(ctx):
    out = run(ctx, straddle_chain(110.0), [order(strikes=(100, 100), structure="straddle")],
              contracts=1)
    (trade,) = out["trades"]
    assert trade["premium_usd"] == pytest.approx(-560.0)          # (2.2 + 3.4) x 100 paid
    assert trade["credit_usd"] == pytest.approx(-561.3)
    assert trade["close_cash_usd"] == pytest.approx(1000.0)
    assert trade["pnl_usd"] == pytest.approx(-560.0 + 1000.0 - 1.3)
    assert trade["max_loss_usd"] == pytest.approx(561.3)
    assert trade["pnl_over_premium"] == pytest.approx((1000.0 - 561.3) / 560.0)


def test_an_unbounded_loss_is_reported_as_null_with_a_flag(ctx):
    chain = [row("ABC", 0, 100, "call", 3.0, 3.4, 100.0), row("ABC", 4, 100, "call", 0.0, 0.1, 99.0)]
    (trade,) = run(ctx, chain, [order(strikes=(100,), structure="naked_call")])["trades"]
    assert trade["max_loss_usd"] is None and trade["max_loss_unbounded"] is True
    assert trade["premium_usd"] == pytest.approx(2 * 100 * 3.0)


def test_a_settlement_fee_is_charged_per_leg_that_finishes_in_the_money(ctx):
    out = run(ctx, spread_chain(), [order()], settle_fee_per_contract=1.0)
    assert out["trades"][0]["close_fees_usd"] == pytest.approx(1.0 * 2 * 1)   # one ITM leg x 2 contracts


# -- the delta hedge ---------------------------------------------------------------------------

SIGMA, RATE, DAYS = 0.30, 0.02, 30


def hedge_world(up_days, seed=2, half_spread=0.005):
    """A +/- sigma sqrt(dt) path whose realized variance equals the implied one exactly, with
    Black-76 quotes at that vol: a long ATM straddle, expiry on day DAYS."""
    signs = [1] * up_days + [-1] * (DAYS - up_days)
    random.Random(seed).shuffle(signs)
    step = SIGMA * math.sqrt(1 / 365)
    path = [100.0]
    for sign in signs:
        path.append(path[-1] * math.exp(sign * step))
    chain = []
    for i in range(DAYS + 1):
        years = (DAYS - i) / 365
        for right in ("put", "call"):
            if years > 0:
                mid = black76(right, path[i] * math.exp(RATE * years), 100.0, SIGMA, years, RATE)
            else:
                mid = max(100.0 - path[i], 0.0) if right == "put" else max(path[i] - 100.0, 0.0)
            half = min(half_spread, mid)            # symmetric, so the NBBO mid is the model price
            chain.append(row("ABC", i, 100, right, mid - half, mid + half, path[i], expiration=DAYS))
    return chain, path


HEDGE_ORDER = order(strikes=(100, 100), structure="straddle", expiration=DAYS)


def test_a_delta_hedge_makes_a_long_straddle_flat_on_a_known_black76_path(ctx):
    chain, path = hedge_world(up_days=21)
    base = dict(contracts=1, fee_per_contract=0.0, year_days=365)
    unhedged = run(ctx, chain, [HEDGE_ORDER], **base)["trades"][0]
    hedged = run(ctx, chain, [HEDGE_ORDER], **base,
                 hedge={"rebalance": "daily", "rate": RATE})["trades"][0]
    assert abs(path[-1] / path[0] - 1) > 0.15                       # a path that really moved
    assert abs(unhedged["pnl_usd"]) > 300.0
    assert abs(hedged["pnl_usd"]) < 0.1 * abs(unhedged["pnl_usd"])
    assert hedged["option_pnl_usd"] == pytest.approx(unhedged["option_pnl_usd"])
    assert hedged["pnl_usd"] == pytest.approx(hedged["option_pnl_usd"] + hedged["hedge_pnl_usd"])


def test_the_hedge_pnl_is_shares_held_times_the_underlying_change(ctx):
    chain, path = hedge_world(up_days=18, seed=5)
    out = run(ctx, chain, [HEDGE_ORDER], contracts=1, fee_per_contract=0.0,
              hedge={"rebalance": "daily", "rate": RATE})
    shares, total = [], 0.0
    for i in range(DAYS):                                           # independent Black-76 deltas
        years = (DAYS - i) / 365
        forward = path[i] * math.exp(RATE * years)
        delta = sum(black76_delta(r, forward, 100.0, SIGMA, years, RATE) for r in ("put", "call"))
        shares.append(-100 * delta * math.exp(RATE * years))
    for i in range(1, DAYS + 1):
        total += shares[i - 1] * (path[i] - path[i - 1])
    marks = out["marks"]
    assert [m["hedge_shares"] for m in marks[:DAYS]] == pytest.approx(shares, abs=1e-6)
    assert out["trades"][0]["hedge_pnl_usd"] == pytest.approx(total, rel=1e-6)
    assert marks[-1]["hedge_shares"] == 0.0                        # flattened at the close
    assert out["summary"]["hedge"]["rebalances"] == DAYS


def test_ex_date_dividends_accrue_on_held_hedge_shares_for_long_and_short(ctx):
    chain, _ = hedge_world(up_days=18, seed=5)
    events = [
        {"symbol": "ABC", "date": day(0).isoformat(), "dividend_amount": 2.0},
        {"symbol": "ABC", "date": day(1).isoformat(), "dividend_amount": 0.5},
        {"symbol": "ABC", "date": day(DAYS).isoformat(), "dividend_amount": 0.1},
        {"symbol": "OTHER", "date": day(1).isoformat(), "dividend_amount": 7.0},
    ]
    for signs in ([1, 1], [-1, -1]):
        structures = {"position": [["put", signs[0]], ["call", signs[1]]]}
        requested = order(strikes=(100, 100), structure="position", expiration=DAYS)
        params = {"contracts": 1, "fee_per_contract": 0.0, "structures": structures,
                  "hedge": {"rebalance": "daily", "rate": RATE}}
        plain = run(ctx, chain, [requested], **params)
        paid = OptionStructureLedger("ledger", {**BASE, **params}).run(
            ctx, {"chain": chain, "orders": [requested], "dividends": events})
        expected = plain["marks"][0]["hedge_shares"] * 0.5
        expected += plain["marks"][-2]["hedge_shares"] * 0.1
        assert paid["trades"][0]["hedge_dividend_usd"] == pytest.approx(expected)
        assert paid["trades"][0]["hedge_pnl_usd"] - plain["trades"][0]["hedge_pnl_usd"] == pytest.approx(expected)
        assert paid["trades"][0]["pnl_usd"] - plain["trades"][0]["pnl_usd"] == pytest.approx(expected)
        assert paid["marks"][0]["hedge_dividend_usd"] == 0.0
        assert paid["marks"][1]["hedge_dividend_usd"] == pytest.approx(
            plain["marks"][0]["hedge_shares"] * 0.5)
        assert paid["marks"][-1]["hedge_dividend_usd"] == pytest.approx(expected)


def test_dividend_cash_on_unquoted_mark_day_survives_to_open_exposure(ctx):
    chain, _ = hedge_world(up_days=18, seed=5)
    chain = [row for row in chain if row["date"] <= day(2).isoformat()]
    for row in chain:
        if row["date"] == day(2).isoformat():
            row["underlying_price"] = None
    params = {"contracts": 1, "fee_per_contract": 0.0,
              "hedge": {"rebalance": "daily", "rate": RATE}}
    plain = run(ctx, chain, [HEDGE_ORDER], **params)
    paid = OptionStructureLedger("ledger", {**BASE, **params}).run(ctx, {
        "chain": chain, "orders": [HEDGE_ORDER], "dividends": [
            {"symbol": "ABC", "date": day(2).isoformat(), "dividend_amount": 0.25}]})
    cash = plain["marks"][1]["hedge_shares"] * 0.25
    assert paid["trades"][0]["status"] == "open_at_end"
    assert paid["trades"][0]["hedge_dividend_usd"] == pytest.approx(cash)
    assert paid["trades"][0]["marked_pnl_usd"] - plain["trades"][0]["marked_pnl_usd"] == pytest.approx(cash)
    assert paid["marks"][-1]["hedge_dividend_usd"] == pytest.approx(cash)


def test_malformed_or_duplicate_dividends_fail_closed(ctx):
    chain, _ = hedge_world(up_days=18, seed=5)
    node = OptionStructureLedger("ledger", {**BASE, "contracts": 1,
        "hedge": {"rebalance": "daily", "rate": RATE}})
    good = {"symbol": "ABC", "date": day(1).isoformat(), "dividend_amount": 0.2}
    for events in ([good, good], [{**good, "dividend_amount": -0.1}],
                   [{**good, "date": "not-a-date"}]):
        with pytest.raises(ValueError, match="dividend"):
            node.run(ctx, {"chain": chain, "orders": [HEDGE_ORDER], "dividends": events})


def test_hedge_fees_are_per_share_traded_and_enter_the_total(ctx):
    chain, _ = hedge_world(up_days=21)
    free = run(ctx, chain, [HEDGE_ORDER], contracts=1, fee_per_contract=0.0,
               hedge={"rebalance": "daily", "rate": RATE})["trades"][0]
    paid = run(ctx, chain, [HEDGE_ORDER], contracts=1, fee_per_contract=0.0,
               hedge={"rebalance": "daily", "rate": RATE, "fee_per_share": 0.01})["trades"][0]
    assert paid["hedge_fees_usd"] > 0.0
    assert paid["pnl_usd"] == pytest.approx(free["pnl_usd"] - paid["hedge_fees_usd"])
    assert paid["fees_usd"] == pytest.approx(paid["hedge_fees_usd"])


def test_the_rebalance_rules_every_n_and_threshold(ctx):
    chain, _ = hedge_world(up_days=15)
    every = run(ctx, chain, [HEDGE_ORDER], contracts=1,
                hedge={"rebalance": "every_n", "every_n": 5, "rate": RATE})
    assert every["summary"]["hedge"]["rebalances"] == 6            # calendar steps 0, 5, ..., 25
    never = run(ctx, chain, [HEDGE_ORDER], contracts=1,
                hedge={"rebalance": "threshold", "threshold": 1e9, "rate": RATE})
    assert never["summary"]["hedge"]["rebalances"] == 0
    assert all(m["hedge_shares"] == 0.0 for m in never["marks"])
    some = run(ctx, chain, [HEDGE_ORDER], contracts=1,
               hedge={"rebalance": "threshold", "threshold": 5.0, "rate": RATE})
    assert 0 < some["summary"]["hedge"]["rebalances"] < DAYS


def test_a_day_the_hedge_cannot_be_priced_keeps_the_old_hedge_and_is_counted(ctx):
    chain, _ = hedge_world(up_days=21)
    chain = [r for r in chain if not (r["date"] == day(10).isoformat() and r["right"] == "put")]
    out = run(ctx, chain, [HEDGE_ORDER], contracts=1,
              hedge={"rebalance": "daily", "rate": RATE})
    marks = out["marks"]
    assert marks[10]["carried"] and marks[10]["hedge_shares"] == marks[9]["hedge_shares"]
    skipped = out["summary"]["hedge"]["skipped"]
    assert skipped["no_leg_quote"] == 1
    # a leg whose mid has collapsed to zero near expiry admits no implied vol: also counted
    assert out["summary"]["hedge"]["rebalances"] == DAYS - sum(skipped.values())


# -- the census, determinism, refusals ---------------------------------------------------------

def test_the_chain_census_counts_what_was_dropped_and_what_conflicted(ctx):
    chain = spread_chain()
    chain.append(row("OTHER", 0, 100, "put", 1.0, 1.2, 50.0))
    chain.append({**row("ABC", 1, 95, "put", 0.7, 0.9, 99.0), "date": "not-a-date"})
    chain.append({**row("ABC", 0, 60, "put", 0.1, 0.2, 101.5)})        # not a needed strike, other underlying
    out = run(ctx, chain, [order()])
    census = out["summary"]["chain"]
    assert census["rows_in"] == len(chain) and census["rows_used"] == 8
    assert census["dropped"]["other_symbol"] == 1 and census["dropped"]["bad_date"] == 1
    assert census["dropped"]["not_needed"] >= 1
    assert census["underlying_conflicts"] == 1
    assert out["marks"][0]["underlying"] == 101.0                  # first seen wins


def test_the_result_is_deterministic_and_independent_of_chain_row_order(ctx):
    chain, orders = refusal_world()
    orders += [order("G", open_day=0, expiration=4)]
    first = run(ctx, chain, orders)
    assert first == run(ctx, list(chain), orders)
    shuffled = list(chain)
    random.Random(4).shuffle(shuffled)
    assert run(ctx, shuffled, orders) == first


@pytest.mark.parametrize("chain,orders,match", [
    ([], [order()], "no chain rows"),
    (spread_chain(), [], "no orders"),
    (spread_chain(), ["x"], "must be a mapping"),
    (spread_chain(), [{**order(), "structure": "iron_fly"}], "unknown structure"),
    (spread_chain(), [{**order(), "strikes": [95]}], "one positive strike per leg"),
    (spread_chain(), [{**order(), "strikes": [95, -1]}], "one positive strike per leg"),
    (spread_chain(), [{**order(), "open_date": "03/02/2020"}], "ISO date"),
    (spread_chain(), [{k: v for k, v in order().items() if k != "symbol"}], "lacks the field"),
    (spread_chain() + [spread_chain()[0]], [order()], "repeats"),
    (["x"], [order()], "must be a mapping")])
def test_a_degenerate_or_malformed_input_refuses(ctx, chain, orders, match):
    with pytest.raises(ValueError, match=match):
        run(ctx, chain, orders)


def test_an_exit_rule_order_without_its_exit_date_refuses(ctx):
    with pytest.raises(ValueError, match="lacks the field 'exit_date'"):
        run(ctx, exit_chain(), [order()], **EXIT)


@pytest.mark.parametrize("over", [
    {"bogus": 1}, {"multiplier": 0}, {"multiplier": 1.5}, {"contracts": 0},
    {"fee_per_contract": -1.0}, {"settle_fee_per_contract": "x"}, {"fill": "last"},
    {"close_rule": "never"}, {"year_days": 0}, {"structures": {}},
    {"structures": {"a": [["swap", 1]]}}, {"structures": {"a": [["put", 0]]}},
    {"structures": {"a": []}}, {"chain_fields": {"symbol": "s"}},
    {"chain_fields": {**CHAIN_FIELDS, "close": "close"}},
    {"order_fields": {**ORDER_FIELDS, "exit": "x"}},
    {"close_rule": "exit"},
    {"hedge": {"rebalance": "hourly"}}, {"hedge": {"rebalance": "every_n"}},
    {"hedge": {"rebalance": "daily", "every_n": 3}},
    {"hedge": {"rebalance": "threshold"}}, {"hedge": {"rebalance": "threshold", "threshold": -1}},
    {"hedge": {"rebalance": "daily", "fee_per_share": -1}},
    {"hedge": {"rebalance": "daily", "bogus": 1}}, {"hedge": "daily"}])
def test_default_deny_params(over):
    assert OptionStructureLedger.validate_params({**BASE, **over})
    with pytest.raises(ConfigError):
        OptionStructureLedger("ledger", {**BASE, **over})


def test_the_required_params_are_required():
    for name in ("chain_fields", "order_fields", "structures", "multiplier"):
        assert OptionStructureLedger.validate_params({k: v for k, v in BASE.items() if k != name})
    assert OptionStructureLedger.validate_params(BASE) == []


def test_it_is_wired_by_import_path_and_registers_nothing():
    assert NODE_KINDS == ()
    assert "OptionStructureLedger" not in DEFAULT_NODE_KINDS
    assert OptionStructureLedger.role == "transform"
    assert OptionStructureLedger.outputs == ("trades", "marks", "summary")


# -- exits are not free fills (review finding 1) ---------------------------------------------

def zero_exit_chain(short_quote, long_quote=(0.4, 0.6)):
    chain = [r for r in spread_chain() if r["date"] != day(3).isoformat()]
    chain += [row("ABC", 3, 95, "put", *long_quote, 96.0), row("ABC", 3, 100, "put", *short_quote, 96.0)]
    return chain


def test_a_short_leg_cannot_be_bought_back_for_free_on_exit(ctx):
    chain = zero_exit_chain((0.0, 0.0))
    out = run(ctx, chain, [order(exit_date=day(3).isoformat())], **EXIT)
    (trade,) = out["trades"]
    assert out["summary"]["exit_refused"] == {"quote_bought_leg_no_ask": 1}
    assert trade["exit_refused"] is True and trade["reason_closed"] == "expiry"
    assert trade["pnl_usd"] == pytest.approx(180.0 - 600.0 - 2.6)       # not +400


def test_a_long_leg_with_no_bid_refuses_the_exit_by_default(ctx):
    chain = zero_exit_chain((1.0, 1.2), long_quote=(0.0, 0.1))
    out = run(ctx, chain, [order(exit_date=day(3).isoformat())], **EXIT)
    assert out["summary"]["exit_refused"] == {"exit_zero_bid_long": 1}
    assert out["trades"][0]["reason_closed"] == "expiry"


def test_a_zero_bid_long_may_close_at_nothing_when_declared_and_is_counted(ctx):
    chain = zero_exit_chain((1.0, 1.2), long_quote=(0.0, 0.1))
    out = run(ctx, chain, [order(exit_date=day(3).isoformat())], exit_zero_bid="allow", **EXIT)
    (trade,) = out["trades"]
    assert trade["reason_closed"] == "exit" and out["summary"]["exit_refused"] == {}
    assert out["summary"]["exit_zero_bid_long"] == 1
    assert trade["close_cash_usd"] == pytest.approx(200 * (0.0 - 1.2))


def test_the_exit_zero_bid_param_is_default_deny():
    bad = {**BASE, **EXIT, "exit_zero_bid": "maybe"}
    assert OptionStructureLedger.validate_params(bad)


# -- fees follow the quantities (review finding 4) ----------------------------------------------

def test_fees_scale_with_the_absolute_leg_quantities_not_the_leg_count(ctx):
    structures = {**STRUCTURES, "ratio": [["put", 1], ["put", -2]]}
    out = run(ctx, spread_chain(), [order(structure="ratio")], structures=structures,
              settle_fee_per_contract=1.0)
    (trade,) = out["trades"]
    assert trade["open_fees_usd"] == pytest.approx(0.65 * 3 * 2)         # 3 contracts a unit, x2 units
    assert trade["close_fees_usd"] == pytest.approx(1.0 * 2 * 2)         # the 2-lot short put is ITM


# -- every order refused (review finding 5) -----------------------------------------------------

def test_when_every_order_is_refused_the_node_raises_with_the_counts(ctx):
    with pytest.raises(ValueError, match=r"every order was refused.*expiry_not_after_open.*2"):
        run(ctx, spread_chain(), [order(expiration=0), order(expiration=0)])
    with pytest.raises(ValueError, match="sold_leg_no_bid"):
        chain, orders = refusal_world()
        run(ctx, chain, [o for o in orders if o["symbol"] == "A"])
