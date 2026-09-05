"""`release.py` — what gets armed: a content-and-runtime-bound release.

D24's claim is that arming binds a *release*, not a document: every input
that can change a decision — the document identity, the run and serving
hashes, every artifact digest and timestamp, every resolved class and its
code, the adapter, the derived `FeedSpec`, the source config, the expected
`ExecutionScope`, the approval and lease verifier fingerprints, the
readiness checklist and the interpreter/distribution inventory — is a
field of `ReleaseManifest`, and `release_hash` moves when any of them
does.  The completeness half of that is a parametrised mutation over
`dataclasses.fields`, which fails the day a field is added without being
bound into `to_obj`.

The other half is that a release cannot be quietly re-earned: artifacts
are re-verified from bytes rather than mtimes, a missing or future-dated
timestamp refuses, an expired one refuses with `artifact_expired`, and a
changed distribution inventory refuses.  `ReleaseReader` is the only
capability a `release_read` node receives, so its public surface is
asserted to be exactly `get` and `names` — no path, no handle, no write
verb.

Expected hashes here are computed with `hashlib`/`json.dumps` rather than
by calling the module under test (CLAUDE.md, "deliberate independent
restatement").
"""

import dataclasses
import hashlib
import inspect
import json
import os
import socket
import sys
import sysconfig
from importlib import metadata
from platform import python_implementation, python_version

import pytest

from dskit.pipeline.node import class_ref
from dskit.production.base import ProductionError, canonical_hash
from dskit.production.records import ExecutionScope
from dskit.production.document import ServeDocument
from dskit.production.release import (
    Distribution,
    ReleaseManifest,
    ReleaseReader,
    RuntimeFingerprint,
    artifact_digest,
    fingerprint_class,
    parse_iso_duration,
    verify_release,
    write_release,
)
from tests.production.test_document import example_document, set_path

# --------------------------------------------------------------------------
# constants and fixtures
# --------------------------------------------------------------------------

SERIES_ID = "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1"
ARTIFACT_MS = 1_767_000_000_000
NOW_MS = ARTIFACT_MS + 3_600_000
MAX_AGE_MS = 30 * 86_400_000
SOURCE_HASH = "5b" * 32

#: §5.3.1 / D24, in the order the manifest declares them.
MANIFEST_FIELDS = (
    "series_id",
    "doc_hash",
    "run_hash",
    "serving_hash",
    "artifacts",
    "classes",
    "adapter",
    "feed_spec",
    "source_config",
    "execution_scope",
    "approval_fingerprint",
    "lease_fingerprint",
    "checklist_digest",
    "runtime_fingerprint",
    "created_ms",
)

#: §5.2's eight `FeedSpec` fields, bound into the release.
FEED_SPEC_FIELDS = (
    "source_binding",
    "entity_key_fields",
    "event_time_field",
    "digest_recipe",
    "required_keys",
    "required_keys_digest",
    "source_config_hash",
    "source_config_version",
)

#: D24's runtime inventory.
RUNTIME_FIELDS = (
    "python_implementation",
    "python_version",
    "cache_tag",
    "abi",
    "platform",
    "libc",
    "distributions",
    "project_digests",
    "image_digest",
)


def feed_spec():
    """The release-bound `FeedSpec`, as canonical JSON."""
    return {
        "source_binding": {"source": "bars-1m", "connector": "yourproject:Bars"},
        "entity_key_fields": ["symbol"],
        "event_time_field": "ts_ms",
        "digest_recipe": "sha256/canonical-rows",
        "required_keys": ["AAPL", "MSFT"],
        "required_keys_digest": "7a" * 32,
        "source_config_hash": SOURCE_HASH,
        "source_config_version": "3",
    }


def artifact_root(tmp_path):
    """Two file artifacts and one directory artifact on disk."""
    root = tmp_path / "artifacts"
    (root / "table" / "part").mkdir(parents=True)
    (root / "model").write_bytes(b"weights-v1")
    (root / "params.json").write_text('{"lookback": 30}', encoding="utf-8")
    (root / "table" / "part" / "0.bin").write_bytes(b"rows")
    return root


