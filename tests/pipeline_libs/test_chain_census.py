"""ChainCensus (ADR-0265 node 1): the per-root coverage and quality table of an option-chain stream.

Every expected number is counted by hand from the small fixture below, never read back from the
node. The fixture is three roots over a few dates; the last test reads the same rows through a
REAL onboarded store and the lazy ``ObservationStreamRows`` reader, the way the census document
does. No network, no vendor file.
"""

import json
import os

import pytest

from dskit.onboarding import OnboardingRoot, run_acquisition
from dskit.pipeline.base import ConfigError
from dskit.pipeline.libs import chain_census
from dskit.pipeline.libs.chain_census import (
    CROSSED,
    LOCKED,
    NULL,
    QUOTED,
    USABLE_STATES,
    ZERO,
    ChainCensus,
    quote_state,
)
from dskit.pipeline.libs.observations import ObservationStreamRows
from dskit.pipeline.node import NodeContext

FIELDS = ("symbol", "date", "strike", "bid", "ask", "iv_error", "underlying_price")


def row(symbol, date, strike, bid, ask, iv_error=0.0, underlying=50.0):
    """One chain row in the vendor's vocabulary, only the fields the census reads."""
    return dict(zip(FIELDS, (symbol, date, strike, bid, ask, iv_error, underlying)))


#: AAA, 15 rows: day 0 has no bid (3 zero rows), then 6 / 2 / 4 rows on three later dates.
AAA = [
    row("AAA", "2019-12-30", 100.0, 0.0, 0.05, 100.0),
    row("AAA", "2019-12-30", 101.0, 0.0, 0.05, 100.0),
    row("AAA", "2019-12-30", 102.0, 0.0, 0.05, 100.0),
    row("AAA", "2020-01-02", 100.0, 1.0, 1.2),
    row("AAA", "2020-01-02", 101.0, 0.9, 1.1, 100.0),
    row("AAA", "2020-01-02", 102.0, None, 1.0, 100.0),
    row("AAA", "2020-01-02", 103.0, 0.5, 0.4),
    row("AAA", "2020-01-02", 104.0, 0.5, 0.5),
    row("AAA", "2020-01-02", 33.333, 0.2, 0.3),
    row("AAA", "2020-01-03", 100.0, 1.0, 1.2, underlying=51.0),
    row("AAA", "2020-01-03", 101.0, 0.0, 0.1, 100.0, underlying=51.0),
    row("AAA", "2021-01-04", 100.0, 1.0, 1.2, underlying=55.0),
    row("AAA", "2021-01-04", 101.0, 1.0, 1.2, underlying=55.0),
    row("AAA", "2021-01-04", 102.0, 1.0, 1.2, underlying=55.0),
    row("AAA", "2021-01-04", 103.0, 1.0, 1.2, underlying=55.0),
]
BBB = [row("BBB", "2020-01-02", 20.0 + k, 1.0, 1.1, underlying=20.0) for k in range(4)]
CCC = [row("CCC", "2020-01-02", 10.0, 0.0, 0.05), row("CCC", "2020-01-02", 11.0, 0.0, 0.05)]
CLOSES = [
    {"sym": "AAA", "day": "2020-01-02", "px": 50.0},
    {"sym": "AAA", "day": "2020-01-03", "px": 50.0},
    {"sym": "AAA", "day": "2021-01-04", "px": 55.55},
]

PARAMS = {
    "symbol_field": "symbol", "date_field": "date", "strike_field": "strike",
    "bid_field": "bid", "ask_field": "ask", "iv_error_field": "iv_error", "iv_error_flag": 100,
    "underlying_price_field": "underlying_price",
    "strike_grid": [{"below": 200.0, "step": 0.5}, {"step": 1.0}],
    "end_before": "2026-01-01", "liquidity_end_before": "2021-01-01",
}
CLOSE_PARAMS = {"close_symbol_field": "sym", "close_date_field": "day", "close_field": "px",
                "price_tolerance": 0.015}


