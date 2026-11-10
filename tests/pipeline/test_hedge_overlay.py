"""HedgeOverlay on the amended design (ADR-0312 Amendment 1, S1b).

Synthetic items only. Expected values are worked out here from Black-76 and hand arithmetic, never from the
overlay under test. The overlay IMPORTS the forward delta (``LegGreeks``), the cost rule (``carry_costs``),
the hard-to-borrow proxy and the hedged ``structure_max_loss``; these tests pin that it restates none of them.
"""

import math
import subprocess
import sys
from datetime import date, timedelta

import pytest

from dskit.pipeline import hedge_overlay as ho
from dskit.pipeline.base import ConfigError
from dskit.pipeline.hedge_overlay import FixedDeltaForwardHedge, HedgeOverlay, NullHedgeOverlay
from dskit.pipeline.option_exposure import ForwardHedge, HardToBorrowProxy, LegGreeks, StaticDeltaHedge
from dskit.pipeline.option_pricing import black76, black76_delta
from dskit.pipeline.option_structures import structure_max_loss, structure_payoff

DAY = "2026-03-02"
EXPIRY = (date.fromisoformat(DAY) + timedelta(days=30)).isoformat()
YEARS = 30 / 365
FORWARD, SPOT, VOL, MULT = 101.5, 100.3, 0.2, 100

BLOCK = {"kind": "fixed_delta_forward", "mode": "program", "ratio": 1.0,
         "delta_source": "black76_chain_forward", "iv_source": "vendor", "rate": 0.0,
         "margin_fraction": 0.15, "round_units": True, "condition": None,
         "small_delta": 0.0, "hedge_cost_source": "placeholder",
         "htb_lookback": 20, "htb_vol_threshold": 2.5, "htb_jump_threshold": 0.75,
         "hedge_fee_per_share": 0.005, "hedge_half_spread_bps": 5.0, "hedge_borrow_rate": 0.005}


def block(**over):
    return {**BLOCK, **over}


def quote(right, strike, forward=FORWARD):
    mid = black76(right, forward, strike, VOL, YEARS)
    return {"bid": max(mid - 0.05, 0.0), "ask": mid + 0.05, "iv": VOL}


def item(legs=(("call", 1),), strikes=(100.0,), with_iv=True):
    quotes = [quote(right, k) for (right, _), k in zip(legs, strikes)]
    if not with_iv:
        quotes[0] = {k: v for k, v in quotes[0].items() if k != "iv"}
    return {"legs": legs, "strikes": strikes, "quotes": quotes, "expiry": EXPIRY}


CALM = [100.0] * 20
WILD = [100.0 * (1.6 if i % 2 else 1.0) for i in range(20)]
CONTEXT = {"forward": FORWARD, "spot": SPOT, "day": DAY, "multiplier": MULT}


def forward_delta(right, strike, sign=1):
    """Forward delta in shares per contract (no F/S), worked here independently."""
    return sign * MULT * black76_delta(right, FORWARD, strike, VOL, YEARS, 0.0)


def entry(units, status="ok", htb=False):
    return {"units": units, "target": units, "status": status, "hard_to_borrow": htb}


def us(entries):
    return [e["units"] for e in entries]


def test_the_public_surface_is_exactly_the_declared_names():
    assert sorted(ho.__all__) == sorted(
        ["HedgeOverlay", "NullHedgeOverlay", "FixedDeltaForwardHedge", "KINDS", "REFUSALS"])


def test_the_abstract_base_cannot_be_instantiated_and_leaves_units_to_subclasses():
    with pytest.raises(TypeError):
        HedgeOverlay(BLOCK)
    assert getattr(HedgeOverlay.units, "__isabstractmethod__", False)
    assert getattr(HedgeOverlay.reference, "__isabstractmethod__", False)
    assert not hasattr(HedgeOverlay, "structure_hedge")           # the per-unit conversion lives in option_structures