def manifest(root, **overrides):
    """A complete `ReleaseManifest` over the artifacts in `root`."""
    fields = {
        "series_id": SERIES_ID,
        "doc_hash": "c1" * 32,
        "run_hash": "d2" * 32,
        "serving_hash": "e3" * 32,
        "artifacts": {
            "model": {
                "digest": artifact_digest(root / "model"),
                "timestamp_ms": ARTIFACT_MS,
            },
            "params.json": {
                "digest": artifact_digest(root / "params.json"),
                "timestamp_ms": ARTIFACT_MS,
            },
        },
        "classes": {
            "bars": {"ref": "yourproject.nodes:Bars", "code_digest": "f4" * 32},
            "select": {"ref": "yourproject.nodes:Select", "code_digest": "a5" * 32},
        },
        "adapter": {"name": "yourproject", "digest": "b6" * 32},
        "feed_spec": feed_spec(),
        "source_config": {"hash": SOURCE_HASH, "version": "3"},
        "execution_scope": ExecutionScope(venue="paper", account="strategy-a"),
        "approval_fingerprint": "c7" * 32,
        "lease_fingerprint": "d8" * 32,
        "checklist_digest": "e9" * 32,
        "runtime_fingerprint": RuntimeFingerprint.capture(),
        "created_ms": ARTIFACT_MS + 1000,
    }
    fields.update(overrides)
    return ReleaseManifest(**fields)


def expected_canonical_hash(obj):
    """The §6 canonical recipe, restated (never imported — see the docstring)."""
    canon = json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(canon.encode("ascii")).hexdigest()


class _Alpha:
    """A class whose source is fingerprinted."""

    def run(self, x):
        return x + 1


class _Beta:
    """A second class with the same body and a different ref."""

    def run(self, x):
        return x + 1


# --------------------------------------------------------------------------
# RuntimeFingerprint
# --------------------------------------------------------------------------


def test_runtime_fingerprint_fields_are_exactly_the_d24_inventory():
    assert tuple(f.name for f in dataclasses.fields(RuntimeFingerprint)) == RUNTIME_FIELDS


def test_runtime_fingerprint_capture_is_deterministic():
    first = RuntimeFingerprint.capture()
    second = RuntimeFingerprint.capture()
    assert first == second
    assert canonical_hash(first.to_obj()) == canonical_hash(second.to_obj())


def test_runtime_fingerprint_names_this_interpreter():
    fp = RuntimeFingerprint.capture()
    assert fp.python_implementation == python_implementation()
    assert fp.python_version == python_version()
    assert fp.cache_tag == sys.implementation.cache_tag
    assert fp.abi == sysconfig.get_config_var("SOABI")
    assert isinstance(fp.platform, str) and fp.platform


def test_runtime_fingerprint_lists_every_installed_distribution_sorted():
    fp = RuntimeFingerprint.capture()
    names = [d.name for d in fp.distributions]
    assert names == sorted(names)
    assert "pytest" in names
    found = [d for d in fp.distributions if d.name == "pytest"][0]
    assert found.version == metadata.version("pytest")


def test_a_distribution_entry_omits_the_fields_it_has_no_value_for():
    fp = RuntimeFingerprint.capture()
    assert tuple(f.name for f in dataclasses.fields(Distribution)) == (
        "name",
        "version",
        "direct_url",
        "record_digest",
    )
    for entry in fp.to_obj()["distributions"]:
        assert set(entry) <= {"name", "version", "direct_url", "record_digest"}
        assert {"name", "version"} <= set(entry)
        assert None not in entry.values()


