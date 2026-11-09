"""Rolling edge calibration, core mechanism (ADR-0311, stage 1).

Does a forecast's predicted edge ``e`` become realized edge ``r``? This
module holds the three domain-neutral pieces that question needs before any
walk exists, all stdlib:

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

Refusals that carry a stable code raise a private ``ValueError`` subclass
whose ``code`` attribute and message prefix are that code.
"""

import bisect
import math
from datetime import date, datetime

from dskit.pipeline.kinds_split import label_reaches
from dskit.pipeline.records import cluster_ok, number_ok
from dskit.pipeline.stats import NO_VARIANCE_RTOL

__all__ = [
    "DEVELOPMENT_SEAL",
    "LinearEdgeState",
    "ReliabilityBuckets",
    "SettledBefore",
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


# Column order of the five cluster sums: rows, e, r, e*e, e*r (the sixth running sum, r*r, is not
# a cluster-score input).
_TRI = [(i, j) for i in range(5) for j in range(i, 5)]
_FIT_RTOL = NO_VARIANCE_RTOL


def _score_map(theta):
    """Return the 2x5 matrix taking a cluster's five sums to its OLS score at ``theta``."""
    a, b = theta
    return ((-a, -b, 1.0, 0.0, 0.0), (0.0, -a, 0.0, -b, 1.0))


def _outer_from_cross(cross, theta):
    """``M C M'`` for the symmetric 5x5 cross-product ``cross`` (15 numbers)."""
    c = [[0.0] * 5 for _ in range(5)]
    for k, (i, j) in enumerate(_TRI):
        c[i][j] = c[j][i] = cross[k]
    m = _score_map(theta)
    mc = [[sum(m[p][i] * c[i][j] for i in range(5)) for j in range(5)] for p in range(2)]
    return [[sum(mc[p][j] * m[q][j] for j in range(5)) for q in range(2)] for p in range(2)]


def _outer_from_vectors(vectors, theta):
    """``sum M x x' M'`` over five-vectors ``x``."""
    m = _score_map(theta)
    out = [[0.0, 0.0], [0.0, 0.0]]
    for x in vectors:
        s = [sum(m[p][i] * x[i] for i in range(5)) for p in range(2)]
        for p in range(2):
            for q in range(2):
                out[p][q] += s[p] * s[q]
    return out


def _sandwich_matrix(bread, meat, groups, rows):
    """CR1 ``G/(G-1) * (n-1)/(n-2) * bread meat bread``."""
    factor = groups / (groups - 1) * (rows - 1) / (rows - 2)
    bm = [[sum(bread[p][k] * meat[k][q] for k in range(2)) for q in range(2)] for p in range(2)]
    return [[factor * sum(bm[p][k] * bread[k][q] for k in range(2)) for q in range(2)] for p in range(2)]


class _Unit:
    """Running sums of one regression: a bucket, or the pooled rows."""

    def __init__(self):
        self.tot = [0.0] * 6  # rows, e, r, e*e, e*r, r*r (weighted)
        self.cross = [0.0] * 15  # sum over (name, date) clusters of x x', x the five cluster sums
        self.names = {}  # name -> weighted five-vector
        self.dates = {}  # settlement date -> weighted five-vector
        self.rows = 0  # unweighted counts, for the CR1 factor and degrees of freedom
        self.clusters = 0

    def add(self, name, day, e, r):
        """Fold one whole (name, date) cluster of rows into the sums."""
        x = [float(len(e)), sum(e), sum(r), sum(v * v for v in e), sum(u * v for u, v in zip(e, r))]
        for k in range(5):
            self.tot[k] += x[k]
        self.tot[5] += sum(v * v for v in r)
        for k, (i, j) in enumerate(_TRI):
            self.cross[k] += x[i] * x[j]
        for table, key in ((self.names, name), (self.dates, day)):
            vec = table.setdefault(key, [0.0] * 5)
            for k in range(5):
                vec[k] += x[k]
        self.rows += len(e)
        self.clusters += 1

    def decay(self, lam):
        """Age every sum by one decision week."""
        self.tot = [v * lam for v in self.tot]
        self.cross = [v * lam * lam for v in self.cross]
        for table in (self.names, self.dates):
            for key, vec in table.items():
                table[key] = [v * lam for v in vec]

    def to_dict(self):
        return {
            "tot": list(self.tot), "cross": list(self.cross), "rows": self.rows,
            "clusters": self.clusters,
            "names": {k: list(self.names[k]) for k in sorted(self.names)},
            "dates": {k: list(self.dates[k]) for k in sorted(self.dates)},
        }

    @classmethod
    def from_dict(cls, blob):
        unit = cls()
        unit.tot = [float(v) for v in blob["tot"]]
        unit.cross = [float(v) for v in blob["cross"]]
        unit.rows, unit.clusters = int(blob["rows"]), int(blob["clusters"])
        unit.names = {k: [float(v) for v in vec] for k, vec in blob["names"].items()}
        unit.dates = {k: [float(v) for v in vec] for k, vec in blob["dates"].items()}
        if len(unit.tot) != 6 or len(unit.cross) != 15:
            raise ValueError("unit sums have the wrong size")
        return unit


class LinearEdgeState:
    """``r = a + b e`` per bucket and pooled, from running sums, with cluster-robust SEs.

    The clock is the sequence of decision weeks the caller advances through:
    each :meth:`advance` to a later ``t`` ages a decayed state by one week
    (call it for every week, empty ones too), then that week's settled
    clusters are added. A ``(name, settlement date)`` cluster arrives whole,
    once. The one-way band clusters on ``(name, date)``; the two-way band is
    Cameron-Gelbach-Miller ``name + date - (name, date)``, the same rule as
    :func:`~dskit.pipeline.ordering.clustered_calibration_slope`. The CR1
    factor and degrees of freedom use the raw (unweighted) row and cluster
    counts, also when the state is decayed.

    Parameters
    ----------
    n_buckets : int
        Number of buckets (at least 1); bucket indices run ``0..n_buckets-1``.
    decay : float or None
        ``None`` for the expanding state, else the weekly factor ``lambda`` in (0, 1).

    Raises
    ------
    ValueError
        On a bad bucket count or decay.

    Examples
    --------
    One cluster settled before the decision week::

        state = LinearEdgeState(24, None)
        state.advance("2020-03-06")
        state.add_cluster("2020-03-06", "AAA", "2020-03-01", "2020-02-26",
                          [3, 3, 7], [1.0, 2.0, 0.5], [0.8, 2.5, 0.1])
        state.fit(None)["n"]  # -> 3.0
    """

    def __init__(self, n_buckets, decay):
        if isinstance(n_buckets, bool) or not isinstance(n_buckets, int) or n_buckets < 1:
            raise ValueError(f"n_buckets must be an integer >= 1, got {n_buckets!r}")
        if decay is not None and (isinstance(decay, bool) or not number_ok(decay) or not 0.0 < decay < 1.0):
            raise ValueError(f"decay must be None or a number in (0, 1), got {decay!r}")
        self.n_buckets = n_buckets
        self.decay = None if decay is None else float(decay)
        self._t = None
        self._pooled = _Unit()
        self._buckets = {}
        self._seen = set()

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

    def add_cluster(self, t, name, settle_date, fill_date, buckets, e, r):
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
        m = len(e) if isinstance(e, (list, tuple)) else -1
        if m < 1 or not all(isinstance(v, (list, tuple)) and len(v) == m for v in (buckets, r)):
            raise ValueError("buckets, e and r must be non-empty lists of the same length")
        for b in buckets:
            if isinstance(b, bool) or not isinstance(b, int) or not 0 <= b < self.n_buckets:
                raise ValueError(f"bucket {b!r} is not an integer in 0..{self.n_buckets - 1}")
        for v in (*e, *r):
            if isinstance(v, bool) or not number_ok(v):
                raise ValueError(f"e and r must be finite numbers, got {v!r}")
        if (name, s) in self._seen:
            raise ValueError(f"cluster ({name}, {s}) was already added: a cluster arrives whole, once")
        self.advance(day)
        self._seen.add((name, s))
        groups = {}
        for b, ev, rv in zip(buckets, e, r):
            es, rs = groups.setdefault(b, ([], []))
            es.append(float(ev))
            rs.append(float(rv))
        self._pooled.add(name, s, [float(v) for v in e], [float(v) for v in r])
        for b, (es, rs) in groups.items():
            self._buckets.setdefault(b, _Unit()).add(name, s, es, rs)

    def _unit(self, b):
        """Return the sums of bucket ``b`` (``None`` is pooled); an unused bucket is empty."""
        if b is None:
            return self._pooled
        if isinstance(b, bool) or not isinstance(b, int) or not 0 <= b < self.n_buckets:
            raise ValueError(f"bucket must be None or an integer in 0..{self.n_buckets - 1}, got {b!r}")
        return self._buckets.get(b) or _Unit()

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
            ``dates`` and ``names`` (distinct, raw), ``a`` and ``b`` (``None`` when
            the ``e`` variance is zero or there are no rows), ``sse``.

        Raises
        ------
        ValueError
            On a bucket outside the state.
        """
        u = self._unit(b)
        n, se, sr, see, ser, srr = u.tot
        out = {"n": n, "rows": u.rows, "clusters": u.clusters, "dates": len(u.dates),
               "names": len(u.names), "a": None, "b": None, "sse": None}
        if u.rows == 0:
            return out
        varx = see - se * se / n
        if varx <= _FIT_RTOL * see:
            return out
        slope = (ser - se * sr / n) / varx
        icept = (sr - slope * se) / n
        out.update(a=icept, b=slope, sse=srr - icept * sr - slope * ser)
        return out

    def sandwich(self, b, theta, way):
        """Return the CR1 cluster-robust covariance of ``theta = (a, b)`` for a bucket.

        Parameters
        ----------
        b : int or None
            The bucket index, or ``None`` for pooled.
        theta : tuple of float
            The ``(a, b)`` point the scores are taken at (the unit's own fit,
            or a shrunk one).
        way : str
            ``"one"`` for ``(name, settlement date)`` clusters, ``"two"`` for
            name plus date minus ``(name, date)``.

        Returns
        -------
        dict or None
            ``cov`` (2x2), ``rule`` (``"one_way"``, ``"two_way"`` or
            ``"max_one_way"`` when the two-way matrix is not positive definite),
            ``df`` (clusters - 1; two-way: fewest of names and dates - 1) and
            ``n_clusters``. ``None`` when there are too few rows or clusters, or
            the ``e`` variance is zero.

        Raises
        ------
        ValueError
            On an unknown ``way``, a bucket outside the state or a bad ``theta``.
        """
        if way not in _WAYS:
            raise ValueError(f"way must be one of {list(_WAYS)}, got {way!r}")
        if not (isinstance(theta, (list, tuple)) and len(theta) == 2 and all(
                number_ok(v) and not isinstance(v, bool) for v in theta)):
            raise ValueError(f"theta must be two finite numbers, got {theta!r}")
        u = self._unit(b)
        n, se, _, see, _, _ = u.tot
        if u.rows < 3 or u.clusters < 2 or n <= 0 or see - se * se / n <= _FIT_RTOL * see:
            return None
        det = n * see - se * se
        bread = [[see / det, -se / det], [-se / det, n / det]]
        theta = (float(theta[0]), float(theta[1]))
        pair = _sandwich_matrix(bread, _outer_from_cross(u.cross, theta), u.clusters, u.rows)
        if way == "one":
            return {"cov": pair, "rule": "one_way", "df": u.clusters - 1, "n_clusters": [u.clusters]}
        gn, gd = len(u.names), len(u.dates)
        if gn < 2 or gd < 2:
            return None
        by_name = _sandwich_matrix(bread, _outer_from_vectors(u.names.values(), theta), gn, u.rows)
        by_date = _sandwich_matrix(bread, _outer_from_vectors(u.dates.values(), theta), gd, u.rows)
        cov = [[by_name[p][q] + by_date[p][q] - pair[p][q] for q in range(2)] for p in range(2)]
        rule = "two_way"
        if not (cov[0][0] > 0.0 and cov[1][1] > 0.0 and cov[0][0] * cov[1][1] - cov[0][1] * cov[1][0] > 0.0):
            cov = max(by_name, by_date, key=lambda m: m[1][1])
            rule = "max_one_way"
        return {"cov": cov, "rule": rule, "df": min(gn, gd) - 1, "n_clusters": [gn, gd, u.clusters]}

    def to_dict(self):
        """Return the whole state as JSON-safe sums (no row history).

        Returns
        -------
        dict
            Restorable with :meth:`from_dict`; keys are sorted so equal states dump equal.
        """
        return {
            "n_buckets": self.n_buckets, "decay": self.decay, "t": self._t,
            "seen": sorted([name, day] for name, day in self._seen),
            "pooled": self._pooled.to_dict(),
            "buckets": {str(k): self._buckets[k].to_dict() for k in sorted(self._buckets)},
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
            On a missing key or a malformed blob.
        """
        try:
            state = cls(blob["n_buckets"], blob["decay"])
            state._t = blob["t"]
            state._seen = {(name, day) for name, day in blob["seen"]}
            state._pooled = _Unit.from_dict(blob["pooled"])
            state._buckets = {int(k): _Unit.from_dict(v) for k, v in blob["buckets"].items()}
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed LinearEdgeState dump: {exc!r}") from exc
        return state


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
        #: Rows answered with the fallback ``low`` for lack of history (``regime_warmup``).
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
        window = self._window(name, date.fromisoformat(_iso(t, "t")))
        if len(window) < self._min:
            self.warmups += 1
            return "low", True
        return ("high" if value > _median(window) else "low"), False


def _median(values):
    """Return the median of a non-empty list of numbers."""
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
