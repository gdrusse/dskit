"""TaxLots and LotPositions (ADR-0279, research A0004): every number here is worked by hand.

Trade rows become open lots by a declared method (FIFO now); the lots are joined to a
positions snapshot, reconciled share for share, and each becomes a position row with
its own acquisition date, so LiquidationTax classes every lot by its own holding period.
"""

import re

import pytest

from dskit.pipeline import tax_lots
from dskit.pipeline.liquidation import LiquidationTax
from dskit.pipeline.node import ConfigError, NodeContext
from dskit.pipeline.tax_lots import (
    AGGREGATE_LOT,
    LOT_METHODS,
    QUANTITY_RTOL,
    FifoOrder,
    LotOrder,
    LotPositions,
    TaxLots,
    format_lot_id,
)


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="lots", asof="2025-10-27", run_dir=str(tmp_path))


LOTS = {
    "method": "fifo",
    "key_fields": ["account", "symbol"],
    "date_field": "trade_date",
    "quantity_field": "quantity",
    "price_field": "price",
    "amount_field": "amount",
    "order_field": "transaction_id",
    "allow_incomplete": False,
}


def _trade(tid, day, quantity, price, symbol="VTI", account="A1", amount=None):
    return {"account": account, "symbol": symbol, "transaction_id": tid, "trade_date": day,
            "quantity": quantity, "price": price, "amount": amount}


# two buys and a partial sell: FIFO takes the 4 shares from the 2024 lot
TWO_BUYS_ONE_SELL = [
    _trade(1, "2024-01-10", 10.0, 100.0),      # lot 1: 10 sh, basis 1000
    _trade(2, "2025-03-01", 5.0, 120.0),       # lot 2: 5 sh, basis 600
    _trade(3, "2025-06-01", -4.0, 130.0),      # FIFO: lot 1 -> 6 sh, basis 1000 x 6/10 = 600
]


def _lots(ctx, rows, **over):
    return TaxLots("lots", {**LOTS, **over}).run(ctx, {"records": rows})


def _brief(records):
    return [(r["lot_id"], r["acquired"], r["quantity"], r["basis"]) for r in records]


def test_two_buys_and_a_partial_sell_fifo_by_hand(ctx):
    out = _lots(ctx, TWO_BUYS_ONE_SELL)
    assert _brief(out["records"]) == [
        ("A1:VTI:2024-01-10:1", "2024-01-10", 6.0, 600.0),
        ("A1:VTI:2025-03-01:1", "2025-03-01", 5.0, 600.0)]
    first, second = out["records"]
    assert (first["account"], first["symbol"]) == ("A1", "VTI")
    assert first["basis_per_share"] == 100.0 and second["basis_per_share"] == 120.0
    summary = out["summary"]
    assert (summary["method"], summary["as_of"], summary["trades"]) == ("fifo", "2025-10-27", 3)
    assert (summary["lots"], summary["keys"], summary["closed"]) == (2, 1, 0)
    assert summary["incomplete"] == [] and summary["after_as_of"] == 0


def test_a_sell_across_lots_closes_the_first_and_splits_the_second(ctx):
    rows = TWO_BUYS_ONE_SELL[:2] + [_trade(3, "2025-06-01", -12.0, 130.0)]
    out = _lots(ctx, rows)
    # lot 1 gives all 10, lot 2 gives 2 of its 5: 3 left at 120 a share = 360
    assert _brief(out["records"]) == [("A1:VTI:2025-03-01:1", "2025-03-01", 3.0, 360.0)]
    assert out["records"][0]["basis_per_share"] == 120.0


def test_selling_everything_closes_the_key(ctx):
    rows = TWO_BUYS_ONE_SELL[:2] + [_trade(3, "2025-06-01", -15.0, 130.0)]
    out = _lots(ctx, rows)
    assert out["records"] == []
    assert (out["summary"]["lots"], out["summary"]["keys"], out["summary"]["closed"]) == (0, 0, 1)


