"""Coherence across linked binary contracts: a riskless set when the quotes admit none, a projection when they do.

Binary contracts on one underlying are linked by identities every probability vector obeys: a
set of mutually exclusive, exhaustive buckets sums to one (a PARTITION); ``P(S >= K)`` cannot
rise with ``K`` (a monotone threshold CHAIN); and a range is the difference of two thresholds,
``between(L, U) = above(L) - above(U)`` (a DIFFERENCE identity). Each contract trades inside an
executable interval ``[bid, ask]`` (fee-adjusted upstream; this pack takes the numbers as given).
This pack asks two questions of a set of contracts and the relations declared between them.

1. Is there ANY coherent probability vector inside every interval? A linear program finds the
   smallest total amount by which the intervals must be widened (``p_i + u_i >= bid_i``,
   ``p_i - v_i <= ask_i``, ``u, v >= 0``, ``p`` in ``[0, 1]`` and satisfying the relations;
   minimise ``sum(u + v)``). Zero means coherent. A positive optimum is, by LP duality, the
   guaranteed profit of a riskless position of at most one unit per contract: the dual weight on
   a contract's bid row is the units SOLD at its bid, the weight on its ask row the units BOUGHT
   at its ask, and the relations' weights certify that the position's payout is the same under
   every probability vector the relations allow. The pack reports the legs (direction and units),
   the relations whose weights are nonzero (the violated ones), the net premium, and, when size
   columns are named, the executable units: the thinnest leg's size over its units.
2. The coherent fair value. A weighted least-squares projection of the two-sided mids onto the
   coherent set (the relations and ``[0, 1]``, NOT the intervals, which may be empty together),
   weights ``1 / max(spread, min_spread)^2`` so a tight market moves less than a wide one. On mids
   that are already coherent it is the identity.

Quotes. ONE method (:meth:`BinaryCoherence._sides`) reads a row's two prices and nothing else
does. A bid of ``b`` is a SALE at ``b`` when ``0 < b <= 1``; absent or ``b <= 0`` is no sale (a
probability is never below 0, so a non-positive bid binds nothing); ``b > 1`` is refused (no
binary pays more than 1). An ask of ``a`` is a PURCHASE at ``a`` when ``0 < a < 1``; absent,
``a == 0`` (no offer, never a free contract) or ``a >= 1`` (binds nothing) is no purchase;
``a < 0`` is refused. A non-number, or a bid above an ask when both are usable, is refused. A
refused row is ``bad_quote`` and every relation naming it is skipped by name. A row that merely
lacks a side stays in every relation: the LP gets a row only for the side it has, and the
projection leaves it a free variable with no objective term. Its projected value is not unique,
so it gets None and status ``bad_quote`` (owner ruling B, 2026-10-09).

Solver. The doorway is :class:`~dskit.pipeline.libs.pyomo.PyomoSolve` with a non-capital role
(``transform``: this pack sizes nothing and reads no ``stat_test`` gate, so the planner's
capital rule does not apply). The base's default ``appsi_highs`` interface refuses a quadratic
objective (it raises on a degree-2 expression), while pyomo's ``highs`` interface hands the same
HiGHS a QP and solves it; so ``solver`` is REQUIRED here rather than defaulted, and a solver that
cannot take the projection is refused by name at run. The isotonic fallback the ADR allowed for
a QP-less solver is therefore not built. Both solves go through the base's
``_resolve_solver``/``_solve``; the LP through the base lifecycle (its ``SolveRecord`` is kept),
the QP as a second solve of the same instance.

The projection is solved WHITENED: a two-sided contract's variable is ``u = p / s`` with ``s =
max(ask - bid, min_spread)``, bounded to ``[0, 1 / s]``, and the objective is ``sum((u - mid /
s)^2)``: the minimiser of the weighted objective (weights ``1 / s^2``) with an identity Hessian.
Scaling the weighted objective by its largest weight left HiGHS "optimal" on materially wrong
points and cycling on degenerate ones. The offset form ``p = mid + s d`` bounds ``d`` by ``mid /
s``, and a tiny mid under a wide floor then ends HiGHS' QP solver in "Solve error" on a
well-posed problem, so ``u`` is that ``d`` shifted by ``mid / s``. When every linked contract is
two-sided and the mids already satisfy every relation EXACTLY (each row evaluated on exact
fractions of the float mids, never a tolerance), they are returned and no QP is solved.

Limits. Under HiGHS (:data:`HIGHS_SOLVERS`) the node injects :data:`DEFAULT_QP_ITERATION_LIMIT`
and :data:`DEFAULT_TIME_LIMIT_S` UNDER the document's ``solver_options`` (the document wins per
key), so a solver that stalls ends as a non-optimal termination instead of hanging the run. Any
other solver is handed only the document's options: its option names differ, so it must declare
its own limit. A non-optimal termination is refused by name; so is a solver that raises (naming
the node and the program), and an "optimal" projection that breaks a relation row by more than
:data:`PROJECTION_TOLERANCE`.

Ids. Every contract id — a row's ``id_field`` cell and every id a relation declares — is read by
:func:`dskit.pipeline.binary_curve.row_key`: a non-empty str (by its characters) or a non-bool int,
so ``numpy.str_("a")`` is ``"a"`` and ``numpy.int64(3)`` is ``3``, while ``3`` and ``"3"`` are two
ids. A missing or refused row id, or two rows with one id, is an input problem; a relation id that
is missing or refused is a params problem, and one no row carries skips that relation by name. A row
whose id is not a key reaches :meth:`BinaryCoherence.run` only when its input check was bypassed; it
is then marked ``bad_id`` and never linked.

Relations are strategy objects (:class:`Relation`, registry :data:`RELATIONS`): a new identity is
a subclass and a registry entry, never a branch in the model builder.

Wiring is by import path: :data:`NODE_KINDS` is empty and nothing registers. A document names
``dskit.pipeline.libs.binary_coherence:BinaryCoherence``.

Import cost: stdlib + toolkit only. pyomo is imported strictly inside run-path methods.
"""

