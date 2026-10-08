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
2. The coherent fair value. A weighted least-squares projection of the mids onto the coherent
   set (the relations and ``[0, 1]``, NOT the intervals, which may be empty together), weights
   ``1 / max(spread, min_spread)^2`` so a tight market moves less than a wide one. On mids that
   are already coherent it is the identity.

Solver. The doorway is :class:`~dskit.pipeline.libs.pyomo.PyomoSolve` with a non-capital role
(``transform``: this pack sizes nothing and reads no ``stat_test`` gate, so the planner's
capital rule does not apply). The base's default ``appsi_highs`` interface refuses a quadratic
objective (it raises on a degree-2 expression), while pyomo's ``highs`` interface hands the same
HiGHS a QP and solves it; so ``solver`` is REQUIRED here rather than defaulted, and a solver that
cannot take the projection is refused by name at run. The isotonic fallback the ADR allowed for
a QP-less solver is therefore not built. Both solves go through the base's
``_resolve_solver``/``_solve``; the LP through the base lifecycle (its ``SolveRecord`` is kept),
the QP as a second solve of the same instance.

Relations are strategy objects (:class:`Relation`, registry :data:`RELATIONS`): a new identity is
a subclass and a registry entry, never a branch in the model builder.

Wiring is by import path: :data:`NODE_KINDS` is empty and nothing registers. A document names
``dskit.pipeline.libs.binary_coherence:BinaryCoherence``.

Import cost: stdlib + toolkit only. pyomo is imported strictly inside run-path methods.
"""

from abc import ABC, abstractmethod
from types import MappingProxyType

from dskit.pipeline.binary_pricing import STATUS_SUFFIX
from dskit.pipeline.libs.pyomo import PyomoSolve
from dskit.pipeline.records import number_ok

__all__ = [
    "DEFAULT_TOLERANCE",
    "LEG_DUST",
    "NODE_KINDS",
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

#: The status a projected contract carries; the others name why it has no coherent value.
STATUS_OK = "ok"
STATUSES = (STATUS_OK, "bad_quote", "unrelated")


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
        """Return the relation as pyomo relational expressions over ``p`` (``id -> variable``).

        Parameters
        ----------
        p : mapping
            Each linked id's probability variable.

        Returns
        -------
        list
            One expression per row.
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
    (fee-adjusted upstream), and sizes when named. Outputs: ``records``, each row with
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
        ``solver_options``.

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
        ids = [row.get(self.params["id_field"]) if isinstance(row, dict) else None for row in records]
        problems = [f"records[{i}] has no usable id, got {cid!r}" for i, cid in enumerate(ids) if not _id_ok(cid)]
        repeated = sorted({repr(c) for c in ids if _id_ok(c) and ids.count(c) > 1})
        if repeated:
            problems.append(f"records list the id(s) {repeated} more than once: one row per contract")
        return problems

    # -- preparation: which quotes and relations are usable -----------------------------------------------

    def _quote_ok(self, row):
        """Say whether a row's bid and ask are a usable interval inside [0, 1]."""
        bid, ask = row.get(self.params["bid_field"]), row.get(self.params["ask_field"])
        return number_ok(bid) and number_ok(ask) and 0.0 <= bid <= ask <= 1.0

    def _prepare(self, inputs):
        """Return the usable rows by id, the active relations and the skipped ones (with reasons)."""
        rows = {row[self.params["id_field"]]: row for row in inputs["records"]}
        usable = {cid: row for cid, row in rows.items() if self._quote_ok(row)}
        active, skipped = [], []
        for kind in RELATION_KINDS:
            relation = RELATIONS[kind]
            for index, ids in enumerate(self.params.get(kind, [])):
                missing = [cid for cid in ids if cid not in rows]
                unusable = [cid for cid in ids if cid in rows and cid not in usable]
                if missing or unusable:
                    skipped.append({"kind": kind, "index": index, "ids": list(ids), "missing": missing,
                                    "bad_quote": unusable})
                else:
                    active.append(relation(index, ids))
        linked = sorted({cid for relation in active for cid in relation.ids}, key=repr)
        return {"rows": rows, "usable": usable, "active": active, "skipped": skipped, "linked": linked}

    # -- the LP: the PyomoSolve hooks ---------------------------------------------------------------------

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
            The LP, with an imported ``dual`` suffix and its preparation on ``_coherence``.
        """
        from pyomo.environ import ConcreteModel, Constraint, NonNegativeReals, Objective, Suffix, Var

        prepared = self._prepare(inputs)
        ids, usable = prepared["linked"], prepared["usable"]
        model = ConcreteModel(name="binary-coherence")
        model.dual = Suffix(direction=Suffix.IMPORT)
        keys = range(len(ids))
        model.p = Var(keys, bounds=(0.0, 1.0))
        model.widen_bid = Var(keys, domain=NonNegativeReals)
        model.widen_ask = Var(keys, domain=NonNegativeReals)
        bid = [float(usable[cid][params["bid_field"]]) for cid in ids]
        ask = [float(usable[cid][params["ask_field"]]) for cid in ids]
        model.at_bid = Constraint(keys, rule=lambda m, i: m.p[i] + m.widen_bid[i] >= bid[i])
        model.at_ask = Constraint(keys, rule=lambda m, i: m.p[i] - m.widen_ask[i] <= ask[i])
        _add_relations(model, prepared["active"], ids)
        model.widening = Objective(expr=sum(model.widen_bid[i] + model.widen_ask[i] for i in keys))
        model._coherence = {**prepared, "bid": bid, "ask": ask}
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

        _require_optimal(results, "the widening LP (the declared relations may admit no probability vector)")
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
            "credit_per_unit": credit, "executable_units": self._executable_units(legs, data["usable"])}}

    def _legs(self, model, data, dust):
        """Return the riskless position the LP's duals certify: units sold at bids, bought at asks."""
        legs = []
        for i, cid in enumerate(data["linked"]):
            net = -model.dual.get(model.at_ask[i], 0.0) - model.dual.get(model.at_bid[i], 0.0)
            if abs(net) > dust:
                side = "buy" if net > 0 else "sell"
                legs.append({"id": cid, "side": side, "units": abs(net),
                             "price": data["ask"][i] if side == "buy" else data["bid"][i]})
        return legs

    def _executable_units(self, legs, usable):
        """Return the thinnest leg's size over its units, or None when no size columns are named."""
        if "bid_size_field" not in self.params:
            return None
        sizes = []
        for leg in legs:
            size = usable[leg["id"]].get(self.params["bid_size_field" if leg["side"] == "sell" else "ask_size_field"])
            sizes.append(size / leg["units"] if number_ok(size) and size >= 0 else 0.0)
        return float(min(sizes))

    # -- the QP: the coherent projection ------------------------------------------------------------------

    def _project(self, prepared):
        """Solve the weighted least-squares projection of the mids; return ``{id: probability}``."""
        from pyomo.environ import ConcreteModel, Objective, Var, value

        ids, usable, p = prepared["linked"], prepared["usable"], self.params
        floor = float(p["min_spread"])
        mids, weights = [], []
        for cid in ids:
            bid, ask = float(usable[cid][p["bid_field"]]), float(usable[cid][p["ask_field"]])
            mids.append((bid + ask) / 2.0)
            weights.append(1.0 / max(ask - bid, floor) ** 2)
        model = ConcreteModel(name="binary-coherence-projection")
        keys = range(len(ids))
        model.p = Var(keys, bounds=(0.0, 1.0), initialize=lambda m, i: mids[i])
        _add_relations(model, prepared["active"], ids)
        # Scaled by the largest weight so the solver's tolerances act on numbers near one.
        scale = max(weights)
        model.distance = Objective(expr=sum(weights[i] / scale * (model.p[i] - mids[i]) ** 2 for i in keys))
        solver = self._resolve_solver()
        try:
            results = self._solve(solver, model)
        except Exception as exc:   # a solver interface that cannot take a quadratic objective raises
            raise RuntimeError(f"solver {p['solver']!r} could not solve the quadratic projection: {exc}") from exc
        _require_optimal(results, "the projection QP")
        return {cid: min(1.0, max(0.0, float(value(model.p[i])))) for i, cid in enumerate(ids)}

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
        name, cid = self.params["fair_field"], row.get(self.params["id_field"])
        if cid not in prepared["usable"]:
            status = "bad_quote"
        else:
            status = STATUS_OK if cid in projected else "unrelated"
        row.update({name: projected.get(cid), name + STATUS_SUFFIX: status})
        return row


