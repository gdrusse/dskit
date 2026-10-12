"""Per-snapshot Parquet export and its incremental quality checks (ADR-0265 Amendment 2).

Snapshots are landed through the real WORM writers (``build_manifest`` /
``write_snapshot``) with the ThetaData row shape, so the committed-snapshot
rule, the raw record count and the observation reader are exercised as in
production.
"""

import datetime
import json
import os
import subprocess
import sys
import tempfile

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from dskit.onboarding import OnboardingRoot  # noqa: E402
from dskit.onboarding.base import AssetError  # noqa: E402
from dskit.onboarding.codec import open_text_writer  # noqa: E402
from dskit.onboarding.libs import parquet_export as pe  # noqa: E402
from dskit.onboarding.libs.thetadata import OPTION_FIELDS, ThetaOptionEodExport  # noqa: E402
from dskit.onboarding.snapshot import build_manifest, write_snapshot  # noqa: E402

SOURCE, STREAM = "theta", "option_eod"
EXPORT = "dskit.onboarding.libs.thetadata:ThetaOptionEodExport"
SMALL = "tests.onboarding.test_parquet_export:SmallExport"
START = datetime.date(2025, 6, 2)


class SmallExport(ThetaOptionEodExport):
    """Tiny blocks and row groups, so a few thousand rows cross every boundary."""

    LINE_BLOCK = 700
    ROW_GROUP_ROWS = 1000


def weekdays(n, start=START):
    out, day = [], start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += datetime.timedelta(days=1)
    return out


