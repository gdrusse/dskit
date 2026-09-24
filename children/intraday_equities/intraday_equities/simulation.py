"""Development simulation nodes (ADR-0182): forecast publisher and per-tick MIO decider.

The simulation replays the P16 walk-forward as production would have run
it: each outer fold is one model RELEASE, retrained on the calendar's
schedule, and its stored out-of-sample ``yhat`` is the inference that
release made at each 30-minute lattice instant. This module turns that
pinned evidence into the two things the capital step consumes -- per
release, a cap artifact plus attested uncertainty; per tick, one
:class:`~intraday_equities.forecast_bundle.ForecastBundle` per lead group
-- as JSON, so every downstream node receives data, never Python objects.
The ``decide`` node carries the validated ``EquityKellyMIO`` params, and
:class:`MioDecider` is the per-tick strategy an ``EquityReplay`` calls to
size each lattice tick from those bundles and the replay's live cash.

Point in time, by construction rather than by care: a release's
calibration reads ONLY rows of earlier folds stamped before its cutoff,
and the tick path reads ``yhat`` through a column projection that never
loads the realized ``y``. The tradable set and leads are read from the
digest-pinned gate artifact, never restated; that admission used evidence
through the end of the last fold, which every artifact here discloses
(developmental post-selection). Nothing here authorizes deployment.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta, timezone

from dskit.pipeline.attempts import (
    merge_session_totals,
    session_flip_nulls,
    session_totals,
    utc_day,
)
from dskit.pipeline.false_signal import (
    FalseSignalEstimate,
    GrenanderLocalFdr,
    SignalEvidence,
)
from dskit.pipeline.node import Node, class_ref, register_node_kind, reject_unknown_params
from dskit.pipeline.outcome_interval import (
    MAX_SCENARIOS,
    MIN_SCENARIOS,
    BlockConformalInterval,
    BlockResiduals,
    OutcomeIntervalResult,
)
from dskit.pipeline.program_calendar import load_program_calendar
from dskit.pipeline.stages import is_sha256hex
from dskit.pipeline.uncertainty_intake import (
    AttestedFalseSignalRate,
    AttestedOutcomeBand,
    CoverageEvidence,
    UncertaintyAttestation,
)
from dskit.production.base import canonical_hash

from .final_gates import (
    DEVELOPMENT_EVIDENCE_SCOPE,
    _outputs,
    _read_pinned_json,
    _verified_prediction_snapshot,
)
from .final_model import _epoch_ms
from .forecast_bundle import ConfirmedCaps, ForecastBundle
from .nodes_capital import EquityKellyMIO, SchwabCostModel
from .nodes import (
    LABEL_PARAMS,
    LABEL_RETURN_BASIS,
    Universe,
    _label_from_params,
    _resolve_path,
    _tapes_from_bars,
)

__all__ = ["NODE_KINDS", "ForecastPublisher", "MioDecider", "MioDeciderNode"]

#: ADR-0152's measured attainment floor for the widened false-signal
#: reading. It is NOT measured on this panel; the evidence id says so.
_FALSE_SIGNAL_ATTAINMENT_FLOOR = 0.53
_FALSE_SIGNAL_EVIDENCE_ID = "ADR-0152-synthetic-attainment-floor-not-measured-on-this-panel"

_DAY_MS = 86_400_000
_SCAN_KIND = "intraday_equities-no-information-scan"
_UNIVERSE_KIND = "intraday_equities-universe"
_CACHE_KIND = "intraday_equities-session-feature-cache"
_UNCERTAINTY_KNOBS = ("n_scenarios", "coverage", "window_blocks", "null_draws", "seed")
#: Market fields are known at the tick; calibrated fields at the release cutoff.
_MARKET_FIELDS = ("sigma", "beta", "reference", "price", "yhat")
_CALIBRATED_FIELDS = ("pi_hat", "pi_widened", "scenarios")


def _plain(value):
    """Return ``value`` as plain JSON (dataclasses and mappings to dict, sequences to list)."""
    if is_dataclass(value):
        return _dataclass_obj(value)
    if hasattr(value, "items"):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _dataclass_obj(value):
    """One frozen dataclass as a JSON object of its fields."""
    return {spec.name: _plain(getattr(value, spec.name)) for spec in fields(value)}


def _attestation_from(obj):
    """Rebuild an :class:`UncertaintyAttestation` from its JSON object."""
    coverage = obj.get("coverage")
    return UncertaintyAttestation(
        **{**obj, "coverage": None if coverage is None else CoverageEvidence(**coverage)}
    )


class _Walk:
    """The pinned P16 walk: inventory, gate admission, calendar geometry, per-fold reads.

    Every read is verified before it is used: both JSON artifacts by
    sha256, every prediction file by its inventory pin, each fold's
    document by its recorded identity, and each feature cache by its
    manifest. Call :meth:`close` to drop the verified snapshots.
    """

    def __init__(self, params):
        self._params = params
        self._guards = []
        self._verified_caches = set()
        self._series = {}
        inventory_path, inventory = self._pinned(
            params["inventory_manifest"], params["inventory_manifest_sha256"],
            "gate inventory manifest",
        )
        gates_path, gates = self._pinned(params["gates"], params["gates_sha256"], "gate artifact")
        self.gates_sha256 = params["gates_sha256"]
        self.generated_ms = os.stat(gates_path).st_mtime_ns // 1_000_000
        manifest = _outputs(inventory, "gate inventory manifest").get("manifest")
        if not isinstance(manifest, dict) or not isinstance(manifest.get("folds"), list):
            raise ValueError("gate inventory manifest has no folds")
        winner = manifest.get("winner")
        if not isinstance(winner, dict) or not isinstance(winner.get("id"), str) or not winner["id"]:
            raise ValueError("gate inventory manifest names no winner id")
        self.model_id = winner["id"]
        self.lead_map = self._admission(gates, inventory_path, manifest)
        try:
            self.folds = self._verified_folds(manifest["folds"])
        except BaseException:
            self.close()
            raise
        self.evidence_end_ms = self.folds[-1]["val_end_ms"]

    def close(self):
        """Remove every verified prediction snapshot."""
        for guard in self._guards:
            guard.cleanup()
        self._guards = []

    @staticmethod
    def _pinned(declared, expected, label):
        """Read one sha256-pinned JSON artifact from a child-relative or absolute path."""
        path = _resolve_path(declared)
        return _read_pinned_json(path, path, expected, label)

    @staticmethod
    def _admission(gates, inventory_path, manifest):
        """Read the gate-admitted ``{symbol: capped_horizon}`` for ``capped_horizon > 0``."""
        outputs = _outputs(gates, "gate artifact")
        metrics = outputs.get("metrics")
        if not isinstance(metrics, dict) or not isinstance(metrics.get("manifest_artifact"), str):
            raise ValueError("gate artifact names no manifest_artifact")
        if os.path.realpath(metrics["manifest_artifact"]) != os.path.realpath(inventory_path):
            raise ValueError(
                f"gate artifact was computed from manifest {metrics['manifest_artifact']!r}, "
                f"not the pinned inventory {inventory_path!r}"
            )
        if (
            metrics.get("evidence_scope") != DEVELOPMENT_EVIDENCE_SCOPE
            or metrics.get("deployment_eligible") is not False
        ):
            raise ValueError("gate artifact must be developmental and deployment-ineligible")
        caps = outputs.get("caps")
        if not isinstance(caps, list) or not caps:
            raise ValueError("gate artifact has no caps")
        units = {
            (row.get("symbol"), row.get("horizon"))
            for row in manifest.get("expected_units") or ()
            if isinstance(row, dict)
        }
        lead_map = {}
        for row in caps:
            symbol = row.get("unit") if isinstance(row, dict) else None
            horizon = row.get("capped_horizon") if isinstance(row, dict) else None
            if not isinstance(symbol, str) or isinstance(horizon, bool) or not isinstance(horizon, int):
                raise ValueError(f"gate artifact has a malformed cap row {row!r}")
            if horizon > 0:
                if (symbol, horizon) not in units:
                    raise ValueError(f"admitted unit {symbol}:h{horizon:02d} was never modeled")
                lead_map[symbol] = horizon
        if not lead_map:
            raise ValueError("gate artifact admits no unit")
        return lead_map

    def _verified_folds(self, folds):
        """Check the calendar geometry and document identity of every fold; snapshot its pins."""
        from dskit.pipeline.driver import RunAttestation
        from dskit.pipeline.predictions import find_predictions

        calendar_path = _resolve_path(self._params["program_calendar"])
        calendar, _digest = load_program_calendar(calendar_path, calendar_path)
        schedule = calendar["fold_schedules"].get(self._params["fold_schedule"])
        if schedule is None:
            raise ValueError(f"program calendar has no fold schedule {self._params['fold_schedule']!r}")
        if len(folds) != schedule["count"]:
            raise ValueError(f"inventory holds {len(folds)} folds, the calendar declares {schedule['count']}")
        first = date.fromisoformat(schedule["first"])
        verified, problems = [], []
        for index, fold in enumerate(folds):
            run_dir = fold.get("run_dir") if isinstance(fold, dict) else None
            with open(os.path.join(run_dir, "resolved.json"), encoding="utf-8") as handle:
                resolved = json.load(handle)
            cutoff = first + timedelta(days=schedule["step_days"] * index)
            problems.extend(self._geometry_problems(index, fold, resolved["splits"], cutoff, schedule))
            if verified and resolved["splits"]["val_start_ms"] != verified[-1]["val_end_ms"] + 1:
                problems.append(f"fold {index} does not start where fold {index - 1} ended")
            if not RunAttestation(run_dir).binds_document_identity(resolved.get("document_hash")):
                problems.append(f"fold {index} document does not reproduce its recorded identity")
            if not is_sha256hex(resolved.get("run_hash")):
                problems.append(f"fold {index} has no sha256 run_hash")
            if problems:
                continue
            guard, declared = _verified_prediction_snapshot(fold["predictions"], fold["cutoff"])
            self._guards.append(guard)
            found = sorted(os.path.realpath(path) for path in find_predictions(run_dir))
            if sorted(declared) != found:
                problems.append(f"fold {index} prediction inventory drifted")
            with open(os.path.join(run_dir, "config.json"), encoding="utf-8") as handle:
                config = json.load(handle)
            verified.append(
                {
                    "index": index,
                    "cutoff": fold["cutoff"],
                    "cutoff_ms": _epoch_ms(cutoff.isoformat()),
                    "val_end_ms": resolved["splits"]["val_end_ms"],
                    "run_hash": resolved["run_hash"],
                    "resolved": resolved,
                    "config": config,
                    "snapshot": guard.name,
                }
            )
        if problems:
            raise ValueError("P16 walk refused: " + "; ".join(problems))
        return verified

    @staticmethod
    def _geometry_problems(index, fold, splits, cutoff, schedule):
        """Problems with one fold's splits against the calendar's fold schedule."""
        embargo_end = cutoff - timedelta(days=schedule["embargo_days"])
        expected = {
            "val_start_ms": _epoch_ms(cutoff.isoformat()),
            "val_end_ms": _epoch_ms((cutoff + timedelta(days=schedule["val_days"])).isoformat()) - 1,
            "train_end_ms": _epoch_ms(embargo_end.isoformat()) - 1,
            "train_start_ms": _epoch_ms((embargo_end - timedelta(days=schedule["train_days"])).isoformat()),
        }
        problems = []
        if fold.get("cutoff") != cutoff.isoformat():
            problems.append(f"fold {index} cutoff {fold.get('cutoff')!r} is not {cutoff.isoformat()}")
        if not splits.get("train_end_ms", math.inf) < expected["val_start_ms"]:
            problems.append(f"fold {index} train_end_ms {splits.get('train_end_ms')!r} is not before its cutoff")
        for name, value in expected.items():
            if splits.get(name) != value:
                problems.append(f"fold {index} {name} {splits.get(name)!r} is not the calendar's {value}")
        return problems

    def series(self, index):
        """Fold ``index``'s scored rows per ``(symbol, lead)``, realized ``y`` included."""
        from dskit.pipeline.predictions import read_prediction_series

        if index not in self._series:
            self._series[index] = read_prediction_series(self.folds[index]["snapshot"])
        return self._series[index]

    def yhat(self, index):
        """Fold ``index``'s ``{(symbol, lead): [(ts, yhat), ...]}``, read WITHOUT ``y``."""
        from dskit.pipeline.predictions import read_predictions

        names = ("ts", "series", "horizon", "yhat")
        table = read_predictions(self.folds[index]["snapshot"], columns=names)
        out = {}
        for stamp, symbol, lead, value in zip(*(table.get(name, ()) for name in names)):
            out.setdefault((symbol, int(lead)), []).append((int(stamp), float(value)))
        return out

    def market(self, index):
        """Build the fold's own label arrays per admitted symbol, and its label contract.

        Returns
        -------
        tuple
            ``({symbol: (stamps, prices, beta, sigma)}, contract)`` where
            ``beta``/``sigma`` are the fold label's own causal per-bar arrays.
        """
        fold = self.folds[index]
        nodes = fold["config"].get("pipeline") or {}
        scans = [spec.get("params") or {} for spec in nodes.values() if spec.get("uses") == _SCAN_KIND]
        knobs = {json.dumps({k: scan.get(k) for k in LABEL_PARAMS}, sort_keys=True) for scan in scans}
        if len(knobs) != 1:
            raise ValueError(f"fold {index} scan nodes disagree on the label (or none exist)")
        scan = scans[0]
        val_end = self._split_value(scan.get("val_end_ms"), fold["resolved"]["splits"], "val_end_ms")
        spec = self._universe(fold, nodes)
        needed = set(self.lead_map) | ({scan["label_residual"]} if scan.get("label_residual") else set())
        arrays = _tapes_from_bars(self._tapes(fold, nodes, needed), spec["price_field"], val_end)
        label = _label_from_params(scan, arrays, int(spec["period_ms"]))
        contract = {
            "label_scale": label.scale,
            "label_residual": label.residual,
            "return_basis": LABEL_RETURN_BASIS,
            "vol_window_minutes": label.vol_window,
            "beta_window_minutes": label.beta_window,
            "vol_floor": label.vol_floor,
        }
        market = {}
        for symbol in sorted(self.lead_map):
            if symbol not in arrays:
                raise ValueError(f"fold {index} has no tape for admitted {symbol!r}")
            beta, sigma = label._prepare(symbol)
            stamps, prices = arrays[symbol]
            market[symbol] = (stamps, prices, beta, sigma)
        return market, contract

    @staticmethod
    def _split_value(value, splits, name):
        """Resolve a scan param that is a ``$splits`` reference or a literal equal to it."""
        if value == f"$splits.{name}" or value == splits.get(name):
            return int(splits[name])
        raise ValueError(f"scan {name} {value!r} disagrees with the fold's split {splits.get(name)!r}")

    def _universe(self, fold, nodes):
        """Load the fold's universe spec, refused unless it reproduces the recorded fingerprint."""
        keys = [key for key, spec in nodes.items() if spec.get("uses") == _UNIVERSE_KIND]
        if len(keys) != 1:
            raise ValueError(f"fold {fold['index']} must declare exactly one universe node")
        params = dict(nodes[keys[0]].get("params") or {})
        params["path"] = os.path.join(self._params["walk_root"], params.get("path", ""))
        node = Universe(keys[0], params)
        recorded = fold["resolved"].get("data_fingerprint", {}).get(keys[0], {})
        if node.fingerprint()["sha256"] != recorded.get("sha256"):
            raise ValueError(f"fold {fold['index']} universe drifted from its recorded fingerprint")
        return node.run(None, {})["spec"]

    def _tapes(self, fold, nodes, needed):
        """Tape frames for ``needed`` symbols from the fold's verified feature caches."""
        from .feature_cache import SessionFeatureCache, verify_feature_cache

        frames, digests = {}, {}
        for key in sorted(k for k, spec in nodes.items() if spec.get("uses") == _CACHE_KIND):
            params = nodes[key].get("params") or {}
            path = os.path.join(self._params["walk_root"], params.get("path", ""))
            sha = params.get("manifest_sha256")
            recorded = fold["resolved"].get("data_fingerprint", {}).get(key, {})
            if sha != recorded.get("manifest_sha256"):
                raise ValueError(f"fold {fold['index']} cache {key} differs from its recorded manifest")
            if (path, sha) not in self._verified_caches:
                verify_feature_cache(path, sha)
                self._verified_caches.add((path, sha))
            with open(os.path.join(path, "manifest.json"), encoding="utf-8") as handle:
                files = json.load(handle)["files"]
            for frame in SessionFeatureCache(key, {"path": path, "manifest_sha256": sha}).run(None, {})["tape"]:
                symbol = frame["symbol"]
                if symbol not in needed:
                    continue
                digest = (files[f"{symbol}.tape.asof_ms.npy"], files[f"{symbol}.tape.close.npy"])
                if symbol in digests and digests[symbol] != digest:
                    raise ValueError(f"fold {fold['index']} caches carry different {symbol} tapes")
                digests[symbol] = digest
                frames.setdefault(symbol, frame)
        return list(frames.values())


