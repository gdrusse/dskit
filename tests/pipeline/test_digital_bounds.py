"""Executable digital bounds from vertical spreads (ADR-0257 part 1).

Every expected number is worked by hand in the comments or is a Black-76 closed form restated here
(``N(d2)``), never read back from the module under test.
"""

import enum
import inspect
import math
import random
from fractions import Fraction

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dskit.pipeline import binary_curve, digital_bounds
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.digital_bounds import STATUS_OK, STATUSES, DigitalBounds, quote_problems
from dskit.pipeline.option_pricing import black76
from tests.pipeline.test_binary_curve import ABSENT, SPEC, SPEC_IDS

PARAMS = {
    "payoff_field": "payoff", "lower_field": "lo", "upper_field": "hi", "chain_fields": ["chain"],
    "quote_chain_fields": ["chain"], "strike_field": "strike", "right_field": "right",
    "call_value": "C", "put_value": "P", "bid_field": "bid", "ask_field": "ask", "bound_field": "dig",
}


def contract(payoff, lo=None, hi=None, chain="x"):
    return {"payoff": payoff, "lo": lo, "hi": hi, "chain": chain}


def quote(right, strike, bid, ask, chain="x", **extra):
    return {"chain": chain, "right": right, "strike": strike, "bid": bid, "ask": ask, **extra}


def run(contracts, quotes, **overrides):
    return DigitalBounds("bounds", {**PARAMS, **overrides}).run(None, {"records": contracts, "quotes": quotes})


# A hand chain on a forward of 100 (parity P = C - (100 - K)); the put at 110 is a tick tighter.
HAND = [
    quote("C", 90, 11.0, 11.4), quote("C", 100, 4.0, 4.2), quote("C", 110, 1.0, 1.2),
    quote("P", 90, 1.0, 1.4), quote("P", 100, 4.0, 4.2), quote("P", 110, 11.0, 11.1),
]


# -- hand-worked bounds -------------------------------------------------------------------------


def test_above_a_listed_strike_keeps_the_tighter_of_the_call_and_put_bands():
    row = run([contract("above", lo=100.0)], HAND)["records"][0]
    # calls: lower (bid C100 - ask C110) / 10 = (4.0 - 1.2) / 10 = 0.28
    #        upper (ask C90 - bid C100) / 10 = (11.4 - 4.0) / 10 = 0.74
    # puts:  P(S < 100) <= (ask P110 - bid P100) / 10 = 0.71, so P(S >= 100) >= 0.29
    #        P(S < 100) >= (bid P100 - ask P90) / 10 = 0.26, so P(S >= 100) <= 0.74
    assert row["dig_lower"] == pytest.approx(0.29)
    assert row["dig_upper"] == pytest.approx(0.74)
    assert row["dig_status"] == STATUS_OK


def test_a_puts_only_chain_bounds_both_sides_by_itself():
    puts = [q for q in HAND if q["right"] == "P"]
    row = run([contract("above", lo=100.0)], puts)["records"][0]
    # ahead:  P(S >= 100) >= 1 - (ask P110 - bid P100) / 10 = 1 - 0.71 = 0.29
    # behind: P(S >= 100) <= 1 - (bid P100 - ask P90) / 10 = 1 - 0.26 = 0.74
    assert row["dig_lower"] == pytest.approx(0.29)
    assert row["dig_upper"] == pytest.approx(0.74)


def test_below_is_the_complement_band():
    row = run([contract("below", hi=100.0)], HAND)["records"][0]
    assert row["dig_lower"] == pytest.approx(1 - 0.74)
    assert row["dig_upper"] == pytest.approx(1 - 0.29)


def test_between_combines_one_edges_lower_with_the_other_edges_upper():
    # above(90): calls lower (bid C90 - ask C100)/10 = 0.68; puts give P(S<90) <= (ask P100 - bid P90)/10 = 0.32 -> 0.68
    #            upper: no listed strike below 90 to buy, so only... nothing: the upper edge is missing
    # use (90, 100]: between = above(90) - above(100); upper needs above(90)'s upper, which is missing
    row = run([contract("between", lo=90.0, hi=100.0)], HAND)["records"][0]
    assert row["dig_upper"] is None and row["dig_status"] == "no_upper_bracket"
    # lower = lower above(90) - upper above(100) = 0.68 - 0.74 < 0 -> clipped to 0
    assert row["dig_lower"] == pytest.approx(0.0)


def test_an_unlisted_strike_with_nothing_above_it_is_marked_never_interpolated():
    row = run([contract("above", lo=105.0)], HAND)["records"][0]
    # lower needs two listed strikes at or above 105: only 110 exists, on either right
    assert row["dig_lower"] is None and row["dig_status"] == "no_lower_bracket"
    # upper: calls (ask C90 - bid C100) / 10 = 0.74; puts: P(S<105) >= (bid P100 - ask P90)/10 = 0.26 -> 0.74
    assert row["dig_upper"] == pytest.approx(0.74)


def test_a_strike_outside_every_listed_pair_has_no_bracket():
    row = run([contract("above", lo=200.0)], HAND)["records"][0]
    assert row["dig_status"] == "no_lower_bracket"
    assert run([contract("above", lo=200.0)], HAND[:1])["records"][0]["dig_status"] == "no_bracket"


# -- a known lognormal: the bounds contain the true digital ---------------------------------------

F, VOL, T = 100.0, 0.2, 0.25


def digital_above(k):
    """P(S >= k) for a driftless lognormal forward: N(d2), restated with erf."""
    d2 = (math.log(F / k) - VOL * VOL * T / 2) / (VOL * math.sqrt(T))
    return 0.5 * (1 + math.erf(d2 / math.sqrt(2)))


def chain(step, half_spread, discount=1.0):
    rows = []
    for i in range(round(60.0 / step) + 1):
        k = round(70.0 + i * step, 9)
        for right, code in (("call", "C"), ("put", "P")):
            mid = discount * black76(right, F, k, VOL, T)
            rows.append(quote(code, k, max(mid - half_spread, 0.0), mid + half_spread, df=discount))
    return rows


@pytest.mark.parametrize("strike", [90.0, 97.5, 100.0, 103.0, 112.0])
def test_bounds_contain_the_true_digital_of_a_lognormal(strike):
    quotes = chain(5.0, 0.05)
    out = run([contract("above", lo=strike), contract("below", hi=strike),
               contract("between", lo=strike - 7.0, hi=strike + 6.0)], quotes)["records"]
    truths = (digital_above(strike), 1 - digital_above(strike),
              digital_above(strike - 7.0) - digital_above(strike + 6.0))
    for row, truth in zip(out, truths):
        assert row["dig_status"] == STATUS_OK, row
        assert row["dig_lower"] <= truth <= row["dig_upper"], (row, truth)


