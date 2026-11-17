"""Fast local search over the one-stage fractional-Kelly program, without ever building it (ADR-0307).

ADR-0299's one-stage Kelly program (``TransportPortfolioSolve`` with a ``utility`` block) maximizes the exact
account objective ``w0 E[u(W)] - charges`` over integer contracts, but its mixed-integer form has 300,000 to
430,000 rows on a real week and did not finish. :class:`KellyLocalSearch` climbs the SAME objective without a
solver: it scores a selection exactly, on the program's own wealth scenarios and dispersion charges, and moves
one contract at a time. It never builds a pyomo model, so its cost grows with the number of moves, not with the
size of the program.

What it owns, and what it reads from the toolkit:

* The objective and every constraint are read from a :class:`~dskit.pipeline.libs.pyomo.TransportActionScore` built
  on the week's one-stage program (``scorer.groups``, ``caps``, ``joint``, ``scenarios``, ``expected``,
  ``charge_plan``, ``utility``). The objective is the scorer's ``utility["exact"]``: ``w0`` times the scenario
  expectation of the exact CRRA utility (:func:`~dskit.pipeline.libs.pyomo.tangent_utility`, not the tangent
  envelope the integer program carries) less each name's exact dispersion charge ``slope x S^2``. Feasibility is
  the scorer's: integer capacities, exclusive sides, group linear rows, the shared reserve caps (each group's
  reserve is the larger of zero and its reserve rows), the joint rows and the wealth floor in every scenario, all
  enforced as checks and never as penalties. The start and the returned selection are passed through the
  scorer itself (``check_feasible``, ``evaluate``), so the search cannot return a book the program would refuse.
* The scenario payoff matrix ``D`` (one row per contract, one column per wealth scenario: the contract's expected
  profit plus its scenario term) is built once. Its size is contracts times scenarios, a few megabytes at a
  production week (about 154,000 items by 17 scenarios), because the one-stage program's scenarios are the shock
  moves and not a large sample: the dispersion that a sampled program would carry in thousands of scenarios is
  the program's per-name charge here. A program with sampled joint scenarios (the scorer's ``atom_tables``,
  ``index_options.joint_scenarios``) has hundreds: ``D`` is then filled group by group from the atom tables, and the
  shortlist's floor check runs in row blocks (:meth:`KellyLocalSearch._floor_ok`) so no second matrix of that size
  is ever built.
* The moves: add one contract of an item, remove one contract, swap one contract of item ``a`` for one of item
  ``b`` (the same group or another). Each round a first-order estimate ranks every addable item from the current
  wealth vector (one matrix-vector product), the top ``adds_evaluated`` feasible adds, every removal of a held
  item and the top ``swaps_evaluated`` swaps are scored EXACTLY and checked for feasibility, and the best
  strictly improving move is taken (steepest ascent). The search stops at a local optimum (no evaluated move
  improves) or when ``moves`` are spent. The shortlist is a search heuristic: it forbids no book and the exact
  score decides.

Determinism (replay parity): every choice is a function of the program and the parameters: ties break by item
rank (group id, then item index), restarts perturb with a generator seeded from ``seed``, and nothing reads the
clock. A recorded week is replayed by running the search again and comparing.
"""

import time

import numpy as np

from dskit.pipeline.libs.pyomo import tangent_utility

__all__ = ["KellyLocalSearch"]