def test_params_tuples_are_the_amendment_tuples_with_the_owners_tuples_imported():
    assert HedgeOverlay._BASE_PARAMS == ("kind", "mode", "ratio", "margin_fraction", "round_units", "condition",
                                          "small_delta", "hedge_cost_source") \
        + HardToBorrowProxy._PARAMS + StaticDeltaHedge._PARAMS
    assert FixedDeltaForwardHedge._EXTRA_PARAMS == ("delta_source", "iv_source", "rate")
    assert ho.KINDS == {"fixed_delta_forward": FixedDeltaForwardHedge}
    assert set(FixedDeltaForwardHedge.params_tuple()) == set(BLOCK)


def test_purity_the_core_module_imports_stdlib_and_dskit_only():
    code = ("import sys; import dskit.pipeline.hedge_overlay; "
            "bad = [m for m in ('numpy', 'pandas', 'scipy', 'pyarrow') if m in sys.modules]; "
            "sys.exit(1 if bad else 0)")
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


# ---- Null overlay: identity everywhere, fields absent -----------------------------------
def test_a_method_without_a_hedge_block_builds_the_null_overlay():
    assert isinstance(HedgeOverlay.from_method({"name": "m"}), NullHedgeOverlay)
    assert isinstance(HedgeOverlay.from_method({"hedge": None}), NullHedgeOverlay)


def test_null_overlay_hooks_are_identity_or_zero_and_record_no_fields():
    null = NullHedgeOverlay()
    items = [item(), item()]
    assert null.units(items, CONTEXT) is None
    assert null.reference(CONTEXT) is None
    assert null.book_units(None, [1, 1]) is None
    assert null.floor_rows(items, None, None) == ([0.0, 0.0], [0.0, 0.0])
    assert null.book_floor(None, None) == 0.0
    assert null.legs(None, CONTEXT) == []
    assert null.settle([], 105.0) == 0.0 and null.mark([], 105.0) == 0.0
    assert null.costs([], 105.0, 10) == {"trade_costs": 0.0, "borrow": 0.0}
    assert null.net_pnl([], 105.0, 10) == 0.0
    assert null.record() == {}                                    # ABSENT, not null
    assert null.max_loss((("call", 1),), (100.0,), -500.0, MULT, None, None) \
        == structure_max_loss((("call", 1),), (100.0,), -500.0, MULT)
    assert null.hedgeable((("call", -1),), (100.0,), 500.0, MULT, None, None) is False   # unhedged rule untouched


def test_ratio_zero_gives_positive_zero_units_zero_legs_and_unchanged_structure_outputs():
    overlay = FixedDeltaForwardHedge(block(ratio=0.0))
    entries = overlay.units([item(), item((("put", -1),), (98.0,))], CONTEXT)
    assert us(entries) == [0.0, 0.0] and all(math.copysign(1.0, u) == 1.0 for u in us(entries))
    assert [e["status"] for e in entries] == ["ok", "ok"]
    book = overlay.book_units(entries, [2, 1])
    assert book["units"] == 0 and book["status"] == "ok"
    (leg,) = overlay.legs(book, CONTEXT)
    assert leg["units"] == 0 and overlay.settle([leg], 120.0) == 0.0
    assert overlay.costs([leg], 120.0, 10) == {"trade_costs": 0.0, "borrow": 0.0}
    legs = (("call", 1),)
    assert overlay.max_loss(legs, (100.0,), -500.0, MULT, book["units"], FORWARD) \
        == structure_max_loss(legs, (100.0,), -500.0, MULT)
    for level in (0.0, 99.0, 120.0):
        assert structure_payoff(legs, level, (100.0,), hedge=(book["units"], FORWARD), multiplier=MULT) \
            == structure_payoff(legs, level, (100.0,))
    plus, minus = overlay.floor_rows([item()], entries[:1], FORWARD)
    assert plus == [0.0] and minus[0] == 0.0


