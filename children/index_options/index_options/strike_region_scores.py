"""Strike-region assessment of saved CDFExperiment fits (ADR-0270, execution path decision).

A fit queue run by :class:`~dskit.pipeline.libs.cdf_experiment.CDFExperiment` leaves, per
(fold, arm, candidate) cell, an immutable checkpoint, paired validation scores and a learning record. This
node reloads those artifacts and scores each cell around the strikes, on BOTH the fit band and the validation
band: the model's mixture, the per-symbol ``horizon_empirical`` baseline refit from that fold's fit rows
exactly as the queue's ``_score`` does, the ``pooled`` standardized empirical (one ``HorizonEmpiricalCDF`` per
fold on the standardized outcomes of ALL admitted symbols' fit rows, conditioned on the study horizon, the
reference column set to one) and the market-implied curve from the panel's ``rn_q_*`` quantiles.
Nothing is fitted and no band logic is reimplemented: the experiment object rebuilds the bands, the imputation
and the one-hot heads (``load_panel``, ``study.plan.calendar``, ``_bands``, ``_arrays``), ``TorchCDF`` restores
the weights, ``ChronologicalCDFStudy.scores`` gives the proper scores, ``DecisionRegionScores`` the listed-strike
Brier and wing score, ``MixtureCurve`` / ``GridCurve`` the curves, ``OptionImpliedTransportCDF._proxy`` the raw
market curve, and ``AtomicFitStore`` verifies every artifact it reads against the cell identity it recomputes.
The validation rows are also re-scored against the shards the queue stored: the restored mixture
``weights``/``means``/``scales`` of every component must match the stored columns (absolute difference at most
``MIXTURE_ATOL``), then model and baseline ``crps`` within ``stored_tolerance``, on equal identity sets; and the
``scores`` directory of a completed cell may hold no shard but its groups', as ``CDFExperiment._resume_state``
requires. Any disagreement refuses. A cell whose arm admits no group is reported as ``not_eligible``, apart
from the refusals (absent or unfinished cells).

Outputs (one new directory, built beside it and published by one rename): ``rows.parquet`` (one row per cell,
band and identity; the fit band is a deterministic stride sample, named by ``band_sampled`` and
``band_rows_population``), ``cells.parquet`` (the protocol's cell families, empty ones with ``rows`` 0: region
Brier scores, global scores and per-threshold reliability, with paired differences and clustered standard
errors), ``curves.json`` (per-epoch losses by seed and the learning signatures) and ``summary.json``
(what was scored, skipped or refused, and the hashes of every input and output).

Reading bound: the node refuses, before it reads the panel, an ``end_before`` earlier than the experiment
document's own read bounds (``date_upper_exclusive``, ``end_upper_exclusive``), which the owner's ``load_panel``
applies as it reads, so no row at or after ``end_before`` is ever decoded; the extra columns are read under the
same bounds.

Documented gaps. Standard errors are clustered by symbol and by quote date (the study's date field, the unit
shared shocks act on) separately and the WIDER is reported as ``<stat>_se`` (NaN unless both have two clusters);
the settlement-date one-way error is kept as a third column (``_se_settlement``) and never decides: two-way
clustering can give a negative variance and is not built. The protocol's ``market_calibrated`` comparison is
produced by the market stage and joined to these cells by identity; only the raw market curve is scored here. The ``pooled`` forecaster pools standardized outcomes by horizon only; it is not the protocol's
transported or calibrated variant. The market curve is the owner's raw
piecewise-linear proxy: between the declared quantiles it interpolates, beyond the outermost ones it adds a
linear tail ``tail_width`` wide (the owner's ``OptionImpliedTransportCDF`` default is 4 in z), so the two tail
regions' market masses and every score reaching past the outermost quantile depend on that completion; no
calibrated or transported market variant is built here. A nested decision-context column cannot sit on a
CDFExperiment panel (its reader refuses nested schemas), so the ``panel_context`` listed-strike source reads a
JSON-text column. Only the pooled regime is read. The checkpoint restores on the device it was saved with.
"""

import json
import math
import os
import re
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
import pandas as pd

from dskit.pipeline.base import value_hash
from dskit.pipeline.libs.cdf_experiment import AtomicFitStore, CDFExperiment, IntegrityError
from dskit.pipeline.libs.predictive_cdf import (
    ChronologicalCDFStudy, DecisionRegionScores, HorizonEmpiricalCDF, OptionImpliedTransportCDF, TorchCDF)
from dskit.pipeline.node import Node, check_int_param, reject_unknown_params

from index_options.cdf_study import _wanted_mask
from index_options.datafiles import DataFiles, archive_relpath, entry_problems
from index_options.theta_exact_expiry import _iso, quotes_valid

__all__ = ["LISTED_SOURCES", "NO_LEARNING_BEST_EPOCH", "StrikeRegionScores"]

#: No node kind is registered: a document names ``index_options.strike_region_scores:StrikeRegionScores``.
NODE_KINDS = ()

#: The output layout version, written into ``summary.json``.
SCHEMA_VERSION = 1
#: The scored forecasters and the paired differences (a, b) reported as ``score_a - score_b``.
FORECASTERS = ("model", "baseline", "pooled", "market")
PAIRS = (("model", "market"), ("model", "baseline"), ("market", "baseline"), ("model", "pooled"),
         ("market", "pooled"))
#: The regime this node reads, and the band names of the experiment's (fit, calibration, validation) triple.
REGIME = "pooled"
BANDS = {0: "fit", 2: "val"}
#: The per-row metrics aggregated beside the region Brier scores, as ``<forecaster>_<name>`` row columns.
GLOBAL_METRICS = ("crps", "tail_crps", "weighted_crps", "decision_strike_brier", "decision_wing_twcrps")
REGION_METRIC = "region_brier"
#: Per grid threshold: mean forecast F(z) (``score_<forecaster>``) beside the realized frequency of ``y <= z``.
RELIABILITY_METRIC = "reliability"
#: How closely the re-scored mixture parameters must reproduce the stored ``weights/means/scales`` columns.
MIXTURE_ATOL = 1e-6
ALL_GROUP, FULL_WINDOW = "all", "full"
#: The quantile levels whose spread is the sharpness column ``q95_q05``.
SHARPNESS_LEVELS = (0.05, 0.95)
#: How the panel's market quantiles are expressed: already in z, or log returns to divide by the scale.
MARKET_UNITS = ("z", "log_return")
#: The no-learning rule: a seed whose best epoch (1-based) is at most this learned nothing before it was stopped.
NO_LEARNING_BEST_EPOCH = 1
#: Rows of an archive file decoded and filtered at a time.
ARCHIVE_BATCH_ROWS = 250_000
#: Region labels are two digits, so the grid may hold at most this many points.
MAX_GRID = 98
_YEAR_WINDOW = re.compile(r"^y\d{4}$")

RULES = {
    "no_learning": (
        "per seed, with the monitor recorded: best_epoch <= 1 (the first epoch was the best), OR the monitor "
        "loss at the best epoch >= the epoch-zero monitor loss when training telemetry recorded it; the cell "
        "flag is true when any seed is, null when no seed recorded a best epoch (no patience monitor)"),
    "overfit": (
        "per seed: the best epoch is before the last epoch AND the training loss at the last epoch is below "
        "its value at the best epoch AND the monitor loss at the last epoch is above its value at the best "
        "epoch; the cell flag is true when any seed is, null when no seed recorded a monitor"),
    "epochs": "best_epoch and every list index are 1-based epochs; monitor_loss[i] is the loss after epoch i + 1",
}


def _zlabel(z):
    """Return the column label of one grid point: ``m`` or ``p`` and the magnitude (``m2.5``, ``p0``)."""
    return ("m" if z < 0 else "p") + format(abs(z), "g")