def census(rows, closes=None, **over):
    """Run the node over ``rows`` and return its outputs."""
    params = dict(PARAMS, **(CLOSE_PARAMS if closes is not None else {}), **over)
    params = {k: v for k, v in params.items() if v is not ...}
    inputs = {"records": rows}
    if closes is not None:
        inputs["closes"] = closes
    ctx = NodeContext(name="t", asof="2025-12-31", run_dir="/nonexistent")
    return ChainCensus("census", params).run(ctx, inputs)


def by_symbol(out):
    return {r["symbol"]: r for r in out["records"]}


# -- the quote classes ---------------------------------------------------------------------


@pytest.mark.parametrize("bid, ask, state", [
    (1.0, 1.2, QUOTED), (0.5, 0.5, LOCKED), (0.5, 0.4, CROSSED), (None, 1.0, NULL),
    (1.0, None, NULL), (None, None, NULL), (0.0, 0.05, ZERO), (0.05, 0.0, ZERO), (0.0, 0.0, ZERO),
    (-1.0, 0.5, ZERO), (float("nan"), 1.0, NULL),
])
def test_a_quote_has_exactly_one_state(bid, ask, state):
    assert quote_state(bid, ask) == state


def test_usable_quotes_are_the_priced_ones():
    assert set(USABLE_STATES) == {QUOTED, LOCKED}


@pytest.mark.parametrize("bad", ["1.0", True, [1.0]])
def test_a_non_numeric_bid_refuses(bad):
    with pytest.raises(ValueError, match="bid"):
        quote_state(bad, 1.0)


# -- the per-root table --------------------------------------------------------------------


def test_first_and_last_dates_with_a_bid():
    out = by_symbol(census(AAA + BBB + CCC))
    assert (out["AAA"]["first_bid_date"], out["AAA"]["last_bid_date"]) == ("2020-01-02", "2021-01-04")
    assert (out["AAA"]["first_date"], out["AAA"]["last_date"]) == ("2019-12-30", "2021-01-04")
    assert out["BBB"]["first_bid_date"] == out["BBB"]["last_bid_date"] == "2020-01-02"
    assert out["AAA"]["rows"] == 15 and out["AAA"]["dates"] == 4


def test_a_root_that_never_has_a_bid_is_named_not_dropped():
    out = by_symbol(census(AAA + BBB + CCC))
    assert out["CCC"]["never_quoted"] is True
    assert out["CCC"]["first_bid_date"] is None and out["CCC"]["last_bid_date"] is None
    assert out["CCC"]["median_quoted_per_date"] is None
    assert out["AAA"]["never_quoted"] is False


def test_quote_state_shares_over_all_rows_and_from_the_first_bid():
    out = by_symbol(census(AAA + BBB + CCC))["AAA"]
    # all 15 rows: 1 null, 4 zero (3 on day 0, 1 on day 2), 1 crossed, 1 locked, 8 quoted
    assert out["null_share"] == pytest.approx(1 / 15)
    assert out["zero_share"] == pytest.approx(4 / 15)
    assert out["crossed_share"] == pytest.approx(1 / 15)
    assert out["locked_share"] == pytest.approx(1 / 15)
    assert out["usable_share"] == pytest.approx(9 / 15)
    # from the first bid date (12 rows): the three day-0 zero rows leave the denominator
    assert out["rows_from_first_bid"] == 12
    assert out["zero_share_from_first_bid"] == pytest.approx(1 / 12)
    assert out["null_share_from_first_bid"] == pytest.approx(1 / 12)
    assert out["usable_share_from_first_bid"] == pytest.approx(9 / 12)


def test_iv_error_flag_share():
    out = by_symbol(census(AAA + BBB + CCC))["AAA"]
    assert out["iv_error_share"] == pytest.approx(6 / 15)         # 3 on day 0, 2 on day 1, 1 on day 2
    assert out["iv_error_share_from_first_bid"] == pytest.approx(3 / 12)
    assert by_symbol(census(AAA + BBB + CCC))["BBB"]["iv_error_share"] == 0.0


def test_the_flag_is_compared_as_a_number_not_a_spelling():
    rows = [row("AAA", "2020-01-02", 100.0, 1.0, 1.2, iv_error=spelling)
            for spelling in (100, 100.0, "100", 99.0, None)]
    assert by_symbol(census(rows))["AAA"]["iv_error_share"] == pytest.approx(3 / 5)


