"""PositionSummary (ADR-0279, research A0002): a decision run's rows reduced to a few numbers.

Every expectation is worked by hand. The three-position portfolio is chosen so each
ratio is a perfect square over a two-year horizon: VTI held in two accounts (merged
for concentration) and AAPL in one. The break-even return is checked twice: by its
closed form, and by feeding it back through HoldVsRedeploy, where it must tie the
two paths.
"""

import math

import pytest

from dskit.pipeline import liquidation, position_summary
from dskit.pipeline.hold_return import BetaHoldReturns
from dskit.pipeline.liquidation import HoldVsRedeploy
from dskit.pipeline.node import ConfigError, NodeContext
from dskit.pipeline.position_summary import (
    BREAK_EVEN_RULES,
    SUMMARY_FIELDS,
    VOLATILITY_BOUNDS,
    AnnualBreakEven,
    PositionSummary,
    TerminalBreakEven,
    UntaxedBreakEven,
)


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="ps", asof="2026-10-27", run_dir=str(tmp_path))


PARAMS = {
    "key_fields": ["account", "symbol"],
    "symbol_field": "symbol",
    "value_field": "market_value",
    "net_field": "liq_net",
    "basis_field": "cost_basis",
    "redeploy_cost": 0.01,
    "risk_premium": 0.02,
    "redeploy_tax_rate": 0.4,
    "redeploy_tax_timing": "annual",
}


def _row(account, symbol, value, basis, net, hold_value, hold_net, redeploy_net, decision):
    return {"account": account, "symbol": symbol, "market_value": value, "cost_basis": basis,
            "liq_net": net, "horizon_years": 2, "hold_value": hold_value, "hold_net": hold_net,
            "redeploy_net": redeploy_net, "decision": decision, "other": "dropped"}


# Every row shares one redeploy growth (x1.44 over two years, 1.2 a year).
ROWS = [
    # gain 400, tax 100; hold 1000 -> 1210 (10%/yr), net 1089 = 900 x 1.1^2; redeploy 900 x 1.44
    _row("A1", "VTI", 1000.0, 600.0, 900.0, 1210.0, 1089.0, 1296.0, "exit"),
    # no gain; hold 500 -> 605 (10%/yr), net 551.25 = 500 x 1.05^2; redeploy 500 x 1.44 = 720
    _row("A2", "VTI", 500.0, 500.0, 500.0, 605.0, 551.25, 720.0, "exit"),
    # gain 400, tax 80; hold 500 -> 720 (20%/yr), net 709.8 = 420 x 1.3^2; redeploy 420 x 1.44
    _row("A1", "AAPL", 500.0, 100.0, 420.0, 720.0, 709.8, 604.8, "hold"),
]


def _run(ctx, rows=None, inputs=None, **over):
    node = PositionSummary("summary", {**PARAMS, **over})
    return node.run(ctx, {"records": ROWS if rows is None else rows, **(inputs or {})})


