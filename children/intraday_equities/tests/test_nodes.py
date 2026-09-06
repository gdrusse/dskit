"""Child kinds under the toolkit conformance bar, plus a store e2e."""

import importlib.util
import json
import math
import os
from datetime import datetime, timedelta, timezone

import pytest
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.kinds_flow import EventGrid
from dskit.pipeline.node import NodeContext

from intraday_equities.nodes import (
    NODE_KINDS,
    BarsFromStore,
    FeedParity,
    HorizonScan,
    KeepSymbols,
    LeadLabeledRows,
    LookbackScan,
    NoInformationScan,
    PortfolioSelect,
    SessionFeatureRows,
    Universe,
    WindowRows,
    _emit_feature_names,
    _horizon_verdict,
    horizon_leads,
    session_feature_names,
)

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAVE_SOLVER = (
    importlib.util.find_spec("pyomo") is not None
    and importlib.util.find_spec("highspy") is not None
)

EXPECTED_ROLES = {
    "intraday_equities-bars": "data",
    "intraday_equities-window": "transform",
    "intraday_equities-session-features": "transform",
    "intraday_equities-universe": "data",
    "intraday_equities-keep-symbols": "transform",
    "intraday_equities-feed-parity": "score",
    "intraday_equities-fold-stats": "transform",
    "intraday_equities-horizon-scan": "score",
    "intraday_equities-no-information-scan": "score",
    "intraday_equities-lookback-scan": "score",
    "intraday_equities-lead-labels": "transform",
    "intraday_equities-portfolio": "score",
}

UNIVERSE_PATH = os.path.join(CHILD_ROOT, "configs", "universe.json")


def _mini_spec(**overrides):
    spec = {
        "symbols": ["AAPL", "SPY"],
        "tradable": ["AAPL"],
        "reference": ["SPY"],
        "holidays": ["2026-01-01"],
        "lookback": 2,
        "max_gap_minutes": 5,
        "period_ms": 60_000,
        "offset_ms": 0,
        "price_field": "close",
        "session": {
            "tz": "America/New_York",
            "rth_start_minutes": 9 * 60 + 30,
            "rth_end_minutes": 16 * 60,
        },
        "scales": [
            {"width": 2, "tag": "2m", "cross_session": False},
            {"width": 4, "tag": "1s", "cross_session": True},
        ],
        "horizon": {
            "lead_start": 1,
            "lead_step": 1,
            "lead_stop": 2,
            "anchors": [2],
            "top_k": 1,
            "se_mult": 2.0,
            "band_leads": 2,
        },
    }
    spec.update(overrides)
    return spec


