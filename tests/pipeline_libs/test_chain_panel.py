"""ChainPanelBuild (ADR-0265 Amendment 3): the date-partitioned panel of chain-day features.

The chains are Black-76 priced rows (the same generator the reader tests use), so a root's features
are known to the node under test only through the ChainDayFeatures it drives; the expected panel is built
by running that node over the same rows directly. No store, no vendor file.
"""

import hashlib
import json
import os

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.libs.chain_features import ChainDayFeatures
from dskit.pipeline.libs.chain_panel import ChainPanelBuild
from dskit.pipeline.libs.chain_parquet import ParquetChainRows
from dskit.pipeline.libs.parquet import DateBoundedParquet
from dskit.pipeline.node import NodeContext
from tests.pipeline_libs.test_chain_parquet import day, priced_chain, write_snapshot

CTX = NodeContext(name="t", asof="2025-12-31", run_dir="/nonexistent")
READ = ["symbol", "date", "expiration", "strike", "right", "bid", "ask", "open_interest", "volume",
        "underlying_price"]
FEATURES = {
    "symbol_field": "symbol", "date_field": "date", "expiration_field": "expiration", "strike_field": "strike",
    "right_field": "right", "bid_field": "bid", "ask_field": "ask", "open_interest_field": "open_interest",
    "volume_field": "volume", "underlying_price_field": "underlying_price", "call_values": ["call"],
    "put_values": ["put"], "end_before": "2026-01-01", "targets": [{"dte": 30, "tolerance": 5}],
    "target_field": "target_dte", "status_field": "status", "atm_band": 0.05, "wing_delta": 0.25,
    "min_forward_pairs": 3, "oi_scope": "expiry", "oi_lag_sessions": 2, "oi_lag_max_days": 7,
    "symbol_contiguous": True,
    "fields": {"expiration": "expiration", "dte": "dte", "atm_iv": "atm_iv", "risk_reversal": "rr",
               "straddle_ratio": "straddle", "oi_change": "dOI"}}
DAYS = ("2020-03-04", "2020-03-05", "2020-03-06")


def export_row(r):
    return dict(r, expiration=day(r["expiration"]), date=day(r["date"]), note="x")


def chains(symbol, spot=100.0, vol=0.30, days=DAYS):
    return [export_row(r) for d in days for r in priced_chain(symbol, d, spot=spot, vol=vol)]


@pytest.fixture
def tree(tmp_path):
    """AAA over two snapshots, BBB over one, DEAD never quoted."""
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa", chains("AAA", days=DAYS[1:])
                   + chains("BBB", spot=100.0, vol=0.4), row_group_size=80)
    write_snapshot(directory, "20260101T000002Z-backfill-bbbbbbbb", chains("AAA", days=DAYS[:1], vol=0.28)
                   + [dict(r, bid=0.0, ask=0.05) for r in chains("DEAD", days=DAYS[:1])], row_group_size=80)
    return directory


def reader(tree, **over):
    params = {"directories": [str(tree)], "fields": READ, "symbol_field": "symbol", "date_field": "date",
              "end_before": "2026-01-01", **over}
    return ParquetChainRows("chain", params).run(CTX, {})["records"]


def panel_params(path, **over):
    return {"features": FEATURES, "path": str(path), **over}


def build(tree, path, rows=None, **over):
    node = ChainPanelBuild("panel", panel_params(path, **over))
    return node.run(CTX, {"records": rows if rows is not None else reader(tree)})


def date_files(path):
    return sorted(n for n in os.listdir(os.path.join(path, "panel")) if not n.startswith("."))


def read_date(path, text):
    return pq.read_table(os.path.join(path, "panel", f"{text}.parquet")).to_pylist()


def direct(tree, symbol):
    """The features of one root computed straight from the node, for comparison."""
    rows = list(reader(tree, symbols=[symbol]))
    return ChainDayFeatures("f", FEATURES).run(CTX, {"records": rows})["records"]


# -- the panel ----------------------------------------------------------------------------------------


