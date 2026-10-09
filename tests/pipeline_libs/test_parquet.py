"""ParquetRows (ADR-0228): a store reference resolved to a parquet file's rows,
digest-verified against the manifest, default-deny, pyarrow only inside run()."""

import ast
import itertools
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

import dskit.onboarding.acquire as acquire_module  # noqa: E402
from dskit.onboarding import OnboardingRoot, run_acquisition  # noqa: E402
from dskit.pipeline.base import ConfigError  # noqa: E402
from dskit.pipeline.libs import parquet as pack  # noqa: E402
from dskit.pipeline.libs.parquet import ParquetRows  # noqa: E402
from dskit.pipeline.node import NodeContext  # noqa: E402

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
