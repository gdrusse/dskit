"""Rolling edge calibration, core mechanism (ADR-0311, stages 1-2).

Does a forecast's predicted edge ``e`` become realized edge ``r``? This
module holds the three domain-neutral pieces that question needs before any
walk exists, all stdlib (stage 1), and the stage-2 pieces built on them (below):

* :class:`SettledBefore` is the one admission gate. A row enters the state
  used at decision time ``t`` only when its label settled STRICTLY before
  ``t``; the boundary is :func:`~dskit.pipeline.kinds_split.label_reaches`
  (owner 7A), so same-day settlement is out. It also owns the data-rights
  window check.
* :class:`ReliabilityBuckets` maps a row to one of 24 buckets from FIXED
  config edges, never from data quantiles, so a bucket means the same thing
  in every week of a walk.
* :class:`LinearEdgeState` is ``r = a + b e`` per bucket and pooled, kept as
  running sums. A cluster ``(name, settlement date)`` arrives whole once it
  settles; nothing is refit over history. The cluster-robust sandwich is
  formed from the same sums: 15 cross-products of the five cluster sums for
  the ``(name, date)`` clusters, and one weighted five-vector per name and per
  date for the two-way band. Exponential forgetting multiplies the sums by
  ``lambda`` per decision week and the cluster cross-products by
  ``lambda ** 2``; because every row of a cluster shares its weight, the
  sandwich equals the explicitly weighted batch one.

* :class:`PartialPooling` shrinks every bucket's line and its through-origin weight toward the
  pooled one (method-of-moments between-bucket variance, floored); :class:`NameAdjustment`
  keeps a shrunk per-name offset on residuals frozen against the bucket-only map of the row's
  own decision date; :class:`EdgeCalibrationMap` freezes the result at one date;
  :class:`TrailingRegime` labels a name high or low against its own trailing median. Every
  float sum over a name or date table runs in sorted key order, so a resumed state equals a
  fresh one under any ``sum``. The walk that drives them is
  :class:`dskit.pipeline.libs.edge_walk.EdgeReliabilityWalk` (tier 2).

Refusals that carry a stable code raise a private ``ValueError`` subclass
whose ``code`` attribute and message prefix are that code.
"""

import bisect
import math
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType

from dskit.pipeline.kinds_split import label_reaches
from dskit.pipeline.records import cluster_ok, number_ok
from dskit.pipeline.stats import NO_VARIANCE_RTOL

__all__ = [
    "DEVELOPMENT_SEAL",
    "LinearEdgeState",
    "ReliabilityBuckets",
    "SettledBefore",
    "kish_effective",
    "PartialPooling",
    "NameAdjustment",
    "EdgeCalibrationMap",
    "TrailingRegime",
]

#: Newest ``sealed_from`` a config may name until the owner lifts the seal (ADR-0311 rule 7).
DEVELOPMENT_SEAL = "2021-07-01"
#: First day that is never readable, whatever the config says (ADR-0311 rule 7).
_HARD_DATA_END = "2024-01-01"
_REGIMES = ("low", "high")
_WAYS = ("one", "two")