def test_a_fractional_reinvestment_is_a_lot_and_its_cash_is_its_basis(ctx):
    rows = [_trade(1, "2024-01-10", 10.0, 100.0, amount=-1000.0),
            # a dividend reinvestment: 0.1234 sh at 101.30 for 12.50 of cash (the amount wins)
            _trade(2, "2024-03-15", 0.1234, 101.3, amount=-12.5),
            _trade(3, "2025-01-02", -10.0, 110.0, amount=1100.0)]
    out = _lots(ctx, rows)
    assert _brief(out["records"]) == [("A1:VTI:2024-03-15:1", "2024-03-15", 0.1234, 12.5)]
    assert out["records"][0]["basis_per_share"] == pytest.approx(12.5 / 0.1234)


def test_float_noise_in_fractional_shares_is_not_a_share(ctx):
    # 0.1 + 0.2 is 0.30000000000000004 in floats; selling 0.3 closes both lots, oversells nothing
    rows = [_trade(1, "2024-01-10", 0.1, 100.0), _trade(2, "2024-01-11", 0.2, 100.0),
            _trade(3, "2024-02-01", -0.3, 100.0)]
    out = _lots(ctx, rows)
    assert out["records"] == [] and out["summary"]["incomplete"] == []
    # a real oversell, far above the noise, still refuses
    rows[2] = _trade(3, "2024-02-01", -0.3 - 1e-6, 100.0)
    with pytest.raises(ValueError, match="oversold"):
        _lots(ctx, rows)
    assert QUANTITY_RTOL == 1e-9


def test_the_amount_is_the_basis_when_declared_and_present(ctx):
    rows = [_trade(1, "2024-01-10", 10.0, 100.0, amount=-1001.0),   # commission in the basis
            _trade(2, "2024-01-11", 1.0, 100.0)]                     # null: quantity x price
    assert [r["basis"] for r in _lots(ctx, rows)["records"]] == [1001.0, 100.0]
    # without amount_field the amount column is not read at all
    no_amount = {k: v for k, v in LOTS.items() if k != "amount_field"}
    out = TaxLots("lots", no_amount).run(ctx, {"records": rows})
    assert [r["basis"] for r in out["records"]] == [1000.0, 100.0]
    # a buy that RECEIVED cash is not a purchase this node understands
    with pytest.raises(ValueError, match=r"lots: trade 0 buys 10.0 'A1:VTI' but its 'amount' "
                                         r"is 5.0; a buy pays cash"):
        _lots(ctx, [_trade(1, "2024-01-10", 10.0, 100.0, amount=5.0)])
    with pytest.raises(ValueError, match="its 'amount' is 0.0; a buy pays cash"):
        _lots(ctx, [_trade(1, "2024-01-10", 10.0, 100.0, amount=0.0)])
    # a sell's amount is never read: its proceeds are not a lot's concern
    out = _lots(ctx, TWO_BUYS_ONE_SELL[:2] + [_trade(3, "2025-06-01", -4.0, 130.0, amount="x")])
    assert out["summary"]["lots"] == 2


def test_trades_after_as_of_are_counted_not_used(ctx):
    out = _lots(ctx, TWO_BUYS_ONE_SELL, as_of="2025-04-01")
    assert _brief(out["records"]) == [
        ("A1:VTI:2024-01-10:1", "2024-01-10", 10.0, 1000.0),
        ("A1:VTI:2025-03-01:1", "2025-03-01", 5.0, 600.0)]
    assert (out["summary"]["as_of"], out["summary"]["after_as_of"]) == ("2025-04-01", 1)
    # absent, as_of is the run's: ctx.asof 2025-10-27 keeps the sell
    assert _lots(ctx, TWO_BUYS_ONE_SELL)["summary"]["after_as_of"] == 0
    early = NodeContext(name="lots", asof="2025-04-01", run_dir=ctx.run_dir)
    assert _lots(early, TWO_BUYS_ONE_SELL)["summary"]["after_as_of"] == 1


