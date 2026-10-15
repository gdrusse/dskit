"""ChainStructureOrders (ADR-0265 Amendment 5): strikes and expiries chosen from a daily chain, orders out.

Chains are priced by Black-76 on a known surface, so every expectation (the ATM strike, the strike whose
delta is nearest a target, the max-open-interest strike, the mirror strike) is derived here independently of
the node. The orders must also be accepted by OptionStructureLedger unchanged.
"""

import math
from datetime import date, timedelta

import pytest

pytest.importorskip("pandas")

from dskit.pipeline.libs.chain_orders import ChainStructureOrders
from dskit.pipeline.libs.option_ledger import OptionStructureLedger
from dskit.pipeline.node import ConfigError, NodeContext
from dskit.pipeline.option_pricing import black76, black76_delta

D0 = date(2020, 3, 2)          # a Monday
CHAIN_FIELDS = {"symbol": "symbol", "date": "date", "expiration": "expiration", "strike": "strike",
                "right": "right", "bid": "bid", "ask": "ask", "underlying": "underlying_price",
                "open_interest": "open_interest"}
ORDER_FIELDS = {"date": "open_date", "symbol": "symbol", "expiration": "expiration", "strikes": "strikes",
                "structure": "structure"}
PUT_WRITE = {"legs": [{"right": "put", "quantity": -1, "select": {"kind": "atm"}}],
             "expiry": {"dte": 30, "tolerance": 7, "min_dte": 1}, "entry": {"kind": "after_expiry"}}
BASE = {"chain_fields": CHAIN_FIELDS, "order_fields": ORDER_FIELDS, "call_values": ["call"],
        "put_values": ["put"], "end_before": "2026-01-01", "start": "2020-01-01", "structures": {"put_write": PUT_WRITE}}


def iso(day):
    return day.isoformat()


def day_rows(symbol, day, expiries, spot=100.0, vol=0.30, strikes=range(70, 131), half=0.01, oi=None):
    rows = []
    for exp in expiries:
        years = max((exp - day).days, 0.001) / 365
        for k in strikes:
            for right in ("call", "put"):
                mid = black76(right, spot, float(k), vol, years)
                rows.append({"symbol": symbol, "date": iso(day), "expiration": iso(exp), "strike": float(k),
                             "right": right, "bid": mid - half, "ask": mid + half, "underlying_price": spot,
                             "open_interest": (oi or (lambda *_: 10))(exp, k, right)})
    return rows