def _number(value):
    """Say whether ``value`` is a real number that is not a bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _clean(value):
    """Return ``value`` with NaN and infinity replaced by None and numpy scalars by Python ones, for JSON."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class _ClusteredMean:
    """Mean and cluster-robust standard error of columns of scores, NaN meaning not available.

    The error is the ratio-of-totals linearization of :func:`dskit.pipeline.stats.cluster_bootstrap_t`
    (``sqrt(g / (g - 1) * sum_j (T_j - mean * m_j)^2) / n`` over the ``g`` clusters holding a value), written
    over whole matrices because a cell table asks for many thousand of them; a test pins the agreement with
    the toolkit's. Fewer than two clusters give NaN.
    """

    @staticmethod
    def estimate(values, codes):
        """Return ``(mean, n, se)`` per column of ``values``, clustered by the integer ``codes``.

        Parameters
        ----------
        values : numpy.ndarray
            ``(rows, columns)`` scores; NaN is missing.
        codes : numpy.ndarray
            One integer cluster code per row.

        Returns
        -------
        tuple of numpy.ndarray
            The mean, the count of values and the standard error of the mean, each of length ``columns``.
        """
        values = np.asarray(values, dtype=float)
        codes = np.asarray(codes)
        columns = values.shape[1]
        if not len(values):
            nan = np.full(columns, np.nan)
            return nan, np.zeros(columns, dtype=int), nan.copy()
        valid = np.isfinite(values)
        filled = np.where(valid, values, 0.0)
        n = valid.sum(0)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = np.where(n > 0, filled.sum(0) / np.maximum(n, 1), np.nan)
            order = np.argsort(codes, kind="stable")
            starts = np.r_[0, np.flatnonzero(np.diff(codes[order])) + 1]
            totals = np.add.reduceat(filled[order], starts, axis=0)
            sizes = np.add.reduceat(valid[order].astype(float), starts, axis=0)
            occupied = sizes > 0
            g = occupied.sum(0)
            ss = np.where(occupied, (totals - mean * sizes) ** 2, 0.0).sum(0)
            se = np.where(g >= 2, np.sqrt(g / np.maximum(g - 1, 1) * ss) / np.maximum(n, 1), np.nan)
        return mean, n, se


class _LearningCurves:
    """The learning record of one checkpoint and its no-learning and overfit signatures.

    Parameters
    ----------
    state : dict
        The ``state.json`` of a TorchCDF checkpoint: ``settings.seeds``, ``losses``, ``monitor_losses``,
        ``best_epochs`` and ``diagnostics``. ``monitor_losses`` and ``best_epochs`` are empty when the fit
        declared no patience, and the signatures are then null. See :data:`RULES` for the numeric rules.

    Examples
    --------
    Flag a seed whose first epoch was its best::

        curves = _LearningCurves({"settings": {"seeds": [7]}, "losses": [[3, 2.9]],
                                  "monitor_losses": [[1.0, 1.1]], "best_epochs": [1],
                                  "diagnostics": [{"epoch_zero_monitor_loss": 1.5}]})
        curves.signatures()
        # -> {"no_learning": True, "overfit": True}
    """

    def __init__(self, state):
        self.state = state

    def _pick(self, key, i):
        """Return the ``i``-th entry of a per-seed list of the state, None when it was not recorded."""
        found = self.state.get(key, [])
        return found[i] if i < len(found) else None

    def _seed(self, i, seed):
        monitor = self._pick("monitor_losses", i) or []
        diagnostics = self._pick("diagnostics", i) or {}
        return {"seed": seed, "train_loss": list(self._pick("losses", i) or []), "monitor_loss": list(monitor),
                "best_epoch": self._pick("best_epochs", i),
                "epoch_zero_monitor_loss": diagnostics.get("epoch_zero_monitor_loss"),
                "parameter_delta_l2": diagnostics.get("parameter_delta_l2")}

    @staticmethod
    def _no_learning(seed):
        best, monitor, zero = seed["best_epoch"], seed["monitor_loss"], seed["epoch_zero_monitor_loss"]
        if best is None:
            return None
        return bool(best <= NO_LEARNING_BEST_EPOCH
                    or (zero is not None and len(monitor) >= best and monitor[best - 1] >= zero))

    @staticmethod
    def _overfit(seed):
        best, monitor, train = seed["best_epoch"], seed["monitor_loss"], seed["train_loss"]
        if best is None or not monitor or len(train) < len(monitor):
            return None
        last = len(monitor)
        return bool(best < last and train[last - 1] < train[best - 1] and monitor[last - 1] > monitor[best - 1])

    def record(self):
        """Return the per-seed curves and the cell-level signatures.

        Returns
        -------
        dict
            ``seeds`` (``seed``, ``train_loss``, ``monitor_loss``, ``best_epoch``,
            ``epoch_zero_monitor_loss``, ``parameter_delta_l2``) and ``signatures``.
        """
        seeds = [self._seed(i, seed) for i, seed in enumerate(self.state["settings"]["seeds"])]
        flags = {"no_learning": [self._no_learning(s) for s in seeds],
                 "overfit": [self._overfit(s) for s in seeds]}
        signatures = {}
        for name, found in flags.items():
            found = [f for f in found if f is not None]
            signatures[name] = any(found) if found else None
        return {"seeds": seeds, "signatures": signatures}

    def signatures(self):
        """Return ``{"no_learning": ..., "overfit": ...}``, each true, false or null (not evaluable)."""
        return self.record()["signatures"]


class _ListedStrikes(ABC):
    """Where a row's listed strikes come from, as decision contexts in the row's standardized units.

    A subclass names its source, its parameters and the extra panel columns it reads, and answers
    :meth:`contexts`: one ``{identity, thresholds, weights, intervals}`` mapping per row, empty thresholds
    meaning the row has no usable listed strikes.
    """

    SOURCE = ""
    #: Every key the ``listed_strikes`` mapping holds for this source (all are required).
    KEYS = ()

    def __init__(self, params):
        self.params = params
        self.sources = {}

    @classmethod
    def problems(cls, params):
        """Return the problems of a ``listed_strikes`` mapping for this source."""
        problems = []
        missing = sorted(set(cls.KEYS) - set(params))
        extra = sorted(set(params) - set(cls.KEYS))
        if missing:
            problems.append(f"listed_strikes ({cls.SOURCE}) lacks {missing}")
        if extra:
            problems.append(f"listed_strikes ({cls.SOURCE}) has unknown keys {extra}")
        return problems

    @abstractmethod
    def columns(self):
        """Return the extra panel columns this source reads."""

    @abstractmethod
    def contexts(self, frame):
        """Return one decision context per row of ``frame`` (identity, thresholds, weights, intervals)."""

    @staticmethod
    def empty(identity):
        """Return the context of a row without usable listed strikes."""
        return {"identity": identity, "thresholds": [], "weights": [], "intervals": []}