def test_three_positions_by_hand_annual(ctx):
    out = _run(ctx, inputs={"volatilities": {"VTI": 0.16, "AAPL": 0.30},
                            "betas": {"VTI": 1.0, "AAPL": 1.2}})
    a, b, c = out["records"]
    assert list(a) == ["account", "symbol", *SUMMARY_FIELDS], "keys, then the numbers; nothing else"
    assert (a["account"], a["symbol"], c["symbol"]) == ("A1", "VTI", "AAPL")
    assert [r["weight"] for r in (a, b, c)] == [0.5, 0.25, 0.25]
    assert [r["expected_return"] for r in (a, b, c)] == pytest.approx([0.1, 0.1, 0.2])
    assert [r["volatility"] for r in (a, b, c)] == [0.16, 0.16, 0.30]
    assert [r["beta"] for r in (a, b, c)] == [1.0, 1.0, 1.2]
    assert [r["embedded_gain"] for r in (a, b, c)] == pytest.approx([0.4, 0.0, 0.8])
    assert [r["tax_cost"] for r in (a, b, c)] == pytest.approx([0.1, 0.0, 0.16])
    # annual: g = (T/N)^(1/2) - 1 = .1, .05, .3; R = .01 + (g + .02) / (1 - .4)
    assert [r["break_even_return"] for r in (a, b, c)] == pytest.approx(
        [0.21, 0.01 + 0.07 / 0.6, 0.01 + 0.32 / 0.6])
    # (redeploy_net / hold_net)^(1/2) - 1 = 36/33, 8/7, 12/13 less one
    assert [r["opportunity_cost"] for r in (a, b, c)] == pytest.approx(
        [36 / 33 - 1, 8 / 7 - 1, 12 / 13 - 1])
    assert [r["decision"] for r in (a, b, c)] == ["exit", "exit", "hold"]
    assert all(r["horizon_years"] == 2 for r in (a, b, c))
    m = out["metrics"]
    assert m["positions"] == 3 and m["total_value"] == 2000.0
    assert m["expected_return"] == pytest.approx(0.5 * 0.1 + 0.25 * 0.1 + 0.25 * 0.2)   # 0.125
    assert m["weighted_volatility"] == pytest.approx(0.75 * 0.16 + 0.25 * 0.30)       # 0.195
    assert m["beta"] == pytest.approx(0.75 * 1.0 + 0.25 * 1.2)                         # 1.05
    # by SYMBOL, the two VTI accounts merged: .75^2 + .25^2
    assert m["hhi"] == pytest.approx(0.625)
    assert m["effective_holdings"] == pytest.approx(1.6)
    assert (m["top_symbol"], m["top_weight"]) == ("VTI", 0.75)
    assert m["embedded_gain"] == pytest.approx(800.0 / 2000.0)
    assert m["tax_cost"] == pytest.approx(180.0 / 2000.0)
    # totals: N 1820, T 2350.05, redeploy 2620.8 (= 1820 x 1.44)
    g = math.sqrt(2350.05 / 1820.0) - 1.0
    assert m["break_even_return"] == pytest.approx(0.01 + (g + 0.02) / 0.6)
    assert m["opportunity_cost"] == pytest.approx(math.sqrt(2620.8 / 2350.05) - 1.0)
    assert (m["exit_positions"], m["hold_positions"]) == (2, 1)
    assert m["horizon_years"] == 2


def test_input_rows_are_not_mutated_and_unwired_risk_is_none(ctx):
    rows = [dict(r) for r in ROWS]
    out = _run(ctx, rows=rows)
    assert rows == ROWS
    assert all(r["volatility"] is None and r["beta"] is None for r in out["records"])
    assert out["metrics"]["weighted_volatility"] is None and out["metrics"]["beta"] is None


def test_terminal_break_even_by_hand_on_both_branches(ctx):
    one = dict(redeploy_tax_timing="terminal", redeploy_tax_rate=0.25)
    # gain branch: T/N 1.15 -> G = (1.15 - .25) / .75 = 1.2; R = sqrt(1.2) - 1 + .03
    row = _row("A", "X", 1000.0, 1000.0, 1000.0, 1150.0, 1150.0, None, "hold")
    assert _run(ctx, rows=[row], **one)["records"][0]["break_even_return"] == pytest.approx(
        math.sqrt(1.2) - 1.0 + 0.03)
    # loss branch: T/N .81 -> G = .81, x = -.1, R = -.07
    row = _row("A", "X", 1000.0, 1000.0, 1000.0, 810.0, 810.0, None, "hold")
    assert _run(ctx, rows=[row], **one)["records"][0]["break_even_return"] == pytest.approx(-0.07)
    # the branches meet at T = N: R = cost + premium
    row = _row("A", "X", 1000.0, 1000.0, 1000.0, 1000.0, 1000.0, None, "hold")
    assert _run(ctx, rows=[row], **one)["records"][0]["break_even_return"] == pytest.approx(0.03)


