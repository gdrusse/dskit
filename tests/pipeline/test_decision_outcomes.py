"""DecisionOutcomes (ADR-0279 Stage 4): score an earlier hold forecast against a later snapshot.

Growth is per share (value / quantity) so a buy or sell between the two dates
does not masquerade as a return; without a quantity field the value itself is
the measure. Every number here is worked by hand.
"""

import pytest

from dskit.pipeline.active_hurdle import ActiveHurdle
from dskit.pipeline.decision_outcomes import DecisionOutcomes
from dskit.pipeline.hold_return import BetaHoldReturns, MarketHoldReturn
from dskit.pipeline.liquidation import HoldVsRedeploy, LiquidationTax
from dskit.pipeline.node import ConfigError, NodeContext


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="do", asof="2027-10-27", run_dir=str(tmp_path))


PARAMS = {
    "key_fields": ["account", "symbol"],
    "as_of_field": "as_of",
    "value_field": "market_value",
    "quantity_field": "long_quantity",
}

DECISIONS = [
    # projected 5-year hold growth 2500 -> 3000 (x1.2); one year in, pro rata x1.2^(1/5)
    {"account": "A1", "symbol": "VTI", "as_of": "2026-10-27", "market_value": 2500.0,
     "long_quantity": 10.0, "hold_value": 3000.0, "horizon_years": 5, "decision": "hold"},
    {"account": "A1", "symbol": "AAPL", "as_of": "2026-10-27", "market_value": 600.0,
     "long_quantity": 3.0, "hold_value": 900.0, "horizon_years": 5, "decision": "exit"},
]
# 365 days later; VTI bought more shares (quantity 20), AAPL unchanged; VTI also held in A2
LATER = [
    {"account": "A1", "symbol": "VTI", "as_of": "2027-10-27", "market_value": 5500.0,
     "long_quantity": 20.0},
    {"account": "A1", "symbol": "AAPL", "as_of": "2027-10-27", "market_value": 540.0,
     "long_quantity": 3.0},
    {"account": "A2", "symbol": "VTI", "as_of": "2027-10-27", "market_value": 10.0,
     "long_quantity": 1.0},
]


def test_outcomes_by_hand(ctx):
    out = DecisionOutcomes("o", PARAMS).run(ctx, {"decisions": DECISIONS, "positions": LATER})
    vti, aapl = out["records"]
    elapsed = 365 / 365.25
    assert vti["elapsed_years"] == pytest.approx(elapsed)
    assert vti["projected_growth"] == pytest.approx(1.2 ** (elapsed / 5))
    assert vti["realized_growth"] == pytest.approx((5500.0 / 20.0) / (2500.0 / 10.0))   # 1.1
    assert vti["surprise"] == pytest.approx(1.1 - 1.2 ** (elapsed / 5))
    assert aapl["realized_growth"] == pytest.approx(0.9)
    assert aapl["projected_growth"] == pytest.approx(1.5 ** (elapsed / 5))
    assert aapl["decision"] == "exit"
    assert vti["account"] == "A1" and vti["symbol"] == "VTI"
    assert out["summary"]["scored"] == 2
    assert out["summary"]["unmatched"] == []
    assert out["summary"]["mean_surprise"] == pytest.approx(
        (vti["surprise"] + aapl["surprise"]) / 2)
    assert out["summary"]["by_decision"]["exit"]["mean_surprise"] == pytest.approx(aapl["surprise"])
    assert out["summary"]["by_decision"]["hold"]["count"] == 1


def test_a_decision_without_a_later_row_is_unmatched_not_scored(ctx):
    out = DecisionOutcomes("o", PARAMS).run(ctx, {"decisions": DECISIONS, "positions": LATER[1:]})
    assert [r["symbol"] for r in out["records"]] == ["AAPL"]
    assert out["summary"]["unmatched"] == [{"account": "A1", "symbol": "VTI"}]


def test_without_a_quantity_field_value_is_the_measure(ctx):
    params = {k: v for k, v in PARAMS.items() if k != "quantity_field"}
    out = DecisionOutcomes("o", params).run(ctx, {"decisions": DECISIONS, "positions": LATER})
    assert out["records"][0]["realized_growth"] == pytest.approx(5500.0 / 2500.0)


def test_unusable_rows_and_declarations_refuse(ctx):
    node = DecisionOutcomes("o", PARAMS)
    with pytest.raises(ValueError, match="decision 0.*horizon_years"):
        node.run(ctx, {"decisions": [{**DECISIONS[0], "horizon_years": "five"}],
                       "positions": LATER})
    with pytest.raises(ValueError, match="position 0.*as_of"):
        node.run(ctx, {"decisions": DECISIONS, "positions": [{**LATER[0], "as_of": "later"}]})
    with pytest.raises(ValueError, match="before"):
        node.run(ctx, {"decisions": DECISIONS,
                       "positions": [{**LATER[0], "as_of": "2026-10-27"}]})
    with pytest.raises(ValueError, match="several"):
        node.run(ctx, {"decisions": DECISIONS, "positions": LATER + [LATER[0]]})
    with pytest.raises(ValueError, match="decisions carry key.*several"):
        node.run(ctx, {"decisions": DECISIONS + [DECISIONS[0]], "positions": LATER})
    with pytest.raises(ValueError, match="decision 0.*key field 'account'"):
        node.run(ctx, {"decisions": [{k: v for k, v in DECISIONS[0].items() if k != "account"}],
                       "positions": LATER})
    with pytest.raises(ValueError, match="decision 0.*non-positive"):
        node.run(ctx, {"decisions": [{**DECISIONS[0], "market_value": 0.0}], "positions": LATER})
    with pytest.raises(ValueError, match="decision 0.*'decision'"):
        node.run(ctx, {"decisions": [{**DECISIONS[0], "decision": "maybe"}], "positions": LATER})
    with pytest.raises(ConfigError, match="missing"):
        DecisionOutcomes("o", {"key_fields": ["symbol"]})
    with pytest.raises(ConfigError, match="key_fields"):
        DecisionOutcomes("o", {**PARAMS, "key_fields": ["symbol", "symbol"]})
    with pytest.raises(ConfigError, match="unknown"):
        DecisionOutcomes("o", {**PARAMS, "extra": 1})


def test_every_adr_0279_kind_serves_pure():
    for cls in (LiquidationTax, HoldVsRedeploy, MarketHoldReturn, BetaHoldReturns,
                ActiveHurdle, DecisionOutcomes):
        assert cls.serving_effect({}, None) == "pure", cls.__name__
