"""Exact-expiry data and bounded condor diagnostics for ADR-0189.

No fit loop lives here: ChronologicalCDFStudy owns model comparison. This
adapter supplies ETF expiry/settlement semantics and calls the existing
observation, numpy feature and payoff owners.
"""

import argparse
import hashlib
import json
from pathlib import Path

from dskit.pipeline.document import date_problem
from dskit.pipeline.kinds_split import label_reaches
from dskit.pipeline.node import check_int_param
from dskit.pipeline.libs.predictive_cdf import (
    ChronologicalCDFStudy, CDFHyperparameterStudy, CDFThresholdAudit,
    DiscreteCDFGrid, GridCurve,
)
from dskit.pipeline.libs.observations import ObservationRows
from .datafiles import DataFiles, DataTree, archive_relpath, entry_problems
from .observations import IndexCloseRows
from .contracts import (CONDOR_LEGS, american_short_charge, condor_credit,
                        quote_problems)

__all__ = ["ExactExpiryCDFPanel", "FROZEN_PANEL_CONVENTIONS", "panel_convention_problems",
           "RawChainFeatureBuilder", "CondorCDFDiagnostic",
           "EligibleCondorChain", "DecisionRegionContextBuilder", "CondorDecisionAudit",
           "DecisionRegionStudy", "DecisionStrikeDiagnosisStudy",
           "CausalStrikeCDFCorrection", "AdaptiveWassersteinRadius",
           "RobustCorrectionStudy"]


class CausalStrikeCDFCorrection:
    """Monotone probability correction fitted to strictly settled strike events.

    The caller owns temporal filtering.  This class deliberately accepts only
    the already-eligible event inventory and adds an identity-map pseudo sample
    whose total weight is the declared shrinkage strength.
    """

    def __init__(self, prior_strength, knots, min_events, min_dates):
        import math

        values = (prior_strength,)
        if (any(isinstance(v, bool) or not isinstance(v, (int, float))
                or not math.isfinite(v) for v in values)
                or prior_strength < 0 or type(knots) is not int or knots < 3
                or type(min_events) is not int or min_events < 1
                or type(min_dates) is not int or min_dates < 1):
            raise ValueError("invalid strike correction settings")
        self.prior_strength = float(prior_strength)
        self.knots, self.min_events, self.min_dates = knots, min_events, min_dates
        self.x = self.y = None

    def fit(self, probabilities, events, dates, weights=None):
        """Fit the weighted isotonic map after enforcing minimum history."""
        import numpy as np
        from sklearn.isotonic import IsotonicRegression

        p = np.asarray(probabilities, dtype=float)
        y = np.asarray(events, dtype=float)
        d = np.asarray(dates)
        w = np.ones(len(p)) if weights is None else np.asarray(weights, dtype=float)
        if (p.ndim != 1 or y.shape != p.shape or d.shape != p.shape
                or w.shape != p.shape or not np.isfinite(w).all() or (w <= 0).any()
                or not np.isfinite(p).all() or not np.isfinite(y).all()
                or (p < 0).any() or (p > 1).any()
                or not np.isin(y, [0., 1.]).all()):
            raise ValueError("invalid strike-event calibration sample")
        if len(p) < self.min_events or len(np.unique(d)) < self.min_dates:
            raise ValueError("insufficient settled strike-event history")
        identity = np.linspace(0., 1., self.knots)
        # Tiny endpoint observations make the map a complete CDF even when the
        # shrinkage strength is zero.  They have no material interior weight.
        endpoint_weight = max(1e-12, self.prior_strength/max(1, self.knots))
        if self.prior_strength:
            x = np.r_[p, identity]
            target = np.r_[y, identity]
            weight = np.r_[w,
                           np.full(self.knots, self.prior_strength/self.knots)]
        else:
            x, target, weight = p, y, w
        x = np.r_[x, 0., 1.]
        target = np.r_[target, 0., 1.]
        weight = np.r_[weight, endpoint_weight, endpoint_weight]
        model = IsotonicRegression(y_min=0., y_max=1., out_of_bounds="clip")
        model.fit(x, target, sample_weight=weight)
        grid = np.unique(np.r_[0., identity, model.X_thresholds_, 1.])
        mapped = np.asarray(model.predict(grid), dtype=float)
        mapped[0], mapped[-1] = 0., 1.
        self.x, self.y = grid, np.maximum.accumulate(np.clip(mapped, 0., 1.))
        return self

    def transform(self, probabilities):
        """Map coherent base probabilities through the fitted correction."""
        import numpy as np

        if self.x is None:
            raise ValueError("strike correction is not fitted")
        p = np.asarray(probabilities, dtype=float)
        if not np.isfinite(p).all() or (p < 0).any() or (p > 1).any():
            raise ValueError("invalid CDF probabilities")
        return np.interp(p, self.x, self.y)

    def curve(self, base):
        """Return a corrected GridCurve with unchanged support knots."""
        if not isinstance(base, GridCurve):
            raise ValueError("strike correction requires a raw GridCurve")
        return GridCurve(base.values, self.transform(base.probabilities))


class AdaptiveWassersteinRadius:
    """Select the smallest W1 radius passing a causal date-block residual bound."""

    def __init__(self, radii, min_dates, block_dates, replicates, alpha, seed):
        import math

        if (not isinstance(radii, list) or not radii
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) or v < 0 for v in radii)
                or radii != sorted(set(radii))
                or type(min_dates) is not int or min_dates < 2
                or type(block_dates) is not int or not 1 <= block_dates <= min_dates
                or type(replicates) is not int or replicates < 20
                or isinstance(alpha, bool) or not 0 < alpha < 0.5
                or type(seed) is not int):
            raise ValueError("invalid adaptive radius settings")
        self.radii = tuple(float(v) for v in radii)
        self.min_dates, self.block_dates = min_dates, block_dates
        self.replicates, self.alpha, self.seed = replicates, float(alpha), seed

    def select(self, history):
        """Return a radius and multiplicity-adjusted upper bounds, or abstain."""
        import numpy as np

        required = {"quote_date", "radius", "residual"}
        if required-set(history):
            raise ValueError("radius history lacks required fields")
        if len(history) and (not np.isfinite(history.residual).all()
                             or not set(history.radius).issubset(self.radii)):
            raise ValueError("invalid radius history")
        bounds = {}
        quantile = 1.-self.alpha/len(self.radii)
        for offset, radius in enumerate(self.radii):
            rows = history[history.radius == radius]
            daily = rows.groupby("quote_date", sort=True).residual.mean().to_numpy()
            if len(daily) < self.min_dates:
                bounds[radius] = None
                continue
            rng = np.random.default_rng(self.seed+offset)
            n, width = len(daily), self.block_dates
            means = np.empty(self.replicates)
            blocks = int(np.ceil(n/width))
            for draw in range(self.replicates):
                starts = rng.integers(0, n, size=blocks)
                indices = ((starts[:, None]+np.arange(width)) % n).ravel()[:n]
                means[draw] = daily[indices].mean()
            bounds[radius] = float(np.quantile(means, quantile))
        passing = [radius for radius in self.radii
                   if bounds[radius] is not None and bounds[radius] <= 0.]
        return {"radius": (passing[0] if passing else None),
                "upper_bounds": bounds,
                "reason": (None if passing else "insufficient_or_uncovered_history")}


class EligibleCondorChain:
    """Enumerate the full declared one-lot universe from one dated snapshot.

    Annual archive quotes lack source timestamps. They support an after-close
    strike/payoff diagnostic only; an executable decision refuses explicitly.
    """

    def __init__(self, max_abs_log_moneyness, min_wing_width=None, max_wing_width=None,
                 max_candidates=None, fee_per_leg=None, multiplier=None,
                 region="condors"):
        import math

        if region not in ("condors", "span"):
            raise ValueError("invalid eligible-condor rule")
        if region == "span":
            # The span region is every quotable strike between each side's
            # extremes: no wing width, candidate cap or fee applies to it.
            if (any(v is not None for v in (min_wing_width, max_wing_width,
                                            max_candidates, fee_per_leg, multiplier))
                    or isinstance(max_abs_log_moneyness, bool)
                    or not isinstance(max_abs_log_moneyness, (int, float))
                    or not math.isfinite(max_abs_log_moneyness)
                    or max_abs_log_moneyness <= 0):
                raise ValueError("invalid eligible-condor rule")
            self.band, self.region = max_abs_log_moneyness, region
            return
        values = (max_abs_log_moneyness, min_wing_width, max_wing_width,
                  fee_per_leg, multiplier)
        if (any(isinstance(v, bool) or not isinstance(v, (int, float))
                or not math.isfinite(v) for v in values)
                or max_abs_log_moneyness <= 0 or min_wing_width <= 0
                or max_wing_width < min_wing_width or fee_per_leg < 0
                or multiplier <= 0 or type(max_candidates) is not int
                or max_candidates < 1):
            raise ValueError("invalid eligible-condor rule")
        self.band, self.region = max_abs_log_moneyness, region
        self.min_width, self.max_width = min_wing_width, max_wing_width
        self.max_candidates = max_candidates
        self.fee, self.multiplier = fee_per_leg, multiplier

    def _eligible(self, chain, spot, executable):
        """Validate one dated chain and return its quotable strikes per right and side."""
        import math

        required = {"symbol", "date", "expiration", "strike", "type",
                    "bid", "ask", "bid_size", "ask_size"}
        if required-set(chain):
            raise ValueError(f"chain lacks {sorted(required-set(chain))}")
        if (chain.empty or chain[["symbol", "date", "expiration"]].isna().any().any()
                or any(chain[field].nunique() != 1 for field in
                       ("symbol", "date", "expiration"))
                or chain.duplicated(["strike", "type"]).any()
                or not isinstance(spot, (int, float)) or not math.isfinite(spot)
                or spot <= 0):
            raise ValueError("invalid single-entry chain or spot")
        if executable:
            if "source_timestamp" not in chain:
                raise ValueError("source quote timestamp required for executable choice")
            raise ValueError("timestamp freshness and decision clock not configured")
        eligible = {"put": {"buy": [], "sell": []},
                    "call": {"buy": [], "sell": []}}
        for row in chain.itertuples(index=False):
            right, strike = row.type, row.strike
            if (right not in eligible or not isinstance(strike, (int, float))
                    or not math.isfinite(strike) or strike <= 0
                    or abs(math.log(strike / spot)) > self.band
                    or (right == "put" and strike >= spot)
                    or (right == "call" and strike <= spot)):
                continue
            for side in ("buy", "sell"):
                if not quote_problems(row.bid, row.ask, row.bid_size,
                                      row.ask_size, 1, side=side):
                    eligible[right][side].append(row)
        return eligible

    def candidates(self, chain, spot, *, executable=False):
        """Return every qualifying four-leg candidate in stable strike order."""
        if self.region != "condors":
            raise ValueError("a span rule has no condor candidates")
        eligible = self._eligible(chain, spot, executable)
        result = []
        for long_put in eligible["put"]["buy"]:
            for short_put in eligible["put"]["sell"]:
                if not (long_put.strike < short_put.strike
                        and self.min_width <= short_put.strike-long_put.strike
                        <= self.max_width):
                    continue
                for short_call in eligible["call"]["sell"]:
                    for long_call in eligible["call"]["buy"]:
                        if not (short_call.strike < long_call.strike
                                and self.min_width <= long_call.strike-short_call.strike
                                <= self.max_width):
                            continue
                        strikes = (long_put.strike, short_put.strike,
                                   short_call.strike, long_call.strike)
                        legs = (long_put, short_put, short_call, long_call)
                        credit, widths = condor_credit(
                            strikes, [(row.bid, row.ask) for row in legs])
                        if not 0 < credit < min(widths):
                            continue
                        net = credit-4*self.fee/self.multiplier
                        if net <= 0:
                            continue
                        result.append({"id": "-".join(str(k) for k in strikes),
                                       "strikes": strikes, "net_credit": float(net)})
                        if len(result) > self.max_candidates:
                            raise ValueError("candidate universe exceeds declared cap")
        return sorted(result, key=lambda item: item["strikes"])

    def span(self, chain, spot, *, executable=False):
        """Return each side's lowest-to-highest quotable strike span.

        A put wing is bought below and sold above, a call wing sold below and
        bought above, so the put span runs from the lowest strike with a valid
        ask to the highest with a valid bid, and the call span from the lowest
        valid bid to the highest valid ask. ``strikes`` lists every strike
        quotable on either side inside a span. No width, credit or pairing
        rule applies, so a gap between quoted strikes stays inside the region.
        """
        if self.region != "span":
            raise ValueError("a condor rule has no span region")
        eligible = self._eligible(chain, spot, executable)
        bounds, strikes = {}, set()
        for right, (low_side, high_side) in (("put", ("buy", "sell")),
                                             ("call", ("sell", "buy"))):
            lows = [row.strike for row in eligible[right][low_side]]
            highs = [row.strike for row in eligible[right][high_side]]
            if lows and highs and min(lows) < max(highs):
                low, high = float(min(lows)), float(max(highs))
                bounds[right] = (low, high)
                strikes |= {float(row.strike) for side in eligible[right].values()
                            for row in side if low <= row.strike <= high}
        return {"bounds": bounds, "strikes": sorted(strikes)}


