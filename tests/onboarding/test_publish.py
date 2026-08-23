"""publish.py: outbox manifests, the certified-only gate, idempotency."""

import json
import os

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import (
    Rule,
    ValidationSuite,
    certify,
    publish_version,
    run_acquisition,
    run_suite,
)

from .fake_connector import FakeConnector, record


@pytest.fixture
def certified(root, registry, fake_source):
    """A full chain up to a certified certification."""
    FakeConnector.script = [record("prices", "2026-01-02", {"close": 10.5}),
                            record("prices", "2026-01-05", {"close": 11.0})]
    snap = run_acquisition(root, registry, "fake", "prices", "live")["snapshot"]
    result = run_suite(root, registry, ValidationSuite(
        name="s", rules=(Rule(id="r", target="prices", rule="bitemporal"),)),
        snap)["result"]
    return snap, certify(registry, result, "certified", certified_by="gibson")


def test_manifest_is_pointers_never_data(root, registry, certified):
    snap_vid, cert_vid = certified
    out = publish_version(root, registry, "vendor-prices", cert_vid)
    with open(out["manifest_path"], encoding="utf-8") as fh:
        manifest = json.load(fh)
    snap = registry.get(snap_vid)
    assert manifest["dataset"] == "vendor-prices"
    assert manifest["name"] == "vendor-prices@00000001"
    assert manifest["mode"] == "live"
    assert manifest["effective_range"] == {"start": "2026-01-02",
                                           "end": "2026-01-05"}
    assert manifest["snapshots"] == [{
        "snapshot": snap_vid, "manifest_hash": snap.payload["manifest_hash"]}]
    assert manifest["certification"] == cert_vid
    # Filename: sequence + hash8 of the manifest's canonical content.
    assert os.path.basename(out["manifest_path"]) == (
        f"00000001-{out['version_manifest_hash'][:8]}.json")


def test_publication_evidence_registered(root, registry, certified):
    _snap, cert_vid = certified
    out = publish_version(root, registry, "vendor-prices", cert_vid)
    rec = registry.get(out["published_version"])
    assert rec.kind == "published_version"
    assert rec.refs["certification"] == cert_vid
    assert rec.payload["version_manifest_hash"] == out["version_manifest_hash"]


def test_republish_same_certification_reuses(root, registry, certified):
    _snap, cert_vid = certified
    a = publish_version(root, registry, "vendor-prices", cert_vid)
    b = publish_version(root, registry, "vendor-prices", cert_vid)
    assert b["reused"] and not a["reused"]
    assert a["manifest_path"] == b["manifest_path"]
    assert a["published_version"] == b["published_version"]
    outbox = os.listdir(root.published_dir("vendor-prices"))
    assert len([f for f in outbox if f.endswith(".json")]) == 1


def test_refused_certification_cannot_publish(root, registry, fake_source):
    FakeConnector.script = [record("prices", "2026-01-02", {"close": None})]
    snap = run_acquisition(root, registry, "fake", "prices", "live")["snapshot"]
    result = run_suite(root, registry, ValidationSuite(
        name="s", rules=(Rule(id="r", target="prices", rule="not_null",
                              kwargs={"field": "close"}),)), snap)["result"]
    refusal = certify(registry, result, "refused")
    with pytest.raises(AssetError, match="only a certified decision"):
        publish_version(root, registry, "vendor-prices", refusal)


def test_wrong_kind_refused(root, registry, certified):
    snap_vid, _cert = certified
    with pytest.raises(AssetError, match="not a certification"):
        publish_version(root, registry, "vendor-prices", snap_vid)


def test_custom_name_used(root, registry, certified):
    _snap, cert_vid = certified
    out = publish_version(root, registry, "vendor-prices", cert_vid,
                          name="prices-2026w01")
    with open(out["manifest_path"], encoding="utf-8") as fh:
        assert json.load(fh)["name"] == "prices-2026w01"
