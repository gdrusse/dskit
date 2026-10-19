"""ParquetChainRows (ADR-0265 Amendment 3): the Parquet-tree source of the chain nodes.

The tree is built by hand in the layout the snapshot export writes (one immutable file per snapshot named
by its acquisition id, sorted by symbol then date, the per-symbol date windows in the footer). Every
expected row is written by hand, never read back from the reader.
"""

import datetime as dt
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.libs.chain_parquet import ParquetChainRows
from dskit.pipeline.libs.root_stitch import RootStitch
from dskit.pipeline.node import NodeContext

FIELDS = ["symbol", "date", "expiration", "strike", "right", "bid", "ask"]
SCHEMA = pa.schema([("symbol", pa.string()), ("expiration", pa.date32()), ("strike", pa.float64()),
                    ("right", pa.string()), ("date", pa.date32()), ("bid", pa.float64()),
                    ("ask", pa.float64()), ("volume", pa.int32()), ("note", pa.string()),
                    ("open_interest", pa.int32()), ("underlying_price", pa.float64()), ("iv_error", pa.float64())])
CTX = NodeContext(name="t", asof="2025-12-31", run_dir="/nonexistent")


def day(text):
    return dt.date.fromisoformat(text)


def chain_row(symbol, date, strike=100.0, right="call", bid=1.0, ask=1.2, expiration=None, volume=3):
    """One contract row; the expiration defaults to 30 days after the date."""
    expiration = expiration or (day(date) + dt.timedelta(days=30)).isoformat()
    return {"symbol": symbol, "expiration": day(expiration), "strike": strike, "right": right,
            "date": day(date), "bid": bid, "ask": ask, "volume": volume, "note": "x"}


def write_snapshot(directory, acq_id, rows, row_group_size=4, windows=None, text_dates=False, manifest=True):
    """Write one export-shaped file: sorted by symbol then date, windows in the footer."""
    rows = sorted(rows, key=lambda r: (r["symbol"], r["date"], r["strike"], r["right"]))
    found = {}
    for r in rows:
        lo, hi = found.get(r["symbol"], (r["date"], r["date"]))
        found[r["symbol"]] = (min(lo, r["date"]), max(hi, r["date"]))
    windows = windows if windows is not None else {s: [a.isoformat(), b.isoformat()] for s, (a, b) in found.items()}
    schema = SCHEMA
    if text_dates:
        schema = pa.schema([pa.field(f.name, pa.string()) if f.name in ("date", "expiration") else f for f in SCHEMA])
        rows = [dict(r, date=r["date"].isoformat(), expiration=r["expiration"].isoformat()) for r in rows]
    table = pa.Table.from_pylist(rows, schema=schema).replace_schema_metadata({
        "dskit.export.acq_id": acq_id, "dskit.export.manifest_sha256": ("m" * 63 + acq_id[-1]) if manifest else "",
        "dskit.export.snapshot_hash": "s" * 63 + acq_id[-1], "dskit.export.windows": json.dumps(windows),
        "dskit.export.missing_fields": "{}"})
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{acq_id}.parquet"
    pq.write_table(table, path, row_group_size=row_group_size)
    return path


def params(directory, **over):
    base = {"directories": [str(directory)], "fields": FIELDS, "symbol_field": "symbol", "date_field": "date",
            "end_before": "2026-01-01"}
    base.update(over)
    return {k: v for k, v in base.items() if v is not ...}


def source(directory, **over):
    return ParquetChainRows("chain", params(directory, **over)).run(CTX, {})["records"]


def dates(rows, symbol):
    return [r["date"] for r in rows if r["symbol"] == symbol]


@pytest.fixture
def tree(tmp_path):
    """Two roots over three snapshots: AAA spans two files (older dates in the NEWER file), BBB one."""
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa",
                   [chain_row("AAA", d, k) for d in ("2020-03-02", "2020-03-03", "2020-03-04") for k in (90.0, 100.0)]
                   + [chain_row("BBB", d) for d in ("2021-05-03", "2021-05-04")])
    write_snapshot(directory, "20260101T000002Z-backfill-bbbbbbbb",
                   [chain_row("AAA", d, k) for d in ("2019-12-30", "2019-12-31") for k in (90.0, 100.0)])
    return directory


# -- the rows ----------------------------------------------------------------------------------


