"""Clock and entry/exit contracts for the tech replay."""
import pytest


from statistical_arbitrage.backtest import VolatilityArbitrageBacktester


def test_signal_fills_next_session_and_exits_after_crossing():
    days = ["2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07"]
    rows = [
        {"date": days[0], "symbol_a": "AAPL", "symbol_b": "AMZN", "z": 2.2, "status": "ok"},
        {"date": days[1], "symbol_a": "AAPL", "symbol_b": "AMZN", "z": 1.1, "status": "ok"},
        {"date": days[2], "symbol_a": "AAPL", "symbol_b": "AMZN", "z": -0.1, "status": "ok"},
    ]
    bt = VolatilityArbitrageBacktester(entry_z=2, stop_z=4, max_hold_sessions=20)
    got = bt.intents(rows, days)
    assert len(got) == 1
    assert got[0]["decision_date"] == days[0]
    assert got[0]["entry_date"] == days[1]
    assert got[0]["exit_date"] == days[3]
    assert got[0]["short_symbol"] == "AAPL"
    assert got[0]["long_symbol"] == "AMZN"


def test_no_future_signal_can_change_past_intent():
    days = ["2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07"]
    first = {"date": days[0], "symbol_a": "AAPL", "symbol_b": "AMZN", "z": 2.2, "status": "ok"}
    bt = VolatilityArbitrageBacktester(entry_z=2, stop_z=4, max_hold_sessions=20)
    a = bt.intents([first], days)
    b = bt.intents([first, {"date": days[2], "symbol_a": "AAPL", "symbol_b": "AMZN",
                            "z": -0.1, "status": "ok"}], days)
    assert a[0]["entry_date"] == b[0]["entry_date"] == days[1]


def test_pair_orders_match_expiry_and_vega_by_integer_contracts():
    intent = {"symbol_a": "AAPL", "symbol_b": "AMZN",
              "short_symbol": "AAPL", "long_symbol": "AMZN",
              "decision_date": "2021-01-04", "entry_date": "2021-01-05",
              "exit_date": "2021-01-15", "exit_reason": "mean_cross", "entry_z": 2.2}
    surface = [
        {"date": "2021-01-05", "symbol": "AAPL", "status": "ok", "target_dte": 30,
         "expiration": "2021-02-05", "atm_strike": 100.0, "atm_iv": 0.3,
         "forward": 100.0, "dte": 31},
        {"date": "2021-01-05", "symbol": "AMZN", "status": "ok", "target_dte": 30,
         "expiration": "2021-02-05", "atm_strike": 200.0, "atm_iv": 0.3,
         "forward": 200.0, "dte": 31},
    ]
    bt = VolatilityArbitrageBacktester(entry_z=2, stop_z=4, max_hold_sessions=20)
    out = bt.pair_orders([intent], surface, max_contracts=5, max_vega_mismatch=0.05)
    assert out["summary"]["accepted"] == 1
    pair = out["pairs"][0]
    assert pair["short_contracts"] == 2 * pair["long_contracts"]
    assert pair["short_order"]["date"] == "2021-01-05"
    assert pair["long_order"]["exit"] == "2021-01-15"


def test_pair_orders_search_feasible_quantities_before_minimizing_mismatch():
    intent = {"symbol_a": "AAPL", "symbol_b": "AMZN",
              "short_symbol": "AAPL", "long_symbol": "AMZN",
              "decision_date": "2021-01-04", "entry_date": "2021-01-05",
              "exit_date": "2021-01-15", "exit_reason": "mean_cross", "entry_z": 2.2}
    surface = [
        {"date": "2021-01-05", "symbol": symbol, "status": "ok",
         "expiration": "2021-02-05", "atm_strike": strike,
         "atm_iv": 0.3, "forward": strike, "dte": 31}
        for symbol, strike in (("AAPL", 100.0), ("AMZN", 200.0))
    ]
    bt = VolatilityArbitrageBacktester(entry_z=2, stop_z=4, max_hold_sessions=20)
    out = bt.pair_orders([intent], surface, max_contracts=5,
                         max_vega_mismatch=0.5,
                         max_gross_notional_usd=30_000)
    assert out["summary"]["accepted"] == 1
    assert (out["pairs"][0]["short_contracts"], out["pairs"][0]["long_contracts"]) == (1, 1)
    assert out["pairs"][0]["gross_underlying_notional_usd"] == 30_000


def test_pair_orders_refuse_unmatched_expiry_atomically():
    intent = {"symbol_a": "AAPL", "symbol_b": "AMZN",
              "short_symbol": "AAPL", "long_symbol": "AMZN",
              "decision_date": "2021-01-04", "entry_date": "2021-01-05",
              "exit_date": "2021-01-15", "exit_reason": "mean_cross", "entry_z": 2.2}
    surface = [
        {"date": "2021-01-05", "symbol": "AAPL", "status": "ok", "target_dte": 30,
         "expiration": "2021-02-05", "atm_strike": 100.0, "atm_iv": 0.3,
         "forward": 100.0, "dte": 31},
        {"date": "2021-01-05", "symbol": "AMZN", "status": "ok", "target_dte": 30,
         "expiration": "2021-02-12", "atm_strike": 200.0, "atm_iv": 0.3,
         "forward": 200.0, "dte": 38},
    ]
    bt = VolatilityArbitrageBacktester(entry_z=2, stop_z=4, max_hold_sessions=20)
    out = bt.pair_orders([intent], surface, max_contracts=5, max_vega_mismatch=0.05)
    assert out["pairs"] == []
    assert out["summary"]["refused"]["expiry_mismatch"] == 1


