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


def test_the_reported_max_loss_is_the_one_owners_not_a_restated_rule(rows, monkeypatch):
    # ADR-0196 B-F2 made structure_max_loss the ONE maximum-loss rule; the exact-Decimal cashflow
    # report must CALL it (a stub's answer comes through) on the condor's legs, the strikes in leg
    # order, the entry cash in USD and the whole position's shares, never restate the widest wing
    calls = []

    def stub(legs, strikes, credit_usd, multiplier):
        calls.append((tuple(legs), tuple(strikes), credit_usd, multiplier))
        return Decimal("12345")

    monkeypatch.setattr("index_options.contracts.structure_max_loss", stub)
    report = position(rows, count=2, fees_usd="8").evaluate()
    assert report["max_loss_before_fees_usd"] == "12345"
    assert report["max_loss_after_fees_usd"] == "12353"
    assert calls == [(contracts.CONDOR_LEGS,
                      tuple(Decimal(c["strike"]) for c in rows["contracts"]),
                      Decimal(report["entry_cashflow_usd"]), 2 * rows["contracts"][0]["multiplier"])]
    assert isinstance(calls[0][2], Decimal)


@pytest.mark.parametrize("call_strike, count", [("525", 1), ("525", 3), ("527.25", 2), ("530.5", 1)])
def test_the_reported_max_loss_is_exactly_the_widest_wing_less_the_credit_in_decimals(
    rows, call_strike, count
):
    # the exact arithmetic survives the move to the owner: scale x (widest width - credit), to the
    # last digit, on an unequal wing and on a strike that is not whole
    rows["contracts"][3]["strike"] = call_strike
    report = position(rows, count=count, fees_usd="8").evaluate()
    strikes = [Decimal(c["strike"]) for c in rows["contracts"]]
    widest = max(strikes[1] - strikes[0], strikes[3] - strikes[2])
    scale = count * rows["contracts"][0]["multiplier"]
    want = scale * (widest - Decimal(report["entry_credit_points"]))
    assert Decimal(report["max_loss_before_fees_usd"]) == want
    assert Decimal(report["max_loss_after_fees_usd"]) == want + 8


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
    (0.4, -0.1, 3, 3, 1, None, ["nonnegative"]),   # a negative ASK alone, not "uncrossed"
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


def test_closes_before_the_entry_never_start_the_carry():
    early = _series([("2024-02-28", 90.0), ("2024-02-29", 90.0)] + WINDOW)
    assert american_short_charge(early, **CHARGE)["put_carry_usd"] == 0.0


def test_the_charge_validates_its_multiplier_dates_and_strikes():
    rows = _series(WINDOW)
    with pytest.raises(ValueError, match="multiplier"):
        american_short_charge(rows, **dict(CHARGE, multiplier=100.0))
    with pytest.raises(ValueError, match="multiplier"):
        american_short_charge(rows, **dict(CHARGE, multiplier=0))
    assert american_short_charge(rows, **dict(CHARGE, multiplier=1))["total_usd"] == 0.0
    with pytest.raises(ValueError, match="settle_date"):
        american_short_charge(rows, **dict(CHARGE, settle_date="2024-03-01"))  # == entry
    with pytest.raises(ValueError, match="settle_date must be an ISO date"):
        american_short_charge(rows, **dict(CHARGE, settle_date="2024/03/08"))
    with pytest.raises(ValueError, match="short_put"):
        american_short_charge(rows, **dict(CHARGE, short_put=0))
    with pytest.raises(ValueError, match="short_call"):
        american_short_charge(rows, **dict(CHARGE, short_call=-1.0))
    bad = _series(WINDOW)
    bad[2]["date"] = "2024/03/05"
    with pytest.raises(ValueError, match="^date must be an ISO date"):
        american_short_charge(bad, **CHARGE)
    flat = _series(WINDOW)
    flat[2]["close"] = 0.0
    with pytest.raises(ValueError, match="close on 2024-03-05 must be a positive number, got 0.0"):
        american_short_charge(flat, **CHARGE)
    with pytest.raises(ValueError, match="dividend"):
        american_short_charge(_series(WINDOW, [("2024-03-05", -0.5)]), **CHARGE)
    with pytest.raises(ValueError, match="no closes between 2024-03-01 and 2024-03-08"):
        american_short_charge([], **CHARGE)


