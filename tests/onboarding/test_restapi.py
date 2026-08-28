"""libs/restapi.py: the declarative REST connector, driven through the contract.

No network anywhere: every test scripts the ``_fetch`` seam, so the
retry, pagination, auth, and parsing logic above it runs for real.
"""

import json
import pathlib

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import check_config, check_message, resolve_connector
from dskit.onboarding.libs import restapi
from dskit.onboarding.libs.localfiles import LocalFilesConnector
from dskit.onboarding.libs.restapi import RestApiConnector

EXAMPLES = pathlib.Path(__file__).parents[2] / "examples" / "onboarding"

#: A deliberately unsorted page — emission must be effective-date order.
PAGE = {"data": [
    {"date": "2026-01-04", "close": "11"},
    {"date": "2026-01-02", "close": "10"},
    {"date": "2026-01-05", "close": "12"},
]}


@pytest.fixture
def conn():
    return RestApiConnector()


@pytest.fixture
def config():
    return {
        "base_url": "https://api.example.test/v1",
        "streams": {"prices": {"path": "prices", "records_path": "data"}},
        "effective_field": "date",
    }


def script(conn, *responses):
    """Replace the transport with a response queue; return the call log.

    Each response is a JSON-able object (-> 200), a ``(status, obj)``
    tuple, or an Exception instance to raise (a network error).
    """
    calls = []
    queue = list(responses)

    def _fetch(url, headers, timeout):
        calls.append((url, dict(headers)))
        assert queue, f"unexpected request: {url}"
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        status, obj = item if isinstance(item, tuple) else (200, item)
        return status, json.dumps(obj).encode("utf-8")

    conn._fetch = _fetch
    return calls


def _read(conn, config, streams, state=None, mode="live"):
    msgs = list(conn.read(config, streams, state or {}, mode))
    for m in msgs:
        assert check_message(m) is not None  # every message envelope-valid
    return msgs


# -- spec / config gate -----------------------------------------------------


def test_spec_passes_its_own_gate(conn, config):
    check_config(conn, config)
    check_config(conn, {**config, "notes": "documentation is always allowed"})
    with pytest.raises(AssetError, match="unknown key"):
        check_config(conn, {**config, "surprise": 1})
    with pytest.raises(AssetError, match="required knob"):
        check_config(conn, {"effective_field": "date"})
    with pytest.raises(AssetError, match="secret knob"):
        check_config(conn, {**config, "secret": 5})


def test_shipped_examples_pass_the_gate(conn):
    # Regression: the localfiles example used to fail its own default-deny.
    localfiles = json.loads((EXAMPLES / "source-localfiles.json").read_text())
    check_config(LocalFilesConnector(), localfiles)
    example = json.loads((EXAMPLES / "source-restapi.json").read_text())
    check_config(conn, example)


def test_bad_shapes_refused(conn, config):
    with pytest.raises(AssetError, match="base_url"):
        conn.check({**config, "base_url": "ftp://nope"})
    with pytest.raises(AssetError, match="at least one stream"):
        conn.check({**config, "streams": {}})
    with pytest.raises(AssetError, match="pagination.strategy"):
        conn.check({**config, "pagination": {"strategy": "scroll"}})
    with pytest.raises(AssetError, match="page_size is required"):
        conn.check({**config, "pagination": {
            "strategy": "offset", "offset_param": "o", "limit_param": "l"}})
    with pytest.raises(AssetError, match="without `secret`"):
        conn.check({**config, "auth_name": "Authorization"})


def test_timeout_and_retries_defaults_are_named_constants(conn, config, monkeypatch):
    # The module docstring and spec() notes state these defaults in prose;
    # pin both to the CURRENT constant value so a later change to the
    # constant without updating the static prose text goes red.
    assert restapi._DEFAULT_TIMEOUT == 30
    assert restapi._DEFAULT_MAX_RETRIES == 3

    # spec() notes are BUILT from the constants (f-strings), so they can
    # never independently drift — no separate pin is meaningful there.
    # The module docstring is static prose, so it's the one text that can
    # go stale; anchor on the whole "(default N)." bullet ending so the
    # short max_retries needle ("3).") cannot match inside the longer
    # timeout needle ("30).") or vice versa.
    assert f"(default {restapi._DEFAULT_TIMEOUT})." in restapi.__doc__
    assert f"(default {restapi._DEFAULT_MAX_RETRIES})." in restapi.__doc__

    # Rebind the constants to sentinel values: a call site that hardcoded
    # 30 / 3 instead of reading the constant would keep resolving to the
    # old default here and the test would fail.
    monkeypatch.setattr(restapi, "_DEFAULT_TIMEOUT", 99)
    monkeypatch.setattr(restapi, "_DEFAULT_MAX_RETRIES", 7)
    cfg = conn._conf(config)
    assert cfg["timeout"] == 99
    assert cfg["max_retries"] == 7


# -- check ------------------------------------------------------------------


