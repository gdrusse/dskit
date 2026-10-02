"""The study code takes its windows, calendar, annualization and dividend field from the config.

Each test changes ONE convention away from the shipped value and asserts the output follows;
the last block pins that the shipped workflow args carry today's values, so the move out of
the code left the outputs unchanged.
"""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from index_options import cdf_study
from index_options.cdf_study import (ExactExpiryCDFPanel, RobustCorrectionStudy,
                                     panel_convention_problems)
from index_options.nodes import ExactExpiryPanelRead

CHILD = Path(__file__).resolve().parents[1]
DAYS = pd.bdate_range("2023-01-02", "2023-06-30").strftime("%Y-%m-%d").tolist()
QUOTES = [d for d in DAYS if "2023-03-01" <= d <= "2023-03-31"] + ["2023-04-05"]


def _expiry(quote):
    return (pd.Timestamp(quote)+pd.Timedelta(days=5 if quote == "2023-04-05" else 7)
            ).strftime("%Y-%m-%d")


def _config(tmp_path, monkeypatch, *, dividends=True, **over):
    """A one-symbol source set on weekday prices, served by a patched ``IndexCloseRows``."""
    prices = {d: 100+i*.3+3*math.sin(i) for i, d in enumerate(DAYS)}

    class Reader:
        def __init__(self, key, params):
            self.symbol = params["symbol"]

        def run(self, ctx, inputs):
            rows = [{"date": d, "close": 20. if self.symbol == "VXN" else p, "asof_ms": 0,
                     "instrument": self.symbol, "contract": self.symbol, "group": self.symbol,
                     **({"div": None} if dividends else {})} for d, p in prices.items()]
            return {"records": rows}

        def fingerprint(self):
            return {"sha256": "fixture"}

    monkeypatch.setattr("index_options.cdf_study.IndexCloseRows", Reader)
    rows = pd.DataFrame({"symbol": "QQQ", "quote_date": QUOTES,
                         "expiry": [_expiry(d) for d in QUOTES],
                         "chain_underlying_price": [prices[d] for d in QUOTES]})
    rows.to_parquet(tmp_path/"surface.parquet")
    rows[["symbol", "quote_date", "expiry"]].assign(first_seen_date="2023-02-01").to_parquet(
        tmp_path/"lifecycle.parquet")
    return {"root": "fixture", "surface": str(tmp_path/"surface.parquet"),
            "lifecycle": str(tmp_path/"lifecycle.parquet"), "symbols": {"QQQ": "VXN"},
            "price_source": "p", "iv_source": "i", "since": "2023-01-01", "max_dte": 45,
            "lags": 5, "windows": [1, 5], "feature_gap_days": 7, "reference_floor": .001,
            "spot_tolerance": .02, "reference_window": 5, "change_lags": [1, 5],
            "directional_windows": [2, 5], "periods_per_year": 252, "calendar": "XNYS",
            "calendar_pad_days": 30, **over}


def test_directional_windows_come_from_the_param(tmp_path, monkeypatch):
    frame = ExactExpiryCDFPanel(_config(tmp_path, monkeypatch, directional_windows=[2])).read()
    assert {"momentum_2", "down_rv_2", "up_rv_2"} <= set(frame)
    assert not {"momentum_5", "momentum_22", "down_rv_5"} & set(frame)
    np.testing.assert_allclose(frame.momentum_2, frame.ret_lag_0+frame.ret_lag_1)


def test_a_directional_window_beyond_lags_is_refused(tmp_path, monkeypatch):
    cfg = _config(tmp_path, monkeypatch, directional_windows=[2, 6])
    with pytest.raises(ValueError, match="exceeds lags 5"):
        ExactExpiryCDFPanel(cfg).read()


@pytest.mark.parametrize("window", [1, 5])
def test_the_reference_scale_uses_the_declared_window(tmp_path, monkeypatch, window):
    frame = ExactExpiryCDFPanel(_config(tmp_path, monkeypatch, reference_window=window)).read()
    expected = frame[f"rv_{window}"].clip(lower=.001)*np.sqrt(frame.sessions_to_expiry)
    np.testing.assert_allclose(frame.reference_scale, expected)
    assert len(frame) > 0


def test_a_reference_window_outside_windows_is_refused(tmp_path, monkeypatch):
    cfg = _config(tmp_path, monkeypatch, reference_window=22)
    with pytest.raises(ValueError, match="reference_window 22"):
        ExactExpiryCDFPanel(cfg).read()


def test_matched_vrp_follows_the_reference_window_and_periods_per_year():
    frame = pd.DataFrame({"chain_atm_iv": [.2], "rv_5": [.01], "rv_22": [.5],
                          "sessions_to_expiry": [10]})
    out = ExactExpiryCDFPanel.add_matched_dte_vrp(frame.copy(), 1e-3, 5, 360)
    assert out.matched_implied_variance.iloc[0] == pytest.approx(.2**2*10/360)
    assert out.matched_trailing_variance.iloc[0] == pytest.approx(.01**2*10)


