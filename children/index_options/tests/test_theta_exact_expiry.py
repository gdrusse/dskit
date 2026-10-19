"""ThetaExactExpiryTables (ADR-0270): Theta EOD chain rows -> the exact-expiry machinery's inputs.

Synthetic only. The tree is built by hand in the layout the snapshot export writes, every expected
number comes from a plain-Python reference written below (never read back from the node), and the
consumers are the REAL ones: ``RawChainFeatureBuilder`` and ``ExactExpiryCDFPanel`` read the outputs.
"""

import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

from dskit.pipeline.document import load_document
from dskit.pipeline.libs.chain_census import quote_state
from dskit.pipeline.libs.chain_parquet import ParquetChainRows
from dskit.pipeline.node import NodeContext
from dskit.pipeline.planner import plan

from index_options.datafiles import archive_relpath
from index_options.theta_exact_expiry import (
    ARCHIVE_COLUMNS, ARCHIVE_EXTRA_COLUMNS, BAR_FIELDS, DEFAULT_ATM_BAND, DEFAULT_BAR_PRICE_FIELD,
    DEFAULT_BASIS_TOLERANCE, DEFAULT_FLUSH_ROWS, DEFAULT_MIN_TWO_SIDED_STRIKES,
    DEFAULT_OWN_IV_MAX_GAP_DAYS, DEFAULT_OWN_IV_TARGET_DTE, DEFAULT_WING_DELTA,
    DEFAULT_WING_WIDTH, LIFECYCLE_COLUMNS, LIFECYCLE_EXTRA_COLUMNS, SOURCE_FIELDS, SURFACE_COLUMNS,
    ThetaExactExpiryTables)

CTX = NodeContext(name="t", asof="2023-12-31", run_dir="/nonexistent")
CONFIG = Path(__file__).resolve().parents[1] / "configs" / "run-theta-exact-expiry-tables.json"

# The consumers' own column expectations, restated here on purpose (a suite must not read its
# expectation from the thing it validates): cdf_study.ExactExpiryCDFPanel / RawChainFeatureBuilder.
SURFACE_EXPECTED = [
    "symbol", "quote_date", "expiry", "chain_contracts", "chain_mean_iv", "chain_atm_iv",
    "chain_call25_iv", "chain_put25_iv", "chain_risk_reversal25", "chain_rel_spread",
    "chain_put_call_oi", "chain_put_call_volume", "chain_open_interest", "chain_volume",
    "chain_quote_depth", "chain_underlying_price"]
LIFECYCLE_EXPECTED = ["symbol", "quote_date", "expiry", "first_seen_date"]
LIFECYCLE_EXTRAS = ["first_seen_censored"]
ARCHIVE_EXPECTED = [
    "symbol", "date", "expiration", "strike", "type", "mark", "bid", "ask", "bid_size",
    "ask_size", "open_interest", "volume", "implied_volatility", "delta", "gamma", "vega"]
READER_FIELDS = [
    "symbol", "date", "expiration", "strike", "right", "bid", "ask", "bid_size", "ask_size",
    "open_interest", "volume", "implied_vol", "delta", "gamma", "vega", "underlying_price"]

ARCHIVE_EXTRAS = ["strike_raw", "underlying_price", "basis_factor"]
START, END_BEFORE, MAX_DTE = "2017-02-28", "2024-01-01", 60

# -- the synthetic world -----------------------------------------------------------------------

REL_STRIKE = [0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20]
# |delta| inside [0.20, 0.30]: calls 0.27 and 0.22 (strikes 6, 7), puts 0.24 and 0.28 (strikes 2, 3).
CALL_DELTA = [0.97, 0.88, 0.76, 0.62, 0.50, 0.38, 0.27, 0.22, 0.08]
PUT_DELTA = [-0.03, -0.12, -0.24, -0.28, -0.50, -0.62, -0.76, -0.88, -0.92]
SPOT = {"AAA": 100.0, "BBB": 50.0}
EXPIRIES = {  # name -> (date, first listed on)
    "E0": "2017-03-03", "E1": "2017-03-17", "E2": "2017-04-21", "E3": "2017-06-16",
    "E4": "2018-01-19", "E5": "2018-02-16", "E6": "2024-01-19"}
LISTED = {
    "AAA": {"2017-02-28": ["E1", "E3"], "2017-03-01": ["E0", "E1", "E3"],
            "2017-03-02": ["E0", "E1", "E2", "E3"], "2017-03-03": ["E0", "E1", "E2", "E3"],
            "2017-03-06": ["E1", "E2"], "2017-03-07": ["E1", "E2"],
            "2017-12-29": ["E4", "E5"], "2018-01-02": ["E4", "E5"], "2024-01-03": ["E6"]},
    "BBB": {"2017-02-28": ["E1", "E3"], "2017-03-01": ["E0", "E1", "E3"],
            "2017-03-02": ["E0", "E1", "E2", "E3"], "2017-03-03": ["E1", "E2", "E3"]}}
#: (symbol, date, expiry, strike index, right or None for both) -> quote override
OVERRIDES = {
    ("AAA", "2017-02-28", "E1", 8, "call"): "crossed",
    ("AAA", "2017-02-28", "E1", 0, "put"): "zero",
    ("AAA", "2017-02-28", "E1", 5, "call"): "null",
}
DAY_IDX = {"2017-02-28": 0, "2017-03-01": 1, "2017-03-02": 2, "2017-03-03": 3,
           "2017-12-29": 4, "2018-01-02": 5, "2024-01-03": 6, "2017-03-06": 7, "2017-03-07": 8}
#: AAA's vendor underlying on 03-06 is stale (5% off its close); there is no AAA bar for 03-07.
BASIS_REFUSED = {("AAA", "2017-03-06"): "basis_deviation", ("AAA", "2017-03-07"): "no_bar"}
STALE = 1.05
#: BBB was split 2:1 on 2017-03-03: Theta's rows before it are RAW (2x prices); the bars are adjusted.
SPLIT = {"BBB": ("2017-03-03", 2.0)}


def day(text):
    return dt.date.fromisoformat(text)



def quote(symbol, date, ename, i, right):
    """One synthetic contract row (python types), with the overrides and the dead-day rules applied."""
    e = int(ename[1])
    shift = 0.02 * e + 0.001 * DAY_IDX[date]
    bid = 1.0 + 0.25 * i
    ask = bid + 0.2
    bid_size, ask_size = 5, 7
    kind = OVERRIDES.get((symbol, date, ename, i, right))
    dead_day = symbol == "BBB" and date == "2017-03-02"
    thin = symbol == "AAA" and date == "2017-03-03" and ename == "E2" and i != 4
    if dead_day or thin or kind == "zero":
        bid, ask = (0.0, 0.0) if (dead_day or thin) else (0.0, 0.05)
    elif kind == "crossed":
        bid, ask = 2.0, 1.5
    elif kind == "null":
        bid = None
    spot = SPOT[symbol]
    return {
        "symbol": symbol, "expiration": day(EXPIRIES[ename]), "strike": round(spot * REL_STRIKE[i], 2),
        "right": right, "date": day(date), "bid": bid, "ask": ask, "bid_size": bid_size,
        "ask_size": ask_size, "open_interest": 100 if right == "call" else 300,
        "volume": 10 if right == "call" else 20,
        "implied_vol": round((0.30 if right == "call" else 0.32) - 0.01 * i + shift, 6),
        "delta": CALL_DELTA[i] if right == "call" else PUT_DELTA[i], "gamma": 0.01, "vega": 0.1,
        "underlying_price": spot + 0.5 * DAY_IDX[date]}


def chain_rows(symbol):
    return [quote(symbol, date, ename, i, right)
            for date, enames in LISTED[symbol].items() for ename in enames
            for i in range(len(REL_STRIKE)) for right in ("call", "put")]


def raw_rows(symbol):
    """The rows as the vendor reports them: RAW prices before a split, a stale underlying on one day."""
    rows = []
    for r in chain_rows(symbol):
        date = r["date"].isoformat()
        r = dict(r)
        if symbol in SPLIT and date < SPLIT[symbol][0]:
            k = SPLIT[symbol][1]
            for name in ("strike", "bid", "ask", "underlying_price"):
                if r[name] is not None:
                    r[name] = r[name] * k
            r["gamma"], r["vega"] = r["gamma"] / k, r["vega"] * k      # per-$ greeks follow the price scale
        if (symbol, date) == ("AAA", "2017-03-06"):
            r["underlying_price"] = r["underlying_price"] * STALE
        rows.append(r)
    return rows


