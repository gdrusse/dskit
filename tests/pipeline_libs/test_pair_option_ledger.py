"""Atomic pair replay tests built from hand-calculated structure cashflows."""

import pytest

from dskit.pipeline.libs.pair_option_ledger import PairedOptionLedger
from dskit.pipeline.node import NodeContext

CHAIN_FIELDS = {
    "symbol": "symbol",
    "date": "date",
    "expiration": "expiration",
    "strike": "strike",
    "right": "right",
    "bid": "bid",
    "ask": "ask",
    "underlying": "underlying_price",
}
ORDER_FIELDS = {
    "date": "date",
    "symbol": "symbol",
    "expiration": "expiration",
    "strikes": "strikes",
    "structure": "structure",
}
LEDGER = {
    "chain_fields": CHAIN_FIELDS,
    "order_fields": ORDER_FIELDS,
    "structures": {"short_call": [["call", -1]], "long_call": [["call", 1]]},
    "multiplier": 100,
    "fee_per_contract": 1.0,
}
OPEN, MID, EXP = "2020-01-02", "2020-01-03", "2020-01-06"


def chain():
    return [
        {
            "symbol": symbol,
            "date": day,
            "expiration": EXP,
            "strike": 100,
            "right": "call",
            "bid": bid,
            "ask": ask,
            "underlying_price": 100,
        }
        for symbol, q in (
            ("A", ((10, 11), (8, 9), (0, 0.05))),
            ("B", ((5, 6), (4, 5), (0, 0.05))),
        )
        for day, (bid, ask) in zip((OPEN, MID, EXP), q)
    ]


def pair():
    return {
        "id": "p1",
        "short_contracts": 1,
        "long_contracts": 1,
        "short_order": {
            "date": OPEN,
            "symbol": "A",
            "expiration": EXP,
            "strikes": [100],
            "structure": "short_call",
        },
        "long_order": {
            "date": OPEN,
            "symbol": "B",
            "expiration": EXP,
            "strikes": [100],
            "structure": "long_call",
        },
    }


def run(pairs, rows=None, **over):
    node = PairedOptionLedger("paired", {"ledger_params": LEDGER, **over})
    ctx = NodeContext(name="t", asof="2025-12-31", run_dir="/nonexistent")
    return node.run(ctx, {"pairs": pairs, "chain": chain() if rows is None else rows})


def test_two_nbbo_filled_and_settled_legs_are_one_trade():
    out = run([pair()])
    assert len(out["trades"]) == 1 and not out["refusals"]
    trade = out["trades"][0]
    assert trade["id"] == "p1"
    assert trade["pnl_usd"] == pytest.approx(398)  # +1000 -600 -two opening fees
    assert trade["fees_usd"] == pytest.approx(2)
    assert trade["short_trade"]["premium_usd"] == pytest.approx(1000)
    assert trade["long_trade"]["premium_usd"] == pytest.approx(-600)
    assert out["marks"][-1]["pnl_usd"] == pytest.approx(398)
    assert sum(
        c["cash_usd"] - c["fees_usd"] for c in out["cashflows"]
    ) == pytest.approx(398)


def test_one_missing_open_quote_refuses_entire_pair_without_partial_trade():
    rows = chain()
    for r in rows:
        if r["symbol"] == "B" and r["date"] == OPEN:
            r["ask"] = None
    out = run([pair()], rows)
    assert out["trades"] == out["marks"] == out["cashflows"] == []
    assert out["refusals"][0]["id"] == "p1"
    assert out["summary"]["pairs_refused"] == 1


def test_one_unsettled_leg_refuses_entire_pair():
    rows = chain()
    for r in rows:
        if r["symbol"] == "B" and r["date"] == EXP:
            r["underlying_price"] = None
    out = run([pair()], rows)
    assert out["trades"] == out["marks"] == out["cashflows"] == []
    assert out["refusals"][0]["status"] == "not_closed"


def test_two_contract_counts_scale_independently():
    order = pair()
    order["short_contracts"] = 2
    out = run([order])
    assert out["trades"][0]["pnl_usd"] == pytest.approx(1397)
    assert out["trades"][0]["fees_usd"] == pytest.approx(3)


def test_chain_iterable_is_read_once_and_unordered_contracts_are_not_retained():
    class Once:
        def __init__(self, data):
            self.data = data
            self.reads = 0

        def __iter__(self):
            self.reads += 1
            if self.reads != 1:
                raise AssertionError("chain read twice")
            return iter(self.data)

    data = chain()
    data += [{**data[0], "strike": 200 + i, "bid": 0.1, "ask": 0.2} for i in range(100)]
    source = Once(data)
    out = run([pair()], source)
    assert out["trades"][0]["pnl_usd"] == pytest.approx(398)
    assert source.reads == 1
    assert out["summary"]["chain_rows_seen"] == len(data)
    assert out["summary"]["chain_rows_retained"] < len(data) / 4


def test_per_pair_hedge_rate_changes_delegate_without_mutating_config():
    from datetime import date
    from dskit.pipeline.option_pricing import black76

    open_day, mark_day, expiry = "2020-01-02", "2020-01-03", "2020-02-03"
    rows = []
    for symbol, path in (("A", (100.0, 102.0, 100.0)), ("B", (100.0, 98.0, 100.0))):
        for day, spot in zip((open_day, mark_day, expiry), path):
            years = (
                max((date.fromisoformat(expiry) - date.fromisoformat(day)).days, 1)
                / 365
            )
            mid = black76("call", spot, 100.0, 0.30, years)
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "expiration": expiry,
                    "strike": 100,
                    "right": "call",
                    "bid": max(mid - 0.01, 0.001),
                    "ask": mid + 0.01,
                    "underlying_price": spot,
                }
            )
    order = pair()
    order["short_order"].update(date=open_day, expiration=expiry)
    order["long_order"].update(date=open_day, expiration=expiry)
    config = {
        **LEDGER,
        "hedge": {"rebalance": "daily", "rate": 0.0, "fee_per_share": 0.0},
    }
    default = run([order], rows, ledger_params=config)["trades"][0]["hedge_pnl_usd"]
    order["hedge_rate"] = 0.05
    override = run([order], rows, ledger_params=config)["trades"][0]["hedge_pnl_usd"]
    assert override != pytest.approx(default)
    assert config["hedge"]["rate"] == 0.0


def test_requested_exit_fallback_to_expiry_is_not_a_paired_exit():
    order = pair()
    order["short_order"]["exit"] = MID
    order["long_order"]["exit"] = MID
    rows = chain()
    for row in rows:
        if row["symbol"] == "B" and row["date"] == MID:
            row["ask"] = (
                None  # long exits by selling at bid, so refuse that side instead
            )
            row["bid"] = None
    cfg = {
        **LEDGER,
        "close_rule": "exit",
        "order_fields": {**ORDER_FIELDS, "exit": "exit"},
    }
    out = run([order], rows, ledger_params=cfg)
    assert out["trades"] == []
    assert out["refusals"][0]["status"] == "not_closed"
    assert out["summary"]["pairs_refused"] == 1


def test_stream_preserves_first_valid_underlying_even_on_unordered_contract():
    rows = chain()
    for row in rows:
        if row["symbol"] == "B" and row["date"] == EXP:
            row["underlying_price"] = None
    rows.append({**rows[-1], "strike": 300, "underlying_price": 100})
    out = run([pair()], rows)
    assert out["trades"][0]["pnl_usd"] == pytest.approx(398)
