"""libs/httpblobs.py: per-entity HTTP payloads as hashed artifacts (ADR-0233).

No network: a fake HTTP server on 127.0.0.1 serves scripted responses, so
fetch, throttle, retry, the 404 refusal, the read-through cache, the
transform hook and the hash manifest all run through the real urllib seam.
"""

import hashlib
import http.server
import json
import os
import threading
import time

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
from dskit.onboarding.libs import httpblobs
from dskit.onboarding.libs.httpblobs import DEFAULT_STREAM, RECORD_FIELDS, HttpBlobsConnector

from .conftest import read_jsonl

AS_OF = "2026-01-01T00:00:00+00:00"

BODIES = {"AAA": b'{"v": 1, "bin": "\\u00ff"}', "BBB": b'{"v": 2}', "CCC": b'{"v": 3}'}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class ReverseTransform:
    """A test transform: the payload becomes its reversed bytes."""

    def __init__(self, params, as_of):
        self.suffix = params.get("suffix", b"") if isinstance(params, dict) else b""
        self.as_of = as_of

    def transform(self, entity, body):
        if body == b'{"v": 3}':
            raise ValueError("cannot reshape CCC")
        return body[::-1] + self.as_of.encode()

    def note(self, entity, body):
        return f"bytes={len(body)}"


class FakeServer:
    """Scripted server: ``script[path]`` is a list of (status, body, headers)
    consumed one per request; the last entry repeats. Arrival times and
    request headers are recorded."""

    def __init__(self):
        self.script = {f"/chart/{k}": [(200, v, {})] for k, v in BODIES.items()}
        self.hits = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?", 1)[0]
                outer.hits.append((path, time.monotonic(), dict(self.headers)))
                steps = outer.script.get(path)
                if steps is None:
                    status, body, headers = 404, b'{"error": "not found"}', {}
                else:
                    seen = sum(1 for h in outer.hits if h[0] == path)
                    status, body, headers = steps[min(seen, len(steps)) - 1]
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.01), daemon=True).start()

    def count(self, path):
        return sum(1 for h in self.hits if h[0] == path)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(httpblobs, "_BACKOFF", 0.0)


@pytest.fixture
def config(server):
    return {
        "url_template": server.url + "/chart/{entity}?interval=1d",
        "entities": ["AAA", "BBB"],
        "relpath_template": "{entity_lower}/payload.json",
        "as_of": AS_OF,
    }


def _read(conn, config, state=None, mode="backfill"):
    msgs = list(conn.read(config, [DEFAULT_STREAM], state or {}, mode))
    for m in msgs:
        assert check_message(m) is not None
    return msgs


def _by(msgs, kind):
    return [m for m in msgs if m["type"] == kind]


# -- the four verbs -------------------------------------------------------------


def test_registered_kind_resolves_to_the_pack():
    assert resolve_connector("httpblobs") is HttpBlobsConnector


def test_spec_is_default_deny_and_check_config_passes(config):
    conn = HttpBlobsConnector()
    check_config(conn, config)
    with pytest.raises(AssetError, match="nope"):
        check_config(conn, {**config, "nope": 1})


