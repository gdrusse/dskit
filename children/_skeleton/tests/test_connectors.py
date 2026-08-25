"""SampleConnector through the four-verb contract, then one acquisition
end-to-end: the shipped config drives a real pull into a fresh
OnboardingRoot, and the shipped suite validates the snapshot.

This file is the template a real child copies: swap the connector, keep
the shape — spec gate, fail-fast check, discover, cursor-honest read,
and ONE e2e proving the configs and the code agree.
"""

import json
import os

import pytest

from dskit.onboarding import (
    AssetError,
    OnboardingRoot,
    check_config,
    check_message,
    load_suite,
    run_acquisition,
    run_suite,
)

from yourproject.connectors import SampleConnector

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")


def _config():
    """The shipped config object — the file is the single source of truth."""
    with open(os.path.join(CONFIGS, "source-sample.json"), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def conn():
    return SampleConnector()


def _read(conn, config, streams, state=None, mode="live"):
    msgs = list(conn.read(config, streams, state or {}, mode))
    for m in msgs:
        assert check_message(m) is not None  # every message envelope-valid
    return msgs


def test_spec_passes_its_own_gate(conn):
    check_config(conn, _config())
    with pytest.raises(AssetError, match="unknown key"):
        check_config(conn, {**_config(), "surprise": 1})
    with pytest.raises(AssetError, match="required knob"):
        check_config(conn, {"start_date": "2026-01-01"})


def test_check_fails_fast_on_bad_knobs(conn):
    conn.check(_config())  # the shipped knobs — fine
    with pytest.raises(AssetError, match="rows"):
        conn.check({"rows": 0})
    with pytest.raises(AssetError, match="not an ISO"):
        conn.check({"rows": 2, "start_date": "not-a-date"})


def test_discover_names_the_stream(conn):
    (stream,) = conn.discover(_config())
    assert stream["stream"] == "samples"
    assert stream["schema"] == {"fields": ["day", "id", "value"]}
    assert stream["primary_key"] == ["id"]


def test_read_emits_schema_records_then_state(conn):
    msgs = _read(conn, _config(), ["samples"])
    assert [m["type"] for m in msgs] == \
        ["SCHEMA", "RECORD", "RECORD", "RECORD", "STATE"]
    effs = [m["effective_date"] for m in msgs if m["type"] == "RECORD"]
    assert effs == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert msgs[-1]["state"] == {"samples": {"cursor": "2026-01-03"}}


def test_cursor_filters_already_durable_rows(conn):
    state = {"samples": {"cursor": "2026-01-02"}}
    records = [m for m in _read(conn, _config(), ["samples"], state)
               if m["type"] == "RECORD"]
    assert [m["effective_date"] for m in records] == ["2026-01-03"]


def test_unknown_stream_named(conn):
    with pytest.raises(AssetError, match="unknown stream"):
        list(conn.read(_config(), ["ghost"], {}, "live"))


def test_acquisition_and_suite_end_to_end(tmp_path):
    """The whole seam: source registered + activated, one pull, the
    shipped suite over the snapshot — and it PASSES."""
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": "sample",
        "catalog_source": "sample-src",
        "connector": "yourproject.connectors:SampleConnector",
        "config": _config(),
    }, origin="test")
    registry.transition(vid, "active", origin="test")

    out = run_acquisition(root, registry, "sample", "samples", "backfill")
    assert out["records"] == 3
    assert out["state_saved"]  # the cursor persisted AFTER the snapshot

    suite = load_suite(os.path.join(CONFIGS, "suite-sample.json"))
    verdict = run_suite(root, registry, suite, out["snapshot"])
    assert verdict["gating"] == "pass", verdict["statistics"]

    # A second pull is caught up: the cursor makes it an empty, honest no-op.
    again = run_acquisition(root, registry, "sample", "samples", "backfill")
    assert again["snapshot"] is None and again["records"] == 0