def sessions(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def run(rows, **over):
    ctx = NodeContext(name="t", asof="2025-12-31", run_dir="/nonexistent")
    params = {**BASE, **over}
    return ChainStructureOrders("orders", params).run(ctx, {"records": rows})


def test_the_atm_leg_is_the_listed_strike_nearest_the_forward_and_the_expiry_the_nearest_to_target():
    rows = day_rows("AAA", D0, [D0 + timedelta(days=9), D0 + timedelta(days=31), D0 + timedelta(days=60)], spot=101.4)
    out = run(rows)
    order = out["records"][0]
    assert order["strikes"] == [101.0] and order["expiration"] == iso(D0 + timedelta(days=31))
    assert (order["open_date"], order["symbol"], order["structure"]) == (iso(D0), "AAA", "put_write")
    assert order["spot"] == 101.4 and order["forward"] == pytest.approx(101.4, rel=1e-6) and order["dte"] == 31


def test_a_delta_leg_takes_the_otm_strike_whose_forward_delta_is_nearest_the_target():
    spec = {"legs": [{"right": "put", "quantity": -1, "select": {"kind": "delta", "target": 0.16}},
                     {"right": "call", "quantity": -1, "select": {"kind": "delta", "target": 0.16}}],
            "expiry": {"dte": 1, "tolerance": 2, "min_dte": 1}, "entry": {"kind": "after_expiry"}}
    exp = D0 + timedelta(days=1)
    out = run(day_rows("AAA", D0, [exp], vol=0.20), structures={"strangle": spec})
    years = 1 / 365

    def nearest(right):
        ks = [k for k in range(70, 131) if (k < 100 if right == "put" else k > 100)]
        return min(ks, key=lambda k: (abs(abs(black76_delta(right, 100.0, float(k), 0.20, years)) - 0.16), k))
    assert out["records"][0]["strikes"] == [float(nearest("put")), float(nearest("call"))]
    assert nearest("put") < 100 < nearest("call")


def test_max_open_interest_and_its_mirror_strike():
    exp = D0 + timedelta(days=7)
    spec = {"legs": [{"right": "call", "quantity": 1, "select": {"kind": "max_oi"}},
                     {"right": "put", "quantity": 1, "select": {"kind": "mirror", "of": 0}}],
            "entry": {"kind": "days_before_expiry", "days": 7}}
    rows = day_rows("AAA", D0, [exp], spot=100.4, oi=lambda e, k, r: 1000 if (k == 104 and r == "call") else 20)
    out = run(rows, structures={"pin": spec})
    assert out["records"][0]["strikes"] == [104.0, 97.0]        # 2*100.4 - 104 = 96.8 -> nearest listed strike 97
    assert out["records"][0]["expiration"] == iso(exp)
    tie = day_rows("AAA", D0, [exp], oi=lambda e, k, r: 500 if k in (96, 103) else 20)
    assert run(tie, structures={"pin": spec})["records"][0]["strikes"][0] == 96.0


def test_after_expiry_opens_the_next_order_on_or_after_the_prior_expiration():
    exp1, exp2 = D0 + timedelta(days=30), D0 + timedelta(days=58)
    rows = []
    for d in sessions(D0, 45):
        rows += day_rows("AAA", d, [exp for exp in (exp1, exp2, D0 + timedelta(days=90)) if exp > d],
                         strikes=range(95, 106))
    out = run(rows)
    assert out["records"][0]["open_date"] == iso(D0) and out["records"][0]["expiration"] == iso(exp1)
    assert out["records"][1]["open_date"] >= iso(exp1) and all(
        a["expiration"] <= b["open_date"] for a, b in zip(out["records"], out["records"][1:]))
    assert all(o["expiration"] > o["open_date"] for o in out["records"])


def test_every_n_takes_the_first_session_and_then_each_nth():
    spec = {**PUT_WRITE, "entry": {"kind": "every_n", "n": 3}}
    rows = []
    for d in sessions(D0, 9):
        rows += day_rows("AAA", d, [D0 + timedelta(days=34)], strikes=range(95, 106))
    out = run(rows, structures={"s": spec})
    assert [o["open_date"] for o in out["records"]] == [iso(d) for d in sessions(D0, 9)[::3]]


def test_days_before_expiry_with_a_third_friday_rule_orders_only_standard_monthlies():
    third_fri, weekly = date(2020, 3, 20), date(2020, 3, 27)
    spec = {"legs": [{"right": "call", "quantity": 1, "select": {"kind": "max_oi"}}],
            "entry": {"kind": "days_before_expiry", "days": 7},
            "expiry_rule": {"weekday": 4, "day_of_month": [15, 21]}}
    rows = []
    for d in sessions(date(2020, 3, 9), 15):
        rows += day_rows("AAA", d, [e for e in (third_fri, weekly) if e > d], strikes=range(95, 106))
    out = run(rows, structures={"pin": spec})
    assert [(o["open_date"], o["expiration"]) for o in out["records"]] == [("2020-03-13", "2020-03-20")]


def test_a_structure_can_be_limited_to_named_symbols():
    spec = {**PUT_WRITE, "symbols": ["BBB"]}
    rows = day_rows("AAA", D0, [D0 + timedelta(days=30)]) + day_rows("BBB", D0, [D0 + timedelta(days=30)])
    assert [o["symbol"] for o in run(rows, structures={"s": spec})["records"]] == ["BBB"]


def test_nothing_is_ordered_before_start_and_refusals_are_counted_by_reason():
    rows = day_rows("AAA", D0, [D0 + timedelta(days=30)])
    out = run(rows, start="2020-03-03")
    assert out["records"] == [] and out["summary"]["before_start_days"] == 1
    far = day_rows("AAA", D0, [D0 + timedelta(days=200)])
    assert run(far)["summary"]["refused"] == {"put_write:no_expiry": 1}
    no_pairs = [r for r in day_rows("AAA", D0, [D0 + timedelta(days=30)]) if r["strike"] in (100.0, 101.0) and r["right"] == "put"]
    assert run(no_pairs)["summary"]["refused"] == {"put_write:no_forward": 1}


def test_unsorted_dates_and_a_reappearing_symbol_refuse_and_so_does_a_row_at_end_before():
    later, earlier = D0 + timedelta(days=1), D0
    rows = day_rows("AAA", later, [D0 + timedelta(days=30)], strikes=range(98, 103)) + \
        day_rows("AAA", earlier, [D0 + timedelta(days=30)], strikes=range(98, 103))
    with pytest.raises(ValueError, match="backwards"):
        run(rows)
    rows = day_rows("AAA", D0, [D0 + timedelta(days=30)], strikes=range(98, 103)) + \
        day_rows("BBB", D0, [D0 + timedelta(days=30)], strikes=range(98, 103)) + \
        day_rows("AAA", D0 + timedelta(days=1), [D0 + timedelta(days=30)], strikes=range(98, 103))
    with pytest.raises(ValueError, match="reappear"):
        run(rows)
    with pytest.raises(ValueError, match="end_before"):
        run(day_rows("AAA", D0, [D0 + timedelta(days=30)], strikes=range(98, 103)), end_before="2020-03-02")


def test_the_orders_feed_the_ledger_unchanged_and_the_put_write_settles_at_intrinsic():
    exp = D0 + timedelta(days=14)
    rows = []
    for i, d in enumerate(sessions(D0, 11)):
        spot = 100.0 if d < exp else 96.0
        rows += day_rows("AAA", d, [exp], spot=spot, strikes=range(90, 111))
    exp_rows = [r for r in rows if r["date"] == iso(exp)]
    assert exp_rows                                                       # the expiry date itself is in the chain
    short = {**PUT_WRITE, "expiry": {"dte": 14, "tolerance": 2, "min_dte": 1}}
    orders = run(rows, structures={"put_write": short})["records"]
    assert len(orders) == 1
    ledger = OptionStructureLedger("ledger", {
        "chain_fields": {k: v for k, v in CHAIN_FIELDS.items() if k != "open_interest"}, "order_fields": ORDER_FIELDS,
        "structures": {"put_write": [["put", -1]]}, "multiplier": 100})
    trades = ledger.run(NodeContext(name="l", asof="2025-12-31", run_dir="/x"), {"chain": rows, "orders": orders})["trades"]
    t = trades[0]
    assert t["structure"] == "put_write" and t["status"] == "closed" and t["strikes"] == [100.0]
    assert t["pnl_usd"] == pytest.approx(t["premium_usd"] - 400.0)       # short 100 put, settled at 96


@pytest.mark.parametrize("bad, text", [
    ({"structures": {}}, "structures"),
    ({"structures": {"s": {**PUT_WRITE, "entry": {"kind": "daily"}}}}, "entry.kind"),
    ({"structures": {"s": {**PUT_WRITE, "legs": [{"right": "put", "quantity": 0, "select": {"kind": "atm"}}]}}}, "quantity"),
    ({"structures": {"s": {**PUT_WRITE, "legs": [{"right": "put", "quantity": 1, "select": {"kind": "delta"}}]}}}, "target"),
    ({"structures": {"s": {**PUT_WRITE, "legs": [{"right": "put", "quantity": 1, "select": {"kind": "mirror", "of": 0}}]}}}, "earlier leg"),
    ({"structures": {"s": {**PUT_WRITE, "expiry": {"dte": 30}}}}, "tolerance"),
    ({"structures": {"s": {"legs": PUT_WRITE["legs"], "entry": {"kind": "days_before_expiry", "days": 7},
                           "expiry": PUT_WRITE["expiry"]}}}, "expiry"),
    ({"structures": {"s": {**PUT_WRITE, "entry": {"kind": "every_n", "n": 0}}}}, "n"),
    ({"structures": {"s": {**PUT_WRITE, "expiry_rule": {"weekday": 9}}}}, "weekday"),
    ({"start": "2020-13-01"}, "start"), ({"rate": "x"}, "rate"), ({"zzz": 1}, "zzz")])
def test_bad_declarations_are_refused_by_name(bad, text):
    problems = ChainStructureOrders.validate_params({**BASE, **bad})
    assert any(text in p for p in problems), problems


def test_a_bad_declaration_refuses_at_construction_and_the_open_interest_field_is_needed_for_max_oi():
    with pytest.raises(ConfigError):
        ChainStructureOrders("o", {**BASE, "start": "nope"})
    no_oi = {k: v for k, v in CHAIN_FIELDS.items() if k != "open_interest"}
    spec = {"legs": [{"right": "call", "quantity": 1, "select": {"kind": "max_oi"}}], "entry": {"kind": "after_expiry"},
            "expiry": PUT_WRITE["expiry"]}
    assert any("open_interest" in p for p in ChainStructureOrders.validate_params(
        {**BASE, "chain_fields": no_oi, "structures": {"s": spec}}))


def test_the_node_is_deterministic():
    rows = day_rows("AAA", D0, [D0 + timedelta(days=30)])
    assert run(rows)["records"] == run(rows)["records"]
    assert not math.isnan(run(rows)["records"][0]["forward"])


def test_max_open_interest_skips_a_strike_whose_leg_has_no_usable_quote_and_a_duplicate_row_refuses():
    exp = D0 + timedelta(days=7)
    rows = day_rows("AAA", D0, [exp], strikes=range(95, 106), oi=lambda e, k, r: 9000 if k == 110 else (500 if k == 103 else 20))
    rows += [dict(r, strike=110.0, bid=0.0) for r in day_rows("AAA", D0, [exp], strikes=[110], oi=lambda *_: 9000)]
    spec = {"legs": [{"right": "put", "quantity": 1, "select": {"kind": "max_oi"}}], "entry": {"kind": "days_before_expiry", "days": 7}}
    assert run(rows, structures={"pin": spec})["records"][0]["strikes"] == [103.0]
    with pytest.raises(ValueError, match="repeats"):
        run(rows + rows[:1], structures={"pin": spec})


def test_a_days_before_expiry_entry_date_with_no_chain_row_is_counted_not_skipped_silently():
    third_fri = date(2020, 3, 20)
    spec = {"legs": [{"right": "call", "quantity": 1, "select": {"kind": "max_oi"}}],
            "entry": {"kind": "days_before_expiry", "days": 7}, "expiry_rule": {"weekday": 4, "day_of_month": [15, 21]}}
    days = [d for d in sessions(date(2020, 3, 9), 15) if d != date(2020, 3, 13)]          # the entry day is missing
    rows = []
    for d in days:
        rows += day_rows("AAA", d, [third_fri], strikes=range(95, 106))
    out = run(rows, structures={"pin": spec})
    assert out["records"] == [] and out["summary"]["refused"] == {"pin:no_entry_row": 1}
    ok = [r for r in rows] + day_rows("AAA", date(2020, 3, 13), [third_fri], strikes=range(95, 106))
    ok.sort(key=lambda r: r["date"])
    assert len(run(ok, structures={"pin": spec})["records"]) == 1
