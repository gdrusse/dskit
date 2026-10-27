"""LiquidationTax and HoldVsRedeploy (ADR-0279): every number here is worked by hand.

Both kinds are wired by import path and are not registered. A row that cannot be
valued refuses by name; nothing is filled or guessed.
"""

import pytest

from dskit.pipeline import liquidation
from dskit.pipeline.liquidation import HoldVsRedeploy, LiquidationTax
from dskit.pipeline.node import ConfigError, NodeContext


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="liq", asof="2026-10-27", run_dir=str(tmp_path))


TAX = {
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
    assert out["summary"]["holdings"] == {"long": 2, "short": 1}   # A and C long, B short


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


def test_short_zero_and_negative_basis_rows_refuse_by_index_and_field(ctx):
    # a short position (market_value -180, cost_basis -200), a zero balance and a
    # negative basis are out of scope for a hold-vs-exit decision: the owner filters them
    ok = {"market_value": 100.0, "cost_basis": 50.0}
    with pytest.raises(ValueError, match="row 1.*market_value.*positive.*short"):
        _tax(ctx, [ok, {"market_value": -180.0, "cost_basis": -200.0}])
    with pytest.raises(ValueError, match="row 0.*market_value.*positive"):
        _tax(ctx, [{"market_value": 0.0, "cost_basis": 0.0}])
    with pytest.raises(ValueError, match="row 0.*cost_basis.*non-negative"):
        _tax(ctx, [{"market_value": 100.0, "cost_basis": -1.0}])
    # a zero basis (a gift, a spin-off) is a legitimate long position
    assert _tax(ctx, [{"market_value": 100.0, "cost_basis": 0.0}])["records"][0]["liq_gain"] == 100.0


def test_as_of_defaults_to_the_run_and_a_declared_one_wins(ctx, tmp_path):
    rows = [{"market_value": 10.0, "cost_basis": 0.0, "acquired": "2025-10-27"}]
    assert _tax(ctx, rows)["summary"]["as_of"] == "2026-10-27"
    assert _tax(ctx, rows, as_of="2026-01-01")["records"][0]["liq_holding"] == "short"
    with pytest.raises(ValueError, match="as_of"):
        _tax(NodeContext(name="x", asof="soon", run_dir=str(tmp_path)), rows)


def test_bad_declarations_refuse_at_construction():
    with pytest.raises(ConfigError, match="unknown"):
        LiquidationTax("t", {**TAX, "surprise": 1})
    with pytest.raises(ConfigError, match="short_term_rate"):
        LiquidationTax("t", {**TAX, "short_term_rate": 1.5})
    with pytest.raises(ConfigError, match="default_holding"):
        LiquidationTax("t", {**TAX, "default_holding": "medium"})
    with pytest.raises(ConfigError, match="as_of"):
        LiquidationTax("t", {**TAX, "as_of": "yesterday"})
    with pytest.raises(ConfigError, match="as_of"):
        LiquidationTax("t", {**TAX, "as_of": "2026-W40"})
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


def test_terminal_tax_on_both_paths_by_hand(ctx):
    # hold: 2500 x 1.07^2 = 2862.25, gain over basis 2000 = 862.25 x .2 = 172.45 -> 2689.80
    # redeploy: 2400 x 1.10^2 = 2904.0, gain over new basis 2400 = 504 x .2 = 100.8 -> 2803.20
    rows = [{"symbol": "A", "market_value": 2500.0, "liq_net": 2400.0, "cost_basis": 2000.0}]
    out = _decide(ctx, rows, terminal_tax_rate=0.2, basis_field="cost_basis",
                  redeploy_tax_rate=0.2, redeploy_tax_timing="terminal")
    row = out["records"][0]
    assert row["hold_terminal_tax"] == pytest.approx(172.45)
    assert row["redeploy_terminal_tax"] == pytest.approx(100.8)
    assert row["hold_net"] == pytest.approx(2689.80)
    assert row["redeploy_net"] == pytest.approx(2803.20)
    assert row["advantage"] == pytest.approx(113.40)
    assert row["decision"] == "exit"
    assert out["summary"]["hold_net"] == pytest.approx(2689.80)
    # a terminal loss pays no terminal tax
    rows = [{"symbol": "A", "market_value": 100.0, "liq_net": 100.0, "cost_basis": 500.0}]
    row = _decide(ctx, rows, terminal_tax_rate=0.2, basis_field="cost_basis",
                  redeploy_tax_rate=0.2, redeploy_tax_timing="terminal",
                  hold_return=0.0, redeploy_return=0.0, redeploy_cost=0.0,
                  risk_premium=0.0)["records"][0]
    assert row["hold_terminal_tax"] == 0.0 and row["redeploy_terminal_tax"] == 0.0


def test_redeploy_tax_annual_and_terminal_by_hand(ctx):
    # redeploy 1000 at 10% for 2 years; ST rate .4
    rows = [{"symbol": "A", "market_value": 1000.0, "liq_net": 1000.0}]
    base = dict(hold_return=0.0, redeploy_return=0.1, redeploy_cost=0.0, risk_premium=0.0)
    # annual: 1000 x (1 + .1 x .6)^2 = 1123.60, no terminal tax
    row = _decide(ctx, rows, **base, redeploy_tax_rate=0.4,
                  redeploy_tax_timing="annual")["records"][0]
    assert row["redeploy_value"] == pytest.approx(1123.60)
    assert row["redeploy_terminal_tax"] == 0.0
    assert row["redeploy_net"] == pytest.approx(1123.60)
    # terminal: 1000 x 1.1^2 = 1210, gain 210 x .4 = 84 -> 1126
    row = _decide(ctx, rows, **base, redeploy_tax_rate=0.4,
                  redeploy_tax_timing="terminal")["records"][0]
    assert row["redeploy_value"] == pytest.approx(1210.0)
    assert row["redeploy_terminal_tax"] == pytest.approx(84.0)
    assert row["redeploy_net"] == pytest.approx(1126.0)
    # undeclared: no tax on the redeploy path, and the summary says so
    out = _decide(ctx, rows, **base)
    assert out["records"][0]["redeploy_net"] == pytest.approx(1210.0)
    assert out["summary"]["redeploy_tax_timing"] is None
    with pytest.raises(ConfigError, match="redeploy_tax_timing"):
        HoldVsRedeploy("d", {**DECIDE, "redeploy_tax_rate": 0.4})
    with pytest.raises(ConfigError, match="redeploy_tax_timing"):
        HoldVsRedeploy("d", {**DECIDE, "redeploy_tax_rate": 0.4, "redeploy_tax_timing": "monthly"})


def test_a_rate_below_minus_one_refuses_instead_of_a_negative_power(ctx):
    rows = [{"symbol": "A", "market_value": 100.0, "liq_net": 100.0}]
    # a cost can push an in-range return below -1; the power then is not a value
    with pytest.raises(ValueError, match="redeploy.*below -1"):
        _decide(ctx, rows, redeploy_return=-0.5, redeploy_cost=0.6, risk_premium=0.0)
    # annual timing: a negative net rate gets no refund, so it is the rate itself that is refused
    with pytest.raises(ValueError, match="redeploy.*below -1"):
        _decide(ctx, rows, redeploy_return=-0.5, redeploy_cost=0.6, risk_premium=0.0,
                redeploy_tax_rate=0.4, redeploy_tax_timing="annual")


def test_terminal_tax_needs_a_basis_field_and_vice_versa(ctx):
    with pytest.raises(ConfigError, match="basis_field"):
        HoldVsRedeploy("d", {**DECIDE, "terminal_tax_rate": 0.2})
    with pytest.raises(ConfigError, match="terminal_tax_rate"):
        HoldVsRedeploy("d", {**DECIDE, "basis_field": "cost_basis"})
    with pytest.raises(ConfigError, match="terminal_tax_rate"):
        HoldVsRedeploy("d", {**DECIDE, "terminal_tax_rate": 1.5, "basis_field": "b"})
    rows = [{"symbol": "A", "market_value": 1.0, "liq_net": 1.0}]
    with pytest.raises(ValueError, match="row 0.*cost_basis"):
        _decide(ctx, rows, terminal_tax_rate=0.2, basis_field="cost_basis")
    # without the pair, the fields are present and zero so a consumer sees one shape
    row = _decide(ctx, rows)["records"][0]
    assert (row["hold_terminal_tax"], row["redeploy_terminal_tax"]) == (0.0, 0.0)
    assert row["hold_net"] == row["hold_value"]


def test_annual_tax_hits_the_net_rate_only_when_positive_and_never_the_premium(ctx):
    rows = [{"symbol": "A", "market_value": 1000.0, "liq_net": 1000.0}]
    base = dict(hold_return=0.0, horizon_years=2, redeploy_tax_rate=0.4,
                redeploy_tax_timing="annual")
    # return .10, cost .01 -> net rate .09; tax .09 x .4 = .036 -> .054; premium .02 is not
    # tax-shielded -> rate used .034; 1000 x 1.034^2 = 1069.156
    out = _decide(ctx, rows, **base, redeploy_return=0.10, redeploy_cost=0.01, risk_premium=0.02)
    assert out["records"][0]["redeploy_value"] == pytest.approx(1069.156)
    assert out["summary"]["redeploy_rate"] == pytest.approx(0.034)
    assert out["summary"]["redeploy_tax_rate"] == 0.4
    # return -.05, cost .01 -> net rate -.06; a loss gets no refund -> -.06; premium .02 -> -.08;
    # 1000 x .92^2 = 846.4 (the old (1 - t) scaling would have given 906.304)
    out = _decide(ctx, rows, **base, redeploy_return=-0.05, redeploy_cost=0.01, risk_premium=0.02)
    assert out["records"][0]["redeploy_value"] == pytest.approx(846.4)
    assert out["summary"]["redeploy_rate"] == pytest.approx(-0.08)
    # terminal and undeclared timing subtract cost and premium untaxed: .10 - .01 - .02 = .07
    for extra in ({"redeploy_tax_rate": 0.4, "redeploy_tax_timing": "terminal"}, {}):
        out = _decide(ctx, rows, hold_return=0.0, redeploy_return=0.10, redeploy_cost=0.01,
                      risk_premium=0.02, **extra)
        assert out["summary"]["redeploy_rate"] == pytest.approx(0.07)
        assert out["records"][0]["redeploy_value"] == pytest.approx(1000.0 * 1.07 ** 2)


def test_non_positive_value_or_net_refuses_by_index_and_field(ctx):
    ok = {"symbol": "A", "market_value": 100.0, "liq_net": 90.0}
    short = {"symbol": "B", "market_value": -180.0, "liq_net": -180.0}
    with pytest.raises(ValueError, match="row 1.*market_value.*positive.*short"):
        _decide(ctx, [ok, short])
    with pytest.raises(ValueError, match="row 0.*market_value.*positive"):
        _decide(ctx, [{**ok, "market_value": 0.0}])
    with pytest.raises(ValueError, match="row 0.*liq_net.*positive"):
        _decide(ctx, [{**ok, "liq_net": 0.0}])
    with pytest.raises(ValueError, match="row 0.*liq_net.*positive"):
        _decide(ctx, [{**ok, "liq_net": -5.0}])


def test_wired_rates_outside_the_bounds_refuse_naming_the_input(ctx):
    rows = [{"symbol": "A", "market_value": 100.0, "liq_net": 90.0}]
    node = HoldVsRedeploy("d", DECIDE)
    # a percent typed where a fraction belongs: 500 means 50000%
    with pytest.raises(ValueError, match=r"hold_returns\['A'\].*500.*fractions.*percent"):
        node.run(ctx, {"records": rows, "hold_returns": {"A": 500}})
    with pytest.raises(ValueError, match=r"hold_returns\['A'\]"):
        node.run(ctx, {"records": rows, "hold_returns": {"A": -1.01}})
    with pytest.raises(ValueError, match="redeploy_return input.*50.*fractions"):
        node.run(ctx, {"records": rows, "redeploy_return": 50})
    with pytest.raises(ValueError, match="redeploy_return input"):
        node.run(ctx, {"records": rows, "redeploy_return": -2.0})
    # the bounds are inclusive and are the ONE owner's
    assert liquidation.RATE_BOUNDS == (-1.0, 10.0)
    free = HoldVsRedeploy("d", {**DECIDE, "redeploy_cost": 0.0, "risk_premium": 0.0})
    out = free.run(ctx, {"records": rows, "hold_returns": {"A": 10.0}, "redeploy_return": -1.0})
    assert out["records"][0]["hold_value"] == pytest.approx(100.0 * 11.0 ** 2)   # (1 + 10)^2
    assert out["records"][0]["redeploy_value"] == 0.0                            # (1 - 1)^2
    out = free.run(ctx, {"records": rows, "hold_returns": {"A": -1.0}, "redeploy_return": 10.0})
    assert out["records"][0]["hold_value"] == 0.0
    assert out["records"][0]["redeploy_value"] == pytest.approx(90.0 * 11.0 ** 2)
    with pytest.raises(ValueError, match="hold_returns"):
        node.run(ctx, {"records": rows, "hold_returns": {"A": 10.01}})
    with pytest.raises(ValueError, match="redeploy_return input"):
        node.run(ctx, {"records": rows, "redeploy_return": 10.01})


def test_cost_and_premium_cannot_be_negative_and_the_horizon_is_capped():
    for name in ("redeploy_cost", "risk_premium"):
        with pytest.raises(ConfigError, match=name):
            HoldVsRedeploy("d", {**DECIDE, name: -0.01})
        HoldVsRedeploy("d", {**DECIDE, name: 0.0})
    assert liquidation.MAX_HORIZON_YEARS == 100
    HoldVsRedeploy("d", {**DECIDE, "horizon_years": 100})
    HoldVsRedeploy("d", {**DECIDE, "horizon_years": 5.0})   # integral float: a count
    for bad in (101, 10 ** 400, 2.5, True):
        with pytest.raises(ConfigError, match="horizon_years"):
            HoldVsRedeploy("d", {**DECIDE, "horizon_years": bad})


def test_an_integral_float_horizon_runs_as_the_same_int(ctx):
    rows = [{"symbol": "A", "market_value": 100.0, "liq_net": 90.0}]
    out = _decide(ctx, rows, horizon_years=2.0)
    assert out["summary"]["horizon_years"] == 2 and isinstance(out["summary"]["horizon_years"], int)
    assert out["records"][0]["horizon_years"] == 2