def test_same_day_trades_follow_the_order_field_and_buys_are_numbered(ctx):
    # input order puts the sell first; the order field puts the 9:30 buy first
    rows = [_trade(12, "2025-06-01", -4.0, 130.0), _trade(11, "2025-06-01", 10.0, 128.0),
            _trade(13, "2025-06-01", 2.0, 131.0)]
    out = _lots(ctx, rows)
    assert _brief(out["records"]) == [
        ("A1:VTI:2025-06-01:1", "2025-06-01", 6.0, 768.0),       # 10 x 128 = 1280, 6/10 left
        ("A1:VTI:2025-06-01:2", "2025-06-01", 2.0, 262.0)]
    # without an order field the input order rules, and the sell finds nothing open
    no_order = {k: v for k, v in LOTS.items() if k != "order_field"}
    with pytest.raises(ValueError, match="oversold"):
        TaxLots("lots", no_order).run(ctx, {"records": rows})


def test_an_oversold_key_refuses_unless_incomplete_history_is_allowed(ctx):
    rows = TWO_BUYS_ONE_SELL + [
        _trade(4, "2025-01-02", -3.0, 150.0, symbol="AAPL"),     # bought before the history
        _trade(5, "2025-02-01", 3.0, 150.0, symbol="AAPL")]
    with pytest.raises(ValueError) as caught:
        _lots(ctx, rows)
    assert str(caught.value) == (
        "lots: trade 3 sells 3.0 of 'A1:AAPL' on 2025-01-02 but 0.0 share(s) are open "
        "(oversold by 3.0): history before the trades' start, a transfer in or a short "
        "sale; declare allow_incomplete to withhold the key's lots instead")
    out = _lots(ctx, rows, allow_incomplete=True)
    # VTI is untouched; every AAPL lot is withheld, the later buy included
    assert [r["symbol"] for r in out["records"]] == ["VTI", "VTI"]
    assert out["summary"]["incomplete"] == [{
        "account": "A1", "symbol": "AAPL",
        "reason": "sells 3.0 on 2025-01-02 with 0.0 open (oversold by 3.0)"}]
    assert out["summary"]["ignored"] == 1


def test_a_partial_oversell_is_incomplete_too(ctx):
    rows = [_trade(1, "2024-01-10", 2.0, 100.0), _trade(2, "2024-05-01", -3.0, 100.0)]
    out = _lots(ctx, rows, allow_incomplete=True)
    assert out["records"] == []
    assert out["summary"]["incomplete"][0]["reason"] == (
        "sells 3.0 on 2024-05-01 with 2.0 open (oversold by 1.0)")


class HighestCostFirst(LotOrder):
    """A test-only HIFO: proves a method is one LotOrder and one LOT_METHODS entry."""

    def order(self, lots):
        return sorted(lots, key=lambda lot: -lot.basis / lot.quantity)


def test_a_method_is_one_lot_order_and_one_registry_entry(ctx, monkeypatch):
    assert LOT_METHODS == {"fifo": FifoOrder}
    monkeypatch.setitem(LOT_METHODS, "hifo", HighestCostFirst)
    out = _lots(ctx, TWO_BUYS_ONE_SELL, method="hifo")
    # the sell of 4 takes the 120 lot first: 1 share (120) left, the 2024 lot whole
    assert _brief(out["records"]) == [
        ("A1:VTI:2024-01-10:1", "2024-01-10", 10.0, 1000.0),
        ("A1:VTI:2025-03-01:1", "2025-03-01", 1.0, 120.0)]


def test_format_lot_id_is_the_one_spelling():
    assert format_lot_id(("A1", "VTI"), "2024-01-10:1") == "A1:VTI:2024-01-10:1"
    assert format_lot_id(("A1", 7), AGGREGATE_LOT) == "A1:7:aggregate"
    assert "format_lot_id" in tax_lots.__all__


