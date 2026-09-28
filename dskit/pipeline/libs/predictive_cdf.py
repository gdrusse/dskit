"""Offline conditional CDFs and paired chronological research (ADR-0189).

This array-oriented library pack complements the existing sample-score and
fitted-transform seams; it does not grant serving authority. All statistical
libraries are imported at method depth. Curves use one row per forecast and
one column per query. Models predict a caller-standardized scalar outcome.
"""

from abc import ABC, abstractmethod
import hashlib
import importlib
import json
from pathlib import Path
import time

__all__ = ["MixtureCurve", "GridCurve", "CalibratedCurve", "CDFEstimator",
           "HorizonEmpiricalCDF", "ScaledEmpiricalCDF", "MonotoneCDF",
           "QuantileCDF", "MixtureMLPCDF", "ChronologicalCDFStudy"]


class _Curve(ABC):
    @abstractmethod
    def cdf(self, values):
        """Evaluate rowwise cumulative probabilities."""

    @abstractmethod
    def quantile(self, probabilities):
        """Evaluate rowwise inverse cumulative probabilities."""


class MixtureCurve(_Curve):
    """Finite Gaussian mixture, with analytic CDF and numerical inverse.

    Parameters
    ----------
    weights, means, scales : array-like
        Matching (rows, components) finite arrays. Weights sum to one;
        scales are strictly positive.

    Examples
    --------
    Standard normal::

        curve = MixtureCurve([[1]], [[0]], [[1]])
    """

    def __init__(self, weights, means, scales):
        import numpy as np

        self.weights, self.means, self.scales = [
            np.asarray(v, dtype=float) for v in (weights, means, scales)]
        arrays = (self.weights, self.means, self.scales)
        if (self.weights.ndim != 2 or min(self.weights.shape) < 1
                or any(a.shape != self.weights.shape for a in arrays)
                or not all(np.isfinite(a).all() for a in arrays)
                or (self.weights < 0).any() or (self.scales <= 0).any()
                or not np.allclose(self.weights.sum(1), 1, atol=1e-7)):
            raise ValueError("invalid mixture weights, means or scales")

    def cdf(self, values):
        """Return cumulative probabilities.

        Parameters
        ----------
        values : array-like
            Broadcastable to (forecast rows, query columns).

        Returns
        -------
        ndarray
            Probabilities with query shape.
        """
        import numpy as np
        from scipy.special import ndtr

        values = np.broadcast_to(values, (len(self.weights), np.shape(values)[-1]))
        result = np.zeros_like(values, dtype=float)
        for j in range(self.weights.shape[1]):
            result += self.weights[:, j, None] * ndtr(
                (values - self.means[:, j, None]) / self.scales[:, j, None])
        return result

    def quantile(self, probabilities):
        """Invert the analytic CDF for probabilities strictly inside (0, 1).

        Parameters
        ----------
        probabilities : array-like
            Common or rowwise query probabilities.

        Returns
        -------
        ndarray
            Quantiles; bisection bounds cover the requested normal quantiles.
        """
        import numpy as np
        from scipy.special import ndtri

        p = np.broadcast_to(probabilities, (len(self.weights), np.shape(probabilities)[-1]))
        if not np.isfinite(p).all() or (p <= 0).any() or (p >= 1).any():
            raise ValueError("quantile probabilities must be strictly inside (0,1)")
        component = self.means[:, :, None] + self.scales[:, :, None] * ndtri(p[:, None, :])
        lo, hi = component.min(1), component.max(1)
        for _ in range(42):
            mid = (lo + hi) / 2
            mask = self.cdf(mid) < p
            lo = np.where(mask, mid, lo)
            hi = np.where(mask, hi, mid)
        return (lo + hi) / 2