class _ArchiveStrikes(_ListedStrikes):
    """Listed strikes from the chain archive: strikes with a valid quote on either side, in z.

    The per-year file of each symbol is read batch by batch (:data:`ARCHIVE_BATCH_ROWS` rows) and every batch is
    cut to the wanted (date, expiration) identities at once: rows outside them are decoded and discarded, and
    only wanted identities (all before ``end_before``) are ever returned or kept.

    Parameters
    ----------
    params : dict
        ``source`` ("archive"), ``archive_root`` (a directory or a store reference), ``data_root`` (the store
        root, or None for a legacy directory) and ``min_valid_strikes`` (fewer valid strikes leave the row
        without a context).

    Examples
    --------
    Read the strikes of a few identities::

        source = _ArchiveStrikes({"source": "archive", "archive_root": "/data/archive",
                                  "data_root": None, "min_valid_strikes": 2})
        source.contexts(frame)   # frame has symbol, quote_date, expiry, spot and reference_scale
    """

    SOURCE = "archive"
    KEYS = ("source", "archive_root", "data_root", "min_valid_strikes")
    ARCHIVE_COLUMNS = ("date", "expiration", "strike", "bid", "ask")

    @classmethod
    def problems(cls, params):
        """Return the missing, unknown and invalid archive keys."""
        problems = super().problems(params)
        if "archive_root" in params:
            problems += entry_problems("listed_strikes.archive_root", params["archive_root"], tree=True)
        root = params.get("data_root", "")
        if root is not None and not (isinstance(root, str) and root):
            problems.append("listed_strikes.data_root must be a non-empty string or null")
        if "min_valid_strikes" in params:
            check_int_param(problems, "listed_strikes.min_valid_strikes", params["min_valid_strikes"], ge=1)
        return problems

    def __init__(self, params):
        super().__init__(params)
        self.tree = DataFiles(params["data_root"]).tree(params["archive_root"])
        self.strikes = {}

    def columns(self):
        """Return the panel column the z conversion needs: the spot."""
        return ["spot"]

    def _read(self, frame):
        """Add the valid-quote strikes of every identity of ``frame`` not yet seen to the cache."""
        import pyarrow.parquet as pq

        todo = frame[[(s, d, e) not in self.strikes for s, d, e in
                      zip(frame.symbol, frame.quote_date, frame.expiry)]]
        for (symbol, year), part in todo.groupby(["symbol", todo.quote_date.str[:4]], sort=True):
            relpath = archive_relpath(symbol, year)
            wanted = set(zip(part.quote_date, part.expiry))
            found = {}
            for batch in pq.ParquetFile(self.tree.path(relpath)).iter_batches(
                    columns=list(self.ARCHIVE_COLUMNS), batch_size=ARCHIVE_BATCH_ROWS, use_threads=False):
                chain = batch.to_pandas()
                chain = chain.loc[_wanted_mask(chain, wanted)]
                good = chain.loc[quotes_valid(chain.bid.to_numpy(float), chain.ask.to_numpy(float))]
                for key, strikes in good.groupby(["date", "expiration"], sort=False).strike:
                    found.setdefault(key, set()).update(strikes.tolist())
            self.sources[self.tree.label(relpath)] = self.tree.sha256(relpath)
            for date, expiry in wanted:
                self.strikes[(symbol, date, expiry)] = np.array(sorted(found.get((date, expiry), ())), float)

    def contexts(self, frame):
        """Return equal-weight contexts at the valid strikes, with adjacent pairs not straddling zero as wings."""
        self._read(frame)
        minimum = self.params["min_valid_strikes"]
        out = []
        for symbol, date, expiry, spot, scale in zip(frame.symbol, frame.quote_date, frame.expiry,
                                                     frame.spot, frame.reference_scale):
            identity = (symbol, date, expiry)
            strikes = self.strikes[identity]
            z = np.unique(np.log(strikes[strikes > 0] / spot) / scale)
            z = z[np.isfinite(z)]
            if len(z) < minimum:
                out.append(self.empty(identity))
                continue
            pairs = [[float(a), float(b)] for a, b in zip(z[:-1], z[1:]) if not a < 0 < b]
            out.append({"identity": identity, "thresholds": z.tolist(), "weights": [1.0 / len(z)] * len(z),
                        "intervals": pairs})
        return out


class _ContextStrikes(_ListedStrikes):
    """Listed strikes from a decision-context column holding one JSON text record per row.

    Parameters
    ----------
    params : dict
        ``source`` ("panel_context") and ``context_field``: the column; each value is a JSON object with
        ``identity``, ``thresholds``, ``weights`` and ``intervals`` as the decision-region builder writes them.

    Examples
    --------
    Read the contexts of a frame::

        source = _ContextStrikes({"source": "panel_context", "context_field": "decision_context"})
        source.contexts(frame)
    """

    SOURCE = "panel_context"
    KEYS = ("source", "context_field")

    @classmethod
    def problems(cls, params):
        """Return the missing, unknown and invalid context keys."""
        problems = super().problems(params)
        field = params.get("context_field", "x")
        if not isinstance(field, str) or not field:
            problems.append("listed_strikes.context_field must be a non-empty string")
        return problems

    def columns(self):
        """Return the context column."""
        return [self.params["context_field"]]

    def contexts(self, frame):
        """Return the stored records, each checked against its row's identity."""
        out = []
        for symbol, date, expiry, text in zip(frame.symbol, frame.quote_date, frame.expiry,
                                              frame[self.params["context_field"]]):
            identity = (symbol, date, expiry)
            record = json.loads(text)
            if tuple(record["identity"]) != identity:
                raise IntegrityError(f"decision context identity {record['identity']} is not the row {identity}")
            out.append({"identity": identity, "thresholds": [float(v) for v in record["thresholds"]],
                        "weights": [float(v) for v in record["weights"]],
                        "intervals": [[float(a), float(b)] for a, b in record["intervals"]]})
        return out


#: Listed-strike sources by their ``source`` name; a new source is a subclass registered here.
LISTED_SOURCES = {cls.SOURCE: cls for cls in (_ArchiveStrikes, _ContextStrikes)}


class _MarketCurves:
    """The raw market-implied curve of each eligible row, from its panel quantiles.

    Parameters
    ----------
    params : dict
        ``probabilities``, ``prefix`` (the quantile column prefix), ``eligible_field``, ``units`` ("z" when
        the columns are already standardized, as the exact-expiry panel writes them, or "log_return" to divide
        by the reference scale) and ``tail_width`` (the linear completion beyond the outermost quantiles).

    Examples
    --------
    Curves for the eligible rows of a frame::

        market = _MarketCurves({"probabilities": [0.05, 0.5, 0.95], "prefix": "rn_q_",
                                "eligible_field": "rn_proxy_eligible", "units": "z", "tail_width": 4.0})
        eligible, curve = market.curve(frame)
    """

    def __init__(self, params):
        self.params = params
        self.names = self.column_names(params)
        n = len(self.names)
        # The owner's raw proxy builder: its constructor validates the probabilities, its _proxy the curve.
        self.owner = OptionImpliedTransportCDF(
            list(range(n)), params["probabilities"], n, [n + 1], n + 2, tail_width=params["tail_width"])

    @staticmethod
    def column_names(params):
        """Return the quantile column names, as the chain builder spells them."""
        return [f"{params['prefix']}{int(round(10000 * p)):04d}" for p in params["probabilities"]]

    def columns(self):
        """Return the panel columns this reads."""
        return [self.params["eligible_field"], *self.names]

    def curve(self, frame, scale):
        """Return ``(eligible mask, GridCurve over the eligible rows or None)``."""
        flag = frame[self.params["eligible_field"]].to_numpy(float)
        if not np.isin(flag, (0, 1)).all():
            raise ValueError(f"{self.params['eligible_field']} must be binary (0 or 1) on every row")
        eligible = flag == 1
        if not eligible.any():
            return eligible, None
        q = frame[self.names].to_numpy(float)[eligible]
        if not np.isfinite(q).all():
            raise ValueError("active market quantiles must be finite")
        if (np.diff(q, axis=1) < 0).any():
            raise ValueError("active market quantiles must be ordered")
        if self.params["units"] == "log_return":
            q = q / np.asarray(scale, float)[eligible, None]
        return eligible, self.owner._proxy(q)


class _DecisionScorer:
    """Listed-strike Brier and wing score through DecisionRegionScores; rows without a valid wing get NaN."""

    @staticmethod
    def wing_context(context):
        """Return ``context``, or an empty one when its intervals cannot be scored as wings."""
        thresholds = np.asarray(context["thresholds"], float)
        pairs = np.asarray(context["intervals"], float).reshape(-1, 2)
        if (len(pairs) and np.isin(pairs, thresholds).all() and (pairs[:, 0] < pairs[:, 1]).all()
                and not ((pairs[:, 0] < 0) & (pairs[:, 1] > 0)).any()):
            return context
        return {**context, "thresholds": [], "weights": [], "intervals": []}

    def score(self, curve, y, contexts):
        """Return ``(strike Brier, wing twCRPS)`` per row."""
        strike = DecisionRegionScores(contexts).score(curve, y)["decision_strike_brier"]
        wings = DecisionRegionScores([self.wing_context(c) for c in contexts]).wing_score(curve, y)
        return strike, wings["decision_wing_twcrps"]


