"""libs/huggingface.py: one hub repository as a WORM acquisition (ADR-0082)
— no network, no hub client.

The two transport seams are METHODS (``resolve``, ``download``), so the
double is a subclass with class-level script tables (the
``stub_connectors`` idiom: ``run_acquisition`` instantiates the class
itself). The REAL bodies are exercised against a fake ``huggingface_hub``
injected into ``sys.modules`` — exact kwargs, error mapping, and the token
never in text.
"""

import os
import sys
import types
from datetime import datetime, timezone

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import (
    check_config,
    check_message,
    file_digest,
    read_manifest,
    resolve_connector,
    run_acquisition,
    scan_stream,
    verified_payload_dir,
)
from dskit.onboarding.libs.huggingface import (
    DEFAULT_TOKEN_ENV,
    RECORD_FIELDS,
    REPO_TYPES,
    SNAPSHOT_STREAM,
    HuggingFaceHubConnector,
)
from dskit.onboarding.state import load_state

SHA = "0123456789abcdef0123456789abcdef01234567"
SHA2 = "fedcba9876543210fedcba9876543210fedcba98"
COMMITTED = datetime(2026, 2, 1, 9, 30, tzinfo=timezone.utc)
CONFIG = {"repo_id": "acme/tiny-bert", "revision": "main"}
FILES = {
    "config.json": b'{"hidden_size": 8}',
    "model.safetensors": bytes(range(256)),
    "tokenizer/vocab.txt": b"[PAD]\n[UNK]\nup\ndown\n",
}


class ScriptedHub(HuggingFaceHubConnector):
    """The pack with both hub seams replaced by class-level scripts."""

    sha = SHA
    last_modified = COMMITTED
    files = dict(FILES)
    calls = []
    local_dirs = []

    def resolve(self, repo_id, revision, repo_type, token, timeout_s):
        type(self).calls.append(("resolve", repo_id, revision, repo_type, token, timeout_s))
        return {"sha": type(self).sha, "last_modified": type(self).last_modified}

    def download(self, repo_id, revision, repo_type, allow_patterns, ignore_patterns,
                 token, local_dir):
        type(self).calls.append(("download", repo_id, revision, repo_type,
                                 allow_patterns, ignore_patterns, token, local_dir))
        type(self).local_dirs.append(local_dir)
        for relpath, data in type(self).files.items():
            path = os.path.join(local_dir, *relpath.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)
        # The hub's own metadata folder inside a local_dir download.
        meta = os.path.join(local_dir, ".cache", "huggingface", "download")
        os.makedirs(meta)
        with open(os.path.join(meta, "model.safetensors.metadata"), "w") as fh:
            fh.write("etag\n")


@pytest.fixture(autouse=True)
def reset_script():
    ScriptedHub.sha, ScriptedHub.last_modified = SHA, COMMITTED
    ScriptedHub.files, ScriptedHub.calls, ScriptedHub.local_dirs = dict(FILES), [], []
    yield
    ScriptedHub.sha, ScriptedHub.last_modified = SHA, COMMITTED
    ScriptedHub.files, ScriptedHub.calls, ScriptedHub.local_dirs = dict(FILES), [], []


