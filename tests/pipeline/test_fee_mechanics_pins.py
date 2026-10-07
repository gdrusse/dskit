"""Boundary pin for the ceil-to-tick rounding guard (ADR-0242): ``guard_decimals`` may be 0, never negative.

A mutation pass found the lower bound of the guard width unpinned (``< 0`` could become ``<= 0`` or ``< 1`` and every test
passed). Only the acceptance and the on-grid result are pinned: what a guard of 0 does to an OFF-grid amount (it snaps to the
nearest whole tick before the ceiling) is TODO F4, an owner decision, and is deliberately not frozen here.
"""

import pytest

from dskit.pipeline.fee_mechanics import CeilToTick, fee_model_from_spec

TICK = 0.01


def test_a_guard_of_zero_places_is_accepted_and_leaves_an_on_grid_fee_alone():
    policy = CeilToTick(TICK, guard_decimals=0)
    assert policy.guard_decimals == 0
    assert policy.round_fee(0.03) == pytest.approx(0.03)
    assert policy.round_fee(0.0) == 0.0


@pytest.mark.parametrize("guard", [-1, -9])
def test_a_negative_guard_width_is_refused_and_a_zero_one_is_not(guard):
    with pytest.raises(ValueError, match="guard_decimals"):
        CeilToTick(TICK, guard_decimals=guard)


def test_a_spec_may_carry_a_guard_of_zero_places():
    spec = {"mechanic": "probability_quadratic", "rounding": {"policy": "ceil_to_tick", "tick": TICK, "guard_decimals": 0}}
    assert fee_model_from_spec(spec).rounding.guard_decimals == 0