class _ForecastScorer:
    """Every per-row score of one forecaster's curve: proper scores, grid Brier, masses, sharpness, decisions."""

    def __init__(self, study_config, z_grid):
        self.config = study_config
        self.z = np.asarray(z_grid, float)
        self.labels = [_zlabel(z) for z in z_grid]
        self.decisions = _DecisionScorer()

    def names(self):
        """Return the score names (row columns are ``<forecaster>_<name>``), in order."""
        return (["crps", "tail_crps", "weighted_crps", "pit", "below_05", "above_95", "sd_z", "q95_q05",
                 "decision_strike_brier", "decision_wing_twcrps"]
                + [f"cdf_z{label}" for label in self.labels]
                + [f"brier_z{label}" for label in self.labels]
                + [f"mass_r{r:02d}" for r in range(len(self.z) + 1)])

    def score(self, curve, y, contexts):
        """Return ``{name: array}`` for one curve against outcomes ``y`` (standardized) and their contexts."""
        s = self.config
        values, _ = ChronologicalCDFStudy.scores(curve, y, s["samples"], s["tail_intervals"], s["tail_points"])
        out = {"crps": values["crps"], "tail_crps": values["tail_crps"],
               "weighted_crps": values["crps"] + s["tail_weight"] * values["tail_crps"],
               "pit": values["pit"], "below_05": values["below_05"], "above_95": values["above_95"]}
        out["sd_z"] = curve.moments()[1]
        low, high = curve.quantile(list(SHARPNESS_LEVELS)).T
        out["q95_q05"] = high - low
        out["decision_strike_brier"], out["decision_wing_twcrps"] = self.decisions.score(curve, y, contexts)
        cdf = curve.cdf(self.z)
        error = cdf - (y[:, None] <= self.z)
        for i, label in enumerate(self.labels):
            out[f"cdf_z{label}"] = cdf[:, i]
            out[f"brier_z{label}"] = error[:, i] ** 2
        mass = np.diff(np.column_stack([np.zeros(len(y)), cdf, np.ones(len(y))]), axis=1)
        for r in range(mass.shape[1]):
            out[f"mass_r{r:02d}"] = mass[:, r]
        return out


class _CellTable:
    """The protocol's cell families over one cell's rows of a band.

    Cells are (window) x (DTE slice) x (ticker group) x (metric); an empty cell is emitted with ``rows`` 0.
    The metrics are the region Brier score of each of the ``len(z_grid) + 1`` regions, the
    :data:`GLOBAL_METRICS` (region -1) and the ``reliability`` of each grid threshold (region = threshold
    index, ``threshold`` = z): there ``score_<forecaster>`` is the mean forecast F(z). Each cell carries
    support, the mean score per forecaster, the paired differences (:data:`PAIRS`) and the realized frequency
    taken on each forecaster's OWN rows (``realized_freq_<forecaster>``; the market's are its eligible rows,
    as its mass and score are); every one with its count and the symbol-, quote-date- and
    settlement-clustered and reported standard errors. The reported error is NaN unless BOTH clusterings have at least two clusters:
    it never falls back to the other.

    Parameters
    ----------
    params : dict
        The node's ``dte_slices``, ``sub_periods`` and optional ``ticker_groups``.
    fields : dict
        The study's column names (``group``, ``date``, ``end``, ``horizon``, ``identity``).
    regions : int
        The number of regions, ``len(z_grid) + 1``.
    z_grid : list of float
        The region edges and reliability thresholds.

    Examples
    --------
    Aggregate one band's rows::

        table = _CellTable(params, fields, 14, z_grid)
        cells = table.aggregate(rows, {"arm": "base", "candidate": "mlp", "fold": 1, "band": "val"})
    """

    def __init__(self, params, fields, regions, z_grid):
        self.params, self.f, self.regions, self.z = params, fields, regions, list(z_grid)
        self.metrics = ([(REGION_METRIC, r) for r in range(regions)] + [(m, -1) for m in GLOBAL_METRICS]
                        + [(RELIABILITY_METRIC, k) for k in range(len(self.z))])

    def _scores(self, rows):
        """Return ``(scores, frequencies, realized)``: per-forecaster (rows, metrics) matrices, NaN where absent."""
        hit = (rows["y"].to_numpy(float)[:, None] <= np.asarray(self.z)).astype(float)
        realized = rows[[f"realized_r{r:02d}" for r in range(self.regions)]].to_numpy(float)
        scores, freqs = {}, {}
        for who in FORECASTERS:
            mass = rows[[f"{who}_mass_r{r:02d}" for r in range(self.regions)]].to_numpy(float)
            glob = rows[[f"{who}_{m}" for m in GLOBAL_METRICS]].to_numpy(float)
            cdf = rows[[f"{who}_cdf_z{_zlabel(z)}" for z in self.z]].to_numpy(float)
            scores[who] = np.column_stack([(mass - realized) ** 2, glob, cdf])
            freq = np.column_stack([realized, np.full((len(rows), len(GLOBAL_METRICS)), np.nan), hit])
            freq[~rows[f"{who}_crps"].notna().to_numpy()] = np.nan
            freqs[who] = freq
        return scores, freqs, realized

    def _windows(self, rows):
        date = rows[self.f["date"]]
        windows = [(FULL_WINDOW, FULL_WINDOW, np.ones(len(rows), dtype=bool))]
        for name, (start, end) in self.params["sub_periods"].items():
            windows.append(("sub_period", name, ((date >= start) & (date <= end)).to_numpy()))
        for year in sorted(date.str[:4].unique()):
            windows.append(("year", f"y{year}", (date.str[:4] == year).to_numpy()))
        return windows

    def _groups(self, rows):
        groups = [(ALL_GROUP, np.ones(len(rows), dtype=bool))]
        mapping = self.params.get("ticker_groups")
        if mapping:
            labelled = rows[self.f["group"]].map(mapping)
            groups += [(g, (labelled == g).to_numpy()) for g in sorted(set(mapping.values()))]
        return groups

    def aggregate(self, rows, ids):
        """Return the cell table of ``rows`` (one cell, one band), labelled with ``ids``."""
        if rows.empty:
            return pd.DataFrame()
        f = self.f
        scores, freqs, realized = self._scores(rows)
        codes = {"symbol": pd.factorize(rows[f["group"]])[0], "quote_date": pd.factorize(rows[f["date"]])[0],
                 "settlement": pd.factorize(rows[f["end"]])[0]}
        stats = {f"score_{who}": scores[who] for who in FORECASTERS}
        for a, b in PAIRS:
            stats[f"diff_{a}_{b}"] = scores[a] - scores[b]
        stats.update({f"realized_freq_{who}": freqs[who] for who in FORECASTERS})
        dte = rows[f["horizon"]].to_numpy(float)
        frames = []
        for kind, window, wmask in self._windows(rows):
            for lo, hi in self.params["dte_slices"]:
                smask = (dte >= lo) & (dte <= hi)
                for group, gmask in self._groups(rows):
                    idx = np.flatnonzero(wmask & smask & gmask)
                    frames.append(self._cell(rows, idx, stats, realized, codes,
                                             {**ids, "window_kind": kind, "window": window,
                                              "dte_slice": f"{lo}-{hi}", "ticker_group": group}))
        return pd.concat(frames, ignore_index=True)

    def _cell(self, rows, idx, stats, realized, codes, ids):
        f, m, regions = self.f, len(self.metrics), self.regions
        part = rows.iloc[idx]
        pad = lambda values: list(values) + [np.nan] * (m - len(values))  # noqa: E731
        edge = lambda name, r, shift: (  # noqa: E731
            self.z[r + shift] if name == REGION_METRIC and 0 <= r + shift < len(self.z) else np.nan)
        table = {**{k: [v] * m for k, v in ids.items()},
                 "metric": [name for name, _ in self.metrics], "region": [r for _, r in self.metrics],
                 "region_low": [edge(name, r, -1) for name, r in self.metrics],
                 "region_high": [edge(name, r, 0) for name, r in self.metrics],
                 "threshold": [self.z[r] if name == RELIABILITY_METRIC else np.nan for name, r in self.metrics],
                 "rows": [len(idx)] * m, "dates": [part[f["date"]].nunique()] * m,
                 "expiries": [part[f["identity"][2]].nunique()] * m,
                 "symbols": [part[f["group"]].nunique()] * m,
                 "events": pad(realized[idx].sum(0))}
        for who in FORECASTERS:
            mass = part[[f"{who}_mass_r{r:02d}" for r in range(regions)]].to_numpy(float)
            table[f"mass_{who}"] = pad(_ClusteredMean.estimate(mass, np.zeros(len(mass), dtype=int))[0])
        for name, matrix in stats.items():
            block = matrix[idx]
            mean, n, _ = _ClusteredMean.estimate(block, codes["symbol"][idx])
            table[name], table[f"{name}_n"] = mean, n
            for key, se in self.errors(block, {k: v[idx] for k, v in codes.items()}).items():
                table[f"{name}_{key}"] = se
        return pd.DataFrame(table)

    @staticmethod
    def errors(block, codes):
        """Return the standard errors of the column means of ``block`` under each clustering.

        Parameters
        ----------
        block : numpy.ndarray
            ``(rows, columns)`` scores, NaN missing.
        codes : dict
            Integer cluster codes per row under ``symbol``, ``quote_date`` and ``settlement``.

        Returns
        -------
        dict
            ``se_symbol``, ``se_quote_date`` and ``se_settlement`` (one-way, each NaN under two clusters) and
            ``se``: the wider of the symbol and quote-date errors, NaN unless both are defined. The quote date,
            the study's date field, is the unit shared shocks act on; settlement dates are reported as a third
            view and never decide the reported error.
        """
        out = {f"se_{key}": _ClusteredMean.estimate(block, codes[key])[2]
               for key in ("symbol", "quote_date", "settlement")}
        both = np.isfinite(out["se_symbol"]) & np.isfinite(out["se_quote_date"])
        out["se"] = np.where(both, np.fmax(out["se_symbol"], out["se_quote_date"]), np.nan)
        return out


