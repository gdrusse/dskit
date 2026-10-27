"""PositionSummary (ADR-0279, research A0002): a decision run's rows reduced to a few numbers.

Every expectation is worked by hand. The three-position portfolio is chosen so each
ratio is a perfect square over a two-year horizon: VTI held in two accounts (merged
for concentration) and AAPL in one. The break-even return is checked twice: by its
closed form, and by feeding it back through HoldVsRedeploy, where it must tie the
two paths.
"""

import math

import pytest

from dskit.pipeline import decision_outcomes, liquidation, position_summary
from dskit.pipeline.hold_return import BetaHoldReturns
from dskit.pipeline.liquidation import MAX_HORIZON_YEARS, HoldVsRedeploy, LiquidationTax
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


def _row(account, symbol, value, basis, net, hold_value, hold_net, redeploy_net, decision,
         years=2):
    return {"account": account, "symbol": symbol, "market_value": value, "cost_basis": basis,
            "liq_net": net, "horizon_years": years, "hold_value": hold_value, "hold_net": hold_net,
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


def _assert_ties(ctx, decide, rows, rate):
    """Redeploying at ``rate`` ties the hold path under ``decide``'s own arithmetic: equal nets,
    and a millionth of a point either side flips the decision (the exact tie is float noise)."""
    tie = HoldVsRedeploy("d", {**decide, "redeploy_return": rate}).run(
        ctx, {"records": rows})["records"][0]
    assert tie["redeploy_net"] == pytest.approx(tie["hold_net"], rel=1e-12)
    for step, decision in ((-1e-6, "hold"), (1e-6, "exit")):
        near = HoldVsRedeploy("d", {**decide, "redeploy_return": rate + step}).run(
            ctx, {"records": rows})
        assert near["records"][0]["decision"] == decision, step


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
    _assert_ties(ctx, decide, rows, rate)


# cost .01, premium .03, tax .4, a flat position (value = basis = net = 1000) whose hold path
# compounds at `hold` for `years`. Each expected break-even is worked by hand, then tied back.
KINK = {"redeploy_cost": 0.01, "risk_premium": 0.03}
KINKS = [
    # annual, the loss side of the kink: g = -.01 is a loss, but g + premium = +.02 is a positive
    # net rate the redeploy path is taxed on: R = .01 + .02 / (1 - .4). (Branching on g, not on
    # g + premium, gives the untaxed .04.)
    ("annual", 3, -0.01, 0.01 + 0.02 / 0.6),
    # annual, g + premium = 0: both branches agree, R = cost
    ("annual", 3, -0.03, 0.01),
    # annual, g + premium = -.01 < 0: no tax and no refund, R = .01 - .01
    ("annual", 3, -0.04, 0.0),
    # annual, a gain: R = .01 + .035 / .6
    ("annual", 3, 0.005, 0.01 + 0.035 / 0.6),
    # terminal, just under the kink at T/N = 1: 1.01 ** 2 reversed, G = T/N = .99 ** 2 (nothing to
    # tax) and R = .99 - 1 + .01 + .03. (Branching on g + premium > 0 taxes a gain it does not have.)
    ("terminal", 2, -0.01, 0.03),
    # terminal, just over it: G = (1.01 ** 2 - .4) / .6, R = sqrt(G) - 1 + .01 + .03
    ("terminal", 2, 0.01, math.sqrt((1.0201 - 0.4) / 0.6) - 1.0 + 0.04),
]


@pytest.mark.parametrize("timing, years, hold, expected", KINKS)
def test_the_break_even_at_the_kinks_by_hand_and_tied_back(ctx, timing, years, hold, expected):
    taxes = {"redeploy_tax_rate": 0.4, "redeploy_tax_timing": timing}
    decide = {"horizon_years": years, "hold_return": hold, "redeploy_return": 0.05, **KINK,
              **taxes, "value_field": "market_value", "net_field": "liq_net",
              "symbol_field": "symbol"}
    rows = [{"account": "A", "symbol": "X", "market_value": 1000.0, "cost_basis": 1000.0,
             "liq_net": 1000.0}]
    decided = HoldVsRedeploy("d", decide).run(ctx, {"records": rows})["records"]
    params = {k: v for k, v in PARAMS.items() if not k.startswith("redeploy_tax")}
    out = PositionSummary("s", {**params, **KINK, **taxes}).run(ctx, {"records": decided})
    rate = out["records"][0]["break_even_return"]
    assert rate == pytest.approx(expected, abs=1e-12)
    assert out["metrics"]["break_even_return"] == pytest.approx(expected, abs=1e-12)
    _assert_ties(ctx, decide, rows, rate)


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


LOSS_TAX = {"short_term_rate": 0.4, "long_term_rate": 0.2, "long_term_days": 365,
            "default_holding": "long", "value_field": "market_value", "basis_field": "cost_basis"}
LOSS_DECIDE = {"horizon_years": 2, "hold_return": 0.1, "redeploy_return": 0.2,
               "redeploy_cost": 0.01, "risk_premium": 0.02, "value_field": "market_value",
               "net_field": "liq_net", "symbol_field": "symbol"}


@pytest.mark.parametrize("loss_offset, loss_net, loss_tax_cost, total_tax_cost", [
    # losses pay nothing: LOSS nets its 800; tax 0 + 120 + 100 = 220 of 2500
    (False, 800.0, 0.0, 220.0 / 2500.0),
    # losses are a negative tax: -200 x .2 = -40, LOSS nets 840; tax -40 + 120 + 100 = 180 of 2500
    (True, 840.0, -40.0 / 800.0, 180.0 / 2500.0),
])
def test_a_loss_and_a_zero_basis_by_hand_through_the_liquidation(
        ctx, loss_offset, loss_net, loss_tax_cost, total_tax_cost):
    book = [("LOSS", 800.0, 1000.0), ("GAIN", 1200.0, 600.0), ("ZERO", 500.0, 0.0)]
    rows = [{"account": "A", "symbol": s, "market_value": v, "cost_basis": b} for s, v, b in book]
    taxed = LiquidationTax("t", {**LOSS_TAX, "loss_offset": loss_offset}).run(
        ctx, {"records": rows})["records"]
    assert [r["liq_net"] for r in taxed] == [loss_net, 1080.0, 400.0]
    decided = HoldVsRedeploy("d", LOSS_DECIDE).run(ctx, {"records": taxed})["records"]
    untaxed = {k: v for k, v in PARAMS.items() if not k.startswith("redeploy_tax")}
    node = PositionSummary("s", {**untaxed, "redeploy_cost": 0.01, "risk_premium": 0.02})
    out = node.run(ctx, {"records": decided})
    loss, gain, zero = out["records"]
    # (value - basis) / value: a loss is negative, never floored; basis 0 is all gain
    assert [r["embedded_gain"] for r in (loss, gain, zero)] == pytest.approx([-0.25, 0.5, 1.0])
    # (value - net) / value: 0 when the loss pays nothing, negative when it is a negative tax
    assert [r["tax_cost"] for r in (loss, gain, zero)] == pytest.approx(
        [loss_tax_cost, 0.1, 0.2])
    assert [r["weight"] for r in (loss, gain, zero)] == pytest.approx([0.32, 0.48, 0.2])
    m = out["metrics"]
    assert m["total_value"] == 2500.0
    # the portfolio's embedded gain NETS the loss: (-200 + 600 + 500) / 2500, not 1100 / 2500
    assert m["embedded_gain"] == pytest.approx(900.0 / 2500.0)
    # its tax cost is the rows' taxes added up: the loss offsets a gain only when it was taxed
    # negative upstream
    assert m["tax_cost"] == pytest.approx(total_tax_cost)
    # untaxed break-even R = (T / N) ** (1 / 2) - 1 + .01 + .02 = sqrt(T / N) - .97, T = 1.21 x value
    assert [r["break_even_return"] for r in (loss, gain, zero)] == pytest.approx([
        math.sqrt(968.0 / loss_net) - 0.97, math.sqrt(1452.0 / 1080.0) - 0.97,
        math.sqrt(605.0 / 400.0) - 0.97])
    total_net = loss_net + 1080.0 + 400.0
    assert m["break_even_return"] == pytest.approx(math.sqrt(3025.0 / total_net) - 0.97)
    rates = [r["break_even_return"] for r in out["records"]]
    assert min(rates) <= m["break_even_return"] <= max(rates)


@pytest.mark.parametrize("timing, tax", [(None, None), ("annual", 0.25), ("terminal", 0.25)])
def test_the_portfolio_break_even_lies_between_its_rows(ctx, timing, tax):
    # why the portfolio-level refusal cannot fire when every row passed: the break-even is
    # non-decreasing in T / N under every rule, and sum(T) / sum(N) lies between the rows' ratios
    rows = [ROWS[0], ROWS[2], _row("A2", "LOSS", 1000.0, 1000.0, 1000.0, 810.0, 810.0, 900.0, "hold")]
    taxes = {} if timing is None else {"redeploy_tax_rate": tax, "redeploy_tax_timing": timing}
    untaxed = {k: v for k, v in PARAMS.items() if not k.startswith("redeploy_tax")}
    out = PositionSummary("s", {**untaxed, **taxes}).run(ctx, {"records": rows})
    rates = [r["break_even_return"] for r in out["records"]]
    assert len(set(rates)) == 3
    assert min(rates) < out["metrics"]["break_even_return"] < max(rates)


def test_a_rule_that_broke_monotonicity_would_still_refuse_at_the_portfolio(ctx, monkeypatch):
    # the portfolio check is the invariant above made executable: this rule passes each row's
    # ratio (1.21, 1.0) but not their mix (2210 / 2000 = 1.105)
    class Jumpy(UntaxedBreakEven):
        def rate(self, ratio, years):
            return 0.1 if ratio in (1.21, 1.0) else 9.0

    monkeypatch.setitem(BREAK_EVEN_RULES, None, Jumpy)
    rows = [_row("A", "X", 1000.0, 1000.0, 1000.0, 1210.0, 1210.0, None, "hold"),
            _row("A", "Y", 1000.0, 1000.0, 1000.0, 1000.0, 1000.0, None, "hold")]
    untaxed = {k: v for k, v in PARAMS.items() if not k.startswith("redeploy_tax")}
    with pytest.raises(ValueError, match=r"summary: portfolio break-even return 9.0 is outside"):
        PositionSummary("summary", untaxed).run(ctx, {"records": rows})


@pytest.mark.parametrize("name, field", [("volatilities", "volatility"), ("betas", "beta")])
def test_a_wired_none_table_refuses_and_only_an_absent_one_is_unwired(ctx, name, field):
    with pytest.raises(ValueError, match=rf"{name} must be a.*mapping, got None"):
        _run(ctx, inputs={name: None})
    # wired but empty is wired: every symbol is then missing from it
    with pytest.raises(ValueError, match=f"row 0 has no {name} entry for symbol 'VTI'"):
        _run(ctx, inputs={name: {}})
    out = _run(ctx)
    assert all(r[field] is None for r in out["records"])


@pytest.mark.parametrize("bad", [2.5, 0.5, -1, 0, MAX_HORIZON_YEARS + 1, 10 ** 3])
def test_a_horizon_that_is_not_a_whole_number_of_years_in_range_refuses(ctx, bad):
    row = {**ROWS[0], "horizon_years": bad}
    with pytest.raises(ValueError) as caught:
        _run(ctx, rows=[row])
    assert str(caught.value) == (
        f"summary: row 0 field 'horizon_years' must be an int in [1, {MAX_HORIZON_YEARS}], "
        f"got {bad!r}")


@pytest.mark.parametrize("bad", ["2", True, None, float("nan")])
def test_a_horizon_that_is_not_a_number_refuses_through_the_number_reader(ctx, bad):
    with pytest.raises(ValueError, match="row 0 field 'horizon_years' is not a number"):
        _run(ctx, rows=[{**ROWS[0], "horizon_years": bad}])


@pytest.mark.parametrize("years", [1, 2.0, MAX_HORIZON_YEARS])
def test_the_horizon_bounds_are_inclusive_and_a_whole_float_is_an_int(ctx, years):
    row = _row("A", "X", 1000.0, 1000.0, 1000.0, 1210.0, 1210.0, None, "hold", years=years)
    out = _run(ctx, rows=[row])
    assert out["metrics"]["horizon_years"] == int(years)
    assert isinstance(out["metrics"]["horizon_years"], int)
    assert isinstance(out["records"][0]["horizon_years"], int)


def test_a_zero_hold_net_refuses_with_its_own_reason_and_the_others_with_the_shared_one(ctx):
    with pytest.raises(ValueError) as caught:
        _run(ctx, rows=[{**ROWS[0], "hold_net": 0.0}])
    assert str(caught.value) == (
        "summary: row 0 field 'hold_net' must be positive, got 0.0; a hold path worth nothing "
        "has no growth to break even against")
    with pytest.raises(ValueError) as caught:
        _run(ctx, rows=[{**ROWS[0], "liq_net": 0.0}])
    assert str(caught.value) == (
        "summary: row 0 field 'liq_net' must be positive, got 0.0; short positions and negative "
        "balances are out of scope for a hold-vs-exit decision")


def test_betas_are_not_bounded_like_volatilities(ctx):
    # a negative beta is legal (an inverse fund) and so is a levered one; a volatility of 25 is
    # not (2500%): the same number is a beta here and a refusal there
    out = _run(ctx, inputs={"betas": {"VTI": -0.5, "AAPL": 25.0}})
    assert [r["beta"] for r in out["records"]] == [-0.5, -0.5, 25.0]
    assert out["metrics"]["beta"] == pytest.approx(0.75 * -0.5 + 0.25 * 25.0)      # 5.875
    with pytest.raises(ValueError, match=r"volatilities\['AAPL'\] 25.0 .*outside"):
        _run(ctx, inputs={"volatilities": {"VTI": 0.16, "AAPL": 25.0}})
    for bad in (float("inf"), float("nan"), True, "1"):
        with pytest.raises(ValueError, match=r"betas\['VTI'\] must be a number"):
            _run(ctx, inputs={"betas": {"VTI": bad, "AAPL": 1.0}})


def test_the_shared_helpers_have_one_owner():
    # the annual rate and the key_fields rule are decision_outcomes'; PositionSummary imports them
    assert position_summary.annualized_growth is decision_outcomes.annualized_growth
    assert position_summary.check_key_fields_param is decision_outcomes.check_key_fields_param
    assert not hasattr(position_summary, "_annualized")
    bad = {"key_fields": ["a", "a"]}
    shared = []
    decision_outcomes.check_key_fields_param(shared, bad)
    assert len(shared) == 1
    for cls in (decision_outcomes.DecisionOutcomes, PositionSummary):
        assert shared[0] in cls.validate_params(bad), cls.__name__


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