def test_rows_are_dicts_of_the_declared_fields_with_dates_as_iso_text(tree):
    first = next(iter(source(tree)))
    assert first == {"symbol": "AAA", "date": "2019-12-30", "expiration": "2020-01-29", "strike": 90.0,
                     "right": "call", "bid": 1.0, "ask": 1.2}
    assert set(first) == set(FIELDS)


def test_rows_come_symbol_by_symbol_with_dates_ascending_across_a_roots_files(tree):
    rows = list(source(tree))
    assert [r["symbol"] for r in rows] == ["AAA"] * 10 + ["BBB"] * 2
    assert dates(rows, "AAA")[::2] == ["2019-12-30", "2019-12-31", "2020-03-02", "2020-03-03", "2020-03-04"]
    assert len(rows) == 12


def test_the_source_is_re_iterable_and_reports_its_counters(tree):
    rows = source(tree)
    assert len(list(rows)) == len(list(rows)) == 12
    assert rows.report() == {"members": 2, "rows": 12, "units": 7, "skipped_rows": 0, "skipped_units": 0,
                             "unit_row_mismatch": 0, "files": 2, "symbols": 2, "symbols_missing": 0,
                             "stitch": {"renames": 0, "breaks": 0, "absent": [], "pending": 0, "pending_landed": []}}


def test_symbols_filter_reads_only_those_symbols_and_their_files(tree):
    rows = source(tree, symbols=["BBB"])
    assert {r["symbol"] for r in rows} == {"BBB"}
    assert rows.report()["members"] == 1 and rows.report()["rows"] == 2


def test_a_requested_symbol_that_has_not_landed_is_counted_not_refused(tree):
    rows = source(tree, symbols=["BBB", "NOTYET"])
    assert {r["symbol"] for r in rows} == {"BBB"} and rows.report()["symbols_missing"] == 1


def test_symbols_listing_is_sorted_and_select_narrows(tree):
    rows = source(tree)
    assert rows.symbols() == ["AAA", "BBB"]
    narrowed = rows.select(["BBB"])
    assert [r["symbol"] for r in narrowed] == ["BBB", "BBB"] and narrowed.symbols() == ["BBB"]
    with pytest.raises(ValueError, match="CCC"):
        rows.select(["CCC"])


def test_start_and_end_before_bound_the_dates_and_prune_files(tree):
    rows = source(tree, start="2020-01-01", end_before="2020-03-04")
    assert sorted(set(dates(rows, "AAA"))) == ["2020-03-02", "2020-03-03"]
    assert rows.report()["members"] == 1          # the 2019 file is outside the window: never opened


def test_the_batches_view_holds_the_same_rows_as_arrow_tables(tree):
    rows = source(tree)
    tables = list(rows.batches())
    assert sum(t.num_rows for t in tables) == 12
    flat = [row for t in tables for row in t.to_pylist()]
    assert [r["symbol"] for r in flat] == ["AAA"] * 10 + ["BBB"] * 2
    assert flat[0]["date"] == "2019-12-30"


def test_a_symbol_with_nothing_inside_the_bounds_yields_nothing(tree):
    assert list(source(tree, start="2030-01-01", end_before="2031-01-01")) == []


# -- newest acquired copy of a unit wins ---------------------------------------------------------


def test_an_older_copy_of_a_unit_is_skipped_and_the_newest_acquired_copy_wins(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa",
                   [chain_row("AAA", "2020-03-02", bid=1.0), chain_row("AAA", "2020-03-03", bid=1.0)])
    write_snapshot(directory, "20260101T000002Z-backfill-bbbbbbbb",
                   [chain_row("AAA", "2020-03-03", bid=9.0), chain_row("AAA", "2020-03-04", bid=9.0)])
    rows = source(tmp_path / "theta")
    got = {r["date"]: r["bid"] for r in rows}
    assert got == {"2020-03-02": 1.0, "2020-03-03": 9.0, "2020-03-04": 9.0}
    assert rows.report()["skipped_rows"] == 1 and rows.report()["skipped_units"] == 1
    assert rows.report()["unit_row_mismatch"] == 0 and rows.report()["units"] == 3


def test_overlapping_files_still_yield_each_symbols_dates_ascending(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa",
                   [chain_row("AAA", f"2020-03-0{n}", bid=1.0) for n in (2, 3, 4, 5, 6)] + [chain_row("ZZZ", "2020-03-02")])
    write_snapshot(directory, "20260101T000002Z-backfill-bbbbbbbb",
                   [chain_row("AAA", f"2020-03-0{n}", bid=9.0) for n in (3, 5, 6)])
    rows = source(directory, symbols=["AAA"])
    assert [(r["date"], r["bid"]) for r in rows] == [("2020-03-02", 1.0), ("2020-03-03", 9.0), ("2020-03-04", 1.0),
                                                     ("2020-03-05", 9.0), ("2020-03-06", 9.0)]
    assert rows.report()["skipped_rows"] == 3 and rows.report()["units"] == 5


