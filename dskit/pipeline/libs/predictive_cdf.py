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

__all__ = ["MixtureCurve", "GridCurve", "CalibratedCurve", "ConvexCurve", "CDFEstimator",
           "HorizonEmpiricalCDF", "ScaledEmpiricalCDF", "MonotoneCDF",
           "QuantileCDF", "MixtureMLPCDF", "StudentMixtureCurve",
           "StudentMixtureMLPCDF", "QuantileForestCDF", "NGBoostCDF",
           "EmpiricalMLPBlendCDF", "ChronologicalCDFStudy", "CDFHyperparameterStudy"]


def _validate_temporal_frame(frame, date_field, end_field):
    """Refuse incomplete or noncanonical temporal metadata before filtering."""
    import pandas as pd

    parsed = []
    for field in (date_field, end_field):
        values = frame[field]
        strings = values.map(lambda value: isinstance(value, str))
        dates = pd.to_datetime(values.where(strings), format="%Y-%m-%d", errors="coerce")
        canonical = dates.dt.strftime("%Y-%m-%d").eq(values)
        if values.isna().any() or not (strings & dates.notna() & canonical).all():
            raise ValueError(f"invalid canonical ISO date metadata: {field}")
        parsed.append(dates)
    if not (parsed[1] > parsed[0]).all():
        raise ValueError("outcome date must be strictly after forecast date")


