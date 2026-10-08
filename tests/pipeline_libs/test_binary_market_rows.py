"""Binary-market readers and the fee columns (ADR-0248), venue-neutral.

Ported from the first child that built them. Every input field name is a param, so each
projection test runs twice: once under a conventional field map and once under a map whose
every name is different (``RENAMED``). The two must give the same rows, which proves no
field literal survived in the readers. Two tests go through a real acquisition (the
``localfiles`` connector), so the scan seam reads what a store actually holds.
"""

import json
import math
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from dskit.onboarding import OnboardingRoot, run_acquisition
from dskit.pipeline import binary_decisions as bd
from dskit.pipeline.binary_decisions import DecisionRows, QuoteState
from dskit.pipeline.fee_mechanics import FeeModel, fee_model_from_spec
from dskit.pipeline.libs import binary_market_rows
from dskit.pipeline.libs.binary_market_rows import (
    BinaryMarketRows,
    FeeColumns,
    FeeScheduleRows,
    QuoteBarRows,
    SECONDS_CEILING,
    instant_ms,
)
from dskit.pipeline.libs.numpy import accessor_narrowing_problems

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def iso(moment):
    return moment.isoformat().replace("+00:00", "Z")


def ms(moment):
    return (moment - _EPOCH) // timedelta(milliseconds=1)


CLOSE = utc(2026, 9, 2, 0, 15)
OPEN = utc(2026, 9, 2, 0, 0)

# -- the field maps ------------------------------------------------------------------------

MARKET_FIELDS = {
    "ticker_field": "ticker", "event_field": "event_ticker", "series_field": "series_ticker",
    "status_field": "status", "result_field": "result", "strike_type_field": "strike_type",
    "floor_field": "floor_strike", "cap_field": "cap_strike", "open_time_field": "open_time",
    "close_time_field": "close_time", "settle_value_field": "expiration_value",
    "settled_at_field": "settlement_ts",
}
RENAMED_MARKET_FIELDS = {
    "ticker_field": "contract_id", "event_field": "group_id", "series_field": "family",
    "status_field": "state", "result_field": "outcome", "strike_type_field": "shape",
    "floor_field": "lo", "cap_field": "hi", "open_time_field": "listed_at",
    "close_time_field": "expires_at", "settle_value_field": "final_level", "settled_at_field": "final_at",
}
BAR_FIELDS = {
    "ticker_field": "ticker", "end_field": "ts", "bid_field": "yes_bid_close", "ask_field": "yes_ask_close",
    "price_field": "close", "volume_field": "volume", "open_interest_field": "open_interest",
}
RENAMED_BAR_FIELDS = {
    "ticker_field": "contract_id", "end_field": "bar_end_s", "bid_field": "best_bid", "ask_field": "best_ask",
    "price_field": "last", "volume_field": "qty", "open_interest_field": "oi",
}
FEE_FIELDS = {"series_field": "series_ticker", "fee_type_field": "fee_type",
              "multiplier_field": "fee_multiplier", "retrieved_field": "retrieved"}
RENAMED_FEE_FIELDS = {"series_field": "family", "fee_type_field": "schedule_kind",
                      "multiplier_field": "scale", "retrieved_field": "pulled_at"}

VOCAB = {
    "series": ["S15", "T15"],
    "payoff_by_strike_type": {"gt": "above", "gte": "above", "lt": "below", "lte": "below", "range": "between"},
    "result_labels": {"Y": 1, "N": 0},
    "settled_statuses": ["done", "closed"],
    "strike_known_lag_s": {"S15": 30, "T15": 30},
}
STORE = {"root": "/unused", "source": "binary-src", "stream": "markets"}


def market_params(fields=MARKET_FIELDS, **over):
    return {**STORE, **fields, **VOCAB, **over}


def raw(fields=MARKET_FIELDS, ticker="S15-0015-15", **over):
    """One market row as the stream stores it, keyed by LOGICAL name and written under ``fields``."""
    logical = {"ticker": ticker, "event": ticker.rsplit("-", 1)[0], "series": ticker.split("-")[0],
               "strike_type": "gte", "floor": 100.0, "cap": None, "status": "done", "result": "Y",
               "open_time": iso(OPEN), "close_time": iso(CLOSE)}
    logical.update(over)
    row = {fields[f"{name}_field"]: value for name, value in logical.items()}
    row["unrelated_column"] = "kept out of the row"
    return row