@pytest.mark.parametrize("patch, needle", [
    ({"url_template": "ftp://x/{entity}"}, "http"),
    ({"url_template": "http://x/fixed"}, "entity"),
    ({"entities": []}, "entity list"),
    ({"entities_file": "/nonexistent.json"}, "exactly one"),
    ({"entities_key": "x"}, "entities_key needs"),
    ({"entities": ["AAA", "AAA"]}, "duplicate"),
    ({"entities": ["a/../b"]}, "relpath"),
    ({"relpath_template": "fixed.json"}, "relpath_template"),
    ({"relpath_template": "../{entity}.json"}, "relpath"),
    ({"as_of": "2999-01-01T00:00:00+00:00"}, "future"),
    ({"as_of": "not a date"}, "as_of"),
    ({"throttle_s": -1}, "throttle_s"),
    ({"max_refused": -1}, "max_refused"),
    ({"max_bytes": 0}, "max_bytes"),
    ({"url_template": "http://x/{entity}/{foo}"}, "placeholder"),
    ({"relpath_template": "{entity}/{foo}.json"}, "placeholder"),
    ({"relpath_template": "{entity}{}.json"}, "placeholder"),
    ({"entities": ["a", "a/b"], "relpath_template": "{entity}"}, "clash"),
    ({"cache_dir": "$NO_SUCH_DSKIT_VAR_X", "raw_relpath_template": "_raw/{entity}.json"}, "cache_dir"),
    ({"max_retries": -1}, "max_retries"),
    ({"timeout": 0}, "timeout"),
    ({"headers": {"X": 1}}, "headers"),
    ({"transform": "not a ref"}, "transform"),
    ({"stream": "Bad Stream"}, "stream"),
    ({"cache_dir": "/tmp"}, "raw_relpath_template"),
    ({"raw_relpath_template": "{entity_lower}/payload.json"}, "collides"),
])
def test_check_refuses_every_bad_knob(config, patch, needle):
    with pytest.raises(AssetError, match=needle):
        HttpBlobsConnector().check({**config, **patch})


def _universe(tmp_path, doc):
    path = tmp_path / "universe.json"
    path.write_text(json.dumps(doc))
    return str(path)


def _pinned(path):
    return {"entities_file": path,
            "entities_sha256": hashlib.sha256(open(path, "rb").read()).hexdigest()}


def test_entities_come_from_a_universe_file_by_key_or_whole(server, config, tmp_path):
    cfg = {k: v for k, v in config.items() if k != "entities"}
    keyed = _universe(tmp_path, {"meta": {"tickers": ["AAA", "BBB"]}, "other": 1})
    msgs = _read(HttpBlobsConnector(), {**cfg, **_pinned(keyed), "entities_key": "meta.tickers"})
    assert [f["relpath"] for f in _by(msgs, "FILE")] == ["aaa/payload.json", "bbb/payload.json"]
    whole = _universe(tmp_path, ["BBB"])
    assert len(_by(_read(HttpBlobsConnector(), {**cfg, **_pinned(whole)}), "FILE")) == 1


@pytest.mark.parametrize("doc, key", [({"a": []}, "a"), ({"a": ["x"]}, "missing"), ("str", None),
                                      ({"a": ["x", "x"]}, "a")])
def test_a_bad_universe_file_refuses(config, tmp_path, doc, key):
    cfg = {k: v for k, v in config.items() if k != "entities"}
    cfg.update(_pinned(_universe(tmp_path, doc)))
    if key:
        cfg["entities_key"] = key
    with pytest.raises(AssetError):
        HttpBlobsConnector().check(cfg)


def _file_cfg(config, tmp_path, doc=("AAA", "BBB")):
    cfg = {k: v for k, v in config.items() if k != "entities"}
    cfg.update(_pinned(_universe(tmp_path, list(doc))))
    return cfg


def test_entities_file_requires_its_hash(config, tmp_path):
    cfg = _file_cfg(config, tmp_path)
    del cfg["entities_sha256"]
    with pytest.raises(AssetError, match="entities_sha256"):
        HttpBlobsConnector().check(cfg)


def test_entities_sha256_needs_entities_file(config):
    with pytest.raises(AssetError, match="entities_sha256 needs"):
        HttpBlobsConnector().check({**config, "entities_sha256": "0" * 64})


def test_a_changed_entities_file_refuses_at_check_and_read(server, config, tmp_path):
    cfg = _file_cfg(config, tmp_path)
    HttpBlobsConnector().check(cfg)
    open(cfg["entities_file"], "w").write(json.dumps(["AAA", "CCC"]))
    with pytest.raises(AssetError, match="does not match"):
        HttpBlobsConnector().check(cfg)
    with pytest.raises(AssetError, match="does not match"):
        _read(HttpBlobsConnector(), cfg)


