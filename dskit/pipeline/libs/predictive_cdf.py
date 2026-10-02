"""Offline conditional CDFs and paired chronological research (ADR-0189).

This array-oriented library pack complements the existing sample-score and
fitted-transform seams; it does not grant serving authority. All statistical
libraries are imported at method depth. Curves use one row per forecast and
one column per query. Models predict a caller-standardized scalar outcome.
"""

from abc import ABC, abstractmethod
from functools import lru_cache
import hashlib
import importlib
import json
from pathlib import Path
import time

from dskit.pipeline.document import date_problem
from dskit.pipeline.kinds_split import label_reaches
from dskit.pipeline.node import Node, JsonArtifact, reject_unknown_params

__all__ = ["MixtureCurve", "GridCurve", "CalibratedCurve", "ConvexCurve",
           "BetaTransformedCurve", "CDFEstimator",
           "HorizonEmpiricalCDF", "ScaledEmpiricalCDF", "MonotoneCDF",
           "QuantileCDF", "CatBoostQuantileCDF", "OptionImpliedTransportCDF",
           "TailConstrainedQuantileBlendCDF", "DynamicPITRecalibratedCDF",
           "BetaTransformedPoolCDF", "SemiparametricGPDTailCDF",
           "SplineFlowCDF", "SetMixtureCDF", "MixtureMLPCDF", "StudentMixtureCurve",
           "StudentMixtureMLPCDF", "QuantileForestCDF", "NGBoostCDF",
           "EmpiricalMLPBlendCDF", "AdaptiveEmpiricalMLPBlendCDF", "PCAAugmentedCDF",
           "DecisionWeightedMixtureMLPCDF", "DecisionWeightedMonotoneCDF",
           "ChronologicalCDFStudy", "CDFHyperparameterStudy", "CDFThresholdAudit",
           "DiscreteCDFGrid", "TorchCDF", "DecisionRegionScores",
           "OptionPriceCDF", "ExpiryCloseLabels", "OptionCDFPanel"]

# Gauss-Legendre points per sub-piece: ONE constant serves the Torch wing and
# CRPS terms (training) and the numpy curve rule (evaluation), so a loss and the
# metric that scores it can never integrate on different grids (ADR-0218).
_QUADRATURE_NODES = 64
# Positive remainder terms of the Student lower-tail series (ADR-0218).
_STUDENT_TAIL_TERMS = 64


@lru_cache(maxsize=None)
def _legendre_rule(n):
    import numpy as np
    return np.polynomial.legendre.leggauss(n)


def _gauss_legendre(n):
    """Return fresh copies of the n-point Gauss-Legendre nodes and weights."""
    nodes, weights = _legendre_rule(n)
    return nodes.copy(), weights.copy()


class CDFThresholdAudit:
    """Score one coherent CDF on thresholds fixed before the forecast is ranked.

    ``intervals`` is the complete entry-known decision-region inventory, not a
    selected position. The positive floor keeps the whole declared support in
    the proper threshold score. All values use the caller's outcome units.
    """

    def __init__(self, grid, intervals, floor):
        import numpy as np

        self.grid = np.asarray(grid, dtype=float)
        if (self.grid.ndim != 1 or len(self.grid) < 2
                or not np.isfinite(self.grid).all()
                or not (np.diff(self.grid) > 0).all()
                or not np.isfinite(floor) or floor <= 0):
            raise ValueError("invalid threshold grid or positive floor")
        self.midpoints = (self.grid[:-1] + self.grid[1:]) / 2
        self.widths = np.diff(self.grid)
        coverage = np.zeros(len(self.midpoints))
        for bounds in intervals:
            if (len(bounds) != 2 or not np.isfinite(bounds).all()
                    or not self.grid[0] <= bounds[0] < bounds[1] <= self.grid[-1]):
                raise ValueError("interval outside threshold grid")
            coverage += ((self.midpoints >= bounds[0])
                         & (self.midpoints < bounds[1]))
        self.weights = floor + coverage / max(1., coverage.max(initial=0.))

    def _cdf(self, cdf):
        import numpy as np

        values = np.asarray(cdf, dtype=float)
        if (values.shape != self.grid.shape or not np.isfinite(values).all()
                or (values < 0).any() or (values > 1).any()
                or (np.diff(values) < -1e-12).any()):
            raise ValueError("invalid coherent CDF on threshold grid")
        return values

    def strike_brier(self, cdf, strike, outcome):
        """Brier loss for the inclusive expiry event at one declared strike."""
        import numpy as np

        values = self._cdf(cdf)
        if (not np.isfinite([strike, outcome]).all()
                or not self.grid[0] <= strike <= self.grid[-1]):
            raise ValueError("strike outside threshold grid")
        return float((np.interp(strike, self.grid, values)
                      - float(outcome <= strike)) ** 2)

    def weighted_crps(self, cdf, outcome, *, midpoint_cdf=None):
        """Positive fixed-weight discrete approximation to threshold CRPS."""
        import numpy as np

        values = self._cdf(cdf)
        if not np.isfinite(outcome):
            raise ValueError("invalid outcome")
        probabilities = (np.interp(self.midpoints, self.grid, values)
                         if midpoint_cdf is None else np.asarray(midpoint_cdf, dtype=float))
        if (probabilities.shape != self.midpoints.shape
                or not np.isfinite(probabilities).all()
                or (probabilities < 0).any() or (probabilities > 1).any()
                or (np.diff(probabilities) < -1e-12).any()):
            raise ValueError("invalid midpoint CDF")
        truth = (outcome <= self.midpoints).astype(float)
        mass = self.weights * self.widths
        return float(np.dot(mass, (probabilities - truth) ** 2) / mass.sum())

    def expected_spread_loss(self, cdf, low, high, right):
        """Integrate put CDF or call survival over a capped spread wing."""
        import numpy as np

        values = self._cdf(cdf)
        if (right not in ("put", "call") or not np.isfinite([low, high]).all()
                or not self.grid[0] <= low < high <= self.grid[-1]):
            raise ValueError("invalid spread interval or right")
        interior = self.grid[(self.grid > low) & (self.grid < high)]
        points = np.r_[low, interior, high]
        probabilities = np.interp(points, self.grid, values)
        return float(np.trapezoid(probabilities if right == "put" else
                                  1 - probabilities, points))

    @staticmethod
    def gridcurve_log_spread_loss(curve, row, low, high, right, spot, scale):
        """Integrate an archived piecewise CDF exactly in log-price space.

        Equal curve abscissae are atoms. Splitting at each distinct knot and
        integrating the open segments prevents a jump from being smeared over
        the preceding price-grid cell.
        """
        import numpy as np

        if (not isinstance(curve, GridCurve) or type(row) is not int
                or not 0 <= row < len(curve.values)
                or right not in ("put", "call")
                or not np.isfinite([low, high, spot, scale]).all()
                or not 0 < low < high or spot <= 0 or scale <= 0):
            raise ValueError("invalid GridCurve spread integral")
        values, probabilities = curve.values[row], curve.probabilities[row]
        z_low, z_high = np.log(low/spot)/scale, np.log(high/spot)/scale
        inner = values[(values > z_low) & (values < z_high)]
        points = np.r_[low, spot*np.exp(scale*np.unique(inner)), high]
        integral = 0.
        for left, upper in zip(points[:-1], points[1:]):
            if upper <= left:
                continue
            midpoint = (left+upper)/2
            index = int(np.searchsorted(values, np.log(midpoint/spot)/scale,
                                        side="right")-1)
            if index < 0:
                continue
            if index >= len(values)-1:
                integral += upper-left
                continue
            slope = ((probabilities[index+1]-probabilities[index]) /
                     (values[index+1]-values[index]))
            intercept = probabilities[index]-slope*values[index]
            log_area = (upper*np.log(upper/spot)-upper
                        -left*np.log(left/spot)+left)
            integral += intercept*(upper-left)+(slope/scale)*log_area
        return float(integral if right == "put" else high-low-integral)


class DiscreteCDFGrid:
    """Clip a coherent CDF to one shared support and stress it by 1D W1.

    The rightmost mass absorbs the upper tail. On a grid containing every
    payoff kink, clipping does not change bounded payoffs outside the support.
    Transport budgets use price distance divided by entry spot.
    """

    def __init__(self, grid, cdf):
        import numpy as np

        self.grid = np.asarray(grid, dtype=float)
        values = np.asarray(cdf, dtype=float)
        if (self.grid.ndim != 1 or len(self.grid) < 2
                or values.shape != self.grid.shape
                or not np.isfinite(self.grid).all()
                or not (np.diff(self.grid) > 0).all()
                or not np.isfinite(values).all()
                or (values < 0).any() or (values > 1).any()
                or (np.diff(values) < -1e-12).any()):
            raise ValueError("invalid grid or coherent CDF")
        self.masses = np.diff(np.r_[0., values[:-1], 1.])
        if (self.masses < -1e-12).any():
            raise ValueError("invalid CDF masses")
        self.masses = np.maximum(self.masses, 0.)
        self.masses /= self.masses.sum()
        self._matrix = None
        self._spot = None

    def _constraints(self, spot):
        import numpy as np
        from scipy.sparse import csr_matrix, hstack, vstack

        if self._matrix is not None and self._spot == spot:
            return self._matrix
        n = len(self.grid)
        cumulative = csr_matrix(np.tril(np.ones((n - 1, n))))
        identity = csr_matrix(np.eye(n - 1))
        zeros = csr_matrix((1, n))
        transport = csr_matrix(((np.diff(self.grid) / spot),
                                (np.zeros(n - 1, dtype=int), np.arange(n - 1))),
                               shape=(1, n - 1))
        constraints = vstack((hstack((cumulative, -identity)),
                              hstack((-cumulative, -identity)),
                              hstack((zeros, transport))), format="csr")
        cumulative_nominal = np.cumsum(self.masses)[:-1]
        self._matrix = constraints, cumulative_nominal
        self._spot = spot
        return self._matrix

    def worst_expected_loss(self, loss, radius, spot):
        """Maximize a fixed bounded payoff over the shared discrete W1 ball."""
        import numpy as np
        from scipy.optimize import linprog

        values = np.asarray(loss, dtype=float)
        if (values.shape != self.masses.shape or not np.isfinite(values).all()
                or not np.isfinite(radius) or radius < 0
                or not np.isfinite(spot) or spot <= 0):
            raise ValueError("invalid loss, radius or spot")
        nominal = float(self.masses @ values)
        if radius == 0:
            return nominal
        matrix, cumulative = self._constraints(spot)
        n = len(self.grid)
        rhs = np.r_[cumulative, -cumulative, radius]
        objective = np.r_[-values, np.zeros(n - 1)]
        equality = np.r_[np.ones(n), np.zeros(n - 1)][None, :]
        result = linprog(objective, A_ub=matrix, b_ub=rhs,
                         A_eq=equality, b_eq=[1.], bounds=(0, None), method="highs")
        if not result.success or not np.isfinite(result.fun):
            raise ValueError(f"W1 worst-loss program failed: {result.message}")
        return max(nominal, float(-result.fun))

    def choose(self, candidates, radius, spot):
        """Select the maximum positive robust value; ties favor no trade."""
        import numpy as np

        if not isinstance(candidates, (list, tuple)):
            raise ValueError("candidates must be a finite declared sequence")
        seen = set()
        for item in candidates:
            if (not isinstance(item, dict) or set(item) != {"id", "net_credit", "loss"}
                    or not isinstance(item["id"], str) or not item["id"]
                    or item["id"] in seen or not np.isfinite(item["net_credit"])
                    or np.asarray(item["loss"]).shape != self.masses.shape
                    or not np.isfinite(item["loss"]).all()
                    or (np.asarray(item["loss"]) < 0).any()):
                raise ValueError("invalid or duplicate candidate")
            seen.add(item["id"])
        best = {"id": None, "robust_value": 0., "worst_loss": 0.}
        for item in sorted(candidates, key=lambda value: value["id"]):
            worst = self.worst_expected_loss(item["loss"], radius, spot)
            value = float(item["net_credit"] - worst)
            if value > best["robust_value"]:
                best = {"id": item["id"], "robust_value": value,
                        "worst_loss": worst}
        return best


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

    def _quadrature_breakpoints(self):
        """Return rowwise interior kinks the numeric rule splits at, or None."""
        return None

    def _segment_integrals(self, lower, upper, y):
        """Integrate (F(z) - 1{y <= z})**2 over (rows, segments) bounds.

        Each segment is cut at the clipped outcome and at the curve's
        breakpoints, with ``_QUADRATURE_NODES`` Gauss-Legendre points per
        sub-piece; a curve with unlisted kinks is a bounded approximation.
        """
        import numpy as np

        nodes, weights = _gauss_legendre(_QUADRATURE_NODES)
        kinks = self._quadrature_breakpoints()
        result = np.zeros(lower.shape)
        for s in range(lower.shape[1]):
            low, high = lower[:, s], upper[:, s]
            if not (high > low).any():
                continue
            cut = np.clip(y, low, high)
            for start, stop, left in ((low, cut, True), (cut, high, False)):
                edges = np.column_stack([start, stop]) if kinks is None else np.sort(
                    np.column_stack([start, np.clip(kinks, start[:, None], stop[:, None]),
                                     stop]), axis=1)
                for a, b in zip(edges[:, :-1].T, edges[:, 1:].T):
                    z = ((a+b)/2)[:, None] + ((b-a)/2)[:, None]*nodes
                    f = self.cdf(z)
                    result[:, s] += (b-a)/2*(((f if left else 1-f)**2)*weights).sum(1)
        return result


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

    def _quadrature_breakpoints(self):
        return self.means

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

    def _segment_integrals(self, lower, upper, y):
        """Exact rule: F is piecewise linear, so 2-point Gauss-Legendre is exact per cell."""
        import numpy as np

        offset = 1/np.sqrt(3)
        result = np.zeros(lower.shape)
        for row in range(lower.shape[0]):
            for s in range(lower.shape[1]):
                low, high = lower[row, s], upper[row, s]
                if not high > low:
                    continue
                knots = self.values[row]
                edges = np.unique(np.r_[low, high, min(max(y[row], low), high),
                                        knots[(knots > low) & (knots < high)]])
                mid, half = (edges[:-1]+edges[1:])/2, (edges[1:]-edges[:-1])/2
                truth = (y[row] <= mid).astype(float)
                for point in (mid-half*offset, mid+half*offset):
                    f = np.interp(point, knots, self.probabilities[row], left=0, right=1)
                    result[row, s] += (half*(f-truth)**2).sum()
        return result

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
    weight : float or array-like
        Right-component weight in the closed interval zero to one, either
        shared by all rows or one value per forecast row.

    Examples
    --------
    Average two finite grids::

        curve = ConvexCurve(GridCurve([[0, 1]], [0, 1]),
                            GridCurve([[1, 2]], [0, 1]), .5)
    """

    def __init__(self, left, right, weight):
        import numpy as np
        if not isinstance(left, _Curve) or not isinstance(right, _Curve):
            raise ValueError("invalid convex curve or weight")
        if left.cdf([0]).shape[0] != right.cdf([0]).shape[0]:
            raise ValueError("convex components must have matching rows")
        rows = left.cdf([0]).shape[0]
        if isinstance(weight, (bool, np.bool_)):
            raise ValueError("invalid convex curve weight")
        raw = np.asarray(weight)
        if raw.dtype.kind not in "fiu" or raw.ndim > 2:
            raise ValueError("invalid convex curve weight")
        if raw.ndim == 0:
            value = float(raw)
        elif raw.shape in ((rows,), (rows, 1)):
            value = np.asarray(raw, dtype=float).reshape(rows, 1)
        else:
            raise ValueError("invalid convex curve row weight shape")
        if not np.isfinite(value).all() or np.any(np.asarray(value) < 0) or np.any(np.asarray(value) > 1):
            raise ValueError("invalid convex curve weight")
        self.left, self.right, self.weight = left, right, value

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
        if isinstance(self.weight, float) and self.weight == 0:
            return self.left.quantile(p)
        if isinstance(self.weight, float) and self.weight == 1:
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


class BetaTransformedCurve(_Curve):
    """Apply a beta-CDF calibration map to a row-compatible base CDF."""

    def __init__(self, base, alpha, beta):
        import math
        if (not isinstance(base, _Curve) or isinstance(alpha, bool) or isinstance(beta, bool)
                or not math.isfinite(alpha) or not math.isfinite(beta)
                or alpha <= 0 or beta <= 0):
            raise ValueError("invalid beta-transformed curve")
        self.base, self.alpha, self.beta = base, float(alpha), float(beta)

    def cdf(self, values):
        from scipy.special import betainc
        return betainc(self.alpha, self.beta, self.base.cdf(values))

    def quantile(self, probabilities):
        import numpy as np
        from scipy.special import betaincinv
        p = np.asarray(probabilities, dtype=float)
        if not np.isfinite(p).all() or (p <= 0).any() or (p >= 1).any():
            raise ValueError("quantile probabilities must be strictly inside (0,1)")
        return self.base.quantile(betaincinv(self.alpha, self.beta, p))

    def _arrays(self):
        return {"kind": "beta_transformed", "alpha": self.alpha, "beta": self.beta,
                **{"base_"+key: value for key, value in self.base._arrays().items()}}


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


def _decision_threshold_inventory(context):
    """Validate immutable identity-keyed, entry-known decision thresholds."""
    import numpy as np

    if not isinstance(context, (list, tuple)):
        raise ValueError("decision context must be an ordered sequence")
    inventories = []
    identities = []
    required = {"identity", "thresholds", "weights", "intervals", "status", "clock",
                "provenance_sha256"}
    for record in context:
        if not isinstance(record, dict) or set(record) != required:
            raise ValueError("invalid decision-context record")
        identity = tuple(record["identity"])
        if len(identity) != 3 or any(not isinstance(v, str) or not v for v in identity):
            raise ValueError("invalid decision-context identity")
        if record["clock"] != "after_date_close_indicative_not_executable":
            raise ValueError("executable or unknown decision clock refused")
        if (not isinstance(record["provenance_sha256"], str)
                or len(record["provenance_sha256"]) != 64
                or any(v not in "0123456789abcdef"
                       for v in record["provenance_sha256"])):
            raise ValueError("invalid decision-context provenance")
        cutoffs = np.asarray(record["thresholds"], dtype=float)
        weights = np.asarray(record["weights"], dtype=float)
        if (not isinstance(record["intervals"], list)
                or any(not isinstance(pair, list) or len(pair) != 2
                       for pair in record["intervals"])):
            raise ValueError("invalid decision-wing intervals")
        intervals = np.asarray(record["intervals"], dtype=float).reshape(-1, 2)
        if (cutoffs.ndim != 1 or weights.shape != cutoffs.shape
                or not np.isfinite(cutoffs).all() or not np.isfinite(weights).all()
                or (weights <= 0).any() or list(cutoffs) != sorted(set(cutoffs.tolist()))):
            raise ValueError("decision thresholds must be finite, unique and weighted")
        if (not np.isfinite(intervals).all()
                or (len(intervals) and (intervals[:, 0] >= intervals[:, 1]).any())):
            raise ValueError("invalid decision-wing intervals")
        if len(cutoffs):
            if record["status"] != "eligible" or not np.isclose(weights.sum(), 1.):
                raise ValueError("invalid eligible decision context")
        elif record["status"] != "no_eligible_condor":
            raise ValueError("empty decision context lacks explicit refusal")
        identities.append(identity)
        inventories.append((cutoffs, weights))
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate decision-context identity")
    if len({record["provenance_sha256"] for record in context}) > 1:
        raise ValueError("mixed decision-context provenance")
    return inventories


class DecisionWeightedMonotoneCDF(MonotoneCDF):
    """Monotone LightGBM CDF trained at global and actual entry strikes.

    LightGBM's scalar custom-objective API cannot soundly express a coupled
    multi-parameter distribution. This model instead uses proper binary
    threshold log loss on a global grid plus each row's listed strikes.
    """

    def __init__(self, thresholds, decision_weight=1., global_weight=1., **settings):
        import math

        super().__init__(thresholds=thresholds, **settings)
        if any(isinstance(v, bool) or not math.isfinite(v) or v <= 0
               for v in (decision_weight, global_weight)):
            raise ValueError("invalid decision-weighted LightGBM parameters")
        self.decision_weight = float(decision_weight)
        self.global_weight = float(global_weight)

    def fit_decision_context(self, fit_context, cal_context):
        self._fit_decisions = _decision_threshold_inventory(fit_context)
        _decision_threshold_inventory(cal_context)
        return self

    def fit(self, x, y, cal_x, cal_y):
        """Fit weighted threshold events defined before outcomes are observed."""
        import numpy as np
        from lightgbm import LGBMClassifier

        if not hasattr(self, "_fit_decisions") or len(self._fit_decisions) != len(x):
            raise ValueError("identity-keyed decision context was not attached")
        inventory = self._fit_decisions
        expanded, labels, weights = [], [], []
        global_mass = self.global_weight/len(self.thresholds)
        for row, outcome, (cutoffs, strike_weights) in zip(np.asarray(x), y, inventory):
            for cutoff in self.thresholds:
                expanded.append(np.r_[row, cutoff])
                labels.append(outcome <= cutoff)
                weights.append(global_mass)
            for cutoff, weight in zip(cutoffs, strike_weights):
                expanded.append(np.r_[row, cutoff])
                labels.append(outcome <= cutoff)
                weights.append(self.decision_weight*weight)
        expanded = np.asarray(expanded, dtype=float)
        self.model = LGBMClassifier(
            **self.settings, monotone_constraints=[0]*np.asarray(x).shape[1]+[1])
        self.model.fit(expanded, np.asarray(labels, dtype=int),
                       sample_weight=np.asarray(weights, dtype=float))
        return self

    def curve_decision_context(self, x, context):
        """Predict directly at every row's decision strikes, without interpolation."""
        import numpy as np

        inventory = _decision_threshold_inventory(context)
        if len(inventory) != len(x):
            raise ValueError("decision curve context length mismatch")
        knots = [np.unique(np.r_[self.thresholds, cutoffs])
                 for cutoffs, _ in inventory]
        width = max(len(values) for values in knots)+2
        values = np.empty((len(x), width), dtype=float)
        probabilities = np.empty_like(values)
        for row, (features, cutoffs) in enumerate(zip(np.asarray(x), knots)):
            expanded = np.column_stack([
                np.repeat(features[None, :], len(cutoffs), axis=0), cutoffs])
            p = self.model.predict_proba(expanded)[:, 1]
            if (np.diff(p) < -1e-10).any():
                raise ValueError("monotone classifier returned crossing CDFs")
            grid = np.r_[cutoffs[0]-self.tail_width, cutoffs,
                         cutoffs[-1]+self.tail_width]
            cdf = np.r_[0., p, 1.]
            values[row, :len(grid)] = grid
            probabilities[row, :len(grid)] = cdf
            values[row, len(grid):] = grid[-1]
            probabilities[row, len(grid):] = 1.
        return GridCurve(values, probabilities)

    def _research_state(self):
        return {"objective": "weighted_binary_logloss",
                "decision_weight": self.decision_weight,
                "global_weight": self.global_weight,
                "strike_weighting": "deduplicated_put_call_balanced"}


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


class CatBoostQuantileCDF(CDFEstimator):
    """One native CatBoost MultiQuantile model with rearranged finite tails."""

    def __init__(self, probabilities, iterations=200, depth=6, learning_rate=.05,
                 l2_leaf_reg=5., threads=2, task_type="CPU", tail_width=4., seed=829,
                 feature_indices=None):
        import math
        if (len(probabilities) < 2 or probabilities[0] <= 0 or probabilities[-1] >= 1
                or any(a >= b for a, b in zip(probabilities, probabilities[1:]))
                or any(type(v) is not int or v < 1 for v in (iterations, depth, threads))
                or task_type not in ("CPU", "GPU") or not math.isfinite(learning_rate)
                or not math.isfinite(l2_leaf_reg) or not math.isfinite(tail_width)
                or min(learning_rate, tail_width) <= 0 or l2_leaf_reg < 0
                or type(seed) is not int or seed < 0
                or (feature_indices is not None and
                    (not feature_indices or len(set(feature_indices)) != len(feature_indices)
                     or any(type(v) is not int or v < 0 for v in feature_indices)))):
            raise ValueError("invalid CatBoost multi-quantile parameters")
        self.probabilities = tuple(float(p) for p in probabilities)
        self.tail_width = float(tail_width)
        self.feature_indices = None if feature_indices is None else tuple(feature_indices)
        self.settings = dict(iterations=iterations, depth=depth, learning_rate=learning_rate,
                             l2_leaf_reg=l2_leaf_reg, thread_count=threads,
                             task_type=task_type, random_seed=seed, verbose=False,
                             allow_writing_files=False)

    def _validate_x(self, x):
        import numpy as np
        x = np.asarray(x)
        indices = getattr(self, "feature_indices", None)
        if x.ndim != 2 or (indices is not None and max(indices) >= x.shape[1]):
            raise ValueError("CatBoost feature indices outside input")

    def _features(self, x):
        import numpy as np
        x = np.asarray(x)
        return x if getattr(self, "feature_indices", None) is None else x[:, self.feature_indices]

    def fit(self, x, y, cal_x, cal_y):
        from catboost import CatBoostRegressor
        alpha = ",".join(format(p, ".12g") for p in self.probabilities)
        self.model = CatBoostRegressor(loss_function=f"MultiQuantile:alpha={alpha}",
                                       **self.settings)
        self.model.fit(self._features(x), y)
        return self

    def curve(self, x):
        import numpy as np
        q = np.asarray(self.model.predict(self._features(x)), dtype=float)
        if q.ndim == 1:
            q = q[:, None]
        if q.shape != (len(x), len(self.probabilities)) or not np.isfinite(q).all():
            raise ValueError("CatBoost returned invalid multi-quantiles")
        q = np.maximum.accumulate(q, axis=1)
        values = np.column_stack([q[:, 0]-self.tail_width, q, q[:, -1]+self.tail_width])
        return GridCurve(values, [0., *self.probabilities, 1.])


