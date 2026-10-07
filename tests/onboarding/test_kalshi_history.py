"""libs/kalshi_history.py: the standalone Kalshi /historical pack (ADR-0236).

No network anywhere: every test injects the ``getter`` transport, a
recording ``sleeper`` and a fixed ``clock``, so cutoff routing, pacing, retry,
cursor paging, event-candle following and the row shapes run for real. The
acquisition e2e resolves :class:`StubHistoryConnector` below by class
reference (the platform builds a connector with no arguments). The parity
tests drive the PUBLIC ``KalshiConnector`` over the same payloads and pin the
shared rows equal; they never import an underscore name of either pack.
"""

import ast
import importlib
import importlib.util
import json
import pathlib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import acquire as acquire_module
from dskit.onboarding import (
    check_config,
    check_message,
    load_state,
    resolve_connector,
    run_acquisition,
    scan_stream,
)
from dskit.onboarding import base as base_module
from dskit.onboarding import connector as connector_module
from dskit.onboarding.libs import kalshi, kalshi_history
from dskit.onboarding.libs.kalshi import KalshiConnector
from dskit.onboarding.libs.kalshi_history import KalshiHistoryConnector

BASE = "https://kalshi.test/trade-api/v2"
NOW = datetime(2026, 10, 7, 12, 0, 30, tzinfo=timezone.utc)
#: ``NOW`` floored to the minute: the capture instant every undated row carries.
CAPTURE = "2026-10-07T12:00:00+00:00"
CUTOFF = "2026-08-07T00:00:00Z"
CUTOFF_BODY = {
    "market_settled_ts": CUTOFF,
    "trades_created_ts": CUTOFF,
    "orders_updated_ts": "2026-09-22T00:00:00Z",
    "market_positions_last_updated_ts": CUTOFF,
}
SERIES = "KXBTCD"
EVENT = "KXBTCD-26OCT0621"
CONFIG = {"series": [SERIES], "base_url": BASE, "period_interval": 1}