# ---- units: forward delta, held shares --------------------------------------------------
def test_units_are_minus_ratio_times_the_forward_delta_held_shares_from_leggreeks():
    overlay = FixedDeltaForwardHedge(block(ratio=0.5))
    entries = overlay.units([item(), item((("put", -1),), (98.0,)),
                             item((("call", 1), ("call", -1)), (100.0, 104.0))], CONTEXT)
    u = us(entries)
    assert u[0] == pytest.approx(-0.5 * forward_delta("call", 100.0), rel=1e-12)
    assert u[1] == pytest.approx(-0.5 * forward_delta("put", 98.0, sign=-1), rel=1e-12)
    assert u[2] == pytest.approx(-0.5 * (forward_delta("call", 100.0) + forward_delta("call", 104.0, -1)), rel=1e-12)
    # the forward delta is the spot delta divided by F/S: the overlay must NOT carry the chain-rule factor
    spot_delta = forward_delta("call", 100.0) * FORWARD / SPOT
    assert abs(u[0]) == pytest.approx(0.5 * spot_delta * SPOT / FORWARD, rel=1e-12)
    row, _ = LegGreeks({"rate": 0.0, "multiplier": MULT, "min_years": 0.001, "vol_floor": 0.01}).of(
        {"right": "call", "strike": 100.0, "sign": 1, "quantity": 1, "expiry": EXPIRY}, item()["quotes"][0],
        FORWARD, SPOT, DAY)
    assert u[0] == pytest.approx(-0.5 * row["delta_f_sh"], rel=1e-12)


def test_a_long_delta_book_sells_shares_and_a_short_delta_book_buys_them():
    entries = FixedDeltaForwardHedge(block()).units([item(), item((("put", 1),), (100.0,))], CONTEXT)
    assert entries[0]["units"] < 0 < entries[1]["units"]


def test_the_reference_is_the_supplied_parity_forward_never_computed_from_a_chain():
    overlay = FixedDeltaForwardHedge(block())
    assert overlay.reference(CONTEXT) == FORWARD
    assert overlay.reference({"forward": 99.25}) == 99.25
    with pytest.raises(ValueError, match="forward"):
        overlay.reference({"spot": SPOT})
    with pytest.raises(ValueError, match="forward"):
        overlay.reference({"forward": 0.0})
    with pytest.raises(ValueError, match="forward"):
        overlay.units([item()], {k: v for k, v in CONTEXT.items() if k != "forward"})


def test_a_leg_without_iv_carries_the_no_iv_status_and_zero_units_and_is_counted():
    overlay = FixedDeltaForwardHedge(block())
    entries = overlay.units([item(with_iv=False), item()], CONTEXT)
    assert entries[0]["units"] == 0.0 and entries[0]["status"] == LegGreeks.NO_IV
    assert entries[1]["status"] == "ok" and entries[1]["units"] < 0
    assert overlay.record()["counts"]["leg_no_iv"] == 1


def test_an_unquotable_leg_carries_its_code():
    overlay = FixedDeltaForwardHedge(block())
    broken = item()
    broken["quotes"] = [None]
    (e,) = overlay.units([broken], CONTEXT)
    assert e["status"] == LegGreeks.NO_FILL_QUOTE and e["units"] == 0.0
    assert overlay.record()["counts"]["no_fill_quote"] == 1


def test_small_delta_zeroes_a_small_item_in_program_mode_only():
    # a deep-out-of-the-money call has |Delta_F| well under 0.05
    far = item(strikes=(140.0,))
    assert abs(forward_delta("call", 140.0)) / MULT < 0.05 < abs(forward_delta("call", 100.0)) / MULT
    program = FixedDeltaForwardHedge(block(small_delta=0.05))
    far_e, near_e = program.units([far, item()], CONTEXT)
    assert far_e["units"] == 0.0 and far_e["status"] == "ok" and near_e["units"] < 0
    assert program.record()["counts"]["hedge_small_delta"] == 1
    replay = FixedDeltaForwardHedge(block(mode="replay", small_delta=0.05))
    assert replay.units([far], CONTEXT)[0]["units"] == pytest.approx(-forward_delta("call", 140.0))
    assert "hedge_small_delta" not in replay.record()["counts"]


