"""Venue-neutral fee mechanics: a per-order fee rule, a rounding policy, a config-built model.

Every expected value below is worked by hand from the stated formula. Nothing here names a
venue: the mechanics are a quadratic-in-probability fee and a ceil-to-tick rounding, and a
project that has never heard of the problem they came from can use them.
"""

import math

import pytest

from dskit.pipeline import fee_mechanics
from dskit.pipeline.fee_mechanics import (
    DEFAULT_GUARD_DECIMALS,
    FEE_MECHANICS,
    ROUNDING_POLICIES,
    CeilToTick,
    FeeModel,
    NoRounding,
    ProbabilityQuadraticFee,
    RoundingPolicy,
    ZeroFee,
    fee_model_from_spec,
    fee_spec_problems,
)

TICK = 0.01
RATE = 0.07


def quadratic():
    return ProbabilityQuadraticFee(CeilToTick(TICK))


# -- the quadratic rule and the per-order ceiling ----------------------------------------


def test_one_contract_pays_a_whole_tick_floor():
    # 0.07 * 1 * 0.5 * 0.5 = 0.0175 = 1.75 ticks of 0.01 -> rounds UP to 2 ticks
    assert quadratic().order_fee(1, 0.5, RATE) == pytest.approx(0.02)


def test_float_dust_does_not_buy_an_extra_tick():
    # 0.07 * 100 * 0.5 * 0.5 * 100 is 175.00000000000003 in binary floating point: a naive
    # ceiling bills 1.76 where the exact amount is 1.75
    assert math.ceil(0.07 * 100 * 0.5 * (1 - 0.5) * 100) == 176, "premise: a naive ceil overshoots"
    assert quadratic().order_fee(100, 0.5, RATE) == pytest.approx(1.75)


def test_a_larger_order_rounds_the_total_not_each_contract():
    # 0.07 * 100 * 0.3 * 0.7 = 1.47 exactly, while 100 one-contract orders would each be
    # 0.07 * 0.21 = 0.0147 -> 0.02 and cost 2.00
    fee = quadratic()
    assert fee.order_fee(100, 0.3, RATE) == pytest.approx(1.47)
    assert 100 * fee.order_fee(1, 0.3, RATE) == pytest.approx(2.00)


def test_the_rate_scales_the_fee_and_the_ends_are_free():
    fee = quadratic()
    assert fee.order_fee(100, 0.5, 0.035) == pytest.approx(0.88)  # 0.875 -> 0.88
    assert fee.order_fee(100, 0.0, RATE) == 0.0
    assert fee.order_fee(100, 1.0, RATE) == 0.0
    assert fee.order_fee(0, 0.5, RATE) == 0.0
    assert fee.order_fee(100, 0.5, 0.0) == 0.0


def test_unrounded_quadratic_is_the_bare_formula():
    # rate * C * P * (1 - P) with no policy: 0.07 * 100 * 0.44 * 0.56 = 1.7248
    assert ProbabilityQuadraticFee().order_fee(100, 0.44, RATE) == pytest.approx(1.7248)
    assert ProbabilityQuadraticFee(NoRounding()).order_fee(100, 0.44, RATE) == pytest.approx(1.7248)
    # and the same order, ceilinged to a tick of 0.01: 172.48 ticks -> 173
    assert quadratic().order_fee(100, 0.44, RATE) == pytest.approx(1.73)


@pytest.mark.parametrize("contracts, price, rate, expected", [
    (1, 0.5, 0.07, 0.02), (1, 0.44, 0.07, 0.02), (100, 0.5, 0.07, 1.75), (100, 0.3, 0.07, 1.47),
    (100, 0.5, 0.035, 0.88), (100, 0.44, 0.07, 1.73), (100, 0.44, 0.035, 0.87),
    (100, 0.6, 0.07, 1.68)])  # the last is 1.6800000000000002 in floating point: the guard keeps it at 1.68
def test_hand_worked_orders_on_a_grid_of_one_hundredth(contracts, price, rate, expected):
    # reference values worked by hand from ceil(rate * C * P * (1 - P) * 100) / 100, written down
    # independently of the code, and the same values a project's own fee tests carry
    assert quadratic().order_fee(contracts, price, rate) == pytest.approx(expected, abs=1e-12)


def test_the_fee_is_symmetric_about_one_half():
    fee = ProbabilityQuadraticFee()
    assert fee.order_fee(10, 0.2, RATE) == pytest.approx(fee.order_fee(10, 0.8, RATE))


# -- the ceil-to-tick policy on its own ---------------------------------------------------


