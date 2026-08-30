"""The child's node kinds under the TOOLKIT'S conformance bar, unit
tests for the domain math, and one end-to-end: store -> train ->
artifact -> the live loop's restore/predict/select chain.

Heavy deps (torch, pyomo/highspy) gate their own tests via skipif — the
suite passes on a bare install of dskit alone; the full chain only
proves itself where the child's real deps are installed.
"""

import ast
import hashlib
import importlib.util
import inspect
import json
import math
import os
import shutil
import tracemalloc
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from dskit.onboarding.base import AssetError
from dskit.onboarding.codec import open_text_writer
from dskit.pipeline import OutputsConfig, run_document
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.document import (
    FOREACH_SEP,
    PipelineDocument,
    load_document,
)
from dskit.pipeline.node import NodeContext

from intraday_poc import connectors, nodes
from intraday_poc.connectors import AlpacaBarsConnector
from intraday_poc.nodes import (
    NODE_KINDS,
    BarsFromStore,
    ForecastRows,
    SelectOne,
    WindowRows,
)

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")

HAVE_SOLVER = (importlib.util.find_spec("pyomo") is not None
               and importlib.util.find_spec("highspy") is not None)
HAVE_TORCH = importlib.util.find_spec("torch") is not None

#: The independent role census — cross-checked against the classes so a
#: mislabelled role cannot silently exit its checks.
EXPECTED_ROLES = {
    "intraday_poc-bars": "data",
    "intraday_poc-window": "transform",
    "intraday_poc-forecast": "score",
    "intraday_poc-select-one": "score",
}

#: A selector declaration deliberately unlike the pyomo pack's default:
#: another solver name, and options that are not empty. The shipped
#: documents declare the default itself, so a test that read only THEM
#: would pass against a loop that hardcoded ``(DEFAULT_SOLVER, {})``.
#: Solver-agnostic on purpose — nothing here solves with it; it is read
#: off a document and recorded at the solver's door.
FOREIGN_SELECTOR = ("glpk", {"mipgap": 0.005, "tmlim": 30})

_ACQUIRED = "2026-01-06T00:00:00+00:00"
_BASE = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)


def _ts(i: int) -> str:
    return (_BASE + timedelta(minutes=i)).isoformat()


def _ms(i: int) -> int:
    return int((_BASE + timedelta(minutes=i)).timestamp() * 1000)


def _ctx(tmp_path):
    return NodeContext(name="test", asof="2026-01-06",
                       run_dir=str(tmp_path))


def _bar(symbol, i, close, acquired=_ACQUIRED):
    return {
        "stream": "bars", "mode": "backfill", "kind": "observation",
        "effective_date": _ts(i), "acquired_at": acquired,
        "data": {"symbol": symbol, "ts": _ts(i), "open": close, "high": close,
                 "low": close, "close": close, "volume": 100.0,
                 "trade_count": 5, "vwap": close},
    }


def _close(symbol, i):
    anchor = 100.0 if symbol == "AAPL" else 200.0
    return round(anchor * (1.0 + 0.002 * math.sin(i / 3.0)), 6)


def _write_store(root, n_minutes=60, symbols=("AAPL", "MSFT"),
                 acq="acq-0001"):
    """A store shaped exactly like acquire's commit:
    ``<root>/observations/alpaca/<acq_id>/bars.jsonl``."""
    directory = os.path.join(root, "observations", "alpaca", acq)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "bars.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        for symbol in symbols:
            for i in range(n_minutes):
                fh.write(json.dumps(_bar(symbol, i, _close(symbol, i)),
                                    sort_keys=True) + "\n")
    return path


class _FakeSignal:
    """The TorchSignal contract without torch: a float per covered row,
    None where coverage is missing."""

    def predict(self, row):
        value = row.get("ret_lag_0")
        return None if value is None else float(value)


def probes(tmp_path):
    """One NodeProbe per kind over a tmp-rooted fixture store."""
    root = str(tmp_path / "ob")
    store_path = _write_store(root, n_minutes=12)
    bars_params = {"root": root, "source": "alpaca"}

    def move():
        # Same rows, one close changed IN PLACE — and mtimes restored,
        # so only a content-reading fingerprint can notice (F-222).
        stat = os.stat(store_path)
        with open(store_path, encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        lines[0]["data"]["close"] = lines[0]["data"]["close"] + 1.5
        with open(store_path, "w", encoding="utf-8") as fh:
            for row in lines:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
        os.utime(store_path, (stat.st_atime, stat.st_mtime))

    def grow():
        with open(store_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(_bar("AAPL", 99, 123.45),
                                sort_keys=True) + "\n")

    window_input = [{"symbol": "AAPL", "asof_ms": _ms(i),
                     "close": _close("AAPL", i)} for i in range(8)]
    forecast_input = [{"symbol": "AAPL", "asof_ms": _ms(i),
                       "ret_lag_0": 0.001 * i, "ret_lag_1": 0.0}
                      for i in range(1, 4)]
    select_forecasts = [
        {"symbol": "AAPL", "asof_ms": _ms(1), "pred": 0.002},
        {"symbol": "MSFT", "asof_ms": _ms(1), "pred": 0.001},
        {"symbol": "AAPL", "asof_ms": _ms(2), "pred": -0.003},
        {"symbol": "MSFT", "asof_ms": _ms(2), "pred": -0.001},
    ]
    select_labeled = [
        {"symbol": "AAPL", "asof_ms": _ms(1), "y_next": 0.004},
        {"symbol": "MSFT", "asof_ms": _ms(1), "y_next": -0.001},
        {"symbol": "AAPL", "asof_ms": _ms(2), "y_next": 0.002},
        {"symbol": "MSFT", "asof_ms": _ms(2), "y_next": -0.002},
    ]

    return {
        "intraday_poc-bars": NodeProbe(
            params=dict(bars_params),
            required=("root", "source"),
            make=lambda: BarsFromStore("bars", dict(bars_params)),
            move=move,
            grow=grow,
            size=lambda out: len(out["records"]),
            runnable=True,
        ),
        "intraday_poc-window": NodeProbe(
            params={"lookback": 2},
            required=("lookback",),
            inputs={"records": [dict(r) for r in window_input]},
            stream_ports=("records",),
            runnable=True,
        ),
        "intraday_poc-forecast": NodeProbe(
            params={"split": "val"},
            required=("split",),
            inputs={"signal": _FakeSignal(),
                    "records": [dict(r) for r in forecast_input]},
            stream_ports=("records",),
            runnable=True,
        ),
        "intraday_poc-select-one": NodeProbe(
            params={"split": "val"},
            required=("split",),
            inputs={"forecasts": [dict(r) for r in select_forecasts],
                    "labeled": [dict(r) for r in select_labeled]},
            stream_ports=("forecasts", "labeled"),
            runnable=HAVE_SOLVER,
        ),
    }


TestConformance = conformance_suite(
    registry=NODE_KINDS,
    module="intraday_poc.nodes",
    probes=probes,
    expected_roles=EXPECTED_ROLES,
    name="TestConformance",
)


# -- one name per default --------------------------------------------------


def test_the_window_defaults_are_named_once(monkeypatch):
    """``price_field`` and ``max_gap_minutes`` each have ONE name.

    The defect this pins is the repo's commonest: the same default
    written in ``validate_params`` AND in ``run``, so validation
    approves a value the run never uses and nothing notices. Rebinding
    each constant must move BOTH consumers — a restated literal in
    either survives the rebinding and fails here.
    """
    # The validation side reads them: rebound to junk, a document that
    # declares neither knob is refused on both.
    monkeypatch.setattr(nodes, "DEFAULT_PRICE_FIELD", 7)
    monkeypatch.setattr(nodes, "DEFAULT_MAX_GAP_MINUTES", 0)
    problems = WindowRows.validate_params({"lookback": 2})
    assert any("price_field" in p for p in problems), problems
    assert any("max_gap_minutes" in p for p in problems), problems

    # The run side reads them too. Bars two minutes apart, close and
    # vwap deliberately different series.
    rows = [{"symbol": "AAPL", "asof_ms": _ms(2 * i), "close": 100.0,
             "vwap": 100.0 + i} for i in range(4)]
    monkeypatch.setattr(nodes, "DEFAULT_PRICE_FIELD", "vwap")
    monkeypatch.setattr(nodes, "DEFAULT_MAX_GAP_MINUTES", 5)
    out = WindowRows("window", {"lookback": 2}).run(None, {"records": rows})
    (row,) = out["records"]
    assert row["ret_lag_0"] == pytest.approx(math.log(102.0 / 101.0)), (
        "the run priced on close, not the declared default field"
    )

    # ... and the gap tolerance: one minute breaks a two-minute chain.
    monkeypatch.setattr(nodes, "DEFAULT_MAX_GAP_MINUTES", 1)
    out = WindowRows("window", {"lookback": 2}).run(None, {"records": rows})
    assert out["records"] == []


def _binding_of(module, name):
    """How ``module``'s source binds ``name``: ('import', <from>) / ('assign',
    None) / (None, None) — the two are not exclusive, and assignment wins."""
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return "assign", None
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == name for alias in node.names):
                return "import", "." * node.level + (node.module or "")
    return None, None


@pytest.mark.parametrize("name", ["BAR_STREAM", "BAR_KEY_FIELDS"])
def test_the_bar_constants_are_imported_never_restated(name):
    """``nodes.py`` must BIND each bar constant by importing it.

    The identity assertion this replaces (``nodes.BAR_STREAM is
    connectors.BAR_STREAM``) could not fail: CPython interns
    identifier-shaped string constants across modules, so a restated
    ``BAR_STREAM = "bars"`` in ``nodes.py`` satisfied ``is`` while the
    writer was free to rename the stream underneath the reader. The
    behavioural halves below cannot see it either — they monkeypatch
    ``nodes``' own global, which a local copy provides. Only the
    BINDING distinguishes the two, so that is what this reads.
    """
    how, source = _binding_of(nodes, name)
    assert (how, source) == ("import", ".connectors"), (
        f"nodes.py binds {name} as {how!r} from {source!r} — it must import "
        "it from .connectors, or the reader can look for a spelling the "
        "writer abandoned"
    )
    assert getattr(nodes, name) == getattr(connectors, name)


@pytest.mark.parametrize("name", ["DEFAULT_PRICE_FIELD",
                                  "DEFAULT_MAX_GAP_MINUTES"])
def test_the_serving_loop_binds_no_window_default_at_all(name):
    """``live.py`` must bind NEITHER window default — it asks the node.

    The earlier bar was "import it, never restate it", because a
    restated ``DEFAULT_PRICE_FIELD = "close"`` satisfies
    ``live.X is nodes.X`` (CPython interns identifier-shaped strings and
    caches small ints) and monkeypatching ``live``'s own global patches
    a local copy just as happily. Since ADR-0040 the loop resolves the
    knobs by CONSTRUCTING the document's window node, so the honest bar
    is stronger: the name must not appear in ``live.py`` in any form.
    Either binding is a copy waiting to drift, and the day the node is
    retuned to ``"vwap"`` a copy keeps feeding close returns into
    vwap-trained weights — the train/serve skew this loop exists not to
    have. The behavioural half is
    ``test_live_falls_back_to_the_window_nodes_own_defaults``.
    """
    from intraday_poc import live

    how, source = _binding_of(live, name)
    assert (how, source) == (None, None), (
        f"live.py binds {name} as {how!r} from {source!r} — the serving "
        "loop must read the knob off the window node it constructs, so it "
        "needs no binding of its own"
    )
    assert not hasattr(live, name)


def test_the_serving_loop_declares_its_public_surface():
    """``__all__`` plus the ``_`` prefix IS the API contract here.

    ``live.py`` grew from a handful of names to the loop's whole seam
    list, and was the only module in either child package declaring no
    ``__all__`` — so every helper it happened to define read as public,
    and nothing said which names a caller may pin to. This asserts both
    halves: every exported name exists, and every public name the module
    DEFINES is exported, so a new helper is either underscored or
    deliberately added to the contract. (Ruling 2.)
    """
    from intraday_poc import live

    tree = ast.parse(inspect.getsource(live))
    defined = {node.name for node in tree.body
               if isinstance(node, ast.FunctionDef)}
    defined |= {target.id for node in tree.body
                if isinstance(node, ast.Assign)
                for target in node.targets if isinstance(target, ast.Name)}
    public = {name for name in defined if not name.startswith("_")}

    assert public == set(live.__all__), (
        "live.py defines public names it does not export "
        f"({sorted(public - set(live.__all__))}) or exports names it does "
        f"not define ({sorted(set(live.__all__) - public)}) — underscore "
        "the internals, or put them in the contract on purpose"
    )
    assert live.__all__ == sorted(live.__all__), live.__all__
    for name in live.__all__:
        assert hasattr(live, name), name