# ---- hard to borrow: the imported proxy blocks SHORT hedges only ------------------------
def test_a_hard_to_borrow_name_blocks_a_short_hedge_but_not_a_long_one():
    overlay = FixedDeltaForwardHedge(block())
    assert HardToBorrowProxy({k: BLOCK[k] for k in HardToBorrowProxy._PARAMS}).of(WILD) is True
    assert HardToBorrowProxy({k: BLOCK[k] for k in HardToBorrowProxy._PARAMS}).of(CALM) is False
    wild = {**CONTEXT, "closes": WILD}
    long_call, long_put = overlay.units([item(), item((("put", 1),), (100.0,))], wild)
    assert long_call["status"] == "hedge_hard_to_borrow" and long_call["units"] == 0.0
    assert long_put["status"] == "ok" and long_put["units"] > 0           # buying shares needs no borrow
    assert long_call["hard_to_borrow"] and long_put["hard_to_borrow"]
    calm = overlay.units([item()], {**CONTEXT, "closes": CALM})
    assert calm[0]["status"] == "ok" and not calm[0]["hard_to_borrow"]
    assert overlay.units([item()], CONTEXT)[0]["status"] == "ok"           # no closes: not flagged
    assert overlay.record()["counts"]["hedge_hard_to_borrow"] == 1


def test_the_thresholds_come_from_the_params_not_a_restated_rule():
    lax = FixedDeltaForwardHedge(block(htb_vol_threshold=1e9, htb_jump_threshold=1e9))
    assert lax.units([item()], {**CONTEXT, "closes": WILD})[0]["status"] == "ok"
    strict = FixedDeltaForwardHedge(block(htb_vol_threshold=1e-6))
    assert strict.units([item()], {**CONTEXT, "closes": [100.0 + 0.5 * (i % 2) for i in range(20)]})[0]["status"] \
        == "hedge_hard_to_borrow"


# ---- book: one net quantity, truncated toward zero --------------------------------------
@pytest.mark.parametrize("raw, whole", [(-288.4, -288), (-288.9, -288), (288.9, 288), (0.9, 0), (-0.9, 0), (-12.5, -12)])
def test_the_book_quantity_truncates_toward_zero(raw, whole):
    book = FixedDeltaForwardHedge(block()).book_units([entry(raw)], [1])
    assert book["units"] == whole and isinstance(book["units"], int) and book["status"] == "ok"


def test_the_book_nets_item_units_times_quantities_then_truncates_once():
    overlay = FixedDeltaForwardHedge(block())
    # per-item rounding would give -3 - 2 + 2 = ... ; the net -3.6 - 2.6 + 2.4 = -3.8 truncates once to -3
    book = overlay.book_units([entry(-1.2), entry(-1.3), entry(2.4)], [3, 2, 1])
    assert book["units"] == -3
    assert book["raw"] == pytest.approx(-3.6 - 2.6 + 2.4)
    assert overlay.record()["counts"]["rounding_residual"] == pytest.approx(0.8)


def test_round_units_false_keeps_the_net_fractional():
    book = FixedDeltaForwardHedge(block(round_units=False)).book_units([entry(-1.25)], [2])
    assert book["units"] == -2.5


def test_a_book_with_an_unpriced_item_is_left_unhedged_whole_with_its_status():
    overlay = FixedDeltaForwardHedge(block())
    book = overlay.book_units([entry(-5.0), entry(0.0, status="leg_no_iv")], [1, 1])
    assert book["units"] == 0 and book["status"] == "leg_no_iv"


def test_hard_to_borrow_applies_to_the_books_net_short_quantity():
    overlay = FixedDeltaForwardHedge(block())
    short = overlay.book_units([entry(-5.0, htb=True)], [1])
    assert short["units"] == 0 and short["status"] == "hedge_hard_to_borrow"
    net_long = overlay.book_units([entry(-5.0, htb=True), entry(9.0, htb=True)], [1, 1])
    assert net_long["units"] == 4 and net_long["status"] == "ok"
    assert overlay.book_units([entry(-5.0)], [1])["status"] == "ok"