def test_check_probes_once_and_discards(conn, config):
    calls = script(conn, PAGE)
    conn.check(config)
    assert len(calls) == 1 and "/prices" in calls[0][0]


def test_check_names_missing_secret_env(conn, config, monkeypatch):
    monkeypatch.delenv("RESTAPI_TOKEN", raising=False)
    config = {**config, "secret": "RESTAPI_TOKEN", "auth_name": "Authorization"}
    with pytest.raises(AssetError, match="RESTAPI_TOKEN"):
        conn.check(config)


# -- discover ---------------------------------------------------------------


def test_discover_is_declared_and_offline(conn, config):
    def boom(url, headers, timeout):
        raise AssertionError("discover must not touch the network")
    conn._fetch = boom
    config["streams"]["outlook"] = {
        "path": "outlook",
        "schema": {"fields": ["date", "view"]},
        "primary_key": ["date"],
    }
    streams = conn.discover(config)
    assert [s["stream"] for s in streams] == ["outlook", "prices"]
    assert streams[0]["schema"] == {"fields": ["date", "view"]}
    assert streams[0]["primary_key"] == ["date"]
    assert streams[1]["schema"] == {"fields": []}  # undeclared -> empty


# -- read: contract basics --------------------------------------------------


def test_read_sorts_and_emits_schema_then_state(conn, config):
    script(conn, PAGE)
    msgs = _read(conn, config, ["prices"])
    assert [m["type"] for m in msgs] == ["SCHEMA", "RECORD", "RECORD", "RECORD", "STATE"]
    assert msgs[0]["schema"] == {"fields": ["close", "date"]}  # derived
    effs = [m["effective_date"] for m in msgs if m["type"] == "RECORD"]
    assert effs == ["2026-01-02", "2026-01-04", "2026-01-05"]
    assert msgs[-1]["state"] == {"prices": {"cursor": "2026-01-05"}}


def test_cursor_filters_and_since_param_rides(conn, config):
    calls = script(conn, PAGE)
    config = {**config, "since_param": "since"}
    state = {"prices": {"cursor": "2026-01-04"}}
    records = [m for m in _read(conn, config, ["prices"], state)
               if m["type"] == "RECORD"]
    assert [m["effective_date"] for m in records] == ["2026-01-05"]
    assert "since=2026-01-04" in calls[0][0]  # pushed server-side too


def test_caught_up_stream_keeps_its_cursor(conn, config):
    script(conn, PAGE)
    state = {"prices": {"cursor": "2026-01-05"}}
    msgs = _read(conn, config, ["prices"], state)
    assert [m for m in msgs if m["type"] == "RECORD"] == []
    assert msgs[-1]["state"] == state  # cursor never regresses on empty


def test_forecast_streams_declared(conn, config):
    script(conn, PAGE)
    config = {**config, "forecast_streams": ["prices"]}
    records = [m for m in _read(conn, config, ["prices"]) if m["type"] == "RECORD"]
    assert all(m["kind"] == "forecast" for m in records)


def test_unknown_stream_named(conn, config):
    script(conn, PAGE)
    with pytest.raises(AssetError, match="unknown stream"):
        list(conn.read(config, ["ghost"], {}, "live"))


# -- pagination -------------------------------------------------------------


def test_cursor_token_chains_until_absent(conn, config):
    config = {**config, "pagination": {
        "strategy": "cursor", "path": "meta.next", "param": "cursor"}}
    calls = script(
        conn,
        {"data": [{"date": "2026-01-02"}], "meta": {"next": "abc"}},
        {"data": [{"date": "2026-01-03"}], "meta": {}},
    )
    records = [m for m in _read(conn, config, ["prices"]) if m["type"] == "RECORD"]
    assert len(records) == 2
    assert len(calls) == 2 and "cursor=abc" in calls[1][0]


def test_cursor_token_must_advance(conn, config):
    config = {**config, "pagination": {
        "strategy": "cursor", "path": "meta.next", "param": "cursor"}}
    script(
        conn,
        {"data": [{"date": "2026-01-02"}], "meta": {"next": "abc"}},
        {"data": [{"date": "2026-01-03"}], "meta": {"next": "abc"}},
    )
    with pytest.raises(AssetError, match="did not advance"):
        list(conn.read(config, ["prices"], {}, "live"))


def test_cursor_next_url_carries_param_auth(conn, config, monkeypatch):
    monkeypatch.setenv("RESTAPI_TOKEN", "tok")
    config = {**config,
              "pagination": {"strategy": "cursor", "path": "meta.next"},
              "secret": "RESTAPI_TOKEN", "auth_name": "key", "auth_in": "param"}
    calls = script(
        conn,
        {"data": [{"date": "2026-01-02"}],
         "meta": {"next": "https://api.example.test/v1/prices?page=2"}},
        {"data": [], "meta": {}},
    )
    _read(conn, config, ["prices"])
    assert calls[1][0] == "https://api.example.test/v1/prices?page=2&key=tok"


