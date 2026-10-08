"""Executable digital bounds from vertical spreads (ADR-0249 part 1).

Every expected number is worked by hand in the comments or is a Black-76 closed form restated here
(``N(d2)``), never read back from the module under test.
"""

import math

import pytest

from dskit.pipeline import digital_bounds
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.digital_bounds import STATUS_OK, STATUSES, DigitalBounds, quote_problems
from dskit.pipeline.option_pricing import black76

PARAMS = {
    "payoff_field": "payoff", "lower_field": "lo", "upper_field": "hi", "chain_field": "chain",
    "quote_chain_field": "chain", "strike_field": "strike", "right_field": "right",
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


def test_a_strike_and_right_listed_twice_is_refused_both_times_by_name():
    quotes = list(HAND) + [quote("C", 100, 4.1, 4.3)]
    out = run([contract("above", lo=100.0)], quotes)
    duplicates = [r for r in out["refusals"] if any("duplicate quote" in m for m in r["problems"])]
    assert len(duplicates) == 2, "neither copy of a repeated strike/right may be used"


def test_a_list_valued_chain_key_is_one_chain_not_a_crash():
    key = ["X", "2026-10-16"]
    quotes = [{**q, "chain": list(key)} for q in HAND]
    row = run([contract("above", lo=100.0, chain=list(key))], quotes)["records"][0]
    assert row["dig_status"] == STATUS_OK
    assert row["dig_lower"] == pytest.approx(0.29)


def test_a_locked_chain_whose_edges_differ_by_float_dust_is_not_a_crossed_band():
    # zero-spread, linear calls: both edges are 0.45 exactly, but float division gives
    # lower 0.45 and upper 0.4499999999999999 -- dust, not a parity violation
    calls = [quote("C", 90, 9.2, 9.2), quote("C", 100, 4.7, 4.7), quote("C", 110, 0.2, 0.2)]
    row = run([contract("above", lo=100.0)], calls)["records"][0]
    assert row["dig_status"] == STATUS_OK
    assert row["dig_lower"] == pytest.approx(0.45) and row["dig_upper"] == pytest.approx(0.45)


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