ROW_REFUSALS = [
    ({"account": None}, "trade 0 key \\['account', 'symbol'\\] is unusable"),
    ({"symbol": ["VTI"]}, "trade 0 key \\['account', 'symbol'\\] is unusable"),
    ({"trade_date": "Jan 10"}, "trade 0 field 'trade_date' is not an ISO date"),
    ({"trade_date": None}, "trade 0 field 'trade_date' is not an ISO date"),
    ({"quantity": 0.0}, "trade 0 field 'quantity' is zero; a trade moves shares"),
    ({"quantity": "10"}, "trade 0 field 'quantity' is not a number"),
    ({"quantity": True}, "trade 0 field 'quantity' is not a number"),
    ({"price": -1.0}, "trade 0 field 'price' is negative"),
    ({"price": None}, "trade 0 field 'price' is not a number"),
    ({"amount": "cash"}, "trade 0 field 'amount' is not a number"),
    ({"transaction_id": None}, "trade 0 field 'transaction_id' is unusable"),
]


@pytest.mark.parametrize("over, match", ROW_REFUSALS)
def test_an_unusable_trade_refuses_by_index_and_field(ctx, over, match):
    with pytest.raises(ValueError, match=match):
        _lots(ctx, [{**_trade(1, "2024-01-10", 10.0, 100.0), **over}])


def test_records_must_be_a_list_of_mappings_and_order_values_one_type(ctx):
    with pytest.raises(ValueError, match="records must be a list"):
        _lots(ctx, None)
    with pytest.raises(ValueError, match="row 1 must be a mapping"):
        _lots(ctx, [TWO_BUYS_ONE_SELL[0], "VTI"])
    rows = [TWO_BUYS_ONE_SELL[0], {**TWO_BUYS_ONE_SELL[1], "transaction_id": "two"}]
    with pytest.raises(ValueError, match="'transaction_id' values mix types"):
        _lots(ctx, rows)


LOTS_REQUIRED = ("method", "key_fields", "date_field", "quantity_field", "price_field",
                 "allow_incomplete")
BAD_LOTS = [
    ({"surprise": 1}, "unknown"),
    ({"as_of": "today"}, "as_of must be an ISO date"),
    ({"method": "hifo"}, r"method must be one of \['fifo'\]"),
    ({"method": None}, r"method must be one of \['fifo'\]"),
    ({"key_fields": []}, "key_fields must be a non-empty list of distinct field names"),
    ({"key_fields": ["account", "quantity"]}, "key_fields may not name an output field"),
    *[({name: bad}, f"{name} must be a non-empty field name")
      for name in ("date_field", "quantity_field", "price_field", "amount_field", "order_field")
      for bad in ("", 5, None)],
    ({"allow_incomplete": "no"}, "allow_incomplete must be a bool"),
    ({"allow_incomplete": 0}, "allow_incomplete must be a bool"),
]


@pytest.mark.parametrize("over, match", BAD_LOTS)
def test_each_bad_lots_declaration_is_one_named_problem(over, match):
    problems = TaxLots.validate_params({**LOTS, **over})
    assert len(problems) == 1 and re.search(match, problems[0]), problems


@pytest.mark.parametrize("name", LOTS_REQUIRED)
def test_each_lots_param_is_named_when_missing(name):
    problems = TaxLots.validate_params({k: v for k, v in LOTS.items() if k != name})
    assert problems == [f"missing required params ['{name}']"]


def test_lots_declarations_accumulate():
    with pytest.raises(ConfigError) as caught:
        TaxLots("lots", {**LOTS, "method": "lifo", "allow_incomplete": 1, "surprise": 1})
    assert len(caught.value.errors) == 3


# ---- LotPositions ---------------------------------------------------------------------------

JOIN = {
    "key_fields": ["account", "symbol"],
    "quantity_field": "long_quantity",
    "value_field": "market_value",
    "basis_field": "cost_basis",
    "carry_fields": ["as_of", "asset_type"],
    "allow_incomplete": False,
}


def _position(symbol="VTI", quantity=11.0, value=1650.0, basis=1200.0, account="A1"):
    return {"account": account, "symbol": symbol, "as_of": "2025-10-27", "asset_type": "EQUITY",
            "long_quantity": quantity, "short_quantity": 0.0, "average_price": basis / quantity,
            "cost_basis": basis, "market_value": value}