def test_performance_reports_net_equity_and_matched_benchmarks():
    from statistical_arbitrage.report import PerformanceReporter

    dates = ["2021-01-04", "2021-01-05", "2021-01-06"]
    model = [0.0, 100.0, -50.0]
    baseline = [0.0, 50.0, 0.0]
    prices = {
        "AAPL": [100.0, 102.0, 101.0],
        "AMZN": [100.0, 102.0, 101.0],
        "GOOGL": [100.0, 102.0, 101.0],
        "QQQ": [100.0, 101.0, 100.0],
    }
    report = PerformanceReporter(capital_usd=10_000.0)
    got = report.evaluate(
        dates=dates, model_pnl=model, baseline_pnl=baseline,
        adjusted_prices=prices,
        model_trades=[{"pnl_usd": 50.0, "entry_date": dates[0],
                       "exit_date": dates[2]}],
        baseline_trades=[],
    )
    assert got["model"]["total_return"] == pytest.approx(0.005)
    assert got["baseline"]["total_return"] == pytest.approx(0.005)
    assert got["equal_weight"]["total_return"] == pytest.approx(0.01)
    assert got["qqq"]["total_return"] == pytest.approx(0.0)
    assert got["model"]["win_rate"] == 1.0
    assert got["model"]["average_trade_duration_sessions"] == 2.0


def test_fold_benchmark_includes_first_session_return_from_prior_close():
    """A calendar slice must not erase the market's first trading return."""
    from statistical_arbitrage.report import PerformanceReporter

    got = PerformanceReporter(capital_usd=100.0).evaluate(
        dates=["2021-01-04", "2021-01-05"], model_pnl=[0.0, 0.0],
        baseline_pnl=[0.0, 0.0],
        adjusted_prices={"AAPL": [110.0, 110.0], "QQQ": [105.0, 105.0]},
        prior_adjusted_prices={"AAPL": 100.0, "QQQ": 100.0},
        model_trades=[], baseline_trades=[],
    )
    assert got["equal_weight"]["total_return"] == pytest.approx(0.10)
    assert got["qqq"]["total_return"] == pytest.approx(0.05)


def test_fold_trades_use_close_date_and_global_duration():
    """A boundary-spanning trade belongs to its closing fold once."""
    from statistical_arbitrage.backtest import TechReplayPipeline
    from statistical_arbitrage.report import PerformanceReporter

    sessions = ["2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07"]
    trades = [
        {"entry_date": sessions[0], "exit_date": sessions[2], "pnl_usd": 10.0},
        {"entry_date": sessions[2], "exit_date": sessions[3], "pnl_usd": -5.0},
    ]
    selected = TechReplayPipeline._closed_in_window(trades, sessions[2], sessions[3])
    assert selected == trades[:1]
    assert TechReplayPipeline._closed_in_window(trades, sessions[0], sessions[2]) == []
    report = PerformanceReporter(capital_usd=100.0).evaluate(
        dates=[sessions[2]], model_pnl=[10.0], baseline_pnl=[0.0],
        adjusted_prices={"AAPL": [100.0], "QQQ": [100.0]},
        model_trades=selected, baseline_trades=[],
        trade_date_index={day: i for i, day in enumerate(sessions)},
    )
    assert report["model"]["trade_count"] == 1
    assert report["model"]["average_trade_duration_sessions"] == 2.0
    assert report["trade_cohort_policy"] == "close_date"


def test_report_rejects_trade_outside_declared_close_cohort():
    from statistical_arbitrage.report import PerformanceReporter

    with pytest.raises(ValueError, match="close date"):
        PerformanceReporter(capital_usd=100.0).evaluate(
            dates=["2021-01-06"], model_pnl=[0.0], baseline_pnl=[0.0],
            adjusted_prices={"AAPL": [100.0], "QQQ": [100.0]},
            model_trades=[{"entry_date": "2021-01-05",
                           "exit_date": "2021-01-07", "pnl_usd": 1.0}],
            baseline_trades=[],
            trade_date_index={"2021-01-05": 0, "2021-01-06": 1,
                              "2021-01-07": 2},
        )