def test_one_file_per_date_holds_one_row_per_symbol_and_target(tree, tmp_path):
    out = build(tree, tmp_path / "panel")
    assert date_files(tmp_path / "panel") == [f"{d}.parquet" for d in DAYS]
    for d in DAYS:
        rows = read_date(tmp_path / "panel", d)
        assert [(r["symbol"], r["target_dte"]) for r in rows] == [("AAA", 30), ("BBB", 30)]
        assert {r["date"].isoformat() for r in rows} == {d}
    assert out["summary"]["rows"] == 6 and out["summary"]["dates"] == 3


def test_the_panel_rows_are_the_feature_node_rows(tree, tmp_path):
    build(tree, tmp_path / "panel")
    for symbol in ("AAA", "BBB"):
        expected = {r["date"]: r for r in direct(tree, symbol)}
        for d, want in expected.items():
            got = next(r for r in read_date(tmp_path / "panel", d) if r["symbol"] == symbol)
            assert got["status"] == want["status"] and got["target_dte"] == want["target_dte"]
            for name in ("atm_iv", "rr", "straddle", "dOI", "dte"):
                assert got[name] == want[name]
            assert got["expiration"].isoformat() == want["expiration"]


def test_a_root_that_never_quotes_is_counted_and_has_no_rows(tree, tmp_path):
    out = build(tree, tmp_path / "panel")
    summary = out["summary"]
    assert summary["roots"] == 3 and summary["roots_empty"] == 1 and summary["roots_built"] == 3
    assert all(r["symbol"] != "DEAD" for d in DAYS for r in read_date(tmp_path / "panel", d))
    assert summary["features"]["groups_before_first_bid"] == 1


def test_the_universe_gate_columns_come_from_first_quote_and_the_census(tree, tmp_path):
    first = tmp_path / "first_quote.json"
    first.write_text(json.dumps({"AAA": "2020-03-05", "BBB": "2020-03-04"}))
    census = tmp_path / "census.jsonl"
    census.write_text("\n".join(json.dumps(r) for r in (
        {"symbol": "AAA", "liquidity_tercile": 3, "first_bid_date": "2020-03-04"},
        {"symbol": "BBB", "liquidity_tercile": 1, "first_bid_date": "2020-03-04"})) + "\n")
    build(tree, tmp_path / "panel", first_quote_file=str(first), first_quote_field="first_quote_date",
          gate_field="after_first_quote", census_file=str(census), census_columns={"tercile": ["liquidity_tercile", "int32"]})
    rows = {(r["symbol"], r["date"].isoformat()): r for d in DAYS for r in read_date(tmp_path / "panel", d)}
    assert rows[("AAA", "2020-03-04")]["after_first_quote"] is False
    assert rows[("AAA", "2020-03-05")]["after_first_quote"] is True
    assert rows[("AAA", "2020-03-05")]["first_quote_date"].isoformat() == "2020-03-05"
    assert rows[("BBB", "2020-03-05")]["tercile"] == 1 and rows[("AAA", "2020-03-06")]["tercile"] == 3
    schema = pq.read_schema(os.path.join(tmp_path / "panel", "panel", f"{DAYS[1]}.parquet"))
    assert schema.field("tercile").type == pa.int32() and schema.field("after_first_quote").type == pa.bool_()


def test_a_root_missing_from_the_gate_files_gets_nulls_and_is_counted(tree, tmp_path):
    first = tmp_path / "first_quote.json"
    first.write_text(json.dumps({"AAA": "2020-03-05"}))
    census = tmp_path / "census.jsonl"
    census.write_text(json.dumps({"symbol": "AAA", "liquidity_tercile": 2}) + "\n")
    out = build(tree, tmp_path / "panel", first_quote_file=str(first), first_quote_field="fq", gate_field="gate",
                census_file=str(census), census_columns={"tercile": ["liquidity_tercile", "int32"]})
    row = next(r for r in read_date(tmp_path / "panel", DAYS[1]) if r["symbol"] == "BBB")
    assert row["fq"] is None and row["gate"] is None and row["tercile"] is None
    assert out["summary"]["roots_without_first_quote"] == 2 and out["summary"]["roots_without_census"] == 2


def test_a_date_file_reads_back_through_the_date_bounded_reader(tree, tmp_path):
    build(tree, tmp_path / "panel")
    path = os.path.join(tmp_path / "panel", "panel", f"{DAYS[1]}.parquet")
    table = DateBoundedParquet(path, ["symbol", "atm_iv"], {"date": {"end_before": DAYS[2]}}).read()
    assert table.num_rows == 2