class _Curve(ABC):
    @abstractmethod
    def cdf(self, values):
        """Evaluate rowwise cumulative probabilities."""

    @abstractmethod
    def quantile(self, probabilities):
        """Evaluate rowwise inverse cumulative probabilities."""

    @abstractmethod
    def _arrays(self):
        """Return exact numerical research state, without executable objects."""


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
        self.weights = self.weights/self.weights.sum(1, keepdims=True)

    def _arrays(self):
        return {"kind": "mixture", "weights": self.weights,
                "means": self.means, "scales": self.scales}

    def _standard_cdf(self, values):
        from scipy.special import ndtr
        return ndtr(values)

    def _standard_quantile(self, probabilities):
        from scipy.special import ndtri
        return ndtri(probabilities)

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

        values = np.atleast_1d(values)
        values = np.broadcast_to(values, (len(self.weights), values.shape[-1]))
        result = np.zeros_like(values, dtype=float)
        for j in range(self.weights.shape[1]):
            result += self.weights[:, j, None] * self._standard_cdf(
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

        probabilities = np.atleast_1d(probabilities)
        p = np.broadcast_to(probabilities, (len(self.weights), probabilities.shape[-1]))
        if not np.isfinite(p).all() or (p <= 0).any() or (p >= 1).any():
            raise ValueError("quantile probabilities must be strictly inside (0,1)")
        component = self.means[:, :, None] + self.scales[:, :, None] * self._standard_quantile(p[:, None, :])
        lo, hi = component.min(1), component.max(1)
        if self.weights.shape[1] == 1:
            return lo
        for _ in range(42):
            mid = (lo + hi) / 2
            mask = self.cdf(mid) < p
            lo = np.where(mask, mid, lo)
            hi = np.where(mask, hi, mid)
        return (lo + hi) / 2


class StudentMixtureCurve(MixtureCurve):
    """Fixed-degree Student mixtures; scale is not standard deviation.

    Parameters
    ----------
    weights, means, scales : array-like
        Matching row/component arrays, as for MixtureCurve.
    degrees : float
        Finite degrees of freedom above two (finite log-return variance).

    Examples
    --------
    A heavy-tailed curve::

        curve = StudentMixtureCurve([[1]], [[0]], [[1]], degrees=5)
    """

    def __init__(self, weights, means, scales, degrees):
        import math
        if isinstance(degrees, bool) or not math.isfinite(degrees) or degrees <= 2:
            raise ValueError("degrees must be finite and greater than two")
        self.degrees = degrees
        super().__init__(weights, means, scales)

    def _standard_cdf(self, values):
        from scipy.special import stdtr
        return stdtr(self.degrees, values)

    def _standard_quantile(self, probabilities):
        from scipy.special import stdtrit
        return stdtrit(self.degrees, probabilities)

    def _arrays(self):
        return {**super()._arrays(), "kind": "student_mixture", "degrees": self.degrees}


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

    def _arrays(self):
        return {"kind": "grid", "values": self.values, "probabilities": self.probabilities}

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

        values = np.atleast_1d(values)
        queries = np.broadcast_to(values, (len(self.values), values.shape[-1]))
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

        probabilities = np.atleast_1d(probabilities)
        queries = np.broadcast_to(probabilities, self.values.shape[:1] + (probabilities.shape[-1],))
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

    def _arrays(self):
        return {**self.base._arrays(), "calibration_x": self.map.values,
                "calibration_p": self.map.probabilities}

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


class ConvexCurve(_Curve):
    """Convex combination of two row-compatible cumulative distributions.

    Parameters
    ----------
    left, right : curve
        Component distributions with the same forecast-row count.
    weight : float
        Right-component weight in the closed interval zero to one.

    Examples
    --------
    Average two finite grids::

        curve = ConvexCurve(GridCurve([[0, 1]], [0, 1]),
                            GridCurve([[1, 2]], [0, 1]), .5)
    """

    def __init__(self, left, right, weight):
        import math
        if (isinstance(weight, bool) or not isinstance(left, _Curve)
                or not isinstance(right, _Curve) or not math.isfinite(weight)
                or not 0 <= weight <= 1):
            raise ValueError("invalid convex curve or weight")
        if left.cdf([0]).shape[0] != right.cdf([0]).shape[0]:
            raise ValueError("convex components must have matching rows")
        self.left, self.right, self.weight = left, right, float(weight)

    @staticmethod
    def _prefixed(prefix, values):
        return {prefix+key: value for key, value in values.items()}

    def _arrays(self):
        return {"kind": "convex", "weight": self.weight,
                **self._prefixed("left_", self.left._arrays()),
                **self._prefixed("right_", self.right._arrays())}

    def cdf(self, values):
        """Return the exact convex sum of both component CDFs."""
        return (1-self.weight)*self.left.cdf(values)+self.weight*self.right.cdf(values)

    def quantile(self, probabilities):
        """Invert the convex CDF by deterministic bisection."""
        import numpy as np
        p = np.atleast_1d(probabilities).astype(float)
        if not np.isfinite(p).all() or (p <= 0).any() or (p >= 1).any():
            raise ValueError("quantile probabilities must be strictly inside (0,1)")
        if self.weight == 0:
            return self.left.quantile(p)
        if self.weight == 1:
            return self.right.quantile(p)
        rows = self.cdf([0]).shape[0]
        p = np.broadcast_to(p, (rows, p.shape[-1]))
        bounds = [curve.quantile(p) for curve in (self.left, self.right)]
        lo, hi = np.minimum(*bounds), np.maximum(*bounds)
        for _ in range(60):
            mid = (lo+hi)/2
            mask = self.cdf(mid) < p
            lo = np.where(mask, mid, lo)
            hi = np.where(mask, hi, mid)
        return (lo+hi)/2


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

    def _validate_x(self, x):
        """Validate raw identity features before optional imputation."""


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

    def __init__(self, horizon_index, reference_index, knots, condition_indices=None):
        self.horizon_index, self.reference_index, self.knots = horizon_index, reference_index, knots
        self.condition_indices = tuple(condition_indices or [horizon_index])

    def _validate_x(self, x):
        import numpy as np
        x = np.asarray(x)
        positions = (*self.condition_indices, self.reference_index)
        if (x.ndim != 2 or not positions or min(positions) < 0
                or max(positions) >= x.shape[1]
                or not np.isfinite(x[:, positions]).all()):
            raise ValueError("condition and reference features must be finite")

    def _keys(self, x):
        return [tuple(row) for row in x[:, self.condition_indices]]

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
        keys = self._keys(x)
        self.shapes = {key: np.quantile(raw[np.array([value == key for value in keys])], self.p)
                       for key in dict.fromkeys(keys)}
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

        values = np.array([self.shapes[key] for key in self._keys(x)])
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


class QuantileForestCDF(CDFEstimator):
    """Finite-grid conditional CDF from a mean-split quantile forest.

    Parameters
    ----------
    knots, trees, min_child, threads, seed : int
        Discretization and bounded forest controls.
    max_features : float
        Feature fraction per split.
    max_samples_leaf : int or None
        Optional retained samples per leaf; None retains the full leaf sample.

    Examples
    --------
    A small CPU forest::

        model = QuantileForestCDF(101, trees=100, min_child=20, threads=2)
    """

    def __init__(self, knots, trees=100, min_child=20, max_features=.5,
                 max_samples_leaf=None, threads=2, seed=829):
        import math
        values = (knots, trees, min_child, threads, seed)
        if (any(type(value) is not int for value in values) or knots < 3
                or min(trees, min_child, threads) < 1 or seed < 0
                or isinstance(max_features, bool) or not math.isfinite(max_features)
                or not 0 < max_features <= 1
                or (max_samples_leaf is not None
                    and (type(max_samples_leaf) is not int or max_samples_leaf < 1))):
            raise ValueError("invalid quantile-forest parameters")
        self.knots, self.trees, self.min_child = knots, trees, min_child
        self.max_features, self.threads, self.seed = max_features, threads, seed
        self.max_samples_leaf = max_samples_leaf

    def fit(self, x, y, cal_x, cal_y):
        """Fit one quantile-regression forest on training rows only."""
        from quantile_forest import RandomForestQuantileRegressor
        self.model = RandomForestQuantileRegressor(
            n_estimators=self.trees, min_samples_leaf=self.min_child,
            max_samples_leaf=self.max_samples_leaf,
            max_features=self.max_features, n_jobs=self.threads,
            random_state=self.seed)
        self.model.fit(x, y)
        return self

    def curve(self, x):
        """Return the declared finite-support forest discretization."""
        import numpy as np
        p = np.linspace(0, 1, self.knots)
        values = np.asarray(self.model.predict(x, quantiles=p.tolist()), dtype=float)
        if values.ndim == 1:
            values = values[:, None]
        values = np.maximum.accumulate(values, axis=1)
        return GridCurve(values, p)


class NGBoostCDF(CDFEstimator):
    """Normal NGBoost distribution trained with the CRP score.

    Parameters
    ----------
    trees, depth, min_child, seed : int
        Bounded boosting and base-tree controls.
    learning_rate, minibatch_frac, col_sample, tol : float
        NGBoost optimization controls.

    Examples
    --------
    A compact normal booster::

        model = NGBoostCDF(trees=100, depth=2, min_child=20)
    """

    def __init__(self, trees=100, depth=2, min_child=20, learning_rate=.03,
                 minibatch_frac=.8, col_sample=1., tol=1e-4, seed=829):
        import math
        ints = (trees, depth, min_child, seed)
        floats = (learning_rate, minibatch_frac, col_sample, tol)
        if (any(type(value) is not int for value in ints) or min(ints[:3]) < 1 or seed < 0
                or any(isinstance(value, bool) or not math.isfinite(value) for value in floats)
                or learning_rate <= 0 or tol < 0
                or not 0 < minibatch_frac <= 1 or not 0 < col_sample <= 1):
            raise ValueError("invalid NGBoost parameters")
        self.trees, self.depth, self.min_child = trees, depth, min_child
        self.learning_rate, self.minibatch_frac = learning_rate, minibatch_frac
        self.col_sample, self.tol, self.seed = col_sample, tol, seed

    def fit(self, x, y, cal_x, cal_y):
        """Fit Normal NGBoost with CRPScore on training rows only."""
        from ngboost import NGBRegressor
        from ngboost.distns import Normal
        from ngboost.scores import CRPScore
        from sklearn.tree import DecisionTreeRegressor
        base = DecisionTreeRegressor(max_depth=self.depth,
                                     min_samples_leaf=self.min_child,
                                     random_state=self.seed)
        self.model = NGBRegressor(
            Dist=Normal, Score=CRPScore, Base=base, natural_gradient=True,
            n_estimators=self.trees, learning_rate=self.learning_rate,
            minibatch_frac=self.minibatch_frac, col_sample=self.col_sample,
            verbose=False, tol=self.tol, random_state=self.seed)
        self.model.fit(x, y)
        return self

    def curve(self, x):
        """Return the fitted analytic normal distribution per row."""
        import numpy as np
        params = self.model.pred_dist(x).params
        loc, scale = np.asarray(params["loc"]), np.asarray(params["scale"])
        return MixtureCurve(np.ones((len(loc), 1)), loc[:, None], scale[:, None])


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
    activation, dropout, head_features : str, float, list or None
        Hidden activation, dropout probability, and raw one-hot task columns.
        A list for hidden declares individual layer widths; an integer keeps
        the legacy two-layer architecture. Heads share only the trunk.

    Examples
    --------
    Three-component CPU research candidate::

        model = MixtureMLPCDF(components=3, device="cpu")
    """

    def __init__(self, components=3, hidden=32, epochs=20, batch_size=1024,
                 seeds=(11, 29), device="cuda", learning_rate=.001,
                 weight_decay=.01, min_scale=.1, activation="tanh", dropout=0.,
                 head_features=None, deterministic=False):
        import math
        widths = [hidden, hidden] if type(hidden) is int else hidden
        if (not isinstance(widths, (list, tuple)) or not widths
                or any(type(v) is not int or v <= 0 for v in [components, epochs, batch_size, *widths])
                or any(not math.isfinite(v) for v in [learning_rate, weight_decay, min_scale, dropout])
                or min(learning_rate, min_scale) <= 0 or weight_decay < 0
                or not 0 <= dropout < 1 or activation not in ("tanh", "relu", "silu")
                or not seeds or any(type(v) is not int for v in seeds) or len(set(seeds)) != len(seeds)
                or type(deterministic) is not bool):
            raise ValueError("invalid mixture training parameters")
        if head_features is not None and (not head_features or len(set(head_features)) != len(head_features)
                or any(type(v) is not int or v < 0 for v in head_features)):
            raise ValueError("invalid head feature positions")
        self.components, self.hidden, self.epochs = components, tuple(widths), epochs
        self.batch_size, self.seeds, self.device = batch_size, seeds, device
        self.learning_rate, self.weight_decay, self.min_scale = learning_rate, weight_decay, min_scale
        self.activation, self.dropout = activation, dropout
        self.head_features = tuple(head_features or ())
        self.deterministic = deterministic

    def _deterministic_context(self):
        from contextlib import contextmanager
        import os
        import torch

        @contextmanager
        def configured():
            if not self.deterministic:
                yield
                return
            if (self.device.startswith("cuda")
                    and os.environ.get("CUBLAS_WORKSPACE_CONFIG") not in (":4096:8", ":16:8")):
                raise ValueError("deterministic CUDA requires CUBLAS_WORKSPACE_CONFIG before launch")
            old_algorithms = torch.are_deterministic_algorithms_enabled()
            old_cudnn = getattr(torch.backends.cudnn, "deterministic", False)
            old_benchmark = getattr(torch.backends.cudnn, "benchmark", False)
            torch.use_deterministic_algorithms(True)
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
            try:
                yield
            finally:
                torch.use_deterministic_algorithms(old_algorithms)
                torch.backends.cudnn.deterministic = old_cudnn
                torch.backends.cudnn.benchmark = old_benchmark
        return configured()

    def _validate_x(self, x):
        import numpy as np
        x = np.asarray(x)
        if self.head_features:
            if x.ndim != 2 or max(self.head_features) >= x.shape[1]:
                raise ValueError("head feature positions outside input")
            ids = x[:, self.head_features]
            if (not np.isfinite(ids).all() or not np.isin(ids, [0, 1]).all()
                    or not (ids.sum(1) == 1).all()):
                raise ValueError("head identifiers must be finite binary one-hot")

    def _heads(self, x):
        import numpy as np
        self._validate_x(x)
        return np.asarray(x)[:, self.head_features].argmax(1) if self.head_features else np.zeros(len(x), dtype=int)

    def _build_module(self, features):
        import torch
        activation = {"tanh": torch.nn.Tanh, "relu": torch.nn.ReLU, "silu": torch.nn.SiLU}[self.activation]
        layers = []
        for width in self.hidden:
            layers.extend([torch.nn.Linear(features, width), activation()])
            if self.dropout:
                layers.append(torch.nn.Dropout(self.dropout))
            features = width
        layers.append(torch.nn.Linear(features, 3*self.components*max(1, len(self.head_features))))
        return torch.nn.Sequential(*layers).to(self.device)

    def _forward(self, model, x, heads):
        import torch
        output = model(x).reshape(len(x), max(1, len(self.head_features)), 3*self.components)
        return self._parts(output[torch.arange(len(x), device=x.device), heads])

    def _logp(self, y, mu, sigma):
        import math
        return -.5*((y-mu)/sigma)**2 - sigma.log() - .5*math.log(2*math.pi)

    def _curve(self, weights, means, scales):
        return MixtureCurve(weights, means, scales)

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
        with self._deterministic_context():
            return self._fit(x, y, cal_x, cal_y)

    def _fit(self, x, y, cal_x, cal_y):
        import torch
        from sklearn.preprocessing import StandardScaler

        self._validate_x(cal_x)
        heads = self._heads(x)
        self.seen_heads = set(heads)
        hh = torch.tensor(heads, dtype=torch.long, device=self.device)
        self.scaler = StandardScaler().fit(x)
        xx = torch.tensor(self.scaler.transform(x), dtype=torch.float32, device=self.device)
        yy = torch.tensor(y[:, None], dtype=torch.float32, device=self.device)
        self.models, self.losses = [], []
        for seed in self.seeds:
            torch.manual_seed(seed)
            model = self._build_module(x.shape[1])
            optimizer = torch.optim.AdamW(model.parameters(), lr=self.learning_rate,
                                          weight_decay=self.weight_decay)
            losses = []
            for _ in range(self.epochs):
                order = torch.randperm(len(xx), device=self.device)
                total = 0.
                for start in range(0, len(xx), self.batch_size):
                    ix = order[start:start+self.batch_size]
                    logw, mu, sigma = self._forward(model, xx[ix], hh[ix])
                    logp = self._logp(yy[ix], mu, sigma)
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

    def _equivalence_state(self):
        """Digest effective preprocessing and every fitted network tensor."""
        import numpy as np
        digest = hashlib.sha256()
        settings = {"components": self.components, "hidden": self.hidden,
                    "epochs": self.epochs, "batch_size": self.batch_size,
                    "seeds": list(self.seeds), "device": self.device,
                    "learning_rate": self.learning_rate,
                    "weight_decay": self.weight_decay, "min_scale": self.min_scale,
                    "activation": self.activation, "dropout": self.dropout,
                    "head_features": self.head_features,
                    "deterministic": self.deterministic}
        digest.update(json.dumps(settings, sort_keys=True).encode())
        for name in ("mean_", "scale_", "var_"):
            value = np.asarray(getattr(self.scaler, name), dtype="<f8")
            digest.update(name.encode()+value.tobytes())
        for model in self.models:
            for name, value in sorted(model.state_dict().items()):
                array = value.detach().cpu().contiguous().numpy()
                digest.update(name.encode()+str(array.dtype).encode()+array.tobytes())
        return digest.hexdigest()

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

        heads = self._heads(x)
        if not set(heads).issubset(self.seen_heads):
            raise ValueError("unseen head requested")
        hh = torch.tensor(heads, dtype=torch.long, device=self.device)
        xx = torch.tensor(self.scaler.transform(x), dtype=torch.float32, device=self.device)
        weights, means, scales = [], [], []
        with torch.no_grad():
            for model in self.models:
                logw, mu, sigma = self._forward(model, xx, hh)
                weights.append(logw.exp().cpu().numpy()/len(self.models))
                means.append(mu.cpu().numpy())
                scales.append(sigma.cpu().numpy())
        return self._curve(np.concatenate(weights, 1), np.concatenate(means, 1),
                           np.concatenate(scales, 1))


class StudentMixtureMLPCDF(MixtureMLPCDF):
    """Student likelihood and CDF with the same fixed degrees and scale.

    Parameters
    ----------
    degrees : float
        Finite, above two; scale is not standard deviation.
    **settings : dict
        MixtureMLPCDF settings, including optional task heads.

    Examples
    --------
    A single heavy-tailed component::

        model = StudentMixtureMLPCDF(degrees=5, components=1)
    """

    def __init__(self, degrees, **settings):
        self.degrees = StudentMixtureCurve([[1]], [[0]], [[1]], degrees).degrees
        super().__init__(**settings)

    def _logp(self, y, mu, sigma):
        from torch.distributions import StudentT
        return StudentT(self.degrees, mu, sigma).log_prob(y)

    def _curve(self, weights, means, scales):
        return StudentMixtureCurve(weights, means, scales, self.degrees)


class EmpiricalMLPBlendCDF(CDFEstimator):
    """Conservative convex blend of conditioned empirical and mixture MLP CDFs.

    Parameters
    ----------
    mlp_weight : float
        MLP weight in the closed unit interval.
    condition_indices : list of int
        Exact-horizon and group-identity columns for the empirical constituent.
    reference_index, knots : int
        Standardization column and empirical quantile resolution.
    mlp : dict
        Exact constructor parameters for MixtureMLPCDF.

    Examples
    --------
    Prefer the empirical constituent::

        model = EmpiricalMLPBlendCDF(.25, [0, 2, 3], 1, 401,
                                     {"device": "cpu"})
    """

    def __init__(self, mlp_weight, condition_indices, reference_index, knots, mlp):
        import math
        if (isinstance(mlp_weight, bool) or not math.isfinite(mlp_weight)
                or not 0 <= mlp_weight <= 1 or not isinstance(mlp, dict)):
            raise ValueError("invalid empirical-MLP blend parameters")
        if (not isinstance(condition_indices, (list, tuple)) or not condition_indices
                or any(type(value) is not int or value < 0 for value in condition_indices)
                or len(set(condition_indices)) != len(condition_indices)):
            raise ValueError("invalid empirical condition indices")
        self.mlp_weight = float(mlp_weight)
        self.condition_indices = tuple(condition_indices)
        self.reference_index, self.knots = reference_index, knots
        self.mlp_settings = dict(mlp)
        self.empirical = HorizonEmpiricalCDF(condition_indices[0], reference_index, knots,
                                              condition_indices=condition_indices)
        self.mlp = MixtureMLPCDF(**self.mlp_settings)

    def _validate_x(self, x):
        self.empirical._validate_x(x)
        self.mlp._validate_x(x)

    def fit(self, x, y, cal_x, cal_y):
        """Fit both constituents on the same training rows."""
        self.empirical.fit(x, y, cal_x, cal_y)
        self.mlp.fit(x, y, cal_x, cal_y)
        return self

    def curve(self, x):
        """Return the exact configured convex CDF."""
        return ConvexCurve(self.empirical.curve(x), self.mlp.curve(x), self.mlp_weight)

    def _equivalence_state(self):
        """Delegate endpoint identity to the fitted MLP constituent."""
        return self.mlp._equivalence_state()


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
            "comparison_references", "identity", "series_identity", "notes"}

    def __init__(self, config):
        unknown = set(config)-self.KEYS
        missing = self.KEYS-{"notes"}-set(config)
        if unknown or missing:
            raise ValueError(f"unknown keys {unknown}; missing keys {missing}")
        for spec in config["models"].values():
            if (set(spec)-{"class", "params", "calibrate", "pooled", "equivalence", "notes"}
                    or not {"class", "params", "calibrate"}.issubset(spec)
                    or type(spec.get("pooled", False)) is not bool
                    or ("equivalence" in spec and (not isinstance(spec["equivalence"], str)
                                                   or not spec["equivalence"]))):
                raise ValueError("invalid model specification keys or pooled flag")
        self.config = config

    def to_obj(self):
        """Return the JSON configuration for the canonical identity owner."""
        return self.config

    def summarize(self, scores, selected_variants=None):
        """Select calibration on development only; report paired later scores.

        Parameters
        ----------
        scores : DataFrame
            Output of run, including development and research-validation years.
        selected_variants : dict or None
            Optional variants frozen by a prior development-only HPO stage.

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
        if selected_variants is not None:
            if set(selected_variants) != set(c["models"]) or any(v not in ("raw", "calibrated") for v in selected_variants.values()):
                raise ValueError("invalid frozen variants")
            selected = selected_variants
        picked = pd.concat([later[(later.model == name) & (later.variant == variant)]
                            for name, variant in selected.items()])
        metrics = ["crps", "tail_crps", "raw_return_crps", "strike_brier", "condor_loss_mse",
                   "condor_loss_bias", "below_05", "above_95", "payoff_quadrature_gap"]
        metrics = [m for m in metrics if m in scores]
        records, grids = [], []
        keys = c["identity"]
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
                                "expiry_series": f[c["series_identity"]].drop_duplicates().shape[0],
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
                model_rows = picked[picked.model == model].set_index(keys)
                compare = model_rows.crps
                if not compare.index.is_unique or not base_rows.index.is_unique or set(compare.index) != set(base_rows.index):
                    raise ValueError("comparison rows are not exactly paired")
                pair = pd.concat([compare.rename("candidate"), base_rows.rename("reference")], axis=1)
                for field in (c["group"], c["date"], c["horizon"]):
                    if field not in keys:
                        pair[field] = model_rows[field]
                pair = pair.reset_index()
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
        _validate_temporal_frame(frame, date_field, end_field)
        return ChronologicalCDFStudy._split_validated(frame, year, date_field, end_field)

    @staticmethod
    def _split_validated(frame, year, date_field, end_field):
        """Split a frame whose temporal columns were already validated."""
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
        _validate_temporal_frame(frame, c["date"], c["end"])
        output = Path(c["output"])
        output.mkdir(parents=True, exist_ok=False)
        from dskit.pipeline.base import config_hash
        (output/"protocol.json").write_text(json.dumps({**c, "config_sha256": config_hash(self, exclude=())}, indent=2))
        frame.to_parquet(output/"input_panel.parquet", index=False)
        from importlib.metadata import version
        distributions = ["numpy", "pandas", "scipy", "scikit-learn", "torch", "lightgbm"]
        classes = {spec["class"].rsplit(":", 1)[-1] for spec in c["models"].values()}
        if "QuantileForestCDF" in classes:
            distributions.append("quantile-forest")
        if "NGBoostCDF" in classes:
            distributions.append("ngboost")
        versions = {name: version(name) for name in distributions}
        import torch
        versions["cuda"] = torch.version.cuda
        versions["deterministic_algorithms"] = torch.are_deterministic_algorithms_enabled()
        versions["cudnn_deterministic"] = torch.backends.cudnn.deterministic
        (output/"versions.json").write_text(json.dumps(versions, indent=2))
        if ((frame[c["reference"]] <= 0).any()
                or not np.isfinite(frame[[c["target"], c["reference"]]]).all().all()
                or (frame[c["end"]] <= frame[c["date"]]).any()):
            raise ValueError("invalid target, reference or nonfuture outcome date")
        if frame[c["identity"]].isna().any().any() or frame.duplicated(c["identity"]).any():
            raise ValueError("missing or duplicate forecast identity")
        results, counts, pooled_cache = [], [], {}
        equivalence = {}
        for group, group_frame in frame.groupby(c["group"]):
            for year in c["years"]:
                fit, cal, val = self._split_validated(group_frame, year, c["date"], c["end"])
                if min(len(fit), len(cal), len(val)) < 1:
                    raise ValueError(f"empty band: {group} {year}")
                count = {"group": group, "year": year}
                for name, band in [("fit", fit), ("cal", cal), ("val", val)]:
                    count[name] = {"n": len(band), "dates": band[c["date"]].nunique(),
                                   "ends": band[c["end"]].nunique(),
                                   "expiry_series": band[c["series_identity"]].drop_duplicates().shape[0],
                                   "first": band[c["date"]].min(), "last": band[c["date"]].max(),
                                   "latest_label": band[c["end"]].max()}
                yc, yv = [(b[c["target"]]/b[c["reference"]]).to_numpy() for b in (cal, val)]
                for name, spec in c["models"].items():
                    start = time.monotonic()
                    key = (year, name, json.dumps(spec, sort_keys=True))
                    pooled = spec.get("pooled", False)
                    new_fit = not (pooled and key in pooled_cache)
                    if new_fit:
                        model_fit, model_cal, _ = self._split_validated(
                            frame, year, c["date"], c["end"]) if pooled else (fit, cal, val)
                        module, cls = spec["class"].split(":")
                        model = getattr(importlib.import_module(module), cls)(**spec["params"])
                        for band in (model_fit, model_cal, val):
                            model._validate_x(band[c["features"]].to_numpy())
                        imputer = SimpleImputer(strategy="median", keep_empty_features=True)
                        x = imputer.fit_transform(model_fit[c["features"]])
                        model.fit(x, (model_fit[c["target"]]/model_fit[c["reference"]]).to_numpy(),
                                  imputer.transform(model_cal[c["features"]]),
                                  (model_cal[c["target"]]/model_cal[c["reference"]]).to_numpy())
                        label = spec.get("equivalence")
                        if label:
                            if not hasattr(model, "_equivalence_state"):
                                raise ValueError("equivalence model does not expose fitted state")
                            state = model._equivalence_state()
                            population = "pooled" if pooled else str(group)
                            equivalence_key = (int(year), population, label)
                            record = equivalence.setdefault(
                                equivalence_key, {"digest": state, "members": []})
                            if record["digest"] != state:
                                raise ValueError("equivalence state mismatch")
                            record["members"].append(name)
                        fit_count = {"n": len(model_fit), "dates": model_fit[c["date"]].nunique(),
                                     "groups": model_fit.groupby(c["group"]).size().to_dict(),
                                     "latest_label": model_fit[c["end"]].max()}
                        if pooled:
                            pooled_cache[key] = model, imputer, fit_count
                    else:
                        model, imputer, fit_count = pooled_cache[key]
                    for band in (cal, val):
                        model._validate_x(band[c["features"]].to_numpy())
                    xc, xv = [imputer.transform(b[c["features"]]) for b in (cal, val)]
                    count[name+"_fit"] = {**fit_count, "new_fit": new_fit}
                    raw = model.curve(xv)
                    variants = {"raw": raw}
                    if spec["calibrate"]:
                        pit = model.curve(xc).cdf(yc[:, None])[:, 0]
                        variants["calibrated"] = CalibratedCurve(raw, pit, c["calibration_knots"])
                    else:
                        variants["calibrated"] = raw
                    for variant, curve in variants.items():
                        scored, draws = self.scores(curve, yv, c["samples"], c["tail_intervals"], c["tail_points"])
                        columns = list(dict.fromkeys([*c["identity"], *c["series_identity"],
                                                      c["group"], c["date"], c["end"], c["horizon"]]))
                        part = val[columns].copy()
                        part["year"], part["model"], part["variant"] = year, name, variant
                        for metric, values in scored.items():
                            part[metric] = values
                        part["raw_return_crps"] = part.crps.to_numpy()*val[c["reference"]].to_numpy()
                        if diagnostic:
                            for metric, values in diagnostic(val, curve, draws).items():
                                part[metric] = values
                        results.append(part)
                        # Retain both exact grids/maps/mixtures and quadrature draws.
                        # This is numerical research evidence, not a serving artifact.
                        if year == max(c["years"]):
                            np.savez_compressed(output/f"{group}-{name}-{variant}-curves.npz",
                                                draws=draws.astype("float32"),
                                                reference=val[c["reference"]].to_numpy(),
                                                identities=val[c["identity"]].astype(str).to_numpy(dtype=str),
                                                row_index=val.index.to_numpy(), **curve._arrays())
                    count[name+"_seconds"] = time.monotonic()-start
                    if isinstance(model, MixtureMLPCDF):
                        count[name+"_training_nll_by_seed"] = [loss[-1] for loss in model.losses]
                    print(group, year, name, round(count[name+"_seconds"], 2), flush=True)
                counts.append(count)
                pd.concat(results, ignore_index=True).to_parquet(output/"scores.parquet", index=False)
                (output/"counts.json").write_text(json.dumps(counts, indent=2, default=int))
        evidence = [{"year": year, "population": population, "label": label,
                     "digest": record["digest"],
                     "members": sorted(set(record["members"])),
                     "status": ("verified" if len(set(record["members"])) >= 2 else "unverified")}
                    for (year, population, label), record in sorted(equivalence.items())]
        (output/"equivalence.json").write_text(json.dumps(evidence, indent=2))
        return pd.concat(results, ignore_index=True)


class CDFHyperparameterStudy:
    """Bounded JSON inventory, development selection and frozen CDF evaluation.

    Parameters
    ----------
    config : dict
        A study plus experiment (axes, candidate specs, partitions, task mapping,
        exact cell sets, cutoff, resolutions and seeds). Data/diagnostic metadata
        can also be included; all are part of the canonical experiment identity.

    Examples
    --------
    Run one declared partition; every fit delegates to ChronologicalCDFStudy::

        study = CDFHyperparameterStudy(config)
        study.run(panel, stage="search", partition="separate", provenance=sources)
    """

    _KEYS = {"output", "development_years", "label_cutoff", "final_seeds", "screen_seed",
             "max_candidates", "candidates", "candidate_labels", "axes", "task_features",
             "resolutions", "expected_cells", "search_partitions", "evaluation_partitions"}
    _COMMON_KEYS = {"output", "development_years", "label_cutoff", "max_candidates",
                    "candidates", "task_features", "resolutions", "expected_cells",
                    "search_partitions", "evaluation_partitions"}

    def __init__(self, config):
        import copy
        import itertools
        from dskit.pipeline.kinds_search import CandidateInventory

        self.config = copy.deepcopy(config)
        if set(config)-{"study", "experiment", "data", "diagnostic", "notes"}:
            raise ValueError("unknown HPO document keys")
        self.base, self.experiment = self.config["study"], self.config["experiment"]
        c, e = self.base, self.experiment
        ChronologicalCDFStudy(c)
        self.grouped = "candidate_groups" in e
        required = self._COMMON_KEYS | ({"candidate_groups"} if self.grouped else
                                        {"final_seeds", "screen_seed", "candidate_labels", "axes"})
        if set(e)-required-{"notes"} or required-set(e):
            raise ValueError("unknown or missing experiment keys")
        self.inventory = CandidateInventory({"candidate": list(e["candidates"])}, max_candidates=e["max_candidates"])
        parts = e["search_partitions"]
        if self.grouped:
            groups = e["candidate_groups"]
            flat = [name for names in groups.values() for name in names]
            if (set(parts) != set(groups) or parts != groups
                    or any(not isinstance(names, list) or not names for names in groups.values())
                    or len(flat) != len(set(flat)) or set(flat) != set(e["candidates"])):
                raise ValueError("candidate groups must exactly partition inventory")
        else:
            axes = e["axes"]
            if set(axes) != {"sharing", "family", "bundle"}:
                raise ValueError("candidate axes must declare sharing, family, bundle")
            expected = set(itertools.product(axes["sharing"], axes["family"], axes["bundle"]))
            labels = e["candidate_labels"]
            if set(labels) != set(e["candidates"]) or any(set(v) != set(axes) for v in labels.values()):
                raise ValueError("candidate label identity mismatch")
            actual = [(v["sharing"], v["family"], v["bundle"]) for v in labels.values()]
            if set(actual) != expected or len(actual) != len(expected):
                raise ValueError("candidate inventory is not the declared Cartesian product")
            if set(parts) != set(axes["sharing"]):
                raise ValueError("search partitions must match sharing groups")
            for sharing, names in parts.items():
                if len(names) != len(set(names)) or set(names) != {n for n, v in labels.items() if v["sharing"] == sharing}:
                    raise ValueError("search partition candidate mismatch")
        if set(c["models"]) & set(e["candidates"]):
            raise ValueError("control and candidate names overlap")
        if (not self.grouped and (not e["final_seeds"] or len(set(e["final_seeds"])) != len(e["final_seeds"])
                or any(type(s) is not int for s in [e["screen_seed"], *e["final_seeds"]]))):
            raise ValueError("invalid frozen seeds")
        mapping = e["task_features"]
        if not mapping or len(set(mapping.values())) != len(mapping) or not set(mapping.values()).issubset(c["features"]):
            raise ValueError("invalid task feature mapping")
        head_positions = [c["features"].index(v) for v in mapping.values()]
        for name, spec in e["candidates"].items():
            if not self.grouped:
                label, p = labels[name], spec["params"]
                cls = {"normal": "MixtureMLPCDF", "student": "StudentMixtureMLPCDF"}.get(label["family"])
                if (cls is None or spec["class"] != f"dskit.pipeline.libs.predictive_cdf:{cls}"
                        or label["sharing"] not in ("separate", "pooled", "heads")
                        or spec.get("pooled", False) != (label["sharing"] != "separate")
                        or p.get("head_features", []) != (head_positions if label["sharing"] == "heads" else [])
                        or p.get("seeds") != [e["screen_seed"]]):
                    raise ValueError("candidate labels, class, heads or seed disagree")
            cls = spec["class"].rsplit(":", 1)[-1]
            ChronologicalCDFStudy({**c, "models": {name: spec}})
            module, class_name = spec["class"].split(":")
            getattr(importlib.import_module(module), class_name)(**spec["params"])
        if self.grouped:
            inventory_specs = {**c["models"], **e["candidates"]}
            labels = {}
            for name, spec in inventory_specs.items():
                if spec.get("equivalence"):
                    labels.setdefault(spec["equivalence"], []).append((name, spec.get("pooled", False)))
            if any(len(members) < 2 or len({pooled for _, pooled in members}) != 1
                   for members in labels.values()):
                raise ValueError("equivalence group needs pooled-compatible peers")
        r = e["resolutions"]
        if (set(r) != {"screen_samples", "final_samples", "tail_points", "integration_points", "audit_samples", "audit_rows"}
                or any(type(r[k]) is not int or r[k] < 1 for k in r if k != "audit_samples")
                or r["final_samples"] != c["samples"] or r["tail_points"] != c["tail_points"]
                or not {r["screen_samples"], r["final_samples"]}.issubset(r["audit_samples"])
                or any(type(n) is not int or n < 1 for n in r["audit_samples"])
                or ("diagnostic" in config and r["integration_points"] != config["diagnostic"]["integration_points"])):
            raise ValueError("invalid or mixed scoring resolution")
        dev = e["development_years"]
        years = [y for values in e["evaluation_partitions"].values() for y in values]
        if (not dev or len(set(dev)) != len(dev) or any(type(y) is not int for y in years+dev)
                or max(dev) != c["development_end"] or e["label_cutoff"] != f"{max(dev)+1}-01-01"
                or e["evaluation_partitions"].get("development") != dev
                or len(years) != len(set(years)) or set(years) != set(c["years"])):
            raise ValueError("development cutoff or evaluation year partition mismatch")
        if set(e["expected_cells"]) != {"development", "evaluation"}:
            raise ValueError("expected cells must declare development and evaluation")
        self.output = Path(e["output"])

    def to_obj(self):
        """Return the full JSON experiment for the canonical identity owner."""
        return self.config

    def _dependency_versions(self):
        """Return versions of optional libraries selected by configured classes."""
        from importlib.metadata import version
        classes = {spec["class"].rsplit(":", 1)[-1]
                   for spec in [*self.base["models"].values(), *self.experiment["candidates"].values()]}
        names = []
        if "QuantileForestCDF" in classes:
            names.append("quantile-forest")
        if "NGBoostCDF" in classes:
            names.append("ngboost")
        return {name: version(name) for name in sorted(names)}

    @staticmethod
    def _digest(value):
        from types import SimpleNamespace
        from dskit.pipeline.base import config_hash
        return config_hash(SimpleNamespace(to_obj=lambda: value), exclude=())

    @staticmethod
    def _file_hash(path):
        with Path(path).open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def _frame_hash(self, frame, keys):
        import pandas as pd
        f = frame.sort_values(keys).reindex(sorted(frame.columns), axis=1)
        schema = self._digest({k: str(v) for k, v in f.dtypes.items()})
        return hashlib.sha256(schema.encode()+pd.util.hash_pandas_object(f, index=False).values.tobytes()).hexdigest()

    @staticmethod
    def _write(path, value):
        from dskit.pipeline.node import atomic_write
        atomic_write(str(path), json.dumps(value, indent=2, allow_nan=False).encode())

    def _complete(self, path, identity, stage, partition, **extra):
        files = {str(p.relative_to(path)): self._file_hash(p) for p in sorted(path.rglob("*")) if p.is_file()}
        self._write(path/"complete.json", {"identity": identity, "stage": stage, "partition": partition,
                                           "files": files, **extra})

    def _load(self, path, identity, stage, partition):
        record = json.loads((path/"complete.json").read_text())
        if record["identity"] != identity or record["stage"] != stage or record["partition"] != partition:
            raise ValueError("artifact identity/provenance mismatch")
        actual_files = {str(p.relative_to(path)) for p in path.rglob("*") if p.is_file() and p != path/"complete.json"}
        if actual_files != set(record["files"]):
            raise ValueError("artifact file inventory mismatch")
        for name, digest in record["files"].items():
            if self._file_hash(path/name) != digest:
                raise ValueError("artifact hash mismatch")
        return record

    def _check_cells(self, frame, which):
        c = self.base
        expected = {(g, h) for g, horizons in self.experiment["expected_cells"][which].items() for h in horizons}
        actual = set(frame[[c["group"], c["horizon"]]].itertuples(index=False, name=None))
        if actual != expected:
            raise ValueError(f"{which} cell set mismatch: missing {expected-actual}, extra {actual-expected}")

    def _expected(self, frame, years):
        return frame[frame[self.base["date"]].str[:4].astype(int).isin(years)]

    def _check_scores(self, scores, expected, models):
        import numpy as np
        c = self.base
        columns = list(dict.fromkeys([*c["identity"], c["group"], c["date"], c["end"], c["horizon"]]))
        want = expected[columns].sort_values(c["identity"]).reset_index(drop=True)
        if set(scores.model) != set(models) or set(scores.variant) != {"raw", "calibrated"}:
            raise ValueError("score model or variant inventory mismatch")
        if scores.duplicated([*c["identity"], "model", "variant"]).any():
            raise ValueError("duplicate score identity")
        numeric = scores.select_dtypes(include="number")
        if not np.isfinite(numeric.to_numpy()).all():
            raise ValueError("nonfinite score; cells cannot be silently dropped")
        for name in models:
            for variant in ("raw", "calibrated"):
                rows = scores[(scores.model == name) & (scores.variant == variant)]
                got = rows[columns].sort_values(c["identity"]).reset_index(drop=True)
                if not got.equals(want) or not (rows.year == rows[c["date"]].str[:4].astype(int)).all():
                    raise ValueError("unpaired or substituted score identities/years")

    def _rank(self, scores):
        c = self.base
        means = scores.groupby([c["group"], c["horizon"], "model", "variant"]).crps.mean().unstack(["model", "variant"])
        if means.isna().any().any() or (means[(c["reference_model"], "raw")] <= 0).any():
            raise ValueError("incomplete cells or nonpositive reference CRPS")
        return means.div(means[(c["reference_model"], "raw")], axis=0).mean()

    def _select(self, frame, identity):
        import copy
        import pandas as pd
        e, c = self.experiment, self.base
        expected = self._expected(frame, e["development_years"])
        all_scores, controls, screen_specs, selected, variants = [], None, {}, {}, {}
        rankings = {}
        for partition, names in e["search_partitions"].items():
            path = self.output/"search"/partition
            self._load(path, identity, "search", partition)
            scores = pd.read_parquet(path/"scores.parquet")
            self._check_scores(scores, expected, [*c["models"], *names])
            repeated = scores[scores.model.isin(c["models"])].sort_values(["model", "variant", *c["identity"]]).reset_index(drop=True)
            if controls is not None and not controls.equals(repeated):
                raise ValueError("repeated control scores disagree")
            controls = repeated
            rank = self._rank(scores)
            rankings.update({f"{n}:{v}": float(rank[(n, v)]) for n in names for v in ("raw", "calibrated")})
            name, variant = min(((n, v) for n in names for v in ("raw", "calibrated")), key=lambda nv: rank[nv])
            screen_specs[name] = copy.deepcopy(e["candidates"][name])
            selected[name] = copy.deepcopy(screen_specs[name])
            if not self.grouped:
                selected[name]["params"]["seeds"] = list(e["final_seeds"])
            variants[name] = variant
            all_scores.append(scores[scores.model.isin(names)])
        rank = self._rank(controls)
        for name, spec in c["models"].items():
            selected[name] = copy.deepcopy(spec)
            variants[name] = min(("raw", "calibrated"), key=lambda v: rank[(name, v)])
        variants[c["reference_model"]] = "raw"
        payload = {"models": selected, "variants": variants, "ranking": rankings,
                   "screen_spec_hash": self._digest(screen_specs), "final_spec_hash": self._digest(selected),
                   "screen_specs": screen_specs, "expected_identity_hash": self._frame_hash(expected[c["identity"]], c["identity"]),
                   "n": len(expected), "cells": len(expected[[c["group"], c["horizon"]]].drop_duplicates())}
        path = self.output/"selection"
        path.mkdir(parents=True, exist_ok=False)
        self._write(path/"selected.json", payload)
        pd.concat([controls, *all_scores], ignore_index=True).to_parquet(path/"development_scores.parquet", index=False)
        self._complete(path, identity, "select", None)
        return payload

    def _selection(self, identity):
        path = self.output/"selection"
        record = self._load(path, identity, "select", None)
        payload = json.loads((path/"selected.json").read_text())
        return payload, record["files"]["selected.json"]

    def _audit(self, paths, frame, diagnostic):
        import numpy as np
        import pandas as pd
        c, r = self.base, self.experiment["resolutions"]
        identity_index = pd.MultiIndex.from_frame(frame[c["identity"]].astype(str))

        def restore(a, ix, prefix=""):
            kind = str(a[prefix+"kind"])
            if kind == "convex":
                return ConvexCurve(restore(a, ix, prefix+"left_"),
                                   restore(a, ix, prefix+"right_"),
                                   float(a[prefix+"weight"]))
            if kind == "grid":
                return GridCurve(a[prefix+"values"][ix], a[prefix+"probabilities"][ix])
            args = [a[prefix+k][ix] for k in ("weights", "means", "scales")]
            if kind == "student_mixture":
                return StudentMixtureCurve(*args, degrees=float(a[prefix+"degrees"]))
            return MixtureCurve(*args)

        records = []
        for path in paths:
            for file in sorted(path.glob("*-curves.npz")):
                with np.load(file, allow_pickle=False) as a:
                    n = len(a["row_index"])
                    ix = np.linspace(0, n-1, min(n, r["audit_rows"]), dtype=int)
                    curve = restore(a, ix)
                    if "calibration_x" in a:
                        calibrated = object.__new__(CalibratedCurve)
                        calibrated.base, calibrated.map = curve, GridCurve(a["calibration_x"], a["calibration_p"])
                        curve = calibrated
                    identities = np.asarray(a["identities"])[ix]
                    keys = pd.MultiIndex.from_arrays(
                        [identities[:, column] for column in range(identities.shape[1])])
                    positions = identity_index.get_indexer(keys)
                    if (positions < 0).any():
                        raise ValueError("saved curve identity absent from audit panel")
                    band = frame.iloc[positions]
                    y = (band[c["target"]]/band[c["reference"]]).to_numpy()
                    values = []
                    for nodes in r["audit_samples"]:
                        metrics, draws = ChronologicalCDFStudy.scores(curve, y, nodes, c["tail_intervals"], nodes)
                        if diagnostic:
                            metrics.update(diagnostic(band, curve, draws))
                        values.append({"samples": nodes, "crps": float(metrics["crps"].mean()),
                                       "tail_points": nodes, "tail_crps": float(metrics["tail_crps"].mean()),
                                       "max_payoff_gap": float(np.max(metrics.get("payoff_quadrature_gap", [0])))})
                    records.append({"file": str(file), "rows": len(ix), "resolutions": values})
        return records

    def run(self, frame, diagnostic=None, *, stage, partition=None, provenance):
        """Execute a declared stage; incomplete or unpaired inputs always refuse.

        Parameters
        ----------
        frame : DataFrame
            Complete causal panel with declared raw one-hot task columns.
        diagnostic : callable or None
            Existing bounded domain diagnostic.
        stage, partition : str, str or None
            search/select/evaluate/report and an explicit declared partition.
        provenance : dict
            Nonempty upstream file/reader fingerprints, pinned across stages.

        Returns
        -------
        DataFrame or dict
            Partition scores, frozen selection, or the completed report.
        """
        import pandas as pd
        c, e = self.base, self.experiment
        if stage not in ("search", "select", "evaluate", "report") or not provenance:
            raise ValueError("invalid stage or missing provenance")
        _validate_temporal_frame(frame, c["date"], c["end"])
        if frame.duplicated(c["identity"]).any() or frame[c["identity"]].isna().any().any():
            raise ValueError("missing or duplicate panel identities")
        mapping = e["task_features"]
        if set(frame[c["group"]]) != set(mapping):
            raise ValueError("unknown or missing task symbols")
        for group, feature in mapping.items():
            if not (frame[feature] == (frame[c["group"]] == group).astype(int)).all():
                raise ValueError("raw one-hot task features disagree with symbol mapping")
        dev = frame[frame[c["end"]] < e["label_cutoff"]]
        self._check_cells(self._expected(dev, e["development_years"]), "development")
        later_years = [y for y in c["years"] if y > c["development_end"]]
        self._check_cells(self._expected(frame, later_years), "evaluation")
        identity = {"config": self._digest(self.config), "panel": self._frame_hash(frame, c["identity"]),
                    "provenance": self._digest(provenance), "inventory": self.inventory.digest,
                    "implementation": self._file_hash(__file__),
                    "resolutions": e["resolutions"],
                    "dependencies": self._dependency_versions()}
        if stage == "select":
            if partition is not None:
                raise ValueError("select does not accept partition")
            return self._select(dev, identity)
        if stage == "search":
            if partition not in e["search_partitions"]:
                raise ValueError("unknown search partition")
            models = {**c["models"], **{n: e["candidates"][n] for n in e["search_partitions"][partition]}}
            years, panel, samples = e["development_years"], dev, e["resolutions"]["screen_samples"]
            selection_hash = None
        else:
            selection, selection_hash = self._selection(identity)
            models = selection["models"]
            if stage == "report":
                if partition is not None:
                    raise ValueError("report does not accept partition")
                parts, paths = [], []
                for name, years in e["evaluation_partitions"].items():
                    path = self.output/"evaluate"/name
                    record = self._load(path, identity, "evaluate", name)
                    if record["selection_hash"] != selection_hash:
                        raise ValueError("frozen selection hash mismatch")
                    part = pd.read_parquet(path/"scores.parquet")
                    self._check_scores(part, self._expected(dev if name == "development" else frame, years), models)
                    parts.append(part)
                    paths.append(path)
                scores = pd.concat(parts, ignore_index=True)
                path = self.output/"report"
                path.mkdir(parents=True, exist_ok=False)
                study = ChronologicalCDFStudy({**c, "output": str(path), "models": models})
                result = study.summarize(scores, selection["variants"])
                scores.to_parquet(path/"scores.parquet", index=False)
                self._write(path/"convergence.json", self._audit(paths, frame, diagnostic))
                self._complete(path, identity, stage, None, selection_hash=selection_hash)
                return result
            if partition not in e["evaluation_partitions"]:
                raise ValueError("unknown evaluation partition")
            years = e["evaluation_partitions"][partition]
            panel = dev if partition == "development" else frame
            samples = e["resolutions"]["final_samples"]
        path = self.output/stage/partition
        study = ChronologicalCDFStudy({**c, "output": str(path), "years": years, "samples": samples, "models": models})
        scores = study.run(panel, diagnostic)
        self._check_scores(scores, self._expected(panel, years), models)
        self._write(path/"data_provenance.json", provenance)
        self._complete(path, identity, stage, partition, selection_hash=selection_hash)
        return scores
