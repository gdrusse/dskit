"""Distribution-forecast models that emit SAMPLE SETS (ADR-0168).

A distribution forecast here is a list of draws per row, scored by
:mod:`dskit.pipeline.distribution_scores`. Every model lives in one unit
system: the label divided by a REFERENCE scale the document names
(``scale_field`` x ``scale_multiplier``, known at forecast time). Scoring in
those standardized units is what makes a threshold region such as
``|z| in [0.5, 2.5]`` mean the same thing on every row — and because the
reference is the document's, not the model's, two models are always scored
against the same outcomes.

:class:`EmpiricalLocationScale` is the family base and the first rung: the
fitted SHAPE is the empirical distribution of standardized labels on the fit
split, and :meth:`~EmpiricalLocationScale.relative_scale` — ``1.0`` here —
is the hook a conditional-scale rung overrides to stretch that shape per row.
It is a :class:`~dskit.pipeline.fitted.FittedTransform`, so split selection,
persistence, restore under ``mode="load"`` and the purity screen are the
family's, not re-implemented. Stdlib only.
"""

from __future__ import annotations

import math
from abc import abstractmethod


from dskit.pipeline.distribution_scores import (
    DEFAULT_OUTCOME_FIELD,
    DEFAULT_SAMPLES_FIELD,
    SampleDistribution,
)
from dskit.pipeline.fitted import FittedTransform
from dskit.pipeline.document import is_node_ref
from dskit.pipeline.node import check_int_param
from dskit.pipeline.records import number_ok

__all__ = [
    "DEFAULT_N_SAMPLES",
    "DEFAULT_RIDGE_ALPHA",
    "DEFAULT_SCALE_MULTIPLIER",
    "EmpiricalLocationScale",
    "LinearScaleLocationScale",
    "REFERENCE_SCALE_FIELD",
    "ScaleModelLocationScale",
]

#: Draws emitted per forecast row, unless a document says otherwise.
DEFAULT_N_SAMPLES = 200

#: The row field carrying each forecast's standardizing divisor — how a
#: consumer maps a level (a strike, a threshold) into the forecast's units.
REFERENCE_SCALE_FIELD = "reference_scale"

#: The reference scale multiplier, unless a document says otherwise.
DEFAULT_SCALE_MULTIPLIER = 1.0


def _number(value):
    """``value`` as a float when :func:`records.number_ok` accepts it, else ``None``."""
    return float(value) if number_ok(value) else None


