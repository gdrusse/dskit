"""Independent, exact cashflow acceptance examples."""

import pytest

from index_options.contracts import DefinedRiskCondor


@pytest.mark.parametrize("version, expected", [
    ("center", "252"), ("down", "-248"), ("up", "-248"),
])
def test_cashflows_include_both_wings_and_fees(rows, version, expected):
    settlement = next(r for r in rows["settlements"] if r["row_version"] == version)
    condor = DefinedRiskCondor(
        rows["contracts"], rows["quotes"], settlement,
        count=1, fees_usd="8.00", quantities=[1, -1, -1, 1],
    )
    # Put credit 1.60 + call credit 1.00 = 2.60, less USD 8 fees.
    # This fixture's long call ask is 1.80, so call credit is 1.00.
    # Values below are pinned to the complete four-leg arithmetic.
    assert condor.evaluate().get("net_pnl_usd") == expected

from decimal import Decimal, localcontext

from index_options.contracts import CashIndexContract


def position(rows, **overrides):
    kwargs = dict(
        contracts=rows["contracts"], quotes=rows["quotes"],
        settlement=rows["settlements"][0], count=1, fees_usd="8",
        quantities=[1, -1, -1, 1],
    )
    kwargs.update(overrides)
    return DefinedRiskCondor(**kwargs)


@pytest.mark.parametrize("level, terminal, net", [
    ("470", "-500", "-248"), ("480", "-500", "-248"),
    ("482", "-300", "-48"), ("485", "0", "252"),
    ("500", "0", "252"), ("515", "0", "252"),
    ("517", "-200", "52"), ("520", "-500", "-248"), ("530", "-500", "-248"),
])
def test_strikes_interiors_and_tails(rows, level, terminal, net):
    rows["settlements"][0]["value"] = level
    report = position(rows).evaluate()
    assert report["entry_credit_points"] == "2.6"
    assert report["entry_cashflow_usd"] == "260"
    assert report["settlement_cashflow_usd"] == terminal
    assert report["net_pnl_usd"] == net
    assert sum(Decimal(leg["entry_cashflow_usd"]) for leg in report["legs"]) == 260
    assert sum(Decimal(leg["settlement_cashflow_usd"]) for leg in report["legs"]) == Decimal(terminal)


@pytest.mark.parametrize("fees, expected", [("0", "520"), ("8", "512")])
def test_scaling_and_fees_are_whole_outcome(rows, fees, expected):
    report = position(rows, count=2, fees_usd=fees).evaluate()
    assert report["net_pnl_usd"] == expected
    assert report["max_loss_before_fees_usd"] == "480"
    assert [leg["quantity"] for leg in report["legs"]] == [2, -2, -2, 2]


@pytest.mark.parametrize("level, net", [("470", "-248"), ("540", "-748")])
def test_unequal_wings_use_wider_worst_loss(rows, level, net):
    rows["contracts"][3]["strike"] = "525"
    rows["settlements"][0]["value"] = level
    report = position(rows).evaluate()
    assert report["net_pnl_usd"] == net
    assert report["max_loss_before_fees_usd"] == "740"
    assert report["max_loss_after_fees_usd"] == "748"


@pytest.mark.parametrize("field, value", [
    ("multiplier", 0), ("multiplier", -1), ("multiplier", True), ("multiplier", 100.0),
    ("exercise_style", "american"), ("settlement_type", "physical"),
    ("settlement_style", "am"), ("currency", "EUR"), ("right", "stock"),
    ("expiry", "2026-02-30"), ("contract_id", ""), ("provenance", "vendor"),
    ("known_at_basis", "inferred"), ("schema_version", "v2"),
    ("effective_at", "2026-01-01"), ("known_at", "2026-01-01T00:00:00"),
    ("last_trade_at", "2026-02-21T00:00:00Z"),
])
def test_contract_domain_refuses_unsupported_values(rows, field, value):
    row = {**rows["contracts"][0], field: value}
    with pytest.raises(ValueError):
        CashIndexContract(row)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1_000", " 480", "", True, 480, 480.0])
def test_all_decimal_families_refuse_noncanonical_numbers(rows, value):
    for field, record in [
        ("strike", rows["contracts"][0]), ("bid", rows["quotes"][0]),
        ("ask", rows["quotes"][0]), ("value", rows["settlements"][0]),
    ]:
        previous = record[field]
        record[field] = value
        with pytest.raises(ValueError):
            position(rows)
        record[field] = previous
    with pytest.raises(ValueError):
        position(rows, fees_usd=value)