def test_the_entry_close_is_the_pre_ex_close_of_an_ex_date_on_the_next_session():
    # the ex-date is the first session after entry, so its pre-ex close is the ENTRY close
    # (103 > the 102 short call): charged 1.5 * 100
    rows = _series([("2024-03-01", 103.0), ("2024-03-04", 101.0), ("2024-03-05", 101.0),
                    ("2024-03-06", 101.0), ("2024-03-07", 101.0), ("2024-03-08", 101.0)],
                   [("2024-03-04", 1.5)])
    assert american_short_charge(rows, **CHARGE)["call_dividend_usd"] == 150.0


# -- ADR-0193: leg-set owners, the put-spread legs and the split American charge ----------

import random  # noqa: E402
from datetime import date, timedelta  # noqa: E402

from index_options import contracts  # noqa: E402


def _condor_credit_as_shipped(strikes, quotes):
    """ADR-0187's ``condor_credit``, restated by hand: the pin for the leg-set refactor."""
    signs = (1, -1, -1, 1)
    credit = sum(-s * (ask if s > 0 else bid) for s, (bid, ask) in zip(signs, quotes))
    return credit, (strikes[1] - strikes[0], strikes[3] - strikes[2])


def test_the_put_spread_legs_are_the_condors_first_two_and_exported():
    assert contracts.PUT_SPREAD_LEGS == (("put", 1), ("put", -1)) == CONDOR_LEGS[:2]
    assert "PUT_SPREAD_LEGS" in contracts.__all__ and "structure_credit" in contracts.__all__
    assert {"american_put_carry", "american_call_dividend", "dividends_paid"} <= set(
        contracts.__all__)


def test_structure_credit_over_the_condor_legs_is_the_shipped_condor_credit_exactly():
    rng = random.Random(193)
    for _ in range(300):  # unsorted strikes and crossed quotes too: neither ever raises
        strikes = tuple(rng.uniform(50.0, 500.0) for _ in range(4))
        quotes = tuple((rng.uniform(0.0, 9.0), rng.uniform(0.0, 9.0)) for _ in range(4))
        shipped = _condor_credit_as_shipped(strikes, quotes)
        assert condor_credit(strikes, quotes) == shipped
        assert contracts.structure_credit(CONDOR_LEGS, strikes, quotes) == shipped
    for _ in range(100):  # exact Decimals keep their type and their digits
        strikes = tuple(Decimal(rng.randrange(400, 5000)) / 10 for _ in range(4))
        quotes = tuple((Decimal(rng.randrange(0, 900)) / 100, Decimal(rng.randrange(0, 900)) / 100)
                       for _ in range(4))
        result = contracts.structure_credit(CONDOR_LEGS, strikes, quotes)
        assert result == _condor_credit_as_shipped(strikes, quotes) == condor_credit(strikes, quotes)
        assert isinstance(result[0], Decimal) and all(isinstance(w, Decimal) for w in result[1])


def test_structure_credit_of_a_put_spread_is_its_own_credit_and_one_width():
    # short put at the bid (2.0), long put at the ask (1.1)
    credit, widths = contracts.structure_credit(
        contracts.PUT_SPREAD_LEGS, (95.0, 100.0), ((1.0, 1.1), (2.0, 2.2)))
    assert credit == pytest.approx(0.9) and widths == (5.0,)
    # it is the put side of the condor's credit: the two sides sum to the whole
    strikes = (92.0, 95.0, 106.0, 108.0)
    quotes = ((1.0, 1.1), (2.0, 2.2), (1.5, 1.6), (0.7, 0.8))
    put, put_widths = contracts.structure_credit(CONDOR_LEGS[:2], strikes[:2], quotes[:2])
    call, call_widths = contracts.structure_credit(CONDOR_LEGS[2:], strikes[2:], quotes[2:])
    assert put + call == pytest.approx(condor_credit(strikes, quotes)[0])
    assert (put_widths, call_widths) == ((3.0,), (2.0,))


