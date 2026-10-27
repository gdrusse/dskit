"""MarketHoldReturn and BetaHoldReturns (ADR-0279, research A0001): worked by hand.

The market rate is yield + growth + inflation + annualized CAPE reversion; a
stock's rate is risk_free + beta x (market - risk_free). Both are wired by
import path. A symbol without a beta refuses unless a default is declared.
"""

import pytest

from dskit.pipeline.hold_return import BetaHoldReturns, MarketHoldReturn
from dskit.pipeline.liquidation import HoldVsRedeploy
from dskit.pipeline.node import ConfigError, NodeContext


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="hr", asof="2026-10-27", run_dir=str(tmp_path))


MARKET = {
    "dividend_yield": 0.013,
    "real_growth": 0.02,
    "inflation": 0.025,
    "cape": 36.0,
    "fair_cape": 27.0,
    "reversion_years": 10,
    "horizon_years": 5,
}


def test_market_rate_is_the_sum_of_named_blocks(ctx):
    out = MarketHoldReturn("m", MARKET).run(ctx, {})
    reversion = (27.0 / 36.0) ** 0.1 - 1.0          # -0.02836...
    assert out["blocks"]["valuation"] == pytest.approx(reversion)
    assert out["rate"] == pytest.approx(0.013 + 0.02 + 0.025 + reversion)
    assert out["blocks"] == {
        "dividend_yield": 0.013, "real_growth": 0.02, "inflation": 0.025,
        "valuation": pytest.approx(reversion)}
    assert out["summary"]["rate"] == pytest.approx(out["rate"])
    assert out["summary"]["horizon_years"] == 5


def test_a_fairly_valued_market_has_no_valuation_block(ctx):
    out = MarketHoldReturn("m", {**MARKET, "cape": 27.0}).run(ctx, {})
    assert out["blocks"]["valuation"] == 0.0
    assert out["rate"] == pytest.approx(0.058)


def test_market_fingerprint_is_its_params_and_bad_blocks_refuse():
    node = MarketHoldReturn("m", MARKET)
    assert node.fingerprint() == MARKET
    with pytest.raises(ConfigError, match="cape"):
        MarketHoldReturn("m", {**MARKET, "cape": 0.0})
    with pytest.raises(ConfigError, match="reversion_years"):
        MarketHoldReturn("m", {**MARKET, "reversion_years": 0})
    with pytest.raises(ConfigError, match="missing"):
        MarketHoldReturn("m", {k: v for k, v in MARKET.items() if k != "inflation"})
    with pytest.raises(ConfigError, match="unknown"):
        MarketHoldReturn("m", {**MARKET, "alpha": 0.1})


def test_the_horizon_cannot_outrun_the_reversion(ctx):
    # the drift is spread over reversion_years; a longer horizon would compound it past full reversion
    MarketHoldReturn("m", {**MARKET, "horizon_years": 10})            # equal is allowed
    with pytest.raises(ConfigError, match="horizon_years.*exceeds reversion_years.*past full reversion"):
        MarketHoldReturn("m", {**MARKET, "horizon_years": 11})
    with pytest.raises(ConfigError, match="missing.*horizon_years"):
        MarketHoldReturn("m", {k: v for k, v in MARKET.items() if k != "horizon_years"})
    with pytest.raises(ConfigError, match="horizon_years"):
        MarketHoldReturn("m", {**MARKET, "horizon_years": 0})
    with pytest.raises(ConfigError, match="horizon_years"):
        MarketHoldReturn("m", {**MARKET, "horizon_years": 2.5})
    # the cap is the shared one: 100 years is allowed only with a reversion that long
    MarketHoldReturn("m", {**MARKET, "horizon_years": 100, "reversion_years": 100})
    with pytest.raises(ConfigError, match="horizon_years.*at most 100"):
        MarketHoldReturn("m", {**MARKET, "horizon_years": 101, "reversion_years": 101})
    # the drift formula is untouched by the horizon
    one = MarketHoldReturn("m", {**MARKET, "horizon_years": 1}).run(ctx, {})["rate"]
    assert one == pytest.approx(MarketHoldReturn("m", MARKET).run(ctx, {})["rate"])


