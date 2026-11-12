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


# -- post-solve labels and descriptive groups --------------------------------------------------

def test_strategy_labels_preserve_outer_size_and_group_only_closed_pnl(ctx):
    chain = spread_chain("CLOSED")
    chain += [row("OPEN", 0, 95, "put", .9, 1.1, 101, expiration=6),
              row("OPEN", 0, 100, "put", 2., 2.2, 101, expiration=6),
              row("MISSING", 0, 95, "put", .9, 1.1, 101),
              row("MISSING", 0, 100, "put", 2., 2.2, 101)]
    out = run(ctx, chain, [order("CLOSED"), order("OPEN", expiration=6), order("MISSING")])
    assert [t["status"] for t in out["trades"]] == ["closed", "open_at_end", "unsettled"]
    for trade in out["trades"]:
        label = trade["option_strategy"]
        assert label["name"] == "bull_put_spread" and label["units"] == 2
        assert [leg["quantity"] for leg in label["legs"]] == [2, -2]
        assert label["input_contract_count"] == label["net_contract_count"] == 4
        assert trade["structure"] == "put_spread"
    (group,) = out["summary"]["strategy_groups"]
    assert group == {
        "name": "bull_put_spread", "family": "vertical", "variant": None,
        "opened": 3, "closed": 1, "open_at_end": 1, "unsettled": 1,
        "gross_contracts_opened": 12, "unbounded_loss_opened": 0,
        "closed_pnl_usd": pytest.approx(-422.6), "closed_option_pnl_usd": pytest.approx(-422.6),
        "closed_fees_usd": pytest.approx(2.6), "option_wins": 0, "option_losses": 1, "option_flats": 0,
        "mean_closed_option_pnl_usd": pytest.approx(-422.6), "closed_option_win_rate": 0.}


def test_strategy_groups_with_no_closed_trade_have_no_mean_or_win_rate(ctx):
    out = run(ctx, [r for r in spread_chain() if r["date"] < day(4).isoformat()], [order()])
    (group,) = out["summary"]["strategy_groups"]
    assert group["closed"] == 0 and group["open_at_end"] == 1
    assert group["closed_option_pnl_usd"] == group["closed_pnl_usd"] == 0.
    assert group["mean_closed_option_pnl_usd"] is group["closed_option_win_rate"] is None
    assert group["option_wins"] == group["option_losses"] == group["option_flats"] == 0


def test_strategy_flat_option_book_keeps_gross_fees_and_is_never_cash(ctx):
    chain = [row("ABC", 0, 100, "call", 2., 3., 101),
             row("ABC", 4, 100, "call", 10., 10.1, 110)]
    out = run(ctx, chain, [order(strikes=(100, 100), structure="offset")],
              structures={"offset": [["call", 1], ["call", -1]]}, contracts=3)
    (trade,) = out["trades"]
    label = trade["option_strategy"]
    assert label["name"] == "flat_option_book" and label["units"] == 0
    assert label["input_contract_count"] == 6 and label["net_contract_count"] == 0
    assert trade["pnl_usd"] == pytest.approx(-303.9)
    group = out["summary"]["strategy_groups"][0]
    assert group["gross_contracts_opened"] == 6 and group["closed_fees_usd"] == pytest.approx(3.9)
    assert group["closed_option_pnl_usd"] == pytest.approx(-303.9)


def test_strategy_groups_separate_sizes_wings_and_signed_outcomes(ctx):
    structures = {"a": [["put", 1], ["put", -1], ["call", -1], ["call", 1]],
                  "b": [["put", 2], ["put", -2], ["call", -2], ["call", 2]]}
    chain, orders = [], []
    for symbol, strikes, structure, spot in (
            ("A", (80, 90, 110, 120), "a", 100),
            ("B", (80, 90, 110, 120), "b", 130),
            ("C", (80, 90, 110, 125), "a", 100)):
        orders.append(order(symbol, strikes, structure))
        for right, strike, market in zip(("put", "put", "call", "call"), strikes,
                                        ((.9, 1.1), (2., 2.2), (2., 2.2), (.9, 1.1))):
            chain.append(row(symbol, 0, strike, right, *market, 100))
            chain.append(row(symbol, 4, strike, right, .01, .02, spot))
    out = run(ctx, chain, orders, structures=structures, contracts=1)
    groups = out["summary"]["strategy_groups"]
    equal, unequal = groups
    assert equal["variant"] == "equal_wings" and unequal["variant"] == "unequal_wings"
    # A earns 180 - 2.6; B earns 360 - 2000 - 5.2. Sizes share the anatomy group.
    assert equal["opened"] == equal["closed"] == 2 and equal["gross_contracts_opened"] == 12
    assert equal["closed_option_pnl_usd"] == pytest.approx(177.4 - 1645.2)
    assert equal["option_wins"] == equal["option_losses"] == 1 and equal["option_flats"] == 0
    assert equal["closed_option_win_rate"] == .5
    assert equal["mean_closed_option_pnl_usd"] == pytest.approx((177.4 - 1645.2)/2)
    assert unequal["closed"] == 1 and unequal["closed_option_pnl_usd"] == pytest.approx(177.4)


