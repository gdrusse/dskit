"""``forecast_bundle`` — the point-in-time MIO forecast bundle (Gate 4).

The P16 label ``y(t, h) = [r_i(t, t+h) - beta_i,t * r_SPY(t, t+h)] /
(sigma_i,t * sqrt(h))`` is a vol-scaled SPY residual (ADR-0059's
:class:`intraday_equities.nodes._LeadLabel`); the capital step needs gross
fractional returns. The owner's §11 item 3 ruling (2026-09-10, carried in
``docs/plans/2026-09-10-gate4-forecast-bundle-kickoff.md`` and ADR-0121)
fixes the inverse: assume a **zero-drift market reference**,
``E[r_SPY(t, t+h)] ~= 0`` over the model's short forecast horizons. The
label uses log returns, so ``yhat * sigma_i,t * sqrt(h)`` first recovers a
log return and ``expm1`` converts it to the capital boundary's simple
fractional-return unit. The SAME causal ``sigma_i,t`` the label used at
training/prediction time is read from the model bundle's manifest / the
same feature pipeline, never recomputed with different parameters. No
SPY-forecast model is authorized by that ruling, and none exists here.

This module owns the child-specific half only: the unit conversion, the
fail-closed assembly/validation of the ADR-0088/MIO bundle, and the pinned
confirmed-cap artifact contract. It calibrates nothing — ``pi_upper`` and
the joint scenario rows arrive from upstream evidence that §11 item 4 has
not unruled, so this file VALIDATES caller-supplied values and never
derives them. Generic calibration estimators, when a ruling ever authorizes
them, graduate to ``dskit``; nothing here is generic.

Nothing in this module reads market data or the March-May confirmation
slice (``configs/run-mean-confirmation.json`` stays unreadable per plan
§8 / §11 item 4, independent of the item 3 ruling).
"""

from __future__ import annotations

import hashlib
import json
import math

from dskit.pipeline.records import number_ok
from dskit.pipeline.stages import is_sha256hex

from .final_gates import DEVELOPMENT_EVIDENCE_SCOPE
from .final_model import HEADS
from .nodes import (
    DEFAULT_BETA_WINDOW_MINUTES,
    DEFAULT_VOL_FLOOR,
    DEFAULT_VOL_WINDOW_MINUTES,
    LABEL_RETURN_BASIS,
)

__all__ = [
    "BUNDLE_UNIT",
    "DEFAULT_REFERENCE",
    "ForecastBundle",
    "ConfirmedCaps",
    "KNOWN_AT_FIELDS",
    "LABEL_CONTRACT_FIELDS",
    "ZERO_DRIFT",
    "default_label_contract",
    "gross_return",
]

#: The ruled market-reference policy (§11 item 3, 2026-09-10): the expected
#: SPY return over the model's short forecast horizons is zero, so no
#: forward reference return enters the conversion. This is the ONLY
#: reference policy the ruling authorizes — a different one needs a new
#: owner ruling and a new ADR, never a code path added beside this name.
ZERO_DRIFT = "zero-drift"

#: The reference symbol the training label residualized against. A domain
#: binding (tier 3): the reference is config data in the run documents, and
#: this default is the pinned training label's own value.
DEFAULT_REFERENCE = "SPY"

#: The unit every assembled bundle row carries. The capital node requires
#: exactly this value on every bundle row it sizes — a vol-scaled
#: SPY-residual prediction (label units) must never be accepted as a gross
#: return (plan §6 Phase 4 item 1).
BUNDLE_UNIT = "gross_fractional_return"

#: The label-contract vocabulary: the exact ``nodes.py`` label knobs that
#: define the ``sigma`` the ruling names (``nodes.LABEL_PARAMS`` minus the
#: degenerate ``label_residual_self`` special case, which the training
#: label does not declare). Pinned against ``LABEL_PARAMS`` by test.
LABEL_CONTRACT_FIELDS = (
    "label_scale",
    "label_residual",
    "return_basis",
    "vol_window_minutes",
    "beta_window_minutes",
    "vol_floor",
)