def test_pair_orders_refuse_corporate_event_during_hold():
    intent = {"symbol_a": "AAPL", "symbol_b": "AMZN",
              "short_symbol": "AAPL", "long_symbol": "AMZN",
              "decision_date": "2021-01-04", "entry_date": "2021-01-05",
              "exit_date": "2021-01-15", "exit_reason": "mean_cross", "entry_z": 2.2}
    surface = [
        {"date": "2021-01-05", "symbol": symbol, "status": "ok", "target_dte": 30,
         "expiration": "2021-02-05", "atm_strike": strike, "atm_iv": 0.3,
         "forward": strike, "dte": 31}
        for symbol, strike in [("AAPL", 100.0), ("AMZN", 200.0)]
    ]
    bt = VolatilityArbitrageBacktester(entry_z=2, stop_z=4, max_hold_sessions=20)
    out = bt.pair_orders(
        [intent], surface, max_contracts=5, max_vega_mismatch=0.05,
        corporate_events=[{"date": "2021-01-11", "symbol": "AAPL",
                           "dividend_amount": 0.2, "split_coefficient": 1.0}],
    )
    assert out["pairs"] == []
    assert out["summary"]["refused"]["corporate_event"] == 1


def test_pair_orders_use_last_rate_strictly_before_entry():
    intent = {"symbol_a": "AAPL", "symbol_b": "AMZN",
              "short_symbol": "AAPL", "long_symbol": "AMZN",
              "decision_date": "2021-01-04", "entry_date": "2021-01-05",
              "exit_date": "2021-01-15", "exit_reason": "mean_cross", "entry_z": 2.2}
    surface = [
        {"date": "2021-01-05", "symbol": symbol, "status": "ok", "target_dte": 30,
         "expiration": "2021-02-05", "atm_strike": strike, "atm_iv": 0.3,
         "forward": strike, "dte": 31}
        for symbol, strike in [("AAPL", 100.0), ("AMZN", 200.0)]
    ]
    rates = [
        {"observation_date": "2021-01-04", "DGS3MO": "0.10"},
        {"observation_date": "2021-01-05", "DGS3MO": "9.00"},
    ]
    bt = VolatilityArbitrageBacktester(entry_z=2, stop_z=4, max_hold_sessions=20)
    out = bt.pair_orders(
        [intent], surface, max_contracts=5, max_vega_mismatch=0.05,
        rates=rates, require_rate=True,
    )
    assert out["pairs"][0]["hedge_rate"] == pytest.approx(0.001)


def test_pair_orders_refuse_cross_year_and_wide_quotes():
    surface = [
        {"date": "2021-12-21", "symbol": symbol, "status": "ok", "target_dte": 30,
         "expiration": "2022-01-21", "atm_strike": strike, "atm_iv": 0.3,
         "forward": strike, "dte": 31, "half_spread_over_premium": width}
        for symbol, strike, width in [("AAPL", 100.0, 0.02), ("AMZN", 200.0, 0.25)]
    ]
    intent = {"symbol_a": "AAPL", "symbol_b": "AMZN",
              "short_symbol": "AAPL", "long_symbol": "AMZN",
              "decision_date": "2021-12-20", "entry_date": "2021-12-21",
              "exit_date": "2022-01-03", "exit_reason": "mean_cross", "entry_z": 2.2}
    bt = VolatilityArbitrageBacktester(entry_z=2, stop_z=4, max_hold_sessions=20)
    out = bt.pair_orders([intent], surface, max_contracts=5,
                         max_vega_mismatch=0.05,
                         max_half_spread_over_premium=0.10)
    assert out["summary"]["refused"]["cross_year"] == 1
    intent["exit_date"] = "2021-12-29"
    out = bt.pair_orders([intent], surface, max_contracts=5,
                         max_vega_mismatch=0.05,
                         max_half_spread_over_premium=0.10)
    assert out["summary"]["refused"]["wide_quote"] == 1


def test_daily_pnl_differences_pair_running_marks_once():
    from statistical_arbitrage.report import PerformanceReporter

    marks = [
        {"id": "p1", "date": "2021-01-05", "pnl_usd": -2.0},
        {"id": "p1", "date": "2021-01-06", "pnl_usd": 8.0},
        {"id": "p2", "date": "2021-01-06", "pnl_usd": -3.0},
        {"id": "p1", "date": "2021-01-07", "pnl_usd": 5.0},
        {"id": "p2", "date": "2021-01-07", "pnl_usd": 7.0},
    ]
    got = PerformanceReporter.daily_pnl_from_marks(
        marks, ["2021-01-05", "2021-01-06", "2021-01-07"]
    )
    assert got == [-2.0, 7.0, 7.0]


def test_replay_refuses_drifted_structural_rows_before_sources(tmp_path, monkeypatch):
    """A stale pin must not score corrected graph output."""
    import hashlib
    import json
    from statistical_arbitrage.backtest import TechReplayPipeline

    rows = tmp_path / "signal.jsonl"
    rows.write_text("{}\n", encoding="utf-8")
    structural = tmp_path / "structural.json"
    structural.write_text("{}", encoding="utf-8")
    cfg = tmp_path / "backtest.json"
    names = TechReplayPipeline._ROWS
    cfg.write_text(json.dumps({
        "structural_config": {"path": str(structural),
                              "sha256": hashlib.sha256(b"{}").hexdigest()},
        "structural_rows": {name: {
            "path": str(rows), "sha256": "0" * 64} for name in names},
        "output_dir": str(tmp_path / "out"),
    }), encoding="utf-8")
    monkeypatch.setattr(TechReplayPipeline, "_load_inputs",
                        lambda self, structural: pytest.fail("source read before pin check"))
    with pytest.raises(ValueError, match="sha256"):
        TechReplayPipeline(cfg).run()
    assert not (tmp_path / "out").exists()