from abc import ABC, abstractmethod
from collections import Counter
from fractions import Fraction
from types import MappingProxyType

from dskit.pipeline.binary_curve import row_key
from dskit.pipeline.binary_pricing import STATUS_SUFFIX
from dskit.pipeline.libs.pyomo import PyomoSolve
from dskit.pipeline.records import number_ok

__all__ = [
    "DEFAULT_QP_ITERATION_LIMIT",
    "DEFAULT_TIME_LIMIT_S",
    "DEFAULT_TOLERANCE",
    "HIGHS_SOLVERS",
    "LEG_DUST",
    "NODE_KINDS",
    "PROJECTION_TOLERANCE",
    "RELATIONS",
    "RELATION_KINDS",
    "STATUSES",
    "STATUS_OK",
    "BinaryCoherence",
    "Chain",
    "Difference",
    "Partition",
    "Relation",
]

#: The default ``tolerance``: a widening at or below it is read as coherent. It gates the
#: feasible/infeasible VERDICT only; it never sizes a leg or decides which relations are named.
DEFAULT_TOLERANCE = 1e-7

#: Solver dust, relative to the largest ``|dual|`` of the LP: a leg's net units, or a relation row's
#: dual, at or below ``LEG_DUST * max|dual|`` is read as zero. Fixed, never the ``tolerance`` knob:
#: dropping a real leg would turn a riskless position into a risky one.
LEG_DUST = 1e-9

#: HiGHS' QP iteration cap, injected under the document's ``solver_options``. HiGHS' QP solver
#: cycled without end on degenerate quote sets; an iteration count, unlike a clock, stops it at the
#: same point on every machine. A projection of a ladder needs hundreds of iterations, so this is
#: far above any real need and still ends a stall in well under a second.
DEFAULT_QP_ITERATION_LIMIT = 100_000

#: HiGHS' wall-clock cap in seconds, injected under the document's ``solver_options``: the backstop
#: for a stall the iteration cap does not count (the LP, or a slow iteration). Generous, because a
#: solve that is merely large must finish; a stop is refused by name, never reported.
DEFAULT_TIME_LIMIT_S = 60.0

#: The solver names whose option names are HiGHS': only these are injected the two limits above.
HIGHS_SOLVERS = ("highs", "appsi_highs")

#: The most an "optimal" projection may break a relation row by before it is refused: the solver's
#: word is checked against the relations, never trusted. Equal to HiGHS' default primal
#: feasibility tolerance, the accuracy it promises.
PROJECTION_TOLERANCE = 1e-7

#: The status a projected contract carries; the others name why it has no coherent value.
STATUS_OK = "ok"
STATUSES = (STATUS_OK, "bad_id", "bad_quote", "unrelated")

#: How a refusal names each of the two programs.
_LP = "the widening LP"
_QP = "the projection QP"