def _write_universe(path, spec=None):
    payload = spec if spec is not None else _mini_spec()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


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
    universe_path = _write_universe(str(tmp_path / "universe.json"))
    bars_params = {
        "root": root,
        "source": "alpaca-sip",
        "universe": universe_path,
    }
    mini = _mini_spec()
    feature_rows = [
        {
            "symbol": "AAPL",
            "asof_ms": _ms(index),
            "close": 100.0 + index,
            "ret_lag_0": 0.01 * index,
        }
        for index in range(8)
    ]
    bars = [
        {"symbol": "AAPL", "asof_ms": _ms(index), "close": 100.0 + index}
        for index in range(8)
    ]

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
            required=("root", "source", "universe"),
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
        "intraday_equities-fold-stats": NodeProbe(
            params={"train_end_ms": _ms(3)},
            required=("train_end_ms",),
            inputs={
                "records": [
                    {
                        "symbol": symbol,
                        "asof_ms": [_ms(index) for index in range(4)],
                        "names": [],
                        "X": [],
                    }
                    for symbol in ("AAPL", "JPM")
                ],
            },
            stream_ports=("records",),
            runnable=True,
        ),
        "intraday_equities-session-features": NodeProbe(
            params={},
            required=(),
            inputs={
                "records": [
                    {
                        "symbol": "AAPL",
                        "asof_ms": _ms(index),
                        "open": 100.0 + index,
                        "high": 101.0 + index,
                        "low": 99.0 + index,
                        "close": 100.0 + index,
                    }
                    for index in range(8)
                ],
                "spec": dict(mini),
            },
            stream_ports=("records",),
            runnable=True,
        ),
        "intraday_equities-universe": NodeProbe(
            params={"path": universe_path},
            required=("path",),
            make=lambda: Universe("universe", {"path": universe_path}),
            move=lambda: _write_universe(universe_path, _mini_spec(notes="moved")),
            grow=lambda: _write_universe(
                universe_path,
                _mini_spec(
                    symbols=["AAPL", "JPM", "SPY"],
                    tradable=["AAPL", "JPM"],
                    reference=["SPY"],
                ),
            ),
            size=lambda out: len(out["symbols"]),
            runnable=True,
        ),
        "intraday_equities-keep-symbols": NodeProbe(
            params={"field": "symbol"},
            required=("field",),
            inputs={
                "records": [dict(row) for row in window_input],
                "symbols": ["AAPL"],
            },
            stream_ports=("records",),
            runnable=True,
        ),
        "intraday_equities-horizon-scan": NodeProbe(
            params={
                "split": "val",
                "train_end_ms": _ms(3),
                "val_start_ms": _ms(4),
                "val_end_ms": _ms(7),
            },
            required=("split", "train_end_ms", "val_start_ms", "val_end_ms"),
            inputs={
                "records": feature_rows,
                "bars": bars,
                "spec": {**mini, "features": ["ret_lag_0"]},
            },
            stream_ports=("records", "bars"),
            runnable=True,
        ),
        "intraday_equities-no-information-scan": NodeProbe(
            params={
                "split": "val",
                "train_end_ms": _ms(3),
                "val_start_ms": _ms(4),
                "val_end_ms": _ms(7),
            },
            required=("split", "train_end_ms", "val_start_ms", "val_end_ms"),
            inputs={
                "records": feature_rows,
                "bars": bars,
                "spec": {**mini, "features": ["ret_lag_0"]},
            },
            stream_ports=("records", "bars"),
            runnable=True,
        ),
        "intraday_equities-lookback-scan": NodeProbe(
            params={
                "split": "val",
                "lead": 2,
                "train_end_ms": _ms(3),
                "val_start_ms": _ms(4),
                "val_end_ms": _ms(7),
            },
            required=("split", "lead", "train_end_ms", "val_start_ms", "val_end_ms"),
            inputs={
                "records": feature_rows,
                "bars": bars,
                "spec": {**mini, "features": ["ret_lag_0"]},
            },
            stream_ports=("records", "bars"),
            runnable=True,
        ),
        "intraday_equities-lead-labels": NodeProbe(
            params={
                "lead": 2,
                "split": "val",
                "train_end_ms": _ms(3),
                "val_start_ms": _ms(4),
                "val_end_ms": _ms(7),
            },
            required=("lead", "split", "train_end_ms", "val_start_ms", "val_end_ms"),
            inputs={
                "records": feature_rows,
                "bars": bars,
                "spec": {**mini, "features": ["ret_lag_0"]},
            },
            stream_ports=("records", "bars"),
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
            required=("split",),
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


def test_window_rows_keeps_n_ahead():
    """ADR-0049: the child does not narrow the path-output knob away."""
    assert "n_ahead" in WindowRows._PARAMS
    assert (
        WindowRows.validate_params({"lookback": 2, "label_lead": 1, "n_ahead": 4}) == []
    )


def test_store_window_and_grid_end_to_end(tmp_path):
    root = str(tmp_path / "ob")
    _write_store(root, "alpaca-sip", n_minutes=20)
    ctx = _ctx(tmp_path)
    bars = BarsFromStore(
        "bars",
        {"root": root, "source": "alpaca-sip", "universe": UNIVERSE_PATH},
    ).run(ctx, {})["records"]
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
    out = PortfolioSelect("select", {"split": "val", "tradable": ["AAPL", "JPM"]}).run(
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


def _ny_ms(year, month, day, hour, minute):
    from zoneinfo import ZoneInfo

    stamp = datetime(
        year, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York")
    )
    return int(stamp.timestamp() * 1000)


def _ohlc(symbol, asof_ms, close, open_=None):
    price = close if open_ is None else open_
    return {
        "symbol": symbol,
        "asof_ms": asof_ms,
        "open": price,
        "high": close,
        "low": close,
        "close": close,
    }


def test_overnight_is_not_a_one_minute_lag():
    friday = [
        _ohlc("AAPL", _ny_ms(2026, 1, 2, 15, 58), 100.0),
        _ohlc("AAPL", _ny_ms(2026, 1, 2, 15, 59), 101.0),
        _ohlc("AAPL", _ny_ms(2026, 1, 5, 9, 30), 110.0, open_=110.0),
        _ohlc("AAPL", _ny_ms(2026, 1, 5, 9, 31), 111.0),
    ]
    spy = [
        dict(row, symbol="SPY", close=row["close"] + 1, open=row["open"] + 1)
        for row in friday
    ]
    spec = _mini_spec(period_ms=60_000, holidays=[])
    out = SessionFeatureRows("features", {}).run(
        None, {"records": friday + spy, "spec": spec}
    )["records"]
    monday = next(
        row
        for row in out
        if row["symbol"] == "AAPL" and row["asof_ms"] == _ny_ms(2026, 1, 5, 9, 30)
    )
    assert monday["ret_lag_0"] is None
    assert monday["overnight_gap"] == math.log(110.0 / 101.0)
    assert monday["session_gap_days"] == 3.0
    assert monday["after_holiday"] == 0.0


def test_horizon_verdict_prefers_the_farthest_confident_lead():
    curve = [
        {"lead": 5, "ic_val": 0.20, "n_val": 400.0},
        {"lead": 390, "ic_val": 0.18, "n_val": 400.0},
        {"lead": 780, "ic_val": 0.02, "n_val": 400.0},
        {"lead": 1170, "ic_val": 0.01, "n_val": 400.0},
    ]
    verdict = _horizon_verdict(
        curve, anchors=(390, 780, 1170), se_mult=2.0, band_leads=6
    )
    assert verdict["go"] is True
    assert verdict["go_anchor"] is True
    assert verdict["farthest"]["lead"] == 390


def test_horizon_verdict_is_no_go_when_the_curve_is_noise():
    curve = [
        {"lead": lead, "ic_val": 0.01, "n_val": 400.0} for lead in (5, 390, 780, 1170)
    ]
    verdict = _horizon_verdict(
        curve, anchors=(390, 780, 1170), se_mult=2.0, band_leads=6
    )
    assert verdict["go"] is False
    assert verdict["farthest"] is None


def test_keep_symbols_uses_the_wired_list():
    rows = [
        {"symbol": "AAPL", "asof_ms": 1},
        {"symbol": "SPY", "asof_ms": 1},
    ]
    kept = KeepSymbols("tradable", {"field": "symbol"}).run(
        None, {"records": rows, "symbols": ["AAPL"]}
    )["records"]
    assert [row["symbol"] for row in kept] == ["AAPL"]


def test_session_feature_names_follow_the_spec():
    names = session_feature_names(
        2,
        [{"tag": "5m"}, {"tag": "1s"}],
        ["SPY"],
        ("tech",),
    )
    assert names[:2] == ("ret_lag_0", "ret_lag_1")
    assert "ret_5m" in names
    assert "vol_5m" in names
    assert "amihud_5m" in names
    assert "clv" in names
    assert "tod_sin" in names
    assert "dow_cos" in names
    assert "month_sin" in names
    assert "residual_SPY" in names
    assert "industry_tech" in names
    assert "spy_ret_1m" not in names


def test_emit_feature_names_adds_scale_moms_and_extra_horizons():
    names = _emit_feature_names(
        0,
        [{"tag": "5m"}, {"tag": "1s"}],
        ["SPY"],
        ("tech",),
        [{"tag": "2h"}],
    )
    assert "ret_lag_0" not in names
    assert "mom_5m" in names
    assert names.count("ret_2h") == 1
    assert "rv_2h" in names
    assert "mom_2h" in names


def test_session_features_lookback_zero_is_valid():
    assert SessionFeatureRows.validate_params({"lookback": 0}) == []


def test_session_features_momentum_horizons_skip_lags():
    spec = _mini_spec()
    spec["industry"] = {"AAPL": "tech"}
    rows = [
        {
            "symbol": symbol,
            "asof_ms": _ms(i),
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.0 + i,
            "volume": 100.0,
        }
        for symbol in ("AAPL", "SPY")
        for i in range(16)
    ]
    extra = [{"width": 3, "tag": "3m", "cross_session": False}]
    out = SessionFeatureRows(
        "features",
        {"lookback": 0, "layout": "columns", "momentum_horizons": extra},
    ).run(None, {"records": rows, "spec": spec})
    names = out["records"][0]["names"]
    assert "ret_lag_0" not in names
    assert "mom_2m" in names
    assert "ret_3m" in names
    assert "rv_3m" in names
    assert "mom_3m" in names
    assert "clv" in names
    assert "residual_SPY" in names


def test_horizon_leads_are_the_declared_range():
    assert horizon_leads(5, 5, 15) == (5, 10, 15)


def test_horizon_scan_drops_labels_that_land_after_val_end():
    spec = _mini_spec()
    spec["features"] = ["ret_lag_0"]
    spec["horizon"] = {
        "lead_start": 2,
        "lead_step": 1,
        "lead_stop": 2,
        "anchors": [2],
        "top_k": 1,
        "se_mult": 2.0,
        "band_leads": 1,
    }
    bars = [{"symbol": "AAPL", "asof_ms": _ms(i), "close": 100.0 + i} for i in range(8)]
    rows = [
        {"symbol": "AAPL", "asof_ms": _ms(i), "ret_lag_0": 0.01 * i, "close": 100.0 + i}
        for i in range(8)
    ]
    out = HorizonScan(
        "scan",
        {
            "split": "val",
            "train_end_ms": _ms(2),
            "val_start_ms": _ms(3),
            "val_end_ms": _ms(4),
        },
    ).run(None, {"records": rows, "bars": bars, "spec": spec})
    assert out["records"][0]["n_val"] == 0.0


def test_no_information_scan_drops_labels_that_land_after_val_end():
    spec = _mini_spec()
    spec["features"] = ["ret_lag_0"]
    spec["horizon"] = {
        "lead_start": 2,
        "lead_step": 1,
        "lead_stop": 2,
        "anchors": [2],
        "top_k": 1,
        "se_mult": 2.0,
        "band_leads": 1,
    }
    bars = [{"symbol": "AAPL", "asof_ms": _ms(i), "close": 100.0 + i} for i in range(8)]
    rows = [
        {"symbol": "AAPL", "asof_ms": _ms(i), "ret_lag_0": 0.01 * i, "close": 100.0 + i}
        for i in range(8)
    ]
    out = NoInformationScan(
        "scan",
        {
            "split": "val",
            "train_end_ms": _ms(2),
            "val_start_ms": _ms(3),
            "val_end_ms": _ms(4),
        },
    ).run(None, {"records": rows, "bars": bars, "spec": spec})
    assert out["metrics"]["n_series"] == 1.0
    assert out["metrics"]["go_AAPL"] in (0.0, 1.0)
    assert out["records"][0]["symbol"] == "AAPL"
    assert out["records"][0]["n"] == 0.0


def test_score_symbols_changes_outputs_not_the_pooled_fit():
    spec = _mini_spec()
    spec["features"] = ["ret_lag_0"]
    spec["period_ms"] = 60_000
    spec["horizon"] = {
        "lead_start": 1,
        "lead_step": 1,
        "lead_stop": 1,
        "anchors": [1],
        "top_k": 1,
        "se_mult": 2.0,
        "band_leads": 1,
    }
    bars = []
    rows = []
    for symbol, phase in (("AAPL", 0), ("JPM", 2)):
        price = 100.0
        for i in range(50):
            ret = 0.002 * (((i + phase) % 7) - 3)
            price *= math.exp(ret)
            bars.append({"symbol": symbol, "asof_ms": _ms(i), "close": price})
            rows.append(
                {
                    "symbol": symbol,
                    "asof_ms": _ms(i),
                    "ret_lag_0": ret,
                    "close": price,
                }
            )
    cuts = {
        "split": "val",
        "train_end_ms": _ms(29),
        "val_start_ms": _ms(31),
        "val_end_ms": _ms(49),
    }
    inputs = {"records": rows, "bars": bars, "spec": spec}
    full = NoInformationScan("full", cuts).run(None, inputs)
    scored = NoInformationScan(
        "scored",
        {**cuts, "fit_symbols": ["AAPL", "JPM"], "score_symbols": ["JPM"]},
    ).run(None, inputs)
    assert full["metrics"]["n_series"] == 2.0
    assert scored["metrics"]["n_series"] == 2.0
    assert scored["metrics"]["n_scored_series"] == 1.0
    assert scored["metrics"]["n_train"] == full["metrics"]["n_train"]
    assert scored["metrics"]["n_fit_series"] == 2.0
    assert scored["metrics"]["n_val"] == full["metrics"]["n_val"]
    assert scored["metrics"]["train_mspe"] == pytest.approx(
        full["metrics"]["train_mspe"]
    )
    assert {row["symbol"] for row in scored["records"]} == {"JPM"}
    assert "go_AAPL" not in scored["metrics"]


def test_no_information_scan_writes_every_scored_row(tmp_path):
    """ADR-0064: every scored validation row is on disk, and the summary
    numbers the fold reported are recomputable from those rows."""
    pytest.importorskip("pyarrow")

    from dskit.pipeline.node import NodeContext
    from dskit.pipeline.predictions import (
        PREDICTIONS_FILE,
        read_prediction_series,
        read_predictions,
    )

    spec = _mini_spec()
    spec["features"] = ["ret_lag_0"]
    spec["period_ms"] = 60_000
    spec["horizon"] = {
        "lead_start": 1,
        "lead_step": 1,
        "lead_stop": 1,
        "anchors": [1],
        "top_k": 1,
        "se_mult": 2.0,
        "band_leads": 1,
    }
    n = 40
    bars, rows = [], []
    for symbol in ("AAPL", "JPM"):
        px = 100.0
        for i in range(n):
            ret = 0.001 * ((i % 7) - 3)
            px *= math.exp(ret)
            bars.append({"symbol": symbol, "asof_ms": _ms(i), "close": px})
            rows.append(
                {
                    "symbol": symbol,
                    "asof_ms": _ms(i),
                    "ret_lag_0": ret,
                    "close": px,
                }
            )
    ctx = NodeContext(
        name="scan",
        asof="2025-11-30",
        run_dir=str(tmp_path),
        fold_index=7,
    )
    out = NoInformationScan(
        "scan",
        {
            "split": "val",
            "train_end_ms": _ms(24),
            "val_start_ms": _ms(26),
            "val_end_ms": _ms(n - 1),
        },
    ).run(ctx, {"records": rows, "bars": bars, "spec": spec})
    path = tmp_path / "artifacts" / "scan" / PREDICTIONS_FILE
    assert path.is_file()
    saved = read_predictions(str(tmp_path))
    assert saved["period_minutes"] == 1
    assert set(saved["series"]) == {"AAPL", "JPM"}
    assert set(saved["fold"]) == {7}
    assert set(saved["horizon"]) == {1}
    assert len(saved["ts"]) == len(saved["y"]) == len(saved["yhat"])
    # The rows REPRODUCE the fold's reported summary: the benchmark and
    # the model MSPE the curve row carries are means over exactly these
    # pairs, which is what makes the saved rows evidence and not a
    # parallel account of the same fold.
    units = {e["symbol"]: e for e in read_prediction_series(str(tmp_path))}
    assert set(units) == {"AAPL", "JPM"}
    for record in out["records"]:
        unit = units[record["symbol"]]
        assert unit["lead"] == record["lead"]
        assert unit["h_steps"] == 1
        assert len(unit["y"]) == int(record["n"])
        mspe_model = sum((y - f) ** 2 for y, f in zip(unit["y"], unit["yhat"])) / len(
            unit["y"]
        )
        assert mspe_model == pytest.approx(record["mspe_model"], rel=1e-5)
        assert unit["q"] == pytest.approx(record["mspe_mean"], rel=1e-5)
        assert unit["q"] > 0.0
        assert sum(unit["d"]) / len(unit["d"]) == pytest.approx(
            record["mspe_mean"] - record["mspe_model"], rel=1e-5, abs=1e-12
        )
    # And the fold's own verdict columns ride on the metrics and rows.
    for symbol in ("AAPL", "JPM"):
        assert f"r2oos_{symbol}" in out["metrics"]
        assert f"dm_t_{symbol}" in out["metrics"]
    for row in out["records"]:
        assert set(row) >= {"r2oos", "dm_t", "dm_p", "t_stat", "mspe_model"}


def test_no_information_scan_fits_once_and_walks_h():
    spec = _mini_spec()
    spec["features"] = ["ret_lag_0"]
    spec["period_ms"] = 60_000
    spec["horizon"] = {
        "lead_start": 1,
        "lead_step": 1,
        "lead_stop": 3,
        "anchors": [1],
        "top_k": 1,
        "se_mult": 2.0,
        "band_leads": 1,
    }
    n = 40
    bars, rows = [], []
    for symbol, bump in (("AAPL", 0.0), ("JPM", 0.1), ("XOM", 0.2)):
        px = 100.0
        for i in range(n):
            ret = 0.001 * ((i % 7) - 3)
            px *= math.exp(ret)
            bars.append({"symbol": symbol, "asof_ms": _ms(i), "close": px})
            rows.append(
                {
                    "symbol": symbol,
                    "asof_ms": _ms(i),
                    "ret_lag_0": ret,
                    "close": px,
                }
            )
    out = NoInformationScan(
        "scan",
        {
            "split": "val",
            "train_end_ms": _ms(24),
            "val_start_ms": _ms(26),
            "val_end_ms": _ms(n - 1),
        },
    ).run(None, {"records": rows, "bars": bars, "spec": spec})
    assert out["metrics"]["n_leads"] == 3.0
    assert out["metrics"]["n_series"] == 3.0
    assert len(out["records"]) == 9
    assert {row["symbol"] for row in out["records"]} == {"AAPL", "JPM", "XOM"}
    assert {row["lead"] for row in out["records"]} == {1, 2, 3}
    for symbol in ("AAPL", "JPM", "XOM"):
        assert out["metrics"][f"go_{symbol}"] in (0.0, 1.0)
        assert out["metrics"][f"h_star_{symbol}"] >= 0.0
        assert 0.0 <= out["metrics"][f"p_value_{symbol}"] <= 1.0
    assert 0.0 <= out["metrics"]["go_frac"] <= 1.0
    assert "go" not in out["metrics"]
    assert out["metrics"]["n_val"] > 0.0
    assert out["metrics"]["train_mspe"] >= 0.0
    assert out["metrics"]["val_mspe"] >= 0.0
    assert -1.0 <= out["metrics"]["train_ic"] <= 1.0
    assert -1.0 <= out["metrics"]["val_ic"] <= 1.0
    assert "train_mspe_AAPL" not in out["metrics"]
    assert out["metrics"]["train_yhat_sd"] > 0.0


def test_session_features_reuse_one_build_across_folds():
    """A walk-forward calls this node once per fold with identical input.

    Nothing upstream reads the fold cuts, so the second call must return
    the first build rather than rebuild 1.25M rows forty times.
    """
    import numpy as np

    spec = _mini_spec()
    spec["period_ms"] = 60_000
    bars = []
    for symbol in ("AAPL", "SPY"):
        px = 100.0
        for i in range(30):
            px *= math.exp(0.001 * ((i % 5) - 2))
            bars.append(
                {
                    "symbol": symbol,
                    "asof_ms": _ms(i),
                    "session": "rth",
                    "open": px,
                    "high": px,
                    "low": px,
                    "close": px,
                    "volume": 1000.0,
                }
            )
    node = SessionFeatureRows("features", {"lookback": 2, "layout": "columns"})

    first = node.run(None, {"records": bars, "spec": spec})
    second = node.run(None, {"records": list(bars), "spec": spec})

    # same content
    assert len(first["records"]) == len(second["records"])
    for a, b in zip(first["records"], second["records"]):
        assert a["symbol"] == b["symbol"]
        assert np.array_equal(a["asof_ms"], b["asof_ms"])
        assert np.array_equal(a["X"], b["X"], equal_nan=True)
    # the arrays are shared, so the second call did not rebuild
    assert second["records"][0]["X"] is first["records"][0]["X"]
    assert second["tape"] is first["tape"]
    # the outer list is a copy, so filtering downstream cannot corrupt it
    assert second["records"] is not first["records"]
    second["records"].clear()
    assert first["records"]

    # a different input must NOT hit the cache
    third = node.run(None, {"records": bars[:20], "spec": spec})
    assert third["records"][0]["X"] is not first["records"][0]["X"]


def test_no_information_scan_honours_the_left_training_bound():
    """``train_days`` only bites if the node reads ``train_start_ms``.

    Regression: the walk-forward driver computed the bound and wrote it
    into every fold's config, but this node neither accepted nor read
    it, so a declared two-year slide silently trained all-prior and
    grew by one validation period every fold.
    """
    spec = _mini_spec()
    spec["features"] = ["ret_lag_0"]
    spec["period_ms"] = 60_000
    spec["horizon"] = {
        "lead_start": 1,
        "lead_step": 1,
        "lead_stop": 2,
        "anchors": [1],
        "top_k": 1,
        "se_mult": 2.0,
        "band_leads": 1,
    }
    n = 60
    bars, rows = [], []
    for symbol in ("AAPL", "JPM"):
        px = 100.0
        for i in range(n):
            px *= math.exp(0.001 * ((i % 7) - 3))
            bars.append({"symbol": symbol, "asof_ms": _ms(i), "close": px})
            rows.append(
                {
                    "symbol": symbol,
                    "asof_ms": _ms(i),
                    "ret_lag_0": 0.001 * ((i % 7) - 3),
                    "close": px,
                }
            )
    base = {
        "split": "val",
        "train_end_ms": _ms(40),
        "val_start_ms": _ms(45),
        "val_end_ms": _ms(n - 1),
    }
    inputs = {"records": rows, "bars": bars, "spec": spec}

    unbounded = NoInformationScan("scan", base).run(None, inputs)
    bounded = NoInformationScan("scan", {**base, "train_start_ms": _ms(20)}).run(
        None, inputs
    )

    assert unbounded["metrics"]["n_train"] > bounded["metrics"]["n_train"]
    assert bounded["metrics"]["n_train"] > 0.0
    # val is untouched by the left training bound
    assert unbounded["metrics"]["n_val"] == bounded["metrics"]["n_val"]


def test_no_information_scan_refuses_a_backwards_training_bound():
    """A left bound at or past the cut is a config error, not a silent 0."""
    problems = NoInformationScan.validate_params(
        {
            "split": "val",
            "train_end_ms": 1_000,
            "train_start_ms": 2_000,
            "val_start_ms": 3_000,
            "val_end_ms": 4_000,
        }
    )
    assert any("train_start_ms must be < train_end_ms" in p for p in problems)


def test_no_information_scan_refuses_a_constant_forecast():
    """A stump-only tree predicts the mean, so the fold must fail loudly.

    Regression for A0013: min_split_gain above any achievable split
    gain made every tree one leaf. The walk still "ran" and reported
    IC exactly 0 for forty folds, which reads as a finding about the
    market rather than a broken model.
    """
    pytest.importorskip("lightgbm")
    spec = _mini_spec()
    spec["features"] = ["ret_lag_0"]
    spec["period_ms"] = 60_000
    spec["horizon"] = {
        "lead_start": 1,
        "lead_step": 1,
        "lead_stop": 3,
        "anchors": [1],
        "top_k": 1,
        "se_mult": 2.0,
        "band_leads": 1,
    }
    spec["scan"] = {
        "estimator": "lightgbm.LGBMRegressor",
        "estimator_params": {
            "n_estimators": 5,
            "min_split_gain": 1e9,
            "n_jobs": 1,
            "random_state": 0,
            "verbosity": -1,
        },
    }
    n = 40
    bars, rows = [], []
    for symbol in ("AAPL", "JPM"):
        px = 100.0
        for i in range(n):
            ret = 0.001 * ((i % 7) - 3)
            px *= math.exp(ret)
            bars.append({"symbol": symbol, "asof_ms": _ms(i), "close": px})
            rows.append(
                {
                    "symbol": symbol,
                    "asof_ms": _ms(i),
                    "ret_lag_0": ret,
                    "close": px,
                }
            )
    with pytest.raises(ValueError, match="degenerate forecast"):
        NoInformationScan(
            "scan",
            {
                "split": "val",
                "train_end_ms": _ms(24),
                "val_start_ms": _ms(26),
                "val_end_ms": _ms(n - 1),
            },
        ).run(None, {"records": rows, "bars": bars, "spec": spec})


def test_lead_labels_drop_rows_whose_label_lands_after_the_cut():
    spec = _mini_spec()
    spec["features"] = ["ret_lag_0"]
    bars = [{"symbol": "AAPL", "asof_ms": _ms(i), "close": 100.0 + i} for i in range(8)]
    rows = [
        {"symbol": "AAPL", "asof_ms": _ms(i), "ret_lag_0": 0.01 * i} for i in range(8)
    ]
    params = {
        "lead": 2,
        "train_end_ms": _ms(2),
        "val_start_ms": _ms(3),
        "val_end_ms": _ms(4),
    }
    train = LeadLabeledRows("train", {**params, "split": "train"}).run(
        None, {"records": rows, "bars": bars, "spec": spec}
    )["records"]
    val = LeadLabeledRows("val", {**params, "split": "val"}).run(
        None, {"records": rows, "bars": bars, "spec": spec}
    )["records"]
    assert train
    assert all(row["y_up"] in (0.0, 1.0) for row in train)
    assert all(row["asof_ms"] + 2 * 60_000 <= _ms(2) for row in train)
    assert val == []


def test_lookback_scan_picks_a_finite_L_and_keeps_calendar():
    spec = _mini_spec()
    spec["lookback"] = 4
    spec["features"] = [
        "ret_lag_0",
        "ret_lag_1",
        "ret_lag_2",
        "ret_lag_3",
        "tod_sin",
    ]
    spec["scan"] = {
        "l_start": 2,
        "l_step": 1,
        "lookback_stop": 4,
        "keep_frac": 0.95,
        "keep_tau": 0.05,
    }
    bars = [
        {"symbol": "AAPL", "asof_ms": _ms(i), "close": 100.0 + i} for i in range(16)
    ]
    rows = [
        {
            "symbol": "AAPL",
            "asof_ms": _ms(i),
            "ret_lag_0": 0.01 * i,
            "ret_lag_1": 0.02 * i,
            "ret_lag_2": 0.03 * i,
            "ret_lag_3": 0.04 * i,
            "tod_sin": 0.5,
            "close": 100.0 + i,
        }
        for i in range(16)
    ]
    out = LookbackScan(
        "lscan",
        {
            "split": "val",
            "lead": 2,
            "train_end_ms": _ms(8),
            "val_start_ms": _ms(9),
            "val_end_ms": _ms(14),
        },
    ).run(None, {"records": rows, "bars": bars, "spec": spec})
    assert out["lookback"] in (2, 3, 4)
    assert "tod_sin" in out["features"]
    assert out["metrics"]["n_features"] >= 1.0


def test_column_layout_keeps_frames_and_still_scans():
    spec = _mini_spec()
    spec["industry"] = {"AAPL": "tech"}
    rows = [
        {
            "symbol": symbol,
            "asof_ms": _ms(i),
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.0 + i,
            "volume": 100.0,
        }
        for symbol in ("AAPL", "SPY")
        for i in range(16)
    ]
    frames = SessionFeatureRows(
        "features", {"layout": "columns", "dtype": "float32"}
    ).run(None, {"records": rows, "spec": spec})
    assert "tape" in frames
    assert "X" in frames["records"][0]
    assert str(frames["records"][0]["X"].dtype) == "float32"
    kept = KeepSymbols("tradable", {"field": "symbol"}).run(
        None, {"records": frames["records"], "symbols": ["AAPL"]}
    )["records"]
    assert [row["symbol"] for row in kept] == ["AAPL"]
    spec["features"] = ["ret_lag_0"]
    out = HorizonScan(
        "scan",
        {
            "split": "val",
            "train_end_ms": _ms(8),
            "val_start_ms": _ms(9),
            "val_end_ms": _ms(14),
        },
    ).run(
        None,
        {
            "records": kept,
            "bars": frames["tape"],
            "spec": spec,
        },
    )
    assert "farthest_confident_lead" in out["metrics"]


def test_lead_labels_accept_column_frames():
    spec = _mini_spec()
    spec["industry"] = {"AAPL": "tech"}
    rows = [
        {
            "symbol": symbol,
            "asof_ms": _ms(i),
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.0 + i,
            "volume": 100.0,
        }
        for symbol in ("AAPL", "SPY")
        for i in range(16)
    ]
    frames = SessionFeatureRows("features", {"layout": "columns"}).run(
        None, {"records": rows, "spec": spec}
    )
    kept = KeepSymbols("tradable", {"field": "symbol"}).run(
        None, {"records": frames["records"], "symbols": ["AAPL"]}
    )["records"]
    spec["features"] = ["ret_lag_0"]
    train = LeadLabeledRows(
        "train",
        {
            "lead": 2,
            "split": "train",
            "train_end_ms": _ms(8),
            "val_start_ms": _ms(9),
            "val_end_ms": _ms(14),
        },
    ).run(
        None,
        {
            "records": kept,
            "bars": frames["tape"],
            "spec": spec,
        },
    )["records"]
    assert train
    assert "X" not in train[0]
    assert "y_next" in train[0]
    assert train[0]["symbol"] == "AAPL"


def _label_tape(n=600, seed=7):
    """A two-symbol 1-minute tape: SPY random-walks, JPM is 2x SPY plus noise."""
    import numpy as np

    rng = np.random.default_rng(seed)
    ref = rng.normal(0.0, 1e-3, n)
    own = 2.0 * ref + rng.normal(0.0, 1e-6, n)
    stamps = np.array([_ms(i) for i in range(n)], dtype=np.int64)
    return {
        "SPY": (stamps, 100.0 * np.exp(np.cumsum(ref))),
        "JPM": (stamps, 50.0 * np.exp(np.cumsum(own))),
    }


def test_raw_label_is_the_log_ratio_and_the_default():
    import numpy as np

    from intraday_equities.nodes import _LeadLabel

    arrays = _label_tape()
    label = _LeadLabel(arrays, 60_000)
    loc = np.array([10, 20, 30])
    future = loc + 5
    _stamps, prices = arrays["JPM"]
    expected = np.log(prices[future] / prices[loc])
    assert not label.transformed
    assert np.allclose(label.values("JPM", loc, future), expected)


def test_vol_normalised_label_divides_by_sigma_root_h():
    import numpy as np

    from dskit.pipeline.libs.numpy import log_return, rolling_std
    from intraday_equities.nodes import _LeadLabel

    arrays = _label_tape()
    label = _LeadLabel(arrays, 60_000, scale="vol", vol_window=100)
    loc = np.array([300, 400])
    future = loc + 9
    stamps, prices = arrays["JPM"]
    sigma = rolling_std(log_return(prices, 1), 100)
    expected = np.log(prices[future] / prices[loc]) / (sigma[loc] * math.sqrt(9))
    assert np.allclose(label.values("JPM", loc, future), expected)


def test_vol_normalised_label_reads_no_bar_after_t():
    """sigma is causal: rewriting the future cannot move a past label."""
    import numpy as np

    from intraday_equities.nodes import _LeadLabel

    arrays = _label_tape()
    loc, future = np.array([300]), np.array([305])
    before = _LeadLabel(arrays, 60_000, scale="vol", vol_window=100).values(
        "JPM",
        loc,
        future,
    )
    stamps, prices = arrays["JPM"]
    moved = prices.copy()
    moved[306:] *= 3.0  # every bar strictly after the label's own window
    shifted = dict(arrays, JPM=(stamps, moved))
    after = _LeadLabel(shifted, 60_000, scale="vol", vol_window=100).values(
        "JPM",
        loc,
        future,
    )
    assert np.allclose(before, after)


def test_market_residual_label_removes_the_reference_move():
    import numpy as np

    from intraday_equities.nodes import _LeadLabel

    arrays = _label_tape()
    loc = np.array([500, 520])
    future = loc + 10
    raw = _LeadLabel(arrays, 60_000).values("JPM", loc, future)
    residual = _LeadLabel(arrays, 60_000, residual="SPY", beta_window=200).values(
        "JPM",
        loc,
        future,
    )
    # JPM is 2x SPY by construction, so beta -> 2 and the residual is
    # the noise term: two orders of magnitude under the raw return.
    assert np.all(np.abs(residual) < 0.05 * np.abs(raw))


def test_reference_symbol_can_use_its_raw_label_in_the_pooled_fit():
    import numpy as np

    from intraday_equities.nodes import _LeadLabel

    arrays = _label_tape()
    loc = np.array([300, 400])
    future = loc + 9
    raw_scaled = _LeadLabel(
        arrays,
        60_000,
        scale="vol",
        vol_window=100,
    ).values("SPY", loc, future)
    pooled = _LeadLabel(
        arrays,
        60_000,
        scale="vol",
        residual="SPY",
        residual_self="raw",
        vol_window=100,
    ).values("SPY", loc, future)
    assert np.all(np.isfinite(pooled))
    assert np.allclose(pooled, raw_scaled)


def test_session_boundary_is_not_a_one_minute_return_for_sigma():
    import numpy as np

    from intraday_equities.nodes import _bar_returns

    stamps = np.array([_ms(0), _ms(1), _ms(2000), _ms(2001)], dtype=np.int64)
    prices = np.array([100.0, 101.0, 130.0, 131.0])
    returns = _bar_returns(stamps, prices, 60_000)
    assert math.isnan(returns[0])  # no bar before the first
    assert math.isnan(returns[2])  # the overnight jump, blanked
    assert not math.isnan(returns[1]) and not math.isnan(returns[3])


def test_label_refuses_an_unknown_scale_and_a_missing_reference():
    from intraday_equities.nodes import _LeadLabel

    arrays = _label_tape(n=10)
    with pytest.raises(ValueError, match="label_scale"):
        _LeadLabel(arrays, 60_000, scale="sharpe")
    with pytest.raises(ValueError, match="no tape"):
        _LeadLabel(arrays, 60_000, residual="QQQ")


def test_scan_validates_the_label_knobs():
    base = {"split": "val", "train_end_ms": 1, "val_start_ms": 2, "val_end_ms": 3}
    assert NoInformationScan.validate_params(base) == []
    assert (
        NoInformationScan.validate_params(
            dict(
                base,
                label_scale="vol",
                label_residual="SPY",
                vol_window_minutes=390,
                beta_window_minutes=3900,
                vol_floor=1e-8,
            )
        )
        == []
    )
    assert any(
        "label_scale" in problem
        for problem in NoInformationScan.validate_params(
            dict(base, label_scale="sharpe")
        )
    )
    assert any(
        "label_residual_self" in problem
        for problem in NoInformationScan.validate_params(
            dict(base, label_residual_self="raw")
        )
    )
    assert (
        NoInformationScan.validate_params(
            dict(base, label_residual="SPY", label_residual_self="raw")
        )
        == []
    )
    assert any(
        "vol_window_minutes" in problem
        for problem in NoInformationScan.validate_params(
            dict(base, vol_window_minutes=1)
        )
    )
    assert any(
        "vol_floor" in problem
        for problem in NoInformationScan.validate_params(dict(base, vol_floor=0))
    )
    assert any(
        "null" in problem
        for problem in NoInformationScan.validate_params(
            dict(base, label_residual=None)
        )
    )


def test_every_label_knob_is_allowed_and_validated():
    """The knob list, the allowed set, and the validator agree.

    LABEL_PARAMS is what ADR-0059 declares; a knob that reaches only one
    of the three is the silently-ignored knob default-deny exists to stop.
    """
    from intraday_equities.nodes import LABEL_PARAMS

    assert set(LABEL_PARAMS) <= set(NoInformationScan._PARAMS)
    base = {"split": "val", "train_end_ms": 1, "val_start_ms": 2, "val_end_ms": 3}
    for knob in LABEL_PARAMS:
        problems = NoInformationScan.validate_params(dict(base, **{knob: None}))
        assert any(knob in problem for problem in problems), (
            f"{knob} is allowed but no validator ever names it"
        )


def test_vol_normalised_scan_scores_the_reshaped_label():
    spec = _mini_spec()
    spec["features"] = ["ret_lag_0"]
    spec["period_ms"] = 60_000
    spec["horizon"] = {
        "lead_start": 1,
        "lead_step": 1,
        "lead_stop": 2,
        "anchors": [1],
        "top_k": 1,
        "se_mult": 2.0,
        "band_leads": 1,
    }
    n = 120
    bars, rows = [], []
    for i in range(n):
        close = 100.0 + math.sin(i / 3.0)
        bars.append({"symbol": "AAPL", "asof_ms": _ms(i), "close": close})
        rows.append(
            {
                "symbol": "AAPL",
                "asof_ms": _ms(i),
                "ret_lag_0": math.cos(i / 3.0),
                "close": close,
            }
        )
    cuts = {
        "split": "val",
        "train_end_ms": _ms(80),
        "val_start_ms": _ms(85),
        "val_end_ms": _ms(n - 1),
    }
    raw = NoInformationScan("scan", cuts).run(
        None,
        {"records": rows, "bars": bars, "spec": spec},
    )
    scaled = NoInformationScan(
        "scan",
        dict(cuts, label_scale="vol", vol_window_minutes=20),
    ).run(None, {"records": rows, "bars": bars, "spec": spec})
    # Same rows, a different label: the label's own sd moves, and MSPE
    # rides with it. The vol run is a different estimand, not a rescale.
    assert scaled["metrics"]["label_sd"] != raw["metrics"]["label_sd"]
    assert scaled["metrics"]["n_train"] < raw["metrics"]["n_train"]  # warmup


def test_bars_source_scans_once_per_store_content(tmp_path, monkeypatch):
    """A walk-forward's per-fold source rebuild must not re-read the store."""
    import intraday_equities.nodes as nodes

    root = str(tmp_path / "ob")
    _write_store(root, "alpaca-sip", n_minutes=4)
    params = {"root": root, "source": "alpaca-sip", "universe": UNIVERSE_PATH}

    calls = []
    real = nodes.scan_stream
    monkeypatch.setattr(
        nodes,
        "scan_stream",
        lambda *a, **k: (calls.append(1), real(*a, **k))[1],
    )
    nodes.BarsFromStore._cached_key = None
    nodes.BarsFromStore._cached_snap = None
    nodes.BarsFromStore._cached_fingerprint = None

    first = nodes.BarsFromStore("bars", params).fingerprint()
    second = nodes.BarsFromStore("bars", params).fingerprint()
    assert calls == [1], "the second fold's source re-read the store"
    assert first == second

    _write_store(root, "alpaca-sip", n_minutes=6)
    grown = nodes.BarsFromStore("bars", params).fingerprint()
    assert len(calls) == 2, "a grown store must invalidate the cache"
    assert grown != first


def _zulu(index):
    """The same instant a bar spells ``+00:00``, spelled ``Z``.

    The two packs really do differ this way on disk, and a join on the
    spelling matches nothing while looking perfectly healthy.
    """
    return _ts(index).replace("+00:00", "Z")


def _write_quote_store(root, source, minutes, symbols=("AAPL",)):
    """Write a minute-quote observation tree beside the bar tree."""
    directory = os.path.join(root, "observations", source, "acq-0001")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "quote_minutes.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        for symbol in symbols:
            for index in minutes:
                mid = 100.0 + index
                row = {
                    "stream": "quote_minutes",
                    "mode": "backfill",
                    "kind": "observation",
                    "effective_date": _zulu(index),
                    "acquired_at": "2026-01-06T00:00:00+00:00",
                    "data": {
                        "symbol": symbol,
                        "ts": _zulu(index),
                        "bid": mid - 0.01,
                        "ask": mid + 0.01,
                        "mid": mid,
                        "spread": 0.02,
                        "spread_bps": 10000.0 * 0.02 / mid,
                        "bid_size": 100,
                        "ask_size": 200,
                        "bid_exchange": "Z",
                        "ask_exchange": "N",
                        "quote_ts": _zulu(index),
                        "quote_age_ms": 5,
                        "n_quotes": 42,
                        "n_crossed": 0,
                        "n_locked": 0,
                    },
                }
                fh.write(json.dumps(row, sort_keys=True) + "\n")
    return path


def test_a_declared_quote_source_puts_mid_and_spread_on_the_bar(tmp_path):
    """ADR-0065: the midpoint is selectable as a price beside the trade."""
    import intraday_equities.nodes as nodes

    root = str(tmp_path / "ob")
    _write_store(root, "alpaca-sip", n_minutes=4, symbols=("AAPL",))
    _write_quote_store(root, "alpaca-sip-quotes", [0, 1, 3])
    params = {
        "root": root,
        "source": "alpaca-sip",
        "universe": UNIVERSE_PATH,
        "quote_source": "alpaca-sip-quotes",
    }
    nodes.BarsFromStore._cached_key = None
    nodes.BarsFromStore._cached_snap = None
    nodes.BarsFromStore._cached_fingerprint = None
    rows = nodes.BarsFromStore("bars", params).run(_ctx(tmp_path), {})["records"]

    assert len(rows) == 4
    # The quote tree spells its minutes "...Z" and the bar tree
    # "...+00:00"; the join is on the instant, so both land.
    priced = {row["ts"]: row for row in rows}
    assert set(priced) == {_ts(index) for index in range(4)}
    first = priced[_ts(0)]
    assert first["mid"] == 100.0
    assert first["bid"] == 99.99 and first["ask"] == 100.01
    assert first["spread"] == 0.02
    assert first["close"] == 100.0, "the trade price is untouched"
    # A bar with no quote keeps its minute and says so with None.
    assert priced[_ts(2)]["mid"] is None
    assert priced[_ts(2)]["close"] == 102.0
    # Undeclared, the bars come back exactly as before.
    nodes.BarsFromStore._cached_key = None
    nodes.BarsFromStore._cached_snap = None
    bare = nodes.BarsFromStore(
        "bars", {k: v for k, v in params.items() if k != "quote_source"}
    ).run(_ctx(tmp_path), {})["records"]
    assert all("mid" not in row for row in bare)


def test_quote_attachment_knobs_are_refused_without_a_source_or_on_a_clash():
    """A quote adds columns; it never overwrites the trade price."""
    from intraday_equities.nodes import BarsFromStore as Bars

    base = {"root": "ob", "source": "alpaca-sip", "universe": UNIVERSE_PATH}
    assert Bars.validate_params(dict(base)) == []
    problems = Bars.validate_params({**base, "quote_fields": ["mid"]})
    assert any("meaningless without quote_source" in p for p in problems)
    problems = Bars.validate_params(
        {
            **base,
            "quote_source": "alpaca-sip-quotes",
            "quote_fields": ["close"],
        }
    )
    assert any("collide with the bar" in p for p in problems)
    problems = Bars.validate_params({**base, "quote_source": ""})
    assert any("quote_source must be a non-empty string" in p for p in problems)


def test_declared_lead_grid_overrides_the_universe():
    """A run asks its own horizon; the cohort file is not restated."""
    from intraday_equities.nodes import LEAD_PARAMS, _lead_grid

    horizon = {"lead_start": 5, "lead_step": 5, "lead_stop": 20}
    assert _lead_grid({}, horizon) == ((5, 10, 15, 20), 5)
    assert _lead_grid({"lead_start": 30, "lead_stop": 30}, horizon) == (
        (30,),
        30,
    )
    assert _lead_grid({"lead_stop": 10}, horizon) == ((5, 10), 5)
    base = {"split": "val", "train_end_ms": 1, "val_start_ms": 2, "val_end_ms": 3}
    assert (
        NoInformationScan.validate_params(
            dict(base, lead_start=60, lead_step=1, lead_stop=60)
        )
        == []
    )
    assert any(
        "lead_stop" in problem
        for problem in NoInformationScan.validate_params(
            dict(base, lead_start=30, lead_stop=10)
        )
    )
    assert any(
        "lead_start" in problem
        for problem in NoInformationScan.validate_params(dict(base, lead_start=0))
    )
    for knob in LEAD_PARAMS:
        assert knob in NoInformationScan._PARAMS
        assert any(
            knob in problem
            for problem in NoInformationScan.validate_params(dict(base, **{knob: None}))
        ), f"{knob} is allowed but no validator names it"


def test_scan_trains_and_walks_the_declared_lead():
    spec = _mini_spec()
    spec["features"] = ["ret_lag_0"]
    spec["period_ms"] = 60_000
    spec["horizon"] = {
        "lead_start": 1,
        "lead_step": 1,
        "lead_stop": 2,
        "anchors": [1],
        "top_k": 1,
        "se_mult": 2.0,
        "band_leads": 1,
    }
    n = 60
    bars, rows = [], []
    for i in range(n):
        close = 100.0 + math.sin(i / 4.0)
        bars.append({"symbol": "AAPL", "asof_ms": _ms(i), "close": close})
        rows.append(
            {
                "symbol": "AAPL",
                "asof_ms": _ms(i),
                "ret_lag_0": math.cos(i / 4.0),
                "close": close,
            }
        )
    cuts = {
        "split": "val",
        "train_end_ms": _ms(40),
        "val_start_ms": _ms(42),
        "val_end_ms": _ms(n - 1),
    }
    out = NoInformationScan(
        "scan",
        dict(cuts, lead_start=3, lead_step=1, lead_stop=3),
    ).run(None, {"records": rows, "bars": bars, "spec": spec})
    leads = {int(row["lead"]) for row in out["records"]}
    assert leads == {3}, "the declared grid is the grid that was walked"


def test_universe_overrides_only_the_three_run_knobs():
    """ADR-0065: spacing and price move per run; the cohort does not."""
    node = Universe(
        "universe",
        {
            "path": UNIVERSE_PATH,
            "overrides": {
                "period_ms": 60_000,
                "offset_ms": 0,
                "price_field": "vwap",
            },
        },
    )
    out = node.run(None, {})
    assert out["spec"]["period_ms"] == 60_000
    assert out["spec"]["price_field"] == "vwap"
    # The cohort file's own values are untouched by the override.
    plain = Universe("universe", {"path": UNIVERSE_PATH})
    assert plain.run(None, {})["spec"]["period_ms"] == 300_000
    assert out["symbols"] == plain.run(None, {})["symbols"]
    # Two spacings are two identities.
    assert node.fingerprint()["sha256"] != plain.fingerprint()["sha256"]
    assert Universe.validate_params(
        {"path": UNIVERSE_PATH, "overrides": {"holidays": []}}
    )
    assert Universe.validate_params(
        {"path": UNIVERSE_PATH, "overrides": {"price_field": "bid"}}
    )


def test_session_features_price_the_declared_field():
    """The lag returns, the tape and the label read ONE series."""
    spec = _mini_spec(price_field="vwap", period_ms=60_000, lookback=2)
    bars = []
    for i in range(6):
        bars.append(
            {
                "symbol": "AAPL",
                "asof_ms": _ms(i),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0 + i,
                "vwap": 200.0 + i,
                "volume": 10.0,
            }
        )
    out = SessionFeatureRows("features", {}).run(
        None,
        {"records": bars, "spec": spec},
    )
    tape = out["tape"][0]
    assert tape["price_field"] == "vwap"
    assert float(tape["close"][0]) == 200.0


def test_session_features_refuse_a_price_field_the_store_lacks():
    """``mid`` is declared for the quote work and not yet acquired."""
    spec = _mini_spec(price_field="mid", period_ms=60_000, lookback=2)
    bars = [
        {
            "symbol": "AAPL",
            "asof_ms": _ms(i),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0 + i,
            "volume": 10.0,
        }
        for i in range(6)
    ]
    with pytest.raises(ValueError, match="mid"):
        SessionFeatureRows("features", {}).run(
            None,
            {"records": bars, "spec": spec},
        )


def _lattice_inputs(n=40):
    """One-minute rows and bars for three names."""
    bars, rows = [], []
    for symbol in ("AAPL", "JPM", "XOM"):
        px = 100.0
        for i in range(n):
            ret = 0.001 * ((i % 7) - 3)
            px *= math.exp(ret)
            bars.append({"symbol": symbol, "asof_ms": _ms(i), "close": px})
            rows.append(
                {
                    "symbol": symbol,
                    "asof_ms": _ms(i),
                    "ret_lag_0": ret,
                    "close": px,
                }
            )
    return bars, rows


def _lattice_spec():
    spec = _mini_spec()
    spec["features"] = ["ret_lag_0"]
    spec["period_ms"] = 60_000
    spec["horizon"] = {
        "lead_start": 1,
        "lead_step": 1,
        "lead_stop": 2,
        "anchors": [1],
        "top_k": 1,
        "se_mult": 2.0,
        "band_leads": 1,
    }
    return spec


def test_the_scoring_lattice_thins_validation_and_not_training():
    """ADR-0065: rows are FORMED at the spacing and JUDGED on the lattice."""
    bars, rows = _lattice_inputs()
    cuts = {
        "split": "val",
        "train_end_ms": _ms(24),
        "val_start_ms": _ms(26),
        "val_end_ms": _ms(39),
    }
    plain = NoInformationScan("scan", dict(cuts)).run(
        None,
        {"records": rows, "bars": bars, "spec": _lattice_spec()},
    )
    latticed = NoInformationScan(
        "scan",
        dict(cuts, score_period_ms=300_000),
    ).run(None, {"records": rows, "bars": bars, "spec": _lattice_spec()})
    # Same fit, a fifth of the scored rows.
    assert latticed["metrics"]["n_train"] == plain["metrics"]["n_train"]
    assert 0 < latticed["metrics"]["n_val"] < plain["metrics"]["n_val"]
    # And the overlap correction follows the lattice, not the spacing.
    lags = {row["lead"]: row["lags"] for row in latticed["records"]}
    assert lags[1] == 0.0 and lags[2] == 0.0


def test_a_lattice_the_row_spacing_cannot_reach_refuses():
    """A lattice off the row grid catches nothing; say so, do not score 0."""
    bars, rows = _lattice_inputs()
    spec = _lattice_spec()
    spec["period_ms"] = 300_000
    with pytest.raises(ValueError, match="whole multiple"):
        NoInformationScan(
            "scan",
            {
                "split": "val",
                "train_end_ms": _ms(24),
                "val_start_ms": _ms(26),
                "val_end_ms": _ms(39),
                "score_period_ms": 60_000,
            },
        ).run(None, {"records": rows, "bars": bars, "spec": spec})


def test_bars_from_store_start_ms_is_part_of_the_identity():
    """ADR-0066: the study start is declared where the data is read."""
    assert (
        BarsFromStore.validate_params(
            {
                "root": "./ob",
                "source": "alpaca-sip-split",
                "universe": UNIVERSE_PATH,
                "start_ms": 1514764800000,
            }
        )
        == []
    )
    assert BarsFromStore.validate_params(
        {
            "root": "./ob",
            "source": "alpaca-sip-split",
            "universe": UNIVERSE_PATH,
            "start_ms": -1,
        }
    )


def test_the_bars_node_reads_only_its_universe_and_never_copies_the_tape(
    tmp_path,
):
    """ADR-0073. The store here holds four names and eight sessions'
    worth of minutes; the universe declares two names. The node must
    read the two, drop the rest AT THE READ, and hand its caller the
    very list the reader built — a second dict per record was a measured
    409 bytes each, 5.4 GB on the study window."""
    import intraday_equities.nodes as nodes

    root = str(tmp_path / "ob")
    _write_store(
        root,
        "alpaca-sip",
        n_minutes=6,
        symbols=("AAPL", "SPY", "QQQ", "XLE"),
    )
    universe_path = _write_universe(str(tmp_path / "universe.json"))
    params = {"root": root, "source": "alpaca-sip", "universe": universe_path}

    seen = {}
    real_scan = nodes.scan_stream

    def _spy(*args, **kwargs):
        records = real_scan(*args, **kwargs)
        seen["bounds"] = kwargs
        seen["list"] = records
        return records

    nodes.BarsFromStore._cached_key = None
    nodes.BarsFromStore._cached_snap = None
    nodes.BarsFromStore._cached_fingerprint = None
    nodes.scan_stream = _spy
    try:
        rows = nodes.BarsFromStore("bars", params).run(
            _ctx(tmp_path),
            {},
        )["records"]
    finally:
        nodes.scan_stream = real_scan

    # The cohort went INTO the read, so the two names nobody scores were
    # never records at all.
    assert seen["bounds"]["keep_values"] == {"symbol": ("AAPL", "SPY")}
    assert {row["symbol"] for row in rows} == {"AAPL", "SPY"}
    assert len(rows) == 12
    # The emitted list IS the scanned list: no full-tape copy.
    assert rows is seen["list"]
    # And the session tag the node owes still rides on every record.
    assert all(row["session"] == "rth" for row in rows)


def test_the_bars_node_bounds_its_read_by_start_ms_and_by_session(tmp_path):
    """ADR-0073: both bounds reach the reader, and the sessions bound
    drops its minutes before they are ever allocated."""
    import intraday_equities.nodes as nodes

    root = str(tmp_path / "ob")
    _write_store(root, "alpaca-sip", n_minutes=6, symbols=("AAPL", "SPY"))
    universe_path = _write_universe(str(tmp_path / "universe.json"))
    base = {"root": root, "source": "alpaca-sip", "universe": universe_path}

    def _run(**extra):
        nodes.BarsFromStore._cached_key = None
        nodes.BarsFromStore._cached_snap = None
        nodes.BarsFromStore._cached_fingerprint = None
        return nodes.BarsFromStore("bars", {**base, **extra}).run(
            _ctx(tmp_path),
            {},
        )["records"]

    whole = _run()
    cut = _ms(3)
    bounded = _run(start_ms=cut)
    assert [r["asof_ms"] for r in bounded] == [
        r["asof_ms"] for r in whole if r["asof_ms"] >= cut
    ]

    # Every minute the fixture writes is inside regular hours, so an
    # rth bound keeps them all and a closed bound keeps none — the
    # bound is real either way, and it is applied at the read.
    assert len(_run(sessions=["rth"])) == len(whole)
    assert _run(sessions=["closed"]) == []


def test_sessions_is_refused_unless_it_names_real_buckets():
    from intraday_equities.nodes import BarsFromStore as Bars

    base = {"root": "ob", "source": "alpaca-sip", "universe": UNIVERSE_PATH}
    assert Bars.validate_params({**base, "sessions": ["rth", "eth"]}) == []
    problems = Bars.validate_params({**base, "sessions": ["regular"]})
    assert any("sessions must be a non-empty list" in p for p in problems)
    problems = Bars.validate_params({**base, "sessions": []})
    assert any("sessions must be a non-empty list" in p for p in problems)


def test_one_design_matrix_is_built_once_per_name(tmp_path):
    """ADR-0073: the scan node's slice takes the lockbox cut and the
    finite-row test in ONE index, and the symbol code is stacked in
    place. The values must be exactly what three passes produced."""
    import numpy as np

    from intraday_equities.nodes import (
        _attach_symbol_codes,
        _frame_matrix,
    )

    names = ["a", "b", "c"]
    stamps = np.arange(6, dtype=np.int64) * 60_000
    x = np.arange(18, dtype=np.float64).reshape(6, 3)
    x[2, 0] = np.nan  # unusable: column 'a' IS wanted below
    x[3, 1] = np.nan  # harmless: column 'b' is not
    frame = {"symbol": "AAPL", "asof_ms": stamps, "names": names, "X": x}
    val_end = int(stamps[4])

    kept, matrix = _frame_matrix(frame, ["c", "a"], val_end=val_end)
    # Rows 0,1,3,4: row 2 is non-finite in a wanted column and row 5 is
    # past the cut. Row 3's NaN sits in a column nobody asked for and
    # costs nothing, exactly as when the two selections ran in turn.
    assert kept.tolist() == [0, 60_000, 180_000, 240_000]
    assert matrix.tolist() == [[2.0, 0.0], [5.0, 3.0], [11.0, 9.0], [14.0, 12.0]]

    prepared = [("AAPL", kept, matrix, None, None, None, None)]
    same = _attach_symbol_codes(prepared, {"AAPL": 7})
    assert same is prepared, "the list is rewritten, not rebuilt"
    coded = prepared[0][2]
    assert coded[:, :-1].tolist() == matrix.tolist()
    assert coded[:, -1].tolist() == [7.0] * 4



def test_direct_heads_share_max_horizon_origins_and_emit_scale_calibration():
    spec = _mini_spec()
    spec["features"] = ["ret_lag_0"]
    spec["period_ms"] = 60_000
    spec["horizon"] = {
        "lead_start": 1,
        "lead_step": 1,
        "lead_stop": 3,
        "anchors": [1],
        "top_k": 1,
        "se_mult": 2.0,
        "band_leads": 1,
    }
    bars, rows = [], []
    price = 100.0
    for index in range(80):
        ret = 0.001 * ((index % 9) - 4)
        price *= math.exp(ret)
        bars.append({"symbol": "JPM", "asof_ms": _ms(index), "close": price})
        rows.append(
            {
                "symbol": "JPM",
                "asof_ms": _ms(index),
                "ret_lag_0": ret,
                "close": price,
            }
        )
    inputs = {"records": rows, "bars": bars, "spec": spec}
    base = {
        "split": "val",
        "train_end_ms": _ms(49),
        "val_start_ms": _ms(51),
        "val_end_ms": _ms(79),
        "common_lead_stop": 3,
        "common_origin_policy": "all_head_labels_finite",
        "estimator": "sklearn.linear_model.Ridge",
        "estimator_params": {"alpha": 1.0},
    }
    results = []
    for lead in (1, 3):
        params = {
            **base,
            "lead_start": lead,
            "lead_step": lead,
            "lead_stop": lead,
        }
        results.append(NoInformationScan(f"h{lead}", params).run(None, inputs))
    assert results[0]["records"][0]["n"] == results[1]["records"][0]["n"]
    for result in results:
        row = result["records"][0]
        assert row["train_scale"] > 0.0
        assert math.isfinite(row["train_scaled_improvement"])
        assert math.isfinite(result["metrics"]["val_calibration_slope"])


def test_direct_heads_share_origins_when_a_reference_bar_is_missing():
    spec = _mini_spec()
    spec["features"] = ["ret_lag_0"]
    spec["period_ms"] = 60_000
    spec["horizon"] = {
        "lead_start": 1,
        "lead_step": 1,
        "lead_stop": 3,
        "anchors": [1],
        "top_k": 1,
        "se_mult": 2.0,
        "band_leads": 1,
    }
    bars, rows = [], []
    own_price = ref_price = 100.0
    for index in range(80):
        own_ret = 0.001 * ((index % 9) - 4)
        ref_ret = 0.0005 * ((index % 7) - 3)
        own_price *= math.exp(own_ret)
        ref_price *= math.exp(ref_ret)
        bars.append({"symbol": "JPM", "asof_ms": _ms(index), "close": own_price})
        if index != 72:
            bars.append({"symbol": "SPY", "asof_ms": _ms(index), "close": ref_price})
        rows.append(
            {
                "symbol": "JPM",
                "asof_ms": _ms(index),
                "ret_lag_0": own_ret,
                "close": own_price,
            }
        )
    inputs = {"records": rows, "bars": bars, "spec": spec}
    base = {
        "split": "val",
        "train_end_ms": _ms(49),
        "val_start_ms": _ms(51),
        "val_end_ms": _ms(79),
        "common_lead_stop": 3,
        "common_origin_policy": "all_head_labels_finite",
        "label_residual": "SPY",
        "label_residual_self": "raw",
        "beta_window_minutes": 5,
        "estimator": "sklearn.linear_model.Ridge",
        "estimator_params": {"alpha": 1.0},
    }
    heads = []
    for lead in (1, 2, 3):
        params = {
            **base,
            "lead_start": lead,
            "lead_step": lead,
            "lead_stop": lead,
        }
        heads.append(NoInformationScan(f"h{lead}", params).run(None, inputs))
    scored = [head["records"][0] for head in heads]
    assert len({row["n"] for row in scored}) == 1
    assert len({row["origin_sha256"] for row in scored}) == 1


def test_common_lead_stop_fails_closed_when_shorter_than_head():
    base = {
        "split": "val",
        "train_end_ms": 1,
        "val_start_ms": 2,
        "val_end_ms": 3,
        "lead_start": 3,
        "lead_step": 3,
        "lead_stop": 3,
        "common_lead_stop": 2,
    }
    assert any(
        "common_lead_stop" in problem
        for problem in NoInformationScan.validate_params(base)
    )
