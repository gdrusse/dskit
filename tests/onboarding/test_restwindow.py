"""libs/restwindow.py: the time-window REST pack, driven through the contract (ADR-0237).

No network anywhere except a loopback server: every test but the transport
and parity ones injects the ``getter`` transport, a recording ``sleeper``
and a fixed ``clock``, so window arithmetic, retry, pagination and the row
shapes above them run for real. The three worked examples under
``examples/onboarding`` are loaded and driven through scripted servers
shaped like the venues they were modelled on (a candles endpoint, an
index-series endpoint, a trades endpoint) -- shapes only; no test touches
a vendor. The acquisition e2e resolves :class:`StubRestWindowConnector`
below by class reference (the platform instantiates a connector with no
arguments, so the stub binds its scripted transport in ``__init__``).
"""

import copy
import http.server
import json
import pathlib
import threading
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import (
    check_config,
    check_message,
    load_state,
    resolve_connector,
    run_acquisition,
    scan_stream,
)
from dskit.onboarding.connector import MAX_BACKOFF_S
from dskit.onboarding.libs import restwindow
from dskit.onboarding.libs.restapi import RestApiConnector
from dskit.onboarding.libs.restwindow import RestWindowConnector

EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "examples" / "onboarding"
BASE = "https://api.example.test"
NOW = datetime(2026, 10, 6, 12, 0, 30, 750000, tzinfo=timezone.utc)
UTC = timezone.utc


def example(name):
    return json.loads((EXAMPLES / f"source-restwindow-{name}.json").read_text())


def at(text):
    """An aware UTC datetime from an ISO string with or without ``Z``."""
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def epoch_s(text):
    return int(at(text).timestamp())


def iso(dt):
    return dt.astimezone(UTC).isoformat()


def http_error(code, headers=None):
    return urllib.error.HTTPError(BASE, code, "scripted", headers or {}, None)


class Script:
    """A scripted ``getter(url, params)`` routed by path under BASE.

    A route value is a body, an Exception instance to raise, a callable of
    ``params`` returning either, or a list of those consumed in order (a
    response queue). Every call is recorded.
    """

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def __call__(self, url, params):
        assert url.startswith(BASE + "/"), url
        path = url[len(BASE):]
        self.calls.append((path, dict(params)))
        handler = self.routes.get(path)
        assert handler is not None, f"unexpected request {path} {params}"
        if isinstance(handler, list):
            assert handler, f"response queue for {path} exhausted"
            handler = handler.pop(0)
        if callable(handler):
            handler = handler(params)
        if isinstance(handler, Exception):
            raise handler
        return handler

    def params(self):
        return [params for _path, params in self.calls]


def connector(routes, now=NOW):
    """A connector over a scripted transport; returns (connector, script, sleeps)."""
    script = Script(routes)
    sleeps = []
    conn = RestWindowConnector(getter=script, sleeper=sleeps.append, clock=lambda: now)
    return conn, script, sleeps


def read(conn, config, streams, state=None, mode="backfill"):
    msgs = list(conn.read(config, streams, state or {}, mode))
    for msg in msgs:
        assert check_message(msg) is not None  # every message envelope-valid
    return msgs


def records(msgs):
    return [m for m in msgs if m["type"] == "RECORD"]


def instants(msgs):
    return [m["effective_date"] for m in records(msgs)]


def state_of(msgs):
    (last,) = [m for m in msgs if m["type"] == "STATE"][-1:]
    return last["state"]


# -- a plain dict-row window stream: the arithmetic fixture -----------------

SERIES = "/series"


def plain(pagination=None, **decl):
    """One dict-row stream ``s`` over a window pagination; override at will."""
    window = {"strategy": "window", "start": "2026-01-01T00:00:00Z",
              "end": "2026-01-01T00:10:00Z", "step": 240,
              "start_param": "from", "end_param": "to"}
    window.update(pagination or {})
    window = {k: v for k, v in window.items() if v is not None}  # None removes a key
    stream = {"path": SERIES, "records_path": "rows", "effective_field": "ts",
              "pagination": window}
    stream.update(decl)
    return {"base_url": BASE, "streams": {"s": stream}}


def empty_rows(_params):
    return {"rows": []}


# -- spec / config gate ------------------------------------------------------


def test_spec_passes_its_own_gate():
    conn = RestWindowConnector()
    check_config(conn, plain())
    check_config(conn, {**plain(), "notes": "documentation is always allowed"})
    with pytest.raises(AssetError, match="unknown key"):
        check_config(conn, {**plain(), "surprise": 1})
    with pytest.raises(AssetError, match="required knob"):
        check_config(conn, {"base_url": BASE})
    with pytest.raises(AssetError, match="required knob"):
        check_config(conn, {"streams": plain()["streams"]})


@pytest.mark.parametrize("name", ["candles", "index-series", "trades"])
def test_shipped_examples_pass_the_gate(name):
    check_config(RestWindowConnector(), example(name))


def test_resolves_by_import_path():
    cls = resolve_connector("dskit.onboarding.libs.restwindow:RestWindowConnector")
    assert cls is RestWindowConnector


def test_constructor_refuses_non_callables():
    for name in ("getter", "sleeper", "clock"):
        with pytest.raises(AssetError, match=f"{name} must be callable"):
            RestWindowConnector(**{name: 3})


def mutate(change, base=None):
    config = copy.deepcopy(base or plain())
    change(config, config["streams"]["s"], config["streams"]["s"]["pagination"])
    return config


BAD_SHAPES = [
    ("base_url", lambda c, s, p: c.update(base_url="ftp://nope"),
     "config.base_url must be an http"),
    ("no streams", lambda c, s, p: c.update(streams={}), "at least one stream"),
    ("stream not a dict", lambda c, s, p: c["streams"].update(s=3), "config.streams.s"),
    ("stream unknown key", lambda c, s, p: s.update(surprise=1), "unknown key"),
    ("path missing", lambda c, s, p: s.pop("path"), "streams.s.path must be a non-empty"),
    ("path with query", lambda c, s, p: s.update(path="/a?b=1"),
     "path may not carry a query"),
    ("param non-scalar", lambda c, s, p: s.update(params={"a": [1]}),
     "params.a must be a string or number"),
    ("param bool", lambda c, s, p: s.update(params={"a": True}),
     "params.a must be a string or number"),
    ("effective_field missing", lambda c, s, p: s.pop("effective_field"),
     "effective_field must be a non-empty"),
    ("records_path empty segment", lambda c, s, p: s.update(records_path="a..b"),
     "empty segment"),
    ("records_path two stars", lambda c, s, p: s.update(records_path="*.*"),
     r"one '\*', as the last"),
    ("records_path star first", lambda c, s, p: s.update(records_path="*.rows"),
     r"one '\*', as the last"),
    ("row_fields empty", lambda c, s, p: s.update(row_fields=[]),
     "row_fields must be a non-empty list of distinct"),
    ("row_fields duplicate", lambda c, s, p: s.update(row_fields=["ts", "ts"]),
     "row_fields must be a non-empty list of distinct"),
    ("row_fields non-string", lambda c, s, p: s.update(row_fields=["ts", 1]),
     "row_fields must be a non-empty list of distinct"),
    ("row_fields lacks effective", lambda c, s, p: s.update(row_fields=["a", "b"]),
     "effective_field must be one of row_fields"),
    ("epoch bad unit",
     lambda c, s, p: s.update(row_fields=["t", "v"], effective_field="iso",
                              epoch={"field": "t", "unit": "us"}),
     "epoch.unit must be one of"),
    ("epoch field not a row field",
     lambda c, s, p: s.update(row_fields=["t", "v"], effective_field="iso",
                              epoch={"field": "x", "unit": "s"}),
     "epoch.field must be one of row_fields"),
    ("epoch effective is a row field",
     lambda c, s, p: s.update(row_fields=["t", "iso"], effective_field="iso",
                              epoch={"field": "t", "unit": "s"}),
     "may not be a row_fields name"),
    ("epoch effective is the epoch field",
     lambda c, s, p: s.update(effective_field="t", epoch={"field": "t", "unit": "s"}),
     "epoch.field and effective_field must differ"),
    ("epoch unknown key",
     lambda c, s, p: s.update(epoch={"field": "t", "unit": "s", "tz": "x"}),
     "unknown key"),
    ("primary_key not a list", lambda c, s, p: s.update(primary_key="ts"),
     "primary_key must be a list"),
    ("primary_key unknown field",
     lambda c, s, p: s.update(row_fields=["ts", "v"], primary_key=["nope"]),
     "primary_key names"),
    ("schema not a dict", lambda c, s, p: s.update(schema=[1]), "streams.s.schema"),
    ("pagination not a dict", lambda c, s, p: s.update(pagination=3),
     "streams.s.pagination"),
    ("strategy unknown", lambda c, s, p: p.update(strategy="scroll"),
     "pagination.strategy must be one of"),
    ("window missing step", lambda c, s, p: p.pop("step"), "pagination.step must be"),
    ("window step zero", lambda c, s, p: p.update(step=0), "pagination.step must be an int >= 1"),
    ("window step bool", lambda c, s, p: p.update(step=True),
     "pagination.step must be an int >= 1"),
    ("window step float", lambda c, s, p: p.update(step=1.5),
     "pagination.step must be an int >= 1"),
    ("window start missing", lambda c, s, p: p.pop("start"), "pagination.start must be an ISO"),
    ("window start not ISO", lambda c, s, p: p.update(start="yesterday"),
     "pagination.start must be an ISO"),
    ("window start sub-second", lambda c, s, p: p.update(start="2026-01-01T00:00:00.5Z"),
     "whole seconds"),
    ("window end sub-second", lambda c, s, p: p.update(end="2026-01-01T00:10:00.5Z"),
     "whole seconds"),
    ("window end not after start", lambda c, s, p: p.update(end="2026-01-01T00:00:00Z"),
     "pagination.end must be after pagination.start"),
    ("window params equal", lambda c, s, p: p.update(end_param="from"),
     "start_param and end_param must differ"),
    ("window param missing", lambda c, s, p: p.pop("end_param"),
     "pagination.end_param must be a non-empty"),
    ("window param collides with static",
     lambda c, s, p: s.update(params={"from": 1}), "collides with a static param"),
    ("window time_format", lambda c, s, p: p.update(time_format="unix"),
     "time_format must be one of"),
    ("window max_windows zero", lambda c, s, p: p.update(max_windows=0),
     "max_windows must be an int >= 1"),
    ("window lag negative", lambda c, s, p: p.update(lag=-1), "lag must be an int >= 0"),
    ("window lag with end", lambda c, s, p: p.update(lag=5), "lag applies only"),
    ("window truncated_path empty", lambda c, s, p: p.update(truncated_path=""),
     "truncated_path must be a non-empty"),
    ("window unknown key", lambda c, s, p: p.update(page_size=5), "unknown key"),
    ("cursor missing path",
     lambda c, s, p: s.update(pagination={"strategy": "cursor", "param": "since"}),
     "pagination.path must be a non-empty"),
    ("cursor missing param",
     lambda c, s, p: s.update(pagination={"strategy": "cursor", "path": "last"}),
     "pagination.param must be a non-empty"),
    ("cursor start without time_format",
     lambda c, s, p: s.update(pagination={"strategy": "cursor", "path": "last",
                                          "param": "since",
                                          "start": "2026-01-01T00:00:00Z"}),
     "pagination.start needs time_format"),
    ("cursor max_pages zero",
     lambda c, s, p: s.update(pagination={"strategy": "cursor", "path": "last",
                                          "param": "since", "max_pages": 0}),
     "max_pages must be an int >= 1"),
    ("cursor param collides with static",
     lambda c, s, p: s.update(params={"since": 1},
                              pagination={"strategy": "cursor", "path": "last",
                                          "param": "since"}),
     "collides with a static param"),
    ("cursor page_size zero",
     lambda c, s, p: s.update(pagination={"strategy": "cursor", "path": "last",
                                          "param": "since", "page_size": 0}),
     "page_size must be an int >= 1"),
    ("cursor size_param without page_size",
     lambda c, s, p: s.update(pagination={"strategy": "cursor", "path": "last",
                                          "param": "since", "size_param": "count"}),
     "size_param needs page_size"),
    ("cursor size_param collides with static",
     lambda c, s, p: s.update(params={"count": 5},
                              pagination={"strategy": "cursor", "path": "last", "param": "since",
                                          "size_param": "count", "page_size": 5}),
     "collides with a static param"),
    ("cursor size_param equals param",
     lambda c, s, p: s.update(pagination={"strategy": "cursor", "path": "last", "param": "since",
                                          "size_param": "since", "page_size": 5}),
     "param and size_param must differ"),
    ("cursor unknown key (end)",
     lambda c, s, p: s.update(pagination={"strategy": "cursor", "path": "last",
                                          "param": "since", "end": "2027-01-01"}),
     "unknown key"),
    ("none unknown key", lambda c, s, p: s.update(pagination={"strategy": "none", "step": 1}),
     "unknown key"),
    ("timeout zero", lambda c, s, p: c.update(timeout=0), "config.timeout must be a positive"),
    ("timeout bool", lambda c, s, p: c.update(timeout=True),
     "config.timeout must be a positive"),
    ("max_retries negative", lambda c, s, p: c.update(max_retries=-1),
     "config.max_retries must be an int >= 0"),
    ("pace negative", lambda c, s, p: c.update(pace_s=-1),
     "config.pace_s must be a number >= 0"),
    ("pace bool", lambda c, s, p: c.update(pace_s=True),
     "config.pace_s must be a number >= 0"),
]