def test_median_quoted_contracts_per_date_and_its_development_version():
    out = by_symbol(census(AAA + BBB + CCC))
    # usable quotes per date from the first bid date: 4, 1, 4 -> median 4; before 2021-01-01: 4, 1 -> 2.5
    assert out["AAA"]["median_quoted_per_date"] == 4
    assert out["AAA"]["median_quoted_development"] == 2.5
    assert out["BBB"]["median_quoted_per_date"] == 4


def test_liquidity_terciles_come_from_the_development_median_only():
    rows = []
    for k, name in enumerate(["R1", "R2", "R3", "R4", "R5", "R6"], start=1):
        rows += [row(name, "2020-01-02", 100.0 + j, 1.0, 1.1) for j in range(k)]
        rows += [row(name, "2021-01-04", 100.0 + j, 1.0, 1.1) for j in range(7 - k)]  # reversed AFTER
    out = by_symbol(census(rows))
    assert [out[n]["liquidity_tercile"] for n in ("R1", "R2", "R3", "R4", "R5", "R6")] == [1, 1, 2, 2, 3, 3]
    assert out["R1"]["median_quoted_development"] == 1 and out["R6"]["median_quoted_development"] == 6


def test_a_root_with_no_development_date_has_no_tercile():
    out = by_symbol(census(AAA + BBB + CCC))
    assert out["CCC"]["liquidity_tercile"] is None
    late = [row("LATE", "2021-03-01", 10.0, 1.0, 1.1)]
    assert by_symbol(census(AAA + late))["LATE"]["liquidity_tercile"] is None


def test_rows_per_date_by_year():
    out = census(AAA + BBB + CCC)
    table = {(r["symbol"], r["year"]): r for r in out["rows_by_year"]}
    assert table[("AAA", 2019)]["rows"] == 3 and table[("AAA", 2019)]["dates"] == 1
    assert table[("AAA", 2020)]["rows"] == 8 and table[("AAA", 2020)]["dates"] == 2
    assert table[("AAA", 2020)]["rows_per_date"] == 4
    assert table[("AAA", 2021)]["rows_per_date"] == 4
    assert len(out["rows_by_year"]) == 5


def test_non_standard_strikes_by_month():
    out = census(AAA + BBB + CCC)
    table = {(r["symbol"], r["month"]): r for r in out["strikes_by_month"]}
    jan = table[("AAA", "2020-01")]
    assert (jan["rows"], jan["nonstandard_rows"], jan["nonstandard_strikes"]) == (8, 1, 1)
    assert table[("AAA", "2019-12")]["nonstandard_rows"] == 0
    assert by_symbol(out)["AAA"]["nonstandard_strike_rows"] == 1
    assert by_symbol(out)["AAA"]["nonstandard_strike_share"] == pytest.approx(1 / 15)


@pytest.mark.parametrize("strike, standard", [
    (150.5, True), (150.25, False), (33.333, False), (199.5, True), (200.0, True), (200.5, False),
    (201.0, True), (1000.0, True), (0.5, True),
])
def test_the_strike_grid_tiers(strike, standard):
    out = census([row("AAA", "2020-01-02", strike, 1.0, 1.1)])
    assert by_symbol(out)["AAA"]["nonstandard_strike_rows"] == (0 if standard else 1)


def test_the_distinct_non_standard_strikes_are_counted_once():
    rows = [row("AAA", day, 33.333, 1.0, 1.1) for day in ("2020-01-02", "2020-01-03")]
    table = census(rows)["strikes_by_month"]
    assert table[0]["nonstandard_rows"] == 2 and table[0]["nonstandard_strikes"] == 1


# -- the external close comparison ---------------------------------------------------------


