"""Leg-set algebra and quote defects (``option_structures.py``).

Graduated from the index_options child (ADR-0265 node 4): the leg algebra is
venue-neutral, so the core owns it and the child re-exports it. The leg sets
here are test data written out by hand, including the post-solve strategy taxonomy.
Expected numbers are computed independently of the functions under test.
"""

import math
from decimal import Decimal

import pytest

from dskit.pipeline import option_structures
from dskit.pipeline.option_structures import (
    leg_intrinsic,
    quote_defect,
    structure_credit,
    structure_max_loss,
    structure_payoff,
)

PUT_SPREAD = (("put", 1), ("put", -1))
STRADDLE = (("put", 1), ("call", 1))


def test_the_public_surface_is_exactly_the_declared_names():
    assert set(option_structures.__all__) == {
        "QUOTE_DEFECTS", "classify_option_strategy", "is_amount", "leg_intrinsic", "payoff_total", "quote_defect",
        "quote_defects", "structure_credit", "structure_max_loss", "structure_payoff"}


def test_a_short_leg_sells_at_the_bid_and_a_long_leg_buys_at_the_ask():
    credit, widths = structure_credit(PUT_SPREAD, (95.0, 100.0), ((1.0, 1.1), (2.0, 2.2)))
    assert credit == pytest.approx(2.0 - 1.1)
    assert widths == (5.0,)


def test_a_debit_structure_has_a_negative_credit_and_no_width():
    credit, widths = structure_credit(STRADDLE, (100.0, 100.0), ((2.0, 2.4), (3.0, 3.5)))
    assert credit == pytest.approx(-(2.4 + 3.5))
    assert widths == ()


def test_credit_refuses_strikes_or_quotes_that_do_not_match_the_legs():
    with pytest.raises(ValueError):
        structure_credit(PUT_SPREAD, (95.0,), ((1.0, 1.1), (2.0, 2.2)))
    with pytest.raises(ValueError):
        structure_credit(PUT_SPREAD, (95.0, 100.0), ((1.0, 1.1),))


@pytest.mark.parametrize("right,strike,level,want", [
    ("put", 100.0, 93.0, 7.0), ("put", 100.0, 107.0, 0.0),
    ("call", 100.0, 107.0, 7.0), ("call", 100.0, 93.0, 0.0)])
def test_intrinsic_value_by_hand(right, strike, level, want):
    assert leg_intrinsic(right, strike, level) == want


def test_payoff_is_the_signed_sum_of_intrinsic_values():
    assert structure_payoff(PUT_SPREAD, 93.0, (90.0, 95.0)) == -2.0
    assert structure_payoff(STRADDLE, 93.0, (100.0, 100.0)) == 7.0


def test_payoff_keeps_decimal_exact():
    got = structure_payoff(PUT_SPREAD, Decimal("93.1"), (Decimal("90"), Decimal("95")))
    assert got == Decimal("-1.9")
    assert isinstance(got, Decimal)


def test_max_loss_of_a_credit_spread_is_width_less_credit():
    assert structure_max_loss(PUT_SPREAD, (90.0, 95.0), 150.0, 100) == 500.0 - 150.0


def test_max_loss_of_a_long_structure_is_the_debit_and_a_naked_call_is_unbounded():
    assert structure_max_loss(STRADDLE, (100.0, 100.0), -650.0, 100) == 650.0
    assert structure_max_loss((("call", -1),), (100.0,), 200.0, 100) == math.inf


@pytest.mark.parametrize("legs,strikes,credit,multiplier", [
    ((), (), 1.0, 100), (PUT_SPREAD, (90.0,), 1.0, 100),
    (PUT_SPREAD, (90.0, -5.0), 1.0, 100), (PUT_SPREAD, (90.0, 95.0), float("nan"), 100),
    (PUT_SPREAD, (90.0, 95.0), 1.0, 0), (PUT_SPREAD, (90.0, 95.0), 1.0, 100.5)])
def test_max_loss_refuses_a_malformed_input(legs, strikes, credit, multiplier):
    with pytest.raises(ValueError):
        structure_max_loss(legs, strikes, credit, multiplier)


