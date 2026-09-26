"""Post-Gate-3 model-zoo candidate materialization and estimator wrappers."""

from __future__ import annotations

import copy
from abc import abstractmethod
import hashlib
import json
import math
import os

from dskit.pipeline.document import PipelineDocument, load_document
from dskit.pipeline.node import Node
from dskit.pipeline.stages import Stage, is_sha256hex, reject_unknown_params
from dskit.pipeline.libs.torch_ts import ZooEstimator

from intraday_equities.modelability_study import asset_walk_document
from intraday_equities.final_gates import FinalModelGateInventory, FinalModelGates

__all__ = [
    "DirectPathScore",
    "EmpiricalSelectRegressor",
    "FinalistCandidate",
    "FrozenWalkCandidate",
    "FinalModelGateInventory",
    "FinalModelGates",
    "Gate3ZooCandidates",
    "PooledDirectPathScore",
    "PooledGate3ZooCandidates",
    "KronosFusionRows",
    "MinuteWalkCandidate",
    "SequenceFusionRows",
    "SequenceOnlyZooEstimator",
    "StandardizedSelectRegressor",
    "TRADE_CACHE_PREFIX",
    "WarmupHpoCandidate",
]

_PARAMS = (
    "source_document",
    "source_document_sha256",
    "gate3_artifact",
    "gate3_sha256",
    "memory_artifact",
    "memory_sha256",
    "templates",
    "path_protocol",
)
_TEMPLATE_FIELDS = frozenset(
    {
        "id",
        "family",
        "representation",
        "feature_policy",
        "feature_source",
        "kronos",
        "sequence",
        "seed_policy",
        "compute_class",
        "compute_rank",
        "enabled",
        "prerequisite",
        "model",
        # Repo standard: `notes` is legal on every config object and the
        # canonical hash strips it at every level, so a template may carry
        # the WHY of its space without moving the identity it declares.
        "notes",
    }
)
_MODEL_FIELDS = frozenset(
    {
        "estimator",
        "estimator_params",
        "hpo_trials",
        "hpo_seed",
        "hpo_val_days",
        "hpo_embargo_days",
        "hpo_objective",
        "hpo_evidence",
        "hpo_space",
    }
)


def _string(value):
    return isinstance(value, str) and bool(value.strip())


class EmpiricalSelectRegressor:
    """Train-only univariate selection around any declared regressor."""

    def __init__(
        self,
        estimator,
        k_features=20,
        scale=False,
        **estimator_params,
    ):
        self.estimator = estimator
        self.k_features = k_features
        self.scale = scale
        self.estimator_params = dict(estimator_params)
        self._indices = None
        self._model = None

    def fit(self, x, y, feature_names=None):
        """Fit feature selection and the declared regressor on training rows."""
        import importlib

        from sklearn.feature_selection import SelectKBest, f_regression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        names = None if feature_names is None else list(feature_names)
        if names is not None and len(names) != int(x.shape[1]):
            raise ValueError("feature_names must match the design-matrix width")
        self._indices = [
            index
            for index in range(int(x.shape[1]))
            if names is None or names[index] != "symbol_code"
        ]
        if not self._indices:
            raise ValueError("no selectable features remain after exclusions")
        k = min(int(self.k_features), len(self._indices))
        if k < 1:
            raise ValueError("k_features must select at least one feature")
        module_name, _, attr = self.estimator.rpartition(".")
        if not module_name or not attr:
            raise ValueError("estimator must be a fully qualified import path")
        cls = getattr(importlib.import_module(module_name), attr)
        estimator_params = dict(self.estimator_params)
        hidden_size = estimator_params.pop("hidden_size", None)
        if hidden_size is not None:
            if self.estimator != "sklearn.neural_network.MLPRegressor":
                raise ValueError("hidden_size is only valid for MLPRegressor")
            estimator_params["hidden_layer_sizes"] = (int(hidden_size),)
        steps = [("select", SelectKBest(score_func=f_regression, k=k))]
        if self.scale:
            steps.append(("scale", StandardScaler()))
        steps.append(("model", cls(**estimator_params)))
        self._model = Pipeline(steps)
        self._model.fit(x[:, self._indices], y)
        return self

    def predict(self, x):
        """Predict with the fitted selected-feature pipeline."""
        if self._model is None:
            raise RuntimeError("regressor is not fitted")
        prediction = self._model.predict(x[:, self._indices])
        return prediction.ravel() if hasattr(prediction, "ravel") else prediction


class CategoricalRidgeRegressor:
    """Ridge over standardized tabular features plus one-hot symbols.

    The pooled design carries ``symbol_code`` as a native categorical for
    LightGBM and the embedding MLP.  This control keeps that identity nominal
    rather than treating integer codes as an ordered numeric feature.
    """

    def __init__(
        self,
        alpha=1.0,
        fit_intercept=True,
        max_iter=2000,
        tol=1e-4,
    ):
        self.alpha = alpha
        self.fit_intercept = fit_intercept
        self.max_iter = max_iter
        self.tol = tol
        self._model = None

    def fit(self, x, y, categorical_feature=None, feature_names=None):
        """Fit train-only scaling, one-hot encoding, and sparse LSQR Ridge."""
        import numpy as np
        from sklearn.compose import ColumnTransformer
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder, StandardScaler

        matrix = np.asarray(x)
        target = np.asarray(y, dtype=np.float64).reshape(-1)
        if matrix.ndim != 2 or matrix.shape[0] != target.size or not target.size:
            raise ValueError("X and y must contain the same non-empty row count")
        categorical = list(categorical_feature or [])
        if not categorical and feature_names is not None:
            categorical = [
                index
                for index, name in enumerate(feature_names)
                if name == "symbol_code"
            ]
        if len(categorical) != 1 or isinstance(categorical[0], bool):
            raise ValueError("exactly one categorical feature index is required")
        category_index = int(categorical[0])
        if category_index < 0 or category_index >= matrix.shape[1]:
            raise ValueError("categorical feature index is outside the matrix")
        continuous = [
            index for index in range(matrix.shape[1]) if index != category_index
        ]
        transform = ColumnTransformer(
            [
                ("continuous", StandardScaler(), continuous),
                (
                    "symbol",
                    OneHotEncoder(handle_unknown="ignore"),
                    [category_index],
                ),
            ],
            sparse_threshold=1.0,
        )
        self._model = Pipeline(
            [
                ("transform", transform),
                (
                    "ridge",
                    Ridge(
                        alpha=float(self.alpha),
                        fit_intercept=bool(self.fit_intercept),
                        solver="lsqr",
                        max_iter=int(self.max_iter),
                        tol=float(self.tol),
                    ),
                ),
            ]
        ).fit(matrix, target)
        return self

    def predict(self, x):
        """Return one forecast per row, refusing inference before fit."""
        if self._model is None:
            raise RuntimeError("CategoricalRidgeRegressor: predict before fit")
        return self._model.predict(x)


class StandardizedSelectRegressor:
    """Leakage-safe sklearn pipeline with train-only empirical selection."""

    def __init__(
        self,
        kind,
        k_features=20,
        alpha=1.0,
        l1_ratio=0.5,
        hidden_size=16,
        max_iter=500,
        random_state=0,
    ):
        self.kind = kind
        self.k_features = k_features
        self.alpha = alpha
        self.l1_ratio = l1_ratio
        self.hidden_size = hidden_size
        self.max_iter = max_iter
        self.random_state = random_state
        self._model = None

    def fit(self, x, y):
        """Fit the standardized selector and configured sklearn regressor."""
        from sklearn.feature_selection import SelectKBest, f_regression
        from sklearn.linear_model import ElasticNet, Ridge
        from sklearn.neural_network import MLPRegressor
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        if self.kind == "ridge":
            estimator = Ridge(alpha=float(self.alpha))
        elif self.kind == "elasticnet":
            estimator = ElasticNet(
                alpha=float(self.alpha),
                l1_ratio=float(self.l1_ratio),
                max_iter=int(self.max_iter),
                random_state=int(self.random_state),
            )
        elif self.kind == "mlp":
            estimator = MLPRegressor(
                hidden_layer_sizes=(int(self.hidden_size),),
                alpha=float(self.alpha),
                max_iter=int(self.max_iter),
                random_state=int(self.random_state),
                early_stopping=False,
            )
        else:
            raise ValueError(f"unknown standardized regressor kind {self.kind!r}")
        k = min(int(self.k_features), int(x.shape[1]))
        if k < 1:
            raise ValueError("k_features must select at least one feature")
        self._model = Pipeline(
            [
                ("scale", StandardScaler()),
                ("select", SelectKBest(score_func=f_regression, k=k)),
                ("model", estimator),
            ]
        )
        self._model.fit(x, y)
        return self

    def predict(self, x):
        """Predict with the fitted standardized pipeline."""
        if self._model is None:
            raise RuntimeError("regressor is not fitted")
        return self._model.predict(x)


