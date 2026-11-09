"""HedgeOverlay, NullHedgeOverlay and FixedDeltaForwardHedge (ADR-0312 S1).

Synthetic items and chains only. Expected values are worked out here from Black-76 and
hand arithmetic, never from the overlay under test. Child wiring (P1-P6 against the study) is
S3 and later; the parity checks here are the core half: the Null overlay and ratio 0 change
nothing in the structure functions.
"""

import math
from datetime import date, timedelta

import pytest

from dskit.pipeline import hedge_overlay as ho
from dskit.pipeline.base import ConfigError
from dskit.pipeline.hedge_overlay import FixedDeltaForwardHedge, HedgeOverlay, NullHedgeOverlay
from dskit.pipeline.option_pricing import black76, black76_delta
from dskit.pipeline.option_structures import structure_max_loss, structure_payoff

DAY = "2026-03-02"
EXPIRY = (date.fromisoformat(DAY) + timedelta(days=30)).isoformat()
YEARS = 30 / 365
FORWARD, SPOT, VOL, MULT = 101.5, 100.3, 0.2, 100

BLOCK = {"kind": "fixed_delta_forward", "mode": "program", "ratio": 1.0,
         "delta_source": "black76_chain_forward", "iv_source": "vendor", "rate": 0.0,
         "parity_strikes": 3, "margin_fraction": 0.15, "round_units": True,
         "cost_per_unit": 0.0, "condition": None}


def block(**over):
    return {**BLOCK, **over}


def chain(forward=FORWARD, strikes=(96, 98, 100, 102, 104, 106), half=0.05):
    rows = []
    for k in strikes:
        for right in ("call", "put"):
            mid = black76(right, forward, k, VOL, YEARS)
            rows.append({"right": right, "strike": k, "bid": mid - half, "ask": mid + half})
    return rows


def quote(right, strike, forward=FORWARD):
    mid = black76(right, forward, strike, VOL, YEARS)
    return {"bid": mid - 0.05, "ask": mid + 0.05, "iv": VOL}


def item(legs=(("call", 1),), strikes=(100.0,), with_iv=True):
    quotes = [quote(right, k) for (right, _), k in zip(legs, strikes)]
    if not with_iv:
        quotes[0] = {k: v for k, v in quotes[0].items() if k != "iv"}
    return {"legs": legs, "strikes": strikes, "quotes": quotes, "expiry": EXPIRY}


CONTEXT = {"rows": chain(), "spot": SPOT, "day": DAY, "multiplier": MULT}


def contract_delta(right, strike, sign=1):
    """Spot-term delta in shares per contract, from the chain rule, worked here independently."""
    return sign * MULT * black76_delta(right, FORWARD, strike, VOL, YEARS, 0.0) * FORWARD / SPOT


def test_the_public_surface_is_exactly_the_declared_names():
    assert sorted(ho.__all__) == sorted(
        ["HedgeOverlay", "NullHedgeOverlay", "FixedDeltaForwardHedge", "KINDS", "REFUSALS"])


def test_the_abstract_base_cannot_be_instantiated_and_leaves_units_to_subclasses():
    with pytest.raises(TypeError):
        HedgeOverlay(BLOCK)
    assert getattr(HedgeOverlay.units, "__isabstractmethod__", False)
    assert getattr(HedgeOverlay.reference, "__isabstractmethod__", False)


def test_params_tuples_are_the_adr_tuples_and_the_registry_names_the_kind():
    assert HedgeOverlay._BASE_PARAMS == ("kind", "mode", "ratio", "margin_fraction", "round_units",
                                          "cost_per_unit", "condition")
    assert FixedDeltaForwardHedge._EXTRA_PARAMS == ("delta_source", "iv_source", "rate", "parity_strikes")
    assert ho.KINDS == {"fixed_delta_forward": FixedDeltaForwardHedge}
    assert set(FixedDeltaForwardHedge.params_tuple()) == set(BLOCK)


# ---- Null overlay: identity everywhere -------------------------------------------------
def test_a_method_without_a_hedge_block_builds_the_null_overlay():
    assert isinstance(HedgeOverlay.from_method({"name": "m"}), NullHedgeOverlay)
    assert isinstance(HedgeOverlay.from_method({"hedge": None}), NullHedgeOverlay)


def test_null_overlay_hooks_are_identity_or_zero():
    null = NullHedgeOverlay()
    items = [item(), item()]
    assert null.units(items, CONTEXT) is None
    assert null.reference(CONTEXT) is None
    assert null.floor_rows(items, None, None) == [0.0, 0.0]
    assert null.legs({"contracts": 3}, None, CONTEXT) == []
    assert null.settle([], 105.0) == 0.0 and null.mark([], 105.0) == 0.0
    assert null.record() == {"kind": "none"}
    assert null.structure_hedge(None, MULT, None) is None


