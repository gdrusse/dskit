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
  the program's per-name charge here.
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
        the first climb) and ``seed`` (int >= 0, the restarts' generator).

    Examples
    --------
    Start from cash and climb::

        search = KellyLocalSearch(scorer, {"moves": 200, "adds_evaluated": 64, "swaps_evaluated": 64,
                                           "restarts": 0, "seed": 0})
        out = search.run(None)
        out["quantities"], out["record"]["end_value"]
    """

    PARAM_KEYS = ("moves", "adds_evaluated", "swaps_evaluated", "restarts", "seed")
    #: A move must raise the objective by more than this fraction of ``w0`` (float noise otherwise cycles).
    IMPROVEMENT = 1e-9
    #: The chance a restart drops each held contract.
    PERTURBATION = .25
    #: The relative miss between the search's score and the scorer's exact recompute above which the search refuses.
    PARITY = 1e-9

    @classmethod
    def validate_params(cls, params):
        """Return the configuration defects of a search declaration (empty when well formed)."""
        if not isinstance(params, dict) or set(params) != set(cls.PARAM_KEYS):
            return [f"local_search needs exactly the keys {list(cls.PARAM_KEYS)}"]
        floors = {"moves": 1, "adds_evaluated": 1, "swaps_evaluated": 0, "restarts": 0, "seed": 0}
        return [f"local_search {key} must be an int >= {floor}" for key, floor in floors.items()
                if type(params[key]) is not int or params[key] < floor]

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
        for s, (_name, _weight, terms) in enumerate(scorer.scenarios):
            for g, row in terms.items():
                self.D[self.off[g]:self.off[g+1], s] += row
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
        self.slopes_ext = np.append(self.slopes, 0.)

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

    def _refresh_totals(self):
        """Recompute the cross-group sums: the caps' reserves, the joint rows, the names' spreads and the wealth."""
        self.capsum = np.array([self.res[m].sum() for m in self.cap_members], dtype=float)
        self.jtot = self.q @ self.J if self.K else np.zeros(0)
        self.keytot = np.bincount(self.charged_key, weights=self.spread[self.charged],
                                  minlength=len(self.slopes)).astype(float)
        self.W = self.w0+self.q.astype(float) @ self.D

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
        return float(self.w0*(self.weights @ u)-(self.slopes @ self.keytot**2))

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
        cap_change, key_change = {}, {}
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
        return float(self.w0*(self.weights @ u)-(self.slopes @ keytot**2))

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
        ids = ids[(self.W[None, :]+self.D[ids] >= self.lo-self.tol).all(axis=1)]
        adds = ids[np.lexsort((self.rank[ids], -add_gain[ids]))[:self.params["adds_evaluated"]]]
        held = np.flatnonzero(self.q > 0)
        return adds, pool, held, add_gain, remove_gain

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

    def _apply(self, changes):
        """Take a move: change ``q``, refresh the groups it touches and the sums."""
        for flat, dq in changes.items():
            self.q[flat] += dq
        for g in sorted({int(self.gid[flat]) for flat in changes}):
            self._refresh_group(g)
        self._refresh_totals()

    def _climb(self):
        """Move until no evaluated move improves or the move budget is spent; return the accepted values and how it ended."""
        values, kinds = [self.value()], {"add": 0, "remove": 0, "swap": 0}
        for _ in range(self.params["moves"]):
            found = self._best_move()
            if found is None:
                return values, kinds, True
            _gain, changes = found
            kinds["swap" if len(changes) == 2 else "add" if next(iter(changes.values())) > 0 else "remove"] += 1
            self._apply(changes)
            values.append(self.value())
        return values, kinds, False

    # --- the search ----------------------------------------------------------------------------------------

    def run(self, start=None):
        """Climb from a start selection (cash when None or infeasible) and return the traded selection and its record.

        Parameters
        ----------
        start : sequence of sequence of int or None
            The start selection in the scorer's nested layout.

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
        kind = "cash" if start is None else "stage1"
        first = np.zeros(self.N, dtype=np.int64) if start is None else self._flat(start)
        if len(first) != self.N:
            raise ValueError("the start differs from the program's layout")
        if start is not None and not self.scorer.check_feasible(start)["feasible"]:
            kind, first = "infeasible_start_cash", np.zeros(self.N, dtype=np.int64)
        self._load(first)
        start_value, start_contracts = self.value(), int(self.q.sum())
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
        self._load(best_q)
        end_value = self.value()
        nested = self._nested(best_q)
        selection = self.scorer.evaluate(nested)
        exact = selection["utility"]["exact"]
        if abs(exact-end_value) > self.PARITY*max(1., abs(exact), self.w0):
            raise ValueError("the local search's score differs from the exact recompute of its selection")
        record = {"start_kind": kind, "start_value": start_value, "end_value": end_value,
                  "start_contracts": start_contracts, "end_contracts": int(best_q.sum()),
                  "moves": total_moves, "evaluations": self.evaluations, "local_optimum": bool(best_done),
                  "budget_exhausted": exhausted, "restarts": ran, "restarts_skipped": skipped,
                  "move_counts": counts}
        return {"quantities": nested, "selection": selection, "record": record, "values": best_values,
                "seconds": time.perf_counter()-began}

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
