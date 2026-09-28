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
* ``lambda_t_bps`` (the joint opportunity-cost charge) and the
  counterfactual (unfunded-candidate) ledger are NOT built — they need real
  market data and the plan's remaining owner decisions (§10) and are named,
  deliberate follow-on work.

**Uncertainty reaches this node only through an attested seam.** The
``uncertainty`` port carries one ``dskit.pipeline.uncertainty_intake``
envelope per estimand — a false-signal rate and a realized-outcome band —
and each is admitted against ONE ``DecisionDemand`` built from the bundle's
own shared decision timestamp and release identity, through
``uncertainty_intake.admission_problems`` (the module FUNCTION, which reads
the registry rather than asking the envelope's class what it is). An artifact that is
stale, wrong-unit, post-decision, uncalibrated or from a different model is
refused by name (ADR-0165). What that machinery establishes is that an
UNATTESTED artifact cannot be consumed; it establishes nothing about
whether any particular artifact is well calibrated, because it measures
nothing. The two policy knobs it screens against
(``uncertainty_max_calibration_age_ms``, ``uncertainty_min_coverage``) are
owner risk decisions and are REQUIRED params with no code-level default.

**The HFDR row is fed a widened POINT ESTIMATE, not a bound.** ADR-0088
locks ``sum_i (pi_i - q) * x_i <= 0``; the number available for ``pi_i``
today is ``pi_widened``, whose measured attainment of the true rate is
0.53-0.82 against a 0.95 nominal (ADR-0152). The row is therefore NOT a
chance constraint at level ``1 - q``, and ``run`` records exactly that in
its ``evidence`` output. The withdrawn name ``pi_upper`` is refused at this
boundary as well as at the bundle's, and
``uncertainty_intake.ProbabilityUpperBound`` — the family a genuine bound
would belong to — has NO REGISTERED INTAKE, and ``admission_problems``
answers that question from the registry rather than from any class's claim
about itself, so a demand for a bound refuses every artifact that exists.
What that buys is that the promotion cannot happen by accident, by a
downstream subclass, or by a virtual ``ABCMeta.register``; it is not
protection against code that already controls the interpreter (ADR-0122's
Correction), and the intake screens generally are in-process checks, not a
root of trust.

Every owner-only risk number (``risk_aversion_gamma``, ``cardinality``,
``cvar_alpha``/``cvar_limit``, ``min_ticket`` from the doorway; ``hfdr_q``,
``band_bps``, ``max_position_notional``, the cost-model rates here) is a
REQUIRED param with no code-level default — a document that omits one
refuses to plan.

**The joint kind.** :class:`JointEquityKellyMIO`
(``intraday_equities-joint-kelly-mio``) is ADR-0188's formulation B: ONE
solve per minute over every held and admitted name, holdings in and whole
target shares out, sells and trims as ordinary decisions, and each name's
target split across its exit horizons (the doorway's tranches) so the whole
forecast path is used rather than one admitted lead. It keeps every input
check and the cost model of :class:`EquityKellyMIO`, applies the HFDR row
per tranche, and refuses the hard-row knobs by name
(:data:`JOINT_REFUSED_PARAMS`): costs live in the objective, never in a cap
that shrinks the feasible set (owner rulings 2026-09-25). The base kind is
unchanged.

Import cost: stdlib + dskit. numpy and pyomo are reached only through the
doorway inside run-path methods, so a document naming this kind plans on a
machine with neither installed.
"""

from __future__ import annotations

import math
import numbers
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dskit.pipeline.libs.pyomo import ScenarioUtilitySolve
from dskit.pipeline.uncertainty_set import BudgetedMeanSet
from dskit.pipeline.node import ConfigError, check_int_param, register_node_kind, reject_unknown_params
from dskit.pipeline.records import number_ok
from dskit.pipeline.stages import is_sha256hex
from dskit.pipeline.uncertainty_intake import (
    AttestedFalseSignalRate,
    AttestedMeanConfidenceFamily,
    AttestedOutcomeBand,
    AttestedUncertainty,
    DecisionDemand,
    admission_problems,
    artifact_of,
    attestation_of,
)

from .final_gates import cell_id
from .final_model import HEADS
from .forecast_bundle import (
    _PATH_FIELDS,
    BUNDLE_UNIT,
    KNOWN_AT_FIELDS,
    UNCERTAINTY_ARTIFACT_FIELDS,
    WITHDRAWN_FIELD_ALIASES,
    ZERO_DRIFT,
    ConfirmedCaps,
    ForecastBundle,
    default_label_contract,
)

__all__ = [
    "BUNDLE_FIELDS",
    "CAP_LOOK_AHEAD_DISCLOSURE",
    "DEFAULT_LOT_SIZE",
    "HFDR_COEFFICIENT_FIELD",
    "HFDR_PATH_FIELD",
    "JOINT_PATH_OUTPUTS",
    "JOINT_REFUSED_PARAMS",
    "REQUIRED_INTAKES",
    "ROUTE_PERMANENT",
    "ROUTE_PRODUCER_FAULT",
    "ROUTE_TRANSIENT",
    "EquityKellyMIO",
    "JointEquityKellyMIO",
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
    "pi_widened",
    "weights",
    "scenarios",
    "reference_policy",
    "label",
    "model_manifest_sha256",
    "producer",
    "uncertainty",
    "known_at",
)

#: Which bundle field the ADR-0088 HFDR row's ``pi_i`` coefficient reads.
#: Named once, here, because the constraint, the evidence record and the
#: tests must all agree on it and a second spelling is how they stop
#: agreeing. It is ``pi_widened``: a widened POINT ESTIMATE, so the row is
#: not a chance constraint — see the module docstring.
HFDR_COEFFICIENT_FIELD = "pi_widened"

#: The ``uncertainty`` port's slots, each bound to the ONE intake member
#: that may fill it. A slot is a question; the member is the only kind of
#: answer that question takes, and it is a TYPE, so a caller cannot
#: relabel an artifact into the wrong slot.
REQUIRED_INTAKES = (
    ("false_signal", AttestedFalseSignalRate),
    ("outcome", AttestedOutcomeBand),
)

#: The no-trade band's rounding granularity, absent a declared
#: ``lot_size`` — a mechanism default (accuracy/speed), never a risk
#: number, so unlike the doorway's owner-only knobs this one is safe to
#: default. NOT a round-lot trading constraint: see ``lot_size`` in
#: :class:`EquityKellyMIO`'s docstring for exactly what it does and does
#: not do (a skeptic review found the name alone reads as a stronger
#: promise than the code keeps).
DEFAULT_LOT_SIZE = 1

#: What the ``evidence`` output records when the declared developmental
#: switch ``cap_evidence_look_ahead`` is true (ADR-0184 Revision 2).
CAP_LOOK_AHEAD_DISCLOSURE = "caps use post-selection evidence (look-ahead disclosed)"

#: How far a bound-setting envelope is padded past the bundle's own observed
#: scenario extremes — a heuristic outer bound for the tangent knots'
#: interval, not a tight one (see :meth:`EquityKellyMIO._account_state`).
#: Widening it never breaks correctness, only tangent-approximation
#: fidelity near the true bounds; narrowing it risks spurious infeasibility.
_WEALTH_ENVELOPE_PAD = 1.5

#: A floor under the padded envelope's half-width, so a quiet bundle (every
#: scenario return near zero) still gives the solver a workable interval.
_WEALTH_ENVELOPE_FLOOR_FRAC = 0.05

#: The structured reason codes :meth:`EquityKellyMIO._routed_rows` returns
#: beside its human-readable messages (ADR-0188). The messages stay the base
#: kind's ``evidence["routed_out"]`` strings, pinned by its tests; a policy
#: keys on the codes, never on the text. PERMANENT: the name left the
#: admitted set at a release boundary (not a ``stat_test`` survivor, no cap,
#: a zero cap).
ROUTE_PERMANENT = frozenset(("not_survivor", "no_cap", "zero_cap", "empty_plan"))

#: TRANSIENT reason codes: a condition of this minute (a stale row, a price
#: under ``min_price``), expected to clear.
ROUTE_TRANSIENT = frozenset(("stale", "below_min_price"))

#: PRODUCER-FAULT reason codes: a row stamped after the decision instant, or
#: a horizon the confirmed cap does not confirm. Both come from the producer
#: that built the bundle and its cap, so neither is a market condition.
ROUTE_PRODUCER_FAULT = frozenset(("future", "cap_mismatch"))

#: The base kind's knobs :class:`JointEquityKellyMIO` refuses BY NAME, each
#: with the reason its refusal gives. Owner rulings 2026-09-25: costs belong
#: in the objective, never in a hard cap that shrinks the feasible set
#: (ADR-0188 questions F(a) and G(a)); even the value that would make a knob
#: inert refuses, so a document cannot carry a dead cap.
JOINT_REFUSED_PARAMS = {
    "band_bps": (
        "the no-trade band is a hard row; inaction emerges from the costs the "
        "objective already charges every trade (ADR-0188 question G(a))"
    ),
    "cardinality": (
        "a cap on the number of names held shrinks the feasible set; "
        "concentration is priced by the utility's curvature, the CVaR row and "
        "the no-leverage bound"
    ),
    "min_ticket": (
        "a minimum ticket is a hard floor; whole shares are the only "
        "granularity and every trade pays its cost in the objective"
    ),
    "max_position_notional": (
        "the per-name ceiling is replaced by the no-leverage bound "
        "max(G, price x held), so a holding that appreciated is never forced "
        "to sell by its own row; the account's aggregate bound G is the "
        "no-leverage bound, NAV, refused by name when it sits below the marked "
        "holdings of the LIVE names in the solve — a mandatory exit's own mark "
        "is excluded from that check, since its target is already pinned to "
        "zero and it can never bind the aggregate row itself (ADR-0188 "
        "question F(a))"
    ),
    "lot_size": (
        "it only scaled the no-trade band's floor, which the joint kind does "
        "not build; shares are plain integers"
    ),
}

#: What the joint kind hands the doorway for the two gating knobs the doorway
#: requires: no cardinality row and no minimum ticket.
_JOINT_DOORWAY_PARAMS = {"cardinality": None, "min_ticket": 0.0}

#: How far below the marked holdings a declared ``gross_limit`` may sit before
#: the joint kind refuses it. Not just float noise in the last bits: a
#: caller's ``gross_limit`` is commonly NAV computed off the replay's own
#: ``mark_prices``, while this refusal marks holdings at the BUNDLE ROW's
#: own price -- the same two numbers ``_refuse_price_disagreement``
#: (``simulation.py``) tolerates differing by up to its own 1e-6 relative
#: bound. A tolerance tighter than that gap would make a nearly-fully-
#: invested book's LEGITIMATE row/mark disagreement alone trip this refusal.
#: 1e-4 is two orders of magnitude looser than that tolerated gap (comfortable
#: margin) while still catching any real shortfall a caller's own tests use
#: (all >= 20% of the marked holdings).
_GROSS_LIMIT_REL_TOLERANCE = 1e-4

#: The assembled exit-horizon outputs every joint bundle row must carry
#: (``ForecastBundle``'s path rows, ADR-0188 formulation B).
JOINT_PATH_OUTPUTS = (
    "admitted_horizon",
    "plan_horizon",
    "mu_gross_path",
    "pi_hat_path",
    "pi_widened_path",
    "scenarios_path",
    "sigma_t",
    "mean_deviation_below_path",
    "mean_deviation_above_path",
)

#: Which path field the joint kind's per-tranche HFDR row reads: the path
#: counterpart of :data:`HFDR_COEFFICIENT_FIELD`, one widened POINT ESTIMATE
#: per lead, so the row is no more a chance constraint than the base's.
HFDR_PATH_FIELD = "pi_widened_path"

#: Any of these on a bundle row marks it a PATH row: the assembled outputs
#: plus ``ForecastBundle``'s input path fields (``yhat_path``).
_PATH_ROW_MARKERS = frozenset(JOINT_PATH_OUTPUTS) | _PATH_FIELDS


def _ceil_div(numerator, denominator):
    """Give ``ceil(numerator / denominator)`` for non-negative floats, as an int."""
    return int(-(-numerator // denominator))


class SchwabCostModel:
    """Per-share Schwab half-spread, TAF, and Section-31 costs, per name and fill minute.

    The one owner of the formula ``EquityKellyMIO`` sizes with and the
    replay bills with: each side pays the name's QUOTED half-spread times
    ``eq_ratio`` (the broker's effective/quoted spread ratio, i.e. price
    improvement) times the time-of-day multiplier of the FILL minute; a
    sell also pays uncapped TAF plus Section 31 (owner rulings 2026-09-25,
    ADR-0120/ADR-0185 amendments). ``min_price`` is the routing floor, not
    a fee input.

    The multiplier is keyed on the instant the order FILLS (the replay's
    fill bar, i.e. the decision bar + ``fill_bar_offset``), never on the
    decision instant, so sizing (which is told that instant) and the
    replay's fill charge the identical per-share rate.

    Parameters
    ----------
    params : dict
        ``half_spread_bps`` (mapping of non-empty symbol -> finite >= 0,
        the quoted half-spread in bp), ``default_half_spread_bps`` (finite
        >= 0 for any unlisted name, or ``None``: an unlisted name then
        refuses), ``eq_ratio`` (finite > 0, effective/quoted),
        ``spread_time_of_day`` (``timezone``: a zoneinfo key;
        ``default_multiplier``: finite > 0 for a minute in no window;
        ``windows``: a list of ``{start_minute, end_minute, multiplier}``,
        integer wall-clock minutes after local midnight in ``timezone``,
        ``0 <= start < end <= 1440``, half-open ``[start, end)``,
        non-overlapping, multiplier finite > 0), ``taf_per_share`` (finite
        >= 0), ``sec31_bps`` (finite >= 0), ``min_price`` (finite > 0).
        All required; ``notes`` is allowed.

    Examples
    --------
    Per-share buy and sell costs for LLY at $800, filled at 09:45 New York::

        costs = SchwabCostModel({
            "half_spread_bps": {"LLY": 4.51}, "default_half_spread_bps": None,
            "eq_ratio": 1.0, "taf_per_share": 0.000195,
            "sec31_bps": 0.0206, "min_price": 5.0,
            "spread_time_of_day": {
                "timezone": "America/New_York", "default_multiplier": 1.0,
                "windows": [{"start_minute": 570, "end_minute": 600, "multiplier": 2.0}],
            },
        })
        costs.buy_per_share("LLY", 800.0, fill_ms)  # 0.7216 (4.51 bp x 2.0)
        costs.sell_per_share("LLY", 800.0, fill_ms)  # 0.7216 + 0.000195 + 0.001648
        costs.buy_per_share("XOM", 110.0, fill_ms)  # ConfigError: no half-spread for XOM
    """

    _PARAMS = (
        "half_spread_bps",
        "default_half_spread_bps",
        "eq_ratio",
        "spread_time_of_day",
        "taf_per_share",
        "sec31_bps",
        "min_price",
    )

    #: The keys of ``spread_time_of_day`` and of each of its windows.
    _TOD_KEYS = ("timezone", "default_multiplier", "windows")
    _WINDOW_KEYS = ("start_minute", "end_minute", "multiplier")
    _MINUTES_PER_DAY = 1440

    def __init__(self, params):
        problems = self.validate_params(params)
        if problems:
            raise ConfigError(problems)
        self._spreads = {symbol: float(bps) for symbol, bps in params["half_spread_bps"].items()}
        default = params["default_half_spread_bps"]
        self._default = None if default is None else float(default)
        self._knobs = {
            name: float(params[name]) for name in ("eq_ratio", "taf_per_share", "sec31_bps", "min_price")
        }
        schedule = params["spread_time_of_day"]
        self._zone = ZoneInfo(schedule["timezone"])
        self._default_multiplier = float(schedule["default_multiplier"])
        self._windows = tuple(
            (int(w["start_minute"]), int(w["end_minute"]), float(w["multiplier"]))
            for w in schedule["windows"]
        )

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + ("notes",))
        problems.extend(cls.spread_problems(params))
        for name in ("taf_per_share", "sec31_bps"):
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

    @classmethod
    def spread_problems(cls, params):
        """Problems with the four spread knobs in ``params``, empty when none."""
        problems = []
        if "half_spread_bps" not in params:
            problems.append(
                "half_spread_bps is required — a {symbol: quoted half-spread bp} map, "
                "no default"
            )
        elif not isinstance(params["half_spread_bps"], dict):
            problems.append(
                f"half_spread_bps must be a mapping of symbol -> bp, got "
                f"{type(params['half_spread_bps']).__name__}"
            )
        else:
            for symbol, bps in params["half_spread_bps"].items():
                if not isinstance(symbol, str) or not symbol:
                    problems.append(f"half_spread_bps keys must be non-empty symbols, got {symbol!r}")
                elif not number_ok(bps) or bps < 0.0:
                    problems.append(
                        f"half_spread_bps[{symbol!r}] must be a finite number >= 0, got {bps!r}"
                    )
        if "default_half_spread_bps" not in params:
            problems.append(
                "default_half_spread_bps is required — a finite bp for unlisted names, or "
                "null to refuse them"
            )
        elif params["default_half_spread_bps"] is not None and (
            not number_ok(params["default_half_spread_bps"])
            or params["default_half_spread_bps"] < 0.0
        ):
            problems.append(
                f"default_half_spread_bps must be null or a finite number >= 0, got "
                f"{params['default_half_spread_bps']!r}"
            )
        if "eq_ratio" not in params:
            problems.append(
                "eq_ratio is required — the broker's effective/quoted spread ratio, no default"
            )
        elif not number_ok(params["eq_ratio"]) or params["eq_ratio"] <= 0.0:
            problems.append(f"eq_ratio must be a finite number > 0, got {params['eq_ratio']!r}")
        if "spread_time_of_day" not in params:
            problems.append(
                "spread_time_of_day is required — {timezone, default_multiplier, windows} "
                "keyed on the fill minute, no default"
            )
        else:
            problems.extend(cls._time_of_day_problems(params["spread_time_of_day"]))
        return problems

    @classmethod
    def _time_of_day_problems(cls, schedule):
        """Problems with a ``spread_time_of_day`` value, each naming the knob."""
        name = "spread_time_of_day"
        if not isinstance(schedule, dict):
            return [
                f"{name} must be a mapping {{timezone, default_multiplier, windows}}, got "
                f"{type(schedule).__name__}"
            ]
        problems = []
        unknown = sorted(str(key) for key in set(schedule) - set(cls._TOD_KEYS))
        if unknown:
            problems.append(f"{name} has unknown keys {unknown}")
        missing = [key for key in cls._TOD_KEYS if key not in schedule]
        problems.extend(f"{name}.{key} is required" for key in missing)
        if "timezone" in schedule:
            zone = schedule["timezone"]
            if not isinstance(zone, str) or not zone:
                problems.append(f"{name}.timezone must be a non-empty zoneinfo key, got {zone!r}")
            else:
                try:
                    ZoneInfo(zone)
                except (ZoneInfoNotFoundError, ValueError):
                    problems.append(f"{name}.timezone is not a known zoneinfo key: {zone!r}")
        if "default_multiplier" in schedule:
            value = schedule["default_multiplier"]
            if not number_ok(value) or value <= 0.0:
                problems.append(f"{name}.default_multiplier must be a finite number > 0, got {value!r}")
        if "windows" not in schedule:
            return problems
        windows = schedule["windows"]
        if not isinstance(windows, list):
            problems.append(f"{name}.windows must be a list, got {type(windows).__name__}")
            return problems
        spans = []
        for index, window in enumerate(windows):
            where = f"{name}.windows[{index}]"
            if not isinstance(window, dict) or set(window) != set(cls._WINDOW_KEYS):
                problems.append(
                    f"{where} must be exactly {{start_minute, end_minute, multiplier}}, got {window!r}"
                )
                continue
            start, end, multiplier = (window[key] for key in cls._WINDOW_KEYS)
            bounds_ok = True
            for key, value in (("start_minute", start), ("end_minute", end)):
                if isinstance(value, bool) or not isinstance(value, int):
                    problems.append(f"{where}.{key} must be an integer minute, got {value!r}")
                    bounds_ok = False
                elif not 0 <= value <= cls._MINUTES_PER_DAY:
                    problems.append(
                        f"{where}.{key} must be in [0, {cls._MINUTES_PER_DAY}], got {value!r}"
                    )
                    bounds_ok = False
            if bounds_ok and start >= end:
                problems.append(f"{where} must have start_minute < end_minute, got [{start}, {end})")
                bounds_ok = False
            if not number_ok(multiplier) or multiplier <= 0.0:
                problems.append(f"{where}.multiplier must be a finite number > 0, got {multiplier!r}")
            if bounds_ok:
                spans.append((start, end, index))
        spans.sort()
        for (start_a, end_a, a), (start_b, end_b, b) in zip(spans, spans[1:]):
            if start_b < end_a:
                problems.append(
                    f"{name}.windows[{a}] [{start_a}, {end_a}) and windows[{b}] "
                    f"[{start_b}, {end_b}) overlap — a fill minute must map to one multiplier"
                )
        return problems

    def time_of_day_multiplier(self, fill_ms):
        """Return the spread multiplier for a fill at epoch ms ``fill_ms``.

        The fill instant's wall-clock minute in the schedule's timezone
        (DST-aware) selects the one window containing it; a minute in no
        window gets ``default_multiplier``.

        Raises
        ------
        ConfigError
            ``fill_ms`` is not an integer epoch ms >= 0.
        """
        if isinstance(fill_ms, bool) or not isinstance(fill_ms, numbers.Integral) or fill_ms < 0:
            raise ConfigError([
                f"the fill instant must be an integer epoch ms >= 0, got {fill_ms!r} — the "
                "time-of-day spread is keyed on the fill minute and has no default"
            ])
        local = datetime.fromtimestamp(int(fill_ms) // 1000, tz=timezone.utc).astimezone(self._zone)
        minute = local.hour * 60 + local.minute
        for start, end, multiplier in self._windows:
            if start <= minute < end:
                return multiplier
        return self._default_multiplier

    def half_spread_bps(self, symbol, fill_ms):
        """Return the half-spread ``symbol`` pays per side at ``fill_ms``, in bp.

        Quoted x ``eq_ratio`` x the fill minute's time-of-day multiplier.

        Raises
        ------
        ConfigError
            ``symbol`` is not listed and no default is declared, or
            ``fill_ms`` is not an integer epoch ms >= 0.
        """
        quoted = self._spreads.get(symbol, self._default)
        if quoted is None:
            raise ConfigError([
                f"no half-spread for {symbol!r}: it is not in half_spread_bps and "
                "default_half_spread_bps is null — an unpriced name refuses"
            ])
        return quoted * self._knobs["eq_ratio"] * self.time_of_day_multiplier(fill_ms)

    def buy_per_share(self, symbol, price, fill_ms):
        """Effective half-spread in quote currency per share of ``symbol`` at ``price``, filled at ``fill_ms``."""
        return self.half_spread_bps(symbol, fill_ms) * 1e-4 * float(price)

    def sell_per_share(self, symbol, price, fill_ms):
        """Effective half-spread plus uncapped TAF plus Section 31, per share, filled at ``fill_ms``."""
        price = float(price)
        return (
            self.buy_per_share(symbol, price, fill_ms)
            + self._knobs["taf_per_share"]
            + self._knobs["sec31_bps"] * 1e-4 * price
        )

    def below_floor(self, price):
        """Return whether ``price`` is under the routing floor."""
        return float(price) < self._knobs["min_price"]


def _fill_instant_problems(portfolio):
    """Problems with ``portfolio.fill_ms``, empty when none.

    Every priced name's fill instant (epoch ms, never before the decision
    ``asof_ms``) keys its time-of-day spread; the map is required.
    """
    if "fill_ms" not in portfolio:
        return [
            "portfolio.fill_ms is required — {symbol: fill instant epoch ms}, the minute "
            "each order fills keys its time-of-day spread"
        ]
    fill_ms = portfolio["fill_ms"]
    if not isinstance(fill_ms, dict):
        return [f"portfolio.fill_ms must be a mapping of symbol -> epoch ms, got {type(fill_ms).__name__}"]
    asof_ms = portfolio.get("asof_ms")
    problems = []
    for symbol, instant in fill_ms.items():
        if not isinstance(symbol, str) or not symbol:
            problems.append(f"portfolio.fill_ms keys must be non-empty strings, got {symbol!r}")
        elif isinstance(instant, bool) or not isinstance(instant, numbers.Integral):
            problems.append(f"portfolio.fill_ms[{symbol!r}] must be an integer epoch ms, got {instant!r}")
        elif isinstance(asof_ms, int) and not isinstance(asof_ms, bool) and instant < asof_ms:
            problems.append(
                f"portfolio.fill_ms[{symbol!r}] = {instant} is before asof_ms {asof_ms} — "
                "an order cannot fill before its decision"
            )
    return problems


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
    uncertainty_ref = None
    decision_ts_ref = None
    lead_ref = None
    release_ref = None
    seen = set()
    for i, row in enumerate(bundle):
        if not isinstance(row, dict):
            problems.append(f"bundle[{i}] must be a mapping, got {type(row).__name__}")
            continue
        for alias in sorted(set(row) & set(WITHDRAWN_FIELD_ALIASES)):
            problems.append(
                f"bundle[{i}] ({row.get('entity', '?')!r}) carries the WITHDRAWN "
                f"field {alias!r}, renamed {WITHDRAWN_FIELD_ALIASES[alias]!r} "
                "(ADR-0152) — a widened point estimate presented under a name "
                "that asserts a probability upper bound is refused here as well "
                "as at the bundle boundary, because a bundle can reach this node "
                "without passing through ForecastBundle"
            )
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
        path_row = bool(set(row) & _PATH_ROW_MARKERS)
        uncertainty_fields = UNCERTAINTY_ARTIFACT_FIELDS + (("mean",) if path_row else ())
        uncertainty = row["uncertainty"]
        if not isinstance(uncertainty, dict) or set(uncertainty) != set(
            uncertainty_fields
        ):
            problems.append(
                f"bundle[{i}] ({entity!r}).uncertainty must carry exactly "
                f"{sorted(uncertainty_fields)!r} — one calibration "
                "artifact identity per estimand"
            )
        elif any(not isinstance(v, str) or not v for v in uncertainty.values()):
            problems.append(
                f"bundle[{i}] ({entity!r}).uncertainty values must be non-empty "
                f"artifact identities, got {uncertainty!r}"
            )
        elif uncertainty_ref is None:
            uncertainty_ref = dict(uncertainty)
        elif dict(uncertainty) != uncertainty_ref:
            problems.append(
                f"bundle[{i}] ({entity!r}).uncertainty {dict(uncertainty)!r} "
                f"differs from the batch's shared identities {uncertainty_ref!r} "
                "— one decision tick is calibrated by one set of artifacts"
            )
        known_at = row["known_at"]
        for alias in sorted(set(known_at or {}) & set(WITHDRAWN_FIELD_ALIASES)):
            problems.append(
                f"bundle[{i}] ({entity!r}).known_at stamps the WITHDRAWN field "
                f"{alias!r}, renamed {WITHDRAWN_FIELD_ALIASES[alias]!r} "
                "(ADR-0152)"
            )
        known_at_fields = KNOWN_AT_FIELDS + (("mean_interval",) if path_row else ())
        if not isinstance(known_at, dict) or set(known_at) != set(known_at_fields):
            problems.append(
                f"bundle[{i}] ({entity!r}).known_at must carry exactly "
                f"{list(known_at_fields)!r}"
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
        pi_widened = row[HFDR_COEFFICIENT_FIELD]
        if not number_ok(pi_hat) or not 0.0 <= pi_hat <= 1.0:
            problems.append(f"bundle[{i}] ({entity!r}).pi_hat must be a finite number in [0, 1]")
        if not number_ok(pi_widened) or not 0.0 <= pi_widened <= 1.0:
            problems.append(
                f"bundle[{i}] ({entity!r}).{HFDR_COEFFICIENT_FIELD} must be a "
                "finite number in [0, 1]"
            )
        elif number_ok(pi_hat) and pi_hat > pi_widened:
            problems.append(
                f"bundle[{i}] ({entity!r}).pi_hat must not exceed "
                f"{HFDR_COEFFICIENT_FIELD}"
            )
    return problems


def _path_cell_id(symbol, lead):
    """Return the ``SYM:hNN`` id of one ``(symbol, lead)`` calibration cell.

    The key a path release's per-cell artifacts carry (the gate's
    false-signal cells, ``ForecastPublisher.path_envelopes``), e.g.
    ``_path_cell_id("LLY", 1) == "LLY:h01"`` — ``final_gates.cell_id``, the
    one owner of the format.
    """
    return cell_id(symbol, lead)


def _path_output_problems(bundle):
    """List problems with a joint bundle's exit-horizon outputs, empty when none.

    Called only on a bundle whose flat contract :func:`_bundle_problems`
    already accepted. A bundle can reach the capital node without passing
    through ``ForecastBundle``, so the shape the joint program reads is
    checked here, by entity: every row carries all of
    :data:`JOINT_PATH_OUTPUTS`; ``scenarios_path`` is 1..10 steps, each as
    long as the row's weights and every return finite and > -1;
    ``admitted_horizon`` and ``plan_horizon`` are integer steps; the three
    per-step lists carry one finite entry per step, the two rates in
    [0, 1] with ``pi_hat`` never above ``pi_widened``; and the flat fields
    ARE the path's first step (``lead`` 1, ``pi_hat``, ``pi_widened`` and
    ``scenarios``), so the numbers the admitted false-signal artifact binds
    are the ones the first tranche reads.

    Parameters
    ----------
    bundle : list of dict
        The bundle rows.

    Returns
    -------
    list of str
        One problem per defect, each naming the row and the field.
    """
    problems = []
    for index, row in enumerate(bundle):
        tag = f"bundle[{index}] ({row['entity']!r})"
        missing = [field for field in JOINT_PATH_OUTPUTS if field not in row]
        if missing:
            problems.append(
                f"{tag} is missing the path output(s) {missing} — the joint kind "
                "sizes every name from its exit-horizon path (ADR-0188 formulation "
                "B), so a flat row refuses by name"
            )
            continue
        path = row["scenarios_path"]
        if not isinstance(path, (list, tuple)) or not 1 <= len(path) <= len(HEADS):
            problems.append(
                f"{tag}.scenarios_path must be a list of 1..{len(HEADS)} steps, "
                f"got {path!r}"
            )
            continue
        n_steps, n_scenarios = len(path), len(row["weights"])
        steps_ok = []
        for step, values in enumerate(path, start=1):
            if not isinstance(values, (list, tuple)) or len(values) != n_scenarios:
                problems.append(
                    f"{tag}.scenarios_path[{step}] must be a list as long as weights "
                    f"({n_scenarios})"
                )
            elif not all(number_ok(v) and v > -1.0 for v in values):
                problems.append(
                    f"{tag}.scenarios_path[{step}] must be all finite simple returns > -1"
                )
            else:
                steps_ok.append(step)
        for field in ("admitted_horizon", "plan_horizon"):
            value = row[field]
            low = 1 if field == "admitted_horizon" else 0
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= n_steps:
                problems.append(
                    f"{tag}.{field} must be an integer step {low}..{n_steps}, got {value!r}"
                )
        rates = {}
        for field in ("mu_gross_path", "pi_hat_path", HFDR_PATH_FIELD):
            values = row[field]
            if not isinstance(values, (list, tuple)) or len(values) != n_steps:
                problems.append(
                    f"{tag}.{field} must carry one entry per step ({n_steps}), got {values!r}"
                )
            elif not all(number_ok(v) for v in values):
                problems.append(f"{tag}.{field} must be all finite numbers")
            elif field != "mu_gross_path" and not all(0.0 <= v <= 1.0 for v in values):
                problems.append(f"{tag}.{field} must be all in [0, 1]")
            else:
                rates[field] = [float(v) for v in values]
        sigma = row["sigma_t"]
        vol_floor = float(row["label"]["vol_floor"])
        if not number_ok(sigma) or sigma <= vol_floor:
            problems.append(
                f"{tag}.sigma_t must be a finite number above the pinned label "
                f"contract's vol_floor {vol_floor!r}, got {sigma!r}"
            )
        for field in ("mean_deviation_below_path", "mean_deviation_above_path"):
            values = row[field]
            if not isinstance(values, (list, tuple)) or len(values) != n_steps:
                problems.append(
                    f"{tag}.{field} must carry one entry per step ({n_steps}), got {values!r}"
                )
            elif not all(number_ok(value) and value >= 0.0 for value in values):
                problems.append(f"{tag}.{field} must be all finite numbers >= 0")
        if "pi_hat_path" in rates and HFDR_PATH_FIELD in rates and any(
            hat > widened for hat, widened in zip(rates["pi_hat_path"], rates[HFDR_PATH_FIELD])
        ):
            problems.append(f"{tag}.pi_hat_path must not exceed {HFDR_PATH_FIELD} at any step")
        if row["lead"] != 1:
            problems.append(
                f"{tag}.lead must be 1 on a path row — its flat fields are the "
                f"path's first step, got {row['lead']!r}"
            )
        first_step = [
            (flat, rates[field][0])
            for flat, field in (("pi_hat", "pi_hat_path"), (HFDR_COEFFICIENT_FIELD, HFDR_PATH_FIELD))
            if field in rates
        ]
        if 1 in steps_ok:
            first_step.append(("scenarios", [float(v) for v in path[0]]))
        for flat, expected in first_step:
            actual = row[flat]
            actual = [float(v) for v in actual] if isinstance(actual, (list, tuple)) else float(actual)
            if actual != expected:
                problems.append(
                    f"{tag}.{flat} {row[flat]!r} must equal the path's first step "
                    f"{expected!r} — the flat fields ARE step 1, and they are the "
                    "numbers the admitted artifacts bind"
                )
    return problems


class EquityKellyMIO(ScenarioUtilitySolve):
    """Size intraday_equities' capital step from a forecast bundle plus portfolio state.

    The ``intraday_equities-kelly-mio`` kind. Inputs: ``bundle`` (a
    materialized list of per-candidate forecast rows, see
    :data:`BUNDLE_FIELDS`; even an empty list must match its artifact pin,
    and it is legal only with no held positions), ``portfolio`` (account state — ``asof_ms``,
    ``cash``, ``buying_power``, ``positions`` (``symbol -> held shares``),
    ``fill_ms`` (``symbol -> epoch ms >= asof_ms``, the instant each name's
    order FILLS — the replay's fill bar, i.e. the decision bar +
    ``fill_bar_offset``; required for every name the program prices, since
    the time-of-day spread is keyed on it), optional ``mark_prices`` for a
    held name the bundle dropped, optional
    ``cash_reserve``/``gross_limit``/``sale_credit``), ``survivors``
    (the ``stat_test`` gate REQUIRED by the planner's capital rule — only
    bundle rows whose ``entity`` is a survivor enter the program),
    ``cap`` (the required, fresh, release-matched confirmed-cap artifact;
    its digest and producer/evidence identities must match config pins),
    and ``uncertainty`` (a mapping carrying exactly the
    :data:`REQUIRED_INTAKES` slots, each an
    ``uncertainty_intake.AttestedUncertainty`` envelope of that slot's
    member type; each is admitted against ONE ``DecisionDemand`` built
    from the bundle's own shared decision timestamp and release, and each
    row's declared calibration identities and ``pi`` numbers must match
    the admitted artifacts).

    Parameters
    ----------
    params : dict
        The doorway's knobs (``risk_aversion_gamma``, ``n_tangents``,
        ``n_scenarios_max``, ``cvar_alpha``, ``cvar_limit``, ``cardinality``,
        ``min_ticket``, ``solver``, ``solver_options``) plus this kind's own:
        ``half_spread_bps`` / ``default_half_spread_bps`` / ``eq_ratio`` /
        ``spread_time_of_day`` (required — :class:`SchwabCostModel`'s
        per-name quoted half-spread map, its default for unlisted names or
        null to refuse them, the effective/quoted ratio, and the
        time-of-day windows; each side pays quoted x ``eq_ratio`` x the
        multiplier of the name's ``portfolio.fill_ms`` minute, on entry AND
        exit), ``taf_per_share`` (required, >= 0 — FINRA TAF,
        sell-only, charged UNCAPPED — see the module docstring on why the
        per-order cap is not modeled), ``sec31_bps`` (required, >= 0 — SEC
        Section 31, sell-only), ``min_price`` (required, > 0 — the per-share
        TAF-argument floor, §3.4), ``hfdr_q`` (required, in (0, 1) — ADR-0088's false-
        discovery threshold), ``band_bps`` (required, >= 0 — the no-trade
        band as basis points of the larger of current ticket or
        ``min_ticket``; so ``min_ticket`` also floors the band, and at 0 an
        unheld name has no band: any whole-share buy clears it),
        ``max_position_notional`` (required, > 0 — a
        UNIFORM per-name dollar ceiling; a bundle-declared per-name cap is a
        follow-up, not built here), ``bundle_max_staleness_ms`` and
        ``cap_max_staleness_ms`` (required ints >= 0),
        ``bundle_artifact_sha256`` (canonical assembled-row-list digest),
        ``bundle_producer_document_sha256``/``bundle_producer_node`` and
        ``bundle_model_manifest_sha256`` (trusted bundle provenance),
        ``cap_artifact_sha256`` (canonical artifact digest),
        ``cap_producer_document_sha256``/``cap_producer_node`` (producer
        identity), ``cap_evidence_sha256`` (evidence identity),
        ``deployment_mode`` (required bool),
        ``uncertainty_max_calibration_age_ms`` (required int >= 0 — how far
        a decision may sit past the end of an artifact's calibration
        window) and ``uncertainty_min_coverage`` (required, in (0, 1) —
        the floor an artifact's ATTESTED measured coverage must reach;
        nothing here measures coverage, it screens what a producer
        attests). Development mode accepts only
        an explicitly non-deployable cap; deployment mode fails closed until
        a trusted real cap producer exists. ``lot_size`` (int >=
        1, default :data:`DEFAULT_LOT_SIZE` — scales
        the no-trade band's ``band_shares_i`` floor to a round number of
        lots; shares bought or sold are NOT themselves constrained to
        multiples of ``lot_size`` — ``model.b``/``model.s`` stay plain
        integers. A round-lot trading constraint would need its own MILP
        change (an integer lot-count variable, not this knob) and is not
        built here). ``cap_evidence_look_ahead`` (optional JSON bool,
        default false; true only with ``deployment_mode`` false —
        ADR-0184 Revision 2): the declared developmental switch that skips
        exactly the two cap-timing refusals ("cap is from the future" and
        ``cap.generated_ms`` after the bundle's ``decision_ts``) for a cap
        whose TRUE stamps postdate the tick; staleness still applies when
        the cap is not from the future, every other cap check is unchanged,
        and ``evidence`` records :data:`CAP_LOOK_AHEAD_DISCLOSURE`.

    Examples
    --------
    One name, no prior position, half-Kelly-ish risk aversion::

        node = EquityKellyMIO("size", {
            "risk_aversion_gamma": 2.0, "n_tangents": 32, "n_scenarios_max": 256,
            "cvar_alpha": 0.95, "cvar_limit": 5000.0, "cardinality": 5,
            "min_ticket": 500.0, "half_spread_bps": {"LLY": 4.51},
            "default_half_spread_bps": 5.62, "eq_ratio": 1.0, "taf_per_share": 0.000195,
            "spread_time_of_day": {
                "timezone": "America/New_York", "default_multiplier": 1.0, "windows": [],
            },
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
            "uncertainty_max_calibration_age_ms": 600_000,
            "uncertainty_min_coverage": 0.90,
        })
    """

    role = "capital"
    outputs = ("target", "trades", "cash_after", "metrics", "evidence")

    _PARAMS = ScenarioUtilitySolve._PARAMS + (
        "half_spread_bps",
        "default_half_spread_bps",
        "eq_ratio",
        "spread_time_of_day",
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
        "uncertainty_max_calibration_age_ms",
        "uncertainty_min_coverage",
        "lot_size",
        "cap_evidence_look_ahead",
    )

    #: Per-run bookkeeping for :meth:`domain_constraints`, set by
    #: :meth:`instruments` and cleared by :meth:`run` — the ``_current_event``
    #: precedent (``pmquant.nodes_capital.KellyMIO``).
    _pi_widened = None
    _band_shares = None
    _payoffs = None
    _evidence = None

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none — the doorway's, then this kind's.

        The kind's own checks live in per-concern helpers
        (:meth:`_cost_problems`, :meth:`_hfdr_problems`,
        :meth:`_trade_limit_problems`, :meth:`_provenance_problems`,
        :meth:`_intake_policy_problems`), run in this order, so a subclass
        can keep the checks it shares without inheriting the knobs it
        refuses (:class:`JointEquityKellyMIO`).
        """
        problems = super().validate_params(params)
        problems.extend(cls._cost_problems(params))
        problems.extend(cls._hfdr_problems(params))
        problems.extend(cls._trade_limit_problems(params))
        problems.extend(cls._provenance_problems(params))
        problems.extend(cls._intake_policy_problems(params))
        check_int_param(problems, "lot_size", params.get("lot_size", DEFAULT_LOT_SIZE), ge=1)
        return problems

    @classmethod
    def _cost_problems(cls, params):
        """Problems with the Schwab cost knobs, empty when none."""
        problems = list(SchwabCostModel.spread_problems(params))
        for name in ("taf_per_share", "sec31_bps"):
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
        return problems

    @classmethod
    def _hfdr_problems(cls, params):
        """Problems with ``hfdr_q``, ADR-0088's false-discovery threshold, empty when none."""
        problems = []
        if "hfdr_q" not in params:
            problems.append(
                "hfdr_q is required — ADR-0088's false-discovery threshold is an owner "
                "decision calibrated against realized hit rates, there is no default"
            )
        elif not number_ok(params["hfdr_q"]) or not 0.0 < params["hfdr_q"] < 1.0:
            problems.append(f"hfdr_q must be a finite number in (0, 1), got {params['hfdr_q']!r}")
        return problems

    @classmethod
    def _trade_limit_problems(cls, params):
        """Problems with the two hard-row knobs, empty when none.

        ``band_bps`` (the no-trade band) and ``max_position_notional`` (the
        uniform per-name ceiling): owner risk decisions this kind requires
        and the joint kind refuses by name (:data:`JOINT_REFUSED_PARAMS`).
        """
        problems = []
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
        return problems

    @classmethod
    def _provenance_problems(cls, params):
        """Problems with the staleness, provenance-pin, deployment and look-ahead knobs."""
        problems = []
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
        look_ahead = params.get("cap_evidence_look_ahead", False)
        if not isinstance(look_ahead, bool):
            problems.append("cap_evidence_look_ahead must be a JSON boolean")
        elif look_ahead and params.get("deployment_mode") is not False:
            problems.append(
                "cap_evidence_look_ahead may be true only with deployment_mode false — "
                "caps built on post-selection evidence never authorize deployment"
            )
        return problems

    @classmethod
    def _intake_policy_problems(cls, params):
        """Problems with the two owner-only uncertainty-intake knobs."""
        problems = []
        if "uncertainty_max_calibration_age_ms" not in params:
            problems.append(
                "uncertainty_max_calibration_age_ms is required — how far a "
                "decision may sit past the end of an artifact's calibration "
                "window is an owner risk decision, there is no default"
            )
        else:
            check_int_param(
                problems,
                "uncertainty_max_calibration_age_ms",
                params["uncertainty_max_calibration_age_ms"],
                ge=0,
            )
        if "uncertainty_min_coverage" not in params:
            problems.append(
                "uncertainty_min_coverage is required — the floor an artifact's "
                "ATTESTED measured coverage must reach is an owner risk "
                "decision, there is no default and nothing here measures it"
            )
        elif (
            not number_ok(params["uncertainty_min_coverage"])
            or not 0.0 < params["uncertainty_min_coverage"] < 1.0
        ):
            problems.append(
                "uncertainty_min_coverage must be a finite number in (0, 1), got "
                f"{params['uncertainty_min_coverage']!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        """Problems with the materialized ``inputs``, empty when none."""
        bundle = inputs.get("bundle")
        bundle_problems = _bundle_problems(bundle)
        problems = list(bundle_problems)
        if not bundle_problems and isinstance(bundle, (list, tuple)):
            mode_problems = self._bundle_mode_problems(bundle)
            if mode_problems:
                # Before the digest pin, which cannot hash a malformed path.
                return mode_problems
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
                if isinstance(bundle, (list, tuple)) and not bundle and any(
                    number_ok(shares) and shares != 0
                    for shares in positions.values()
                ):
                    problems.append(
                        "an empty bundle cannot authorize liquidation of a non-empty "
                        "portfolio — require an authenticated bundle carrying the held names"
                    )
            problems.extend(_fill_instant_problems(portfolio))
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
        problems.extend(self._uncertainty_problems(inputs, bundle, bundle_problems))
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
            # ADR-0184 Revision 2: the declared developmental switch skips
            # exactly the two cap-timing refusals below; staleness still
            # applies whenever age_ms >= 0.
            look_ahead = self.params.get("cap_evidence_look_ahead", False) is True
            if (
                isinstance(portfolio, dict)
                and isinstance(portfolio.get("asof_ms"), int)
                and not isinstance(portfolio.get("asof_ms"), bool)
            ):
                age_ms = portfolio["asof_ms"] - confirmed.generated_ms
                max_stale = int(self.params["cap_max_staleness_ms"])
                if age_ms < 0:
                    if not look_ahead:
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
                not look_ahead
                and isinstance(bundle, (list, tuple))
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

    def _bundle_mode_problems(self, bundle):
        """Problems with the bundle's MODE for this kind, empty when none.

        This kind sizes each name at its row's one lead, so a PATH row
        (ADR-0188: one carrying the exit-horizon fields) refuses by name
        rather than being sized at its path's first step with the rest of
        the curve silently ignored. :class:`JointEquityKellyMIO` overrides
        this the other way round.

        Parameters
        ----------
        bundle : list of dict
            Rows whose flat contract :func:`_bundle_problems` accepted.

        Returns
        -------
        list of str
            One problem per path row, naming it and the joint kind.
        """
        problems = []
        for index, row in enumerate(bundle):
            carried = sorted(set(row) & _PATH_ROW_MARKERS)
            if carried:
                problems.append(
                    f"bundle[{index}] ({row['entity']!r}) carries the exit-horizon path "
                    f"field(s) {carried} — a path bundle must be sized by the joint kind "
                    "intraday_equities-joint-kelly-mio (ADR-0188); this kind reads one "
                    "lead and would size every name at its path's first step"
                )
        return problems

    @classmethod
    def required_intakes(cls):
        """Return the attested uncertainty slots this capital kind consumes."""
        return REQUIRED_INTAKES

    def decision_demand(self, bundle):
        """State what this tick demands of any uncertainty it consumes.

        Parameters
        ----------
        bundle : list of dict
            The validated bundle rows. Their SHARED ``decision_ts`` and
            ``model_release_id`` are what every artifact is screened
            against, which is what makes "everything agrees on a single
            decision timestamp" a checkable claim rather than a hope.

        Returns
        -------
        dskit.pipeline.uncertainty_intake.DecisionDemand or None
            The demand, or ``None`` when the bundle is empty or its first
            row's identity fields are unusable — the empty gate sizes
            nothing, so there is no decision for an artifact to inform.
        """
        if not bundle or not isinstance(bundle[0], dict):
            return None
        decision_ts = bundle[0].get("decision_ts")
        release = bundle[0].get("model_release_id")
        if (
            isinstance(decision_ts, bool)
            or not isinstance(decision_ts, int)
            or decision_ts < 0
            or not isinstance(release, str)
            or not release
        ):
            return None
        return DecisionDemand(
            decision_ts_ms=decision_ts,
            model_identity=release,
            max_calibration_age_ms=int(
                self.params["uncertainty_max_calibration_age_ms"]
            ),
            min_measured_coverage=float(self.params["uncertainty_min_coverage"]),
        )

    def _uncertainty_problems(self, inputs, bundle, bundle_problems):
        """Problems with the ``uncertainty`` port, empty when none."""
        required = self.required_intakes()
        slots = sorted(name for name, _ in required)
        if "uncertainty" not in inputs:
            return [
                f"uncertainty is required — one attested artifact per {slots!r}; "
                "capital never sizes against uncertainty it cannot attest"
            ]
        port = inputs["uncertainty"]
        # set, not sorted: a mapping with a non-string key would raise a bare
        # TypeError out of sorted() instead of refusing by name.
        if not isinstance(port, dict) or set(port) != set(slots):
            return [
                f"uncertainty must be a mapping carrying exactly {slots!r}, got "
                f"{port!r}"
            ]
        problems = []
        for slot, _member in required:
            envelope = port[slot]
            if not isinstance(envelope, AttestedUncertainty):
                problems.append(
                    f"uncertainty.{slot} must be an AttestedUncertainty envelope "
                    "(dskit.pipeline.uncertainty_intake), got "
                    f"{type(envelope).__name__} — a bare number or mapping "
                    "carries no attestation and cannot be admitted"
                )
        if problems or bundle_problems:
            return problems
        demand = self.decision_demand(bundle)
        if demand is None:
            return problems
        for slot, member in required:
            # The FUNCTION, never the envelope's own method: a method is
            # resolved through the envelope's class, and that class is
            # exactly what capital has no reason to trust. admission_problems
            # reads the dskit registry, this node's own REQUIRED_INTAKES
            # class and the envelope's raw state instead (ADR-0165's
            # 2026-09-18 correction round).
            for problem in admission_problems(port[slot], demand, member):
                problems.append(f"uncertainty.{slot}: {problem}")
        if problems:
            # Fail closed in ORDER. The domain bindings below read artifact
            # fields (`lower_offset`, `pi_hat`) that only an ADMITTED
            # artifact is known to have; running them on a refused envelope
            # raised AttributeError instead of naming the refusal, which is
            # a crash where a refusal belongs.
            return problems
        return self._binding_problems(bundle, port)

    def _binding_problems(self, bundle, port):
        """Problems binding each bundle row to the admitted artifacts, empty when none."""
        problems = []
        for index, row in enumerate(bundle):
            entity = row["entity"]
            for slot, member in self.required_intakes():
                envelope = port[slot]
                # artifact_of/attestation_of, never the properties: a
                # consumer reads the SAME values the seam screened, rather
                # than whatever a class-level descriptor chooses to return.
                attested_id = attestation_of(envelope).artifact_id
                declared = row["uncertainty"][slot]
                if declared != attested_id:
                    problems.append(
                        f"bundle[{index}] ({entity!r}) names {slot} calibration "
                        f"{declared!r}, but the admitted artifact is {attested_id!r}"
                    )
                else:
                    problems.extend(
                        self._entity_problems(
                            index, row, slot, artifact_of(envelope)
                        )
                    )
        return problems

    @staticmethod
    def _entity_problems(index, row, slot, artifact):
        """Problems binding ONE row's numbers to ONE admitted artifact."""
        entity = row["entity"]
        where = f"bundle[{index}] ({entity!r})"
        if slot == "outcome":
            if entity not in artifact.lower_offset:
                return [
                    f"{where} has no calibrated outcome band — the admitted "
                    "realized-outcome artifact covers "
                    f"{sorted(artifact.lower_offset)!r}"
                ]
            return []
        if entity not in artifact.pi_hat:
            return [
                f"{where} has no entry in the admitted false-signal artifact, "
                f"which covers {sorted(artifact.pi_hat)!r}"
            ]
        problems = []
        for field, attested in (
            ("pi_hat", artifact.pi_hat[entity]),
            (HFDR_COEFFICIENT_FIELD, artifact.pi_widened[entity]),
        ):
            if float(row[field]) != float(attested):
                problems.append(
                    f"{where}.{field} {row[field]!r} does not match the admitted "
                    f"false-signal artifact's {attested!r} — the number the "
                    "capital program reads must be the number that was attested"
                )
        return problems

    def _intake_evidence(self, inputs):
        """Record the admitted-uncertainty provenance beside every decision."""
        demand = self.decision_demand(inputs["bundle"])
        port = inputs["uncertainty"]
        admitted = {}
        for slot, _member in self.required_intakes():
            attestation = attestation_of(port[slot])
            coverage = attestation.coverage
            admitted[slot] = {
                "artifact_id": attestation.artifact_id,
                "estimand": type(port[slot]).estimand(),
                "calibration_end_ms": attestation.calibration_end_ms,
                "known_at_ms": attestation.known_at_ms,
                "attested_measured_coverage": None if coverage is None else coverage.measured,
                "coverage_evidence_id": None if coverage is None else coverage.evidence_id,
            }
        return {
            "decision_ts": None if demand is None else demand.decision_ts_ms,
            "model_identity": None if demand is None else demand.model_identity,
            "max_calibration_age_ms": int(
                self.params["uncertainty_max_calibration_age_ms"]
            ),
            "min_measured_coverage": float(self.params["uncertainty_min_coverage"]),
            "admitted": admitted,
            # Stated at every decision so a reader of the evidence never has
            # to infer it: ADR-0088's row is fed a widened point estimate,
            # which does not make it hold with probability 1 - hfdr_q.
            "hfdr_coefficient": {
                "field": HFDR_COEFFICIENT_FIELD,
                "claim": "widened_point_estimate",
                "chance_constraint": False,
            },
        }

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
        mode_problems = self._bundle_mode_problems(bundle)
        if mode_problems:
            raise ValueError(f"{self.key}: " + "; ".join(mode_problems))
        survivors = set(inputs["survivors"])
        confirmed = ConfirmedCaps(inputs["cap"])
        max_notional = float(self.params["max_position_notional"])
        lot = int(self.params.get("lot_size", DEFAULT_LOT_SIZE))

        held = {k: int(v) for k, v in portfolio.get("positions", {}).items() if int(v) != 0}
        mark_prices = portfolio.get("mark_prices", {})
        by_name, routed_out, _codes = self._routed_rows(
            bundle, survivors, confirmed, portfolio["asof_ms"], horizon_of=lambda row: row["lead"]
        )

        names = sorted(set(by_name) | set(held))
        self._evidence = {
            "n_bundle_rows": len(bundle),
            "n_gated": len(by_name),
            "n_held": len(held),
            "routed_out": routed_out,
            "uncertainty": self._intake_evidence(inputs),
        }
        if not names:
            self._pi_widened, self._band_shares = {}, {}
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

        rows, pi_widened, band_shares, payoffs_r = {}, {}, {}, {}
        worst_r, best_r = 0.0, 0.0
        costs = SchwabCostModel({name: self.params[name] for name in SchwabCostModel._PARAMS})
        fill_ms = portfolio.get("fill_ms")
        fill_ms = fill_ms if isinstance(fill_ms, dict) else {}
        for name in names:
            row = by_name.get(name)
            h = held.get(name, 0)
            if row is None:
                exit_row = self._mandatory_exit_row(
                    name, h, mark_prices.get(name), len(shared_weights)
                )
                price, pi_widened_i = exit_row["price"], exit_row["pi_widened"]
                x_max, scenarios = exit_row["x_max"], exit_row["scenarios"]
            else:
                price = float(row["price"])
                pi_widened_i = float(row[HFDR_COEFFICIENT_FIELD])
                x_max = max_notional
                scenarios = [float(v) for v in row["scenarios"]]
            rows[name] = self._cost_row(costs, fill_ms, name, price, h, x_max)
            rows[name]["lot"] = lot
            pi_widened[name] = pi_widened_i
            payoffs_r[name] = scenarios
            if row is None:
                band_shares[name] = 0
            else:
                ticket = max(price * h, float(self.params["min_ticket"]))
                band_bps = float(self.params["band_bps"])
                band_shares[name] = lot * _ceil_div(band_bps * 1e-4 * ticket, price * lot)
            worst_r = min(worst_r, min(scenarios))
            best_r = max(best_r, max(scenarios))
        self._pi_widened, self._band_shares = pi_widened, band_shares
        self._payoffs = (shared_weights, payoffs_r)
        return names, rows, self._account_envelope(rows, portfolio, worst_r, best_r, 0.0)

    def _routed_rows(self, bundle, survivors, confirmed, asof_ms, horizon_of):
        """Route every bundle row through the gate, the cap, staleness and the price floor.

        Checks run in a fixed order and the first failure routes the row
        out: not a ``stat_test`` survivor, no confirmed cap, a zero cap,
        a horizon the cap does not confirm (:meth:`_cap_route_reason`), a
        row stale past ``bundle_max_staleness_ms`` or stamped after
        ``asof_ms``, a price below ``min_price``.

        Parameters
        ----------
        bundle : list of dict
            The validated bundle rows.
        survivors : set of str
            The ``stat_test`` survivors.
        confirmed : ConfirmedCaps
            The admitted confirmed-cap artifact.
        asof_ms : int
            The decision instant (``portfolio.asof_ms``).
        horizon_of : callable
            ``row -> int``, the horizon the cap check reads: the base kind
            passes the row's ``lead``, the joint kind its
            ``admitted_horizon``.

        Returns
        -------
        by_name : dict
            ``entity -> row`` for every row that enters the program.
        routed_out : dict
            ``entity -> reason`` for every row routed out, in the words the
            base kind's ``evidence["routed_out"]`` has always carried.
        routed_codes : dict
            ``entity -> code`` for the same rows, one member of
            :data:`ROUTE_PERMANENT`, :data:`ROUTE_TRANSIENT` or
            :data:`ROUTE_PRODUCER_FAULT` — what a policy keys on, never
            the text.
        """
        max_stale = int(self.params["bundle_max_staleness_ms"])
        min_price = float(self.params["min_price"])

        def route(row):
            """``(reason, code)`` for a row routed out, ``None`` for one kept."""
            entity = row["entity"]
            if entity not in survivors:
                return "not a stat_test survivor", "not_survivor"
            capped_horizon = confirmed.capped_horizon(entity)
            if capped_horizon is None:
                return "no confirmed cap for symbol", "no_cap"
            if capped_horizon == 0:
                return "zero confirmed cap", "zero_cap"
            reason = self._cap_route_reason(horizon_of(row), capped_horizon)
            if reason is not None:
                return reason, "cap_mismatch"
            age_ms = asof_ms - row["decision_ts"]
            if age_ms < 0 or age_ms > max_stale:
                code = "future" if age_ms < 0 else "stale"
                return f"bundle stale or from the future: age_ms={age_ms}", code
            if float(row["price"]) < min_price:
                return f"price {row['price']!r} below min_price {min_price!r}", "below_min_price"
            return None

        by_name, routed_out, routed_codes = {}, {}, {}
        for row in bundle:
            routed = route(row)
            if routed is None:
                by_name[row["entity"]] = row
            else:
                routed_out[row["entity"]], routed_codes[row["entity"]] = routed
        return by_name, routed_out, routed_codes

    @staticmethod
    def _cap_route_reason(horizon, capped_horizon):
        """Why a row at ``horizon`` fails a positive confirmed cap, or ``None``.

        The base kind routes out a lead ABOVE the cap, in the words
        ``evidence["routed_out"]`` has always carried.
        """
        if horizon > capped_horizon:
            return f"lead {horizon} is above confirmed cap {capped_horizon}"
        return None

    def _mandatory_exit_row(self, name, held, mark, n_scenarios):
        """Return the sell-only terms of a held name the program may no longer hold.

        No live belief exists for it, so the HFDR row must not bind it
        either: a zero coefficient keeps its ``(pi_i - q)`` term negative,
        and ``x_max = 0`` already forces ``q = 0`` whatever the HFDR row
        says. Its sale proceeds are certain, so its scenario returns are
        all zero.

        Parameters
        ----------
        name : str
            The held name.
        held : int
            Its held shares.
        mark : object
            The price it sells at; must be a finite number > 0.
        n_scenarios : int
            The batch's scenario count.

        Returns
        -------
        dict
            ``price``, ``pi_widened`` (0.0), ``x_max`` (0.0) and
            ``scenarios`` (``n_scenarios`` zeros).

        Raises
        ------
        ValueError
            ``mark`` is not a finite number > 0.
        """
        if not number_ok(mark) or mark <= 0.0:
            raise ValueError(
                f"{self.key}: {name!r} is held ({held} shares) but absent from the "
                "surviving bundle rows and portfolio.mark_prices carries no usable "
                f"price for it (got {mark!r}) — a mandatory exit needs a finite mark "
                "> 0 to trade against"
            )
        return {
            "price": float(mark),
            "pi_widened": 0.0,
            "x_max": 0.0,
            "scenarios": [0.0] * n_scenarios,
        }

    def _cost_row(self, costs, fill_ms, name, price, held, x_max):
        """One name's doorway row: its price, holding, bound and per-share costs.

        Uncapped TAF lives in :class:`SchwabCostModel` (see that class and
        the module docstring), never a size-referenced rate. The per-name
        half-spread, keyed on the name's FILL minute, is the SAME one the
        replay bills this name's fill at that minute.

        Parameters
        ----------
        costs : SchwabCostModel
            The cost model built from this node's params.
        fill_ms : dict
            ``portfolio.fill_ms``.
        name : str
            The name priced.
        price : float
            Its decision price.
        held : int
            Its held shares.
        x_max : float
            Its target-notional bound.

        Returns
        -------
        dict
            ``price``, ``held``, ``x_max``, ``cost_buy``, ``cost_sell`` and
            ``exit_cost_per_share``.

        Raises
        ------
        ValueError
            ``name`` has no ``portfolio.fill_ms`` instant.
        """
        if name not in fill_ms:
            raise ValueError(
                f"{self.key}: {name!r} has no portfolio.fill_ms instant — the "
                "time-of-day spread is keyed on the fill minute, so an unkeyed name "
                "cannot be priced"
            )
        spread = costs.buy_per_share(name, price, fill_ms[name])
        sell_cost = costs.sell_per_share(name, price, fill_ms[name])
        return {
            "price": price,
            "held": held,
            "x_max": x_max,
            "cost_buy": spread,
            "cost_sell": sell_cost,
            # Liquidating at the horizon pays the same sell-side costs
            # as an ordinary exit (§5.3's exit_cost_o(q)) — never left
            # at the doorway's zero default, or the CVaR cap and the
            # objective both silently price every position as
            # free-to-unwind.
            "exit_cost_per_share": sell_cost,
        }

    @staticmethod
    def _account_envelope(rows, portfolio, worst_r, best_r, cost_bound, carried_wealth=None):
        """Return the doorway's ``account``: the cash terms plus a padded wealth envelope.

        Parameters
        ----------
        rows : dict
            The doorway rows (``price``, ``held``, ``x_max``) of every name
            in the program.
        portfolio : dict
            The validated account state: ``cash``, ``buying_power`` and the
            optional ``sale_credit``, ``cash_reserve`` and ``gross_limit``.
        worst_r, best_r : float
            The most negative and the most positive scenario return the
            program can realize, each already bounded by zero on its own
            side. The caller computes them over whatever payoffs it built.
        cost_bound : float
            Dollars taken off the envelope's lower edge, inside its floor,
            for costs the return span does not already cover. Both kinds
            pass 0.
        carried_wealth : float or None
            Wealth held outside the program: a constant added to the
            wealth mark (so the envelope brackets the doorway's own
            ``w0_mark``) and passed on as ``account["carried_wealth"]``.
            ``None`` (the base kind) adds nothing and leaves the key out.

        Returns
        -------
        dict
            ``cash``, ``buying_power``, ``sale_credit``, ``cash_reserve``,
            ``gross_limit``, ``wealth_lo`` and ``wealth_hi``, plus
            ``carried_wealth`` when given.
        """
        carried = 0.0 if carried_wealth is None else float(carried_wealth)
        notional_cap = sum(r["x_max"] for r in rows.values())
        gross_limit = portfolio.get("gross_limit")
        if gross_limit is not None:
            notional_cap = min(notional_cap, float(gross_limit))
        w0_mark = float(portfolio.get("cash", 0.0)) + sum(
            r["price"] * r["held"] for r in rows.values()
        ) + carried
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
            "wealth_lo": max(w0_mark * 0.01, w0_mark - span - cost_bound),
            "wealth_hi": w0_mark + span,
        }
        if carried_wealth is not None:
            account["carried_wealth"] = carried
        return account

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

        HFDR (C7, ADR-0088, locked): ``sum_i (pi_i - q) * x_i <= 0`` —
        linear in the target notional the doorway already exposes as
        ``model.x``. ``pi_i`` is every gated row's
        :data:`HFDR_COEFFICIENT_FIELD`, taken from the ADMITTED
        false-signal artifact (``validate_inputs`` refuses a row whose
        number differs from the attested one). That number is a WIDENED
        POINT ESTIMATE, so this row does not hold with probability
        ``1 - q``: ADR-0152 measured 0.53-0.82 attainment against a 0.95
        nominal and withdrew the bound claim. ``run`` records the reading
        in its ``evidence`` output rather than leaving a reader to assume
        a chance constraint.

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
            expr=sum((self._pi_widened[i] - q) * model.x[i] for i in names) <= 0
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
                "uncertainty": self._intake_evidence(inputs),
            }
            if self.params.get("cap_evidence_look_ahead", False) is True:
                out["evidence"]["cap_evidence_look_ahead"] = CAP_LOOK_AHEAD_DISCLOSURE
            # The doorway's record of this solve (ADR-0183 phase 2); None on
            # the empty-gate short circuit, which never wakes the solver.
            record = self.solve_record
            out["evidence"]["solve"] = None if record is None else record.to_obj()
            return out
        finally:
            self._pi_widened = self._band_shares = self._payoffs = None
            self._evidence = None