@pytest.mark.parametrize("legs, strikes, widths", [
    # a vertical pair: consecutive legs of ONE right with opposite signs; the width is the
    # later leg's strike less the earlier leg's, in leg order
    ((("put", 1), ("put", -1)), (90.0, 100.0), (10.0,)),
    ((("call", -1), ("call", 1)), (100.0, 110.0), (10.0,)),
    (CONDOR_LEGS, (90.0, 100.0, 110.0, 125.0), (10.0, 15.0)),
    # a butterfly: only the two outer consecutive pairs are verticals
    ((("call", 1), ("call", -1), ("call", -1), ("call", 1)), (90.0, 100.0, 100.0, 110.0),
     (10.0, 10.0)),
    # no vertical: two rights, or two legs of one sign
    ((("put", -1), ("call", -1)), (95.0, 105.0), ()),
    # ... and two rights of OPPOSITE signs: the right clause alone keeps these apart (B-M2)
    ((("put", 1), ("call", -1)), (95.0, 105.0), ()),
    ((("call", -1), ("put", 1)), (105.0, 95.0), ()),
    ((("put", 1), ("put", 1)), (95.0, 100.0), ()),
    ((("call", -1),), (100.0,), ()),
])
def test_the_widths_are_those_of_consecutive_same_right_opposite_sign_pairs(
    legs, strikes, widths
):
    quotes = [(1.0, 1.1)] * len(legs)
    assert contracts.structure_credit(legs, strikes, quotes)[1] == widths


def test_structure_credit_refuses_a_strike_or_quote_list_that_does_not_match_its_legs():
    quotes = ((1.0, 1.1), (2.0, 2.2))
    legs = contracts.PUT_SPREAD_LEGS
    for strikes, pairs in (((95.0,), quotes), ((90.0, 95.0, 100.0), quotes),
                           ((95.0, 100.0), quotes[:1]),
                           ((95.0, 100.0), quotes * 2)):  # a condor's quotes on a put spread
        with pytest.raises(ValueError):
            contracts.structure_credit(legs, strikes, pairs)
    with pytest.raises(ValueError):  # and a condor's four strikes cannot ride a put spread
        contracts.condor_credit((90.0, 95.0), quotes)


def _random_series(rng, entry="2024-03-01", days=8):
    """Random closes and ex-dates over ``days`` sessions (ISO dates, one per calendar day)."""
    start = date.fromisoformat(entry)
    return [{"date": (start + timedelta(days=k)).isoformat(),
             "close": round(rng.uniform(88.0, 112.0), 2),
             "dividend_amount": round(rng.choice([0.0, 0.0, 0.5, 1.25]), 2)}
            for k in range(days)]


def _american_short_charge_as_shipped(rows, short_put, short_call, entry_date, settle_date,
                                      carry_rate, multiplier):
    """ADR-0187's ``american_short_charge`` restated in the test's own words (per-share math)."""
    entry, settle = date.fromisoformat(entry_date), date.fromisoformat(settle_date)
    window = sorted((r for r in rows if entry <= date.fromisoformat(r["date"]) <= settle),
                    key=lambda r: r["date"])
    call, assigned, previous = 0.0, False, None
    for row in window:
        if date.fromisoformat(row["date"]) > entry:
            if row["dividend_amount"] > 0 and (assigned or (previous is not None
                                                            and previous > short_call)):
                assigned = True
                call += row["dividend_amount"]
        previous = row["close"]
    put = 0.0
    for row in window:
        day = date.fromisoformat(row["date"])
        if day < settle and row["close"] < short_put:
            tau = (settle - day).days / 365
            put = short_put * (math.exp(carry_rate * tau) - 1)
            break
    return call * multiplier, put * multiplier


def test_the_split_charges_are_the_shipped_composite_pieces_on_random_series():
    rng = random.Random(1930)
    for _ in range(200):
        rows = _random_series(rng)
        short_put, short_call = rng.uniform(92.0, 100.0), rng.uniform(100.0, 108.0)
        rate, multiplier = rng.choice([0.0, 0.055]), rng.choice([1, 100])
        args = (rows, short_put, short_call, "2024-03-01", "2024-03-08", rate, multiplier)
        call, put = _american_short_charge_as_shipped(*args)
        assert contracts.american_call_dividend(
            rows, short_call, "2024-03-01", "2024-03-08", multiplier) == call
        assert contracts.american_put_carry(
            rows, short_put, "2024-03-01", "2024-03-08", rate, multiplier) == put
        composite = american_short_charge(*args)
        assert set(composite) == {"call_dividend_usd", "put_carry_usd", "total_usd"}
        assert composite["call_dividend_usd"] == call and composite["put_carry_usd"] == put
        assert composite["total_usd"] == pytest.approx(call + put, rel=1e-12, abs=1e-12)


