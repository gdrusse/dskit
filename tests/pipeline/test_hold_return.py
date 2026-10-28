"""MarketHoldReturn and BetaHoldReturns (ADR-0279, research A0001): worked by hand.

The market rate is yield + growth + inflation + annualized CAPE reversion; a
stock's rate is risk_free + beta x (market - risk_free). Both are wired by
import path. A symbol without a beta refuses unless a default is declared.
"""

import re

import pytest

from dskit.pipeline import liquidation
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
                              "market": 0.10, "risk_free": 0.04, "defaulted": [],
                              "betas_source": "param", "betas_param_ignored": False}
    assert out["betas"] == {"VTI": 1.0, "AAPL": 1.2}


def test_a_wired_beta_table_replaces_the_param_by_hand(ctx):
    # risk_free 0.04, market 0.10, excess 0.06: VTI 0.04 + 0.9 x 0.06 = 0.094 and AAPL
    # 0.04 + 1.5 x 0.06 = 0.13, though the param says 1.0 and 1.2; SPY is in the table, not held
    wired = {"VTI": 0.9, "AAPL": 1.5, "SPY": 1.0}
    out = BetaHoldReturns("b", BETAS).run(ctx, {"records": ROWS, "market": 0.10, "betas": wired})
    assert out["rates"] == {"VTI": pytest.approx(0.094), "AAPL": pytest.approx(0.13)}
    assert out["betas"] == {"VTI": 0.9, "AAPL": 1.5}
    assert out["summary"]["betas_source"] == "wired"
    # the param is optional once the table is wired, and the same numbers come out
    without = {k: v for k, v in BETAS.items() if k != "betas"}
    assert BetaHoldReturns.validate_params(without) == []
    again = BetaHoldReturns("b", without).run(ctx, {"records": ROWS, "market": 0.10,
                                                    "betas": wired})
    assert again["rates"] == out["rates"] and again["summary"]["betas_source"] == "wired"
    with pytest.raises(ValueError, match="no betas param and no betas input"):
        BetaHoldReturns("b", without).run(ctx, {"records": ROWS, "market": 0.10})


def test_a_declared_betas_param_the_wired_table_replaces_is_reported_not_silent(ctx, caplog):
    """Two sources of one number: the wired table wins, and a hand table declared beside it
    says so in the summary and in the log, so a stale hand beta is never mistaken for the one used."""
    wired = {"VTI": 0.9, "AAPL": 1.5}
    with caplog.at_level("WARNING", logger="dskit.pipeline.b"):
        out = BetaHoldReturns("b", BETAS).run(ctx, {"records": ROWS, "market": 0.10, "betas": wired})
    assert out["summary"]["betas_source"] == "wired" and out["summary"]["betas_param_ignored"] is True
    (record,) = [r for r in caplog.records if r.name == "dskit.pipeline.b" and r.levelname == "WARNING"]
    assert "betas param (2 symbol(s)) is ignored" in record.getMessage()
    assert "betas input is wired" in record.getMessage()
    # nothing to report when only one source exists
    caplog.clear()
    without = {k: v for k, v in BETAS.items() if k != "betas"}
    with caplog.at_level("WARNING", logger="dskit.pipeline.b"):
        wired_only = BetaHoldReturns("b", without).run(
            ctx, {"records": ROWS, "market": 0.10, "betas": wired})
        param_only = BetaHoldReturns("b", BETAS).run(ctx, {"records": ROWS, "market": 0.10})
    assert wired_only["summary"]["betas_param_ignored"] is False
    assert param_only["summary"]["betas_param_ignored"] is False
    assert [r for r in caplog.records if r.levelname == "WARNING"] == []


def test_a_wired_beta_table_refuses_what_is_not_a_number_by_symbol(ctx):
    node = BetaHoldReturns("b", BETAS)
    for bad in (None, [1.0], "VTI", 1.0):
        with pytest.raises(ValueError, match="betas input must be a {symbol: number} mapping"):
            node.run(ctx, {"records": ROWS, "market": 0.10, "betas": bad})
    for entry in (None, True, "1.0", float("nan"), float("inf")):
        with pytest.raises(ValueError, match=r"betas input\['NEWCO'\] must be a number"):
            node.run(ctx, {"records": ROWS, "market": 0.10,
                           "betas": {"VTI": 1.0, "AAPL": 1.2, "NEWCO": entry}})
    with pytest.raises(ValueError, match="betas input keys must be non-empty symbols"):
        node.run(ctx, {"records": ROWS, "market": 0.10, "betas": {"VTI": 1.0, 5: 1.0}})
    # a held symbol the wired table lacks still refuses, unless a default is declared
    with pytest.raises(ValueError, match="row 1 has no beta for symbol 'AAPL'"):
        node.run(ctx, {"records": ROWS, "market": 0.10, "betas": {"VTI": 1.0}})
    out = BetaHoldReturns("b", {**BETAS, "default_beta": 1.0}).run(
        ctx, {"records": ROWS, "market": 0.10, "betas": {"VTI": 0.9}})
    assert out["rates"]["AAPL"] == pytest.approx(0.10) and out["summary"]["defaulted"] == ["AAPL"]
    # and a wired beta is held to the same rate bound: 0.04 + 40 x 0.06 = 2.44 > 2
    with pytest.raises(ValueError, match="rate for 'AAPL'.*outside"):
        node.run(ctx, {"records": ROWS, "market": 0.10, "betas": {"VTI": 1.0, "AAPL": 40.0}})


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


