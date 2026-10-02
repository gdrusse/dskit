"""libs/localblobs.py: local files as hashed binary artifacts (ADR-0225) — the
conformance shape of test_localfiles.py / test_localtables.py, plus the FILE
inventory, cursor and refusal rules this pack adds, and one run through the
real acquisition job."""

import hashlib
import json
import os

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import (
    check_config,
    check_message,
    read_manifest,
    resolve_connector,
    run_acquisition,
    verify_snapshot,
)
from dskit.onboarding.codec import resolve_stream_file
from dskit.onboarding.libs.localblobs import (
    DEFAULT_STREAM,
    RECORD_FIELDS,
    LocalBlobsConnector,
)
from dskit.onboarding.state import load_state

from .conftest import norm_read, read_jsonl

AS_OF = "2026-01-01T00:00:00+00:00"

#: Binary on purpose (NUL, 0xff): these are artifacts, not text.
FILES = {
    "qqq/options_2012.parquet": b"PAR1\x00\xff qqq 2012 PAR1",
    "qqq/options_2013.parquet": bytes(range(256)) * 4,
    "spy/options_2012.parquet": b"PAR1 spy 2012",
    "underlying_prices.parquet": b"PAR1 prices",
    "sidecar/meta.json": b'{"vendor": "x"}',
    "README.txt": b"hello\n",
}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write(directory, files):
    for rel, data in files.items():
        path = directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _read(conn, config, state=None, mode="backfill", streams=None):
    msgs = list(conn.read(config, streams or [DEFAULT_STREAM], state or {}, mode))
    for m in msgs:
        assert check_message(m) is not None  # every message envelope-valid
    return msgs


def _of(msgs, mtype):
    return [m for m in msgs if m["type"] == mtype]


@pytest.fixture
def conn():
    return LocalBlobsConnector()


@pytest.fixture
def archive(tmp_path):
    """A per-symbol archive: nested parquet shards, a sidecar and a readme."""
    d = tmp_path / "archive"
    _write(d, FILES)
    return d


@pytest.fixture
def config(archive):
    return {"path": str(archive), "as_of": AS_OF}


# -- the four verbs -----------------------------------------------------------


def test_registered_kind_resolves_to_the_pack():
    assert resolve_connector("localblobs") is LocalBlobsConnector


def test_spec_passes_its_own_gate(conn, config):
    check_config(conn, config)
    check_config(conn, {**config, "stream": "chains", "include": ["*"], "exclude": ["x"]})
    with pytest.raises(AssetError, match="unknown key"):
        check_config(conn, {**config, "surprise": 1})
    with pytest.raises(AssetError, match="required knob") as exc:
        check_config(conn, {})
    assert "as_of" in str(exc.value) and "path" in str(exc.value)


def test_check_accepts_a_good_config_and_refuses_a_missing_directory(conn, tmp_path, config):
    conn.check(config)
    with pytest.raises(AssetError, match="config.path must be an existing directory"):
        conn.check({**config, "path": str(tmp_path / "nope")})


def test_check_refuses_a_selection_that_matches_no_file(conn, tmp_path, config):
    with pytest.raises(AssetError, match="matched no file") as exc:
        conn.check({**config, "include": ["*.nothing"]})
    assert "*.nothing" in str(exc.value)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(AssetError, match="matched no file"):
        conn.check({**config, "path": str(empty)})


def test_discover_offers_the_one_stream_and_its_inventory_schema(conn, config):
    assert conn.discover(config) == [{
        "stream": "files",
        "schema": {"fields": list(RECORD_FIELDS)},
        "primary_key": ["relpath"],
    }]
    assert RECORD_FIELDS == ("relpath", "size", "sha256")
    [chains] = conn.discover({**config, "stream": "chains"})
    assert chains["stream"] == "chains"


def test_read_emits_file_then_record_per_file_in_relpath_order_then_state(conn, archive, config):
    msgs = _read(conn, config)
    assert [m["type"] for m in msgs] == ["FILE", "RECORD"] * len(FILES) + ["STATE"]
    files, records = _of(msgs, "FILE"), _of(msgs, "RECORD")
    assert [m["relpath"] for m in files] == sorted(FILES)
    for fmsg, rec in zip(files, records):
        rel = fmsg["relpath"]
        assert fmsg["stream"] == rec["stream"] == "files"
        assert os.path.isabs(fmsg["path"])
        with open(fmsg["path"], "rb") as fh:
            assert fh.read() == FILES[rel]
        assert rec["data"] == {"relpath": rel, "size": len(FILES[rel]), "sha256": _sha(FILES[rel])}
        assert rec["effective_date"] == AS_OF
    # The machine path rides on FILE only — never in an inventory row.
    assert str(archive) not in json.dumps(records)


