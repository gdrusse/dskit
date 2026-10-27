"""LiquidationTax and HoldVsRedeploy (ADR-0279): every number here is worked by hand.

Both kinds are wired by import path and are not registered. A row that cannot be
valued refuses by name; nothing is filled or guessed.
"""

import pytest

from dskit.pipeline.liquidation import HoldVsRedeploy, LiquidationTax
from dskit.pipeline.node import ConfigError, NodeContext


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="liq", asof="2026-10-27", run_dir=str(tmp_path))


TAX = {
    "as_of": "2026-10-27",
    "short_term_rate": 0.4,
    "long_term_rate": 0.2,
    "long_term_days": 365,
    "default_holding": "long",
    "loss_offset": False,
    "value_field": "market_value",
    "basis_field": "cost_basis",
    "acquired_field": "acquired",
}


def _tax(ctx, rows, **over):
    return LiquidationTax("tax", {**TAX, **over}).run(ctx, {"records": rows})


def test_long_short_and_defaulted_holdings_by_hand(ctx):
    rows = [
        {"symbol": "A", "market_value": 2500.0, "cost_basis": 2000.0,
         "acquired": "2024-01-01"},                       # long: 500 gain x .2
        {"symbol": "B", "market_value": 600.0, "cost_basis": 450.0,
         "acquired": "2026-06-01"},                       # short: 150 gain x .4
        {"symbol": "C", "market_value": 100.0, "cost_basis": 50.0},  # defaulted long
    ]
    out = _tax(ctx, rows)
    a, b, c = out["records"]
    assert (a["liq_holding"], a["liq_gain"], a["liq_tax"], a["liq_net"]) == (
        "long", 500.0, 100.0, 2400.0)
    assert (b["liq_holding"], b["liq_tax"], b["liq_net"]) == ("short", 60.0, 540.0)
    assert (c["liq_holding"], c["liq_tax"], c["liq_net"]) == ("long", 10.0, 90.0)
    assert a["liq_rate"] == 0.2 and b["liq_rate"] == 0.4
    assert out["summary"]["rows"] == 3
    assert out["summary"]["tax"] == 170.0
    assert out["summary"]["net"] == 3030.0


def test_exactly_the_threshold_day_is_long(ctx):
    rows = [{"market_value": 10.0, "cost_basis": 0.0, "acquired": "2025-10-27"}]
    assert _tax(ctx, rows)["records"][0]["liq_holding"] == "long"
    rows = [{"market_value": 10.0, "cost_basis": 0.0, "acquired": "2025-10-28"}]
    assert _tax(ctx, rows)["records"][0]["liq_holding"] == "short"


def test_a_loss_is_untaxed_unless_offset_is_declared(ctx):
    rows = [{"market_value": 80.0, "cost_basis": 100.0, "acquired": "2020-01-01"}]
    row = _tax(ctx, rows)["records"][0]
    assert (row["liq_gain"], row["liq_tax"], row["liq_net"]) == (-20.0, 0.0, 80.0)
    row = _tax(ctx, rows, loss_offset=True)["records"][0]
    assert (row["liq_tax"], row["liq_net"]) == (-4.0, 84.0)


def test_input_rows_are_not_mutated_and_prefixed_fields_refuse(ctx):
    rows = [{"market_value": 10.0, "cost_basis": 5.0}]
    _tax(ctx, rows)
    assert rows == [{"market_value": 10.0, "cost_basis": 5.0}]
    with pytest.raises(ValueError, match="liq_tax"):
        _tax(ctx, [{"market_value": 10.0, "cost_basis": 5.0, "liq_tax": 1.0}])


def test_unvaluable_rows_refuse_by_name(ctx):
    with pytest.raises(ValueError, match="row 0.*cost_basis"):
        _tax(ctx, [{"market_value": 10.0, "cost_basis": "five"}])
    with pytest.raises(ValueError, match="row 0.*acquired"):
        _tax(ctx, [{"market_value": 10.0, "cost_basis": 5.0, "acquired": "soon"}])
    with pytest.raises(ValueError, match="row 0.*mapping"):
        _tax(ctx, [[1, 2]])