def test_the_bars_stream_default_is_named_once(tmp_path, monkeypatch):
    """The stream name is the connector's, and the node borrows it.

    ``BAR_STREAM`` lives in ``connectors.py`` (the module that emits the
    stream) and ``nodes.py`` imports it, so the reader can never look
    for a spelling the writer stopped using. Both of the node's own
    consumers — the knob gate and the scan — must read it.
    """
    root = str(tmp_path / "ob")
    path = _write_store(root, n_minutes=3, symbols=("AAPL",))

    monkeypatch.setattr(nodes, "BAR_STREAM", 5)
    problems = BarsFromStore.validate_params({"root": root,
                                              "source": "alpaca"})
    assert any("stream" in p for p in problems), problems

    monkeypatch.setattr(nodes, "BAR_STREAM", "ticks")
    os.rename(path, os.path.join(os.path.dirname(path), "ticks.jsonl"))
    node = BarsFromStore("bars", {"root": root, "source": "alpaca"})
    assert len(node.run(None, {})["records"]) == 3


def test_the_default_prose_states_the_constants_values():
    """The class docstrings quote each default for a reader, and prose
    is the one text that can go stale on its own — the code around it
    now reads the constants. Each needle is anchored on its OWNING
    knob's words, so two values swapped between knobs still fails."""
    window = " ".join(WindowRows.__doc__.split())
    assert (f'``price_field`` — default ``DEFAULT_PRICE_FIELD`` '
            f'(``"{nodes.DEFAULT_PRICE_FIELD}"``);') in window, window
    assert (f'``max_gap_minutes`` — default ``DEFAULT_MAX_GAP_MINUTES`` '
            f'({nodes.DEFAULT_MAX_GAP_MINUTES}):') in window, window
    bars = " ".join(BarsFromStore.__doc__.split())
    assert (f'``stream`` — default ``BAR_STREAM`` '
            f'(``"{nodes.BAR_STREAM}"``),') in bars, bars


def test_the_bar_primary_key_has_one_source_of_truth(tmp_path, monkeypatch):
    """The node's dedup key IS the connector's declared primary key.

    ``connectors.discover`` publishes ``primary_key`` and the store's
    bitemporal dedup keys off whatever the NODE passes; two copies of
    that tuple means the store can dedupe on a different key than the
    platform advertised, silently. The connector half of this pin lives
    in ``test_connectors.py``; this half proves the node reads the same
    object.
    """
    root = str(tmp_path / "ob")
    _write_store(root, n_minutes=3, symbols=("AAPL",))
    monkeypatch.setattr(nodes, "BAR_KEY_FIELDS", ("symbol", "nosuchfield"))
    node = BarsFromStore("bars", {"root": root, "source": "alpaca"})
    with pytest.raises(AssetError, match="nosuchfield"):
        node.run(None, {})


# -- domain math -----------------------------------------------------------


def test_bars_store_dedupes_bitemporally(tmp_path):
    """Two acquisitions overlap on one (symbol, ts): the row with the
    LATER acquired_at wins — a restated bar supersedes, never duplicates."""
    root = str(tmp_path / "ob")
    _write_store(root, n_minutes=3, symbols=("AAPL",), acq="acq-0001")
    directory = os.path.join(root, "observations", "alpaca", "acq-0002")
    os.makedirs(directory)
    restated = _bar("AAPL", 2, 555.0, acquired="2026-01-07T00:00:00+00:00")
    with open(os.path.join(directory, "bars.jsonl"), "w",
              encoding="utf-8") as fh:
        fh.write(json.dumps(restated, sort_keys=True) + "\n")

    node = BarsFromStore("bars", {"root": root, "source": "alpaca"})
    records = node.run(None, {})["records"]
    assert len(records) == 3  # deduped, not 4
    assert [r["close"] for r in records if r["asof_ms"] == _ms(2)] == [555.0]
    assert [r["asof_ms"] for r in records] == sorted(r["asof_ms"]
                                                     for r in records)


def test_a_mistyped_source_refuses_loudly(tmp_path):
    """A source name nothing ever acquired is an ERROR, not an empty
    scan — the README said the opposite for a while, and an empty scan
    would train a model on nothing and report success."""
    node = BarsFromStore("bars", {"root": str(tmp_path), "source": "typo"})
    with pytest.raises(AssetError, match="typo"):
        node.run(None, {})


