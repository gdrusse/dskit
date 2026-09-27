"""Development simulation nodes (ADR-0184): publisher, per-tick MIO decider, simulation, report.

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
:class:`DevelopmentSimulation` runs one ``EquityReplay`` segment per
release over its ``bars`` input, carrying cash between releases, and
:class:`SimulationReport` folds the fills through ``WindowBook`` into a
daily NAV/P&L report.

ADR-0186 adds the per-MINUTE cadence the ADR-0185 retrain document runs:
:class:`MinuteForecastPublisher` publishes each release's per-minute tick
columns from the walk's pinned trade predictions (every validation minute,
one fitted model), :class:`MinuteMioDecider` assembles a minute's bundle
from those columns through the same builders and skips held, queued and
after-close units, and :class:`MinuteDevelopmentSimulation` carries a lot
open at a release boundary into the next release. Scoring, calibration,
false signal and caps stay on the 30-minute lattice rows.

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
from bisect import bisect_right
from collections import Counter
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from zoneinfo import ZoneInfo

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
from dskit.pipeline.node import ConfigError, Node, class_ref, register_node_kind, reject_unknown_params
from dskit.pipeline.outcome_interval import (
    MAX_SCENARIOS,
    MIN_SCENARIOS,
    BlockConformalInterval,
    BlockResiduals,
    OutcomeIntervalResult,
)
from dskit.pipeline.program_calendar import load_program_calendar
from dskit.pipeline.stages import Stage, is_sha256hex
from dskit.pipeline.uncertainty_intake import (
    AttestedFalseSignalRate,
    AttestedOutcomeBand,
    CoverageEvidence,
    UncertaintyAttestation,
)
from dskit.production.accounting import WindowBook
from dskit.production.base import canonical_hash
from dskit.production.records import Fill

from .final_gates import (
    DEVELOPMENT_EVIDENCE_SCOPE,
    _outputs,
    _read_pinned_json,
    _verified_prediction_snapshot,
    cell_id,
)
from .final_model import _epoch_ms
from .forecast_bundle import ConfirmedCaps, ForecastBundle
from .model_zoo import TRADE_CACHE_PREFIX
from .nodes_capital import CAP_LOOK_AHEAD_DISCLOSURE, EquityKellyMIO, SchwabCostModel
from .nodes import (
    LABEL_PARAMS,
    LABEL_RETURN_BASIS,
    Universe,
    _label_from_params,
    _resolve_path,
    _tapes_from_bars,
)
from .replay import DevelopmentReplay, EquityReplay

__all__ = [
    "BOUND_BY_STAGE",
    "DISCLOSURE",
    "NODE_KINDS",
    "DevelopmentSimulation",
    "ForecastPublisher",
    "MinuteDevelopmentSimulation",
    "MinuteForecastPublisher",
    "MinuteMioDecider",
    "MioDecider",
    "MioDeciderNode",
    "RetrainedSimulation",
    "SimulationReport",
]

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
        """Tape frames for ``needed`` symbols from the fold's verified SCORED feature caches.

        A minute walk's trade caches (``TRADE_CACHE_PREFIX`` nodes, ADR-0186)
        are the same kind but carry no label of their own: their tapes are
        the scored caches', so they are neither verified nor read here.
        """
        from .feature_cache import SessionFeatureCache, verify_feature_cache

        frames, digests = {}, {}
        scored = (
            k for k, spec in nodes.items()
            if spec.get("uses") == _CACHE_KIND and not k.startswith(TRADE_CACHE_PREFIX)
        )
        for key in sorted(scored):
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
    ADR-0184 S5). Reads the sha256-pinned P16 gate inventory and gate
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

    The row builders are static and shared with the per-tick deciders:
    :meth:`tick_row`/:meth:`bundle_rows` for a lead group, and -- for a
    release :class:`MinuteForecastPublisher` published with ``leads:
    "path"`` (ADR-0188 formulation B) -- :meth:`path_plan`,
    :meth:`path_row`, :meth:`path_rows`, :meth:`path_bundle_rows` and
    :meth:`path_envelopes` for the exit-horizon path rows.

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
    #: The walk reader and the per-tick output port; the minute subclass swaps both.
    _WALK = _Walk
    _TICK_PORT = "bundles"
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

    _KIND = "intraday_equities-forecast-publisher"

    def fingerprint(self):
        """Identity: the kind plus the two pinned artifacts (dict)."""
        return {
            "kind": self._KIND,
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
        walk = self._WALK(self.params)
        try:
            if self.params["last_fold"] >= len(walk.folds):
                raise ValueError(f"last_fold {self.params['last_fold']} is past the walk's {len(walk.folds)} folds")
            releases, ticks = [], []
            for index in range(self.params["first_fold"], self.params["last_fold"] + 1):
                release, scenarios = self._release(walk, index, producer)
                releases.append(release)
                ticks.extend(self._publish_ticks(walk, index, release, scenarios))
        finally:
            walk.close()
        return {"releases": releases, self._TICK_PORT: ticks}

    def _publish_ticks(self, walk, index, release, scenarios):
        """One segment's per-tick output: here, one ``ForecastBundle`` per lattice tick and lead group."""
        return self._bundles(walk, index, release, scenarios)

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

    def _release(self, walk, index, producer, estimate=None):
        """One segment's release object, and its per-lead scenario sets.

        ``estimate`` is the segment's false-signal estimate when the caller
        already holds it (the minute publisher's path mode projects it a
        second time); ``None`` computes it here.
        """
        fold = walk.folds[index]
        release_id = f"{walk.model_id}-fold{index:02d}-{fold['run_hash'][:16]}"
        cutoff_ms = fold["cutoff_ms"]
        groups = {}
        for symbol, lead in walk.lead_map.items():
            groups.setdefault(lead, []).append(symbol)
        if estimate is None:
            estimate = self._false_signal(walk, index)
        uncertainty, scenarios, measured = {}, {}, {}
        for lead in sorted(groups):
            symbols = sorted(groups[lead])
            tag = f"h{lead:02d}"
            outcome, scenarios[lead], measured[lead], _per_symbol = self._outcome(
                walk, index, {symbol: (symbol, lead) for symbol in symbols}, tag, release_id
            )
            cells = {symbol: self._cell_id(symbol, lead) for symbol in symbols}
            uncertainty[str(lead)] = {
                "false_signal": self._false_signal_obj(estimate, cells, tag, release_id, cutoff_ms),
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

    @staticmethod
    def _cell_id(symbol, lead):
        """Return the ``SYM:hNN`` name of one ``(symbol, lead)`` cell -- ``final_gates.cell_id``, the one owner."""
        return cell_id(symbol, lead)

    def _residual_panel(self, walk, folds, start, end, components):
        """Complete-case ``y - yhat`` rows of ``components`` from ``folds``, stamped in ``[start, end)``.

        ``components`` maps each panel column's name to the ``(symbol,
        lead)`` unit it reads, in column order: a lead group names its
        columns by symbol at its one lead, the path mode names them by cell
        across every lead (ADR-0188), so a row is one instant across every
        column either way.
        """
        wanted = {unit: name for name, unit in components.items()}
        by_name = {name: {} for name in components}
        for index in folds:
            for unit in walk.series(index):
                name = wanted.get((unit["symbol"], unit["lead"]))
                if name is None:
                    continue
                for stamp, y, yhat in zip(unit["stamps"], unit["y"], unit["yhat"]):
                    if start <= stamp < end:
                        by_name[name][stamp] = y - yhat
        return self._complete_case(by_name, list(components))

    @staticmethod
    def _complete_case(by_symbol, symbols):
        """``(stamps, rows)`` at the instants every symbol has a residual, in time order."""
        stamps = sorted(set.intersection(*(set(by_symbol[s]) for s in symbols)))
        return stamps, [tuple(by_symbol[s][stamp] for s in symbols) for stamp in stamps]

    def _calibrated(self, walk, before, cutoff_ms, components, tag):
        """``(panel, band)``: the block-conformal band over ``components`` from folds < ``before``."""
        knobs = self.params["uncertainty"]
        stamps, rows = self._residual_panel(
            walk, range(before), self._window_start(cutoff_ms), cutoff_ms, components
        )
        if not rows:
            raise ValueError(f"no complete-case residuals at {tag} before {cutoff_ms}")
        panel = BlockResiduals(
            names=tuple(components), rows=rows, blocks=[utc_day(s) for s in stamps], stamps=stamps
        )
        band = BlockConformalInterval().calibrate(
            panel, coverage=knobs["coverage"], window_blocks=knobs["window_blocks"]
        )
        return panel, band

    def _outcome(self, walk, index, components, tag, release_id):
        """Calibrate the attested outcome band (JSON), its scenario set and measured coverage.

        Parameters
        ----------
        walk : _Walk
        index : int
            The release's fold.
        components : dict
            ``{column name: (symbol, lead)}``, in column order (see
            :meth:`_residual_panel`).
        tag : str
            The calibration's name in its artifact and evidence ids:
            ``hNN`` for a lead group, ``path`` for the path mode's cells.
        release_id : str

        Returns
        -------
        tuple
            ``(outcome, scenarios, measured, per_component)``: the band and
            its attestation as JSON, ONE joint scenario draw over every
            column (seeded from the panel digest), the artifact-level
            out-of-sample coverage the attestation carries, and the same
            reading per column.
        """
        knobs = self.params["uncertainty"]
        cutoff_ms = walk.folds[index]["cutoff_ms"]
        panel, band = self._calibrated(walk, index, cutoff_ms, components, tag)
        scenarios = BlockConformalInterval().scenarios(panel, knobs["n_scenarios"], knobs["seed"])
        measured, per_component, n_units = self._measured_coverage(walk, index, components, tag)
        artifact = _dataclass_obj(band)
        attestation = UncertaintyAttestation(
            artifact_id=f"{release_id}:{tag}:outcome:{canonical_hash(artifact)[:16]}",
            model_identity=release_id,
            calibration_end_ms=cutoff_ms - 1,
            known_at_ms=cutoff_ms,
            producer=class_ref(BlockConformalInterval),
            coverage=CoverageEvidence(
                target=float(knobs["coverage"]),
                measured=measured,
                evidence_id=(
                    f"procedure-level:{walk.model_id}:{tag}:calibrated-before-fold"
                    f"{index - 1:02d}-scored-on-fold{index - 1:02d}"
                ),
                n_units=n_units,
            ),
        )
        outcome = {"artifact": artifact, "attestation": _dataclass_obj(attestation)}
        return outcome, scenarios, measured, per_component

    def _measured_coverage(self, walk, index, components, tag):
        """Out-of-sample coverage of the procedure at release ``index - 1``, scored on its own fold.

        Returns
        -------
        tuple
            ``(measured, per_component, n_units)``: the hit rate over every
            scored value, each column's own hit rate on the SAME
            complete-case rows (so ``measured`` is their mean), and the
            number of UTC days scored.
        """
        prior = walk.folds[index - 1]
        _panel, band = self._calibrated(walk, index - 1, prior["cutoff_ms"], components, tag)
        stamps, rows = self._residual_panel(
            walk, (index - 1,), prior["cutoff_ms"], walk.folds[index]["cutoff_ms"], components
        )
        if not rows:
            raise ValueError(f"fold {index - 1} has no complete-case rows at {tag} to score")
        names = list(components)
        hits = dict.fromkeys(names, 0)
        for row in rows:
            for name, value in zip(names, row):
                if band.lower_offset[name] <= value <= band.upper_offset[name]:
                    hits[name] += 1
        measured = sum(hits.values()) / (len(rows) * len(names))
        per_component = {name: hits[name] / len(rows) for name in names}
        return measured, per_component, len({utc_day(s) for s in stamps})

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
                cell = self._cell_id(unit["symbol"], unit["lead"])
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

    def _false_signal_obj(self, estimate, cells, tag, release_id, cutoff_ms):
        """Project ``estimate`` onto ``cells`` (values unchanged), attested, as JSON.

        ``cells`` maps each projected key to its ``SYM:hNN`` cell: a lead
        group keys its projection by symbol, the path mode by the cell
        itself (ADR-0188). ``tag`` names the projection in the artifact id.
        """
        missing = sorted(cell for cell in cells.values() if cell not in estimate.pi_hat)
        if missing:
            raise ValueError(f"false-signal estimate has no statistic for admitted cell(s) {missing}")
        projected = FalseSignalEstimate(
            pi_hat={key: estimate.pi_hat[cell] for key, cell in cells.items()},
            pi_widened={key: estimate.pi_widened[cell] for key, cell in cells.items()},
            evidence=estimate.evidence,
        )
        artifact = _dataclass_obj(projected)
        attestation = UncertaintyAttestation(
            artifact_id=f"{release_id}:{tag}:false-signal:{canonical_hash(artifact)[:16]}",
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
        return cls._envelopes_of(release["uncertainty"][str(lead)])

    @classmethod
    def path_envelopes(cls, release):
        """Rebuild a path release's ONE pair of attested envelopes (ADR-0188 formulation B).

        Parameters
        ----------
        release : dict
            A ``releases`` entry published with ``leads: "path"`` (as
            emitted, or after a JSON round trip).

        Returns
        -------
        dict
            ``{"false_signal": AttestedFalseSignalRate, "outcome":
            AttestedOutcomeBand}``, both keyed per cell ``SYM:hNN``: the
            rates cover every ADMITTED cell (lead ``<= H_i``), the band
            every calibrated cell (lead ``<= K_i``). The capital step binds
            a path row to them per cell, not per entity.

        Raises
        ------
        ValueError
            The release carries no path block.
        """
        return cls._envelopes_of(cls._path_block(release)["uncertainty"])

    @staticmethod
    def _envelopes_of(group):
        """Rebuild the ``false_signal``/``outcome`` envelopes of one ``uncertainty`` group's JSON."""
        signal, outcome = group["false_signal"], group["outcome"]
        return {
            "false_signal": AttestedFalseSignalRate(
                FalseSignalEstimate(**signal["artifact"]), _attestation_from(signal["attestation"])
            ),
            "outcome": AttestedOutcomeBand(
                OutcomeIntervalResult(**outcome["artifact"]), _attestation_from(outcome["attestation"])
            ),
        }

    @staticmethod
    def _path_block(release):
        """``release["path"]``, or a refusal naming the undeclared path mode."""
        path = release.get("path") if isinstance(release, dict) else None
        if not isinstance(path, dict):
            release_id = release.get("release_id") if isinstance(release, dict) else None
            raise ValueError(
                f"release {release_id!r} carries no path block: its publisher did not declare "
                "leads 'path' (ADR-0188)"
            )
        return path

    # -- the tick path ------------------------------------------------------

    @staticmethod
    def _market_at(market, symbol, stamp):
        """``(price, sigma, beta)`` of ``symbol``'s fold tape bar at ``stamp``; refuses a missing bar."""
        stamps, prices, beta, sigma = market[symbol]
        loc = int(stamps.searchsorted(stamp))
        if loc >= stamps.size or int(stamps[loc]) != stamp:
            raise ValueError(f"{symbol} has a prediction at {stamp} but no tape bar")
        return float(prices[loc]), float(sigma[loc]), 0.0 if beta is None else float(beta[loc])

    @staticmethod
    def tick_row(release, symbol, stamp, lead, price, yhat, sigma, beta, weights, draws, contract):
        """One ``ForecastBundle`` input row -- the ONE builder both cadences use (ADR-0186).

        Market fields are known at ``stamp``; the calibrated ones at the
        release cutoff. ``yhat`` is the forecast alone, never ``y``.

        Parameters
        ----------
        release : dict
            The ``releases`` entry (its cutoff and false-signal artifact).
        symbol : str
        stamp : int
            The decision instant, epoch ms.
        lead : int
        price, yhat, sigma, beta : float
            The decision bar's close, the forecast, and the label's sigma/beta there.
        weights : list of float
            The lead group's scenario weights.
        draws : list of float
            This symbol's scenario residuals.
        contract : dict
            The fold label's effective knobs.

        Returns
        -------
        dict
        """
        signal = release["uncertainty"][str(lead)]["false_signal"]["artifact"]
        cutoff_ms = release["segment_start_ms"]
        return {
            "entity": symbol,
            "decision_ts": stamp,
            "lead": lead,
            "price": price,
            "yhat": yhat,
            "sigma_t": sigma,
            "beta_t": beta,
            "pi_hat": signal["pi_hat"][symbol],
            "pi_widened": signal["pi_widened"][symbol],
            "weights": weights,
            "scenarios": draws,
            "label": contract,
            "known_at": ForecastPublisher._known_at(stamp, cutoff_ms),
        }

    @staticmethod
    def _known_at(stamp, cutoff_ms):
        """Return a row's ``known_at``: market fields at the tick, calibrated ones at the release cutoff."""
        return {
            **{name: stamp for name in _MARKET_FIELDS},
            **{name: cutoff_ms for name in _CALIBRATED_FIELDS},
        }

    @staticmethod
    def bundle_rows(release, lead, rows):
        """Assemble one lead group's ``ForecastBundle`` rows for a release (both cadences, ADR-0186).

        The producer is the release cap's own (the publisher document and
        node), with output ``bundle``.
        """
        return ForecastPublisher._assembled(release, release["uncertainty"][str(lead)], rows).rows

    # -- the exit-horizon path (ADR-0188 formulation B) ---------------------

    @classmethod
    def path_plan(cls, release, symbol, coverage_floor):
        """One name's exit-horizon plan in a path release, screened per cell (ADR-0188).

        ``kstar`` is the largest lead ``k <= K_i`` such that EVERY cell
        ``(symbol, 1..k)`` measured an out-of-sample coverage at or above
        ``coverage_floor`` (owner question I(a)): a breach at lead ``j``
        truncates the plan to ``j - 1`` whatever the later cells measured,
        and a lead-1 breach leaves the name no path row at all (sell-only).
        This per-cell reading sits BESIDE the artifact-level screen, which is
        unchanged: the capital step's intake still admits the joint band on
        its one attested coverage. Session-close truncation is not applied
        here; a caller shortens the plan through :meth:`path_row`.

        Parameters
        ----------
        release : dict
            A ``releases`` entry published with ``leads: "path"``.
        symbol : str
            A gate-admitted name (a positive ``capped_horizon``).
        coverage_floor : float
            The per-cell floor, a finite number in (0, 1): the capital
            step's ``uncertainty_min_coverage``, the same floor its intake
            holds the artifact-level reading to.

        Returns
        -------
        dict
            ``calibrated`` (``K_i``, the name's scored heads), ``admitted``
            (``H_i``, the gate's ``capped_horizon``), ``kstar`` and ``plan``
            (``min(K_i, kstar)``, the tranches a row offers; 0 = no row).

        Raises
        ------
        ValueError
            The release carries no path block, ``symbol`` has no path (no
            cap or a zero cap), a cell of it has no measured coverage, or
            ``coverage_floor`` is not a finite number in (0, 1).

        Examples
        --------
        ::

            ForecastPublisher.path_plan(release, "LLY", 0.53)
            # -> {"calibrated": 3, "admitted": 2, "kstar": 3, "plan": 3}
        """
        path = cls._path_block(release)
        if (
            isinstance(coverage_floor, bool)
            or not isinstance(coverage_floor, (int, float))
            or not math.isfinite(coverage_floor)
            or not 0.0 < coverage_floor < 1.0
        ):
            raise ValueError(
                "coverage_floor must be a finite number in (0, 1) -- the capital step's "
                f"uncertainty_min_coverage -- got {coverage_floor!r}"
            )
        horizons = path["horizons"]
        if symbol not in horizons:
            raise ValueError(
                f"{symbol!r} has no path in release {release['release_id']}: only a gate-admitted "
                f"name (a positive capped_horizon) is published, the admitted are {sorted(horizons)}"
            )
        calibrated = horizons[symbol]
        kstar = 0
        for lead in range(1, calibrated + 1):
            cell = cls._cell_id(symbol, lead)
            measured = path["cell_coverage"].get(cell)
            if measured is None:
                raise ValueError(f"release {release['release_id']} records no measured coverage for {cell}")
            if measured < coverage_floor:
                break
            kstar = lead
        return {
            "calibrated": calibrated,
            "admitted": release["lead_map"][symbol],
            "kstar": kstar,
            "plan": min(calibrated, kstar),
        }

    @classmethod
    def path_plans(cls, release, coverage_floor):
        """:meth:`path_plan` for every name with a path in ``release``: ``{symbol: plan}``."""
        return {
            symbol: cls.path_plan(release, symbol, coverage_floor)
            for symbol in sorted(cls._path_block(release)["horizons"])
        }

    @classmethod
    def path_row(cls, release, symbol, stamp, price, yhat_path, sigma, beta, coverage_floor, plan_horizon=None):
        """One exit-horizon PATH input row -- the path counterpart of :meth:`tick_row` (ADR-0188).

        The row carries the name's whole forecast curve beside the flat
        contract: ``yhat_path`` (every scored head, lead order),
        ``admitted_horizon`` (the gate's cap ``H_i``), ``plan_horizon``
        (the screened ``min(K_i, kstar_i)`` of :meth:`path_plan`, or a
        caller's shorter value), ``pi_hat_path``/``pi_widened_path`` (the
        per-cell false-signal readings at the ADMITTED leads only) and
        ``scenarios_path`` (cell ``(symbol, k)``'s residuals from the
        release's ONE joint draw, so scenario ``o`` is the same world in
        every row and step). Its flat fields ARE step 1: ``lead`` 1 and
        ``yhat``/``pi_hat``/``pi_widened``/``scenarios`` the path's first
        entries. Market fields are known at ``stamp``, calibrated ones at
        the release cutoff, exactly as :meth:`tick_row`. The values stay in
        label units; :class:`ForecastBundle` converts and recenters them.

        Parameters
        ----------
        release : dict
            A ``releases`` entry published with ``leads: "path"``.
        symbol : str
        stamp : int
            The decision instant, epoch ms.
        price, sigma, beta : float
            The decision bar's close and the label's sigma/beta there.
        yhat_path : list of float
            Every scored head's prediction at ``stamp``, leads ``1..K_i``
            (:meth:`MinuteForecastPublisher.path_tick`).
        coverage_floor : float
            As :meth:`path_plan`.
        plan_horizon : int, optional
            A shorter plan than the screened one (the decider's session
            close); never longer. Default: the screened plan.

        Returns
        -------
        dict
            One :class:`ForecastBundle` path input row.

        Raises
        ------
        ValueError
            Anything :meth:`path_plan` refuses, a name whose screened plan
            is empty (``kstar`` 0), a ``plan_horizon`` outside
            ``1..min(K_i, kstar_i)``, or a ``yhat_path`` that is not one
            prediction per scored head.
        """
        plan = cls.path_plan(release, symbol, coverage_floor)
        screened, calibrated, admitted = plan["plan"], plan["calibrated"], plan["admitted"]
        if screened == 0:
            raise ValueError(
                f"{symbol} gets no path row: its lead-1 cell measured coverage below the per-cell "
                f"floor {coverage_floor!r} (kstar 0, owner question I(a)) -- the name is sell-only"
            )
        if plan_horizon is None:
            plan_horizon = screened
        elif isinstance(plan_horizon, bool) or not isinstance(plan_horizon, int) or not 1 <= plan_horizon <= screened:
            raise ValueError(
                f"{symbol}: plan_horizon must be an int in 1..{screened} (min(K_i, kstar_i)) -- a "
                f"caller may shorten the screened plan, never extend it -- got {plan_horizon!r}"
            )
        if not isinstance(yhat_path, (list, tuple)) or len(yhat_path) != calibrated:
            raise ValueError(
                f"{symbol}: yhat_path must carry one prediction per scored head ({calibrated}), "
                f"got {yhat_path!r}"
            )
        path = release["path"]
        signal = path["uncertainty"]["false_signal"]["artifact"]
        draws = path["tick_constants"]["scenarios"]
        cells = [cls._cell_id(symbol, lead) for lead in range(1, calibrated + 1)]
        yhat_path = list(yhat_path)
        pi_hat_path = [signal["pi_hat"][cell] for cell in cells[:admitted]]
        pi_widened_path = [signal["pi_widened"][cell] for cell in cells[:admitted]]
        scenarios_path = [list(draws[cell]) for cell in cells]
        return {
            "entity": symbol,
            "decision_ts": stamp,
            "lead": 1,
            "price": price,
            "yhat": yhat_path[0],
            "sigma_t": sigma,
            "beta_t": beta,
            "pi_hat": pi_hat_path[0],
            "pi_widened": pi_widened_path[0],
            "weights": list(path["tick_constants"]["weights"]),
            "scenarios": list(scenarios_path[0]),
            "label": dict(release["label"]),
            "known_at": cls._known_at(stamp, release["segment_start_ms"]),
            "admitted_horizon": admitted,
            "plan_horizon": plan_horizon,
            "yhat_path": yhat_path,
            "pi_hat_path": pi_hat_path,
            "pi_widened_path": pi_widened_path,
            "scenarios_path": scenarios_path,
        }

    @classmethod
    def path_rows(cls, release, stamp, ticks, coverage_floor):
        """Every path input row of one decision tick, in entity order (ADR-0188 formulation B).

        One row per name with a tick at ``stamp`` whose screened plan holds
        at least one tranche. A name whose ``kstar`` is 0 gets no row (owner
        question I(a): sell-only), which :meth:`path_plans` makes visible;
        a tick for a name with no path refuses.

        Parameters
        ----------
        release : dict
            A ``releases`` entry published with ``leads: "path"``.
        stamp : int
            The decision instant, epoch ms.
        ticks : dict
            ``{symbol: {"price", "yhat_path", "sigma", "beta"}}`` at
            ``stamp`` (:meth:`MinuteForecastPublisher.path_tick`).
        coverage_floor : float
            As :meth:`path_plan`.

        Returns
        -------
        list of dict
            :meth:`path_row` rows with their screened plans.
        """
        rows = []
        for symbol in sorted(ticks):
            if cls.path_plan(release, symbol, coverage_floor)["plan"] == 0:
                continue
            tick = ticks[symbol]
            rows.append(cls.path_row(
                release, symbol, stamp, tick["price"], tick["yhat_path"], tick["sigma"], tick["beta"],
                coverage_floor,
            ))
        return rows

    @classmethod
    def path_bundle_rows(cls, release, rows):
        """Assemble one path tick's ``ForecastBundle`` rows against the release's joint artifacts.

        The path counterpart of :meth:`bundle_rows` (ADR-0188): the same
        producer, with the path block's ONE false-signal and ONE outcome
        identity on every row. An empty tick is the empty gate; any row
        without the path fields refuses.
        """
        bundle = cls._assembled(release, cls._path_block(release)["uncertainty"], rows)
        if rows and not bundle.has_paths:
            raise ValueError(
                "path_bundle_rows assembles path rows only -- a row without the exit-horizon "
                "fields belongs in bundle_rows (ADR-0188)"
            )
        return bundle.rows

    @staticmethod
    def _assembled(release, group, rows):
        """Build the ``ForecastBundle`` of ``rows`` under the release cap's producer and ``group``'s ids."""
        producer = release["cap"]["producer"]
        return ForecastBundle(
            release["release_id"],
            sorted(rows, key=lambda row: row["entity"]),
            producer={"document_sha256": producer["document_sha256"], "node": producer["node"], "output": "bundle"},
            model_manifest_sha256=release["model_manifest_sha256"],
            uncertainty={slot: group[slot]["attestation"]["artifact_id"] for slot in ("false_signal", "outcome")},
        )

    def _tick_rows(self, walk, index, release, scenarios):
        """``{(ts, lead): [bundle input row, ...]}`` for one segment -- ``yhat`` only, never ``y``."""
        fold = walk.folds[index]
        market, contract = walk.market(index)
        predictions = walk.yhat(index)
        cutoff_ms = fold["cutoff_ms"]
        out = {}
        for symbol, lead in sorted(walk.lead_map.items()):
            weights, draws = scenarios[lead].weighted_draws()
            for stamp, yhat in predictions.get((symbol, lead), ()):
                if not cutoff_ms <= stamp < release["segment_end_ms"]:
                    continue
                price, sigma, beta = self._market_at(market, symbol, stamp)
                out.setdefault((stamp, lead), []).append(
                    self.tick_row(release, symbol, stamp, lead, price, yhat, sigma, beta, weights, draws[symbol], contract)
                )
        return out

    def _bundles(self, walk, index, release, scenarios):
        """One ``ForecastBundle`` per ``(tick, lead group)`` of a segment, as JSON rows."""
        return [
            {
                "fold": index,
                "release_id": release["release_id"],
                "decision_ts": stamp,
                "lead": lead,
                "rows": self.bundle_rows(release, lead, rows),
            }
            for (stamp, lead), rows in sorted(self._tick_rows(walk, index, release, scenarios).items())
        ]


class _MinuteWalk(_Walk):
    """The pinned walk plus every fold's per-minute trade predictions (ADR-0186).

    Each manifest fold must carry ``trade_predictions`` pins; they are
    hashed and snapshotted like the scored pins, and the fold's run dir must
    hold exactly those trade files. Label tapes come from the scored caches
    only (:meth:`_Walk._tapes` skips ``TRADE_CACHE_PREFIX`` nodes).
    """

    def __init__(self, params):
        self._markets = {}
        super().__init__(params)

    def _verified_folds(self, folds):
        """Run the scored verification, then snapshot each fold's trade pins."""
        from dskit.pipeline.predictions import TRADE_PREDICTIONS_FILE, find_predictions

        verified = super()._verified_folds(folds)
        for entry, fold in zip(verified, folds):
            pins = fold.get("trade_predictions")
            if not isinstance(pins, list) or not pins:
                raise ValueError(
                    f"fold {entry['index']} carries no trade prediction pins: the inventory "
                    "is not a minute walk's (ADR-0186)"
                )
            guard, declared = _verified_prediction_snapshot(pins, fold["cutoff"], filename=TRADE_PREDICTIONS_FILE)
            self._guards.append(guard)
            found = sorted(
                os.path.realpath(path) for path in find_predictions(fold["run_dir"], filename=TRADE_PREDICTIONS_FILE)
            )
            if sorted(declared) != found:
                raise ValueError(f"fold {entry['index']} trade prediction inventory drifted")
            entry["trade_snapshot"] = guard.name
        return verified

    def _admission(self, gates, inventory_path, manifest):
        """:meth:`_Walk._admission`, keeping the cap rows and modeled units a path release reads."""
        lead_map = super()._admission(gates, inventory_path, manifest)
        self._cap_rows = {row["unit"]: row for row in _outputs(gates, "gate artifact")["caps"]}
        self._modeled = {
            (row.get("symbol"), row.get("horizon"))
            for row in manifest.get("expected_units") or ()
            if isinstance(row, dict)
        }
        return lead_map

    def calibrated_horizons(self):
        """Every admitted name's calibrated horizon ``K_i``: its gate unit's ``n_horizons`` (ADR-0188).

        ``K_i`` is the number of leads with a scored head (the unit's dense
        evidence ladder from h1), which the path mode forecasts and
        calibrates past the admitted cap ``H_i``. Read in path mode only, so
        a gate artifact without ``n_horizons`` still serves the cap mode.

        Returns
        -------
        dict
            ``{symbol: K_i}`` for every admitted symbol, in symbol order.

        Raises
        ------
        ValueError
            A cap row without an integer ``n_horizons``, one outside
            ``[capped_horizon, 10]``, or one claiming a head the pinned
            inventory never modeled -- every problem named.
        """
        from .final_model import HEADS

        horizons, problems = {}, []
        for symbol, cap in sorted(self.lead_map.items()):
            top = self._cap_rows[symbol].get("n_horizons")
            if isinstance(top, bool) or not isinstance(top, int):
                problems.append(
                    f"gate cap row {symbol} carries no integer n_horizons (got {top!r}): the path mode "
                    "reads each admitted name's calibrated horizon from the gate"
                )
            elif not cap <= top <= len(HEADS):
                problems.append(
                    f"gate cap row {symbol} has n_horizons {top}, outside [{cap}, {len(HEADS)}] "
                    "(its capped_horizon up to the release's scored heads)"
                )
            else:
                unmodeled = [lead for lead in range(1, top + 1) if (symbol, lead) not in self._modeled]
                if unmodeled:
                    problems.append(
                        f"{symbol}:h{unmodeled[0]:02d} was never modeled, yet the gate's n_horizons "
                        f"{top} claims it"
                    )
                else:
                    horizons[symbol] = top
        if problems:
            raise ValueError("path mode refused: " + "; ".join(problems))
        return horizons

    def trade_yhat(self, index, horizons=None):
        """Fold ``index``'s ``{(symbol, lead): [(ts, yhat), ...]}`` trade rows, admitted units only, no ``y``.

        ``horizons`` (``{symbol: K_i}``, the path mode's calibrated horizons,
        ADR-0188) widens each admitted name from its capped lead to every
        scored lead ``1..K_i``; ``None`` keeps the capped lead alone.
        """
        from dskit.pipeline.predictions import TRADE_PREDICTIONS_FILE, read_predictions

        names = ("ts", "series", "horizon", "yhat")
        table = read_predictions(
            self.folds[index]["trade_snapshot"], columns=names, filename=TRADE_PREDICTIONS_FILE,
        )
        if horizons is None:
            wanted = set(self.lead_map.items())
        else:
            wanted = {(symbol, lead) for symbol, top in horizons.items() for lead in range(1, top + 1)}
        out = {}
        for stamp, symbol, lead, value in zip(*(table.get(name, ()) for name in names)):
            if (symbol, int(lead)) in wanted:
                out.setdefault((symbol, int(lead)), []).append((int(stamp), float(value)))
        return out

    def market(self, index):
        """:meth:`_Walk.market`, built once per fold (the release and its ticks both read it).

        Only the latest fold is kept: each holds whole-tape label arrays for
        every admitted name, so keeping every fold would hold them all.
        """
        if index not in self._markets:
            self._markets = {index: super().market(index)}
        return self._markets[index]


class MinuteForecastPublisher(ForecastPublisher):
    """Publish each release and its per-MINUTE tick columns (ADR-0186).

    The ``intraday_equities-minute-forecast-publisher`` kind. Releases are
    :class:`ForecastPublisher`'s -- calibration, false signal and caps from
    the scored lattice rows, unchanged -- plus ``tick_constants`` (each lead
    group's scenario ``weights`` and per-symbol ``scenarios``) and ``label``
    (the fold label's contract), so a decider can assemble any minute's
    bundle. ``ticks`` replaces ``bundles``: per release and admitted unit,
    the columns ``ts``, ``price``, ``yhat``, ``sigma`` and ``beta`` at every
    minute its pinned trade predictions cover inside the segment. It refuses
    unless the trade ``yhat`` equals the scored ``yhat`` at every scored
    stamp of the segment (one model, one feature pipeline).

    PATH mode (ADR-0188 formulation B) is declared, never defaulted: with
    ``leads: "path"`` every release is the default release PLUS a ``path``
    block (:meth:`_path_release`: each admitted name's calibrated horizon
    ``K_i`` from the gate, ONE joint outcome calibration over every cell
    ``(name, lead <= K_i)`` with its per-cell coverage beside the attested
    artifact-level one, the false-signal estimate per admitted cell, and ONE
    joint scenario draw over the cells), and every tick block also carries
    ``yhat_by_lead``: the trade ``yhat`` of every scored head ``1..K_i``,
    one column per lead in lead order, refused when a head misses a minute
    its capped lead ticks. :meth:`path_tick` reads one minute of it and
    :meth:`ForecastPublisher.path_rows` turns that into the tick's path rows.

    Parameters
    ----------
    params : dict
        :class:`ForecastPublisher`'s, plus the optional ``leads``:
        ``"cap"`` (the default: ADR-0186's releases and ticks, byte for
        byte) or ``"path"``.

    Examples
    --------
    ::

        out = MinuteForecastPublisher("publish", params).run(ctx, {})
        out["ticks"][0]
        # -> {"fold": 2, "release_id": ..., "symbol": "LLY", "lead": 2, "ts": [...], "price": [...], ...}
    """

    outputs = ("releases", "ticks")
    _WALK = _MinuteWalk
    _TICK_PORT = "ticks"
    _KIND = "intraday_equities-minute-forecast-publisher"
    #: The optional ``leads`` param's closed vocabulary (ADR-0188).
    _LEADS = ("cap", "path")

    @classmethod
    def validate_params(cls, params):
        """:meth:`ForecastPublisher.validate_params`, plus the optional ``leads`` (default ``"cap"``)."""
        problems = super().validate_params({name: value for name, value in params.items() if name != "leads"})
        if "leads" in params and params["leads"] not in cls._LEADS:
            problems.append(f"leads must be one of {list(cls._LEADS)} (default 'cap'), got {params['leads']!r}")
        return problems

    def fingerprint(self):
        """:meth:`ForecastPublisher.fingerprint`, naming the path mode when it is declared."""
        fingerprint = super().fingerprint()
        if self._path_mode():
            fingerprint["leads"] = "path"
        return fingerprint

    def _path_mode(self):
        """Whether the document declared ``leads: "path"`` (ADR-0188)."""
        return self.params.get("leads", "cap") == "path"

    def _release(self, walk, index, producer):
        """Return the lattice release plus the constants a per-minute bundle needs.

        In path mode the release also carries :meth:`_path_release`'s block,
        projected from the SAME false-signal estimate the lead groups read.
        """
        estimate = self._false_signal(walk, index) if self._path_mode() else None
        release, scenarios = super()._release(walk, index, producer, estimate=estimate)
        _market, contract = walk.market(index)
        constants = {}
        for lead in release["lead_groups"]:
            weights, draws = scenarios[lead].weighted_draws()
            members = sorted(symbol for symbol, held in release["lead_map"].items() if held == lead)
            constants[str(lead)] = {"weights": list(weights), "scenarios": {s: list(draws[s]) for s in members}}
        release["tick_constants"] = constants
        release["label"] = contract
        if estimate is not None:
            release["path"] = self._path_release(walk, index, release, estimate)
        return release, scenarios

    def _path_release(self, walk, index, release, estimate):
        """Build the release's ``path`` block (ADR-0188 formulation B), as JSON.

        * ``horizons`` -- ``{symbol: K_i}``, every admitted name's calibrated
          horizon (:meth:`_MinuteWalk.calibrated_horizons`).
        * ``cells`` -- the joint panel's columns ``SYM:hNN``, every lead
          ``1..K_i`` of every admitted name, in symbol then lead order.
        * ``uncertainty`` -- ONE outcome band over the cells, calibrated on
          the same complete-case lattice rows and UTC-day blocks the lead
          groups use (a row is one instant across every name AND lead), and
          ``estimate`` projected to every ADMITTED cell (lead ``<= H_i``):
          nothing is calibrated past the cap. Both attested.
        * ``measured_coverage`` -- the band's artifact-level out-of-sample
          coverage (the number its attestation carries and the capital
          intake screens); ``cell_coverage`` -- each cell's own reading on
          the same rows, the per-cell screen of
          :meth:`ForecastPublisher.path_plan` (owner question I(a)).
        * ``tick_constants`` -- ONE joint scenario draw over the cells
          (shared ``weights``, one residual array per cell, seeded from the
          panel digest as every draw here is), which every tick of the
          release reads, so scenario ``o`` is one calibration instant across
          every name and every lead.
        """
        horizons = walk.calibrated_horizons()
        release_id = release["release_id"]
        cells = {
            self._cell_id(symbol, lead): (symbol, lead)
            for symbol in sorted(horizons)
            for lead in range(1, horizons[symbol] + 1)
        }
        outcome, scenarios, measured, per_cell = self._outcome(walk, index, cells, "path", release_id)
        admitted = {cell: cell for cell, (symbol, lead) in cells.items() if lead <= release["lead_map"][symbol]}
        weights, draws = scenarios.weighted_draws()
        return {
            "horizons": horizons,
            "cells": list(cells),
            "uncertainty": {
                "false_signal": self._false_signal_obj(
                    estimate, admitted, "path", release_id, release["segment_start_ms"]
                ),
                "outcome": outcome,
            },
            "measured_coverage": measured,
            "cell_coverage": per_cell,
            "tick_constants": {
                "weights": list(weights),
                "scenarios": {cell: list(draws[cell]) for cell in cells},
            },
        }

    def _publish_ticks(self, walk, index, release, scenarios):
        """Per admitted unit, the segment's per-minute tick columns (every head's too, in path mode)."""
        del scenarios
        market, _contract = walk.market(index)
        horizons = release["path"]["horizons"] if self._path_mode() else None
        trade = walk.trade_yhat(index, horizons)
        scored = walk.yhat(index)
        start, end = release["segment_start_ms"], release["segment_end_ms"]
        out = []
        for symbol, lead in sorted(walk.lead_map.items()):
            rows = [(stamp, value) for stamp, value in trade.get((symbol, lead), ()) if start <= stamp < end]
            stamps = [stamp for stamp, _ in rows]
            if stamps != sorted(set(stamps)):
                raise ValueError(f"{symbol}:h{lead:02d} trade rows are not unique and time-ordered")
            self._refuse_trade_disagreement(symbol, lead, dict(rows), scored.get((symbol, lead), ()), start, end)
            block = {"fold": index, "release_id": release["release_id"], "symbol": symbol, "lead": lead,
                     "ts": stamps, "price": [], "yhat": [value for _, value in rows], "sigma": [], "beta": []}
            for stamp in stamps:
                price, sigma, beta = self._market_at(market, symbol, stamp)
                block["price"].append(price)
                block["sigma"].append(sigma)
                block["beta"].append(beta)
            if horizons is not None:
                block["yhat_by_lead"] = self._lead_columns(
                    symbol, stamps, horizons[symbol], trade, scored, start, end
                )
            out.append(block)
        return out

    def _lead_columns(self, symbol, stamps, calibrated, trade, scored, start, end):
        """Every scored head's trade ``yhat`` at ``stamps``: one column per lead ``1..calibrated``.

        Each head is held to the capped lead's own checks -- unique,
        time-ordered rows equal to the scored ``yhat`` at every scored stamp
        of the segment -- and must tick exactly the minutes the capped lead
        ticks: a path row needs every head at its minute, so a missing cell
        refuses by name (ADR-0188).
        """
        columns = []
        for lead in range(1, calibrated + 1):
            rows = [(stamp, value) for stamp, value in trade.get((symbol, lead), ()) if start <= stamp < end]
            found = [stamp for stamp, _ in rows]
            if found != sorted(set(found)):
                raise ValueError(f"{symbol}:h{lead:02d} trade rows are not unique and time-ordered")
            if found != stamps:
                missing = sorted(set(stamps) - set(found))
                extra = sorted(set(found) - set(stamps))
                raise ValueError(
                    f"{symbol}:h{lead:02d} trade rows do not tick the minutes its capped lead ticks "
                    f"({len(missing)} missing, e.g. {missing[:3]}; {len(extra)} extra, e.g. {extra[:3]}): "
                    "a path row carries every scored head at its minute (ADR-0188)"
                )
            self._refuse_trade_disagreement(symbol, lead, dict(rows), scored.get((symbol, lead), ()), start, end)
            columns.append([value for _, value in rows])
        return columns

    @staticmethod
    def path_tick(block, at):
        """Minute ``at`` of a path tick block, as the ``ticks`` entry the path builders take (ADR-0188).

        Parameters
        ----------
        block : dict
            One ``ticks`` entry published with ``leads: "path"``.
        at : int
            The minute's index in ``block["ts"]``.

        Returns
        -------
        dict
            ``{"price", "yhat_path", "sigma", "beta"}``, where
            ``yhat_path`` is every scored head's prediction at that minute
            in lead order -- the input of :meth:`ForecastPublisher.path_row`.

        Raises
        ------
        ValueError
            The block was published without the path mode.
        """
        if "yhat_by_lead" not in block:
            raise ValueError(
                f"tick block {block.get('symbol')!r} carries no yhat_by_lead: its publisher did not "
                "declare leads 'path' (ADR-0188)"
            )
        return {
            "price": block["price"][at],
            "yhat_path": [column[at] for column in block["yhat_by_lead"]],
            "sigma": block["sigma"][at],
            "beta": block["beta"][at],
        }

    @staticmethod
    def _refuse_trade_disagreement(symbol, lead, trade, scored, start, end):
        """Refuse unless every scored stamp in the segment has an identical trade ``yhat``."""
        bad = [
            (stamp, value, trade.get(stamp))
            for stamp, value in scored
            if start <= stamp < end and trade.get(stamp) != value
        ]
        if bad:
            raise ValueError(
                f"{symbol}:h{lead:02d} trade yhat differs from (or lacks) the scored yhat at "
                f"{len(bad)} stamp(s), e.g. {bad[:3]}"
            )


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
    "half_spread_bps": {},
    "default_half_spread_bps": 0.0,
    "eq_ratio": 1.0,
    "spread_time_of_day": {"timezone": "UTC", "default_multiplier": 1.0, "windows": []},
    "taf_per_share": 0.0,
    "sec31_bps": 0.0,
    "min_price": 1.0,
}


class MioDecider:
    """Per-tick ``EquityKellyMIO`` strategy an ``EquityReplay`` calls (ADR-0184 S6).

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
    that group. Every solve that ran -- a refused non-optimal one
    included -- leaves ``{asof_ms, lead, **SolveRecord.to_obj()}`` in
    :attr:`solves` (ADR-0183 phase 2) and is counted in :attr:`n_solves`
    and :attr:`solve_seconds`. The per-tick steps are hooks --
    :meth:`_context`, :meth:`_names_at`, :meth:`_exclusion`,
    :meth:`_bundle_rows` -- which :class:`MinuteMioDecider` overrides.

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
    keep_solves : bool, optional
        ``False`` counts solves without keeping their records (a per-minute
        run makes millions). Default ``True``.

    Examples
    --------
    Drive one release through the replay::

        decider = MioDecider(release, bundles, mio, policy, ctx)
        EquityReplay(policy, cash_policy, decider=decider).run(bars)
        decider.refused  # [{"asof_ms": ..., "lead": 2, "reason": "mio_refused", ...}]
    """

    def __init__(self, release, bundles, mio, fill_policy, ctx, keep_solves=True):
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
        self._keep_solves = bool(keep_solves)
        self.skipped = []
        self.refused = []
        self.solves = []
        self.n_solves = 0
        self.solve_seconds = 0.0

    def decide(self, asof_ms, portfolio):
        """Orders for this tick: ``{"symbol", "asof_ms", "lead", "qty", "side": "buy"}``."""
        context = self._context(asof_ms, portfolio)
        cash = context["cash"]
        orders = []
        for lead in self._leads:
            names = self._names_at(asof_ms, lead)
            if not names:
                continue
            kept = []
            for name in names:
                reason = self._exclusion(name, asof_ms, lead, context)
                if reason is None:
                    kept.append(name)
                else:
                    self.skipped.append({"symbol": name, "asof_ms": asof_ms, "lead": lead, "reason": reason})
            if not kept:
                continue
            kept = self._bundle_rows(asof_ms, lead, kept)
            node = EquityKellyMIO(f"mio_h{lead:02d}", {**self._params, **self._costs, **self._pins(kept)})
            inputs = {
                "bundle": kept,
                "portfolio": {
                    "asof_ms": asof_ms,
                    "cash": cash,
                    "buying_power": cash,
                    # Held units were dropped above, so the MIO sees an empty
                    # book: its inventory path is not exercised (ADR-0184).
                    "positions": {},
                    "mark_prices": portfolio["mark_prices"],
                    "gross_limit": portfolio["gross_limit"],
                    "cash_reserve": 0.0,
                    "sale_credit": 1.0,
                    # Each name's fill-bar instant: the time-of-day spread
                    # is keyed on it in sizing exactly as the replay bills.
                    "fill_ms": portfolio["fill_ms"],
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
            finally:
                self._keep_solve(node, asof_ms, lead)
            for symbol, trade in sorted(out["trades"].items()):
                if trade["sell"]:
                    raise ValueError(f"{node.key}: MIO sold {symbol} from an empty book at {asof_ms}")
                orders.append({"symbol": symbol, "asof_ms": asof_ms, "lead": lead, "qty": trade["buy"], "side": "buy"})
            cash = out["cash_after"]
        return orders

    def _context(self, asof_ms, portfolio):
        """Per-tick state the exclusions and the budget read: the held names and the cash."""
        del asof_ms
        return {
            "held": {symbol for symbol, shares in portfolio["positions"].items() if shares},
            "cash": portfolio["cash"],
        }

    def _names_at(self, asof_ms, lead):
        """Return the names with a forecast for ``lead`` at this tick, in bundle order."""
        return [row["entity"] for row in self._bundles.get((asof_ms, lead)) or ()]

    def _exclusion(self, name, asof_ms, lead, context):
        """Why ``name`` sits this tick out, or ``None``: a held unit is never re-sized (ADR-0184)."""
        del asof_ms, lead
        return "open_lot_at_decision" if name in context["held"] else None

    def _bundle_rows(self, asof_ms, lead, names):
        """Return the stored bundle's rows for ``names``."""
        wanted = set(names)
        return [row for row in self._bundles[(asof_ms, lead)] if row["entity"] in wanted]

    def _keep_solve(self, node, asof_ms, lead):
        """Count the node's solve, if it solved (a refused solve included); keep it when asked."""
        if node.solve_record is not None:
            record = node.solve_record.to_obj()
            self.n_solves += 1
            self.solve_seconds += float(record["seconds"])
            if self._keep_solves:
                self.solves.append({"asof_ms": asof_ms, "lead": lead, **record})

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


_MINUTE_MS = 60_000


class MinuteMioDecider(MioDecider):
    """The per-tick MIO decider over per-MINUTE tick columns (ADR-0186).

    Holds one release (a :class:`MinuteForecastPublisher` ``releases``
    entry) and its ``ticks``; at a decision instant it assembles each lead
    group's ``ForecastBundle`` rows through the SAME builders the lattice
    publisher uses (:meth:`ForecastPublisher.tick_row`,
    :meth:`ForecastPublisher.bundle_rows`) from the tick at that instant
    alone. Beyond :class:`MioDecider`'s held-unit rule it skips, recording
    the reason, a unit whose entry is queued but not filled
    (``pending_entry_at_decision``) -- reserving that entry's cost, qty x
    the tick price it was sized at plus the cost model's buy cost per
    share at the entry's scheduled fill minute (its ``fill_ms``), from the
    cash the MIO may spend -- and a unit whose forced exit
    (fill ``fill_bar_offset`` minutes later, exit ``lead`` minutes after the
    fill, both read from the fill policy) would fall after the date's close
    (``exit_after_close``).

    Parameters
    ----------
    release : dict
        One minute ``releases`` entry.
    ticks : list of dict
        The publisher's ``ticks``; only this release's blocks are used.
    mio, fill_policy, ctx, keep_solves
        As :class:`MioDecider`.
    closes : dict
        ``{local ISO date: last tape minute (epoch ms)}``: the session close.
    tz : zoneinfo.ZoneInfo
        The zone ``closes`` is keyed in.

    Examples
    --------
    ::

        decider = MinuteMioDecider(release, ticks, mio, policy, ctx, closes, ZoneInfo("America/New_York"))
        EquityReplay(policy, cash_policy, decider=decider).run(bars)
        decider.skipped  # [{"symbol": "LLY", "asof_ms": ..., "lead": 2, "reason": "open_lot_at_decision"}, ...]
    """

    def __init__(self, release, ticks, mio, fill_policy, ctx, closes, tz, keep_solves=True):
        super().__init__(release, [], mio, fill_policy, ctx, keep_solves=keep_solves)
        self._fill_offset = int(fill_policy.fill_bar_offset)
        self._closes = dict(closes)
        self._tz = tz
        self._cost_model = SchwabCostModel(self._costs)
        self._groups = {
            lead: sorted(symbol for symbol, held in release["lead_map"].items() if held == lead)
            for lead in self._leads
        }
        self._blocks, self._at = {}, {}
        for block in ticks:
            if block["release_id"] != release["release_id"]:
                continue
            symbol = block["symbol"]
            if release["lead_map"].get(symbol) != int(block["lead"]) or symbol in self._blocks:
                raise ValueError(f"tick block {symbol}:h{block['lead']} is not one admitted unit of the release")
            self._blocks[symbol] = block
            self._at[symbol] = {int(stamp): index for index, stamp in enumerate(block["ts"])}

    def _context(self, asof_ms, portfolio):
        """Held names, queued names, the date's close, and cash less the queued entries' cost."""
        context = super()._context(asof_ms, portfolio)
        pending = portfolio["pending"]
        context["pending"] = {row["symbol"] for row in pending}
        context["cash"] -= sum(self._reserved(row) for row in pending if row["side"] == "buy")
        context["close"] = self._closes.get(_local_date(asof_ms, self._tz))
        return context

    def _reserved(self, row):
        """Estimate a queued buy's cost at the tick price and fill minute it was sized at."""
        at = self._at.get(row["symbol"], {}).get(int(row["decision_ms"]))
        if at is None:
            raise ValueError(f"pending entry {row!r} was not sized from this release's ticks")
        if "fill_ms" not in row:
            raise ValueError(
                f"pending entry {row!r} carries no fill_ms: its time-of-day spread is keyed on "
                "the scheduled fill minute"
            )
        price = self._blocks[row["symbol"]]["price"][at]
        return row["qty"] * price + row["qty"] * self._cost_model.buy_per_share(
            row["symbol"], price, row["fill_ms"]
        )

    def _names_at(self, asof_ms, lead):
        """Return the lead group's names with a tick at this instant."""
        return [symbol for symbol in self._groups[lead] if asof_ms in self._at.get(symbol, ())]

    def _exclusion(self, name, asof_ms, lead, context):
        """Held, queued, or not able to exit before the close -- else ``None``."""
        reason = super()._exclusion(name, asof_ms, lead, context)
        if reason is not None:
            return reason
        if name in context["pending"]:
            return "pending_entry_at_decision"
        close = context["close"]
        if close is None or asof_ms + (self._fill_offset + lead) * _MINUTE_MS > close:
            return "exit_after_close"
        return None

    def _bundle_rows(self, asof_ms, lead, names):
        """Assemble this tick's bundle rows for ``names`` from the tick at ``asof_ms`` only."""
        constants = self._release["tick_constants"][str(lead)]
        rows = []
        for symbol in names:
            block, at = self._blocks[symbol], self._at[symbol][asof_ms]
            rows.append(ForecastPublisher.tick_row(
                self._release, symbol, asof_ms, lead, block["price"][at], block["yhat"][at],
                block["sigma"][at], block["beta"][at], constants["weights"],
                constants["scenarios"][symbol], self._release["label"],
            ))
        return ForecastPublisher.bundle_rows(self._release, lead, rows)


class MioDeciderNode(Node):
    """Carry the validated per-tick MIO params to the simulation (ADR-0184 S6, Revision 3).

    The ``intraday_equities-mio-decider`` kind. It sizes nothing: a DAG
    node runs once, while the MIO must run per tick on live cash, so this
    node only validates the ``EquityKellyMIO`` base params and emits them,
    with the releases' ascending lead-group order, as JSON for the
    ``simulate`` node's :class:`MioDecider`. Role ``transform``, not
    ``capital``: the planner's capital rule requires a ``stat_test`` wire,
    and this document's survivor set is the pinned gate admission carried
    on each release (ADR-0184 Revision 1), never a new test.

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
                "post-selection evidence (ADR-0184 Revision 2)"
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


#: Stamped on every ``simulate``/``report`` row, the summary and the run
#: metadata (ADR-0184 S7, Revision 2): nothing here is deployment evidence.
DISCLOSURE = {
    "deployment_eligible": False,
    "evidence_scope": "development_replay_post_selection",
    "cap_evidence_look_ahead": CAP_LOOK_AHEAD_DISCLOSURE,
}
#: A bundle's ``price`` is the fold cache tape's float32 close; the bar's
#: close is the same minute read from the store. Relative float32 rounding
#: is ~6e-8, so anything past this is a different minute or a different tape.
_PRICE_TOLERANCE = 1e-6


def _local_date(asof_ms, tz):
    """ISO local date of an epoch-ms instant in ``tz``."""
    return datetime.fromtimestamp(int(asof_ms) / 1000, tz=timezone.utc).astimezone(tz).date().isoformat()


class _DayCloses:
    """Forward to a per-tick decider, keeping each local date's last live account state.

    ``EquityReplay`` hands its decider :meth:`EquityReplay._portfolio` after
    every tick's exits and entries, so the last one of a date is that
    date's closing cash and NAV as the replay itself holds them.
    """

    def __init__(self, inner, tz):
        self._inner = inner
        self._tz = tz
        self.closes = {}

    def decide(self, asof_ms, portfolio):
        """Record ``portfolio`` as its date's latest state, then delegate."""
        self.closes[_local_date(asof_ms, self._tz)] = portfolio
        return self._inner.decide(asof_ms, portfolio)


class DevelopmentSimulation(DevelopmentReplay):
    """Run every release's segment through ``EquityReplay`` with the per-tick MIO (ADR-0184 S7).

    The ``intraday_equities-development-simulation`` kind (role
    ``transform``). A :class:`~intraday_equities.replay.DevelopmentReplay`
    subclass, so it keeps that doorway's gates -- ``deployment_eligible``
    false, ``caps`` ``development-only``, the ``evidence_end`` window
    (``_refuse_out_of_window``) and the digest-pinned fill and cash-flow
    policies -- and adds nothing but the segment loop. It reads NO data
    itself: its ``bars`` input is sliced into one segment per release
    (``[segment_start_ms, segment_end_ms)``, gate-admitted names only,
    ``halted`` false where the source carries no halt flag), and each
    segment runs ``EquityReplay(fill policy, cash policy,
    decider=MioDecider(...))`` -- the production release lifecycle: new
    model, new release, restart, account carried. Cash carries through the
    policy's :meth:`~intraday_equities.replay.CashFlowPolicy.segment` hook,
    bound to the previous segment's closing cash and the whole run's trading
    calendar, so the pinned seed is booked once and a scheduled policy
    (ADR-0185) keeps one global phase across releases; a trading date with
    no contribution reports 0. A segment that ends with an open lot refuses. Every output row, and the metadata,
    carry :data:`DISCLOSURE`.

    Parameters
    ----------
    params : dict
        ``DevelopmentReplay``'s five, with ``cash_flow_policy`` and
        ``cash_flow_policy_sha256`` REQUIRED here, plus ``first_fold`` and
        ``last_fold`` (ints >= 2, the releases this run must receive) and
        optional ``consume_bars`` (JSON bool, default false): clear the
        ``bars`` input list once it is sliced -- the explicit ownership
        transfer ``concat``'s ``consume_inputs`` makes, for bounded memory;
        and optional ``keep_solves`` (JSON bool, default true): false leaves
        ``solves`` empty and records ``{count, seconds}`` in the metadata
        instead (ADR-0186: a per-minute run solves millions of times).

    Inputs
    ------
    ``bars`` (records), ``releases`` and ``bundles`` (the publisher's),
    ``mio`` (the ``decide`` node's).

    Outputs
    -------
    ``fills``, ``skipped``, ``refused``
        The replays' rows plus the decider's (``mio_refused``,
        ``open_lot_at_decision``), each with ``fold`` and ``release_id``.
    ``cash``
        One row per trading date: the ledger's booked amount, the carried
        cash (segment-opening rows only), the external ``contribution``
        and its running total, and the replay's own closing ``cash_close``,
        ``nav_close`` and ``marks``.
    ``metadata``
        The disclosure, the pins, and per segment its opening and closing
        cash.
    ``solves``
        Every MIO solve (:attr:`MioDecider.solves`), with ``fold`` and
        ``release_id`` (ADR-0183 phase 2; additive).

    Examples
    --------
    ::

        node = DevelopmentSimulation("simulate", {
            "deployment_eligible": False, "evidence_end": "2025-10-16",
            "fill_policy": "configs/fill-policy.json", "fill_policy_sha256": fill_sha,
            "caps": "development-only",
            "cash_flow_policy": "configs/cash-flow-policy.json",
            "cash_flow_policy_sha256": cash_sha,
            "first_fold": 2, "last_fold": 19,
        })
        out = node.run(ctx, {"bars": bars, "releases": releases, "bundles": bundles, "mio": mio})
    """

    outputs = ("fills", "skipped", "refused", "cash", "metadata", "solves")
    _PARAMS = DevelopmentReplay._PARAMS + (
        "cash_flow_policy",
        "cash_flow_policy_sha256",
        "first_fold",
        "last_fold",
    )
    _OPTIONAL_PARAMS = ("consume_bars", "keep_solves")
    #: The publisher port this node sizes from; the minute subclass reads ``ticks``.
    _TICK_PORT = "bundles"

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none (the inherited gates first)."""
        problems = super().validate_params(params)
        for name in ("first_fold", "last_fold"):
            if name in params:
                problems.extend(ForecastPublisher._int_problems(params, name, 2))
        if not problems and params["last_fold"] < params["first_fold"]:
            problems.append("last_fold must be >= first_fold")
        for name in cls._OPTIONAL_PARAMS:
            if not isinstance(params.get(name, False), bool):
                problems.append(f"{name} must be a JSON bool, got {params[name]!r}")
        return problems

    def validate_inputs(self, inputs):
        """Require ``bars``/``bundles`` (or ``ticks``) lists, a non-empty ``releases`` list and a ``mio`` mapping."""
        problems = []
        for port in ("bars", self._TICK_PORT, "releases"):
            if not isinstance(inputs.get(port), list):
                problems.append(f"{port} must be a list")
        if isinstance(inputs.get("releases"), list) and not inputs["releases"]:
            problems.append("releases must not be empty")
        mio = inputs.get("mio")
        if not isinstance(mio, dict) or not {"params", "lead_groups"} <= set(mio):
            problems.append("mio must be the decide node's {params, lead_groups}")
        return problems

    def run(self, ctx, inputs):
        """Replay every segment in fold order, carrying cash; see the class docstring."""
        problems = self.validate_inputs(inputs)
        if problems:
            raise ConfigError([f"{self.key}: {problem}" for problem in problems])
        releases = sorted(inputs["releases"], key=lambda release: release["fold"])
        folds = [release["fold"] for release in releases]
        declared = list(range(self.params["first_fold"], self.params["last_fold"] + 1))
        if folds != declared:
            raise ConfigError([
                f"{self.key}: the releases cover folds {folds}, but first_fold..last_fold "
                f"declare {declared}"
            ])
        bundles = inputs[self._TICK_PORT]
        self._refuse_out_of_window(inputs["bars"], self._decision_rows(bundles))
        segments, census = self._segment_bars(inputs["bars"], releases)
        if self.params.get("consume_bars", False):
            inputs["bars"].clear()
        tz = self._cash_flow_policy.timezone
        calendar = frozenset(
            date.fromisoformat(_local_date(bar["asof_ms"], tz)) for segment in segments for bar in segment
        )
        out = {"fills": [], "skipped": [], "refused": [], "cash": [], "solves": []}
        summaries = []
        carried = Decimal("0")
        contributed = Decimal("0")
        lots, open_at_end, solves = [], [], {"count": 0, "seconds": 0.0}
        for index, release in enumerate(releases):
            bars, segments[index] = segments[index], None
            self._refuse_price_disagreement(release, bundles, bars)
            policy = self._cash_flow_policy.segment(carried if index else None, calendar)
            decider = self._decider(release, bundles, inputs["mio"], ctx, bars, tz)
            recorder = _DayCloses(decider, tz)
            replay = self._replay(policy, recorder, lots)
            result = replay.run(bars)
            lots = self._carry(release, result)
            if index == len(releases) - 1:
                lots, open_at_end = [], lots
            stamp = {"fold": release["fold"], "release_id": release["release_id"], **DISCLOSURE}
            out["fills"].extend({**row, **stamp} for row in result["fills"])
            out["skipped"].extend({**row, **stamp} for row in result["skipped"] + decider.skipped)
            out["refused"].extend({**row, **stamp} for row in result["refused"] + decider.refused)
            solves["count"] += decider.n_solves
            solves["seconds"] += decider.solve_seconds
            if self.params.get("keep_solves", True):
                out["solves"].extend({**row, **stamp} for row in decider.solves)
            rows, contributed = self._cash_rows(
                replay, recorder, carried if index else None, contributed, tz, self._cash_flow_policy,
            )
            out["cash"].extend({**row, **stamp} for row in rows)
            summaries.append({
                "fold": release["fold"],
                "release_id": release["release_id"],
                "segment_start_ms": release["segment_start_ms"],
                "segment_end_ms": release["segment_end_ms"],
                "opening_cash": str(carried),
                "closing_cash": str(replay.cash_balance),
                "trading_days": len(rows),
                "bars": len(bars),
                "fills": len(result["fills"]),
                **self._segment_extras(lots),
            })
            carried = replay.cash_balance
            del bars, replay, recorder, decider
        out["metadata"] = {
            **DISCLOSURE,
            "evidence_end": self.params["evidence_end"],
            "first_fold": self.params["first_fold"],
            "last_fold": self.params["last_fold"],
            "fill_policy_sha256": self.params["fill_policy_sha256"],
            "cash_flow_policy_sha256": self.params["cash_flow_policy_sha256"],
            "currency": self._cash_flow_policy.currency,
            "timezone": tz.key,
            "segments": summaries,
            **census,
            **self._run_extras(open_at_end),
        }
        if not self.params.get("keep_solves", True):
            out["metadata"]["solves"] = solves
        return out

    # -- hooks the minute subclass overrides (ADR-0186) -------------------

    @staticmethod
    def _decision_rows(bundles):
        """Return the decision stamps the evidence-window gate reads: every bundle's."""
        return [{"asof_ms": bundle["decision_ts"]} for bundle in bundles]

    def _decider(self, release, bundles, mio, ctx, bars, tz):
        """Build the segment's per-tick strategy: the lattice :class:`MioDecider`."""
        del bars, tz
        return MioDecider(
            release, bundles, mio, self._policy, ctx, keep_solves=self.params.get("keep_solves", True),
        )

    def _replay(self, cash_policy, recorder, lots):
        """Build the segment's replay; no lot ever crosses a lattice release boundary."""
        del lots
        return EquityReplay(self._policy, cash_policy, decider=recorder)

    def _carry(self, release, result):
        """Refuse a segment that ended with an open lot (ADR-0184); nothing is carried."""
        stuck = [row for row in result["refused"] if row["reason"] == "expiry_past_tape"]
        if stuck:
            raise ConfigError([
                f"{self.key}: segment fold {release['fold']} ended with {len(stuck)} open lot(s) "
                f"that cannot exit inside it, e.g. {stuck[:3]}"
            ])
        return []

    def _segment_extras(self, carried_out):
        """Extra per-segment summary fields; none on the lattice."""
        del carried_out
        return {}

    def _run_extras(self, open_at_end):
        """Extra run metadata; none on the lattice."""
        del open_at_end
        return {}

    def _segment_bars(self, bars, releases):
        """Slice ``bars`` into one minimal bar list per release; count what no segment holds."""
        policy = self._policy
        starts = [release["segment_start_ms"] for release in releases]
        ends = [release["segment_end_ms"] for release in releases]
        admitted = [set(release["survivors"]) for release in releases]
        prices = (policy.decision_price_field, policy.fill_price_field, policy.forced_exit_price_field)
        segments = [[] for _ in releases]
        outside = not_admitted = 0
        for bar in bars:
            asof = int(bar["asof_ms"])
            k = bisect_right(starts, asof) - 1
            if k < 0 or asof >= ends[k]:
                outside += 1
                continue
            symbol = bar[policy.symbol_field]
            if symbol not in admitted[k]:
                not_admitted += 1
                continue
            row = {policy.symbol_field: symbol, "asof_ms": asof, policy.halt_field: bar.get(policy.halt_field, False)}
            row.update({field: bar[field] for field in prices if field in bar})
            segments[k].append(row)
        return segments, {"bars_outside_segments": outside, "bars_not_admitted": not_admitted}

    def _refuse_price_disagreement(self, release, bundles, bars):
        """Refuse when a bar's decision close is not the bundle's price for that minute."""
        field = self._policy.decision_price_field
        closes = {(bar[self._policy.symbol_field], bar["asof_ms"]): bar.get(field) for bar in bars}
        bad = []
        for bundle in bundles:
            if bundle["release_id"] != release["release_id"]:
                continue
            for row in bundle["rows"]:
                close = closes.get((row["entity"], int(bundle["decision_ts"])))
                if close is not None and abs(close - row["price"]) > _PRICE_TOLERANCE * max(abs(row["price"]), 1.0):
                    bad.append((row["entity"], bundle["decision_ts"], close, row["price"]))
        if bad:
            raise ConfigError([
                f"{self.key}: the bar {field} disagrees with the bundle price at {len(bad)} "
                f"decision(s) of fold {release['fold']}, e.g. {bad[:3]}"
            ])

    @staticmethod
    def _cash_rows(replay, recorder, carried, contributed, tz, policy):
        """One row per trading date from the replay's booked cash flows and its day closes.

        A policy that funds every trading date (the daily one) still refuses
        a ticked date nothing was booked on; a scheduled one reports it as 0.
        """
        booked = {}
        for body in replay.booked_cash_flows:
            day = _local_date(body["effective_at_ms"], tz)
            booked[day] = booked.get(day, Decimal("0")) + Decimal(body["amount"])
        if not set(booked) <= set(recorder.closes):
            raise ConfigError([
                f"funded dates {sorted(set(booked) - set(recorder.closes))} are not dates "
                "the replay ticked"
            ])
        if policy.FUNDS_EVERY_TRADING_DAY and set(booked) != set(recorder.closes):
            raise ConfigError([
                f"ticked dates {sorted(set(recorder.closes) - set(booked))} were not funded "
                "under a policy that funds every trading date"
            ])
        rows = []
        for position, day in enumerate(sorted(recorder.closes)):
            carry = carried if carried is not None and position == 0 else Decimal("0")
            booked.setdefault(day, Decimal("0"))
            contribution = booked[day] - carry
            contributed += contribution
            close = recorder.closes[day]
            rows.append({
                "date": day,
                "booked": str(booked[day]),
                "carried_in": str(carry),
                "contribution": str(contribution),
                "contributions_to_date": str(contributed),
                "cash_close": close["cash"],
                "nav_close": close["gross_limit"],
                "marks": dict(close["mark_prices"]),
            })
        return rows, contributed


class MinuteDevelopmentSimulation(DevelopmentSimulation):
    """Replay every release deciding EVERY MINUTE (ADR-0186).

    The ``intraday_equities-minute-development-simulation`` kind: a
    :class:`DevelopmentSimulation` -- every gate, the cash carry and the
    outputs unchanged -- that reads the minute publisher's ``ticks`` instead
    of ``bundles`` and decides through :class:`MinuteMioDecider`, whose
    session close per date is the last minute of this segment's own bars.
    A lot still open at a release boundary (a name that missed minutes
    after a late entry) is carried into the next release's replay, the way
    production restarts with its positions, and listed in the segment's
    ``carried_out``; one open at the evidence end stays marked in NAV and is
    listed in the metadata's ``open_at_evidence_end``.

    Parameters
    ----------
    params : dict
        :class:`DevelopmentSimulation`'s.

    Examples
    --------
    ::

        node = MinuteDevelopmentSimulation("simulate", {**params, "keep_solves": False})
        out = node.run(ctx, {"bars": bars, "releases": releases, "ticks": ticks, "mio": mio})
    """

    _TICK_PORT = "ticks"

    @staticmethod
    def _decision_rows(ticks):
        """Each tick block's LAST stamp: the window gate refuses any stamp at or past the end."""
        return [{"asof_ms": max(block["ts"])} for block in ticks if block["ts"]]

    def _decider(self, release, ticks, mio, ctx, bars, tz):
        """Build the minute decider, its closes read from this segment's bars."""
        closes = {}
        for bar in bars:
            day = _local_date(bar["asof_ms"], tz)
            closes[day] = max(closes.get(day, 0), int(bar["asof_ms"]))
        return MinuteMioDecider(
            release, ticks, mio, self._policy, ctx, closes, tz,
            keep_solves=self.params.get("keep_solves", True),
        )

    def _replay(self, cash_policy, recorder, lots):
        """Build a replay seeded with the lots the previous release left open, returning its own."""
        return EquityReplay(self._policy, cash_policy, decider=recorder, carried_lots=lots, carry_lots=True)

    def _carry(self, release, result):
        """Return the lots this release leaves open, carried to the next."""
        del release
        return list(result["open_lots"])

    def _segment_extras(self, carried_out):
        """Each segment names the lots it handed on."""
        return {"carried_out": list(carried_out)}

    def _run_extras(self, open_at_end):
        """Return the lots still open at the evidence end, marked in NAV."""
        return {"open_at_evidence_end": list(open_at_end)}

    def _refuse_price_disagreement(self, release, ticks, bars):
        """Refuse when a bar's decision close is not the tick price for that minute."""
        field = self._policy.decision_price_field
        closes = {(bar[self._policy.symbol_field], bar["asof_ms"]): bar.get(field) for bar in bars}
        bad = []
        for block in ticks:
            if block["release_id"] != release["release_id"]:
                continue
            for stamp, price in zip(block["ts"], block["price"]):
                close = closes.get((block["symbol"], int(stamp)))
                if close is not None and abs(close - price) > _PRICE_TOLERANCE * max(abs(price), 1.0):
                    bad.append((block["symbol"], stamp, close, price))
        if bad:
            raise ConfigError([
                f"{self.key}: the bar {field} disagrees with the tick price at {len(bad)} "
                f"minute(s) of fold {release['fold']}, e.g. {bad[:3]}"
            ])


class SimulationReport(Node):
    """Fold the simulation's fills into a daily NAV/P&L report (ADR-0184 S7).

    The ``intraday_equities-simulation-report`` kind (role ``report``). It
    reads only its wires. Every fill becomes one ``records.Fill`` folded
    through :class:`dskit.production.accounting.WindowBook` -- the one P&L
    fold production uses -- once for the account, once per symbol and once
    per lead. NAV is ``contributions to date + WindowBook.pnl(marks)``
    (cash is contributions plus every fill's cash, so the two are the same
    number); the replay's own day-close NAV rides beside it as
    ``replay_nav`` and ``nav_discrepancy``, a cross-check of two
    independent accounts of one history.

    Parameters
    ----------
    params : dict
        None (``notes`` allowed).

    Inputs
    ------
    ``fills``, ``skipped``, ``refused``, ``cash``, ``metadata`` (the
    ``simulate`` node's) and ``releases`` (the publisher's).

    Outputs
    -------
    ``daily``
        One row per trading date: ``nav``, ``contribution``,
        ``contributions_to_date``, ``net_pnl`` (NAV less contributions),
        ``realised_pnl`` and ``unrealised_pnl``, ``fees`` and
        ``fees_to_date``, ``buy_notional``/``sell_notional``, ``turnover``
        (gross notional / NAV), ``entries``/``exits``, ``drawdown`` (the
        daily net P&L's peak-to-date less its value), ``replay_cash``,
        ``replay_nav``, ``nav_discrepancy``, the fold and the disclosure.
    ``summary``
        Totals, ``max_drawdown`` (``WindowBook.drawdown``: fill-resolution
        net P&L path), fills by kind, refusals and skips by reason,
        attribution by symbol and by lead, per-segment release evidence,
        the disclosure and the run metadata.

    Examples
    --------
    ::

        report = SimulationReport("report", {}).run(ctx, {**simulate_outputs, "releases": releases})
        report["summary"]["final_nav"]
    """

    role = "report"
    outputs = ("daily", "summary")
    _PORTS = ("fills", "skipped", "refused", "cash", "releases")

    @classmethod
    def validate_params(cls, params):
        """Refuse every knob: the report is a function of its wires."""
        problems = []
        reject_unknown_params(problems, params, ("notes",))
        return problems

    def validate_inputs(self, inputs):
        """Require the list ports and a metadata mapping that carries the disclosure."""
        problems = [f"{port} must be a list" for port in self._PORTS if not isinstance(inputs.get(port), list)]
        metadata = inputs.get("metadata")
        if not isinstance(metadata, dict) or {k: metadata.get(k) for k in DISCLOSURE} != DISCLOSURE:
            problems.append("metadata must be the simulate node's, carrying the development disclosure")
        return problems

    def run(self, ctx, inputs):
        """Build ``daily`` and ``summary``; see the class docstring."""
        problems = self.validate_inputs(inputs)
        if problems:
            raise ConfigError([f"{self.key}: {problem}" for problem in problems])
        metadata = inputs["metadata"]
        tz = ZoneInfo(metadata["timezone"])
        by_day = {}
        for index, row in enumerate(inputs["fills"]):
            by_day.setdefault(_local_date(row["asof_ms"], tz), []).append((index, row))
        account, symbols, leads = WindowBook(), {}, {}
        attribution = {}
        daily, peak, fees_to_date, gross, navs = [], Fraction(0), Fraction(0), Fraction(0), []
        marks = {}
        for cash in inputs["cash"]:
            day = cash["date"]
            fees = buy = sell = Fraction(0)
            entries = exits = 0
            for index, row in by_day.pop(day, ()):
                fill = self._fill(index, row, metadata["currency"])
                account.apply(fill)
                symbols.setdefault(row["symbol"], WindowBook()).apply(fill)
                leads.setdefault(str(row["lead"]), WindowBook()).apply(fill)
                notional = fill.price * fill.qty
                entry = attribution.setdefault(row["symbol"], {
                    "lead": row["lead"], "fees": Fraction(0), "entries": 0, "exits": 0,
                    "buy_notional": Fraction(0), "sell_notional": Fraction(0),
                })
                entry["fees"] += Fraction(fill.fee)
                entry["entries" if row["kind"] == "entry" else "exits"] += 1
                entry["buy_notional" if row["side"] == "buy" else "sell_notional"] += Fraction(notional)
                fees += Fraction(fill.fee)
                if row["side"] == "buy":
                    buy += Fraction(notional)
                else:
                    sell += Fraction(notional)
                entries += row["kind"] == "entry"
                exits += row["kind"] == "exit"
            marks = cash["marks"]
            contributed = Fraction(Decimal(cash["contributions_to_date"]))
            pnl = account.pnl(marks.get)
            nav = contributed + pnl
            peak = max(peak, pnl)
            fees_to_date += fees
            gross += buy + sell
            navs.append(nav)
            daily.append({
                "date": day,
                "nav": float(nav),
                "contribution": float(Decimal(cash["contribution"])),
                "contributions_to_date": float(contributed),
                "net_pnl": float(pnl),
                "realised_pnl": float(account.realised),
                "unrealised_pnl": float(account.unrealised(marks.get)),
                "fees": float(fees),
                "fees_to_date": float(fees_to_date),
                "buy_notional": float(buy),
                "sell_notional": float(sell),
                "turnover": float((buy + sell) / nav) if nav > 0 else None,
                "entries": entries,
                "exits": exits,
                "drawdown": float(peak - pnl),
                "replay_cash": cash["cash_close"],
                "replay_nav": cash["nav_close"],
                "nav_discrepancy": float(nav) - cash["nav_close"],
                "fold": cash["fold"],
                "release_id": cash["release_id"],
                **DISCLOSURE,
            })
        if by_day:
            raise ConfigError([f"{self.key}: fills on dates with no cash row: {sorted(by_day)[:5]}"])
        summary = self._summary(inputs, daily, account, marks, symbols, leads, attribution, gross, navs)
        return {"daily": daily, "summary": summary}

    @staticmethod
    def _fill(index, row, currency):
        """One simulation fill row as the ``records.Fill`` ``WindowBook`` folds."""
        return Fill(
            fill_id=f"{row['fold']}-{index}",
            venue_ref="development-replay",
            client_ref=f"{row['kind']}-{row['symbol']}-{row['lead']}-{row['asof_ms']}",
            instrument=row["symbol"],
            side=row["side"],
            qty=Decimal(str(row["qty"])),
            price=Decimal(str(row["price"])),
            fee=Decimal(str(row["fee"])),
            fee_currency=currency,
            liquidity="taker",
            status="final",
            ts_ms=int(row["asof_ms"]),
            native=None,
        )

    @staticmethod
    def _counts(rows):
        """``{reason: count}`` over rows."""
        return dict(Counter(row["reason"] for row in rows))

    def _summary(self, inputs, daily, account, marks, symbols, leads, attribution, gross, navs):
        """Return the run's totals, attribution and evidence (JSON)."""
        refusals = self._counts(inputs["refused"])
        by_symbol = {
            symbol: {
                "lead": entry["lead"],
                "realised_pnl": float(symbols[symbol].realised),
                "fees": float(entry["fees"]),
                "entries": entry["entries"],
                "exits": entry["exits"],
                "buy_notional": float(entry["buy_notional"]),
                "sell_notional": float(entry["sell_notional"]),
            }
            for symbol, entry in sorted(attribution.items())
        }
        by_lead = {}
        for symbol, entry in by_symbol.items():
            lead = by_lead.setdefault(str(entry["lead"]), {"symbols": [], "fees": 0.0, "entries": 0, "exits": 0})
            lead["symbols"].append(symbol)
            lead["fees"] += entry["fees"]
            lead["entries"] += entry["entries"]
            lead["exits"] += entry["exits"]
        for lead, book in leads.items():
            by_lead[lead]["realised_pnl"] = float(book.realised)
        mean_nav = sum(navs) / len(navs) if navs else Fraction(0)
        last = daily[-1] if daily else {}
        return {
            **DISCLOSURE,
            "metadata": inputs["metadata"],
            "currency": inputs["metadata"]["currency"],
            "first_date": daily[0]["date"] if daily else None,
            "last_date": last.get("date"),
            "trading_days": len(daily),
            "final_nav": last.get("nav"),
            "total_contributed": last.get("contributions_to_date"),
            "net_pnl": last.get("net_pnl"),
            "realised_pnl": float(account.realised),
            "unrealised_pnl": float(account.unrealised(marks.get)),
            "fees": sum(row["fees"] for row in daily),
            "gross_notional": float(gross),
            "turnover": float(gross / mean_nav) if mean_nav > 0 else None,
            "max_drawdown": float(account.drawdown(marks.get)),
            "max_daily_drawdown": max((row["drawdown"] for row in daily), default=0.0),
            "max_abs_nav_discrepancy": max((abs(row["nav_discrepancy"]) for row in daily), default=0.0),
            "fills_by_kind": {
                kind: sum(1 for row in inputs["fills"] if row["kind"] == kind) for kind in ("entry", "exit")
            },
            "refusals_by_reason": refusals,
            "skips_by_reason": self._counts(inputs["skipped"]),
            "mio_refused": refusals.get("mio_refused", 0),
            "by_symbol": by_symbol,
            "by_lead": by_lead,
            "segments": [
                {
                    "fold": release["fold"],
                    "release_id": release["release_id"],
                    "cutoff": release["cutoff"],
                    "survivors": release["survivors"],
                    "lead_map": release["lead_map"],
                    "measured_coverage": release["calibration"]["measured_coverage"],
                    "false_signal": {
                        lead: {
                            "pi_hat": group["false_signal"]["artifact"]["pi_hat"],
                            "pi_widened": group["false_signal"]["artifact"]["pi_widened"],
                        }
                        for lead, group in release["uncertainty"].items()
                    },
                }
                for release in sorted(inputs["releases"], key=lambda release: release["fold"])
            ],
        }



#: The template value the simulation stage must bind; any other value is a stale pin.
BOUND_BY_STAGE = "BOUND-BY-STAGE"
#: Either publisher binds: the lattice one (ADR-0184) or the minute one (ADR-0186).
_PUBLISHER_KINDS = (ForecastPublisher._KIND, MinuteForecastPublisher._KIND)
_WRITER_KINDS = ("records-write", "table-write")


class RetrainedSimulation(Stage):
    """Run the ADR-0184 decision graph over THIS staged run's retrained walk (ADR-0185).

    The last stage of ``configs/run-retrain-simulation.json``. It reads the
    template document (sha256-pinned), requires every run-specific value to
    be the literal :data:`BOUND_BY_STAGE`, and binds them: the publisher's
    ``inventory_manifest``/``gates`` to this run's ``inventory_stage`` and
    ``gates_stage`` artifacts with their sha256 read now, its ``walk_root``
    to the directory the walks ran from (the process's working directory),
    and each writer's ``path`` under ``<artifact_dir>/<key>/``. The on-disk
    inventory must be the manifest this stage was handed. The bound graph
    runs through the ordinary ``run_document``; a run that does not finish
    ``ran`` refuses. Nothing here decides, sizes or accounts -- the graph's
    own nodes do.

    Parameters
    ----------
    params : dict
        ``template`` (path, relative to the staged document's directory or
        absolute), ``template_sha256``, ``inventory_stage``, ``gates_stage``.

    Examples
    --------
    ::

        stage = RetrainedSimulation("simulate", {
            "template": "run-retrain-simulation-template.json",
            "template_sha256": digest, "inventory_stage": "inventory", "gates_stage": "gates",
        })
        out = stage.run(ctx, {"manifest": manifest, "caps": caps})
    """

    outputs = ("run_dir", "document_hash", "summary", "files")
    _PARAMS = ("template", "template_sha256", "inventory_stage", "gates_stage")

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + ("notes",))
        for name in cls._PARAMS:
            if not isinstance(params.get(name), str) or not params.get(name):
                problems.append(f"{name} must be a non-empty string")
        if isinstance(params.get("template_sha256"), str) and not is_sha256hex(params["template_sha256"]):
            problems.append("template_sha256 must be a lowercase sha256")
        return problems

    def validate_inputs(self, inputs):
        """Require the inventory manifest and the gate caps (the stages this one follows)."""
        if not isinstance(inputs, dict) or set(inputs) != {"manifest", "caps"}:
            return ["inputs must contain exactly manifest and caps"]
        return []

    def run(self, ctx, inputs):
        """Bind the template to this run, execute it, and return where its results are."""
        from dskit.pipeline import driver
        from dskit.pipeline.document import PipelineDocument

        obj = self._template(ctx)
        inventory = self._artifact(ctx, self.params["inventory_stage"])
        gates = self._artifact(ctx, self.params["gates_stage"])
        if inventory["value"].get("outputs", {}).get("manifest") != inputs["manifest"]:
            raise ValueError(f"{self.key}: the inventory on disk differs from the manifest this stage was handed")
        if gates["value"].get("outputs", {}).get("caps") != inputs["caps"]:
            raise ValueError(f"{self.key}: the gates on disk differ from the caps this stage was handed")
        files = self._bind(obj, ctx, inventory, gates)
        document = PipelineDocument.from_obj(obj)
        result = driver.run_document(document, asof=ctx.asof)
        if result.state != "ran":
            raise ValueError(f"{self.key}: the simulation ended in state {result.state!r} ({result.run_dir})")
        summary = (result.outputs.get("report") or {}).get("summary")
        return {"run_dir": result.run_dir, "document_hash": document.hash, "summary": summary, "files": files}

    def _template(self, ctx):
        """Return the pinned template document object."""
        import hashlib

        path = self.params["template"]
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(os.path.abspath(ctx.source_path)), path)
        with open(path, "rb") as handle:
            raw = handle.read()
        if hashlib.sha256(raw).hexdigest() != self.params["template_sha256"]:
            raise ValueError(f"{self.key}: the template {path} does not match template_sha256")
        return json.loads(raw)

    @staticmethod
    def _artifact(ctx, stage_key):
        """``{path, sha256, value}`` of one earlier stage's artifact in this run."""
        import hashlib

        path = os.path.realpath(os.path.join(ctx.artifact_dir, f"{stage_key}.json"))
        with open(path, "rb") as handle:
            raw = handle.read()
        return {"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "value": json.loads(raw)}

    def _bind(self, obj, ctx, inventory, gates):
        """Replace every :data:`BOUND_BY_STAGE` value; refuse a template with a stale one."""
        values = {
            "inventory_manifest": inventory["path"], "inventory_manifest_sha256": inventory["sha256"],
            "gates": gates["path"], "gates_sha256": gates["sha256"], "walk_root": os.getcwd(),
        }
        out_dir = os.path.join(ctx.artifact_dir, self.key)
        files = {}
        publishers = 0
        for key, node in obj["pipeline"].items():
            params = node.get("params") or {}
            if node.get("uses") in _PUBLISHER_KINDS:
                publishers += 1
                stale = sorted(name for name in values if params.get(name) != BOUND_BY_STAGE)
                if stale:
                    raise ValueError(f"{self.key}: publisher {key} must carry {BOUND_BY_STAGE} in {stale}")
                params.update(values)
            elif node.get("uses") in _WRITER_KINDS:
                prefix = BOUND_BY_STAGE + "/"
                if not str(params.get("path", "")).startswith(prefix):
                    raise ValueError(f"{self.key}: writer {key} path must start with {prefix}")
                params["path"] = os.path.join(out_dir, params["path"][len(prefix):])
                files[key] = params["path"]
        if publishers != 1:
            raise ValueError(f"{self.key}: the template must hold exactly one publisher, found {publishers}")
        return files


NODE_KINDS = {
    "intraday_equities-forecast-publisher": ForecastPublisher,
    "intraday_equities-minute-forecast-publisher": MinuteForecastPublisher,
    "intraday_equities-mio-decider": MioDeciderNode,
    "intraday_equities-development-simulation": DevelopmentSimulation,
    "intraday_equities-minute-development-simulation": MinuteDevelopmentSimulation,
    "intraday_equities-simulation-report": SimulationReport,
}

for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)