def test_strategy_groups_keep_option_and_hedge_results_distinct(ctx):
    chain, _ = hedge_world(up_days=21)
    out = run(ctx, chain, [HEDGE_ORDER], contracts=1, fee_per_contract=0.,
              hedge={"rebalance": "daily", "rate": RATE})
    trade = out["trades"][0]
    group = out["summary"]["strategy_groups"][0]
    assert trade["option_strategy"]["name"] == group["name"] == "long_straddle"
    assert group["closed_option_pnl_usd"] == pytest.approx(trade["option_pnl_usd"])
    assert group["closed_pnl_usd"] == pytest.approx(trade["option_pnl_usd"] + trade["hedge_pnl_usd"])
    assert abs(group["closed_option_pnl_usd"] - group["closed_pnl_usd"]) > 300.


def test_strategy_groups_count_flat_pnl_and_unbounded_loss(ctx):
    chain = [row("ABC", 0, 100, "call", 1., 1.1, 100),
             row("ABC", 4, 100, "call", 1., 1.1, 101)]
    out = run(ctx, chain, [order(strikes=(100,), structure="naked_call")],
              contracts=1, fee_per_contract=0.)
    (group,) = out["summary"]["strategy_groups"]
    assert group["name"] == "short_call" and group["unbounded_loss_opened"] == 1
    assert group["option_flats"] == 1 and group["option_wins"] == group["option_losses"] == 0
    assert group["closed_option_pnl_usd"] == group["mean_closed_option_pnl_usd"] == 0.
    assert group["closed_option_win_rate"] == 0.



def test_funded_account_contributions_overlap_and_atomic_admission():
    """Deposits are not profit; outstanding risk consumes shared capital."""
    from dskit.pipeline.libs import option_ledger as pack

    assert hasattr(pack, "FundedOptionAccount"), "missing funded shared account"
    account = pack.FundedOptionAccount(10000., 1000., 14, "2019-01-02")
    assert account.advance("2019-01-02", {}) == 0.
    a = {"id": "a", "symbol": "X", "expiry": "2019-02-01", "reserve": 2000., "credit": 200.}
    limits = [{"members": ["X", "Y"], "upper": 2500.}]
    assert account.admit("2019-01-02", [a], limits)
    assert account.funding == 10000. and account.cash == 10200.
    assert account.remaining(["X", "Y"], 1.) == 8000.
    before = account.cash
    assert not account.admit("2019-01-02", [
        {"id": "b", "symbol": "Y", "expiry": "2019-02-01", "reserve": 3000., "credit": -3000.}], limits)
    assert account.cash == before and set(account.open) == {"a"}
    assert account.advance("2019-01-16", {}) == 1000.
    assert account.advance("2019-02-01", {"a": -1500.}) == 1000.
    assert account.funding == 10500. and account.cash == 10500.
    assert account.contributions == 12000. and account.realized == -1500.
    assert not account.open



def test_funded_account_invalid_settlement_and_debit_are_atomic():
    """A missed maturity or unfunded guaranteed-payoff debit is never admitted."""
    from dskit.pipeline.libs.option_ledger import FundedOptionAccount

    account = FundedOptionAccount(100., 10., 14, "2019-01-02")
    with pytest.raises(ValueError):
        account.admit("2019-01-02", [
            {"id": "box", "symbol": "X", "expiry": "2019-02-01", "reserve": 0., "credit": -150.}],
            [{"members": ["X"], "upper": 100.}])
    assert account.cash == 100. and not account.open
    assert account.admit("2019-01-02", [
        {"id": "a", "symbol": "X", "expiry": "2019-01-16", "reserve": 50., "credit": 10.}],
        [{"members": ["X"], "upper": 100.}])
    with pytest.raises(ValueError):
        account.advance("2019-01-16", {})
    assert account.contributions == 100. and account.cash == 110.
    with pytest.raises(ValueError):
        account.advance("2019-01-17", {"a": 0.})
    with pytest.raises(ValueError):
        account.advance("2019-01-16", {"a": -60.})
    assert account.advance("2019-01-16", {"a": -50.}) == 10.
    assert account.cash == 60.

def test_standard_split_converts_open_contracts_on_effective_date(ctx):
    """The split changes held contracts, not the original fill or its fee."""
    chain = [row("ABC", 0, 610, "call", 10.0, 11.0, 600.0)]
    chain += [row("ABC", i, 61, "call", bid, bid + 0.2, under)
              for i, bid, under in [(1, 2.0, 62.0), (2, 3.0, 64.0),
                                    (3, 5.0, 66.0), (4, 8.9, 70.0)]]
    event = {"symbol": "ABC", "root_before": "ABC", "root_after": "ABC",
             "deliverable_shares": 100, "published": "2020-02-28",
             "effective": day(1).isoformat(), "ratio": 10, "strike_divisor": 10,
             "multiplier": 100, "basis_before": 0.1, "basis_after": 1.0}
    out = OptionStructureLedger("ledger", BASE).run(
        ctx, {"chain": chain, "orders": [order(strikes=(610,), structure="naked_call")],
              "adjustments": [event]})
    trade = out["trades"][0]
    assert trade["strikes"] == [610.0] and trade["contracts"] == 2
    assert trade["premium_usd"] == 2000.0
    assert trade["open_fees_usd"] == 1.3
    assert out["marks"][1]["carried"] is False
    assert out["marks"][1]["mark_value_usd"] == pytest.approx(-4200.0)
    assert trade["close_cash_usd"] == pytest.approx(-18000.0)
    assert trade["pnl_usd"] == pytest.approx(-16001.3)


