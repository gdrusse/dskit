"""Exact-expiry data and bounded condor diagnostics for ADR-0189.

No fit loop lives here: ChronologicalCDFStudy owns model comparison. This
adapter supplies ETF expiry/settlement semantics and calls the existing
observation, numpy feature and payoff owners.
"""

import argparse
import hashlib
import json
from pathlib import Path

from dskit.pipeline.libs.predictive_cdf import ChronologicalCDFStudy
from .observations import IndexCloseRows
from .contracts import CONDOR_LEGS

__all__ = ["ExactExpiryCDFPanel", "CondorCDFDiagnostic"]


class ExactExpiryCDFPanel:
    """Join observed listed expiries to complete exchange-session price paths.

    Parameters
    ----------
    config : dict
        Archive root, surface/lifecycle files, symbols, volatility-index mapping,
        max DTE, lag/window counts and reference floor.

    Examples
    --------
    Read a configured panel::

        panel = ExactExpiryCDFPanel(config).read()
    """

    def __init__(self, config):
        self.config = config

    def read(self):
        """Construct raw-price terminal log returns and backward-only inputs.

        Returns
        -------
        DataFrame
            One symbol/quote-date/nominal-expiry row, with actual settlement date.

        Raises
        ------
        ValueError
            For duplicate metadata, inconsistent prices or unknown split paths.
        """
        import exchange_calendars as xc
        import numpy as np
        import pandas as pd
        from dskit.pipeline.libs.numpy import RealizedVolFeatures, ReturnWindows

        c = self.config
        surface = pd.read_parquet(c["surface"])
        lifecycle = pd.read_parquet(c["lifecycle"])
        keys = ["symbol", "quote_date", "expiry"]
        meta = surface.merge(lifecycle, on=keys, validate="one_to_one")
        calendar = xc.get_calendar("XNYS", start="1998-01-01", end="2026-12-31")
        sessions = calendar.sessions.tz_localize(None)
        panels, refused = [], {}
        self.reader_fingerprints = {}
        for symbol, iv_symbol in c["symbols"].items():
            price_reader = IndexCloseRows("prices", {"root": c["root"], "source": c["price_source"],
                                               "since_ms": int(pd.Timestamp(c["since"]).timestamp()*1000),
                                               "symbol": symbol})
            prices = price_reader.run(None, {})["records"]
            self.reader_fingerprints[symbol] = price_reader.fingerprint()
            price_frame = pd.DataFrame(prices).set_index("date")
            rows = meta[meta.symbol == symbol].copy()
            expiry = pd.DatetimeIndex(pd.to_datetime(rows.expiry))
            end_index = sessions.searchsorted(expiry, side="right")-1
            rows["settlement_date"] = sessions[end_index].strftime("%Y-%m-%d")
            rows["calendar_dte"] = (pd.to_datetime(rows.settlement_date)-pd.to_datetime(rows.quote_date)).dt.days
            rows = rows[(rows.calendar_dte >= 1) & (rows.calendar_dte <= c["max_dte"])].copy()
            entry_index = sessions.get_indexer(pd.to_datetime(rows.quote_date))
            end_index = sessions.get_indexer(pd.to_datetime(rows.settlement_date))
            valid_dates = (entry_index >= 0) & (end_index >= 0)
            refused[symbol] = {"non_session_quote": int((~valid_dates).sum())}
            rows = rows[valid_dates].copy()
            entry_index, end_index = entry_index[valid_dates], end_index[valid_dates]
            rows["sessions_to_expiry"] = end_index-entry_index
            # Reindex exposes missing interior observations instead of treating a gap as one day.
            closes = price_frame.close.reindex(sessions.strftime("%Y-%m-%d"))
            dividends = price_frame.dividend_amount.reindex(closes.index)
            missing = closes.isna().to_numpy().astype(int)
            dividend_missing = np.r_[0, np.cumsum(dividends.isna().to_numpy().astype(int))]
            rows["dividends_known"] = dividend_missing[end_index+1]-dividend_missing[entry_index] == 0
            refused[symbol]["unknown_dividend_window_not_entry_eligible"] = int((~rows.dividends_known).sum())
            cumulative_missing = np.r_[0, np.cumsum(missing)]
            complete = cumulative_missing[end_index+1]-cumulative_missing[entry_index] == 0
            refused[symbol]["incomplete_path"] = int((~complete).sum())
            rows = rows[complete].copy()
            entry_index, end_index = entry_index[complete], end_index[complete]
            rows["spot"] = closes.to_numpy()[entry_index]
            rows["terminal_price"] = closes.to_numpy()[end_index]
            rows["terminal_return"] = np.log(rows.terminal_price/rows.spot)
            # Reader refuses split coefficients !=1. Archive spot is a separate consistency check.
            mismatch = abs(rows.spot/rows.chain_underlying_price-1)
            if (mismatch > c["spot_tolerance"]).any():
                raise ValueError(f"{symbol}: close/option-snapshot disagreement {mismatch.max()}")
            common = {"fields": ["close"], "max_gap": c["feature_gap_days"]*86400000,
                      "carry_fields": ["date"], "require_fields": [], "drop_incomplete": False}
            rv = RealizedVolFeatures("rv", {**common, "windows": c["windows"]}).run(None, {"records": prices})["rows"]
            lags = ReturnWindows("lags", {**common, "lookback": c["lags"], "lag_prefix": "ret_lag_"}).run(None, {"records": prices})["rows"]
            # The one-step label from ReturnWindows is deliberately never carried into features.
            lag_frame = pd.DataFrame(lags).drop(columns=["label"])
            features = pd.DataFrame(rv).merge(lag_frame, on="date", validate="one_to_one")
            rows = rows.merge(features, left_on="quote_date", right_on="date", validate="many_to_one")
            iv_reader = IndexCloseRows("iv", {"root": c["root"], "source": c["iv_source"],
                                              "symbol": iv_symbol})
            iv = iv_reader.run(None, {})["records"]
            self.reader_fingerprints[iv_symbol] = iv_reader.fingerprint()
            iv_by_date = {r["date"]: r["close"] for r in iv}
            rows["own_iv"] = rows.quote_date.map(iv_by_date)
            rows["log_calendar_dte"] = np.log(rows.calendar_dte)
            rows["log_sessions_to_expiry"] = np.log(rows.sessions_to_expiry)
            rows["series_age_calendar"] = (pd.to_datetime(rows.quote_date)-pd.to_datetime(rows.first_seen_date)).dt.days
            first_index = sessions.searchsorted(pd.to_datetime(rows.first_seen_date))
            entry_index = sessions.get_indexer(pd.to_datetime(rows.quote_date))
            rows["series_age_sessions"] = entry_index-first_index
            rows["series_total_tenor_calendar"] = rows.series_age_calendar+rows.calendar_dte
            rows["series_total_tenor_sessions"] = rows.series_age_sessions+rows.sessions_to_expiry
            rows["life_fraction_calendar"] = rows.series_age_calendar/rows.series_total_tenor_calendar
            rows["life_fraction_sessions"] = rows.series_age_sessions/rows.series_total_tenor_sessions
            rows["calendar_days_per_session"] = rows.calendar_dte/rows.sessions_to_expiry
            rows["reference_scale"] = rows.rv_22.clip(lower=c["reference_floor"])*np.sqrt(rows.sessions_to_expiry)
            good = rows.reference_scale.notna()
            refused[symbol]["missing_reference"] = int((~good).sum())
            refused[symbol]["iv_for_train_only_imputation"] = int(rows.own_iv.isna().sum())
            panels.append(rows[good])
        self.refused = refused
        self.source_hashes = {name: hashlib.sha256(Path(c[name]).read_bytes()).hexdigest()
                              for name in ("surface", "lifecycle")}
        return pd.concat(panels, ignore_index=True)