def test_a_relative_entities_file_refuses(config, tmp_path, monkeypatch):
    cfg = _file_cfg(config, tmp_path)
    monkeypatch.chdir(tmp_path)
    cfg["entities_file"] = "universe.json"
    with pytest.raises(AssetError, match="absolute"):
        HttpBlobsConnector().check(cfg)


def test_entities_file_expands_vars_before_the_absolute_rule(config, tmp_path, monkeypatch):
    cfg = _file_cfg(config, tmp_path)
    monkeypatch.setenv("DSKIT_UNIVERSE_DIR", str(tmp_path))
    cfg["entities_file"] = "$DSKIT_UNIVERSE_DIR/universe.json"
    HttpBlobsConnector().check(cfg)


def test_discover_names_the_stream_and_inventory_schema(config):
    (entry,) = HttpBlobsConnector().discover(config)
    assert entry["stream"] == DEFAULT_STREAM
    assert entry["schema"]["fields"] == list(RECORD_FIELDS)
    assert entry["primary_key"] == ["entity"]


# -- fetch ----------------------------------------------------------------------


def test_fetch_emits_one_file_and_one_record_per_entity_then_state(server, config):
    msgs = _read(HttpBlobsConnector(), config)
    files = _by(msgs, "FILE")
    assert [f["relpath"] for f in files] == ["aaa/payload.json", "bbb/payload.json"]
    recs = {m["data"]["entity"]: m["data"] for m in _by(msgs, "RECORD")}
    for ent in ("AAA", "BBB"):
        assert recs[ent]["status"] == "ok" and recs[ent]["http_status"] == 200
        assert recs[ent]["sha256"] == _sha(BODIES[ent]) and recs[ent]["size"] == len(BODIES[ent])
    assert msgs[-1]["type"] == "STATE"
    assert server.count("/chart/AAA") == 1
    assert {m["effective_date"] for m in _by(msgs, "RECORD")} == {AS_OF}


def test_the_entity_is_url_quoted_and_the_query_survives(server, config):
    server.script["/chart/%5EVIX"] = [(200, b"{}", {})]
    _read(HttpBlobsConnector(), {**config, "entities": ["^VIX"],
                                 "relpath_template": "{entity_lower}.json"})
    assert server.count("/chart/%5EVIX") == 1


def test_static_headers_are_sent(server, config):
    _read(HttpBlobsConnector(), {**config, "headers": {"User-Agent": "dskit-test/1"}})
    assert server.hits[0][2]["User-Agent"] == "dskit-test/1"


def test_transform_hook_reshapes_the_payload_and_receives_as_of(server, config):
    cfg = {**config, "transform": "tests.onboarding.test_httpblobs:ReverseTransform"}
    msgs = _read(HttpBlobsConnector(), cfg)
    rec = {m["data"]["entity"]: m["data"] for m in _by(msgs, "RECORD")}["AAA"]
    want = BODIES["AAA"][::-1] + AS_OF.encode()
    assert rec["sha256"] == _sha(want) and rec["raw_sha256"] == _sha(BODIES["AAA"])


def test_transform_failure_is_a_recorded_refusal_not_an_abort(server, config):
    cfg = {**config, "entities": ["AAA", "CCC"], "max_refused": 1,
           "transform": "tests.onboarding.test_httpblobs:ReverseTransform"}
    msgs = _read(HttpBlobsConnector(), cfg)
    recs = {m["data"]["entity"]: m["data"] for m in _by(msgs, "RECORD")}
    assert recs["CCC"]["status"] == "refused" and "cannot reshape" in recs["CCC"]["reason"]
    assert recs["AAA"]["status"] == "ok"
    assert [f["relpath"] for f in _by(msgs, "FILE")] == ["aaa/payload.json"]


def test_raw_sidecar_is_kept_beside_the_transformed_payload(server, config):
    cfg = {**config, "raw_relpath_template": "_raw/{entity}.json",
           "transform": "tests.onboarding.test_httpblobs:ReverseTransform"}
    msgs = _read(HttpBlobsConnector(), cfg)
    assert [f["relpath"] for f in _by(msgs, "FILE")] == [
        "aaa/payload.json", "_raw/AAA.json", "bbb/payload.json", "_raw/BBB.json"]


