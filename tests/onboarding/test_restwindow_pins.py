"""restwindow pins (ADR-0237): the restated transport agrees with the packs it was restated from, and its refusals are exact.

``restwindow`` restates ``restapi``'s transport rather than importing an underscore name of it (so a private rename cannot
break it), which makes agreement something a test must hold: the retried statuses, the client name sent and the dot-path
reader (TODO R1-08). A mutation pass also found refusals and retries that every test passed through a different door:
a token that is not a scalar refused by the loop guard instead of the type guard, a retry code nobody drove, a window label
nobody read. Each case here goes through the intended door only.
"""

import pytest

from dskit.onboarding.libs import kalshi, kalshi_history, restapi, restwindow

from .test_restwindow import (
    BASE,
    SERIES,
    AssetError,
    connector,
    http_error,
    one_window,
    plain,
    positional,
    read,
    records,
)

# -- agreement with the transport it was restated from ---------------------------------------------------


def test_the_retried_statuses_are_the_ones_restapi_retries_and_are_pinned_by_value():
    assert restwindow._RETRY_STATUSES == restapi._RETRY_STATUSES == (429, 500, 502, 503, 504)
    assert restwindow._RETRY_STATUSES == kalshi._RETRY_STATUSES == kalshi_history._RETRY_STATUSES


def test_the_client_name_sent_is_the_one_the_kalshi_packs_send():
    assert restwindow._USER_AGENT == kalshi._USER_AGENT == kalshi_history._USER_AGENT == "dskit-onboarding"


@pytest.mark.parametrize("body, path", [
    ({"a": {"b": {"c": 1}}}, "a.b.c"), ({"a": {"b": 2}}, "a.b"), ({"a": 1}, "a"), ({"a": {"b": 1}}, "a.x"),
    ({"a": 1}, "a.b"), ({"a": [1, 2]}, "a.0"), ({}, "a"), ([1], "a"), (None, "a"), ({"a": None}, "a"),
    ({"a": {"b": {"c": 1}}}, "a.b"), ({"": 1}, ""), ({"a.b": 1}, "a.b"), ("text", "a"),
])
def test_the_dot_path_reader_answers_as_restapis_does(body, path):
    assert restwindow._pluck(body, path) == restapi._pluck(body, path)


@pytest.mark.parametrize("status", restwindow._RETRY_STATUSES)
def test_every_retried_status_is_retried_once_then_the_pull_succeeds(status):
    conn, script, sleeps = connector({SERIES: [http_error(status), {"rows": []}]})
    read(conn, one_window(), ["s"])
    assert len(script.calls) == 2 and sleeps == [0.5], f"HTTP {status} must be retried"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 501, 505])
def test_a_status_outside_the_retried_ones_is_refused_at_once(status):
    conn, script, sleeps = connector({SERIES: [http_error(status), {"rows": []}]})
    with pytest.raises(AssetError, match=f"HTTP {status}"):
        read(conn, one_window(), ["s"])
    assert len(script.calls) == 1 and sleeps == []


# -- cursor tokens: the type guard, and the echo of the token just sent ---------------------------------------


@pytest.mark.parametrize("token", [["a"], {"a": 1}, True, False])
def test_a_token_that_is_not_a_string_or_number_is_refused_by_the_type_guard_on_the_first_page(token):
    body = {"r": {"X": [[1767225600, 1, 2]]}, "n": token}
    config = positional(records_path="r.*", pagination={"strategy": "cursor", "path": "n", "param": "after"})
    conn, script, _ = connector({SERIES: lambda p: body})
    with pytest.raises(AssetError, match="must be a string or number, got"):
        read(conn, config, ["s"])
    assert len(script.calls) == 1, "refused before any second request, not caught later as a repeat"


def test_a_token_that_only_echoes_the_one_just_sent_is_refused_before_the_next_request():
    config = positional(records_path="r.*", pagination={
        "strategy": "cursor", "path": "r.n", "param": "after", "start": "2026-01-01T00:00:00Z", "time_format": "epoch_s"})
    conn, script, _ = connector({SERIES: lambda p: {"r": {"X": [[1767225600, 1, 2]], "n": p["after"]}}})
    with pytest.raises(AssetError, match="did not advance"):
        read(conn, config, ["s"])
    assert [p["after"] for p in script.params()] == [1767225600], "the start token counts as seen from the first request"


def test_a_repeated_boundary_row_is_dropped_whatever_order_its_keys_arrive_in():
    config = {"base_url": BASE, "streams": {"s": {
        "path": SERIES, "records_path": "rows", "effective_field": "ts", "primary_key": ["ts"],
        "pagination": {"strategy": "cursor", "path": "next", "param": "after", "page_size": 2, "size_param": "n"}}}}
    first = [{"ts": "2026-01-01T00:00:00Z", "v": 1}, {"ts": "2026-01-01T00:00:01Z", "v": 2}]
    again = {"v": 2, "ts": "2026-01-01T00:00:01Z"}  # the same row, keys in the other order
    pages = [{"rows": first, "next": "t1"}, {"rows": [again, {"ts": "2026-01-01T00:00:02Z", "v": 3}], "next": "t2"},
             {"rows": [], "next": "t2"}]
    conn, script, _ = connector({SERIES: pages})
    msgs = read(conn, config, ["s"])
    assert [m["data"]["v"] for m in records(msgs)] == [1, 2, 3], "the boundary row appears once"
    assert len(script.calls) == 3


# -- labels --------------------------------------------------------------------------------------------------------


def test_a_failed_first_probe_names_the_first_of_the_windows_it_would_pull():
    conn, _, _ = connector({SERIES: http_error(404)})
    with pytest.raises(AssetError, match=r"window 1/3 \[2026-01-01T00:00:00\+00:00, 2026-01-01T00:04:00\+00:00\)"):
        conn.check(plain())
