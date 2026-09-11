"""``forecast_bundle`` — the point-in-time MIO forecast bundle (Gate 4).

The P16 label ``y(t, h) = [r_i(t, t+h) - beta_i,t * r_SPY(t, t+h)] /
(sigma_i,t * sqrt(h))`` is a vol-scaled SPY residual (ADR-0059's
:class:`intraday_equities.nodes._LeadLabel`); the capital step needs gross
fractional returns. The owner's §11 item 3 ruling (2026-09-10, carried in
``docs/plans/2026-09-10-gate4-forecast-bundle-kickoff.md`` and ADR-0121)
fixes the inverse: assume a **zero-drift market reference**,
``E[r_SPY(t, t+h)] ~= 0`` over the model's short forecast horizons, so the
point-in-time gross-return forecast is ``yhat * sigma_i,t * sqrt(h)`` using
the SAME causal ``sigma_i,t`` the label used at training/prediction time —
read from the model bundle's manifest / the same feature pipeline, never
recomputed with different parameters. No SPY-forecast model is authorized
by that ruling, and none exists here.

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

import math

from dskit.pipeline.records import number_ok

from .final_gates import DEVELOPMENT_EVIDENCE_SCOPE
from .final_model import HEADS
from .nodes import (
    DEFAULT_BETA_WINDOW_MINUTES,
    DEFAULT_VOL_FLOOR,
    DEFAULT_VOL_WINDOW_MINUTES,
)

__all__ = [
    "BUNDLE_UNIT",
    "DEFAULT_REFERENCE",
    "ForecastBundle",
    "ConfirmedCaps",
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
        ``vol_window_minutes`` 390, ``beta_window_minutes`` 3900,
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
        "vol_window_minutes": DEFAULT_VOL_WINDOW_MINUTES,
        "beta_window_minutes": DEFAULT_BETA_WINDOW_MINUTES,
        "vol_floor": DEFAULT_VOL_FLOOR,
    }


def gross_return(yhat, sigma, lead):
    """Convert one label-unit prediction to a gross fractional return — the §11 item 3 ruled inverse.

    ``gross_return_i(t, h) = yhat_i(t, h) * sigma_i, t * sqrt(h)`` under the
    zero-drift market reference ``E[r_SPY(t, t+h)] ~= 0``: the beta-hedge
    term's expectation vanishes, and the SAME causal ``sigma_i, t`` the
    label divided by multiplies straight back.

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
        ``yhat * sigma * sqrt(lead)`` — a gross fractional return over the
        lead.

    Raises
    ------
    ValueError
        ``yhat`` or ``sigma`` is not a finite number, ``sigma`` is not
        strictly positive, or ``lead`` is not an integer >= 1.

    Examples
    --------
    One ruled conversion::

        gross_return(1.5, 0.0008, 4)
        # -> 0.0024
    """
    if not number_ok(yhat):
        raise ValueError(f"gross_return: yhat must be a finite number, got {yhat!r}")
    if not number_ok(sigma) or sigma <= 0.0:
        raise ValueError(
            f"gross_return: sigma must be a finite number > 0, got {sigma!r}"
        )
    if isinstance(lead, bool) or not isinstance(lead, int) or lead < 1:
        raise ValueError(f"gross_return: lead must be an int >= 1, got {lead!r}")
    return float(yhat) * float(sigma) * math.sqrt(lead)


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
    if not number_ok(decision_ts):
        problems.append(
            f"{where} ({entity!r}): decision_ts must be a finite number (epoch ms)"
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
    if not number_ok(row["pi_upper"]) or not 0.0 <= row["pi_upper"] <= 1.0:
        problems.append(
            f"{where} ({entity!r}): pi_upper must be a finite number in [0, 1]"
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
                if not number_ok(stamp) or stamp < 0:
                    problems.append(
                        f"{where} ({entity!r}): known_at[{key!r}] must be a "
                        f"finite epoch-ms number, got {stamp!r}"
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
    ``prediction + scenario residual`` payoff, and emits gross-unit rows in
    exactly the shape
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
        contract's ``vol_floor``), ``beta_t`` (finite), ``pi_upper``
        (in [0, 1]), ``weights`` (non-empty, finite >= 0, summing to 1,
        shared), ``scenarios`` (label-unit residuals, one per weight),
        ``label`` (the contract that produced ``sigma_t`` — must equal the
        pinned contract), and ``known_at`` (a map keyed EXACTLY by
        ``('sigma', 'beta', 'reference', 'price', 'yhat', 'pi_upper',
        'scenarios')``, every stamp at or before ``decision_ts``).
        An empty list is the empty gate and assembles to an empty bundle.
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
                "pi_upper": 0.2, "weights": [0.5, 0.5],
                "scenarios": [-0.4, 0.9],
                "label": default_label_contract(),
                "known_at": {"sigma": 1_699_999_939_000, "beta": 1_699_999_939_000,
                              "reference": 1_699_999_939_000, "price": 1_699_999_939_000,
                              "yhat": 1_699_999_939_000, "pi_upper": 1_699_999_500_000,
                              "scenarios": 1_699_999_939_000},
            },
        ])
        bundle.rows[0]["mu_gross"] == 0.5 * 0.0012 * 3 ** 0.5
        # -> True
    """

    def __init__(self, release_id, rows, label_contract=None):
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
        self.rows = [self._assemble(row) for row in rows]

    def _assemble(self, row):
        """Convert one validated input row into its gross-unit output row."""
        sigma = float(row["sigma_t"])
        lead = row["lead"]
        return {
            "entity": row["entity"],
            "decision_ts": row["decision_ts"],
            "lead": lead,
            "model_release_id": self.release_id,
            "unit": BUNDLE_UNIT,
            "price": float(row["price"]),
            "pi_upper": float(row["pi_upper"]),
            "weights": list(row["weights"]),
            "scenarios": [
                gross_return(row["yhat"] + residual, sigma, lead)
                for residual in row["scenarios"]
            ],
            "mu_gross": gross_return(row["yhat"], sigma, lead),
            "reference_policy": self.reference_policy,
            "known_at": dict(row["known_at"]),
        }