def test_a_discount_factor_column_turns_discounted_quotes_into_forward_probabilities():
    quotes = chain(5.0, 0.0, discount=0.9)
    scaled = run([contract("above", lo=100.0)], quotes, discount_field="df")["records"][0]
    assert scaled["dig_lower"] <= digital_above(100.0) <= scaled["dig_upper"]
    # without the column, discounted CALL quotes read as forward values: 0.9 times the band
    calls = [q for q in quotes if q["right"] == "C"]
    plain = run([contract("above", lo=100.0)], calls)["records"][0]
    assert plain["dig_lower"] == pytest.approx(0.9 * scaled["dig_lower"])
    assert plain["dig_upper"] == pytest.approx(0.9 * scaled["dig_upper"])


def test_the_band_narrows_as_the_strike_spacing_shrinks():
    widths = []
    for step in (10.0, 5.0, 2.5, 1.0, 0.5):
        row = run([contract("above", lo=100.0)], chain(step, 0.0))["records"][0]
        widths.append(row["dig_upper"] - row["dig_lower"])
    assert widths == sorted(widths, reverse=True) and widths[-1] < 0.05


# -- quotes refused by name ------------------------------------------------------------------------


def test_a_crossed_quote_is_refused_by_name_and_never_used():
    quotes = [q for q in HAND if not (q["right"] == "C" and q["strike"] == 110)] + [quote("C", 110, 1.3, 1.2)]
    out = run([contract("above", lo=100.0)], quotes)
    refusal = [r for r in out["refusals"] if r["strike"] == 110 and r["right"] == "C"]
    assert refusal and any("uncrossed" in p for p in refusal[0]["problems"])
    # the call band has no strike above 100 to buy, so the lower bound is the put's alone: 0.29
    assert out["records"][0]["dig_lower"] == pytest.approx(0.29)
    assert out["census"]["quotes"]["refused"] == 1


def test_a_zero_bid_is_no_market_on_the_sale_side_and_is_refused_by_name():
    quotes = [q for q in HAND if not (q["right"] == "C" and q["strike"] == 100)] + [quote("C", 100, 0.0, 4.2)]
    out = run([contract("above", lo=100.0)], quotes)
    refusal = [r for r in out["refusals"] if r["strike"] == 100 and r["right"] == "C" and r["side"] == "sell"]
    assert refusal and any("bid must be positive to sell" in p for p in refusal[0]["problems"])
    assert out["records"][0]["dig_lower"] == pytest.approx(0.29)  # the put band still answers


def test_size_coverage_is_enforced_when_size_columns_are_named():
    quotes = [{**q, "bs": 5, "as": 5} for q in HAND]
    quotes[1]["bs"] = 2  # C100 bid size below the 3 contracts asked for
    out = run([contract("above", lo=100.0)], quotes, bid_size_field="bs", ask_size_field="as", min_size=3)
    assert any("does not cover count 3" in p for r in out["refusals"] for p in r["problems"])


def test_a_contract_naming_an_unknown_chain_or_payoff_or_bad_bounds_is_marked():
    out = run([contract("above", lo=100.0, chain="nope"), contract("sideways", lo=100.0),
               contract("between", lo=110.0, hi=100.0), contract("above")], HAND)
    assert [r["dig_status"] for r in out["records"]] == ["no_chain", "unknown_payoff", "bad_bounds", "bad_bounds"]
    assert out["census"]["by_status"]["no_chain"] == 1 and set(out["census"]["by_status"]) == set(STATUSES)


def test_crossed_call_and_put_bands_are_reported_not_hidden():
    # puts imply P(S >= 100) >= 0.9 while calls cap it at 0.74: a parity inconsistency
    quotes = HAND[:3] + [quote("P", 100, 4.0, 4.2), quote("P", 110, 4.5, 5.0)]  # P(S<100) <= (5.0 - 4.0)/10
    row = run([contract("above", lo=100.0)], quotes)["records"][0]
    assert row["dig_status"] == "crossed_band"
    assert row["dig_lower"] == pytest.approx(0.9) and row["dig_upper"] == pytest.approx(0.74)


def test_the_tighter_upper_edge_is_kept_when_the_puts_cap_lower_than_the_calls():
    # calls cap P(S >= 100) at (11.4 - 4.0) / 10 = 0.74; a put at 90 offered at 1.0 gives
    # P(S < 100) >= (4.0 - 1.0) / 10 = 0.30, so P(S >= 100) <= 0.70: the put edge must win.
    quotes = [q for q in HAND if not (q["right"] == "P" and q["strike"] == 90)] + [quote("P", 90, 0.8, 1.0)]
    row = run([contract("above", lo=100.0)], quotes)["records"][0]
    assert row["dig_upper"] == pytest.approx(0.70)
    assert row["dig_status"] == STATUS_OK


def test_a_wide_chain_is_clipped_to_the_unit_interval_on_both_edges():
    # calls only: lower (bid C100 - ask C110) / 10 = (1.0 - 6.0) / 10 = -0.5 -> 0;
    #             upper (ask C90 - bid C100) / 10 = (11.4 - 1.0) / 10 = 1.04 -> 1
    calls = [quote("C", 90, 11.0, 11.4), quote("C", 100, 1.0, 4.2), quote("C", 110, 1.0, 6.0)]
    row = run([contract("above", lo=100.0)], calls)["records"][0]
    assert (row["dig_lower"], row["dig_upper"]) == (0.0, 1.0)


def test_each_edge_is_clipped_before_a_between_band_combines_them():
    # between(100, 110) upper = upper P(S >= 100) - lower P(S >= 110).
    # upper P(S >= 100) = (ask C90 - bid C100) / 10 = (11.4 - 4.0) / 10 = 0.74
    # lower P(S >= 110) = (bid C110 - ask C120) / 10 = (1.0 - 8.0) / 10 = -0.7 -> clipped to 0
    # so the band's upper edge is 0.74; an unclipped edge would loosen it to min(1, 1.44) = 1.
    calls = [quote("C", 90, 11.0, 11.4), quote("C", 100, 4.0, 4.2), quote("C", 110, 1.0, 1.2),
             quote("C", 120, 1.0, 8.0)]
    row = run([contract("between", lo=100.0, hi=110.0)], calls)["records"][0]
    assert row["dig_upper"] == pytest.approx(0.74)
    assert row["dig_lower"] == 0.0  # (4.0 - 1.2) / 10 - (4.2 - 1.0) / 10 = -0.04 -> 0
    # the other direction: upper P(S >= 100) = (15.0 - 4.0) / 10 = 1.1 -> clipped to 1, and
    # lower P(S >= 110) = (1.0 - 0.5) / 10 = 0.05, so the upper edge is 0.95, not min(1, 1.05) = 1
    calls = [quote("C", 90, 11.0, 15.0), quote("C", 100, 4.0, 4.2), quote("C", 110, 1.0, 1.2),
             quote("C", 120, 0.3, 0.5)]
    row = run([contract("between", lo=100.0, hi=110.0)], calls)["records"][0]
    assert row["dig_upper"] == pytest.approx(0.95)