# -- refusal --------------------------------------------------------------------


def test_404_is_a_recorded_refusal_and_the_rest_still_land(server, config):
    cfg = {**config, "entities": ["AAA", "GONE", "BBB"], "max_refused": 1}
    msgs = _read(HttpBlobsConnector(), cfg)
    recs = {m["data"]["entity"]: m["data"] for m in _by(msgs, "RECORD")}
    assert recs["GONE"]["status"] == "refused" and recs["GONE"]["http_status"] == 404
    assert recs["GONE"]["sha256"] is None and recs["GONE"]["relpath"] is None
    assert [f["relpath"] for f in _by(msgs, "FILE")] == ["aaa/payload.json", "bbb/payload.json"]
    assert server.count("/chart/GONE") == 1  # a 4xx is final, never retried
    assert msgs[-1]["type"] == "STATE"


# -- retry ----------------------------------------------------------------------


def test_transient_failures_retry_then_succeed(server, config):
    server.script["/chart/AAA"] = [(503, b"x", {}), (429, b"x", {"Retry-After": "0"}),
                                   (200, BODIES["AAA"], {})]
    msgs = _read(HttpBlobsConnector(), config)
    assert server.count("/chart/AAA") == 3
    assert {m["data"]["entity"]: m["data"]["status"] for m in _by(msgs, "RECORD")}["AAA"] == "ok"


def test_exhausted_retries_abort_without_state(server, config):
    server.script["/chart/AAA"] = [(500, b"boom", {})]
    gen = HttpBlobsConnector().read({**config, "max_retries": 2}, [DEFAULT_STREAM], {}, "backfill")
    seen = []
    with pytest.raises(AssetError, match="giving up.*3 attempt"):
        for m in gen:
            seen.append(m)
    assert server.count("/chart/AAA") == 3
    assert not [m for m in seen if m["type"] == "STATE"]


def test_a_network_error_is_retried_then_reported(config, monkeypatch):
    calls = []

    def refuse(self, url, headers, timeout, max_bytes):
        calls.append(url)
        raise ConnectionRefusedError("down")

    monkeypatch.setattr(HttpBlobsConnector, "_fetch", refuse)
    with pytest.raises(AssetError, match="giving up.*network error: down"):
        _read(HttpBlobsConnector(), {**config, "max_retries": 1})
    assert len(calls) == 2


# -- throttle -------------------------------------------------------------------


def test_throttle_spaces_network_requests(server, config):
    _read(HttpBlobsConnector(), {**config, "entities": ["AAA", "BBB", "CCC"],
                                 "throttle_s": 0.2})
    times = [h[1] for h in server.hits]
    assert len(times) == 3
    assert all(b - a >= 0.18 for a, b in zip(times, times[1:]))


# -- cache ----------------------------------------------------------------------


def test_cache_dir_serves_raw_payloads_without_the_network(server, config, tmp_path):
    cache = tmp_path / "cache"
    (cache / "_raw").mkdir(parents=True)
    (cache / "_raw" / "AAA.json").write_bytes(BODIES["AAA"])
    cfg = {**config, "raw_relpath_template": "_raw/{entity}.json", "cache_dir": str(cache)}
    msgs = _read(HttpBlobsConnector(), cfg)
    recs = {m["data"]["entity"]: m["data"] for m in _by(msgs, "RECORD")}
    assert server.count("/chart/AAA") == 0 and server.count("/chart/BBB") == 1
    assert "origin" not in recs["AAA"]  # provenance of the fetch never reaches a hashed record
    logs = " ".join(m["message"] for m in _by(msgs, "LOG"))
    assert "1 from cache" in logs and "1 from network" in logs
    assert recs["AAA"]["sha256"] == _sha(BODIES["AAA"])