class Relation(ABC):
    """One declared identity among contracts, as linear rows over their probabilities.

    Parameters
    ----------
    index : int
        Its position in the document's list of this kind (named in every report).
    ids : list
        The contract ids it links, in the order the kind reads them.

    Examples
    --------
    A subclass supplies its shape rule and its rows::

        class Equal(Relation):
            kind = "equals"

            @classmethod
            def shape_problem(cls, ids):
                return None if len(ids) == 2 else "equals links exactly two contracts"

            def rows(self, p):
                return [p[self.ids[0]] == p[self.ids[1]]]

        Equal(0, ["a", "b"]).describe()   # {'kind': 'equals', 'index': 0, 'ids': ['a', 'b']}
    """

    #: The params key a document lists this kind under.
    kind = ""

    def __init__(self, index, ids):
        self.index, self.ids = index, list(ids)

    @classmethod
    @abstractmethod
    def shape_problem(cls, ids):
        """Say what is wrong with the id list's shape, or None.

        Parameters
        ----------
        ids : list
            The distinct contract ids, as declared.

        Returns
        -------
        str or None
            A problem, or None when the shape is one this kind reads.
        """

    @abstractmethod
    def rows(self, p):
        """Return the relation as relational expressions over ``p`` (``id -> value``).

        Parameters
        ----------
        p : mapping
            Each linked id's probability: a pyomo variable or expression, or an exact
            :class:`fractions.Fraction` (the rows are then plain bools).

        Returns
        -------
        list
            One expression (or bool) per row.
        """

    def describe(self):
        """Return ``{"kind", "index", "ids"}``, how every report names this relation.

        Returns
        -------
        dict
            The relation's identity.
        """
        return {"kind": self.kind, "index": self.index, "ids": list(self.ids)}


class Partition(Relation):
    """Mutually exclusive, exhaustive buckets: their probabilities sum to one.

    Examples
    --------
    Three buckets::

        Partition(0, ["lt90", "mid", "ge110"]).describe()["kind"]   # 'partitions'
    """

    kind = "partitions"

    @classmethod
    def shape_problem(cls, ids):
        """Need at least two buckets (see :meth:`Relation.shape_problem`).

        Parameters
        ----------
        ids : list
            The bucket ids.

        Returns
        -------
        str or None
            A problem, or None.
        """
        return None if len(ids) >= 2 else "a partition needs at least two buckets"

    def rows(self, p):
        """Return the one row ``sum p = 1``.

        Parameters
        ----------
        p : mapping
            Each bucket's variable.

        Returns
        -------
        list
            One expression.
        """
        return [sum(p[i] for i in self.ids) == 1]


class Chain(Relation):
    """Thresholds ``above(K)`` listed in increasing ``K``: each at least the next.

    Examples
    --------
    A three-strike ladder::

        Chain(0, ["a90", "a100", "a110"]).describe()["ids"]   # ['a90', 'a100', 'a110']
    """

    kind = "chains"

    @classmethod
    def shape_problem(cls, ids):
        """Need at least two thresholds (see :meth:`Relation.shape_problem`).

        Parameters
        ----------
        ids : list
            The thresholds in increasing strike.

        Returns
        -------
        str or None
            A problem, or None.
        """
        return None if len(ids) >= 2 else "a chain needs at least two thresholds"

    def rows(self, p):
        """Return ``p[k] >= p[k + 1]`` for each neighbouring pair.

        Parameters
        ----------
        p : mapping
            Each threshold's variable.

        Returns
        -------
        list
            One expression per neighbouring pair.
        """
        return [p[a] >= p[b] for a, b in zip(self.ids, self.ids[1:])]


class Difference(Relation):
    """A range as the difference of two thresholds: ``[range, above(L), above(U)]``.

    Examples
    --------
    ``between(100, 110) = above(100) - above(110)``::

        Difference(0, ["b100_110", "a100", "a110"]).describe()["kind"]   # 'differences'
    """

    kind = "differences"

    @classmethod
    def shape_problem(cls, ids):
        """Need exactly ``[range, lower threshold, upper threshold]``.

        Parameters
        ----------
        ids : list
            The three ids.

        Returns
        -------
        str or None
            A problem, or None.
        """
        return None if len(ids) == 3 else "a difference is [range, above(L), above(U)]: exactly three ids"

    def rows(self, p):
        """Return the one row ``p[range] = p[above(L)] - p[above(U)]``.

        Parameters
        ----------
        p : mapping
            The three variables.

        Returns
        -------
        list
            One expression.
        """
        between, lower, upper = self.ids
        return [p[between] == p[lower] - p[upper]]


#: kind -> the Relation subclass a document's params key builds, read-only.
RELATIONS = MappingProxyType({cls.kind: cls for cls in (Partition, Chain, Difference)})
#: The params keys a document declares relations under, in :data:`RELATIONS`' order (derived, never restated).
RELATION_KINDS = tuple(RELATIONS)