@pytest.mark.parametrize("change,fragment", [(c, f) for _n, c, f in BAD_SHAPES],
                         ids=[n for n, _c, _f in BAD_SHAPES])
def test_bad_shapes_refused_by_every_verb_before_any_request(change, fragment):
    config = mutate(change)
    conn, script, _ = connector({SERIES: empty_rows})
    with pytest.raises(AssetError, match=fragment):
        conn.discover(config)
    with pytest.raises(AssetError, match=fragment):
        conn.check(config)
    with pytest.raises(AssetError, match=fragment):
        list(conn.read(config, ["s"], {}, "backfill"))
    assert script.calls == []


def test_every_problem_is_listed_at_once():
    config = mutate(lambda c, s, p: (s.pop("path"), p.update(step=0), c.update(timeout=0)))
    conn, _, _ = connector({})
    with pytest.raises(AssetError) as exc:
        conn.discover(config)
    text = str(exc.value)
    assert "streams.s.path" in text and "pagination.step" in text and "config.timeout" in text


def test_pagination_defaults_to_none_and_none_is_one_request():
    config = {"base_url": BASE, "streams": {"s": {
        "path": SERIES, "records_path": "rows", "effective_field": "ts"}}}
    conn, script, _ = connector({SERIES: lambda p: {"rows": [
        {"ts": "2026-01-01T00:00:00Z", "v": 1}]}})
    msgs = read(conn, config, ["s"])
    assert script.calls == [(SERIES, {})]
    assert len(records(msgs)) == 1
    explicit = {"base_url": BASE, "streams": {"s": {
        **config["streams"]["s"], "pagination": {"strategy": "none"}}}}
    conn2, script2, _ = connector({SERIES: lambda p: {"rows": []}})
    read(conn2, explicit, ["s"])
    assert script2.calls == [(SERIES, {})]


# -- discover / check --------------------------------------------------------


def test_discover_declares_the_streams_offline():
    config = example("candles")
    config["streams"]["other"] = {
        "path": "/x", "effective_field": "ts", "schema": {"fields": ["ts", "v"]},
        "primary_key": ["ts"]}
    conn, script, _ = connector({})
    found = conn.discover(config)
    assert script.calls == []
    assert found == [
        {"stream": "candles-btc-usd",
         "schema": {"fields": ["time", "low", "high", "open", "close", "volume",
                               "time_iso"]},
         "primary_key": ["time"]},
        {"stream": "other", "schema": {"fields": ["ts", "v"]}, "primary_key": ["ts"]},
    ]
    bare = {"base_url": BASE, "streams": {"s": {"path": "/x", "effective_field": "ts"}}}
    assert conn.discover(bare) == [
        {"stream": "s", "schema": {"fields": []}, "primary_key": []}]


def test_check_makes_one_probe_of_the_first_stream_first_window():
    config = plain(pagination={"max_windows": 1})  # 3 windows needed: check never counts them
    config["streams"]["a"] = copy.deepcopy(config["streams"]["s"])
    config["streams"]["a"]["path"] = "/a"
    conn, script, _ = connector({"/a": empty_rows, SERIES: empty_rows})
    conn.check(config)
    assert script.calls == [("/a", {"from": "2026-01-01T00:00:00Z",
                                    "to": "2026-01-01T00:04:00Z"})]


def test_check_surfaces_a_bad_response_shape_and_a_failed_ping():
    conn, _, _ = connector({SERIES: lambda p: {"nope": 1}})
    with pytest.raises(AssetError, match="records_path 'rows'"):
        conn.check(plain())
    conn, _, _ = connector({SERIES: http_error(404)})
    with pytest.raises(AssetError, match="HTTP 404"):
        conn.check(plain())


def test_check_with_nothing_to_pull_makes_no_request():
    future = plain(pagination={"start": "2026-10-07T00:00:00Z", "end": None})
    conn, script, _ = connector({})
    conn.check(future)  # the span is empty: nothing to probe, nothing wrong
    assert script.calls == []


# -- the window arithmetic ----------------------------------------------------

WINDOWS = [("2026-01-01T00:00:00Z", "2026-01-01T00:04:00Z"),
           ("2026-01-01T00:04:00Z", "2026-01-01T00:08:00Z"),
           ("2026-01-01T00:08:00Z", "2026-01-01T00:10:00Z")]


@pytest.mark.parametrize("fmt,render", [
    ("iso", lambda text: text),
    ("epoch_s", lambda text: epoch_s(text)),
    ("epoch_ms", lambda text: epoch_s(text) * 1000),
])
def test_one_request_per_window_in_each_time_format(fmt, render):
    conn, script, sleeps = connector({SERIES: empty_rows})
    read(conn, plain(pagination={"time_format": fmt}), ["s"])
    assert script.params() == [{"from": render(lo), "to": render(hi)} for lo, hi in WINDOWS]
    assert sleeps == []  # pace_s defaults to 0


def test_default_time_format_is_iso_with_a_z_suffix():
    conn, script, _ = connector({SERIES: empty_rows})
    read(conn, plain(), ["s"])
    assert script.params()[0] == {"from": "2026-01-01T00:00:00Z", "to": "2026-01-01T00:04:00Z"}


def test_static_params_ride_on_every_request_beside_the_window():
    conn, script, _ = connector({SERIES: empty_rows})
    read(conn, plain(params={"granularity": 60, "pair": "x"}), ["s"])
    assert all(p["granularity"] == 60 and p["pair"] == "x" for p in script.params())
    assert [p["from"] for p in script.params()][1] == "2026-01-01T00:04:00Z"


def test_a_span_that_is_one_exact_multiple_has_no_empty_tail_window():
    conn, script, _ = connector({SERIES: empty_rows})
    read(conn, plain(pagination={"end": "2026-01-01T00:08:00Z"}), ["s"])
    assert len(script.calls) == 2
    assert script.params()[-1]["to"] == "2026-01-01T00:08:00Z"


def test_end_omitted_runs_to_the_clock_floored_to_the_second():
    conn, script, _ = connector({SERIES: empty_rows})
    config = plain(pagination={"start": "2026-10-06T11:59:00Z", "step": 60, "end": None})
    read(conn, config, ["s"])
    # NOW is 12:00:30.75: the open end is 12:00:30, never the fraction.
    assert script.params() == [
        {"from": "2026-10-06T11:59:00Z", "to": "2026-10-06T12:00:00Z"},
        {"from": "2026-10-06T12:00:00Z", "to": "2026-10-06T12:00:30Z"}]