def test_underlying_price_against_the_external_close():
    out = census(AAA + BBB + CCC, closes=CLOSES)
    aaa = by_symbol(out)["AAA"]
    assert aaa["price_compared"] == 3 and aaa["price_missing_close"] == 1     # day 0 has no close
    assert aaa["price_median_abs_rel_diff"] == pytest.approx(abs(55.0 - 55.55) / 55.55)
    assert aaa["price_share_beyond_tolerance"] == pytest.approx(1 / 3)        # 2% on day 2 > 1.5%
    assert aaa["price_max_abs_rel_diff"] == pytest.approx(0.02)
    bbb = by_symbol(out)["BBB"]
    assert bbb["price_compared"] == 0 and bbb["price_missing_close"] == 1
    assert bbb["price_median_abs_rel_diff"] is None
    assert "price_check" not in out["summary"]["skipped"]


def test_without_a_closes_input_the_price_check_is_skipped_and_said_so():
    out = census(AAA)
    assert "closes" in out["summary"]["skipped"]["price_check"]
    assert "price_compared" not in by_symbol(out)["AAA"]


def test_a_day_whose_rows_disagree_on_the_underlying_is_counted():
    rows = [row("AAA", "2020-01-02", 100.0, 1.0, 1.1, underlying=50.0),
            row("AAA", "2020-01-02", 101.0, 1.0, 1.1, underlying=52.0)]
    out = census(rows, closes=[{"sym": "AAA", "day": "2020-01-02", "px": 51.0}])
    assert by_symbol(out)["AAA"]["price_spread_dates"] == 1       # 2/50 = 4% apart, above 1.5%
    assert by_symbol(out)["AAA"]["price_share_beyond_tolerance"] == 0.0     # the midpoint is the close


def test_a_non_positive_underlying_price_is_counted_not_compared():
    rows = [row("AAA", "2020-01-02", 100.0, 1.0, 1.1, underlying=0.0),
            row("AAA", "2020-01-03", 100.0, 1.0, 1.1, underlying=50.0)]
    out = by_symbol(census(rows, closes=CLOSES))["AAA"]
    assert out["underlying_price_nonpositive_rows"] == 1 and out["price_compared"] == 1
    assert out["price_missing_close"] == 0


def test_a_null_underlying_price_is_counted():
    rows = [row("AAA", "2020-01-02", 100.0, 1.0, 1.1, underlying=None),
            row("AAA", "2020-01-02", 101.0, 1.0, 1.1, underlying=float("nan")),
            row("AAA", "2020-01-02", 102.0, 1.0, 1.1, underlying=50.0)]
    out = by_symbol(census(rows))["AAA"]
    assert out["underlying_price_null_rows"] == 2 and out["underlying_price_nonpositive_rows"] == 0


def test_a_closes_row_on_or_after_end_before_refuses():
    late = CLOSES + [{"sym": "AAA", "day": "2026-01-05", "px": 60.0}]
    with pytest.raises(ValueError, match="end_before"):
        census(AAA, closes=late)


def test_the_reader_counters_are_copied_into_the_summary():
    class Reporting(list):
        def report(self):
            return {"skipped_rows": 3}

    assert census(Reporting(AAA))["summary"]["reader"] == {"skipped_rows": 3}
    assert "reader" not in census(AAA)["summary"]


def test_a_reader_reporting_split_units_refuses_unless_allowed():
    class Splitting(list):
        def report(self):
            return {"unit_row_mismatch": 1}

    with pytest.raises(ValueError, match="unit_row_mismatch"):
        census(Splitting(AAA))
    assert census(Splitting(AAA), allow_unit_mismatch=True)["summary"]["reader"]["unit_row_mismatch"] == 1
    assert ChainCensus.validate_params(dict(PARAMS, allow_unit_mismatch="yes"))


def test_memory_is_roots_times_dates_not_rows():
    import tracemalloc
    rows = [row("AAA", f"2020-01-{1 + d % 28:02d}", 100.0 + k, 1.0, 1.1) for d in range(28) for k in range(400)]
    tracemalloc.start()
    out = census(iter(rows))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert out["summary"]["rows"] == 11200
    held = rows                                                          # the input itself is the test's, not the node's
    assert held and peak < 3_000_000


def test_a_closes_input_with_a_duplicate_day_refuses():
    dup = CLOSES + [dict(CLOSES[0])]
    with pytest.raises(ValueError, match="duplicate"):
        census(AAA, closes=dup)