def test_an_older_copy_with_a_different_row_count_is_a_counted_split_unit(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa",
                   [chain_row("AAA", "2020-03-03", 90.0), chain_row("AAA", "2020-03-03", 100.0)])
    write_snapshot(directory, "20260101T000002Z-backfill-bbbbbbbb", [chain_row("AAA", "2020-03-03", 90.0)])
    rows = source(directory)
    assert [r["strike"] for r in rows] == [90.0]
    assert rows.report()["skipped_rows"] == 2 and rows.report()["unit_row_mismatch"] == 1


def test_three_copies_of_a_unit_keep_the_newest_and_count_the_unit_once(tmp_path):
    directory = tmp_path / "theta"
    for i, bid in enumerate((1.0, 2.0, 3.0), start=1):
        write_snapshot(directory, f"20260101T00000{i}Z-backfill-aaaaaaa{i}", [chain_row("AAA", "2020-03-03", bid=bid)])
    rows = source(directory)
    assert [r["bid"] for r in rows] == [3.0]
    assert rows.report()["skipped_rows"] == 2 and rows.report()["skipped_units"] == 1


def test_files_of_other_roots_never_shadow_a_root(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa", [chain_row("AAA", "2020-03-03")])
    write_snapshot(directory, "20260101T000002Z-backfill-bbbbbbbb", [chain_row("BBB", "2020-03-03")])
    assert source(directory).report()["skipped_rows"] == 0


# -- trees ---------------------------------------------------------------------------------------


def test_several_directories_are_one_stream_ordered_by_acquisition_id(tmp_path):
    write_snapshot(tmp_path / "tier1" / "theta", "20260101T000001Z-backfill-aaaaaaaa", [chain_row("AAA", "2020-03-03", bid=1.0)])
    write_snapshot(tmp_path / "tier2" / "theta", "20260101T000002Z-backfill-bbbbbbbb", [chain_row("AAA", "2020-03-03", bid=2.0)])
    node = ParquetChainRows("chain", params(tmp_path / "tier1" / "theta", directories=[
        str(tmp_path / "tier2" / "theta"), str(tmp_path / "tier1" / "theta")]))
    rows = node.run(CTX, {})["records"]
    assert [r["bid"] for r in rows] == [2.0]


def test_hidden_temp_files_and_foreign_files_are_ignored(tree):
    (tree / ".tmp-1.parquet").write_bytes(b"torn")
    (tree / "notes.txt").write_text("x")
    assert len(list(source(tree))) == 12


# -- the sealed period and refusals -------------------------------------------------------------


def test_a_file_that_reaches_the_sealed_date_is_refused_whole_even_when_bounds_would_cut_it(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa",
                   [chain_row("AAA", "2025-12-31"), chain_row("AAA", "2026-01-02")])
    with pytest.raises(ValueError, match="sealed"):
        source(directory, sealed_from="2026-01-01")
    assert len(list(source(directory))) == 1       # without the seal, the bound alone cuts the row


def test_end_before_beyond_the_sealed_date_refuses_at_construction(tree):
    with pytest.raises(ConfigError, match="end_before"):
        ParquetChainRows("chain", params(tree, end_before="2026-01-02", sealed_from="2026-01-01"))


def test_a_file_without_footer_windows_refuses_naming_it(tmp_path):
    directory = tmp_path / "theta"
    directory.mkdir()
    pq.write_table(pa.Table.from_pylist([chain_row("AAA", "2020-03-03")], schema=SCHEMA),
                   directory / "20260101T000001Z-backfill-aaaaaaaa.parquet")
    with pytest.raises(ValueError, match="aaaaaaaa.*windows"):
        source(directory)


def test_a_declared_field_missing_from_the_schema_refuses(tree):
    with pytest.raises(ValueError, match="nope"):
        source(tree, fields=[*FIELDS, "nope"])


def test_an_empty_directory_refuses(tmp_path):
    (tmp_path / "theta").mkdir()
    with pytest.raises(ValueError, match="no Parquet files"):
        source(tmp_path / "theta")


@pytest.mark.parametrize("over, fragment", [
    ({"directories": []}, "directories"),
    ({"directories": "x"}, "directories"),
    ({"fields": []}, "fields"),
    ({"end_before": ...}, "end_before"),
    ({"end_before": "2026-1-1"}, "end_before"),
    ({"start": "2020-01-01", "end_before": "2020-01-01"}, "start"),
    ({"symbols": []}, "symbols"),
    ({"symbols": [1]}, "symbols"),
    ({"batch_size": 0}, "batch_size"),
    ({"surprise": 1}, "surprise"),
    ({"renames_file": "/x"}, "renames"),
    ({"symbol_field": ...}, "symbol_field"),
])
def test_bad_params_refuse_by_name(tree, over, fragment):
    with pytest.raises(ConfigError, match=fragment):
        ParquetChainRows("chain", params(tree, **over))


def test_a_file_that_changed_between_fingerprint_and_run_refuses(tree):
    node = ParquetChainRows("chain", params(tree))
    node.fingerprint()
    extra = write_snapshot(tree, "20260101T000009Z-backfill-99999999", [chain_row("CCC", "2020-03-03")])
    assert extra.exists()
    rows = node.run(CTX, {})["records"]               # the pinned plan: the new file is NOT read
    assert {r["symbol"] for r in rows} == {"AAA", "BBB"}
    path = tree / "20260101T000002Z-backfill-bbbbbbbb.parquet"
    path.write_bytes(path.read_bytes() + b"\0")
    with pytest.raises(ValueError, match="changed"):
        list(rows)


def test_the_fingerprint_moves_with_the_files_and_not_with_the_clock(tree):
    first = ParquetChainRows("chain", params(tree)).fingerprint()
    assert first == ParquetChainRows("chain", params(tree)).fingerprint()
    write_snapshot(tree, "20260101T000009Z-backfill-99999999", [chain_row("CCC", "2020-03-03")])
    assert ParquetChainRows("chain", params(tree)).fingerprint()["sha256"] != first["sha256"]
    assert first["members"] == ["20260101T000001Z-backfill-aaaaaaaa.parquet",
                                "20260101T000002Z-backfill-bbbbbbbb.parquet"]


# -- stitching -----------------------------------------------------------------------------------


@pytest.fixture
def renamed(tmp_path):
    """OLDCO to 2022-06-08 in one file, NEWCO from 2022-06-09 in the next, plus an unrelated root."""
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa",
                   [chain_row("NEWCO", d) for d in ("2022-06-09", "2022-06-10")] + [chain_row("ZZZ", "2022-06-09")])
    write_snapshot(directory, "20260101T000002Z-backfill-bbbbbbbb",
                   [chain_row("OLDCO", d) for d in ("2022-06-07", "2022-06-08")])
    renames = tmp_path / "renames.json"
    renames.write_text(json.dumps({"renames": [{"old": "OLDCO", "new": "NEWCO", "effective": "2022-06-09"}]}))
    return directory, {"renames_file": str(renames), "renames_key": "renames",
                       "renames_sha256": RootStitch.file_digest(str(renames))}