def test_capture_makes_no_network_call(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("capture() must not touch the network")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert RuntimeFingerprint.capture().python_version == python_version()


def test_project_digests_pin_the_files_they_are_given(tmp_path):
    lock = tmp_path / "requirements.lock"
    lock.write_text("dskit==1.0\n", encoding="utf-8")
    fp = RuntimeFingerprint.capture(project_files=[lock])
    assert fp.project_digests[str(lock)] == hashlib.sha256(lock.read_bytes()).hexdigest()
    lock.write_text("dskit==1.1\n", encoding="utf-8")
    assert RuntimeFingerprint.capture(project_files=[lock]) != fp


def test_capture_refuses_a_project_file_that_is_not_there(tmp_path):
    missing = tmp_path / "uv.lock"
    with pytest.raises(ProductionError) as exc:
        RuntimeFingerprint.capture(project_files=[missing])
    assert "uv.lock" in str(exc.value)


def test_capture_without_project_files_records_none():
    assert RuntimeFingerprint.capture().project_digests == {}


# --------------------------------------------------------------------------
# class, adapter and artifact fingerprints
# --------------------------------------------------------------------------


def test_fingerprint_class_is_the_source_under_the_class_ref():
    expected = canonical_hash(
        {"ref": class_ref(_Alpha), "source": inspect.getsource(_Alpha)}
    )
    assert fingerprint_class(_Alpha) == expected
    assert fingerprint_class(_Alpha) == fingerprint_class(_Alpha)


def test_two_classes_with_the_same_body_fingerprint_differently():
    assert fingerprint_class(_Alpha) != fingerprint_class(_Beta)


def test_fingerprint_class_refuses_a_class_it_cannot_read():
    dynamic = type("_NeverWrittenDown", (), {})
    with pytest.raises(ProductionError) as exc:
        fingerprint_class(dynamic)
    assert "_NeverWrittenDown" in str(exc.value)


def test_artifact_digest_of_a_file_is_sha256_of_its_bytes(tmp_path):
    root = artifact_root(tmp_path)
    assert artifact_digest(root / "model") == hashlib.sha256(b"weights-v1").hexdigest()


def test_artifact_digest_of_a_directory_covers_names_and_bytes(tmp_path):
    root = artifact_root(tmp_path)
    expected = expected_canonical_hash(
        {
            "model": hashlib.sha256(b"weights-v1").hexdigest(),
            "params.json": hashlib.sha256(b'{"lookback": 30}').hexdigest(),
            "table/part/0.bin": hashlib.sha256(b"rows").hexdigest(),
        }
    )
    assert artifact_digest(root) == expected


def test_artifact_digest_moves_when_a_file_is_renamed_or_rewritten(tmp_path):
    root = artifact_root(tmp_path)
    before = artifact_digest(root)
    (root / "model").rename(root / "model.bin")
    assert artifact_digest(root) != before
    (root / "model.bin").rename(root / "model")
    assert artifact_digest(root) == before
    (root / "model").write_bytes(b"weights-v2")
    assert artifact_digest(root) != before


def test_artifact_digest_refuses_a_path_that_is_not_there(tmp_path):
    with pytest.raises(ProductionError) as exc:
        artifact_digest(tmp_path / "gone")
    assert "gone" in str(exc.value)


# --------------------------------------------------------------------------
# ISO-8601 durations (`serving.max_artifact_age`)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "ms"),
    (
        ("P30D", 2_592_000_000),
        ("P1D", 86_400_000),
        ("PT1H", 3_600_000),
        ("PT90M", 5_400_000),
        ("PT45S", 45_000),
        ("P1DT2H3M4S", 93_784_000),
        ("PT0S", 0),
    ),
)
def test_parse_iso_duration_reads_days_hours_minutes_and_seconds(text, ms):
    assert parse_iso_duration(text) == ms


@pytest.mark.parametrize(
    "text",
    ("P1M", "P1Y", "P1Y2M", "P2M3D", "30D", "P", "PT", "", "p30d", "PT1H30", "-P1D", "P-1D"),
)
def test_parse_iso_duration_refuses_calendar_units_and_malformed_text(text):
    with pytest.raises(ProductionError):
        parse_iso_duration(text)


# --------------------------------------------------------------------------
# ReleaseManifest — every release input bound
# --------------------------------------------------------------------------


def test_manifest_fields_are_exactly_the_release_inputs(tmp_path):
    assert tuple(f.name for f in dataclasses.fields(ReleaseManifest)) == MANIFEST_FIELDS
    assert set(manifest(artifact_root(tmp_path)).to_obj()) == set(MANIFEST_FIELDS)


def test_manifest_to_obj_is_json_native(tmp_path):
    obj = manifest(artifact_root(tmp_path)).to_obj()
    assert json.loads(json.dumps(obj)) == obj