def test_replay_refuses_altered_calendar_before_sources(tmp_path, monkeypatch):
    """Reporting windows are part of the immutable replay identity."""
    import hashlib
    import json
    from statistical_arbitrage.backtest import TechReplayPipeline

    def pin(path):
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    structural = tmp_path / "structural.json"
    structural.write_text("{}", encoding="utf-8")
    rows = tmp_path / "rows.jsonl"
    rows.write_text("{}\n", encoding="utf-8")
    calendar = tmp_path / "calendar.json"
    calendar.write_text("{}", encoding="utf-8")
    stock = tmp_path / "stock.json"
    stock.write_text("{}", encoding="utf-8")
    theta = tmp_path / "theta.json"
    theta.write_text("{}", encoding="utf-8")
    resolved = tmp_path / "resolved.json"
    resolved.write_text("{\"data_fingerprint\": {}}", encoding="utf-8")
    config = tmp_path / "backtest.json"
    config.write_text(json.dumps({
        "structural_config": pin(structural),
        "structural_rows": {name: pin(rows) for name in TechReplayPipeline._ROWS},
        "source_configs": {"stock": pin(stock), "theta": pin(theta)},
        "structural_resolved": pin(resolved),
        "validation_calendar": pin(calendar),
    }), encoding="utf-8")
    calendar.write_text("{\"blocks\": []}", encoding="utf-8")
    monkeypatch.setattr(TechReplayPipeline, "_load_inputs",
                        lambda self, structural: pytest.fail("sources read before pin check"))
    with pytest.raises(ValueError, match="sha256"):
        TechReplayPipeline(config).run()


def test_replay_refuses_changed_manifest_plan_before_reader_run():
    """A changed source fingerprint cannot be replayed under saved results."""
    from statistical_arbitrage.backtest import TechReplayPipeline

    class ChangedReader:
        def fingerprint(self):
            return {"kind": "ParquetRows", "sha256": "new"}

        def run(self, ctx, inputs):
            pytest.fail("drifted source was read")

    with pytest.raises(ValueError, match="source fingerprint"):
        TechReplayPipeline._verified_reader(
            "prices_aapl", ChangedReader(),
            {"prices_aapl": {"kind": "ParquetRows", "sha256": "old"}})


@pytest.mark.parametrize("drifted", ["prices_aapl", "rates_raw", "chain"])
def test_replay_source_reader_plan_check_uses_saved_resolved_identity(
    tmp_path, monkeypatch, drifted,
):
    """Every reader refuses changed plan identity before executing that reader."""
    import json
    from pathlib import Path
    from statistical_arbitrage.backtest import TechReplayPipeline
    import dskit.pipeline.libs.parquet as parquet_module
    import dskit.pipeline.libs.observations as observations_module
    import dskit.pipeline.libs.chain_parquet as chain_module

    seen = []

    class Reader:
        def __init__(self, key, params):
            self.key = key
            self.members = params.get("members")

        def fingerprint(self):
            return ({"key": self.key, "members": self.members}
                    if self.members is not None else {"key": self.key})

        def run(self, ctx, inputs):
            seen.append(self.key)
            return {"records": []}

    monkeypatch.setattr(parquet_module, "ParquetRows", Reader)
    monkeypatch.setattr(observations_module, "ObservationRows", Reader)
    monkeypatch.setattr(chain_module, "ParquetChainRows", Reader)
    config_path = tmp_path / "backtest.json"
    config_path.write_text(json.dumps({"evaluation": {"end_before": "2021-01-08"},
        "execution": {"max_rate_age_days": 5}}), encoding="utf-8")
    runner = TechReplayPipeline(config_path)
    stock = {"onboarding_root": "/ob", "source": "stocks", "stream": "files",
             "symbols": ["AAPL", "AMZN"],
             "required_columns": ["symbol", "date", "adjusted_close",
                                  "split_coefficient", "dividend_amount"],
             "factor": {"symbol": "QQQ", "source": "factor", "stream": "files",
                        "relpath": "qqq/prices.parquet"}}
    theta = {"symbols": ["AAPL", "AMZN"], "start": "2016-01-01",
             "end_before": "2026-01-01",
             "parquet_export": {"directories": ["/theta"],
                                "sealed_from": "2026-01-01"},
             "rate": {"date_field": "observation_date", "value_field": "DGS3MO",
                      "scale": 0.01, "strictly_prior": True, "max_age_days": 5,
                      "onboarding_root": "/ob", "source": "rates",
                      "stream": "dgs3mo"}}
    runner._source_documents = {"stock": (Path(config_path), stock),
                                "theta": (Path(config_path), theta)}
    names = {"prices_aapl": "replay_price_aapl",
             "prices_amzn": "replay_price_amzn",
             "prices_qqq": "replay_price_qqq",
             "rates_raw": "replay_rates", "chain": "replay_chain"}
    runner._resolved_fingerprints = {
        key: {"key": value if key != drifted else "prior-plan",
              **({"members": ["old.parquet"]} if key == "chain" else {})}
        for key, value in names.items()
    }
    def price(port, symbol):
        source = "factor" if symbol == "QQQ" else "stocks"
        relpath = "qqq/prices.parquet" if symbol == "QQQ" else f"{port}/prices.parquet"
        return {"uses": "dskit.pipeline.libs.parquet:ParquetRows", "params": {
            "root": "/ob", "source": source, "stream": "files",
            "relpath_by_key": {symbol: relpath},
            "columns": {name: name for name in stock["required_columns"]},
            "read_bounds": {"date": {"end_before": "2021-01-08"}}}}
    structural = {"pipeline": {
        "returns": {"params": {"ports": {"aapl": "AAPL", "amzn": "AMZN",
                                         "qqq": "QQQ"}}},
        **{f"prices_{port}": price(port, symbol)
           for port, symbol in (("aapl", "AAPL"), ("amzn", "AMZN"),
                                ("qqq", "QQQ"))},
        "rates_raw": {"uses": "dskit.pipeline.libs.observations:ObservationRows",
                      "params": {"root": "/ob", "source": "rates", "stream": "dgs3mo"}},
        "chain": {"uses": "dskit.pipeline.libs.chain_parquet:ParquetChainRows",
                  "params": {"symbols": ["AAPL", "AMZN"], "directories": ["/theta"],
                             "start": "2018-01-01", "end_before": "2021-01-08",
                             "sealed_from": "2026-01-01"}},
    }}
    with pytest.raises(ValueError, match=f"source fingerprint mismatch for {drifted}"):
        runner._load_inputs(structural)
    assert names[drifted] not in seen


