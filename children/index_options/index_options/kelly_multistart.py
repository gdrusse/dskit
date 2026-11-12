"""Multi-start Kelly local search from per-group LP books, with a pair add move (ADR-0315).

ADR-0307's :class:`~index_options.kelly_local.KellyLocalSearch` climbs the exact one-stage Kelly objective from the
selection of a stage-one dollar MILP. On real weeks that MILP is almost all of the wall time (up to 2,400 s a week)
while the climb takes about a second and is almost pure removal. :class:`KellyMultiStart` drops the stage-one solve:

* **Starts.** Each group's own LP relaxation (the ``lp`` group screen's relaxation, caps ignored:
  ``TransportPortfolioSolve._group_lp_relaxation``, the same LP whose value is the screen's bound) gives a fractional
  book. Of the groups whose relaxed value exceeds the parity tolerance, the ``top_groups`` best (ties by group id) are
  kept. A book is rounded at each declared ``scales`` fraction (``floor(s x + 1/2)`` within the capacity; of two
  exclusive sides the larger survives, ties to the lower index). The structured starts are, in this order: cash; every
  kept group alone at every scale; the prefix unions of the kept groups (best two, best three, ...) at every scale.
  Seeded random combinations (a random subset of the kept groups, each at a random declared scale) fill the remainder
  up to ``starts`` (duplicates dropped, the first occurrence kept); a structured list longer than ``starts`` is cut.
* **Scoring.** Every start is scored EXACTLY from the cash state by the search's own incremental trial (the scorer's
  feasibility and ``utility["exact"]``, held to the scorer at 1e-9 by ADR-0307's tests); an infeasible start scores
  nothing. A start touches few groups, so a trial costs tens of microseconds and the batch is a few milliseconds.
* **Climbs.** The ``climbed`` best feasible starts (ties by start order) are each climbed to completion by ADR-0307's
  moves plus a PAIR add (one contract of each of two items of the same group at once: a spread whose single legs
  each fail a linear row or do not beat cash alone). The best end book wins (ties to the earlier climb).
* **Cash floor.** A book scoring below cash is never traded: the week trades cash instead and says so.

Determinism (replay parity): the starts are a function of the program, the declaration and ``seed`` (a
``numpy.random.default_rng``); nothing reads the clock. The record carries the seed, a SHA-256 digest of every start
in order, the chosen start and its end book, so a replay that re-derives a different start list or choice refuses.
"""

import hashlib
import json
import time

import numpy as np

from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

from .kelly_local import KellyLocalSearch

__all__ = ["KellyMultiStart"]