class SequenceOnlyZooEstimator:
    """Use genuine return-lag history only; never broadcast static columns."""

    def __init__(self, arch, context_length=20, **knobs):
        self.arch = arch
        self.context_length = context_length
        self.knobs = dict(knobs)
        self._indices = None
        self._names = None
        self._model = None

    def fit(self, x, y, feature_names=None):
        """Fit the sequence model on contiguous return-lag features only."""
        if not isinstance(feature_names, list) or not feature_names:
            raise ValueError("SequenceOnlyZooEstimator requires feature_names")
        available = []
        for index, name in enumerate(feature_names):
            if name.startswith("ret_lag_") and name[len("ret_lag_") :].isdigit():
                available.append((int(name[len("ret_lag_") :]), index, name))
        available.sort()
        length = int(self.context_length)
        selected = [row for row in available if row[0] < length]
        if len(selected) != length or [row[0] for row in selected] != list(
            range(length)
        ):
            raise ValueError(
                f"context_length={length} requires contiguous ret_lag_0..{length - 1}"
            )
        self._indices = [row[1] for row in selected]
        self._names = [row[2] for row in selected]
        self._model = ZooEstimator(self.arch, **self.knobs)
        self._model.fit(x[:, self._indices], y, feature_names=self._names)
        return self

    def predict(self, x):
        """Predict from the fitted sequence model using its retained lags."""
        if self._model is None:
            raise RuntimeError("sequence estimator is not fitted")
        return self._model.predict(x[:, self._indices])


def _resolve(source_path, declared):
    return os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(source_path)), declared)
    )


def _read_pinned_json(source_path, declared, digest, label):
    path = _resolve(source_path, declared)
    with open(path, "rb") as handle:
        raw = handle.read()
    observed = hashlib.sha256(raw).hexdigest()
    if observed != digest:
        raise ValueError(f"{label} hash changed: {digest} -> {observed}")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    return path, value


def _gate3_rows(artifact, expected_count=25):
    outputs = artifact.get("outputs") if isinstance(artifact, dict) else None
    if not isinstance(outputs, dict):
        raise ValueError("Gate-3 artifact has no outputs object")
    rows = outputs.get("rows")
    if not isinstance(rows, list):
        raise ValueError("Gate-3 artifact has no combined rows list")
    seen = set()
    eligible = []
    for row in rows:
        if not isinstance(row, dict) or not _string(row.get("asset")):
            raise ValueError("Gate-3 row is malformed")
        if row.get("gate3_passes") is not True or row.get("gate3_status") != "pass":
            continue
        horizon = row.get("gate1_h")
        if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 1:
            raise ValueError(f"Gate-3 horizon is invalid for {row.get('asset')!r}")
        key = (row["asset"], horizon)
        if key in seen:
            raise ValueError(f"duplicate Gate-3 family {key!r}")
        seen.add(key)
        eligible.append({"asset": row["asset"], "horizon": horizon})
    if expected_count is not None and len(eligible) != expected_count:
        raise ValueError(
            "locked Gate-3 result must contain "
            f"{expected_count} passers, got {len(eligible)}"
        )
    return sorted(eligible, key=lambda row: (row["asset"], row["horizon"]))


def _groups(artifact):
    outputs = artifact.get("outputs") if isinstance(artifact, dict) else None
    groups = outputs.get("groups") if isinstance(outputs, dict) else None
    if artifact.get("state") != "ran" or not isinstance(groups, dict) or not groups:
        raise ValueError("memory artifact is not a completed group-cache record")
    return groups


def _cache_for(asset, groups):
    found = [entry for entry in groups.values() if asset in entry.get("symbols", [])]
    if len(found) != 1:
        raise ValueError(f"eligible asset {asset!r} belongs to {len(found)} caches")
    entry = found[0]
    for field in ("cache", "manifest_sha256", "universe"):
        if not _string(entry.get(field)):
            raise ValueError(f"cache for {asset!r} has no valid {field}")
    return entry


def _template_problems(template, index):
    where = f"templates[{index}]"
    if not isinstance(template, dict):
        return [f"{where} must be an object"]
    problems = []
    unknown = sorted(set(template) - _TEMPLATE_FIELDS)
    if unknown:
        problems.append(f"{where} has unknown field(s) {unknown}")
    required = _TEMPLATE_FIELDS - {
        "prerequisite",
        "kronos",
        "sequence",
        "feature_source",
        "notes",
    }
    for field in sorted(required):
        if field not in template:
            problems.append(f"{where}.{field} is required")
    for field in required - {"compute_rank", "enabled", "model"}:
        if field in template and not _string(template[field]):
            problems.append(f"{where}.{field} must be a non-empty string")
    if not isinstance(template.get("enabled"), bool):
        problems.append(f"{where}.enabled must be boolean")
    if template.get("feature_source", "tabular") not in (
        "tabular",
        "kronos",
        "sequence",
    ):
        problems.append(f"{where}.feature_source must be tabular, kronos, or sequence")
    kronos = template.get("kronos")
    if template.get("feature_source") == "kronos":
        if not isinstance(kronos, dict) or set(kronos) != _KRONOS_FIELDS:
            problems.append(
                f"{where}.kronos must contain exactly {sorted(_KRONOS_FIELDS)}"
            )
        else:
            for field in _KRONOS_FIELDS - {
                "score_period_ms",
                "batch_size",
                "feature_names",
            }:
                if not _string(kronos[field]):
                    problems.append(f"{where}.kronos.{field} must be a string")
            for field in ("score_period_ms", "batch_size"):
                value = kronos[field]
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    problems.append(f"{where}.kronos.{field} must be positive")
            names = kronos["feature_names"]
            if (
                not isinstance(names, list)
                or not names
                or any(not _string(name) for name in names)
                or len(names) != len(set(names))
            ):
                problems.append(f"{where}.kronos.feature_names must be unique strings")
    elif kronos is not None:
        problems.append(f"{where}.kronos is only valid for feature_source=kronos")
    sequence = template.get("sequence")
    if template.get("feature_source") == "sequence":
        if not isinstance(sequence, dict) or set(sequence) != _SEQUENCE_FIELDS:
            problems.append(
                f"{where}.sequence must contain exactly {sorted(_SEQUENCE_FIELDS)}"
            )
        else:
            names = sequence["feature_names"]
            if (
                not isinstance(names, list)
                or not names
                or any(not _string(name) for name in names)
                or len(names) != len(set(names))
            ):
                problems.append(
                    f"{where}.sequence.feature_names must be unique strings"
                )
    elif sequence is not None:
        problems.append(f"{where}.sequence is only valid for feature_source=sequence")
    rank = template.get("compute_rank")
    if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
        problems.append(f"{where}.compute_rank must be a positive integer")
    model = template.get("model")
    if template.get("enabled") is True:
        if not isinstance(model, dict) or not model or set(model) - _MODEL_FIELDS:
            problems.append(f"{where}.model must be a non-empty closed model object")
        elif not _string(model.get("estimator")):
            problems.append(f"{where}.model.estimator is required")
    if template.get("enabled") is False and not _string(template.get("prerequisite")):
        problems.append(f"{where}.prerequisite is required when disabled")
    return problems


def _metadata(template, candidate_id, group, horizon, weights):
    return {
        "id": candidate_id,
        "group": group,
        "family": template["family"],
        "representation": template["representation"],
        "feature_policy": template["feature_policy"],
        "seed_policy": template["seed_policy"],
        "compute_class": template["compute_class"],
        "compute_rank": template["compute_rank"],
        "enabled": template["enabled"],
        "forecast_strategy": "direct_per_lead",
        "max_horizon": horizon,
        "leads": list(range(1, horizon + 1)),
        "horizon_weights": list(weights),
    }


def _document(source, template, asset, horizon, cache, candidate_id, weights):
    obj = asset_walk_document(
        source,
        "p13-model-zoo",
        asset,
        horizon,
        cache,
        tag=template["id"],
    ).to_obj()
    pipeline = obj["pipeline"]
    base = pipeline.pop("scan")
    path_inputs = {}
    for lead in range(1, horizon + 1):
        key = f"scan_h{lead:02d}"
        node = copy.deepcopy(base)
        scan = node["params"]
        scan["lead_start"] = lead
        scan["lead_step"] = lead
        scan["lead_stop"] = lead
        scan["common_lead_stop"] = horizon
        scan["common_origin_policy"] = "all_head_labels_finite"
        for field in _MODEL_FIELDS:
            if field in template["model"]:
                scan[field] = copy.deepcopy(template["model"][field])
            else:
                scan.pop(field, None)
        pipeline[key] = node
        path_inputs[f"records_h{lead:02d}"] = "$" + f"{key}.records"
        path_inputs[f"metrics_h{lead:02d}"] = "$" + f"{key}.metrics"
    pipeline["path"] = {
        "uses": "intraday_equities.model_zoo:DirectPathScore",
        "inputs": path_inputs,
        "params": {
            "split": "val",
            "asset": asset,
            "max_horizon": horizon,
            "horizon_weights": list(weights),
            "score": "train_scaled_improvement",
        },
    }
    obj["walkforward"]["objective"] = "$path.metrics.path_score"
    obj["walkforward"]["select"] = "max"
    obj["name"] = candidate_id
    obj["notes"] = (
        "ADR-0099/0100: pinned P12 target/cache; one honest direct head "
        "per lead 1..H_i, common max-H origins, and a path-level score."
    )
    return PipelineDocument.from_obj(obj)