def test_a_market_rate_outside_the_bounds_refuses(ctx):
    # cape 1 against a fair 1000 in one year: valuation +999, a rate of ~1000x
    wild = {**MARKET, "cape": 1.0, "fair_cape": 1000.0, "reversion_years": 1, "horizon_years": 1}
    with pytest.raises(ValueError, match="market rate.*outside.*fractions.*percent"):
        MarketHoldReturn("m", wild).run(ctx, {})
    # fair CAPE a thousandth of today's in one year: valuation -0.999, rate
    # 0.013 + 0.02 + 0.025 - 0.999 = -0.941, inside [-1, 2], so it runs
    low = {**MARKET, "cape": 1000.0, "fair_cape": 1.0, "reversion_years": 1, "horizon_years": 1}
    assert MarketHoldReturn("m", low).run(ctx, {})["rate"] == pytest.approx(-0.941)
    # a market priced at a third of fair for one year: valuation (30 / 10) - 1 = +2.0, so the
    # rate is 0.058 + 2.0 = 2.058, past the +200% ceiling a fraction can plausibly reach
    third = {**MARKET, "cape": 10.0, "fair_cape": 30.0, "reversion_years": 1, "horizon_years": 1}
    with pytest.raises(ValueError, match="market rate.*2.058.*outside"):
        MarketHoldReturn("m", third).run(ctx, {})


BETAS = {
    "risk_free": 0.04,
    "betas": {"VTI": 1.0, "AAPL": 1.2},
    "symbol_field": "symbol",
}
ROWS = [{"symbol": "VTI"}, {"symbol": "AAPL"}, {"symbol": "VTI"}]


def test_beta_rates_by_hand(ctx):
    out = BetaHoldReturns("b", BETAS).run(ctx, {"records": ROWS, "market": 0.10})
    assert out["rates"] == {"VTI": pytest.approx(0.10), "AAPL": pytest.approx(0.112)}
    assert out["summary"] == {"stage": "beta hold returns", "symbols": 2,
                              "market": 0.10, "risk_free": 0.04, "defaulted": []}


def test_beta_rates_follow_the_declared_risk_free_by_hand(ctx):
    # risk_free 0.02, market 0.10: the excess is 0.08, VTI 0.02 + 1.0 x 0.08 = 0.10 and
    # AAPL 0.02 + 1.2 x 0.08 = 0.116 (at risk_free 0.04 it was 0.112); a beta of 0 earns the
    # risk-free rate itself and a beta of 0.5 half the excess, 0.02 + 0.04 = 0.06
    params = {**BETAS, "risk_free": 0.02, "betas": {"VTI": 1.0, "AAPL": 1.2, "CASH": 0.0,
                                                    "BOND": 0.5}}
    rows = [{"symbol": s} for s in ("VTI", "AAPL", "CASH", "BOND")]
    out = BetaHoldReturns("b", params).run(ctx, {"records": rows, "market": 0.10})
    assert out["rates"] == {"VTI": pytest.approx(0.10), "AAPL": pytest.approx(0.116),
                            "CASH": pytest.approx(0.02), "BOND": pytest.approx(0.06)}
    assert out["summary"]["risk_free"] == 0.02
    # a negative risk-free rate is a legitimate declaration: -0.01 + 1.2 x 0.11 = 0.122
    neg = BetaHoldReturns("b", {**BETAS, "risk_free": -0.01}).run(
        ctx, {"records": [{"symbol": "AAPL"}], "market": 0.10})
    assert neg["rates"]["AAPL"] == pytest.approx(0.122)


def test_a_symbol_without_a_beta_refuses_unless_a_default_is_declared(ctx):
    rows = [{"symbol": "NEW"}]
    with pytest.raises(ValueError, match="row 0.*beta.*'NEW'"):
        BetaHoldReturns("b", BETAS).run(ctx, {"records": rows, "market": 0.10})
    out = BetaHoldReturns("b", {**BETAS, "default_beta": 1.0}).run(
        ctx, {"records": rows, "market": 0.10})
    assert out["rates"] == {"NEW": pytest.approx(0.10)}
    assert out["summary"]["defaulted"] == ["NEW"]


