"""A hand-computable two-day, two-instrument run shared by the evaluation suite.

Day 1 (2026-09-01, New York): deposit 10 000; buy 10 AAA @ 100.05 (fee 1);
mark 101; sell 4 @ 101 (fee 0.5) — a partial close; a refused and a
skipped decision and a hold; withdraw 500; mark 102; sell 6 @ 101.9
(fee 0.5). Day 2: short 5 BBB @ 50 (fee 0.25), mark 49, cover 5 @ 49.1
(fee 0.25).

Hand values: AAA trips +2.9 and +10.0, BBB trip +4.0; net 16.9, gross
19.4, fees 2.5; ending equity 10 000 - 500 + 16.9 = 9 516.9.
"""

import pytest

from dskit.evaluation.events import SCHEMA

#: 2026-09-01 14:30 UTC = 10:30 New York.
DAY1 = 1_788_273_000_000
DAY2 = DAY1 + 86_400_000
MIN = 60_000


class Builder:
    """Accumulate schema-v1 dicts with increasing seq."""

    def __init__(self):
        self.events = []

    def add(self, kind, ts_ms, instrument=None, known_ms=None, **fields):
        event = {"schema": SCHEMA, "seq": len(self.events), "kind": kind, "ts_ms": ts_ms,
                 "known_ms": ts_ms if known_ms is None else known_ms,
                 "instrument": instrument, **fields}
        self.events.append(event)
        return event


def candidates(best, other):
    return [{"instrument": best, "score": 0.002, "rank": 1, "eligible": True},
            {"instrument": other, "score": 0.001, "rank": 2, "eligible": True}]


def build_scenario(criteria=None, trials=3):
    b = Builder()
    b.add("run_start", 0, run_id="run-1", title="Synthetic", project="demo",
          tz="America/New_York", config_hash="abc", config={"lookback": 30},
          code={"commit": "deadbeef", "dirty": False}, data={"bars": "sha256:1"},
          env={"python": "3.12", "platform": "linux", "packages": {"dskit": "0"}},
          criteria=criteria if criteria is not None else [
              {"name": "positive trades", "stat": "trade_mean", "op": ">", "value": 0.0,
               "min_n": 1},
              {"name": "daily sharpe", "stat": "daily_sharpe", "op": ">", "value": 0.0,
               "min_n": 30},
          ],
          trials=trials)
    b.add("cashflow", DAY1, amount=10000, rule="initial", detail="seed capital")
    b.add("mark", DAY1, "AAA", price=100.0)
    b.add("mark", DAY1, "BBB", price=50.0)
    b.add("decision", DAY1, decision_id="d1", candidates=candidates("AAA", "BBB"),
          chosen="AAA", threshold=0.0005, edge=0.0015, action="enter",
          reason="edge_above_threshold", model="m1")
    b.add("order", DAY1, "AAA", order_id="o1", decision_id="d1", side="buy", qty=10,
          ref_price=100.0)
    b.add("fill", DAY1, "AAA", fill_id="f1", order_id="o1", side="buy", qty=10,
          price=100.05, fee=1.0, tag="entry")
    b.add("mark", DAY1 + MIN, "AAA", price=101.0)
    b.add("decision", DAY1 + MIN, decision_id="d2", chosen="AAA", action="exit",
          reason="take_profit")
    b.add("order", DAY1 + MIN, "AAA", order_id="o2", decision_id="d2", side="sell", qty=4,
          ref_price=101.0)
    b.add("fill", DAY1 + MIN, "AAA", fill_id="f2", order_id="o2", side="sell", qty=4,
          price=101.0, fee=0.5)
    b.add("decision", DAY1 + 2 * MIN, decision_id="d3", candidates=candidates("BBB", "AAA"),
          chosen="BBB", action="refuse", reason="max_exposure", edge=0.001)
    b.add("refusal", DAY1 + 2 * MIN, "BBB", decision_id="d3", reason="max_exposure",
          detail="gross exposure cap")
    b.add("decision", DAY1 + 3 * MIN, decision_id="d4", action="skip", reason="no_bar")
    b.add("skip", DAY1 + 3 * MIN, decision_id="d4", reason="no_bar")
    b.add("decision", DAY1 + 4 * MIN, decision_id="d5", action="hold", reason="below_threshold")
    b.add("cashflow", DAY1 + 5 * MIN, amount=-500, rule="withdraw")
    b.add("mark", DAY1 + 6 * MIN, "AAA", price=102.0)
    b.add("decision", DAY1 + 6 * MIN, decision_id="d6", chosen="AAA", action="exit",
          reason="close_out")
    b.add("order", DAY1 + 6 * MIN, "AAA", order_id="o3", decision_id="d6", side="sell", qty=6,
          ref_price=102.0)
    b.add("fill", DAY1 + 6 * MIN, "AAA", fill_id="f3", order_id="o3", side="sell", qty=6,
          price=101.9, fee=0.5)
    b.add("mark", DAY2, "BBB", price=50.0)
    b.add("decision", DAY2, decision_id="d7", candidates=candidates("BBB", "AAA"),
          chosen="BBB", action="enter", reason="short_edge", edge=0.002, threshold=0.0005)
    b.add("order", DAY2, "BBB", order_id="o4", decision_id="d7", side="sell", qty=5,
          ref_price=50.0)
    b.add("fill", DAY2, "BBB", fill_id="f4", order_id="o4", side="sell", qty=5, price=50.0,
          fee=0.25)
    b.add("mark", DAY2 + MIN, "BBB", price=49.0)
    b.add("decision", DAY2 + MIN, decision_id="d8", chosen="BBB", action="exit",
          reason="take_profit")
    b.add("order", DAY2 + MIN, "BBB", order_id="o5", decision_id="d8", side="buy", qty=5,
          ref_price=49.0)
    b.add("fill", DAY2 + MIN, "BBB", fill_id="f5", order_id="o5", side="buy", qty=5,
          price=49.1, fee=0.25)
    b.add("outcome", DAY2 + 2 * MIN, "BBB", decision_id="d7", horizon="1m", realized=0.02)
    b.add("run_end", DAY2 + 2 * MIN, status="ok", wall_s=1.5)
    return b.events


@pytest.fixture
def scenario():
    return build_scenario()