def test_each_put_edge_is_clipped_on_its_own_side_before_a_between_band_combines_them():
    # puts only; between(100, 110) = above(100) - above(110), so lower = lower(100) - upper(110) and
    # upper = upper(100) - lower(110). Each edge is one of the docstring's cases 3 and 4, with h = 10:
    #   lower(100), puts ahead:  1 - (ask P110 - bid P100) / 10 = 1 - (1.2 - 4.0) / 10 = 1.28  (a lower edge clips at 0 only)
    #   upper(110), puts behind: 1 - (bid P110 - ask P100) / 10 = 1 - (1.0 - 4.2) / 10 = 1.32  -> clipped to 1
    #   lower(110), puts ahead:  1 - (ask P120 - bid P110) / 10 = 1 - (18.0 - 1.0) / 10 = -0.7 -> clipped to 0
    #   upper(100), puts behind: 1 - (bid P100 - ask P90) / 10 = 1 - (4.0 - 1.4) / 10 = 0.74
    # lower = 1.28 - 1 = 0.28 and upper = 0.74 - 0 = 0.74. With the two put-side flags swapped, 1.28 would clip to 1
    # and 1.32 stay (lower 0), and -0.7 stay (upper min(1, 0.74 + 0.7) = 1).
    puts = [quote("P", 90, 1.0, 1.4), quote("P", 100, 4.0, 4.2), quote("P", 110, 1.0, 1.2), quote("P", 120, 1.0, 18.0)]
    row = run([contract("between", lo=100.0, hi=110.0)], puts)["records"][0]
    assert (row["dig_lower"], row["dig_upper"]) == (pytest.approx(0.28), pytest.approx(0.74))
    assert row["dig_status"] == STATUS_OK


def test_a_refusal_row_names_the_quote_by_its_declared_chain_columns():
    quotes = [{**q, "und": "X", "exp": 20261016} for q in HAND] + [
        {"und": "X", "exp": 20261016, "right": "C", "strike": 105, "bid": 1.3, "ask": 1.2}]
    params = {"chain_fields": ["und", "exp"], "quote_chain_fields": ["und", "exp"]}
    rows = [{"payoff": "above", "lo": 100.0, "und": "X", "exp": 20261016}]
    refused = [r for r in run(rows, quotes, **params)["refusals"] if r["strike"] == 105]
    assert refused and all(r["chain"] == ["X", 20261016] and r["right"] == "C" for r in refused)


def test_a_strike_and_right_listed_twice_is_refused_both_times_by_name():
    quotes = list(HAND) + [quote("C", 100, 4.1, 4.3)]
    out = run([contract("above", lo=100.0)], quotes)
    duplicates = [r for r in out["refusals"] if any("duplicate quote" in m for m in r["problems"])]
    assert len(duplicates) == 2, "neither copy of a repeated strike/right may be used"


class _Right(enum.StrEnum):
    C = "C"
    P = "P"


@pytest.mark.parametrize("right", [np.str_("C"), _Right.C], ids=["numpy_str", "str_enum"])
def test_a_duplicate_whose_right_is_a_str_subclass_is_refused_with_the_plain_copy(right):
    # np.str_("C") and a StrEnum C are the call right; listed beside a plain "C" C100 they are the
    # same strike and right twice, never a second quote that silently overwrites the first
    quotes = list(HAND) + [quote(right, 100, 9.0, 9.1)]
    out = run([contract("above", lo=100.0)], quotes)
    duplicates = [r for r in out["refusals"] if any("duplicate quote" in m for m in r["problems"])]
    assert len(duplicates) == 2
    assert out["records"][0]["dig_lower"] == pytest.approx(0.29)  # the puts alone: neither C100 is used


@pytest.mark.parametrize("right", [np.str_("P"), _Right.P])
def test_a_str_subclass_right_is_filed_on_the_side_its_characters_name(right):
    quotes = [q for q in HAND if not (q["right"] == "P" and q["strike"] == 100)] + [quote(right, 100, 4.0, 4.2)]
    out = run([contract("above", lo=100.0)], quotes)
    assert out["refusals"] == []
    assert out["records"][0]["dig_lower"] == pytest.approx(0.29)  # the put band read P100


class _Liar(str):
    """A str subclass whose ``__str__`` and ``__eq__`` lie: only its characters say which right it is."""

    def __str__(self):
        return "P"

    def __eq__(self, other):
        return False

    __hash__ = str.__hash__


def test_a_right_whose_own_str_and_eq_lie_is_read_by_its_characters():
    quotes = list(HAND) + [quote(_Liar("C"), 100, 9.0, 9.1)]
    duplicates = [r for r in run([contract("above", lo=100.0)], quotes)["refusals"]
                  if any("duplicate quote" in m for m in r["problems"])]
    assert len(duplicates) == 2


def test_a_lying_right_is_accepted_and_filed_as_the_right_its_characters_name():
    # calls only, C100 spelled by the liar: filed as a call it bounds as HAND's calls do,
    # lower (4.0 - 1.2) / 10 = 0.28 and upper (11.4 - 4.0) / 10 = 0.74
    calls = [quote("C", 90, 11.0, 11.4), quote(_Liar("C"), 100, 4.0, 4.2), quote("C", 110, 1.0, 1.2)]
    out = run([contract("above", lo=100.0)], calls)
    assert out["refusals"] == []
    assert (out["records"][0]["dig_lower"], out["records"][0]["dig_upper"]) == (pytest.approx(0.28), pytest.approx(0.74))


@pytest.mark.parametrize("key", [1.0, float("nan"), {1, 2}, b"x", ["X"], ("X",), True])
def test_a_chain_key_that_cannot_be_keyed_is_refused_by_name_on_quotes_and_contracts(key):
    out = run([contract("above", lo=100.0, chain=key), contract("above", lo=100.0)],
              list(HAND) + [quote("C", 120, 0.1, 0.2, chain=key)])
    refused = [r for r in out["refusals"] if r["side"] == "both"]
    assert len(refused) == 1 and any("chain key" in m for m in refused[0]["problems"])
    assert [r["dig_status"] for r in out["records"]] == ["bad_chain_key", STATUS_OK]
    assert "bad_chain_key" in STATUSES


COMPOSITE = {"chain_fields": ["root", "expiry"], "quote_chain_fields": ["und", "exp"]}