def history(fields=MARKET_FIELDS, **over):
    return raw(fields, **{"settle_value": 101.25, "settled_at": "2026-09-02T00:17:20.5Z", **over})


def project(rows, fields=MARKET_FIELDS, **params):
    node = BinaryMarketRows("markets", market_params(fields, **params))
    return node, node.project(rows)


BOTH_MARKET_MAPS = pytest.mark.parametrize("fields", [MARKET_FIELDS, RENAMED_MARKET_FIELDS], ids=["plain", "renamed"])


# -- BinaryMarketRows -----------------------------------------------------------------------


@BOTH_MARKET_MAPS
def test_a_settled_market_becomes_one_row_in_the_toolkit_vocabulary(fields):
    _, rows = project([raw(fields)], fields)
    assert rows == [{
        bd.TICKER: "S15-0015-15", bd.EVENT: "S15-0015", bd.SERIES: "S15", bd.STRIKE_TYPE: "gte",
        bd.PAYOFF: "above", bd.FLOOR_STRIKE: 100.0, bd.CAP_STRIKE: None,
        bd.OPEN_MS: ms(OPEN), bd.CLOSE_MS: ms(CLOSE), bd.LABEL: 1, bd.STRIKE_KNOWN_MS: ms(OPEN) + 30_000}]


def test_the_renamed_stream_read_through_the_plain_map_is_refused_so_no_literal_survives():
    # a row with no field under the declared ticker name cannot even be listed as excluded
    with pytest.raises(KeyError, match="'ticker'"):
        project([raw(RENAMED_MARKET_FIELDS)], MARKET_FIELDS)


@BOTH_MARKET_MAPS
def test_the_strike_is_known_a_declared_lag_after_the_open_and_at_the_open_otherwise(fields):
    _, rows = project([raw(fields), raw(fields, "T15-0015-15")], fields, strike_known_lag_s={"S15": 45})
    assert rows[0][bd.STRIKE_KNOWN_MS] == rows[0][bd.OPEN_MS] + 45_000
    assert rows[1][bd.STRIKE_KNOWN_MS] == rows[1][bd.OPEN_MS], "a series with no lag listed has its strike at the open"


@pytest.mark.parametrize("strike_type, floor, cap, payoff", [
    ("gt", 100.0, None, "above"), ("gte", 100.0, None, "above"), ("lt", None, 110.0, "below"),
    ("lte", None, 110.0, "below"), ("range", 100.0, 110.0, "between")])
def test_every_declared_geometry_maps_to_a_payoff(strike_type, floor, cap, payoff):
    _, rows = project([raw(RENAMED_MARKET_FIELDS, strike_type=strike_type, floor=floor, cap=cap)],
                      RENAMED_MARKET_FIELDS)
    assert rows[0][bd.PAYOFF] == payoff


def test_result_is_the_label_and_nothing_else_is_guessed():
    _, rows = project([raw(result="N")])
    assert rows[0][bd.LABEL] == 0


@pytest.mark.parametrize("over, reason", [
    ({"status": "open", "result": ""}, "not_settled"),
    ({"result": ""}, "no_result"),
    ({"result": "void"}, "result_not_binary"),
    ({"strike_type": "", "floor": None}, "no_strike"),
    ({"strike_type": "structured"}, "unknown_strike_type"),
    ({"strike_type": "gt", "floor": None}, "strike_missing"),
    ({"strike_type": "gt", "floor": 0.0}, "strike_missing"),
    ({"strike_type": "gt", "floor": -5.0}, "strike_missing"),
    ({"strike_type": "lt", "floor": 100.0, "cap": None}, "strike_missing"),
    ({"strike_type": "range", "floor": 100.0, "cap": None}, "strike_missing"),
    ({"close_time": ""}, "bad_time"),
    ({"open_time": "not a time"}, "bad_time"),
    ({"open_time": "2026-09-02T00:00:00"}, "bad_time"),
])
@BOTH_MARKET_MAPS
def test_a_row_that_cannot_be_labelled_or_placed_is_excluded_by_name(fields, over, reason):
    node, rows = project([raw(fields, **over)], fields)
    assert rows == []
    assert node.excluded == [{bd.TICKER: "S15-0015-15", "reason": reason}]