def underlying_of(symbol, date):
    return SPOT[symbol] + 0.5 * DAY_IDX[date]


def bars_rows():
    """Split-adjusted daily bars for every XNYS session; on a quote day the close is the true underlying."""
    import exchange_calendars as xc
    rows = []
    for symbol in SPOT:
        for i, d in enumerate(xc.get_calendar("XNYS").sessions_in_range("2016-10-03", "2018-03-01")):
            date = d.strftime("%Y-%m-%d")
            if (symbol, date) in BASIS_REFUSED and BASIS_REFUSED[(symbol, date)] == "no_bar":
                continue
            close = underlying_of(symbol, date) if date in DAY_IDX else SPOT[symbol] + 0.01 * i
            split = SPLIT.get(symbol, (None, 1.0))
            rows.append({"symbol": symbol, "date": date, "open": close * .99, "high": close * 1.01,
                         "low": close * .98, "close": close, "volume": 1000, "dividend_amount": 0.0,
                         "split_coefficient": split[1] if date == split[0] else 1.0})
    return rows


BARS = bars_rows()

SCHEMA = pa.schema([
    ("symbol", pa.string()), ("expiration", pa.date32()), ("strike", pa.float64()),
    ("right", pa.string()), ("date", pa.date32()), ("bid", pa.float64()), ("ask", pa.float64()),
    ("bid_size", pa.int32()), ("ask_size", pa.int32()), ("open_interest", pa.int32()),
    ("volume", pa.int32()), ("implied_vol", pa.float64()), ("delta", pa.float64()),
    ("gamma", pa.float64()), ("vega", pa.float64()), ("underlying_price", pa.float64())])


def write_snapshot(directory, acq_id, rows):
    """One export-shaped file: sorted by symbol then date, the per-symbol windows in the footer."""
    rows = sorted(rows, key=lambda r: (r["symbol"], r["date"], r["expiration"], r["strike"], r["right"]))
    found = {}
    for r in rows:
        lo, hi = found.get(r["symbol"], (r["date"], r["date"]))
        found[r["symbol"]] = (min(lo, r["date"]), max(hi, r["date"]))
    table = pa.Table.from_pylist(rows, schema=SCHEMA).replace_schema_metadata({
        "dskit.export.acq_id": acq_id, "dskit.export.manifest_sha256": "m" * 63 + acq_id[-1],
        "dskit.export.snapshot_hash": "s" * 63 + acq_id[-1],
        "dskit.export.windows": json.dumps({s: [a.isoformat(), b.isoformat()] for s, (a, b) in found.items()}),
        "dskit.export.missing_fields": "{}"})
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, directory / f"{acq_id}.parquet", row_group_size=64)


@pytest.fixture
def tree(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa", raw_rows("AAA"))
    write_snapshot(directory, "20260101T000002Z-backfill-bbbbbbbb", raw_rows("BBB"))
    return directory


def reader(directory, **over):
    params = {"directories": [str(directory)], "fields": READER_FIELDS, "symbol_field": "symbol",
              "date_field": "date", "start": START, "end_before": END_BEFORE}
    params.update(over)
    return ParquetChainRows("chain", {k: v for k, v in params.items() if v is not ...}).run(CTX, {})["records"]


def node_params(out, **over):
    base = {"output_root": str(out), "max_dte": MAX_DTE, "start": START, "end_before": END_BEFORE}
    base.update(over)
    return base


def build(tree, out, reader_over=None, bars=None, **over):
    source = reader(tree, **(reader_over or {}))
    return ThetaExactExpiryTables("tables", node_params(out, **over)).run(
        CTX, {"records": source, "bars": BARS if bars is None else bars})


@pytest.fixture
def built(tree, tmp_path):
    out = tmp_path / "out"
    result = build(tree, out)
    return out, result


def surface_of(out):
    return pd.read_parquet(out / "surface.parquet")


def lifecycle_of(out):
    return pd.read_parquet(out / "lifecycle.parquet")


def archive_of(out, symbol):
    paths = sorted((out / "archive" / symbol.lower()).glob("options_*.parquet"))
    return pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)


# -- an independent plain-Python reference -----------------------------------------------------


def nan_mean(values):
    values = [v for v in values if v is not None and math.isfinite(v)]
    return sum(values) / len(values) if values else math.nan


def reference(symbol, max_dte=MAX_DTE, min_strikes=DEFAULT_MIN_TWO_SIDED_STRIKES):
    """Per kept (date, expiry): the surface values by brute force; plus the refusal lists."""
    by_key, rows = {}, [r for r in chain_rows(symbol) if START <= r["date"].isoformat() < END_BEFORE]
    for r in rows:
        dte = (r["expiration"] - r["date"]).days
        if 1 <= dte <= max_dte and (symbol, r["date"].isoformat()) not in BASIS_REFUSED:
            by_key.setdefault((r["date"].isoformat(), r["expiration"].isoformat()), []).append(r)

    def valid(r):
        return r["bid"] is not None and r["bid"] > 0 and r["ask"] >= r["bid"]

    dates = {}
    for (d, _), group in by_key.items():
        dates[d] = dates.get(d, False) or any(valid(r) for r in group)
    kept, refused_days, few = {}, sorted(d for d, ok in dates.items() if not ok), 0
    for (d, e), group in sorted(by_key.items()):
        strikes = {r["strike"] for r in group if valid(r)}
        if not dates[d]:
            continue
        if len(strikes) < min_strikes:
            few += 1
            continue
        calls = [r for r in group if r["right"] == "call"]
        puts = [r for r in group if r["right"] == "put"]
        und = float(np.median([r["underlying_price"] for r in group]))
        ivs = lambda rs: [r["implied_vol"] for r in rs if r["implied_vol"] > 0]  # noqa: E731
        wing = lambda rs: [r for r in rs if 0.20 <= abs(r["delta"]) <= 0.30]  # noqa: E731
        spreads = [(r["ask"] - r["bid"]) / ((r["ask"] + r["bid"]) / 2) for r in group
                   if r["bid"] is not None and r["ask"] >= r["bid"] and (r["ask"] + r["bid"]) > 0]
        oi_c, oi_p = sum(r["open_interest"] for r in calls), sum(r["open_interest"] for r in puts)
        v_c, v_p = sum(r["volume"] for r in calls), sum(r["volume"] for r in puts)
        c25, p25 = nan_mean(ivs(wing(calls))), nan_mean(ivs(wing(puts)))
        kept[(d, e)] = {
            "chain_contracts": len(group), "chain_mean_iv": nan_mean(ivs(group)),
            "chain_atm_iv": nan_mean(ivs([r for r in group if abs(r["strike"] / und - 1) <= 0.025])),
            "chain_call25_iv": c25, "chain_put25_iv": p25, "chain_risk_reversal25": c25 - p25,
            "chain_rel_spread": nan_mean(spreads),
            "chain_put_call_oi": oi_p / oi_c if oi_c > 0 and oi_p > 0 else math.nan,
            "chain_put_call_volume": v_p / v_c if v_c > 0 and v_p > 0 else math.nan,
            "chain_open_interest": float(oi_c + oi_p), "chain_volume": float(v_c + v_p),
            "chain_quote_depth": float(sum(r["bid_size"] + r["ask_size"] for r in group)),
            "chain_underlying_price": und}
    return kept, refused_days, few, by_key


def assert_same(a, b):
    if isinstance(a, float) and math.isnan(a):
        assert isinstance(b, float) and math.isnan(b)
    else:
        assert b == pytest.approx(a, rel=1e-9, abs=1e-12)


# -- parameters --------------------------------------------------------------------------------


def test_defaults_are_named_once_and_match_the_documented_legacy_definitions():
    assert (DEFAULT_ATM_BAND, DEFAULT_WING_DELTA, DEFAULT_WING_WIDTH) == (0.025, 0.25, 0.05)
    assert (DEFAULT_MIN_TWO_SIDED_STRIKES, DEFAULT_OWN_IV_TARGET_DTE) == (2, 30)
    assert DEFAULT_OWN_IV_MAX_GAP_DAYS >= 1 and DEFAULT_FLUSH_ROWS >= 1
    assert list(SURFACE_COLUMNS) == SURFACE_EXPECTED
    assert list(LIFECYCLE_COLUMNS) == LIFECYCLE_EXPECTED
    assert list(LIFECYCLE_EXTRA_COLUMNS) == LIFECYCLE_EXTRAS
    assert list(ARCHIVE_COLUMNS) == ARCHIVE_EXPECTED
    assert list(SOURCE_FIELDS) == READER_FIELDS
    assert list(ARCHIVE_EXTRA_COLUMNS) == ARCHIVE_EXTRAS
    assert (DEFAULT_BAR_PRICE_FIELD, DEFAULT_BASIS_TOLERANCE) == ("close", 0.01)
    assert "split_coefficient" in BAR_FIELDS