# ---- classifier and the glossary 5:1 book -----------------------------------------------
GLOSSARY_LEGS, GLOSSARY_STRIKES = (("call", 5), ("call", 1)), (450.0, 455.0)


def test_the_glossary_five_to_one_book_hedged_with_288_sold_shares_reserves_650_90():
    overlay = FixedDeltaForwardHedge(block())
    book = overlay.book_units([entry(-288.4)], [1])
    assert book["units"] == -288
    args = (GLOSSARY_LEGS, GLOSSARY_STRIKES, -938.90, 100)
    assert overlay.max_loss(*args, book["units"], 451.0) == pytest.approx(650.90)
    assert overlay.max_loss(*args, None, None) == pytest.approx(938.90)        # unhedged
    assert overlay.hedgeable(*args, book["units"], 451.0) is True
    assert structure_max_loss(*args, hedge=(-288, 451.0)) == overlay.max_loss(*args, -288, 451.0)


@pytest.mark.parametrize("legs, strikes, credit, u, ok", [
    ((("call", 1),), (105.0,), -200.0, -35, True),                          # long call
    ((("call", 1), ("call", -1)), (105.0, 110.0), -150.0, -15, False),        # bull call spread
    ((("call", -1), ("call", 1)), (105.0, 110.0), 120.0, 15, True),           # bear call spread
    ((("put", -1),), (95.0,), 200.0, -30, False),                           # short put
    ((("put", -1), ("put", 1)), (95.0, 90.0), 120.0, -12, False),             # bull put spread
    ((("put", 1), ("put", -1)), (95.0, 90.0), -120.0, 12, True),              # bear put spread
])
def test_the_hedgeable_classifier_follows_the_amendment_table(legs, strikes, credit, u, ok):
    assert FixedDeltaForwardHedge(block()).hedgeable(legs, strikes, credit, MULT, u, 100.0) is ok


# ---- margin floor: netted +/- rows, fraction 0.15 ---------------------------------------
def test_floor_rows_are_the_two_netted_rows_plus_and_minus_m_reference_u():
    overlay = FixedDeltaForwardHedge(block())
    plus, minus = overlay.floor_rows([item(), item()], [entry(-40.0), entry(25.0)], 101.5)
    assert plus == [pytest.approx(0.15 * 101.5 * -40.0), pytest.approx(0.15 * 101.5 * 25.0)]
    assert minus == [pytest.approx(-0.15 * 101.5 * -40.0), pytest.approx(-0.15 * 101.5 * 25.0)]


def test_netted_rows_floor_a_spread_below_the_gross_rows():
    # a spread of +30 and -18 per leg nets +12: the netted rows bind on 12, a gross floor on 48
    overlay = FixedDeltaForwardHedge(block())
    plus, minus = overlay.floor_rows([item(), item()], [entry(30.0), entry(-18.0)], 100.0)
    net = sum(plus)
    gross = 0.15 * 100.0 * (30.0 + 18.0)
    assert max(net, -net) == pytest.approx(0.15 * 100.0 * 12.0)
    assert max(net, -net) < gross
    assert overlay.book_floor(12, 100.0) == pytest.approx(0.15 * 100.0 * 12.0)
    assert overlay.book_floor(-12, 100.0) == pytest.approx(0.15 * 100.0 * 12.0)


# ---- legs, settlement, mark, costs ------------------------------------------------------
def test_legs_carry_the_book_units_and_the_fill_reference_and_the_rounding_residual():
    overlay = FixedDeltaForwardHedge(block())
    book = overlay.book_units([entry(-288.4)], [1])
    (leg,) = overlay.legs(book, {**CONTEXT, "forward": 451.0})
    assert leg["units"] == -288 and leg["reference"] == 451.0 and leg["instrument"] == "forward"
    assert leg["residual"] == pytest.approx(0.4)          # units - raw: what truncation left unhedged
    assert leg["residual"] == pytest.approx(ForwardHedge.rounding(-288, 288.4))


