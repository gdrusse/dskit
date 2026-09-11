"""``nodes_capital`` — ``EquityKellyMIO``, the intraday_equities capital step.

Role ``capital``: the planner refuses to plan a document that reaches this
node without a ``stat_test`` survivors wire, so it can never size a name the
edge test did not clear. Sits on the toolkit's
:class:`~dskit.pipeline.libs.pyomo.ScenarioUtilitySolve` doorway (ADR-0111,
``docs/plans/2026-09-intraday-equities-mio.md``) — the doorway owns the
scenario-utility MILP mechanism (inventory transition, self-financing, the
tangent-plane objective, the CVaR block, the empty-gate short circuit, the
post-solve exact recompute); this node supplies only the equity domain: a
fail-closed forecast-bundle reader, the Schwab per-share/bps cost model, the
ADR-0088 HFDR row, and a proportional-cost no-trade band.

This is a SCOPED build (ADR-0111), not the full live system the design
proposal describes. In particular:

* the Bertsimas-Sim robust counterpart of ``mu`` uncertainty is not built —
  only the already-approved scenario-recentering mechanism for ``U_mu``
  (a bundle's ``mu_gross``/scenario rows are assumed already recentered
  upstream, by whatever produced the bundle);
* the TAF per-order dollar cap is not modeled at all — every sell is
  charged the uncapped flat ``taf_per_share`` rate. An earlier version of
  this file tried a size-referenced rate (``taf_cap / held``), matching
  the pattern ``pmquant.mio.gated_sides`` uses for its own per-level fee;
  a skeptic review proved it UNDERCHARGES a small sell against a large
  held position (trimming 5 shares off a 1,000-share position priced the
  fee as if 1,000 were selling). The uncapped rate is conservative in the
  opposite, safe direction — it can only overstate the true fee on a
  single large sell that would hit the per-order cap, never understate
  it — so it never overstates achievable edge;
* ``lambda_t_bps`` (the joint opportunity-cost charge), the counterfactual
  (unfunded-candidate) ledger, and every calibration artifact (``pi_upper``,
  ``U_mu``, ``U_r`` by time-of-day) are NOT built — they need real market
  data and the plan's remaining owner decisions (§10) and are named,
  deliberate follow-on work.

Every owner-only risk number (``risk_aversion_gamma``, ``cardinality``,
``cvar_alpha``/``cvar_limit``, ``min_ticket`` from the doorway; ``hfdr_q``,
``band_bps``, ``max_position_notional``, the cost-model rates here) is a
REQUIRED param with no code-level default — a document that omits one
refuses to plan.

Import cost: stdlib + dskit. numpy and pyomo are reached only through the
doorway inside run-path methods, so a document naming this kind plans on a
machine with neither installed.
"""

from __future__ import annotations

from dskit.pipeline.libs.pyomo import ScenarioUtilitySolve
from dskit.pipeline.node import ConfigError, check_int_param, register_node_kind, reject_unknown_params
from dskit.pipeline.records import number_ok
from dskit.pipeline.stages import is_sha256hex

from .final_model import HEADS
from .forecast_bundle import (
    BUNDLE_UNIT,
    KNOWN_AT_FIELDS,
    ZERO_DRIFT,
    ConfirmedCaps,
    ForecastBundle,
    default_label_contract,
)

__all__ = [
    "BUNDLE_FIELDS",
    "DEFAULT_LOT_SIZE",
    "EquityKellyMIO",
    "NODE_KINDS",
    "SchwabCostModel",
]

#: What every surviving bundle row must carry — Path A18256's fail-closed
#: contract (docs/plans/2026-09-intraday-equities-mio.md §7), narrowed to
#: the fields this scoped build actually READS or verifies. Besides payoff
#: inputs, ADR-0121 requires the label/reference contract, point-in-time
#: audit trail, model-manifest identity, and producer identity at the capital
#: boundary; the full canonical row list is checked against its config pin.
BUNDLE_FIELDS = (
    "entity",
    "decision_ts",
    "lead",
    "model_release_id",
    "unit",
    "price",
    "pi_hat",
    "pi_upper",
    "weights",
    "scenarios",
    "reference_policy",
    "label",
    "model_manifest_sha256",
    "producer",
    "known_at",
)

#: The no-trade band's rounding granularity, absent a declared
#: ``lot_size`` — a mechanism default (accuracy/speed), never a risk
#: number, so unlike the doorway's owner-only knobs this one is safe to
#: default. NOT a round-lot trading constraint: see ``lot_size`` in
#: :class:`EquityKellyMIO`'s docstring for exactly what it does and does
#: not do (a skeptic review found the name alone reads as a stronger
#: promise than the code keeps).
DEFAULT_LOT_SIZE = 1

#: How far a bound-setting envelope is padded past the bundle's own observed
#: scenario extremes — a heuristic outer bound for the tangent knots'
#: interval, not a tight one (see :meth:`EquityKellyMIO._account_state`).
#: Widening it never breaks correctness, only tangent-approximation
#: fidelity near the true bounds; narrowing it risks spurious infeasibility.
_WEALTH_ENVELOPE_PAD = 1.5

#: A floor under the padded envelope's half-width, so a quiet bundle (every
#: scenario return near zero) still gives the solver a workable interval.
_WEALTH_ENVELOPE_FLOOR_FRAC = 0.05