def test_closes_without_the_close_field_params_refuse():
    ctx = NodeContext(name="t", asof="2025-12-31", run_dir="/nonexistent")
    with pytest.raises(ValueError, match="closes"):
        ChainCensus("c", dict(PARAMS)).run(ctx, {"records": AAA, "closes": CLOSES})


# -- skipped checks ------------------------------------------------------------------------


def test_undeclared_optional_checks_are_skipped_and_said_so():
    out = census(AAA, strike_grid=..., iv_error_field=..., iv_error_flag=..., underlying_price_field=...,
                 liquidity_end_before=...)
    skipped = out["summary"]["skipped"]
    assert {"strike_grid", "iv_error", "price_check", "liquidity_development"} <= set(skipped)
    assert all(skipped[name] for name in skipped)
    assert out["strikes_by_month"] == []
    one = by_symbol(out)["AAA"]
    assert "iv_error_share" not in one and "nonstandard_strike_rows" not in one
    assert "median_quoted_development" not in one


# -- loud refusals -------------------------------------------------------------------------


def test_an_empty_input_refuses():
    with pytest.raises(ValueError, match="empty"):
        census([])
    with pytest.raises(ValueError, match="empty"):
        census(iter(()))


def test_a_row_on_or_after_end_before_refuses_and_names_the_row():
    rows = AAA + [row("AAA", "2026-01-02", 100.0, 1.0, 1.1)]
    with pytest.raises(ValueError, match=r"end_before.*2026-01-02|2026-01-02.*end_before"):
        census(rows)
    with pytest.raises(ValueError, match="end_before"):
        census([row("AAA", "2026-01-01", 100.0, 1.0, 1.1)])      # end_before is exclusive


def test_a_missing_field_a_bad_date_and_a_bad_number_refuse_by_row():
    broken = dict(AAA[3])
    del broken["bid"]
    with pytest.raises(ValueError, match=r"row 1.*bid"):
        census([AAA[3], broken])
    with pytest.raises(ValueError, match="date"):
        census([row("AAA", "01/02/2020", 100.0, 1.0, 1.1)])
    with pytest.raises(ValueError, match="strike"):
        census([row("AAA", "2020-01-02", "x", 1.0, 1.1)])
    with pytest.raises(ValueError, match="symbol"):
        census([row("", "2020-01-02", 100.0, 1.0, 1.1)])


def test_the_input_is_read_once():
    consumed = []

    def once():
        for r in AAA + BBB:
            consumed.append(r["symbol"])
            yield r

    assert len(census(once())["records"]) == 2
    assert len(consumed) == len(AAA) + len(BBB)


@pytest.mark.parametrize("over, fragment", [
    ({"surprise": 1}, "surprise"),
    ({"symbol_field": ...}, "symbol_field"),
    ({"end_before": "2026-1-1"}, "end_before"),
    ({"strike_grid": []}, "strike_grid"),
    ({"strike_grid": [{"below": 5.0, "step": 0.5}]}, "strike_grid"),                       # no open last tier
    ({"strike_grid": [{"below": 5.0, "step": 0.5}, {"below": 3.0, "step": 1.0}, {"step": 1.0}]}, "strike_grid"),
    ({"strike_grid": [{"step": 0.0}]}, "strike_grid"),
    ({"iv_error_flag": ...}, "iv_error_flag"),
    ({"iv_error_field": ...}, "iv_error_flag"),
    ({"liquidity_end_before": "soon"}, "liquidity_end_before"),
    ({"price_tolerance": 0.1}, "close"),                       # a tolerance without the close fields
    ({"close_field": "px"}, "close"),                          # a partial close declaration
])
def test_bad_params_refuse_by_name(over, fragment):
    params = {k: v for k, v in dict(PARAMS, **over).items() if v is not ...}
    problems = ChainCensus.validate_params(params)
    assert any(fragment in p for p in problems), problems
    with pytest.raises(ConfigError):
        ChainCensus("c", params)


def test_the_reference_params_validate_clean():
    assert ChainCensus.validate_params(PARAMS) == []
    assert ChainCensus.validate_params(dict(PARAMS, **CLOSE_PARAMS)) == []


