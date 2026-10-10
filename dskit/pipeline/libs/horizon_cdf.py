"""Horizon-wise predictive distributions on device, scored on ONE common grid.

A forecaster here emits, for ``R`` rows and ``H`` horizons, either a parametric
mixture (:class:`MixtureHorizons`) or a set of quantile knots
(:class:`QuantileHorizons`). Both are :class:`HorizonDistribution` objects whose
``cdf`` and ``quantile`` run as tensor programs over ``(R, H, Z)`` blocks; no
per-row curve object is ever built. :class:`CommonGrid` is the single scoring
rule every model is held to: CRPS and tail CRPS by exact integration of the
squared CDF error over a shared ``z`` grid, the pinball loss at the shared
levels, and the PIT. The row chunk is derived from a memory budget, never fixed.

Two baselines fit on arrays and emit those same distributions:
:class:`EmpiricalHorizonFit` (the vol-standardized empirical baseline, valid
because labels arrive already divided by their scale) and
:class:`StudentScaleFit` (an EWMA-style Student-t baseline on a volatility
column, built on the existing mixture family).

Units: every label is expected in standardized units already; nothing here
rescales. The tail wings are one declared value, shared by the training loss and
this grid, and :meth:`CommonGrid.check_forecaster` refuses a disagreement.
Torch is imported inside methods only; the pack registers no node kinds.
"""

from abc import ABC, abstractmethod

from dskit.pipeline.distribution_scores import DEFAULT_PIT_BINS, PitCalibration
from dskit.pipeline.libs.predictive_cdf import mixture_family
from dskit.pipeline.records import number_ok

__all__ = [
    "BASIS_EXTRAPOLATED", "BASIS_PARAMETRIC", "CommonGrid", "DEFAULT_EM_ITERATIONS",
    "DEFAULT_EM_TOLERANCE", "DEFAULT_FIT_CHUNK_ROWS", "DEFAULT_MAX_CHUNK_MB",
    "DEFAULT_Z_POINTS", "EmpiricalHorizonFit", "HorizonDistribution",
    "HorizonForecaster", "HorizonScores", "MixtureHorizons", "NODE_KINDS",
    "QuantileHorizons", "StudentScaleFit",
]

NODE_KINDS = ()

#: ``cdf_basis`` of an exact parametric CDF.
BASIS_PARAMETRIC = "parametric"
#: ``cdf_basis`` of knots whose tails were extended linearly to probability 0 and 1.
BASIS_EXTRAPOLATED = "extrapolated"

#: Points on the shared ``z`` grid, unless a grid declares otherwise.
DEFAULT_Z_POINTS = 2001
#: Memory budget of one scoring chunk in MiB, unless a grid declares otherwise.
DEFAULT_MAX_CHUNK_MB = 256
#: EM scale iterations of :class:`StudentScaleFit`, unless it is told otherwise.
DEFAULT_EM_ITERATIONS = 100
#: Largest relative change of a multiplier at which EM stops.
DEFAULT_EM_TOLERANCE = 1e-8
#: Rows per pass over the fit data, unless the fit is told otherwise.
DEFAULT_FIT_CHUNK_ROWS = 262144

# Bisection steps of the mixture quantile, and the half-width (in component
# scales) of its initial bracket. The bracket must hold every probability the
# caller can ask for; 1e4 scales leaves a Student-3 tail mass below 1e-11.
_BISECTION_STEPS = 60
_BRACKET_SCALES = 1e4
# Live (R, H, Z) temporaries one scored chunk holds besides the K component terms.
_ROW_TEMPS = 12
_MIB = 1 << 20
_LEVEL_TOLERANCE = 1e-12
_MIN_Z_POINTS = 3


def _checked_levels(levels, minimum):
    """Return ``levels`` as a tuple of floats, strictly increasing in (0, 1), or refuse."""
    if (not isinstance(levels, (list, tuple)) or len(levels) < minimum
            or not all(number_ok(v) for v in levels)
            or not all(0 < a < 1 for a in levels)
            or any(b <= a for a, b in zip(levels, levels[1:]))):
        raise ValueError(
            f"levels must be {minimum}+ numbers strictly increasing inside (0, 1), got {levels!r}")
    return tuple(float(v) for v in levels)


def _checked_intervals(intervals, name):
    """Return ``intervals`` as sorted disjoint ``(lo, hi)`` tuples, or refuse."""
    if (not isinstance(intervals, (list, tuple)) or not intervals
            or not all(isinstance(i, (list, tuple)) and len(i) == 2
                       and all(number_ok(v) for v in i) and i[0] < i[1] for i in intervals)):
        raise ValueError(f"{name} must be a non-empty list of [lo, hi] with lo < hi")
    out = tuple(sorted((float(a), float(b)) for a, b in intervals))
    if any(b[0] < a[1] for a, b in zip(out, out[1:])):
        raise ValueError(f"{name} must be disjoint")
    return out


def _chunks(n, size):
    """Yield ``(start, stop)`` pairs covering ``range(n)`` in steps of ``size``."""
    for start in range(0, n, size):
        yield start, min(start + size, n)