def test_a_stitched_root_is_one_series_under_the_new_name(renamed):
    directory, stitch = renamed
    rows = source(directory, **stitch)
    assert rows.symbols() == ["NEWCO", "ZZZ"]
    assert [(r["symbol"], r["date"]) for r in rows if r["symbol"] == "NEWCO"] == [
        ("NEWCO", "2022-06-07"), ("NEWCO", "2022-06-08"), ("NEWCO", "2022-06-09"), ("NEWCO", "2022-06-10")]
    only = source(directory, symbols=["NEWCO"], **stitch)
    assert len(list(only)) == 4 and only.report()["rows"] == 4 and only.report()["members"] == 2
    assert rows.report()["members"] == 2


def test_without_the_map_the_roots_stay_apart(renamed):
    assert source(renamed[0]).symbols() == ["NEWCO", "OLDCO", "ZZZ"]


def test_overlapping_old_and_new_dates_refuse_and_name_both_roots(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa", [chain_row("NEWCO", "2022-06-09")])
    write_snapshot(directory, "20260101T000002Z-backfill-bbbbbbbb", [chain_row("OLDCO", "2022-06-09")])
    renames = tmp_path / "r.json"
    renames.write_text(json.dumps({"renames": [{"old": "OLDCO", "new": "NEWCO", "effective": "2022-06-09"}]}))
    with pytest.raises(ValueError, match="OLDCO.*NEWCO"):
        source(directory, renames_file=str(renames), renames_key="renames",
               renames_sha256=RootStitch.file_digest(str(renames)))


def test_a_wrong_effective_date_is_caught_by_the_data(renamed, tmp_path):
    directory, _ = renamed
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"renames": [{"old": "OLDCO", "new": "NEWCO", "effective": "2022-06-08"}]}))
    with pytest.raises(ValueError, match="effective"):
        source(directory, renames_file=str(bad), renames_key="renames",
               renames_sha256=RootStitch.file_digest(str(bad)))