def test_the_manifest_lists_every_file_with_its_sha256(tree, tmp_path):
    out = build(tree, tmp_path / "panel")
    manifest = json.load(open(tmp_path / "panel" / "manifest.json"))
    assert manifest == out["manifest"] and manifest["rows"] == 6
    assert sorted(manifest["files"]) == [f"panel/{d}.parquet" for d in DAYS]
    for name, entry in manifest["files"].items():
        raw = (tmp_path / "panel" / name).read_bytes()
        assert entry["sha256"] == hashlib.sha256(raw).hexdigest() and entry["bytes"] == len(raw)
    assert manifest["roots"]["AAA"]["rows"] == 3


# -- bounded memory and resumability -------------------------------------------------------------------


def test_the_feature_node_sees_one_root_at_a_time(tree, tmp_path):
    rows = reader(tree)
    seen = []
    original = rows.select

    def spy(symbols):
        seen.append(list(symbols))
        return original(symbols)

    rows.select = spy
    build(tree, tmp_path / "panel", rows=rows)
    assert seen == [["AAA"], ["BBB"], ["DEAD"]]


def test_a_second_run_rebuilds_nothing_and_touches_no_file(tree, tmp_path):
    path = tmp_path / "panel"
    build(tree, path)
    stamps = {p: os.stat(os.path.join(root, p)).st_mtime_ns for root, _, names in os.walk(path) for p in names}
    again = build(tree, path)["summary"]
    assert again["roots_built"] == 0 and again["roots_skipped"] == 3 and again["dates_written"] == 0
    assert again["rows"] == 6 and again["roots_empty"] == 1                   # totals still complete
    assert stamps == {p: os.stat(os.path.join(root, p)).st_mtime_ns for root, _, names in os.walk(path) for p in names}


def test_a_new_snapshot_for_one_root_rebuilds_that_root_and_the_dates_only(tree, tmp_path):
    path = tmp_path / "panel"
    build(tree, path)
    write_snapshot(tree, "20260101T000003Z-backfill-cccccccc", chains("BBB", spot=100.0, vol=0.4,
                                                                      days=("2020-03-02", "2020-03-03")))
    again = build(tree, path)["summary"]
    assert again["roots_built"] == 1 and again["roots_skipped"] == 2
    assert date_files(path) == [f"2020-03-0{n}.parquet" for n in (2, 3, 4, 5, 6)]
    assert [r["symbol"] for r in read_date(path, "2020-03-03")] == ["BBB"]
    assert again["dates_written"] == 5


def test_a_changed_gate_value_rebuilds_only_its_root(tree, tmp_path):
    path = tmp_path / "panel"
    first = tmp_path / "first_quote.json"
    kw = {"first_quote_file": str(first), "first_quote_field": "fq", "gate_field": "gate"}
    first.write_text(json.dumps({"AAA": "2020-03-05", "BBB": "2020-03-04"}))
    build(tree, path, **kw)
    first.write_text(json.dumps({"AAA": "2020-03-05", "BBB": "2020-03-06"}))
    again = build(tree, path, **kw)["summary"]
    assert again["roots_built"] == 1 and again["roots_skipped"] == 2
    assert next(r for r in read_date(path, "2020-03-05") if r["symbol"] == "BBB")["gate"] is False


def test_changed_feature_params_rebuild_every_root(tree, tmp_path):
    path = tmp_path / "panel"
    build(tree, path)
    changed = dict(FEATURES, atm_band=0.04)
    out = ChainPanelBuild("panel", {"features": changed, "path": str(path)}).run(CTX, {"records": reader(tree)})
    assert out["summary"]["roots_built"] == 3 and out["summary"]["roots_skipped"] == 0


def test_a_torn_temp_file_never_blocks_or_counts(tree, tmp_path):
    path = tmp_path / "panel"
    build(tree, path)
    (path / "roots" / ".tmp-AAA.parquet").write_bytes(b"torn")
    (path / "panel" / ".tmp-2020-03-04.parquet").write_bytes(b"torn")
    again = build(tree, path)["summary"]
    assert again["roots_skipped"] == 3 and date_files(path) == [f"{d}.parquet" for d in DAYS][0:3]