class BinaryCoherence(PyomoSolve):
    """Test linked binaries for a riskless set and project their mids onto the coherent set (role ``transform``).

    Inputs: ``records``, one row per contract with its id and executable YES bid and ask
    (fee-adjusted upstream; a missing, non-positive or non-binding side is read by the module's
    Quotes rule), and sizes when named. Outputs: ``records``, each row with
    ``<fair_field>`` (the projected coherent probability, None when it has none) and
    ``<fair_field>_status`` (one of :data:`STATUSES`); ``arbitrage``, ``{"feasible",
    "violation", "legs", "relations", "credit_per_unit", "executable_units"}`` (``violation``
    is the riskless profit per unit, ``credit_per_unit`` the net premium taken at the quotes);
    and ``summary``, with the relations skipped (and why), counts and the LP's solve record.

    Parameters
    ----------
    params : dict
        REQUIRED: ``id_field``, ``bid_field``, ``ask_field`` (column names), ``fair_field``
        (output column), ``min_spread`` (positive: the spread floor in the projection weights)
        and ``solver`` (a pyomo solver that takes a quadratic objective, e.g. ``"highs"``).
        At least one of ``partitions``, ``chains`` and ``differences``: each a list of id
        lists (:data:`RELATIONS`). OPTIONAL: ``bid_size_field`` and ``ask_size_field`` (both
        or neither), ``tolerance`` (>= 0, default :data:`DEFAULT_TOLERANCE`; it gates only the
        feasible/infeasible verdict, legs and relations use :data:`LEG_DUST`) and
        ``solver_options``: under a HiGHS solver (:data:`HIGHS_SOLVERS`) the defaults are
        ``qp_iteration_limit`` :data:`DEFAULT_QP_ITERATION_LIMIT` and ``time_limit``
        :data:`DEFAULT_TIME_LIMIT_S`, each overridden per key by the document; any other solver
        is handed only the document's options, so it must declare its own limit.

    Examples
    --------
    A two-strike ladder and the range between it::

        node = BinaryCoherence("coherence", {
            "id_field": "id", "bid_field": "yes_bid", "ask_field": "yes_ask",
            "fair_field": "coherent", "min_spread": 0.01, "solver": "highs",
            "chains": [["a100", "a110"]], "differences": [["b100_110", "a100", "a110"]]})
        out = node.run(ctx, {"records": contracts})
        # -> out["arbitrage"]["feasible"] is False when a riskless set exists
    """

    role = "transform"
    outputs = ("records", "arbitrage", "summary")
    _FIELDS = ("id_field", "bid_field", "ask_field", "fair_field")
    _SIZE_FIELDS = ("bid_size_field", "ask_size_field")
    _PARAMS = PyomoSolve._PARAMS + (*_FIELDS, *_SIZE_FIELDS, "min_spread", "tolerance", *RELATION_KINDS)

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per unknown, missing or unusable knob.
        """
        problems = super().validate_params(params)
        if "solver" not in params:
            problems.append("solver is required: a pyomo solver that accepts a quadratic objective "
                            "(for example 'highs'); the base default 'appsi_highs' does not")
        for name in cls._FIELDS:
            if not _name_ok(params.get(name)):
                problems.append(f"{name} is required: a non-empty column name, got {params.get(name)!r}")
        for name in cls._SIZE_FIELDS:
            if name in params and not _name_ok(params[name]):
                problems.append(f"{name} must be a non-empty column name, got {params[name]!r}")
        if sum(name in params for name in cls._SIZE_FIELDS) == 1:
            problems.append("bid_size_field and ask_size_field are declared together: name both sizes or neither")
        spread = params.get("min_spread")
        if not (number_ok(spread) and spread > 0):
            problems.append(f"min_spread is required: a positive number, got {spread!r}")
        tolerance = params.get("tolerance", DEFAULT_TOLERANCE)
        if not (number_ok(tolerance) and tolerance >= 0):
            problems.append(f"tolerance must be a number >= 0, got {tolerance!r}")
        if not any(params.get(kind) for kind in RELATION_KINDS):
            problems.append(f"declare at least one relation under {list(RELATION_KINDS)}")
        for kind in RELATION_KINDS:
            problems.extend(_relation_problems(kind, RELATIONS[kind], params.get(kind, [])))
        return problems + cls._collision_problems(params)

    @classmethod
    def _collision_problems(cls, params):
        """Problems with an output column that would overwrite an input column."""
        name = params.get("fair_field")
        if not _name_ok(name):
            return []
        outputs = {name, name + STATUS_SUFFIX}
        named = {params.get(k) for k in (*cls._FIELDS[:3], *cls._SIZE_FIELDS) if _name_ok(params.get(k))}
        clash = sorted(outputs & named)
        return [f"fair_field {name!r} writes {sorted(outputs)}, which would overwrite the input column(s) {clash}"
                ] if clash else []

    def validate_inputs(self, inputs):
        """Refuse a ``records`` port that is not a list, and an id listed twice.

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            One problem per unusable port or repeated id.
        """
        records = inputs.get("records")
        if not isinstance(records, list):
            return [f"records must be a list of rows, got {type(records).__name__}"]
        keyed = [row_key(row.get(self.params["id_field"])) if isinstance(row, dict) else (None, "not a row")
                 for row in records]
        problems = [f"records[{i}] has no usable id: {why}" for i, (_, why) in enumerate(keyed) if why]
        counts = Counter(key for key, why in keyed if why is None)
        repeated = sorted({repr(key) for key, why in keyed if why is None and counts[key] > 1})
        if repeated:
            problems.append(f"records list the id(s) {repeated} more than once: one row per contract")
        return problems

    # -- preparation: which quotes and relations are usable -----------------------------------------------

    @staticmethod
    def _bid_side(bid):
        """Return ``(sale price or None, problem or None)`` for a bid cell."""
        if bid is None:
            return None, None
        if not number_ok(bid):
            return None, f"bid {bid!r} is not a finite number"
        if bid > 1:
            return None, f"bid {bid!r} is above 1: no binary pays more than 1"
        return (float(bid), None) if bid > 0 else (None, None)

    @staticmethod
    def _ask_side(ask):
        """Return ``(purchase price or None, problem or None)`` for an ask cell."""
        if ask is None:
            return None, None
        if not number_ok(ask):
            return None, f"ask {ask!r} is not a finite number"
        if ask < 0:
            return None, f"ask {ask!r} is below 0"
        return (float(ask), None) if 0 < ask < 1 else (None, None)

    def _sides(self, row):
        """Return ``(sell_at, buy_at, problem)`` for a row: the ONE reading of a quote (module docstring, Quotes)."""
        sell_at, bid_problem = self._bid_side(row.get(self.params["bid_field"]))
        buy_at, ask_problem = self._ask_side(row.get(self.params["ask_field"]))
        problem = bid_problem or ask_problem
        if problem is None and sell_at is not None and buy_at is not None and sell_at > buy_at:
            problem = f"crossed: bid {sell_at!r} is above ask {buy_at!r}"
        return (None, None, problem) if problem else (sell_at, buy_at, None)

    def _prepare(self, inputs):
        """Return the rows by id, their usable prices, the active relations and the skipped ones (with reasons)."""
        keyed = ((row_key(row.get(self.params["id_field"]))[0], row) for row in inputs["records"])
        rows = {cid: row for cid, row in keyed if cid is not None}
        read = {cid: self._sides(row) for cid, row in rows.items()}
        refused = {cid for cid, (_, _, problem) in read.items() if problem}
        sides = {cid: (sell_at, buy_at) for cid, (sell_at, buy_at, problem) in read.items() if not problem}
        priced = {cid: pair for cid, pair in sides.items() if None not in pair}
        active, skipped = [], []
        for kind in RELATION_KINDS:
            relation = RELATIONS[kind]
            for index, ids in enumerate(_relation_keys(self.params.get(kind, []))):
                missing = [cid for cid in ids if cid not in rows]
                unusable = [cid for cid in ids if cid in refused]
                if missing or unusable:
                    skipped.append({"kind": kind, "index": index, "ids": list(ids), "missing": missing,
                                    "bad_quote": unusable})
                else:
                    active.append(relation(index, ids))
        linked = sorted({cid for relation in active for cid in relation.ids}, key=repr)
        return {"rows": rows, "sides": sides, "priced": priced, "active": active, "skipped": skipped,
                "linked": linked}

    # -- the LP: the PyomoSolve hooks ---------------------------------------------------------------------

    def _solver_options(self):
        """Merge the HiGHS limits UNDER the document's ``solver_options`` (the document wins per key)."""
        options = super()._solver_options()
        if self.params.get("solver") not in HIGHS_SOLVERS:
            return options
        return {"qp_iteration_limit": DEFAULT_QP_ITERATION_LIMIT, "time_limit": DEFAULT_TIME_LIMIT_S, **options}

    def _solve(self, solver, model):
        """Solve; a solver that raises is re-raised naming this node and the program, in the solver's words.

        Raises
        ------
        RuntimeError
            The solver raised (an interface that cannot take the program, or no loadable solution).
        """
        program = getattr(model, "_program", "the program")
        hint = " (a quadratic objective needs a QP solver, e.g. 'highs')" if program == _QP else ""
        try:
            return solver.solve(model)
        except Exception as exc:
            raise RuntimeError(f"{self.key}: solver {self.params.get('solver')!r} raised on {program}{hint}: "
                               f"{exc}") from exc

    def build_model(self, inputs, params):
        """Return the widening LP over the linked contracts (module docstring, question 1).

        Parameters
        ----------
        inputs : dict
            ``records``: the contract rows.
        params : dict
            ``self.params``.

        Returns
        -------
        pyomo.environ.ConcreteModel
            The LP, with an imported ``dual`` suffix and its preparation on ``_coherence``. A
            contract gets a bid row only if it has a sale price and an ask row only if it has a
            purchase price.
        """
        from pyomo.environ import ConcreteModel, Constraint, NonNegativeReals, Objective, Suffix, Var

        prepared = self._prepare(inputs)
        ids = prepared["linked"]
        sell, buy = ([prepared["sides"][cid][side] for cid in ids] for side in (0, 1))
        model = ConcreteModel(name="binary-coherence")
        model._program = _LP
        model.dual = Suffix(direction=Suffix.IMPORT)
        keys = range(len(ids))
        bid_keys = [i for i in keys if sell[i] is not None]
        ask_keys = [i for i in keys if buy[i] is not None]
        model.p = Var(keys, bounds=(0.0, 1.0))
        model.widen_bid = Var(bid_keys, domain=NonNegativeReals)
        model.widen_ask = Var(ask_keys, domain=NonNegativeReals)
        model.at_bid = Constraint(bid_keys, rule=lambda m, i: m.p[i] + m.widen_bid[i] >= sell[i])
        model.at_ask = Constraint(ask_keys, rule=lambda m, i: m.p[i] - m.widen_ask[i] <= buy[i])
        _add_relations(model, prepared["active"], {cid: model.p[i] for i, cid in enumerate(ids)})
        model.widening = Objective(expr=sum(model.widen_bid[i] for i in bid_keys)
                                   + sum(model.widen_ask[i] for i in ask_keys))
        model._coherence = {**prepared, "sell": sell, "buy": buy}
        return model

    def extract(self, model, results):
        """Read the LP's optimum and duals into the ``arbitrage`` report.

        Parameters
        ----------
        model : pyomo.environ.ConcreteModel
            The solved LP.
        results : object
            The solver's results.

        Returns
        -------
        dict
            ``{"arbitrage": {...}}``; :meth:`run` adds the other outputs.

        Raises
        ------
        RuntimeError
            When the LP did not finish optimal (the declared relations admit no probability
            vector at all, or the solver failed); and when the verdict is infeasible but the duals
            recover no position (no leg survives :data:`LEG_DUST`) or one that cannot profit (its
            credit plus the units it buys, its best payout, is not positive): a verdict with no
            usable trade is refused by name, never reported as an empty or losing trade.
        """
        from pyomo.environ import value

        _require_optimal(results, f"{_LP} (the declared relations may admit no probability vector)")
        data = model._coherence
        violation = max(0.0, float(value(model.widening)))
        feasible = violation <= self.params.get("tolerance", DEFAULT_TOLERANCE)
        if feasible:
            return {"arbitrage": {"feasible": True, "violation": violation, "legs": [], "relations": [],
                                  "credit_per_unit": 0.0, "executable_units": None}}
        dust = LEG_DUST * max((abs(d) for d in model.dual.values()), default=0.0)
        legs = self._legs(model, data, dust)
        credit = sum(leg["units"] * (leg["price"] if leg["side"] == "sell" else -leg["price"]) for leg in legs)
        _require_position(legs, credit, violation)
        violated = [r.describe() for r in data["active"]
                    if any(abs(model.dual.get(row, 0.0)) > dust for row in _rows_of(model, r))]
        return {"arbitrage": {
            "feasible": False, "violation": violation, "legs": legs, "relations": violated,
            "credit_per_unit": credit, "executable_units": self._executable_units(legs, data["rows"])}}

    def _legs(self, model, data, dust):
        """Return the riskless position the LP's duals certify: units sold at bids, bought at asks."""
        legs = []
        for i, cid in enumerate(data["linked"]):
            net = -_dual_of(model, "at_ask", i) - _dual_of(model, "at_bid", i)
            if abs(net) > dust:
                side = "buy" if net > 0 else "sell"
                price = data["buy"][i] if side == "buy" else data["sell"][i]
                if price is None:
                    raise RuntimeError(f"{self.key}: the widening LP's duals put a {side} leg on {cid!r}, "
                                       f"which quotes no {'ask' if side == 'buy' else 'bid'} to trade: refused")
                legs.append({"id": cid, "side": side, "units": abs(net), "price": price})
        return legs

    def _executable_units(self, legs, rows):
        """Return the thinnest leg's size over its units, or None when no size columns are named."""
        if "bid_size_field" not in self.params:
            return None
        sizes = []
        for leg in legs:
            size = rows[leg["id"]].get(self.params["bid_size_field" if leg["side"] == "sell" else "ask_size_field"])
            sizes.append(size / leg["units"] if number_ok(size) and size >= 0 else 0.0)
        return float(min(sizes))

    # -- the QP: the coherent projection ------------------------------------------------------------------

    def _project(self, prepared):
        """Return ``{id: coherent probability}`` for every linked two-sided contract (module docstring, question 2).

        Raises
        ------
        RuntimeError
            The projection did not finish optimal, the solver raised, or its "optimal" breaks a relation.
        """
        from pyomo.environ import value

        priced = prepared["priced"]
        mids = {cid: (priced[cid][0] + priced[cid][1]) / 2.0 for cid in prepared["linked"] if cid in priced}
        if not mids:
            return {}
        if len(mids) == len(prepared["linked"]) and self._holds_exactly(prepared["active"], mids):
            return mids
        model = self._projection_model(prepared, mids)
        results = self._solve(self._resolve_solver(), model)
        _require_optimal(results, _QP)
        self._require_relations_hold(model, prepared["active"])
        return {cid: min(1.0, max(0.0, float(value(model._probability[cid])))) for cid in mids}

    @staticmethod
    def _holds_exactly(active, mids):
        """Say whether every row of every relation holds on the exact fractions of the float ``mids``."""
        exact = {cid: Fraction(mid) for cid, mid in mids.items()}
        return all(row is True for relation in active for row in relation.rows(exact))

    def _projection_model(self, prepared, mids):
        """Return the whitened projection QP: ``p = s u`` per two-sided contract, minimise ``sum((u - mid / s)^2)``."""
        from pyomo.environ import ConcreteModel, Objective, Var

        floor, priced = float(self.params["min_spread"]), prepared["priced"]
        steps = {cid: max(priced[cid][1] - priced[cid][0], floor) for cid in mids}
        floating = [cid for cid in prepared["linked"] if cid not in mids]
        model = ConcreteModel(name="binary-coherence-projection")
        model._program = _QP
        centres = {cid: mids[cid] / steps[cid] for cid in mids}
        model.units = Var(list(mids), initialize=lambda m, cid: centres[cid],
                          bounds=lambda m, cid: (0.0, 1.0 / steps[cid]))
        model.floating = Var(floating, bounds=(0.0, 1.0), initialize=0.5)
        p = {cid: steps[cid] * model.units[cid] for cid in mids}
        p.update({cid: model.floating[cid] for cid in floating})
        _add_relations(model, prepared["active"], p)
        # (u - centre)^2 without its constant centre^2, which grows like 1 / s^2 and only costs the solver precision
        model.distance = Objective(
            expr=sum(model.units[cid] ** 2 - 2.0 * centres[cid] * model.units[cid] for cid in mids))
        model._probability = p
        return model

    def _require_relations_hold(self, model, active):
        """Refuse an "optimal" projection that breaks a relation row by more than :data:`PROJECTION_TOLERANCE`."""
        for relation in active:
            for k, row in enumerate(_rows_of(model, relation)):
                broken = -min(row.lslack(), row.uslack())
                if broken > PROJECTION_TOLERANCE:
                    raise RuntimeError(f"{self.key}: {_QP} finished optimal but breaks "
                                       f"{relation.kind}[{relation.index}] row {k} by {broken:.3g} "
                                       f"(more than {PROJECTION_TOLERANCE:g}): refused")

    # -- the lifecycle -----------------------------------------------------------------------------------

    def run(self, ctx, inputs):
        """Solve the LP, then the projection, and write both reports.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records``: the contract rows.

        Returns
        -------
        dict
            ``{"records": [...], "arbitrage": {...}, "summary": {...}}``.
        """
        prepared = self._prepare(inputs)
        if prepared["active"]:
            arbitrage = super().run(ctx, inputs)["arbitrage"]
            projected = self._project(prepared)
        else:
            self.solve_record = None
            arbitrage = {"feasible": True, "violation": 0.0, "legs": [], "relations": [],
                         "credit_per_unit": 0.0, "executable_units": None}
            projected = {}
        records = [self._annotated(dict(row), prepared, projected) for row in inputs["records"]]
        statuses = [row[self.params["fair_field"] + STATUS_SUFFIX] for row in records]
        summary = {"rows": len(records), "projected": statuses.count(STATUS_OK),
                   "by_status": {status: statuses.count(status) for status in STATUSES},
                   "relations": len(prepared["active"]), "skipped_relations": prepared["skipped"],
                   "solve": self.solve_record.to_obj() if self.solve_record is not None else None}
        self.log.info("coherence over %d relation(s): %s", summary["relations"],
                      "coherent" if arbitrage["feasible"] else f"riskless set worth {arbitrage['violation']:.6g}")
        return {"records": records, "arbitrage": arbitrage, "summary": summary}

    def _annotated(self, row, prepared, projected):
        """Write the coherent value and its status onto ``row`` (a copy)."""
        name, cid = self.params["fair_field"], row_key(row.get(self.params["id_field"]))[0]
        if cid is None:
            status = "bad_id"
        elif cid not in prepared["priced"]:
            status = "bad_quote"
        else:
            status = STATUS_OK if cid in projected else "unrelated"
        row.update({name: projected.get(cid), name + STATUS_SUFFIX: status})
        return row