@pytest.mark.parametrize("bad, fragment", [
    ({"surprise": 1}, "surprise"),
    ({"output_root": ""}, "output_root"),
    ({"output_root": 3}, "output_root"),
    ({"max_dte": 0}, "max_dte"),
    ({"max_dte": True}, "max_dte"),
    ({"start": "2017-2-28"}, "start"),
    ({"end_before": "never"}, "end_before"),
    ({"start": "2018-01-01", "end_before": "2017-01-01"}, "before"),
    ({"atm_band": 0}, "atm_band"),
    ({"atm_band": 1.5}, "atm_band"),
    ({"wing_delta": 1.2}, "wing_delta"),
    ({"wing_delta_width": 0.3}, "wing_delta_width"),
    ({"min_two_sided_strikes": 0}, "min_two_sided_strikes"),
    ({"own_iv_target_dte": 0}, "own_iv_target_dte"),
    ({"own_iv_target_dte": 61}, "own_iv_target_dte"),
    ({"own_iv_max_gap_days": -1}, "own_iv_max_gap_days"),
    ({"flush_rows": 0}, "flush_rows"),
    ({"bar_price_field": ""}, "bar_price_field"),
    ({"basis_tolerance": 0}, "basis_tolerance"),
    ({"basis_tolerance": 1.2}, "basis_tolerance"),
    ({"lifecycle_start": "2017-3-1"}, "lifecycle_start"),
    ({"lifecycle_start": "2017-03-01"}, "lifecycle_start"),         # after start
])
def test_validate_params_is_default_deny(tmp_path, bad, fragment):
    problems = ThetaExactExpiryTables.validate_params(node_params(tmp_path / "o", **bad))
    assert problems and any(fragment in p for p in problems), problems


@pytest.mark.parametrize("missing", ["output_root", "max_dte", "start", "end_before"])
def test_required_params_are_required(tmp_path, missing):
    params = node_params(tmp_path / "o")
    del params[missing]
    assert any(missing in p for p in ThetaExactExpiryTables.validate_params(params))


def test_a_complete_param_set_validates(tmp_path):
    full = node_params(tmp_path / "o", atm_band=0.03, wing_delta=0.25, wing_delta_width=0.1,
                       min_two_sided_strikes=3, own_iv_target_dte=45, own_iv_max_gap_days=7,
                       flush_rows=10, bar_price_field="close", basis_tolerance=0.02,
                       lifecycle_start="2017-02-01")
    assert ThetaExactExpiryTables.validate_params(full) == []


# -- the surface table -------------------------------------------------------------------------


def test_surface_schema_is_exactly_the_consumers(built):
    out, _ = built
    surface = surface_of(out)
    assert list(surface.columns) == SURFACE_EXPECTED
    assert surface.symbol.dtype == object and surface.quote_date.dtype == object
    assert surface.chain_contracts.dtype == np.int64
    for name in SURFACE_EXPECTED[4:]:
        assert surface[name].dtype == np.float64, name
    assert not surface.duplicated(["symbol", "quote_date", "expiry"]).any()
    assert surface.equals(surface.sort_values(["symbol", "quote_date", "expiry"]).reset_index(drop=True))


@pytest.mark.parametrize("symbol", ["AAA", "BBB"])
def test_surface_values_equal_the_brute_force_reference(built, symbol):
    out, _ = built
    kept, _, _, _ = reference(symbol)
    surface = surface_of(out)
    got = surface[surface.symbol == symbol].set_index(["quote_date", "expiry"])
    assert set(got.index) == set(kept)
    for key, values in kept.items():
        for name, value in values.items():
            assert_same(float(value), float(got.loc[key, name]))


def test_one_hand_computed_row(built):
    out, _ = built
    row = surface_of(out).set_index(["symbol", "quote_date", "expiry"]).loc[("AAA", "2017-03-01", "2017-03-17")]
    assert row.chain_contracts == 18                      # 9 strikes x 2 rights
    assert row.chain_open_interest == 9 * 100 + 9 * 300   # 3600
    assert row.chain_put_call_oi == pytest.approx(3.0)
    assert row.chain_put_call_volume == pytest.approx(2.0)
    assert row.chain_quote_depth == 18 * 12
    assert row.chain_volume == 9 * 10 + 9 * 20
    assert row.chain_underlying_price == 100.5
    # ATM band 2.5% of 100.5 keeps only strike 100.0: mean of the call (0.30-0.04+0.021) and put (0.32-0.04+0.021).
    assert row.chain_atm_iv == pytest.approx((0.281 + 0.301) / 2)
    # 25-delta: calls 0.27 / 0.22 are strikes 6, 7; puts -0.24 / -0.28 are strikes 2, 3.
    assert row.chain_call25_iv == pytest.approx((0.30 - 0.06 + 0.021 + 0.30 - 0.07 + 0.021) / 2)
    assert row.chain_put25_iv == pytest.approx((0.32 - 0.02 + 0.021 + 0.32 - 0.03 + 0.021) / 2)
    assert row.chain_risk_reversal25 == pytest.approx(row.chain_call25_iv - row.chain_put25_iv)
    assert row.chain_rel_spread == pytest.approx(
        np.mean([0.2 / (1.1 + 0.25 * i) for i in range(9)]))     # ask-bid=0.2, mid = bid+0.1, both rights equal


def test_surface_satisfies_the_consumers_value_rules(built):
    out, _ = built
    from index_options.cdf_study import ExactExpiryCDFPanel
    frame = ExactExpiryCDFPanel.add_surface_features(surface_of(out).copy())   # raises on a violated rule
    assert frame.chain_has_25d_pair.sum() > 0


def test_expiries_outside_the_horizon_are_not_listed(built):
    out, _ = built
    surface = surface_of(out)
    dte = (pd.to_datetime(surface.expiry) - pd.to_datetime(surface.quote_date)).dt.days
    assert dte.between(1, MAX_DTE).all()
    assert "2017-06-16" not in set(surface.expiry)       # E3, 105+ days out
    assert not ((surface.quote_date == "2017-03-03") & (surface.expiry == "2017-03-03")).any()   # dte 0


def test_the_zero_quote_day_and_the_thin_expiry_are_refused_not_emitted(built):
    out, result = built
    surface = surface_of(out)
    assert not ((surface.symbol == "BBB") & (surface.quote_date == "2017-03-02")).any()
    assert not ((surface.symbol == "AAA") & (surface.quote_date == "2017-03-03")
                & (surface.expiry == "2017-04-21")).any()
    assert ((surface.symbol == "AAA") & (surface.quote_date == "2017-03-03")
            & (surface.expiry == "2017-03-17")).any()
    _, refused_days, few, _ = reference("AAA")
    manifest = result["manifest"]
    assert manifest["symbols"]["BBB"]["refused"]["days_no_valid_quotes"] == ["2017-03-02"]
    assert manifest["symbols"]["AAA"]["refused"]["expiries_few_two_sided_strikes"] == few == 1
    assert manifest["symbols"]["AAA"]["refused"]["days_no_valid_quotes"] == refused_days == []


def test_a_stricter_threshold_refuses_more(tree, tmp_path):
    loose = build(tree, tmp_path / "a")
    strict = build(tree, tmp_path / "b", min_two_sided_strikes=10)
    assert len(surface_of(tmp_path / "b")) < len(surface_of(tmp_path / "a"))
    assert strict["manifest"]["symbols"]["AAA"]["refused"]["expiries_few_two_sided_strikes"] > loose[
        "manifest"]["symbols"]["AAA"]["refused"]["expiries_few_two_sided_strikes"]


# -- the lifecycle table -----------------------------------------------------------------------