def test_release_hash_is_the_canonical_hash_of_the_manifest(tmp_path):
    made = manifest(artifact_root(tmp_path))
    assert made.release_hash == expected_canonical_hash(made.to_obj())
    assert made.release_hash == canonical_hash(made.to_obj())
    assert len(made.release_hash) == 64


def test_the_expected_execution_scope_is_bound(tmp_path):
    made = manifest(artifact_root(tmp_path))
    assert made.execution_scope == ExecutionScope(venue="paper", account="strategy-a")
    assert made.to_obj()["execution_scope"] == {"venue": "paper", "account": "strategy-a"}


def test_the_feed_spec_binds_the_eight_section_5_2_fields(tmp_path):
    made = manifest(artifact_root(tmp_path))
    assert set(made.feed_spec) == set(FEED_SPEC_FIELDS)
    assert made.feed_spec["required_keys"] == ["AAPL", "MSFT"]
    assert made.feed_spec["required_keys_digest"] == "7a" * 32


@pytest.mark.parametrize("field", FEED_SPEC_FIELDS)
def test_a_feed_spec_missing_a_field_refuses(tmp_path, field):
    spec = feed_spec()
    del spec[field]
    with pytest.raises(ProductionError) as exc:
        manifest(artifact_root(tmp_path), feed_spec=spec)
    assert field in str(exc.value)


#: One mutation per manifest field — the completeness half of D24.
MANIFEST_MUTATIONS = (
    ("series_id", "018f0f4e-7b21-7d3a-9c31-6d8f36d806a2"),
    ("doc_hash", "0c" * 32),
    ("run_hash", "0d" * 32),
    ("serving_hash", "0e" * 32),
    ("classes", {"bars": {"ref": "yourproject.nodes:Bars", "code_digest": "00" * 32}}),
    ("adapter", {"name": "yourproject", "digest": "0b" * 32}),
    ("source_config", {"hash": "0a" * 32, "version": "3"}),
    ("execution_scope", ExecutionScope(venue="paper", account="strategy-b")),
    ("approval_fingerprint", "07" * 32),
    ("lease_fingerprint", "08" * 32),
    ("checklist_digest", "09" * 32),
    ("created_ms", ARTIFACT_MS + 2000),
)


@pytest.mark.parametrize(("field", "value"), MANIFEST_MUTATIONS)
def test_every_scalar_release_input_moves_the_release_hash(tmp_path, field, value):
    root = artifact_root(tmp_path)
    base = manifest(root)
    moved = dataclasses.replace(base, **{field: value})
    assert moved.release_hash != base.release_hash


def test_an_artifact_digest_or_timestamp_moves_the_release_hash(tmp_path):
    root = artifact_root(tmp_path)
    base = manifest(root)
    rehashed = dict(base.artifacts)
    rehashed["model"] = {"digest": "01" * 32, "timestamp_ms": ARTIFACT_MS}
    assert dataclasses.replace(base, artifacts=rehashed).release_hash != base.release_hash
    restamped = dict(base.artifacts)
    restamped["model"] = {**base.artifacts["model"], "timestamp_ms": ARTIFACT_MS + 1}
    assert dataclasses.replace(base, artifacts=restamped).release_hash != base.release_hash


def test_the_feed_spec_and_runtime_fingerprint_move_the_release_hash(tmp_path):
    root = artifact_root(tmp_path)
    base = manifest(root)
    spec = feed_spec()
    spec["required_keys"] = ["AAPL", "MSFT", "NVDA"]
    assert dataclasses.replace(base, feed_spec=spec).release_hash != base.release_hash
    drifted = dataclasses.replace(
        base.runtime_fingerprint, image_digest="sha256:" + "0" * 64
    )
    assert (
        dataclasses.replace(base, runtime_fingerprint=drifted).release_hash
        != base.release_hash
    )


def test_the_mutations_cover_every_manifest_field():
    covered = {field for field, _ in MANIFEST_MUTATIONS}
    covered |= {"artifacts", "feed_spec", "runtime_fingerprint"}
    assert covered == set(MANIFEST_FIELDS)


def test_manifest_round_trips_through_from_obj(tmp_path):
    made = manifest(artifact_root(tmp_path))
    assert ReleaseManifest.from_obj(made.to_obj()) == made
    assert ReleaseManifest.from_obj(json.loads(json.dumps(made.to_obj()))) == made


