"""JSON-declared, paired walk-forward model benchmarks (ADR-0097).

The benchmark is deliberately a protocol over ordinary pipeline documents,
not a second estimator registry.  Candidate JSON owns model-specific choices;
these stages own only inventory validation, execution, and comparison.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import random

from dskit.pipeline.document import load_document
from dskit.pipeline.driver import run_walk_forward
from dskit.pipeline.planner import plan as plan_document
from dskit.pipeline.predictions import read_prediction_series
from dskit.pipeline.program_calendar import ProgramCalendar
from dskit.pipeline.stages import Stage, reject_unknown_params
from dskit.pipeline.stats import bonferroni, cluster_bootstrap_t, newey_west_mean

__all__ = [
    "BenchmarkApproval",
    "BenchmarkCompare",
    "BenchmarkPlan",
    "PathBenchmarkCompare",
    "BenchmarkRun",
    "ProgramCalendar",
]

_CANDIDATE_FIELDS = frozenset(
    {
        "id",
        "path",
        "group",
        "family",
        "representation",
        "feature_policy",
        "seed_policy",
        "compute_class",
        "compute_rank",
        "enabled",
        "prerequisite",
        "forecast_strategy",
        "max_horizon",
        "leads",
        "horizon_weights",
    }
)
_PATH_CANDIDATE_FIELDS = {
    "forecast_strategy", "max_horizon", "leads", "horizon_weights"
}
_PROTOCOL_FIELDS = frozenset(
    {
        "alpha",
        "attempt_family",
        "comparison_hac_lags",
        "primary_score",
        "null_baseline",
        "cost_model",
        "lockbox",
        "decision_rule",
        "indifference_method",
        "multiplicity_method",
    }
)
_PATH_PROTOCOL_FIELDS = frozenset(
    {
        "path_evidence",
        "path_loss",
        "path_resampling",
        "horizon_diagnostic",
        "path_selection",
        "tie_break",
    }
)
_PROTOCOL_STRING_FIELDS = (
    _PROTOCOL_FIELDS | _PATH_PROTOCOL_FIELDS
) - {"alpha", "comparison_hac_lags"}


def _string(value):
    return isinstance(value, str) and bool(value.strip())


def _candidate_problems(candidate, index):
    where = f"candidates[{index}]"
    if not isinstance(candidate, dict):
        return [f"{where} must be an object"]
    problems = []
    unknown = sorted(set(candidate) - _CANDIDATE_FIELDS)
    if unknown:
        problems.append(f"{where} has unknown field(s) {unknown}")
    required = {
        "id",
        "group",
        "family",
        "representation",
        "feature_policy",
        "seed_policy",
        "compute_class",
        "compute_rank",
        "enabled",
    }
    missing = sorted(required - set(candidate))
    if missing:
        problems.append(f"{where} is missing field(s) {missing}")
    for key in sorted(required - {"compute_rank", "enabled"}):
        if key in candidate and not _string(candidate[key]):
            problems.append(f"{where}.{key} must be a non-empty string")
    rank = candidate.get("compute_rank")
    if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
        problems.append(f"{where}.compute_rank must be a positive integer")
    enabled = candidate.get("enabled")
    if not isinstance(enabled, bool):
        problems.append(f"{where}.enabled must be boolean")
    if enabled is True and not _string(candidate.get("path")):
        problems.append(f"{where}.path is required when enabled")
    if enabled is False and not _string(candidate.get("prerequisite")):
        problems.append(f"{where}.prerequisite is required when disabled")
    if "path" in candidate and not _string(candidate["path"]):
        problems.append(f"{where}.path must be a non-empty string")
    if "prerequisite" in candidate and not _string(candidate["prerequisite"]):
        problems.append(f"{where}.prerequisite must be a non-empty string")
    declared_path = _PATH_CANDIDATE_FIELDS & set(candidate)
    if declared_path and declared_path != _PATH_CANDIDATE_FIELDS:
        problems.append(f"{where} must declare every path metadata field together")
    if declared_path == _PATH_CANDIDATE_FIELDS:
        horizon = candidate["max_horizon"]
        leads = candidate["leads"]
        weights = candidate["horizon_weights"]
        if candidate["forecast_strategy"] != "direct_per_lead":
            problems.append(f"{where}.forecast_strategy must be direct_per_lead")
        if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 1:
            problems.append(f"{where}.max_horizon must be a positive integer")
        if not isinstance(leads, list) or (
            isinstance(horizon, int) and leads != list(range(1, horizon + 1))
        ):
            problems.append(f"{where}.leads must equal 1..max_horizon")
        if not isinstance(weights, list) or (
            isinstance(horizon, int) and len(weights) != horizon
        ) or any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value < 0.0
            for value in (weights if isinstance(weights, list) else [])
        ) or (
            isinstance(weights, list)
            and weights
            and not math.isclose(sum(weights), 1.0, rel_tol=1e-12, abs_tol=1e-12)
        ):
            problems.append(f"{where}.horizon_weights must be finite and sum to one")
    return problems


def _protocol_problems(protocol):
    if not isinstance(protocol, dict):
        return ["protocol must be an object"]
    problems = []
    unknown = sorted(set(protocol) - _PROTOCOL_FIELDS - _PATH_PROTOCOL_FIELDS)
    if unknown:
        problems.append(f"protocol has unknown field(s) {unknown}")
    missing = sorted(_PROTOCOL_FIELDS - set(protocol))
    if missing:
        problems.append(f"protocol is missing field(s) {missing}")
    for key in sorted(_PROTOCOL_STRING_FIELDS):
        if key in protocol and not _string(protocol[key]):
            problems.append(f"protocol.{key} must be a non-empty string")
    alpha = protocol.get("alpha")
    if (
        not isinstance(alpha, (int, float))
        or isinstance(alpha, bool)
        or not 0 < alpha < 1
    ):
        problems.append("protocol.alpha must be a number in (0, 1)")
    lags = protocol.get("comparison_hac_lags")
    if not isinstance(lags, int) or isinstance(lags, bool) or lags < 0:
        problems.append(
            "protocol.comparison_hac_lags must be a non-negative integer"
        )
    if protocol.get("multiplicity_method") != "bonferroni_all_pairwise":
        problems.append(
            "protocol.multiplicity_method must be bonferroni_all_pairwise"
        )
    if protocol.get("indifference_method") != "paired_fold_newey_west_no_detectable_difference":
        problems.append(
            "protocol.indifference_method must be "
            "paired_fold_newey_west_no_detectable_difference"
        )
    return problems


def _resolve_candidate_path(source_path, candidate_path):
    base = os.path.dirname(os.path.abspath(source_path))
    return os.path.abspath(os.path.join(base, candidate_path))


def _expected_summary_dir(document, asof):
    outputs = document.outputs
    root = os.path.abspath(
        os.path.expanduser(
            (outputs.run_root if outputs is not None else "")
            or "./pipeline_runs"
        )
    )
    return os.path.join(
        root, f"{document.name}-walkforward-{asof}-{document.hash[:8]}"
    )


def _at_path(obj, dotted):
    value = obj
    for segment in dotted.split("."):
        if not isinstance(value, dict) or segment not in value:
            raise ValueError(f"contract path {dotted!r} does not exist")
        value = value[segment]
    return copy.deepcopy(value)


def _candidate_metadata(candidate):
    return {
        key: copy.deepcopy(candidate[key])
        for key in (
            "id",
            "group",
            "family",
            "representation",
            "feature_policy",
            "seed_policy",
            "compute_class",
            "compute_rank",
            "forecast_strategy",
            "max_horizon",
            "leads",
            "horizon_weights",
        )
        if key in candidate
    }


class BenchmarkPlan(Stage):
    """Validate and inventory candidate pipeline documents without running them.

    Parameters
    ----------
    key : str
        Stage key in the staged benchmark document.
    params : dict
        ``candidates``, ``contract_paths``, and the shared ``protocol``.

    Examples
    --------
    Build the stage from the same data a JSON document declares::

        stage = BenchmarkPlan("plan", {"candidates": candidates,
                              "contract_paths": paths, "protocol": protocol})
    """

    outputs = ("candidates", "protocol", "contracts", "inventory_sha256")
    _PARAMS = ("candidates", "contract_paths", "protocol")

    @classmethod
    def validate_params(cls, params):
        """Return every candidate, contract, and protocol shape problem."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        candidates = params.get("candidates")
        if candidates is not None:
            if not isinstance(candidates, list) or not candidates:
                problems.append("candidates must be a non-empty list when declared")
            else:
                for index, candidate in enumerate(candidates):
                    problems.extend(_candidate_problems(candidate, index))
        paths = params.get("contract_paths")
        if (
            not isinstance(paths, list)
            or not paths
            or any(not _string(path) for path in paths)
        ):
            problems.append("contract_paths must be a non-empty list of strings")
        elif len(set(paths)) != len(paths):
            problems.append("contract_paths must not contain duplicates")
        problems.extend(_protocol_problems(params.get("protocol")))
        return problems

    def validate_inputs(self, inputs):
        """Require a validated calendar phase and its content digest."""
        if inputs == {}:
            return []
        wanted = {"phase", "calendar_sha256"}
        allowed = (wanted, wanted | {"candidates"})
        if not isinstance(inputs, dict) or set(inputs) not in allowed:
            return [
                f"inputs must contain {sorted(wanted)} and may also contain candidates"
            ]
        if not isinstance(inputs["phase"], dict):
            return ["phase must materialize as an object"]
        if not _string(inputs["calendar_sha256"]):
            return ["calendar_sha256 must materialize as a non-empty string"]
        if "candidates" in inputs:
            candidates = inputs["candidates"]
            if not isinstance(candidates, list) or not candidates:
                return ["candidates must materialize as a non-empty list"]
            problems = []
            for index, candidate in enumerate(candidates):
                problems.extend(_candidate_problems(candidate, index))
            return problems
        return []

    def run(self, ctx, inputs):
        """Plan candidates, pin hashes, and enforce group contract equality."""
        phase = inputs.get("phase")
        if phase is not None and (phase.get("selection_allowed") is not True or "walkforward" not in phase):
            raise ValueError(f"calendar phase {phase.get('key')!r} is not a fold-based selection phase")
        if phase is not None and ctx.asof > phase["latest_asof"]:
            raise ValueError(f"benchmark asof {ctx.asof} exceeds calendar phase limit {phase['latest_asof']}")
        candidates = inputs.get("candidates", self.params.get("candidates"))
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("benchmark has no materialized candidates")
        rows = []
        contracts = {}
        ids = set()
        declared_paths = set()
        hashes = set()
        for candidate in candidates:
            metadata = _candidate_metadata(candidate)
            candidate_id = candidate["id"]
            if candidate_id in ids:
                raise ValueError(f"duplicate candidate id {candidate_id!r}")
            ids.add(candidate_id)
            if not candidate["enabled"]:
                rows.append(
                    {
                        **metadata,
                        "state": "disabled",
                        "prerequisite": candidate["prerequisite"],
                        **({"calendar_sha256": inputs["calendar_sha256"], "calendar_phase": phase["key"]} if phase is not None else {}),
                    }
                )
                continue
            declared = candidate["path"]
            if declared in declared_paths:
                raise ValueError(f"duplicate candidate path {declared!r}")
            declared_paths.add(declared)
            resolved = _resolve_candidate_path(ctx.source_path, declared)
            document = load_document(resolved)
            if document.hash in hashes:
                raise ValueError(
                    f"candidate {candidate_id!r} duplicates document hash "
                    f"{document.hash}"
                )
            hashes.add(document.hash)
            planned = plan_document(document)
            searches = [
                key for key in planned.order if planned.role_of(key) == "search"
            ]
            if document.walkforward is None:
                raise ValueError(
                    f"candidate {candidate_id!r} has no walkforward section"
                )
            if phase is not None:
                declared_walk = document.walkforward.to_obj()
                declared_walk.pop("notes", None)
                wanted_walk = copy.deepcopy(phase["walkforward"])
                wanted_walk.pop("last_validation_end_exclusive", None)
                if declared_walk != wanted_walk:
                    changed = sorted(
                        key
                        for key in set(declared_walk) | set(wanted_walk)
                        if declared_walk.get(key) != wanted_walk.get(key)
                    )
                    raise ValueError(
                        f"candidate {candidate_id!r} changes calendar "
                        f"walk-forward field(s) {changed} in phase "
                        f"{phase['key']!r}"
                    )
            if searches:
                raise ValueError(
                    f"candidate {candidate_id!r} uses generic search node(s) "
                    f"{searches}; their fold validation is reported evidence, "
                    "so use an inner-training search instead"
                )
            contract_paths = list(self.params["contract_paths"])
            if _PATH_CANDIDATE_FIELDS <= set(candidate):
                templates = [
                    path
                    for path in contract_paths
                    if path.startswith("pipeline.scan_h01.")
                ]
                for lead in candidate["leads"][1:]:
                    contract_paths.extend(
                        path.replace("pipeline.scan_h01.", f"pipeline.scan_h{lead:02d}.")
                        for path in templates
                    )
            values = {
                path: _at_path(document.to_obj(), path)
                for path in contract_paths
            }
            group = candidate["group"]
            if group not in contracts:
                contracts[group] = values
            elif values != contracts[group]:
                changed = [
                    path
                    for path in sorted(set(values) | set(contracts[group]))
                    if values.get(path) != contracts[group].get(path)
                ]
                raise ValueError(
                    f"candidate {candidate_id!r} changes shared contract "
                    f"path(s) {changed} in group {group!r}"
                )
            rows.append(
                {
                    **metadata,
                    "state": "planned",
                    "path": declared,
                    "document_hash": document.hash,
                    "name": document.name,
                    "objective": document.walkforward.objective,
                    "select": document.walkforward.select,
                    "asof": ctx.asof,
                    "expected_fold_count": len(
                        document.walkforward.fold_cutoffs()
                    ),
                    "expected_cutoffs": list(
                        document.walkforward.fold_cutoffs()
                    ),
                    **(
                        {
                            "calendar_sha256": inputs["calendar_sha256"],
                            "calendar_phase": phase["key"],
                            "latest_asof": phase["latest_asof"],
                        }
                        if phase is not None
                        else {}
                    ),
                }
            )
        protocol = copy.deepcopy(self.params["protocol"])
        return {
            "candidates": rows,
            "protocol": protocol,
            "contracts": contracts,
            "inventory_sha256": _inventory_sha256(rows, protocol, contracts),
        }