def test_an_unpublished_strike_is_dropped_by_ticker_never_guessed_and_counted():
    pending = raw(ticker="S15-0215-15", strike_type="", floor=None)
    node, rows = project([pending, raw()])
    assert [r[bd.TICKER] for r in rows] == ["S15-0015-15"]
    assert node.excluded == [{bd.TICKER: "S15-0215-15", "reason": "no_strike"}]
    assert node.census == {"rows": 2, "kept": 1, "no_strike": 1}


def test_rows_come_out_in_close_order_for_a_reproducible_table():
    later = raw(ticker="S15-0030-30", close_time=iso(utc(2026, 9, 2, 0, 30)))
    _, rows = project([later, raw()])
    assert [r[bd.CLOSE_MS] for r in rows] == sorted(r[bd.CLOSE_MS] for r in rows)


def test_only_the_declared_series_are_read_at_intake_under_the_mapped_series_field():
    node = BinaryMarketRows("markets", market_params(RENAMED_MARKET_FIELDS))
    assert node.keep_values() == {"family": ["S15", "T15"]}
    assert node.key_fields() == ("contract_id",)


@BOTH_MARKET_MAPS
def test_a_settled_row_carries_the_settlement_value_and_the_instant_it_became_known(fields):
    (row,) = project([history(fields)], fields)[1]
    assert row[bd.SETTLE_VALUE] == 101.25
    assert row[bd.SETTLEMENT_MS] == ms(utc(2026, 9, 2, 0, 17, 20)) + 500
    assert row[bd.SETTLEMENT_MS] > row[bd.CLOSE_MS], "the label exists only after the close: gate a join on it"
    assert row[bd.LABEL] == 1 and "unrelated_column" not in row


def test_a_stream_without_the_settlement_fields_leaves_the_row_exactly_as_it_was():
    _, rows = project([raw()])
    assert bd.SETTLE_VALUE not in rows[0] and bd.SETTLEMENT_MS not in rows[0]


def test_an_unsettled_value_is_none_and_a_missing_settlement_instant_is_excluded_by_name():
    _, rows = project([history(settle_value=None)])
    assert rows[0][bd.SETTLE_VALUE] is None and rows[0][bd.LABEL] == 1
    node, rows = project([history(settled_at="")])
    assert rows == [] and node.excluded == [{bd.TICKER: "S15-0015-15", "reason": "no_settlement_ts"}]
    node, rows = project([history(settled_at="2026-09-02T00:17:20")])  # no zone: a guess
    assert rows == [] and node.excluded[0]["reason"] == "no_settlement_ts"


def test_the_vocabularies_and_the_field_map_are_params_with_no_defaults():
    with pytest.raises(Exception, match="mystery"):
        BinaryMarketRows("markets", market_params(payoff_by_strike_type={"gt": "mystery"}))
    for knob in (*VOCAB, *MARKET_FIELDS, "stream"):
        with pytest.raises(Exception, match=knob):
            BinaryMarketRows("markets", {k: v for k, v in market_params().items() if k != knob})
    for bad_lag in ({"U15": 30}, {"S15": -1}, {"S15": "30"}, ["S15"]):
        with pytest.raises(Exception, match="strike_known_lag_s"):
            BinaryMarketRows("markets", market_params(strike_known_lag_s=bad_lag))
    with pytest.raises(Exception, match="surprise"):
        BinaryMarketRows("markets", market_params(surprise=1))
    with pytest.raises(Exception, match="result_labels"):
        BinaryMarketRows("markets", market_params(result_labels={"Y": 2}))
    for bad in ("", 5, None):
        with pytest.raises(Exception, match="floor_field"):
            BinaryMarketRows("markets", market_params(floor_field=bad))


@pytest.mark.parametrize("cls, params", [
    (BinaryMarketRows, market_params()),
    (QuoteBarRows, {**STORE, **BAR_FIELDS}),
    (FeeScheduleRows, {**STORE, **FEE_FIELDS})])
def test_the_pinned_seam_knobs_are_narrowed_away_not_left_to_the_document(cls, params):
    assert accessor_narrowing_problems(cls) == []
    assert cls("n", params).ts_field() is None
    for pinned in ("key_fields", "ts_field", "since_ms"):
        with pytest.raises(Exception, match=pinned):
            cls("n", {**params, pinned: ["x"] if pinned == "key_fields" else "x"})
    assert cls.serving_effect(params, {}) == "forbidden", "a research reader stays out of served graphs"


# -- a real acquisition through the scan seam ----------------------------------------------