class KellyLocalSearch:
    """Climb the exact one-stage Kelly objective by single-contract adds, removals and swaps.

    Parameters
    ----------
    scorer : TransportActionScore
        The week's one-stage program with a ``utility`` block.
    params : dict
        ``moves`` (int >= 1, accepted moves per climb), ``adds_evaluated`` (int >= 1, the add shortlist),
        ``swaps_evaluated`` (int >= 0, the swap shortlist), ``restarts`` (int >= 0, perturbation restarts after
        the first climb) and ``seed`` (int >= 0, the restarts' generator). Optional, opt-in (the FAST variant's
        amendment to ADR-0307; absent means the original behaviour, byte for byte): ``cash_floor`` (bool: a climb
        that ends below cash trades cash) and ``repair`` (bool: an infeasible start is repaired by greedy removal
        instead of being replaced by cash). See :attr:`OPTION_KEYS`.

    Examples
    --------
    Start from cash and climb::

        search = KellyLocalSearch(scorer, {"moves": 200, "adds_evaluated": 64, "swaps_evaluated": 64,
                                           "restarts": 0, "seed": 0})
        out = search.run(None)
        out["quantities"], out["record"]["end_value"]
    """

    PARAM_KEYS = ("moves", "adds_evaluated", "swaps_evaluated", "restarts", "seed")
    #: Opt-in booleans of the FAST variant (ADR-0307 Amendment 1). Each writes its record field only when declared.
    OPTION_KEYS = ("cash_floor", "repair", "risk")
    #: The booleans among :attr:`OPTION_KEYS` (``risk`` is a dict: :meth:`validate_risk`).
    BOOL_KEYS = ("cash_floor", "repair")
    #: Opt-in priced risk terms (ADR-0319): ``cvar`` {alpha, weight}, ``concentration`` {by, weight, power} and
    #: ``replace_name_charge`` (bool). Absent, nothing changes, byte for byte. See :meth:`validate_risk`.
    RISK_KEYS = ("cvar", "concentration", "replace_name_charge")
    CVAR_KEYS = ("alpha", "weight")
    CONCENTRATION_KEYS = ("by", "weight", "power")
    CONCENTRATION_BY = ("name", "event")
    #: A move must raise the objective by more than this fraction of ``w0`` (float noise otherwise cycles).
    IMPROVEMENT = 1e-9
    #: The chance a restart drops each held contract.
    PERTURBATION = .25
    #: The relative miss between the search's score and the scorer's exact recompute above which the search refuses.
    PARITY = 1e-9
    #: Refresh the wealth vector by the moved contracts' rows (O(S) per changed item, the arithmetic ``_trial`` already
    #: did) and prefilter the add floor check by each item's worst scenario. ``False`` restores the full ``q @ D``
    #: refresh and the full floor check; it exists as the reference the parity test compares against.
    INCREMENTAL = True

    @classmethod
    def validate_params(cls, params):
        """Return the configuration defects of a search declaration (empty when well formed)."""
        if not isinstance(params, dict) or set(params)-set(cls.OPTION_KEYS) != set(cls.PARAM_KEYS):
            return [f"local_search needs exactly the keys {list(cls.PARAM_KEYS)}"]
        floors = {"moves": 1, "adds_evaluated": 1, "swaps_evaluated": 0, "restarts": 0, "seed": 0}
        return ([f"local_search {key} must be an int >= {floor}" for key, floor in floors.items()
                 if type(params[key]) is not int or params[key] < floor]
                + [f"local_search {key} must be a bool" for key in cls.BOOL_KEYS
                   if key in params and type(params[key]) is not bool]
                + (cls.validate_risk(params["risk"]) if "risk" in params else []))

    @classmethod
    def validate_risk(cls, risk):
        """Return the defects of a ``risk`` declaration (empty when well formed).

        ``cvar``: ``alpha`` in (0, 1] is the TAIL probability (0.05 prices the mean loss over the worst 5% of the
        scenario weight) and ``weight`` >= 0 the price in objective dollars per dollar of tail loss.
        ``concentration``: ``by`` is ``name`` (the program's aggregate key, the symbol) or ``event`` (one program
        group: a name and an expiry), ``weight`` >= 0 and ``power`` >= 1 (convexity).
        ``replace_name_charge``: true drops the name-spread charge from the objective (the concentration cost on
        reserves replaces it).
        """
        def num(value):
            return type(value) in (int, float) and np.isfinite(value)

        if not isinstance(risk, dict) or not risk or set(risk)-set(cls.RISK_KEYS):
            return [f"risk needs a nonempty dict with keys from {list(cls.RISK_KEYS)}"]
        defects = []
        if "cvar" in risk:
            cvar = risk["cvar"]
            if not isinstance(cvar, dict) or set(cvar) != set(cls.CVAR_KEYS):
                defects.append(f"risk cvar needs exactly the keys {list(cls.CVAR_KEYS)}")
            else:
                if not (num(cvar["alpha"]) and 0 < cvar["alpha"] <= 1):
                    defects.append("risk cvar alpha must be a number in (0, 1]")
                if not (num(cvar["weight"]) and cvar["weight"] >= 0):
                    defects.append("risk cvar weight must be a number >= 0")
        if "concentration" in risk:
            conc = risk["concentration"]
            if not isinstance(conc, dict) or set(conc) != set(cls.CONCENTRATION_KEYS):
                defects.append(f"risk concentration needs exactly the keys {list(cls.CONCENTRATION_KEYS)}")
            else:
                if conc["by"] not in cls.CONCENTRATION_BY:
                    defects.append(f"risk concentration by must be one of {list(cls.CONCENTRATION_BY)}")
                if not (num(conc["weight"]) and conc["weight"] >= 0):
                    defects.append("risk concentration weight must be a number >= 0")
                if not (num(conc["power"]) and conc["power"] >= 1):
                    defects.append("risk concentration power must be a number >= 1")
        if "replace_name_charge" in risk and type(risk["replace_name_charge"]) is not bool:
            defects.append("risk replace_name_charge must be a bool")
        return defects

    def __init__(self, scorer, params):
        defects = self.validate_params(params)
        if defects:
            raise ValueError("; ".join(defects))
        if scorer.utility is None:
            raise ValueError("a local search needs a program with a utility block")
        self.scorer, self.params = scorer, dict(params)
        self.tol = float(scorer.params.get("parity_tolerance", 0.))
        self.evaluations = 0
        self._build()

    # --- the program, once ---------------------------------------------------------------------------------

    def _build(self):
        """Lay the program out as flat arrays: items, scenario payoffs, per-group rows, caps, joint rows, charges."""
        scorer, utility = self.scorer, self.scorer.utility
        groups = scorer.groups
        self.G = len(groups)
        self.sizes = np.array([len(g["capacity"]) for g in groups])
        self.off = np.concatenate([[0], np.cumsum(self.sizes)]).astype(int)
        self.N = int(self.off[-1])
        self.cap = np.concatenate([np.asarray(g["capacity"], dtype=np.int64) for g in groups])
        self.gid = np.repeat(np.arange(self.G), self.sizes)
        self.loc = np.concatenate([np.arange(n) for n in self.sizes])
        rank_of_group = np.empty(self.G, dtype=int)
        rank_of_group[sorted(range(self.G), key=lambda g: groups[g]["id"])] = np.arange(self.G)
        order = np.lexsort((self.loc, rank_of_group[self.gid]))
        self.rank = np.empty(self.N, dtype=int)
        self.rank[order] = np.arange(self.N)
        self.w0, self.gamma, self.lo = utility["w0"], utility["gamma"], utility["wealth_lo"]
        # The scenario payoff matrix: a contract's wealth change in each scenario (expected profit + scenario term).
        self.S = len(scorer.scenarios)
        self.weights = np.array([weight for _name, weight, _terms in scorer.scenarios], dtype=float)
        self.D = np.empty((self.N, self.S))
        for g in range(self.G):
            self.D[self.off[g]:self.off[g+1], :] = np.asarray(scorer.expected[g], dtype=float)[:, None]
        atom_tables = getattr(scorer, "atom_tables", None)
        if atom_tables is not None:
            # Sampled atom scenarios: a group's column block is its atom table gathered onto the scenarios.
            for g, (table, atoms) in atom_tables[1].items():
                self.D[self.off[g]:self.off[g+1], :] += table[atoms].T
        else:
            for s, (_name, _weight, terms) in enumerate(scorer.scenarios):
                for g, row in terms.items():
                    self.D[self.off[g]:self.off[g+1], s] += row
        self.dmin = self.D.min(axis=1) if self.S else np.zeros(self.N)
        self.R = [np.asarray(g["reserve_rows"], dtype=float).reshape(-1, n) for g, n in zip(groups, self.sizes)]
        self.lin_coef = [np.asarray([row["coefficients"] for row in g["linear_rows"]], dtype=float).reshape(-1, n)
                         for g, n in zip(groups, self.sizes)]
        self.lin_up = [np.asarray([row["upper"] for row in g["linear_rows"]], dtype=float) for g in groups]
        self.partners = [[] for _ in range(self.N)]
        for g, group in enumerate(groups):
            for a, b in group["exclusive"]:
                self.partners[self.off[g]+a].append(int(self.off[g]+b))
                self.partners[self.off[g]+b].append(int(self.off[g]+a))
        self.has_partner = np.array([bool(p) for p in self.partners])
        self.pair_a = np.array([a for a in range(self.N) for _ in self.partners[a]], dtype=int)
        self.pair_b = np.array([b for a in range(self.N) for b in self.partners[a]], dtype=int)
        index = {g["id"]: i for i, g in enumerate(groups)}
        self.cap_members = [np.array([index[key] for key in cap["groups"]], dtype=int) for cap in scorer.caps]
        self.cap_up = np.array([cap["upper"] for cap in scorer.caps], dtype=float)
        self.caps_of = [[] for _ in range(self.G)]
        for c, members in enumerate(self.cap_members):
            for g in members:
                self.caps_of[g].append(c)
        self.K = len(scorer.joint)
        self.J = np.zeros((self.N, self.K))
        for r, row in enumerate(scorer.joint):
            for key, coefficients in row["terms"].items():
                self.J[self.off[index[key]]:self.off[index[key]+1], r] = coefficients
        self.j_up = np.array([row["upper"] for row in scorer.joint], dtype=float)
        # The dispersion charge: per group the atoms' payoff rows, per name key the summed spread and the slope.
        plan = scorer.charge_plan
        self.measure, self.pay_rows = plan["charge"], plan["rows"]
        names = sorted(plan["members"])
        self.slopes = np.array([plan["lines"][name].slope for name in names], dtype=float)
        self.key_of = {}
        for k, name in enumerate(names):
            for g in plan["members"][name]:
                self.key_of[g] = k
        self.charged = np.array(sorted(self.key_of), dtype=int)
        self.charged_key = np.array([self.key_of[g] for g in self.charged], dtype=int)
        self.own = np.zeros(self.N)     # a contract's own spread: the most it can add to its group's S
        for g in self.charged:
            m = self.measure[g]
            self.own[self.off[g]:self.off[g+1]] = m.price*m.columns(self.pay_rows[g])
        self.item_key = np.full(self.N, len(names), dtype=int)   # the sentinel key carries no charge
        for g, k in self.key_of.items():
            self.item_key[self.off[g]:self.off[g+1]] = k
        self._build_risk()
        self.slopes_ext = np.append(self.slopes, 0.)

    def _build_risk(self):
        """Read the opt-in ``risk`` declaration (ADR-0319): the CVaR price, the concentration keys, the charge switch."""
        risk = self.params.get("risk")
        self.risk_on = risk is not None
        self.cvar_alpha = self.cvar_weight = self.conc_weight = 0.
        self.conc_power, self.conc_by, self.conc_labels = 2., None, []
        self.slopes_true = self.slopes      # the scorer's own name charge, kept to reconcile the exact recompute
        if not self.risk_on:
            return
        if "cvar" in risk:
            self.cvar_alpha, self.cvar_weight = float(risk["cvar"]["alpha"]), float(risk["cvar"]["weight"])
        if "concentration" in risk:
            conc = risk["concentration"]
            self.conc_by, self.conc_weight, self.conc_power = conc["by"], float(conc["weight"]), float(conc["power"])
            groups = self.scorer.groups
            if self.conc_by == "event":
                labels = [g["id"] for g in groups]
            else:
                aggregate = self.scorer.utility.get("aggregate", {})
                labels = [aggregate.get(g["id"], g["id"]) for g in groups]
            self.conc_labels = sorted(set(labels))
            index = {label: i for i, label in enumerate(self.conc_labels)}
            self.conc_key = np.array([index[label] for label in labels], dtype=int)
        if risk.get("replace_name_charge"):
            self.slopes = np.zeros_like(self.slopes)

    # --- state of one selection ----------------------------------------------------------------------------

    def _refresh_group(self, g):
        """Recompute one group's reserve, linear rows, spread and its items' one-contract add checks from ``q``."""
        a, b = self.off[g], self.off[g+1]
        qg = self.q[a:b].astype(float)
        rows = self.R[g]
        self.Rq[g] = rows @ qg
        self.res[g] = max(0., float(self.Rq[g].max())) if rows.shape[0] else 0.
        self.lin[g] = self.lin_coef[g] @ qg
        if rows.shape[0]:
            self.dres[a:b] = np.maximum(0., (self.Rq[g][:, None]+rows).max(axis=0))-self.res[g]
        else:
            self.dres[a:b] = 0.
        if self.lin_coef[g].shape[0]:
            self.lin_ok[a:b] = ((self.lin[g][:, None]+self.lin_coef[g]) <= self.lin_up[g][:, None]+self.tol).all(axis=0)
        else:
            self.lin_ok[a:b] = True
        if g in self.key_of:
            measure = self.measure[g]
            self.pay[g] = self.pay_rows[g] @ qg
            self.spread[g] = measure.price*float(measure.columns(self.pay[g][:, None])[0])

    def _refresh_totals(self, wealth=None):
        """Recompute the cross-group sums: the caps' reserves, the joint rows, the names' spreads and the wealth.

        ``wealth`` is the already-updated wealth vector of an applied move; None recomputes it in full from ``q``."""
        self.capsum = np.array([self.res[m].sum() for m in self.cap_members], dtype=float)
        self.jtot = self.q @ self.J if self.K else np.zeros(0)
        self.keytot = np.bincount(self.charged_key, weights=self.spread[self.charged],
                                  minlength=len(self.slopes)).astype(float)
        self.W = self.w0+self.q.astype(float) @ self.D if wealth is None else wealth
        if self.conc_weight:
            self.rtot = np.bincount(self.conc_key, weights=self.res, minlength=len(self.conc_labels)).astype(float)

    def _load(self, q):
        """Make ``q`` (a flat int array) the current selection and rebuild every cached quantity."""
        self.q = np.array(q, dtype=np.int64)
        self.Rq, self.lin, self.pay = [None]*self.G, [None]*self.G, {}
        self.res, self.spread = np.zeros(self.G), np.zeros(self.G)
        self.dres, self.lin_ok = np.zeros(self.N), np.ones(self.N, dtype=bool)
        for g in range(self.G):
            self._refresh_group(g)
        self._refresh_totals()

    def _nested(self, q):
        """Return a flat quantity array in the scorer's nested ``[group][item]`` layout (plain ints)."""
        return [[int(v) for v in q[self.off[g]:self.off[g+1]]] for g in range(self.G)]

    def _flat(self, nested):
        """Return the nested quantities as one flat int array."""
        return np.array([v for group in nested for v in group], dtype=np.int64)

    def value(self):
        """Return the exact objective of the current selection: ``w0 E[u(W)]`` less the names' exact charges."""
        u = tangent_utility(self.W, self.w0, self.gamma)[0]
        value = float(self.w0*(self.weights @ u)-(self.slopes @ self.keytot**2))
        if self.risk_on:
            value -= self._penalty(self.W, self.rtot if self.conc_weight else None)
        return value

    # --- the priced risk terms (ADR-0319) ------------------------------------------------------------------

    def _cvar_tail(self, wealth):
        """Return ``(tail loss, tail weights)`` of a wealth vector: the Rockafellar-Uryasev ``CVaR_alpha`` of the loss
        ``w0 - W`` over the scenario weights, and the weight vector (summing to 1) that averages it.

        ``CVaR_alpha(L) = min_theta theta + E[(L - theta)+]/alpha`` equals the mean of ``L`` over the worst ``alpha``
        of the weight (the boundary scenario counted in part): sort the losses downward, take weight until ``alpha``
        is used. O(S log S); ties are interchangeable, so the value does not depend on the sort's tie order.
        """
        loss = self.w0-wealth
        order = np.argsort(-loss, kind="stable")
        w = self.weights[order]
        take = np.clip(self.cvar_alpha-(np.cumsum(w)-w), 0., w)
        tail = take/self.cvar_alpha
        weights = np.zeros(len(loss))
        weights[order] = tail
        return float(tail @ loss[order]), weights

    def _cvar_rows(self, wealth):
        """Return the tail loss of every row of a ``(rows, S)`` wealth matrix (the vectorized :meth:`_cvar_tail`)."""
        loss = self.w0-wealth
        order = np.argsort(-loss, axis=1, kind="stable")
        w = self.weights[order]
        take = np.clip(self.cvar_alpha-(np.cumsum(w, axis=1)-w), 0., w)
        return (take*np.take_along_axis(loss, order, axis=1)).sum(axis=1)/self.cvar_alpha

    def _concentration(self, rtot):
        """Return the concentration cost of per-key reserves ``rtot``: ``weight w0 sum_k (r_k / w0)^power``."""
        return self.conc_weight*self.w0*float(((rtot/self.w0)**self.conc_power).sum())

    def _penalty(self, wealth, rtot):
        """Return the priced risk terms at a wealth vector and per-key reserves: ``kappa max(0, CVaR)`` plus the
        concentration cost (each zero when not declared)."""
        out = 0.
        if self.cvar_weight:
            out += self.cvar_weight*max(0., self._cvar_tail(wealth)[0])
        if self.conc_weight:
            out += self._concentration(rtot)
        return out

    def _risk_shift(self):
        """Return the first-order penalty change of adding one contract of every item (rank-only; the exact trial decides).

        CVaR's subgradient is ``-D_i . t`` for the tail weights ``t`` (zero when the tail loss is not positive); the
        concentration part is the exact change of the item's key at the item's cached one-contract reserve change.
        """
        out = np.zeros(self.N)
        if self.cvar_weight:
            tail, weights = self._cvar_tail(self.W)
            if tail > 0.:
                out -= self.cvar_weight*(self.D @ weights)
        if self.conc_weight:
            r = self.rtot[self.conc_key[self.gid]]
            out += self.conc_weight*self.w0*(((r+self.dres)/self.w0)**self.conc_power-(r/self.w0)**self.conc_power)
        return out

    def risk_terms(self):
        """Return the current selection's risk terms as plain numbers (the week record's ``risk`` block)."""
        tail = max(0., self._cvar_tail(self.W)[0]) if self.cvar_weight else None
        out = {"declared": self.params["risk"],
               "name_charge_exact": float(self.slopes_true @ self.keytot**2),
               "name_charge_applied": float(self.slopes @ self.keytot**2)}
        if self.cvar_weight:
            out.update(cvar_alpha=self.cvar_alpha, cvar_tail_loss=tail, cvar_penalty=self.cvar_weight*tail)
        if self.conc_weight:
            share = self.rtot/self.w0
            top = int(np.argmax(share)) if len(share) else None
            out.update(concentration_by=self.conc_by, concentration_power=self.conc_power,
                       concentration_sum=float((share**self.conc_power).sum()),
                       concentration_penalty=self._concentration(self.rtot),
                       reserve_share_max=float(share.max()) if len(share) else 0.,
                       reserve_share_max_key=None if top is None else self.conc_labels[top],
                       reserve_share_by_key={self.conc_labels[k]: float(share[k]) for k in np.flatnonzero(share)})
        out["penalty"] = self._penalty(self.W, self.rtot if self.conc_weight else None)
        out["objective"] = self.value()
        out["objective_unpenalized"] = out["objective"]+out["penalty"]
        return out

    def _explains(self, exact):
        """Return the objective the search's score must equal for the scorer's ``exact``: its dispersion charge less
        the risk penalties, plus the name charge a ``replace_name_charge`` declaration dropped."""
        if not self.risk_on:
            return exact
        return (exact+float(self.slopes_true @ self.keytot**2)-float(self.slopes @ self.keytot**2)
                - self._penalty(self.W, self.rtot if self.conc_weight else None))

    # --- scoring a move ------------------------------------------------------------------------------------

    def _trial(self, changes):
        """Return the objective after ``changes`` (``{flat item: +1 or -1}``), or None when the result is infeasible."""
        self.evaluations += 1
        q, tol = self.q, self.tol
        new = {}
        for flat, dq in changes.items():
            value = int(q[flat])+dq
            if value < 0 or value > self.cap[flat]:
                return None
            new[flat] = value
        for flat, value in new.items():
            if value > 0 and any(new.get(p, int(q[p])) > 0 for p in self.partners[flat]):
                return None
        wealth = self.W.copy()
        for flat, dq in changes.items():
            wealth += dq*self.D[flat]
        if wealth.min() < self.lo-tol:
            return None
        if self.K:
            joint = self.jtot.copy()
            for flat, dq in changes.items():
                joint += dq*self.J[flat]
            if (joint > self.j_up+tol).any():
                return None
        touched = {}
        for flat, dq in changes.items():
            touched.setdefault(int(self.gid[flat]), []).append((int(self.loc[flat]), dq))
        cap_change, key_change, conc_change = {}, {}, {}
        for g, items in touched.items():
            columns = [c for c, _dq in items]
            step = np.array([dq for _c, dq in items], dtype=float)
            if self.lin_coef[g].shape[0] and (self.lin[g]+self.lin_coef[g][:, columns] @ step
                                              > self.lin_up[g]+tol).any():
                return None
            rows = self.R[g]
            reserve = max(0., float((self.Rq[g]+rows[:, columns] @ step).max())) if rows.shape[0] else 0.
            for c in self.caps_of[g]:
                cap_change[c] = cap_change.get(c, 0.)+reserve-self.res[g]
            if self.conc_weight:
                conc_change[self.conc_key[g]] = conc_change.get(self.conc_key[g], 0.)+reserve-self.res[g]
            if g in self.key_of:
                measure = self.measure[g]
                pay = self.pay[g]+self.pay_rows[g][:, columns] @ step
                spread = measure.price*float(measure.columns(pay[:, None])[0])
                key_change[self.key_of[g]] = key_change.get(self.key_of[g], 0.)+spread-self.spread[g]
        for c, change in cap_change.items():
            if self.capsum[c]+change > self.cap_up[c]+tol:
                return None
        keytot = self.keytot.copy()
        for k, change in key_change.items():
            keytot[k] += change
        u = tangent_utility(wealth, self.w0, self.gamma)[0]
        value = float(self.w0*(self.weights @ u)-(self.slopes @ keytot**2))
        if self.risk_on:
            rtot = None
            if self.conc_weight:
                rtot = self.rtot.copy()
                for k, change in conc_change.items():
                    rtot[k] += change
            value -= self._penalty(wealth, rtot)
        return value

    def _blocked(self):
        """Return the items whose exclusive partner is held."""
        blocked = np.zeros(self.N, dtype=bool)
        if len(self.pair_a):
            blocked[self.pair_a[self.q[self.pair_b] > 0]] = True
        return blocked

    def _estimates(self):
        """Return the first-order gain of adding one contract of every item and of removing one, from the current state.

        The utility part is exact to first order (``w0 sum_s p_s u'(W_s) D_is``); the charge part uses the item's own
        spread as the bound on how far it can move its group's spread. Estimates rank; they never decide.
        """
        marginal = self.D @ (self.w0*self.weights*tangent_utility(self.W, self.w0, self.gamma)[1])
        total = np.append(self.keytot, 0.)[self.item_key]
        charge = 2.*self.slopes_ext[self.item_key]*total*self.own
        if self.risk_on:
            shift = self._risk_shift()
            return marginal-charge-shift, charge-marginal+shift
        return marginal-charge, charge-marginal

    def _shortlists(self):
        """Return the add shortlist, the swap pool, the held items and the estimates, all in a stable order."""
        add_gain, remove_gain = self._estimates()
        eligible = (self.q < self.cap) & ~self._blocked()
        pool_ids = np.flatnonzero(eligible)
        pool = pool_ids[np.lexsort((self.rank[pool_ids], -add_gain[pool_ids]))[:self.params["adds_evaluated"]]]
        # An add must fit now: the cheap checks that every contract of a cached group answers at once.
        slack = np.full(self.G, np.inf)
        for c, members in enumerate(self.cap_members):
            slack[members] = np.minimum(slack[members], self.cap_up[c]+self.tol-self.capsum[c])
        fits = eligible & self.lin_ok & (self.dres <= slack[self.gid])
        if self.K:
            fits &= ((self.jtot[None, :]+self.J) <= self.j_up+self.tol).all(axis=1)
        ids = np.flatnonzero(fits)
        ids = ids[self._floor_ok(ids)]
        adds = ids[np.lexsort((self.rank[ids], -add_gain[ids]))[:self.params["adds_evaluated"]]]
        held = np.flatnonzero(self.q > 0)
        return adds, pool, held, add_gain, remove_gain

    #: Elements per block of :meth:`_floor_ok` (about 32 MB of float64): bounds the check's temporaries when a program
    #: carries hundreds of sampled scenarios, where one ``items x scenarios`` temporary would be gigabytes.
    FLOOR_CHUNK_ELEMENTS = 1 << 22

    def _floor_rows(self, ids):
        """Return, per item in ``ids``, whether one more contract keeps every scenario's wealth on the floor.

        Elementwise the same comparison as one ``(W + D[ids] >= lo - tol).all(axis=1)``, taken in row blocks.
        """
        rows = max(1, self.FLOOR_CHUNK_ELEMENTS//max(1, self.S))
        out = np.empty(len(ids), dtype=bool)
        for a in range(0, len(ids), rows):
            out[a:a+rows] = (self.W[None, :]+self.D[ids[a:a+rows]] >= self.lo-self.tol).all(axis=1)
        return out

    def _floor_ok(self, ids):
        """Return which of ``ids`` keep the wealth floor in every scenario after one more contract.

        Exact: ``W_s + D_is >= min W + min_s D_is`` in floating point too (rounding is monotone), so an item whose
        worst-scenario sum clears the floor passes without its row being read; only the rest get the full check
        (taken in row blocks, see :meth:`_floor_rows`)."""
        if not self.INCREMENTAL:
            return self._floor_rows(ids)
        if not self.S:
            return np.ones(len(ids), dtype=bool)
        ok = self.W.min()+self.dmin[ids] >= self.lo-self.tol
        rest = np.flatnonzero(~ok)
        if len(rest):
            ok[rest] = self._floor_rows(ids[rest])
        return ok

    def _best_move(self):
        """Return ``(gain, changes)`` of the best strictly improving move among those evaluated, or None."""
        current = self.value()
        adds, pool, held, add_gain, remove_gain = self._shortlists()
        moves = [(0, (int(self.rank[b]),), {int(b): 1}) for b in adds]
        moves += [(1, (int(self.rank[a]),), {int(a): -1}) for a in held]
        swaps = self.params["swaps_evaluated"]
        if swaps and len(held) and len(pool):
            a = np.repeat(held, len(pool))
            b = np.tile(pool, len(held))
            keep = a != b
            a, b = a[keep], b[keep]
            gain = remove_gain[a]+add_gain[b]
            for i in np.lexsort((self.rank[b], self.rank[a], -gain))[:swaps]:
                moves.append((2, (int(self.rank[a[i]]), int(self.rank[b[i]])), {int(a[i]): -1, int(b[i]): 1}))
        best = None
        for kind, ranks, changes in moves:
            value = self._trial(changes)
            if value is None:
                continue
            gain = value-current
            key = (-gain, kind, ranks)
            if best is None or key < best[0]:
                best = (key, gain, changes)
        if best is None or best[1] <= self.IMPROVEMENT*self.w0:
            return None
        return best[1], best[2]

    def _apply(self, changes, full=False):
        """Take a move: change ``q``, refresh the groups it touches and the sums."""
        wealth = None
        if self.INCREMENTAL and not full:
            wealth = self.W.copy()      # the same additions, in the same order, as ``_trial`` scored
            for flat, dq in changes.items():
                wealth += dq*self.D[flat]
        for flat, dq in changes.items():
            self.q[flat] += dq
        for g in sorted({int(self.gid[flat]) for flat in changes}):
            self._refresh_group(g)
        self._refresh_totals(wealth)

    def _climb(self):
        """Move until no evaluated move improves or the move budget is spent; return the accepted values and how it ended."""
        values, kinds = [self.value()], {"add": 0, "remove": 0, "swap": 0}
        for _ in range(self.params["moves"]):
            found = self._best_move()
            if found is None:
                return self._settled(values), kinds, True
            _gain, changes = found
            kinds["swap" if len(changes) == 2 else "add" if next(iter(changes.values())) > 0 else "remove"] += 1
            self._apply(changes)
            values.append(self.value())
        return self._settled(values), kinds, False

    def _settled(self, values):
        """End a climb on the full ``q @ D`` wealth, so the final value is the full-refresh one to the last bit (the
        incremental wealth differs from it by rounding only)."""
        if self.INCREMENTAL and len(values) > 1:
            self.W = self.w0+self.q.astype(float) @ self.D
            values[-1] = self.value()
        return values

    # --- the search ----------------------------------------------------------------------------------------

    def run(self, start=None, start_kind="stage1"):
        """Climb from a start selection (cash when None or infeasible) and return the traded selection and its record.

        Parameters
        ----------
        start : sequence of sequence of int or None
            The start selection in the scorer's nested layout.
        start_kind : str
            The record's ``start_kind`` for a feasible ``start`` (``stage1``, or ``stage1_incumbent`` for a stage one
            that stopped at its time limit with an incumbent). With ``repair`` declared, an infeasible start is
            repaired and recorded as ``repaired_`` plus this kind.

        Returns
        -------
        dict
            ``quantities`` (nested ints), ``selection`` (``scorer.evaluate`` of them), ``record`` (the decision's
            ``kelly.local`` fields: nothing in it reads the clock), ``values`` (the objective after each accepted
            move of the returned climb: nondecreasing) and ``seconds`` (a measurement, not part of the record).

        Raises
        ------
        ValueError
            The search's final score disagrees with the scorer's exact recompute (a defect, never a skipped week).
        """
        began = time.perf_counter()
        self.evaluations = 0
        kind = "cash" if start is None else start_kind
        first = np.zeros(self.N, dtype=np.int64) if start is None else self._flat(start)
        if len(first) != self.N:
            raise ValueError("the start differs from the program's layout")
        repaired = 0
        if start is not None and not self.scorer.check_feasible(start)["feasible"]:
            if self.params.get("repair"):
                first, repaired = self._repaired(first)
                kind = "repaired_"+start_kind if first.any() else "infeasible_start_cash"
            else:
                kind, first = "infeasible_start_cash", np.zeros(self.N, dtype=np.int64)
        self._load(first)
        start_value, start_contracts = self.value(), int(self.q.sum())
        start_penalty = self._penalty(self.W, self.rtot if self.conc_weight else None) if self.risk_on else None
        best_q, best_values, best_kinds, best_done = None, None, None, None
        total_moves, exhausted, ran, skipped = 0, False, 0, 0
        counts = {"add": 0, "remove": 0, "swap": 0}
        rng = np.random.default_rng(self.params["seed"])
        for attempt in range(self.params["restarts"]+1):
            if attempt:
                perturbed = self._perturbed(best_q, rng)
                if perturbed is None:
                    skipped += 1
                    continue
                self._load(perturbed)
                ran += 1
            values, kinds, done = self._climb()
            total_moves += len(values)-1
            exhausted = exhausted or not done
            for key, count in kinds.items():
                counts[key] += count
            if best_q is None or values[-1] > best_values[-1]+self.IMPROVEMENT*self.w0:
                best_q, best_values, best_kinds, best_done = self.q.copy(), values, kinds, done
        floored = False
        if self.params.get("cash_floor"):
            # ADR-0307 Amendment 1: a climb that ends below cash trades cash (cash is always feasible).
            self._load(np.zeros(self.N, dtype=np.int64))
            cash_value = self.value()
            self._load(best_q)
            if self.value() < cash_value:
                floored, best_q, best_values = True, np.zeros(self.N, dtype=np.int64), [cash_value]
        self._load(best_q)
        end_value = self.value()
        nested = self._nested(best_q)
        selection = self.scorer.evaluate(nested)
        exact = selection["utility"]["exact"]
        if abs(self._explains(exact)-end_value) > self.PARITY*max(1., abs(exact), self.w0):
            raise ValueError("the local search's score differs from the exact recompute of its selection")
        record = {"start_kind": kind, "start_value": start_value, "end_value": end_value,
                  "start_contracts": start_contracts, "end_contracts": int(best_q.sum()),
                  "moves": total_moves, "evaluations": self.evaluations, "local_optimum": bool(best_done),
                  "budget_exhausted": exhausted, "restarts": ran, "restarts_skipped": skipped,
                  "move_counts": counts}
        if "cash_floor" in self.params:
            record["cash_floor"] = floored
        if "repair" in self.params:
            record["repaired_contracts"] = repaired
        if self.risk_on:
            record["risk"] = {**self.risk_terms(), "start_penalty": start_penalty}
        return {"quantities": nested, "selection": selection, "record": record, "values": best_values,
                "seconds": time.perf_counter()-began}

    # --- repairing an infeasible start (ADR-0307 Amendment 1) -----------------------------------------------

    def _excess_shared(self, wealth, joint, capsum):
        """The summed breach of the account-wide rules: the wealth floor per scenario, the joint rows, the caps."""
        tol = self.tol
        out = float(np.maximum(0., self.lo-tol-wealth).sum())
        if self.K:
            out += float(np.maximum(0., joint-self.j_up-tol).sum())
        return out+float(np.maximum(0., capsum-self.cap_up-tol).sum())

    def _excess(self):
        """The current selection's summed breach of every rule :meth:`_trial` checks (zero exactly when it passes)."""
        q, tol = self.q, self.tol
        out = float(np.maximum(0, q-self.cap).sum())
        if len(self.pair_a):
            out += float(np.minimum(q[self.pair_a], q[self.pair_b]).sum())
        for g in range(self.G):
            if self.lin_coef[g].shape[0]:
                out += float(np.maximum(0., self.lin[g]-self.lin_up[g]-tol).sum())
        return out+self._excess_shared(self.W, self.jtot, self.capsum)

    def _removal(self, flat):
        """Return ``(breach change, objective)`` after removing one contract of ``flat``, feasible or not."""
        q, tol = self.q, self.tol
        g, c, held = int(self.gid[flat]), int(self.loc[flat]), int(q[flat])
        change = float(max(0, held-1-self.cap[flat])-max(0, held-self.cap[flat]))
        change += 2.*sum(min(held-1, int(q[p]))-min(held, int(q[p])) for p in self.partners[flat])
        if self.lin_coef[g].shape[0]:
            after = self.lin[g]-self.lin_coef[g][:, c]
            change += float(np.maximum(0., after-self.lin_up[g]-tol).sum()
                            - np.maximum(0., self.lin[g]-self.lin_up[g]-tol).sum())
        wealth = self.W-self.D[flat]
        joint = self.jtot-self.J[flat] if self.K else self.jtot
        rows = self.R[g]
        reserve = max(0., float((self.Rq[g]-rows[:, c]).max())) if rows.shape[0] else 0.
        capsum = self.capsum.copy()
        for k in self.caps_of[g]:
            capsum[k] += reserve-self.res[g]
        change += self._excess_shared(wealth, joint, capsum)-self._excess_shared(self.W, self.jtot, self.capsum)
        keytot = self.keytot.copy()
        if g in self.key_of:
            measure = self.measure[g]
            pay = self.pay[g]-self.pay_rows[g][:, c]
            keytot[self.key_of[g]] += measure.price*float(measure.columns(pay[:, None])[0])-self.spread[g]
        u = tangent_utility(wealth, self.w0, self.gamma)[0]
        value = float(self.w0*(self.weights @ u)-(self.slopes @ keytot**2))
        if self.risk_on:
            rtot = None
            if self.conc_weight:
                rtot = self.rtot.copy()
                rtot[self.conc_key[g]] += reserve-self.res[g]
            value -= self._penalty(wealth, rtot)
        return change, value

    def _repaired(self, q):
        """Return ``(q repaired, contracts removed)``: drop one contract at a time until the scorer accepts the book.

        Each step removes the contract whose removal leaves the smallest summed breach, then the highest objective,
        then the lowest item rank (deterministic). It ends at a feasible book at worst at cash (always feasible).
        """
        self._load(q)
        removed = 0
        while self.q.any():
            if self._excess() <= 0. and self.scorer.check_feasible(self._nested(self.q))["feasible"]:
                break
            best = None
            for flat in np.flatnonzero(self.q > 0):
                change, value = self._removal(int(flat))
                key = (change, -value, int(self.rank[flat]))
                if best is None or key < best[0]:
                    best = (key, int(flat))
            self._apply({best[1]: -1}, full=True)    # repair decisions read W: keep it the full refresh
            removed += 1
        return self.q.copy(), removed

    def _perturbed(self, q, rng):
        """Return ``q`` with each held contract dropped with probability :attr:`PERTURBATION`, None when that changes
        nothing or the result is infeasible (a removal can breach a floor that a hedge held up)."""
        held = np.flatnonzero(q > 0)
        ordered = held[np.argsort(self.rank[held], kind="stable")]
        units = np.repeat(ordered, q[ordered])
        dropped = units[rng.random(len(units)) < self.PERTURBATION]
        if not len(dropped):
            return None
        out = q.copy()
        np.subtract.at(out, dropped, 1)
        return out if self.scorer.check_feasible(self._nested(out))["feasible"] else None