def test_lag_pulls_the_open_end_back():
    conn, script, _ = connector({SERIES: empty_rows})
    config = plain(pagination={"start": "2026-10-06T11:59:00Z", "step": 60, "lag": 30,
                               "end": None})
    read(conn, config, ["s"])
    assert script.params() == [
        {"from": "2026-10-06T11:59:00Z", "to": "2026-10-06T12:00:00Z"}]


def test_clock_is_sampled_once_per_read():
    ticks = iter([NOW, NOW + timedelta(hours=5)])
    script = Script({SERIES: empty_rows})
    conn = RestWindowConnector(getter=script, sleeper=lambda s: None,
                               clock=lambda: next(ticks))
    config = plain(pagination={"start": "2026-10-06T11:59:00Z", "step": 60, "end": None})
    config["streams"]["t"] = copy.deepcopy(config["streams"]["s"])
    read(conn, config, ["s", "t"])
    assert len(script.calls) == 4  # both streams saw the SAME end


def test_a_start_after_the_end_of_the_span_makes_no_request():
    conn, script, _ = connector({})
    config = plain(pagination={"start": "2026-10-07T00:00:00Z", "end": None})
    msgs = read(conn, config, ["s"])
    assert script.calls == []
    assert [m["type"] for m in msgs] == ["SCHEMA", "STATE"]
    assert state_of(msgs) == {"s": {"cursor": ""}}


# -- the refuse-not-truncate cap ----------------------------------------------


def test_cap_refuses_before_any_request_and_names_the_numbers():
    conn, script, _ = connector({SERIES: empty_rows})
    with pytest.raises(AssetError) as exc:
        read(conn, plain(pagination={"max_windows": 2}), ["s"])
    assert script.calls == []
    text = str(exc.value)
    assert "3 windows" in text and "max_windows" in text and "2" in text
    assert "'s'" in text


def test_cap_exactly_met_is_not_a_refusal():
    conn, script, _ = connector({SERIES: empty_rows})
    read(conn, plain(pagination={"max_windows": 3}), ["s"])
    assert len(script.calls) == 3


def test_cap_is_checked_for_every_stream_before_the_first_request():
    config = plain()
    config["streams"]["z"] = copy.deepcopy(config["streams"]["s"])
    config["streams"]["z"]["pagination"]["max_windows"] = 1
    conn, script, _ = connector({SERIES: empty_rows})
    with pytest.raises(AssetError, match="stream 'z'"):
        read(conn, config, ["s", "z"])
    assert script.calls == []  # 's' was not pulled and abandoned


def test_cap_counts_the_windows_the_cursor_leaves_not_the_whole_history():
    conn, script, _ = connector({SERIES: empty_rows})
    state = {"s": {"cursor": "2026-01-01T00:07:00+00:00"}}
    read(conn, plain(pagination={"max_windows": 1}), ["s"], state=state)
    assert script.params() == [{"from": "2026-01-01T00:07:00Z", "to": "2026-01-01T00:10:00Z"}]


def test_default_cap_is_one_named_constant():
    assert isinstance(restwindow.DEFAULT_MAX_WINDOWS, int)
    conn, script, _ = connector({SERIES: empty_rows})
    config = plain(pagination={"step": 1, "end": "2026-01-02T00:00:00Z"})
    assert 86400 > restwindow.DEFAULT_MAX_WINDOWS
    with pytest.raises(AssetError) as exc:
        read(conn, config, ["s"])
    assert str(restwindow.DEFAULT_MAX_WINDOWS) in str(exc.value)
    assert script.calls == []
    # and a span of exactly the default is accepted
    exact = plain(pagination={"step": 1, "end": iso(at("2026-01-01T00:00:00Z")
                                                    + timedelta(seconds=restwindow.DEFAULT_MAX_WINDOWS))})
    conn2, script2, _ = connector({SERIES: empty_rows})
    read(conn2, exact, ["s"])
    assert len(script2.calls) == restwindow.DEFAULT_MAX_WINDOWS


def test_spec_states_the_defaults_from_their_constants():
    notes = json.dumps(RestWindowConnector().spec())
    for value in (restwindow.DEFAULT_MAX_WINDOWS, restwindow.DEFAULT_MAX_PAGES,
                  restwindow.DEFAULT_TIMEOUT_S, restwindow.DEFAULT_MAX_RETRIES):
        assert str(value) in notes


def test_defaults_are_read_from_their_names(monkeypatch):
    monkeypatch.setattr(restwindow, "DEFAULT_MAX_WINDOWS", 2)
    conn, script, _ = connector({SERIES: empty_rows})
    with pytest.raises(AssetError, match="max_windows"):
        read(conn, plain(), ["s"])  # 3 windows against a default of 2
    assert script.calls == []


# -- rows: clip, order, cursor -------------------------------------------------


def ticks(lo, hi, step=60):
    """Dict rows from ``lo`` to ``hi`` INCLUSIVE, newest first (a vendor's habit)."""
    out, t = [], at(lo)
    while t <= at(hi):
        out.append({"ts": iso(t), "v": int(t.timestamp()) % 1000})
        t += timedelta(seconds=step)
    return list(reversed(out))


def inclusive_server(params):
    return {"rows": ticks(params["from"], params["to"])}


def test_rows_are_clipped_to_their_window_so_a_shared_bound_is_emitted_once():
    conn, script, _ = connector({SERIES: inclusive_server})
    msgs = read(conn, plain(), ["s"])
    got = instants(msgs)
    # the vendor returned each shared bound (00:04, 00:08) in TWO windows
    assert got == [iso(at("2026-01-01T00:00:00Z") + timedelta(minutes=m)) for m in range(10)]
    assert len(got) == len(set(got))


def test_rows_outside_the_span_are_dropped_not_stored():
    def noisy(params):
        return {"rows": ticks("2025-12-31T23:58:00Z", "2026-01-01T00:12:00Z")}

    conn, _, _ = connector({SERIES: noisy})
    got = instants(read(conn, plain(), ["s"]))
    assert got[0] == "2026-01-01T00:00:00+00:00" and got[-1] == "2026-01-01T00:09:00+00:00"
    assert len(got) == 10


def test_emitted_ascending_and_the_cursor_is_the_max_instant():
    conn, _, _ = connector({SERIES: inclusive_server})
    msgs = read(conn, plain(), ["s"])
    got = instants(msgs)
    assert got == sorted(got)
    assert state_of(msgs) == {"s": {"cursor": "2026-01-01T00:09:00+00:00"}}
    assert [m["type"] for m in msgs][0] == "SCHEMA"
    assert msgs[-1]["type"] == "STATE"


def test_record_envelope_is_restapi_shaped():
    conn, _, _ = connector({SERIES: inclusive_server})
    msgs = read(conn, plain(), ["s"])
    first = records(msgs)[0]
    assert set(first) == {"protocol", "type", "stream", "effective_date", "kind", "data"}
    assert first["stream"] == "s" and first["kind"] == "observation"
    assert first["data"] == {"ts": "2026-01-01T00:00:00+00:00", "v": 1767225600 % 1000}
    assert msgs[0] == {"protocol": 1, "type": "SCHEMA", "stream": "s",
                       "schema": {"fields": ["ts", "v"]}}


def test_declared_schema_wins_over_the_first_row():
    conn, _, _ = connector({SERIES: inclusive_server})
    msgs = read(conn, plain(schema={"fields": ["ts"], "x": 1}), ["s"])
    assert msgs[0]["schema"] == {"fields": ["ts"], "x": 1}


def test_cursor_resumes_the_windows_and_never_re_emits():
    conn, script, _ = connector({SERIES: inclusive_server})
    state = {"s": {"cursor": "2026-01-01T00:05:30+00:00"}}
    msgs = read(conn, plain(), ["s"], state=state)
    assert script.params() == [
        {"from": "2026-01-01T00:05:30Z", "to": "2026-01-01T00:09:30Z"},
        {"from": "2026-01-01T00:09:30Z", "to": "2026-01-01T00:10:00Z"}]
    # the vendor's tick grid starts at each window's own lo (00:05:30), so the
    # cursor row itself (00:05:30) is the one the client filter drops
    assert instants(msgs) == ["2026-01-01T00:06:30+00:00", "2026-01-01T00:07:30+00:00",
                              "2026-01-01T00:08:30+00:00", "2026-01-01T00:09:30+00:00"]
    assert state_of(msgs)["s"]["cursor"] == "2026-01-01T00:09:30+00:00"


def test_cursor_at_or_past_the_end_makes_no_request_and_keeps_the_cursor():
    conn, script, _ = connector({})
    for cursor in ("2026-01-01T00:10:00+00:00", "2026-02-01T00:00:00+00:00"):
        msgs = read(conn, plain(), ["s"], state={"s": {"cursor": cursor}})
        assert [m["type"] for m in msgs] == ["SCHEMA", "STATE"]
        assert state_of(msgs) == {"s": {"cursor": cursor}}
    assert script.calls == []


def test_a_cursor_inside_the_span_with_nothing_new_does_not_regress():
    cursor = "2026-01-01T00:09:00+00:00"
    conn, script, _ = connector({SERIES: empty_rows})
    msgs = read(conn, plain(), ["s"], state={"s": {"cursor": cursor}})
    assert state_of(msgs) == {"s": {"cursor": cursor}}
    assert script.params() == [{"from": "2026-01-01T00:09:00Z", "to": "2026-01-01T00:10:00Z"}]


def test_other_streams_state_is_carried_through():
    conn, _, _ = connector({SERIES: empty_rows})
    state = {"elsewhere": {"cursor": "x", "keep": 1}}
    msgs = read(conn, plain(), ["s"], state=state)
    assert state_of(msgs)["elsewhere"] == {"cursor": "x", "keep": 1}
    assert state == {"elsewhere": {"cursor": "x", "keep": 1}}  # the input is not mutated