def test_a_changed_rename_file_refuses_by_hash(renamed):
    directory, stitch = renamed
    with pytest.raises(ValueError, match="sha256"):
        source(directory, **{**stitch, "renames_sha256": "0" * 64})


# -- parity with the gzip unit reader, node outputs included -----------------------------------------


def priced_chain(symbol, date, dte=28, spot=100.0, vol=0.30, bid_shift=0.0):
    """Black-76 priced rows of one expiry (zero rate), a call and a put at each whole strike 80..120."""
    from dskit.pipeline.option_pricing import black76
    expiration = (day(date) + dt.timedelta(days=dte)).isoformat()
    rows = []
    for strike in range(80, 121):
        for right in ("call", "put"):
            mid = black76(right, spot, float(strike), vol, dte / 365)
            rows.append({"symbol": symbol, "date": date, "expiration": expiration, "strike": float(strike),
                         "right": right, "bid": mid - 0.005 + bid_shift, "ask": mid + 0.005 + bid_shift,
                         "open_interest": 10, "volume": 3, "underlying_price": spot, "iv_error": 0.0})
    return rows


def test_both_readers_give_the_nodes_identical_outputs_over_a_spanning_revised_store(tmp_path):
    from dskit.onboarding import OnboardingRoot, run_acquisition
    from dskit.pipeline.libs.chain_census import ChainCensus
    from dskit.pipeline.libs.chain_features import ChainDayFeatures
    from dskit.pipeline.libs.observations import ObservationUnitRows
    first = [r for d in ("2020-03-04", "2020-03-05", "2020-03-06") for r in priced_chain("AAA", d)] + [
        r for d in ("2020-03-05", "2020-03-06") for r in priced_chain("BBB", d, spot=50.0, vol=0.4)]
    second = [r for d in ("2020-03-02", "2020-03-03", "2020-03-04") for r in priced_chain("AAA", d, vol=0.35)]
    revised = second + priced_chain("BBB", "2020-03-06", spot=50.0, vol=0.45)          # BBB 03-06 is re-pulled, revised
    data = tmp_path / "data"
    data.mkdir()
    ob = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = ob.registry()
    vid = registry.register("source_config", {"name": "theta", "catalog_source": "theta-src",
                                              "connector": "localfiles",
                                              "config": {"path": str(data), "effective_field": "pulled"}}, origin="test")
    registry.transition(vid, "active", origin="test")
    members, names = [], []
    for i, batch in enumerate((first, revised)):
        with open(data / "option_eod.jsonl", "w", encoding="utf-8") as fh:
            for r in batch:
                fh.write(json.dumps(dict(r, pulled=f"2020-04-0{i + 1}"), sort_keys=True) + "\n")
        done = run_acquisition(ob, registry, "theta", "option_eod", "backfill")
        assert done["records"] == len(batch)
        members.append(batch)
        names.append(done["acq_id"])        # the id decides recency in BOTH readers, even within one second
    assert len(set(names)) == 2
    tree = tmp_path / "theta"
    for name, batch in zip(names, members):
        write_snapshot(tree, name, [dict(r, expiration=day(r["expiration"]), date=day(r["date"]),
                                         **{"note": "x"}) for r in batch], row_group_size=50)
    read = ["symbol", "date", "expiration", "strike", "right", "bid", "ask", "open_interest", "volume",
            "underlying_price", "iv_error"]
    ctx = NodeContext(name="t", asof="2025-12-31", run_dir=str(tmp_path / "run"))
    gz = ObservationUnitRows("chain", {"root": ob.root, "source": "theta", "stream": "option_eod",
                                       "unit_fields": ["symbol", "date"], "fields": read}).run(ctx, {})["records"]
    pqs = ParquetChainRows("chain", params(tree, fields=read)).run(ctx, {})["records"]
    census = {"symbol_field": "symbol", "date_field": "date", "strike_field": "strike", "bid_field": "bid",
              "ask_field": "ask", "iv_error_field": "iv_error", "iv_error_flag": 100,
              "underlying_price_field": "underlying_price", "end_before": "2026-01-01",
              "allow_unit_mismatch": True}
    features = {"symbol_field": "symbol", "date_field": "date", "expiration_field": "expiration",
                "strike_field": "strike", "right_field": "right", "bid_field": "bid", "ask_field": "ask",
                "open_interest_field": "open_interest", "volume_field": "volume",
                "underlying_price_field": "underlying_price", "call_values": ["call"], "put_values": ["put"],
                "end_before": "2026-01-01", "targets": [{"dte": 30, "tolerance": 5}], "target_field": "target_dte",
                "status_field": "status", "atm_band": 0.05, "wing_delta": 0.25, "min_forward_pairs": 3,
                "oi_scope": "expiry", "oi_lag_sessions": 2, "oi_lag_max_days": 7,
                "allow_unit_mismatch": True, "symbol_contiguous": True,
                "fields": {"atm_iv": "atm_iv", "risk_reversal": "rr", "forward": "forward", "oi_change": "dOI",
                           "straddle_ratio": "straddle", "put_wing_iv": "p25", "call_wing_iv": "c25"}}
    for cls, over in ((ChainCensus, census), (ChainDayFeatures, features)):
        # the gzip reader's newest-member-first order interleaves a root that spans snapshots, which
        # symbol_contiguous refuses; it runs hold-everything, the Parquet reader runs symbol by symbol
        a = cls("n", {**over, "symbol_contiguous": False} if cls is ChainDayFeatures else over).run(ctx, {"records": gz})
        b = cls("n", over).run(ctx, {"records": pqs})
        shared = ("members", "rows", "units", "skipped_rows", "skipped_units", "unit_row_mismatch")
        key = "reader"
        assert {k: a["summary"][key][k] for k in shared} == {k: b["summary"][key][k] for k in shared}
        a["summary"].pop(key), b["summary"].pop(key)
        assert a == b
    assert gz.report()["skipped_rows"] == 164 and gz.report()["skipped_units"] == 2 and gz.report()["unit_row_mismatch"] == 0