#: The closed point-in-time vocabulary: every dependency whose known-at
#: stamp an input row must supply (plan §6 Phase 4 item 2). Anything else
#: — notably an ``outcome``, future information under the zero-drift
#: ruling — is refused by name, not read.
KNOWN_AT_FIELDS = (
    "sigma",
    "beta",
    "reference",
    "price",
    "yhat",
    "pi_hat",
    "pi_upper",
    "scenarios",
)

#: The exact field set an input row may carry — default-deny, so a caller
#: cannot smuggle a precomputed gross value (``mu_gross``) or a
#: self-declared ``unit`` past the conversion (plan §6 Phase 4 item 1).
_INPUT_FIELDS = frozenset(
    (
        "entity",
        "decision_ts",
        "lead",
        "price",
        "yhat",
        "sigma_t",
        "beta_t",
        "pi_hat",
        "pi_upper",
        "weights",
        "scenarios",
        "label",
        "known_at",
    )
)

#: How far a weights list may miss summing to exactly 1 before it refuses —
#: the same tolerance the capital node's own bundle validator applies; the
#: two are pinned to agree by ``test_forecast_bundle``.
_WEIGHTS_SUM_TOLERANCE = 1e-8


def default_label_contract():
    """Return the pinned training-label contract the ruling converts through.

    Built from ``nodes.py``'s own default knob names — the one real source,
    never a restated copy — so the bundle's conversion can never drift from
    the label that produced its ``sigma`` values.

    Returns
    -------
    dict
        ``label_scale`` ``"vol"``, ``label_residual`` ``"SPY"``,
        ``return_basis`` ``"log"``, ``vol_window_minutes`` 390,
        ``beta_window_minutes`` 3900,
        ``vol_floor`` 1e-8.

    Examples
    --------
    Read the pinned contract::

        contract = default_label_contract()
        contract["vol_window_minutes"]
        # -> 390
    """
    return {
        "label_scale": "vol",
        "label_residual": DEFAULT_REFERENCE,
        "return_basis": LABEL_RETURN_BASIS,
        "vol_window_minutes": DEFAULT_VOL_WINDOW_MINUTES,
        "beta_window_minutes": DEFAULT_BETA_WINDOW_MINUTES,
        "vol_floor": DEFAULT_VOL_FLOOR,
    }


def gross_return(yhat, sigma, lead):
    """Convert one label-unit log prediction to a simple fractional return.

    The ruled inverse first recovers the log return
    ``z = yhat_i(t, h) * sigma_i,t * sqrt(h)`` under the
    zero-drift market reference ``E[r_SPY(t, t+h)] ~= 0``: the beta-hedge
    term's expectation vanishes, and the SAME causal ``sigma_i, t`` the
    label divided by multiplies straight back. Because ``_LeadLabel`` used
    ``log(P1/P0)``, the capital-facing simple return is ``expm1(z)``.

    Parameters
    ----------
    yhat : float
        The head's label-unit prediction (vol-scaled SPY-residual units).
    sigma : float
        The causal trailing residual std the label used at this row —
        supplied point-in-time by the caller, never recomputed here.
    lead : int
        The holding horizon in bars (``h`` in the label formula), 1..10.

    Returns
    -------
    float
        ``expm1(yhat * sigma * sqrt(lead))`` — a simple fractional return.

    Raises
    ------
    ValueError
        ``yhat`` or ``sigma`` is not a finite number, ``sigma`` is not
        strictly positive, or ``lead`` is not an integer >= 1.

    Examples
    --------
    One ruled conversion::

        gross_return(1.5, 0.0008, 4)
        # -> approximately 0.00240288
    """
    if not number_ok(yhat):
        raise ValueError(f"gross_return: yhat must be a finite number, got {yhat!r}")
    if not number_ok(sigma) or sigma <= 0.0:
        raise ValueError(
            f"gross_return: sigma must be a finite number > 0, got {sigma!r}"
        )
    if isinstance(lead, bool) or not isinstance(lead, int) or lead < 1:
        raise ValueError(f"gross_return: lead must be an int >= 1, got {lead!r}")
    log_return = float(yhat) * float(sigma) * math.sqrt(lead)
    try:
        simple_return = math.expm1(log_return)
    except OverflowError as exc:
        raise ValueError("gross_return: recovered log return is too large") from exc
    if not math.isfinite(simple_return):
        raise ValueError("gross_return: recovered simple return must be finite")
    return simple_return