class _ActiveGridCurve(GridCurve):
    """Grid curve retaining whether each row used its option-implied proxy."""

    def __init__(self, values, probabilities, active):
        import numpy as np
        super().__init__(values, probabilities)
        self.active = np.asarray(active, dtype="uint8").reshape(-1, 1)
        if len(self.active) != len(self.values):
            raise ValueError("active proxy mask does not match curve rows")

    def _arrays(self):
        return {**super()._arrays(), "active": self.active}


class OptionImpliedTransportCDF(CDFEstimator):
    """Calibrate row-wise risk-neutral proxy quantiles with empirical fallback.

    The proxy columns are standardized terminal-return quantiles prepared from
    the entry snapshot. A shared training-only PIT map transports active rows;
    inactive rows use the unchanged conditioned empirical distribution.
    """

    def __init__(self, proxy_indices, probabilities, eligible_index,
                 condition_indices, reference_index, knots=401, tail_width=4.,
                 transport_knots=21, transport_condition_indices=None,
                 transport_prior_strength=0., transport_local_bounds=None):
        import math
        indices = [*proxy_indices, eligible_index, *condition_indices, reference_index]
        transport_indices = ([] if transport_condition_indices is None
                             else list(transport_condition_indices))
        local_bounds = ([] if transport_local_bounds is None
                        else list(transport_local_bounds))
        if (len(proxy_indices) != len(probabilities) or len(probabilities) < 2
                or any(type(v) is not int or v < 0 for v in [*indices, *transport_indices])
                or len(set(proxy_indices)) != len(proxy_indices)
                or (transport_condition_indices is not None
                    and (not transport_indices
                         or len(set(transport_indices)) != len(transport_indices)))
                or probabilities[0] <= 0 or probabilities[-1] >= 1
                or any(a >= b for a, b in zip(probabilities, probabilities[1:]))
                or type(knots) is not int or knots < 5
                or type(transport_knots) is not int
                or (transport_knots != 0 and transport_knots < 3)
                or not math.isfinite(tail_width) or tail_width <= 0
                or isinstance(transport_prior_strength, bool)
                or not math.isfinite(transport_prior_strength)
                or transport_prior_strength < 0
                or (not transport_indices and transport_prior_strength != 0)
                or (transport_local_bounds is not None
                    and (not transport_indices or len(local_bounds) != 2
                         or any(isinstance(v, bool) or not math.isfinite(v)
                                for v in local_bounds)
                         or not 0 < local_bounds[0] < local_bounds[1] < 1))):
            raise ValueError("invalid option-implied transport parameters")
        self.proxy_indices = tuple(proxy_indices)
        self.probabilities = tuple(float(p) for p in probabilities)
        self.eligible_index = eligible_index
        self.tail_width, self.transport_knots = float(tail_width), transport_knots
        self.transport_condition_indices = tuple(transport_indices)
        self.transport_prior_strength = float(transport_prior_strength)
        self.transport_local_bounds = (None if not local_bounds
                                       else tuple(float(v) for v in local_bounds))
        self.output_knots = knots
        if not condition_indices:
            raise ValueError("option-implied fallback needs conditioning fields")
        self.fallback = HorizonEmpiricalCDF(condition_indices[0], reference_index, knots,
                                            condition_indices=condition_indices)

    def _validate_x(self, x):
        import numpy as np
        x = np.asarray(x)
        checked = (*self.proxy_indices, self.eligible_index,
                   *self.transport_condition_indices)
        if x.ndim != 2 or max(checked) >= x.shape[1]:
            raise ValueError("proxy indices outside input")
        active = x[:, self.eligible_index]
        if not np.isin(active, [0, 1]).all():
            raise ValueError("proxy eligibility must be binary")
        q = x[:, self.proxy_indices]
        if (not np.isfinite(q[active == 1]).all()
                or (np.diff(q[active == 1], axis=1) < 0).any()):
            raise ValueError("active proxy quantiles must be finite and ordered")
        if (self.transport_condition_indices
                and not np.isfinite(x[:, self.transport_condition_indices]).all()):
            raise ValueError("transport conditions must be finite")
        self.fallback._validate_x(x)

    def _proxy(self, x):
        import numpy as np
        q = np.asarray(x, dtype=float)[:, self.proxy_indices]
        q = np.maximum.accumulate(q, axis=1)
        values = np.column_stack([q[:, 0]-self.tail_width, q, q[:, -1]+self.tail_width])
        return GridCurve(values, [0., *self.probabilities, 1.])

    def fit(self, x, y, cal_x, cal_y):
        import numpy as np
        self._validate_x(x)
        self.fallback.fit(x, y, cal_x, cal_y)
        active = np.asarray(x)[:, self.eligible_index] == 1
        if not active.any():
            raise ValueError("no eligible option-implied training rows")
        pit = self._proxy(np.asarray(x)[active]).cdf(np.asarray(y)[active, None])[:, 0]
        if self.transport_knots:
            p = np.linspace(0, 1, self.transport_knots)
            knots = np.quantile(pit, p)
            self.transport_x = np.r_[0., np.clip(knots[1:-1], 1e-9, 1-1e-9), 1.]
            self.transport_p = p
        else:
            self.transport_x = self.transport_p = np.array([0., 1.])
        self.group_transports = {}
        if self.transport_condition_indices:
            conditions = np.asarray(x)[active][:, self.transport_condition_indices]
            for condition in np.unique(conditions, axis=0):
                mask = (conditions == condition).all(axis=1)
                local = np.quantile(pit[mask], self.transport_p)
                local = np.r_[0., np.clip(local[1:-1], 1e-9, 1-1e-9), 1.]
                weight = mask.sum()/(mask.sum()+self.transport_prior_strength)
                shrunk = weight*local + (1-weight)*self.transport_x
                self.group_transports[tuple(condition.tolist())] = np.maximum.accumulate(shrunk)
        return self

    def curve(self, x):
        import numpy as np
        self._validate_x(x)
        x = np.asarray(x)
        active = x[:, self.eligible_index] == 1
        p = np.linspace(0, 1, self.output_knots)
        fallback = self.fallback.curve(x).quantile(p)
        values = fallback.copy()
        if active.any():
            global_mapped = np.interp(p, self.transport_p, self.transport_x)
            mapped = np.tile(global_mapped, (active.sum(), 1))
            if self.transport_condition_indices:
                conditions = x[active][:, self.transport_condition_indices]
                for row, condition in enumerate(conditions):
                    transport = self.group_transports.get(tuple(condition.tolist()))
                    if transport is not None:
                        local = np.interp(p, self.transport_p, transport)
                        if self.transport_local_bounds is not None:
                            lo, hi = self.transport_local_bounds
                            middle = (p > lo) & (p < hi)
                            lower = np.interp(lo, self.transport_p, self.transport_x)
                            upper = np.interp(hi, self.transport_p, self.transport_x)
                            local[~middle] = global_mapped[~middle]
                            local[middle] = np.clip(local[middle], lower, upper)
                            local = np.maximum.accumulate(local)
                        mapped[row] = local
            mapped = np.clip(mapped, 0., 1.)
            proxy = self._proxy(x[active])
            lo, hi = self.probabilities[0], self.probabilities[-1]
            empirical = self.fallback.curve(x[active])
            left_anchor = proxy.quantile([lo])[:, 0]
            right_anchor = proxy.quantile([hi])[:, 0]
            empirical_at_left = empirical.cdf(left_anchor[:, None])[:, 0]
            empirical_at_right = empirical.cdf(right_anchor[:, None])[:, 0]
            raw_probability = mapped.copy()
            left = raw_probability < lo
            right = raw_probability > hi
            if left.any():
                raw_probability[left] *= np.repeat(
                    empirical_at_left[:, None]/lo, len(p), axis=1)[left]
            if right.any():
                raw_probability[right] = 1.-(1.-raw_probability[right])*np.repeat(
                    (1.-empirical_at_right)[:, None]/(1.-hi), len(p), axis=1)[right]
            raw_probability = np.clip(raw_probability, 0., 1.)
            spliced = empirical.quantile(raw_probability)
            middle = (mapped >= lo) & (mapped <= hi)
            proxy_values = proxy.quantile(mapped)
            spliced[middle] = proxy_values[middle]
            values[active] = np.maximum.accumulate(spliced, axis=1)
        return _ActiveGridCurve(values, p, active)


def _label_free_endpoint(path, params):
    if not isinstance(path, str) or path.count(":") != 1 or not isinstance(params, dict):
        raise ValueError("invalid endpoint declaration")
    module, name = path.split(":", 1)
    estimator = getattr(importlib.import_module(module), name)
    if getattr(estimator, "consumes_calibration_labels", False):
        raise ValueError("nested endpoint cannot consume calibration labels")
    return estimator(**params)


def _weighted_quantiles(values, probabilities, weights):
    import numpy as np
    values, probabilities, weights = (np.asarray(value, dtype=float)
                                      for value in (values, probabilities, weights))
    order = np.argsort(values, kind="stable")
    values, weights = values[order], weights[order]
    total = weights.sum()
    if not len(values) or not np.isfinite(values).all() or not np.isfinite(weights).all() \
            or (weights < 0).any() or total <= 0:
        raise ValueError("invalid weighted quantile inputs")
    cumulative = (np.cumsum(weights)-.5*weights)/total
    return np.interp(probabilities, cumulative, values, left=values[0], right=values[-1])


class DynamicPITRecalibratedCDF(CDFEstimator):
    """Causal rolling PIT recalibration with delayed-label admission."""

    consumes_calibration_labels = True

    def __init__(self, endpoint_class, endpoint_params, knots=401, map_knots=21,
                 half_life_days=90., lookback_days=1095, prior_strength=50.,
                 minimum_rows=50):
        import math
        if (type(knots) is not int or knots < 5 or type(map_knots) is not int
                or map_knots < 3 or isinstance(half_life_days, bool)
                or not math.isfinite(half_life_days) or half_life_days <= 0
                or type(lookback_days) is not int or lookback_days < 1
                or isinstance(prior_strength, bool) or not math.isfinite(prior_strength)
                or prior_strength < 0 or type(minimum_rows) is not int or minimum_rows < 2):
            raise ValueError("invalid dynamic PIT parameters")
        self.endpoint = _label_free_endpoint(endpoint_class, endpoint_params)
        self.knots, self.map_knots = knots, map_knots
        self.half_life_days, self.lookback_days = float(half_life_days), lookback_days
        self.prior_strength, self.minimum_rows = float(prior_strength), minimum_rows

    def _validate_x(self, x):
        self.endpoint._validate_x(x)

    def _map(self, pit, weights=None):
        import numpy as np
        p = np.linspace(0., 1., self.map_knots)
        x = (np.quantile(pit, p) if weights is None
             else _weighted_quantiles(pit, p, weights))
        return np.r_[0., np.clip(x[1:-1], 1e-9, 1-1e-9), 1.]

    def fit(self, x, y, cal_x, cal_y):
        import numpy as np
        self._validate_x(x); self._validate_x(cal_x)
        self.endpoint.fit(x, y, cal_x, cal_y)
        self.cal_pit = self.endpoint.curve(cal_x).cdf(np.asarray(cal_y)[:, None])[:, 0]
        if len(self.cal_pit) < 2 or not np.isfinite(self.cal_pit).all():
            raise ValueError("insufficient finite calibration PIT rows")
        self.map_p = np.linspace(0., 1., self.map_knots)
        self.global_map_x = self._map(self.cal_pit)
        self.cal_context = None
        self.admitted_rows_by_date = []
        return self

    def fit_context(self, calibration, date_field, end_field):
        import pandas as pd
        if len(calibration) != len(self.cal_pit):
            raise ValueError("calibration context length mismatch")
        _validate_temporal_frame(calibration, date_field, end_field)
        self.cal_context = pd.DataFrame({
            "date": pd.to_datetime(calibration[date_field]).to_numpy(),
            "end": pd.to_datetime(calibration[end_field]).to_numpy(),
            "pit": self.cal_pit})
        return self

    def _curve_from_maps(self, base, maps):
        import numpy as np
        p = np.linspace(0., 1., self.knots)
        mapped = np.array([np.interp(p, self.map_p, row) for row in maps])
        values = base.quantile(np.clip(mapped, 1e-9, 1-1e-9))
        return GridCurve(np.maximum.accumulate(values, axis=1), p)

    def curve(self, x):
        import numpy as np
        self._validate_x(x)
        maps = np.tile(self.global_map_x, (len(x), 1))
        return self._curve_from_maps(self.endpoint.curve(x), maps)

    def curve_context(self, x, dates, ends, outcomes):
        import numpy as np
        import pandas as pd
        if self.cal_context is None:
            raise ValueError("dynamic PIT temporal context was not fitted")
        self._validate_x(x)
        dates = pd.to_datetime(np.asarray(dates)).to_numpy()
        ends = pd.to_datetime(np.asarray(ends)).to_numpy()
        outcomes = np.asarray(outcomes, dtype=float)
        if (dates.shape != (len(x),) or ends.shape != (len(x),)
                or outcomes.shape != (len(x),) or not np.isfinite(outcomes).all()
                or np.any(ends <= dates)):
            raise ValueError("invalid forecast temporal context")
        base = self.endpoint.curve(x)
        validation_pit = base.cdf(outcomes[:, None])[:, 0]
        maps = np.tile(self.global_map_x, (len(x), 1))
        admitted = []
        for date in np.unique(dates):
            matured_cal = self.cal_context.end.to_numpy() < date
            prior = (self._map(self.cal_context.pit.to_numpy()[matured_cal])
                     if matured_cal.sum() >= 2 else self.map_p)
            cal_age = (date-self.cal_context.date.to_numpy()).astype("timedelta64[D]").astype(int)
            cal_mask = ((self.cal_context.end.to_numpy() < date) & (cal_age >= 0)
                        & (cal_age <= self.lookback_days))
            val_age = (date-dates).astype("timedelta64[D]").astype(int)
            val_mask = ((ends < date) & (val_age >= 0) & (val_age <= self.lookback_days))
            pit = np.r_[self.cal_context.pit.to_numpy()[cal_mask], validation_pit[val_mask]]
            age = np.r_[cal_age[cal_mask], val_age[val_mask]]
            admitted.append(int(len(pit)))
            local = prior
            if len(pit) >= self.minimum_rows:
                weights = np.exp2(-age/self.half_life_days)
                proposal = self._map(pit, weights)
                effective = weights.sum()**2/np.square(weights).sum()
                shrink = effective/(effective+self.prior_strength)
                local = np.maximum.accumulate(
                    shrink*proposal+(1.-shrink)*prior)
            maps[dates == date] = local
        self.admitted_rows_by_date = admitted
        return self._curve_from_maps(base, maps)

    def _research_state(self):
        return {"half_life_days": self.half_life_days,
                "lookback_days": self.lookback_days,
                "prior_strength": self.prior_strength,
                "minimum_rows": self.minimum_rows,
                "admitted_rows_by_date": self.admitted_rows_by_date}


class BetaTransformedPoolCDF(CDFEstimator):
    """Finite-grid beta-transformed CDF pool selected on calibration labels."""

    consumes_calibration_labels = True

    def __init__(self, left_class, left_params, right_class, right_params,
                 index_indices, cell_indices, knots, weights, alphas, betas,
                 tail_probability=.1, crps_weight=1., tail_weight=1.):
        import math
        grids = (weights, alphas, betas)
        if (not index_indices or not cell_indices or not set(index_indices).issubset(cell_indices)
                or any(type(v) is not int or v < 0 for v in [*index_indices, *cell_indices])
                or type(knots) is not int or knots < 11
                or any(not isinstance(g, (list, tuple)) or not g for g in grids)
                or any(isinstance(v, bool) or not math.isfinite(v)
                       for g in grids for v in g)
                or any(not 0 <= v <= 1 for v in weights)
                or any(v <= 0 for g in (alphas, betas) for v in g)
                or not 0 < tail_probability < .5
                or any(isinstance(v, bool) or not math.isfinite(v) or v < 0
                       for v in (crps_weight, tail_weight))
                or crps_weight+tail_weight <= 0):
            raise ValueError("invalid beta pool parameters")
        self.left = _label_free_endpoint(left_class, left_params)
        self.right = _label_free_endpoint(right_class, right_params)
        self.index_indices, self.cell_indices = tuple(index_indices), tuple(cell_indices)
        self.knots = knots
        self.weights, self.alphas, self.betas = tuple(weights), tuple(alphas), tuple(betas)
        self.tail_probability = float(tail_probability)
        self.crps_weight, self.tail_weight = float(crps_weight), float(tail_weight)

    def _validate_x(self, x):
        import numpy as np
        x = np.asarray(x)
        if x.ndim != 2 or max((*self.index_indices, *self.cell_indices)) >= x.shape[1]:
            raise ValueError("index or cell indices outside input")
        groups = x[:, self.index_indices]
        if not np.isfinite(x[:, self.cell_indices]).all() or not np.isin(groups, [0, 1]).all() \
                or not (groups.sum(1) == 1).all():
            raise ValueError("invalid index/cell metadata")
        self.left._validate_x(x); self.right._validate_x(x)

    @staticmethod
    def _row_scores(curve, outcomes, probabilities, tail_probability):
        import numpy as np
        q = curve.quantile(probabilities)
        residual = outcomes[:, None]-q
        pinball = (probabilities[None, :]-(residual < 0))*residual
        crps = 2*np.mean(pinball, axis=1)
        lower = 2*np.mean(pinball[:, probabilities <= tail_probability], axis=1)
        upper = 2*np.mean(pinball[:, probabilities >= 1-tail_probability], axis=1)
        return crps, lower, upper

    def _calibration_pool(self, left, right, weight):
        """Build one deterministic dense CDF grid; avoid repeated inversions."""
        import numpy as np
        p = (np.arange(self.knots)+.5)/self.knots
        values = np.sort(np.concatenate([left.quantile(p), right.quantile(p)], axis=1), axis=1)
        span = np.maximum(values[:, -1]-values[:, 0], 1.)
        values = np.column_stack([values[:, 0]-span, values, values[:, -1]+span])
        probabilities = ConvexCurve(left, right, weight).cdf(values)
        probabilities[:, 0], probabilities[:, -1] = 0., 1.
        return GridCurve(values, np.maximum.accumulate(probabilities, axis=1))

    def fit(self, x, y, cal_x, cal_y):
        import itertools
        import numpy as np
        self._validate_x(x); self._validate_x(cal_x)
        self.left.fit(x, y, cal_x, cal_y); self.right.fit(x, y, cal_x, cal_y)
        cells = np.asarray(cal_x)[:, self.cell_indices]
        _, cell = np.unique(cells, axis=0, return_inverse=True)
        counts = np.bincount(cell)
        row_weight = 1./(len(counts)*counts[cell])
        p = (np.arange(self.knots)+.5)/self.knots
        choices = []
        left, right = self.left.curve(cal_x), self.right.curve(cal_x)
        for weight in self.weights:
            pooled = self._calibration_pool(left, right, weight)
            for alpha, beta in itertools.product(self.alphas, self.betas):
                curve = BetaTransformedCurve(pooled, alpha, beta)
                crps, lower, upper = self._row_scores(curve, np.asarray(cal_y), p,
                                                       self.tail_probability)
                score = np.dot(row_weight, self.crps_weight*crps
                                + self.tail_weight*(lower+upper)/2)
                choices.append((float(score), float(weight), float(alpha), float(beta)))
        self.selected = min(choices)
        return self

    def curve(self, x):
        self._validate_x(x)
        _, weight, alpha, beta = self.selected
        return BetaTransformedCurve(ConvexCurve(self.left.curve(x), self.right.curve(x), weight),
                                    alpha, beta)

    def _research_state(self):
        score, weight, alpha, beta = self.selected
        return {"objective": score, "weight": weight, "alpha": alpha, "beta": beta,
                "grid_candidates": len(self.weights)*len(self.alphas)*len(self.betas)}


class SemiparametricGPDTailCDF(CDFEstimator):
    """Preserve an endpoint center and attach partially pooled GPD tails."""

    def __init__(self, endpoint_class, endpoint_params, index_indices, knots=401,
                 splice_probabilities=(.1, .9), minimum_exceedances=30,
                 prior_strength=50., shape_bounds=(-.4, .8), probability_floor=1e-5,
                 anchor_batch_size=8192):
        import math
        if (not index_indices or any(type(v) is not int or v < 0 for v in index_indices)
                or type(knots) is not int or knots < 11
                or len(splice_probabilities) != 2
                or not 0 < splice_probabilities[0] < .5 < splice_probabilities[1] < 1
                or type(minimum_exceedances) is not int or minimum_exceedances < 3
                or isinstance(prior_strength, bool) or not math.isfinite(prior_strength)
                or prior_strength < 0 or len(shape_bounds) != 2
                or not -1 < shape_bounds[0] < shape_bounds[1] < 1
                or not 0 < probability_floor < min(splice_probabilities[0],
                                                   1-splice_probabilities[1])
                or type(anchor_batch_size) is not int or anchor_batch_size < 1):
            raise ValueError("invalid semiparametric GPD parameters")
        if not isinstance(endpoint_class, str) or endpoint_class.count(":") != 1 \
                or not isinstance(endpoint_params, dict):
            raise ValueError("invalid GPD endpoint declaration")
        module, name = endpoint_class.split(":", 1)
        self.endpoint = getattr(importlib.import_module(module), name)(**endpoint_params)
        self.index_indices, self.knots = tuple(index_indices), knots
        self.splice_probabilities = tuple(float(v) for v in splice_probabilities)
        self.minimum_exceedances = minimum_exceedances
        self.prior_strength = float(prior_strength)
        self.shape_bounds = tuple(float(v) for v in shape_bounds)
        self.probability_floor = float(probability_floor)
        self.anchor_batch_size = anchor_batch_size

    def _validate_x(self, x):
        import numpy as np
        x = np.asarray(x)
        if x.ndim != 2 or max(self.index_indices) >= x.shape[1]:
            raise ValueError("index indices outside input")
        groups = x[:, self.index_indices]
        if not np.isin(groups, [0, 1]).all() or not (groups.sum(1) == 1).all():
            raise ValueError("index identities must be one-hot")
        self.endpoint._validate_x(x)

    def _fit_tail(self, excess):
        import numpy as np
        from scipy.stats import genpareto
        excess = np.asarray(excess, dtype=float)
        if len(excess) < self.minimum_exceedances or not np.isfinite(excess).all() \
                or (excess <= 0).any():
            return None
        shape, _, scale = genpareto.fit(excess, floc=0.)
        shape = float(np.clip(shape, *self.shape_bounds))
        scale = float(max(scale, np.finfo(float).eps))
        return shape, scale

    def _anchor_quantiles(self, x):
        """Read only splice anchors through bounded endpoint predictions."""
        import numpy as np
        probabilities = list(self.splice_probabilities)
        return np.vstack([
            self.endpoint.curve(x[start:start+self.anchor_batch_size]).quantile(probabilities)
            for start in range(0, len(x), self.anchor_batch_size)
        ])

    def fit(self, x, y, cal_x, cal_y):
        import numpy as np
        self._validate_x(x)
        self.endpoint.fit(x, y, cal_x, cal_y)
        y = np.asarray(y, dtype=float)
        lo, hi = self.splice_probabilities
        anchors = self._anchor_quantiles(x)
        left_all, right_all = anchors[:, 0]-y, y-anchors[:, 1]
        self.global_tails = (self._fit_tail(left_all[left_all > 0]),
                             self._fit_tail(right_all[right_all > 0]))
        if any(value is None for value in self.global_tails):
            raise ValueError("insufficient global tail exceedances")
        groups = np.asarray(x)[:, self.index_indices].argmax(1)
        self.group_tails, self.group_diagnostics = [], []
        for group in range(len(self.index_indices)):
            mask = groups == group
            local = [self._fit_tail(tail[mask & (tail > 0)])
                     for tail in (left_all, right_all)]
            fitted, fallback = [], False
            for side, global_fit in zip(local, self.global_tails):
                if side is None:
                    fitted.append(global_fit); fallback = True
                else:
                    count = int((mask & ((left_all if len(fitted) == 0 else right_all) > 0)).sum())
                    weight = count/(count+self.prior_strength)
                    fitted.append(tuple(weight*a+(1-weight)*b
                                        for a, b in zip(side, global_fit)))
            self.group_tails.append(tuple(fitted))
            self.group_diagnostics.append({
                "index_position": group, "fallback": "global" if fallback else "pooled",
                "left_shape": fitted[0][0], "left_scale": fitted[0][1],
                "right_shape": fitted[1][0], "right_scale": fitted[1][1]})
        return self

    def curve(self, x):
        import numpy as np
        from scipy.stats import genpareto
        self._validate_x(x)
        p = np.linspace(0., 1., self.knots)
        safe = np.clip(p, self.probability_floor, 1-self.probability_floor)
        base = self.endpoint.curve(x)
        values = base.quantile(safe)
        lo, hi = self.splice_probabilities
        anchors = base.quantile([lo, hi])
        groups = np.asarray(x)[:, self.index_indices].argmax(1)
        for row, group in enumerate(groups):
            left, right = self.group_tails[group]
            mask = p < lo
            conditional = np.clip(1-safe[mask]/lo, 0, 1-self.probability_floor)
            values[row, mask] = anchors[row, 0]-genpareto.ppf(
                conditional, left[0], loc=0, scale=left[1])
            mask = p > hi
            conditional = np.clip((safe[mask]-hi)/(1-hi), 0, 1-self.probability_floor)
            values[row, mask] = anchors[row, 1]+genpareto.ppf(
                conditional, right[0], loc=0, scale=right[1])
        return GridCurve(np.maximum.accumulate(values, axis=1), p)

    def _research_state(self):
        return {"splice_probabilities": list(self.splice_probabilities),
                "anchor_batch_size": self.anchor_batch_size,
                "global": {"left": list(self.global_tails[0]),
                           "right": list(self.global_tails[1])},
                "groups": self.group_diagnostics}