def test_put_carry_needs_no_dividend_data_while_the_composite_and_the_call_charge_do():
    bare = [{"date": d, "close": c} for d, c in (
        ("2024-03-01", 100.0), ("2024-03-04", 94.0), ("2024-03-05", 93.0),
        ("2024-03-06", 96.0), ("2024-03-07", 97.0), ("2024-03-08", 98.0))]
    carry = contracts.american_put_carry(bare, 95.0, "2024-03-01", "2024-03-08", 0.055, 100)
    assert carry == pytest.approx(95.0 * (math.exp(0.055 * 4 / 365) - 1) * 100)
    for refuser in (
        lambda: american_short_charge(bare, **CHARGE),
        lambda: contracts.american_call_dividend(bare, 102.0, "2024-03-01", "2024-03-08", 100),
    ):
        with pytest.raises(ValueError, match="dividend_amount on 2024-03-04"):
            refuser()


def test_the_call_charge_takes_no_carry_rate_and_the_put_carry_no_short_call():
    import inspect

    assert list(inspect.signature(contracts.american_call_dividend).parameters) == [
        "rows", "short_call", "entry_date", "settle_date", "multiplier"]
    assert list(inspect.signature(contracts.american_put_carry).parameters) == [
        "rows", "short_put", "entry_date", "settle_date", "carry_rate", "multiplier"]
    itm = _series(WINDOW, [("2024-03-07", 1.5)])
    assert contracts.american_call_dividend(itm, 102.0, "2024-03-01", "2024-03-08", 100) == 150.0
    assert contracts.american_put_carry(itm, 95.0, "2024-03-01", "2024-03-08", 0.055, 100) == 0.0


#: One refused input per rule of the shared validation, each with the message it must carry.
BAD_CHARGE_INPUTS = [
    ({"settle_date": "2024-03-01"}, "settle_date 2024-03-01 must follow entry_date 2024-03-01"),
    ({"settle_date": "2024/03/08"}, "settle_date must be an ISO date"),
    ({"entry_date": "2024/03/01"}, "entry_date must be an ISO date"),
    ({"multiplier": 100.0}, "multiplier must use an integer"),
    ({"multiplier": 0}, "multiplier"),
    ({"short_put": 0}, "short_put must be a positive number, got 0"),
    ({"short_call": -1.0}, "short_call must be a positive number, got -1.0"),
    ({"carry_rate": -0.01}, "carry_rate must be a finite number >= 0, got -0.01"),
    ({"carry_rate": "0.055"}, "carry_rate must be a finite number >= 0, got '0.055'"),
]
#: Which knob each function under test owns; a bad input outside its knobs is not its business.
OWNS = {
    "american_put_carry": {"settle_date", "entry_date", "multiplier", "short_put", "carry_rate"},
    "american_call_dividend": {"settle_date", "entry_date", "multiplier", "short_call"},
    "american_short_charge": {"settle_date", "entry_date", "multiplier", "short_put",
                              "short_call", "carry_rate"},
}


def _charge(name, rows, **kwargs):
    """Call ``name`` with ``CHARGE`` restricted to the knobs it takes, then ``kwargs`` applied."""
    knobs = {k: v for k, v in dict(CHARGE, **kwargs).items() if k in OWNS[name]}
    return getattr(contracts, name)(rows, **knobs)


@pytest.mark.parametrize("name, change, message", [
    (name, change, message) for name in sorted(OWNS)
    for change, message in BAD_CHARGE_INPUTS if next(iter(change)) in OWNS[name]
])
def test_every_charge_function_refuses_a_bad_input_it_owns_with_the_same_message(
    name, change, message
):
    with pytest.raises(ValueError, match=message):
        _charge(name, _series(WINDOW), **change)


@pytest.mark.parametrize("name", sorted(OWNS))
def test_every_charge_function_refuses_a_bad_window_the_same_way(name):
    bad_date = _series(WINDOW)
    bad_date[2]["date"] = "2024/03/05"
    with pytest.raises(ValueError, match="^date must be an ISO date"):
        _charge(name, bad_date)
    flat = _series(WINDOW)
    flat[2]["close"] = 0.0
    with pytest.raises(ValueError, match="close on 2024-03-05 must be a positive number, got 0.0"):
        _charge(name, flat)
    with pytest.raises(ValueError, match="no closes between 2024-03-01 and 2024-03-08"):
        _charge(name, [])