@pytest.mark.parametrize("as_of, expected", [
    ("2026-01-01", "2026-01-01T00:00:00+00:00"),
    ("2026-01-01T05:00:00+05:00", "2026-01-01T00:00:00+00:00"),
])
def test_as_of_is_declared_and_normalised_to_utc(conn, config, as_of, expected):
    records = _of(_read(conn, {**config, "as_of": as_of}), "RECORD")
    assert {r["effective_date"] for r in records} == {expected}


def test_nested_relpaths_keep_their_directories(conn, config):
    relpaths = [m["relpath"] for m in _of(_read(conn, config), "FILE")]
    assert "qqq/options_2012.parquet" in relpaths and "sidecar/meta.json" in relpaths
    assert "underlying_prices.parquet" in relpaths  # top-level beside nested


def test_a_second_pull_under_the_same_state_is_nothing_new(conn, config):
    first = _read(conn, config)
    state = json.loads(json.dumps(first[-1]["state"]))  # the cursor as saved on disk
    again = _read(conn, config, state)
    assert [m["type"] for m in again] == ["LOG", "STATE"]
    assert "nothing new" in again[0]["message"]
    assert again[-1]["state"] == state


def test_the_default_include_spelled_out_is_the_same_declaration(conn, config):
    state = _read(conn, config)[-1]["state"]
    again = _read(conn, {**config, "include": ["*"]}, state)
    assert [m["type"] for m in again] == ["LOG", "STATE"]


@pytest.mark.parametrize("change", ["edit", "add", "remove"])
def test_a_changed_listing_re_emits_every_file(conn, archive, config, change):
    state = _read(conn, config)[-1]["state"]
    if change == "edit":  # same size, different bytes: only the digest sees it
        (archive / "README.txt").write_bytes(b"jello\n")
    elif change == "add":
        (archive / "extra.bin").write_bytes(b"\x00\x01")
    else:
        (archive / "README.txt").unlink()
    again = _read(conn, config, state)
    assert [m["type"] for m in again if m["type"] != "STATE"] == (
        ["FILE", "RECORD"] * len(_of(again, "FILE"))
    )
    expected = len(FILES) + {"edit": 0, "add": 1, "remove": -1}[change]
    assert len(_of(again, "FILE")) == expected  # a complete inventory, not a delta
    assert again[-1]["state"]["fingerprint"] != state["fingerprint"]


@pytest.mark.parametrize("override", [
    {"as_of": "2026-02-01T00:00:00+00:00"},
    {"exclude": ["README.txt"]},
    {"include": ["*.parquet"]},
])
def test_a_changed_declaration_re_emits_even_over_the_same_bytes(conn, config, override):
    state = _read(conn, config)[-1]["state"]
    again = _read(conn, {**config, **override}, state)
    assert _of(again, "FILE") and not _of(again, "LOG")


def test_an_empty_selection_refuses_and_emits_no_state(conn, config):
    msgs = []
    with pytest.raises(AssetError, match="matched no file") as exc:
        for msg in conn.read({**config, "include": ["*.nothing"]}, ["files"], {}, "backfill"):
            msgs.append(msg)
    assert "refusing to cursor past an empty selection" in str(exc.value)
    assert msgs == []  # no STATE, so the cursor stays where it was


def test_include_and_exclude_globs_select_over_the_posix_relpath(conn, archive, config):
    (archive / "qqq" / "deep").mkdir()
    (archive / "qqq" / "deep" / "x.parquet").write_bytes(b"deep")

    def selected(**knobs):
        return [m["relpath"] for m in _of(_read(conn, {**config, **knobs}), "FILE")]

    # fnmatch: '*' crosses '/', so "*/*.parquet" reaches the nested shard too,
    # but a top-level file has no '/' to match.
    assert selected(include=["*/*.parquet"]) == [
        "qqq/deep/x.parquet", "qqq/options_2012.parquet", "qqq/options_2013.parquet",
        "spy/options_2012.parquet",
    ]
    assert selected(include=["qqq/*"], exclude=["*/deep/*", "*2013*"]) == [
        "qqq/options_2012.parquet"]
    assert selected(include=["*.json", "*.txt"]) == ["README.txt", "sidecar/meta.json"]
    assert "README.txt" not in selected(exclude=["README.txt"])