def test_surface_dynamics_follow_change_lags_reference_window_and_periods_per_year():
    dates = pd.bdate_range("2020-01-02", periods=8).strftime("%Y-%m-%d")
    rows = [{"symbol": "SPY", "expiry": "2020-03-20", "quote_date": d, "actual_calendar_dte": 40-i,
             "chain_atm_iv": .2, "rv_3": .01, "chain_log_atm_iv": i/100,
             "chain_log_skew25": i/200, "chain_log_curvature25": i/300,
             "chain_log_put_call_oi": .2, "rn_q_1000": -1., "rn_q_5000": 0., "rn_q_9000": 2.}
            for i, d in enumerate(dates)]
    out = ExactExpiryCDFPanel.add_surface_dynamics(pd.DataFrame(rows), [.1, .5, .9], [2, 3], 3, 360)
    assert {"chain_log_atm_iv_change_2", "chain_log_atm_iv_change_3"} <= set(out)
    assert not {c for c in out if c.endswith(("_change_1", "_change_5", "_change_22"))}
    assert out.chain_log_atm_iv_change_3.iloc[-1] == pytest.approx(.03)
    assert out.implied_minus_realized_variance.iloc[0] == pytest.approx(.2**2/360-.01**2)


def test_change_lags_must_be_members_of_windows():
    base = {"windows": [1, 5], "lags": 5, "reference_window": 5, "directional_windows": [2]}
    assert panel_convention_problems({**base, "change_lags": [1, 5]}) == []
    assert any("change_lags" in p for p in panel_convention_problems({**base, "change_lags": [3]}))


@pytest.mark.parametrize("bad", [{"periods_per_year": 0}, {"periods_per_year": True},
                                 {"calendar": ""}, {"calendar_pad_days": -1},
                                 {"calendar_pad_days": 1.5}, {"dividend_field": ""},
                                 {"directional_windows": []}, {"directional_windows": [2, 2]}])
def test_a_malformed_convention_is_a_problem(bad):
    base = {"windows": [1, 5], "lags": 5, "reference_window": 5, "change_lags": [1, 5]}
    assert panel_convention_problems({**base, **bad})


def test_the_reader_node_validates_the_conventions_at_plan_time():
    params = {"root": "x", "surface": "s", "lifecycle": "l", "symbols": {"QQQ": "VXN"},
              "price_source": "p", "iv_source": "i", "since": "2020-01-01", "max_dte": 45,
              "lags": 5, "windows": [1, 5], "feature_gap_days": 7, "reference_floor": .001,
              "spot_tolerance": .02, "columns": ["symbol"], "directional_windows": [2],
              "reference_window": 5, "change_lags": [1, 5]}
    assert not any("window" in p for p in ExactExpiryPanelRead.validate_params(params))
    problems = ExactExpiryPanelRead.validate_params({**params, "reference_window": 22})
    assert any("reference_window" in p for p in problems)


def test_the_calendar_comes_from_the_param(tmp_path, monkeypatch):
    """2023-04-10 (Easter Monday) is a NYSE session and a London holiday."""
    nyse = ExactExpiryCDFPanel(_config(tmp_path, monkeypatch)).read()
    london = ExactExpiryCDFPanel(_config(tmp_path, monkeypatch, calendar="XLON")).read()
    row = lambda f: f[f.quote_date == "2023-04-05"].iloc[0]  # noqa: E731
    assert row(nyse).settlement_date == "2023-04-10"
    assert row(london).settlement_date == "2023-04-06"


def test_the_calendar_range_derives_from_the_data_and_the_pad(tmp_path, monkeypatch):
    import exchange_calendars as xc
    seen = {}
    real = xc.get_calendar

    def spy(name, start=None, end=None, **kwargs):
        seen.update(name=name, start=pd.Timestamp(start), end=pd.Timestamp(end))
        return real(name, start=start, end=end, **kwargs)

    monkeypatch.setattr(xc, "get_calendar", spy)
    cfg = _config(tmp_path, monkeypatch, calendar_pad_days=11)
    ExactExpiryCDFPanel(cfg).read()
    assert seen["name"] == "XNYS"
    assert seen["start"] == pd.Timestamp("2023-02-01")-pd.Timedelta(days=11)  # first_seen_date
    assert seen["end"] == pd.Timestamp(max(map(_expiry, QUOTES)))+pd.Timedelta(days=11)