def test_from_obj_refuses_an_unknown_key(tmp_path):
    obj = manifest(artifact_root(tmp_path)).to_obj()
    obj["retention"] = "P7D"
    with pytest.raises(ProductionError) as exc:
        ReleaseManifest.from_obj(obj)
    assert "retention" in str(exc.value)


@pytest.mark.parametrize("field", MANIFEST_FIELDS)
def test_from_obj_refuses_a_missing_field(tmp_path, field):
    obj = manifest(artifact_root(tmp_path)).to_obj()
    del obj[field]
    with pytest.raises(ProductionError) as exc:
        ReleaseManifest.from_obj(obj)
    assert field in str(exc.value)


@pytest.mark.parametrize(
    ("field", "value", "offender"),
    (
        ("artifacts", {"model": {"digest": "01" * 32, "timestamp_ms": 1, "path": "x"}}, "path"),
        ("classes", {"bars": {"ref": "a:B", "code_digest": "01" * 32, "module": "a"}}, "module"),
        ("adapter", {"name": "yourproject", "digest": "01" * 32, "kind": "pkg"}, "kind"),
        ("source_config", {"hash": SOURCE_HASH, "version": "3", "alias": "bars"}, "alias"),
        ("feed_spec", {**feed_spec(), "locator": "s3://x"}, "locator"),
    ),
)
def test_from_obj_is_default_deny_inside_every_sub_mapping(tmp_path, field, value, offender):
    obj = manifest(artifact_root(tmp_path)).to_obj()
    obj[field] = value
    with pytest.raises(ProductionError) as exc:
        ReleaseManifest.from_obj(obj)
    assert offender in str(exc.value)


@pytest.mark.parametrize("missing", ("digest", "timestamp_ms"))
def test_an_artifact_entry_needs_a_digest_and_a_timestamp(tmp_path, missing):
    root = artifact_root(tmp_path)
    base = manifest(root)
    entry = {k: v for k, v in base.artifacts["model"].items() if k != missing}
    with pytest.raises(ProductionError) as exc:
        dataclasses.replace(base, artifacts={**base.artifacts, "model": entry})
    text = str(exc.value)
    assert missing in text
    assert "model" in text


def test_to_obj_hands_out_a_copy_the_caller_cannot_mutate_back_in(tmp_path):
    made = manifest(artifact_root(tmp_path))
    pinned = made.release_hash
    first = made.to_obj()
    first["artifacts"]["model"]["digest"] = "00" * 32
    first["feed_spec"]["required_keys"].append("TSLA")
    assert made.to_obj()["artifacts"]["model"]["digest"] != "00" * 32
    assert made.to_obj()["feed_spec"]["required_keys"] == ["AAPL", "MSFT"]
    assert made.release_hash == pinned


def test_the_release_binds_the_documents_identity_and_not_its_placement(tmp_path):
    # "Exact non-identity paths": relocating storage or notification endpoints
    # must not mint a new release; changing what the run computes must.
    root = artifact_root(tmp_path)
    base_doc = ServeDocument.from_obj(example_document())
    relocated = ServeDocument.from_obj(
        set_path(example_document(), ("placement", "ledger_root"), "/srv/elsewhere")
    )
    regraded = ServeDocument.from_obj(
        set_path(example_document(), ("schedule", "max_staleness_ms"), 60000)
    )
    same = manifest(root, doc_hash=base_doc.doc_hash)
    assert manifest(root, doc_hash=relocated.doc_hash).release_hash == same.release_hash
    assert manifest(root, doc_hash=regraded.doc_hash).release_hash != same.release_hash


# --------------------------------------------------------------------------
# ReleaseReader — the only capability a `release_read` node receives
# --------------------------------------------------------------------------


def test_the_reader_exposes_get_and_names_and_nothing_else(tmp_path):
    root = artifact_root(tmp_path)
    reader = ReleaseReader(manifest(root), ("model",), root)
    assert {name for name in dir(reader) if not name.startswith("_")} == {"get", "names"}