def test_a_cursor_that_is_not_an_instant_is_refused():
    conn, script, _ = connector({SERIES: empty_rows})
    with pytest.raises(AssetError, match="cursor"):
        read(conn, plain(), ["s"], state={"s": {"cursor": "tuesday"}})
    with pytest.raises(AssetError, match="cursor"):
        read(conn, plain(), ["s"], state={"s": {"cursor": 5}})
    assert script.calls == []


def test_an_undeclared_but_requested_stream_is_refused():
    conn, script, _ = connector({SERIES: empty_rows})
    with pytest.raises(AssetError, match="unknown stream 'nope'"):
        read(conn, plain(), ["nope"])
    assert script.calls == []


def test_read_validates_its_arguments():
    conn, _, _ = connector({SERIES: empty_rows})
    with pytest.raises(AssetError, match="streams must be a non-empty list"):
        list(conn.read(plain(), [], {}, "live"))
    with pytest.raises(AssetError, match="state"):
        list(conn.read(plain(), ["s"], [], "live"))
    with pytest.raises(AssetError, match="mode"):
        list(conn.read(plain(), ["s"], {}, "sideways"))
    with pytest.raises(AssetError, match="more than once"):
        list(conn.read(plain(), ["s", "s"], {}, "live"))
    with pytest.raises(AssetError, match="state.s"):
        list(conn.read(plain(), ["s"], {"s": 3}, "live"))


@pytest.mark.parametrize("mode", ["backfill", "live"])
def test_either_mode_reads_the_same(mode):
    conn, _, _ = connector({SERIES: inclusive_server})
    assert len(records(read(conn, plain(), ["s"], mode=mode))) == 10


# -- positional rows and the epoch effective instant --------------------------


def positional(**over):
    stream = {"path": SERIES, "records_path": "rows",
              "row_fields": ["t", "a", "b"], "effective_field": "when",
              "epoch": {"field": "t", "unit": "s"},
              "pagination": {"strategy": "none"}}
    stream.update(over)
    return {"base_url": BASE, "streams": {"s": stream}}


def serve(rows):
    return {SERIES: lambda p: {"rows": rows}}


def test_positional_rows_become_dicts_with_the_epoch_effective_instant():
    conn, _, _ = connector(serve([[1767225660, 2.5, "x"], [1767225600, 1.5, "y"]]))
    msgs = read(conn, positional(), ["s"])
    assert [m["data"] for m in records(msgs)] == [
        {"t": 1767225600, "a": 1.5, "b": "y", "when": "2026-01-01T00:00:00+00:00"},
        {"t": 1767225660, "a": 2.5, "b": "x", "when": "2026-01-01T00:01:00+00:00"}]
    assert instants(msgs) == ["2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00"]
    assert msgs[0]["schema"] == {"fields": ["t", "a", "b", "when"]}


@pytest.mark.parametrize("unit,value,expected", [
    ("s", 1767225600, "2026-01-01T00:00:00+00:00"),
    ("ms", 1767225600000, "2026-01-01T00:00:00+00:00"),
    ("ms", 1767225600123, "2026-01-01T00:00:00.123000+00:00"),
    ("s", 1767225600.25, "2026-01-01T00:00:00.250000+00:00"),
    ("s", 1704067200.0004199, "2024-01-01T00:00:00.000420+00:00"),
    ("s", 0, "1970-01-01T00:00:00+00:00"),
    ("ms", 1767225600999.0, "2026-01-01T00:00:00.999000+00:00"),
])
def test_epoch_units_and_fractions(unit, value, expected):
    conn, _, _ = connector(serve([[value, 1, 2]]))
    config = positional(epoch={"field": "t", "unit": unit})
    (rec,) = records(read(conn, config, ["s"]))
    assert rec["effective_date"] == expected
    assert rec["data"]["when"] == expected
    assert rec["data"]["t"] == value  # the vendor's own value is kept


@pytest.mark.parametrize("bad", [True, "1767225600", None, float("nan"), float("inf"),
                                 [1], 1e30])
def test_a_non_epoch_value_is_refused_naming_the_record(bad):
    conn, _, _ = connector(serve([[1767225600, 1, 2], [bad, 1, 2]]))
    with pytest.raises(AssetError, match=r"stream 's': record 1: .*epoch"):
        read(conn, positional(), ["s"])


def test_the_effective_field_may_not_overwrite_a_vendor_field():
    conn, _, _ = connector(serve([{"t": 1767225600, "when": "vendor"}]))
    config = positional()
    del config["streams"]["s"]["row_fields"]  # dict rows now
    with pytest.raises(AssetError, match="already holds 'when'"):
        read(conn, config, ["s"])


def test_dict_rows_with_an_epoch_field():
    conn, _, _ = connector(serve([{"t": 1767225600, "k": 1}]))
    config = positional()
    del config["streams"]["s"]["row_fields"]
    (rec,) = records(read(conn, config, ["s"]))
    assert rec["data"] == {"t": 1767225600, "k": 1, "when": "2026-01-01T00:00:00+00:00"}


def test_a_missing_epoch_field_is_refused():
    conn, _, _ = connector(serve([{"k": 1}]))
    config = positional()
    del config["streams"]["s"]["row_fields"]
    with pytest.raises(AssetError, match="record 0: .*'t'"):
        read(conn, config, ["s"])


def test_array_rows_without_row_fields_are_refused():
    conn, _, _ = connector(serve([[1767225600, 1, 2]]))
    config = positional()
    del config["streams"]["s"]["row_fields"]
    with pytest.raises(AssetError, match=r"record 0 is not an object.*row_fields"):
        read(conn, config, ["s"])


def test_object_rows_with_row_fields_are_refused():
    conn, _, _ = connector(serve([{"t": 1767225600, "a": 1, "b": 2}]))
    with pytest.raises(AssetError, match=r"record 0 is not an array.*row_fields"):
        read(conn, positional(), ["s"])


@pytest.mark.parametrize("row", [[1767225600, 1], [1767225600, 1, 2, 3]])
def test_a_row_of_the_wrong_length_is_refused_not_truncated(row):
    conn, _, _ = connector(serve([[1767225600, 1, 2], row]))
    with pytest.raises(AssetError, match=r"record 1 has \d items?, row_fields names 3"):
        read(conn, positional(), ["s"])


def test_iso_effective_field_must_be_a_parseable_instant():
    for rows, fragment in (
            ([{"ts": "not a date"}], "not an ISO"),
            ([{"ts": ""}], "missing or empty"),
            ([{"ts": 5}], "missing or empty"),
            ([{"other": "2026-01-01T00:00:00Z"}], "missing or empty")):
        conn, _, _ = connector(serve(rows))
        config = {"base_url": BASE, "streams": {"s": {
            "path": SERIES, "records_path": "rows", "effective_field": "ts"}}}
        with pytest.raises(AssetError, match=fragment) as exc:
            read(conn, config, ["s"])
        assert "stream 's': record 0" in str(exc.value)


def test_iso_rows_keep_the_vendors_own_effective_string():
    conn, _, _ = connector(serve([{"ts": "2026-01-01T00:00:00Z", "v": 1}]))
    config = {"base_url": BASE, "streams": {"s": {
        "path": SERIES, "records_path": "rows", "effective_field": "ts"}}}
    msgs = read(conn, config, ["s"])
    assert records(msgs)[0]["effective_date"] == "2026-01-01T00:00:00Z"
    assert state_of(msgs) == {"s": {"cursor": "2026-01-01T00:00:00Z"}}


def test_the_decoded_rows_are_not_mutated_in_place():
    body = {"rows": [{"t": 1767225600, "k": 1}]}
    conn, _, _ = connector({SERIES: lambda p: body})
    config = positional()
    del config["streams"]["s"]["row_fields"]
    read(conn, config, ["s"])
    assert body == {"rows": [{"t": 1767225600, "k": 1}]}


# -- records_path: literal, absent, and the single '*' -------------------------

KRAKEN_BODY = {"error": [], "result": {
    "XXBTZUSD": [[1767225600, 1, 2]], "last": "123"}}


def star(path="result.*", **over):
    return positional(records_path=path, **over)


def test_star_selects_the_one_key_holding_a_list():
    conn, _, _ = connector({SERIES: lambda p: KRAKEN_BODY})
    (rec,) = records(read(conn, star(), ["s"]))
    assert rec["data"]["t"] == 1767225600
    other = {"result": {"last": "9", "SOMEPAIR": [[1767225600, 3, 4]]}}
    conn2, _, _ = connector({SERIES: lambda p: other})
    (rec2,) = records(read(conn2, star(), ["s"]))
    assert rec2["data"]["a"] == 3  # the key's NAME never mattered


def test_star_with_an_empty_list_is_an_empty_page_not_a_refusal():
    conn, _, _ = connector({SERIES: lambda p: {"result": {"X": [], "last": "1"}}})
    assert records(read(conn, star(), ["s"])) == []


def test_star_with_no_list_is_refused():
    conn, _, _ = connector({SERIES: lambda p: {"result": {"last": "1"}}})
    with pytest.raises(AssetError, match=r"'\*' matched no list"):
        read(conn, star(), ["s"])


def test_star_with_several_lists_is_refused_naming_them():
    body = {"result": {"A": [], "B": [], "last": "1"}}
    conn, _, _ = connector({SERIES: lambda p: body})
    with pytest.raises(AssetError, match=r"'\*' matched 2 lists.*'A'.*'B'"):
        read(conn, star(), ["s"])


def test_star_under_a_non_object_is_refused():
    conn, _, _ = connector({SERIES: lambda p: {"result": [[1767225600, 1, 2]]}})
    with pytest.raises(AssetError, match="does not lead to a list"):
        read(conn, star("result.*"), ["s"])
    conn2, _, _ = connector({SERIES: lambda p: {"other": {}}})
    with pytest.raises(AssetError, match="does not lead to a list"):
        read(conn2, star("result.*"), ["s"])


