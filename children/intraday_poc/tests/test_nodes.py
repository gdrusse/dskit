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
from dskit.pipeline.document import PipelineDocument, load_document
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
def test_the_serving_loop_imports_the_window_defaults(name):
    """``live.py`` must BIND each window default by importing it.

    The same un-failable-``is`` trap as the bar constants above, and it
    costs more here: ``"close"`` is identifier-shaped so CPython
    interns it and ``5`` is a cached small int, so a restated
    ``DEFAULT_PRICE_FIELD = "close"`` in ``live.py`` satisfies
    ``live.X is nodes.X``. Monkeypatching ``live``'s own global cannot
    see the divergence either — a local copy is patched just as
    happily. Only the BINDING tells the two apart, and the day the node
    is retuned to ``"vwap"`` a copy would keep feeding close returns
    into vwap-trained weights: the exact train/serve skew this loop
    exists not to have.
    """
    from intraday_poc import live

    how, source = _binding_of(live, name)
    assert (how, source) == ("import", ".nodes"), (
        f"live.py binds {name} as {how!r} from {source!r} — it must import "
        "it from .nodes, or the serving loop windows on a default the "
        "training node abandoned"
    )


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
def test_live_window_parity(price_field):
    """live and WindowRows agree bit-for-bit on the lag construction AND
    on WHICH FIELD they price — the train/serve-skew guard.

    The close-only version of this test was blind to the skew that
    matters: set ``price_field`` to anything else and the backtest
    trained on that series while the loop fed close returns into the
    same weights. The vwap case fails against a loop that prices on
    close, however right its arithmetic.
    """
    from intraday_poc.live import bar_series, latest_feature_row

    prices = _CLOSES if price_field == "close" else _VWAPS
    rows = [{"symbol": "AAPL", "asof_ms": _ms(i), "close": close,
             "vwap": vwap}
            for i, (close, vwap) in enumerate(zip(_CLOSES, _VWAPS))]
    node = WindowRows("window", {"lookback": 3, "price_field": price_field,
                                 "max_gap_minutes": 5})
    out = node.run(None, {"records": rows})["records"]

    # The live row ends at the newest bar (t5, no label needed forward).
    live_row = latest_feature_row(bar_series(_vendor_bars(), price_field),
                                  lookback=3, max_gap_minutes=5)
    assert live_row is not None
    for lag in range(3):
        expect = math.log(prices[5 - lag] / prices[4 - lag])
        assert live_row[f"ret_lag_{lag}"] == pytest.approx(expect)
    # The node's newest LABELLED row is the same construction one bar
    # back — same lag orientation, same values.
    newest = out[-1]
    assert newest["asof_ms"] == _ms(4)
    for lag in range(3):
        expect = math.log(prices[4 - lag] / prices[3 - lag])
        assert newest[f"ret_lag_{lag}"] == pytest.approx(expect)


def test_bar_series_refuses_a_field_the_vendor_does_not_carry():
    """A document naming a price field Alpaca does not publish must be
    loud: silently pricing on nothing would hand the model an empty
    series and call it "no coverage"."""
    from intraday_poc.live import bar_series

    with pytest.raises(SystemExit, match="price_field"):
        bar_series(_vendor_bars(), "mid")