@pytest.mark.parametrize("field", list(CashIndexContract._FIELDS) + ["known_at", "provenance"])
def test_missing_contract_metadata_refuses(rows, field):
    del rows["contracts"][0][field]
    with pytest.raises(ValueError, match="missing"):
        position(rows)


@pytest.mark.parametrize("field, value", [
    ("underlying_id", "OTHER"), ("product", "OTHER"), ("expiry", "2026-03-20"),
    ("settlement_id", "OTHER"), ("multiplier", 10), ("reference_version", "ref-v2"),
    ("corpus_id", "another-corpus"), ("right", "call"), ("strike", "490"),
    ("contract_id", "p485"),
])
def test_mixed_leg_identity_and_order_refuse(rows, field, value):
    rows["contracts"][0][field] = value
    with pytest.raises(ValueError):
        position(rows)


@pytest.mark.parametrize("argument, value", [
    ("count", 0), ("count", -1), ("count", True), ("count", 1.0),
    ("fees_usd", "-0.01"), ("quantities", [True, -1, -1, 1]),
    ("quantities", [1, 1, -1, 1]), ("quantities", [1, -1, -1]),
])
def test_bad_count_quantities_and_fees_refuse(rows, argument, value):
    with pytest.raises(ValueError):
        position(rows, **{argument: value})


@pytest.mark.parametrize("field, value", [
    ("bid", "-0.1"), ("ask", "2.49"), ("bid_size", 0), ("ask_size", 0),
    ("bid_size", True), ("ask_size", 1.0), ("condition_valid", False),
    ("condition_valid", 1), ("reference_version", "ref-v2"),
    ("contract_id", "other"), ("corpus_id", "other"),
    ("quote_at", "2026-01-16T20:44:00Z"),
])
def test_bad_quotes_refuse(rows, field, value):
    rows["quotes"][0][field] = value
    with pytest.raises(ValueError):
        position(rows)


@pytest.mark.parametrize("quote_time", ["2026-01-16T20:46:00Z", "2026-02-21T00:00:00Z"])
def test_inconsistent_or_post_last_trade_quote_refuses(rows, quote_time):
    targets = rows["quotes"] if quote_time.startswith("2026-02") else rows["quotes"][:1]
    for row in targets:
        row["quote_at"] = row["effective_at"] = quote_time
    with pytest.raises(ValueError):
        position(rows)


@pytest.mark.parametrize("field, value", [
    ("settlement_id", "wrong"), ("expiry", "2026-03-20"),
    ("underlying_id", "wrong"), ("settlement_style", "am"),
    ("corpus_id", "wrong"), ("official_value_at", "2026-02-20T20:00:00Z"),
])
def test_bad_settlement_identity_refuses(rows, field, value):
    rows["settlements"][0][field] = value
    with pytest.raises(ValueError):
        position(rows)


def test_settlement_instant_must_match_reference_even_when_own_clocks_agree(rows):
    row = rows["settlements"][0]
    row["effective_at"] = row["official_value_at"] = "2026-02-20T22:00:00Z"
    with pytest.raises(ValueError, match="settlement instant"):
        position(rows)


@pytest.mark.parametrize("short_bid", ["1.60", "6.60", "7.60"])
def test_nonpositive_or_wing_width_credit_refuses(rows, short_bid):
    rows["quotes"][1].update(bid=short_bid, ask=short_bid)
    with pytest.raises(ValueError, match="credit"):
        position(rows)


def test_qualified_offsets_and_snapshot_immutability(rows):
    rows["quotes"][0]["quote_at"] = rows["quotes"][0]["effective_at"] = "2026-01-16T15:45:00-05:00"
    condor = position(rows)
    rows["quotes"][0]["ask"] = "9999"
    rows["contracts"][0]["strike"] = "9999"
    rows["settlements"][0]["value"] = "9999"
    assert condor.evaluate()["net_pnl_usd"] == "252"
    with pytest.raises(TypeError):
        condor.contracts[0].data["strike"] = "1"


def test_decimal_precision_does_not_silently_round(rows):
    rows["quotes"][0]["ask"] = "2.600000000000000000000000000000000001"
    with localcontext() as context:
        context.prec = 3
        report = position(rows, fees_usd="0").evaluate()
    assert report["net_pnl_usd"] == "259.9999999999999999999999999999999999"