@pytest.mark.parametrize("change", [
    {"root_after": "ABC1"},
    {"deliverable_shares": 1000},
    {"ratio": 1},
    {"strike_divisor": 5},
    {"multiplier": 10},
    {"basis_after": 2.},
    {"published": "2020-03-02"},
])
def test_standard_split_refuses_nonstandard_or_inconsistent_event(change):
    """Only a same-root standard integer conversion may change held contracts."""
    from dskit.pipeline.libs.option_ledger import StandardSplitAdjustment
    event = {"symbol": "ABC", "root_before": "ABC", "root_after": "ABC",
             "deliverable_shares": 100, "published": "2020-02-28",
             "effective": "2020-03-01", "ratio": 10, "strike_divisor": 10,
             "multiplier": 100, "basis_before": 0.1, "basis_after": 1.}
    event.update(change)
    with pytest.raises(ValueError, match="unsupported"):
        StandardSplitAdjustment.parse([event], 100)


# -- the fixed forward hedge (ADR-0312 Amendment 1, S2) ----------------------------------------
# Units and the parity forwards are INPUTS: the order carries the book's held shares and the child
# supplies F_t per (order, day) through ``hedge_forwards``. The ledger computes no forward and no
# delta; the cost rule is StaticDeltaHedge.carry_costs, imported.

from dskit.pipeline.option_exposure import StaticDeltaHedge  # noqa: E402

FIXED = {"rebalance": "fixed", "instrument": "forward", "fee_per_share": 0.0, "half_spread_bps": 0.0}
HEDGE_ORDER_FIELDS = {**ORDER_FIELDS, "hedge_units": "hedge_units"}
UNITS = -50.0


def fixed_order(units=UNITS, **extra):
    return {**HEDGE_ORDER, "hedge_units": units, **extra}


def forwards(path, order_index=0, carried=(), skip=(), last=DAYS - 1):
    """The child's parity forwards: spot carried at the rate, a distinct F_t each day."""
    return [{"order": order_index, "date": day(i).isoformat(),
             "forward": path[i] * math.exp(RATE * (DAYS - i) / 365) * (1.0 + 0.001 * i),
             "carried": i in carried}
            for i in range(last + 1) if i not in skip]


def fwd(path, i):
    return path[i] * math.exp(RATE * (DAYS - i) / 365) * (1.0 + 0.001 * i)


def runf(ctx, chain, orders, rows, **over):
    params = {**BASE, "contracts": 1, "fee_per_contract": 0.0, "order_fields": HEDGE_ORDER_FIELDS,
              "hedge": FIXED, **over}
    inputs = {"chain": chain, "orders": orders}
    if rows is not None:
        inputs["hedge_forwards"] = rows
    return OptionStructureLedger("ledger", params).run(ctx, inputs)


def test_absent_fixed_forward_options_leave_every_existing_output_byte_identical(ctx):
    """Parity: sha256 of full outputs, pinned on unmodified main 9e6f5cf (equal to the old S2 pins)."""
    import hashlib
    import json

    def digest(out):
        return hashlib.sha256(json.dumps(out, sort_keys=True, default=str).encode()).hexdigest()[:16]
    chain, _ = hedge_world(up_days=21)
    pinned = {
        "none": (None, "2de2a3131d18d75e"),
        "daily": ({"rebalance": "daily", "rate": RATE}, "ae9a156883627877"),
        "every_n": ({"rebalance": "every_n", "every_n": 5, "rate": RATE}, "45830ca1aaa03a24"),
        "threshold": ({"rebalance": "threshold", "threshold": 5.0, "rate": RATE,
                       "fee_per_share": 0.01}, "e0f51fabc05ba6c6")}
    for name, (hedge, want) in pinned.items():
        over = {"hedge": hedge} if hedge else {}
        assert digest(run(ctx, chain, [HEDGE_ORDER], **over)) == want, name
    assert digest(run(ctx, spread_chain(), [order()])) == "d0419059f207beaa"


def test_forward_fields_are_absent_when_no_forward_hedge_and_the_old_columns_stay(ctx):
    chain, _ = hedge_world(up_days=21)
    for over in ({}, {"hedge": {"rebalance": "daily", "rate": RATE}}):
        out = run(ctx, chain, [HEDGE_ORDER], **over)
        rows = [out["trades"][0], *out["marks"]]
        assert all("hedge_reference" not in r and "mark_forward_carried" not in r for r in rows)
        assert "mark_forward_carried" not in out["summary"].get("hedge", {})
        for column in ("hedge_pnl_usd", "hedge_dividend_usd"):
            assert column in out["trades"][0] and column in out["marks"][0]
        assert "hedge_fees_usd" in out["trades"][0] and "hedge_shares" in out["marks"][0]
    plain = run(ctx, chain, [HEDGE_ORDER])
    assert plain["trades"][0]["hedge_fees_usd"] == 0.0
    assert all(m["hedge_shares"] == 0.0 and m["hedge_pnl_usd"] == 0.0 for m in plain["marks"])


@pytest.mark.parametrize("units", [UNITS, 37.0])
def test_a_fixed_forward_settles_units_times_settle_minus_reference_with_no_entry_cash(ctx, units):
    chain, path = hedge_world(up_days=21)
    plain = run(ctx, chain, [HEDGE_ORDER], contracts=1, fee_per_contract=0.0)
    out = runf(ctx, chain, [fixed_order(units)], forwards(path))
    trade, ref = out["trades"][0], fwd(path, 0)
    assert trade["hedge_pnl_usd"] == pytest.approx(units * (path[-1] - ref), rel=1e-12)
    assert trade["hedge_reference"] == ref
    assert trade["hedge_dividend_usd"] == 0.0
    assert trade["premium_usd"] == plain["trades"][0]["premium_usd"]          # no cash at entry
    assert trade["option_pnl_usd"] == pytest.approx(plain["trades"][0]["option_pnl_usd"])
    assert trade["pnl_usd"] == pytest.approx(trade["option_pnl_usd"] + trade["hedge_pnl_usd"])