def test_get_returns_the_artifact_after_verifying_its_recorded_digest(tmp_path):
    root = artifact_root(tmp_path)
    reader = ReleaseReader(manifest(root), ("model", "params.json"), root)
    assert reader.get("model") == b"weights-v1"
    assert reader.get("params.json") == b'{"lookback": 30}'


def test_names_lists_exactly_what_this_node_may_read(tmp_path):
    root = artifact_root(tmp_path)
    reader = ReleaseReader(manifest(root), ("params.json", "model"), root)
    assert reader.names() == ("model", "params.json")


def test_get_refuses_a_name_the_manifest_does_not_carry(tmp_path):
    root = artifact_root(tmp_path)
    reader = ReleaseReader(manifest(root), ("model",), root)
    with pytest.raises(ProductionError) as exc:
        reader.get("secrets")
    assert "secrets" in str(exc.value)


def test_get_refuses_a_manifest_artifact_this_node_may_not_read(tmp_path):
    root = artifact_root(tmp_path)
    reader = ReleaseReader(manifest(root), ("model",), root)
    with pytest.raises(ProductionError) as exc:
        reader.get("params.json")
    assert "params.json" in str(exc.value)


def test_get_refuses_a_file_that_changed_since_the_release(tmp_path):
    root = artifact_root(tmp_path)
    reader = ReleaseReader(manifest(root), ("model",), root)
    (root / "model").write_bytes(b"weights-v2")
    with pytest.raises(ProductionError) as exc:
        reader.get("model")
    assert "model" in str(exc.value)


def test_get_refuses_a_file_that_is_gone(tmp_path):
    root = artifact_root(tmp_path)
    reader = ReleaseReader(manifest(root), ("model",), root)
    (root / "model").unlink()
    with pytest.raises(ProductionError) as exc:
        reader.get("model")
    assert "model" in str(exc.value)


def test_an_allowed_name_outside_the_manifest_refuses(tmp_path):
    root = artifact_root(tmp_path)
    with pytest.raises(ProductionError) as exc:
        ReleaseReader(manifest(root), ("model", "ghost"), root)
    assert "ghost" in str(exc.value)


# --------------------------------------------------------------------------
# verify_release — re-earned at startup, at every tick and before submit
# --------------------------------------------------------------------------


def test_verify_release_returns_none_when_nothing_drifted(tmp_path):
    root = artifact_root(tmp_path)
    assert verify_release(manifest(root), root, NOW_MS, MAX_AGE_MS) is None


def test_verify_release_refuses_a_mutated_artifact(tmp_path):
    root = artifact_root(tmp_path)
    made = manifest(root)
    (root / "model").write_bytes(b"weights-v2")
    with pytest.raises(ProductionError) as exc:
        verify_release(made, root, NOW_MS, MAX_AGE_MS)
    assert "model" in str(exc.value)


def test_verify_release_refuses_a_missing_artifact(tmp_path):
    root = artifact_root(tmp_path)
    made = manifest(root)
    (root / "params.json").unlink()
    with pytest.raises(ProductionError) as exc:
        verify_release(made, root, NOW_MS, MAX_AGE_MS)
    assert "params.json" in str(exc.value)


def test_verify_release_refuses_a_future_dated_timestamp(tmp_path):
    root = artifact_root(tmp_path)
    made = manifest(root)
    ahead = {**made.artifacts, "model": {**made.artifacts["model"], "timestamp_ms": NOW_MS + 1}}
    with pytest.raises(ProductionError) as exc:
        verify_release(dataclasses.replace(made, artifacts=ahead), root, NOW_MS, MAX_AGE_MS)
    assert "model" in str(exc.value)


def test_verify_release_refuses_an_expired_artifact_by_that_name(tmp_path):
    root = artifact_root(tmp_path)
    made = manifest(root)
    with pytest.raises(ProductionError) as exc:
        verify_release(made, root, ARTIFACT_MS + MAX_AGE_MS + 1, MAX_AGE_MS)
    text = str(exc.value)
    assert "artifact_expired" in text
    assert "model" in text


def test_an_artifact_exactly_at_the_age_bound_still_verifies(tmp_path):
    root = artifact_root(tmp_path)
    assert (
        verify_release(manifest(root), root, ARTIFACT_MS + MAX_AGE_MS, MAX_AGE_MS) is None
    )