def _inventory_sha256(candidates, protocol, contracts):
    rows = []
    for candidate in candidates:
        rows.append(
            {
                key: copy.deepcopy(value)
                for key, value in candidate.items()
                if key != "path"
            }
        )
    payload = {"candidates": rows, "protocol": protocol, "contracts": contracts}
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class BenchmarkApproval(Stage):
    """Require human review of the frozen inventory before any fit starts."""

    outputs = ("approval",)
    _PARAMS = ("approved_inventory_sha256", "approved_by", "approval_note")
    _PENDING = "PENDING-PLAN-REVIEW"

    @classmethod
    def validate_params(cls, params):
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for field in cls._PARAMS:
            if not _string(params.get(field)):
                problems.append(f"{field} must be a non-empty string")
        digest = params.get("approved_inventory_sha256")
        if _string(digest) and digest != cls._PENDING and (
            len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)
        ):
            problems.append(
                "approved_inventory_sha256 must be PENDING-PLAN-REVIEW or a lowercase SHA-256"
            )
        return problems

    def validate_inputs(self, inputs):
        if not isinstance(inputs, dict) or set(inputs) != {"inventory_sha256"}:
            return ["inputs must contain exactly inventory_sha256"]
        digest = inputs["inventory_sha256"]
        if not _string(digest) or len(digest) != 64:
            return ["inventory_sha256 must materialize as a SHA-256 string"]
        return []

    def run(self, ctx, inputs):
        observed = inputs["inventory_sha256"]
        approved = self.params["approved_inventory_sha256"]
        if approved == self._PENDING:
            return {
                "approval": {
                    "approved": False,
                    "inventory_sha256": observed,
                    "approved_by": self._PENDING,
                    "approval_note": self.params["approval_note"],
                    "state": "awaiting-plan-review",
                }
            }
        if approved != observed:
            raise ValueError(
                f"approved inventory hash changed: {approved} -> {observed}"
            )
        if self.params["approved_by"] == self._PENDING:
            raise ValueError("approved_by must identify the reviewer after approval")
        return {
            "approval": {
                "approved": True,
                "inventory_sha256": observed,
                "approved_by": self.params["approved_by"],
                "approval_note": self.params["approval_note"],
                "state": "approved",
            }
        }


