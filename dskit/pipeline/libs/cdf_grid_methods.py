"""Forecast methods on a CDF grid over a scored-rows table (ADR-0270).

A scored-rows parquet holds one row per forecast: identity columns, a ``fold``, a ``band`` (``fit`` or
``val``), the outcome ``y`` in z units, the quote and settlement dates, and for every forecaster ``<name>`` its
CDF on one declared z grid in the columns ``<name>_cdf_<label>``. :class:`GridCDFMethod` builds ONE new
forecaster from those columns, per fold, by one method, and writes the new CDFs, the learned state and the
paired scored tables against declared comparators. Nothing here knows an asset, a venue or a model family:
the grid, the labels, the score weights, the column roles and the method all come from the document.

The score every method minimizes is the grid threshold-weighted CRPS: the trapezoid, over the grid points,
of ``w_k * (F(z_k) - 1{y <= z_k})^2`` with the actual spacing (at spacing 0.5 this is
``0.5 * (sum f - 0.5 * (f_0 + f_last))``). With all weights one it is the plain grid CRPS (``crps13`` in the
tables). Region Brier scores split the line at the grid points into ``len(grid) + 1`` regions, the first and
last open.

Method kinds are subclasses of one abstract base, chosen from the :data:`METHOD_KINDS` mapping; the driver
never branches on a kind. ``pit_map`` recalibrates one component by the empirical CDF of its own PIT
(optionally per bucket). ``linear_pool`` mixes components with weights on the simplex: the pooled CDF is
linear in the weights, so the mean grid score is a quadratic form in them and is minimized exactly (every
support of the simplex is solved and the best taken), per bucket, per tercile of a column, per threshold (then
rearranged into a monotone CDF) and shrunk toward the global weights. ``best_component`` picks, per cell, the
component with the lowest mean score, kept only when it beats the default by a margin. ``binary_gbm`` is a
LightGBM classifier over (threshold, base logit, extra features) examples with monotone constraints, read back
as a CDF made monotone by a running maximum.

Learning is causal by declaration. ``fit_band`` learns on the target fold's fit band. ``prior_validation``
learns on earlier folds' validation rows whose settlement precedes the first validation quote date of the
target fold (purged), so a fold with no earlier evidence keeps the declared fallback and is flagged
``fitted`` false. ``rolling`` refits as outcomes settle: the target fold's validation rows are cut into
consecutive blocks of ``refit_days`` calendar days from the fold's first validation quote, and each block is
fitted on the validation rows of this and every earlier fold that settled strictly before the block starts
(so earlier blocks of the same fold teach it once they have settled); the fold's fit-band rows use the first
block's state. A block with too little to learn from keeps the fallback, ``fitted`` false. Any rule takes
``half_life_days``: a learning row is weighted ``0.5 ** ((reference - settlement) / half_life_days)``, the
reference being the block start for ``rolling`` and the target fold's first validation quote otherwise; every
kind uses the weights (``min_rows`` still counts rows, and quantile bucket edges stay unweighted).
Rows are read under an ``end_before`` bound pushed into the parquet scan, so no row at or
after it is ever decoded; rows with a null date are not read at all.

Two more kinds pool components with parameters found by a numerical optimizer (L-BFGS-B from the exact
linear-pool weights, deterministic) on a strided sample of the learning rows. ``beta_pool`` is the beta
transform of a linear pool, ``BetaCDF_{a,b}(sum_i w_i F_i)`` (Ranjan and Gneiting 2010), never worse on the
sample than the linear pool it starts from. ``quantile_average`` averages the components' quantile functions,
``sum_i w_i Q_i``, each obtained by inverting its grid CDF with the PIT tail convention, and reads the pooled
CDF back at the grid points. A component may name a ``path``: its columns are then read from another scored-rows
parquet (hash-verified, same ``end_before`` bound, unique ``keys``) and left-joined; an unmatched row is NaN.

The output is one new directory, built beside it and published by one rename: ``forecasts.parquet``,
``tables/<name>.csv`` plus ``tables/windows.csv``, ``fit.json`` and ``summary.json``.

numpy, pandas, pyarrow and lightgbm are named only inside methods, so importing this pack needs none of them.
Wiring is by import path; the pack registers nothing.
"""

import datetime
import hashlib
import itertools
import json
import math
import os
import re
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path

from dskit.pipeline.base import value_hash
from dskit.pipeline.libs.lightgbm import (
    ALLOWED_LGBM_KEYS,
    DEFAULT_LGBM_PARAMS,
    FORCED_LGBM_KEYS,
    LGBM_KEY_TYPES,
)
from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["GridCDFMethod", "METHOD_KINDS", "NODE_KINDS", "SCHEMA_VERSION"]

#: Deliberately EMPTY: a document names ``dskit.pipeline.libs.cdf_grid_methods:GridCDFMethod``.
NODE_KINDS = ()

#: The output layout version, written into ``summary.json`` and ``fit.json``.
SCHEMA_VERSION = 1
#: The two band names the node reads; any other band value is carried and never used.
FIT_BAND, VAL_BAND = "fit", "val"
#: The column roles a document's ``columns`` mapping must name, exactly.
COLUMN_ROLES = ("identity", "date", "settlement", "fold", "band", "outcome", "dte", "group", "eligible")
#: Table metrics. ``twcrps`` weights by ``score_weights``; ``crps13`` is the same trapezoid with weights one.
SCORE_METRICS = ("twcrps", "crps13")
REGION_METRIC = "region_brier"
#: The metric of a declared group of regions (a strike zone): the sum of its regions' Brier scores per row.
REGION_GROUP_METRIC = "region_group_brier"
#: The reserved table name of the always-written window table.
WINDOWS_TABLE = "windows"
#: Derived category columns a table or a method may name besides parquet columns.
PERIOD_COLUMN, YEAR_COLUMN = "period", "year"
#: The component name the new forecaster takes in the tables; no declared component may use it.
METHOD_NAME = "method"
#: Pool sizes above this are refused: every support of the simplex is solved exactly (2^K - 1 of them).
MAX_POOL_COMPONENTS = 6
#: The base probability is clipped to this before the logit.
LOGIT_CLIP = 1e-4
#: Rows scored or predicted per call, bounding the working arrays.
CHUNK_ROWS = 50_000
#: A row-support candidate whose smallest weight is below this (numerical noise) is clipped to zero.
SIMPLEX_NOISE = 1e-12
#: Parametric pools fit on at most this many learning rows per cell unless the document sets ``max_learning_rows``.
DEFAULT_MAX_LEARNING_ROWS = 20_000
#: Interior quantile levels of ``quantile_average`` unless the document sets ``levels``, and the fewest allowed.
DEFAULT_LEVELS, MIN_LEVELS = 199, 3
#: Rows of a quantile average inverted and located per call, bounding the working arrays.
QUANTILE_CHUNK = 10_000
#: Optimizer settings of the parametric pools: iteration cap, softmax logit bound and floor of a zero weight.
MAX_ITERATIONS, LOGIT_BOUND, WEIGHT_FLOOR = 100, 30.0, 1e-4
#: The beta pool's ``log a`` and ``log b`` stay within this, the probability clip of its density, and the
#: relative step of its forward difference in the shapes.
BETA_LOG_BOUND, PROB_CLIP, SHAPE_STEP = 3.0, 1e-9, 1e-6
_TABLE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _jsonable(value):
    """Return ``value`` with numpy scalars and arrays as Python ones and non-finite floats as None."""
    import numpy as np

    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _is_name(value):
    """Say whether ``value`` is a non-empty string."""
    return isinstance(value, str) and bool(value)


def _is_int(value):
    """Say whether ``value`` is an int and not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


def _as_list(value):
    """Return ``value`` as a list: None as empty, a string as one entry."""
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _rearranged(cdf):
    """Return each row of ``cdf`` clipped to [0, 1] and sorted ascending (monotone rearrangement)."""
    import numpy as np

    return np.sort(np.clip(cdf, 0.0, 1.0), axis=1)


# ---------------------------------------------------------------------------------------------------- dates


class _Dates:
    """Calendar helpers: ISO text, epoch day numbers and parquet filter scalars."""

    EPOCH = datetime.date(1970, 1, 1)

    @staticmethod
    def parse(text):
        """Return the date of an ISO ``YYYY-MM-DD`` text, None when it is not one."""
        if not (isinstance(text, str) and len(text) == 10):
            return None
        try:
            return datetime.date.fromisoformat(text)
        except ValueError:
            return None

    @classmethod
    def epoch_days(cls, text):
        """Return the day number (days since 1970-01-01) of an ISO date text."""
        return (cls.parse(text) - cls.EPOCH).days

    @classmethod
    def iso(cls, day):
        """Return the ISO text of a day number (days since 1970-01-01)."""
        return (cls.EPOCH + datetime.timedelta(days=int(day))).isoformat()

    @staticmethod
    def day_numbers(series):
        """Return a date-like series as int64 day numbers, refusing a null."""
        import pandas as pd

        parsed = pd.to_datetime(series)
        if parsed.isna().any():
            raise ValueError(f"column {series.name!r} holds a null date")
        if parsed.dt.tz is not None:
            parsed = parsed.dt.tz_localize(None)
        return parsed.to_numpy().astype("datetime64[D]").astype("int64")

    @classmethod
    def scalar(cls, arrow_type, text):
        """Return the parquet filter scalar of an ISO date in the column's own Arrow type."""
        import pyarrow as pa

        if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
            return pa.scalar(text, type=arrow_type)
        day = cls.parse(text)
        if pa.types.is_date(arrow_type):
            return pa.scalar(day, type=arrow_type)
        if pa.types.is_timestamp(arrow_type):
            return pa.scalar(datetime.datetime(day.year, day.month, day.day), type=arrow_type)
        raise ValueError(f"a date column of Arrow type {arrow_type} cannot be bounded by end_before")


# ------------------------------------------------------------------------------------------------- reading


class _Source:
    """Hash-verified, date-bounded parquet reads."""

    @staticmethod
    def verify(path, expected, what):
        """Raise ValueError unless the sha256 of the file at ``path`` is ``expected``."""
        from dskit.pipeline.workflow import path_hash

        if not os.path.isfile(path):
            raise FileNotFoundError(f"no file to hash at {path}")
        actual = path_hash(str(path))
        if actual != expected:
            raise ValueError(f"{what} {path} has sha256 {actual}, the document declares {expected}")

    @staticmethod
    def read(path, columns, date_columns, end_before):
        """Return the Arrow table of ``columns`` with every ``date_columns`` value before ``end_before``.

        The bound is a parquet filter, so rows at or after it are never decoded; the result is checked
        again after the read.
        """
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

        schema = pq.read_schema(path)
        missing = [c for c in columns if c not in schema.names]
        if missing:
            raise ValueError(f"{path} lacks the columns {missing}")
        bound = None
        for name in date_columns:
            term = pc.field(name) < _Dates.scalar(schema.field(name).type, end_before)
            bound = term if bound is None else bound & term
        table = pq.read_table(path, columns=list(columns), filters=bound)
        limit = _Dates.epoch_days(end_before)
        for name in date_columns:
            days = _Dates.day_numbers(table[name].to_pandas(date_as_object=False))
            if days.size and days.max() >= limit:
                raise ValueError(f"{path}: a {name} value is at or after end_before {end_before}")
        return table


# -------------------------------------------------------------------------------------------------- grid


class _Grid:
    """The declared z grid: scores, regions and PITs of CDFs given on it."""

    def __init__(self, z, labels, weights, tail):
        import numpy as np

        self.z = np.asarray(z, dtype=float)
        self.labels = list(labels)
        self.points = len(self.z)
        self.weights = np.asarray(weights, dtype=float)
        self.plain = np.ones(self.points)
        self.tail = float(tail)
        gaps = np.diff(self.z)
        self.trapezoid = np.zeros(self.points)
        self.trapezoid[:-1] += gaps / 2
        self.trapezoid[1:] += gaps / 2
        self.regions = self.points + 1

    def indicator(self, y):
        """Return ``1{y <= z_k}`` as a float ``(rows, points)`` array."""
        import numpy as np

        return (np.asarray(y, dtype=float)[:, None] <= self.z[None, :]).astype(float)

    def score(self, cdf, y, weights=None):
        """Return the grid threshold-weighted CRPS of every row, NaN where ``y`` or the CDF is not finite.

        Parameters
        ----------
        cdf : numpy.ndarray
            ``(rows, points)`` CDF values on the grid.
        y : numpy.ndarray
            One outcome per row, in z units.
        weights : numpy.ndarray, optional
            Threshold weights; the declared score weights when omitted.

        Returns
        -------
        numpy.ndarray
            The trapezoid over the grid of ``w_k * (F_k - 1{y <= z_k})^2``, per row.
        """
        import numpy as np

        y = np.asarray(y, dtype=float)
        coef = self.trapezoid * (self.weights if weights is None else np.asarray(weights, dtype=float))
        out = (np.asarray(cdf, dtype=float) - self.indicator(y)) ** 2 @ coef
        out[~np.isfinite(y)] = np.nan
        return out

    def quadratic_weights(self):
        """Return the per-threshold weight of the squared residual in the declared score."""
        return self.trapezoid * self.weights

    def pit(self, cdf, y):
        """Return the PIT of ``y`` under each row's grid CDF.

        Linear between grid points, and linear in the tails from 0 at ``z_first - tail`` and to 1 at
        ``z_last + tail``; beyond those the value is 0 or 1.
        """
        import numpy as np

        n, k = cdf.shape
        x = np.concatenate([[self.z[0] - self.tail], self.z, [self.z[-1] + self.tail]])
        full = np.concatenate([np.zeros((n, 1)), cdf, np.ones((n, 1))], axis=1)
        y = np.asarray(y, dtype=float)
        seg = np.clip(np.searchsorted(x, y, side="right") - 1, 0, k)
        t = np.clip((y - x[seg]) / (x[seg + 1] - x[seg]), 0.0, 1.0)
        lo = np.take_along_axis(full, seg[:, None], axis=1)[:, 0]
        hi = np.take_along_axis(full, (seg + 1)[:, None], axis=1)[:, 0]
        return lo + t * (hi - lo)

    def region_masses(self, cdf):
        """Return the ``points + 1`` region masses: ``F(z_0)``, the differences, ``1 - F(z_last)``."""
        import numpy as np

        return np.concatenate([cdf[:, :1], np.diff(cdf, axis=1), 1.0 - cdf[:, -1:]], axis=1)

    def region_index(self, y):
        """Return the region of each outcome: region ``r`` is ``(z_{r-1}, z_r]``, the ends open."""
        import numpy as np

        return np.searchsorted(self.z, np.asarray(y, dtype=float), side="left")

    def region_brier(self, cdf, y):
        """Return the ``(rows, regions)`` squared error of the masses against the realized region."""
        import numpy as np

        y = np.asarray(y, dtype=float)
        hit = np.zeros((len(y), self.regions))
        hit[np.arange(len(y)), self.region_index(y)] = 1.0
        out = (self.region_masses(cdf) - hit) ** 2
        out[~np.isfinite(y)] = np.nan
        return out


