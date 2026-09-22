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