def test_throttle_does_not_wait_on_cache_hits(server, config, tmp_path):
    cache = tmp_path / "cache"
    (cache / "_raw").mkdir(parents=True)
    for k in ("AAA", "BBB"):
        (cache / "_raw" / f"{k}.json").write_bytes(BODIES[k])
    cfg = {**config, "raw_relpath_template": "_raw/{entity}.json",
           "cache_dir": str(cache), "throttle_s": 30}
    start = time.monotonic()
    _read(HttpBlobsConnector(), cfg)
    assert time.monotonic() - start < 5 and not server.hits


# -- cursor ---------------------------------------------------------------------


def test_an_unchanged_declaration_does_no_network_and_keeps_state(server, config):
    first = _read(HttpBlobsConnector(), config)
    state = first[-1]["state"]
    hits = len(server.hits)
    again = _read(HttpBlobsConnector(), config, state=state)
    assert [m["type"] for m in again] == ["LOG", "STATE"] and again[-1]["state"] == state
    assert len(server.hits) == hits


def test_a_changed_declaration_repulls(server, config):
    state = _read(HttpBlobsConnector(), config)[-1]["state"]
    again = _read(HttpBlobsConnector(), {**config, "as_of": "2026-01-02T00:00:00+00:00"},
                  state=state)
    assert len(_by(again, "FILE")) == 2


def test_unknown_stream_refuses(config):
    with pytest.raises(AssetError, match="unknown stream"):
        list(HttpBlobsConnector().read(config, ["other"], {}, "backfill"))


# -- real acquisition -----------------------------------------------------------