class GridCurve(_Curve):
    """Continuous, monotone piecewise-linear CDF with explicit finite tails.

    Parameters
    ----------
    values, probabilities : array-like
        Rowwise increasing abscissae and nondecreasing probabilities. End
        probabilities must be exactly zero and one. Ties in values are atoms.

    Examples
    --------
    Uniform distribution::

        curve = GridCurve([[0, 1]], [[0, 1]])
    """

    def __init__(self, values, probabilities):
        import numpy as np

        self.values = np.asarray(values, dtype=float)
        self.probabilities = np.broadcast_to(probabilities, self.values.shape).copy()
        if (self.values.ndim != 2 or self.values.shape[1] < 2
                or not np.isfinite(self.values).all()
                or not np.isfinite(self.probabilities).all()
                or (np.diff(self.values, axis=1) < 0).any()
                or (np.diff(self.probabilities, axis=1) < 0).any()
                or not (self.probabilities[:, 0] == 0).all()
                or not (self.probabilities[:, -1] == 1).all()):
            raise ValueError("invalid CDF grid or endpoints")

    def cdf(self, values):
        """Interpolate CDF queries, using zero/one outside the declared tails.

        Parameters
        ----------
        values : array-like
            Common or rowwise cutoffs.

        Returns
        -------
        ndarray
            Rowwise cumulative probabilities.
        """
        import numpy as np

        queries = np.broadcast_to(values, (len(self.values), np.shape(values)[-1]))
        return np.array([np.interp(q, x, p, left=0, right=1)
                         for q, x, p in zip(queries, self.values, self.probabilities)])

    def quantile(self, probabilities):
        """Interpolate inverse-CDF queries.

        Parameters
        ----------
        probabilities : array-like
            Common or rowwise probabilities in [0,1].

        Returns
        -------
        ndarray
            Rowwise quantiles.
        """
        import numpy as np

        queries = np.broadcast_to(probabilities, self.values.shape[:1] + (np.shape(probabilities)[-1],))
        if not np.isfinite(queries).all() or (queries < 0).any() or (queries > 1).any():
            raise ValueError("probabilities outside [0,1]")
        return np.array([np.interp(q, p, x) for q, x, p in
                         zip(queries, self.values, self.probabilities)])


class CalibratedCurve(_Curve):
    """Compose a curve with a held-out PIT calibration map.

    Parameters
    ----------
    base : curve
        Uncalibrated forecasts.
    pit : array-like
        CDF values at outcomes in a separate, already-settled calibration band.
    knots : int
        Fixed number of calibration probability knots.

    Examples
    --------
    Recalibrate a uniform curve::

        curve = CalibratedCurve(GridCurve([[0, 1]], [[0, 1]]), [.2, .6, .9], 5)
    """

    def __init__(self, base, pit, knots):
        import numpy as np

        p = np.linspace(0, 1, knots)
        x = np.quantile(pit, p)
        # Endpoint anchors retain a proper CDF, including tails absent in cal.
        self.map = GridCurve(np.r_[0., np.clip(x[1:-1], 1e-9, 1-1e-9), 1.][None, :], p[None, :])
        self.base = base

    def cdf(self, values):
        """Apply the monotone map to base CDF values.

        Parameters
        ----------
        values : array-like
            Query cutoffs.

        Returns
        -------
        ndarray
            Recalibrated probabilities.
        """
        import numpy as np

        return np.interp(self.base.cdf(values), self.map.values[0], self.map.probabilities[0])

    def quantile(self, probabilities):
        """Pass inverse calibration probabilities to the base curve.

        Parameters
        ----------
        probabilities : array-like
            Query probabilities strictly inside (0,1).

        Returns
        -------
        ndarray
            Recalibrated quantiles.
        """
        import numpy as np

        p = np.interp(probabilities, self.map.probabilities[0], self.map.values[0])
        return self.base.quantile(np.clip(p, 1e-9, 1-1e-9))


class CDFEstimator(ABC):
    """Array estimator seam; calibration is always supplied separately.

    Parameters
    ----------
    None
        Subclasses declare their own default-deny constructor knobs.

    Examples
    --------
    Instantiate a concrete subclass::

        model = MonotoneCDF(thresholds=[-4, -2, 0, 2, 4])
    """

    @abstractmethod
    def fit(self, x, y, cal_x, cal_y):
        """Learn from training arrays; an empirical shape may use calibration."""

    @abstractmethod
    def curve(self, x):
        """Return one curve per feature row."""


