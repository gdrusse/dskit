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
