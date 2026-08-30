"""Connector contract and one stubbed acquire → validate path."""

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

from intraday_equities.connectors import AlpacaBars, SchwabBars
from intraday_equities.testing import StubAlpacaBars, StubSchwabBars

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")


def _load(name):
    with open(os.path.join(CONFIGS, name), encoding="utf-8") as fh:
        return json.load(fh)


def _read(conn, config, streams, state=None, mode="backfill"):
    msgs = list(conn.read(config, streams, state or {}, mode))
    for msg in msgs:
        assert check_message(msg) is not None
    return msgs


@pytest.mark.parametrize("cls,name", [
    (AlpacaBars, "source-alpaca-backfill.json"),
    (SchwabBars, "source-schwab-live.json"),
])
def test_shipped_sources_pass_the_spec_gate(cls, name):
    check_config(cls(), _load(name))
    with pytest.raises(AssetError, match="unknown key"):
        check_config(cls(), {**_load(name), "surprise": 1})


def test_alpaca_stub_emits_schema_records_then_state():
    conn = StubAlpacaBars()
    config = {
        "symbols": ["AAPL"],
        "start": "2026-01-05T14:30:00+00:00",
        "bars_per_symbol": 3,
    }
    msgs = _read(conn, config, ["bars"])
    types = [msg["type"] for msg in msgs]
    assert types[0] == "SCHEMA"
    assert types[-1] == "STATE"
    records = [msg for msg in msgs if msg["type"] == "RECORD"]
    assert len(records) == 3
    assert records[0]["data"]["symbol"] == "AAPL"


def test_schwab_stub_emits_closed_bars_without_oauth():
    conn = StubSchwabBars()
    config = {
        "symbols": ["AAPL"],
        "start": "2026-01-05T14:30:00+00:00",
        "bars_per_symbol": 3,
    }
    msgs = _read(conn, config, ["bars"])
    records = [msg for msg in msgs if msg["type"] == "RECORD"]
    assert records
    assert {msg["data"]["symbol"] for msg in records} == {"AAPL"}


def test_unknown_stream_is_named():
    conn = StubAlpacaBars()
    with pytest.raises(AssetError, match="unknown stream"):
        list(conn.read(
            {"symbols": ["AAPL"], "start": "2026-01-05T14:30:00+00:00"},
            ["ghost"], {}, "backfill",
        ))


def test_acquisition_and_suite_end_to_end(tmp_path):
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = root.registry()
    config = {
        **_load("source-alpaca-backfill.json"),
        "start": "2026-01-05T14:30:00+00:00",
        "bars_per_symbol": 4,
        "symbols": ["AAPL", "JPM", "XOM", "WMT", "LLY", "SPY"],
    }
    vid = registry.register("source_config", {
        "name": "alpaca-sip",
        "catalog_source": "alpaca-sip-source",
        "connector": "intraday_equities.testing:StubAlpacaBars",
        "config": config,
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    out = run_acquisition(root, registry, "alpaca-sip", "bars", "backfill")
    assert out["records"] > 0
    assert out["state_saved"]
    suite = load_suite(os.path.join(CONFIGS, "suite-alpaca-bars.json"))
    verdict = run_suite(root, registry, suite, out["snapshot"])
    assert verdict["gating"] == "pass", verdict["statistics"]