class BenchmarkRun(Stage):
    """Execute planned candidates with an atomic per-candidate checkpoint."""

    outputs = ("runs",)

    @classmethod
    def validate_params(cls, params):
        problems = []
        reject_unknown_params(problems, params, ())
        return problems

    def validate_inputs(self, inputs):
        wanted = {"approval", "candidates"}
        if not isinstance(inputs, dict) or set(inputs) != wanted:
            return [f"inputs must contain exactly {sorted(wanted)}"]
        if not isinstance(inputs["candidates"], list):
            return ["candidates must materialize as a list"]
        approval = inputs["approval"]
        if not isinstance(approval, dict) or not isinstance(approval.get("approved"), bool):
            return ["approval must materialize as an approval object"]
        return []

    def run(self, ctx, inputs):
        approval = inputs["approval"]
        if approval.get("approved") is not True:
            return {
                "runs": [
                    {
                        **copy.deepcopy(candidate),
                        "state": (
                            "disabled"
                            if candidate.get("state") == "disabled"
                            else "awaiting_approval"
                        ),
                    }
                    for candidate in inputs["candidates"]
                ]
            }
        signature = _run_signature(ctx, inputs["candidates"], approval)
        checkpoint_path = os.path.join(
            ctx.artifact_dir, "benchmark-run-checkpoint.json"
        )
        completed = {
            row["id"]: row
            for row in _load_checkpoint(checkpoint_path, signature)
        }
        rows = []
        for candidate in inputs["candidates"]:
            if candidate.get("state") == "disabled":
                rows.append(copy.deepcopy(candidate))
                continue
            if candidate.get("state") != "planned":
                raise ValueError(
                    f"candidate {candidate.get('id')!r} has invalid plan state "
                    f"{candidate.get('state')!r}"
                )
            resolved = _resolve_candidate_path(ctx.source_path, candidate["path"])
            document = load_document(resolved)
            if document.hash != candidate["document_hash"]:
                raise ValueError(
                    f"candidate {candidate['id']!r} moved after planning: "
                    f"{candidate['document_hash']} -> {document.hash}"
                )
            if candidate.get("latest_asof") and ctx.asof > candidate["latest_asof"]:
                raise ValueError(
                    f"candidate {candidate['id']!r} asof {ctx.asof} exceeds "
                    f"calendar phase limit {candidate['latest_asof']}"
                )
            expected_summary = _expected_summary_dir(document, ctx.asof)
            if candidate["id"] in completed:
                prior = copy.deepcopy(completed[candidate["id"]])
                if prior.get("document_hash") != candidate["document_hash"]:
                    raise ValueError(
                        f"checkpoint hash drift for candidate {candidate['id']!r}"
                    )
                if prior.get("summary_dir") != expected_summary:
                    raise ValueError(
                        f"checkpoint summary path drift for candidate {candidate['id']!r}"
                    )
                if prior.get("state") == "running":
                    prior = {
                        **copy.deepcopy(candidate),
                        "state": "ran",
                        "summary_dir": expected_summary,
                        "exit_code": 0,
                        "recovered_after_interruption": True,
                    }
                    _validate_summary(prior, _load_summary(prior))
                    rows.append(prior)
                    _write_checkpoint(checkpoint_path, signature, rows)
                    continue
                if prior.get("state") != "ran" or prior.get("exit_code") != 0:
                    raise ValueError(
                        f"candidate {candidate['id']!r} previously ended in "
                        f"state {prior.get('state')!r}; resolve it under a new "
                        "benchmark identity"
                    )
                _validate_summary(prior, _load_summary(prior))
                rows.append(prior)
                continue
            running = {
                **copy.deepcopy(candidate),
                "state": "running",
                "summary_dir": expected_summary,
                "exit_code": None,
            }
            _write_checkpoint(checkpoint_path, signature, rows + [running])
            result = run_walk_forward(document, asof=ctx.asof)
            if result.summary_dir != expected_summary:
                raise ValueError(
                    f"candidate {candidate['id']!r} returned an unexpected summary path"
                )
            row = {
                **copy.deepcopy(candidate),
                "state": result.state,
                "summary_dir": result.summary_dir,
                "exit_code": result.exit_code,
            }
            if result.state == "ran" and result.exit_code == 0:
                _validate_summary(row, _load_summary(row))
            rows.append(row)
            _write_checkpoint(checkpoint_path, signature, rows)
            if result.state != "ran" or result.exit_code != 0:
                raise ValueError(
                    f"candidate {candidate['id']!r} ended in state "
                    f"{result.state!r} with exit code {result.exit_code}"
                )
        return {"runs": rows}


