"""Post-Gate-3 model-zoo candidate materialization and estimator wrappers."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os

from dskit.pipeline.document import PipelineDocument, load_document
from dskit.pipeline.node import Node
from dskit.pipeline.stages import Stage, reject_unknown_params
from dskit.pipeline.libs.torch_ts import ZooEstimator

from intraday_equities.modelability_study import asset_walk_document

__all__ = [
    "DirectPathScore",
    "EmpiricalSelectRegressor",
    "Gate3ZooCandidates",
    "PooledDirectPathScore",
    "PooledGate3ZooCandidates",
    "KronosFusionRows",
    "SequenceOnlyZooEstimator",
    "StandardizedSelectRegressor",
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
        "seed_policy",
        "compute_class",
        "compute_rank",
        "enabled",
        "prerequisite",
        "model",
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
        if self._model is None:
            raise RuntimeError("regressor is not fitted")
        prediction = self._model.predict(x[:, self._indices])
        return prediction.ravel() if hasattr(prediction, "ravel") else prediction


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


def _gate3_rows(artifact):
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
    if len(eligible) != 25:
        raise ValueError(
            f"locked Gate-3 result must contain 25 passers, got {len(eligible)}"
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
    required = _TEMPLATE_FIELDS - {"prerequisite", "kronos", "feature_source"}
    for field in sorted(required):
        if field not in template:
            problems.append(f"{where}.{field} is required")
    for field in required - {"compute_rank", "enabled", "model"}:
        if field in template and not _string(template[field]):
            problems.append(f"{where}.{field} must be a non-empty string")
    if not isinstance(template.get("enabled"), bool):
        problems.append(f"{where}.enabled must be boolean")
    if template.get("feature_source", "tabular") not in ("tabular", "kronos"):
        problems.append(f"{where}.feature_source must be tabular or kronos")
    kronos = template.get("kronos")
    if template.get("feature_source") == "kronos":
        if not isinstance(kronos, dict) or set(kronos) != _KRONOS_FIELDS:
            problems.append(
                f"{where}.kronos must contain exactly {sorted(_KRONOS_FIELDS)}"
            )
        else:
            for field in _KRONOS_FIELDS - {
                "score_period_ms", "batch_size", "feature_names"
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
                problems.append(
                    f"{where}.kronos.feature_names must be unique strings"
                )
    elif kronos is not None:
        problems.append(f"{where}.kronos is only valid for feature_source=kronos")
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
            name_to_index = {
                name: index for index, name in enumerate(feature["names"])
            }
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
                    "X": np.column_stack([hidden, side]).astype(
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
    use_kronos = template.get("feature_source", "tabular") == "kronos"
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
                    "where": [
                        {"field": "symbol", "op": "in", "value": group_assets}
                    ]
                },
            }
            kline_inputs[group] = f"${kline_key}.records"
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
    obj["walkforward"]["objective"] = "$path.metrics.path_score"
    obj["walkforward"]["select"] = "max"
    obj["name"] = candidate_id
    obj["notes"] = (
        "ADR-0101/0102: one pooled fit per direct lead over all 25 Gate-3 "
        "passers; optional frozen Kronos states are fused only with the "
        "declared side-feature allowlist."
    )
    return PipelineDocument.from_obj(obj)


_POOLED_PARAMS = (
    "gate3_artifact",
    "gate3_sha256",
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
        for field in ("gate3_artifact", "gate3_sha256"):
            if not _string(params.get(field)):
                problems.append(f"{field} must be a non-empty string")
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

    def run(self, ctx, inputs):
        """Pin eligibility and write one candidate document per template."""
        gate3_path, gate3 = _read_pinned_json(
            ctx.source_path,
            self.params["gate3_artifact"],
            self.params["gate3_sha256"],
            "Gate-3 artifact",
        )
        eligible = _gate3_rows(gate3)
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
            raise ValueError("the first pooled cache does not contain the residual reference")
        horizon = max(row["horizon"] for row in eligible)
        weights = _pooled_horizon_weights(eligible)
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
                "gate3_artifact": gate3_path,
                "gate3_sha256": self.params["gate3_sha256"],
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


class Gate3ZooCandidates(Stage):
    """Expand inline model templates across the 25 pinned Gate-3 passers."""

    outputs = ("candidates", "eligibility", "provenance")

    @classmethod
    def validate_params(cls, params):
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
        return [] if inputs == {} else ["Gate3ZooCandidates takes no inputs"]

    def run(self, ctx, inputs):
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
