"""Digest-pinned post-selection evidence and horizon gates (ADR-0110)."""

from __future__ import annotations

import copy
import hashlib
import json
import os

from dskit.pipeline.stages import Stage, reject_unknown_params

__all__ = [
    "DEVELOPMENT_EVIDENCE_SCOPE",
    "FinalModelGateInventory",
    "FinalModelGates",
]

_INVENTORY_PARAMS = (
    "run_artifact", "run_sha256", "compare_artifact", "compare_sha256"
)
_GATE_PARAMS = (
    "manifest_artifact", "manifest_sha256", "alpha", "correction",
    "evidence_scope", "season_timezone", "season_months",
)
_SCOPE = "developmental_post_selection"
#: The public name for the P16 development evidence scope — what this
#: module stamps on every winner/cap it emits. Exported (ADR-0121) so the
#: confirmed-cap validator can refuse any cap artifact claiming this same
#: scope: confirmation caps must come from evidence NOT used to choose the
#: P16 mask (plan §6 Phase 4 item 4).
DEVELOPMENT_EVIDENCE_SCOPE = _SCOPE
_SELECTION = "simplicity_heuristic_after_no_detected_difference"


def _string(value):
    return isinstance(value, str) and bool(value.strip())


def _sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _resolve(source_path, declared):
    return os.path.realpath(
        os.path.join(os.path.dirname(os.path.abspath(source_path)), declared)
    )


def _read_pinned_json(source_path, declared, expected, label):
    path = _resolve(source_path, declared)
    with open(path, "rb") as handle:
        raw = handle.read()
    observed = hashlib.sha256(raw).hexdigest()
    if observed != expected:
        raise ValueError(f"{label} hash changed: {expected} -> {observed}")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    return path, value


def _outputs(artifact, label):
    value = artifact.get("outputs") if isinstance(artifact, dict) else None
    if not isinstance(value, dict):
        raise ValueError(f"{label} has no outputs object")
    return value


def _pin_problems(params, path_field, sha_field):
    problems = []
    if not _string(params.get(path_field)):
        problems.append(f"{path_field} must be a non-empty string")
    if not _sha256(params.get(sha_field)):
        problems.append(f"{sha_field} must be a lowercase SHA-256")
    return problems


def _verified_prediction_snapshot(pins, cutoff):
    """Copy and hash each pin from one read, returning an immutable snapshot."""
    import tempfile

    guard = tempfile.TemporaryDirectory(prefix="dskit-gate-evidence-")
    declared = []
    try:
        for index, pin in enumerate(pins):
            path = pin.get("path") if isinstance(pin, dict) else None
            expected = pin.get("sha256") if isinstance(pin, dict) else None
            if not _string(path) or not _sha256(expected):
                raise ValueError(f"fold {cutoff!r} has malformed prediction pin")
            path = os.path.realpath(path)
            with open(path, "rb") as handle:
                raw = handle.read()
            observed = hashlib.sha256(raw).hexdigest()
            if observed != expected:
                raise ValueError(
                    f"prediction artifact hash changed: {expected} -> {observed}"
                )
            target_dir = os.path.join(
                guard.name, "artifacts", f"pin-{index:04d}"
            )
            os.makedirs(target_dir)
            with open(
                os.path.join(target_dir, "predictions.parquet"), "wb"
            ) as handle:
                handle.write(raw)
            declared.append(path)
        return guard, declared
    except BaseException:
        guard.cleanup()
        raise


def _same_benchmark(run_artifact, compare_artifact):
    run_token = run_artifact.get("stage_token")
    compare_token = compare_artifact.get("stage_token")
    if (
        not _string(run_token)
        or not run_token.endswith(":run")
        or not _string(compare_token)
        or not compare_token.endswith(":compare")
        or run_token.rsplit(":", 1)[0] != compare_token.rsplit(":", 1)[0]
    ):
        raise ValueError(
            "run and comparison artifacts must come from the same staged "
            "benchmark identity"
        )
    return run_token.rsplit(":", 1)[0]