def _finite_panel(torch, y, name, columns=None):
    """Return ``y`` after refusing anything but a finite non-empty (N, H) tensor."""
    if (not isinstance(y, torch.Tensor) or y.dim() != 2 or y.shape[0] == 0
            or (columns is not None and y.shape[1] != columns)
            or not bool(torch.isfinite(y).all())):
        raise ValueError(f"{name} must be a finite, non-empty (rows, horizons) tensor")
    return y


class HorizonDistribution(ABC):
    """One predictive distribution per row and horizon, evaluated as tensors.

    A subclass sets ``self._anchor`` (any tensor shaped ``(R, H, ...)``, which
    fixes the dtype, device and shape) and supplies ``cdf`` and ``quantile``.

    Attributes
    ----------
    basis : str
        ``BASIS_PARAMETRIC`` or ``BASIS_EXTRAPOLATED``.
    levels : tuple or None
        The quantile levels of a knot distribution, ``None`` for a parametric one.
    bounds : tuple or None
        The ``(z_min, z_max)`` its tails were clipped to, ``None`` when unclipped.

    Examples
    --------
    A subclass states how it integrates and inverts::

        class Uniform(HorizonDistribution):
            basis = BASIS_PARAMETRIC
            def __init__(self, width):
                self._anchor = width
            def cdf(self, z):
                return (z / self._anchor).clamp(0, 1)
            def quantile(self, levels):
                return self._anchor * levels
    """

    basis = BASIS_PARAMETRIC
    levels = None
    bounds = None

    @abstractmethod
    def cdf(self, z):
        """Evaluate the CDF.

        Parameters
        ----------
        z : torch.Tensor
            Cutoffs of shape ``(Z,)`` (common to all rows) or ``(R, H, Z)``.

        Returns
        -------
        torch.Tensor
            Probabilities of shape ``(R, H, Z)``.
        """

    @abstractmethod
    def quantile(self, levels):
        """Invert the CDF.

        Parameters
        ----------
        levels : sequence of float
            Probabilities in ``[0, 1]``.

        Returns
        -------
        torch.Tensor
            Quantiles of shape ``(R, H, L)``.
        """

    @property
    def shape(self):
        """The ``(rows, horizons)`` this distribution describes."""
        return tuple(self._anchor.shape[:2])

    @property
    def n_rows(self):
        """Rows described; ``1`` means row-constant, valid for any row count."""
        return self._anchor.shape[0]

    @property
    def dtype(self):
        """The floating dtype the distribution computes in."""
        return self._anchor.dtype

    @property
    def device(self):
        """The device the distribution lives on."""
        return self._anchor.device

    def _broadcast_cutoffs(self, z):
        """Return ``z`` as an ``(R, H, Z)`` tensor, expanding a shared grid over rows."""
        r, h = self.n_rows, self.shape[1]
        if z.dim() == 1:
            return z.reshape(1, 1, -1).expand(r, h, -1)
        if z.dim() != 3 or z.shape[1] != h:
            raise ValueError(f"cutoffs must be (Z,) or (R, {h}, Z), got {tuple(z.shape)}")
        return z

    @staticmethod
    def _rows_like(t, n):
        """Return ``t`` with a leading row axis of 1 expanded to ``n`` rows."""
        return t.expand(n, *t.shape[1:]) if t.shape[0] == 1 and n != 1 else t


class MixtureHorizons(HorizonDistribution):
    """A parametric location-scale mixture per row and horizon.

    Parameters
    ----------
    logw, mu, sigma : torch.Tensor
        ``(R, H, K)`` log mixture weights, locations and positive scales.
    family : object
        A ``predictive_cdf.mixture_family`` result (Gaussian or Student); its
        ``mixture_cdf`` supplies the exact CDF.

    Examples
    --------
    One standard normal per row and horizon::

        import torch
        zeros = torch.zeros(4, 3, 1)
        dist = MixtureHorizons(zeros, zeros, zeros + 1, mixture_family({"kind": "gaussian"}))
        dist.cdf(torch.tensor([0.0]))   # every entry is 0.5
    """

    basis = BASIS_PARAMETRIC

    def __init__(self, logw, mu, sigma, family):
        if not (logw.dim() == 3 and logw.shape == mu.shape == sigma.shape):
            raise ValueError("logw, mu and sigma must share one (rows, horizons, components) shape")
        self.logw, self.mu, self.sigma, self.family = logw, mu, sigma, family
        self._anchor = mu
        self._quantiles = {}

    def cdf(self, z):
        """Evaluate the mixture CDF; see :meth:`HorizonDistribution.cdf`."""
        zz = self._broadcast_cutoffs(z)
        r, h, k = self.logw.shape
        n = max(r, zz.shape[0])
        flat = zz.reshape(n * h, -1)
        parts = (self._rows_like(t, n).reshape(n * h, k) for t in (self.logw, self.mu, self.sigma))
        return self.family.mixture_cdf(flat, *parts).reshape(n, h, -1)

    def quantile(self, levels):
        """Invert by vectorized bisection; see :meth:`HorizonDistribution.quantile`.

        The result for one set of levels is kept, because scoring asks for the grid's levels twice
        per chunk (the pinball and its per-level table) and each ask is 60 bisection steps.
        """
        key = tuple(float(v) for v in levels)
        if key not in self._quantiles:
            self._quantiles[key] = self._bisect(key)
        return self._quantiles[key]

    def _bisect(self, levels):
        """Return the quantiles at ``levels`` by 60 halvings of a bracket around the mixture."""
        import torch
        p = torch.as_tensor(levels, dtype=self.dtype, device=self.device)
        span = _BRACKET_SCALES * self.sigma
        r, h = self.shape
        lo = (self.mu - span).min(-1).values.unsqueeze(-1).expand(r, h, p.numel())
        hi = (self.mu + span).max(-1).values.unsqueeze(-1).expand(r, h, p.numel())
        for _ in range(_BISECTION_STEPS):
            mid = (lo + hi) / 2
            below = self.cdf(mid) < p
            lo, hi = torch.where(below, mid, lo), torch.where(below, hi, mid)
        return (lo + hi) / 2