@pytest.fixture
def hub_source(registry):
    """An ACTIVE source_config named 'hub' driving the scripted pack."""
    vid = registry.register("source_config", {
        "name": "hub",
        "catalog_source": "huggingface.co",
        "connector": "tests.onboarding.test_huggingface:ScriptedHub",
        "config": dict(CONFIG),
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    return vid


def _read(conn, config=CONFIG, state=None, mode="backfill"):
    return list(conn.read(dict(config), [SNAPSHOT_STREAM], state or {}, mode))


# -- the contract --------------------------------------------------------------


def test_registered_kind_resolves_to_the_pack():
    assert resolve_connector("huggingface") is HuggingFaceHubConnector


def test_spec_is_default_deny_and_names_its_secret():
    conn = HuggingFaceHubConnector()
    spec = conn.spec()["params"]
    assert spec["repo_id"]["required"] and spec["revision"]["required"]
    assert spec["token_env"]["secret"] is True
    check_config(conn, dict(CONFIG))
    with pytest.raises(AssetError, match="hub_name"):
        check_config(conn, {**CONFIG, "hub_name": "x"})
    with pytest.raises(AssetError, match="revision"):
        check_config(conn, {"repo_id": "acme/tiny-bert"})


@pytest.mark.parametrize(
    "bad, needle",
    [
        ({"repo_id": "no-owner"}, "repo_id"),
        ({"repo_id": "acme/tiny bert"}, "repo_id"),
        ({"revision": ""}, "revision"),
        ({"repo_type": "space"}, "repo_type"),
        ({"allow_patterns": "*.json"}, "allow_patterns"),
        ({"ignore_patterns": [""]}, "ignore_patterns"),
        ({"timeout_s": 0}, "timeout_s"),
        ({"timeout_s": float("nan")}, "timeout_s"),
    ],
)
def test_check_validates_every_knob_offline(bad, needle):
    with pytest.raises(AssetError, match=needle):
        HuggingFaceHubConnector().check({**CONFIG, **bad})


def test_check_reports_every_problem_at_once():
    with pytest.raises(AssetError) as exc:
        HuggingFaceHubConnector().check(
            {"repo_id": "nope", "revision": 3, "repo_type": "space"}
        )
    text = str(exc.value)
    assert "repo_id" in text and "revision" in text and "repo_type" in text


def test_discover_offers_the_snapshot_stream():
    [stream] = HuggingFaceHubConnector().discover(dict(CONFIG))
    assert stream["stream"] == SNAPSHOT_STREAM
    assert stream["primary_key"] == ["repo_id", "commit_sha", "relpath"]
    assert stream["schema"]["fields"] == list(RECORD_FIELDS)
    assert REPO_TYPES == ("model", "dataset")


def test_an_unknown_stream_refuses_by_name():
    with pytest.raises(AssetError, match="ledger"):
        list(ScriptedHub().read(dict(CONFIG), ["ledger"], {}, "backfill"))
    assert ScriptedHub.calls == []  # refused before the hub is asked anything


# -- read() --------------------------------------------------------------------


def test_read_emits_one_file_and_one_record_per_file_then_state():
    # A FILE's bytes are read AS the message arrives — the platform copies
    # them there and then; the staging directory is gone once read() ends.
    msgs, staged = [], {}
    for msg in ScriptedHub().read(dict(CONFIG), [SNAPSHOT_STREAM], {}, "backfill"):
        msgs.append(msg)
        if msg["type"] == "FILE":
            with open(msg["path"], "rb") as fh:
                staged[msg["relpath"]] = (fh.read(), file_digest(msg["path"]))
    assert [check_message(m) for m in msgs] == (
        ["FILE", "RECORD"] * len(FILES) + ["STATE"]
    )
    relpaths = [m["relpath"] for m in msgs if m["type"] == "FILE"]
    assert relpaths == sorted(FILES)  # the hub's .cache/ never becomes a file
    assert {k: v[0] for k, v in staged.items()} == FILES
    records = [m for m in msgs if m["type"] == "RECORD"]
    for rec in records:
        assert rec["effective_date"] == COMMITTED.isoformat()
        assert tuple(sorted(rec["data"])) == tuple(sorted(RECORD_FIELDS))
        assert rec["data"]["repo_id"] == "acme/tiny-bert"
        assert rec["data"]["repo_type"] == "model"
        assert rec["data"]["revision"] == "main"
        assert rec["data"]["commit_sha"] == SHA
        assert rec["data"]["size"] == len(FILES[rec["data"]["relpath"]])
        assert rec["data"]["sha256"] == staged[rec["data"]["relpath"]][1]
    assert msgs[-1]["state"] == {"commit_sha": SHA, "revision": "main"}


def test_the_staging_directory_is_gone_once_read_is_exhausted():
    _read(ScriptedHub())
    [local_dir] = ScriptedHub.local_dirs
    assert not os.path.exists(local_dir)


def test_download_is_pinned_to_the_resolved_sha_and_passes_the_knobs(monkeypatch):
    monkeypatch.setenv("MY_HF", "sekret-token")
    config = {**CONFIG, "token_env": "MY_HF", "allow_patterns": ["*.json"],
              "ignore_patterns": ["*.md"], "repo_type": "dataset", "timeout_s": 7}
    _read(ScriptedHub(), config=config)
    resolve, download = ScriptedHub.calls[0], ScriptedHub.calls[1]
    assert resolve == ("resolve", "acme/tiny-bert", "main", "dataset", "sekret-token", 7)
    assert download[1:7] == ("acme/tiny-bert", SHA, "dataset", ["*.json"], ["*.md"],
                             "sekret-token")


def test_an_unset_token_env_means_anonymous(monkeypatch):
    monkeypatch.delenv(DEFAULT_TOKEN_ENV, raising=False)
    _read(ScriptedHub())
    assert ScriptedHub.calls[0][4] is None and ScriptedHub.calls[1][6] is None


def test_an_unchanged_commit_is_an_empty_pull_that_never_downloads():
    msgs = _read(ScriptedHub(), state={"commit_sha": SHA, "revision": "main"})
    assert [m["type"] for m in msgs] == ["LOG", "STATE"]
    assert "nothing new" in msgs[0]["message"] and SHA[:12] in msgs[0]["message"]
    assert msgs[1]["state"] == {"commit_sha": SHA, "revision": "main"}
    assert [c[0] for c in ScriptedHub.calls] == ["resolve"]


def test_a_commit_the_hub_does_not_date_refuses():
    ScriptedHub.last_modified = None
    with pytest.raises(AssetError, match="date"):
        _read(ScriptedHub())


def test_a_malformed_sha_from_the_hub_refuses():
    ScriptedHub.sha = "not-a-sha"
    with pytest.raises(AssetError, match="commit sha"):
        _read(ScriptedHub())
    assert [c[0] for c in ScriptedHub.calls] == ["resolve"]


def test_a_dated_string_is_accepted_and_normalized():
    ScriptedHub.last_modified = "2026-02-01T09:30:00Z"
    msgs = _read(ScriptedHub())
    records = [m for m in msgs if m["type"] == "RECORD"]
    assert records and all(r["effective_date"] == "2026-02-01T09:30:00+00:00" for r in records)


# -- end to end through acquire ------------------------------------------------


def test_acquisition_lands_a_verifiable_snapshot_whose_inventory_matches(
    root, registry, hub_source
):
    s = run_acquisition(root, registry, "hub", SNAPSHOT_STREAM, "backfill")
    assert s["files"] == len(FILES) and s["records"] == len(FILES) and s["state_saved"]
    digest = registry.get(s["snapshot"]).payload["manifest_hash"]
    files_dir = verified_payload_dir(root.root, digest, SNAPSHOT_STREAM)
    assert sorted(
        os.path.relpath(os.path.join(d, f), files_dir).replace(os.sep, "/")
        for d, _, fs in os.walk(files_dir) for f in fs
    ) == sorted(FILES)
    manifest = {f["relpath"]: f for f in read_manifest(root.snapshot_dir("hub", s["acq_id"]))["files"]}
    rows = scan_stream(root.root, "hub", SNAPSHOT_STREAM,
                       key_fields=("repo_id", "commit_sha", "relpath"))
    assert len(rows) == len(FILES)
    for row in rows:
        assert manifest[f"{SNAPSHOT_STREAM}/{row['relpath']}"]["sha256"] == row["sha256"]
        assert manifest[f"{SNAPSHOT_STREAM}/{row['relpath']}"]["size"] == row["size"]
    assert load_state(root, "hub", SNAPSHOT_STREAM, "backfill") == {
        "commit_sha": SHA, "revision": "main"}
    assert registry.get(s["snapshot"]).payload["effective_start"] == COMMITTED.isoformat()


def test_a_second_pull_of_the_same_commit_commits_nothing(root, registry, hub_source):
    first = run_acquisition(root, registry, "hub", SNAPSHOT_STREAM, "backfill")
    again = run_acquisition(root, registry, "hub", SNAPSHOT_STREAM, "backfill")
    assert again["snapshot"] is None and again["files"] == 0 and again["state_saved"]
    assert os.listdir(root.raw_dir("hub")) == [first["acq_id"]]


def test_a_new_commit_lands_a_second_snapshot(root, registry, hub_source):
    run_acquisition(root, registry, "hub", SNAPSHOT_STREAM, "backfill")
    ScriptedHub.sha = SHA2
    ScriptedHub.files = {**FILES, "model.safetensors": bytes(reversed(range(256)))}
    s = run_acquisition(root, registry, "hub", SNAPSHOT_STREAM, "backfill")
    assert s["snapshot"] is not None and len(os.listdir(root.raw_dir("hub"))) == 2
    assert load_state(root, "hub", SNAPSHOT_STREAM, "backfill")["commit_sha"] == SHA2


# -- the real hub bodies, against a fake client ---------------------------------


class _HubError(Exception):
    """Stand-in for ``HfHubHTTPError`` (and its subclasses below)."""


class _RepoNotFound(_HubError):
    pass


class _RevisionNotFound(_HubError):
    pass


class _Gated(_RepoNotFound):
    pass


@pytest.fixture
def fake_hub(monkeypatch):
    """A fake ``huggingface_hub`` + ``huggingface_hub.utils`` in sys.modules."""
    calls = []
    behaviour = {"info": None, "download": None}

    class Info:
        def __init__(self, sha, last_modified):
            self.sha, self.last_modified = sha, last_modified

    class HfApi:
        def repo_info(self, repo_id, **kwargs):
            calls.append(("repo_info", repo_id, kwargs))
            if isinstance(behaviour["info"], Exception):
                raise behaviour["info"]
            return behaviour["info"] or Info(SHA, COMMITTED)

    def snapshot_download(**kwargs):
        calls.append(("snapshot_download", kwargs))
        if isinstance(behaviour["download"], Exception):
            raise behaviour["download"]
        return kwargs["local_dir"]

    hub = types.ModuleType("huggingface_hub")
    hub.HfApi, hub.snapshot_download = HfApi, snapshot_download
    utils = types.ModuleType("huggingface_hub.utils")
    utils.HfHubHTTPError = _HubError
    utils.RepositoryNotFoundError = _RepoNotFound
    utils.RevisionNotFoundError = _RevisionNotFound
    utils.GatedRepoError = _Gated
    hub.utils = utils
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setitem(sys.modules, "huggingface_hub.utils", utils)
    return calls, behaviour


def test_real_resolve_asks_the_hub_with_exact_kwargs(fake_hub):
    calls, _ = fake_hub
    out = HuggingFaceHubConnector().resolve("acme/tiny-bert", "main", "model", "tok", 9)
    assert out == {"sha": SHA, "last_modified": COMMITTED}
    assert calls == [("repo_info", "acme/tiny-bert",
                      {"revision": "main", "repo_type": "model", "timeout": 9, "token": "tok"})]


def test_real_download_pins_the_sha_and_stages_locally(fake_hub, tmp_path):
    calls, _ = fake_hub
    HuggingFaceHubConnector().download(
        "acme/tiny-bert", SHA, "model", ["*.json"], None, "tok", str(tmp_path))
    assert calls == [("snapshot_download", {
        "repo_id": "acme/tiny-bert", "revision": SHA, "repo_type": "model",
        "local_dir": str(tmp_path), "token": "tok",
        "allow_patterns": ["*.json"], "ignore_patterns": None,
    })]


@pytest.mark.parametrize(
    "error, needle",
    [
        (_RepoNotFound("404 Client Error: https://huggingface.co/api/models/x?token=sekret"),
         "not found"),
        (_Gated("403 gated"), "gated"),
        (_RevisionNotFound("404 revision"), "revision"),
        (_HubError("500 Server Error sekret"), "refused"),
        (OSError("connection reset sekret"), "unreachable"),
    ],
)
def test_real_hub_errors_cross_the_seam_typed_and_without_the_token(
    fake_hub, error, needle
):
    _, behaviour = fake_hub
    behaviour["info"] = error
    with pytest.raises(AssetError, match=needle) as exc:
        HuggingFaceHubConnector().resolve("acme/x", "main", "model", "sekret", 5)
    assert "sekret" not in str(exc.value)
    behaviour["info"], behaviour["download"] = None, error
    with pytest.raises(AssetError, match=needle) as exc:
        HuggingFaceHubConnector().download("acme/x", SHA, "model", None, None, "sekret", "/tmp")
    assert "sekret" not in str(exc.value)


def test_without_the_hub_client_both_seams_refuse_naming_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    with pytest.raises(AssetError, match=r"dskit\[huggingface\]"):
        HuggingFaceHubConnector().resolve("acme/x", "main", "model", None, 5)
    with pytest.raises(AssetError, match=r"dskit\[huggingface\]"):
        HuggingFaceHubConnector().download("acme/x", SHA, "model", None, None, None, "/tmp")