def test_replay_chain_uses_resolved_members_despite_extra_and_refuses_missing(
    tmp_path, monkeypatch,
):
    """The child streams the original source vintage even after another root lands."""
    import datetime as dt
    import json
    from pathlib import Path
    import pyarrow as pa
    import pyarrow.parquet as pq
    import dskit.pipeline.libs.parquet as parquet_module
    import dskit.pipeline.libs.observations as observations_module
    from dskit.pipeline.libs.chain_parquet import ParquetChainRows
    from statistical_arbitrage.backtest import TechReplayPipeline

    class EmptyReader:
        def __init__(self, key, params):
            self.key = key

        def fingerprint(self):
            return {"key": self.key}

        def run(self, ctx, inputs):
            return {"records": []}

    monkeypatch.setattr(parquet_module, "ParquetRows", EmptyReader)
    monkeypatch.setattr(observations_module, "ObservationRows", EmptyReader)
    directory = tmp_path / "theta"
    directory.mkdir()
    def snapshot(name, symbol):
        row = {"symbol": symbol, "date": dt.date(2021, 1, 4),
               "expiration": dt.date(2021, 2, 5), "strike": 100.0,
               "right": "call", "bid": 1.0, "ask": 1.2,
               "underlying_price": 100.0}
        table = pa.Table.from_pylist([row]).replace_schema_metadata({
            "dskit.export.windows": json.dumps({symbol: ["2021-01-04", "2021-01-04"]}),
            "dskit.export.manifest_sha256": "m" * 64,
            "dskit.export.snapshot_hash": "s" * 64,
        })
        path = directory / name
        pq.write_table(table, path)
        return path

    first = snapshot("20210105T000000Z-backfill-aaaaaaaa.parquet", "AAPL")
    chain_params = {"directories": [str(directory)],
                    "fields": ["symbol", "date", "expiration", "strike", "right",
                               "bid", "ask", "underlying_price"],
                    "symbol_field": "symbol", "date_field": "date",
                    "symbols": ["AAPL"], "start": "2020-01-01",
                    "end_before": "2025-12-16", "sealed_from": "2026-01-01"}
    pinned = ParquetChainRows("chain", chain_params).fingerprint()
    snapshot("20210106T000000Z-backfill-bbbbbbbb.parquet", "MGM")
    config = tmp_path / "backtest.json"
    config.write_text(json.dumps({"evaluation": {"end_before": "2025-12-16"},
                                  "execution": {"max_rate_age_days": 5}}), encoding="utf-8")
    runner = TechReplayPipeline(config)
    stock = {"onboarding_root": "/ob", "source": "stocks", "stream": "files",
             "symbols": ["AAPL"], "required_columns": ["symbol", "date",
             "adjusted_close", "split_coefficient", "dividend_amount"],
             "factor": {"symbol": "QQQ", "source": "factor", "stream": "files",
                        "relpath": "qqq/prices.parquet"}}
    theta = {"symbols": ["AAPL"], "start": "2016-01-01",
             "end_before": "2026-01-01",
             "parquet_export": {"directories": [str(directory)],
                                "sealed_from": "2026-01-01"},
             "rate": {"date_field": "observation_date", "value_field": "DGS3MO",
                      "scale": 0.01, "strictly_prior": True, "max_age_days": 5,
                      "onboarding_root": "/ob", "source": "rates",
                      "stream": "dgs3mo"}}
    runner._source_documents = {"stock": (Path(config), stock),
                                "theta": (Path(config), theta)}
    runner._resolved_fingerprints = {
        "prices_aapl": {"key": "replay_price_aapl"},
        "prices_qqq": {"key": "replay_price_qqq"},
        "rates_raw": {"key": "replay_rates"}, "chain": pinned,
    }
    def price(port, symbol):
        return {"uses": "dskit.pipeline.libs.parquet:ParquetRows", "params": {
            "root": "/ob", "source": "factor" if symbol == "QQQ" else "stocks",
            "stream": "files",
            "relpath_by_key": {symbol: "qqq/prices.parquet" if symbol == "QQQ"
                               else "aapl/prices.parquet"},
            "columns": {name: name for name in stock["required_columns"]},
            "read_bounds": {"date": {"end_before": "2025-12-16"}}}}
    structural = {"pipeline": {
        "returns": {"params": {"ports": {"aapl": "AAPL", "qqq": "QQQ"}}},
        "prices_aapl": price("aapl", "AAPL"), "prices_qqq": price("qqq", "QQQ"),
        "rates_raw": {"uses": "dskit.pipeline.libs.observations:ObservationRows",
                      "params": {"root": "/ob", "source": "rates", "stream": "dgs3mo"}},
        "chain": {"uses": "dskit.pipeline.libs.chain_parquet:ParquetChainRows",
                  "params": chain_params},
    }}
    _, _, chain = runner._load_inputs(structural)
    assert [row["symbol"] for row in chain] == ["AAPL"]
    assert runner._reader_fingerprints["chain"] == pinned
    first.unlink()
    with pytest.raises(ValueError, match="missing.*member"):
        runner._load_inputs(structural)