def test_lifecycle_schema_and_one_to_one_with_the_surface(built):
    out, _ = built
    lifecycle, surface = lifecycle_of(out), surface_of(out)
    assert list(lifecycle.columns) == LIFECYCLE_EXPECTED + LIFECYCLE_EXTRAS
    assert lifecycle.first_seen_censored.dtype == bool
    keys = ["symbol", "quote_date", "expiry"]
    assert not lifecycle.duplicated(keys).any()
    merged = surface.merge(lifecycle, on=keys, validate="one_to_one")     # the consumer's own merge
    assert len(merged) == len(surface) == len(lifecycle)
    assert (lifecycle.first_seen_date <= lifecycle.quote_date).all()


def test_first_seen_is_the_first_listing_date_and_monotone_per_series(built):
    out, _ = built
    lifecycle = lifecycle_of(out)
    one = lifecycle.groupby(["symbol", "expiry"]).first_seen_date.nunique()
    assert (one == 1).all()                               # one first_seen per series, however many quote dates
    first = lifecycle.groupby(["symbol", "expiry"]).first_seen_date.first().to_dict()
    assert first[("AAA", "2017-03-17")] == "2017-02-28"   # E1 listed from the first day
    assert first[("AAA", "2017-03-03")] == "2017-03-01"   # E0 appears on day 2
    assert first[("AAA", "2017-04-21")] == "2017-03-02"   # E2 appears on day 3
    assert first[("AAA", "2018-01-19")] == "2017-12-29"
    # the series' first_seen is no later than its earliest quote date, and sorted quote dates never precede it
    earliest = lifecycle.groupby(["symbol", "expiry"]).quote_date.min()
    for key, seen in first.items():
        assert seen <= earliest[key]


def test_first_seen_counts_listings_beyond_the_horizon_and_refused_days(built):
    out, _ = built
    lifecycle = lifecycle_of(out)
    # BBB's 2017-03-02 is a refused day, but 2017-03-02 is still E2's first listing for BBB.
    row = lifecycle[(lifecycle.symbol == "BBB") & (lifecycle.expiry == "2017-04-21")]
    assert set(row.first_seen_date) == {"2017-03-02"}


def test_left_censoring_is_counted(built):
    _, result = built
    # AAA's data begins 2017-02-28: E1 (and E3, out of range) are first seen on that first day.
    assert result["manifest"]["symbols"]["AAA"]["left_censored_expiries"] == 1


# -- the chain archive -------------------------------------------------------------------------


def test_archive_layout_and_columns_match_the_consumers(built):
    out, _ = built
    for symbol, years in (("AAA", (2017, 2018)), ("BBB", (2017,))):
        for year in years:
            assert (out / "archive" / archive_relpath(symbol, year)).is_file()
    assert not (out / "archive" / archive_relpath("AAA", 2024)).exists()
    for path in (out / "archive").rglob("*.parquet"):
        assert pq.ParquetFile(path).schema_arrow.names == ARCHIVE_EXPECTED + ARCHIVE_EXTRAS
    schema = pq.ParquetFile(out / "archive" / archive_relpath("AAA", 2017)).schema_arrow
    assert schema.field("date").type == pa.string() and schema.field("expiration").type == pa.string()
    assert schema.field("type").type == pa.string()
    assert schema.field("strike").type == pa.float64() and schema.field("bid_size").type == pa.int64()


def test_archive_rows_map_fields_and_derive_mark_from_valid_quotes_only(built):
    out, _ = built
    frame = archive_of(out, "AAA")
    assert set(frame.type) == {"call", "put"}
    day1 = frame[(frame.date == "2017-02-28") & (frame.expiration == "2017-03-17")]
    assert len(day1) == 18                                        # every row kept: provenance
    valid = (day1.bid > 0) & (day1.ask >= day1.bid)
    assert np.allclose(day1.loc[valid, "mark"], (day1.bid + day1.ask)[valid] / 2)
    bad = day1[~valid]
    assert len(bad) == 3 and bad.mark.isna().all()                 # crossed, zero-bid and null-bid rows
    assert (day1.implied_volatility > 0).all()
    assert set(frame.date) <= {"2017-02-28", "2017-03-01", "2017-03-02", "2017-03-03", "2017-12-29",
                               "2018-01-02"}
    assert frame.date.str[:4].astype(int).between(2017, 2018).all()


def test_archive_holds_exactly_the_rows_of_the_emitted_expiries(built):
    out, _ = built
    surface = surface_of(out)
    for symbol in ("AAA", "BBB"):
        frame = archive_of(out, symbol)
        emitted = set(zip(surface[surface.symbol == symbol].quote_date,
                          surface[surface.symbol == symbol].expiry))
        assert set(zip(frame.date, frame.expiration)) == emitted
        counts = frame.groupby(["date", "expiration"]).size()
        contracts = surface[surface.symbol == symbol].set_index(["quote_date", "expiry"]).chain_contracts
        assert (counts.values == contracts.reindex(counts.index).values).all()


def test_the_raw_chain_feature_builder_consumes_the_archive(built, tmp_path):
    from index_options.cdf_study import RawChainFeatureBuilder
    out, _ = built
    meta = surface_of(out)
    builder = RawChainFeatureBuilder(nodes=5, moneyness_bounds=[-.25, .25], max_node_gap=.1,
                                     proxy_probabilities=[.1, .25, .5, .75, .9], min_wing_nodes=2,
                                     max_inner_gap=.15, max_outer_gap=.3)
    features = builder.build_archive(meta, str(out / "archive"), tmp_path / "features.parquet")
    assert len(features) == len(meta)
    assert not features.duplicated(["symbol", "quote_date", "expiry"]).any()
    masks = [c for c in features if c.endswith("_mask")]
    assert features[masks].to_numpy().sum() > 0                    # nodes found real contracts
    assert features.rn_proxy_eligible.sum() > 0                    # the risk-neutral proxy was solvable
    usable = RawChainFeatureBuilder._usable_quotes(archive_of(out, "AAA").query(
        "date == '2017-02-28' and expiration == '2017-03-17'"))
    assert len(usable) == 15                                        # 18 less crossed, zero-bid and null rows


def test_the_decision_region_reader_columns_resolve_from_the_archive_root(built):
    from index_options.cdf_study import DecisionRegionContextBuilder
    from index_options.datafiles import DataFiles
    out, _ = built
    tree = DataFiles().tree(str(out / "archive"))
    path = tree.path(archive_relpath("AAA", 2017))
    batch = next(pq.ParquetFile(path).iter_batches(columns=list(DecisionRegionContextBuilder.COLUMNS),
                                                    batch_size=10))
    assert batch.num_rows == 10 and batch.schema.names == list(DecisionRegionContextBuilder.COLUMNS)


# -- own_iv ------------------------------------------------------------------------------------


def test_own_iv_is_the_atm_iv_of_the_expiry_nearest_thirty_days(built):
    out, _ = built
    surface = surface_of(out)
    for symbol in ("AAA", "BBB"):
        series = pd.read_parquet(out / "own_iv" / "index_daily" / f"{symbol}.parquet")
        assert list(series.columns[:3]) == ["symbol", "date", "close"]
        assert (series.symbol == symbol).all() and series.date.is_unique and series.date.is_monotonic_increasing
        assert (series.close > 0).all() and np.isfinite(series.close).all()
        mine = surface[surface.symbol == symbol].copy()
        mine["dte"] = (pd.to_datetime(mine.expiry) - pd.to_datetime(mine.quote_date)).dt.days
        for row in series.itertuples():
            today = mine[(mine.quote_date == row.date) & mine.chain_atm_iv.notna()]
            gap = (today.dte - DEFAULT_OWN_IV_TARGET_DTE).abs()
            chosen = today.loc[gap.sort_values(kind="mergesort").index[0]] if len(today) else None
            assert chosen is not None
            assert row.close == pytest.approx(100 * chosen.chain_atm_iv) and row.source_expiry == chosen.expiry
            assert row.source_dte == chosen.dte
    aaa = pd.read_parquet(out / "own_iv" / "index_daily" / "AAA.parquet")
    # VIX-style POINTS like every existing own_iv consumer (bands at 20 and 30): 2017-03-01's ATM IV 0.291 is 29.1
    assert aaa.loc[aaa.date == "2017-03-01", "close"].item() == pytest.approx(29.1)
    # 2017-02-28: only E1 (17 dte) is in range and E3 is beyond; the nearest to 30 is E1.
    assert aaa.loc[aaa.date == "2017-02-28", "source_expiry"].item() == "2017-03-17"
    # 2017-03-02: E1 (15), E2 (50) in range: nearest 30 is E1 (gap 15) over E2 (gap 20).
    assert aaa.loc[aaa.date == "2017-03-02", "source_expiry"].item() == "2017-03-17"
    assert "2017-03-02" not in set(pd.read_parquet(out / "own_iv" / "index_daily" / "BBB.parquet").date)


