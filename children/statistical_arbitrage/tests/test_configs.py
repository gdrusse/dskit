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
    config = tmp_path / "backtest.json"
    config.write_text(json.dumps({
        "name": "fixture", "structural_config": {"path": str(structural),
            "sha256": hashlib.sha256(structural.read_bytes()).hexdigest()},
        "structural_rows": pinned, "source_configs": {},
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
        "validation_calendar": str(calendar),
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