def _selected_candidate(run_artifact, compare_artifact):
    runs = _outputs(run_artifact, "benchmark run artifact").get("runs")
    ranking = _outputs(compare_artifact, "benchmark comparison artifact").get(
        "ranking"
    )
    if not isinstance(runs, list) or not runs:
        raise ValueError("benchmark run artifact has no runs list")
    if not isinstance(ranking, list) or not ranking:
        raise ValueError("benchmark comparison artifact has no ranking list")
    selected = [
        row for row in ranking
        if isinstance(row, dict)
        and row.get("selected_simplest_not_detectably_different") is True
    ]
    if len(selected) != 1 or not _string(selected[0].get("id")):
        raise ValueError("comparison must declare exactly one simplicity-heuristic winner")
    matches = [
        row for row in runs
        if isinstance(row, dict) and row.get("id") == selected[0]["id"]
    ]
    if len(matches) != 1 or matches[0].get("state") != "ran":
        raise ValueError("selected candidate must have exactly one completed run")
    return selected[0], matches[0]


def _validate_compare_provenance(compare_artifact, identity, candidate):
    provenance = _outputs(
        compare_artifact, "benchmark comparison artifact"
    ).get("provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("benchmark_hash") != identity
        or provenance.get("asof") != candidate.get("asof")
    ):
        raise ValueError(
            "comparison provenance must match the selected run identity and asof"
        )