def _write(path, document):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(document.to_obj(), handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class DirectPathScore(Node):
    """Aggregate one stock's direct heads into a predeclared path score.

    Every input pair is produced by a separate honest lead-specific fit.
    The upstream scans enforce the common H_i outcome boundary; this
    node refuses mismatched row counts and computes an equal-origin,
    training-scale-normalized path score without reading the lockbox.

    Parameters
    ----------
    params : dict
        asset, max_horizon, explicit horizon_weights, and the score name.
    """

    role = "score"
    outputs = ("records", "metrics")
    _PARAMS = ("split", "asset", "max_horizon", "horizon_weights", "score")

    @classmethod
    def validate_params(cls, params):
        """Validate the declared direct-path scoring protocol."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        if params.get("split") != "val":
            problems.append("split must be val")
        if not _string(params.get("asset")):
            problems.append("asset must be a non-empty string")
        horizon = params.get("max_horizon")
        if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 1:
            problems.append("max_horizon must be a positive integer")
        weights = params.get("horizon_weights")
        if not isinstance(weights, list) or not weights:
            problems.append("horizon_weights must be a non-empty list")
        elif any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value < 0.0
            for value in weights
        ) or not math.isclose(sum(weights), 1.0, rel_tol=1e-12, abs_tol=1e-12):
            problems.append(
                "horizon_weights must be finite, non-negative, and sum to one"
            )
        elif isinstance(horizon, int) and len(weights) != horizon:
            problems.append("horizon_weights length must equal max_horizon")
        if params.get("score") != "train_scaled_improvement":
            problems.append("score must be train_scaled_improvement")
        return problems

    def validate_inputs(self, inputs):
        """Require one records/metrics pair for every direct lead."""
        horizon = self.params.get("max_horizon")
        if not isinstance(horizon, int) or horizon < 1:
            return []
        wanted = {
            f"{kind}_h{lead:02d}"
            for lead in range(1, horizon + 1)
            for kind in ("records", "metrics")
        }
        if not isinstance(inputs, dict) or set(inputs) != wanted:
            return [f"inputs must contain exactly {sorted(wanted)}"]
        return []

    def run(self, ctx, inputs):
        """Aggregate lead-specific validation records into a path score."""
        del ctx
        asset = self.params["asset"]
        weights = self.params["horizon_weights"]
        rows = []
        counts = set()
        origins = set()
        for lead, weight in enumerate(weights, start=1):
            records = inputs[f"records_h{lead:02d}"]
            metrics = inputs[f"metrics_h{lead:02d}"]
            if not isinstance(records, list):
                raise ValueError(f"lead {lead} records must be a list")
            matched = [
                row
                for row in records
                if row.get("symbol") == asset and row.get("lead") == lead
            ]
            if len(matched) != 1:
                raise ValueError(f"lead {lead} must contain exactly one {asset} row")
            row = copy.deepcopy(matched[0])
            score = row.get("train_scaled_improvement")
            count = row.get("n")
            if (
                not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not math.isfinite(score)
                or not isinstance(count, (int, float))
                or isinstance(count, bool)
                or count < 1
            ):
                raise ValueError(f"lead {lead} has incomplete path evidence")
            origin = row.get("origin_sha256")
            if not _string(origin) or len(origin) != 64:
                raise ValueError(f"lead {lead} has no validation-origin digest")
            counts.add(int(count))
            origins.add(origin)
            row.update(
                {
                    "weight": float(weight),
                    "train_ic": float(metrics.get("train_ic", 0.0)),
                    "val_ic": float(metrics.get("val_ic", 0.0)),
                    "train_calibration_slope": float(
                        metrics.get("train_calibration_slope", 0.0)
                    ),
                    "val_calibration_slope": float(
                        metrics.get("val_calibration_slope", 0.0)
                    ),
                }
            )
            rows.append(row)
        if len(counts) != 1 or len(origins) != 1:
            raise ValueError(
                "all path heads must score identical common validation origins"
            )
        path_score = sum(
            row["weight"] * row["train_scaled_improvement"] for row in rows
        )
        return {
            "records": rows,
            "metrics": {
                "path_score": float(path_score),
                "worst_horizon_score": float(
                    min(row["train_scaled_improvement"] for row in rows)
                ),
                "mean_val_ic": float(
                    sum(row["weight"] * row["val_ic"] for row in rows)
                ),
                "n_leads": float(len(rows)),
                "n_common_origins": float(next(iter(counts))),
            },
        }


class PooledDirectPathScore(Node):
    """Aggregate pooled direct heads with equal stock/path weighting.

    Each stock contributes one twenty-fifth of the score and divides that
    weight equally across its certified direct leads. Origins must match across
    every head belonging to the same stock; stocks need not share row counts.

    Parameters
    ----------
    params : dict
        ``asset_horizons``, ``horizon_weighting``, ``split``, and ``score``.

    Examples
    --------
    Build a scorer for one one-lead and one two-lead stock::

        node = PooledDirectPathScore("path", {
            "split": "val",
            "asset_horizons": [{"asset": "A", "horizon": 1},
                               {"asset": "B", "horizon": 2}],
            "horizon_weighting": "equal_asset_equal_within_asset",
            "score": "train_scaled_improvement",
        })
    """

    role = "score"
    outputs = ("records", "metrics")
    _PARAMS = ("split", "asset_horizons", "horizon_weighting", "score")

    @classmethod
    def validate_params(cls, params):
        """Return every malformed pooled-path parameter."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        if params.get("split") != "val":
            problems.append("split must be val")
        rows = params.get("asset_horizons")
        if not isinstance(rows, list) or not rows:
            problems.append("asset_horizons must be a non-empty list")
        else:
            assets = []
            for index, row in enumerate(rows):
                if not isinstance(row, dict) or set(row) != {"asset", "horizon"}:
                    problems.append(
                        f"asset_horizons[{index}] must contain exactly asset and horizon"
                    )
                    continue
                if not _string(row["asset"]):
                    problems.append(f"asset_horizons[{index}].asset is invalid")
                horizon = row["horizon"]
                if (
                    not isinstance(horizon, int)
                    or isinstance(horizon, bool)
                    or horizon < 1
                ):
                    problems.append(f"asset_horizons[{index}].horizon is invalid")
                assets.append(row["asset"])
            if len(assets) != len(set(assets)):
                problems.append("asset_horizons assets must be unique")
        if params.get("horizon_weighting") != "equal_asset_equal_within_asset":
            problems.append("horizon_weighting must be equal_asset_equal_within_asset")
        if params.get("score") != "train_scaled_improvement":
            problems.append("score must be train_scaled_improvement")
        return problems

    def validate_inputs(self, inputs):
        """Require one records/metrics pair for every direct lead."""
        rows = self.params.get("asset_horizons") or []
        horizon = max((row.get("horizon", 0) for row in rows), default=0)
        wanted = {
            f"{kind}_h{lead:02d}"
            for lead in range(1, horizon + 1)
            for kind in ("records", "metrics")
        }
        return (
            []
            if isinstance(inputs, dict) and set(inputs) == wanted
            else [f"inputs must contain exactly {sorted(wanted)}"]
        )

    def run(self, ctx, inputs):
        """Aggregate complete stock paths into one outer-fold score."""
        del ctx
        asset_horizons = self.params["asset_horizons"]
        n_assets = len(asset_horizons)
        output = []
        origins = {row["asset"]: set() for row in asset_horizons}
        counts = {row["asset"]: set() for row in asset_horizons}
        head_weights = {}
        for item in asset_horizons:
            asset = item["asset"]
            horizon = item["horizon"]
            weight = 1.0 / n_assets / horizon
            for lead in range(1, horizon + 1):
                matched = [
                    row
                    for row in inputs[f"records_h{lead:02d}"]
                    if row.get("symbol") == asset and row.get("lead") == lead
                ]
                if len(matched) != 1:
                    raise ValueError(
                        f"lead {lead} must contain exactly one row for {asset}"
                    )
                row = copy.deepcopy(matched[0])
                score = row.get("train_scaled_improvement")
                count = row.get("n")
                origin = row.get("origin_sha256")
                if (
                    not isinstance(score, (int, float))
                    or isinstance(score, bool)
                    or not math.isfinite(score)
                    or not isinstance(count, (int, float))
                    or isinstance(count, bool)
                    or count < 1
                    or not _string(origin)
                    or len(origin) != 64
                ):
                    raise ValueError(
                        f"{asset} lead {lead} has incomplete path evidence"
                    )
                origins[asset].add(origin)
                counts[asset].add(int(count))
                row["weight"] = weight
                output.append(row)
                head_weights[lead] = head_weights.get(lead, 0.0) + weight
        mismatched = [
            asset
            for asset in origins
            if len(origins[asset]) != 1 or len(counts[asset]) != 1
        ]
        if mismatched:
            raise ValueError(
                f"stocks have non-common validation origins across heads: {mismatched}"
            )
        path_score = sum(
            row["weight"] * row["train_scaled_improvement"] for row in output
        )
        mean_val_ic = sum(
            head_weights[lead]
            * float(inputs[f"metrics_h{lead:02d}"].get("val_ic", 0.0))
            for lead in head_weights
        )
        return {
            "records": output,
            "metrics": {
                "path_score": float(path_score),
                "worst_asset_horizon_score": float(
                    min(row["train_scaled_improvement"] for row in output)
                ),
                "mean_val_ic": float(mean_val_ic),
                "n_asset_paths": float(n_assets),
                "n_asset_heads": float(len(output)),
                "min_common_origins": float(
                    min(next(iter(values)) for values in counts.values())
                ),
            },
        }


def _pooled_horizon_weights(eligible):
    """Collapse equal-stock/equal-within-stock weights onto direct leads."""
    n_assets = len(eligible)
    horizon = max(row["horizon"] for row in eligible)
    return [
        sum(
            1.0 / n_assets / row["horizon"]
            for row in eligible
            if row["horizon"] >= lead
        )
        for lead in range(1, horizon + 1)
    ]


class KronosFusionRows(Node):
    """Align Kronos states with explicitly allowed non-OHLCV side features.

    Parameters
    ----------
    params : dict
        ``feature_names`` is the exact ordered side-feature allowlist.  The
        downstream scan adds ``symbol_code`` from its governed universe.
    """

    role = "transform"
    outputs = ("records",)
    _PARAMS = ("feature_names",)
    _cached_key = None
    _cached_records = None

    @classmethod
    def validate_params(cls, params):
        """Require one non-empty, unique string allowlist."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        names = params.get("feature_names")
        if (
            not isinstance(names, list)
            or not names
            or any(not _string(name) for name in names)
            or len(set(names)) != len(names)
        ):
            problems.append("feature_names must be unique non-empty strings")
        return problems

    def validate_inputs(self, inputs):
        """Require columnar feature and embedding frame lists."""
        if not isinstance(inputs, dict) or set(inputs) != {"features", "embeddings"}:
            return ["inputs must contain exactly features and embeddings"]
        return [
            f"{key} must be a list"
            for key in ("features", "embeddings")
            if not isinstance(inputs[key], list)
        ]

    @staticmethod
    def _signature(frames):
        return tuple(
            (
                frame.get("symbol"),
                tuple(getattr(frame.get("X"), "shape", ())),
                getattr(frame.get("X"), "filename", None),
            )
            for frame in frames
        )

    def run(self, ctx, inputs):
        """Inner-align each symbol and return one fused columnar frame."""
        import numpy as np

        del ctx
        key = (
            tuple(self.params["feature_names"]),
            self._signature(inputs["features"]),
            self._signature(inputs["embeddings"]),
        )
        cls = type(self)
        if cls._cached_key == key:
            return {"records": list(cls._cached_records)}
        features = {frame["symbol"]: frame for frame in inputs["features"]}
        embeddings = {frame["symbol"]: frame for frame in inputs["embeddings"]}
        if set(features) != set(embeddings):
            raise ValueError("feature and Kronos caches name different symbols")
        side_names = list(self.params["feature_names"])
        records = []
        for symbol in features:
            feature = features[symbol]
            embedding = embeddings[symbol]
            name_to_index = {name: index for index, name in enumerate(feature["names"])}
            missing = [name for name in side_names if name not in name_to_index]
            if missing:
                raise ValueError(f"{symbol} is missing side features {missing}")
            feature_ms = np.asarray(feature["asof_ms"], dtype=np.int64)
            embedding_ms = np.asarray(embedding["asof_ms"], dtype=np.int64)
            at = np.searchsorted(feature_ms, embedding_ms)
            if np.any(at >= len(feature_ms)) or not np.array_equal(
                feature_ms[at], embedding_ms
            ):
                raise ValueError(f"{symbol} Kronos origins do not align to features")
            side = np.asarray(feature["X"])[
                np.ix_(at, [name_to_index[name] for name in side_names])
            ]
            hidden = np.asarray(embedding["X"])
            records.append(
                {
                    "symbol": symbol,
                    "asof_ms": embedding_ms,
                    "close": np.asarray(feature["close"])[at],
                    "names": list(embedding["names"]) + side_names,
                    "X": np.column_stack([hidden, side]).astype(np.float32, copy=False),
                }
            )
        cls._cached_key = key
        cls._cached_records = records
        return {"records": list(records)}


class SequenceFusionRows(Node):
    """Align one-minute OHLCV windows with an explicit side-feature allowlist."""

    role = "transform"
    outputs = ("records",)
    _PARAMS = ("feature_names",)
    _cached_key = None
    _cached_records = None

    @classmethod
    def validate_params(cls, params):
        """Require one non-empty, unique side-feature allowlist."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        names = params.get("feature_names")
        if (
            not isinstance(names, list)
            or not names
            or any(not _string(name) for name in names)
            or len(names) != len(set(names))
        ):
            problems.append("feature_names must be unique non-empty strings")
        return problems

    def validate_inputs(self, inputs):
        """Require columnar feature and one-minute sequence frame lists."""
        if not isinstance(inputs, dict) or set(inputs) != {"features", "sequences"}:
            return ["inputs must contain exactly features and sequences"]
        return [
            f"{key} must be a list"
            for key in ("features", "sequences")
            if not isinstance(inputs[key], list)
        ]

    @staticmethod
    def _signature(frames):
        return tuple(
            (
                frame.get("symbol"),
                tuple(getattr(frame.get("X"), "shape", ())),
                getattr(frame.get("X"), "filename", None),
            )
            for frame in frames
        )

    def run(self, ctx, inputs):
        """Inner-align sequence origins and return fused columnar frames."""
        import numpy as np

        del ctx
        key = (
            tuple(self.params["feature_names"]),
            self._signature(inputs["features"]),
            self._signature(inputs["sequences"]),
        )
        cls = type(self)
        if cls._cached_key == key:
            return {"records": list(cls._cached_records)}
        features = {frame["symbol"]: frame for frame in inputs["features"]}
        sequences = {frame["symbol"]: frame for frame in inputs["sequences"]}
        if set(features) != set(sequences):
            raise ValueError("feature and sequence caches name different symbols")
        side_names = list(self.params["feature_names"])
        records = []
        for symbol in features:
            feature = features[symbol]
            sequence = sequences[symbol]
            name_to_index = {name: index for index, name in enumerate(feature["names"])}
            missing = [name for name in side_names if name not in name_to_index]
            if missing:
                raise ValueError(f"{symbol} is missing side features {missing}")
            feature_ms = np.asarray(feature["asof_ms"], dtype=np.int64)
            sequence_ms = np.asarray(sequence["asof_ms"], dtype=np.int64)
            at = np.searchsorted(feature_ms, sequence_ms)
            if np.any(at >= len(feature_ms)) or not np.array_equal(
                feature_ms[at], sequence_ms
            ):
                raise ValueError(f"{symbol} sequence origins do not align to features")
            side = np.asarray(feature["X"])[
                np.ix_(at, [name_to_index[name] for name in side_names])
            ]
            records.append(
                {
                    "symbol": symbol,
                    "asof_ms": sequence_ms,
                    "close": np.asarray(feature["close"])[at],
                    "names": list(sequence["names"]) + side_names,
                    "X": np.column_stack([np.asarray(sequence["X"]), side]).astype(
                        np.float32, copy=False
                    ),
                }
            )
        cls._cached_key = key
        cls._cached_records = records
        return {"records": list(records)}


def _pooled_document(source, template, eligible, caches, candidate_id):
    """Materialize one pooled candidate over verified group feature caches."""
    obj = source.to_obj()
    obj.pop("stages", None)
    base = copy.deepcopy(obj["pipeline"]["scan"])
    assets = [row["asset"] for row in eligible]
    horizon = max(row["horizon"] for row in eligible)
    residual = base["params"].get("label_residual")
    pipeline = {
        "universe": copy.deepcopy(obj["pipeline"]["universe"]),
    }
    feature_inputs = {}
    tape_inputs = {}
    kline_inputs = {}
    sequence_inputs = {}
    feature_source = template.get("feature_source", "tabular")
    use_kronos = feature_source == "kronos"
    use_sequence = feature_source == "sequence"
    for index, (group, cache) in enumerate(caches.items()):
        group_assets = [asset for asset in assets if asset in cache["symbols"]]
        feature_key = f"features_{group}"
        selected_key = f"pooled_features_{group}"
        tape_key = f"pooled_tape_{group}"
        pipeline[feature_key] = {
            "uses": "intraday_equities-session-feature-cache",
            "params": {
                "path": cache["cache"],
                "manifest_sha256": cache["manifest_sha256"],
            },
        }
        pipeline[selected_key] = {
            "uses": "filter",
            "inputs": {"records": f"${feature_key}.records"},
            "params": {
                "where": [{"field": "symbol", "op": "in", "value": group_assets}]
            },
        }
        tape_symbols = list(group_assets)
        if index == 0 and residual not in tape_symbols:
            tape_symbols.append(residual)
        pipeline[tape_key] = {
            "uses": "filter",
            "inputs": {"records": f"${feature_key}.tape"},
            "params": {
                "where": [{"field": "symbol", "op": "in", "value": tape_symbols}]
            },
        }
        feature_inputs[group] = f"${selected_key}.records"
        tape_inputs[group] = f"${tape_key}.records"
        if use_kronos:
            kline_key = f"pooled_klines_{group}"
            pipeline[kline_key] = {
                "uses": "filter",
                "inputs": {"records": f"${feature_key}.klines"},
                "params": {
                    "where": [{"field": "symbol", "op": "in", "value": group_assets}]
                },
            }
            kline_inputs[group] = f"${kline_key}.records"
        if use_sequence:
            sequence_key = f"pooled_sequences_{group}"
            pipeline[sequence_key] = {
                "uses": "filter",
                "inputs": {"records": f"${feature_key}.sequences"},
                "params": {
                    "where": [{"field": "symbol", "op": "in", "value": group_assets}]
                },
            }
            sequence_inputs[group] = f"${sequence_key}.records"
    concat_params = {
        "shape": "records",
        "provenance_waiver": (
            "Immutable cache rows retain symbol identity; selected group symbol "
            "namespaces are disjoint."
        ),
        "key": "symbol",
        "consume_inputs": True,
    }
    pipeline["pooled_features"] = {
        "uses": "concat",
        "inputs": feature_inputs,
        "params": copy.deepcopy(concat_params),
    }
    pipeline["reference_tape"] = {
        "uses": "concat",
        "inputs": tape_inputs,
        "params": copy.deepcopy(concat_params),
    }
    scan_records = "$pooled_features.merged"
    if use_kronos:
        pipeline["pooled_klines"] = {
            "uses": "concat",
            "inputs": kline_inputs,
            "params": copy.deepcopy(concat_params),
        }
        kronos = copy.deepcopy(template["kronos"])
        side_names = kronos.pop("feature_names")
        kronos["input_identity"] = [
            caches[group]["manifest_sha256"] for group in caches
        ]
        pipeline["kronos"] = {
            "uses": "dskit.pipeline.libs.kronos:KronosHiddenState",
            "inputs": {"records": "$pooled_klines.merged"},
            "params": kronos,
        }
        pipeline["fusion_features"] = {
            "uses": "intraday_equities.model_zoo:KronosFusionRows",
            "inputs": {
                "features": "$pooled_features.merged",
                "embeddings": "$kronos.records",
            },
            "params": {"feature_names": side_names},
        }
        scan_records = "$fusion_features.records"
    if use_sequence:
        pipeline["pooled_sequences"] = {
            "uses": "concat",
            "inputs": sequence_inputs,
            "params": copy.deepcopy(concat_params),
        }
        side_names = copy.deepcopy(template["sequence"]["feature_names"])
        pipeline["fusion_features"] = {
            "uses": "intraday_equities.model_zoo:SequenceFusionRows",
            "inputs": {
                "features": "$pooled_features.merged",
                "sequences": "$pooled_sequences.merged",
            },
            "params": {"feature_names": side_names},
        }
        scan_records = "$fusion_features.records"
    path_inputs = {}
    for lead in range(1, horizon + 1):
        key = f"scan_h{lead:02d}"
        node = copy.deepcopy(base)
        node["inputs"] = {
            "records": scan_records,
            "bars": "$reference_tape.merged",
            "spec": "$universe.spec",
        }
        params = node["params"]
        params["fit_symbols"] = list(assets)
        params["score_symbols"] = [
            row["asset"] for row in eligible if row["horizon"] >= lead
        ]
        params["lead_start"] = lead
        params["lead_step"] = lead
        params["lead_stop"] = lead
        params["common_lead_stop"] = horizon
        params["common_origin_policy"] = "all_head_labels_finite"
        for field in _MODEL_FIELDS:
            if field in template["model"]:
                params[field] = copy.deepcopy(template["model"][field])
            else:
                params.pop(field, None)
        pipeline[key] = node
        path_inputs[f"records_h{lead:02d}"] = f"${key}.records"
        path_inputs[f"metrics_h{lead:02d}"] = f"${key}.metrics"
    pipeline["path"] = {
        "uses": "intraday_equities.model_zoo:PooledDirectPathScore",
        "inputs": path_inputs,
        "params": {
            "split": "val",
            "asset_horizons": copy.deepcopy(eligible),
            "horizon_weighting": "equal_asset_equal_within_asset",
            "score": "train_scaled_improvement",
        },
    }
    obj["pipeline"] = pipeline
    # A zoo source carries a walk-forward and the candidate inherits it. A
    # FINALIST source carries none (`final_hpo` declares no fold schedule),
    # and `to_obj` omits the section rather than emitting a null, so its
    # absence is the single fit the calendar phase asks for.
    if "walkforward" in obj:
        obj["walkforward"]["objective"] = "$path.metrics.path_score"
        obj["walkforward"]["select"] = "max"
    obj["name"] = candidate_id
    obj["notes"] = (
        "ADR-0104: session-local one-minute OHLCV enters a recurrent tower and "
        "the declared side-feature allowlist enters a separate projection."
        if use_sequence
        else (
            "ADR-0101/0102: one pooled fit per direct lead over all 25 Gate-3 "
            "passers; optional frozen Kronos states are fused only with the "
            "declared side-feature allowlist."
        )
    )
    return PipelineDocument.from_obj(obj)


_POOLED_PARAMS = (
    "gate3_artifact",
    "gate3_sha256",
    "gate3_artifacts",
    "expected_eligible_count",
    "cache_groups",
    "templates",
    "path_protocol",
)

_KRONOS_FIELDS = frozenset(
    {
        "source_root",
        "source_revision",
        "onboarding_root",
        "tokenizer_snapshot",
        "model_snapshot",
        "cache_dir",
        "score_period_ms",
        "batch_size",
        "device",
        "dtype",
        "timezone",
        "encoder_contract",
        "feature_names",
    }
)

_SEQUENCE_FIELDS = frozenset({"feature_names"})


class PooledGate3ZooCandidates(Stage):
    """Materialize pooled candidates over verified group caches.

    Parameters
    ----------
    params : dict
        Pinned Gate-3 artifact, ordered cache groups, templates, and protocol.

    Examples
    --------
    Construct from a complete config-owned parameter block::

        params = document.stages["materialize"].params
        stage = PooledGate3ZooCandidates("materialize", params)
    """

    outputs = ("candidates", "eligibility", "provenance")

    @classmethod
    def validate_params(cls, params):
        """Return every malformed provenance, template, and protocol field."""
        problems = []
        reject_unknown_params(problems, params, _POOLED_PARAMS)
        single = (
            params.get("gate3_artifact") is not None
            or params.get("gate3_sha256") is not None
        )
        many = params.get("gate3_artifacts") is not None
        if single == many:
            problems.append(
                "declare exactly one of gate3_artifact/gate3_sha256 or gate3_artifacts"
            )
        if single:
            for field in ("gate3_artifact", "gate3_sha256"):
                if not _string(params.get(field)):
                    problems.append(f"{field} must be a non-empty string")
        if many:
            artifacts = params.get("gate3_artifacts")
            if not isinstance(artifacts, list) or not artifacts:
                problems.append("gate3_artifacts must be a non-empty list")
            else:
                paths = []
                for index, artifact in enumerate(artifacts):
                    if not isinstance(artifact, dict) or set(artifact) != {
                        "path",
                        "sha256",
                    }:
                        problems.append(
                            f"gate3_artifacts[{index}] must contain exactly path and sha256"
                        )
                        continue
                    if not _string(artifact["path"]):
                        problems.append(f"gate3_artifacts[{index}].path is invalid")
                    artifact_digest = artifact["sha256"]
                    if (
                        not _string(artifact_digest)
                        or len(artifact_digest) != 64
                        or any(
                            char not in "0123456789abcdef" for char in artifact_digest
                        )
                    ):
                        problems.append(f"gate3_artifacts[{index}].sha256 is invalid")
                    paths.append(artifact["path"])
                if len(paths) != len(set(paths)):
                    problems.append("gate3_artifacts paths must be unique")
        expected = params.get("expected_eligible_count", 25)
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
            problems.append("expected_eligible_count must be a positive integer")
        groups = params.get("cache_groups")
        if (
            not isinstance(groups, list)
            or not groups
            or any(not _string(group) for group in groups)
            or len(set(groups)) != len(groups)
        ):
            problems.append("cache_groups must be a non-empty list of unique strings")
        digest = params.get("gate3_sha256")
        if _string(digest) and (
            len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)
        ):
            problems.append("gate3_sha256 must be a lowercase SHA-256")
        templates = params.get("templates")
        if not isinstance(templates, list) or not templates:
            problems.append("templates must be a non-empty list")
        else:
            ids = []
            for index, template in enumerate(templates):
                problems.extend(_template_problems(template, index))
                if isinstance(template, dict) and _string(template.get("id")):
                    ids.append(template["id"])
            if len(ids) != len(set(ids)):
                problems.append("template ids must be unique")
        if params.get("path_protocol") != {
            "forecast_strategy": "direct_per_lead",
            "horizon_weighting": "equal_asset_equal_within_asset",
            "fit_universe": "all_gate3_passers_each_lead",
            "score_universe": "certified_horizon_includes_lead",
        }:
            problems.append("path_protocol must declare the supported pooled path")
        return problems

    def validate_inputs(self, inputs):
        """Require the passed memory gate and its verified caches."""
        if not isinstance(inputs, dict) or set(inputs) != {"preflight", "caches"}:
            return ["inputs must contain exactly preflight and caches"]
        if inputs["preflight"] is not True:
            return ["preflight must pass before candidate materialization"]
        if not isinstance(inputs["caches"], dict):
            return ["caches must materialize as an object"]
        return []

    def resolve(self, ctx, inputs):
        """Pinned eligibility and verified caches, with the pooled geometry.

        Shared with :class:`FinalistCandidate`, which materializes one
        document rather than many: the Gate-3 pin, the cache membership
        rules and the lead weighting are the same facts for both, and a
        second copy of them would drift.

        Parameters
        ----------
        ctx : NodeContext
            The stage context; ``document`` supplies the residual reference.
        inputs : dict
            The passed ``preflight`` gate and the ``caches`` it verified.

        Returns
        -------
        tuple
            ``(gate3_path, eligible, caches, horizon, weights)``.

        Raises
        ------
        ValueError
            An absent or incomplete cache group, an asset that belongs to
            none or several of them, or a first group missing the residual.
        """
        declarations = self.params.get("gate3_artifacts")
        if declarations is None:
            declarations = [
                {
                    "path": self.params["gate3_artifact"],
                    "sha256": self.params["gate3_sha256"],
                }
            ]
        gate3_sources = []
        eligible = []
        for index, declaration in enumerate(declarations):
            gate3_path, gate3 = _read_pinned_json(
                ctx.source_path,
                declaration["path"],
                declaration["sha256"],
                f"Gate-3 artifact {index + 1}",
            )
            gate3_sources.append({"path": gate3_path, "sha256": declaration["sha256"]})
            eligible.extend(_gate3_rows(gate3, expected_count=None))
        assets = [row["asset"] for row in eligible]
        if len(assets) != len(set(assets)):
            duplicates = sorted(
                asset for asset in set(assets) if assets.count(asset) > 1
            )
            raise ValueError(f"duplicate Gate-3 assets across artifacts: {duplicates}")
        expected = self.params.get("expected_eligible_count", 25)
        if len(eligible) != expected:
            raise ValueError(
                f"locked Gate-3 result must contain {expected} passers, "
                f"got {len(eligible)}"
            )
        eligible.sort(key=lambda row: (row["asset"], row["horizon"]))
        groups = self.params["cache_groups"]
        caches = {}
        for group in groups:
            cache = inputs["caches"].get(group)
            if not isinstance(cache, dict):
                raise ValueError(f"pooled cache group {group!r} is absent")
            for field in ("cache", "manifest_sha256", "universe", "symbols"):
                if field not in cache:
                    raise ValueError(f"pooled cache group {group!r} has no {field}")
            caches[group] = cache
        residual = ctx.document.pipeline["scan"].params.get("label_residual")
        assets = {row["asset"] for row in eligible}
        for asset in assets:
            membership = [
                group for group, cache in caches.items() if asset in cache["symbols"]
            ]
            if len(membership) != 1:
                raise ValueError(
                    f"pooled asset {asset!r} belongs to {len(membership)} selected caches"
                )
        if residual not in caches[groups[0]]["symbols"]:
            raise ValueError(
                "the first pooled cache does not contain the residual reference"
            )
        horizon = max(row["horizon"] for row in eligible)
        weights = _pooled_horizon_weights(eligible)
        return gate3_sources, eligible, caches, horizon, weights

    def run(self, ctx, inputs):
        """Pin eligibility and write one candidate document per template."""
        gate3_sources, eligible, caches, horizon, weights = self.resolve(ctx, inputs)
        candidates = []
        root = os.path.join(ctx.artifact_dir, "candidate-documents")
        for template in self.params["templates"]:
            candidate_id = f"{template['id']}-pooled-h{horizon:02d}"
            metadata = _metadata(
                template, candidate_id, "pooled-gate3", horizon, weights
            )
            if not template["enabled"]:
                candidates.append(
                    {**metadata, "prerequisite": template["prerequisite"]}
                )
                continue
            document = _pooled_document(
                ctx.document, template, eligible, caches, candidate_id
            )
            path = os.path.join(root, candidate_id + ".json")
            _write(path, document)
            candidates.append({**metadata, "path": path})
        return {
            "candidates": candidates,
            "eligibility": eligible,
            "provenance": {
                "gate3_artifacts": gate3_sources,
                "caches": [
                    {
                        "group": group,
                        "cache": cache["cache"],
                        "manifest_sha256": cache["manifest_sha256"],
                        "universe_sha256": cache["universe_sha256"],
                    }
                    for group, cache in caches.items()
                ],
                "eligible_count": len(eligible),
                "candidate_count": len(candidates),
            },
        }