class QuantileHorizons(HorizonDistribution):
    """Piecewise-linear CDF through quantile knots, with a DECLARED tail rule.

    The first two knots are extended linearly to probability 0 and the last two
    to probability 1; with ``bounds`` (REQUIRED by :class:`CommonGrid`) the extended endpoints are clipped into
    ``[z_min, z_max]`` (never inside the outermost knot). Probabilities between
    levels are therefore interpolated, which is why scores on this CDF carry
    ``cdf_basis: extrapolated``.

    Parameters
    ----------
    levels : sequence of float
        ``K >= 2`` strictly increasing probabilities inside (0, 1).
    values : torch.Tensor
        ``(R, H, K)`` non-decreasing knot values; ``R == 1`` is row-constant.
    bounds : tuple of float or None
        ``(z_min, z_max)`` to clip the extended endpoints to, or ``None``.

    Raises
    ------
    ValueError
        For bad levels, non-finite or decreasing knots, or inverted bounds.

    Examples
    --------
    One shared set of knots for three horizons::

        import torch
        knots = torch.tensor([[[-1.0, 0.0, 1.0]] * 3])
        dist = QuantileHorizons([0.1, 0.5, 0.9], knots)
        dist.quantile([0.5])   # -> shape (1, 3, 1), all zeros
    """

    basis = BASIS_EXTRAPOLATED

    def __init__(self, levels, values, bounds=None):
        import torch
        self.levels = _checked_levels(levels, 2)
        if (not isinstance(values, torch.Tensor) or values.dim() != 3
                or values.shape[2] != len(self.levels) or not bool(torch.isfinite(values).all())
                or bool((values[..., 1:] < values[..., :-1]).any())):
            raise ValueError("values must be finite, non-decreasing (rows, horizons, len(levels))")
        if bounds is not None:
            bounds = _checked_intervals([bounds], "bounds")[0]
        self.bounds = bounds
        lv = torch.as_tensor(self.levels, dtype=values.dtype, device=values.device)
        left = values[..., 0] - lv[0] * (values[..., 1] - values[..., 0]) / (lv[1] - lv[0])
        right = values[..., -1] + (1 - lv[-1]) * (values[..., -1] - values[..., -2]) / (lv[-1] - lv[-2])
        if bounds is not None:
            left = torch.minimum(left.clamp(min=bounds[0]), values[..., 0])
            right = torch.maximum(right.clamp(max=bounds[1]), values[..., -1])
        self._knots = torch.cat([left.unsqueeze(-1), values, right.unsqueeze(-1)], dim=-1).contiguous()
        edge = torch.tensor([0.0], dtype=torch.float64, device=values.device)
        self._levels64 = torch.cat([edge, torch.tensor(self.levels, dtype=torch.float64,
                                                       device=values.device), edge + 1])
        self._anchor = values

    def cdf(self, z):
        """Interpolate the CDF; see :meth:`HorizonDistribution.cdf`."""
        import torch
        zz = self._broadcast_cutoffs(z)
        knots = self._rows_like(self._knots, zz.shape[0]).contiguous()
        zz = zz.contiguous()
        n = knots.shape[-1]
        idx = torch.searchsorted(knots, zz, right=True)
        i = (idx - 1).clamp(0, n - 2)
        x0, x1 = knots.gather(-1, i), knots.gather(-1, i + 1)
        p = self._levels64.to(zz.dtype)
        p0, p1 = p[i], p[i + 1]
        width = x1 - x0
        frac = torch.where(width > 0, (zz - x0) / width.clamp(min=torch.finfo(zz.dtype).tiny),
                           (zz >= x1).to(zz.dtype))
        return p0 + frac.clamp(0, 1) * (p1 - p0)

    def quantile(self, levels):
        """Interpolate the inverse CDF; see :meth:`HorizonDistribution.quantile`."""
        import torch
        lv = torch.as_tensor(levels, dtype=torch.float64, device=self.device)
        if bool(((lv < 0) | (lv > 1)).any()):
            raise ValueError("levels must lie in [0, 1]")
        n = self._levels64.numel()
        j = (torch.searchsorted(self._levels64, lv, right=True) - 1).clamp(0, n - 2)
        t = ((lv - self._levels64[j]) / (self._levels64[j + 1] - self._levels64[j])).to(self.dtype)
        x0, x1 = self._knots[..., j], self._knots[..., j + 1]
        return x0 + t * (x1 - x0)