def _store(tmp_path, stream, rows, effective_field):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    with open(data / f"{stream}.jsonl", "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": "binary-src", "catalog_source": "binary-src-cat", "connector": "localfiles",
        "config": {"path": str(data), "effective_field": effective_field}}, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(root, registry, "binary-src", stream, "backfill")
    return str(tmp_path / "ob")


def test_markets_read_through_a_real_acquisition_under_a_renamed_map(tmp_path):
    f = RENAMED_MARKET_FIELDS
    rows = [raw(f, "S15-0015-15", floor=100.0, result="Y"),
            raw(f, "T15-0015-15", floor=30.0, result="N"),
            raw(f, "S15-0030-15", strike_type="", floor=None, close_time=iso(utc(2026, 9, 2, 0, 30))),
            raw(f, "S15-0045-15", result="", close_time=iso(utc(2026, 9, 2, 0, 45))),
            raw(f, "U15-0015-15", series="U15")]
    root = _store(tmp_path, "markets", rows, f["close_time_field"])
    node = BinaryMarketRows("markets", market_params(f, root=root))
    out = node.run(None, {})
    assert [r[bd.TICKER] for r in out["records"]] == ["S15-0015-15", "T15-0015-15"]
    assert [r[bd.LABEL] for r in out["records"]] == [1, 0]
    assert out["census"] == {"rows": 4, "kept": 2, "no_strike": 1, "no_result": 1}, "U15 is never read"
    assert node.fingerprint()["rows"] == 2


def test_the_dropped_markets_and_census_are_written_beside_the_run(tmp_path):
    import os

    class Ctx:
        run_dir = str(tmp_path)

    node, _ = project([raw(), raw(ticker="S15-0215-15", strike_type="", floor=None)])
    assert node.write_dropped(Ctx()) is None
    directory = os.path.join(str(tmp_path), "artifacts", "markets")
    assert sorted(os.listdir(directory)) == ["census.json", "excluded.json"]
    assert json.load(open(os.path.join(directory, "excluded.json"), encoding="utf-8")) == node.excluded
    assert json.load(open(os.path.join(directory, "census.json"), encoding="utf-8")) == node.census
    node.write_dropped(None)


# -- QuoteBarRows ------------------------------------------------------------------------------


def bar_raw(fields, ts, **over):
    logical = {"ticker": "S15-0015-15", "end": ts, "bid": 0.39, "ask": 0.43, "price": 0.41,
               "volume": 12.0, "open_interest": 99.0}
    logical.update(over)
    row = {fields[f"{name}_field"]: value for name, value in logical.items()}
    row["open"] = 0.4
    return row


BOTH_BAR_MAPS = pytest.mark.parametrize("fields", [BAR_FIELDS, RENAMED_BAR_FIELDS], ids=["plain", "renamed"])


def bars(fields):
    return QuoteBarRows("c", {**STORE, "stream": "candles", **fields})


@BOTH_BAR_MAPS
def test_a_bar_end_instant_is_converted_from_seconds_to_milliseconds(fields):
    end = utc(2026, 9, 2, 0, 5)
    out = bars(fields).project([bar_raw(fields, int(end.timestamp()))])
    assert out == [{bd.TICKER: "S15-0015-15", bd.END_MS: ms(end), bd.YES_BID: 0.39, bd.YES_ASK: 0.43,
                    bd.PRICE: 0.41, bd.VOLUME: 12.0, bd.OPEN_INTEREST: 99.0}]
    assert bars(fields).key_fields() == (fields["ticker_field"], fields["end_field"])


@BOTH_BAR_MAPS
def test_a_bar_whose_end_already_looks_like_milliseconds_is_refused(fields):
    with pytest.raises(ValueError, match="milliseconds"):
        bars(fields).project([bar_raw(fields, int(utc(2026, 9, 2).timestamp()) * 1000)])
    assert SECONDS_CEILING == 10**11
    for bad in (-1, 1.5, True, "1788000060", None):
        with pytest.raises(ValueError, match="epoch seconds"):
            bars(fields).project([bar_raw(fields, bad)])


def test_a_quiet_interval_keeps_none_never_a_filled_value():
    f = RENAMED_BAR_FIELDS
    out = bars(f).project([bar_raw(f, 1_788_000_060, price=None, bid=None, ask=None, volume=None)])
    assert out[0][bd.PRICE] is None and out[0][bd.YES_BID] is None
    assert out[0][bd.YES_ASK] is None and out[0][bd.VOLUME] is None