def test_annual_loss_branch_and_untaxed_by_hand(ctx):
    # annual, T/N .81: g = -.1, g + premium = -.08 <= 0, no tax shield: R = .01 - .08
    row = _row("A", "X", 1000.0, 1000.0, 1000.0, 810.0, 810.0, None, "hold")
    assert _run(ctx, rows=[row])["records"][0]["break_even_return"] == pytest.approx(-0.07)
    # untaxed (no tax pair declared), T/N 1.21: R = .1 + .01 + .02
    untaxed = {k: v for k, v in PARAMS.items() if not k.startswith("redeploy_tax")}
    row = _row("A", "X", 1000.0, 1000.0, 1000.0, 1210.0, 1210.0, None, "hold")
    out = PositionSummary("s", untaxed).run(ctx, {"records": [row]})
    assert out["records"][0]["break_even_return"] == pytest.approx(0.13)


DECIDE = {"horizon_years": 3, "hold_return": 0.07, "redeploy_cost": 0.01, "risk_premium": 0.02,
          "value_field": "market_value", "net_field": "liq_net", "symbol_field": "symbol",
          "terminal_tax_rate": 0.238, "basis_field": "cost_basis"}


@pytest.mark.parametrize("timing", [None, "annual", "terminal"])
@pytest.mark.parametrize("hold_return", [0.07, -0.08])
def test_the_break_even_ties_hold_vs_redeploy(ctx, timing, hold_return):
    taxes = {} if timing is None else {"redeploy_tax_rate": 0.408, "redeploy_tax_timing": timing}
    decide = {**DECIDE, **taxes, "hold_return": hold_return, "redeploy_return": 0.05}
    rows = [{"account": "A", "symbol": "X", "market_value": 2500.0, "cost_basis": 2000.0,
             "liq_net": 2381.0}]
    decided = HoldVsRedeploy("d", decide).run(ctx, {"records": rows})["records"]
    params = {**PARAMS, **{k: decide[k] for k in ("redeploy_cost", "risk_premium")}}
    params = {k: v for k, v in params.items() if not k.startswith("redeploy_tax")}
    summary = PositionSummary("s", {**params, **taxes}).run(ctx, {"records": decided})
    rate = summary["records"][0]["break_even_return"]
    assert summary["metrics"]["break_even_return"] == pytest.approx(rate)
    tied = HoldVsRedeploy("d", {**decide, "redeploy_return": rate}).run(ctx, {"records": rows})
    tie = tied["records"][0]
    assert tie["redeploy_net"] == pytest.approx(tie["hold_net"], rel=1e-12)
    # the exact tie is float noise either way; a millionth on each side is not
    for step, decision in ((-1e-6, "hold"), (1e-6, "exit")):
        near = HoldVsRedeploy("d", {**decide, "redeploy_return": rate + step}).run(
            ctx, {"records": rows})
        assert near["records"][0]["decision"] == decision, step


def test_a_break_even_that_does_not_exist_refuses_by_row(ctx):
    rows = [ROWS[0], ROWS[2]]
    # a 100% annual tax keeps nothing of a positive return: no return reaches a gain
    with pytest.raises(ValueError, match="row 0.*no redeploy return"):
        _run(ctx, rows=rows, redeploy_tax_rate=1.0)
    with pytest.raises(ValueError, match="row 0.*no redeploy return"):
        _run(ctx, rows=rows, redeploy_tax_rate=1.0, redeploy_tax_timing="terminal")
    # nearly all of it: the break-even, .01 + .12 / .01 = 12.01, is outside RATE_BOUNDS
    with pytest.raises(ValueError, match=r"row 0.*break-even.*outside"):
        _run(ctx, rows=rows, redeploy_tax_rate=0.99)
    # at 100% a position whose hold path loses still has one: R = cost + g + premium
    loss = _row("A", "X", 1000.0, 1000.0, 1000.0, 810.0, 810.0, None, "hold")
    assert _run(ctx, rows=[loss], redeploy_tax_rate=1.0)["records"][0]["break_even_return"] == \
        pytest.approx(-0.07)


def test_an_inadmissible_hurdle_leaves_the_opportunity_cost_none(ctx):
    rows = [{**r, "redeploy_net": None, "decision": "hold"} for r in ROWS]
    out = _run(ctx, rows=rows)
    assert all(r["opportunity_cost"] is None for r in out["records"])
    assert out["metrics"]["opportunity_cost"] is None
    assert out["records"][0]["break_even_return"] == pytest.approx(0.21), "still known"
    assert (out["metrics"]["exit_positions"], out["metrics"]["hold_positions"]) == (0, 3)
    with pytest.raises(ValueError, match="redeploy_net"):
        _run(ctx, rows=[ROWS[0], rows[1]])