class _Experiment:
    """The experiment document, its pre-holdout frame and the node's extra panel columns, loaded once.

    Everything about bands, imputation and heads is the experiment object's: this class only constructs it,
    bounds what it reads, and joins the extra columns (spot, market quantiles, contexts) on the identity.
    """

    def __init__(self, params, extra_columns):
        path = Path(params["experiment_document"])
        document = json.loads(path.read_text())
        if Path(document["panel"]).resolve() != Path(params["panel"]).resolve():
            raise ValueError(f"panel {params['panel']} is not the experiment document's panel {document['panel']}")
        self.document_sha = AtomicFitStore.file_hash(path)
        self.exp = CDFExperiment(document)
        self.config, self.study_config = document, document["study"]
        holdout = self.study_config["fold_table"]["holdout_start"]
        self.end_before = params["end_before"]
        if self.end_before > holdout:
            raise ValueError(f"end_before {self.end_before} is after the document's holdout_start {holdout}: "
                             "the holdout is never read")
        reads = max(document["date_upper_exclusive"], document["end_upper_exclusive"])
        if self.end_before < reads:
            raise ValueError(f"end_before {self.end_before} is earlier than the experiment's own read bound {reads}: "
                             "its fits saw rows this node must not decode, and truncating them would change the bands")
        s = self.study_config
        if len(s["identity"]) != 3 or s["group"] != s["identity"][0]:
            raise ValueError("the study identity must be (group, date, expiry) with the group first")
        self.fields = {k: s[k] for k in ("group", "date", "end", "horizon", "target", "reference", "identity")}
        self._check_columns(extra_columns)
        self.frame = self.exp.load_panel()
        self._check_bound()
        self.extras = self._read_extras(extra_columns)
        self.calendar = self.exp.study.plan.calendar(self.frame)

    def _check_columns(self, extra):
        import pyarrow.parquet as pq

        have = set(pq.ParquetFile(self.config["panel"]).schema_arrow.names)
        missing = sorted(set(extra) - have)
        if missing:
            raise ValueError(f"the panel lacks column(s) {missing}: refusing before any cell is read")

    def _check_bound(self):
        f = self.fields
        late = self.frame[(self.frame[f["date"]] >= self.end_before) | (self.frame[f["end"]] >= self.end_before)]
        if len(late):
            raise ValueError(f"{len(late)} panel row(s) are dated or settle at or after end_before "
                             f"{self.end_before}: the screen reads none of them")

    @staticmethod
    def read_bounds(fields, config, end_before):
        """Return the ``DateBoundedParquet`` bounds: the document's, lowered to ``end_before``.

        One column serving as both the date and the end field takes the smaller of the two upper bounds.
        """
        date, end = fields["date"], fields["end"]
        if date == end:
            upper = {date: min(config["date_upper_exclusive"], config["end_upper_exclusive"])}
        else:
            upper = {date: config["date_upper_exclusive"], end: config["end_upper_exclusive"]}
        return {k: {"end_before": min(v, end_before)} for k, v in upper.items()}

    def _read_extras(self, extra):
        from dskit.pipeline.libs.parquet import DateBoundedParquet

        c, f = self.config, self.fields
        bounds = self.read_bounds(f, c, self.end_before)
        columns = list(dict.fromkeys([*f["identity"], f["date"], f["end"], *extra]))
        frame = DateBoundedParquet(c["panel"], columns, bounds).read().to_pandas()
        if frame.duplicated(f["identity"]).any():
            raise IntegrityError("duplicate panel identities")
        return frame.drop(columns=[k for k in (f["date"], f["end"]) if k not in f["identity"] and k not in extra])

    def attach(self, band, columns):
        """Return ``band`` with ``columns`` joined on the identity, row order kept; a miss is an error."""
        keys = self.fields["identity"]
        joined = band[keys].merge(self.extras[[*keys, *columns]], on=keys, how="left", validate="one_to_one",
                                  indicator=True)
        if (joined.pop("_merge") != "both").any():
            raise IntegrityError("the panel extras do not cover the band identities")
        return joined.reset_index(drop=True)