@pytest.mark.parametrize("name", ["american_call_dividend", "american_short_charge"])
def test_the_dividend_readers_refuse_an_unusable_ex_date_amount_only_inside_the_window(name):
    for bad in (None, -0.5, float("nan"), "0.5", True):
        rows = _series(WINDOW)
        rows[3]["dividend_amount"] = bad
        with pytest.raises(ValueError, match="dividend_amount on 2024-03-06 must be a finite "
                                             "number >= 0"):
            _charge(name, rows)
    outside = _series(WINDOW + [("2024-03-11", 106.0)])
    outside[-1]["dividend_amount"] = None   # after settlement: never read
    outside[0]["dividend_amount"] = None    # the entry day's own ex-date: never read
    clean = _series(WINDOW + [("2024-03-11", 106.0)])
    assert _charge(name, outside) == _charge(name, clean)


def test_dividends_paid_sums_the_ex_dates_after_entry_up_to_and_including_settlement():
    rows = _series(WINDOW + [("2024-03-11", 106.0)],
                   [("2024-03-01", 9.0), ("2024-03-04", 0.5), ("2024-03-08", 0.25),
                    ("2024-03-11", 7.0)])
    # the entry day's (9.0) and the day after settlement's (7.0) are outside (entry, settle]
    assert contracts.dividends_paid(rows, "2024-03-01", "2024-03-08") == pytest.approx(0.75)
    assert contracts.dividends_paid(list(reversed(rows)), "2024-03-01", "2024-03-08") == \
        pytest.approx(0.75)
    assert contracts.dividends_paid(_series(WINDOW), "2024-03-01", "2024-03-08") == 0.0
    # the same refusals as the charge: window, unusable amount, a settle date that is not later
    bad = _series(WINDOW)
    bad[4]["dividend_amount"] = None
    with pytest.raises(ValueError, match="dividend_amount on 2024-03-07"):
        contracts.dividends_paid(bad, "2024-03-01", "2024-03-08")
    with pytest.raises(ValueError, match="settle_date 2024-03-01 must follow"):
        contracts.dividends_paid(_series(WINDOW), "2024-03-01", "2024-03-01")
    with pytest.raises(ValueError, match="no closes between"):
        contracts.dividends_paid([], "2024-03-01", "2024-03-08")


def test_dividends_paid_excludes_the_day_after_settlement_and_never_reads_an_unused_none():
    # ADR-0193 review B-M4. 2024-03-08 is a Friday; an ex-date on the very next day (a
    # Saturday here, a Thursday for a Wednesday expiry) is one calendar day past the window.
    rows = _series(WINDOW + [("2024-03-09", 106.0)], [("2024-03-09", 3.0), ("2024-03-08", 0.25)])
    assert contracts.dividends_paid(rows, "2024-03-01", "2024-03-08") == pytest.approx(0.25)
    # the entry day's amount is never read, so a missing one is no refusal (it is not paid
    # to a holder from that close on); nor is a missing one after settlement
    unread = _series(WINDOW + [("2024-03-09", 106.0)], [("2024-03-04", 0.5)])
    unread[0]["dividend_amount"] = None
    unread[-1]["dividend_amount"] = None
    assert contracts.dividends_paid(unread, "2024-03-01", "2024-03-08") == pytest.approx(0.5)
    # ... while a None on the last session inside the window still refuses
    last = _series(WINDOW + [("2024-03-09", 106.0)])
    last[-2]["dividend_amount"] = None
    with pytest.raises(ValueError, match="dividend_amount on 2024-03-08"):
        contracts.dividends_paid(last, "2024-03-01", "2024-03-08")


def test_the_composite_return_contract_is_unchanged():
    rows = _series(WINDOW, [("2024-03-07", 1.5)])
    rows[4]["close"] = 94.0  # the put goes in the money on 03-07: one day of carry
    charged = american_short_charge(rows, **CHARGE)
    assert charged["put_carry_usd"] == pytest.approx(95.0 * (math.exp(0.055 / 365) - 1) * 100)
    assert list(charged) == ["call_dividend_usd", "put_carry_usd", "total_usd"]
    assert charged["call_dividend_usd"] == contracts.american_call_dividend(
        rows, 102.0, "2024-03-01", "2024-03-08", 100) == 150.0
    assert charged["total_usd"] == pytest.approx(150.0 + charged["put_carry_usd"])