@pytest.mark.parametrize("leg", range(4))
@pytest.mark.parametrize("field", ["bid_size", "ask_size"])
def test_each_leg_size_covers_requested_count(rows, leg, field):
    rows["quotes"][leg][field] = 1
    with pytest.raises(ValueError, match="sizes"):
        position(rows, count=2)
    rows["quotes"][leg][field] = 2
    assert position(rows, count=2).evaluate()["net_pnl_usd"] == "512"


@pytest.mark.parametrize("stream, effective", [
    ("quotes", "2026-01-15T20:45:00Z"), ("settlements", "2026-02-19T21:00:00Z"),
])
def test_own_effective_instant_mismatch_is_independently_rejected(rows, stream, effective):
    rows[stream][0]["effective_at"] = effective
    with pytest.raises(ValueError, match="must equal effective_at"):
        position(rows)


def test_settlement_equivalent_offset_is_valid(rows):
    rows["settlements"][0]["effective_at"] = "2026-02-20T16:00:00-05:00"
    assert position(rows).evaluate()["net_pnl_usd"] == "252"


# -- ADR-0187: the quote, credit and American-exercise rules as module functions --------

import math  # noqa: E402

from index_options.contracts import (  # noqa: E402
    CONDOR_LEGS,
    american_short_charge,
    condor_credit,
    quote_problems,
)


def test_condor_legs_are_the_one_owner_of_leg_order_and_sign():
    assert CONDOR_LEGS == (("put", 1), ("put", -1), ("call", -1), ("call", 1))
    from index_options.distribution import _LEGS

    assert _LEGS is CONDOR_LEGS


@pytest.mark.parametrize("bid, ask, bid_size, ask_size, count, side, expected", [
    (1.0, 1.2, 1, 1, 1, None, []),
    (0.0, 0.5, 0, 3, 1, "buy", []),        # a no-bid far strike is still BUYABLE
    (0.0, 0.5, 0, 3, 1, "sell", ["bid", "bid_size"]),
    (0.4, 0.0, 3, 0, 1, "buy", ["uncrossed", "ask", "ask_size"]),
    (0.4, 0.6, 3, 0, 1, "sell", []),       # a zero ask size does not stop a sale
    (0.4, 0.6, 3, 0, 1, None, ["ask_size"]),
    (0.4, 0.6, 1, 1, 2, None, ["bid_size", "ask_size"]),
    (0.4, 0.6, 1, 2, 2, "buy", []),
    (-0.1, 0.6, 3, 3, 1, None, ["nonnegative"]),
    (0.7, 0.6, 3, 3, 1, None, ["uncrossed"]),
    (float("nan"), 0.6, 3, 3, 1, None, ["bid"]),
    (0.4, None, 3, 3, 1, None, ["ask"]),
    (0.4, 0.6, True, 3, 1, None, ["bid_size"]),
])
def test_quote_problems_by_side(bid, ask, bid_size, ask_size, count, side, expected):
    problems = quote_problems(bid, ask, bid_size, ask_size, count, side=side)
    assert len(problems) == len(expected), problems
    for word, problem in zip(expected, problems):
        assert word in problem


def test_quote_problems_refuses_an_unknown_side_or_count():
    with pytest.raises(ValueError, match="side"):
        quote_problems(1.0, 1.2, 1, 1, 1, side="hold")
    with pytest.raises(ValueError, match="count"):
        quote_problems(1.0, 1.2, 1, 1, -1)
    with pytest.raises(ValueError, match="count"):
        quote_problems(1.0, 1.2, 1, 1, True)
    # count 0 is the row-level rule: a valid quote, no trade to cover
    assert quote_problems(0.0, 0.0, 0, 0, 0) == []


def test_condor_credit_is_the_per_share_credit_and_both_widths_without_raising():
    strikes = (470.0, 480.0, 515.0, 525.0)
    quotes = ((1.0, 1.1), (2.5, 2.7), (1.8, 1.9), (0.7, 0.8))  # (bid, ask) in leg order
    credit, widths = condor_credit(strikes, quotes)
    # short legs at the bid, long legs at the ask: 2.5 + 1.8 - 1.1 - 0.8
    assert credit == pytest.approx(2.4) and widths == (10.0, 10.0)
    from decimal import Decimal as D

    exact = condor_credit((D("470"), D("480"), D("515"), D("530")),
                          ((D("1.00"), D("1.10")), (D("2.50"), D("2.70")),
                           (D("1.80"), D("1.90")), (D("0.70"), D("0.80"))))
    assert exact == (D("2.40"), (D("10"), D("15")))
    # a worthless or inverted package is reported, never raised
    negative, _ = condor_credit(strikes, ((3.0, 3.1), (0.5, 0.6), (0.5, 0.6), (3.0, 3.1)))
    assert negative == pytest.approx(-5.2)