def _join(ctx, lots, positions, **over):
    return LotPositions("lot_positions", {**JOIN, **over}).run(
        ctx, {"lots": lots, "positions": positions})


def test_each_lot_becomes_a_position_row_valued_pro_rata(ctx):
    lots = _lots(ctx, TWO_BUYS_ONE_SELL)["records"]
    out = _join(ctx, lots, [_position()])
    # 1650 / 11 = 150 a share: 6 x 150 = 900 and 5 x 150 = 750; basis is the lot's own
    assert out["records"] == [
        {"account": "A1", "symbol": "VTI", "as_of": "2025-10-27", "asset_type": "EQUITY",
         "lot_id": "A1:VTI:2024-01-10:1", "acquired": "2024-01-10",
         "long_quantity": 6.0, "market_value": 900.0, "cost_basis": 600.0},
        {"account": "A1", "symbol": "VTI", "as_of": "2025-10-27", "asset_type": "EQUITY",
         "lot_id": "A1:VTI:2025-03-01:1", "acquired": "2025-03-01",
         "long_quantity": 5.0, "market_value": 750.0, "cost_basis": 600.0}]
    summary = out["summary"]
    assert (summary["positions"], summary["lots"]) == (1, 2)
    assert summary["fallback"] == [] and summary["unpositioned"] == []
    assert summary["reconciled"] == [{"account": "A1", "symbol": "VTI", "lots": 2,
                                      "quantity": 11.0, "lot_basis": 1200.0,
                                      "position_basis": 1200.0, "basis_gap": 0.0}]


def test_each_lot_gets_its_own_holding_class_and_tax_by_hand(ctx):
    """The point of the lots: the 2024 lot is long (.2 of 300), the 2025 lot short (.4 of 150).
    Tax 60 + 60 = 120; the aggregate row, all long by default, would have paid 450 x .2 = 90."""
    lots = _lots(ctx, TWO_BUYS_ONE_SELL)["records"]
    rows = _join(ctx, lots, [_position()])["records"]
    tax = LiquidationTax("tax", {
        "as_of": "2025-10-27", "short_term_rate": 0.4, "long_term_rate": 0.2,
        "long_term_days": 366, "default_holding": "long", "loss_offset": False,
        "value_field": "market_value", "basis_field": "cost_basis",
        "acquired_field": "acquired"}).run(ctx, {"records": rows})
    old, young = tax["records"]
    assert (old["liq_holding"], old["liq_gain"], old["liq_tax"], old["liq_net"]) == (
        "long", 300.0, 60.0, 840.0)
    assert (young["liq_holding"], young["liq_gain"], young["liq_tax"], young["liq_net"]) == (
        "short", 150.0, 60.0, 690.0)
    assert tax["summary"]["tax"] == 120.0
    assert old["lot_id"] == "A1:VTI:2024-01-10:1"


def test_a_reconciliation_mismatch_refuses(ctx):
    lots = _lots(ctx, TWO_BUYS_ONE_SELL)["records"]
    with pytest.raises(ValueError) as caught:
        _join(ctx, lots, [_position(quantity=12.0, value=1800.0)])
    assert str(caught.value) == (
        "lot_positions: position 0 ('A1', 'VTI') holds 12.0 share(s) but its lots hold 11.0 "
        "(difference -1.0, tolerance 0.0): the lot history is incomplete (a purchase before "
        "the trades' start, a transfer in, a split or a reinvestment the pull did not cover); "
        "declare allow_incomplete to fall back to the aggregate row")
    with pytest.raises(ValueError, match=r"position 0 \('A1', 'AAPL'\) has no lots"):
        _join(ctx, lots, [_position(symbol="AAPL", quantity=3.0, value=540.0, basis=450.0)])