def _ceil_div(numerator, denominator):
    """Give ``ceil(numerator / denominator)`` for non-negative floats, as an int."""
    return int(-(-numerator // denominator))


class SchwabCostModel:
    """Per-share Schwab half-spread, TAF, and Section-31 costs.

    The one owner of the formula ``EquityKellyMIO`` already uses: buy
    pays half-spread; sell pays half-spread plus uncapped TAF plus
    Section 31. ``min_price`` is the routing floor, not a fee input.

    Parameters
    ----------
    params : dict
        ``spread_bps`` (finite >= 0), ``taf_per_share`` (finite >= 0),
        ``sec31_bps`` (finite >= 0), ``min_price`` (finite > 0). All
        required; ``notes`` is allowed.

    Examples
    --------
    Per-share buy and sell costs at $11::

        costs = SchwabCostModel({
            "spread_bps": 2.2, "taf_per_share": 0.000195,
            "sec31_bps": 0.0206, "min_price": 5.0,
        })
        costs.buy_per_share(11.0)  # 0.00242
        costs.sell_per_share(11.0)  # 0.00242 + 0.000195 + 0.0002266
    """

    _PARAMS = ("spread_bps", "taf_per_share", "sec31_bps", "min_price")

    def __init__(self, params):
        problems = self.validate_params(params)
        if problems:
            raise ConfigError(problems)
        self._knobs = {name: float(params[name]) for name in self._PARAMS}

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + ("notes",))
        for name in ("spread_bps", "taf_per_share", "sec31_bps"):
            if name not in params:
                problems.append(f"{name} is required")
            elif not number_ok(params[name]) or params[name] < 0.0:
                problems.append(f"{name} must be a finite number >= 0, got {params[name]!r}")
        if "min_price" not in params:
            problems.append("min_price is required")
        elif not number_ok(params["min_price"]) or params["min_price"] <= 0.0:
            problems.append(
                f"min_price must be a finite number > 0, got {params['min_price']!r}"
            )
        return problems

    def buy_per_share(self, price):
        """Half-spread in quote currency per share at ``price``."""
        return self._knobs["spread_bps"] * 1e-4 * float(price)

    def sell_per_share(self, price):
        """Half-spread plus uncapped TAF plus Section 31, per share."""
        price = float(price)
        return (
            self.buy_per_share(price)
            + self._knobs["taf_per_share"]
            + self._knobs["sec31_bps"] * 1e-4 * price
        )

    def below_floor(self, price):
        """Return whether ``price`` is under the routing floor."""
        return float(price) < self._knobs["min_price"]


def _bundle_problems(bundle):
    """List problems with a declared ``bundle`` port, empty when none.

    An empty bundle is legal — it is the empty-gate case together with an
    empty portfolio, handled by :meth:`EquityKellyMIO.instruments`.
    """
    if not isinstance(bundle, (list, tuple)):
        return [
            f"bundle must be a materialized list of candidate rows, got "
            f"{type(bundle).__name__} — a one-shot iterable is refused by name "
            "rather than walked"
        ]
    problems = []
    weights_ref = None
    decision_ts_ref = None
    lead_ref = None
    release_ref = None
    seen = set()
    for i, row in enumerate(bundle):
        if not isinstance(row, dict):
            problems.append(f"bundle[{i}] must be a mapping, got {type(row).__name__}")
            continue
        missing = sorted(set(BUNDLE_FIELDS) - set(row))
        if missing:
            problems.append(
                f"bundle[{i}] ({row.get('entity', '?')!r}) missing required field(s) "
                f"{missing} — a stale, incomplete or unverifiable bundle row refuses by "
                "name (Path A18256, fail-closed)"
            )
            continue
        entity = row["entity"]
        if not isinstance(entity, str) or not entity:
            problems.append(f"bundle[{i}].entity must be a non-empty string, got {entity!r}")
        elif entity in seen:
            problems.append(f"bundle[{i}]: duplicate entity {entity!r} in one bundle")
        else:
            seen.add(entity)
        decision_ts = row["decision_ts"]
        if (
            isinstance(decision_ts, bool)
            or not isinstance(decision_ts, int)
            or decision_ts < 0
        ):
            problems.append(
                f"bundle[{i}] ({entity!r}).decision_ts must be an integer epoch ms >= 0"
            )
        elif decision_ts_ref is None:
            decision_ts_ref = decision_ts
        elif decision_ts != decision_ts_ref:
            problems.append(
                f"bundle[{i}] ({entity!r}).decision_ts must equal the batch's "
                f"shared decision_ts {decision_ts_ref!r}, got {decision_ts!r}"
            )
        lead = row["lead"]
        if isinstance(lead, bool) or not isinstance(lead, int) or not 1 <= lead <= len(HEADS):
            problems.append(
                f"bundle[{i}] ({entity!r}).lead must be an integer 1..{len(HEADS)}, "
                f"got {lead!r}"
            )
        elif lead_ref is None:
            lead_ref = lead
        elif lead != lead_ref:
            problems.append(
                f"bundle[{i}] ({entity!r}).lead must equal the batch's shared lead "
                f"{lead_ref!r}, got {lead!r}"
            )
        release = row["model_release_id"]
        if not isinstance(release, str) or not release:
            problems.append(
                f"bundle[{i}] ({entity!r}).model_release_id must be a non-empty string, "
                f"got {release!r}"
            )
        elif release_ref is None:
            release_ref = release
        elif release != release_ref:
            problems.append(
                f"bundle[{i}] ({entity!r}).model_release_id must equal the batch's "
                f"shared model_release_id {release_ref!r}, got {release!r}"
            )
        if row["unit"] != BUNDLE_UNIT:
            problems.append(
                f"bundle[{i}] ({entity!r}).unit must be {BUNDLE_UNIT!r}, got "
                f"{row['unit']!r} — label-unit predictions are never gross returns"
            )
        if row["reference_policy"] != ZERO_DRIFT:
            problems.append(
                f"bundle[{i}] ({entity!r}).reference_policy must be {ZERO_DRIFT!r}"
            )
        if row["label"] != default_label_contract():
            problems.append(
                f"bundle[{i}] ({entity!r}).label must equal the pinned label contract"
            )
        if not is_sha256hex(row["model_manifest_sha256"]):
            problems.append(
                f"bundle[{i}] ({entity!r}).model_manifest_sha256 must be lowercase SHA-256"
            )
        producer = row["producer"]
        producer_fields = {"document_sha256", "node", "output"}
        if not isinstance(producer, dict) or set(producer) != producer_fields:
            problems.append(
                f"bundle[{i}] ({entity!r}).producer must carry exactly "
                f"{sorted(producer_fields)!r}"
            )
        else:
            if not is_sha256hex(producer["document_sha256"]):
                problems.append(
                    f"bundle[{i}] ({entity!r}).producer.document_sha256 must be lowercase SHA-256"
                )
            if not isinstance(producer["node"], str) or not producer["node"]:
                problems.append(
                    f"bundle[{i}] ({entity!r}).producer.node must be non-empty"
                )
            if producer["output"] != "bundle":
                problems.append(
                    f"bundle[{i}] ({entity!r}).producer.output must be 'bundle'"
                )
        known_at = row["known_at"]
        if not isinstance(known_at, dict) or set(known_at) != set(KNOWN_AT_FIELDS):
            problems.append(
                f"bundle[{i}] ({entity!r}).known_at must carry exactly "
                f"{list(KNOWN_AT_FIELDS)!r}"
            )
        elif isinstance(decision_ts, int) and not isinstance(decision_ts, bool):
            for field, stamp in sorted(known_at.items()):
                if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp < 0:
                    problems.append(
                        f"bundle[{i}] ({entity!r}).known_at[{field!r}] must be "
                        "an integer epoch ms >= 0"
                    )
                elif stamp > decision_ts:
                    problems.append(
                        f"bundle[{i}] ({entity!r}).known_at[{field!r}] is after decision_ts"
                    )
        weights = row["weights"]
        scenarios = row["scenarios"]
        if not isinstance(weights, (list, tuple)) or not weights:
            problems.append(f"bundle[{i}] ({entity!r}).weights must be a non-empty list")
        elif not all(number_ok(w) and w >= 0.0 for w in weights):
            problems.append(
                f"bundle[{i}] ({entity!r}).weights must be all finite numbers >= 0"
            )
        elif abs(sum(float(w) for w in weights) - 1.0) > 1e-8:
            problems.append(
                f"bundle[{i}] ({entity!r}).weights must sum to 1, got "
                f"{sum(float(w) for w in weights)!r}"
            )
        elif weights_ref is None:
            weights_ref = list(weights)
        elif list(weights) != weights_ref:
            problems.append(
                f"bundle[{i}] ({entity!r}) carries scenario weights that differ from the "
                "batch's shared weights — one joint scenario set per decision tick "
                "(docs/plans/2026-09-intraday-equities-mio.md §7: mixed holding horizons "
                "or mismatched scenario sets in one bundle refuse)"
            )
        if not isinstance(scenarios, (list, tuple)):
            problems.append(f"bundle[{i}] ({entity!r}).scenarios must be a list")
        else:
            if isinstance(weights, (list, tuple)) and len(scenarios) != len(weights):
                problems.append(
                    f"bundle[{i}] ({entity!r}): scenarios length {len(scenarios)} != weights "
                    f"length {len(weights)}"
                )
            if not all(number_ok(v) for v in scenarios):
                problems.append(
                    f"bundle[{i}] ({entity!r}).scenarios must be all finite numbers"
                )
        if not number_ok(row.get("price")) or row["price"] <= 0.0:
            problems.append(f"bundle[{i}] ({entity!r}).price must be a finite number > 0")
        pi_hat = row["pi_hat"]
        pi_upper = row["pi_upper"]
        if not number_ok(pi_hat) or not 0.0 <= pi_hat <= 1.0:
            problems.append(f"bundle[{i}] ({entity!r}).pi_hat must be a finite number in [0, 1]")
        if not number_ok(pi_upper) or not 0.0 <= pi_upper <= 1.0:
            problems.append(f"bundle[{i}] ({entity!r}).pi_upper must be a finite number in [0, 1]")
        elif number_ok(pi_hat) and pi_hat > pi_upper:
            problems.append(
                f"bundle[{i}] ({entity!r}).pi_hat must not exceed pi_upper"
            )
    return problems


class EquityKellyMIO(ScenarioUtilitySolve):
    """Size intraday_equities' capital step from a forecast bundle plus portfolio state.

    The ``intraday_equities-kelly-mio`` kind. Inputs: ``bundle`` (a
    materialized list of per-candidate forecast rows, see
    :data:`BUNDLE_FIELDS`), ``portfolio`` (account state — ``asof_ms``,
    ``cash``, ``buying_power``, ``positions`` (``symbol -> held shares``),
    optional ``mark_prices`` for a held name the bundle dropped, optional
    ``cash_reserve``/``gross_limit``/``sale_credit``), ``survivors``
    (the ``stat_test`` gate REQUIRED by the planner's capital rule — only
    bundle rows whose ``entity`` is a survivor enter the program), and
    ``cap`` (the required, fresh, release-matched confirmed-cap artifact;
    its digest and producer/evidence identities must match config pins).

    Parameters
    ----------
    params : dict
        The doorway's knobs (``risk_aversion_gamma``, ``n_tangents``,
        ``n_scenarios_max``, ``cvar_alpha``, ``cvar_limit``, ``cardinality``,
        ``min_ticket``, ``solver``, ``solver_options``) plus this kind's own:
        ``spread_bps`` (required, >= 0 — half-spread charged on entry AND
        exit, both sides), ``taf_per_share`` (required, >= 0 — FINRA TAF,
        sell-only, charged UNCAPPED — see the module docstring on why the
        per-order cap is not modeled), ``sec31_bps`` (required, >= 0 — SEC
        Section 31, sell-only), ``min_price`` (required, > 0 — the per-share
        TAF-argument floor, §3.4), ``hfdr_q`` (required, in (0, 1) — ADR-0088's false-
        discovery threshold), ``band_bps`` (required, >= 0 — the no-trade
        band as basis points of the larger of current ticket or
        ``min_ticket``), ``max_position_notional`` (required, > 0 — a
        UNIFORM per-name dollar ceiling; a bundle-declared per-name cap is a
        follow-up, not built here), ``bundle_max_staleness_ms`` and
        ``cap_max_staleness_ms`` (required ints >= 0),
        ``bundle_artifact_sha256`` (canonical assembled-row-list digest),
        ``bundle_producer_document_sha256``/``bundle_producer_node`` and
        ``bundle_model_manifest_sha256`` (trusted bundle provenance),
        ``cap_artifact_sha256`` (canonical artifact digest),
        ``cap_producer_document_sha256``/``cap_producer_node`` (producer
        identity), ``cap_evidence_sha256`` (evidence identity), and
        ``deployment_mode`` (required bool). Development mode accepts only
        an explicitly non-deployable cap; deployment mode fails closed until
        a trusted real cap producer exists. ``lot_size`` (int >=
        1, default :data:`DEFAULT_LOT_SIZE` — scales
        the no-trade band's ``band_shares_i`` floor to a round number of
        lots; shares bought or sold are NOT themselves constrained to
        multiples of ``lot_size`` — ``model.b``/``model.s`` stay plain
        integers. A round-lot trading constraint would need its own MILP
        change (an integer lot-count variable, not this knob) and is not
        built here).

    Examples
    --------
    One name, no prior position, half-Kelly-ish risk aversion::

        node = EquityKellyMIO("size", {
            "risk_aversion_gamma": 2.0, "n_tangents": 32, "n_scenarios_max": 256,
            "cvar_alpha": 0.95, "cvar_limit": 5000.0, "cardinality": 5,
            "min_ticket": 500.0, "spread_bps": 2.2, "taf_per_share": 0.000195,
            "sec31_bps": 0.0206, "min_price": 5.0,
            "hfdr_q": 0.10, "band_bps": 10.0, "max_position_notional": 5000.0,
            "bundle_max_staleness_ms": 5000,
            "bundle_artifact_sha256": "d" * 64,
            "bundle_producer_document_sha256": "e" * 64,
            "bundle_producer_node": "forecast",
            "bundle_model_manifest_sha256": "f" * 64,
            "cap_max_staleness_ms": 5000,
            "cap_artifact_sha256": "a" * 64,
            "cap_producer_document_sha256": "b" * 64,
            "cap_producer_node": "source",
            "cap_evidence_sha256": "c" * 64,
            "deployment_mode": False,
        })
    """

    role = "capital"
    outputs = ("target", "trades", "cash_after", "metrics", "evidence")

    _PARAMS = ScenarioUtilitySolve._PARAMS + (
        "spread_bps",
        "taf_per_share",
        "sec31_bps",
        "min_price",
        "hfdr_q",
        "band_bps",
        "max_position_notional",
        "bundle_max_staleness_ms",
        "bundle_artifact_sha256",
        "bundle_producer_document_sha256",
        "bundle_producer_node",
        "bundle_model_manifest_sha256",
        "cap_max_staleness_ms",
        "cap_artifact_sha256",
        "cap_producer_document_sha256",
        "cap_producer_node",
        "cap_evidence_sha256",
        "deployment_mode",
        "lot_size",
    )

    #: Per-run bookkeeping for :meth:`domain_constraints`, set by
    #: :meth:`instruments` and cleared by :meth:`run` — the ``_current_event``
    #: precedent (``pmquant.nodes_capital.KellyMIO``).
    _pi_upper = None
    _band_shares = None
    _payoffs = None
    _evidence = None

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none — the doorway's, then this kind's."""
        problems = super().validate_params(params)
        for name in ("spread_bps", "taf_per_share", "sec31_bps"):
            if name not in params:
                problems.append(
                    f"{name} is required — the Schwab cost model has no default "
                    "(docs/plans/2026-09-intraday-equities-mio.md §3.4)"
                )
            elif not number_ok(params[name]) or params[name] < 0.0:
                problems.append(f"{name} must be a finite number >= 0, got {params[name]!r}")
        if "min_price" not in params:
            problems.append(
                "min_price is required — the per-share TAF-argument price floor, no default"
            )
        elif not number_ok(params["min_price"]) or params["min_price"] <= 0.0:
            problems.append(f"min_price must be a finite number > 0, got {params['min_price']!r}")
        if "hfdr_q" not in params:
            problems.append(
                "hfdr_q is required — ADR-0088's false-discovery threshold is an owner "
                "decision calibrated against realized hit rates, there is no default"
            )
        elif not number_ok(params["hfdr_q"]) or not 0.0 < params["hfdr_q"] < 1.0:
            problems.append(f"hfdr_q must be a finite number in (0, 1), got {params['hfdr_q']!r}")
        if "band_bps" not in params:
            problems.append(
                "band_bps is required — the no-trade band is an owner risk decision, "
                "there is no default"
            )
        elif not number_ok(params["band_bps"]) or params["band_bps"] < 0.0:
            problems.append(f"band_bps must be a finite number >= 0, got {params['band_bps']!r}")
        if "max_position_notional" not in params:
            problems.append(
                "max_position_notional is required — the per-name dollar ceiling is an "
                "owner risk decision, there is no default"
            )
        elif not number_ok(params["max_position_notional"]) or params["max_position_notional"] <= 0.0:
            problems.append(
                f"max_position_notional must be a finite number > 0, got "
                f"{params['max_position_notional']!r}"
            )
        if "bundle_max_staleness_ms" not in params:
            problems.append(
                "bundle_max_staleness_ms is required — how stale a bundle may be before "
                "this refuses, by name (Path A18256, fail-closed)"
            )
        else:
            check_int_param(problems, "bundle_max_staleness_ms", params["bundle_max_staleness_ms"], ge=0)
        if "cap_max_staleness_ms" not in params:
            problems.append(
                "cap_max_staleness_ms is required — how stale a confirmed-cap "
                "artifact may be before this refuses, by name"
            )
        else:
            check_int_param(
                problems,
                "cap_max_staleness_ms",
                params["cap_max_staleness_ms"],
                ge=0,
            )
        for name in (
            "bundle_artifact_sha256",
            "bundle_producer_document_sha256",
            "bundle_model_manifest_sha256",
            "cap_artifact_sha256",
            "cap_producer_document_sha256",
            "cap_evidence_sha256",
        ):
            if name not in params:
                problems.append(f"{name} is required — trusted provenance has no default")
            elif not is_sha256hex(params[name]):
                problems.append(f"{name} must be a lowercase SHA-256 digest")
        if "bundle_producer_node" not in params:
            problems.append(
                "bundle_producer_node is required — trusted bundle provenance has no default"
            )
        elif not isinstance(params["bundle_producer_node"], str) or not params[
            "bundle_producer_node"
        ]:
            problems.append("bundle_producer_node must be a non-empty string")
        if "cap_producer_node" not in params:
            problems.append("cap_producer_node is required — trusted cap provenance has no default")
        elif not isinstance(params["cap_producer_node"], str) or not params["cap_producer_node"]:
            problems.append("cap_producer_node must be a non-empty string")
        if "deployment_mode" not in params:
            problems.append("deployment_mode is required — development versus deployment must be explicit")
        elif not isinstance(params["deployment_mode"], bool):
            problems.append("deployment_mode must be a JSON boolean")
        check_int_param(problems, "lot_size", params.get("lot_size", DEFAULT_LOT_SIZE), ge=1)
        return problems

    def validate_inputs(self, inputs):
        """Problems with the materialized ``inputs``, empty when none."""
        bundle = inputs.get("bundle")
        bundle_problems = _bundle_problems(bundle)
        problems = list(bundle_problems)
        if not bundle_problems and bundle:
            digest = ForecastBundle.digest(bundle)
            if digest != self.params["bundle_artifact_sha256"]:
                problems.append(
                    "bundle artifact does not match bundle_artifact_sha256: "
                    f"expected {self.params['bundle_artifact_sha256']!r}, got {digest!r}"
                )
            for index, row in enumerate(bundle):
                producer = row["producer"]
                if producer["document_sha256"] != self.params[
                    "bundle_producer_document_sha256"
                ]:
                    problems.append(
                        f"bundle[{index}] producer document does not match "
                        "bundle_producer_document_sha256"
                    )
                if producer["node"] != self.params["bundle_producer_node"]:
                    problems.append(
                        f"bundle[{index}] producer node does not match bundle_producer_node"
                    )
                if row["model_manifest_sha256"] != self.params[
                    "bundle_model_manifest_sha256"
                ]:
                    problems.append(
                        f"bundle[{index}] model manifest does not match "
                        "bundle_model_manifest_sha256"
                    )
        portfolio = inputs.get("portfolio")
        if not isinstance(portfolio, dict):
            problems.append(
                f"portfolio must be a mapping of account state, got {type(portfolio).__name__}"
            )
        else:
            asof_ms = portfolio.get("asof_ms")
            if isinstance(asof_ms, bool) or not isinstance(asof_ms, int) or asof_ms < 0:
                problems.append("portfolio.asof_ms must be an integer epoch ms >= 0")
            if not number_ok(portfolio.get("cash")):
                problems.append("portfolio.cash must be a finite number")
            if not number_ok(portfolio.get("buying_power")):
                problems.append("portfolio.buying_power must be a finite number")
            positions = portfolio.get("positions", {})
            if not isinstance(positions, dict):
                problems.append("portfolio.positions must be a mapping of symbol -> held shares")
            else:
                for symbol, shares in positions.items():
                    if not isinstance(symbol, str) or not symbol:
                        problems.append(
                            f"portfolio.positions keys must be non-empty strings, got {symbol!r}"
                        )
                    elif not number_ok(shares) or shares != int(shares) or shares < 0:
                        problems.append(
                            f"portfolio.positions[{symbol!r}] must be a non-negative integer "
                            f"share count, got {shares!r} — a fractional or negative holding is "
                            "refused by name rather than silently truncated or solved into an "
                            "opaque infeasibility"
                        )
            mark_prices = portfolio.get("mark_prices", {})
            if not isinstance(mark_prices, dict):
                problems.append("portfolio.mark_prices must be a mapping of symbol -> price when given")
            else:
                for symbol, price in mark_prices.items():
                    if not isinstance(symbol, str) or not symbol:
                        problems.append(
                            f"portfolio.mark_prices keys must be non-empty strings, got {symbol!r}"
                        )
                    elif not number_ok(price) or price <= 0.0:
                        problems.append(
                            f"portfolio.mark_prices[{symbol!r}] must be a finite number > 0, "
                            f"got {price!r}"
                        )
            if "cash_reserve" in portfolio and not number_ok(portfolio["cash_reserve"]):
                problems.append(
                    f"portfolio.cash_reserve must be a finite number when given, got "
                    f"{portfolio['cash_reserve']!r}"
                )
            gross_limit = portfolio.get("gross_limit")
            if gross_limit is not None and (not number_ok(gross_limit) or gross_limit < 0.0):
                problems.append(
                    f"portfolio.gross_limit must be a finite number >= 0, or null (unconstrained), "
                    f"got {gross_limit!r}"
                )
            if "sale_credit" in portfolio and (
                not number_ok(portfolio["sale_credit"]) or not 0.0 <= portfolio["sale_credit"] <= 1.0
            ):
                problems.append(
                    f"portfolio.sale_credit must be a finite number in [0, 1] when given, got "
                    f"{portfolio['sale_credit']!r}"
                )
        survivors = inputs.get("survivors")
        if not isinstance(survivors, (list, tuple, set, frozenset)):
            problems.append(
                "survivors must be a materialized collection of entity names (the "
                f"stat_test survivors wire), got {type(survivors).__name__}"
            )
        else:
            for name in survivors:
                if not isinstance(name, str):
                    problems.append(f"survivors entries must be strings, got {name!r}")
        cap = inputs.get("cap")
        if "cap" not in inputs:
            problems.append("cap is required — the pinned confirmed-cap artifact has no default")
            return problems
        cap_problems = ConfirmedCaps.problems(cap)
        problems.extend(cap_problems)
        if not cap_problems:
            confirmed = ConfirmedCaps(cap)
            digest = ConfirmedCaps.digest(cap)
            if digest != self.params["cap_artifact_sha256"]:
                problems.append(
                    "cap artifact does not match cap_artifact_sha256: "
                    f"expected {self.params['cap_artifact_sha256']!r}, got {digest!r}"
                )
            if confirmed.producer["document_sha256"] != self.params[
                "cap_producer_document_sha256"
            ]:
                problems.append(
                    "cap producer document does not match "
                    "cap_producer_document_sha256"
                )
            if confirmed.producer["node"] != self.params["cap_producer_node"]:
                problems.append("cap producer node does not match cap_producer_node")
            if confirmed.evidence["sha256"] != self.params["cap_evidence_sha256"]:
                problems.append("cap evidence does not match cap_evidence_sha256")
            if self.params["deployment_mode"]:
                if not confirmed.deployment_eligible:
                    problems.append(
                        "cap.deployment_eligible must be true in deployment mode"
                    )
                problems.append(
                    "deployment mode refuses: no trusted real confirmation-cap "
                    "producer exists yet"
                )
            elif confirmed.deployment_eligible:
                problems.append(
                    "cap.deployment_eligible must be false in development mode"
                )
            if (
                isinstance(portfolio, dict)
                and isinstance(portfolio.get("asof_ms"), int)
                and not isinstance(portfolio.get("asof_ms"), bool)
            ):
                age_ms = portfolio["asof_ms"] - confirmed.generated_ms
                max_stale = int(self.params["cap_max_staleness_ms"])
                if age_ms < 0:
                    problems.append(
                        f"cap is from the future: age_ms={age_ms} against portfolio.asof_ms"
                    )
                elif age_ms > max_stale:
                    problems.append(
                        f"cap is stale: age_ms={age_ms} exceeds cap_max_staleness_ms={max_stale}"
                    )
            if (
                isinstance(bundle, (list, tuple))
                and bundle
                and isinstance(bundle[0], dict)
                and isinstance(bundle[0].get("model_release_id"), str)
                and bundle[0]["model_release_id"] != confirmed.model_release_id
            ):
                problems.append(
                    "cap.model_release_id must equal the bundle's shared "
                    f"model_release_id {bundle[0]['model_release_id']!r}, got "
                    f"{confirmed.model_release_id!r}"
                )
            if (
                isinstance(bundle, (list, tuple))
                and bundle
                and isinstance(bundle[0], dict)
                and isinstance(bundle[0].get("decision_ts"), int)
                and not isinstance(bundle[0].get("decision_ts"), bool)
                and confirmed.generated_ms > bundle[0]["decision_ts"]
            ):
                problems.append(
                    f"cap.generated_ms {confirmed.generated_ms!r} is after bundle "
                    f"decision_ts {bundle[0]['decision_ts']!r} — the cap must exist "
                    "before it can authorize that forecast decision"
                )
        return problems

    # -- the three doorway hooks --------------------------------------------

    def instruments(self, inputs):
        """Fail-closed bundle read + Schwab cost pricing -> ``(names, rows, account)``.

        A bundle row is routed out (never entering the program) for a
        stat_test-gate miss, missing/zero/over-horizon confirmed cap,
        staleness past ``bundle_max_staleness_ms``, or a price below
        ``min_price`` — each reason recorded in
        ``self._evidence`` for :meth:`run`'s ``evidence`` output. A currently
        held name absent from the surviving bundle rows enters as a
        MANDATORY EXIT (``x_max = 0``): it may only be sold, never bought.
        """
        bundle = inputs["bundle"]
        portfolio = inputs["portfolio"]
        survivors = set(inputs["survivors"])
        confirmed = ConfirmedCaps(inputs["cap"])
        asof_ms = portfolio["asof_ms"]
        max_stale = int(self.params["bundle_max_staleness_ms"])
        min_price = float(self.params["min_price"])
        max_notional = float(self.params["max_position_notional"])
        lot = int(self.params.get("lot_size", DEFAULT_LOT_SIZE))

        held = {k: int(v) for k, v in portfolio.get("positions", {}).items() if int(v) != 0}
        mark_prices = portfolio.get("mark_prices", {})
        routed_out = {}
        by_name = {}
        for row in bundle:
            entity = row["entity"]
            if entity not in survivors:
                routed_out[entity] = "not a stat_test survivor"
                continue
            capped_horizon = confirmed.capped_horizon(entity)
            if capped_horizon is None:
                routed_out[entity] = "no confirmed cap for symbol"
                continue
            if capped_horizon == 0:
                routed_out[entity] = "zero confirmed cap"
                continue
            if row["lead"] > capped_horizon:
                routed_out[entity] = (
                    f"lead {row['lead']} is above confirmed cap {capped_horizon}"
                )
                continue
            age_ms = asof_ms - row["decision_ts"]
            if age_ms < 0 or age_ms > max_stale:
                routed_out[entity] = f"bundle stale or from the future: age_ms={age_ms}"
                continue
            if float(row["price"]) < min_price:
                routed_out[entity] = f"price {row['price']!r} below min_price {min_price!r}"
                continue
            by_name[entity] = row

        names = sorted(set(by_name) | set(held))
        self._evidence = {
            "n_bundle_rows": len(bundle),
            "n_gated": len(by_name),
            "n_held": len(held),
            "routed_out": routed_out,
        }
        if not names:
            self._pi_upper, self._band_shares = {}, {}
            return [], {}, {"cash": float(portfolio.get("cash", 0.0))}

        # The batch's shared scenario weights: a gated row's (validated
        # identical across the whole bundle by _bundle_problems), or a
        # degenerate single certain scenario when nothing gated but a held
        # name must still be able to exit — its trade is riskless from the
        # optimizer's view (deterministic sale proceeds), so one certain
        # scenario is the correct shape, never a borrowed one.
        shared_weights = (
            list(next(iter(by_name.values()))["weights"]) if by_name else [1.0]
        )

        rows, pi_upper, band_shares, payoffs_r = {}, {}, {}, {}
        worst_r, best_r = 0.0, 0.0
        for name in names:
            row = by_name.get(name)
            h = held.get(name, 0)
            if row is None:
                # Held but the bundle dropped it: mandatory exit only. No
                # live belief exists for it, so the HFDR row must not bind
                # it either — pi_upper=0 keeps its (pi_upper - q) coefficient
                # negative, and x_max=0 (below) already forces q=0 whatever
                # the HFDR row says.
                mark = mark_prices.get(name)
                if not number_ok(mark) or mark <= 0.0:
                    raise ValueError(
                        f"{self.key}: {name!r} is held ({h} shares) but absent from the "
                        "surviving bundle rows and portfolio.mark_prices carries no usable "
                        f"price for it (got {mark!r}) — a mandatory exit needs a finite mark "
                        "> 0 to trade against"
                    )
                price = float(mark)
                pi_upper_i = 0.0
                x_max = 0.0
                scenarios = [0.0] * len(shared_weights)
            else:
                price = float(row["price"])
                pi_upper_i = float(row["pi_upper"])
                x_max = max_notional
                scenarios = [float(v) for v in row["scenarios"]]
            # Uncapped TAF lives in SchwabCostModel (see that class and the
            # module docstring) — never a size-referenced rate.
            costs = SchwabCostModel({name: self.params[name] for name in SchwabCostModel._PARAMS})
            spread = costs.buy_per_share(price)
            sell_cost = costs.sell_per_share(price)
            rows[name] = {
                "price": price,
                "held": h,
                "x_max": x_max,
                "cost_buy": spread,
                "cost_sell": sell_cost,
                # Liquidating at the horizon pays the same sell-side costs
                # as an ordinary exit (§5.3's exit_cost_o(q)) — never left
                # at the doorway's zero default, or the CVaR cap and the
                # objective both silently price every position as
                # free-to-unwind.
                "exit_cost_per_share": sell_cost,
                "lot": lot,
            }
            pi_upper[name] = pi_upper_i
            payoffs_r[name] = scenarios
            if row is None:
                band_shares[name] = 0
            else:
                ticket = max(price * h, float(self.params["min_ticket"]))
                band_bps = float(self.params["band_bps"])
                band_shares[name] = lot * _ceil_div(band_bps * 1e-4 * ticket, price * lot)
            worst_r = min(worst_r, min(scenarios))
            best_r = max(best_r, max(scenarios))
        self._pi_upper, self._band_shares = pi_upper, band_shares
        self._payoffs = (shared_weights, payoffs_r)

        notional_cap = sum(r["x_max"] for r in rows.values())
        gross_limit = portfolio.get("gross_limit")
        if gross_limit is not None:
            notional_cap = min(notional_cap, float(gross_limit))
        w0_mark = float(portfolio.get("cash", 0.0)) + sum(
            r["price"] * r["held"] for r in rows.values()
        )
        span = notional_cap * max(abs(worst_r), abs(best_r), _WEALTH_ENVELOPE_FLOOR_FRAC)
        span = max(span, _WEALTH_ENVELOPE_FLOOR_FRAC * max(w0_mark, 1.0)) * _WEALTH_ENVELOPE_PAD
        account = {
            "cash": float(portfolio.get("cash", 0.0)),
            "buying_power": float(portfolio.get("buying_power", 0.0)),
            "sale_credit": float(portfolio.get("sale_credit", 1.0)),
            "cash_reserve": float(portfolio.get("cash_reserve", 0.0)),
            "gross_limit": None if gross_limit is None else float(gross_limit),
            # The floor is relative to w0_mark, never an absolute dollar
            # figure — a round-12 skeptic review found a hardcoded
            # max(1.0, ...) floor could exceed wealth_hi for a small
            # positive net worth (e.g. a near-zero account holding one
            # residual sub-dollar position), spuriously refusing an
            # otherwise perfectly legitimate "just sell the one share"
            # state. w0_mark > 0 is already guaranteed (the doorway
            # itself refuses w0_mark <= 0) and span > 0 always (the
            # _WEALTH_ENVELOPE_FLOOR_FRAC term never vanishes), so
            # wealth_lo < w0_mark < wealth_hi holds BY CONSTRUCTION at
            # every scale, never just for dollar-sized accounts.
            "wealth_lo": max(w0_mark * 0.01, w0_mark - span),
            "wealth_hi": w0_mark + span,
        }
        return names, rows, account

    def payoffs(self, inputs):
        """Return the batch's shared scenario weights + per-name gross-return matrix.

        Cached by :meth:`instruments` (the same pass that decides which
        names are gated vs. mandatory-exit already built these arrays;
        re-deriving them here from the raw ``bundle`` port would silently
        diverge from that routing decision for a row that was routed OUT
        but still present in the raw bundle).
        """
        return self._payoffs

    def domain_constraints(self, model, inputs, params):
        """Add the ADR-0088 HFDR row and a proportional-cost no-trade band.

        HFDR (C7, ADR-0088, locked): ``sum_i (pi_upper_i - q) * x_i <= 0`` —
        linear in the target notional the doorway already exposes as
        ``model.x``.

        No-trade band (§3.3(b)): a trade either does not happen at all, or
        moves at least ``band_shares_i`` — the wedge-shaped inaction region
        proportional trading costs require (Constantinides 1986; Davis &
        Norman 1990) — split by direction against the doorway's own
        ``model.d`` (1 buys this tick, 0 sells; ``b``/``s`` are already
        mutually exclusive per name, by construction).

        The SELL-side floor is ``min(band_shares_i, held_i)``, never
        ``band_shares_i`` alone. A round-5 skeptic review proved that using
        the raw floor made a legacy position smaller than the band a "roach
        motel": ``s[i]`` is hard-capped at ``held[i]`` (the doorway's own
        variable bound), so whenever ``band_shares_i > held_i`` a full exit
        could never clear the floor and no PARTIAL exit was legal either —
        the position could be held or bought into, never sold, with no
        error raised. Flooring the sell side at ``held_i`` keeps the band's
        intent (no dust-sized partial sells) while always leaving a full
        exit reachable — the same "do nothing must stay possible regardless
        of a legacy position's size" principle behind the doorway's own
        ``elig_lo``/``model.d`` fix for min_ticket.

        A trade-active binary ``a[i]`` keeps "no trade" a genuine third
        option distinct from "trade in whichever direction ``d`` picks" —
        without it, forcing a floor whenever ``d`` resolves either way would
        make SOME trade mandatory for any name with ``held > 0``. Because
        ``b[i]`` is already forced to 0 when ``d[i]=0`` (and ``s[i]`` to 0
        when ``d[i]=1``, both by the doorway's own ``buy_only``/``sell_only``
        rows), the two big-M forms below apply their own direction's floor
        only when both that direction AND ``a[i]`` are active, and stay
        slack (never binding) on the other direction — this is linear, not
        a product of two binaries, because the "other direction" term is
        already pinned to 0 elsewhere in the model.
        """
        from pyomo.environ import Binary, Constraint, Var

        names = model._scn["names"]
        rows = model._scn["rows"]
        q = float(params["hfdr_q"])
        model.hfdr = Constraint(
            expr=sum((self._pi_upper[i] - q) * model.x[i] for i in names) <= 0
        )

        band = self._band_shares
        sell_floor = {i: min(band[i], int(rows[i]["held"])) for i in names}
        trade_room = {}
        for i in names:
            price = rows[i]["price"]
            buy_room = int(rows[i]["x_max"] / price) if price > 0 else 0
            trade_room[i] = max(buy_room + rows[i]["held"], band[i], 1)

        model.a = Var(names, domain=Binary)
        model.band_buy_floor = Constraint(
            names,
            rule=lambda m, i: band[i] * m.a[i] - band[i] * (1 - m.d[i]) <= m.b[i],
        )
        model.band_sell_floor = Constraint(
            names,
            rule=lambda m, i: sell_floor[i] * m.a[i] - sell_floor[i] * m.d[i] <= m.s[i],
        )
        model.band_hi = Constraint(
            names, rule=lambda m, i: m.b[i] + m.s[i] <= trade_room[i] * m.a[i]
        )

    # -- run: fold in the evidence output ------------------------------------

    def run(self, ctx, inputs):
        """Solve, then attach the routing/gating evidence to the reported outputs."""
        try:
            problems = self.validate_inputs(inputs)
            if problems:
                raise ValueError(f"{self.key}: " + "; ".join(problems))
            out = super().run(ctx, inputs)
            out["evidence"] = self._evidence or {
                "n_bundle_rows": 0, "n_gated": 0, "n_held": 0, "routed_out": {},
            }
            return out
        finally:
            self._pi_upper = self._band_shares = self._payoffs = self._evidence = None


#: kind name -> class: what the registry, the conformance suite, and a
#: document's ``uses`` all key off.
NODE_KINDS = {
    "intraday_equities-kelly-mio": EquityKellyMIO,
}

# Import = registration (``owned`` deliberately NOT set — see CLAUDE.md).
for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)
