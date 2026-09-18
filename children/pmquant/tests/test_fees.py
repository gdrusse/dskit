"""Fees: both venue rounding models (golden cases from the venues' own
published numbers), the venue dispatch, and the dated fee book — every
hole refuses by name, nothing is defaulted or interpolated."""

import datetime as dt

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pmquant.fees import (
    CLOSE_TS_FIELD,
    DEFAULT_FILL_FEE_POLICY,
    FEE_ROUNDING,
    FILL_FEE_POLICIES,
    ConservativeFee,
    FeeBook,
    FeeRateUnresolved,
    FillFeePolicy,
    OrderVwapFee,
    PerFillFee,
    close_ts_of,
    fill_cost_for_series,
    fill_fee_policy,
    kalshi_trading_fee,
    poly_trading_fee,
    resolve_fee_rates,
    trading_fee_for_series,
)
from pmquant.ladder.protocols import VENUES, scope_to_venue, venue_of

GUARD = 1e-9
QUANTUM = 1e-5


# --- Kalshi: ceil-to-cent -------------------------------------------------


def test_kalshi_fee_at_fifty_cents_round_lot_is_the_float_trap_case():
    # 0.07 * 100 * 0.25 is not exactly 1.75 in binary; the 9-dp snap keeps
    # the ceiling at 1.75, not 1.76.
    assert kalshi_trading_fee(100, 0.50, 0.07) == pytest.approx(1.75)


def test_kalshi_sub_cent_fee_floors_at_one_cent():
    assert kalshi_trading_fee(1, 0.99, 0.07) == pytest.approx(0.01)


def test_kalshi_degenerate_prices_have_zero_fee():
    assert kalshi_trading_fee(1000, 0.0, 0.07) == 0.0
    assert kalshi_trading_fee(1000, 1.0, 0.07) == 0.0


def test_kalshi_fee_is_symmetric_about_one_half():
    assert kalshi_trading_fee(50, 0.30, 0.07) == kalshi_trading_fee(50, 0.70, 0.07)


def test_kalshi_ceils_an_on_grid_poly_value():
    # 0.07 * 100 * 0.25 * 0.75 = 1.3125: Kalshi ceils to 1.32, poly keeps it.
    assert kalshi_trading_fee(100, 0.25, 0.07) == pytest.approx(1.32, abs=GUARD)
    assert poly_trading_fee(100, 0.25, 0.07) == pytest.approx(1.3125, abs=GUARD)


# --- Polymarket: nearest 1e-5, no floor -----------------------------------


def test_poly_headline_peak():
    assert poly_trading_fee(100, 0.5, 0.07) == pytest.approx(1.75, abs=GUARD)


def test_poly_subquantum_fee_rounds_to_zero_and_small_fee_to_one_quantum():
    assert poly_trading_fee(1, 0.00005, 0.07) == 0.0
    assert poly_trading_fee(1, 0.0001, 0.07) == pytest.approx(QUANTUM, abs=GUARD)


def test_poly_float_dust_guard():
    assert poly_trading_fee(300, 0.35, 0.07) == pytest.approx(4.7775, abs=GUARD)


def test_poly_exact_half_quantum_ties_round_up_at_every_fill_size():
    # 0.07 * 1 * 0.05 * 0.95 = 332.5 units: a tie, half-up -> 333
    assert poly_trading_fee(1, 0.05, 0.07) == pytest.approx(333 * QUANTUM, abs=GUARD)
    # 0.07 * 10001 * 0.0475 = 3_325_332.5 units: the same tie at ~3e6 units, where
    # the product's float dust (~1e-9) exceeds an absolute 5e-10 guard and only
    # the RELATIVE tolerance keeps the tie deciding the same way
    assert poly_trading_fee(10001, 0.05, 0.07) == pytest.approx(3_325_333 * QUANTUM, abs=GUARD)
    # a tiny rate never rounds a positive Kalshi fee below the one-cent floor
    assert kalshi_trading_fee(1000, 0.5, 1e-9) == 0.01
    assert poly_trading_fee(1000, 0.5, 1e-9) == 0.0  # no floor at that venue


def test_poly_zero_cases_and_sourced_zero_rate():
    assert poly_trading_fee(0, 0.5, 0.07) == 0.0
    assert poly_trading_fee(100, 0.0, 0.07) == 0.0
    assert poly_trading_fee(100, 1.0, 0.07) == 0.0
    assert poly_trading_fee(1_000, 0.5, 0.0) == 0.0