def test_bars_fingerprint_digest_is_the_whole_snapshot_dump(tmp_path):
    """The digest recipe is FROZEN: sha256 of json.dumps(records,
    sort_keys=True) over the emitted snapshot. However the hash is
    computed internally, it must reproduce that byte for byte — identity
    movement would orphan every existing run's artifacts."""
    root = str(tmp_path / "ob")
    _write_store(root, n_minutes=4)
    node = BarsFromStore("bars", {"root": root, "source": "alpaca"})
    fp = node.fingerprint()
    records = node.run(None, {})["records"]
    expected = hashlib.sha256(
        json.dumps(records, sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert fp["sha256"] == expected
    assert fp["rows"] == len(records)


def test_bars_reads_gzip_observations(tmp_path):
    """A source that opts into observations_codec "gzip" (ADR-0036)
    stores <stream>.jsonl.gz — the reader must see those rows, not
    silently return an empty store."""
    plain_root = str(tmp_path / "plain")
    _write_store(plain_root, n_minutes=5, symbols=("AAPL",))
    expected = BarsFromStore(
        "bars", {"root": plain_root, "source": "alpaca"}
    ).run(None, {})["records"]

    gz_root = str(tmp_path / "gz")
    directory = os.path.join(gz_root, "observations", "alpaca", "acq-0001")
    os.makedirs(directory)
    with open_text_writer(os.path.join(directory, "bars.jsonl.gz"),
                          "gzip") as fh:
        for i in range(5):
            fh.write(json.dumps(_bar("AAPL", i, _close("AAPL", i)),
                                sort_keys=True) + "\n")

    node = BarsFromStore("bars", {"root": gz_root, "source": "alpaca"})
    assert node.run(None, {})["records"] == expected


def test_bars_refuses_ambiguous_stream_spellings(tmp_path):
    """bars.jsonl AND bars.jsonl.gz in one acquisition dir is
    tamper-shaped (the observations tree has no manifest) — refuse
    loudly, never silently pick one."""
    root = str(tmp_path / "ob")
    _write_store(root, n_minutes=3, symbols=("AAPL",))
    directory = os.path.join(root, "observations", "alpaca", "acq-0001")
    with open_text_writer(os.path.join(directory, "bars.jsonl.gz"),
                          "gzip") as fh:
        fh.write(json.dumps(_bar("AAPL", 0, 111.0), sort_keys=True) + "\n")

    node = BarsFromStore("bars", {"root": root, "source": "alpaca"})
    with pytest.raises(AssetError):
        node.run(None, {})


def test_bars_scan_holds_one_copy_of_the_stream(tmp_path):
    """The OOM regression pin (14.3 GB on 2M bars): fingerprint + run
    must hold ONE copy of the snapshot — no second records list, no
    run()-time dict-per-row copy, no whole-snapshot JSON string. The
    budgets sit between the single-copy cost and the measured multi-copy
    defect (~1550 B/row peak, ~1265 B/row resident) — and tight enough
    to catch a whole-dump digest regression alone (~930 B/row)."""
    root = str(tmp_path / "ob")
    n_minutes = 5000
    _write_store(root, n_minutes=n_minutes)  # 2 symbols -> 10_000 rows
    n_rows = n_minutes * 2

    node = BarsFromStore("bars", {"root": root, "source": "alpaca"})
    tracemalloc.start()
    try:
        node.fingerprint()
        out = node.run(None, {})
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(out["records"]) == n_rows
    assert peak / n_rows < 800, f"peak {peak / n_rows:.0f} B/row"
    assert current / n_rows < 700, f"resident {current / n_rows:.0f} B/row"


def test_score_kinds_accept_the_cal_split():
    """dskit grew a fourth split name (ADR-0034: the cal band); both
    score kinds must accept it — and still refuse junk by name."""
    assert ForecastRows.validate_params({"split": "cal"}) == []
    assert SelectOne.validate_params({"split": "cal"}) == []
    assert ForecastRows.validate_params({"split": "holdout"})
    assert SelectOne.validate_params({"split": "holdout"})


#: The pin stream's AAPL closes: a contiguous session, then a 34-minute
#: hole, then a second session. Deliberately irregular so a lag written
#: backwards, an off-by-one label, or a bridged gap all move a value.
_PIN_AAPL = {0: 100.0, 1: 101.0, 2: 100.5, 3: 102.0, 4: 101.0, 5: 101.5,
             40: 99.0, 41: 99.5, 42: 99.25, 43: 99.75}
#: MSFT's minute 2 carries NO close and minute 5 a non-positive one: both
#: are dropped, and the SURVIVORS chain across the hole they leave (2
#: minutes apart, inside the bound). That bridging is the semantic a
#: naive port loses — it is pinned here on purpose.
_PIN_MSFT = {0: 200.0, 1: 201.0, 2: None, 3: 203.0, 4: 204.0, 5: 0.0}


def _pin_records():
    """The pin stream, in an order that is NOT time order — the node must
    order its own bars, never inherit the stream's accident."""
    rows = [{"symbol": "AAPL", "asof_ms": _ms(i), "close": close}
            for i, close in _PIN_AAPL.items()]
    rows += [{"symbol": "MSFT", "asof_ms": _ms(i), "close": close}
             for i, close in _PIN_MSFT.items()]
    return rows[::-1]


def _pin_expected():
    """What today's WindowRows computes, restated INDEPENDENTLY.

    Nothing here calls the node or reads its source: the chain rule is
    written out again from the docstring's promise (drop a bar with no
    usable price, chain the survivors while consecutive bars sit inside
    the bound, ``ret_lag_0`` ends at ``asof_ms``, ``y_next`` is the next
    return). An expectation sourced from its subject asserts nothing.
    """
    expected = []
    for symbol, closes in (("AAPL", _PIN_AAPL), ("MSFT", _PIN_MSFT)):
        usable = [(i, c) for i, c in sorted(closes.items())
                  if isinstance(c, float) and c > 0]
        chains, chain = [], []
        for (prev_i, prev_c), (i, c) in zip(usable, usable[1:]):
            if i - prev_i > 5:
                chains.append(chain)
                chain = []
                continue
            chain.append((_ms(i), math.log(c / prev_c)))
        chains.append(chain)
        for chain in chains:
            for i in range(1, len(chain) - 1):
                expected.append({
                    "symbol": symbol, "asof_ms": chain[i][0],
                    "y_next": chain[i + 1][1],
                    "ret_lag_0": chain[i][1], "ret_lag_1": chain[i - 1][1],
                })
    expected.sort(key=lambda r: (r["asof_ms"], r["symbol"]))
    return expected


def test_window_rows_pins_todays_gap_and_sparse_semantics():
    """THE port pin: every window this node emits today, value for value.

    Gap discipline and sparse-bar handling are the two things the child
    got right and a rewrite loses silently — a bridged session boundary
    or a hole that stops bridging changes what every model downstream
    trains on, and no other test in this file would notice. So the whole
    output is pinned against an independent restatement of the rule
    BEFORE the node is rewritten over the toolkit's window ops.
    """
    out = WindowRows("window", {"lookback": 2}).run(
        None, {"records": _pin_records()})["records"]
    expected = _pin_expected()

    assert [(r["asof_ms"], r["symbol"]) for r in out] == \
        [(r["asof_ms"], r["symbol"]) for r in expected]
    assert [sorted(r) for r in out] == [sorted(r) for r in expected]
    for got, want in zip(out, expected):
        for name in sorted(want):
            assert got[name] == pytest.approx(want[name]), (name, got, want)
    # The bridge itself, spelled out: MSFT's one row reads THROUGH the
    # minute that carried no price.
    (msft,) = [r for r in out if r["symbol"] == "MSFT"]
    assert msft["asof_ms"] == _ms(3)
    assert msft["ret_lag_0"] == pytest.approx(math.log(203.0 / 201.0))


def test_window_rows_admits_and_refuses_the_DEGENERATE_bars_it_now_does():
    """The port's other divergences, on degenerate input — declared.

    Three of them, none visible in the value pins above because those
    feed well-formed bars. The pre-port node read ``symbol``/``asof_ms``
    with its own predicates; the pack reads them with the ENVELOPE's,
    which is stricter in one direction and wider in two:

    * an EMPTY symbol was its own series and is now no series at all —
      an empty group key is not an identity the toolkit can hold
      (``cluster_ok``), and a stream of ONLY such bars now refuses by
      name where it used to answer an empty list;
    * a FLOAT ``asof_ms`` was dropped and now lifts (the pack's order
      predicate takes a finite float, which is what lets a foreign
      vocabulary in at all);
    * an attribute-bearing record was dropped and now lifts, because
      dicts and envelopes are interchangeable everywhere in the pack.

    All three are degenerate-input behaviour, which is exactly why they
    need a pin: nothing else in this suite would notice them moving.
    """
    def rows_for(records):
        return WindowRows("w", {"lookback": 2}).run(
            None, {"records": records})["records"]

    good = [{"symbol": "AAPL", "asof_ms": _ms(i), "close": 100.0 + i}
            for i in range(5)]
    nameless = [{"symbol": "", "asof_ms": _ms(i), "close": 50.0 + i}
                for i in range(5)]
    assert [r["symbol"] for r in rows_for(good + nameless)] == ["AAPL"] * 2
    with pytest.raises(ValueError, match="was unlifted"):
        rows_for(nameless)

    floated = [{"symbol": "AAPL", "asof_ms": float(_ms(i)), "close": 100.0 + i}
               for i in range(5)]
    assert len(rows_for(floated)) == 2

    objects = [SimpleNamespace(symbol="AAPL", asof_ms=_ms(i), close=100.0 + i)
               for i in range(5)]
    assert len(rows_for(objects)) == 2


def test_window_rows_drops_a_NON_FINITE_price_the_pre_port_node_KEPT():
    """The FIFTH divergence — the only one on ordinary-looking input.

    The pre-port filter was ``price <= 0``, and NEITHER ``inf`` NOR
    ``nan`` satisfies it: both rode straight into the chain and produced
    a non-finite log return, which every window overlapping them then
    carried into training. ``keep_mask`` asks for a FINITE positive
    price, so such a minute is now dropped, counted in ``n_dropped``,
    and the survivors chain across it — the same treatment a missing or
    non-positive price already got.

    It is listed in the ``WindowRows`` docstring beside the other four
    and pinned here, because an unlisted behaviour change in a node the
    SERVING path also calls is how train/serve skew comes back.
    """
    def rows_for(bad):
        records = [{"symbol": "AAPL", "asof_ms": _ms(i), "close": 100.0 + i}
                   for i in range(6)]
        if bad is not None:
            records[3]["close"] = bad
        else:
            del records[3]
        return WindowRows("w", {"lookback": 2}).run(
            None, {"records": records})["records"]

    # A dropped bar and an ABSENT bar must produce the same windows: the
    # non-finite price is not data, and the neighbours chain across it.
    absent = rows_for(None)
    for bad in (float("inf"), float("-inf"), float("nan")):
        got = rows_for(bad)
        assert got == absent, bad
        assert all(math.isfinite(v) for row in got for v in row.values()
                   if isinstance(v, float)), bad

    # And the claim the docstring makes about itself stays true.
    assert "finite" in WindowRows.__doc__


def test_window_rows_orders_same_instant_bars_by_the_STREAM(price_field="close"):
    """The FIRST of the places the port changed what the child computes.

    (The others — degenerate symbols, float stamps and non-dict records
    — are pinned by the test above; the non-finite price by the one
    below it.)

    Two bars of one symbol can share an ``asof_ms``: ``BarsFromStore``
    orders by ``(asof_ms, symbol, ts)`` and two ``ts`` spellings can
    flatten onto one instant (ADR-0037). The pre-port implementation
    sorted ``(asof_ms, price)`` TUPLES, so those two ordered BY PRICE —
    an accident of tuple comparison with no domain meaning, and not
    reproducible for a stream whose price is absent. The pack breaks the
    tie by STREAM POSITION, which is the store's own ``ts`` order, so
    the chain reads them the way the vendor published them.

    Declared rather than silent: this is the divergence the port pin
    (distinct stamps only) cannot see.
    """
    rows = [
        {"symbol": "AAPL", "asof_ms": _ms(0), "close": 100.0},
        {"symbol": "AAPL", "asof_ms": _ms(1), "close": 103.0},
        {"symbol": "AAPL", "asof_ms": _ms(1), "close": 101.0},  # same instant
        {"symbol": "AAPL", "asof_ms": _ms(2), "close": 102.0},
        {"symbol": "AAPL", "asof_ms": _ms(3), "close": 104.0},
    ]
    out = WindowRows("window", {"lookback": 2}).run(None, {"records": rows})["records"]

    assert [r["asof_ms"] for r in out] == [_ms(1), _ms(2)]
    # 100 -> 103 -> 101 -> 102 -> 104, in the order the stream carried
    # them; a price-ordered tie-break would read 100 -> 101 -> 103.
    assert out[0]["ret_lag_1"] == pytest.approx(math.log(103.0 / 100.0))
    assert out[0]["ret_lag_0"] == pytest.approx(math.log(101.0 / 103.0))
    assert out[0]["y_next"] == pytest.approx(math.log(102.0 / 101.0))
    assert out[1]["ret_lag_0"] == pytest.approx(math.log(102.0 / 101.0))
    assert out[1]["y_next"] == pytest.approx(math.log(104.0 / 102.0))


def test_window_rows_narrowed_every_accessor_it_answers():
    """The pack's rule, held here (ADR-0040).

    This node answers eleven of the pack's accessors from its own
    vocabulary, and every one of them must be gone from ``_PARAMS`` — or
    default-deny would approve a document knob the run discards. The
    pack REFUSES such a class at construction, so this is belt and
    braces; it is here because the failure it prevents is silent for the
    document's author, not for the class's.
    """
    from dskit.pipeline.libs.numpy import (
        ReturnWindows,
        accessor_narrowing_problems,
    )

    assert accessor_narrowing_problems(WindowRows) == []
    # Derived from the class itself, not from a table the pack keeps:
    # every accessor this node answers differently from the pack.
    overridden = [
        knob for knob in ReturnWindows._PARAMS
        if getattr(WindowRows, knob, None) is not getattr(ReturnWindows, knob, None)
    ]
    assert len(overridden) == 11, overridden
    assert set(overridden) & set(WindowRows._PARAMS) == set()
    # And the knobs that DID survive are the ones the documents write.
    assert set(WindowRows._PARAMS) == {"causality_check", "cuts", "lookback",
                                       "max_gap_minutes", "price_field"}


def test_window_rows_inherits_the_causality_screen():
    """The screen this node never had, now on by default.

    A subclass that reached forward in a LAG column is refused; the
    label is not, because it declares its horizon. Both halves matter:
    a screen that waved the whole class through would be theatre.
    """
    from dskit.pipeline.libs.numpy import lead

    rows = [{"symbol": "AAPL", "asof_ms": _ms(i), "close": 100.0 + i}
            for i in range(8)]
    assert WindowRows("w", {"lookback": 2}).run(
        None, {"records": rows})["records"], "the declared label passes"

    class _Leaky(WindowRows):
        def apply(self, arrays, params):
            columns = super().apply(arrays, params)
            columns["ret_lag_0"] = lead(columns["ret_lag_0"], 1)
            return columns

    with pytest.raises(ValueError, match="not causal"):
        _Leaky("w", {"lookback": 2}).run(None, {"records": rows})


def test_window_rows_lags_labels_and_gap_discipline():
    """ret_lag_0 is the return ENDING at asof_ms, y_next the one after;
    a gap over max_gap_minutes breaks the chain — no row bridges it."""
    closes = [100.0, 101.0, 100.5, 102.0, 101.0]
    rows = [{"symbol": "AAPL", "asof_ms": _ms(i), "close": closes[i]}
            for i in range(4)]
    # The fifth bar arrives after a 30-minute hole.
    rows.append({"symbol": "AAPL", "asof_ms": _ms(34), "close": closes[4]})

    node = WindowRows("window", {"lookback": 2, "max_gap_minutes": 5})
    out = node.run(None, {"records": rows})["records"]

    # Chain of returns: r1 (t1), r2 (t2), r3 (t3); a window needs 2
    # returns and a next-return label -> exactly one row, at t2.
    assert len(out) == 1
    row = out[0]
    assert row["asof_ms"] == _ms(2)
    assert row["ret_lag_0"] == pytest.approx(math.log(closes[2] / closes[1]))
    assert row["ret_lag_1"] == pytest.approx(math.log(closes[1] / closes[0]))
    assert row["y_next"] == pytest.approx(math.log(closes[3] / closes[2]))


_CLOSES = [100.0, 100.7, 100.2, 101.1, 100.9, 101.4]
#: A deliberately DIFFERENT series in the same bars — every parity
#: assertion below would pass on either field if the two agreed.
_VWAPS = [round(c + 0.35, 6) for c in _CLOSES]


def _vendor_bars():
    """Alpaca-shaped minute bars (only the attributes this loop reads),
    so the serving side is exercised through the same extraction the
    REST fetch uses — no vendor SDK required."""
    return [SimpleNamespace(timestamp=_BASE + timedelta(minutes=i),
                            close=close, vwap=vwap)
            for i, (close, vwap) in enumerate(zip(_CLOSES, _VWAPS))]


@pytest.mark.parametrize("price_field", ["close", "vwap"])
def test_live_serves_the_training_row_for_the_same_key(price_field):
    """A SERVING row equals the TRAINING row for the same (symbol, bar).

    The parity test this replaces compared two implementations of the
    lag construction; a mechanism-only parity test cannot catch a
    differing FIELD, which was the actual defect (audit HIGH-4). Now
    there is one implementation — the loop calls ``latest_rows`` on the
    very node the document declares — so what is worth asserting is that
    the two CALLS agree, key for key, including which series they price.
    The vwap case still fails against any loop that prices on close.
    """
    from intraday_poc.live import bar_series, window_records

    rows = [{"symbol": "AAPL", "asof_ms": _ms(i), "close": close,
             "vwap": vwap}
            for i, (close, vwap) in enumerate(zip(_CLOSES, _VWAPS))]
    node = WindowRows("window", {"lookback": 3, "price_field": price_field,
                                 "max_gap_minutes": 5})
    trained = {row["asof_ms"]: row
               for row in node.run(None, {"records": rows})["records"]}

    # The vendor's bars, through the loop's own extraction, minus the
    # newest one — so the serving row lands on a bar that HAS a label
    # row to be compared against.
    series = window_records(node, "AAPL",
                            bar_series(_vendor_bars(), price_field))
    served = node.latest_rows(series[:-1])["AAPL"]

    training = trained[served["asof_ms"]]
    assert served == {k: v for k, v in training.items() if k != "y_next"}
    prices = _CLOSES if price_field == "close" else _VWAPS
    for lag in range(3):
        expect = math.log(prices[4 - lag] / prices[3 - lag])
        assert served[f"ret_lag_{lag}"] == pytest.approx(expect)


def test_the_serving_records_speak_the_NODE_s_vocabulary_not_a_copy():
    """The loop restates NO key field — it asks the node for all of them.

    ``window_records`` wrote ``{"symbol": ..., "asof_ms": ...}`` as
    literals while reading only the price field off the node, but
    ``WindowRows.group_field()`` and ``order_field()`` are the accessors
    that OWN those two spellings. Retune ``order_field()`` to ``ts_ms``
    alongside the store and training is unaffected, the loop goes on
    emitting ``asof_ms``, every record is unlifted, and the first fetch
    dies inside the pack's "every one of the N input record(s) was
    unlifted" refusal — pointing at the fetch rather than at the copy.
    Same shape as ``DEFAULT_PRICE_FIELD``, same answer: a serving path
    never restates a training knob.
    """
    from intraday_poc.live import window_records

    class _Retuned(WindowRows):
        def group_field(self):
            return "ticker"

        def order_field(self):
            return "ts_ms"

    node = _Retuned("window", {"lookback": 3, "max_gap_minutes": 5,
                               "price_field": "vwap"})
    series = [(_ms(i), price) for i, price in enumerate(_VWAPS)]
    records = window_records(node, "AAPL", series)

    assert set(records[0]) == {"ticker", "ts_ms", "vwap"}
    served = node.latest_rows(records)["AAPL"]
    assert served["ticker"] == "AAPL"
    assert served["ts_ms"] == _ms(len(_VWAPS) - 1)


def test_the_serving_row_never_carries_a_label(price_field="close"):
    """``y_next`` does not exist yet at the newest bar — and a serving
    row that carried one would be reading the future."""
    from intraday_poc.live import bar_series, window_records

    node = WindowRows("window", {"lookback": 3, "max_gap_minutes": 5})
    series = window_records(node, "AAPL",
                            bar_series(_vendor_bars(), price_field))
    served = node.latest_rows(series)["AAPL"]
    assert "y_next" not in served
    assert served["asof_ms"] == _ms(len(_CLOSES) - 1)


def test_bar_series_refuses_a_field_the_vendor_does_not_carry():
    """A document naming a price field Alpaca does not publish must be
    loud: silently pricing on nothing would hand the model an empty
    series and call it "no coverage"."""
    from intraday_poc.live import bar_series

    with pytest.raises(SystemExit, match="price_field"):
        bar_series(_vendor_bars(), "mid")


def test_the_third_copy_of_the_chain_semantics_is_gone():
    """``latest_feature_row`` DIES (ADR-0040).

    It restated the chain on a HARDCODED price field — the train/serve
    skew of audit HIGH-4 — and no parity test over the mechanism could
    see it. The serving path now calls the window node itself.
    """
    from intraday_poc import live

    assert not hasattr(live, "latest_feature_row")
    assert "latest_feature_row" not in live.__all__


def test_live_serving_refuses_gaps_and_short_history():
    """Coverage the window cannot support serves NOTHING — never a
    staler row, never a bridged session break."""
    node = WindowRows("window", {"lookback": 3, "max_gap_minutes": 5})
    bars = [{"symbol": "AAPL", "asof_ms": _ms(i), "close": 100.0 + i}
            for i in range(4)]

    assert WindowRows("window", {"lookback": 5}).latest_rows(bars) == {}
    gapped = bars[:2] + [{"symbol": "AAPL", "asof_ms": _ms(30), "close": 105.0},
                         {"symbol": "AAPL", "asof_ms": _ms(31), "close": 106.0}]
    assert node.latest_rows(gapped) == {}


def test_live_serving_refuses_a_newest_bar_with_no_usable_price():
    """A priceless newest minute makes the symbol ABSENT, not stale.

    ``keep_mask`` drops that bar and the survivors chain — right for
    TRAINING, wrong for SERVING: the newest survivor is a minute old,
    and a loop handed it would trade a stale feature vector with no
    signal that it did. The deleted ``latest_feature_row`` refused
    exactly this (``if tail[i][1] <= 0: return None``), so this is the
    regression bar for its replacement.
    """
    node = WindowRows("window", {"lookback": 2, "max_gap_minutes": 5})
    bars = [{"symbol": "AAPL", "asof_ms": _ms(i), "close": 100.0 + i}
            for i in range(6)]
    assert node.latest_rows(bars)["AAPL"]["asof_ms"] == _ms(5)

    bars.append({"symbol": "AAPL", "asof_ms": _ms(6), "close": 0.0})
    assert node.latest_rows(bars) == {}

    # A hole BEHIND the newest bar is the case the chain reads through.
    bars[3] = {"symbol": "AAPL", "asof_ms": _ms(3), "close": 0.0}
    bars[-1] = {"symbol": "AAPL", "asof_ms": _ms(6), "close": 106.0}
    assert node.latest_rows(bars)["AAPL"]["asof_ms"] == _ms(6)


@pytest.mark.skipif(not HAVE_SOLVER, reason="pyomo/highspy not installed")
def test_select_one_picks_the_larger_prediction(tmp_path):
    """Per timestamp the larger predicted return wins, and realized PnL
    joins from the labels."""
    from intraday_poc.nodes import SelectOne

    forecasts = [
        {"symbol": "AAPL", "asof_ms": _ms(1), "pred": 0.002},
        {"symbol": "MSFT", "asof_ms": _ms(1), "pred": 0.001},
        {"symbol": "AAPL", "asof_ms": _ms(2), "pred": -0.004},
        {"symbol": "MSFT", "asof_ms": _ms(2), "pred": -0.001},
    ]
    labeled = [
        {"symbol": "AAPL", "asof_ms": _ms(1), "y_next": 0.005},
        {"symbol": "MSFT", "asof_ms": _ms(2), "y_next": -0.002},
    ]
    node = SelectOne("select", {"split": "val"})
    out = node.run(_ctx(tmp_path), {"forecasts": forecasts,
                                    "labeled": labeled})
    picks = {p["asof_ms"]: p["symbol"] for p in out["picks"]}
    assert picks == {_ms(1): "AAPL", _ms(2): "MSFT"}
    assert out["metrics"]["n_picks"] == 2
    assert out["metrics"]["total_realized"] == pytest.approx(0.005 - 0.002)


def test_select_one_empty_forecasts_skip_the_solver():
    """No solver import, no solve — an empty selection is a result."""
    from intraday_poc.nodes import SelectOne

    node = SelectOne("select", {"split": "val"})
    out = node.run(None, {"forecasts": [], "labeled": []})
    assert out == {"picks": [], "metrics": {
        "n_picks": 0, "total_pred": 0.0, "n_realized": 0,
        "total_realized": 0.0}}


# -- the serving loop READS what the run already declared -------------------


def _run_dir(tmp_path, window_params, uses="intraday_poc-window"):
    """A REAL run dir for a bars -> window document.

    The driver writes the whole document to ``<run-dir>/config.json``,
    which is exactly what the live loop reads — so these tests read the
    driver's own output rather than a hand-made dict that could agree
    with the reader and disagree with production.
    """
    root = str(tmp_path / "ob")
    _write_store(root, n_minutes=20, symbols=("AAPL",))
    doc = {
        "name": "knobs",
        "pipeline": {
            "bars": {"uses": "intraday_poc-bars",
                     "params": {"root": root, "source": "alpaca"}},
            "window": {"uses": uses,
                       "inputs": {"records": "$bars.records"},
                       "params": dict(window_params)},
        },
    }
    path = tmp_path / "knobs.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    document = replace(load_document(str(path)),
                       outputs=OutputsConfig(run_root=str(tmp_path / "runs")))
    result = run_document(document, asof="2026-01-06")
    assert result.state == "ran", (result.state, result.error)
    return result.run_dir


def _document(pipeline):
    """A typed document from a bare node map — what the loop reads once
    it loads its run through the engine's own loader (Ruling 3)."""
    return PipelineDocument.from_obj({"name": "serving", "pipeline": pipeline})


def test_live_reads_the_window_knobs_the_run_declared(tmp_path):
    """price_field and max_gap_minutes come from the run dir, never from
    a second copy in the loop: a serving path that restates a training
    knob drifts the day someone tunes the document."""
    from intraday_poc.live import load_run_document, window_knobs

    run_dir = _run_dir(tmp_path, {"lookback": 3, "price_field": "vwap",
                                  "max_gap_minutes": 7})
    assert window_knobs(load_run_document(run_dir)) == ("vwap", 7.0)


def test_live_falls_back_to_the_window_nodes_own_defaults(tmp_path,
                                                          monkeypatch):
    """An undeclared knob resolves to the NODE's constant.

    The behavioural half of
    ``test_the_serving_loop_binds_no_window_default_at_all``: rebinding
    the constant in ``nodes`` moves the SERVING answer, which nothing
    but reading it through the node can do. A copy in ``live.py`` — the
    defect — would leave this answer where it was.
    """
    from intraday_poc import live

    run_dir = _run_dir(tmp_path, {"lookback": 3})
    document = live.load_run_document(run_dir)
    assert live.window_knobs(document) == (nodes.DEFAULT_PRICE_FIELD,
                                           float(nodes.DEFAULT_MAX_GAP_MINUTES))
    monkeypatch.setattr(nodes, "DEFAULT_PRICE_FIELD", "vwap")
    monkeypatch.setattr(nodes, "DEFAULT_MAX_GAP_MINUTES", 9)
    assert live.window_knobs(document) == ("vwap", 9.0)


def test_live_refuses_a_run_dir_it_cannot_read(tmp_path):
    """Loud, always: a missing document, a document with no window node,
    and two window nodes that disagree are each a refusal — never a
    quietly assumed default.

    The three cases are now expressed as DOCUMENTS rather than raw
    dicts, because the loop reads its run through the engine's loader
    (Ruling 3) — the empty-pipeline case, which the grammar itself
    refuses, is therefore a document that simply declares no window
    node.
    """
    from intraday_poc.live import load_run_document, window_knobs

    with pytest.raises(SystemExit, match="config.json"):
        load_run_document(str(tmp_path))

    with pytest.raises(SystemExit, match="intraday_poc-window"):
        window_knobs(_document({"bars": {"uses": "intraday_poc-bars",
                                         "params": {"root": ".",
                                                    "source": "alpaca"}}}))

    two = _document({
        "a": {"uses": "intraday_poc-window",
              "params": {"lookback": 3, "price_field": "close"}},
        "b": {"uses": "intraday_poc-window",
              "params": {"lookback": 3, "price_field": "vwap"}},
    })
    with pytest.raises(SystemExit, match="disagree"):
        window_knobs(two)


def test_live_reads_the_run_document_through_the_engines_own_loader(tmp_path):
    """The run document is TIER-1 truth — read it, never re-derive it.

    A hand-rolled ``json.load`` plus an "is ``pipeline`` a dict" check
    ACCEPTS documents the engine refuses: a dangling ``$`` wire, a node
    that is not an object, a missing series ``name``. The loop then
    resolves knobs off a node the engine would never plan, or blames the
    DOCUMENT for the reader's own gap ("the run declares no ... node").
    ``dskit.pipeline.document.load_document`` already reads the file,
    refuses non-JSON, refuses a non-object document, validates the whole
    node-map grammar and returns typed ``NodeSpec``s — so it is what
    runs, and ITS message is what the operator sees. (Ruling 3.)
    """
    from intraday_poc.live import load_run_document, window_knobs

    run_dir = tmp_path / "badrun"
    run_dir.mkdir()
    (run_dir / "config.json").write_text(json.dumps({
        "name": "knobs",
        "pipeline": {"window": {"uses": "intraday_poc-window",
                                "inputs": {"records": "$nope.records"},
                                "params": {"lookback": 3,
                                           "price_field": "vwap"}}},
    }), encoding="utf-8")

    with pytest.raises(ValueError) as engine:
        load_document(str(run_dir / "config.json"))
    with pytest.raises(SystemExit) as refusal:
        load_run_document(str(run_dir))
    assert str(refusal.value) == str(engine.value)
    assert "$nope" in str(refusal.value), refusal.value

    # A well-formed run dir still reads back — as the TYPED document,
    # which is what lets the child drop the guards NodeSpec already
    # makes.
    document = load_run_document(_run_dir(tmp_path, {"lookback": 3,
                                                     "price_field": "vwap"}))
    assert document.pipeline["window"].params["price_field"] == "vwap"
    assert window_knobs(document)[0] == "vwap"


def test_live_serves_a_document_that_names_the_class_directly(tmp_path):
    """A window node declared by CLASS REFERENCE is still readable.

    The document grammar accepts ``"uses": "<kind or pkg.module:Class>"``
    and the engine plans either spelling, so a loop that matches only
    the registered kind name refuses a legitimately-trained run — and
    blames the document for its own lookup. The knobs must resolve off
    the class the reference names, whichever spelling wrote it.
    """
    from intraday_poc.live import load_run_document, window_knobs

    run_dir = _run_dir(tmp_path, {"lookback": 3, "price_field": "vwap",
                                  "max_gap_minutes": 7},
                       uses="intraday_poc.nodes:WindowRows")
    assert window_knobs(load_run_document(run_dir)) == ("vwap", 7.0)

    # A reference to some OTHER class is not a window node, and a
    # reference that cannot be imported is not one either — neither may
    # be mistaken for one, and neither may raise anything but the
    # loop's own refusal. (Documents, not raw dicts, per Ruling 3.)
    for other in ("intraday_poc.nodes:ForecastRows", "no.such:Module"):
        with pytest.raises(SystemExit, match="intraday_poc-window"):
            window_knobs(_document({"w": {"uses": other,
                                          "params": {"lookback": 3}}}))


def test_live_reads_the_declared_module_from_the_run_document():
    """ADR-0025's seam: the document names the module class, so the
    serving loop must too. A literal in the loop makes swapping the
    declared class break serving — which is the whole point of the
    seam.

    Built as a typed document, per Ruling 3: the "no module declared"
    case is now a node with a legal ``uses`` and no ``module`` param,
    since the grammar refuses a node with no ``uses`` at all — that
    refusal is the ENGINE's, and pinning it here would only restate it.
    """
    from intraday_poc.live import declared_module

    nodemap = {
        "window": {"uses": "intraday_poc-window", "params": {"lookback": 3}},
        "qhat_aapl": {"uses": "dskit.pipeline.libs.torch:DeclaredTrain",
                      "params": {"module": "somepkg.models:OtherNet"}},
        "qhat_msft": {"uses": "dskit.pipeline.libs.torch:DeclaredTrain",
                      "params": {"module": "somepkg.models:OtherNet"}},
    }
    assert declared_module(_document(nodemap)) == "somepkg.models:OtherNet"

    with pytest.raises(SystemExit, match="declares no"):
        declared_module(_document({"window": {"uses": "intraday_poc-window",
                                              "params": {}}}))

    nodemap["qhat_msft"]["params"]["module"] = "somepkg.models:Net2"
    with pytest.raises(SystemExit, match="several module classes"):
        declared_module(_document(nodemap))

    from dskit.pipeline.libs.torch_ts import TimeSeriesTrain

    zoo = {
        "window": {"uses": "intraday_poc-window", "params": {"lookback": 3}},
        "qhat_aapl": {"uses": "torch-ts-train",
                      "params": {"arch": "lstm", "seq_len": 3}},
        "qhat_msft": {"uses": "dskit.pipeline.libs.torch_ts:TimeSeriesTrain",
                      "params": {"arch": "lstm", "seq_len": 3}},
    }
    assert declared_module(_document(zoo)) == TimeSeriesTrain._class_ref()


def test_a_predict_node_that_pins_arch_is_not_a_trainer():
    """``arch`` is a zoo param, not a trainer mark.

    TimeSeriesPredict may pin ``arch`` (optional, not forbidden). A
    classifier that keys on the param treats ``serve_aapl`` as a second
    trainer for AAPL and the loop refuses a legal document.
    """
    from intraday_poc.live import artifact_dirs

    doc = _document({
        "window": {"uses": "intraday_poc-window", "params": {"lookback": 3}},
        "qhat_aapl": {"uses": "torch-ts-train",
                      "params": {"arch": "lstm", "seq_len": 3}},
        "serve_aapl": {"uses": "torch-ts-predict",
                       "params": {"arch": "lstm", "artifact": "x.pt"}},
    })
    dirs = artifact_dirs(doc, "/runs/r1", ["AAPL"], {})
    assert dirs["AAPL"] == os.path.join("/runs/r1", "artifacts", "qhat_aapl")


def test_the_loop_reads_the_nodes_the_run_RAN_not_the_ones_it_declared():
    """A fanned-out document keeps its nodes in ``foreach.pipeline``.

    ADR-0039 splits a document in two: ``pipeline`` is what the author
    WROTE and ``expanded`` is what the engine RAN, and they are the same
    object only when there is no ``foreach``. run-train.json now has
    one, so its trainers are template instances (only in ``expanded``)
    — and a loop reading the declared map alone finds no ``module`` and
    refuses a run that trained perfectly good models. Both readers
    therefore read what RAN; documents without a fan-out are unaffected,
    because for them the two maps ARE one.

    BOTH nodes are fanned here, which is the whole test: with the window
    node left in the SHARED map (where the shipped document happens to
    keep it) ``window_knobs`` reads the same spec off either map, so the
    reader half of this pin did not bite — reverting ``_window_nodes``
    to ``document.pipeline`` left the suite green. A document that fans
    its window node is legal today, so the fixture fans it: only a
    reader of ``expanded`` finds it, and the two instances agree, which
    is what ``window_node`` collapses to one.
    """
    from intraday_poc.live import declared_module, window_knobs

    document = PipelineDocument.from_obj({
        "name": "serving",
        "foreach": {
            "keys": ["AAPL", "MSFT"],
            "pipeline": {
                "qhat": {"uses": "dskit.pipeline.libs.torch:DeclaredTrain",
                         "params": {"module": "somepkg.models:OtherNet"}},
                "window": {"uses": "intraday_poc-window",
                           "params": {"lookback": 3, "price_field": "vwap",
                                      "max_gap_minutes": 7}},
            },
        },
        "pipeline": {},
    })
    assert not document.pipeline, (
        "both readers must have to look at `expanded`, or one of the two "
        "halves is pinned by a map that carries the node anyway"
    )
    assert declared_module(document) == "somepkg.models:OtherNet"
    assert window_knobs(document) == ("vwap", 7.0)


def _serving_obj(trainers, **window_params):
    """A minimal RUN document: a window node, N trainers, one selector.

    What the loop READS out of a run dir, in the smallest document that
    carries it — the window knobs, one trainer node per symbol (keyed
    the way the documents key them), and the selector node whose solver
    the loop solves its own minute with. Written as one helper because
    three tests need the same shape, and a fourth spelling of it is a
    fourth thing to update.
    """
    return {
        "name": "serving",  # a run document, per the engine's grammar
        "pipeline": {
            "window": {"uses": "intraday_poc-window",
                       "params": {"lookback": 3, **window_params}},
            **{key: {"uses": "dskit.pipeline.libs.torch:DeclaredTrain",
                     "params": {"module": "somepkg.models:OtherNet"}}
               for key in trainers},
            "select": {"uses": "intraday_poc-select-one",
                       "params": {"solver": "appsi_highs",
                                  "solver_options": {}, "split": "val"}},
        },
        # A node declaring `split` obliges the document to declare the
        # cuts; the loop reads neither, so any causal triple will do.
        "splits": {"kind": "time", "train_end_ms": 1, "val_end_ms": 2,
                   "test_end_ms": 3},
    }


def _serving_document(trainers, **window_params):
    """:func:`_serving_obj`, typed by the engine's own loader."""
    return PipelineDocument.from_obj(_serving_obj(trainers, **window_params))


def _fanned_document(keys=("AAPL", "MSFT")):
    """A run document whose ONE trainer template fans out over ``keys``."""
    return PipelineDocument.from_obj({
        "name": "serving",
        "foreach": {
            "keys": list(keys),
            "pipeline": {
                "qhat": {"uses": "dskit.pipeline.libs.torch:DeclaredTrain",
                         "params": {
                             "module": "somepkg.models:OtherNet"}},
            },
        },
        "pipeline": {
            "window": {"uses": "intraday_poc-window",
                       "params": {"lookback": 3}},
        },
    })


def test_artifact_paths_come_off_the_trainer_keys_the_run_wrote(tmp_path):
    """The loop reads the run's OWN node keys; ``--artifact`` still bends.

    A convention restated here (``artifacts/qhat_<symbol>``) is a
    serving path restating a training knob — root CLAUDE.md forbids it
    — and it went stale the moment ``run-train.json`` fanned its trainer
    out of a ``foreach`` template: the instance keys carry the fan-out's
    DOUBLE underscore (``qhat__aapl``), so every operator serving the
    shipped run had to hand-type a ``--artifact`` pair or take a startup
    SystemExit. The mapping was already on disk: the document the loop
    loads keys one trainer per symbol, and a trainer is the node
    declaring ``module`` — the same rule :func:`declared_module` reads.
    """
    from intraday_poc.live import artifact_dirs, parse_artifact_overrides

    assert parse_artifact_overrides(["AAPL=artifacts/other"]) == \
        {"AAPL": "artifacts/other"}
    with pytest.raises(SystemExit, match="SYMBOL=PATH"):
        parse_artifact_overrides(["AAPL"])

    fanned = _fanned_document()
    dirs = artifact_dirs(fanned, "/runs/r1", ["AAPL", "MSFT"], {})
    assert dirs["AAPL"] == os.path.join("/runs/r1", "artifacts", "qhat__aapl")
    assert dirs["MSFT"] == os.path.join("/runs/r1", "artifacts", "qhat__msft")

    longhand = _serving_document(("qhat_aapl", "qhat_msft"))
    dirs = artifact_dirs(longhand, "/runs/r1", ["AAPL", "MSFT"],
                         {"MSFT": "artifacts/other"})
    assert dirs["AAPL"] == os.path.join("/runs/r1", "artifacts", "qhat_aapl")
    assert dirs["MSFT"] == os.path.join("/runs/r1", "artifacts", "other")
    absolute = artifact_dirs(longhand, "/runs/r1", ["AAPL"],
                             {"AAPL": str(tmp_path / "elsewhere")})
    assert absolute["AAPL"] == str(tmp_path / "elsewhere")


def test_a_symbol_the_run_trained_no_model_for_is_refused_by_name():
    """No trainer for a symbol is a refusal that NAMES what the run keyed.

    Falling back to a convention here is what produced the old failure
    mode: a path nothing wrote, then ``artifact incomplete:
    .../model.pt is missing`` — true, and silent about the six other
    directories sitting beside it. The refusal lists the trainer keys
    the run actually wrote, which is also exactly what an operator would
    pass to ``--artifact``.
    """
    from intraday_poc.live import artifact_dirs

    with pytest.raises(SystemExit, match="qhat__aapl"):
        artifact_dirs(_fanned_document(), "/runs/r1", ["AAPL", "GOOG"], {})
    # ...and an override is the answer to it, so it must be consulted
    # BEFORE the derivation, never after.
    dirs = artifact_dirs(_fanned_document(), "/runs/r1", ["AAPL", "GOOG"],
                         {"GOOG": "artifacts/goog_v2"})
    assert dirs["GOOG"] == os.path.join("/runs/r1", "artifacts", "goog_v2")


def test_a_fanned_trainer_answers_only_for_the_symbol_it_was_built_for():
    """A SUFFIX match lets one symbol's model serve another's bars.

    The fan-out's instance key is ``<template>__<slug>``, so a rule that
    only asks whether a key ENDS in ``_<slug>`` reads ``qhat__brk_b`` as
    a match for the symbol ``B``: the loop would restore BRK.B's weights,
    push B's bars through them and hand the prediction to the selector,
    and nothing downstream could see it — the regime cross-check compares
    two artifacts, and here both symbols share ONE. Alpaca spells real
    tickers this way (BRK.B, BF.B beside A, B, C), and the fan-out's own
    notes invite exactly this edit ("add a key here").

    The mapping needs no heuristic: ``foreach.keys`` plus the engine's
    ``foreach_slug`` builds every instance key, so an instance answers
    for the ONE key it was built from. Hand-declared trainers keep the
    suffix rule — nothing on disk says which stem is which there — and
    the ambiguous case among them is still the refusal it was.
    """
    from intraday_poc.live import artifact_dirs

    # The fan-out trained BRK.B alone; B is a different symbol.
    one = _fanned_document(keys=["BRK.B"])
    assert sorted(one.expanded) == ["qhat__brk_b", "window"]
    assert artifact_dirs(one, "/runs/r1", ["BRK.B"], {})["BRK.B"] == \
        os.path.join("/runs/r1", "artifacts", "qhat__brk_b")
    with pytest.raises(SystemExit, match="qhat__brk_b"):
        artifact_dirs(one, "/runs/r1", ["BRK.B", "B"], {})

    # ...and when the run DID train both, each gets its own — the suffix
    # rule matched B against both keys and refused to serve at all.
    both = _fanned_document(keys=["BRK.B", "B"])
    dirs = artifact_dirs(both, "/runs/r1", ["BRK.B", "B"], {})
    assert dirs["BRK.B"] == os.path.join("/runs/r1", "artifacts",
                                         "qhat__brk_b")
    assert dirs["B"] == os.path.join("/runs/r1", "artifacts", "qhat__b")

    # A hand-written document carries no fan-out to read, so the suffix
    # rule still serves it — and still refuses an ambiguous pair rather
    # than picking one.
    longhand = _serving_document(("qhat_aapl", "qhat_msft"))
    assert artifact_dirs(longhand, "/runs/r1", ["AAPL"], {})["AAPL"] == \
        os.path.join("/runs/r1", "artifacts", "qhat_aapl")
    ambiguous = _serving_document(("qhat_brk_b", "qhat_b"))
    with pytest.raises(SystemExit, match="2 trainers"):
        artifact_dirs(ambiguous, "/runs/r1", ["B"], {})


def test_which_instance_belongs_to_which_key_is_read_not_recomposed():
    """The fan-out's naming is the ENGINE's answer, asked once.

    ``PipelineDocument`` derives a PUBLIC ``foreach_groups`` — template
    key -> instance keys, zipped against ``foreach.keys`` in that order
    inside ``_expand`` — so the pairing already exists, sourced from the
    same statement that built the names. Composing them again here
    (``f"{template}__{slug}"``) is the engine's PRIVATE ``_instance_key``
    written a second time with nothing pinning the pair, which is the
    duplication class root CLAUDE.md calls a scheduled bug.

    The scheduled bug, concretely: ``_instance_key`` is private exactly
    so the engine may change how a name is assembled. Let it stop
    slugging (or add a prefix, or join differently) and the child's
    rebuilt names match nothing in ``expanded``; every trainer then falls
    through to the SUFFIX branch this file's neighbour documents as
    unsafe, ``qhat__brk_b`` answers for ``B`` again, both symbols read
    ONE artifact, and the pair regime-check cannot see it because it is
    comparing an artifact with itself. No ordinary assertion catches
    that, because every test here would build its expectation the same
    rebuilt way — so the engine's composition is REBOUND instead, and
    the loop has to follow it.
    """
    from intraday_poc.live import artifact_dirs

    document = _fanned_document()
    assert artifact_dirs(document, "/runs/r1", ["AAPL"], {})["AAPL"] == \
        os.path.join("/runs/r1", "artifacts", "qhat__aapl")

    # The engine, hypothetically, stops lowercasing an instance name.
    # Nothing else about the document changes — and a reader of
    # `foreach_groups` needs no telling, while a rebuilder keeps its own
    # stale spelling and matches none of the keys that ran.
    regrouped = {template: tuple(f"{template}{FOREACH_SEP}{key}"
                                 for key in document.foreach.keys)
                 for template in document.foreach_groups}
    renames = {old: new
               for template, names in document.foreach_groups.items()
               for old, new in zip(names, regrouped[template])}
    object.__setattr__(document, "expanded", {
        renames.get(key, key): spec
        for key, spec in document.expanded.items()
    })
    object.__setattr__(document, "foreach_groups", regrouped)

    dirs = artifact_dirs(document, "/runs/r1", ["AAPL", "MSFT"], {})
    assert dirs["AAPL"] == os.path.join("/runs/r1", "artifacts", "qhat__AAPL")
    assert dirs["MSFT"] == os.path.join("/runs/r1", "artifacts", "qhat__MSFT")


def test_an_artifact_override_for_an_unserved_symbol_is_refused():
    """A typo in ``--artifact`` is an error, not a silent default.

    The mapping is consumed by lookup, so an override keyed to a symbol
    the source config does not declare is simply never read: an
    operator who retrains MSFT and mistypes ``--artifact
    MFST=...artifacts/qhat_msft_v2`` gets the OLD model restored, a
    cheerful "models restored for ['AAPL', 'MSFT']", and trades on the
    weights they explicitly tried to replace. Default-deny says refuse,
    naming the universe.
    """
    from intraday_poc.live import artifact_dirs

    with pytest.raises(SystemExit, match="MFST"):
        artifact_dirs(_fanned_document(), "/runs/r1", ["AAPL", "MSFT"],
                      {"MFST": "artifacts/qhat_msft_v2"})


def test_the_loop_refuses_a_pair_of_artifacts_trained_at_different_widths(
        monkeypatch):
    """The tuned run's ONE unpinned agreement, pinned where it lands.

    ``foreach`` pins the two symbols' DECLARED params to one template,
    but ``hpo-grid`` crosses the expanded space keys, so the winner of a
    nine-trial search may pair 16 with 64 — and the driver re-applies
    the winner to the run's own artifacts, which is what the loop then
    serves. Six of the shipped grid's nine points are asymmetric, and
    the RUNNER-UP of the run this branch shipped is one of them (run
    ``intraday_poc-train-2026-08-28-698e75f3``: 64/64 wins at 0.182448,
    32/64 is second at 0.068577), so an asymmetric winner is one
    re-tuned band away, not a thought experiment. Nothing else notices:
    the sidecars verify individually, and the config-level twin pin
    reads the DECLARED params, which the search never touches.
    CLAUDE.md's rule for a value that must appear twice is a test or a
    runtime refusal; the value lands at serve time, so the refusal does
    too — over the WHOLE regime map, because a pin that omits
    a knob claims coverage it lacks.

    The refusal's REMEDY is pinned with it, because the first spelling
    of it named two that do not exist. "Re-run the fit" reproduces the
    same winner (the grid is enumerated, the loaders are seeded, and two
    runs of this document produced bit-identical trial lists), and
    "promote the pairing into both documents" cannot be written down at
    all: an asymmetric pair needs a per-instance ``arch_params``, and
    declaring ``qhat__aapl`` beside the template it fans from is refused
    by the engine as a collision. What IS reachable is named instead —
    the symmetric trials in the run's ``carry.json``, promoted onto the
    TEMPLATE (one edit, both symbols) — so the operator is not sent to a
    dead end at the moment trading stops.
    """
    from intraday_poc import live

    widths = {"qhat__aapl": 16, "qhat__msft": 64}

    def fake_restore(directory, module_ref):
        width = widths[os.path.basename(directory)]
        return (SimpleNamespace(lookback=3), ["ret_lag_0"],
                {"lookback": 3, "hidden_size": width, "num_layers": 1})

    monkeypatch.setattr(live, "restore_model", fake_restore)
    document = _fanned_document()
    with pytest.raises(SystemExit, match="hidden_size") as refusal:
        live._restore_signals(document, "/runs/r1", ["AAPL", "MSFT"], {},
                              "somepkg.models:OtherNet")
    message = str(refusal.value)
    assert "carry.json" in message and "template" in message, (
        "the refusal must name where the symmetric trials are listed and "
        "the one declaration that moves both symbols; without them the "
        "operator's only documented move is a re-run that reproduces the "
        "same winner"
    )
    assert "re-run the fit" not in message, (
        "the grid is deterministic and the loaders are seeded — re-running "
        "as declared reproduces the refused pairing"
    )

    widths["qhat__msft"] = 16
    signals, lookback = live._restore_signals(
        document, "/runs/r1", ["AAPL", "MSFT"], {},
        "somepkg.models:OtherNet")
    assert lookback == 3 and sorted(signals) == ["AAPL", "MSFT"]


def test_omitted_zoo_defaults_compare_equal_to_the_declared_defaults():
    """ADR-0041: a defaulted knob compares against the default, not presence."""
    from dskit.pipeline.libs.torch_ts import ARCHS, zoo_regime

    base = {"arch": "lstm", "seq_len": 30, "channels": 1, "head": "regression"}
    omitted = zoo_regime(base)
    spelled = zoo_regime({
        **base,
        "order": "recent_first",
        "arch_params": {"lstm": dict(ARCHS["lstm"]["defaults"])},
    })
    assert omitted == spelled
    assert omitted["hidden_size"] == ARCHS["lstm"]["defaults"]["hidden_size"]


def test_the_loop_solves_with_the_solver_the_run_declared():
    """The selector's solver is the run's, not a third copy in the loop.

    ``run-backtest.json`` scores its folds with a named solver,
    ``run-train.json``'s search objective scores its trials with the
    same one, and this loop solves that program a minute at a time — so
    a literal here is the third place, with nothing pinning any pair.
    The document the loop already loads declares it.

    What is CONSTRUCTED is the run's own selector node, the way
    ``window_node`` constructs the run's own window node: the params go
    through the class's knob gate at startup, and everything the pack's
    doorway does with them — resolving the solver, refusing an unknown
    or unavailable one BY NAME, applying ``solver_options`` through the
    ``_solver_options`` seam a subclass may override — happens in the
    pack, once, rather than in a copy here that can drift from it.

    The shipped documents declare exactly the pack's default
    (``appsi_highs`` with no options), so asserting only THAT would pass
    against a loop that never read a document at all — the pin would not
    bite the divergence :func:`~intraday_poc.live.selector_node` exists
    to prevent. A FOREIGN declaration is therefore read too: a different
    name and non-empty options, so both halves have to come off the node.
    """
    from dskit.pipeline.libs.pyomo import DEFAULT_SOLVER

    from intraday_poc.live import selector_node
    from intraday_poc.nodes import SelectOne

    name, options = FOREIGN_SELECTOR
    assert name != DEFAULT_SOLVER and options, (
        "the foreign declaration must differ from the pack default in BOTH "
        "halves, or a hardcoded return would satisfy this test"
    )

    document = load_document(os.path.join(CONFIGS, "run-train.json"))
    shipped = selector_node(document)
    assert isinstance(shipped, SelectOne), (
        "the live minute must be solved by the run's own selector NODE — "
        "a second solve written here re-derives what the pack's doorway "
        "already owns"
    )
    assert shipped.params["solver"] == DEFAULT_SOLVER
    assert shipped.params["solver_options"] == {}

    obj = _serving_obj(("qhat_aapl",))
    obj["pipeline"]["select"]["params"].update(solver=name,
                                               solver_options=dict(options))
    foreign = selector_node(PipelineDocument.from_obj(obj))
    assert (foreign.params["solver"], foreign.params["solver_options"]) == \
        (name, options), (
        "the solver and its options are the RUN's declaration; a literal "
        "here would ignore a gap or limit option added to the document"
    )

    with pytest.raises(SystemExit, match="selector"):
        selector_node(_fanned_document())


@pytest.mark.skipif(not HAVE_SOLVER, reason="pyomo/highspy not installed")
def test_a_solver_this_machine_lacks_refuses_before_the_first_order():
    """An unusable solver stops the loop at STARTUP, never mid-session.

    The pack's doorway refuses an unregistered name and an unavailable
    backend BY NAME, before it solves anything
    (``PyomoSolve._resolve_solver``). A loop that called
    ``SolverFactory`` itself dropped both refusals: ``python -m
    intraday_poc.live`` against a document naming ``glpk`` on a machine
    with no ``glpsol`` started fine, printed "models restored", opened
    the paper trading client, and then died with an uncaught pyomo
    ``ApplicationError`` on the first minute that had coverage — leaving
    whatever position the previous flip had opened, unmanaged.

    So the preflight is the pin: one throwaway solve of the served
    universe before any order can exist, and the pack's refusal
    translated into this file's own currency (``SystemExit``), naming
    the solver so an operator knows which declaration to change. ``glpk``
    is the stand-in because :data:`FOREIGN_SELECTOR` already names it and
    this environment installs HiGHS, not GLPK.
    """
    from intraday_poc.live import preflight_selector, selector_node

    name, _options = FOREIGN_SELECTOR
    obj = _serving_obj(("qhat_aapl",))
    obj["pipeline"]["select"]["params"]["solver"] = name
    selector = selector_node(PipelineDocument.from_obj(obj))
    ctx = NodeContext(name="serving", asof="2026-08-28", run_dir="/runs/r1")

    with pytest.raises(SystemExit, match=name) as refusal:
        preflight_selector(selector, ctx, ["AAPL", "MSFT"])
    assert "backend" in str(refusal.value), (
        "the refusal must say what to DO about it — a solver name with no "
        "remedy is the ApplicationError with better spelling"
    )

    # ...and the shipped declaration passes the very same gate, or the
    # preflight would refuse every honest startup.
    shipped = selector_node(load_document(os.path.join(CONFIGS,
                                                       "run-train.json")))
    preflight_selector(shipped, ctx, ["AAPL", "MSFT"])


@pytest.mark.skipif(not HAVE_SOLVER, reason="pyomo/highspy not installed")
def test_the_declared_solver_options_reach_the_solver(monkeypatch):
    """Reading the options is half the job; APPLYING them is the other.

    ``selector_node`` can carry a full option map and the live minute
    still drop it on the floor — the loop would then solve every minute
    under the solver's own defaults while the document, the backtest and
    the search all ran with a gap or a time limit. Nothing else in this
    suite ever hands the loop a NON-EMPTY option map, so the application
    step would be dead code that looks covered.

    The options must arrive by the pack's OWN route
    (``PyomoSolve._solver_options``), not by a second application here:
    that method is the documented override point for a program pinning
    determinism or tolerances (``BudgetedSelect`` is the worked example),
    so a copy in the loop would send the document's options to the live
    minute and a subclass's pinned ones nowhere. Overriding it on a
    throwaway subclass is what proves the route: the injected option can
    only appear if the loop solved through the doorway.

    Solved against a stand-in factory rather than a real solver: what is
    pinned is that the requested NAME and the declared options land on
    the object the loop solves with, which no real solve can show.
    """
    import pyomo.environ as pyo

    from intraday_poc.live import solve_pick

    preds = {"AAPL": 0.002, "MSFT": -0.001}
    name, options = FOREIGN_SELECTOR
    seen = {}

    class _Recorder:
        """A solver that records what it was given, then picks the max."""

        def __init__(self, requested):
            self.requested = requested
            self.options = {}

        def available(self, exception_flag=False):
            return True

        def solve(self, model):
            seen["name"] = self.requested
            seen["options"] = dict(self.options)
            best = max(preds, key=preds.get)
            for index in model.x:
                model.x[index].value = 1.0 if index[1] == best else 0.0

    class _Pinned(SelectOne):
        """A selector whose program pins one option of its own."""

        def _solver_options(self):
            return {"pinned": 1, **super()._solver_options()}

    monkeypatch.setattr(pyo, "SolverFactory", _Recorder)
    ctx = NodeContext(name="serving", asof="2026-08-28", run_dir="/runs/r1")
    selector = _Pinned("select", {"solver": name,
                                  "solver_options": dict(options),
                                  "split": "val"})
    assert solve_pick(selector, ctx, preds) == "AAPL"
    assert seen == {"name": name,
                    "options": {"pinned": 1, **options}}, (
        "the run's solver name and its solver_options must reach the "
        "solver the live minute is decided by, through the seam a "
        "subclass overrides to pin its own"
    )


def test_live_takes_the_credential_env_names_from_the_source_config(
        tmp_path, monkeypatch):
    """The credential knobs are knobs: ``key_env``/``secret_env``.

    ``spec()`` advertises them, the PULLER honours them, and the loop
    resolves the very same config — so a loop that reads
    ``APCA_API_KEY_ID`` out of the environment regardless is restating a
    vendor knob it already has. The failure is not theoretical: a config
    naming another account's pair acquires fine and then serves under
    whatever the default names happen to hold.
    """
    pytest.importorskip("alpaca.trading.client")
    import alpaca.trading.client as trading_client

    from intraday_poc import live

    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "config.json").write_text(
        json.dumps(_serving_obj(("qhat_aapl",))), encoding="utf-8")
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"symbols": ["AAPL"], "start": "2026-01-01",
                                  "key_env": "ALPACA_KEY_A",
                                  "secret_env": "ALPACA_SECRET_A"}),
                      encoding="utf-8")

    seen = {}
    monkeypatch.setattr(live, "fetch_bars",
                        lambda *a, **kw: seen.update(fetch=(a, kw)) or {})
    monkeypatch.setattr(live, "restore_model",
                        lambda directory, ref: (SimpleNamespace(lookback=3),
                                                ["ret_lag_0"],
                                                {"lookback": 3}))
    monkeypatch.setattr(trading_client, "TradingClient",
                        lambda *a, **kw: seen.update(trading=(a, kw))
                        or SimpleNamespace(
                            get_clock=lambda: SimpleNamespace(
                                is_open=True, next_open="")))
    for name in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALPACA_KEY_A", "pk-a")
    monkeypatch.setenv("ALPACA_SECRET_A", "sk-a")

    assert live.main(["--run-dir", str(run_dir),
                      "--source-config", str(source),
                      "--once", "--dry-run",
                      "--log-dir", str(tmp_path)]) == 0
    assert seen["trading"][0][:2] == ("pk-a", "sk-a"), seen["trading"]
    assert seen["fetch"][0][-2:] == ("pk-a", "sk-a"), seen["fetch"]