def test_null_overlay_leaves_the_structure_functions_byte_identical():
    strikes = (90.0, 95.0, 105.0, 115.0)
    legs = (("put", 1), ("put", -1), ("call", -1), ("call", 1))
    hedge = NullHedgeOverlay().structure_hedge(None, MULT, None)
    for level in (0.0, 92.0, 100.0, 130.0):
        assert structure_payoff(legs, level, strikes, hedge=hedge) == structure_payoff(legs, level, strikes)
    assert structure_max_loss(legs, strikes, 150.0, MULT, hedge=hedge) == structure_max_loss(legs, strikes, 150.0, MULT)


def test_ratio_zero_gives_zero_units_and_unchanged_structure_outputs():
    overlay = FixedDeltaForwardHedge(block(ratio=0.0))
    units = overlay.units([item(), item((("put", -1),), (98.0,))], CONTEXT)
    assert units == [0.0, 0.0]
    assert all(math.copysign(1.0, u) == 1.0 for u in units)       # +0.0, never -0.0
    legs = (("call", 1),)
    hedge = overlay.structure_hedge(units[0], MULT, overlay.reference(CONTEXT))
    for level in (0.0, 99.0, 120.0):
        assert structure_payoff(legs, level, (100.0,), hedge=hedge) == structure_payoff(legs, level, (100.0,))
    assert structure_max_loss(legs, (100.0,), -500.0, MULT, hedge=hedge) == structure_max_loss(legs, (100.0,), -500.0, MULT)
    assert overlay.legs({"contracts": 3}, units[0], CONTEXT)[0]["units"] == 0
    assert overlay.floor_rows([item()], [0.0], 101.5) == [0.0]


# ---- FixedDeltaForwardHedge ----------------------------------------------------------
def test_units_are_minus_ratio_times_the_book_delta_in_shares_per_contract():
    overlay = FixedDeltaForwardHedge(block(ratio=0.5))
    units = overlay.units([item(), item((("put", -1),), (98.0,)),
                           item((("call", 1), ("call", -1)), (100.0, 104.0))], CONTEXT)
    assert units[0] == pytest.approx(-0.5 * contract_delta("call", 100.0), rel=1e-12)
    assert units[1] == pytest.approx(-0.5 * contract_delta("put", 98.0, sign=-1), rel=1e-12)
    assert units[2] == pytest.approx(-0.5 * (contract_delta("call", 100.0) + contract_delta("call", 104.0, -1)),
                                     rel=1e-12)
    assert units[0] < 0 and units[1] < 0      # long call and short put are both long delta: shares sold


def test_sign_a_long_delta_book_sells_shares_and_a_short_delta_book_buys_them():
    overlay = FixedDeltaForwardHedge(block())
    long_call, long_put = overlay.units([item(), item((("put", 1),), (100.0,))], CONTEXT)
    assert long_call < 0 and long_put > 0


def test_the_reference_is_the_chains_parity_forward_and_the_spot_fallback_is_counted():
    overlay = FixedDeltaForwardHedge(block())
    assert overlay.reference(CONTEXT) == pytest.approx(FORWARD, abs=1e-9)
    bare = {**CONTEXT, "rows": []}
    assert overlay.reference(bare) == SPOT
    overlay.units([item()], bare)
    assert overlay.record()["counts"]["forward_spot"] == 1
    assert overlay.units([item()], bare)[0] < 0            # flagged, never refused


def test_a_leg_without_iv_leaves_the_item_unhedged_and_counted():
    overlay = FixedDeltaForwardHedge(block())
    units = overlay.units([item(with_iv=False), item()], CONTEXT)
    assert units[0] == 0.0 and units[1] < 0
    assert overlay.record()["counts"]["leg_no_iv"] == 1


def test_an_unquotable_leg_is_unhedged_and_counted_by_its_code():
    overlay = FixedDeltaForwardHedge(block())
    broken = item()
    broken["quotes"] = [None]
    assert overlay.units([broken], CONTEXT) == [0.0]
    assert overlay.record()["counts"]["no_fill_quote"] == 1


def test_the_margin_floor_is_fraction_times_absolute_units_times_the_reference():
    overlay = FixedDeltaForwardHedge(block(margin_fraction=0.15))
    rows = overlay.floor_rows([item(), item()], [-40.0, 25.0], 101.5)
    assert rows == [pytest.approx(0.15 * 40.0 * 101.5), pytest.approx(0.15 * 25.0 * 101.5)]
    assert FixedDeltaForwardHedge(block(margin_fraction=0.0)).floor_rows([item()], [-40.0], 101.5) == [0.0]