def test_the_pack_surface():
    assert ChainCensus.role == "transform"
    assert ChainCensus.outputs == ("records", "rows_by_year", "strikes_by_month", "reuse_candidates", "stale_spot_days", "summary")
    assert chain_census.NODE_KINDS == ()
    for name in ("ChainCensus", "quote_state", "USABLE_STATES", "NODE_KINDS"):
        assert name in chain_census.__all__


# -- the summary ---------------------------------------------------------------------------


def test_the_summary_counts_the_run():
    summary = census(AAA + BBB + CCC)["summary"]
    assert summary["roots"] == 3 and summary["rows"] == 21 and summary["never_quoted_roots"] == 1
    assert (summary["first_date"], summary["last_date"]) == ("2019-12-30", "2021-01-04")
    assert summary["end_before"] == "2026-01-01"
    assert summary["states"] == {"null": 1, "zero": 4 + 2, "crossed": 1, "locked": 1, "quoted": 8 + 4}


# -- through a real onboarded store ----------------------------------------------------------


def test_the_census_reads_an_onboarded_stream_through_the_lazy_reader(tmp_path):
    keyed = [dict(r, expiration="2020-02-21", right="call") for r in AAA + BBB + CCC]
    data = tmp_path / "data"
    data.mkdir()
    with open(data / "option_eod.jsonl", "w", encoding="utf-8") as fh:
        for r in keyed:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": "theta", "catalog_source": "theta-src", "connector": "localfiles",
        "config": {"path": str(data), "effective_field": "date"}}, origin="test")
    registry.transition(vid, "active", origin="test")
    assert run_acquisition(root, registry, "theta", "option_eod", "backfill")["records"] == len(keyed)
    reader = ObservationStreamRows("chain", {
        "root": root.root, "source": "theta", "stream": "option_eod",
        "key_fields": ["symbol", "expiration", "strike", "right", "date"], "fields": list(FIELDS)})
    ctx = NodeContext(name="t", asof="2025-12-31", run_dir=str(tmp_path / "run"))
    reader.fingerprint()
    rows = reader.run(ctx, {})["records"]
    out = ChainCensus("census", dict(PARAMS)).run(ctx, {"records": rows})
    assert by_symbol(out)["AAA"]["rows"] == 15 and by_symbol(out)["AAA"]["first_bid_date"] == "2020-01-02"
    assert out["summary"]["rows"] == len(keyed) and os.path.isdir(root.root)


# -- suspected symbol reuse -------------------------------------------------------------------------

from datetime import date, timedelta  # noqa: E402

REUSE = {"reuse_price_factor": 2.0, "reuse_rows_factor": 4.0, "reuse_window": 5, "reuse_band": 0.25,
         "reuse_overlap_max": 0.1, "reuse_split_share": 0.05, "reuse_persistence": 5}


def regime(symbol, start, days, price, strikes, odd=()):
    """``days`` consecutive calendar dates from ``start``, one row per strike (plus ``odd`` strikes) each date."""
    out = []
    for i in range(days):
        day = (date.fromisoformat(start) + timedelta(days=i)).isoformat()
        out += [row(symbol, day, float(k), 1.0, 1.2, underlying=price) for k in (*strikes, *odd)]
    return out


def reuse(rows, **over):
    return census(rows, **{**REUSE, **over})


def candidates(out):
    return {(c["symbol"], c["date"]): c for c in out["reuse_candidates"]}


#: a different issuer takes the symbol on 2020-01-11: price 3 -> 100, 3 strikes a date -> 21, strikes disjoint.
TAKEN = (regime("TAKEN", "2020-01-01", 10, 3.0, [2.5, 3.0, 3.5])
         + regime("TAKEN", "2020-01-11", 10, 100.0, range(90, 111)))
#: a real 2:1 split on 2020-01-11 with adjusted (off-grid) strikes appearing after it.
SPLIT = (regime("SPLIT", "2020-01-01", 10, 100.0, range(90, 111))
         + regime("SPLIT", "2020-01-11", 10, 50.0, range(45, 56), odd=(41.667, 33.333, 66.667)))
#: a price that triples with the same chain size, no off-grid strikes.
JUMP = (regime("JUMP", "2020-01-01", 10, 50.0, range(40, 61))
        + regime("JUMP", "2020-01-11", 10, 150.0, range(140, 161)))