def test_live_reads_dotenv_through_the_toolkits_own_parser(tmp_path,
                                                           monkeypatch):
    """The dotenv rules are dskit's, imported, not re-derived.

    ``dskit.pipeline.env`` documents the format the child's ``.env``
    follows — an optional ``export `` prefix, matched quotes stripped,
    process environment winning. A second parser here diverges silently:
    a key written the documented way loads WITH its quotes, and the
    vendor rejects credentials from a file that looks correct.
    """
    from intraday_poc import live

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "# the toolkit's documented format\n"
        'export ALPACA_KEY_A="pk-quoted"\n'
        "ALPACA_SECRET_A='sk-quoted'\n",
        encoding="utf-8")
    for name in ("ALPACA_KEY_A", "ALPACA_SECRET_A"):
        monkeypatch.delenv(name, raising=False)
    knobs = {"key_env": "ALPACA_KEY_A", "secret_env": "ALPACA_SECRET_A"}
    assert live.credentials(knobs) == ("pk-quoted", "sk-quoted")

    # The process environment still wins over the file.
    monkeypatch.setenv("ALPACA_KEY_A", "pk-process")
    assert live.credentials(knobs)[0] == "pk-process"

    # And a name the file never declares is a loud refusal that names it.
    missing = {"key_env": "ALPACA_KEY_B", "secret_env": "ALPACA_SECRET_A"}
    with pytest.raises(SystemExit, match="ALPACA_KEY_B"):
        live.credentials(missing)