def test_bad_knobs_are_listed_together(conn, tmp_path):
    bad = {"path": str(tmp_path / "nope"), "stream": "Bad Stream", "as_of": "yesterday",
           "include": "*.parquet", "exclude": [""]}
    for verb in (conn.resolve_knobs, conn.check, conn.discover):
        with pytest.raises(AssetError) as exc:
            verb(bad)
        text = str(exc.value)
        for knob in ("config.path", "config.stream", "config.as_of",
                     "config.include", "config.exclude"):
            assert knob in text
    with pytest.raises(AssetError, match="config.path"):
        list(conn.read(bad, ["files"], {}, "backfill"))


@pytest.mark.parametrize("override, match", [
    ({"as_of": None}, "config.as_of must be an ISO-8601 instant"),
    ({"as_of": "2999-01-01"}, "in the future"),
    ({"stream": ".."}, "config.stream must be filesystem-safe"),
    ({"stream": "a/b"}, "config.stream must be filesystem-safe"),
    ({"include": []}, "config.include must be a non-empty list"),
    ({"exclude": "README.txt"}, "config.exclude must be a non-empty list"),
    ({"path": ""}, "config.path"),
    ({"path": None}, "config.path"),
])
def test_each_unusable_knob_is_named(conn, config, override, match):
    with pytest.raises(AssetError, match=match):
        conn.resolve_knobs({**config, **override})


def test_path_is_expanded_so_a_home_relative_spelling_works(conn, archive, config, monkeypatch):
    monkeypatch.setenv("HOME", str(archive.parent))
    assert conn.resolve_knobs({**config, "path": "~/archive"})["path"] == str(archive)


def test_an_unknown_or_empty_stream_request_refuses(conn, config):
    with pytest.raises(AssetError, match="unknown stream 'ghost'"):
        list(conn.read(config, ["ghost"], {}, "backfill"))
    with pytest.raises(AssetError, match="non-empty list"):
        list(conn.read(config, [], {}, "backfill"))
    with pytest.raises(AssetError, match="unknown stream 'files' — this source offers 'chains' only"):
        list(conn.read({**config, "stream": "chains"}, ["files"], {}, "backfill"))


def test_a_configured_stream_names_the_messages(conn, config):
    msgs = _read(conn, {**config, "stream": "chains"}, streams=["chains"])
    assert {m["stream"] for m in msgs if "stream" in m} == {"chains"}


def test_the_pull_is_identical_in_both_modes(conn, config):
    assert _read(conn, config, mode="backfill") == _read(conn, config, mode="live")


def test_a_name_the_envelope_cannot_carry_refuses_before_any_file_moves(conn, archive, config):
    (archive / "a:b.parquet").write_bytes(b"x")
    (archive / "back\\slash.parquet").write_bytes(b"y")
    for verb in (conn.check, lambda c: list(conn.read(c, ["files"], {}, "backfill"))):
        with pytest.raises(AssetError) as exc:
            verb(config)
        assert "a:b.parquet" in str(exc.value) and "back\\\\slash.parquet" in str(exc.value)
    # The same names, excluded by glob, are simply not part of the selection.
    msgs = _read(conn, {**config, "exclude": ["a:*", "back*"]})
    assert len(_of(msgs, "FILE")) == len(FILES)


def test_a_name_that_is_not_valid_utf8_refuses(conn, archive, config):
    name = os.fsdecode(b"bad\xff.parquet")
    try:
        (archive / name).write_bytes(b"x")
    except (OSError, UnicodeError):
        pytest.skip("this filesystem refuses non-UTF-8 names")
    with pytest.raises(AssetError, match="not valid UTF-8"):
        list(conn.read(config, ["files"], {}, "backfill"))


def test_a_file_that_changes_mid_pull_refuses_rather_than_committing_a_stale_digest(
    conn, archive, config
):
    gen = conn.read(config, ["files"], {}, "backfill")
    first = next(gen)
    assert first["type"] == "FILE"
    with open(first["path"], "ab") as fh:  # a writer touches it after the digest
        fh.write(b"!")
    with pytest.raises(AssetError, match="changed while it was being acquired"):
        next(gen)


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="permissions do not bind root",
)
def test_an_unreadable_directory_refuses_instead_of_shrinking_the_inventory(
    conn, archive, config
):
    locked = archive / "spy"
    locked.chmod(0)
    try:
        with pytest.raises(AssetError, match="cannot list"):
            list(conn.read(config, ["files"], {}, "backfill"))
        with pytest.raises(AssetError, match="cannot list"):
            conn.check(config)
    finally:
        locked.chmod(0o755)