def test_each_market_block_is_read_from_its_own_param(ctx):
    # a fairly valued market (cape == fair_cape) has no valuation block, so the three declared
    # blocks stand alone, each a different size from the shipped .013 / .02 / .025:
    # yield .02 + growth .03 + inflation .01 = .06
    fair = {**MARKET, "cape": 27.0, "dividend_yield": 0.02, "real_growth": 0.03,
            "inflation": 0.01, "horizon_years": 7}
    out = MarketHoldReturn("m", fair).run(ctx, {})
    assert out["blocks"] == {"dividend_yield": 0.02, "real_growth": 0.03,
                             "inflation": 0.01, "valuation": 0.0}
    assert out["rate"] == pytest.approx(0.06)
    summary = out["summary"]
    assert (summary["dividend_yield"], summary["real_growth"], summary["inflation"]) == (
        0.02, 0.03, 0.01)
    assert summary["horizon_years"] == 7
    # change exactly one block: the rate moves by that block alone
    # yield .02 -> .05 gives .05 + .03 + .01 = .09; growth .03 -> .07 gives .02 + .07 + .01 = .10;
    # inflation .01 -> .04 gives .02 + .03 + .04 = .09
    for name, value, rate in (("dividend_yield", 0.05, 0.09), ("real_growth", 0.07, 0.10),
                              ("inflation", 0.04, 0.09)):
        out = MarketHoldReturn("m", {**fair, name: value}).run(ctx, {})
        assert out["blocks"][name] == value
        assert out["rate"] == pytest.approx(rate)
    # with the CAPE drift on: 0.75 ** 0.1 - 1 = -0.0283584, so .06 - .0283584 = .0316416
    drift = MarketHoldReturn("m", {**fair, "cape": 36.0}).run(ctx, {})
    assert drift["blocks"]["valuation"] == pytest.approx(-0.0283584, abs=1e-6)
    assert drift["rate"] == pytest.approx(0.0316416, abs=1e-6)


BAD_MARKET = [
    ({"alpha": 0.1}, "unknown"),
    *[({name: bad}, f"{name} must be a number in")
      for name in ("dividend_yield", "real_growth", "inflation")
      for bad in (7, -1.01, True, "x", float("nan"))],
    *[({name: bad}, f"{name} must be a positive number")
      for name in ("cape", "fair_cape")
      for bad in (0.0, -1.0, True, "x", float("nan"), float("inf"))],
    *[({"reversion_years": bad}, "reversion_years must be an int >= 1")
      for bad in (0, 1.5, True, "ten", None)],
    *[({"reversion_years": bad}, "reversion_years must be at most 1000000")
      for bad in (10 ** 6 + 1, 10 ** 400, 1e300)],
    *[({"horizon_years": bad}, "horizon_years must be an int >= 1") for bad in (0, 2.5, True, "5")],
    ({"horizon_years": 101, "reversion_years": 101}, "horizon_years must be at most 100"),
    ({"horizon_years": 11}, "horizon_years .*exceeds reversion_years"),
]


@pytest.mark.parametrize("over, match", BAD_MARKET)
def test_each_bad_market_declaration_is_one_named_problem(over, match):
    problems = MarketHoldReturn.validate_params({**MARKET, **over})
    assert len(problems) == 1 and re.search(match, problems[0]), problems


@pytest.mark.parametrize("name", list(MARKET))
def test_each_market_param_is_named_when_missing(name):
    problems = MarketHoldReturn.validate_params({k: v for k, v in MARKET.items() if k != name})
    assert problems == [f"missing required params ['{name}']"]