class JointEquityKellyMIO(EquityKellyMIO):
    """Size every held and admitted name in ONE per-minute solve over exit-horizon tranches.

    The ``intraday_equities-joint-kelly-mio`` kind: formulation B of
    ADR-0188
    (``children/intraday_equities/docs/research/mio-joint-policy/2026-09-26-from-scratch-formulation.md``).
    Every minute it takes the whole shares carried and the cash, plus each
    name's forecast PATH, and chooses whole shares to buy or sell now
    (filled at the next bar; ``q_i = h_i + b_i - s_i >= 0``): sells and
    trims are ordinary decisions. A name's target is split across its exit
    horizons ``k = 1..K_i`` (``K_i`` is the row's ``plan_horizon``) by
    continuous tranches ``e_i(k) >= 0`` summing to ``q_i``, an expectation
    of WHEN the shares are sold, never an order, and tranche ``k`` is
    valued at the name's step-``k`` scenario returns (the doorway's tranche
    mechanism). Terminal wealth per scenario is the cash after this
    minute's costed trades, plus every tranche at ``p_i (1 + r_io(k)) -
    kappa^x_i``, plus ``carried_wealth``; the objective is the doorway's
    tangent-linearized CRRA with its one CVaR row. The next minute's
    economic inputs come only from the new replay state; a persistent
    solver may retain the earlier optimal variable values solely as a
    numerical warm-start hint.

    Kept from :class:`EquityKellyMIO`: every input check (the bundle
    contract, the provenance pins, the cap, the attested uncertainty), the
    Schwab cost model keyed on the fill minute, the routing and its
    evidence strings, the mandatory-exit route, and the doorway's objective
    and CVaR row. Changed:

    * **No hard caps.** The knobs in :data:`JOINT_REFUSED_PARAMS` are
      refused by name; the doorway gets no cardinality row and a zero
      minimum ticket. Inaction emerges from the costs the objective
      charges (owner rulings 2026-09-25).
    * **The per-name bound is the no-leverage bound.** ``x_max_i =
      max(G, p_i h_i)``. ``G`` is ``portfolio.gross_limit`` when declared
      (the replay passes NAV; the doorway's gross row already holds the
      sum of target notionals under it, so a per-name bound of ``G`` is
      never tighter than that row); when ``gross_limit`` is null, ``G`` is
      ``buying_power + sum_i p_i h_i`` over the names in the solve, the
      most notional any one name can reach when every other held name is
      sold and all buying power spent (sale credit at most 1), so the row
      only hands the doorway a finite bound. A holding is therefore never
      forced to sell by its own row. The account's aggregate bound is the
      no-leverage bound, NAV with cash >= 0, so the doorway's gross row
      never binds the no-trade point either: a declared ``gross_limit``
      below the marked holdings of the LIVE names in the solve would make
      that row trim a wanted position, and refuses the minute by name
      instead (ADR-0188 F(a)). A mandatory exit's own mark is excluded
      from that check: its ``x_max`` is pinned to 0
      (:meth:`_mandatory_exit_row`), so it is never forced to sell BY ITS
      OWN ROW and can never bind the doorway's gross row regardless of
      ``gross_limit`` — charging its mark against the limit would refuse
      a minute the real solve could always fund by liquidating it in
      full. Only the priced objective and the risk rows the owner kept
      (CVaR, HFDR) move a holding.
    * **HFDR per tranche.** ADR-0088's row applied to each tranche:
      ``sum_i sum_k (pi_widened_i(k) - q) p_i e_i(k) <= 0``, reading
      :data:`HFDR_PATH_FIELD`. No band rows.
    * **Routing classification.** A routed-out HELD name is classed by
      its structured code, never its text. Permanent
      (:data:`ROUTE_PERMANENT`): a mandatory exit, sold in full now.
      Transient (:data:`ROUTE_TRANSIENT`): skipped this minute, its
      position kept, its ``shares x mark`` added to ``carried_wealth``.
      A producer fault (:data:`ROUTE_PRODUCER_FAULT`) on ANY row, held or
      not, refuses the whole minute: nothing trades. A held name with no
      row at all is a mandatory exit, as in the base kind.

    Inputs are the base kind's with three differences: every bundle row
    must be an assembled PATH row carrying :data:`JOINT_PATH_OUTPUTS` (its
    flat fields are its first step); the ``uncertainty`` port carries a
    path release's PER-CELL artifacts (``ForecastPublisher.path_envelopes``,
    keyed ``SYM:hNN``), admitted by the base kind's intake screens and
    bound to each row per cell (:meth:`_entity_problems`); and
    ``portfolio`` may carry ``carried_wealth`` (finite, >= 0): the mark of
    positions the caller left out of ``positions`` this minute, such as a
    name with a queued order. A mark comes from ``portfolio.mark_prices``
    first (the replay marks its NAV, and so the ``gross_limit`` it passes,
    there), else from the name's own bundle row.

    Parameters
    ----------
    params : dict
        :class:`EquityKellyMIO`'s params minus :data:`JOINT_REFUSED_PARAMS`.
        A declared solver time limit (``solver_options: {"time_limit":
        seconds}`` under appsi_highs) is a halt: a solve that ends without
        an optimal solution refuses by name and nothing trades.

    Notes
    -----
    Outputs are the base kind's. ``target`` maps each name in the solve to
    its whole target shares (zero targets omitted; a skipped name is absent
    and keeps its shares); ``trades`` maps each name that trades to
    ``{"buy": int, "sell": int}``, never both sides; ``metrics`` carries the
    doorway's ``tranches``; ``evidence`` carries the base kind's keys plus
    ``routed_codes``, ``carried_wealth`` (the total constant: the caller's
    plus every skipped position's), ``skipped_transient``,
    ``mandatory_exits``, ``plan_horizons``, ``no_leverage_bound`` (``G``)
    and ``tranches``. Disclosed, not modelled here: a later tranche's exit
    cost is priced at this minute's fill instant, not at its own exit
    minute; and the per-cell measured coverage the publisher screens each
    plan against rides in the release, not in the attested artifact, so
    this node admits the band on its one artifact-level coverage and takes
    the row's ``plan_horizon`` as screened.

    Examples
    --------
    One two-tranche path row and no position (``params`` are the base
    kind's reference params without the refused knobs)::

        node = JointEquityKellyMIO("size", params)
        out = node.run(ctx, {"bundle": path_rows, "portfolio": portfolio,
                             "survivors": ["AAPL"], "cap": cap, "uncertainty": port})
        out["trades"]                # {"AAPL": {"buy": 52, "sell": 0}}
        out["evidence"]["tranches"]  # {"AAPL": [0.0, 52.0]}
    """

    _PARAMS = tuple(name for name in EquityKellyMIO._PARAMS if name not in JOINT_REFUSED_PARAMS) + (
        "mean_uncertainty_budget",
        "mean_deviation_multipliers",
    )
    _mean_uncertainty = None
    _PERSISTENT_MODEL_REUSE = True

    def _persistent_params(self):
        """Return the joint kind's fixed doorway policy for cached model preparation."""
        return {**self.params, **_JOINT_DOORWAY_PARAMS}

    def _persistent_domain_signature(self, inputs, prepared, params):
        """HFDR's structure is fixed by the base key's names/tranches."""
        del inputs, prepared
        return ("joint_hfdr", float(params["hfdr_q"]))

    def _rebind_bundle(self, rows):
        """Bind this release-local node to the current minute's authenticated rows."""
        self.params["bundle_artifact_sha256"] = ForecastBundle.digest(rows)

    def _refresh_domain_constraints(self, model, inputs, params):
        """Refresh every per-tranche HFDR coefficient on a persistent hit."""
        del inputs
        model.input_hfdr_q.set_value(float(params["hfdr_q"]))
        for name in model._scn["names"]:
            rates = self._pi_widened[name]
            for k, rate in enumerate(rates):
                model.input_hfdr_rate[name, k].set_value(float(rate))

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Each knob in :data:`JOINT_REFUSED_PARAMS` refuses by name with its
        reason; every other check is the base kind's own (the doorway's
        knobs, costs, ``hfdr_q``, provenance, the intake policy), with the
        doorway's required gating knobs supplied as
        ``cardinality: null, min_ticket: 0``.
        """
        problems = [
            f"{name} is refused by the joint kind: {reason}"
            for name, reason in JOINT_REFUSED_PARAMS.items()
            if name in params
        ]
        kept = {name: value for name, value in params.items() if name not in JOINT_REFUSED_PARAMS}
        reject_unknown_params(problems, kept, cls._PARAMS)
        doorway = {name: value for name, value in kept.items() if name in ScenarioUtilitySolve._PARAMS}
        problems.extend(ScenarioUtilitySolve.validate_params({**doorway, **_JOINT_DOORWAY_PARAMS}))
        problems.extend(cls._cost_problems(kept))
        problems.extend(cls._hfdr_problems(kept))
        problems.extend(cls._provenance_problems(kept))
        problems.extend(cls._intake_policy_problems(kept))
        if "mean_uncertainty_budget" not in kept:
            problems.append(
                "mean_uncertainty_budget is required — the Bertsimas-Sim budget has no default"
            )
        elif not number_ok(kept["mean_uncertainty_budget"]) or kept["mean_uncertainty_budget"] <= 0.0:
            problems.append(
                "mean_uncertainty_budget must be a finite number > 0, got "
                f"{kept['mean_uncertainty_budget']!r}"
            )
        multipliers = kept.get("mean_deviation_multipliers")
        if multipliers is None:
            problems.append(
                "mean_deviation_multipliers is required — per-name kappa values have no default"
            )
        elif not isinstance(multipliers, dict) or not multipliers:
            problems.append(
                "mean_deviation_multipliers must be a non-empty mapping of name -> finite number > 0"
            )
        else:
            for name, value in multipliers.items():
                if not isinstance(name, str) or not name:
                    problems.append(
                        "mean_deviation_multipliers keys must be non-empty strings, "
                        f"got {name!r}"
                    )
                elif not number_ok(value) or value <= 0.0:
                    problems.append(
                        f"mean_deviation_multipliers[{name!r}] must be a finite number > 0, "
                        f"got {value!r}"
                    )
        return problems

    @classmethod
    def required_intakes(cls):
        """Add the path release's attested family of mean-confidence intervals."""
        return super().required_intakes() + (("mean", AttestedMeanConfidenceFamily),)

    def _bundle_mode_problems(self, bundle):
        """Problems with the bundle's MODE for this kind, empty when none.

        The joint kind sizes every name from its exit-horizon path, so a
        FLAT row (no path outputs) or a malformed path refuses by name
        (:func:`_path_output_problems`). The base kind runs this before its
        digest pin, which cannot hash a non-finite number.
        """
        return _path_output_problems(bundle)

    def validate_inputs(self, inputs):
        """Problems with the materialized ``inputs``, empty when none.

        The base kind's checks (a flat row or a malformed path refuses by
        name first, through :meth:`_bundle_mode_problems`), plus
        ``portfolio.carried_wealth`` (finite, >= 0, when given).
        """
        problems = super().validate_inputs(inputs)
        portfolio = inputs.get("portfolio")
        if isinstance(portfolio, dict) and "carried_wealth" in portfolio:
            carried = portfolio["carried_wealth"]
            if not number_ok(carried) or carried < 0.0:
                problems.append(
                    f"portfolio.carried_wealth must be a finite number >= 0 when given, "
                    f"got {carried!r} — the mark of positions held outside this solve, "
                    "never cash"
                )
        bundle = inputs.get("bundle")
        multipliers = self.params["mean_deviation_multipliers"]
        if isinstance(bundle, (list, tuple)):
            names = sorted(
                row["entity"]
                for row in bundle
                if isinstance(row, dict) and isinstance(row.get("entity"), str)
                and row["entity"] not in multipliers
            )
            if names:
                problems.append(
                    "mean_deviation_multipliers has no kappa for bundle name(s) "
                    f"{names!r}"
                )
            if isinstance(portfolio, dict) and isinstance(portfolio.get("positions", {}), dict):
                held = portfolio.get("positions", {})
                unheld = sorted(
                    row["entity"]
                    for row in bundle
                    if isinstance(row, dict)
                    and row.get("plan_horizon") == 0
                    and not (number_ok(held.get(row.get("entity"), 0)) and held.get(row.get("entity"), 0) > 0)
                )
                if unheld:
                    problems.append(
                        "a zero plan is authenticated mandatory-exit evidence only for a held "
                        f"positive-share name; unheld row(s) {unheld!r} refuse"
                    )
        return problems

    @staticmethod
    def _entity_problems(index, row, slot, artifact):
        """Problems binding ONE path row to ONE admitted artifact, per cell.

        A path release's artifacts are keyed per cell ``SYM:hNN``, not per
        entity (``ForecastPublisher.path_envelopes``). For a row admitting
        ``H`` leads, step ``k`` (1-based) binds to:

        * the false-signal cell ``SYM:h{min(k, H):02d}``, for EVERY step the
          row carries: ``pi_hat_path[k-1]`` and ``pi_widened_path[k-1]``
          must equal the admitted artifact's numbers there (past the cap the
          last admitted cell's rates carry forward, exactly as
          ``ForecastBundle`` assembles them, since nothing is calibrated
          past the cap);
        * the outcome band's cell ``SYM:h{k:02d}``, for every step the solve
          reads (``k <= plan_horizon``): the band must cover it.

        The flat fields are step 1 (:func:`_path_output_problems`), so they
        bind to ``SYM:h01`` through the path. An entity-keyed artifact (the
        base kind's lead-group shape) carries no cell and refuses here.
        """
        entity = row["entity"]
        where = f"bundle[{index}] ({entity!r})"
        if slot == "outcome":
            missing = [
                _path_cell_id(entity, k)
                for k in range(1, int(row["plan_horizon"]) + 1)
                if _path_cell_id(entity, k) not in artifact.lower_offset
            ]
            if missing:
                return [
                    f"{where} has no calibrated outcome band for cell(s) {missing} — the "
                    "admitted realized-outcome artifact covers "
                    f"{sorted(artifact.lower_offset)!r}"
                ]
            return []
        if slot == "mean":
            problems = []
            admitted = int(row["admitted_horizon"])
            sigma = float(row["sigma_t"])
            for k in range(1, len(row["scenarios_path"]) + 1):
                cell = _path_cell_id(entity, min(k, admitted))
                if cell not in artifact.members:
                    problems.append(
                        f"{where} step {k} has no cell {cell!r} in the admitted mean "
                        f"confidence family, which covers {sorted(artifact.members)!r}"
                    )
                    continue
                interval = artifact.members[cell]
                scale = 1.0 if k <= admitted else math.sqrt(admitted / k)
                center = float(row["mu_gross_path"][k - 1])
                label_center = math.log1p(center) / (sigma * math.sqrt(k))
                lower = math.expm1(
                    (label_center + (interval.low - interval.mean) * scale)
                    * sigma * math.sqrt(k)
                )
                upper = math.expm1(
                    (label_center + (interval.high - interval.mean) * scale)
                    * sigma * math.sqrt(k)
                )
                haircut = 1.0 - float(row["pi_hat_path"][k - 1])
                expected = {
                    "mean_deviation_below_path": haircut * (center - lower),
                    "mean_deviation_above_path": haircut * (upper - center),
                }
                for field, wanted in expected.items():
                    actual = float(row[field][k - 1])
                    if not math.isclose(actual, wanted, rel_tol=1e-12, abs_tol=1e-15):
                        problems.append(
                            f"{where}.{field}[{k - 1}] {actual!r} does not match "
                            f"the admitted mean interval for cell {cell!r}: {wanted!r}"
                        )
            return problems
        problems = []
        admitted = int(row["admitted_horizon"])
        for k in range(1, len(row["scenarios_path"]) + 1):
            cell = _path_cell_id(entity, min(k, admitted))
            if cell not in artifact.pi_hat:
                problems.append(
                    f"{where} step {k} has no cell {cell!r} in the admitted false-signal "
                    f"artifact, which covers {sorted(artifact.pi_hat)!r}"
                )
                continue
            for field, attested in (
                ("pi_hat_path", artifact.pi_hat[cell]),
                (HFDR_PATH_FIELD, artifact.pi_widened[cell]),
            ):
                value = row[field][k - 1]
                if float(value) != float(attested):
                    problems.append(
                        f"{where}.{field}[{k - 1}] {value!r} does not match the admitted "
                        f"false-signal artifact's {attested!r} for cell {cell!r} — the "
                        "number the capital program reads must be the number that was "
                        "attested"
                    )
        return problems

    def instruments(self, inputs):
        """Classify, route and price the path rows -> ``(names, rows, account)``.

        Raises
        ------
        ValueError
            A row lacks or malforms its path outputs; a producer-fault route
            (a row from the future, a cap mismatch) on any row; a skipped or
            exiting held name with no usable mark; a declared
            ``gross_limit`` below the marked holdings of the LIVE names in
            the solve (the no-leverage bound cannot sit under them; a
            mandatory exit's own mark is excluded, since its target is
            already pinned to zero and it can never bind that row); a
            priced name with no fill instant.
        """
        self._mean_uncertainty = None
        bundle = inputs["bundle"]
        portfolio = inputs["portfolio"]
        problems = self._bundle_mode_problems(bundle)
        if problems:
            raise ValueError(f"{self.key}: " + "; ".join(problems))
        held = {k: int(v) for k, v in portfolio.get("positions", {}).items() if int(v) != 0}
        mark_prices = portfolio.get("mark_prices", {})
        rows_of = {row["entity"]: row for row in bundle}
        by_name, routed_out, routed_codes = self._routed_rows(
            bundle,
            set(inputs["survivors"]),
            ConfirmedCaps(inputs["cap"]),
            portfolio["asof_ms"],
            horizon_of=lambda row: row["admitted_horizon"],
        )
        for name in sorted(
            name for name, row in by_name.items() if int(row["plan_horizon"]) == 0
        ):
            del by_name[name]
            routed_out[name] = "authenticated path has an empty screened plan"
            routed_codes[name] = "empty_plan"
        faults = sorted(name for name, code in routed_codes.items() if code in ROUTE_PRODUCER_FAULT)
        if faults:
            raise ValueError(
                f"{self.key}: producer fault, so the minute's solve refuses and nothing "
                "trades — "
                + "; ".join(f"{name}: {routed_codes[name]} ({routed_out[name]})" for name in faults)
            )

        carried = float(portfolio.get("carried_wealth", 0.0))
        skipped = {}
        for name in sorted(held):
            if routed_codes.get(name) not in ROUTE_TRANSIENT:
                continue
            # A routed code comes from the name's own row, so a mark exists.
            mark, source = self._mark_of(name, rows_of.get(name), mark_prices)
            skipped[name] = {
                "code": routed_codes[name], "shares": held[name], "mark": mark, "mark_source": source,
            }
            carried += held[name] * mark
        names = sorted(set(by_name) | (set(held) - set(skipped)))
        self._evidence = {
            "n_bundle_rows": len(bundle),
            "n_gated": len(by_name),
            "n_held": len(held),
            "routed_out": routed_out,
            "uncertainty": self._intake_evidence(inputs),
            "routed_codes": routed_codes,
            "carried_wealth": carried,
            "skipped_transient": skipped,
            "mandatory_exits": {},
            "plan_horizons": {name: int(by_name[name]["plan_horizon"]) for name in sorted(by_name)},
            "no_leverage_bound": None,
        }
        if not names:
            # Wealth outside the solve is still wealth on the empty gate: the
            # doorway reports it in wealth_min/wealth_max.
            self._pi_widened, self._band_shares = {}, {}
            return [], {}, {"cash": float(portfolio.get("cash", 0.0)), "carried_wealth": carried}

        # One certain scenario when only mandatory exits remain (their sale
        # proceeds are riskless), as in the base kind.
        shared_weights = list(next(iter(by_name.values()))["weights"]) if by_name else [1.0]
        exits, exit_rows, prices = {}, {}, {}
        for name in names:
            if name in by_name:
                prices[name] = float(by_name[name]["price"])
                continue
            mark, source = self._mark_of(name, rows_of.get(name), mark_prices)
            exit_rows[name] = self._mandatory_exit_row(name, held[name], mark, len(shared_weights))
            prices[name] = exit_rows[name]["price"]
            exits[name] = {
                "code": routed_codes.get(name, "no_row"),
                "shares": held[name],
                "mark": prices[name],
                "mark_source": source,
            }
        gross_limit = portfolio.get("gross_limit")
        held_mark = sum(prices[name] * held.get(name, 0) for name in names)
        # The refusal below reads ONLY the live (`by_name`) names: a
        # mandatory exit's x_max is pinned to 0 (`_mandatory_exit_row`), so
        # `elig_hi` forces `model.x[name] == 0` no matter what and it can
        # never bind the doorway's `gross_exposure` row (`sum_i model.x[i]
        # <= gross_limit`) — charging its mark against `gross_limit` would
        # refuse a minute the real solve can always fund by liquidating it
        # in full. `held_mark` above still counts it for the `bound`
        # fallback below, where that is correct: its sale proceeds really
        # do fund a live name's ceiling this same minute.
        live_held_mark = sum(
            prices[name] * held.get(name, 0) for name in names if name in by_name
        )
        if gross_limit is not None and float(gross_limit) < live_held_mark * (
            1.0 - _GROSS_LIMIT_REL_TOLERANCE
        ):
            listing = ", ".join(
                f"{name} {held[name]} x {prices[name]!r}"
                for name in names
                if name in by_name and held.get(name, 0)
            )
            raise ValueError(
                f"{self.key}: portfolio.gross_limit {float(gross_limit)!r} is below the "
                f"marked holdings of the LIVE names in the solve, {live_held_mark!r} "
                f"({listing}), shortfall {live_held_mark - float(gross_limit)!r} — the "
                "no-leverage bound is below the marked holdings; the joint kind never "
                "trims a holding to fit an aggregate limit, ADR-0188 F(a): the aggregate "
                "bound must be the no-leverage bound, NAV with cash >= 0. A mandatory "
                "exit's own mark is excluded from this check: its x_max is already pinned "
                "to 0, so it is never forced to sell BY ITS OWN ROW and can never bind the "
                "gross_exposure row regardless of gross_limit"
            )
        if gross_limit is not None:
            bound = float(gross_limit)
        else:
            bound = float(portfolio.get("buying_power", 0.0)) + held_mark
        bound = max(bound, 0.0)

        rows, coefficients, payoffs_r = {}, {}, {}
        worst_r, best_r = 0.0, 0.0
        costs = SchwabCostModel({name: self.params[name] for name in SchwabCostModel._PARAMS})
        fill_ms = portfolio.get("fill_ms")
        fill_ms = fill_ms if isinstance(fill_ms, dict) else {}
        for name in names:
            h = held.get(name, 0)
            row = by_name.get(name)
            if row is None:
                exit_row = exit_rows[name]
                x_max, path, rates = exit_row["x_max"], [exit_row["scenarios"]], [exit_row["pi_widened"]]
            else:
                steps = int(row["plan_horizon"])
                x_max = max(bound, prices[name] * h)
                path = [[float(v) for v in step] for step in row["scenarios_path"][:steps]]
                rates = [float(v) for v in row[HFDR_PATH_FIELD][:steps]]
            rows[name] = self._cost_row(costs, fill_ms, name, prices[name], h, x_max)
            coefficients[name] = rates
            payoffs_r[name] = path[0] if len(path) == 1 else path
            worst_r = min(worst_r, min(min(step) for step in path))
            best_r = max(best_r, max(max(step) for step in path))
        if by_name:
            multipliers = self.params["mean_deviation_multipliers"]
            below = {}
            above = {}
            for name, row in sorted(by_name.items()):
                steps = int(row["plan_horizon"])
                kappa = float(multipliers[name])
                below[name] = [
                    kappa * float(value)
                    for value in row["mean_deviation_below_path"][:steps]
                ]
                above[name] = [
                    kappa * float(value)
                    for value in row["mean_deviation_above_path"][:steps]
                ]
            self._mean_uncertainty = {
                "budget": float(self.params["mean_uncertainty_budget"]),
                "deviation_below": below,
                "deviation_above": above,
            }
        self._pi_widened, self._band_shares = coefficients, {}
        self._payoffs = (shared_weights, payoffs_r)
        self._evidence["mandatory_exits"] = exits
        self._evidence["no_leverage_bound"] = bound
        robust_bound = 0.0
        if self._mean_uncertainty is not None:
            impacts = {
                name: float(rows[name]["x_max"])
                * max(self._mean_uncertainty["deviation_below"][name])
                for name in self._mean_uncertainty["deviation_below"]
            }
            impacts = {name: value for name, value in impacts.items() if value > 0.0}
            if impacts:
                robust_bound = BudgetedMeanSet(
                    nominal={name: 0.0 for name in impacts},
                    deviation_below=impacts,
                    deviation_above=impacts,
                    budget=min(float(self.params["mean_uncertainty_budget"]), len(impacts)),
                ).protection({name: 1.0 for name in impacts})
        account = self._account_envelope(
            rows, portfolio, worst_r, best_r, robust_bound, carried_wealth=carried
        )
        return names, rows, account

    def mean_uncertainty(self, inputs):
        """Return this minute's name-budgeted, kappa-scaled mean deviations."""
        del inputs
        return self._mean_uncertainty

    @staticmethod
    def _cap_route_reason(horizon, capped_horizon):
        """Why a path row's ``admitted_horizon`` fails a positive cap, or ``None``.

        A path row's admitted horizon IS the gate's confirmed cap (both come
        from the same gate artifact), so any difference, above or below, is
        a producer inconsistency: the joint kind refuses the minute on it.
        """
        if horizon != capped_horizon:
            return f"admitted_horizon {horizon} differs from confirmed cap {capped_horizon}"
        return None

    @staticmethod
    def _mark_of(name, row, mark_prices):
        """Return the price a held name is marked or sold at, as ``(mark, source)``.

        ``portfolio.mark_prices`` first: the replay marks its NAV, and so
        the ``gross_limit`` it hands the solve, at those prices. The name's
        own bundle row otherwise (a routed-out row still carries its
        validated decision price). ``(None, None)`` when neither exists.
        """
        mark = mark_prices.get(name)
        if number_ok(mark) and mark > 0.0:
            return float(mark), "mark_prices"
        if row is not None:
            return float(row["price"]), "bundle_row"
        return None, None

    def build_model(self, inputs, params):
        """Build the doorway's program with no cardinality row and no minimum ticket."""
        return super().build_model(inputs, {**params, **_JOINT_DOORWAY_PARAMS})

    def domain_constraints(self, model, inputs, params):
        """Add ADR-0088's HFDR row once per exit-horizon tranche; no band rows.

        ``sum_i sum_k (pi_widened_i(k) - q) * p_i * e_i(k) <= 0``. A name
        with one tranche contributes ``(pi_widened_i(1) - q) * x_i``, its
        target notional (``model.x``); a name with ``K > 1`` tranches
        contributes each ``model.e[i, k]`` at the price, at its own lead's
        widened rate. A mandatory exit's coefficient is 0 and its target 0.
        Like the base kind's row, this reads a widened POINT ESTIMATE
        (:data:`HFDR_PATH_FIELD`), so it is not a chance constraint.
        """
        from pyomo.environ import Constraint, Param

        tranches = model._scn["tranches"]
        rate_ix = []
        initial_rates = {}
        q = float(params["hfdr_q"])
        for name in model._scn["names"]:
            rates = self._pi_widened[name]
            if len(rates) != tranches[name]:
                raise RuntimeError(
                    f"{self.key}: {name!r} carries {len(rates)} HFDR rates for "
                    f"{tranches[name]} tranches — the payoff and the HFDR row must be "
                    "built from the same path"
                )
            for k, rate in enumerate(rates):
                rate_ix.append((name, k))
                initial_rates[name, k] = float(rate)

        model.input_hfdr_q = Param(mutable=True, initialize=q)
        model.input_hfdr_rate = Param(
            rate_ix,
            mutable=True,
            initialize=lambda m, name, k: initial_rates[name, k],
        )
        terms = []
        for name in model._scn["names"]:
            if tranches[name] == 1:
                terms.append(
                    (model.input_hfdr_rate[name, 0] - model.input_hfdr_q) * model.x[name]
                )
            else:
                terms.extend(
                    (model.input_hfdr_rate[name, k] - model.input_hfdr_q)
                    * model.input_price[name]
                    * model.e[name, k]
                    for k in range(tranches[name])
                )
        model.hfdr = Constraint(expr=sum(terms) <= 0)

    def run(self, ctx, inputs):
        """Solve, then attach the joint evidence, the tranche split included."""
        out = super().run(ctx, inputs)
        out["evidence"]["tranches"] = {
            name: list(split) for name, split in out["metrics"].get("tranches", {}).items()
        }
        return out


#: kind name -> class: what the registry, the conformance suite, and a
#: document's ``uses`` all key off.
NODE_KINDS = {
    "intraday_equities-kelly-mio": EquityKellyMIO,
    "intraday_equities-joint-kelly-mio": JointEquityKellyMIO,
}

# Import = registration (``owned`` deliberately NOT set — see CLAUDE.md).
for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)