def test_star_alone_selects_from_the_body_itself():
    conn, _, _ = connector({SERIES: lambda p: {"pair": [[1767225600, 1, 2]], "n": 1}})
    assert len(records(read(conn, star("*"), ["s"]))) == 1


def test_literal_path_missing_or_not_a_list_is_refused():
    for body in ({"rows": {"a": 1}}, {"x": []}, {"rows": "no"}):
        conn, _, _ = connector({SERIES: lambda p, b=body: b})
        with pytest.raises(AssetError, match="records_path 'rows' does not lead to a list"):
            read(conn, plain(), ["s"])


def test_no_records_path_means_the_body_is_the_list():
    config = positional()
    del config["streams"]["s"]["records_path"]
    conn, _, _ = connector({SERIES: lambda p: [[1767225600, 1, 2]]})
    assert len(records(read(conn, config, ["s"]))) == 1
    conn2, _, _ = connector({SERIES: lambda p: {"rows": []}})
    with pytest.raises(AssetError, match="response body is not a list"):
        read(conn2, config, ["s"])


# -- cursor pagination (a trades-shaped walk) ----------------------------------

LEDGER = [  # (ns, price, volume, side, type, misc, trade id)
    (1767225600250000000, "10.0", "1", "b", "m", "", 1),
    (1767225600500000000, "10.5", "2", "s", "l", "", 2),
    (1767225601750000000, "11.0", "3", "b", "m", "", 3),
    (1767225603000000000, "11.5", "4", "s", "m", "", 4),
    (1767225605250000000, "12.0", "5", "b", "l", "", 5),
]
N = [row[0] for row in LEDGER]  # the vendor's own tokens, one per trade


