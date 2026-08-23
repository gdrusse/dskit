"""acquire.py: the orchestrated pull — evidence chain, cursors, failure paths."""

import os

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import find_active_source, load_state, run_acquisition

from .conftest import norm_path, read_jsonl
from .fake_connector import FakeConnector, record, state


def test_happy_path_builds_the_whole_chain(root, registry, fake_source):
    FakeConnector.script = [
        record("prices", "2026-01-02", {"close": 10.5}),
        record("prices", "2026-01-05", {"close": 11.0}),
        state({"cursor": "2026-01-05"}),
    ]
    s = run_acquisition(root, registry, "fake", "prices", "live")

    assert s["records"] == 2 and s["forecasts"] == 0 and s["state_saved"]
    # Evidence chain: snapshot -> job -> source_config, mode stamped through.
    snap = registry.get(s["snapshot"])
    job = registry.get(snap.refs["job"])
    assert job.refs["source_config"] == fake_source
    assert snap.payload["mode"] == job.payload["mode"] == "live"
    assert snap.payload["effective_start"] == "2026-01-02"
    assert snap.payload["effective_end"] == "2026-01-05"
    # Normalized rows carry the bitemporal pair (ADR-0014).
    rows = read_jsonl(norm_path(root, "fake", s["acq_id"], "prices"))
    assert [r["effective_date"] for r in rows] == ["2026-01-02", "2026-01-05"]
    assert all(r["acquired_at"] == snap.payload["acquired_at"] for r in rows)
    # Raw bronze holds the messages as received.
    raw = os.path.join(root.snapshot_dir("fake", s["acq_id"]),
                       "payload", "prices.jsonl")
    assert len(read_jsonl(raw)) == 2
    # The cursor persisted only because everything above is durable.
    assert load_state(root, "fake", "prices", "live") == {"cursor": "2026-01-05"}


def test_declared_forecasts_segregated(root, registry, fake_source):
    FakeConnector.script = [
        record("outlook", "2026-01-01", {"v": 1}),
        record("outlook", "2027-01-01", {"v": 2}, kind="forecast"),
    ]
    s = run_acquisition(root, registry, "fake", "outlook", "live")
    assert s["records"] == 1 and s["forecasts"] == 1
    assert len(read_jsonl(norm_path(root, "fake", s["acq_id"], "outlook"))) == 1
    fc = read_jsonl(norm_path(root, "fake", s["acq_id"], "outlook", forecasts=True))
    assert len(fc) == 1 and fc[0]["kind"] == "forecast"


def test_future_observation_refused_loudly(root, registry, fake_source):
    # ADR-0014's assertion: an observation about the future is a lie or
    # an undeclared forecast — either way, not silently saved.
    FakeConnector.script = [record("prices", "2999-01-01")]
    with pytest.raises(AssetError, match='kind="forecast"'):
        run_acquisition(root, registry, "fake", "prices", "live")
    # Nothing committed, nothing checkpointed.
    assert os.listdir(root.raw_dir("fake")) == []
    assert load_state(root, "fake", "prices", "live") == {}


def test_error_message_aborts_leaving_no_debris(root, registry, fake_source):
    FakeConnector.script = [
        record("prices", "2026-01-02"),
        {"protocol": 1, "type": "ERROR", "message": "auth expired"},
    ]
    with pytest.raises(AssetError, match="auth expired"):
        run_acquisition(root, registry, "fake", "prices", "live")
    assert os.listdir(root.raw_dir("fake")) == []
    assert not os.path.isdir(os.path.join(root.root, "observations", "fake"))


def test_unknown_types_skipped_logs_collected(root, registry, fake_source):
    FakeConnector.script = [
        {"protocol": 1, "type": "FUTURE_THING", "whatever": 1},
        {"protocol": 1, "type": "LOG", "message": "hello"},
        record("prices", "2026-01-02"),
    ]
    s = run_acquisition(root, registry, "fake", "prices", "live")
    assert s["skipped"] == 1 and s["logs"] == ["hello"]


def test_empty_pull_writes_no_snapshot_but_honors_state(root, registry, fake_source):
    FakeConnector.script = [state({"cursor": "2026-01-05"})]
    s = run_acquisition(root, registry, "fake", "prices", "live")
    assert s["snapshot"] is None and s["records"] == 0 and s["state_saved"]
    assert load_state(root, "fake", "prices", "live") == {"cursor": "2026-01-05"}
    assert os.listdir(root.raw_dir("fake")) == []


def test_connector_receives_the_mode_keyed_state(root, registry, fake_source):
    FakeConnector.script = [record("prices", "2026-01-02"),
                            state({"cursor": "2026-01-02"})]
    run_acquisition(root, registry, "fake", "prices", "live")
    FakeConnector.script = [record("prices", "2001-06-01")]
    run_acquisition(root, registry, "fake", "prices", "backfill")
    reads = [c for c in FakeConnector.calls if c[0] == "read"]
    # live's cursor was persisted; backfill started from ITS OWN empty state.
    assert reads[0][2] == {} and reads[0][3] == "live"
    assert reads[1][2] == {} and reads[1][3] == "backfill"


def test_find_active_source_requires_exactly_one(registry, fake_source):
    assert find_active_source(registry, "fake") == fake_source
    with pytest.raises(AssetError, match="no ACTIVE"):
        find_active_source(registry, "ghost")
    # A second active config with the same alias is ambiguous.
    vid = registry.register("source_config", {
        "name": "fake", "catalog_source": "x",
        "connector": "tests.onboarding.fake_connector:FakeConnector",
        "config": {"flavor": "other"},
    })
    registry.transition(vid, "active")
    with pytest.raises(AssetError, match="active source_config"):
        find_active_source(registry, "fake")


def test_malformed_message_names_connector_and_index(root, registry, fake_source):
    FakeConnector.script = [{"protocol": 1, "type": "RECORD"}]
    with pytest.raises(AssetError, match="message 0"):
        run_acquisition(root, registry, "fake", "prices", "live")