# -------------------------------------------------------------------------------------------- group helpers


class _Groups:
    """Rows grouped by a non-negative integer key; a negative key is left out."""

    def __init__(self, keys):
        import numpy as np

        keys = np.asarray(keys)
        kept = np.flatnonzero(keys >= 0)
        self.order = kept[np.argsort(keys[kept], kind="stable")]
        sorted_keys = keys[self.order]
        self.starts = (np.r_[0, np.flatnonzero(np.diff(sorted_keys)) + 1] if self.order.size
                       else np.zeros(0, dtype=int))
        self.keys = sorted_keys[self.starts]
        self.counts = np.diff(np.r_[self.starts, self.order.size])

    def sums(self, values):
        """Return the per-group column sums of ``values`` (``(rows, D)``), one row per group."""
        import numpy as np

        if not self.order.size:
            return np.zeros((0, values.shape[1]))
        return np.add.reduceat(values[self.order], self.starts, axis=0)

    def members(self):
        """Return the row indices of each group, in key order."""
        ends = self.starts + self.counts
        return [self.order[a:b] for a, b in zip(self.starts, ends)]


class _SimplexQuadratic:
    """The minimizer of ``w'Qw`` over the probability simplex, exact for a small ``Q``."""

    @staticmethod
    def solve(q):
        """Return the weights on the simplex minimizing ``w'Qw`` for a PSD matrix ``q``.

        Every support is solved on its own (the stationary point of the equality-constrained problem) and
        the best feasible candidate taken, so the answer is the global minimum, with no iteration.
        """
        import numpy as np

        q = np.asarray(q, dtype=float)
        k = len(q)
        best_value, best = np.inf, None
        for size in range(1, k + 1):
            for support in itertools.combinations(range(k), size):
                w = _SimplexQuadratic._candidate(q, list(support))
                if w is None:
                    continue
                value = w @ q @ w
                if best is None or value < best_value - 1e-15 * abs(best_value):
                    best_value, best = value, w
        return best

    @staticmethod
    def _candidate(q, support):
        """Return the feasible stationary weights on ``support``, None when there are none."""
        import numpy as np

        w = np.zeros(len(q))
        if len(support) == 1:
            w[support[0]] = 1.0
            return w
        block = q[np.ix_(support, support)]
        x = np.linalg.lstsq(block, np.ones(len(support)), rcond=None)[0]
        total = x.sum()
        if not np.isfinite(total) or abs(total) < 1e-300:
            return None
        x = x / total
        if x.min() < -SIMPLEX_NOISE:
            return None
        x = np.clip(x, 0.0, None)
        w[support] = x / x.sum()
        return w


# ------------------------------------------------------------------------------------ derived categories


class _EdgeBuckets:
    """A numeric column cut into labelled ``[lo, hi]`` intervals; a value outside every one is missing."""

    def __init__(self, column, edges):
        self.column = column
        self.edges = edges

    def categorize(self, panel):
        """Return ``(codes, labels)``, code -1 where the value lies in no interval."""
        import numpy as np

        values = panel.numeric(self.column)
        codes = np.full(len(values), -1, dtype=np.int64)
        for index, (lo, hi) in enumerate(self.edges):
            codes[(values >= lo) & (values <= hi)] = index
        return codes, [f"{lo}-{hi}" for lo, hi in self.edges]


class _DatePeriods:
    """Named inclusive ISO date ranges over the quote date; a date in no range is missing."""

    def __init__(self, periods):
        self.periods = periods

    def categorize(self, panel):
        """Return ``(codes, labels)`` of the declared periods."""
        import numpy as np

        codes = np.full(len(panel.date_days), -1, dtype=np.int64)
        for index, (start, end) in enumerate(self.periods.values()):
            lo, hi = _Dates.epoch_days(start), _Dates.epoch_days(end)
            codes[(panel.date_days >= lo) & (panel.date_days <= hi)] = index
        return codes, list(self.periods)


class _CalendarYears:
    """The calendar year of the quote date."""

    def categorize(self, panel):
        """Return ``(codes, labels)`` with one label per year present."""
        import numpy as np

        years = panel.date_days.astype("datetime64[D]").astype("datetime64[Y]").astype(int) + 1970
        uniq, codes = np.unique(years, return_inverse=True)
        return codes.astype(np.int64), [str(y) for y in uniq]


# ---------------------------------------------------------------------------------------------- the panel


class _Panel:
    """The scored rows as numpy arrays, with the component CDFs, derived categories and extra features."""

    def __init__(self, frame, params, grid):
        import numpy as np
        import pandas as pd

        roles, buckets, periods = params["columns"], params.get("buckets", {}), params.get("periods", {})
        self.frame = frame
        self.roles = roles
        self.grid = grid
        self.components = {spec["name"]: spec for spec in params["components"]}
        self.rows = len(frame)
        for role in ("fold", "band"):
            if frame[roles[role]].isna().any():
                raise ValueError(f"column {roles[role]!r} holds a null")
        fold = frame[roles["fold"]].to_numpy()
        if fold.dtype.kind not in "iu" and not np.all(fold == np.floor(fold)):
            raise ValueError(f"column {roles['fold']!r} must hold whole numbers")
        self.fold = fold.astype(np.int64)
        self.band = frame[roles["band"]].astype(str).to_numpy()
        self.is_fit = self.band == FIT_BAND
        self.is_val = self.band == VAL_BAND
        self.y = pd.to_numeric(frame[roles["outcome"]]).to_numpy(dtype=float)
        self.date_days = _Dates.day_numbers(frame[roles["date"]])
        self.settle_days = _Dates.day_numbers(frame[roles["settlement"]])
        self.eligible = frame[roles["eligible"]].fillna(False).astype(bool).to_numpy()
        self.cdf = {}
        self.extra = {}
        self.external_pit = {}
        self._finite = {}
        self._categories = {}
        self._derived = {name: _EdgeBuckets(spec["column"], spec["edges"]) for name, spec in buckets.items()}
        self._derived[YEAR_COLUMN] = _CalendarYears()
        if periods:
            self._derived[PERIOD_COLUMN] = _DatePeriods(periods)

    def numeric(self, column):
        """Return a parquet column as float64."""
        import pandas as pd

        return pd.to_numeric(self.frame[column]).to_numpy(dtype=float)

    def category(self, name):
        """Return ``(codes, labels)`` of a derived or parquet column, code -1 where missing."""
        import pandas as pd

        if name not in self._categories:
            if name in self._derived:
                self._categories[name] = self._derived[name].categorize(self)
            else:
                codes, uniques = pd.factorize(self.frame[name], sort=True)
                self._categories[name] = (codes.astype("int64"), [str(u) for u in uniques])
        return self._categories[name]

    def finite(self, name):
        """Return the rows whose whole CDF of component ``name`` is finite."""
        import numpy as np

        if name not in self._finite:
            self._finite[name] = np.isfinite(self.cdf[name]).all(axis=1)
        return self._finite[name]

    def ready(self, names):
        """Return the rows whose CDF is finite for every component in ``names``."""
        import numpy as np

        ok = np.ones(self.rows, dtype=bool)
        for name in names:
            ok = ok & self.finite(name)
        return ok

    def usable(self, names):
        """Return the rows that may teach: ready for ``names``, eligible, with a finite outcome."""
        import numpy as np

        return self.ready(names) & self.eligible & np.isfinite(self.y)

    def declared_pit(self, name):
        """Return the full-length PIT column component ``name`` declares (an external file's included), else None."""
        if name in self.external_pit:
            return self.external_pit[name]
        column = self.components[name].get("pit_column")
        return self.numeric(column) if column else None

    def pit(self, name, rows):
        """Return the PIT of the outcome under component ``name`` at ``rows``.

        A raw component with a declared ``pit_column`` reads it; any other is read off its grid CDF.
        """
        declared = None if "pit_map" in self.components[name] else self.declared_pit(name)
        if declared is not None:
            return declared[rows]
        return self.grid.pit(self.cdf[name][rows], self.y[rows])


# ----------------------------------------------------------------------------------------- learning rows


class _Block:
    """One refit unit of a target fold: the rows it forecasts, the rows that may teach it, its reference day."""

    def __init__(self, target, learn, reference, tag):
        self.target = target
        self.learn = learn
        self.reference = reference
        self.tag = tag


class _LearningRule(ABC):
    """Which rows of the panel may teach a method for one target fold, and in how many refit blocks."""

    #: The extra method keys this rule reads.
    KEYS = ()
    #: Whether ``fit.json`` lists the fold's blocks; a rule with one block per fold has nothing to list.
    LISTS_BLOCKS = False

    def problems(self, spec):
        """Return the problems with the rule's own keys in a method ``spec``; the default has none."""
        return []

    @abstractmethod
    def blocks(self, panel, fold, spec):
        """Return the fold's :class:`_Block` list, first block first (the fit band uses the first)."""

    @staticmethod
    def first_validation_day(panel, fold):
        """Return the day of the fold's first validation quote (its first quote of any band if none)."""
        target = panel.is_val & (panel.fold == fold)
        return int(panel.date_days[target if target.any() else panel.fold == fold].min())


class _SingleBlockRule(_LearningRule):
    """A rule that fits once per fold, from the rows its :meth:`mask` allows."""

    @abstractmethod
    def mask(self, panel, fold):
        """Return the boolean mask of rows allowed to teach the target ``fold``."""

    def blocks(self, panel, fold, spec):
        """Return the one block: every row of the fold, referenced at its first validation quote."""
        import numpy as np

        return [_Block(np.flatnonzero(panel.fold == fold), self.mask(panel, fold),
                       self.first_validation_day(panel, fold), f"fold_{fold}")]


class _FitBandRule(_SingleBlockRule):
    """The target fold's own fit band."""

    def mask(self, panel, fold):
        """Return band ``fit`` rows of ``fold``."""
        return panel.is_fit & (panel.fold == fold)


class _PriorValidationRule(_SingleBlockRule):
    """Earlier folds' validation rows that settled before the target fold's first validation quote."""

    def mask(self, panel, fold):
        """Return band ``val`` rows of earlier folds with settlement before the fold's first val quote."""
        import numpy as np

        target = panel.is_val & (panel.fold == fold)
        if not target.any():
            return np.zeros(panel.rows, dtype=bool)
        first = panel.date_days[target].min()
        return panel.is_val & (panel.fold < fold) & (panel.settle_days < first)


class _RollingRule(_LearningRule):
    """Refit every ``refit_days`` calendar days of the target fold's validation quotes.

    The fold's validation rows are cut into consecutive blocks of ``refit_days`` days from the fold's first
    validation quote. A block is taught by the validation rows of this and every earlier fold whose
    settlement falls strictly before the block's start, so earlier blocks of the same fold teach it once
    they have settled. Rows of the fold that are not validation rows (its fit band) take the first block.
    """

    KEYS = ("refit_days",)
    LISTS_BLOCKS = True

    def problems(self, spec):
        """Require ``refit_days`` to be an int of at least one."""
        problems = []
        check_int_param(problems, "method.refit_days", spec.get("refit_days"), ge=1)
        return problems

    def blocks(self, panel, fold, spec):
        """Return one block per non-empty ``refit_days`` window of the fold's validation quotes."""
        import numpy as np

        every = int(spec["refit_days"])
        target = np.flatnonzero(panel.fold == fold)
        anchor = np.flatnonzero(panel.is_val & (panel.fold == fold))
        if not anchor.size:
            anchor = target
        quote = panel.date_days[anchor]
        first = int(quote.min())
        window = (quote - first) // every
        taught = panel.is_val & (panel.fold <= fold)
        rest = np.setdiff1d(target, anchor)
        out = []
        for index in np.unique(window):
            start = first + int(index) * every
            rows = anchor[window == index]
            if not out:
                rows = np.union1d(rows, rest)
            out.append(_Block(rows, taught & (panel.settle_days < start), start, f"fold_{fold}_block_{len(out)}"))
        return out


LEARNING_RULES = {"fit_band": _FitBandRule(), "prior_validation": _PriorValidationRule(),
                  "rolling": _RollingRule()}


# ------------------------------------------------------------------------------------------ cell bucketing


class _Dimension(ABC):
    """One coordinate of a bucket: how rows map to integer codes, and what the codes are called."""

    def __init__(self, column):
        self.column = column

    @property
    def name(self):
        """The dimension's name in a cell description."""
        return self.column

    @abstractmethod
    def fit(self, panel, rows):
        """Return the learned state (plain JSON) from the learning ``rows``."""

    @abstractmethod
    def codes(self, panel, rows, state):
        """Return the int64 code of each of ``rows`` (-1 where missing) under the learned ``state``."""

    @abstractmethod
    def labels(self, panel, state):
        """Return the label of every code, in code order."""

    @abstractmethod
    def describe(self, state):
        """Return the JSON description of the binning."""


class _CategoryDimension(_Dimension):
    """The values of a derived or parquet column."""

    def fit(self, panel, rows):
        """Return an empty state: the categories are the column's own."""
        return {}

    def codes(self, panel, rows, state):
        """Return the column's category codes."""
        return panel.category(self.column)[0][rows]

    def labels(self, panel, state):
        """Return the column's category labels."""
        return panel.category(self.column)[1]

    def describe(self, state):
        """Return ``{"by": column}``."""
        return {"by": self.column}


class _QuantileDimension(_Dimension):
    """Buckets cut at quantiles of a numeric column, learned on the learning rows."""

    def __init__(self, column, quantiles):
        super().__init__(column)
        self.quantiles = list(quantiles)

    @property
    def name(self):
        """The dimension's name: the column and the word quantile."""
        return f"{self.column}_quantile"

    def fit(self, panel, rows):
        """Return the learned edges."""
        import numpy as np

        values = panel.numeric(self.column)[rows]
        values = values[np.isfinite(values)]
        return {"edges": np.quantile(values, self.quantiles).tolist() if values.size else []}

    def codes(self, panel, rows, state):
        """Return the bucket of each row, -1 where the value is not finite."""
        import numpy as np

        values = panel.numeric(self.column)[rows]
        codes = np.searchsorted(np.asarray(state["edges"], dtype=float), values, side="right").astype(np.int64)
        codes[~np.isfinite(values) | (len(state["edges"]) == 0)] = -1
        return codes

    def labels(self, panel, state):
        """Return ``q0 .. q<n>``."""
        return [f"q{i}" for i in range(len(self.quantiles) + 1)]

    def describe(self, state):
        """Return the column, the quantiles and the learned edges."""
        return {"column": self.column, "quantiles": self.quantiles, "edges": state["edges"]}