def test_structure_hedge_converts_shares_per_contract_to_shares_per_option_unit():
    assert HedgeOverlay.structure_hedge(-40.0, 100, 101.5) == (-0.4, 101.5)


def test_hedged_payoff_is_the_payoff_plus_h_times_the_move_with_h_fixed():
    overlay = FixedDeltaForwardHedge(block())
    units = overlay.units([item()], CONTEXT)[0]
    ref = overlay.reference(CONTEXT)
    hedge = overlay.structure_hedge(units, MULT, ref)
    h = units / MULT
    for level in (80.0, 100.0, 101.5, 115.0):
        expected = max(level - 100.0, 0.0) + h * (level - ref)
        assert structure_payoff((("call", 1),), level, (100.0,), hedge=hedge) == pytest.approx(expected)
    # the same h at every level: the second difference of the hedge term is zero
    gaps = [structure_payoff((("call", 1),), s, (100.0,), hedge=hedge) - structure_payoff((("call", 1),), s, (100.0,))
            for s in (90.0, 95.0, 100.0)]
    assert gaps[1] - gaps[0] == pytest.approx(gaps[2] - gaps[1])


def test_legs_carry_whole_shares_reference_cost_and_the_rounding_residual():
    overlay = FixedDeltaForwardHedge(block(cost_per_unit=0.01))
    (leg,) = overlay.legs({"contracts": 3}, -4.25, {**CONTEXT, "rows": chain(forward=102.0)})
    assert leg["units"] == -13                          # -12.75 -> whole shares
    assert leg["reference"] == pytest.approx(102.0, abs=1e-9)
    assert leg["cost"] == pytest.approx(0.01 * 13)
    assert leg["instrument"] == "forward"
    assert overlay.record()["counts"]["rounding_residual"] == pytest.approx(0.25)


@pytest.mark.parametrize("total, whole", [(12.5, 13), (12.49, 12), (-12.5, -13), (-12.49, -12), (0.4, 0)])
def test_rounding_at_half_a_share_goes_away_from_zero(total, whole):
    (leg,) = FixedDeltaForwardHedge(block()).legs({"contracts": 1}, total, CONTEXT)
    assert leg["units"] == whole


def test_round_units_false_keeps_fractional_shares():
    (leg,) = FixedDeltaForwardHedge(block(round_units=False)).legs({"contracts": 2}, -4.25, CONTEXT)
    assert leg["units"] == -8.5


def test_settle_and_mark_are_units_times_level_minus_reference_and_exclude_fill_cost():
    overlay = FixedDeltaForwardHedge(block(cost_per_unit=0.01))
    legs = [{"units": -13, "reference": 102.0, "cost": 0.13}, {"units": 5, "reference": 100.0, "cost": 0.05}]
    assert overlay.settle(legs, 99.0) == pytest.approx(-13 * (99.0 - 102.0) + 5 * (99.0 - 100.0))
    assert overlay.mark(legs, 104.0) == pytest.approx(-13 * 2.0 + 5 * 4.0)
    assert overlay.settle([], 99.0) == 0.0


def test_a_settled_hedge_leg_offsets_a_long_calls_move_for_a_small_move():
    # no cash at entry: the forward leg's P&L is units x (S - F_fill); with ratio 1 the book is
    # delta-flat, so a 0.1 move changes the combined value by far less than the option alone
    overlay = FixedDeltaForwardHedge(block(round_units=False))
    units = overlay.units([item()], CONTEXT)[0]
    legs = overlay.legs({"contracts": 1}, units, CONTEXT)
    option_move = (black76("call", FORWARD + 0.1, 100.0, VOL, YEARS) - black76("call", FORWARD, 100.0, VOL, YEARS)) * MULT
    hedge_move = overlay.mark(legs, overlay.reference(CONTEXT) + 0.1)
    assert abs(option_move + hedge_move) < 0.02 * abs(option_move)


def test_record_names_the_block_and_the_counts():
    rec = FixedDeltaForwardHedge(block(ratio=0.5)).record()
    assert rec["kind"] == "fixed_delta_forward" and rec["ratio"] == 0.5 and rec["mode"] == "program"
    assert rec["counts"] == {}


