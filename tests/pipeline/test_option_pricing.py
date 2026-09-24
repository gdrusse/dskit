"""Black-76 and the vol-index smile quote model (``option_pricing.py``).

Moved from the index_options child (ADR-0182 tier placement amendment):
the pricing is venue-neutral, so it lives in the core and the child
imports it. The expected values are written out independently of the
class under test.
"""

import math

import pytest

from dskit.pipeline.option_pricing import RIGHTS, VolIndexSmileQuotes, black76

YEARS = 21 / 252

#: (atm_ratio, put_skew_per_z, call_skew_per_z, smile_curvature).
SMILE = (0.8, 0.2, -0.05, 0.04)
#: (iv_floor, iv_ceiling, half_spread_min, half_spread_frac, rate).
REST = (0.05, 2.0, 0.05, 0.03, 0.0)
#: The 2026-09-23 calibration (module docstring), with the 2.0 ceiling.
CALIBRATED = (0.794, 0.176, -0.064, 0.045, 0.05, 2.0, 0.20, 0.005, 0.0)


def _smile_iv(z, level=20.0, atm_ratio=0.8, put=0.2, call=-0.05, curvature=0.04):
    """The smile written out independently of ``VolIndexSmileQuotes``."""
    wing = put * -z if z < 0 else call * z
    return level / 100 * (atm_ratio + wing + curvature * z ** 2)


def _strike(z, forward=1000.0, level=20.0, years=YEARS):
    return forward * math.exp(z * level / 100 * math.sqrt(years))


# -- black76 ----------------------------------------------------------------------------


def test_black76_known_value_and_put_call_parity():
    # ATM, F=K=100, vol 20%, 1y: 100 * (2 N(0.1) - 1)
    assert black76("call", 100.0, 100.0, 0.2, 1.0) == pytest.approx(7.965567, abs=1e-6)
    for strike in (80.0, 100.0, 125.0):
        call = black76("call", 100.0, strike, 0.3, 0.5, rate=0.04)
        put = black76("put", 100.0, strike, 0.3, 0.5, rate=0.04)
        assert call - put == pytest.approx(math.exp(-0.02) * (100.0 - strike))


def test_black76_monotonicity_and_refusals():
    assert RIGHTS == ("put", "call")
    calls = [black76("call", 100.0, k, 0.2, YEARS) for k in (90, 95, 100, 105, 110)]
    puts = [black76("put", 100.0, k, 0.2, YEARS) for k in (90, 95, 100, 105, 110)]
    assert calls == sorted(calls, reverse=True) and puts == sorted(puts)
    assert black76("put", 100.0, 95.0, 0.3, YEARS) > black76("put", 100.0, 95.0, 0.2, YEARS)
    for args in (("fwd", 100.0, 100.0, 0.2, 1.0), ("call", 0.0, 100.0, 0.2, 1.0),
                 ("call", 100.0, 100.0, 0.0, 1.0), ("call", 100.0, 100.0, 0.2, -1.0),
                 ("call", 100.0, float("nan"), 0.2, 1.0)):
        with pytest.raises(ValueError):
            black76(*args)
    with pytest.raises(ValueError, match="rate"):
        black76("call", 100.0, 100.0, 0.2, 1.0, rate=float("inf"))


# -- VolIndexSmileQuotes ----------------------------------------------------------------


def test_atm_iv_is_atm_ratio_times_level():
    quotes = VolIndexSmileQuotes(*SMILE, *REST)
    assert quotes.iv(1000.0, 1000.0, 20.0, YEARS) == pytest.approx(0.8 * 0.20)
    assert quotes.iv(5000.0, 5000.0, 35.0, 0.25) == pytest.approx(0.8 * 0.35)