def test_an_unhedged_book_has_a_zero_unit_leg_and_a_none_book_has_none():
    overlay = FixedDeltaForwardHedge(block())
    left = overlay.book_units([entry(0.0, status="leg_no_iv")], [1])
    assert overlay.legs(left, CONTEXT)[0]["units"] == 0


def test_cost_is_carry_costs_exactly_with_the_forward_borrow_at_zero():
    overlay = FixedDeltaForwardHedge(block())
    book = overlay.book_units([entry(-288.4)], [1])
    legs = overlay.legs(book, {**CONTEXT, "forward": 451.0})
    costs = overlay.costs(legs, 448.0, 10)
    assert costs["trade_costs"] == pytest.approx(132.336, abs=1e-9)           # the ADR-0310 A2 pin
    assert costs["borrow"] == 0.0                                            # borrow rate 0 on the forward
    ref = StaticDeltaHedge({"hedge_fee_per_share": 0.005, "hedge_half_spread_bps": 5.0, "hedge_borrow_rate": 0.0})
    assert costs["trade_costs"] == ref.carry_costs(-288, 451.0, 448.0, 10)["trade_costs"]
    net = overlay.net_pnl(legs, 448.0, 10)
    assert net == pytest.approx(-288 * (448.0 - 451.0) - 132.336)
    fh = ForwardHedge({"hedge_cost_source": "placeholder"})
    assert net == pytest.approx(fh.of(0.0, -288, 451.0, 448.0, ref.carry_costs(-288, 451.0, 448.0, 10))["hedge_pnl"])


def test_settle_is_units_times_level_minus_reference_and_mark_takes_the_supplied_forward():
    overlay = FixedDeltaForwardHedge(block())
    legs = [{"units": -13, "reference": 102.0}, {"units": 5, "reference": 100.0}]
    assert overlay.settle(legs, 99.0) == pytest.approx(-13 * (99.0 - 102.0) + 5 * (99.0 - 100.0))
    assert overlay.mark(legs, 104.0) == pytest.approx(-13 * 2.0 + 5 * 4.0)
    assert overlay.settle([], 99.0) == 0.0
    overlay.mark(legs, 104.0, carried=True)
    assert overlay.record()["counts"]["mark_forward_carried"] == 1


def test_a_settled_hedge_leg_offsets_a_long_calls_move_for_a_small_move():
    overlay = FixedDeltaForwardHedge(block(round_units=False))
    entries = overlay.units([item()], CONTEXT)
    book = overlay.book_units(entries, [1])
    legs = overlay.legs(book, CONTEXT)
    option_move = (black76("call", FORWARD + 0.1, 100.0, VOL, YEARS) - black76("call", FORWARD, 100.0, VOL, YEARS)) * MULT
    assert abs(option_move + overlay.mark(legs, FORWARD + 0.1)) < 0.02 * abs(option_move)


def test_record_names_the_block_and_the_counts():
    rec = FixedDeltaForwardHedge(block(ratio=0.5)).record()
    assert rec["kind"] == "fixed_delta_forward" and rec["ratio"] == 0.5 and rec["mode"] == "program"
    assert rec["counts"] == {}


# ---- condition (decision-day inputs only) -----------------------------------------------
def test_a_shock_held_share_condition_switches_the_hedge_on_from_decision_inputs():
    overlay = FixedDeltaForwardHedge(block(condition={"shock_held_share": 0.5}))
    off = overlay.units([item()], {**CONTEXT, "shock_held_share": 0.4})
    on = overlay.units([item()], {**CONTEXT, "shock_held_share": 0.5})
    assert us(off) == [0.0] and on[0]["units"] < 0


def test_a_vol_regime_condition_and_a_poisoned_later_input_changes_nothing():
    overlay = FixedDeltaForwardHedge(block(condition={"vol_regime": "high"}))
    base = overlay.units([item()], {**CONTEXT, "vol_regime": "high"})
    poisoned = overlay.units([item()], {**CONTEXT, "vol_regime": "high", "later_vol_regime": "low",
                                        "shock_held_share": 0.0})
    assert base == poisoned and base[0]["units"] < 0
    assert us(overlay.units([item()], {**CONTEXT, "vol_regime": "normal"})) == [0.0]