#: liquidity grows eightfold on the SAME strikes and price: continuous, not a reuse.
GROWS = (regime("GROWS", "2020-01-01", 10, 50.0, [49, 50, 51]) + regime("GROWS", "2020-01-11", 10, 50.0, range(38, 63)))
CALM = regime("CALM", "2020-01-01", 20, 50.0, range(45, 56))
#: a price spike that reverts after three dates: a move, not a new company.
SPIKE = (regime("SPIKE", "2020-01-01", 10, 50.0, range(40, 61)) + regime("SPIKE", "2020-01-11", 3, 150.0, range(140, 161))
         + regime("SPIKE", "2020-01-14", 10, 50.0, range(40, 61)))


def test_a_new_issuer_on_a_reused_symbol_is_a_candidate_with_its_evidence():
    out = reuse(TAKEN + CALM)
    found = candidates(out)
    assert list(found) == [("TAKEN", "2020-01-11")]
    c = found[("TAKEN", "2020-01-11")]
    assert c["previous_date"] == "2020-01-10" and c["gap_days"] == 1
    assert c["reasons"] == ["price_jump", "rows_shift"]
    assert (c["price_before"], c["price_after"]) == (3.0, 100.0) and c["price_ratio"] == pytest.approx(100 / 3)
    assert (c["rows_median_before"], c["rows_median_after"]) == (3, 21) and c["rows_ratio"] == pytest.approx(7.0)
    assert c["near_spot_overlap"] == 0.0 and c["split_signature"] is False
    assert c["nonstandard_share_before"] == 0.0 and c["nonstandard_share_after"] == 0.0


def test_a_real_split_with_off_grid_strikes_is_explained_and_counted_not_flagged():
    out = reuse(SPLIT + CALM)
    assert out["reuse_candidates"] == []
    assert out["summary"]["reuse"] == {"candidates": 0, "split_explained": 1, "reverted": 0}


def test_a_price_jump_alone_is_flagged_without_the_rows_reason():
    c = candidates(reuse(JUMP))[("JUMP", "2020-01-11")]
    assert c["reasons"] == ["price_jump"] and c["rows_ratio"] == pytest.approx(1.0)


def test_growing_liquidity_on_continuous_strikes_is_not_a_reuse():
    assert reuse(GROWS)["reuse_candidates"] == []


def test_without_a_strike_grid_the_split_signature_cannot_be_checked_and_the_jump_is_flagged():
    out = reuse(SPLIT, strike_grid=...)
    c = candidates(out)[("SPLIT", "2020-01-11")]
    assert c["split_signature"] is None and c["reasons"] == ["price_jump"]


def test_a_root_with_one_regime_and_the_input_order_change_nothing():
    rows = TAKEN + SPLIT + JUMP + GROWS + CALM
    forward = reuse(rows)["reuse_candidates"]
    assert forward == reuse(list(reversed(rows)))["reuse_candidates"]
    assert [(c["symbol"], c["date"]) for c in forward] == [("JUMP", "2020-01-11"), ("TAKEN", "2020-01-11")]


def test_a_missing_price_day_is_bridged_by_the_previous_priced_day():
    rows = [r for r in TAKEN if r["date"] != "2020-01-11"]
    c = candidates(reuse(rows))[("TAKEN", "2020-01-12")]
    assert c["previous_date"] == "2020-01-10" and c["gap_days"] == 2


def test_the_detector_is_skipped_and_said_so_when_not_declared():
    out = census(TAKEN)
    assert out["reuse_candidates"] == [] and "reuse" in out["summary"]["skipped"]
    assert "reuse" not in reuse(TAKEN)["summary"]["skipped"]