def test_live_refuses_empty_credentials_by_name(tmp_path, monkeypatch):
    """PRESENT is not AUTHENTICATED — and empty is the shipped case.

    ``.env.example`` ships both keys with no value, so copying it
    verbatim (what its own header tells an operator to do) yields a
    ``.env`` in which every required NAME exists holding ``""``.
    ``load_env`` checks presence, so the loop resolved ``('', '')`` and
    went on to open a broker client and place orders against a vendor
    that can only answer 401 — while the PULLER's gate refuses that
    exact pair by name. The child owns ONE credential rule, so the loop
    runs the connector's own instead of a second copy that can drift
    from it. (Ruling 1.)
    """
    from intraday_poc import live

    monkeypatch.chdir(tmp_path)
    shutil.copyfile(os.path.join(CHILD_ROOT, ".env.example"),
                    str(tmp_path / ".env"))
    knobs = AlpacaBarsConnector().resolve_knobs({"symbols": ["AAPL"],
                                                 "start": "2026-01-01"})
    for name in (knobs["key_env"], knobs["secret_env"]):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit) as refusal:
        live.credentials(knobs)
    assert knobs["key_env"] in str(refusal.value), refusal.value
    assert knobs["secret_env"] in str(refusal.value), refusal.value

    # ...and it IS the connector's refusal, not a restatement of it: one
    # rule, one message, whichever side of the child hits it.
    with pytest.raises(AssetError) as puller:
        AlpacaBarsConnector()._credentials(knobs)
    assert str(refusal.value) == str(puller.value)