def test_bad_declarations_refuse_at_construction():
    with pytest.raises(ConfigError, match="unknown"):
        LiquidationTax("t", {**TAX, "surprise": 1})
    with pytest.raises(ConfigError, match="short_term_rate"):
        LiquidationTax("t", {**TAX, "short_term_rate": 1.5})
    with pytest.raises(ConfigError, match="default_holding"):
        LiquidationTax("t", {**TAX, "default_holding": "medium"})
    with pytest.raises(ConfigError, match="as_of"):
        LiquidationTax("t", {**TAX, "as_of": "yesterday"})
    with pytest.raises(ConfigError, match="long_term_days"):
        LiquidationTax("t", {**TAX, "long_term_days": 0})
    with pytest.raises(ConfigError, match="loss_offset"):
        LiquidationTax("t", {k: v for k, v in TAX.items() if k != "loss_offset"})


DECIDE = {
    "horizon_years": 2,
    "hold_return": 0.07,
    "redeploy_return": 0.15,
    "redeploy_cost": 0.02,
    "risk_premium": 0.03,
    "value_field": "market_value",
    "net_field": "liq_net",
    "symbol_field": "symbol",
}


def _decide(ctx, rows, **over):
    return HoldVsRedeploy("d", {**DECIDE, **over}).run(ctx, {"records": rows})


def test_hold_and_redeploy_terminal_values_by_hand(ctx):
    # hold: 2500 x 1.07^2 = 2862.25; redeploy: 2400 x 1.10^2 = 2904.0 -> exit
    # hold: 600 x 1.07^2 = 686.94; redeploy: 540 x 1.10^2 = 653.4 -> hold
    rows = [
        {"symbol": "A", "market_value": 2500.0, "liq_net": 2400.0},
        {"symbol": "B", "market_value": 600.0, "liq_net": 540.0},
    ]
    out = _decide(ctx, rows)
    a, b = out["records"]
    assert a["hold_value"] == pytest.approx(2862.25)
    assert a["redeploy_value"] == pytest.approx(2904.0)
    assert a["advantage"] == pytest.approx(41.75)
    assert a["decision"] == "exit"
    assert b["decision"] == "hold"
    assert b["advantage"] == pytest.approx(653.4 - 686.94)
    assert out["summary"]["decisions"] == {"exit": 1, "hold": 1}
    assert out["summary"]["hold_value"] == pytest.approx(2862.25 + 686.94)
    assert out["summary"]["redeploy_value"] == pytest.approx(2904.0 + 653.4)


def test_per_symbol_hold_returns_and_a_missing_symbol_refuses(ctx):
    rows = [{"symbol": "A", "market_value": 100.0, "liq_net": 90.0}]
    out = _decide(ctx, rows, hold_return={"A": 0.0})
    assert out["records"][0]["hold_value"] == pytest.approx(100.0)
    with pytest.raises(ValueError, match="row 0.*hold_return.*'B'"):
        _decide(ctx, [{"symbol": "B", "market_value": 1.0, "liq_net": 1.0}],
                hold_return={"A": 0.0})


def test_a_tie_holds_and_bad_declarations_refuse(ctx):
    rows = [{"symbol": "A", "market_value": 100.0, "liq_net": 100.0}]
    out = _decide(ctx, rows, hold_return=0.1, redeploy_return=0.1,
                  redeploy_cost=0.0, risk_premium=0.0)
    assert out["records"][0]["decision"] == "hold"
    with pytest.raises(ConfigError, match="horizon_years"):
        HoldVsRedeploy("d", {**DECIDE, "horizon_years": 0})
    with pytest.raises(ConfigError, match="hold_return"):
        HoldVsRedeploy("d", {**DECIDE, "hold_return": "high"})
    with pytest.raises(ConfigError, match="unknown"):
        HoldVsRedeploy("d", {**DECIDE, "extra": 1})
