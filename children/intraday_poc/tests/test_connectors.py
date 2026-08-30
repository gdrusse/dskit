"""AlpacaBarsConnector through the four-verb contract, then one
acquisition end-to-end — all against :class:`StubBarsConnector`, the
production class with only ``_fetch``/``_credentials`` doubled, so the
knob gate, the SIP window clamp, the cursor filter and the message
envelope under test are the REAL code. The shipped configs drive the
spec-gate tests; no test touches the network.
"""

import ast
import inspect
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


@pytest.mark.parametrize("knob,constant,rebound", [
    ("feed", "DEFAULT_FEED", "iex"),
    ("adjustment", "DEFAULT_ADJUSTMENT", "split"),
    ("live_lookback_minutes", "DEFAULT_LIVE_LOOKBACK_MINUTES", 77),
    ("chunk_days", "DEFAULT_CHUNK_DAYS", 17),
    ("key_env", "DEFAULT_KEY_ENV", "SOME_OTHER_KEY_ID"),
    ("secret_env", "DEFAULT_SECRET_ENV", "SOME_OTHER_SECRET_KEY"),
    ("timeframe", "BAR_INTERVAL", (5, "Minute")),
])
def test_every_spec_default_is_named_once(conn, monkeypatch, knob, constant,
                                          rebound):
    """One name per default: the gate AND the note both read it.

    ``spec()``'s notes are what a config author reads to decide whether
    to declare a knob at all, so a note restating the default as a
    LITERAL advertises a value the pull may no longer use — the repo's
    commonest defect, in prose. Rebinding each constant must therefore
    move both consumers; a literal in either survives the rebinding and
    fails here. This replaces the lookback-only version, which read the
    constant but never rebound it and covered one knob of five.
    (Ruling 4.)
    """
    bare = {"symbols": ["AAPL"], "start": STUB_CONFIG["start"]}
    assert conn.resolve_knobs(bare)[knob] == getattr(connectors, constant)

    monkeypatch.setattr(connectors, constant, rebound)
    notes = conn.spec()["params"][knob]["notes"]
    assert f"Default {rebound}." in notes, notes


def test_timeframe_knob_refuses_bad_shapes(conn):
    """A mistyped interval is a gate refusal, never an SDK error mid-pull.

    Non-Minute units are refused too: the forward loop's wake cadence is
    minute-derived (skeptic BLOCKER on silent train/serve drift if the
    store stays 1-minute while live fetches another unit).
    """
    for bad in ([1], "1Min", [1, "minute"], [0, "Minute"], [1.5, "Minute"],
                [True, "Minute"], [1, "Hour"], [1, "Day"]):
        with pytest.raises(AssetError, match="timeframe"):
            conn.resolve_knobs({**STUB_CONFIG, "timeframe": bad})
    knobs = conn.resolve_knobs({**STUB_CONFIG, "timeframe": [5, "Minute"]})
    assert knobs["timeframe"] == (5, "Minute")
    discovered = AlpacaBarsConnector().discover(
        {**STUB_CONFIG, "timeframe": [5, "Minute"]}
    )
    assert discovered[0]["timeframe"] == [5, "Minute"]
    assert discovered[0]["primary_key"] == list(connectors.BAR_KEY_FIELDS)


def test_a_live_lookback_the_sip_clamp_swallows_is_refused(conn):
    """A lookback inside the SIP clamp is a permanent silent no-op.

    On ``feed=sip`` a cursor-less live pull runs from ``now -
    live_lookback_minutes`` to ``now - 16min``, so any lookback at or
    below the clamp gives an EMPTY window: no records, no error, and an
    empty cursor, so the next pull repeats the no-op forever. An
    operator tightening the knob for a lean top-up would get a live mode
    that never acquires and never complains. The gate refuses it, and
    reads the bound off the clamp so the two cannot drift.
    """
    clamp = connectors._SIP_LAG.total_seconds() / 60
    with pytest.raises(AssetError, match="live_lookback_minutes"):
        conn.resolve_knobs({**STUB_CONFIG, "feed": "sip",
                            "live_lookback_minutes": clamp})

    knobs = conn.resolve_knobs({**STUB_CONFIG, "feed": "sip",
                                "live_lookback_minutes": clamp + 1})
    start, end = conn._window(knobs, "", "live")
    assert start is not None and end > start

    # iex is not clamped, so the same lookback is legitimate there —
    # the refusal is about the INTERACTION, not the number.
    assert conn.resolve_knobs(
        {**STUB_CONFIG, "feed": "iex", "live_lookback_minutes": clamp}
    )["live_lookback_minutes"] == clamp


def _unpacked_from(module, names):
    """The module-level name ``names`` is tuple-unpacked from, or None."""
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (isinstance(target, ast.Tuple)
                and all(isinstance(e, ast.Name) for e in target.elts)
                and tuple(e.id for e in target.elts) == tuple(names)):
            return node.value.id if isinstance(node.value, ast.Name) else None
    return None


def _imports_from(module, name):
    """The dotted module ``name`` is imported from, or None."""
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(
                alias.name == name for alias in node.names):
            return "." * node.level + (node.module or "")
    return None


def test_the_mode_vocabulary_comes_from_the_platform():
    """The forward mode's NAME is UNPACKED from the platform's ``MODES``.

    ADR-0014 declares two acquisition modes, and this connector's whole
    live/backfill split is keyed on their names. A platform that grows a
    THIRD mode must break this module at import — loudly — instead of
    letting the newcomer fall through ``_window``'s ``elif`` and be
    windowed as a backfill.

    A value comparison alone cannot show that: ``BACKFILL_MODE =
    "backfill"`` / ``LIVE_MODE = "live"`` written as two literals
    satisfies it exactly as well, and then a third platform mode lands
    silently. So the BINDING is what is read — the trick ``test_nodes``
    uses for the constants a reader must not restate — and both halves
    matter: unpacked from a name, and that name imported from the
    platform rather than rebuilt here. (Ruling 5.)
    """
    from dskit.onboarding import MODES

    assert _unpacked_from(connectors, ("BACKFILL_MODE", "LIVE_MODE")) == \
        "MODES", (
        "connectors.py must UNPACK the platform's MODES tuple — two "
        "literals cannot break when the platform grows a mode"
    )
    assert _imports_from(connectors, "MODES") == "dskit.onboarding", (
        "MODES must come from the platform, not a tuple rebuilt here"
    )
    assert (connectors.BACKFILL_MODE, connectors.LIVE_MODE) == MODES


def test_both_fetch_paths_pull_one_bar_interval(monkeypatch):
    """The store's bars and the served bars share ONE resolved interval.

    ``connectors._fetch`` and ``live.fetch_bars`` each build a vendor
    ``TimeFrame`` from the source config's ``timeframe`` (defaulting to
    ``BAR_INTERVAL``). Two literals there would let the loop serve
    5-minute bars into weights fit on 1-minute bars, with nothing
    raising.
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
    live.fetch_bars(["AAPL"], 30, "close", "all", "stub-key", "stub-secret",
                    timeframe=knobs["timeframe"])

    assert seen == ["5Min", "5Min"], seen
    # An explicit knob on the config wins over the rebound default.
    knobs5 = AlpacaBarsConnector().resolve_knobs(
        {**STUB_CONFIG, "timeframe": [15, "Minute"]}
    )
    start, end = AlpacaBarsConnector()._window(knobs5, "", "backfill")
    list(AlpacaBarsConnector()._fetch(knobs5, start, end))
    assert seen[-1] == "15Min", seen


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
