"""Child kinds under the toolkit conformance bar, plus a store e2e."""

import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.kinds_flow import EventGrid
from dskit.pipeline.node import NodeContext

from intraday_equities.nodes import (
    NODE_KINDS,
    BarsFromStore,
    FeedParity,
    PortfolioSelect,
    WindowRows,
)

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAVE_SOLVER = (
    importlib.util.find_spec("pyomo") is not None
    and importlib.util.find_spec("highspy") is not None
)

EXPECTED_ROLES = {
    "intraday_equities-bars": "data",
    "intraday_equities-window": "transform",
    "intraday_equities-feed-parity": "score",
    "intraday_equities-portfolio": "score",
}

_BASE = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)


def _ts(index):
    return (_BASE + timedelta(minutes=index)).isoformat()


def _ms(index):
    return int((_BASE + timedelta(minutes=index)).timestamp() * 1000)


def _bar(symbol, index, close, source="alpaca-sip", acq="acq-0001"):
    return {
        "stream": "bars",
        "mode": "backfill",
        "kind": "observation",
        "effective_date": _ts(index),
        "acquired_at": "2026-01-06T00:00:00+00:00",
        "data": {
            "symbol": symbol,
            "ts": _ts(index),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 100.0,
            "trade_count": 5,
            "vwap": close,
        },
    }


def _write_store(root, source, n_minutes=16, symbols=("AAPL", "JPM")):
    directory = os.path.join(root, "observations", source, "acq-0001")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "bars.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        for symbol in symbols:
            for index in range(n_minutes):
                close = 100.0 + index + (0.5 if symbol == "JPM" else 0.0)
                fh.write(json.dumps(_bar(symbol, index, close), sort_keys=True) + "\n")
    return path


class _FakeSignal:
    """Predict-able stand-in for a fitted estimator."""

    def predict(self, row):
        value = row.get("ret_lag_0", row.get("close"))
        return None if value is None else float(value)


def _ctx(tmp_path):
    return NodeContext(name="test", asof="2026-01-06", run_dir=str(tmp_path))


def probes(tmp_path):
    root = str(tmp_path / "ob")
    store_path = _write_store(root, "alpaca-sip")
    bars_params = {"root": root, "source": "alpaca-sip"}

    def move():
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
            fh.write(json.dumps(_bar("AAPL", 99, 123.45), sort_keys=True) + "\n")

    window_input = [
        {"symbol": "AAPL", "asof_ms": _ms(index), "close": 100.0 + index}
        for index in range(8)
    ]
    return {
        "intraday_equities-bars": NodeProbe(
            params=dict(bars_params),
            required=("root", "source"),
            make=lambda: BarsFromStore("bars", dict(bars_params)),
            move=move,
            grow=grow,
            size=lambda out: len(out["records"]),
            runnable=True,
        ),
        "intraday_equities-window": NodeProbe(
            params={"lookback": 2},
            required=("lookback",),
            inputs={"records": [dict(row) for row in window_input]},
            stream_ports=("records",),
            runnable=True,
        ),
        "intraday_equities-feed-parity": NodeProbe(
            params={"split": "val"},
            required=("split",),
            inputs={
                "left": [dict(row) for row in window_input],
                "right": [dict(row) for row in window_input],
            },
            stream_ports=("left", "right"),
            runnable=True,
        ),
        "intraday_equities-portfolio": NodeProbe(
            params={"split": "val", "tradable": ["AAPL", "JPM"]},
            required=("split", "tradable"),
            inputs={
                "signal": _FakeSignal(),
                "records": [
                    {
                        "symbol": "AAPL",
                        "asof_ms": _ms(1),
                        "ret_lag_0": 0.002,
                        "y_next": 0.01,
                    },
                    {
                        "symbol": "JPM",
                        "asof_ms": _ms(1),
                        "ret_lag_0": 0.001,
                        "y_next": -0.01,
                    },
                ],
            },
            stream_ports=("records",),
            runnable=HAVE_SOLVER,
        ),
    }


TestConformance = conformance_suite(
    registry=NODE_KINDS,
    module="intraday_equities.nodes",
    probes=probes,
    expected_roles=EXPECTED_ROLES,
    name="TestConformance",
)


def test_store_window_and_grid_end_to_end(tmp_path):
    root = str(tmp_path / "ob")
    _write_store(root, "alpaca-sip", n_minutes=20)
    ctx = _ctx(tmp_path)
    bars = BarsFromStore("bars", {"root": root, "source": "alpaca-sip"}).run(
        ctx, {}
    )["records"]
    assert bars
    assert {row["session"] for row in bars} <= {"rth", "eth", "closed"}
    windows = WindowRows("window", {"lookback": 2, "label_lead": 1}).run(
        ctx, {"records": bars}
    )["records"]
    assert windows
    assert "ret_lag_0" in windows[0]
    assert "y_next" in windows[0]
    kept = EventGrid("grid", {"period_ms": 60_000, "offset_ms": 0}).run(
        ctx, {"records": windows}
    )["records"]
    assert len(kept) <= len(windows)


def test_feed_parity_counts_overlap():
    left = [{"symbol": "AAPL", "asof_ms": 1, "close": 10.0}]
    right = [{"symbol": "AAPL", "asof_ms": 1, "close": 11.0}]
    out = FeedParity("parity", {"split": "val"}).run(
        None, {"left": left, "right": right}
    )
    assert out["metrics"]["n_overlap"] == 1
    assert out["metrics"]["mae_close"] == 1.0


def test_portfolio_emits_decision_metrics(tmp_path):
    """Labeled picks produce IC, hit rate, and a no-cost return path."""
    if not HAVE_SOLVER:
        return
    rows = [
        {
            "symbol": "AAPL",
            "asof_ms": _ms(1),
            "ret_lag_0": 0.02,
            "y_next": 0.03,
        },
        {
            "symbol": "JPM",
            "asof_ms": _ms(1),
            "ret_lag_0": 0.01,
            "y_next": -0.02,
        },
        {
            "symbol": "AAPL",
            "asof_ms": _ms(2),
            "ret_lag_0": -0.01,
            "y_next": -0.04,
        },
        {
            "symbol": "JPM",
            "asof_ms": _ms(2),
            "ret_lag_0": 0.03,
            "y_next": 0.05,
        },
    ]
    out = PortfolioSelect(
        "select", {"split": "val", "tradable": ["AAPL", "JPM"]}
    ).run(
        _ctx(tmp_path),
        {"signal": _FakeSignal(), "records": rows, "labeled": rows},
    )
    metrics = out["metrics"]
    assert metrics["n_picks"] == 2
    assert metrics["n_stamps"] == 2
    assert metrics["n_labeled"] == 4
    assert metrics["n_scored"] == 4
    assert metrics["rank_ic"] > 0
    assert 0.0 <= metrics["pick_hit_rate"] <= 1.0
    assert metrics["turnover"] == 1.0
    assert set(metrics) >= {
        "rank_ic",
        "pick_hit_rate",
        "pick_mean_y",
        "pick_sum_y",
        "pick_max_drawdown",
        "turnover",
    }
