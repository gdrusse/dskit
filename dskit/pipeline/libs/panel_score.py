"""Score any horizon forecaster on a panel's validation band, and two baselines that fit on its fit band.

:class:`PanelScore` is the ONE scoring node of the panel zoo. It takes a single
:class:`~dskit.pipeline.libs.horizon_cdf.HorizonForecaster` (one wire, never a mapping of them),
cuts the panel with :class:`~dskit.pipeline.libs.array_panel.PanelBands`, and scores the
validation rows on the declared :class:`~dskit.pipeline.libs.horizon_cdf.CommonGrid`. Every model
is therefore held to the same rows (``rows_sha256``) and the same rule. Its ``metrics`` output is
flat (``crps_h1`` ... ``pit_ks_h15``, horizon means, ``weighted_crps``) so a walk-forward
``objective`` and the ``runs`` table read it directly; the full per-horizon, per-asset and
per-level tables sit in the ``horizon_scores.json`` artifact.

**Pins.** Every value stated in two places is checked, not trusted. The grid refuses a model whose
levels or ``training_tail`` disagree with it; this node additionally refuses a model that declares
``training_tail_weight`` or ``training_bounds`` (``min_run``, ``cal_days``) different from its own.
Both are OPTIONAL attributes of a forecaster: a model that declares neither is scored without
those two pins.

**Baselines.** :class:`EmpiricalBaseline` and :class:`EwmaStudentBaseline` fit on the FIT band
only (never calibration, never validation), keep their state in ``state.json``, and restore it in
``mode: load`` without touching the panel, so a later window can be scored from an earlier fit.
Labels arrive already divided by their scale (the array-panel unit contract), so the empirical
distribution of the labels is the volatility-standardised empirical baseline. They pool every
asset named in ``assets``; the unpooled regime is one lane per asset.

Wiring is by import path: :data:`NODE_KINDS` is empty. numpy and torch are imported only inside
methods.
"""

import statistics
from abc import abstractmethod

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.libs.array_panel import ArrayPanel, PanelBands
from dskit.pipeline.libs.horizon_cdf import (
    CommonGrid,
    EmpiricalHorizonFit,
    HorizonForecaster,
    StudentScaleFit,
)
from dskit.pipeline.node import Node, TrainableNode, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = [
    "EmpiricalBaseline",
    "EmpiricalForecaster",
    "EwmaStudentBaseline",
    "NODE_KINDS",
    "PanelScore",
    "SCORED_SPLIT",
    "SCORES_NAME",
    "STATE_NAME",
    "StudentForecaster",
]

#: Deliberately EMPTY: wired by import path (``dskit.pipeline.libs.panel_score:PanelScore``).
NODE_KINDS = ()

#: The artifact a baseline saves its fitted state in.
STATE_NAME = "state.json"
#: The artifact :class:`PanelScore` writes its full tables to.
SCORES_NAME = "horizon_scores.json"

#: The cuts every band-using node declares; the values are :class:`PanelBands`' own.
_BOUND_KEYS = ("train_end_ms", "val_start_ms", "val_end_ms", "cal_days", "min_run")
#: The one band :class:`PanelScore` reads; the planner's ``score`` role makes every score node name it.
SCORED_SPLIT = "val"
#: The two bounds a forecaster's ``training_bounds`` must agree with the scoring node on.
_PINNED_BOUNDS = ("min_run", "cal_days")
_SCORED_METRICS = ("crps", "tail_crps", "pinball", "pit_ks")