class _Refusal(ValueError):
    """A refusal with a stable ``code`` (also the message prefix)."""

    def __init__(self, code, detail=""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def _iso(value, what):
    """Return ``value`` (a ``YYYY-MM-DD`` string or a ``date``) as an ISO string."""
    if isinstance(value, datetime):
        raise ValueError(f"{what} must be a date or YYYY-MM-DD string, got a datetime")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and len(value) == 10:
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    raise ValueError(f"{what} must be a date or a YYYY-MM-DD string, got {value!r}")


class SettledBefore:
    """The admission gate: a row counts at ``t`` only if it settled before ``t``.

    Parameters
    ----------
    sealed_from : str or datetime.date
        First date the run may not read, required, at most
        :data:`DEVELOPMENT_SEAL`.

    Raises
    ------
    ValueError
        ``sealed_from_missing`` when absent or empty; ``development_seal_exceeded``
        when later than :data:`DEVELOPMENT_SEAL`; a plain error when malformed.

    Examples
    --------
    A settlement on the decision day is not admitted; the day before is::

        gate = SettledBefore("2021-07-01")
        gate.admits("2020-03-05", "2020-03-06")  # True
        gate.admits("2020-03-06", "2020-03-06")  # False
    """

    def __init__(self, sealed_from):
        if sealed_from is None or sealed_from == "":
            raise _Refusal("sealed_from_missing", "sealed_from is required")
        self.sealed_from = _iso(sealed_from, "sealed_from")
        if label_reaches(self.sealed_from, DEVELOPMENT_SEAL) and self.sealed_from != DEVELOPMENT_SEAL:
            raise _Refusal("development_seal_exceeded",
                           f"sealed_from {self.sealed_from} is after {DEVELOPMENT_SEAL}")

    def admits(self, s, t):
        """Say whether a row settling at ``s`` may enter the state used at ``t``.

        Parameters
        ----------
        s : str or datetime.date
            The settlement date of the row.
        t : str or datetime.date
            The decision date the state is for.

        Returns
        -------
        bool
            True only when ``s`` is strictly before ``t``.

        Raises
        ------
        ValueError
            When either date is malformed.
        """
        return not label_reaches(_iso(s, "s"), _iso(t, "t"))

    def check_window(self, dates):
        """Refuse any date the run has no right to read.

        Parameters
        ----------
        dates : iterable of str or datetime.date
            Every decision, fill, expiry and settlement date of the run.

        Raises
        ------
        ValueError
            ``window_outside_data_rights`` for a date on or after ``sealed_from``
            or on or after the hard end of the data, whatever the config.
        """
        for d in dates:
            day = _iso(d, "date")
            if label_reaches(day, self.sealed_from) or label_reaches(day, _HARD_DATA_END):
                raise _Refusal("window_outside_data_rights",
                               f"{day} is on or after sealed_from {self.sealed_from}")


def _edges(params, key, length):
    """Validate one fixed, strictly increasing numeric edge list of ``length``."""
    value = params.get(key)
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise _Refusal("bucket_edges_invalid", f"{key} must be a list of {length} numbers, got {value!r}")
    for x in value:
        if isinstance(x, bool) or not number_ok(x):
            raise _Refusal("bucket_edges_invalid",
                           f"{key} holds {x!r}: fixed finite numbers only, no quantile spec")
    if any(b <= a for a, b in zip(value, value[1:])):
        raise _Refusal("bucket_edges_invalid", f"{key} must be strictly increasing, got {list(value)!r}")
    return tuple(float(x) for x in value)


class ReliabilityBuckets:
    """24 fixed buckets: DTE (3) x ``|z|`` (2) x relative spread (2) x regime (2).

    DTE edges start a bin (``[8, 22)`` is the middle one); the ``|z|`` and
    spread edges belong to the lower bin (``|z| <= 1`` is inner). The edges
    are config values, never fitted.

    Parameters
    ----------
    params : dict
        ``dte_edges`` (2 increasing numbers), ``abs_z_edges`` (1) and
        ``spread_edges`` (1), all required.

    Raises
    ------
    ValueError
        ``bucket_edges_invalid`` for missing, unsorted, non-finite, non-numeric
        (a quantile spec) or wrong-sized edges; a plain error for an unknown key.

    Examples
    --------
    The ADR defaults::

        buckets = ReliabilityBuckets(
            {"dte_edges": [8, 22], "abs_z_edges": [1.0], "spread_edges": [0.15]})
        buckets.of(10, 0.4, 0.05, "low")  # 8
    """

    _PARAMS = ("dte_edges", "abs_z_edges", "spread_edges")

    def __init__(self, params):
        if not isinstance(params, dict):
            raise ValueError(f"params must be a dict, got {type(params).__name__}")
        unknown = sorted(set(params) - set(self._PARAMS))
        if unknown:
            raise ValueError(f"unknown params {unknown}; allowed {list(self._PARAMS)}")
        self._dte = _edges(params, "dte_edges", 2)
        self._z = _edges(params, "abs_z_edges", 1)
        self._spread = _edges(params, "spread_edges", 1)

    @staticmethod
    def _cell(value, what, low=None):
        """Return ``value`` as a float when it is a finite number at least ``low``."""
        if isinstance(value, bool) or not number_ok(value) or (low is not None and value < low):
            raise ValueError(f"{what} must be a finite number"
                             + ("" if low is None else f" >= {low}") + f", got {value!r}")
        return float(value)

    def of(self, dte, abs_z, spread, regime):
        """Return the bucket index of one row.

        Parameters
        ----------
        dte : float
            Calendar days to expiry.
        abs_z : float
            Absolute moneyness, at least 0.
        spread : float
            Relative spread ``(ask - bid) / mid``.
        regime : str
            ``"low"`` or ``"high"``.

        Returns
        -------
        int
            The index in 0..23, ``((dte_bin * 2 + z_bin) * 2 + spread_bin) * 2 + regime_bin``.

        Raises
        ------
        ValueError
            On a non-finite input, a negative ``abs_z`` or an unknown regime.
        """
        d = self._cell(dte, "dte")
        z = self._cell(abs_z, "abs_z", 0.0)
        s = self._cell(spread, "spread")
        if regime not in _REGIMES:
            raise ValueError(f"regime must be one of {list(_REGIMES)}, got {regime!r}")
        d_bin = sum(d >= edge for edge in self._dte)
        z_bin = sum(z > edge for edge in self._z)
        s_bin = sum(s > edge for edge in self._spread)
        return ((d_bin * 2 + z_bin) * 2 + s_bin) * 2 + _REGIMES.index(regime)


_FIT_RTOL = NO_VARIANCE_RTOL
_STRATA = ("buy", "sell", "top")


def _solve(m, rhs):
    """Solve ``m x = rhs`` by Gaussian elimination with partial pivoting, for any dimension.

    ``rhs`` is a vector or a matrix (a list of rows); the answer has the same shape.

    Raises
    ------
    ValueError
        When ``m`` is singular (a pivot is zero or not finite).
    """
    n = len(m)
    matrix = isinstance(rhs[0], (list, tuple))
    cols = len(rhs[0]) if matrix else 1
    a = [list(map(float, m[i])) + ([float(x) for x in rhs[i]] if matrix else [float(rhs[i])]) for i in range(n)]
    for c in range(n):
        piv = max(range(c, n), key=lambda i: abs(a[i][c]))
        if not abs(a[piv][c]) > 0.0 or not math.isfinite(a[piv][c]):
            raise ValueError("singular matrix")
        a[c], a[piv] = a[piv], a[c]
        for i in range(c + 1, n):
            f = a[i][c] / a[c][c]
            if f:
                for j in range(c, n + cols):
                    a[i][j] -= f * a[c][j]
    x = [[0.0] * cols for _ in range(n)]
    for col in range(cols):
        for i in range(n - 1, -1, -1):
            s = a[i][n + col]
            for j in range(i + 1, n):
                s -= a[i][j] * x[j][col]
            x[i][col] = s / a[i][i]
    return [row[0] for row in x] if not matrix else x


def kish_effective(weight_sum, weight_sq_sum):
    """Return the Kish effective count ``weight_sum ** 2 / weight_sq_sum`` (``0.0`` when the squares sum to 0).

    The one owner of every effective count in this module: row counts, cluster counts, the CR1 factor,
    degrees of freedom and the ``min_clusters`` / ``min_blocks`` / ``name_min_clusters`` gates. The ratio is
    unchanged by a common factor on all weights, so it is the same in reference units under lazy decay.

    Parameters
    ----------
    weight_sum : float
        The sum of the weights (rows) or of the units' total weights (clusters).
    weight_sq_sum : float
        The sum of the squared weights.

    Returns
    -------
    float
        The effective count; equal to the raw count when all weights are equal.
    """
    if not weight_sq_sum > 0.0:
        return 0.0
    return weight_sum * weight_sum / weight_sq_sum


def _mat_mul(a, b):
    """Product of two matrices given as lists of rows."""
    return [[sum(a[p][k] * b[k][q] for k in range(len(b))) for q in range(len(b[0]))] for p in range(len(a))]


def _det(m):
    """Return the determinant by elimination with partial pivoting (small matrices)."""
    n, a, d = len(m), [list(map(float, r)) for r in m], 1.0
    for c in range(n):
        piv = max(range(c, n), key=lambda i: abs(a[i][c]))
        if a[piv][c] == 0.0:
            return 0.0
        if piv != c:
            a[c], a[piv] = a[piv], a[c]
            d = -d
        d *= a[c][c]
        for i in range(c + 1, n):
            f = a[i][c] / a[c][c]
            for j in range(c, n):
                a[i][j] -= f * a[c][j]
    return d


def _positive_definite(m):
    """Say whether every leading principal minor of the symmetric ``m`` is positive."""
    return all(_det([row[:i] for row in m[:i]]) > 0.0 for i in range(1, len(m) + 1))


class _Layout:
    """Index map of a cluster's moment vector for ``k`` regressors.

    ``k = 1``: ``(N, E, R, EE, ER)``; ``k = 2``: ``(N, D, S, DD, DS, SS, R, DR, SR)``
    (``D`` the direction edge, ``S`` the shape edge). ``N`` is the total row weight.
    """

    def __init__(self, k):
        self.k = k
        if k == 1:
            self.z, self.r, self.zr, self.zz = (1,), 2, (4,), {(0, 0): 3}
            self.size = 5
        else:
            self.z, self.r, self.zr = (1, 2), 6, (7, 8)
            self.zz = {(0, 0): 3, (0, 1): 4, (1, 0): 4, (1, 1): 5}
            self.size = 9
        self.tri = [(i, j) for i in range(self.size) for j in range(i, self.size)]

    def row_vector(self, w, z, r):
        """Return the weighted moment vector of one row with regressors ``z`` and outcome ``r``."""
        out = [0.0] * self.size
        out[0] = w
        for j, zj in enumerate(z):
            out[self.z[j]] = w * zj
            out[self.zr[j]] = w * zj * r
            for ll in range(j, len(z)):
                out[self.zz[(j, ll)]] = w * zj * z[ll]
        out[self.r] = w * r
        return out

    def gram(self, vec):
        """Return the ``(k+1) x (k+1)`` matrix ``X'WX`` of a moment vector."""
        k = self.k
        g = [[0.0] * (k + 1) for _ in range(k + 1)]
        g[0][0] = vec[0]
        for j in range(k):
            g[0][j + 1] = g[j + 1][0] = vec[self.z[j]]
            for ll in range(k):
                g[j + 1][ll + 1] = vec[self.zz[(j, ll)]]
        return g

    def xr(self, vec):
        """Return the vector ``X'Wr`` of a moment vector."""
        return [vec[self.r]] + [vec[i] for i in self.zr]

    def score(self, theta, vec):
        """Return the OLS score ``X'W(r - X theta) = M(theta) vec`` of a moment vector."""
        g, u = self.gram(vec), self.xr(vec)
        return [u[i] - sum(g[i][j] * theta[j] for j in range(self.k + 1)) for i in range(self.k + 1)]

    def score_map(self, theta):
        """Return the ``(k+1) x size`` matrix ``M(theta)`` taking a moment vector to its score."""
        k = self.k
        m = [[0.0] * self.size for _ in range(k + 1)]
        m[0][0] = -theta[0]
        m[0][self.r] = 1.0
        for j in range(k):
            m[0][self.z[j]] = -theta[j + 1]
            m[j + 1][self.z[j]] = -theta[0]
            m[j + 1][self.zr[j]] = 1.0
            for ll in range(k):
                m[j + 1][self.zz[(j, ll)]] -= theta[ll + 1]
        return m


_LAYOUTS = {1: _Layout(1), 2: _Layout(2)}


def _outer_from_cross(lay, cross, theta):
    """``M C M'`` for the symmetric cross-product ``cross`` of the moment vectors."""
    p = lay.size
    c = [[0.0] * p for _ in range(p)]
    for idx, (i, j) in enumerate(lay.tri):
        c[i][j] = c[j][i] = cross[idx]
    m = lay.score_map(theta)
    mc = _mat_mul(m, c)
    return [[sum(mc[a][j] * m[b][j] for j in range(p)) for b in range(len(m))] for a in range(len(m))]


def _outer_from_vectors(lay, vectors, theta):
    """``sum s s'`` over the scores ``s`` of the moment vectors."""
    q = lay.k + 1
    out = [[0.0] * q for _ in range(q)]
    for x in vectors:
        s = lay.score(theta, x)
        for a in range(q):
            for b in range(q):
                out[a][b] += s[a] * s[b]
    return out


def _cr1(g_eff, n_eff, k):
    """Return the CR1 factor ``G_eff/(G_eff-1) * (n_eff-1)/(n_eff-k-1)``."""
    return g_eff / (g_eff - 1) * (n_eff - 1) / (n_eff - k - 1)


def _sandwich_matrix(bread, meat, g_eff, n_eff, k=1):
    """Return the CR1 sandwich ``cr1 * bread meat bread`` with Kish effective counts."""
    factor = _cr1(g_eff, n_eff, k)
    bm = _mat_mul(bread, meat)
    full = _mat_mul(bm, bread)
    return [[factor * v for v in row] for row in full]


class _Table:
    """A dimension of cluster units (names, dates, ...): key -> weighted moment vector."""

    def __init__(self, p):
        self.p = p
        self.cells = {}
        self.w2 = 0.0  # running sum over cells of (total weight N)^2, for the Kish count

    @property
    def count(self):
        return len(self.cells)

    def add(self, key, vec):
        cell = self.cells.setdefault(key, [0.0] * self.p)
        old = cell[0]
        for q in range(self.p):
            cell[q] += vec[q]
        self.w2 += cell[0] * cell[0] - old * old

    def scale(self, f):
        for key, cell in self.cells.items():
            self.cells[key] = [v * f for v in cell]
        self.w2 *= f * f

    def sorted_vectors(self):
        return [self.cells[k] for k in sorted(self.cells)]

    def to_dict(self):
        return {str(k): list(self.cells[k]) for k in sorted(self.cells)}

    @classmethod
    def from_dict(cls, p, blob, w2):
        t = cls(p)
        t.cells = {k: [float(v) for v in vec] for k, vec in blob.items()}
        t.w2 = float(w2)
        return t


class _Unit:
    """Running sums of one regression: a bucket, or the pooled rows."""

    def __init__(self, lay):
        self.lay = lay
        self.tot = [0.0] * (lay.size + 1)  # the moment vector, then r*r (weighted)
        self.serr = 0.0  # sum w sigma^2 of the direction regressor
        self.rw2 = 0.0  # sum w^2 over rows (the row Kish count)
        self.cw2 = 0.0  # sum over (name, date) clusters of (total weight)^2 (the cluster Kish count)
        self.groups = 0.0  # undecayed sum of base weights: the raw group count under group_equal
        self.cross = [0.0] * len(lay.tri)  # sum over (name, date) clusters of x x'
        self.names = _Table(lay.size)
        self.dates = _Table(lay.size)
        self.rows = 0  # unweighted counts
        self.clusters = 0

    def add(self, name, day, vec, rr, serr, rows, rw2, groups):
        """Fold one whole (name, date) cluster, given as its moment vector, into the sums."""
        for q in range(self.lay.size):
            self.tot[q] += vec[q]
        self.tot[self.lay.size] += rr
        self.serr += serr
        self.rw2 += rw2
        self.cw2 += vec[0] * vec[0]
        self.groups += groups
        for idx, (i, j) in enumerate(self.lay.tri):
            self.cross[idx] += vec[i] * vec[j]
        self.names.add(name, vec)
        self.dates.add(day, vec)
        self.rows += rows
        self.clusters += 1

    def decay(self, lam):
        """Age every sum by one decision week."""
        self.tot = [v * lam for v in self.tot]
        self.serr *= lam
        self.rw2 *= lam * lam
        self.cw2 *= lam * lam
        self.cross = [v * lam * lam for v in self.cross]
        self.names.scale(lam)
        self.dates.scale(lam)

    def to_dict(self):
        return {
            "tot": list(self.tot), "serr": self.serr, "rw2": self.rw2, "cw2": self.cw2, "groups": self.groups,
            "cross": list(self.cross), "rows": self.rows,
            "clusters": self.clusters, "names": self.names.to_dict(), "dates": self.dates.to_dict(),
            "names_w2": self.names.w2, "dates_w2": self.dates.w2,
        }

    @classmethod
    def from_dict(cls, lay, blob):
        unit = cls(lay)
        unit.tot = [float(v) for v in blob["tot"]]
        unit.serr, unit.rw2 = float(blob["serr"]), float(blob["rw2"])
        unit.cw2, unit.groups = float(blob["cw2"]), float(blob["groups"])
        unit.cross = [float(v) for v in blob["cross"]]
        unit.rows, unit.clusters = int(blob["rows"]), int(blob["clusters"])
        unit.names = _Table.from_dict(lay.size, blob["names"], blob["names_w2"])
        unit.dates = _Table.from_dict(lay.size, blob["dates"], blob["dates_w2"])
        if len(unit.tot) != lay.size + 1 or len(unit.cross) != len(lay.tri):
            raise ValueError("unit sums have the wrong size")
        return unit


class LinearEdgeState:
    """``r = a + b e`` (or ``r = a + b_dir e_dir + b_shape e_shape``) per bucket and pooled, from running sums.

    The clock is the sequence of decision weeks the caller advances through:
    each :meth:`advance` to a later ``t`` ages a decayed state by one week
    (call it for every week, empty ones too), then that week's settled
    clusters are added. A ``(name, settlement date)`` cluster arrives whole,
    once. The one-way band clusters on ``(name, date)``; the two-way band is
    Cameron-Gelbach-Miller ``name + date - (name, date)``, the same rule as
    :func:`~dskit.pipeline.ordering.clustered_calibration_slope`.

    With ``regressors = 2`` the row's regressors are ``(e_dir, e_shape)`` with
    ``e_shape = e - e_dir`` formed here (one owner); the fit is a 3x3 solve.

    Parameters
    ----------
    n_buckets : int
        Number of buckets (at least 1); bucket indices run ``0..n_buckets-1``.
    decay : float or None
        ``None`` for the expanding state, else the weekly factor ``lambda`` in (0, 1).
    regressors : int
        1 (``e``) or 2 (``e_dir`` and ``e - e_dir``).
    block_lengths : int
        How many block indices each cluster carries (at least 1).

    Raises
    ------
    ValueError
        On a bad bucket count, decay, regressor count or block count.

    Examples
    --------
    One cluster settled before the decision week::

        state = LinearEdgeState(24, None)
        state.advance("2020-03-06")
        state.add_cluster("2020-03-06", "AAA", "2020-03-01", "2020-02-26",
                          [3, 3, 7], [1.0, 2.0, 0.5], [0.8, 2.5, 0.1])
        state.fit(None)["n"]  # -> 3.0
    """

    def __init__(self, n_buckets, decay, regressors=1, block_lengths=1):
        if isinstance(n_buckets, bool) or not isinstance(n_buckets, int) or n_buckets < 1:
            raise ValueError(f"n_buckets must be an integer >= 1, got {n_buckets!r}")
        if decay is not None and (isinstance(decay, bool) or not number_ok(decay) or not 0.0 < decay < 1.0):
            raise ValueError(f"decay must be None or a number in (0, 1), got {decay!r}")
        if isinstance(regressors, bool) or regressors not in (1, 2):
            raise ValueError(f"regressors must be 1 or 2, got {regressors!r}")
        if isinstance(block_lengths, bool) or not isinstance(block_lengths, int) or block_lengths < 1:
            raise ValueError(f"block_lengths must be an integer >= 1, got {block_lengths!r}")
        self.n_buckets = n_buckets
        self.decay = None if decay is None else float(decay)
        self.regressors = regressors
        self.block_lengths = block_lengths
        self._lay = _LAYOUTS[regressors]
        self._t = None
        self._pooled = _Unit(self._lay)
        self._buckets = {}
        self._seen = set()
        #: Rows counted, never silent: ``sigma2_missing`` (a direction row without a forward error variance).
        self.counts = {"sigma2_missing": 0}

    def advance(self, t):
        """Move to decision week ``t``, aging a decayed state by one week.

        Parameters
        ----------
        t : str or datetime.date
            The decision date; the same date again is a no-op.

        Raises
        ------
        ValueError
            When ``t`` is malformed or earlier than the current week.
        """
        day = _iso(t, "t")
        if self._t is None:
            self._t = day
        elif day < self._t:
            raise ValueError(f"time cannot run backward: {day} is before {self._t}")
        elif day > self._t:
            if self.decay is not None:
                for unit in (self._pooled, *self._buckets.values()):
                    unit.decay(self.decay)
            self._t = day

    def _rows(self, buckets, e, r, e_dir, sigma2):
        """Validate one cluster's rows and return the finite floats ``(e, r, e_dir, sigma2)``."""
        m = len(e) if isinstance(e, (list, tuple)) else -1
        if m < 1 or not all(isinstance(v, (list, tuple)) and len(v) == m for v in (buckets, r)):
            raise ValueError("buckets, e and r must be non-empty lists of the same length")
        for b in buckets:
            if isinstance(b, bool) or not isinstance(b, int) or not 0 <= b < self.n_buckets:
                raise ValueError(f"bucket {b!r} is not an integer in 0..{self.n_buckets - 1}")
        for v in (*e, *r):
            if isinstance(v, bool) or not number_ok(v):
                raise ValueError(f"e and r must be finite numbers, got {v!r}")
        if self.regressors == 1:
            if e_dir is not None or sigma2 is not None:
                raise ValueError("e_dir and sigma2 belong to a two-regressor state")
            return [float(v) for v in e], [float(v) for v in r], None, None
        if not isinstance(e_dir, (list, tuple)) or len(e_dir) != m:
            raise ValueError("a two-regressor state needs e_dir, a list of the same length as e")
        for v in e_dir:
            if isinstance(v, bool) or not number_ok(v):
                raise ValueError(f"e_dir must be finite numbers, got {v!r}")
        if sigma2 is not None:
            if not isinstance(sigma2, (list, tuple)) or len(sigma2) != m:
                raise ValueError("sigma2 must be a list of the same length as e")
            for v in sigma2:
                if v is not None and (isinstance(v, bool) or not number_ok(v) or v < 0):
                    raise ValueError(f"sigma2 must be None or a finite number >= 0, got {v!r}")
        return ([float(v) for v in e], [float(v) for v in r], [float(v) for v in e_dir],
                None if sigma2 is None else [None if v is None else float(v) for v in sigma2])

    @staticmethod
    def _per_row(values, m, what, ok):
        """Return ``values`` (a list of ``m`` entries each passing ``ok``), or ``None`` when absent."""
        if values is None:
            return None
        if not isinstance(values, (list, tuple)) or len(values) != m or not all(ok(v) for v in values):
            raise ValueError(f"{what} must be a list of {m} valid entries, got {values!r}")
        return list(values)

    def add_cluster(self, t, name, settle_date, fill_date, buckets, e, r, blocks=None, e_dir=None,
                    weights=None, sides=None, top=None, sigma2=None):
        """Fold one whole ``(name, settle_date)`` cluster into the state at ``t``.

        Parameters
        ----------
        t : str or datetime.date
            The decision week the state is for (advanced to if later).
        name : str
            The cluster's name.
        settle_date : str or datetime.date
            When the rows settled; must be strictly before ``t``.
        fill_date : str or datetime.date
            The fill session; must be strictly before ``settle_date``.
        buckets : list of int
            The bucket of each row.
        e : list of float
            Predicted edge per row.
        r : list of float
            Realized edge per row.
        blocks : list of int, optional
            One non-negative block index per configured block length.
        e_dir : list of float, optional
            Direction edge per row (required with ``regressors = 2``, refused with 1).
        weights : list of float, optional
            Per-row base weight, positive and finite (default 1); fixed at arrival, only the common decay
            factor ages. With ``group_equal`` a row carries ``1 / m_g`` of its group of ``m_g`` rows.
        sides : list of int, optional
            Per-row side, +1 (buy) or -1 (sell); feeds the ``"buy"`` and ``"sell"`` units (default none).
        top : list of bool, optional
            Per-row top-decile flag; feeds the ``"top"`` unit (default none).
        sigma2 : list of float or None, optional
            Per-row error variance of the direction regressor (two-regressor states).

        Raises
        ------
        ValueError
            ``state_from_future`` when ``settle_date >= t`` or ``fill_date >=
            settle_date``; a plain error for a malformed cluster, one already
            added, or a ``t`` before the current week. The state is unchanged.
        """
        day, s, f = _iso(t, "t"), _iso(settle_date, "settle_date"), _iso(fill_date, "fill_date")
        if label_reaches(s, day):
            raise _Refusal("state_from_future", f"settlement {s} is not before t {day}")
        if label_reaches(f, s):
            raise _Refusal("state_from_future", f"fill {f} is not before settlement {s}")
        if not cluster_ok(name):
            raise ValueError(f"name must be a non-empty string, got {name!r}")
        ev, rv, dv, sv = self._rows(buckets, e, r, e_dir, sigma2)
        m = len(ev)
        wv = self._per_row(weights, m, "weights", lambda v: not isinstance(v, bool) and number_ok(v) and v > 0)
        sd = self._per_row(sides, m, "sides", lambda v: isinstance(v, int) and not isinstance(v, bool) and v in (1, -1))
        tp = self._per_row(top, m, "top", lambda v: isinstance(v, bool))
        if (name, s) in self._seen:
            raise ValueError(f"cluster ({name}, {s}) was already added: a cluster arrives whole, once")
        self.advance(day)
        self._seen.add((name, s))
        lay = self._lay
        acc = {}
        for i, b in enumerate(buckets):
            w = 1.0 if wv is None else float(wv[i])
            z = [ev[i]] if dv is None else [dv[i], ev[i] - dv[i]]
            row = lay.row_vector(w, z, rv[i])
            serr = 0.0
            if sv is not None:
                if sv[i] is None:
                    self.counts["sigma2_missing"] += 1
                else:
                    serr = w * sv[i]
            keys = [None, b]
            if sd is not None:
                keys.append("buy" if sd[i] == 1 else "sell")
            if tp is not None and tp[i]:
                keys.append("top")
            for key in keys:
                a = acc.setdefault(key, [[0.0] * lay.size, 0.0, 0.0, 0, 0.0, 0.0])
                for q in range(lay.size):
                    a[0][q] += row[q]
                a[1] += w * rv[i] * rv[i]
                a[2] += serr
                a[3] += 1
                a[4] += w * w
                a[5] += w
        for key, (vec, rr, serr, rows, rw2, groups) in acc.items():
            unit = self._pooled if key is None else self._buckets.setdefault(key, _Unit(lay))
            unit.add(name, s, vec, rr, serr, rows, rw2, groups)

    def _unit(self, b):
        """Return the sums of bucket ``b`` (``None`` pooled; ``"buy"``, ``"sell"``, ``"top"``); an unused unit is empty."""
        if b is None:
            return self._pooled
        if b in _STRATA:
            return self._buckets.get(b) or _Unit(self._lay)
        if isinstance(b, bool) or not isinstance(b, int) or not 0 <= b < self.n_buckets:
            raise ValueError(f"bucket must be None, one of {list(_STRATA)} or an integer in 0..{self.n_buckets - 1}, got {b!r}")
        return self._buckets.get(b) or _Unit(self._lay)

    @staticmethod
    def _effective(u):
        """Return the Kish counts of a unit: rows, groups and the cluster dimensions (part C)."""
        n = u.tot[0]
        return {"n_eff": kish_effective(n, u.rw2), "n_groups": u.groups, "g_eff": kish_effective(n, u.cw2),
                "g_eff_names": kish_effective(n, u.names.w2), "g_eff_dates": kish_effective(n, u.dates.w2)}

    def _centred(self, u):
        """``(theta, centred Gram, centred cross, z sums)`` of a unit, or ``None`` when it cannot be fitted."""
        lay, v = u.lay, u.tot
        n, k = v[0], lay.k
        if u.rows == 0 or not n > 0.0:
            return None
        z = [v[i] for i in lay.z]
        c = [[v[lay.zz[(i, j)]] - z[i] * z[j] / n for j in range(k)] for i in range(k)]
        if any(c[i][i] <= _FIT_RTOL * v[lay.zz[(i, i)]] for i in range(k)):
            return None
        if k == 2 and c[0][0] * c[1][1] - c[0][1] * c[1][0] <= _FIT_RTOL * c[0][0] * c[1][1]:
            return None
        cz = [v[lay.zr[j]] - z[j] * v[lay.r] / n for j in range(k)]
        beta = _solve(c, cz)
        a = (v[lay.r] - sum(beta[j] * z[j] for j in range(k))) / n
        return [a, *beta], c, cz, z

    def fit(self, b):
        """Return the least-squares fit of one bucket (``None`` for the pooled rows).

        Parameters
        ----------
        b : int or None
            The bucket index, or ``None`` for pooled.

        Returns
        -------
        dict
            ``n`` (weighted rows), ``rows`` and ``clusters`` (raw counts),
            ``dates`` and ``names`` (distinct, raw), the Kish counts ``n_eff``, ``g_eff``,
            ``g_eff_names``, ``g_eff_dates``, the raw group count ``n_groups``, ``theta`` (``None`` when a
            regressor has no variance, the two are exactly collinear or there
            are no rows), ``a`` and ``b`` (one regressor) or ``a``, ``b_dir``,
            ``b_shape``, ``reliability_dir``, ``b_dir_corrected`` and
            ``corr_dir_shape`` (two regressors), ``sse``.

        Raises
        ------
        ValueError
            On a bucket outside the state.
        """
        u = self._unit(b)
        lay, v = u.lay, u.tot
        out = {"n": v[0], "rows": u.rows, "clusters": u.clusters, "dates": u.dates.count,
               "names": u.names.count, "theta": None, "a": None, "sse": None, **self._effective(u)}
        if lay.k == 1:
            out["b"] = None
        else:
            out.update(b_dir=None, b_shape=None, reliability_dir=None, b_dir_corrected=None, corr_dir_shape=None)
        got = self._centred(u)
        if got is None:
            return out
        theta, c, _, _ = got
        sse = v[lay.size] - theta[0] * v[lay.r] - sum(theta[j + 1] * v[lay.zr[j]] for j in range(lay.k))
        out.update(theta=theta, a=theta[0], sse=sse)
        if lay.k == 1:
            out["b"] = theta[1]
            return out
        rel = max((c[0][0] - u.serr) / c[0][0], 0.0)
        out.update(b_dir=theta[1], b_shape=theta[2], reliability_dir=rel,
                   b_dir_corrected=None if rel <= 0.0 else theta[1] / rel,
                   corr_dir_shape=c[0][1] / math.sqrt(c[0][0] * c[1][1]))
        return out

    def sandwich(self, b, theta, way):
        """Return the CR1 cluster-robust covariance of ``theta`` for a bucket.

        Parameters
        ----------
        b : int or None
            The bucket index, or ``None`` for pooled.
        theta : sequence of float
            The ``(a, b)`` or ``(a, b_dir, b_shape)`` point the scores are
            taken at (the unit's own fit, or a shrunk one).
        way : str
            ``"one"`` for ``(name, settlement date)`` clusters, ``"two"`` for
            name plus date minus ``(name, date)``.

        Returns
        -------
        dict or None
            ``cov``, ``rule`` (``"one_way"``, ``"two_way"`` or ``"max_one_way"``
            when the two-way matrix is not positive definite), ``df`` (effective
            clusters - 1; two-way: fewest effective names and dates - 1),
            ``n_clusters`` (raw) and ``g_eff``. The CR1 factor is
            ``G_eff/(G_eff-1) (n_eff-1)/(n_eff-k-1)`` with the Kish counts of part C.
            ``None`` when there are too few rows or clusters (raw, or effective
            below ``k + 2`` rows and 2 clusters), or a regressor has no variance.

        Raises
        ------
        ValueError
            On an unknown ``way``, a bucket outside the state or a bad ``theta``.
        """
        if way not in _WAYS:
            raise ValueError(f"way must be one of {list(_WAYS)}, got {way!r}")
        k = self.regressors
        if not (isinstance(theta, (list, tuple)) and len(theta) == k + 1 and all(
                number_ok(x) and not isinstance(x, bool) for x in theta)):
            raise ValueError(f"theta must be {k + 1} finite numbers, got {theta!r}")
        u = self._unit(b)
        lay = u.lay
        if u.rows < k + 2 or u.clusters < 2 or self._centred(u) is None:
            return None
        eff = self._effective(u)
        if eff["n_eff"] < k + 2 or eff["g_eff"] < 2.0:
            return None
        bread = _solve(lay.gram(u.tot), [[1.0 if i == j else 0.0 for j in range(k + 1)] for i in range(k + 1)])
        theta = [float(x) for x in theta]
        n_eff = eff["n_eff"]
        pair = _sandwich_matrix(bread, _outer_from_cross(lay, u.cross, theta), eff["g_eff"], n_eff, k)
        if way == "one":
            return {"cov": pair, "rule": "one_way", "df": eff["g_eff"] - 1, "n_clusters": [u.clusters],
                    "g_eff": [eff["g_eff"]]}
        gn, gd = u.names.count, u.dates.count
        if gn < 2 or gd < 2 or eff["g_eff_names"] <= 1.0 or eff["g_eff_dates"] <= 1.0:
            return None
        by_name = _sandwich_matrix(bread, _outer_from_vectors(lay, u.names.sorted_vectors(), theta),
                                   eff["g_eff_names"], n_eff, k)
        by_date = _sandwich_matrix(bread, _outer_from_vectors(lay, u.dates.sorted_vectors(), theta),
                                   eff["g_eff_dates"], n_eff, k)
        cov = [[by_name[p][q] + by_date[p][q] - pair[p][q] for q in range(k + 1)] for p in range(k + 1)]
        rule = "two_way"
        if not _positive_definite(cov):
            cov = max(by_name, by_date, key=lambda m: m[1][1])
            rule = "max_one_way"
        return {"cov": cov, "rule": rule, "df": min(eff["g_eff_names"], eff["g_eff_dates"]) - 1,
                "n_clusters": [gn, gd, u.clusters], "g_eff": [eff["g_eff_names"], eff["g_eff_dates"], eff["g_eff"]]}

    def to_dict(self):
        """Return the whole state as JSON-safe sums (no row history).

        Returns
        -------
        dict
            Restorable with :meth:`from_dict`; keys are sorted so equal states dump equal.
        """
        return {
            "n_buckets": self.n_buckets, "decay": self.decay, "t": self._t,
            "regressors": self.regressors, "block_lengths": self.block_lengths, "counts": dict(self.counts),
            "seen": sorted([name, day] for name, day in self._seen),
            "pooled": self._pooled.to_dict(),
            "buckets": {str(k): self._buckets[k].to_dict() for k in sorted(self._buckets, key=str)},
        }

    @classmethod
    def from_dict(cls, blob):
        """Rebuild a state from :meth:`to_dict`.

        Parameters
        ----------
        blob : dict
            A dump from :meth:`to_dict`.

        Returns
        -------
        LinearEdgeState
            A state that continues exactly as the dumped one would.

        Raises
        ------
        ValueError
            On a missing key or a malformed blob (including a dump from before
            the two-regressor layout).
        """
        try:
            state = cls(blob["n_buckets"], blob["decay"], blob["regressors"], blob["block_lengths"])
            state._t = blob["t"]
            state.counts = {k: int(v) for k, v in blob["counts"].items()}
            state._seen = {(name, day) for name, day in blob["seen"]}
            state._pooled = _Unit.from_dict(state._lay, blob["pooled"])
            state._buckets = {(k if k in _STRATA else int(k)): _Unit.from_dict(state._lay, v)
                              for k, v in blob["buckets"].items()}
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed LinearEdgeState dump: {exc!r}") from exc
        return state


# ---------------------------------------------------------------- stage 2a: pooling, names, the map


def _count_param(params, key, low, integer):
    """Validate one numeric param: finite, at least ``low`` (strictly when ``low`` is 0.0 and float)."""
    v = params[key]
    if isinstance(v, bool) or not number_ok(v) or (integer and not isinstance(v, int)):
        raise ValueError(f"{key} must be a finite {'integer' if integer else 'number'}, got {v!r}")
    if v < low or (not integer and low == 0.0 and v <= 0.0):
        raise ValueError(f"{key} must be {'>=' if integer or low else '>'} {low}, got {v!r}")
    return v


def _checked_params(params, allowed):
    """Return ``params`` after refusing a non-dict, an unknown key or a missing key."""
    if not isinstance(params, dict):
        raise ValueError(f"params must be a dict, got {type(params).__name__}")
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        raise ValueError(f"unknown params {unknown}; allowed {list(allowed)}")
    missing = [k for k in allowed if k not in params]
    if missing:
        raise ValueError(f"missing params {missing}")
    return params


class PartialPooling:
    """Partially pooled ``(a_b, b_b)`` per bucket, from a :class:`LinearEdgeState`.

    Each bucket's fit ``theta_b`` with sandwich covariance ``V_b`` (the
    two-way band, owner B-2) is shrunk toward the pooled ``theta_0``::

        theta* = theta_b + V_b (T + V_b)^-1 (theta_0 - theta_b)
        cov*   = T (T + V_b)^-1 V_b

    which equals ``(T^-1 + V^-1)^-1 (T^-1 theta_0 + V^-1 theta_b)`` but needs
    no inverse of ``V_b``, so a bucket that fits exactly (``V_b = 0``) keeps
    its fit. ``T = diag(tau_a^2, tau_b^2)`` is the method-of-moments
    between-bucket variance (variance of ``theta_b`` across the eligible
    buckets minus the mean of their sampling variances), each entry floored at
    ``tau2_floor``; with fewer than two eligible buckets ``T`` is the floor. A
    bucket below ``min_clusters`` clusters, with no fit (zero ``e`` variance)
    or no sandwich takes ``theta_0``. Until the pooled state has ``min_dates``
    settlement dates and ``min_clusters`` clusters the prior holds: every
    ``a``, ``b`` and weight is 0.

    Beside the line, the same shrinkage in one dimension gives variant (a),
    the slope through the origin ``w`` clipped to ``[0, weight_cap]``, and both
    variants get a lower bound ``x - lcb_k * se``.

    Parameters
    ----------
    params : dict
        ``min_clusters`` (int >= 2), ``min_dates`` (int >= 1), ``tau2_floor``
        (number > 0), ``weight_cap`` (number > 0), ``lcb_k`` (number >= 0); all
        required.

    Raises
    ------
    ValueError
        On an unknown or missing key or an invalid value.

    Examples
    --------
    Fit the map inputs of a state::

        pooling = PartialPooling({"min_clusters": 20, "min_dates": 13,
                                  "tau2_floor": 1e-6, "weight_cap": 1.0, "lcb_k": 1.0})
        out = pooling.fit(state)
        out["buckets"][3]["b"]
    """

    _PARAMS = ("min_clusters", "min_dates", "tau2_floor", "weight_cap", "lcb_k")

    def __init__(self, params):
        _checked_params(params, self._PARAMS)
        self.min_clusters = _count_param(params, "min_clusters", 2, True)
        self.min_dates = _count_param(params, "min_dates", 1, True)
        self.tau2_floor = float(_count_param(params, "tau2_floor", 0.0, False))
        self.weight_cap = float(_count_param(params, "weight_cap", 0.0, False))
        self.lcb_k = self._lcb_k(params["lcb_k"])

    @staticmethod
    def _lcb_k(v):
        """Validate ``lcb_k``: a finite number >= 0."""
        if isinstance(v, bool) or not number_ok(v) or v < 0:
            raise ValueError(f"lcb_k must be a finite number >= 0, got {v!r}")
        return float(v)

    def _enough(self, effective_clusters):
        """Say whether a unit has enough effective (Kish) clusters to stand alone."""
        return effective_clusters >= self.min_clusters

    def _line(self, state, b):
        """``(theta, V, df)`` of bucket ``b`` (two-way band), or ``None`` when it cannot stand alone."""
        f = state.fit(b)
        if f["a"] is None or not self._enough(f["g_eff"]):
            return None
        sw = state.sandwich(b, (f["a"], f["b"]), "two")
        if sw is None:
            return None
        return [f["a"], f["b"]], sw["cov"], sw["df"]

    @staticmethod
    def _origin(unit):
        """``(w, variance, df)`` of the slope through the origin (two-way band), or ``None``.

        The variance carries the same Kish CR1 factor as :func:`_sandwich_matrix` (``k = 1``); every sum
        over the name or date table runs in sorted key order, so a resumed state equals a fresh one.
        """
        n, see, ser = unit.tot[0], unit.tot[3], unit.tot[4]
        if unit.rows < 3 or unit.clusters < 2 or see <= 0.0 or n <= 0:
            return None
        w = ser / see
        tri = unit.lay.tri
        i, j, k = tri.index((3, 3)), tri.index((3, 4)), tri.index((4, 4))
        n_eff = kish_effective(n, unit.rw2)
        g_pair, g_name, g_date = (kish_effective(n, x) for x in (unit.cw2, unit.names.w2, unit.dates.w2))
        if n_eff < 3.0 or g_pair < 2.0 or g_name <= 1.0 or g_date <= 1.0:
            return None

        def one(meat, g):
            return _cr1(g, n_eff, 1) * meat / (see * see)

        pair = one(unit.cross[k] - 2 * w * unit.cross[j] + w * w * unit.cross[i], g_pair)
        gn, gd = unit.names.count, unit.dates.count
        if gn < 2 or gd < 2:
            return None
        by = [one(sum((cell[4] - w * cell[3]) ** 2 for cell in table.sorted_vectors()), g)
              for table, g in ((unit.names, g_name), (unit.dates, g_date))]
        var = by[0] + by[1] - pair
        if not var > 0.0:
            var = max(by)
        return w, var, min(g_name, g_date) - 1

    def _tau(self, items):
        """Method-of-moments between-bucket variances of one or two parameters, floored."""
        dim = len(items[0][0]) if items else 1
        if len(items) < 2:
            return [self.tau2_floor] * dim
        out = []
        for d in range(dim):
            vals = [th[d] for th, _ in items]
            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
            out.append(max(var - sum(v[d][d] for _, v in items) / len(items), self.tau2_floor))
        return out

    def _shrink_line(self, th, v, th0, tdiag):
        """Posterior mean and covariance of one bucket's coefficient vector (any dimension)."""
        q = len(th)
        t = [[tdiag[i] if i == j else 0.0 for j in range(q)] for i in range(q)]
        tv = [[t[i][j] + v[i][j] for j in range(q)] for i in range(q)]
        shift = _mat_mul(v, [[x] for x in _solve(tv, [th0[i] - th[i] for i in range(q)])])
        post = _mat_mul(t, _solve(tv, v))
        return [th[i] + shift[i][0] for i in range(q)], post

    def _clip(self, w):
        """Clip a weight to ``[0, weight_cap]``."""
        return min(max(w, 0.0), self.weight_cap)

    def _record(self, state, b, line, origin, pooled):
        """One bucket's output row."""
        f = state.fit(b)
        a, slope, se_a, se_b, w, se_w = line
        w_lcb = self._clip(w - self.lcb_k * se_w) if se_w is not None else 0.0
        return {
            "a": a, "b": slope, "se_a": se_a, "se_b": se_b,
            "b_lcb": None if se_b is None else slope - self.lcb_k * se_b,
            "w": w, "se_w": se_w, "w_lcb": w_lcb, "pooled": pooled,
            "clusters": f["clusters"], "rows": f["rows"], "dates": f["dates"], "names": f["names"],
        }

    def fit(self, state):
        """Return the shrunk per-bucket lines and weights of a state.

        Parameters
        ----------
        state : LinearEdgeState
            The admitted state (expanding or decayed); it is not changed.

        Returns
        -------
        dict
            ``warm`` (bool), ``pooled`` (the pooled row) and ``buckets`` (a list
            over every bucket index). A row holds ``a``, ``b``, ``se_a``,
            ``se_b`` (``None`` when unavailable), ``b_lcb``, ``w``, ``se_w``,
            ``w_lcb``, ``pooled`` (True when the bucket took the pool) and the raw
            ``clusters``, ``rows``, ``dates``, ``names``. Before warm-up every
            ``a``, ``b`` and ``w`` is 0.

        Raises
        ------
        ValueError
            When ``state`` is not a :class:`LinearEdgeState`.
        """
        if not isinstance(state, LinearEdgeState):
            raise ValueError(f"state must be a LinearEdgeState, got {type(state).__name__}")
        zero = (0.0, 0.0, None, None, 0.0, None)
        pf = state.fit(None)
        warm = pf["a"] is not None and self._enough(pf["g_eff"]) and pf["dates"] >= self.min_dates
        if not warm:
            return {"warm": False, "pooled": self._record(state, None, zero, None, True),
                    "buckets": [self._record(state, b, zero, None, True) for b in range(state.n_buckets)]}
        th0 = [pf["a"], pf["b"]]
        sw0 = state.sandwich(None, th0, "two")
        se0 = None if sw0 is None else [math.sqrt(sw0["cov"][0][0]), math.sqrt(sw0["cov"][1][1])]
        org0 = self._origin(state._unit(None))
        w0 = 0.0 if org0 is None else self._clip(org0[0])
        se_w0 = None if org0 is None else math.sqrt(org0[1])
        pooled_row = (th0[0], th0[1], se0 and se0[0], se0 and se0[1], w0, se_w0)
        lines = {b: self._line(state, b) for b in range(state.n_buckets)}
        tau = self._tau([(x[0], x[1]) for x in lines.values() if x is not None])
        origins = {b: self._origin(state._unit(b)) if self._enough(state.fit(b)["g_eff"]) else None
                   for b in range(state.n_buckets)}
        tau_w = self._tau([([o[0]], [[o[1]]]) for o in origins.values() if o is not None])
        rows = []
        for b in range(state.n_buckets):
            ln, org = lines[b], origins[b]
            if ln is None:
                a, slope, se_a, se_b = pooled_row[:4]
            else:
                (a, slope), post = self._shrink_line(ln[0], ln[1], th0, tau)
                se_a, se_b = math.sqrt(max(post[0][0], 0.0)), math.sqrt(max(post[1][1], 0.0))
            if org is None:
                w, se_w = w0, se_w0
            else:
                wb, vb = self._clip(org[0]), org[1]
                w = self._clip(wb + vb / (tau_w[0] + vb) * (w0 - wb))
                se_w = math.sqrt(tau_w[0] * vb / (tau_w[0] + vb))
            rows.append(self._record(state, b, (a, slope, se_a, se_b, w, se_w), org, ln is None))
        return {"warm": True, "pooled": self._record(state, None, pooled_row, org0, False), "buckets": rows}


class NameAdjustment:
    """Per-name skill adjustment ``(da_n, db_n)`` from a name's own frozen residuals.

    A cluster arrives when it settles, with the bucket-only calibrated edge
    ``c`` that was in force at its own decision ``t_i`` (stored then, never
    recomputed): the residual ``u = r - c`` is fixed once, so a later bucket map
    cannot move it and the adjustment cannot feed back into it. Per name the
    class keeps weighted sums of ``(1, e, u, e*e, e*u)`` and the cluster
    weights ``Sw`` and ``Sw2`` (a cluster's weight is its TOTAL row weight). The OLS of ``u`` on ``(1, e)``
    is multiplied by ``G_eff / (G_eff + name_prior_clusters)`` with the Kish count :func:`kish_effective`
    ``(Sw, Sw2)``, and is zero below ``name_min_clusters`` effective clusters or when the name's ``e``
    is constant.

    Parameters
    ----------
    params : dict
        ``name_prior_clusters`` (number >= 0) and ``name_min_clusters``
        (int >= 1), both required.
    decay : float or None
        ``None`` for expanding, else the weekly factor ``lambda`` in (0, 1);
        the sums age by ``lambda`` and the weights ``Sw2`` by ``lambda ** 2``.

    Raises
    ------
    ValueError
        On an unknown or missing key, an invalid value or a bad ``decay``.

    Examples
    --------
    One settled cluster, then the adjustment::

        adj = NameAdjustment({"name_prior_clusters": 26, "name_min_clusters": 4})
        adj.advance("2020-03-13")
        adj.add_cluster("2020-03-13", "AAA", "2020-03-06",
                        [1.0, 2.0], [1.5, 2.2], [1.0, 1.6])
        adj.adjust("AAA")  # -> (0.0, 0.0) until 4 clusters
    """

    _PARAMS = ("name_prior_clusters", "name_min_clusters")

    def __init__(self, params, decay=None):
        _checked_params(params, self._PARAMS)
        prior = params["name_prior_clusters"]
        if isinstance(prior, bool) or not number_ok(prior) or prior < 0:
            raise ValueError(f"name_prior_clusters must be a finite number >= 0, got {prior!r}")
        self.name_prior_clusters = float(prior)
        self.name_min_clusters = _count_param(params, "name_min_clusters", 1, True)
        if decay is not None and (isinstance(decay, bool) or not number_ok(decay) or not 0.0 < decay < 1.0):
            raise ValueError(f"decay must be None or a number in (0, 1), got {decay!r}")
        self.decay = None if decay is None else float(decay)
        self._t = None
        self._names = {}  # name -> {"s": [n, e, u, ee, eu], "w": [Sw, Sw2], "g": raw clusters}
        self._seen = set()

    def advance(self, t):
        """Move to decision week ``t``, aging a decayed adjustment by one week.

        Parameters
        ----------
        t : str or datetime.date
            The decision date; the same date again is a no-op.

        Raises
        ------
        ValueError
            When ``t`` is malformed or earlier than the current week.
        """
        day = _iso(t, "t")
        if self._t is None:
            self._t = day
        elif day < self._t:
            raise ValueError(f"time cannot run backward: {day} is before {self._t}")
        elif day > self._t:
            if self.decay is not None:
                lam = self.decay
                for rec in self._names.values():
                    rec["s"] = [v * lam for v in rec["s"]]
                    rec["w"] = [rec["w"][0] * lam, rec["w"][1] * lam * lam]
            self._t = day

    def add_cluster(self, t, name, settle_date, e, r, c, weights=None):
        """Fold one settled ``(name, settle_date)`` cluster in, with its frozen residual.

        Parameters
        ----------
        t : str or datetime.date
            The decision week the adjustment is for (advanced to if later).
        name : str
            The cluster's name.
        settle_date : str or datetime.date
            When the rows settled; must be strictly before ``t``.
        e : list of float
            Predicted edge per row.
        r : list of float
            Realized edge per row.
        c : list of float
            The bucket-only calibrated edge of each row at its OWN decision
            session ``t_i`` (``EdgeCalibrationMap.bucket_only`` then).
        weights : list of float, optional
            Positive finite base weight per row (the ``group_equal`` weights of ADR-0311 D4; default 1).
            The five running sums are weighted by them, and the cluster's weight in the Kish count is its
            total row weight.

        Raises
        ------
        ValueError
            ``state_from_future`` when ``settle_date >= t``; a plain error for a
            malformed cluster or one already added. The adjustment is unchanged.
        """
        day, s = _iso(t, "t"), _iso(settle_date, "settle_date")
        if label_reaches(s, day):
            raise _Refusal("state_from_future", f"settlement {s} is not before t {day}")
        if not cluster_ok(name):
            raise ValueError(f"name must be a non-empty string, got {name!r}")
        m = len(e) if isinstance(e, (list, tuple)) else -1
        if m < 1 or not all(isinstance(v, (list, tuple)) and len(v) == m for v in (r, c)):
            raise ValueError("e, r and c must be non-empty lists of the same length")
        for v in (*e, *r, *c):
            if isinstance(v, bool) or not number_ok(v):
                raise ValueError(f"e, r and c must be finite numbers, got {v!r}")
        if weights is not None and (not isinstance(weights, (list, tuple)) or len(weights) != m or not all(
                not isinstance(v, bool) and number_ok(v) and v > 0 for v in weights)):
            raise ValueError(f"weights must be {m} finite numbers > 0, got {weights!r}")
        if (name, s) in self._seen:
            raise ValueError(f"cluster ({name}, {s}) was already added: a cluster arrives whole, once")
        self.advance(day)
        self._seen.add((name, s))
        u = [float(a) - float(b) for a, b in zip(r, c)]
        ev = [float(v) for v in e]
        rec = self._names.setdefault(name, {"s": [0.0] * 5, "w": [0.0, 0.0], "g": 0})
        wv = [1.0] * m if weights is None else [float(v) for v in weights]
        add = [sum(wv), sum(w * v for w, v in zip(wv, ev)), sum(w * v for w, v in zip(wv, u)),
               sum(w * v * v for w, v in zip(wv, ev)), sum(w * a * b for w, a, b in zip(wv, ev, u))]
        rec["s"] = [x + y for x, y in zip(rec["s"], add)]
        total = sum(wv)
        rec["w"] = [rec["w"][0] + total, rec["w"][1] + total * total]
        rec["g"] += 1

    def effective_clusters(self, name):
        """Return the Kish effective cluster count of a name (0 if unknown), on cluster total row weights."""
        rec = self._names.get(name)
        return 0.0 if rec is None else kish_effective(*rec["w"])

    def adjust(self, name):
        """Return the shrunk ``(da, db)`` of a name.

        Parameters
        ----------
        name : str
            The name.

        Returns
        -------
        tuple of float
            ``(0.0, 0.0)`` for an unknown name, below ``name_min_clusters``, or
            when the name's ``e`` has no variance.
        """
        rec = self._names.get(name)
        if rec is None or kish_effective(*rec["w"]) < self.name_min_clusters:
            return (0.0, 0.0)
        n, se, su, see, seu = rec["s"]
        varx = see - se * se / n
        if n <= 0 or varx <= _FIT_RTOL * see:
            return (0.0, 0.0)
        db = (seu - se * su / n) / varx
        da = (su - db * se) / n
        g = self.effective_clusters(name)
        k = g / (g + self.name_prior_clusters)
        return (da * k, db * k)

    def names(self):
        """Return the sorted names the adjustment has seen."""
        return sorted(self._names)

    def to_dict(self):
        """Return the sums as a JSON-safe dict (restorable with :meth:`from_dict`)."""
        return {
            "params": {"name_prior_clusters": self.name_prior_clusters,
                       "name_min_clusters": self.name_min_clusters},
            "decay": self.decay, "t": self._t,
            "seen": sorted([name, day] for name, day in self._seen),
            "names": {k: {"s": list(v["s"]), "w": list(v["w"]), "g": v["g"]}
                      for k, v in sorted(self._names.items())},
        }

    @classmethod
    def from_dict(cls, blob):
        """Rebuild from :meth:`to_dict`.

        Raises
        ------
        ValueError
            On a missing key or a malformed blob.
        """
        try:
            adj = cls(blob["params"], decay=blob["decay"])
            adj._t = blob["t"]
            adj._seen = {(name, day) for name, day in blob["seen"]}
            adj._names = {k: {"s": [float(x) for x in v["s"]], "w": [float(x) for x in v["w"]],
                              "g": int(v["g"])} for k, v in blob["names"].items()}
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed NameAdjustment dump: {exc!r}") from exc
        return adj


@dataclass(frozen=True)
class _BucketMap:
    """One bucket of a frozen map: line, weights, standard errors and counts."""

    a: float
    b: float
    se_a: float
    se_b: float
    b_lcb: float
    w: float
    se_w: float
    w_lcb: float
    pooled: bool
    clusters: int
    rows: int
    dates: int
    names: int


class EdgeCalibrationMap:
    """The frozen map from predicted to calibrated edge at one decision date.

    Build it with :meth:`build` from the admitted state; nothing in it changes
    afterwards, so it can be stored on a pending row (its :meth:`bucket_only`
    value at ``t_i`` is the residual baseline of :class:`NameAdjustment`).
    Before warm-up every value is the prior 0.

    Attributes
    ----------
    t : str
        The decision date.
    warm : bool
        Whether the pooling gates passed.
    buckets : tuple of _BucketMap
        Per bucket ``a``, ``b``, ``se_a``, ``se_b``, ``b_lcb`` (variant b),
        ``w``, ``se_w``, ``w_lcb`` (variants a), ``pooled`` and counts.
    pooled : _BucketMap
        The pooled line.
    names : mapping
        Name -> ``(da, db)`` for every name the adjustment has seen.

    Examples
    --------
    Calibrate one item::

        cmap = EdgeCalibrationMap.build("2020-03-13", state, pooling, adjust)
        cmap.calibrated(bucket=7, name="AAA", e=1.2)
    """

    __slots__ = ("t", "warm", "buckets", "pooled", "names")

    def __init__(self, t, warm, buckets, pooled, names):
        object.__setattr__(self, "t", _iso(t, "t"))
        object.__setattr__(self, "warm", bool(warm))
        object.__setattr__(self, "buckets", tuple(buckets))
        object.__setattr__(self, "pooled", pooled)
        object.__setattr__(self, "names", MappingProxyType({k: tuple(v) for k, v in names.items()}))

    def __setattr__(self, key, value):
        """Refuse: the map is frozen."""
        raise AttributeError(f"EdgeCalibrationMap is frozen; cannot set {key!r}")

    def __delattr__(self, key):
        """Refuse: the map is frozen."""
        raise AttributeError(f"EdgeCalibrationMap is frozen; cannot delete {key!r}")

    @classmethod
    def build(cls, t, state, pooling, adjust):
        """Freeze the map at ``t`` from an admitted state.

        Parameters
        ----------
        t : str or datetime.date
            The decision date.
        state : LinearEdgeState
            The admitted state.
        pooling : PartialPooling
            The pooling rule.
        adjust : NameAdjustment
            The per-name adjustment (its names are copied).

        Returns
        -------
        EdgeCalibrationMap
            The frozen map; before warm-up every name adjustment is 0 too.
        """
        out = pooling.fit(state)
        keys = _BucketMap.__dataclass_fields__
        rows = [_BucketMap(**{k: x[k] for k in keys}) for x in out["buckets"]]
        pooled = _BucketMap(**{k: out["pooled"][k] for k in keys})
        names = {n: adjust.adjust(n) for n in adjust.names()} if out["warm"] else {}
        return cls(t, out["warm"], rows, pooled, names)

    def _bucket(self, bucket):
        """Return a bucket's record, refusing an index outside the map."""
        if isinstance(bucket, bool) or not isinstance(bucket, int) or not 0 <= bucket < len(self.buckets):
            raise ValueError(f"bucket must be an integer in 0..{len(self.buckets) - 1}, got {bucket!r}")
        return self.buckets[bucket]

    @staticmethod
    def _edge(e):
        if isinstance(e, bool) or not number_ok(e):
            raise ValueError(f"e must be a finite number, got {e!r}")
        return float(e)

    def bucket_only(self, bucket, e):
        """Return ``a_b + b_b e`` (no name adjustment): the baseline a residual is frozen against."""
        b, e = self._bucket(bucket), self._edge(e)
        return b.a + b.b * e

    def calibrated(self, bucket, name, e):
        """Return ``(a_b + da_n) + (b_b + db_n) e``; an unseen name has no adjustment."""
        b, e = self._bucket(bucket), self._edge(e)
        da, db = self.names.get(name, (0.0, 0.0))
        return (b.a + da) + (b.b + db) * e

    def origin(self, bucket, e, lcb=False):
        """Return variant (a), ``w_b e`` (``w_lcb`` when ``lcb``): zero predicted, zero calibrated."""
        b, e = self._bucket(bucket), self._edge(e)
        return (b.w_lcb if lcb else b.w) * e

    def lower(self, bucket, e):
        """Return variant (b), ``a_b + (b_b - lcb_k se) e``."""
        b, e = self._bucket(bucket), self._edge(e)
        return b.a + b.b_lcb * e if b.b_lcb is not None else b.a + b.b * e

    def to_dict(self):
        """Return the map as a JSON-safe dict (restorable with :meth:`from_dict`)."""
        return {
            "t": self.t, "warm": self.warm,
            "buckets": [dict(_fields_of(b)) for b in self.buckets],
            "pooled": dict(_fields_of(self.pooled)),
            "names": {k: list(v) for k, v in sorted(self.names.items())},
        }

    @classmethod
    def from_dict(cls, blob):
        """Rebuild from :meth:`to_dict`.

        Raises
        ------
        ValueError
            On a missing key or a malformed blob.
        """
        try:
            return cls(blob["t"], blob["warm"], [_BucketMap(**x) for x in blob["buckets"]],
                       _BucketMap(**blob["pooled"]), {k: v for k, v in blob["names"].items()})
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed EdgeCalibrationMap dump: {exc!r}") from exc


def _fields_of(record):
    """Return a frozen record's fields as a dict."""
    return {k: getattr(record, k) for k in record.__dataclass_fields__}


class TrailingRegime:
    """A name's volatility regime at decision ``t``: is it above its own trailing median.

    ``v`` is the name's annualized scale at a decision session (see
    :meth:`annualize`). The regime at ``t`` is ``high`` when ``v`` is STRICTLY
    above the median of that name's earlier values dated in
    ``[t - 7 * regime_lookback_weeks days, t)``; the value at ``t`` itself and
    anything later never counts, so a tie is ``low``. With fewer than
    ``regime_min_weeks`` such values the regime is the fallback ``low`` and the
    answer carries ``warmup=True`` (ADR-0311 counts it as ``regime_warmup``;
    :attr:`warmups` is that count).

    State is incremental: each name keeps only the values still inside the
    newest window (about ``regime_lookback_weeks + 1``), appended in date order,
    so a call never rescans history. A query is exact for any ``t`` whose window
    has not been evicted, else it refuses.

    Parameters
    ----------
    params : dict
        ``regime_lookback_weeks`` and ``regime_min_weeks``, both positive ints
        (``min <= lookback``), required.

    Raises
    ------
    ValueError
        On an unknown or missing key or a bad value (params); on an
        out-of-order ``add``, a bad ``v`` or a query into an evicted window.

    Examples
    --------
    ::

        reg = TrailingRegime({"regime_lookback_weeks": 52, "regime_min_weeks": 13})
        v = TrailingRegime.annualize([(0.02, 7), (0.04, 28)])
        regime, warmup = reg.of("AAA", "2020-03-06", v)  # then reg.add("AAA", "2020-03-06", v)
    """

    _PARAMS = ("regime_lookback_weeks", "regime_min_weeks")

    def __init__(self, params):
        if not isinstance(params, dict):
            raise ValueError(f"params must be a dict, got {type(params).__name__}")
        unknown = sorted(set(params) - set(self._PARAMS))
        if unknown:
            raise ValueError(f"unknown params {unknown}; allowed {list(self._PARAMS)}")
        for key in self._PARAMS:
            value = params.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} must be an int >= 1, got {value!r}")
        self._lookback = params["regime_lookback_weeks"]
        self._min = params["regime_min_weeks"]
        if self._min > self._lookback:
            raise ValueError(f"regime_min_weeks {self._min} exceeds regime_lookback_weeks {self._lookback}")
        self._days = {}
        self._vals = {}
        self._evicted = {}
        self._warm_seen = set()  # (name, ordinal day) already counted as a warm-up
        #: Distinct ``(name, t)`` queries answered with the fallback ``low`` for lack of history (``regime_warmup``).
        self.warmups = 0

    @staticmethod
    def _scale(v):
        if isinstance(v, bool) or not number_ok(v) or v <= 0:
            raise ValueError(f"v must be a finite number > 0, got {v!r}")
        return float(v)

    @staticmethod
    def annualize(scale_dte_pairs):
        """Return the median over expiries of ``reference_scale * sqrt(365 / dte)``.

        Parameters
        ----------
        scale_dte_pairs : iterable of (float, float)
            One ``(reference_scale, dte)`` per eligible expiry of the name at
            the decision session; scale ``> 0``, calendar ``dte > 0``.

        Returns
        -------
        float
            The annualized scale ``v`` (the median limits one bad expiry).

        Raises
        ------
        ValueError
            When empty or any scale or dte is not a finite positive number.
        """
        out = []
        for scale, dte in scale_dte_pairs:
            for x, what in ((scale, "reference_scale"), (dte, "dte")):
                if isinstance(x, bool) or not number_ok(x) or x <= 0:
                    raise ValueError(f"{what} must be a finite number > 0, got {x!r}")
            out.append(float(scale) * math.sqrt(365.0 / float(dte)))
        if not out:
            raise ValueError("annualize needs at least one (reference_scale, dte) pair")
        return _median(out)

    def _window(self, name, t):
        """Return the values of ``name`` dated in ``[t - lookback weeks, t)``."""
        days = self._days.get(name, [])
        start = t.toordinal() - 7 * self._lookback
        if name in self._evicted and start <= self._evicted[name]:
            raise ValueError(f"{name}: window starting {date.fromordinal(start)} was evicted "
                             f"(values up to {date.fromordinal(self._evicted[name])} are gone)")
        lo = bisect.bisect_left(days, start)
        hi = bisect.bisect_left(days, t.toordinal())
        return self._vals.get(name, [])[lo:hi]

    def add(self, name, t, v):
        """Record ``name``'s annualized scale ``v`` at decision session ``t``.

        Parameters
        ----------
        name : str
            The name (ticker).
        t : str or datetime.date
            The decision session; strictly later than the name's last one.
        v : float
            The annualized scale, finite and positive.

        Raises
        ------
        ValueError
            On a date not after the name's latest (``out of order``) or a bad ``v``.
        """
        day = date.fromisoformat(_iso(t, "t")).toordinal()
        value = self._scale(v)
        days = self._days.setdefault(name, [])
        vals = self._vals.setdefault(name, [])
        if days and day <= days[-1]:
            raise ValueError(f"{name}: add at {_iso(t, 't')} is out of order (latest "
                             f"{date.fromordinal(days[-1]).isoformat()})")
        days.append(day)
        vals.append(value)
        cut = bisect.bisect_left(days, day - 7 * self._lookback)
        if cut:
            self._evicted[name] = days[cut - 1]
            del days[:cut]
            del vals[:cut]

    def window_size(self, name, t):
        """Return how many earlier values of ``name`` fall in the window at ``t``."""
        return len(self._window(name, date.fromisoformat(_iso(t, "t"))))

    def stored(self, name):
        """Return how many values are retained for ``name`` (bounded by the window)."""
        return len(self._vals.get(name, ()))

    def to_dict(self):
        """Return the retained history as a JSON-safe dict (restorable with :meth:`from_dict`)."""
        return {
            "params": {"regime_lookback_weeks": self._lookback, "regime_min_weeks": self._min},
            "warmups": self.warmups,
            "warm_seen": sorted([k, d] for k, d in self._warm_seen),
            "names": {k: {"days": list(self._days[k]), "vals": list(self._vals[k]),
                          "evicted": self._evicted.get(k)} for k in sorted(self._days)},
        }

    @classmethod
    def from_dict(cls, blob):
        """Rebuild from :meth:`to_dict`.

        Raises
        ------
        ValueError
            On a missing key or a malformed blob.
        """
        try:
            reg = cls(blob["params"])
            reg.warmups = int(blob["warmups"])
            reg._warm_seen = {(k, int(d)) for k, d in blob["warm_seen"]}
            for k, rec in blob["names"].items():
                reg._days[k] = [int(x) for x in rec["days"]]
                reg._vals[k] = [float(x) for x in rec["vals"]]
                if len(reg._days[k]) != len(reg._vals[k]):
                    raise ValueError("days and vals differ in length")
                if rec["evicted"] is not None:
                    reg._evicted[k] = int(rec["evicted"])
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed TrailingRegime dump: {exc!r}") from exc
        return reg

    def of(self, name, t, v):
        """Return ``(regime, warmup)`` for ``name`` at ``t`` with annualized scale ``v``.

        Parameters
        ----------
        name : str
            The name (ticker).
        t : str or datetime.date
            The decision session; only values dated strictly before it count.
        v : float
            The name's annualized scale at ``t``, finite and positive.

        Returns
        -------
        tuple of (str, bool)
            ``"high"`` when ``v`` is strictly above the trailing median, else
            ``"low"``; and True when fewer than ``regime_min_weeks`` earlier
            values existed (the regime is then the fallback ``"low"``).

        Raises
        ------
        ValueError
            On a bad ``v`` or when the window at ``t`` has been evicted.
        """
        value = self._scale(v)
        day = date.fromisoformat(_iso(t, "t"))
        window = self._window(name, day)
        if len(window) < self._min:
            if (name, day.toordinal()) not in self._warm_seen:
                self._warm_seen.add((name, day.toordinal()))
                self.warmups += 1
            return "low", True
        return ("high" if value > _median(window) else "low"), False


def _median(values):
    """Return the median of a non-empty list of numbers."""
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
