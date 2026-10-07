"""Kalshi's taker fee: the SHIPPED Kalshi mapping (the document's ``fee_types``) over dskit's venue-neutral mechanics.

The mechanic itself (a per-order ``rate * C * P * (1 - P)`` rounded up to a grid) is dskit's and tested there.
What is the child's, and pinned here, is that the mapping the document ships makes Kalshi's published
schedule: the base rate, the mechanic, the cent. Expected cents are worked by hand. The rounding rule (round
the ORDER's fee up to the next cent, after snapping binary-float dust) is the one pmquant documents in
``children/pmquant/pmquant/fees.py``; the research notes state no rounding rule.
"""

import math

import pytest
from dskit.pipeline.fee_mechanics import fee_model_from_spec
from synthetic import ScriptedKalshi, Store, shipped

from crypto_trading.fees import FeeColumns
from crypto_trading.kalshi_rows import FeeRows

#: The Kalshi mapping exactly as the shipped document declares it.
FEE_TYPES = shipped("run-features-15m.json")["pipeline"]["fees"]["params"]["fee_types"]
KALSHI_MODEL = fee_model_from_spec(FEE_TYPES["quadratic"]["model"])
RATE = FEE_TYPES["quadratic"]["base_rate"]


def test_the_shipped_mapping_is_kalshis_published_rate_on_a_cent_grid():
    assert RATE == 0.07 and set(FEE_TYPES) == {"quadratic"}


def test_one_contract_pays_a_whole_cent_floor():
    # 0.07 * 1 * 0.5 * 0.5 = 0.0175 dollars = 1.75 cents -> rounds UP to 2 cents
    assert KALSHI_MODEL.order_fee(1, 0.5, 0.07) == pytest.approx(0.02)


def test_float_dust_does_not_buy_an_extra_cent():
    # 0.07 * 100 * 0.25 * 100 is 175.00000000000003 in binary floating point: a naive
    # ceil bills 1.76. The exact amount is 1.75 dollars.
    assert math.ceil(0.07 * 100 * 0.5 * (1 - 0.5) * 100) == 176, "premise: a naive ceil overshoots"
    assert KALSHI_MODEL.order_fee(100, 0.5, 0.07) == pytest.approx(1.75)


def test_a_larger_order_rounds_the_total_not_each_contract():
    # 0.07 * 100 * 0.3 * 0.7 = 1.47 exactly; one order of 100 is 1.47, while 100 orders of one
    # contract would each be 0.07*0.21 = 0.0147 -> 0.02 and cost 2.00.
    fee = KALSHI_MODEL
    assert fee.order_fee(100, 0.3, 0.07) == pytest.approx(1.47)
    assert 100 * fee.order_fee(1, 0.3, 0.07) == pytest.approx(2.00)


def test_a_multiplier_scales_the_rate_and_the_ends_are_free():
    fee = KALSHI_MODEL
    assert fee.order_fee(100, 0.5, 0.035) == pytest.approx(0.88)  # 0.875 -> 0.88
    assert fee.order_fee(100, 0.0, 0.07) == 0.0
    assert fee.order_fee(100, 1.0, 0.07) == 0.0
    assert fee.order_fee(0, 0.5, 0.07) == 0.0


@pytest.mark.parametrize("contracts, price, rate", [
    (-1, 0.5, 0.07), (1.5, 0.5, 0.07), (True, 0.5, 0.07), (1, 1.5, 0.07), (1, -0.1, 0.07),
    (1, 0.5, -0.07), (1, 0.5, float("nan")), (1, None, 0.07), (1, 0.5, None)])
def test_a_fill_no_formula_may_price_is_refused(contracts, price, rate):
    with pytest.raises(ValueError):
        KALSHI_MODEL.order_fee(contracts, price, rate)


# -- the node ---------------------------------------------------------------------


def schedule(series="KXBTC15M", fee_type="quadratic", multiplier=1.0, retrieved="2026-10-06T00:00:00+00:00"):
    return {"series": series, "fee_type": fee_type, "fee_multiplier": multiplier,
            "retrieved": retrieved, "retrieved_ms": 1_791_244_800_000}


def row(series="KXBTC15M", bid=0.40, ask=0.44):
    return {"ticker": "T", "series": series, "yes_bid": bid, "yes_ask": ask}


def run(rows, schedules, contracts=100, fee_types=None):
    node = FeeColumns("fees", {"fee_types": FEE_TYPES if fee_types is None else fee_types, "contracts": contracts})
    return node.run(None, {"records": rows, "schedules": schedules})["records"]


def test_node_prices_a_buy_of_yes_at_the_ask_and_of_no_at_the_bid():
    out = run([row()], [schedule()])[0]
    # YES at 0.44: 0.07*100*0.44*0.56 = 1.7248 -> 1.73 dollars -> 0.0173 per contract
    assert out["fee_buy_yes"] == pytest.approx(0.0173)
    # NO costs 1 - 0.40 = 0.60: 0.07*100*0.6*0.4 = 1.68 (binary dust makes it 1.6800000000000002)
    assert out["fee_buy_no"] == pytest.approx(0.0168)
    assert out["fee_rate"] == pytest.approx(0.07)
    assert out["fee_status"] == "ok"
    assert out["fee_schedule_retrieved"] == "2026-10-06T00:00:00+00:00"