class KellyMultiStart(KellyLocalSearch):
    """Climb the exact one-stage Kelly objective from many LP-rounded starts, with single and pair moves.

    Parameters
    ----------
    scorer : TransportActionScore
        The week's one-stage program with a ``utility`` block.
    params : dict
        ADR-0307's search budgets (``moves``, ``adds_evaluated``, ``swaps_evaluated``, ``restarts`` and ``seed``);
        ``restarts`` must be 0 here (the starts replace the perturbation restarts) and ``seed`` seeds the starts.
    multistart : dict
        ``top_groups`` (int >= 1, the LP books kept), ``starts`` (int >= 2, starts generated, cash included),
        ``climbed`` (int >= 1, starts climbed), optional ``evaluations`` (int >= 1: a cap on the exact trials of all
        climbs, shared evenly over the climbs still to run and checked at move boundaries, so a count not a clock),
        ``pairs_evaluated`` (int >= 0, the pair-add shortlist; 0 turns the move
        off) and ``scales`` (a nonempty list of distinct numbers in (0, 1], the rounding fractions, in order).

    Examples
    --------
    ::

        search = KellyMultiStart(scorer, {"moves": 200, "adds_evaluated": 64, "swaps_evaluated": 64,
                                          "restarts": 0, "seed": 1},
                                 {"top_groups": 8, "starts": 64, "climbed": 4, "pairs_evaluated": 64,
                                  "scales": [1., .5, .25, .125]})
        out = search.run_multistart()
        out["quantities"], out["record"]["multistart"]["chosen_start"]
    """

    MULTISTART_KEYS = ("top_groups", "starts", "climbed", "pairs_evaluated", "scales")
    #: Optional key: a deterministic cap on exact trials across all climbs (a count, never a clock).
    OPTIONAL_KEYS = ("evaluations",)
    #: Draws per random start slot before the generator gives up on finding a new combination.
    DRAWS = 20

    @classmethod
    def validate_multistart(cls, params, multistart):
        """Return the defects of a multi-start declaration: the search budgets and the ``multistart`` block."""
        defects = cls.validate_params(params)
        if defects:
            return defects
        if params["restarts"] != 0:
            return ["local_search restarts must be 0 with multistart (the starts replace them)"]
        if (not isinstance(multistart, dict) or not set(cls.MULTISTART_KEYS) <= set(multistart)
                or set(multistart) - set(cls.MULTISTART_KEYS) - set(cls.OPTIONAL_KEYS)):
            return [f"multistart needs the keys {list(cls.MULTISTART_KEYS)} and may add {list(cls.OPTIONAL_KEYS)}"]
        if "evaluations" in multistart and (type(multistart["evaluations"]) is not int
                                            or multistart["evaluations"] < 1):
            return ["multistart evaluations must be an int >= 1"]
        floors = {"top_groups": 1, "starts": 2, "climbed": 1, "pairs_evaluated": 0}
        defects = [f"multistart {key} must be an int >= {floor}" for key, floor in floors.items()
                   if type(multistart[key]) is not int or multistart[key] < floor]
        scales = multistart["scales"]
        if (not isinstance(scales, list) or not scales or len(set(map(repr, scales))) != len(scales)
                or any(type(s) not in (int, float) or not np.isfinite(s) or not 0 < s <= 1 for s in scales)):
            defects.append("multistart scales must be a nonempty list of distinct numbers in (0, 1]")
        return defects

    def __init__(self, scorer, params, multistart):
        defects = self.validate_multistart(params, multistart)
        if defects:
            raise ValueError("; ".join(defects))
        super().__init__(scorer, params)
        self.multi = json.loads(json.dumps(multistart))

    # --- the pair add move -------------------------------------------------------------------------------------

    def _pair_moves(self):
        """Return the pair-add shortlist: ``(ranks, changes)`` for the best ``pairs_evaluated`` same-group pairs.

        A pair leads with one of the ``adds_evaluated`` best eligible items by first-order gain (fit or not: a leg that
        fails a row alone may fit beside its partner) and takes any other eligible item of its group that is not its
        exclusive partner; pairs rank by the summed estimate, ties by item rank. The exact trial decides.
        """
        limit = self.multi["pairs_evaluated"]
        if not limit:
            return []
        add_gain, _remove_gain = self._estimates()
        eligible = (self.q < self.cap) & ~self._blocked()
        ids = np.flatnonzero(eligible)
        lead = ids[np.lexsort((self.rank[ids], -add_gain[ids]))[:self.params["adds_evaluated"]]]
        firsts, seconds = [], []
        for a in lead:
            g = self.gid[a]
            others = np.arange(self.off[g], self.off[g+1])
            others = others[eligible[others] & (others != a)]
            if self.partners[a]:
                others = others[~np.isin(others, self.partners[a])]
            firsts.append(np.full(len(others), a))
            seconds.append(others)
        if not firsts:
            return []
        a, b = np.concatenate(firsts), np.concatenate(seconds)
        if not len(a):
            return []
        swap = self.rank[a] > self.rank[b]
        a, b = np.where(swap, b, a), np.where(swap, a, b)
        unique = np.unique(np.stack([a, b], axis=1), axis=0)
        a, b = unique[:, 0], unique[:, 1]
        order = np.lexsort((self.rank[b], self.rank[a], -(add_gain[a]+add_gain[b])))[:limit]
        return [((int(self.rank[a[i]]), int(self.rank[b[i]])), {int(a[i]): 1, int(b[i]): 1}) for i in order]

    def _best_move(self):
        """ADR-0307's best move, or a strictly better pair add (ties go to the single moves, the pair is kind 3)."""
        single = super()._best_move()
        current, best = self.value(), None
        for ranks, changes in self._pair_moves():
            value = self._trial(changes)
            if value is None:
                continue
            key = (-(value-current), ranks)
            if best is None or key < best[0]:
                best = (key, value-current, changes)
        if best is None or best[1] <= self.IMPROVEMENT*self.w0:
            return single
        return (best[1], best[2]) if single is None or best[1] > single[0] else single

    def _climb(self, stop=None):
        """ADR-0307's climb, counting the pair adds apart from the swaps; ``stop`` is an evaluation count to stop at."""
        values, kinds = [self.value()], {"add": 0, "remove": 0, "swap": 0, "pair": 0}
        for _ in range(self.params["moves"]):
            if stop is not None and self.evaluations >= stop:
                return values, kinds, False
            found = self._best_move()
            if found is None:
                return values, kinds, True
            _gain, changes = found
            steps = list(changes.values())
            kinds["pair" if steps == [1, 1] else "swap" if len(steps) == 2
                  else "add" if steps[0] > 0 else "remove"] += 1
            self._apply(changes)
            values.append(self.value())
        return values, kinds, False

    # --- the starts ------------------------------------------------------------------------------------------

    def lp_books(self):
        """Return ``(bounds, books)``: every group's LP relaxation value and fractional actions, in program order."""
        bounds, books = [], []
        for group in self.scorer.groups:
            bound, actions = TransportPortfolioSolve._group_lp_relaxation(group)
            bounds.append(bound)
            books.append(np.clip(actions, 0., np.asarray(group["capacity"], dtype=float)))
        return bounds, books

    def _rounded(self, g, book, scale):
        """Return one group's LP book rounded at ``scale`` as ``{flat item: quantity}`` (exclusive sides repaired)."""
        a = self.off[g]
        q = np.minimum(np.floor(scale*book+.5), self.cap[a:self.off[g+1]]).astype(np.int64)
        for x, y in self.scorer.groups[g]["exclusive"]:
            if q[x] and q[y]:
                q[y if q[x] >= q[y] else x] = 0
        return {int(a+i): int(v) for i, v in enumerate(q) if v > 0}

    def starts(self, bounds, books):
        """Return ``(kept, starts)``: the kept group indices and the ordered start list (``(kind, {flat: qty})``)."""
        live = [g for g in range(self.G) if bounds[g] > self.tol]
        kept = sorted(live, key=lambda g: (-bounds[g], self.scorer.groups[g]["id"]))[:self.multi["top_groups"]]
        scales = [float(s) for s in self.multi["scales"]]
        rounded = {(g, s): self._rounded(g, books[g], s) for g in kept for s in scales}
        out, seen = [], set()

        def push(kind, start):
            key = tuple(sorted(start.items()))
            if key not in seen and len(out) < self.multi["starts"]:
                seen.add(key)
                out.append((kind, dict(key)))

        push("cash", {})
        for s in scales:
            for g in kept:
                push("group", rounded[g, s])
        for s in scales:
            for j in range(2, len(kept)+1):
                push("union", {k: v for g in kept[:j] for k, v in rounded[g, s].items()})
        rng = np.random.default_rng(self.params["seed"])
        draws = self.DRAWS*self.multi["starts"]
        while kept and len(out) < self.multi["starts"] and draws:
            draws -= 1
            size = int(rng.integers(1, len(kept)+1))
            chosen = sorted(int(i) for i in rng.choice(len(kept), size=size, replace=False))
            picks = rng.integers(0, len(scales), size=size)
            push("random", {k: v for i, pick in zip(chosen, picks) for k, v in rounded[kept[i], scales[pick]].items()})
        return kept, out

    @staticmethod
    def digest(starts):
        """Return the SHA-256 of the ordered start list (kinds and sparse books), the replay's start fingerprint."""
        text = json.dumps([[kind, sorted([int(k), int(v)] for k, v in start.items())] for kind, start in starts],
                          separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    # --- the search ------------------------------------------------------------------------------------------

    def run_multistart(self, lp=None):
        """Generate, score and climb the starts; return the traded selection and its record (``run``'s shape).

        Parameters
        ----------
        lp : tuple or None
            ``(bounds, books)`` as :meth:`lp_books` returns them; computed here when None.

        Returns
        -------
        dict
            ``quantities``, ``selection``, ``record`` (ADR-0307's ``kelly.local`` fields with ``start_kind``
            ``"multistart"``, ``move_counts`` with ``pair``, and ``multistart``: the start statistics), ``values``,
            ``screen`` (the LP screen's counts) and ``seconds`` (a measurement, not part of the record).

        Raises
        ------
        ValueError
            The search's final score disagrees with the scorer's exact recompute.
        """
        began = time.perf_counter()
        self.evaluations = 0
        bounds, books = self.lp_books() if lp is None else lp
        kept, starts = self.starts(bounds, books)
        cash = np.zeros(self.N, dtype=np.int64)
        self._load(cash)
        cash_value = self.value()
        scores = [cash_value if not start else self._trial(start) for _kind, start in starts]
        feasible = [i for i, v in enumerate(scores) if v is not None]
        order = sorted(feasible, key=lambda i: (-scores[i], i))[:self.multi["climbed"]]
        counts = {"add": 0, "remove": 0, "swap": 0, "pair": 0}
        climbs, best, exhausted, total = [], None, False, 0
        budget = self.multi.get("evaluations")
        for n, i in enumerate(order):
            stop = None if budget is None else self.evaluations+max(0, budget-self.evaluations)//(len(order)-n)
            first = cash.copy()
            for flat, quantity in starts[i][1].items():
                first[flat] = quantity
            self._load(first)
            values, kinds, done = self._climb(stop)
            total += len(values)-1
            exhausted = exhausted or not done
            for key, count in kinds.items():
                counts[key] += count
            climbs.append({"start": i, "kind": starts[i][0], "start_value": values[0], "end_value": values[-1],
                           "moves": len(values)-1, "local_optimum": bool(done)})
            if best is None or values[-1] > best[1][-1]+self.IMPROVEMENT*self.w0:
                best = (i, values, done, self.q.copy())
        floor = best is None or best[1][-1] < cash_value
        if floor:
            chosen, best_values, best_done, best_q = None, [cash_value], True, cash
        else:
            chosen, best_values, best_done, best_q = best
        self._load(best_q)
        end_value = self.value()
        nested = self._nested(best_q)
        selection = self.scorer.evaluate(nested)
        exact = selection["utility"]["exact"]
        if abs(exact-end_value) > self.PARITY*max(1., abs(exact), self.w0):
            raise ValueError("the multi-start search's score differs from the exact recompute of its selection")
        start_q = cash.copy()
        if chosen is not None:
            for flat, quantity in starts[chosen][1].items():
                start_q[flat] = quantity
        groups = self.scorer.groups
        record = {"start_kind": "multistart", "start_value": best_values[0], "end_value": end_value,
                  "start_contracts": int(start_q.sum()), "end_contracts": int(best_q.sum()),
                  "moves": total, "evaluations": self.evaluations, "local_optimum": bool(best_done),
                  "budget_exhausted": exhausted, "restarts": 0, "restarts_skipped": 0, "move_counts": counts,
                  "multistart": {"seed": self.params["seed"], "declared": json.loads(json.dumps(self.multi)),
                                 "lp_live": sum(1 for b in bounds if b > self.tol),
                                 "lp_groups": [groups[g]["id"] for g in kept],
                                 "lp_values": [float(bounds[g]) for g in kept],
                                 "starts": len(starts), "starts_feasible": len(feasible),
                                 "starts_digest": self.digest(starts), "cash_value": cash_value,
                                 "climbs": climbs, "chosen_start": chosen,
                                 "chosen_kind": None if chosen is None else starts[chosen][0],
                                 "cash_floor": bool(floor)}}
        live = record["multistart"]["lp_live"]
        screen = {"rule": "lp", "candidates": self.G, "kept": live, "screened": self.G-live, "fallback": None}
        return {"quantities": nested, "selection": selection, "record": record, "values": best_values,
                "screen": screen, "seconds": time.perf_counter()-began}
