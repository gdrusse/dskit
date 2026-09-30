"""Exact-expiry data and bounded condor diagnostics for ADR-0189.

No fit loop lives here: ChronologicalCDFStudy owns model comparison. This
adapter supplies ETF expiry/settlement semantics and calls the existing
observation, numpy feature and payoff owners.
"""

import argparse
import hashlib
import json
from pathlib import Path

from dskit.pipeline.libs.predictive_cdf import ChronologicalCDFStudy, CDFHyperparameterStudy
from dskit.pipeline.libs.observations import ObservationRows
from .observations import IndexCloseRows
from .contracts import CONDOR_LEGS

__all__ = ["ExactExpiryCDFPanel", "RawChainFeatureBuilder", "CondorCDFDiagnostic"]


class RawChainFeatureBuilder:
    """Prepare order-invariant contract tensors and option-implied proxy quantiles."""

    NODE_FIELDS = ("log_moneyness", "iv", "log_rel_spread", "log_oi", "log_depth")

    def __init__(self, nodes, moneyness_bounds, max_node_gap, proxy_probabilities,
                 min_wing_nodes, max_inner_gap, max_outer_gap):
        import math
        if (type(nodes) is not int or nodes < 3 or len(moneyness_bounds) != 2
                or moneyness_bounds[0] >= 0 or moneyness_bounds[1] <= 0
                or moneyness_bounds[0] >= moneyness_bounds[1]
                or any(not math.isfinite(v) or v <= 0 for v in
                       (max_node_gap, max_inner_gap, max_outer_gap))
                or type(min_wing_nodes) is not int or min_wing_nodes < 1
                or len(proxy_probabilities) < 3 or proxy_probabilities[0] <= 0
                or proxy_probabilities[-1] >= 1
                or any(a >= b for a, b in zip(proxy_probabilities,
                                               proxy_probabilities[1:]))):
            raise ValueError("invalid raw-chain feature contract")
        self.nodes, self.bounds = nodes, tuple(moneyness_bounds)
        self.max_node_gap = max_node_gap
        self.proxy_probabilities = tuple(proxy_probabilities)
        self.min_wing_nodes = min_wing_nodes
        self.max_inner_gap, self.max_outer_gap = max_inner_gap, max_outer_gap

    @staticmethod
    def _qname(probability):
        return f"rn_q_{int(round(10000*probability)):04d}"

    @staticmethod
    def _usable_quotes(rows):
        """Mask invalid, crossed and duplicate contracts deterministically."""
        import numpy as np
        duplicate = rows.duplicated(["strike", "type"], keep=False)
        finite = np.isfinite(rows[["strike", "mark", "bid", "ask", "bid_size",
                                   "ask_size", "open_interest",
                                   "implied_volatility"]]).all(axis=1)
        valid = (finite & ~duplicate & (rows.strike > 0) & (rows.mark > 0)
                 & (rows.bid >= 0) & (rows.ask >= rows.bid)
                 & (rows.mark >= rows.bid) & (rows.mark <= rows.ask)
                 & (rows.bid_size >= 0) & (rows.ask_size >= 0)
                 & (rows.open_interest >= 0) & (rows.implied_volatility > 0))
        return rows.loc[valid].sort_values(["strike", "type"], kind="mergesort").copy()

    def _proxy(self, rows, spot):
        import numpy as np
        import pandas as pd
        clean = self._usable_quotes(rows)
        paired = clean.pivot_table(index="strike", columns="type", values="mark",
                                   aggfunc="median").dropna()
        if paired.empty or not {"call", "put"}.issubset(paired.columns):
            return None
        forwards = paired.index.to_numpy()+paired.call.to_numpy()-paired.put.to_numpy()
        forwards = forwards[np.isfinite(forwards) & (forwards > 0)]
        if not len(forwards):
            return None
        forward = float(np.median(forwards))
        clean["call_equivalent"] = np.where(clean.type.eq("call"), clean.mark,
                                             clean.mark+forward-clean.strike)
        otm = clean[((clean.type == "put") & (clean.strike <= forward))
                    | ((clean.type == "call") & (clean.strike >= forward))]
        calls = otm.groupby("strike", as_index=False).call_equivalent.median().sort_values("strike")
        strike, price = calls.strike.to_numpy(), calls.call_equivalent.to_numpy()
        if len(strike) < 2*self.min_wing_nodes+1:
            return None
        left, right = strike < forward, strike > forward
        logk = np.log(strike/forward)
        if (left.sum() < self.min_wing_nodes or right.sum() < self.min_wing_nodes
                or -logk[left].max() > self.max_inner_gap
                or logk[right].min() > self.max_inner_gap
                or (np.diff(logk) > self.max_outer_gap).any()):
            return None
        raw_slope = np.diff(price)/np.diff(strike)
        from sklearn.isotonic import IsotonicRegression
        slope = IsotonicRegression(increasing=True, y_min=-1., y_max=0.,
                                   out_of_bounds="clip").fit_transform(
                                       (strike[:-1]+strike[1:])/2, raw_slope,
                                       sample_weight=np.diff(strike))
        cdf = np.clip(1+slope, 0, 1)
        if not (np.isfinite(cdf).all() and (np.diff(cdf) >= -1e-12).all()):
            return None
        informative = np.flatnonzero((cdf > 1e-4) & (cdf < 1-1e-4))
        if len(informative) < 3:
            return None
        first, last = informative[0], informative[-1]
        cdf = cdf[first:last+1]
        midpoint = (strike[:-1]+strike[1:])/2
        midpoint = midpoint[first:last+1]
        p = np.r_[0., cdf, 1.]
        values = np.r_[np.log(strike[first]/spot), np.log(midpoint/spot),
                       np.log(strike[last+1]/spot)]
        # Flat probability stretches are atoms; np.interp requires a stable
        # increasing inverse grid, so retain the first occurrence explicitly.
        keep = np.r_[True, np.diff(p) > 1e-12]
        p, values = p[keep], values[keep]
        if p[-1] < 1:
            p, values = np.r_[p, 1.], np.r_[values, np.log(strike[-1]/spot)]
        quantiles = np.interp(self.proxy_probabilities, p, values)
        projected = price[0]+np.r_[0., np.cumsum(slope*np.diff(strike))]
        return {"forward_ratio": forward/spot, "projection_distance": float(
                    np.sqrt(np.mean((projected-price)**2))/spot),
                "mass": float(cdf[-1]-cdf[0]), "quantiles": quantiles}

    def transform(self, chain, meta):
        """Return one fixed tensor/proxy row for every requested chain key."""
        import numpy as np
        import pandas as pd
        required = {"symbol", "date", "expiration", "strike", "type", "mark",
                    "bid", "ask", "bid_size", "ask_size", "open_interest",
                    "implied_volatility"}
        if required-set(chain) or {"symbol", "quote_date", "expiry",
                                   "chain_underlying_price"}-set(meta):
            raise ValueError("missing raw-chain or metadata columns")
        keys = ["symbol", "quote_date", "expiry"]
        lookup = {(r.symbol, r.quote_date, r.expiry): r.chain_underlying_price
                  for r in meta[keys+["chain_underlying_price"]].itertuples(index=False)}
        grouped = {(a, b, c): g for (a, b, c), g in
                   chain.groupby(["symbol", "date", "expiration"], sort=False)}
        centers = np.linspace(*self.bounds, self.nodes)
        records = []
        for key in sorted(lookup):
            spot = float(lookup[key]); rows = grouped.get(key)
            record = dict(zip(keys, key)); proxy = None
            if rows is not None and np.isfinite(spot) and spot > 0:
                rows = self._usable_quotes(rows)
                rows["log_moneyness"] = np.log(rows.strike/spot)
                rows["depth"] = rows.bid_size.fillna(0)+rows.ask_size.fillna(0)
                rows["open_interest"] = rows.open_interest.fillna(0)
                rows["rel_spread"] = (rows.ask-rows.bid)/rows.mark
                proxy = self._proxy(rows, spot)
            else:
                rows = None
            for i, center in enumerate(centers):
                chosen = None
                if rows is not None:
                    right = "put" if center < 0 else "call"
                    candidates = rows[(rows.type == right) & rows.implied_volatility.gt(0)
                                      & rows.mark.gt(0) & rows.rel_spread.ge(0)]
                    if len(candidates):
                        distance = abs(candidates.log_moneyness-center)
                        if distance.min() <= self.max_node_gap:
                            chosen = candidates.loc[distance.sort_values(kind="mergesort").index[0]]
                values = ([chosen.log_moneyness, chosen.implied_volatility,
                           np.log1p(chosen.rel_spread), np.log1p(max(chosen.open_interest, 0)),
                           np.log1p(max(chosen.depth, 0))] if chosen is not None
                          else [np.nan]*len(self.NODE_FIELDS))
                for field, value in zip(self.NODE_FIELDS, values):
                    record[f"chain_node_{i:02d}_{field}"] = value
                record[f"chain_node_{i:02d}_mask"] = int(chosen is not None)
            record["rn_proxy_eligible"] = int(proxy is not None)
            record["rn_forward_ratio"] = proxy["forward_ratio"] if proxy else np.nan
            record["rn_projection_distance"] = proxy["projection_distance"] if proxy else np.nan
            record["rn_interior_mass"] = proxy["mass"] if proxy else np.nan
            for j, probability in enumerate(self.proxy_probabilities):
                record[self._qname(probability)] = proxy["quantiles"][j] if proxy else np.nan
            if rows is not None:
                volume = (pd.to_numeric(rows.get("volume"), errors="coerce")
                          if "volume" in rows else pd.Series(np.nan, index=rows.index))
                volume = volume.where(np.isfinite(volume) & volume.ge(0))
                put_volume = float(volume[rows.type.eq("put")].sum(min_count=1))
                call_volume = float(volume[rows.type.eq("call")].sum(min_count=1))
                total_volume = put_volume+call_volume
                record["chain_log_volume"] = (np.log1p(total_volume)
                                                if np.isfinite(total_volume) else np.nan)
                record["chain_put_call_volume_imbalance"] = (
                    (put_volume-call_volume)/total_volume
                    if np.isfinite(total_volume) and total_volume > 0 else np.nan)
                record["chain_total_oi_snapshot"] = float(rows.open_interest.sum())
                for greek in ("delta", "gamma", "vega"):
                    values = (pd.to_numeric(rows.get(greek), errors="coerce")
                              if greek in rows else pd.Series(np.nan, index=rows.index))
                    valid = np.isfinite(values)
                    exposure = (abs(values[valid])*rows.loc[valid, "open_interest"]).sum(
                        min_count=1)
                    record[f"chain_log_{greek}_oi"] = (
                        np.log1p(float(exposure)) if np.isfinite(exposure) else np.nan)
                weights = volume.where(volume > 0)
                record["chain_volume_weighted_rel_spread"] = (
                    float(np.average(rows.loc[weights.notna(), "rel_spread"],
                                     weights=weights.dropna()))
                    if weights.notna().any() else np.nan)
            else:
                for name in ("chain_log_volume", "chain_put_call_volume_imbalance",
                             "chain_total_oi_snapshot", "chain_log_delta_oi",
                             "chain_log_gamma_oi", "chain_log_vega_oi",
                             "chain_volume_weighted_rel_spread"):
                    record[name] = np.nan
            records.append(record)
        result = pd.DataFrame(records).sort_values(keys).reset_index(drop=True)
        group = result.groupby(["symbol", "expiry"], sort=False)
        lag_oi = group.chain_total_oi_snapshot.shift(1)
        lag2_oi = group.chain_total_oi_snapshot.shift(2)
        result["chain_log_lag_open_interest"] = np.log1p(lag_oi)
        result["chain_log_lag_oi_change"] = np.log1p(lag_oi)-np.log1p(lag2_oi)
        return result.drop(columns=["chain_total_oi_snapshot"])

    def build_archive(self, meta, archive_root, output):
        """Scan bounded annual parquet files and atomically publish features."""
        import pyarrow.parquet as pq
        import pandas as pd

        def content_hash(path):
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(8*1024*1024), b""):
                    digest.update(block)
            return digest.hexdigest()

        columns = ["symbol", "date", "expiration", "strike", "type", "mark", "bid",
                   "ask", "bid_size", "ask_size", "open_interest", "volume",
                   "implied_volatility", "delta", "gamma", "vega"]
        parts = []
        meta = meta.copy()
        metadata_columns = ["symbol", "quote_date", "expiry", "chain_underlying_price"]
        missing_metadata = set(metadata_columns)-set(meta)
        if missing_metadata:
            raise ValueError(f"missing raw-chain metadata columns: {sorted(missing_metadata)}")
        canonical_metadata = (meta[metadata_columns]
                              .sort_values(metadata_columns[:3], kind="mergesort")
                              .reset_index(drop=True))
        metadata_sha256 = hashlib.sha256(canonical_metadata.to_json(
            orient="records", date_format="iso", double_precision=15).encode()).hexdigest()
        meta["year"] = meta.quote_date.str[:4].astype(int)
        identity = hashlib.sha256(json.dumps({
            "nodes": self.nodes, "bounds": self.bounds, "max_node_gap": self.max_node_gap,
            "probabilities": self.proxy_probabilities, "min_wing_nodes": self.min_wing_nodes,
            "max_inner_gap": self.max_inner_gap, "max_outer_gap": self.max_outer_gap,
            "metadata_sha256": metadata_sha256,
            "implementation": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }, sort_keys=True).encode()).hexdigest()[:16]
        cache = Path(str(output)+".parts")/identity
        cache.mkdir(parents=True, exist_ok=True)
        source_hashes = {}
        for (symbol, year), requested in meta.groupby(["symbol", "year"]):
            path = Path(archive_root)/symbol.lower()/f"options_{year}.parquet"
            if not path.exists():
                continue
            source_digest = content_hash(path)
            source_hashes[f"{symbol}-{year}"] = {
                "path": str(path), "sha256": source_digest,
            }
            cached = cache/f"{symbol}-{year}-{source_digest[:16]}.parquet"
            if cached.exists():
                part = pd.read_parquet(cached)
                expected = set(zip(requested.symbol, requested.quote_date, requested.expiry))
                actual = set(zip(part.symbol, part.quote_date, part.expiry))
                if actual != expected:
                    raise ValueError("cached raw-chain identities disagree")
                parts.append(part)
                continue
            wanted = set(zip(requested.quote_date, requested.expiry))
            selected = []
            parquet = pq.ParquetFile(path)
            available = set(parquet.schema_arrow.names)
            required_columns = set(columns[:11]+["implied_volatility"])
            if required_columns-available:
                raise ValueError(f"raw chain lacks required columns: "
                                 f"{sorted(required_columns-available)}")
            selected_columns = [name for name in columns if name in available]
            for batch in parquet.iter_batches(columns=selected_columns, batch_size=250_000):
                frame = batch.to_pandas()
                mask = [key in wanted for key in zip(frame.date, frame.expiration)]
                if any(mask):
                    selected.append(frame[mask])
            chain = pd.concat(selected, ignore_index=True) if selected else pd.DataFrame(columns=columns)
            part = self.transform(chain, requested)
            temporary = cached.with_suffix(".tmp")
            part.to_parquet(temporary, index=False); temporary.replace(cached)
            parts.append(part)
        result = pd.concat(parts, ignore_index=True)
        if len(result) != len(meta) or result.duplicated(["symbol", "quote_date", "expiry"]).any():
            raise ValueError("raw-chain preparation did not preserve requested identities")
        target = Path(output); target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix+".tmp")
        result.to_parquet(temporary, index=False); temporary.replace(target)
        manifest = Path(str(target)+".sources.json")
        temporary_manifest = manifest.with_suffix(manifest.suffix+".tmp")
        temporary_manifest.write_text(json.dumps({
            "metadata_columns": metadata_columns,
            "metadata_sha256": metadata_sha256,
            "sources": source_hashes,
        }, indent=2, sort_keys=True)+"\n")
        temporary_manifest.replace(manifest)
        return result


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

    @staticmethod
    def ohlc_features(prices, windows):
        """Build close-known daily range and jump proxies with backward windows."""
        import numpy as np
        import pandas as pd

        required = {"date", "open", "high", "low", "close"}
        missing = required-set(prices)
        if missing:
            raise ValueError(f"missing OHLC columns: {sorted(missing)}")
        if (not isinstance(windows, (list, tuple)) or not windows
                or any(type(w) is not int or w < 1 for w in windows)):
            raise ValueError("OHLC windows must be non-empty positive integers")
        frame = prices.sort_values("date", kind="mergesort").copy()
        values = frame[["open", "high", "low", "close"]].astype(float)
        if ((values <= 0) | ~np.isfinite(values)).any(axis=None):
            raise ValueError("OHLC values must be finite and positive")
        prior = values.close.shift(1)
        frame["overnight_return"] = np.log(values.open/prior)
        frame["intraday_return"] = np.log(values.close/values.open)
        frame["log_high_low_range"] = np.log(values.high/values.low)
        frame["parkinson_variance"] = frame.log_high_low_range**2/(4*np.log(2))
        close_variance = np.log(values.close/prior)**2
        frame["jump_variance_proxy"] = (close_variance-frame.parkinson_variance).clip(lower=0)
        for window in windows:
            frame[f"range_variance_{window}"] = frame.parkinson_variance.rolling(
                window, min_periods=window).mean()
            frame[f"jump_variance_{window}"] = frame.jump_variance_proxy.rolling(
                window, min_periods=window).mean()
            frame[f"overnight_variance_{window}"] = frame.overnight_return.pow(2).rolling(
                window, min_periods=window).mean()
            frame[f"intraday_variance_{window}"] = frame.intraday_return.pow(2).rolling(
                window, min_periods=window).mean()
        return frame.drop(columns=["open", "high", "low", "close"])

    @staticmethod
    def add_matched_dte_vrp(frame, reference_floor):
        """Add requested-session implied minus backward realized variance."""
        import numpy as np

        sessions = frame.sessions_to_expiry.astype(float)
        implied = frame.chain_atm_iv.astype(float).pow(2)*sessions/252.
        realized = frame.rv_22.astype(float).pow(2)*sessions
        floor = max(float(reference_floor), np.finfo(float).eps)**2*sessions
        frame["matched_implied_variance"] = implied
        frame["matched_trailing_variance"] = realized
        frame["matched_vrp"] = implied-realized
        frame["matched_vrp_ratio"] = implied/np.maximum(realized, floor)-1
        return frame

    @staticmethod
    def add_macro_event_features(frame, calendars):
        """Count only events whose scheduled date was known by each entry."""
        import pandas as pd

        if calendars is None:
            return frame, {"available": False,
                           "reason": "no local point-in-time macro calendar"}
        if not isinstance(calendars, dict) or not calendars:
            raise ValueError("macro event calendars must be a non-empty mapping")
        quote = pd.to_datetime(frame.quote_date)
        end = pd.to_datetime(frame.planned_settlement_date)
        total = None
        for family, records in sorted(calendars.items()):
            if not isinstance(family, str) or not family or not isinstance(records, list):
                raise ValueError("invalid macro event calendar family")
            count = pd.Series(0, index=frame.index, dtype=int)
            for record in records:
                if not isinstance(record, dict) or set(record) != {"event_date", "known_at"}:
                    raise ValueError("macro events require event_date and known_at")
                event = pd.Timestamp(record["event_date"])
                known = pd.Timestamp(record["known_at"])
                count += ((known <= quote) & (event > quote) & (event <= end)).astype(int)
            frame[f"macro_{family}_count"] = count
            frame[f"macro_{family}_inside"] = (count > 0).astype(int)
            total = count.copy() if total is None else total+count
        frame["macro_event_count"] = total
        frame["macro_any_event"] = (total > 0).astype(int)
        return frame, {"available": True, "families": sorted(calendars)}

    @staticmethod
    def dividend_window_eligibility(dividends, entry_index, end_index):
        """Return historical path completeness without filling unknown dividends."""
        import numpy as np
        values = dividends.isna().to_numpy().astype(int)
        cumulative = np.r_[0, np.cumsum(values)]
        entry = np.asarray(entry_index, dtype=int)
        end = np.asarray(end_index, dtype=int)
        return cumulative[end+1]-cumulative[entry] == 0

    @staticmethod
    def _availability_dates(observation_dates, lag_sessions=0, lag_days=0):
        """Return conservative dates after the declared publication lag."""
        import numpy as np
        import pandas as pd

        dates = pd.DatetimeIndex(pd.to_datetime(observation_dates))
        if bool(lag_sessions) == bool(lag_days):
            raise ValueError("declare exactly one of lag_sessions or lag_days")
        if lag_days:
            return dates+pd.to_timedelta(lag_days, unit="D")
        import exchange_calendars as xcals
        start = dates.min()-pd.Timedelta(days=10)
        end = dates.max()+pd.Timedelta(days=30)
        calendar = xcals.get_calendar("XNYS", start=start, end=end)
        sessions = calendar.sessions.tz_localize(None)
        positions = np.searchsorted(sessions.to_numpy(), dates.to_numpy(), side="right")
        positions = positions+lag_sessions
        if (positions >= len(sessions)).any():
            raise ValueError("exchange-session availability outside calendar")
        return pd.DatetimeIndex(sessions[positions])

    @staticmethod
    def add_surface_features(frame):
        """Add declared option-surface transforms without filling absent quotes.

        Risk-neutral surface values are causal entry-snapshot predictors only;
        they are not interpreted as physical return probabilities.
        """
        import numpy as np

        required = {
            "chain_atm_iv", "chain_put25_iv", "chain_call25_iv",
            "chain_rel_spread", "chain_put_call_oi", "chain_contracts",
            "chain_open_interest", "chain_quote_depth",
        }
        missing = required-set(frame)
        if missing:
            raise ValueError(f"missing option-surface columns: {sorted(missing)}")
        positive = ("chain_atm_iv", "chain_put25_iv", "chain_call25_iv",
                    "chain_put_call_oi")
        nonnegative = ("chain_rel_spread", "chain_contracts",
                       "chain_open_interest", "chain_quote_depth")
        for name in positive:
            values = frame[name]
            invalid = values.notna() & ((values <= 0) | ~np.isfinite(values))
            if invalid.any():
                raise ValueError(f"{name} must be positive or missing")
        for name in nonnegative:
            values = frame[name]
            invalid = values.notna() & ((values < 0) | ~np.isfinite(values))
            if invalid.any():
                raise ValueError(f"{name} must be nonnegative or missing")

        pair = frame.chain_put25_iv.notna() & frame.chain_call25_iv.notna()
        put_call = frame.chain_put_call_oi.notna()
        frame["chain_log_atm_iv"] = np.log(frame.chain_atm_iv)
        frame["chain_log_skew25"] = np.where(
            pair, np.log(frame.chain_put25_iv)-np.log(frame.chain_call25_iv), np.nan)
        frame["chain_log_curvature25"] = np.where(
            pair & frame.chain_atm_iv.notna(),
            .5*(np.log(frame.chain_put25_iv)+np.log(frame.chain_call25_iv))
            - np.log(frame.chain_atm_iv), np.nan)
        frame["chain_log_rel_spread"] = np.log1p(frame.chain_rel_spread)
        frame["chain_log_put_call_oi"] = np.log(frame.chain_put_call_oi)
        frame["chain_log_contracts"] = np.log1p(frame.chain_contracts)
        frame["chain_log_open_interest"] = np.log1p(frame.chain_open_interest)
        frame["chain_log_quote_depth"] = np.log1p(frame.chain_quote_depth)
        frame["chain_has_25d_pair"] = pair.astype(int)
        frame["chain_has_put_call_oi"] = put_call.astype(int)
        return frame

    @staticmethod
    def add_surface_dynamics(frame, proxy_probabilities):
        """Add causal same-series changes, cross-expiry slopes and proxy moments."""
        import numpy as np
        import pandas as pd

        frame = frame.sort_values(["symbol", "expiry", "quote_date"]).copy()
        group = frame.groupby(["symbol", "expiry"], sort=False)
        surface = ("chain_log_atm_iv", "chain_log_skew25", "chain_log_curvature25")
        for name in surface:
            for lag in (1, 5, 22):
                frame[f"{name}_change_{lag}"] = frame[name]-group[name].shift(lag)
        frame["chain_liquidity_asymmetry"] = np.tanh(frame.chain_log_put_call_oi)
        frame["implied_minus_realized_variance"] = (
            frame.chain_atm_iv**2/252.-frame.rv_22**2)

        frame["term_slope_prev"] = np.nan
        frame["term_slope_next"] = np.nan
        for _, index in frame.groupby(["symbol", "quote_date"], sort=False).groups.items():
            ordered = frame.loc[index].sort_values("actual_calendar_dte")
            dte = ordered.actual_calendar_dte.to_numpy(dtype=float)
            iv = ordered.chain_log_atm_iv.to_numpy(dtype=float)
            if len(ordered) > 1:
                delta = np.diff(dte)
                slope = np.divide(np.diff(iv), delta, out=np.full(len(delta), np.nan),
                                  where=delta != 0)
                frame.loc[ordered.index[1:], "term_slope_prev"] = slope
                frame.loc[ordered.index[:-1], "term_slope_next"] = slope

        qnames = [RawChainFeatureBuilder._qname(p) for p in proxy_probabilities]
        q = frame[qnames].to_numpy(dtype=float)
        grid = np.asarray(proxy_probabilities, dtype=float)
        dense_p = np.linspace(grid[0], grid[-1], 99)
        moments = np.full((len(frame), 6), np.nan)
        for row in np.flatnonzero(np.isfinite(q).all(axis=1)):
            values = np.interp(dense_p, grid, q[row])
            mean = values.mean(); centered = values-mean
            variance = np.mean(centered**2)
            if variance > 0:
                moments[row, 0:4] = [mean, variance,
                                     np.mean(centered**3)/variance**1.5,
                                     np.mean(centered**4)/variance**2]
            moments[row, 4] = np.mean(np.maximum(-values, 0))
            moments[row, 5] = np.mean(np.maximum(values, 0))
        names = ("rn_mean", "rn_variance", "rn_skewness", "rn_kurtosis",
                 "rn_left_tail_integral", "rn_right_tail_integral")
        for column, values in zip(names, moments.T):
            frame[column] = values
        return frame.sort_index()

    def _join_fred_features(self, frame, specifications):
        """Join pinned market observations at their conservative availability dates."""
        import numpy as np
        import pandas as pd

        quotes = pd.DataFrame({"quote_date": sorted(frame.quote_date.unique())})
        quotes["quote_date_dt"] = pd.to_datetime(quotes.quote_date)
        coverage = getattr(self, "market_coverage", {}).copy()
        for feature, spec in specifications.items():
            reader = ObservationRows(feature, {
                "root": self.config["root"], "source": "fred-market-features",
                "stream": spec["stream"], "key_fields": ["observation_date"],
                "ts_field": "observation_date",
            })
            records = reader.run(None, {})["records"]
            self.reader_fingerprints[f"fred:{spec['stream']}"] = reader.fingerprint()
            values = pd.DataFrame(records)[["observation_date", spec["field"]]].copy()
            values["observation_date"] = pd.to_datetime(values.observation_date)
            values[feature] = pd.to_numeric(values[spec["field"]], errors="coerce")
            values["available_date"] = self._availability_dates(
                values.observation_date, spec.get("lag_sessions", 0),
                spec.get("lag_days", 0))
            values = values.dropna(subset=[feature]).sort_values("available_date")
            joined = pd.merge_asof(quotes.sort_values("quote_date_dt"), values,
                                   left_on="quote_date_dt", right_on="available_date",
                                   direction="backward", allow_exact_matches=True)
            age = (joined.quote_date_dt-joined.observation_date).dt.days
            stale = age.isna() | age.gt(spec["max_age_days"])
            joined.loc[stale, feature] = np.nan
            joined["age_days"] = age
            mapping = joined.set_index("quote_date")
            frame[feature] = frame.quote_date.map(mapping[feature])
            frame[feature+"_age_days"] = frame.quote_date.map(mapping.age_days)
            frame[feature+"_missing"] = frame[feature].isna().astype(int)
            coverage[feature] = {
                "rows": int(frame[feature].notna().sum()),
                "fraction": float(frame[feature].notna().mean()),
                "lag_days": spec.get("lag_days", 0),
                "lag_sessions": spec.get("lag_sessions", 0),
                "age_basis": "observation_date",
                "max_age_days": spec["max_age_days"],
            }
        if {"credit_cp_nonfinancial", "credit_cp_financial"}.issubset(frame):
            frame["credit_cp_spread"] = (frame.credit_cp_financial
                                         -frame.credit_cp_nonfinancial)
            frame["credit_cp_spread_missing"] = frame.credit_cp_spread.isna().astype(int)
        self.market_coverage = coverage
        return frame

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
        if c.get("chain_features"):
            chain_features = pd.read_parquet(c["chain_features"])
            meta = meta.merge(chain_features, on=keys, validate="one_to_one")
        if c.get("surface_features", False):
            meta = self.add_surface_features(meta)
        calendar = xc.get_calendar("XNYS", start="1998-01-01", end="2026-12-31")
        sessions = calendar.sessions.tz_localize(None)
        # Fixed planned convention: regular holidays, never future ad-hoc closures.
        # Actual session dates remain the label/purge truth, not model predictors.
        planned = pd.bdate_range(sessions[0], sessions[-1], freq="C",
                                 holidays=calendar.regular_holidays.holidays(sessions[0], sessions[-1]))
        panels, refused = [], {}
        self.reader_fingerprints = {}
        for symbol, iv_symbol in c["symbols"].items():
            price_reader = IndexCloseRows("prices", {"root": c["root"], "source": c["price_source"],
                                               "since_ms": int(pd.Timestamp(c["since"]).timestamp()*1000),
                                               "symbol": symbol})
            prices = price_reader.run(None, {})["records"]
            self.reader_fingerprints[symbol] = price_reader.fingerprint()
            price_frame = pd.DataFrame(prices).set_index("date")
            ohlc = None
            if c.get("ohlc_windows"):
                ohlc = self.ohlc_features(price_frame.reset_index(), c["ohlc_windows"])
            rows = meta[meta.symbol == symbol].copy()
            expiry = pd.DatetimeIndex(pd.to_datetime(rows.expiry))
            end_index = sessions.searchsorted(expiry, side="right")-1
            rows["settlement_date"] = sessions[end_index].strftime("%Y-%m-%d")
            rows["actual_calendar_dte"] = (pd.to_datetime(rows.settlement_date)-pd.to_datetime(rows.quote_date)).dt.days
            planned_end = planned[planned.searchsorted(expiry, side="right")-1]
            rows["planned_settlement_date"] = planned_end.strftime("%Y-%m-%d")
            rows["calendar_dte"] = (pd.to_datetime(rows.planned_settlement_date)-pd.to_datetime(rows.quote_date)).dt.days
            rows = rows[(rows.actual_calendar_dte >= 1) & (rows.calendar_dte <= c["max_dte"])].copy()
            entry_index = sessions.get_indexer(pd.to_datetime(rows.quote_date))
            end_index = sessions.get_indexer(pd.to_datetime(rows.settlement_date))
            valid_dates = (entry_index >= 0) & (end_index >= 0)
            refused[symbol] = {"non_session_quote": int((~valid_dates).sum())}
            rows = rows[valid_dates].copy()
            entry_index, end_index = entry_index[valid_dates], end_index[valid_dates]
            rows["actual_sessions_to_expiry"] = end_index-entry_index
            rows["sessions_to_expiry"] = (planned.get_indexer(pd.to_datetime(rows.planned_settlement_date))
                                           - planned.get_indexer(pd.to_datetime(rows.quote_date)))
            refused[symbol]["planned_and_actual_horizons_differ"] = int(
                ((rows.actual_calendar_dte != rows.calendar_dte)
                 | (rows.actual_sessions_to_expiry != rows.sessions_to_expiry)).sum())
            # Reindex exposes missing interior observations instead of treating a gap as one day.
            closes = price_frame.close.reindex(sessions.strftime("%Y-%m-%d"))
            dividends = price_frame.dividend_amount.reindex(closes.index)
            missing = closes.isna().to_numpy().astype(int)
            rows["dividends_known"] = self.dividend_window_eligibility(
                dividends, entry_index, end_index)
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
            if ohlc is not None:
                rows = rows.merge(ohlc, left_on="quote_date", right_on="date",
                                  validate="many_to_one", suffixes=("", "_ohlc"))
                if "date_ohlc" in rows:
                    rows = rows.drop(columns=["date_ohlc"])
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
            rows["strategy_dividend_eligible"] = rows.dividends_known.astype(int)
            refused[symbol]["strategy_dividend_eligible"] = int(rows.dividends_known.sum())
            refused[symbol]["strategy_dividend_ineligible"] = int((~rows.dividends_known).sum())
            for probability in c.get("raw_chain", {}).get("proxy_probabilities", []):
                name = RawChainFeatureBuilder._qname(probability)
                rows[name] = rows[name]/rows.reference_scale
            qnames = [RawChainFeatureBuilder._qname(p)
                      for p in c.get("raw_chain", {}).get("proxy_probabilities", [])]
            if qnames:
                rows["rn_median"] = rows[qnames[len(qnames)//2]]
                rows["rn_iqr"] = rows[qnames[-2]]-rows[qnames[1]]
                rows["rn_left_width"] = rows[qnames[len(qnames)//2]]-rows[qnames[0]]
                rows["rn_right_width"] = rows[qnames[-1]]-rows[qnames[len(qnames)//2]]
            good = rows.reference_scale.notna()
            refused[symbol]["missing_reference"] = int((~good).sum())
            refused[symbol]["iv_for_train_only_imputation"] = int(rows.own_iv.isna().sum())
            panels.append(rows[good])
        self.refused = refused
        source_names = ["surface", "lifecycle"]+(["chain_features"] if c.get("chain_features") else [])
        self.source_hashes = {name: hashlib.sha256(Path(c[name]).read_bytes()).hexdigest()
                              for name in source_names}
        if c.get("chain_features"):
            manifest = Path(str(c["chain_features"])+".sources.json")
            if not manifest.exists():
                raise ValueError("raw-chain source hash manifest is missing")
            self.source_hashes["chain_feature_sources"] = hashlib.sha256(
                manifest.read_bytes()).hexdigest()
        result = pd.concat(panels, ignore_index=True)
        for symbol in c["symbols"]:
            result[f"is_{symbol}"] = (result.symbol == symbol).astype(int)
        lags = result[[f"ret_lag_{i}" for i in range(c["lags"])]].to_numpy()
        for window in (5, 22):
            values = lags[:, :window]
            result[f"momentum_{window}"] = values.sum(1)
            result[f"down_rv_{window}"] = np.sqrt(np.mean(np.minimum(values, 0)**2, axis=1))
            result[f"up_rv_{window}"] = np.sqrt(np.mean(np.maximum(values, 0)**2, axis=1))
        if c.get("market_symbols"):
            self.market_coverage = {}
            quotes = pd.DataFrame({"quote_date": sorted(result.quote_date.unique())})
            quotes["quote_date_dt"] = pd.to_datetime(quotes.quote_date)
            for feature, spec in c["market_symbols"].items():
                symbol = spec["symbol"]
                reader = IndexCloseRows(feature, {"root": c["root"], "source": c["iv_source"],
                                                   "symbol": symbol})
                records = reader.run(None, {})["records"]
                self.reader_fingerprints[symbol] = reader.fingerprint()
                values = pd.DataFrame(records)[["date", "close"]].drop_duplicates("date")
                values["date"] = pd.to_datetime(values.date)
                joined = pd.merge_asof(quotes.sort_values("quote_date_dt"), values.sort_values("date"),
                                       left_on="quote_date_dt", right_on="date",
                                       direction="backward", allow_exact_matches=False)
                mapping = dict(zip(joined.quote_date, joined.close))
                result[feature] = result.quote_date.map(mapping)
                result[feature+"_age_days"] = (pd.to_datetime(result.quote_date)
                                               -pd.to_datetime(result.quote_date.map(
                                                   dict(zip(joined.quote_date,
                                                            joined.date.dt.strftime("%Y-%m-%d")))))).dt.days
                stale = result[feature+"_age_days"].gt(spec["max_age_days"])
                result.loc[stale, feature] = np.nan
                result[feature+"_missing"] = result[feature].isna().astype(int)
                self.market_coverage[feature] = {
                    "rows": int(result[feature].notna().sum()),
                    "fraction": float(result[feature].notna().mean()),
                    "lag_days": 1, "max_age_days": spec["max_age_days"],
                }
        if c.get("fred_market_symbols"):
            result = self._join_fred_features(result, c["fred_market_symbols"])
        if c.get("surface_features") and c.get("raw_chain", {}).get("proxy_probabilities"):
            result = self.add_surface_dynamics(
                result, c["raw_chain"]["proxy_probabilities"])
        if c.get("matched_dte_vrp"):
            result = self.add_matched_dte_vrp(result, c["reference_floor"])
        result, self.macro_event_status = self.add_macro_event_features(
            result, c.get("macro_event_calendars"))
        return result


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

        result = np.zeros_like(levels, dtype=float)
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
        # Bounded wing payoff is constant outside the outer strikes. Avoid
        # exponentiating unbounded Student tails; the CDF itself is unchanged.
        bounded = np.clip(draws, self.strikes_z[0], self.strikes_z[-1])
        mean_loss = -self.payoff(spot[:, None]*np.exp(scale[:, None]*bounded), strikes).mean(1)
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
    parser.add_argument("--stage", choices=["prepare", "search", "select", "evaluate", "report"])
    parser.add_argument("--partition")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if args.stage == "prepare":
        import pandas as pd
        if args.partition:
            parser.error("prepare does not accept a partition")
        data = config["data"]
        surface = pd.read_parquet(data["surface"])
        lifecycle = pd.read_parquet(data["lifecycle"])
        meta = surface.merge(lifecycle, on=["symbol", "quote_date", "expiry"],
                             validate="one_to_one")
        builder = RawChainFeatureBuilder(**data["raw_chain"])
        rows = builder.build_archive(meta, data["archive_root"], data["chain_features"])
        print("prepared raw-chain rows", len(rows), flush=True)
        return
    adapter = ExactExpiryCDFPanel(config["data"])
    frame = adapter.read()
    provenance = {"refused": adapter.refused, "sha256": adapter.source_hashes,
                  "readers": adapter.reader_fingerprints,
                  "market_coverage": getattr(adapter, "market_coverage", {}),
                  "macro_event_status": getattr(adapter, "macro_event_status", {}),
                  "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    diagnostic = CondorCDFDiagnostic(**config["diagnostic"])
    if "experiment" in config:
        if not args.stage:
            parser.error("HPO requires an explicit --stage")
        CDFHyperparameterStudy(config).run(frame, diagnostic, stage=args.stage,
                                            partition=args.partition, provenance=provenance)
        return
    if args.stage or args.partition:
        parser.error("stage/partition require an experiment document")
    output = Path(config["study"]["output"])
    if output.exists():
        raise FileExistsError(output)
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
