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

from dskit.pipeline.distribution_scores import (
    DEFAULT_OUTCOME_FIELD,
    DEFAULT_SAMPLES_FIELD,
    SampleDistribution,
)
from dskit.pipeline.fitted import FittedTransform
from dskit.pipeline.document import is_node_ref
from dskit.pipeline.node import check_int_param

__all__ = ["DEFAULT_N_SAMPLES", "EmpiricalLocationScale", "REFERENCE_SCALE_FIELD"]

#: Draws emitted per forecast row, unless a document says otherwise.
DEFAULT_N_SAMPLES = 200

#: The row field carrying each forecast's standardizing divisor — how a
#: consumer maps a level (a strike, a threshold) into the forecast's units.
REFERENCE_SCALE_FIELD = "reference_scale"

#: The reference scale multiplier, unless a document says otherwise.
DEFAULT_SCALE_MULTIPLIER = 1.0


def _number(value):
    """``value`` as a float when it is a finite non-boolean number, else ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


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
            A positive multiplier on the fitted shape.
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
        return {"shape": [dist.quantile((k + 0.5) / n) for k in range(n)], "n_fit": len(z)}

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
            draws = None
            if scale is not None:
                stretch = self.relative_scale(row, state)
                draws = [stretch * q for q in state["shape"]]
            out.append({**row, samples_field: draws,
                        outcome_field: self.standardized_label(row),
                        REFERENCE_SCALE_FIELD: scale})
        return out