@pytest.mark.parametrize("fee", [kalshi_trading_fee, poly_trading_fee])
def test_validation_failures_refuse_by_name(fee):
    with pytest.raises(ValueError, match="contracts"):
        fee(-1, 0.5, 0.07)
    with pytest.raises(ValueError, match="contracts"):
        fee(1.5, 0.5, 0.07)
    with pytest.raises(ValueError, match="price"):
        fee(10, 1.5, 0.07)
    with pytest.raises(ValueError, match="rate"):
        fee(10, 0.5, -0.01)
    with pytest.raises(FeeRateUnresolved, match="missing"):
        fee(10, 0.5, None)


_contracts = st.integers(min_value=0, max_value=100_000)
_prices = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@given(contracts=_contracts, price=_prices)
@settings(max_examples=200, deadline=None)
def test_poly_fee_is_on_grid_nonnegative_and_never_exceeds_kalshi(contracts, price):
    fee = poly_trading_fee(contracts, price, 0.07)
    assert fee >= 0.0
    assert abs(fee * 1e5 - round(fee * 1e5)) < 1e-6
    assert fee <= kalshi_trading_fee(contracts, price, 0.07) + GUARD


@given(contracts=_contracts, price=_prices)
@settings(max_examples=200, deadline=None)
def test_both_models_are_symmetric_in_price(contracts, price):
    assert poly_trading_fee(contracts, price, 0.07) == pytest.approx(
        poly_trading_fee(contracts, 1.0 - price, 0.07), abs=2 * QUANTUM
    )
    assert kalshi_trading_fee(contracts, price, 0.07) == pytest.approx(
        kalshi_trading_fee(contracts, 1.0 - price, 0.07), abs=0.01 + GUARD
    )


# --- venue dispatch -------------------------------------------------------


def test_venue_table_is_the_one_declaration():
    assert VENUES == ("kalshi", "polymarket")
    assert venue_of("KXHIGHNY") == "kalshi"
    assert venue_of("POLYBTCUPDOWN5M") == "polymarket"
    with pytest.raises(ValueError, match="no declared venue prefix"):
        venue_of("SPX-2026")
    assert venue_of("SPX-2026", default="kalshi") == "kalshi"
    assert set(FEE_ROUNDING) == set(VENUES)


def test_scope_to_venue_refuses_orphans_rather_than_dropping_them():
    assert scope_to_venue(["KXA", "POLYB", "KXC"], "kalshi") == ["KXA", "KXC"]
    with pytest.raises(ValueError, match="no declared venue prefix"):
        scope_to_venue(["KXA", "SPX"], "kalshi")
    with pytest.raises(ValueError, match="unknown venue"):
        scope_to_venue(["KXA"], "nyse")


def test_dispatch_selects_the_rounding_rule_and_requires_a_rate_for_both():
    assert trading_fee_for_series("POLYX", 100, 0.25, 0.07) == pytest.approx(1.3125, abs=GUARD)
    assert trading_fee_for_series("KXX", 100, 0.25, 0.07) == pytest.approx(1.32, abs=GUARD)
    for series in ("POLYX", "KXX"):
        with pytest.raises(FeeRateUnresolved, match="missing"):
            trading_fee_for_series(series, 100, 0.25, None)
    with pytest.raises(ValueError, match="no declared venue prefix"):
        trading_fee_for_series("SPX", 100, 0.25, 0.07)


def test_fill_cost_decomposes():
    assert fill_cost_for_series("KXX", 100, 0.40, 0.07) == pytest.approx(
        40.0 + kalshi_trading_fee(100, 0.40, 0.07)
    )


# --- the dated fee book ---------------------------------------------------

WINDOW_LO = "2026-02-24T00:00:00Z"
WINDOW_HI = "2026-08-12T00:00:00Z"
SWITCH = "2026-03-30T12:00:00Z"


def ms(iso):
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def _clause(op, value):
    return {"field": CLOSE_TS_FIELD, "op": op, "value": value}


def _weather_entry():
    return {
        "source": "https://gamma-api.polymarket.com/events?series_slug=atlanta",
        "retrieved": "2026-08-16",
        "cases": [
            {"when": [_clause(">=", WINDOW_LO), _clause("<", SWITCH)], "value": 0.0,
             "fee_type": None, "note": "feesEnabled:false"},
            {"when": [_clause(">=", SWITCH), _clause("<", WINDOW_HI)], "value": 0.05,
             "fee_type": "weather_fees"},
        ],
    }