def test_the_mark_is_units_times_the_supplied_forward_minus_the_reference_not_spot(ctx):
    chain, path = hedge_world(up_days=18, seed=5)
    out = runf(ctx, chain, [fixed_order()], forwards(path))
    ref = fwd(path, 0)
    assert out["marks"][0]["hedge_pnl_usd"] == 0.0                             # day 0: no carry artifact
    assert out["marks"][0]["hedge_reference"] == ref
    for i, m in enumerate(out["marks"][:-1]):
        assert m["hedge_pnl_usd"] == pytest.approx(UNITS * (fwd(path, i) - ref), rel=1e-12, abs=1e-12)
        assert m["hedge_pnl_usd"] != pytest.approx(UNITS * (path[i] - ref)) or i == 0
    assert out["marks"][-1]["hedge_pnl_usd"] == out["trades"][0]["hedge_pnl_usd"]
    assert out["marks"][-1]["hedge_pnl_usd"] == pytest.approx(UNITS * (path[-1] - ref))


def test_a_fixed_hedge_holds_its_units_and_never_rebalances(ctx):
    chain, path = hedge_world(up_days=18, seed=5)
    out = runf(ctx, chain, [fixed_order()], forwards(path))
    assert [m["hedge_shares"] for m in out["marks"][:-1]] == [UNITS] * DAYS
    assert out["marks"][-1]["hedge_shares"] == 0.0                             # settled
    assert out["summary"]["hedge"]["rebalances"] == 0
    assert out["summary"]["hedge"]["skipped"] == {}


def test_a_fixed_forward_pays_no_dividend_cash(ctx):
    chain, path = hedge_world(up_days=18, seed=5)
    params = {**BASE, "contracts": 1, "fee_per_contract": 0.0, "order_fields": HEDGE_ORDER_FIELDS,
              "hedge": FIXED}
    out = OptionStructureLedger("ledger", params).run(
        None, {"chain": chain, "orders": [fixed_order()], "hedge_forwards": forwards(path),
               "dividends": [{"symbol": "ABC", "date": day(10).isoformat(), "dividend_amount": 0.5}]})
    assert out["trades"][0]["hedge_dividend_usd"] == 0.0
    assert out["trades"][0]["hedge_pnl_usd"] == pytest.approx(UNITS * (path[-1] - fwd(path, 0)))


@pytest.mark.parametrize("units", [UNITS, 37.0])
def test_costs_are_the_imported_carry_costs_fee_both_legs_half_spread_both_prices_borrow_zero(ctx, units):
    chain, path = hedge_world(up_days=21)
    hedge = {**FIXED, "fee_per_share": 0.02, "half_spread_bps": 7.0}
    out = runf(ctx, chain, [fixed_order(units)], forwards(path), hedge=hedge)
    rule = StaticDeltaHedge({"hedge_fee_per_share": 0.02, "hedge_half_spread_bps": 7.0,
                             "hedge_borrow_rate": 0.9})
    ref = fwd(path, 0)
    want = rule.carry_costs(units, ref, path[-1], DAYS)["trade_costs"]
    free = runf(ctx, chain, [fixed_order(units)], forwards(path))["trades"][0]
    trade = out["trades"][0]
    assert want == pytest.approx(abs(units) * (0.04 + (ref + path[-1]) * 7.0 / 1e4))
    assert trade["hedge_fees_usd"] == pytest.approx(want, rel=1e-12)       # no borrow, though rate 0.9 exists
    assert trade["fees_usd"] == pytest.approx(want)
    assert trade["pnl_usd"] == pytest.approx(free["pnl_usd"] - want)
    # the entry half is on the open row (fee and half-spread at F_fill), all of it on the settle row
    entry = rule.carry_costs(units, ref, ref, DAYS)["trade_costs"] / 2
    assert out["marks"][0]["pnl_usd"] == pytest.approx(out["marks"][0]["option_pnl_usd"] - entry)
    assert out["marks"][-1]["pnl_usd"] == pytest.approx(trade["pnl_usd"])


@pytest.mark.parametrize(("units", "held"), [(-288.6, -288), (-288.4, -288), (288.9, 288), (0.7, 0)])
def test_round_units_truncates_toward_zero_not_half_away_or_half_even(ctx, units, held):
    chain, path = hedge_world(up_days=21)
    out = runf(ctx, chain, [fixed_order(units)], forwards(path),
               hedge={**FIXED, "round_units": True})
    assert out["marks"][0]["hedge_shares"] == held
    assert out["trades"][0]["hedge_pnl_usd"] == pytest.approx(held * (path[-1] - fwd(path, 0)), abs=1e-9)
    raw = runf(ctx, chain, [fixed_order(units)], forwards(path))
    assert raw["marks"][0]["hedge_shares"] == units                             # off: units as supplied


