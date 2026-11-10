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

from array import array
import bisect
import itertools
import math
import random
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType

from dskit.pipeline.kinds_split import label_reaches
from dskit.pipeline.method_blocks import DEVELOPMENT_SEAL  # the single owner (ADR-0313 X4)
from dskit.pipeline.records import cluster_ok, number_ok
from dskit.pipeline.stats import NO_VARIANCE_RTOL, student_t_critical

__all__ = [
    "DEVELOPMENT_SEAL",
    "LinearEdgeState",
    "ReliabilityBuckets",
    "SettledBefore",
    "block_index",
    "kish_effective",
    "wild_cluster_test",
    "three_way_verdict",
    "read_one",
    "origin_two",
    "PartialPooling",
    "NameAdjustment",
    "EdgeCalibrationMap",
    "TrailingRegime",
    "CalibratedObjective",
]

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
#: Rebase the lazy clock when ``lambda ** -j`` would exceed this (about 520 weeks at the 26-week half-life).
_REBASE_AT = 1e6
_WAYS = ("one", "two", "two_block", "hac")
#: Webb's six-point weights, each with probability 1/6 (used when the raw block count is below 12).
_WEBB_WEIGHTS = (-math.sqrt(1.5), -1.0, -math.sqrt(0.5), math.sqrt(0.5), 1.0, math.sqrt(1.5))
_ENUMERATE_MAX = 100_000
_WEBB_BELOW = 12


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


def block_index(day, window_start, weeks):
    """Return the time block of ``day``: ``floor((day - window_start).days / (7 * weeks))``.

    Parameters
    ----------
    day : str or datetime.date
        The settlement date.
    window_start : str or datetime.date
        The first day of the window (block 0 starts here).
    weeks : int
        The block length in weeks, at least 1.

    Returns
    -------
    int
        The block index, at least 0.

    Raises
    ------
    ValueError
        On a malformed date, a ``day`` before ``window_start`` or a bad ``weeks``.
    """
    if isinstance(weeks, bool) or not isinstance(weeks, int) or weeks < 1:
        raise ValueError(f"weeks must be an integer >= 1, got {weeks!r}")
    days = (date.fromisoformat(_iso(day, "day")) - date.fromisoformat(_iso(window_start, "window_start"))).days
    if days < 0:
        raise ValueError(f"{day} is before the window start {window_start}")
    return days // (7 * weeks)


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
    """A dimension of cluster units: a dense ``(n1 x n2)`` grid of ``p``-vectors in ONE flat ``array('d')``.

    One-dimensional dimensions (names, dates, blocks) use ``n2 = 1``; the ``(name, block)`` table is
    two-dimensional. Cells are indexed ``(i * cap2 + j) * p + component``; the capacity doubles on demand.
    ``w2`` is the running sum over cells of ``(total weight)^2`` (the first component), kept for the Kish
    count; every sum over the cells runs in index order, so a resumed state equals a fresh one.
    """

    def __init__(self, p, two=False):
        self.p, self.two = p, two
        self.cap1, self.cap2 = 4, (4 if two else 1)
        self.data = array("d", bytes(8 * p * self.cap1 * self.cap2))
        self.present = bytearray(self.cap1 * self.cap2)
        self.w2 = 0.0
        self.count = 0

    def _grow(self, i, j):
        c1, c2 = self.cap1, self.cap2
        while i >= c1:
            c1 *= 2
        while j >= c2:
            c2 *= 2
        if (c1, c2) == (self.cap1, self.cap2):
            return
        data = array("d", bytes(8 * self.p * c1 * c2))
        present = bytearray(c1 * c2)
        for a in range(self.cap1):
            src, dst = a * self.cap2, a * c2
            data[dst * self.p:(dst + self.cap2) * self.p] = self.data[src * self.p:(src + self.cap2) * self.p]
            present[dst:dst + self.cap2] = self.present[src:src + self.cap2]
        self.cap1, self.cap2, self.data, self.present = c1, c2, data, present

    def add(self, i, j, vec):
        """Add ``vec`` to cell ``(i, j)``."""
        if i >= self.cap1 or j >= self.cap2:
            self._grow(i, j)
        o = i * self.cap2 + j
        base, p, data = o * self.p, self.p, self.data
        old = data[base]
        for q in range(p):
            data[base + q] += vec[q]
        self.w2 += data[base] * data[base] - old * old
        if not self.present[o]:
            self.present[o] = 1
            self.count += 1

    def scale(self, f1, f2):
        """Multiply every cell by ``f1`` (the squared weights by ``f2``)."""
        self.data = array("d", (x * f1 for x in self.data))
        self.w2 *= f2

    def keys(self):
        """Return the present cell keys in index order (``i`` or ``(i, j)``)."""
        out = []
        for o in range(len(self.present)):
            if self.present[o]:
                i, j = divmod(o, self.cap2)
                out.append((i, j) if self.two else i)
        return out

    def vector(self, key):
        """Return the cell ``key`` as a list (zeros if absent)."""
        i, j = key if self.two else (key, 0)
        if i >= self.cap1 or j >= self.cap2:
            return [0.0] * self.p
        base = (i * self.cap2 + j) * self.p
        return list(self.data[base:base + self.p])

    def vectors(self):
        """Return the present cells as lists, in index order."""
        return [self.vector(k) for k in self.keys()]

    @property
    def cells(self):
        """Return ``{key: vector}`` of the present cells (index keys)."""
        return {k: self.vector(k) for k in self.keys()}

    def load(self, items, w2):
        """Fill from ``(key, vector)`` items (a dump) and set ``w2``."""
        for key, vec in items:
            i, j = key if self.two else (key, 0)
            self._grow(i, j)
            base = (i * self.cap2 + j) * self.p
            self.data[base:base + self.p] = array("d", vec)
            if not self.present[i * self.cap2 + j]:
                self.present[i * self.cap2 + j] = 1
                self.count += 1
        self.w2 = float(w2)