_CAP_SCHEMA_FIELDS = frozenset(
    (
        "schema_version",
        "model_release_id",
        "deployment_eligible",
        "evidence_scope",
        "evidence_end_ms",
        "generated_ms",
        "caps",
    )
)

#: The one schema version a confirmed-cap artifact may declare.
_CAP_SCHEMA_VERSION = 1


class ConfirmedCaps:
    """The pinned, deployable ``(symbol, lead)`` confirmation-cap artifact.

    Phase 4's cap contract (ADR-0121): confirmation caps are pinned,
    deployable, contiguous from h1, and from evidence not used to choose
    the P16 mask. Development caps always refuse deployment. No real
    artifact exists yet — the March-May confirmation evidence (§11 item 4)
    is unreadable — so this class only VALIDATES a caller-supplied
    artifact; it never produces or calibrates one.

    Parameters
    ----------
    artifact : dict
        Exactly: ``schema_version`` (1), ``model_release_id`` (non-empty
        str), ``deployment_eligible`` (must be ``True``),         ``evidence_scope``
        (non-empty str, NOT this child's P16 development scope),
        ``evidence_end_ms`` (epoch ms, strictly before ``generated_ms`` —
        every confirming outcome realized before the artifact was pinned),
        ``generated_ms`` (epoch ms), and ``caps`` — a list of unique
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
    Validate one deployable cap artifact::

        caps = ConfirmedCaps({
            "schema_version": 1, "model_release_id": "rel-abc",
            "deployment_eligible": True,
            "evidence_scope": "mean_confirmation_2026_03_05",
            "evidence_end_ms": 1_699_990_000_000,
            "generated_ms": 1_699_999_000_000,
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
        self.caps = {
            row["symbol"]: row["capped_horizon"] for row in artifact["caps"]
        }

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
        if artifact["deployment_eligible"] is not True:
            problems.append(
                "cap.deployment_eligible must be true — development caps "
                "always refuse deployment (plan §6 Phase 4 item 4)"
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
            if not number_ok(value) or value < 0:
                problems.append(
                    f"cap.{name} must be a finite epoch-ms number >= 0, got "
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
