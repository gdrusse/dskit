"""sync.py: the outbox scan — delivery, anti-entropy, and repairs (ADR-0012)."""

import json
import os

import pytest

from dskit.assets import AssetError, Lineage, sync_published


def _outbox(tmp_path, dataset, name, manifest_extra=None, fname="00000001-abcd1234.json"):
    """Write one manifest into a published-root layout."""
    d = tmp_path / "published" / dataset
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_version": 1,
        "dataset": dataset,
        "name": name,
        "mode": "live",
        "acquired_at": "2026-08-23T12:00:00+00:00",
        "effective_range": {"start": "2026-01-02", "end": "2026-01-05"},
        "snapshots": [{"snapshot": "ab" * 32, "manifest_hash": "cd" * 32}],
        "certification": "ef" * 32,
    }
    manifest.update(manifest_extra or {})
    (d / fname).write_text(json.dumps(manifest))
    return str(tmp_path / "published")


@pytest.fixture
def catalog(registry):
    """A registry with the dataset chain the manifests reference."""
    src = registry.register("source", {"name": "vendor-src"})
    ds = registry.register("dataset", {"name": "vendor-prices"},
                           refs={"source": src})
    return registry, src, ds


def test_sync_registers_version_with_lineage(tmp_path, catalog):
    registry, src, ds = catalog
    root = _outbox(tmp_path, "vendor-prices", "vendor-prices@00000001")
    summary = sync_published(registry, root)
    assert len(summary["registered"]) == 1 and not summary["failed"]
    vid = summary["registered"][0]
    rec = registry.get(vid)
    assert rec.kind == "dataset_version"
    assert rec.refs["dataset"] == ds
    assert rec.payload["effective_date"] == "2026-01-02/2026-01-05"
    assert rec.payload["acquisition_date"] == "2026-08-23T12:00:00+00:00"
    # Onboarding-phase lineage: source -> version (ADR-0004).
    edges = Lineage(registry).edges(vid)
    assert [(e["src"], e["phase"]) for e in edges] == [(src, "onboarding")]


def test_rescan_is_free(tmp_path, catalog):
    registry, _src, _ds = catalog
    root = _outbox(tmp_path, "vendor-prices", "v1")
    sync_published(registry, root)
    summary = sync_published(registry, root)
    assert summary["registered"] == [] and summary["existing"] == 1
    assert summary["edges_added"] == 0


def test_point_range_flattens_to_single_date(tmp_path, catalog):
    registry, _src, _ds = catalog
    root = _outbox(tmp_path, "vendor-prices", "v-point",
                   {"effective_range": {"start": "2026-01-02",
                                        "end": "2026-01-02"}})
    vid = sync_published(registry, root)["registered"][0]
    assert registry.get(vid).payload["effective_date"] == "2026-01-02"


def test_uncataloged_dataset_fails_that_file_scan_continues(tmp_path, catalog):
    registry, _src, _ds = catalog
    root = _outbox(tmp_path, "vendor-prices", "good")
    _outbox(tmp_path, "ghost-dataset", "orphan")
    summary = sync_published(registry, root)
    assert len(summary["registered"]) == 1
    assert len(summary["failed"]) == 1
    assert "not cataloged" in summary["failed"][0]["error"]
    assert summary["failed"][0]["file"].startswith("ghost-dataset")


def test_ambiguous_dataset_alias_fails(tmp_path, catalog):
    registry, src, _ds = catalog
    # A second dataset version under the same alias — no unambiguous parent.
    registry.register("dataset", {"name": "vendor-prices",
                                  "description": "competing definition"},
                      refs={"source": src})
    root = _outbox(tmp_path, "vendor-prices", "v1")
    summary = sync_published(registry, root)
    assert summary["registered"] == []
    assert "ambiguous" in summary["failed"][0]["error"]


def test_malformed_manifest_reported_not_fatal(tmp_path, catalog):
    registry, _src, _ds = catalog
    root = _outbox(tmp_path, "vendor-prices", "good")
    bad_dir = tmp_path / "published" / "vendor-prices"
    (bad_dir / "00000002-deadbeef.json").write_text("{not json")
    (bad_dir / "00000003-deadbeef.json").write_text('{"name": "no-dataset"}')
    summary = sync_published(registry, root)
    assert len(summary["registered"]) == 1
    assert len(summary["failed"]) == 2


def test_extra_manifest_keys_tolerated_and_hash_material(tmp_path, catalog):
    registry, _src, _ds = catalog
    root = _outbox(tmp_path, "vendor-prices", "v1",
                   {"producer": "some-future-tool"})
    vid = sync_published(registry, root)["registered"][0]
    # The digest is the manifest's canonical hash — extras included, so
    # the producer's identity computation and ours agree.
    from dskit.assets.base import canonical_hash

    path = next((tmp_path / "published" / "vendor-prices").glob("*.json"))
    assert registry.get(vid).payload["digest"] == canonical_hash(
        json.loads(path.read_text()))


def test_missing_root_refused(registry):
    with pytest.raises(AssetError, match="does not exist"):
        sync_published(registry, "/nonexistent/published")


def test_hidden_and_non_json_files_ignored(tmp_path, catalog):
    registry, _src, _ds = catalog
    root = _outbox(tmp_path, "vendor-prices", "v1")
    d = tmp_path / "published" / "vendor-prices"
    (d / ".tmp-inflight").write_text("staging debris")
    (d / "README.txt").write_text("not a manifest")
    summary = sync_published(registry, root)
    assert len(summary["registered"]) == 1 and not summary["failed"]