def test_live_window_refuses_gaps_and_short_history():
    from intraday_poc.live import latest_feature_row

    bars = [(_ms(i), 100.0 + i) for i in range(4)]
    assert latest_feature_row(bars, lookback=5, max_gap_minutes=5) is None
    gapped = bars[:2] + [(_ms(30), 105.0), (_ms(31), 106.0)]
    assert latest_feature_row(gapped, lookback=3, max_gap_minutes=5) is None


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

    That the loop BINDS those constants by import rather than keeping a
    copy is pinned structurally by
    ``test_the_serving_loop_imports_the_window_defaults`` — an identity
    assertion here could not have shown it, since CPython interns
    ``"close"`` and caches ``5``. This half is the behaviour: whatever
    the loop is bound to is what an undeclared knob resolves to.
    """
    from intraday_poc import live

    run_dir = _run_dir(tmp_path, {"lookback": 3})
    document = live.load_run_document(run_dir)
    assert live.window_knobs(document) == (nodes.DEFAULT_PRICE_FIELD,
                                           float(nodes.DEFAULT_MAX_GAP_MINUTES))
    monkeypatch.setattr(live, "DEFAULT_PRICE_FIELD", "vwap")
    monkeypatch.setattr(live, "DEFAULT_MAX_GAP_MINUTES", 9)
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


def test_artifact_paths_default_by_convention_and_bend_by_flag(tmp_path):
    """``--artifact SYMBOL=PATH`` is the documented override; the
    convention below it is the fallback, so no table of symbols lives in
    the code."""
    from intraday_poc.live import artifact_dirs, parse_artifact_overrides

    assert parse_artifact_overrides(["AAPL=artifacts/other"]) == \
        {"AAPL": "artifacts/other"}
    with pytest.raises(SystemExit, match="SYMBOL=PATH"):
        parse_artifact_overrides(["AAPL"])

    dirs = artifact_dirs("/runs/r1", ["AAPL", "MSFT"],
                         {"MSFT": "artifacts/other"})
    assert dirs["AAPL"] == os.path.join("/runs/r1", "artifacts", "qhat_aapl")
    assert dirs["MSFT"] == os.path.join("/runs/r1", "artifacts", "other")
    absolute = artifact_dirs("/runs/r1", ["AAPL"],
                             {"AAPL": str(tmp_path / "elsewhere")})
    assert absolute["AAPL"] == str(tmp_path / "elsewhere")


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
        artifact_dirs("/runs/r1", ["AAPL", "MSFT"],
                      {"MFST": "artifacts/qhat_msft_v2"})


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
    (run_dir / "config.json").write_text(json.dumps({
        "name": "serving",  # a run document, per the engine's grammar
        "pipeline": {
            "window": {"uses": "intraday_poc-window",
                       "params": {"lookback": 3}},
            "qhat_aapl": {
                "uses": "dskit.pipeline.libs.torch:DeclaredTrain",
                "params": {"module": "intraday_poc.models:NextBarLSTM"}},
        }}), encoding="utf-8")
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
                                                ["ret_lag_0"]))
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
    (run_dir / "config.json").write_text(json.dumps({
        "name": "serving",  # a run document, per the engine's grammar
        "pipeline": {
            "window": {"uses": "intraday_poc-window",
                       "params": {"lookback": 3}},
            "qhat_aapl": {
                "uses": "dskit.pipeline.libs.torch:DeclaredTrain",
                "params": {"module": "intraday_poc.models:NextBarLSTM"}},
        }}), encoding="utf-8")
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"symbols": ["AAPL", "MSFT", "GOOG"],
                                  "start": "2026-01-01"}), encoding="utf-8")

    seen = {}
    monkeypatch.setattr(live, "fetch_bars",
                        lambda symbols, *a, **kw: seen.update(
                            symbols=list(symbols)) or {})
    monkeypatch.setattr(live, "restore_model",
                        lambda directory, ref: (SimpleNamespace(lookback=3),
                                                ["ret_lag_0"]))
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
    (run_dir / "config.json").write_text(json.dumps({
        "name": "serving",  # a run document, per the engine's grammar
        "pipeline": {
            "window": {"uses": "intraday_poc-window",
                       "params": {"lookback": 3, "price_field": "vwap"}},
            "qhat_aapl": {
                "uses": "dskit.pipeline.libs.torch:DeclaredTrain",
                "params": {"module": "intraday_poc.models:NextBarLSTM"}},
        }}), encoding="utf-8")

    seen = {}

    def fake_fetch(symbols, minutes, price_field, adjustment, key, secret):
        seen.update(symbols=list(symbols), minutes=minutes,
                    price_field=price_field, adjustment=adjustment)
        return {}  # no coverage: the iteration decides nothing, loudly

    monkeypatch.setattr(live, "fetch_bars", fake_fetch)
    monkeypatch.setattr(live, "restore_model",
                        lambda directory, ref: (SimpleNamespace(lookback=3),
                                                ["ret_lag_0"]))
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
    epochs cut — placement and effort, not shape) runs end to end; the
    live loop then restores the artifacts through its own sidecar
    verification, predicts, and the pyomo program picks a symbol."""
    from intraday_poc.live import (
        declared_module,
        latest_feature_row,
        load_run_document,
        predict,
        restore_model,
        solve_pick,
    )

    root = str(tmp_path / "ob")
    _write_store(root, n_minutes=120)

    with open(os.path.join(CONFIGS, "run-train.json"),
              encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["pipeline"]["bars"]["params"]["root"] = root
    for key in ("qhat_aapl", "qhat_msft"):
        doc["pipeline"][key]["params"]["epochs"] = 2
    doc_path = tmp_path / "run-train.json"
    doc_path.write_text(json.dumps(doc), encoding="utf-8")

    document = load_document(str(doc_path))
    document = replace(document,
                       outputs=OutputsConfig(run_root=str(tmp_path / "runs")))
    result = run_document(document, asof="2026-01-06")
    assert result.state == "ran", (result.state, result.error)

    bars = {symbol: [(_ms(i), _close(symbol, i)) for i in range(120)]
            for symbol in ("AAPL", "MSFT")}

    # The loop reads the class the DOCUMENT declared off the run dir the
    # driver just wrote — the ADR-0025 seam, end to end.
    written = load_run_document(result.run_dir)
    module_ref = declared_module(written)
    assert module_ref == doc["pipeline"]["qhat_aapl"]["params"]["module"]

    preds = {}
    for symbol, node_key in (("AAPL", "qhat_aapl"), ("MSFT", "qhat_msft")):
        artifact_dir = os.path.join(result.run_dir, "artifacts", node_key)
        with pytest.raises(SystemExit, match="wrong artifact"):
            restore_model(artifact_dir, "somepkg.models:OtherNet")
        module, features = restore_model(artifact_dir, module_ref)
        assert module.lookback == 30
        assert features[0] == "ret_lag_0" and len(features) == 30
        row = latest_feature_row(bars[symbol], lookback=30,
                                 max_gap_minutes=5)
        assert row is not None
        pred = predict(module, features, row)
        assert pred is not None and math.isfinite(pred)
        preds[symbol] = pred

    winner = solve_pick(preds)
    assert winner == max(preds, key=preds.get)


@pytest.mark.skipif(not HAVE_TORCH, reason="torch not installed")
def test_restore_model_resolves_the_class_the_run_declared(tmp_path):
    """The declared path is RESOLVED, not string-compared to a literal.

    A run whose declared class is not a torch module refuses BY THAT
    PATH, through the same resolver the torch pack builds with — a loop
    that constructed NextBarLSTM regardless would sail past this and
    fail much later, on weights that do not fit.
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
    doc["pipeline"] = {k: v for k, v in doc["pipeline"].items()
                       if k in ("bars", "window", "aapl_rows", "qhat_aapl")}
    doc["pipeline"]["qhat_aapl"]["params"]["epochs"] = 1
    doc_path = tmp_path / "doc.json"
    doc_path.write_text(json.dumps(doc), encoding="utf-8")
    document = replace(load_document(str(doc_path)),
                       outputs=OutputsConfig(run_root=str(tmp_path / "runs")))
    result = run_document(document, asof="2026-01-06")
    assert result.state == "ran", (result.state, result.error)

    artifact_dir = os.path.join(result.run_dir, "artifacts", "qhat_aapl")
    state_path = os.path.join(artifact_dir, "model.pt")
    with open(state_path, "r+b") as fh:
        fh.seek(-1, os.SEEK_END)
        last = fh.read(1)
        fh.seek(-1, os.SEEK_END)
        fh.write(bytes([last[0] ^ 0xFF]))
    with pytest.raises(SystemExit, match="state_hash"):
        restore_model(artifact_dir,
                      doc["pipeline"]["qhat_aapl"]["params"]["module"])