class HorizonEmpiricalCDF(CDFEstimator):
    """Training-only empirical outcomes conditioned on an exact horizon.

    Parameters
    ----------
    horizon_index, reference_index : int
        Column positions of the untransformed horizon and reference divisor.
    knots : int
        Number of empirical quantile knots, including support endpoints.

    Examples
    --------
    Name the special feature positions::

        model = HorizonEmpiricalCDF(0, 1, 401)
    """

    def __init__(self, horizon_index, reference_index, knots):
        self.horizon_index, self.reference_index, self.knots = horizon_index, reference_index, knots

    def fit(self, x, y, cal_x, cal_y):
        """Learn exact-horizon raw-outcome empirical quantiles.

        Parameters
        ----------
        x, y, cal_x, cal_y : array-like
            Training and disjoint calibration data; calibration is unused.

        Returns
        -------
        self
            Fitted estimator.
        """
        import numpy as np

        self.p = np.linspace(0, 1, self.knots)
        raw = y * x[:, self.reference_index]
        self.shapes = {h: np.quantile(raw[x[:, self.horizon_index] == h], self.p)
                       for h in np.unique(x[:, self.horizon_index])}
        return self

    def curve(self, x):
        """Return the exact-horizon shape in each row's standardized units.

        Parameters
        ----------
        x : array-like
            Feature rows; a horizon absent in training refuses.

        Returns
        -------
        GridCurve
            Interpolated empirical quantiles, not an assumption about unseen tails.
        """
        import numpy as np

        values = np.array([self.shapes[h] for h in x[:, self.horizon_index]])
        return GridCurve(values / x[:, self.reference_index, None], self.p)


class ScaledEmpiricalCDF(CDFEstimator):
    """Ridge conditional scale with shape learned only from held-out outcomes.

    Parameters
    ----------
    alpha : float
        Ridge penalty for log absolute standardized outcomes.
    floor : float
        Positive absolute-outcome floor, not a tail truncation.
    knots : int
        Number of held-out empirical shape quantiles.

    Examples
    --------
    Regularized filtered empirical reference::

        model = ScaledEmpiricalCDF(alpha=10, floor=.02, knots=401)
    """

    def __init__(self, alpha, floor, knots):
        self.alpha, self.floor, self.knots = alpha, floor, knots

    def fit(self, x, y, cal_x, cal_y):
        """Fit scale on training and residual shape on calibration only.

        Parameters
        ----------
        x, y, cal_x, cal_y : array-like
            Disjoint chronological arrays.

        Returns
        -------
        self
            Fitted estimator.
        """
        import numpy as np
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        self.model = make_pipeline(StandardScaler(), Ridge(alpha=self.alpha))
        self.model.fit(x, np.log(np.maximum(abs(y), self.floor)))
        self.p = np.linspace(0, 1, self.knots)
        self.shape = np.quantile(cal_y / self._scale(cal_x), self.p)
        return self

    def _scale(self, x):
        import numpy as np

        return np.exp(np.clip(self.model.predict(x), -5, 5))

    def curve(self, x):
        """Stretch held-out shape by a conditional scale.

        Parameters
        ----------
        x : array-like
            Feature rows.

        Returns
        -------
        GridCurve
            Conditional empirical curve.
        """
        return GridCurve(self._scale(x)[:, None] * self.shape, self.p)