def test_a_fixed_forward_exit_unwinds_at_the_exit_day_forward_with_the_closing_cost(ctx):
    chain, path = hedge_world(up_days=21)
    hedge = {**FIXED, "fee_per_share": 0.02, "half_spread_bps": 7.0}
    out = runf(ctx, chain, [fixed_order(exit=day(12).isoformat())], forwards(path), hedge=hedge,
               close_rule="exit", order_fields={**HEDGE_ORDER_FIELDS, "exit": "exit"})
    trade, ref = out["trades"][0], fwd(path, 0)
    rule = StaticDeltaHedge({"hedge_fee_per_share": 0.02, "hedge_half_spread_bps": 7.0,
                             "hedge_borrow_rate": 0.0})
    assert trade["reason_closed"] == "exit"
    assert trade["hedge_pnl_usd"] == pytest.approx(UNITS * (fwd(path, 12) - ref), rel=1e-12)
    assert trade["hedge_pnl_usd"] != pytest.approx(UNITS * (path[12] - ref))      # not last spot
    assert trade["hedge_fees_usd"] == pytest.approx(
        rule.carry_costs(UNITS, ref, fwd(path, 12), 12)["trade_costs"])


def test_a_missing_forward_row_refuses_the_hedged_order_and_never_falls_back_to_spot(ctx):
    chain, path = hedge_world(up_days=21)
    # no row at all, and none on the open day: the order is refused at open, counted
    for rows in (None, forwards(path, skip=(0,))):
        with pytest.raises(ValueError, match="hedge_forward_missing"):
            runf(ctx, chain, [fixed_order()], rows)
    second = fixed_order()
    out = runf(ctx, chain, [second, fixed_order()],
               forwards(path, order_index=1))                                   # order 0 has no rows
    assert out["summary"]["refused_at_open"] == {"hedge_forward_missing": 1}
    assert [t["order_index"] for t in out["trades"]] == [1]
    # a mark day without a row carries the last forward, flagged and counted under the stable code
    out = runf(ctx, chain, [fixed_order()], forwards(path, skip=(7,)))
    assert out["trades"][0]["hedge_forward_missing"] == 1 and out["trades"][0]["mark_forward_carried"] == 1
    flagged = [m for m in out["marks"] if m["mark_forward_carried"]]
    assert [m["date"] for m in flagged] == [day(7).isoformat()]
    assert flagged[0]["hedge_pnl_usd"] == out["marks"][[m["date"] for m in out["marks"]].index(day(6).isoformat())]["hedge_pnl_usd"]
    # the exit day needs its row: without it the exit is REFUSED under a stable code (S3 note) and the
    # trade runs on to its expiration, where the forward settles at the underlying
    out = runf(ctx, chain, [fixed_order(exit=day(12).isoformat())], forwards(path, skip=(12,)),
               close_rule="exit", order_fields={**HEDGE_ORDER_FIELDS, "exit": "exit"})
    assert out["summary"]["exit_refused"] == {"hedge_forward_missing": 1}
    assert out["trades"][0]["reason_closed"] == "expiry" and out["trades"][0]["exit_refused"] is True
    runf(ctx, chain, [fixed_order()], forwards(path, last=DAYS - 1))             # no expiry-day row: fine


def test_carried_marks_are_counted_and_flagged_not_hidden(ctx):
    chain, path = hedge_world(up_days=21)
    out = runf(ctx, chain, [fixed_order()], forwards(path, carried=(3, 4, 9)))
    assert out["trades"][0]["mark_forward_carried"] == 3
    assert out["summary"]["hedge"]["mark_forward_carried"] == 3
    flagged = [i for i, m in enumerate(out["marks"]) if m["mark_forward_carried"]]
    assert flagged == [3, 4, 9]
    clean = runf(ctx, chain, [fixed_order()], forwards(path))
    assert clean["trades"][0]["mark_forward_carried"] == 0
    assert clean["summary"]["hedge"]["mark_forward_carried"] == 0


def test_a_fixed_forward_order_without_hedge_units_or_with_bad_forward_rows_refuses(ctx):
    chain, path = hedge_world(up_days=21)
    bare = {k: v for k, v in HEDGE_ORDER.items()}
    with pytest.raises(ValueError, match="hedge_units"):
        runf(ctx, chain, [bare], forwards(path))
    good = forwards(path)
    for bad in ([{**good[0], "forward": -1.0}, *good[1:]], [{**good[0], "forward": float("nan")}, *good[1:]],
                [*good, good[3]], [{**good[0], "date": "nope"}, *good[1:]],
                [{k: v for k, v in good[0].items() if k != "carried"}, *good[1:]]):
        with pytest.raises(ValueError, match="hedge_forwards"):
            runf(ctx, chain, [fixed_order()], bad)


@pytest.mark.parametrize("hedge", [
    {"rebalance": "fixed"},                                                  # fixed needs the forward
    {"rebalance": "daily", "instrument": "forward", "fee_per_share": 0.0, "half_spread_bps": 0.0},
    {"rebalance": "fixed", "instrument": "swap"},
    {**FIXED, "every_n": 3}, {**FIXED, "threshold": 1.0}, {**FIXED, "rate": 0.01},
    {**FIXED, "ratio": 0.5}, {**FIXED, "round_units": "yes"}, {**FIXED, "bogus": 1},
    {"rebalance": "fixed", "instrument": "forward", "fee_per_share": 0.0},   # half-spread required
    {"rebalance": "fixed", "instrument": "forward", "half_spread_bps": 0.0},  # fee required
    {**FIXED, "half_spread_bps": -1.0}, {**FIXED, "half_spread_bps": True},
    {**FIXED, "fee_per_share": -0.1}, {**FIXED, "borrow_rate": 0.01},
    {"rebalance": "daily", "round_units": True}, {"rebalance": "daily", "half_spread_bps": 1.0},
    {"rebalance": "daily", "instrument": "stock", "bogus": 1}])