def test_repartition_false_builds_the_roots_and_leaves_the_dates(tree, tmp_path):
    path = tmp_path / "panel"
    out = build(tree, path, repartition=False)
    assert out["summary"]["roots_built"] == 3 and out["summary"]["dates_written"] == 0
    assert not (path / "panel").exists() or date_files(path) == []


def test_a_stitched_series_is_one_root_in_the_panel(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa", chains("NEWCO", days=DAYS[1:]))
    write_snapshot(directory, "20260101T000002Z-backfill-bbbbbbbb", chains("OLDCO", days=DAYS[:1]))
    renames = tmp_path / "renames.json"
    renames.write_text(json.dumps({"renames": [{"old": "OLDCO", "new": "NEWCO", "effective": DAYS[1]}]}))
    from dskit.pipeline.libs.root_stitch import RootStitch
    rows = reader(directory, renames_file=str(renames), renames_key="renames",
                  renames_sha256=RootStitch.file_digest(str(renames)))
    out = build(directory, tmp_path / "panel", rows=rows)
    assert out["summary"]["roots"] == 1 and out["manifest"]["rows"] == 3
    assert [read_date(tmp_path / "panel", d)[0]["symbol"] for d in DAYS] == ["NEWCO"] * 3


# -- refusals ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("over, fragment", [
    ({"path": ...}, "path"),
    ({"features": ...}, "features"),
    ({"features": {"symbol_field": "symbol"}}, "features"),
    ({"surprise": 1}, "surprise"),
    ({"chunk_months": 0}, "chunk_months"),
    ({"repartition": "yes"}, "repartition"),
    ({"first_quote_file": "/x"}, "first_quote_field"),
    ({"census_file": "/x"}, "census_columns"),
    ({"census_file": "/x", "census_columns": {"c": ["f", "complex"]}}, "census_columns"),
])
def test_bad_params_refuse_by_name(tmp_path, over, fragment):
    params = {k: v for k, v in {**panel_params(tmp_path / "panel"), **over}.items() if v is not ...}
    with pytest.raises(ConfigError, match=fragment):
        ChainPanelBuild("panel", params)


def test_a_records_input_that_cannot_select_roots_refuses(tree, tmp_path):
    with pytest.raises(ValueError, match="select"):
        ChainPanelBuild("panel", panel_params(tmp_path / "panel")).run(CTX, {"records": [{"symbol": "AAA"}]})


def test_a_root_file_written_for_other_params_is_rebuilt_not_trusted(tree, tmp_path):
    path = tmp_path / "panel"
    build(tree, path)
    target = path / "roots" / "AAA.parquet"
    table = pq.read_table(target)
    pq.write_table(table.replace_schema_metadata({"dskit.panel.meta": "{}"}), target)
    again = build(tree, path)["summary"]
    assert again["roots_built"] == 1


def test_a_reuse_break_makes_two_roots_and_the_later_company_cannot_borrow_the_earlier_first_quote(tmp_path):
    directory = tmp_path / "theta"
    write_snapshot(directory, "20260101T000001Z-backfill-aaaaaaaa", chains("AI", days=DAYS[1:]))
    write_snapshot(directory, "20260101T000002Z-backfill-bbbbbbbb", chains("AI", days=DAYS[:1], vol=0.28))
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"renames": [], "breaks": [
        {"root": "AI", "effective": DAYS[1], "earlier_as": "AI.before"}]}))
    from dskit.pipeline.libs.root_stitch import RootStitch
    rows = reader(directory, renames_file=str(mapping), renames_key="renames", breaks_key="breaks",
                  renames_sha256=RootStitch.file_digest(str(mapping)))
    first = tmp_path / "first_quote.json"
    first.write_text(json.dumps({"AI": "2020-01-02"}))
    out = build(directory, tmp_path / "panel", rows=rows, first_quote_file=str(first),
                first_quote_field="fq", gate_field="gate")
    assert out["summary"]["roots"] == 2
    later = next(r for r in read_date(tmp_path / "panel", DAYS[1]) if r["symbol"] == "AI")
    earlier = read_date(tmp_path / "panel", DAYS[0])[0]
    assert earlier["symbol"] == "AI.before" and earlier["fq"].isoformat() == "2020-01-02"
    assert later["fq"].isoformat() == DAYS[1] and later["gate"] is True