def _add_relations(model, active, p):
    """Add each active relation's rows over ``p`` (``id -> variable or expression``), one constraint component each."""
    from pyomo.environ import Constraint

    for relation in active:
        expressions = relation.rows({cid: p[cid] for cid in relation.ids})
        model.add_component(_component_name(relation),
                            Constraint(range(len(expressions)), rule=lambda m, k, e=expressions: e[k]))


def _component_name(relation):
    """Return the model component that holds ``relation``'s rows."""
    return f"{relation.kind}_{relation.index}"


def _rows_of(model, relation):
    """Every row of ``relation`` in a built model."""
    return list(model.component(_component_name(relation)).values())


def _dual_of(model, name, i):
    """Return the dual of row ``i`` of the constraint ``name``, or 0 when that side has no row."""
    rows = model.component(name)
    return model.dual.get(rows[i], 0.0) if i in rows else 0.0


def _require_position(legs, credit, violation):
    """Refuse an infeasible verdict whose recovered position is empty or cannot profit, naming it."""
    if not legs:
        raise RuntimeError(f"the widening LP found a violation of {violation:.6g} but its duals recover no riskless "
                           "position (every leg is solver dust): refused rather than reported as an empty trade")
    best = credit + sum(leg["units"] for leg in legs if leg["side"] == "buy")
    if not best > 0.0:
        raise RuntimeError(f"the widening LP found a violation of {violation:.6g} but the recovered position cannot "
                           f"profit (credit {credit:.6g}, at most {best:.6g} at settlement): refused, not reported")