def test_far_expiries_neither_stretch_the_calendar_nor_enter_the_panel(tmp_path, monkeypatch):
    cfg = _config(tmp_path, monkeypatch)
    surface = pd.read_parquet(cfg["surface"])
    far = surface.iloc[:1].assign(expiry="2031-12-19")
    pd.concat([surface, far]).to_parquet(cfg["surface"])
    life = pd.read_parquet(cfg["lifecycle"])
    pd.concat([life, life.iloc[:1].assign(expiry="2031-12-19")]).to_parquet(cfg["lifecycle"])
    frame = ExactExpiryCDFPanel(cfg).read()
    assert "2031-12-19" not in set(frame.expiry) and len(frame) == len(QUOTES)


def test_no_dividend_field_means_dividends_known_zero(tmp_path, monkeypatch):
    frame = ExactExpiryCDFPanel(_config(tmp_path, monkeypatch, dividends=False)).read()
    assert frame.dividends_known.all() and len(frame) == len(QUOTES)


def test_a_declared_dividend_field_marks_unknown_windows(tmp_path, monkeypatch):
    cfg = _config(tmp_path, monkeypatch, dividend_field="div")
    frame = ExactExpiryCDFPanel(cfg).read()
    assert not frame.dividends_known.any()          # the fixture's dividends are all null


def test_a_dividend_field_the_price_source_lacks_is_refused(tmp_path, monkeypatch):
    cfg = _config(tmp_path, monkeypatch, dividends=False, dividend_field="div")
    with pytest.raises(ValueError, match="dividend_field 'div'"):
        ExactExpiryCDFPanel(cfg).read()


def test_the_report_limitation_names_the_symbols_with_unknown_dividends(tmp_path):
    prepare = tmp_path/"prepare"
    prepare.mkdir()
    study = object.__new__(RobustCorrectionStudy)
    study.config = {"decision_root": str(tmp_path)}
    panel = pd.DataFrame({"symbol": ["AAA", "BBB", "CCC"], "dividends_known": [False, True, False]})
    panel.to_parquet(prepare/"panel.parquet")
    assert study._dividend_limitations() == [
        "AAA, CCC are excluded when dividend windows are unknown"]
    panel.iloc[:1].to_parquet(prepare/"panel.parquet")
    assert study._dividend_limitations() == ["AAA is excluded when dividend windows are unknown"]
    panel.assign(dividends_known=True).to_parquet(prepare/"panel.parquet")
    assert study._dividend_limitations() == []


def test_no_literal_study_convention_is_left_in_the_panel_code():
    source = Path(cdf_study.__file__).read_text()
    body = source[source.index("class ExactExpiryCDFPanel"):source.index("class CondorCDFDiagnostic")]
    for literal in ('"XNYS"', "1998-01-01", "2026-12-31", "/252", "rv_22", "(5, 22)",
                    "(1, 5, 22)", ".dividend_amount"):
        assert literal not in body, literal
    assert "IWM" not in source and "QQQ" not in source


# --- the shipped args hold today's values, so the move out of the code changed no output -----

def _args():
    return json.loads((CHILD/"configs"/"workflow.json").read_text())["args"]


def test_the_shipped_args_carry_the_values_that_were_literals():
    args = _args()
    limits, windows = args["feature_limits"], args["feature_windows"]
    assert limits["reference_window"] == 22 and limits["change_lags"] == [1, 5, 22]
    assert limits["periods_per_year"] == 252 and limits["calendar"] == "XNYS"
    assert windows["directional"] == [5, 22]
    assert args["feature_sources"]["dividend_field"] == "dividend_amount"


def test_the_shipped_args_pass_the_convention_rules():
    args = _args()
    config = {"windows": args["feature_windows"]["returns"], "lags": args["feature_limits"]["lags"],
              "directional_windows": args["feature_windows"]["directional"],
              **{k: args["feature_limits"][k] for k in (
                  "reference_window", "change_lags", "periods_per_year", "calendar",
                  "calendar_pad_days")},
              "dividend_field": args["feature_sources"]["dividend_field"]}
    assert panel_convention_problems(config) == []


@pytest.mark.parametrize("name", ["step1b-feature-engineering", "step4-feature-selection",
                                  "step5-model-zoo", "step6-hpo"])
def test_every_panel_template_passes_every_convention(name):
    text = (CHILD/"configs"/"templates"/f"{name}.json").read_text()
    for placeholder in ("G.reference_window", "G.change_lags", "windows.directional",
                        "G.periods_per_year", "G.calendar", "G.calendar_pad_days",
                        "F.dividend_field"):
        assert "${"+placeholder+"}" in text, placeholder


def test_the_directional_windows_have_their_declared_return_history_fields():
    args = _args()
    fields = args["families"]["availability"]["return_history"]["fields"]
    volatility = args["families"]["availability"]["realized_volatility"]["fields"]
    for window in args["feature_windows"]["directional"]:
        assert f"momentum_{window}" in fields
        assert {f"down_rv_{window}", f"up_rv_{window}"} <= set(volatility)
