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
    assert top <= {"__future__", "datetime", "hashlib", "importlib", "json", "os", "re", "shutil", "string", "tempfile",
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


# -- entities_dates: UTC days that advance in live mode (ADR-0266) ---------------

DATES_AS_OF = "2026-01-03T12:00:00+00:00"
DATES = {"start": "2025-12-30", "lag_days": 1}
BACKFILL_DAYS = ["2025-12-30", "2025-12-31", "2026-01-01", "2026-01-02"]
CLOCK_START = "2026-01-06T03:00:00+00:00"  # UTC today 01-06, so live ends 01-05 at lag 1


@pytest.fixture
def clock(monkeypatch):
    """A settable clock (a one-cell list) behind the pack's own ``utc_now`` name."""
    cell = [CLOCK_START]
    monkeypatch.setattr(httpblobs, "utc_now", lambda: cell[0])
    return cell


@pytest.fixture
def dates_config(server, config):
    cfg = {k: v for k, v in config.items() if k != "entities"}
    cfg.update(entities_dates=dict(DATES), as_of=DATES_AS_OF)
    return cfg


def _publish(server, *days):
    for day in days:
        server.script[f"/chart/{day}"] = [(200, ('{"day": "%s"}' % day).encode(), {})]


def _paths(server):
    return [h[0] for h in server.hits]


def _record_days(msgs, status):
    return [m["data"]["entity"] for m in _by(msgs, "RECORD") if m["data"]["status"] == status]


def _seeded(server, cfg, through="2026-01-02"):
    """A live checkpoint already past the backfill, as the first live pull would leave it."""
    _publish(server, *BACKFILL_DAYS)
    state = _read(HttpBlobsConnector(), cfg)[-1]["state"]
    del server.hits[:]
    return dict(state, through=through, absent=[])


def test_backfill_covers_start_through_as_of_minus_lag_inclusive(dates_config):
    knobs = HttpBlobsConnector().resolve_knobs(dates_config)
    assert knobs["entities"] == BACKFILL_DAYS


def test_lag_days_defaults_to_the_named_constant(dates_config):
    dates_config["entities_dates"] = {"start": "2026-01-02"}
    assert httpblobs.DEFAULT_LAG_DAYS == 0
    assert HttpBlobsConnector().resolve_knobs(dates_config)["entities"] == ["2026-01-02", "2026-01-03"]


def test_dates_check_config_passes_and_the_knob_is_declared(dates_config):
    conn = HttpBlobsConnector()
    check_config(conn, dates_config)
    assert "entities_dates" in conn.spec()["params"]


def test_a_dates_backfill_is_the_declaration_an_explicit_list_would_be(server, dates_config, config):
    """Same days -> same checkpoint, so re-registering as dates re-pulls nothing."""
    _publish(server, *BACKFILL_DAYS)
    by_dates = _read(HttpBlobsConnector(), dates_config)
    by_list = _read(HttpBlobsConnector(), {**config, "entities": BACKFILL_DAYS, "as_of": DATES_AS_OF})
    assert by_dates[-1]["state"] == by_list[-1]["state"]
    hits = len(server.hits)
    again = _read(HttpBlobsConnector(), dates_config, state=by_list[-1]["state"])
    assert [m["type"] for m in again] == ["LOG", "STATE"] and len(server.hits) == hits


def test_backfill_ignores_the_clock(server, dates_config, clock):
    _publish(server, *BACKFILL_DAYS)
    clock[0] = "2030-01-01T00:00:00+00:00"
    assert _record_days(_read(HttpBlobsConnector(), dates_config), "ok") == BACKFILL_DAYS


def test_first_live_pull_starts_give_up_days_before_the_backfill_end(server, dates_config, clock):
    dates_config["entities_dates"] = {**DATES, "give_up_days": 2}
    days = ["2025-12-31", "2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]
    _publish(server, *days)
    msgs = _read(HttpBlobsConnector(), dates_config, mode="live")
    assert _paths(server) == [f"/chart/{d}" for d in days]
    assert _record_days(msgs, "ok") == days
    assert [m["relpath"] for m in _by(msgs, "FILE")] == [f"{d}/payload.json" for d in days]
    assert msgs[-1]["state"]["through"] == "2026-01-05"


def test_the_first_live_window_never_starts_before_start(server, dates_config, clock):
    _publish(server, *BACKFILL_DAYS, "2026-01-03", "2026-01-04", "2026-01-05")
    _read(HttpBlobsConnector(), dates_config, mode="live")  # give_up_days 7 reaches past start
    assert _paths(server)[0] == "/chart/2025-12-30"


def test_a_backfill_refusal_published_since_is_fetched_by_the_first_live_pull(server, dates_config, clock):
    dates_config["max_refused"] = 1
    _publish(server, "2025-12-30", "2025-12-31", "2026-01-01")  # backfill: 01-02 refused
    state = _read(HttpBlobsConnector(), dates_config)[-1]["state"]
    _publish(server, "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05")  # now published
    msgs = _read(HttpBlobsConnector(), dates_config, state=state, mode="live")
    assert "2026-01-02" in _record_days(msgs, "ok") and not _record_days(msgs, "refused")


def test_a_backfill_refusal_still_unpublished_blocks_young_and_is_given_up_when_old(server, dates_config, clock):
    dates_config.update(max_refused=1, entities_dates={**DATES, "give_up_days": 4})
    _publish(server, "2025-12-30", "2026-01-01", "2026-01-02")  # backfill: 12-31 refused
    state = _read(HttpBlobsConnector(), dates_config)[-1]["state"]
    _publish(server, "2026-01-03", "2026-01-04", "2026-01-05")
    old = _read(HttpBlobsConnector(), dates_config, state=state, mode="live")  # clock 01-06: 12-31 is 6 days old
    assert old[-1]["state"]["absent"] == ["2025-12-31"] and old[-1]["state"]["through"] == "2026-01-05"
    assert "2025-12-31" in _log_text(old)
    clock[0] = "2026-01-02T03:00:00+00:00"  # as if first pulled the day after: 12-31 is 2 days old
    young = _read(HttpBlobsConnector(), {**dates_config, "as_of": "2026-01-01T12:00:00+00:00"},
                  mode="live")
    assert young[-1]["state"]["absent"] == [] and young[-1]["state"]["through"] == "2025-12-30"


def test_live_resumes_from_its_checkpoint_and_advances_with_the_clock(server, dates_config, clock):
    seeded = _seeded(server, dates_config)
    _publish(server, "2026-01-03", "2026-01-04", "2026-01-05", "2026-01-06")
    state = _read(HttpBlobsConnector(), dates_config, state=seeded, mode="live")[-1]["state"]
    hits = len(server.hits)
    idle = _read(HttpBlobsConnector(), dates_config, state=state, mode="live")
    assert [m["type"] for m in idle] == ["LOG", "STATE"] and idle[-1]["state"] == state
    assert len(server.hits) == hits
    clock[0] = "2026-01-07T00:00:01+00:00"
    msgs = _read(HttpBlobsConnector(), dates_config, state=state, mode="live")
    assert _paths(server)[hits:] == ["/chart/2026-01-06"]
    assert msgs[-1]["state"]["through"] == "2026-01-06"
    assert msgs[-1]["state"]["declaration"] == state["declaration"]


def test_live_before_a_new_day_is_due_keeps_the_checkpoint(server, dates_config, clock):
    seeded = _seeded(server, dates_config)
    clock[0] = "2026-01-03T23:59:59+00:00"  # live ends 01-02 == the backfill end
    msgs = _read(HttpBlobsConnector(), dates_config, state=seeded, mode="live")
    assert [m["type"] for m in msgs] == ["LOG", "STATE"] and not server.hits
    assert msgs[-1]["state"]["through"] == "2026-01-02"


def test_a_refused_day_is_retried_and_the_checkpoint_never_passes_it(server, dates_config, clock):
    dates_config["max_refused"] = 1
    seeded = _seeded(server, dates_config)
    _publish(server, "2026-01-03", "2026-01-05")  # 01-04 not published yet -> 404
    first = _read(HttpBlobsConnector(), dates_config, state=seeded, mode="live")
    assert _record_days(first, "refused") == ["2026-01-04"]
    assert _record_days(first, "ok") == ["2026-01-03", "2026-01-05"]
    state = first[-1]["state"]
    assert state["through"] == "2026-01-03"
    _publish(server, "2026-01-04")
    del server.hits[:]
    second = _read(HttpBlobsConnector(), dates_config, state=state, mode="live")
    assert _paths(server) == ["/chart/2026-01-04", "/chart/2026-01-05"]
    assert _record_days(second, "refused") == [] and second[-1]["state"]["through"] == "2026-01-05"


def test_a_refused_first_live_day_leaves_the_checkpoint_at_the_backfill_end(server, dates_config, clock):
    dates_config["max_refused"] = 1
    seeded = _seeded(server, dates_config)
    _publish(server, "2026-01-04", "2026-01-05")
    msgs = _read(HttpBlobsConnector(), dates_config, state=seeded, mode="live")
    assert msgs[-1]["state"]["through"] == "2026-01-02"


def test_live_refusals_count_toward_max_refused_and_abort_without_state(server, dates_config, clock):
    seeded = _seeded(server, dates_config)
    _publish(server, "2026-01-03")
    with pytest.raises(AssetError, match="max_refused"):
        _read(HttpBlobsConnector(), dates_config, state=seeded, mode="live")  # default max_refused 0


def test_a_live_state_without_through_initialises_at_the_backfill_end(server, dates_config, clock):
    """The pre-ADR-0266 live state was the bare declaration digest."""
    _publish(server, *BACKFILL_DAYS)
    legacy = _read(HttpBlobsConnector(), dates_config)[-1]["state"]
    assert "through" not in legacy
    _publish(server, "2026-01-03", "2026-01-04", "2026-01-05")
    msgs = _read(HttpBlobsConnector(), dates_config, state=legacy, mode="live")
    assert _record_days(msgs, "ok") == [*BACKFILL_DAYS, "2026-01-03", "2026-01-04", "2026-01-05"]


def test_a_live_state_from_another_declaration_is_discarded(server, dates_config, clock):
    _publish(server, *BACKFILL_DAYS, "2026-01-03", "2026-01-04", "2026-01-05")
    stale = {"declaration": "0" * 64, "through": "2026-01-05"}
    msgs = _read(HttpBlobsConnector(), dates_config, state=stale, mode="live")
    assert _record_days(msgs, "ok") == [*BACKFILL_DAYS, "2026-01-03", "2026-01-04", "2026-01-05"]


@pytest.mark.parametrize("through", ["yesterday", 20260105, "2026-1-5", None])
def test_a_malformed_live_through_refuses(server, dates_config, clock, through):
    _publish(server, *BACKFILL_DAYS)
    state = dict(_read(HttpBlobsConnector(), dates_config)[-1]["state"], through=through)
    with pytest.raises(AssetError, match="through"):
        _read(HttpBlobsConnector(), dates_config, state=state, mode="live")


def test_live_with_plain_entities_is_unchanged(server, config, clock):
    first = _read(HttpBlobsConnector(), config, mode="live")
    assert len(_by(first, "FILE")) == 2 and "through" not in first[-1]["state"]
    again = _read(HttpBlobsConnector(), config, state=first[-1]["state"], mode="live")
    assert [m["type"] for m in again] == ["LOG", "STATE"]


@pytest.mark.parametrize("patch, needle", [
    ({"entities_dates": "2025-12-30"}, "entities_dates"),
    ({"entities_dates": {}}, "start"),
    ({"entities_dates": {"lag_days": 1}}, "start"),
    ({"entities_dates": {"start": "2025-12-30", "stride": 2}}, "stride"),
    ({"entities_dates": {"start": "2025-12-30", "notes": "x"}}, "notes"),
    ({"entities_dates": {"start": "20251230"}}, "start"),
    ({"entities_dates": {"start": "2025-13-40"}}, "start"),
    ({"entities_dates": {"start": 20251230}}, "start"),
    ({"entities_dates": {"start": "2025-12-30", "lag_days": -1}}, "lag_days"),
    ({"entities_dates": {"start": "2025-12-30", "lag_days": 1.5}}, "lag_days"),
    ({"entities_dates": {"start": "2025-12-30", "lag_days": True}}, "lag_days"),
    ({"entities_dates": {"start": "2026-01-03", "lag_days": 1}}, "after"),
    ({"entities": ["AAA"]}, "exactly one"),
    ({"entities_file": "/abs/u.json", "entities_sha256": "0" * 64}, "exactly one"),
    ({"entities_key": "dates"}, "entities_key needs"),
    ({"entities_sha256": "0" * 64}, "entities_sha256 needs"),
])
def test_entities_dates_is_default_deny_and_exclusive(dates_config, patch, needle):
    with pytest.raises(AssetError, match=needle):
        HttpBlobsConnector().check({**dates_config, **patch})


def test_none_of_the_three_entity_sources_refuses(dates_config):
    del dates_config["entities_dates"]
    with pytest.raises(AssetError, match="exactly one"):
        HttpBlobsConnector().check(dates_config)


def test_acquisition_advances_across_live_runs(root, registry, server, dates_config, clock):
    vid = registry.register("source_config", {
        "name": "http", "catalog_source": "http-src", "connector": "httpblobs",
        "config": dates_config}, origin="test")
    registry.transition(vid, "active", origin="test")
    _publish(server, *BACKFILL_DAYS, "2026-01-03", "2026-01-04", "2026-01-05", "2026-01-06")
    first = run_acquisition(root, registry, "http", "files", "live")
    assert first["files"] == 7  # the four backfill days it re-tries, then the three due and first["state_saved"]
    idle = run_acquisition(root, registry, "http", "files", "live")
    assert idle["snapshot"] is None and idle["files"] == 0
    clock[0] = "2026-01-07T00:00:01+00:00"
    nxt = run_acquisition(root, registry, "http", "files", "live")
    assert nxt["files"] == 1
    assert "/chart/2026-01-06" == _paths(server)[-1]


# -- give_up_days: a day the vendor never publishes stops blocking live (ADR-0266) --


def _log_text(msgs):
    return " ".join(m["message"] for m in _by(msgs, "LOG"))


def test_give_up_days_defaults_to_the_named_constant(dates_config):
    assert httpblobs.DEFAULT_GIVE_UP_DAYS == 7
    assert HttpBlobsConnector().resolve_knobs(dates_config)["dates"]["give_up_days"] == 7


def test_a_day_still_refused_past_give_up_days_is_recorded_absent_and_passed(server, dates_config, clock):
    dates_config.update(max_refused=1, entities_dates={**DATES, "give_up_days": 2})
    seeded = _seeded(server, dates_config)
    _publish(server, "2026-01-03", "2026-01-05", "2026-01-06")  # 01-04 never published
    clock[0] = "2026-01-07T03:00:00+00:00"  # live ends 01-06; 01-04 is 3 days old, over 2
    msgs = _read(HttpBlobsConnector(), dates_config, state=seeded, mode="live")
    state = msgs[-1]["state"]
    assert state["through"] == "2026-01-06" and state["absent"] == ["2026-01-04"]
    assert "2026-01-04" in _log_text(msgs) and "giving up" in _log_text(msgs)


def test_a_refused_day_is_not_given_up_before_it_is_older_than_give_up_days(server, dates_config, clock):
    dates_config.update(max_refused=1, entities_dates={**DATES, "give_up_days": 2})
    seeded = _seeded(server, dates_config)
    _publish(server, "2026-01-03", "2026-01-05")  # clock 01-06: 01-04 is exactly 2 days old
    msgs = _read(HttpBlobsConnector(), dates_config, state=seeded, mode="live")
    assert msgs[-1]["state"]["through"] == "2026-01-03" and msgs[-1]["state"]["absent"] == []
    assert "giving up" not in _log_text(msgs)


def test_absent_days_are_never_refetched_and_never_count_toward_max_refused(server, dates_config, clock):
    dates_config.update(max_refused=1, entities_dates={**DATES, "give_up_days": 2})
    seeded = _seeded(server, dates_config)
    _publish(server, "2026-01-03", "2026-01-05", "2026-01-06", "2026-01-07")
    clock[0] = "2026-01-07T03:00:00+00:00"
    state = _read(HttpBlobsConnector(), dates_config, state=seeded, mode="live")[-1]["state"]
    del server.hits[:]
    clock[0] = "2026-01-08T03:00:00+00:00"
    msgs = _read(HttpBlobsConnector(), {**dates_config, "max_refused": 0}, state=state, mode="live")
    assert _paths(server) == ["/chart/2026-01-07"]
    assert msgs[-1]["state"]["absent"] == ["2026-01-04"] and msgs[-1]["state"]["through"] == "2026-01-07"


def test_only_the_old_refused_days_are_given_up_while_a_young_one_still_blocks(server, dates_config, clock):
    dates_config.update(max_refused=2, entities_dates={**DATES, "give_up_days": 2})
    seeded = _seeded(server, dates_config)
    _publish(server, "2026-01-04", "2026-01-06")  # 01-03 old, 01-05 young, both unpublished
    clock[0] = "2026-01-07T03:00:00+00:00"
    state = _read(HttpBlobsConnector(), dates_config, state=seeded, mode="live")[-1]["state"]
    assert state["absent"] == ["2026-01-03"] and state["through"] == "2026-01-04"


@pytest.mark.parametrize("absent", ["2026-01-04", [20260104], ["soon"], None])
def test_a_malformed_live_absent_list_refuses(server, dates_config, clock, absent):
    _publish(server, *BACKFILL_DAYS)
    state = dict(_read(HttpBlobsConnector(), dates_config)[-1]["state"], through="2026-01-02", absent=absent)
    with pytest.raises(AssetError, match="absent"):
        _read(HttpBlobsConnector(), dates_config, state=state, mode="live")


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "7"])
def test_give_up_days_is_validated(dates_config, value):
    with pytest.raises(AssetError, match="give_up_days"):
        HttpBlobsConnector().check({**dates_config, "entities_dates": {**DATES, "give_up_days": value}})