class ForecastPublisher(Node):
    """Publish per-release caps and attested uncertainty, and per-tick forecast bundles.

    The ``intraday_equities-forecast-publisher`` kind (role ``data``,
    ADR-0182 S5). Reads the sha256-pinned P16 gate inventory and gate
    artifact, verifies every fold against the program calendar's fold
    schedule, restricts to the gate-admitted units at their capped
    horizons, and emits JSON only:

    * ``releases`` -- one object per fold (segment) ``k`` in
      ``[first_fold, last_fold]``: ``release_id``,
      ``model_manifest_sha256`` (the fold's ``run_hash``), segment
      bounds, ``cap`` (a ``ConfirmedCaps``-shaped artifact with TRUE
      stamps and a scope that names the post-selection evidence),
      ``survivors``, ``lead_map``, ``lead_groups``, per-lead
      ``uncertainty`` (a ``BlockConformalInterval`` band calibrated on
      prior folds' residuals stamped before the cutoff, and a
      ``GrenanderLocalFdr`` estimate over every modeled cell, projected
      to the group's symbols; both with attestations) and
      ``calibration`` provenance. :meth:`envelopes` rebuilds the
      attested envelopes the capital step admits.
    * ``bundles`` -- one object per ``(tick, lead group)``:
      ``fold``, ``release_id``, ``decision_ts``, ``lead`` and ``rows``,
      the :class:`~intraday_equities.forecast_bundle.ForecastBundle`
      rows built from ``yhat`` alone (never ``y``), ``price`` the fold
      tape's decision-bar close, and ``sigma``/``beta`` the fold label's
      own causal values at that bar.

    Parameters
    ----------
    params : dict
        ``inventory_manifest``/``inventory_manifest_sha256`` and
        ``gates``/``gates_sha256`` (pinned stage artifacts, paths absolute
        or child-relative), ``program_calendar`` (child-relative or
        absolute) and ``fold_schedule`` (its key), ``walk_root`` (the
        directory the P16 walk ran from; its fold documents' relative
        paths resolve there), ``first_fold`` (int >= 2: one prior fold
        calibrates and one more measures coverage) and ``last_fold``,
        ``calibration_window_days`` (int >= 1), and ``uncertainty`` --
        exactly ``n_scenarios``, ``coverage``, ``window_blocks``,
        ``null_draws``, ``seed``.

    Examples
    --------
    The shipped simulation's publisher (pins abbreviated)::

        node = ForecastPublisher("publish", {
            "inventory_manifest": "/runs/p16-inventory/stages/inventory.json",
            "inventory_manifest_sha256": "3c07" + "0" * 60,
            "gates": "/runs/p16-gates/stages/gates.json",
            "gates_sha256": "1fb4" + "0" * 60,
            "program_calendar": "configs/program-calendar.json",
            "fold_schedule": "development_outer",
            "walk_root": "/home/me/dskit/children/intraday_equities",
            "first_fold": 2, "last_fold": 19,
            "calibration_window_days": 730,
            "uncertainty": {"n_scenarios": 64, "coverage": 0.95,
                            "window_blocks": 10, "null_draws": 999, "seed": 0},
        })
        out = node.run(ctx, {})
        # -> {"releases": [...], "bundles": [...]}
    """

    role = "data"
    outputs = ("releases", "bundles")
    _PARAMS = (
        "inventory_manifest",
        "inventory_manifest_sha256",
        "gates",
        "gates_sha256",
        "program_calendar",
        "fold_schedule",
        "walk_root",
        "first_fold",
        "last_fold",
        "calibration_window_days",
        "uncertainty",
    )

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none (default-deny, every knob required)."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        missing = sorted(set(cls._PARAMS) - set(params))
        if missing:
            return problems + [f"missing required param(s) {missing}"]
        for name in ("inventory_manifest", "gates", "program_calendar", "fold_schedule", "walk_root"):
            if not isinstance(params[name], str) or not params[name]:
                problems.append(f"{name} must be a non-empty string")
        for name in ("inventory_manifest_sha256", "gates_sha256"):
            if not is_sha256hex(params[name]):
                problems.append(f"{name} must be a lowercase sha256")
        problems.extend(cls._int_problems(params, "first_fold", 2))
        problems.extend(cls._int_problems(params, "last_fold", 2))
        problems.extend(cls._int_problems(params, "calibration_window_days", 1))
        if not problems and params["last_fold"] < params["first_fold"]:
            problems.append("last_fold must be >= first_fold")
        problems.extend(cls._uncertainty_problems(params["uncertainty"]))
        return problems

    @staticmethod
    def _int_problems(params, name, floor):
        """One problem unless ``params[name]`` is a non-bool int >= ``floor``."""
        value = params.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < floor:
            return [f"{name} must be an int >= {floor}, got {value!r}"]
        return []

    @classmethod
    def _uncertainty_problems(cls, knobs):
        """Problems with the ``uncertainty`` block, empty when none."""
        if not isinstance(knobs, dict) or set(knobs) != set(_UNCERTAINTY_KNOBS):
            return [f"uncertainty must carry exactly {list(_UNCERTAINTY_KNOBS)}, got {knobs!r}"]
        problems = []
        n = knobs["n_scenarios"]
        if isinstance(n, bool) or not isinstance(n, int) or not MIN_SCENARIOS <= n <= MAX_SCENARIOS:
            problems.append(f"uncertainty.n_scenarios must be an int in [{MIN_SCENARIOS}, {MAX_SCENARIOS}]")
        coverage = knobs["coverage"]
        if isinstance(coverage, bool) or not isinstance(coverage, (int, float)) or not 0.0 < coverage < 1.0:
            problems.append("uncertainty.coverage must be a number in (0, 1)")
        problems.extend(cls._int_problems(knobs, "window_blocks", 1))
        problems.extend(cls._int_problems(knobs, "null_draws", 100))
        problems.extend(cls._int_problems(knobs, "seed", 0))
        return [p if p.startswith("uncertainty") else f"uncertainty.{p}" for p in problems]

    def fingerprint(self):
        """Identity: the kind plus the two pinned artifacts (dict)."""
        return {
            "kind": "intraday_equities-forecast-publisher",
            "inventory_manifest_sha256": self.params["inventory_manifest_sha256"],
            "gates_sha256": self.params["gates_sha256"],
        }

    def run(self, ctx, inputs):
        """Verify the walk, then publish every segment's release and bundles.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            Its ``run_dir`` holds the driver's ``resolved.json``, whose
            ``document_hash`` is the producer identity every cap and
            bundle row names.
        inputs : dict
            Unused -- this is a source node.

        Returns
        -------
        dict
            ``releases`` and ``bundles``, both lists of JSON objects.
        """
        del inputs
        producer = self._document_sha256(ctx)
        walk = _Walk(self.params)
        try:
            if self.params["last_fold"] >= len(walk.folds):
                raise ValueError(f"last_fold {self.params['last_fold']} is past the walk's {len(walk.folds)} folds")
            releases, bundles = [], []
            for index in range(self.params["first_fold"], self.params["last_fold"] + 1):
                release, scenarios = self._release(walk, index, producer)
                releases.append(release)
                bundles.extend(self._bundles(walk, index, release, scenarios, producer))
        finally:
            walk.close()
        return {"releases": releases, "bundles": bundles}

    @staticmethod
    def _document_sha256(ctx):
        """Read the running document's hash from the driver's ``resolved.json``."""
        path = os.path.join(ctx.run_dir, "resolved.json")
        with open(path, encoding="utf-8") as handle:
            digest = json.load(handle).get("document_hash")
        if not is_sha256hex(digest):
            raise ValueError(f"{path} carries no sha256 document_hash")
        return digest

    # -- one release --------------------------------------------------------

    def _release(self, walk, index, producer):
        """One segment's release object, and its per-lead scenario sets."""
        fold = walk.folds[index]
        release_id = f"{walk.model_id}-fold{index:02d}-{fold['run_hash'][:16]}"
        cutoff_ms = fold["cutoff_ms"]
        groups = {}
        for symbol, lead in walk.lead_map.items():
            groups.setdefault(lead, []).append(symbol)
        estimate = self._false_signal(walk, index)
        uncertainty, scenarios, measured = {}, {}, {}
        for lead in sorted(groups):
            symbols = sorted(groups[lead])
            outcome, scenarios[lead], measured[lead] = self._outcome(walk, index, lead, symbols, release_id)
            uncertainty[str(lead)] = {
                "false_signal": self._false_signal_obj(estimate, lead, symbols, release_id, cutoff_ms),
                "outcome": outcome,
            }
        release = {
            "fold": index,
            "cutoff": fold["cutoff"],
            "segment_start_ms": cutoff_ms,
            "segment_end_ms": fold["val_end_ms"] + 1,
            "release_id": release_id,
            "model_manifest_sha256": fold["run_hash"],
            "cap": self._cap(walk, release_id, producer),
            "survivors": sorted(walk.lead_map),
            "lead_map": dict(sorted(walk.lead_map.items())),
            "lead_groups": sorted(groups),
            "uncertainty": uncertainty,
            "calibration": {
                "window_start_ms": self._window_start(cutoff_ms),
                "calibration_end_ms": cutoff_ms - 1,
                "known_at_ms": cutoff_ms,
                "folds": list(range(index)),
                "measured_coverage": {str(lead): measured[lead] for lead in sorted(measured)},
                "scope": "procedure-level: earlier refits' out-of-sample residuals attested for this release",
            },
        }
        return release, scenarios

    def _window_start(self, cutoff_ms):
        """First instant of the trailing calibration window before ``cutoff_ms``."""
        return cutoff_ms - int(self.params["calibration_window_days"]) * _DAY_MS

    def _cap(self, walk, release_id, producer):
        """Build the per-release cap artifact: the gate admission, TRUE stamps, never a confirmation."""
        end_day = datetime.fromtimestamp(walk.evidence_end_ms / 1000, tz=timezone.utc).date()
        scope = f"p16-gate-admission-developmental-post-selection-evidence-end-{end_day.isoformat()}"
        cap = {
            "schema_version": 2,
            "model_release_id": release_id,
            "deployment_eligible": False,
            "evidence_scope": scope,
            "evidence_end_ms": walk.evidence_end_ms,
            "generated_ms": walk.generated_ms,
            "producer": {"document_sha256": producer, "node": self.key, "output": "cap"},
            "evidence": {"sha256": walk.gates_sha256, "scope": scope, "end_ms": walk.evidence_end_ms},
            "caps": [
                {"symbol": symbol, "capped_horizon": lead}
                for symbol, lead in sorted(walk.lead_map.items())
            ],
        }
        problems = ConfirmedCaps.problems(cap)
        if problems:
            raise ValueError("cap artifact refused: " + "; ".join(problems))
        return cap

    def _residual_panel(self, walk, before, cutoff_ms, lead, symbols):
        """Complete-case ``y - yhat`` rows at ``lead`` from folds < ``before``, stamped in the window."""
        start = self._window_start(cutoff_ms)
        by_symbol = {symbol: {} for symbol in symbols}
        for index in range(before):
            for unit in walk.series(index):
                if unit["lead"] != lead or unit["symbol"] not in by_symbol:
                    continue
                for stamp, y, yhat in zip(unit["stamps"], unit["y"], unit["yhat"]):
                    if start <= stamp < cutoff_ms:
                        by_symbol[unit["symbol"]][stamp] = y - yhat
        return self._complete_case(by_symbol, symbols)

    @staticmethod
    def _complete_case(by_symbol, symbols):
        """``(stamps, rows)`` at the instants every symbol has a residual, in time order."""
        stamps = sorted(set.intersection(*(set(by_symbol[s]) for s in symbols)))
        return stamps, [tuple(by_symbol[s][stamp] for s in symbols) for stamp in stamps]

    def _calibrated(self, walk, before, cutoff_ms, lead, symbols):
        """``(panel, band)``: the block-conformal band from folds < ``before``."""
        knobs = self.params["uncertainty"]
        stamps, rows = self._residual_panel(walk, before, cutoff_ms, lead, symbols)
        if not rows:
            raise ValueError(f"no complete-case residuals at h{lead:02d} before {cutoff_ms}")
        panel = BlockResiduals(
            names=tuple(symbols), rows=rows, blocks=[utc_day(s) for s in stamps], stamps=stamps
        )
        band = BlockConformalInterval().calibrate(
            panel, coverage=knobs["coverage"], window_blocks=knobs["window_blocks"]
        )
        return panel, band

    def _outcome(self, walk, index, lead, symbols, release_id):
        """Calibrate the attested outcome band (JSON), its scenario set and measured coverage."""
        knobs = self.params["uncertainty"]
        cutoff_ms = walk.folds[index]["cutoff_ms"]
        panel, band = self._calibrated(walk, index, cutoff_ms, lead, symbols)
        scenarios = BlockConformalInterval().scenarios(panel, knobs["n_scenarios"], knobs["seed"])
        measured, n_units = self._measured_coverage(walk, index, lead, symbols)
        artifact = _dataclass_obj(band)
        attestation = UncertaintyAttestation(
            artifact_id=f"{release_id}:h{lead:02d}:outcome:{canonical_hash(artifact)[:16]}",
            model_identity=release_id,
            calibration_end_ms=cutoff_ms - 1,
            known_at_ms=cutoff_ms,
            producer=class_ref(BlockConformalInterval),
            coverage=CoverageEvidence(
                target=float(knobs["coverage"]),
                measured=measured,
                evidence_id=(
                    f"procedure-level:{walk.model_id}:h{lead:02d}:calibrated-before-fold"
                    f"{index - 1:02d}-scored-on-fold{index - 1:02d}"
                ),
                n_units=n_units,
            ),
        )
        return {"artifact": artifact, "attestation": _dataclass_obj(attestation)}, scenarios, measured

    def _measured_coverage(self, walk, index, lead, symbols):
        """Out-of-sample coverage of the procedure at release ``index - 1``, scored on its own fold."""
        prior = walk.folds[index - 1]
        _panel, band = self._calibrated(walk, index - 1, prior["cutoff_ms"], lead, symbols)
        by_symbol = {symbol: {} for symbol in symbols}
        for unit in walk.series(index - 1):
            if unit["lead"] == lead and unit["symbol"] in by_symbol:
                for stamp, y, yhat in zip(unit["stamps"], unit["y"], unit["yhat"]):
                    if prior["cutoff_ms"] <= stamp < walk.folds[index]["cutoff_ms"]:
                        by_symbol[unit["symbol"]][stamp] = y - yhat
        stamps, rows = self._complete_case(by_symbol, symbols)
        if not rows:
            raise ValueError(f"fold {index - 1} has no complete-case rows at h{lead:02d} to score")
        hits = sum(
            1
            for row in rows
            for symbol, value in zip(symbols, row)
            if band.lower_offset[symbol] <= value <= band.upper_offset[symbol]
        )
        return hits / (len(rows) * len(symbols)), len({utc_day(s) for s in stamps})

    def _false_signal(self, walk, index):
        """One ``GrenanderLocalFdr`` estimate over every modeled cell in the window before the cutoff."""
        knobs = self.params["uncertainty"]
        cutoff_ms = walk.folds[index]["cutoff_ms"]
        start = self._window_start(cutoff_ms)
        cells, owner = {}, {}
        for prior in range(index):
            for unit in walk.series(prior):
                q = float(unit["q"])
                if q <= 0.0:
                    continue
                kept = [
                    (stamp, gap / q)
                    for stamp, gap in zip(unit["stamps"], unit["d"])
                    if start <= stamp < cutoff_ms
                ]
                if not kept:
                    continue
                cell = f"{unit['symbol']}:h{unit['lead']:02d}"
                owner[cell] = unit["symbol"]
                merge_session_totals(
                    cells.setdefault(cell, {}),
                    session_totals([s for s, _ in kept], [g for _, g in kept]),
                )
        nulls = session_flip_nulls(cells, n_boot=knobs["null_draws"], seed=knobs["seed"])
        evidence = {cell: SignalEvidence(*pair) for cell, pair in nulls.items()}
        return GrenanderLocalFdr().estimate(
            evidence, independent_units=len({owner[cell] for cell in evidence})
        )

    def _false_signal_obj(self, estimate, lead, symbols, release_id, cutoff_ms):
        """One lead group's projection of ``estimate`` (values unchanged), attested, as JSON."""
        cells = {symbol: f"{symbol}:h{lead:02d}" for symbol in symbols}
        missing = sorted(cell for cell in cells.values() if cell not in estimate.pi_hat)
        if missing:
            raise ValueError(f"false-signal estimate has no statistic for admitted cell(s) {missing}")
        projected = FalseSignalEstimate(
            pi_hat={symbol: estimate.pi_hat[cell] for symbol, cell in cells.items()},
            pi_widened={symbol: estimate.pi_widened[cell] for symbol, cell in cells.items()},
            evidence=estimate.evidence,
        )
        artifact = _dataclass_obj(projected)
        attestation = UncertaintyAttestation(
            artifact_id=f"{release_id}:h{lead:02d}:false-signal:{canonical_hash(artifact)[:16]}",
            model_identity=release_id,
            calibration_end_ms=cutoff_ms - 1,
            known_at_ms=cutoff_ms,
            producer=class_ref(GrenanderLocalFdr),
            coverage=CoverageEvidence(
                target=float(estimate.evidence["widening_level"]),
                measured=_FALSE_SIGNAL_ATTAINMENT_FLOOR,
                evidence_id=_FALSE_SIGNAL_EVIDENCE_ID,
                n_units=int(estimate.evidence["independent_units"]),
            ),
        )
        return {"artifact": artifact, "attestation": _dataclass_obj(attestation)}

    @classmethod
    def envelopes(cls, release, lead):
        """Rebuild one lead group's attested envelopes from a release's JSON.

        Parameters
        ----------
        release : dict
            One ``releases`` entry (as emitted, or after a JSON round trip).
        lead : int
            The lead group.

        Returns
        -------
        dict
            ``{"false_signal": AttestedFalseSignalRate, "outcome":
            AttestedOutcomeBand}`` -- the ``uncertainty`` port
            ``EquityKellyMIO`` admits.
        """
        group = release["uncertainty"][str(lead)]
        signal, outcome = group["false_signal"], group["outcome"]
        return {
            "false_signal": AttestedFalseSignalRate(
                FalseSignalEstimate(**signal["artifact"]), _attestation_from(signal["attestation"])
            ),
            "outcome": AttestedOutcomeBand(
                OutcomeIntervalResult(**outcome["artifact"]), _attestation_from(outcome["attestation"])
            ),
        }

    # -- the tick path ------------------------------------------------------

    def _tick_rows(self, walk, index, release, scenarios):
        """``{(ts, lead): [bundle input row, ...]}`` for one segment -- ``yhat`` only, never ``y``."""
        fold = walk.folds[index]
        market, contract = walk.market(index)
        predictions = walk.yhat(index)
        cutoff_ms = fold["cutoff_ms"]
        out = {}
        for symbol, lead in sorted(walk.lead_map.items()):
            stamps, prices, beta, sigma = market[symbol]
            signal = release["uncertainty"][str(lead)]["false_signal"]["artifact"]
            weights, draws = scenarios[lead].weighted_draws()
            for stamp, yhat in predictions.get((symbol, lead), ()):
                if not cutoff_ms <= stamp < release["segment_end_ms"]:
                    continue
                loc = int(stamps.searchsorted(stamp))
                if loc >= stamps.size or int(stamps[loc]) != stamp:
                    raise ValueError(f"{symbol} has a prediction at {stamp} but no tape bar")
                out.setdefault((stamp, lead), []).append(
                    {
                        "entity": symbol,
                        "decision_ts": stamp,
                        "lead": lead,
                        "price": float(prices[loc]),
                        "yhat": yhat,
                        "sigma_t": float(sigma[loc]),
                        "beta_t": 0.0 if beta is None else float(beta[loc]),
                        "pi_hat": signal["pi_hat"][symbol],
                        "pi_widened": signal["pi_widened"][symbol],
                        "weights": weights,
                        "scenarios": draws[symbol],
                        "label": contract,
                        "known_at": {
                            **{name: stamp for name in _MARKET_FIELDS},
                            **{name: cutoff_ms for name in _CALIBRATED_FIELDS},
                        },
                    }
                )
        return out

    def _bundles(self, walk, index, release, scenarios, producer):
        """One ``ForecastBundle`` per ``(tick, lead group)`` of a segment, as JSON rows."""
        out = []
        for (stamp, lead), rows in sorted(self._tick_rows(walk, index, release, scenarios).items()):
            group = release["uncertainty"][str(lead)]
            bundle = ForecastBundle(
                release["release_id"],
                sorted(rows, key=lambda row: row["entity"]),
                producer={"document_sha256": producer, "node": self.key, "output": "bundle"},
                model_manifest_sha256=release["model_manifest_sha256"],
                uncertainty={
                    slot: group[slot]["attestation"]["artifact_id"] for slot in ("false_signal", "outcome")
                },
            )
            out.append(
                {
                    "fold": index,
                    "release_id": release["release_id"],
                    "decision_ts": stamp,
                    "lead": lead,
                    "rows": bundle.rows,
                }
            )
        return out