def test_own_iv_gap_limit_drops_days_without_a_near_expiry(tree, tmp_path):
    result = build(tree, tmp_path / "o", own_iv_max_gap_days=2)
    series = pd.read_parquet(tmp_path / "o" / "own_iv" / "index_daily" / "AAA.parquet")
    assert series.empty or (series.source_dte - 30).abs().max() <= 2
    assert result["manifest"]["symbols"]["AAA"]["own_iv"]["days_beyond_gap"] > 0


def test_index_close_rows_serves_the_series_through_a_localtables_store(built, tmp_path):
    from dskit.onboarding import OnboardingRoot, run_acquisition
    from index_options.observations import IndexCloseRows
    out, _ = built
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": "own-iv", "catalog_source": "own-iv-src", "connector": "localtables",
        "config": {"path": str(out / "own_iv"), "layout": "directory", "effective_field": "date",
                   "stamp_stem_as": "symbol"}}, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(root, registry, "own-iv", "index_daily", "backfill")
    records = IndexCloseRows("iv", {"root": root.root, "source": "own-iv", "symbol": "AAA"}).run(None, {})["records"]
    series = pd.read_parquet(out / "own_iv" / "index_daily" / "AAA.parquet")
    assert [r["date"] for r in records] == series.date.tolist()
    assert [r["close"] for r in records] == pytest.approx(series.close.tolist())


# -- the real panel consumer -------------------------------------------------------------------


