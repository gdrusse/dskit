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


def test_pyarrow_is_imported_only_inside_run():
    tree = ast.parse(Path(pack.__file__).read_text())
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
            assert not any("pyarrow" in n for n in names)
