"""ParquetRows (ADR-0228): a store reference resolved to a parquet file's rows,
digest-verified against the manifest, default-deny, pyarrow only inside run()."""

import ast
import errno
import itertools
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

import dskit.onboarding.acquire as acquire_module  # noqa: E402
from dskit.onboarding import OnboardingRoot, run_acquisition
from dskit.onboarding.artifacts import payload_files
from dskit.onboarding.base import AssetError  # noqa: E402
from dskit.pipeline.base import ConfigError  # noqa: E402
from dskit.pipeline.libs import parquet as pack  # noqa: E402
from dskit.pipeline.libs.parquet import ParquetRows, ParquetStreamRows  # noqa: E402
from dskit.pipeline.node import NodeContext  # noqa: E402
from dskit.pipeline.workflow import path_hash  # noqa: E402

SOURCE, STREAM, REL = "blobs", "files", "grp/prices.parquet"
ROWS = {"d": ["2024-01-02", "2024-01-03"], "c": [10.0, 11.5], "extra": [1, 2]}


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    ticks = itertools.count()
    start = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(
        acquire_module, "utc_now",
        lambda: (start + timedelta(seconds=next(ticks))).isoformat(timespec="seconds"),
    )


@pytest.fixture
def store(tmp_path):
    archive = tmp_path / "archive"
    (archive / "grp").mkdir(parents=True)
    pq.write_table(pa.table(ROWS), archive / REL)
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": SOURCE, "catalog_source": "x", "connector": "localblobs",
        "config": {"path": str(archive), "as_of": "2026-01-01T00:00:00+00:00"},
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(root, registry, SOURCE, STREAM, "backfill")
    return root


def _params(store, **over):
    base = {"root": store.root, "source": SOURCE, "stream": STREAM,
            "relpath_by_key": {"K": REL}, "key": "K",
            "columns": {"d": "when", "c": "px"}}
    base.update(over)
    return base


def _ctx(tmp_path):
    return NodeContext(name="t", asof="2026-01-10", run_dir=str(tmp_path / "run"))


def test_round_trip_projects_only_the_declared_columns(store, tmp_path):
    out = ParquetRows("p", _params(store)).run(_ctx(tmp_path), {})
    assert out["records"] == [{"when": "2024-01-02", "px": 10.0},
                              {"when": "2024-01-03", "px": 11.5}]


def test_fingerprint_is_the_manifest_digest(store):
    fp = ParquetRows("p", _params(store)).fingerprint()
    assert fp["relpath"] == REL and len(fp["sha256"]) == 64


def test_default_deny_and_shape_problems(store):
    with pytest.raises(ConfigError, match="typo"):
        ParquetRows("p", _params(store, typo=1))
    for name in ("root", "source", "stream", "relpath_by_key", "key", "columns"):
        bad = _params(store)
        del bad[name]
        with pytest.raises(ConfigError, match=name):
            ParquetRows("p", bad)
    with pytest.raises(ConfigError, match="columns"):
        ParquetRows("p", _params(store, columns={}))


WIDE = {"d": ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
        "c": [10.0, 11.5, 12.0, 13.0], "extra": [1, 2, 3, 4]}