class FinalModelGateInventory(Stage):
    """Hash every saved prediction needed by the developmental gate run."""

    outputs = ("manifest",)

    @classmethod
    def validate_params(cls, params):
        """Return every malformed benchmark-artifact pin."""
        problems = []
        reject_unknown_params(problems, params, _INVENTORY_PARAMS)
        problems.extend(_pin_problems(params, "run_artifact", "run_sha256"))
        problems.extend(
            _pin_problems(params, "compare_artifact", "compare_sha256")
        )
        return problems

    def validate_inputs(self, inputs):
        """Refuse inputs because the inventory reads only pinned artifacts."""
        return [] if inputs == {} else ["FinalModelGateInventory takes no inputs"]

    def run(self, ctx, inputs):
        """Select the heuristic winner and emit a content manifest of its rows."""
        from dskit.pipeline.document import load_document
        from dskit.pipeline.predictions import find_predictions

        del inputs
        run_path, run_artifact = _read_pinned_json(
            ctx.source_path, self.params["run_artifact"],
            self.params["run_sha256"], "benchmark run artifact",
        )
        compare_path, compare_artifact = _read_pinned_json(
            ctx.source_path, self.params["compare_artifact"],
            self.params["compare_sha256"], "benchmark comparison artifact",
        )
        identity = _same_benchmark(run_artifact, compare_artifact)
        selected, candidate = _selected_candidate(run_artifact, compare_artifact)
        _validate_compare_provenance(
            compare_artifact, identity, candidate
        )
        candidate_path = _resolve(ctx.source_path, candidate.get("path", ""))
        document = load_document(candidate_path)
        if document.hash != candidate.get("document_hash"):
            raise ValueError("selected candidate document hash drifted")
        path_node = document.pipeline.get("path")
        asset_horizons = None if path_node is None else path_node.params.get(
            "asset_horizons"
        )
        if not isinstance(asset_horizons, list) or not asset_horizons:
            raise ValueError("selected candidate has no approved asset_horizons")
        expected_units = []
        for row in asset_horizons:
            asset = row.get("asset") if isinstance(row, dict) else None
            horizon = row.get("horizon") if isinstance(row, dict) else None
            if (
                not _string(asset) or isinstance(horizon, bool)
                or not isinstance(horizon, int) or horizon < 1
            ):
                raise ValueError("selected candidate has malformed asset_horizons")
            expected_units.extend(
                {"symbol": asset, "horizon": lead}
                for lead in range(1, horizon + 1)
            )
        if len({(r["symbol"], r["horizon"]) for r in expected_units}) != len(
            expected_units
        ):
            raise ValueError("selected candidate has duplicate expected units")

        summary_dir = os.path.realpath(candidate.get("summary_dir", ""))
        summary_path = os.path.join(summary_dir, "walkforward.json")
        sealed_path = candidate.get("evidence_manifest_path")
        sealed_sha = candidate.get("evidence_manifest_sha256")
        if not _string(sealed_path) or not _sha256(sealed_sha):
            raise ValueError("selected candidate has no original evidence seal")
        pinned_path, summary = _read_pinned_json(
            ctx.source_path, sealed_path, sealed_sha,
            "selected candidate original evidence seal",
        )
        if pinned_path != os.path.realpath(summary_path):
            raise ValueError("selected candidate evidence seal points outside its summary")
        folds = summary.get("folds") if isinstance(summary, dict) else None
        sealed = summary.get("evidence") if isinstance(summary, dict) else None
        expected_cutoffs = candidate.get("expected_cutoffs")
        if (
            not isinstance(summary, dict) or summary.get("state") != "ran"
            or summary.get("document_hash") != candidate.get("document_hash")
            or summary.get("asof") != candidate.get("asof")
            or summary.get("objective") != candidate.get("objective")
            or summary.get("select") != candidate.get("select")
            or not isinstance(folds, list) or not folds
            or len(folds) != candidate.get("expected_fold_count")
            or [fold.get("cutoff") for fold in folds] != expected_cutoffs
            or any(fold.get("state") != "ran" for fold in folds)
            or not isinstance(sealed, dict) or sealed.get("schema_version") != 2
            or sealed.get("contract")
            != "walkforward_fold_artifacts_at_summary_publish"
            or not isinstance(sealed.get("folds"), list)
        ):
            raise ValueError("selected candidate walk-forward contract drifted")
        run_dirs = [os.path.realpath(fold.get("run_dir", "")) for fold in folds]
        if len(set(run_dirs)) != len(run_dirs):
            raise ValueError("walk-forward folds need unique run directories")
        manifest_folds = []
        if len(sealed["folds"]) != len(folds):
            raise ValueError("selected candidate evidence fold count drifted")
        for fold, run_dir, sealed_fold in zip(folds, run_dirs, sealed["folds"]):
            if (
                not isinstance(sealed_fold, dict)
                or sealed_fold.get("cutoff") != fold["cutoff"]
                or os.path.realpath(sealed_fold.get("run_dir", "")) != run_dir
            ):
                raise ValueError("selected candidate evidence fold identity drifted")
            carry = sealed_fold.get("carry")
            carry_path = carry.get("path") if isinstance(carry, dict) else None
            carry_digest = carry.get("sha256") if isinstance(carry, dict) else None
            expected_carry = os.path.realpath(os.path.join(run_dir, "carry.json"))
            if (
                not _string(carry_path)
                or not _sha256(carry_digest)
                or os.path.realpath(carry_path) != expected_carry
            ):
                raise ValueError(f"fold {fold['cutoff']!r} has malformed carry pin")
            if _digest(expected_carry) != carry_digest:
                raise ValueError("carry drifted from original run seal")
            pins = sealed_fold.get("predictions")
            if not isinstance(pins, list) or not pins:
                raise ValueError(f"fold {fold['cutoff']!r} has no saved predictions")
            declared = []
            for pin in pins:
                path = pin.get("path") if isinstance(pin, dict) else None
                digest = pin.get("sha256") if isinstance(pin, dict) else None
                if not _string(path) or not _sha256(digest):
                    raise ValueError(f"fold {fold['cutoff']!r} has malformed evidence pin")
                path = os.path.realpath(path)
                if _digest(path) != digest:
                    raise ValueError("prediction drifted from original run seal")
                declared.append(path)
            found = [os.path.realpath(path) for path in find_predictions(run_dir)]
            if sorted(declared) != sorted(found) or len(set(declared)) != len(declared):
                raise ValueError("prediction inventory drifted from original run seal")
            manifest_folds.append(
                {
                    "cutoff": fold["cutoff"],
                    "run_dir": run_dir,
                    "carry": copy.deepcopy(carry),
                    "predictions": copy.deepcopy(pins),
                }
            )
        return {
            "manifest": {
                "schema_version": 1,
                "benchmark_identity": identity,
                "evidence_scope": _SCOPE,
                "selection_caveat": (
                    "Non-rejection is not equivalence; this is a simplicity "
                    "heuristic on development folds, not confirmation."
                ),
                "winner": {
                    "id": selected["id"],
                    "feature_policy": candidate.get("feature_policy"),
                    "mean_path_score": selected.get("mean"),
                    "selection_rule": _SELECTION,
                    "document_hash": candidate["document_hash"],
                    "asof": candidate.get("asof"),
                    "objective": candidate.get("objective"),
                    "select": candidate.get("select"),
                },
                "sources": {
                    "run": {"path": run_path, "sha256": self.params["run_sha256"]},
                    "compare": {
                        "path": compare_path,
                        "sha256": self.params["compare_sha256"],
                    },
                    "candidate": {
                        "path": os.path.realpath(candidate_path),
                        "document_hash": document.hash,
                    },
                },
                "summary": {"path": pinned_path, "sha256": sealed_sha},
                "expected_units": sorted(
                    expected_units, key=lambda row: (row["symbol"], row["horizon"])
                ),
                "folds": manifest_folds,
            }
        }