def test_payoff_total_equals_the_sum_of_payoffs_over_levels():
    levels = [85.0, 92.0, 99.0, 110.0]
    assert option_structures.payoff_total(PUT_SPREAD, levels, (90.0, 95.0)) == pytest.approx(
        sum(structure_payoff(PUT_SPREAD, level, (90.0, 95.0)) for level in levels))


@pytest.mark.parametrize("bid,ask,side,locked_ok,want", [
    (1.0, 1.2, "buy", False, None), (1.0, 1.2, "sell", False, None),
    (1.0, 1.2, None, False, None),
    (None, 1.2, None, False, "not_finite"), (float("nan"), 1.2, "buy", False, "not_finite"),
    (1.0, float("inf"), None, False, "not_finite"), (True, 1.2, None, False, "not_finite"),
    (-0.1, 1.2, None, False, "negative"), (1.0, -1.0, None, False, "negative"),
    (1.3, 1.2, None, False, "crossed"), (1.3, 1.2, "buy", True, "crossed"),
    (1.2, 1.2, "buy", False, "locked"), (1.2, 1.2, None, False, "locked"),
    (1.2, 1.2, "buy", True, None),
    (0.0, 0.4, "sell", False, "sold_leg_no_bid"), (0.0, 0.4, "buy", False, None),
    (0.0, 0.0, "buy", False, "bought_leg_no_ask"), (0.0, 0.0, "sell", False, "sold_leg_no_bid"),
    (0.0, 0.0, None, True, None)])
def test_quote_defect_codes(bid, ask, side, locked_ok, want):
    assert quote_defect(bid, ask, side, locked_ok=locked_ok) == want


def test_quote_defect_codes_are_the_declared_vocabulary():
    assert set(option_structures.QUOTE_DEFECTS) == {
        "not_finite", "negative", "crossed", "locked", "sold_leg_no_bid", "bought_leg_no_ask"}


def test_quote_defect_refuses_an_unknown_side():
    with pytest.raises(ValueError):
        quote_defect(1.0, 1.1, "hold")


@pytest.mark.parametrize("bid,ask,side,want", [
    (1.0, 1.2, "buy", []), (None, 1.2, "sell", ["not_finite"]),
    (-1.0, 1.2, "sell", ["negative", "sold_leg_no_bid"]),
    (-1.0, 1.2, "buy", ["negative"]), (1.3, 1.2, "buy", ["crossed"]),
    (0.0, 0.0, "buy", ["bought_leg_no_ask"]), (0.0, 0.4, "sell", ["sold_leg_no_bid"]),
    (1.2, 1.2, "buy", ["locked"])])
def test_quote_defects_lists_every_defect_and_quote_defect_is_the_first(bid, ask, side, want):
    got = option_structures.quote_defects(bid, ask, side)
    assert got == want
    assert quote_defect(bid, ask, side) == (want[0] if want else None)


# -- post-solve diagnostic taxonomy: expected anatomy written independently --------------------