#: ``EquityKellyMIO`` params the per-tick decider binds itself, so the
#: ``decide`` node's ``mio`` block must not carry them: the bundle/cap
#: identity pins (read from each tick's in-process publisher outputs) and
#: the Schwab cost knobs (read from the replay's ``fill-policy.json``, the
#: one place costs live).
_PIN_PARAMS = (
    "bundle_artifact_sha256",
    "bundle_producer_document_sha256",
    "bundle_producer_node",
    "bundle_model_manifest_sha256",
    "cap_artifact_sha256",
    "cap_producer_document_sha256",
    "cap_producer_node",
    "cap_evidence_sha256",
)
_BOUND_PARAMS = _PIN_PARAMS + SchwabCostModel._PARAMS
#: Syntactically valid stand-ins used ONLY to validate a ``mio`` block at
#: plan time; the decider replaces every one of them per tick.
_BOUND_PLACEHOLDERS = {
    **{name: "0" * 64 for name in _PIN_PARAMS},
    "bundle_producer_node": "bound-per-tick",
    "cap_producer_node": "bound-per-tick",
    "spread_bps": 0.0,
    "taf_per_share": 0.0,
    "sec31_bps": 0.0,
    "min_price": 1.0,
}


class MioDecider:
    """Per-tick ``EquityKellyMIO`` strategy an ``EquityReplay`` calls (ADR-0182 S6).

    Holds one release's JSON (``ForecastPublisher``'s ``releases`` entry)
    and that release's tick bundles. At a decision instant it drops every
    unit that still holds an open lot (recorded ``open_lot_at_decision``;
    never an early exit), then solves ONE ``EquityKellyMIO`` per lead
    group in ascending lead order, all from one cash budget: each group
    sees the cash the previous groups' planned buys left
    (``cash_after``) -- a disclosed allocation bias. The node is built
    fresh per solve because its params pin the exact bundle and cap
    digests; those pins are the in-process publisher's own identities (the
    release's cap and model manifest, the cap producer's document and
    node), and the cost knobs are the fill policy's. A refused or
    non-optimal solve is recorded ``mio_refused`` and trades nothing for
    that group.

    Parameters
    ----------
    release : dict
        One ``releases`` entry.
    bundles : list of dict
        ``bundles`` entries; only this release's are used.
    mio : dict
        The ``decide`` node's ``mio`` output: ``params`` (the
        ``EquityKellyMIO`` base params) and ``lead_groups`` (ascending).
    fill_policy : intraday_equities.replay.FillPolicy
        Source of the Schwab cost knobs.
    ctx : dskit.pipeline.node.NodeContext
        Passed to each solve.

    Examples
    --------
    Drive one release through the replay::

        decider = MioDecider(release, bundles, mio, policy, ctx)
        EquityReplay(policy, cash_policy, decider=decider).run(bars)
        decider.refused  # [{"asof_ms": ..., "lead": 2, "reason": "mio_refused", ...}]
    """

    def __init__(self, release, bundles, mio, fill_policy, ctx):
        if list(release["lead_groups"]) != list(mio["lead_groups"]):
            raise ValueError(
                f"release {release['release_id']} lead groups {release['lead_groups']} "
                f"differ from the decider's {mio['lead_groups']}"
            )
        self._release = release
        self._params = dict(mio["params"])
        self._costs = {name: getattr(fill_policy, name) for name in SchwabCostModel._PARAMS}
        self._ctx = ctx
        self._leads = [int(lead) for lead in mio["lead_groups"]]
        self._envelopes = {lead: ForecastPublisher.envelopes(release, lead) for lead in self._leads}
        self._bundles = {
            (int(bundle["decision_ts"]), int(bundle["lead"])): bundle["rows"]
            for bundle in bundles
            if bundle["release_id"] == release["release_id"]
        }
        self.skipped = []
        self.refused = []

    def decide(self, asof_ms, portfolio):
        """Orders for this tick: ``{"symbol", "asof_ms", "lead", "qty", "side": "buy"}``."""
        held = {symbol for symbol, shares in portfolio["positions"].items() if shares}
        cash = portfolio["cash"]
        orders = []
        for lead in self._leads:
            rows = self._bundles.get((asof_ms, lead))
            if not rows:
                continue
            kept = []
            for row in rows:
                if row["entity"] in held:
                    self.skipped.append({
                        "symbol": row["entity"], "asof_ms": asof_ms, "lead": lead,
                        "reason": "open_lot_at_decision",
                    })
                else:
                    kept.append(row)
            if not kept:
                continue
            node = EquityKellyMIO(f"mio_h{lead:02d}", {**self._params, **self._costs, **self._pins(kept)})
            inputs = {
                "bundle": kept,
                "portfolio": {
                    "asof_ms": asof_ms,
                    "cash": cash,
                    "buying_power": cash,
                    # Held units were dropped above, so the MIO sees an empty
                    # book: its inventory path is not exercised (ADR-0182).
                    "positions": {},
                    "mark_prices": portfolio["mark_prices"],
                    "gross_limit": portfolio["gross_limit"],
                    "cash_reserve": 0.0,
                    "sale_credit": 1.0,
                },
                "survivors": list(self._release["survivors"]),
                "cap": self._release["cap"],
                "uncertainty": self._envelopes[lead],
            }
            try:
                out = node.run(self._ctx, inputs)
            except (ValueError, RuntimeError) as exc:
                self.refused.append({
                    "asof_ms": asof_ms, "lead": lead, "reason": "mio_refused", "detail": str(exc),
                })
                continue
            for symbol, trade in sorted(out["trades"].items()):
                if trade["sell"]:
                    raise ValueError(f"{node.key}: MIO sold {symbol} from an empty book at {asof_ms}")
                orders.append({"symbol": symbol, "asof_ms": asof_ms, "lead": lead, "qty": trade["buy"], "side": "buy"})
            cash = out["cash_after"]
        return orders

    def _pins(self, rows):
        """Return the in-process producer's identities this solve is pinned to."""
        cap = self._release["cap"]
        return {
            "bundle_artifact_sha256": ForecastBundle.digest(rows),
            "bundle_producer_document_sha256": cap["producer"]["document_sha256"],
            "bundle_producer_node": cap["producer"]["node"],
            "bundle_model_manifest_sha256": self._release["model_manifest_sha256"],
            "cap_artifact_sha256": ConfirmedCaps.digest(cap),
            "cap_producer_document_sha256": cap["producer"]["document_sha256"],
            "cap_producer_node": cap["producer"]["node"],
            "cap_evidence_sha256": cap["evidence"]["sha256"],
        }