def test_a_composite_chain_is_declared_as_columns_and_matched_in_order():
    quotes = [{**q, "und": "X", "exp": "2026-10-16"} for q in HAND]
    rows = [{**contract("above", lo=100.0), "root": "X", "expiry": "2026-10-16"},
            {**contract("above", lo=100.0), "root": "2026-10-16", "expiry": "X"},
            {**contract("above", lo=100.0), "root": "X"}]
    out = run(rows, quotes, **COMPOSITE)
    assert [r["dig_status"] for r in out["records"]] == [STATUS_OK, "no_chain", "no_chain"]
    assert out["records"][0]["dig_lower"] == pytest.approx(0.29)


def test_a_composite_chain_refuses_a_strike_listed_as_100_and_as_100_point_0():
    # the same strike spelled int and float under one composite chain is one quote listed twice
    calls = [quote("C", k, b, a, und="X", exp=7) for k, b, a in ((90, 11.0, 11.4), (100, 4.0, 4.2),
                                                                  (110, 1.0, 1.2), (100.0, 4.1, 4.3))]
    out = run([{**contract("above", lo=100.0), "root": "X", "expiry": np.int64(7)}], calls, **COMPOSITE)
    duplicates = [r for r in out["refusals"] if any("duplicate quote" in m for m in r["problems"])]
    assert len(duplicates) == 2
    assert out["records"][0]["dig_status"] == "no_bracket"


@pytest.mark.parametrize("bad", [{"chain_fields": "chain"}, {"chain_fields": []}, {"quote_chain_fields": ["c", ""]},
                                 {"chain_fields": ["a", "b"]}, {"chain_fields": ["a", "a"],
                                                                "quote_chain_fields": ["b", "c"]}])
def test_chain_field_lists_are_refused_by_name_when_malformed(bad):
    assert any("chain_fields" in p for p in DigitalBounds.validate_params({**PARAMS, **bad}))


def test_a_chain_key_spelled_1_is_not_the_chain_spelled_one_as_text():
    quotes = [{**q, "chain": 1} for q in HAND]
    out = run([contract("above", lo=100.0, chain="1"), contract("above", lo=100.0, chain=np.int64(1))], quotes)
    assert [r["dig_status"] for r in out["records"]] == ["no_chain", STATUS_OK]


@pytest.mark.parametrize("missing", [None, "", "absent"])
def test_a_contract_with_a_missing_chain_cell_is_no_chain_even_beside_a_quote_missing_it_too(missing):
    quotes = [{**q, "chain": None} for q in HAND]
    row = contract("above", lo=100.0, chain=missing)
    if missing == "absent":
        del row["chain"]
    out = run([row], quotes)
    assert out["records"][0]["dig_status"] == "no_chain"
    assert all(any("chain key is missing" in m for m in r["problems"]) for r in out["refusals"])


def test_a_locked_cent_priced_chain_far_from_the_money_is_not_a_crossed_band():
    # exactly linear calls 0.05 apart per unit strike: both edges are 0.05, but at prices near
    # 8800 the subtraction's rounding (~1e-12 per unit of spacing) exceeds any fixed 1e-12 dust
    calls = [quote("C", 6695, 8799.0, 8799.0), quote("C", 6696, 8798.95, 8798.95),
             quote("C", 6697, 8798.9, 8798.9)]
    row = run([contract("above", lo=6696.0)], calls)["records"][0]
    assert row["dig_status"] == STATUS_OK
    assert row["dig_lower"] == pytest.approx(0.05) and row["dig_upper"] == pytest.approx(0.05)


def test_a_genuine_crossing_of_one_millionth_at_cent_prices_is_still_crossed():
    # lower (8798.95 - (8798.9 - 1e-6)) / 1 = 0.05 + 1e-6 > upper (8799.0 - 8798.95) / 1 = 0.05
    calls = [quote("C", 6695, 8799.0, 8799.0), quote("C", 6696, 8798.95, 8798.95),
             quote("C", 6697, 8798.9 - 1e-6, 8798.9 - 1e-6)]
    row = run([contract("above", lo=6696.0)], calls)["records"][0]
    assert row["dig_status"] == "crossed_band"
    assert row["dig_lower"] - row["dig_upper"] == pytest.approx(1e-6, rel=1e-3)


def test_a_crossing_far_below_the_old_chain_allowance_is_reported_on_a_cheap_chain():
    # calls 0.30 / 0.20 / 0.10 at 90 / 100 / 110: each edge's proven error is ~1e-17 here, so a
    # 1e-12 crossing is a parity violation, not dust (the chain-wide allowance used to swallow it)
    far = 0.10 - 10 * 1e-12
    calls = [quote("C", 90, 0.30, 0.30), quote("C", 100, 0.20, 0.20), quote("C", 110, far, far)]
    assert run([contract("above", lo=100.0)], calls)["records"][0]["dig_status"] == "crossed_band"


def test_one_edge_error_cap_and_no_chain_wide_tolerance():
    assert digital_bounds.EDGE_MAX_ERROR == 1e-9 and "EDGE_MAX_ERROR" in digital_bounds.__all__
    assert not hasattr(digital_bounds, "BAND_RELATIVE_TOLERANCE"), "the chain-wide scale is gone"
    assert not hasattr(digital_bounds, "BAND_TOLERANCE")


# A chain with a GENUINE 26-point parity violation at K = 100: calls say P(S >= 100) >= 0.9, puts
# say P(S >= 100) <= 1 - (bid P100 - ask P95) / 5 = 1 - (1.0 - 0.4) / 5 = 0.88... and the calls
# behind cap it at (ask C95 - bid C100) / 5 = (9.2 - 6.0) / 5 = 0.64, so the band is [0.9, 0.64].
CROSSED = [quote("C", 95, 9.0, 9.2), quote("C", 100, 6.0, 6.1), quote("C", 105, 1.4, 1.5),
           quote("C", 110, 0.5, 0.6), quote("P", 95, 0.3, 0.4), quote("P", 100, 1.0, 1.1),
           quote("P", 105, 3.5, 3.6), quote("P", 110, 7.4, 7.5)]


def test_the_planted_crossing_is_reported():
    row = run([contract("above", lo=100.0)], CROSSED)["records"][0]
    assert row["dig_status"] == "crossed_band"
    assert (row["dig_lower"], row["dig_upper"]) == (pytest.approx(0.9), pytest.approx(0.64))


def test_float_noise_strikes_elsewhere_in_the_chain_cannot_hide_the_planted_crossing():
    # 0.1 + 0.2 and 0.3 are one strike to any reader; their "spacing" of 5.6e-17 used to inflate the
    # chain-wide allowance to ~1.7e5 and mark the crossing ok. Their own edge is unusable, nothing else moves.
    noisy = CROSSED + [quote("P", 0.1 + 0.2, 0.0001, 0.0002), quote("P", 0.3, 0.0001, 0.0002)]
    row = run([contract("above", lo=100.0)], noisy)["records"][0]
    assert row["dig_status"] == "crossed_band"
    assert (row["dig_lower"], row["dig_upper"]) == (pytest.approx(0.9), pytest.approx(0.64))


