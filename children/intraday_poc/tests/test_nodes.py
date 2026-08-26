"""The child's node kinds under the TOOLKIT'S conformance bar, unit
tests for the domain math, and one end-to-end: store -> train ->
artifact -> the live loop's restore/predict/select chain.

Heavy deps (torch, pyomo/highspy) gate their own tests via skipif — the
suite passes on a bare install of dskit alone; the full chain only
proves itself where the child's real deps are installed.
"""

import importlib.util
import json
import math
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from dskit.pipeline import OutputsConfig, run_document
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.document import load_document
from dskit.pipeline.node import NodeContext

from intraday_poc.nodes import (
    NODE_KINDS,
    BarsFromStore,
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


def test_live_window_parity():
    """live.latest_feature_row and WindowRows agree bit-for-bit on the
    lag construction — the train/serve-skew guard."""
    from intraday_poc.live import latest_feature_row

    closes = [100.0, 100.7, 100.2, 101.1, 100.9, 101.4]
    rows = [{"symbol": "AAPL", "asof_ms": _ms(i), "close": c}
            for i, c in enumerate(closes)]
    node = WindowRows("window", {"lookback": 3, "max_gap_minutes": 5})
    out = node.run(None, {"records": rows})["records"]

    # The live row ends at the newest bar (t5, no label needed forward).
    live_row = latest_feature_row([(r["asof_ms"], r["close"]) for r in rows],
                                  lookback=3)
    assert live_row is not None
    for lag in range(3):
        expect = math.log(closes[5 - lag] / closes[4 - lag])
        assert live_row[f"ret_lag_{lag}"] == pytest.approx(expect)
    # The node's newest LABELLED row is the same construction one bar
    # back — same lag orientation, same values.
    newest = out[-1]
    assert newest["asof_ms"] == _ms(4)
    for lag in range(3):
        expect = math.log(closes[4 - lag] / closes[3 - lag])
        assert newest[f"ret_lag_{lag}"] == pytest.approx(expect)


def test_live_window_refuses_gaps_and_short_history():
    from intraday_poc.live import latest_feature_row

    bars = [(_ms(i), 100.0 + i) for i in range(4)]
    assert latest_feature_row(bars, lookback=5) is None  # too short
    gapped = bars[:2] + [(_ms(30), 105.0), (_ms(31), 106.0)]
    assert latest_feature_row(gapped, lookback=3) is None  # gap refused


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


# -- end-to-end: store -> train -> artifact -> live restore/select ---------


@pytest.mark.skipif(not (HAVE_TORCH and HAVE_SOLVER),
                    reason="torch/pyomo/highspy not installed")
def test_train_document_to_live_chain_end_to_end(tmp_path):
    """The shipped train document (bars root repointed at a tmp store,
    epochs cut — placement and effort, not shape) runs end to end; the
    live loop then restores the artifacts through its own sidecar
    verification, predicts, and the pyomo program picks a symbol."""
    from intraday_poc.live import (
        latest_feature_row,
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

    preds = {}
    for symbol, node_key in (("AAPL", "qhat_aapl"), ("MSFT", "qhat_msft")):
        artifact_dir = os.path.join(result.run_dir, "artifacts", node_key)
        module, features = restore_model(artifact_dir)
        assert module.lookback == 30
        assert features[0] == "ret_lag_0" and len(features) == 30
        row = latest_feature_row(bars[symbol], lookback=30)
        assert row is not None
        pred = predict(module, features, row)
        assert pred is not None and math.isfinite(pred)
        preds[symbol] = pred

    winner = solve_pick(preds)
    assert winner == max(preds, key=preds.get)


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
        restore_model(artifact_dir)
