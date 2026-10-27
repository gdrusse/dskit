"""DecisionOutcomes (ADR-0279 Stage 4): score an earlier hold forecast against a later snapshot.

Growth is per share (value / quantity) so a buy or sell between the two dates
does not masquerade as a return; without a quantity field the value itself is
the measure. The forecast is a TOTAL return (price + income) and the realized
side is price only, so the projection removes ``income_rate`` a year before it
is compounded: projected = (g - income_rate) ** elapsed, g = (hold / value) **
(1 / horizon). Every number here is worked by hand.
"""

import re

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
    "income_rate": 0.013,
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
    # VTI: total growth per year g = 1.2^(1/5) = e^(0.18232/5) = 1.037137; less the 1.3% income
    # leaves 1.024137 price growth a year; over 365/365.25 of a year that is 1.024121
    assert vti["projected_growth"] == pytest.approx((1.2 ** 0.2 - 0.013) ** elapsed)
    assert vti["projected_growth"] == pytest.approx(1.024121, abs=1e-6)
    assert vti["realized_growth"] == pytest.approx((5500.0 / 20.0) / (2500.0 / 10.0))   # 1.1
    assert vti["surprise"] == pytest.approx(1.1 - 1.024121, abs=1e-6)
    assert aapl["realized_growth"] == pytest.approx(0.9)
    # AAPL: g = 1.5^(1/5) = 1.084472, p = 1.071472, ^0.999316 = 1.071421
    assert aapl["projected_growth"] == pytest.approx(1.071421, abs=1e-6)
    assert aapl["decision"] == "exit"
    assert vti["account"] == "A1" and vti["symbol"] == "VTI"
    assert out["summary"]["scored"] == 2
    assert out["summary"]["unmatched"] == []
    assert out["summary"]["mean_surprise"] == pytest.approx(
        (vti["surprise"] + aapl["surprise"]) / 2)
    assert out["summary"]["by_decision"]["exit"]["mean_surprise"] == pytest.approx(aapl["surprise"])
    assert out["summary"]["by_decision"]["hold"]["count"] == 1


def test_income_is_removed_before_compounding_by_hand(ctx):
    # 2026-10-27 -> 2030-10-27 is 1461 days = 4 x 365.25, so elapsed is exactly 4.0 years.
    # Every hold is worth 10000 now; per-year TOTAL growth g, income 1%, price growth g - .01.
    params = {k: v for k, v in PARAMS.items() if k != "quantity_field"}
    params["income_rate"] = 0.01
    top = 10000.0 * 1.05 ** 5          # 12762.815625: g = 1.05 over the 5-year horizon
    decided = [
        {"account": "A", "symbol": "UP", "as_of": "2026-10-27", "market_value": 10000.0,
         "hold_value": top, "horizon_years": 5, "decision": "hold"},
        {"account": "A", "symbol": "MID", "as_of": "2026-10-27", "market_value": 10000.0,
         "hold_value": top, "horizon_years": 5, "decision": "hold"},
        {"account": "A", "symbol": "FLAT", "as_of": "2026-10-27", "market_value": 10000.0,
         "hold_value": 10000.0, "horizon_years": 5, "decision": "exit"},
    ]
    later = [
        {"account": "A", "symbol": "UP", "as_of": "2030-10-27", "market_value": 13000.0},
        {"account": "A", "symbol": "MID", "as_of": "2030-10-27", "market_value": 11000.0},
        {"account": "A", "symbol": "FLAT", "as_of": "2030-10-27", "market_value": 9000.0},
    ]
    out = DecisionOutcomes("o", params).run(ctx, {"decisions": decided, "positions": later})
    up, mid, flat = out["records"]
    assert up["elapsed_years"] == 4.0
    # g = 1.05, price growth 1.04, over 4 years: 1.04^2 = 1.0816, 1.0816^2 = 1.16985856
    assert up["projected_growth"] == pytest.approx(1.16985856)
    assert mid["projected_growth"] == pytest.approx(1.16985856)
    # FLAT: g = 1, price growth .99, .99^2 = .9801, .9801^2 = .96059601
    assert flat["projected_growth"] == pytest.approx(0.96059601)
    # surprise = realized - projected: 1.3 - 1.16985856, 1.1 - 1.16985856, .9 - .96059601
    assert up["surprise"] == pytest.approx(0.13014144)
    assert mid["surprise"] == pytest.approx(-0.06985856)
    assert flat["surprise"] == pytest.approx(-0.06059601)
    by = out["summary"]["by_decision"]
    # two holds with DIFFERENT surprises: mean (.13014144 - .06985856) / 2 = .03014144
    assert by["hold"]["count"] == 2
    assert by["hold"]["mean_surprise"] == pytest.approx(0.03014144)
    assert by["exit"] == {"count": 1, "mean_surprise": pytest.approx(-0.06059601)}
    # all three: (.06028288 - .06059601) / 3 = -.00031313 / 3
    assert out["summary"]["mean_surprise"] == pytest.approx(-0.00031313 / 3)
    # no income to remove: projected is the plain pro-rata total growth, 1.05^4 = 1.21550625
    plain = DecisionOutcomes("o", {**params, "income_rate": 0.0}).run(
        ctx, {"decisions": decided[:1], "positions": later[:1]})
    assert plain["records"][0]["projected_growth"] == pytest.approx(1.21550625)