class _PanelBound:
    """What a node that opens a panel and cuts its bands shares: params, validation, the cut.

    A mixin, not a node: :class:`PanelScore` and the baselines both declare ``panel``, ``bounds``,
    ``assets`` and ``grid``, and the cut itself is :class:`PanelBands`, so the rule for opening a
    panel and cutting it has exactly one home.
    """

    _BOUND_PARAMS = ("panel", "bounds", "assets", "grid")

    @classmethod
    def _bound_problems(cls, problems, params):
        """Append a problem for each ill-formed shared knob; ranges are :class:`PanelBands`' to check."""
        panel = params.get("panel")
        if "panel" in params and not is_node_ref(panel) and not isinstance(panel, dict):
            problems.append(f"panel must be a source spec dict, got {panel!r}")
        if "bounds" in params and not is_node_ref(params["bounds"]):
            cls._cut_problems(problems, params["bounds"])
        if "assets" in params:
            assets = params["assets"]
            ok = isinstance(assets, (list, tuple)) and assets and all(
                isinstance(a, int) and not isinstance(a, bool) for a in assets)
            if not is_node_ref(assets) and not ok:
                problems.append(f"assets must be a non-empty list of integer group codes, got {assets!r}")
        if "grid" in params and not is_node_ref(params["grid"]):
            try:
                CommonGrid(params["grid"])
            except ValueError as exc:
                problems.append(f"grid: {exc}")

    @classmethod
    def _cut_problems(cls, problems, bounds):
        """Append a problem unless ``bounds`` is a dict of exactly the cut keys, each an int or a reference."""
        if not isinstance(bounds, dict):
            problems.append(f"bounds must be a dict with keys {list(_BOUND_KEYS)}, got {bounds!r}")
            return
        problems += [f"bounds is missing {k!r}" for k in _BOUND_KEYS if k not in bounds]
        problems += [f"bounds has unknown key {k!r} - allowed: {list(_BOUND_KEYS)}" for k in bounds
                     if k not in _BOUND_KEYS]
        for key in _BOUND_KEYS:
            if key in bounds and not is_node_ref(bounds[key]):
                check_int_param(problems, f"bounds.{key}", bounds[key], ge=0)

    def _open_bands(self):
        """Open the declared panel and cut it; returns ``(panel, bands)``."""
        panel = ArrayPanel.from_source(self.params["panel"])
        assets = self.params.get("assets")
        bands = PanelBands(panel, panel.groups if assets is None else assets, **self.params["bounds"])
        return panel, bands