def test_cursor_next_url_must_be_absolute(conn, config):
    config = {**config, "pagination": {"strategy": "cursor", "path": "meta.next"}}
    script(conn, {"data": [{"date": "2026-01-02"}], "meta": {"next": "page2"}})
    with pytest.raises(AssetError, match="absolute next URL"):
        list(conn.read(config, ["prices"], {}, "live"))


def test_page_numbers_until_empty_or_short(conn, config):
    config = {**config, "pagination": {"strategy": "page", "param": "page"}}
    calls = script(conn, {"data": [{"date": "2026-01-02"}]},
                   {"data": [{"date": "2026-01-03"}]}, {"data": []})
    records = [m for m in _read(conn, config, ["prices"]) if m["type"] == "RECORD"]
    assert len(records) == 2 and len(calls) == 3
    assert "page=1" in calls[0][0] and "page=3" in calls[2][0]

    config["pagination"] = {"strategy": "page", "param": "page",
                            "size_param": "per_page", "page_size": 2}
    calls = script(conn, {"data": [{"date": "2026-01-02"}, {"date": "2026-01-03"}]},
                   {"data": [{"date": "2026-01-04"}]})
    records = [m for m in _read(conn, config, ["prices"]) if m["type"] == "RECORD"]
    assert len(records) == 3 and len(calls) == 2  # short page -> no empty probe
    assert "per_page=2" in calls[0][0]


def test_offset_walks_until_short_page(conn, config):
    config = {**config, "pagination": {
        "strategy": "offset", "offset_param": "offset",
        "limit_param": "limit", "page_size": 2}}
    calls = script(conn, {"data": [{"date": "2026-01-02"}, {"date": "2026-01-03"}]},
                   {"data": [{"date": "2026-01-04"}]})
    records = [m for m in _read(conn, config, ["prices"]) if m["type"] == "RECORD"]
    assert len(records) == 3 and len(calls) == 2
    assert "offset=0" in calls[0][0] and "limit=2" in calls[0][0]
    assert "offset=2" in calls[1][0]


# -- auth, retry, and failure shapes ---------------------------------------


def test_header_auth_formatted_from_env(conn, config, monkeypatch):
    monkeypatch.setenv("RESTAPI_TOKEN", "tok")
    config = {**config, "secret": "RESTAPI_TOKEN",
              "auth_name": "Authorization", "auth_format": "Bearer {secret}"}
    calls = script(conn, PAGE)
    _read(conn, config, ["prices"])
    assert calls[0][1]["Authorization"] == "Bearer tok"


def test_retries_recover_then_give_up(conn, config, monkeypatch):
    monkeypatch.setattr(restapi, "_BACKOFF", 0)
    calls = script(conn, (500, {}), OSError("connection refused"), PAGE)
    assert len(_read(conn, config, ["prices"])) == 5  # recovered
    assert len(calls) == 3

    script(conn, (503, {}), (503, {}))
    with pytest.raises(AssetError, match="giving up.*HTTP 503"):
        list(conn.read({**config, "max_retries": 1}, ["prices"], {}, "live"))


def test_client_errors_fail_immediately(conn, config):
    calls = script(conn, (404, {"error": "no such endpoint"}))
    with pytest.raises(AssetError, match="HTTP 404"):
        list(conn.read(config, ["prices"], {}, "live"))
    assert len(calls) == 1  # no retry on a non-transient status


def test_secret_never_leaks_into_errors(conn, config, monkeypatch):
    monkeypatch.setenv("RESTAPI_TOKEN", "tok-SECRET")
    config = {**config, "secret": "RESTAPI_TOKEN",
              "auth_name": "key", "auth_in": "param"}
    script(conn, (404, {}))
    with pytest.raises(AssetError) as exc:
        list(conn.read(config, ["prices"], {}, "live"))
    assert "tok-SECRET" not in str(exc.value)
    assert "?" not in str(exc.value)  # the whole query string is stripped


def test_response_shape_errors_named(conn, config):
    script(conn, {"data": {"not": "a list"}})
    with pytest.raises(AssetError, match="records_path"):
        list(conn.read(config, ["prices"], {}, "live"))

    script(conn, {"rows": []})
    config_flat = {**config, "streams": {"prices": {"path": "prices"}}}
    with pytest.raises(AssetError, match="declare records_path"):
        list(conn.read(config_flat, ["prices"], {}, "live"))

    script(conn, {"data": [{"date": "2026-01-02"}, ["not", "an", "object"]]})
    with pytest.raises(AssetError, match="record 1 is not an object"):
        list(conn.read(config, ["prices"], {}, "live"))

    script(conn, {"data": [{"date": "2026-01-02"}, {"close": "10"}]})
    with pytest.raises(AssetError, match="record 1.*'date' missing"):
        list(conn.read(config, ["prices"], {}, "live"))


def test_registered_kind_resolves(conn):
    assert resolve_connector("restapi") is RestApiConnector