def test_put_wing_rises_with_depth():
    quotes = VolIndexSmileQuotes(*SMILE, *REST)
    zs = (-0.5, -1.0, -2.0, -3.0)
    ivs = [quotes.iv(1000.0, _strike(z), 20.0, YEARS) for z in zs]
    assert ivs == pytest.approx([_smile_iv(z) for z in zs])
    assert ivs == sorted(ivs) and ivs[0] > quotes.iv(1000.0, 1000.0, 20.0, YEARS)
    # z = -2: 0.2 * (0.8 + 0.4 + 0.16) = 0.272
    assert ivs[2] == pytest.approx(0.272)


def test_call_wing_follows_call_skew_and_curvature():
    zs = (0.5, 1.0, 2.0, 3.0)
    dipping = VolIndexSmileQuotes(0.8, 0.2, -0.05, 0.001, *REST)
    ivs = [dipping.iv(1000.0, _strike(z), 20.0, YEARS) for z in zs]
    assert ivs == pytest.approx([_smile_iv(z, curvature=0.001) for z in zs])
    assert ivs == sorted(ivs, reverse=True)  # the slope wins over this range: dips
    curved = VolIndexSmileQuotes(*SMILE, *REST)
    ivs = [curved.iv(1000.0, _strike(z), 20.0, YEARS) for z in zs]
    assert ivs == pytest.approx([_smile_iv(z) for z in zs])
    # the quadratic lifts the far wing back above ATM: z = 3 -> 0.2 * (0.8 - 0.15 + 0.36)
    assert ivs[-1] == pytest.approx(0.202)
    assert ivs[-1] > curved.iv(1000.0, 1000.0, 20.0, YEARS)
    flat = VolIndexSmileQuotes(1.0, 0.2, 0.0, 0.0, *REST)
    assert flat.iv(1000.0, _strike(2.0), 20.0, YEARS) == pytest.approx(0.2)


def test_floor_applies_to_a_low_level():
    # a positive smile still sits under the floor when the index is low
    quotes = VolIndexSmileQuotes(0.1, 0, 0, 0, 0.3, 2.0, 0, 0, 0)
    assert quotes.iv(1000.0, 1000.0, 20.0, YEARS) == 0.3


def test_ceiling_clamps_a_deep_short_dated_wing():
    """Level 15, one calendar day: the calibrated smile is unbounded in z_K."""
    quotes = VolIndexSmileQuotes(*CALIBRATED)
    day = 1 / 365
    # 10% OTM: z_K = ln(0.9) / (0.15 sqrt(1/365)) ~ -13.42 -> ~169% IV, under 2.0
    z10 = math.log(0.9) / (0.15 * math.sqrt(day))
    unclamped = 0.15 * (0.794 + 0.176 * -z10 + 0.045 * z10 ** 2)
    assert unclamped == pytest.approx(1.6889, abs=1e-4)
    assert quotes.iv(5000.0, 4500.0, 15.0, day) == pytest.approx(unclamped)
    # a tighter ceiling clamps that same leg
    tight = VolIndexSmileQuotes(*CALIBRATED[:5], 1.0, *CALIBRATED[6:])
    assert tight.iv(5000.0, 4500.0, 15.0, day) == 1.0
    # 15% OTM: ~356% before the clamp; the configs' 2.0 ceiling decides
    assert quotes.iv(5000.0, 4250.0, 15.0, day) == 2.0
    bid, mid, ask = quotes.quote("put", 5000.0, 4250.0, 15.0, day)
    assert mid == pytest.approx(black76("put", 5000.0, 4250.0, 2.0, day))


def test_spread():
    quotes = VolIndexSmileQuotes(*SMILE, *REST)
    bid, mid, ask = quotes.quote("put", 1000.0, 950.0, 20.0, YEARS)
    z = math.log(950 / 1000) / (0.2 * math.sqrt(YEARS))
    assert mid == pytest.approx(black76("put", 1000.0, 950.0, _smile_iv(z), YEARS))
    assert ask - mid == pytest.approx(max(0.05, 0.03 * mid)) == pytest.approx(mid - bid)
    far_bid, far_mid, far_ask = quotes.quote("call", 1000.0, 1400.0, 20.0, YEARS)
    assert far_bid == 0.0 and far_ask == pytest.approx(far_mid + 0.05)