class CondorCDFDiagnostic:
    """Fixed synthetic strike geometry, not selected trades or quoted returns.

    Parameters
    ----------
    strikes_z : list of float
        Four ordered log-moneyness coordinates in causal reference-scale units.
    integration_points : int
        Midpoint nodes per spread for CDF integration.

    Examples
    --------
    Symmetric diagnostic, common to all models::

        diagnostic = CondorCDFDiagnostic([-2, -1, 1, 2], 101)
    """

    def __init__(self, strikes_z, integration_points):
        self.strikes_z, self.integration_points = strikes_z, integration_points

    @staticmethod
    def payoff(levels, strikes):
        """Vectorize the existing contract owner's signed intrinsic rule.

        Parameters
        ----------
        levels : ndarray
            (rows, draws) terminal price levels.
        strikes : ndarray
            (rows, 4) strike prices in long-put through long-call order.

        Returns
        -------
        ndarray
            Nonpositive bounded settlement payoff, before initial credit.
        """
        import numpy as np

        result = np.zeros_like(levels)
        for j, (right, sign) in enumerate(CONDOR_LEGS):
            intrinsic = strikes[:, j, None]-levels if right == "put" else levels-strikes[:, j, None]
            result += sign*np.maximum(intrinsic, 0)
        return result

    def __call__(self, frame, curve, draws):
        """Return expected-loss error and CDF/payoff numerical agreement.

        Parameters
        ----------
        frame : DataFrame
            Spot, reference_scale and realized terminal_price columns.
        curve : curve
            Conditional standardized-return CDF.
        draws : ndarray
            Deterministic quantile quadrature nodes, not extra observations.

        Returns
        -------
        dict
            Per-row loss diagnostics, normalized by wider wing and in dollars/share.
        """
        import numpy as np

        scale, spot = frame.reference_scale.to_numpy(), frame.spot.to_numpy()
        strikes = spot[:, None]*np.exp(scale[:, None]*self.strikes_z)
        width = np.maximum(strikes[:, 1]-strikes[:, 0], strikes[:, 3]-strikes[:, 2])
        mean_loss = -self.payoff(spot[:, None]*np.exp(scale[:, None]*draws), strikes).mean(1)
        realized = -self.payoff(frame.terminal_price.to_numpy()[:, None], strikes)[:, 0]
        integral = np.zeros(len(frame))
        for a, b, put in [(0, 1, True), (2, 3, False)]:
            prices = strikes[:, a, None]+(strikes[:, b]-strikes[:, a])[:, None]*(np.arange(self.integration_points)+.5)/self.integration_points
            p = curve.cdf(np.log(prices/spot[:, None])/scale[:, None])
            integral += (p if put else 1-p).mean(1)*(strikes[:, b]-strikes[:, a])
        z = np.array(self.strikes_z)
        truth = np.log(frame.terminal_price.to_numpy()/spot)/scale
        brier = ((curve.cdf(z)-(truth[:, None] <= z))**2).mean(1)
        return {"condor_loss_mse": ((integral-realized)/width)**2,
                "condor_loss_bias": (integral-realized)/width,
                "expected_loss_per_share": integral,
                "realized_loss_per_share": realized,
                "payoff_quadrature_gap": abs(integral-mean_loss),
                "strike_brier": brier}


def _main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    adapter = ExactExpiryCDFPanel(config["data"])
    frame = adapter.read()
    output = Path(config["study"]["output"])
    if output.exists():
        raise FileExistsError(output)
    diagnostic = CondorCDFDiagnostic(**config["diagnostic"])
    study = ChronologicalCDFStudy(config["study"])
    print("panel", len(frame), adapter.refused, flush=True)
    scores = study.run(frame, diagnostic)
    study.summarize(scores)
    frame.to_parquet(output/"panel.parquet", index=False)
    (output/"data_provenance.json").write_text(json.dumps({"refused": adapter.refused,
                                                         "sha256": adapter.source_hashes,
                                                         "readers": adapter.reader_fingerprints}, indent=2))


if __name__ == "__main__":
    _main()