def onboard(root, registry, name, directory):
    """Acquire a localtables directory layout as an ``index_daily`` stream (what iv_source / price_source name)."""
    from dskit.onboarding import run_acquisition
    vid = registry.register("source_config", {
        "name": name, "catalog_source": f"{name}-src", "connector": "localtables",
        "config": {"path": str(directory), "layout": "directory", "effective_field": "date",
                   "stamp_stem_as": "symbol"}}, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(root, registry, name, "index_daily", "backfill")


def test_exact_expiry_panel_reads_the_outputs_through_the_real_readers(built, tmp_path):
    """No patched reader: price_source and iv_source are localtables acquisitions of prices/ and own_iv/."""
    from dskit.onboarding import OnboardingRoot
    from index_options.cdf_study import ExactExpiryCDFPanel
    out, _ = built
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = root.registry()
    onboard(root, registry, "px", out / "prices")
    onboard(root, registry, "iv", out / "own_iv")
    surface = surface_of(out)
    panel = ExactExpiryCDFPanel({
        "root": root.root, "surface": str(out / "surface.parquet"), "lifecycle": str(out / "lifecycle.parquet"),
        "symbols": {"AAA": "AAA", "BBB": "BBB"}, "price_source": "px", "iv_source": "iv",
        "since": "2016-10-01", "max_dte": MAX_DTE, "lags": 22, "windows": [1, 5, 22, 66],
        "feature_gap_days": 7, "reference_floor": .001, "spot_tolerance": .02, "surface_features": True,
        "dividend_field": "dividend_amount"})
    frame = panel.read()          # _spot_check would raise if the adjusted basis were wrong
    keys = set(zip(frame.symbol, frame.quote_date, frame.expiry))
    expected = set(zip(surface.symbol, surface.quote_date, surface.expiry))
    assert keys and keys <= expected                         # only settled paths survive; none invented
    assert {"AAA", "BBB"} == set(frame.symbol)
    # BBB straddles its split: rows before 2017-03-03 were RAW in Theta, adjusted here, and priced the same
    assert (frame[frame.symbol == "BBB"].quote_date < "2017-03-03").any()
    for symbol in ("AAA", "BBB"):
        own = pd.read_parquet(out / "own_iv" / "index_daily" / f"{symbol}.parquet")
        mapped = dict(zip(own.date, own.close))
        mine = frame[frame.symbol == symbol]
        # a day with no near-30-day expiry has no own_iv row; the panel marks it missing (never imputes)
        assert (mine.own_iv.notna() == mine.quote_date.isin(mapped)).all() and mine.own_iv.notna().any()
        assert all(v == pytest.approx(mapped[d]) for d, v in zip(mine.quote_date, mine.own_iv) if d in mapped)
    assert (frame.spot / frame.chain_underlying_price - 1).abs().max() < 1e-12
    assert frame.chain_log_atm_iv.notna().all() and (frame.series_age_calendar >= 0).all()


# -- the price basis --------------------------------------------------------------------------


def test_split_names_are_adjusted_to_the_bars_basis(built):
    out, result = built
    frame = archive_of(out, "BBB")
    truth = pd.DataFrame([r for r in chain_rows("BBB") if r["right"] in ("call", "put")])
    truth["date"] = truth["date"].map(lambda d: d.isoformat())
    truth["expiration"] = truth["expiration"].map(lambda d: d.isoformat())
    merged = frame.merge(truth, left_on=["date", "expiration", "type", "strike"],
                         right_on=["date", "expiration", "right", "strike"], suffixes=("", "_true"))
    assert len(merged) == len(frame)                          # every adjusted strike is a TRUE strike
    pre = frame.date < "2017-03-03"
    assert pre.any() and (~pre).any()
    assert (frame.loc[pre, "basis_factor"] == 0.5).all() and (frame.loc[~pre, "basis_factor"] == 1.0).all()
    assert (frame.strike_raw[pre] == 2 * frame.strike[pre]).all()
    assert (frame.strike_raw[~pre] == frame.strike[~pre]).all()
    assert np.allclose(frame.underlying_price, [underlying_of("BBB", d) for d in frame.date])
    valid = frame.mark.notna()
    assert np.allclose(frame.loc[valid, "bid"], merged.loc[valid.values, "bid_true"])   # bid/ask scaled back
    # unscaled: sizes, open interest, volume, IV and the greeks are the vendor's
    assert (frame.open_interest.isin([100, 300])).all() and (frame.bid_size == 5).all()
    assert set(frame.delta) <= set(CALL_DELTA) | set(PUT_DELTA)
    basis = result["manifest"]["symbols"]["BBB"]["basis"]
    assert [(g["factor"], g["snapped"]) for g in basis["segments"]] == [(0.5, True), (1.0, True)]
    assert basis["price_field"] == "close" and basis["tolerance"] == 0.01
    assert set(basis["factors"].values()) == {0.5, 1.0}
    assert all(f == (0.5 if d < "2017-03-03" else 1.0) for d, f in basis["factors"].items())
    assert set(basis["factors"]) == {"2017-02-28", "2017-03-01", "2017-03-02", "2017-03-03"}


def test_a_stale_underlying_day_and_a_day_without_a_bar_are_refused(built):
    out, result = built
    refused = result["manifest"]["symbols"]["AAA"]["refused"]
    assert refused["days_basis_deviation"] == ["2017-03-06"]
    assert refused["days_no_bar"] == ["2017-03-07"]
    assert refused["days_no_underlying"] == []
    surface = surface_of(out)
    assert not surface[surface.symbol == "AAA"].quote_date.isin(["2017-03-06", "2017-03-07"]).any()
    assert not archive_of(out, "AAA").date.isin(["2017-03-06", "2017-03-07"]).any()
    # their listings still count toward first_seen, and their rows are counted refused, not lost
    info = result["manifest"]["symbols"]["AAA"]
    assert info["rows_read"] == (info["rows_before_start"] + info["rows_outside_dte"]
                                 + info["rows_refused"] + info["rows_archived"])
    assert info["rows_refused"] >= 2 * 2 * 2 * len(REL_STRIKE)


def test_a_wider_tolerance_accepts_the_stale_day_at_the_segment_factor(tree, tmp_path):
    result = build(tree, tmp_path / "o", basis_tolerance=0.06)
    assert result["manifest"]["symbols"]["AAA"]["refused"]["days_basis_deviation"] == []
    assert "2017-03-06" in set(surface_of(tmp_path / "o").quote_date)


def test_the_adjusted_close_is_a_declared_not_default_basis(tree, tmp_path):
    bars = [dict(r, adjusted_close=r["close"] * 0.9) for r in BARS]
    result = build(tree, tmp_path / "o", bars=bars, bar_price_field="adjusted_close", basis_tolerance=0.2)
    basis = result["manifest"]["symbols"]["AAA"]["basis"]
    assert basis["price_field"] == "adjusted_close" and basis["segments"][0]["factor"] == pytest.approx(0.9)
    series = pd.read_parquet(tmp_path / "o" / "prices" / "index_daily" / "AAA.parquet")
    assert "open" not in series                        # the OHLC of another basis is never mixed in
    assert series.close.iloc[0] == pytest.approx(bars[0]["close"] * 0.9)


def test_price_shards_are_the_adjusted_bars_without_a_split_column(built):
    out, result = built
    for symbol in ("AAA", "BBB"):
        series = pd.read_parquet(out / "prices" / "index_daily" / f"{symbol}.parquet")
        mine = [r for r in BARS if r["symbol"] == symbol]
        assert series.date.tolist() == sorted(r["date"] for r in mine)
        assert series.columns.tolist()[:3] == ["symbol", "date", "close"]
        assert "split_coefficient" not in series
        assert set(series.columns) <= {"symbol", "date", "close", "open", "high", "low", "volume",
                                       "dividend_amount"}
        assert series.close.tolist() == pytest.approx([r["close"] for r in sorted(mine, key=lambda r: r["date"])])
        assert result["manifest"]["symbols"][symbol]["prices_rows"] == len(series)
    assert (out / "prices" / "index_daily" / "BBB.parquet").exists() and not (
        out / "prices" / "index_daily" / "CCC.parquet").exists()
    assert result["manifest"]["bars"]["symbols"] == 2 and result["manifest"]["bars"]["symbols_ignored"] == 0


def test_the_bars_input_is_validated(tree, tmp_path):
    for number, bad in enumerate((None, 0, -3.0, float('nan'))):
        with pytest.raises(ValueError, match='close'):
            build(tree, tmp_path / f'bad{number}', bars=[dict(BARS[0], close=bad)] + BARS[1:])
    with pytest.raises(ValueError, match="split_coefficient"):
        build(tree, tmp_path / "a", bars=[{k: v for k, v in BARS[0].items() if k != "split_coefficient"}])
    with pytest.raises(ValueError, match="duplicate"):
        build(tree, tmp_path / "b", bars=[BARS[0], BARS[0]])
    with pytest.raises(ValueError, match="end_before"):
        build(tree, tmp_path / "c", bars=[dict(BARS[0], date="2024-01-02")])
    with pytest.raises(ValueError, match="nope"):
        build(tree, tmp_path / "d", bar_price_field="nope")
    assert not any((tmp_path / n).exists() for n in "abcd")
    assert not any((tmp_path / f"bad{n}").exists() for n in range(4))


# -- review round 1 (Lens 1): exact snapping, greek basis, censored listings, intake, cohort -----


@pytest.mark.parametrize("scale, near", [(0.9267, 0.9444), (0.50505, 0.5)])
def test_a_factor_that_is_not_an_exact_split_fraction_is_never_snapped(tree, tmp_path, scale, near):
    bars = [dict(r, close=r["close"] * scale) if r["symbol"] == "AAA" else r for r in BARS]
    result = build(tree, tmp_path / "o", bars=bars)
    segment = result["manifest"]["symbols"]["AAA"]["basis"]["segments"][0]
    assert segment["factor"] == pytest.approx(scale, rel=1e-12) and segment["snapped"] is False
    assert abs(segment["factor"] / near - 1) > 1e-3 and segment["residual"] == 0.0
    archive = archive_of(tmp_path / "o", "AAA")
    assert np.allclose(archive.basis_factor, scale, rtol=1e-12, atol=0)


def test_true_splits_still_snap_and_report_a_residual(built):
    _, result = built
    for symbol, factors in (("BBB", [0.5, 1.0]), ("AAA", [1.0])):
        segments = result["manifest"]["symbols"][symbol]["basis"]["segments"]
        assert [g["factor"] for g in segments] == factors and all(g["snapped"] for g in segments)
        assert all(g["residual"] <= 1e-6 for g in segments)


def test_gamma_and_vega_follow_the_price_basis_so_exposures_have_no_level_shift_at_a_split(built, tmp_path):
    from index_options.cdf_study import RawChainFeatureBuilder
    out, _ = built
    frame = archive_of(out, "BBB")
    assert np.allclose(frame.gamma, 0.01) and np.allclose(frame.vega, 0.1)
    assert (frame.delta.abs() <= 1).all()                      # delta and IV are scale free: untouched
    meta = surface_of(out)
    meta = meta[meta.symbol == "BBB"]
    builder = RawChainFeatureBuilder(nodes=5, moneyness_bounds=[-.25, .25], max_node_gap=.1,
                                     proxy_probabilities=[.1, .25, .5, .75, .9], min_wing_nodes=2,
                                     max_inner_gap=.15, max_outer_gap=.3)
    features = builder.transform(frame, meta).set_index(["quote_date", "expiry"])
    before, after = features.loc[("2017-03-01", "2017-03-17")], features.loc[("2017-03-03", "2017-03-17")]
    for name in ("chain_log_gamma_oi", "chain_log_vega_oi", "chain_log_delta_oi"):
        assert np.isfinite(before[name]) and before[name] == pytest.approx(after[name]), name


def test_the_consumer_reads_a_lifecycle_with_the_censoring_flag(built, tmp_path):
    """The extra boolean rides through _meta_rows' one_to_one merge; first_seen_date keeps a real date."""
    from index_options.cdf_study import ExactExpiryCDFPanel
    out, _ = built
    panel = ExactExpiryCDFPanel({"root": "x", "surface": str(out / "surface.parquet"),
                                 "lifecycle": str(out / "lifecycle.parquet"), "symbols": {"AAA": "AAA"},
                                 "price_source": "p", "iv_source": "i", "since": "2016-10-01", "max_dte": MAX_DTE,
                                 "lags": 22, "windows": [1, 5, 22, 66], "feature_gap_days": 7,
                                 "reference_floor": .001, "spot_tolerance": .02})
    from index_options.datafiles import DataFiles
    panel.conventions = panel._resolve_conventions()
    meta = panel._meta_rows(DataFiles("x"))
    assert "first_seen_censored" in meta and meta.first_seen_date.notna().all()


def test_listings_are_scanned_from_lifecycle_start_before_the_window(tree, tmp_path):
    result = build(tree, tmp_path / "o", reader_over={"start": "2017-02-28"}, start="2017-03-01",
                   lifecycle_start="2017-02-28")
    surface, lifecycle = surface_of(tmp_path / "o"), lifecycle_of(tmp_path / "o")
    assert surface.quote_date.min() == "2017-03-01"                  # nothing before start is emitted
    assert archive_of(tmp_path / "o", "AAA").date.min() == "2017-03-01"
    row = lifecycle[(lifecycle.symbol == "AAA") & (lifecycle.expiry == "2017-03-17")].iloc[0]
    assert row.first_seen_date == "2017-02-28" and bool(row.first_seen_censored)     # listed before start
    e0 = lifecycle[(lifecycle.symbol == "AAA") & (lifecycle.expiry == "2017-03-03")].iloc[0]
    assert e0.first_seen_date == "2017-03-01" and not bool(e0.first_seen_censored)   # a real first listing
    info = result["manifest"]["symbols"]["AAA"]
    assert info["rows_before_start"] > 0 and info["first_scanned_date"] == "2017-02-28"
    assert info["left_censored_expiries"] == 1
    assert info["rows_read"] == (info["rows_before_start"] + info["rows_outside_dte"]
                                 + info["rows_refused"] + info["rows_archived"])


def test_censoring_flags_follow_the_first_scanned_date(built):
    out, _ = built
    lifecycle = lifecycle_of(out)
    flagged = lifecycle[lifecycle.first_seen_censored]
    assert set(flagged.expiry) == {"2017-03-17"} and set(flagged.symbol) == {"AAA", "BBB"}
    assert (flagged.first_seen_date == "2017-02-28").all()


def test_the_reader_start_must_equal_lifecycle_start(tree, tmp_path):
    with pytest.raises(ValueError, match="lifecycle_start"):
        build(tree, tmp_path / "o", reader_over={"start": "2017-03-01"}, start="2017-03-01",
              lifecycle_start="2017-02-28")                       # the reader skips the scan-only days


def test_a_chain_symbol_without_bars_is_refused_by_name_and_skipped(tree, tmp_path):
    result = build(tree, tmp_path / "o", bars=[r for r in BARS if r["symbol"] != "BBB"])
    assert result["manifest"]["refused_symbols"] == {"BBB": "no_bar_rows"}
    assert result["summary"]["refused_symbols"] == ["BBB"]
    assert set(surface_of(tmp_path / "o").symbol) == {"AAA"} and set(lifecycle_of(tmp_path / "o").symbol) == {"AAA"}
    assert "BBB" not in result["manifest"]["symbols"]
    assert not (tmp_path / "o" / "archive" / "bbb").exists()
    assert not (tmp_path / "o" / "own_iv" / "index_daily" / "BBB.parquet").exists()


def test_bars_of_symbols_without_a_chain_are_ignored_and_not_held(tree, tmp_path):
    extra = [dict(r, symbol="ZZZ") for r in BARS if r["symbol"] == "AAA"]
    result = build(tree, tmp_path / "o", bars=BARS + extra)
    assert result["manifest"]["bars"]["symbols_ignored"] == 1
    assert not (tmp_path / "o" / "prices" / "index_daily" / "ZZZ.parquet").exists()


def test_rows_the_consumer_will_drop_are_counted(built):
    out, result = built
    for symbol in ("AAA", "BBB"):
        frame = archive_of(out, symbol)
        assert result["manifest"]["symbols"][symbol]["archive_rows_unusable"] == int(frame.mark.isna().sum())


# -- review round 2 (Lens A) ---------------------------------------------------------------------


def test_ohlc_is_all_or_none_per_symbol_and_the_shard_reads_through_index_close_rows(tree, tmp_path):
    from dskit.onboarding import OnboardingRoot
    from index_options.observations import IndexCloseRows
    bars = [dict(r) for r in BARS]
    aaa = [r for r in bars if r["symbol"] == "AAA"]
    aaa[10]["high"] = None                                     # ONE null high on ONE row
    result = build(tree, tmp_path / "o", bars=bars)
    shard = lambda sym: pd.read_parquet(tmp_path / "o" / "prices" / "index_daily" / f"{sym}.parquet")  # noqa: E731
    assert not {"open", "high", "low"} & set(shard("AAA").columns)         # all three dropped, not just high
    assert {"open", "high", "low"} <= set(shard("BBB").columns)
    info = result["manifest"]["symbols"]
    assert info["AAA"]["prices_ohlc"] == "dropped_incomplete" and info["BBB"]["prices_ohlc"] == "kept"
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = root.registry()
    onboard(root, registry, "px", tmp_path / "o" / "prices")
    for symbol in ("AAA", "BBB"):                                          # the real reader accepts both shards
        rows = IndexCloseRows("p", {"root": root.root, "source": "px", "symbol": symbol}).run(None, {})["records"]
        assert len(rows) == len(shard(symbol))
        assert ("open" in rows[0]) == (symbol == "BBB")


def test_ohlc_with_a_non_positive_value_is_dropped_too(tree, tmp_path):
    bars = [dict(r, low=0.0) if (r["symbol"] == "BBB" and r["date"] == "2017-01-04") else r for r in BARS]
    result = build(tree, tmp_path / "o", bars=bars)
    assert result["manifest"]["symbols"]["BBB"]["prices_ohlc"] == "dropped_incomplete"


def test_a_factor_beyond_denominator_twenty_snaps_when_exact(tree, tmp_path):
    scale = 50 / 99
    bars = [dict(r, close=r["close"] * scale) if r["symbol"] == "AAA" else r for r in BARS]
    segment = build(tree, tmp_path / "o", bars=bars)["manifest"]["symbols"]["AAA"]["basis"]["segments"][0]
    assert segment["snapped"] is True and segment["factor"] == scale


def test_symbols_equal_ignoring_case_are_refused(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa", chain_rows("AAA"))
    write_snapshot(directory, "20260101T000002Z-backfill-bbbbbbbb", [dict(r, symbol="aaa") for r in chain_rows("AAA")])
    with pytest.raises(ValueError, match="case"):
        build(directory, tmp_path / "o")
    assert not (tmp_path / "o").exists()


# -- provenance, determinism, refusals ---------------------------------------------------------


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_manifest_lists_every_file_with_its_sha256_and_counts(built):
    out, result = built
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest == json.loads(json.dumps(result["manifest"]))
    on_disk = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()} - {"manifest.json", "run.json"}
    run = json.loads((out / "run.json").read_text())
    assert run["output_root"] == str(out) and run["seconds"] >= 0 and "run" not in manifest
    assert manifest["own_iv_unit"] == "vix_points" and "output_root" not in manifest["params"]
    assert set(manifest["files"]) == on_disk
    for rel, entry in manifest["files"].items():
        assert entry["sha256"] == sha(out / rel)
        assert entry["bytes"] == (out / rel).stat().st_size
        assert entry["rows"] == pq.read_metadata(out / rel).num_rows
    surface = surface_of(out)
    for symbol, info in manifest["symbols"].items():
        by_year = info["by_year"]
        assert sum(v["surface_rows"] for v in by_year.values()) == int((surface.symbol == symbol).sum())
        for year, counts in by_year.items():
            path = out / "archive" / archive_relpath(symbol, year)
            assert counts["archive_rows"] == pq.read_metadata(path).num_rows
        assert info["rows_read"] == (info["rows_before_start"] + info["rows_outside_dte"]
                                     + info["rows_refused"] + info["rows_archived"])
    assert manifest["definitions"] and manifest["source"]["identity"]["end_before"] == END_BEFORE
    assert manifest["symbols"]["AAA"]["contributing_files"]


def test_the_outputs_are_reproducible_byte_for_byte(tree, tmp_path):
    a = build(tree, tmp_path / "a")["manifest"]["files"]
    b = build(tree, tmp_path / "b")["manifest"]["files"]
    assert a == b
    assert (tmp_path / "a" / "manifest.json").read_bytes() == (tmp_path / "b" / "manifest.json").read_bytes()


def test_chunk_boundaries_do_not_change_the_outputs(tree, tmp_path):
    big = build(tree, tmp_path / "big")
    source = reader(tree, batch_size=7)
    small = ThetaExactExpiryTables("tables", node_params(tmp_path / "small", flush_rows=5)).run(
        CTX, {"records": source, "bars": BARS})
    for name in ("surface", "lifecycle"):
        pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "big" / f"{name}.parquet"),
                                      pd.read_parquet(tmp_path / "small" / f"{name}.parquet"))
    for symbol in ("AAA", "BBB"):
        pd.testing.assert_frame_equal(archive_of(tmp_path / "big", symbol), archive_of(tmp_path / "small", symbol))
        pd.testing.assert_frame_equal(
            pd.read_parquet(tmp_path / "big" / "own_iv" / "index_daily" / f"{symbol}.parquet"),
            pd.read_parquet(tmp_path / "small" / "own_iv" / "index_daily" / f"{symbol}.parquet"))
    assert big["manifest"]["symbols"]["AAA"]["rows_read"] == small["manifest"]["symbols"]["AAA"]["rows_read"]


