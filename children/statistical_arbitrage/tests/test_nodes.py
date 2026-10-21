"""Focused contracts for phase-one source wrappers."""

from __future__ import annotations

import math

import pytest

from statistical_arbitrage.data import TechAdjustedReturns
from statistical_arbitrage.surface import TechOptionSurface

PRICE_PARAMS = {"end_before": "2026-01-01", "ports": {"aapl": "AAPL", "amzn": "AMZN", "googl": "GOOGL", "qqq": "QQQ"}, "factor_port": "qqq"}
SURFACE_PARAMS = {"symbol_field": "symbol", "date_field": "date", "expiration_field": "expiration", "strike_field": "strike", "right_field": "right", "bid_field": "bid", "ask_field": "ask", "call_values": ["call"], "put_values": ["put"], "end_before": "2026-01-01", "targets": [{"dte": 30, "tolerance": 7}], "target_field": "target_dte", "status_field": "status", "atm_band": 0.05, "wing_delta": 0.25, "min_forward_pairs": 5, "fields": {"atm_iv": "atm_iv", "risk_reversal": "risk_reversal"}, "rate_date_field": "observation_date", "rate_value_field": "DGS3MO", "rate_max_age_days": 5, "rate_scale": 0.01}


def _bars(symbol: str, closes: list[float], raw: list[float] | None = None) -> list[dict]:
    dates = ["2020-08-27", "2020-08-28", "2020-08-31"]
    raw = closes if raw is None else raw
    return [
        {"symbol": symbol, "date": date, "adjusted_close": adjusted, "close": plain,
         "split_coefficient": 4.0 if symbol == "AAPL" and date == "2020-08-31" else 1.0,
         "dividend_amount": 0.0}
        for date, adjusted, plain in zip(dates, closes, raw)
    ]


def test_adjusted_returns_use_common_calendar_and_lagged_factor() -> None:
    node = TechAdjustedReturns("prices", PRICE_PARAMS)
    out = node.run(None, {
        "aapl": _bars("AAPL", [100, 102, 104], [400, 408, 104]),
        "amzn": _bars("AMZN", [200, 202, 204]),
        "googl": _bars("GOOGL", [300, 303, 306]),
        "qqq": _bars("QQQ", [150, 151, 152]),
    })
    rows = {(r["symbol"], r["date"]): r for r in out["records"]}
    assert len(rows) == 9
    assert len(out["benchmark"]) == 3
    assert all(r["symbol"] == "QQQ" for r in out["benchmark"])
    aapl = rows["AAPL", "2020-08-31"]
    assert aapl["log_return"] == pytest.approx(math.log(104 / 102))
    assert aapl["factor_return"] == pytest.approx(math.log(152 / 151))
    assert aapl["adjusted_close"] == 104
    assert aapl["split_coefficient"] == 4.0
    assert out["corporate_events"] == [{"symbol": "AAPL", "date": "2020-08-31", "split_coefficient": 4.0, "dividend_amount": 0.0}]
    assert out["summary"]["common_dates"] == 3


def test_adjusted_returns_refuse_missing_or_future_prices() -> None:
    node = TechAdjustedReturns("prices", PRICE_PARAMS)
    valid = {
        "aapl": _bars("AAPL", [100, 102, 104]),
        "amzn": _bars("AMZN", [200, 202, 204]),
        "googl": _bars("GOOGL", [300, 303, 306]),
        "qqq": _bars("QQQ", [150, 151, 152]),
    }
    with pytest.raises(ValueError, match="future"):
        node.run(None, {**valid, "qqq": valid["qqq"] + [
            {"symbol": "QQQ", "date": "2026-01-02", "adjusted_close": 153}
        ]})
    with pytest.raises(ValueError, match="positive"):
        bad = [dict(r) for r in valid["aapl"]]
        bad[1]["adjusted_close"] = 0
        node.run(None, {**valid, "aapl": bad})
    with pytest.raises(ValueError, match="corporate action fields"):
        bad = [dict(r) for r in valid["aapl"]]
        bad[1].pop("split_coefficient")
        node.run(None, {**valid, "aapl": bad})


def test_surface_delegates_30dte_and_prior_rate_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_run(self, ctx, inputs):
        seen.update(params=self.params, inputs=inputs)
        return {"records": [{"symbol": "AAPL", "date": "2020-08-31", "target_dte": 30,
                            "atm_iv": 0.3, "risk_reversal": 0.05, "status": "ok"}],
                "summary": {"groups": 1}}

    monkeypatch.setattr("statistical_arbitrage.surface.ChainDayFeatures.run", fake_run)
    node = TechOptionSurface("surface", SURFACE_PARAMS)
    chain, rates = object(), object()
    out = node.run(None, {"records": chain, "rates": rates})
    assert seen["inputs"] == {"records": chain, "rates": rates}
    p = seen["params"]
    assert p["targets"] == [{"dte": 30, "tolerance": 7}]
    assert p["rate_max_age_days"] == 5
    assert p["rate_scale"] == 0.01
    assert p["fields"]["risk_reversal"] == "risk_reversal"
    assert p["fields"]["atm_iv"] == "atm_iv"
    assert out["records"][0]["atm_iv"] == 0.3


def test_internal_fill_is_bounded_flagged_and_never_extends_last_date() -> None:
    valid = {
        "aapl": _bars("AAPL", [100, 102, 104]),
        "amzn": _bars("AMZN", [200, 202, 204]),
        "googl": _bars("GOOGL", [300, 303, 306]),
        "qqq": _bars("QQQ", [150, 151, 152]),
    }
    internal = {**valid, "qqq": [valid["qqq"][0], valid["qqq"][2]]}
    filled = TechAdjustedReturns(
        "prices", {**PRICE_PARAMS, "max_forward_fill_sessions": 1}
    ).run(None, internal)
    assert filled["summary"]["common_dates"] == 3
    assert filled["summary"]["imputed_dates"]["qqq"] == 1
    assert filled["benchmark"][1]["imputed"] is True
    assert filled["benchmark"][1]["log_return"] == pytest.approx(0.0)
    strict = TechAdjustedReturns(
        "prices", {**PRICE_PARAMS, "max_forward_fill_sessions": 0}
    ).run(None, internal)
    assert strict["summary"]["common_dates"] == 2
    trailing = {**valid, "qqq": valid["qqq"][:2]}
    bounded = TechAdjustedReturns(
        "prices", {**PRICE_PARAMS, "max_forward_fill_sessions": 1}
    ).run(None, trailing)
    assert bounded["summary"]["end"] == "2020-08-28"