class MonotoneCDF(CDFEstimator):
    """One boosted binary classifier, constrained increasing in the cutoff.

    Parameters
    ----------
    thresholds : list of float
        Fixed interior standardized cutoffs. End support extends by tail_width.
    trees, leaves, min_child, threads : int
        Bounded boosting complexity and execution width.
    learning_rate, regularization, tail_width : float
        Learning rate, L2 penalty, and finite tail extension.

    Examples
    --------
    A small direct CDF::

        model = MonotoneCDF(thresholds=[-4, -2, 0, 2, 4])
    """

    def __init__(self, thresholds, trees=100, leaves=7, min_child=200,
                 threads=2, learning_rate=.05, regularization=5., tail_width=4.):
        if thresholds and any(a >= b for a, b in zip(thresholds, thresholds[1:])):
            raise ValueError("thresholds must be strictly increasing")
        if min(trees, leaves, min_child, threads, learning_rate, tail_width) <= 0 or regularization < 0:
            raise ValueError("invalid positive complexity or tail parameter")
        self.thresholds = thresholds
        self.settings = dict(n_estimators=trees, num_leaves=leaves,
                             min_child_samples=min_child, n_jobs=threads,
                             learning_rate=learning_rate, reg_lambda=regularization,
                             verbosity=-1, deterministic=True, force_col_wise=True,
                             random_state=0)
        self.tail_width = tail_width

    def _expanded(self, x):
        import numpy as np

        return np.column_stack([np.repeat(x, len(self.thresholds), axis=0),
                                np.tile(self.thresholds, len(x))])

    def fit(self, x, y, cal_x, cal_y):
        """Learn shared cutoff probabilities; calibration remains separate.

        Parameters
        ----------
        x, y, cal_x, cal_y : array-like
            Training and calibration arrays.

        Returns
        -------
        self
            Fitted estimator.
        """
        import numpy as np
        from lightgbm import LGBMClassifier

        self.model = LGBMClassifier(**self.settings, monotone_constraints=[0]*x.shape[1]+[1])
        self.model.fit(self._expanded(x), (y[:, None] <= np.array(self.thresholds)).ravel().astype(int))
        return self

    def curve(self, x):
        """Interpolate monotone cutoff probabilities with declared finite tails.

        Parameters
        ----------
        x : array-like
            Feature rows.

        Returns
        -------
        GridCurve
            Conditional direct CDF.
        """
        import numpy as np

        p = self.model.predict_proba(self._expanded(x))[:, 1].reshape(len(x), -1)
        if (np.diff(p, axis=1) < -1e-10).any():
            raise ValueError("monotone classifier returned crossing CDFs")
        t = self.thresholds
        grid = np.array([t[0]-self.tail_width, *t, t[-1]+self.tail_width])
        return GridCurve(np.broadcast_to(grid, (len(x), len(grid))),
                         np.column_stack([np.zeros(len(x)), p, np.ones(len(x))]))


class QuantileCDF(MonotoneCDF):
    """LightGBM quantiles with rearrangement and explicit finite tails.

    Parameters
    ----------
    probabilities : list of float
        Fixed ordered quantile levels strictly inside (0,1).
    **settings : dict
        MonotoneCDF complexity settings, except thresholds.

    Examples
    --------
    A compact quantile model::

        model = QuantileCDF(probabilities=[.05, .5, .95])
    """

    def __init__(self, probabilities, **settings):
        super().__init__(thresholds=[], **settings)
        if (len(probabilities) < 2 or probabilities[0] <= 0 or probabilities[-1] >= 1
                or any(a >= b for a, b in zip(probabilities, probabilities[1:]))):
            raise ValueError("quantile levels must increase strictly inside (0,1)")
        self.probabilities = probabilities

    def fit(self, x, y, cal_x, cal_y):
        """Fit each declared quantile on the same training rows.

        Parameters
        ----------
        x, y, cal_x, cal_y : array-like
            Training and separate calibration arrays.

        Returns
        -------
        self
            Fitted estimator.
        """
        from lightgbm import LGBMRegressor

        self.models = [LGBMRegressor(**self.settings, objective="quantile", alpha=p).fit(x, y)
                       for p in self.probabilities]
        return self

    def curve(self, x):
        """Sort quantiles and interpolate a valid CDF with bounded tails.

        Parameters
        ----------
        x : array-like
            Feature rows.

        Returns
        -------
        GridCurve
            Repaired noncrossing quantiles.
        """
        import numpy as np

        q = np.sort(np.column_stack([model.predict(x) for model in self.models]), axis=1)
        q = np.column_stack([q[:, 0]-self.tail_width, q, q[:, -1]+self.tail_width])
        return GridCurve(q, [0., *self.probabilities, 1.])