def test_live_vendor_knobs_come_from_the_source_config(tmp_path):
    """The vendor half of the same rule: the live fetch takes its
    adjustment from the config the PULLER uses, through the connector's
    own knob gate — so the loop cannot fetch a differently-adjusted
    price series than the store was built from, and cannot restate the
    connector's default either."""
    from intraday_poc.live import source_knobs

    shipped = source_knobs(os.path.join(CONFIGS, "source-backfill.json"))
    assert shipped["adjustment"] == "all"

    bare = tmp_path / "source-bare.json"
    bare.write_text(json.dumps({"symbols": ["AAPL"], "start": "2026-01-01"}),
                    encoding="utf-8")
    assert source_knobs(str(bare))["adjustment"] == \
        AlpacaBarsConnector().resolve_knobs(
            {"symbols": ["AAPL"], "start": "2026-01-01"})["adjustment"]

    with pytest.raises(SystemExit, match="source config"):
        source_knobs(str(tmp_path / "nope.json"))


def test_live_resolves_knobs_through_the_connectors_public_gate(tmp_path,
                                                                monkeypatch):
    """``source_knobs`` calls the connector's PUBLIC gate.

    ``__all__`` plus the ``_`` prefix is this package's API contract, so
    a serving loop reaching into a method the connector declares private
    is pinned to a name that module is free to rename — and would break
    at serve time with nothing having warned. Rebinding the public gate
    must move the loop.
    """
    from intraday_poc.live import source_knobs

    monkeypatch.setattr(AlpacaBarsConnector, "resolve_knobs",
                        lambda self, config: {"resolved": config})
    bare = tmp_path / "source.json"
    bare.write_text(json.dumps({"symbols": ["AAPL"], "start": "2026-01-01"}),
                    encoding="utf-8")
    assert source_knobs(str(bare)) == \
        {"resolved": {"symbols": ["AAPL"], "start": "2026-01-01"}}


