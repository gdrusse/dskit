"""AlpacaBarsConnector through the four-verb contract, then one
acquisition end-to-end — all against :class:`StubBarsConnector`, the
production class with only ``_fetch``/``_credentials`` doubled, so the
knob gate, the SIP window clamp, the cursor filter and the message
envelope under test are the REAL code. The shipped configs drive the
spec-gate tests; no test touches the network.
"""

import json
import os

import pytest

from dskit.onboarding import (
    AssetError,
    OnboardingRoot,
    check_config,
    check_message,
    load_suite,
    parse_utc,
    run_acquisition,
    run_suite,
)

from intraday_poc import connectors
from intraday_poc.connectors import AlpacaBarsConnector
from intraday_poc.testing import StubBarsConnector

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")

#: The one shipped connector config — one source name, both modes
#: (see test_configs.py::test_one_source_name_carries_both_pulls).
SOURCE_CONFIG = "source-backfill.json"

#: A small, past-dated stub config — 90 minutes of bars per symbol.
STUB_CONFIG = {
    "symbols": ["AAPL", "MSFT"],
    "start": "2026-01-05T14:30:00+00:00",
    "feed": "iex",
    "adjustment": "raw",
    "bars_per_symbol": 90,
}


def _shipped(name):
    with open(os.path.join(CONFIGS, name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def conn():
    return StubBarsConnector()


def _read(conn, config, streams, state=None, mode="backfill"):
    msgs = list(conn.read(config, streams, state or {}, mode))
    for m in msgs:
        assert check_message(m) is not None  # every message envelope-valid
    return msgs


def test_spec_passes_its_own_gate():
    conn = AlpacaBarsConnector()
    check_config(conn, _shipped(SOURCE_CONFIG))
    with pytest.raises(AssetError, match="unknown key"):
        check_config(conn, {**_shipped(SOURCE_CONFIG), "surprise": 1})
    with pytest.raises(AssetError, match="required knob"):
        check_config(conn, {"start": "2026-01-01"})


def test_check_fails_fast_on_bad_knobs(conn):
    conn.check(STUB_CONFIG)  # the stub knobs — fine
    with pytest.raises(AssetError, match="symbols"):
        conn.check({**STUB_CONFIG, "symbols": []})
    with pytest.raises(AssetError, match="feed"):
        conn.check({**STUB_CONFIG, "feed": "bloomberg"})
    with pytest.raises(AssetError, match="adjustment"):
        conn.check({**STUB_CONFIG, "adjustment": "sideways"})
    with pytest.raises(AssetError):
        conn.check({**STUB_CONFIG, "start": "not-a-date"})


def test_credentials_refused_by_env_var_name(monkeypatch):
    """The PRODUCTION credential gate: empty env vars are named, the
    material itself is never echoed."""
    conn = AlpacaBarsConnector()
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    knobs = conn.resolve_knobs({"symbols": ["AAPL"], "start": "2026-01-01"})
    with pytest.raises(AssetError, match="APCA_API_KEY_ID"):
        conn._credentials(knobs)


def test_discover_names_the_stream(conn):
    """The frozen shape, restated independently of the code — an
    assertion that read its expectation from the constants would assert
    nothing. That the CODE reads those constants is
    ``test_discover_reads_the_stream_and_key_constants``."""
    (stream,) = conn.discover(STUB_CONFIG)
    assert stream["stream"] == "bars"
    assert stream["primary_key"] == ["symbol", "ts"]
    assert "close" in stream["schema"]["fields"]


def test_discover_reads_the_stream_and_key_constants(conn, monkeypatch):
    """``discover`` publishes the module's constants, so the node that
    imports them cannot key its dedup off a different tuple than the
    platform advertises. Rebinding each constant must move the
    published record; the node half of the pin lives in
    ``test_nodes.py``."""
    monkeypatch.setattr(connectors, "BAR_STREAM", "ticks")
    monkeypatch.setattr(connectors, "BAR_KEY_FIELDS", ("symbol",))
    (stream,) = conn.discover(STUB_CONFIG)
    assert stream["stream"] == "ticks"
    assert stream["primary_key"] == ["symbol"]


def test_read_emits_schema_records_then_state(conn):
    msgs = _read(conn, STUB_CONFIG, ["bars"])
    assert msgs[0]["type"] == "SCHEMA" and msgs[-1]["type"] == "STATE"
    records = [m for m in msgs if m["type"] == "RECORD"]
    assert len(records) == 180  # 90 minutes x 2 symbols
    effs = [m["effective_date"] for m in records]
    cursor = msgs[-1]["state"]["bars"]["cursor"]
    assert cursor == max(effs)
    assert all(m["kind"] == "observation" for m in records)
    symbols = {m["data"]["symbol"] for m in records}
    assert symbols == {"AAPL", "MSFT"}


def test_cursor_filters_already_durable_rows(conn):
    first = _read(conn, STUB_CONFIG, ["bars"])
    cursor_state = first[-1]["state"]
    again = _read(conn, STUB_CONFIG, ["bars"], dict(cursor_state))
    assert [m for m in again if m["type"] == "RECORD"] == []
    assert again[-1]["state"] == cursor_state  # an honest, empty no-op

    # A mid-stream cursor lets only the strictly-newer tail through.
    records = [m for m in first if m["type"] == "RECORD"]
    mid = sorted({m["effective_date"] for m in records})[45]
    tail = [m for m in _read(conn, STUB_CONFIG, ["bars"],
                             {"bars": {"cursor": mid}})
            if m["type"] == "RECORD"]
    assert tail and all(m["effective_date"] > mid for m in tail)


def test_sip_window_clamps_the_end(conn):
    """feed=sip clamps the fetch window 16 minutes into the past — the
    free tier's recent-SIP gate can then never trip mid-pull."""
    from datetime import datetime, timedelta, timezone

    knobs = conn.resolve_knobs({**STUB_CONFIG, "feed": "sip"})
    _start, end = conn._window(knobs, "", "backfill")
    assert end <= datetime.now(timezone.utc) - timedelta(minutes=15)


def test_the_knob_gate_is_public(conn):
    """``resolve_knobs`` is the connector's PUBLIC gate.

    ``live.py`` resolves the source config through it (see
    ``test_nodes.py::test_live_vendor_knobs_come_from_the_source_config``)
    rather than restating the defaults, so the method it calls must be
    part of the contract: ``__all__`` plus the ``_`` prefix IS the
    public API here, and a serving loop pinned to a name the module
    declares private breaks on any internal rename, silently.
    """
    assert not hasattr(conn, "_knobs"), (
        "the knob gate moved back behind the private prefix while a "
        "second module calls it"
    )
    assert "resolve_knobs" in dir(AlpacaBarsConnector)
    assert conn.resolve_knobs(STUB_CONFIG)["adjustment"] == "raw"


# -- the forward mode is a top-up, not a second backfill --------------------


def test_a_live_pull_without_a_cursor_reaches_back_the_declared_lookback(conn):
    """The forward mode's window is bounded by ``live_lookback_minutes``.

    The cursor is keyed per (source, stream, MODE), so the live mode's
    is EMPTY on its first pull. Windowing that from ``config.start`` —
    which is what a mode-blind ``_window`` does — asks the vendor for
    the entire history a second time and writes it as a full duplicate
    acquisition, whatever the backfill cursor has reached. Bounded, the
    first live pull covers the seam between the backfill's tail and now.
    """
    from datetime import datetime, timedelta, timezone

    knobs = conn.resolve_knobs({**STUB_CONFIG, "feed": "iex",
                                "live_lookback_minutes": 30})
    start, end = conn._window(knobs, "", "live")
    assert timedelta(minutes=29) <= end - start <= timedelta(minutes=31)
    assert start > datetime.now(timezone.utc) - timedelta(minutes=31)

    # Backfill is untouched: all the history the config declares.
    back_start, _ = conn._window(knobs, "", "backfill")
    assert back_start == parse_utc(STUB_CONFIG["start"])

    # A live cursor still wins outright — the floor exists only to keep
    # a FIRST pull from re-fetching everything; skipping back to it
    # after a long outage would tear a hole in the store.
    cursor = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    resumed, _ = conn._window(knobs, cursor, "live")
    assert resumed == parse_utc(cursor)


def test_a_first_live_pull_does_not_re_fetch_the_backfill(conn):
    """The same rule end to end, through ``read``: the stub's bars are
    all in the past, so a fresh live pull emits NOTHING while the same
    config in backfill mode emits the whole history."""
    live = [m for m in _read(conn, STUB_CONFIG, ["bars"], {}, mode="live")
            if m["type"] == "RECORD"]
    assert live == []
    backfill = [m for m in _read(conn, STUB_CONFIG, ["bars"], {},
                                 mode="backfill") if m["type"] == "RECORD"]
    assert len(backfill) == 180


def test_the_live_lookback_default_is_named_once(conn):
    """One name for the default: the knob gate and the ``spec()`` note a
    config author reads must both come from the constant."""
    default = connectors.DEFAULT_LIVE_LOOKBACK_MINUTES
    assert conn.resolve_knobs(STUB_CONFIG)["live_lookback_minutes"] == default
    notes = conn.spec()["params"]["live_lookback_minutes"]["notes"]
    assert f"Default {default}." in notes, notes


def test_the_mode_vocabulary_comes_from_the_platform():
    """The forward mode's NAME is unpacked from ``MODES`` (ADR-0014), so
    a platform that grows a third mode breaks this connector at import —
    loudly — instead of silently treating the newcomer as a backfill."""
    from dskit.onboarding import MODES

    assert (connectors.BACKFILL_MODE, connectors.LIVE_MODE) == MODES


def test_both_fetch_paths_pull_one_bar_interval(monkeypatch):
    """The store's bars and the served bars share ONE interval constant.

    ``connectors._fetch`` and ``live.fetch_bars`` each build a vendor
    ``TimeFrame``; two literals there would let the loop serve 5-minute
    bars into weights fit on 1-minute bars, with nothing raising. A
    ``timeframe`` knob on ``spec()`` is still open in TODO.md — until
    then the agreement is pinned here.
    """
    pytest.importorskip("alpaca.data.historical")
    import alpaca.data.historical as historical

    from intraday_poc import live

    seen = []

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_stock_bars(self, request):
            seen.append(request.timeframe.value)
            return type("Bars", (), {"data": {}})()

    monkeypatch.setattr(historical, "StockHistoricalDataClient", _FakeClient)
    monkeypatch.setattr(connectors, "BAR_INTERVAL", (5, "Minute"))
    monkeypatch.setenv("APCA_API_KEY_ID", "stub-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "stub-secret")

    knobs = AlpacaBarsConnector().resolve_knobs(STUB_CONFIG)
    start, end = AlpacaBarsConnector()._window(knobs, "", "backfill")
    list(AlpacaBarsConnector()._fetch(knobs, start, end))
    live.fetch_bars(["AAPL"], 30, "close", "all")

    assert seen == ["5Min", "5Min"], seen


def test_unknown_stream_named(conn):
    with pytest.raises(AssetError, match="unknown stream"):
        list(conn.read(STUB_CONFIG, ["ghost"], {}, "live"))


def test_acquisition_and_suite_end_to_end(tmp_path):
    """The whole seam: source registered + activated, one pull through
    the REAL acquire path, the shipped suite over the snapshot — and it
    PASSES. Then a second pull is caught up: an empty, honest no-op."""
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": "alpaca",
        "catalog_source": "alpaca-src",
        "connector": "intraday_poc.testing:StubBarsConnector",
        "config": dict(STUB_CONFIG),
    }, origin="test")
    registry.transition(vid, "active", origin="test")

    out = run_acquisition(root, registry, "alpaca", "bars", "backfill")
    assert out["records"] == 180
    assert out["state_saved"]  # the cursor persisted AFTER the snapshot

    suite = load_suite(os.path.join(CONFIGS, "suite-bars.json"))
    verdict = run_suite(root, registry, suite, out["snapshot"])
    assert verdict["gating"] == "pass", verdict["statistics"]

    again = run_acquisition(root, registry, "alpaca", "bars", "backfill")
    assert again["snapshot"] is None and again["records"] == 0