def test_an_existing_output_is_refused_and_left_untouched(tree, tmp_path):
    out = tmp_path / "out"
    build(tree, out)
    before = {p.relative_to(out).as_posix(): sha(p) for p in out.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="exists"):
        build(tree, out)
    after = {p.relative_to(out).as_posix(): sha(p) for p in out.rglob("*") if p.is_file()}
    assert before == after
    assert not (tmp_path / ".out.partial").exists()


def test_a_stale_partial_directory_is_refused(tree, tmp_path):
    (tmp_path / ".out.partial").mkdir()
    with pytest.raises(ValueError, match="partial"):
        build(tree, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_a_failed_run_leaves_neither_output_nor_partial(tmp_path):
    directory = tmp_path / "theta"
    rows = chain_rows("AAA")
    rows[3]["right"] = "straddle"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa", rows)
    with pytest.raises(ValueError, match="right"):
        build(directory, tmp_path / "out")
    assert not (tmp_path / "out").exists() and not (tmp_path / ".out.partial").exists()


def test_symbols_that_cannot_be_a_path_segment_are_refused(tmp_path):
    directory = tmp_path / "theta"
    rows = [dict(r, symbol="A/B") for r in chain_rows("AAA")]
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa", rows)
    with pytest.raises(ValueError, match="symbol"):
        build(directory, tmp_path / "out")
    assert not (tmp_path / "out").exists()


# -- the sealed period -------------------------------------------------------------------------


def test_a_row_dated_2024_is_never_read_or_emitted(built):
    out, result = built
    assert chain_rows("AAA")[-1]["date"].isoformat() >= "2024-01-01"       # the world does hold one
    for frame in (surface_of(out).quote_date, lifecycle_of(out).quote_date, archive_of(out, "AAA").date,
                  pd.read_parquet(out / "own_iv" / "index_daily" / "AAA.parquet").date):
        assert frame.max() < END_BEFORE
    # the reader's pushed-down bound kept the row out of the scan entirely
    total = len([r for r in chain_rows("AAA") if r["date"].isoformat() < END_BEFORE])
    assert result["manifest"]["symbols"]["AAA"]["rows_read"] == total


def test_the_reader_refuses_a_file_that_reaches_the_sealed_date(tree):
    with pytest.raises(ValueError, match="sealed"):
        reader(tree, sealed_from=END_BEFORE)


def test_node_bounds_must_equal_the_readers(tree, tmp_path):
    with pytest.raises(ValueError, match="end_before"):
        build(tree, tmp_path / "a", reader_over={"end_before": "2025-01-01"})
    with pytest.raises(ValueError, match="start"):
        build(tree, tmp_path / "b", reader_over={"start": ...})
    with pytest.raises(ValueError, match="start"):
        build(tree, tmp_path / "c", reader_over={"start": "2017-03-01"})
    assert not any((tmp_path / n).exists() for n in "abc")


class FakeSource:
    """A source with the ChainRowSource surface whose rows break the contract (a late date)."""

    def __init__(self, table, end_before=END_BEFORE, start=START):
        self.table, self._id = table, {"end_before": end_before, "start": start, "renames": "x"}

    def symbols(self):
        return ["AAA"]

    def select(self, symbols):
        return self

    def identity(self):
        return self._id

    def contributing(self, symbol):
        return []

    def report(self):
        return {"rows": self.table.num_rows}

    def batches(self):
        yield self.table


def test_a_row_at_or_after_end_before_from_any_source_is_refused(tmp_path):
    rows = [r for r in chain_rows("AAA") if r["date"].isoformat() >= "2024-01-01"]
    table = pa.Table.from_pylist(rows, schema=SCHEMA).select(READER_FIELDS)
    table = table.set_column(1, "date", pc.cast(table.column("date"), pa.string()))
    table = table.set_column(2, "expiration", pc.cast(table.column("expiration"), pa.string()))
    with pytest.raises(ValueError, match="end_before"):
        ThetaExactExpiryTables("tables", node_params(tmp_path / "o")).run(
            CTX, {"records": FakeSource(table), "bars": BARS})
    assert not (tmp_path / "o").exists()


def test_missing_reader_fields_are_named(tmp_path, tree):
    source = reader(tree, fields=["symbol", "date", "expiration", "strike", "right", "bid", "ask"])
    with pytest.raises(ValueError, match="underlying_price"):
        ThetaExactExpiryTables("tables", node_params(tmp_path / "o")).run(
            CTX, {"records": source, "bars": BARS})
    assert not (tmp_path / "o").exists()


# -- one rule, two owners ----------------------------------------------------------------------


@pytest.mark.parametrize("bid, ask", [(1.0, 1.2), (1.0, 1.0), (0.0, 0.05), (0.0, 0.0), (2.0, 1.5),
                                      (None, 1.0), (1.0, None), (float("nan"), 1.0), (-1.0, 1.0)])
def test_the_validity_rule_agrees_with_the_census_quote_state(bid, ask):
    """valid = bid > 0 and ask >= bid is quote_state in {quoted, locked}: pinned, not imported."""
    from index_options.theta_exact_expiry import quotes_valid
    got = bool(quotes_valid(np.array([np.nan if bid is None else bid]),
                            np.array([np.nan if ask is None else ask]))[0])
    state = quote_state(bid, ask)
    assert got == (state in ("quoted", "locked"))


# -- the shipped document ----------------------------------------------------------------------


def test_shipped_document_plans_and_pins_the_sealed_bound():
    document = load_document(str(CONFIG))
    planned = plan(document)
    assert set(planned.order) == {"chain", "bars", "tables"}
    pipeline = json.loads(CONFIG.read_text())["pipeline"]
    chain, tables = pipeline["chain"]["params"], pipeline["tables"]["params"]
    assert chain["fields"] == READER_FIELDS
    assert chain["end_before"] == tables["end_before"] and chain["start"] == tables["lifecycle_start"]
    assert "sealed_from" not in chain          # the export's files straddle the sealed date; end_before is the seal
    assert chain["end_before"] <= "2024-01-01"
    assert ThetaExactExpiryTables.validate_params(tables) == []
    assert "ADR-0270" in json.loads(CONFIG.read_text())["notes"]
    assert pipeline["tables"]["inputs"] == {"records": "$chain.records", "bars": "$bars.records"}
    for source in pipeline["bars"]["params"]["sources"]:
        assert source["read_bounds"]["date"]["end_before"] == chain["end_before"]   # one sealed date, said twice
        assert "adjusted_close" not in source["columns"] and "split_coefficient" in source["columns"]
    assert tables["bar_price_field"] == "close"
    assert tables["lifecycle_start"] == chain["start"] == "2016-01-04" and tables["start"] == "2017-01-03"
    assert len(chain["symbols"]) == 118 and chain["symbols"] == sorted(set(chain["symbols"]))
    cohorts = Path("/home/russell/data/index_options/theta-strike-screen-20261018/registration/cohorts.json")
    if cohorts.exists():
        assert chain["symbols"] == sorted(json.loads(cohorts.read_text())["eligible"])


def write_descending_snapshot(directory, acq_id, rows, row_group_size=16):
    """The frozen export's real layout: a symbol's rows newest date first over several row groups."""
    rows = sorted(rows, key=lambda r: (r["date"], r["expiration"], r["strike"], r["right"]), reverse=True)
    rows = sorted(rows, key=lambda r: r["symbol"])
    found = {}
    for r in rows:
        lo, hi = found.get(r["symbol"], (r["date"], r["date"]))
        found[r["symbol"]] = (min(lo, r["date"]), max(hi, r["date"]))
    table = pa.Table.from_pylist(rows, schema=SCHEMA).replace_schema_metadata({
        "dskit.export.acq_id": acq_id, "dskit.export.manifest_sha256": "m" * 63 + acq_id[-1],
        "dskit.export.snapshot_hash": "s" * 63 + acq_id[-1],
        "dskit.export.windows": json.dumps({s: [a.isoformat(), b.isoformat()] for s, (a, b) in found.items()}),
        "dskit.export.missing_fields": "{}"})
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, directory / f"{acq_id}.parquet", row_group_size=row_group_size)