def test_the_tolerance_admits_a_fractional_difference_and_nothing_more(ctx):
    rows = [_trade(1, "2024-01-10", 10.0, 100.0), _trade(2, "2024-03-15", 0.0012, 101.0)]
    lots = _lots(ctx, rows)["records"]
    position = _position(quantity=10.001, value=1500.15, basis=1000.12)
    with pytest.raises(ValueError, match="difference 0.0002"):
        _join(ctx, lots, [position])
    out = _join(ctx, lots, [position], tolerance=0.001)
    # 1500.15 / 10.001 = 150 a share, each lot valued at its own quantity
    assert [r["market_value"] for r in out["records"]] == [pytest.approx(1500.0),
                                                           pytest.approx(0.18)]
    with pytest.raises(ValueError, match="difference"):
        _join(ctx, lots, [_position(quantity=10.0025, value=1500.375)], tolerance=0.001)


def test_float_noise_reconciles_at_zero_tolerance(ctx):
    # lots of 0.1 and 0.2 sum to 0.30000000000000004: the same 0.3 shares the snapshot holds
    lots = _lots(ctx, [_trade(1, "2024-01-10", 0.1, 100.0),
                       _trade(2, "2024-01-11", 0.2, 100.0)])["records"]
    out = _join(ctx, lots, [_position(quantity=0.3, value=45.0, basis=30.0)])
    assert [r["market_value"] for r in out["records"]] == [pytest.approx(15.0),
                                                           pytest.approx(30.0)]


def test_allow_incomplete_falls_back_to_the_aggregate_row(ctx):
    lots = _lots(ctx, TWO_BUYS_ONE_SELL)["records"]
    positions = [_position(quantity=12.0, value=1800.0, basis=1300.0),
                 _position(symbol="AAPL", quantity=3.0, value=540.0, basis=450.0)]
    out = _join(ctx, lots, positions, allow_incomplete=True)
    assert out["records"] == [
        {"account": "A1", "symbol": "VTI", "as_of": "2025-10-27", "asset_type": "EQUITY",
         "lot_id": "A1:VTI:aggregate", "acquired": None,
         "long_quantity": 12.0, "market_value": 1800.0, "cost_basis": 1300.0},
        {"account": "A1", "symbol": "AAPL", "as_of": "2025-10-27", "asset_type": "EQUITY",
         "lot_id": "A1:AAPL:aggregate", "acquired": None,
         "long_quantity": 3.0, "market_value": 540.0, "cost_basis": 450.0}]
    assert out["summary"]["fallback"] == [
        {"account": "A1", "symbol": "VTI", "reason": "lots hold 11.0 of 12.0 share(s)"},
        {"account": "A1", "symbol": "AAPL", "reason": "no lots"}]
    # a null acquisition date is the declared default class downstream
    tax = LiquidationTax("tax", {
        "short_term_rate": 0.4, "long_term_rate": 0.2, "long_term_days": 366,
        "default_holding": "long", "loss_offset": False, "value_field": "market_value",
        "basis_field": "cost_basis", "acquired_field": "acquired"}).run(
            ctx, {"records": out["records"]})
    assert [r["liq_holding"] for r in tax["records"]] == ["long", "long"]


def test_lots_the_snapshot_does_not_carry_are_counted_not_emitted(ctx):
    rows = TWO_BUYS_ONE_SELL + [_trade(9, "2024-02-02", 1.0, 400.0, symbol="MSFT")]
    out = _join(ctx, _lots(ctx, rows)["records"], [_position()])
    assert {r["symbol"] for r in out["records"]} == {"VTI"}
    assert out["summary"]["unpositioned"] == [{"account": "A1", "symbol": "MSFT", "lots": 1}]


def test_the_basis_gap_shows_where_fifo_and_the_books_differ(ctx):
    # the books carry an average cost of 110 a share (1210); FIFO's lots carry 1200
    lots = _lots(ctx, TWO_BUYS_ONE_SELL)["records"]
    out = _join(ctx, lots, [_position(basis=1210.0)])
    assert out["summary"]["reconciled"][0]["basis_gap"] == pytest.approx(-10.0)