def _row_problems(index, row, label_contract):
    """Problems with one input row, empty when none — everything by name."""
    where = f"row {index}"
    if not isinstance(row, dict):
        return [f"{where}: must be a mapping, got {type(row).__name__}"]
    problems = []
    extra = sorted(set(row) - _INPUT_FIELDS)
    if extra:
        problems.append(
            f"{where}: unknown input field(s) {extra} — an assembled row is "
            "converted from label units here, never handed a precomputed "
            "gross value or a self-declared unit (plan §6 Phase 4 item 1)"
        )
    missing = sorted(_INPUT_FIELDS - set(row))
    if missing:
        problems.append(f"{where}: missing required field(s) {missing}")
        return problems
    entity = row["entity"]
    if not isinstance(entity, str) or not entity:
        problems.append(f"{where}: entity must be a non-empty string, got {entity!r}")
    decision_ts = row["decision_ts"]
    if isinstance(decision_ts, bool) or not isinstance(decision_ts, int) or decision_ts < 0:
        problems.append(
            f"{where} ({entity!r}): decision_ts must be an integer epoch ms >= 0"
        )
    lead = row["lead"]
    if isinstance(lead, bool) or not isinstance(lead, int) or not 1 <= lead <= len(HEADS):
        problems.append(
            f"{where} ({entity!r}): lead must be an integer 1..{len(HEADS)}, "
            f"got {lead!r}"
        )
    if not number_ok(row["price"]) or row["price"] <= 0.0:
        problems.append(
            f"{where} ({entity!r}): price must be a finite number > 0"
        )
    if not number_ok(row["yhat"]):
        problems.append(
            f"{where} ({entity!r}): yhat must be a finite number (label units)"
        )
    sigma = row["sigma_t"]
    vol_floor = float(label_contract["vol_floor"])
    if not number_ok(sigma) or sigma <= vol_floor:
        problems.append(
            f"{where} ({entity!r}): sigma_t must be a finite number above the "
            f"pinned label contract's vol_floor {vol_floor!r}, got {sigma!r} — "
            "the label itself refuses such rows, so its inverse must too"
        )
    if not number_ok(row["beta_t"]):
        problems.append(
            f"{where} ({entity!r}): beta_t must be a finite number"
        )
    pi_hat = row["pi_hat"]
    pi_upper = row["pi_upper"]
    if not number_ok(pi_hat) or not 0.0 <= pi_hat <= 1.0:
        problems.append(
            f"{where} ({entity!r}): pi_hat must be a finite number in [0, 1]"
        )
    if not number_ok(pi_upper) or not 0.0 <= pi_upper <= 1.0:
        problems.append(
            f"{where} ({entity!r}): pi_upper must be a finite number in [0, 1]"
        )
    elif number_ok(pi_hat) and pi_hat > pi_upper:
        problems.append(
            f"{where} ({entity!r}): pi_hat {pi_hat!r} must not exceed the "
            f"conservative pi_upper {pi_upper!r}"
        )
    if row["label"] != label_contract:
        problems.append(
            f"{where} ({entity!r}): label contract {row['label']!r} differs "
            f"from the pinned contract {label_contract!r} — sigma may never "
            "be recomputed with different parameters (§11 item 3)"
        )
    weights = row["weights"]
    if not isinstance(weights, (list, tuple)) or not weights:
        problems.append(f"{where} ({entity!r}): weights must be a non-empty list")
    elif not all(number_ok(w) and w >= 0.0 for w in weights):
        problems.append(
            f"{where} ({entity!r}): weights must be all finite numbers >= 0"
        )
    elif abs(sum(float(w) for w in weights) - 1.0) > _WEIGHTS_SUM_TOLERANCE:
        problems.append(
            f"{where} ({entity!r}): weights must sum to 1, got "
            f"{sum(float(w) for w in weights)!r}"
        )
    scenarios = row["scenarios"]
    if not isinstance(scenarios, (list, tuple)):
        problems.append(f"{where} ({entity!r}): scenarios must be a list")
    else:
        if isinstance(weights, (list, tuple)) and len(scenarios) != len(weights):
            problems.append(
                f"{where} ({entity!r}): scenarios length {len(scenarios)} != "
                f"weights length {len(weights)}"
            )
        if not all(number_ok(v) for v in scenarios):
            problems.append(
                f"{where} ({entity!r}): scenarios must be all finite numbers "
                "(label-unit residuals)"
            )
    known_at = row["known_at"]
    if not isinstance(known_at, dict):
        problems.append(
            f"{where} ({entity!r}): known_at must be a mapping of "
            f"{list(KNOWN_AT_FIELDS)!r}"
        )
    else:
        unknown = sorted(set(known_at) - set(KNOWN_AT_FIELDS))
        if unknown:
            problems.append(
                f"{where} ({entity!r}): unknown known_at key(s) {unknown} — "
                "the closed vocabulary is the dependency list; an outcome is "
                "future information and may never enter this bundle"
            )
        missing_stamps = sorted(set(KNOWN_AT_FIELDS) - set(known_at))
        if missing_stamps:
            problems.append(
                f"{where} ({entity!r}): known_at is missing {missing_stamps}"
            )
        if number_ok(decision_ts):
            for key, stamp in sorted(known_at.items()):
                if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp < 0:
                    problems.append(
                        f"{where} ({entity!r}): known_at[{key!r}] must be a "
                        f"integer epoch-ms value >= 0, got {stamp!r}"
                    )
                elif stamp > decision_ts:
                    problems.append(
                        f"{where} ({entity!r}): known_at[{key!r}] "
                        f"({stamp!r}) is after decision_ts — point-in-time "
                        "inputs only (plan §6 Phase 4 item 2)"
                    )
    return problems