class MixtureMLPCDF(CDFEstimator):
    """Small Gaussian mixture MLP ensemble trained with log likelihood.

    Parameters
    ----------
    components, hidden, epochs, batch_size : int
        Component count, two-layer width, passes and batch size.
    seeds : list of int
        Independently fitted networks, averaged as a mixture, never best-seed selected.
    device : str
        Explicit torch device. CUDA failure refuses instead of silently using CPU.
    learning_rate, weight_decay, min_scale : float
        Optimizer controls and positive standardized scale floor.

    Examples
    --------
    Three-component CPU research candidate::

        model = MixtureMLPCDF(components=3, device="cpu")
    """

    def __init__(self, components=3, hidden=32, epochs=20, batch_size=1024,
                 seeds=(11, 29), device="cuda", learning_rate=.001,
                 weight_decay=.01, min_scale=.1):
        if (min(components, hidden, epochs, batch_size, learning_rate, min_scale) <= 0
                or weight_decay < 0 or not seeds or len(set(seeds)) != len(seeds)):
            raise ValueError("invalid mixture training parameters")
        self.components, self.hidden, self.epochs = components, hidden, epochs
        self.batch_size, self.seeds, self.device = batch_size, seeds, device
        self.learning_rate, self.weight_decay, self.min_scale = learning_rate, weight_decay, min_scale

    def _parts(self, output):
        import torch

        logits, means, raw_scale = output.chunk(3, dim=1)
        return (torch.log_softmax(logits, dim=1), means,
                torch.nn.functional.softplus(raw_scale) + self.min_scale)

    def fit(self, x, y, cal_x, cal_y):
        """Train with train-only scaling, fixed epochs and explicit seeds.

        Parameters
        ----------
        x, y, cal_x, cal_y : array-like
            Training and separate calibration arrays; no validation early stopping.

        Returns
        -------
        self
            Fitted ensemble, retaining networks for exact CDF queries.
        """
        import numpy as np
        import torch
        from sklearn.preprocessing import StandardScaler

        self.scaler = StandardScaler().fit(x)
        xx = torch.tensor(self.scaler.transform(x), dtype=torch.float32, device=self.device)
        yy = torch.tensor(y[:, None], dtype=torch.float32, device=self.device)
        self.models, self.losses = [], []
        for seed in self.seeds:
            torch.manual_seed(seed)
            model = torch.nn.Sequential(torch.nn.Linear(x.shape[1], self.hidden), torch.nn.Tanh(),
                                        torch.nn.Linear(self.hidden, self.hidden), torch.nn.Tanh(),
                                        torch.nn.Linear(self.hidden, 3*self.components)).to(self.device)
            optimizer = torch.optim.AdamW(model.parameters(), lr=self.learning_rate,
                                          weight_decay=self.weight_decay)
            losses = []
            for _ in range(self.epochs):
                order = torch.randperm(len(xx), device=self.device)
                total = 0.
                for start in range(0, len(xx), self.batch_size):
                    ix = order[start:start+self.batch_size]
                    logw, mu, sigma = self._parts(model(xx[ix]))
                    logp = -.5*((yy[ix]-mu)/sigma)**2 - sigma.log() - .5*np.log(2*np.pi)
                    loss = -torch.logsumexp(logw+logp, dim=1).mean()
                    if not torch.isfinite(loss):
                        raise ValueError("nonfinite mixture likelihood")
                    optimizer.zero_grad()
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 5.)
                    optimizer.step()
                    total += loss.item()*len(ix)
                losses.append(total/len(xx))
            model.eval()
            self.models.append(model)
            self.losses.append(losses)
        return self

    def curve(self, x):
        """Produce an analytic mixture CDF, averaging all declared seeds.

        Parameters
        ----------
        x : array-like
            Feature rows.

        Returns
        -------
        MixtureCurve
            Ensemble curve; components times seeds Gaussian terms.
        """
        import numpy as np
        import torch

        xx = torch.tensor(self.scaler.transform(x), dtype=torch.float32, device=self.device)
        weights, means, scales = [], [], []
        with torch.no_grad():
            for model in self.models:
                logw, mu, sigma = self._parts(model(xx))
                weights.append(logw.exp().cpu().numpy()/len(self.models))
                means.append(mu.cpu().numpy())
                scales.append(sigma.cpu().numpy())
        return MixtureCurve(np.concatenate(weights, 1), np.concatenate(means, 1),
                            np.concatenate(scales, 1))