class _Cells:
    """A cell is one combination of dimension codes; its key is the mixed-radix number of the codes."""

    def __init__(self, dimensions):
        self.dimensions = dimensions

    @classmethod
    def from_spec(cls, spec):
        """Build the dimensions a method spec declares through ``by`` and ``quantile_by``."""
        dims = [_CategoryDimension(column) for column in _as_list(spec.get("by"))]
        if spec.get("quantile_by"):
            dims.append(_QuantileDimension(spec["quantile_by"]["column"], spec["quantile_by"]["quantiles"]))
        return cls(dims)

    def columns(self):
        """Return the parquet columns the dimensions read, derived ones included."""
        return [d.column for d in self.dimensions]

    def fit(self, panel, rows):
        """Return the learned state of every dimension."""
        return [d.fit(panel, rows) for d in self.dimensions]

    def keys(self, panel, rows, states):
        """Return each row's cell key, -1 where any coordinate is missing (or there are no dimensions)."""
        import numpy as np

        key = np.zeros(len(rows), dtype=np.int64)
        bad = np.zeros(len(rows), dtype=bool) | (not self.dimensions)
        mult = 1
        for dim, state in zip(self.dimensions, states):
            code = dim.codes(panel, rows, state)
            bad |= code < 0
            key += np.where(code < 0, 0, code) * mult
            mult *= max(len(dim.labels(panel, state)), 1)
        key[bad] = -1
        return key

    def decode(self, panel, key, states):
        """Return ``{dimension name: label}`` of one cell key."""
        out, mult = {}, 1
        for dim, state in zip(self.dimensions, states):
            labels = dim.labels(panel, state)
            out[dim.name] = labels[(int(key) // mult) % len(labels)]
            mult *= len(labels)
        return out

    def key_of(self, panel, described, states):
        """Return the key of a cell described as ``{dimension name: label}``."""
        key, mult = 0, 1
        for dim, state in zip(self.dimensions, states):
            labels = dim.labels(panel, state)
            key += labels.index(described[dim.name]) * mult
            mult *= len(labels)
        return key

    def name_of(self, panel, key, states):
        """Return the cell's label for a JSON key: the dimension labels joined by ``|``."""
        return "|".join(self.decode(panel, key, states).values())


# ------------------------------------------------------------------------------------------------ methods


class _Fitted:
    """What one fold's fit leaves: JSON state, the in-memory model and any text artifacts."""

    def __init__(self, state, model, artifacts=None):
        self.state = state
        self.model = model
        self.artifacts = artifacts or {}


class _Method(ABC):
    """One way of building a forecaster on the grid; subclasses fill the hooks, the driver never branches.

    A subclass declares ``KIND``, its extra spec keys in ``_KEYS`` (those in ``_REQUIRED`` must be given,
    the others take ``_DEFAULTS``) and implements ``_extra_problems``, ``used``, ``fit`` and ``predict``.
    ``fit`` receives the learning ``weights`` (None for equal weights, which every kind must leave on its
    unweighted path) and the ``tag`` that names the block's artifacts.
    """

    KIND = ""
    _KEYS = ()
    _REQUIRED = ()
    _DEFAULTS = {}

    def __init__(self, spec, params, grid):
        self.spec = {**self._DEFAULTS, **spec}
        self.params = params
        self.grid = grid

    @classmethod
    def problems(cls, spec, params, names):
        """Return the problems with a method spec: the shared keys, then the kind's own."""
        problems = []
        learn = spec.get("learn")
        rule = LEARNING_RULES.get(learn) if isinstance(learn, str) else None
        reject_unknown_params(problems, spec, ("kind", "learn", "half_life_days", *(rule.KEYS if rule else ()),
                                               *cls._KEYS))
        missing = sorted(set(cls._REQUIRED) - set(spec))
        if missing:
            problems.append(f"method needs {missing}")
        if rule is None:
            problems.append(f"method.learn must be one of {sorted(LEARNING_RULES)}, got {learn!r}")
        else:
            problems += rule.problems(spec)
        if "half_life_days" in spec and not (number_ok(spec["half_life_days"]) and spec["half_life_days"] > 0):
            problems.append(f"method.half_life_days must be a number above zero, got {spec['half_life_days']!r}")
        return problems + cls._extra_problems({**cls._DEFAULTS, **spec}, params, names)

    @classmethod
    @abstractmethod
    def _extra_problems(cls, spec, params, names):
        """Return the kind's own problems with the (default-completed) spec."""

    @staticmethod
    def _name_problems(problems, label, value, names):
        """Append a problem unless ``value`` names a declared component."""
        if value not in names:
            problems.append(f"method.{label} names {value!r}, not a declared component {sorted(names)}")

    @staticmethod
    def _cell_problems(problems, spec, single):
        """Append the problems of the ``by`` and ``quantile_by`` keys."""
        by, quantile = spec.get("by"), spec.get("quantile_by")
        if single and by is not None and not _is_name(by):
            problems.append("method.by must be one column name")
        if not single and not (isinstance(by, list) and by and all(_is_name(c) for c in by)):
            problems.append("method.by must be a non-empty list of column names")
        if quantile is not None:
            ok = (isinstance(quantile, dict) and set(quantile) == {"column", "quantiles"}
                  and _is_name(quantile["column"]) and isinstance(quantile["quantiles"], list)
                  and quantile["quantiles"] and all(number_ok(q) and 0 < q < 1 for q in quantile["quantiles"])
                  and all(a < b for a, b in zip(quantile["quantiles"], quantile["quantiles"][1:])))
            if not ok:
                problems.append("method.quantile_by must be {column, quantiles: ascending numbers in (0, 1)}")
            if by is not None:
                problems.append("method.by and method.quantile_by are exclusive")

    @staticmethod
    def _rows_problem(problems, spec):
        """Append a problem unless ``min_rows`` is a positive int."""
        check_int_param(problems, "method.min_rows", spec.get("min_rows"), ge=1)

    def needed_columns(self):
        """Return the parquet columns the method reads beyond the declared roles and component CDFs."""
        return []

    def prepare(self, panel):
        """Load anything the method needs from outside the scored rows; the default needs nothing."""

    @abstractmethod
    def used(self):
        """Return the component names a row must have finite CDFs for."""

    @abstractmethod
    def fit(self, panel, rows, fold, weights=None, tag=None):
        """Learn from the learning ``rows`` (weighted by ``weights``) for target ``fold``; return a :class:`_Fitted` or None."""

    @abstractmethod
    def predict(self, panel, rows, fitted):
        """Return the ``(len(rows), points)`` CDF of the rows under a fold's :class:`_Fitted`."""


class _PitMap:
    """The empirical-CDF recalibration map: G(p) is the fraction of learned PITs at or below p."""

    def __init__(self, knots):
        import numpy as np

        self.knots = int(knots)
        self.p = np.linspace(0.0, 1.0, self.knots)

    def fit(self, pit, weights=None):
        """Return G at the knots (side right) from the learned PIT values, weighted when ``weights`` is given."""
        import numpy as np

        if weights is None:
            return np.searchsorted(np.sort(pit), self.p, side="right") / len(pit)
        order = np.argsort(pit, kind="stable")
        mass = np.concatenate([[0.0], np.cumsum(weights[order])])
        return mass[np.searchsorted(pit[order], self.p, side="right")] / mass[-1]

    def apply(self, cdf, g):
        """Return ``interp(F, p, G)`` elementwise."""
        import numpy as np

        return np.interp(cdf.ravel(), self.p, g).reshape(cdf.shape)


class _PitMapMethod(_Method):
    """Recalibrate one component by the empirical CDF of its PIT, per bucket when asked."""

    KIND = "pit_map"
    _KEYS = ("component", "knots", "by", "min_rows")
    _REQUIRED = ("component", "knots", "min_rows")

    @classmethod
    def _extra_problems(cls, spec, params, names):
        problems = []
        cls._name_problems(problems, "component", spec.get("component"), names)
        check_int_param(problems, "method.knots", spec.get("knots"), ge=2)
        cls._rows_problem(problems, spec)
        cls._cell_problems(problems, spec, single=True)
        return problems

    def __init__(self, spec, params, grid):
        super().__init__(spec, params, grid)
        self.cells = _Cells.from_spec(self.spec)
        self.map = _PitMap(self.spec["knots"])

    def needed_columns(self):
        """Return the bucket column."""
        return self.cells.columns()

    def used(self):
        """Return the recalibrated component."""
        return [self.spec["component"]]

    def fit(self, panel, rows, fold, weights=None, tag=None):
        """Learn the global map and one map per bucket holding at least ``min_rows`` rows."""
        pit = panel.pit(self.spec["component"], rows)
        states = self.cells.fit(panel, rows)
        keys = self.cells.keys(panel, rows, states)
        groups = _Groups(keys)
        buckets = {int(k): self.map.fit(pit[m], None if weights is None else weights[m])
                   for k, m in zip(groups.keys, groups.members()) if len(m) >= self.spec["min_rows"]}
        sizes = {int(k): len(m) for k, m in zip(groups.keys, groups.members())}
        glob = self.map.fit(pit, weights)
        state = {"component": self.spec["component"], "knots": self.map.knots,
                 "bucketing": [d.describe(s) for d, s in zip(self.cells.dimensions, states)],
                 "global": {"rows": len(rows), "g": glob},
                 "buckets": {self.cells.name_of(panel, k, states): {"rows": sizes[k], "g": g}
                             for k, g in buckets.items()}}
        return _Fitted(state, {"global": glob, "buckets": buckets, "states": states})

    def predict(self, panel, rows, fitted):
        """Apply the bucket's map, the global map where the bucket has none."""
        cdf = panel.cdf[self.spec["component"]][rows]
        out = self.map.apply(cdf, fitted.model["global"])
        if not fitted.model["buckets"]:
            return out
        keys = self.cells.keys(panel, rows, fitted.model["states"])
        for key, g in fitted.model["buckets"].items():
            hit = keys == key
            out[hit] = self.map.apply(cdf[hit], g)
        return out


# --------------------------------------------------------------------------------- parametric pool numerics


class _SoftmaxWeights:
    """Weights on the probability simplex parametrized by unconstrained logits."""

    @staticmethod
    def of(logits):
        """Return the softmax of ``logits``."""
        import numpy as np

        e = np.exp(logits - logits.max())
        return e / e.sum()

    @staticmethod
    def logits(weights):
        """Return logits whose softmax is ``weights``, with zeros lifted to :data:`WEIGHT_FLOOR`."""
        import numpy as np

        return np.log(np.maximum(weights, WEIGHT_FLOOR))

    @staticmethod
    def backprop(weights, grad):
        """Return the gradient with respect to the logits from the gradient with respect to the weights."""
        return weights * (grad - weights @ grad)


class _QuantileGrid:
    """Quantile functions of grid CDFs at equally spaced levels, and the CDFs of quantile functions.

    A grid CDF is the piecewise-linear curve through ``(z_k, F_k)`` closed by the PIT tails: ``0`` at
    ``z_first - tail`` and ``1`` at ``z_last + tail``. Its quantile function is the left-continuous
    inverse ``inf {z : F(z) >= p}``, so a flat part inverts to its left end and the answer never depends on
    how ties are met. ``levels`` interior levels ``l / (levels + 1)`` get the knots ``p = 0`` and ``p = 1``
    at the two tail ends, ``levels + 2`` columns in all.
    """

    def __init__(self, grid, levels):
        import numpy as np

        self.z = grid.z
        self.levels = int(levels)
        self.inner = np.arange(1, self.levels + 1) / (self.levels + 1)
        self.p = np.concatenate([[0.0], self.inner, [1.0]])
        self.x = np.concatenate([[grid.z[0] - grid.tail], grid.z, [grid.z[-1] + grid.tail]])
        self.width = self.levels + 2

    def invert(self, cdf):
        """Return the ``(rows, levels + 2)`` quantiles of the grid CDFs in the rows of ``cdf``."""
        import numpy as np

        n, k = cdf.shape
        clean = np.maximum.accumulate(np.clip(cdf, 0.0, 1.0), axis=1)
        f = np.concatenate([np.zeros((n, 1)), clean, np.ones((n, 1))], axis=1)
        base = np.arange(n)[:, None]
        # Each row's knots lie in [0, 1]; a shift of two per row makes the whole array sorted, so one search
        # serves every row.
        position = np.searchsorted((f + 2.0 * base).ravel(), (self.inner[None, :] + 2.0 * base).ravel(),
                                   side="left").reshape(n, self.levels)
        local = np.clip(position - base * (k + 2), 1, k + 1)
        flat = f.ravel()
        upper, lower = flat[base * (k + 2) + local], flat[base * (k + 2) + local - 1]
        t = np.clip((self.inner[None, :] - lower) / np.maximum(upper - lower, 1e-300), 0.0, 1.0)
        inner = self.x[local - 1] + t * (self.x[local] - self.x[local - 1])
        return np.concatenate([np.full((n, 1), self.x[0]), inner, np.full((n, 1), self.x[-1])], axis=1)

    def locate(self, pooled):
        """Return ``(position, cdf)``: the CDF at every grid point of the quantile functions in ``pooled``.

        ``pooled`` is ``(rows, levels + 2)`` and non-decreasing along each row. ``position`` is the flat index
        into ``pooled`` of the last knot at or below each grid point, so the CDF there interpolates level
        ``p`` linearly between that knot and the next (the right-continuous CDF: ties take the larger level).
        """
        import numpy as np

        n = pooled.shape[0]
        span = self.x[-1] - self.x[0] + 1.0
        shift = np.arange(n)[:, None] * span
        position = np.searchsorted((pooled + shift).ravel(), (self.z[None, :] + shift).ravel(),
                                   side="right").reshape(n, len(self.z)) - 1
        flat = pooled.ravel()
        lower, upper = flat[position], flat[position + 1]
        local = position % self.width
        t = np.clip((self.z[None, :] - lower) / np.maximum(upper - lower, 1e-300), 0.0, 1.0)
        return position, self.p[local] + t * (self.p[local + 1] - self.p[local])


class _PoolObjective(ABC):
    """The weighted mean grid twCRPS of a pool with a few parameters, with its gradient.

    ``stack`` is ``(rows, components, points)``; ``row_weights`` the learning weights (None for equal). The
    parameters are a vector ``theta`` of softmax logits followed by the kind's own shape parameters; the
    kind says how a start, the bounds and the final description derive from it.
    """

    #: What the comparison optimum is called in the state: ``<name>_weights`` and ``<name>_objective``.
    BASELINE = ""

    def __init__(self, grid, stack, y, row_weights):
        import numpy as np

        self.components = stack.shape[1]
        self.points = grid.points
        omega = np.ones(len(y)) if row_weights is None else np.asarray(row_weights, dtype=float)
        self.rows = len(y)
        self.weight = ((omega / omega.sum())[:, None] * grid.quadratic_weights()[None, :]).ravel()
        self.indicator = grid.indicator(y).ravel()
        self._setup(stack)

    @abstractmethod
    def _setup(self, stack):
        """Keep whatever the objective needs from the ``(rows, components, points)`` ``stack``."""

    @abstractmethod
    def value_and_grad(self, theta):
        """Return ``(objective, gradient)`` at ``theta``."""

    @abstractmethod
    def start(self, weights):
        """Return the starting ``theta`` from the linear-pool ``weights``."""

    @abstractmethod
    def bounds(self):
        """Return the ``(low, high)`` bound of every entry of ``theta``."""

    @abstractmethod
    def unpack(self, theta):
        """Return the plain description (weights and shape parameters) of ``theta``."""

    @abstractmethod
    def baseline(self, weights):
        """Return ``(description, objective)`` of the comparison point the optimum may not be worse than."""

    def solve(self, weights):
        """Minimize the objective from the linear-pool ``weights`` and return the state dict.

        The optimizer is L-BFGS-B from a fixed start, so the answer is deterministic. The comparison point
        is kept when the optimizer does not beat it.
        """
        from scipy.optimize import minimize

        result = minimize(self.value_and_grad, self.start(weights), jac=True, method="L-BFGS-B",
                          bounds=self.bounds(), options={"maxiter": MAX_ITERATIONS})
        found, value = self.unpack(result.x), float(result.fun)
        base, base_value = self.baseline(weights)
        better = value < base_value
        return {**(found if better else base), "objective": value if better else base_value,
                f"{self.BASELINE}_weights": base["weights"], f"{self.BASELINE}_objective": base_value,
                "iterations": int(result.nit), "converged": bool(result.success)}


class _BetaPoolObjective(_PoolObjective):
    """``F = BetaCDF_{a,b}(sum_i w_i F_i)``: logits, ``log a``, ``log b``."""

    BASELINE = "linear_pool"

    def _setup(self, stack):
        import numpy as np

        self.stack = np.ascontiguousarray(np.clip(stack, 0.0, 1.0).transpose(1, 0, 2).reshape(self.components, -1))

    def loss(self, weights, a, b):
        """Return the objective at explicit weights and shapes."""
        from scipy import special

        pooled = (weights @ self.stack).clip(0.0, 1.0)
        return float(self.weight @ (special.betainc(a, b, pooled) - self.indicator) ** 2)

    def value_and_grad(self, theta):
        """Return the objective and its gradient (exact in the weights, a forward difference in the shapes)."""
        import numpy as np
        from scipy import special

        j = self.components
        w = _SoftmaxWeights.of(theta[:j])
        a, b = np.exp(theta[j:])
        pooled = (w @ self.stack).clip(0.0, 1.0)
        g = special.betainc(a, b, pooled)
        resid = g - self.indicator
        slope = 2.0 * self.weight * resid
        inside = np.clip(pooled, PROB_CLIP, 1.0 - PROB_CLIP)
        density = np.exp(special.xlogy(a - 1.0, inside) + special.xlog1py(b - 1.0, -inside) - special.betaln(a, b))
        grad_w = self.stack @ (slope * density)
        step_a, step_b = SHAPE_STEP * a, SHAPE_STEP * b
        grad_a = a * (slope @ ((special.betainc(a + step_a, b, pooled) - g) / step_a))
        grad_b = b * (slope @ ((special.betainc(a, b + step_b, pooled) - g) / step_b))
        return float(self.weight @ resid ** 2), np.concatenate([_SoftmaxWeights.backprop(w, grad_w), [grad_a, grad_b]])

    def start(self, weights):
        """Return the linear-pool logits with ``a = b = 1``."""
        import numpy as np

        return np.concatenate([_SoftmaxWeights.logits(weights), [0.0, 0.0]])

    def bounds(self):
        """Return wide logit bounds and ``log a``, ``log b`` within :data:`BETA_LOG_BOUND`."""
        return [(-LOGIT_BOUND, LOGIT_BOUND)] * self.components + [(-BETA_LOG_BOUND, BETA_LOG_BOUND)] * 2

    def unpack(self, theta):
        """Return the weights, ``a`` and ``b`` of ``theta``."""
        import numpy as np

        j = self.components
        return {"weights": _SoftmaxWeights.of(theta[:j]), "a": float(np.exp(theta[j])), "b": float(np.exp(theta[j + 1]))}

    def baseline(self, weights):
        """Return the exact linear pool (``a = b = 1``) and its objective."""
        return {"weights": weights, "a": 1.0, "b": 1.0}, self.loss(weights, 1.0, 1.0)


class _QuantileAverageObjective(_PoolObjective):
    """``Q = sum_i w_i Q_i`` at ``levels`` equally spaced probabilities, read back as a CDF on the grid."""

    BASELINE = "start"

    def __init__(self, grid, stack, y, row_weights, levels):
        self.inverse = _QuantileGrid(grid, levels)
        super().__init__(grid, stack, y, row_weights)

    def _setup(self, stack):
        import numpy as np

        self.quantiles = np.stack([self.inverse.invert(stack[:, j, :]) for j in range(self.components)])
        self.quantiles = self.quantiles.reshape(self.components, -1)

    def _evaluate(self, w):
        """Return ``(pooled quantiles, position, cdf)`` of the pool with weights ``w``."""
        pooled = (w @ self.quantiles).reshape(self.rows, self.inverse.width)
        position, cdf = self.inverse.locate(pooled)
        return pooled, position, cdf

    def loss(self, weights):
        """Return the objective at explicit weights."""
        return float(self.weight @ (self._evaluate(weights)[2].ravel() - self.indicator) ** 2)

    def value_and_grad(self, theta):
        """Return the objective and its gradient.

        With the CDF value ``F`` solving ``sum_i w_i Q_i(F) = z``, ``dF/dw_i = -Q_i(F) / Q'(F)``, where
        ``Q'`` is the slope of the pooled quantile function in the segment holding ``F``.
        """
        import numpy as np

        w = _SoftmaxWeights.of(theta)
        pooled, position, cdf = self._evaluate(w)
        resid = (cdf.ravel() - self.indicator)
        slope = (2.0 * self.weight * resid).reshape(cdf.shape)
        local = position % self.inverse.width
        p_low = self.inverse.p[local]
        step = self.inverse.p[local + 1] - p_low
        flat = pooled.ravel()
        gap = np.maximum(flat[position + 1] - flat[position], 1e-300)
        t = (cdf - p_low) / step
        lower, upper = self.quantiles[:, position], self.quantiles[:, position + 1]
        at_cdf = lower + t * (upper - lower)
        grad_w = -((at_cdf / (gap / step)) * slope).sum(axis=(1, 2))
        return float(self.weight @ resid ** 2), _SoftmaxWeights.backprop(w, grad_w)

    def start(self, weights):
        """Return the linear-pool logits."""
        return _SoftmaxWeights.logits(weights)

    def bounds(self):
        """Return wide logit bounds."""
        return [(-LOGIT_BOUND, LOGIT_BOUND)] * self.components

    def unpack(self, theta):
        """Return the weights of ``theta``."""
        return {"weights": _SoftmaxWeights.of(theta)}

    def baseline(self, weights):
        """Return the quantile average at the linear-pool weights and its objective."""
        return {"weights": weights}, self.loss(weights)


class _PoolBase(_Method):
    """A method that pools 2 to 6 named components; the shared stack, moments and exact linear-pool solve."""

    def __init__(self, spec, params, grid):
        super().__init__(spec, params, grid)
        self.cells = _Cells.from_spec(self.spec)

    @classmethod
    def _component_list_problems(cls, problems, spec, names):
        """Append the problems of ``components``: 2 to 6 distinct declared names."""
        parts = spec.get("components")
        if not (isinstance(parts, list) and 2 <= len(parts) <= MAX_POOL_COMPONENTS and len(set(map(str, parts))) == len(parts)):
            problems.append(f"method.components must be 2 to {MAX_POOL_COMPONENTS} distinct component names")
        else:
            for part in parts:
                cls._name_problems(problems, "components", part, names)

    def needed_columns(self):
        """Return the bucket column."""
        return self.cells.columns()

    def used(self):
        """Return the pooled components."""
        return list(self.spec["components"])

    def _stack(self, panel, rows):
        """Return the ``(rows, components, points)`` CDF tensor."""
        import numpy as np

        return np.stack([panel.cdf[name][rows] for name in self.spec["components"]], axis=1)

    def _row_moments(self, panel, rows):
        """Return each row's flattened quadratic-form contribution ``sum_k a_k e_jk e_lk``.

        With one weight vector the contribution sums over the grid with the score weights; per threshold it
        keeps the grid axis (the weight of a single point does not move its minimizer).
        """
        import numpy as np

        resid = self._stack(panel, rows) - self.grid.indicator(panel.y[rows])[:, None, :]
        if self.spec.get("per_threshold", False):
            return np.einsum("cjk,clk->ckjl", resid, resid).reshape(len(rows), -1)
        return np.einsum("cjk,clk,k->cjl", resid, resid, self.grid.quadratic_weights()).reshape(len(rows), -1)

    def _moments(self, panel, rows, keys, weights=None):
        """Return the summed moments of all rows and of each bucket key, with the row counts and masses.

        With learning ``weights`` each row's moment is scaled by its weight and a mass is the weight sum;
        without, a mass is the row count.
        """
        total, per_key, counts, masses = 0.0, {}, {}, {}
        for start in range(0, len(rows), CHUNK_ROWS):
            part = slice(start, start + CHUNK_ROWS)
            moments = self._row_moments(panel, rows[part])
            if weights is not None:
                moments = moments * weights[part][:, None]
            total = total + moments.sum(axis=0)
            groups = _Groups(keys[part])
            mass = groups.counts if weights is None else groups.sums(weights[part][:, None])[:, 0]
            for key, row_sum, n, m in zip(groups.keys, groups.sums(moments), groups.counts, mass):
                k = int(key)
                per_key[k] = per_key.get(k, 0.0) + row_sum
                counts[k] = counts.get(k, 0) + int(n)
                masses[k] = masses.get(k, 0) + (int(n) if weights is None else float(m))
        return total, per_key, counts, masses

    def _solve(self, moment, n):
        """Return the optimal weights (a vector, or one per threshold) from summed moments of mass ``n``."""
        import numpy as np

        j = len(self.spec["components"])
        if self.spec.get("per_threshold", False):
            q = (moment / n).reshape(self.grid.points, j, j)
            return np.array([_SimplexQuadratic.solve(qk) for qk in q])
        return _SimplexQuadratic.solve((moment / n).reshape(j, j))

    @staticmethod
    def _mass(rows, weights):
        """Return the learning mass of ``rows``: their count, or their weight sum."""
        return len(rows) if weights is None else float(weights.sum())

    def _exact_weights(self, panel, rows, weights):
        """Return the exact linear-pool weights on ``rows`` (one vector, whatever ``per_threshold`` says)."""
        import numpy as np

        total, _, _, _ = self._moments(panel, rows, np.full(len(rows), -1, dtype=np.int64), weights)
        return self._solve(total, self._mass(rows, weights))


class _LinearPoolMethod(_PoolBase):
    """Mix components with weights on the simplex that minimize the mean grid score."""

    KIND = "linear_pool"
    _KEYS = ("components", "by", "per_threshold", "shrink_rows", "quantile_by", "min_rows")
    _REQUIRED = ("components", "min_rows")
    _DEFAULTS = {"per_threshold": False}

    @classmethod
    def _extra_problems(cls, spec, params, names):
        problems = []
        cls._component_list_problems(problems, spec, names)
        cls._rows_problem(problems, spec)
        cls._cell_problems(problems, spec, single=True)
        if not isinstance(spec["per_threshold"], bool):
            problems.append("method.per_threshold must be a boolean")
        shrink = spec.get("shrink_rows")
        if shrink is not None:
            if not (number_ok(shrink) and shrink >= 0):
                problems.append("method.shrink_rows must be a number >= 0")
            if spec.get("by") is None and spec.get("quantile_by") is None:
                problems.append("method.shrink_rows needs by or quantile_by")
        return problems

    def fit(self, panel, rows, fold, weights=None, tag=None):
        """Solve the global weights and each bucket's (shrunk toward the global ones)."""
        states = self.cells.fit(panel, rows)
        keys = self.cells.keys(panel, rows, states)
        total, per_key, counts, masses = self._moments(panel, rows, keys, weights)
        glob = self._solve(total, self._mass(rows, weights))
        shrink = self.spec.get("shrink_rows")
        buckets = {}
        for key, moment in per_key.items():
            n, mass = counts[key], masses[key]
            if n < self.spec["min_rows"]:
                continue
            w = self._solve(moment, mass)
            buckets[key] = w if not shrink else (mass * w + shrink * glob) / (mass + shrink)
        state = {"components": self.used(), "per_threshold": self.spec["per_threshold"],
                 "bucketing": self.cells.dimensions[0].describe(states[0]) if states else None,
                 "global": {"rows": len(rows), "weights": glob},
                 "buckets": {self.cells.name_of(panel, k, states): {"rows": counts[k], "weights": w}
                             for k, w in buckets.items()}}
        return _Fitted(state, {"global": glob, "buckets": buckets, "states": states})

    def predict(self, panel, rows, fitted):
        """Pool the components with the row's bucket weights (the global ones when it has none)."""
        import numpy as np

        weights = np.broadcast_to(fitted.model["global"], (len(rows), *fitted.model["global"].shape)).copy()
        if fitted.model["buckets"]:
            keys = self.cells.keys(panel, rows, fitted.model["states"])
            for key, w in fitted.model["buckets"].items():
                weights[keys == key] = w
        stack = self._stack(panel, rows)
        if self.spec["per_threshold"]:
            return _rearranged(np.einsum("mkj,mjk->mk", weights, stack))
        return np.einsum("mj,mjk->mk", weights, stack)


class _ParametricPoolMethod(_PoolBase):
    """A pool whose few parameters are fitted numerically on a strided sample of the learning rows.

    Subclasses name the objective (:meth:`_objective`) and how fitted parameters turn into CDFs
    (:meth:`_apply`). The fit is the optimizer's start from the exact linear-pool weights, per bucket of
    ``by`` with at least ``min_rows`` rows and once globally; fewer than ``min_rows`` learning rows decline.
    """

    _KEYS = ("components", "by", "min_rows", "max_learning_rows")
    _REQUIRED = ("components", "min_rows")
    _DEFAULTS = {"max_learning_rows": DEFAULT_MAX_LEARNING_ROWS}

    @classmethod
    def _extra_problems(cls, spec, params, names):
        problems = []
        cls._component_list_problems(problems, spec, names)
        cls._rows_problem(problems, spec)
        cls._cell_problems(problems, spec, single=True)
        check_int_param(problems, "method.max_learning_rows", spec.get("max_learning_rows"), ge=1)
        return problems + cls._shape_problems(spec)

    @classmethod
    def _shape_problems(cls, spec):
        """Return the problems with the kind's own keys; the default kind has none."""
        return []

    @abstractmethod
    def _objective(self, panel, rows, weights):
        """Return the :class:`_PoolObjective` over the (already strided) learning ``rows``."""

    @abstractmethod
    def _apply(self, panel, rows, params):
        """Return the ``(len(rows), points)`` CDF under one cell's fitted ``params`` (a state dict)."""

    def _fit_cell(self, panel, rows, weights):
        """Fit one cell on a deterministic stride of ``rows`` and return its state dict."""
        stride = max(1, math.ceil(len(rows) / self.spec["max_learning_rows"]))
        used = rows[::stride]
        used_weights = None if weights is None else weights[::stride]
        start = self._exact_weights(panel, used, used_weights)
        solved = self._objective(panel, used, used_weights).solve(start)
        return {"rows": len(rows), "rows_used": len(used), **solved}

    def fit(self, panel, rows, fold, weights=None, tag=None):
        """Fit the global cell and each bucket with enough rows; None when ``rows`` are below ``min_rows``."""
        if len(rows) < self.spec["min_rows"]:
            return None
        states = self.cells.fit(panel, rows)
        groups = _Groups(self.cells.keys(panel, rows, states))
        glob = self._fit_cell(panel, rows, weights)
        buckets = {int(k): self._fit_cell(panel, rows[m], None if weights is None else weights[m])
                   for k, m in zip(groups.keys, groups.members()) if len(m) >= self.spec["min_rows"]}
        state = {"components": self.used(),
                 "bucketing": self.cells.dimensions[0].describe(states[0]) if states else None,
                 "global": glob,
                 "buckets": {self.cells.name_of(panel, k, states): b for k, b in buckets.items()}}
        return _Fitted(state, {"global": glob, "buckets": buckets, "states": states})

    def predict(self, panel, rows, fitted):
        """Apply the row's bucket parameters, the global ones where its bucket has none."""
        import numpy as np

        out = np.empty((len(rows), self.grid.points))
        covered = np.zeros(len(rows), dtype=bool)
        if fitted.model["buckets"]:
            keys = self.cells.keys(panel, rows, fitted.model["states"])
            for key, params in fitted.model["buckets"].items():
                hit = keys == key
                if hit.any():
                    out[hit] = self._apply(panel, rows[hit], params)
                    covered |= hit
        if not covered.all():
            out[~covered] = self._apply(panel, rows[~covered], fitted.model["global"])
        return out


class _BetaPoolMethod(_ParametricPoolMethod):
    """A beta-transformed linear pool (Ranjan and Gneiting 2010): ``BetaCDF_{a,b}(sum_i w_i F_i)``."""

    KIND = "beta_pool"

    def _objective(self, panel, rows, weights):
        return _BetaPoolObjective(self.grid, self._stack(panel, rows), panel.y[rows], weights)

    def _apply(self, panel, rows, params):
        """Return the beta transform of the pool at the cell's weights, ``a`` and ``b``."""
        from scipy import special

        import numpy as np

        pooled = np.einsum("j,mjk->mk", np.asarray(params["weights"]), self._stack(panel, rows)).clip(0.0, 1.0)
        return special.betainc(params["a"], params["b"], pooled)


class _QuantileAverageMethod(_ParametricPoolMethod):
    """The weighted average of the components' quantile functions, read back as a CDF on the grid."""

    KIND = "quantile_average"
    _KEYS = _ParametricPoolMethod._KEYS + ("levels",)
    _DEFAULTS = {**_ParametricPoolMethod._DEFAULTS, "levels": DEFAULT_LEVELS}

    @classmethod
    def _shape_problems(cls, spec):
        problems = []
        check_int_param(problems, "method.levels", spec.get("levels"), ge=MIN_LEVELS)
        return problems

    def __init__(self, spec, params, grid):
        super().__init__(spec, params, grid)
        self.inverse = _QuantileGrid(grid, self.spec["levels"])

    def _objective(self, panel, rows, weights):
        return _QuantileAverageObjective(self.grid, self._stack(panel, rows), panel.y[rows], weights,
                                         self.spec["levels"])

    def _fit_cell(self, panel, rows, weights):
        """Fit one cell and record the number of quantile levels it used."""
        return {**super()._fit_cell(panel, rows, weights), "levels": int(self.spec["levels"])}

    def _apply(self, panel, rows, params):
        """Return the CDF of the pooled quantile function ``sum_i w_i Q_i``, in chunks of rows."""
        import numpy as np

        out = np.empty((len(rows), self.grid.points))
        for start in range(0, len(rows), QUANTILE_CHUNK):
            part = rows[start:start + QUANTILE_CHUNK]
            pooled = 0.0
            for w, name in zip(params["weights"], self.spec["components"]):
                pooled = pooled + w * self.inverse.invert(panel.cdf[name][part])
            out[start:start + QUANTILE_CHUNK] = self.inverse.locate(pooled)[1]
        return out


class _BestComponentMethod(_Method):
    """Per cell, the component with the lowest mean score, kept only when it beats the default by a margin."""

    KIND = "best_component"
    _KEYS = ("components", "by", "default", "min_rows", "margin")
    _REQUIRED = ("components", "by", "default", "min_rows", "margin")

    @classmethod
    def _extra_problems(cls, spec, params, names):
        problems = []
        parts = spec.get("components")
        if not (isinstance(parts, list) and parts and len(set(map(str, parts))) == len(parts)):
            problems.append("method.components must be a non-empty list of distinct component names")
        else:
            for part in parts:
                cls._name_problems(problems, "components", part, names)
            if spec.get("default") not in parts:
                problems.append(f"method.default {spec.get('default')!r} must be one of method.components")
        cls._rows_problem(problems, spec)
        cls._cell_problems(problems, spec, single=False)
        margin = spec.get("margin")
        if not (number_ok(margin) and margin >= 0):
            problems.append("method.margin must be a number >= 0")
        return problems

    def __init__(self, spec, params, grid):
        super().__init__(spec, params, grid)
        self.cells = _Cells.from_spec(self.spec)

    def needed_columns(self):
        """Return the cell columns."""
        return self.cells.columns()

    def used(self):
        """Return the candidate components."""
        return list(self.spec["components"])

    def _choices(self, means, counts):
        """Return the chosen component index of each learned cell, -1 for a cell too small to decide."""
        import numpy as np

        names = self.used()
        default = names.index(self.spec["default"])
        chosen = np.full(len(counts), -1, dtype=int)
        for i in np.flatnonzero(counts >= self.spec["min_rows"]):
            best = int(np.argmin(means[i]))
            base = means[i][default]
            gain = (base - means[i][best]) / base if base > 0 else 0.0
            chosen[i] = best if best != default and gain > self.spec["margin"] else default
        return chosen

    def fit(self, panel, rows, fold, weights=None, tag=None):
        """Score every component on the learning rows by cell and choose per cell."""
        import numpy as np

        names = self.used()
        scores = np.column_stack([self.grid.score(panel.cdf[n][rows], panel.y[rows]) for n in names])
        states = self.cells.fit(panel, rows)
        groups = _Groups(self.cells.keys(panel, rows, states))
        if weights is None:
            means = groups.sums(scores) / np.maximum(groups.counts, 1)[:, None]
        else:
            means = groups.sums(scores * weights[:, None]) / np.maximum(groups.sums(weights[:, None]), 1e-300)
        chosen = self._choices(means, groups.counts)
        learned = chosen >= 0
        cells = [{"cell": self.cells.decode(panel, k, states), "rows": int(n), "chosen": names[c],
                  "scores": dict(zip(names, m))}
                 for k, n, c, m in zip(groups.keys[learned], groups.counts[learned], chosen[learned], means[learned])]
        state = {"default": self.spec["default"], "components": names, "cells": cells}
        return _Fitted(state, {"keys": groups.keys[learned], "chosen": chosen[learned], "states": states})

    def predict(self, panel, rows, fitted):
        """Return the chosen component's CDF of the row's cell, the default's where the cell is unknown."""
        import numpy as np

        names = self.used()
        pick = np.full(len(rows), names.index(self.spec["default"]), dtype=int)
        known, chosen = fitted.model["keys"], fitted.model["chosen"]
        if known.size:
            keys = self.cells.keys(panel, rows, fitted.model["states"])
            at = np.clip(np.searchsorted(known, keys), 0, known.size - 1)
            hit = (keys >= 0) & (known[at] == keys)
            pick[hit] = chosen[at[hit]]
        stack = np.stack([panel.cdf[n][rows] for n in names], axis=1)
        return stack[np.arange(len(rows)), pick]


class _BinaryGbmMethod(_Method):
    """A monotone LightGBM classifier of ``1{y <= z_k}`` over (threshold, base logit, extra features)."""

    KIND = "binary_gbm"
    _KEYS = ("base", "features", "feature_source", "lgbm_params", "seed", "max_learning_rows")
    _REQUIRED = ("base", "lgbm_params", "seed", "max_learning_rows")
    _DEFAULTS = {"features": []}
    _SOURCE_KEYS = ("path", "sha256", "keys")
    #: Settings the pack sets itself on the classifier, after the document's own.
    _FORCED = {"objective": "binary", "n_jobs": 1, "deterministic": True, "force_row_wise": True,
               "verbosity": -1}

    @classmethod
    def _extra_problems(cls, spec, params, names):
        problems = []
        cls._name_problems(problems, "base", spec.get("base"), names)
        check_int_param(problems, "method.seed", spec.get("seed"), ge=0)
        check_int_param(problems, "method.max_learning_rows", spec.get("max_learning_rows"), ge=1)
        features = spec["features"]
        if not (isinstance(features, list) and all(_is_name(f) for f in features) and len(set(features)) == len(features)):
            problems.append("method.features must be a list of distinct column names")
        elif features:
            cls._source_problems(problems, spec.get("feature_source"), params, features)
        elif spec.get("feature_source") is not None:
            problems.append("method.feature_source is given but method.features is empty")
        problems += cls._lgbm_problems(spec.get("lgbm_params"))
        return problems

    @classmethod
    def _source_problems(cls, problems, source, params, features):
        """Append the problems of the feature source mapping."""
        keys = source.get("keys") if isinstance(source, dict) else None
        if not (isinstance(source, dict) and set(source) == set(cls._SOURCE_KEYS) and _is_name(source["path"])
                and isinstance(source["sha256"], str) and _SHA256.match(source["sha256"])
                and isinstance(keys, list) and keys and all(_is_name(k) for k in keys)):
            problems.append(f"method.feature_source must be exactly {list(cls._SOURCE_KEYS)} with a hex sha256")
            return
        date = params.get("columns", {}).get("date") if isinstance(params.get("columns"), dict) else None
        if date is not None and date not in keys:
            problems.append(f"method.feature_source.keys must include the date column {date!r}, so end_before "
                            "bounds the feature read")
        if set(features) & set(keys):
            problems.append("method.features may not name a key column")

    @staticmethod
    def _lgbm_problems(extra):
        """Return the problems with ``lgbm_params`` under the LightGBM pack's default-deny key set."""
        if not isinstance(extra, dict) or any(not isinstance(k, str) for k in extra):
            return [f"method.lgbm_params must be a dict with string keys, got {extra!r}"]
        problems = []
        forced = sorted(set(extra) & set(FORCED_LGBM_KEYS))
        if forced:
            problems.append(f"method.lgbm_params may not set {forced} - the node forces them")
        unknown = sorted(set(extra) - set(ALLOWED_LGBM_KEYS) - set(FORCED_LGBM_KEYS))
        if unknown:
            problems.append(f"method.lgbm_params does not accept {unknown} - allowed: {list(ALLOWED_LGBM_KEYS)}")
        kinds = {"str": _is_name, "int": _is_int, "number": number_ok}
        wrong = sorted(k for k, v in extra.items() if k in LGBM_KEY_TYPES and not kinds[LGBM_KEY_TYPES[k]](v))
        if wrong:
            problems.append(f"method.lgbm_params {wrong} have the wrong type - expected "
                            f"{ {k: LGBM_KEY_TYPES[k] for k in wrong} }")
        if extra.get("objective", "binary") != "binary":
            problems.append("method.lgbm_params.objective must be 'binary'")
        return problems

    def needed_columns(self):
        """Return the feature-source key columns, read from the scored rows to join on."""
        source = self.spec.get("feature_source")
        return list(source["keys"]) if source else []

    def used(self):
        """Return the base component."""
        return [self.spec["base"]]

    def prepare(self, panel):
        """Join the extra feature columns onto the rows by the declared keys."""
        source = self.spec.get("feature_source")
        if not source:
            return
        import pandas as pd

        _Source.verify(source["path"], source["sha256"], "feature source")
        date = self.params["columns"]["date"]
        keys, names = source["keys"], self.spec["features"]
        table = _Source.read(source["path"], [*keys, *names], [date], self.params["end_before"])
        right = table.to_pandas(date_as_object=False)
        if right.duplicated(subset=keys).any():
            raise ValueError(f"feature source {source['path']} holds duplicate keys {keys}")
        left = pd.DataFrame({k: panel.frame[k].to_numpy() for k in keys})
        for frame in (left, right):
            frame[date] = _Dates.day_numbers(frame[date])
        joined = left.merge(right, on=keys, how="left", sort=False)
        for name in names:
            panel.extra[name] = pd.to_numeric(joined[name]).to_numpy(dtype=float)

    def _examples(self, panel, rows):
        """Return the ``(rows * points, 2 + features)`` matrix of threshold, base logit and extras."""
        import numpy as np

        points = self.grid.points
        base = np.clip(panel.cdf[self.spec["base"]][rows], LOGIT_CLIP, 1.0 - LOGIT_CLIP)
        x = np.empty((len(rows) * points, 2 + len(self.spec["features"])))
        x[:, 0] = np.tile(self.grid.z, len(rows))
        x[:, 1] = np.log(base / (1.0 - base)).ravel()
        for j, name in enumerate(self.spec["features"]):
            x[:, 2 + j] = np.repeat(panel.extra[name][rows], points)
        return x

    def _classifier_params(self):
        """Return the deterministic classifier keywords: defaults, the document's, then the forced ones."""
        monotone = [1, 1] + [0] * len(self.spec["features"])
        return {**DEFAULT_LGBM_PARAMS, **self.spec["lgbm_params"], **self._FORCED,
                "random_state": self.spec["seed"], "monotone_constraints": monotone}

    def fit(self, panel, rows, fold, weights=None, tag=None):
        """Fit the booster on a deterministic stride subsample of the learning rows (sample-weighted if asked)."""
        import lightgbm
        import numpy as np

        stride = max(1, math.ceil(len(rows) / self.spec["max_learning_rows"]))
        used = rows[::stride]
        x = self._examples(panel, used)
        label = (panel.y[used][:, None] <= self.grid.z[None, :]).ravel().astype(int)
        if label.min() == label.max():
            return None
        model = lightgbm.LGBMClassifier(**self._classifier_params())
        if weights is None:
            model.fit(x, label)
        else:
            model.fit(x, label, sample_weight=np.repeat(weights[::stride], self.grid.points))
        text = model.booster_.model_to_string()
        name = f"{tag or f'fold_{fold}'}_booster.txt"
        state = {"booster_sha256": hashlib.sha256(text.encode()).hexdigest(), "booster_file": name,
                 "learning_rows_total": len(rows), "learning_rows_used": len(used),
                 "examples": int(len(label)), "base": self.spec["base"],
                 "features": ["z", "base_logit", *self.spec["features"]],
                 "classifier_params": self._classifier_params()}
        return _Fitted(state, model, {name: text})

    def predict(self, panel, rows, fitted):
        """Return the classifier's probabilities per threshold, made monotone by a running maximum."""
        import numpy as np

        proba = fitted.model.predict_proba(self._examples(panel, rows))[:, 1]
        return np.clip(np.maximum.accumulate(proba.reshape(len(rows), self.grid.points), axis=1), 0.0, 1.0)


#: The method kinds a document may name, kind -> class.
METHOD_KINDS = {cls.KIND: cls for cls in (_PitMapMethod, _LinearPoolMethod, _BestComponentMethod, _BinaryGbmMethod,
                                          _BetaPoolMethod, _QuantileAverageMethod)}


# --------------------------------------------------------------------------------------- components


class _Components:
    """The declared forecasters: each source's raw CDF, the external ones joined in, the per-fold PIT maps.

    A component is internal (its ``<source>_cdf_<label>`` columns are in the main rows) or, when it names a
    ``path``, external: the same columns are read from another rows parquet and left-joined by ``keys``.
    Internally everything is keyed by the component NAME, so an internal and an external component may share
    a source prefix and stay distinct.
    """

    def __init__(self, specs, grid):
        self.specs = specs
        self.grid = grid
        self.internal = [s for s in specs if "path" not in s]
        self.external = [s for s in specs if "path" in s]

    @staticmethod
    def verify_external(specs):
        """Raise ValueError unless every external component's file has the sha256 its spec declares."""
        for spec in specs:
            if "path" in spec:
                _Source.verify(spec["path"], spec["sha256"], f"component {spec['name']!r} file")

    def key_columns(self):
        """Return the identity columns the external joins read from the main rows."""
        return list(dict.fromkeys(k for s in self.external for k in s["keys"]))

    def _cdf_columns(self, spec):
        """Return the ``<source>_cdf_<label>`` column names of one component."""
        return [f"{spec['source']}_cdf_{label}" for label in self.grid.labels]

    def source_columns(self):
        """Return every internal ``<source>_cdf_<label>`` column and PIT column the main read must carry."""
        columns = [c for s in self.internal for c in self._cdf_columns(s)]
        columns += [s["pit_column"] for s in self.internal if s.get("pit_column")]
        return list(dict.fromkeys(columns))

    def pit_columns(self):
        """Return the internal components' declared PIT columns, which the panel reads row by row."""
        return list(dict.fromkeys(s["pit_column"] for s in self.internal if s.get("pit_column")))

    def raw_matrix(self, table, source):
        """Return the ``(rows, points)`` float64 CDF matrix of one source forecaster."""
        import numpy as np

        return np.column_stack([table[f"{source}_cdf_{label}"].to_numpy().astype(float, copy=False)
                                for label in self.grid.labels])

    def internal_matrices(self, table):
        """Return ``{component name: raw CDF matrix}`` of the internal components (one matrix per source)."""
        by_source, raw = {}, {}
        for spec in self.internal:
            if spec["source"] not in by_source:
                by_source[spec["source"]] = self.raw_matrix(table, spec["source"])
            raw[spec["name"]] = by_source[spec["source"]]
        return raw

    def attach_external(self, panel, raw, end_before):
        """Join each external component's CDFs onto the panel's rows; return the coverage record.

        The file is read under the same ``end_before`` bound as the main rows (on the date key), its keys
        must be unique after that bound, and the join is a left join on ``keys``: a row with no match gets
        NaN, so it can neither teach nor be forecast, and falls back where the method needs the component.
        """
        import numpy as np
        import pandas as pd

        date = panel.roles["date"]
        record = {}
        for spec in self.external:
            keys, names = spec["keys"], self._cdf_columns(spec)
            wanted = [*keys, *names, *([spec["pit_column"]] if spec.get("pit_column") else [])]
            right = _Source.read(spec["path"], list(dict.fromkeys(wanted)), [date], end_before).to_pandas(
                date_as_object=False)
            if right.duplicated(subset=keys).any():
                raise ValueError(f"component {spec['name']!r} file {spec['path']} holds duplicate keys {keys}")
            left = pd.DataFrame({k: panel.frame[k].to_numpy() for k in keys})
            for frame in (left, right):
                frame[date] = _Dates.day_numbers(frame[date])
            joined = left.merge(right, on=keys, how="left", sort=False)
            raw[spec["name"]] = np.column_stack([pd.to_numeric(joined[n]).to_numpy(dtype=float) for n in names])
            if spec.get("pit_column"):
                panel.external_pit[spec["name"]] = pd.to_numeric(joined[spec["pit_column"]]).to_numpy(dtype=float)
            matched = np.isfinite(raw[spec["name"]]).all(axis=1)
            record[spec["name"]] = {
                "path": spec["path"], "sha256": spec["sha256"], "keys": list(keys), "rows_read": int(len(right)),
                "coverage": {str(int(f)): float(matched[panel.fold == f].mean()) for f in np.unique(panel.fold)}}
        return record

    def build(self, panel, raw):
        """Fill ``panel.cdf`` with every component and return the per-fold map record.

        A component with a ``pit_map`` is its source recalibrated, fold by fold, by the empirical CDF of
        the source's PIT on that fold's learning rows; a fold with no learning rows is left missing.
        """
        import numpy as np

        record = {}
        for spec in self.specs:
            if "pit_map" not in spec:
                panel.cdf[spec["name"]] = raw[spec["name"]]
                continue
            out = np.full_like(raw[spec["name"]], np.nan)
            record[spec["name"]] = self._fold_maps(panel, raw[spec["name"]], spec, out)
            panel.cdf[spec["name"]] = out
        return record

    def _fold_maps(self, panel, source_cdf, spec, out):
        """Fit and apply the PIT map of one component for every fold, filling ``out``."""
        import numpy as np

        pit = self._source_pit(panel, source_cdf, spec)
        maps = {}
        pit_map = _PitMap(spec["pit_map"]["knots"])
        rule = LEARNING_RULES[spec["pit_map"]["learn"]]
        ok = panel.eligible & np.isfinite(panel.y) & np.isfinite(pit) & np.isfinite(source_cdf).all(axis=1)
        for fold in np.unique(panel.fold):
            learn = np.flatnonzero(rule.mask(panel, fold) & ok)
            maps[str(fold)] = {"learning_rows": int(len(learn))}
            if not len(learn):
                continue
            g = pit_map.fit(pit[learn])
            target = np.flatnonzero(panel.fold == fold)
            out[target] = pit_map.apply(source_cdf[target], g)
            maps[str(fold)].update({"knots": pit_map.knots, "g": g})
        return maps

    def _source_pit(self, panel, source_cdf, spec):
        """Return the PIT of the source: the declared column when named, else read off the grid CDF."""
        declared = panel.declared_pit(spec["name"])
        return self.grid.pit(source_cdf, panel.y) if declared is None else declared


# ---------------------------------------------------------------------------------------------- tables


class _Selection:
    """The rows one family of comparisons reads, and every forecaster's per-row scores on them."""

    def __init__(self, panel, grid, forecasts, rows):
        import numpy as np
        import pandas as pd

        self.panel, self.grid, self.forecasts, self.rows = panel, grid, forecasts, rows
        self.y = panel.y[rows]
        self.scores = {name: {"twcrps": grid.score(f[rows], self.y), "crps13": grid.score(f[rows], self.y, grid.plain)}
                       for name, f in forecasts.items()}
        self._regions = {}
        self.date_codes = np.unique(panel.date_days[rows], return_inverse=True)[1].astype(np.int64)
        self.group_codes = pd.factorize(panel.frame[panel.roles["group"]].iloc[rows])[0].astype(np.int64)

    def region(self, name):
        """Return the ``(rows, regions)`` Brier of a forecaster (computed once)."""
        if name not in self._regions:
            self._regions[name] = self.grid.region_brier(self.forecasts[name][self.rows], self.y)
        return self._regions[name]


class _Clusters:
    """The clusters of the rows in each cell: the ratio-of-totals linearization of a mean's standard error."""

    def __init__(self, cell, cells, codes):
        import numpy as np

        width = codes.max() + 1
        uniq, self.inverse = np.unique(cell * width + codes, return_inverse=True)
        self.owner = uniq // width
        self.size = np.bincount(self.inverse)
        self.count = np.bincount(self.owner, minlength=cells)
        self.rows = np.bincount(cell, minlength=cells).astype(float)
        self.cells = cells

    def se(self, values):
        """Return the cell x column standard error of the column means of ``values`` (``(rows, C)``).

        NaN where a cell holds fewer than two clusters. With cluster totals ``T_j``, sizes ``m_j``, the
        cell mean ``theta`` and ``g`` clusters, it is ``sqrt(g / (g - 1) * sum_j (T_j - theta * m_j)^2) / n``.
        """
        import numpy as np

        out = np.full((self.cells, values.shape[1]), np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            for c in range(values.shape[1]):
                total = np.bincount(self.inverse, weights=values[:, c])
                theta = np.bincount(self.owner, weights=total, minlength=self.cells) / self.rows
                ss = np.bincount(self.owner, weights=(total - theta[self.owner] * self.size) ** 2,
                                 minlength=self.cells)
                out[:, c] = np.where(self.count >= 2,
                                     np.sqrt(self.count / np.maximum(self.count - 1, 1) * ss) / self.rows, np.nan)
        return out


class _PairedTable:
    """Paired comparisons of the method with each comparator over the cells of a grouping."""

    COLUMNS = ("comparator", "metric", "region", "region_group", "rows", "dates", "method", "comparator_mean",
               "edge_pct", "se_pct", "t")

    def __init__(self, selection, comparators):
        self.selection = selection
        self.comparators = comparators

    @staticmethod
    def _cell_index(categories, n):
        """Return ``(cell, uniq_codes)``: a compact cell id per row (-1 where any category is missing)."""
        import numpy as np

        key = np.zeros(n, dtype=np.int64)
        bad = np.zeros(n, dtype=bool)
        mult = 1
        for codes, labels, _ in categories:
            bad |= codes < 0
            key += np.where(codes < 0, 0, codes) * mult
            mult *= max(len(labels), 1)
        uniq, inverse = np.unique(key[~bad], return_inverse=True)
        cell = np.full(n, -1, dtype=np.int64)
        cell[~bad] = inverse
        return cell, uniq

    def _metrics(self, metrics, regions, groups):
        """Return ``(labels, method columns, {comparator: columns})``: one column per metric, region and group."""
        import numpy as np

        sel = self.selection
        labels = [(metric, None, None) for metric in metrics]
        method = [sel.scores["method"][metric][:, None] for metric in metrics]
        others = {c: [sel.scores[c][metric][:, None] for metric in metrics] for c in self.comparators}
        if regions:
            labels += [(REGION_METRIC, r, None) for r in range(sel.grid.regions)]
            method.append(sel.region("method"))
            for c in self.comparators:
                others[c].append(sel.region(c))
        for name, members in groups.items():
            labels.append((REGION_GROUP_METRIC, None, name))
            method.append(sel.region("method")[:, members].sum(axis=1, keepdims=True))
            for c in self.comparators:
                others[c].append(sel.region(c)[:, members].sum(axis=1, keepdims=True))
        return labels, np.hstack(method), {c: np.hstack(v) for c, v in others.items()}

    @staticmethod
    def _means(values, cell, cells, rows):
        """Return the cell x column means of ``values``."""
        import numpy as np

        return np.column_stack([np.bincount(cell, weights=values[:, c], minlength=cells)
                                for c in range(values.shape[1])]) / rows[:, None]

    def frame(self, categories, metrics, regions, by_group, groups=None):
        """Return the comparison table: one row per cell x comparator x metric (x region).

        Parameters
        ----------
        categories : list of tuple
            ``(codes, labels, column name)`` per grouping column, aligned to the selection's rows; none
            means one pooled cell.
        metrics : tuple of str
            The score metrics to compare.
        regions : bool
            Whether to add the per-region Brier metric.
        by_group : bool
            Cluster by the group column as well as by date (the larger standard error), else by date only.
        groups : dict, optional
            ``{name: region indices}``: each adds the metric ``region_group_brier``, the per-row sum of
            those regions' Brier scores (a strike zone), with its own paired standard error.

        Returns
        -------
        pandas.DataFrame
            The grouping columns then :attr:`COLUMNS`; empty when no cell holds a row.
        """
        import numpy as np
        import pandas as pd

        sel = self.selection
        cell, uniq = self._cell_index(categories, len(sel.rows))
        keep = cell >= 0
        if not keep.any():
            return pd.DataFrame(columns=[c[2] for c in categories] + list(self.COLUMNS))
        used, local = np.unique(cell[keep], return_inverse=True)
        cells = len(used)
        codes = [sel.date_codes[keep], *([sel.group_codes[keep]] if by_group else [])]
        clusters = [_Clusters(local, cells, c) for c in codes]
        labels = self._cell_labels(categories, uniq[used])
        names, method, others = self._metrics(metrics, regions, groups or {})
        method_mean = self._means(method[keep], local, cells, clusters[0].rows)
        pieces = []
        for position, comparator in enumerate(self.comparators):
            other = others[comparator][keep]
            other_mean = self._means(other, local, cells, clusters[0].rows)
            ses = np.stack([c.se(other - method[keep]) for c in clusters])
            se = np.where(np.isnan(ses).any(axis=0), np.nan, ses.max(axis=0))
            with np.errstate(invalid="ignore", divide="ignore"):
                edge = 100.0 * (other_mean - method_mean) / other_mean
                se_pct = 100.0 * se / other_mean
                t = np.where(np.isfinite(se_pct) & (se_pct > 0), edge / se_pct, np.nan)
            for column, (metric, region, group) in enumerate(names):
                piece = pd.DataFrame(labels, index=range(cells))
                piece["comparator"], piece["metric"] = comparator, metric
                piece["region"] = pd.array([region] * cells, dtype="Int64")
                piece["region_group"] = pd.array([group] * cells, dtype="string")
                piece["rows"], piece["dates"] = clusters[0].rows.astype(int), clusters[0].count
                piece["method"], piece["comparator_mean"] = method_mean[:, column], other_mean[:, column]
                piece["edge_pct"], piece["se_pct"], piece["t"] = edge[:, column], se_pct[:, column], t[:, column]
                piece["_order"] = column * len(self.comparators) + position
                piece["_cell"] = np.arange(cells)
                pieces.append(piece)
        table = pd.concat(pieces, ignore_index=True).sort_values(["_cell", "_order"], kind="stable")
        return table.drop(columns=["_cell", "_order"]).reset_index(drop=True)

    @staticmethod
    def _cell_labels(categories, keys):
        """Return ``{column name: labels}`` of each cell, decoded from the mixed-radix keys."""
        out, mult = {}, 1
        for _, labels, name in categories:
            size = max(len(labels), 1)
            out[name] = [labels[(int(k) // mult) % size] for k in keys]
            mult *= size
        return out


class _Tables:
    """The declared comparison tables and the always-written windows table."""

    def __init__(self, panel, grid, params, forecasts):
        import numpy as np

        self.panel, self.grid, self.params = panel, grid, params
        names = ["method", *params["comparators"]]
        ranked = np.isin(panel.fold, params["ranked_folds"])
        finite = np.isfinite(panel.y)
        for name in names:
            finite = finite & np.isfinite(forecasts[name]).all(axis=1)
        self.val = _Selection(panel, grid, forecasts, np.flatnonzero(ranked & panel.is_val & finite))
        self.fit = _Selection(panel, grid, forecasts, np.flatnonzero(ranked & panel.is_fit & finite))

    def _categories(self, selection, names):
        """Return ``(codes, labels, name)`` per grouping column, aligned to the selection."""
        return [(self.panel.category(n)[0][selection.rows], self.panel.category(n)[1], n) for n in names]

    def declared(self, spec):
        """Return the table of one ``tables`` entry."""
        names = spec["by"]
        by_group = self.params["columns"]["group"] not in names
        return _PairedTable(self.val, self.params["comparators"]).frame(
            self._categories(self.val, names), SCORE_METRICS, spec["regions"], by_group, spec.get("region_groups"))

    def windows(self):
        """Return the windows table: fold, year, period, pooled over the ranked folds, and the fit band."""
        import numpy as np
        import pandas as pd

        pieces = []
        grouping = [("fold", self.panel.roles["fold"]), ("year", YEAR_COLUMN)]
        if self.params.get("periods"):
            grouping.append(("period", PERIOD_COLUMN))
        for kind, column in grouping:
            pieces.append(self._window(self.val, kind, self._categories(self.val, [column])))
        for kind, selection in (("pooled", self.val), ("in_sample", self.fit)):
            label = "ranked" if kind == "pooled" else "fit_band"
            constant = (np.zeros(len(selection.rows), dtype=np.int64), [label], "window")
            pieces.append(self._window(selection, kind, [constant]))
        return pd.concat(pieces, ignore_index=True)

    def _window(self, selection, kind, categories):
        """Return one window kind's rows with the window label in ``window``."""
        table = _PairedTable(selection, self.params["comparators"]).frame(
            [(codes, labels, "window") for codes, labels, _ in categories], SCORE_METRICS, False, True)
        table.insert(0, "window_kind", kind)
        return table.drop(columns=["region"])


# ------------------------------------------------------------------------------------------------- node


class GridCDFMethod(Node):
    """Build one forecaster per fold, by one method, from CDFs on a declared grid in a scored-rows parquet.

    Parameters
    ----------
    params : dict
        ``source`` (rows parquet path) and ``source_sha256``; ``output`` (a NEW directory); ``end_before``
        (ISO date, rows with a quote or settlement date at or after it are never read); ``columns`` (the
        roles ``identity`` (list), ``date``, ``settlement``, ``fold``, ``band``, ``outcome``, ``dte``,
        ``group``, ``eligible``); ``z_grid`` (ascending floats), ``grid_labels`` (column suffixes),
        ``score_weights`` (one per point), ``pit_tail`` (float > 0, the PIT's linear tail width);
        ``components`` (list of ``{name, source, pit_map?: {knots, learn: "fit_band"}, pit_column?, path?,
        sha256?, keys?}``; ``path``, ``sha256`` and ``keys`` come together and make the component external:
        its ``<source>_cdf_<label>`` columns (and ``pit_column``) are read from that scored-rows parquet,
        hash-verified and bounded by ``end_before`` on the date key, whose ``keys`` (identity columns that
        include the date column) must be unique there and are left-joined onto the main rows, an unmatched
        row being NaN; components are keyed by ``name``, so an external and an internal one may share a
        ``source`` prefix); ``fallback`` (component used where the method is not fitted or a needed CDF is
        missing); ``method`` (``{kind, learn: "fit_band" | "prior_validation" | "rolling", refit_days (rolling
        only, int >= 1), half_life_days? (number > 0), ...}`` with the kind's own keys, see
        :data:`METHOD_KINDS`); ``comparators`` (component names) and ``ranked_folds`` (ints) for the tables;
        ``tables`` (list of ``{name, by, regions, region_groups?}``; a region group sums named regions into
        a strike zone); optional ``buckets`` (derived bucketed columns,
        ``{name: {column, edges: [[lo, hi], ...]}}``) and ``periods`` (``{name: [start, end]}``).

    The kinds and their keys (all take ``learn`` and ``half_life_days``; ``min_rows`` counts rows):
    ``pit_map`` (``component``, ``knots``, ``min_rows``, ``by?``); ``linear_pool`` (``components``,
    ``min_rows``, ``by?``, ``quantile_by?``, ``per_threshold?``, ``shrink_rows?``, where ``shrink_rows``
    counts weight mass under a half life); ``best_component`` (``components``, ``by``, ``default``,
    ``min_rows``, ``margin``); ``binary_gbm`` (``base``, ``lgbm_params``, ``seed``, ``max_learning_rows``,
    ``features?``, ``feature_source?``); ``beta_pool`` (``components`` 2 to 6, ``min_rows``, ``by?``,
    ``max_learning_rows?``, default :data:`DEFAULT_MAX_LEARNING_ROWS`); ``quantile_average`` (the same plus
    ``levels?`` interior probabilities, default :data:`DEFAULT_LEVELS`).

    ``fit.json`` holds, per fold, the first block's ``learning_rows``, ``fitted`` and ``state`` and the row
    totals; for ``rolling`` also ``blocks``, each ``{start, learning_rows, fitted, reason, rows, rows_fitted,
    state}``; a ``half_life_days`` on a single-block rule adds the fold's ``reference`` date; when a component
    is external, ``external`` maps its name to ``{path, sha256, keys, rows_read, coverage: {fold: share of
    the fold's rows with a matched, finite CDF}}``. A parametric pool's ``state`` carries ``weights`` (and
    ``a``, ``b`` for ``beta_pool``), ``rows``, ``rows_used``, the ``objective`` reached and the objective of
    its comparison start (``linear_pool_objective`` or ``start_objective``).

    Examples
    --------
    Pool a model and a recalibrated market with one weight learned on each fold's fit band::

        node = GridCDFMethod("pool", {
            "source": "rows.parquet", "source_sha256": "<sha256>", "output": "out/pool",
            "end_before": "2024-01-01",
            "columns": {"identity": ["row_id", "quote_date"], "date": "quote_date",
                        "settlement": "settlement_date", "fold": "fold", "band": "band", "outcome": "y",
                        "dte": "dte", "group": "symbol", "eligible": "market_eligible"},
            "z_grid": [-1.0, 0.0, 1.0], "grid_labels": ["zm1", "z0", "zp1"],
            "score_weights": [1.0, 1.0, 1.0], "pit_tail": 4.0,
            "components": [{"name": "model", "source": "mdl"}, {"name": "market", "source": "mkt"}],
            "fallback": "model",
            "method": {"kind": "linear_pool", "learn": "fit_band", "components": ["model", "market"],
                       "min_rows": 100},
            "comparators": ["market"], "ranked_folds": [2, 3], "tables": []})
        out = node.run(ctx, {})
        # -> {"summary": {"output": "out/pool", "rows": ..., "rows_not_fitted": ..., ...}}
    """

    role = "transform"
    outputs = ("summary",)
    _PARAMS = ("source", "source_sha256", "output", "end_before", "columns", "z_grid", "grid_labels",
               "score_weights", "pit_tail", "components", "fallback", "method", "comparators", "ranked_folds",
               "tables", "buckets", "periods")
    _OPTIONAL = ("buckets", "periods")
    _COMPONENT_KEYS = ("name", "source", "pit_map", "pit_column", "path", "sha256", "keys")
    _EXTERNAL_KEYS = ("path", "sha256", "keys")

    @classmethod
    def validate_params(cls, params):
        """List the problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One message per problem.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        missing = sorted(set(cls._PARAMS) - set(cls._OPTIONAL) - set(params))
        if missing:
            problems.append(f"missing parameters: {missing}")
        cls._file_problems(problems, params)
        cls._grid_problems(problems, params)
        names = cls._component_problems(problems, params.get("components"), params.get("columns"))
        if "fallback" in params and params["fallback"] not in names:
            problems.append(f"fallback names {params['fallback']!r}, not a declared component {sorted(names)}")
        cls._comparator_problems(problems, params, names)
        cls._derived_problems(problems, params)
        cls._table_problems(problems, params.get("tables"), params.get("z_grid"), bool(params.get("periods")))
        cls._method_problems(problems, params, names)
        return problems

    @classmethod
    def _file_problems(cls, problems, params):
        for name in ("source", "output"):
            if name in params and not _is_name(params[name]):
                problems.append(f"{name} must be a non-empty string")
        if "source_sha256" in params and not (isinstance(params["source_sha256"], str)
                                               and _SHA256.match(params["source_sha256"])):
            problems.append("source_sha256 must be 64 lowercase hex characters")
        if "end_before" in params and _Dates.parse(params["end_before"]) is None:
            problems.append(f"end_before must be an ISO date text YYYY-MM-DD, got {params['end_before']!r}")
        if _is_name(params.get("output")) and Path(params["output"]).exists():
            problems.append(f"output {params['output']} exists; choose a new directory")
        columns = params.get("columns")
        if "columns" in params:
            ok = (isinstance(columns, dict) and set(columns) == set(COLUMN_ROLES)
                  and all(_is_name(columns[r]) for r in COLUMN_ROLES if r != "identity")
                  and isinstance(columns.get("identity"), list) and columns["identity"]
                  and all(_is_name(c) for c in columns["identity"]))
            if not ok:
                problems.append(f"columns must name exactly {list(COLUMN_ROLES)} (identity a non-empty list)")
        if "ranked_folds" in params:
            folds = params["ranked_folds"]
            if not (isinstance(folds, list) and folds and all(_is_int(f) for f in folds)):
                problems.append("ranked_folds must be a non-empty list of ints")

    @classmethod
    def _grid_problems(cls, problems, params):
        grid = params.get("z_grid")
        if "z_grid" in params and not (isinstance(grid, list) and len(grid) >= 2 and all(number_ok(z) for z in grid)
                                       and all(a < b for a, b in zip(grid, grid[1:]))):
            problems.append("z_grid must be at least two finite numbers in ascending order")
            grid = None
        points = len(grid) if grid else None
        labels = params.get("grid_labels")
        if "grid_labels" in params and not (isinstance(labels, list) and all(_is_name(x) for x in labels)
                                            and len(set(labels)) == len(labels)
                                            and (points is None or len(labels) == points)):
            problems.append("grid_labels must be distinct non-empty strings, one per z_grid point")
        weights = params.get("score_weights")
        if "score_weights" in params and not (isinstance(weights, list) and all(number_ok(w) and w >= 0 for w in weights)
                                              and (points is None or len(weights) == points)):
            problems.append("score_weights must be non-negative numbers, one per z_grid point")
        if "pit_tail" in params and not (number_ok(params["pit_tail"]) and params["pit_tail"] > 0):
            problems.append("pit_tail must be a number above zero")

    @classmethod
    def _component_problems(cls, problems, components, columns):
        """Append the components' problems; return the set of declared names."""
        if components is None:
            return set()
        if not (isinstance(components, list) and components and all(isinstance(c, dict) for c in components)):
            problems.append("components must be a non-empty list of mappings")
            return set()
        names = [c.get("name") for c in components]
        if METHOD_NAME in names:
            problems.append(f"a component may not be named {METHOD_NAME!r}: it is reserved for the new forecaster")
        for index, spec in enumerate(components):
            reject_unknown_params(problems, spec, cls._COMPONENT_KEYS)
            if not (_is_name(spec.get("name")) and _is_name(spec.get("source"))):
                problems.append(f"components[{index}] needs a name and a source")
            cls._external_problems(problems, index, spec, columns.get("date") if isinstance(columns, dict) else None)
            if "pit_column" in spec and not _is_name(spec["pit_column"]):
                problems.append(f"components[{index}].pit_column must be a column name")
            if "pit_map" in spec:
                pit = spec["pit_map"]
                if not (isinstance(pit, dict) and set(pit) == {"knots", "learn"}):
                    problems.append(f"components[{index}].pit_map must be exactly {{knots, learn}}")
                    continue
                check_int_param(problems, f"components[{index}].pit_map.knots", pit["knots"], ge=2)
                if pit["learn"] != "fit_band":
                    problems.append(f"components[{index}].pit_map.learn must be 'fit_band'")
        if len(set(names)) != len(names):
            problems.append(f"duplicate component names in {names}")
        return {n for n in names if _is_name(n)}

    @classmethod
    def _external_problems(cls, problems, index, spec, date):
        """Append the problems of a component's ``path``, ``sha256`` and ``keys``: all or none, keys with the date."""
        given = [k for k in cls._EXTERNAL_KEYS if k in spec]
        if not given:
            return
        if len(given) != len(cls._EXTERNAL_KEYS):
            problems.append(f"components[{index}] needs path, sha256 and keys together, got only {given}")
            return
        if not _is_name(spec["path"]):
            problems.append(f"components[{index}].path must be a non-empty string")
        if not (isinstance(spec["sha256"], str) and _SHA256.match(spec["sha256"])):
            problems.append(f"components[{index}].sha256 must be 64 lowercase hex characters")
        keys = spec["keys"]
        if not (isinstance(keys, list) and keys and all(_is_name(k) for k in keys) and len(set(keys)) == len(keys)):
            problems.append(f"components[{index}].keys must be a non-empty list of distinct column names")
        elif date is not None and date not in keys:
            problems.append(f"components[{index}].keys must include the date column {date!r}, so end_before "
                            "bounds the read")

    @classmethod
    def _comparator_problems(cls, problems, params, names):
        found = params.get("comparators")
        if "comparators" not in params:
            return
        if not (isinstance(found, list) and found and len(set(map(str, found))) == len(found)):
            problems.append("comparators must be a non-empty list of distinct component names")
            return
        for name in found:
            if name not in names:
                problems.append(f"comparators names {name!r}, not a declared component {sorted(names)}")

    @classmethod
    def _derived_problems(cls, problems, params):
        buckets = params.get("buckets", {})
        if not isinstance(buckets, dict):
            problems.append("buckets must be a mapping of name to {column, edges}")
            buckets = {}
        for name, spec in buckets.items():
            edges = spec.get("edges") if isinstance(spec, dict) else None
            ok = (_is_name(name) and name not in (PERIOD_COLUMN, YEAR_COLUMN) and isinstance(spec, dict)
                  and set(spec) == {"column", "edges"} and _is_name(spec["column"]) and isinstance(edges, list)
                  and edges and all(isinstance(e, list) and len(e) == 2 and all(number_ok(v) for v in e)
                                    and e[0] <= e[1] for e in edges)
                  and all(a[1] < b[0] for a, b in zip(sorted(edges), sorted(edges)[1:])))
            if not ok:
                problems.append(f"buckets[{name!r}] must be {{column, edges: non-overlapping [lo, hi] pairs}}")
        periods = params.get("periods", {})
        spans = list(periods.values()) if isinstance(periods, dict) else None
        ok = (spans is not None and all(isinstance(s, list) and len(s) == 2 and all(_Dates.parse(v) for v in s)
                                        and s[0] <= s[1] for s in spans)
              and all(a[1] < b[0] for a, b in zip(sorted(spans), sorted(spans)[1:])))
        if not ok:
            problems.append("periods must map names to non-overlapping [start, end] ISO date pairs")

    @classmethod
    def _table_problems(cls, problems, tables, grid, has_periods):
        if tables is None:
            return
        if not isinstance(tables, list):
            problems.append("tables must be a list of {name, by, regions}")
            return
        seen = []
        for index, spec in enumerate(tables):
            ok = (isinstance(spec, dict) and set(spec) - {"region_groups"} == {"name", "by", "regions"} and _is_name(spec["name"])
                  and _TABLE_NAME.match(spec["name"]) and isinstance(spec["by"], list)
                  and all(_is_name(c) for c in spec["by"]) and isinstance(spec["regions"], bool))
            if not ok:
                problems.append(f"tables[{index}] must be {{name (file-safe), by (column list), regions (bool)}}")
                continue
            if PERIOD_COLUMN in spec["by"] and not has_periods:
                problems.append(f"tables[{index}].by names {PERIOD_COLUMN!r}, but no periods are declared")
            if "region_groups" in spec:
                cls._region_group_problems(problems, index, spec["region_groups"], grid)
            if spec["name"] == WINDOWS_TABLE or spec["name"] in seen:
                problems.append(f"tables[{index}].name {spec['name']!r} is reserved or repeated "
                                f"({WINDOWS_TABLE!r} is always written)")
            seen.append(spec["name"])

    @staticmethod
    def _region_group_problems(problems, index, groups, grid):
        """Require each region group to name a non-empty set of distinct region indices of the grid."""
        regions = len(grid) + 1 if isinstance(grid, list) else None
        ok = isinstance(groups, dict) and bool(groups) and all(
            _is_name(name) and isinstance(members, list) and members
            and all(isinstance(r, int) and not isinstance(r, bool) and r >= 0 and (regions is None or r < regions)
                    for r in members)
            and len(set(members)) == len(members)
            for name, members in groups.items())
        if not ok:
            problems.append(f"tables[{index}].region_groups must map names to non-empty lists of distinct "
                            f"region indices (0 to {regions - 1 if regions else 'len(z_grid)'})")

    @classmethod
    def _method_problems(cls, problems, params, names):
        method = params.get("method")
        if "method" not in params:
            return
        kind = method.get("kind") if isinstance(method, dict) else None
        if kind not in METHOD_KINDS:
            problems.append(f"method.kind must be one of {sorted(METHOD_KINDS)}, got {kind!r}")
            return
        problems += METHOD_KINDS[kind].problems(method, params, names)

    # -- the run ---------------------------------------------------------------------------------------

    def run(self, ctx, inputs):
        """Build the method's forecasts and tables and publish them by one rename.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            Unused; the node reads its rows from disk.

        Returns
        -------
        dict
            ``{"summary": {...}}`` with the output path, the row counts and the table names.

        Raises
        ------
        ValueError
            The output or its partial directory exists, a hash differs, a declared column is absent, a row
            is at or after ``end_before``, or the feature source holds duplicate keys. A failure removes
            what was built and leaves no output.
        """
        started = time.monotonic()
        p = self.params
        output = Path(p["output"])
        work = output.parent / f".{output.name}.partial"
        if output.exists():
            raise ValueError(f"{self.key}: {output} exists; refusing to overwrite an output (choose a new output)")
        if work.exists():
            raise ValueError(f"{self.key}: {work} is a partial directory from an interrupted run; remove it first")
        _Source.verify(p["source"], p["source_sha256"], "source")
        _Components.verify_external(p["components"])
        output.parent.mkdir(parents=True, exist_ok=True)
        work.mkdir()
        try:
            summary = self._publish(work, started)
            os.rename(work, output)
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
        return {"summary": {"output": str(output), "rows": summary["rows"],
                            "rows_not_fitted": summary["rows_not_fitted"], "folds": summary["folds"],
                            "tables": summary["tables"]}}

    def _needed_columns(self, method):
        """Return the parquet columns to read besides the component CDFs."""
        p = self.params
        roles = p["columns"]
        wanted = [*roles["identity"], *(roles[r] for r in COLUMN_ROLES if r != "identity")]
        derived = {*p.get("buckets", {}), YEAR_COLUMN, PERIOD_COLUMN}
        wanted += [spec["column"] for spec in p.get("buckets", {}).values()]
        wanted += _Components(p["components"], None).key_columns()
        wanted += [c for c in method.needed_columns() if c not in derived]
        wanted += [c for table in p["tables"] for c in table["by"] if c not in derived]
        return list(dict.fromkeys(wanted))

    def _load(self, method, components, grid):
        """Read the bounded rows and return the panel with every component CDF built."""
        p = self.params
        roles = p["columns"]
        frame_columns = self._needed_columns(method)
        frame_columns += [c for c in components.pit_columns() if c not in frame_columns]
        table = _Source.read(p["source"], list(dict.fromkeys([*frame_columns, *components.source_columns()])),
                             [roles["date"], roles["settlement"]], p["end_before"])
        raw = components.internal_matrices(table)
        return _Panel(table.select(frame_columns).to_pandas(date_as_object=False), p, grid), raw

    def _publish(self, work, started):
        import numpy as np

        p = self.params
        grid = _Grid(p["z_grid"], p["grid_labels"], p["score_weights"], p["pit_tail"])
        method = METHOD_KINDS[p["method"]["kind"]](p["method"], p, grid)
        components = _Components(p["components"], grid)
        panel, raw = self._load(method, components, grid)
        external_record = components.attach_external(panel, raw, p["end_before"])
        component_record = components.build(panel, raw)
        method.prepare(panel)
        cdf, fitted, folds = self._forecast(panel, method)
        tables = _Tables(panel, grid, p, {"method": cdf, **{c: panel.cdf[c] for c in p["comparators"]}})
        names = self._write_tables(work, tables)
        self._write_forecasts(work / "forecasts.parquet", panel, grid, cdf, fitted)
        for record in folds.values():
            for name, text in record.pop("artifacts").items():
                (work / name).write_text(text)
        self._write_json(work / "fit.json", {"schema_version": SCHEMA_VERSION, "method": p["method"],
                                             "components": component_record,
                                             **({"external": external_record} if external_record else {}),
                                             "folds": folds})
        not_fitted = int((~fitted).sum())
        summary = {"schema_version": SCHEMA_VERSION, "source": p["source"], "source_sha256": p["source_sha256"],
                   "params_identity": value_hash(p), "method_kind": p["method"]["kind"], "rows": int(panel.rows),
                   "rows_by_fold": {str(k): int((panel.fold == k).sum()) for k in np.unique(panel.fold)},
                   "rows_not_fitted": not_fitted,
                   "rows_not_fitted_by_fold": {str(k): int(((panel.fold == k) & ~fitted).sum())
                                               for k in np.unique(panel.fold)},
                   "folds": sorted(int(k) for k in np.unique(panel.fold)), "tables": names,
                   "wall_seconds": round(time.monotonic() - started, 3)}
        self._write_json(work / "summary.json", summary)
        return summary

    def _forecast(self, panel, method):
        """Fit the method per fold and block and return ``(cdf, fitted flags, per-fold record)``."""
        import numpy as np

        spec = self.params["method"]
        rule = LEARNING_RULES[spec["learn"]]
        teach, ready = panel.usable(method.used()), panel.ready(method.used())
        cdf = panel.cdf[self.params["fallback"]].copy()
        fitted_rows = np.zeros(panel.rows, dtype=bool)
        record = {}
        for fold in np.unique(panel.fold):
            blocks = [self._fit_block(panel, method, block, int(fold), teach, ready, cdf, fitted_rows)
                      for block in rule.blocks(panel, fold, spec)]
            record[str(int(fold))] = self._fold_record(rule, spec, blocks)
        return cdf, fitted_rows, record

    def _learning_weights(self, panel, learn, reference):
        """Return ``0.5 ** ((reference - settlement) / half_life_days)`` per learning row, None without a half life."""
        import numpy as np

        half_life = self.params["method"].get("half_life_days")
        if half_life is None:
            return None
        with np.errstate(over="ignore", under="ignore"):
            return 0.5 ** ((reference - panel.settle_days[learn]) / half_life)

    def _fit_block(self, panel, method, block, fold, teach, ready, cdf, fitted_rows):
        """Fit one block from the rows allowed to teach it, forecast its rows into ``cdf`` and describe it.

        A learning row whose weight underflows to zero is dropped (it teaches nothing and would only empty a
        cell's mass); a block left with no learning rows, or that the method declines, keeps the fallback.
        """
        import numpy as np

        learn = np.flatnonzero(block.learn & teach)
        weights = self._learning_weights(panel, learn, block.reference)
        if weights is not None:
            keep = np.isfinite(weights) & (weights > 0)
            learn, weights = learn[keep], weights[keep]
        fit = method.fit(panel, learn, fold, weights, block.tag) if len(learn) else None
        rows = block.target[ready[block.target]] if fit is not None else block.target[:0]
        for start in range(0, len(rows), CHUNK_ROWS):
            part = rows[start:start + CHUNK_ROWS]
            cdf[part] = np.clip(method.predict(panel, part, fit), 0.0, 1.0)
        fitted_rows[rows] = True
        return {"reference": _Dates.iso(block.reference), "learning_rows": int(len(learn)), "fitted": fit is not None,
                "reason": None if fit else ("no learning rows" if not len(learn) else "the method declined to fit"),
                "rows": int(len(block.target)), "rows_fitted": int(len(rows)),
                "state": fit.state if fit else None, "artifacts": fit.artifacts if fit else {}}

    @staticmethod
    def _fold_record(rule, spec, blocks):
        """Return the fold's ``fit.json`` record.

        It carries the first block's fit (the one the fit band uses), the row totals and, for a rule with
        several blocks, the list of blocks (``start`` is a block's first day and its weight reference).
        """
        first = blocks[0]
        record = {"learn": spec["learn"], "learning_rows": first["learning_rows"], "fitted": first["fitted"],
                  "reason": first["reason"], "rows": sum(b["rows"] for b in blocks),
                  "rows_fitted": sum(b["rows_fitted"] for b in blocks), "state": first["state"],
                  "artifacts": {name: text for b in blocks for name, text in b["artifacts"].items()}}
        if "half_life_days" in spec and not rule.LISTS_BLOCKS:
            record["reference"] = first["reference"]
        if rule.LISTS_BLOCKS:
            keys = ("learning_rows", "fitted", "reason", "rows", "rows_fitted", "state")
            record["blocks"] = [{"start": b["reference"], **{k: b[k] for k in keys}} for b in blocks]
        return record

    def _write_tables(self, work, tables):
        """Write the declared tables and the windows table; return the names."""
        directory = work / "tables"
        directory.mkdir()
        names = []
        for spec in self.params["tables"]:
            tables.declared(spec).to_csv(directory / f"{spec['name']}.csv", index=False)
            names.append(spec["name"])
        tables.windows().to_csv(directory / f"{WINDOWS_TABLE}.csv", index=False)
        return [*names, WINDOWS_TABLE]

    def _write_forecasts(self, path, panel, grid, cdf, fitted):
        """Write identity, fold, band, the method CDF columns and the fitted flag as one parquet."""
        import pandas as pd

        roles = self.params["columns"]
        keep = list(dict.fromkeys([*roles["identity"], roles["fold"], roles["band"]]))
        out = pd.concat([panel.frame[keep].reset_index(drop=True),
                         pd.DataFrame(cdf, columns=[f"method_cdf_{label}" for label in grid.labels]),
                         pd.DataFrame({"fitted": fitted})], axis=1)
        out.to_parquet(path, index=False)

    @staticmethod
    def _write_json(path, value):
        path.write_text(json.dumps(_jsonable(value), indent=2, allow_nan=False))