# -- ADR-0195: the call spread and the structure table -----------------------------------------


def test_the_call_spread_legs_and_the_structure_table_are_single_exported_owners():
    assert contracts.CALL_SPREAD_LEGS == (("call", -1), ("call", 1)) == CONDOR_LEGS[2:]
    # the table the payoff selector chooses among, in the order a tie is broken by default
    assert dict(contracts.STRUCTURES) == {"put_spread": contracts.PUT_SPREAD_LEGS,
                                          "call_spread": contracts.CALL_SPREAD_LEGS,
                                          "condor": CONDOR_LEGS}
    assert list(contracts.STRUCTURES) == ["put_spread", "call_spread", "condor"]
    assert {"CALL_SPREAD_LEGS", "STRUCTURES"} <= set(contracts.__all__)
    with pytest.raises(TypeError):        # a shared table nobody may edit in place
        contracts.STRUCTURES["butterfly"] = CONDOR_LEGS
    # every structure's legs are a run of the condor's, so they share one leg order and sign rule
    for legs in contracts.STRUCTURES.values():
        assert all(leg in CONDOR_LEGS for leg in legs)


def test_structure_credit_of_a_call_spread_is_its_own_credit_and_one_width():
    # short call at the bid (1.5), long call at the ask (0.8): the condor's call side
    credit, widths = contracts.structure_credit(
        contracts.CALL_SPREAD_LEGS, (106.0, 108.0), ((1.5, 1.6), (0.7, 0.8)))
    assert credit == pytest.approx(0.7) and widths == (2.0,)


# -- ADR-0197: the debit leg sets and the one owner of a structure's maximum loss --------------

LONG_STRADDLE = (("put", 1), ("call", 1))
LONG_CALL_SPREAD = (("call", 1), ("call", -1))
LONG_PUT_SPREAD = (("put", -1), ("put", 1))
DEBIT_LEGS = {"long_straddle": LONG_STRADDLE, "long_call_spread": LONG_CALL_SPREAD,
              "long_put_spread": LONG_PUT_SPREAD}


def test_the_debit_leg_sets_are_single_exported_owners_in_ascending_strike_order():
    # restated here, never read back: buying what the credit structures sell. A spread lists its
    # strikes low to high, like CONDOR_LEGS: the long call under the short call, the SHORT put
    # under the long put (a long put spread's bought put is its HIGHER strike)
    assert contracts.LONG_STRADDLE_LEGS == LONG_STRADDLE
    assert contracts.LONG_CALL_SPREAD_LEGS == LONG_CALL_SPREAD
    assert contracts.LONG_PUT_SPREAD_LEGS == LONG_PUT_SPREAD
    assert {"LONG_STRADDLE_LEGS", "LONG_CALL_SPREAD_LEGS", "LONG_PUT_SPREAD_LEGS",
            "structure_max_loss", "structure_payoff"} <= set(contracts.__all__)
    # they are not selector candidates: the selector chooses among CREDIT structures only
    assert not set(DEBIT_LEGS.values()) & set(contracts.STRUCTURES.values())


def test_structure_credit_of_the_debit_structures_is_a_negative_credit_and_the_right_widths():
    # long legs at the ask, short legs at the bid; the spreads' width is the later strike less the
    # earlier, positive because the strikes ascend; a straddle's two rights pair with nothing
    credit, widths = contracts.structure_credit(LONG_CALL_SPREAD, (100.0, 105.0),
                                                ((2.0, 2.2), (0.9, 1.0)))
    assert credit == pytest.approx(-2.2 + 0.9) and widths == (5.0,)
    credit, widths = contracts.structure_credit(LONG_PUT_SPREAD, (95.0, 100.0),
                                                ((1.0, 1.1), (2.0, 2.2)))
    assert credit == pytest.approx(1.0 - 2.2) and widths == (5.0,)
    credit, widths = contracts.structure_credit(LONG_STRADDLE, (100.0, 100.0),
                                                ((1.9, 2.0), (2.3, 2.4)))
    assert credit == pytest.approx(-2.0 - 2.4) and widths == ()