def test_a_permanently_missing_run_does_not_trip_max_refused_once_old(server, dates_config, clock):
    """Four never-published days with max_refused 3 must not stall every live pull."""
    dates_config.update(max_refused=3, entities_dates={**DATES, "give_up_days": 3})
    seeded = _seeded(server, dates_config)
    _publish(server, "2026-01-07", "2026-01-08", "2026-01-09")  # 01-03..01-06 never published
    clock[0] = "2026-01-10T03:00:00+00:00"  # ages 7,6,5,4: all over give_up_days 3
    msgs = _read(HttpBlobsConnector(), dates_config, state=seeded, mode="live")
    state = msgs[-1]["state"]
    assert state["through"] == "2026-01-09"
    assert state["absent"] == ["2026-01-03", "2026-01-04", "2026-01-05", "2026-01-06"]
    assert _log_text(msgs).count("giving up") == 4


def test_only_young_refusals_count_toward_max_refused(server, dates_config, clock):
    dates_config.update(max_refused=1, entities_dates={**DATES, "give_up_days": 3})
    seeded = _seeded(server, dates_config)
    _publish(server, "2026-01-03", "2026-01-08")  # 01-04..01-06 old, 01-07 and 01-09 young, all missing
    clock[0] = "2026-01-10T03:00:00+00:00"
    with pytest.raises(AssetError, match="max_refused"):
        _read(HttpBlobsConnector(), dates_config, state=seeded, mode="live")  # 2 young > 1
    _publish(server, "2026-01-09")
    msgs = _read(HttpBlobsConnector(), dates_config, state=seeded, mode="live")  # 1 young + 3 old
    assert msgs[-1]["state"]["through"] == "2026-01-06"
    assert msgs[-1]["state"]["absent"] == ["2026-01-04", "2026-01-05", "2026-01-06"]


def test_a_day_that_blocks_is_not_logged_as_given_up(server, dates_config, clock):
    dates_config.update(max_refused=1, entities_dates={**DATES, "give_up_days": 2})
    seeded = _seeded(server, dates_config)
    _publish(server, "2026-01-03", "2026-01-05")  # clock 01-06: 01-04 is 2 days old, still blocking
    assert "giving up" not in _log_text(_read(HttpBlobsConnector(), dates_config, state=seeded,
                                              mode="live"))
