"""acquire.py: the orchestrated pull — evidence chain, cursors, failure paths."""

import os

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import find_active_source, load_state, run_acquisition

from .conftest import norm_path, norm_read, read_jsonl
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


# -- compressed payloads (ADR-0036) -------------------------------------------


def test_gz_source_lands_compressed_and_verifiable(root, registry, gz_source):
    from dskit.onboarding import verify_snapshot

    FakeConnector.script = [
        record("prices", "2026-01-02", {"close": 10.5}),
        record("prices", "2026-01-05", {"close": 11.0}),
        state({"cursor": "2026-01-05"}),
    ]
    s = run_acquisition(root, registry, "gz", "prices", "live")
    assert s["records"] == 2 and s["state_saved"]
    # Bronze landed under the gz spelling, decodes to the two messages,
    # and the manifest's relpath carries the codec (identity material).
    snap_dir = root.snapshot_dir("gz", s["acq_id"])
    raw = os.path.join(snap_dir, "payload", "prices.jsonl.gz")
    assert os.path.isfile(raw)
    assert not os.path.exists(os.path.join(snap_dir, "payload", "prices.jsonl"))
    assert len(read_jsonl(raw)) == 2
    # verify is codec-agnostic: digests are the stored bytes.
    assert verify_snapshot(snap_dir) == []
    # Normalized rows landed compressed too, readable via norm_read.
    assert len(norm_read(root, "gz", s["acq_id"], "prices")) == 2
    assert load_state(root, "gz", "prices", "live") == {"cursor": "2026-01-05"}


def test_gz_determinism_dedupes_via_worm(root, registry, gz_source, monkeypatch):
    # Same records + same stamp => same compressed bytes => same acq_id:
    # the re-pull hits the WORM refusal, which IS the at-least-once
    # dedupe story surviving compression.
    import dskit.onboarding.acquire as acquire_mod

    monkeypatch.setattr(
        acquire_mod, "utc_now", lambda: "2026-08-26T12:00:00+00:00"
    )
    script = [record("prices", "2026-01-02", {"close": 10.5})]
    FakeConnector.script = list(script)
    first = run_acquisition(root, registry, "gz", "prices", "live")
    FakeConnector.script = list(script)
    with pytest.raises(AssetError, match="WORM") as exc:
        run_acquisition(root, registry, "gz", "prices", "live")
    assert first["acq_id"] in str(exc.value)


def test_connector_never_sees_the_storage_block(root, registry, gz_source):
    FakeConnector.script = [record("prices", "2026-01-02")]
    run_acquisition(root, registry, "gz", "prices", "live")
    checks = [c for c in FakeConnector.calls if c[0] == "check"]
    assert checks and all("storage" not in cfg for _, cfg in checks)


def test_gz_error_mid_stream_leaves_no_debris(root, registry, gz_source):
    FakeConnector.script = [
        record("prices", "2026-01-02"),
        {"protocol": 1, "type": "ERROR", "message": "auth expired"},
    ]
    with pytest.raises(AssetError, match="auth expired"):
        run_acquisition(root, registry, "gz", "prices", "live")
    assert os.listdir(root.raw_dir("gz")) == []
    assert not os.path.isdir(os.path.join(root.root, "observations", "gz"))


def test_gz_forecasts_segregate_compressed(root, registry, gz_source):
    FakeConnector.script = [
        record("outlook", "2026-01-01", {"v": 1}),
        record("outlook", "2027-01-01", {"v": 2}, kind="forecast"),
    ]
    s = run_acquisition(root, registry, "gz", "outlook", "live")
    assert s["records"] == 1 and s["forecasts"] == 1
    assert len(norm_read(root, "gz", s["acq_id"], "outlook")) == 1
    fc = norm_read(root, "gz", s["acq_id"], "outlook", forecasts=True)
    assert len(fc) == 1 and fc[0]["kind"] == "forecast"


# -- the commit instant (ADR-0079) --------------------------------------------

T0 = "2026-08-26T12:00:00+00:00"  # the pre-read instant
T1 = "2026-08-26T12:00:01+00:00"  # dated DURING the pull, by the stream's clock
T2 = "2026-08-26T12:00:02+00:00"  # the commit instant
T3 = "2026-08-26T12:00:03+00:00"  # after commit: a genuine future


def _clock(monkeypatch, at):
    """Point ``utc_now`` at a settable clock (a one-cell list) reading ``at``."""
    import dskit.onboarding.acquire as acquire_mod

    clock = [at]
    monkeypatch.setattr(acquire_mod, "utc_now", lambda: clock[0])
    return clock


def _pull(clock, messages, then):
    """A one-shot script: the messages, then the clock moves to ``then`` —
    time passes inside ``read()``, as it does in a live capture."""
    yield from messages
    clock[0] = then


def test_observation_dated_during_the_pull_is_accepted(
    root, registry, fake_source, monkeypatch
):
    # The stamp is the COMMIT instant: a row a capture stream dated one
    # second after the pull began is not "the future".
    clock = _clock(monkeypatch, T0)
    FakeConnector.script = _pull(clock, [record("books", T1, {"bid": 0.4})], T2)
    s = run_acquisition(root, registry, "fake", "books", "live")
    assert s["records"] == 1
    rows = read_jsonl(norm_path(root, "fake", s["acq_id"], "books"))
    assert rows[0]["effective_date"] == T1 and rows[0]["acquired_at"] == T2