def test_a_non_finite_number_is_none_not_a_value():
    f = BAR_FIELDS
    out = bars(f).project([bar_raw(f, 1_788_000_060, bid=float("nan"), volume="12")])
    assert out[0][bd.YES_BID] is None and out[0][bd.VOLUME] is None


def test_bars_come_out_ordered_by_ticker_then_end():
    f = RENAMED_BAR_FIELDS
    out = bars(f).project([bar_raw(f, 180, ticker="B"), bar_raw(f, 120, ticker="B"), bar_raw(f, 60, ticker="A")])
    assert [(r[bd.TICKER], r[bd.END_MS]) for r in out] == [("A", 60_000), ("B", 120_000), ("B", 180_000)]


def test_bar_fields_are_required_and_default_deny():
    for knob in (*BAR_FIELDS, "stream"):
        with pytest.raises(Exception, match=knob):
            QuoteBarRows("c", {k: v for k, v in {**STORE, **BAR_FIELDS}.items() if k != knob})
    with pytest.raises(Exception, match="surprise"):
        QuoteBarRows("c", {**STORE, **BAR_FIELDS, "surprise": 1})


# -- FeeScheduleRows ---------------------------------------------------------------------------


def fee_raw(fields, series="S15", fee_type="type_q", multiplier=1, retrieved="2026-10-06T00:30:00+00:00"):
    return {fields["series_field"]: series, fields["fee_type_field"]: fee_type,
            fields["multiplier_field"]: multiplier, fields["retrieved_field"]: retrieved}


@pytest.mark.parametrize("fields", [FEE_FIELDS, RENAMED_FEE_FIELDS], ids=["plain", "renamed"])
def test_the_fee_schedule_reader_keeps_the_schedule_and_the_retrieval_in_ms(fields):
    node = FeeScheduleRows("fees", {**STORE, "stream": "fee_schedules", **fields})
    out = node.project([fee_raw(fields, "T15"), fee_raw(fields, "S15", multiplier=None)])
    assert out == [
        {bd.SERIES: "S15", bd.FEE_TYPE: "type_q", bd.FEE_MULTIPLIER: None,
         bd.RETRIEVED: "2026-10-06T00:30:00+00:00", bd.RETRIEVED_MS: ms(utc(2026, 10, 6, 0, 30))},
        {bd.SERIES: "T15", bd.FEE_TYPE: "type_q", bd.FEE_MULTIPLIER: 1.0,
         bd.RETRIEVED: "2026-10-06T00:30:00+00:00", bd.RETRIEVED_MS: ms(utc(2026, 10, 6, 0, 30))}]
    assert node.key_fields() == (fields["series_field"], fields["retrieved_field"])
    with pytest.raises(ValueError, match="retrieved"):
        node.project([fee_raw(fields, retrieved="2026-10-06T00:30:00")])


def test_the_fee_schedule_reader_reads_a_real_acquisition(tmp_path):
    f = RENAMED_FEE_FIELDS
    root = _store(tmp_path, "fee_schedules", [fee_raw(f, s) for s in ("S15", "T15")], f["retrieved_field"])
    rows = FeeScheduleRows("fees", {**STORE, "root": root, "stream": "fee_schedules", **f}).run(None, {})["records"]
    assert [r[bd.SERIES] for r in rows] == ["S15", "T15"] and rows[0][bd.FEE_MULTIPLIER] == 1.0


def test_fee_fields_are_required_and_default_deny():
    for knob in FEE_FIELDS:
        with pytest.raises(Exception, match=knob):
            FeeScheduleRows("f", {k: v for k, v in {**STORE, **FEE_FIELDS}.items() if k != knob})


# -- the instant parser and its agreement with the platform's own ----------------------------


@pytest.mark.parametrize("text", [
    "2026-09-02T00:17:20.5Z", "1970-01-01T00:00:01Z", "2026-09-02T02:00:00+02:00", "1969-12-31T23:59:59.9995Z"])
def test_the_instant_parser_agrees_with_the_platforms_zone_required_parser(text):
    from dskit.production.base import parse_utc_ms

    assert instant_ms(text) == parse_utc_ms(text)


@pytest.mark.parametrize("text", ["", None, 5, "not a time", "2026-09-02T00:00:00", "2026-09-02"])
def test_an_unreadable_or_zoneless_instant_is_none_never_a_guess(text):
    assert instant_ms(text) is None


# -- FeeColumns --------------------------------------------------------------------------------

