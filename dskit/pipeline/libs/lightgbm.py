"""The tier-2 LightGBM pack — a gradient-boosted volatility-scale rung (ADR-0169).

:class:`LightGBMScaleLocationScale` is a
:class:`~dskit.pipeline.distribution_models.ScaleModelLocationScale` whose
log-volatility model is a LightGBM regressor. The booster is persisted as
LightGBM's own TEXT model (``model_to_string``) inside the fitted JSON state,
so the family's sidecar, restore and purity screen apply unchanged and no
pickle is written.

Determinism is forced, not requested: ``seed`` is a declared knob, the run
is single-threaded with LightGBM's ``deterministic`` flag, and a document
may not smuggle a second seed or thread count through ``lgbm_params``.

LightGBM and numpy are named only inside methods, so importing this pack —
and planning a document that references it — needs neither installed.
Wiring is by import path; the pack registers nothing.
"""

from __future__ import annotations

import hashlib

from dskit.pipeline.distribution_models import ScaleModelLocationScale
from dskit.pipeline.document import is_node_ref
from dskit.pipeline.node import check_int_param
from dskit.pipeline.records import number_ok

__all__ = [
    "DEFAULT_LGBM_PARAMS",
    "ALLOWED_LGBM_KEYS",
    "FORCED_LGBM_KEYS",
    "LGBM_KEY_TYPES",
    "LightGBMScaleLocationScale",
    "NODE_KINDS",
]

#: Booster settings unless a document overrides them: small, shallow and
#: heavily regularized, because the effective sample at a monthly horizon
#: is a few hundred independent windows (research A0004).
DEFAULT_LGBM_PARAMS = {
    "n_estimators": 200,
    "learning_rate": 0.05,
    "num_leaves": 7,
    "min_child_samples": 50,
    "reg_lambda": 1.0,
}

#: Keys the pack sets itself; a document naming one is refused.
FORCED_LGBM_KEYS = ("deterministic", "n_jobs", "random_state", "seed", "verbosity")

#: The ONLY ``LGBMRegressor`` keywords a document may set, and each one's
#: type — its named
#: constructor arguments less the forced ones and the classifier-only
#: ``class_weight``. Default-deny: LightGBM silently ignores an unknown key
#: and honors aliases (``num_threads``, ``random_seed``) that would override
#: the forced settings, so anything else is refused. Stated here rather than
#: read from the library so a document plans with LightGBM absent; a test
#: pins the list to the installed signature.
LGBM_KEY_TYPES = {
    "boosting_type": "str", "importance_type": "str", "objective": "str",
    "max_depth": "int", "min_child_samples": "int", "n_estimators": "int",
    "num_leaves": "int", "subsample_for_bin": "int", "subsample_freq": "int",
    "colsample_bytree": "number", "learning_rate": "number", "min_child_weight": "number",
    "min_split_gain": "number", "reg_alpha": "number", "reg_lambda": "number",
    "subsample": "number",
}

#: The allowed key names, derived from :data:`LGBM_KEY_TYPES` (one owner).
ALLOWED_LGBM_KEYS = tuple(sorted(LGBM_KEY_TYPES))

#: What each declared value type accepts. ``bool`` is never an int here.
_TYPE_OK = {
    "str": lambda v: isinstance(v, str) and bool(v),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": number_ok,
}


class LightGBMScaleLocationScale(ScaleModelLocationScale):
    """A LightGBM model of log forward volatility as the distribution's scale.

    Parameters
    ----------
    params : dict
        :class:`ScaleModelLocationScale`'s knobs plus ``lgbm_params`` (a
        dict of ``LGBMRegressor`` keyword arguments with JSON-scalar
        values, merged over :data:`DEFAULT_LGBM_PARAMS`; none of
        :data:`FORCED_LGBM_KEYS`) and ``seed`` (int >= 0, default 0).

    Examples
    --------
    A gradient-boosted scale rung on HAR inputs::

        node = LightGBMScaleLocationScale("gbm", {
            "fit_split": "train", "label": "label", "scale_field": "rv_22",
            "scale_multiplier": 4.58257569495584, "scale_target": "rv_fwd",
            "scale_features": ["rv_1", "rv_5", "rv_22"],
            "lgbm_params": {"n_estimators": 100},
        })
        out = node.run(ctx, {"rows": rows})
    """

    serving_load_audited = False

    _PARAMS = ScaleModelLocationScale._PARAMS + ("lgbm_params", "seed")

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
            The base's problems plus the booster knobs'.
        """
        problems = super().validate_params(params)
        seed = params.get("seed", 0)
        if not is_node_ref(seed):
            check_int_param(problems, "seed", seed, ge=0)
        extra = params.get("lgbm_params", {})
        if not isinstance(extra, dict) or any(not isinstance(k, str) for k in extra):
            return problems + [f"lgbm_params must be a dict with string keys, got {extra!r}"]
        forced = sorted(set(extra) & set(FORCED_LGBM_KEYS))
        if forced:
            problems.append(f"lgbm_params may not set {forced} — the pack forces them")
        unknown = sorted(set(extra) - set(ALLOWED_LGBM_KEYS) - set(FORCED_LGBM_KEYS))
        if unknown:
            problems.append(
                f"lgbm_params does not accept {unknown} — allowed: {list(ALLOWED_LGBM_KEYS)}"
            )
        bad = sorted(k for k, v in extra.items()
                     if k in LGBM_KEY_TYPES and not _TYPE_OK[LGBM_KEY_TYPES[k]](v))
        if bad:
            problems.append(
                f"lgbm_params {bad} have the wrong type — expected "
                f"{ {k: LGBM_KEY_TYPES[k] for k in bad} }"
            )
        return problems

    def booster_params(self):
        """Return the full, deterministic ``LGBMRegressor`` keyword set.

        Returns
        -------
        dict
            Defaults, then the document's overrides, then the forced keys.
        """
        return {
            **DEFAULT_LGBM_PARAMS, **self.params.get("lgbm_params", {}),
            "random_state": self.params.get("seed", 0), "n_jobs": 1,
            "deterministic": True, "verbosity": -1,
        }

    def described_knobs(self):
        """Return the describing knobs, the booster's included.

        Returns
        -------
        dict
            Knob name to effective value.
        """
        return {**super().described_knobs(), "booster_params": self.booster_params()}

    def fit_scale(self, x, y):
        """Fit the booster and keep its text model.

        Parameters
        ----------
        x : list of list of float
            Log features.
        y : list of float
            Log forward volatility.

        Returns
        -------
        dict
            ``{"booster": <LightGBM text model>}``.
        """
        import lightgbm
        import numpy as np

        model = lightgbm.LGBMRegressor(**self.booster_params())
        model.fit(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
        return {"booster": model.booster_.model_to_string()}

    def predict_scale(self, model, x):
        """Predict one row with the (cached) restored booster.

        Parameters
        ----------
        model : dict
            ``{"booster": <text model>}``.
        x : list of float
            One row of log features.

        Returns
        -------
        float
            The predicted log volatility.
        """
        import numpy as np

        return float(self._booster(model["booster"]).predict(np.asarray([x], dtype=float))[0])

    def _booster(self, text):
        """Restore a booster from its text once per distinct model."""
        import lightgbm

        cache = self.__dict__.setdefault("_boosters", {})
        key = hashlib.sha256(text.encode()).hexdigest()
        if key not in cache:
            cache[key] = lightgbm.Booster(model_str=text)
        return cache[key]


#: Deliberately EMPTY — wired by import path, like the numpy pack.
NODE_KINDS = ()