def _series(pairs, dividends=()):
    """Rows of (date, close) with the dividend on its ex-date, else 0."""
    paid = dict(dividends)
    return [{"date": d, "close": c, "dividend_amount": paid.get(d, 0.0)} for d, c in pairs]


WINDOW = [("2024-03-01", 100.0), ("2024-03-04", 101.0), ("2024-03-05", 99.0),
          ("2024-03-06", 103.0), ("2024-03-07", 104.0), ("2024-03-08", 105.0)]
CHARGE = dict(short_put=95.0, short_call=102.0, entry_date="2024-03-01",
              settle_date="2024-03-08", carry_rate=0.055, multiplier=100)


def test_call_charge_is_the_dividend_when_the_pre_ex_close_is_above_the_short_call():
    itm = american_short_charge(_series(WINDOW, [("2024-03-07", 1.5)]), **CHARGE)
    assert itm == {"call_dividend_usd": 150.0, "put_carry_usd": 0.0, "total_usd": 150.0}
    otm = american_short_charge(_series(WINDOW, [("2024-03-05", 1.5)]), **CHARGE)  # pre-ex 101 < 102
    assert otm["call_dividend_usd"] == 0.0 and otm["total_usd"] == 0.0


def test_ex_dates_on_the_entry_date_or_after_expiry_are_never_charged():
    on_entry = american_short_charge(_series(WINDOW, [("2024-03-01", 1.5)]), **CHARGE)
    assert on_entry["call_dividend_usd"] == 0.0
    beyond = _series(WINDOW + [("2024-03-11", 106.0)], [("2024-03-11", 1.5)])
    assert american_short_charge(beyond, **CHARGE)["call_dividend_usd"] == 0.0


def test_every_ex_date_after_the_first_qualifying_one_is_charged():
    rows = _series(WINDOW, [("2024-03-05", 0.5), ("2024-03-07", 1.0), ("2024-03-08", 0.25)])
    # 03-05: pre-ex close 101 < 102, not charged; 03-07: pre-ex 103 > 102, charged;
    # 03-08: pre-ex 104 > 102, charged on its own account
    charged = american_short_charge(rows, **CHARGE)
    assert charged["call_dividend_usd"] == pytest.approx(125.0)
    # once assigned, a later ex-date is charged EVEN IF its own pre-ex close is below the call
    assigned = _series([("2024-03-01", 100.0), ("2024-03-04", 101.0), ("2024-03-05", 99.0),
                        ("2024-03-06", 103.0), ("2024-03-07", 98.0), ("2024-03-08", 105.0)],
                       [("2024-03-07", 1.0), ("2024-03-08", 0.25)])
    assert american_short_charge(assigned, **CHARGE)["call_dividend_usd"] == pytest.approx(125.0)
    # without the earlier assignment, that same ex-date (pre-ex 98 < 102) is not charged
    alone = _series([("2024-03-01", 100.0), ("2024-03-04", 101.0), ("2024-03-05", 99.0),
                     ("2024-03-06", 103.0), ("2024-03-07", 98.0), ("2024-03-08", 105.0)],
                    [("2024-03-08", 0.25)])
    assert american_short_charge(alone, **CHARGE)["call_dividend_usd"] == 0.0
    # two ex-dates that are BOTH out of the money never latch the assignment
    otm_twice = _series([("2024-03-01", 100.0), ("2024-03-04", 101.0), ("2024-03-05", 99.0),
                         ("2024-03-06", 100.0), ("2024-03-07", 98.0), ("2024-03-08", 105.0)],
                        [("2024-03-05", 1.0), ("2024-03-07", 0.25)])
    assert american_short_charge(otm_twice, **CHARGE)["call_dividend_usd"] == 0.0


def test_put_carry_runs_from_the_first_in_the_money_close_to_settlement():
    rows = _series([("2024-03-01", 100.0), ("2024-03-04", 94.0), ("2024-03-05", 93.0),
                    ("2024-03-06", 96.0), ("2024-03-07", 97.0), ("2024-03-08", 98.0)])
    charged = american_short_charge(rows, **CHARGE)
    tau = 4 / 365  # 03-04 -> 03-08
    assert charged["put_carry_usd"] == pytest.approx(95.0 * (math.exp(0.055 * tau) - 1) * 100)
    assert charged["total_usd"] == pytest.approx(charged["put_carry_usd"])
    entry_itm = american_short_charge(_series([("2024-03-01", 90.0), ("2024-03-08", 98.0)]),
                                      **CHARGE)
    assert entry_itm["put_carry_usd"] == pytest.approx(95.0 * (math.exp(0.055 * 7 / 365) - 1) * 100)
    assert american_short_charge(rows, **dict(CHARGE, carry_rate=0.0))["put_carry_usd"] == 0.0
    otm = american_short_charge(_series(WINDOW), **CHARGE)
    assert otm["put_carry_usd"] == 0.0