FEE_TYPES = {"type_q": {"base_rate": 0.07, "model": {
    "mechanic": "probability_quadratic", "rounding": {"policy": "ceil_to_tick", "tick": 0.01}}}}
MODEL = fee_model_from_spec(FEE_TYPES["type_q"]["model"])


def test_the_mechanics_are_the_toolkits_and_float_dust_buys_no_extra_tick():
    assert math.ceil(0.07 * 100 * 0.5 * (1 - 0.5) * 100) == 176, "premise: a naive ceil overshoots"
    assert MODEL.order_fee(100, 0.5, 0.07) == pytest.approx(1.75)
    assert MODEL.order_fee(1, 0.5, 0.07) == pytest.approx(0.02)


def schedule(series="S15", fee_type="type_q", multiplier=1.0, retrieved="2026-10-06T00:00:00+00:00",
             retrieved_ms=1_791_244_800_000):
    return {bd.SERIES: series, bd.FEE_TYPE: fee_type, bd.FEE_MULTIPLIER: multiplier,
            bd.RETRIEVED: retrieved, bd.RETRIEVED_MS: retrieved_ms}


def fee_row(series="S15", bid=0.40, ask=0.44):
    return {bd.TICKER: "T", bd.SERIES: series, bd.YES_BID: bid, bd.YES_ASK: ask}


def fees(rows, schedules, contracts=100, fee_types=None):
    node = FeeColumns("fees", {"fee_types": FEE_TYPES if fee_types is None else fee_types, "contracts": contracts})
    return node.run(None, {"records": rows, "schedules": schedules})["records"]


def test_node_prices_a_buy_of_yes_at_the_ask_and_of_no_at_one_minus_the_bid():
    out = fees([fee_row()], [schedule()])[0]
    assert out[bd.FEE_BUY_YES] == pytest.approx(0.0173)  # 0.07*100*0.44*0.56 = 1.7248 -> 1.73
    assert out[bd.FEE_BUY_NO] == pytest.approx(0.0168)  # 0.07*100*0.60*0.40 = 1.68
    assert out[bd.FEE_RATE] == pytest.approx(0.07) and out[bd.FEE_STATUS] == "ok"
    assert out[bd.FEE_SCHEDULE_RETRIEVED] == "2026-10-06T00:00:00+00:00"


def test_rounding_depends_on_the_declared_contract_count():
    assert fees([fee_row()], [schedule()], contracts=1)[0][bd.FEE_BUY_YES] == pytest.approx(0.02)


def test_the_series_multiplier_scales_the_rate():
    out = fees([fee_row()], [schedule(multiplier=0.5)])[0]
    assert out[bd.FEE_RATE] == pytest.approx(0.035) and out[bd.FEE_BUY_YES] == pytest.approx(0.0087)


def test_the_latest_schedule_per_series_wins_and_is_named():
    old = schedule(multiplier=2.0, retrieved="2026-09-01T00:00:00+00:00", retrieved_ms=1_788_220_800_000)
    out = fees([fee_row()], [old, schedule(multiplier=1.0)])[0]
    assert out[bd.FEE_RATE] == pytest.approx(0.07)
    assert out[bd.FEE_SCHEDULE_RETRIEVED] == "2026-10-06T00:00:00+00:00"


@pytest.mark.parametrize("rows, schedules, status", [
    ([fee_row()], [], "no_schedule"),
    ([fee_row()], [schedule(fee_type="type_unlisted")], "unsupported_fee_type"),
    ([fee_row()], [schedule(multiplier=None)], "no_multiplier"),
    ([fee_row(bid=None)], [schedule()], "no_quote"),
    ([fee_row(ask=None)], [schedule()], "no_quote"),
])
def test_what_cannot_be_priced_is_marked_never_defaulted(rows, schedules, status):
    out = fees(rows, schedules)[0]
    assert out[bd.FEE_STATUS] == status
    assert out[bd.FEE_BUY_YES] is None and out[bd.FEE_BUY_NO] is None


def test_a_negative_multiplier_is_no_multiplier_not_a_rebate():
    out = fees([fee_row()], [schedule(multiplier=-1.0)])[0]
    assert out[bd.FEE_STATUS] == "no_multiplier" and out[bd.FEE_RATE] is None