class TailConstrainedQuantileBlendCDF(CDFEstimator):
    """Calibration-only, per-index quantile blend with incumbent tail guards."""

    consumes_calibration_labels = True
    anchors = (0.05, 0.50, 0.95)

    def __init__(self, incumbent_class, incumbent_params, option_class, option_params,
                 index_indices, cell_indices, knots, left_weights, center_weights,
                 right_weights, tolerance=1e-12):
        import math

        classes = (incumbent_class, option_class)
        mappings = (incumbent_params, option_params)
        grids = (left_weights, center_weights, right_weights)
        if (any(not isinstance(value, str) or value.count(":") != 1 for value in classes)
                or any(not isinstance(value, dict) for value in mappings)
                or not isinstance(index_indices, (list, tuple)) or not index_indices
                or not isinstance(cell_indices, (list, tuple)) or not cell_indices
                or any(type(v) is not int or v < 0 for v in [*index_indices, *cell_indices])
                or len(set(index_indices)) != len(index_indices)
                or len(set(cell_indices)) != len(cell_indices)
                or not set(index_indices).issubset(cell_indices)
                or type(knots) is not int or knots < 3
                or isinstance(tolerance, bool) or not math.isfinite(tolerance)
                or tolerance < 0):
            raise ValueError("invalid tail-constrained blend parameters")
        normalized = []
        for grid in grids:
            if (not isinstance(grid, (list, tuple)) or not grid
                    or any(isinstance(v, bool) or not math.isfinite(v)
                           or not 0 <= v <= 1 for v in grid)):
                raise ValueError("blend weight grids must be finite and bounded")
            values = tuple(float(v) for v in grid)
            if len(set(values)) != len(values) or tuple(sorted(values)) != values or 0. not in values:
                raise ValueError("blend weight grids must be sorted, unique and include zero")
            normalized.append(values)

        metadata = set(cell_indices) - set(index_indices)
        if not metadata:
            raise ValueError("actual-DTE cell metadata is required")

        def declared_indices(value):
            found = set()
            if isinstance(value, dict):
                for name, child in value.items():
                    if name.endswith("_index") and type(child) is int:
                        found.add(child)
                    elif name.endswith("_indices") and isinstance(child, (list, tuple)):
                        found.update(v for v in child if type(v) is int)
                    found.update(declared_indices(child))
            elif isinstance(value, (list, tuple)):
                for child in value:
                    found.update(declared_indices(child))
            return found

        if any(metadata & declared_indices(params) for params in mappings):
            raise ValueError("cell metadata must be excluded from endpoint predictors")
        self.incumbent_class, self.option_class = classes
        self.incumbent_params, self.option_params = (dict(v) for v in mappings)
        self.index_indices, self.cell_indices = tuple(index_indices), tuple(cell_indices)
        self.knots, self.weight_grids, self.tolerance = knots, tuple(normalized), float(tolerance)
        self.incumbent = self._endpoint(incumbent_class, self.incumbent_params)
        self.option = self._endpoint(option_class, self.option_params)

    @staticmethod
    def _endpoint(path, params):
        module, name = path.split(":", 1)
        estimator = getattr(importlib.import_module(module), name)
        if getattr(estimator, "consumes_calibration_labels", False):
            raise ValueError("nested endpoint cannot consume calibration labels")
        return estimator(**params)

    def _validate_x(self, x):
        import numpy as np
        x = np.asarray(x)
        if x.ndim != 2 or max((*self.index_indices, *self.cell_indices)) >= x.shape[1]:
            raise ValueError("index or cell indices outside input")
        cells = x[:, self.cell_indices]
        groups = x[:, self.index_indices]
        if not np.isfinite(cells).all():
            raise ValueError("reporting cell metadata must be finite")
        if (not np.isin(groups, [0, 1]).all() or not (groups.sum(1) == 1).all()):
            raise ValueError("index identities must be finite binary one-hot")
        self.incumbent._validate_x(x)
        self.option._validate_x(x)

    @staticmethod
    def _blend(incumbent, option, probabilities, weights):
        import numpy as np
        if all(weight == 0 for weight in weights):
            return np.asarray(incumbent).copy()
        if all(weight == 1 for weight in weights):
            return np.asarray(option).copy()
        curve_weight = np.interp(probabilities, TailConstrainedQuantileBlendCDF.anchors,
                                 weights, left=weights[0], right=weights[-1])
        return np.maximum.accumulate(
            incumbent + curve_weight[None, :] * (option - incumbent), axis=1)

    @staticmethod
    def _endpoint_quantiles(curve, probabilities):
        """Discretize strict-interior quantiles and extrapolate finite endpoints."""
        import numpy as np
        middle = curve.quantile(probabilities[1:-1])
        left = middle[:, :1] - (middle[:, 1:2] - middle[:, :1])
        right = middle[:, -1:] + (middle[:, -1:] - middle[:, -2:-1])
        return np.maximum.accumulate(np.column_stack([left, middle, right]), axis=1)

    @staticmethod
    def _row_scores(values, probabilities, outcomes):
        import numpy as np
        residual = outcomes[:, None] - values
        pinball = (probabilities[None, :] - (residual < 0)) * residual
        return 2 * np.trapezoid(pinball, probabilities, axis=1)

    @staticmethod
    def _tail_deviation(values, probabilities, outcomes):
        import numpy as np
        pit = GridCurve(values, probabilities).cdf(outcomes[:, None])[:, 0]
        return (abs(float(np.mean(pit < .05)) - .05),
                abs(float(np.mean(pit > .95)) - .05))

    def fit(self, x, y, cal_x, cal_y):
        import itertools
        import numpy as np

        self._validate_x(x); self._validate_x(cal_x)
        y, cal_y = np.asarray(y, dtype=float), np.asarray(cal_y, dtype=float)
        if (y.shape != (len(x),) or cal_y.shape != (len(cal_x),)
                or not len(cal_y) or not np.isfinite(y).all() or not np.isfinite(cal_y).all()):
            raise ValueError("invalid training or calibration labels")
        self.incumbent.fit(x, y, cal_x, cal_y)
        self.option.fit(x, y, cal_x, cal_y)
        p = np.linspace(0., 1., self.knots)
        incumbent = self._endpoint_quantiles(self.incumbent.curve(cal_x), p)
        option = self._endpoint_quantiles(self.option.curve(cal_x), p)
        groups = np.asarray(cal_x)[:, self.index_indices].argmax(1)
        cells = np.asarray(cal_x)[:, self.cell_indices]
        self.weights_by_index = np.zeros((len(self.index_indices), 3))
        self.calibration_diagnostics = []
        self.calibration_row_weights_by_index = []
        for group in range(len(self.index_indices)):
            mask = groups == group
            if not mask.any():
                raise ValueError("calibration fold missing an index")
            _, cell = np.unique(cells[mask], axis=0, return_inverse=True)
            counts = np.bincount(cell)
            row_weight = 1. / (len(counts) * counts[cell])
            self.calibration_row_weights_by_index.append(row_weight.copy())
            guard = self._tail_deviation(incumbent[mask], p, cal_y[mask])
            feasible = []
            for weights in itertools.product(*self.weight_grids):
                values = self._blend(incumbent[mask], option[mask], p, weights)
                tails = self._tail_deviation(values, p, cal_y[mask])
                if all(tails[i] <= guard[i] + self.tolerance for i in range(2)):
                    score = float(np.dot(row_weight,
                                         self._row_scores(values, p, cal_y[mask])))
                    feasible.append((score, sum(weights), tuple(weights), tails))
            if not feasible:
                raise ValueError("tail-constrained blend has no feasible candidate")
            score, _, weights, tails = min(feasible)
            self.weights_by_index[group] = weights
            self.calibration_diagnostics.append({
                "index_position": group, "weights": list(weights), "objective": score,
                "incumbent_tail_deviation": list(guard), "selected_tail_deviation": list(tails),
                "calibration_n": int(mask.sum()), "calibration_cells": int(len(counts)),
                "feasible_candidates": int(len(feasible)),
            })
        return self

    def curve(self, x):
        import numpy as np
        self._validate_x(x)
        p = np.linspace(0., 1., self.knots)
        incumbent = self._endpoint_quantiles(self.incumbent.curve(x), p)
        option = self._endpoint_quantiles(self.option.curve(x), p)
        groups = np.asarray(x)[:, self.index_indices].argmax(1)
        values = np.empty_like(incumbent)
        for group, weights in enumerate(self.weights_by_index):
            mask = groups == group
            values[mask] = self._blend(incumbent[mask], option[mask], p, weights)
        return GridCurve(values, p)

    def _research_state(self):
        """Return JSON-safe fitted calibration state for fold-level evidence."""
        return {"anchors": list(self.anchors),
                "weights_by_index": self.weights_by_index.tolist(),
                "calibration_diagnostics": self.calibration_diagnostics}


class PCAAugmentedCDF(CDFEstimator):
    """Append train-only principal components before fitting a declared estimator."""

    def __init__(self, estimator_class, estimator_params, pca_indices, components):
        if (not isinstance(estimator_class, str) or ":" not in estimator_class
                or not isinstance(estimator_params, dict)
                or not isinstance(pca_indices, (list, tuple)) or not pca_indices
                or any(type(value) is not int or value < 0 for value in pca_indices)
                or len(set(pca_indices)) != len(pca_indices)
                or type(components) is not int or not 0 < components <= len(pca_indices)
                or estimator_class.endswith(":PCAAugmentedCDF")):
            raise ValueError("invalid PCA augmentation parameters")
        self.estimator_class = estimator_class
        self.estimator_params = dict(estimator_params)
        self.pca_indices = tuple(pca_indices)
        self.components = components

    def _validate_x(self, x):
        import numpy as np
        x = np.asarray(x)
        if (x.ndim != 2 or max(self.pca_indices) >= x.shape[1]
                or np.isinf(x[:, self.pca_indices]).any()):
            raise ValueError("PCA feature indices outside finite input")

    def _augment(self, x):
        import numpy as np
        x = np.asarray(x, dtype=float)
        scores = self.pca.transform(self.scaler.transform(x[:, self.pca_indices]))
        return np.column_stack([x, scores])

    def fit(self, x, y, cal_x, cal_y):
        from sklearn.decomposition import PCA
        from sklearn.preprocessing import StandardScaler
        self._validate_x(x); self._validate_x(cal_x)
        self.scaler = StandardScaler().fit(x[:, self.pca_indices])
        self.pca = PCA(n_components=self.components, svd_solver="full").fit(
            self.scaler.transform(x[:, self.pca_indices]))
        module, name = self.estimator_class.split(":", 1)
        self.estimator = getattr(importlib.import_module(module), name)(**self.estimator_params)
        self.estimator.fit(self._augment(x), y, self._augment(cal_x), cal_y)
        return self

    def curve(self, x):
        self._validate_x(x)
        return self.estimator.curve(self._augment(x))

    def _equivalence_state(self):
        state = self.estimator._equivalence_state()
        return {**state, "pca_components": self.pca.components_.tolist(),
                "pca_mean": self.scaler.mean_.tolist(),
                "pca_scale": self.scaler.scale_.tolist()}


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
                 max_samples_leaf=None, threads=2, seed=829, feature_indices=None):
        import math
        values = (knots, trees, min_child, threads, seed)
        if (any(type(value) is not int for value in values) or knots < 3
                or min(trees, min_child, threads) < 1 or seed < 0
                or isinstance(max_features, bool) or not math.isfinite(max_features)
                or not 0 < max_features <= 1
                or (max_samples_leaf is not None
                    and (type(max_samples_leaf) is not int or max_samples_leaf < 1))
                or (feature_indices is not None and
                    (not feature_indices or len(set(feature_indices)) != len(feature_indices)
                     or any(type(v) is not int or v < 0 for v in feature_indices)))):
            raise ValueError("invalid quantile-forest parameters")
        self.knots, self.trees, self.min_child = knots, trees, min_child
        self.max_features, self.threads, self.seed = max_features, threads, seed
        self.max_samples_leaf = max_samples_leaf
        self.feature_indices = None if feature_indices is None else tuple(feature_indices)

    def _validate_x(self, x):
        import numpy as np
        x = np.asarray(x)
        if (x.ndim != 2 or (self.feature_indices is not None
                            and max(self.feature_indices) >= x.shape[1])):
            raise ValueError("quantile-forest feature indices outside input")

    def _features(self, x):
        import numpy as np
        x = np.asarray(x)
        return x if self.feature_indices is None else x[:, self.feature_indices]

    def fit(self, x, y, cal_x, cal_y):
        """Fit one quantile-regression forest on training rows only."""
        from quantile_forest import RandomForestQuantileRegressor
        self.model = RandomForestQuantileRegressor(
            n_estimators=self.trees, min_samples_leaf=self.min_child,
            max_samples_leaf=self.max_samples_leaf,
            max_features=self.max_features, n_jobs=self.threads,
            random_state=self.seed)
        self.model.fit(self._features(x), y)
        return self

    def curve(self, x):
        """Return the declared finite-support forest discretization."""
        import numpy as np
        p = np.linspace(0, 1, self.knots)
        values = np.asarray(self.model.predict(self._features(x), quantiles=p.tolist()), dtype=float)
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