class PanelScore(_PanelBound, Node):
    """Score one forecaster's validation-band forecasts on the common grid.

    Role ``score``. Input port ``model``: ONE :class:`HorizonForecaster`. Output ``metrics``: flat
    ``crps_h<H>``, ``tail_crps_h<H>``, ``pinball_h<H>``, ``pit_ks_h<H>`` per panel horizon, their
    means over horizons (``crps``, ``tail_crps``, ``pinball``, ``pit_ks``), ``weighted_crps``
    (``crps + tail_weight * tail_crps``, the walk-forward objective), ``n_rows`` and
    ``rows_sha256``. Artifact ``horizon_scores.json`` holds the full tables: overall and per asset
    (each with PIT histograms and p-values), pinball per level, ``cdf_basis`` and the declared
    knobs.

    Parameters
    ----------
    params : dict
        ``panel`` (dict, required): the :meth:`ArrayPanel.from_source` spec. ``bounds`` (dict,
        required): ``train_end_ms``, ``val_start_ms``, ``val_end_ms``, ``cal_days``, ``min_run``;
        each may be a ``$splits.*`` reference. ``grid`` (dict, required): a :class:`CommonGrid`
        spec. ``tail_weight`` (number ``>= 0``, required): the weight of ``tail_crps`` in
        ``weighted_crps``. ``split`` (``"val"``, required): the band scored; the planner makes
        every ``score`` node declare one, and ``val`` is the only band this node reads.
        ``assets`` (non-empty list of group codes, optional): score only those
        groups; the artifact reports each. Default: every group in the panel.

    Raises
    ------
    ValueError
        At run, when the forecaster's levels, ``training_tail``, ``training_tail_weight`` or
        ``training_bounds`` disagree with this node, a band is empty, or an asset is not in the
        panel.

    Examples
    --------
    Score a fitted baseline on one fold's validation window::

        node = PanelScore("score", {
            "panel": {"root": "/store", "source": "blobs", "stream": "files", "relpath": "px"},
            "bounds": {"train_end_ms": "$splits.train_end_ms", "val_start_ms": "$splits.val_start_ms",
                       "val_end_ms": "$splits.val_end_ms", "cal_days": 30, "min_run": 1441},
            "grid": {"z_min": -8, "z_max": 8, "levels": [0.1, 0.5, 0.9],
                     "tail_intervals": [[-8, -2], [2, 8]]},
            "tail_weight": 1.0, "split": "val"})
        metrics = node.run(ctx, {"model": forecaster})["metrics"]
        metrics["weighted_crps"]   # -> crps + tail_weight * tail_crps, averaged over horizons
    """

    role = "score"
    outputs = ("metrics",)

    _PARAMS = _PanelBound._BOUND_PARAMS + ("tail_weight", "split")
    _REQUIRED = ("panel", "bounds", "grid", "tail_weight", "split")

    @classmethod
    def validate_params(cls, params):
        """Default-deny the knobs, require the four that have no default, and hold each to its type.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = super().validate_params(params)
        reject_unknown_params(problems, params, cls._PARAMS)
        problems += [f"{k} is required" for k in cls._REQUIRED if k not in params]
        cls._bound_problems(problems, params)
        weight = params.get("tail_weight")
        if "tail_weight" in params and not is_node_ref(weight) and not (number_ok(weight) and weight >= 0):
            problems.append(f"tail_weight must be a number >= 0, got {weight!r}")
        if "split" in params and params["split"] != SCORED_SPLIT:
            problems.append(f"split must be {SCORED_SPLIT!r} (the only band this node scores), got {params['split']!r}")
        return problems

    def validate_inputs(self, inputs):
        """Require ``model`` to be a :class:`HorizonForecaster`.

        Parameters
        ----------
        inputs : dict or None
            The materialized inputs.

        Returns
        -------
        list of str
            A problem when ``model`` is absent or not a forecaster.
        """
        model = (inputs or {}).get("model")
        if not isinstance(model, HorizonForecaster):
            return [f"input 'model' must be a HorizonForecaster, got {type(model).__name__}"]
        return []

    def run(self, ctx, inputs):
        """Cut the bands, check the pins, score the validation rows and write the tables.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; the artifact lands under its run dir.
        inputs : dict
            ``model``: the forecaster to score.

        Returns
        -------
        dict
            ``metrics``, as described on the class.

        Raises
        ------
        ValueError
            For a bad input, a pin that disagrees, an empty band, or an unknown asset.
        """
        problems = self.validate_inputs(inputs)
        if problems:
            raise ValueError(f"{self.key}: {'; '.join(problems)}")
        model = inputs["model"]
        panel, bands = self._open_bands()
        grid = CommonGrid(self.params["grid"])
        self._check_model(model, grid)
        overall, by_level = self._evaluate(grid, model, panel, bands.val_idx)
        per_asset, per_asset_levels = self._per_asset(grid, model, panel, bands, (overall, by_level))
        metrics = self._metrics(panel, bands, overall)
        self.write_artifact(ctx, SCORES_NAME, {
            "cdf_basis": overall["cdf_basis"], "horizons": list(panel.horizons),
            "levels": list(grid.levels), "tail_intervals": [list(i) for i in grid.tail_intervals],
            "tail_weight": self.params["tail_weight"], "rows_sha256": metrics["rows_sha256"],
            "overall": overall, "per_asset": per_asset,
            "pinball_by_level": {"overall": by_level, "per_asset": per_asset_levels},
        })
        return {"metrics": metrics}

    def _check_model(self, model, grid):
        """Refuse a forecaster whose declared pins disagree with the grid or with this node."""
        grid.check_forecaster(model)
        weight = getattr(model, "training_tail_weight", None)
        if weight is not None and weight != self.params["tail_weight"]:
            raise ValueError(f"{self.key}: the model was trained with tail_weight {weight}, "
                             f"this node scores with tail_weight {self.params['tail_weight']}")
        trained = getattr(model, "training_bounds", None)
        for key in _PINNED_BOUNDS:
            if trained is not None and trained[key] != self.params["bounds"][key]:
                raise ValueError(f"{self.key}: the model's training_bounds {key} {trained[key]} differs "
                                 f"from this node's {self.params['bounds'][key]}: not one common row set")

    def _per_asset(self, grid, model, panel, bands, overall):
        """Return the per-asset summaries and per-level pinball tables, keyed by group code."""
        import numpy as np

        if len(bands.assets) == 1:
            key = str(bands.assets[0])
            return {key: overall[0]}, {key: overall[1]}
        group = np.asarray(panel.group)[bands.val_idx]
        tables, levels = {}, {}
        for code in bands.assets:
            tables[str(code)], levels[str(code)] = self._evaluate(grid, model, panel, bands.val_idx[group == code])
        return tables, levels

    def _evaluate(self, grid, model, panel, rows):
        """Forecast and score ``rows`` chunk by chunk; returns ``(summary, pinball by horizon and level)``."""
        import numpy as np
        import torch

        y = torch.as_tensor(np.asarray(panel.labels[rows], dtype=np.float64))
        step = grid.chunk_rows(y.shape[1], model.components, y.element_size())
        scores = grid.new_scores()
        level_sums = torch.zeros(y.shape[1], len(grid.levels), dtype=torch.float64)
        for start in range(0, len(rows), step):
            stop = min(start + step, len(rows))
            dist = model.forecast(panel, rows[start:stop], step)
            scores.add(dist, y[start:stop])
            level_sums += self._level_pinball(torch, grid, dist, y[start:stop])
        summary = scores.summary()
        return summary, (level_sums / summary["n_rows"]).tolist()

    @staticmethod
    def _level_pinball(torch, grid, dist, y):
        """Sum the quantile loss per horizon and level for one chunk (the level mean is the grid pinball, pinned by test)."""
        tau = torch.tensor(grid.levels, dtype=dist.dtype, device=dist.device)
        quantiles = dist.quantile(grid.levels)
        quantiles = quantiles.expand(y.shape[0], *quantiles.shape[1:])
        residual = y.to(device=dist.device, dtype=dist.dtype).unsqueeze(-1) - quantiles
        loss = torch.maximum(tau * residual, (tau - 1) * residual)
        return loss.sum(0, dtype=torch.float64).cpu()

    def _metrics(self, panel, bands, summary):
        """Flatten the overall summary into the ``metrics`` output."""
        metrics = {}
        for name in _SCORED_METRICS:
            for horizon, value in zip(panel.horizons, summary[name]):
                metrics[f"{name}_h{horizon}"] = value
            metrics[name] = statistics.fmean(summary[name])
        metrics["weighted_crps"] = metrics["crps"] + self.params["tail_weight"] * metrics["tail_crps"]
        metrics["n_rows"] = summary["n_rows"]
        metrics["rows_sha256"] = bands.rows_sha256()
        return metrics


class EmpiricalForecaster(HorizonForecaster):
    """A row-constant forecaster: the fitted empirical quantiles, clipped to the grid's range.

    Parameters
    ----------
    fit : EmpiricalHorizonFit
        A pooled fit.
    bounds : tuple of float
        The grid's ``(z_min, z_max)``; ``CommonGrid.bounds``.
    training_bounds : dict or None
        The ``min_run`` and ``cal_days`` the fit's rows were cut with, for :class:`PanelScore`'s pin.

    Examples
    --------
    Wrap a fit and forecast any rows (the panel is not read)::

        fit = EmpiricalHorizonFit([0.1, 0.5, 0.9]).fit(labels)
        model = EmpiricalForecaster(fit, (-8.0, 8.0), {"min_run": 3, "cal_days": 2})
        dist = model.forecast(panel, rows, 1024)
    """

    components = 1

    def __init__(self, fit, bounds, training_bounds=None):
        self.fit = fit
        self.bounds = tuple(bounds)
        self.levels = tuple(fit.levels)
        self.training_bounds = training_bounds

    def forecast(self, panel, rows, chunk_rows):
        """Return the one row-constant distribution; see :meth:`HorizonForecaster.forecast`."""
        return self.fit.predict(None, bounds=self.bounds)


class StudentForecaster(HorizonForecaster):
    """A Student-t forecaster whose scale is a fitted multiple of a panel volatility column.

    Parameters
    ----------
    fit : StudentScaleFit
        A fitted (or restored) scale model.
    vol_column : str
        The panel feature column the fit's volatility was read from.
    training_bounds : dict or None
        The ``min_run`` and ``cal_days`` the fit's rows were cut with, for :class:`PanelScore`'s pin.

    Examples
    --------
    Wrap a fit and forecast rows of a panel that carries the column::

        fit = StudentScaleFit(degrees=(3, 5)).fit(labels, vol)
        model = StudentForecaster(fit, "vol__ewma", {"min_run": 3, "cal_days": 2})
        dist = model.forecast(panel, rows, 1024)
    """

    components = 1

    def __init__(self, fit, vol_column, training_bounds=None):
        self.fit = fit
        self.vol_column = vol_column
        self.training_bounds = training_bounds

    def forecast(self, panel, rows, chunk_rows):
        """Return the Student mixture for ``rows``; see :meth:`HorizonForecaster.forecast`.

        Raises
        ------
        ValueError
            When the panel lacks the volatility column.
        """
        import numpy as np
        import torch

        if self.vol_column not in panel.columns:
            raise ValueError(f"panel has no volatility column {self.vol_column!r}")
        vol = np.asarray(panel.features[np.asarray(rows), panel.columns.index(self.vol_column)],
                         dtype=np.float64)
        return self.fit.predict(torch.as_tensor(vol))


class _PanelBaseline(_PanelBound, TrainableNode):
    """Fit a baseline on the fit band, save ``state.json``, restore it in ``mode: load``.

    A template: this class owns params, band cutting, the artifact and the mode hooks. A subclass
    supplies only the model, through two abstract hooks, so an incomplete baseline refuses to
    construct.
    """

    role = "train"
    outputs = ("model",)

    _PARAMS = _PanelBound._BOUND_PARAMS
    _EXTRA_PARAMS = ()

    @classmethod
    def validate_params(cls, params):
        """Default-deny the knobs and hold the shared ones to their types.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = super().validate_params(params)
        reject_unknown_params(problems, params, cls._PARAMS + cls._EXTRA_PARAMS)
        cls._bound_problems(problems, params)
        return problems

    @abstractmethod
    def fit_state(self, panel, rows, grid):
        """Fit on the fit-band ``rows`` and return the model's state as plain JSON-able data."""

    @abstractmethod
    def forecaster_from(self, state, grid):
        """Rebuild the :class:`HorizonForecaster` from a saved ``state``, refusing one that disagrees with ``grid``."""

    def run_train(self, ctx, inputs):
        """Fit on the fit band, write ``state.json`` and return the forecaster.

        Parameters
        ----------
        ctx : NodeContext
            The run frame.
        inputs : dict
            Unused: the rows come from the bands.

        Returns
        -------
        dict
            ``model``: the fitted forecaster.

        Raises
        ------
        ValueError
            When ``panel``, ``bounds`` or ``grid`` is missing, or the fit refuses its rows.
        """
        self._require("panel", "bounds", "grid")
        panel, bands = self._open_bands()
        grid = CommonGrid(self.params["grid"])
        record = {"baseline": type(self).__name__, "state": self.fit_state(panel, bands.fit_idx, grid),
                  "bounds": dict(self.params["bounds"]), "assets": list(bands.assets),
                  "rows_sha256": bands.rows_sha256(), "n_fit_rows": int(len(bands.fit_idx))}
        self.write_artifact(ctx, STATE_NAME, record)
        return {"model": self._forecaster(record, grid)}

    def run_load(self, ctx, inputs):
        """Restore the pinned ``state.json``; never fits and never opens the panel.

        Parameters
        ----------
        ctx : NodeContext
            The run frame.
        inputs : dict
            Unused.

        Returns
        -------
        dict
            ``model``: the restored forecaster.

        Raises
        ------
        ValueError
            When ``grid`` is missing, the state belongs to another baseline, or it disagrees with
            the grid.
        """
        self._require("grid")
        ref = self.pinned_artifact(missing="mode='load' needs a pinned artifact")
        record = self.read_artifact(ctx, STATE_NAME, ref=ref)
        if record["baseline"] != type(self).__name__:
            raise ValueError(f"{self.key}: the saved state is a {record['baseline']}, this node is a "
                             f"{type(self).__name__}")
        return {"model": self._forecaster(record, CommonGrid(self.params["grid"]))}

    def _require(self, *knobs):
        """Refuse when a knob that fitting or restoring needs was not declared."""
        missing = [k for k in knobs if k not in self.params]
        if missing:
            raise ValueError(f"{self.key}: {', '.join(missing)} is required to {self.effective_mode}")

    def _forecaster(self, record, grid):
        """Build the forecaster from a state record and stamp the bounds it was cut with."""
        forecaster = self.forecaster_from(record["state"], grid)
        forecaster.training_bounds = {k: record["bounds"][k] for k in _PINNED_BOUNDS}
        return forecaster


