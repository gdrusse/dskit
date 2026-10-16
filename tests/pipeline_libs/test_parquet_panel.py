"""Bounded panel augmentation: immutable values, prior dates and atomic publication."""
import hashlib
import json
from pathlib import Path

import pytest

from dskit.onboarding import OnboardingRoot, run_acquisition
from dskit.onboarding.artifacts import payload_files
from dskit.pipeline.base import ConfigError
from dskit.pipeline.node import NodeContext


def _store(tmp_path, name, rows):
    import pyarrow as pa
    import pyarrow.parquet as pq
    archive = tmp_path / (name + "-source")
    archive.mkdir()
    pq.write_table(pa.Table.from_pylist(rows), archive / "rows.parquet", row_group_size=1)
    ob = OnboardingRoot.create(str(tmp_path / (name + "-ob")))
    registry = ob.registry()
    vid = registry.register("source_config", {
        "name": name, "catalog_source": "synthetic", "connector": "localblobs",
        "config": {"path": str(archive), "as_of": "2025-12-31T00:00:00+00:00"}}, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(ob, registry, name, "files", "backfill")
    return {"root": ob.root, "source": name, "stream": "files",
            "snapshot": payload_files(ob.root, name, "files")["manifest_sha256"],
            "columns": {k: k for k in rows[0]}, "include": ["*.parquet"]}


@pytest.fixture
def spec(tmp_path):
    base = _store(tmp_path, "base", [
        {"entity": "A", "day": "2020-01-03", "end": "2020-02-03", "y": .2, "x": 1.},
        {"entity": "A", "day": "2020-01-06", "end": "2020-02-06", "y": -.1, "x": 2.},
        {"entity": "B", "day": "2020-01-06", "end": "2020-02-06", "y": .5, "x": 3.},
        {"entity": "A", "day": "2025-12-02", "end": "2026-01-02", "y": 99., "x": 99.},
        {"entity": "A", "day": "2026-01-02", "end": "2026-02-02", "y": 99., "x": 99.}])
    aux = _store(tmp_path, "aux", [
        {"ticker": "A", "at": "2020-01-02", "v": 10.},
        {"ticker": "A", "at": "2020-01-03", "v": 20.},
        {"ticker": "A", "at": "2020-01-06", "v": 30.},
        {"ticker": "B", "at": "2019-12-20", "v": 40.},
        {"ticker": "A", "at": "2026-01-01", "v": 999.}])
    base["read_bounds"] = {"day": {"end_before": "2026-01-01"},
                           "end": {"end_before": "2026-01-01"}}
    aux["read_bounds"] = {"at": {"end_before": "2026-01-01"}}
    return {"base": base, "key": ["entity", "day"], "identity": ["entity", "day"],
            "temporal_fields": ["day", "end"], "tables": {"signals": {
                "sources": [aux], "key_fields": ["ticker", "at"],
                "columns": {"v": "signal", "at": "signal_date"},
                "max_age_days": 4, "strict_prior": True}},
            "age_suffix": "_age_days", "missing_suffix": "_missing",
            "end_before": "2026-01-01", "output": str(tmp_path / "result"),
            "max_rows": 100, "max_output_bytes": 1000000}


def _node(spec):
    from dskit.pipeline.libs.parquet_panel import ParquetPanelAugment
    return ParquetPanelAugment("augment", spec)


def _run(spec, tmp_path):
    node = _node(spec)
    node.fingerprint()
    return node.run(NodeContext(name="synthetic", asof="2025-12-31", run_dir=str(tmp_path)), {})


def test_prior_join_preserves_base_values_missingness_and_writes_verified_panel(spec, tmp_path):
    import pyarrow.parquet as pq
    result = _run(spec, tmp_path)
    dest = Path(spec["output"])
    assert (dest / "panel.parquet").is_file(), "augmentation must materialize an immutable panel"
    rows = pq.read_table(dest / "panel.parquet").to_pylist()
    assert rows == result["records"]
    assert [(r["entity"], r["day"], r["x"], r["y"]) for r in rows] == [
        ("A", "2020-01-03", 1., .2), ("A", "2020-01-06", 2., -.1),
        ("B", "2020-01-06", 3., .5)]
    assert [r["signal"] for r in rows] == [10., 20., None]
    assert [r["signal_age_days"] for r in rows] == [1, 3, None]
    assert [r["signal_missing"] for r in rows] == [0, 0, 1]
    assert [r["signal_date"] for r in rows] == ["2020-01-02", "2020-01-03", None]
    manifest = json.loads((dest / "manifest.json").read_text())
    assert manifest["rows"] == 3
    assert manifest["panel_sha256"] == hashlib.sha256((dest / "panel.parquet").read_bytes()).hexdigest()
    assert manifest["sources"]["base"]["snapshots"][0]["manifest_sha256"] == spec["base"]["snapshot"]


@pytest.mark.parametrize("edit", [
    lambda p: p["base"]["read_bounds"].pop("end"),
    lambda p: p["base"]["read_bounds"]["day"].update(end_before="2027-01-01"),
    lambda p: p["tables"]["signals"]["sources"][0]["read_bounds"].clear(),
    lambda p: p["tables"]["signals"].update(strict_prior=False),
    lambda p: p["tables"]["signals"].update(max_age_days=-1),
    lambda p: p.update(typo=1),
    lambda p: p.update(temporal_fields=[]),
])
def test_bad_declarations_refuse_without_io(spec, edit, monkeypatch):
    from dskit.pipeline.libs.parquet import ParquetStreamRows
    monkeypatch.setattr(ParquetStreamRows, "_snapshots", lambda self: pytest.fail("source resolution"))
    edit(spec)
    with pytest.raises((ConfigError, ValueError)):
        _node(spec)
    assert not Path(spec["output"]).exists()


@pytest.mark.parametrize("source", ["base", "aux"])
@pytest.mark.parametrize("entry", ["fingerprint", "run"])
def test_zero_pin_refuses_before_any_source_resolution(spec, source, entry, monkeypatch, tmp_path):
    from dskit.pipeline.libs.parquet import ParquetStreamRows
    target = spec["base"] if source == "base" else spec["tables"]["signals"]["sources"][0]
    target["snapshot"] = "0" * 64
    monkeypatch.setattr(ParquetStreamRows, "_snapshots", lambda self: pytest.fail("source resolution"))
    with pytest.raises((ConfigError, ValueError), match="pin"):
        node = _node(spec)
        node.fingerprint() if entry == "fingerprint" else node.run(None, {})
    assert not Path(spec["output"]).exists()


@pytest.mark.parametrize("change", ["base_duplicate", "table_duplicate", "collision", "null_identity", "invalid_day"])
def test_corrupt_identity_or_column_contract_never_publishes(spec, change, monkeypatch, tmp_path):
    from dskit.pipeline.libs.parquet import ParquetStreamRows
    original = ParquetStreamRows.run
    def altered(self, ctx, inputs):
        result = original(self, ctx, inputs)
        rows = result["records"]
        base = self.params["sources"][0]["source"] == "base"
        if (change == "base_duplicate" and base) or (change == "table_duplicate" and not base):
            rows.append(dict(rows[0]))
        if change == "collision" and base:
            rows[-1]["signal"] = -100.
        if change == "null_identity" and base:
            rows[-1]["entity"] = None
        if change == "invalid_day" and base:
            rows[-1]["day"] = "2020-01-06T12:00:00"
        return result
    monkeypatch.setattr(ParquetStreamRows, "run", altered)
    with pytest.raises(ValueError):
        _run(spec, tmp_path)
    assert not Path(spec["output"]).exists()


def test_existing_output_and_source_directory_are_refused_before_reads(spec, monkeypatch, tmp_path):
    from dskit.pipeline.libs.parquet import ParquetStreamRows
    monkeypatch.setattr(ParquetStreamRows, "run", lambda *a: pytest.fail("payload read"))
    dest = Path(spec["output"])
    dest.mkdir()
    (dest / "owner.txt").write_text("keep")
    with pytest.raises((ValueError, FileExistsError)):
        _run(spec, tmp_path)
    assert (dest / "owner.txt").read_text() == "keep"
    spec["output"] = str(Path(spec["base"]["root"]) / "new-output")
    with pytest.raises((ConfigError, ValueError)):
        _run(spec, tmp_path)
    assert not Path(spec["output"]).exists()


@pytest.mark.parametrize("limit", ["max_rows", "max_output_bytes"])
def test_size_limit_leaves_no_output(spec, tmp_path, limit):
    spec[limit] = 1
    with pytest.raises(ValueError, match="limit"):
        _run(spec, tmp_path)
    assert not Path(spec["output"]).exists()


def test_write_failure_cleans_only_owned_temp_and_keeps_inputs(spec, tmp_path, monkeypatch):
    import pyarrow.parquet as pq
    marker = tmp_path / "unrelated"
    marker.write_text("keep")
    def fail(*a, **k):
        raise OSError("injected disk failure")
    monkeypatch.setattr(pq, "write_table", fail)
    with pytest.raises(OSError, match="disk failure"):
        _run(spec, tmp_path)
    assert not Path(spec["output"]).exists()
    assert marker.read_text() == "keep"
    assert not list(tmp_path.glob(".result.partial-*"))


def test_direct_run_revalidates_mutated_temporal_contract(spec, tmp_path):
    node = _node(spec)
    node.params["base"]["read_bounds"].pop("end")
    with pytest.raises(ValueError):
        node.run(None, {})
    assert not Path(spec["output"]).exists()


def test_output_date_aliases_map_back_to_source_predicates(spec, tmp_path):
    spec["base"]["columns"].update(day="stamp", end="finish")
    spec["identity"] = ["entity", "stamp"]
    spec["key"] = ["entity", "stamp"]
    spec["temporal_fields"] = ["stamp", "finish"]
    result = _run(spec, tmp_path)
    assert result["records"][0]["stamp"] == "2020-01-03"
    assert result["records"][0]["signal"] == 10.


def test_duplicate_across_table_sources_refuses(spec, tmp_path):
    table = spec["tables"]["signals"]
    table["sources"].append(dict(table["sources"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        _run(spec, tmp_path)
    assert not Path(spec["output"]).exists()


def test_manifest_pin_mismatch_and_payload_drift_refuse(spec, tmp_path):
    from dskit.pipeline.libs.parquet import ParquetStreamRows
    node = _node(spec)
    node.fingerprint()
    reader = ParquetStreamRows("locate", {"sources": [spec["base"]]})
    reader.fingerprint()
    path = reader._pinned[0]["files"][0][1]
    path.write_bytes(path.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="manifest"):
        node.run(None, {})
    assert not Path(spec["output"]).exists()
    spec["base"]["snapshot"] = "f" * 64
    with pytest.raises((ValueError, FileNotFoundError)):
        _node(spec).fingerprint()


def test_null_temporal_metadata_refuses_before_arrow_materialization(spec, tmp_path, monkeypatch):
    import pyarrow.dataset as ds
    bad = _store(tmp_path, "null-date", [
        {"entity": "A", "day": "2020-01-03", "end": None, "y": .2, "x": 1.}])
    bad["read_bounds"] = spec["base"]["read_bounds"]
    spec["base"] = bad
    monkeypatch.setattr(ds, "dataset", lambda *a, **k: pytest.fail("Arrow materialization"))
    with pytest.raises(ValueError, match="temporal"):
        _run(spec, tmp_path)
    assert not Path(spec["output"]).exists()


def test_concurrent_writer_lock_refuses_before_payload(spec, tmp_path, monkeypatch):
    import fcntl
    from dskit.pipeline.libs.parquet import ParquetStreamRows
    monkeypatch.setattr(ParquetStreamRows, "run", lambda *a: pytest.fail("payload read"))
    dest = Path(spec["output"])
    with (dest.parent / f".{dest.name}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            _run(spec, tmp_path)
    assert not dest.exists()


def test_rename_failure_leaves_no_published_artifact(spec, tmp_path, monkeypatch):
    import dskit.pipeline.libs.parquet_panel as pack
    def fail(*args):
        raise OSError("injected rename failure")
    monkeypatch.setattr(pack.os, "rename", fail)
    with pytest.raises(OSError, match="rename failure"):
        _run(spec, tmp_path)
    assert not Path(spec["output"]).exists()
    assert not list(tmp_path.glob(".result.partial-*"))


def test_valid_parameter_change_after_fingerprint_refuses(spec, tmp_path):
    node = _node(spec)
    node.fingerprint()
    node.params["tables"]["signals"]["max_age_days"] = 3
    with pytest.raises(ValueError, match="changed after fingerprint"):
        node.run(None, {})
    assert not Path(spec["output"]).exists()