def test_a_near_duplicate_strike_at_k_cannot_produce_an_ok_band():
    # 100.00000000000001 is 100 to any reader: its pair with 100 is an edge of width 1.4e-14 whose
    # error is enormous, so the next farther pair (100, 105) is used and the crossing still shows
    row = run([contract("above", lo=100.0)], CROSSED + [quote("C", 100.00000000000001, 6.2, 6.3)])["records"][0]
    assert row["dig_status"] == "crossed_band"
    assert row["dig_lower"] == pytest.approx(0.9)


def test_an_unusable_nearest_pair_falls_back_to_the_next_farther_pair():
    # C100's nearest ask above is a near-duplicate strike offered at 3.9 (an edge of 7e12, clipped
    # to 1, a false crossing); the fallback pair (C100, C110) gives (4.0 - 1.2) / 10 = 0.28 and the
    # puts' 0.29 stays the tighter lower edge
    row = run([contract("above", lo=100.0)], HAND + [quote("C", 100.00000000000001, 3.8, 3.9)])["records"][0]
    assert row["dig_status"] == STATUS_OK
    assert row["dig_lower"] == pytest.approx(0.29) and row["dig_upper"] == pytest.approx(0.74)


def test_an_unusable_nearest_pair_behind_falls_back_to_the_next_farther_ask():
    # the mirror of the case above: C100's nearest ask below is a near-duplicate strike, so the
    # upper edge comes from (C90, C100): (11.4 - 4.0) / 10 = 0.74
    calls = [quote("C", 90, 11.0, 11.4), quote("C", 99.99999999999999, 4.3, 4.4), quote("C", 100, 4.0, 4.2),
             quote("C", 110, 1.0, 1.2)]
    row = run([contract("above", lo=100.0)], calls)["records"][0]
    assert row["dig_upper"] == pytest.approx(0.74) and row["dig_status"] == STATUS_OK


def test_an_unusable_nearest_bid_falls_back_to_the_next_bid_out():
    # C100 is quoted at 1e9: every pair it sells carries an error far above the cap, so the lower
    # edge sells the next bid out, (bid C105 - ask C110) / 5 = (2.0 - 1.2) / 5 = 0.16
    calls = [quote("C", 100, 1e9, 1e9), quote("C", 105, 2.0, 2.1), quote("C", 110, 1.0, 1.2)]
    row = run([contract("above", lo=100.0)], calls)["records"][0]
    assert row["dig_lower"] == pytest.approx(0.16)


def test_a_band_crosses_only_beyond_the_sum_of_both_edges_errors():
    status = digital_bounds._band_status
    assert status((0.5 + 1.5e-10, 1e-10), (0.5, 1e-10)) == STATUS_OK            # inside either error's sum
    assert status((0.5, 1e-10), (0.5 - 1.5e-10, 1e-10)) == STATUS_OK
    assert status((0.5 + 2.5e-10, 1e-10), (0.5, 1e-10)) == "crossed_band"
    assert status((0.5, 0.0), (0.5, 0.0)) == STATUS_OK                         # equal edges are a point, not a crossing


def test_strikes_near_one_hundred_thousand_spaced_one_cent_have_no_usable_edge():
    # the accepted limit: h = 0.01 at K ~ 1e5 carries a strike-rounding error of ~4e-11 per cent of
    # spacing, so every edge's bound exceeds EDGE_MAX_ERROR and no pair is usable
    calls = [quote("C", 100000.00, 5.02, 5.02), quote("C", 100000.01, 5.01, 5.01), quote("C", 100000.02, 5.0, 5.0)]
    row = run([contract("above", lo=100000.01)], calls)["records"][0]
    assert row["dig_status"] == "no_bracket"


#: Unit roundoff of a binary64 float, restated (not read from the module under test).
_U = Fraction(1, 2 ** 53)


def _meant(x, rng):
    """A rational the float ``x`` may stand for, ``|meant - x| <= u |x|``: mostly a corner of that range."""
    return Fraction(x) * (1 + _U * rng.choice([-1, 1, -1, 1, 0, Fraction(rng.random())]))


def _random_edge(rng):
    """A random spread: magnitudes over eleven decades, near-duplicate strikes and near-equal prices included."""
    scale = 10.0 ** rng.randint(-3, 6)
    near_k = scale * rng.uniform(0.5, 2.0)
    gap = scale * 10.0 ** rng.choice([-16, -14, -12, -9, -6, -3, -2, -1, 0])
    far_k = near_k + gap * rng.choice([-1, 1]) * rng.uniform(1.0, 3.0)
    price = 10.0 ** rng.randint(-4, 5)
    near_q = price * rng.uniform(0.5, 2.0)
    far_q = near_q * (1 + rng.choice([0.0, 1e-15, 1e-9, 1e-3, 0.5]) * rng.choice([-1, 1]))
    discounts = (rng.choice([None, 0.97, 0.5]),) * 2 if rng.random() < 0.7 else (rng.uniform(0.3, 1), rng.uniform(0.3, 1))
    return near_k, far_k, near_q, far_q, discounts


def _exact_edge(near_k, far_k, near_q, far_q, discounts, offset, sign, rng):
    """The edge the module computes, in exact rational arithmetic from the values the inputs MEANT."""
    d_near, d_far = (Fraction(1) if d is None else _meant(d, rng) for d in discounts)
    credit = (_meant(near_q, rng) / d_near - _meant(far_q, rng) / d_far) / abs(_meant(far_k, rng) - _meant(near_k, rng))
    return offset + sign * credit


@pytest.mark.parametrize("seed", range(200))
def test_every_usable_edge_lies_within_its_proven_error_of_the_exact_rational_edge(seed):
    rng = random.Random(seed)
    near_k, far_k, near_q, far_q, discounts = _random_edge(rng)
    prices = [digital_bounds._forward(q, d) for q, d in zip((near_q, far_q), discounts)]
    for offset, sign in ((0, 1), (0, -1), (1, 1), (1, -1)):
        edge = digital_bounds._spread_edge(near_k, prices[0], far_k, prices[1], offset, sign)
        if edge is None:
            continue
        value, error = edge   # the bound must hold for every edge, usable or not
        for _ in range(32):   # admissible readings of what the floats meant
            exact = _exact_edge(near_k, far_k, near_q, far_q, discounts, offset, sign, rng)
            assert abs(Fraction(value) - exact) <= Fraction(error), (seed, value, float(exact), error)