def test_fixed_forward_params_are_rejected(hedge):
    params = {**BASE, "order_fields": HEDGE_ORDER_FIELDS, "hedge": hedge}
    assert OptionStructureLedger.validate_params(params)
    with pytest.raises(ConfigError):
        OptionStructureLedger("ledger", params)


def test_the_order_field_hedge_units_is_declared_exactly_when_the_forward_hedge_is():
    ok = {**BASE, "order_fields": HEDGE_ORDER_FIELDS, "hedge": FIXED}
    assert OptionStructureLedger.validate_params(ok) == []
    assert OptionStructureLedger.validate_params({**ok, "order_fields": ORDER_FIELDS})
    assert OptionStructureLedger.validate_params({**ok, "hedge": {"rebalance": "daily"}})
    assert OptionStructureLedger.validate_params(
        {**BASE, "order_fields": ORDER_FIELDS, "hedge": FIXED})


def test_the_stock_instrument_named_explicitly_is_the_existing_hedge(ctx):
    chain, _ = hedge_world(up_days=21)
    a = run(ctx, chain, [HEDGE_ORDER], hedge={"rebalance": "daily", "rate": RATE})
    b = run(ctx, chain, [HEDGE_ORDER],
            hedge={"rebalance": "daily", "instrument": "stock", "rate": RATE})
    assert a == b


# ADR-0314: causal, fully cash-funded long-stock inventory; all inputs synthetic.
def _daily_stock_inputs(deltas=(-5, 0, 0, 0, 0), prices=(100, 100, 110, 110, 110)):
    sessions = [f"2020-03-{n:02d}" for n in range(2, 7)]
    return {"sessions": sessions, "symbols": ["ABC"], "decision_dates": [sessions[0]],
            "coverage": {"raw_prices": True, "dividends_complete": True,
                         "corporate_actions": "none"},
            "cash": [{"date": d, "free_cash": 1000.} for d in sessions],
            "market": [{"date": d, "symbol": "ABC", "delta_sh": delta,
                        "active": delta != 0, "known_at": d,
                        "execution_price": price, "close": price}
                       for d, delta, price in zip(sessions, deltas, prices)],
            "dividends": []}


def _daily_stock_run(ctx, inputs, **params):
    import dskit.pipeline.libs.option_ledger as ledger
    assert hasattr(ledger, "DailyStockHedge"), "missing causal daily inventory replay"
    config = {"ratio": 1., "fee_per_share": 0., "half_spread_bps": 0.,
              "capital_charge_rate": 0., "name_cap_usd": 10000.,
              "gross_cap_usd": 10000., "settlement_lag_sessions": 2}
    return ledger.DailyStockHedge("daily_stock", {**config, **params}).run(ctx, inputs)


def test_daily_stock_causal_cash_settlement_oracle(ctx):
    out = _daily_stock_run(ctx, _daily_stock_inputs())
    assert [(r["date"], r["shares"]) for r in out["trades"]] == [
        ("2020-03-03", 5), ("2020-03-04", -5)]
    assert [r["available_cash"] for r in out["daily"]] == [1000, 500, 500, 500, 1050]
    assert [r["sale_receivables"] for r in out["daily"]] == [0, 0, 550, 550, 0]
    assert out["summary"]["stock_pnl_usd"] == 50
    assert out["summary"]["open_shares"] == {"ABC": 0}


def test_daily_stock_costs_dividends_and_capital_by_hand(ctx):
    inputs = _daily_stock_inputs()
    inputs["dividends"] = [{"symbol": "ABC", "ex_date": "2020-03-04",
                            "pay_date": "2020-03-06", "amount": 2.}]
    out = _daily_stock_run(ctx, inputs, fee_per_share=1., half_spread_bps=100.,
                           capital_charge_rate=.365)
    # Buy cost 5*(1+1)=10; sell cost 5*(1+1.10)=10.50; one overnight costs .50.
    assert out["summary"]["trade_costs_usd"] == 20.5
    assert out["summary"]["capital_charge_usd"] == .5
    assert out["summary"]["dividend_income_usd"] == 10
    assert out["summary"]["stock_pnl_usd"] == 39
    assert out["daily"][2]["dividend_receivables"] == 10
    assert out["daily"][2]["available_cash"] == 479
    assert out["daily"][-1]["available_cash"] == 1039


def test_daily_stock_no_current_exposure_or_close_lookahead(ctx):
    original = _daily_stock_inputs()
    _daily_stock_tail(original)
    expected = _daily_stock_run(ctx, original)["trades"][0]
    original["market"][1].update(delta_sh=-9, active=True, close=12345.)
    actual = _daily_stock_run(ctx, original)["trades"][0]
    assert actual == expected


def test_daily_stock_decision_skip_does_not_defer_old_signal(ctx):
    inputs = _daily_stock_inputs(deltas=(-5, 0, 0, 0, 0))
    inputs["decision_dates"].append("2020-03-03")
    out = _daily_stock_run(ctx, inputs)
    assert out["trades"] == []
    # A fresh decision-day signal may execute the following non-decision day.
    inputs["market"][1].update(delta_sh=-2, active=True)
    inputs["market"][2].update(delta_sh=-2, active=True)
    inputs["market"][3].update(delta_sh=-2, active=True)
    out = _daily_stock_run(ctx, inputs)
    assert [(r["date"], r["shares"]) for r in out["trades"]] == [("2020-03-04", 2)]