@pytest.mark.parametrize("tick, exact, expected", [
    (0.01, 0.0175, 0.02), (0.01, 1.75, 1.75), (0.01, 0.0, 0.0),
    (0.01, 1.7500000000000002, 1.75),   # one ulp of dust is absorbed by the guard
    (0.01, 1.7500001, 1.76),            # a real sub-tick overage is billed
    (0.25, 0.3, 0.5), (0.25, 0.5, 0.5), (0.25, 0.51, 0.75),
    (0.001, 0.0021, 0.003), (1.0, 2.0000001, 3.0)])
def test_ceil_to_tick_rounds_up_to_the_grid_after_the_guard(tick, exact, expected):
    assert CeilToTick(tick).round_fee(exact) == pytest.approx(expected)


def test_the_guard_width_is_a_knob_with_one_named_default():
    assert DEFAULT_GUARD_DECIMALS == 9
    assert CeilToTick(TICK).guard_decimals == DEFAULT_GUARD_DECIMALS
    # 1.7500001 dollars is 175.00001 ticks: a guard of 4 decimals reads it as exactly 175 ticks
    assert CeilToTick(TICK, guard_decimals=4).round_fee(1.7500001) == pytest.approx(1.75)
    assert CeilToTick(TICK, guard_decimals=9).round_fee(1.7500001) == pytest.approx(1.76)


@pytest.mark.parametrize("tick", [0, -0.01, float("nan"), float("inf"), None, "0.01", True])
def test_a_tick_that_is_not_a_positive_finite_number_is_refused(tick):
    with pytest.raises(ValueError, match="tick"):
        CeilToTick(tick)


@pytest.mark.parametrize("guard", [-1, 1.5, True, None, "9"])
def test_a_guard_that_is_not_a_non_negative_integer_is_refused(guard):
    with pytest.raises(ValueError, match="guard_decimals"):
        CeilToTick(TICK, guard_decimals=guard)


@pytest.mark.parametrize("exact", [-0.01, float("nan"), float("inf"), None])
def test_a_policy_refuses_an_amount_that_is_not_a_non_negative_number(exact):
    for policy in (CeilToTick(TICK), NoRounding()):
        with pytest.raises(ValueError, match="exact"):
            policy.round_fee(exact)


def test_no_rounding_returns_the_exact_amount():
    assert NoRounding().round_fee(1.7248) == 1.7248


# -- zero fee and the order check --------------------------------------------------------


def test_the_zero_model_charges_nothing_for_any_valid_order():
    for model in (ZeroFee(), ZeroFee(CeilToTick(TICK))):
        assert model.order_fee(100, 0.5, RATE) == 0.0
        assert model.order_fee(0, 0.5, RATE) == 0.0


@pytest.mark.parametrize("contracts, price, rate", [
    (-1, 0.5, RATE), (1.5, 0.5, RATE), (True, 0.5, RATE), (None, 0.5, RATE),
    (1, 1.5, RATE), (1, -0.1, RATE), (1, None, RATE), (1, float("nan"), RATE),
    (1, 0.5, -RATE), (1, 0.5, float("nan")), (1, 0.5, None), (1, 0.5, True)])
def test_a_fill_no_formula_may_price_is_refused_by_every_model(contracts, price, rate):
    for model in (quadratic(), ProbabilityQuadraticFee(), ZeroFee()):
        with pytest.raises(ValueError):
            model.order_fee(contracts, price, rate)


def test_the_rate_is_a_call_argument_never_a_property_of_the_model():
    # one model object prices two series that carry different rates
    model = quadratic()
    assert model.order_fee(100, 0.5, 0.07) != model.order_fee(100, 0.5, 0.035)


# -- the seams ----------------------------------------------------------------------------


def test_fee_model_and_rounding_policy_are_abstract():
    with pytest.raises(TypeError):
        FeeModel()
    with pytest.raises(TypeError):
        RoundingPolicy()

    class NoHook(FeeModel):
        pass

    with pytest.raises(TypeError):
        NoHook()


def test_a_new_mechanic_is_a_subclass_and_inherits_the_validation_and_rounding():
    class FlatPerContract(FeeModel):
        def _exact_fee(self, contracts, price, rate):
            return rate * contracts

    model = FlatPerContract(CeilToTick(0.05))
    assert model.order_fee(3, 0.5, 0.02) == pytest.approx(0.10)  # 0.06 -> next 0.05 tick
    with pytest.raises(ValueError):
        model.order_fee(3, 1.5, 0.02)