def option_row(symbol, day, i):
    strike = 90.0 + (i // 2)
    bid = round(1.0 + (i % 7) * 0.5, 2)
    row = {name: 0.0 for name in OPTION_FIELDS}
    row.update({
        "symbol": symbol, "expiration": (day + datetime.timedelta(days=30)).isoformat(),
        "strike": strike, "right": "call" if i % 2 == 0 else "put", "date": day.isoformat(),
        "created": None, "last_trade": None,
        "timestamp": day.isoformat() + "T00:00:00.000",
        "underlying_timestamp": day.isoformat() + "T17:15:00.616",
        "volume": i, "count": 1, "bid_size": 10, "ask_size": 12, "open_interest": 100 + i,
        "bid_exchange": 9, "ask_exchange": 42, "bid_condition": 50, "ask_condition": 50,
        "bid": bid, "ask": round(bid + 0.1, 2), "implied_vol": 0.3, "iv_error": 0.0,
        "underlying_price": 100.5, "delta": 0.5,
    })
    return row


def chain(symbols=("AAA", "BBB"), days=30, per_date=60, start=START, skip=()):
    rows = []
    for symbol in symbols:
        for day in weekdays(days, start):
            if day in skip:
                continue
            rows.extend(option_row(symbol, day, i) for i in range(per_date))
    return rows


_clock = [0]


def land(root, rows, *, payload_rows=None, source=SOURCE, stream=STREAM, observations=True, payload_name=None):
    """Commit one snapshot the way acquire does; returns its acq_id."""
    _clock[0] += 60
    stamp = (datetime.datetime(2026, 10, 12, 16, 0, 0, tzinfo=datetime.timezone.utc)
             + datetime.timedelta(seconds=_clock[0])).isoformat()
    payload_rows = rows if payload_rows is None else payload_rows
    os.makedirs(root.raw_dir(source), exist_ok=True)
    staged = tempfile.mkdtemp(dir=root.raw_dir(source), prefix=".stage-")
    payload = os.path.join(staged, "payload")
    os.makedirs(payload)
    with open_text_writer(os.path.join(payload, f"{payload_name or stream}.jsonl.gz"), "gzip") as fh:
        fh.write(json.dumps({"protocol": 1, "type": "SCHEMA", "stream": stream,
                             "schema": {"fields": list(OPTION_FIELDS)}}, sort_keys=True) + "\n")
        for row in payload_rows:
            fh.write(json.dumps({"protocol": 1, "type": "RECORD", "stream": stream,
                                 "effective_date": row.get("date", "2025-06-02"), "kind": "observation",
                                 "data": row}, sort_keys=True) + "\n")
    manifest = build_manifest(payload, source=source, mode="backfill", acquired_at=stamp)
    acq_id, _ = write_snapshot(root, staged, manifest)
    if observations:
        target = root.records_dir(source, acq_id)
        os.makedirs(target)
        with open_text_writer(os.path.join(target, f"{stream}.jsonl.gz"), "gzip") as fh:
            for row in rows:
                fh.write(json.dumps({"acquired_at": stamp, "data": row, "effective_date": row.get("date", "2025-06-02"),
                                     "kind": "observation", "mode": "backfill", "stream": stream},
                                    sort_keys=True) + "\n")
    return acq_id


@pytest.fixture
def ob(tmp_path):
    return OnboardingRoot.create(str(tmp_path / "ob"))


@pytest.fixture
def out(tmp_path):
    return str(tmp_path / "parquet")


def cli(ob, out, *extra, export=SMALL):
    return pe.main(["--export-class", export, "--root", ob.root, "--source", SOURCE,
                    "--stream", STREAM, "--out", out, *extra])


def lines(out):
    with open(os.path.join(out, "verify.jsonl"), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def parquet_path(out, acq_id):
    return os.path.join(out, SOURCE, f"{acq_id}.parquet")


# -- conversion -------------------------------------------------------------


def test_every_committed_snapshot_becomes_one_immutable_parquet(ob, out):
    ids = [land(ob, chain(symbols=(s,), days=10)) for s in ("AAA", "BBB", "CCC")]
    assert cli(ob, out) == 0
    assert sorted(os.listdir(os.path.join(out, SOURCE))) == sorted(f"{i}.parquet" for i in ids)
    recorded = lines(out)
    assert [r["snapshot"] for r in recorded] == sorted(ids)  # acquisition order
    for acq_id in ids:
        table = pq.read_table(parquet_path(out, acq_id))
        assert table.num_rows == 600


def test_schema_types_and_metadata_follow_the_study(ob, out):
    acq_id = land(ob, chain(symbols=("AAA",), days=5))
    assert cli(ob, out) == 0
    path = parquet_path(out, acq_id)
    schema = pq.read_schema(path)
    assert schema.names == list(OPTION_FIELDS) + ["acquired_at"]
    types = {field.name: field.type for field in schema}
    assert types["date"] == pa.date32() and types["expiration"] == pa.date32()
    assert types["timestamp"] == pa.timestamp("ms")
    assert types["underlying_timestamp"] == pa.timestamp("ms")
    assert types["acquired_at"] == pa.timestamp("ms", "UTC")  # parquet has no seconds unit
    assert types["volume"] == pa.int32() and types["bid_size"] == pa.int32()
    assert types["bid_exchange"] == pa.int16() and types["ask_condition"] == pa.int16()
    assert types["strike"] == pa.float64() and types["delta"] == pa.float64()
    assert types["symbol"] == pa.string() and types["right"] == pa.string()
    assert types["created"] == pa.string() and types["last_trade"] == pa.string()
    table = pq.read_table(path)
    assert table.column("created").null_count == table.num_rows
    meta = {k.decode(): v.decode() for k, v in pq.read_metadata(path).metadata.items()}
    manifest = os.path.join(ob.snapshot_dir(SOURCE, acq_id), "manifest.json")
    from dskit.onboarding.base import file_digest
    assert meta["dskit.export.manifest_sha256"] == file_digest(manifest)
    assert meta["dskit.export.schema_version"] == str(SmallExport.SCHEMA_VERSION)
    assert meta["dskit.export.acq_id"] == acq_id


def test_values_survive_the_round_trip(ob, out):
    rows = chain(symbols=("AAA",), days=2, per_date=4)
    rows[1]["bid"] = None
    acq_id = land(ob, rows)
    assert cli(ob, out) == 0
    table = pq.read_table(parquet_path(out, acq_id))
    assert table.column("symbol").to_pylist() == [r["symbol"] for r in rows]
    assert table.column("bid").to_pylist() == [r["bid"] for r in rows]
    assert table.column("date").to_pylist()[0] == START
    assert table.column("timestamp").to_pylist()[0] == datetime.datetime(2025, 6, 2)
    from dskit.onboarding.snapshot import read_manifest
    stamped = read_manifest(ob.snapshot_dir(SOURCE, acq_id))["acquired_at"]
    assert table.column("acquired_at").to_pylist()[0].isoformat() == stamped


def test_row_groups_and_blocks_cross_boundaries(ob, out):
    acq_id = land(ob, chain(symbols=("AAA",), days=10))  # 600 rows -> one block boundary at 700? use 2 symbols
    acq2 = land(ob, chain(symbols=("BBB", "CCC"), days=15))  # 1800 rows
    assert cli(ob, out) == 0
    meta = pq.read_metadata(parquet_path(out, acq2))
    assert meta.num_rows == 1800 and meta.num_row_groups == 2
    assert [meta.row_group(i).num_rows for i in range(2)] == [1000, 800]
    assert pq.read_metadata(parquet_path(out, acq_id)).num_rows == 600


def test_uncommitted_and_staging_directories_are_skipped(ob, out):
    good = land(ob, chain(symbols=("AAA",), days=5))
    land(ob, chain(symbols=("BBB",), days=5), observations=False)  # raw landed, rows not yet moved
    os.makedirs(os.path.join(ob.raw_dir(SOURCE), ".stage-leftover", "payload"))
    stray = os.path.join(ob.root, "observations", SOURCE, ".stage-leftover")
    os.makedirs(stray)
    with open_text_writer(os.path.join(stray, f"{STREAM}.jsonl.gz"), "gzip") as fh:
        fh.write("not json\n")
    orphan = os.path.join(ob.root, "observations", SOURCE, "20260101T000000Z-backfill-deadbeef")
    os.makedirs(orphan)  # observations with no raw manifest
    with open_text_writer(os.path.join(orphan, f"{STREAM}.jsonl.gz"), "gzip") as fh:
        fh.write("not json\n")
    assert cli(ob, out) == 0
    assert os.listdir(os.path.join(out, SOURCE)) == [f"{good}.parquet"]


def test_rerun_is_idempotent_and_leaves_files_alone(ob, out):
    acq_id = land(ob, chain(symbols=("AAA",), days=5))
    assert cli(ob, out) == 0
    before = os.stat(parquet_path(out, acq_id)).st_mtime_ns
    assert cli(ob, out) == 0
    assert os.stat(parquet_path(out, acq_id)).st_mtime_ns == before
    assert len(lines(out)) == 1
    land(ob, chain(symbols=("BBB",), days=5))
    assert cli(ob, out) == 0
    assert len(lines(out)) == 2


def test_existing_output_with_other_metadata_is_refused_not_overwritten(ob, out):
    acq_id = land(ob, chain(symbols=("AAA",), days=5))
    os.makedirs(os.path.join(out, SOURCE))
    path = parquet_path(out, acq_id)
    pq.write_table(pa.table({"x": [1]}).replace_schema_metadata({"dskit.export.manifest_sha256": "0" * 64}), path)
    before = open(path, "rb").read()
    assert cli(ob, out) == 1
    assert open(path, "rb").read() == before
    assert not os.path.exists(os.path.join(out, "verify.jsonl")) or lines(out) == []


def test_a_schema_version_bump_is_a_metadata_mismatch(ob, out):
    land(ob, chain(symbols=("AAA",), days=5))
    assert cli(ob, out) == 0

    class V2(SmallExport):
        SCHEMA_VERSION = 99

    export = V2(ob.root, SOURCE, STREAM, out)
    with pytest.raises(AssetError, match="schema_version"):
        export.run()


def test_no_temp_file_remains_after_a_successful_run(ob, out):
    land(ob, chain(symbols=("AAA",), days=5))
    assert cli(ob, out) == 0
    assert not [n for n in os.listdir(os.path.join(out, SOURCE)) if n.startswith(".")]


def test_a_truncated_observations_member_is_an_error_not_a_short_parquet(ob, out):
    acq_id = land(ob, chain(symbols=("AAA",), days=5))
    member = os.path.join(ob.records_dir(SOURCE, acq_id), f"{STREAM}.jsonl.gz")
    data = open(member, "rb").read()
    open(member, "wb").write(data[: len(data) // 2])
    assert cli(ob, out) == 1
    assert not os.path.exists(parquet_path(out, acq_id))


# -- refusals ---------------------------------------------------------------


def test_unexpected_field_refuses_naming_it_and_leaves_no_output(ob, out):
    rows = chain(symbols=("AAA",), days=5)
    rows[200]["brand_new_greek"] = 1.5
    acq_id = land(ob, rows)
    with pytest.raises(AssetError, match="brand_new_greek"):
        SmallExport(ob.root, SOURCE, STREAM, out).run()
    assert not os.path.exists(parquet_path(out, acq_id))
    assert not [n for n in os.listdir(os.path.join(out, SOURCE)) if n.startswith(".")]
    assert cli(ob, out) == 1


def test_unexpected_envelope_key_refuses_naming_it(ob, out):
    rows = chain(symbols=("AAA",), days=2, per_date=3)
    acq_id = land(ob, rows, observations=False)
    target = ob.records_dir(SOURCE, acq_id)
    os.makedirs(target)
    with open_text_writer(os.path.join(target, f"{STREAM}.jsonl.gz"), "gzip") as fh:
        for row in rows:
            fh.write(json.dumps({"acquired_at": "2026-10-12T16:00:00+00:00", "data": row, "stowaway": 1,
                                 "effective_date": row.get("date", "2025-06-02"), "kind": "observation",
                                 "mode": "backfill", "stream": STREAM}) + "\n")
    with pytest.raises(AssetError, match="stowaway"):
        SmallExport(ob.root, SOURCE, STREAM, out).run()


def test_integer_overflow_raises_not_wraps(ob, out):
    rows = chain(symbols=("AAA",), days=3, per_date=5)
    rows[4]["volume"] = 3_000_000_000
    acq_id = land(ob, rows)
    with pytest.raises(AssetError, match="volume"):
        SmallExport(ob.root, SOURCE, STREAM, out).run()
    assert not os.path.exists(parquet_path(out, acq_id))


def test_unparseable_date_raises(ob, out):
    rows = chain(symbols=("AAA",), days=3, per_date=5)
    rows[2]["date"] = "2025-13-45"
    land(ob, rows)
    with pytest.raises(AssetError, match="date"):
        SmallExport(ob.root, SOURCE, STREAM, out).run()


def test_missing_source_directory_is_an_error_exit(ob, out):
    assert cli(ob, out) == 1  # no observations directory for the source: a typo'd root, not an empty store


# -- the quality checks -----------------------------------------------------


def test_clean_snapshot_is_quiet_and_records_numbers_and_thresholds(ob, out):
    rows = chain()
    acq_id = land(ob, rows)
    assert cli(ob, out) == 0
    (line,) = lines(out)
    assert line["snapshot"] == acq_id and line["source"] == SOURCE and line["stream"] == STREAM
    assert line["rows"] == line["expected_rows"] == len(rows)
    assert line["alarms"] == []
    assert line["files"][0]["bytes"] == os.path.getsize(parquet_path(out, acq_id))
    assert line["seconds"] >= 0
    q, t = line["quality"], line["thresholds"]
    for name in ("duplicate_keys", "crossed_share", "locked_share", "null_quote_share", "zero_bid_share",
                 "iv_error_100_share", "future_date_rows", "worst_missing_weekday_share"):
        assert name in q
    assert q["duplicate_keys"] == 0 and q["crossed_share"] == 0.0
    assert t["max_crossed_share"] == 1e-4 and t["max_locked_share"] == 1e-3
    assert t["max_null_quote_share"] == 0.10 and t["max_zero_bid_share"] == 0.60
    assert t["max_iv_error_100_share"] == 0.15 and t["max_missing_weekday_share"] == 0.08
    assert t["sealed_from"] == "2026-01-01" and t["max_rows_per_date_ratio"] == 10


def mutate(rows, every, change):
    for i, row in enumerate(rows):
        if i % every == 0:
            change(row)
    return rows


BAD = {
    "duplicate_keys": lambda: chain() + [dict(chain()[0])],
    "crossed_quotes": lambda: mutate(chain(), 100, lambda r: r.update(bid=5.0, ask=4.0)),
    "locked_quotes": lambda: mutate(chain(), 100, lambda r: r.update(bid=5.0, ask=5.0)),
    "null_quotes": lambda: mutate(chain(), 3, lambda r: r.update(bid=None, ask=None)),
    "zero_bid": lambda: mutate(chain(), 1, lambda r: r.update(bid=0.0, ask=0.1)),
    "iv_error_100": lambda: mutate(chain(), 4, lambda r: r.update(iv_error=100.0)),
    "future_dates": lambda: chain(start=datetime.date(2026, 1, 5)),
    "date_coverage": lambda: chain(skip=set(weekdays(40)[10:20]), days=40),
}


@pytest.mark.parametrize("alarm", sorted(BAD))
def test_each_alarm_fires_on_its_crafted_snapshot_and_exits_3(ob, out, alarm):
    land(ob, BAD[alarm]())
    assert cli(ob, out) == 3
    (line,) = lines(out)
    assert alarm in line["alarms"]
    assert os.path.exists(parquet_path(out, line["snapshot"]))  # converted and recorded before the stop


@pytest.mark.parametrize("at", [10, 999])  # inside a row group; on the 1000-row boundary, mid-unit
def test_a_duplicate_inside_a_unit_is_found_even_across_a_row_group_edge(ob, out, at):
    rows = chain()
    rows.insert(at + 1, dict(rows[at]))
    land(ob, rows)
    assert cli(ob, out) == 3
    (line,) = lines(out)
    assert line["alarms"] == ["duplicate_keys"] and line["quality"]["duplicate_keys"] == 1


def test_row_count_mismatch_against_the_raw_records_alarms(ob, out):
    rows = chain()
    land(ob, rows[:-5], payload_rows=rows)  # observations lost five rows the raw payload still holds
    assert cli(ob, out) == 3
    (line,) = lines(out)
    assert "row_count_mismatch" in line["alarms"]
    assert line["expected_rows"] == len(rows) and line["rows"] == len(rows) - 5


def test_rows_per_date_jump_against_the_previous_snapshot_of_the_symbol(ob, out):
    land(ob, chain(symbols=("AAA", "BBB"), per_date=60))
    land(ob, chain(symbols=("AAA",), per_date=70, start=datetime.date(2025, 4, 1)))  # ratio 1.17: quiet
    assert cli(ob, out) == 0
    land(ob, chain(symbols=("AAA", "BBB"), per_date=800, start=datetime.date(2025, 1, 1), days=10))
    assert cli(ob, out) == 3
    last = lines(out)[-1]
    assert "rows_per_date_jump" in last["alarms"]
    assert sorted(last["quality"]["jumped_symbols"]) == ["AAA", "BBB"]


def test_a_jump_between_small_chains_is_only_logged(ob, out):
    land(ob, chain(symbols=("AAA",), per_date=20))
    land(ob, chain(symbols=("AAA",), per_date=200, start=datetime.date(2025, 1, 1), days=10))
    assert cli(ob, out) == 0
    assert lines(out)[-1]["quality"]["jumped_symbols"] == []
    assert lines(out)[-1]["quality"]["max_rows_per_date_ratio_seen"] == pytest.approx(10.0)


def test_thresholds_are_overridable_by_a_params_file(ob, out, tmp_path):
    land(ob, mutate(chain(), 100, lambda r: r.update(bid=5.0, ask=4.0)))
    params = tmp_path / "params.json"
    params.write_text(json.dumps({"max_crossed_share": 0.5}))
    assert cli(ob, out, "--params", str(params)) == 0
    assert lines(out)[0]["thresholds"]["max_crossed_share"] == 0.5


def test_unknown_param_is_refused(ob, out, tmp_path):
    land(ob, chain())
    params = tmp_path / "params.json"
    params.write_text(json.dumps({"max_crossd_share": 0.5}))
    assert cli(ob, out, "--params", str(params)) == 1
    with pytest.raises(AssetError, match="max_crossd_share"):
        ThetaOptionEodExport(ob.root, SOURCE, STREAM, out, params={"max_crossd_share": 0.5})


def test_stops_at_the_first_alarm_and_a_rerun_continues(ob, out):
    land(ob, chain(symbols=("AAA",)))
    bad = land(ob, mutate(chain(symbols=("BBB",)), 100, lambda r: r.update(bid=5.0, ask=4.0)))
    later = land(ob, chain(symbols=("CCC",)))
    assert cli(ob, out) == 3
    assert lines(out)[-1]["snapshot"] == bad
    assert not os.path.exists(parquet_path(out, later))
    assert cli(ob, out) == 0  # the alarmed snapshot is recorded; the driver restart is the acknowledgement
    assert os.path.exists(parquet_path(out, later))


def test_max_snapshots_bounds_one_invocation(ob, out):
    for s in ("AAA", "BBB", "CCC"):
        land(ob, chain(symbols=(s,), days=5))
    assert cli(ob, out, "--max-snapshots", "2") == 0
    assert len(lines(out)) == 2
    assert cli(ob, out) == 0
    assert len(lines(out)) == 3


def test_checks_hook_receives_only_the_columns_it_names(ob, out):
    seen = {}

    class Probe(SmallExport):
        def checks(self, batches, report):
            seen["names"] = next(iter(batches)).column_names
            return []

    land(ob, chain(symbols=("AAA",), days=5))
    assert Probe(ob.root, SOURCE, STREAM, out).run() == 0
    assert "delta" not in seen["names"] and "bid" in seen["names"]


def test_a_second_writer_on_the_same_out_dir_is_refused(ob, out):
    land(ob, chain(symbols=("AAA",), days=5))
    first = SmallExport(ob.root, SOURCE, STREAM, out)
    with first._lock():
        with pytest.raises(AssetError, match="already running"):
            SmallExport(ob.root, SOURCE, STREAM, out).run()


# -- verify -----------------------------------------------------------------


def test_verify_recounts_footers_and_summarises(ob, out, capsys):
    for s in ("AAA", "BBB"):
        land(ob, chain(symbols=(s,), days=5))
    assert cli(ob, out) == 0
    capsys.readouterr()
    assert pe.main(["verify", "--out", out]) == 0
    text = capsys.readouterr().out
    assert SOURCE in text and "snapshots 2" in text and "rows 600" in text
    assert "bytes/row" in text and "alarms 0" in text


def test_verify_flags_a_changed_count_a_missing_file_and_an_unlisted_file(ob, out, capsys):
    ids = [land(ob, chain(symbols=(s,), days=5)) for s in ("AAA", "BBB")]
    assert cli(ob, out) == 0
    capsys.readouterr()
    log = os.path.join(out, "verify.jsonl")
    recorded = lines(out)
    recorded[0]["rows"] += 1
    with open(log, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in recorded))
    os.remove(parquet_path(out, ids[1]))
    open(os.path.join(out, SOURCE, "stray.parquet"), "wb").close()
    assert pe.main(["verify", "--out", out]) == 1
    text = capsys.readouterr().out
    assert "rows" in text and ids[0] in text and ids[1] in text and "stray.parquet" in text


# -- generic base -----------------------------------------------------------


def test_the_base_class_is_a_hook_contract_not_instantiable_without_a_schema(ob, out):
    with pytest.raises(TypeError):
        pe.SnapshotParquetExport(ob.root, SOURCE, STREAM, out)


def test_module_cli_runs_as_a_subprocess_and_exits_with_the_code(ob, out):
    land(ob, mutate(chain(), 100, lambda r: r.update(bid=5.0, ask=4.0)))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    done = subprocess.run(
        [sys.executable, "-m", "dskit.onboarding.libs.parquet_export", "--export-class", EXPORT,
         "--root", ob.root, "--source", SOURCE, "--stream", STREAM, "--out", out],
        capture_output=True, text=True, env=env)
    assert done.returncode == 3, done.stderr
    assert "crossed_quotes" in done.stdout + done.stderr


# -- review round: null quotes by quoted era, overlap, sentinel, torn log, missing fields ---------


def pre_quote(rows, n_dates):
    """Blank the quotes of the first ``n_dates`` dates per symbol (the vendor has none before listing)."""
    firsts = {}
    for row in rows:
        firsts.setdefault(row["symbol"], sorted({r["date"] for r in rows if r["symbol"] == row["symbol"]})[:n_dates])
        if row["date"] in firsts[row["symbol"]]:
            row.update(bid=None, ask=None)
    return rows


def test_null_quotes_are_measured_only_from_each_symbols_first_quoted_date(ob, out):
    land(ob, pre_quote(chain(symbols=("AAA",), days=30), 10))
    assert cli(ob, out) == 0
    (line,) = lines(out)
    assert line["quality"]["pre_quote_rows"] == 600 and line["quality"]["null_quote_share"] == 0.0
    state = json.load(open(os.path.join(out, "first_quote.json")))
    assert state == {"AAA": weekdays(30)[10].isoformat()}


def test_an_older_all_null_snapshot_after_the_quoted_era_is_pre_quote_not_an_alarm(ob, out):
    land(ob, chain(symbols=("AAA",), days=30))
    older = chain(symbols=("AAA",), days=30, start=datetime.date(2025, 1, 2))
    land(ob, mutate(older, 1, lambda r: r.update(bid=None, ask=None)))
    assert cli(ob, out) == 0
    assert lines(out)[1]["quality"]["pre_quote_rows"] == 1800 and lines(out)[1]["alarms"] == []


def test_an_all_null_snapshot_of_a_symbol_never_quoted_still_alarms(ob, out):
    land(ob, mutate(chain(symbols=("AAA",), days=30), 1, lambda r: r.update(bid=None, ask=None)))
    assert cli(ob, out) == 3
    assert "null_quotes" in lines(out)[0]["alarms"] and lines(out)[0]["quality"]["pre_quote_rows"] == 0


def test_null_quotes_inside_the_quoted_era_alarm_at_the_new_default(ob, out):
    rows = chain(symbols=("AAA",), days=30)
    land(ob, mutate(rows, 8, lambda r: r.update(bid=None, ask=None)))  # 12.5% > 10%
    assert cli(ob, out) == 3
    assert lines(out)[0]["alarms"] == ["null_quotes"]


def test_a_new_snapshot_overlapping_an_earlier_window_of_the_same_symbol_alarms_naming_both(ob, out):
    first = land(ob, chain(symbols=("AAA",), days=30))
    second = land(ob, chain(symbols=("AAA",), days=30, start=weekdays(30)[20]))  # re-pull of the last 10 dates
    assert cli(ob, out) == 3
    last = lines(out)[-1]
    assert last["alarms"] == ["overlapping_symbol_window"]
    assert first in last["detail"]["overlapping_symbol_window"] and second in last["detail"]["overlapping_symbol_window"]
    meta = pq.read_metadata(parquet_path(out, first)).metadata
    assert json.loads(meta[b"dskit.export.windows"]) == {"AAA": [weekdays(30)[0].isoformat(), weekdays(30)[-1].isoformat()]}
    assert lines(out)[0]["windows"] == {"AAA": [weekdays(30)[0].isoformat(), weekdays(30)[-1].isoformat()]}


def test_adjacent_windows_of_one_symbol_are_quiet_and_verify_rechecks_overlaps(ob, out, capsys):
    land(ob, chain(symbols=("AAA",), days=30))
    land(ob, chain(symbols=("AAA",), days=30, start=datetime.date(2025, 1, 2)))
    assert cli(ob, out) == 0
    capsys.readouterr()
    assert pe.main(["verify", "--out", out]) == 0
    land(ob, chain(symbols=("AAA",), days=5, start=weekdays(30)[10]))
    assert cli(ob, out) == 3
    capsys.readouterr()
    assert pe.main(["verify", "--out", out]) == 1
    assert "overlap" in capsys.readouterr().out


def test_vendor_sentinel_values_are_counted_not_modified_and_alarm_above_the_share(ob, out):
    rows = chain(symbols=("AAA",), days=10)
    mutate(rows, 50, lambda r: r.update(implied_vol=214748.36))  # 2%
    acq_id = land(ob, rows)
    assert cli(ob, out) == 0
    q = lines(out)[0]["quality"]
    assert q["sentinel_rows"] == 12 and q["sentinel_share"] == pytest.approx(0.02)
    values = pq.read_table(parquet_path(out, acq_id), columns=["implied_vol"]).column(0).to_pylist()
    assert max(values) == 214748.36
    land(ob, mutate(chain(symbols=("BBB",), days=10), 5, lambda r: r.update(d1=-214748.36)))  # 20%
    assert cli(ob, out) == 3
    assert lines(out)[-1]["alarms"] == ["sentinel_values"]


def test_unknown_expected_row_count_alarms(ob, out):
    land(ob, chain(symbols=("AAA",), days=5), payload_name="other")
    assert cli(ob, out) == 3
    assert lines(out)[0]["alarms"] == ["expected_rows_unavailable"]


def test_raw_only_snapshots_are_listed_by_verify_and_the_run(ob, out, capsys):
    land(ob, chain(symbols=("AAA",), days=5))
    half = land(ob, chain(symbols=("BBB",), days=5), observations=False)
    assert cli(ob, out) == 0
    ran = capsys.readouterr().out
    assert "raw_only" in ran and half in ran
    assert pe.main(["verify", "--out", out, "--root", ob.root, "--source", SOURCE, "--stream", STREAM]) == 0
    text = capsys.readouterr().out
    assert "raw_only" in text and half in text


def test_rows_missing_fields_are_counted_and_explicit_nulls_are_not(ob, out):
    rows = chain(symbols=("AAA",), days=5, per_date=10)
    del rows[3]["gamma"]
    del rows[4]["gamma"]
    rows[5]["delta"] = None
    acq_id = land(ob, rows)
    assert cli(ob, out) == 0
    assert lines(out)[0]["missing_fields"] == {"gamma": 2}
    assert pq.read_table(parquet_path(out, acq_id)).num_rows == len(rows)


@pytest.mark.parametrize("key", ["symbol", "expiration", "strike", "right", "date"])
def test_a_row_missing_a_key_field_refuses_the_conversion(ob, out, key):
    rows = chain(symbols=("AAA",), days=3, per_date=5)
    del rows[2][key]
    acq_id = land(ob, rows)
    assert cli(ob, out) == 1
    assert not os.path.exists(parquet_path(out, acq_id))


def test_a_torn_last_line_in_the_log_is_reported_and_repaired(ob, out, capsys):
    land(ob, chain(symbols=("AAA",), days=5))
    assert cli(ob, out) == 0
    with open(os.path.join(out, "verify.jsonl"), "a", encoding="utf-8") as fh:
        fh.write('{"snapshot": "20260101T000000Z-backfill-tornnnnn", "sou')
    land(ob, chain(symbols=("BBB",), days=5))
    capsys.readouterr()
    assert cli(ob, out) == 0
    assert "torn" in capsys.readouterr().out
    assert len(lines(out)) == 2  # every line parses again


def test_a_corrupt_line_in_the_middle_of_the_log_is_an_error(ob, out):
    land(ob, chain(symbols=("AAA",), days=5))
    assert cli(ob, out) == 0
    log = os.path.join(out, "verify.jsonl")
    body = open(log, encoding="utf-8").read()
    open(log, "w", encoding="utf-8").write("garbage\n" + body)
    assert cli(ob, out) == 1


def test_a_unit_that_reappears_without_repeating_a_key_is_not_a_duplicate(ob, out):
    rows = chain(symbols=("AAA",), days=3)
    day1 = [r for r in rows if r["date"] == rows[0]["date"]]
    rest = [r for r in rows if r["date"] != rows[0]["date"]]
    land(ob, day1[:30] + rest + day1[30:])
    assert cli(ob, out) == 0
    q = lines(out)[0]["quality"]
    assert q["duplicate_keys"] == 0 and q["reappeared_units"] == 1


def test_a_repeated_key_in_a_reappearing_unit_counts_once(ob, out):
    rows = chain(symbols=("AAA",), days=3)
    land(ob, rows + [dict(rows[0])])
    assert cli(ob, out) == 3
    assert lines(out)[0]["quality"]["duplicate_keys"] == 1


def test_footer_declares_timezones_and_acquired_at_is_utc_ms(ob, out):
    acq_id = land(ob, chain(symbols=("AAA",), days=2))
    assert cli(ob, out) == 0
    meta = pq.read_metadata(parquet_path(out, acq_id)).metadata
    zones = json.loads(meta[b"dskit.export.timezones"])
    assert zones["timestamp"].startswith("America/New_York") and zones["acquired_at"] == "UTC"


def test_a_stale_temp_file_from_a_crash_is_ignored_and_cleaned(ob, out):
    acq_id = land(ob, chain(symbols=("AAA",), days=5))
    os.makedirs(os.path.join(out, SOURCE))
    stale = os.path.join(out, SOURCE, f".{acq_id}.parquet.tmp")
    open(stale, "wb").write(b"half a footer")
    assert cli(ob, out) == 0
    assert not os.path.exists(stale) and pq.read_metadata(parquet_path(out, acq_id)).num_rows == 300


def test_a_temp_file_never_counts_as_an_unlisted_output_in_verify(ob, out, capsys):
    land(ob, chain(symbols=("AAA",), days=5))
    assert cli(ob, out) == 0
    open(os.path.join(out, SOURCE, ".leftover.parquet.tmp"), "wb").write(b"x")
    capsys.readouterr()
    assert pe.main(["verify", "--out", out]) == 0


def test_params_of_the_wrong_type_are_one_clean_error_line(ob, out, tmp_path, capsys):
    land(ob, chain(symbols=("AAA",), days=5))
    for body in ('{"max_crossed_share": "high"}', '{"sealed_from": "not a date"}', '[1, 2]', "{not json"):
        params = tmp_path / "p.json"
        params.write_text(body)
        capsys.readouterr()
        assert cli(ob, out, "--params", str(params)) == 1
        err = capsys.readouterr().err.strip()
        assert err.startswith("error:") and "\n" not in err and "Traceback" not in err