@pytest.mark.parametrize("book,name,family,variant", [
    ([("call", 3, 100)], "long_call", "single_option", None),
    ([("call", -3, 100)], "short_call", "single_option", None),
    ([("put", 3, 100)], "long_put", "single_option", None),
    ([("put", -10, 157.5)], "short_put", "single_option", None),
    ([("call", 2, 90), ("call", -2, 110)], "bull_call_spread", "vertical", None),
    ([("call", -2, 90), ("call", 2, 110)], "bear_call_spread", "vertical", None),
    ([("put", 2, 90), ("put", -2, 110)], "bull_put_spread", "vertical", None),
    ([("put", -2, 90), ("put", 2, 110)], "bear_put_spread", "vertical", None),
    ([("call", 2, 90), ("call", -3, 110)], "call_ratio_spread", "ratio_vertical", "more_shorts"),
    ([("call", -2, 90), ("call", 3, 110)], "call_ratio_backspread", "ratio_vertical", "more_longs"),
    ([("put", -3, 90), ("put", 2, 110)], "put_ratio_spread", "ratio_vertical", "more_shorts"),
    ([("put", 3, 90), ("put", -2, 110)], "put_ratio_backspread", "ratio_vertical", "more_longs"),
    ([("call", 3, 90), ("call", -2, 110)], "call_ratio_vertical", "ratio_vertical", "more_longs"),
    ([("put", 2, 90), ("put", -3, 110)], "put_ratio_vertical", "ratio_vertical", "more_shorts"),
    ([("put", 2, 100), ("call", 2, 100)], "long_straddle", "straddle", None),
    ([("put", -2, 100), ("call", -2, 100)], "short_straddle", "straddle", None),
    ([("put", 2, 90), ("call", 2, 110)], "long_strangle", "strangle", None),
    ([("put", -2, 90), ("call", -2, 110)], "short_strangle", "strangle", None),
    ([("put", -2, 100), ("call", 2, 100)], "synthetic_long_forward", "synthetic_forward", None),
    ([("put", 2, 100), ("call", -2, 100)], "synthetic_short_forward", "synthetic_forward", None),
    ([("call", 2, 90), ("call", -4, 100), ("call", 2, 110)], "long_call_butterfly", "butterfly", "equal_wings"),
    ([("put", -2, 90), ("put", 4, 100), ("put", -2, 115)], "short_put_butterfly", "butterfly", "broken_wing"),
    ([("call", 1, 80), ("call", -1, 90), ("call", -1, 110), ("call", 1, 120)],
     "long_call_condor", "condor", "equal_wings"),
    ([("put", -1, 80), ("put", 1, 90), ("put", 1, 110), ("put", -1, 125)],
     "short_put_condor", "condor", "unequal_wings"),
    ([("put", 5, 80), ("put", -5, 90), ("call", -5, 110), ("call", 5, 120)],
     "iron_condor", "iron_condor", "equal_wings"),
    ([("put", -5, 80), ("put", 5, 90), ("call", 5, 110), ("call", -5, 125)],
     "reverse_iron_condor", "iron_condor", "unequal_wings"),
    ([("put", 1, 90), ("put", -1, 100), ("call", -1, 100), ("call", 1, 110)],
     "iron_butterfly", "iron_butterfly", "equal_wings"),
    ([("put", -1, 90), ("put", 1, 100), ("call", 1, 100), ("call", -1, 115)],
     "reverse_iron_butterfly", "iron_butterfly", "unequal_wings"),
    ([("put", -2, 90), ("put", 2, 110), ("call", 2, 90), ("call", -2, 110)],
     "long_box", "box", None),
    ([("put", 2, 90), ("put", -2, 110), ("call", -2, 90), ("call", 2, 110)],
     "short_box", "box", None),
])
def test_option_strategy_labels_exact_anatomy(book, name, family, variant):
    got = option_structures.classify_option_strategy(
        [(right, qty) for right, qty, _ in book], [strike for _, _, strike in book])
    assert (got["name"], got["family"], got["variant"]) == (name, family, variant)
    assert got["version"] == 1
    assert got["input_contract_count"] == got["net_contract_count"] == sum(abs(q) for _, q, _ in book)
    assert all(leg["quantity"] == got["units"] * leg["ratio"] for leg in got["legs"])


@pytest.mark.parametrize("book", [
    [("put", 1, 90), ("call", 2, 110)],  # unequal quantities are not a strangle
    [("put", 1, 110), ("call", 1, 90)],  # crossed strangle geometry
    [("put", 1, 90), ("put", -1, 115), ("call", -1, 110), ("call", 1, 120)],
    [("put", 1, 80), ("put", -1, 90), ("call", -1, 110), ("call", 2, 120)],
    [("put", 1, 80), ("put", -1, 90), ("call", -1, 110), ("call", 1, 120), ("put", 1, 70)],
    [("call", 1, 90), ("call", -3, 100), ("call", 1, 110)],
])
def test_option_strategy_does_not_force_a_partial_or_near_match(book):
    got = option_structures.classify_option_strategy(
        [(right, qty) for right, qty, _ in book], [strike for _, _, strike in book])
    assert got["name"] == got["family"] == "custom"
    assert len(got["legs"]) == len(book)