def trades_server(ledger):
    """A trades endpoint as probed: ``since`` is epoch seconds (small, exclusive) or a
    nanosecond token (INCLUSIVE: the final token returns the final trade again);
    ``count`` caps the page; ``last`` is the final row's token, or ``since`` echoed."""

    def handler(params):
        since = params.get("since")
        if since is None:
            fresh = list(ledger)
        elif int(since) < 10**12:
            fresh = [row for row in ledger if row[0] > int(since) * 10**9]
        else:
            fresh = [row for row in ledger if row[0] >= int(since)]
        fresh = fresh[:params.get("count", 1000)]
        rows = [[r[1], r[2], r[0] // 10**9 + (r[0] % 10**9) / 1e9, r[3], r[4], r[5], r[6]]
                for r in fresh]
        last = str(fresh[-1][0]) if fresh else str(since)
        return {"error": [], "result": {"XXBTZUSD": rows, "last": last}}

    return handler


def trades_config(**pagination):
    """The shipped trades example; a ``None`` value removes that pagination key."""
    config = example("trades")
    knobs = config["streams"]["trades-xbtusd"]["pagination"]
    knobs.update(pagination)
    for key in [k for k, v in knobs.items() if v is None]:
        del knobs[key]
    return config


TRADES = "/0/public/Trades"
NO_SHORT_RULE = {"page_size": None, "size_param": None}


def test_trades_walk_pages_by_token_until_a_short_page():
    conn, script, _ = connector({TRADES: trades_server(LEDGER)})
    msgs = read(conn, trades_config(page_size=2), ["trades-xbtusd"])
    sent = [p.get("since") for p in script.params()]
    assert sent == [epoch_s("2024-01-01T00:00:00Z"), str(N[1]), str(N[2]), str(N[3]), str(N[4])]
    assert all(p["pair"] == "XBTUSD" and p["count"] == 2 for p in script.params())
    got = records(msgs)
    assert [m["data"]["trade_id"] for m in got] == [1, 2, 3, 4, 5]
    assert got[0]["effective_date"] == "2026-01-01T00:00:00.250000+00:00"
    assert got[0]["data"] == {
        "price": "10.0", "volume": "1", "time": 1767225600.25, "side": "b", "type": "m",
        "misc": "", "trade_id": 1, "time_iso": "2026-01-01T00:00:00.250000+00:00"}
    assert state_of(msgs) == {"trades-xbtusd": {"cursor": "2026-01-01T00:00:05.250000+00:00"}}


def test_a_short_first_page_is_the_whole_walk_at_the_head_of_the_data():
    # as probed: 15 trades, well under a full page of 1000 -- ONE request, and the
    # echoed token is never chased (the vendor would answer it with the last trade)
    conn, script, _ = connector({TRADES: trades_server(LEDGER)})
    msgs = read(conn, trades_config(), ["trades-xbtusd"])
    assert len(script.calls) == 1 and script.params()[0]["count"] == 1000
    assert [m["data"]["trade_id"] for m in records(msgs)] == [1, 2, 3, 4, 5]


def test_an_inclusive_token_repeats_the_boundary_row_and_the_repeat_is_dropped():
    conn, script, _ = connector({TRADES: trades_server(LEDGER)})
    msgs = read(conn, trades_config(page_size=2), ["trades-xbtusd"])
    assert len(script.calls) == 5  # every later page began AT the previous page's last row
    assert [m["data"]["trade_id"] for m in records(msgs)] == [1, 2, 3, 4, 5]


def test_only_a_repeat_from_the_page_just_before_is_dropped():
    twin = ["1", "1", 1767225600.5, "b", "m", "", 9]
    pages = iter([
        {"result": {"X": [twin, twin], "last": "t1"}},   # twins within ONE page: both kept
        {"result": {"X": [["2", "1", 1767225601.5, "b", "m", "", 10],
                          ["3", "1", 1767225602.5, "b", "m", "", 11]], "last": "t2"}},
        {"result": {"X": [twin, ["4", "1", 1767225603.5, "b", "m", "", 12]], "last": "t3"}},
        {"result": {"X": [], "last": "t3"}},
    ])
    conn, _, _ = connector({TRADES: lambda p: next(pages)})
    msgs = read(conn, trades_config(page_size=2, max_pages=10), ["trades-xbtusd"])
    ids = sorted(m["data"]["trade_id"] for m in records(msgs))
    # the twin on page 3 equals a row two pages back, not the page just before: kept
    assert ids == [9, 9, 9, 10, 11, 12]


def test_trades_resume_from_the_cursor_rounded_down_to_the_second():
    conn, script, _ = connector({TRADES: trades_server(LEDGER)})
    state = {"trades-xbtusd": {"cursor": "2026-01-01T00:00:01.750000+00:00"}}
    msgs = read(conn, trades_config(page_size=2), ["trades-xbtusd"], state=state)
    assert script.params()[0]["since"] == 1767225601  # floor, so no trade is skipped
    assert [m["data"]["trade_id"] for m in records(msgs)] == [4, 5]  # 3 is the cursor itself


def test_the_later_of_start_and_the_cursor_is_where_a_cursor_walk_begins():
    conn, script, _ = connector({TRADES: trades_server(LEDGER)})
    behind = {"trades-xbtusd": {"cursor": "2020-01-01T00:00:00+00:00"}}
    read(conn, trades_config(), ["trades-xbtusd"], state=behind)
    assert script.params()[0]["since"] == epoch_s("2024-01-01T00:00:00Z")


def test_a_cursor_walk_with_no_start_sends_no_token_first():
    conn, script, _ = connector({TRADES: trades_server(LEDGER)})
    read(conn, trades_config(start=None, page_size=5), ["trades-xbtusd"])
    assert "since" not in script.params()[0]
    assert script.params()[1]["since"] == str(N[4])  # the first page was full: chase the token


def test_a_cursor_walk_with_no_time_format_never_renders_an_initial_token():
    conn, script, _ = connector({TRADES: trades_server(LEDGER)})
    state = {"trades-xbtusd": {"cursor": "2026-01-01T00:00:01.750000+00:00"}}
    msgs = read(conn, trades_config(start=None, time_format=None, page_size=5),
                ["trades-xbtusd"], state=state)
    assert "since" not in script.params()[0]
    assert [m["data"]["trade_id"] for m in records(msgs)] == [4, 5]  # the client filter held


def test_a_cursor_walk_stops_when_the_token_is_absent_or_empty():
    for last in (None, ""):
        body = {"result": {"X": [["1", "1", 1767225600.5, "b", "m", "", 9]], "last": last}}
        if last is None:
            del body["result"]["last"]
        conn, script, _ = connector({TRADES: lambda p, b=body: b})
        msgs = read(conn, trades_config(**NO_SHORT_RULE), ["trades-xbtusd"])
        assert len(script.calls) == 1 and len(records(msgs)) == 1


def test_a_token_that_does_not_advance_while_rows_arrive_is_refused():
    def stuck(params):
        return {"result": {"X": [["1", "1", 1767225600.5, "b", "m", "", 9]], "last": "same"}}

    conn, script, _ = connector({TRADES: stuck})
    with pytest.raises(AssetError, match="did not advance"):
        read(conn, trades_config(**NO_SHORT_RULE), ["trades-xbtusd"])
    assert len(script.calls) == 2  # the repeat is caught on the page that shows it
    # a FULL page (one row against a page_size of 1) with the same token is a stuck server
    conn, script, _ = connector({TRADES: stuck})
    with pytest.raises(AssetError, match="did not advance"):
        read(conn, trades_config(page_size=1), ["trades-xbtusd"])
    assert len(script.calls) == 2


def test_a_server_that_ignores_the_cursor_param_is_refused_not_half_read():
    # a misspelled `param`: every request returns the SAME full first page and token
    def ignores(params):
        return trades_server(LEDGER)({"count": params["count"]})

    conn, script, _ = connector({TRADES: ignores})
    with pytest.raises(AssetError, match="did not advance"):
        read(conn, trades_config(page_size=2, param="sinse"), ["trades-xbtusd"])
    assert len(script.calls) == 2  # the second page shows the same token as the first


def test_a_token_cycle_is_refused():
    tokens = iter(["a", "b", "a"])

    def cycle(params):
        return {"result": {"X": [["1", "1", 1767225600.5, "b", "m", "", 9]],
                           "last": next(tokens)}}

    conn, script, _ = connector({TRADES: cycle})
    with pytest.raises(AssetError, match="did not advance"):
        read(conn, trades_config(**NO_SHORT_RULE), ["trades-xbtusd"])
    assert len(script.calls) == 3


def test_the_page_cap_refuses_instead_of_truncating():
    counter = iter(range(1, 100))

    def endless(params):
        n = next(counter)
        return {"result": {"X": [["1", "1", 1767225600 + n, "b", "m", "", n]],
                           "last": f"t{n}"}}

    conn, script, _ = connector({TRADES: endless})
    with pytest.raises(AssetError, match=r"max_pages.*3") as exc:
        read(conn, trades_config(max_pages=3, **NO_SHORT_RULE), ["trades-xbtusd"])
    assert "more to fetch" in str(exc.value)
    assert len(script.calls) == 3


def test_the_page_cap_met_exactly_with_nothing_left_is_not_a_refusal():
    conn, script, _ = connector({TRADES: trades_server(LEDGER[:2])})
    msgs = read(conn, trades_config(max_pages=2, page_size=2), ["trades-xbtusd"])
    assert len(script.calls) == 2 and len(records(msgs)) == 2


def test_default_page_cap_is_one_named_constant(monkeypatch):
    monkeypatch.setattr(restwindow, "DEFAULT_MAX_PAGES", 1)
    conn, script, _ = connector({TRADES: trades_server(LEDGER)})
    with pytest.raises(AssetError, match="max_pages"):
        read(conn, trades_config(max_pages=None, page_size=2), ["trades-xbtusd"])
    assert len(script.calls) == 1


@pytest.mark.parametrize("token", [["a"], {"a": 1}, True])
def test_a_token_that_is_not_a_scalar_is_refused(token):
    body = {"result": {"X": [["1", "1", 1767225600.5, "b", "m", "", 9]]}, "next": token}
    conn, _, _ = connector({TRADES: lambda p: body})
    with pytest.raises(AssetError, match="token"):
        read(conn, trades_config(path="next", **NO_SHORT_RULE), ["trades-xbtusd"])


def test_a_numeric_token_walks_too():
    pages = iter([
        {"r": {"X": [[1767225600, 1, 2]], "n": 7}},
        {"r": {"X": [], "n": 7}},
    ])
    config = positional(records_path="r.*",
                        pagination={"strategy": "cursor", "path": "r.n", "param": "after"})
    conn, script, _ = connector({SERIES: lambda p: next(pages)})
    msgs = read(conn, config, ["s"])
    assert script.params() == [{}, {"after": 7}]
    assert len(records(msgs)) == 1


# -- a truncation marker ------------------------------------------------------


def dvol_server(marker=None, per_window=None):
    def handler(params):
        lo, hi = params["start_timestamp"], params["end_timestamp"]
        rows = [[t, 36.0, 36.5, 35.5, 36.2] for t in range(lo, hi + 1, 60000)]
        if per_window is not None:
            rows = rows[:per_window]
        return {"jsonrpc": "2.0", "result": {"data": rows, "continuation": marker}}

    return handler


DVOL = "/api/v2/public/get_volatility_index_data"


def dvol_config(**pagination):
    config = example("index-series")
    config["streams"]["index-btc-60s"]["pagination"].update(pagination)
    return config


def test_a_marker_that_says_the_vendor_cut_the_range_short_is_refused():
    conn, _, _ = connector({DVOL: dvol_server(marker=1704067199000)},
                           now=at("2024-01-01T02:00:30Z"))
    with pytest.raises(AssetError, match="truncated") as exc:
        read(conn, dvol_config(), ["index-btc-60s"])
    assert "result.continuation" in str(exc.value)
    assert "window 1/1" in str(exc.value)


@pytest.mark.parametrize("marker", [None, False, "", [], {}])
def test_an_absent_or_empty_marker_is_not_a_truncation(marker):
    conn, _, _ = connector({DVOL: dvol_server(marker=marker)},
                           now=at("2024-01-01T02:00:30Z"))
    msgs = read(conn, dvol_config(), ["index-btc-60s"])
    assert len(records(msgs)) == 120  # 00:00 .. 01:59 (now 02:00:30, lag 60 -> end 01:59:30)


def test_without_truncated_path_nothing_is_inferred_from_the_row_count():
    config = dvol_config()
    del config["streams"]["index-btc-60s"]["pagination"]["truncated_path"]
    conn, _, _ = connector({DVOL: dvol_server(marker=5)}, now=at("2024-01-01T02:00:30Z"))
    assert len(records(read(conn, config, ["index-btc-60s"]))) == 120


# -- the worked examples, end to end -------------------------------------------

CANDLES = "/products/BTC-USD/candles"


def candles_server(params):
    """Candles newest first, both bounds INCLUSIVE, [time, low, high, open, close, volume]."""
    lo, hi = at(params["start"]), at(params["end"])
    out = []
    t = lo
    while t <= hi:
        s = int(t.timestamp())
        out.append([s, s % 97, s % 97 + 2, s % 97 + 1, s % 97 + 1.5, round(s % 7 / 3, 6)])
        t += timedelta(seconds=params["granularity"])
    return list(reversed(out))


def test_candles_example_one_window_with_the_vendor_inclusive_end():
    now = at("2024-01-01T00:20:30.5Z")
    conn, script, _ = connector({CANDLES: candles_server}, now=now)
    msgs = read(conn, example("candles"), ["candles-btc-usd"])
    assert script.calls == [(CANDLES, {
        "granularity": 60, "start": "2024-01-01T00:00:00Z", "end": "2024-01-01T00:19:30Z"})]
    got = records(msgs)
    # the candle opened 00:19:00 closed at 00:20:00 <= now: stored. 00:20:00 is forming: not.
    assert [m["effective_date"] for m in got][0] == "2024-01-01T00:00:00+00:00"
    assert [m["effective_date"] for m in got][-1] == "2024-01-01T00:19:00+00:00"
    assert len(got) == 20
    first = got[0]["data"]
    assert first["time"] == 1704067200 and first["time_iso"] == "2024-01-01T00:00:00+00:00"
    assert set(first) == {"time", "low", "high", "open", "close", "volume", "time_iso"}
    assert msgs[0]["schema"] == {"fields": ["time", "low", "high", "open", "close", "volume",
                                            "time_iso"]}


def test_candles_example_the_forming_candle_is_stored_only_without_lag():
    now = at("2024-01-01T00:20:30Z")
    with_lag, _, _ = connector({CANDLES: candles_server}, now=now)
    kept = instants(read(with_lag, example("candles"), ["candles-btc-usd"]))
    assert "2024-01-01T00:20:00+00:00" not in kept
    config = example("candles")
    config["streams"]["candles-btc-usd"]["pagination"]["lag"] = 0
    no_lag, _, _ = connector({CANDLES: candles_server}, now=now)
    forming = instants(read(no_lag, config, ["candles-btc-usd"]))
    assert forming[-1] == "2024-01-01T00:20:00+00:00"  # what `lag` exists to prevent


def test_candles_example_many_windows_store_each_candle_exactly_once():
    config = example("candles")
    config["streams"]["candles-btc-usd"]["pagination"]["step"] = 300
    now = at("2024-01-01T01:00:00Z")
    conn, script, _ = connector({CANDLES: candles_server}, now=now)
    msgs = read(conn, config, ["candles-btc-usd"])
    got = instants(msgs)
    assert len(script.calls) == 12 and len(got) == len(set(got)) == 59  # lag 60: 00:00-00:58
    assert got == sorted(got)
    assert got[0] == "2024-01-01T00:00:00+00:00" and got[-1] == "2024-01-01T00:58:00+00:00"
    gaps = {(at(b) - at(a)).total_seconds() for a, b in zip(got, got[1:])}
    assert gaps == {60.0}  # no candle lost at a window seam


def test_candles_example_an_incremental_pull_adds_only_the_new_candles():
    config = example("candles")
    config["streams"]["candles-btc-usd"]["pagination"]["step"] = 600
    first, _, _ = connector({CANDLES: candles_server}, now=at("2024-01-01T00:30:30Z"))
    first_msgs = read(first, config, ["candles-btc-usd"])
    state = state_of(first_msgs)
    assert state == {"candles-btc-usd": {"cursor": "2024-01-01T00:29:00+00:00"}}
    second, script, _ = connector({CANDLES: candles_server}, now=at("2024-01-01T00:40:30Z"))
    second_msgs = read(second, config, ["candles-btc-usd"], state=state)
    assert script.params()[0]["start"] == "2024-01-01T00:29:00Z"  # the cursor, not 2024-01-01
    assert instants(second_msgs)[0] == "2024-01-01T00:30:00+00:00"
    assert instants(second_msgs)[-1] == "2024-01-01T00:39:00+00:00"
    assert len(instants(second_msgs)) == 10


def test_index_series_example_epoch_ms_windows_and_rows():
    conn, script, _ = connector({DVOL: dvol_server()}, now=at("2024-01-01T01:30:30Z"))
    msgs = read(conn, example("index-series"), ["index-btc-60s"])
    (call,) = script.calls
    assert call == (DVOL, {"currency": "BTC", "resolution": "60",
                           "start_timestamp": 1704067200000, "end_timestamp": 1704072570000})
    got = records(msgs)
    assert len(got) == 90 and got[0]["effective_date"] == "2024-01-01T00:00:00+00:00"
    assert got[0]["data"] == {"ts": 1704067200000, "open": 36.0, "high": 36.5, "low": 35.5,
                              "close": 36.2, "ts_iso": "2024-01-01T00:00:00+00:00"}


def test_trades_example_state_round_trip_through_a_second_pull():
    conn, script, _ = connector({TRADES: trades_server(LEDGER)})
    first = read(conn, example("trades"), ["trades-xbtusd"])
    state = state_of(first)
    more = LEDGER + [(1767225610500000000, "13.0", "6", "b", "m", "", 6)]
    conn2, script2, _ = connector({TRADES: trades_server(more)})
    second = read(conn2, example("trades"), ["trades-xbtusd"], state=state)
    assert [m["data"]["trade_id"] for m in records(second)] == [6]
    assert script2.params()[0]["since"] == 1767225605  # the cursor's second
    assert script2.params()[0]["count"] == 1000


# -- transport: retry, pacing, errors -------------------------------------------


def one_window(**pagination):
    return plain(pagination={"end": "2026-01-01T00:04:00Z", **pagination})


def test_retry_on_429_honors_retry_after_then_succeeds():
    conn, script, sleeps = connector({SERIES: [http_error(429, {"Retry-After": "3"}),
                                               {"rows": []}]})
    read(conn, one_window(), ["s"])
    assert len(script.calls) == 2 and sleeps == [3.0]
    assert script.calls[0] == script.calls[1]  # the SAME window is retried


def test_retry_backs_off_exponentially_on_5xx_and_network_errors():
    conn, script, sleeps = connector({SERIES: [
        http_error(503), urllib.error.URLError("connection refused"),
        http_error(500, {"Retry-After": "soon"}), {"rows": []}]})
    read(conn, one_window(), ["s"])
    assert len(script.calls) == 4
    assert sleeps == [0.5, 1.0, 2.0]  # a non-numeric Retry-After falls back


def test_retry_after_is_capped_at_max_backoff():
    conn, script, sleeps = connector({SERIES: [
        http_error(429, {"Retry-After": "100000"}), {"rows": []}]})
    read(conn, one_window(), ["s"])
    assert sleeps == [MAX_BACKOFF_S]


def test_exponential_backoff_is_capped_at_max_backoff():
    conn, script, sleeps = connector({SERIES: (
        [http_error(503)] * 8 + [urllib.error.URLError("reset"), {"rows": []}])})
    read(conn, {**one_window(), "max_retries": 9}, ["s"])
    assert len(script.calls) == 10
    assert sleeps == [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, MAX_BACKOFF_S, MAX_BACKOFF_S]


def test_retries_exhausted_names_the_stream_the_window_and_the_url_without_its_query():
    conn, script, sleeps = connector({SERIES: [http_error(429)] * 3})
    with pytest.raises(AssetError, match=r"giving up.*after 2 attempt") as exc:
        read(conn, {**one_window(), "max_retries": 1}, ["s"])
    text = str(exc.value)
    assert "HTTP 429" in text and "stream 's'" in text and "window 1/1" in text
    assert f"{BASE}{SERIES}" in text and "from=" not in text  # the query never leaks
    assert len(script.calls) == 2 and sleeps == [0.5]

    conn, script, _ = connector({SERIES: [urllib.error.URLError("dns")]})
    with pytest.raises(AssetError, match="network error"):
        read(conn, {**one_window(), "max_retries": 0}, ["s"])
    assert len(script.calls) == 1


def test_a_truncated_or_dropped_response_is_retried_like_a_network_error():
    import http.client

    conn, script, sleeps = connector({SERIES: [
        http.client.IncompleteRead(b"par"), ConnectionResetError("reset"),
        http.client.RemoteDisconnected("closed"), {"rows": []}]})
    read(conn, one_window(), ["s"])
    assert len(script.calls) == 4 and sleeps == [0.5, 1.0, 2.0]


def test_a_client_error_is_not_retried():
    conn, script, sleeps = connector({SERIES: [http_error(404), {"rows": []}]})
    with pytest.raises(AssetError, match="HTTP 404"):
        read(conn, one_window(), ["s"])
    assert len(script.calls) == 1 and sleeps == []


def test_a_non_json_body_is_refused_not_retried():
    conn, script, sleeps = connector({SERIES: ValueError("Expecting value")})
    with pytest.raises(AssetError, match="not JSON"):
        read(conn, one_window(), ["s"])
    assert len(script.calls) == 1 and sleeps == []


def test_a_failed_window_abandons_the_pull_with_nothing_emitted():
    # rows of window 1 were fetched, window 2 fails: no STATE is ever yielded,
    # so the platform persists no checkpoint for a half pull.
    conn, _, _ = connector({SERIES: [{"rows": ticks("2026-01-01T00:00:00Z",
                                                    "2026-01-01T00:03:00Z")},
                                     http_error(404)]})
    seen = []
    with pytest.raises(AssetError, match="HTTP 404"):
        for msg in conn.read(plain(), ["s"], {}, "backfill"):
            seen.append(msg["type"])
    assert "STATE" not in seen and "RECORD" not in seen


def test_pacing_sleeps_between_requests_only():
    conn, script, sleeps = connector({SERIES: empty_rows})
    read(conn, {**plain(), "pace_s": 0.25}, ["s"])
    assert len(script.calls) == 3 and sleeps == [0.25, 0.25]  # never before the first
    conn, _, sleeps = connector({SERIES: empty_rows})
    read(conn, {**plain(), "pace_s": 0}, ["s"])
    assert sleeps == []


def test_the_getter_receives_the_url_and_a_params_dict_with_no_query_in_the_url():
    seen = []

    def getter(url, params):
        seen.append((url, dict(params)))
        return {"rows": []}

    conn = RestWindowConnector(getter=getter, sleeper=lambda s: None, clock=lambda: NOW)
    config = plain()
    config["base_url"] = BASE + "/"
    config["streams"]["s"]["path"] = "series"
    read(conn, config, ["s"])
    assert seen[0] == (f"{BASE}/series", {"from": "2026-01-01T00:00:00Z",
                                         "to": "2026-01-01T00:04:00Z"})


# -- the default transport and restapi parity, over a loopback server ---------------


class Loopback:
    """A stdlib HTTP server on 127.0.0.1 serving ``routes[path](query) -> (status, body)``."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                path, _, query = self.path.partition("?")
                outer.requests.append(
                    (path, query, {k.lower(): v for k, v in self.headers.items()}))
                status, body = outer.routes[path](query)
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        return False


ITEMS = {"items": [{"ts": "2026-01-03T00:00:00Z", "v": 3}, {"ts": "2026-01-01T00:00:00Z", "v": 1},
                   {"ts": "2026-01-02T00:00:00Z", "v": 2}]}


def test_default_transport_is_stdlib_urllib_with_a_user_agent_and_retries():
    attempts = []

    def flaky(query):
        attempts.append(query)
        if len(attempts) == 1:
            return 503, {"error": "later"}
        return 200, {"rows": [{"ts": "2026-01-01T00:00:00Z"}]}

    with Loopback({SERIES: flaky}) as server:
        conn = RestWindowConnector(sleeper=lambda s: None, clock=lambda: NOW)
        config = {**plain(pagination={"end": "2026-01-01T00:04:00Z"}), "base_url": server.url}
        msgs = read(conn, config, ["s"])
        assert len(records(msgs)) == 1
        assert len(server.requests) == 2
        assert server.requests[0][1] == "from=2026-01-01T00%3A00%3A00Z&to=2026-01-01T00%3A04%3A00Z"
        assert server.requests[0][2]["user-agent"] == "dskit-onboarding"
        assert server.requests[0][2]["accept"] == "application/json"


def test_default_transport_refuses_a_client_error_and_non_json():
    with Loopback({SERIES: lambda q: (404, {"message": "no"}),
                   "/junk": lambda q: (200, b"<html>")}) as server:
        conn = RestWindowConnector(sleeper=lambda s: None, clock=lambda: NOW)
        config = {**plain(pagination={"end": "2026-01-01T00:04:00Z"}), "base_url": server.url}
        with pytest.raises(AssetError, match="HTTP 404"):
            read(conn, config, ["s"])
        config["streams"]["s"]["path"] = "/junk"
        with pytest.raises(AssetError, match="not JSON"):
            read(conn, config, ["s"])


def test_a_plain_dict_row_stream_gives_the_rows_restapi_gives():
    with Loopback({"/items": lambda q: (200, ITEMS)}) as server:
        old = {"base_url": server.url, "effective_field": "ts", "streams": {
            "s": {"path": "/items", "records_path": "items", "primary_key": ["ts"]}}}
        new = {"base_url": server.url, "streams": {"s": {
            "path": "/items", "records_path": "items", "primary_key": ["ts"],
            "effective_field": "ts"}}}
        first_old = list(RestApiConnector().read(old, ["s"], {}, "backfill"))
        first_new = list(RestWindowConnector().read(new, ["s"], {}, "backfill"))
        assert first_new == first_old and len(records(first_new)) == 3
        # and again from the checkpoint the first pull produced
        state = first_old[-1]["state"]
        again_old = list(RestApiConnector().read(old, ["s"], state, "backfill"))
        again_new = list(RestWindowConnector().read(new, ["s"], state, "backfill"))
        assert again_new == again_old and records(again_new) == []
        assert RestWindowConnector().discover(new) == RestApiConnector().discover(old)
        # a half-way checkpoint
        half = {"s": {"cursor": "2026-01-02T00:00:00Z"}}
        assert (list(RestWindowConnector().read(new, ["s"], half, "live"))
                == list(RestApiConnector().read(old, ["s"], half, "live")))


def test_a_bare_list_body_matches_restapi_too():
    with Loopback({"/bare": lambda q: (200, ITEMS["items"])}) as server:
        old = {"base_url": server.url, "effective_field": "ts",
               "streams": {"s": {"path": "/bare"}}}
        new = {"base_url": server.url,
               "streams": {"s": {"path": "/bare", "effective_field": "ts"}}}
        assert (list(RestWindowConnector().read(new, ["s"], {}, "live"))
                == list(RestApiConnector().read(old, ["s"], {}, "live")))


def test_both_connectors_refuse_the_same_bad_row():
    with Loopback({"/items": lambda q: (200, {"items": [{"v": 1}]})}) as server:
        old = {"base_url": server.url, "effective_field": "ts",
               "streams": {"s": {"path": "/items", "records_path": "items"}}}
        new = {"base_url": server.url, "streams": {"s": {
            "path": "/items", "records_path": "items", "effective_field": "ts"}}}
        with pytest.raises(AssetError, match="missing or empty"):
            list(RestApiConnector().read(old, ["s"], {}, "live"))
        with pytest.raises(AssetError, match="missing or empty"):
            list(RestWindowConnector().read(new, ["s"], {}, "live"))


# -- acquisition e2e ------------------------------------------------------------------


class StubRestWindowConnector(RestWindowConnector):
    """The pack over a class-level script and clock; acquisition builds it with no arguments."""

    script = None
    now = NOW

    def __init__(self):
        super().__init__(getter=type(self).script, sleeper=lambda s: None,
                         clock=lambda: type(self).now)


@pytest.fixture
def candles_source(registry):
    config = example("candles")
    config["streams"]["candles-btc-usd"]["pagination"]["step"] = 600
    vid = registry.register("source_config", {
        "name": "exchange",
        "catalog_source": "exchange-src",
        "connector": "tests.onboarding.test_restwindow:StubRestWindowConnector",
        "config": config,
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    StubRestWindowConnector.script = Script({CANDLES: candles_server})
    StubRestWindowConnector.now = at("2024-01-01T00:30:30Z")
    yield vid
    StubRestWindowConnector.script = None
    StubRestWindowConnector.now = NOW


def test_acquisition_pulls_then_resumes_from_its_checkpoint(root, registry, candles_source):
    first = run_acquisition(root, registry, "exchange", "candles-btc-usd", "backfill")
    assert first["records"] == 30 and first["snapshot"] is not None and first["state_saved"]
    assert load_state(root, "exchange", "candles-btc-usd", "backfill") == {
        "candles-btc-usd": {"cursor": "2024-01-01T00:29:00+00:00"}}

    StubRestWindowConnector.now = at("2024-01-01T00:40:30Z")
    script = StubRestWindowConnector.script = Script({CANDLES: candles_server})
    second = run_acquisition(root, registry, "exchange", "candles-btc-usd", "backfill")
    assert second["records"] == 10 and second["snapshot"] != first["snapshot"]
    # check() probes the first window from `start`; the read resumes at the checkpoint
    assert script.calls[0][1]["start"] == "2024-01-01T00:00:00Z"
    assert script.calls[1][1]["start"] == "2024-01-01T00:29:00Z"
    rows = scan_stream(root.root, "exchange", "candles-btc-usd", key_fields=("time",))
    assert len(rows) == 40
    assert sorted(r["time_iso"] for r in rows)[0] == "2024-01-01T00:00:00+00:00"


# -- the pack's own contract: public names only, standalone, vendor-neutral ----------

PACK = pathlib.Path(restwindow.__file__)


def test_notes_are_allowed_on_every_declaration_object():
    config = copy.deepcopy(example("trades"))
    stream = config["streams"]["trades-xbtusd"]
    stream["epoch"]["notes"] = "why this field"
    assert stream["notes"] and stream["pagination"]["notes"]
    assert RestWindowConnector().discover(config)[0]["stream"] == "trades-xbtusd"


def test_imports_only_public_names_and_nothing_from_restapi():
    import ast
    import importlib

    tree = ast.parse(PACK.read_text(encoding="utf-8"))
    seen = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            module = "dskit.onboarding" + ("." + node.module if node.module else "")
            seen[module] = [alias.name for alias in node.names]
        elif isinstance(node, ast.Import):
            assert not any("restapi" in alias.name for alias in node.names)
    assert seen, "the pack imports nothing from its siblings?"
    for module, names in seen.items():
        assert "restapi" not in module and not module.endswith("libs"), module
        public = importlib.import_module(module).__all__
        assert all(name in public for name in names), (module, names)
    # and no attribute reach into another module's private name
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("_") \
                and isinstance(node.value, ast.Name) and node.value.id not in ("self", "cls"):
            raise AssertionError(f"underscore attribute reach: {node.value.id}.{node.attr}")


def test_standalone_subclasses_nothing_but_the_connector_contract():
    from dskit.onboarding.connector import Connector

    assert RestWindowConnector.__mro__[1] is Connector
    assert not issubclass(RestWindowConnector, RestApiConnector)


def test_all_is_declared_and_exports_no_underscore_name():
    assert "RestWindowConnector" in restwindow.__all__
    for name in restwindow.__all__:
        assert not name.startswith("_") and hasattr(restwindow, name), name
    assert restwindow.__doc__ and ">>>" not in PACK.read_text(encoding="utf-8")


def test_the_pack_is_vendor_neutral():
    text = PACK.read_text(encoding="utf-8").lower()
    for vendor in ("coinbase", "deribit", "kraken", "binance", "kalshi", "polymarket"):
        assert vendor not in text, vendor


# -- every default has ONE name, and the prose that states it is pinned to it ---------


def test_every_default_is_read_from_its_one_name(monkeypatch):
    # time_format: the wire spelling of a bound
    monkeypatch.setattr(restwindow, "DEFAULT_TIME_FORMAT", "epoch_s")
    config = plain(pagination={"end": "2026-01-01T00:04:00Z"})
    conn, script, _ = connector({SERIES: empty_rows})
    read(conn, config, ["s"])
    assert script.params() == [{"from": epoch_s("2026-01-01T00:00:00Z"),
                                "to": epoch_s("2026-01-01T00:04:00Z")}]
    monkeypatch.undo()

    # lag: how far the open end sits behind the clock
    monkeypatch.setattr(restwindow, "DEFAULT_LAG_S", 30)
    config = plain(pagination={"start": "2026-10-06T11:59:00Z", "step": 60, "end": None})
    conn, script, _ = connector({SERIES: empty_rows})
    read(conn, config, ["s"])
    assert script.params() == [{"from": "2026-10-06T11:59:00Z", "to": "2026-10-06T12:00:00Z"}]
    monkeypatch.undo()

    # max_retries and pace_s
    monkeypatch.setattr(restwindow, "DEFAULT_MAX_RETRIES", 1)
    conn, script, sleeps = connector({SERIES: [http_error(503)] * 5})
    with pytest.raises(AssetError, match="after 2 attempt"):
        read(conn, one_window(), ["s"])
    monkeypatch.undo()
    monkeypatch.setattr(restwindow, "DEFAULT_PACE_S", 0.5)
    conn, script, sleeps = connector({SERIES: empty_rows})
    read(conn, plain(), ["s"])
    assert sleeps == [0.5, 0.5]


def test_the_default_timeout_reaches_the_urllib_call(monkeypatch):
    import urllib.request

    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"rows": []}'

    def urlopen(request, timeout):
        seen["timeout"] = timeout
        seen["url"] = request.full_url
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(restwindow, "DEFAULT_TIMEOUT_S", 99)
    conn = RestWindowConnector(sleeper=lambda s: None, clock=lambda: NOW)
    read(conn, one_window(), ["s"])
    assert seen["timeout"] == 99
    assert seen["url"] == (f"{BASE}{SERIES}?from=2026-01-01T00%3A00%3A00Z"
                           "&to=2026-01-01T00%3A04%3A00Z")
    read(conn, {**one_window(), "timeout": 7}, ["s"])
    assert seen["timeout"] == 7


def test_the_module_prose_states_the_defaults_it_names():
    # static prose can go stale on its own: anchor each number on its OWNING sentence
    doc = restwindow.__doc__
    assert f"request timeout in seconds (default {restwindow.DEFAULT_TIMEOUT_S})." in doc
    assert f"exponential backoff (default {restwindow.DEFAULT_MAX_RETRIES});" in doc
    assert f"between requests, never before the first\n  (default {restwindow.DEFAULT_PACE_S})." in doc
    assert f"``max_windows`` (default {restwindow.DEFAULT_MAX_WINDOWS})" in doc
    assert f"``max_pages`` (default\n{restwindow.DEFAULT_MAX_PAGES})" in doc
    assert f"``lag`` seconds\n(default {restwindow.DEFAULT_LAG_S})" in doc
    assert f"``epoch_ms``; default ``{restwindow.DEFAULT_TIME_FORMAT}``" in doc


# -- messages carry the window, never a query; window bounds are whole seconds -------


def test_error_messages_never_carry_the_query_string():
    for failure, fragment in ((http_error(404), "HTTP 404"), (ValueError("junk"), "not JSON")):
        conn, _, _ = connector({SERIES: failure})
        with pytest.raises(AssetError, match=fragment) as exc:
            read(conn, {**one_window(), "streams": {"s": {
                **one_window()["streams"]["s"], "params": {"key": "SECRET-IN-PARAMS"}}}}, ["s"])
        text = str(exc.value)
        assert f"{BASE}{SERIES}" in text
        assert "?" not in text and "SECRET-IN-PARAMS" not in text and "from=" not in text


def test_a_checkpoint_with_a_fraction_starts_the_walk_on_a_whole_second():
    conn, _, _ = connector({SERIES: [http_error(404)]})
    state = {"s": {"cursor": "2026-01-01T00:05:30.750000+00:00"}}
    with pytest.raises(AssetError) as exc:
        read(conn, plain(), ["s"], state=state)
    assert "window 1/2 [2026-01-01T00:05:30+00:00, 2026-01-01T00:09:30+00:00)" in str(exc.value)


def test_the_clock_must_be_aware_and_may_be_in_any_zone():
    config = plain(pagination={"start": "2026-10-06T11:59:00Z", "step": 60, "end": None})
    for bad in (datetime(2026, 10, 6, 12, 0, 30), "2026-10-06T12:00:30Z", None):
        script = Script({SERIES: empty_rows})
        conn = RestWindowConnector(getter=script, sleeper=lambda s: None, clock=lambda b=bad: b)
        with pytest.raises(AssetError, match="clock must return an aware datetime"):
            read(conn, config, ["s"])
        assert script.calls == []
    plus_five = timezone(timedelta(hours=5))
    conn, script, _ = connector({SERIES: empty_rows}, now=datetime(2026, 10, 6, 17, 0, 30, tzinfo=plus_five))
    read(conn, config, ["s"])
    assert script.params()[-1] == {"from": "2026-10-06T12:00:00Z", "to": "2026-10-06T12:00:30Z"}