@pytest.fixture
def http_source(registry, config):
    vid = registry.register("source_config", {
        "name": "http", "catalog_source": "http-src", "connector": "httpblobs",
        "config": dict(config, entities=["AAA", "GONE", "BBB"], max_refused=1),
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    return vid


def test_acquisition_lays_out_payload_and_hash_manifest(root, registry, server, http_source):
    s = run_acquisition(root, registry, "http", "files", "backfill")
    assert s["files"] == 2 and s["records"] == 3 and s["state_saved"]
    snap = root.snapshot_dir("http", s["acq_id"])
    manifest = {f["relpath"]: f for f in read_manifest(snap)["files"]}
    for ent in ("AAA", "BBB"):
        rel = f"files/{ent.lower()}/payload.json"
        assert manifest[rel]["sha256"] == _sha(BODIES[ent])
        with open(os.path.join(snap, "payload", *rel.split("/")), "rb") as fh:
            assert fh.read() == BODIES[ent]
    assert verify_snapshot(snap) == []
    bronze = read_jsonl(resolve_stream_file(os.path.join(snap, "payload"), "files"))
    by = {m["data"]["entity"]: m["data"] for m in bronze}
    assert by["GONE"]["status"] == "refused" and by["GONE"]["http_status"] == 404
    assert by["AAA"]["sha256"] == manifest["files/aaa/payload.json"]["sha256"]
    assert "127.0.0.1" not in open(os.path.join(snap, "manifest.json"), encoding="utf-8").read()
    again = run_acquisition(root, registry, "http", "files", "backfill")
    assert again["snapshot"] is None and again["files"] == 0


def test_the_pack_is_stdlib_only_at_import():
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(httpblobs.__file__).read_text(encoding="utf-8"))
    top = {a.name.split(".")[0] for n in tree.body if isinstance(n, ast.Import) for a in n.names}
    top |= {n.module.split(".")[0] for n in tree.body
            if isinstance(n, ast.ImportFrom) and n.module and not n.level}
    assert top <= {"__future__", "hashlib", "importlib", "json", "os", "re", "shutil", "string", "tempfile",
                   "time", "urllib"}


# -- review hardening -----------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 408])
def test_a_non_missing_4xx_aborts_the_pull_without_state(server, config, status):
    server.script["/chart/BBB"] = [(status, b"no", {})]
    seen = []
    with pytest.raises(AssetError, match=f"HTTP {status}"):
        for m in HttpBlobsConnector().read(config, [DEFAULT_STREAM], {}, "backfill"):
            seen.append(m)
    assert not [m for m in seen if m["type"] == "STATE"] and server.count("/chart/BBB") == 1


def test_410_is_a_refusal_like_404(server, config):
    server.script["/chart/BBB"] = [(410, b"gone", {})]
    msgs = _read(HttpBlobsConnector(), {**config, "max_refused": 1})
    assert {m["data"]["entity"]: m["data"]["http_status"] for m in _by(msgs, "RECORD")}["BBB"] == 410


def test_refusals_beyond_max_refused_abort(server, config):
    cfg = {**config, "entities": ["AAA", "X1", "X2"], "max_refused": 1}
    with pytest.raises(AssetError, match="max_refused"):
        _read(HttpBlobsConnector(), cfg)
    with pytest.raises(AssetError, match="max_refused"):
        _read(HttpBlobsConnector(), {k: v for k, v in cfg.items() if k != "max_refused"})


def test_identical_bytes_hash_identically_from_cache_or_network(server, config, tmp_path):
    raw = {**config, "raw_relpath_template": "_raw/{entity}.json"}
    net = {m["data"]["entity"]: m["data"] for m in _by(_read(HttpBlobsConnector(), raw), "RECORD")}
    cache = tmp_path / "c"
    (cache / "_raw").mkdir(parents=True)
    for k in ("AAA", "BBB"):
        (cache / "_raw" / f"{k}.json").write_bytes(BODIES[k])
    hit = {m["data"]["entity"]: m["data"]
           for m in _by(_read(HttpBlobsConnector(), {**raw, "cache_dir": str(cache)}), "RECORD")}
    assert net == hit


def test_max_bytes_caps_a_response(server, config):
    with pytest.raises(AssetError, match="max_bytes"):
        _read(HttpBlobsConnector(), {**config, "max_bytes": 5})
    assert len(_by(_read(HttpBlobsConnector(), {**config, "max_bytes": 1000}), "FILE")) == 2


def test_a_cross_host_redirect_is_refused_and_a_same_host_one_followed(server, config):
    other = server.url.replace("127.0.0.1", "localhost")
    server.script["/chart/AAA"] = [(302, b"", {"Location": other + "/chart/BBB"})]
    with pytest.raises(AssetError, match="redirect"):
        _read(HttpBlobsConnector(), config)
    server.hits.clear()
    server.script["/chart/AAA"] = [(302, b"", {"Location": "/chart/BBB"})]
    msgs = _read(HttpBlobsConnector(), config)
    assert len(_by(msgs, "FILE")) == 2


def test_env_vars_expand_in_path_knobs_and_a_missing_one_refuses(server, config, tmp_path, monkeypatch):
    (tmp_path / "_raw").mkdir()
    (tmp_path / "_raw" / "AAA.json").write_bytes(BODIES["AAA"])
    monkeypatch.setenv("DSKIT_TEST_CACHE", str(tmp_path))
    cfg = {**config, "raw_relpath_template": "_raw/{entity}.json", "cache_dir": "$DSKIT_TEST_CACHE"}
    _read(HttpBlobsConnector(), cfg)
    assert server.count("/chart/AAA") == 0


def test_one_owner_for_the_query_stripping_helper():
    from dskit.onboarding import connector
    from dskit.onboarding.libs import polymarket, restapi
    assert httpblobs.safe_url is restapi.safe_url is polymarket.safe_url is connector.safe_url
    assert connector.safe_url("http://h/p?token=x") == "http://h/p"


def test_a_transform_note_lands_in_the_record(server, config):
    cfg = {**config, "transform": "tests.onboarding.test_httpblobs:ReverseTransform"}
    recs = {m["data"]["entity"]: m["data"] for m in _by(_read(HttpBlobsConnector(), cfg), "RECORD")}
    assert recs["AAA"]["note"] == f"bytes={len(BODIES['AAA'])}"
    plain = {m["data"]["entity"]: m["data"] for m in _by(_read(HttpBlobsConnector(), config), "RECORD")}
    assert plain["AAA"]["note"] is None