def test_the_discounted_price_bound_covers_its_inputs_and_its_division_at_their_worst():
    # a locked pair (both legs the same float quote and discount) computes a credit of exactly 0;
    # the quotes and discounts it stands for may differ by their half ulps in OPPOSITE directions,
    # and the division rounds. Search for the quote whose division rounds worst, then take the
    # adversarial corner: the edge's bound must still cover the exact credit.
    d = 0.7
    q = max((1.0 + i / 997.0 for i in range(997)), key=lambda b: abs(Fraction(b / d) - Fraction(b) / Fraction(d)))
    price = digital_bounds._forward(q, d)
    value, error = digital_bounds._spread_edge(1.0, price, 2.0, price, 0, 1)
    up, down = 1 + _U, 1 - _U
    worst = (Fraction(q) * up / (Fraction(d) * down) - Fraction(q) * down / (Fraction(d) * up)) / (
        Fraction(2.0) * down - Fraction(1.0) * up)
    assert value == 0.0 and Fraction(error) >= worst


def test_the_random_edges_exercise_both_usable_and_unusable_pairs():
    usable = 0
    for seed in range(200):
        near_k, far_k, near_q, far_q, discounts = _random_edge(random.Random(seed))
        prices = [digital_bounds._forward(q, d) for q, d in zip((near_q, far_q), discounts)]
        edge = digital_bounds._spread_edge(near_k, prices[0], far_k, prices[1], 0, 1)
        usable += edge is not None and edge[1] <= digital_bounds.EDGE_MAX_ERROR
    assert 40 < usable < 190, usable


@pytest.mark.parametrize("payoff, present", [("above", {"lo": 100.0}), ("below", {"hi": 100.0})])
def test_a_row_without_the_bound_column_its_payoff_never_reads_is_bounded(payoff, present):
    row = {"payoff": payoff, "chain": "x", **present}
    assert run([row], HAND)["records"][0]["dig_status"] == STATUS_OK


@pytest.mark.parametrize("row", [{"payoff": "above", "chain": "x"}, {"payoff": "between", "lo": 90.0, "chain": "x"},
                                 {"payoff": "below", "lo": 100.0, "chain": "x"}])
def test_a_row_missing_a_bound_column_its_payoff_reads_is_bad_bounds_not_a_crash(row):
    assert run([row], HAND)["records"][0]["dig_status"] == "bad_bounds"


def test_a_negative_strike_bound_is_bad_bounds():
    assert run([contract("above", lo=-100.0)], HAND)["records"][0]["dig_status"] == "bad_bounds"


@pytest.mark.parametrize("field, value, needle", [
    ("strike", -100, "strike must be a positive"), ("strike", "100", "strike must be a positive"),
    ("right", "p", "right must be"), ("chain", None, "chain key is missing"), ("chain", "", "chain key is missing"),
    ("chain", 1.5, "refused builtins.float"), ("right", True, "right must be")])
def test_a_quote_row_with_an_unusable_strike_right_or_chain_is_refused_on_both_sides(field, value, needle):
    bad = {**quote("P", 100, 4.0, 4.2), field: value}
    out = run([contract("above", lo=100.0)], [q for q in HAND if not (q["right"] == "P" and q["strike"] == 100)] + [bad])
    refused = [r for r in out["refusals"] if r["side"] == "both"]
    assert len(refused) == 1 and any(needle in m for m in refused[0]["problems"])
    # never filed as a put: the put band would otherwise read P100 and tighten the lower edge to 0.29
    assert out["records"][0]["dig_lower"] == pytest.approx(0.28)


@pytest.mark.parametrize("df", [0.0, -0.9, None, float("inf")])
def test_a_quote_with_an_unusable_discount_factor_is_refused(df):
    quotes = [{**q, "df": 1.0} for q in HAND]
    quotes[1]["df"] = df  # C100
    out = run([contract("above", lo=100.0)], quotes, discount_field="df")
    refused = [r for r in out["refusals"] if r["side"] == "both"]
    assert len(refused) == 1 and "discount factor" in refused[0]["problems"][0]


def test_sizes_without_min_size_must_cover_one_contract():
    assert digital_bounds.DEFAULT_MIN_SIZE == 1
    quotes = [{**q, "bs": 5, "as": 5} for q in HAND]
    quotes[1]["bs"] = 0  # C100: no size on the bid
    out = run([contract("above", lo=100.0)], quotes, bid_size_field="bs", ask_size_field="as")
    sells = [r for r in out["refusals"] if r["side"] == "sell"]
    assert len(sells) == 1 and any("does not cover count 1" in m for m in sells[0]["problems"])
    assert out["census"]["quotes"]["refused"] == 1


def test_a_locked_chain_whose_edges_differ_by_float_dust_is_not_a_crossed_band():
    # zero-spread, linear calls: both edges are 0.45 exactly, but float division gives
    # lower 0.45 and upper 0.4499999999999999 -- dust, not a parity violation
    calls = [quote("C", 90, 9.2, 9.2), quote("C", 100, 4.7, 4.7), quote("C", 110, 0.2, 0.2)]
    row = run([contract("above", lo=100.0)], calls)["records"][0]
    assert row["dig_status"] == STATUS_OK
    assert row["dig_lower"] == pytest.approx(0.45) and row["dig_upper"] == pytest.approx(0.45)


# -- the row_key SPEC table, end to end (R5) ------------------------------------------------------


@pytest.mark.parametrize("a, b, expected", SPEC, ids=SPEC_IDS)
def test_the_spec_table_end_to_end_through_digital_bounds_chains(a, b, expected):
    # the contract's chain cell is a, every quote's chain cell is b
    row = contract("above", lo=100.0, chain=a)
    if a is ABSENT:
        del row["chain"]
    out = run([row], [{**q, "chain": b} for q in HAND])
    assert out["records"][0]["dig_status"] == {"same": STATUS_OK, "different": "no_chain", "refused": "bad_chain_key",
                                               "missing": "no_chain"}[expected]


@pytest.mark.parametrize("a, b, expected", [r for r in SPEC if r[2] in ("same", "different")],
                         ids=[i for i, r in zip(SPEC_IDS, SPEC) if r[2] in ("same", "different")])
def test_the_spec_table_decides_whether_two_quotes_share_a_chain(a, b, expected):
    # C100 listed once under chain a and once under chain b: a duplicate exactly when a and b are one chain
    quotes = [quote("C", 100, 4.0, 4.2, chain=a), quote("C", 100, 4.1, 4.3, chain=b)]
    duplicates = [r for r in run([], quotes)["refusals"] if any("duplicate quote" in m for m in r["problems"])]
    assert len(duplicates) == (2 if expected == "same" else 0)


