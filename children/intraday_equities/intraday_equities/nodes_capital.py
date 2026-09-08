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
from dskit.pipeline.node import check_int_param, register_node_kind
from dskit.pipeline.records import number_ok

__all__ = [
    "BUNDLE_FIELDS",
    "DEFAULT_LOT_SIZE",
    "EquityKellyMIO",
    "NODE_KINDS",
]

#: What every surviving bundle row must carry — Path A18256's fail-closed
#: contract (docs/plans/2026-09-intraday-equities-mio.md §7), narrowed to
#: the fields this scoped build actually READS: this pass sizes off the
#: scenario-return rows directly and does not re-derive them from
#: ``mu_gross``/``mu_net``/``cost_reference``/model-hash provenance, so
#: those stay out of this tuple rather than being required-but-unread. A
#: later, fuller bundle reader that DOES consume them extends this tuple;
#: it is additive, not a breaking change.
BUNDLE_FIELDS = (
    "entity",
    "decision_ts",
    "price",
    "pi_upper",
    "weights",
    "scenarios",
)

#: Smallest position a name may hold if it holds one at all, absent a
#: declared ``lot_size`` — a mechanism default (accuracy/speed), never a
#: risk number, so unlike the doorway's owner-only knobs this one is safe
#: to default.
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
        weights = row["weights"]
        scenarios = row["scenarios"]
        if not isinstance(weights, (list, tuple)) or not weights:
            problems.append(f"bundle[{i}] ({entity!r}).weights must be a non-empty list")
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
        elif isinstance(weights, (list, tuple)) and len(scenarios) != len(weights):
            problems.append(
                f"bundle[{i}] ({entity!r}): scenarios length {len(scenarios)} != weights "
                f"length {len(weights)}"
            )
        if not number_ok(row.get("price")) or row["price"] <= 0.0:
            problems.append(f"bundle[{i}] ({entity!r}).price must be a finite number > 0")
        if not number_ok(row.get("pi_upper")) or not 0.0 <= row["pi_upper"] <= 1.0:
            problems.append(f"bundle[{i}] ({entity!r}).pi_upper must be a finite number in [0, 1]")
    return problems