class _Unit:
    """Running sums of one regression: a bucket, a stratum, or the pooled rows (all in reference units)."""

    def __init__(self, lay, n_blocks=1):
        self.lay = lay
        self.blocks = [_Table(lay.size) for _ in range(n_blocks)]  # one dimension per block length
        self.pairs = [_Table(lay.size, True) for _ in range(n_blocks)]  # (name, block) units per block length
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

    def add(self, ni, di, vec, rr, serr, rows, rw2, groups, blocks):
        """Fold one whole (name, date) cluster, ALREADY in reference units, into the sums."""
        for q in range(self.lay.size):
            self.tot[q] += vec[q]
        self.tot[self.lay.size] += rr
        self.serr += serr
        self.rw2 += rw2
        self.cw2 += vec[0] * vec[0]
        self.groups += groups
        for idx, (i, j) in enumerate(self.lay.tri):
            self.cross[idx] += vec[i] * vec[j]
        self.names.add(ni, 0, vec)
        self.dates.add(di, 0, vec)
        for table, pairs, blk in zip(self.blocks, self.pairs, blocks):
            table.add(blk, 0, vec)
            pairs.add(ni, blk, vec)
        self.rows += rows
        self.clusters += 1

    def rescale(self, f1):
        """Multiply first-order sums by ``f1`` and second-order ones by ``f1 ** 2`` (the lazy-decay rebase)."""
        f2 = f1 * f1
        self.tot = [v * f1 for v in self.tot]
        self.serr *= f1
        self.rw2 *= f2
        self.cw2 *= f2
        self.cross = [v * f2 for v in self.cross]
        for table in (self.names, self.dates, *self.blocks, *self.pairs):
            table.scale(f1, f2)

    def to_dict(self, names, dates):
        """Dump the sums; ``names`` and ``dates`` are the state's registries (index -> label)."""
        return {
            "tot": list(self.tot), "serr": self.serr, "rw2": self.rw2, "cw2": self.cw2, "groups": self.groups,
            "cross": list(self.cross), "rows": self.rows, "clusters": self.clusters,
            "names": {names[i]: self.names.vector(i) for i in self.names.keys()},
            "dates": {dates[i]: self.dates.vector(i) for i in self.dates.keys()},
            "names_w2": self.names.w2, "dates_w2": self.dates.w2,
            "blocks": [{str(i): t.vector(i) for i in t.keys()} for t in self.blocks],
            "blocks_w2": [t.w2 for t in self.blocks],
            "pairs": [{f"{names[i]}\x00{j}": t.vector((i, j)) for i, j in t.keys()} for t in self.pairs],
            "pairs_w2": [t.w2 for t in self.pairs],
        }

    @classmethod
    def from_dict(cls, lay, blob, name_idx, date_idx):
        unit = cls(lay, len(blob["blocks"]))
        unit.tot = [float(v) for v in blob["tot"]]
        unit.serr, unit.rw2 = float(blob["serr"]), float(blob["rw2"])
        unit.cw2, unit.groups = float(blob["cw2"]), float(blob["groups"])
        unit.cross = [float(v) for v in blob["cross"]]
        unit.rows, unit.clusters = int(blob["rows"]), int(blob["clusters"])
        unit.names.load(sorted(((name_idx[k], v) for k, v in blob["names"].items())), blob["names_w2"])
        unit.dates.load(sorted(((date_idx[k], v) for k, v in blob["dates"].items())), blob["dates_w2"])
        for table, b, w2 in zip(unit.blocks, blob["blocks"], blob["blocks_w2"]):
            table.load(sorted((int(k), v) for k, v in b.items()), w2)
        for table, b, w2 in zip(unit.pairs, blob["pairs"], blob["pairs_w2"]):
            items = []
            for key, vec in b.items():
                name, blk = key.rsplit("\x00", 1)
                items.append(((name_idx[name], int(blk)), vec))
            table.load(sorted(items), w2)
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
        self._pooled = _Unit(self._lay, self.block_lengths)
        self._buckets = {}
        self._seen = set()
        self._names = {}  # name -> index (the registry every dimension table is indexed by)
        self._dates = {}  # settlement date -> index
        self._j = 0  # decision-week index of the lazy clock: stored sums are in units where week j has weight 1
        #: Rows counted, never silent: ``sigma2_missing`` (a direction row without a forward error variance).
        self.counts = {"sigma2_missing": 0}

    def advance(self, t):
        """Move to decision week ``t``, aging a decayed state by one week (O(1): the lazy clock moves).

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
                self._j += 1
                if self.decay ** (-self._j) > _REBASE_AT:
                    f1 = self.decay ** self._j
                    for unit in (self._pooled, *self._buckets.values()):
                        unit.rescale(f1)
                    self._j = 0
            self._t = day

    def _f1(self):
        """Return the factor taking reference-unit first-order sums to current weights (``lambda ** j``)."""
        return 1.0 if self.decay is None else self.decay ** self._j

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

    def _blocks(self, blocks):
        """Return the cluster's block indices, one per block length (all 0 when ``blocks`` is ``None``)."""
        if blocks is None:
            return [0] * self.block_lengths
        if not isinstance(blocks, (list, tuple)) or len(blocks) != self.block_lengths or not all(
                isinstance(b, int) and not isinstance(b, bool) and b >= 0 for b in blocks):
            raise ValueError(f"blocks must be {self.block_lengths} non-negative integers, got {blocks!r}")
        return list(blocks)

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
            One non-negative block index per configured block length
            (see :func:`block_index`); ``None`` puts the cluster in block 0.
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
        bl = self._blocks(blocks)
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
                a = acc.setdefault(key, {"vec": [0.0] * lay.size, "rr": 0.0, "serr": 0.0, "rows": 0, "rw2": 0.0,
                                         "groups": 0.0})
                for q in range(lay.size):
                    a["vec"][q] += row[q]
                a["rr"] += w * rv[i] * rv[i]
                a["serr"] += serr
                a["rows"] += 1
                a["rw2"] += w * w
                a["groups"] += w
        self._absorb(name, s, bl, acc)

    def _absorb(self, name, s, blocks, units):
        """Register the cluster and fold its per-unit sums (current weights) into reference units."""
        ni = self._names.setdefault(name, len(self._names))
        di = self._dates.setdefault(s, len(self._dates))
        if self.decay is None:
            s1 = s2 = 1.0
        else:
            s1 = self.decay ** (-self._j)
            s2 = s1 * s1
        lay = self._lay
        for key, a in units.items():
            unit = self._pooled if key is None else self._buckets.setdefault(key, _Unit(lay, self.block_lengths))
            unit.add(ni, di, [v * s1 for v in a["vec"]], a["rr"] * s1, a["serr"] * s1, a["rows"], a["rw2"] * s2,
                     a["groups"], blocks)

    def add_cluster_sums(self, t, name, settle_date, fill_date, blocks, per_unit_sums):
        """Fold one whole cluster given as per-unit moment vectors that the caller has already formed.

        The tier-2 walk forms the vectors for a week's settled rows with numpy (no Python row loop) and
        calls this; :meth:`add_cluster` (row lists, stdlib) remains for tests and small users and the two
        paths agree to 1e-12.

        Parameters
        ----------
        t, name, settle_date, fill_date : as for :meth:`add_cluster`.
        blocks : list of int
            One non-negative block index per configured block length.
        per_unit_sums : dict
            Unit (``None`` pooled, required; a bucket int; ``"buy"``, ``"sell"``, ``"top"``) -> dict with
            ``vec`` (the moment vector of that unit's rows in this cluster: length 5 or 9, ``vec[0] > 0``),
            ``rr`` (``sum w r r``), ``serr`` (``sum w sigma2``), ``rw2`` (``sum w w``), ``rows`` (int >= 1)
            and ``groups`` (``sum`` of the base weights).

        Raises
        ------
        ValueError
            ``state_from_future`` as for :meth:`add_cluster`; a plain error for a malformed vector, an
            unknown unit, a missing pooled unit, a wrong block count or a cluster already added. The state is
            unchanged.
        """
        day, s, f = _iso(t, "t"), _iso(settle_date, "settle_date"), _iso(fill_date, "fill_date")
        if label_reaches(s, day):
            raise _Refusal("state_from_future", f"settlement {s} is not before t {day}")
        if label_reaches(f, s):
            raise _Refusal("state_from_future", f"fill {f} is not before settlement {s}")
        if not cluster_ok(name):
            raise ValueError(f"name must be a non-empty string, got {name!r}")
        bl = self._blocks(blocks)
        if not isinstance(per_unit_sums, dict) or None not in per_unit_sums:
            raise ValueError("per_unit_sums must be a dict holding the pooled unit under None")
        lay = self._lay
        for key, a in per_unit_sums.items():
            if not (key is None or key in _STRATA or (isinstance(key, int) and not isinstance(key, bool)
                                                      and 0 <= key < self.n_buckets)):
                raise ValueError(f"unknown unit {key!r}")
            if not isinstance(a, dict) or not {"vec", "rr", "serr", "rw2", "rows", "groups"} <= set(a):
                raise ValueError(f"unit {key!r} needs vec, rr, serr, rw2, rows and groups")
            vec = a["vec"]
            if not isinstance(vec, (list, tuple)) or len(vec) != lay.size or not all(
                    not isinstance(v, bool) and number_ok(v) for v in vec) or not vec[0] > 0.0:
                raise ValueError(f"unit {key!r}: vec must be {lay.size} finite numbers with vec[0] > 0")
            if any(isinstance(a[x], bool) or not number_ok(a[x]) or a[x] < 0 for x in ("rr", "serr", "rw2", "groups")):
                raise ValueError(f"unit {key!r}: rr, serr, rw2 and groups must be finite numbers >= 0")
            if isinstance(a["rows"], bool) or not isinstance(a["rows"], int) or a["rows"] < 1:
                raise ValueError(f"unit {key!r}: rows must be an integer >= 1")
        if (name, s) in self._seen:
            raise ValueError(f"cluster ({name}, {s}) was already added: a cluster arrives whole, once")
        self.advance(day)
        self._seen.add((name, s))
        self._absorb(name, s, bl, {k: {**a, "vec": [float(v) for v in a["vec"]]} for k, a in per_unit_sums.items()})

    def _unit(self, b):
        """Return the sums of bucket ``b`` (``None`` pooled; ``"buy"``, ``"sell"``, ``"top"``); an unused unit is empty."""
        if b is None:
            return self._pooled
        if b in _STRATA:
            return self._buckets.get(b) or _Unit(self._lay, self.block_lengths)
        if isinstance(b, bool) or not isinstance(b, int) or not 0 <= b < self.n_buckets:
            raise ValueError(f"bucket must be None, one of {list(_STRATA)} or an integer in 0..{self.n_buckets - 1}, got {b!r}")
        return self._buckets.get(b) or _Unit(self._lay, self.block_lengths)

    @staticmethod
    def _effective(u):
        """Return the Kish counts of a unit: rows, groups and the cluster dimensions (part C)."""
        n = u.tot[0]
        return {"n_eff": kish_effective(n, u.rw2), "n_groups": u.groups, "g_eff": kish_effective(n, u.cw2),
                "g_eff_names": kish_effective(n, u.names.w2), "g_eff_dates": kish_effective(n, u.dates.w2),
                "g_eff_blocks": [kish_effective(n, t.w2) for t in u.blocks]}

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
        f1 = self._f1()
        out = {"n": v[0] * f1, "rows": u.rows, "clusters": u.clusters, "dates": u.dates.count,
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
        out.update(theta=theta, a=theta[0], sse=sse * f1)
        if lay.k == 1:
            out["b"] = theta[1]
            return out
        rel = max((c[0][0] - u.serr) / c[0][0], 0.0)
        out.update(b_dir=theta[1], b_shape=theta[2], reliability_dir=rel,
                   b_dir_corrected=None if rel <= 0.0 else theta[1] / rel,
                   corr_dir_shape=c[0][1] / math.sqrt(c[0][0] * c[1][1]))
        return out

    def sandwich(self, b, theta, way, block=0, lags=0):
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
            name plus date minus ``(name, date)``, ``"two_block"`` for name plus
            block minus ``(name, block)`` (Cameron-Gelbach-Miller on the time
            blocks of length index ``block``), ``"hac"`` for the Driscoll-Kraay
            band across the ordered blocks with Bartlett ``lags``.
        block : int
            Which configured block length (an index) the block ways use.
        lags : int
            Bartlett lags of the ``"hac"`` band (at least 0).

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
        if way in ("two_block", "hac"):
            self._block_arg(block)
        if isinstance(lags, bool) or not isinstance(lags, int) or lags < 0:
            raise ValueError(f"lags must be an integer >= 0, got {lags!r}")
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
        if way == "hac":
            return self._hac(u, bread, theta, eff, block, lags)
        if way == "two":
            other, pairs, g_other, count_other = u.dates, None, eff["g_eff_dates"], u.dates.count
        else:
            other, g_other, count_other = u.blocks[block], eff["g_eff_blocks"][block], u.blocks[block].count
            pairs = u.pairs[block]
        gn = u.names.count
        if gn < 2 or count_other < 2 or eff["g_eff_names"] <= 1.0 or g_other <= 1.0:
            return None
        if pairs is not None:
            pair = _sandwich_matrix(bread, _outer_from_vectors(lay, pairs.vectors(), theta),
                                    kish_effective(u.tot[0], pairs.w2), n_eff, k)
        by_name = _sandwich_matrix(bread, _outer_from_vectors(lay, u.names.vectors(), theta),
                                   eff["g_eff_names"], n_eff, k)
        by_date = _sandwich_matrix(bread, _outer_from_vectors(lay, other.vectors(), theta), g_other, n_eff, k)
        cov = [[by_name[p][q] + by_date[p][q] - pair[p][q] for q in range(k + 1)] for p in range(k + 1)]
        rule = "two_way"
        if not _positive_definite(cov):
            cov = max(by_name, by_date, key=lambda m: m[1][1])
            rule = "max_one_way"
        return {"cov": cov, "rule": rule, "df": min(eff["g_eff_names"], g_other) - 1,
                "n_clusters": [gn, count_other, u.clusters],
                "g_eff": [eff["g_eff_names"], g_other, kish_effective(u.tot[0], pairs.w2) if pairs else eff["g_eff"]]}

    def _block_arg(self, block):
        """Validate a block-length index."""
        if isinstance(block, bool) or not isinstance(block, int) or not 0 <= block < self.block_lengths:
            raise ValueError(f"block must be an integer in 0..{self.block_lengths - 1}, got {block!r}")

    @staticmethod
    def _hac(u, bread, theta, eff, block, lags):
        """Return the Driscoll-Kraay band across the ordered blocks (Bartlett ``lags``), or ``None``."""
        lay, k = u.lay, u.lay.k
        table = u.blocks[block]
        g_eff = eff["g_eff_blocks"][block]
        if table.count < 2 or g_eff <= 1.0:
            return None
        present = {i: lay.score(theta, table.vector(i)) for i in table.keys()}
        lo, hi = min(present), max(present)
        zero = [0.0] * (k + 1)
        scores = [present.get(i, zero) for i in range(lo, hi + 1)]
        q = k + 1
        meat = [[0.0] * q for _ in range(q)]
        for s in scores:
            for a in range(q):
                for c in range(q):
                    meat[a][c] += s[a] * s[c]
        for lag in range(1, lags + 1):
            wgt = 1.0 - lag / (lags + 1.0)
            for g in range(len(scores) - lag):
                for a in range(q):
                    for c in range(q):
                        meat[a][c] += wgt * (scores[g][a] * scores[g + lag][c] + scores[g + lag][a] * scores[g][c])
        cov = _sandwich_matrix(bread, meat, g_eff, eff["n_eff"], k)
        return {"cov": cov, "rule": "hac", "df": g_eff - 1, "n_clusters": [table.count], "g_eff": [g_eff]}

    def block_vectors(self, b, block=0):
        """Return the stored block moment vectors of a unit, in time order (present blocks only).

        These are the block score inputs of the wild-cluster test and the HAC band: each vector is the
        weighted sum of the unit's rows in one block, in the state's current weight units.

        Parameters
        ----------
        b : int, str or None
            The unit (as for :meth:`fit`).
        block : int
            Which configured block length (an index).

        Returns
        -------
        list of list of float
            One moment vector (``N, ...``) per block, ascending block index.
        """
        self._block_arg(block)
        table = self._unit(b).blocks[block]
        f1 = self._f1()
        return [[x * f1 for x in table.vector(i)] for i in table.keys()]

    def bread(self, b):
        """Return ``(X'WX)^-1`` of a unit in the state's current weight units, or ``None`` if it cannot be fitted."""
        u = self._unit(b)
        if self._centred(u) is None:
            return None
        k = self.regressors
        inv = _solve(u.lay.gram(u.tot), [[1.0 if i == j else 0.0 for j in range(k + 1)] for i in range(k + 1)])
        f1 = self._f1()
        return [[x / f1 for x in row] for row in inv]

    def fit_ratio(self, b, block=0):
        """Return the ratio ``L = sum w r / sum w e`` of a unit with its linearised block scores.

        The tail level of ADR-0311 D5 (``b = "top"``): how much of the predicted edge was realized. With two
        regressors ``e`` is ``e_dir + e_shape``. The block score is ``z_g = R_g - L E_g``; the band is the CR1
        sandwich ``G_eff/(G_eff-1) (n_eff-1)/(n_eff-2) sum z_g^2 / E^2`` on the blocks of length index ``block``.

        Returns
        -------
        dict
            ``L`` (``None`` when there are no rows or ``sum w e`` is 0), ``R``, ``E``, ``rows``, ``clusters``,
            ``n_eff``, ``g_eff_blocks``, ``scores`` (ascending block index), ``se`` and ``df`` (``None`` below
            two effective blocks).
        """
        self._block_arg(block)
        u = self._unit(b)
        lay, v = u.lay, u.tot
        e_tot = v[1] if lay.k == 1 else v[1] + v[2]
        eff = self._effective(u)
        f1 = self._f1()
        out = {"L": None, "R": v[lay.r] * f1, "E": e_tot * f1, "rows": u.rows, "clusters": u.clusters, "n_eff": eff["n_eff"],
               "g_eff_blocks": eff["g_eff_blocks"], "scores": [], "se": None, "df": None}
        if u.rows == 0 or e_tot == 0.0:
            return out
        ratio = v[lay.r] / e_tot
        out["L"] = ratio
        table = u.blocks[block]
        zs = []
        for i in table.keys():
            c = table.vector(i)
            zs.append(c[lay.r] - ratio * (c[1] if lay.k == 1 else c[1] + c[2]))
        out["scores"] = [z * f1 for z in zs]
        g_eff = eff["g_eff_blocks"][block]
        if table.count >= 2 and g_eff > 1.0 and eff["n_eff"] > 2.0:
            out["se"] = math.sqrt(_cr1(g_eff, eff["n_eff"], 1) * sum(z * z for z in zs)) / abs(e_tot)
            out["df"] = g_eff - 1
        return out

    def to_dict(self):
        """Return the whole state as JSON-safe sums (no row history).

        Returns
        -------
        dict
            Restorable with :meth:`from_dict`; keys are sorted so equal states dump equal.
        """
        names = [n for n, _ in sorted(self._names.items(), key=lambda kv: kv[1])]
        dates = [d for d, _ in sorted(self._dates.items(), key=lambda kv: kv[1])]
        return {
            "n_buckets": self.n_buckets, "decay": self.decay, "t": self._t,
            "regressors": self.regressors, "block_lengths": self.block_lengths, "counts": dict(self.counts),
            "week_index": self._j, "names_registry": names, "dates_registry": dates,
            "seen": sorted([name, day] for name, day in self._seen),
            "pooled": self._pooled.to_dict(names, dates),
            "buckets": {str(k): self._buckets[k].to_dict(names, dates) for k in sorted(self._buckets, key=str)},
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
            state._j = int(blob["week_index"])
            state.counts = {k: int(v) for k, v in blob["counts"].items()}
            state._seen = {(name, day) for name, day in blob["seen"]}
            state._names = {n: i for i, n in enumerate(blob["names_registry"])}
            state._dates = {d: i for i, d in enumerate(blob["dates_registry"])}
            state._pooled = _Unit.from_dict(state._lay, blob["pooled"], state._names, state._dates)
            state._buckets = {(k if k in _STRATA else int(k)): _Unit.from_dict(state._lay, v, state._names, state._dates)
                              for k, v in blob["buckets"].items()}
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed LinearEdgeState dump: {exc!r}") from exc
        return state


# ---------------------------------------------------------------- the wild-cluster test and the reads


def _wild_terms(vectors, q, score):
    """Return per-block ``(u_g, G_g)`` with score ``s_g(theta) = u_g - G_g theta`` and the block count."""
    if not vectors:
        raise ValueError("block_vectors must not be empty")
    p = len(vectors[0])
    if p not in (5, 9) or any(len(v) != p for v in vectors):
        raise ValueError("block_vectors must be moment vectors of 5 or 9 numbers")
    lay = _LAYOUTS[1 if p == 5 else 2]
    if score == "ratio":
        if q != 1:
            raise ValueError("the ratio score map has one parameter")
        terms = [([v[lay.r]], [[v[1] if lay.k == 1 else v[1] + v[2]]]) for v in vectors]
    elif score == "ols":
        if q != lay.k + 1:
            raise ValueError(f"theta_hat must have {lay.k + 1} entries for these block vectors")
        terms = [(lay.xr(v), lay.gram(v)) for v in vectors]
    else:
        raise ValueError(f"score must be 'ols' or 'ratio', got {score!r}")
    return terms


class _Wild:
    """The restricted wild-cluster bootstrap of one hypothesis, formed from stored block sums."""

    def __init__(self, terms, theta_hat, bread, h, h0, factor):
        self.terms, self.bread, self.h, self.h0, self.factor = terms, bread, h, h0, factor
        q = len(theta_hat)
        self.q = q
        bh = bread[h]
        self.theta_hat = [float(x) for x in theta_hat]
        shift = (self.theta_hat[h] - h0) / bread[h][h]
        self.theta_r = [self.theta_hat[i] - bread[i][h] * shift for i in range(q)]
        self.s_r = [self._score(u, g, self.theta_r) for u, g in terms]
        self.c_r = [sum(bh[a] * s[a] for a in range(q)) for s in self.s_r]
        obs = [sum(bh[a] * x for a, x in enumerate(self._score(u, g, self.theta_hat))) for u, g in terms]
        var = factor * sum(c * c for c in obs)
        self.se = math.sqrt(var)
        self.t = (self.theta_hat[h] - h0) / self.se if self.se > 0.0 else math.inf

    @staticmethod
    def _score(u, g, theta):
        return [u[i] - sum(g[i][j] * theta[j] for j in range(len(theta))) for i in range(len(theta))]

    def draw(self, eps):
        """Return ``(theta_star, t_star)`` for one vector of block weights."""
        q, bread, h = self.q, self.bread, self.h
        v = [sum(e * s[a] for e, s in zip(eps, self.s_r)) for a in range(q)]
        delta = [sum(bread[i][a] * v[a] for a in range(q)) for i in range(q)]
        theta_star = [self.theta_r[i] + delta[i] for i in range(q)]
        bh = bread[h]
        total = 0.0
        for (_, g), e, c_r in zip(self.terms, eps, self.c_r):
            gd = [sum(g[a][j] * delta[j] for j in range(q)) for a in range(q)]
            c = e * c_r - sum(bh[a] * gd[a] for a in range(q))
            total += c * c
        var = self.factor * total
        return theta_star, (theta_star[h] - self.h0) / math.sqrt(var) if var > 0.0 else math.inf


def _wild_draw(block_vectors, theta_hat, bread, h0_index, h0_value, eps, factor=1.0, score="ols"):
    """Return ``(theta_r, theta_star, t_star)`` of ONE draw with the given block weights ``eps``."""
    terms = _wild_terms(block_vectors, len(theta_hat), score)
    w = _Wild(terms, theta_hat, bread, h0_index, float(h0_value), factor)
    theta_star, t_star = w.draw(list(eps))
    return w.theta_r, theta_star, t_star


def wild_cluster_test(block_vectors, theta_hat, bread, h0_index, h0_value, alternative, level, draws, seed,
                      factor=None, score="ols"):
    """Test ``theta[h0_index] = h0_value`` by the restricted wild-cluster bootstrap over time blocks.

    Formed entirely from the stored block moment vectors (so it is incremental and deterministic). The
    restricted estimate ``theta_r`` imposes the null; per draw each block ``g`` gets a weight ``eps_g``,
    ``theta* = theta_r + bread sum_g eps_g s_g`` with ``s_g`` the restricted block score, the bootstrap
    block scores are ``G_g (theta_r - theta*) + eps_g s_g`` (``G_g = X_g'WX_g``), and ``t*`` is the
    bootstrap CR1 t of the tested coefficient. The weights are Webb's six-point weights when the RAW block
    count is below 12, else Rademacher; all ``6^G`` (``2^G``) patterns are enumerated when there are at most
    ``10^5``, else ``draws`` random patterns from ``random.Random(seed)``.

    Parameters
    ----------
    block_vectors : list of list of float
        The stored block moment vectors (:meth:`LinearEdgeState.block_vectors`), at least two.
    theta_hat : sequence of float
        The unrestricted estimate.
    bread : list of list of float
        ``(X'WX)^-1`` in the same weight units as the block vectors (:meth:`LinearEdgeState.bread`).
    h0_index : int
        The coefficient tested.
    h0_value : float
        Its value under the null.
    alternative : str
        ``"greater"`` (reject when ``theta_h`` is established above the null), ``"less"`` or ``"two_sided"``.
    level : float
        The confidence level in (0, 1); the test rejects when ``p < (1 - level) / 2`` (one-sided) or
        ``p < 1 - level`` (two-sided).
    draws : int
        Random draws when the patterns are too many to enumerate (at least 1).
    seed : int
        Seed of the random draws.
    factor : float, optional
        The CR1 factor applied to the sandwich (default ``G / (G - 1)`` on the raw block count). A common
        factor does not change ``p``; it scales ``t``.
    score : str
        ``"ols"`` (the regression score map) or ``"ratio"`` (the one-parameter ratio ``sum R / sum E``).

    Returns
    -------
    dict
        ``p``, ``reject``, ``t`` (observed), ``se``, ``scheme`` (``"webb"`` or ``"rademacher"``), ``draws``
        (patterns used) and ``theta_restricted``.

    Raises
    ------
    ValueError
        On fewer than two blocks, a bad argument, or a zero standard error.
    """
    if alternative not in ("greater", "less", "two_sided"):
        raise ValueError(f"alternative must be 'greater', 'less' or 'two_sided', got {alternative!r}")
    if isinstance(level, bool) or not number_ok(level) or not 0.0 < level < 1.0:
        raise ValueError(f"level must be a number in (0, 1), got {level!r}")
    for what, v, low in (("draws", draws, 1), ("seed", seed, None)):
        if isinstance(v, bool) or not isinstance(v, int) or (low is not None and v < low):
            raise ValueError(f"{what} must be an integer" + ("" if low is None else f" >= {low}") + f", got {v!r}")
    q = len(theta_hat)
    if isinstance(h0_index, bool) or not isinstance(h0_index, int) or not 0 <= h0_index < q:
        raise ValueError(f"h0_index must be an integer in 0..{q - 1}, got {h0_index!r}")
    if isinstance(h0_value, bool) or not number_ok(h0_value):
        raise ValueError(f"h0_value must be a finite number, got {h0_value!r}")
    terms = _wild_terms(block_vectors, q, score)
    g_raw = len(terms)
    if g_raw < 2:
        raise ValueError("the wild-cluster test needs at least two blocks")
    cr1 = g_raw / (g_raw - 1) if factor is None else float(factor)
    w = _Wild(terms, theta_hat, bread, h0_index, float(h0_value), cr1)
    if not w.se > 0.0:
        raise ValueError("the observed standard error is zero")
    webb = g_raw < _WEBB_BELOW
    weights = _WEBB_WEIGHTS if webb else (-1.0, 1.0)
    patterns = len(weights) ** g_raw
    if patterns <= _ENUMERATE_MAX:
        stream = itertools.product(weights, repeat=g_raw)
        used = patterns
    else:
        rng = random.Random(seed)
        stream = ([rng.choice(weights) for _ in range(g_raw)] for _ in range(draws))
        used = draws
    tol = 1e-12 * max(1.0, abs(w.t))
    hits = 0
    for eps in stream:
        t_star = w.draw(eps)[1]
        if alternative == "greater":
            hits += t_star >= w.t - tol
        elif alternative == "less":
            hits += t_star <= w.t + tol
        else:
            hits += abs(t_star) >= abs(w.t) - tol
    p = hits / used
    alpha = (1.0 - level) / 2.0 if alternative != "two_sided" else 1.0 - level
    return {"p": p, "reject": p < alpha, "t": w.t, "se": w.se, "scheme": "webb" if webb else "rademacher",
            "draws": used, "theta_restricted": list(w.theta_r)}


def three_way_verdict(pass_rejects, fail_rejects, ok=True):
    """Return ``"PASS"``, ``"FAIL"`` or ``"UNRESOLVED"`` from the one-sided tests of a read (ADR-0311 D5).

    PASS iff ``ok`` and every test in ``pass_rejects`` rejected (each statistic is established above 0);
    FAIL iff ``ok`` and every test in ``fail_rejects`` rejected (each statistic is established below its
    material floor); anything else, including a read whose fill checks failed (``ok`` False), is
    UNRESOLVED.

    Parameters
    ----------
    pass_rejects : sequence of bool
        ``reject`` flags of the tests ``statistic <= 0``.
    fail_rejects : sequence of bool
        ``reject`` flags of the tests ``statistic >= floor``.
    ok : bool
        Whether the fill-rate and sensitivity checks hold (D2(iv)).

    Returns
    -------
    str
        The verdict.
    """
    if ok and pass_rejects and all(pass_rejects):
        return "PASS"
    if ok and fail_rejects and all(fail_rejects):
        return "FAIL"
    return "UNRESOLVED"


def _floor_param(name, value):
    if isinstance(value, bool) or not number_ok(value) or not 0.0 < value <= 1.0:
        raise ValueError(f"{name} must be a finite number in (0, 1], got {value!r}")
    return float(value)


def _statistic_tests(value, vectors, theta_hat, bread, h, factor, score, floor, level, draws, seed):
    """Run the two one-sided tests of one statistic: ``> 0`` and ``< floor``."""
    greater = wild_cluster_test(vectors, theta_hat, bread, h, 0.0, "greater", level, draws, seed, factor, score)
    less = wild_cluster_test(vectors, theta_hat, bread, h, floor, "less", level, draws, seed, factor, score)
    return {"value": value, "t": greater["t"], "se": greater["se"], "p_gt": greater["p"], "p_lt": less["p"],
            "scheme": greater["scheme"], "draws": greater["draws"], "gt0": greater["reject"],
            "lt_floor": less["reject"]}


def read_one(state, block, level, draws, seed, b0_floor, tail_level_floor, ok=True):
    """Return read 1 of ADR-0311 D5 from a one-regressor state: ``b_0`` and ``L_top``, with the verdict.

    ``b_0`` is the pooled slope of ``r`` on ``e``; ``L_top`` the tail level ``sum w r / sum w e`` of the
    ``"top"`` unit. Each is tested ``> 0`` and ``< floor`` by :func:`wild_cluster_test` on the blocks of
    length index ``block`` (the quarter blocks). The verdict is :func:`three_way_verdict` with ``ok`` the
    fill-rate and sensitivity checks the caller made.

    Parameters
    ----------
    state : LinearEdgeState
        A one-regressor state holding the reported rows, with a ``"top"`` unit.
    block : int
        The block-length index to test on.
    level : float
        The confidence level of the fixed-sequence tests.
    draws, seed : int
        As for :func:`wild_cluster_test`.
    b0_floor, tail_level_floor : float
        The material floors in (0, 1].
    ok : bool
        Whether the fill checks hold.

    Returns
    -------
    dict
        ``b0`` and ``ltop`` (each ``value``, ``t``, ``se``, ``p_gt``, ``p_lt``, ``scheme``, ``draws``, ``gt0``,
        ``lt_floor``, or ``None`` when the statistic cannot be formed) and ``verdict``.
    """
    if not isinstance(state, LinearEdgeState) or state.regressors != 1:
        raise ValueError("read_one needs a one-regressor LinearEdgeState")
    b0f, tlf = _floor_param("b0_floor", b0_floor), _floor_param("tail_level_floor", tail_level_floor)
    out = {"b0": None, "ltop": None, "verdict": "UNRESOLVED"}
    fit = state.fit(None)
    if fit["theta"] is not None and len(state.block_vectors(None, block)) >= 2:
        eff = fit
        factor = _cr1(eff["g_eff_blocks"][block], eff["n_eff"], 1) if eff["g_eff_blocks"][block] > 1.0 else None
        if factor is not None and eff["n_eff"] > 2.0:
            out["b0"] = _statistic_tests(fit["b"], state.block_vectors(None, block), fit["theta"], state.bread(None),
                                         1, factor, "ols", b0f, level, draws, seed)
    ratio = state.fit_ratio("top", block)
    if ratio["L"] is not None and ratio["se"] is not None:
        factor = _cr1(ratio["g_eff_blocks"][block], ratio["n_eff"], 1)
        out["ltop"] = _statistic_tests(ratio["L"], state.block_vectors("top", block), [ratio["L"]],
                                       [[1.0 / ratio["E"]]], 0, factor, "ratio", tlf, level, draws, seed)
    if out["b0"] and out["ltop"]:
        out["verdict"] = three_way_verdict([out["b0"]["gt0"], out["ltop"]["gt0"]],
                                           [out["b0"]["lt_floor"], out["ltop"]["lt_floor"]], ok)
    return out


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
    """Partially pooled coefficient vectors per bucket, from a :class:`LinearEdgeState`.

    Each bucket's fit ``theta_b`` (``(a, b)`` for one regressor, ``(a, b_dir, b_shape)`` for two) with its
    sandwich covariance ``V_b`` is shrunk toward the pooled ``theta_0``::

        theta* = theta_b + V_b (T + V_b)^-1 (theta_0 - theta_b)
        cov*   = T (T + V_b)^-1 V_b

    which equals ``(T^-1 + V^-1)^-1 (T^-1 theta_0 + V^-1 theta_b)`` but needs no inverse of ``V_b``, so a
    bucket that fits exactly (``V_b = 0``) keeps its fit. ``T`` is diagonal, the method-of-moments
    between-bucket variance of each coefficient (variance of ``theta_b`` across the eligible buckets minus
    the mean of their sampling variances), each entry floored at ``tau2_floor``; with fewer than two eligible
    buckets ``T`` is the floor. The dimension is the state's (``k + 1``); one :func:`_solve` serves it.

    The band of ``V_b`` is ``band``: ``"one"`` (``(name, date)`` clusters), ``"two"`` (name + date) or
    ``"two_block"`` (name + 4-week block, the PRIMARY band of ADR-0311 D1, descriptive). A bucket takes the
    pool when it has too few effective (Kish) clusters, no fit (a regressor without variance; for two
    regressors exact collinearity too, counted ``direction_singular``), no sandwich, or, under
    ``"two_block"``, fewer than ``min_blocks`` effective blocks (counted ``pooled_bucket_blocks``). Under
    ``"two_block"`` the diagonal of ``V_b`` is floored at ``V_0,jj G_eff,blocks,0 / G_eff,blocks,b`` (off
    diagonal scaled to keep the correlation; counted ``v_floored``). Until the pooled state has
    ``min_dates`` settlement dates, ``min_clusters`` effective clusters and (``"two_block"``) ``min_blocks``
    effective blocks, the prior holds: every coefficient is 0.

    With one regressor the same shrinkage in one dimension gives variant (a), the slope through the origin
    ``w`` clipped to ``[0, weight_cap]``; ``weight_cap`` applies to nothing else, and the two-regressor map
    has no through-origin variant. Each coefficient gets a lower bound ``x - lcb_k * se`` and, at every level
    in ``ci_levels``, a Student-t interval.

    Parameters
    ----------
    params : dict
        ``min_clusters`` (int >= 2), ``min_dates`` (int >= 1), ``tau2_floor`` (number > 0), ``weight_cap``
        (number > 0), ``lcb_k`` (number >= 0) are required; ``band`` (``"two_block"`` default, ``"two"``,
        ``"one"``), ``min_blocks`` (int >= 2, default 6) and ``ci_levels`` (list of levels in (0, 1), default
        ``[0.90, 0.95]``) default as stated.

    Raises
    ------
    ValueError
        On an unknown or missing key or an invalid value.

    Examples
    --------
    Fit the map inputs of a state::

        pooling = PartialPooling({"min_clusters": 20, "min_dates": 13, "tau2_floor": 1e-6,
                                  "weight_cap": 1.0, "lcb_k": 1.0, "band": "two"})
        out = pooling.fit(state)
        out["buckets"][3]["b"]
    """

    _PARAMS = ("min_clusters", "min_dates", "tau2_floor", "weight_cap", "lcb_k", "band", "min_blocks", "ci_levels")
    _DEFAULTS = {"band": "two_block", "min_blocks": 6, "ci_levels": [0.90, 0.95]}
    _BANDS = ("one", "two", "two_block")

    def __init__(self, params):
        params = self._with_defaults(params)
        _checked_params(params, self._PARAMS)
        self.min_clusters = _count_param(params, "min_clusters", 2, True)
        self.min_dates = _count_param(params, "min_dates", 1, True)
        self.tau2_floor = float(_count_param(params, "tau2_floor", 0.0, False))
        self.weight_cap = float(_count_param(params, "weight_cap", 0.0, False))
        self.lcb_k = self._lcb_k(params["lcb_k"])
        if params["band"] not in self._BANDS:
            raise ValueError(f"band must be one of {list(self._BANDS)}, got {params['band']!r}")
        self.band = params["band"]
        self.min_blocks = _count_param(params, "min_blocks", 2, True)
        levels = params["ci_levels"]
        if not isinstance(levels, (list, tuple)) or not levels or any(
                isinstance(v, bool) or not number_ok(v) or not 0.0 < v < 1.0 for v in levels):
            raise ValueError(f"ci_levels must be a non-empty list of levels in (0, 1), got {levels!r}")
        self.ci_levels = tuple(float(v) for v in levels)

    @classmethod
    def _with_defaults(cls, params):
        """Return ``params`` with the documented defaults for the keys that have one."""
        if not isinstance(params, dict):
            return params
        return {**{k: v for k, v in cls._DEFAULTS.items() if k not in params}, **params}

    @staticmethod
    def _lcb_k(v):
        """Validate ``lcb_k``: a finite number >= 0."""
        if isinstance(v, bool) or not number_ok(v) or v < 0:
            raise ValueError(f"lcb_k must be a finite number >= 0, got {v!r}")
        return float(v)

    def _enough(self, effective_clusters):
        """Say whether a unit has enough effective (Kish) clusters to stand alone."""
        return effective_clusters >= self.min_clusters

    def _cov(self, state, b, theta):
        """Return the sandwich of a unit under the configured band (block length index 0), or ``None``."""
        return state.sandwich(b, theta, self.band, block=0)

    def _floored(self, v, v0, g0, gb, counts):
        """Floor the diagonal of ``v`` at ``v0_jj g0 / gb`` (correlation kept); count a binding floor."""
        q = len(v)
        floor = [v0[j][j] * g0 / gb for j in range(q)]
        diag = [max(v[j][j], floor[j]) for j in range(q)]
        if all(diag[j] == v[j][j] for j in range(q)):
            return v
        counts["v_floored"] += 1
        out = [[0.0] * q for _ in range(q)]
        for i in range(q):
            for j in range(q):
                if i == j:
                    out[i][j] = diag[i]
                elif v[i][i] > 0.0 and v[j][j] > 0.0:
                    out[i][j] = v[i][j] * math.sqrt(diag[i] / v[i][i] * diag[j] / v[j][j])
        return out

    def _line(self, state, b, f, ctx):
        """``(theta, V, df)`` of bucket ``b``, or ``None`` when it takes the pool (reasons counted)."""
        if f["rows"] == 0:
            return None
        counts, k = ctx["counts"], state.regressors
        if f["theta"] is None:
            if k == 2:
                counts["direction_singular"] += 1
            return None
        if not self._enough(f["g_eff"]):
            return None
        two_block = self.band == "two_block"
        if two_block and f["g_eff_blocks"][0] < self.min_blocks:
            counts["pooled_bucket_blocks"] += 1
            return None
        sw = self._cov(state, b, f["theta"])
        if sw is None:
            return None
        v = sw["cov"]
        if two_block and ctx["v0"] is not None:
            v = self._floored(v, ctx["v0"], ctx["g0"], f["g_eff_blocks"][0], counts)
        return f["theta"], v, sw["df"]

    @staticmethod
    def _origin(unit):
        """``(w, variance, df)`` of the slope through the origin (two-way band), or ``None``.

        The variance carries the same Kish CR1 factor as :func:`_sandwich_matrix` (``k = 1``); every sum
        over the name or date table runs in index order, so a resumed state equals a fresh one.
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
        by = [one(sum((cell[4] - w * cell[3]) ** 2 for cell in table.vectors()), g)
              for table, g in ((unit.names, g_name), (unit.dates, g_date))]
        var = by[0] + by[1] - pair
        if not var > 0.0:
            var = max(by)
        return w, var, min(g_name, g_date) - 1

    def _tau(self, items):
        """Method-of-moments between-bucket variances of each coefficient, floored."""
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

    def _bands(self, theta, se, df):
        """Return the Student-t interval of every coefficient at every level of ``ci_levels``."""
        if se is None or df is None or not df > 0.0:
            return {f"{lvl:g}": {"lcb": None, "ucb": None} for lvl in self.ci_levels}
        out = {}
        for lvl in self.ci_levels:
            c = student_t_critical(lvl, df)
            out[f"{lvl:g}"] = {"lcb": [t - c * s for t, s in zip(theta, se)],
                               "ucb": [t + c * s for t, s in zip(theta, se)]}
        return out

    def _record(self, state, b, theta, se, df, origin, pooled):
        """One unit's output row."""
        f = state.fit(b)
        row = {
            "theta": list(theta), "se": None if se is None else list(se), "df": df,
            "lcb": None if se is None else [t - self.lcb_k * s for t, s in zip(theta, se)],
            "bands": self._bands(theta, se, df), "pooled": pooled,
            "clusters": f["clusters"], "rows": f["rows"], "dates": f["dates"], "names": f["names"],
            "g_eff": f["g_eff"], "g_eff_blocks": f["g_eff_blocks"],
        }
        if state.regressors == 1:
            w, se_w = origin
            row.update(
                a=theta[0], b=theta[1], se_a=None if se is None else se[0], se_b=None if se is None else se[1],
                b_lcb=None if se is None else theta[1] - self.lcb_k * se[1], w=w, se_w=se_w,
                w_lcb=self._clip(w - self.lcb_k * se_w) if se_w is not None else 0.0)
        return row

    def fit(self, state):
        """Return the shrunk per-bucket coefficients (and, for one regressor, weights) of a state.

        Parameters
        ----------
        state : LinearEdgeState
            The admitted state (expanding or decayed); it is not changed.

        Returns
        -------
        dict
            ``warm`` (bool), ``pooled`` (the pooled row), ``buckets`` (a list over every bucket index) and
            ``counts`` (``pooled_bucket_blocks``, ``v_floored``, ``direction_singular``,
            ``pooled_bucket_direction``). A row holds ``theta`` and ``se`` (lists over the coefficients,
            ``None`` when unavailable), ``df``, ``lcb`` (``x - lcb_k se``), ``bands`` (per level in
            ``ci_levels``, ``lcb`` and ``ucb`` lists), ``pooled`` (True when the bucket took the pool) and the
            raw ``clusters``, ``rows``, ``dates``, ``names`` with the Kish ``g_eff`` and ``g_eff_blocks``;
            with one regressor also ``a``, ``b``, ``se_a``, ``se_b``, ``b_lcb``, ``w``, ``se_w``, ``w_lcb``.
            Before warm-up every coefficient and weight is 0.

        Raises
        ------
        ValueError
            When ``state`` is not a :class:`LinearEdgeState`.
        """
        if not isinstance(state, LinearEdgeState):
            raise ValueError(f"state must be a LinearEdgeState, got {type(state).__name__}")
        k = state.regressors
        q = k + 1
        counts = {"pooled_bucket_blocks": 0, "v_floored": 0, "direction_singular": 0, "pooled_bucket_direction": 0}
        no_origin = (0.0, None)
        pf = state.fit(None)
        warm = (pf["theta"] is not None and self._enough(pf["g_eff"]) and pf["dates"] >= self.min_dates
                and (self.band != "two_block" or pf["g_eff_blocks"][0] >= self.min_blocks))
        if not warm:
            zero = [0.0] * q
            return {"warm": False, "counts": counts,
                    "pooled": self._record(state, None, zero, None, None, no_origin, True),
                    "buckets": [self._record(state, b, zero, None, None, no_origin, True)
                                for b in range(state.n_buckets)]}
        th0 = pf["theta"]
        sw0 = self._cov(state, None, th0)
        se0 = None if sw0 is None else [math.sqrt(sw0["cov"][i][i]) for i in range(q)]
        df0 = None if sw0 is None else sw0["df"]
        org0 = self._origin(state._unit(None)) if k == 1 else None
        origin0 = (0.0, None) if k == 2 else (0.0 if org0 is None else self._clip(org0[0]),
                                                None if org0 is None else math.sqrt(org0[1]))
        ctx = {"counts": counts, "v0": None if sw0 is None else sw0["cov"], "g0": pf["g_eff_blocks"][0]}
        fits = {b: state.fit(b) for b in range(state.n_buckets)}
        lines = {b: self._line(state, b, fits[b], ctx) for b in range(state.n_buckets)}
        tau = self._tau([(x[0], x[1]) for x in lines.values() if x is not None])
        origins = {}
        tau_w = None
        if k == 1:
            origins = {b: self._origin(state._unit(b)) if self._enough(fits[b]["g_eff"]) else None
                       for b in range(state.n_buckets)}
            tau_w = self._tau([([o[0]], [[o[1]]]) for o in origins.values() if o is not None])
        rows = []
        for b in range(state.n_buckets):
            ln = lines[b]
            if ln is None:
                if k == 2 and fits[b]["rows"] > 0:
                    counts["pooled_bucket_direction"] += 1
                theta, se, df = th0, se0, df0
            else:
                theta, post = self._shrink_line(ln[0], ln[1], th0, tau)
                se, df = [math.sqrt(max(post[i][i], 0.0)) for i in range(q)], ln[2]
            if k == 1:
                org = origins[b]
                if org is None:
                    wse = origin0
                else:
                    wb, vb = self._clip(org[0]), org[1]
                    wse = (self._clip(wb + vb / (tau_w[0] + vb) * (origin0[0] - wb)),
                           math.sqrt(tau_w[0] * vb / (tau_w[0] + vb)))
            else:
                wse = no_origin
            rows.append(self._record(state, b, theta, se, df, wse, ln is None))
        return {"warm": True, "counts": counts, "buckets": rows,
                "pooled": self._record(state, None, th0, se0, df0, origin0, False)}


def _origin_two(unit, cap):
    """Return the clipped no-intercept ``(w_dir, w_shape)`` of a two-regressor unit, or ``None`` if singular."""
    v = unit.tot
    dd, ds, ss, dr, sr = v[3], v[4], v[5], v[7], v[8]
    det = dd * ss - ds * ds
    if unit.rows == 0 or dd <= 0.0 or ss <= 0.0 or det <= _FIT_RTOL * dd * ss:
        return None
    w = ((dr * ss - sr * ds) / det, (sr * dd - dr * ds) / det)
    return [min(max(x, 0.0), cap) for x in w]


def origin_two(pooling, state, warm):
    """Return the no-intercept two-regressor line ``c = w_dir e_dir + w_shape e_shape`` per bucket and pooled.

    The 2x2 normal equations of ``r`` on ``(e_dir, e_shape)`` without an intercept (ADR-0313 X2), each weight
    clipped to ``[0, weight_cap]`` so neither part is ever sign-flipped or amplified. A bucket with no fit
    (a singular 2x2, or too few effective clusters) takes the pooled weights, counted. Before ``warm`` every
    weight is 0 (the prior). No shrinkage between buckets: "pooling" here is the substitution of the pool.

    Parameters
    ----------
    pooling : PartialPooling
        Supplies ``weight_cap`` and the ``min_clusters`` gate.
    state : LinearEdgeState
        A two-regressor state.
    warm : bool
        Whether the map built from the state is warm (the same gate :meth:`PartialPooling.fit` applies).

    Returns
    -------
    dict
        ``warm``, ``pooled`` (the two weights), ``buckets`` (a list of ``{"w": [w_dir, w_shape], "pooled": bool}``
        over every bucket) and ``counts`` (``origin_singular``).

    Raises
    ------
    ValueError
        When ``state`` is not a two-regressor :class:`LinearEdgeState`.
    """
    if not isinstance(state, LinearEdgeState) or state.regressors != 2:
        raise ValueError("origin_two needs a two-regressor LinearEdgeState")
    counts = {"origin_singular": 0}
    pool = _origin_two(state._unit(None), pooling.weight_cap) if warm else None
    if pool is None:
        zero = [0.0, 0.0]
        return {"warm": False, "pooled": zero, "counts": counts,
                "buckets": [{"w": list(zero), "pooled": True} for _ in range(state.n_buckets)]}
    buckets = []
    for b in range(state.n_buckets):
        unit = state._unit(b)
        got = None
        if unit.rows > 0 and pooling._enough(state.fit(b)["g_eff"]):
            got = _origin_two(unit, pooling.weight_cap)
            if got is None:
                counts["origin_singular"] += 1
        buckets.append({"w": pool if got is None else got, "pooled": got is None})
    return {"warm": True, "pooled": pool, "buckets": buckets, "counts": counts}


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
    #: Two-regressor maps only (ADR-0311 A1 part F): the shrunk ``(a, b_dir, b_shape)``, its standard errors,
    #: degrees of freedom and the Student-t bands per configured level; ``None`` for a one-regressor map.
    theta: tuple = None
    se: tuple = None
    df: float = None
    bands: dict = None


_EXTRA_FIELDS = ("theta", "se", "df", "bands")


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
        two = state.regressors == 2
        keys = [k for k in _BucketMap.__dataclass_fields__ if two or k not in _EXTRA_FIELDS]

        def record(x):
            vals = {k: x[k] for k in keys if k in x}
            if two:
                vals = {k: x[k] for k in _EXTRA_FIELDS}
                vals.update({k: x[k] for k in ("pooled", "clusters", "rows", "dates", "names")})
                vals.update(a=x["theta"][0], b=x["theta"][1], se_a=None, se_b=None, b_lcb=None, w=0.0,
                            se_w=None, w_lcb=0.0)
                vals["theta"] = tuple(x["theta"])
                vals["se"] = None if x["se"] is None else tuple(x["se"])
            return _BucketMap(**vals)

        rows = [record(x) for x in out["buckets"]]
        pooled = record(out["pooled"])
        if two:
            names = {}
        else:
            names = {n: adjust.adjust(n) for n in adjust.names()} if out["warm"] else {}
        return cls(t, out["warm"], rows, pooled, names)

    def bucket_only_two(self, bucket, e_dir, e_shape):
        """Return ``a_b + b_dir e_dir + b_shape e_shape`` of a two-regressor map (the ``c2`` of a row).

        Parameters
        ----------
        bucket : int
            The bucket index.
        e_dir, e_shape : float
            The direction edge and the shape edge (``e - e_dir``) in the map's units.

        Returns
        -------
        float
            The calibrated edge; ``0.0`` before warm-up (every coefficient is the prior 0).

        Raises
        ------
        ValueError
            On a one-regressor map or a non-finite edge.
        """
        rec = self._bucket(bucket)
        if rec.theta is None:
            raise ValueError("bucket_only_two needs a map built from a two-regressor state")
        d, sh = self._edge(e_dir), self._edge(e_shape)
        return rec.theta[0] + rec.theta[1] * d + rec.theta[2] * sh

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
    """Return a frozen record's fields as a dict (the two-regressor extras only when present)."""
    return {k: (list(v) if isinstance(v, tuple) else v) for k in record.__dataclass_fields__
            for v in [getattr(record, k)] if v is not None or k not in _EXTRA_FIELDS}


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


class CalibratedObjective:
    """The calibrated-edge term of the integrated program: ``adj_i = c_i - e_i`` per item (ADR-0313 section A).

    Pure functions over one loaded ``map-<t>.json`` body (a dict; this class reads no file). The program's
    value is ``[credit . x - worst(x)] + adj . x``; the caller adds ``adj`` to the objective only and leaves
    credit, losses, reserves and caps raw. Forms (``form``): ``slope`` (default) ``c = w_b e`` with the
    bucket's clipped through-origin weight, so ``|c| <= |e|`` and a sign never flips; ``affine`` the stored
    line ``scale a_b + b_b e`` (refused unless the caller passes ``affine_read=True`` after checking the
    walk's per-side read); ``identity`` ``adj = 0.0`` (the ``control`` twin, which still reads the ``warm``
    flag so it sits in cash on the same weeks).

    No trade is signalled by ``None`` (:data:`NO_TRADE`): the map is missing or cold, the bucket is not in
    the map, or the item has no walk row (``e`` or, with two regressors, ``e_dir`` is ``None``). The caller
    excludes such items; it never falls back to the raw edge.

    Parameters
    ----------
    params : dict
        The ``calibration`` block, keys exactly :attr:`_PARAMS` (``control`` optional, ``form`` defaults to
        ``slope``, ``variant`` to ``point``, ``line`` to ``bucket``, ``regressors`` to 1, ``edge_scale`` to
        ``notional``, ``warmup`` to ``no_trade``, ``min_warm_weeks`` to 8; ``source`` and ``state`` required).
    affine_read : bool, optional
        Set by the attach owner once the walk's per-side read and a significant intercept exist.

    Raises
    ------
    _Refusal
        ``calibration_params`` (malformed), ``calibration_affine_unread``, ``calibration_identity_not_control``,
        ``calibration_line_unsupported``, ``calibration_variant_unsupported``.

    Examples
    --------
    ``w = 0.6`` and ``e = 0.60`` give ``c = 0.36`` and ``adj = -0.24``; the mirror side gets ``+0.24``::

        obj = CalibratedObjective({"source": "edge_maps", "state": "expanding_fill"})
        adj = obj.adjust(map_body, bucket=0, name="AAA", e=0.60)
        mirror = obj.mirror(adj)
    """

    _PARAMS = ("source", "state", "edge_scale", "regressors", "line", "form", "variant", "warmup",
               "min_warm_weeks", "control")
    _REQUIRED = ("source", "state")
    _STATES = ("expanding_fill", "decayed_fill", "expanding_decision", "decayed_decision")
    _SCALES = ("notional", "scale", "none")
    _FORMS = ("slope", "affine", "identity")
    NO_TRADE = None

    def __init__(self, params, affine_read=False):
        if not isinstance(params, dict):
            raise _Refusal("calibration_params", f"the calibration block must be an object, got {params!r}")
        unknown = sorted(set(params) - set(self._PARAMS))
        missing = [k for k in self._REQUIRED if k not in params]
        if unknown or missing:
            raise _Refusal("calibration_params", f"unknown keys {unknown}, missing keys {missing}")
        p = {"edge_scale": "notional", "regressors": 1, "line": "bucket", "form": "slope", "variant": "point",
             "warmup": "no_trade", "min_warm_weeks": 8, "control": False, **params}
        for key, allowed in (("source", ("edge_maps",)), ("state", self._STATES), ("edge_scale", self._SCALES),
                             ("line", ("bucket", "bucket_name")), ("form", self._FORMS),
                             ("variant", ("point", "lcb")), ("warmup", ("no_trade",))):
            if not isinstance(p[key], str) or p[key] not in allowed:
                raise _Refusal("calibration_params", f"{key} must be one of {list(allowed)}, got {p[key]!r}")
        if isinstance(p["regressors"], bool) or p["regressors"] not in (1, 2):
            raise _Refusal("calibration_params", f"regressors must be 1 or 2, got {p['regressors']!r}")
        if isinstance(p["min_warm_weeks"], bool) or not isinstance(p["min_warm_weeks"], int) \
                or p["min_warm_weeks"] < 1:
            raise _Refusal("calibration_params", f"min_warm_weeks must be an integer >= 1, got {p['min_warm_weeks']!r}")
        if not isinstance(p["control"], bool):
            raise _Refusal("calibration_params", f"control must be a boolean, got {p['control']!r}")
        if (p["form"] == "identity") != p["control"]:
            raise _Refusal("calibration_identity_not_control", "form identity needs control true, and only it")
        if p["form"] == "affine" and not affine_read:
            raise _Refusal("calibration_affine_unread", "affine needs the walk's per-side read and a significant "
                           "intercept (ADR-0313 A4)")
        if p["form"] == "slope" and p["line"] != "bucket":
            raise _Refusal("calibration_line_unsupported", "the through-origin slope has no name-adjusted line")
        if p["variant"] == "lcb" and p["regressors"] == 2:
            raise _Refusal("calibration_variant_unsupported", "the two-regressor line has no lower-bound variant")
        self._p = p

    @property
    def params(self):
        """The resolved block (defaults filled in)."""
        return dict(self._p)

    def __getattr__(self, key):
        """Expose each resolved param as an attribute (``form``, ``state``, ``regressors``, ...)."""
        p = self.__dict__.get("_p")
        if p is not None and key in p:
            return p[key]
        raise AttributeError(key)

    def check_discount(self, rho, risk_charge=False):
        """Refuse a lower-confidence map on a method that already pays a radius or a risk charge.

        Raises
        ------
        _Refusal
            ``calibration_double_discount`` when ``variant == "lcb"`` and ``rho > 0`` or ``risk_charge``.
        """
        if self._p["variant"] == "lcb" and (rho > 0 or risk_charge):
            raise _Refusal("calibration_double_discount", "the lcb map is allowed only at rho 0 with no risk_charge")

    @staticmethod
    def check_weight_cap(weight_cap):
        """Refuse a walk whose ``weight_cap`` exceeds 1 (``|c| <= |e|`` would not hold)."""
        if isinstance(weight_cap, bool) or not number_ok(weight_cap) or weight_cap > 1.0:
            raise _Refusal("calibration_weight_cap", f"weight_cap must be a number <= 1, got {weight_cap!r}")

    @staticmethod
    def mirror(adj):
        """Return the adjustment of the mirror side of an option: ``-adj`` (``None`` stays ``None``)."""
        return None if adj is None else 0.0 - adj

    def _part(self, edge_map, key):
        """Return ``edge_map[key]`` (a dict), refusing a map of the wrong shape."""
        got = edge_map.get(key) if isinstance(edge_map, dict) else None
        if not isinstance(got, dict):
            raise _Refusal("calibration_map_malformed", f"the map has no {key!r} block")
        return got

    def _blocks(self, edge_map):
        """Return ``(line_state, origin_two)`` blocks of the map this objective reads."""
        if self._p["regressors"] == 1:
            return self._part(self._part(edge_map, "states"), self._p["state"]), None
        line = self._part(self._part(edge_map, "direction"), "dir_" + self._p["state"])
        return line, self._part(edge_map, "origin_two")

    @staticmethod
    def _index(blocks, bucket):
        """Return the valid bucket index or ``None``."""
        if isinstance(bucket, bool) or not isinstance(bucket, int) or bucket < 0:
            return None
        return bucket if bucket < len(blocks) else None

    def warm(self, edge_map, bucket):
        """Return whether the item's bucket has a usable warm map (``False`` is a no-trade week for it).

        Parameters
        ----------
        edge_map : dict or None
            One loaded ``map-<t>.json`` body; ``None`` (no map) is not warm.
        bucket : int
            The item's bucket index; one outside the map is not warm.

        Returns
        -------
        bool

        Raises
        ------
        _Refusal
            ``calibration_map_malformed`` when a present map lacks the block this objective reads.
        """
        if edge_map is None:
            return False
        line, origin = self._blocks(edge_map)
        for blk in (line, origin):
            if blk is not None and "warm" not in blk:
                raise _Refusal("calibration_map_malformed", "a map block has no warm flag")
        if not line["warm"] or (origin is not None and not origin["warm"]):
            return False
        return self._index(line.get("buckets", ()), bucket) is not None

    @staticmethod
    def _number(value, what):
        if isinstance(value, bool) or not number_ok(value):
            raise ValueError(f"{what} must be a finite number, got {value!r}")
        return float(value)

    def adjust(self, edge_map, bucket, name, e, e_dir=None, scale=None, h_i=0.0):
        """Return ``adj = c - e`` for one item in the units of ``e``, or ``None`` for no trade.

        Parameters
        ----------
        edge_map : dict or None
            One loaded ``map-<t>.json`` body.
        bucket : int
            The item's bucket index.
        name : str
            The underlying (read only by the name-adjusted affine line).
        e : float or None
            The item's raw predicted edge (``None``: the walk gave it no row, so no trade).
        e_dir : float or None
            The direction part of ``e`` (two regressors only; ``None``: no row, so no trade).
        scale : float or None
            The per-contract scale of the intercept (affine only; the slope is scale-free).
        h_i : float
            The item's effective hedge ratio in ``[0, 1]``; nonzero needs two regressors.

        Returns
        -------
        float or None
            Two regressors: ``c = (1 - h) w_dir e_dir + w_shape (e - e_dir)`` against the hedged raw edge
            ``(1 - h) e_dir + (e - e_dir)``. One regressor: ``c = w_b e`` against ``e``.

        Raises
        ------
        _Refusal
            ``hedge_needs_direction_map`` (``h_i > 0`` with one regressor), ``calibration_weight_cap`` (a stored
            weight above 1), ``calibration_map_malformed``.
        ValueError
            On a non-finite input, ``h_i`` outside ``[0, 1]`` or an affine item without a positive ``scale``.
        """
        h = self._number(h_i, "h_i")
        if not 0.0 <= h <= 1.0:
            raise ValueError(f"h_i must be in [0, 1], got {h_i!r}")
        if scale is not None and self._number(scale, "scale") <= 0.0:
            raise ValueError(f"scale must be positive, got {scale!r}")
        two = self._p["regressors"] == 2
        if h > 0.0 and not two:
            raise _Refusal("hedge_needs_direction_map", "a hedged item needs the two-regressor map")
        if e is None or (two and e_dir is None):
            return self.NO_TRADE
        e = self._number(e, "e")
        d = self._number(e_dir, "e_dir") if two else 0.0
        if edge_map is None or not self.warm(edge_map, bucket):
            return self.NO_TRADE
        if self._p["form"] == "identity":
            return 0.0
        line, origin = self._blocks(edge_map)
        if self._p["form"] == "slope":
            return self._slope(line, origin, bucket, e, d, h)
        return self._affine(line, bucket, name, e, d, h, scale)

    def _weight(self, w):
        """Return a stored through-origin weight, refusing one outside ``[0, 1]``."""
        if isinstance(w, bool) or not number_ok(w) or w < 0.0:
            raise _Refusal("calibration_map_malformed", f"a through-origin weight must be >= 0, got {w!r}")
        if w > 1.0:
            raise _Refusal("calibration_weight_cap", f"stored weight {w!r} exceeds 1")
        return float(w)

    def _slope(self, line, origin, bucket, e, d, h):
        if origin is None:
            rec = line["buckets"][bucket]
            w = self._weight(rec["w_lcb" if self._p["variant"] == "lcb" else "w"])
            return w * e - e
        wd, ws = (self._weight(x) for x in origin["buckets"][bucket]["w"])
        shape = e - d
        return (1.0 - h) * wd * d + ws * shape - ((1.0 - h) * d + shape)

    def _affine(self, line, bucket, name, e, d, h, scale):
        sc = 1.0 if self._p["edge_scale"] == "none" else scale
        if sc is None:
            raise ValueError("the affine form needs the item's scale")
        rec = line["buckets"][bucket]
        if self._p["regressors"] == 2:
            a, bd, bs = rec["theta"]
            shape = e - d
            return sc * a + (1.0 - h) * bd * d + bs * shape - ((1.0 - h) * d + shape)
        a, b = rec["a"], rec["b"]
        if self._p["variant"] == "lcb" and rec.get("b_lcb") is not None:
            b = rec["b_lcb"]
        if self._p["line"] == "bucket_name":
            da, db = line.get("names", {}).get(name, (0.0, 0.0))
            a, b = a + da, b + db
        return sc * a + b * e - e