def test_income_that_swallows_the_growth_refuses_by_decision(ctx):
    # hold_value = 10000 x .5^5 = 312.5 over 5 years makes g exactly .5 a year, so an income of
    # 60% a year would leave a price factor of -0.1 and one of 50% leaves exactly 0: neither
    # has a growth path
    params = {k: v for k, v in PARAMS.items() if k != "quantity_field"}
    row = {"account": "A", "symbol": "UP", "as_of": "2026-10-27", "market_value": 10000.0,
           "hold_value": 10000.0 * 0.5 ** 5, "horizon_years": 5, "decision": "hold"}
    later = [{"account": "A", "symbol": "UP", "as_of": "2027-10-27", "market_value": 5000.0}]
    for income in (0.6, 0.5):
        with pytest.raises(ValueError, match=f"decision 0.*income_rate {income}.*price growth"):
            DecisionOutcomes("o", {**params, "income_rate": income}).run(
                ctx, {"decisions": [row], "positions": later})
    # an income below g runs: p = .5 - .4 = .1, and .1^(365/365.25) = 0.1003...
    out = DecisionOutcomes("o", {**params, "income_rate": 0.4}).run(
        ctx, {"decisions": [row], "positions": later})
    assert out["records"][0]["projected_growth"] == pytest.approx(0.1 ** (365 / 365.25))
    # a price that is forecast to FALL while income is paid is legitimate: g = 1.05, income 6%
    # leaves a factor of .99, and .99^(365/365.25) = .98999...
    grow = {**row, "hold_value": 10000.0 * 1.05 ** 5}
    out = DecisionOutcomes("o", {**params, "income_rate": 0.06}).run(
        ctx, {"decisions": [grow], "positions": later})
    assert out["records"][0]["projected_growth"] == pytest.approx(0.99 ** (365 / 365.25))


def test_an_undecided_later_row_is_never_validated_or_scored(ctx):
    # positions nobody decided on: a closed lot (zero quantity), a short (zero long quantity,
    # negative value), a row with no quantity at all and one with a text value. None of them
    # is scored, so none may refuse the run; the decided rows score exactly as without them.
    extra = [
        {"account": "A9", "symbol": "ZERO", "as_of": "2027-10-27", "market_value": 0.0,
         "long_quantity": 0.0},
        {"account": "A9", "symbol": "SHORT", "as_of": "2027-10-27", "market_value": -180.0,
         "long_quantity": 0.0},
        {"account": "A9", "symbol": "NOQTY", "as_of": "2027-10-27", "market_value": 5.0},
        {"account": "A9", "symbol": "TEXT", "as_of": "2027-10-27", "market_value": "n/a",
         "long_quantity": 1.0},
    ]
    node = DecisionOutcomes("o", PARAMS)
    plain = node.run(ctx, {"decisions": DECISIONS, "positions": LATER})
    out = node.run(ctx, {"decisions": DECISIONS, "positions": extra[:2] + LATER + extra[2:]})
    assert [r["symbol"] for r in out["records"]] == ["VTI", "AAPL"]
    assert out["records"] == plain["records"]
    assert out["summary"] == plain["summary"]
    assert out["summary"]["unmatched"] == []