def test_unusable_rows_refuse_by_index_and_field(ctx):
    with pytest.raises(ValueError, match="records must be a list"):
        _run(ctx, rows={"a": 1})
    with pytest.raises(ValueError, match="no rows"):
        _run(ctx, rows=[])
    with pytest.raises(ValueError, match="row 1 must be a mapping"):
        _run(ctx, rows=[ROWS[0], [1]])
    short = {**ROWS[1], "market_value": -180.0}
    with pytest.raises(ValueError, match="row 1 field 'market_value'.*positive.*short"):
        _run(ctx, rows=[ROWS[0], short])
    with pytest.raises(ValueError, match="row 0 field 'liq_net'.*positive"):
        _run(ctx, rows=[{**ROWS[0], "liq_net": 0.0}])
    with pytest.raises(ValueError, match="row 0 field 'cost_basis' is not a number"):
        _run(ctx, rows=[{**ROWS[0], "cost_basis": None}])
    with pytest.raises(ValueError, match="row 0 field 'hold_net'.*positive"):
        _run(ctx, rows=[{**ROWS[0], "hold_net": 0.0}])
    with pytest.raises(ValueError, match="row 0 field 'hold_value'.*non-negative"):
        _run(ctx, rows=[{**ROWS[0], "hold_value": -1.0}])
    with pytest.raises(ValueError, match="row 0 field 'redeploy_net'.*non-negative"):
        _run(ctx, rows=[{**ROWS[0], "redeploy_net": -1.0}])
    with pytest.raises(ValueError, match="row 0 field 'horizon_years'"):
        _run(ctx, rows=[{**ROWS[0], "horizon_years": 0}])
    with pytest.raises(ValueError, match="row 2 field 'decision'"):
        _run(ctx, rows=[ROWS[0], ROWS[1], {**ROWS[2], "decision": "sell"}])
    with pytest.raises(ValueError, match="row 1 key.*'symbol'"):
        _run(ctx, rows=[ROWS[0], {**ROWS[1], "symbol": None}])
    with pytest.raises(ValueError, match="rows 0 and 2 carry the key"):
        _run(ctx, rows=[ROWS[0], ROWS[1], ROWS[0]])
    with pytest.raises(ValueError, match="several horizons"):
        _run(ctx, rows=[ROWS[0], {**ROWS[1], "horizon_years": 3}])


def test_wired_tables_refuse_by_port_and_row(ctx):
    with pytest.raises(ValueError, match="volatilities must be a.*mapping"):
        _run(ctx, inputs={"volatilities": [0.2]})
    # a percent typed for a fraction: 20 is 2000%
    with pytest.raises(ValueError, match=r"volatilities\['VTI'\] 20 .*outside.*fraction.*not percent"):
        _run(ctx, inputs={"volatilities": {"VTI": 20, "AAPL": 0.3}})
    with pytest.raises(ValueError, match=r"volatilities\['VTI'\]"):
        _run(ctx, inputs={"volatilities": {"VTI": -0.1, "AAPL": 0.3}})
    with pytest.raises(ValueError, match="row 2 has no volatilities entry for symbol 'AAPL'"):
        _run(ctx, inputs={"volatilities": {"VTI": 0.16}})
    with pytest.raises(ValueError, match=r"betas\['VTI'\]"):
        _run(ctx, inputs={"betas": {"VTI": "one", "AAPL": 1.2}})
    with pytest.raises(ValueError, match="row 2 has no betas entry for symbol 'AAPL'"):
        _run(ctx, inputs={"betas": {"VTI": 1.0}})
    # the bounds are inclusive: zero (cash) and ten are volatilities
    assert VOLATILITY_BOUNDS == (0.0, 10.0)
    out = _run(ctx, inputs={"volatilities": {"VTI": 0.0, "AAPL": 10.0}})
    assert out["metrics"]["weighted_volatility"] == pytest.approx(2.5)