def test_a_symlinked_file_is_followed_and_a_dangling_link_is_skipped(conn, archive, config):
    try:
        os.symlink(archive / "README.txt", archive / "alias.txt")
        os.symlink(archive / "gone.txt", archive / "ghost.txt")
    except OSError:
        pytest.skip("this platform cannot create symlinks")
    records = {r["data"]["relpath"]: r["data"] for r in _of(_read(conn, config), "RECORD")}
    assert records["alias.txt"]["sha256"] == records["README.txt"]["sha256"]
    assert "ghost.txt" not in records


# -- through the real acquisition job --------------------------------------------


@pytest.fixture
def blob_source(registry, config):
    """An ACTIVE source_config named 'blobs' for the registered localblobs kind."""
    vid = registry.register("source_config", {
        "name": "blobs",
        "catalog_source": "blobs-src",
        "connector": "localblobs",
        "config": dict(config),
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    return vid


def test_acquisition_lays_out_payload_and_manifest_and_keeps_the_machine_path_out_of_bronze(
    root, registry, archive, blob_source
):
    s = run_acquisition(root, registry, "blobs", "files", "backfill")
    assert s["files"] == s["records"] == len(FILES) and s["state_saved"]
    assert s["snapshot"] is not None
    snap = root.snapshot_dir("blobs", s["acq_id"])

    # payload/<stream>/<relpath>, byte for byte
    for rel, data in FILES.items():
        with open(os.path.join(snap, "payload", "files", *rel.split("/")), "rb") as fh:
            assert fh.read() == data

    # the manifest chains every digest, and the snapshot re-hashes clean
    manifest = {f["relpath"]: f for f in read_manifest(snap)["files"]}
    for rel, data in FILES.items():
        assert manifest[f"files/{rel}"]["sha256"] == _sha(data)
        assert manifest[f"files/{rel}"]["size"] == len(data)
    assert verify_snapshot(snap) == []

    # bronze holds the RECORD inventory — and not the machine path
    bronze_file = resolve_stream_file(os.path.join(snap, "payload"), "files")
    bronze = read_jsonl(bronze_file)
    assert sorted(m["data"]["relpath"] for m in bronze) == sorted(FILES)
    for msg in bronze:
        assert msg["type"] == "RECORD" and msg["effective_date"] == AS_OF
        assert manifest[f"files/{msg['data']['relpath']}"]["sha256"] == msg["data"]["sha256"]
    for text in (open(bronze_file, encoding="utf-8").read(),
                 open(os.path.join(snap, "manifest.json"), encoding="utf-8").read()):
        assert str(archive) not in text
    rows = norm_read(root, "blobs", s["acq_id"], "files")
    assert sorted(r["data"]["relpath"] for r in rows) == sorted(FILES)
    assert {r["effective_date"] for r in rows} == {AS_OF}

    cursor = load_state(root, "blobs", "files", "backfill")
    assert set(cursor) == {"fingerprint", "as_of", "stream", "include", "exclude"}


def test_a_second_acquisition_commits_nothing_and_a_change_lands_a_second_snapshot(
    root, registry, archive, blob_source
):
    first = run_acquisition(root, registry, "blobs", "files", "backfill")
    again = run_acquisition(root, registry, "blobs", "files", "backfill")
    assert again["snapshot"] is None and again["files"] == 0 and again["state_saved"]
    assert os.listdir(root.raw_dir("blobs")) == [first["acq_id"]]

    (archive / "qqq" / "options_2012.parquet").write_bytes(b"PAR1 revised")
    third = run_acquisition(root, registry, "blobs", "files", "backfill")
    assert third["files"] == len(FILES) and third["snapshot"] is not None  # a complete inventory
    assert sorted(os.listdir(root.raw_dir("blobs"))) == sorted([first["acq_id"], third["acq_id"]])
    for acq_id in (first["acq_id"], third["acq_id"]):
        assert verify_snapshot(root.snapshot_dir("blobs", acq_id)) == []