def test_the_later_snapshot_keeps_its_structural_refusals_for_every_row(ctx):
    # an undecided row still has to be addressable, dated, unique and on the one date
    node = DecisionOutcomes("o", PARAMS)
    zero = {"account": "A9", "symbol": "ZERO", "as_of": "2027-10-27", "market_value": 0.0,
            "long_quantity": 0.0}
    with pytest.raises(ValueError, match="positions carry key.*several"):
        node.run(ctx, {"decisions": DECISIONS, "positions": LATER + [zero, zero]})
    with pytest.raises(ValueError, match="position 3.*as_of"):
        node.run(ctx, {"decisions": DECISIONS, "positions": LATER + [{**zero, "as_of": "soon"}]})
    with pytest.raises(ValueError, match="position 3.*key field 'symbol'"):
        node.run(ctx, {"decisions": DECISIONS, "positions": LATER + [
            {k: v for k, v in zero.items() if k != "symbol"}]})
    with pytest.raises(ValueError, match="one as_of.*2027-10-27.*2027-10-28"):
        node.run(ctx, {"decisions": DECISIONS, "positions": LATER + [
            {**zero, "as_of": "2027-10-28"}]})
    # a MATCHED row is validated, and the message names its position in the snapshot
    with pytest.raises(ValueError, match="position 1.*zero"):
        node.run(ctx, {"decisions": DECISIONS, "positions": [
            zero, {**LATER[0], "long_quantity": 0.0}, LATER[1]]})
    with pytest.raises(ValueError, match="position 0.*market_value.*not a number"):
        node.run(ctx, {"decisions": DECISIONS, "positions": [
            {**LATER[0], "market_value": "n/a"}, LATER[1]]})


def test_horizon_years_is_read_from_the_decision_row_by_hand(ctx):
    # horizon 3: hold_value 1331 = 1000 x 1.1^3, so the total growth is 1.1 a year; income 5%
    # leaves a price growth of 1.05; 2026-10-27 -> 2030-10-27 is 1461 days = exactly 4 years,
    # and 1.05^4 = 1.21550625. Realized 1300 / 1000 = 1.3, so the surprise is 0.08449375.
    params = {k: v for k, v in PARAMS.items() if k != "quantity_field"}
    params["income_rate"] = 0.05
    row = {"account": "A", "symbol": "H3", "as_of": "2026-10-27", "market_value": 1000.0,
           "hold_value": 1331.0, "horizon_years": 3, "decision": "hold"}
    later = [{"account": "A", "symbol": "H3", "as_of": "2030-10-27", "market_value": 1300.0}]
    node = DecisionOutcomes("o", params)
    scored = node.run(ctx, {"decisions": [row], "positions": later})["records"][0]
    assert scored["elapsed_years"] == 4.0
    assert scored["projected_growth"] == pytest.approx(1.21550625)
    assert scored["surprise"] == pytest.approx(0.08449375)
    # the same hold_value read over 5 years is a different forecast: 1.331^0.2 = 1.058853, less
    # the income 1.008853, ^4 = 1.035884
    five = node.run(ctx, {"decisions": [{**row, "horizon_years": 5}], "positions": later})
    assert five["records"][0]["projected_growth"] == pytest.approx(1.035884, abs=1e-5)
    # and a one-year horizon: g = 1331 / 1000 = 1.331, price growth 1.281, ^4 = 2.6928
    one = node.run(ctx, {"decisions": [{**row, "horizon_years": 1}], "positions": later})
    assert one["records"][0]["projected_growth"] == pytest.approx(1.281 ** 4)


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
    with pytest.raises(ValueError, match="positions carry key.*several"):
        node.run(ctx, {"decisions": DECISIONS, "positions": LATER + [LATER[0]]})
    with pytest.raises(ValueError, match="one as_of.*2027-10-27.*2027-10-28"):
        node.run(ctx, {"decisions": DECISIONS, "positions": LATER + [
            {**LATER[2], "symbol": "X", "as_of": "2027-10-28"}]})
    with pytest.raises(ValueError, match="position 0.*zero"):
        node.run(ctx, {"decisions": DECISIONS,
                       "positions": [{**LATER[0], "long_quantity": 0.0}]})
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
    with pytest.raises(ConfigError, match="missing.*income_rate"):
        DecisionOutcomes("o", {k: v for k, v in PARAMS.items() if k != "income_rate"})
    for bad in (-0.01, 1.01, True, "0.01", float("nan")):
        with pytest.raises(ConfigError, match="income_rate"):
            DecisionOutcomes("o", {**PARAMS, "income_rate": bad})
    DecisionOutcomes("o", {**PARAMS, "income_rate": 0})
    DecisionOutcomes("o", {**PARAMS, "income_rate": 1})