@pytest.mark.parametrize("delta,ratio", [(20, 1.), (-5, 0.)])
def test_daily_stock_positive_delta_or_zero_ratio_never_buys(ctx, delta, ratio):
    out = _daily_stock_run(ctx, _daily_stock_inputs(deltas=(delta,)*5), ratio=ratio)
    assert out["trades"] == []
    assert out["summary"]["stock_pnl_usd"] == 0


def test_daily_stock_caps_priority_cash_persistence_and_pure_replay(ctx):
    import copy
    inputs = _daily_stock_inputs(deltas=(-8.9,)*5, prices=(100,)*5)
    inputs["symbols"] = ["XYZ", "ABC"]
    inputs["market"] += [{**r, "symbol": "XYZ"} for r in list(inputs["market"])]
    before = copy.deepcopy(inputs)
    out = _daily_stock_run(ctx, inputs, name_cap_usd=600., gross_cap_usd=900.)
    assert [(r["symbol"], r["shares"]) for r in out["trades"]] == [("ABC", 6), ("XYZ", 3)]
    assert out["daily"][-1]["available_cash"] == 100
    assert out["summary"]["open_shares"] == {"ABC": 6, "XYZ": 3}
    assert inputs == before
    inputs["symbols"].reverse()
    inputs["market"].reverse()
    inputs["cash"].reverse()
    assert _daily_stock_run(ctx, inputs, name_cap_usd=600., gross_cap_usd=900.) == out


def test_daily_stock_cash_clips_and_sale_proceeds_do_not_fund_same_day_buy(ctx):
    inputs = _daily_stock_inputs(deltas=(-8, 0, 0, 0, 0), prices=(100,)*5)
    inputs["symbols"].append("XYZ")
    inputs["market"] += [{**r, "symbol": "XYZ", "delta_sh": -8 if i == 1 else 0,
                           "active": i == 1}
                          for i, r in enumerate(list(inputs["market"]))]
    _daily_stock_tail(inputs)
    out = _daily_stock_run(ctx, inputs)
    assert [(r["date"], r["symbol"], r["shares"]) for r in out["trades"]] == [
        ("2020-03-03", "ABC", 8), ("2020-03-04", "ABC", -8),
        ("2020-03-04", "XYZ", 2), ("2020-03-05", "XYZ", -2)]


def test_daily_stock_weekend_charge_and_price_drift_are_disclosed(ctx):
    inputs = _daily_stock_inputs(deltas=(-5,)*5, prices=(100, 100, 120, 120, 120))
    dates = ["2020-03-05", "2020-03-06", "2020-03-09", "2020-03-10", "2020-03-11"]
    inputs["sessions"] = dates
    inputs["decision_dates"] = dates[:1]
    for i, day in enumerate(dates):
        inputs["market"][i].update(date=day, known_at=day)
        inputs["cash"][i]["date"] = day
    out = _daily_stock_run(ctx, inputs, name_cap_usd=500., gross_cap_usd=500.,
                           capital_charge_rate=.365)
    assert out["summary"]["capital_charge_usd"] == pytest.approx(2.7)
    assert out["daily"][2]["gross_cap_breach"] is True
    assert out["daily"][2]["name_cap_breaches"] == ["ABC"]
    assert out["summary"]["open_shares"] == {"ABC": 5}


def test_daily_stock_dividend_on_purchase_day_excludes_new_shares(ctx):
    inputs = _daily_stock_inputs()
    inputs["dividends"] = [{"symbol": "ABC", "ex_date": "2020-03-03",
                            "pay_date": "2020-03-03", "amount": 2.}]
    assert _daily_stock_run(ctx, inputs)["summary"]["dividend_income_usd"] == 0


def test_daily_stock_refuses_negative_cash_after_external_reserve_change(ctx):
    inputs = _daily_stock_inputs()
    inputs["cash"][2]["free_cash"] = 400.
    with pytest.raises(ValueError, match="insufficient settled cash"):
        _daily_stock_run(ctx, inputs)


def test_daily_stock_requires_tail_only_for_actual_trades(ctx):
    inputs = _daily_stock_inputs(deltas=(0, 0, 0, -2, 0))
    with pytest.raises(ValueError, match="incomplete settlement tail"):
        _daily_stock_run(ctx, inputs)
    inputs["market"][3].update(delta_sh=0, active=False)
    assert _daily_stock_run(ctx, inputs)["trades"] == []
    inputs["sessions"] = inputs["sessions"][:1]
    inputs["market"] = inputs["market"][:1]
    inputs["cash"] = inputs["cash"][:1]
    assert _daily_stock_run(ctx, inputs)["summary"]["stock_pnl_usd"] == 0