def test_a_computed_beta_rate_outside_the_bounds_refuses_by_symbol(ctx):
    # 0.04 + 200 x (0.10 - 0.04) = 12.04 > 2; 0.04 - 100 x 0.06 = -5.96 < -1;
    # 0.04 + 40 x 0.06 = 2.44 > 2
    for beta in (200.0, -100.0, 40.0):
        node = BetaHoldReturns("b", {**BETAS, "betas": {"VTI": 1.0, "WILD": beta}})
        with pytest.raises(ValueError, match="rate for 'WILD'.*outside.*fractions.*percent"):
            node.run(ctx, {"records": [{"symbol": "VTI"}, {"symbol": "WILD"}], "market": 0.10})
    # a defaulted beta is held to the same bound
    node = BetaHoldReturns("b", {**BETAS, "default_beta": 200.0})
    with pytest.raises(ValueError, match="rate for 'NEW'"):
        node.run(ctx, {"records": [{"symbol": "NEW"}], "market": 0.10})
    # inclusive at the +200% ceiling: risk_free 0, market 0.5, beta 4 -> 4 x 0.5 = 2.0
    ceiling = {**BETAS, "risk_free": 0.0}
    node = BetaHoldReturns("b", {**ceiling, "betas": {"HIGH": 4.0}})
    assert node.run(ctx, {"records": [{"symbol": "HIGH"}], "market": 0.5})["rates"]["HIGH"] == 2.0
    # and just past it, beta 4.2 -> 2.1, refuses; the floor is inclusive too: beta -2 -> -1.0
    node = BetaHoldReturns("b", {**ceiling, "betas": {"HIGH": 4.2}})
    with pytest.raises(ValueError, match="rate for 'HIGH'.*2.1"):
        node.run(ctx, {"records": [{"symbol": "HIGH"}], "market": 0.5})
    node = BetaHoldReturns("b", {**ceiling, "betas": {"LOW": -2.0}})
    assert node.run(ctx, {"records": [{"symbol": "LOW"}], "market": 0.5})["rates"]["LOW"] == -1.0


def test_beta_inputs_and_declarations_refuse_by_name(ctx):
    node = BetaHoldReturns("b", BETAS)
    with pytest.raises(ValueError, match="market"):
        node.run(ctx, {"records": ROWS, "market": "ten"})
    with pytest.raises(ValueError, match="row 0.*symbol"):
        node.run(ctx, {"records": [{"ticker": "VTI"}], "market": 0.1})
    with pytest.raises(ConfigError, match="betas"):
        BetaHoldReturns("b", {**BETAS, "betas": {"VTI": "one"}})
    with pytest.raises(ConfigError, match="betas"):
        BetaHoldReturns("b", {**BETAS, "betas": {}})


def test_hold_vs_redeploy_takes_wired_rates_over_the_param(ctx):
    params = {"horizon_years": 1, "hold_return": 0.0, "redeploy_return": 0.0,
              "redeploy_cost": 0.0, "risk_premium": 0.0, "value_field": "v",
              "net_field": "n", "symbol_field": "symbol"}
    rows = [{"symbol": "VTI", "v": 100.0, "n": 100.0}]
    out = HoldVsRedeploy("d", params).run(
        ctx, {"records": rows, "hold_returns": {"VTI": 0.5}})
    assert out["records"][0]["hold_value"] == pytest.approx(150.0)
    assert out["summary"]["hold_return_source"] == "wired"
    out = HoldVsRedeploy("d", params).run(ctx, {"records": rows})
    assert out["summary"]["hold_return_source"] == "param"
    with pytest.raises(ValueError, match="row 0.*hold_return.*'VTI'"):
        HoldVsRedeploy("d", params).run(ctx, {"records": rows, "hold_returns": {}})
    with pytest.raises(ValueError, match="hold_returns"):
        HoldVsRedeploy("d", params).run(ctx, {"records": rows, "hold_returns": [0.5]})
    without = {k: v for k, v in params.items() if k != "hold_return"}
    assert HoldVsRedeploy("d", without).run(
        ctx, {"records": rows, "hold_returns": {"VTI": 0.5}})["records"][0]["decision"] == "hold"
    with pytest.raises(ValueError, match="no hold_return param"):
        HoldVsRedeploy("d", without).run(ctx, {"records": rows})