def test_filesystem_mtimes_are_never_the_age_authority(tmp_path):
    root = artifact_root(tmp_path)
    made = manifest(root)
    os.utime(root / "model", (0, 0))
    assert verify_release(made, root, NOW_MS, MAX_AGE_MS) is None
    os.utime(root / "model", (NOW_MS / 1000, NOW_MS / 1000))
    with pytest.raises(ProductionError):
        verify_release(made, root, ARTIFACT_MS + MAX_AGE_MS + 1, MAX_AGE_MS)


def test_verify_release_refuses_a_changed_distribution_inventory(tmp_path):
    root = artifact_root(tmp_path)
    made = manifest(root)
    fp = made.runtime_fingerprint
    added = dataclasses.replace(
        fp,
        distributions=tuple(
            sorted(
                fp.distributions + (Distribution(name="zzzghost", version="9.9"),),
                key=lambda d: d.name,
            )
        ),
    )
    with pytest.raises(ProductionError) as exc:
        verify_release(dataclasses.replace(made, runtime_fingerprint=added), root, NOW_MS, MAX_AGE_MS)
    assert "runtime" in str(exc.value).lower()

    removed = dataclasses.replace(fp, distributions=fp.distributions[1:])
    with pytest.raises(ProductionError):
        verify_release(
            dataclasses.replace(made, runtime_fingerprint=removed), root, NOW_MS, MAX_AGE_MS
        )


def test_verify_release_refuses_interpreter_drift(tmp_path):
    root = artifact_root(tmp_path)
    made = manifest(root)
    drifted = dataclasses.replace(made.runtime_fingerprint, python_version="3.0.0")
    with pytest.raises(ProductionError) as exc:
        verify_release(
            dataclasses.replace(made, runtime_fingerprint=drifted), root, NOW_MS, MAX_AGE_MS
        )
    assert "runtime" in str(exc.value).lower()


def test_verify_release_refuses_source_config_drift(tmp_path):
    root = artifact_root(tmp_path)
    made = manifest(root)
    assert (
        verify_release(made, root, NOW_MS, MAX_AGE_MS, source_config_hash=SOURCE_HASH)
        is None
    )
    with pytest.raises(ProductionError) as exc:
        verify_release(made, root, NOW_MS, MAX_AGE_MS, source_config_hash="0f" * 32)
    assert "source" in str(exc.value)


# --------------------------------------------------------------------------
# write_release — the immutable release subdirectory
# --------------------------------------------------------------------------


def test_write_release_lays_out_the_release_directory(tmp_path):
    made = manifest(artifact_root(tmp_path))
    root = tmp_path / "serve" / SERIES_ID
    doc = ServeDocument.from_obj(example_document())
    written = write_release(root, made, doc)
    assert written == root / "releases" / made.release_hash
    assert json.loads((written / "document.json").read_text(encoding="utf-8")) == doc.to_obj()
    assert json.loads((written / "release.json").read_text(encoding="utf-8")) == made.to_obj()


def test_the_written_release_rehashes_to_its_own_directory_name(tmp_path):
    made = manifest(artifact_root(tmp_path))
    root = tmp_path / "serve"
    written = write_release(root, made, ServeDocument.from_obj(example_document()))
    on_disk = json.loads((written / "release.json").read_text(encoding="utf-8"))
    assert canonical_hash(on_disk) == made.release_hash == written.name


def test_write_release_is_idempotent_for_the_same_manifest(tmp_path):
    made = manifest(artifact_root(tmp_path))
    root = tmp_path / "serve"
    doc = ServeDocument.from_obj(example_document())
    first = write_release(root, made, doc)
    assert write_release(root, made, doc) == first


def test_write_release_refuses_a_differing_manifest_under_the_same_hash(tmp_path):
    made = manifest(artifact_root(tmp_path))
    root = tmp_path / "serve"
    doc = ServeDocument.from_obj(example_document())
    written = write_release(root, made, doc)
    tampered = made.to_obj()
    tampered["checklist_digest"] = "00" * 32
    (written / "release.json").write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ProductionError) as exc:
        write_release(root, made, doc)
    assert "release.json" in str(exc.value) or made.release_hash in str(exc.value)