@pytest.mark.parametrize("legs, strikes, level, want", [
    # long straddle at 100: worth the distance from the strike either way, nothing at it
    (LONG_STRADDLE, (100.0, 100.0), 95.0, 5.0), (LONG_STRADDLE, (100.0, 100.0), 108.0, 8.0),
    (LONG_STRADDLE, (100.0, 100.0), 100.0, 0.0),
    # long call spread 100 / 105: 0 below, the gain between, the width above
    (LONG_CALL_SPREAD, (100.0, 105.0), 98.0, 0.0), (LONG_CALL_SPREAD, (100.0, 105.0), 103.0, 3.0),
    (LONG_CALL_SPREAD, (100.0, 105.0), 110.0, 5.0),
    # long put spread, short 95 under long 100: 0 above, the gain between, the width below
    (LONG_PUT_SPREAD, (95.0, 100.0), 102.0, 0.0), (LONG_PUT_SPREAD, (95.0, 100.0), 97.0, 3.0),
    (LONG_PUT_SPREAD, (95.0, 100.0), 90.0, 5.0),
])
def test_structure_payoff_of_the_debit_structures_by_hand(legs, strikes, level, want):
    assert contracts.structure_payoff(legs, level, strikes) == want


def test_structure_payoff_has_one_owner_contracts_and_distribution_re_exports_it():
    from index_options import distribution

    assert distribution.structure_payoff is contracts.structure_payoff
    assert "structure_payoff" in distribution.__all__


@pytest.mark.parametrize("name, strikes, credit, multiplier, want", [
    # the debit is what a long structure can lose: fees included, nothing else
    ("long_straddle", (100.0, 100.0), -(4.4 * 100 + 4 * 0.65), 100, 4.4 * 100 + 4 * 0.65),
    ("long_call_spread", (100.0, 105.0), -130.0 - 1.3, 100, 131.3),
    ("long_put_spread", (95.0, 100.0), -120.0 - 1.3, 100, 121.3),
    ("long_call_spread", (100.0, 105.0), -13.0, 10, 13.0),
])
def test_structure_max_loss_of_a_long_structure_is_its_debit(name, strikes, credit, multiplier,
                                                              want):
    got = contracts.structure_max_loss(DEBIT_LEGS[name], strikes, credit, multiplier)
    assert got == pytest.approx(want, rel=1e-12)


@pytest.mark.parametrize("legs, strikes, credit, multiplier, want", [
    (CONDOR_LEGS, (90.0, 95.0, 105.0, 115.0), 150.0, 100, 850.0),       # the wider wing, 10
    (CONDOR_LEGS, (85.0, 95.0, 105.0, 110.0), 300.0, 100, 700.0),       # the wider wing is the PUT
    (contracts.PUT_SPREAD_LEGS, (92.0, 95.0), 60.0, 100, 240.0),
    (contracts.CALL_SPREAD_LEGS, (106.0, 109.0), 50.0, 100, 250.0),
    (CONDOR_LEGS, (90.0, 95.0, 105.0, 115.0), 15.0, 10, 85.0),
])
def test_structure_max_loss_of_a_credit_structure_by_hand(legs, strikes, credit, multiplier, want):
    assert contracts.structure_max_loss(legs, strikes, credit, multiplier) == want


def test_structure_max_loss_is_the_multiplier_times_the_widest_width_less_the_credit_exactly():
    # the rule the selector and the ledger study each restated until ADR-0197: pinned over random
    # condors, put spreads and call spreads, credits and multipliers, to the bit
    rng = random.Random(197)
    for _ in range(400):
        ks = sorted(rng.sample(range(40, 400), 4))
        strikes = tuple(k / 2 for k in ks)
        for legs, picked in ((CONDOR_LEGS, strikes), (contracts.PUT_SPREAD_LEGS, strikes[:2]),
                             (contracts.CALL_SPREAD_LEGS, strikes[2:])):
            credit, multiplier = rng.uniform(-50.0, 900.0), rng.choice([1, 10, 100])
            _, widths = contracts.structure_credit(legs, picked, [(0.0, 0.0)] * len(legs))
            assert contracts.structure_max_loss(legs, picked, credit, multiplier) == (
                multiplier * max(widths) - credit), (legs, picked, credit, multiplier)