def test_a_scalar_document_is_the_dict_it_was():
    declared = {"KXHIGHDEN": 0.07, "KXLOWTNYC": 0.035}
    book = FeeBook.from_document(declared)
    assert dict(book) == declared
    assert book["KXHIGHDEN"] == 0.07 and "KXLOWTNYC" in book and len(book) == 2
    assert FeeBook.from_document(book) is book
    assert not book.is_time_varying("KXHIGHDEN")


def test_the_boundary_is_inclusive_at_its_lower_edge_and_the_key_is_the_instant():
    book = FeeBook.from_document({"POLYWX": _weather_entry()})
    assert book.rate_for("POLYWX", ms("2026-03-30T11:59:59Z")) == 0.0
    assert book.rate_for("POLYWX", ms(SWITCH)) == 0.05
    assert close_ts_of(ms(SWITCH)) == SWITCH
    assert book.rate_for("POLYWX", ms("2026-03-30T00:00:00Z")) == 0.0
    assert book.rate_for("POLYWX", ms("2026-03-30T23:00:00Z")) == 0.05
    assert book.time_varying_series == ("POLYWX",)
    assert book.provenance_for("POLYWX")["retrieved"] == "2026-08-16"


def test_a_date_shaped_bound_still_works():
    book = FeeBook.from_document({"POLYX": [
        {"when": [_clause(">=", "2026-02-24"), _clause("<", "2026-04-01")], "value": 0.0},
        {"when": [_clause(">=", "2026-04-01"), _clause("<", "2026-08-12")], "value": 0.05},
    ]})
    assert book.rate_for("POLYX", ms("2026-03-31T23:59:00Z")) == 0.0
    assert book.rate_for("POLYX", ms("2026-04-01T00:00:00Z")) == 0.05


def test_every_hole_refuses_by_name():
    book = FeeBook.from_document({"POLYWX": _weather_entry(), "KXA": 0.07})
    with pytest.raises(FeeRateUnresolved, match="no fee rate declared"):
        book.rate_for("KXB")
    with pytest.raises(FeeRateUnresolved, match="TIME-VARYING"):
        book.rate_for("POLYWX")
    with pytest.raises(FeeRateUnresolved, match="FAIL-CLOSED"):
        book.rate_for("POLYWX", ms("2026-09-01T00:00:00Z"))
    with pytest.raises(FeeRateUnresolved, match="time-varying"):
        book["POLYWX"]
    with pytest.raises(KeyError):
        book["KXB"]
    # at() omits what it cannot price rather than inventing a number.
    assert book.at(ms("2026-09-01T00:00:00Z")) == {"KXA": 0.07}
    assert book.at(ms("2026-05-01T00:00:00Z")) == {"KXA": 0.07, "POLYWX": 0.05}


def test_an_unpriceable_span_refuses_and_quotes_the_reason():
    book = FeeBook.from_document({"POLYX": [
        {"when": [_clause("<", SWITCH)], "value": 0.0},
        {"when": [_clause(">=", SWITCH)], "value": None, "fee_type": "crypto_fees",
         "unpriceable": "exponent-2 schedule"},
    ]})
    with pytest.raises(FeeRateUnresolved, match="exponent-2 schedule"):
        book.rate_for("POLYX", ms("2026-05-01T00:00:00Z"))


def test_parse_time_refusals():
    with pytest.raises(ValueError, match="unpriceable"):
        FeeBook.from_document({"POLYX": [{"when": [], "value": None}]})
    with pytest.raises(ValueError, match="LAST case"):
        FeeBook.from_document({"POLYX": [
            {"when": [], "value": 0.0},
            {"when": [_clause(">=", SWITCH)], "value": 0.05},
        ]})
    with pytest.raises(ValueError, match="declares no cases"):
        FeeBook.from_document({"POLYX": {"cases": []}})
    with pytest.raises(ValueError, match="op must be one of"):
        FeeBook.from_document({"POLYX": [{"when": [{"field": "close_ts", "op": "~", "value": 1}],
                                          "value": 0.0}]})
    with pytest.raises(ValueError, match=r"\[0, 1\)"):
        FeeBook.from_document({"KXA": 1.5})
    with pytest.raises(ValueError, match="mapping"):
        FeeBook.from_document([("KXA", 0.07)])


def test_a_catch_all_answers_without_a_close_instant():
    book = FeeBook.from_document({"POLYX": [{"when": [], "value": 0.04}]})
    assert book.rate_for("POLYX") == 0.04