def parse_iso(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def unix(iso):
    return int(parse_iso(iso).timestamp())


def market(ticker, event=EVENT, **over):
    """A live-shaped market payload; ``over`` replaces or adds fields."""
    raw = {
        "ticker": ticker,
        "event_ticker": event,
        "strike_type": "greater",
        "floor_strike": 100000,
        "status": "finalized",
        "result": "yes",
        "open_time": "2026-10-07T00:00:00Z",
        "close_time": "2026-10-07T01:00:00Z",
        "yes_sub_title": "above 100k",
        "yes_bid_dollars": "0.9900",
        "yes_ask_dollars": "1.0000",
        "last_price_dollars": "0.9700",
        "expiration_value": "85735.80",
        "settlement_ts": "2026-10-07T01:02:20.5Z",
        "volume_fp": "12.50",
    }
    raw.update(over)
    return raw


def http_error(code, headers=None):
    return urllib.error.HTTPError(BASE, code, "scripted", headers or {}, None)


class Script:
    """A scripted ``getter(url, params)`` routed by path under BASE.

    A route value is a body dict, an Exception to raise, a callable of
    ``params`` returning either, or a list of those consumed in order.
    Every call is recorded.
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

    def paths(self):
        return [path for path, _params in self.calls]

    def params(self, path):
        return [p for q, p in self.calls if q == path]


def connector(routes, now=NOW, clock=None):
    """A connector over a scripted transport; returns (connector, script, sleeps)."""
    script = Script({"/historical/cutoff": CUTOFF_BODY, **routes})
    sleeps = []
    conn = KalshiHistoryConnector(
        getter=script, sleeper=sleeps.append, clock=clock or (lambda: now))
    return conn, script, sleeps


def read(conn, streams, config=CONFIG, state=None, mode="backfill"):
    msgs = list(conn.read(config, streams, state or {}, mode))
    for msg in msgs:
        assert check_message(msg) is not None  # every envelope is valid
    return msgs


def records(msgs):
    return [m for m in msgs if m["type"] == "RECORD"]


def data(msgs):
    return [m["data"] for m in records(msgs)]


def by_status(**lists):
    """A ``/markets`` handler answering per ``status`` param (missing = empty)."""
    return lambda p: {"markets": lists.get(p["status"], []), "cursor": ""}


# -- spec / knobs -------------------------------------------------------------


def test_spec_passes_its_own_gate():
    conn = KalshiHistoryConnector()
    check_config(conn, CONFIG)
    check_config(conn, {**CONFIG, "notes": "documentation is always allowed",
                        "candle_grouping": "batch", "batch_size": 50,
                        "max_candles": 5000, "archive_max_candles": 2500})
    with pytest.raises(AssetError, match="unknown key"):
        check_config(conn, {**CONFIG, "surprise": 1})
    with pytest.raises(AssetError, match="required knob"):
        check_config(conn, {"base_url": BASE})


def test_bad_knob_shapes_refused():
    conn = KalshiHistoryConnector()
    bad = {
        "series": ([], ["KXA", ""], "KXA", ["KXA", "KXA"]),
        "statuses": ([], "open", [1], ["open", "open"]),
        "limit": (0, 1.5, True),
        "max_pages": (0,),
        "period_interval": (0,),
        "retries": (-1, 0.5),
        "pace_s": (-0.1, "fast", "0.5"),
        "timeout_s": (0, -1, "30"),
        "base_url": ("ftp://kalshi", 5),
        "candle_grouping": ("ladder", 3, ""),
        "batch_size": (0, 1.5, True),
        "max_candles": (0, 1, 2.5, True),
        "archive_max_candles": (0, 1, 2.5, True),
    }
    for knob, values in bad.items():
        for value in values:
            with pytest.raises(AssetError, match=f"config.{knob}"):
                conn.resolve_knobs({**CONFIG, knob: value})
    with pytest.raises(AssetError, match="config must be a dict"):
        conn.resolve_knobs("nope")


def test_every_default_has_one_name(monkeypatch):
    # Each default is ONE module constant read by resolve_knobs AND quoted in
    # the spec() notes: rebind it and watch both move.
    pins = {
        "statuses": ("DEFAULT_STATUSES", ("settled", "closed", "open"), ["open"]),
        "limit": ("DEFAULT_LIMIT", 1000, 77),
        "max_pages": ("DEFAULT_MAX_PAGES", 10000, 5),
        "period_interval": ("DEFAULT_PERIOD_INTERVAL", 60, 1440),
        "pace_s": ("DEFAULT_PACE_S", 0.2, 0.9),
        "retries": ("DEFAULT_RETRIES", 4, 7),
        "timeout_s": ("DEFAULT_TIMEOUT_S", 30, 99),
        "base_url": ("DEFAULT_BASE_URL",
                     "https://external-api.kalshi.com/trade-api/v2",
                     "https://elsewhere.test/v2"),
        "candle_grouping": ("DEFAULT_CANDLE_GROUPING", "event", "batch"),
        "batch_size": ("DEFAULT_BATCH_SIZE", 100, 7),
        "max_candles": ("DEFAULT_MAX_CANDLES", 10000, 333),
        "archive_max_candles": ("DEFAULT_ARCHIVE_MAX_CANDLES", 5000, 444),
    }
    conn = KalshiHistoryConnector()
    for knob, (name, current, _sentinel) in pins.items():
        assert getattr(kalshi_history, name) == current, name
    for knob, (name, _current, sentinel) in pins.items():
        monkeypatch.setattr(kalshi_history, name, sentinel)
    knobs = conn.resolve_knobs({"series": [SERIES]})
    notes = conn.spec()["params"]
    for knob, (_name, _current, sentinel) in pins.items():
        assert knobs[knob] == (list(sentinel) if knob == "statuses" else sentinel)
        shown = list(sentinel) if knob == "statuses" else sentinel
        assert f"default {shown}" in notes[knob]["notes"], knob


def test_shared_defaults_agree_with_the_kalshi_pack():
    # The knobs both packs declare resolve to the same defaults: the new pack
    # imports kalshi's public DEFAULT_* names rather than restating literals.
    both = KalshiConnector().resolve_knobs({"series": [SERIES]})
    mine = KalshiHistoryConnector().resolve_knobs({"series": [SERIES]})
    for knob in ("limit", "max_pages", "period_interval", "pace_s", "retries",
                 "timeout_s", "base_url"):
        assert mine[knob] == both[knob], knob
    # The one deliberate difference: a closed-but-unsettled market is listed
    # too, so no market is ever unlisted between its close and its settlement.
    assert both["statuses"] == ["settled", "open"]
    assert mine["statuses"] == ["settled", "closed", "open"]


def test_all_lists_every_public_name_the_module_defines():
    tree = ast.parse(pathlib.Path(kalshi_history.__file__).read_text(encoding="utf-8"))
    imported = {
        (alias.asname or alias.name).split(".")[0]
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names}
    defined = {
        name for name in vars(kalshi_history)
        if not name.startswith("_") and name not in imported}
    assert defined <= set(kalshi_history.__all__), sorted(defined - set(kalshi_history.__all__))
    assert all(hasattr(kalshi_history, name) for name in kalshi_history.__all__)
    assert {kalshi_history.GROUPING_EVENT, kalshi_history.GROUPING_BATCH} == set(
        kalshi_history.CANDLE_GROUPINGS)


def test_base_url_loses_its_trailing_slash():
    conn = KalshiHistoryConnector()
    assert conn.resolve_knobs({"series": [SERIES], "base_url": BASE + "///"})["base_url"] == BASE


def test_constructor_refuses_non_callables():
    with pytest.raises(AssetError, match="getter must be callable"):
        KalshiHistoryConnector(getter="nope")
    with pytest.raises(AssetError, match="sleeper must be callable"):
        KalshiHistoryConnector(sleeper=1)
    with pytest.raises(AssetError, match="clock must be callable"):
        KalshiHistoryConnector(clock=3)


# -- check / discover / registration -------------------------------------------


def test_check_reads_the_cutoff_once():
    conn, script, sleeps = connector({})
    conn.check({"series": ["KXA", "KXB"], "base_url": BASE})
    assert script.calls == [("/historical/cutoff", {})]
    assert sleeps == []  # the connector's first request is never paced


@pytest.mark.parametrize("missing", ["market_settled_ts", "trades_created_ts"])
def test_check_refuses_a_cutoff_missing_either_boundary(missing):
    body = {k: v for k, v in CUTOFF_BODY.items() if k != missing}
    conn, _, _ = connector({"/historical/cutoff": body})
    with pytest.raises(AssetError, match=missing):
        conn.check(CONFIG)


def test_check_surfaces_a_failed_ping_and_a_bad_config():
    conn, script, _ = connector({"/historical/cutoff": http_error(404)})
    with pytest.raises(AssetError, match="HTTP 404"):
        conn.check(CONFIG)
    assert len(script.calls) == 1  # a client error never retries
    with pytest.raises(AssetError, match="config.series"):
        conn.check({"series": []})


def test_discover_declares_four_streams_offline():
    conn, script, _ = connector({})
    streams = conn.discover(CONFIG)
    assert script.calls == []
    assert [s["stream"] for s in streams] == [
        "candles", "markets", "orderbooks", "trades"]
    by_name = {s["stream"]: s for s in streams}
    assert by_name["markets"]["primary_key"] == ["ticker"]
    assert by_name["candles"]["primary_key"] == ["ticker", "ts"]
    assert by_name["orderbooks"]["primary_key"] == ["ticker", "captured_at"]
    assert by_name["trades"]["primary_key"] == ["trade_id"]
    assert by_name["markets"]["schema"]["fields"] == [
        "ticker", "event_ticker", "series_ticker", "strike_type", "floor_strike",
        "cap_strike", "status", "result", "open_time", "close_time",
        "yes_sub_title", "yes_bid", "yes_ask", "last_price",
        "expiration_value", "settlement_ts", "volume"]
    assert by_name["candles"]["schema"]["fields"] == [
        "ticker", "ts", "open", "high", "low", "close", "mean",
        "yes_bid_close", "yes_ask_close", "volume", "open_interest"]
    assert by_name["orderbooks"]["schema"]["fields"] == [
        "ticker", "event_ticker", "series_ticker", "captured_at", "yes_bids",
        "no_bids", "strike_type", "floor_strike", "cap_strike", "close_time",
        "observed_at"]
    assert by_name["trades"]["schema"]["fields"] == [
        "trade_id", "ticker", "created_time", "yes_price", "count", "taker_side"]
    with pytest.raises(AssetError, match="config.series"):
        conn.discover({"series": []})


def test_wired_by_import_path_and_no_registry_entry_added():
    ref = "dskit.onboarding.libs.kalshi_history:KalshiHistoryConnector"
    assert resolve_connector(ref) is KalshiHistoryConnector
    # Additive only: the registry names no new kind and still names kalshi.
    assert "kalshi_history" not in connector_module.DEFAULT_CONNECTORS
    assert connector_module.DEFAULT_CONNECTORS["kalshi"] == (
        "dskit.onboarding.libs.kalshi:KalshiConnector")
    assert not issubclass(KalshiHistoryConnector, KalshiConnector)


PACK_PACKAGE = "dskit.onboarding.libs"


def _is_module(dotted):
    """Report whether ``dotted`` names an importable dskit module (a name inside a module is not)."""
    try:
        return importlib.util.find_spec(dotted) is not None
    except (ImportError, ValueError):
        return False


class ForeignNames:
    """Every way a source reaches another dskit module's names, relative or absolute.

    ``modules`` is the set of dskit modules it imports from; ``unlisted`` the
    ``(module, name)`` pairs it uses that the module's ``__all__`` does not
    list (an import, or an attribute read off an imported module alias);
    ``pack_bases`` the base classes that come from a sibling pack.
    """

    def __init__(self, source, package=PACK_PACKAGE):
        self._package = package.split(".")
        self.modules, self.unlisted, self.pack_bases = set(), set(), []
        self._alias = {}   # local name -> the dotted dskit module it binds
        self._origin = {}  # local name -> the dotted dskit module it was imported from
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self._from(node)
            elif isinstance(node, ast.Import):
                self._import(node)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self._attribute(node)
            elif isinstance(node, ast.ClassDef):
                self._bases(node)

    def _listed(self, module, name):
        """Record ``name`` as unlisted unless ``module`` declares it in ``__all__``."""
        public = getattr(importlib.import_module(module), "__all__", ())
        if name not in public:
            self.unlisted.add((module, name))

    def _from(self, node):
        """Resolve a ``from`` import (relative or absolute) and check each name."""
        if node.level:
            parts = self._package[:len(self._package) - node.level + 1]
            target = ".".join(parts + ([node.module] if node.module else []))
        else:
            target = node.module or ""
        if target.split(".")[0] != "dskit":
            return
        self.modules.add(target)
        for item in node.names:
            local = item.asname or item.name
            if _is_module(f"{target}.{item.name}"):
                self._alias[local] = f"{target}.{item.name}"
                if item.name.startswith("_"):
                    self.unlisted.add((target, item.name))
            else:
                self._origin[local] = target
                self._listed(target, item.name)

    def _import(self, node):
        """Bind ``import a.b.c [as k]``; the attribute walk checks what is read off it."""
        for item in node.names:
            if item.name.split(".")[0] != "dskit":
                continue
            self.modules.add(item.name)
            if item.asname:
                self._alias[item.asname] = item.name
            else:
                self._alias[item.name.split(".")[0]] = item.name.split(".")[0]

    def _resolve(self, node):
        """``(module, name)`` an attribute chain reads, walking through submodules; None when unbound."""
        chain = []
        while isinstance(node, ast.Attribute):
            chain.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name) or node.id not in self._alias:
            return None
        module = self._alias[node.id]
        for attr in reversed(chain):
            if _is_module(f"{module}.{attr}"):
                module = f"{module}.{attr}"
                continue
            return module, attr
        return module, None

    def _attribute(self, node):
        """Check one attribute read off an imported dskit module."""
        found = self._resolve(node)
        if found and found[1] is not None:
            self._listed(*found)

    def _bases(self, node):
        """Record a base class that comes from a sibling pack of ``libs``."""
        for base in node.bases:
            module = None
            if isinstance(base, ast.Name):
                module = self._origin.get(base.id)
            elif isinstance(base, ast.Attribute):
                found = self._resolve(base)
                module = found[0] if found else None
            if module and module.startswith(PACK_PACKAGE + "."):
                self.pack_bases.append(ast.unparse(base))


def test_the_new_module_uses_only_public_names_of_other_modules():
    # The ADR's standalone ruling, pinned: a private name of another module
    # may change without notice, so none may be imported, called or subclassed
    # (relative or absolute import, or an attribute read off an imported module).
    source = pathlib.Path(kalshi_history.__file__).read_text(encoding="utf-8")
    seen = ForeignNames(source)
    assert seen.modules == {
        "dskit.onboarding.libs.kalshi", "dskit.onboarding.connector",
        "dskit.onboarding.base"}
    assert seen.unlisted == set()
    assert seen.pack_bases == []
    # The names it leans on really are in the other modules' public lists.
    for module in (kalshi, connector_module, base_module):
        assert module.__all__


@pytest.mark.parametrize("snippet, unlisted", [
    ("from .kalshi import _finite", {("dskit.onboarding.libs.kalshi", "_finite")}),
    ("from dskit.onboarding.libs.kalshi import _finite as _borrowed",
     {("dskit.onboarding.libs.kalshi", "_finite")}),
    ("from . import kalshi\nx = kalshi._price",
     {("dskit.onboarding.libs.kalshi", "_price")}),
    ("import dskit.onboarding.libs.kalshi as k\nx = k._price",
     {("dskit.onboarding.libs.kalshi", "_price")}),
    ("import dskit.onboarding.libs.kalshi\nx = dskit.onboarding.libs.kalshi._price",
     {("dskit.onboarding.libs.kalshi", "_price")}),
    ("from ..connector import _private_thing",
     {("dskit.onboarding.connector", "_private_thing")}),
    ("from .kalshi import *", {("dskit.onboarding.libs.kalshi", "*")}),
    ("from . import _helper", {("dskit.onboarding.libs", "_helper")}),
])
def test_the_public_names_checker_catches_every_import_form(snippet, unlisted):
    assert ForeignNames(snippet).unlisted == unlisted


@pytest.mark.parametrize("snippet", [
    "from .kalshi import CANDLE_FIELDS\nx = CANDLE_FIELDS",
    "from ..connector import Connector, PROTOCOL",
    "import json\nfrom collections import namedtuple",
    "from . import kalshi\nx = kalshi.CANDLE_FIELDS",
])
def test_the_public_names_checker_passes_public_use(snippet):
    assert ForeignNames(snippet).unlisted == set()


@pytest.mark.parametrize("snippet", [
    "from .kalshi import KalshiConnector\nclass X(KalshiConnector):\n    pass",
    "from . import kalshi\nclass X(kalshi.KalshiConnector):\n    pass",
    "import dskit.onboarding.libs.kalshi as k\nclass X(k.KalshiConnector):\n    pass",
])
def test_the_public_names_checker_catches_subclassing_a_sibling_pack(snippet):
    assert len(ForeignNames(snippet).pack_bases) == 1
    allowed = "from ..connector import Connector\nclass X(Connector):\n    pass"
    assert ForeignNames(allowed).pack_bases == []


# -- read: contract basics ------------------------------------------------------


def test_read_validates_its_arguments():
    conn, _, _ = connector({})
    with pytest.raises(AssetError, match="state must be a dict"):
        list(conn.read(CONFIG, ["markets"], [], "live"))
    with pytest.raises(AssetError, match="state.markets must be a dict"):
        list(conn.read(CONFIG, ["markets"], {"markets": "x"}, "live"))
    with pytest.raises(AssetError, match="streams must be a non-empty list"):
        list(conn.read(CONFIG, [], {}, "live"))
    with pytest.raises(AssetError, match="mode must be one of"):
        list(conn.read(CONFIG, ["markets"], {}, "nightly"))
    with pytest.raises(AssetError, match="unknown stream.*ghost"):
        list(conn.read(CONFIG, ["markets", "ghost"], {}, "live"))
    with pytest.raises(AssetError, match="unknown stream.*fee_schedules"):
        list(conn.read(CONFIG, ["fee_schedules"], {}, "live"))


@pytest.mark.parametrize("mode", ["backfill", "live"])
def test_read_emits_schema_records_state_in_either_mode(mode):
    conn, _, _ = connector({"/markets": by_status(), "/historical/markets": {"markets": []}})
    msgs = read(conn, ["markets"], mode=mode)
    assert [m["type"] for m in msgs] == ["SCHEMA", "STATE"]
    assert msgs[0]["schema"] == {"fields": list(kalshi_history.MARKET_FIELDS)}


def test_read_never_mutates_the_state_it_was_given():
    state = {"markets": {"cursor": "2026-10-07T13:00:00+00:00"}, "other": {"cursor": "x"}}
    before = json.loads(json.dumps(state))
    conn, _, _ = connector({"/markets": by_status(), "/historical/markets": {"markets": []}})
    msgs = read(conn, ["markets"], state=state)
    assert state == before
    assert msgs[-1]["state"] == before  # carried over, in a copy of its own
    assert msgs[-1]["state"]["other"] is not state["other"]
    assert msgs[-1]["state"]["markets"] is not state["markets"]


@pytest.mark.parametrize("stream", ["markets", "candles", "trades", "orderbooks"])
def test_a_naive_clock_is_refused_whatever_the_stream(stream):
    conn = KalshiHistoryConnector(
        getter=Script({}), sleeper=lambda s: None, clock=lambda: datetime(2026, 10, 7, 12))
    with pytest.raises(AssetError, match="clock must return an aware datetime"):
        list(conn.read(CONFIG, [stream], {}, "live"))


# -- the archive cutoff ----------------------------------------------------------


def test_cutoff_is_read_once_per_pull_across_streams():
    conn, script, _ = connector({
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(),
    })
    read(conn, ["markets", "candles", "trades"])
    assert script.paths().count("/historical/cutoff") == 1
    assert script.paths()[0] == "/historical/cutoff"


def test_the_cutoff_comes_from_the_venue_never_from_the_pack():
    # The same payloads route differently when the VENUE moves its cutoff: the
    # boundary lives in the response, not in the code. The archive and the live
    # listing both carry ``old`` (the overlap week): at the real cutoff the
    # live copy is a duplicate, so it is dropped; with the cutoff moved to the
    # very instant ``old`` settled, the live API owns it ("at the cutoff or
    # later is live") and its copy is kept.
    old = market("KXBTCD-26AUG0617-T1", settlement_ts="2026-08-06T21:02:21.8Z")
    routes = {
        "/historical/markets": {"markets": [old], "cursor": ""},
        "/markets": by_status(settled=[old]),
    }
    conn, _, _ = connector(routes)
    assert [d["ticker"] for d in data(read(conn, ["markets"]))] == [old["ticker"]]

    moved = {**CUTOFF_BODY, "market_settled_ts": old["settlement_ts"]}
    conn, _, _ = connector({**routes, "/historical/cutoff": moved})
    assert [d["ticker"] for d in data(read(conn, ["markets"]))] == [old["ticker"]] * 2


@pytest.mark.parametrize("body, message", [
    ({"trades_created_ts": CUTOFF}, "market_settled_ts"),
    ({"market_settled_ts": "soon", "trades_created_ts": CUTOFF}, "market_settled_ts"),
    ({"market_settled_ts": CUTOFF}, "trades_created_ts"),
    ({"market_settled_ts": CUTOFF, "trades_created_ts": 5}, "trades_created_ts"),
])
def test_a_malformed_cutoff_refuses_the_pull(body, message):
    conn, _, _ = connector({
        "/historical/cutoff": body,
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(),
    })
    with pytest.raises(AssetError, match=message):
        list(conn.read(CONFIG, ["markets", "trades"], {}, "live"))


def test_streams_that_need_no_archive_never_read_the_cutoff():
    # orderbooks are live only; a status list without 'settled' has no
    # archive leg, so markets need no boundary either.
    conn, script, _ = connector({
        "/markets": by_status(open=[]),
    })
    read(conn, ["orderbooks"])
    read(conn, ["markets"], config={**CONFIG, "statuses": ["open"]})
    assert "/historical/cutoff" not in script.paths()
    assert "/historical/markets" not in script.paths()


# -- markets ---------------------------------------------------------------------


OLD = market("KXBTCD-26AUG0617-T73749.99", event="KXBTCD-26AUG0617",
             open_time="2026-08-05T09:00:00Z", close_time="2026-08-06T21:00:00Z",
             expiration_value="64396.95", settlement_ts="2026-08-06T21:02:21.849381Z",
             volume_fp="1900430.27")
AT_CUTOFF = market("KXBTCD-26AUG0700-T1", event="KXBTCD-26AUG0700",
                   open_time="2026-08-06T23:00:00Z", close_time="2026-08-07T00:00:00Z",
                   settlement_ts="2026-08-07T00:00:00Z")
JUST_BEFORE = market("KXBTCD-26AUG0623-T1", event="KXBTCD-26AUG0623",
                     open_time="2026-08-06T22:00:00Z", close_time="2026-08-06T23:00:00Z",
                     settlement_ts="2026-08-06T23:59:59Z")
NEW = market("KXBTCD-26OCT0621-T95000", result="no", expiration_value="85735.80",
             settlement_ts="2026-10-07T01:02:20.5Z", volume_fp="3.00")
OPEN = market("KXBTCD-26OCT0713-T96000", event="KXBTCD-26OCT0713", status="active",
              result="", open_time="2026-10-07T12:00:00Z",
              close_time="2026-10-07T13:00:00Z", expiration_value="",
              settlement_ts=None, volume_fp="0.00",
              yes_bid_dollars="0.4000", yes_ask_dollars="0.4500",
              last_price_dollars="0.4200")


def history_pages(params):
    """Two archive pages chained by cursor."""
    if "cursor" not in params:
        return {"markets": [OLD], "cursor": "page-2"}
    assert params["cursor"] == "page-2"
    return {"markets": [JUST_BEFORE], "cursor": ""}


MARKET_ROUTES = {
    "/historical/markets": history_pages,
    # The live API still serves the overlap week: OLD and JUST_BEFORE are there too.
    "/markets": by_status(settled=[OLD, JUST_BEFORE, AT_CUTOFF, NEW],
                          closed=[], open=[OPEN]),
}


def test_markets_walk_every_live_status_in_lifecycle_order_then_the_archive():
    # open -> closed -> settled is the order a market moves through, so a scan
    # in that order sees a market that changes status mid-pull in SOME scan; the
    # archive is read last for the same reason (a market that settles live
    # during the pull may be archived by the time its scan runs).
    conn, script, _ = connector(MARKET_ROUTES)
    read(conn, ["markets"])
    common = {"series_ticker": SERIES, "limit": 1000}
    assert script.calls == [
        ("/historical/cutoff", {}),
        ("/markets", {**common, "status": "open"}),
        ("/markets", {**common, "status": "closed"}),
        ("/markets", {**common, "status": "settled"}),
        ("/historical/markets", common),                       # None cursor dropped
        ("/historical/markets", {**common, "cursor": "page-2"}),
    ]


@pytest.mark.parametrize("configured, scanned", [
    (["settled", "closed", "open"], ["open", "closed", "settled"]),
    (["closed", "settled", "open"], ["open", "closed", "settled"]),
    (["open", "settled"], ["open", "settled"]),
    (["settled", "paused", "open", "unopened", "closed"],
     ["unopened", "open", "paused", "closed", "settled"]),
    # A status the pack does not rank goes first, in the order configured.
    (["settled", "zzz", "aaa"], ["zzz", "aaa", "settled"]),
])
def test_the_scan_order_is_the_lifecycle_whatever_the_config_order(configured, scanned):
    conn, script, _ = connector({
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status()})
    read(conn, ["markets"], config={**CONFIG, "statuses": configured})
    assert [p["status"] for p in script.params("/markets")] == scanned


FLIP_X = market("KXBTCD-26OCT0710-T1", event="KXBTCD-26OCT0710", status="closed",
                result="", open_time="2026-10-07T10:00:00Z",
                close_time="2026-10-07T11:00:00Z", expiration_value="",
                settlement_ts="", volume_fp="4.00")
FLIP_X_SETTLED = {**FLIP_X, "status": "finalized", "result": "yes",
                  "expiration_value": "85000.00",
                  "settlement_ts": "2026-10-07T11:02:00Z"}
#: Closes an hour after FLIP_X and is settled throughout: its rows move a stored
#: cursor past FLIP_X's close, the situation in which a lost market stays lost.
FLIP_Y = market("KXBTCD-26OCT0711-T1", event="KXBTCD-26OCT0711",
                open_time="2026-10-07T11:00:00Z", close_time="2026-10-07T12:00:00Z",
                settlement_ts="2026-10-07T12:02:00Z")
FLIP_NOW = datetime(2026, 10, 7, 13, 0, 30, tzinfo=timezone.utc)


def flip_event_path(listed):
    return f"/series/{SERIES}/events/{listed['event_ticker']}/candlesticks"


def flipping_routes(flips_after, later=()):
    """Routes for one market that is closed until ``flips_after`` live scans were read, then settled.

    The venue answers a status scan from the market's status AT THE MOMENT
    of the read, as it does: a scan sees the market only while it holds that
    status. ``later`` markets are settled throughout.
    """
    scans = []

    def markets(params):
        status = "closed" if len(scans) < flips_after else "settled"
        scans.append(params["status"])
        listed = []
        if params["status"] == status:
            listed.append({"closed": FLIP_X, "settled": FLIP_X_SETTLED}[status])
        if params["status"] == "settled":
            listed.extend(later)
        return {"markets": listed, "cursor": ""}

    def trades(params):
        ticker = params["ticker"]
        last = {FLIP_X["ticker"]: FLIP_X["close_time"]}.get(ticker, FLIP_Y["close_time"])
        created = (parse_iso(last) - timedelta(seconds=1)).isoformat()
        return {"trades": [trade(f"t-{ticker}", created, ticker)], "cursor": ""}

    routes = {
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": markets,
        "/markets/trades": trades,
        "/historical/trades": {"trades": [], "cursor": ""},
    }
    for listed in (FLIP_X, *later):
        routes[flip_event_path(listed)] = (
            lambda p, t=listed["ticker"]: event_body(
                p["end_ts"], **{t: [live_candle(p["end_ts"])]}))
    return routes


@pytest.mark.parametrize("stream", ["markets", "candles", "trades"])
@pytest.mark.parametrize("flips_after", [0, 1, 2, 3])
def test_a_market_that_changes_status_while_the_pull_scans_is_never_lost(
        stream, flips_after):
    # F1: the scans take time. Whichever scan the change falls between, the
    # market is listed in at least one, is listed once overall, and the copy
    # kept is the one from the latest scan that saw it.
    conn, script, _ = connector(flipping_routes(flips_after), now=FLIP_NOW)
    rows = data(read(conn, [stream]))
    assert [r["ticker"] for r in rows] == [FLIP_X["ticker"]]
    if stream == "markets":
        assert rows[0]["status"] == ("closed" if flips_after == 3 else "finalized")
    if stream == "candles":
        assert script.paths().count(flip_event_path(FLIP_X)) == 1
    if stream == "trades":
        assert script.paths().count("/markets/trades") == 1


@pytest.mark.parametrize("stream", ["candles", "trades"])
def test_a_market_lost_in_one_pull_cannot_stay_lost_behind_the_cursor(stream):
    # The reproduction: X closes at 11:00, Y at 12:00, and X settles between
    # the first scan and the second. A pull that missed X emits only Y's rows,
    # stores a cursor past X's close, and the next pull never asks for X.
    conn, _, _ = connector(flipping_routes(1, later=(FLIP_Y,)), now=FLIP_NOW)
    first = read(conn, [stream])
    assert sorted({d["ticker"] for d in data(first)}) == [
        FLIP_X["ticker"], FLIP_Y["ticker"]]
    state = first[-1]["state"]
    assert parse_iso(state[stream]["cursor"]) > parse_iso(FLIP_X["close_time"])
    again, script, _ = connector(flipping_routes(0, later=(FLIP_Y,)), now=FLIP_NOW)
    read(again, [stream], state=state)
    # The first pull had X, so the second skipping it loses nothing.
    assert flip_event_path(FLIP_X) not in script.paths()
    assert FLIP_X["ticker"] not in [p["ticker"] for p in script.params("/markets/trades")]


ARCHIVING = market("KXBTCD-26AUG0610-T1", event="KXBTCD-26AUG0610",
                   open_time="2026-08-06T09:00:00Z", close_time="2026-08-06T10:00:00Z",
                   settlement_ts="2026-08-06T10:02:00Z")


def archiving_routes(moves_after):
    """Routes for one market that the live API stops listing, and the archive starts, after ``moves_after`` reads.

    Settled before the cutoff, so the archive owns it once it is there. The
    move is atomic, as the venue's is: no read sees it in both or neither.
    """
    reads = []

    def moved():
        reads.append(1)
        return len(reads) > moves_after

    live = ARCHIVING["event_ticker"]
    return {
        "/historical/markets": lambda p: {
            "markets": [ARCHIVING] if moved() else [], "cursor": ""},
        "/markets": lambda p: {
            "markets": [] if moved() or p["status"] != "settled" else [ARCHIVING],
            "cursor": ""},
        f"/series/{SERIES}/events/{live}/candlesticks": lambda p: event_body(
            p["end_ts"], **{ARCHIVING["ticker"]: [live_candle("2026-08-06T09:59:00Z")]}),
        f"/historical/markets/{ARCHIVING['ticker']}/candlesticks": {
            "candlesticks": [archive_candle("2026-08-06T09:59:00Z")]},
    }


@pytest.mark.parametrize("stream", ["markets", "candles"])
@pytest.mark.parametrize("moves_after", [0, 1, 2, 3, 4])
def test_a_market_archived_while_the_pull_scans_is_never_lost(stream, moves_after):
    # Three live scans then the archive's: the archive is read LAST, so a market
    # that leaves the live listing for the archive between two scans is still
    # in the archive's. Listed live and archived too, it is kept once.
    conn, script, _ = connector(archiving_routes(moves_after))
    rows = data(read(conn, [stream]))
    assert [r["ticker"] for r in rows] == [ARCHIVING["ticker"]]
    if stream == "candles":
        assert len([p for p in script.paths() if p.endswith("/candlesticks")]) == 1


@pytest.mark.parametrize("stream", ["candles", "trades"])
def test_every_listing_request_precedes_every_per_market_request(stream):
    # Per-market requests used to sit BETWEEN the status scans, hours apart on
    # a backfill: the listing is one pass, and the work follows it.
    conn, script, _ = connector({
        **trade_routes(**{
            "/historical/trades": {"trades": [], "cursor": ""},
            "/markets/trades": {"trades": [], "cursor": ""}}),
        f"/historical/markets/{OLD['ticker']}/candlesticks": {"candlesticks": []},
        f"/series/{SERIES}/events/{NEW['event_ticker']}/candlesticks": quiet_event(NEW),
        f"/series/{SERIES}/events/{STRADDLE['event_ticker']}/candlesticks":
            quiet_event(STRADDLE),
    })
    read(conn, [stream])
    paths = script.paths()
    listing = [i for i, path in enumerate(paths) if path in ("/markets", "/historical/markets")]
    work = [i for i, path in enumerate(paths)
            if i not in listing and path != "/historical/cutoff"]
    assert listing and work
    assert max(listing) < min(work)


def test_markets_each_market_belongs_to_one_archive_and_the_boundary_day():
    conn, _, _ = connector(MARKET_ROUTES)
    rows = data(read(conn, ["markets"]))
    # OLD and JUST_BEFORE settled before the cutoff: the archive owns them, so
    # the live overlap copies are not re-emitted. A market settled AT the
    # cutoff instant belongs to the live API (the venue: "before" is archived).
    assert [r["ticker"] for r in rows] == [
        OLD["ticker"], JUST_BEFORE["ticker"], AT_CUTOFF["ticker"],
        NEW["ticker"], OPEN["ticker"]]


def test_markets_a_live_copy_the_archive_does_not_hold_is_kept_not_dropped():
    # The date rule says the archive owns every market settled before the
    # cutoff, so the live overlap copy is a duplicate. If the archive lags and
    # does not list one, dropping the live copy would lose the market from
    # every stream with no error: it is kept, and requested through the live
    # endpoints (the archive has nothing to serve).
    lagging = market("KXBTCD-26AUG0623-T9", event="KXBTCD-26AUG0623",
                     open_time="2026-08-06T22:00:00Z", close_time="2026-08-06T23:00:00Z",
                     settlement_ts="2026-08-06T23:02:00Z")
    event_path = f"/series/{SERIES}/events/{lagging['event_ticker']}/candlesticks"
    conn, script, _ = connector({
        "/historical/markets": {"markets": [OLD], "cursor": ""},
        "/markets": by_status(settled=[OLD, lagging]),
        f"/historical/markets/{OLD['ticker']}/candlesticks": {"candlesticks": []},
        event_path: event_body(unix(lagging["close_time"]), **{lagging["ticker"]: []}),
    })
    msgs = read(conn, ["markets", "candles"])
    tickers = [d["ticker"] for d in data(msgs) if "close" not in d]
    assert tickers == [OLD["ticker"], lagging["ticker"]]  # each once, none lost
    assert event_path in script.paths()  # served live: the archive does not hold it
    assert script.paths().count(f"/historical/markets/{OLD['ticker']}/candlesticks") == 1


@pytest.mark.parametrize("settlement_ts", ["2026-08-07T00:00:00Z", "", None])
def test_markets_a_live_copy_is_dropped_only_with_evidence_it_settled_before_the_cutoff(
        settlement_ts):
    # Listed by the archive too, but settled AT the cutoff or not (yet) dated:
    # nothing says the archive owns it, so the live copy is not dropped.
    odd = market("KXBTCD-26AUG0700-T9", settlement_ts=settlement_ts)
    conn, _, _ = connector({
        "/historical/markets": {"markets": [odd], "cursor": ""},
        "/markets": by_status(settled=[odd])})
    assert [d["ticker"] for d in data(read(conn, ["markets"]))] == [odd["ticker"]] * 2


def test_markets_add_the_three_settlement_fields():
    conn, _, _ = connector(MARKET_ROUTES)
    rows = {r["ticker"]: r for r in data(read(conn, ["markets"]))}
    assert rows[OLD["ticker"]]["expiration_value"] == 64396.95
    assert rows[OLD["ticker"]]["settlement_ts"] == "2026-08-06T21:02:21.849381Z"
    assert rows[OLD["ticker"]]["volume"] == 1900430.27
    assert rows[NEW["ticker"]]["volume"] == 3.0
    # Not yet settled: an empty value is None, a missing instant is "".
    assert rows[OPEN["ticker"]]["expiration_value"] is None
    assert rows[OPEN["ticker"]]["settlement_ts"] == ""
    assert list(rows[OLD["ticker"]]) == list(kalshi_history.MARKET_FIELDS)
    for row in rows.values():
        json.dumps(row, allow_nan=False)


@pytest.mark.parametrize("value, expected", [
    ("85735.80", 85735.8), (85735.8, 85735.8), ("", None), (None, None),
    ("Boston", None), ("nan", None), ("inf", None), (True, None),
])
def test_expiration_value_is_a_finite_float_or_none(value, expected):
    odd = market("KXBTCD-26OCT0621-T1", expiration_value=value)
    conn, _, _ = connector({
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(settled=[odd]),
    })
    assert data(read(conn, ["markets"]))[0]["expiration_value"] == expected


def test_markets_effective_is_close_time_or_the_capture_minute():
    conn, _, _ = connector(MARKET_ROUTES)
    msgs = read(conn, ["markets"])
    effs = {m["data"]["ticker"]: m["effective_date"] for m in records(msgs)}
    assert effs[OLD["ticker"]] == "2026-08-06T21:00:00Z"
    assert effs[OPEN["ticker"]] == CAPTURE  # its close lies in the future
    assert msgs[-1]["state"] == {"markets": {"cursor": CAPTURE}}


def test_markets_never_filter_by_cursor():
    conn, _, _ = connector(MARKET_ROUTES)
    later = "2026-10-07T13:00:00+00:00"
    msgs = read(conn, ["markets"], state={"markets": {"cursor": later}})
    assert len(records(msgs)) == 5  # a full re-pull, by design
    assert msgs[-1]["state"] == {"markets": {"cursor": later}}


def test_markets_without_settled_never_touch_the_archive():
    conn, script, _ = connector({"/markets": by_status(open=[OPEN])})
    rows = data(read(conn, ["markets"], config={**CONFIG, "statuses": ["open"]}))
    assert [r["ticker"] for r in rows] == [OPEN["ticker"]]
    assert script.paths() == ["/markets"]


def test_markets_refuse_a_stuck_or_truncated_walk_in_either_archive():
    for path in ("/historical/markets", "/markets"):
        routes = {"/historical/markets": {"markets": [], "cursor": ""},
                  "/markets": by_status()}
        routes[path] = lambda p: {"markets": [OLD], "cursor": "same"}
        conn, _, _ = connector(routes)
        with pytest.raises(AssetError, match="did not advance"):
            list(conn.read(CONFIG, ["markets"], {}, "live"))

        counter = iter(range(10 ** 6))
        routes[path] = lambda p: {"markets": [OLD], "cursor": f"c{next(counter)}"}
        conn, _, _ = connector(routes)
        with pytest.raises(AssetError, match="still paging after 3 page"):
            list(conn.read({**CONFIG, "max_pages": 3}, ["markets"], {}, "live"))


def test_markets_an_empty_page_ends_the_walk_even_with_a_cursor():
    # The same rule as the kalshi pack's: no rows on a page means the end.
    conn, script, _ = connector({
        "/historical/markets": {"markets": [], "cursor": "more"},
        "/markets": by_status()})
    assert data(read(conn, ["markets"])) == []
    assert script.paths().count("/historical/markets") == 1


def test_markets_refuse_malformed_pages():
    live = {"/markets": by_status()}  # the live scans run (and pass) before the archive's
    conn, _, _ = connector({
        "/historical/markets": {"markets": [{"event_ticker": "X"}], "cursor": ""}, **live})
    with pytest.raises(AssetError, match="lacks a ticker"):
        list(conn.read(CONFIG, ["markets"], {}, "live"))
    conn, _, _ = connector({"/historical/markets": {"markets": {"not": "a list"}}, **live})
    with pytest.raises(AssetError, match="'markets' is not a list"):
        list(conn.read(CONFIG, ["markets"], {}, "live"))
    conn, _, _ = connector({
        "/historical/markets": {"markets": ["str"], "cursor": ""}, **live})
    with pytest.raises(AssetError, match="not a dict"):
        list(conn.read(CONFIG, ["markets"], {}, "live"))
    # A live page is refused the same way, whichever status it came from.
    conn, _, _ = connector({
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(closed=[{"event_ticker": "X"}])})
    with pytest.raises(AssetError, match="status 'closed'.*lacks a ticker"):
        list(conn.read(CONFIG, ["markets"], {}, "live"))


# -- candles: the archive ----------------------------------------------------------


def live_candle(end_iso, close="0.55", volume="12.5"):
    """A live-shaped candlestick: ``*_dollars`` prices and ``*_fp`` counts."""
    ts = end_iso if isinstance(end_iso, int) else unix(end_iso)
    return {
        "end_period_ts": ts,
        "price": {"open_dollars": "0.50", "high_dollars": "0.60",
                  "low_dollars": "0.40", "close_dollars": close,
                  "mean_dollars": "0.52"},
        "yes_bid": {"close_dollars": "0.54"},
        "yes_ask": {"close_dollars": "0.56"},
        "volume_fp": volume,
        "open_interest_fp": "100",
    }


def archive_candle(end_iso, close="0.55", volume="12.5"):
    """The ARCHIVE's spelling of the same candlestick: no ``_dollars``/``_fp``."""
    ts = end_iso if isinstance(end_iso, int) else unix(end_iso)
    return {
        "end_period_ts": ts,
        "price": {"open": "0.50", "high": "0.60", "low": "0.40", "close": close,
                  "mean": "0.52", "previous": None},
        "yes_bid": {"close": "0.54"},
        "yes_ask": {"close": "0.56"},
        "volume": volume,
        "open_interest": "100",
    }


CANDLE_ROW = {
    "ticker": OLD["ticker"], "ts": unix("2026-08-06T20:00:00Z"),
    "open": 0.5, "high": 0.6, "low": 0.4, "close": 0.55, "mean": 0.52,
    "yes_bid_close": 0.54, "yes_ask_close": 0.56, "volume": 12.5,
    "open_interest": 100.0,
}


def archive_routes(candles):
    return {
        "/historical/markets": {"markets": [OLD], "cursor": ""},
        "/markets": by_status(),
        f"/historical/markets/{OLD['ticker']}/candlesticks": {"candlesticks": candles},
    }


def test_archive_candles_use_the_archive_path_and_shape():
    conn, script, _ = connector(archive_routes([archive_candle("2026-08-06T20:00:00Z")]))
    msgs = read(conn, ["candles"])
    assert script.params(f"/historical/markets/{OLD['ticker']}/candlesticks") == [{
        "start_ts": unix(OLD["open_time"]), "end_ts": unix(OLD["close_time"]),
        "period_interval": 1}]
    assert data(msgs) == [CANDLE_ROW]
    assert records(msgs)[0]["effective_date"] == "2026-08-06T20:00:00+00:00"
    # The same candlestick spelled the live way gives the identical row.
    conn, _, _ = connector(archive_routes([live_candle("2026-08-06T20:00:00Z")]))
    assert data(read(conn, ["candles"])) == [CANDLE_ROW]


def test_archive_candles_unusable_shapes_refuse_or_none():
    conn, _, _ = connector(archive_routes([{"price": {}}]))
    with pytest.raises(AssetError, match="numeric end_period_ts"):
        list(conn.read(CONFIG, ["candles"], {}, "live"))
    conn, _, _ = connector(archive_routes(["nope"]))
    with pytest.raises(AssetError, match="not a dict"):
        list(conn.read(CONFIG, ["candles"], {}, "live"))
    routes = archive_routes([])
    routes[f"/historical/markets/{OLD['ticker']}/candlesticks"] = {"candlesticks": {"x": 1}}
    conn, _, _ = connector(routes)
    with pytest.raises(AssetError, match="'candlesticks' is not a list"):
        list(conn.read(CONFIG, ["candles"], {}, "live"))
    empty_price = archive_candle("2026-08-06T20:00:00Z")
    empty_price["price"] = {"close": None}
    conn, _, _ = connector(archive_routes([empty_price]))
    row = data(read(conn, ["candles"]))[0]
    assert row["close"] is None and row["open"] is None and row["volume"] == 12.5


def test_candle_dollar_spelling_wins_and_the_bare_spelling_is_a_guarded_fallback():
    candle = {
        "end_period_ts": unix("2026-08-06T20:00:00Z"),
        "price": {
            "close_dollars": "0.55", "close": "0.99",     # both spellings: dollars win
            "open_dollars": None, "open": "0.40",         # dollars absent: the bare one
            "high_dollars": None, "high": 62,             # bare but cents-sized: not a price
            "low_dollars": "nan", "low": "0.10",          # dollars present, unusable: None
            "mean": "1.0",                                # the bare boundary is kept
        },
        "yes_bid": {"close_dollars": "62", "close": "0.5"},  # live spelling: unguarded, as kalshi
        "yes_ask": {"close": -0.01},
        "volume_fp": "7", "volume": "99",
        "open_interest_fp": None, "open_interest": "100",
    }
    conn, _, _ = connector(archive_routes([candle]))
    row = data(read(conn, ["candles"]))[0]
    assert {k: row[k] for k in (
        "open", "high", "low", "close", "mean", "yes_bid_close", "yes_ask_close",
        "volume", "open_interest")} == {
        "open": 0.4, "high": None, "low": None, "close": 0.55, "mean": 1.0,
        "yes_bid_close": 62.0, "yes_ask_close": None, "volume": 7.0,
        "open_interest": 100.0}


def test_candle_timestamps_are_whole_seconds():
    floaty = {**archive_candle(unix("2026-08-06T20:00:00Z")),
              "end_period_ts": unix("2026-08-06T20:00:00Z") + 0.0}
    conn, _, _ = connector(archive_routes([floaty]))
    ts = data(read(conn, ["candles"]))[0]["ts"]
    assert type(ts) is int and ts == unix("2026-08-06T20:00:00Z")


@pytest.mark.parametrize("ts", [1e30, -1e30, 1e18, 253402300800])
def test_candles_an_unrepresentable_end_period_ts_refuses_by_name(ts):
    conn, _, _ = connector(archive_routes([{"end_period_ts": ts, "price": {}}]))
    with pytest.raises(AssetError, match="end_period_ts"):
        list(conn.read(CONFIG, ["candles"], {}, "live"))


def test_archive_candles_slice_a_life_longer_than_the_venues_ceiling():
    # The archive endpoint answers HTTP 400 to a window of more than 5000
    # periods (probed 2026-10-07), so a longer life is requested in slices that
    # share their boundary instant, and the boundary candle is emitted once.
    long_lived = {**OLD, "open_time": "2026-08-01T00:00:00Z"}
    path = f"/historical/markets/{OLD['ticker']}/candlesticks"
    calls = []

    def answer(params):
        calls.append(params)
        return {"candlesticks": [archive_candle(params["start_ts"]),
                                 archive_candle(params["end_ts"])]}

    conn, _, _ = connector({
        "/historical/markets": {"markets": [long_lived], "cursor": ""},
        "/markets": by_status(), path: answer})
    rows = data(read(conn, ["candles"]))
    start, close = unix(long_lived["open_time"]), unix(OLD["close_time"])
    boundary = start + (kalshi_history.DEFAULT_ARCHIVE_MAX_CANDLES - 1) * 60
    assert [(c["start_ts"], c["end_ts"]) for c in calls] == [
        (start, boundary), (boundary, close)]
    for call in calls:  # the venue counts (end - start) / period against its ceiling
        assert (call["end_ts"] - call["start_ts"]) / 60 <= 5000
    assert [r["ts"] for r in rows] == [start, boundary, close]


def test_archive_candle_budget_is_a_knob():
    path = f"/historical/markets/{OLD['ticker']}/candlesticks"
    calls = []

    def answer(params):
        calls.append(params)
        return {"candlesticks": []}

    conn, _, _ = connector({
        "/historical/markets": {"markets": [OLD], "cursor": ""},
        "/markets": by_status(), path: answer})
    read(conn, ["candles"], config={**CONFIG, "archive_max_candles": 301})
    # OLD lives 09:00 the day before to 21:00: 36 h = 2160 one-minute periods.
    windows = [(c["start_ts"], c["end_ts"]) for c in calls]
    assert len(windows) == 8 and windows[0][1] - windows[0][0] == 300 * 60
    assert windows[0][0] == unix(OLD["open_time"]) and windows[-1][1] == unix(OLD["close_time"])
    assert all(a[1] == b[0] for a, b in zip(windows, windows[1:]))


def test_candles_skip_a_market_closed_at_or_before_the_cursor():
    conn, script, _ = connector(archive_routes([archive_candle("2026-08-06T20:00:00Z")]))
    state = {"candles": {"cursor": OLD["close_time"]}}
    msgs = read(conn, ["candles"], state=state)
    assert data(msgs) == []
    assert not [p for p in script.paths() if "candlesticks" in p]
    assert msgs[-1]["state"] == {"candles": {"cursor": OLD["close_time"]}}


# -- candles: event level ------------------------------------------------------------

S0 = unix("2026-10-07T00:00:00Z")
E0 = unix("2026-10-07T01:00:00Z")
EVENT_PATH = f"/series/{SERIES}/events/{EVENT}/candlesticks"
A = market("KXBTCD-26OCT0621-T95000")
B = market("KXBTCD-26OCT0621-T96000")


def event_body(adjusted, **per_ticker):
    tickers = list(per_ticker)
    return {"adjusted_end_ts": adjusted, "market_tickers": tickers,
            "market_candlesticks": [per_ticker[t] for t in tickers]}


def quiet_event(*markets):
    """An event answer that carries no candles: every strike of ``markets`` present, each empty."""
    return lambda p: event_body(p["end_ts"], **{m["ticker"]: [] for m in markets})


def live_routes(spans=(A, B), **extra):
    return {
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(settled=list(spans)),
        **extra,
    }


def test_event_candles_follow_adjusted_end_ts_until_the_window_is_covered():
    bodies = [
        event_body(S0 + 1560, **{
            A["ticker"]: [live_candle(S0 + 60), live_candle(S0 + 1560)],
            B["ticker"]: [live_candle(S0 + 60, close="0.30")]}),
        event_body(S0 + 3120, **{
            A["ticker"]: [live_candle(S0 + 1560), live_candle(S0 + 3120)],
            B["ticker"]: []}),
        event_body(E0, **{
            A["ticker"]: [live_candle(S0 + 3120), live_candle(E0)],
            B["ticker"]: [live_candle(E0, close="0.31")]}),
    ]
    conn, script, _ = connector(live_routes(**{EVENT_PATH: bodies}))
    msgs = read(conn, ["candles"])
    assert script.params(EVENT_PATH) == [
        {"start_ts": S0, "end_ts": E0, "period_interval": 1},
        {"start_ts": S0 + 1560, "end_ts": E0, "period_interval": 1},
        {"start_ts": S0 + 3120, "end_ts": E0, "period_interval": 1},
    ]
    got = [(d["ticker"], d["ts"]) for d in data(msgs)]
    # The candle AT each adjusted_end_ts comes back in the next call too; it is
    # emitted once. One request chain per EVENT, not per market.
    assert got == [
        (A["ticker"], S0 + 60), (A["ticker"], S0 + 1560),
        (B["ticker"], S0 + 60),
        (A["ticker"], S0 + 3120),
        (A["ticker"], E0), (B["ticker"], E0),
    ]
    assert data(msgs)[2]["close"] == 0.30
    assert msgs[-1]["state"] == {"candles": {"cursor": "2026-10-07T01:00:00+00:00"}}


def test_event_candles_one_request_chain_per_event_and_known_tickers_only():
    other = market("KXBTCD-26OCT0622-T1", event="KXBTCD-26OCT0622",
                   open_time="2026-10-07T01:00:00Z", close_time="2026-10-07T02:00:00Z")
    other_path = f"/series/{SERIES}/events/KXBTCD-26OCT0622/candlesticks"
    s1, e1 = E0, unix("2026-10-07T02:00:00Z")
    routes = live_routes(spans=(A, other), **{
        EVENT_PATH: event_body(E0, **{
            A["ticker"]: [live_candle(S0 + 60)],
            "KXBTCD-26OCT0621-TUNLISTED": [live_candle(S0 + 60)]}),
        other_path: event_body(e1, **{other["ticker"]: [live_candle(s1 + 60)]}),
    })
    conn, script, _ = connector(routes)
    rows = data(read(conn, ["candles"]))
    assert [r["ticker"] for r in rows] == [A["ticker"], other["ticker"]]
    assert script.paths().count(EVENT_PATH) == 1 and script.paths().count(other_path) == 1


def test_event_window_spans_its_markets_and_is_clamped_to_the_capture():
    early = market("KXBTCD-26OCT0621-T1", open_time="2026-10-07T00:10:00Z")
    now = datetime(2026, 10, 7, 0, 30, 30, tzinfo=timezone.utc)
    routes = live_routes(spans=(A, early), **{
        EVENT_PATH: event_body(S0 + 1800, **{
            A["ticker"]: [live_candle(S0 + 600), live_candle(S0 + 1860)],
            early["ticker"]: []}),
    })
    conn, script, _ = connector(routes, now=now)
    rows = data(read(conn, ["candles"]))
    assert script.params(EVENT_PATH) == [
        {"start_ts": S0, "end_ts": S0 + 1800, "period_interval": 1}]
    # The candle after the capture instant (00:30:00) is still forming: dropped.
    assert [r["ts"] for r in rows] == [S0 + 600]


def test_event_window_runs_to_the_last_member_to_close():
    # Strikes of one event may close at different times: the request must reach
    # the latest close, not the first, or the later candles are never asked for.
    late = market("KXBTCD-26OCT0621-T97000", close_time="2026-10-07T02:00:00Z")
    last = unix("2026-10-07T02:00:00Z")
    routes = live_routes(spans=(A, late), **{
        EVENT_PATH: event_body(last, **{
            A["ticker"]: [live_candle(S0 + 60)],
            late["ticker"]: [live_candle(last)]})})
    conn, script, _ = connector(routes)
    rows = data(read(conn, ["candles"]))
    assert script.params(EVENT_PATH) == [
        {"start_ts": S0, "end_ts": last, "period_interval": 1}]
    assert [(r["ticker"], r["ts"]) for r in rows] == [
        (A["ticker"], S0 + 60), (late["ticker"], last)]


def test_a_candle_ending_exactly_at_the_capture_is_complete_one_second_later_is_not():
    now = datetime(2026, 10, 7, 0, 30, 30, tzinfo=timezone.utc)  # capture 00:30:00
    routes = live_routes(spans=(A,), **{
        EVENT_PATH: event_body(S0 + 1800, **{A["ticker"]: [
            live_candle(S0 + 1799), live_candle(S0 + 1800), live_candle(S0 + 1801)]})})
    conn, _, _ = connector(routes, now=now)
    assert [r["ts"] for r in data(read(conn, ["candles"]))] == [S0 + 1799, S0 + 1800]


@pytest.mark.parametrize("body, message", [
    (event_body(S0 + 1560, **{A["ticker"]: [], B["ticker"]: []}),
     "no progress"),                          # adjusted equals the next start
    ({"adjusted_end_ts": E0, "market_tickers": ["a"], "market_candlesticks": []},
     "misaligned"),
    ({"adjusted_end_ts": E0, "market_tickers": [], "market_candlesticks": [[]]},
     "misaligned"),                                  # more lists than tickers
    ({"adjusted_end_ts": E0, "market_tickers": "a", "market_candlesticks": [[]]},
     "market_tickers"),
    ({"market_tickers": [], "market_candlesticks": []}, "adjusted_end_ts"),
    ({"adjusted_end_ts": "soon", "market_tickers": [], "market_candlesticks": []},
     "adjusted_end_ts"),
    ({"adjusted_end_ts": E0, "market_tickers": ["a"],
      "market_candlesticks": [{"x": 1}]}, "not a list"),
])
def test_event_candles_refuse_what_they_cannot_follow(body, message):
    bodies = [body]
    if message == "no progress":
        bodies = [body, body]
    conn, _, _ = connector(live_routes(**{EVENT_PATH: bodies}))
    with pytest.raises(AssetError, match=message):
        list(conn.read(CONFIG, ["candles"], {}, "live"))


def test_event_candles_refuse_a_response_that_omits_a_listed_market():
    # Refuse, never truncate: B was listed and asked for, so a response without
    # it is refused as the batch path refuses one (and not skipped for good by
    # a cursor a later pull stores).
    conn, _, _ = connector(live_routes(**{EVENT_PATH: event_body(E0, **{A["ticker"]: []})}))
    with pytest.raises(AssetError, match="lacks.*T96000"):
        list(conn.read(CONFIG, ["candles"], {}, "live"))
    # Also a later response of the chain.
    bodies = [event_body(S0 + 1560, **{A["ticker"]: [], B["ticker"]: []}),
              event_body(E0, **{A["ticker"]: []})]
    conn, _, _ = connector(live_routes(**{EVENT_PATH: bodies}))
    with pytest.raises(AssetError, match="lacks.*T96000"):
        list(conn.read(CONFIG, ["candles"], {}, "live"))


def test_event_candles_still_ignore_a_market_the_pull_did_not_list():
    extra = "KXBTCD-26OCT0621-TEXTRA"
    conn, _, _ = connector(live_routes(**{EVENT_PATH: event_body(E0, **{
        A["ticker"]: [live_candle(S0 + 60)], B["ticker"]: [],
        extra: [live_candle(S0 + 60)]})}))
    assert [r["ticker"] for r in data(read(conn, ["candles"]))] == [A["ticker"]]


def test_event_candles_refuse_a_market_with_no_event_to_group_by():
    orphan = market("KXBTCD-26OCT0621-T1", event="")
    conn, _, _ = connector(live_routes(spans=(orphan,)))
    with pytest.raises(AssetError, match="event_ticker.*batch"):
        list(conn.read(CONFIG, ["candles"], {}, "live"))


def test_event_candles_skip_markets_closed_by_the_cursor():
    conn, script, _ = connector(live_routes(**{EVENT_PATH: event_body(E0)}))
    state = {"candles": {"cursor": "2026-10-07T01:00:00+00:00"}}
    msgs = read(conn, ["candles"], state=state)
    assert EVENT_PATH not in script.paths()
    assert data(msgs) == []


# -- candles: ticker batches ------------------------------------------------------------

BATCH_PATH = "/markets/candlesticks"
BATCH = {**CONFIG, "candle_grouping": "batch"}


def batch_body(**per_ticker):
    return {"markets": [{"market_ticker": t, "candlesticks": c}
                        for t, c in per_ticker.items()]}


def test_batch_candles_answer_by_ticker_whatever_the_response_order():
    # The venue answers in ITS order, not the request's: rows are keyed by
    # market_ticker, never by position.
    conn, script, _ = connector(live_routes(**{BATCH_PATH: batch_body(**{
        B["ticker"]: [live_candle(S0 + 60, close="0.30")],
        A["ticker"]: [live_candle(S0 + 60)]})}))
    rows = data(read(conn, ["candles"], config=BATCH))
    assert script.params(BATCH_PATH) == [{
        "market_tickers": f"{A['ticker']},{B['ticker']}",
        "start_ts": S0, "end_ts": E0, "period_interval": 1}]
    assert {(r["ticker"], r["close"]) for r in rows} == {
        (A["ticker"], 0.55), (B["ticker"], 0.30)}


def test_batch_candles_respect_the_batch_size_and_the_candle_budget():
    markets = [market(f"KXBTCD-26OCT0621-T{i}") for i in range(5)]
    seen = []

    def answer(params):
        tickers = params["market_tickers"].split(",")
        seen.append(tickers)
        return batch_body(**{t: [] for t in tickers})

    conn, script, _ = connector(live_routes(spans=markets, **{BATCH_PATH: answer}))
    read(conn, ["candles"], config={**BATCH, "batch_size": 2})
    assert [len(t) for t in seen] == [2, 2, 1]

    # 60 one-minute periods a market + the inclusive end = 61; a budget of 130
    # holds two markets, not three, whatever batch_size allows.
    seen.clear()
    conn, script, _ = connector(live_routes(spans=markets, **{BATCH_PATH: answer}))
    read(conn, ["candles"], config={**BATCH, "max_candles": 130})
    assert [len(t) for t in seen] == [2, 2, 1]
    for call in script.params(BATCH_PATH):
        n = len(call["market_tickers"].split(","))
        assert n * ((call["end_ts"] - call["start_ts"]) // 60 + 1) <= 130


@pytest.mark.parametrize("budget, requests", [(122, 1), (121, 2)])
def test_batch_candles_a_chunk_one_period_over_the_budget_is_split(budget, requests):
    # Two markets of 61 periods each ask for 122 candles: it fits a budget of
    # 122 and is one over a budget of 121 (each alone fits, so they split).
    markets = [market(f"KXBTCD-26OCT0621-T{i}") for i in range(2)]
    calls = []

    def answer(params):
        calls.append(params)
        return batch_body(**{t: [] for t in params["market_tickers"].split(",")})

    conn, _, _ = connector(live_routes(spans=markets, **{BATCH_PATH: answer}))
    read(conn, ["candles"], config={**BATCH, "max_candles": budget})
    assert len(calls) == requests


def test_batch_candles_chain_back_to_back_markets_within_the_budget():
    # Fifteen-minute markets one after another: a request covers consecutive
    # markets while (markets x periods across the union window) fits the budget.
    day = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
    markets = []
    for i in range(6):
        opened = day + timedelta(minutes=15 * i)
        closed = opened + timedelta(minutes=15)
        markets.append(market(
            f"KXBTC15M-26OCT07{i:02d}-0", event=f"KXBTC15M-26OCT07{i:02d}",
            open_time=opened.isoformat().replace("+00:00", "Z"),
            close_time=closed.isoformat().replace("+00:00", "Z")))
    calls = []

    def answer(params):
        calls.append(params)
        return batch_body(**{t: [] for t in params["market_tickers"].split(",")})

    routes = {
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(settled=markets),
        BATCH_PATH: answer,
    }
    conn, _, _ = connector(routes)
    # 3 markets x (45 min + 1 = 46 periods) = 138 <= 140; a 4th would be 4 x 61.
    read(conn, ["candles"], config={**BATCH, "series": ["KXBTC15M"], "max_candles": 140})
    sizes = [len(c["market_tickers"].split(",")) for c in calls]
    assert sizes == [3, 3]
    assert calls[0]["start_ts"] == int(day.timestamp())


def test_batch_candles_slice_a_window_longer_than_the_budget():
    long_lived = market("KXBTCD-26OCT0621-TLONG", open_time="2026-10-07T00:00:00Z",
                        close_time="2026-10-07T03:00:00Z")
    calls = []

    def answer(params):
        calls.append(params)
        return batch_body(**{params["market_tickers"]: [
            live_candle(params["start_ts"]), live_candle(params["end_ts"])]})

    conn, _, _ = connector(live_routes(spans=(long_lived,), **{BATCH_PATH: answer}))
    rows = data(read(conn, ["candles"], config={**BATCH, "max_candles": 61}))
    # 3 h = 180 periods: slices of 60 minutes each, sharing their boundary
    # instant; the boundary candle of adjacent slices is emitted once.
    windows = [(c["start_ts"], c["end_ts"]) for c in calls]
    assert windows == [(S0, S0 + 3600), (S0 + 3600, S0 + 7200), (S0 + 7200, S0 + 10800)]
    assert [r["ts"] for r in rows] == [S0, S0 + 3600, S0 + 7200, S0 + 10800]


def test_batch_candles_one_request_when_a_lone_window_exactly_fits_the_budget():
    # 00:00..01:00 at one minute counts 61 periods, both ends included.
    calls = []

    def answer(params):
        calls.append(params)
        return batch_body(**{params["market_tickers"]: []})

    conn, _, _ = connector(live_routes(spans=(A,), **{BATCH_PATH: answer}))
    read(conn, ["candles"], config={**BATCH, "max_candles": 61})
    assert [(c["start_ts"], c["end_ts"]) for c in calls] == [(S0, E0)]

    # A window that is not a whole number of periods still counts 61: one request.
    calls.clear()
    ragged = market("KXBTCD-26OCT0621-TRAGGED", close_time="2026-10-07T01:00:30Z")
    conn, _, _ = connector(live_routes(spans=(ragged,), **{BATCH_PATH: answer}))
    read(conn, ["candles"], config={**BATCH, "max_candles": 61})
    assert [(c["start_ts"], c["end_ts"]) for c in calls] == [(S0, E0 + 30)]


def test_batch_candles_a_window_nested_inside_another_widens_nothing():
    # A short market inside a long one: the union window stays the long one's,
    # so the long market's later candles are still asked for.
    long_lived = market("KXBTCD-26OCT0621-TLONG", close_time="2026-10-07T03:00:00Z")
    nested = market("KXBTCD-26OCT0621-TNEST", open_time="2026-10-07T00:30:00Z",
                    close_time="2026-10-07T01:00:00Z")
    calls = []

    def answer(params):
        calls.append(params)
        return batch_body(**{t: [] for t in params["market_tickers"].split(",")})

    conn, _, _ = connector(live_routes(spans=(nested, long_lived), **{BATCH_PATH: answer}))
    read(conn, ["candles"], config=BATCH)
    assert [(c["market_tickers"], c["start_ts"], c["end_ts"]) for c in calls] == [
        (f"{long_lived['ticker']},{nested['ticker']}", S0, S0 + 10800)]


def test_batch_candles_the_last_slice_of_a_lone_window_stops_at_its_end():
    # 100 minutes under a 61-period budget: 60 minutes, then the remaining 40,
    # never a second 60 that reaches past the market's close.
    odd = market("KXBTCD-26OCT0621-TODD", close_time="2026-10-07T01:40:00Z")
    calls = []

    def answer(params):
        calls.append(params)
        return batch_body(**{params["market_tickers"]: []})

    conn, _, _ = connector(live_routes(spans=(odd,), **{BATCH_PATH: answer}))
    read(conn, ["candles"], config={**BATCH, "max_candles": 61})
    assert [(c["start_ts"], c["end_ts"]) for c in calls] == [
        (S0, S0 + 3600), (S0 + 3600, S0 + 6000)]


def test_batch_candles_a_window_one_period_over_the_budget_is_sliced():
    # 00:00..01:00 counts 61 periods; a budget of 60 may not be asked for it whole.
    calls = []

    def answer(params):
        calls.append(params)
        return batch_body(**{params["market_tickers"]: []})

    conn, _, _ = connector(live_routes(spans=(A,), **{BATCH_PATH: answer}))
    read(conn, ["candles"], config={**BATCH, "max_candles": 60})
    windows = [(c["start_ts"], c["end_ts"]) for c in calls]
    assert windows == [(S0, S0 + 3540), (S0 + 3540, E0)]
    for start, end in windows:
        assert (end - start) // 60 + 1 <= 60


def test_batch_candles_pack_in_time_order_whatever_order_the_venue_lists():
    day = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
    markets = []
    for i in range(6):
        opened = day + timedelta(minutes=15 * i)
        markets.append(market(
            f"KXBTC15M-26OCT07{i:02d}-0", event=f"KXBTC15M-26OCT07{i:02d}",
            open_time=opened.isoformat().replace("+00:00", "Z"),
            close_time=(opened + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")))
    calls = []

    def answer(params):
        calls.append(params)
        return batch_body(**{t: [] for t in params["market_tickers"].split(",")})

    routes = {
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(settled=list(reversed(markets))),  # newest first, as listed
        BATCH_PATH: answer,
    }
    conn, _, _ = connector(routes)
    read(conn, ["candles"], config={**BATCH, "series": ["KXBTC15M"], "max_candles": 140})
    start = int(day.timestamp())
    assert [(c["start_ts"], c["end_ts"]) for c in calls] == [
        (start, start + 2700), (start + 2700, start + 5400)]
    assert calls[0]["market_tickers"].split(",") == [m["ticker"] for m in markets[:3]]


def test_batch_candles_refuse_a_response_missing_a_market():
    conn, _, _ = connector(live_routes(**{BATCH_PATH: batch_body(**{A["ticker"]: []})}))
    with pytest.raises(AssetError, match="lacks.*T96000"):
        list(conn.read(BATCH, ["candles"], {}, "live"))
    conn, _, _ = connector(live_routes(**{BATCH_PATH: {"markets": "no"}}))
    with pytest.raises(AssetError, match="'markets' is not a list"):
        list(conn.read(BATCH, ["candles"], {}, "live"))


def test_hostile_payloads_refuse_instead_of_crashing():
    # Unhashable or non-string identifiers must reach an AssetError, never a
    # TypeError out of a set or dict.
    conn, _, _ = connector(live_routes(**{EVENT_PATH: {
        "adjusted_end_ts": E0, "market_tickers": [["x"]], "market_candlesticks": [[]]}}))
    with pytest.raises(AssetError, match="not a ticker"):
        list(conn.read(CONFIG, ["candles"], {}, "live"))
    entries = [{"market_ticker": ["x"], "candlesticks": []}, "junk"]
    conn, _, _ = connector(live_routes(**{BATCH_PATH: {"markets": entries}}))
    with pytest.raises(AssetError, match="lacks requested market"):
        list(conn.read(BATCH, ["candles"], {}, "live"))
    odd = [{"end_period_ts": [1]}, {"end_period_ts": [1]}]
    conn, _, _ = connector(live_routes(spans=(A,), **{
        BATCH_PATH: batch_body(**{A["ticker"]: odd})}))
    with pytest.raises(AssetError, match="numeric end_period_ts"):
        list(conn.read(BATCH, ["candles"], {}, "live"))
    conn, _, _ = connector({
        "/historical/markets": {"markets": [OLD], "cursor": {"a": 1}},
        "/markets": by_status()})
    with pytest.raises(AssetError, match="cursor is not a string"):
        list(conn.read(CONFIG, ["markets"], {}, "live"))


def candle_requests(grouping, spans, now=NOW, **knobs):
    """Run one candles pull and return the ``(start_ts, end_ts)`` of every candle request."""
    if grouping == "event":
        route = {EVENT_PATH: quiet_event(*spans)}
        path = EVENT_PATH
    else:
        route = {BATCH_PATH: lambda p: batch_body(
            **{t: [] for t in p["market_tickers"].split(",")})}
        path = BATCH_PATH
    conn, script, _ = connector(live_routes(spans=spans, **route), now=now)
    read(conn, ["candles"], config={**CONFIG, "candle_grouping": grouping, **knobs})
    return [(p["start_ts"], p["end_ts"]) for p in script.params(path)]


@pytest.mark.parametrize("grouping", ["event", "batch"])
def test_candles_a_market_without_an_open_time_looks_back_a_fixed_span(grouping):
    unopened = market("KXBTCD-26OCT0621-T1", open_time="")
    span = int(kalshi_history._FALLBACK_WINDOW.total_seconds())
    # Hourly candles keep the fortnight inside one batch request's budget.
    assert candle_requests(grouping, (unopened,), period_interval=60) == [(E0 - span, E0)]


@pytest.mark.parametrize("grouping", ["event", "batch"])
def test_candles_a_market_without_a_close_time_runs_to_the_capture(grouping):
    unclosed = market("KXBTCD-26OCT0621-T1", close_time="")
    assert candle_requests(grouping, (unclosed,)) == [(S0, unix(CAPTURE))]


def test_archive_candles_a_market_without_an_open_time_looks_back_a_fixed_span():
    unopened = {**OLD, "open_time": ""}
    path = f"/historical/markets/{OLD['ticker']}/candlesticks"
    conn, script, _ = connector({
        "/historical/markets": {"markets": [unopened], "cursor": ""},
        "/markets": by_status(), path: {"candlesticks": []}})
    read(conn, ["candles"], config={**CONFIG, "period_interval": 60})
    close = unix(OLD["close_time"])
    assert script.params(path) == [{
        "start_ts": close - int(kalshi_history._FALLBACK_WINDOW.total_seconds()),
        "end_ts": close, "period_interval": 60}]


@pytest.mark.parametrize("grouping", ["event", "batch"])
@pytest.mark.parametrize("opened, closed", [
    ("2026-10-07T01:00:00Z", "2026-10-07T01:00:00Z"),   # opens as it closes
    ("2026-10-07T12:00:00Z", "2026-10-07T13:00:00Z"),   # opens at the capture instant
])
def test_candles_an_empty_window_is_never_requested(grouping, opened, closed):
    brief = market("KXBTCD-26OCT0621-T1", open_time=opened, close_time=closed)
    assert candle_requests(grouping, (brief,)) == []


def test_archive_candles_an_empty_window_is_never_requested():
    brief = {**OLD, "open_time": OLD["close_time"]}
    path = f"/historical/markets/{OLD['ticker']}/candlesticks"
    conn, script, _ = connector({
        "/historical/markets": {"markets": [brief], "cursor": ""},
        "/markets": by_status()})
    assert data(read(conn, ["candles"])) == []
    assert script.params(path) == []


@pytest.mark.parametrize("grouping", ["event", "batch"])
def test_candles_a_market_without_a_close_time_is_requested_whatever_the_cursor(grouping):
    # Nothing says an undated market has closed, so a cursor never skips it.
    unclosed = market("KXBTCD-26OCT0621-T1", close_time="")
    cursor = "2026-10-07T12:00:00+00:00"
    if grouping == "event":
        route = {EVENT_PATH: quiet_event(unclosed)}
        path = EVENT_PATH
    else:
        route = {BATCH_PATH: lambda p: batch_body(**{unclosed["ticker"]: []})}
        path = BATCH_PATH
    conn, script, _ = connector(live_routes(spans=(unclosed,), **route))
    read(conn, ["candles"], config={**CONFIG, "candle_grouping": grouping},
         state={"candles": {"cursor": cursor}})
    assert len(script.params(path)) == 1


def test_trades_a_market_without_a_close_time_is_requested_whatever_the_cursor():
    unclosed = market("KXBTCD-26OCT0621-T1", close_time="")
    conn, script, _ = connector(trade_routes(settled=(unclosed,), history=(), **{
        "/historical/trades": {"trades": [], "cursor": ""},
        "/markets/trades": {"trades": [], "cursor": ""}}))
    read(conn, ["trades"], state={"trades": {"cursor": "2026-10-07T12:00:00+00:00"}})
    assert [p["ticker"] for p in script.params("/markets/trades")] == [unclosed["ticker"]]


def test_batch_mode_needs_no_event_ticker():
    orphan = market("KXBTCD-26OCT0621-T1", event="")
    conn, _, _ = connector(live_routes(spans=(orphan,), **{
        BATCH_PATH: batch_body(**{orphan["ticker"]: [live_candle(S0 + 60)]})}))
    assert len(data(read(conn, ["candles"], config=BATCH))) == 1


# -- trades --------------------------------------------------------------------------------


def trade(trade_id, created, ticker, taker="yes", price="0.4600", count="16.04"):
    return {
        "trade_id": trade_id, "ticker": ticker, "created_time": created,
        "yes_price_dollars": price, "no_price_dollars": "0.5400",
        "count_fp": count, "taker_side": taker, "is_block_trade": False,
        "taker_book_side": "bid", "taker_outcome_side": taker,
    }


STRADDLE = market("KXBTCD-26AUG0702-T1", event="KXBTCD-26AUG0702",
                  open_time="2026-08-06T22:00:00Z", close_time="2026-08-07T02:00:00Z",
                  settlement_ts="2026-08-07T02:02:00Z")
CUT_S = unix(CUTOFF)


def trade_routes(settled=(OLD, NEW, STRADDLE), history=(OLD,), **extra):
    return {
        "/historical/markets": {"markets": list(history), "cursor": ""},
        "/markets": by_status(settled=list(settled)),
        **extra,
    }


def test_trades_route_each_period_to_the_archive_that_owns_it():
    conn, script, _ = connector(trade_routes(**{
        "/historical/trades": {"trades": [], "cursor": ""},
        "/markets/trades": {"trades": [], "cursor": ""},
    }), )
    read(conn, ["trades"])
    hist = [p["ticker"] for p in script.params("/historical/trades")]
    live = [p["ticker"] for p in script.params("/markets/trades")]
    # OLD closed before the cutoff: archive only. NEW opened after it: live
    # only. STRADDLE spans the cutoff: both, each side bounded by it.
    assert hist == [OLD["ticker"], STRADDLE["ticker"]]
    assert live == [NEW["ticker"], STRADDLE["ticker"]]
    for params in script.params("/historical/trades"):
        assert params["max_ts"] == CUT_S and "min_ts" not in params
    for params in script.params("/markets/trades"):
        assert params["min_ts"] == CUT_S and "max_ts" not in params
        assert params["limit"] == 1000


def test_trades_rows_page_by_cursor_and_are_dated_at_created_time():
    pages = [
        {"trades": [trade("t3", "2026-10-07T00:30:00.123456Z", NEW["ticker"]),
                    trade("t2", "2026-10-07T00:20:00Z", NEW["ticker"], taker="no",
                          price="1.2000")], "cursor": "p2"},
        {"trades": [trade("t1", "2026-10-07T00:10:00.26836Z", NEW["ticker"])],
         "cursor": ""},
    ]
    conn, script, _ = connector(trade_routes(
        settled=(NEW,), history=(), **{"/markets/trades": pages}))
    msgs = read(conn, ["trades"])
    assert script.params("/markets/trades")[1]["cursor"] == "p2"
    assert data(msgs)[0] == {
        "trade_id": "t3", "ticker": NEW["ticker"],
        "created_time": "2026-10-07T00:30:00.123456Z", "yes_price": 0.46,
        "count": 16.04, "taker_side": "yes"}
    assert data(msgs)[1]["taker_side"] == "no"
    assert data(msgs)[1]["yes_price"] is None  # outside [0, 1]
    assert [m["effective_date"] for m in records(msgs)] == [
        "2026-10-07T00:30:00.123456Z", "2026-10-07T00:20:00Z",
        "2026-10-07T00:10:00.26836Z"]
    # The checkpoint is the newest trade created.
    assert msgs[-1]["state"] == {"trades": {"cursor": "2026-10-07T00:30:00.123456Z"}}
    for row in data(msgs):
        json.dumps(row, allow_nan=False)


def test_candle_and_trade_rows_keep_the_declared_field_order():
    # A consumer may read a row positionally: the dict order IS the schema
    # discover() declares (markets and orderbooks are pinned the same way).
    conn, _, _ = connector(archive_routes([archive_candle("2026-08-06T20:00:00Z")]))
    assert list(data(read(conn, ["candles"]))[0]) == list(kalshi_history.CANDLE_FIELDS)
    conn, _, _ = connector(trade_routes(settled=(NEW,), history=(), **{
        "/markets/trades": {"trades": [
            trade("t1", "2026-10-07T00:10:00Z", NEW["ticker"])], "cursor": ""}}))
    assert list(data(read(conn, ["trades"]))[0]) == list(kalshi_history.TRADE_FIELDS)


def test_trades_resume_from_the_cursor_and_skip_closed_markets():
    conn, script, _ = connector(trade_routes(**{
        "/historical/trades": {"trades": [], "cursor": ""},
        "/markets/trades": {"trades": [], "cursor": ""},
    }))
    cursor = "2026-10-07T00:30:00+00:00"
    read(conn, ["trades"], state={"trades": {"cursor": cursor}})
    # OLD and STRADDLE closed at or before the cursor: no request at all.
    # NEW is still open at the cursor: live only, from the cursor (> cutoff).
    assert script.params("/historical/trades") == []
    assert [(p["ticker"], p["min_ts"]) for p in script.params("/markets/trades")] == [
        (NEW["ticker"], unix(cursor))]


def test_trades_resume_inside_the_archive_period_bounds_the_archive_request():
    # A cursor older than the cutoff: the archive leg starts at the cursor.
    conn, script, _ = connector(trade_routes(**{
        "/historical/trades": {"trades": [], "cursor": ""},
        "/markets/trades": {"trades": [], "cursor": ""},
    }))
    cursor = "2026-08-06T23:00:00+00:00"
    read(conn, ["trades"], state={"trades": {"cursor": cursor}})
    hist = {p["ticker"]: p for p in script.params("/historical/trades")}
    assert hist[STRADDLE["ticker"]] == {
        "ticker": STRADDLE["ticker"], "limit": 1000, "max_ts": CUT_S,
        "min_ts": unix(cursor)}
    live = {p["ticker"]: p for p in script.params("/markets/trades")}
    assert live[STRADDLE["ticker"]]["min_ts"] == CUT_S  # never below the cutoff


def test_trades_a_cursor_at_the_cutoff_instant_leaves_nothing_for_the_archive():
    # The archive leg ends AT the cutoff: a cursor already there has pulled it all.
    long_lived = market("KXBTCD-LONG", event="KXBTCD-LONG",
                        open_time="2026-08-06T22:00:00Z", close_time="2026-10-07T03:00:00Z",
                        settlement_ts="2026-10-07T03:02:00Z")
    conn, script, _ = connector(trade_routes(
        settled=(long_lived,), history=(), **{
            "/historical/trades": {"trades": [], "cursor": ""},
            "/markets/trades": {"trades": [], "cursor": ""}}))
    read(conn, ["trades"], state={"trades": {"cursor": CUTOFF}})
    assert script.params("/historical/trades") == []
    assert [p["min_ts"] for p in script.params("/markets/trades")] == [CUT_S]


def test_trades_the_archive_is_not_asked_for_a_period_the_cursor_already_passed():
    # Opened before the cutoff, still open at the cursor, cursor after the
    # cutoff: everything before the cursor was pulled, so only the live leg runs.
    long_lived = market("KXBTCD-LONG", event="KXBTCD-LONG",
                        open_time="2026-08-06T22:00:00Z", close_time="2026-10-07T03:00:00Z",
                        settlement_ts="2026-10-07T03:02:00Z")
    conn, script, _ = connector(trade_routes(
        settled=(long_lived,), history=(), **{
            "/historical/trades": {"trades": [], "cursor": ""},
            "/markets/trades": {"trades": [], "cursor": ""}}))
    cursor = "2026-10-07T00:30:00+00:00"
    read(conn, ["trades"], state={"trades": {"cursor": cursor}})
    assert script.params("/historical/trades") == []
    assert [p["min_ts"] for p in script.params("/markets/trades")] == [unix(cursor)]


@pytest.mark.parametrize("opened, closed, legs", [
    ("2026-08-06T22:00:00Z", "2026-08-07T00:00:00Z", ["/historical/trades", "/markets/trades"]),
    ("2026-08-07T00:00:00Z", "2026-08-07T01:00:00Z", ["/markets/trades"]),
    ("2026-08-06T22:00:00Z", "2026-08-06T23:59:59Z", ["/historical/trades"]),
    ("2026-08-06T23:59:59Z", "2026-08-07T00:00:01Z", ["/historical/trades", "/markets/trades"]),
])
def test_trades_a_market_touching_the_cutoff_instant_is_asked_where_it_traded(
        opened, closed, legs):
    edge = market("KXBTCD-EDGE", event="KXBTCD-EDGE", open_time=opened, close_time=closed,
                  settlement_ts="2026-08-07T01:02:00Z")
    conn, script, _ = connector(trade_routes(settled=(edge,), history=(), **{
        "/historical/trades": {"trades": [], "cursor": ""},
        "/markets/trades": {"trades": [], "cursor": ""}}))
    read(conn, ["trades"])
    assert [p for p in script.paths() if "trades" in p] == legs


@pytest.mark.parametrize("opened, closed, legs", [
    ("", "2026-08-06T23:00:00Z", ["/historical/trades"]),      # unknown start: could be old
    ("2026-08-07T01:00:00Z", "", ["/markets/trades"]),         # unknown end: could be live
    ("", "", ["/historical/trades", "/markets/trades"]),
])
def test_trades_a_market_missing_an_open_or_close_time_is_asked_where_it_could_trade(
        opened, closed, legs):
    odd = market("KXBTCD-ODD", event="KXBTCD-ODD", open_time=opened, close_time=closed,
                 settlement_ts="2026-08-07T01:02:00Z")
    conn, script, _ = connector(trade_routes(settled=(odd,), history=(), **{
        "/historical/trades": {"trades": [], "cursor": ""},
        "/markets/trades": {"trades": [], "cursor": ""}}))
    read(conn, ["trades"])
    assert [p for p in script.paths() if "trades" in p] == legs


def test_trades_one_seen_on_both_legs_of_a_market_is_emitted_once():
    dup = trade("same", "2026-08-07T00:00:00Z", STRADDLE["ticker"])
    other = trade("other", "2026-08-07T00:00:01Z", STRADDLE["ticker"])
    conn, _, _ = connector(trade_routes(settled=(STRADDLE,), history=(), **{
        "/historical/trades": {"trades": [dup], "cursor": ""},
        "/markets/trades": {"trades": [dup, other], "cursor": ""}}))
    assert [d["trade_id"] for d in data(read(conn, ["trades"]))] == ["same", "other"]


def test_trades_a_payload_without_a_ticker_takes_the_requested_one():
    anonymous = trade("t1", "2026-10-07T00:10:00Z", NEW["ticker"])
    del anonymous["ticker"]
    conn, _, _ = connector(trade_routes(settled=(NEW,), history=(), **{
        "/markets/trades": {"trades": [anonymous], "cursor": ""}}))
    assert data(read(conn, ["trades"]))[0]["ticker"] == NEW["ticker"]


def test_trades_dated_after_the_capture_are_left_for_the_next_pull():
    # A trade created after the capture instant (the pull started at 12:00; a
    # long pull, or a venue clock ahead of this host's) is not emitted: the
    # cursor stays at the last trade emitted, so the next pull asks for it
    # again. Nothing a pull emits is dated after the instant it was captured.
    still_open = market("KXBTCD-26OCT0713-T1", event="KXBTCD-26OCT0713",
                        close_time="2026-10-07T13:00:00Z")
    early = trade("early", "2026-10-07T00:30:00Z", still_open["ticker"])
    late = trade("late", "2026-10-07T12:00:20Z", still_open["ticker"])
    edge = trade("edge", CAPTURE.replace("+00:00", "Z"), still_open["ticker"])
    conn, script, _ = connector(trade_routes(settled=(still_open,), history=(), **{
        "/markets/trades": {"trades": [early, late, edge], "cursor": ""}}))
    msgs = read(conn, ["trades"])
    assert [d["trade_id"] for d in data(msgs)] == ["early", "edge"]
    assert msgs[-1]["state"] == {"trades": {"cursor": "2026-10-07T12:00:00Z"}}
    for record in records(msgs):
        assert parse_iso(record["effective_date"]) <= parse_iso(CAPTURE)

    # Next pull, later: it resumes at the cursor and the late trade is emitted.
    conn, script, _ = connector(trade_routes(settled=(still_open,), history=(), **{
        "/markets/trades": {"trades": [late], "cursor": ""}}), now=NOW + timedelta(minutes=5))
    again = read(conn, ["trades"], state=msgs[-1]["state"])
    assert [d["trade_id"] for d in data(again)] == ["late"]
    assert script.params("/markets/trades")[0]["min_ts"] == unix("2026-10-07T12:00:00Z")


def test_a_trade_dated_after_the_commit_instant_does_not_abort_acquisition(
        root, registry, history_source, monkeypatch):
    # The venue's clock a few seconds ahead of this host's: its newest trade is
    # "after" the commit instant, which acquisition refuses as a forecast. The
    # pack never emits such a trade, so the pull completes with the rest.
    monkeypatch.setattr(acquire_module, "utc_now", lambda: "2026-10-07T12:00:45+00:00")
    StubHistoryConnector.script = Script({
        "/historical/cutoff": CUTOFF_BODY,
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(settled=[NEW]),
        "/markets/trades": {"trades": [
            trade("ok", "2026-10-07T00:30:00Z", NEW["ticker"]),
            trade("skewed", "2026-10-07T12:00:50Z", NEW["ticker"])], "cursor": ""},
    })
    result = run_acquisition(root, registry, "kalshihist", "trades", "backfill")
    assert result["records"] == 1
    trades = scan_stream(root.root, "kalshihist", "trades", key_fields=("trade_id",))
    assert [t["trade_id"] for t in trades] == ["ok"]


@pytest.mark.parametrize("bad, message", [
    ({"created_time": "2026-10-07T00:10:00Z"}, "trade_id"),
    ({"trade_id": "t"}, "created_time"),
    ({"trade_id": "t", "created_time": "yesterday"}, "created_time"),
    ("nope", "not a dict"),
])
def test_trades_refuse_what_cannot_be_keyed_or_dated(bad, message):
    conn, _, _ = connector(trade_routes(settled=(NEW,), history=(), **{
        "/markets/trades": {"trades": [bad], "cursor": ""}}))
    with pytest.raises(AssetError, match=message):
        list(conn.read(CONFIG, ["trades"], {}, "live"))


def test_trades_refuse_a_stuck_walk():
    conn, _, _ = connector(trade_routes(settled=(NEW,), history=(), **{
        "/markets/trades": lambda p: {
            "trades": [trade("t", "2026-10-07T00:10:00Z", NEW["ticker"])],
            "cursor": "same"}}))
    with pytest.raises(AssetError, match="did not advance"):
        list(conn.read(CONFIG, ["trades"], {}, "live"))


# -- orderbooks -------------------------------------------------------------------------------


def book(yes, no):
    return {"orderbook_fp": {"yes_dollars": yes, "no_dollars": no}}


def test_orderbooks_carry_the_instant_each_response_returned():
    stamps = iter([
        datetime(2026, 10, 7, 12, 0, 30, tzinfo=timezone.utc),   # capture
        datetime(2026, 10, 7, 12, 0, 31, 250000, tzinfo=timezone.utc),
        datetime(2026, 10, 7, 12, 4, 2, tzinfo=timezone.utc),
    ])
    second = market("KXBTCD-26OCT0713-T97000", event="KXBTCD-26OCT0713", status="active")
    conn, script, _ = connector({
        "/markets": by_status(open=[OPEN, second]),
        f"/markets/{OPEN['ticker']}/orderbook": book([["0.40", "10"]], [["0.55", "5"]]),
        f"/markets/{second['ticker']}/orderbook": book([], [["0.50", "2"]]),
    }, clock=lambda: next(stamps))
    msgs = read(conn, ["orderbooks"])
    rows = data(msgs)
    # captured_at is the pull's minute (unchanged); observed_at is per book.
    assert [r["captured_at"] for r in rows] == [CAPTURE, CAPTURE]
    assert [r["observed_at"] for r in rows] == [
        "2026-10-07T12:00:31.250000+00:00", "2026-10-07T12:04:02+00:00"]
    assert [m["effective_date"] for m in records(msgs)] == [CAPTURE, CAPTURE]
    assert rows[0]["yes_bids"] == [[0.4, 10.0]] and rows[0]["no_bids"] == [[0.55, 5.0]]
    assert list(rows[0]) == list(kalshi_history.ORDERBOOK_FIELDS)
    assert script.params("/markets") == [
        {"series_ticker": SERIES, "status": "open", "limit": 1000}]


def test_orderbook_observed_at_is_when_that_books_response_returned():
    # The clock moves while a request is in flight: the pacing gap and a 429's
    # wait (sleeper) and the response time itself (getter) all advance it. A
    # book is stamped with the instant ITS response came back, never the
    # instant its request began, so an as-of join cannot see it early.
    now = [NOW]
    returned = []
    waits = []

    def sleeper(seconds):
        waits.append(seconds)
        now[0] += timedelta(seconds=seconds)

    def answer(params):
        now[0] += timedelta(seconds=90)
        returned.append(now[0])
        return book([["0.40", "10"]], [["0.55", "5"]])

    second = market("KXBTCD-26OCT0713-T97000", event="KXBTCD-26OCT0713", status="active")
    script = Script({
        "/markets": by_status(open=[OPEN, second]),
        f"/markets/{OPEN['ticker']}/orderbook": [http_error(429, {"Retry-After": "7"}), answer],
        f"/markets/{second['ticker']}/orderbook": answer,
    })
    conn = KalshiHistoryConnector(getter=script, sleeper=sleeper, clock=lambda: now[0])
    rows = data(read(conn, ["orderbooks"]))
    assert 7.0 in waits  # the 429's wait happened before the first book's answer
    assert [r["observed_at"] for r in rows] == [t.isoformat() for t in returned]
    first, later = (datetime.fromisoformat(r["observed_at"]) for r in rows)
    captured = datetime.fromisoformat(CAPTURE)
    # Later than the capture by at least the wait plus the response time.
    assert first - captured >= timedelta(seconds=30 + 7 + 90)
    assert later - first >= timedelta(seconds=90)
    assert [r["captured_at"] for r in rows] == [CAPTURE, CAPTURE]


def test_orderbooks_legacy_cents_book_and_an_empty_book():
    conn, _, _ = connector({
        "/markets": by_status(open=[OPEN]),
        f"/markets/{OPEN['ticker']}/orderbook": {"orderbook": {"yes": [[40, 10]], "no": None}},
    })
    row = data(read(conn, ["orderbooks"]))[0]
    assert row["yes_bids"] == [[0.4, 10.0]] and row["no_bids"] == []


def test_clock_must_return_an_aware_instant():
    conn, _, _ = connector({"/markets": by_status(open=[OPEN]),
                            f"/markets/{OPEN['ticker']}/orderbook": book([], [])},
                           clock=lambda: datetime(2026, 10, 7, 12, 0))
    with pytest.raises(AssetError, match="clock must return an aware datetime"):
        list(conn.read(CONFIG, ["orderbooks"], {}, "live"))


def test_every_ticker_in_a_path_is_url_quoted():
    odd_series, odd_event = "KX/S", "KX/S-26 OCT"
    quote = urllib.parse.quote
    config = {**CONFIG, "series": [odd_series]}
    live = market("KX/S-26 OCT-T1", event=odd_event)
    event_path = f"/series/{quote(odd_series, safe='')}/events/{quote(odd_event, safe='')}/candlesticks"
    conn, script, _ = connector({
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(settled=[live], open=[live]),
        event_path: event_body(E0, **{live["ticker"]: []}),
        f"/markets/{quote(live['ticker'], safe='')}/orderbook": book([], []),
    })
    read(conn, ["candles", "orderbooks"], config=config)
    assert event_path in script.paths()
    assert f"/markets/{quote(live['ticker'], safe='')}/orderbook" in script.paths()

    archived = {**live, "settlement_ts": "2026-08-06T21:00:00Z"}
    archive_path = f"/historical/markets/{quote(live['ticker'], safe='')}/candlesticks"
    conn, script, _ = connector({
        "/historical/markets": {"markets": [archived], "cursor": ""},
        "/markets": by_status(), archive_path: {"candlesticks": []}})
    read(conn, ["candles"], config=config)
    assert archive_path in script.paths()


def test_nothing_a_pull_emits_is_dated_after_its_capture():
    # The module's one temporal promise, across all four streams at once, on
    # payloads that each tempt the pull to run past the capture instant.
    live = market("KXBTCD-26OCT0713-T1", event="KXBTCD-26OCT0713", status="active",
                  open_time="2026-10-07T11:00:00Z", close_time="2026-10-07T13:00:00Z")
    capture = unix(CAPTURE)
    event_path = f"/series/{SERIES}/events/{live['event_ticker']}/candlesticks"
    conn, _, _ = connector({
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(open=[live]),
        event_path: event_body(capture, **{live["ticker"]: [
            live_candle(capture - 1800), live_candle(capture), live_candle(capture + 60)]}),
        "/markets/trades": {"trades": [
            trade("a", "2026-10-07T11:45:00Z", live["ticker"]),
            trade("b", "2026-10-07T12:00:30Z", live["ticker"])], "cursor": ""},
        f"/markets/{live['ticker']}/orderbook": book([["0.40", "1"]], []),
    })
    msgs = read(conn, ["markets", "candles", "trades", "orderbooks"])
    emitted = {}
    for record in records(msgs):
        emitted.setdefault(record["stream"], []).append(record)
        assert parse_iso(record["effective_date"]) <= parse_iso(CAPTURE)
    assert {k: len(v) for k, v in emitted.items()} == {
        "markets": 1, "candles": 2, "trades": 1, "orderbooks": 1}
    for stream, state in msgs[-1]["state"].items():
        assert parse_iso(state["cursor"]) <= parse_iso(CAPTURE), stream


# -- transport: pacing, retry, refusals -----------------------------------------------------------


def test_pacing_sleeps_between_requests_but_not_before_the_first():
    conn, _, sleeps = connector(MARKET_ROUTES)
    read(conn, ["markets"], config={**CONFIG, "pace_s": 0.5})
    assert sleeps == [0.5] * 5  # six requests, the first unpaced


def test_retry_with_backoff_on_429_and_5xx_and_network_errors():
    conn, script, sleeps = connector({"/historical/cutoff": [
        http_error(429, {"Retry-After": "3"}),
        http_error(503),
        urllib.error.URLError("reset"),
        CUTOFF_BODY,
    ]})
    conn.check({**CONFIG, "retries": 3, "pace_s": 0})
    assert len(script.calls) == 4
    assert sleeps == [3.0, 1.0, 2.0]  # Retry-After honoured, then backoff(2), backoff(3)


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_every_retried_status_is_retried_once_then_succeeds(code):
    conn, script, sleeps = connector({"/historical/cutoff": [http_error(code), CUTOFF_BODY]})
    conn.check({**CONFIG, "retries": 1, "pace_s": 0})
    assert len(script.calls) == 2 and len(sleeps) == 1


@pytest.mark.parametrize("code", [400, 401, 403, 404, 501, 505])
def test_any_other_status_is_refused_at_once(code):
    conn, script, sleeps = connector({"/historical/cutoff": http_error(code)})
    with pytest.raises(AssetError, match=f"HTTP {code}"):
        conn.check({**CONFIG, "retries": 3, "pace_s": 0})
    assert len(script.calls) == 1 and sleeps == []


def test_retry_gives_up_and_refuses_a_non_json_or_non_object_answer():
    conn, script, _ = connector({"/historical/cutoff": http_error(500)})
    with pytest.raises(AssetError, match="giving up after 2 attempt"):
        conn.check({**CONFIG, "retries": 1})
    conn, _, _ = connector({"/historical/cutoff": ValueError("bad json")})
    with pytest.raises(AssetError, match="not JSON"):
        conn.check(CONFIG)
    conn, _, _ = connector({"/historical/cutoff": lambda p: ["a list"]})
    with pytest.raises(AssetError, match="not a JSON object"):
        conn.check(CONFIG)


def test_an_error_names_the_failing_request():
    conn, _, _ = connector({"/markets": http_error(404),
                            "/historical/markets": {"markets": [], "cursor": ""}})
    with pytest.raises(AssetError, match=r"/markets\?.*HTTP 404"):
        list(conn.read(CONFIG, ["markets"], {}, "live"))


class Response:
    """The context-manager a patched ``urlopen`` returns."""

    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def patch_urlopen(monkeypatch, answers):
    """Replace ``urllib.request.urlopen``; ``answers`` maps a path to a body, an Exception or a list of them."""
    seen = []

    def urlopen(request, timeout):
        seen.append({"url": request.full_url, "timeout": timeout,
                     "headers": dict(request.header_items())})
        path = request.full_url.split("?")[0][len(BASE):]
        answer = answers[path]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Response(answer if isinstance(answer, bytes) else json.dumps(answer).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


def test_default_transport_is_stdlib_urllib(monkeypatch):
    seen = patch_urlopen(monkeypatch, {
        "/historical/cutoff": CUTOFF_BODY,
        "/markets": {"markets": [OPEN], "cursor": ""},
    })
    conn = KalshiHistoryConnector(sleeper=lambda s: None, clock=lambda: NOW)
    knobs = {**CONFIG, "timeout_s": 7}
    conn.check(knobs)
    rows = data(read(conn, ["markets"], config={**knobs, "statuses": ["open"]}))
    assert [r["ticker"] for r in rows] == [OPEN["ticker"]]
    assert [c["url"] for c in seen] == [
        f"{BASE}/historical/cutoff",
        f"{BASE}/markets?series_ticker={SERIES}&status=open&limit=1000"]
    for call in seen:
        assert call["timeout"] == 7
        assert call["headers"] == {"User-agent": "dskit-onboarding",
                                   "Accept": "application/json"}


def test_default_transport_failures_are_retried_or_refused_by_kind(monkeypatch):
    sleeps = []
    fails = {"/historical/cutoff": [
        urllib.error.HTTPError(BASE, 503, "busy", {}, None),
        urllib.error.URLError("reset"),
        CUTOFF_BODY]}
    seen = patch_urlopen(monkeypatch, fails)
    conn = KalshiHistoryConnector(sleeper=sleeps.append, clock=lambda: NOW)
    conn.check({**CONFIG, "retries": 2, "pace_s": 0})
    assert len(seen) == 3
    assert sleeps == [connector_module.backoff(1), connector_module.backoff(2)]

    for answer, message in (
        (urllib.error.HTTPError(BASE, 404, "gone", {}, None), "HTTP 404"),
        (b"<html>not json</html>", "not JSON"),
        (b"\xff\xfe", "not JSON"),
        (b"[1, 2]", "not a JSON object"),
    ):
        seen = patch_urlopen(monkeypatch, {"/historical/cutoff": [answer]})
        with pytest.raises(AssetError, match=message):
            KalshiHistoryConnector(sleeper=lambda s: None).check(CONFIG)
        assert len(seen) == 1  # none of these is retried


# -- parity with the public kalshi pack ----------------------------------------------------------------


def kalshi_connector(routes, now=NOW):
    script = Script(routes)
    return KalshiConnector(getter=script, sleeper=lambda s: None, clock=lambda: now), script


PARITY_PAYLOADS = [
    market("KXBTCD-26OCT0621-T95000"),
    market("KXBTCD-26OCT0621-T96000", floor_strike="80", cap_strike="75",
           yes_ask_dollars="1.2000", last_price_dollars="nan"),       # out of range, NaN
    market("KXBTCD-26OCT0621-TBLANK", event_ticker=None, strike_type=None,
           floor_strike="x", result=None, yes_sub_title="", subtitle="fallback sub",
           series_ticker="KXOVERRIDE"),
    market("KXBTCD-26OCT0713-TOPEN", status="active", result="",
           open_time="2026-10-07T12:00:00Z", close_time="2026-10-07T13:00:00Z",
           yes_bid_dollars="0.4000"),
    market("KXBTCD-26OCT0712-TEDGE", status="active", result="",
           open_time="2026-10-07T11:00:00Z", close_time="2026-10-07T12:00:00Z"),
]


def test_markets_parity_with_the_public_kalshi_pack():
    # The shared fields and the effective instant are the kalshi pack's, on the
    # very same payloads (the new pack restates the row rules; this pins them).
    old, _ = kalshi_connector({"/markets": by_status(settled=PARITY_PAYLOADS[:3],
                                                     open=PARITY_PAYLOADS[3:])})
    mine, _, _ = connector({"/historical/markets": {"markets": [], "cursor": ""},
                            "/markets": by_status(settled=PARITY_PAYLOADS[:3],
                                                  open=PARITY_PAYLOADS[3:])})
    a = records(list(old.read({"series": [SERIES], "base_url": BASE}, ["markets"], {}, "live")))
    b = records(read(mine, ["markets"]))
    assert len(a) == len(b) == 5
    for left, right in zip(a, b):
        shared = {k: right["data"][k] for k in kalshi.MARKET_FIELDS}
        assert shared == left["data"]
        assert right["effective_date"] == left["effective_date"]
        assert list(right["data"])[:len(kalshi.MARKET_FIELDS)] == list(kalshi.MARKET_FIELDS)
        assert right["kind"] == left["kind"] == "observation"
    assert set(kalshi_history.MARKET_FIELDS) - set(kalshi.MARKET_FIELDS) == {
        "expiration_value", "settlement_ts", "volume"}


def test_candles_parity_with_the_public_kalshi_pack():
    candles = [live_candle("2026-10-07T00:30:00Z"),
               live_candle("2026-10-07T00:45:00Z", close="nan"),
               live_candle("2026-10-07T00:40:00Z", close="1.5"),  # kept, as kalshi keeps it
               {"end_period_ts": unix("2026-10-07T00:50:00Z"), "price": None}]
    old, _ = kalshi_connector({
        "/markets": by_status(settled=[A]),
        f"/series/{SERIES}/markets/{A['ticker']}/candlesticks": {"candlesticks": candles},
    })
    expected = records(list(old.read(
        {"series": [SERIES], "base_url": BASE, "statuses": ["settled"],
         "period_interval": 1}, ["candles"], {}, "live")))
    assert len(expected) == 4

    for grouping, routes in (
        ("event", {EVENT_PATH: event_body(E0, **{A["ticker"]: candles})}),
        ("batch", {BATCH_PATH: batch_body(**{A["ticker"]: candles})}),
    ):
        mine, _, _ = connector(live_routes(spans=(A,), **routes))
        got = records(read(mine, ["candles"], config={
            **CONFIG, "statuses": ["settled"], "candle_grouping": grouping}))
        assert [(m["data"], m["effective_date"]) for m in got] == [
            (m["data"], m["effective_date"]) for m in expected], grouping
    assert kalshi_history.CANDLE_FIELDS == kalshi.CANDLE_FIELDS
    assert kalshi_history.CANDLE_KEY_FIELDS == kalshi.CANDLE_KEY_FIELDS


def test_orderbook_parity_with_the_public_kalshi_pack():
    bodies = [
        book([["0.40", "10"], ["0.42", "3"], ["bad", "1"], ["0.10", "0"]],
             [["0.55", "5"], [2, 1]]),
        book([["0.40", "10", "extra"], "junk", None, ["0.30", "2"]], "not a list"),
        {"orderbook": {"yes": [[40, 10], [101, 1]], "no": [[55, 5]]}},   # legacy cents
        {"orderbook": "junk"},
        {},
    ]
    for body in bodies:
        old, _ = kalshi_connector({
            "/markets": by_status(open=[OPEN]),
            f"/markets/{OPEN['ticker']}/orderbook": body})
        mine, _, _ = connector({
            "/markets": by_status(open=[OPEN]),
            f"/markets/{OPEN['ticker']}/orderbook": body})
        left = records(list(old.read({"series": [SERIES], "base_url": BASE},
                                     ["orderbooks"], {}, "live")))[0]
        right = records(read(mine, ["orderbooks"]))[0]
        assert {k: right["data"][k] for k in kalshi.ORDERBOOK_FIELDS} == left["data"], body
        assert right["effective_date"] == left["effective_date"]
    assert kalshi_history.ORDERBOOK_FIELDS == kalshi.ORDERBOOK_FIELDS + ("observed_at",)
    assert kalshi_history.MARKET_FIELDS[:len(kalshi.MARKET_FIELDS)] == kalshi.MARKET_FIELDS
    assert kalshi_history.ORDERBOOK_KEY_FIELDS == kalshi.ORDERBOOK_KEY_FIELDS
    assert kalshi_history.MARKET_KEY_FIELDS == kalshi.MARKET_KEY_FIELDS


def kalshi_candle_request(market_obj, **knobs):
    """The ``(start_ts, end_ts)`` the PUBLIC kalshi pack asks of one market's candles."""
    old, script = kalshi_connector({
        "/markets": by_status(settled=[market_obj]),
        f"/series/{SERIES}/markets/{market_obj['ticker']}/candlesticks": {"candlesticks": []},
    })
    list(old.read({"series": [SERIES], "base_url": BASE, "statuses": ["settled"],
                   **knobs}, ["candles"], {}, "live"))
    (params,) = script.params(f"/series/{SERIES}/markets/{market_obj['ticker']}/candlesticks")
    return params["start_ts"], params["end_ts"]


@pytest.mark.parametrize("grouping", ["event", "batch"])
@pytest.mark.parametrize("open_time", ["", "2026-10-07T00:00:00Z"])
@pytest.mark.parametrize("close_time", ["", "2026-10-07T01:00:00Z"])
def test_candle_window_parity_with_the_public_kalshi_pack(grouping, open_time, close_time):
    # The fallback span for a market with no open_time (and the capture as the
    # end of one with no close_time) lives privately in each pack: only the
    # requests they make can pin that they agree.
    undated = market("KXBTCD-26OCT0621-T1", open_time=open_time, close_time=close_time)
    expected = kalshi_candle_request(undated, period_interval=60)
    mine = candle_requests(grouping, (undated,), period_interval=60)
    assert mine == [expected]


def test_retried_statuses_parity_with_the_public_kalshi_pack():
    # Drive both packs through every client/server error status: the attempts
    # each makes (retries=1) agree, so the sets of retried statuses do.
    def attempts(make, code):
        script = Script({"/markets": http_error(code), "/historical/cutoff": CUTOFF_BODY,
                         "/historical/markets": {"markets": [], "cursor": ""}})
        conn = make(script)
        with pytest.raises(AssetError):
            list(conn.read({"series": [SERIES], "base_url": BASE, "statuses": ["open"],
                            "retries": 1, "pace_s": 0}, ["markets"], {}, "live"))
        return len(script.params("/markets"))

    retried = set()
    for code in range(400, 600):
        old = attempts(lambda g: KalshiConnector(
            getter=g, sleeper=lambda s: None, clock=lambda: NOW), code)
        mine = attempts(lambda g: KalshiHistoryConnector(
            getter=g, sleeper=lambda s: None, clock=lambda: NOW), code)
        assert mine == old, code
        if old > 1:
            retried.add(code)
    assert {429, 500, 502, 503, 504} <= retried  # the parity is not vacuous


def test_the_default_clock_is_the_current_utc_time():
    undated = market("KXBTCD-26OCT0713-T1", close_time="")  # dated at the capture
    before = datetime.now(timezone.utc) - timedelta(minutes=1)
    conn = KalshiHistoryConnector(
        getter=Script({"/markets": by_status(open=[undated])}), sleeper=lambda s: None)
    msgs = read(conn, ["markets"], config={**CONFIG, "statuses": ["open"]})
    capture = parse_iso(records(msgs)[0]["effective_date"])
    assert before <= capture <= datetime.now(timezone.utc)
    assert capture.second == 0 and capture.utcoffset() == timedelta(0)


# -- acquisition e2e -------------------------------------------------------------------------------------


class StubHistoryConnector(KalshiHistoryConnector):
    """The pack over a class-level script; acquisition builds it with no args."""

    script = None
    now = NOW

    def __init__(self):
        super().__init__(getter=type(self).script, sleeper=lambda s: None,
                         clock=lambda: type(self).now)


@pytest.fixture
def history_source(registry):
    vid = registry.register("source_config", {
        "name": "kalshihist",
        "catalog_source": "kalshihist-src",
        "connector": "tests.onboarding.test_kalshi_history:StubHistoryConnector",
        "config": CONFIG,
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    yield vid
    StubHistoryConnector.script = None
    StubHistoryConnector.now = NOW


def test_acquisition_end_to_end_markets_then_a_resumed_trade_pull(
        root, registry, history_source, monkeypatch):
    stamps = iter(["2026-10-07T12:00:45+00:00", "2026-10-07T12:01:45+00:00",
                   "2026-10-08T12:00:45+00:00"])
    monkeypatch.setattr(acquire_module, "utc_now", lambda: next(stamps))

    StubHistoryConnector.script = Script({
        "/historical/cutoff": CUTOFF_BODY, **MARKET_ROUTES})
    first = run_acquisition(root, registry, "kalshihist", "markets", "backfill")
    assert first["records"] == 5 and first["snapshot"] is not None
    assert StubHistoryConnector.script.paths()[:2] == [
        "/historical/cutoff", "/historical/cutoff"]  # check(), then the pull
    rows = scan_stream(root.root, "kalshihist", "markets", key_fields=("ticker",))
    assert {r["ticker"]: r["expiration_value"] for r in rows}[OLD["ticker"]] == 64396.95

    # trades: first pull from nothing, the second resumes at the cursor.
    later = trade("tx", "2026-10-07T00:30:00Z", NEW["ticker"])
    StubHistoryConnector.script = Script({
        "/historical/cutoff": CUTOFF_BODY,
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(settled=[NEW]),
        "/markets/trades": {"trades": [later], "cursor": ""},
    })
    second = run_acquisition(root, registry, "kalshihist", "trades", "backfill")
    assert second["records"] == 1
    assert load_state(root, "kalshihist", "trades", "backfill") == {
        "trades": {"cursor": "2026-10-07T00:30:00Z"}}

    StubHistoryConnector.now = NOW + timedelta(days=1)
    StubHistoryConnector.script = Script({
        "/historical/cutoff": CUTOFF_BODY,
        "/historical/markets": {"markets": [], "cursor": ""},
        "/markets": by_status(settled=[NEW]),
        "/markets/trades": {"trades": [], "cursor": ""},
    })
    run_acquisition(root, registry, "kalshihist", "trades", "backfill")
    # NEW closed at 01:00, after the 00:30 cursor, so it is asked again, from it.
    assert StubHistoryConnector.script.params("/markets/trades") == [{
        "ticker": NEW["ticker"], "limit": 1000, "min_ts": unix("2026-10-07T00:30:00Z")}]
    trades = scan_stream(root.root, "kalshihist", "trades", key_fields=("trade_id",))
    assert [t["trade_id"] for t in trades] == ["tx"]