def test_structure_max_loss_of_exact_decimals_stays_decimal():
    got = contracts.structure_max_loss(
        CONDOR_LEGS, (Decimal("90"), Decimal("95"), Decimal("105"), Decimal("115")),
        Decimal("150.35"), 100)
    assert got == Decimal("849.65") and isinstance(got, Decimal)


@pytest.mark.parametrize("legs, strikes", [
    ((("call", -1),), (100.0,)),                                  # a naked short call
    ((("put", -1), ("call", -1)), (95.0, 105.0)),                 # a short strangle
    ((("call", 1), ("call", -2)), (100.0, 105.0)),                # a call ratio: slope -1 on top
    ((("call", -1), ("call", 1), ("call", -1)), (95.0, 100.0, 105.0)),
])
def test_a_structure_whose_payoff_falls_without_bound_above_its_top_strike_has_infinite_loss(
    legs, strikes
):
    assert contracts.structure_max_loss(legs, strikes, 1000.0, 100) == math.inf


def test_structure_max_loss_finds_the_worst_kink_not_just_the_ends():
    # a short put alone loses most at zero, the strike per share
    assert contracts.structure_max_loss((("put", -1),), (100.0,), 250.0, 100) == 100 * 100.0 - 250.0
    # a put butterfly (long 90, short two 100s, long 110) pays 0 at 0, at 90 and from 110 up, and 10
    # at 100 (between 90 and 100 it pays L - 90): never negative, so it can lose only its debit
    fly = (("put", 1), ("put", -2), ("put", 1))
    assert contracts.structure_max_loss(fly, (90.0, 100.0, 110.0), -500.0, 100) == 500.0
    # the short butterfly is its negation: worst at the 100 body, 10 a share against the credit
    short_fly = (("put", -1), ("put", 2), ("put", -1))
    assert contracts.structure_max_loss(short_fly, (90.0, 100.0, 110.0), 200.0, 100) == 800.0


def test_structure_max_loss_finds_a_worst_point_at_the_lowest_strike():
    # a put backspread (short one 100 put, long two 90 puts) pays 2 x 90 - 100 = +80 a share at zero,
    # 0 from 100 up, and LESS at 90 than anywhere else: -(100 - 90) = -10 a share, the lowest strike
    # and neither end. 1000 at 100 shares (credit 0); the kink set must hold EVERY strike
    backspread = (("put", 2), ("put", -1))
    assert contracts.structure_max_loss(backspread, (90.0, 100.0), 0.0, 100) == 1000.0
    # the credit shifts every point equally
    assert contracts.structure_max_loss(backspread, (90.0, 100.0), 250.0, 100) == 750.0
    # the call backspread (short one 100 call, long two 110 calls) is worst at its HIGHEST strike
    call_backspread = (("call", -1), ("call", 2))
    assert contracts.structure_max_loss(call_backspread, (100.0, 110.0), 0.0, 100) == 1000.0


@pytest.mark.parametrize("args", [
    ((), (), 1.0, 100),                                                # no legs at all
    (LONG_STRADDLE, (100.0,), -1.0, 100),                              # strikes do not match legs
    (LONG_STRADDLE, (100.0, 100.0), -1.0, 0),                          # multiplier < 1
    (LONG_STRADDLE, (100.0, 100.0), -1.0, 1.5),
    (LONG_STRADDLE, (100.0, 100.0), -1.0, True),
    (LONG_STRADDLE, (100.0, 100.0), float("nan"), 100),                # credit not finite
    (LONG_STRADDLE, (100.0, 100.0), "1", 100),
    (LONG_STRADDLE, (0.0, 100.0), -1.0, 100),                          # a strike must be positive
    (LONG_STRADDLE, (100.0, -5.0), -1.0, 100),
    # the length check comes BEFORE the infinite-loss early return: a naked short call is unbounded
    # only when its strikes match its legs, and a mismatch is an error either way
    ((("call", -1),), (), 50.0, 100),
    ((("call", -1),), (100.0, 105.0), 1.0, 100),
    ((("call", -1), ("call", -1)), (100.0,), 1.0, 100),
])
def test_structure_max_loss_refuses_what_it_cannot_value(args):
    with pytest.raises(ValueError):
        contracts.structure_max_loss(*args)