class _PatienceStop:
    """Early-stopping rule over a monitored loss, with best-weight restore.

    One instance per fitted network. ``exhausted`` records an epoch's loss and
    says whether ``patience`` epochs have passed without a STRICT improvement;
    ``restore`` puts the best epoch's weights back.
    """

    def __init__(self, patience):
        self.patience, self.history, self.best, self.best_epoch = patience, [], None, 0

    def exhausted(self, loss, model):
        import math
        if not math.isfinite(loss):
            raise ValueError("nonfinite monitored loss")
        self.history.append(loss)
        if self.best is None or loss < self.best:
            self.best, self.best_epoch = loss, len(self.history)
            self.state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        return len(self.history)-self.best_epoch >= self.patience

    def restore(self, model):
        model.load_state_dict(self.state)


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
    patience : int or None
        Absent trains exactly ``epochs``. An integer >= 1 watches the training
        objective on the calibration rows after every epoch, restores the best
        epoch's weights and stops once ``patience`` epochs bring no strict
        improvement. That is the only exit: ``epochs`` becomes a ceiling, and
        reaching it first raises. The calibration rows then steer training, so
        a PIT map fitted on them is refused by the study.

    Examples
    --------
    Three-component CPU research candidate::

        model = MixtureMLPCDF(components=3, device="cpu")
    """

    def __init__(self, components=3, hidden=32, epochs=20, batch_size=1024,
                 seeds=(11, 29), device="cuda", learning_rate=.001,
                 weight_decay=.01, min_scale=.1, activation="tanh", dropout=0.,
                 head_features=None, deterministic=False, left_cdf_weight=0.,
                 left_cdf_bounds=(-6., 0.), left_cdf_points=61, feature_indices=None,
                 patience=None):
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
        if (isinstance(left_cdf_weight, bool) or not math.isfinite(left_cdf_weight)
                or left_cdf_weight < 0 or not isinstance(left_cdf_bounds, (list, tuple))
                or len(left_cdf_bounds) != 2
                or any(isinstance(v, bool) or not math.isfinite(v) for v in left_cdf_bounds)
                or left_cdf_bounds[0] >= left_cdf_bounds[1] or left_cdf_bounds[1] > 0
                or type(left_cdf_points) is not int or left_cdf_points < 2):
            raise ValueError("invalid left CDF training parameters")
        if (feature_indices is not None and
                (not isinstance(feature_indices, (list, tuple)) or not feature_indices
                 or any(type(v) is not int or v < 0 for v in feature_indices)
                 or len(set(feature_indices)) != len(feature_indices))):
            raise ValueError("invalid feature indices")
        if patience is not None and (type(patience) is not int or patience < 1):
            raise ValueError("invalid patience: an integer >= 1 or absent")
        self.patience = patience
        self.components, self.hidden, self.epochs = components, tuple(widths), epochs
        self.batch_size, self.seeds, self.device = batch_size, seeds, device
        self.learning_rate, self.weight_decay, self.min_scale = learning_rate, weight_decay, min_scale
        self.activation, self.dropout = activation, dropout
        self.head_features = tuple(head_features or ())
        self.deterministic = deterministic
        self.left_cdf_weight = float(left_cdf_weight)
        self.left_cdf_bounds = tuple(float(v) for v in left_cdf_bounds)
        self.left_cdf_points = left_cdf_points
        self.feature_indices = None if feature_indices is None else tuple(feature_indices)

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

    def _features(self, x):
        import numpy as np
        x = np.asarray(x)
        if x.ndim != 2 or (self.feature_indices is not None
                            and max(self.feature_indices) >= x.shape[1]):
            raise ValueError("feature indices outside input")
        return x if self.feature_indices is None else x[:, self.feature_indices]

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
        return _GaussianFamily({"kind": _GaussianFamily.KIND}).log_density(y, mu, sigma)

    def _left_cdf_score(self, y, logw, mu, sigma):
        """Finite-grid CDF Brier score for a standardized left-tail interval."""
        import math
        import torch
        grid = torch.linspace(*self.left_cdf_bounds, self.left_cdf_points,
                              dtype=mu.dtype, device=mu.device)
        z = (grid[None, None, :] - mu[:, :, None]) / (sigma[:, :, None] * math.sqrt(2.))
        cdf = (logw.exp()[:, :, None] * (.5 * (1. + torch.erf(z)))).sum(1)
        observed = (y <= grid[None, :]).to(cdf.dtype)
        return (cdf - observed).square().mean()

    def _training_context(self, x):
        """Return optional immutable tensors used by subclass penalties."""
        return None

    def _monitor_context(self, cal_x):
        """Return the context the stop rule's loss reads on the calibration rows."""
        if type(self)._training_context is not MixtureMLPCDF._training_context:
            raise ValueError(f"{type(self).__name__} has no calibration-row context: "
                             "patience is unsupported")
        return None

    def _monitor_set(self, cal_x, cal_y):
        """Return the calibration rows as tensors, scaled by the TRAINING scaler."""
        import numpy as np
        import torch
        heads = self._heads(cal_x)
        if not set(heads).issubset(self.seen_heads):
            raise ValueError("unseen head in the calibration rows")
        return (torch.tensor(self.scaler.transform(self._features(cal_x)),
                             dtype=torch.float32, device=self.device),
                self._target_tensor(np.asarray(cal_y)),
                torch.tensor(heads, dtype=torch.long, device=self.device),
                self._monitor_context(cal_x))

    def _monitor_loss(self, model, monitor):
        """Return the training objective averaged over the calibration rows."""
        import torch
        xx, yy, hh, context = monitor
        model.eval()
        total = 0.
        with torch.no_grad():
            for start in range(0, len(xx), self.batch_size):
                ix = torch.arange(start, min(start+self.batch_size, len(xx)), device=self.device)
                logw, mu, sigma = self._forward(model, xx[ix], hh[ix])
                total += self._objective_loss(
                    yy[ix], logw, mu, sigma, ix, context).item()*len(ix)
        model.train()
        return total/len(xx)

    def _extra_training_loss(self, y, logw, mu, sigma, indices, context):
        """Return an optional proper-score addend for one mini-batch."""
        return None

    def _objective_loss(self, y, logw, mu, sigma, indices, context):
        import torch
        loss = -torch.logsumexp(logw+self._logp(y, mu, sigma), dim=1).mean()
        if self.left_cdf_weight:
            loss = loss + self.left_cdf_weight*self._left_cdf_score(y, logw, mu, sigma)
        extra = self._extra_training_loss(y, logw, mu, sigma, indices, context)
        return loss if extra is None else loss + extra

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
            Training and separate calibration arrays. The calibration rows are
            read only when ``patience`` is set, as the stop rule's monitor.

        Returns
        -------
        self
            Fitted ensemble, retaining networks for exact CDF queries.
        """
        with self._deterministic_context():
            return self._fit(x, y, cal_x, cal_y)

    def _target_tensor(self, y):
        import torch
        return torch.tensor(y[:, None], dtype=torch.float32, device=self.device)

    def _fit(self, x, y, cal_x, cal_y):
        import gc
        import torch
        from sklearn.preprocessing import StandardScaler

        self._validate_x(cal_x)
        self._features(cal_x)
        heads = self._heads(x)
        features = self._features(x)
        self.seen_heads = set(heads)
        hh = torch.tensor(heads, dtype=torch.long, device=self.device)
        self.scaler = StandardScaler().fit(features)
        xx = torch.tensor(self.scaler.transform(features), dtype=torch.float32, device=self.device)
        yy = self._target_tensor(y)
        training_context = self._training_context(x)
        monitor = None if self.patience is None else self._monitor_set(cal_x, cal_y)
        self.models, self.losses, self.monitor_losses, self.best_epochs = [], [], [], []
        for seed in self.seeds:
            torch.manual_seed(seed)
            model = self._build_module(features.shape[1])
            optimizer = torch.optim.AdamW(model.parameters(), lr=self.learning_rate,
                                          weight_decay=self.weight_decay)
            losses = []
            stop = None if monitor is None else _PatienceStop(self.patience)
            for _ in range(self.epochs):
                order = torch.randperm(len(xx), device=self.device)
                total = 0.
                for start in range(0, len(xx), self.batch_size):
                    ix = order[start:start+self.batch_size]
                    logw, mu, sigma = self._forward(model, xx[ix], hh[ix])
                    loss = self._objective_loss(
                        yy[ix], logw, mu, sigma, ix, training_context)
                    if not torch.isfinite(loss):
                        raise ValueError("nonfinite mixture likelihood")
                    optimizer.zero_grad()
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 5.)
                    optimizer.step()
                    total += loss.item()*len(ix)
                losses.append(total/len(xx))
                if stop is not None and stop.exhausted(self._monitor_loss(model, monitor), model):
                    break
            else:
                if stop is not None:
                    raise ValueError(
                        f"patience {self.patience} was not exhausted within epochs "
                        f"{self.epochs}: the monitored loss may still be improving; "
                        "raise epochs, the ceiling")
            if stop is not None:
                stop.restore(model)
                self.monitor_losses.append(stop.history)
                self.best_epochs.append(stop.best_epoch)
            model.eval()
            self.models.append(model)
            self.losses.append(losses)
        del xx, yy, hh, optimizer, order, logw, mu, sigma, loss, ix
        gc.collect()
        if self.device.startswith("cuda"):
            torch.cuda.empty_cache()
        return self

    def _equivalence_settings(self):
        """Return the settings the equivalence digest covers."""
        settings = {"components": self.components, "hidden": self.hidden,
                    "epochs": self.epochs, "batch_size": self.batch_size,
                    "seeds": list(self.seeds), "device": self.device,
                    "learning_rate": self.learning_rate,
                    "weight_decay": self.weight_decay, "min_scale": self.min_scale,
                    "activation": self.activation, "dropout": self.dropout,
                    "head_features": self.head_features,
                    "deterministic": self.deterministic}
        if self.feature_indices is not None:
            settings["feature_indices"] = self.feature_indices
        if self.patience is not None:
            settings["patience"] = self.patience
        if self.left_cdf_weight:
            settings.update(left_cdf_weight=self.left_cdf_weight,
                            left_cdf_bounds=self.left_cdf_bounds,
                            left_cdf_points=self.left_cdf_points)
        return settings

    def _equivalence_state(self):
        """Digest effective preprocessing and every fitted network tensor."""
        import numpy as np
        digest = hashlib.sha256()
        settings = self._equivalence_settings()
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
        import gc
        import numpy as np
        import torch

        heads = self._heads(x)
        if not set(heads).issubset(self.seen_heads):
            raise ValueError("unseen head requested")
        hh = torch.tensor(heads, dtype=torch.long, device=self.device)
        xx = torch.tensor(self.scaler.transform(self._features(x)), dtype=torch.float32, device=self.device)
        weights, means, scales = [], [], []
        with torch.no_grad():
            for model in self.models:
                logw, mu, sigma = self._forward(model, xx, hh)
                weights.append(logw.exp().cpu().numpy()/len(self.models))
                means.append(mu.cpu().numpy())
                scales.append(sigma.cpu().numpy())
        result = self._curve(np.concatenate(weights, 1), np.concatenate(means, 1),
                             np.concatenate(scales, 1))
        del xx, hh, logw, mu, sigma
        gc.collect()
        if self.device.startswith("cuda"):
            torch.cuda.empty_cache()
        return result


class DecisionRegionScores:
    """Proper scores at immutable caller-supplied decision thresholds.

    Parameters
    ----------
    context : list of dict
        Source-hashed threshold inventories, bound to caller identities. A
        record may also carry ``intervals``: the decision wings as
        ``[low, high]`` pairs whose endpoints are among its thresholds, in the
        same units. Only the wing methods read them.

    Attributes
    ----------
    METRIC_COUNTS : dict
        Per-row metric name to the count column that says which rows count.
    PROPER_METRICS : tuple of str
        Metrics that are proper scores, usable as a selection metric.
    WING_METRICS : tuple of str
        The proper metrics that need the wing intervals.

    Examples
    --------
    Score a forecast against its entry-known threshold inventory::

        scorer = DecisionRegionScores(context)
        scores = scorer.score(curve, outcomes)
        wings = scorer.wing_score(curve, outcomes)  # needs ``intervals``
    """

    WING_METRICS = ("decision_wing_twcrps",)
    PROPER_METRICS = ("decision_strike_brier", *WING_METRICS)
    METRIC_COUNTS = {"decision_strike_brier": "decision_strike_count",
                     "decision_bias": "decision_strike_count",
                     "decision_wing_twcrps": "decision_wing_segment_count"}

    def __init__(self, context):
        import numpy as np
        self.inventory, identities, self._intervals = [], set(), []
        self._segments = None
        for record in context:
            identity = tuple(record["identity"])
            values = np.asarray(record["thresholds"], dtype=float)
            weights = np.asarray(record["weights"], dtype=float)
            if (not identity or any(not isinstance(v, str) or not v for v in identity)
                    or identity in identities or values.ndim != 1
                    or weights.shape != values.shape or not np.isfinite(values).all()
                    or not np.isfinite(weights).all() or (weights <= 0).any()
                    or list(values) != sorted(set(values.tolist()))
                    or (len(values) and not np.isclose(weights.sum(), 1.))):
                raise ValueError("invalid generic decision inventory")
            identities.add(identity)
            self.inventory.append((values, weights))
            self._intervals.append(record.get("intervals"))

    def arrays(self):
        """Return padded thresholds, normalized masses and observation counts."""
        import numpy as np
        width = max(1, max((len(v) for v, _ in self.inventory), default=0))
        thresholds = np.zeros((len(self.inventory), width), dtype="float64")
        weights = np.zeros_like(thresholds)
        for row, (values, mass) in enumerate(self.inventory):
            thresholds[row, :len(values)] = values
            weights[row, :len(mass)] = mass
        return thresholds, weights, np.asarray([len(v) for v, _ in self.inventory])

    def _outcomes(self, y):
        """Return outcomes as a finite float vector paired with the inventories."""
        import numpy as np
        y = np.asarray(y, dtype=float)
        if y.ndim != 1 or len(y) != len(self.inventory) or not np.isfinite(y).all():
            raise ValueError("outcomes must be finite and paired with decision inventories")
        return y

    def score(self, curve, y):
        """Return local Brier, calibration residual and eligible strike counts."""
        import numpy as np
        thresholds, weights, counts = self.arrays()
        y = self._outcomes(y)
        error = curve.cdf(thresholds) - (y[:, None] <= thresholds)
        brier, bias = (weights * error**2).sum(1), (weights * error).sum(1)
        brier[counts == 0] = np.nan
        bias[counts == 0] = np.nan
        return {"decision_strike_brier": brier, "decision_bias": bias,
                "decision_strike_count": counts}

    @staticmethod
    def _checked_intervals(intervals):
        """Return ``[low, high]`` pairs as a finite (n, 2) float array or refuse."""
        import numpy as np
        try:
            pairs = np.asarray(intervals, dtype=float)
        except (TypeError, ValueError):
            raise ValueError("invalid decision wing intervals") from None
        if pairs.size == 0:
            return pairs.reshape(0, 2)
        if (pairs.ndim != 2 or pairs.shape[1] != 2 or not np.isfinite(pairs).all()
                or (pairs[:, 0] >= pairs[:, 1]).any()):
            raise ValueError("invalid decision wing intervals")
        return pairs

    @staticmethod
    def wing_groups(intervals):
        """Split decision-wing intervals by side of zero, the underlying's return.

        Parameters
        ----------
        intervals : list of [low, high]
            Standardized wing intervals, such as ``[[-2., -1.], [.5, 1.]]``.

        Returns
        -------
        list
            ``[lower, upper]``: intervals with ``high <= 0`` and with
            ``low >= 0``, each a list of ``[low, high]`` in input order.

        Raises
        ------
        ValueError
            For an invalid interval, or one that straddles zero: its side is
            not guessed.

        Examples
        --------
        Put wings below zero, call wings above::

            DecisionRegionScores.wing_groups([[-2., -1.], [.5, 1.]])
            # -> [[[-2.0, -1.0]], [[0.5, 1.0]]]
        """
        pairs = DecisionRegionScores._checked_intervals(intervals)
        if ((pairs[:, 0] < 0) & (pairs[:, 1] > 0)).any():
            raise ValueError("decision wing interval straddles zero")
        return [pairs[pairs[:, 1] <= 0].tolist(), pairs[pairs[:, 0] >= 0].tolist()]

    @staticmethod
    def segments_from_groups(groups):
        """Union each group and give every non-empty group equal mass, uniform.

        Parameters
        ----------
        groups : list of list of [low, high]
            Interval groups; empty groups carry no mass.

        Returns
        -------
        list of [low, high, density]
            Sorted segments of each group's union. A group's mass is
            ``1 / number of non-empty groups``, spread uniformly over its
            union, so the densities integrate to one.

        Raises
        ------
        ValueError
            For invalid intervals or a positive-measure overlap between the
            unions of different groups (touching is allowed).

        Examples
        --------
        One put wing and one call wing, half the mass each::

            DecisionRegionScores.segments_from_groups([[[-2., -1.]], [[1., 3.]]])
            # -> [[-2.0, -1.0, 0.5], [1.0, 3.0, 0.25]]
        """
        if not isinstance(groups, (list, tuple)):
            raise ValueError("invalid decision wing groups")
        unions = []
        for group in groups:
            union = []
            pairs = DecisionRegionScores._checked_intervals(group)
            for low, high in sorted(map(tuple, pairs.tolist())):
                if union and low <= union[-1][1]:
                    union[-1][1] = max(union[-1][1], high)
                else:
                    union.append([low, high])
            if union:
                unions.append(union)
        segments = [[low, high, 1./(len(unions)*sum(b-a for a, b in union))]
                    for union in unions for low, high in union]
        segments.sort()
        if any(nxt[0] < prev[1] for prev, nxt in zip(segments, segments[1:])):
            raise ValueError("decision wing groups overlap")
        return segments

    def _wing_segments(self):
        """Validate once and return each record's (S, 3) low/high/density array."""
        import numpy as np
        if self._segments is None:
            rows = []
            for (cutoffs, _), intervals in zip(self.inventory, self._intervals):
                if intervals is None:
                    raise ValueError("decision wing intervals missing")
                pairs = self._checked_intervals(intervals)
                if bool(len(cutoffs)) != bool(len(pairs)):
                    raise ValueError("decision wing intervals and thresholds must be "
                                     "both empty or both non-empty")
                if not np.isin(pairs, cutoffs).all():
                    raise ValueError("decision wing endpoint is not a listed threshold")
                rows.append(np.asarray(self.segments_from_groups(self.wing_groups(pairs)),
                                       dtype=float).reshape(-1, 3))
            self._segments = rows
        return self._segments

    def segment_arrays(self):
        """Return padded wing segments and their per-record counts.

        Returns
        -------
        tuple
            ``(lower, upper, density, counts)``: three (records, width) float
            arrays, zero padded (a zero-width padding segment integrates to
            zero), and the integer segment count per record. A record with
            no thresholds is a null region with count zero.

        Raises
        ------
        ValueError
            When a record lacks ``intervals``, has exactly one of thresholds
            and intervals empty, lists an endpoint that is not among its
            thresholds, or has an interval straddling zero.
        """
        import numpy as np
        segments = self._wing_segments()
        width = max(1, max((len(rows) for rows in segments), default=0))
        lower = np.zeros((len(segments), width))
        upper, density = np.zeros_like(lower), np.zeros_like(lower)
        for row, found in enumerate(segments):
            lower[row, :len(found)] = found[:, 0]
            upper[row, :len(found)] = found[:, 1]
            density[row, :len(found)] = found[:, 2]
        return lower, upper, density, np.asarray([len(rows) for rows in segments])

    def wing_score(self, curve, y):
        """Score the CDF across whole condor wings, not only at strikes.

        ``decision_wing_twcrps`` is ``sum_s d_s * int (F(z) - 1{y <= z})**2 dz``
        over each record's wing segments (indicator-weighted CRPS). A mixture
        curve is integrated with Gauss-Legendre cut at the outcome and at its
        component means, a grid curve exactly, any other curve with plain
        Gauss-Legendre (within 1e-3 relative on kinked curves).

        Parameters
        ----------
        curve : curve
            Rowwise forecasts paired with the records.
        y : array-like
            One finite outcome per record, in the thresholds' units.

        Returns
        -------
        dict
            ``decision_wing_twcrps`` (NaN on null regions) and
            ``decision_wing_segment_count``; ``score`` is unchanged.

        Raises
        ------
        ValueError
            For unpaired outcomes or any refusal of ``segment_arrays``.

        Examples
        --------
        Wing score beside the strike scores::

            wings = DecisionRegionScores(context).wing_score(curve, outcomes)
        """
        import numpy as np
        lower, upper, density, counts = self.segment_arrays()
        integrals = curve._segment_integrals(lower, upper, self._outcomes(y))
        value = (density * integrals).sum(1)
        value[counts == 0] = np.nan
        return {"decision_wing_twcrps": value, "decision_wing_segment_count": counts}


class _CDFMLPEncoder:
    def __init__(self, config):
        if set(config) != {"kind"}:
            raise ValueError("unknown MLP encoder settings")

    def build(self, owner, features):
        return MixtureMLPCDF._build_module(owner, features)


