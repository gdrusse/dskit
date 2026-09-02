"""Child kinds under the toolkit conformance bar, plus a store e2e."""

import importlib.util
import json
import math
import os
from datetime import datetime, timedelta, timezone
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
            move=lambda: _write_universe(
                universe_path, _mini_spec(notes="moved")
            ),
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
    assert WindowRows.validate_params(
        {"lookback": 2, "label_lead": 1, "n_ahead": 4}
    ) == []


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


def _ny_ms(year, month, day, hour, minute):
    from zoneinfo import ZoneInfo

    stamp = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York"))
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
        row for row in out
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
        {"lead": lead, "ic_val": 0.01, "n_val": 400.0}
        for lead in (5, 390, 780, 1170)
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
    bars = [
        {"symbol": "AAPL", "asof_ms": _ms(i), "close": 100.0 + i}
        for i in range(8)
    ]
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
    bars = [
        {"symbol": "AAPL", "asof_ms": _ms(i), "close": 100.0 + i}
        for i in range(8)
    ]
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
            rows.append({
                "symbol": symbol,
                "asof_ms": _ms(i),
                "ret_lag_0": ret,
                "close": px,
            })
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


def test_lead_labels_drop_rows_whose_label_lands_after_the_cut():
    spec = _mini_spec()
    spec["features"] = ["ret_lag_0"]
    bars = [
        {"symbol": "AAPL", "asof_ms": _ms(i), "close": 100.0 + i}
        for i in range(8)
    ]
    rows = [
        {"symbol": "AAPL", "asof_ms": _ms(i), "ret_lag_0": 0.01 * i}
        for i in range(8)
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
        "ret_lag_0", "ret_lag_1", "ret_lag_2", "ret_lag_3", "tod_sin",
    ]
    spec["scan"] = {
        "l_start": 2,
        "l_step": 1,
        "lookback_stop": 4,
        "keep_frac": 0.95,
        "keep_tau": 0.05,
    }
    bars = [
        {"symbol": "AAPL", "asof_ms": _ms(i), "close": 100.0 + i}
        for i in range(16)
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
    frames = SessionFeatureRows("features", {"layout": "columns"}).run(
        None, {"records": rows, "spec": spec}
    )
    assert "tape" in frames
    assert "X" in frames["records"][0]
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
    ).run(None, {
        "records": kept, "bars": frames["tape"], "spec": spec,
    })
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
    ).run(None, {
        "records": kept, "bars": frames["tape"], "spec": spec,
    })["records"]
    assert train
    assert "X" not in train[0]
    assert "y_next" in train[0]
    assert train[0]["symbol"] == "AAPL"