@pytest.mark.parametrize("a, b, expected", [r for r in SPEC if r[2] in ("refused", "missing")],
                         ids=[i for i, r in zip(SPEC_IDS, SPEC) if r[2] in ("refused", "missing")])
def test_the_spec_table_refuses_a_right_cell_that_is_not_a_key(a, b, expected):
    bad = quote("C", 100, 4.0, 4.2)
    if a is ABSENT:
        del bad["right"]
    else:
        bad["right"] = a
    refused = [r for r in run([], [bad])["refusals"] if r["side"] == "both"]
    assert len(refused) == 1 and any("right must be" in m for m in refused[0]["problems"])


def test_a_composite_spec_key_needs_every_column_to_agree():
    quotes = [{**q, "und": np.str_("X"), "exp": np.int64(16)} for q in HAND]
    rows = [{**contract("above", lo=100.0), "root": "X", "expiry": 16},
            {**contract("above", lo=100.0), "root": "X", "expiry": "16"},
            {**contract("above", lo=100.0), "root": "X", "expiry": True},
            {**contract("above", lo=100.0), "root": "", "expiry": 16},
            {**contract("above", lo=100.0), "root": "", "expiry": True}]
    out = run(rows, quotes, **COMPOSITE)
    assert [r["dig_status"] for r in out["records"]] == [STATUS_OK, "no_chain", "bad_chain_key", "no_chain",
                                                         "bad_chain_key"]


def test_a_bound_field_that_would_overwrite_a_chain_column_is_refused():
    params = {**PARAMS, "chain_fields": ["dig_status"], "quote_chain_fields": ["chain"]}
    assert any("overwrite" in p for p in DigitalBounds.validate_params(params))


def test_the_overwrite_message_is_the_one_binary_curve_owns():
    got = DigitalBounds.validate_params({**PARAMS, "chain_fields": ["dig_status"], "quote_chain_fields": ["chain"]})
    assert ["bound_field 'dig' writes ['dig_lower', 'dig_status', 'dig_upper'], which would overwrite the input "
            "column(s) ['dig_status']"] == [p for p in got if "overwrite" in p]


def test_the_bounds_node_uses_the_binary_curve_owners_of_the_name_payoff_and_collision_rules():
    source = inspect.getsource(digital_bounds)
    assert digital_bounds.name_ok is binary_curve.name_ok
    assert digital_bounds.named_payoff is binary_curve.named_payoff
    assert digital_bounds.output_collision_problems is binary_curve.output_collision_problems
    assert "def _name_ok" not in source and "_name_ok(" not in source and "PAYOFFS.get" not in source
    assert "which would overwrite" not in source and "clash" not in source


@pytest.mark.parametrize("df", [0.0, -0.0])
def test_a_zero_discount_is_refused_once_by_price_ok_never_by_the_floor(df):
    quotes = [{**q, "df": 1.0} for q in HAND]
    quotes[1]["df"] = df
    refused = [r for r in run([contract("above", lo=100.0)], quotes, discount_field="df")["refusals"]
               if r["side"] == "both"]
    assert len(refused) == 1 and len(refused[0]["problems"]) == 1, "zero is not below the floor: it is no price"


# -- the interval arithmetic's own error, against exact rationals (R7a) -------------------------------


_EDGES = st.tuples(st.floats(-2.0, 2.0, allow_nan=False, allow_subnormal=False),
                   st.sampled_from([0.0, 1e-17, 1e-12, 3e-10]))


@settings(max_examples=500, deadline=None)
@given(constant=st.sampled_from([0.0, 1.0]), coefficients=st.lists(st.sampled_from([1.0, -1.0, 0.5, -2.0]),
                                                                    min_size=1, max_size=3),
       edges=st.lists(st.tuples(_EDGES, _EDGES), min_size=3, max_size=3), corner=st.integers(0, 2 ** 6 - 1))
def test_the_interval_error_covers_every_exact_value_its_edges_allow(constant, coefficients, edges, corner):
    terms = {100.0 + i: c for i, c in enumerate(coefficients)}
    bands = {k: (lo, hi) for k, (lo, hi) in zip(terms, edges)}
    low, high = digital_bounds._interval(constant, terms, bands.__getitem__)
    for out, side in ((low, 0), (high, 1)):
        exact = Fraction(constant)
        for i, (strike, coefficient) in enumerate(terms.items()):
            take = side if coefficient > 0 else 1 - side
            value, error = bands[strike][take]
            sign = 1 if (corner >> (2 * i + side)) & 1 else -1
            exact += Fraction(coefficient) * (Fraction(value) + sign * Fraction(error))
        clip = max(Fraction(0), exact) if side == 0 else min(Fraction(1), exact)
        # the error sum is itself a float sum and may round down by an ulp of the bound: real edges carry
        # _SLACK = 2 on their own errors, far more than that ulp, so the test grants it explicitly
        assert abs(Fraction(out[0]) - clip) <= Fraction(out[1]) * (1 + 4 * _U), (out, float(clip))


# -- the price floor on this node's own quote check (R7b) --------------------------------------------


def test_the_price_floor_is_one_public_name_far_below_any_real_price():
    assert digital_bounds.PRICE_FLOOR == 1e-12 and "PRICE_FLOOR" in digital_bounds.__all__


@pytest.mark.parametrize("field, value, side", [
    ("bid", 5e-13, "sell"), ("bid", 5e-324, "sell"), ("ask", 1e-300, "buy"), ("strike", 1e-13, "both"),
    ("df", 5e-324, "both")])
def test_a_nonzero_price_below_the_floor_is_refused_by_name(field, value, side):
    quotes = [{**q, "df": 1.0} for q in HAND]
    quotes[1] = {**quotes[1], field: value}   # C100
    if field == "ask":
        quotes[1]["bid"] = 0.0
    out = run([contract("above", lo=100.0)], quotes, discount_field="df")
    named = [r for r in out["refusals"] if r["side"] == side and any("PRICE_FLOOR" in m for m in r["problems"])]
    assert len(named) == 1


def test_a_price_at_the_floor_is_used_and_records_price_ok_is_untouched():
    from dskit.pipeline.records import price_ok

    calls = [quote("C", 90, 2e-12, 2e-12), quote("C", 100, 1e-12, 1e-12), quote("C", 110, 1e-12, 1e-12)]
    assert run([contract("above", lo=100.0)], calls)["refusals"] == []
    assert price_ok(1e-300), "the floor is DigitalBounds' own rule, never records.price_ok's"


def test_a_subnormal_quote_cannot_reach_the_proven_bound():
    # a subnormal rounds with an absolute error the relative proof does not cover; it never becomes an edge
    calls = [quote("C", 90, 3e-320, 3e-320), quote("C", 100, 2e-320, 2e-320), quote("C", 110, 1e-320, 1e-320)]
    out = run([contract("above", lo=100.0)], calls)
    assert out["records"][0]["dig_status"] == "no_bracket"
    assert out["census"]["quotes"]["refused"] == 3


