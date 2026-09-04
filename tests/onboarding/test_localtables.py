"""libs/localtables.py: parquet / newline-JSON table directories through the
contract (ADR-0076) — the conformance shape of test_localfiles.py, plus the
format, layout, instant-unit and stamping rules this pack adds."""

import builtins
import gzip
import json
from datetime import datetime, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dskit.assets.base import AssetError
from dskit.onboarding import (
    OnboardingRoot,
    check_config,
    check_message,
    parse_utc,
    resolve_connector,
    run_acquisition,
    scan_stream,
)
from dskit.onboarding.libs import localtables
from dskit.onboarding.libs.localtables import LocalTablesConnector

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _epoch_ms(iso):
    """Exact epoch milliseconds of an ISO instant — computed here, independently
    of the module under test (deliberate restatement: the pin)."""
    delta = parse_utc(iso) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


def _write_ndjson(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _write_gz_members(path, *members):
    """One gzip MEMBER per append — the archive shape the corpus has."""
    with open(path, "wb") as fh:
        for rows in members:
            text = "".join(json.dumps(r) + "\n" for r in rows)
            fh.write(gzip.compress(text.encode("utf-8")))


def _read(conn, config, streams, state=None, mode="live"):
    msgs = list(conn.read(config, streams, state or {}, mode))
    for m in msgs:
        assert check_message(m) is not None  # every message envelope-valid
    return msgs


def _records(conn, config, streams, state=None, mode="live"):
    return [m for m in _read(conn, config, streams, state, mode) if m["type"] == "RECORD"]


@pytest.fixture
def conn():
    return LocalTablesConnector()


@pytest.fixture
def text_dir(tmp_path):
    """Directory layout, text formats only: ``bars`` has two shards (unsorted,
    one instant shared across shards), ``outlook`` is a two-member gzip."""
    d = tmp_path / "tables"
    (d / "bars").mkdir(parents=True)
    _write_ndjson(d / "bars" / "aaa.ndjson", [
        {"ts": "2026-01-04T00:00:00+00:00", "v": 4},
        {"ts": "2026-01-02T00:00:00+00:00", "v": 2},
    ])
    _write_ndjson(d / "bars" / "bbb.jsonl", [
        {"ts": "2026-01-03T00:00:00+00:00", "v": 3},
        {"ts": "2026-01-02T00:00:00+00:00", "v": 22},  # ties aaa's row 2 on instant
    ])
    (d / "outlook").mkdir()
    _write_gz_members(
        d / "outlook" / "x.ndjson.gz",
        [{"ts": "2026-02-01", "view": "up"}],
        [{"ts": "2026-03-01", "view": "flat"}],
    )
    (d / "README.txt").write_text("a file under a directory layout is not a stream")
    (d / ".hidden").mkdir()
    return str(d)


@pytest.fixture
def config(text_dir):
    return {"path": text_dir, "layout": "directory", "effective_field": "ts"}


@pytest.fixture
def parquet_dir(tmp_path):
    """File layout: one parquet stream with a naive timestamp, a NaN, a list
    column, and one jsonl stream beside it."""
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    d = tmp_path / "flat"
    d.mkdir()
    table = pa.table({
        "ts": pa.array(
            [datetime(2026, 1, 3), datetime(2026, 1, 2),
             datetime(2026, 1, 2, 0, 0, 0, 123000)],
            pa.timestamp("us"),
        ),
        "close": pa.array([1.5, float("nan"), 2.5], pa.float64()),
        "tags": pa.array([[1, 2], [], None], pa.list_(pa.int64())),
        "sym": ["A", "B", "C"],
    })
    pq.write_table(table, d / "prices.parquet")
    _write_ndjson(d / "notes.jsonl", [{"ts": "2026-01-05T12:00:00+00:00", "text": "hi"}])
    return str(d)


# -- registration + spec ----------------------------------------------------


def test_registered_kind_resolves():
    assert resolve_connector("localtables") is LocalTablesConnector


def test_spec_passes_its_own_gate(conn, config):
    check_config(conn, config)
    with pytest.raises(AssetError, match="unknown key"):
        check_config(conn, {**config, "surprise": 1})
    with pytest.raises(AssetError, match="required knob"):
        check_config(conn, {"effective_field": "ts"})
    with pytest.raises(AssetError, match="required knob"):
        check_config(conn, {"path": config["path"], "layout": "file"})


def test_defaults_are_one_named_constant(conn, config, tmp_path, monkeypatch):
    # discover() and read() fall back to the SAME module constants; rebinding
    # each must move every consumer. A call site that hardcoded the literal
    # would keep seeing it here and fail.
    assert localtables._DEFAULT_ENCODING == "utf-8"
    assert localtables._DEFAULT_EFFECTIVE_UNIT == "iso"
    monkeypatch.setattr(localtables, "_DEFAULT_ENCODING", "utf-8-sig")
    seen = []
    real_rows = LocalTablesConnector._json_rows

    def _spy(self, path, fmt, encoding):
        seen.append(encoding)
        return real_rows(self, path, fmt, encoding)

    monkeypatch.setattr(LocalTablesConnector, "_json_rows", _spy)
    conn.discover(config)
    list(conn.read(config, ["bars"], {}, "live"))
    assert seen and all(enc == "utf-8-sig" for enc in seen)

    # The unit default: with the constant rebound to "ms", an undeclared unit
    # reads epoch milliseconds — and refuses the ISO text_dir rows.
    monkeypatch.setattr(localtables, "_DEFAULT_EFFECTIVE_UNIT", "ms")
    d = tmp_path / "epoch"
    (d / "t").mkdir(parents=True)
    _write_ndjson(d / "t" / "a.ndjson", [{"ts": _epoch_ms("2026-01-02"), "v": 1}])
    cfg = {"path": str(d), "layout": "directory", "effective_field": "ts"}
    assert [m["effective_date"] for m in _records(conn, cfg, ["t"])] == \
        ["2026-01-02T00:00:00+00:00"]
    with pytest.raises(AssetError, match="integer epoch"):
        list(conn.read(config, ["bars"], {}, "live"))


def test_defaults_pinned_in_prose(conn):
    # The trailing period terminates each needle, so a drifted value cannot
    # pass on a shared prefix (the localfiles idiom).
    for needle in (f"default {localtables._DEFAULT_ENCODING}.",
                   f"default {localtables._DEFAULT_EFFECTIVE_UNIT}."):
        assert needle in localtables.__doc__
    params = conn.spec()["params"]
    assert f"default {localtables._DEFAULT_ENCODING}." in params["encoding"]["notes"]
    assert f"default {localtables._DEFAULT_EFFECTIVE_UNIT}." in \
        params["effective_unit"]["notes"]


# -- check ------------------------------------------------------------------


def test_check_fails_fast(conn, tmp_path, config):
    conn.check(config)  # data exists — fine
    with pytest.raises(AssetError, match="not a directory"):
        conn.check({**config, "path": str(tmp_path / "nope")})
    with pytest.raises(AssetError, match="config.layout"):
        conn.check({**config, "layout": "heap"})
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(AssetError, match="no table files"):
        conn.check({**config, "path": str(empty)})
    with pytest.raises(AssetError, match="config.effective_unit"):
        conn.check({**config, "effective_unit": "ns"})
    with pytest.raises(AssetError, match="config.formats"):
        conn.check({**config, "formats": ["csv"]})
    with pytest.raises(AssetError, match="config.formats"):
        conn.check({**config, "formats": "parquet"})
    with pytest.raises(AssetError, match="unknown stream"):
        conn.check({**config, "streams": ["ghost"]})
    with pytest.raises(AssetError, match="config.stamp_stem_as"):
        conn.check({**config, "stamp_stem_as": ""})


def test_unknown_encoding_refused_at_check_not_mid_read(conn, config):
    # open() raises a raw LookupError for a codec it cannot find — from
    # inside read(), after check() approved the knob. The knob is checked
    # where knobs are checked; any alias Python resolves still passes.
    with pytest.raises(AssetError, match=r"config\.encoding 'utf-9'"):
        conn.check({**config, "encoding": "utf-9"})
    with pytest.raises(AssetError, match=r"config\.encoding"):
        list(conn.read({**config, "encoding": "utf-9"}, ["bars"], {}, "live"))
    aliased = {**config, "encoding": "UTF8"}
    conn.check(aliased)
    assert [m["data"]["v"] for m in _records(conn, aliased, ["bars"])] == [2, 22, 3, 4]


def test_check_accumulates_knob_problems(conn):
    with pytest.raises(AssetError) as exc:
        conn.check({"path": "", "layout": "heap", "effective_field": 3,
                    "effective_unit": "ns", "encoding": ""})
    assert len(exc.value.errors) == 5


def test_duplicate_stem_within_a_stream_refused(conn, tmp_path):
    d = tmp_path / "dup"
    (d / "s").mkdir(parents=True)
    _write_ndjson(d / "s" / "a.ndjson", [{"ts": "2026-01-01"}])
    _write_ndjson(d / "s" / "a.jsonl", [{"ts": "2026-01-01"}])
    with pytest.raises(AssetError, match="one file per stem"):
        conn.check({"path": str(d), "layout": "directory", "effective_field": "ts"})
    with pytest.raises(AssetError, match="one file per stem"):
        conn.check({"path": str(d / "s"), "layout": "file", "effective_field": "ts"})


# -- discover ---------------------------------------------------------------


def test_discover_directory_layout(conn, config):
    streams = conn.discover(config)
    assert [s["stream"] for s in streams] == ["bars", "outlook"]
    assert streams[0] == {"stream": "bars", "schema": {"fields": ["ts", "v"]},
                          "primary_key": []}
    assert streams[1]["schema"] == {"fields": ["ts", "view"]}
    # The stamp field is part of the emitted rows, so it is part of the schema.
    stamped = conn.discover({**config, "stamp_stem_as": "shard"})
    assert stamped[0]["schema"] == {"fields": ["shard", "ts", "v"]}


def test_discover_file_layout(conn, parquet_dir):
    cfg = {"path": parquet_dir, "layout": "file", "effective_field": "ts"}
    streams = conn.discover(cfg)
    assert [s["stream"] for s in streams] == ["notes", "prices"]
    assert streams[1]["schema"] == {"fields": ["close", "sym", "tags", "ts"]}


def test_formats_restriction_ignores_other_files(conn, config):
    cfg = {**config, "formats": ["ndjson"]}
    # bbb.jsonl drops out of bars; outlook's only file is .ndjson.gz, so the
    # subdirectory holds no table file and is not a stream at all.
    assert [s["stream"] for s in conn.discover(cfg)] == ["bars"]
    assert [m["data"]["v"] for m in _records(conn, cfg, ["bars"])] == [2, 4]


def test_streams_restriction_scopes_discover_and_read(conn, config):
    cfg = {**config, "streams": ["outlook"]}
    assert [s["stream"] for s in conn.discover(cfg)] == ["outlook"]
    with pytest.raises(AssetError, match="unknown stream"):
        list(conn.read(cfg, ["bars"], {}, "live"))


# -- read: order, cursor, envelope -----------------------------------------


def test_read_sorts_across_shards_and_emits_schema_then_state(conn, config):
    msgs = _read(conn, config, ["bars"])
    assert [m["type"] for m in msgs] == ["SCHEMA", "RECORD", "RECORD", "RECORD", "RECORD", "STATE"]
    assert msgs[0]["schema"] == {"fields": ["ts", "v"]}
    records = msgs[1:-1]
    # (instant, stem, row): the shared instant orders aaa before bbb.
    assert [m["effective_date"] for m in records] == [
        "2026-01-02T00:00:00+00:00", "2026-01-02T00:00:00+00:00",
        "2026-01-03T00:00:00+00:00", "2026-01-04T00:00:00+00:00",
    ]
    assert [m["data"]["v"] for m in records] == [2, 22, 3, 4]
    assert all(m["kind"] == "observation" for m in records)
    assert msgs[-1]["state"] == {"bars": {"cursor": "2026-01-04T00:00:00+00:00"}}
    # Mode is the platform's axis: the pull is identical under both.
    assert _read(conn, config, ["bars"], mode="backfill") == msgs


def test_record_data_is_the_original_row(conn, config):
    records = _records(conn, config, ["outlook"])
    # The date-only source instant is normalized on the envelope only.
    assert [m["effective_date"] for m in records] == \
        ["2026-02-01T00:00:00+00:00", "2026-03-01T00:00:00+00:00"]
    assert records[0]["data"] == {"ts": "2026-02-01", "view": "up"}


def test_cursor_filters_already_durable_rows(conn, config):
    state = {"bars": {"cursor": "2026-01-02T00:00:00+00:00"}}
    assert [m["data"]["v"] for m in _records(conn, config, ["bars"], state)] == [3, 4]
    # The cursor is compared as an INSTANT, so another spelling of it agrees.
    assert [m["data"]["v"] for m in _records(conn, config, ["bars"],
                                             {"bars": {"cursor": "2026-01-02"}})] == [3, 4]


def test_caught_up_stream_keeps_its_cursor(conn, config):
    state = {"bars": {"cursor": "2026-01-04T00:00:00+00:00"}}
    msgs = _read(conn, config, ["bars"], state)
    assert [m["type"] for m in msgs] == ["SCHEMA", "STATE"]
    assert msgs[-1]["state"] == state  # never regresses on empty


def test_multi_member_gzip_is_read_whole(conn, config):
    records = _records(conn, config, ["outlook"])
    assert [m["data"]["view"] for m in records] == ["up", "flat"]


def test_unknown_stream_named(conn, config):
    with pytest.raises(AssetError, match="unknown stream 'ghost'"):
        list(conn.read(config, ["ghost"], {}, "live"))


# -- stamping ---------------------------------------------------------------


def test_stamp_stem_as_writes_the_shard_stem(conn, config):
    cfg = {**config, "stamp_stem_as": "shard"}
    msgs = _read(conn, cfg, ["bars"])
    assert msgs[0]["schema"] == {"fields": ["shard", "ts", "v"]}
    records = msgs[1:-1]
    assert [m["data"]["shard"] for m in records] == ["aaa", "bbb", "bbb", "aaa"]
    assert records[0]["data"] == {"ts": "2026-01-02T00:00:00+00:00", "v": 2, "shard": "aaa"}


def test_stamp_conflict_refused_same_value_tolerated(conn, tmp_path):
    d = tmp_path / "stamp"
    (d / "s").mkdir(parents=True)
    _write_ndjson(d / "s" / "abc.ndjson", [
        {"ts": "2026-01-01", "series": "abc"},  # already right: idempotent
        {"ts": "2026-01-02", "series": "xyz"},  # a DIFFERENT value: never rewritten
    ])
    cfg = {"path": str(d), "layout": "directory", "effective_field": "ts",
           "stamp_stem_as": "series"}
    with pytest.raises(AssetError, match=r"abc\.ndjson:2.*'series' already holds 'xyz'"):
        list(conn.read(cfg, ["s"], {}, "live"))
    _write_ndjson(d / "s" / "abc.ndjson", [{"ts": "2026-01-01", "series": "abc"}])
    assert _records(conn, cfg, ["s"])[0]["data"]["series"] == "abc"


# -- instants: units and exactness ------------------------------------------


def test_effective_unit_ms_and_s_are_exact(conn, tmp_path):
    d = tmp_path / "epoch"
    (d / "ticks").mkdir(parents=True)
    ms = _epoch_ms("2026-01-02T00:00:00+00:00")
    _write_ndjson(d / "ticks" / "a.ndjson", [{"t": ms + 123, "v": 1}, {"t": ms, "v": 0}])
    cfg = {"path": str(d), "layout": "directory", "effective_field": "t",
           "effective_unit": "ms"}
    records = _records(conn, cfg, ["ticks"])
    assert [m["effective_date"] for m in records] == \
        ["2026-01-02T00:00:00+00:00", "2026-01-02T00:00:00.123+00:00"]
    assert records[1]["data"]["t"] == ms + 123  # the original value, untouched

    _write_ndjson(d / "ticks" / "a.ndjson", [{"t": ms // 1000, "v": 0}])
    cfg["effective_unit"] = "s"
    assert [m["effective_date"] for m in _records(conn, cfg, ["ticks"])] == \
        ["2026-01-02T00:00:00+00:00"]

    # A string under an epoch unit, or an epoch under iso, refuses by row.
    _write_ndjson(d / "ticks" / "a.ndjson",
                  [{"t": ms // 1000, "v": 0}, {"t": "2026-01-02", "v": 1}])
    with pytest.raises(AssetError, match=r"a\.ndjson:2: field 't'.*integer epoch"):
        list(conn.read(cfg, ["ticks"], {}, "live"))
    cfg["effective_unit"] = "iso"
    with pytest.raises(AssetError, match=r"a\.ndjson:1: field 't'.*ISO"):
        list(conn.read(cfg, ["ticks"], {}, "live"))
    # Milliseconds read as seconds land in year ~58000: out of range, by row.
    _write_ndjson(d / "ticks" / "a.ndjson", [{"t": ms, "v": 0}])
    cfg["effective_unit"] = "s"
    with pytest.raises(AssetError, match=r"a\.ndjson:1: field 't'.*outside the datetime range"):
        list(conn.read(cfg, ["ticks"], {}, "live"))


@given(st.integers(min_value=-(10**12), max_value=4 * 10**12))
def test_epoch_ms_round_trip_is_exact(ms):
    # Every millisecond from 1938 to 2096 survives ms -> ISO -> ms exactly;
    # a float path would land ~1-2% of stamps one ms off.
    eff = localtables._iso(localtables._instant(ms, "ms"))
    assert _epoch_ms(eff) == ms
    assert eff.endswith("+00:00")


def test_iso_spelling_is_the_shortest_exact_one():
    # seconds only / milliseconds / microseconds — never a padded fraction.
    assert localtables._iso(localtables._instant("2026-01-02", "iso")) == \
        "2026-01-02T00:00:00+00:00"
    assert localtables._iso(localtables._instant("2026-01-02T00:00:00.120000", "iso")) == \
        "2026-01-02T00:00:00.120+00:00"
    assert localtables._iso(localtables._instant("2026-01-02T00:00:00.123456", "iso")) == \
        "2026-01-02T00:00:00.123456+00:00"
    # An offset spelling normalizes to UTC.
    assert localtables._iso(localtables._instant("2026-01-02T00:00:00-05:00", "iso")) == \
        "2026-01-02T05:00:00+00:00"
    # Integral floats are exact integers; anything else under an epoch unit refuses.
    assert localtables._instant(1000.0, "ms") == localtables._instant(1, "s")
    for bad in (1.5, True, float("nan"), None, "1000"):
        with pytest.raises(AssetError, match="integer epoch"):
            localtables._instant(bad, "ms")


# -- rows that refuse, by file and line -------------------------------------


def test_malformed_json_line_names_file_and_line(conn, tmp_path):
    d = tmp_path / "bad"
    (d / "s").mkdir(parents=True)
    (d / "s" / "x.ndjson").write_text('{"ts": "2026-01-01"}\n\n{"ts": \n', encoding="utf-8")
    cfg = {"path": str(d), "layout": "directory", "effective_field": "ts"}
    with pytest.raises(AssetError, match=r"x\.ndjson:3 is not valid JSON"):
        list(conn.read(cfg, ["s"], {}, "live"))
    (d / "s" / "x.ndjson").write_text('[1, 2]\n', encoding="utf-8")
    with pytest.raises(AssetError, match=r"x\.ndjson:1 must be a JSON object"):
        list(conn.read(cfg, ["s"], {}, "live"))
    (d / "s" / "x.ndjson").write_text('{"ts": "2026-01-01", "v": Infinity}\n', encoding="utf-8")
    with pytest.raises(AssetError, match=r"x\.ndjson:1 is not valid JSON"):
        list(conn.read(cfg, ["s"], {}, "live"))


def test_nan_token_reads_as_null(conn, tmp_path):
    d = tmp_path / "nan"
    (d / "s").mkdir(parents=True)
    (d / "s" / "x.ndjson").write_text('{"ts": "2026-01-01", "v": NaN, "w": [NaN]}\n',
                                      encoding="utf-8")
    cfg = {"path": str(d), "layout": "directory", "effective_field": "ts"}
    assert _records(conn, cfg, ["s"])[0]["data"] == {"ts": "2026-01-01", "v": None, "w": [None]}


def test_row_missing_effective_field_named_with_line(conn, tmp_path):
    d = tmp_path / "missing"
    (d / "s").mkdir(parents=True)
    _write_ndjson(d / "s" / "x.ndjson", [{"ts": "2026-01-01"}, {"other": 1}])
    cfg = {"path": str(d), "layout": "directory", "effective_field": "ts"}
    with pytest.raises(AssetError, match=r"x\.ndjson:2: field 'ts'"):
        list(conn.read(cfg, ["s"], {}, "live"))
    _write_ndjson(d / "s" / "x.ndjson", [{"ts": None}])
    with pytest.raises(AssetError, match=r"x\.ndjson:1: field 'ts'"):
        list(conn.read(cfg, ["s"], {}, "live"))


def test_truncated_gzip_member_is_loud(conn, tmp_path):
    d = tmp_path / "gz"
    (d / "s").mkdir(parents=True)
    whole = gzip.compress(b'{"ts": "2026-01-01", "v": 1}\n' * 50)
    (d / "s" / "x.jsonl.gz").write_bytes(whole[:-12])  # no trailer
    cfg = {"path": str(d), "layout": "directory", "effective_field": "ts"}
    with pytest.raises(AssetError, match=r"x\.jsonl\.gz"):
        list(conn.read(cfg, ["s"], {}, "live"))


# -- parquet ----------------------------------------------------------------


def test_parquet_rows_round_trip_to_json_values(conn, parquet_dir):
    cfg = {"path": parquet_dir, "layout": "file", "effective_field": "ts"}
    records = _records(conn, cfg, ["prices"])
    assert [m["effective_date"] for m in records] == [
        "2026-01-02T00:00:00+00:00",       # naive timestamp: UTC by convention
        "2026-01-02T00:00:00.123+00:00",   # the source's milliseconds, exactly
        "2026-01-03T00:00:00+00:00",
    ]
    # data: the row as JSON values — timestamp as its own ISO spelling
    # (naive stays naive), NaN -> None, list<int> -> list, null list -> None.
    assert [m["data"] for m in records] == [
        {"ts": "2026-01-02T00:00:00", "close": None, "tags": [], "sym": "B"},
        {"ts": "2026-01-02T00:00:00.123000", "close": 2.5, "tags": None, "sym": "C"},
        {"ts": "2026-01-03T00:00:00", "close": 1.5, "tags": [1, 2], "sym": "A"},
    ]
    for m in records:
        json.dumps(m["data"], allow_nan=False)  # strictly serializable


def test_parquet_aware_timestamps_and_epoch_units(conn, tmp_path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    d = tmp_path / "aware"
    d.mkdir()
    ms = _epoch_ms("2026-01-02T05:00:00+00:00")
    pq.write_table(pa.table({
        "ts": pa.array([datetime(2026, 1, 2, 5, tzinfo=UTC)], pa.timestamp("ms", tz="UTC")),
        "t_ms": pa.array([ms], pa.int64()),
        "t_s": pa.array([float(ms // 1000)], pa.float64()),  # an integral double
    }), d / "s.parquet")
    base = {"path": str(d), "layout": "file"}
    for field, unit in (("ts", "iso"), ("t_ms", "ms"), ("t_s", "s")):
        cfg = {**base, "effective_field": field, "effective_unit": unit}
        (rec,) = _records(conn, cfg, ["s"])
        assert rec["effective_date"] == "2026-01-02T05:00:00+00:00", (field, unit)
    assert rec["data"] == {"ts": "2026-01-02T05:00:00+00:00", "t_ms": ms, "t_s": float(ms // 1000)}


def test_parquet_values_json_cannot_carry_refuse_by_row_and_field(conn, tmp_path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    d = tmp_path / "blobs"
    d.mkdir()
    pq.write_table(pa.table({
        "ts": ["2026-01-01", "2026-01-02"],
        "blob": pa.array([None, b"\x00"], pa.binary()),
    }), d / "s.parquet")
    cfg = {"path": str(d), "layout": "file", "effective_field": "ts"}
    with pytest.raises(AssetError, match=r"s\.parquet:2 field 'blob'.*no JSON spelling"):
        list(conn.read(cfg, ["s"], {}, "live"))
    pq.write_table(pa.table({
        "ts": ["2026-01-01"],
        "x": pa.array([float("inf")], pa.float64()),
    }), d / "s.parquet")
    with pytest.raises(AssetError, match=r"s\.parquet:1 field 'x'.*no JSON spelling"):
        list(conn.read(cfg, ["s"], {}, "live"))


def test_corrupt_parquet_is_loud(conn, tmp_path):
    pytest.importorskip("pyarrow")
    d = tmp_path / "corrupt"
    d.mkdir()
    (d / "s.parquet").write_bytes(b"PAR1 not a parquet file")
    cfg = {"path": str(d), "layout": "file", "effective_field": "ts"}
    with pytest.raises(AssetError, match=r"parquet.*s\.parquet"):
        conn.discover(cfg)
    with pytest.raises(AssetError, match=r"parquet.*s\.parquet"):
        list(conn.read(cfg, ["s"], {}, "live"))


def test_parquet_without_pyarrow_is_loud(conn, tmp_path, monkeypatch):
    d = tmp_path / "p"
    d.mkdir()
    (d / "s.parquet").write_bytes(b"PAR1")  # the import is refused before any byte is read
    real_import = builtins.__import__

    def no_pyarrow(name, *args, **kwargs):
        if name == "pyarrow" or name.startswith("pyarrow."):
            raise ImportError("pyarrow is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pyarrow)
    cfg = {"path": str(d), "layout": "file", "effective_field": "ts"}
    with pytest.raises(AssetError, match="needs pyarrow"):
        conn.check(cfg)
    with pytest.raises(AssetError, match="needs pyarrow"):
        list(conn.read(cfg, ["s"], {}, "live"))
    # Text-only sources never touch the import.
    _write_ndjson(d / "t.ndjson", [{"ts": "2026-01-01"}])
    assert len(_records(conn, {**cfg, "formats": ["ndjson"]}, ["t"])) == 1


# -- end to end -------------------------------------------------------------


def test_acquisition_end_to_end(tmp_path, text_dir):
    """Registered as the ``localtables`` kind, one backfill pull lands the
    stream as a WORM snapshot, and the read seam hands the rows back."""
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": "tables",
        "catalog_source": "tables-src",
        "connector": "localtables",
        "config": {"path": text_dir, "layout": "directory",
                   "effective_field": "ts", "stamp_stem_as": "shard"},
    }, origin="test")
    registry.transition(vid, "active", origin="test")

    out = run_acquisition(root, registry, "tables", "bars", "backfill")
    assert out["records"] == 4
    assert out["state_saved"]  # the cursor persisted AFTER the snapshot

    rows = scan_stream(root.root, "tables", "bars", key_fields=("shard", "ts"), ts_field="ts")
    assert [(r["shard"], r["v"]) for r in rows] == [("aaa", 2), ("bbb", 22), ("bbb", 3), ("aaa", 4)]
    assert rows[0]["asof_ms"] == _epoch_ms("2026-01-02")

    # A second pull is caught up: the cursor makes it an empty, honest no-op.
    again = run_acquisition(root, registry, "tables", "bars", "backfill")
    assert again["snapshot"] is None and again["records"] == 0