def test_level_must_be_positive():
    quotes = VolIndexSmileQuotes(*SMILE, *REST)
    for level in (0.0, -5.0, float("nan"), None):
        with pytest.raises(ValueError, match="level"):
            quotes.iv(1000.0, 1000.0, level, YEARS)


@pytest.mark.parametrize("knobs", [
    (0.8, -0.1, 0.0, 0.0, *REST), (0.8, 0.1, 0.0, 0.0, 0.0, *REST[1:]),
    (0.8, 0.1, 0.0, 0.0, 0.05, 2.0, -1, 0.03, 0.0), (0.8, 0.1, 0.0, 0.0, 0.05, 2.0, 0.05, 1.0, 0.0),
    (0.8, 0.1, 0.0, 0.0, 0.05, 2.0, 0.05, 0.03, float("inf")),
    (0.8, True, 0.0, 0.0, *REST),
    (0.0, 0.1, 0.0, 0.0, *REST), (-0.8, 0.1, 0.0, 0.0, *REST),
    (float("nan"), 0.1, 0.0, 0.0, *REST),
    (0.8, 0.1, float("inf"), 0.0, *REST),
    (0.8, 0.1, "-0.1", 0.0, *REST),
    (0.8, 0.1, 0.0, -0.01, *REST),
    (0.8, 0.1, 0.0, float("nan"), *REST),
    (0.8, 0.1, 0.0, 0.0, 0.05, 0.0, 0.05, 0.03, 0.0),   # ceiling not positive
    (0.8, 0.1, 0.0, 0.0, 0.05, 0.05, 0.05, 0.03, 0.0),  # ceiling == floor
    (0.8, 0.1, 0.0, 0.0, 0.5, 0.4, 0.05, 0.03, 0.0),    # ceiling below floor
])
def test_knob_refusal(knobs):
    assert VolIndexSmileQuotes.problems(*knobs)
    with pytest.raises(ValueError):
        VolIndexSmileQuotes(*knobs)


@pytest.mark.parametrize("smile, wing", [
    ((0.8, 0.2, -1.0, 0.0), "call"),     # a straight negative call wing: unbounded
    ((0.8, 0.2, -0.05, 0.0), "call"),    # however shallow, with no curvature
    ((0.5, 0.2, -0.5, 0.1), "call"),     # minimum 0.5 - 0.25/0.4 = -0.125 at z = 2.5
    ((0.5, 0.2, -0.2, 0.02), "call"),    # minimum exactly 0: still refused
])
def test_a_smile_that_dips_to_zero_before_the_floor_refuses(smile, wing):
    problems = VolIndexSmileQuotes.problems(*smile, *REST)
    assert problems and f"the {wing} wing" in problems[0]
    with pytest.raises(ValueError, match="floor never masks"):
        VolIndexSmileQuotes(*smile, *REST)


def test_the_calibrated_smile_stays_positive():
    assert VolIndexSmileQuotes.problems(*CALIBRATED) == []
    # the call wing's minimum: 0.794 - 0.064^2 / (4 * 0.045) ~ 0.771 at z ~ 0.71
    quotes = VolIndexSmileQuotes(*CALIBRATED)
    z_min = 0.064 / (2 * 0.045)
    iv = quotes.iv(1000.0, _strike(z_min, level=20.0), 20.0, YEARS)
    assert iv == pytest.approx(0.2 * (0.794 - 0.064 ** 2 / (4 * 0.045)))


def test_knobs_list_the_constructor_order():
    quotes = VolIndexSmileQuotes(*CALIBRATED)
    assert tuple(getattr(quotes, k) for k in VolIndexSmileQuotes.KNOBS) == CALIBRATED