def test_replay_output_is_atomic_retryable_and_immutable(tmp_path, monkeypatch):
    """A failed plot cannot leave a completed-looking result directory."""
    import json
    from statistical_arbitrage.backtest import TechReplayPipeline
    from statistical_arbitrage.report import PerformanceReporter

    root = tmp_path / "out"
    config = tmp_path / "backtest.json"
    config.write_text(json.dumps({
        "name": "fixture", "output_dir": str(root),
        "evaluation": {"initial_capital_usd": 100.0, "sessions_per_year": 252},
        "variants": {"atm": {"network": "atm_network_rows.jsonl"},
                     "skew": {"network": "skew_network_rows.jsonl"}},
        "signal": {"entry_z": 2.0},
    }), encoding="utf-8")
    runner = TechReplayPipeline(config)
    provenance = {"backtest_config_sha256": runner._digest(config)}
    rows = {"atm_network_rows.jsonl": [], "skew_network_rows.jsonl": [],
            "graph_fits.jsonl": []}
    structural = {"pipeline": {"graph": {"params": {"symbols": ["AAPL", "AMZN"]}}}}
    def fail_plot(self, path, *args, **kwargs):
        (path / "partial.png").write_bytes(b"partial")
        raise RuntimeError("plot failed")
    monkeypatch.setattr(PerformanceReporter, "save_plots", fail_plot)
    with pytest.raises(RuntimeError, match="plot failed"):
        runner._write_output({"name": "fixture"}, rows, structural, provenance)
    assert not list(root.iterdir())
    monkeypatch.setattr(PerformanceReporter, "save_plots", lambda *args, **kwargs: [])
    result = {"name": "fixture"}
    target = runner._write_output(result, rows, structural, provenance)
    saved = (target / "evaluation.json").read_bytes()
    with pytest.raises(FileExistsError):
        runner._write_output({"name": "different"}, rows, structural, provenance)
    assert (target / "evaluation.json").read_bytes() == saved
    assert not any(path.name.startswith(".fixture-") for path in root.iterdir())