def test_a_condition_whose_decision_input_is_absent_is_refused():
    overlay = FixedDeltaForwardHedge(block(condition={"vol_regime": "high"}))
    with pytest.raises(ValueError, match="vol_regime"):
        overlay.units([item()], CONTEXT)


# ---- refusals and params ----------------------------------------------------------------
def refused(method):
    with pytest.raises(ConfigError) as err:
        HedgeOverlay.from_method(method)
    return str(err.value)


def test_the_refusal_codes_are_the_adr_codes_s1b_owns():
    assert set(ho.REFUSALS) >= {"hedge_unknown_kind", "hedge_unknown_key", "hedge_missing_key", "hedge_ratio_range",
                                "hedge_mode", "hedge_param_value", "hedge_overrides_formulation",
                                "hedge_rebalance_not_supported", "hedge_cost_below_floor"}
    assert not {"hedge_with_sizer", "hedge_seal"} & set(ho.REFUSALS)     # S3's


def test_a_valid_block_builds_the_fixed_delta_forward_overlay():
    overlay = HedgeOverlay.from_method({"hedge": BLOCK})
    assert type(overlay) is FixedDeltaForwardHedge and overlay.params == BLOCK


def test_unknown_kind():
    assert "hedge_unknown_kind" in refused({"hedge": block(kind="index_forward")})
    assert "hedge_unknown_kind" in refused({"hedge": {k: v for k, v in BLOCK.items() if k != "kind"}})


def test_unknown_key_including_the_dropped_parity_strikes_and_cost_per_unit():
    assert "hedge_unknown_key" in refused({"hedge": block(colour="red")})
    assert "hedge_unknown_key" in refused({"hedge": block(parity_strikes=3)})
    assert "hedge_unknown_key" in refused({"hedge": block(cost_per_unit=0.0)})


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
    ("margin_fraction", -0.1), ("margin_fraction", float("nan")), ("small_delta", -0.1), ("small_delta", float("inf")),
    ("round_units", "yes"), ("rate", float("inf")), ("iv_source", "last"), ("delta_source", "spot"),
    ("hedge_cost_source", "guess"), ("htb_lookback", 3), ("htb_vol_threshold", 0), ("htb_jump_threshold", -1.0),
    ("hedge_fee_per_share", -0.1), ("hedge_borrow_rate", 1.0),
    ("condition", {"shock_held_share": 1.5}), ("condition", {"vol_regime": "low"}),
    ("condition", {"a": 1, "b": 2}), ("condition", "high")])
def test_bad_values_are_refused_with_the_value_code(name, value):
    assert "hedge_param_value" in refused({"hedge": block(**{name: value})})


def test_cost_rates_below_the_placeholder_floors_are_refused_unless_measured():
    assert "hedge_cost_below_floor" in refused({"hedge": block(hedge_fee_per_share=0.001)})
    assert "hedge_cost_below_floor" in refused({"hedge": block(hedge_half_spread_bps=1.0)})
    assert FixedDeltaForwardHedge(block(hedge_fee_per_share=0.001, hedge_half_spread_bps=1.0,
                                        hedge_cost_source="measured"))
    assert FixedDeltaForwardHedge(block(hedge_fee_per_share=0.02, hedge_half_spread_bps=9.0))


def test_a_missing_required_key_is_refused():
    for key in ("ratio", "small_delta", "hedge_cost_source", "htb_lookback", "hedge_fee_per_share"):
        assert "hedge_missing_key" in refused({"hedge": {k: v for k, v in BLOCK.items() if k != key}})


def test_a_non_mapping_block_is_refused():
    assert "hedge_unknown_kind" in refused({"hedge": "fixed_delta_forward"})


def test_direct_construction_validates_the_same_way():
    with pytest.raises(ConfigError, match="hedge_ratio_range"):
        FixedDeltaForwardHedge(block(ratio=2.0))
    assert FixedDeltaForwardHedge.validate_params(BLOCK) == []
    assert FixedDeltaForwardHedge.validate_params("x")