def test_the_no_side_is_priced_at_one_minus_the_bid_under_an_asymmetric_mechanic(monkeypatch):
    # every shipped mechanic is symmetric in P(1 - P), so only an asymmetric one tells 1 - bid from bid
    class Linear(FeeModel):
        name = "linear_test"

        def _exact_fee(self, contracts, price, rate):
            return rate * contracts * price

    monkeypatch.setattr(binary_market_rows, "fee_model_from_spec", lambda spec: Linear())
    out = fees([fee_row(bid=0.40, ask=0.44)], [schedule()])[0]
    assert out[bd.FEE_BUY_YES] == pytest.approx(0.07 * 0.44)
    assert out[bd.FEE_BUY_NO] == pytest.approx(0.07 * 0.60)


def test_a_second_fee_type_is_one_more_config_entry_not_code():
    types = {**FEE_TYPES, "type_grid": {"base_rate": 0.02, "model": {
        "mechanic": "probability_quadratic", "rounding": {"policy": "ceil_to_tick", "tick": 0.05}}}}
    out = fees([fee_row()], [schedule(fee_type="type_grid")], fee_types=types)[0]
    assert out[bd.FEE_STATUS] == "ok" and out[bd.FEE_BUY_YES] == pytest.approx(0.005)


def test_inputs_are_not_mutated_and_both_ports_must_be_lists():
    rows, schedules = [fee_row()], [schedule()]
    before = [dict(r) for r in rows]
    fees(rows, schedules)
    assert rows == before
    node = FeeColumns("fees", {"fee_types": FEE_TYPES, "contracts": 1})
    assert node.validate_inputs({"records": [], "schedules": ()}) == ["schedules must be a list of rows, got tuple"]


def test_fee_columns_default_deny_and_required_knobs():
    with pytest.raises(Exception, match="surprise"):
        FeeColumns("fees", {"fee_types": FEE_TYPES, "contracts": 1, "surprise": 1})
    with pytest.raises(Exception, match="contracts"):
        FeeColumns("fees", {"fee_types": FEE_TYPES})
    with pytest.raises(Exception, match="fee_types"):
        FeeColumns("fees", {"contracts": 1})
    with pytest.raises(Exception, match="contracts"):
        FeeColumns("fees", {"fee_types": FEE_TYPES, "contracts": 0})
    entry = FEE_TYPES["type_q"]
    with pytest.raises(Exception, match="mechanic"):
        FeeColumns("fees", {"fee_types": {"type_q": {**entry, "model": {**entry["model"], "mechanic": "mystery"}}},
                            "contracts": 1})
    with pytest.raises(Exception, match="base_rate"):
        FeeColumns("fees", {"fee_types": {"type_q": {**entry, "base_rate": -0.07}}, "contracts": 1})
    with pytest.raises(Exception, match="exactly the keys"):
        FeeColumns("fees", {"fee_types": {"type_q": {**entry, "notes": "a comment would move the hash"}},
                            "contracts": 1})


def test_fee_columns_validation_names_a_missing_contracts_and_a_negative_rate_as_problems():
    # refused by validate_params itself (as the first child's FeeColumns does), not by a KeyError later
    assert any("contracts is required" in p for p in FeeColumns.validate_params({"fee_types": FEE_TYPES}))
    entry = FEE_TYPES["type_q"]
    assert any("base_rate" in p for p in FeeColumns.validate_params(
        {"fee_types": {"type_q": {**entry, "base_rate": -0.07}}, "contracts": 1}))
    assert FeeColumns.validate_params({"fee_types": FEE_TYPES, "contracts": 1}) == []


# -- differential: the first child's interim classes, under its own field map ---------------------

_CHILD = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      "children", "crypto_trading")

#: The child's interim classes, run in their OWN interpreter: importing the child registers its
#: node kinds, which must not leak into this suite's process.
_CHILD_SCRIPT = """
import json, sys
sys.path.insert(0, sys.argv[1])
try:
    from crypto_trading.decisions import DecisionRows
    from crypto_trading.fees import FeeColumns
    from crypto_trading.kalshi_rows import CandleRows, FeeRows, MarketRows
    from crypto_trading.market_state import MarketState
except ImportError as exc:
    print(json.dumps({"skip": str(exc)}))
    raise SystemExit(0)
case = json.load(sys.stdin)
markets = MarketRows("markets", {"root": "r", "source": "s", **case["vocab"]})
rows = markets.project(case["markets"])
bars = CandleRows("c", {"root": "r", "source": "s"}).project(case["bars"])
schedules = FeeRows("f", {"root": "r", "source": "s"}).project(case["fees"])
decided = DecisionRows("d", case["decide"]).run(None, {"records": rows})
quoted = MarketState("q", case["quote"]).run(None, {"records": decided["records"], "candles": bars})["records"]
priced = FeeColumns("p", case["fee"]).run(None, {"records": quoted, "schedules": schedules})["records"]
print(json.dumps({"markets": rows, "excluded": markets.excluded, "census": markets.census, "bars": bars,
                  "fees": schedules, "decided_excluded": decided["excluded"], "priced": priced}))
"""


