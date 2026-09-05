"""Post-Gate-3 model-zoo candidate materialization and estimator wrappers."""

from __future__ import annotations

import copy
import hashlib
import json
import os

from dskit.pipeline.document import PipelineDocument, load_document
from dskit.pipeline.stages import Stage, reject_unknown_params
from dskit.pipeline.libs.torch_ts import ZooEstimator

from intraday_equities.modelability_study import asset_walk_document

__all__ = [
    "EmpiricalSelectRegressor",
    "Gate3ZooCandidates",
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
)
_TEMPLATE_FIELDS = frozenset(
    {
        "id",
        "family",
        "representation",
        "feature_policy",
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
        if len(selected) != length or [row[0] for row in selected] != list(range(length)):
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
        raise ValueError(f"locked Gate-3 result must contain 25 passers, got {len(eligible)}")
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
    required = _TEMPLATE_FIELDS - {"prerequisite"}
    for field in sorted(required):
        if field not in template:
            problems.append(f"{where}.{field} is required")
    for field in required - {"compute_rank", "enabled", "model"}:
        if field in template and not _string(template[field]):
            problems.append(f"{where}.{field} must be a non-empty string")
    if not isinstance(template.get("enabled"), bool):
        problems.append(f"{where}.enabled must be boolean")
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


def _metadata(template, candidate_id, group):
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
    }


def _document(source, template, asset, horizon, cache, candidate_id):
    obj = asset_walk_document(
        source,
        "p13-model-zoo",
        asset,
        horizon,
        cache,
        tag=template["id"],
    ).to_obj()
    scan = obj["pipeline"]["scan"]["params"]
    for field in _MODEL_FIELDS:
        if field in template["model"]:
            scan[field] = copy.deepcopy(template["model"][field])
        else:
            scan.pop(field, None)
    obj["name"] = candidate_id
    obj["notes"] = (
        "ADR-0099: pinned P12 target/cache plus an inline P13 model template."
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


class Gate3ZooCandidates(Stage):
    """Expand inline model templates across the 25 pinned Gate-3 passers."""

    outputs = ("candidates", "eligibility", "provenance")

    @classmethod
    def validate_params(cls, params):
        problems = []
        reject_unknown_params(problems, params, _PARAMS)
        for field in _PARAMS[:-1]:
            if not _string(params.get(field)):
                problems.append(f"{field} must be a non-empty string")
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
            ctx.source_path, self.params["gate3_artifact"], self.params["gate3_sha256"], "Gate-3 artifact"
        )
        memory_path, memory = _read_pinned_json(
            ctx.source_path, self.params["memory_artifact"], self.params["memory_sha256"], "memory artifact"
        )
        eligible = _gate3_rows(gate3)
        groups = _groups(memory)
        candidates = []
        root = os.path.join(ctx.artifact_dir, "candidate-documents")
        for item in eligible:
            asset, horizon = item["asset"], item["horizon"]
            group = f"{asset}:h{horizon:02d}"
            cache = _cache_for(asset, groups)
            for template in self.params["templates"]:
                candidate_id = f"{template['id']}-{asset.lower()}-h{horizon:02d}"
                metadata = _metadata(template, candidate_id, group)
                if not template["enabled"]:
                    candidates.append({**metadata, "prerequisite": template["prerequisite"]})
                    continue
                document = _document(source, template, asset, horizon, cache, candidate_id)
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
                "enabled_template_count": sum(1 for row in self.params["templates"] if row["enabled"]),
                "candidate_count": len(candidates),
            },
        }
