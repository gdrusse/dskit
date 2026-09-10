"""Final-model domain assembly — ten leads, one shared HPO grid (ADR-0114 Phase 2, reconciling ADR-0113's Phase 1).

This module owns exactly what ADR-0114's plan §5 assigns it and nothing
generic: the ten lead (``h01``..``h10``) head-name vocabulary, the real
24-combination LightGBM HPO grid (the plan-locked five dimensions, read
verbatim from ``configs/run-final-hpo.json``'s own ``hpo_space``), the
exact P16 lean-mask drop list (read from its one real source,
``configs/run-p16-feature-mask-zoo.json``, never hardcoded a second
time), the lead-specific outer-aligned forecast-accuracy score (squared-
error improvement vs. the training-mean baseline — never Spearman IC,
per the plan's §2), the ADR-0114 §11 item 1 ruling's simplicity order,
and synthetic frozen-winner refit helpers that can assemble ten fitted heads
for :func:`dskit.pipeline.libs.sklearn.write_bundle` when called directly.

Every generic mechanism — the candidate inventory, the trial ledger, the
one-standard-error selection rule, the cluster-robust standard error, the
multi-head bundle artifact — is IMPORTED, never re-derived: this file
calls into :mod:`dskit.pipeline.kinds_search`, :mod:`dskit.pipeline.stats`
and :mod:`dskit.pipeline.libs.sklearn`.

**No pipeline execution and no real data here.** The assembly helpers are
plain, directly-callable Python APIs exercised with synthetic rows and a
synthetic ``evaluate`` callback.  ``FinalRefit`` is wired by
``configs/run-final-refit.json`` only as a fail-closed pipeline contract: its
config is PENDING and the node unconditionally refuses validation and runtime
until the driver owns immutable completed-run provenance, content-derived
identities for the materialized refit rows, and ten labelled input wires.
Filling the current evidence placeholders cannot enable a refit or bundle
write.  Nothing here reads market data, fits a real model against real
history, or loads a real P16 artifact.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime, timezone

from dskit.pipeline.kinds_search import (
    CandidateInventory,
    OneStandardErrorSelector,
    TrialLedger,
)
from dskit.pipeline.driver import resolve_json_artifact
from dskit.pipeline.document import load_document
from dskit.pipeline.libs.sklearn import ColumnSubsetEstimator
from dskit.pipeline.node import Node, reject_unknown_params
from dskit.pipeline.stats import cluster_bootstrap_t

__all__ = [
    "EVIDENCE_FIELDS",
    "FinalRefit",
    "HEADS",
    "boundary_flags",
    "build_candidate_inventory",
    "cluster_scores_by_day",
    "hpo_space",
    "lean_feature_drop",
    "permitted_for_refit",
    "refit_heads",
    "run_lead_selection",
    "simplicity_key",
    "squared_error_improvement",
]


class FinalRefit(Node):
    """Non-executable contract for a future attested final refit.

    The current driver has neither an immutable completed-run manifest nor
    content-derived identities for its materialized input rows.  Mutable
    ``result.json``/node/carry sidecars and config-supplied source/cache hashes
    cannot prove ADR-0116 provenance.  Validation therefore fails closed even
    after placeholders are filled.  Keep the assembly helpers below for the
    future owner that supplies both trustworthy contracts.
    """

    role = "train"
    outputs = ("bundle_path", "manifest")
    _PARAMS = (
        "hpo_run_dir",
        "hpo_document_sha256",
        "hpo_evidence",
        "feature_order",
        "categorical_feature",
        "categorical_encoding",
        "predict_fixture",
        "refit_identity",
        "seed",
    )

    @classmethod
    def validate_params(cls, params):
        """Return problems with evidence pins and bundle schema knobs."""
        problems = [
            "FinalRefit is non-executable: no trustworthy run attestation or "
            "content-derived input identity contract is available"
        ]
        reject_unknown_params(problems, params, cls._PARAMS)
        run_dir = params.get("hpo_run_dir")
        if not isinstance(run_dir, str) or not run_dir:
            problems.append("hpo_run_dir must be a non-empty path")
        elif "PENDING" in run_dir.upper():
            problems.append("hpo_run_dir is pending a real final-HPO run")
        document_digest = params.get("hpo_document_sha256")
        if (
            not isinstance(document_digest, str)
            or len(document_digest) != 64
            or any(char not in "0123456789abcdef" for char in document_digest)
        ):
            problems.append("hpo_document_sha256 must be a lowercase sha256 digest")
        evidence = params.get("hpo_evidence")
        if not isinstance(evidence, dict) or set(evidence) != set(HEADS):
            problems.append(f"hpo_evidence must be keyed by exactly {list(HEADS)!r}")
        elif any(not isinstance(value, dict) for value in evidence.values()):
            problems.append("hpo_evidence pins are pending real JSON-artifact manifests")
        features = params.get("feature_order")
        if (
            not isinstance(features, list)
            or not features
            or len(features) != len(set(features))
            or any(not isinstance(name, str) or not name for name in features)
        ):
            problems.append("feature_order must be a non-empty unique string list")
        categories = params.get("categorical_feature")
        if not isinstance(categories, list) or any(type(i) is not int for i in categories):
            problems.append("categorical_feature must be a list of integer indices")
        encoding = params.get("categorical_encoding")
        if not isinstance(encoding, dict):
            problems.append("categorical_encoding must be a mapping")
        fixture = params.get("predict_fixture")
        if not isinstance(fixture, list) or not fixture:
            problems.append("predict_fixture must be a non-empty list")
        identity = params.get("refit_identity")
        identity_fields = {
            "source", "cache", "train_start_ms", "refit_end_ms",
            "embargo_start_ms", "embargo_end_ms",
        }
        if not isinstance(identity, dict) or set(identity) != identity_fields:
            problems.append(f"refit_identity must carry exactly {sorted(identity_fields)!r}")
        else:
            for field in ("source", "cache"):
                value = identity[field]
                digest = value.get("sha256") if isinstance(value, dict) else None
                if (not isinstance(value, dict) or not value or type(digest) is not str
                        or len(digest) != 64
                        or any(char not in "0123456789abcdef" for char in digest)):
                    problems.append(f"refit_identity.{field} must be a non-empty identity with sha256")
            if type(identity["train_start_ms"]) is not int or identity["train_start_ms"] < 0:
                problems.append("refit_identity.train_start_ms must be a nonnegative integer")
            expected = {
                "refit_end_ms": LOCKBOX_START_MS,
                "embargo_start_ms": EMBARGO_START_MS,
                "embargo_end_ms": EMBARGO_END_MS,
            }
            for field, value in expected.items():
                if identity[field] != value:
                    problems.append(f"refit_identity.{field} must equal {value}")
        seed = params.get("seed", 0)
        if type(seed) is not int or seed < 0:
            problems.append("seed must be a nonnegative integer")
        return problems

    def validate_inputs(self, inputs):
        """Require exactly one labelled-row stream for every frozen head."""
        if not isinstance(inputs, dict) or set(inputs) != set(HEADS):
            return [f"inputs must be keyed by exactly {list(HEADS)!r}"]
        problems = [f"{head} must be a list of labelled rows"
                    for head in HEADS if not isinstance(inputs[head], list)]
        identity = self.params.get("refit_identity")
        if isinstance(identity, dict) and type(identity.get("train_start_ms")) is int:
            start = identity["train_start_ms"]
            for head in HEADS:
                if isinstance(inputs[head], list) and any(
                    not isinstance(row, Mapping) or type(row.get("ts_ms")) is not int
                    or row["ts_ms"] < start for row in inputs[head]
                ):
                    problems.append(f"{head} contains a row before refit_identity.train_start_ms")
        return problems

    def _verified_hpo_outputs(self):
        """Refuse mutable sidecars until the driver owns a run attestation."""
        raise ValueError(
            "FinalRefit: no trustworthy run attestation binds the HPO document, "
            "run, node outputs, and carry"
        )

    def _winner_from_evidence(self, head, evidence):
        """Rebuild the frozen inventory, ledger, and 1-SE ruling."""
        template = self._hpo_template()
        model = template["model"]
        inventory = CandidateInventory(
            model["hpo_space"], n_trials=model["hpo_trials"], seed=model["hpo_seed"]
        )
        ledger_obj, selection = evidence["ledger"], evidence["selection"]
        if not isinstance(ledger_obj, dict) or set(ledger_obj) != {
            "inventory_digest", "evidence_fields", "rows",
        }:
            raise ValueError(f"FinalRefit: {head} ledger has the wrong shape")
        if ledger_obj["inventory_digest"] != inventory.digest:
            raise ValueError(f"FinalRefit: {head} ledger inventory differs from pinned HPO")
        if tuple(ledger_obj["evidence_fields"]) != EVIDENCE_FIELDS:
            raise ValueError(f"FinalRefit: {head} ledger evidence fields differ from contract")
        ledger = TrialLedger(inventory, evidence_fields=EVIDENCE_FIELDS)
        for row in ledger_obj["rows"]:
            ledger.record(row["overrides"], row["score"], **{
                key: value for key, value in row.items() if key not in {"overrides", "score"}
            })
        if ledger.to_obj() != ledger_obj:
            raise ValueError(f"FinalRefit: {head} ledger is incomplete or noncanonical")
        ruled = OneStandardErrorSelector(select="max", simplicity_key=simplicity_key).select(ledger)
        if ruled.to_obj() != selection:
            raise ValueError(f"FinalRefit: {head} selection is not the pinned one-standard-error ruling")
        return ruled.selected_candidate

    def _hpo_template(self):
        templates = self._hpo_document["stages"]["finalist"]["params"]["templates"]
        matches = [row for row in templates if row.get("family") == "pooled-lightgbm"]
        if len(matches) != 1:
            raise ValueError("FinalRefit: pinned HPO document has no unique LightGBM recipe")
        return matches[0]

    def _winners(self):
        source_document = load_document(
            os.path.join(self.params["hpo_run_dir"], "config.json")
        )
        if source_document.hash != self.params["hpo_document_sha256"]:
            raise ValueError("FinalRefit: final-HPO document identity does not match its pin")
        self._hpo_document = source_document.to_obj()
        winners = {}
        inventory_digest = None
        attested = self._verified_hpo_outputs()
        manifest_digests = [attested[head].get("sha256") for head in HEADS]
        if len(set(manifest_digests)) != len(HEADS):
            raise ValueError("FinalRefit: every head requires a distinct evidence manifest")
        for head in HEADS:
            manifest = attested[head]
            evidence = resolve_json_artifact(
                self.params["hpo_run_dir"], manifest
            )
            if not isinstance(evidence, dict) or set(evidence) != {
                "producer_key", "feature_order", "categorical_feature",
                "ledger", "selection",
            }:
                raise ValueError(f"FinalRefit: {head} evidence has the wrong shape")
            if evidence["producer_key"] != f"scan_{head}":
                raise ValueError(f"FinalRefit: {head} evidence has the wrong producer")
            if evidence["feature_order"] != self.params["feature_order"]:
                raise ValueError(f"FinalRefit: {head} feature order differs from the pin")
            if evidence["categorical_feature"] != self.params["categorical_feature"]:
                raise ValueError(f"FinalRefit: {head} category contract differs from the pin")
            ledger = evidence["ledger"]
            digest = ledger.get("inventory_digest") if isinstance(ledger, dict) else None
            if inventory_digest is None:
                inventory_digest = digest
            elif digest != inventory_digest:
                raise ValueError("FinalRefit: every head must share one candidate inventory")
            winners[head] = dict(self._winner_from_evidence(head, evidence))
        return winners

    def _base_params(self):
        params = dict(self._hpo_template()["model"]["estimator_params"])
        if params.pop("estimator", None) != "lightgbm.LGBMRegressor":
            raise ValueError("FinalRefit: pinned HPO document has the wrong estimator")
        params.pop("drop", None)
        return params

    def run(self, ctx, inputs):
        """Fail closed until run and input attestations have upstream owners."""
        raise ValueError(
            "FinalRefit is non-executable: trustworthy run and content-derived "
            "input attestations are unavailable"
        )

#: The ten independent LightGBM lead heads (ADR-0114 §2): one per direct
#: lead h=1..10, sharing feature schema, category rules, search space and
#: release identity — never trees or weights.
HEADS = tuple(f"h{i:02d}" for i in range(1, 11))

#: The one seed :func:`build_candidate_inventory` defaults to — matches
#: the ``hpo_seed`` already declared beside this grid in
#: ``configs/run-final-hpo.json``'s pooled-lightgbm template, so a caller
#: who never overrides ``seed`` reuses the same convention rather than an
#: arbitrary new one.
DEFAULT_INVENTORY_SEED = 0

#: The exact frozen inventory size ADR-0114 §2 locks: "the same exact 24
#: hyperparameter combinations."
FROZEN_CANDIDATE_COUNT = 24

#: Every per-candidate evidence field :func:`run_lead_selection` records,
#: beyond TrialLedger's own reserved ``overrides``/``score``. ``se`` is
#: OneStandardErrorSelector's own required field; ``diagnostics`` is the
#: full :func:`dskit.pipeline.stats.cluster_bootstrap_t` result (so the
#: ledger never re-derives what that call already returned); the rest are
#: the plan's own named diagnostics — "train/validation gaps, collapsed
#: prediction variance, and whether the selected value lies on a searched
#: boundary" (§2) plus the fit identity the plan's §6 test list requires
#: ("fit seed, cuts, row counts").
EVIDENCE_FIELDS = (
    "se",
    "diagnostics",
    "on_boundary",
    "fit_seed",
    "cuts",
    "n_rows",
    "train_val_gap",
    "collapsed_prediction_variance",
)

#: The one real source of the P16 lean mask — never a second hardcoded
#: copy of its 33 names (root CLAUDE.md's duplication rule).
_DEFAULT_LEAN_MASK_CONFIG = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "configs",
        "run-p16-feature-mask-zoo.json",
    )
)

#: The one real source of the locked 24-combination LightGBM HPO grid —
#: never a second hardcoded copy of its five dimensions (root CLAUDE.md's
#: duplication rule; the same rule :func:`lean_feature_drop` already
#: follows for the mask).
_DEFAULT_HPO_CONFIG = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "configs",
        "run-final-hpo.json",
    )
)

#: The locked pre-March calendar (ADR-0114 §2, carried from the master
#: plan): fit/HPO/refit reads stop before the 2026-03-01 lockbox, and the
#: 2025-12-01 session is embargoed — read by neither the fit nor the HPO
#: window. UTC midnight boundaries; the plan does not name a session
#: timezone for this specific cut (unlike the capital-flow calendar's
#: explicit America/New_York, §11 item 2), so this module states the
#: assumption here rather than inventing a silent one.
def _epoch_ms(date_str):
    """One UTC-midnight epoch-ms boundary for a locked calendar date."""
    return int(
        datetime.strptime(date_str, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc)
        .timestamp()
        * 1000
    )


EMBARGO_START_MS = _epoch_ms("2025-12-01")
EMBARGO_END_MS = _epoch_ms("2025-12-02")
LOCKBOX_START_MS = _epoch_ms("2026-03-01")


def permitted_for_refit(ts_ms) -> bool:
    """Report whether ``ts_ms`` is inside the frozen-winner refit window.

    Parameters
    ----------
    ts_ms : int
        A row's event timestamp, epoch milliseconds.

    Returns
    -------
    bool
        ``True`` when ``ts_ms`` is strictly before the 2026-03-01
        lockbox AND outside the half-open 2025-12-01 embargo session.
    """
    if ts_ms >= LOCKBOX_START_MS:
        return False
    return not (EMBARGO_START_MS <= ts_ms < EMBARGO_END_MS)


def lean_feature_drop(config_path=None) -> tuple:
    """Return the exact P16 lean-mask column drop list, read from its one real source (ADR-0108) rather than hardcoded a second time.

    Parameters
    ----------
    config_path : str, optional
        Override path to the config (default: the shipped
        ``configs/run-p16-feature-mask-zoo.json`` beside this package).

    Returns
    -------
    tuple of str
        The 33 dropped column names, in the config's own declared order.

    Raises
    ------
    ValueError
        The config is missing, unreadable, or its ``"lean"`` template's
        shape does not match ADR-0108/ADR-0114 (not exactly one ``"lean"``
        template, the wrong family/estimator, or not exactly 33 names).

    Examples
    --------
    Read the shipped mask::

        drop = lean_feature_drop()
        len(drop)
        # -> 33
    """
    path = _DEFAULT_LEAN_MASK_CONFIG if config_path is None else config_path
    try:
        with open(path, encoding="utf-8") as fh:
            document = json.load(fh)
    except OSError as exc:
        raise ValueError(f"lean_feature_drop: cannot read {path!r} ({exc})") from exc
    templates = (
        document.get("stages", {})
        .get("materialize", {})
        .get("params", {})
        .get("templates", [])
    )
    matches = [
        t for t in templates if isinstance(t, dict) and t.get("id") == "lean"
    ]
    if len(matches) != 1:
        raise ValueError(
            f"lean_feature_drop: {path!r} must declare exactly one "
            f"templates[].id == 'lean', found {len(matches)}"
        )
    template = matches[0]
    model = template.get("model", {})
    if (
        template.get("family") != "pooled-lightgbm"
        or model.get("estimator")
        != "dskit.pipeline.libs.sklearn.ColumnSubsetEstimator"
    ):
        raise ValueError(
            f"lean_feature_drop: {path!r}'s 'lean' template does not match "
            "ADR-0108's pooled-lightgbm/ColumnSubsetEstimator shape"
        )
    drop = model.get("estimator_params", {}).get("drop")
    if (
        not isinstance(drop, list)
        or len(drop) != 33
        or not all(isinstance(name, str) and name for name in drop)
    ):
        raise ValueError(
            f"lean_feature_drop: {path!r}'s 'lean' template drop list is "
            "not the expected 33 non-empty column names"
        )
    return tuple(drop)


def hpo_space(config_path=None) -> dict:
    """Return the real five-dimension LightGBM HPO grid, read from its one real source (ADR-0114 §11.1) rather than hardcoded a second time.

    Parameters
    ----------
    config_path : str, optional
        Override path to the config (default: the shipped
        ``configs/run-final-hpo.json`` beside this package).

    Returns
    -------
    dict
        ``"learning_rate"``/``"num_leaves"``/``"min_child_samples"``/
        ``"reg_lambda"``/``"reg_alpha"`` -> that dimension's declared
        value list, in the config's own order — 324 combinations total.

    Raises
    ------
    ValueError
        The config is missing, unreadable, or its finalist template's
        shape does not match ADR-0114 (not exactly one
        ``family == "pooled-lightgbm"`` template, or no non-empty
        ``hpo_space`` mapping of dimension -> value list).

    Examples
    --------
    Read the shipped grid::

        space = hpo_space()
        sorted(space)
        # -> ['learning_rate', 'min_child_samples', 'num_leaves', 'reg_alpha', 'reg_lambda']
    """
    path = _DEFAULT_HPO_CONFIG if config_path is None else config_path
    try:
        with open(path, encoding="utf-8") as fh:
            document = json.load(fh)
    except OSError as exc:
        raise ValueError(f"hpo_space: cannot read {path!r} ({exc})") from exc
    templates = (
        document.get("stages", {})
        .get("finalist", {})
        .get("params", {})
        .get("templates", [])
    )
    # Matched by FAMILY, not `id`: `id` is the finalist's candidate-name
    # component (ADR-0115 — it must read "lean" to match
    # FinalistCandidate's own "{id}-pooled-h{horizon}" recipe lookup
    # against the real P16 winner "lean-pooled-h10"), so pinning this
    # read to one fixed `id` string would refuse the very config it
    # exists to serve the moment that name changed for an unrelated
    # reason. `family == "pooled-lightgbm"` is the stable identity of
    # "the one LightGBM HPO recipe" this function's caller cares about.
    matches = [
        t for t in templates
        if isinstance(t, dict) and t.get("family") == "pooled-lightgbm"
    ]
    if len(matches) != 1:
        raise ValueError(
            f"hpo_space: {path!r} must declare exactly one "
            f"templates[] entry with family == 'pooled-lightgbm', found "
            f"{len(matches)}"
        )
    template = matches[0]
    model = template.get("model", {})
    space = model.get("hpo_space")
    if (
        not isinstance(space, dict)
        or not space
        or not all(
            isinstance(values, list) and values for values in space.values()
        )
    ):
        raise ValueError(
            f"hpo_space: {path!r}'s pooled-lightgbm template hpo_space is "
            "not a non-empty mapping of dimension -> non-empty value list"
        )
    return {name: list(values) for name, values in space.items()}


def build_candidate_inventory(
    seed=DEFAULT_INVENTORY_SEED, n_trials=FROZEN_CANDIDATE_COUNT
) -> CandidateInventory:
    """Return the frozen candidate inventory every lead reuses byte-identically.

    ADR-0114 §2: "the candidate list is materialized once, ordered,
    hashed, and reused by every head. A lead must not draw its own random
    set."

    Parameters
    ----------
    seed : int
        The deterministic subsample-rank seed (default
        :data:`DEFAULT_INVENTORY_SEED`).
    n_trials : int
        The frozen inventory size (default :data:`FROZEN_CANDIDATE_COUNT`
        — the plan-locked 24).

    Returns
    -------
    CandidateInventory
        Deterministic: two calls with the same arguments produce
        byte-identical (equal-digest) inventories, over :func:`hpo_space`
        — the real 324-combination grid.

    Examples
    --------
    Build the plan-locked 24-combination inventory::

        inventory = build_candidate_inventory()
        len(inventory.combinations)
        # -> 24
    """
    return CandidateInventory(hpo_space(), n_trials=n_trials, seed=seed)


def squared_error_improvement(y, yhat, mu) -> float:
    """Return one decision's squared-error improvement of the model over the training-mean baseline.

    ADR-0114 §2: "that lead's equal-stock, training-mean-baseline
    forecast-accuracy contribution ... the same squared-error improvement
    semantics used by the outer model score — not Spearman IC."

    Parameters
    ----------
    y : float
        The realized outcome.
    yhat : float
        The candidate's prediction.
    mu : float
        The training-mean baseline's prediction (constant per fold).

    Returns
    -------
    float
        ``(y - mu) ** 2 - (y - yhat) ** 2`` — positive means the model
        beat the baseline on this one decision.

    Examples
    --------
    A model prediction closer to the outcome than the baseline::

        squared_error_improvement(y=3.0, yhat=2.8, mu=2.0)
        # -> 0.96
    """
    y, yhat, mu = float(y), float(yhat), float(mu)
    return (y - mu) ** 2 - (y - yhat) ** 2


def cluster_scores_by_day(rows) -> dict:
    """Group per-decision squared-error-improvement contributions by trading day — the cluster unit ADR-0114 §11 item 1 rules.

    Parameters
    ----------
    rows : list of mapping
        Each row carries ``day`` (str, the trading-day cluster key),
        ``y`` (float), ``yhat`` (float) and ``mu`` (float), for one lead
        and one HPO candidate.

    Returns
    -------
    dict
        ``{day: [contribution, ...]}``, ready for
        :func:`dskit.pipeline.stats.cluster_bootstrap_t`.

    Raises
    ------
    ValueError
        ``rows`` is empty, or a row is missing ``day``/``y``/``yhat``/
        ``mu``.

    Examples
    --------
    Two decisions on the same day::

        cluster_scores_by_day([
            {"day": "2026-01-05", "y": 1.0, "yhat": 0.9, "mu": 0.0},
            {"day": "2026-01-05", "y": -1.0, "yhat": -0.8, "mu": 0.0},
        ])
        # -> {"2026-01-05": [0.99, 0.96]}
    """
    if not rows:
        raise ValueError("cluster_scores_by_day: rows must be non-empty")
    out = {}
    for i, row in enumerate(rows):
        missing = [k for k in ("day", "y", "yhat", "mu") if k not in row]
        if missing:
            raise ValueError(
                f"cluster_scores_by_day: row {i} is missing {missing!r}"
            )
        out.setdefault(row["day"], []).append(
            squared_error_improvement(row["y"], row["yhat"], row["mu"])
        )
    return out


def boundary_flags(candidate, space=None) -> dict:
    """Report, per HPO dimension, whether ``candidate`` sits on a searched boundary (ADR-0114 §2).

    Parameters
    ----------
    candidate : mapping
        One candidate's overrides (:func:`hpo_space` keys -> a value
        drawn from that key's declared list).
    space : dict, optional
        The grid to check against (default :func:`hpo_space`).

    Returns
    -------
    dict
        ``{dimension: bool}`` — ``True`` when that dimension's value
        equals the declared grid's min or max.

    Examples
    --------
    A candidate at the smallest ``num_leaves`` and a mid ``learning_rate``::

        boundary_flags({
            "num_leaves": 4, "learning_rate": 0.01, "min_child_samples": 1000,
            "reg_lambda": 100.0, "reg_alpha": 0.1,
        })["num_leaves"]
        # -> True
    """
    space = hpo_space() if space is None else space
    return {
        name: candidate[name] in (min(values), max(values))
        for name, values in space.items()
    }


def simplicity_key(row) -> tuple:
    """Return the ADR-0114 §11 item 1 ruled simplicity key for one ledger row's candidate.

    ``(num_leaves, learning_rate, -min_child_samples, -reg_lambda,
    -reg_alpha)``, ascending (lowest wins). Fewer leaves is the primary,
    classic CART one-standard-error
    dimension; the three tie-breaks are negated so ascending order still
    reads "more conservative = simpler" throughout.

    Parameters
    ----------
    row : mapping
        A :class:`~dskit.pipeline.kinds_search.TrialLedger` row (its
        ``"overrides"`` carries the candidate).

    Returns
    -------
    tuple
        The five-element ordering key.

    Examples
    --------
    A row's own simplicity key::

        simplicity_key({"overrides": {
            "num_leaves": 8, "learning_rate": 0.01, "min_child_samples": 1000,
            "reg_lambda": 100.0, "reg_alpha": 0.1,
        }})
        # -> (8, 0.01, -1000, -100.0, -0.1)
    """
    overrides = row["overrides"]
    return (
        overrides["num_leaves"],
        overrides["learning_rate"],
        -overrides["min_child_samples"],
        -overrides["reg_lambda"],
        -overrides["reg_alpha"],
    )


def run_lead_selection(inventory, evaluate, *, n_boot, seed, alpha=0.05):
    """Score every candidate for one lead, independently, and select its frozen winner (ADR-0114 §2/§11.1).

    Builds one fresh :class:`~dskit.pipeline.kinds_search.TrialLedger`
    bound to ``inventory`` (so two leads never share ledger state), calls
    ``evaluate(candidate)`` once per candidate in the inventory's own
    canonical order, computes that candidate's score and standard error
    via :func:`dskit.pipeline.stats.cluster_bootstrap_t` over
    ``evaluate``'s returned ``cluster_scores`` (ADR-0114 §11 item 1: day
    is the cluster unit, the caller supplies per-day contributions), and
    selects the simplest candidate inside one standard error of the best
    via :class:`~dskit.pipeline.kinds_search.OneStandardErrorSelector`
    (``select="max"`` — this score is squared-error improvement, higher
    is better).

    Parameters
    ----------
    inventory : CandidateInventory
        The shared, frozen inventory (:func:`build_candidate_inventory`).
    evaluate : callable
        ``candidate -> mapping``, called once per candidate. The mapping
        must carry ``"cluster_scores"`` (the day -> contributions map for
        :func:`dskit.pipeline.stats.cluster_bootstrap_t`) and MAY carry
        any of :data:`EVIDENCE_FIELDS`' other names (``fit_seed``,
        ``cuts``, ``n_rows``, ``train_val_gap``,
        ``collapsed_prediction_variance``) — an omitted one is NOT
        defaulted here; it reaches ``TrialLedger.record`` unset and that
        ledger's own evidence contract refuses it by name (never
        re-derived in this module).
    n_boot : int
        Bootstrap replicates for :func:`cluster_bootstrap_t`.
    seed : int
        Base bootstrap seed for :func:`cluster_bootstrap_t`.
    alpha : float
        Bootstrap-t interval level (default 0.05).

    Returns
    -------
    tuple
        ``(TrialLedger, SelectionRecord)`` — the complete evidence ledger
        and this lead's frozen selection.

    Raises
    ------
    TypeError
        ``inventory`` is not an exact :class:`CandidateInventory`.
    ValueError
        ``evaluate`` returned something other than a mapping carrying
        ``"cluster_scores"``, or omitted a required evidence field (the
        ledger's own refusal).

    Examples
    --------
    Select over a one-candidate inventory with a canned evaluator::

        inventory = CandidateInventory(
            {"learning_rate": [0.01], "num_leaves": [8],
             "min_child_samples": [1000], "reg_lambda": [100.0],
             "reg_alpha": [0.1]},
        )
        def evaluate(candidate):
            return {
                "cluster_scores": {"2026-01-05": [0.5, 0.3]},
                "fit_seed": 0, "cuts": {"train_end": "2025-11-30"},
                "n_rows": 2, "train_val_gap": 0.01,
                "collapsed_prediction_variance": False,
            }
        ledger, selection = run_lead_selection(
            inventory, evaluate, n_boot=200, seed=0,
        )
        selection.selected_candidate["num_leaves"]
        # -> 8
    """
    if type(inventory) is not CandidateInventory:
        raise TypeError("run_lead_selection: inventory must be an exact CandidateInventory")
    ledger = TrialLedger(inventory, evidence_fields=EVIDENCE_FIELDS)
    for index, candidate in enumerate(inventory.combinations):
        overrides = dict(candidate)
        result = evaluate(overrides)
        if not isinstance(result, Mapping) or "cluster_scores" not in result:
            raise ValueError(
                f"run_lead_selection: evaluate({overrides!r}) must return a "
                "mapping carrying at least 'cluster_scores'"
            )
        diagnostics = cluster_bootstrap_t(
            result["cluster_scores"], n_boot, seed,
            label=f"candidate-{index}", alpha=alpha,
        )
        passthrough = {
            name: result[name]
            for name in ("fit_seed", "cuts", "n_rows", "train_val_gap",
                          "collapsed_prediction_variance")
            if name in result
        }
        ledger.record(
            overrides,
            score=diagnostics["mean"],
            se=diagnostics["se"],
            diagnostics=diagnostics,
            on_boundary=boundary_flags(overrides),
            **passthrough,
        )
    selection = OneStandardErrorSelector(
        select="max", simplicity_key=simplicity_key
    ).select(ledger)
    return ledger, selection


def refit_heads(
    rows_by_head,
    winners,
    *,
    feature_order,
    lean_drop,
    estimator_path="lightgbm.LGBMRegressor",
    seed=0,
    categorical_feature=(),
):
    """Refit all ten heads ONCE, each on only its own frozen winner, on permitted rows through 2026-02-28 — no search, no HPO (ADR-0114 §2).

    Every head shares ``feature_order``, ``lean_drop`` and
    ``categorical_feature`` (the identical feature/category contract);
    only each head's own ``winners[head]`` overrides and its own rows
    differ.

    Parameters
    ----------
    rows_by_head : dict
        ``head -> list of row``, keyed by EXACTLY :data:`HEADS`. Each row
        is a mapping carrying every name in ``feature_order``, a
        ``"label"`` (the fit target) and a ``"ts_ms"`` (epoch ms) this
        function validates against :func:`permitted_for_refit`.
    winners : dict
        ``head -> {param: value}``, keyed by EXACTLY :data:`HEADS` — that
        head's own frozen winner overrides (e.g. a
        ``SelectionRecord.selected_candidate``), forwarded verbatim to
        the wrapped estimator's constructor.
    feature_order : list of str
        The full candidate feature column order every head shares.
    lean_drop : list of str
        The exact P16 lean-mask drop list every head shares
        (:func:`lean_feature_drop`).
    estimator_path : str
        Dotted import path of the wrapped estimator class (default
        ``"lightgbm.LGBMRegressor"`` — this build's real recipe; a test
        double may pass any sklearn-shaped estimator instead).
    seed : int
        Forwarded as ``random_state`` unless a head's own ``winners``
        entry already names one.
    categorical_feature : list of int
        Indices into ``feature_order`` (before masking) naming native
        categorical columns, shared by every head.

    Returns
    -------
    tuple
        ``(estimators, training_identities)`` — ``head -> fitted
        ColumnSubsetEstimator`` and ``head -> JSON-safe identity dict``
        (``cut_ms``, ``seed``, ``n_rows``, ``winner``), the exact shape
        :func:`dskit.pipeline.libs.sklearn.write_bundle`'s
        ``estimators``/``training_identities`` arguments expect.

    Raises
    ------
    ValueError
        ``rows_by_head``/``winners`` are not keyed by exactly
        :data:`HEADS`, a head has zero rows, or any row carries a
        ``ts_ms`` outside the permitted pre-lockbox, post-embargo window.
    """
    import numpy as np

    if set(rows_by_head) != set(HEADS) or set(winners) != set(HEADS):
        raise ValueError(
            f"refit_heads requires rows_by_head and winners keyed by "
            f"exactly {list(HEADS)!r}; got rows "
            f"{sorted(rows_by_head)!r} and winners {sorted(winners)!r}"
        )
    feature_order = list(feature_order)
    categorical_feature = list(categorical_feature) if categorical_feature else None
    estimators = {}
    training_identities = {}
    for head in HEADS:
        rows = rows_by_head[head]
        if not rows:
            raise ValueError(f"refit_heads: head {head!r} has zero rows")
        matrix, targets, stamps = [], [], []
        for row in rows:
            ts_ms = row.get("ts_ms")
            if ts_ms is None or not permitted_for_refit(ts_ms):
                raise ValueError(
                    f"refit_heads: head {head!r} row carries ts_ms={ts_ms!r}, "
                    "outside the permitted pre-lockbox, post-embargo window "
                    f"(lockbox starts {LOCKBOX_START_MS}, embargo "
                    f"[{EMBARGO_START_MS}, {EMBARGO_END_MS}))"
                )
            matrix.append([row[name] for name in feature_order])
            targets.append(row["label"])
            stamps.append(ts_ms)

        params = dict(winners[head])
        params.setdefault("random_state", seed)
        estimator = ColumnSubsetEstimator(
            estimator_path, drop=list(lean_drop), **params
        )
        estimator.fit(
            np.array(matrix, dtype=float),
            np.array(targets, dtype=float),
            feature_names=feature_order,
            categorical_feature=categorical_feature,
        )
        estimators[head] = estimator
        training_identities[head] = {
            "cut_ms": max(stamps),
            "seed": seed,
            "n_rows": len(rows),
            "winner": dict(winners[head]),
        }
    return estimators, training_identities