class FinalistCandidate(PooledGate3ZooCandidates):
    """Materialize the ONE finalist document the cross-zoo selector named.

    The `final_hpo` phase fits a single finalist rather than a field, so
    this subclass changes exactly two things about its parent and inherits
    everything else: WHICH template is built (the one the selector named,
    never a name this document restates) and HOW MANY (one).

    The document it emits carries no walk-forward, because the phase
    declares no fold schedule: its window is the source document's own
    ``splits`` block, which is the calendar's finalist cut. Selection is
    therefore not re-litigated here and the window is not re-derived here.

    A selector naming a candidate this document holds no recipe for is a
    REFUSAL, by name and with the recipes it does hold — the alternative is
    training whatever happens to be first and calling it the winner.

    Parameters
    ----------
    params : dict
        :class:`PooledGate3ZooCandidates`' block exactly: the pinned Gate-3
        artifact and digest, the ordered cache groups, the templates (one
        per candidate this document can train), and the path protocol.

    Examples
    --------
    Construct from a complete config-owned parameter block::

        params = document.stages["finalist"].params
        stage = FinalistCandidate("finalist", params)
    """

    outputs = ("candidate", "eligibility", "provenance")

    def validate_inputs(self, inputs):
        """Require the memory gate, its caches, and the selector's choice."""
        if not isinstance(inputs, dict) or set(inputs) != {
            "preflight",
            "caches",
            "selection",
        }:
            return ["inputs must contain exactly preflight, caches and selection"]
        if inputs["preflight"] is not True:
            return ["preflight must pass before finalist materialization"]
        if not isinstance(inputs["caches"], dict):
            return ["caches must materialize as an object"]
        selection = inputs["selection"]
        if not isinstance(selection, dict):
            return ["selection must be the selector's object"]
        if not _string(selection.get("candidate")):
            return ["selection.candidate must name the selected candidate"]
        if selection.get("auto_promote") is not False:
            return ["selection.auto_promote must be False — this phase never promotes"]
        return []

    def run(self, ctx, inputs):
        """Write the selected candidate's finalist document, and only it."""
        gate3_sources, eligible, caches, horizon, weights = self.resolve(ctx, inputs)
        chosen = inputs["selection"]["candidate"]
        recipes = {
            f"{template['id']}-pooled-h{horizon:02d}": template
            for template in self.params["templates"]
        }
        template = recipes.get(chosen)
        if template is None:
            raise ValueError(
                f"the selector named {chosen!r} and this document declares no "
                f"finalist recipe for it; declared: {sorted(recipes)}"
            )
        if not template["enabled"]:
            raise ValueError(
                f"the selector named {chosen!r} and its template is disabled "
                f"pending: {template['prerequisite']}"
            )
        document = _pooled_document(ctx.document, template, eligible, caches, chosen)
        path = os.path.join(ctx.artifact_dir, "finalist-document", chosen + ".json")
        _write(path, document)
        metadata = _metadata(template, chosen, "pooled-gate3", horizon, weights)
        return {
            "candidate": {**metadata, "path": path},
            "eligibility": eligible,
            "provenance": {
                "gate3_artifacts": gate3_sources,
                "selected_by": inputs["selection"].get("decision_metric"),
                "selection_direction": inputs["selection"].get("select"),
                "declared_recipes": sorted(recipes),
                "caches": [
                    {
                        "group": group,
                        "cache": cache["cache"],
                        "manifest_sha256": cache["manifest_sha256"],
                        "universe_sha256": cache["universe_sha256"],
                    }
                    for group, cache in caches.items()
                ],
                "eligible_count": len(eligible),
            },
        }