def test_every_adr_0279_kind_serves_pure():
    for cls in (LiquidationTax, HoldVsRedeploy, MarketHoldReturn, BetaHoldReturns,
                ActiveHurdle, DecisionOutcomes):
        assert cls.serving_effect({}, None) == "pure", cls.__name__


@pytest.mark.parametrize("bad", [0, -1, 0.0, -0.5])
def test_a_non_positive_horizon_refuses_by_decision(ctx, bad):
    # a zero horizon would divide by zero and a negative one invert the forecast
    with pytest.raises(ValueError, match="decision 0 has non-positive horizon_years"):
        DecisionOutcomes("o", PARAMS).run(
            ctx, {"decisions": [{**DECISIONS[0], "horizon_years": bad}], "positions": LATER})


@pytest.mark.parametrize("bad", ["five", True, None, float("nan"), float("inf"), [5]])
def test_a_non_numeric_horizon_is_refused_through_the_one_number_reader(ctx, bad):
    with pytest.raises(ValueError) as caught:
        DecisionOutcomes("o", PARAMS).run(
            ctx, {"decisions": [{**DECISIONS[0], "horizon_years": bad}], "positions": LATER})
    assert str(caught.value) == f"o: decision 0 field 'horizon_years' is not a number, got {bad!r}"
    # a decision with no horizon at all is the same refusal
    row = {k: v for k, v in DECISIONS[0].items() if k != "horizon_years"}
    with pytest.raises(ValueError, match="decision 0 field 'horizon_years' is not a number, got None"):
        DecisionOutcomes("o", PARAMS).run(ctx, {"decisions": [row], "positions": LATER})


def test_an_empty_later_snapshot_says_none_not_several(ctx):
    with pytest.raises(ValueError) as caught:
        DecisionOutcomes("o", PARAMS).run(ctx, {"decisions": DECISIONS, "positions": []})
    assert "exactly one as_of" in str(caught.value) and "got none" in str(caught.value)
    assert "several" not in str(caught.value)


RENAME = {"account": "acct", "symbol": "tkr", "as_of": "day", "market_value": "mv",
          "long_quantity": "q"}


def _renamed(rows):
    return [{RENAME.get(key, key): value for key, value in row.items()} for row in rows]