class DecisionRegionContextBuilder:
    """Attach immutable actual-strike training context to exact-expiry rows.

    ``archive_root`` is a legacy directory string or a store reference
    (``{"source", "stream"}``, no ``relpath``) that ``files`` resolves. ``files`` is a
    :class:`~index_options.datafiles.DataFiles` bound to ``data.root``; without it only
    legacy paths resolve.
    """

    COLUMNS = ("symbol", "date", "expiration", "strike", "type",
               "bid", "ask", "bid_size", "ask_size")
    CLOCK = "after_date_close_indicative_not_executable"

    def __init__(self, config, files=None):
        required = {"archive_root", "chain_rule", "max_chain_rows", "clock", "limits"}
        if (not required.issubset(config)
                or set(config)-required != ({"panel_years"} if "panel_years" in config else set())
                or config["clock"] != self.CLOCK
                or type(config["max_chain_rows"]) is not int
                or config["max_chain_rows"] < 1
                or ("panel_years" in config
                    and (not isinstance(config["panel_years"], list)
                         or not config["panel_years"]
                         or any(type(year) is not int for year in config["panel_years"])
                         or config["panel_years"] != sorted(set(config["panel_years"]))))
                or set(config["limits"]) != {"max_seconds", "max_resident_mib"}
                or any(type(v) is not int or v < 1 for v in config["limits"].values())
                or config["limits"]["max_seconds"] > 1800
                or config["limits"]["max_resident_mib"] > 6144
                or entry_problems("archive_root", config["archive_root"], tree=True)):
            raise ValueError("invalid non-executable decision-context protocol")
        self.config = config
        self.rule = EligibleCondorChain(**config["chain_rule"])
        self.files = DataFiles() if files is None else files

    def build(self, panel):
        """Scan exact identities and derive all unique eligible-wing strikes."""
        import math
        import numpy as np
        import pandas as pd
        import pyarrow.parquet as pq

        identity = ["symbol", "quote_date", "expiry"]
        required = {*identity, "spot", "reference_scale"}
        if (required-set(panel) or panel.empty or panel.duplicated(identity).any()
                or not np.isfinite(panel[["spot", "reference_scale"]]).all().all()
                or (panel[["spot", "reference_scale"]] <= 0).any().any()):
            raise ValueError("invalid decision-context panel identities")
        if ("panel_years" in self.config
                and not panel.quote_date.str[:4].astype(int).isin(
                    self.config["panel_years"]).all()):
            raise ValueError("decision-context panel exceeds declared years")
        retained, sources, payloads = 0, {}, {}
        tree = self.files.tree(self.config["archive_root"])
        for (symbol, year), rows in panel.groupby(
                ["symbol", panel.quote_date.str[:4]], sort=True):
            relpath = archive_relpath(symbol, year)
            path = tree.path(relpath)
            wanted = set(zip(rows.quote_date, rows.expiry))
            parts = []
            for batch in pq.ParquetFile(path).iter_batches(
                    columns=list(self.COLUMNS), batch_size=250_000,
                    use_threads=False):
                frame = batch.to_pandas()
                mask = [(date, expiry) in wanted for date, expiry in
                        zip(frame.date, frame.expiration)]
                if any(mask):
                    chosen = frame.loc[mask]
                    retained += len(chosen)
                    if retained > self.config["max_chain_rows"]:
                        raise ValueError("decision-context matching chain rows exceed cap")
                    parts.append(chosen)
            sources[tree.label(relpath)] = tree.sha256(relpath)
            chain = (pd.concat(parts, ignore_index=True) if parts
                     else pd.DataFrame(columns=self.COLUMNS))
            grouped = {(a, b, c): snapshot for (a, b, c), snapshot in
                       chain.groupby(["symbol", "date", "expiration"], sort=False)}
            for row in rows.itertuples(index=False):
                key = (row.symbol, row.quote_date, row.expiry)
                snapshot = grouped.get(key)
                if self.rule.region == "span":
                    found = (self.rule.span(snapshot, float(row.spot))
                             if snapshot is not None else {"bounds": {}, "strikes": []})
                    strikes = found["strikes"]
                    wing_prices = sorted(found["bounds"].values())
                else:
                    candidates = (self.rule.candidates(snapshot, float(row.spot))
                                  if snapshot is not None else [])
                    strikes = sorted({float(strike) for candidate in candidates
                                      for strike in candidate["strikes"]})
                    wing_prices = sorted({tuple(pair) for candidate in candidates
                                          for pair in
                                          ((candidate["strikes"][0], candidate["strikes"][1]),
                                           (candidate["strikes"][2], candidate["strikes"][3]))})
                thresholds = [math.log(strike/float(row.spot))/float(row.reference_scale)
                              for strike in strikes]
                intervals = [[math.log(low/float(row.spot))/float(row.reference_scale),
                              math.log(high/float(row.spot))/float(row.reference_scale)]
                             for low, high in wing_prices]
                put = [i for i, strike in enumerate(strikes) if strike < row.spot]
                call = [i for i, strike in enumerate(strikes) if strike > row.spot]
                weights = np.zeros(len(strikes), dtype=float)
                if put and call:
                    weights[put], weights[call] = .5/len(put), .5/len(call)
                elif strikes:
                    weights[:] = 1./len(strikes)
                payloads[key] = {"identity": list(key), "thresholds": thresholds,
                                 "weights": weights.tolist(), "intervals": intervals,
                                 "status": ("eligible" if strikes else
                                            "no_eligible_condor"),
                                 "clock": self.CLOCK}
            del chain, grouped, parts
        provenance = hashlib.sha256(json.dumps({
            "clock": self.CLOCK, "sources": sources,
            "chain_rule": self.config["chain_rule"]}, sort_keys=True).encode()).hexdigest()
        contexts = []
        for row in panel.itertuples(index=False):
            key = (row.symbol, row.quote_date, row.expiry)
            contexts.append({**payloads[key], "provenance_sha256": provenance})
        self.provenance = {"sha256": provenance, "sources": sources,
                           "matching_chain_rows": retained,
                           "eligible_rows": sum(c["status"] == "eligible"
                                                for c in contexts)}
        return contexts

    def enforce_process_limits(self):
        """Bound wall time and RSS without blocking CUDA virtual mappings."""
        import resource
        import signal
        import time

        limits = self.config["limits"]
        requested = limits["max_resident_mib"]*1024*1024
        started = time.monotonic()
        def check_budget(_signum, _frame):
            # Linux reports ru_maxrss in KiB. CUDA reserves large virtual address
            # ranges, so RLIMIT_AS would reject a low-RSS GPU process.
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
            if rss > requested:
                raise MemoryError("decision-region zoo exceeded JSON RSS budget")
            if time.monotonic()-started > limits["max_seconds"]:
                raise TimeoutError("decision-region zoo exceeded JSON time budget")
        signal.signal(signal.SIGALRM, check_budget)
        signal.setitimer(signal.ITIMER_REAL, 1., 1.)


class CondorDecisionAudit:
    """Audit one frozen CDF against the entire entry-known candidate universe."""

    def __init__(self, mesh_tolerance, floor):
        import math

        if (not math.isfinite(mesh_tolerance) or mesh_tolerance < 0
                or not math.isfinite(floor) or floor <= 0):
            raise ValueError("invalid mesh tolerance or score floor")
        self.mesh_tolerance, self.floor = mesh_tolerance, floor

    def evaluate(self, grid, cdf, *, terminal, spot, candidates, radius,
                 american_charges=None, curve=None, reference_scale=None):
        """Return score, candidate loss errors, and nominal/robust choices."""
        import numpy as np

        if (not isinstance(candidates, (list, tuple))
                or not np.isfinite([terminal, spot]).all() or spot <= 0):
            raise ValueError("invalid outcome, spot or candidates")
        if ((curve is None) != (reference_scale is None)
                or (curve is not None and (not isinstance(curve, GridCurve)
                                           or len(curve.values) != 1))):
            raise ValueError("exact decision audit requires one frozen GridCurve")
        if any(set(item) != {"id", "strikes", "net_credit"} for item in candidates):
            raise ValueError("invalid candidate fields")
        if (american_charges is not None
                and set(american_charges) != {item["id"] for item in candidates}):
            raise ValueError("American charge inventory differs from candidates")
        intervals = sorted({pair for item in candidates
                            for pair in ((item["strikes"][0], item["strikes"][1]),
                                         (item["strikes"][2], item["strikes"][3]))})
        threshold = CDFThresholdAudit(grid, intervals, self.floor)
        distribution = DiscreteCDFGrid(grid, cdf)
        strike_set = sorted({strike for item in candidates
                             for strike in item["strikes"]})
        brier = (float(np.mean([threshold.strike_brier(cdf, k, terminal)
                                for k in strike_set])) if strike_set else None)
        records, choices = [], []
        nominal = {"id": None, "robust_value": 0., "worst_loss": 0.}
        for item in candidates:
            strikes = tuple(item["strikes"])
            if (len(strikes) != 4 or not all(a < b for a, b in
                                            zip(strikes, strikes[1:]))
                    or not threshold.grid[0] < strikes[0]
                    or not strikes[-1] < threshold.grid[-1]):
                raise ValueError("candidate strikes outside shared support")
            if curve is None:
                put_expected = threshold.expected_spread_loss(
                    cdf, strikes[0], strikes[1], "put")
                call_expected = threshold.expected_spread_loss(
                    cdf, strikes[2], strikes[3], "call")
            else:
                put_expected = threshold.gridcurve_log_spread_loss(
                    curve, 0, strikes[0], strikes[1], "put", spot, reference_scale)
                call_expected = threshold.gridcurve_log_spread_loss(
                    curve, 0, strikes[2], strikes[3], "call", spot, reference_scale)
            direct = put_expected+call_expected
            loss = -CondorCDFDiagnostic.payoff(
                threshold.grid[None, :], np.asarray(strikes)[None, :])[0]
            grid_loss = float(distribution.masses @ loss)
            gap = abs(direct - grid_loss)
            if gap > self.mesh_tolerance:
                raise ValueError(f"mesh expected-loss gap {gap} exceeds tolerance")
            realized = float(-CondorCDFDiagnostic.payoff(
                np.asarray([[terminal]]), np.asarray(strikes)[None, :])[0, 0])
            put_realized = max(strikes[1]-terminal, 0)-max(strikes[0]-terminal, 0)
            call_realized = max(terminal-strikes[2], 0)-max(terminal-strikes[3], 0)
            charge = (american_charges[item["id"]]
                      if american_charges is not None else None)
            if charge is not None and (not np.isfinite(charge) or charge < 0):
                raise ValueError("invalid American charge")
            records.append({"id": item["id"], "expected_loss": direct,
                            "realized_loss": realized, "loss_bias": direct-realized,
                            "abs_error": abs(direct-realized),
                            "squared_error": (direct-realized)**2,
                            "put_expected_loss": put_expected,
                            "put_realized_loss": put_realized,
                            "put_abs_error": abs(put_expected-put_realized),
                            "call_expected_loss": call_expected,
                            "call_realized_loss": call_realized,
                            "call_abs_error": abs(call_expected-call_realized),
                            "mesh_gap": gap, "american_charge": charge})
            choices.append({"id": item["id"], "net_credit": item["net_credit"],
                            "loss": loss})
            direct_value = item["net_credit"]-direct
            if direct_value > nominal["robust_value"]:
                nominal = {"id": item["id"], "robust_value": direct_value,
                           "worst_loss": direct}
        if radius == 0:
            robust = nominal.copy()
        else:
            grid_nominal = distribution.choose(choices, radius=0., spot=spot)
            if grid_nominal["id"] != nominal["id"]:
                raise ValueError("mesh changes nominal candidate ranking")
            robust = distribution.choose(choices, radius=radius, spot=spot)
        complete_charges = all(record["american_charge"] is not None
                               for record in records)
        realized_pnl = ({item["id"]: item["net_credit"]-record["realized_loss"]
                         -record["american_charge"]
                         for item, record in zip(candidates, records)}
                        if complete_charges else {})
        oracle = max([0., *realized_pnl.values()]) if complete_charges else None
        for selection in (nominal, robust):
            selection["realized_pnl"] = (realized_pnl[selection["id"]]
                                         if selection["id"] is not None
                                         and complete_charges else
                                         (0. if complete_charges else None))
            selection["regret"] = (oracle-selection["realized_pnl"]
                                   if complete_charges else None)
        midpoint_cdf = (None if curve is None else
                        curve.cdf(np.log(threshold.midpoints/spot)/reference_scale)[0])
        return {"strike_brier": brier, "unique_wing_templates": len(intervals),
                "weighted_crps": threshold.weighted_crps(
                    cdf, terminal, midpoint_cdf=midpoint_cdf),
                "candidates": records, "nominal": nominal, "robust": robust}


