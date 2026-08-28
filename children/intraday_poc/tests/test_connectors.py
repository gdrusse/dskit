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


def _read(conn, config, streams, state=None, mode="live"):
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
    knobs = conn._knobs({"symbols": ["AAPL"], "start": "2026-01-01"})
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

    knobs = conn._knobs({**STUB_CONFIG, "feed": "sip"})
    _start, end = conn._window(knobs, "")
    assert end <= datetime.now(timezone.utc) - timedelta(minutes=15)


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