def _add_relations(model, active, ids):
    """Add each active relation's rows to ``model`` as one constraint component per relation."""
    from pyomo.environ import Constraint

    position = {cid: i for i, cid in enumerate(ids)}
    for relation in active:
        p = {cid: model.p[position[cid]] for cid in relation.ids}
        expressions = relation.rows(p)
        model.add_component(_component_name(relation),
                            Constraint(range(len(expressions)), rule=lambda m, k, e=expressions: e[k]))


def _component_name(relation):
    """Return the model component that holds ``relation``'s rows."""
    return f"{relation.kind}_{relation.index}"


def _rows_of(model, relation):
    """Every row of ``relation`` in a built model."""
    return list(model.component(_component_name(relation)).values())


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
    """Refuse a solve that did not finish optimal, naming it."""
    condition = str(getattr(getattr(results, "solver", None), "termination_condition", None))
    if condition != "optimal":
        raise RuntimeError(f"{what} finished with termination condition {condition!r}, not 'optimal'")


def _name_ok(value):
    """Say whether ``value`` is a non-empty string."""
    return isinstance(value, str) and bool(value)


def _id_ok(value):
    """Say whether ``value`` can be a contract id: a non-empty string or a non-bool int."""
    return _name_ok(value) or (isinstance(value, int) and not isinstance(value, bool))


def _relation_problems(kind, relation, declared):
    """Problems with one relation kind's declared id lists."""
    if not isinstance(declared, list):
        return [f"{kind} must be a list of id lists, got {declared!r}"]
    problems = []
    for index, ids in enumerate(declared):
        if not isinstance(ids, list) or not all(_id_ok(cid) for cid in ids):
            problems.append(f"{kind}[{index}] must be a list of contract ids, got {ids!r}")
            continue
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