def test_an_export_written_newest_first_builds_the_same_outputs_as_one_written_oldest_first(tree, tmp_path):
    directory = tmp_path / "theta-desc"
    write_descending_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa", raw_rows("AAA"))
    write_descending_snapshot(directory, "20260101T000002Z-backfill-bbbbbbbb", raw_rows("BBB"))
    ascending = build(tree, tmp_path / "asc")["manifest"]
    whole = ThetaExactExpiryTables("tables", node_params(tmp_path / "desc")).run(
        CTX, {"records": reader(directory), "bars": BARS})["manifest"]
    archives = {k: v for k, v in ascending["files"].items() if k.startswith("archive/")}
    assert {k: v for k, v in whole["files"].items() if k.startswith("archive/")} == archives   # byte-identical
    assert {k: v["rows"] for k, v in whole["files"].items()} == {k: v["rows"] for k, v in ascending["files"].items()}
    surface = pd.read_parquet(tmp_path / "asc" / "surface.parquet")
    pd.testing.assert_frame_equal(surface, pd.read_parquet(tmp_path / "desc" / "surface.parquet"))
    chunked = ThetaExactExpiryTables("tables", node_params(tmp_path / "desc-chunked", flush_rows=40)).run(
        CTX, {"records": reader(directory, batch_size=10), "bars": BARS})["manifest"]
    assert chunked["symbols"]["AAA"]["rows_read"] == ascending["symbols"]["AAA"]["rows_read"]
    for name in ("surface", "lifecycle"):
        pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "asc" / f"{name}.parquet"),
                                      pd.read_parquet(tmp_path / "desc-chunked" / f"{name}.parquet"))
    for symbol in ("AAA", "BBB"):
        pd.testing.assert_frame_equal(archive_of(tmp_path / "asc", symbol), archive_of(tmp_path / "desc-chunked", symbol))
