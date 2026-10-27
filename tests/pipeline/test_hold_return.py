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


def test_a_symbol_without_a_beta_refuses_unless_a_default_is_declared(ctx):
    rows = [{"symbol": "NEW"}]
    with pytest.raises(ValueError, match="row 0.*beta.*'NEW'"):
        BetaHoldReturns("b", BETAS).run(ctx, {"records": rows, "market": 0.10})
    out = BetaHoldReturns("b", {**BETAS, "default_beta": 1.0}).run(
        ctx, {"records": rows, "market": 0.10})
    assert out["rates"] == {"NEW": pytest.approx(0.10)}
    assert out["summary"]["defaulted"] == ["NEW"]


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