def test_the_source_names_its_identity_and_the_raw_windows_of_every_root(renamed):
    directory, stitch = renamed
    rows = source(directory, start="2022-06-01", **stitch)
    identity = rows.identity()
    assert identity["end_before"] == "2026-01-01" and identity["start"] == "2022-06-01"
    assert identity["renames"] == RootStitch([{"old": "OLDCO", "new": "NEWCO", "effective": "2022-06-09"}]).digest()
    assert source(directory).identity()["renames"] == RootStitch([]).digest()
    assert rows.raw_windows() == {"NEWCO": ("2022-06-09", "2022-06-10"), "OLDCO": ("2022-06-07", "2022-06-08"),
                                  "ZZZ": ("2022-06-09", "2022-06-09")}
    assert rows.contributing("NEWCO") == [
        ("20260101T000002Z-backfill-bbbbbbbb.parquet", (directory / "20260101T000002Z-backfill-bbbbbbbb.parquet").stat().st_size, "m" * 63 + "b"),
        ("20260101T000001Z-backfill-aaaaaaaa.parquet", (directory / "20260101T000001Z-backfill-aaaaaaaa.parquet").stat().st_size, "m" * 63 + "a")]


# -- reuse breaks ----------------------------------------------------------------------------------------


@pytest.fixture
def reused(tmp_path):
    """Root AI: one file holds both companies (the break falls INSIDE it), a second holds only the earlier one."""
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa",
                   [chain_row("AI", d, bid=9.0) for d in ("2020-12-14", "2020-12-15", "2020-12-16")]
                   + [chain_row("ZZZ", "2020-12-15")])
    write_snapshot(directory, "20260101T000002Z-backfill-bbbbbbbb", [chain_row("AI", "2020-12-10", bid=1.0)])
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"renames": [], "breaks": [
        {"root": "AI", "effective": "2020-12-15", "earlier_as": "AI.before"}]}))
    return directory, {"renames_file": str(path), "renames_key": "renames", "breaks_key": "breaks",
                       "renames_sha256": RootStitch.file_digest(str(path))}