def test_option_strategy_nets_exact_identities_and_preserves_scale_and_inputs():
    legs = [("call", 3), ("put", -6), ("call", 3), ("put", 6)]
    strikes = [120., 90., 120., 80.]
    got = option_structures.classify_option_strategy(legs, strikes)
    assert got["name"] == "custom" and got["units"] == 6
    assert got["legs"] == [
        {"right": "put", "strike": 80., "quantity": 6, "ratio": 1},
        {"right": "put", "strike": 90., "quantity": -6, "ratio": -1},
        {"right": "call", "strike": 120., "quantity": 6, "ratio": 1}]
    assert got == option_structures.classify_option_strategy(legs[::-1], strikes[::-1])
    assert legs == [("call", 3), ("put", -6), ("call", 3), ("put", 6)]
    assert strikes == [120., 90., 120., 80.]
    reduced = option_structures.classify_option_strategy(
        [("put", 1), ("put", -1), ("call", 1)], [80., 90., 120.])
    assert [leg["ratio"] for leg in reduced["legs"]] == [leg["ratio"] for leg in got["legs"]]


def test_option_strategy_cash_differs_from_an_offsetting_executed_book():
    empty = option_structures.classify_option_strategy([], [])
    flat = option_structures.classify_option_strategy([("call", 3), ("call", -3)], [100., 100.])
    assert empty["name"] == "cash" and empty["input_contract_count"] == 0
    assert flat["name"] == "flat_option_book" and flat["input_contract_count"] == 6
    for got in (empty, flat):
        assert got["legs"] == [] and got["units"] == got["net_contract_count"] == 0
    partly = option_structures.classify_option_strategy([("call", 3), ("call", -2)], [100., 100.])
    assert partly["name"] == "long_call" and partly["units"] == 1
    assert partly["input_contract_count"] == 5 and partly["net_contract_count"] == 1


def test_option_strategy_uses_exact_identity_and_only_tolerates_wing_widths():
    near = option_structures.classify_option_strategy(
        [("call", 1), ("call", -1)], [100., math.nextafter(100., math.inf)])
    assert near["name"] == "bull_call_spread" and len(near["legs"]) == 2
    legs = [("put", 1), ("put", -1), ("call", -1), ("call", 1)]
    near_inner = option_structures.classify_option_strategy(
        legs, [90., 100., math.nextafter(100., math.inf), 110.])
    assert near_inner["name"] == "iron_condor" and near_inner["variant"] == "equal_wings"
    decimal = option_structures.classify_option_strategy(
        [("call", 1), ("call", -2), ("call", 1)],
        [Decimal("1.1"), Decimal("1.2"), Decimal("1.3")])
    assert decimal["variant"] == "equal_wings"


@pytest.mark.parametrize("legs,strikes", [
    (None, []), ([], None), ([("call", 1)], []), ([("stock", 1)], [100]),
    ([("call", True)], [100]), ([("call", 1.)], [100]), ([("call", 0)], [100]),
    ([("call", 1, 2)], [100]), ([None], [100]), ([("call", 1)], [True]),
    ([("call", 1)], ["100"]), ([("call", 1)], [0]), ([("call", 1)], [-1]),
    ([("call", 1)], [float("nan")]), ([("call", 1)], [float("inf")]),
    ([("call", 1)], [Decimal("NaN")]),
])
def test_option_strategy_refuses_malformed_books(legs, strikes):
    with pytest.raises(ValueError):
        option_structures.classify_option_strategy(legs, strikes)


def test_option_strategy_preserves_arbitrary_finite_integer_strikes():
    large = 10**400
    got = option_structures.classify_option_strategy(
        [("call", 1), ("call", -2), ("call", 1)], [large, large+1, large+2])
    assert got["name"] == "long_call_butterfly" and got["variant"] == "equal_wings"
    assert [leg["strike"] for leg in got["legs"]] == [large, large+1, large+2]