@pytest.mark.parametrize("over, fragment", [
    ({"reuse_price_factor": 1.0}, "reuse_price_factor"),
    ({"reuse_rows_factor": 0.5}, "reuse_rows_factor"),
    ({"reuse_window": 0}, "reuse_window"),
    ({"reuse_band": 0}, "reuse_band"),
    ({"reuse_overlap_max": 1.5}, "reuse_overlap_max"),
    ({"reuse_split_share": -0.1}, "reuse_split_share"),
    ({"reuse_price_factor": ...}, "reuse"),                    # the rest declared without the price factor
    ({"underlying_price_field": ...}, "underlying_price_field"),
])
def test_bad_reuse_params_refuse_by_name(over, fragment):
    params = {k: v for k, v in {**PARAMS, **REUSE, **over}.items() if v is not ...}
    assert any(fragment in p for p in ChainCensus.validate_params(params)), ChainCensus.validate_params(params)


def test_a_price_spike_that_reverts_inside_the_persistence_window_is_not_a_candidate_and_is_counted():
    out = reuse(SPIKE + JUMP)
    assert [(c["symbol"], c["date"]) for c in out["reuse_candidates"]] == [("JUMP", "2020-01-11")]
    assert out["summary"]["reuse"]["reverted"] == 2                      # the way up and the way back
    assert candidates(out)[("JUMP", "2020-01-11")]["price_persists"] is True


def test_without_a_persistence_param_a_reverting_spike_is_still_listed():
    out = reuse(SPIKE, reuse_persistence=...)
    assert ("SPIKE", "2020-01-11") in candidates(out)
    assert candidates(out)[("SPIKE", "2020-01-11")]["price_persists"] is None


def test_a_jump_at_the_end_of_the_series_with_fewer_later_dates_is_judged_on_those():
    short = regime("TAIL", "2020-01-01", 10, 50.0, range(40, 61)) + regime("TAIL", "2020-01-11", 2, 150.0, range(140, 161))
    assert ("TAIL", "2020-01-11") in candidates(reuse(short))


def test_a_bad_persistence_refuses():
    params = {**PARAMS, **REUSE, "reuse_persistence": 0}
    assert any("reuse_persistence" in p for p in ChainCensus.validate_params(params))


# -- stale vendor spots (flagged by the feature node, counted per root here) -------------------------------


def stale_file(tmp_path, *entries):
    path = tmp_path / "stale.jsonl"
    path.write_text("\n".join(json.dumps({"symbol": s, "date": d}) for s, d in entries) + "\n")
    return {"stale_spots_file": str(path)}


def test_flagged_stale_days_are_counted_per_root_and_listed(tmp_path):
    cfg = stale_file(tmp_path, ("AAA", "2020-01-03"), ("AAA", "2020-01-03"), ("BBB", "2020-01-02"), ("ZZZ", "2020-01-02"))
    out = census(AAA + BBB + CCC, **cfg)
    roots = by_symbol(out)
    assert (roots["AAA"]["stale_spot_days"], roots["BBB"]["stale_spot_days"], roots["CCC"]["stale_spot_days"]) == (1, 1, 0)
    assert out["stale_spot_days"] == [{"symbol": "AAA", "date": "2020-01-03"}, {"symbol": "BBB", "date": "2020-01-02"},
                                      {"symbol": "ZZZ", "date": "2020-01-02"}]
    assert out["summary"]["stale_spot_days"] == 3 and out["summary"]["stale_spot_unknown_roots"] == 1


def test_a_missing_stale_file_is_a_counted_skip_not_a_refusal(tmp_path):
    out = census(AAA, stale_spots_file=str(tmp_path / "nope.jsonl"))
    assert "not found" in out["summary"]["skipped"]["stale_spot"] and out["stale_spot_days"] == []
    assert "stale_spot_days" not in out["records"][0]
    assert "no stale_spots_file" in census(AAA)["summary"]["skipped"]["stale_spot"]


def test_a_stale_row_on_or_after_end_before_or_without_a_date_refuses(tmp_path):
    with pytest.raises(ValueError, match="end_before"):
        census(AAA, **stale_file(tmp_path, ("AAA", "2026-01-02")))
    path = tmp_path / "bad.jsonl"
    path.write_text('{"symbol": "AAA"}\n')
    with pytest.raises(ValueError, match="date"):
        census(AAA, stale_spots_file=str(path))


def test_a_bad_stale_file_param_refuses():
    assert any("stale_spots_file" in p for p in ChainCensus.validate_params({**PARAMS, "stale_spots_file": ""}))