class EmpiricalLocationScale(FittedTransform):
    """Forecast draws from the fit split's empirical standardized-label shape.

    Each row's standardized outcome is ``label / (scale_field * scale_multiplier)``.
    The state is ``n_samples`` evenly spaced quantiles of those values on
    the fit split. Every row is forecast as that shape times
    :meth:`relative_scale` — constant here, so this class is the
    unconditional baseline; a rung with a better scale estimate overrides
    the hook and nothing else.

    Emitted per row: ``samples_field`` (the draws), ``outcome_field`` (the
    standardized label, ``None`` when the label is not yet known), and
    ``reference_scale`` (the divisor, ``None`` when unusable — such a row
    gets no forecast rather than an invented one).

    Parameters
    ----------
    params : dict
        ``label`` and ``scale_field`` (row field names, required),
        ``scale_multiplier`` (> 0, default 1.0), ``n_samples`` (int >= 2,
        default 200), ``samples_field`` / ``outcome_field`` (default
        ``"samples"`` / ``"outcome"``), plus :class:`FittedTransform`'s.

    Examples
    --------
    A 21-step log-return label standardized by 22-step realized vol::

        node = EmpiricalLocationScale("model", {
            "fit_split": "train", "label": "label", "scale_field": "rv_22",
            "scale_multiplier": 4.58257569495584,
        })
        out = node.run(ctx, {"rows": rows})
        # -> {"transform": ..., "rows": [{"samples": [...], "outcome": ...}], ...}
    """

    serving_load_audited = False

    _PARAMS = FittedTransform._PARAMS + (
        "label", "n_samples", "outcome_field", "samples_field",
        "scale_field", "scale_multiplier",
    )

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            The base's problems plus one per broken knob of this class.
        """
        problems = super().validate_params(params)
        for knob in ("label", "scale_field"):
            if knob not in params:
                problems.append(f"{knob} is required")
        for knob, default in (("label", "x"), ("scale_field", "x"),
                              ("samples_field", DEFAULT_SAMPLES_FIELD),
                              ("outcome_field", DEFAULT_OUTCOME_FIELD)):
            value = params.get(knob, default)
            if not is_node_ref(value) and (not isinstance(value, str) or not value):
                problems.append(f"{knob} must be a non-empty string, got {value!r}")
        mult = params.get("scale_multiplier", DEFAULT_SCALE_MULTIPLIER)
        if not is_node_ref(mult) and (_number(mult) is None or mult <= 0):
            problems.append(f"scale_multiplier must be a finite number > 0, got {mult!r}")
        n = params.get("n_samples", DEFAULT_N_SAMPLES)
        if not is_node_ref(n):
            check_int_param(problems, "n_samples", n, ge=2)
        return problems

    def reference_scale(self, row):
        """Return the document's standardizing divisor for one row.

        Parameters
        ----------
        row : dict
            A feature row.

        Returns
        -------
        float or None
            ``scale_field * scale_multiplier``, or ``None`` when the field
            is absent, non-numeric or not positive.
        """
        scale = _number(row.get(self.params["scale_field"]))
        if scale is None or scale <= 0:
            return None
        return scale * self.params.get("scale_multiplier", DEFAULT_SCALE_MULTIPLIER)

    def standardized_label(self, row):
        """Return the row's label in reference units.

        Parameters
        ----------
        row : dict
            A feature row.

        Returns
        -------
        float or None
            ``label / reference_scale``, or ``None`` when either is unusable.
        """
        label, scale = _number(row.get(self.params["label"])), self.reference_scale(row)
        return None if label is None or scale is None else label / scale

    def relative_scale(self, row, state):
        """Return how much to stretch the fitted shape for this row.

        The hook a conditional-scale rung overrides. ``1.0`` here: the
        unconditional baseline.

        Parameters
        ----------
        row : dict
            A feature row with a usable reference scale.
        state : dict
            The fitted state.

        Returns
        -------
        float
            A positive multiplier on the fitted shape, or ``None`` when this
            row cannot be scaled (it then gets no forecast).
        """
        return 1.0

    def fit(self, rows, params):
        """Learn the standardized-label shape from the fit split.

        Parameters
        ----------
        rows : list of dict
            The fit split's rows.
        params : dict
            This node's params.

        Returns
        -------
        dict
            ``{"shape": [...], "n_fit": int}``.

        Raises
        ------
        ValueError
            When fewer than two fit rows carry a usable label and scale.
        """
        z = [v for v in (self.standardized_label(r) for r in rows) if v is not None]
        if len(z) < 2:
            raise ValueError(
                f"{self.key}: {len(z)} fit row(s) carry a usable "
                f"{params['label']!r} and {params['scale_field']!r}; need >= 2"
            )
        dist = SampleDistribution(z)
        n = params.get("n_samples", DEFAULT_N_SAMPLES)
        return {"shape": [dist.quantile((k + 0.5) / n) for k in range(n)], "n_fit": len(z),
                "describes": self.described_knobs()}

    def described_knobs(self):
        """Return the knobs that DESCRIBE the fitted state.

        The label, its reference scale and the draw count define what the
        shape means; a restore under different values would score outcomes
        in one unit against draws fitted in another.

        Returns
        -------
        dict
            Knob name to effective value.
        """
        return {
            "label": self.params["label"],
            "scale_field": self.params["scale_field"],
            "scale_multiplier": self.params.get("scale_multiplier", DEFAULT_SCALE_MULTIPLIER),
            "n_samples": self.params.get("n_samples", DEFAULT_N_SAMPLES),
        }

    def state_problems(self, state):
        """Refuse a restored state fitted under different describing knobs.

        Parameters
        ----------
        state : dict
            The restored state.

        Returns
        -------
        list of str
            One problem per disagreeing knob.
        """
        stored = state.get("describes")
        if not isinstance(stored, dict):
            return ["restored state does not record the knobs that describe it"]
        return [
            f"{knob}={value!r} contradicts the restored state's {stored.get(knob)!r}"
            for knob, value in self.described_knobs().items() if stored.get(knob) != value
        ]

    def apply_state(self, state, rows, params):
        """Attach draws, the standardized outcome and the divisor to each row.

        Parameters
        ----------
        state : dict
            The fitted state.
        rows : list of dict
            Any rows.
        params : dict
            This node's params.

        Returns
        -------
        list of dict
            One new row per input row.
        """
        samples_field = params.get("samples_field", DEFAULT_SAMPLES_FIELD)
        outcome_field = params.get("outcome_field", DEFAULT_OUTCOME_FIELD)
        out = []
        for row in rows:
            scale = self.reference_scale(row)
            stretch = None if scale is None else self.relative_scale(row, state)
            draws = None if stretch is None else [stretch * q for q in state["shape"]]
            out.append({**row, samples_field: draws,
                        outcome_field: self.standardized_label(row),
                        REFERENCE_SCALE_FIELD: scale})
        return out


#: Ridge penalty of :class:`LinearScaleLocationScale`, unless a document says otherwise.
DEFAULT_RIDGE_ALPHA = 0.0


class ScaleModelLocationScale(EmpiricalLocationScale):
    """A location-scale rung whose scale is a FITTED model of forward volatility.

    Abstract (ADR-0169). On the fit split it regresses
    ``log(scale_target)`` — the per-step volatility realized over the
    horizon — on ``log`` of the ``scale_features``, then standardizes each
    fit label by its PREDICTED horizon scale to learn the shape. A forecast
    is that shape times ``predicted / reference``, so draws stay in the
    document's reference units and every rung scores against the same
    outcomes. Standardizing by the prediction rather than the reference is
    what stops the shape from double-counting the spread the model explains.

    Log space keeps the scale positive; ``exp`` of a mean log is a median,
    not a mean, and the fitted shape absorbs that bias because it is
    standardized by the same predictor. The shape uses IN-SAMPLE
    predictions, so a flexible model (a booster) fits a shape that is too
    narrow; cross-fitting is the remedy, not yet built. A row whose feature
    is zero (a flat day's one-step vol) gets no forecast, so on real data
    rungs can score different row sets — compare the scorer's ``n``.

    A member supplies :meth:`fit_scale` and :meth:`predict_scale`; the
    model's state must be JSON.

    Parameters
    ----------
    params : dict
        :class:`EmpiricalLocationScale`'s knobs plus ``scale_target`` (the
        forward per-step vol field, required) and ``scale_features`` (a
        non-empty list of positive per-row fields, required).

    Examples
    --------
    A member is two hooks::

        class MeanScale(ScaleModelLocationScale):
            def fit_scale(self, x, y):
                return {"mean": sum(y) / len(y)}

            def predict_scale(self, model, x):
                return model["mean"]

        node = MeanScale("m", {"fit_split": "train", "label": "label",
                              "scale_field": "rv_22", "scale_target": "rv_fwd",
                              "scale_features": ["rv_22"]})
    """

    _PARAMS = EmpiricalLocationScale._PARAMS + ("scale_features", "scale_target")

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            The base's problems plus the scale model's.
        """
        problems = super().validate_params(params)
        target = params.get("scale_target")
        if "scale_target" not in params:
            problems.append("scale_target is required")
        elif not is_node_ref(target) and (not isinstance(target, str) or not target):
            problems.append(f"scale_target must be a non-empty string, got {target!r}")
        features = params.get("scale_features")
        if "scale_features" not in params:
            problems.append("scale_features is required")
        elif not is_node_ref(features) and (
            not isinstance(features, list) or not features
            or any(not isinstance(f, str) or not f for f in features)
            or len(set(features)) != len(features)
        ):
            problems.append(
                f"scale_features must be a non-empty list of distinct field names, got {features!r}"
            )
        return problems

    @abstractmethod
    def fit_scale(self, x, y):
        """Fit the log-volatility model.

        Parameters
        ----------
        x : list of list of float
            One row of log features per fit row.
        y : list of float
            The log forward per-step volatility per fit row.

        Returns
        -------
        dict
            The model, JSON-serializable.
        """

    @abstractmethod
    def predict_scale(self, model, x):
        """Predict one row's log forward per-step volatility.

        Parameters
        ----------
        model : dict
            What :meth:`fit_scale` returned.
        x : list of float
            One row of log features.

        Returns
        -------
        float
            The predicted log volatility.
        """

    def log_features(self, row):
        """Return the row's log features, or ``None`` when any is not positive.

        Parameters
        ----------
        row : dict
            A feature row.

        Returns
        -------
        list of float or None
            ``log`` of each ``scale_features`` value.
        """
        values = [_number(row.get(f)) for f in self.params["scale_features"]]
        if any(v is None or v <= 0 for v in values):
            return None
        return [math.log(v) for v in values]

    def predicted_vol(self, row, model):
        """Return the model's per-step volatility for one row.

        Parameters
        ----------
        row : dict
            A feature row.
        model : dict
            The fitted scale model.

        Returns
        -------
        float or None
            ``exp`` of the predicted log vol; ``None`` without usable features.
        """
        x = self.log_features(row)
        return None if x is None else math.exp(self.predict_scale(model, x))

    def _fit_pairs(self, rows):
        """Rows usable for fitting: a label, a positive target and positive features."""
        pairs = []
        for row in rows:
            x, target = self.log_features(row), _number(row.get(self.params["scale_target"]))
            label = _number(row.get(self.params["label"]))
            if x is not None and target is not None and target > 0 and label is not None:
                pairs.append((row, x, math.log(target), label))
        return pairs

    def fit(self, rows, params):
        """Fit the scale model, then the shape of labels standardized by it.

        Parameters
        ----------
        rows : list of dict
            The fit split's rows.
        params : dict
            This node's params.

        Returns
        -------
        dict
            ``shape``, ``n_fit``, ``describes`` and the scale ``model``.

        Raises
        ------
        ValueError
            When fewer usable fit rows exist than features + 2.
        """
        pairs = self._fit_pairs(rows)
        if len(pairs) < len(params["scale_features"]) + 2:
            raise ValueError(
                f"{self.key}: {len(pairs)} fit row(s) carry a usable label, "
                f"{params['scale_target']!r} and every scale feature; need >= "
                f"{len(params['scale_features']) + 2}"
            )
        model = self.fit_scale([p[1] for p in pairs], [p[2] for p in pairs])
        mult = params.get("scale_multiplier", DEFAULT_SCALE_MULTIPLIER)
        z = [label / (math.exp(self.predict_scale(model, x)) * mult)
             for _, x, _, label in pairs]
        dist = SampleDistribution(z)
        n = params.get("n_samples", DEFAULT_N_SAMPLES)
        return {"shape": [dist.quantile((k + 0.5) / n) for k in range(n)], "n_fit": len(z),
                "describes": self.described_knobs(), "model": model}

    def described_knobs(self):
        """Return the knobs that describe the state, the scale model's included.

        Returns
        -------
        dict
            Knob name to effective value.
        """
        return {**super().described_knobs(),
                "scale_target": self.params["scale_target"],
                "scale_features": list(self.params["scale_features"])}

    def relative_scale(self, row, state):
        """Stretch the shape by predicted over reference per-step volatility.

        Parameters
        ----------
        row : dict
            A feature row with a usable reference scale.
        state : dict
            The fitted state.

        Returns
        -------
        float or None
            ``predicted / scale_field``; ``None`` without usable features.
        """
        predicted = self.predicted_vol(row, state["model"])
        return None if predicted is None else predicted / _number(row[self.params["scale_field"]])