def test_a_break_files_the_rows_before_it_under_the_earlier_name(reused):
    directory, stitch = reused
    rows = source(directory, **stitch)
    assert rows.symbols() == ["AI", "AI.before", "ZZZ"]
    got = [(r["symbol"], r["date"]) for r in rows]
    assert got == [("AI", "2020-12-15"), ("AI", "2020-12-16"), ("AI.before", "2020-12-10"),
                   ("AI.before", "2020-12-14"), ("ZZZ", "2020-12-15")]
    assert rows.report()["rows"] == 5 and rows.report()["units"] == 5


def test_a_break_can_be_read_one_series_at_a_time_and_names_its_raw_root(reused):
    directory, stitch = reused
    before = source(directory, symbols=["AI.before"], **stitch)
    assert [r["date"] for r in before] == ["2020-12-10", "2020-12-14"] and before.raws("AI.before") == ["AI"]
    assert before.report()["members"] == 2
    after = source(directory, symbols=["AI"], **stitch)
    assert [r["date"] for r in after] == ["2020-12-15", "2020-12-16"] and after.report()["members"] == 1
    assert after.regime_start("AI") == "2020-12-15" and after.regime_start("AI.before") is None


def test_a_break_with_no_data_on_one_side_just_yields_the_other(tmp_path, reused):
    directory, stitch = reused
    only_late = tmp_path / "late"
    write_snapshot(only_late, "20260101T000001Z-backfill-aaaaaaaa", [chain_row("AI", "2020-12-16")])
    assert source(only_late, **stitch).symbols() == ["AI"]


def test_the_break_changes_the_source_identity(reused):
    directory, stitch = reused
    assert source(directory, **stitch).identity()["renames"] != source(directory).identity()["renames"]


def test_breaks_key_without_the_renames_file_refuses(reused):
    directory, stitch = reused
    with pytest.raises(ConfigError, match="breaks_key"):
        ParquetChainRows("chain", params(directory, breaks_key="breaks"))


# -- review fixes: footer proof, text dates, identity, fingerprint ---------------------------------------


def test_a_footer_window_the_data_contradicts_refuses_naming_the_file_and_root(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa",
                   [chain_row("AAA", d) for d in ("2020-03-02", "2020-03-03", "2020-03-04")],
                   windows={"AAA": ["2020-03-03", "2020-03-04"]})
    with pytest.raises(ValueError, match=r"aaaaaaaa.*AAA.*window|AAA.*aaaaaaaa.*window"):
        source(directory)


def test_a_footer_window_for_a_root_the_data_does_not_hold_refuses(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa", [chain_row("AAA", "2020-03-03")],
                   windows={"AAA": ["2020-03-03", "2020-03-03"], "GHOST": ["2020-03-03", "2020-03-04"]})
    with pytest.raises(ValueError, match="GHOST"):
        source(directory)


def test_a_window_wider_than_the_data_in_pure_row_groups_is_a_mismatch_too(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa", [chain_row("AAA", "2020-03-03")],
                   windows={"AAA": ["2020-01-01", "2020-12-31"]})
    with pytest.raises(ValueError, match="window"):
        source(directory)


def test_a_text_date_column_is_read_like_a_date32_one_including_overlap_ordering(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa",
                   [chain_row("AAA", f"2020-03-0{n}", bid=1.0) for n in (2, 3, 4)], text_dates=True)
    write_snapshot(directory, "20260101T000002Z-backfill-bbbbbbbb",
                   [chain_row("AAA", "2020-03-03", bid=9.0)], text_dates=True)
    rows = source(directory)
    assert [(r["date"], r["bid"]) for r in rows] == [("2020-03-02", 1.0), ("2020-03-03", 9.0), ("2020-03-04", 1.0)]
    assert rows.report()["units"] == 3
    assert len(list(source(directory, start="2020-03-03", end_before="2020-03-04"))) == 1