class _CDFGRUEncoder:
    def __init__(self, config):
        required = {"kind", "sequence_indices", "context_indices", "hidden_size", "num_layers"}
        if set(config) != required:
            raise ValueError("invalid GRU encoder settings")
        sequence, context = config["sequence_indices"], config["context_indices"]
        if (not isinstance(sequence, list) or not sequence
                or any(not isinstance(row, list) or not row for row in sequence)
                or len({len(row) for row in sequence}) != 1
                or not isinstance(context, list)):
            raise ValueError("invalid GRU sequence/context indices")
        flat = [v for row in sequence for v in row] + context
        if (any(type(v) is not int or v < 0 for v in flat)
                or len(set(flat)) != len(flat)
                or any(type(config[k]) is not int or config[k] < 1
                       for k in ("hidden_size", "num_layers"))):
            raise ValueError("invalid GRU dimensions or duplicate indices")
        self.config = config

    def build(self, owner, features):
        import torch
        config = self.config
        sequence, context = config["sequence_indices"], config["context_indices"]
        flat = [v for row in sequence for v in row] + context
        if set(flat) != set(range(features)):
            raise ValueError(f"GRU indices must partition the {features} model input "
                             "features (positions within feature_indices when set)")
        class Module(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.rnn = torch.nn.GRU(len(sequence[0]), config["hidden_size"],
                                        config["num_layers"], batch_first=True)
                self.head = MixtureMLPCDF._build_module(
                    owner, config["hidden_size"] + len(context))

            def forward(self, x):
                sequence_output, _ = self.rnn(x[:, sequence])
                return self.head(torch.cat(
                    [sequence_output[:, -1], x[:, context]], dim=1))
        return Module().to(owner.device)


class _MixtureFamily(ABC):
    """Torch-side location-scale mixture family: density, CDF and exact CRPS.

    A family names the standard distribution of every mixture component; the
    loss terms depend only on this interface, so a new family is a subclass.
    """

    KIND = ""
    _KEYS = frozenset({"kind"})

    def __init__(self, config):
        if (not isinstance(config, dict) or set(config) != self._KEYS
                or config["kind"] != self.KIND):
            raise ValueError("invalid CDF family settings")

    @abstractmethod
    def standard_cdf(self, t):
        """Return the standard component CDF."""

    @abstractmethod
    def standard_log_cdf(self, t):
        """Return the log of the standard component CDF."""

    @abstractmethod
    def standard_log_pdf(self, t):
        """Return the log of the standard component density."""

    @abstractmethod
    def abs_error(self, c, mu, sigma):
        """Return ``E|X - c|`` for a component ``X`` with location and scale."""

    @abstractmethod
    def pair_distance(self, mu, sigma):
        """Return the (rows, K, K) matrix of ``E|X_i - X_j|``."""

    @abstractmethod
    def curve(self, weights, means, scales):
        """Return the numpy curve of the same family."""

    def log_density(self, y, mu, sigma):
        """Return each component's log density at ``y``."""
        return self.standard_log_pdf((y-mu)/sigma) - sigma.log()

    def mixture_cdf(self, z, logw, mu, sigma):
        """Evaluate each row's mixture CDF at ``z`` of shape (rows, ...)."""
        shape = (z.shape[0],) + (1,)*(z.dim()-1) + (mu.shape[1],)
        t = (z.unsqueeze(-1) - mu.reshape(shape)) / sigma.reshape(shape)
        return (logw.exp().reshape(shape) * self.standard_cdf(t)).sum(-1)

    def crps(self, y, logw, mu, sigma):
        """Return the exact mixture CRPS: ``E|X - y| - E|X - X'| / 2``."""
        w = logw.exp()
        first = (w*self.abs_error(y, mu, sigma)).sum(1)
        pairs = w.unsqueeze(2)*w.unsqueeze(1)*self.pair_distance(mu, sigma)
        return first - .5*pairs.sum((1, 2))


class _GaussianFamily(_MixtureFamily):
    """Gaussian components; the legacy NLL and decision expressions, unchanged."""

    KIND = "gaussian"

    def standard_cdf(self, t):
        import torch
        return torch.special.ndtr(t)

    def standard_log_cdf(self, t):
        import torch
        return torch.special.log_ndtr(t)

    def standard_log_pdf(self, t):
        import math
        return -.5*t**2 - .5*math.log(2*math.pi)

    def log_density(self, y, mu, sigma):
        import math
        return -.5*((y-mu)/sigma)**2 - sigma.log() - .5*math.log(2*math.pi)

    @staticmethod
    def _folded_mean(m, v):
        """Return ``E|N(m, v)|`` (Grimit et al. 2006)."""
        import math
        import torch
        s = v.sqrt()
        r = m/s
        return 2*s*torch.exp(-.5*r*r)/math.sqrt(2*math.pi) + m*(2*torch.special.ndtr(r)-1)

    def abs_error(self, c, mu, sigma):
        return self._folded_mean(c-mu, sigma**2)

    def pair_distance(self, mu, sigma):
        return self._folded_mean(mu.unsqueeze(2)-mu.unsqueeze(1),
                                 sigma.unsqueeze(2)**2+sigma.unsqueeze(1)**2)

    def curve(self, weights, means, scales):
        return MixtureCurve(weights, means, scales)


class _StudentFamily(_MixtureFamily):
    """Student components with fixed integer degrees of freedom ``>= 3``.

    The CDF is the exact finite series of Abramowitz and Stegun 26.7.3-4; the
    lower tail uses the positive remainder series so it keeps relative accuracy.
    """

    KIND = "student"
    _KEYS = frozenset({"kind", "degrees"})

    def __init__(self, config):
        import math
        super().__init__(config)
        nu = config["degrees"]
        if type(nu) is not int or nu < 3:
            raise ValueError("invalid CDF family settings")
        self.degrees = nu
        self._head = nu//2
        ratio = ((lambda k: (2*k-1)/(2*k)) if nu % 2 == 0
                 else (lambda k: 2*k/(2*k+1)))
        self._coefficients = [1.]
        for k in range(1, self._head+_STUDENT_TAIL_TERMS):
            self._coefficients.append(self._coefficients[-1]*ratio(k))
        self._log_norm = (math.lgamma((nu+1)/2) - math.lgamma(nu/2)
                          - .5*math.log(nu*math.pi))
        # E|X - X'| / scale for one component: 4 sqrt(nu) B(1/2, nu-1/2)
        # / ((nu-1) B(1/2, nu/2)^2), with exactly one Gamma(1/2) in the numerator.
        self._self_distance = 4*math.sqrt(nu)*math.exp(
            math.lgamma(.5) + math.lgamma(nu-.5) - math.lgamma(nu)
            - 2*(math.lgamma(.5) + math.lgamma(nu/2) - math.lgamma((nu+1)/2)))/(nu-1)
        self._tail_mass = math.exp(math.lgamma((nu+1)/2) - math.lgamma(nu/2))/math.sqrt(math.pi)

    @staticmethod
    def _horner(coefficients, x):
        """Evaluate ``sum_j coefficients[j] * x**j`` by Horner's rule."""
        import torch
        result = torch.full_like(x, coefficients[-1])
        for coefficient in reversed(coefficients[:-1]):
            result = result*x + coefficient
        return result

    def _series(self, t):
        import math
        import torch
        nu = self.degrees
        r = torch.rsqrt(nu + t*t)
        s2 = nu*r*r
        c = t*r
        head = self._horner(self._coefficients[:self._head], s2)
        tail = s2**self._head*self._horner(self._coefficients[self._head:], s2)
        if nu % 2 == 0:
            direct, lower = .5*(1+c*head), .5*(-c)*tail
        else:
            scale = math.sqrt(nu)*r
            direct = .5*(1 + (2/math.pi)*(torch.atan(t/math.sqrt(nu)) + c*scale*head))
            lower = (1/math.pi)*(-c)*scale*tail
        return torch.where((t < 0) & (s2 < .5), lower, direct)

    def standard_cdf(self, t):
        import torch
        if torch.is_grad_enabled() and t.requires_grad:
            # The series is long; recompute it in backward instead of storing it.
            from torch.utils.checkpoint import checkpoint
            return checkpoint(self._series, t, use_reentrant=False)
        return self._series(t)

    def standard_log_cdf(self, t):
        import torch
        return self.standard_cdf(t).clamp_min(torch.finfo(t.dtype).tiny).log()

    def standard_log_pdf(self, t):
        import torch
        return self._log_norm - (self.degrees+1)/2*torch.log1p(t*t/self.degrees)

    def abs_error(self, c, mu, sigma):
        import torch
        a = (c-mu)/sigma
        nu = self.degrees
        return sigma*(a*(2*self.standard_cdf(a)-1)
                      + 2*torch.exp(self.standard_log_pdf(a))*(nu+a*a)/(nu-1))

    def pair_distance(self, mu, sigma):
        """Cross pairs by Gauss-Legendre over the narrower component's density."""
        import math
        import torch
        nu = self.degrees
        x, g = _gauss_legendre(_QUADRATURE_NODES)
        phi = torch.as_tensor(x*math.pi/2, dtype=mu.dtype, device=mu.device)
        omega = (self._tail_mass*torch.as_tensor(g*math.pi/2, dtype=mu.dtype, device=mu.device)
                 * torch.cos(phi)**(nu-1))
        u = math.sqrt(nu)*torch.tan(phi)
        mi, mj = mu.unsqueeze(2), mu.unsqueeze(1)
        si, sj = sigma.unsqueeze(2), sigma.unsqueeze(1)
        narrow = si <= sj
        near, near_scale = torch.where(narrow, mi, mj), torch.where(narrow, si, sj)
        far, far_scale = torch.where(narrow, mj, mi), torch.where(narrow, sj, si)
        points = near.unsqueeze(-1) + near_scale.unsqueeze(-1)*u
        cross = (omega*self.abs_error(points, far.unsqueeze(-1), far_scale.unsqueeze(-1))).sum(-1)
        same = torch.eye(mu.shape[1], dtype=torch.bool, device=mu.device)
        return torch.where(same, self._self_distance*si.expand_as(cross), cross)

    def curve(self, weights, means, scales):
        return StudentMixtureCurve(weights, means, scales, self.degrees)


class _CDFLossTerm(ABC):
    """One strategy in the composite Torch CDF loss, built with its family.

    ``scope`` is ``global`` (every row) or ``local`` (rows with decision
    structure); ``context_keys`` names the context arrays ``values`` reads.
    Every context also carries ``weights``, which fixes the row count.
    """

    scope = ""
    context_keys = ()

    def __init__(self, family):
        self.family = family

    def eligible(self, context):
        """Return the rows the term scores; global terms score every row."""
        import torch
        weights = context["weights"]
        return torch.ones(len(weights), dtype=torch.bool, device=weights.device)

    def population_count(self, context):
        """Count eligible rows in the full training context, for minibatch scaling."""
        return self.eligible(context).sum()

    @abstractmethod
    def values(self, y, logw, mu, sigma, context):
        """Return per-row (values, eligible) for one minibatch."""


class _CDFNLL(_CDFLossTerm):
    scope = "global"

    def values(self, y, logw, mu, sigma, context):
        import torch
        logp = self.family.log_density(y, mu, sigma)
        return -torch.logsumexp(logw+logp, dim=1), self.eligible(context)


class _CDFCRPS(_CDFLossTerm):
    scope = "global"

    def values(self, y, logw, mu, sigma, context):
        logw, mu, sigma = (a.to(y.dtype) for a in (logw, mu, sigma))
        return self.family.crps(y, logw, mu, sigma), self.eligible(context)


class _CDFDecisionBrier(_CDFLossTerm):
    scope = "local"
    context_keys = ("thresholds", "weights")

    def eligible(self, context):
        return context["weights"].sum(1) > 0

    def values(self, y, logw, mu, sigma, context):
        thresholds, weights = context["thresholds"], context["weights"]
        z = (thresholds[:, None, :]-mu[:, :, None])/sigma[:, :, None]
        p = (logw.exp()[:, :, None]*self.family.standard_cdf(z)).sum(1)
        truth = (y <= thresholds).to(p.dtype)
        return ((p-truth)**2*weights).sum(1), self.eligible(context)


class _CDFDecisionLog(_CDFDecisionBrier):
    def values(self, y, logw, mu, sigma, context):
        import torch
        thresholds, weights = context["thresholds"], context["weights"]
        z = (thresholds[:, None, :]-mu[:, :, None])/sigma[:, :, None]
        logp = torch.logsumexp(logw[:, :, None]+self.family.standard_log_cdf(z), dim=1)
        logq = torch.logsumexp(logw[:, :, None]+self.family.standard_log_cdf(-z), dim=1)
        return (-(torch.where(y <= thresholds, logp, logq)*weights).sum(1),
                self.eligible(context))


class _CDFWingTwCRPS(_CDFLossTerm):
    """Threshold-weighted CRPS over the condor wings, indicator weight.

    Per row ``sum_s d_s * int_{lo_s}^{hi_s} (F(z) - 1{y <= z})**2 dz``. Each
    segment is cut at the clamped outcome and at the component means (which do
    not carry gradient: the integrand is continuous across a cut), with
    ``_QUADRATURE_NODES`` Gauss-Legendre points per sub-piece. Zero-width
    sub-pieces, which are most of them, are never evaluated.
    """

    scope = "local"
    context_keys = ("lower", "upper", "density")

    def eligible(self, context):
        return context["density"].sum(1) > 0

    def values(self, y, logw, mu, sigma, context):
        import torch
        logw, mu, sigma = (a.to(y.dtype) for a in (logw, mu, sigma))
        lower, upper, density = (context[k].to(y.dtype) for k in self.context_keys)
        x, g = _gauss_legendre(_QUADRATURE_NODES)
        nodes, weights = (torch.as_tensor(a, dtype=y.dtype, device=y.device) for a in (x, g))
        cut = torch.minimum(torch.maximum(y, lower), upper)
        start, stop = torch.stack([lower, cut], -1), torch.stack([cut, upper], -1)
        kinks = mu.detach()[:, None, None, :]
        inner = torch.minimum(torch.maximum(kinks, start.unsqueeze(-1)), stop.unsqueeze(-1))
        edges = torch.sort(torch.cat(
            [start.unsqueeze(-1), inner, stop.unsqueeze(-1)], -1), -1).values
        a, b = edges[..., :-1], edges[..., 1:]
        found = (b > a).nonzero(as_tuple=True)
        row, segment, side = found[:3]
        low, high = a[found], b[found]
        z = ((low+high)/2).unsqueeze(-1) + ((high-low)/2).unsqueeze(-1)*nodes
        f = self.family.mixture_cdf(z, logw[row], mu[row], sigma[row])
        # Left of the outcome the error is F, right of it F - 1.
        gap = (f-side.to(y.dtype).unsqueeze(-1))**2
        piece = (high-low)/2*(gap*weights).sum(-1)
        total = torch.zeros(len(y), dtype=y.dtype, device=y.device)
        return total.index_add(0, row, density[row, segment]*piece), self.eligible(context)


class TorchCDF(MixtureMLPCDF):
    """Configurable encoder, family and composite proper scores for scalar CDFs.

    The loss is ``sum_g w_g * mean_over_all_rows(g) + sum_l w_l *
    mean_over_eligible_rows(l)``: a global term (``nll`` or ``crps``) models
    every date, a local term scores the dates that have decision structure and
    never dilutes its weight on dates without it (minibatches are rescaled by
    the full-training eligible fraction).

    Parameters
    ----------
    encoder : dict
        kind=mlp, or kind=gru with explicit oldest-to-newest sequence_indices,
        disjoint context_indices, hidden_size and num_layers.
    losses : list of dict
        Unique ``{"kind", "weight"}`` terms, weight above zero, at least one
        global (``nll``, ``crps``) and one local (``decision_brier``,
        ``decision_log``, ``wing_twcrps``). ``wing_twcrps`` is the
        indicator-weighted CRPS over the condor wings, read from each record's
        ``intervals``.
    family : dict or None
        Absent means Gaussian components. ``{"kind": "student", "degrees": k}``
        with integer ``k >= 3`` selects Student components; ``{"kind":
        "gaussian"}`` names the default explicitly.
    settings : dict
        Existing mixture training and output-head settings. ``feature_indices``
        (the base class's one rule; absent = every column) picks the encoder's
        input columns, and GRU indices are then positions WITHIN that subset.
        Task heads and the legacy left-tail penalty are deliberately refused.

    Examples
    --------
    A plain encoder, a global CRPS term and the wing term, Student components::

        model = TorchCDF(encoder={"kind": "mlp"},
                         family={"kind": "student", "degrees": 5},
                         losses=[{"kind": "crps", "weight": 1.0},
                                 {"kind": "wing_twcrps", "weight": 0.5}],
                         device="cpu")
    """

    _ENCODERS = {"mlp": _CDFMLPEncoder, "gru": _CDFGRUEncoder}
    _FAMILIES = {_GaussianFamily.KIND: _GaussianFamily,
                 _StudentFamily.KIND: _StudentFamily}
    _DEFAULT_FAMILY = _GaussianFamily.KIND
    _LOSSES = {"nll": _CDFNLL, "crps": _CDFCRPS, "decision_brier": _CDFDecisionBrier,
               "decision_log": _CDFDecisionLog, "wing_twcrps": _CDFWingTwCRPS}
    _WING_KEYS = _CDFWingTwCRPS.context_keys

    def __init__(self, encoder, losses, family=None, **settings):
        import copy
        import math
        super().__init__(**settings)
        if self.head_features or self.left_cdf_weight:
            raise ValueError("TorchCDF refuses head_features and left_cdf_weight: "
                             "it uses explicit encoder columns and composite losses")
        if not isinstance(encoder, dict) or encoder.get("kind") not in self._ENCODERS:
            raise ValueError("unknown CDF encoder")
        if family is not None and (not isinstance(family, dict)
                                   or family.get("kind") not in self._FAMILIES):
            raise ValueError("unknown CDF family")
        if (not isinstance(losses, list) or not losses
                or any(not isinstance(term, dict) or set(term) != {"kind", "weight"}
                       or term["kind"] not in self._LOSSES
                       or type(term["weight"]) not in (int, float)
                       or not math.isfinite(term["weight"]) or term["weight"] <= 0
                       for term in losses)
                or len({term["kind"] for term in losses}) != len(losses)
                or {self._LOSSES[term["kind"]].scope for term in losses}
                != {"global", "local"}):
            raise ValueError("invalid composite loss terms")
        self.encoder_config, self.loss_config = copy.deepcopy(encoder), copy.deepcopy(losses)
        self.family_config = copy.deepcopy(family)
        self.family = self._FAMILIES[
            self._DEFAULT_FAMILY if family is None else family["kind"]](
            self.family_config or {"kind": self._DEFAULT_FAMILY})
        self._terms = [self._LOSSES[term["kind"]](self.family) for term in losses]
        self.encoder = self._ENCODERS[encoder["kind"]](self.encoder_config)

    def _curve(self, weights, means, scales):
        return self.family.curve(weights, means, scales)

    def _equivalence_settings(self):
        settings = super()._equivalence_settings()
        if self.family_config is not None:
            settings["family"] = self.family_config
        return settings

    def _context_arrays(self, scorer):
        """Return the numpy context arrays the configured terms read, refusing early."""
        arrays = dict(zip(("thresholds", "weights"), scorer.arrays()))
        if set(self._WING_KEYS) & {k for term in self._terms for k in term.context_keys}:
            arrays.update(zip(self._WING_KEYS, scorer.segment_arrays()))
        return arrays

    def fit_decision_context(self, fit_context, cal_context):
        """Validate and attach entry-known inventories without using outcomes."""
        self._fit_context = DecisionRegionScores(fit_context)
        self._context_arrays(self._fit_context)
        self._cal_context = DecisionRegionScores(cal_context)
        self._context_arrays(self._cal_context)
        return self

    def _target_tensor(self, y):
        import torch
        return torch.tensor(y[:, None], dtype=torch.float64, device=self.device)

    def _build_module(self, features):
        return self.encoder.build(self, features)

    def _context_tensors(self, scorer, x, kind, rows):
        """Return one row set's context as tensors, refusing an unattached or empty one."""
        import torch
        if scorer is None or len(scorer.inventory) != len(x):
            raise ValueError(f"identity-keyed {kind} context was not attached")
        context = {key: torch.as_tensor(value, device=self.device) for key, value
                   in self._context_arrays(scorer).items()}
        for term_config, term in zip(self.loss_config, self._terms):
            if not term.population_count(context) > 0:
                raise ValueError(f"no eligible {rows} rows for {term_config['kind']}")
        return context

    def _training_context(self, x):
        return self._context_tensors(getattr(self, "_fit_context", None), x,
                                     "decision", "training")

    def _monitor_context(self, cal_x):
        return self._context_tensors(getattr(self, "_cal_context", None), cal_x,
                                     "calibration", "calibration")

    def _objective_loss(self, y, logw, mu, sigma, indices, context):
        local = {key: value[indices] for key, value in context.items()}
        rows = len(context["weights"])
        total = mu.sum()*0.
        for term_config, term in zip(self.loss_config, self._terms):
            values, eligible = term.values(y, logw, mu, sigma, local)
            # Uniform minibatches estimate the full-population objective,
            # including batches with no locally eligible observations.
            normalization = rows/term.population_count(context)
            total = total + term_config["weight"]*values[eligible].sum()/len(y)*normalization
        return total

    def loss_report(self, x, y, context):
        """Measure final ensemble losses, counts and local errors in eval mode."""
        import numpy as np
        import torch
        scorer = DecisionRegionScores(context)
        counts = scorer.arrays()[2]
        y = np.asarray(y, dtype=float)
        if (y.ndim != 1 or len(counts) != len(y) or len(x) != len(y)
                or not np.isfinite(y).all()):
            raise ValueError("loss report outcomes, features and context must be paired")
        arrays = self._context_arrays(scorer)
        curve = self.curve(x)
        total, terms = 0., {}
        # CPU float64 evaluation is bounded by the same configured mini-batch.
        for term_config, term in zip(self.loss_config, self._terms):
            sums, n = 0., 0
            for start in range(0, len(y), self.batch_size):
                sl = slice(start, start+self.batch_size)
                args = [torch.as_tensor(a[sl], dtype=torch.float64) for a in
                        (y[:, None], np.log(curve.weights), curve.means, curve.scales)]
                batch = {key: torch.as_tensor(a[sl], dtype=torch.float64)
                         for key, a in arrays.items()}
                values, eligible = term.values(*args, batch)
                sums += values[eligible].sum().item()
                n += int(eligible.sum())
            mean = sums/n if n else None
            terms[term_config["kind"]] = {"mean": mean, "n": n,
                                          "weight": term_config["weight"]}
            if mean is not None:
                total += term_config["weight"]*mean
        return {"composite": total if all(t["n"] for t in terms.values()) else None,
                "terms": terms, "n": len(y), "eligible_n": int((counts > 0).sum()),
                "threshold_n": int(counts.sum())}

    def _research_state(self):
        import torch
        state = {"encoder": self.encoder_config, "losses": self.loss_config,
                 "training_composite_by_seed": self.losses,
                 "peak_cuda_allocated_bytes": (torch.cuda.max_memory_allocated(self.device)
                                               if self.device.startswith("cuda") else 0)}
        if self.family_config is not None:
            state["family"] = self.family_config
        if self.patience is not None:
            state.update(patience=self.patience, best_epoch_by_seed=self.best_epochs,
                         monitor_loss_by_seed=self.monitor_losses)
        return state


class DecisionWeightedMixtureMLPCDF(MixtureMLPCDF):
    """Mixture MLP with global NLL plus proper entry-strike CDF scoring.

    Actual entry-chain strikes receive a put/call-balanced Brier term. A fixed
    symmetric grid receives a smaller Brier term, while negative log likelihood
    remains the globally strict proper score. Region weights inspect no outcome.
    """

    def __init__(self, decision_weight=1., global_cdf_weight=.25,
                 global_thresholds=(-4., -3., -2., -1., 0., 1., 2., 3., 4.),
                 **settings):
        import math

        super().__init__(**settings)
        if (any(isinstance(v, bool) or not math.isfinite(v) or v < 0
                       for v in (decision_weight, global_cdf_weight))
                or decision_weight == global_cdf_weight == 0
                or not isinstance(global_thresholds, (list, tuple))
                or len(global_thresholds) < 2
                or any(not math.isfinite(v) for v in global_thresholds)
                or any(a >= b for a, b in zip(global_thresholds,
                                               global_thresholds[1:]))):
            raise ValueError("invalid decision-weighted MLP parameters")
        self.decision_weight = float(decision_weight)
        self.global_cdf_weight = float(global_cdf_weight)
        self.global_thresholds = tuple(float(v) for v in global_thresholds)

    def fit_decision_context(self, fit_context, cal_context):
        self._fit_decisions = _decision_threshold_inventory(fit_context)
        _decision_threshold_inventory(cal_context)
        return self

    @staticmethod
    def _cdf_brier(y, logw, mu, sigma, thresholds, weights):
        import math
        import torch

        z = ((thresholds[:, None, :]-mu[:, :, None])
             /(sigma[:, :, None]*math.sqrt(2.)))
        cdf = (logw.exp()[:, :, None]*(.5*(1.+torch.erf(z)))).sum(1)
        observed = (y <= thresholds).to(cdf.dtype)
        return ((cdf-observed).square()*weights).sum(1).mean()

    def _training_context(self, x):
        import numpy as np
        import torch

        if not hasattr(self, "_fit_decisions") or len(self._fit_decisions) != len(x):
            raise ValueError("identity-keyed decision context was not attached")
        inventory = self._fit_decisions
        width = max(1, max(len(values) for values, _ in inventory))
        thresholds = np.zeros((len(inventory), width), dtype="float32")
        weights = np.zeros_like(thresholds)
        for row, (values, mass) in enumerate(inventory):
            thresholds[row, :len(values)] = values
            weights[row, :len(mass)] = mass
        return (torch.tensor(thresholds, device=self.device),
                torch.tensor(weights, device=self.device),
                torch.tensor(self.global_thresholds, dtype=torch.float32,
                             device=self.device))

    def _extra_training_loss(self, y, logw, mu, sigma, indices, context):
        import torch

        thresholds, weights, global_thresholds = context
        loss = torch.zeros((), dtype=mu.dtype, device=mu.device)
        if self.decision_weight:
            loss = loss + self.decision_weight*self._cdf_brier(
                y, logw, mu, sigma, thresholds[indices], weights[indices])
        if self.global_cdf_weight:
            grid = global_thresholds[None, :].expand(len(y), -1)
            mass = torch.full_like(grid, 1./grid.shape[1])
            loss = loss + self.global_cdf_weight*self._cdf_brier(
                y, logw, mu, sigma, grid, mass)
        return loss

    def _research_state(self):
        return {"objective": "nll_plus_decision_and_global_cdf_brier",
                "decision_weight": self.decision_weight,
                "global_cdf_weight": self.global_cdf_weight,
                "strike_weighting": "deduplicated_put_call_balanced"}


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


class SetMixtureCDF(MixtureMLPCDF):
    """Permutation-invariant raw-contract encoder with a mixture-density head."""

    def __init__(self, nodes, node_features, tensor_indices, mask_indices,
                 context_indices, pooling="deepsets", attention_heads=2, **settings):
        positions = [*tensor_indices, *mask_indices, *context_indices]
        if (type(nodes) is not int or type(node_features) is not int
                or min(nodes, node_features) < 1
                or len(tensor_indices) != nodes*node_features
                or len(mask_indices) != nodes or not context_indices
                or any(type(v) is not int or v < 0 for v in positions)
                or len(set(positions)) != len(positions)
                or pooling not in ("deepsets", "attention")
                or type(attention_heads) is not int or attention_heads < 1):
            raise ValueError("invalid set encoder contract")
        self.nodes, self.node_features = nodes, node_features
        self.tensor_indices = tuple(tensor_indices)
        self.mask_indices = tuple(mask_indices)
        self.context_indices = tuple(context_indices)
        self.pooling, self.attention_heads = pooling, attention_heads
        settings.pop("feature_indices", None)
        settings.pop("head_features", None)
        if settings.get("patience") is not None:
            raise ValueError("SetMixtureCDF trains its own loop: patience is unsupported")
        super().__init__(feature_indices=None, head_features=None, **settings)

    def _validate_x(self, x):
        import numpy as np
        x = np.asarray(x)
        if x.ndim != 2 or max((*self.tensor_indices, *self.mask_indices,
                               *self.context_indices)) >= x.shape[1]:
            raise ValueError("set encoder indices outside input")
        masks = x[:, self.mask_indices]
        nodes = x[:, self.tensor_indices].reshape(len(x), self.nodes, self.node_features)
        if (not np.isin(masks, [0, 1]).all()
                or np.isinf(x[:, self.context_indices]).any()
                or not np.isfinite(nodes[masks == 1]).all()):
            raise ValueError("invalid set mask, active node or context")

    def _arrays_for_torch(self, x, fit=False):
        import numpy as np
        from sklearn.preprocessing import StandardScaler
        x = np.asarray(x, dtype=float)
        masks = x[:, self.mask_indices].astype(float)
        nodes = x[:, self.tensor_indices].reshape(len(x), self.nodes, self.node_features)
        if fit:
            self.node_scaler = StandardScaler().fit(nodes[masks == 1])
            self.context_scaler = StandardScaler().fit(x[:, self.context_indices])
        flat = self.node_scaler.transform(nodes.reshape(-1, self.node_features)).reshape(nodes.shape)
        flat = np.where(masks[:, :, None] == 1, flat, 0.)
        context = self.context_scaler.transform(x[:, self.context_indices])
        return flat, masks, context

    def _build_set_module(self):
        import torch
        width, components = self.hidden[0], self.components
        if self.pooling == "attention" and width % self.attention_heads:
            raise ValueError("attention width must be divisible by attention heads")

        class Module(torch.nn.Module):
            def __init__(module):
                super().__init__()
                module.embed = torch.nn.Sequential(
                    torch.nn.Linear(self.node_features, width), torch.nn.SiLU(),
                    torch.nn.Linear(width, width))
                module.attention = (torch.nn.TransformerEncoder(
                    torch.nn.TransformerEncoderLayer(
                        width, self.attention_heads, 2*width, dropout=self.dropout,
                        activation="gelu", batch_first=True), 1)
                    if self.pooling == "attention" else None)
                layers, size = [], width+len(self.context_indices)
                for hidden in self.hidden[1:]:
                    layers.extend([torch.nn.Linear(size, hidden), torch.nn.SiLU()])
                    size = hidden
                layers.append(torch.nn.Linear(size, 3*components))
                module.head = torch.nn.Sequential(*layers)

            def forward(module, nodes, masks, context):
                encoded = module.embed(nodes)
                if module.attention is not None:
                    safe = masks.clone()
                    empty = safe.sum(1) == 0
                    safe[empty, 0] = 1
                    encoded = module.attention(encoded, src_key_padding_mask=safe == 0)
                    encoded[empty] = 0
                pooled = ((encoded*masks[:, :, None]).sum(1)
                          / masks.sum(1, keepdim=True).clamp_min(1))
                return module.head(torch.cat([pooled, context], dim=1))
        return Module().to(self.device)

    def fit(self, x, y, cal_x, cal_y):
        with self._deterministic_context():
            return self._fit_set(x, y, cal_x, cal_y)

    def _fit_set(self, x, y, cal_x, cal_y):
        import torch
        self._validate_x(x); self._validate_x(cal_x)
        nodes, masks, context = self._arrays_for_torch(x, fit=True)
        tensors = [torch.tensor(v, dtype=torch.float32, device=self.device)
                   for v in (nodes, masks, context)]
        yy = torch.tensor(y[:, None], dtype=torch.float32, device=self.device)
        self.models, self.losses = [], []
        for seed in self.seeds:
            torch.manual_seed(seed)
            model = self._build_set_module()
            optimizer = torch.optim.AdamW(model.parameters(), lr=self.learning_rate,
                                          weight_decay=self.weight_decay)
            losses = []
            for _ in range(self.epochs):
                order = torch.randperm(len(yy), device=self.device); total = 0.
                for start in range(0, len(yy), self.batch_size):
                    ix = order[start:start+self.batch_size]
                    logw, mu, sigma = self._parts(model(*(value[ix] for value in tensors)))
                    loss = -torch.logsumexp(logw+self._logp(yy[ix], mu, sigma), dim=1).mean()
                    if not torch.isfinite(loss):
                        raise ValueError("nonfinite set likelihood")
                    optimizer.zero_grad(); loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 5.); optimizer.step()
                    total += loss.item()*len(ix)
                losses.append(total/len(yy))
            model.eval(); self.models.append(model); self.losses.append(losses)
        return self

    def curve(self, x):
        import numpy as np
        import torch
        self._validate_x(x)
        values = [torch.tensor(v, dtype=torch.float32, device=self.device)
                  for v in self._arrays_for_torch(x)]
        weights, means, scales = [], [], []
        with torch.no_grad():
            for model in self.models:
                logw, mu, sigma = self._parts(model(*values))
                weights.append(logw.exp().cpu().numpy()/len(self.models))
                means.append(mu.cpu().numpy()); scales.append(sigma.cpu().numpy())
        return MixtureCurve(np.concatenate(weights, 1), np.concatenate(means, 1),
                            np.concatenate(scales, 1))


class SplineFlowCDF(CDFEstimator):
    """Conditional one-dimensional rational-quadratic normalizing spline."""

    def __init__(self, probabilities, hidden=32, bins=8, epochs=20,
                 batch_size=1024, learning_rate=.001, weight_decay=.01,
                 tail_bound=8., tail_width=2., seed=829, device="cuda",
                 deterministic=False, feature_indices=None):
        import math
        if (len(probabilities) < 3 or probabilities[0] <= 0 or probabilities[-1] >= 1
                or any(a >= b for a, b in zip(probabilities, probabilities[1:]))
                or any(type(v) is not int or v < 1 for v in
                       (hidden, bins, epochs, batch_size))
                or any(not math.isfinite(v) or v <= 0 for v in
                       (learning_rate, tail_bound, tail_width))
                or not math.isfinite(weight_decay) or weight_decay < 0
                or type(seed) is not int or seed < 0 or type(deterministic) is not bool
                or (feature_indices is not None and
                    (not feature_indices or len(set(feature_indices)) != len(feature_indices)
                     or any(type(v) is not int or v < 0 for v in feature_indices)))):
            raise ValueError("invalid spline-flow parameters")
        self.probabilities = tuple(float(p) for p in probabilities)
        self.hidden, self.bins, self.epochs, self.batch_size = hidden, bins, epochs, batch_size
        self.learning_rate, self.weight_decay = learning_rate, weight_decay
        self.tail_bound, self.tail_width = tail_bound, tail_width
        self.seed, self.device, self.deterministic = seed, device, deterministic
        self.feature_indices = None if feature_indices is None else tuple(feature_indices)

    def _validate_x(self, x):
        import numpy as np
        x = np.asarray(x)
        if (x.ndim != 2 or np.isinf(x).any()
                or (self.feature_indices is not None
                    and max(self.feature_indices) >= x.shape[1])):
            raise ValueError("spline-flow features must be finite")

    def _features(self, x):
        import numpy as np
        x = np.asarray(x)
        return x if self.feature_indices is None else x[:, self.feature_indices]

    def fit(self, x, y, cal_x, cal_y):
        import numpy as np
        import torch
        from sklearn.preprocessing import StandardScaler
        from nflows.distributions.normal import StandardNormal
        from nflows.flows.base import Flow
        from nflows.transforms.autoregressive import MaskedPiecewiseRationalQuadraticAutoregressiveTransform
        self._validate_x(x); self._validate_x(cal_x)
        if self.device.startswith("cuda") and not torch.cuda.is_available():
            raise ValueError("declared CUDA spline flow is unavailable")
        features = self._features(x)
        self.scaler = StandardScaler().fit(features)
        self.y_mean, self.y_scale = float(np.mean(y)), float(np.std(y))
        if not np.isfinite(self.y_scale) or self.y_scale <= 0:
            raise ValueError("spline-flow target has zero scale")
        xx = torch.tensor(self.scaler.transform(features), dtype=torch.float32, device=self.device)
        yy = torch.tensor(((np.asarray(y)-self.y_mean)/self.y_scale)[:, None],
                          dtype=torch.float32, device=self.device)
        torch.manual_seed(self.seed)
        transform = MaskedPiecewiseRationalQuadraticAutoregressiveTransform(
            features=1, hidden_features=self.hidden, context_features=features.shape[1],
            num_bins=self.bins, tails="linear", tail_bound=self.tail_bound,
            num_blocks=2, use_residual_blocks=True, random_mask=False)
        self.model = Flow(transform, StandardNormal([1])).to(self.device)
        optimizer = torch.optim.AdamW(self.model.parameters(), lr=self.learning_rate,
                                      weight_decay=self.weight_decay)
        self.losses = []
        old = torch.are_deterministic_algorithms_enabled()
        if self.deterministic:
            torch.use_deterministic_algorithms(True)
        try:
            for _ in range(self.epochs):
                order = torch.randperm(len(xx), device=self.device); total = 0.
                for start in range(0, len(xx), self.batch_size):
                    ix = order[start:start+self.batch_size]
                    loss = -self.model.log_prob(yy[ix], context=xx[ix]).mean()
                    if not torch.isfinite(loss):
                        raise ValueError("nonfinite spline-flow likelihood")
                    optimizer.zero_grad(); loss.backward()
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), 5.); optimizer.step()
                    total += loss.item()*len(ix)
                self.losses.append(total/len(xx))
        finally:
            if self.deterministic:
                torch.use_deterministic_algorithms(old)
        self.model.eval()
        return self

    def curve(self, x):
        import numpy as np
        import torch
        from scipy.special import ndtri
        self._validate_x(x)
        context = torch.tensor(self.scaler.transform(self._features(x)), dtype=torch.float32,
                               device=self.device)
        p = np.asarray(self.probabilities)
        z = torch.tensor(np.tile(ndtri(p), len(x))[:, None], dtype=torch.float32,
                         device=self.device)
        repeated = context.repeat_interleave(len(p), dim=0)
        with torch.no_grad():
            q, _ = self.model._transform.inverse(z, context=repeated)
        q = q.cpu().numpy().reshape(len(x), len(p))*self.y_scale+self.y_mean
        q = np.maximum.accumulate(q, axis=1)
        values = np.column_stack([q[:, 0]-self.tail_width, q, q[:, -1]+self.tail_width])
        return GridCurve(values, [0., *self.probabilities, 1.])


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