#: The calendar fold-schedule fields a retraining walk must reproduce exactly.
_SCHEDULE_FIELDS = ("first", "step_days", "count", "val_days", "embargo_days", "train_days")
_HPO_FIELDS = tuple(sorted(field for field in _MODEL_FIELDS if field.startswith("hpo_")))


class _SingleRecipeCandidate(PooledGate3ZooCandidates):
    """Materialize ONE document from the ONE declared recipe, shaped by a subclass (ADR-0185).

    The parent's pinned Gate-3 eligibility, cache membership and pooled
    geometry are reused unchanged (:meth:`PooledGate3ZooCandidates.resolve`);
    this class adds the checks both retraining stages share -- exactly one
    enabled recipe, and a source walk-forward that is the locked calendar
    schedule field for field -- and leaves one hook, :meth:`_shape`, for
    what each stage changes about the document.
    """

    outputs = ("candidate", "eligibility", "provenance")
    _EXTRA_INPUTS = ()
    _SUFFIX = ""

    @classmethod
    def validate_params(cls, params):
        """Return the parent's problems plus a refusal of anything but one enabled recipe."""
        problems = super().validate_params(params)
        templates = params.get("templates")
        if isinstance(templates, list) and len(templates) != 1:
            problems.append("templates must declare exactly one recipe")
        elif isinstance(templates, list) and isinstance(templates[0], dict):
            if templates[0].get("enabled") is not True:
                problems.append("the one recipe must be enabled")
            problems.extend(cls._recipe_problems(templates[0].get("model") or {}))
        return problems

    @classmethod
    def _recipe_problems(cls, model):
        """Problems with the recipe's model block specific to this stage."""
        del model
        return []

    def validate_inputs(self, inputs):
        """Require the memory gate, its caches, the calendar phase and this stage's extra inputs."""
        wanted = {"preflight", "caches", "phase", *self._EXTRA_INPUTS}
        if not isinstance(inputs, dict) or set(inputs) != wanted:
            return [f"inputs must contain exactly {sorted(wanted)}"]
        if inputs["preflight"] is not True:
            return ["preflight must pass before candidate materialization"]
        if not isinstance(inputs["caches"], dict):
            return ["caches must materialize as an object"]
        phase = inputs["phase"]
        if not isinstance(phase, dict) or not isinstance(phase.get("walkforward"), dict):
            return ["phase must be the calendar phase with its walkforward schedule"]
        return []

    def run(self, ctx, inputs):
        """Write the shaped document of the one recipe; refuse a walk that is not the calendar's."""
        self._check_phase(ctx.asof, inputs["phase"])
        self._check_schedule(ctx.document, inputs["phase"]["walkforward"])
        gate3_sources, eligible, caches, horizon, weights = self.resolve(ctx, inputs)
        template = self.params["templates"][0]
        candidate_id = f"{template['id']}-pooled-h{horizon:02d}-{self._SUFFIX}"
        document = _pooled_document(ctx.document, template, eligible, caches, candidate_id)
        obj = self._shape(document.to_obj(), template, inputs)
        document = PipelineDocument.from_obj(obj)
        path = os.path.join(ctx.artifact_dir, f"{self.key}-document", candidate_id + ".json")
        _write(path, document)
        metadata = _metadata(template, candidate_id, "pooled-gate3", horizon, weights)
        return {
            "candidate": {**metadata, "path": path, "document_hash": document.hash},
            "eligibility": eligible,
            "provenance": {
                "gate3_artifacts": gate3_sources,
                "caches": [
                    {"group": group, "cache": cache["cache"],
                     "manifest_sha256": cache["manifest_sha256"],
                     "universe_sha256": cache["universe_sha256"]}
                    for group, cache in caches.items()
                ],
                "eligible_count": len(eligible),
            },
        }

    @staticmethod
    def _check_phase(asof, phase):
        """Refuse a phase that forbids fitting or selection, or an asof past its limit.

        The same gates ``BenchmarkPlan`` applies to a zoo, applied here because
        this chain fits and selects hyperparameters without one.
        """
        for gate in ("fit_allowed", "selection_allowed"):
            if phase.get(gate) is not True:
                raise ValueError(f"calendar phase {phase.get('key')!r} does not set {gate}")
        if asof > phase.get("latest_asof", ""):
            raise ValueError(
                f"asof {asof} exceeds calendar phase {phase.get('key')!r} latest_asof "
                f"{phase.get('latest_asof')!r}"
            )

    @staticmethod
    def _check_schedule(document, schedule):
        """Refuse a source walk-forward that differs from the locked calendar schedule."""
        walk = document.walkforward.to_obj() if document.walkforward is not None else {}
        changed = [field for field in _SCHEDULE_FIELDS if walk.get(field) != schedule.get(field)]
        if changed:
            raise ValueError(
                f"the document's walk-forward differs from the calendar schedule in {changed}"
            )

    @abstractmethod
    def _shape(self, obj, template, inputs):
        """Return the candidate document object this stage emits."""