class HorizonForecaster(ABC):
    """A model that turns row indices of a panel into a :class:`HorizonDistribution`.

    Attributes
    ----------
    training_tail : sequence or None
        The tail wings its loss was trained on; ``None`` when it has none.
    levels : sequence or None
        The quantile levels its head emits; ``None`` for a parametric head.
    components : int
        Mixture components per row and horizon, which sizes the scoring chunk.

    Examples
    --------
    A forecaster that ignores the panel and returns fixed knots::

        class Fixed(HorizonForecaster):
            levels = (0.1, 0.5, 0.9)
            def forecast(self, panel, rows, chunk_rows):
                import torch
                return QuantileHorizons(self.levels, torch.zeros(1, 3, 3))
    """

    training_tail = None
    levels = None
    components = 1

    @abstractmethod
    def forecast(self, panel, rows, chunk_rows):
        """Forecast one chunk.

        Parameters
        ----------
        panel : object
            The data the forecaster reads (its own contract).
        rows : sequence of int
            Row indices to forecast, at most ``chunk_rows`` of them.
        chunk_rows : int
            The scoring chunk size, for the forecaster's own inner batching.

        Returns
        -------
        HorizonDistribution
            One distribution per requested row.
        """


class CommonGrid:
    """The one scoring rule: a shared ``z`` grid, shared levels, shared tail wings.

    CRPS is the integral of ``(F(z) - 1{y <= z})^2`` with ``F`` piecewise linear
    between grid nodes and the indicator split exactly at ``y`` (each piece is a
    quadratic, integrated exactly). An outcome beyond the grid adds an edge term
    with ``F`` held at its edge value. ``tail_crps`` is the same integral over
    ``tail_intervals``; ``pinball`` is the mean quantile loss at ``levels``; PIT is
    ``F(y)`` from the distribution itself.

    Parameters
    ----------
    params : dict
        Required: ``z_min``, ``z_max`` (``z_min < z_max``), ``levels`` (strictly
        increasing inside (0, 1)), ``tail_intervals`` (disjoint ``[lo, hi]`` inside
        the grid). Optional: ``z_points`` (int >= 3, default ``DEFAULT_Z_POINTS``),
        ``pit_bins`` (int >= 2, default ``DEFAULT_PIT_BINS``), ``max_chunk_mb``
        (> 0, default ``DEFAULT_MAX_CHUNK_MB``).

    Raises
    ------
    ValueError
        For an unknown, missing or inconsistent declaration.

    Examples
    --------
    A grid whose tails are the two wings beyond two standard units::

        grid = CommonGrid({"z_min": -8, "z_max": 8, "levels": [0.1, 0.5, 0.9],
                           "tail_intervals": [[-8, -2], [2, 8]]})
        summary = grid.evaluate(forecaster, panel, rows, labels)
    """

    _PARAMS = ("z_min", "z_max", "levels", "tail_intervals", "z_points", "pit_bins",
               "max_chunk_mb")
    _REQUIRED = ("z_min", "z_max", "levels", "tail_intervals")

    def __init__(self, params):
        if not isinstance(params, dict):
            raise ValueError("grid params must be a dict")
        problems = [f"unknown param {k!r}" for k in params if k not in self._PARAMS]
        problems += [f"{k} is required" for k in self._REQUIRED if k not in params]
        if problems:
            raise ValueError("; ".join(problems))
        z_min, z_max = params["z_min"], params["z_max"]
        if not (number_ok(z_min) and number_ok(z_max) and z_min < z_max):
            raise ValueError("z_min and z_max must be finite numbers with z_min < z_max")
        points = params.get("z_points", DEFAULT_Z_POINTS)
        if type(points) is not int or points < _MIN_Z_POINTS:
            raise ValueError(f"z_points must be an int >= {_MIN_Z_POINTS}")
        budget = params.get("max_chunk_mb", DEFAULT_MAX_CHUNK_MB)
        if not number_ok(budget) or budget <= 0:
            raise ValueError("max_chunk_mb must be a number > 0")
        self.z_min, self.z_max, self.z_points = float(z_min), float(z_max), points
        self.levels = _checked_levels(params["levels"], 1)
        self.tail_intervals = _checked_intervals(params["tail_intervals"], "tail_intervals")
        if self.tail_intervals[0][0] < self.z_min or self.tail_intervals[-1][1] > self.z_max:
            raise ValueError("tail_intervals must lie inside [z_min, z_max]")
        self.pit_bins = params.get("pit_bins", DEFAULT_PIT_BINS)
        PitCalibration(self.pit_bins)
        self.max_chunk_mb = float(budget)

    @property
    def bounds(self):
        """The ``(z_min, z_max)`` pair a clipped distribution must agree with."""
        return (self.z_min, self.z_max)

    def chunk_rows(self, n_horizons, n_components=1, itemsize=4):
        """Derive the scoring chunk from the memory budget.

        Parameters
        ----------
        n_horizons : int
            Horizons per row.
        n_components : int
            Mixture components per row and horizon.
        itemsize : int
            Bytes per float.

        Returns
        -------
        int
            Rows per chunk, at least 1.
        """
        per_row = n_horizons * self.z_points * (n_components + _ROW_TEMPS) * itemsize
        return max(1, int(self.max_chunk_mb * _MIB // per_row))

    def missing_levels(self, have):
        """List the grid levels absent from ``have``.

        Parameters
        ----------
        have : sequence of float
            A head's levels.

        Returns
        -------
        list of float
            Grid levels with no match within ``1e-12``.
        """
        return [p for p in self.levels if not any(abs(p - q) <= _LEVEL_TOLERANCE for q in have)]

    def check_forecaster(self, forecaster):
        """Refuse a forecaster whose levels or training tail disagree with this grid.

        Parameters
        ----------
        forecaster : HorizonForecaster
            The model to be scored.

        Raises
        ------
        ValueError
            When its levels omit a grid level, or its ``training_tail`` differs
            from ``tail_intervals``.
        """
        if forecaster.levels is not None and self.missing_levels(forecaster.levels):
            raise ValueError(f"forecaster levels lack grid level(s) {self.missing_levels(forecaster.levels)}")
        if forecaster.training_tail is not None:
            if _checked_intervals(forecaster.training_tail, "training_tail") != self.tail_intervals:
                raise ValueError("forecaster training_tail differs from the grid's tail_intervals")

    def check_distribution(self, dist, n_rows):
        """Refuse a distribution this grid cannot score exactly.

        Parameters
        ----------
        dist : HorizonDistribution
            One chunk's forecast.
        n_rows : int
            Rows of outcomes it will be scored against.

        Raises
        ------
        ValueError
            For a row count other than ``n_rows`` or 1, missing grid levels, tail
            bounds other than the grid's, or a knot distribution with no bounds
            (its extended tails would escape the grid range).
        """
        if dist.n_rows not in (1, n_rows):
            raise ValueError(f"distribution has {dist.n_rows} rows for {n_rows} outcomes")
        if dist.levels is not None and self.missing_levels(dist.levels):
            raise ValueError(f"distribution levels lack grid level(s) {self.missing_levels(dist.levels)}")
        if dist.levels is not None and dist.bounds is None:
            raise ValueError(f"knot distribution has no bounds: its tails must be clipped to the grid's {self.bounds}")
        if dist.bounds is not None and tuple(dist.bounds) != self.bounds:
            raise ValueError(f"distribution bounds {dist.bounds} differ from the grid's {self.bounds}")

    def new_scores(self):
        """Return an empty :class:`HorizonScores` accumulator on this grid."""
        return HorizonScores(self)

    def score(self, dist, y):
        """Score one chunk.

        Parameters
        ----------
        dist : HorizonDistribution
            The forecast.
        y : torch.Tensor
            ``(R, H)`` finite outcomes in standardized units.

        Returns
        -------
        dict
            ``crps``, ``tail_crps``, ``pinball``, ``pit``: ``(R, H)`` tensors.
        """
        import torch
        _finite_panel(torch, y, "y")
        if y.shape[1] != dist.shape[1]:
            raise ValueError(f"y has {y.shape[1]} horizons, the distribution {dist.shape[1]}")
        self.check_distribution(dist, y.shape[0])
        with torch.no_grad():
            y = y.to(device=dist.device, dtype=dist.dtype)
            z = torch.linspace(self.z_min, self.z_max, self.z_points, dtype=dist.dtype, device=dist.device)
            f = dist._rows_like(dist.cdf(z), y.shape[0])
            return {
                "crps": self._crps(f, z, y),
                "tail_crps": sum(self._integral(f, z, y, lo, hi) for lo, hi in self.tail_intervals),
                "pinball": self._pinball(dist, y),
                "pit": dist._rows_like(dist.cdf(y.unsqueeze(-1)), y.shape[0]).squeeze(-1),
            }

    def _crps(self, f, z, y):
        """CRPS: the grid integral plus the edge terms of an outcome beyond the grid."""
        yy = y.unsqueeze(-1)
        edge = ((yy - self.z_max).clamp(min=0) * f[..., -1:] ** 2
                + (self.z_min - yy).clamp(min=0) * (1 - f[..., :1]) ** 2).squeeze(-1)
        return self._integral(f, z, y, self.z_min, self.z_max) + edge

    @staticmethod
    def _integral(f, z, y, lo, hi):
        """Exact integral of the squared error over ``[lo, hi]`` for piecewise-linear ``f``."""
        import torch
        z0, z1 = z[:-1], z[1:]
        u = z0.clamp(min=lo, max=hi)
        v = torch.maximum(z1.clamp(min=lo, max=hi), u)
        s = torch.minimum(torch.maximum(y.unsqueeze(-1), u), v)
        slope = (f[..., 1:] - f[..., :-1]) / (z1 - z0)
        f_u, f_s, f_v = (f[..., :-1] + slope * (t - z0) for t in (u, s, v))

        def piece(a, b, length, truth):
            return length / 6 * ((a - truth) ** 2 + (a + b - 2 * truth) ** 2 + (b - truth) ** 2)

        return (piece(f_u, f_s, s - u, 0.0) + piece(f_s, f_v, v - s, 1.0)).sum(-1)

    def _pinball(self, dist, y):
        """Mean quantile loss over the grid levels, at exactly those levels."""
        import torch
        tau = torch.tensor(self.levels, dtype=y.dtype, device=y.device)
        residual = y.unsqueeze(-1) - dist._rows_like(dist.quantile(self.levels), y.shape[0])
        return torch.maximum(tau * residual, (tau - 1) * residual).mean(-1)

    def evaluate(self, forecaster, panel, rows, y):
        """Forecast and score ``rows`` chunk by chunk.

        Parameters
        ----------
        forecaster : HorizonForecaster
            The model; checked against this grid first.
        panel : object
            Handed to ``forecaster.forecast`` untouched.
        rows : sequence of int
            Row indices (sliceable), aligned with ``y``.
        y : torch.Tensor
            ``(len(rows), H)`` outcomes in standardized units.

        Returns
        -------
        dict
            :meth:`HorizonScores.summary`.
        """
        import torch
        _finite_panel(torch, y, "y")
        if len(rows) != y.shape[0]:
            raise ValueError(f"{len(rows)} rows for {y.shape[0]} outcome rows")
        self.check_forecaster(forecaster)
        step = self.chunk_rows(y.shape[1], forecaster.components, y.element_size())
        scores = self.new_scores()
        for start, stop in _chunks(len(rows), step):
            scores.add(forecaster.forecast(panel, rows[start:stop], step), y[start:stop])
        return scores.summary()


class HorizonScores:
    """Accumulates :meth:`CommonGrid.score` over chunks and summarises per horizon.

    Parameters
    ----------
    grid : CommonGrid
        The scoring rule.

    Examples
    --------
    Score two chunks, then read the per-horizon table::

        scores = grid.new_scores()
        scores.add(first_dist, first_y)
        scores.add(second_dist, second_y)
        table = scores.summary()
    """

    def __init__(self, grid):
        self.grid = grid
        self.n = 0
        self._sums = None
        self._pits = None
        self._bases = set()

    def add(self, dist, y):
        """Score one chunk and fold it in.

        Parameters
        ----------
        dist : HorizonDistribution
            The chunk's forecast.
        y : torch.Tensor
            ``(R, H)`` outcomes.
        """
        import torch
        out = self.grid.score(dist, y)
        h = y.shape[1]
        if self._sums is None:
            self._sums = {k: torch.zeros(h, dtype=torch.float64) for k in ("crps", "tail_crps", "pinball")}
            self._pits = [[] for _ in range(h)]
        elif len(self._pits) != h:
            raise ValueError(f"chunk has {h} horizons, earlier chunks {len(self._pits)}")
        for k, total in self._sums.items():
            total += out[k].sum(0, dtype=torch.float64).cpu()
        pit = out["pit"].cpu()  # one transfer per chunk, not one per horizon
        for j in range(h):
            self._pits[j].append(pit[:, j])
        self.n += y.shape[0]
        self._bases.add(dist.basis)

    def summary(self):
        """Return the per-horizon table.

        Returns
        -------
        dict
            ``n_rows``, ``cdf_basis``, and per-horizon lists ``crps``, ``tail_crps``,
            ``pinball``, ``pit_ks``, ``pit_pvalue``, ``histogram``.

        Raises
        ------
        ValueError
            With no rows added, or when chunks mixed CDF bases.
        """
        import torch
        if not self.n:
            raise ValueError("no rows were scored")
        if len(self._bases) != 1:
            raise ValueError(f"scores mix cdf basis {sorted(self._bases)}")
        test = PitCalibration(self.grid.pit_bins)
        tests = [test.evaluate(torch.cat(p).tolist()) for p in self._pits]
        out = {"n_rows": self.n, "cdf_basis": next(iter(self._bases))}
        out.update({k: (v / self.n).tolist() for k, v in self._sums.items()})
        out["pit_ks"] = [t["statistic"] for t in tests]
        out["pit_pvalue"] = [t["pvalue"] for t in tests]
        out["histogram"] = [t["histogram"] for t in tests]
        return out


class EmpiricalHorizonFit:
    """The vol-standardized empirical baseline: per-group, per-horizon quantiles.

    Labels arrive already divided by their scale, so the empirical distribution of
    the labels IS the vol-standardized empirical baseline. Quantiles use linear
    interpolation (the ``numpy.quantile`` default) at the declared levels.

    Parameters
    ----------
    levels : sequence of float
        ``K >= 2`` strictly increasing probabilities inside (0, 1); use the
        grid's levels so the pinball needs no interpolation.

    Examples
    --------
    Fit on training labels, then forecast any number of rows::

        fit = EmpiricalHorizonFit(grid.levels).fit(train_labels)
        dist = fit.predict(bounds=grid.bounds)   # row-constant QuantileHorizons
    """

    def __init__(self, levels):
        self.levels = _checked_levels(levels, 2)
        self.group_ids = None
        self.values = None

    def fit(self, y, groups=None):
        """Learn the quantile functions.

        Parameters
        ----------
        y : torch.Tensor
            ``(N, H)`` finite standardized labels.
        groups : torch.Tensor or None
            ``(N,)`` integer group per row, or ``None`` for one pooled group.

        Returns
        -------
        EmpiricalHorizonFit
            ``self``.

        Raises
        ------
        ValueError
            For non-finite or empty labels, or misaligned groups.
        """
        import torch
        _finite_panel(torch, y, "y")
        lv = torch.tensor(self.levels, dtype=torch.float64, device=y.device)
        if groups is None:
            self.group_ids, self.values = [], self._quantiles(y, lv).unsqueeze(0)
            return self
        if groups.shape != (y.shape[0],):
            raise ValueError("groups must be one integer per row")
        ids = torch.unique(groups)
        self.group_ids = [int(g) for g in ids]
        self.values = torch.stack([self._quantiles(y[groups == g], lv) for g in ids])
        return self

    @staticmethod
    def _quantiles(y, lv):
        """Linear-interpolated quantiles of ``y`` (n, H) at ``lv``, as (H, K)."""
        s = y.sort(0).values
        n = s.shape[0]
        pos = lv * (n - 1)
        lo = pos.floor().long().clamp(max=n - 1)
        hi = (lo + 1).clamp(max=n - 1)
        w = (pos - lo).to(y.dtype).unsqueeze(1)
        return (s[lo] * (1 - w) + s[hi] * w).T

    def predict(self, groups=None, *, bounds):
        """Emit the fitted distribution.

        Parameters
        ----------
        groups : torch.Tensor or None
            ``(R,)`` group per row; ``None`` for a pooled fit (row-constant output).
        bounds : tuple or None
            Required keyword: the grid's ``(z_min, z_max)`` to clip the tail
            extension to (``CommonGrid.bounds``); ``None`` leaves the tails
            unclipped, which the grid refuses to score.

        Returns
        -------
        QuantileHorizons
            One row per requested group, or a single row-constant row.

        Raises
        ------
        ValueError
            When the fit and ``groups`` disagree, or a group was never fitted.
        """
        import torch
        if self.values is None:
            raise ValueError("fit before predict")
        if (groups is None) != (not self.group_ids):
            raise ValueError("group argument disagrees with how the fit was grouped")
        if groups is None:
            return QuantileHorizons(self.levels, self.values, bounds)
        known = torch.tensor(self.group_ids, device=groups.device)
        index = torch.searchsorted(known, groups)
        if bool((index >= len(known)).any()) or bool((known[index.clamp(max=len(known) - 1)] != groups).any()):
            raise ValueError("a group was not seen in the fit")
        return QuantileHorizons(self.levels, self.values.to(groups.device)[index], bounds)

    def state(self):
        """Return the fit as plain JSON-able data."""
        return {"levels": list(self.levels), "group_ids": list(self.group_ids),
                "values": self.values.detach().cpu().double().tolist()}

    @classmethod
    def from_state(cls, state):
        """Rebuild a fit from :meth:`state`.

        Parameters
        ----------
        state : dict
            What :meth:`state` returned, possibly via JSON.

        Returns
        -------
        EmpiricalHorizonFit
            The restored fit.
        """
        import torch
        fit = cls(state["levels"])
        fit.group_ids = [int(g) for g in state["group_ids"]]
        fit.values = torch.tensor(state["values"], dtype=torch.float64)
        return fit


class StudentScaleFit:
    """A Student-t baseline whose scale is a per-horizon multiple of a volatility column.

    One component per row and horizon: ``y_h ~ t_df(0, c_h * vol / reference)``. The
    degrees of freedom are the declared integer whose fit has the lowest training
    NLL; each ``c_h`` is the EM scale-mixture iteration for that ``df``. The Student
    CDF and log density are the existing mixture family's, not restated here.

    Parameters
    ----------
    degrees : sequence of int
        Distinct candidate degrees of freedom, each ``>= 3``.
    reference : float or None
        Divisor of the volatility column; ``None`` fits it as the column's mean.
    em_iterations : int
        Maximum EM passes (default ``DEFAULT_EM_ITERATIONS``).
    tolerance : float
        EM stops when no multiplier moves by more than this relative amount.
    chunk_rows : int
        Rows per pass over the data (default ``DEFAULT_FIT_CHUNK_ROWS``).

    Raises
    ------
    ValueError
        For a bad candidate list or knob.

    Examples
    --------
    Fit on training labels and their volatility column, then forecast::

        fit = StudentScaleFit(degrees=(3, 5, 8)).fit(train_labels, train_vol)
        dist = fit.predict(score_vol)    # MixtureHorizons, K = 1
    """

    def __init__(self, degrees, reference=None, em_iterations=DEFAULT_EM_ITERATIONS,
                 tolerance=DEFAULT_EM_TOLERANCE, chunk_rows=DEFAULT_FIT_CHUNK_ROWS):
        if (not isinstance(degrees, (list, tuple)) or not degrees
                or any(type(d) is not int or d < 3 for d in degrees)
                or len(set(degrees)) != len(degrees)):
            raise ValueError("degrees must be distinct integers >= 3")
        if reference is not None and not (number_ok(reference) and reference > 0):
            raise ValueError("reference must be a number > 0 or None")
        if type(em_iterations) is not int or em_iterations < 1:
            raise ValueError("em_iterations must be an int >= 1")
        if not (number_ok(tolerance) and tolerance > 0):
            raise ValueError("tolerance must be a number > 0")
        if type(chunk_rows) is not int or chunk_rows < 1:
            raise ValueError("chunk_rows must be an int >= 1")
        self.degrees = tuple(sorted(degrees))
        self.reference = None if reference is None else float(reference)
        self.em_iterations, self.tolerance, self.chunk_rows = em_iterations, tolerance, chunk_rows
        self.chosen = None
        self.multipliers = None
        self.nll = None

    @staticmethod
    def _positive(torch, vol, n):
        """Return ``vol`` after refusing anything but ``n`` finite positive numbers."""
        if (not isinstance(vol, torch.Tensor) or vol.shape != (n,)
                or not bool(torch.isfinite(vol).all()) or not bool((vol > 0).all())):
            raise ValueError(f"vol must be {n} finite positive numbers")
        return vol

    def fit(self, y, vol):
        """Choose ``df`` and the per-horizon multipliers.

        Parameters
        ----------
        y : torch.Tensor
            ``(N, H)`` finite standardized labels.
        vol : torch.Tensor
            ``(N,)`` positive volatility column.

        Returns
        -------
        StudentScaleFit
            ``self``.

        Raises
        ------
        ValueError
            For non-finite labels or a non-positive volatility.
        """
        import torch
        _finite_panel(torch, y, "y")
        v = self._positive(torch, vol, y.shape[0]).to(y.dtype)
        if self.reference is None:
            self.reference = float(v.double().mean())
        v = v / self.reference
        fits = {df: self._fit_df(y, v, df) for df in self.degrees}
        self.nll = {df: nll for df, (_, nll) in fits.items()}
        self.chosen = min(self.degrees, key=lambda df: (self.nll[df], df))
        self.multipliers = fits[self.chosen][0]
        return self

    def _fit_df(self, y, v, df):
        """EM scale multipliers and mean NLL for one ``df``, as ((H,) tensor, float)."""
        import torch
        family = mixture_family({"kind": "student", "degrees": df})
        ratio2 = [(y[a:b] / v[a:b, None]) ** 2 for a, b in _chunks(y.shape[0], self.chunk_rows)]
        c = torch.sqrt(sum(r.sum(0, dtype=torch.float64) for r in ratio2) / y.shape[0])
        for _ in range(self.em_iterations):
            total = torch.zeros_like(c)
            for r in ratio2:
                weight = (df + 1) / (df + r / c.to(y.dtype) ** 2)
                total += (weight * r).sum(0, dtype=torch.float64)
            new = torch.sqrt(total / y.shape[0])
            moved = float((new / c - 1).abs().max())
            c = new
            if moved < self.tolerance:
                break
        loss = 0.0
        for (a, b), r in zip(_chunks(y.shape[0], self.chunk_rows), ratio2):
            sigma = c.to(y.dtype) * v[a:b, None]
            loss -= float(family.log_density(y[a:b], torch.zeros_like(y[a:b]), sigma).sum(dtype=torch.float64))
        return c, loss / y.numel()

    def predict(self, vol):
        """Emit the fitted distribution for rows with volatility ``vol``.

        Parameters
        ----------
        vol : torch.Tensor
            ``(R,)`` positive volatility column.

        Returns
        -------
        MixtureHorizons
            ``K = 1`` Student mixture with scale ``c_h * vol / reference``.

        Raises
        ------
        ValueError
            Before fitting, or for a non-positive volatility.
        """
        import torch
        if self.multipliers is None:
            raise ValueError("fit before predict")
        v = self._positive(torch, vol, vol.shape[0] if isinstance(vol, torch.Tensor) else 0)
        c = self.multipliers.to(device=v.device, dtype=v.dtype)
        sigma = (c[None, :] * (v / self.reference)[:, None]).unsqueeze(-1)
        zeros = torch.zeros_like(sigma)
        return MixtureHorizons(zeros, zeros.clone(), sigma,
                               mixture_family({"kind": "student", "degrees": self.chosen}))

    def state(self):
        """Return the fit as plain JSON-able data."""
        return {"degrees": self.chosen, "multipliers": self.multipliers.detach().cpu().double().tolist(),
                "reference": self.reference, "nll": {str(k): v for k, v in self.nll.items()}}

    @classmethod
    def from_state(cls, state):
        """Rebuild a fit from :meth:`state`.

        Parameters
        ----------
        state : dict
            What :meth:`state` returned, possibly via JSON.

        Returns
        -------
        StudentScaleFit
            The restored fit, ready to ``predict``.
        """
        import torch
        fit = cls(degrees=(state["degrees"],), reference=state["reference"])
        fit.chosen = state["degrees"]
        fit.multipliers = torch.tensor(state["multipliers"], dtype=torch.float64)
        fit.nll = {int(k): v for k, v in state["nll"].items()}
        return fit