def test_a_non_policy_is_refused_at_construction():
    with pytest.raises(ValueError, match="rounding"):
        ProbabilityQuadraticFee(0.01)


def test_a_new_policy_is_a_subclass():
    class WholeUnits(RoundingPolicy):
        def round_fee(self, exact):
            return float(math.ceil(exact))

    assert ProbabilityQuadraticFee(WholeUnits()).order_fee(100, 0.5, RATE) == 2.0


# -- configuration -----------------------------------------------------------------------


SPEC = {"mechanic": "probability_quadratic", "rounding": {"policy": "ceil_to_tick", "tick": 0.01}}


def test_the_vocabulary_is_the_mechanics_own_and_closed():
    assert set(FEE_MECHANICS) == {"probability_quadratic", "zero"}
    assert set(ROUNDING_POLICIES) == {"none", "ceil_to_tick"}
    assert FEE_MECHANICS["probability_quadratic"] is ProbabilityQuadraticFee
    with pytest.raises(TypeError):
        FEE_MECHANICS["mine"] = ZeroFee  # read-only: a project registers its own by import, not by editing this


def test_a_spec_builds_the_same_model_as_the_constructor():
    built = fee_model_from_spec(SPEC)
    assert isinstance(built, ProbabilityQuadraticFee) and isinstance(built.rounding, CeilToTick)
    for contracts, price in ((1, 0.5), (100, 0.5), (100, 0.3), (100, 0.44)):
        assert built.order_fee(contracts, price, RATE) == quadratic().order_fee(contracts, price, RATE)


def test_a_spec_may_choose_the_zero_mechanic_and_no_rounding():
    model = fee_model_from_spec({"mechanic": "zero", "rounding": {"policy": "none"}, "notes": "free tier"})
    assert isinstance(model, ZeroFee) and model.order_fee(5, 0.5, RATE) == 0.0
    exact = fee_model_from_spec({"mechanic": "probability_quadratic", "rounding": {"policy": "none"}})
    assert exact.order_fee(100, 0.44, RATE) == pytest.approx(1.7248)


def test_a_spec_carries_the_guard_width():
    spec = {"mechanic": "probability_quadratic",
            "rounding": {"policy": "ceil_to_tick", "tick": 0.01, "guard_decimals": 4}}
    assert fee_model_from_spec(spec).rounding.guard_decimals == 4


@pytest.mark.parametrize("spec, fragment", [
    ("quadratic", "spec"),
    ({}, "mechanic"),
    ({"mechanic": "quadratic", "rounding": {"policy": "none"}}, "quadratic"),
    ({"mechanic": "zero"}, "rounding"),
    ({"mechanic": "zero", "rounding": "ceil"}, "rounding"),
    ({"mechanic": "zero", "rounding": {}}, "policy"),
    ({"mechanic": "zero", "rounding": {"policy": "nearest"}}, "nearest"),
    ({"mechanic": "zero", "rounding": {"policy": "ceil_to_tick"}}, "tick"),
    ({"mechanic": "zero", "rounding": {"policy": "ceil_to_tick", "tick": 0}}, "tick"),
    ({"mechanic": "zero", "rounding": {"policy": "ceil_to_tick", "tick": 0.01, "guard": 9}}, "guard"),
    ({"mechanic": "zero", "rounding": {"policy": "none", "tick": 0.01}}, "tick"),
    ({"mechanic": "zero", "rounding": {"policy": "none"}, "rate": 0.07}, "rate"),
])
def test_a_bad_spec_is_refused_by_name_and_every_problem_is_listed(spec, fragment):
    problems = fee_spec_problems(spec)
    assert problems and any(fragment in p for p in problems), problems
    with pytest.raises(ValueError, match=fragment):
        fee_model_from_spec(spec)


def test_a_good_spec_has_no_problems_and_problems_accumulate():
    assert fee_spec_problems(SPEC) == []
    both = fee_spec_problems({"mechanic": "mystery", "rounding": {"policy": "mystery"}})
    assert len(both) == 2


def test_the_module_declares_its_public_api_and_leaks_no_underscore_name():
    assert set(fee_mechanics.__all__) == {
        "DEFAULT_GUARD_DECIMALS", "FEE_MECHANICS", "ROUNDING_POLICIES", "CeilToTick", "FeeModel",
        "NoRounding", "ProbabilityQuadraticFee", "RoundingPolicy", "ZeroFee", "fee_model_from_spec",
        "fee_spec_problems"}
    assert not [n for n in fee_mechanics.__all__ if n.startswith("_")]