class EmpiricalBaseline(_PanelBaseline):
    """The volatility-standardised empirical baseline: pooled label quantiles at the grid's levels.

    Labels arrive already divided by their scale, so the empirical distribution of the fit-band
    labels IS the vol-standardised empirical distribution; every row and asset gets the same
    quantile function per horizon. Role ``train``; output ``model`` is an
    :class:`EmpiricalForecaster`.

    Parameters
    ----------
    params : dict
        ``panel``, ``bounds`` (``train_end_ms``, ``val_start_ms``, ``val_end_ms``, ``cal_days``,
        ``min_run``; each may be a ``$splits.*`` reference), ``grid`` (a :class:`CommonGrid`
        spec; supplies the levels and the tail clip) and optional ``assets``, exactly as on
        :class:`PanelScore`. ``panel``, ``bounds`` and ``grid`` are required to fit; ``grid`` alone
        to restore.

    Examples
    --------
    Fit one fold's baseline, then hand it to a scoring node::

        node = EmpiricalBaseline("emp", {"panel": source, "bounds": bounds, "grid": grid})
        model = node.run(ctx, {})["model"]
    """

    _EXTRA_PARAMS = ()

    def fit_state(self, panel, rows, grid):
        """Fit the pooled quantile functions on the fit-band labels; return :meth:`EmpiricalHorizonFit.state`."""
        import numpy as np
        import torch

        y = torch.as_tensor(np.asarray(panel.labels[rows], dtype=np.float64))
        return EmpiricalHorizonFit(grid.levels).fit(y).state()

    def forecaster_from(self, state, grid):
        """Rebuild an :class:`EmpiricalForecaster`, refusing a fit whose levels differ from the grid's."""
        fit = EmpiricalHorizonFit.from_state(state)
        if tuple(fit.levels) != grid.levels:
            raise ValueError(f"{self.key}: the saved levels {tuple(fit.levels)} differ from the grid's levels "
                             f"{grid.levels}")
        return EmpiricalForecaster(fit, grid.bounds)