class WarmupHpoCandidate(_SingleRecipeCandidate):
    """The warmup HPO document: the locked recipe's search on the schedule's FIRST fold only (ADR-0185).

    The recipe must run the evidence-mode search (``hpo_evidence`` true,
    ``hpo_objective`` ``squared_error_improvement``): the one-standard-error
    ruling over a complete trial ledger is what :class:`FrozenWinners`
    re-derives. The walk keeps the calendar's ``val_days``,
    ``embargo_days`` and ``train_days`` and fixes ``folds`` to the first
    cutoff, so the search reads only that fold's training window.

    Parameters
    ----------
    params : dict
        :class:`PooledGate3ZooCandidates`' block with exactly one template.

    Examples
    --------
    ::

        params = document.stages["hpo_document"].params
        stage = WarmupHpoCandidate("hpo_document", params)
    """

    _SUFFIX = "warmup-hpo"

    @classmethod
    def _recipe_problems(cls, model):
        """Require the evidence-mode, squared-error-improvement search."""
        problems = []
        if model.get("hpo_evidence") is not True:
            problems.append("the recipe must set hpo_evidence true (the one-standard-error ledger)")
        if model.get("hpo_objective") != "squared_error_improvement":
            problems.append("the recipe's hpo_objective must be squared_error_improvement")
        return problems

    def _shape(self, obj, template, inputs):
        """Narrow the walk to the first cutoff."""
        del template
        schedule = inputs["phase"]["walkforward"]
        walk = {key: value for key, value in obj["walkforward"].items()
                if key not in ("first", "step_days", "count")}
        walk["folds"] = [schedule["first"]]
        obj["walkforward"] = walk
        return obj