class StrikeRegionScores(Node):
    """Score saved CDFExperiment fits around the listed strikes, on fit and validation rows.

    Role ``report``; no inputs. See the module docstring for what is reused and the documented gaps. Outputs
    ``summary``: the counts the run printed (the files hold the rest).

    Parameters
    ----------
    params : dict
        All required but ``ticker_groups``. ``experiment_document`` (path of the CDFExperiment document),
        ``panel`` (its panel, which must be the document's), ``output`` (a new directory), ``z_grid`` (strictly
        increasing region edges; the threshold Brier grid and ``len + 1`` regions: ``len - 1`` inner bins and
        two tails), ``dte_slices`` (``[[lo, hi], ...]`` inclusive calendar days, disjoint),
        ``sub_periods`` (``{name: [start, end]}`` over the quote date), ``ticker_groups`` (``{symbol: group}``,
        optional), ``market`` (``probabilities``, ``prefix``, ``eligible_field``, ``units``, ``tail_width``),
        ``listed_strikes`` (``{"source": "archive", "archive_root", "data_root", "min_valid_strikes"}`` or
        ``{"source": "panel_context", "context_field"}``), ``fit_band_sample`` (most fit rows scored per cell,
        by a deterministic stride, or null for all), ``end_before`` (ISO date: not earlier than the document's own
        read bounds and not after its ``holdout_start``, so nothing at or after it is decoded), ``chunk_rows`` (rows
        scored at a time, bounding memory) and ``stored_tolerance`` (how closely the validation rows must
        reproduce the shards the queue stored).

    Examples
    --------
    Score a finished queue::

        node = StrikeRegionScores("scores", params)
        out = node.run(ctx, {})
        out["summary"]["output"]
    """

    role = "report"
    outputs = ("summary",)
    _PARAMS = ("experiment_document", "panel", "output", "z_grid", "dte_slices", "sub_periods",
               "ticker_groups", "market", "listed_strikes", "fit_band_sample", "end_before", "chunk_rows",
               "stored_tolerance")
    _MARKET_KEYS = ("probabilities", "prefix", "eligible_field", "units", "tail_width")

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none.

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
        missing = sorted(set(cls._PARAMS) - {"ticker_groups"} - set(params))
        if missing:
            problems.append(f"missing parameters: {missing}")
        for name in ("experiment_document", "panel", "output"):
            if name in params and not (isinstance(params[name], str) and params[name]):
                problems.append(f"{name} must be a non-empty string")
        if "z_grid" in params:
            cls._z_problems(problems, params["z_grid"])
        if "dte_slices" in params:
            cls._slice_problems(problems, params["dte_slices"])
        if "sub_periods" in params:
            cls._period_problems(problems, params["sub_periods"])
        if "ticker_groups" in params:
            cls._group_problems(problems, params["ticker_groups"])
        if "market" in params:
            cls._market_problems(problems, params["market"])
        if "listed_strikes" in params:
            source = params["listed_strikes"]
            owner = LISTED_SOURCES.get(source.get("source")) if isinstance(source, dict) else None
            if owner is None:
                problems.append(f"listed_strikes needs a mapping whose source is one of {sorted(LISTED_SOURCES)}")
            else:
                problems += owner.problems(source)
        sample = params.get("fit_band_sample", 1)
        if sample is not None:
            check_int_param(problems, "fit_band_sample", sample, ge=1)
        if "end_before" in params and _iso(params["end_before"]) is None:
            problems.append(f"end_before must be an ISO date text YYYY-MM-DD, got {params['end_before']!r}")
        if "chunk_rows" in params:
            check_int_param(problems, "chunk_rows", params["chunk_rows"], ge=1)
        if "stored_tolerance" in params:
            tol = params["stored_tolerance"]
            if not (_number(tol) and math.isfinite(tol) and tol > 0):
                problems.append(f"stored_tolerance must be a finite number above zero, got {tol!r}")
        return problems

    @staticmethod
    def _z_problems(problems, grid):
        if not (isinstance(grid, list) and 2 <= len(grid) <= MAX_GRID and all(_number(z) and math.isfinite(z) for z in grid)
                and all(a < b for a, b in zip(grid, grid[1:]))):
            problems.append(f"z_grid must be 2 to {MAX_GRID} strictly increasing finite numbers")
        elif len({_zlabel(z) for z in grid}) != len(grid):
            problems.append("z_grid points collide in their column labels")

    @staticmethod
    def _slice_problems(problems, slices):
        ok = (isinstance(slices, list) and slices and all(
            isinstance(s, list) and len(s) == 2 and all(type(v) is int for v in s) and 1 <= s[0] <= s[1]
            for s in slices))
        if not ok:
            problems.append("dte_slices must be a non-empty list of [lo, hi] integers with 1 <= lo <= hi")
        elif any(b[0] <= a[1] for a, b in zip(sorted(slices), sorted(slices)[1:])):
            problems.append("dte_slices overlap")

    @staticmethod
    def _period_problems(problems, periods):
        if not isinstance(periods, dict):
            problems.append("sub_periods must be a mapping of name to [start, end]")
            return
        for name, span in periods.items():
            ok = (isinstance(name, str) and name and name != FULL_WINDOW and not _YEAR_WINDOW.match(name)
                  and isinstance(span, list) and len(span) == 2 and all(_iso(v) for v in span) and span[0] <= span[1])
            if not ok:
                problems.append(f"sub_periods[{name!r}] must be [start, end] ISO dates (start <= end) under a "
                                f"name that is not {FULL_WINDOW!r} or a year window")

    @staticmethod
    def _group_problems(problems, groups):
        if not (isinstance(groups, dict) and all(isinstance(k, str) and k and isinstance(v, str) and v
                                                 and v != ALL_GROUP for k, v in groups.items())):
            problems.append(f"ticker_groups must map symbols to non-empty group names other than {ALL_GROUP!r}")

    @classmethod
    def _market_problems(cls, problems, market):
        keys = cls._MARKET_KEYS
        if not (isinstance(market, dict) and set(market) == set(keys)):
            problems.append(f"market must hold exactly {list(keys)}")
            return
        p = market["probabilities"]
        if not (isinstance(p, list) and len(p) >= 2 and all(_number(v) and 0 < v < 1 for v in p)
                and all(a < b for a, b in zip(p, p[1:]))):
            problems.append("market.probabilities must be at least two strictly increasing numbers in (0, 1)")
        for name in ("prefix", "eligible_field"):
            if not (isinstance(market[name], str) and market[name]):
                problems.append(f"market.{name} must be a non-empty string")
        if market["units"] not in MARKET_UNITS:
            problems.append(f"market.units must be one of {list(MARKET_UNITS)}, got {market['units']!r}")
        if not (_number(market["tail_width"]) and math.isfinite(market["tail_width"]) and market["tail_width"] > 0):
            problems.append("market.tail_width must be a finite number above zero")

    # -- the run ---------------------------------------------------------------------------------------

    def run(self, ctx, inputs):
        """Score every cell of the experiment and publish the four files by one rename.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            Unused; the node reads its experiment from disk.

        Returns
        -------
        dict
            ``{"summary": {...}}`` with the output path and the counts.

        Raises
        ------
        ValueError
            The output or its partial directory exists, the panel is not the document's, a required panel
            column (the spot, the market quantiles) is absent, a row is at or after ``end_before``, or
            ``end_before`` passes the holdout.
        IntegrityError
            An artifact fails its manifest, the panel hash differs, or the re-scored validation rows
            disagree with the stored shards. A raised failure leaves nothing behind.

        Notes
        -----
        The files are built in ``.<output>.partial`` and published by one rename, and a failure removes the
        partial. A process killed outright (power loss, SIGKILL) cannot clean up: it leaves the partial
        directory, and a rerun to the same name is refused until that directory is removed by hand.
        """
        started = time.monotonic()
        output = Path(self.params["output"])
        work = output.parent / f".{output.name}.partial"
        if output.exists():
            raise ValueError(f"{self.key}: {output} exists; refusing to overwrite an output (choose a new output)")
        if work.exists():
            raise ValueError(f"{self.key}: {work} is a partial directory from an interrupted run; remove it first")
        self.listed = LISTED_SOURCES[self.params["listed_strikes"]["source"]](self.params["listed_strikes"])
        self.market = _MarketCurves(self.params["market"])
        self.world = _Experiment(self.params, [*self.listed.columns(), *self.market.columns()])
        output.parent.mkdir(parents=True, exist_ok=True)
        work.mkdir()
        try:
            summary = self._publish(work, started)
            os.rename(work, output)
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
        return {"summary": {"output": str(output), "scored_cells": len(summary["scored_cells"]),
                            "skipped_cells": len(summary["skipped_cells"]), "refusals": len(summary["refusals"]),
                            "not_eligible_cells": len(summary["not_eligible_cells"]),
                            "rows": summary["rows"]}}

    def _publish(self, work, started):
        import pyarrow as pa
        import pyarrow.parquet as pq

        w = self.world
        self.scorer = _ForecastScorer(w.study_config, self.params["z_grid"])
        regions = len(self.params["z_grid"]) + 1
        table = _CellTable(self.params, w.fields, regions, self.params["z_grid"])
        scored, skipped, refusals, ineligible, curves, cells, hashes = [], [], [], [], [], [], {}
        writer, total, schema = None, 0, None
        for cell in self._cells():
            if cell["status"] == "skipped":
                skipped.append({"cell": cell["name"], "reason": cell["reason"]})
                continue
            if cell["status"] == "not_eligible":
                ineligible.append({"cell": cell["name"], "reason": cell["reason"], "admission": cell["admission"]})
                continue
            if cell["status"] != "complete":
                refusals.append({"cell": cell["name"], "reason": cell["reason"]})
                continue
            result = self._score_cell(cell)
            curves.append(result["curves"])
            hashes[cell["name"]] = result["hashes"]
            for band, rows in result["rows"].items():
                ids = {"arm": cell["arm"], "candidate": cell["candidate"], "fold": cell["fold"], "band": band,
                       "band_sampled": bool(rows.band_sampled.iloc[0]),
                       "band_rows_population": int(rows.band_rows_population.iloc[0])}
                cells.append(table.aggregate(rows, ids))
                data = pa.Table.from_pandas(rows, preserve_index=False)
                if writer is None:
                    schema = data.schema
                    writer = pq.ParquetWriter(work / "rows.parquet", schema, compression="zstd")
                writer.write_table(data.cast(schema))
                total += len(rows)
            scored.append(result["summary"])
        if writer is None:
            pd.DataFrame(columns=self._row_columns(regions)).to_parquet(work / "rows.parquet", index=False)
        else:
            writer.close()
        frames = [c for c in cells if len(c)]
        (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()).to_parquet(
            work / "cells.parquet", index=False, compression="zstd")
        self._write_json(work / "curves.json", {"schema": SCHEMA_VERSION, "rules": RULES, "cells": curves})
        outputs = {n: AtomicFitStore.file_hash(work / n) for n in ("rows.parquet", "cells.parquet", "curves.json")}
        summary = {
            "schema": SCHEMA_VERSION, "scored_cells": scored, "skipped_cells": skipped, "refusals": refusals,
            "not_eligible_cells": ineligible, "forecasters": list(FORECASTERS), "rows": total, "z_grid": self.params["z_grid"], "z_labels": self.scorer.labels,
            "regions": regions, "params": self.params,
            "hashes": {"experiment_document_sha256": w.document_sha,
                       "panel_sha256": AtomicFitStore.file_hash(w.config["panel"]),
                       "experiment_identity": w.exp.identity, "checkpoints": hashes,
                       "listed_strike_sources": self.listed.sources, "outputs": outputs},
            "seconds": round(time.monotonic() - started, 3)}
        self._write_json(work / "summary.json", summary)
        return summary

    @staticmethod
    def _write_json(path, value):
        path.write_text(json.dumps(_clean(value), sort_keys=True, indent=1, allow_nan=False) + "\n")

    def _row_columns(self, regions):
        f = self.world.fields
        names = self.scorer.names()
        return ["arm", "candidate", "fold", "band", "band_sampled", "band_rows_population", *f["identity"], f["end"], f["horizon"], f["reference"],
                "spot", "y", "realized_region", *[f"realized_r{r:02d}" for r in range(regions)],
                "market_eligible", "listed_strikes_n", "listed_wing_intervals_n",
                *[f"{who}_{n}" for who in FORECASTERS for n in names]]

    # -- the cells -------------------------------------------------------------------------------------

    def _cells(self):
        """Yield one record per (fold, arm, candidate): its paths, bands and terminal state (verified)."""
        w = self.world
        exp = w.exp
        keys = w.fields["identity"]
        for fold in exp.study.plan.ids():
            for arm_name, arm in w.config["arms"].items():
                bands, groups, admission = exp._bands(w.frame, w.calendar, fold, arm)
                for candidate in w.config["candidates"]:
                    name = f"{arm_name}/{candidate['name']}/{fold}"
                    root = exp.output / REGIME / arm_name / candidate["name"] / str(fold) / "pooled"
                    cell = {"name": name, "arm": arm_name, "candidate": candidate["name"], "fold": fold,
                            "arm_spec": arm, "root": root, "bands": bands, "groups": groups, "admission": admission}
                    cell["identity"] = {
                        "experiment": exp.identity, "regime": REGIME, "arm": arm_name,
                        "model": candidate["name"], "fold": fold, "groups": groups,
                        "band_identities": [value_hash(b[keys].values.tolist()) for b in bands]}
                    cell.update(self._state(cell))
                    yield cell

    def _state(self, cell):
        root, identity = cell["root"], cell["identity"]
        if not cell["groups"]:
            return {"status": "not_eligible", "reason": "the arm admits no group in this fold: the queue skips it"}
        if not root.exists():
            return {"status": "refused", "reason": "cell absent: the queue has not reached it"}
        skipped, completed = root / "skipped", root / "completed"
        if skipped.exists() and completed.exists():
            raise IntegrityError("conflicting completed and skipped terminal states")
        if skipped.exists():
            AtomicFitStore(skipped, identity).verify()
            return {"status": "skipped", "reason": json.loads((skipped / "reason.json").read_text())}
        if not completed.exists():
            return {"status": "refused", "reason": "cell incomplete: neither completed nor skipped"}
        AtomicFitStore(completed, identity).verify()
        record = AtomicFitStore(root / "fit", identity).verify()
        checkpoint = value_hash(record)
        shards = root / "scores"
        for path in sorted(shards.iterdir()) if shards.exists() else ():
            if ".pending-" not in path.name and not (path.is_dir() and path.name in cell["groups"]):
                raise IntegrityError(f"unexpected score shard {path.name} under {shards}")
        for ticker in cell["groups"]:
            AtomicFitStore(root / "scores" / ticker, {"fit": identity, "checkpoint": checkpoint,
                                                      "ticker": ticker}).verify()
        return {"status": "complete", "checkpoint": checkpoint}

    def _score_cell(self, cell):
        w = self.world
        exp, root, groups, arm = w.exp, cell["root"], cell["groups"], cell["arm_spec"]
        mapping = self.params.get("ticker_groups")
        unmapped = sorted(set(groups) - set(mapping or groups))
        if unmapped:
            raise ValueError(f"admitted symbol(s) {unmapped} are missing from ticker_groups: map every admitted "
                             "symbol, or give an empty mapping for the 'all' group only")
        metadata = json.loads((root / "fit" / "metadata.json").read_text())
        arrays, medians = exp._arrays(cell["bands"], arm, groups, REGIME)
        if medians != metadata["medians"]:
            raise IntegrityError("training imputation differs from the checkpoint's: the bands were rebuilt differently")
        model = TorchCDF.load_checkpoint(root / "fit" / "model")
        state = json.loads((root / "fit" / "model" / "state.json").read_text())
        references = {"baselines": self._baselines(cell["bands"][0], groups),
                       "pooled": self._pooled(cell["bands"][0])}
        rows, mixtures = {}, {}
        for position, band in BANDS.items():
            rows[band], mixtures[band] = self._score_band(
                cell, band, cell["bands"][position], arrays[position], model, references,
                self._sample(len(cell["bands"][position]), band))
        self._check_stored(cell, rows["val"], mixtures["val"])
        summary = json.loads((root / "completed" / "summary.json").read_text())
        if summary["checkpoint"] != cell["checkpoint"]:
            raise IntegrityError(f"{cell['name']}: the completed summary checkpoint {summary['checkpoint']} is not "
                                 f"the fit manifest's {cell['checkpoint']}")
        curves = {"cell": cell["name"], "arm": cell["arm"], "candidate": cell["candidate"], "fold": cell["fold"],
                  "regime": REGIME, "parameters": summary["parameters"], "total_epochs": summary["total_epochs"],
                  "best_epochs": summary["best_epochs"], "elapsed_seconds": metadata["elapsed_seconds"],
                  **_LearningCurves(state).record()}
        info = {"cell": cell["name"], "arm": cell["arm"], "candidate": cell["candidate"], "fold": cell["fold"],
                "groups": groups, "rows": {b: len(r) for b, r in rows.items()},
                "fit_rows_total": len(cell["bands"][0]), "market_eligible_rows": {
                    b: int(r.market_eligible.sum()) for b, r in rows.items()},
                "rows_without_listed_strikes": {b: int((r.listed_strikes_n == 0).sum()) for b, r in rows.items()}}
        return {"rows": rows, "curves": curves, "summary": info,
                "hashes": {"weights_sha256": state["weights_sha256"], "fit_manifest": cell["checkpoint"]}}

    def _sample(self, n, band):
        """Return the row positions scored: a deterministic stride over the fit band, every validation row."""
        limit = self.params["fit_band_sample"]
        if band != "fit" or limit is None or n <= limit:
            return np.arange(n)
        return np.arange(0, n, math.ceil(n / limit))

    def _reference_params(self):
        """Return the baseline's params over the (horizon, reference) columns, as the queue builds them."""
        s = self.world.study_config
        params = dict(s["models"][s["reference_model"]]["params"], horizon_index=0, reference_index=1)
        if "condition_indices" in params:
            raise ValueError("additional baseline conditions need explicit mapped columns")
        return params

    def _pooled(self, fit):
        """Return the pooled forecaster: one horizon_empirical fit of the standardized outcomes of ALL fit rows.

        The reference column is all ones, so the owner's class stores and returns standardized (z) outcomes
        grouped by the study horizon, with no per-symbol split.
        """
        f = self.world.fields
        x = np.column_stack([fit[f["horizon"]].to_numpy(float), np.ones(len(fit))])
        y = (fit[f["target"]] / fit[f["reference"]]).to_numpy(float)
        return HorizonEmpiricalCDF(**self._reference_params()).fit(x, y, None, None)

    def _baselines(self, fit, groups):
        """Return the per-symbol horizon_empirical baseline, refit from the fold's fit rows as the queue does."""
        f = self.world.fields
        params = self._reference_params()
        out = {}
        for ticker in groups:
            part = fit[fit[f["group"]] == ticker]
            x = part[[f["horizon"], f["reference"]]].to_numpy()
            y = (part[f["target"]] / part[f["reference"]]).to_numpy()
            out[ticker] = HorizonEmpiricalCDF(**params).fit(x, y, None, None)
        return out

    def _score_band(self, cell, band, frame, x, model, references, sel):
        w, f = self.world, self.world.fields
        baselines, pooled = references["baselines"], references["pooled"]
        population = len(frame)
        frame, x = frame.iloc[sel].reset_index(drop=True), x[sel]
        wanted = [c for c in (*self.listed.columns(), *self.market.columns())]
        extras = w.attach(frame, wanted)
        n = len(frame)
        scored = frame.assign(**{c: extras[c].to_numpy() for c in wanted})
        if "spot" not in scored:
            scored["spot"] = np.nan
        y = (frame[f["target"]] / frame[f["reference"]]).to_numpy(float)
        symbols = frame[f["group"]].to_numpy()
        contexts = self.listed.contexts(scored.rename(columns={
            f["identity"][0]: "symbol", f["identity"][1]: "quote_date", f["identity"][2]: "expiry",
            f["reference"]: "reference_scale"}))
        names = self.scorer.names()
        out = {f"{who}_{name}": np.full(n, np.nan) for who in FORECASTERS for name in names}
        eligible = np.zeros(n, dtype=bool)
        baseline_x = frame[[f["horizon"], f["reference"]]].to_numpy()
        pooled_x = np.column_stack([frame[f["horizon"]].to_numpy(float), np.ones(n)])
        mixture = None
        chunk = self.params["chunk_rows"]
        for ticker in cell["groups"]:
            at = np.flatnonzero(symbols == ticker)
            for start in range(0, len(at), chunk):
                i = at[start:start + chunk]
                part = [contexts[k] for k in i]
                everyone = np.ones(len(i), dtype=bool)
                fitted = model.curve(x[i])
                if mixture is None:
                    mixture = np.full((n, 3, fitted.weights.shape[1]), np.nan)
                mixture[i] = np.stack([fitted.weights, fitted.means, fitted.scales], axis=1)
                curves = {"model": (fitted, everyone),
                          "baseline": (baselines[ticker].curve(baseline_x[i]), everyone),
                          "pooled": (pooled.curve(pooled_x[i]), everyone)}
                mask, curve = self.market.curve(scored.iloc[i], scored[f["reference"]].to_numpy()[i])
                eligible[i] = mask
                if curve is not None:
                    curves["market"] = (curve, mask)
                for who, (curve, mask) in curves.items():
                    scores = self.scorer.score(curve, y[i][mask], [c for c, m in zip(part, mask) if m])
                    for name, values in scores.items():
                        out[f"{who}_{name}"][i[mask]] = values
        grid = np.asarray(self.params["z_grid"], float)
        region = np.searchsorted(grid, y, side="left")
        frame_out = pd.DataFrame({
            "arm": cell["arm"], "candidate": cell["candidate"], "fold": cell["fold"], "band": band,
            "band_sampled": population > n, "band_rows_population": population,
            **{k: frame[k].to_numpy() for k in (*f["identity"], f["end"], f["horizon"], f["reference"])},
            "spot": scored["spot"].to_numpy(float), "y": y, "realized_region": region,
            **{f"realized_r{r:02d}": (region == r).astype("int64") for r in range(len(grid) + 1)},
            "market_eligible": eligible,
            "listed_strikes_n": [len(c["thresholds"]) for c in contexts],
            "listed_wing_intervals_n": [len(_DecisionScorer.wing_context(c)["intervals"]) for c in contexts],
            **out})
        return frame_out, mixture

    def _check_stored(self, cell, val, mixture):
        """Refuse when the re-scored validation rows differ from the shards the queue stored.

        Compared on the validation identities: the mixture ``weights``, ``means`` and ``scales`` of every
        component against the stored columns (absolute difference at most :data:`MIXTURE_ATOL`), then the
        model and baseline ``crps``, ``tail_crps`` and ``weighted_crps`` (within ``stored_tolerance``); the
        identity sets must be equal.
        """
        f, tol = self.world.fields, self.params["stored_tolerance"]
        stored = pd.concat([pd.read_parquet(cell["root"] / "scores" / t / "scores.parquet") for t in cell["groups"]])
        keys = f["identity"]
        parts = ("weights", "means", "scales")
        names = [f"{part}{j}" for part in parts for j in range(mixture.shape[2])]
        absent = sorted(set(names) - set(stored))
        if absent:
            raise IntegrityError(f"{cell['name']}: the stored shards lack mixture column(s) {absent}")
        restored = val[keys].copy()
        for a, part in enumerate(parts):
            for j in range(mixture.shape[2]):
                restored[f"{part}{j}"] = mixture[:, a, j]
        joined = restored.merge(stored[[*keys, *names]], on=keys, how="outer", suffixes=("", "_stored"),
                                validate="one_to_one", indicator=True)
        if (joined.pop("_merge") != "both").any():
            raise IntegrityError(f"{cell['name']}: validation identities differ from the stored shards")
        for name in names:
            if not np.allclose(joined[name], joined[f"{name}_stored"], rtol=0, atol=MIXTURE_ATOL):
                raise IntegrityError(f"{cell['name']}: the restored mixture column {name} differs from the "
                                     f"stored parameters beyond {MIXTURE_ATOL}")
        scores = [f"{who}_{m}" for who in ("model", "baseline") for m in ("crps", "tail_crps", "weighted_crps")]
        joined = val.merge(stored[[*keys, *scores]], on=keys, how="inner", suffixes=("", "_stored"),
                           validate="one_to_one")
        for name in scores:
            if not np.allclose(joined[name], joined[f"{name}_stored"], rtol=tol, atol=tol):
                raise IntegrityError(f"{cell['name']}: re-scored validation {name} differs from the "
                                     f"stored shards beyond {tol}")
