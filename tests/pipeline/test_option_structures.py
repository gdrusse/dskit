"""Leg-set algebra and quote defects (``option_structures.py``).

Graduated from the index_options child (ADR-0265 node 4): the leg algebra is
venue-neutral, so the core owns it and the child re-exports it. The leg sets
here are test data written out by hand; the module ships no named structure.
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
        "QUOTE_DEFECTS", "is_amount", "leg_intrinsic", "payoff_total", "quote_defect",
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