class EquityKellyMIO(ScenarioUtilitySolve):
    """Size intraday_equities' capital step from a forecast bundle plus portfolio state.

    The ``intraday_equities-kelly-mio`` kind. Inputs: ``bundle`` (a
    materialized list of per-candidate forecast rows, see
    :data:`BUNDLE_FIELDS`), ``portfolio`` (account state — ``asof_ms``,
    ``cash``, ``buying_power``, ``positions`` (``symbol -> held shares``),
    optional ``mark_prices`` for a held name the bundle dropped, optional
    ``cash_reserve``/``gross_limit``/``sale_credit``), and ``survivors``
    (the ``stat_test`` gate REQUIRED by the planner's capital rule — only
    bundle rows whose ``entity`` is a survivor enter the program).

    Parameters
    ----------
    params : dict
        The doorway's knobs (``risk_aversion_gamma``, ``n_tangents``,
        ``n_scenarios_max``, ``cvar_alpha``, ``cvar_limit``, ``cardinality``,
        ``min_ticket``, ``solver``, ``solver_options``) plus this kind's own:
        ``spread_bps`` (required, >= 0 — half-spread charged on entry AND
        exit, both sides), ``taf_per_share`` / ``taf_cap`` (required, >= 0 —
        FINRA TAF, sell-only), ``sec31_bps`` (required, >= 0 — SEC Section 31,
        sell-only), ``min_price`` (required, > 0 — the per-share TAF-argument
        floor, §3.4), ``hfdr_q`` (required, in (0, 1) — ADR-0088's false-
        discovery threshold), ``band_bps`` (required, >= 0 — the no-trade
        band as basis points of the larger of current ticket or
        ``min_ticket``), ``max_position_notional`` (required, > 0 — a
        UNIFORM per-name dollar ceiling; a bundle-declared per-name cap is a
        follow-up, not built here), ``bundle_max_staleness_ms`` (required,
        >= 0), ``lot_size`` (int >= 1, default :data:`DEFAULT_LOT_SIZE`).

    Examples
    --------
    One name, no prior position, half-Kelly-ish risk aversion::

        node = EquityKellyMIO("size", {
            "risk_aversion_gamma": 2.0, "n_tangents": 32, "n_scenarios_max": 256,
            "cvar_alpha": 0.95, "cvar_limit": 5000.0, "cardinality": 5,
            "min_ticket": 500.0, "spread_bps": 2.2, "taf_per_share": 0.000195,
            "taf_cap": 9.79, "sec31_bps": 0.0206, "min_price": 5.0,
            "hfdr_q": 0.10, "band_bps": 10.0, "max_position_notional": 5000.0,
            "bundle_max_staleness_ms": 5000,
        })
    """

    role = "capital"
    outputs = ("target", "trades", "cash_after", "metrics", "evidence")

    _PARAMS = ScenarioUtilitySolve._PARAMS + (
        "spread_bps",
        "taf_per_share",
        "taf_cap",
        "sec31_bps",
        "min_price",
        "hfdr_q",
        "band_bps",
        "max_position_notional",
        "bundle_max_staleness_ms",
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
        for name in ("spread_bps", "taf_per_share", "taf_cap", "sec31_bps"):
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
        check_int_param(problems, "lot_size", params.get("lot_size", DEFAULT_LOT_SIZE), ge=1)
        return problems

    def validate_inputs(self, inputs):
        """Problems with the materialized ``inputs``, empty when none."""
        problems = list(_bundle_problems(inputs.get("bundle")))
        portfolio = inputs.get("portfolio")
        if not isinstance(portfolio, dict):
            problems.append(
                f"portfolio must be a mapping of account state, got {type(portfolio).__name__}"
            )
        else:
            if not number_ok(portfolio.get("asof_ms")):
                problems.append("portfolio.asof_ms must be a finite number (epoch ms)")
            if not number_ok(portfolio.get("cash")):
                problems.append("portfolio.cash must be a finite number")
            if not number_ok(portfolio.get("buying_power")):
                problems.append("portfolio.buying_power must be a finite number")
            positions = portfolio.get("positions", {})
            if not isinstance(positions, dict):
                problems.append("portfolio.positions must be a mapping of symbol -> held shares")
        survivors = inputs.get("survivors")
        if not isinstance(survivors, (list, tuple, set, frozenset)):
            problems.append(
                "survivors must be a materialized collection of entity names (the "
                f"stat_test survivors wire), got {type(survivors).__name__}"
            )
        return problems

    # -- the three doorway hooks --------------------------------------------

    def instruments(self, inputs):
        """Fail-closed bundle read + Schwab cost pricing -> ``(names, rows, account)``.

        A bundle row is routed out (never entering the program) for a
        stat_test-gate miss, staleness past ``bundle_max_staleness_ms``, or a
        price below ``min_price`` — each reason recorded in
        ``self._evidence`` for :meth:`run`'s ``evidence`` output. A currently
        held name absent from the surviving bundle rows enters as a
        MANDATORY EXIT (``x_max = 0``): it may only be sold, never bought.
        """
        bundle = inputs["bundle"]
        portfolio = inputs["portfolio"]
        survivors = set(inputs["survivors"])
        asof_ms = int(portfolio["asof_ms"])
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
            age_ms = asof_ms - int(row["decision_ts"])
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
                price = float(mark_prices.get(name) or 0.0)
                if price <= 0.0:
                    raise ValueError(
                        f"{self.key}: {name!r} is held ({h} shares) but absent from the "
                        "surviving bundle rows and portfolio.mark_prices carries no price "
                        "for it — a mandatory exit needs a mark to trade against"
                    )
                pi_upper_i = 0.0
                x_max = 0.0
                scenarios = [0.0] * len(shared_weights)
            else:
                price = float(row["price"])
                pi_upper_i = float(row["pi_upper"])
                x_max = max_notional
                scenarios = [float(v) for v in row["scenarios"]]
            spread = float(self.params["spread_bps"]) * 1e-4 * price
            # taf_per_share UNCAPPED, deliberately: the cap applies per
            # ORDER, and this coefficient prices a per-SHARE rate the base
            # applies to whatever quantity the solver picks — sizing the
            # rate against current holdings (a "cap / held" trick) was
            # tried and rejected here because it undercharges any sell
            # smaller than the full held size (e.g. trimming 5 shares off
            # a 1,000-share position priced the fee as if 1,000 shares
            # were selling). Charging the uncapped rate is conservative —
            # it can only OVERSTATE the true fee on a single large sell
            # that would hit the per-order cap, never understate it, so it
            # never overstates achievable edge.
            taf_rate = float(self.params["taf_per_share"])
            sec31 = float(self.params["sec31_bps"]) * 1e-4 * price
            sell_cost = spread + taf_rate + sec31
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
            "wealth_lo": max(1.0, w0_mark - span),
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

        No-trade band (§3.3(b)): one binary ``a[i]`` per name and
        ``band_shares_i * a[i] <= b[i] + s[i] <= M_i * a[i]``, so a trade
        either does not happen or moves at least ``band_shares_i`` — the
        wedge-shaped inaction region proportional trading costs require
        (Constantinides 1986; Davis & Norman 1990). ``M_i`` is the name's
        fillable buy-or-hold room, floored at ``band_shares_i`` so the band
        never manufactures artificial infeasibility.
        """
        from pyomo.environ import Binary, Constraint, Var

        names = model._scn["names"]
        rows = model._scn["rows"]
        q = float(params["hfdr_q"])
        model.hfdr = Constraint(
            expr=sum((self._pi_upper[i] - q) * model.x[i] for i in names) <= 0
        )

        band = self._band_shares
        trade_room = {}
        for i in names:
            price = rows[i]["price"]
            buy_room = int(rows[i]["x_max"] / price) if price > 0 else 0
            trade_room[i] = max(buy_room + rows[i]["held"], band[i], 1)

        model.a = Var(names, domain=Binary)
        model.band_lo = Constraint(
            names, rule=lambda m, i: band[i] * m.a[i] <= m.b[i] + m.s[i]
        )
        model.band_hi = Constraint(
            names, rule=lambda m, i: m.b[i] + m.s[i] <= trade_room[i] * m.a[i]
        )

    # -- run: fold in the evidence output ------------------------------------

    def run(self, ctx, inputs):
        """Solve, then attach the routing/gating evidence to the reported outputs."""
        out = super().run(ctx, inputs)
        out["evidence"] = self._evidence or {"n_bundle_rows": 0, "n_gated": 0, "n_held": 0, "routed_out": {}}
        return out


#: kind name -> class: what the registry, the conformance suite, and a
#: document's ``uses`` all key off.
NODE_KINDS = {
    "intraday_equities-kelly-mio": EquityKellyMIO,
}

# Import = registration (``owned`` deliberately NOT set — see CLAUDE.md).
for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)