def test_resolve_fee_rates_names_every_failure():
    declared = {"POLYWX": _weather_entry(), "KXA": 0.07}
    out = resolve_fee_rates(declared, ["KXA", "POLYWX"],
                            close_ms_by={"POLYWX": ms("2026-05-01T00:00:00Z")})
    assert out == {"KXA": 0.07, "POLYWX": 0.05}
    with pytest.raises(FeeRateUnresolved, match="cannot price 2 series"):
        resolve_fee_rates(declared, ["KXB", "POLYWX"], where="size")


# --- how a venue bills a SET of fills (PM-01) -----------------------------
#
# The independent restatement again: exact decimal, nothing imported from
# the module under test. Prices and rates arrive as decimal STRINGS.


def decimal_fee(venue, contracts, price, rate):
    """One venue fee on one billable quantity, in exact decimal."""
    from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

    raw = Decimal(str(rate)) * Decimal(int(contracts)) * Decimal(str(price)) * (
        Decimal(1) - Decimal(str(price))
    )
    if venue == "kalshi":
        return float(raw.quantize(Decimal("0.01"), rounding=ROUND_CEILING))
    return float(raw.quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP))


WIDE = (("0.30", 500), ("0.40", 500))  # the audit's worked example
TINY = (("0.02", 1), ("0.03", 1))  # two lots, two levels, one cent each


def _floats(fills):
    return tuple((float(p), n) for p, n in fills)


def test_the_policy_table_is_the_one_declaration_and_names_match():
    assert set(FILL_FEE_POLICIES) == {"order_vwap", "per_fill", "conservative"}
    for name, policy in FILL_FEE_POLICIES.items():
        assert isinstance(policy, FillFeePolicy) and policy.name == name
    assert DEFAULT_FILL_FEE_POLICY == "order_vwap"
    assert fill_fee_policy(None) is FILL_FEE_POLICIES[DEFAULT_FILL_FEE_POLICY]
    assert fill_fee_policy("per_fill") is FILL_FEE_POLICIES["per_fill"]
    with pytest.raises(ValueError, match="fill fee policy"):
        fill_fee_policy("vwap")


def test_the_abstract_policy_cannot_be_instantiated():
    with pytest.raises(TypeError):
        FillFeePolicy()


def test_each_policy_reproduces_the_audits_worked_example():
    assert OrderVwapFee().fee_for("KXA", _floats(WIDE), 0.07) == pytest.approx(
        decimal_fee("kalshi", 1000, "0.35", "0.07"), abs=GUARD
    )
    assert OrderVwapFee().fee_for("KXA", _floats(WIDE), 0.07) == pytest.approx(15.93, abs=GUARD)
    per_fill = sum(decimal_fee("kalshi", n, p, "0.07") for p, n in WIDE)
    assert PerFillFee().fee_for("KXA", _floats(WIDE), 0.07) == pytest.approx(per_fill, abs=GUARD)
    assert PerFillFee().fee_for("KXA", _floats(WIDE), 0.07) == pytest.approx(15.75, abs=GUARD)
    # the gap the MIO's one-cent allowance was standing in for
    assert 15.93 - 15.75 > 0.01


def test_no_fixed_cent_bounds_the_gap_and_neither_aggregation_dominates():
    # Wide fills: Jensen makes the ORDER-level charge the larger one, and by
    # eighteen times the cent the linear program bills.
    assert OrderVwapFee().fee_for("KXA", _floats(WIDE), 0.07) > PerFillFee().fee_for(
        "KXA", _floats(WIDE), 0.07
    )
    # Tiny fills: Kalshi's per-ORDER cent floor reverses it, so "order VWAP is
    # always the conservative one" is FALSE once rounding enters.
    assert OrderVwapFee().fee_for("KXA", _floats(TINY), 0.07) == pytest.approx(0.01, abs=GUARD)
    assert PerFillFee().fee_for("KXA", _floats(TINY), 0.07) == pytest.approx(0.02, abs=GUARD)
    assert ConservativeFee().fee_for("KXA", _floats(TINY), 0.07) == pytest.approx(0.02, abs=GUARD)
    assert ConservativeFee().fee_for("KXA", _floats(WIDE), 0.07) == pytest.approx(15.93, abs=GUARD)