class AdaptiveEmpiricalMLPBlendCDF(EmpiricalMLPBlendCDF):
    """Calibration-only logistic gate between frozen empirical and MLP CDFs.

    The bounded-grid objective gives each declared reporting cell equal weight.
    Gate imputation and scaling are learned from training features only.
    """

    consumes_calibration_labels = True

    def __init__(self, lower_weight, upper_weight, condition_indices, cell_indices,
                 reference_index, knots, gate_indices, grid_bounds, grid_points,
                 l2, max_iter, tolerance, mlp):
        import math
        if (isinstance(lower_weight, bool) or isinstance(upper_weight, bool)
                or not math.isfinite(lower_weight) or not math.isfinite(upper_weight)
                or not 0 <= lower_weight < upper_weight <= 1
                or not isinstance(cell_indices, (list, tuple)) or not cell_indices
                or any(type(v) is not int or v < 0 for v in cell_indices)
                or len(set(cell_indices)) != len(cell_indices)
                or not isinstance(gate_indices, (list, tuple)) or not gate_indices
                or any(type(v) is not int or v < 0 for v in gate_indices)
                or len(set(gate_indices)) != len(gate_indices)
                or not isinstance(grid_bounds, (list, tuple)) or len(grid_bounds) != 2
                or any(isinstance(v, bool) or not math.isfinite(v) for v in grid_bounds)
                or grid_bounds[0] >= grid_bounds[1]
                or type(grid_points) is not int or grid_points < 2
                or isinstance(l2, bool) or not math.isfinite(l2) or l2 < 0
                or type(max_iter) is not int or max_iter <= 0
                or isinstance(tolerance, bool) or not math.isfinite(tolerance)
                or tolerance <= 0):
            raise ValueError("invalid adaptive blend gate parameters")
        super().__init__(lower_weight, condition_indices, reference_index, knots, mlp)
        self.cell_indices = tuple(cell_indices)
        self.lower_weight, self.upper_weight = float(lower_weight), float(upper_weight)
        self.gate_indices = tuple(gate_indices)
        self.grid_bounds = tuple(float(v) for v in grid_bounds)
        self.grid_points = grid_points
        self.l2, self.max_iter, self.tolerance = float(l2), max_iter, float(tolerance)
        metadata = set(self.cell_indices) - set(self.condition_indices[1:])
        if (not metadata or metadata & (set(self.condition_indices) | {self.reference_index}
                                      | set(self.gate_indices))
                or self.mlp.feature_indices is None
                or metadata & set(self.mlp.feature_indices)):
            raise ValueError("cell metadata must be excluded from model and gate inputs")

    def _validate_x(self, x):
        import numpy as np
        super()._validate_x(x)
        x = np.asarray(x)
        if max((*self.gate_indices, *self.cell_indices)) >= x.shape[1]:
            raise ValueError("gate or cell indices outside input")
        if not np.isfinite(x[:, self.cell_indices]).all():
            raise ValueError("reporting cell metadata must be finite")
        if len(self.condition_indices) > 1:
            ids = x[:, self.condition_indices[1:]]
            if (not np.isfinite(ids).all() or not np.isin(ids, [0, 1]).all()
                    or not (ids.sum(1) == 1).all()):
                raise ValueError("index identities must be finite one-hot")

    def _gate_features(self, x):
        import numpy as np
        x = np.asarray(x, dtype=float)[:, self.gate_indices]
        if np.isinf(x).any():
            raise ValueError("infinite gate feature")
        filled = np.where(np.isnan(x), self.gate_fill, x)
        return (filled - self.gate_mean) / self.gate_scale

    def fit(self, x, y, cal_x, cal_y):
        """Fit frozen endpoints on training and the logistic gate on calibration."""
        import numpy as np
        from scipy.optimize import minimize
        from scipy.special import expit

        self._validate_x(x)
        self._validate_x(cal_x)
        cal_y = np.asarray(cal_y, dtype=float)
        if cal_y.shape != (len(cal_x),) or not np.isfinite(cal_y).all() or not len(cal_y):
            raise ValueError("invalid calibration labels")
        super().fit(x, y, cal_x, cal_y)
        raw = np.asarray(x, dtype=float)[:, self.gate_indices]
        if np.isinf(raw).any():
            raise ValueError("infinite gate feature")
        self.gate_fill = np.array([np.median(v[np.isfinite(v)]) if np.isfinite(v).any()
                                   else 0. for v in raw.T])
        training = np.where(np.isnan(raw), self.gate_fill, raw)
        self.gate_mean = training.mean(0)
        self.gate_scale = training.std(0)
        self.gate_scale[self.gate_scale == 0] = 1.
        features = self._gate_features(cal_x)
        nodes = np.linspace(*self.grid_bounds, self.grid_points)
        empirical = self.empirical.curve(cal_x).cdf(nodes)
        difference = self.mlp.curve(cal_x).cdf(nodes) - empirical
        target = (cal_y[:, None] <= nodes[None, :]).astype(float)
        _, cell = np.unique(np.asarray(cal_x)[:, self.cell_indices],
                            axis=0, return_inverse=True)
        counts = np.bincount(cell)
        row_weight = 1. / (len(counts) * counts[cell])
        self.calibration_row_weights = row_weight.copy()
        span = self.upper_weight - self.lower_weight

        def objective(parameters):
            sigmoid = expit(parameters[0] + features @ parameters[1:])
            weight = self.lower_weight + span * sigmoid
            residual = empirical + weight[:, None] * difference - target
            loss = np.dot(row_weight, np.mean(residual**2, axis=1))
            loss += self.l2 * np.dot(parameters[1:], parameters[1:])
            row_gradient = (2 * row_weight * np.mean(residual * difference, axis=1)
                            * span * sigmoid * (1 - sigmoid))
            gradient = np.r_[row_gradient.sum(), features.T @ row_gradient
                             + 2 * self.l2 * parameters[1:]]
            return loss, gradient

        result = minimize(objective, np.zeros(features.shape[1] + 1), jac=True,
                          method="L-BFGS-B", options={"maxiter": self.max_iter,
                                                        "ftol": self.tolerance})
        if (not result.success or not np.isfinite(result.x).all()
                or not np.isfinite(result.fun)):
            raise ValueError(f"adaptive blend gate failed: {result.message}")
        self.gate_parameters = result.x
        self.gate_result = {"success": bool(result.success), "iterations": int(result.nit),
                            "objective": float(result.fun)}
        return self

    def curve(self, x):
        """Return row-wise bounded convex forecasts from the fitted gate."""
        from scipy.special import expit
        self._validate_x(x)
        features = self._gate_features(x)
        weight = self.lower_weight + (self.upper_weight - self.lower_weight) * expit(
            self.gate_parameters[0] + features @ self.gate_parameters[1:])
        return ConvexCurve(self.empirical.curve(x), self.mlp.curve(x), weight)


class _SplitPlan(ABC):
    """How a study carves its bands: one subclass per config grammar.

    ``column`` names the score column that carries the split id. The study
    asks the plan for every split-shaped fact (ids, bands, the rows a split
    evaluates), so a new grammar is a subclass, never a branch in the study.
    """

    column = ""

    def __init__(self, config):
        self.date, self.end = config["date"], config["end"]

    @abstractmethod
    def ids(self):
        """Return the split ids one run fits, oldest first."""

    @abstractmethod
    def bands(self, frame, split, calendar):
        """Return the (fit, calibration, evaluation) rows of one split of ``frame``."""

    @abstractmethod
    def expected(self, frame, ids):
        """Return the rows the splits ``ids`` evaluate."""

    @abstractmethod
    def assigned(self, dates):
        """Return the split id each evaluation date belongs to."""

    @abstractmethod
    def stage(self, ids):
        """Return the config keys that make a study fit exactly the splits ``ids``."""

    def calendar(self, frame):
        """Return the run's date calendar, for grammars that count dates."""
        return None

    def lock(self, frame):
        """Refuse rows the grammar forbids reading; the default forbids none."""


class _YearPlan(_SplitPlan):
    """Calendar years: the evaluation year, the year before it as calibration."""

    column = "year"

    def __init__(self, config):
        super().__init__(config)
        self.years, self.development_end = config["years"], config["development_end"]

    def ids(self):
        return list(self.years)

    def bands(self, frame, split, calendar):
        return ChronologicalCDFStudy._split_validated(frame, split, self.date, self.end)

    def expected(self, frame, ids):
        return frame[frame[self.date].str[:4].astype(int).isin(ids)]

    def assigned(self, dates):
        return dates.str[:4].astype(int)

    def stage(self, ids):
        return {"years": ids}