class ForecastBundle:
    """One decision tick's assembled, unit-verified MIO forecast bundle.

    Validates caller-supplied point-in-time rows, applies the §11 item 3
    ruled inverse (:func:`gross_return`) to the mean prediction and to each
    ``prediction + scenario residual`` payoff. ``mu_gross`` is the plug-in
    mean ``expm1(E[log return])`` with no Jensen correction. After nonlinear
    conversion, the finite scenario set is shifted to weighted mean
    ``(1 - pi_hat) * mu_gross``; that explicit false-signal haircut target
    deliberately overrides the raw finite-scenario Jensen mean. Emits
    gross-unit rows in exactly the shape
    :class:`~intraday_equities.nodes_capital.EquityKellyMIO` consumes.
    Fail-closed: every problem is named, and any one of them refuses the
    whole tick.

    Parameters
    ----------
    release_id : str
        The model release identity every row of this bundle shares (the
        confirmed cap artifact pins the same value).
    rows : list of dict
        A materialized list of per-candidate rows for ONE decision tick.
        Each row carries exactly: ``entity`` (non-empty str, unique),
        ``decision_ts`` (epoch ms), ``lead`` (int 1..10, shared),
        ``price`` (> 0), ``yhat`` (label units), ``sigma_t`` (the SAME
        causal trailing residual std the label used — above the pinned
        contract's ``vol_floor``), ``beta_t`` (finite), ``pi_hat``
        (point false-signal prevalence in [0, 1]), ``pi_upper``
        (conservative prevalence bound in [pi_hat, 1]), ``weights`` (non-empty, finite >= 0, summing to 1,
        shared), ``scenarios`` (label-unit residuals, one per weight),
        ``label`` (the contract that produced ``sigma_t`` — must equal the
        pinned contract), and ``known_at`` (a map keyed EXACTLY by
        ``('sigma', 'beta', 'reference', 'price', 'yhat', 'pi_hat',
        'pi_upper', 'scenarios')``, every stamp at or before
        ``decision_ts``). All time values are integer epoch milliseconds.
        An empty list is the empty gate and assembles to an empty bundle.
    producer : dict
        Exact producer identity: lowercase ``document_sha256``, non-empty
        ``node``, and ``output="bundle"``.
    model_manifest_sha256 : str
        Lowercase SHA-256 of the release manifest that binds label/model
        semantics.
    label_contract : dict, optional
        The pinned label contract ``sigma_t`` was computed under (default
        :func:`default_label_contract` — the training label's own knobs,
        read from ``nodes.py``'s names).

    Raises
    ------
    ValueError
        Any row or cross-row problem, joined and named — including mixed
        leads or mismatched scenario sets across the tick (plan §6 Phase 4
        item 3) and a non-list ``rows`` argument.

    Examples
    --------
    Assemble one two-name tick at lead 3::

        bundle = ForecastBundle("rel-abc", [
            {
                "entity": "AAPL", "decision_ts": 1_700_000_000_000, "lead": 3,
                "price": 190.0, "yhat": 0.5, "sigma_t": 0.0012, "beta_t": 1.1,
                "pi_hat": 0.1, "pi_upper": 0.2, "weights": [0.5, 0.5],
                "scenarios": [-0.4, 0.9],
                "label": default_label_contract(),
                "known_at": {"sigma": 1_699_999_939_000, "beta": 1_699_999_939_000,
                              "reference": 1_699_999_939_000, "price": 1_699_999_939_000,
                              "yhat": 1_699_999_939_000, "pi_hat": 1_699_999_500_000,
                              "pi_upper": 1_699_999_500_000,
                              "scenarios": 1_699_999_939_000},
            },
        ], producer={"document_sha256": "a" * 64,
                     "node": "forecast", "output": "bundle"},
           model_manifest_sha256="b" * 64)
        bundle.rows[0]["mu_gross"] == math.expm1(0.5 * 0.0012 * 3 ** 0.5)
        # -> True
    """

    def __init__(
        self,
        release_id,
        rows,
        *,
        producer=None,
        model_manifest_sha256=None,
        label_contract=None,
    ):
        self.release_id = release_id
        self.label_contract = default_label_contract()
        self.reference_policy = ZERO_DRIFT
        problems = []
        if label_contract is not None and label_contract != self.label_contract:
            problems.append(
                f"label_contract {label_contract!r} differs from the pinned "
                f"training label contract {self.label_contract!r} — callers "
                "cannot redefine the release's label semantics"
            )
        if not isinstance(release_id, str) or not release_id:
            problems.append(
                f"release_id must be a non-empty string, got {release_id!r}"
            )
        producer_fields = {"document_sha256", "node", "output"}
        if not isinstance(producer, dict) or set(producer) != producer_fields:
            problems.append(
                f"producer must carry exactly {sorted(producer_fields)!r}"
            )
        else:
            if not is_sha256hex(producer["document_sha256"]):
                problems.append("producer.document_sha256 must be lowercase SHA-256")
            if not isinstance(producer["node"], str) or not producer["node"]:
                problems.append("producer.node must be a non-empty string")
            if producer["output"] != "bundle":
                problems.append("producer.output must be 'bundle'")
        if not is_sha256hex(model_manifest_sha256):
            problems.append("model_manifest_sha256 must be a lowercase SHA-256")
        if not isinstance(rows, (list, tuple)):
            problems.append(
                f"rows must be a materialized list of candidate rows, got "
                f"{type(rows).__name__} — a one-shot iterable is refused by "
                "name rather than walked"
            )
            rows = []
        seen = set()
        shared = {}
        for index, row in enumerate(rows):
            for problem in _row_problems(index, row, self.label_contract):
                problems.append(problem)
            if not isinstance(row, dict):
                continue
            entity = row.get("entity")
            if isinstance(entity, str) and entity:
                if entity in seen:
                    problems.append(
                        f"row {index}: duplicate entity {entity!r} in one bundle"
                    )
                else:
                    seen.add(entity)
            for field in ("decision_ts", "lead", "weights"):
                if field not in row:
                    continue
                if field not in shared:
                    shared[field] = row[field]
                elif row[field] != shared[field]:
                    problems.append(
                        f"row {index} ({entity!r}) carries {field} "
                        f"{row[field]!r} where the tick's shared value is "
                        f"{shared[field]!r} — one joint scenario set per "
                        "decision tick: mixed horizons or mismatched "
                        "scenario sets refuse (plan §6 Phase 4 item 3)"
                    )
        if problems:
            raise ValueError("ForecastBundle: " + "; ".join(problems))
        self.decision_ts = shared.get("decision_ts")
        self.lead = shared.get("lead")
        self.weights = list(shared["weights"]) if "weights" in shared else None
        self.producer = dict(producer)
        self.model_manifest_sha256 = model_manifest_sha256
        self.rows = [self._assemble(row) for row in rows]

    @classmethod
    def digest(cls, rows):
        """Return the canonical SHA-256 of an assembled row list."""
        try:
            raw = json.dumps(
                rows,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii")
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise ValueError("bundle rows are not canonical JSON") from exc
        return hashlib.sha256(raw).hexdigest()

    def _assemble(self, row):
        """Convert one validated input row into its gross-unit output row."""
        sigma = float(row["sigma_t"])
        lead = row["lead"]
        mu_gross = gross_return(row["yhat"], sigma, lead)
        return {
            "entity": row["entity"],
            "decision_ts": row["decision_ts"],
            "lead": lead,
            "model_release_id": self.release_id,
            "unit": BUNDLE_UNIT,
            "price": float(row["price"]),
            "pi_hat": float(row["pi_hat"]),
            "pi_upper": float(row["pi_upper"]),
            "weights": list(row["weights"]),
            "scenarios": self._recentered_scenarios(row, mu_gross),
            "mu_gross": mu_gross,
            "reference_policy": self.reference_policy,
            "label": dict(self.label_contract),
            "model_manifest_sha256": self.model_manifest_sha256,
            "producer": dict(self.producer),
            "known_at": dict(row["known_at"]),
        }

    @staticmethod
    def _recentered_scenarios(row, mu_gross):
        """Convert log residual draws, then pin their weighted simple-return mean."""
        sigma = float(row["sigma_t"])
        lead = row["lead"]
        converted = [
            gross_return(row["yhat"] + residual, sigma, lead)
            for residual in row["scenarios"]
        ]
        observed_mean = sum(
            float(weight) * value
            for weight, value in zip(row["weights"], converted)
        )
        target_mean = (1.0 - float(row["pi_hat"])) * mu_gross
        recentered = [value - observed_mean + target_mean for value in converted]
        if any(value <= -1.0 or not math.isfinite(value) for value in recentered):
            raise ValueError(
                "ForecastBundle: recentered simple-return scenarios must be "
                "finite and greater than -1"
            )
        return recentered


_CAP_SCHEMA_FIELDS = frozenset(
    (
        "schema_version",
        "model_release_id",
        "deployment_eligible",
        "evidence_scope",
        "evidence_end_ms",
        "generated_ms",
        "producer",
        "evidence",
        "caps",
    )
)

#: The one schema version a confirmed-cap artifact may declare.
_CAP_SCHEMA_VERSION = 2


class ConfirmedCaps:
    """A hash-addressable ``(symbol, lead)`` confirmation-cap artifact.

    Phase 4's structural cap contract (ADR-0121): confirmation caps are
    contiguous from h1, bind a producer and evidence digest, and come from
    evidence not used to choose the P16 mask. This value class validates
    structure and computes the canonical digest; the capital node compares
    that digest and the producer/evidence identities with trusted config
    pins and enforces development versus deployment mode. No trusted real
    producer exists yet, so deployment mode fails closed regardless of an
    artifact's self-declared eligibility.

    Parameters
    ----------
    artifact : dict
        Exactly: ``schema_version`` (2), ``model_release_id`` (non-empty
        str), ``deployment_eligible`` (JSON boolean), ``evidence_scope``
        (non-empty str, NOT this child's P16 development scope),
        ``evidence_end_ms`` (epoch ms, strictly before ``generated_ms`` —
        every confirming outcome realized before the artifact was pinned),
        ``generated_ms`` (integer epoch ms), ``producer`` (exactly a
        lowercase document SHA-256, node, and ``output="cap"``),
        ``evidence`` (exactly a lowercase SHA-256 plus scope/end bindings
        equal to the top-level declarations), and ``caps`` — a list of unique
        ``{"symbol": str, "capped_horizon": int}`` rows, ``capped_horizon``
        in 0..10. The integer IS the contiguous-from-h1 encoding: a cap of
        N confirms leads h1..hN; 0 confirms nothing (the capital node
        routes that symbol's rows out).

    Raises
    ------
    ValueError
        Any artifact problem, joined and named.

    Examples
    --------
    Validate one structurally sound nonproduction cap artifact::

        caps = ConfirmedCaps({
            "schema_version": 2, "model_release_id": "rel-abc",
            "deployment_eligible": False,
            "evidence_scope": "synthetic_demo",
            "evidence_end_ms": 1_699_990_000_000,
            "generated_ms": 1_699_999_000_000,
            "producer": {"document_sha256": "a" * 64,
                         "node": "source", "output": "cap"},
            "evidence": {"sha256": "b" * 64, "scope": "synthetic_demo",
                         "end_ms": 1_699_990_000_000},
            "caps": [{"symbol": "AAPL", "capped_horizon": 4}],
        })
        caps.allows("AAPL", 4)
        # -> True
        caps.allows("AAPL", 5)
        # -> False
    """

    def __init__(self, artifact):
        problems = self.problems(artifact)
        if problems:
            raise ValueError("ConfirmedCaps: " + "; ".join(problems))
        self.model_release_id = artifact["model_release_id"]
        self.deployment_eligible = artifact["deployment_eligible"]
        self.evidence_scope = artifact["evidence_scope"]
        self.evidence_end_ms = artifact["evidence_end_ms"]
        self.generated_ms = artifact["generated_ms"]
        self.producer = dict(artifact["producer"])
        self.evidence = dict(artifact["evidence"])
        self.caps = {
            row["symbol"]: row["capped_horizon"] for row in artifact["caps"]
        }

    @classmethod
    def digest(cls, artifact):
        """Return the canonical SHA-256 a trusted config must pin."""
        try:
            raw = json.dumps(
                artifact,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii")
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise ValueError("cap artifact is not canonical JSON") from exc
        return hashlib.sha256(raw).hexdigest()

    @classmethod
    def problems(cls, artifact):
        """Problems with a declared cap ``artifact``, empty when none.

        Parameters
        ----------
        artifact : dict
            The cap artifact to validate (see the class docstring for the
            exact field contract).

        Returns
        -------
        list of str
            Every problem, named — so a node's ``validate_inputs`` can
            accumulate them with its own rather than catch an exception.
        """
        if not isinstance(artifact, dict):
            return [
                f"cap must be a mapping (the pinned confirmed-cap "
                f"artifact), got {type(artifact).__name__}"
            ]
        problems = []
        unknown = sorted(set(artifact) - _CAP_SCHEMA_FIELDS)
        if unknown:
            problems.append(f"cap carries unknown field(s) {unknown}")
        missing = sorted(_CAP_SCHEMA_FIELDS - set(artifact))
        if missing:
            problems.append(f"cap is missing required field(s) {missing}")
            return problems
        if artifact["schema_version"] != _CAP_SCHEMA_VERSION:
            problems.append(
                f"cap.schema_version must be {_CAP_SCHEMA_VERSION}, got "
                f"{artifact['schema_version']!r}"
            )
        release = artifact["model_release_id"]
        if not isinstance(release, str) or not release:
            problems.append(
                f"cap.model_release_id must be a non-empty string, got {release!r}"
            )
        if not isinstance(artifact["deployment_eligible"], bool):
            problems.append(
                "cap.deployment_eligible must be a JSON boolean"
            )
        scope = artifact["evidence_scope"]
        if not isinstance(scope, str) or not scope:
            problems.append(
                f"cap.evidence_scope must be a non-empty string, got {scope!r}"
            )
        elif scope == DEVELOPMENT_EVIDENCE_SCOPE:
            problems.append(
                "cap.evidence_scope is the P16 development scope "
                f"{DEVELOPMENT_EVIDENCE_SCOPE!r} — confirmation caps must "
                "come from evidence not used to choose the P16 mask"
            )
        evidence_end = artifact["evidence_end_ms"]
        generated = artifact["generated_ms"]
        for name, value in (("evidence_end_ms", evidence_end), ("generated_ms", generated)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                problems.append(
                    f"cap.{name} must be an integer epoch-ms value >= 0, got "
                    f"{value!r}"
                )
        if (
            number_ok(evidence_end)
            and number_ok(generated)
            and evidence_end >= generated
        ):
            problems.append(
                f"cap.evidence_end_ms ({evidence_end!r}) must be strictly "
                f"before cap.generated_ms ({generated!r}) — every "
                "confirming outcome realizes before the artifact is pinned"
            )
        producer = artifact["producer"]
        producer_fields = {"document_sha256", "node", "output"}
        if not isinstance(producer, dict) or set(producer) != producer_fields:
            problems.append(
                f"cap.producer must carry exactly {sorted(producer_fields)!r}"
            )
        else:
            if not is_sha256hex(producer["document_sha256"]):
                problems.append("cap.producer.document_sha256 must be lowercase SHA-256")
            if not isinstance(producer["node"], str) or not producer["node"]:
                problems.append("cap.producer.node must be a non-empty string")
            if producer["output"] != "cap":
                problems.append("cap.producer.output must be 'cap'")
        evidence = artifact["evidence"]
        evidence_fields = {"sha256", "scope", "end_ms"}
        if not isinstance(evidence, dict) or set(evidence) != evidence_fields:
            problems.append(
                f"cap.evidence must carry exactly {sorted(evidence_fields)!r}"
            )
        else:
            if not is_sha256hex(evidence["sha256"]):
                problems.append("cap.evidence.sha256 must be lowercase SHA-256")
            if evidence["scope"] != scope:
                problems.append("cap.evidence.scope must equal cap.evidence_scope")
            if evidence["end_ms"] != evidence_end:
                problems.append("cap.evidence.end_ms must equal cap.evidence_end_ms")
        caps = artifact["caps"]
        if not isinstance(caps, (list, tuple)) or not caps:
            problems.append(
                "cap.caps must be a non-empty list of "
                "{'symbol', 'capped_horizon'} rows"
            )
            return problems
        seen = set()
        for index, row in enumerate(caps):
            if not isinstance(row, dict) or set(row) != {"symbol", "capped_horizon"}:
                problems.append(
                    f"cap.caps[{index}] must carry exactly "
                    f"{{'symbol', 'capped_horizon'}}, got {row!r}"
                )
                continue
            symbol = row["symbol"]
            if not isinstance(symbol, str) or not symbol:
                problems.append(
                    f"cap.caps[{index}].symbol must be a non-empty string, "
                    f"got {symbol!r}"
                )
            elif symbol in seen:
                problems.append(f"cap.caps[{index}]: duplicate symbol {symbol!r}")
            else:
                seen.add(symbol)
            horizon = row["capped_horizon"]
            if (
                isinstance(horizon, bool)
                or not isinstance(horizon, int)
                or not 0 <= horizon <= len(HEADS)
            ):
                problems.append(
                    f"cap.caps[{index}] ({symbol!r}): capped_horizon must be "
                    f"an integer 0..{len(HEADS)} — the contiguous-from-h1 "
                    f"encoding (cap N confirms h1..hN), got {horizon!r}"
                )
        return problems

    def capped_horizon(self, symbol):
        """Return ``symbol``'s confirmed max lead, or ``None`` when absent.

        Parameters
        ----------
        symbol : str
            The bundle row's entity name.

        Returns
        -------
        int or None
            The ``capped_horizon`` this artifact pins for ``symbol``;
            ``None`` when the artifact carries no entry for it.
        """
        return self.caps.get(symbol)

    def allows(self, symbol, lead):
        """Report whether the confirmed cap covers ``(symbol, lead)``.

        Parameters
        ----------
        symbol : str
            The bundle row's entity name.
        lead : int
            The bundle row's lead (bars).

        Returns
        -------
        bool
            ``True`` only when an entry exists and ``1 <= lead <=
            capped_horizon`` — a zero cap or a lead above the confirmed
            run both refuse.
        """
        horizon = self.caps.get(symbol)
        return horizon is not None and 1 <= lead <= horizon