def _run_signature(ctx, candidates, approval):
    payload = {
        "benchmark_hash": ctx.document.hash,
        "asof": ctx.asof,
        "approved_inventory_sha256": approval["inventory_sha256"],
        "candidates": [
            {
                "id": row.get("id"),
                "state": row.get("state"),
                "hash": row.get("document_hash"),
            }
            for row in candidates
        ],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _load_checkpoint(path, signature):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        checkpoint = json.load(handle)
    if not isinstance(checkpoint, dict) or set(checkpoint) != {"signature", "rows"}:
        raise ValueError("benchmark checkpoint is malformed")
    if checkpoint["signature"] != signature:
        raise ValueError("benchmark checkpoint does not match the current plan")
    if not isinstance(checkpoint["rows"], list):
        raise ValueError("benchmark checkpoint rows must be a list")
    return checkpoint["rows"]


def _write_checkpoint(path, signature, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(
            {"signature": signature, "rows": rows},
            handle,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _load_summary(candidate):
    path = os.path.join(candidate["summary_dir"], "walkforward.json")
    with open(path, "r", encoding="utf-8") as fh:
        summary = json.load(fh)
    if summary.get("document_hash") != candidate["document_hash"]:
        raise ValueError(
            f"candidate {candidate['id']!r} summary hash does not match its plan"
        )
    return summary


def _validate_summary(candidate, summary):
    folds = summary.get("folds")
    aggregate = summary.get("aggregate")
    expected_cutoffs = candidate.get("expected_cutoffs")
    expected_count = candidate.get("expected_fold_count")
    scores = [] if not isinstance(folds, list) else [fold.get("score") for fold in folds]
    complete = (
        summary.get("state") == "ran"
        and summary.get("asof") == candidate.get("asof")
        and summary.get("objective") == candidate.get("objective")
        and summary.get("select") == candidate.get("select")
        and isinstance(aggregate, dict)
        and isinstance(expected_cutoffs, list)
        and expected_count == len(expected_cutoffs)
        and isinstance(folds, list)
        and len(folds) == expected_count
        and [fold.get("cutoff") for fold in folds] == expected_cutoffs
        and len(set(expected_cutoffs)) == len(expected_cutoffs)
        and all(fold.get("state") == "ran" for fold in folds)
        and all(
            isinstance(score, (int, float))
            and not isinstance(score, bool)
            and math.isfinite(score)
            for score in scores
        )
        and aggregate.get("n_folds") == expected_count
        and aggregate.get("n_scored") == expected_count
        and isinstance(aggregate.get("mean"), (int, float))
        and not isinstance(aggregate.get("mean"), bool)
        and math.isfinite(aggregate.get("mean"))
    )
    if complete:
        observed = sum(float(score) for score in scores) / expected_count
        complete = math.isclose(
            float(aggregate["mean"]), observed, rel_tol=1e-12, abs_tol=1e-15
        )
    if not complete:
        raise ValueError(
            f"candidate {candidate['id']!r} has incomplete or inconsistent fold evidence"
        )
    return [float(score) for score in scores]


def _better(left, right, select):
    return left > right if select == "max" else left < right


def _frontier(rows, select):
    frontier = []
    for row in rows:
        dominated = False
        for other in rows:
            score_no_worse = (
                other["mean"] >= row["mean"]
                if select == "max"
                else other["mean"] <= row["mean"]
            )
            if (
                other["compute_rank"] <= row["compute_rank"]
                and score_no_worse
                and (
                    other["compute_rank"] < row["compute_rank"]
                    or _better(other["mean"], row["mean"], select)
                )
            ):
                dominated = True
                break
        if not dominated:
            frontier.append(copy.deepcopy(row))
    return frontier


class BenchmarkCompare(Stage):
    """Compare only complete paired folds with family-wide multiplicity."""

    outputs = (
        "ranking",
        "family_ranking",
        "paired",
        "pairwise",
        "frontier",
        "provenance",
    )

    @classmethod
    def validate_params(cls, params):
        problems = []
        reject_unknown_params(problems, params, ())
        return problems

    def validate_inputs(self, inputs):
        wanted = {"runs", "protocol", "contracts", "approval"}
        if not isinstance(inputs, dict) or set(inputs) != wanted:
            return [f"inputs must contain exactly {sorted(wanted)}"]
        if not isinstance(inputs["runs"], list):
            return ["runs must materialize as a list"]
        if not isinstance(inputs["protocol"], dict):
            return ["protocol must materialize as an object"]
        if not isinstance(inputs["contracts"], dict):
            return ["contracts must materialize as an object"]
        approval = inputs["approval"]
        if not isinstance(approval, dict) or not isinstance(approval.get("approved"), bool):
            return ["approval must materialize as an approval object"]
        return []

    def run(self, ctx, inputs):
        if inputs["approval"].get("approved") is not True:
            if any(row.get("state") == "ran" for row in inputs["runs"]):
                raise ValueError("an unapproved benchmark contains executed candidates")
            return {
                "ranking": [],
                "family_ranking": [],
                "paired": [],
                "pairwise": [],
                "frontier": [],
                "provenance": {
                    "benchmark_hash": ctx.document.hash,
                    "asof": ctx.asof,
                    "protocol": copy.deepcopy(inputs["protocol"]),
                    "contracts": copy.deepcopy(inputs["contracts"]),
                    "approval": copy.deepcopy(inputs["approval"]),
                    "no_launch": True,
                },
            }
        grouped = {}
        disabled = []
        for candidate in inputs["runs"]:
            if candidate.get("state") == "disabled":
                disabled.append(copy.deepcopy(candidate))
                continue
            if candidate.get("state") != "ran" or candidate.get("exit_code") != 0:
                raise ValueError(
                    f"candidate {candidate.get('id')!r} is not a successful run"
                )
            summary = _load_summary(candidate)
            _validate_summary(candidate, summary)
            row = copy.deepcopy(candidate)
            row["summary"] = summary
            grouped.setdefault(candidate["group"], []).append(row)

        ranking = []
        paired = []
        frontier = []
        material = {}
        for group, candidates in sorted(grouped.items()):
            selects = {row["summary"].get("select") for row in candidates}
            if len(selects) != 1 or next(iter(selects)) not in {"max", "min"}:
                raise ValueError(f"group {group!r} does not share one select direction")
            select = next(iter(selects))
            cutoffs = candidates[0]["expected_cutoffs"]
            scores = {}
            group_rows = []
            for candidate in candidates:
                if candidate["expected_cutoffs"] != cutoffs:
                    raise ValueError(
                        f"group {group!r} candidates do not share ordered cutoffs"
                    )
                values = [float(fold["score"]) for fold in candidate["summary"]["folds"]]
                scores[candidate["id"]] = values
                mean = candidate["summary"]["aggregate"].get("mean")
                if (
                    not isinstance(mean, (int, float))
                    or isinstance(mean, bool)
                    or not math.isfinite(mean)
                ):
                    raise ValueError(f"candidate {candidate['id']!r} has no finite mean")
                group_rows.append(
                    {
                        **_candidate_metadata(candidate),
                        "state": candidate["state"],
                        "document_hash": candidate["document_hash"],
                        "summary_dir": candidate["summary_dir"],
                        "mean": float(mean),
                        "std": candidate["summary"]["aggregate"].get("std"),
                        "n_folds": candidate["expected_fold_count"],
                        "n_scored": candidate["expected_fold_count"],
                    }
                )
            paired.extend(
                {
                    "group": group,
                    "cutoff": cutoff,
                    "scores": {
                        candidate_id: values[index]
                        for candidate_id, values in sorted(scores.items())
                    },
                }
                for index, cutoff in enumerate(cutoffs)
            )
            best = min(
                group_rows,
                key=lambda row: (
                    -row["mean"] if select == "max" else row["mean"],
                    row["id"],
                ),
            )
            material[group] = {
                "select": select,
                "best": best["id"],
                "rows": group_rows,
                "scores": scores,
                "cutoffs": cutoffs,
            }

        if not material:
            raise ValueError("benchmark has no approved candidate groups")
        directions = {item["select"] for item in material.values()}
        if len(directions) != 1:
            raise ValueError("all candidate groups must share one select direction")
        overall_select = next(iter(directions))
        family_sets = [
            {row["family"] for row in item["rows"]}
            for item in material.values()
        ]
        if any(families != family_sets[0] for families in family_sets[1:]):
            raise ValueError("every approved pair must contain the same model families")

        pairwise = []
        pvalues = {}
        lags = inputs["protocol"]["comparison_hac_lags"]
        for group, item in sorted(material.items()):
            ids = sorted(item["scores"])
            if len(ids) < 2:
                raise ValueError(f"group {group!r} needs at least two candidates")
            if lags >= len(item["cutoffs"]):
                raise ValueError(
                    f"comparison_hac_lags={lags} must be smaller than the "
                    f"{len(item['cutoffs'])} folds in group {group!r}"
                )
            for index, left in enumerate(ids):
                for right in ids[index + 1 :]:
                    key = f"{group}:{left}:{right}"
                    differences = [
                        float(a) - float(b)
                        for a, b in zip(
                            item["scores"][left], item["scores"][right]
                        )
                    ]
                    forward = newey_west_mean(differences, lags=lags)
                    reverse = newey_west_mean(
                        [-value for value in differences], lags=lags
                    )
                    raw = max(
                        math.nextafter(0.0, 1.0),
                        min(
                            1.0,
                            2.0 * min(
                                forward["p_value"], reverse["p_value"]
                            ),
                        ),
                    )
                    pvalues[key] = raw
                    pairwise.append(
                        {
                            "key": key,
                            "group": group,
                            "left": left,
                            "right": right,
                            "p_value": raw,
                            "mean_difference_left_minus_right": forward["mean"],
                            "hac_se": forward["se"],
                            "hac_lags": lags,
                        }
                    )
        rejected = bonferroni(pvalues, inputs["protocol"]["alpha"])
        threshold = inputs["protocol"]["alpha"] / len(pvalues)
        by_pair = {}
        for test in pairwise:
            test["reject_equal_performance"] = rejected[test["key"]]
            test["family_size"] = len(pvalues)
            test["adjusted_threshold"] = threshold
            by_pair[(test["group"], frozenset((test["left"], test["right"])))] = test

        for group, item in material.items():
            eligible = []
            for row in item["rows"]:
                best = item["best"]
                not_detectably_different = row["id"] == best or not by_pair[
                    (group, frozenset((row["id"], best)))
                ]["reject_equal_performance"]
                row["best_mean_candidate"] = best
                row["statistically_not_detectably_different"] = not_detectably_different
                if not_detectably_different:
                    eligible.append(row)
            chosen = min(
                eligible,
                key=lambda row: (
                    row["compute_rank"],
                    -row["mean"] if item["select"] == "max" else row["mean"],
                    row["id"],
                ),
            )
            for row in item["rows"]:
                row["selected_simplest_not_detectably_different"] = (
                    row["id"] == chosen["id"]
                )
            item["rows"].sort(
                key=lambda row: (
                    row["compute_rank"],
                    -row["mean"] if item["select"] == "max" else row["mean"],
                    row["id"],
                )
            )
            counts = {}
            for row in item["rows"]:
                compute = row["compute_class"]
                counts[compute] = counts.get(compute, 0) + 1
                row["fixed_compute_rank"] = counts[compute]
                ranking.append(row)
            frontier.extend(_frontier(item["rows"], item["select"]))

        families = {}
        for row in ranking:
            entry = families.setdefault(
                row["family"], {"family": row["family"], "pair_means": [], "pair_ranks": []}
            )
            entry["pair_means"].append(row["mean"])
            same_pair = [other for other in ranking if other["group"] == row["group"]]
            ordered = sorted(
                same_pair,
                key=lambda other: (
                    -other["mean"] if overall_select == "max" else other["mean"],
                    other["id"],
                ),
            )
            entry["pair_ranks"].append(
                1 + [other["id"] for other in ordered].index(row["id"])
            )
        family_ranking = []
        for entry in families.values():
            family_ranking.append(
                {
                    "family": entry["family"],
                    "n_pairs": len(entry["pair_means"]),
                    "equal_pair_mean": sum(entry["pair_means"]) / len(entry["pair_means"]),
                    "equal_pair_mean_rank": sum(entry["pair_ranks"]) / len(entry["pair_ranks"]),
                }
            )
        expected_pairs = len(material)
        if any(row["n_pairs"] != expected_pairs for row in family_ranking):
            raise ValueError("family aggregation is missing an approved pair")
        family_ranking.sort(
            key=lambda row: (
                -row["equal_pair_mean"]
                if overall_select == "max"
                else row["equal_pair_mean"],
                row["family"],
            )
        )

        return {
            "ranking": ranking,
            "family_ranking": family_ranking,
            "paired": paired,
            "pairwise": pairwise,
            "frontier": frontier,
            "provenance": {
                "benchmark_hash": ctx.document.hash,
                "asof": ctx.asof,
                "protocol": copy.deepcopy(inputs["protocol"]),
                "contracts": copy.deepcopy(inputs["contracts"]),
                "approval": copy.deepcopy(inputs["approval"]),
                "disabled": disabled,
                "multiplicity_family_size": len(pvalues),
                "compute_measurement": (
                    "declared compute_rank; elapsed time, peak memory, and "
                    "inference latency await a walk-forward resource record"
                ),
            },
        }



def _carry_path(run_dir, dotted):
    path = os.path.join(run_dir, "carry.json")
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
    for segment in dotted.split("."):
        if not isinstance(value, dict) or segment not in value:
            raise ValueError(f"path evidence {dotted!r} is absent from {path}")
        value = value[segment]
    return value


class _LossTensorWriter:
    """Stream comparison loss rows to one compressed parquet artifact."""

    def __init__(self, path):
        import pyarrow as pa
        import pyarrow.parquet as pq

        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._tmp = f"{path}.tmp-{os.getpid()}"
        self._schema = pa.schema(
            [
                ("group", pa.string()), ("candidate", pa.string()),
                ("family", pa.string()), ("cutoff", pa.string()),
                ("fold", pa.int16()), ("ts", pa.int64()), ("lead", pa.int16()),
                ("y", pa.float64()), ("yhat", pa.float64()), ("mu", pa.float64()),
                ("train_scale", pa.float64()), ("model_loss", pa.float64()),
                ("benchmark_loss", pa.float64()),
                ("normalized_improvement", pa.float64()), ("weight", pa.float64()),
            ],
            metadata={"schema_version": "1"},
        )
        self._writer = pq.ParquetWriter(self._tmp, self._schema, compression="zstd")
        self.rows = 0

    def append(self, rows):
        import pyarrow as pa

        if rows:
            self._writer.write_table(pa.Table.from_pylist(rows, schema=self._schema))
            self.rows += len(rows)

    def close(self):
        self._writer.close()
        os.replace(self._tmp, self.path)
        digest = hashlib.sha256()
        with open(self.path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return {
            "path": self.path,
            "sha256": digest.hexdigest(),
            "rows": self.rows,
            "schema_version": "1",
        }


def _bootstrap_row(clusters, draws, seed, label, alpha):
    result = cluster_bootstrap_t(
        clusters, n_boot=draws, seed=seed, label=label, alpha=alpha
    )
    return {key: value for key, value in result.items()}


def _pooled_cluster_mean_se(totals, sizes):
    n = len(totals)
    total_size = sum(sizes)
    mean = sum(totals) / total_size
    if n < 2:
        raise ValueError("session-cluster inference needs at least two sessions")
    residual_ss = sum(
        (total - mean * size) ** 2
        for total, size in zip(totals, sizes)
    )
    se = math.sqrt(n / (n - 1) * residual_ss) / total_size
    return mean, se


def _family_spa(by_candidate, draws, seed, label):
    """Shared recentered session-cluster max-t evidence for a model family."""
    ids = sorted(by_candidate)
    if not ids:
        raise ValueError(f"{label} has no candidates")
    keys = sorted(by_candidate[ids[0]])
    samples = {}
    observed = {}
    for candidate in ids:
        if sorted(by_candidate[candidate]) != keys:
            raise ValueError(f"{label} candidates do not share session clusters")
        totals = [float(sum(by_candidate[candidate][key])) for key in keys]
        sizes = [len(by_candidate[candidate][key]) for key in keys]
        if any(size < 1 for size in sizes):
            raise ValueError(f"{label} contains an empty session cluster")
        mean, se = _pooled_cluster_mean_se(totals, sizes)
        centered = [
            total - max(mean, 0.0) * size
            for total, size in zip(totals, sizes)
        ]
        samples[candidate] = (centered, sizes)
        observed[candidate] = {
            "mean": mean,
            "se": se,
            "t": (
                mean / se
                if se > 0.0
                else (math.inf if mean > 0.0 else 0.0)
            ),
            "n_clusters": len(keys),
        }
    digest = hashlib.sha256(f"{seed}|{label}".encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    exceed = {candidate: 0 for candidate in ids}
    n_clusters = len(keys)
    for _ in range(draws):
        picked = [rng.randrange(n_clusters) for _ in range(n_clusters)]
        maximum = -math.inf
        for candidate in ids:
            centered, sizes = samples[candidate]
            totals_star = [centered[index] for index in picked]
            sizes_star = [sizes[index] for index in picked]
            mean_star, se_star = _pooled_cluster_mean_se(totals_star, sizes_star)
            statistic = mean_star / se_star if se_star > 0.0 else 0.0
            maximum = max(maximum, statistic)
        for candidate in ids:
            if maximum >= observed[candidate]["t"]:
                exceed[candidate] += 1
    return {
        candidate: {
            **observed[candidate],
            "p_value": (1 + exceed[candidate]) / (draws + 1),
            "method": "shared recentered whole-session family max-t",
        }
        for candidate in ids
    }


def _superior_set(by_candidate, draws, seed, alpha, label):
    means = {
        candidate: sum(map(sum, clusters.values()))
        / sum(len(values) for values in clusters.values())
        for candidate, clusters in by_candidate.items()
    }
    best = max(means, key=lambda candidate: (means[candidate], candidate))
    members = [best]
    tests = []
    comparisons = max(len(by_candidate) - 1, 1)
    for candidate in sorted(by_candidate):
        if candidate == best:
            continue
        keys = sorted(by_candidate[best])
        if keys != sorted(by_candidate[candidate]):
            raise ValueError(f"{label} candidates do not share session clusters")
        differences = {
            key: [
                left - right
                for left, right in zip(
                    by_candidate[candidate][key], by_candidate[best][key]
                )
            ]
            for key in keys
        }
        test = _bootstrap_row(
            differences, draws, seed, f"{label}:{candidate}:{best}",
            alpha / comparisons,
        )
        test.update({"candidate": candidate, "best": best})
        tests.append(test)
        if test["ci_high"] is None or test["ci_high"] >= 0.0:
            members.append(candidate)
    return {
        "best_mean": best,
        "members": sorted(members),
        "eliminated": sorted(set(by_candidate) - set(members)),
        "tests": tests,
        "method": "session-cluster bootstrap-t; Bonferroni superior set",
    }


class PathBenchmarkCompare(BenchmarkCompare):
    """Add per-lead loss evidence and path-level uncertainty sets.

    This comparator never trains or promotes. It reads completed outer
    folds, persists the per-origin/per-lead normalized loss tensor, uses
    whole UTC sessions as bootstrap clusters, and returns conservative
    horizon and path superior sets plus average and uniform SPA evidence.
    """

    outputs = BenchmarkCompare.outputs + (
        "loss_evidence",
        "horizon_ranking",
        "horizon_confidence_sets",
        "model_confidence_sets",
        "superior_predictive_ability",
        "selection",
    )
    _PARAMS = ("bootstrap_draws", "bootstrap_seed")

    @classmethod
    def validate_params(cls, params):
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        draws = params.get("bootstrap_draws")
        seed = params.get("bootstrap_seed")
        if not isinstance(draws, int) or isinstance(draws, bool) or draws < 99:
            problems.append("bootstrap_draws must be an integer >= 99")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            problems.append("bootstrap_seed must be a non-negative integer")
        return problems

    def run(self, ctx, inputs):
        result = super().run(ctx, inputs)
        extras = {
            "loss_evidence": {},
            "horizon_ranking": [],
            "horizon_confidence_sets": [],
            "model_confidence_sets": [],
            "superior_predictive_ability": [],
            "selection": [],
        }
        if inputs["approval"].get("approved") is not True:
            result.update(extras)
            return result
        missing = _PATH_PROTOCOL_FIELDS - set(inputs["protocol"])
        if missing:
            raise ValueError(f"path protocol is missing field(s) {sorted(missing)}")
        draws = self.params["bootstrap_draws"]
        seed = self.params["bootstrap_seed"]
        alpha = inputs["protocol"]["alpha"]
        writer = _LossTensorWriter(
            os.path.join(ctx.artifact_dir, "path-loss-tensor.parquet")
        )
        horizon_scores = {}
        path_scores = {}
        expected_evidence = {}
        try:
            for candidate in inputs["runs"]:
                if candidate.get("state") != "ran":
                    continue
                if not _PATH_CANDIDATE_FIELDS <= set(candidate):
                    raise ValueError(f"candidate {candidate.get('id')!r} lacks path metadata")
                summary = _load_summary(candidate)
                horizon = candidate["max_horizon"]
                weights = candidate["horizon_weights"]
                for fold_index, fold in enumerate(summary["folds"]):
                    cutoff = fold["cutoff"]
                    run_dir = fold["run_dir"]
                    records = _carry_path(
                        run_dir, inputs["protocol"]["path_evidence"]
                    )
                    scales = {
                        int(row["lead"]): float(row["train_scale"])
                        for row in records
                    }
                    units = read_prediction_series(run_dir)
                    if {unit["lead"] for unit in units} != set(range(1, horizon + 1)):
                        raise ValueError(
                            f"{candidate['id']} fold {cutoff} lacks the complete 1..H_i path"
                        )
                    path_by_stamp = {}
                    fold_rows = []
                    common = None
                    for unit in units:
                        lead = unit["lead"]
                        stamps = tuple(unit["stamps"])
                        common = stamps if common is None else common
                        if stamps != common:
                            raise ValueError(
                                f"{candidate['id']} fold {cutoff} heads do not share origins"
                            )
                        scale = scales.get(lead, 0.0)
                        if not math.isfinite(scale) or scale <= 0.0:
                            raise ValueError(
                                f"{candidate['id']} fold {cutoff} lead {lead} has no train scale"
                            )
                        evidence_key = (candidate["group"], cutoff, lead)
                        evidence = (
                            stamps,
                            tuple(unit["y"]),
                            float(unit["mu"]),
                            scale,
                        )
                        prior = expected_evidence.setdefault(evidence_key, evidence)
                        if prior != evidence:
                            raise ValueError(
                                f"group {candidate['group']} fold {cutoff} lead {lead} "
                                "is not paired on origins, outcomes, benchmark, and scale"
                            )
                        weight = float(weights[lead - 1])
                        clusters = horizon_scores.setdefault(
                            (candidate["group"], lead), {}
                        ).setdefault(candidate["id"], {})
                        for ts, y, yhat, mu in zip(
                            unit["stamps"], unit["y"], unit["yhat"], [unit["mu"]] * len(unit["y"])
                        ):
                            benchmark_loss = (y - mu) ** 2 / scale
                            model_loss = (y - yhat) ** 2 / scale
                            improvement = benchmark_loss - model_loss
                            cluster = f"{cutoff}:{int(ts) // 86400000}"
                            clusters.setdefault(cluster, []).append(improvement)
                            path_by_stamp[ts] = path_by_stamp.get(ts, 0.0) + weight * improvement
                            fold_rows.append(
                                {
                                    "group": candidate["group"],
                                    "candidate": candidate["id"],
                                    "family": candidate["family"],
                                    "cutoff": cutoff,
                                    "fold": fold_index,
                                    "ts": ts,
                                    "lead": lead,
                                    "y": y,
                                    "yhat": yhat,
                                    "mu": mu,
                                    "train_scale": scale,
                                    "model_loss": model_loss,
                                    "benchmark_loss": benchmark_loss,
                                    "normalized_improvement": improvement,
                                    "weight": weight,
                                }
                            )
                    path_clusters = path_scores.setdefault(
                        candidate["group"], {}
                    ).setdefault(candidate["id"], {})
                    for ts, score in path_by_stamp.items():
                        cluster = f"{cutoff}:{int(ts) // 86400000}"
                        path_clusters.setdefault(cluster, []).append(score)
                    writer.append(fold_rows)
        except Exception:
            writer._writer.close()
            if os.path.exists(writer._tmp):
                os.remove(writer._tmp)
            raise
        extras["loss_evidence"] = writer.close()

        horizons_per_group = {}
        for group, lead in horizon_scores:
            horizons_per_group.setdefault(group, set()).add(lead)
        for (group, lead), candidates in sorted(horizon_scores.items()):
            superior = _superior_set(
                candidates, draws, seed, alpha / len(horizons_per_group[group]),
                f"horizon:{group}:{lead}",
            )
            extras["horizon_confidence_sets"].append(
                {"group": group, "lead": lead, **superior}
            )
            means = {
                candidate: sum(map(sum, clusters.values()))
                / sum(len(values) for values in clusters.values())
                for candidate, clusters in candidates.items()
            }
            for rank, (candidate, mean) in enumerate(
                sorted(means.items(), key=lambda item: (-item[1], item[0])), start=1
            ):
                extras["horizon_ranking"].append(
                    {
                        "group": group,
                        "lead": lead,
                        "candidate": candidate,
                        "mean_normalized_improvement": mean,
                        "rank": rank,
                        "in_confidence_set": candidate in superior["members"],
                    }
                )

        ranking = {row["id"]: row for row in result["ranking"]}
        for group, candidates in sorted(path_scores.items()):
            model_set = _superior_set(
                candidates, draws, seed, alpha, f"path:{group}"
            )
            extras["model_confidence_sets"].append({"group": group, **model_set})
            horizon_means = {}
            for (one_group, lead), by_candidate in horizon_scores.items():
                if one_group != group:
                    continue
                horizon_means[lead] = {
                    candidate: sum(map(sum, clusters.values()))
                    / sum(len(values) for values in clusters.values())
                    for candidate, clusters in by_candidate.items()
                }
            best_by_h = {
                lead: max(values.values()) for lead, values in horizon_means.items()
            }
            average_spa = _family_spa(
                candidates, draws, seed, f"average-spa:{group}"
            )
            horizon_spa = {
                lead: _family_spa(
                    horizon_scores[(group, lead)],
                    draws,
                    seed,
                    f"horizon-spa:{group}:{lead}",
                )
                for lead in sorted(horizon_means)
            }
            tie_rows = []
            for candidate in sorted(candidates):
                per_h = {
                    str(lead): horizon_spa[lead][candidate]
                    for lead in sorted(horizon_spa)
                }
                uniform_p = max(row["p_value"] for row in per_h.values())
                extras["superior_predictive_ability"].append(
                    {
                        "group": group,
                        "candidate": candidate,
                        "average": average_spa[candidate],
                        "per_horizon": per_h,
                        "uniform_p_value": uniform_p,
                        "uniform_pass": uniform_p <= alpha
                        and all(row["mean"] > 0.0 for row in per_h.values()),
                        "selection_adjustment": (
                            "shared recentered candidate-family max-t; "
                            "uniform result is intersection-union across horizons"
                        ),
                    }
                )
                if candidate not in model_set["members"]:
                    continue
                worst_regret = max(
                    best_by_h[lead] - horizon_means[lead][candidate]
                    for lead in horizon_means
                )
                std = ranking[candidate].get("std")
                stability = float(std) if isinstance(std, (int, float)) else math.inf
                tie_rows.append(
                    (
                        stability,
                        worst_regret,
                        ranking[candidate]["compute_rank"],
                        candidate,
                    )
                )
            chosen = min(tie_rows)[3]
            extras["selection"].append(
                {
                    "group": group,
                    "candidate": chosen,
                    "superior_set": model_set["members"],
                    "tie_break": "fold stability, worst-horizon regret, compute rank, id",
                    "auto_promote": False,
                }
            )
        result.update(extras)
        return result