def test_a_file_without_a_manifest_identity_refuses(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa", [chain_row("AAA", "2020-03-03")], manifest=False)
    with pytest.raises(ValueError, match="manifest_sha256"):
        source(directory)


def test_run_pins_the_file_set_itself_so_two_readers_of_one_node_agree(tree):
    node = ParquetChainRows("chain", params(tree))
    first = node.run(CTX, {})["records"]
    write_snapshot(tree, "20260101T000009Z-backfill-99999999", [chain_row("CCC", "2020-03-03")])
    second = node.run(CTX, {})["records"]
    assert first.symbols() == second.symbols() == ["AAA", "BBB"]
    assert ParquetChainRows("chain", params(tree)).run(CTX, {})["records"].symbols() == ["AAA", "BBB", "CCC"]


# -- stitch bookkeeping and the break proof on the real reader -----------------------------------------------


def stitch_file(tmp_path, **document):
    path = tmp_path / "map.json"
    path.write_text(json.dumps(document))
    return {"renames_file": str(path), "renames_key": "renames", "renames_sha256": RootStitch.file_digest(str(path)),
            **({"breaks_key": "breaks"} if "breaks" in document else {}),
            **({"pending_key": "pending_verification"} if "pending_verification" in document else {})}


def test_a_break_inside_a_run_of_observed_dates_refuses_from_the_data(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa",
                   [chain_row("AI", d) for d in ("2020-12-10", "2020-12-14", "2020-12-15", "2020-12-16")])
    stitch = stitch_file(tmp_path, renames=[], breaks=[{"root": "AI", "effective": "2020-12-15", "earlier_as": "AI.b"}])
    assert source(directory, **{**stitch, "symbols": ["AI"]}).report()["stitch"]["breaks"] == 1
    bad = stitch_file(tmp_path, renames=[], breaks=[{"root": "AI", "effective": "2020-12-12", "earlier_as": "AI.b"}])
    with pytest.raises(ValueError, match=r"AI.*2020-12-10.*2020-12-14"):
        source(directory, **bad)


def test_the_report_names_declared_roots_absent_from_the_data_and_pending_entries_now_landed(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa", [chain_row("AAA", "2020-03-03"), chain_row("OLD2", "2020-03-03")])
    stitch = stitch_file(tmp_path, renames=[{"old": "OLD1", "new": "NEW1", "effective": "2020-06-01"}],
                         pending_verification=[{"old": "OLD2", "new": "NEW2", "note": "x"},
                                               {"old": "OLD3", "new": "NEW3", "note": "y"}])
    report = source(directory, **stitch).report()["stitch"]
    assert report == {"renames": 1, "breaks": 0, "absent": ["NEW1", "OLD1"], "pending": 2, "pending_landed": ["OLD2"]}


def write_descending_snapshot(directory, acq_id, rows, row_group_size=2):
    """The export's real layout: a symbol's rows newest date first, several row groups, windows in the footer."""
    rows = sorted(rows, key=lambda r: (r["symbol"], r["date"], r["strike"], r["right"]), reverse=True)
    rows = sorted(rows, key=lambda r: r["symbol"])
    found = {}
    for r in rows:
        lo, hi = found.get(r["symbol"], (r["date"], r["date"]))
        found[r["symbol"]] = (min(lo, r["date"]), max(hi, r["date"]))
    windows = {s: [a.isoformat(), b.isoformat()] for s, (a, b) in found.items()}
    table = pa.Table.from_pylist(rows, schema=SCHEMA).replace_schema_metadata({
        "dskit.export.acq_id": acq_id, "dskit.export.manifest_sha256": "m" * 63 + acq_id[-1],
        "dskit.export.snapshot_hash": "s" * 63 + acq_id[-1], "dskit.export.windows": json.dumps(windows),
        "dskit.export.missing_fields": "{}"})
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, directory / f"{acq_id}.parquet", row_group_size=row_group_size)


def test_a_file_written_newest_first_still_yields_each_symbols_dates_ascending(tmp_path):
    directory = tmp_path / "theta"
    write_descending_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa",
                              [chain_row("AAA", f"2020-03-0{n}", k) for n in (2, 3, 4, 5, 6) for k in (90.0, 100.0)]
                              + [chain_row("BBB", f"2020-03-0{n}") for n in (3, 4, 5)])
    write_descending_snapshot(directory, "20260101T000002Z-backfill-bbbbbbbb",
                              [chain_row("AAA", f"2020-02-2{n}", k) for n in (6, 7, 8) for k in (90.0, 100.0)])
    rows = source(directory, end_before="2020-03-06", batch_size=3)
    got = list(rows)
    assert [r["symbol"] for r in got] == ["AAA"] * 14 + ["BBB"] * 3
    assert dates(got, "AAA") == sorted(dates(got, "AAA"))
    assert dates(got, "AAA")[::2] == ["2020-02-26", "2020-02-27", "2020-02-28", "2020-03-02", "2020-03-03",
                                       "2020-03-04", "2020-03-05"]
    assert dates(got, "BBB") == ["2020-03-03", "2020-03-04", "2020-03-05"]
    assert rows.report()["rows"] == 17 and rows.report()["units"] == 10
    for table in rows.batches():
        column = table.column("date").to_pylist()
        assert column == sorted(column)