# ---- condition (decision-day inputs only) -----------------------------------------------
def test_a_shock_held_share_condition_switches_the_hedge_on_from_decision_inputs():
    overlay = FixedDeltaForwardHedge(block(condition={"shock_held_share": 0.5}))
    off = overlay.units([item()], {**CONTEXT, "shock_held_share": 0.4})
    on = overlay.units([item()], {**CONTEXT, "shock_held_share": 0.5})
    assert off == [0.0] and on[0] < 0


def test_a_vol_regime_condition_and_a_poisoned_later_input_changes_nothing():
    overlay = FixedDeltaForwardHedge(block(condition={"vol_regime": "high"}))
    base = overlay.units([item()], {**CONTEXT, "vol_regime": "high"})
    poisoned = overlay.units([item()], {**CONTEXT, "vol_regime": "high", "later_vol_regime": "low",
                                        "shock_held_share": 0.0})
    assert base == poisoned and base[0] < 0
    assert overlay.units([item()], {**CONTEXT, "vol_regime": "normal"}) == [0.0]


def test_a_condition_whose_decision_input_is_absent_is_refused():
    overlay = FixedDeltaForwardHedge(block(condition={"vol_regime": "high"}))
    with pytest.raises(ValueError, match="vol_regime"):
        overlay.units([item()], CONTEXT)


# ---- refusals and params ---------------------------------------------------------------
def refused(method):
    with pytest.raises(ConfigError) as err:
        HedgeOverlay.from_method(method)
    return str(err.value)


def test_the_refusal_codes_are_the_adr_codes_plus_the_two_value_codes():
    assert set(ho.REFUSALS) >= {"hedge_unknown_kind", "hedge_unknown_key", "hedge_ratio_range", "hedge_mode",
                                "hedge_overrides_formulation", "hedge_rebalance_not_supported"}


def test_a_valid_block_builds_the_fixed_delta_forward_overlay():
    overlay = HedgeOverlay.from_method({"hedge": BLOCK})
    assert type(overlay) is FixedDeltaForwardHedge and overlay.params == BLOCK


def test_unknown_kind():
    assert "hedge_unknown_kind" in refused({"hedge": block(kind="index_forward")})
    assert "hedge_unknown_kind" in refused({"hedge": {k: v for k, v in BLOCK.items() if k != "kind"}})


def test_unknown_key():
    assert "hedge_unknown_key" in refused({"hedge": block(colour="red")})


@pytest.mark.parametrize("ratio", [-0.1, 1.01, float("nan"), "1", True, None])
def test_ratio_range(ratio):
    assert "hedge_ratio_range" in refused({"hedge": block(ratio=ratio)})


def test_ratio_at_the_bounds_is_accepted():
    assert FixedDeltaForwardHedge(block(ratio=0.0)) and FixedDeltaForwardHedge(block(ratio=1.0))


@pytest.mark.parametrize("mode", ["live", None, "Program", 1])
def test_mode(mode):
    assert "hedge_mode" in refused({"hedge": block(mode=mode)})


@pytest.mark.parametrize("key", ["rho", "shock", "kelly", "sizer", "radius_scale", "risk_charge"])
def test_a_block_naming_a_formulation_key_overrides_the_formulation(key):
    assert "hedge_overrides_formulation" in refused({"hedge": block(**{key: 1})})


def test_a_rebalance_key_is_not_supported():
    assert "hedge_rebalance_not_supported" in refused({"hedge": block(rebalance="daily")})


@pytest.mark.parametrize("name, value", [
    ("margin_fraction", -0.1), ("margin_fraction", float("nan")), ("cost_per_unit", -0.01),
    ("round_units", "yes"), ("rate", float("inf")), ("parity_strikes", 0), ("parity_strikes", 2.5),
    ("iv_source", "last"), ("delta_source", "spot"), ("condition", {"shock_held_share": 1.5}),
    ("condition", {"vol_regime": "low"}), ("condition", {"a": 1, "b": 2}), ("condition", "high")])
def test_bad_values_are_refused_with_the_value_code(name, value):
    assert "hedge_param_value" in refused({"hedge": block(**{name: value})})


def test_a_missing_required_key_is_refused():
    assert "hedge_missing_key" in refused({"hedge": {k: v for k, v in BLOCK.items() if k != "ratio"}})


def test_a_non_mapping_block_is_refused():
    assert "hedge_unknown_kind" in refused({"hedge": "fixed_delta_forward"})


def test_direct_construction_validates_the_same_way():
    with pytest.raises(ConfigError, match="hedge_ratio_range"):
        FixedDeltaForwardHedge(block(ratio=2.0))
    assert FixedDeltaForwardHedge.validate_params(BLOCK) == []
    assert FixedDeltaForwardHedge.validate_params("x")