class MioDeciderNode(Node):
    """Carry the validated per-tick MIO params to the simulation (ADR-0182 S6, Revision 3).

    The ``intraday_equities-mio-decider`` kind. It sizes nothing: a DAG
    node runs once, while the MIO must run per tick on live cash, so this
    node only validates the ``EquityKellyMIO`` base params and emits them,
    with the releases' ascending lead-group order, as JSON for the
    ``simulate`` node's :class:`MioDecider`. Role ``transform``, not
    ``capital``: the planner's capital rule requires a ``stat_test`` wire,
    and this document's survivor set is the pinned gate admission carried
    on each release (ADR-0182 Revision 1), never a new test.

    Parameters
    ----------
    params : dict
        ``mio``: the ``EquityKellyMIO`` params WITHOUT the bundle/cap pins
        and Schwab cost knobs (bound per tick), and with
        ``cap_evidence_look_ahead`` declared true -- the P16 caps are
        post-selection, so the switch is stated in the document, never
        implied.

    Examples
    --------
    ::

        node = MioDeciderNode("decide", {"mio": {..., "cap_evidence_look_ahead": True}})
        node.run(ctx, {"releases": releases})["mio"]
        # -> {"params": {...}, "lead_groups": [1, 2, 3, 4, 5, 6, 10]}
    """

    role = "transform"
    outputs = ("mio",)
    _PARAMS = ("mio",)

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + ("notes",))
        mio = params.get("mio")
        if not isinstance(mio, dict):
            return problems + [f"mio must be a mapping of EquityKellyMIO params, got {mio!r}"]
        bound = sorted(set(mio) & set(_BOUND_PARAMS))
        if bound:
            problems.append(
                f"mio must not carry {bound}: pins are bound per tick from the publisher "
                "and costs from fill-policy.json"
            )
        if mio.get("cap_evidence_look_ahead") is not True:
            problems.append(
                "mio.cap_evidence_look_ahead must be declared true: the gate caps use "
                "post-selection evidence (ADR-0182 Revision 2)"
            )
        problems.extend(
            f"mio: {problem}"
            for problem in EquityKellyMIO.validate_params({**mio, **_BOUND_PLACEHOLDERS})
        )
        return problems

    def validate_inputs(self, inputs):
        """Require a non-empty ``releases`` list that agrees on its lead groups."""
        releases = inputs.get("releases")
        if not isinstance(releases, list) or not releases:
            return ["releases must be a non-empty list"]
        groups = {json.dumps(release.get("lead_groups")) for release in releases}
        if len(groups) != 1:
            return [f"releases disagree on their lead groups: {sorted(groups)}"]
        return []

    def run(self, ctx, inputs):
        """Emit ``mio``: the base params and the ascending lead-group order (JSON)."""
        problems = self.validate_inputs(inputs)
        if problems:
            raise ValueError(f"{self.key}: " + "; ".join(problems))
        leads = sorted(int(lead) for lead in inputs["releases"][0]["lead_groups"])
        return {"mio": {"params": dict(self.params["mio"]), "lead_groups": leads}}


NODE_KINDS = {
    "intraday_equities-forecast-publisher": ForecastPublisher,
    "intraday_equities-mio-decider": MioDeciderNode,
}

for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)