@pytest.mark.parametrize("mutation", [
    lambda x: x["market"].pop(),
    lambda x: x["market"].append(dict(x["market"][0])),
    lambda x: x["market"][0].update(known_at="2020-03-03"),
    lambda x: x["market"][0].update(delta_sh=float("nan")),
    lambda x: x["market"][0].update(active=False),
    lambda x: x["market"][0].update(execution_price=0),
    lambda x: x["market"][0].update(close=float("inf")),
    lambda x: x["market"][0].update(assignment=100),
    lambda x: x["cash"].pop(),
    lambda x: x["cash"].append(dict(x["cash"][0])),
    lambda x: x["sessions"].reverse(),
    lambda x: x["symbols"].append("ABC"),
    lambda x: x["decision_dates"].append("2020-03-01"),
    lambda x: x["decision_dates"].append("2020-03-02"),
    lambda x: x["coverage"].update(raw_prices=False),
    lambda x: x["coverage"].update(corporate_actions="split"),
    lambda x: x["coverage"].update(dividends_complete=False),
    lambda x: x["dividends"].append({"symbol": "ABC", "ex_date": "2020-03-04",
                                    "pay_date": "2020-03-09", "amount": 1.}),
])
def test_daily_stock_bad_inputs_refuse_atomically(ctx, mutation):
    inputs = _daily_stock_inputs()
    mutation(inputs)
    with pytest.raises(ValueError):
        _daily_stock_run(ctx, inputs)


@pytest.mark.parametrize("params", [{"ratio": 1.1}, {"ratio": True}, {"fee_per_share": -1},
                                    {"half_spread_bps": float("inf")}, {"name_cap_usd": 0},
                                    {"settlement_lag_sessions": 0}, {"borrow_rate": .1}])
def test_daily_stock_config_default_deny(ctx, params):
    with pytest.raises(ConfigError):
        _daily_stock_run(ctx, _daily_stock_inputs(), **params)


def test_daily_stock_existing_greeks_owner_supplies_short_call_signal(ctx):
    from dskit.pipeline.option_exposure import LegGreeks
    owner = LegGreeks({"rate": 0., "multiplier": 100., "min_years": 1e-6, "vol_floor": 0.})
    leg = {"right": "call", "strike": 100., "sign": -1, "quantity": 1,
           "expiry": "2020-04-03"}
    greeks, reason = owner.of(leg, {"bid": 2., "ask": 2.1, "iv": .2},
                             100., 100., "2020-03-02")
    assert reason is None
    assert -52 < greeks["delta_sh"] < -50
    inputs = _daily_stock_inputs(deltas=(greeks["delta_sh"],)*5, prices=(100,)*5)
    for row in inputs["cash"]:
        row["free_cash"] = 10000.
    out = _daily_stock_run(ctx, inputs)
    assert out["summary"]["open_shares"] == {"ABC": 51}
    assert len(out["trades"]) == 1


def _daily_stock_tail(inputs):
    """Add a real later session so late liquidation can settle T+2."""
    day = "2020-03-09"
    inputs["sessions"].append(day)
    inputs["cash"].append({"date": day, "free_cash": 1000.})
    for symbol in inputs["symbols"]:
        previous = next(r for r in reversed(inputs["market"]) if r["symbol"] == symbol)
        inputs["market"].append({**previous, "date": day, "known_at": day,
                                  "delta_sh": 0., "active": False})


def test_daily_stock_deferred_dividend_stays_nonspendable(ctx):
    inputs = _daily_stock_inputs(deltas=(-5, -5, -5, -5, -5), prices=(100,)*5)
    inputs["dividends"] = [{"symbol": "ABC", "ex_date": "2020-03-04",
                            "pay_date": None, "amount": 2.}]
    out = _daily_stock_run(ctx, inputs, defer_unpaid_dividends=True)
    assert out["summary"]["dividend_receivables"] == 10.
    assert out["daily"][-1]["available_cash"] == 500.
    assert out["summary"]["stock_pnl_usd"] == 10.


def test_daily_stock_date_only_settlement_tail(ctx):
    inputs = _daily_stock_inputs(deltas=(-5, -5, -5, 0, 0), prices=(100,)*5)
    inputs["settlement_sessions"] = inputs["sessions"] + ["2020-03-09", "2020-03-10"]
    out = _daily_stock_run(ctx, inputs)
    assert out["trades"][-1]["settlement_date"] == "2020-03-10"
    assert out["daily"][-1]["sale_receivables"] == 500.
    assert out["daily"][-1]["available_cash"] == 500.
    assert len(out["daily"]) == 5


@pytest.mark.parametrize("tail", [[], ["2020-03-09"], ["2020-03-06"], ["2020-03-09", "2020-03-09"]])
def test_daily_stock_invalid_settlement_calendar_refuses(ctx, tail):
    inputs = _daily_stock_inputs(deltas=(-5, -5, -5, 0, 0))
    inputs["settlement_sessions"] = tail
    with pytest.raises(ValueError):
        _daily_stock_run(ctx, inputs)


@pytest.mark.parametrize("deltas", [(-5, -5, 0, 0, 0), (0, -5, 0, 0, 0)])
def test_daily_stock_event_refuses_inventory_or_stale_target(ctx, deltas):
    inputs = _daily_stock_inputs(deltas=deltas)
    inputs["coverage"]["corporate_actions"] = [{"symbol": "ABC", "date": "2020-03-04",
                                               "kind": "unsupported"}]
    with pytest.raises(ValueError, match="corporate action"):
        _daily_stock_run(ctx, inputs)


def test_daily_stock_event_flat_control(ctx):
    inputs = _daily_stock_inputs(deltas=(0,)*5)
    inputs["coverage"]["corporate_actions"] = [{"symbol": "ABC", "date": "2020-03-04",
                                               "kind": "unsupported"}]
    assert _daily_stock_run(ctx, inputs)["trades"] == []