class _FoldPlan(_SplitPlan):
    """Count-sized folds from a pinned ``rolling-origin-plan`` table (ADR-0223).

    Each fold evaluates on its validation dates, calibrates on the last
    ``cal_n`` calendar dates of its train window and fits on the earlier ones;
    labels are purged with ``kinds_split.label_reaches``. Warm-up rows whose
    label reaches ``scored_start`` are dropped (ADR-0222's seam), so selection
    never sees a label that settles inside scored evidence.
    """

    column = "fold"
    #: The fold roles ``rolling-origin-plan`` writes; a test pins them to its constants.
    ROLES = ("warmup", "scored")
    _SPEC_KEYS = {"path", "sha256", "holdout_start", "cal_n", "roles", "notes"}
    _EDGES = ("train_start", "train_end", "val_start", "val_end")

    def __init__(self, config):
        super().__init__(config)
        self.spec = spec = config["fold_table"]
        self._check_spec(spec)
        self.cal_n, self.holdout = spec["cal_n"], spec["holdout_start"]
        rows = self._read(spec)
        self._check_folds(rows)
        self.folds = {row["fold"]: row for row in rows}
        ids = sorted(self.folds)
        self.warmup_ids = [i for i in ids if self.folds[i]["role"] == "warmup"]
        self.scored_ids = [i for i in ids if self.folds[i]["role"] == "scored"]
        self.scored_start = self.folds[self.scored_ids[0]]["val_start"]
        self.development_end = max(self.warmup_ids, default=0)

    def _check_spec(self, spec):
        """Refuse a malformed ``fold_table`` block by name."""
        if (not isinstance(spec, dict) or set(spec)-self._SPEC_KEYS
                or (self._SPEC_KEYS-{"notes"})-set(spec)):
            raise ValueError("invalid fold_table: needs exactly path, sha256, "
                             "holdout_start, cal_n, roles (and optional notes)")
        sha, roles, problems = spec["sha256"], spec["roles"], []
        if not isinstance(spec["path"], str) or not spec["path"]:
            problems.append("path must be a nonempty string")
        if not (isinstance(sha, str) and len(sha) == 64
                and all(ch in "0123456789abcdef" for ch in sha)):
            problems.append("sha256 must be 64 lowercase hex digits")
        if date_problem(spec["holdout_start"]):
            problems.append("holdout_start must be an ISO date")
        if type(spec["cal_n"]) is not int or spec["cal_n"] < 1:
            problems.append("cal_n must be an integer >= 1")
        if (not isinstance(roles, list) or not roles or len(set(roles)) != len(roles)
                or not set(roles) <= set(self.ROLES)):
            problems.append(f"roles must be a nonempty list of unique roles from {self.ROLES}")
        if problems:
            raise ValueError("invalid fold_table: " + "; ".join(problems))

    @staticmethod
    def _read(spec):
        """Return the table's rows, refusing a file that is not the pinned one."""
        raw = Path(spec["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != spec["sha256"]:
            raise ValueError(f"fold table sha256 mismatch: {spec['path']} is not the pinned file")
        return [json.loads(line) for line in raw.decode().splitlines() if line]

    def _check_folds(self, rows):
        """Refuse rows that are not plan folds, reach the holdout or misorder the roles."""
        for row in rows:
            if (not isinstance(row, dict) or type(row.get("fold")) is not int
                    or row.get("role") not in self.ROLES
                    or any(date_problem(row.get(edge)) for edge in self._EDGES)
                    or not row["train_start"] <= row["train_end"] < row["val_start"]
                    <= row["val_end"]):
                raise ValueError(f"fold table row is not a rolling-origin-plan fold: {row!r}")
            if label_reaches(row["val_end"], self.holdout):
                raise ValueError(f"fold {row['fold']} validates through {row['val_end']}, on or "
                                 f"after holdout_start {self.holdout}")
        roles = [row["role"] for row in sorted(rows, key=lambda row: row["fold"])]
        if (len({row["fold"] for row in rows}) != len(rows) or "scored" not in roles
                or roles != sorted(roles, key=self.ROLES.index)):
            raise ValueError("fold table needs unique folds, a scored fold and every "
                             "warmup fold before it")

    def ids(self):
        return [i for i in sorted(self.folds) if self.folds[i]["role"] in self.spec["roles"]]

    def calendar(self, frame):
        return sorted(frame[self.date].unique())

    def _val(self, frame, fold):
        """Return a fold's evaluation rows, warm-up ones cut at the scored seam."""
        date = frame[self.date]
        rows = (date >= fold["val_start"]) & (date <= fold["val_end"])
        if fold["role"] == "warmup":
            rows &= ~label_reaches(frame[self.end], self.scored_start)
        return frame[rows]

    def bands(self, frame, split, calendar):
        fold = self.folds[split]
        days = [d for d in calendar if fold["train_start"] <= d <= fold["train_end"]]
        start = (days[-self.cal_n:] or [fold["train_start"]])[0]
        date, end = frame[self.date], frame[self.end]
        train = (date >= fold["train_start"]) & (date <= fold["train_end"])
        return (frame[train & (date < start) & ~label_reaches(end, start)],
                frame[train & (date >= start) & ~label_reaches(end, fold["val_start"])],
                self._val(frame, fold))

    def expected(self, frame, ids):
        import pandas as pd
        return pd.concat([self._val(frame, self.folds[i]) for i in ids])

    def assigned(self, dates):
        import pandas as pd
        result = pd.Series(0, index=dates.index)
        for number, fold in self.folds.items():
            result[(dates >= fold["val_start"]) & (dates <= fold["val_end"])] = number
        return result

    def stage(self, ids):
        roles = {self.folds[i]["role"] for i in ids}
        return {"fold_table": {**self.spec, "roles": [r for r in self.ROLES if r in roles]}}

    def lock(self, frame):
        for field in (self.date, self.end):
            late = label_reaches(frame[field], self.holdout)
            if late.any():
                raise ValueError(
                    f"{field} {frame.loc[late, field].min()} is on or after holdout_start "
                    f"{self.holdout}: the holdout is locked and is never read")


class ChronologicalCDFStudy:
    """JSON-driven paired research, with purged training/calibration/year splits.

    Parameters
    ----------
    config : dict
        Fields, features, named estimator imports, integration resolutions and
        output directory, and ONE split grammar: ``years`` with
        ``development_end`` (calendar years), or ``fold_table`` (``path``,
        ``sha256``, ``holdout_start``, ``cal_n``, ``roles``: the pinned
        ``rolling-origin-plan`` folds of ADR-0223, whose ``roles`` pick the
        folds this run fits). Unknown top-level keys refuse.

    Examples
    --------
    Run on an already constructed, provenance-pinned panel::

        study = ChronologicalCDFStudy(config)
        result = study.run(panel)
    """

    KEYS = {"features", "group", "date", "end", "horizon", "target", "reference",
            "models", "years", "output", "samples", "tail_intervals", "tail_points",
            "calibration_knots", "development_end", "bootstrap", "reference_model",
            "comparison_references", "identity", "series_identity", "tail_probabilities",
            "decision_context", "frozen_variants", "promotion_guard", "decision_acceptance",
            "wing_metrics", "fold_table", "notes"}

    def __init__(self, config):
        unknown = set(config)-self.KEYS
        optional = {"notes", "tail_probabilities", "decision_context",
                    "frozen_variants", "promotion_guard", "decision_acceptance",
                    "wing_metrics", "fold_table"}
        folds = "fold_table" in config
        year_keys = {"years", "development_end"}
        missing = self.KEYS-optional-set(config)-(year_keys if folds else set())
        if unknown or missing:
            raise ValueError(f"unknown keys {unknown}; missing keys {missing}")
        if folds and year_keys & set(config):
            raise ValueError(f"fold_table replaces {sorted(year_keys & set(config))}: "
                             "declare exactly one of years or fold_table")
        if "wing_metrics" in config and (type(config["wing_metrics"]) is not bool
                                         or (config["wing_metrics"]
                                             and not config.get("decision_context"))):
            raise ValueError("wing metrics must be a bool and need a decision context")
        for spec in config["models"].values():
            if (set(spec)-{"class", "params", "calibrate", "pooled", "equivalence", "notes"}
                    or not {"class", "params", "calibrate"}.issubset(spec)
                    or type(spec.get("pooled", False)) is not bool
                    or ("equivalence" in spec and (not isinstance(spec["equivalence"], str)
                                                   or not spec["equivalence"]))):
                raise ValueError("invalid model specification keys or pooled flag")
            if spec["calibrate"]:
                module, cls = spec["class"].split(":")
                estimator = getattr(importlib.import_module(module), cls)
                if (getattr(estimator, "consumes_calibration_labels", False)
                        or spec["params"].get("patience") is not None):
                    raise ValueError("estimator consumes calibration labels; second map refused")
        frozen = config.get("frozen_variants")
        if (frozen is not None
                and (set(frozen) != set(config["models"])
                     or any(value not in ("raw", "calibrated")
                            for value in frozen.values()))):
            raise ValueError("invalid frozen variants")
        guard = config.get("promotion_guard")
        guard_keys = {"reference", "candidate", "local_metric", "blocks",
                      "min_lower_bound", "noninferiority_pct", "coverage_targets",
                      "max_coverage_deviation_increase", "authority"}
        if guard is not None:
            import math
            if (set(guard) != guard_keys
                    or guard["reference"] not in config["models"]
                    or guard["candidate"] not in config["models"]
                    or guard["reference"] == guard["candidate"]
                    or guard["local_metric"] != "decision_strike_brier"
                    or not isinstance(guard["blocks"], list) or not guard["blocks"]
                    or any(type(v) is not int or v not in config["bootstrap"]["blocks"]
                           for v in guard["blocks"])
                    or len(set(guard["blocks"])) != len(guard["blocks"])
                    or isinstance(guard["min_lower_bound"], bool)
                    or not math.isfinite(guard["min_lower_bound"])
                    or set(guard["noninferiority_pct"]) != {
                        "crps", "tail_crps", "lower_tail_quantile_score",
                        "upper_tail_quantile_score"}
                    or any(isinstance(v, bool) or not math.isfinite(v) or v < 0
                           for v in guard["noninferiority_pct"].values())
                    or guard["coverage_targets"] != {"below_05": .05, "above_95": .05}
                    or isinstance(guard["max_coverage_deviation_increase"], bool)
                    or not math.isfinite(guard["max_coverage_deviation_increase"])
                    or guard["max_coverage_deviation_increase"] < 0
                    or guard["authority"] != "descriptive_only"):
                raise ValueError("invalid descriptive promotion guard")
        acceptance = config.get("decision_acceptance")
        if acceptance is not None:
            import math
            if (set(acceptance) != {"blocks", "min_lower_bound", "max_bias_increase"}
                    or not config.get("decision_context")
                    or config.get("promotion_guard") is not None
                    or not acceptance["blocks"]
                    or not set(acceptance["blocks"]).issubset(config["bootstrap"]["blocks"])
                    or any(type(acceptance[k]) not in (int, float)
                           or not math.isfinite(acceptance[k])
                           for k in ("min_lower_bound", "max_bias_increase"))
                    or acceptance["max_bias_increase"] < 0):
                raise ValueError("invalid decision-only acceptance guard")
        self.config = config
        self.plan = (_FoldPlan if folds else _YearPlan)(config)

    def to_obj(self):
        """Return the JSON configuration for the canonical identity owner."""
        return self.config

    def _decision_context_rows(self, frame):
        """Return context only after binding every record to its frame identity."""
        field = self.config.get("decision_context")
        if not field or field not in frame:
            raise ValueError("decision-aware model lacks frozen context")
        context = frame[field].tolist()
        expected = [tuple(row) for row in frame[self.config["identity"]].itertuples(
            index=False, name=None)]
        if len(context) != len(expected) or any(
                not isinstance(record, dict)
                or tuple(record.get("identity", ())) != identity
                for record, identity in zip(context, expected)):
            raise ValueError("decision context and forecast identity mismatch")
        return context

    def _validate_decision_scores(self, scores):
        import numpy as np
        if "decision_strike_brier" not in scores:
            if self.config.get("decision_context"):
                raise ValueError("decision scores missing")
            return
        keys = self.config["identity"]
        owners = DecisionRegionScores.METRIC_COUNTS
        counted = [metric for metric in owners if metric in scores]
        columns = sorted({owners[metric] for metric in counted})
        reference = None
        for _, rows in scores.groupby(["model", "variant"]):
            if rows.duplicated(keys).any():
                raise ValueError("duplicate decision score identity")
            rows = rows.sort_values(keys).reset_index(drop=True)
            if any(column not in rows for column in columns):
                raise ValueError("decision observation counts missing")
            for column in columns:
                counts = rows[column]
                if (not np.isfinite(counts).all() or (counts < 0).any()
                        or (counts != counts.astype(int)).any()):
                    raise ValueError("invalid decision counts")
            for metric in counted:
                eligible, values = rows[owners[metric]] > 0, rows[metric]
                if (not np.isfinite(values[eligible]).all()
                        or not values[~eligible].isna().all()):
                    raise ValueError("decision score eligibility mismatch")
            shape = rows[keys + columns]
            if reference is not None and not shape.equals(reference):
                raise ValueError("unpaired decision eligibility or identities")
            reference = shape

    def _equal_cell_skill(self, band, candidate, reference):
        """Return percent skill of per-row scores, equal-weighting group/horizon cells."""
        c = self.config
        paired = band[[c["group"], c["horizon"]]].copy()
        paired["candidate"], paired["reference"] = candidate, reference
        means = paired.groupby([c["group"], c["horizon"]])[["candidate", "reference"]].mean().dropna()
        return (float(100*(1-(means.candidate/means.reference).mean()))
                if len(means) and (means.reference > 0).all() else None)

    def _loss_telemetry(self, model, imputer, baseline, baseline_imputer, fit, cal, val):
        import numpy as np
        c = self.config
        result = {}
        for label, band in (("training", fit), ("calibration", cal), ("validation", val)):
            bx = imputer.transform(band[c["features"]])
            by = (band[c["target"]]/band[c["reference"]]).to_numpy()
            context = self._decision_context_rows(band)
            report = model.loss_report(bx, by, context)
            scorer = DecisionRegionScores(context)
            curve = model.curve(bx)
            baseline_curve = baseline.curve(baseline_imputer.transform(band[c["features"]]))
            candidate = scorer.score(curve, by)["decision_strike_brier"]
            reference = scorer.score(baseline_curve, by)["decision_strike_brier"]
            report["decision_skill_pct"] = self._equal_cell_skill(band, candidate, reference)
            report["decision_brier"] = float(np.nanmean(candidate)) if report["eligible_n"] else None
            report["reference_decision_brier"] = float(np.nanmean(reference)) if report["eligible_n"] else None
            if c.get("wing_metrics"):
                key = DecisionRegionScores.WING_METRICS[0]
                wing = scorer.wing_score(curve, by)[key]
                wing_reference = scorer.wing_score(baseline_curve, by)[key]
                report["wing_twcrps"] = float(np.nanmean(wing)) if report["eligible_n"] else None
                report["reference_wing_twcrps"] = (
                    float(np.nanmean(wing_reference)) if report["eligible_n"] else None)
                report["wing_twcrps_skill_pct"] = self._equal_cell_skill(
                    band, wing, wing_reference)
            report["scope"] = "reported_group_slice"
            report["dates"] = int(band[c["date"]].nunique())
            report["eligible_dates"] = int(band.loc[np.isfinite(candidate), c["date"]].nunique())
            report["excluded_n"] = report["n"]-report["eligible_n"]
            result[label] = report
        return result

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
        if c.get("decision_acceptance"):
            self._validate_decision_scores(scores)
        output = Path(c["output"])
        cells = [c["group"], c["horizon"]]
        split = self.plan.column
        dev = scores[scores[split] <= self.plan.development_end]
        later = scores[scores[split] > self.plan.development_end]
        selection_metric = ("decision_strike_brier"
                            if "decision_strike_brier" in dev else "crps")
        dev_means = dev.groupby(cells+["model", "variant"])[selection_metric].mean().unstack(["model", "variant"])
        # Equal-cell relative score uses a fixed raw horizon-empirical reference.
        baseline = c["reference_model"]
        base = dev_means[(baseline, "raw")]
        dev_rel = dev_means.div(base, axis=0).mean()
        crps_means = dev.groupby(cells+["model", "variant"])["crps"].mean().unstack(
            ["model", "variant"])
        dev_crps_rel = crps_means.div(
            crps_means[(baseline, "raw")], axis=0).mean()
        selected = {name: min(("raw", "calibrated"), key=lambda v: dev_rel[(name, v)])
                    for name in c["models"]}
        selected[baseline] = "raw"
        if selected_variants is None and c.get("frozen_variants") is not None:
            selected_variants = c["frozen_variants"]
        if selected_variants is not None:
            if set(selected_variants) != set(c["models"]) or any(v not in ("raw", "calibrated") for v in selected_variants.values()):
                raise ValueError("invalid frozen variants")
            selected = selected_variants
        picked = pd.concat([later[(later.model == name) & (later.variant == variant)]
                            for name, variant in selected.items()])
        metrics = ["crps", "tail_crps", "lower_tail_quantile_score",
                   "upper_tail_quantile_score", "raw_return_crps", "strike_brier", "condor_loss_mse",
                   "condor_loss_bias", "below_05", "above_95", "payoff_quadrature_gap"]
        if "decision_strike_brier" in scores:
            metrics.insert(0, "decision_strike_brier")
            if "decision_bias" in scores:
                metrics.append("decision_bias")
            metrics[1:1] = [m for m in DecisionRegionScores.WING_METRICS if m in scores]
        metrics.extend(name for name in scores.columns
                       if (name.startswith("below_") or name.startswith("above_"))
                       and name not in metrics)
        metrics = [m for m in metrics if m in scores]
        records, grids, yearly_grids = [], [], []
        keys = c["identity"]
        for metric in metrics:
            mean = picked.groupby(cells+["model"])[metric].mean().unstack("model")
            ratio = mean.div(mean[baseline], axis=0)
            if metric not in ("decision_bias", "condor_loss_bias", "below_05", "above_95", "payoff_quadrature_gap"):
                for model in c["models"]:
                    r = (100*(1-ratio[model])).rename("skill").reset_index()
                    r["metric"], r["model"] = metric, model
                    n = picked[picked.model == model].dropna(subset=[metric]).groupby(cells).size().rename("n").reset_index()
                    grids.append(r.merge(n, on=cells, validate="one_to_one"))
            for model in c["models"]:
                f = picked[picked.model == model]
                total_n = len(f)
                f = f.dropna(subset=[metric])
                records.append({"model": model, "variant": selected[model], "metric": metric,
                                "n": len(f), "total_n": total_n, "excluded_n": total_n-len(f),
                                "threshold_n": (int(f[DecisionRegionScores.METRIC_COUNTS[metric]].sum())
                                                if metric in DecisionRegionScores.METRIC_COUNTS
                                                and DecisionRegionScores.METRIC_COUNTS[metric] in f
                                                else None),
                                "dates": f[c["date"]].nunique(),
                                "expiry_series": f[c["series_identity"]].drop_duplicates().shape[0],
                                "mean": float(f[metric].mean()),
                                "equal_cell_skill": (float(100*(1-ratio[model].mean()))
                    if metric in (*DecisionRegionScores.PROPER_METRICS, "crps", "tail_crps",
                                  "raw_return_crps", "strike_brier", "condor_loss_mse")
                                                     and np.isfinite(ratio[model].mean()) else None)})
            if metric not in ("decision_bias", "condor_loss_bias", "below_05", "above_95",
                              "payoff_quadrature_gap"):
                annual = picked.groupby([c["group"], split, "model"])[metric].mean().unstack("model")
                annual_ratio = annual.div(annual[baseline], axis=0)
                for model in c["models"]:
                    row = (100*(1-annual_ratio[model])).rename("skill").reset_index()
                    row["metric"], row["model"] = metric, model
                    n = (picked[picked.model == model].dropna(subset=[metric])
                         .groupby([c["group"], split]).size().rename("n").reset_index())
                    yearly_grids.append(row.merge(n, on=[c["group"], split],
                                                  validate="one_to_one"))
        intervals = []
        # Same entry date carries ALL indexes/horizons together. Sum within cell;
        # paired denominators cancel in each cell's candidate/reference ratio.
        interval_metrics = ["crps"]+[m for m in DecisionRegionScores.PROPER_METRICS
                                     if m in picked]
        for interval_metric in interval_metrics:
          for reference in c["comparison_references"]:
            base_rows = picked[picked.model == reference].set_index(keys)[interval_metric].dropna()
            for model in c["models"]:
                model_rows = picked[picked.model == model].set_index(keys)
                compare = model_rows[interval_metric].dropna()
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
                        supported = denom > 0
                        if not supported.any():
                            raise ValueError("bootstrap cell lacks observations")
                        # A date-block resample can legitimately omit every row
                        # from a sparse exact-day cell. Recompute the statistic
                        # on represented cells, as groupby does on the sample;
                        # structural absence is not a zero-valued observation.
                        replicates.append(100*(1-np.mean(
                            a[idx].sum(0)[supported]/denom[supported])))
                    lo, hi = np.quantile(replicates, [.025, .975])
                    intervals.append({"metric": interval_metric, "model": model, "reference": reference, "block_dates": width,
                                      "n": len(pair), "dates": len(a),
                                      "lo": float(lo), "hi": float(hi),
                                      "point": float(100*(1-np.mean(a.sum(0)/b.sum(0))))})
        guard_result = None
        if c.get("promotion_guard") is not None:
            guard = c["promotion_guard"]
            candidate, reference = guard["candidate"], guard["reference"]
            local_checks = []
            for width in guard["blocks"]:
                matches = [row for row in intervals
                           if row["metric"] == guard["local_metric"]
                           and row["model"] == candidate
                           and row["reference"] == reference
                           and row["block_dates"] == width]
                if len(matches) != 1:
                    raise ValueError("promotion guard lacks unique local interval")
                row = matches[0]
                local_checks.append({"block_dates": width, "lo": row["lo"],
                                     "threshold": guard["min_lower_bound"],
                                     "passed": row["lo"] > guard["min_lower_bound"]})
            noninferiority = []
            for metric, margin in guard["noninferiority_pct"].items():
                mean = picked.groupby(cells+["model"])[metric].mean().unstack("model")
                skill = float(100*(1-(mean[candidate]/mean[reference]).mean()))
                noninferiority.append({"metric": metric, "skill": skill,
                                       "minimum": -float(margin),
                                       "passed": skill >= -float(margin)})
            coverage = []
            for metric, target in guard["coverage_targets"].items():
                mean = picked.groupby(cells+["model"])[metric].mean().unstack("model")
                candidate_deviation = float((mean[candidate]-target).abs().mean())
                reference_deviation = float((mean[reference]-target).abs().mean())
                increase = candidate_deviation-reference_deviation
                coverage.append({"metric": metric,
                                 "candidate_abs_deviation": candidate_deviation,
                                 "reference_abs_deviation": reference_deviation,
                                 "increase": increase,
                                 "maximum": guard["max_coverage_deviation_increase"],
                                 "passed": increase <= guard["max_coverage_deviation_increase"]})
            passed = all(row["passed"] for row in
                         [*local_checks, *noninferiority, *coverage])
            guard_result = {"candidate": candidate, "reference": reference,
                            "authority": guard["authority"], "passed": passed,
                            "local_intervals": local_checks,
                            "noninferiority": noninferiority,
                            "coverage": coverage}
        decision_acceptance = {}
        if c.get("decision_acceptance"):
            rule = c["decision_acceptance"]
            means = picked.groupby(cells+["model"]).decision_bias.mean().unstack("model")
            for model in c["models"]:
                if model == baseline:
                    continue
                local = [row for row in intervals if row["metric"] == "decision_strike_brier"
                         and row["model"] == model and row["reference"] == baseline
                         and row["block_dates"] in rule["blocks"]]
                if len(local) != len(rule["blocks"]) or means[[model, baseline]].isna().any().any():
                    raise ValueError("incomplete decision acceptance evidence")
                increase = float(means[model].abs().mean()-means[baseline].abs().mean())
                decision_acceptance[model] = {
                    "passed": bool(all(row["lo"] > rule["min_lower_bound"] for row in local)
                                   and increase <= rule["max_bias_increase"]),
                    "bias_abs_increase": increase, "intervals": local,
                    "authority": "descriptive_only", "rule": rule}
        summary = {"decision_acceptance": decision_acceptance,
                   "selected_variants_from_development": selected,
                   "selection_metric": selection_metric,
                   "promotion_status": ("descriptive_only_no_promotion" if guard_result is None
                                        else ("descriptive_guard_pass_no_promotion"
                                              if guard_result["passed"] else
                                              "descriptive_guard_failed_no_promotion")),
                   "promotion_reason": ("global and tail noninferiority are reported but not a preregistered gate"
                                        if guard_result is None else
                                        "historical inspected data have descriptive authority only"),
                   "promotion_guard": guard_result,
                   "decision_eligible_rows_by_model": (
                       picked.dropna(subset=["decision_strike_brier"])
                       .groupby("model").size().to_dict()
                       if "decision_strike_brier" in picked else {}),
                   "development_relative_primary": {f"{k[0]}:{k[1]}": float(v) for k, v in dev_rel.items()},
                   "development_relative_crps": {f"{k[0]}:{k[1]}": float(v)
                                                 for k, v in dev_crps_rel.items()},
                   "metrics": records, "paired_block_intervals": intervals,
                   "limits": "Pointwise research intervals, not multiplicity-adjusted selection guarantees; historical evaluation already inspected."}
        (output/"comparison.json").write_text(json.dumps(summary, indent=2, allow_nan=False))
        pd.concat(grids, ignore_index=True).to_csv(output/"skill_by_exact_day.csv", index=False)
        pd.concat(yearly_grids, ignore_index=True).to_csv(
            output/f"skill_by_index_{split}.csv", index=False)
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
    def scores(curve, y, samples, intervals, points, tail_probabilities=None):
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
        residual = y[:, None]-q
        pinball = (p[None, :]-(residual < 0))*residual
        lower = p <= .1
        upper = p >= .9
        pit = curve.cdf(y[:, None])[:, 0]
        result = {"crps": crps, "tail_crps": tail, "pit": pit,
                  "lower_tail_quantile_score": 2*np.mean(pinball[:, lower], axis=1),
                  "upper_tail_quantile_score": 2*np.mean(pinball[:, upper], axis=1)}
        levels = [.05, .95] if tail_probabilities is None else tail_probabilities
        names = {.01: "01", .025: "025", .05: "05", .1: "10",
                 .9: "90", .95: "95", .975: "975", .99: "99"}
        if (not isinstance(levels, (list, tuple)) or not levels
                or any(level not in names for level in levels)):
            raise ValueError("unsupported tail probability")
        for level in levels:
            prefix = "below" if level < .5 else "above"
            result[f"{prefix}_{names[level]}"] = ((pit < level) if level < .5
                                                   else (pit > level)).astype(float)
        return result, q

    def run(self, frame, diagnostic=None):
        """Fit paired splits (years or fold-table folds); retain scores, counts, curves.

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
            For missing/empty bands or invalid labels/reference scales, or, with a
            fold table, a row dated or settling on or after ``holdout_start``.
        FileExistsError
            If the output directory exists; no research evidence is overwritten.
        """
        import numpy as np
        import pandas as pd
        from sklearn.impute import SimpleImputer

        c = self.config
        _validate_temporal_frame(frame, c["date"], c["end"])
        self.plan.lock(frame)
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
        if "CatBoostQuantileCDF" in classes:
            distributions.append("catboost")
        if "SplineFlowCDF" in classes:
            distributions.append("nflows")
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
        calendar = self.plan.calendar(frame)
        for group, group_frame in frame.groupby(c["group"]):
            for split in self.plan.ids():
                fit, cal, val = self.plan.bands(group_frame, split, calendar)
                if min(len(fit), len(cal), len(val)) < 1:
                    raise ValueError(f"empty band: {group} {split}")
                if c.get("wing_metrics"):
                    # Refuse malformed wing intervals before any model is fitted.
                    for band in (fit, cal, val):
                        DecisionRegionScores(self._decision_context_rows(band)).segment_arrays()
                count = {"group": group, self.plan.column: split}
                for name, band in [("fit", fit), ("cal", cal), ("val", val)]:
                    count[name] = {"n": len(band), "dates": band[c["date"]].nunique(),
                                   "ends": band[c["end"]].nunique(),
                                   "expiry_series": band[c["series_identity"]].drop_duplicates().shape[0],
                                   "first": band[c["date"]].min(), "last": band[c["date"]].max(),
                                   "latest_label": band[c["end"]].max()}
                yc, yv = [(b[c["target"]]/b[c["reference"]]).to_numpy() for b in (cal, val)]
                model_items = sorted(c["models"].items(),
                                     key=lambda item: item[0] != c["reference_model"])
                for name, spec in model_items:
                    start = time.monotonic()
                    key = (split, name, json.dumps(spec, sort_keys=True))
                    pooled = spec.get("pooled", False)
                    new_fit = not (pooled and key in pooled_cache)
                    if new_fit:
                        model_fit, model_cal, _ = self.plan.bands(
                            frame, split, calendar) if pooled else (fit, cal, val)
                        module, cls = spec["class"].split(":")
                        model = getattr(importlib.import_module(module), cls)(**spec["params"])
                        for band in (model_fit, model_cal, val):
                            model._validate_x(band[c["features"]].to_numpy())
                        imputer = SimpleImputer(strategy="median", keep_empty_features=True)
                        x = imputer.fit_transform(model_fit[c["features"]])
                        if hasattr(model, "fit_decision_context"):
                            model.fit_decision_context(
                                self._decision_context_rows(model_fit),
                                self._decision_context_rows(model_cal))
                        model.fit(x, (model_fit[c["target"]]/model_fit[c["reference"]]).to_numpy(),
                                  imputer.transform(model_cal[c["features"]]),
                                  (model_cal[c["target"]]/model_cal[c["reference"]]).to_numpy())
                        if hasattr(model, "fit_context"):
                            model.fit_context(model_cal, date_field=c["date"], end_field=c["end"])
                        label = spec.get("equivalence")
                        if label:
                            if not hasattr(model, "_equivalence_state"):
                                raise ValueError("equivalence model does not expose fitted state")
                            state = model._equivalence_state()
                            population = "pooled" if pooled else str(group)
                            equivalence_key = (int(split), population, label)
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
                    if hasattr(model, "curve_decision_context"):
                        raw = model.curve_decision_context(
                            xv, self._decision_context_rows(val))
                    else:
                        raw = (model.curve_context(
                        xv, val[c["date"]].to_numpy(), val[c["end"]].to_numpy(), yv)
                        if hasattr(model, "curve_context") else model.curve(xv))
                    variants = {"raw": raw}
                    if spec["calibrate"]:
                        calibration_curve = (model.curve_decision_context(
                            xc, self._decision_context_rows(cal))
                            if hasattr(model, "curve_decision_context")
                            else model.curve(xc))
                        pit = calibration_curve.cdf(yc[:, None])[:, 0]
                        variants["calibrated"] = CalibratedCurve(raw, pit, c["calibration_knots"])
                    else:
                        variants["calibrated"] = raw
                    for variant, curve in variants.items():
                        scored, draws = self.scores(
                            curve, yv, c["samples"], c["tail_intervals"], c["tail_points"],
                            c.get("tail_probabilities"))
                        columns = list(dict.fromkeys([*c["identity"], *c["series_identity"],
                                                      c["group"], c["date"], c["end"], c["horizon"]]))
                        part = val[columns].copy()
                        part[self.plan.column], part["model"], part["variant"] = split, name, variant
                        for metric, values in scored.items():
                            part[metric] = values
                        part["raw_return_crps"] = part.crps.to_numpy()*val[c["reference"]].to_numpy()
                        if diagnostic:
                            for metric, values in diagnostic(val, curve, draws).items():
                                part[metric] = values
                        if c.get("decision_context"):
                            scorer = DecisionRegionScores(self._decision_context_rows(val))
                            for metric, values in scorer.score(curve, yv).items():
                                part[metric] = values
                            if c.get("wing_metrics"):
                                for metric, values in scorer.wing_score(curve, yv).items():
                                    part[metric] = values
                        results.append(part)
                        # Retain both exact grids/maps/mixtures and quadrature draws.
                        # This is numerical research evidence, not a serving artifact.
                        if split == max(self.plan.ids()):
                            np.savez_compressed(output/f"{group}-{name}-{variant}-curves.npz",
                                                draws=draws.astype("float32"),
                                                reference=val[c["reference"]].to_numpy(),
                                                identities=val[c["identity"]].astype(str).to_numpy(dtype=str),
                                                row_index=val.index.to_numpy(), **curve._arrays())
                    count[name+"_seconds"] = time.monotonic()-start
                    if hasattr(model, "_research_state"):
                        count[name+"_research_state"] = model._research_state()
                    if name == c["reference_model"]:
                        reference_model, reference_imputer = model, imputer
                    if isinstance(model, TorchCDF):
                        count[name+"_losses"] = self._loss_telemetry(
                            model, imputer, reference_model, reference_imputer, fit, cal, val)
                    elif isinstance(model, MixtureMLPCDF):
                        count[name+"_training_nll_by_seed"] = [loss[-1] for loss in model.losses]
                    print(group, split, name, round(count[name+"_seconds"], 2), flush=True)
                counts.append(count)
                pd.concat(results, ignore_index=True).to_parquet(output/"scores.parquet", index=False)
                (output/"counts.json").write_text(json.dumps(counts, indent=2, default=int))
        evidence = [{self.plan.column: split, "population": population, "label": label,
                     "digest": record["digest"],
                     "members": sorted(set(record["members"])),
                     "status": ("verified" if len(set(record["members"])) >= 2 else "unverified")}
                    for (split, population, label), record in sorted(equivalence.items())]
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
    #: The year-grammar keys a fold table derives (ADR-0223), refused beside one.
    _YEAR_KEYS = {"development_years", "label_cutoff", "evaluation_partitions"}

    def __init__(self, config):
        import copy
        import itertools
        from dskit.pipeline.kinds_search import CandidateInventory

        self.config = copy.deepcopy(config)
        if set(config)-{"study", "experiment", "data", "diagnostic", "notes"}:
            raise ValueError("unknown HPO document keys")
        self.base, self.experiment = self.config["study"], self.config["experiment"]
        c, e = self.base, self.experiment
        self.plan = ChronologicalCDFStudy(c).plan
        folds = "fold_table" in c
        if folds and self._YEAR_KEYS & set(e):
            raise ValueError(f"{sorted(self._YEAR_KEYS & set(e))} are derived from the fold table")
        self.grouped = "candidate_groups" in e
        required = ((self._COMMON_KEYS - (self._YEAR_KEYS if folds else set()))
                    | ({"candidate_groups"} if self.grouped else
                       {"final_seeds", "screen_seed", "candidate_labels", "axes"}))
        if set(e)-required-{"notes", "public_probes", "selection_guard", "selection_metric"} or required-set(e):
            raise ValueError("unknown or missing experiment keys")
        metric = e.get("selection_metric", "crps")
        proper = DecisionRegionScores.PROPER_METRICS
        if (metric not in ("crps", *proper)
                or (metric in proper and not c.get("decision_context"))
                or (metric in DecisionRegionScores.WING_METRICS and not c.get("wing_metrics"))):
            raise ValueError("invalid primary selection metric")
        guard = e.get("selection_guard")
        if metric in proper and guard is not None:
            guarded = [*guard.get("metrics", []), *guard.get("cell_improvement_metrics", []),
                       *guard.get("cell_noninferiority_metrics", [])]
            if any(name not in DecisionRegionScores.METRIC_COUNTS for name in guarded):
                raise ValueError("decision selection guards must apply at decision regions")

        if guard is not None:
            import math
            guard_keys = {"reference", "variant", "metrics", "target", "tolerance"}
            optional_guard_keys = {"cell_improvement_metrics", "cell_noninferiority_metrics"}
            metric_lists = ([guard.get(name, []) for name in optional_guard_keys]
                            if isinstance(guard, dict) else [None])
            if (not isinstance(guard, dict) or not guard_keys.issubset(guard)
                    or set(guard)-guard_keys-optional_guard_keys
                    or guard["reference"] not in c["models"]
                    or guard["variant"] not in ("raw", "calibrated")
                    or not isinstance(guard["metrics"], list) or not guard["metrics"]
                    or len(set(guard["metrics"])) != len(guard["metrics"])
                    or any(not isinstance(metric, str) or not metric for metric in guard["metrics"])
                    or any(not isinstance(values, list)
                           or len(set(values)) != len(values)
                           or any(not isinstance(metric, str) or not metric for metric in values)
                           for values in metric_lists)
                    or isinstance(guard["target"], bool) or not math.isfinite(guard["target"])
                    or isinstance(guard["tolerance"], bool)
                    or not math.isfinite(guard["tolerance"]) or guard["tolerance"] < 0):
                raise ValueError("invalid development selection guard")
        probes = e.get("public_probes", [])
        probe_keys = {"name", "checkpoint", "training_cutoff", "status", "reason"}
        if (not isinstance(probes, list) or len({p.get("name") for p in probes}) != len(probes)
                or any(set(p) != probe_keys or p["status"] != "descriptive_only"
                       or p["training_cutoff"] is not None or not p["reason"]
                       for p in probes)):
            raise ValueError("invalid public probe inventory")
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
        if folds:
            plan = self.plan
            if not plan.warmup_ids or sorted(c["fold_table"]["roles"]) != sorted(plan.ROLES):
                raise ValueError("selection needs warmup folds and a fold_table naming both roles")
            self.development, self.later, self.cutoff = (
                plan.warmup_ids, plan.scored_ids, plan.scored_start)
            self.partitions = {"development": self.development, "later": self.later}
        else:
            dev = e["development_years"]
            years = [y for values in e["evaluation_partitions"].values() for y in values]
            if (not dev or len(set(dev)) != len(dev) or any(type(y) is not int for y in years+dev)
                    or max(dev) != c["development_end"] or e["label_cutoff"] != f"{max(dev)+1}-01-01"
                    or e["evaluation_partitions"].get("development") != dev
                    or len(years) != len(set(years)) or set(years) != set(c["years"])):
                raise ValueError("development cutoff or evaluation year partition mismatch")
            self.development, self.cutoff = dev, e["label_cutoff"]
            self.later = [y for y in c["years"] if y > c["development_end"]]
            self.partitions = e["evaluation_partitions"]
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
        if "CatBoostQuantileCDF" in classes:
            names.append("catboost")
        if "SplineFlowCDF" in classes:
            names.append("nflows")
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
        field = self.base.get("decision_context")
        if field and field in f:
            f = f.copy()
            f[field] = f[field].map(lambda value: json.dumps(value, sort_keys=True, allow_nan=False))

        return hashlib.sha256(schema.encode()+pd.util.hash_pandas_object(f, index=False).values.tobytes()).hexdigest()

    @staticmethod
    def _write(path, value):
        from dskit.pipeline.node import atomic_write
        atomic_write(str(path), json.dumps(value, indent=2, allow_nan=False).encode())

    def _complete(self, path, identity, stage, partition, **extra):
        import resource
        files = {str(p.relative_to(path)): self._file_hash(p) for p in sorted(path.rglob("*")) if p.is_file()}
        self._write(path/"complete.json", {"identity": identity, "stage": stage, "partition": partition,
                                           "files": files,
                                           "resource": {
                                               "peak_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
                                               "stage_wall_seconds": time.monotonic()-self._stage_started,
                                           }, **extra})

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

    def _expected(self, frame, ids):
        return self.plan.expected(frame, ids)

    def _check_scores(self, scores, expected, models):
        import numpy as np
        c = self.base
        columns = list(dict.fromkeys([*c["identity"], c["group"], c["date"], c["end"], c["horizon"]]))
        want = expected[columns].sort_values(c["identity"]).reset_index(drop=True)
        if set(scores.model) != set(models) or set(scores.variant) != {"raw", "calibrated"}:
            raise ValueError("score model or variant inventory mismatch")
        if scores.duplicated([*c["identity"], "model", "variant"]).any():
            raise ValueError("duplicate score identity")
        decision_selection = (self.experiment.get("selection_metric")
                              in DecisionRegionScores.PROPER_METRICS)
        if decision_selection:
            ChronologicalCDFStudy(c)._validate_decision_scores(scores)
        numeric = scores.select_dtypes(include="number")
        if decision_selection:
            numeric = numeric.drop(columns=list(DecisionRegionScores.METRIC_COUNTS),
                                   errors="ignore")

        if not np.isfinite(numeric.to_numpy()).all():
            raise ValueError("nonfinite score; cells cannot be silently dropped")
        for name in models:
            for variant in ("raw", "calibrated"):
                rows = scores[(scores.model == name) & (scores.variant == variant)]
                got = rows[columns].sort_values(c["identity"]).reset_index(drop=True)
                if (not got.equals(want)
                        or not (rows[self.plan.column] == self.plan.assigned(rows[c["date"]])).all()):
                    raise ValueError("unpaired or substituted score identities/years")

    def _rank(self, scores):
        c = self.base
        metric = self.experiment.get("selection_metric", "crps")
        means = scores.groupby([c["group"], c["horizon"], "model", "variant"])[metric].mean().unstack(["model", "variant"])
        if means.isna().any().any() or (means[(c["reference_model"], "raw")] <= 0).any():
            raise ValueError("incomplete cells or nonpositive reference primary score")
        return means.div(means[(c["reference_model"], "raw")], axis=0).mean()

    def _guard(self, scores, candidates):
        """Return feasible model/variants and complete development guard evidence."""
        import numpy as np

        guard = self.experiment.get("selection_guard")
        if guard is None:
            return candidates, []
        c = self.base
        reference = scores[(scores.model == guard["reference"])
                           & (scores.variant == guard["variant"])]
        evidence, feasible = [], []
        for name, variant in candidates:
            candidate = scores[(scores.model == name) & (scores.variant == variant)]
            passed = True
            comparisons = []
            for metric in guard["metrics"]:
                if metric not in scores:
                    raise ValueError(f"selection guard metric absent: {metric}")
                left = candidate.groupby(c["group"])[metric].mean()
                right = reference.groupby(c["group"])[metric].mean()
                if (set(left.index) != set(right.index) or not np.isfinite(left).all()
                        or not np.isfinite(right).all()):
                    raise ValueError("selection guard group coverage mismatch")
                for group in sorted(left.index):
                    candidate_deviation = abs(float(left[group])-guard["target"])
                    reference_deviation = abs(float(right[group])-guard["target"])
                    ok = candidate_deviation <= reference_deviation + guard["tolerance"]
                    passed &= ok
                    comparisons.append({
                        "group": group, "metric": metric,
                        "candidate_mean": float(left[group]),
                        "reference_mean": float(right[group]),
                        "candidate_deviation": candidate_deviation,
                        "reference_deviation": reference_deviation,
                        "passed": bool(ok),
                    })
            for comparison, strict in (("cell_improvement_metrics", True),
                                       ("cell_noninferiority_metrics", False)):
                for metric in guard.get(comparison, []):
                    if metric not in scores:
                        raise ValueError(f"selection guard metric absent: {metric}")
                    keys = [c["group"], c["horizon"]]
                    left = candidate.groupby(keys)[metric].mean()
                    right = reference.groupby(keys)[metric].mean()
                    if (not left.index.equals(right.index) or not np.isfinite(left).all()
                            or not np.isfinite(right).all()):
                        raise ValueError("selection guard cell coverage mismatch")
                    candidate_mean, reference_mean = float(left.mean()), float(right.mean())
                    ok = (candidate_mean < reference_mean-guard["tolerance"] if strict
                          else candidate_mean <= reference_mean+guard["tolerance"])
                    passed &= ok
                    comparisons.append({
                        "comparison": ("cell_strict_improvement" if strict
                                       else "cell_noninferiority"),
                        "metric": metric,
                        "candidate_mean": candidate_mean,
                        "reference_mean": reference_mean,
                        "passed": bool(ok),
                    })
            evidence.append({"model": name, "variant": variant,
                             "feasible": bool(passed), "comparisons": comparisons})
            if passed:
                feasible.append((name, variant))
        if not feasible:
            raise ValueError("no candidate satisfies development selection guard")
        return feasible, evidence

    def _select(self, frame, identity):
        import copy
        import pandas as pd
        e, c = self.experiment, self.base
        expected = self._expected(frame, self.development)
        all_scores, controls, screen_specs, selected, variants = [], None, {}, {}, {}
        rankings, guard_evidence = {}, {}
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
            eligible, evidence = self._guard(
                scores, [(n, v) for n in names for v in ("raw", "calibrated")])
            guard_evidence[partition] = evidence
            name, variant = min(eligible, key=lambda nv: rank[nv])
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
                   "selection_metric": e.get("selection_metric", "crps"),
                   "skill_pct": {key: 100*(1-value) for key, value in rankings.items()},
                   "selection_guard": self.experiment.get("selection_guard"),
                   "selection_guard_evidence": guard_evidence,
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

        records = []
        for path in paths:
            for file in sorted(path.glob("*-curves.npz")):
                with np.load(file, allow_pickle=False) as a:
                    n = len(a["row_index"])
                    ix = np.linspace(0, n-1, min(n, r["audit_rows"]), dtype=int)
                    curve = self.restore_curve(a, ix)
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

    @staticmethod
    def restore_curve(archive, indices):
        """Reconstruct exact archived forecasts for specified saved row indices."""
        import numpy as np

        ix = np.asarray(indices, dtype=int)
        if (ix.ndim != 1 or not len(ix) or (ix < 0).any()
                or (ix >= len(archive["row_index"])).any()):
            raise ValueError("invalid frozen curve indices")

        def restore(prefix=""):
            kind = str(archive[prefix+"kind"])
            if kind == "convex":
                weight = archive[prefix+"weight"]
                weight = float(weight) if weight.ndim == 0 else weight[ix]
                return ConvexCurve(restore(prefix+"left_"),
                                   restore(prefix+"right_"), weight)
            if kind == "grid":
                return GridCurve(archive[prefix+"values"][ix],
                                 archive[prefix+"probabilities"][ix])
            if kind not in ("mixture", "student_mixture"):
                raise ValueError(f"unknown frozen curve kind {kind!r}")
            args = [archive[prefix+k][ix] for k in ("weights", "means", "scales")]
            if kind == "student_mixture":
                return StudentMixtureCurve(*args, degrees=float(archive[prefix+"degrees"]))
            return MixtureCurve(*args)

        curve = restore()
        if "calibration_x" in archive:
            calibrated = object.__new__(CalibratedCurve)
            calibrated.base = curve
            calibrated.map = GridCurve(archive["calibration_x"], archive["calibration_p"])
            curve = calibrated
        return curve

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
        self._stage_started = time.monotonic()
        c, e = self.base, self.experiment
        if stage not in ("search", "select", "evaluate", "report") or not provenance:
            raise ValueError("invalid stage or missing provenance")
        _validate_temporal_frame(frame, c["date"], c["end"])
        self.plan.lock(frame)
        if frame.duplicated(c["identity"]).any() or frame[c["identity"]].isna().any().any():
            raise ValueError("missing or duplicate panel identities")
        mapping = e["task_features"]
        if set(frame[c["group"]]) != set(mapping):
            raise ValueError("unknown or missing task symbols")
        for group, feature in mapping.items():
            if not (frame[feature] == (frame[c["group"]] == group).astype(int)).all():
                raise ValueError("raw one-hot task features disagree with symbol mapping")
        dev = frame[~label_reaches(frame[c["end"]], self.cutoff)]
        self._check_cells(self._expected(dev, self.development), "development")
        self._check_cells(self._expected(frame, self.later), "evaluation")
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
            years, panel, samples = self.development, dev, e["resolutions"]["screen_samples"]
            selection_hash = None
        else:
            selection, selection_hash = self._selection(identity)
            models = selection["models"]
            if stage == "report":
                if partition is not None:
                    raise ValueError("report does not accept partition")
                parts, paths = [], []
                for name, years in self.partitions.items():
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
                self._write(path/"public_probes.json", {
                    "selection_eligible": [],
                    "descriptive_only": e.get("public_probes", []),
                    "rule": "unknown pretraining cutoff cannot enter historical selection or promotion",
                })
                self._complete(path, identity, stage, None, selection_hash=selection_hash)
                return result
            if partition not in self.partitions:
                raise ValueError("unknown evaluation partition")
            years = self.partitions[partition]
            panel = dev if partition == "development" else frame
            samples = e["resolutions"]["final_samples"]
        path = self.output/stage/partition
        study = ChronologicalCDFStudy({**c, "output": str(path), **self.plan.stage(years),
                                       "samples": samples, "models": models})
        scores = study.run(panel, diagnostic)
        self._check_scores(scores, self._expected(panel, years), models)
        self._write(path/"data_provenance.json", provenance)
        self._complete(path, identity, stage, partition, selection_hash=selection_hash)
        return scores


class OptionPriceCDF:
    """Undiscounted parity/isotonic option-price CDF proxy (ADR-0213).

    Parameters
    ----------
    probabilities : sequence
        Quantiles to evaluate on the exported canonical inverse grid.
    min_forward_pairs, min_wing_nodes : int
        Minimum positive parity pairs and strikes on each forward wing.
    max_inner_gap, max_outer_gap : float
        Maximum gaps in log strike divided by parity forward.
    max_projection_distance, max_forward_dispersion : float or None
        Optional guards in spot-normalized price units.
    """

    def __init__(self, probabilities, min_forward_pairs, min_wing_nodes,
                 max_inner_gap, max_outer_gap, max_projection_distance=None,
                 max_forward_dispersion=None):
        import numpy as np
        self.proxy_probabilities = np.asarray(probabilities, dtype=float)
        if (self.proxy_probabilities.ndim != 1 or not len(self.proxy_probabilities)
                or not np.isfinite(self.proxy_probabilities).all()
                or not (np.diff(self.proxy_probabilities) > 0).all()
                or not ((self.proxy_probabilities > 0) & (self.proxy_probabilities < 1)).all()):
            raise ValueError("probabilities must increase strictly inside (0, 1)")
        for name, value in (("min_forward_pairs", min_forward_pairs),
                            ("min_wing_nodes", min_wing_nodes)):
            if type(value) is not int or value < 1:
                raise ValueError(name+" must be a positive integer")
            setattr(self, name, value)
        for name, value in (("max_inner_gap", max_inner_gap),
                            ("max_outer_gap", max_outer_gap),
                            ("max_projection_distance", max_projection_distance),
                            ("max_forward_dispersion", max_forward_dispersion)):
            if ((value is None and name in ("max_inner_gap", "max_outer_gap"))
                    or (value is not None and (isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not np.isfinite(value) or value < 0))):
                raise ValueError(name+" must be finite and nonnegative")
            setattr(self, name, value)
    def estimate(self, rows, spot):
        """Return an eligible curve and diagnostics, or explicit rejection reasons."""
        import numpy as np
        clean = rows.copy()
        if (not {"strike", "type", "mark"}.issubset(clean.columns)
                or not len(clean)):
            return {"eligible": False, "reasons": ["missing_price_rows"]}
        try:
            numeric = clean[["strike", "mark"]].to_numpy(dtype=float)
        except (TypeError, ValueError):
            return {"eligible": False, "reasons": ["nonnumeric_prices"]}
        if (not np.isfinite(numeric).all()
                or not (numeric > 0).all()
                or not clean.type.isin(["call", "put"]).all()
                or clean.duplicated(["strike", "type"]).any()):
            return {"eligible": False, "reasons": ["invalid_or_duplicate_prices"]}
        from numbers import Real
        if (isinstance(spot, (bool, np.bool_)) or not isinstance(spot, Real)
                or not np.isfinite(spot) or spot <= 0):
            return {"eligible": False, "reasons": ["invalid_spot"]}
        if np.max(numeric[:, 0]) > np.finfo(float).max*min(float(spot), 1.):
            return {"eligible": False, "reasons": ["nonfinite_return_coordinates"]}
        clean[["strike", "mark"]] = numeric
        paired = clean.pivot_table(index="strike", columns="type", values="mark",
                                   aggfunc="median").dropna()
        if paired.empty or not {"call", "put"}.issubset(paired.columns):
            return {"eligible": False, "reasons": ["missing_forward_pairs"]}
        forwards = paired.index.to_numpy()+paired.call.to_numpy()-paired.put.to_numpy()
        forwards = forwards[np.isfinite(forwards) & (forwards > 0)]
        if len(forwards) < self.min_forward_pairs:
            return {"eligible": False, "reasons": ["insufficient_forward_pairs"]}
        forward = float(np.median(forwards))
        if not np.isfinite(forward):
            return {"eligible": False, "reasons": ["nonfinite_forward"]}
        dispersion = float(np.subtract(*np.percentile(forwards, [75, 25]))/spot)
        clean["call_equivalent"] = np.where(clean.type.eq("call"), clean.mark,
                                             clean.mark+forward-clean.strike)
        otm = clean[((clean.type == "put") & (clean.strike <= forward))
                    | ((clean.type == "call") & (clean.strike >= forward))]
        calls = otm.groupby("strike", as_index=False).call_equivalent.median().sort_values("strike")
        strike, price = calls.strike.to_numpy(), calls.call_equivalent.to_numpy()
        if not np.isfinite(price).all():
            return {"eligible": False, "reasons": ["nonfinite_call_equivalents"]}
        if len(strike) < 2*self.min_wing_nodes+1:
            return {"eligible": False, "reasons": ["insufficient_strikes"]}
        left, right = strike < forward, strike > forward
        logk = np.log(strike/forward)
        if not np.isfinite(logk).all():
            return {"eligible": False, "reasons": ["nonfinite_strike_geometry"]}
        if (left.sum() < self.min_wing_nodes or right.sum() < self.min_wing_nodes
                or -logk[left].max() > self.max_inner_gap
                or logk[right].min() > self.max_inner_gap
                or (np.diff(logk) > self.max_outer_gap).any()):
            return {"eligible": False, "reasons": ["inadequate_wing_support"]}
        raw_slope = np.diff(price)/np.diff(strike)
        if not np.isfinite(raw_slope).all():
            return {"eligible": False, "reasons": ["nonfinite_price_slopes"]}
        from sklearn.isotonic import IsotonicRegression
        slope = IsotonicRegression(increasing=True, y_min=-1., y_max=0.,
                                   out_of_bounds="clip").fit_transform(
                                       (strike[:-1]/2+strike[1:]/2), raw_slope,
                                       sample_weight=np.diff(strike))
        cdf = np.clip(1+slope, 0, 1)
        if not (np.isfinite(cdf).all() and (np.diff(cdf) >= -1e-12).all()):
            return {"eligible": False, "reasons": ["invalid_cdf"]}
        informative = np.flatnonzero((cdf > 1e-4) & (cdf < 1-1e-4))
        if len(informative) < 3:
            return {"eligible": False, "reasons": ["insufficient_interior_knots"]}
        first, last = informative[0], informative[-1]
        cdf = cdf[first:last+1]
        midpoint = (strike[:-1]/2+strike[1:]/2)
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
        distance = float(np.sqrt(np.mean((projected-price)**2))/spot)
        if (not np.isfinite([distance, dispersion, forward/spot]).all()
                or not np.isfinite(values).all() or not np.isfinite(quantiles).all()):
            return {"eligible": False, "reasons": ["nonfinite_curve_diagnostics"]}
        reasons = []
        for name, value, limit in (
                ("projection_distance", distance, self.max_projection_distance),
                ("forward_dispersion", dispersion, self.max_forward_dispersion)):
            if limit is not None and value > limit:
                reasons.append(name+"_exceeded")
        return {"eligible": not reasons, "reasons": reasons,
                "forward_ratio": forward/spot, "projection_distance": distance,
                "forward_dispersion": dispersion, "forward_pairs": len(forwards),
                "mass": float(cdf[-1]-cdf[0]), "quantiles": quantiles.tolist(),
                "probabilities": p.tolist(), "log_returns": values.tolist(),
                "raw_projected_probabilities": (1+slope).tolist(),
                "raw_midpoint_log_returns": np.log(
                    (strike[:-1]/2+strike[1:]/2)/spot).tolist(),
                "tail_completion": "finite_support_endpoint_completion"}



class ExpiryCloseLabels(Node):
    """Join option session/expiry keys to verified exchange closes.

    Parameters
    ----------
    params : dict
        calendar and asof declare the exchange and latest allowed close.
    """

    role = "labels"
    outputs = ("records", "labels", "summary")
    _PARAMS = ("calendar", "asof")

    @classmethod
    def validate_params(cls, params):
        """Return unknown or missing parameter problems."""
        errors = []
        reject_unknown_params(errors, params, cls._PARAMS)
        for name in cls._PARAMS:
            if not isinstance(params.get(name), str) or not params[name]:
                errors.append(name+" must be a nonempty string")
        return errors

    @staticmethod
    def _prices(rows):
        prices = {}
        for row in rows:
            key = (row["symbol"], row["quote_date"])
            if key in prices:
                raise ValueError("duplicate underlying session")
            prices[key] = row
        return prices

    @staticmethod
    def _close(row):
        import math
        value = row.get("close") if row else None
        return (value if row and row.get("close_complete") is True
                and row.get("post_session_split") is False
                and row.get("unit_history_verified") is True
                and isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value > 0 else None)

    def run(self, ctx, inputs):
        """Return nullable terminal-close labels and durable full evidence."""
        import math
        import pandas as pd
        import exchange_calendars as xc
        from collections import Counter
        options = inputs["bars"]+inputs["snapshots"]
        prices = self._prices(inputs["prices"])
        keys = sorted({(r["symbol"], r["quote_date"], r["expiry"], r["price_basis"])
                       for r in options if r.get("quote_date") is not None})
        asof = pd.Timestamp(self.params["asof"])
        if asof.tzinfo is None:
            raise ValueError("asof requires an explicit timezone")
        dates = [k[1] for k in keys]+[k[2] for k in keys]
        calendar = xc.get_calendar(
            self.params["calendar"], start=pd.Timestamp(min(dates))-pd.Timedelta(days=7),
            end=pd.Timestamp(max(dates))+pd.Timedelta(days=7)) if dates else None
        records = []
        for symbol, day, expiry, basis in keys:
            row = {"symbol": symbol, "quote_date": day, "expiry": expiry,
                   "price_basis": basis, "terminal_return": None, "spot": None,
                   "terminal_price": None, "settlement_date": None,
                   "actual_calendar_dte": None, "entry_close_at": None, "decision_at": None,
                   "label_kind": "underlying_expiry_close", "label_reasons": []}
            if not calendar.is_session(day):
                row["label_reasons"].append("non_session_entry")
                records.append(row)
                continue
            terminal = calendar.date_to_session(expiry, direction="previous")
            row["settlement_date"] = terminal.date().isoformat()
            row["actual_calendar_dte"] = (terminal.date()-pd.Timestamp(day).date()).days
            entry_close = calendar.session_close(day)
            row["entry_close_at"] = entry_close.isoformat()
            row["decision_at"] = entry_close.isoformat()
            entry_source = prices.get((symbol, day)) or {}
            terminal_source = prices.get((symbol, row["settlement_date"])) or {}
            row["entry_price_source_sha256"] = entry_source.get("source_sha256")
            row["terminal_price_source_sha256"] = terminal_source.get("source_sha256")
            row["unit_check"] = ("verified_no_subsequent_split"
                                 if entry_source.get("unit_history_verified") is True
                                 and entry_source.get("post_session_split") is False
                                 else "unverified_or_changed_units")
            row["outcome_asof"] = asof.isoformat()
            spot = self._close(prices.get((symbol, day)))
            if spot is None or entry_close > asof:
                row["label_reasons"].append("unverified_entry_close")
            else:
                row["spot"] = spot
            terminal_price = self._close(prices.get((symbol, row["settlement_date"])))
            if row["actual_calendar_dte"] <= 0:
                row["label_reasons"].append("nonpositive_horizon")
            elif calendar.session_close(terminal) > asof or terminal_price is None:
                row["label_reasons"].append("pending_or_missing_terminal_close")
            elif row["spot"] is not None:
                row["terminal_price"] = terminal_price
                row["terminal_return"] = math.log(terminal_price/spot)
            records.append(row)
        reasons = Counter(reason for r in records for reason in r["label_reasons"])
        return {"records": records, "labels": JsonArtifact(records),
                "summary": {"rows": len(records),
                            "settled": sum(r["terminal_return"] is not None for r in records),
                            "reasons": dict(reasons)}}


class OptionCDFPanel(Node):
    """Build source-labelled option CDF proxies and a shared coverage panel.

    Parameters
    ----------
    params : dict
        Numerical admission, standard-contract terms and quote-age policy.
        Full evidence is emitted through JsonArtifact; records feed the graph.
    """

    role = "transform"
    outputs = ("records", "panel", "options", "cdfs", "summary")
    _CURVE_PARAMS = ("probabilities", "min_forward_pairs", "min_wing_nodes",
                     "max_inner_gap", "max_outer_gap", "max_projection_distance",
                     "max_forward_dispersion")
    _PARAMS = _CURVE_PARAMS+("required_multiplier", "required_style", "max_quote_age_seconds")

    @classmethod
    def validate_params(cls, params):
        """Return missing, unknown and invalid policy problems."""
        import math
        errors = []
        reject_unknown_params(errors, params, cls._PARAMS)
        missing = set(cls._PARAMS)-set(params)
        if missing:
            return errors+["missing parameters: "+str(sorted(missing))]
        try:
            OptionPriceCDF(**{k: params[k] for k in cls._CURVE_PARAMS})
            names = [f"rn_q_{round(p*10000):04d}" for p in params["probabilities"]]
            if len(set(names)) != len(names):
                errors.append("probabilities collide in prepared quantile field names")
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
        for key in ("required_multiplier", "max_quote_age_seconds"):
            value = params[key]
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                errors.append(key+" must be finite and positive")
        if not isinstance(params["required_style"], str) or not params["required_style"]:
            errors.append("required_style must be a nonempty string")
        return errors

    def _admission(self, row, label):
        import math
        import pandas as pd
        reasons = list(row.get("observation_reasons", []))
        if row.get("contract_terms_status") != "metadata_present":
            reasons.append("unverified_contract_terms")
        elif (row.get("root_symbol") != row.get("symbol")
              or row.get("multiplier") != self.params["required_multiplier"]
              or row.get("contract_size") != self.params["required_multiplier"]
              or row.get("style") != self.params["required_style"]):
            reasons.append("nonstandard_contract_terms")
        mark, strike = row.get("mark"), row.get("strike")
        if any(isinstance(v, bool) or not isinstance(v, (int, float))
               or not math.isfinite(v) or v <= 0 for v in (mark, strike)):
            reasons.append("invalid_price")
        if label is None or label["spot"] is None:
            reasons.append("unverified_entry_close")
        if label is None or not label.get("entry_close_at"):
            reasons.append("unverified_entry_clock")
        if row["price_basis"] == "indicative_quote":
            stamp = row.get("quote_timestamp")
            if not stamp:
                reasons.append("missing_quote_timestamp")
            elif label and label.get("entry_close_at"):
                try:
                    age = (pd.Timestamp(label["entry_close_at"])-pd.Timestamp(stamp)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    age = float("nan")
                if not math.isfinite(age):
                    reasons.append("invalid_quote_clock")
                elif age < 0 or age > self.params["max_quote_age_seconds"]:
                    reasons.append("quote_outside_close_window")
        elif row["price_basis"] != "trade_close":
            reasons.append("unsupported_price_basis")
        return sorted(set(reasons))

    def run(self, ctx, inputs):
        """Return full canonical observations, proxy curves and coverage records."""
        from collections import Counter, defaultdict
        import pandas as pd
        keys = ("symbol", "quote_date", "expiry", "price_basis")
        labels = {tuple(r[k] for k in keys): r for r in inputs["labels"]}
        if len(labels) != len(inputs["labels"]):
            raise ValueError("duplicate label key")
        observations, groups = [], defaultdict(list)
        seen = set()
        for source in inputs["bars"]+inputs["snapshots"]:
            row = dict(source)
            key = tuple(row[k] for k in keys)
            identity = (key, row["contract"])
            if identity in seen:
                raise ValueError("duplicate option observation")
            seen.add(identity)
            row["admission_reasons"] = self._admission(row, labels.get(key))
            row["available_after"] = (labels.get(key) or {}).get("entry_close_at")
            row["spot_basis"] = "completed_close"
            row["decision_at"] = row["available_after"]
            observations.append(row)
            groups[key].append(row)
        helper = OptionPriceCDF(**{k: self.params[k] for k in self._CURVE_PARAMS})
        records, curves = [], []
        for key, label in sorted(labels.items()):
            rows = groups[key]
            usable = [r for r in rows if not r["admission_reasons"]]
            proxy = {"eligible": False, "reasons": ["no_admissible_options"]}
            if usable and label["spot"] is not None:
                frame = pd.DataFrame(usable)
                duplicates = frame.duplicated(["strike", "type"], keep=False)
                proxy = helper.estimate(frame.loc[~duplicates], label["spot"])
            record = {**label, "rn_proxy_eligible": int(proxy["eligible"]),
                      "asof_ms": int(pd.Timestamp(label["quote_date"], tz="UTC").timestamp()*1000),
                      "option_rows": len(rows), "admissible_option_rows": len(usable),
                      "cdf_reasons": proxy["reasons"],
                      "cdf_kind": "american_option_price_proxy",
                      "spot_basis": "completed_close"}
            for i, probability in enumerate(self.params["probabilities"]):
                record[f"rn_q_{round(probability*10000):04d}"] = (
                    proxy["quantiles"][i] if proxy["eligible"] else None)
            records.append(record)
            curves.append({**dict(zip(keys, key)), **proxy,
                           "cdf_kind": record["cdf_kind"],
                           "probability_interpretation": "option_price_proxy_not_physical_probability"})
        reasons = Counter(r for row in observations for r in row["admission_reasons"])
        cdf_reasons = Counter(r for curve in curves for r in curve["reasons"])
        summary = {"options": len(observations), "contracts": len(inputs["contracts"]),
                   "panel_rows": len(records), "eligible_cdfs": sum(r["rn_proxy_eligible"] for r in records),
                   "settled_eligible_cdfs": sum(r["rn_proxy_eligible"] and r["terminal_return"] is not None
                                               for r in records),
                   "option_reasons": dict(reasons), "cdf_reasons": dict(cdf_reasons)}
        return {"records": records, "panel": JsonArtifact(records),
                "options": JsonArtifact({"contracts": inputs["contracts"],
                                         "observations": observations}),
                "cdfs": JsonArtifact(curves), "summary": summary}