def test_the_polymarket_grid_has_no_cent_floor_under_either_aggregation():
    assert OrderVwapFee().fee_for("POLYA", _floats(WIDE), 0.07) == pytest.approx(
        decimal_fee("polymarket", 1000, "0.35", "0.07"), abs=GUARD
    )
    assert OrderVwapFee().fee_for("POLYA", _floats(WIDE), 0.07) == pytest.approx(15.925, abs=GUARD)
    assert PerFillFee().fee_for("POLYA", _floats(TINY), 0.07) == pytest.approx(
        decimal_fee("polymarket", 1, "0.02", "0.07")
        + decimal_fee("polymarket", 1, "0.03", "0.07"),
        abs=GUARD,
    )
    # a sub-half-quantum poly fee is a real zero, where Kalshi floors at a cent
    assert PerFillFee().fee_for("POLYA", ((0.00005, 1),), 0.07) == 0.0
    assert PerFillFee().fee_for("KXA", ((0.00005, 1),), 0.07) == pytest.approx(0.01, abs=GUARD)


def test_the_policies_price_nothing_for_nothing_and_refuse_a_fractional_lot():
    for policy in FILL_FEE_POLICIES.values():
        assert policy.fee_for("KXA", (), 0.07) == 0.0
        assert policy.fee_for("KXA", ((0.30, 0),), 0.07) == 0.0
        with pytest.raises(ValueError, match="whole number"):
            policy.fee_for("KXA", ((0.30, 1.5),), 0.07)
        with pytest.raises(ValueError, match="non-negative"):
            policy.fee_for("KXA", ((0.30, -1),), 0.07)
        with pytest.raises(FeeRateUnresolved):
            policy.fee_for("KXA", ((0.30, 10),), None)
    with pytest.raises(ValueError, match="FillFeePolicy"):
        ConservativeFee([object()])
    assert [m.name for m in ConservativeFee().members] == ["order_vwap", "per_fill"]
    assert [m.name for m in ConservativeFee([PerFillFee()]).members] == ["per_fill"]
    with pytest.raises(ValueError, match="at least one"):
        ConservativeFee([])


@given(
    fills=st.lists(
        st.tuples(st.integers(min_value=1, max_value=98), st.integers(min_value=1, max_value=4000)),
        min_size=1,
        max_size=5,
    ),
    rate=st.sampled_from([0.0, 0.01, 0.07, 0.2]),
)
@settings(max_examples=200, deadline=None)
def test_the_unrounded_gap_between_the_two_aggregations_is_rate_times_n_times_variance(
    fills, rate
):
    rows = tuple((cents / 100.0, lots) for cents, lots in fills)
    n = sum(lots for _p, lots in rows)
    vwap = sum(p * lots for p, lots in rows) / n
    raw_order = rate * n * vwap * (1.0 - vwap)
    raw_per_fill = sum(rate * lots * p * (1.0 - p) for p, lots in rows)
    variance = sum(lots * (p - vwap) ** 2 for p, lots in rows) / n
    # Jensen, restated: the order-level charge exceeds the per-fill sum by
    # exactly rate * n * Var(price), which is >= 0 and is NOT a fixed cent.
    assert raw_order - raw_per_fill == pytest.approx(rate * n * variance, abs=1e-9)
    assert raw_order >= raw_per_fill - 1e-12


@given(
    fills=st.lists(
        st.tuples(st.integers(min_value=1, max_value=98), st.integers(min_value=1, max_value=4000)),
        min_size=1,
        max_size=5,
    ),
    rate=st.sampled_from([0.0, 0.01, 0.07, 0.2]),
    series=st.sampled_from(["KXA", "POLYA"]),
)
@settings(max_examples=200, deadline=None)
def test_the_conservative_policy_bounds_every_member_at_every_venue(fills, rate, series):
    rows = tuple((cents / 100.0, lots) for cents, lots in fills)
    order = OrderVwapFee().fee_for(series, rows, rate)
    per_fill = PerFillFee().fee_for(series, rows, rate)
    conservative = ConservativeFee().fee_for(series, rows, rate)
    assert conservative >= order - GUARD
    assert conservative >= per_fill - GUARD
    assert conservative == pytest.approx(max(order, per_fill), abs=GUARD)


def test_the_child_prices_taker_fills_only_so_there_is_no_maker_branch():
    # Both sides of a binary contract cross an ASK (books.walk_book walks
    # asks; entry_gate prices asks), and a rate is keyed on (series, close
    # instant) with no maker/taker dimension. So a YES fill at p and the
    # mirrored NO fill at 1-p bill the SAME schedule, and a maker rebate
    # would be a new fee-book dimension, not a knob a policy could hide.
    yes = ((0.30, 400), (0.40, 100))
    no = ((0.70, 400), (0.60, 100))
    for policy in FILL_FEE_POLICIES.values():
        assert policy.fee_for("KXA", yes, 0.07) == pytest.approx(
            policy.fee_for("KXA", no, 0.07), abs=GUARD
        )