class DecisionRegionStudy:
    """JSON-driven posthoc audit of saved OOF curves on archived chain strikes.

    ``archive_root`` is a legacy directory string or a store reference
    (``{"source", "stream"}``, no ``relpath``) resolved against ``underlying.root``,
    the onboarding store this document already names.
    """

    KEYS = {"forecast_root", "partition", "models", "symbols", "archive_root",
            "output", "max_rows_per_symbol", "chain_rule", "audit",
            "underlying", "strata", "limits", "bootstrap", "notes"}
    AUDIT_KEYS = {"price_step", "support_margin_fraction", "mesh_tolerance",
                  "floor", "radius", "score_refinement_factor", "score_tolerance"}
    CHAIN_COLUMNS = ("symbol", "date", "expiration", "strike", "type",
                     "bid", "ask", "bid_size", "ask_size")
    IDENTITY = ("symbol", "quote_date", "expiry")

    def __init__(self, config):
        import math

        if (set(config)-self.KEYS or self.KEYS-{"notes", "bootstrap"}-set(config)
                or set(config["audit"]) != self.AUDIT_KEYS
                or not config["models"] or not config["symbols"]
                or len(config["symbols"]) != len(set(config["symbols"]))
                or type(config["max_rows_per_symbol"]) is not int
                or config["max_rows_per_symbol"] < 1):
            raise ValueError("invalid decision-region JSON document")
        bootstrap = config.get("bootstrap")
        if bootstrap is not None and (
                set(bootstrap) != {"reference_model", "metrics", "blocks",
                                   "replicates", "alpha", "seed"}
                or bootstrap["reference_model"] not in config["models"]
                or not bootstrap["metrics"]
                or not set(bootstrap["metrics"]).issubset(
                    {"weighted_crps_refined", "strike_brier", "loss_mse"})
                or not bootstrap["blocks"]
                or any(type(value) is not int or value < 1
                       for value in bootstrap["blocks"])
                or type(bootstrap["replicates"]) is not int
                or bootstrap["replicates"] < 1
                or isinstance(bootstrap["alpha"], bool)
                or not isinstance(bootstrap["alpha"], (int, float))
                or not 0 < bootstrap["alpha"] < 1
                or type(bootstrap["seed"]) is not int):
            raise ValueError("invalid decision-region bootstrap")
        if any(variant != "raw" for variant in config["models"].values()):
            raise ValueError("decision-region pilot requires raw GridCurve variants")
        limits = config["limits"]
        if (set(limits) != {"max_seconds", "max_address_space_mib",
                            "max_chain_rows", "max_grid_nodes"}
                or any(type(v) is not int or v < 1 for v in limits.values())
                or limits["max_seconds"] > 1800
                or limits["max_address_space_mib"] > 6144
                or limits["max_grid_nodes"] < 2):
            raise ValueError("invalid decision-region limits")
        audit = config["audit"]
        if (any(isinstance(v, bool) or not isinstance(v, (int, float))
                or not math.isfinite(v) for v in audit.values())
                or audit["price_step"] <= 0 or audit["support_margin_fraction"] <= 0
                or audit["mesh_tolerance"] < 0 or audit["floor"] <= 0
                or audit["radius"] < 0
                or type(audit["score_refinement_factor"]) is not int
                or audit["score_refinement_factor"] < 2
                or audit["score_tolerance"] < 0):
            raise ValueError("invalid decision-region audit settings")
        self.config = config
        self.rule = EligibleCondorChain(**config["chain_rule"])
        if (set(config["underlying"]) != {"root", "source", "since_ms", "carry_rate"}
                or not math.isfinite(config["underlying"]["carry_rate"])
                or config["underlying"]["carry_rate"] < 0):
            raise ValueError("invalid underlying/assignment source")
        if (set(config["strata"]) != {"tenor_days", "iv", "wing_log_moneyness"}
                or any(len(bounds) != 2 or not all(math.isfinite(v) and v > 0
                                                   for v in bounds)
                       or bounds[0] >= bounds[1]
                       for bounds in config["strata"].values())):
            raise ValueError("invalid frozen reporting strata")
        problems = entry_problems("archive_root", config["archive_root"], tree=True)
        if problems:
            raise ValueError(f"invalid decision-region archive_root: {'; '.join(problems)}")
        self.output = Path(config["output"])

    def _enforce_limits(self):
        """Apply the JSON budget inside the WSL stage process."""
        import resource
        import signal

        limits = self.config["limits"]
        requested = limits["max_address_space_mib"]*1024*1024
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS,
                           (min(requested, soft) if soft != resource.RLIM_INFINITY
                            else requested, hard))
        def timed_out(_signum, _frame):
            raise TimeoutError("decision stage exceeded JSON time budget")
        signal.signal(signal.SIGALRM, timed_out)
        signal.setitimer(signal.ITIMER_REAL, limits["max_seconds"])

    def _band(self, name, value):
        import math

        if not math.isfinite(value):
            return "missing"
        low, high = self.config["strata"][name]
        return "low" if value <= low else "middle" if value <= high else "high"

    @staticmethod
    def _digest(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(8*1024*1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _curve_path(self, symbol, model):
        variant = self.config["models"][model]
        if variant not in ("raw", "calibrated"):
            raise ValueError("invalid frozen variant")
        return (Path(self.config["forecast_root"])/"evaluate"/
                self.config["partition"]/
                f"{symbol}-{model}-{variant}-curves.npz")

    def _config_digest(self):
        return hashlib.sha256(json.dumps(self.config, sort_keys=True,
                                         allow_nan=False).encode()).hexdigest()

    @staticmethod
    def _check_curve_archive(path):
        import numpy as np

        with np.load(path, allow_pickle=False) as archive:
            if str(archive["kind"]) != "grid" or "calibration_x" in archive:
                raise ValueError("decision-region pilot requires raw GridCurve archives")

    def _verified_partition(self, partition):
        marker = partition/"complete.json"
        try:
            record = json.loads(marker.read_text())
            selected_path = Path(self.config["forecast_root"])/"selection"/"selected.json"
            selected = json.loads(selected_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("invalid frozen completion or selection record") from exc
        if (record.get("stage") != "evaluate"
                or record.get("partition") != self.config["partition"]
                or not isinstance(record.get("identity"), dict)
                or record.get("selection_hash") != self._digest(selected_path)
                or not isinstance(record.get("files"), dict)
                or any(selected.get("variants", {}).get(model) != variant
                       for model, variant in self.config["models"].items())):
            raise ValueError("frozen completion or model selection mismatch")
        paths = [partition/"input_panel.parquet", partition/"scores.parquet",
                 *(self._curve_path(symbol, model)
                   for symbol in self.config["symbols"]
                   for model in self.config["models"])]
        for path in paths:
            if record["files"].get(path.name) != self._digest(path):
                raise ValueError(f"frozen completion hash mismatch: {path.name}")
        for path in paths[2:]:
            self._check_curve_archive(path)
        return {str(path): self._digest(path) for path in
                [marker, selected_path, *paths]}

    @staticmethod
    def validate_panel_temporal(panel):
        """Refuse rows whose entry, expiry and actual settlement clocks differ."""
        import numpy as np
        import pandas as pd
        import exchange_calendars as xc
        from dskit.pipeline.libs.predictive_cdf import _validate_temporal_frame

        required = {"quote_date", "expiry", "settlement_date", "actual_calendar_dte",
                    "spot", "terminal_price", "reference_scale"}
        if required-set(panel) or panel.empty:
            raise ValueError("missing temporal settlement fields")
        _validate_temporal_frame(panel, "quote_date", "settlement_date")
        expiry = pd.to_datetime(panel.expiry, format="%Y-%m-%d", errors="coerce")
        if (expiry.isna().any() or not expiry.dt.strftime("%Y-%m-%d").eq(panel.expiry).all()
                or not (expiry > pd.to_datetime(panel.quote_date)).all()):
            raise ValueError("invalid declared expiry")
        calendar = xc.get_calendar("XNYS", start=panel.quote_date.min(),
                                   end=(expiry.max()+pd.Timedelta(days=10)))
        sessions = calendar.sessions.tz_localize(None)
        expected = sessions[sessions.searchsorted(expiry, side="right")-1]
        actual = pd.to_datetime(panel.settlement_date)
        days = (actual-pd.to_datetime(panel.quote_date)).dt.days
        if (not (expected == actual.to_numpy()).all()
                or not np.array_equal(days.to_numpy(),
                                      panel.actual_calendar_dte.to_numpy())
                or not np.isfinite(panel[["spot", "terminal_price",
                                          "reference_scale"]]).all().all()
                or (panel[["spot", "terminal_price", "reference_scale"]] <= 0).any().any()):
            raise ValueError("settlement date, DTE or outcome disagrees")

    def prepare(self):
        """Pin a deterministic paired forecast sample and its dated raw chain."""
        import numpy as np
        import pandas as pd
        import pyarrow.parquet as pq

        stage = self.output/"prepare"
        if stage.exists():
            raise FileExistsError(stage)
        partition = Path(self.config["forecast_root"])/"evaluate"/self.config["partition"]
        sources = self._verified_partition(partition)
        panel_path = partition/"input_panel.parquet"
        panel = pd.read_parquet(panel_path)
        self.validate_panel_temporal(panel)
        if panel.duplicated(list(self.IDENTITY)).any():
            raise ValueError("duplicate forecast panel identity")
        targets = []
        for symbol in self.config["symbols"]:
            paired = None
            for model in self.config["models"]:
                path = self._curve_path(symbol, model)
                with np.load(path, allow_pickle=False) as archive:
                    identities = archive["identities"]
                if identities.ndim != 2 or identities.shape[1] != len(self.IDENTITY):
                    raise ValueError("invalid saved forecast identities")
                keys = [tuple(row) for row in identities]
                if len(keys) != len(set(keys)):
                    raise ValueError("duplicate saved forecast identity")
                if paired is not None and set(keys) != paired:
                    raise ValueError("models lack identical OOF forecast identities")
                paired = set(keys)
                sources[str(path)] = self._digest(path)
            ordered = sorted(paired)
            count = min(len(ordered), self.config["max_rows_per_symbol"])
            indices = np.linspace(0, len(ordered)-1, count, dtype=int)
            targets.extend(ordered[i] for i in indices)
        target_set = set(targets)
        selected = panel.set_index(list(self.IDENTITY)).loc[sorted(target_set)].reset_index()
        if len(selected) != len(target_set):
            raise ValueError("saved forecast identities absent from input panel")
        parts = []
        retained = 0
        files = DataFiles(self.config["underlying"]["root"])
        tree = files.tree(self.config["archive_root"])
        for (symbol, year), group in selected.groupby(
                ["symbol", selected.quote_date.str[:4]], sort=True):
            relpath = archive_relpath(symbol, year)
            source = tree.path(relpath)
            wanted = set(zip(group.quote_date, group.expiry))
            for batch in pq.ParquetFile(source).iter_batches(
                    columns=list(self.CHAIN_COLUMNS), batch_size=250_000):
                frame = batch.to_pandas()
                mask = [(date, expiry) in wanted for date, expiry in
                        zip(frame.date, frame.expiration)]
                if any(mask):
                    chosen = frame.loc[mask]
                    retained += len(chosen)
                    if retained > self.config["limits"]["max_chain_rows"]:
                        raise ValueError("matching chain rows exceed JSON cap")
                    parts.append(chosen)
            sources[str(source)] = tree.sha256(relpath)
        chain = (pd.concat(parts, ignore_index=True) if parts
                 else pd.DataFrame(columns=self.CHAIN_COLUMNS))
        stage.mkdir(parents=True)
        selected.to_parquet(stage/"panel.parquet", index=False)
        chain.to_parquet(stage/"chain.parquet", index=False)
        manifest = {"config_sha256": self._config_digest(), "sources": sources,
                    "implementation_sha256": self._digest(__file__),
                    "selected_rows": len(selected), "chain_rows": len(chain),
                    "panel_sha256": self._digest(stage/"panel.parquet"),
                    "chain_sha256": self._digest(stage/"chain.parquet"),
                    "clock": "after_date_close_indicative_not_executable"}
        if files.provenance():
            manifest["store"] = files.provenance()
        (stage/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
        return manifest

    def _price_grid(self, strikes, spot, *, step=None):
        import numpy as np

        settings = self.config["audit"]
        lower = max(np.finfo(float).tiny, min(strikes)-
                    settings["support_margin_fraction"]*spot)
        upper = max(strikes)+settings["support_margin_fraction"]*spot
        step = settings["price_step"] if step is None else step
        regular = np.linspace(lower, upper, max(2, int(np.ceil((upper-lower)/step))+1))
        grid = np.unique(np.r_[regular, strikes])
        if len(grid) > self.config["limits"]["max_grid_nodes"]:
            raise ValueError("price grid exceeds declared implementation cap")
        return grid

    @staticmethod
    def check_weighted_score_refinement(rows, tolerance):
        """Refuse a local-score quadrature that changes ranking when refined."""
        import numpy as np

        required = {"model", "weighted_crps", "weighted_crps_refined"}
        if (required-set(rows) or not np.isfinite(rows[["weighted_crps",
                                                         "weighted_crps_refined"]]).all().all()
                or not np.isfinite(tolerance) or tolerance < 0):
            raise ValueError("invalid weighted-score refinement evidence")
        error = abs(rows.weighted_crps_refined-rows.weighted_crps)
        if (error > tolerance).any():
            raise ValueError("weighted-score quadrature refinement exceeds tolerance")
        coarse = rows.groupby("model").weighted_crps.mean().sort_values(kind="mergesort")
        refined = rows.groupby("model").weighted_crps_refined.mean().sort_values(kind="mergesort")
        if list(coarse.index) != list(refined.index):
            raise ValueError("weighted-score ranking changes after refinement")

    def evaluate(self):
        """Score frozen models on identical entry-known candidates and rows."""
        import numpy as np
        import pandas as pd

        stage = self.output/"evaluate"
        if stage.exists():
            raise FileExistsError(stage)
        prepared = self.output/"prepare"
        manifest = json.loads((prepared/"manifest.json").read_text())
        if (manifest["config_sha256"] != self._config_digest()
                or manifest.get("implementation_sha256") != self._digest(__file__)):
            raise ValueError("decision-region JSON changed after preparation")
        for source, digest in manifest["sources"].items():
            if self._digest(source) != digest:
                raise ValueError("frozen source changed after preparation")
        for name in ("panel", "chain"):
            if self._digest(prepared/f"{name}.parquet") != manifest[f"{name}_sha256"]:
                raise ValueError("prepared decision input changed")
        panel = pd.read_parquet(prepared/"panel.parquet")
        self.validate_panel_temporal(panel)
        chain = pd.read_parquet(prepared/"chain.parquet")
        partition = Path(self.config["forecast_root"])/"evaluate"/self.config["partition"]
        frozen_scores = pd.read_parquet(partition/"scores.parquet")
        score_keys = [*self.IDENTITY, "model", "variant"]
        if (frozen_scores.duplicated(score_keys).any()
                or not {"crps", "raw_return_crps"}.issubset(frozen_scores)):
            raise ValueError("invalid frozen full-curve scores")
        score_lookup = frozen_scores.set_index(score_keys)
        grouped = {(key[0], key[1], key[2]): part for key, part in
                   chain.groupby(["symbol", "date", "expiration"], sort=False)}
        identities = [tuple(row) for row in panel[list(self.IDENTITY)].itertuples(
            index=False, name=None)]
        close_rows, close_fingerprints = {}, {}
        source = self.config["underlying"]
        for symbol in self.config["symbols"]:
            reader = IndexCloseRows("decision_close", {
                "root": source["root"], "source": source["source"],
                "since_ms": source["since_ms"], "symbol": symbol})
            close_rows[symbol] = reader.run(None, {})["records"]
            close_fingerprints[symbol] = reader.fingerprint()
        closes = {symbol: {item["date"]: item for item in rows}
                  for symbol, rows in close_rows.items()}
        inventory = {}
        charges = {}
        for key, row in zip(identities, panel.itertuples(index=False)):
            entry_close = closes[key[0]].get(key[1])
            settled_close = closes[key[0]].get(row.settlement_date)
            if (entry_close is None or settled_close is None
                    or abs(entry_close["close"]-row.spot) > 1e-8
                    or abs(settled_close["close"]-row.terminal_price) > 1e-8):
                raise ValueError("settlement or entry price disagrees with frozen closes")
            rows = grouped.get(key)
            inventory[key] = (self.rule.candidates(rows, float(row.spot))
                              if rows is not None else [])
            if not bool(row.dividends_known):
                charges[key] = {item["id"]: None for item in inventory[key]}
                continue
            window = [item for item in close_rows[key[0]]
                      if key[1] <= item["date"] <= row.settlement_date]
            charges[key] = {
                item["id"]: american_short_charge(
                    window, item["strikes"][1], item["strikes"][2],
                    key[1], row.settlement_date, source["carry_rate"],
                    self.rule.multiplier)["total_usd"]/self.rule.multiplier
                for item in inventory[key]}
        records, candidate_records = [], []
        scorer = CondorDecisionAudit(self.config["audit"]["mesh_tolerance"],
                                     self.config["audit"]["floor"])
        for symbol in self.config["symbols"]:
            keys = [key for key in identities if key[0] == symbol]
            for model in self.config["models"]:
                with np.load(self._curve_path(symbol, model), allow_pickle=False) as archive:
                    lookup = {tuple(row): i for i, row in enumerate(archive["identities"])}
                    if not set(keys).issubset(lookup):
                        raise ValueError("prepared identity absent from saved model")
                    for key in keys:
                        row = panel.loc[(panel.symbol == key[0])
                                        & (panel.quote_date == key[1])
                                        & (panel.expiry == key[2])].iloc[0]
                        candidates = inventory[key]
                        if not candidates:
                            records.append({**dict(zip(self.IDENTITY, key)),
                                            "model": model, "candidates": 0,
                                            "reason": "no_eligible_condor"})
                            continue
                        strikes = sorted({strike for candidate in candidates
                                          for strike in candidate["strikes"]})
                        grid = self._price_grid(strikes, float(row.spot))
                        curve = CDFHyperparameterStudy.restore_curve(
                            archive, [lookup[key]])
                        price_z = np.log(grid/float(row.spot))/float(row.reference_scale)
                        cdf = curve.cdf(price_z)[0]
                        result = scorer.evaluate(
                            grid, cdf, terminal=float(row.terminal_price),
                            spot=float(row.spot), candidates=candidates,
                            radius=self.config["audit"]["radius"],
                            american_charges=charges[key], curve=curve,
                            reference_scale=float(row.reference_scale))
                        refinement = self.config["audit"]["score_refinement_factor"]
                        refined_grid = self._price_grid(
                            strikes, float(row.spot),
                            step=self.config["audit"]["price_step"]/refinement)
                        refined_cdf = curve.cdf(
                            np.log(refined_grid/float(row.spot))/float(row.reference_scale))[0]
                        intervals = sorted({pair for item in candidates
                                            for pair in ((item["strikes"][0], item["strikes"][1]),
                                                         (item["strikes"][2], item["strikes"][3]))})
                        refined_score = CDFThresholdAudit(
                            refined_grid, intervals, self.config["audit"]["floor"]
                        ).weighted_crps(refined_cdf, float(row.terminal_price))
                        score_key = (*key, model, self.config["models"][model])
                        if score_key not in score_lookup.index:
                            raise ValueError("missing paired frozen full-curve score")
                        saved = score_lookup.loc[score_key]
                        tenor = int(row.calendar_dte)
                        tenor_band = self._band("tenor_days", tenor)
                        regime = self._band("iv", float(row.own_iv))
                        for candidate, result_row in zip(candidates, result["candidates"]):
                            short_put, short_call = candidate["strikes"][1:3]
                            distance = max(abs(np.log(short_put/row.spot)),
                                           abs(np.log(short_call/row.spot)))
                            candidate_records.append({
                                **dict(zip(self.IDENTITY, key)), "model": model,
                                "tenor_band": tenor_band, "regime": regime,
                                "wing_region": self._band("wing_log_moneyness", distance),
                                "wing_distance": distance,
                                "net_credit": candidate["net_credit"],
                                **result_row})
                        records.append({**dict(zip(self.IDENTITY, key)), "model": model,
                                        "candidates": len(candidates), "reason": None,
                                        "requested_tenor_days": tenor,
                                        "tenor_band": tenor_band,
                                        "entry_iv": float(row.own_iv),
                                        "regime": regime,
                                        "full_crps_standardized": float(saved.crps),
                                        "full_crps_log_return": float(saved.raw_return_crps),
                                        "weighted_crps": result["weighted_crps"],
                                        "weighted_crps_refined": refined_score,
                                        "weighted_crps_quadrature_error": abs(
                                            refined_score-result["weighted_crps"]),
                                        "strike_brier": result["strike_brier"],
                                        "unique_wing_templates": result["unique_wing_templates"],
                                        "loss_bias": float(np.mean([x["loss_bias"] for x in
                                                                    result["candidates"]])),
                                        "loss_mae": float(np.mean([x["abs_error"] for x in
                                                                   result["candidates"]])),
                                        "loss_mse": float(np.mean([x["squared_error"] for x in
                                                                   result["candidates"]])),
                                        "put_spread_mae": float(np.mean([x["put_abs_error"] for x in
                                                                         result["candidates"]])),
                                        "call_spread_mae": float(np.mean([x["call_abs_error"] for x in
                                                                          result["candidates"]])),
                                        "nominal_id": result["nominal"]["id"],
                                        "nominal_regret": result["nominal"]["regret"],
                                        "robust_id": result["robust"]["id"],
                                        "robust_regret": result["robust"]["regret"],
                                        "american_charge_status": (
                                            "evaluated" if bool(row.dividends_known)
                                            else "unknown_dividend_window"),
                                        "max_mesh_gap": max(x["mesh_gap"] for x in
                                                            result["candidates"])})
                print("decision-region", symbol, model, len(keys), flush=True)
        score_rows = pd.DataFrame(records)
        eligible = score_rows.loc[score_rows.reason.isna()]
        self.check_weighted_score_refinement(
            eligible, self.config["audit"]["score_tolerance"])
        stage.mkdir(parents=True)
        import pyarrow as pa
        import pyarrow.parquet as pq

        score_table = pa.Table.from_pandas(
            score_rows, preserve_index=False, nthreads=1)
        pq.write_table(score_table, stage/"scores.parquet")
        writer = None
        try:
            for offset in range(0, len(candidate_records), 100_000):
                table = pa.Table.from_pylist(candidate_records[offset:offset+100_000])
                if writer is None:
                    writer = pq.ParquetWriter(stage/"candidate_scores.parquet",
                                              table.schema)
                writer.write_table(table)
        finally:
            if writer is not None:
                writer.close()
        if writer is None:
            raise ValueError("decision-region audit produced no candidate scores")
        result = {"config_sha256": self._config_digest(), "rows": len(records),
                  "implementation_sha256": self._digest(__file__),
                  "scores_sha256": self._digest(stage/"scores.parquet"),
                  "candidate_scores_sha256": self._digest(stage/"candidate_scores.parquet"),
                  "underlying_fingerprints": close_fingerprints}
        (stage/"manifest.json").write_text(json.dumps(result, indent=2)+"\n")
        return result

    def report(self):
        """Summarize the frozen audit without changing its paired row universe."""
        import pandas as pd

        stage = self.output/"report"
        if stage.exists():
            raise FileExistsError(stage)
        evaluated = self.output/"evaluate"
        manifest = json.loads((evaluated/"manifest.json").read_text())
        if (manifest["config_sha256"] != self._config_digest()
                or manifest.get("implementation_sha256") != self._digest(__file__)):
            raise ValueError("decision-region JSON changed before report")
        for name in ("scores", "candidate_scores"):
            if self._digest(evaluated/f"{name}.parquet") != manifest[f"{name}_sha256"]:
                raise ValueError("decision score artifact changed before report")
        rows = pd.read_parquet(evaluated/"scores.parquet")
        candidates = pd.read_parquet(evaluated/"candidate_scores.parquet")
        metrics = ["full_crps_standardized", "weighted_crps", "strike_brier",
                   "loss_bias", "loss_mae", "loss_mse", "put_spread_mae",
                   "call_spread_mae", "nominal_regret", "robust_regret"]
        by_entry = rows.groupby(["symbol", "tenor_band", "regime", "model"],
                                dropna=False)[metrics].agg(["mean", "count"])
        by_wing = candidates.groupby(["symbol", "tenor_band", "regime",
                                      "wing_region", "model"], dropna=False)[
                                          ["abs_error", "put_abs_error", "call_abs_error",
                                           "loss_bias", "american_charge"]].agg(["mean", "count"])
        stage.mkdir(parents=True)
        by_entry.to_csv(stage/"by_entry.csv")
        by_wing.to_csv(stage/"by_wing.csv")
        intervals = []
        if "bootstrap" in self.config:
            intervals = self.paired_block_intervals(rows, self.config["bootstrap"])
            (stage/"paired_block_intervals.json").write_text(
                json.dumps(intervals, indent=2, allow_nan=False)+"\n")
        summary = {"config_sha256": self._config_digest(),
                   "eligible_rows": int(rows.reason.isna().sum()),
                   "total_rows": len(rows),
                   "candidate_rows": len(candidates),
                   "unique_eligible_forecasts": int(rows.loc[rows.reason.isna(),
                                                               list(self.IDENTITY)].drop_duplicates().shape[0]),
                   "unique_wing_templates": int(rows.loc[rows.reason.isna(),
                                                         "unique_wing_templates"].sum()
                                                / len(self.config["models"])),
                   "charge_status": rows.american_charge_status.value_counts(
                       dropna=False).to_dict(),
                   "by_entry_sha256": self._digest(stage/"by_entry.csv"),
                   "by_wing_sha256": self._digest(stage/"by_wing.csv"),
                   "paired_block_intervals": intervals}
        if intervals:
            summary["paired_block_intervals_sha256"] = self._digest(
                stage/"paired_block_intervals.json")
        (stage/"summary.json").write_text(json.dumps(summary, indent=2)+"\n")
        return summary

    @staticmethod
    def paired_block_intervals(rows, spec):
        """Return paired circular moving-date-block skill intervals."""
        import numpy as np
        import pandas as pd

        identity = list(DecisionRegionStudy.IDENTITY)
        required = {*identity, "model", "reason", *spec["metrics"]}
        eligible = rows.loc[rows.reason.isna()].copy()
        if required-set(rows) or eligible.empty:
            raise ValueError("invalid paired decision-region scores")
        models = sorted(eligible.model.unique())
        reference = spec["reference_model"]
        if reference not in models:
            raise ValueError("missing paired decision-region reference")
        dates = sorted(eligible.quote_date.unique())
        if any(width > len(dates) for width in spec["blocks"]):
            raise ValueError("decision-region block exceeds available dates")
        alpha = float(spec["alpha"])
        records = []
        for metric in spec["metrics"]:
            wide = eligible.pivot(index=identity, columns="model", values=metric)
            if (set(wide.columns) != set(models) or wide.isna().any().any()
                    or not np.isfinite(wide.to_numpy()).all()):
                raise ValueError("decision-region comparison rows are not paired")
            frame = wide.reset_index()
            symbols = sorted(frame.symbol.unique())
            arrays = {}
            for symbol in symbols:
                group = frame.loc[frame.symbol.eq(symbol)]
                grouped = group.groupby("quote_date", sort=False)
                count = grouped.size().reindex(dates, fill_value=0).to_numpy(float)
                arrays[symbol] = {
                    model: grouped[model].sum().reindex(dates, fill_value=0).to_numpy(float)
                    for model in models}
                arrays[symbol]["count"] = count
            for model in models:
                if model == reference:
                    continue
                per_symbol = {}
                for symbol in symbols:
                    values = arrays[symbol]
                    if values["count"].sum() <= 0 or values[reference].sum() <= 0:
                        raise ValueError("decision-region symbol lacks reference scores")
                    per_symbol[symbol] = 100*(1-
                        (values[model].sum()/values["count"].sum())
                        /(values[reference].sum()/values["count"].sum()))
                point = float(np.mean(list(per_symbol.values())))
                for width in spec["blocks"]:
                    rng = np.random.default_rng(spec["seed"])
                    draws = []
                    for _ in range(spec["replicates"]):
                        starts = rng.integers(0, len(dates),
                                              size=int(np.ceil(len(dates)/width)))
                        idx = ((starts[:, None]+np.arange(width)) % len(dates)
                               ).ravel()[:len(dates)]
                        skills = []
                        for symbol in symbols:
                            values = arrays[symbol]
                            count = values["count"][idx].sum()
                            reference_sum = values[reference][idx].sum()
                            if count <= 0 or reference_sum <= 0:
                                raise ValueError("bootstrap draw lacks paired symbol rows")
                            skills.append(100*(1-
                                (values[model][idx].sum()/count)
                                /(reference_sum/count)))
                        draws.append(float(np.mean(skills)))
                    lo, hi = np.quantile(draws, [alpha/2, 1-alpha/2])
                    records.append({
                        "metric": metric, "model": model,
                        "reference": reference, "forecast_rows": len(wide),
                        "dates": len(dates), "symbols": symbols,
                        "block_dates": width, "replicates": spec["replicates"],
                        "point_skill_pct": point, "lo_skill_pct": float(lo),
                        "hi_skill_pct": float(hi),
                        "skill_by_symbol_pct": per_symbol})
        return records


class RobustCorrectionStudy:
    """JSON-driven causal strike correction and adaptive robust condor study."""

    KEYS = {"decision_root", "forecast_root", "partition", "base_model",
            "base_variant", "output", "selection_cutoff", "optimization_cutoff", "correction",
            "radius_calibration", "audit", "limits", "notes"}
    CORRECTION_KEYS = {"prior_strengths", "knots", "min_events", "min_dates",
                       "samples", "tail_intervals", "tail_tolerance"}
    RADIUS_KEYS = {"radii", "min_dates", "block_dates", "replicates", "alpha",
                   "seed"}
    AUDIT_KEYS = {"price_step", "support_margin_fraction", "mesh_tolerance",
                  "floor", "max_grid_nodes"}
    IDENTITY = ("symbol", "quote_date", "expiry")

    def __init__(self, config):
        import math

        if (set(config) != self.KEYS or set(config["correction"]) != self.CORRECTION_KEYS
                or set(config["radius_calibration"]) != self.RADIUS_KEYS
                or set(config["audit"]) != self.AUDIT_KEYS
                or config["base_variant"] != "raw"):
            raise ValueError("invalid robust-correction JSON document")
        correction, audit, limits = (config["correction"], config["audit"],
                                     config["limits"])
        strengths = correction["prior_strengths"]
        if (not isinstance(strengths, list) or not strengths
                or strengths != sorted(set(strengths))
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) or v < 0 for v in strengths)
                or type(correction["samples"]) is not int
                or correction["samples"] < 101
                or not isinstance(correction["tail_intervals"], list)
                or not correction["tail_intervals"]
                or any(len(v) != 2 or not v[0] < v[1]
                       for v in correction["tail_intervals"])
                or not math.isfinite(correction["tail_tolerance"])
                or correction["tail_tolerance"] < 0):
            raise ValueError("invalid correction inventory")
        # Constructors provide the rest of the parameter validation.
        for strength in strengths:
            CausalStrikeCDFCorrection(strength, correction["knots"],
                                      correction["min_events"],
                                      correction["min_dates"])
        self.radius_owner = AdaptiveWassersteinRadius(**config["radius_calibration"])
        if (any(isinstance(audit[k], bool) or not isinstance(audit[k], (int, float))
                or not math.isfinite(audit[k]) for k in
                ("price_step", "support_margin_fraction", "mesh_tolerance", "floor"))
                or audit["price_step"] <= 0 or audit["support_margin_fraction"] <= 0
                or audit["mesh_tolerance"] < 0 or audit["floor"] <= 0
                or type(audit["max_grid_nodes"]) is not int
                or audit["max_grid_nodes"] < 2
                or set(limits) != {"max_seconds", "max_address_space_mib"}
                or any(type(v) is not int or v < 1 for v in limits.values())
                or limits["max_seconds"] > 1800
                or limits["max_address_space_mib"] > 6144):
            raise ValueError("invalid correction audit or limits")
        if (not isinstance(config["selection_cutoff"], str)
                or not isinstance(config["optimization_cutoff"], str)
                or config["selection_cutoff"] >= config["optimization_cutoff"]):
            raise ValueError("correction and optimization cutoffs must be ordered")
        self.config, self.output = config, Path(config["output"])

    @staticmethod
    def _digest(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(8*1024*1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _config_digest(self):
        return hashlib.sha256(json.dumps(self.config, sort_keys=True,
                                         allow_nan=False).encode()).hexdigest()

    def _enforce_limits(self):
        import resource
        import signal

        limit = self.config["limits"]
        requested = limit["max_address_space_mib"]*1024*1024
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS,
                           (min(requested, soft) if soft != resource.RLIM_INFINITY
                            else requested, hard))
        signal.signal(signal.SIGALRM,
                      lambda _signal, _frame: (_ for _ in ()).throw(
                          TimeoutError("robust-correction stage exceeded JSON budget")))
        signal.setitimer(signal.ITIMER_REAL, limit["max_seconds"])

    def _inputs(self):
        import pandas as pd

        root = Path(self.config["decision_root"])
        prepare, evaluated = root/"prepare", root/"evaluate"
        p_manifest = json.loads((prepare/"manifest.json").read_text())
        e_manifest = json.loads((evaluated/"manifest.json").read_text())
        paths = {"panel": prepare/"panel.parquet", "chain": prepare/"chain.parquet",
                 "scores": evaluated/"scores.parquet",
                 "candidate_scores": evaluated/"candidate_scores.parquet"}
        if (self._digest(paths["panel"]) != p_manifest["panel_sha256"]
                or self._digest(paths["chain"]) != p_manifest["chain_sha256"]
                or self._digest(paths["scores"]) != e_manifest["scores_sha256"]
                or self._digest(paths["candidate_scores"])
                != e_manifest["candidate_scores_sha256"]):
            raise ValueError("decision-region source hash mismatch")
        panel = pd.read_parquet(paths["panel"])
        scores = pd.read_parquet(paths["scores"])
        candidates = pd.read_parquet(paths["candidate_scores"])
        DecisionRegionStudy.validate_panel_temporal(panel)
        model = self.config["base_model"]
        scores = scores[(scores.model == model) & scores.reason.isna()].copy()
        candidates = candidates[candidates.model == model].copy()
        if (scores.empty or candidates.empty
                or scores.duplicated(list(self.IDENTITY)).any()
                or candidates.duplicated([*self.IDENTITY, "id"]).any()):
            raise ValueError("missing or duplicate base decision rows")
        return panel, scores, candidates, {str(p): self._digest(p)
                                           for p in paths.values()}

    def _curve_path(self, symbol):
        return (Path(self.config["forecast_root"])/"evaluate"/
                self.config["partition"]/
                f"{symbol}-{self.config['base_model']}-{self.config['base_variant']}-curves.npz")

    def _price_grid(self, strikes, spot):
        import numpy as np

        audit = self.config["audit"]
        lower = max(np.finfo(float).tiny,
                    min(strikes)-audit["support_margin_fraction"]*spot)
        upper = max(strikes)+audit["support_margin_fraction"]*spot
        grid = np.unique(np.r_[np.linspace(
            lower, upper,
            max(2, int(np.ceil((upper-lower)/audit["price_step"]))+1)), strikes])
        if len(grid) > audit["max_grid_nodes"]:
            raise ValueError("correction grid exceeds declared cap")
        return grid

    @staticmethod
    def _strikes(identifier):
        try:
            strikes = tuple(float(value) for value in str(identifier).split("-"))
        except ValueError as exc:
            raise ValueError("invalid frozen condor identifier") from exc
        if len(strikes) != 4 or not all(a < b for a, b in zip(strikes, strikes[1:])):
            raise ValueError("invalid frozen condor strikes")
        return strikes

    @staticmethod
    def _label(strength):
        return "prior_"+format(float(strength), ".12g").replace(".", "p")

    @staticmethod
    def _settled_events(history, quote_date, tenor_band):
        """Return only prior-date observations settled before this batch."""
        return [item for item in history
                if item["quote_date"] < quote_date
                and item["settlement_date"] < quote_date
                and item["tenor_band"] == tenor_band]

    @staticmethod
    def _radius_history(policies, quote_date, symbol, tenor_band, start):
        """Return the independent post-selection, pre-decision residual band."""
        import pandas as pd

        rows = [item for item in policies
                if start <= item["quote_date"] < quote_date
                and item["settlement_date"] < quote_date
                and item["symbol"] == symbol
                and item["tenor_band"] == tenor_band]
        return pd.DataFrame(rows, columns=["quote_date", "settlement_date",
                                           "symbol", "tenor_band", "radius",
                                           "residual"])

    def train(self):
        """Fit causal rowwise maps and precompute every frozen radius policy."""
        import numpy as np
        import pandas as pd
        from dskit.pipeline.libs.predictive_cdf import ChronologicalCDFStudy

        stage = self.output/"train"
        if stage.exists():
            raise FileExistsError(stage)
        panel, score_source, candidate_source, sources = self._inputs()
        panel_lookup = panel.set_index(list(self.IDENTITY))
        correction = self.config["correction"]
        scorer = CondorDecisionAudit(self.config["audit"]["mesh_tolerance"],
                                     self.config["audit"]["floor"])
        score_rows, event_rows = [], []
        labels = [("identity", None), *[(self._label(v), float(v))
                                        for v in correction["prior_strengths"]]]
        for symbol in sorted(score_source.symbol.unique()):
            curve_path = self._curve_path(symbol)
            sources[str(curve_path)] = self._digest(curve_path)
            with np.load(curve_path, allow_pickle=False) as archive:
                if str(archive["kind"]) != "grid" or "calibration_x" in archive:
                    raise ValueError("robust correction requires raw GridCurve archive")
                lookup = {tuple(row): i for i, row in enumerate(archive["identities"])}
                symbol_scores = score_source[score_source.symbol == symbol].sort_values(
                    ["quote_date", "expiry"])
                history = []
                for quote_date, dated in symbol_scores.groupby("quote_date", sort=True):
                    pending = []
                    for source_row in dated.itertuples(index=False):
                        key = tuple(getattr(source_row, name) for name in self.IDENTITY)
                        if key not in lookup or key not in panel_lookup.index:
                            raise ValueError("source identity absent from raw curve or panel")
                        row = panel_lookup.loc[key]
                        selected = candidate_source
                        for name, value in zip(self.IDENTITY, key):
                            selected = selected[selected[name] == value]
                        if selected.empty:
                            raise ValueError("eligible decision row lacks candidates")
                        candidates = [{"id": item.id,
                                       "strikes": self._strikes(item.id),
                                       "net_credit": float(item.net_credit)}
                                      for item in selected.itertuples(index=False)]
                        charges = {item.id: (None if pd.isna(item.american_charge)
                                             else float(item.american_charge))
                                   for item in selected.itertuples(index=False)}
                        base = CDFHyperparameterStudy.restore_curve(
                            archive, [lookup[key]])
                        unique_strikes = sorted({strike for item in candidates
                                                 for strike in item["strikes"]})
                        strike_z = np.log(np.asarray(unique_strikes)/float(row.spot))/float(
                            row.reference_scale)
                        base_probability = base.cdf(strike_z)[0]
                        event = (float(row.terminal_price) <= np.asarray(unique_strikes)).astype(float)
                        eligible_history = self._settled_events(
                            history, quote_date, source_row.tenor_band)
                        history_frame = pd.DataFrame(eligible_history)
                        grid = self._price_grid(unique_strikes, float(row.spot))
                        y = float(row.terminal_return/row.reference_scale)
                        for label, strength in labels:
                            if strength is None:
                                curve, training_events, training_dates = base, 0, 0
                            else:
                                try:
                                    owner = CausalStrikeCDFCorrection(
                                        strength, correction["knots"],
                                        correction["min_events"], correction["min_dates"])
                                    owner.fit(history_frame.probability.to_numpy(),
                                              history_frame.event.to_numpy(),
                                              history_frame.quote_date.to_numpy(),
                                              history_frame.weight.to_numpy())
                                    curve = owner.curve(base)
                                    training_events = len(history_frame)
                                    training_dates = history_frame.quote_date.nunique()
                                except (AttributeError, ValueError):
                                    score_rows.append({**dict(zip(self.IDENTITY, key)),
                                                       "settlement_date": row.settlement_date,
                                                       "tenor_band": source_row.tenor_band,
                                                       "label": label, "available": False,
                                                       "reason": "insufficient_settled_history"})
                                    continue
                            cdf = curve.cdf(np.log(grid/float(row.spot))/float(
                                row.reference_scale))[0]
                            result = scorer.evaluate(
                                grid, cdf, terminal=float(row.terminal_price),
                                spot=float(row.spot), candidates=candidates, radius=0.,
                                american_charges=charges, curve=curve,
                                reference_scale=float(row.reference_scale))
                            metrics, _ = ChronologicalCDFStudy.scores(
                                curve, np.asarray([y]), correction["samples"],
                                correction["tail_intervals"], correction["samples"])
                            score_rows.append({**dict(zip(self.IDENTITY, key)),
                                               "settlement_date": row.settlement_date,
                                               "tenor_band": source_row.tenor_band,
                                               "label": label, "available": True,
                                               "reason": None,
                                               "training_events": training_events,
                                               "training_dates": training_dates,
                                               "weighted_crps": result["weighted_crps"],
                                               "strike_brier": result["strike_brier"],
                                               "full_crps": float(metrics["crps"][0]),
                                               "below_05": float(metrics["below_05"][0]),
                                               "above_95": float(metrics["above_95"][0]),
                                               "unique_wing_templates": result[
                                                   "unique_wing_templates"]})
                        weight = 1./len(unique_strikes)
                        pending.extend({"quote_date": quote_date,
                                        "settlement_date": row.settlement_date,
                                        "tenor_band": source_row.tenor_band,
                                        "probability": float(probability),
                                        "event": float(outcome), "weight": weight}
                                       for probability, outcome in
                                       zip(base_probability, event))
                    # Same-date outcomes cannot calibrate another forecast on that date.
                    history.extend(pending)
                    event_rows.extend({"symbol": symbol, **item} for item in pending)
            print("robust-correction train", symbol, len(symbol_scores), flush=True)
        stage.mkdir(parents=True)
        score_frame = pd.DataFrame(score_rows)
        score_frame.to_parquet(stage/"scores.parquet", index=False)
        pd.DataFrame(event_rows).to_parquet(stage/"events.parquet", index=False)
        manifest = {"config_sha256": self._config_digest(), "sources": sources,
                    "implementation_sha256": self._digest(__file__),
                    "score_rows": len(score_frame),
                    "event_rows": len(event_rows),
                    "scores_sha256": self._digest(stage/"scores.parquet"),
                    "events_sha256": self._digest(stage/"events.parquet")}
        (stage/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
        return manifest

    def _verified_train(self):
        import pandas as pd

        stage = self.output/"train"
        record = json.loads((stage/"manifest.json").read_text())
        if record["config_sha256"] != self._config_digest():
            raise ValueError("correction JSON changed after training")
        if record.get("implementation_sha256") != self._digest(__file__):
            raise ValueError("correction implementation changed after training")
        for name in ("scores", "events"):
            if self._digest(stage/f"{name}.parquet") != record[f"{name}_sha256"]:
                raise ValueError("correction training artifact changed")
        return pd.read_parquet(stage/"scores.parquet"), record

    def select(self):
        """Freeze the correction using only the predeclared selection period."""
        stage = self.output/"selection"
        if stage.exists():
            raise FileExistsError(stage)
        scores, train_record = self._verified_train()
        scores = scores[(scores.available) &
                        (scores.quote_date < self.config["selection_cutoff"])]
        labels = [self._label(v) for v in self.config["correction"]["prior_strengths"]]
        corrected = scores[scores.label.isin(labels)]
        common = set.intersection(*[set(map(tuple, corrected[corrected.label == label][
            list(self.IDENTITY)].to_numpy())) for label in labels])
        common &= set(map(tuple, scores[scores.label == "identity"][
            list(self.IDENTITY)].to_numpy()))
        if not common:
            raise ValueError("no paired correction-selection identities")
        paired = scores[[tuple(row) in common for row in scores[list(self.IDENTITY)].to_numpy()]]
        reference = paired[paired.label == "identity"]
        tolerance = self.config["correction"]["tail_tolerance"]
        evidence, feasible = [], []
        for label in labels:
            candidate = paired[paired.label == label]
            passed, guards = True, []
            for symbol in sorted(reference.symbol.unique()):
                for metric in ("below_05", "above_95"):
                    a = float(candidate[candidate.symbol == symbol][metric].mean())
                    b = float(reference[reference.symbol == symbol][metric].mean())
                    ok = abs(a-.05) <= abs(b-.05)+tolerance
                    passed &= ok
                    guards.append({"symbol": symbol, "metric": metric,
                                   "candidate": a, "reference": b,
                                   "passed": bool(ok)})
            mean = float(candidate.weighted_crps.mean())
            evidence.append({"label": label, "weighted_crps": mean,
                             "tail_guard": bool(passed), "guards": guards})
            if passed:
                feasible.append((mean, label))
        baseline = float(reference.weighted_crps.mean())
        winner = min(feasible) if feasible else (baseline, "identity")
        if winner[0] >= baseline:
            winner = (baseline, "identity")
        record = {"config_sha256": self._config_digest(),
                  "train_manifest_sha256": self._digest(self.output/"train"/"manifest.json"),
                  "selection_cutoff": self.config["selection_cutoff"],
                  "paired_identities": len(common), "baseline_weighted_crps": baseline,
                  "selected_label": winner[1], "selected_weighted_crps": winner[0],
                  "evidence": evidence}
        stage.mkdir(parents=True)
        (stage/"selected.json").write_text(json.dumps(record, indent=2)+"\n")
        return record

    def optimize(self):
        """Apply the frozen correction and causally selected W1 radius."""
        import numpy as np
        import pandas as pd

        stage = self.output/"optimize"
        if stage.exists():
            raise FileExistsError(stage)
        self._verified_train()
        selected_path = self.output/"selection"/"selected.json"
        selected = json.loads(selected_path.read_text())
        if (selected["config_sha256"] != self._config_digest()
                or selected["train_manifest_sha256"]
                != self._digest(self.output/"train"/"manifest.json")):
            raise ValueError("invalid frozen correction selection")
        panel, score_source, candidate_source, _ = self._inputs()
        panel_lookup = panel.set_index(list(self.IDENTITY))
        label = selected["selected_label"]
        strength_lookup = {self._label(v): float(v)
                           for v in self.config["correction"]["prior_strengths"]}
        if label != "identity" and label not in strength_lookup:
            raise ValueError("selected correction absent from frozen inventory")
        strength = strength_lookup.get(label)
        scorer = CondorDecisionAudit(self.config["audit"]["mesh_tolerance"],
                                     self.config["audit"]["floor"])
        policies, decisions = [], []
        for symbol in sorted(score_source.symbol.unique()):
            with np.load(self._curve_path(symbol), allow_pickle=False) as archive:
                lookup = {tuple(row): i for i, row in enumerate(archive["identities"])}
                history_events = []
                symbol_scores = score_source[score_source.symbol == symbol].sort_values(
                    ["quote_date", "expiry"])
                for quote_date, dated in symbol_scores.groupby("quote_date", sort=True):
                    pending = []
                    for source_row in dated.itertuples(index=False):
                        key = tuple(getattr(source_row, name) for name in self.IDENTITY)
                        row = panel_lookup.loc[key]
                        selected_candidates = candidate_source
                        for name, value in zip(self.IDENTITY, key):
                            selected_candidates = selected_candidates[
                                selected_candidates[name] == value]
                        candidates = [{"id": item.id,
                                       "strikes": self._strikes(item.id),
                                       "net_credit": float(item.net_credit)}
                                      for item in selected_candidates.itertuples(index=False)]
                        charges = {item.id: (None if pd.isna(item.american_charge)
                                             else float(item.american_charge))
                                   for item in selected_candidates.itertuples(index=False)}
                        base = CDFHyperparameterStudy.restore_curve(
                            archive, [lookup[key]])
                        strikes = sorted({strike for item in candidates
                                          for strike in item["strikes"]})
                        strike_z = np.log(np.asarray(strikes)/float(row.spot))/float(
                            row.reference_scale)
                        base_probability = base.cdf(strike_z)[0]
                        events = (float(row.terminal_price)
                                  <= np.asarray(strikes)).astype(float)
                        if strength is None:
                            curve = base
                        else:
                            settled = pd.DataFrame(self._settled_events(
                                history_events, quote_date, source_row.tenor_band))
                            try:
                                owner = CausalStrikeCDFCorrection(
                                    strength, self.config["correction"]["knots"],
                                    self.config["correction"]["min_events"],
                                    self.config["correction"]["min_dates"])
                                owner.fit(settled.probability.to_numpy(),
                                          settled.event.to_numpy(),
                                          settled.quote_date.to_numpy(),
                                          settled.weight.to_numpy())
                                curve = owner.curve(base)
                            except (AttributeError, ValueError):
                                curve = None
                        if (quote_date >= self.config["selection_cutoff"]
                                and curve is not None and all(value is not None
                                                     for value in charges.values())):
                            grid = self._price_grid(strikes, float(row.spot))
                            cdf = curve.cdf(np.log(grid/float(row.spot))/float(
                                row.reference_scale))[0]
                            nominal = scorer.evaluate(
                                grid, cdf, terminal=float(row.terminal_price),
                                spot=float(row.spot), candidates=candidates, radius=0.,
                                american_charges=charges, curve=curve,
                                reference_scale=float(row.reference_scale))
                            distribution = DiscreteCDFGrid(grid, cdf)
                            realized = {record["id"]: record["realized_loss"]
                                        for record in nominal["candidates"]}
                            realized_pnl = {item["id"]: item["net_credit"]
                                            -realized[item["id"]]-charges[item["id"]]
                                            for item in candidates}
                            oracle = max([0., *realized_pnl.values()])
                            choices = [{"id": item["id"],
                                        "net_credit": item["net_credit"],
                                        "loss": -CondorCDFDiagnostic.payoff(
                                            grid[None, :],
                                            np.asarray(item["strikes"])[None, :])[0]}
                                       for item in candidates]
                            current = []
                            for radius in self.radius_owner.radii:
                                choice = distribution.choose(
                                    choices, radius=radius, spot=float(row.spot))
                                actual_loss = (0. if choice["id"] is None
                                               else realized[choice["id"]])
                                pnl = (0. if choice["id"] is None
                                       else realized_pnl[choice["id"]])
                                item = {**dict(zip(self.IDENTITY, key)),
                                        "settlement_date": row.settlement_date,
                                        "tenor_band": source_row.tenor_band,
                                        "label": label, "radius": radius,
                                        "selection_id": choice["id"],
                                        "robust_value": choice["robust_value"],
                                        "worst_loss": choice["worst_loss"],
                                        "realized_loss": actual_loss,
                                        "residual": actual_loss-choice["worst_loss"],
                                        "realized_pnl": pnl, "regret": oracle-pnl,
                                        "oracle_pnl": oracle}
                                policies.append(item)
                                current.append(item)
                            if quote_date >= self.config["optimization_cutoff"]:
                                history = self._radius_history(
                                    policies, quote_date, symbol,
                                    source_row.tenor_band,
                                    self.config["selection_cutoff"])
                                radius_result = self.radius_owner.select(history)
                                radius = radius_result["radius"]
                                choice = next((item for item in current
                                               if item["radius"] == radius), None)
                                reason = (radius_result["reason"] if choice is None
                                          else ("robust_no_trade"
                                                if choice["selection_id"] is None else None))
                                decisions.append({**dict(zip(self.IDENTITY, key)),
                                                  "radius": radius,
                                                  "selection_id": (None if choice is None
                                                                   else choice["selection_id"]),
                                                  "realized_pnl": (0. if choice is None
                                                                   else choice["realized_pnl"]),
                                                  "regret": (oracle if choice is None
                                                             else choice["regret"]),
                                                  "reason": reason,
                                                  "upper_bounds": json.dumps(
                                                      radius_result["upper_bounds"])})
                        weight = 1./len(strikes)
                        pending.extend({"quote_date": quote_date,
                                        "settlement_date": row.settlement_date,
                                        "tenor_band": source_row.tenor_band,
                                        "probability": float(probability),
                                        "event": float(event), "weight": weight}
                                       for probability, event in
                                       zip(base_probability, events))
                    history_events.extend(pending)
            print("robust-correction optimize", symbol, len(symbol_scores), flush=True)
        frame = pd.DataFrame(decisions)
        stage.mkdir(parents=True)
        pd.DataFrame(policies).to_parquet(stage/"policies.parquet", index=False)
        frame.to_parquet(stage/"decisions.parquet", index=False)
        record = {"config_sha256": self._config_digest(),
                  "implementation_sha256": self._digest(__file__),
                  "selection_sha256": self._digest(selected_path),
                  "rows": len(frame), "trades": int(frame.selection_id.notna().sum()),
                  "robust_no_trades": int((frame.reason == "robust_no_trade").sum()),
                  "unsupported": int((frame.reason ==
                                      "insufficient_or_uncovered_history").sum()),
                  "mean_realized_pnl": float(frame.realized_pnl.mean()) if len(frame) else None,
                  "mean_regret": float(frame.regret.mean()) if len(frame) else None,
                  "policies_sha256": self._digest(stage/"policies.parquet"),
                  "decisions_sha256": self._digest(stage/"decisions.parquet")}
        (stage/"summary.json").write_text(json.dumps(record, indent=2)+"\n")
        return record

    def _dividend_limitations(self):
        """One limitation line naming the symbols whose prepared rows have unknown dividends."""
        import pandas as pd

        panel = pd.read_parquet(Path(self.config["decision_root"])/"prepare"/"panel.parquet")
        if "dividends_known" not in panel:
            return []
        symbols = sorted(panel.symbol[~panel.dividends_known.astype(bool)].unique())
        if not symbols:
            return []
        verb = "is" if len(symbols) == 1 else "are"
        return [f"{', '.join(symbols)} {verb} excluded when dividend windows are unknown"]

    def report(self):
        """Create one immutable machine-readable final summary."""
        stage = self.output/"report"
        if stage.exists():
            raise FileExistsError(stage)
        selected = json.loads((self.output/"selection"/"selected.json").read_text())
        optimized = json.loads((self.output/"optimize"/"summary.json").read_text())
        if optimized["selection_sha256"] != self._digest(
                self.output/"selection"/"selected.json"):
            raise ValueError("optimizer selection binding changed")
        result = {"status": "offline_after_close_research_only",
                  "selected_correction": selected["selected_label"],
                  "baseline_weighted_crps": selected["baseline_weighted_crps"],
                  "selected_weighted_crps": selected["selected_weighted_crps"],
                  "paired_selection_identities": selected["paired_identities"],
                  "optimization": optimized,
                  "limitations": ["date-only chain quotes are not executable",
                                  "reused historical sample is not a fresh holdout",
                                  *self._dividend_limitations()]}
        stage.mkdir(parents=True)
        (stage/"summary.json").write_text(json.dumps(result, indent=2)+"\n")
        return result


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
        from dskit.pipeline.libs.predictive_cdf import OptionPriceCDF
        proxy = OptionPriceCDF(
            self.proxy_probabilities, 1, self.min_wing_nodes,
            self.max_inner_gap, self.max_outer_gap).estimate(self._usable_quotes(rows), spot)
        if not proxy["eligible"]:
            return None
        import numpy as np
        return {k: np.asarray(proxy[k]) if k == "quantiles" else proxy[k]
                for k in ("forward_ratio", "projection_distance", "mass", "quantiles")}

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
        """Scan bounded annual parquet files and atomically publish features.

        ``archive_root`` is a legacy directory or a
        :class:`~index_options.datafiles.DataTree` (``DataFiles.tree`` resolves a store
        reference to one). A store tree takes its file digests from the snapshot manifest
        and names the snapshot in the ``.sources.json`` sidecar.
        """
        import pyarrow.parquet as pq
        import pandas as pd

        tree = (archive_root if isinstance(archive_root, DataTree)
                else DataFiles().tree(archive_root))
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
            relpath = archive_relpath(symbol, year)
            if not tree.has(relpath):
                continue
            path = tree.path(relpath)
            source_digest = tree.sha256(relpath)
            source_hashes[f"{symbol}-{year}"] = {
                "path": tree.label(relpath), "sha256": source_digest,
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
        sidecar = {"metadata_columns": metadata_columns,
                   "metadata_sha256": metadata_sha256,
                   "sources": source_hashes}
        if tree.store is not None:
            sidecar["store"] = tree.store
        temporary_manifest.write_text(json.dumps(sidecar, indent=2, sort_keys=True)+"\n")
        temporary_manifest.replace(manifest)
        return result


# The conventions the frozen pre-workflow configs were written under. A config that
# declares a key overrides it; the workflow always declares every key (pinned by
# tests/test_study_literals.py), so no new config relies on this table.
FROZEN_PANEL_CONVENTIONS = {
    "reference_window": 22, "change_lags": (1, 5, 22), "directional_windows": (5, 22),
    "periods_per_year": 252, "calendar": "XNYS", "calendar_pad_days": 30}


def _int_list_problems(problems, name, values, allowed=None, upper=None):
    """Append what is wrong with a non-empty list of distinct positive ints."""
    if (not isinstance(values, (list, tuple)) or not values
            or any(type(v) is not int or v < 1 for v in values)
            or len(set(values)) != len(values)):
        problems.append(f"{name} must be a non-empty list of distinct ints >= 1, got {values!r}")
        return
    if allowed is not None and not set(values) <= set(allowed):
        problems.append(f"{name} {list(values)} must be members of windows {list(allowed)}")
    if upper is not None and max(values) > upper:
        problems.append(f"{name} {list(values)} exceeds lags {upper}")


def panel_convention_problems(config):
    """List what is wrong with the study-convention keys of a panel config.

    Parameters
    ----------
    config : dict
        A panel config; absent convention keys take ``FROZEN_PANEL_CONVENTIONS``.
        ``reference_window`` and every ``change_lags`` member must be in ``windows``,
        every ``directional_windows`` member at most ``lags``; ``periods_per_year``
        is a positive number, ``calendar`` an exchange-calendars name and
        ``calendar_pad_days`` an int >= 0; ``dividend_field`` (optional) names the
        price column holding dividends, none declared meaning dividends are known zero.

    Returns
    -------
    list of str
        Every problem; empty when the keys are usable.
    """
    got = {k: config.get(k, v) for k, v in FROZEN_PANEL_CONVENTIONS.items()}
    windows, lags, problems = config.get("windows"), config.get("lags"), []
    _int_list_problems(problems, "directional_windows", got["directional_windows"],
                           upper=lags if type(lags) is int else None)
    _int_list_problems(problems, "change_lags", got["change_lags"], allowed=windows)
    if got["reference_window"] not in (windows or ()):
        problems.append(f"reference_window {got['reference_window']!r} must be a member of "
                        f"windows {windows!r}")
    ppy = got["periods_per_year"]
    if isinstance(ppy, bool) or not isinstance(ppy, (int, float)) or not ppy > 0:
        problems.append(f"periods_per_year must be a number > 0, got {ppy!r}")
    if not isinstance(got["calendar"], str) or not got["calendar"]:
        problems.append(f"calendar must be a non-empty string, got {got['calendar']!r}")
    pad = got["calendar_pad_days"]
    if type(pad) is not int or pad < 0:
        problems.append(f"calendar_pad_days must be an int >= 0, got {pad!r}")
    field = config.get("dividend_field")
    if "dividend_field" in config and (not isinstance(field, str) or not field):
        problems.append(f"dividend_field must be a non-empty string, got {field!r}")
    return problems


class ExactExpiryCDFPanel:
    """Join observed listed expiries to complete exchange-session price paths.

    Parameters
    ----------
    config : dict
        Store ``root``, surface/lifecycle files, symbols, volatility-index mapping,
        max DTE, lag/window counts and reference floor. ``surface``, ``lifecycle``,
        ``chain_features`` (and ``decision_regions.archive_root``) each name an onboarded
        file by store reference (``{"source", "stream", "relpath"[, "manifest_sha256"]}``,
        resolved against ``root``) or, legacy, by path string. Optional ``exact_dte``
        (int >= 1) keeps only rows whose ``actual_calendar_dte`` equals it;
        absent keeps every horizon up to ``max_dte``.
        Study conventions (see ``panel_convention_problems``): ``reference_window``,
        ``change_lags``, ``directional_windows``, ``periods_per_year``, ``calendar``,
        ``calendar_pad_days``, and ``dividend_field`` (absent: dividends known zero).
    holdout_start : str or None
        ISO date of a locked holdout (a fold-table study's own value). Rows
        dated on or after it, or whose label reaches it, never enter the panel.

    Examples
    --------
    Read a configured panel::

        panel = ExactExpiryCDFPanel(config).read()
    """

    def __init__(self, config, holdout_start=None):
        if holdout_start is not None and date_problem(holdout_start):
            raise ValueError("holdout_start must be an ISO date or None")
        if "exact_dte" in config:
            problems = []
            check_int_param(problems, "exact_dte", config["exact_dte"], ge=1)
            if problems:
                raise ValueError(f"exact_dte must be an integer >= 1 or absent: {problems}")
        self.config, self.holdout_start = config, holdout_start

    def _resolve_conventions(self):
        """Return the convention keys (declared or frozen), refusing a malformed set."""
        problems = panel_convention_problems(self.config)
        if problems:
            raise ValueError("; ".join(problems))
        return {name: self._convention(name) for name in FROZEN_PANEL_CONVENTIONS}

    def _convention(self, name):
        """One convention key: the config's value, else the frozen-config one."""
        return self.config.get(name, FROZEN_PANEL_CONVENTIONS[name])

    @staticmethod
    def _within_horizon(meta, max_dte, pad_days):
        """Keep rows whose expiry can fall inside the cohort (``max_dte`` plus the pad).

        A further expiry can never pass the ``calendar_dte <= max_dte`` cut, and keeping it
        would stretch the session calendar to the furthest listed expiry.
        """
        import pandas as pd

        days = (pd.to_datetime(meta.expiry)-pd.to_datetime(meta.quote_date)).dt.days
        kept = meta[days <= max_dte+pad_days]
        if kept.empty:
            raise ValueError("no surface row expires within max_dte plus calendar_pad_days")
        return kept

    def _dividend_series(self, price_frame, index):
        """Dividends on ``index``; NaN is unknown, and no declared field means known zero."""
        import pandas as pd

        field = self.config.get("dividend_field")
        if field is None:
            return pd.Series(0.0, index=index)
        if field not in price_frame:
            raise ValueError(f"dividend_field {field!r} is not a column of the price source")
        return price_frame[field].reindex(index)

    def _horizon_cohort(self, rows):
        """Keep the rows at ``exact_dte`` when declared, else every row."""
        if "exact_dte" not in self.config:
            return rows
        return rows[rows.actual_calendar_dte == self.config["exact_dte"]]

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
    def add_matched_dte_vrp(frame, reference_floor, reference_window, periods_per_year):
        """Add requested-session implied minus backward realized variance.

        Parameters
        ----------
        frame : DataFrame
            Rows with ``chain_atm_iv``, ``sessions_to_expiry`` and ``rv_<reference_window>``.
        reference_floor : float
            Floor of the realized-variance denominator in the ratio.
        reference_window : int
            Window of the trailing realized volatility column ``rv_<window>``.
        periods_per_year : float
            Sessions per year the implied volatility is annualized over.

        Returns
        -------
        DataFrame
            ``frame`` with the four ``matched_*`` columns added.
        """
        import numpy as np

        sessions = frame.sessions_to_expiry.astype(float)
        implied = frame.chain_atm_iv.astype(float).pow(2)*sessions/float(periods_per_year)
        realized = frame[f"rv_{reference_window}"].astype(float).pow(2)*sessions
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
    def _availability_dates(observation_dates, calendar_name, lag_sessions=0, lag_days=0):
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
        calendar = xcals.get_calendar(calendar_name, start=start, end=end)
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
    def add_surface_dynamics(frame, proxy_probabilities, change_lags, reference_window,
                             periods_per_year):
        """Add causal same-series changes, cross-expiry slopes and proxy moments.

        Parameters
        ----------
        frame : DataFrame
            The cohort rows (surface columns, ``rv_<reference_window>``).
        proxy_probabilities : list of float
            The risk-neutral proxy probabilities.
        change_lags : list of int
            Same-series lags (in quote dates) of the ``*_change_<lag>`` columns.
        reference_window : int
            Window of the trailing realized volatility column ``rv_<window>``.
        periods_per_year : float
            Sessions per year the implied variance is annualized over.

        Returns
        -------
        DataFrame
            ``frame`` sorted by symbol, expiry and quote date, with the columns added.
        """
        import numpy as np
        import pandas as pd

        frame = frame.sort_values(["symbol", "expiry", "quote_date"]).copy()
        group = frame.groupby(["symbol", "expiry"], sort=False)
        surface = ("chain_log_atm_iv", "chain_log_skew25", "chain_log_curvature25")
        for name in surface:
            for lag in change_lags:
                frame[f"{name}_change_{lag}"] = frame[name]-group[name].shift(lag)
        frame["chain_liquidity_asymmetry"] = np.tanh(frame.chain_log_put_call_oi)
        frame["implied_minus_realized_variance"] = (
            frame.chain_atm_iv**2/float(periods_per_year)
            -frame[f"rv_{reference_window}"]**2)

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
                values.observation_date, self._convention("calendar"), spec.get("lag_sessions", 0),
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
        self.conventions = self._resolve_conventions()
        conv = self.conventions
        files = self.data_files = DataFiles(c.get("root"))
        surface = pd.read_parquet(files.path(c["surface"]))
        lifecycle = pd.read_parquet(files.path(c["lifecycle"]))
        keys = ["symbol", "quote_date", "expiry"]
        meta = surface.merge(lifecycle, on=keys, validate="one_to_one")
        if c.get("chain_features"):
            chain_features = pd.read_parquet(files.path(c["chain_features"]))
            meta = meta.merge(chain_features, on=keys, validate="one_to_one")
        if c.get("surface_features", False):
            meta = self.add_surface_features(meta)
        meta = self._within_horizon(meta, c["max_dte"], conv["calendar_pad_days"])
        pad = pd.Timedelta(days=conv["calendar_pad_days"])
        first_day = pd.to_datetime(pd.concat([meta.quote_date, meta.first_seen_date])).min()
        calendar = xc.get_calendar(conv["calendar"], start=first_day-pad,
                                   end=pd.to_datetime(meta.expiry).max()+pad)
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
            rows = self._horizon_cohort(
                rows[(rows.actual_calendar_dte >= 1) & (rows.calendar_dte <= c["max_dte"])]).copy()
            locked = None
            if self.holdout_start:
                locked = (label_reaches(rows.quote_date, self.holdout_start)
                          | label_reaches(rows.settlement_date, self.holdout_start))
                rows = rows[~locked].copy()
            entry_index = sessions.get_indexer(pd.to_datetime(rows.quote_date))
            end_index = sessions.get_indexer(pd.to_datetime(rows.settlement_date))
            valid_dates = (entry_index >= 0) & (end_index >= 0)
            refused[symbol] = {"non_session_quote": int((~valid_dates).sum())}
            if locked is not None:
                refused[symbol]["holdout_locked"] = int(locked.sum())
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
            dividends = self._dividend_series(price_frame, closes.index)
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
            rows["reference_scale"] = rows[f"rv_{conv['reference_window']}"].clip(lower=c["reference_floor"])*np.sqrt(rows.sessions_to_expiry)
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
        self.source_hashes = {name: files.sha256(c[name]) for name in source_names}
        if c.get("chain_features"):
            if not files.has(c["chain_features"], ".sources.json"):
                raise ValueError("raw-chain source hash manifest is missing")
            self.source_hashes["chain_feature_sources"] = files.sha256(
                c["chain_features"], ".sources.json")
        result = pd.concat(panels, ignore_index=True)
        for symbol in c["symbols"]:
            result[f"is_{symbol}"] = (result.symbol == symbol).astype(int)
        lags = result[[f"ret_lag_{i}" for i in range(c["lags"])]].to_numpy()
        for window in conv["directional_windows"]:
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
                result, c["raw_chain"]["proxy_probabilities"], conv["change_lags"],
                conv["reference_window"], conv["periods_per_year"])
        if c.get("matched_dte_vrp"):
            result = self.add_matched_dte_vrp(
                result, c["reference_floor"], conv["reference_window"], conv["periods_per_year"])
        result, self.macro_event_status = self.add_macro_event_features(
            result, c.get("macro_event_calendars"))
        if c.get("decision_regions"):
            decision_config = c["decision_regions"]
            if decision_config.get("panel_years"):
                years = result.quote_date.str[:4].astype(int)
                result = result.loc[years.isin(decision_config["panel_years"])].copy()
                if result.empty:
                    raise ValueError("decision-context declared years select no rows")
            builder = DecisionRegionContextBuilder(decision_config, files)
            result["decision_region_context"] = builder.build(result)
            self.decision_region_provenance = builder.provenance
            self.source_hashes.update({f"decision_chain:{path}": digest
                                       for path, digest in
                                       builder.provenance["sources"].items()})
        return result

    def provenance(self):
        """Describe what the last ``read()`` consumed and which code read it.

        Returns
        -------
        dict
            ``refused`` (per-symbol exclusion counts), ``sha256`` (source
            file hashes), ``readers`` (the price/index reader
            fingerprints), ``market_coverage`` and ``macro_event_status``
            (empty when the read declared neither), ``adapter_sha256``
            (this module's bytes) and, only when the read resolved a store
            reference, ``store``: one record per resolved snapshot (``source``,
            ``stream``, ``snapshot``, ``manifest_sha256``, ``files``), whose
            manifest digests are the ``sha256`` entries for those files.

        Raises
        ------
        AttributeError
            When ``read()`` has not run.
        """
        result = {"refused": self.refused, "sha256": self.source_hashes,
                  "readers": self.reader_fingerprints,
                  "market_coverage": getattr(self, "market_coverage", {}),
                  "macro_event_status": getattr(self, "macro_event_status", {}),
                  "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        files = getattr(self, "data_files", None)
        if files is not None and files.provenance():
            result["store"] = files.provenance()
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
        """Return expected-loss error and CDF/payoff numerical agreement."""
        import numpy as np

        scale, spot = frame.reference_scale.to_numpy(), frame.spot.to_numpy()
        strikes = spot[:, None]*np.exp(scale[:, None]*self.strikes_z)
        width = np.maximum(strikes[:, 1]-strikes[:, 0], strikes[:, 3]-strikes[:, 2])
        bounded = np.clip(draws, self.strikes_z[0], self.strikes_z[-1])
        mean_loss = -self.payoff(
            spot[:, None]*np.exp(scale[:, None]*bounded), strikes).mean(1)
        realized = -self.payoff(
            frame.terminal_price.to_numpy()[:, None], strikes)[:, 0]
        integral = np.zeros(len(frame))
        for a, b, put in [(0, 1, True), (2, 3, False)]:
            prices = (strikes[:, a, None]
                      +(strikes[:, b]-strikes[:, a])[:, None]
                      *(np.arange(self.integration_points)+.5)
                      / self.integration_points)
            p = curve.cdf(np.log(prices/spot[:, None])/scale[:, None])
            integral += ((p if put else 1-p).mean(1)
                         *(strikes[:, b]-strikes[:, a]))
        z = np.array(self.strikes_z)
        truth = np.log(frame.terminal_price.to_numpy()/spot)/scale
        brier = ((curve.cdf(z)-(truth[:, None] <= z))**2).mean(1)
        result = {"condor_loss_mse": ((integral-realized)/width)**2,
                  "condor_loss_bias": (integral-realized)/width,
                  "expected_loss_per_share": integral,
                  "realized_loss_per_share": realized,
                  "payoff_quadrature_gap": abs(integral-mean_loss),
                  "strike_brier": brier}
        if "decision_region_context" in frame:
            from dskit.pipeline.libs.predictive_cdf import _decision_threshold_inventory
            inventory = _decision_threshold_inventory(
                frame.decision_region_context.tolist())
            count = max(1, max(len(values) for values, _ in inventory))
            thresholds = np.zeros((len(frame), count), dtype=float)
            weights = np.zeros_like(thresholds)
            for row, (values, mass) in enumerate(inventory):
                thresholds[row, :len(values)] = values
                weights[row, :len(mass)] = mass
            truth = (frame.terminal_return.to_numpy()
                     / frame.reference_scale.to_numpy())
            probabilities = curve.cdf(thresholds)
            local = (weights*(probabilities
                              -(truth[:, None] <= thresholds))**2).sum(1)
            counts = np.asarray([len(values) for values, _ in inventory], dtype=int)
            local[counts == 0] = np.nan
            result["decision_strike_brier"] = local
            result["decision_strike_count"] = counts
        return result


class DecisionStrikeDiagnosisStudy:
    """JSON-driven paired diagnosis at every saved listed decision strike."""

    KEYS = {"forecast_root", "models", "reference_model", "symbol", "years",
            "output", "distance_bins", "dte_bins", "groupings", "bootstrap",
            "limits", "notes"}
    GROUP_FIELDS = {"side", "distance_band", "dte_band", "year"}

    def __init__(self, config):
        import math

        if (set(config) != self.KEYS
                or not isinstance(config["models"], list) or len(config["models"]) < 2
                or len(set(config["models"])) != len(config["models"])
                or config["reference_model"] not in config["models"]
                or not isinstance(config["symbol"], str) or not config["symbol"]
                or not isinstance(config["years"], list) or not config["years"]
                or any(type(v) is not int for v in config["years"])
                or config["years"] != sorted(set(config["years"]))
                or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                       or not math.isfinite(v) for name in ("distance_bins", "dte_bins")
                       for v in config[name])
                or any(config[name][0] != 0
                       or len(config[name]) < 2
                       or any(a >= b for a, b in zip(config[name], config[name][1:]))
                       for name in ("distance_bins", "dte_bins"))
                or not isinstance(config["groupings"], list)
                or not config["groupings"]
                or any(not isinstance(grouping, list)
                       or len(grouping) != len(set(grouping))
                       or not set(grouping).issubset(self.GROUP_FIELDS)
                       for grouping in config["groupings"])
                or set(config["bootstrap"]) != {"blocks", "replicates", "alpha", "seed"}
                or any(type(v) is not int or v < 1
                       for v in [*config["bootstrap"]["blocks"],
                                 config["bootstrap"]["replicates"],
                                 config["bootstrap"]["seed"]])
                or not 0 < config["bootstrap"]["alpha"] < 1
                or set(config["limits"]) != {"max_seconds", "max_address_space_mib"}
                or config["limits"]["max_seconds"] > 1800
                or config["limits"]["max_address_space_mib"] > 6144):
            raise ValueError("invalid decision-strike diagnosis JSON")
        self.config = config

    def _enforce_limits(self):
        import resource
        import signal

        limit = self.config["limits"]
        requested = limit["max_address_space_mib"]*1024*1024
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS,
                           (min(requested, soft) if soft != resource.RLIM_INFINITY
                            else requested, hard))
        signal.signal(signal.SIGALRM,
                      lambda _signal, _frame: (_ for _ in ()).throw(
                          TimeoutError("decision-strike diagnosis exceeded time limit")))
        signal.alarm(limit["max_seconds"])

    @staticmethod
    def _band(value, edges):
        import numpy as np

        position = int(np.searchsorted(edges, value, side="right")-1)
        if position < 0 or position >= len(edges)-1:
            raise ValueError("diagnostic value outside declared bins")
        return f"[{edges[position]},{edges[position+1]})"

    @staticmethod
    def _sha256(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(8*1024*1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def run(self):
        """Score exact frozen curves at their identity-bound listed cutoffs."""
        import math
        import numpy as np
        import pandas as pd
        from dskit.pipeline.libs.predictive_cdf import _decision_threshold_inventory

        c = self.config
        root, output = Path(c["forecast_root"]), Path(c["output"])
        if output.exists():
            raise FileExistsError(output)
        panel_path = root/"input_panel.parquet"
        protocol_path = root/"protocol.json"
        if not panel_path.exists() or not protocol_path.exists():
            raise ValueError("diagnosis source lacks frozen panel or protocol")
        panel = pd.read_parquet(panel_path)
        identity = ["symbol", "quote_date", "expiry"]
        panel["year"] = panel.quote_date.str[:4].astype(int)
        panel = panel.loc[(panel.symbol == c["symbol"])
                          & panel.year.isin(c["years"])].copy()
        if (panel.empty or panel.duplicated(identity).any()
                or panel[identity].isna().any().any()
                or "decision_region_context" not in panel):
            raise ValueError("invalid diagnosis panel identities")
        context = []
        for record in panel.decision_region_context.tolist():
            context.append({**record,
                            "identity": [str(v) for v in record["identity"]],
                            "thresholds": [float(v) for v in record["thresholds"]],
                            "weights": [float(v) for v in record["weights"]],
                            "intervals": [[float(v) for v in pair]
                                          for pair in record["intervals"]]})
        inventory = _decision_threshold_inventory(context)
        expected = [tuple(row) for row in panel[identity].itertuples(index=False,
                                                                      name=None)]
        if any(tuple(record["identity"]) != key for record, key in
               zip(context, expected)):
            raise ValueError("diagnosis context identity mismatch")
        source_hashes = {str(panel_path): self._sha256(panel_path),
                         str(protocol_path): self._sha256(protocol_path)}
        records = []
        for model in c["models"]:
            path = root/f"{c['symbol']}-{model}-raw-curves.npz"
            if not path.exists():
                raise ValueError("missing frozen raw curve archive")
            source_hashes[str(path)] = self._sha256(path)
            with np.load(path, allow_pickle=False) as archive:
                archive_ids = [tuple(row) for row in archive["identities"]]
                lookup = {key: i for i, key in enumerate(archive_ids)}
                if len(lookup) != len(archive_ids) or not set(expected).issubset(lookup):
                    raise ValueError("curve archive lacks unique paired identities")
                indices = [lookup[key] for key in expected]
                curve = CDFHyperparameterStudy.restore_curve(archive, indices)
                max_nodes = max((len(nodes) for nodes, _ in inventory), default=0)
                thresholds = np.zeros((len(panel), max(1, max_nodes)))
                weights = np.zeros_like(thresholds)
                for row, (nodes, mass) in enumerate(inventory):
                    thresholds[row, :len(nodes)] = nodes
                    weights[row, :len(mass)] = mass
                probabilities = curve.cdf(thresholds)
                outcomes = (panel.terminal_return.to_numpy()
                            / panel.reference_scale.to_numpy())
                for row, (key, nodes_mass) in enumerate(zip(expected, inventory)):
                    nodes, mass = nodes_mass
                    for column, (threshold, weight) in enumerate(zip(nodes, mass)):
                        probability = float(probabilities[row, column])
                        event = float(outcomes[row] <= threshold)
                        if not 0 <= probability <= 1 or not math.isfinite(probability):
                            raise ValueError("invalid archived decision probability")
                        dte = int(panel.iloc[row].actual_calendar_dte)
                        records.append({
                            **dict(zip(identity, key)), "year": int(panel.iloc[row].year),
                            "model": model, "threshold": float(threshold),
                            "weight": float(weight), "probability": probability,
                            "event": event, "brier": (probability-event)**2,
                            "side": "put" if threshold < 0 else "call",
                            "distance_band": self._band(abs(float(threshold)),
                                                        c["distance_bins"]),
                            "dte_band": self._band(dte, c["dte_bins"]),
                            "actual_calendar_dte": dte})
        detail = pd.DataFrame(records)
        if detail.empty:
            raise ValueError("diagnosis produced no eligible strikes")
        keys = identity+["threshold"]
        counts = detail.groupby(keys).model.nunique()
        if len(counts) != detail[keys].drop_duplicates().shape[0] or not (
                counts == len(c["models"])).all():
            raise ValueError("diagnosis model rows are not exactly paired")
        detail["weighted_error"] = detail.weight*detail.brier
        detail["weighted_probability"] = detail.weight*detail.probability
        detail["weighted_event"] = detail.weight*detail.event
        summary, intervals = [], []
        dates = sorted(detail.quote_date.unique())
        alpha = c["bootstrap"]["alpha"]
        for grouping in c["groupings"]:
            fields = list(grouping)
            work = detail.copy()
            if not fields:
                work["_all"] = "all"
                fields = ["_all"]
            aggregates = (work.groupby(fields+["model"], dropna=False)
                          .agg(weighted_error=("weighted_error", "sum"),
                               weighted_probability=("weighted_probability", "sum"),
                               weighted_event=("weighted_event", "sum"),
                               weight=("weight", "sum"),
                               thresholds=("brier", "size"),
                               identities=("quote_date", "size"),
                               dates=("quote_date", "nunique")).reset_index())
            aggregates["score"] = aggregates.weighted_error/aggregates.weight
            for values, block in aggregates.groupby(fields, dropna=False):
                values = values if isinstance(values, tuple) else (values,)
                labels = dict(zip(fields, values))
                scores = dict(zip(block.model, block.score))
                if set(scores) != set(c["models"]):
                    raise ValueError("diagnosis stratum lacks paired models")
                reference_score = scores[c["reference_model"]]
                for model in c["models"]:
                    row = block.loc[block.model == model].iloc[0]
                    skill = (100*(1-scores[model]/reference_score)
                             if reference_score > 0 else None)
                    summary.append({"view": "+".join(grouping) or "overall",
                                    **{k: v for k, v in labels.items() if k != "_all"},
                                    "model": model, "score": float(scores[model]),
                                    "reference_score": float(reference_score),
                                    "score_delta": float(scores[model]-reference_score),
                                    "skill": float(skill) if skill is not None else None,
                                    "predicted_event_rate": float(
                                        row.weighted_probability/row.weight),
                                    "observed_event_rate": float(
                                        row.weighted_event/row.weight),
                                    "calibration_gap": float(
                                        (row.weighted_probability-row.weighted_event)
                                        / row.weight),
                                    "thresholds": int(row.thresholds),
                                    "dates": int(row.dates)})
            daily = (work.groupby(["quote_date", *fields, "model"], dropna=False)
                     .agg(weighted_error=("weighted_error", "sum"),
                          weight=("weight", "sum")).reset_index())
            for values, block in daily.groupby(fields, dropna=False):
                values = values if isinstance(values, tuple) else (values,)
                labels = dict(zip(fields, values))
                for model in c["models"]:
                    if model == c["reference_model"]:
                        continue
                    table = block.pivot(index="quote_date", columns="model",
                                        values=["weighted_error", "weight"]).reindex(
                                            dates).fillna(0.)
                    required = [("weighted_error", model),
                                ("weighted_error", c["reference_model"]),
                                ("weight", model), ("weight", c["reference_model"])]
                    if any(column not in table for column in required):
                        raise ValueError("diagnosis bootstrap lacks paired model")
                    candidate_num = table[("weighted_error", model)].to_numpy()
                    reference_num = table[("weighted_error", c["reference_model"])].to_numpy()
                    candidate_weight = table[("weight", model)].to_numpy()
                    reference_weight = table[("weight", c["reference_model"])].to_numpy()
                    if not np.allclose(candidate_weight, reference_weight):
                        raise ValueError("diagnosis bootstrap weights differ by model")
                    for width in c["bootstrap"]["blocks"]:
                        rng = np.random.default_rng(c["bootstrap"]["seed"])
                        draws = []
                        for _ in range(c["bootstrap"]["replicates"]):
                            starts = rng.integers(0, len(dates),
                                                  size=int(np.ceil(len(dates)/width)))
                            ix = ((starts[:, None]+np.arange(width)) % len(dates)).ravel()[:len(dates)]
                            weight = reference_weight[ix].sum()
                            if weight > 0:
                                candidate_score = candidate_num[ix].sum()/weight
                                reference_score = reference_num[ix].sum()/weight
                                if reference_score > 0:
                                    draws.append(100*(1-candidate_score/reference_score))
                        if draws:
                            lo, hi = np.quantile(draws, [alpha/2, 1-alpha/2])
                        else:
                            lo = hi = None
                        intervals.append({"view": "+".join(grouping) or "overall",
                                          **{k: v for k, v in labels.items() if k != "_all"},
                                          "model": model,
                                          "reference": c["reference_model"],
                                          "block_dates": width,
                                          "lo": float(lo) if lo is not None else None,
                                          "hi": float(hi) if hi is not None else None,
                                          "replicates": len(draws),
                                          "status": ("estimated" if draws else
                                                     "undefined_zero_reference")})
        output.mkdir(parents=True)
        detail.to_parquet(output/"threshold_scores.parquet", index=False)
        pd.DataFrame(summary).to_csv(output/"stratified_skill.csv", index=False)
        (output/"paired_intervals.json").write_text(json.dumps(intervals, indent=2))
        evidence = {"symbol": c["symbol"], "years": c["years"],
                    "forecast_identities": len(panel),
                    "eligible_identities": int(sum(len(nodes) > 0
                                                   for nodes, _ in inventory)),
                    "threshold_rows": len(detail), "dates": len(dates),
                    "models": c["models"], "reference_model": c["reference_model"],
                    "source_sha256": source_hashes,
                    "implementation_sha256": self._sha256(Path(__file__)),
                    "config": c, "intervals": intervals}
        (output/"summary.json").write_text(json.dumps(evidence, indent=2))
        return evidence

def _main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("--stage", choices=["prepare", "search", "select", "evaluate", "report"])
    parser.add_argument("--partition")
    parser.add_argument("--decision-stage", choices=["prepare", "evaluate", "report"])
    parser.add_argument("--robust-stage", choices=["train", "select", "optimize", "report"])
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if set(config) == {"decision_strike_diagnosis"}:
        if args.stage or args.partition or args.decision_stage or args.robust_stage:
            parser.error("decision-strike diagnosis does not accept stage flags")
        study = DecisionStrikeDiagnosisStudy(config["decision_strike_diagnosis"])
        study._enforce_limits()
        print(json.dumps(study.run(), indent=2), flush=True)
        return
    if config.get("data", {}).get("decision_regions"):
        # The CLI is a one-study worker. Apply limits before loading/scanning
        # archive data and deliberately let process exit restore OS state.
        DecisionRegionContextBuilder(
            config["data"]["decision_regions"]).enforce_process_limits()
    if args.robust_stage:
        if (args.stage or args.partition or args.decision_stage
                or set(config) != {"robust_correction_study"}):
            parser.error("robust stage needs a standalone robust_correction_study document")
        study = RobustCorrectionStudy(config["robust_correction_study"])
        study._enforce_limits()
        print(json.dumps(getattr(study, args.robust_stage)(), indent=2), flush=True)
        return
    if args.decision_stage:
        if args.stage or args.partition or args.robust_stage or set(config) != {"decision_study"}:
            parser.error("decision stage needs a standalone decision_study document")
        study = DecisionRegionStudy(config["decision_study"])
        study._enforce_limits()
        print(json.dumps(getattr(study, args.decision_stage)(), indent=2), flush=True)
        return
    if args.stage == "prepare":
        import pandas as pd
        if args.partition:
            parser.error("prepare does not accept a partition")
        data = config["data"]
        if not isinstance(data["chain_features"], str):
            raise ValueError("prepare writes data.chain_features: name a plain output path "
                             "(a store reference is read-only; onboard the result afterwards)")
        files = DataFiles(data.get("root"))
        surface = pd.read_parquet(files.path(data["surface"]))
        lifecycle = pd.read_parquet(files.path(data["lifecycle"]))
        meta = surface.merge(lifecycle, on=["symbol", "quote_date", "expiry"],
                             validate="one_to_one")
        builder = RawChainFeatureBuilder(**data["raw_chain"])
        rows = builder.build_archive(meta, files.tree(data["archive_root"]),
                                     data["chain_features"])
        print("prepared raw-chain rows", len(rows), flush=True)
        return
    fold_table = config.get("study", {}).get("fold_table")
    adapter = ExactExpiryCDFPanel(
        config["data"], holdout_start=fold_table.get("holdout_start") if fold_table else None)
    frame = adapter.read()
    provenance = adapter.provenance()
    diagnostic = CondorCDFDiagnostic(**config["diagnostic"])
    if "experiment" in config:
        if not args.stage:
            parser.error("HPO requires an explicit --stage")
        CDFHyperparameterStudy(config).run(frame, diagnostic, stage=args.stage,
                                            partition=args.partition, provenance=provenance)
        if config.get("data", {}).get("decision_regions"):
            import signal
            signal.setitimer(signal.ITIMER_REAL, 0.)
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
    record = {"refused": adapter.refused, "sha256": adapter.source_hashes,
              "readers": adapter.reader_fingerprints}
    if "store" in provenance:
        record["store"] = provenance["store"]
    (output/"data_provenance.json").write_text(json.dumps(record, indent=2))
    if config.get("data", {}).get("decision_regions"):
        # The transient systemd service owns the hard wall/RSS limits. Stop the
        # secondary periodic watchdog before Python restores SIGALRM's default
        # disposition during interpreter shutdown.
        import signal
        signal.setitimer(signal.ITIMER_REAL, 0.)


if __name__ == "__main__":
    _main()