def _differential_case():
    """One synthetic input touching every exclusion, timing rule and fee status, in the child's field names."""
    f = MARKET_FIELDS
    markets = [
        history(f), raw(f, "T15-0015-15", result="N", floor=30.0),
        raw(f, "S15-0030-15", strike_type="range", floor=100.0, cap=110.0, close_time=iso(utc(2026, 9, 2, 0, 30))),
        raw(f, "S15-0045-15", strike_type="", floor=None), raw(f, "S15-0100-15", result="void"),
        raw(f, "S15-0115-15", status="open"), raw(f, "S15-0130-15", open_time="2026-09-02T00:00:00"),
        history(f, ticker="S15-0145-15", settled_at=""), raw(f, "S15-0200-15", strike_type="lt", cap=None),
    ]
    end = ms(CLOSE) // 1000
    quote_bars = [bar_raw(BAR_FIELDS, end - 120, bid=0.30, ask=0.34), bar_raw(BAR_FIELDS, end - 60, bid=None),
                  bar_raw(BAR_FIELDS, end - 300, ticker="T15-0015-15"),
                  bar_raw(BAR_FIELDS, end + 60, bid=0.99, ask=0.995)]
    schedules = [fee_raw(FEE_FIELDS, "S15"), fee_raw(FEE_FIELDS, "S15", multiplier=2, retrieved="2026-09-01T00:00:00Z"),
                 fee_raw(FEE_FIELDS, "T15", fee_type="type_unlisted")]
    return {"vocab": VOCAB, "markets": markets, "bars": quote_bars, "fees": schedules,
            "decide": {"leads_minutes": [1, 2, 5, 14.5, 20], "exec_lag_s": 5},
            "quote": {"max_candle_age_ms": 120_000, "quote_floor": 0.0, "quote_ceiling": 1.0},
            "fee": {"fee_types": FEE_TYPES, "contracts": 100}}


def _toolkit_side(case):
    markets = BinaryMarketRows("markets", market_params())
    rows = markets.project(case["markets"])
    quote_bars = bars(BAR_FIELDS).project(case["bars"])
    schedules = FeeScheduleRows("f", {**STORE, **FEE_FIELDS}).project(case["fees"])
    decided = DecisionRows("d", case["decide"]).run(None, {"records": rows})
    quoted = QuoteState("q", case["quote"]).run(None, {"records": decided["records"], "candles": quote_bars})
    priced = FeeColumns("p", case["fee"]).run(None, {"records": quoted["records"], "schedules": schedules})
    return {"markets": rows, "excluded": markets.excluded, "census": markets.census, "bars": quote_bars,
            "fees": schedules, "decided_excluded": decided["excluded"], "priced": priced["records"]}


def test_the_toolkit_classes_reproduce_the_first_childs_rows_and_census_under_its_field_map():
    if not os.path.isdir(os.path.join(_CHILD, "crypto_trading")):
        pytest.skip("the first child is not in this checkout")
    case = _differential_case()
    done = subprocess.run([sys.executable, "-c", _CHILD_SCRIPT, _CHILD], input=json.dumps(case),
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    child = json.loads(done.stdout)
    if "skip" in child:
        pytest.skip(f"the first child is not importable: {child['skip']}")
    ours = json.loads(json.dumps(_toolkit_side(case)))
    assert ours == child
    assert set(ours["census"]) - {"rows", "kept"} == {
        "no_strike", "result_not_binary", "not_settled", "bad_time", "no_settlement_ts", "strike_missing"}, (
        "the case exercises every exclusion it claims to")
    assert {e["reason"] for e in ours["decided_excluded"]} == {"before_open", "strike_not_known"}
    assert {"ok", "unsupported_fee_type"} <= {r[bd.FEE_STATUS] for r in ours["priced"]}
    assert any(r[bd.TWO_SIDED] for r in ours["priced"]) and any(r[bd.QUOTE_MISSING] for r in ours["priced"])