class FrozenWalkCandidate(_SingleRecipeCandidate):
    """The retraining document: every release refit with its lead's FROZEN winner, no search (ADR-0185).

    Each ``scan_hNN`` loses every ``hpo_*`` knob and its ``estimator_params``
    become the recipe base merged with that lead's winner. A winner set that
    misses or adds a lead, or names a knob outside the recipe's
    ``hpo_space``, refuses. The walk is the calendar schedule unchanged.

    Parameters
    ----------
    params : dict
        :class:`PooledGate3ZooCandidates`' block with exactly one template.

    Examples
    --------
    ::

        params = document.stages["walk_document"].params
        stage = FrozenWalkCandidate("walk_document", params)
    """

    _EXTRA_INPUTS = ("winners",)
    _SUFFIX = "frozen-walk"

    def _shape(self, obj, template, inputs):
        """Apply each lead's winner and drop the search."""
        winners = inputs["winners"]
        space = template["model"]["hpo_space"]
        scans = sorted(key for key in obj["pipeline"] if key.startswith("scan_h"))
        if not isinstance(winners, dict) or sorted(winners) != scans:
            raise ValueError(
                f"winners cover {sorted(winners) if isinstance(winners, dict) else winners!r}; "
                f"the document's leads are {scans}"
            )
        base = template["model"]["estimator_params"]
        for key in scans:
            outside = sorted(set(winners[key]) - set(space))
            if outside:
                raise ValueError(f"{key}'s winner names knob(s) {outside} outside the recipe's hpo_space")
            params = obj["pipeline"][key]["params"]
            for field in _HPO_FIELDS:
                params.pop(field, None)
            params["estimator_params"] = {**copy.deepcopy(base), **copy.deepcopy(winners[key])}
        return obj