class LinearScaleLocationScale(ScaleModelLocationScale):
    """Ridge-regularized OLS of log forward vol on log features (HAR in logs).

    With ``scale_features`` ``[rv_1, rv_5, rv_22]`` this is the log-HAR
    model of Corsi (2009); the intercept is never penalized. Stdlib only:
    the normal equations are small and solved directly.

    Parameters
    ----------
    params : dict
        :class:`ScaleModelLocationScale`'s knobs plus ``ridge_alpha``
        (float >= 0, default 0.0).

    Examples
    --------
    A log-HAR scale rung::

        node = LinearScaleLocationScale("har", {
            "fit_split": "train", "label": "label", "scale_field": "rv_22",
            "scale_multiplier": 4.58257569495584, "scale_target": "rv_fwd",
            "scale_features": ["rv_1", "rv_5", "rv_22"],
        })
        out = node.run(ctx, {"rows": rows})
    """

    serving_load_audited = False

    _PARAMS = ScaleModelLocationScale._PARAMS + ("ridge_alpha",)

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            The base's problems plus the penalty's.
        """
        problems = super().validate_params(params)
        alpha = params.get("ridge_alpha", DEFAULT_RIDGE_ALPHA)
        if not is_node_ref(alpha) and (_number(alpha) is None or alpha < 0):
            problems.append(f"ridge_alpha must be a finite number >= 0, got {alpha!r}")
        return problems

    def described_knobs(self):
        """Return the describing knobs, the penalty included.

        Returns
        -------
        dict
            Knob name to effective value.
        """
        return {**super().described_knobs(),
                "ridge_alpha": self.params.get("ridge_alpha", DEFAULT_RIDGE_ALPHA)}

    def fit_scale(self, x, y):
        """Solve the ridge normal equations with an unpenalized intercept.

        Parameters
        ----------
        x : list of list of float
            Log features.
        y : list of float
            Log forward volatility.

        Returns
        -------
        dict
            ``{"coef": [intercept, b_1, ...]}``.

        Raises
        ------
        ValueError
            When the normal equations are singular.
        """
        alpha = float(self.params.get("ridge_alpha", DEFAULT_RIDGE_ALPHA))
        design = [[1.0, *row] for row in x]
        k = len(design[0])
        gram = [[sum(r[i] * r[j] for r in design) + (alpha if i == j and i else 0.0)
                 for j in range(k)] for i in range(k)]
        rhs = [sum(r[i] * t for r, t in zip(design, y)) for i in range(k)]
        return {"coef": _solve(gram, rhs, self.key)}

    def predict_scale(self, model, x):
        """Evaluate the linear model; see :meth:`ScaleModelLocationScale.predict_scale`."""
        coef = model["coef"]
        return coef[0] + sum(b * v for b, v in zip(coef[1:], x))


def _solve(matrix, rhs, key):
    """Gaussian elimination with partial pivoting; refuses a singular system."""
    n = len(rhs)
    a = [list(row) + [b] for row, b in zip(matrix, rhs)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError(f"{key}: the scale features are collinear — the fit is singular")
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(n):
            if r != col:
                factor = a[r][col] / a[col][col]
                a[r] = [v - factor * w for v, w in zip(a[r], a[col])]
    return [a[i][n] / a[i][i] for i in range(n)]