def test_bad_declarations_refuse_at_construction():
    with pytest.raises(ConfigError, match="unknown"):
        PositionSummary("s", {**PARAMS, "extra": 1})
    with pytest.raises(ConfigError, match="missing.*basis_field"):
        PositionSummary("s", {k: v for k, v in PARAMS.items() if k != "basis_field"})
    with pytest.raises(ConfigError, match="symbol_field.*key_fields"):
        PositionSummary("s", {**PARAMS, "key_fields": ["account"]})
    with pytest.raises(ConfigError, match="key_fields"):
        PositionSummary("s", {**PARAMS, "key_fields": ["symbol", "symbol"]})
    with pytest.raises(ConfigError, match="key_fields.*'weight'"):
        PositionSummary("s", {**PARAMS, "key_fields": ["symbol", "weight"]})
    with pytest.raises(ConfigError, match="value_field"):
        PositionSummary("s", {**PARAMS, "value_field": ""})
    with pytest.raises(ConfigError, match="redeploy_tax_timing"):
        PositionSummary("s", {**PARAMS, "redeploy_tax_timing": "monthly"})
    with pytest.raises(ConfigError, match="redeploy_tax_timing"):
        PositionSummary("s", {k: v for k, v in PARAMS.items() if k != "redeploy_tax_timing"})
    with pytest.raises(ConfigError, match="risk_premium"):
        PositionSummary("s", {**PARAMS, "risk_premium": -0.01})


def test_the_redeploy_terms_are_validated_by_one_owner():
    bad = {"redeploy_cost": -1, "risk_premium": "x", "redeploy_tax_rate": 1.5,
           "redeploy_tax_timing": "weekly"}
    shared = []
    liquidation.check_redeploy_params(shared, bad)
    assert len(shared) == 4
    for cls in (HoldVsRedeploy, PositionSummary):
        problems = cls.validate_params(bad)
        assert all(p in problems for p in shared), cls.__name__


def test_the_names_read_and_the_rules_are_pinned_to_their_owners(ctx):
    # the HoldVsRedeploy outputs read here are its declared outputs
    read = {position_summary._HORIZON, position_summary._HOLD_VALUE, position_summary._HOLD_NET,
            position_summary._REDEPLOY_NET, position_summary._DECISION}
    assert read <= set(liquidation.DECISION_FIELDS)
    rows = [{"symbol": "X", "market_value": 1.0, "liq_net": 1.0, "cost_basis": 1.0}]
    decisions = HoldVsRedeploy("d", {**DECIDE, "redeploy_return": 0.0}).run(
        ctx, {"records": rows})["summary"]["decisions"]
    assert set(decisions) == set(position_summary._DECISIONS)
    # one break-even rule per redeploy tax timing, plus the untaxed one
    assert set(BREAK_EVEN_RULES) == {None, *liquidation.REDEPLOY_TAX_TIMINGS}
    assert (BREAK_EVEN_RULES[None], BREAK_EVEN_RULES["annual"], BREAK_EVEN_RULES["terminal"]) == (
        UntaxedBreakEven, AnnualBreakEven, TerminalBreakEven)
    assert TerminalBreakEven(0.0, 0.0, 1.0).rate(1.5, 2) is None
    assert AnnualBreakEven(0.0, 0.0, 1.0).rate(1.5, 2) is None


def test_beta_hold_returns_wires_the_betas_it_priced_with(ctx):
    node = BetaHoldReturns("b", {"risk_free": 0.04, "betas": {"VTI": 1.0, "AAPL": 1.2},
                                 "default_beta": 0.9, "symbol_field": "symbol"})
    rows = [{"symbol": "AAPL"}, {"symbol": "NEW"}, {"symbol": "AAPL"}]
    assert node.run(ctx, {"records": rows, "market": 0.1})["betas"] == {"AAPL": 1.2, "NEW": 0.9}


def test_position_summary_serves_pure():
    assert PositionSummary.serving_effect({}, None) == "pure"
    assert PositionSummary.role == "transform"
    assert PositionSummary.outputs == ("records", "metrics")