def test_live_serves_the_universe_the_source_config_declares(tmp_path,
                                                             monkeypatch):
    """The symbol universe is READ, never restated on the CLI.

    ``symbols`` is a vendor knob the source config already declares —
    the same list the store was acquired for — and the loop resolves
    that config anyway. A default list in the argparse flag is the same
    train/serve skew as a restated price field: add a ticker to the
    config, retrain, and every invocation without the flag keeps
    trading the old universe with nothing raising.
    """
    pytest.importorskip("alpaca.trading.client")
    import alpaca.trading.client as trading_client

    from intraday_poc import live

    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "config.json").write_text(
        json.dumps(_serving_obj(("qhat_aapl", "qhat_msft", "qhat_goog"))),
        encoding="utf-8")
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"symbols": ["AAPL", "MSFT", "GOOG"],
                                  "start": "2026-01-01"}), encoding="utf-8")

    seen = {}
    monkeypatch.setattr(live, "fetch_bars",
                        lambda symbols, *a, **kw: seen.update(
                            symbols=list(symbols)) or {})
    monkeypatch.setattr(live, "restore_model",
                        lambda directory, ref: (SimpleNamespace(lookback=3),
                                                ["ret_lag_0"],
                                                {"lookback": 3}))
    monkeypatch.setattr(trading_client, "TradingClient",
                        lambda *a, **kw: SimpleNamespace(
                            get_clock=lambda: SimpleNamespace(
                                is_open=True, next_open="")))
    monkeypatch.setenv("APCA_API_KEY_ID", "stub-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "stub-secret")

    assert live.main(["--run-dir", str(run_dir),
                      "--source-config", str(source),
                      "--once", "--dry-run",
                      "--log-dir", str(tmp_path)]) == 0
    assert seen["symbols"] == ["AAPL", "MSFT", "GOOG"], seen


def test_live_main_fetches_with_the_knobs_it_read(tmp_path, monkeypatch):
    """The wiring itself: main() must hand fetch_bars the document's
    price field and the source config's adjustment. Everything the loop
    cannot reach in a test — the broker client, the artifacts, the
    vendor — is doubled; the knob resolution under test is real."""
    pytest.importorskip("alpaca.trading.client")
    import alpaca.trading.client as trading_client

    from intraday_poc import live

    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "config.json").write_text(
        json.dumps(_serving_obj(("qhat_aapl", "qhat_msft"),
                                price_field="vwap")),
        encoding="utf-8")

    seen = {}

    def fake_fetch(symbols, minutes, price_field, adjustment, key, secret):
        seen.update(symbols=list(symbols), minutes=minutes,
                    price_field=price_field, adjustment=adjustment)
        return {}  # no coverage: the iteration decides nothing, loudly

    monkeypatch.setattr(live, "fetch_bars", fake_fetch)
    monkeypatch.setattr(live, "restore_model",
                        lambda directory, ref: (SimpleNamespace(lookback=3),
                                                ["ret_lag_0"],
                                                {"lookback": 3}))
    monkeypatch.setattr(trading_client, "TradingClient",
                        lambda *a, **kw: SimpleNamespace(
                            get_clock=lambda: SimpleNamespace(
                                is_open=True, next_open="")))
    monkeypatch.setenv("APCA_API_KEY_ID", "stub-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "stub-secret")

    rc = live.main(["--run-dir", str(run_dir),
                    "--source-config", os.path.join(CONFIGS,
                                                    "source-backfill.json"),
                    "--once", "--dry-run",
                    "--log-dir", str(tmp_path)])
    assert rc == 0
    assert seen["price_field"] == "vwap", seen
    assert seen["adjustment"] == "all", seen
    assert seen["symbols"] == ["AAPL", "MSFT"], seen  # the config's universe


# -- end-to-end: store -> train -> artifact -> live restore/select ---------


@pytest.mark.skipif(not (HAVE_TORCH and HAVE_SOLVER),
                    reason="torch/pyomo/highspy not installed")
def test_train_document_to_live_chain_end_to_end(tmp_path):
    """The shipped train document (bars root repointed at a tmp store,
    epochs cut, split cuts moved onto the fixture's timeline, the
    tracking sink dropped — placement and effort, not shape) runs end to
    end, SEARCH INCLUDED; the live loop then restores the artifacts
    through its own sidecar verification, predicts, and the pyomo
    program picks a symbol.

    The cuts move because they are absolute instants: left at the
    store's real August window every fixture row would land in train,
    the selection window would be empty, and ``concat`` refuses a port
    that contributed nothing — so the document would fail on the fixture
    for a reason that says nothing about the document.
    """
    from intraday_poc.live import (
        artifact_dirs,
        declared_module,
        load_run_document,
        predict,
        restore_model,
        selector_node,
        solve_pick,
        window_node,
    )

    root = str(tmp_path / "ob")
    _write_store(root, n_minutes=120)

    with open(os.path.join(CONFIGS, "run-train.json"),
              encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["pipeline"]["bars"]["params"]["root"] = root
    doc["foreach"]["pipeline"]["qhat"]["params"]["epochs"] = 2
    doc["splits"].update({"train_end_ms": _ms(90), "val_start_ms": _ms(95),
                          "val_end_ms": _ms(119), "test_end_ms": _ms(200)})
    doc.pop("tracking")
    doc_path = tmp_path / "run-train.json"
    doc_path.write_text(json.dumps(doc), encoding="utf-8")

    document = load_document(str(doc_path))
    document = replace(document,
                       outputs=OutputsConfig(run_root=str(tmp_path / "runs")))
    result = run_document(document, asof="2026-01-06")
    assert result.state == "ran", (result.state, result.error)

    bars = [{"symbol": symbol, "asof_ms": _ms(i), "close": _close(symbol, i)}
            for symbol in ("AAPL", "MSFT") for i in range(120)]

    # The loop reads the trainer identity the DOCUMENT declared off the
    # run dir the driver just wrote — zoo class, end to end.
    from dskit.pipeline.libs.torch_ts import TimeSeriesTrain

    written = load_run_document(result.run_dir)
    module_ref = declared_module(written)
    assert module_ref == TimeSeriesTrain._class_ref()

    # The serving features come off the DOCUMENT'S OWN window node —
    # the same object the run trained through, not a second reading of
    # what it did.
    served = window_node(written).latest_rows(bars)

    # No --artifact anywhere: the fanned trainers' directories are found
    # through the document the run wrote, which is the whole point.
    dirs = artifact_dirs(written, result.run_dir, ["AAPL", "MSFT"], {})
    assert dirs["AAPL"] == os.path.join(result.run_dir, "artifacts",
                                        "qhat__aapl")

    preds = {}
    for symbol in ("AAPL", "MSFT"):
        artifact_dir = dirs[symbol]
        with pytest.raises(SystemExit, match="wrong artifact"):
            restore_model(artifact_dir, "somepkg.models:OtherNet")
        module, features, regime = restore_model(artifact_dir, module_ref)
        assert module.seq_len == 30
        assert regime["seq_len"] == 30
        assert regime["hidden_size"] in (16, 32, 64)
        assert features[0] == "ret_lag_0" and len(features) == 30
        row = served[symbol]
        assert "y_next" not in row
        pred = predict(module, features, row)
        assert pred is not None and math.isfinite(pred)
        preds[symbol] = pred

    # And the minute is solved by the run's OWN selector node, built
    # from the same document — not by a program and a solver name
    # spelled again here.
    selector = selector_node(written)
    ctx = NodeContext(name=written.name, asof="2026-01-06",
                      run_dir=result.run_dir)
    winner = solve_pick(selector, ctx, preds)
    assert winner == max(preds, key=preds.get)


@pytest.mark.skipif(not HAVE_TORCH, reason="torch not installed")
def test_restore_model_resolves_the_class_the_run_declared(tmp_path):
    """The declared path is RESOLVED, not string-compared to a literal.

    A run whose declared class is not a torch module refuses BY THAT
    PATH, through the same resolver the torch pack builds with — a loop
    that constructed a net regardless of the named class would sail
    past this and fail much later, on weights that do not fit.
    NextBarLSTM was deleted; this pin stays on the declared-class
    branch (params.module set, no arch).
    """
    from intraday_poc.live import restore_model

    artifact = tmp_path / "artifacts" / "qhat_aapl"
    artifact.mkdir(parents=True)
    state = artifact / "model.pt"
    state.write_bytes(b"never loaded: the resolver refuses first")
    sidecar = {"params": {"module": "intraday_poc.nodes:WindowRows",
                          "module_params": {}, "features": ["ret_lag_0"]}}
    digest = hashlib.sha256(state.read_bytes())
    digest.update(b"\x00")
    digest.update(json.dumps(sidecar, sort_keys=True,
                             separators=(",", ":")).encode("utf-8"))
    sidecar["state_hash"] = digest.hexdigest()
    (artifact / "model.json").write_text(json.dumps(sidecar),
                                         encoding="utf-8")

    with pytest.raises(SystemExit, match="forward"):
        restore_model(str(artifact), "intraday_poc.nodes:WindowRows")


@pytest.mark.skipif(not HAVE_TORCH, reason="torch not installed")
def test_restore_model_refuses_a_zoo_sidecar_naming_the_wrong_class(tmp_path):
    """A zoo sidecar whose module_class is not TimeSeriesTrain refuses."""
    from dskit.pipeline.libs.torch_ts import TimeSeriesTrain
    from intraday_poc.live import restore_model

    artifact = tmp_path / "artifacts" / "qhat_aapl"
    artifact.mkdir(parents=True)
    state = artifact / "model.pt"
    state.write_bytes(b"never loaded: the class check refuses first")
    sidecar = {
        "module_class": "dskit.pipeline.libs.torch:DeclaredTrain",
        "params": {
            "arch": "lstm", "head": "regression", "seq_len": 4,
            "channels": 1, "features": ["ret_lag_0", "ret_lag_1",
                                        "ret_lag_2", "ret_lag_3"],
        },
    }
    digest = hashlib.sha256(state.read_bytes())
    digest.update(b"\x00")
    digest.update(json.dumps(sidecar, sort_keys=True,
                             separators=(",", ":")).encode("utf-8"))
    sidecar["state_hash"] = digest.hexdigest()
    (artifact / "model.json").write_text(json.dumps(sidecar),
                                         encoding="utf-8")

    with pytest.raises(SystemExit, match="wrong artifact"):
        restore_model(str(artifact), TimeSeriesTrain._class_ref())


@pytest.mark.skipif(not HAVE_TORCH, reason="torch not installed")
def test_restore_model_rebuilds_a_zoo_net(tmp_path):
    """Zoo nets live inside build_module — restore rebuilds, never imports."""
    import torch
    from dskit.pipeline.libs.torch_ts import TimeSeriesTrain
    from intraday_poc.live import restore_model

    params = {
        "arch": "lstm",
        "head": "regression",
        "seq_len": 4,
        "channels": 1,
        "order": "recent_first",
        "arch_params": {"lstm": {"hidden_size": 8, "num_layers": 1}},
        "features": ["ret_lag_0", "ret_lag_1", "ret_lag_2", "ret_lag_3"],
        "label": "y",
        "epochs": 1,
    }
    module = TimeSeriesTrain("restore", params).build_module(params)
    artifact = tmp_path / "art"
    artifact.mkdir()
    state = artifact / "model.pt"
    torch.save(module.state_dict(), state)
    from dskit.pipeline.libs.torch import ARTIFACT_FORMAT
    sidecar = {
        "format": ARTIFACT_FORMAT,
        "module_class": TimeSeriesTrain._class_ref(),
        "params": params,
        "seed": 0,
    }
    digest = hashlib.sha256(state.read_bytes())
    digest.update(b"\x00")
    digest.update(json.dumps(sidecar, sort_keys=True,
                             separators=(",", ":")).encode("utf-8"))
    sidecar["state_hash"] = digest.hexdigest()
    (artifact / "model.json").write_text(json.dumps(sidecar),
                                         encoding="utf-8")

    restored, features, regime = restore_model(
        str(artifact), TimeSeriesTrain._class_ref())
    assert restored.seq_len == 4
    assert features == params["features"]
    assert regime["hidden_size"] == 8
    assert regime["arch"] == "lstm"
    with torch.no_grad():
        out = restored(torch.zeros(1, 4))
    assert tuple(out.shape)[-1] == 1

    with pytest.raises(SystemExit, match="wrong artifact"):
        restore_model(str(artifact), "somepkg.models:OtherNet")


@pytest.mark.skipif(not HAVE_TORCH, reason="torch not installed")
def test_restore_model_refuses_a_tampered_artifact(tmp_path):
    """A byte flipped in model.pt after training fails the sidecar's
    state_hash — the live loop refuses to trade on it."""
    from intraday_poc.live import restore_model

    root = str(tmp_path / "ob")
    _write_store(root, n_minutes=60, symbols=("AAPL",))
    with open(os.path.join(CONFIGS, "run-train.json"),
              encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["pipeline"]["bars"]["params"]["root"] = root
    # The fit alone: the shared scan plus the ONE symbol's template
    # nodes that feed it — its rows and the band its checkpoint monitor
    # reads. The selection half (val_rows/fc/forecasts/labeled/select)
    # and the search that consumes it are what this test is not about,
    # and the tracking sink is placement.
    doc["pipeline"] = {k: v for k, v in doc["pipeline"].items()
                       if k in ("bars", "window")}
    doc["foreach"]["keys"] = ["AAPL"]
    doc["foreach"]["pipeline"] = {
        k: v for k, v in doc["foreach"]["pipeline"].items()
        if k in ("rows", "mon_rows", "qhat")
    }
    doc["foreach"]["pipeline"]["qhat"]["params"]["epochs"] = 1
    # Cuts inside the 60-minute fixture, with the monitor band OPEN: the
    # trainer monitors on it, and an empty one is refused by the engine
    # (rightly — a monitor with nothing to score selects nothing).
    doc["splits"].update({"train_end_ms": _ms(45), "val_start_ms": _ms(50),
                          "val_end_ms": _ms(58), "test_end_ms": _ms(200)})
    doc.pop("tracking")
    doc_path = tmp_path / "doc.json"
    doc_path.write_text(json.dumps(doc), encoding="utf-8")
    document = replace(load_document(str(doc_path)),
                       outputs=OutputsConfig(run_root=str(tmp_path / "runs")))
    result = run_document(document, asof="2026-01-06")
    assert result.state == "ran", (result.state, result.error)

    artifact_dir = os.path.join(result.run_dir, "artifacts", "qhat__aapl")
    state_path = os.path.join(artifact_dir, "model.pt")
    with open(state_path, "r+b") as fh:
        fh.seek(-1, os.SEEK_END)
        last = fh.read(1)
        fh.seek(-1, os.SEEK_END)
        fh.write(bytes([last[0] ^ 0xFF]))
    from dskit.pipeline.libs.torch_ts import TimeSeriesTrain

    with pytest.raises(SystemExit, match="hash"):
        restore_model(artifact_dir, TimeSeriesTrain._class_ref())