def test_the_count_ceiling_is_one_owner_inclusive_and_huge_ints_never_reach_run(ctx):
    assert liquidation.MAX_COUNT == 10 ** 6
    # exactly the ceiling is accepted, and runs: 0.75 ** (1 / 1e6) - 1 = -2.8768e-7
    top = MarketHoldReturn("m", {**MARKET, "cape": 36.0, "reversion_years": 10 ** 6})
    assert top.run(ctx, {})["blocks"]["valuation"] == pytest.approx(-2.8768e-7, rel=1e-3)
    # an int no float can hold used to raise OverflowError at run; now it is a named refusal
    with pytest.raises(ConfigError, match="reversion_years must be at most 1000000"):
        MarketHoldReturn("m", {**MARKET, "reversion_years": 10 ** 400})


BETAS_REQUIRED = ("risk_free", "symbol_field")
BAD_BETAS = [
    ({"surprise": 1}, "unknown"),
    *[({"risk_free": bad}, "risk_free must be a number in") for bad in (7, -1.01, True, "x", None)],
    *[({"betas": bad}, "betas must be a non-empty") for bad in ([1.0], {}, "VTI", None,
                                                                {"": 1.0}, {5: 1.0})],
    *[({"betas": {"VTI": bad}}, r"betas\['VTI'\] must be a number")
      for bad in ("one", True, None, float("nan"))],
    *[({"symbol_field": bad}, "symbol_field must be a non-empty field name")
      for bad in ("", 5, None)],
    *[({"default_beta": bad}, "default_beta must be a number") for bad in ("1", True, None,
                                                                             float("nan"))],
]


@pytest.mark.parametrize("over, match", BAD_BETAS)
def test_each_bad_beta_declaration_is_one_named_problem(over, match):
    problems = BetaHoldReturns.validate_params({**BETAS, **over})
    assert len(problems) == 1 and re.search(match, problems[0]), problems


@pytest.mark.parametrize("name", BETAS_REQUIRED)
def test_each_beta_param_is_named_when_missing(name):
    problems = BetaHoldReturns.validate_params({k: v for k, v in BETAS.items() if k != name})
    assert problems == [f"missing required params ['{name}']"]


def test_beta_declarations_accumulate_and_the_default_beta_may_be_zero_or_negative():
    with pytest.raises(ConfigError) as caught:
        BetaHoldReturns("b", {"risk_free": 7, "betas": {}, "default_beta": "x"})
    assert len(caught.value.errors) == 4          # risk_free, betas, missing symbol_field, default_beta
    for beta in (0, -0.5):
        assert BetaHoldReturns.validate_params({**BETAS, "default_beta": beta}) == []
    assert BetaHoldReturns.validate_params({**BETAS, "betas": {"HEDGE": -1.5}}) == []


def test_a_renamed_symbol_field_gives_the_same_rates(ctx):
    # test_beta_rates_by_hand with the rows' symbol column called "tkr": 0.04 + 1.2 x 0.06 = 0.112
    rows = [{"tkr": "VTI"}, {"tkr": "AAPL"}, {"tkr": "VTI"}]
    out = BetaHoldReturns("b", {**BETAS, "symbol_field": "tkr"}).run(
        ctx, {"records": rows, "market": 0.10})
    assert out["rates"] == {"VTI": pytest.approx(0.10), "AAPL": pytest.approx(0.112)}
    assert out["summary"]["symbols"] == 2
    # the default spelling is not read when another is declared
    with pytest.raises(ValueError, match="row 0 has no symbol in field 'tkr'"):
        BetaHoldReturns("b", {**BETAS, "symbol_field": "tkr"}).run(
            ctx, {"records": ROWS, "market": 0.10})


def test_a_defaulted_symbol_in_two_rows_is_listed_once(ctx):
    rows = [{"symbol": "NEW"}, {"symbol": "VTI"}, {"symbol": "NEW"}, {"symbol": "OLD"},
            {"symbol": "NEW"}]
    out = BetaHoldReturns("b", {**BETAS, "default_beta": 0.5}).run(
        ctx, {"records": rows, "market": 0.10})
    # NEW and OLD take the default (0.04 + 0.5 x 0.06 = 0.07), each listed once, sorted
    assert out["summary"]["defaulted"] == ["NEW", "OLD"]
    assert out["summary"]["symbols"] == 3
    assert out["rates"] == {"NEW": pytest.approx(0.07), "VTI": pytest.approx(0.10),
                            "OLD": pytest.approx(0.07)}


def test_beta_records_must_be_a_list_of_mappings_with_a_symbol(ctx):
    node = BetaHoldReturns("b", BETAS)
    for bad in (None, {"symbol": "VTI"}, "VTI"):
        with pytest.raises(ValueError, match="records must be a list, got "):
            node.run(ctx, {"records": bad, "market": 0.10})
    for bad in ([5], ["VTI"], [{"symbol": ""}], [{"symbol": 7}]):
        with pytest.raises(ValueError, match="row 0 has no symbol in field 'symbol'"):
            node.run(ctx, {"records": bad, "market": 0.10})