def test_rounding_depends_on_the_declared_contract_count():
    one = run([row()], [schedule()], contracts=1)[0]
    # one contract: 0.07*0.44*0.56 = 0.017248 -> 1.7248 cents -> 2 cents
    assert one["fee_buy_yes"] == pytest.approx(0.02)


def test_the_series_multiplier_scales_the_rate():
    out = run([row()], [schedule(multiplier=0.5)])[0]
    assert out["fee_rate"] == pytest.approx(0.035)
    # 0.035*100*0.44*0.56 = 0.8624 -> 0.87 dollars -> 0.0087
    assert out["fee_buy_yes"] == pytest.approx(0.0087)


def test_the_latest_schedule_per_series_wins_and_is_named():
    old = schedule(multiplier=2.0, retrieved="2026-09-01T00:00:00+00:00")
    old["retrieved_ms"] = 1_788_220_800_000
    out = run([row()], [old, schedule(multiplier=1.0)])[0]
    assert out["fee_rate"] == pytest.approx(0.07)
    assert out["fee_schedule_retrieved"] == "2026-10-06T00:00:00+00:00"


@pytest.mark.parametrize("rows, schedules, status", [
    ([row()], [], "no_schedule"),
    ([row()], [schedule(fee_type="quadratic_with_maker_fees")], "unsupported_fee_type"),
    ([row()], [schedule(multiplier=None)], "no_multiplier"),
    ([row(bid=None)], [schedule()], "no_quote"),
    ([row(ask=None)], [schedule()], "no_quote"),
])
def test_what_cannot_be_priced_is_marked_never_defaulted(rows, schedules, status):
    out = run(rows, schedules)[0]
    assert out["fee_status"] == status
    assert out["fee_buy_yes"] is None and out["fee_buy_no"] is None


def test_a_second_fee_type_is_one_more_config_entry_not_code():
    """A venue type with its own rate and a different grid prices through the SAME node, by config alone."""
    types = {**FEE_TYPES, "flat_grid": {"base_rate": 0.02, "model": {
        "mechanic": "probability_quadratic", "rounding": {"policy": "ceil_to_tick", "tick": 0.05}}}}
    out = run([row()], [schedule(fee_type="flat_grid")], fee_types=types)[0]
    # 0.02 * 100 * 0.44 * 0.56 = 0.4928 dollars -> next 5 cents = 0.50 -> 0.005 per contract
    assert out["fee_status"] == "ok" and out["fee_buy_yes"] == pytest.approx(0.005)


def test_node_default_deny_and_required_knobs():
    with pytest.raises(Exception, match="surprise"):
        FeeColumns("fees", {"fee_types": FEE_TYPES, "contracts": 1, "surprise": 1})
    with pytest.raises(Exception, match="contracts"):
        FeeColumns("fees", {"fee_types": FEE_TYPES})
    with pytest.raises(Exception, match="fee_types"):
        FeeColumns("fees", {"contracts": 1})
    with pytest.raises(Exception, match="contracts"):
        FeeColumns("fees", {"fee_types": FEE_TYPES, "contracts": 0})
    entry = FEE_TYPES["quadratic"]
    with pytest.raises(Exception, match="mechanic"):
        FeeColumns("fees", {"fee_types": {"quadratic": {**entry, "model": {**entry["model"], "mechanic": "mystery"}}},
                            "contracts": 1})
    with pytest.raises(Exception, match="base_rate"):
        FeeColumns("fees", {"fee_types": {"quadratic": {**entry, "base_rate": -0.07}}, "contracts": 1})
    with pytest.raises(Exception, match="exactly the keys"):
        FeeColumns("fees", {"fee_types": {"quadratic": {**entry, "notes": "a comment would move the hash"}},
                            "contracts": 1})


def test_the_fee_schedule_reader_projects_the_stream_the_pack_emits(tmp_path, monkeypatch):
    store = Store(tmp_path, monkeypatch)
    api = ScriptedKalshi(fees={s: ("quadratic", 1) for s in
                               ("KXBTC", "KXBTCD", "KXBTC15M", "KXETH", "KXETHD", "KXETH15M")})
    store.kalshi("kalshi-crypto", "source-kalshi-crypto.json", api, ["fee_schedules"])
    node = FeeRows("fee_schedules", {"root": store.path, "source": "kalshi-crypto"})
    rows = node.run(None, {})["records"]
    assert {r["series"] for r in rows} == {"KXBTC", "KXBTCD", "KXBTC15M", "KXETH", "KXETHD", "KXETH15M"}
    first = rows[0]
    assert first["fee_type"] == "quadratic" and first["fee_multiplier"] == 1.0
    assert isinstance(first["retrieved_ms"], int) and first["retrieved"].startswith("2026-10-06T00:30")