# -- a locked, coherent chain never reports crossed_band (R7a) ----------------------------------------


@st.composite
def _locked_chain(draw):
    """Calls and puts of an exact discrete law on a strike grid, each quote the correctly rounded float."""
    step = Fraction(draw(st.sampled_from(["0.05", "0.5", "1", "2.5", "5"])))
    base = Fraction(draw(st.integers(1, 10 ** 5))) * step
    n = draw(st.integers(6, 14))
    strikes = [base + i * step for i in range(n)]
    atoms = draw(st.lists(st.integers(-3, n + 3), min_size=1, max_size=3, unique=True))
    weights = [draw(st.integers(1, 9)) for _ in atoms]
    law = [(base + a * step, Fraction(w, sum(weights))) for a, w in zip(atoms, weights)]
    law = [(s, p) for s, p in law if s > 0] or [(base, Fraction(1))]
    forward = sum(s * p for s, p in law)
    quotes = []
    for k in strikes:
        call = sum(p * max(s - k, 0) for s, p in law)
        for right, value in (("C", call), ("P", call - (forward - k))):
            price = float(value)
            quotes.append(quote(right, float(k), price, price))
    inner = strikes[1:-1]
    lo, hi = sorted(draw(st.lists(st.sampled_from(inner), min_size=2, max_size=2, unique=True)))
    k = draw(st.sampled_from(inner))

    def above(x):
        return sum(p for s, p in law if s >= x)

    truths = (above(k), 1 - above(k), above(lo) - above(hi))
    return quotes, float(k), float(lo), float(hi), truths


@settings(max_examples=400, deadline=None)
@given(chain=_locked_chain())
def test_a_locked_coherent_chain_never_reports_crossed_band(chain):
    quotes, k, lo, hi, truths = chain
    out = run([contract("above", lo=k), contract("below", hi=k), contract("between", lo=lo, hi=hi)], quotes)
    assert [r["dig_status"] for r in out["records"]].count("crossed_band") == 0, out["records"]
    for row, truth in zip(out["records"], truths):   # and every edge present holds the law's own digital
        assert row["dig_lower"] is None or row["dig_lower"] - 1e-9 <= truth, (row, float(truth))
        assert row["dig_upper"] is None or truth <= row["dig_upper"] + 1e-9, (row, float(truth))


# -- the graduated quote rule ---------------------------------------------------------------------


@pytest.mark.parametrize("args, needle", [
    ((1.0, 0.5, 1, 1, 0), "uncrossed"),
    ((-0.1, 0.5, 1, 1, 0), "nonnegative"),
    ((0.0, 0.5, 1, 1, 1, "sell"), "bid must be positive to sell"),
    ((0.1, 0.0, 1, 1, 1, "buy"), "ask must be positive to buy"),
    ((0.1, 0.2, 1, 0, 1, "buy"), "ask_size 0 does not cover count 1"),
    ((float("nan"), 0.2, 1, 1, 0), "bid must be a finite number"),
])
def test_quote_problems_names_each_rule(args, needle):
    assert any(needle in p for p in quote_problems(*args))


BAD_SIZES = [None, -1, -0.5, "5", float("nan"), float("inf"), True, b"5"]


@pytest.mark.parametrize("bad", BAD_SIZES, ids=repr)
def test_quote_problems_refuses_a_size_that_is_not_a_nonnegative_number_whatever_the_count(bad):
    # not only a size below the count: a missing, negative or non-number size is its own named problem
    for count in (0, 1):
        assert any("bid_size must be a number >= 0" in p for p in quote_problems(0.1, 0.2, bad, 9, count, "sell"))
        assert any("ask_size must be a number >= 0" in p for p in quote_problems(0.1, 0.2, 9, bad, count, "buy"))
        both = quote_problems(0.1, 0.2, bad, bad, count)
        assert any("bid_size must be" in p for p in both) and any("ask_size must be" in p for p in both)
    # a named side judges only its own size
    assert quote_problems(0.1, 0.2, 9, bad, 1, "sell") == []
    assert quote_problems(0.1, 0.2, bad, 9, 1, "buy") == []


def test_quote_problems_passes_a_clean_quote_and_an_unbid_wing_stays_buyable():
    assert quote_problems(0.1, 0.2, 3, 3, 3) == []
    assert quote_problems(0.0, 0.05, 0, 4, 2, side="buy") == []  # the far wing with no bid
    with pytest.raises(ValueError):
        quote_problems(0.1, 0.2, 1, 1, 1, side="hold")
    with pytest.raises(ValueError):
        quote_problems(0.1, 0.2, 1, 1, -1)


# -- params, contract, platform -------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(PARAMS))
def test_every_required_column_knob_is_named_when_missing(name):
    params = {k: v for k, v in PARAMS.items() if k != name}
    assert any(name in p for p in DigitalBounds.validate_params(params))


def test_unknown_knobs_and_half_declared_sizes_are_refused():
    assert any("unknown" in p for p in DigitalBounds.validate_params({**PARAMS, "spread": 1}))
    assert any("size" in p for p in DigitalBounds.validate_params({**PARAMS, "bid_size_field": "bs"}))
    assert any("min_size" in p for p in DigitalBounds.validate_params({**PARAMS, "min_size": 2}))
    assert any("call_value" in p for p in DigitalBounds.validate_params({**PARAMS, "put_value": "C"}))
    assert any("overwrite" in p for p in DigitalBounds.validate_params({**PARAMS, "bound_field": "lo",
                                                                        "lower_field": "lo_lower"}))


def test_the_node_declares_its_contract_and_its_public_api():
    assert DigitalBounds.role == "transform"
    assert DigitalBounds.outputs == ("records", "refusals", "census")
    node = DigitalBounds("b", PARAMS)
    assert node.validate_inputs({"records": [], "quotes": []}) == []
    assert node.validate_inputs({"records": iter([]), "quotes": []})
    assert not [n for n in digital_bounds.__all__ if n.startswith("_")]


def _probes(tmp_path):
    return {"digital-bounds": NodeProbe(
        params=dict(PARAMS), required=tuple(PARAMS),
        inputs={"records": [contract("above", lo=100.0)], "quotes": list(HAND)},
        stream_ports=("records", "quotes"), runnable=True)}


TestDigitalBoundsConformance = conformance_suite(
    registry=(("digital-bounds", DigitalBounds),), module="dskit.pipeline.digital_bounds", probes=_probes,
    expected_roles={"digital-bounds": "transform"}, name="TestDigitalBoundsConformance")