def test_a_missing_dividend_in_the_window_refuses_and_the_window_is_bounded():
    rows = _series(WINDOW)
    rows[3]["dividend_amount"] = None
    with pytest.raises(ValueError, match="dividend_amount"):
        american_short_charge(rows, **CHARGE)
    # a None outside (entry, settle_date] is never read
    outside = _series(WINDOW + [("2024-03-11", 106.0)])
    outside[-1]["dividend_amount"] = None
    assert american_short_charge(outside, **CHARGE)["total_usd"] == 0.0
    with pytest.raises(ValueError, match="carry_rate"):
        american_short_charge(_series(WINDOW), **dict(CHARGE, carry_rate=-0.01))
    with pytest.raises(ValueError, match="settle_date"):
        american_short_charge(_series(WINDOW), **dict(CHARGE, settle_date="2024-02-28"))


def test_a_pre_ex_close_exactly_at_the_short_call_is_not_assigned():
    at = _series([("2024-03-01", 100.0), ("2024-03-06", 102.0), ("2024-03-07", 104.0),
                  ("2024-03-08", 105.0)], [("2024-03-07", 1.5)])
    assert american_short_charge(at, **CHARGE)["call_dividend_usd"] == 0.0
    above = _series([("2024-03-01", 100.0), ("2024-03-06", 102.01), ("2024-03-07", 104.0),
                     ("2024-03-08", 105.0)], [("2024-03-07", 1.5)])
    assert american_short_charge(above, **CHARGE)["call_dividend_usd"] == 150.0


def test_a_close_exactly_at_the_short_put_carries_nothing():
    at = _series([("2024-03-01", 100.0), ("2024-03-04", 95.0), ("2024-03-05", 96.0),
                  ("2024-03-06", 96.0), ("2024-03-07", 97.0), ("2024-03-08", 98.0)])
    assert american_short_charge(at, **CHARGE)["put_carry_usd"] == 0.0
    below = _series([("2024-03-01", 100.0), ("2024-03-04", 95.0), ("2024-03-05", 94.99),
                     ("2024-03-06", 96.0), ("2024-03-07", 97.0), ("2024-03-08", 98.0)])
    tau = 3 / 365  # from 03-05, not 03-04
    assert american_short_charge(below, **CHARGE)["put_carry_usd"] == pytest.approx(
        95.0 * (math.exp(0.055 * tau) - 1) * 100)


def test_the_charge_reads_rows_in_any_order_and_never_the_entry_days_dividend():
    # the pre-ex close (03-06, 103) is above the call but the ex-date's own close (101) is
    # not, so reading the rows backwards would find no assignment
    itm = _series([("2024-03-01", 100.0), ("2024-03-04", 101.0), ("2024-03-05", 99.0),
                   ("2024-03-06", 103.0), ("2024-03-07", 101.0), ("2024-03-08", 101.0)],
                  [("2024-03-07", 1.5)])
    assert american_short_charge(list(reversed(itm)), **CHARGE) == \
        american_short_charge(itm, **CHARGE) == {"call_dividend_usd": 150.0,
                                                 "put_carry_usd": 0.0, "total_usd": 150.0}
    on_entry = _series(WINDOW)
    on_entry[0]["dividend_amount"] = None  # the entry date's own ex-date is never read
    assert american_short_charge(on_entry, **CHARGE)["total_usd"] == 0.0


def test_the_entry_close_is_the_pre_ex_close_of_an_ex_date_on_the_next_session():
    # the ex-date is the first session after entry, so its pre-ex close is the ENTRY close
    # (103 > the 102 short call): charged 1.5 * 100
    rows = _series([("2024-03-01", 103.0), ("2024-03-04", 101.0), ("2024-03-05", 101.0),
                    ("2024-03-06", 101.0), ("2024-03-07", 101.0), ("2024-03-08", 101.0)],
                   [("2024-03-04", 1.5)])
    assert american_short_charge(rows, **CHARGE)["call_dividend_usd"] == 150.0