def test_join_rows_refuse_by_name(ctx):
    lots = _lots(ctx, TWO_BUYS_ONE_SELL)["records"]
    for positions, match in (
        ([_position(), _position()], r"positions carry \('A1', 'VTI'\) twice"),
        ([{**_position(), "long_quantity": 0.0}], "position 0 field 'long_quantity' must be positive"),
        ([{**_position(), "long_quantity": -4.0}], "position 0 field 'long_quantity' must be positive"),
        ([{k: v for k, v in _position().items() if k != "asset_type"}],
         "position 0 has no carry field 'asset_type'"),
        ([{**_position(), "market_value": "x"}], "position 0 field 'market_value' is not a number"),
        ([{**_position(), "symbol": None}], r"position 0 key \['account', 'symbol'\] is unusable"),
        (None, "positions must be a list"),
    ):
        with pytest.raises(ValueError, match=match):
            _join(ctx, lots, positions)
    for bad, match in (
        ({**lots[0], "quantity": 0.0}, "lot 0 field 'quantity' must be positive"),
        ({**lots[0], "basis": -1.0}, "lot 0 field 'basis' must be non-negative"),
        ({**lots[0], "acquired": "soon"}, "lot 0 field 'acquired' is not an ISO date"),
        ({**lots[0], "lot_id": ""}, "lot 0 field 'lot_id' is not a non-empty string"),
    ):
        with pytest.raises(ValueError, match=match):
            _join(ctx, [bad], [_position()])
    with pytest.raises(ValueError, match="lots must be a list"):
        _join(ctx, None, [_position()])


JOIN_REQUIRED = ("key_fields", "quantity_field", "value_field", "basis_field",
                 "allow_incomplete")
BAD_JOIN = [
    ({"surprise": 1}, "unknown"),
    ({"key_fields": "symbol"}, "key_fields must be a non-empty list of distinct field names"),
    ({"key_fields": ["account", "lot_id"]}, "key_fields may not name an output field"),
    *[({name: bad}, f"{name} must be a non-empty field name")
      for name in ("quantity_field", "value_field", "basis_field") for bad in ("", 5, None)],
    ({"value_field": "symbol"}, "value_field may not be a key field"),
    ({"carry_fields": "as_of"}, "carry_fields must be a list of distinct field names"),
    ({"carry_fields": ["as_of", "as_of"]}, "carry_fields must be a list of distinct field names"),
    ({"carry_fields": ["market_value"]}, "carry_fields may not name a key or output field"),
    ({"carry_fields": ["acquired"]}, "carry_fields may not name a key or output field"),
    ({"tolerance": -0.1}, "tolerance must be a number in"),
    ({"tolerance": 1.5}, "tolerance must be a number in"),
    ({"tolerance": True}, "tolerance must be a number in"),
    ({"allow_incomplete": None}, "allow_incomplete must be a bool"),
]


@pytest.mark.parametrize("over, match", BAD_JOIN)
def test_each_bad_join_declaration_is_one_named_problem(over, match):
    problems = LotPositions.validate_params({**JOIN, **over})
    assert len(problems) == 1 and re.search(match, problems[0]), problems


@pytest.mark.parametrize("name", JOIN_REQUIRED)
def test_each_join_param_is_named_when_missing(name):
    problems = LotPositions.validate_params({k: v for k, v in JOIN.items() if k != name})
    assert problems == [f"missing required params ['{name}']"]


def test_both_kinds_serve_pure_and_carry_fields_is_optional(ctx):
    assert TaxLots.serving_effect({}, None) == "pure"
    assert LotPositions.serving_effect({}, None) == "pure"
    plain = {k: v for k, v in JOIN.items() if k != "carry_fields"}
    lots = _lots(ctx, TWO_BUYS_ONE_SELL)["records"]
    out = LotPositions("lot_positions", plain).run(ctx, {"lots": lots, "positions": [_position()]})
    assert set(out["records"][0]) == {"account", "symbol", "lot_id", "acquired",
                                      "long_quantity", "market_value", "cost_basis"}