def test_observation_after_the_commit_instant_still_refuses(
    root, registry, fake_source, monkeypatch
):
    clock = _clock(monkeypatch, T0)
    FakeConnector.script = _pull(
        clock, [record("books", T1), record("books", T3)], T2
    )
    with pytest.raises(AssetError, match='kind="forecast"') as exc:
        run_acquisition(root, registry, "fake", "books", "live")
    text = str(exc.value)
    assert "message 1" in text and T3 in text and T2 in text
    assert os.listdir(root.raw_dir("fake")) == []
    assert load_state(root, "fake", "books", "live") == {}


def test_committed_rows_manifest_and_evidence_share_one_stamp(
    root, registry, fake_source, monkeypatch
):
    import json

    from dskit.onboarding.base import parse_utc

    clock = _clock(monkeypatch, T0)
    FakeConnector.script = _pull(clock, [
        record("books", "2026-08-25", {"v": 1}),
        record("books", T1, {"v": 2}),
        record("books", T3, {"v": 3}, kind="forecast"),
        state({"cursor": T1}),
    ], T2)
    s = run_acquisition(root, registry, "fake", "books", "live")
    rows = read_jsonl(norm_path(root, "fake", s["acq_id"], "books"))
    fc = read_jsonl(norm_path(root, "fake", s["acq_id"], "books", forecasts=True))
    assert len(rows) == 2 and len(fc) == 1
    assert {r["acquired_at"] for r in rows + fc} == {T2}
    assert all(parse_utc(r["effective_date"]) <= parse_utc(T2) for r in rows)
    with open(os.path.join(root.snapshot_dir("fake", s["acq_id"]),
                           "manifest.json"), encoding="utf-8") as fh:
        assert json.load(fh)["acquired_at"] == T2
    snap = registry.get(s["snapshot"])
    assert snap.payload["acquired_at"] == T2
    # The job and everything named after it carry the stamp via the acq_id.
    assert "20260826T120002Z" in s["acq_id"]
    assert registry.get(s["job"]).payload["name"] == f"fake-{s['acq_id']}"
    assert load_state(root, "fake", "books", "live") == {"cursor": T1}


def test_gz_rows_are_restamped_under_the_codec(
    root, registry, gz_source, monkeypatch
):
    from dskit.onboarding import verify_snapshot

    clock = _clock(monkeypatch, T0)
    FakeConnector.script = _pull(clock, [
        record("books", T1, {"v": 1}),
        record("books", "2026-08-25", {"v": 2}),
    ], T2)
    s = run_acquisition(root, registry, "gz", "books", "live")
    rows = norm_read(root, "gz", s["acq_id"], "books")
    assert [r["effective_date"] for r in rows] == [T1, "2026-08-25"]
    assert {r["acquired_at"] for r in rows} == {T2}
    assert verify_snapshot(root.snapshot_dir("gz", s["acq_id"])) == []


def test_forecasts_ignore_the_commit_instant(
    root, registry, fake_source, monkeypatch
):
    clock = _clock(monkeypatch, T0)
    FakeConnector.script = _pull(
        clock, [record("outlook", T3, {"v": 1}, kind="forecast")], T2
    )
    s = run_acquisition(root, registry, "fake", "outlook", "live")
    assert s["records"] == 0 and s["forecasts"] == 1
    fc = read_jsonl(norm_path(root, "fake", s["acq_id"], "outlook", forecasts=True))
    assert fc[0]["effective_date"] == T3 and fc[0]["acquired_at"] == T2


@pytest.mark.parametrize("source", ["fake", "gz"])
def test_restamp_is_byte_for_byte_a_single_pass_write(
    root, registry, request, monkeypatch, source
):
    # The staged rows are rewritten with the commit instant: every other
    # byte — sort_keys order, float repr, escapes, big ints — must survive
    # the round trip, the provisional stamp must be nowhere on disk, and
    # the rewrite must leave no temp file behind. Both codecs.
    import gzip
    import json

    from dskit.onboarding.acquire import _PENDING_STAMP
    from dskit.onboarding.codec import iter_text_lines, resolve_stream_file

    request.getfixturevalue(f"{source}_source")
    data = {"z": 1e16, "a": [1.0, -0.0, 2**70, 'é "\\', None, True],
            "n": {"y": {"k": 3.14159}}, "1": "s"}
    clock = _clock(monkeypatch, T0)
    FakeConnector.script = _pull(clock, [
        record("books", T1, data),
        record("books", "2026-08-25", data, kind="forecast"),
    ], T2)
    s = run_acquisition(root, registry, source, "books", "live")
    for forecasts, kind, eff in ((False, "observation", T1),
                                 (True, "forecast", "2026-08-25")):
        path = resolve_stream_file(
            root.records_dir(source, s["acq_id"], forecasts=forecasts), "books"
        )
        want = json.dumps({"stream": "books", "mode": "live", "kind": kind,
                           "effective_date": eff, "acquired_at": T2,
                           "data": data}, sort_keys=True) + "\n"
        assert list(iter_text_lines(path)) == [want]
    for dirpath, _, names in os.walk(root.root):
        for name in names:
            assert not name.endswith(".restamp")
            full = os.path.join(dirpath, name)
            opener = gzip.open if name.endswith(".gz") else open
            with opener(full, "rb") as fh:
                assert _PENDING_STAMP.encode() not in fh.read(), full


def test_empty_pull_shape_survives_the_commit_stamp(
    root, registry, fake_source, monkeypatch
):
    clock = _clock(monkeypatch, T0)
    FakeConnector.script = _pull(
        clock, [{"protocol": 1, "type": "LOG", "message": "nothing new"}], T2
    )
    s = run_acquisition(root, registry, "fake", "books", "live")
    assert s == {"job": None, "snapshot": None, "acq_id": None,
                 "records": 0, "forecasts": 0, "skipped": 0,
                 "logs": ["nothing new"], "state_saved": False}
    assert os.listdir(root.raw_dir("fake")) == []