def _require_optimal(results, what):
    """Refuse a solve that did not finish optimal, naming it (a limit stop can be raised under ``solver_options``)."""
    condition = str(getattr(getattr(results, "solver", None), "termination_condition", None))
    if condition != "optimal":
        raise RuntimeError(f"{what} finished with termination condition {condition!r}, not 'optimal' "
                           "(if a solver limit stopped it, raise that limit under solver_options)")


def _name_ok(value):
    """Say whether ``value`` is a non-empty string."""
    return isinstance(value, str) and bool(value)


def _relation_keys(declared):
    """Return each declared id list as its :func:`row_key` keys (``validate_params`` refused every bad id)."""
    return [[row_key(cid)[0] for cid in ids] for ids in declared]


def _relation_problems(kind, relation, declared):
    """Problems with one relation kind's declared id lists, every id read by :func:`row_key`."""
    if not isinstance(declared, list):
        return [f"{kind} must be a list of id lists, got {declared!r}"]
    problems = []
    for index, raw in enumerate(declared):
        if not isinstance(raw, list):
            problems.append(f"{kind}[{index}] must be a list of contract ids, got {raw!r}")
            continue
        bad = [f"{cid!r} {why}" for cid, why in ((cid, row_key(cid)[1]) for cid in raw) if why]
        if bad:
            problems.append(f"{kind}[{index}] holds an id that cannot be a key: {'; '.join(bad)}")
            continue
        ids = _relation_keys([raw])[0]
        if len(set(ids)) != len(ids):
            problems.append(f"{kind}[{index}] lists an id more than once: {ids!r}")
            continue
        shape = relation.shape_problem(ids)
        if shape is not None:
            problems.append(f"{kind}[{index}]: {shape}, got {ids!r}")
    return problems


#: Deliberately EMPTY: the node is wired by import path
#: (``dskit.pipeline.libs.binary_coherence:BinaryCoherence``) and nothing here registers.
NODE_KINDS = ()