@pytest.fixture
def wide_store(tmp_path):
    archive = tmp_path / "wide"
    (archive / "grp").mkdir(parents=True)
    pq.write_table(pa.table(WIDE), archive / REL)
    root = OnboardingRoot.create(str(tmp_path / "ob_wide"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": SOURCE, "catalog_source": "x", "connector": "localblobs",
        "config": {"path": str(archive), "as_of": "2026-01-01T00:00:00+00:00"},
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(root, registry, SOURCE, STREAM, "backfill")
    return root


def _windowed(store, tmp_path, **window):
    node = ParquetRows("p", _params(store, window={"field": "when", **window}))
    return [r["when"] for r in node.run(_ctx(tmp_path), {})["records"]]


def test_a_window_keeps_rows_between_start_and_end_inclusive(wide_store, tmp_path):
    assert _windowed(wide_store, tmp_path, start="2024-01-03", end="2024-01-04") == [
        "2024-01-03", "2024-01-04"]


def test_a_window_may_be_open_at_either_end(wide_store, tmp_path):
    assert _windowed(wide_store, tmp_path, start="2024-01-04") == ["2024-01-04", "2024-01-05"]
    assert _windowed(wide_store, tmp_path, end="2024-01-02") == ["2024-01-02"]


def test_a_window_reads_dates_and_datetimes_as_their_calendar_day(tmp_path):
    import datetime as dt
    archive = tmp_path / "typed"
    (archive / "grp").mkdir(parents=True)
    pq.write_table(pa.table({"d": [dt.date(2024, 1, 2), dt.date(2024, 1, 3)],
                             "c": [1.0, 2.0]}), archive / REL)
    root = OnboardingRoot.create(str(tmp_path / "ob_typed"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": SOURCE, "catalog_source": "x", "connector": "localblobs",
        "config": {"path": str(archive), "as_of": "2026-01-01T00:00:00+00:00"},
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(root, registry, SOURCE, STREAM, "backfill")
    assert _windowed(root, tmp_path, start="2024-01-03") == [dt.date(2024, 1, 3)]


def test_a_text_date_that_does_not_parse_refuses_the_run(tmp_path, wide_store):
    node = ParquetRows("p", _params(wide_store, window={"field": "when", "start": "2024-01-01"},
                                    columns={"c": "when", "d": "px"}))
    with pytest.raises(ValueError, match="when"):
        node.run(_ctx(tmp_path), {})


def test_a_window_leaving_no_row_is_an_empty_stream_not_an_error(wide_store, tmp_path):
    assert _windowed(wide_store, tmp_path, start="2030-01-01") == []


def test_no_window_is_the_whole_file(store, tmp_path):
    assert len(ParquetRows("p", _params(store)).run(_ctx(tmp_path), {})["records"]) == 2


@pytest.mark.parametrize("window", [
    {"start": "2024-01-02"}, {"field": "when"}, {"field": "nope", "start": "2024-01-02"},
    {"field": "when", "start": "02/01/2024"}, {"field": "when", "start": "20240102"},
    {"field": "when", "start": "2024-W01-1"}, {"field": "when", "start": 5},
    {"field": "when", "start": "2024-01-04", "end": "2024-01-02"},
    {"field": "when", "start": "2024-01-02", "typo": 1}, "2024-01-02", {},
], ids=["no_field", "no_bound", "field_not_output", "bad_date", "compact", "week_date", "not_text", "reversed",
        "unknown_key", "not_a_dict", "empty"])
def test_a_malformed_window_is_refused_at_construction(store, window):
    with pytest.raises(ConfigError, match="window"):
        ParquetRows("p", _params(store, window=window))


def test_a_window_may_be_a_node_reference_and_a_none_window_means_none(store):
    ParquetRows("p", _params(store, window="$other.window"))
    ParquetRows("p", _params(store, window=None))


def test_unknown_key_and_missing_column_refuse_by_name(store, tmp_path):
    with pytest.raises(ValueError, match="'Z'"):
        ParquetRows("p", _params(store, key="Z")).run(_ctx(tmp_path), {})
    with pytest.raises(ValueError, match="nope"):
        ParquetRows("p", _params(store, columns={"nope": "x"})).run(_ctx(tmp_path), {})


def test_a_drifted_file_refuses(store, tmp_path):
    node = ParquetRows("p", _params(store))
    victim = next(Path(store.root).rglob("prices.parquet"))
    victim.write_bytes(victim.read_bytes() + b"\0")
    with pytest.raises(ValueError, match="sha256"):
        node.run(_ctx(tmp_path), {})


# -- ParquetFrameCache (ADR-0236 amendment): build a frame once per identity ----------------------

def _builder(calls, frame=None):
    import pandas as pd

    def build():
        calls.append(1)
        built = pd.DataFrame({"a": [1, 2], "b": ["x", None]}) if frame is None else frame
        return built, {"rows": len(built), "pair": (1, 2), 3: "int key"}
    return build


def test_a_frame_is_built_once_and_reused_while_its_identity_holds(tmp_path):
    import pandas as pd
    cache, calls = pack.ParquetFrameCache(tmp_path / "cache"), []
    frame, payload, state = cache.load_or_build(lambda: {"v": 1}, _builder(calls))
    assert (state, calls) == ("stored", [1])
    assert payload == {"rows": 2, "pair": [1, 2], "3": "int key"}  # what a reuse will return
    again, reused, state = cache.load_or_build(lambda: {"v": 1}, _builder(calls))
    assert (state, calls, reused) == ("reused", [1], payload)
    pd.testing.assert_frame_equal(frame, again)  # a build hands back the stored frame too


def test_a_moved_identity_rebuilds_into_the_one_slot(tmp_path):
    import pandas as pd
    cache, calls = pack.ParquetFrameCache(tmp_path / "cache"), []
    cache.load_or_build(lambda: {"v": 1}, _builder(calls))
    other = pd.DataFrame({"a": [7]})
    frame, _, state = cache.load_or_build(lambda: {"v": 2}, _builder(calls, other))
    assert state == "stored" and calls == [1, 1] and frame.a.tolist() == [7]
    _, _, state = cache.load_or_build(lambda: {"v": 1}, _builder(calls))
    assert state == "stored" and calls == [1, 1, 1]  # one slot: the first was replaced
    assert sorted(p.name for p in (tmp_path / "cache").iterdir()) == ["frame.parquet",
                                                                        "record.json"]


def test_a_frame_file_that_no_longer_matches_its_digest_is_rebuilt(tmp_path):
    cache, calls = pack.ParquetFrameCache(tmp_path / "cache"), []
    cache.load_or_build(lambda: {"v": 1}, _builder(calls))
    stored = tmp_path / "cache" / "frame.parquet"
    stored.write_bytes(stored.read_bytes() + b"\0")
    _, _, state = cache.load_or_build(lambda: {"v": 1}, _builder(calls))
    assert state == "stored" and calls == [1, 1]


def test_an_identity_that_moves_during_the_build_is_not_stored(tmp_path):
    cache, calls, ticks = pack.ParquetFrameCache(tmp_path / "cache"), [], itertools.count()
    frame, _, state = cache.load_or_build(lambda: {"v": next(ticks)}, _builder(calls))
    assert state == "unstored" and len(frame) == 2 and not (tmp_path / "cache").exists()


def test_a_frame_holding_containers_is_never_stored(tmp_path):
    # Parquet hands a dict's lists back as arrays: such a frame is rebuilt, never reused.
    import pandas as pd
    cache, calls = pack.ParquetFrameCache(tmp_path / "cache"), []
    contexts = pd.DataFrame({"a": [1, 2], "context": [{"t": [1.0]}, {"t": [2.0]}]})
    for _ in range(2):
        frame, _, state = cache.load_or_build(lambda: {"v": 1}, _builder(calls, contexts))
        assert state == "unstored" and frame.context[0] == {"t": [1.0]}
    assert calls == [1, 1] and not (tmp_path / "cache").exists()
    assert pack.ParquetFrameCache.storable(pd.DataFrame({"s": ["x", None], "n": [1, 2]}))
    with pytest.raises(ValueError, match="container"):
        cache.store({"v": 1}, contexts, {})


def test_a_failed_write_or_a_lost_slot_degrades_to_an_unstored_build(tmp_path, monkeypatch):
    import pandas as pd
    cache, calls = pack.ParquetFrameCache(tmp_path / "cache"), []
    real = pd.DataFrame.to_parquet

    def full_disk(self, path, **kw):
        real(self, path, **kw)
        raise OSError("no space left on device")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", full_disk)
    frame, payload, state = cache.load_or_build(lambda: {"v": 1}, _builder(calls))
    assert state == "unstored" and len(frame) == 2 and payload["pair"] == [1, 2]
    assert list((tmp_path / "cache").iterdir()) == []      # no partial file is left behind
    monkeypatch.setattr(pd.DataFrame, "to_parquet", real)
    monkeypatch.setattr(cache, "load", lambda identity: None)   # another writer took the slot
    _, _, state = cache.load_or_build(lambda: {"v": 1}, _builder(calls))
    assert state == "unstored" and calls == [1, 1]
    assert not pack.ParquetFrameCache.storable(pd.DataFrame([[1, 2]], columns=["a", "a"]))
    assert not pack.ParquetFrameCache.storable(pd.DataFrame({0: [1]}))


def test_the_code_digest_names_the_code_that_built_a_frame(tmp_path):
    import types
    package = types.ModuleType("pkg")
    package.__file__ = str(tmp_path / "pkg" / "__init__.py")
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "pkg" / "__init__.py").write_text("")
    module = tmp_path / "pkg" / "sub" / "rule.py"
    module.write_text("LIMIT = 1\n")
    (tmp_path / "pkg" / "notes.txt").write_text("not code")
    first = pack.ParquetFrameCache.code_digest(package)
    assert pack.ParquetFrameCache.code_digest(package) == first and len(first) == 64
    (tmp_path / "pkg" / "notes.txt").write_text("still not code")
    (tmp_path / "pkg" / "sub" / "rule.cpython-312.pyc").write_bytes(b"compiled")
    assert pack.ParquetFrameCache.code_digest(package) == first
    module.write_text("LIMIT = 2\n")
    assert pack.ParquetFrameCache.code_digest(package) != first


def test_pyarrow_is_imported_only_inside_run():
    tree = ast.parse(Path(pack.__file__).read_text())
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
            assert not any("pyarrow" in n for n in names)


def test_a_damaged_slot_is_a_miss_that_rebuilds(tmp_path):
    # Review round 6: a record that does not parse, lacks its payload, or names bytes that
    # hash right but are not parquet never crashes a caller; the next build replaces it.
    import json
    cache, calls = pack.ParquetFrameCache(tmp_path / "cache"), []
    record, frame = tmp_path / "cache" / "record.json", tmp_path / "cache" / "frame.parquet"
    cache.load_or_build(lambda: {"v": 1}, _builder(calls))
    stored = json.loads(record.read_text())
    without_payload = {k: v for k, v in stored.items() if k != "payload"}
    for damage in ("{not json", json.dumps([1]), json.dumps(without_payload)):
        record.write_text(damage)
        assert cache.load({"v": 1}) is None
    frame.write_bytes(b"not parquet at all")
    record.write_text(json.dumps({**stored, "frame_sha256": pack._sha256(frame)}))
    assert cache.load({"v": 1}) is None
    _, _, state = cache.load_or_build(lambda: {"v": 1}, _builder(calls))
    assert state == "stored" and calls == [1, 1]
    with pytest.raises(FileNotFoundError):
        pack._sha256(tmp_path / "missing.parquet")


def test_the_code_digest_moves_when_a_module_is_renamed(tmp_path):
    import types
    package = types.ModuleType("pkg")
    package.__file__ = str(tmp_path / "pkg" / "__init__.py")
    module = _write_module(tmp_path / "pkg", "rule.py")
    first = pack.ParquetFrameCache.code_digest(package)
    module.rename(tmp_path / "pkg" / "renamed.py")      # same bytes, new name
    assert pack.ParquetFrameCache.code_digest(package) != first


def _write_module(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "__init__.py").write_text("")
    path = folder / name
    path.write_text("LIMIT = 1\n")
    return path


# ADR-0248: source predicates precede Python payload materialization.
def test_declared_bounds_exclude_payload_before_record_projection(wide_store, tmp_path, monkeypatch):
    node = ParquetRows("p", _params(wide_store))
    # Attach the declaration after construction to expose the old read ordering,
    # independently of the newly introduced parameter validator.
    node.params["read_bounds"] = {"d": {"end_before": "2024-01-04"}}
    out = node.run(_ctx(tmp_path), {})["records"]
    assert out == [{"when": "2024-01-02", "px": 10.0},
                   {"when": "2024-01-03", "px": 11.5}]

@pytest.mark.parametrize("kind", ["text", "date32", "date64"])
def test_bounded_read_intersects_clocks_and_projects_before_materialization(tmp_path, monkeypatch, kind):
    import datetime as dt
    import pyarrow.dataset as ds

    dates = ["2025-12-29", "2025-12-30", "2026-01-01"]
    ends = ["2025-12-31", "2026-01-01", "2026-02-01"]
    if kind != "text":
        dtype = pa.date32() if kind == "date32" else pa.date64()
        dates = pa.array([dt.date.fromisoformat(d) for d in dates], type=dtype)
        ends = pa.array([dt.date.fromisoformat(d) for d in ends], type=dtype)
    path = tmp_path / "mixed.parquet"
    pq.write_table(pa.table({"quote": dates, "end": ends, "payload": [7, 999, 9999],
                            "unused": [1, 2, 3]}), path, row_group_size=1)
    original, calls = ds.dataset, []
    class Scan:
        def __init__(self, *args, **kwargs):
            self.inner = original(*args, **kwargs)
        def to_table(self, **kwargs):
            assert kwargs["columns"] == ["payload"]
            assert kwargs["filter"] is not None
            result = self.inner.to_table(**kwargs)
            assert result.to_pydict() == {"payload": [7]}
            calls.append(kwargs)
            return result
    monkeypatch.setattr(ds, "dataset", Scan)
    result = pack.DateBoundedParquet(path, ["payload"], {
        "quote": {"start": "2025-12-29", "end_before": "2026-01-01"},
        "end": {"end_before": "2026-01-01"}}).read()
    assert result.to_pylist() == [{"payload": 7}]
    assert len(calls) == 1


@pytest.mark.parametrize("bounds", [
    None, {}, [], {"d": {}}, {"d": None}, {"d": {"until": "2025-01-01"}},
    {"": {"start": "2025-01-01"}}, {"d": {"start": None}},
    {"d": {"start": "20250101"}}, {"d": {"start": "2025-1-1"}},
    {"d": {"start": "2025-02-30"}}, {"d": {"start": "2025-01-01", "end_before": "2025-01-01"}},
    {"d": {"start": "2025-01-02", "end_before": "2025-01-01"}},
])
def test_bounded_declaration_refuses_before_io(store, tmp_path, bounds):
    with pytest.raises(ConfigError, match="read_bounds"):
        ParquetRows("p", _params(store, read_bounds=bounds))
    with pytest.raises(ValueError, match="read_bounds"):
        pack.DateBoundedParquet(tmp_path / "absent", ["c"], bounds)


def test_resolved_bounded_declaration_revalidates_before_read(store, tmp_path, monkeypatch):
    node = ParquetRows("p", _params(store, read_bounds="$other.bounds"))
    node.params["read_bounds"] = {"d": {"end_before": "bad"}}
    with pytest.raises(ValueError, match="read_bounds"):
        node.run(_ctx(tmp_path), {})


@pytest.mark.parametrize("failure", ["null", "no_stats", "timestamp", "integer",
                                    "nested", "duplicate", "missing"])
def test_bounded_schema_refuses_before_scanning(tmp_path, monkeypatch, failure):
    import datetime as dt
    import pyarrow.dataset as ds
    table = pa.table({"d": ["2025-01-01"], "value": [7]})
    if failure == "null":
        table = pa.table({"d": pa.array([None], type=pa.string()), "value": [7]})
    elif failure == "timestamp":
        table = pa.table({"d": [dt.datetime(2025, 1, 1)], "value": [7]})
    elif failure == "integer":
        table = pa.table({"d": [20250101], "value": [7]})
    elif failure == "nested":
        table = pa.table({"d": ["2025-01-01"], "value": [{"a": 7}]})
    elif failure == "duplicate":
        table = pa.Table.from_arrays([pa.array(["2025-01-01"])] * 2, names=["d", "d"])
    path = tmp_path / "source.parquet"
    pq.write_table(table, path, write_statistics=failure != "no_stats")
    def forbidden(*a, **kw):
        raise AssertionError("scanner called before metadata refusal")
    monkeypatch.setattr(ds, "dataset", forbidden)
    with pytest.raises(ValueError, match="temporal predicate"):
        pack.DateBoundedParquet(path, ["value"],
            {"missing" if failure == "missing" else "d": {"end_before": "2026-01-01"}}).read()


@pytest.mark.parametrize("bad", ["2025-02-30", "20250101", "2025-1-1",
                                 "2025-01-01T00:00:00", "2026", " 2025-01-01"])
def test_bounded_invalid_string_date_refuses(tmp_path, bad):
    path = tmp_path / "source.parquet"
    pq.write_table(pa.table({"d": ["2025-01-01", bad], "value": [1, 2]}), path)
    with pytest.raises(ValueError):
        pack.DateBoundedParquet(path, ["value"], {"d": {"end_before": "2026-01-01"}}).read()


def test_boundaries_and_legacy_window_compose_without_widening(wide_store, tmp_path):
    node = ParquetRows("p", _params(wide_store,
        read_bounds={"d": {"end_before": "2024-01-05"}},
        window={"field": "when", "start": "2024-01-03"}))
    assert node.run(_ctx(tmp_path), {})["records"] == [
        {"when": "2024-01-03", "px": 11.5}, {"when": "2024-01-04", "px": 12.0}]
    node.params["read_bounds"] = {"d": {"start": "2024-01-06"}}
    assert node.run(_ctx(tmp_path), {})["records"] == []


def test_bounded_manifest_drift_still_refuses_before_scan(store, tmp_path, monkeypatch):
    import pyarrow.dataset as ds
    node = ParquetRows("p", _params(store, read_bounds={"d": {"end_before": "2026-01-01"}}))
    victim = next(Path(store.root).rglob("prices.parquet"))
    victim.write_bytes(victim.read_bytes() + b"changed")
    def forbidden(*a, **kw):
        raise AssertionError("scanner called on changed manifest bytes")
    monkeypatch.setattr(ds, "dataset", forbidden)
    with pytest.raises(ValueError, match="sha256"):
        node.run(_ctx(tmp_path), {})
@pytest.fixture
def blob_store(tmp_path):
    class Store:
        def __init__(self):
            self.root = OnboardingRoot.create(str(tmp_path / "blob-ob"))
            self.path, self.registry = self.root.root, self.root.registry()
        def add(self, name, directory):
            version = self.registry.register("source_config", {
                "name": name, "catalog_source": name, "connector": "localblobs",
                "config": {"path": str(directory), "as_of": "2026-01-01T00:00:00+00:00"},
            }, origin="test")
            self.registry.transition(version, "active", origin="test")
            return run_acquisition(self.root, self.registry, name, "files", "backfill")
    return Store()




# ADR-0255: a source snapshot can hold several parquet members. Each member
# stays manifest-verified and bounded before it becomes Python records.
def test_multi_file_reader_uses_every_manifest_member_and_returns_source_snapshots(blob_store, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    pq.write_table(pa.table({"date": ["2025-12-31", "2026-01-01"], "symbol": ["A", "A"]}),
                   first / "a.parquet", row_group_size=1)
    pq.write_table(pa.table({"date": ["2025-12-30"], "symbol": ["B"]}), second / "b.parquet")
    blob_store.add("one", first)
    blob_store.add("two", second)
    node = ParquetStreamRows("bars", {"sources": [
        {"root": blob_store.path, "source": "one", "stream": "files",
         "snapshot": payload_files(blob_store.path, "one", "files")["manifest_sha256"],
         "columns": {"date": "date", "symbol": "symbol"},
         "read_bounds": {"date": {"end_before": "2026-01-01"}}},
        {"root": blob_store.path, "source": "two", "stream": "files",
         "snapshot": payload_files(blob_store.path, "two", "files")["manifest_sha256"],
         "columns": {"date": "date", "symbol": "symbol"},
         "read_bounds": {"date": {"end_before": "2026-01-01"}}},
    ]})
    assert node.run(_ctx(tmp_path), {})["records"] == [
        {"date": "2025-12-31", "symbol": "A"}, {"date": "2025-12-30", "symbol": "B"}]
    fingerprint = node.fingerprint()
    assert fingerprint["kind"] == "ParquetStreamRows"
    assert [(item["source"], item["members"]) for item in fingerprint["snapshots"]] == [
        ("one", ["a.parquet"]), ("two", ["b.parquet"])]


def test_multi_file_reader_refuses_a_manifest_member_that_moves_before_scanning(blob_store, tmp_path, monkeypatch):
    archive = tmp_path / "source"
    archive.mkdir()
    pq.write_table(pa.table({"date": ["2025-12-31"], "payload": [1]}), archive / "safe.parquet")
    blob_store.add("one", archive)
    node = ParquetStreamRows("bars", {"sources": [{
        "root": blob_store.path, "source": "one", "stream": "files",
         "snapshot": payload_files(blob_store.path, "one", "files")["manifest_sha256"],
        "columns": {"date": "date", "payload": "payload"},
        "read_bounds": {"date": {"end_before": "2026-01-01"}},
    }]})
    path = next(Path(blob_store.path).rglob("safe.parquet"))
    path.write_bytes(path.read_bytes() + b"changed")
    import pyarrow.dataset as ds
    monkeypatch.setattr(ds, "dataset", lambda *a, **kw: pytest.fail("scanner called"))

    with pytest.raises(AssetError, match="verification|sha256"):
        node.run(_ctx(tmp_path), {})


def test_multi_file_requires_explicit_snapshot_pin():
    problems = ParquetStreamRows.validate_params({"sources": [{
        "root": "./ob", "source": "x", "stream": "files",
        "columns": {"date": "date"},
        "read_bounds": {"date": {"end_before": "2026-01-01"}},
    }]})
    assert any("snapshot" in problem for problem in problems)


def test_multi_file_pinned_snapshot_survives_newer_partial_inventory(blob_store, tmp_path):
    archive = tmp_path / "pinned"
    archive.mkdir()
    for name, day in (("a", "2025-01-01"), ("b", "2025-01-02")):
        pq.write_table(pa.table({"date": [day]}), archive / (name + ".parquet"))
    blob_store.add("pinned", archive)
    pin = payload_files(blob_store.path, "pinned", "files")["manifest_sha256"]
    declaration = {"root": blob_store.path, "source": "pinned", "stream": "files",
                   "snapshot": pin, "columns": {"date": "date"},
                   "read_bounds": {"date": {"end_before": "2026-01-01"}}}
    node = ParquetStreamRows("p", {"sources": [declaration]})
    before = node.fingerprint()
    (archive / "a.parquet").unlink()
    pq.write_table(pa.table({"date": ["2025-01-03"]}), archive / "b.parquet")
    run_acquisition(blob_store.root, blob_store.registry, "pinned", "files", "backfill")
    assert node.run(_ctx(tmp_path), {})["records"] == [
        {"date": "2025-01-01"}, {"date": "2025-01-02"}]
    assert node.fingerprint() == before
    assert before["snapshots"][0]["manifest_sha256"] == pin
    bad = {**declaration, "source": "another-source"}
    with pytest.raises(ValueError, match="source"):
        ParquetStreamRows("p", {"sources": [bad]}).run(_ctx(tmp_path), {})


def test_multi_file_reader_registered_kind():
    assert ("parquet-stream-rows", ParquetStreamRows) in pack.NODE_KINDS


def test_bounded_batches_stream_the_same_rows_under_an_extra_predicate(tmp_path):
    import datetime as dt
    import pyarrow.dataset as ds
    days = [dt.date(2025, 12, 30), dt.date(2025, 12, 31), dt.date(2026, 1, 2)] * 2
    path = tmp_path / "rows.parquet"
    pq.write_table(pa.table({"sym": ["A"] * 3 + ["B"] * 3, "d": pa.array(days, pa.date32()),
                             "value": [1, 2, 3, 4, 5, 6]}), path, row_group_size=3)
    bounded = pack.DateBoundedParquet(path, ["value"], {"d": {"end_before": "2026-01-01"}})
    both = [row for batch in bounded.batches(batch_size=2) for row in batch.to_pylist()]
    assert both == bounded.read().to_pylist() == [{"value": v} for v in (1, 2, 4, 5)]
    only_b = [row for batch in bounded.batches(where=ds.field("sym") == "B") for row in batch.to_pylist()]
    assert only_b == [{"value": 4}, {"value": 5}]


def test_bounded_batches_refuse_unverified_dates_before_scanning(tmp_path):
    path = tmp_path / "source.parquet"
    pq.write_table(pa.table({"d": pa.array([None], type=pa.string()), "value": [7]}), path)
    with pytest.raises(ValueError, match="temporal predicate"):
        list(pack.DateBoundedParquet(path, ["value"], {"d": {"end_before": "2026-01-01"}}).batches())


# ADR-0265 Amendment 5: a snapshot that also holds raw responses (the Yahoo bar pulls keep _raw/*.json beside
# <ticker>/underlying_prices.parquet) is read through an include filter on the member path.
def _bar_snapshot(blob_store, tmp_path, name="bars"):
    archive = tmp_path / name
    for ticker, day in (("aa", "2025-01-02"), ("bb", "2025-01-03")):
        (archive / ticker).mkdir(parents=True)
        pq.write_table(pa.table({"date": [day], "symbol": [ticker.upper()]}), archive / ticker / "underlying_prices.parquet")
    (archive / "_raw").mkdir()
    (archive / "_raw" / "AA.json").write_text("{}")
    blob_store.add(name, archive)
    return {"root": blob_store.path, "source": name, "stream": "files",
            "snapshot": payload_files(blob_store.path, name, "files")["manifest_sha256"],
            "columns": {"date": "date", "symbol": "symbol"},
            "read_bounds": {"date": {"end_before": "2026-01-01"}}}


def test_include_reads_only_the_matching_members_in_manifest_order(blob_store, tmp_path):
    declaration = _bar_snapshot(blob_store, tmp_path)
    node = ParquetStreamRows("bars", {"sources": [{**declaration, "include": ["*/underlying_prices.parquet"]}]})
    assert node.run(_ctx(tmp_path), {})["records"] == [
        {"date": "2025-01-02", "symbol": "AA"}, {"date": "2025-01-03", "symbol": "BB"}]
    assert node.fingerprint()["snapshots"][0]["members"] == ["aa/underlying_prices.parquet", "bb/underlying_prices.parquet"]
    narrow = ParquetStreamRows("bars", {"sources": [{**declaration, "include": ["aa/*"]}]})
    assert narrow.run(_ctx(tmp_path), {})["records"] == [{"date": "2025-01-02", "symbol": "AA"}]


def test_without_include_a_non_parquet_member_is_still_read_and_fails_loudly(blob_store, tmp_path):
    declaration = _bar_snapshot(blob_store, tmp_path)
    with pytest.raises(Exception):
        ParquetStreamRows("bars", {"sources": [declaration]}).run(_ctx(tmp_path), {})


def test_include_matching_nothing_refuses_and_a_bad_include_is_a_declaration_problem(blob_store, tmp_path):
    declaration = _bar_snapshot(blob_store, tmp_path)
    with pytest.raises(ValueError, match="include"):
        ParquetStreamRows("bars", {"sources": [{**declaration, "include": ["zz/*"]}]}).fingerprint()
    for bad in ([], "x", [1], [""]):
        assert any("include" in p for p in ParquetStreamRows.validate_params({"sources": [{**declaration, "include": bad}]}))


def test_iso_fields_turn_date_columns_into_iso_text(blob_store, tmp_path):
    import datetime as dt
    archive = tmp_path / "dates"
    archive.mkdir()
    pq.write_table(pa.table({"d": pa.array([dt.date(2020, 3, 2), dt.date(2020, 3, 3)], type=pa.date32()), "x": [1.0, 2.0]}),
                   archive / "a.parquet")
    blob_store.add("dates", archive)
    declaration = {"root": blob_store.path, "source": "dates", "stream": "files",
                   "snapshot": payload_files(blob_store.path, "dates", "files")["manifest_sha256"],
                   "columns": {"d": "date", "x": "x"}, "read_bounds": {"d": {"end_before": "2026-01-01"}}}
    plain = ParquetStreamRows("p", {"sources": [declaration]}).run(_ctx(tmp_path), {})["records"]
    assert plain[0]["date"] == dt.date(2020, 3, 2)
    node = ParquetStreamRows("p", {"sources": [{**declaration, "iso_fields": ["date"]}]})
    assert node.run(_ctx(tmp_path), {})["records"] == [{"date": "2020-03-02", "x": 1.0}, {"date": "2020-03-03", "x": 2.0}]
    for bad in ([], "date", [1], ["nope"]):
        assert any("iso_fields" in p for p in ParquetStreamRows.validate_params({"sources": [{**declaration, "iso_fields": bad}]})), bad


def test_where_keeps_only_the_rows_that_pass_every_clause_on_output_fields(blob_store, tmp_path):
    archive = tmp_path / "wheres"
    archive.mkdir()
    pq.write_table(pa.table({"d": ["2025-01-02", "2025-01-02", "2025-01-03"], "t": [30, 90, 30],
                             "ok": [True, True, False], "x": [1.0, 2.0, 3.0]}), archive / "a.parquet")
    blob_store.add("wheres", archive)
    declaration = {"root": blob_store.path, "source": "wheres", "stream": "files",
                   "snapshot": payload_files(blob_store.path, "wheres", "files")["manifest_sha256"],
                   "columns": {"d": "date", "t": "target", "ok": "ok", "x": "x"},
                   "read_bounds": {"d": {"end_before": "2026-01-01"}}}
    rows = ParquetStreamRows("p", {"sources": [{**declaration, "where": [
        {"field": "target", "op": "==", "value": 30}, {"field": "ok", "op": "==", "value": True}]}]}).run(_ctx(tmp_path), {})["records"]
    assert rows == [{"date": "2025-01-02", "target": 30, "ok": True, "x": 1.0}]
    nothing = ParquetStreamRows("p", {"sources": [{**declaration, "where": [{"field": "x", "op": ">", "value": 9}]}]})
    assert nothing.run(_ctx(tmp_path), {})["records"] == []
    for bad in ([], "x", [{"field": "nope", "op": "==", "value": 1}], [{"field": "x", "op": "~", "value": 1}]):
        assert any("where" in p for p in ParquetStreamRows.validate_params({"sources": [{**declaration, "where": bad}]})), bad


def test_where_on_a_date_column_not_in_iso_fields_refuses_instead_of_dropping_every_row(blob_store, tmp_path):
    import datetime as dt
    archive = tmp_path / "wheredate"
    archive.mkdir()
    pq.write_table(pa.table({"d": pa.array([dt.date(2020, 3, 2)], type=pa.date32()), "x": [1.0]}), archive / "a.parquet")
    blob_store.add("wheredate", archive)
    declaration = {"root": blob_store.path, "source": "wheredate", "stream": "files",
                   "snapshot": payload_files(blob_store.path, "wheredate", "files")["manifest_sha256"],
                   "columns": {"d": "date", "x": "x"}, "read_bounds": {"d": {"end_before": "2026-01-01"}},
                   "where": [{"field": "date", "op": ">=", "value": "2020-01-01"}]}
    with pytest.raises(ValueError, match="iso_fields"):
        ParquetStreamRows("p", {"sources": [declaration]}).run(_ctx(tmp_path), {})
    ok = ParquetStreamRows("p", {"sources": [{**declaration, "iso_fields": ["date"]}]}).run(_ctx(tmp_path), {})["records"]
    assert ok == [{"date": "2020-03-02", "x": 1.0}]


def test_the_where_date_guard_looks_past_leading_nulls(blob_store, tmp_path):
    import datetime as dt
    archive = tmp_path / "wherenull"
    archive.mkdir()
    pq.write_table(pa.table({"d": pa.array([dt.date(2020, 3, 2), dt.date(2020, 3, 3)], type=pa.date32()),
                             "e": pa.array([None, dt.date(2020, 4, 1)], type=pa.date32())}), archive / "a.parquet")
    blob_store.add("wherenull", archive)
    declaration = {"root": blob_store.path, "source": "wherenull", "stream": "files",
                   "snapshot": payload_files(blob_store.path, "wherenull", "files")["manifest_sha256"],
                   "columns": {"d": "date", "e": "expiry"}, "read_bounds": {"d": {"end_before": "2026-01-01"}},
                   "where": [{"field": "expiry", "op": ">=", "value": "2020-01-01"}]}
    with pytest.raises(ValueError, match="iso_fields"):
        ParquetStreamRows("p", {"sources": [declaration]}).run(_ctx(tmp_path), {})


def _descending_file(path, kind):
    """Three row groups written newest first, one date straddling two groups (the export's layout)."""
    import datetime as dt

    days = ["2024-01-05", "2024-01-04", "2024-01-04", "2024-01-03", "2024-01-02", "2024-01-01"]
    column = days if kind == "text" else pa.array([dt.date.fromisoformat(d) for d in days], type=pa.date32())
    pq.write_table(pa.table({"when": column, "px": [5.0, 4.5, 4.0, 3.0, 2.0, 1.0]}), path, row_group_size=2)
    return path


@pytest.mark.parametrize("kind", ["text", "date32"])
def test_batches_ordered_by_a_column_ascend_whatever_the_physical_row_group_order(tmp_path, kind):
    path = _descending_file(tmp_path / "desc.parquet", kind)
    reader = pack.DateBoundedParquet(str(path), ["when", "px"], {"when": {"end_before": "2024-01-05"}})
    plain = [v for b in reader.batches(batch_size=2) for v in b.column("px").to_pylist()]
    assert plain == [4.5, 4.0, 3.0, 2.0, 1.0]
    ordered = list(reader.batches(batch_size=2, order_by="when"))
    assert [v for b in ordered for v in b.column("px").to_pylist()] == [1.0, 2.0, 3.0, 4.0, 4.5]
    assert all(b.num_rows <= 2 for b in ordered)
    assert [b.schema.names for b in ordered] == [["when", "px"]] * len(ordered)


def test_order_by_must_name_a_projected_column(tmp_path):
    path = _descending_file(tmp_path / "desc.parquet", "text")
    reader = pack.DateBoundedParquet(str(path), ["px"], {"when": {"end_before": "2024-01-05"}})
    with pytest.raises(ValueError, match="order_by"):
        list(reader.batches(order_by="when"))


def test_row_groups_that_interleave_in_the_order_column_refuse_by_name(tmp_path):
    path = tmp_path / "mixed.parquet"
    pq.write_table(pa.table({"when": ["2024-01-01", "2024-01-03", "2024-01-02", "2024-01-04"],
                             "px": [1.0, 3.0, 2.0, 4.0]}), path, row_group_size=2)
    reader = pack.DateBoundedParquet(str(path), ["when", "px"], {"when": {"end_before": "2024-02-01"}})
    with pytest.raises(ValueError, match="interleave"):
        list(reader.batches(order_by="when"))


def test_a_row_group_shared_by_two_entities_is_ranked_by_the_rows_the_predicate_keeps(tmp_path):
    import pyarrow.dataset as ds

    path = tmp_path / "shared.parquet"
    pq.write_table(pa.table({"who": ["A", "A", "A", "B", "B", "B"],
                             "when": ["2024-01-03", "2024-01-02", "2024-01-01", "2024-01-09", "2024-01-08", "2024-01-07"],
                             "px": [3.0, 2.0, 1.0, 9.0, 8.0, 7.0]}), path, row_group_size=2)
    reader = pack.DateBoundedParquet(str(path), ["who", "when", "px"], {"when": {"end_before": "2024-02-01"}})
    for who, want in (("A", [1.0, 2.0, 3.0]), ("B", [7.0, 8.0, 9.0])):
        got = [v for b in reader.batches(where=ds.field("who") == who, order_by="when")
               for v in b.column("px").to_pylist()]
        assert got == want


def test_a_null_in_the_order_column_refuses_instead_of_sorting_around_it(tmp_path):
    import datetime as dt

    path = tmp_path / "nulls.parquet"
    when = pa.array([None, dt.date(2024, 1, 2), dt.date(2024, 1, 1)], type=pa.date32())
    pq.write_table(pa.table({"when": when, "day": ["2024-01-01"] * 3, "px": [0.0, 2.0, 1.0]}), path, row_group_size=2)
    reader = pack.DateBoundedParquet(str(path), ["when", "px"], {"day": {"end_before": "2024-02-01"}})
    with pytest.raises(ValueError, match="holds nulls"):
        list(reader.batches(order_by="when"))


def test_row_groups_with_equal_minima_rank_by_their_maxima(tmp_path):
    path = tmp_path / "ties.parquet"
    pq.write_table(pa.table({"when": ["2024-01-02", "2024-01-01", "2024-01-01", "2024-01-01"],
                             "px": [2.0, 1.0, 1.0, 1.0]}), path, row_group_size=2)
    reader = pack.DateBoundedParquet(str(path), ["when", "px"], {"when": {"end_before": "2024-02-01"}})
    got = [v for b in reader.batches(order_by="when") for v in b.column("when").to_pylist()]
    assert got == ["2024-01-01", "2024-01-01", "2024-01-01", "2024-01-02"]


def test_sqlite_parquet_index_preserves_source_order_and_normalizes_numeric_keys(tmp_path):
    source = tmp_path / "quotes.parquet"
    target = tmp_path / "quotes.sqlite"
    table = pa.table({"symbol": ["X", "X", "X"], "date": ["2019-01-03"]*3,
                      "expiration": ["2019-02-01"]*3, "type": ["put"]*3,
                      "strike_raw": [10, 10.0, 11], "price": [1.0, 2.0, 3.0]})
    pq.write_table(table, source, row_group_size=1)
    # The first two strike representations are the same Python tuple identity.
    with pytest.raises(Exception, match="UNIQUE|unique|duplicate"):
        pack.SQLiteParquetIndex.build(str(source), str(target),
            ["symbol", "date", "expiration", "type", "strike_raw", "price"],
            {"date": {"start": "2019-01-01", "end_before": "2020-01-01"}},
            unique_by=("symbol", "date", "expiration", "type", "strike_raw"),
            partition_by=("date", "expiration"), float_keys=("strike_raw",), batch_size=1)


def test_sqlite_parquet_index_lookup_partition_and_digest_boundaries(tmp_path):
    source = tmp_path / "quotes.parquet"
    target = tmp_path / "quotes.sqlite"
    pq.write_table(pa.table({"symbol": ["X", "X", "X"],
        "date": ["2019-01-03", "2019-01-02", "2019-01-02"],
        "expiration": ["2019-02-01"]*3, "type": ["put", "put", "call"],
        "strike_raw": [10.0, 9.0, 11.0], "price": [1.0, 2.0, 3.0]}), source,
        row_group_size=1)
    descriptor = pack.SQLiteParquetIndex.build(str(source), str(target),
        ["symbol", "date", "expiration", "type", "strike_raw", "price"],
        {"date": {"start": "2019-01-01", "end_before": "2020-01-01"}},
        unique_by=("symbol", "date", "expiration", "type", "strike_raw"),
        partition_by=("date", "expiration"), float_keys=("strike_raw",), batch_size=1)
    index = pack.SQLiteParquetIndex(descriptor, fetch_size=1)
    assert [r["type"] for r in index.iter_partition(date="2019-01-02", expiration="2019-02-01")] == ["put", "call"]
    assert index.get({"symbol": "X", "date": "2019-01-03", "expiration": "2019-02-01",
                      "type": "put", "strike_raw": 10})["price"] == 1.0
    with pytest.raises(Exception):
        index.connection.execute("DELETE FROM rows")
    index.verify_digest()
    index.close(verify=True)
    target.write_bytes(target.read_bytes()+b"drift")
    with pytest.raises(ValueError, match="digest mismatch"):
        pack.SQLiteParquetIndex(descriptor)


def test_sqlite_parquet_index_cleans_partial_build_and_reads_without_rescanning(tmp_path, monkeypatch):
    source = tmp_path / "quotes.parquet"
    target = tmp_path / "quotes.sqlite"
    pq.write_table(pa.table({"symbol": ["X", "X"], "date": ["2019-01-02"]*2,
        "expiration": ["2019-02-01"]*2, "type": ["put", "call"],
        "strike_raw": [10.0, 11.0], "price": [1.0, 2.0]}), source, row_group_size=1)
    args = (str(source), str(target), ["symbol", "date", "expiration", "type", "strike_raw", "price"],
            {"date": {"start": "2019-01-01", "end_before": "2020-01-01"}})
    with pytest.raises(ValueError, match="injected"):
        pack.SQLiteParquetIndex.build(*args, unique_by=("symbol", "date", "expiration", "type", "strike_raw"),
            partition_by=("date", "expiration"), float_keys=("strike_raw",),
            batch_size=1, validator=lambda row: (_ for _ in ()).throw(ValueError("injected")))
    assert not target.exists() and not Path(str(target)+".building").exists()
    descriptor = pack.SQLiteParquetIndex.build(*args, unique_by=("symbol", "date", "expiration", "type", "strike_raw"),
        partition_by=("date", "expiration"), float_keys=("strike_raw",), batch_size=1)
    index = pack.SQLiteParquetIndex(descriptor)
    monkeypatch.setattr(pack.DateBoundedParquet, "batches",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("source rescanned")))
    assert len(list(index.iter_partition(date="2019-01-02", expiration="2019-02-01"))) == 2
    assert index.get({"symbol": "X", "date": "2019-01-02", "expiration": "2019-02-01",
                      "type": "put", "strike_raw": 10})["price"] == 1.0
    index.close(verify=True)


def test_sqlite_index_reuses_verified_input_without_another_large_build(tmp_path, monkeypatch):
    source = tmp_path / "quotes.parquet"
    pq.write_table(pa.table({"symbol": ["X", "X"], "date": ["2019-01-02"]*2,
        "expiration": ["2019-02-01"]*2, "type": ["put", "call"],
        "strike_raw": [10.0, 11.0], "price": [1.0, 2.0]}), source)
    kwargs = {"unique_by": ("symbol", "date", "expiration", "type", "strike_raw"),
              "partition_by": ("date", "expiration"), "float_keys": ("strike_raw",),
              "reuse_root": tmp_path / "cache",
              "reuse_identity": {"verified_source_sha256": path_hash(str(source)), "filter": "all",
                                 "validator": "records-only-v1"},
              "replay_validator": True}
    args = (str(source), None,
            ["symbol", "date", "expiration", "type", "strike_raw", "price"],
            {"date": {"start": "2019-01-01", "end_before": "2020-01-01"}})
    seen_first, seen_second = [], []
    first = tmp_path / "run-one" / "quotes.sqlite"
    second = tmp_path / "run-two" / "quotes.sqlite"
    one = pack.SQLiteParquetIndex.build(args[0], str(first), *args[2:],
        **kwargs, validator=lambda row: seen_first.append(dict(row)))
    stale = Path(str(second) + ".building")
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"interrupted prior build")
    advised = []
    monkeypatch.setattr(os, "posix_fadvise", lambda fd, start, length, advice:
                        advised.append((start, length, advice)))
    monkeypatch.setattr(pack.DateBoundedParquet, "batches",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("index rebuilt")))
    two = pack.SQLiteParquetIndex.build(args[0], str(second), *args[2:],
        **kwargs, validator=lambda row: seen_second.append(dict(row)))
    cached = list((tmp_path / "cache").rglob("*.sqlite"))
    assert len(cached) == 1
    assert seen_second == seen_first and len(seen_second) == 2
    assert one["sha256"] == two["sha256"] and one["path"] == str(first)
    assert two["path"] == str(second)
    assert not stale.exists()
    assert any(length == 0 and advice == os.POSIX_FADV_DONTNEED
               for _, length, advice in advised)
    assert len({first.stat().st_ino, second.stat().st_ino, cached[0].stat().st_ino}) == 1
    index = pack.SQLiteParquetIndex(two)
    try:
        assert [r["price"] for r in index.iter_partition(
            date="2019-01-02", expiration="2019-02-01")] == [1.0, 2.0]
    finally:
        index.close()
    refused = tmp_path / "run-refused" / "quotes.sqlite"
    with pytest.raises(ValueError, match="replayed refusal"):
        pack.SQLiteParquetIndex.build(args[0], str(refused), *args[2:],
            **kwargs, validator=lambda row: (_ for _ in ()).throw(
                ValueError("replayed refusal")) if row["price"] == 2.0 else None)
    assert not refused.exists()


def test_sqlite_index_cache_refuses_corruption_before_publishing_run_path(tmp_path):
    source = tmp_path / "quotes.parquet"
    pq.write_table(pa.table({"symbol": ["X"], "date": ["2019-01-02"],
                             "expiration": ["2019-02-01"], "price": [1.0]}), source)
    kwargs = {"unique_by": ("symbol", "date", "expiration"),
              "partition_by": ("date", "expiration"), "reuse_root": tmp_path / "cache",
              "reuse_identity": {"verified_source_sha256": path_hash(str(source)), "filter": "all"}}
    args = (str(source), ["symbol", "date", "expiration", "price"],
            {"date": {"start": "2019-01-01", "end_before": "2020-01-01"}})
    first = tmp_path / "one.sqlite"
    descriptor = pack.SQLiteParquetIndex.build(args[0], str(first), *args[1:], **kwargs)
    cached = next((tmp_path / "cache").rglob("*.sqlite"))
    replacement = tmp_path / "corrupt.sqlite"
    replacement.write_bytes(b"not SQLite")
    os.replace(replacement, cached)
    second = tmp_path / "two.sqlite"
    with pytest.raises(ValueError, match="cache.*digest|digest.*cache"):
        pack.SQLiteParquetIndex.build(args[0], str(second), *args[1:], **kwargs)
    assert not second.exists()
    index = pack.SQLiteParquetIndex(descriptor)
    try:
        assert index.get({"symbol": "X", "date": "2019-01-02", "expiration": "2019-02-01"})["price"] == 1.0
    finally:
        index.close()


def test_sqlite_index_refuses_source_drift_before_cache_publication(tmp_path, monkeypatch):
    source = tmp_path / "quotes.parquet"
    def write_price(price):
        pq.write_table(pa.table({"symbol": ["X"], "date": ["2019-01-02"],
                                 "expiration": ["2019-02-01"], "price": [price]}), source)
    write_price(1.0)
    identity = {"verified_source_sha256": path_hash(str(source)), "filter": "all"}
    cache = tmp_path / "cache"
    destination = tmp_path / "occupied.sqlite"
    destination.write_bytes(b"prior run-local output")
    kwargs = {"unique_by": ("symbol", "date", "expiration"),
              "partition_by": ("date", "expiration"), "reuse_root": cache,
              "reuse_identity": identity}
    columns = ["symbol", "date", "expiration", "price"]
    bounds = {"date": {"start": "2019-01-01", "end_before": "2020-01-01"}}
    original_batches = pack.DateBoundedParquet.batches
    drifted = []
    def drift_at_scan_end(self, *args, **kwargs):
        yield from original_batches(self, *args, **kwargs)
        if not drifted:
            write_price(2.0)
            drifted.append(True)
    monkeypatch.setattr(pack.DateBoundedParquet, "batches", drift_at_scan_end)
    with pytest.raises(ValueError, match="source digest drift"):
        pack.SQLiteParquetIndex.build(str(source), str(destination), columns, bounds, **kwargs)
    assert destination.read_bytes() == b"prior run-local output"
    assert not Path(str(destination) + ".building").exists()
    assert not list(cache.rglob("*.sqlite")) and not list(cache.rglob("*.json"))
    write_price(1.0)
    clean = pack.SQLiteParquetIndex.build(str(source), str(tmp_path / "clean.sqlite"),
        columns, bounds, **kwargs)
    index = pack.SQLiteParquetIndex(clean)
    try:
        assert index.get({"symbol": "X", "date": "2019-01-02",
                          "expiration": "2019-02-01"})["price"] == 1.0
    finally:
        index.close()


def test_sqlite_index_cross_device_hit_builds_locally(tmp_path, monkeypatch):
    source = tmp_path / "quotes.parquet"
    pq.write_table(pa.table({"symbol": ["X"], "date": ["2019-01-02"],
                             "expiration": ["2019-02-01"], "price": [1.0]}), source)
    kwargs = {"unique_by": ("symbol", "date", "expiration"),
              "partition_by": ("date", "expiration"), "reuse_root": tmp_path / "cache",
              "reuse_identity": {"verified_source_sha256": path_hash(str(source)), "filter": "all"},
              "replay_validator": True}
    args = (str(source), ["symbol", "date", "expiration", "price"],
            {"date": {"start": "2019-01-01", "end_before": "2020-01-01"}})
    first = tmp_path / "one.sqlite"
    original = pack.SQLiteParquetIndex.build(args[0], str(first), *args[1:],
        **kwargs, validator=lambda row: None)
    cached = next((tmp_path / "cache").rglob("*.sqlite"))
    original_link = os.link
    def cross_device(src, dst, *a, **k):
        if str(src) == str(cached):
            raise OSError(errno.EXDEV, "different filesystem")
        return original_link(src, dst, *a, **k)
    monkeypatch.setattr(os, "link", cross_device)
    second = tmp_path / "two.sqlite"
    seen = []
    descriptor = pack.SQLiteParquetIndex.build(args[0], str(second), *args[1:],
        **kwargs, validator=lambda row: seen.append(dict(row)))
    assert second.exists() and second.stat().st_ino != first.stat().st_ino
    assert descriptor["sha256"] == original["sha256"]
    assert len(seen) == 1


def test_sqlite_index_missing_cache_blob_rebuilds_from_source(tmp_path):
    source = tmp_path / "quotes.parquet"
    pq.write_table(pa.table({"symbol": ["X"], "date": ["2019-01-02"],
                             "expiration": ["2019-02-01"], "price": [1.0]}), source)
    kwargs = {"unique_by": ("symbol", "date", "expiration"),
              "partition_by": ("date", "expiration"), "reuse_root": tmp_path / "cache",
              "reuse_identity": {"verified_source_sha256": path_hash(str(source)), "filter": "all"}}
    columns = ["symbol", "date", "expiration", "price"]
    bounds = {"date": {"start": "2019-01-01", "end_before": "2020-01-01"}}
    first = tmp_path / "one.sqlite"
    one = pack.SQLiteParquetIndex.build(str(source), str(first), columns, bounds, **kwargs)
    cached = next((tmp_path / "cache").rglob("*.sqlite"))
    cached.unlink()
    second = tmp_path / "two.sqlite"
    two = pack.SQLiteParquetIndex.build(str(source), str(second), columns, bounds, **kwargs)
    assert second.exists() and two["sha256"] == one["sha256"]
    assert next((tmp_path / "cache").rglob("*.sqlite")).stat().st_ino == second.stat().st_ino


def test_sqlite_index_concurrent_cache_publication_verifies_winner(tmp_path, monkeypatch):
    source = tmp_path / "quotes.parquet"
    pq.write_table(pa.table({"symbol": ["X"], "date": ["2019-01-02"],
                             "expiration": ["2019-02-01"], "price": [1.0]}), source)
    cache_root = tmp_path / "cache"
    original_link = os.link
    collided = []
    def competing_writer(src, dst, *a, **k):
        if str(dst).endswith(".sqlite") and str(dst).startswith(str(cache_root)) and not collided:
            collided.append(True)
            original_link(src, dst, *a, **k)
            raise FileExistsError(errno.EEXIST, "peer published", str(dst))
        return original_link(src, dst, *a, **k)
    monkeypatch.setattr(os, "link", competing_writer)
    destination = tmp_path / "run" / "quotes.sqlite"
    descriptor = pack.SQLiteParquetIndex.build(str(source), str(destination),
        ["symbol", "date", "expiration", "price"],
        {"date": {"start": "2019-01-01", "end_before": "2020-01-01"}},
        unique_by=("symbol", "date", "expiration"), partition_by=("date", "expiration"),
        reuse_root=cache_root,
        reuse_identity={"verified_source_sha256": path_hash(str(source)), "filter": "all"})
    assert collided and destination.exists()
    assert next(cache_root.rglob("*.sqlite")).stat().st_ino == destination.stat().st_ino
    assert descriptor["sha256"] == path_hash(str(destination))


def test_index_hash_evicts_consumed_pages_without_changing_digest(tmp_path, monkeypatch):
    file = tmp_path / "large.bin"
    file.write_bytes(b"x" * (3 << 20))
    advised = []
    monkeypatch.setattr(os, "posix_fadvise", lambda fd, start, length, advice:
                        advised.append((start, length, advice)))
    assert path_hash(str(file), evict=True) == path_hash(str(file))
    assert len(advised) >= 3
    assert advised[0] == (0, 1 << 20, os.POSIX_FADV_DONTNEED)


def test_sqlite_index_rejects_swapped_input_manifest(tmp_path):
    cache = tmp_path / "cache"
    kwargs = {"unique_by": ("symbol", "date", "expiration"),
              "partition_by": ("date", "expiration"), "reuse_root": cache}
    columns = ["symbol", "date", "expiration", "price"]
    bounds = {"date": {"start": "2019-01-01", "end_before": "2020-01-01"}}
    sources = []
    descriptors = []
    for number in (1, 2):
        source = tmp_path / f"quotes-{number}.parquet"
        pq.write_table(pa.table({"symbol": ["X"], "date": ["2019-01-02"],
            "expiration": ["2019-02-01"], "price": [float(number)]}), source)
        identity = {"verified_source_sha256": path_hash(str(source)), "filter": "all"}
        descriptor = pack.SQLiteParquetIndex.build(str(source), str(tmp_path / f"run-{number}.sqlite"),
            columns, bounds, **kwargs, reuse_identity=identity)
        sources.append((source, identity))
        descriptors.append(descriptor)
    manifests = list((cache / "inputs").rglob("*.json"))
    first_manifest = next(p for p in manifests
        if json.loads(p.read_text())["sha256"] == descriptors[0]["sha256"])
    second_manifest = next(p for p in manifests if p != first_manifest)
    replacement = tmp_path / "wrong.json"
    replacement.write_bytes(second_manifest.read_bytes())
    os.replace(replacement, first_manifest)
    third = tmp_path / "run-three.sqlite"
    with pytest.raises(ValueError, match="manifest"):
        pack.SQLiteParquetIndex.build(str(sources[0][0]), str(third), columns, bounds,
            **kwargs, reuse_identity=sources[0][1])
    assert not third.exists()
    forged = json.loads(second_manifest.read_text())
    forged["input_sha256"] = first_manifest.stem
    replacement.write_text(json.dumps(forged))
    os.replace(replacement, first_manifest)
    fourth = tmp_path / "run-four.sqlite"
    with pytest.raises(ValueError, match="input identity"):
        pack.SQLiteParquetIndex.build(str(sources[0][0]), str(fourth), columns, bounds,
            **kwargs, reuse_identity=sources[0][1])
    assert not fourth.exists()


def test_sqlite_index_same_input_key_cannot_overwrite_different_manifest(tmp_path):
    source = tmp_path / "quotes.parquet"
    pq.write_table(pa.table({"symbol": ["X"], "date": ["2019-01-02"],
                             "expiration": ["2019-02-01"], "price": [1.0]}), source)
    cache = tmp_path / "cache"
    identity = {"verified_source_sha256": path_hash(str(source)), "filter": "all",
                "validator": "same declared behavior"}
    kwargs = {"unique_by": ("symbol", "date", "expiration"),
              "partition_by": ("date", "expiration"), "reuse_root": cache,
              "reuse_identity": identity}
    columns = ["symbol", "date", "expiration", "price"]
    bounds = {"date": {"start": "2019-01-01", "end_before": "2020-01-01"}}
    first = pack.SQLiteParquetIndex.build(str(source), str(tmp_path / "one.sqlite"),
        columns, bounds, **kwargs, validator=lambda row: None)
    manifest = next((cache / "inputs").rglob("*.json"))
    original = manifest.read_bytes()
    with pytest.raises(ValueError, match="identity collision"):
        pack.SQLiteParquetIndex.build(str(source), str(tmp_path / "two.sqlite"),
            columns, bounds, **kwargs, validator=lambda row: row.update(price=2.0))
    assert manifest.read_bytes() == original
    assert first["sha256"] == json.loads(manifest.read_text())["sha256"]
    assert not (tmp_path / "two.sqlite").exists()
    assert len(list(cache.rglob("*.sqlite"))) == 1
    occupied = tmp_path / "occupied.sqlite"
    occupied.write_bytes(b"prior run-local output")
    with pytest.raises(ValueError, match="identity collision"):
        pack.SQLiteParquetIndex.build(str(source), str(occupied), columns, bounds,
            **kwargs, validator=lambda row: row.update(price=2.0))
    assert occupied.read_bytes() == b"prior run-local output"
    assert len(list(cache.rglob("*.sqlite"))) == 1


def test_sqlite_index_invalid_reuse_root_has_no_filesystem_effect(tmp_path):
    source = tmp_path / "quotes.parquet"
    pq.write_table(pa.table({"symbol": ["X"], "date": ["2019-01-02"],
                             "expiration": ["2019-02-01"], "price": [1.0]}), source)
    destination = tmp_path / "absent" / "quotes.sqlite"
    with pytest.raises(ValueError, match="absolute index reuse root"):
        pack.SQLiteParquetIndex.build(str(source), str(destination),
            ["symbol", "date", "expiration", "price"],
            {"date": {"start": "2019-01-01", "end_before": "2020-01-01"}},
            unique_by=("symbol", "date", "expiration"), partition_by=("date", "expiration"),
            reuse_root="relative-cache")
    assert not destination.parent.exists()