def test_replay_four_books_one_chain_pass_and_folds(tmp_path, monkeypatch):
    """A single ledger sees all four books and annual and subfold scores are written."""
    import hashlib
    import json
    from statistical_arbitrage.backtest import TechReplayPipeline

    class SinglePass:
        passes = 0

        def __iter__(self):
            self.passes += 1
            if self.passes > 1:
                raise AssertionError("chain reread")
            return iter([{"quote": "sentinel"}])

    class FakeLedger:
        calls = []

        def __init__(self, key, params):
            self.params = params

        def run(self, ctx, inputs):
            FakeLedger.calls.append(inputs["pairs"])
            list(inputs["chain"])
            assert len(inputs["pairs"]) == 4
            return {
                "marks": [{"id": p["id"], "date": "2021-01-06", "pnl_usd": 10.0}
                          for p in inputs["pairs"]],
                "trades": [{"id": p["id"], "open_date": "2021-01-05",
                            "close_date": "2021-01-07", "pnl_usd": 10.0}
                           for p in inputs["pairs"]],
                "exposures": [], "cashflows": [], "refusals": [],
                "summary": {"pairs_in": 4},
            }

    days = ["2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07"]
    signal = {"date": days[0], "symbol_a": "AAPL", "symbol_b": "AMZN",
              "z": 2.5, "status": "ok"}
    surface = [
        {"date": days[1], "symbol": symbol, "status": "ok",
         "expiration": "2021-02-05", "atm_strike": 100.0,
         "atm_iv": 0.3, "forward": 100.0, "dte": 31,
         "half_spread_over_premium": 0.01}
        for symbol in ("AAPL", "AMZN")
    ]
    pinned = {}
    for name in TechReplayPipeline._ROWS:
        path = tmp_path / name
        data = (surface if name == "surface_rows.jsonl" else
                [{"date": days[0], "fit_date": days[0], "refit": True,
                  "alpha": 0.1, "cv_score": -1.0, "train_log_likelihood": -0.5,
                  "n_iter": 2}] if name == "graph_fits.jsonl" else [signal])
        path.write_text("".join(json.dumps(row) + "\n" for row in data), encoding="utf-8")
        pinned[name] = {"path": str(path),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    structural = tmp_path / "structural.json"
    structural.write_text(json.dumps({"pipeline": {"graph": {"params": {
        "symbols": ["AAPL", "AMZN", "GOOGL"]}}, "returns": {"params": {
        "ports": {name.lower(): name for name in ("AAPL", "AMZN", "GOOGL", "QQQ")},
        "factor_port": "qqq", "end_before": "2021-01-08",
        "max_forward_fill_sessions": 0}}}}), encoding="utf-8")
    calendar = tmp_path / "calendar.json"
    calendar.write_text(json.dumps({"blocks": [{"block": 1,
        "origin_start": days[0], "origin_end_before": "2021-01-08"}]}), encoding="utf-8")
    stock = tmp_path / "stock.json"
    stock.write_text("{}", encoding="utf-8")
    theta = tmp_path / "theta.json"
    theta.write_text("{}", encoding="utf-8")
    resolved = tmp_path / "resolved.json"
    resolved.write_text('{"data_fingerprint": {}}', encoding="utf-8")
    def pin(path):
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    config = tmp_path / "backtest.json"
    config.write_text(json.dumps({
        "name": "fixture", "structural_config": {"path": str(structural),
            "sha256": hashlib.sha256(structural.read_bytes()).hexdigest()},
        "structural_rows": pinned,
        "source_configs": {"stock": pin(stock), "theta": pin(theta)},
        "structural_resolved": pin(resolved),
        "variants": {variant: {"network": f"{variant}_network_rows.jsonl",
                               "baseline": f"{variant}_baseline_rows.jsonl"}
                     for variant in ("atm", "skew")},
        "evaluation": {"start": days[0], "end_before": "2021-01-08",
                       "warmup_year": 2019, "development_year": 2021,
                       "validation_years": [2021], "initial_capital_usd": 10000.0,
                       "sessions_per_year": 252},
        "signal": {"entry_z": 2.0, "stop_z": 4.0,
                   "max_hold_sessions": 2, "one_active_pair_per_book": True},
        "execution": {"max_contracts_per_leg": 2,
                      "max_initial_vega_mismatch": 0.2,
                      "max_half_spread_over_premium": 0.2,
                      "max_gross_underlying_notional_usd": 100000.0,
                      "require_prior_rate": False, "max_rate_age_days": 5,
                      "refuse_corporate_events": True},
        "ledger": {"ledger_params": {}},
        "validation_calendar": pin(calendar),
        "output_dir": str(tmp_path / "out"),
    }), encoding="utf-8")
    chain = SinglePass()
    monkeypatch.setattr(TechReplayPipeline, "_load_inputs", lambda self, _: (
        {symbol: [{"symbol": symbol, "date": day, "adjusted_close": 100.0,
                   "split_coefficient": 1.0, "dividend_amount": 0.0}
                  for day in days]
         for symbol in ("AAPL", "AMZN", "GOOGL", "QQQ")}, [], chain))
    import statistical_arbitrage.backtest as module
    monkeypatch.setattr(module, "PairedOptionLedger", FakeLedger)
    result = TechReplayPipeline(config).run()
    assert chain.passes == 1
    assert len(FakeLedger.calls) == 1
    assert set(result["books"]) == {"atm_network", "atm_baseline",
                                    "skew_network", "skew_baseline"}
    assert result["books"]["atm_network"]["annual"]["2021"]["model"]["total_return"] == pytest.approx(0.001)
    assert len(result["books"]["atm_network"]["subfolds"]) == 1
    assert (tmp_path / "out" / f"fixture-{hashlib.sha256(config.read_bytes()).hexdigest()[:12]}"
            / "evaluation.json").exists()
    assert len(result["plots"]) == 4
    assert all((tmp_path / "out" / f"fixture-{hashlib.sha256(config.read_bytes()).hexdigest()[:12]}"
                / plot).is_file() for plot in result["plots"])


def test_replay_retains_unclosed_exposure_and_blocks_later_book_entries():
    """Later planned fills cannot coexist with a pair that never closed."""
    from statistical_arbitrage.backtest import TechReplayPipeline

    pairs = [
        {"id": "atm_network:first", "entry_date": "2021-01-05"},
        {"id": "atm_network:later", "entry_date": "2021-01-12"},
        {"id": "atm_baseline:other", "entry_date": "2021-01-12"},
    ]
    outcome = {
        "trades": [{"id": "atm_network:later", "pnl_usd": 99.0}],
        "exposures": [{"id": "atm_network:first", "open_date": "2021-01-05",
                       "marked_pnl_usd": -20.0}],
        "marks": [{"id": "atm_network:first", "date": "2021-01-11",
                   "pnl_usd": -20.0},
                  {"id": "atm_network:later", "date": "2021-01-13",
                   "pnl_usd": 99.0}],
        "cashflows": [{"id": "atm_network:first", "amount_usd": -20.0},
                      {"id": "atm_network:later", "amount_usd": 99.0}],
        "refusals": [], "summary": {"pairs_in": 3},
    }
    filtered, blocked = TechReplayPipeline._block_unclosed(pairs, outcome)
    assert [row["id"] for row in filtered["marks"]] == ["atm_network:first"]
    assert filtered["exposures"][0]["marked_pnl_usd"] == -20.0
    assert filtered["trades"] == []
    assert blocked == [{"id": "atm_network:later",
                        "status": "blocked_by_unclosed_exposure"}]


def test_replay_rate_filter_rejects_empty_and_nonfinite_observations():
    from statistical_arbitrage.backtest import TechReplayPipeline

    assert TechReplayPipeline._valid_rate({"DGS3MO": "0.25"}, "DGS3MO")
    for value in ("", ".", None, "NaN", "inf"):
        assert not TechReplayPipeline._valid_rate({"DGS3MO": value}, "DGS3MO")


def test_closed_hedge_failure_does_not_block_later_entries():
    from statistical_arbitrage.backtest import TechReplayPipeline

    pairs = [
        {"id": "atm_network:p1", "entry_date": "2022-01-03"},
        {"id": "atm_network:p2", "entry_date": "2022-01-10"},
    ]
    outcome = {
        "trades": [{"id": "atm_network:p2"}],
        "exposures": [{
            "id": "atm_network:p1", "open_date": "2022-01-03",
            "short_trade": {"close_date": "2022-01-07"},
            "long_trade": {"close_date": "2022-01-07"},
        }],
        "marks": [], "cashflows": [], "refusals": [], "summary": {},
    }
    filtered, blocked = TechReplayPipeline._block_unclosed(pairs, outcome)
    assert blocked == []
    assert len(filtered["trades"]) == 1
    assert len(filtered["exposures"]) == 1


def test_report_plots_use_pinned_pair_and_evaluation_curves(tmp_path, monkeypatch):
    """Chart data must follow actual pairs and scored equity, not fresh signals."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.figure import Figure
    from statistical_arbitrage.report import PerformanceReporter

    captured = {}
    original = Figure.savefig

    def capture(self, filename, *args, **kwargs):
        if filename.name == "cumulative-equity.png":
            captured["equity"] = {
                line.get_label(): list(line.get_ydata())
                for line in self.axes[0].lines
            }
        if filename.name == "z-atm-network.png":
            captured["z"] = list(self.axes[0].lines[0].get_ydata())
            captured["markers"] = len(self.axes[0].collections)
        return original(self, filename, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", capture)

    day = ["2021-01-04", "2021-01-05", "2021-01-06"]
    pair = {"symbol_a": "AAPL", "symbol_b": "AMZN", "fit_date": day[0],
            "status": "ok"}
    rows = [dict(pair, date=d, z=z) for d, z in zip(day, [2.2, 1.0, -0.1])]
    daily = [
        {"date": d, "model_equity": m, "baseline_equity": b,
         "equal_weight_equity": q, "qqq_equity": qqq}
        for d, m, b, q, qqq in zip(day, [100, 102, 101], [100, 101, 101],
                                  [100, 103, 104], [100, 102, 103])
    ]
    trade = {"id": "atm_network:2021-01-04:AAPL:AMZN",
             "entry_date": day[1], "exit_date": day[2]}
    result = {"books": {f"{variant}_network": {"full": {"daily": daily},
                          "trades": [trade] if variant == "atm" else []}
                        for variant in ("atm", "skew")}}
    charts = PerformanceReporter(capital_usd=100).save_plots(
        tmp_path, result, {"atm": rows, "skew": rows},
        symbols=["AAPL", "AMZN", "GOOGL"])
    assert len(charts) == 4
    assert all((tmp_path / name).is_file() for name in charts)
    assert all((tmp_path / name).stat().st_size > 0 for name in charts)
    assert plt.get_fignums() == []
    assert captured["equity"]["ATM network"] == [100, 102, 101]
    assert captured["equity"]["ATM all-pairs baseline"] == [100, 101, 101]
    assert captured["equity"]["Equal-weight tech"] == [100, 103, 104]
    assert captured["equity"]["QQQ"] == [100, 102, 103]
    assert captured["z"] == [2.2, 1.0, -0.1]
    assert captured["markers"] == 2