#: Node-key prefix of a minute walk's per-group trade-cache nodes (ADR-0186).
#: The one owner: the publisher reads it to tell a trade cache from the
#: scored caches whose tapes carry the label.
TRADE_CACHE_PREFIX = "trade_features_"
_TRADE_CACHE_FIELDS = ("universe", "universe_sha256", "symbols")


class MinuteWalkCandidate(FrozenWalkCandidate):
    """The frozen retraining walk that ALSO predicts every validation minute (ADR-0186).

    :class:`FrozenWalkCandidate`'s document, unchanged, plus per group a
    trade-cache node (``trade_features_<group>``, the
    :class:`~intraday_equities.modelability_study.TradeFeatureCaches`
    cache), the group's own symbol filter (``trade_<group>``, the scored
    filter's params), one ``trade_features`` concat, and
    ``trade_records: $trade_features.merged`` on every ``scan_hNN``. Each
    scan then writes its per-minute trade predictions beside its scored
    lattice rows. The trade caches must be the scored groups: the same
    group keys, universes and memberships, each with its ``row_window``.

    Parameters
    ----------
    params : dict
        :class:`FrozenWalkCandidate`'s.

    Examples
    --------
    ::

        stage = MinuteWalkCandidate("walk_document", document.stages["walk_document"].params)
        stage.run(ctx, {"preflight": True, "caches": groups, "phase": phase,
                        "winners": winners, "trade_caches": trade_groups})
    """

    _EXTRA_INPUTS = ("winners", "trade_caches")
    _SUFFIX = "minute-walk"

    def run(self, ctx, inputs):
        """Emit the minute walk document; its provenance names the trade caches."""
        out = super().run(ctx, inputs)
        out["provenance"]["trade_caches"] = [
            {"group": group, "cache": entry["cache"], "manifest_sha256": entry["manifest_sha256"],
             "row_window": list(entry["row_window"])}
            for group, entry in inputs["trade_caches"].items()
        ]
        return out

    def _shape(self, obj, template, inputs):
        """Apply the frozen winners, then wire the per-minute trade rows into every scan."""
        obj = super()._shape(obj, template, inputs)
        trade = self._checked_trade_caches(inputs["trade_caches"], inputs["caches"])
        pipeline = obj["pipeline"]
        concat = {}
        for group, entry in trade.items():
            cache_key = f"{TRADE_CACHE_PREFIX}{group}"
            pipeline[cache_key] = {
                "uses": "intraday_equities-session-feature-cache",
                "params": {"path": entry["cache"], "manifest_sha256": entry["manifest_sha256"]},
            }
            pipeline[f"trade_{group}"] = {
                "uses": "filter",
                "inputs": {"records": f"${cache_key}.records"},
                "params": copy.deepcopy(pipeline[f"pooled_features_{group}"]["params"]),
            }
            concat[group] = f"$trade_{group}.records"
        pipeline["trade_features"] = {
            "uses": "concat",
            "inputs": concat,
            "params": copy.deepcopy(pipeline["pooled_features"]["params"]),
        }
        for key in sorted(k for k in pipeline if k.startswith("scan_h")):
            pipeline[key]["inputs"]["trade_records"] = "$trade_features.merged"
        return obj

    @staticmethod
    def _checked_trade_caches(trade, caches):
        """Refuse trade caches that are not exactly the scored groups."""
        if not isinstance(trade, dict) or list(trade) != list(caches):
            raise ValueError(
                f"trade_caches groups {list(trade) if isinstance(trade, dict) else trade!r} "
                f"are not the scored groups {list(caches)}"
            )
        for group, entry in trade.items():
            for field in _TRADE_CACHE_FIELDS:
                if entry.get(field) != caches[group].get(field):
                    raise ValueError(f"trade cache {group} {field} differs from the scored group's")
            window = entry.get("row_window")
            if (
                not isinstance(window, list) or len(window) != 2
                or any(isinstance(v, bool) or not isinstance(v, int) for v in window)
                or window[0] >= window[1]
            ):
                raise ValueError(f"trade cache {group} row_window must be [start_ms, end_ms], got {window!r}")
            if not _string(entry.get("cache")) or not is_sha256hex(entry.get("manifest_sha256")):
                raise ValueError(f"trade cache {group} needs a cache path and a sha256 manifest_sha256")
        return trade


class Gate3ZooCandidates(Stage):
    """Expand inline model templates across the 25 pinned Gate-3 passers."""

    outputs = ("candidates", "eligibility", "provenance")

    @classmethod
    def validate_params(cls, params):
        """Validate the pinned sources, protocol, and model templates."""
        problems = []
        reject_unknown_params(problems, params, _PARAMS)
        for field in _PARAMS[:6]:
            if not _string(params.get(field)):
                problems.append(f"{field} must be a non-empty string")
        protocol = params.get("path_protocol")
        if protocol != {
            "forecast_strategy": "direct_per_lead",
            "horizon_weighting": "equal",
            "primary_metric": "train_scaled_improvement",
            "common_origins": True,
        }:
            problems.append(
                "path_protocol must declare the supported direct equal-weight path"
            )
        templates = params.get("templates")
        if not isinstance(templates, list) or not templates:
            problems.append("templates must be a non-empty list")
        else:
            ids = []
            for index, template in enumerate(templates):
                problems.extend(_template_problems(template, index))
                if isinstance(template, dict) and _string(template.get("id")):
                    ids.append(template["id"])
            if len(ids) != len(set(ids)):
                problems.append("template ids must be unique")
        return problems

    def validate_inputs(self, inputs):
        """Reject inputs because this stage reads only pinned declarations."""
        return [] if inputs == {} else ["Gate3ZooCandidates takes no inputs"]

    def run(self, ctx, inputs):
        """Materialize candidates for the verified Gate-3 eligible set."""
        del inputs
        source_path = _resolve(ctx.source_path, self.params["source_document"])
        source = load_document(source_path)
        if source.hash != self.params["source_document_sha256"]:
            raise ValueError(
                f"source document hash changed: {self.params['source_document_sha256']} -> {source.hash}"
            )
        gate3_path, gate3 = _read_pinned_json(
            ctx.source_path,
            self.params["gate3_artifact"],
            self.params["gate3_sha256"],
            "Gate-3 artifact",
        )
        memory_path, memory = _read_pinned_json(
            ctx.source_path,
            self.params["memory_artifact"],
            self.params["memory_sha256"],
            "memory artifact",
        )
        eligible = _gate3_rows(gate3)
        groups = _groups(memory)
        candidates = []
        root = os.path.join(ctx.artifact_dir, "candidate-documents")
        for item in eligible:
            asset, horizon = item["asset"], item["horizon"]
            group = f"{asset}:h{horizon:02d}"
            cache = _cache_for(asset, groups)
            weights = [1.0 / horizon] * horizon
            for template in self.params["templates"]:
                candidate_id = f"{template['id']}-{asset.lower()}-h{horizon:02d}"
                metadata = _metadata(template, candidate_id, group, horizon, weights)
                if not template["enabled"]:
                    candidates.append(
                        {**metadata, "prerequisite": template["prerequisite"]}
                    )
                    continue
                document = _document(
                    source, template, asset, horizon, cache, candidate_id, weights
                )
                path = os.path.join(root, candidate_id + ".json")
                _write(path, document)
                candidates.append({**metadata, "path": path})
        return {
            "candidates": candidates,
            "eligibility": eligible,
            "provenance": {
                "source_document": source_path,
                "source_document_sha256": source.hash,
                "gate3_artifact": gate3_path,
                "gate3_sha256": self.params["gate3_sha256"],
                "memory_artifact": memory_path,
                "memory_sha256": self.params["memory_sha256"],
                "eligible_count": len(eligible),
                "enabled_template_count": sum(
                    1 for row in self.params["templates"] if row["enabled"]
                ),
                "candidate_count": len(candidates),
            },
        }