class EwmaStudentBaseline(_PanelBaseline):
    """A Student-t baseline whose scale is a fitted per-horizon multiple of a volatility column.

    The degrees of freedom are chosen from the declared list by fit-band NLL and the multipliers
    by EM, both by :class:`StudentScaleFit`. Point the column at an EWMA volatility to make it the
    EWMA Student-t baseline. Role ``train``; output ``model`` is a :class:`StudentForecaster`.

    Parameters
    ----------
    params : dict
        The :class:`EmpiricalBaseline` params plus ``vol_column`` (str, required): a positive
        panel feature column, and ``degrees`` (list of distinct ints ``>= 3``, required).
        ``vol_column`` and ``degrees`` are needed to restore as well as to fit.

    Examples
    --------
    Fit on a panel carrying an EWMA volatility column::

        node = EwmaStudentBaseline("stu", {"panel": source, "bounds": bounds, "grid": grid,
                                           "vol_column": "vol__ewma_60", "degrees": [3, 5, 8]})
        model = node.run(ctx, {})["model"]
    """

    _EXTRA_PARAMS = ("vol_column", "degrees")

    @classmethod
    def validate_params(cls, params):
        """Add the volatility column and the degrees to the shared checks.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = super().validate_params(params)
        problems += [f"{k} is required" for k in cls._EXTRA_PARAMS if k not in params]
        column = params.get("vol_column")
        if "vol_column" in params and not is_node_ref(column) and not (isinstance(column, str) and column):
            problems.append(f"vol_column must be a non-empty string, got {column!r}")
        if "degrees" in params and not is_node_ref(params["degrees"]):
            try:
                StudentScaleFit(params["degrees"])
            except ValueError as exc:
                problems.append(f"degrees: {exc}")
        return problems

    def fit_state(self, panel, rows, grid):
        """Fit the multipliers on the fit-band labels and volatility; return the fit state and the column."""
        import numpy as np
        import torch

        column = self.params["vol_column"]
        if column not in panel.columns:
            raise ValueError(f"{self.key}: vol_column {column!r} is not a panel column")
        y = torch.as_tensor(np.asarray(panel.labels[rows], dtype=np.float64))
        vol = torch.as_tensor(np.asarray(panel.features[rows, panel.columns.index(column)], dtype=np.float64))
        fit = StudentScaleFit(degrees=self.params["degrees"]).fit(y, vol)
        return {"fit": fit.state(), "vol_column": column}

    def forecaster_from(self, state, grid):
        """Rebuild a :class:`StudentForecaster`, refusing a state fit on another volatility column."""
        if state["vol_column"] != self.params["vol_column"]:
            raise ValueError(f"{self.key}: the saved state used vol_column {state['vol_column']!r}, "
                             f"this node declares {self.params['vol_column']!r}")
        return StudentForecaster(StudentScaleFit.from_state(state["fit"]), state["vol_column"])