def test_renamed_fields_give_the_same_outcomes_by_hand(ctx):
    # test_outcomes_by_hand with account / symbol / as_of / market_value / long_quantity renamed;
    # hold_value, horizon_years and decision are the HoldVsRedeploy output names and stay
    params = {"key_fields": ["acct", "tkr"], "as_of_field": "day", "value_field": "mv",
              "quantity_field": "q", "income_rate": 0.013}
    out = DecisionOutcomes("o", params).run(
        ctx, {"decisions": _renamed(DECISIONS), "positions": _renamed(LATER)})
    vti, aapl = out["records"]
    assert (vti["acct"], vti["tkr"], aapl["tkr"]) == ("A1", "VTI", "AAPL")
    assert "account" not in vti and "symbol" not in vti
    # VTI: (1.2^0.2 - .013)^(365/365.25) = 1.024121; (5500 / 20) / (2500 / 10) = 1.1
    assert vti["projected_growth"] == pytest.approx(1.024121, abs=1e-6)
    assert vti["realized_growth"] == pytest.approx(1.1)
    assert vti["surprise"] == pytest.approx(1.1 - 1.024121, abs=1e-6)
    # AAPL: (1.5^0.2 - .013)^(365/365.25) = 1.071421; 540 / 3 over 600 / 3 = 0.9
    assert aapl["projected_growth"] == pytest.approx(1.071421, abs=1e-6)
    assert aapl["realized_growth"] == pytest.approx(0.9)
    assert (vti["decided_as_of"], vti["observed_as_of"]) == ("2026-10-27", "2027-10-27")
    assert out["summary"]["scored"] == 2 and out["summary"]["unmatched"] == []
    # a decided key the later snapshot lacks is reported under the declared key names
    out = DecisionOutcomes("o", params).run(
        ctx, {"decisions": _renamed(DECISIONS), "positions": _renamed(LATER[1:])})
    assert out["summary"]["unmatched"] == [{"acct": "A1", "tkr": "VTI"}]


OUTCOMES_REQUIRED = ("key_fields", "as_of_field", "value_field", "income_rate")
BAD_OUTCOMES = [
    ({"extra": 1}, "unknown"),
    *[({"key_fields": bad}, "key_fields must be a non-empty list of distinct field names")
      for bad in ("symbol", [], [""], ["a", "a"], [5], None)],
    *[({name: bad}, f"{name} must be a non-empty field name")
      for name in ("as_of_field", "value_field", "quantity_field") for bad in ("", 5, None)],
    *[({"income_rate": bad}, "income_rate must be a number in")
      for bad in (-0.01, 1.01, True, "0.01", float("nan"))],
]


@pytest.mark.parametrize("over, match", BAD_OUTCOMES)
def test_each_bad_outcomes_declaration_is_one_named_problem(over, match):
    problems = DecisionOutcomes.validate_params({**PARAMS, **over})
    assert len(problems) == 1 and re.search(match, problems[0]), problems


@pytest.mark.parametrize("name", OUTCOMES_REQUIRED)
def test_each_outcomes_param_is_named_when_missing(name):
    problems = DecisionOutcomes.validate_params({k: v for k, v in PARAMS.items() if k != name})
    assert problems == [f"missing required params ['{name}']"]


def test_inputs_must_be_lists_of_mappings_and_a_decision_must_be_dated(ctx):
    node = DecisionOutcomes("o", PARAMS)
    with pytest.raises(ValueError, match="decisions must be a list"):
        node.run(ctx, {"decisions": None, "positions": LATER})
    with pytest.raises(ValueError, match="positions must be a list"):
        node.run(ctx, {"decisions": DECISIONS, "positions": None})
    with pytest.raises(ValueError, match="decision 1 must be a mapping, got list"):
        node.run(ctx, {"decisions": [DECISIONS[0], [1, 2]], "positions": LATER})
    with pytest.raises(ValueError, match="position 3 must be a mapping, got str"):
        node.run(ctx, {"decisions": DECISIONS, "positions": LATER + ["VTI"]})
    for bad in ("soon", None, 20261027):
        with pytest.raises(ValueError, match="decision 0 field 'as_of' is not an ISO date"):
            node.run(ctx, {"decisions": [{**DECISIONS[0], "as_of": bad}], "positions": LATER})