class FinalModelGates(Stage):
    """Gate a manifest-pinned heuristic winner without re-fitting it."""

    outputs = ("winner", "evidence", "caps", "metrics")

    @classmethod
    def validate_params(cls, params):
        """Return every malformed manifest pin, threshold, or season map."""
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        problems = []
        reject_unknown_params(problems, params, _GATE_PARAMS)
        problems.extend(
            _pin_problems(params, "manifest_artifact", "manifest_sha256")
        )
        alpha = params.get("alpha")
        if (
            isinstance(alpha, bool) or not isinstance(alpha, (int, float))
            or not 0.0 < alpha < 1.0
        ):
            problems.append("alpha must be a number in (0, 1)")
        if params.get("correction") != "bonferroni":
            problems.append("correction must be 'bonferroni'")
        if params.get("evidence_scope") != _SCOPE:
            problems.append(f"evidence_scope must be {_SCOPE!r}")
        timezone = params.get("season_timezone")
        if not _string(timezone):
            problems.append("season_timezone must be a non-empty string")
        else:
            try:
                ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError):
                problems.append("season_timezone must name an installed timezone")
        seasons = params.get("season_months")
        months = []
        if not isinstance(seasons, dict) or not seasons:
            problems.append("season_months must be a non-empty object")
        else:
            for name, values in seasons.items():
                if not _string(name):
                    problems.append("season_months keys must be non-empty strings")
                if (
                    not isinstance(values, list) or not values
                    or any(
                        isinstance(value, bool) or not isinstance(value, int)
                        or not 1 <= value <= 12 for value in values
                    )
                ):
                    problems.append(
                        f"season_months[{name!r}] must be a non-empty list "
                        "of months 1..12"
                    )
                else:
                    months.extend(values)
            if sorted(months) != list(range(1, 13)):
                problems.append("season_months must partition months 1..12 exactly once")
        return problems

    def validate_inputs(self, inputs):
        """Refuse inputs because this stage reads one pinned manifest."""
        return [] if inputs == {} else ["FinalModelGates takes no inputs"]

    def run(self, ctx, inputs):
        """Verify every dependency, compute corrected skill, slice, and cap."""
        import sys
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from dskit.pipeline.conquest import HorizonConquest
        from dskit.pipeline.predictions import (
            find_predictions, read_prediction_series, read_predictions,
        )
        from dskit.pipeline.stats import correction, skill_vs_mean

        del inputs
        manifest_path, artifact = _read_pinned_json(
            ctx.source_path, self.params["manifest_artifact"],
            self.params["manifest_sha256"], "gate evidence manifest",
        )
        manifest = _outputs(artifact, "gate evidence manifest").get("manifest")
        if (
            not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("evidence_scope") != self.params["evidence_scope"]
            or not isinstance(manifest.get("winner"), dict)
            or manifest["winner"].get("selection_rule") != _SELECTION
        ):
            raise ValueError("gate evidence manifest contract is invalid")
        summary = manifest.get("summary")
        if not isinstance(summary, dict) or not _sha256(summary.get("sha256")):
            raise ValueError("gate evidence manifest has no pinned summary")
        observed_summary = _digest(summary.get("path", ""))
        if observed_summary != summary["sha256"]:
            raise ValueError(
                f"walk-forward summary hash changed: {summary['sha256']} -> "
                f"{observed_summary}"
            )
        expected_rows = manifest.get("expected_units")
        if not isinstance(expected_rows, list) or not expected_rows:
            raise ValueError("gate evidence manifest has no expected units")
        expected = {
            (row.get("symbol"), row.get("horizon"))
            for row in expected_rows if isinstance(row, dict)
        }
        if (
            len(expected) != len(expected_rows)
            or any(not _string(symbol) or isinstance(horizon, bool)
                   or not isinstance(horizon, int) or horizon < 1
                   for symbol, horizon in expected)
        ):
            raise ValueError("gate evidence manifest expected units are malformed")
        folds = manifest.get("folds")
        if not isinstance(folds, list) or len(folds) < 2:
            raise ValueError("gate evidence manifest needs at least two folds")

        by_unit = {key: [] for key in expected}
        last_stamp = {}
        seen_cutoffs = set()
        for fold_index, fold in enumerate(folds):
            cutoff = fold.get("cutoff") if isinstance(fold, dict) else None
            run_dir = fold.get("run_dir") if isinstance(fold, dict) else None
            pins = fold.get("predictions") if isinstance(fold, dict) else None
            if not _string(cutoff) or cutoff in seen_cutoffs or not _string(run_dir):
                raise ValueError("gate evidence manifest has malformed fold identity")
            seen_cutoffs.add(cutoff)
            if not isinstance(pins, list) or not pins:
                raise ValueError(f"fold {cutoff!r} has no prediction pins")
            snapshot, declared_paths = _verified_prediction_snapshot(pins, cutoff)
            found = [os.path.realpath(path) for path in find_predictions(run_dir)]
            if sorted(declared_paths) != sorted(found) or len(set(found)) != len(found):
                snapshot.cleanup()
                raise ValueError(f"fold {cutoff!r} prediction inventory drifted")
            raw = read_predictions(snapshot.name)
            if not raw:
                snapshot.cleanup()
                raise ValueError(f"fold {cutoff!r} has no saved predictions")
            raw_keys = set()
            mus = {}
            for index, symbol in enumerate(raw["series"]):
                key = (symbol, int(raw["horizon"][index]))
                raw_keys.add(key)
                if int(raw["fold"][index]) != fold_index:
                    raise ValueError(f"fold {cutoff!r} stores the wrong fold ordinal")
                mus.setdefault(key, set()).add(float(raw["mu"][index]))
            if raw_keys != expected:
                raise ValueError("prediction units differ from approved inventory")
            if any(len(values) != 1 for values in mus.values()):
                raise ValueError(f"fold {cutoff!r} has a non-constant benchmark mean")
            units = read_prediction_series(snapshot.name)
            snapshot.cleanup()
            keys = {(unit["symbol"], int(unit["lead"])) for unit in units}
            if keys != expected or len(units) != len(expected):
                raise ValueError("prediction units differ from approved inventory")
            for unit in units:
                key = (unit["symbol"], int(unit["lead"]))
                stamps = unit["stamps"]
                if not stamps or any(left >= right for left, right in zip(stamps, stamps[1:])):
                    raise ValueError(f"{key!r} timestamps are not strictly increasing")
                if key in last_stamp and stamps[0] <= last_stamp[key]:
                    raise ValueError(f"{key!r} fold timestamps overlap or go backward")
                last_stamp[key] = stamps[-1]
                by_unit[key].append(unit)

        alpha = float(self.params["alpha"])
        skills = {}
        raw_pvalues = {}
        for key, units in sorted(by_unit.items()):
            h_steps = {int(unit["h_steps"]) for unit in units}
            if len(h_steps) != 1:
                raise ValueError(f"{key!r} changed row spacing")
            skill = skill_vs_mean(units, h_steps=h_steps.pop(), alpha=alpha)
            skills[key] = skill
            raw_pvalues[key] = max(float(skill["p_pool"]), float(skill["p_fold"]))
        safe_pvalues = {
            f"{symbol}:h{horizon:02d}": max(value, sys.float_info.min)
            for (symbol, horizon), value in raw_pvalues.items()
        }
        corrected = correction(self.params["correction"])["fn"](
            safe_pvalues, alpha
        )
        family_size = len(raw_pvalues)

        timezone = ZoneInfo(self.params["season_timezone"])
        season_for_month = {
            month: season for season, months in self.params["season_months"].items()
            for month in months
        }
        season_names = sorted(self.params["season_months"])
        evidence = []
        for (symbol, horizon), units in sorted(by_unit.items()):
            skill = skills[(symbol, horizon)]
            cell = f"{symbol}:h{horizon:02d}"
            seasonal = {
                name: {"sse_model": 0.0, "sse_mean": 0.0, "n": 0}
                for name in season_names
            }
            for unit in units:
                for stamp, y, yhat in zip(unit["stamps"], unit["y"], unit["yhat"]):
                    season = season_for_month[
                        datetime.fromtimestamp(stamp / 1000.0, timezone).month
                    ]
                    bucket = seasonal[season]
                    bucket["sse_model"] += (y - yhat) ** 2
                    bucket["sse_mean"] += (y - unit["mu"]) ** 2
                    bucket["n"] += 1
            for season in season_names:
                bucket = seasonal[season]
                if bucket["n"] < 1 or bucket["sse_mean"] <= 0.0:
                    raise ValueError(
                        f"{symbol} horizon {horizon} lacks usable {season!r} evidence"
                    )
                raw_p = raw_pvalues[(symbol, horizon)]
                evidence.append(
                    {
                        "symbol": symbol, "horizon": horizon, "season": season,
                        "beats_mean": skill["r2oos_pool"] > 0.0,
                        "skill_pass_raw": skill["passes"],
                        "skill_pass_adjusted": bool(
                            skill["passes"] and corrected[cell]
                        ),
                        "r2oos": skill["r2oos_pool"],
                        "p_pool": skill["p_pool"], "p_fold": skill["p_fold"],
                        "p_family_raw": raw_p,
                        "p_family_adjusted": min(1.0, raw_p * family_size),
                        "family_size": family_size,
                        "n_folds": skill["n_folds"], "n_rows": skill["n_rows"],
                        "season_r2oos": 1.0 - bucket["sse_model"] / bucket["sse_mean"],
                        "season_n_rows": bucket["n"],
                    }
                )
        gate = HorizonConquest(
            "gate",
            {
                "unit_field": "symbol", "horizon_field": "horizon",
                "slice_field": "season",
                "checks": [
                    {"metric": "beats_mean", "pass_if": "boolean"},
                    {"metric": "skill_pass_adjusted", "pass_if": "boolean"},
                    {"metric": "season_r2oos", "pass_if": "positive"},
                ],
            },
        )
        verdict = gate.run(None, {"records": evidence})
        caps = verdict["caps"]
        winner = copy.deepcopy(manifest["winner"])
        winner.update(
            {
                "evidence_scope": _SCOPE,
                "deployment_eligible": False,
                "selection_caveat": manifest.get("selection_caveat"),
            }
        )
        return {
            "winner": winner, "evidence": evidence, "caps": caps,
            "metrics": {
                **verdict["metrics"],
                "n_stock_horizons": len(by_unit),
                "n_evidence_rows": len(evidence),
                "n_served_units": sum(1 for row in caps if row["capped_horizon"] > 0),
                "family_size": family_size,
                "alpha": alpha,
                "correction": self.params["correction"],
                "evidence_scope": _SCOPE,
                "deployment_eligible": False,
                "manifest_artifact": manifest_path,
            },
        }