class ChronologicalCDFStudy:
    """JSON-driven paired research, with purged training/calibration/year splits.

    Parameters
    ----------
    config : dict
        Fields, features, named estimator imports, years, integration resolutions
        and output directory. Unknown top-level keys refuse.

    Examples
    --------
    Run on an already constructed, provenance-pinned panel::

        study = ChronologicalCDFStudy(config)
        result = study.run(panel)
    """

    KEYS = {"features", "group", "date", "end", "horizon", "target", "reference",
            "models", "years", "output", "samples", "tail_intervals", "tail_points",
            "calibration_knots", "development_end", "bootstrap", "reference_model",
            "comparison_references", "notes"}

    def __init__(self, config):
        unknown = set(config)-self.KEYS
        missing = self.KEYS-{"notes"}-set(config)
        if unknown or missing:
            raise ValueError(f"unknown keys {unknown}; missing keys {missing}")
        self.config = config

    def summarize(self, scores):
        """Select calibration on development only; report paired later scores.

        Parameters
        ----------
        scores : DataFrame
            Output of run, including development and research-validation years.

        Returns
        -------
        dict
            Selected variants, rankings, grids and paired date-block uncertainty.
            All files are evidence, never a promotion or trading gate.
        """
        import numpy as np
        import pandas as pd

        c = self.config
        output = Path(c["output"])
        cells = [c["group"], c["horizon"]]
        dev = scores[scores.year <= c["development_end"]]
        later = scores[scores.year > c["development_end"]]
        dev_means = dev.groupby(cells+["model", "variant"]).crps.mean().unstack(["model", "variant"])
        # Equal-cell relative score uses a fixed raw horizon-empirical reference.
        baseline = c["reference_model"]
        base = dev_means[(baseline, "raw")]
        dev_rel = dev_means.div(base, axis=0).mean()
        selected = {name: min(("raw", "calibrated"), key=lambda v: dev_rel[(name, v)])
                    for name in c["models"]}
        selected[baseline] = "raw"
        picked = pd.concat([later[(later.model == name) & (later.variant == variant)]
                            for name, variant in selected.items()])
        metrics = ["crps", "tail_crps", "raw_return_crps", "strike_brier", "condor_loss_mse",
                   "condor_loss_bias", "below_05", "above_95", "payoff_quadrature_gap"]
        metrics = [m for m in metrics if m in scores]
        records, grids = [], []
        keys = [c["group"], c["date"], c["end"], c["horizon"]]
        for metric in metrics:
            mean = picked.groupby(cells+["model"])[metric].mean().unstack("model")
            ratio = mean.div(mean[baseline], axis=0)
            if metric not in ("condor_loss_bias", "below_05", "above_95", "payoff_quadrature_gap"):
                for model in c["models"]:
                    r = (100*(1-ratio[model])).rename("skill").reset_index()
                    r["metric"], r["model"] = metric, model
                    n = picked[picked.model == model].groupby(cells).size().rename("n").reset_index()
                    grids.append(r.merge(n, on=cells, validate="one_to_one"))
            for model in c["models"]:
                f = picked[picked.model == model]
                records.append({"model": model, "variant": selected[model], "metric": metric,
                                "n": len(f), "dates": f[c["date"]].nunique(),
                                "expiry_series": f[[c["group"], c["end"]]].drop_duplicates().shape[0],
                                "mean": float(f[metric].mean()),
                                "equal_cell_skill": (float(100*(1-ratio[model].mean()))
                                                     if metric in ("crps", "tail_crps", "raw_return_crps", "strike_brier", "condor_loss_mse")
                                                     and np.isfinite(ratio[model].mean()) else None)})
        intervals = []
        # Same entry date carries ALL indexes/horizons together. Sum within cell;
        # paired denominators cancel in each cell's candidate/reference ratio.
        for reference in c["comparison_references"]:
            base_rows = picked[picked.model == reference].set_index(keys).crps
            for model in c["models"]:
                compare = picked[picked.model == model].set_index(keys).crps
                if not compare.index.is_unique or not base_rows.index.is_unique or set(compare.index) != set(base_rows.index):
                    raise ValueError("comparison rows are not exactly paired")
                pair = pd.concat([compare.rename("candidate"), base_rows.rename("reference")], axis=1).reset_index()
                a = pair.pivot_table(index=c["date"], columns=cells, values="candidate", aggfunc="sum").fillna(0).to_numpy()
                b = pair.pivot_table(index=c["date"], columns=cells, values="reference", aggfunc="sum").fillna(0).to_numpy()
                for width in c["bootstrap"]["blocks"]:
                    rng = np.random.default_rng(c["bootstrap"]["seed"])
                    replicates = []
                    for _ in range(c["bootstrap"]["replicates"]):
                        start = rng.integers(0, len(a), size=int(np.ceil(len(a)/width)))
                        idx = ((start[:, None]+np.arange(width)) % len(a)).ravel()[:len(a)]
                        denom = b[idx].sum(0)
                        if (denom <= 0).any():
                            raise ValueError("bootstrap cell lacks observations")
                        replicates.append(100*(1-np.mean(a[idx].sum(0)/denom)))
                    lo, hi = np.quantile(replicates, [.025, .975])
                    intervals.append({"model": model, "reference": reference, "block_dates": width,
                                      "lo": float(lo), "hi": float(hi),
                                      "point": float(100*(1-np.mean(a.sum(0)/b.sum(0))))})
        summary = {"selected_variants_from_development": selected,
                   "development_relative_crps": {f"{k[0]}:{k[1]}": float(v) for k, v in dev_rel.items()},
                   "metrics": records, "paired_block_intervals": intervals,
                   "limits": "Pointwise research intervals, not multiplicity-adjusted selection guarantees; historical evaluation already inspected."}
        (output/"comparison.json").write_text(json.dumps(summary, indent=2, allow_nan=False))
        pd.concat(grids, ignore_index=True).to_csv(output/"skill_by_exact_day.csv", index=False)
        picked.groupby([c["group"], "model"])[metrics].mean().to_csv(output/"metrics_by_index.csv")
        later.groupby(["model", "variant"])[metrics].mean().to_csv(output/"raw_vs_calibrated.csv")
        return summary

    @staticmethod
    def split(frame, year, date_field, end_field):
        """Carve disjoint bands and purge unsettled labels at BOTH boundaries.

        Parameters
        ----------
        frame : DataFrame
            ISO date fields; end is when the outcome is known.
        year : int
            Evaluation year; preceding calendar year is calibration.
        date_field, end_field : str
            Entry and outcome-availability columns.

        Returns
        -------
        tuple of DataFrame
            Training, calibration, evaluation rows.
        """
        c, v, stop = f"{year-1}-01-01", f"{year}-01-01", f"{year+1}-01-01"
        date, end = frame[date_field], frame[end_field]
        return (frame[(date < c) & (end < c)],
                frame[(date >= c) & (date < v) & (end < v)],
                frame[(date >= v) & (date < stop)])

    @staticmethod
    def scores(curve, y, samples, intervals, points):
        """Score a curve with midpoint-quantile CRPS and tail CDF quadrature.

        Parameters
        ----------
        curve : curve
            Rowwise distribution forecasts.
        y : ndarray
            Standardized outcomes.
        samples, points : int
            Quantile and per-interval integration resolution.
        intervals : list
            Predeclared standardized tail ranges, common to every model.

        Returns
        -------
        tuple
            Metric arrays and quantile draws; draw count is not an observation count.
        """
        import numpy as np

        p = (np.arange(samples)+.5)/samples
        q = curve.quantile(p)
        crps = np.mean(abs(q-y[:, None]), axis=1) - q @ (2*np.arange(samples)-samples+1)/(samples*samples)
        tail = np.zeros(len(y))
        for a, b in intervals:
            grid = a+(np.arange(points)+.5)*(b-a)/points
            error = curve.cdf(grid)-(y[:, None] <= grid)
            tail += np.mean(error**2, axis=1)*(b-a)
        pit = curve.cdf(y[:, None])[:, 0]
        return {"crps": crps, "tail_crps": tail, "pit": pit,
                "below_05": (pit < .05).astype(float),
                "above_95": (pit > .95).astype(float)}, q

    def run(self, frame, diagnostic=None):
        """Fit paired annual folds and retain row scores, counts and curves.

        Parameters
        ----------
        frame : DataFrame
            Complete labels, causal features and metadata.
        diagnostic : callable or None
            Optional domain diagnostic(frame, curve, draws) returning row metrics.

        Returns
        -------
        DataFrame
            All paired scores, also persisted in the declared new output directory.

        Raises
        ------
        ValueError
            For missing/empty bands or invalid labels/reference scales.
        FileExistsError
            If the output directory exists; no research evidence is overwritten.
        """
        import numpy as np
        import pandas as pd
        from sklearn.impute import SimpleImputer

        c = self.config
        output = Path(c["output"])
        output.mkdir(parents=True, exist_ok=False)
        identity = hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()
        (output/"protocol.json").write_text(json.dumps({"identity": identity, **c}, indent=2))
        frame.to_parquet(output/"input_panel.parquet", index=False)
        from importlib.metadata import version
        versions = {name: version(name) for name in ("numpy", "pandas", "scipy", "scikit-learn", "torch", "lightgbm")}
        (output/"versions.json").write_text(json.dumps(versions, indent=2))
        if ((frame[c["reference"]] <= 0).any()
                or not np.isfinite(frame[[c["target"], c["reference"]]]).all().all()
                or (frame[c["end"]] <= frame[c["date"]]).any()):
            raise ValueError("invalid target, reference or nonfuture outcome date")
        results, counts = [], []
        for group, group_frame in frame.groupby(c["group"]):
            for year in c["years"]:
                fit, cal, val = self.split(group_frame, year, c["date"], c["end"])
                if min(len(fit), len(cal), len(val)) < 1:
                    raise ValueError(f"empty band: {group} {year}")
                count = {"group": group, "year": year}
                for name, band in [("fit", fit), ("cal", cal), ("val", val)]:
                    count[name] = {"n": len(band), "dates": band[c["date"]].nunique(),
                                   "ends": band[c["end"]].nunique(),
                                   "first": band[c["date"]].min(), "last": band[c["date"]].max(),
                                   "latest_label": band[c["end"]].max()}
                imputer = SimpleImputer(strategy="median", keep_empty_features=True)
                x = imputer.fit_transform(fit[c["features"]])
                xc, xv = [imputer.transform(b[c["features"]]) for b in (cal, val)]
                y, yc, yv = [(b[c["target"]]/b[c["reference"]]).to_numpy() for b in (fit, cal, val)]
                for name, spec in c["models"].items():
                    start = time.monotonic()
                    module, cls = spec["class"].split(":")
                    model = getattr(importlib.import_module(module), cls)(**spec["params"])
                    model.fit(x, y, xc, yc)
                    raw = model.curve(xv)
                    variants = {"raw": raw}
                    if spec["calibrate"]:
                        pit = model.curve(xc).cdf(yc[:, None])[:, 0]
                        variants["calibrated"] = CalibratedCurve(raw, pit, c["calibration_knots"])
                    else:
                        variants["calibrated"] = raw
                    for variant, curve in variants.items():
                        scored, draws = self.scores(curve, yv, c["samples"], c["tail_intervals"], c["tail_points"])
                        part = val[[c["group"], c["date"], c["end"], c["horizon"]]].copy()
                        part["year"], part["model"], part["variant"] = year, name, variant
                        for metric, values in scored.items():
                            part[metric] = values
                        part["raw_return_crps"] = part.crps.to_numpy()*val[c["reference"]].to_numpy()
                        if diagnostic:
                            for metric, values in diagnostic(val, curve, draws).items():
                                part[metric] = values
                        results.append(part)
                        # Last evaluation-year curves are sufficient to reproduce price queries;
                        # all years retain paired row scores. No pickle/serving contract is created.
                        if year == max(c["years"]) and variant == "calibrated":
                            np.savez_compressed(output/f"{group}-{name}-curves.npz",
                                                draws=draws.astype("float32"),
                                                reference=val[c["reference"]].to_numpy(),
                                                row_index=val.index.to_numpy())
                            if isinstance(raw, MixtureCurve):
                                np.savez_compressed(output/f"{group}-{name}-mixture.npz",
                                                    weights=raw.weights, means=raw.means, scales=raw.scales)
                    count[name+"_seconds"] = time.monotonic()-start
                    print(group, year, name, round(count[name+"_seconds"], 2), flush=True)
                counts.append(count)
                pd.concat(results, ignore_index=True).to_parquet(output/"scores.parquet", index=False)
                (output/"counts.json").write_text(json.dumps(counts, indent=2, default=int))
        return pd.concat(results, ignore_index=True)
