"""The record-flow kinds: filter, event-grid, concat, join, derive, groupby.

Two verbs read a single stream; the RELATIONAL four combine, project or
reduce streams instead of reading one.

These are toolkit's plain, un-owned kinds, registered with
``owned=False`` (any project may shadow them with its own class via an
import path); the doctrine kinds (``stat_test``, ``validate``) are a
separate, owned matter. The banking/admission chain that once shared
this module — ``event-bank``, ``eligibility``, ``banking-report`` — is
one cohesive story of its own and now lives in
:mod:`dskit.pipeline.kinds_banking` (TODO 3e); kinds register by NAME,
so that move changed no document and no identity hash.

The relational three are the vocabulary a document needs before two
data sources can share ONE bankroll: without a verb that says "and
also", two venues are two documents, two ``replay`` nodes and two
``final_bankroll`` numbers, and any allocation between them is
arithmetic performed after the fact on runs that never competed. They
are venue-blind by construction and take N inputs, never two (D-137: no
venue is privileged, and nothing here may special-case a pair).
``groupby`` (ADR-0086) is the family's fourth verb, the REDUCTION — one
record per group of declared keys, each carrying declared aggregates over
a closed op table — and it refuses like the three: a mean over the rows
that happened to carry a field is a different number from the one the
document declared.

Record tolerance — the rule for the single-stream verbs, here and in
:mod:`dskit.pipeline.kinds_banking`: records flowing through them are
either plain dicts or objects with attributes (e.g.
:class:`~dskit.pipeline.records.MarketRecord`), and every field access
goes through the single :func:`_field` accessor, which the banking
module imports from here rather than restating. A record that lacks a
needed field is DROPPED or SKIPPED, never crashed on — sparse records
are data, not shape errors (each class documents its exact semantics).
The relational three deliberately do the OPPOSITE and REFUSE; the
section comment above :class:`Concat` says why, and every relaxation
has to be declared in the document before it is allowed.

Validator convention: params may legally arrive as unmaterialized
``$``-references at plan time; validators tolerate the ``$``-form and
check the real value when construction sees the materialized params.
``$prev`` carries never reach a validator raw — the planner substitutes
their literal defaults first.

``is_node_ref`` and ``_reject_unknown`` are imported here from their
homes (:mod:`dskit.pipeline.document` and
:mod:`dskit.pipeline.kinds_stats`) and stay reachable through this
module for one release, because that is the path a sibling could be
importing them by.

No import-time side effects: nothing registers until :func:`register`
is called.

Import cost: stdlib only.
"""

from __future__ import annotations

import copy
import itertools
import re
import statistics
from dataclasses import dataclass

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.kinds_stats import _reject_unknown
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node
from dskit.pipeline.records import number_ok

__all__ = [
    "CLAUSE_OPS",
    "Concat",
    "Derive",
    "EventGrid",
    "Filter",
    "GroupBy",
    "Join",
    "clause_holds",
    "clause_problems",
    "register",
]

#: Sentinel for "the record carries no such field" — distinct from every
#: real value a field could hold (``None`` included).
_MISSING = object()


def _field(record, name):
    """Read one field: ``record[name]`` for mappings, ``record.name`` for objects.

    Returns :data:`_MISSING` when absent. THE accessor — every record
    field read in this module and in
    :mod:`dskit.pipeline.kinds_banking` goes through here, so dicts and
    :class:`~dskit.pipeline.records.MarketRecord`-like objects are
    interchangeable by construction.
    """
    if isinstance(record, dict):
        return record.get(name, _MISSING)
    return getattr(record, name, _MISSING)


# ---------------------------------------------------------------------------
# filter — the record filter (role: transform)
# ---------------------------------------------------------------------------

#: The known where-clause operators. ``in`` tests membership of the
#: record's value in the clause's value. PUBLIC, with :func:`clause_holds`
#: and :func:`clause_problems`: this is the document's one clause DSL
#: (``filter``, ``derive``), and a tier-3 module that keys a table on the
#: same grammar — a dated fee book resolved at a market's close instant
#: — imports the rule rather than mirroring it, so the two can never
#: drift (the "tier-2 never restates tier-1 truth" rule, one tier down).
CLAUSE_OPS = {
    "==": lambda value, target: value == target,
    "!=": lambda value, target: value != target,
    ">": lambda value, target: value > target,
    "<": lambda value, target: value < target,
    ">=": lambda value, target: value >= target,
    "<=": lambda value, target: value <= target,
    "in": lambda value, target: value in target,
}


def clause_problems(name, clause):
    """Shape problems with one where clause, empty when none.

    Parameters
    ----------
    name : str
        How the refusal names the clause (``"where[0]"``).
    clause : object
        The declared clause — a dict with exactly ``field``/``op``/``value``.

    Returns
    -------
    list of str
        One message per problem; empty when the clause is well-shaped.
    """
    if not isinstance(clause, dict):
        return [
            (
                f"{name} must be a dict with exactly the keys "
                f"'field'/'op'/'value', got {clause!r}"
            )
        ]
    problems = []
    if set(clause) != {"field", "op", "value"}:
        problems.append(
            f"{name} must have exactly the keys 'field'/'op'/'value', "
            f"got {sorted(clause)!r}"
        )
    field = clause.get("field")
    if not isinstance(field, str) or not field:
        problems.append(f"{name}.field must be a non-empty string, got {field!r}")
    # Type-check before the table lookup: an unhashable op (a JSON list or
    # object) would make ``in`` raise TypeError out of a validator.
    op = clause.get("op")
    if not isinstance(op, str) or op not in CLAUSE_OPS:
        problems.append(f"{name}.op must be one of {sorted(CLAUSE_OPS)}, got {op!r}")
    return problems


def clause_holds(record, clause) -> bool:
    """Whether ``record`` passes one where clause.

    A clause can only PASS on a present, comparable field: a missing
    field fails it (even under ``!=``), and so does an incomparable pair
    (``TypeError`` — e.g. a string against an int bound, or ``in``
    against a non-container).

    Parameters
    ----------
    record : object
        A mapping or an attribute-bearing record.
    clause : dict
        A well-shaped ``{"field", "op", "value"}`` clause.

    Returns
    -------
    bool
        True only when the field is present and the comparison holds.
    """
    value = _field(record, clause["field"])
    if value is _MISSING:
        return False
    try:
        return bool(CLAUSE_OPS[clause["op"]](value, clause["value"]))
    except TypeError:
        return False


class Filter(Node):
    """Keep the records that pass every declared condition.

    Role ``transform`` — the toolkit's ``filter`` kind.

    Sparse-record semantics, by design: a record missing a tested field
    (or carrying an incomparable value) is DROPPED — a filter never
    crashes on sparse records, and it never passes a record it could not
    actually test. That includes ``require_usable`` (no ``usable`` field
    = cannot claim usability = dropped) and the allow-list (no
    ``instrument`` field = cannot prove membership = dropped).

    Input order is preserved; kept/total is logged.

    Parameters
    ----------
    params : dict
        ``require_usable`` (bool, default ``False``) drops records whose
        ``usable`` field is falsy; ``where`` (list, default ``[]``) is a
        list of ``{"field", "op", "value"}`` clauses over :data:`CLAUSE_OPS`,
        ALL of which must hold.

    Examples
    --------
    Keep the usable records of one venue::

        node = Filter(
            "usable",
            {
                "require_usable": True,
                "where": [{"field": "venue", "op": "==", "value": "examplevenue"}],
            },
        )
        out = node.run(ctx, {"records": records})
    """

    role = "transform"
    outputs = ("records",)

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("require_usable", "where")

    @classmethod
    def validate_params(cls, params):
        """Problems with this node's declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block, possibly carrying unmaterialized
            ``$``-references.

        Returns
        -------
        list of str
            One message per problem; empty when the params are legal.
        """
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        require_usable = params.get("require_usable", False)
        if not is_node_ref(require_usable) and not isinstance(require_usable, bool):
            problems.append(f"require_usable must be a bool, got {require_usable!r}")
        where = params.get("where", [])
        if is_node_ref(where):
            return problems  # a reference — the materialized value re-validates
        if not isinstance(where, list):
            problems.append(
                f"where must be a list of {{'field', 'op', 'value'}} clause "
                f"dicts, got {where!r}"
            )
            return problems
        for i, clause in enumerate(where):
            problems.extend(clause_problems(f"where[{i}]", clause))
        return problems

    def validate_inputs(self, inputs):
        """Problems with the materialized inputs, empty when none.

        Parameters
        ----------
        inputs : dict
            ``records`` (list of dicts or record objects) and OPTIONAL
            ``instruments`` — an allow-list (list/tuple/set) keeping only
            records whose ``instrument`` is in it. The banking chain's
            eligible family is what gets wired here, so the universe grows
            with zero config edits.

        Returns
        -------
        list of str
            One message per problem; empty when the inputs are usable.
        """
        problems = []
        if not isinstance(inputs.get("records"), list):
            problems.append(
                f"records must be a list of records, got {inputs.get('records')!r}"
            )
        allowed = inputs.get("instruments")
        if allowed is not None and not isinstance(allowed, (list, tuple, set)):
            problems.append(
                f"instruments must be an allow-list (list/tuple/set) when "
                f"wired, got {allowed!r}"
            )
        return problems

    def run(self, ctx, inputs):
        """Keep the records that pass every condition, in input order.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused here beyond the node's own logging.
        inputs : dict
            As validated by :meth:`validate_inputs`.

        Returns
        -------
        dict
            ``records`` — the kept rows (list), in input order.
        """
        records = inputs["records"]
        allowed = inputs.get("instruments")
        require_usable = self.params.get("require_usable", False)
        where = self.params.get("where", [])
        kept = []
        for record in records:
            if allowed is not None:
                instrument = _field(record, "instrument")
                if instrument is _MISSING or instrument not in allowed:
                    continue
            if require_usable:
                usable = _field(record, "usable")
                if usable is _MISSING or not usable:
                    continue
            if all(clause_holds(record, clause) for clause in where):
                kept.append(record)
        self.log.info("filter kept %d/%d record(s)", len(kept), len(records))
        return {"records": kept}


# ---------------------------------------------------------------------------
# event-grid — event-time cadence independent of label horizon
# ---------------------------------------------------------------------------


class EventGrid(Node):
    """Keep records whose event instant lies on a declared clock grid.

    Input order and record identity are preserved. A missing or non-integer
    ``asof_ms`` cannot prove grid membership and is dropped, matching the
    single-stream sparse-record convention. The period and offset are both
    hash-material, so action cadence can vary independently of label horizon.

    Parameters
    ----------
    params : dict
        ``period_ms`` (required int > 0) and ``offset_ms`` (required int
        satisfying ``0 <= offset_ms < period_ms``).

    Examples
    --------
    Keep records on five-minute UTC boundaries::

        node = EventGrid(
            "five-minute",
            {"period_ms": 300_000, "offset_ms": 0},
        )
        out = node.run(ctx, {"records": records})
    """

    role = "transform"
    outputs = ("records",)
    _PARAMS = ("period_ms", "offset_ms")

    @classmethod
    def validate_params(cls, params):
        """Validate the declared period and offset.

        Parameters
        ----------
        params : dict
            Node params, possibly carrying unresolved node references.

        Returns
        -------
        list of str
            Every parameter problem; empty when valid.
        """
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        period = params.get("period_ms")
        offset = params.get("offset_ms")
        period_ref = is_node_ref(period)
        offset_ref = is_node_ref(offset)
        period_ok = (
            not period_ref
            and isinstance(period, int)
            and not isinstance(period, bool)
            and period > 0
        )
        offset_ok = (
            not offset_ref
            and isinstance(offset, int)
            and not isinstance(offset, bool)
            and offset >= 0
        )
        if not period_ref and not period_ok:
            problems.append(f"period_ms is required as an int > 0, got {period!r}")
        if not offset_ref and not offset_ok:
            problems.append(f"offset_ms is required as an int >= 0, got {offset!r}")
        if period_ok and offset_ok and offset >= period:
            problems.append(
                "offset_ms must be less than period_ms, "
                f"got offset_ms={offset!r}, period_ms={period!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        """Require one materialized record list.

        Parameters
        ----------
        inputs : dict
            ``records`` must be a list.

        Returns
        -------
        list of str
            Input problems; empty when valid.
        """
        if not isinstance(inputs.get("records"), list):
            return [f"records must be a list of records, got {inputs.get('records')!r}"]
        return []

    def run(self, ctx, inputs):
        """Filter records by ``(asof_ms - offset_ms) % period_ms == 0``.

        Parameters
        ----------
        ctx : NodeContext
            Run frame, used only through node logging.
        inputs : dict
            Validated input record list.

        Returns
        -------
        dict
            ``records`` in their original order.
        """
        period = self.params["period_ms"]
        offset = self.params["offset_ms"]
        records = inputs["records"]
        kept = []
        for record in records:
            instant = _field(record, "asof_ms")
            if (
                isinstance(instant, int)
                and not isinstance(instant, bool)
                and (instant - offset) % period == 0
            ):
                kept.append(record)
        self.log.info("event-grid kept %d/%d record(s)", len(kept), len(records))
        return {"records": kept}


# ---------------------------------------------------------------------------
# the relational kinds — concat / join / derive (role: transform)
# ---------------------------------------------------------------------------
#
# The three verbs above read ONE stream. These three COMBINE streams, which
# is the vocabulary a document needs before two data sources can share one
# bankroll instead of being two documents with two bankrolls and a fictional
# allocation between them.
#
# They deliberately break the module's sparse-record tolerance, and the
# reason is worth stating once for all three. A ``filter`` that cannot test
# a record drops it, and dropping is safe there because the record was
# never claimed to be evidence. A union, a lookup and a projection are the
# opposite: their whole job is to say what a row IS. A concat that dropped
# a schema-mismatched row would silently shrink one source; a join that
# filled an unmatched row with null would price it at nothing; a derive
# that defaulted an unmatched branch would apply one venue's fee schedule
# to another venue's contract and look fine doing it, because the two
# numbers happen to match today. So these three REFUSE, by name, naming the
# node, the port and the row — and every relaxation (an overlap, a
# fan-out, an unmatched row, an untagged stream, an empty port) has to be
# DECLARED in the document before it is allowed.

#: Port names supplied by declaration must obey the same grammar the
#: document enforces on wired ports, so a declared port and a wired port
#: are never distinguishable by their names alone.
_PORT_OK = r"^[a-z_][a-z0-9_]*$"

#: What :class:`Concat` may union. ``"records"`` is a SEQUENCE of rows —
#: the record streams two venues emit. ``"table"`` is a MAPPING keyed by
#: identity — a settlement ledger, a per-series fee schedule. One kind
#: serves both because a union is a union: the same overlap rule, the same
#: empty-port rule, the same refusal to coerce. The document must SAY
#: which, because "what did this node merge" is the first question a joint
#: run has to answer and the answer must be hash-material, not inferred
#: from whatever arrived.
_SHAPES = ("records", "table")

#: How :class:`Join` treats a row no side table matched. There is no
#: fourth option and no default: an unmatched row must never become a null
#: nobody notices.
_JOIN_HOWS = ("strict", "inner", "left")


def _row_schema(row):
    """One row's comparable schema.

    A mapping is compared by its KEY SET (that is what a reader means by
    "the same schema"); anything else by its TYPE, because two instances
    of one frozen envelope class always carry the same fields and an
    object of a different class is a different schema however similar its
    attributes look. ``int`` and ``float`` collapse to one schema: JSON
    writes ``0`` and ``0.0`` for the same number, and refusing that pair
    would be pedantry — while ``"0.07"`` against ``0.07``, the mistake
    that actually happens when a rate is transcribed by hand, still
    refuses.
    """
    if isinstance(row, dict):
        return frozenset(row)
    if not isinstance(row, bool) and isinstance(row, (int, float)):
        return float
    return type(row)


def _schema_text(schema) -> str:
    """Render a schema for a refusal message."""
    if isinstance(schema, frozenset):
        return f"fields {sorted(schema)}"
    if schema is float:
        return "a number"
    return f"a {getattr(schema, '__name__', schema)}"


def _bool_problems(problems, params, names) -> None:
    """Every named knob must be a bool (or an unmaterialized reference)."""
    for name in names:
        value = params.get(name, False)
        if not is_node_ref(value) and not isinstance(value, bool):
            problems.append(f"{name} must be a bool, got {value!r}")


def _field_list(value):
    """Read ``value`` as a tuple of field names, or ``None`` when it is not one.

    A field name or a non-empty list of them qualifies. One reader for
    ``key`` and ``schema`` so the two knobs cannot drift apart.
    """
    names = (value,) if isinstance(value, str) else value
    if not isinstance(names, (list, tuple)) or not names:
        return None
    if any(not isinstance(name, str) or not name for name in names):
        return None
    return tuple(names)


def _declared_tables_problems(problems, params, *, values_must_be):
    """Append the shape problems with a ``tables`` block.

    A ``tables`` block supplies ports by DECLARATION rather than by wire.
    Wired ports carry another node's output; declared ports carry document
    data. Per-series fee rates are exactly that (I-001: rates are API data
    transcribed into the document, never computed and never defaulted),
    and there is no node whose job is to hold a literal — so the relational
    kinds accept a literal port here rather than forcing a document to
    invent one. Being params, declared tables are hash-material: what a
    joint run merged is pinned by the document identity.
    """
    tables = params.get("tables")
    if tables is None or is_node_ref(tables):
        return
    if not isinstance(tables, dict) or not tables:
        problems.append(
            f"tables must be a non-empty dict of {{port: table}}, got {tables!r}"
        )
        return
    for port, table in tables.items():
        if not isinstance(port, str) or not re.match(_PORT_OK, port):
            problems.append(f"tables: port names must match {_PORT_OK}, got {port!r}")
        if not isinstance(table, dict):
            problems.append(
                f"tables[{port!r}] must be a {values_must_be}, got {table!r}"
            )


def _mapping_row(node_key, verb, row, index):
    """Refuse a row a projection cannot lawfully rewrite.

    Returns nothing; raises naming the node.
    ``join`` and ``derive`` both ADD a field to a row, and a row that is
    not a mapping has nowhere to put one. The venue envelope this toolkit
    passes around (:class:`~dskit.pipeline.records.MarketRecord`) is a
    frozen, slotted dataclass: there is no field to add, and adding one
    would mean REPLACING the object — which drops the ``native`` record
    every venue stage reads and silently changes the row type mid-DAG.
    Refusing by name is the only honest answer.

    The message names the two lawful alternatives, because a caller who
    hits this usually wants one of them: enrich the TABLE the consumer
    reads (a per-series schedule wired into a capital node's params —
    which is also why the joint backtest applies fees per source and then
    unions the schedules, rather than stamping a rate onto every row), or
    derive the field before the envelope is built, inside the adapter.
    """
    if isinstance(row, dict):
        return
    raise ValueError(
        f"{node_key}: {verb} rewrites rows, and row {index} is a "
        f"{type(row).__name__}, not a mapping — a frozen venue envelope has "
        "no field to add and replacing it would drop the native record every "
        f"venue stage reads. Either {verb} the TABLE the consumer reads (a "
        "per-series schedule wired into a capital node's params), or add the "
        "field before the envelope is built"
    )


class Concat(Node):
    """Union N inputs into one — the toolkit's ``concat`` kind.

    Role ``transform``, and the vocabulary two venues need to share ONE
    bankroll.

    Two documents with two ``replay`` nodes have two ``final_bankroll``
    numbers, and any allocation between them is arithmetic performed after
    the fact on runs that never competed. One document that unions both
    streams and replays them once has a single balance the venues actually
    compete for. Every node downstream is already venue-blind (D-137); the
    only thing missing was a verb that says "and also".

    Ports
    -----
    Any number, any names, supplied two ways and merged in SORTED PORT-NAME
    order so the union never depends on JSON key ordering:

    * WIRED — ``inputs``, each port another node's output;
    * DECLARED — ``params.tables`` (``shape="table"`` only), each port a
      literal the document carries. A port supplied both ways is refused.

    The port NAME is the provenance label, and under ``shape="records"``
    with a ``provenance`` field that is not cosmetic: a row must already
    agree with its port, or (being a mapping) be stampable with it. That is
    what turns "one venue's stream wired into the other venue's port" from
    an invisible conflation into a refusal.

    Parameters
    ----------
    ``shape`` (REQUIRED, no default)
        ``"records"`` (sequences of rows) or ``"table"`` (mappings).
    ``provenance`` (``"records"`` only)
        The field naming where a row came from. A MAPPING row is copied
        and stamped with the port name — refusing when it already claims a
        different source. A non-mapping row cannot be stamped, so it must
        already carry the field with the port's own name; a row that
        cannot say where it came from does not enter the union. Venue
        envelopes carry ``venue``, which is exactly this field.
    ``provenance_waiver`` (``"records"`` only)
        A WRITTEN reason this stream carries no source tag — the explicit
        opt-out, in the house pattern of ``no_budget_reason``. Exactly one
        of the two must be declared: an untagged union is how two sources'
        rows become indistinguishable, so declining the tag is allowed and
        being silent about it is not.
    ``key`` (``"records"`` only)
        A field name, or a list of them, whose values must not appear in
        two ports. Each is checked INDEPENDENTLY — this is a namespace
        disjointness check, not a composite key, because the failure it
        exists to catch is two sources claiming the same instrument or the
        same contract. A row missing one is refused: it cannot prove it is
        disjoint. Under ``shape="table"`` the mapping's own keys are the
        namespace and are always checked.
    ``allow_overlap`` (default ``False``)
        Overlapping namespaces are legal when DECLARED (and logged); the
        default refuses, naming the field, the two ports and examples. In
        ``"table"`` shape a declared overlap resolves to the LAST port in
        sorted order.
    ``allow_empty`` (default ``False``)
        A port that contributed nothing is refused. A joint run that
        silently loses a whole source still runs, still reports and is a
        different experiment from the one the document declares — which is
        precisely the failure that "looks fine".
    ``consume_inputs`` (default ``False``, ``"records"`` only)
        Clear each mutable input list after its rows have entered ``merged``.
        This is an explicit ownership transfer for bounded-memory pipelines:
        downstream receives the same row objects and upstream must not reuse
        its list. Tuples and declared tables are never mutated.
    ``schema``
        The exact field set every row must carry. Absent, the FIRST row
        seen sets the reference and every later row must match it. Either
        way a mismatch is REFUSED rather than filled: a coerced row is a
        row whose missing field is indistinguishable from a real one.
    ``tables`` (``"table"`` only)
        Declared literal ports (see Ports above).

    Outputs
    -------
    ``merged``
        The union — a list under ``shape="records"``, a dict under
        ``shape="table"``.
    ``sources``
        Per port: how many rows it contributed and, per declared key
        field, how many distinct values — the census that proves the
        disjointness check ran and shows at a glance which source is
        carrying the run.

    Examples
    --------
    Union two venues' record streams, tagged and namespace-disjoint::

        node = Concat(
            "both_venues",
            {"shape": "records", "provenance": "venue", "key": "contract"},
        )
        out = node.run(ctx, {"examplevenue": rows_a, "othervenue": rows_b})
    """

    role = "transform"
    outputs = ("merged", "sources")

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = (
        "allow_empty",
        "allow_overlap",
        "consume_inputs",
        "key",
        "provenance",
        "provenance_waiver",
        "schema",
        "shape",
        "tables",
    )

    @classmethod
    def validate_params(cls, params):
        """Problems with this node's declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block, possibly carrying unmaterialized
            ``$``-references. Shape-specific knobs are cross-checked here:
            a ``"table"``-only knob under ``shape="records"`` (and the
            reverse) is a problem, not a silently ignored key.

        Returns
        -------
        list of str
            One message per problem; empty when the params are legal.
        """
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        _bool_problems(
            problems, params, ("allow_empty", "allow_overlap", "consume_inputs")
        )
        shape = params.get("shape")
        if shape is None:
            problems.append(
                "shape is required — say whether this node unions record "
                f"streams or keyed tables, one of {list(_SHAPES)}. There is no "
                "default: what a joint run merged must be readable from the "
                "document, not inferred from whatever arrived"
            )
        elif not is_node_ref(shape) and shape not in _SHAPES:
            problems.append(f"shape must be one of {list(_SHAPES)}, got {shape!r}")
        provenance = params.get("provenance")
        if (
            provenance is not None
            and not is_node_ref(provenance)
            and (not isinstance(provenance, str) or not provenance)
        ):
            problems.append(
                f"provenance must be a non-empty field name, got {provenance!r}"
            )
        waiver = params.get("provenance_waiver")
        if waiver is not None and (not isinstance(waiver, str) or not waiver.strip()):
            problems.append(
                "provenance_waiver must be a WRITTEN reason this stream carries "
                f"no source tag, got {waiver!r}"
            )
        key = params.get("key")
        if key is not None and not is_node_ref(key) and _field_list(key) is None:
            problems.append(
                "key must be a field name (or a non-empty list of them) whose "
                f"values must not appear in two ports, got {key!r}"
            )
        schema = params.get("schema")
        if schema is not None and not is_node_ref(schema):
            fields = _field_list(schema)
            if fields is None:
                problems.append(
                    f"schema must be a non-empty list of field names, got {schema!r}"
                )
            elif len(set(fields)) != len(fields):
                problems.append(f"schema names a field twice: {schema!r}")
        _declared_tables_problems(problems, params, values_must_be="table (a dict)")
        if shape == "records":
            if (provenance is None) == (waiver is None):
                problems.append(
                    "declare exactly one of provenance (the field naming where "
                    "a row came from) or provenance_waiver (a WRITTEN reason "
                    "these rows carry none) — an untagged union is how two "
                    "sources' rows become indistinguishable, and the fee "
                    "schedule that follows a row is the first thing lost"
                )
            if params.get("tables") is not None:
                problems.append(
                    "tables is a 'table'-shape knob — a record STREAM is "
                    "another node's output, never a literal in this document"
                )
        elif shape == "table":
            if params.get("consume_inputs"):
                problems.append(
                    "consume_inputs is a 'records'-shape ownership transfer; "
                    "tables are never mutated"
                )
            if provenance is not None or waiver is not None:
                problems.append(
                    "provenance/provenance_waiver are 'records'-shape knobs — a "
                    "table's VALUES cannot carry a source tag without becoming "
                    "something other than what the consumer expects, so a table "
                    "union proves provenance by refusing overlapping keys"
                )
            if key is not None:
                problems.append(
                    "key is a 'records'-shape knob — a table's own keys ARE its "
                    "identity, and they are what the overlap check already reads"
                )
            if schema is not None:
                problems.append(
                    "schema names FIELDS of a row; under shape='table' the rows "
                    "are the mapping's VALUES, whose shapes are compared to each "
                    "other automatically"
                )
        return problems

    def validate_inputs(self, inputs):
        """Problems with the materialized inputs, empty when none.

        Container shapes only — never a walk. Walking here would consume a
        one-shot stream and hand ``run`` an exhausted iterator (F-220 #6),
        so a generator is refused BY NAME and every row-level check waits
        for ``run``.

        Parameters
        ----------
        inputs : dict
            One entry per WIRED port: a list or tuple of records under
            ``shape="records"``, a dict under ``shape="table"``. Ports
            declared in ``params.tables`` are supplied there instead, and
            supplying one BOTH ways is a problem.

        Returns
        -------
        list of str
            One message per problem; empty when the inputs are usable.
        """
        shape = self.params.get("shape")
        tables = self.params.get("tables") or {}
        problems = []
        collisions = sorted(set(inputs) & set(tables))
        if collisions:
            problems.append(
                f"port(s) {collisions} are supplied BOTH by wire and by "
                "params.tables — one port, one source"
            )
        if not inputs and not tables:
            problems.append(
                "concat needs at least one port — wire the streams to union, or "
                "declare them under params.tables"
            )
        for port, value in inputs.items():
            if shape == "table":
                if not isinstance(value, dict):
                    problems.append(
                        f"{port} must be a table (a dict) under shape='table', "
                        f"got {value!r}"
                    )
            elif isinstance(value, (str, bytes, dict)) or not isinstance(
                value, (list, tuple)
            ):
                problems.append(
                    f"{port} must be a list or tuple of records under "
                    f"shape='records' (a one-shot iterable would be consumed by "
                    f"validation and reach run() empty), got {value!r}"
                )
        return problems

    def _rows(self, port, value, shape):
        """Read one port as ``[(identity, row), ...]``.

        Identity is the mapping key in ``"table"`` shape and ``None`` in
        ``"records"``.
        """
        if shape == "table":
            if not isinstance(value, dict):
                raise ValueError(
                    f"{self.key}: port {port!r} must be a table (a dict) under "
                    f"shape='table', got {value!r}"
                )
            return list(value.items())
        if isinstance(value, (str, bytes, dict)) or not isinstance(
            value, (list, tuple)
        ):
            raise ValueError(
                f"{self.key}: port {port!r} must be a list or tuple of records "
                f"under shape='records', got {value!r}"
            )
        return [(None, row) for row in value]

    def _checked_schema(self, port, index, row, reference, declared):
        """Check ``row`` against the reference schema and return it.

        The first row sets the reference when the document declared none.
        """
        if declared is not None and not isinstance(row, dict):
            raise ValueError(
                f"{self.key}: schema declares {sorted(declared)}, but port "
                f"{port!r} row {index} is {_schema_text(_row_schema(row))} and "
                "has no fields to check"
            )
        schema = _row_schema(row)
        if reference is None:
            return schema
        if schema != reference:
            raise ValueError(
                f"{self.key}: port {port!r} row {index} has "
                f"{_schema_text(schema)}, the union's reference is "
                f"{_schema_text(reference)} — concat REFUSES a schema mismatch "
                "rather than coercing or filling, because a filled row is a row "
                "whose missing field is indistinguishable from a real one"
            )
        return reference

    def _tagged(self, port, index, row, field):
        """``row`` carrying its provenance, or a refusal naming the node."""
        if isinstance(row, dict):
            present = row.get(field, _MISSING)
            if present is not _MISSING and present != port:
                raise ValueError(
                    f"{self.key}: port {port!r} row {index} already claims "
                    f"{field}={present!r} — refusing to overwrite a row's own "
                    "account of where it came from"
                )
            return {**row, field: port}
        present = _field(row, field)
        if present is _MISSING:
            raise ValueError(
                f"{self.key}: port {port!r} row {index} is a "
                f"{type(row).__name__}, which cannot be stamped, and carries no "
                f"{field!r} of its own — a row that cannot say where it came "
                "from does not enter a union"
            )
        if present != port:
            raise ValueError(
                f"{self.key}: port {port!r} row {index} carries "
                f"{field}={present!r} — name the port after the source it "
                "carries, so the wire and the label cannot disagree. This is "
                "the check that catches one source's stream wired into "
                "another's port, which no downstream number would reveal"
            )
        return row

    def _namespace_problem(self, field, value, port, owners):
        """Record ``value`` as ``port``'s and return the overlap, if any.

        Repeats WITHIN a port are ordinary — a ladder stream carries one
        contract at every lead. Only a value claimed by two ports is an
        overlap, and only that breaks the independence unit.
        """
        owner = owners.get(value)
        if owner is None:
            owners[value] = port
            return None
        return None if owner == port else (field, value, owner, port)

    def run(self, ctx, inputs):
        """Merge every port in sorted port-name order.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused here beyond the node's own logging.
        inputs : dict
            The wired ports; declared ports come from ``params.tables``.

        Returns
        -------
        dict
            ``merged`` — the union (a list under ``shape="records"``, a
            dict under ``shape="table"``); ``sources`` — the per-port
            census described in the class docstring.

        Raises
        ------
        ValueError
            On an empty port, a schema mismatch, an untagged or
            mis-tagged row, a row that cannot prove disjointness, or an
            undeclared namespace overlap — every relaxation must be
            DECLARED in the document before it is allowed.
        """
        shape = self.params["shape"]
        ports = dict(inputs)
        ports.update(self.params.get("tables") or {})
        allow_empty = self.params.get("allow_empty", False)
        allow_overlap = self.params.get("allow_overlap", False)
        consume_inputs = self.params.get("consume_inputs", False)
        provenance = self.params.get("provenance")
        declared = _field_list(self.params.get("schema"))
        reference = frozenset(declared) if declared is not None else None
        # In "table" shape the mapping's own keys are the namespace, under
        # the reserved pseudo-field name below; in "records" shape the
        # document names the fields.
        fields = ("key",) if shape == "table" else _field_list(self.params.get("key"))
        owners = {field: {} for field in (fields or ())}
        overlaps = []
        merged = [] if shape == "records" else {}
        sources = {}
        for port in sorted(ports):
            rows = self._rows(port, ports[port], shape)
            if not rows and not allow_empty:
                raise ValueError(
                    f"{self.key}: port {port!r} contributed NOTHING and "
                    "allow_empty is not declared — a union that silently loses a "
                    "whole source still runs, still reports, and is a different "
                    "experiment from the one this document declares"
                )
            for index, (identity, row) in enumerate(rows):
                reference = self._checked_schema(port, index, row, reference, declared)
                if provenance is not None:
                    row = self._tagged(port, index, row, provenance)
                for field in fields or ():
                    value = identity if shape == "table" else _field(row, field)
                    if value is _MISSING:
                        raise ValueError(
                            f"{self.key}: port {port!r} row {index} carries no "
                            f"{field!r}, which this document declared as a key — "
                            "a row that cannot prove it is disjoint from the "
                            "other sources does not enter the union"
                        )
                    clash = self._namespace_problem(field, value, port, owners[field])
                    if clash is not None:
                        overlaps.append(clash)
                if shape == "table":
                    merged[identity] = row
                else:
                    merged.append(row)
            sources[port] = {
                "rows": len(rows),
                "distinct": {
                    field: sum(1 for owner in owners[field].values() if owner == port)
                    for field in (fields or ())
                },
            }
            if consume_inputs and port in inputs and isinstance(inputs[port], list):
                inputs[port].clear()
            del rows
        if overlaps and not allow_overlap:
            shown = "; ".join(
                f"{field}={value!r} is claimed by both {first!r} and {second!r}"
                for field, value, first, second in overlaps[:5]
            )
            raise ValueError(
                f"{self.key}: {len(overlaps)} overlapping key(s) across ports "
                f"and allow_overlap is not declared — {shown}. Two sources "
                "claiming one identity break the independence unit every "
                "cluster bootstrap and every event count depends on; declare "
                "the overlap if it is real, and fix the namespaces if it is not"
            )
        if overlaps:
            self.log.warning(
                "concat: %d DECLARED overlapping key(s) across ports — the "
                "later port in sorted order wins in table shape",
                len(overlaps),
            )
        self.log.info(
            "concat merged %d %s from %d port(s): %s",
            len(merged),
            "record(s)" if shape == "records" else "table entry(ies)",
            len(ports),
            ", ".join(f"{port}={sources[port]['rows']}" for port in sorted(sources)),
        )
        return {"merged": merged, "sources": sources}


class Join(Node):
    """Align a record stream against N side tables on a declared key.

    Role ``transform`` — the toolkit's ``join`` kind.

    A side table is any lookup a row needs and does not carry: a fee
    schedule, a settlement ledger, a category map. The stream arrives on
    the RESERVED port ``records``; every other port — wired, or declared
    under ``params.tables`` — is a side table, and its name is the field
    it contributes when its values are scalars.

    Rows must be MAPPINGS. A join adds fields, and a frozen venue envelope
    has nowhere to put one (see :func:`_mapping_row`); the refusal names
    the alternatives rather than quietly producing a different row type.

    Parameters
    ----------
    ``key`` (REQUIRED)
        The field on each row whose value indexes every side table.
    ``how`` (REQUIRED, no default)
        What an UNMATCHED row is: ``"strict"`` raises naming the row and
        the ports that missed, ``"inner"`` drops it (counted), ``"left"``
        keeps it and applies ``unmatched_fill``. There is no fourth
        option and no default, because the one behaviour a join must
        never have is turning a miss into a null nobody notices.
    ``unmatched_fill`` (REQUIRED iff ``how="left"``)
        The fields an unmatched row gets, written out. ``{}`` is legal and
        means "keep the row with no joined fields" — which is a decision,
        stated, rather than a null.
    ``allow_fanout`` (default ``False``)
        A side table whose value is a LIST matches a row several times and
        multiplies it. That is one-to-many, and across two tables it is a
        cross join; it must be DECLARED. Even declared, a row fanning out
        in more than one table is refused — a cartesian product is never
        what a lookup meant.
    ``tables``
        Declared literal side tables (see :func:`_declared_tables_problems`).

    Outputs
    -------
    ``records``
        The joined rows, in input order.
    ``matched``
        Per port matched/unmatched counts plus rows in, out and dropped —
        the census that shows a join quietly matching nothing.

    Examples
    --------
    Attach a settlement ledger, refusing any row it does not cover::

        node = Join("settled", {"key": "contract", "how": "strict"})
        out = node.run(ctx, {"records": rows, "outcome": ledger})
    """

    role = "transform"
    outputs = ("records", "matched")

    #: The port carrying the stream; every other port is a side table.
    _LEFT = "records"

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("allow_fanout", "how", "key", "tables", "unmatched_fill")

    @classmethod
    def validate_params(cls, params):
        """Problems with this node's declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block, possibly carrying unmaterialized
            ``$``-references.

        Returns
        -------
        list of str
            One message per problem; empty when the params are legal.
        """
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        _bool_problems(problems, params, ("allow_fanout",))
        key = params.get("key")
        if key is None:
            problems.append(
                "key is required — a join must say which field aligns the "
                "stream against its side tables"
            )
        elif not is_node_ref(key) and (not isinstance(key, str) or not key):
            problems.append(f"key must be a non-empty field name, got {key!r}")
        how = params.get("how")
        if how is None:
            problems.append(
                f"how is required, one of {list(_JOIN_HOWS)} — an unmatched row "
                "must never silently become a null, so what happens to one is "
                "the document's to state"
            )
        elif not is_node_ref(how) and how not in _JOIN_HOWS:
            problems.append(f"how must be one of {list(_JOIN_HOWS)}, got {how!r}")
        fill = params.get("unmatched_fill")
        if fill is not None and not is_node_ref(fill) and not isinstance(fill, dict):
            problems.append(
                f"unmatched_fill must be a dict of {{field: value}}, got {fill!r}"
            )
        if how == "left" and fill is None:
            problems.append(
                "how='left' keeps unmatched rows, so unmatched_fill is required "
                "— write out what an unmatched row gets ({} is legal and means "
                "'no joined fields'), because the alternative is a null nobody "
                "declared"
            )
        if how in ("strict", "inner") and fill is not None:
            problems.append(
                f"unmatched_fill is meaningless under how={how!r} — no unmatched "
                "row survives to be filled"
            )
        _declared_tables_problems(
            problems, params, values_must_be="side table (a dict)"
        )
        tables = params.get("tables")
        if isinstance(tables, dict) and cls._LEFT in tables:
            problems.append(
                f"tables[{cls._LEFT!r}] collides with the reserved stream port "
                f"— {cls._LEFT!r} is the row stream, never a side table"
            )
        return problems

    def validate_inputs(self, inputs):
        """Problems with the materialized inputs, empty when none.

        Container shapes only — the stream is never walked here, because a
        one-shot iterable consumed by validation would reach ``run``
        exhausted.

        Parameters
        ----------
        inputs : dict
            The ``records`` stream (list or tuple of rows) plus one port
            per WIRED side table, each a dict keyed by the join key. Side
            tables declared in ``params.tables`` are supplied there
            instead, and supplying one BOTH ways is a problem.

        Returns
        -------
        list of str
            One message per problem; empty when the inputs are usable.
        """
        problems = []
        records = inputs.get(self._LEFT)
        if isinstance(records, (str, bytes, dict)) or not isinstance(
            records, (list, tuple)
        ):
            problems.append(
                f"{self._LEFT} must be a list or tuple of rows (a one-shot "
                f"iterable would be consumed by validation), got {records!r}"
            )
        tables = self.params.get("tables") or {}
        wired = {port: value for port, value in inputs.items() if port != self._LEFT}
        collisions = sorted(set(wired) & set(tables))
        if collisions:
            problems.append(
                f"side table(s) {collisions} are supplied BOTH by wire and by "
                "params.tables — one port, one source"
            )
        for port, value in wired.items():
            if not isinstance(value, dict):
                problems.append(
                    f"side table {port} must be a dict keyed by the join key, "
                    f"got {value!r}"
                )
        if not wired and not tables:
            problems.append(
                "join needs at least one side table — wire one, or declare it "
                "under params.tables"
            )
        return problems

    def _match_fields(self, port, match):
        """Render one match as the fields it contributes.

        A mapping contributes its own pairs, anything else the port's
        name.
        """
        return dict(match) if isinstance(match, dict) else {port: match}

    def _lookup(self, port, table, identity, index):
        """``(hit, [field-dicts])`` for one side table."""
        try:
            match = table.get(identity, _MISSING)
        except TypeError as exc:  # an unhashable key value
            raise ValueError(
                f"{self.key}: row {index} has an unusable join key "
                f"{identity!r} — a key must be something a table can be "
                f"indexed by ({exc})"
            ) from exc
        if match is _MISSING:
            return False, []
        if isinstance(match, (list, tuple)):
            return True, [self._match_fields(port, item) for item in match]
        return True, [self._match_fields(port, match)]

    def _combined(self, index, row, per_port):
        """``row`` plus every port's fields, refusing any collision.

        A field two tables both supply, or a field the row already
        carries, is refused rather than resolved: silently preferring one
        of two fee schedules is exactly the conflation this kind exists to
        make impossible.
        """
        out = dict(row)
        claimed = {}
        for port, fields in per_port:
            for field, value in fields.items():
                if field in claimed:
                    raise ValueError(
                        f"{self.key}: row {index} — side tables {claimed[field]!r} "
                        f"and {port!r} both contribute {field!r}; refusing to "
                        "pick one"
                    )
                if field in row:
                    raise ValueError(
                        f"{self.key}: row {index} already carries {field!r} and "
                        f"side table {port!r} would overwrite it — a join adds "
                        "fields, it does not rewrite the row's own"
                    )
                claimed[field] = port
                out[field] = value
        return out

    def run(self, ctx, inputs):
        """Align every row against every side table, in input order.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused here beyond the node's own logging.
        inputs : dict
            The stream on ``records`` plus the wired side tables;
            declared side tables come from ``params.tables``.

        Returns
        -------
        dict
            ``records`` — the joined rows (list), in input order;
            ``matched`` — the per-port census described in the class
            docstring.

        Raises
        ------
        ValueError
            On a non-mapping row, a row with no join key or an unusable
            one, an undeclared fan-out (or one spanning two tables), a
            field two tables both contribute, or — under
            ``how="strict"`` — a row nothing matched.
        """
        key = self.params["key"]
        how = self.params["how"]
        fill = self.params.get("unmatched_fill") or {}
        allow_fanout = self.params.get("allow_fanout", False)
        sides = {port: value for port, value in inputs.items() if port != self._LEFT}
        sides.update(self.params.get("tables") or {})
        census = {port: {"matched": 0, "unmatched": 0} for port in sides}
        out = []
        dropped = 0
        for index, row in enumerate(inputs[self._LEFT]):
            _mapping_row(self.key, "join", row, index)
            identity = row.get(key, _MISSING)
            if identity is _MISSING:
                raise ValueError(
                    f"{self.key}: row {index} carries no {key!r} — a row with no "
                    "join key cannot be aligned, and guessing one is how a fee "
                    "schedule ends up on the wrong contract"
                )
            per_port = []
            missed = []
            fanning = []
            for port in sorted(sides):
                hit, fields = self._lookup(port, sides[port], identity, index)
                if not hit:
                    missed.append(port)
                    census[port]["unmatched"] += 1
                    continue
                census[port]["matched"] += 1
                if len(fields) > 1:
                    fanning.append(port)
                per_port.append((port, fields))
            if fanning and not allow_fanout:
                raise ValueError(
                    f"{self.key}: row {index} matches {key}={identity!r} more "
                    f"than once in {fanning} — one-to-many multiplies the row "
                    "and must be DECLARED with allow_fanout"
                )
            if len(fanning) > 1:
                raise ValueError(
                    f"{self.key}: row {index} fans out in {fanning} at once — "
                    "that is a cartesian product across side tables, which is "
                    "never what a lookup meant, and allow_fanout does not "
                    "authorise it"
                )
            if missed:
                if how == "strict":
                    raise ValueError(
                        f"{self.key}: row {index} ({key}={identity!r}) found no "
                        f"match in {missed} and how='strict' — an unmatched row "
                        "raises rather than riding through as a gap"
                    )
                if how == "inner":
                    dropped += 1
                    continue
                per_port.append(("unmatched_fill", [dict(fill)]))
            # One output row per combination of the ports' matches. With
            # fan-out refused above unless declared, and never in two
            # tables at once, this is a single row in every ordinary case.
            choices = [
                [(port, fields) for fields in options] for port, options in per_port
            ]
            for combo in itertools.product(*choices):
                out.append(self._combined(index, row, combo))
        matched = {
            "ports": census,
            "rows_in": len(inputs[self._LEFT]),
            "rows_out": len(out),
            "dropped": dropped,
        }
        self.log.info(
            "join %s: %d row(s) in, %d out, %d dropped; matched %s",
            how,
            matched["rows_in"],
            matched["rows_out"],
            dropped,
            {port: census[port]["matched"] for port in sorted(census)},
        )
        return {"records": out, "matched": matched}


class Derive(Node):
    """Add one field by a declared conditional — the ``derive`` kind.

    Role ``transform``, and the only FAIL-CLOSED projection.

    Each case is ``{"when": [clauses], "value": X}``; the clauses are the
    same ``{"field", "op", "value"}`` DSL :class:`Filter` uses (one DSL,
    one meaning), ALL of a case's clauses must hold, and the FIRST
    matching case wins.

    **An unmatched row RAISES. There is no implicit default**, and that is
    the whole point of the kind. A default is invisible precisely when it
    is wrong: apply one venue's fee schedule to another venue's contract
    and every number downstream still computes, still reports and still
    looks right — most of all when the two schedules happen to carry the
    same number today and are the same number for different reasons. The
    row nobody wrote a case for is exactly the row nobody checked.

    A default IS expressible, but only out loud: a final case with an
    EMPTY ``when`` matches everything. It must be LAST — a catch-all in
    the middle silently shadows every case after it, which is a default
    wearing a disguise.

    A row that already carries the field raises unless ``overwrite`` is
    declared. Rows must be mappings, for the reason in
    :func:`_mapping_row`.

    Outputs: ``records`` (the projected rows, input order) and
    ``branches`` (rows per case, positionally) — a branch that took zero
    rows is logged, because a dead case is usually a case that was meant
    to fire.

    Parameters
    ----------
    params : dict
        ``field`` (str, REQUIRED) — the field this node adds; ``cases``
        (non-empty list, REQUIRED) — the ``{"when": [clauses], "value":
        X}`` cases, first match wins, a final empty ``when`` being the
        only lawful catch-all; ``overwrite`` (bool, default ``False``) —
        whether a row may already carry ``field``.

    Examples
    --------
    Price each venue's fee schedule, refusing any third venue::

        node = Derive(
            "fee_rate",
            {
                "field": "fee_rate",
                "cases": [
                    {"when": [{"field": "venue", "op": "==", "value": "a"}],
                     "value": 0.07},
                    {"when": [{"field": "venue", "op": "==", "value": "b"}],
                     "value": 0.02},
                ],
            },
        )
        out = node.run(ctx, {"records": rows})
    """

    role = "transform"
    outputs = ("records", "branches")

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("cases", "field", "overwrite")

    @classmethod
    def validate_params(cls, params):
        """Problems with this node's declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block, possibly carrying unmaterialized
            ``$``-references.

        Returns
        -------
        list of str
            One message per problem; empty when the params are legal. A
            catch-all case that is not LAST is one of them — it shadows
            every case after it, which is a default in disguise.
        """
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        _bool_problems(problems, params, ("overwrite",))
        field = params.get("field")
        if field is None:
            problems.append("field is required — name the field this node adds")
        elif not is_node_ref(field) and (not isinstance(field, str) or not field):
            problems.append(f"field must be a non-empty field name, got {field!r}")
        cases = params.get("cases")
        if cases is None:
            problems.append(
                "cases is required — a list of {'when': [...], 'value': ...}, "
                "first match wins, and an unmatched row raises"
            )
            return problems
        if is_node_ref(cases):
            return problems  # a reference — the materialized value re-validates
        if not isinstance(cases, list) or not cases:
            problems.append(
                f"cases must be a non-empty list of {{'when', 'value'}} case "
                f"dicts, got {cases!r}"
            )
            return problems
        for i, case in enumerate(cases):
            if not isinstance(case, dict) or set(case) != {"when", "value"}:
                got = sorted(case) if isinstance(case, dict) else case
                problems.append(
                    f"cases[{i}] must have exactly the keys 'when'/'value', got {got!r}"
                )
                continue
            when = case["when"]
            if not isinstance(when, list):
                problems.append(
                    f"cases[{i}].when must be a list of clauses (empty = the "
                    f"explicit catch-all), got {when!r}"
                )
                continue
            for j, clause in enumerate(when):
                problems.extend(clause_problems(f"cases[{i}].when[{j}]", clause))
            if not when and i != len(cases) - 1:
                problems.append(
                    f"cases[{i}] has an empty 'when', so it matches EVERY row, "
                    f"but {len(cases) - 1 - i} case(s) follow it and can never "
                    "fire — a catch-all must be the LAST case, or it is a "
                    "default in disguise"
                )
        return problems

    def validate_inputs(self, inputs):
        """Problems with the materialized inputs, empty when none.

        Container shape only — the stream is never walked here, because a
        one-shot iterable consumed by validation would reach ``run``
        exhausted; the per-row case matching waits for ``run``.

        Parameters
        ----------
        inputs : dict
            ``records`` — a list or tuple of rows (dicts or record
            objects); the only port this kind reads.

        Returns
        -------
        list of str
            One message per problem; empty when the inputs are usable.
        """
        records = inputs.get("records")
        if isinstance(records, (str, bytes, dict)) or not isinstance(
            records, (list, tuple)
        ):
            return [
                "records must be a list or tuple of rows (a one-shot iterable "
                f"would be consumed by validation), got {records!r}"
            ]
        return []

    def _unmatched(self, index, row, cases):
        """Refuse a row no case claimed, showing what was tested."""
        tested = sorted({clause["field"] for case in cases for clause in case["when"]})
        seen = {
            name: ("<missing>" if _field(row, name) is _MISSING else _field(row, name))
            for name in tested
        }
        raise ValueError(
            f"{self.key}: row {index} matched NO case for "
            f"{self.params['field']!r} — tested {seen!r}. derive is "
            "FAIL-CLOSED: there is no implicit default, because the row a "
            "default would silently price is exactly the row nobody checked. "
            "Add the case it needs, or declare a catch-all as the LAST case "
            "with an empty 'when'"
        )

    def run(self, ctx, inputs):
        """Project every row through the first case that matches it.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused here beyond the node's own logging.
        inputs : dict
            ``records`` — the rows to project (list or tuple).

        Returns
        -------
        dict
            ``records`` — the projected rows (list), in input order;
            ``branches`` — rows per case, positionally (list of int).

        Raises
        ------
        ValueError
            On a non-mapping row, a row that already carries the field
            without ``overwrite``, or a row NO case claimed — derive is
            fail-closed by design.
        """
        field = self.params["field"]
        cases = self.params["cases"]
        overwrite = self.params.get("overwrite", False)
        branches = [0] * len(cases)
        out = []
        for index, row in enumerate(inputs["records"]):
            _mapping_row(self.key, "derive", row, index)
            if not overwrite and field in row:
                raise ValueError(
                    f"{self.key}: row {index} already carries {field!r} — derive "
                    "never overwrites a row's own field silently; declare "
                    "overwrite if replacing it is the intent"
                )
            for i, case in enumerate(cases):
                if all(clause_holds(row, clause) for clause in case["when"]):
                    branches[i] += 1
                    value = case["value"]
                    out.append(
                        {
                            **row,
                            field: copy.deepcopy(value)
                            if isinstance(value, (dict, list))
                            else value,
                        }
                    )
                    break
            else:
                self._unmatched(index, row, cases)
        dead = [i for i, n in enumerate(branches) if not n]
        if dead:
            self.log.warning(
                "derive %r: case(s) %s matched NOTHING — a dead case is usually "
                "a case that was meant to fire",
                field,
                dead,
            )
        self.log.info(
            "derive set %r on %d row(s); per case %s", field, len(out), branches
        )
        return {"records": out, "branches": branches}


# ---------------------------------------------------------------------------
# groupby — the reduction (role: transform)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AggOp:
    """One aggregate operator: what it needs, and how it reduces a group."""

    needs_field: bool
    numeric: bool
    ordered: bool
    reduce: object


#: The known aggregate operators — a CLOSED table, read by NAME. A
#: document declares an op and the entry supplies its whole rule: does it
#: read a field, must its cells be numbers, does it need the group's rows
#: ORDERED, and what turns those cells into one value. The alternative is
#: an ``if op ==`` chain in :meth:`GroupBy.run`, where the validator's
#: vocabulary and the run's would be two lists nothing pins together.
#: What a document may spell is exactly ``sorted(_AGG_OPS)``, and the
#: refusal names it; adding an op is one entry here plus its test.
_AGG_OPS = {
    "count": _AggOp(needs_field=False, numeric=False, ordered=False, reduce=len),
    "sum": _AggOp(needs_field=True, numeric=True, ordered=False, reduce=sum),
    "mean": _AggOp(
        needs_field=True, numeric=True, ordered=False, reduce=statistics.fmean
    ),
    "min": _AggOp(needs_field=True, numeric=True, ordered=False, reduce=min),
    "max": _AggOp(needs_field=True, numeric=True, ordered=False, reduce=max),
    "first": _AggOp(
        needs_field=True, numeric=False, ordered=True, reduce=lambda cells: cells[0]
    ),
    "last": _AggOp(
        needs_field=True, numeric=False, ordered=True, reduce=lambda cells: cells[-1]
    ),
    "nunique": _AggOp(
        needs_field=True,
        numeric=False,
        ordered=False,
        reduce=lambda cells: len({_identity_part(cell) for cell in cells}),
    ),
}


def _identity_part(value):
    """Type-tag one hashable value so Python equality cannot merge JSON types."""
    if isinstance(value, bool):
        return (bool, value)
    if isinstance(value, float):
        return (float, repr(value))
    return (type(value), value)


def _sort_part(value):
    """Preserve normal key ordering while breaking numeric equality ties by type."""
    if isinstance(value, bool):
        return (value, 0, repr(value))
    if isinstance(value, int):
        return (value, 1, repr(value))
    if isinstance(value, float):
        return (value, 2, repr(value))
    return value


def _ordered_op(op) -> bool:
    """Whether a declared op reads the group in order — False for an unknown one."""
    # Type-check before the table lookup: an unhashable op (a JSON list or
    # object) would make ``in`` raise TypeError out of a validator.
    return isinstance(op, str) and op in _AGG_OPS and _AGG_OPS[op].ordered


def _aggregate_problems(problems, aggregates, keys) -> None:
    """Append the shape problems with a declared ``aggregates`` block."""
    if not isinstance(aggregates, dict) or not aggregates:
        problems.append(
            "aggregates must be a non-empty dict of {out_field: {'op': ..., "
            f"'field': ...}}, got {aggregates!r}"
        )
        return
    for out_field, spec in aggregates.items():
        if not isinstance(out_field, str) or not out_field:
            problems.append(
                "aggregates: output field names must be non-empty strings, "
                f"got {out_field!r}"
            )
            continue
        name = f"aggregates[{out_field!r}]"
        if out_field in keys:
            problems.append(
                f"{name} restates a key — a group's key fields are already on "
                "its output row, and an aggregate that overwrote one would "
                "make the row disagree with the group it names"
            )
            continue
        problems.extend(_spec_problems(name, spec))


def _spec_problems(name, spec):
    """Problems with one ``{out_field: spec}`` entry, empty when none."""
    if not isinstance(spec, dict):
        return [
            f"{name} must be a dict naming an op over the closed table "
            f"{sorted(_AGG_OPS)}, got {spec!r}"
        ]
    problems = []
    if "op" not in spec or set(spec) - {"op", "field"}:
        problems.append(
            f"{name} must have exactly the keys 'op'/'field' (an op that reads "
            f"no field takes 'op' alone), got {sorted(spec, key=repr)!r}"
        )
    op = spec.get("op")
    if not isinstance(op, str) or op not in _AGG_OPS:
        problems.append(f"{name}.op must be one of {sorted(_AGG_OPS)}, got {op!r}")
        return problems
    field = spec.get("field")
    # PRESENCE, not None-ness: a declared ``"field": null`` under an op
    # that reads no field is still a knob the run never applies.
    if not _AGG_OPS[op].needs_field:
        if "field" in spec:
            problems.append(
                f"{name}: {op} counts rows, so it takes no field, got {field!r}"
            )
    elif "field" not in spec:
        problems.append(f"{name}: {op} needs a field to reduce — name the field")
    elif not isinstance(field, str) or not field:
        problems.append(f"{name}.field must be a non-empty field name, got {field!r}")
    return problems


class GroupBy(Node):
    """One record per group, carrying declared aggregates — the ``groupby`` kind.

    Role ``transform``, and the relational family's REDUCTION (ADR-0086).
    ``filter`` and ``event-grid`` read a stream, ``concat`` unions,
    ``join`` looks up and ``derive`` projects; nothing REDUCED, so "one
    row per event with its strike count and its last mid" had to be
    child code.

    It refuses where the single-stream verbs drop, for the reason the
    section comment above :class:`Concat` gives: a reduction says what a
    row IS, and a mean over the rows that happened to carry a field is a
    different number from the one the document declared. A row missing a
    key or an aggregate's field, a key value nothing can be keyed by, a
    non-numeric cell under a numeric op, an order value that is not a
    number, key values that cannot be ordered against each other — each
    raises, naming the node, the row and the field.

    Rows may be mappings or attribute-bearing records (the module's
    :func:`_field` accessor); an output row is always a plain dict
    carrying the key fields and then the aggregates. Output order is
    DETERMINISTIC — groups sorted by their key values — so two runs over
    the same stream write the same rows in the same order.

    Outputs: ``records`` (one row per group) and ``metrics``
    (``rows_in``/``groups`` — the census that shows a reduction quietly
    collapsing a stream to one row).

    Parameters
    ----------
    params : dict
        ``keys`` (str or non-empty list of str, REQUIRED) — the field(s)
        whose values define a group, each of which lands on the output
        row; ``aggregates`` (non-empty dict, REQUIRED) — ``{out_field:
        {"op": <op>, "field": <field>}}`` over the closed vocabulary
        :data:`_AGG_OPS` (``count`` takes no ``field``, every other op
        requires one, and an ``out_field`` may not restate a key);
        ``order_field`` (str, REQUIRED iff an aggregate is ``first`` or
        ``last``, refused otherwise) — the numeric field saying which row
        of a group is first and which is last, ties resolved by input
        order.

    Examples
    --------
    Reduce a strike ladder to one row per event::

        node = GroupBy(
            "per_event",
            {
                "keys": "instrument",
                "order_field": "asof_ms",
                "aggregates": {
                    "strikes": {"op": "nunique", "field": "contract"},
                    "last_mid": {"op": "last", "field": "mid"},
                },
            },
        )
        out = node.run(ctx, {"records": rows})
        # -> {"records": [{"instrument": "A", "strikes": 3, "last_mid": 0.62}],
        #     "metrics": {"rows_in": 9, "groups": 1}}
    """

    role = "transform"
    outputs = ("records", "metrics")

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("aggregates", "keys", "order_field")

    @classmethod
    def validate_params(cls, params):
        """Problems with this node's declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block, possibly carrying unmaterialized
            ``$``-references. The ``order_field`` cross-check waits for a
            materialized ``aggregates``: whether an order is READ is a
            fact about the ops declared there.

        Returns
        -------
        list of str
            One message per problem; empty when the params are legal.
        """
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        keys = cls._key_problems(problems, params)
        aggregates = params.get("aggregates")
        if aggregates is None:
            problems.append(
                "aggregates is required — a group with nothing measured over "
                "it is a row that says only that it existed"
            )
        elif not is_node_ref(aggregates):
            _aggregate_problems(problems, aggregates, keys)
        order_field = params.get("order_field")
        if (
            order_field is not None
            and not is_node_ref(order_field)
            and (not isinstance(order_field, str) or not order_field)
        ):
            problems.append(
                f"order_field must be a non-empty field name, got {order_field!r}"
            )
        cls._order_problems(problems, aggregates, order_field)
        return problems

    @classmethod
    def _key_problems(cls, problems, params):
        """Append the ``keys`` problems; answer the key names, empty when unusable."""
        keys = params.get("keys")
        if keys is None:
            problems.append(
                "keys is required — name the field(s) whose values define a group"
            )
            return ()
        if is_node_ref(keys):
            return ()
        names = _field_list(keys)
        if names is None:
            problems.append(
                "keys must be a field name or a non-empty list of them, "
                f"got {keys!r}"
            )
            return ()
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            problems.append(
                f"keys names a field twice ({repeated}) — a key restated does "
                "not narrow a group, it just says the same thing again"
            )
        return names

    @classmethod
    def _order_problems(cls, problems, aggregates, order_field) -> None:
        """Append the ``order_field``-vs-``aggregates`` agreement problems."""
        if not isinstance(aggregates, dict):
            return  # unmaterialized or already refused above
        ordered = sorted(
            out_field
            for out_field, spec in aggregates.items()
            if isinstance(spec, dict) and _ordered_op(spec.get("op"))
        )
        if ordered and order_field is None:
            problems.append(
                f"order_field is required — aggregate(s) {ordered} take the "
                "FIRST or LAST row of a group, and which row that is depends "
                "on an order the document has not named"
            )
        elif not ordered and order_field is not None:
            problems.append(
                "order_field is meaningless here — no aggregate reads an "
                "order, so nothing would use it, and a knob nothing reads is "
                "a value validation approves and the run never applies"
            )

    def validate_inputs(self, inputs):
        """Problems with the materialized inputs, empty when none.

        Container shape only — the stream is never walked here, because a
        one-shot iterable consumed by validation would reach ``run``
        exhausted; the per-row refusals wait for ``run``.

        Parameters
        ----------
        inputs : dict
            ``records`` — a list or tuple of rows (dicts or record
            objects); the only port this kind reads.

        Returns
        -------
        list of str
            One message per problem; empty when the inputs are usable.
        """
        records = inputs.get("records")
        if isinstance(records, (str, bytes, dict)) or not isinstance(
            records, (list, tuple)
        ):
            return [
                "records must be a list or tuple of rows (a one-shot iterable "
                f"would be consumed by validation), got {records!r}"
            ]
        return []

    def _cell(self, index, row, name):
        """One row's value for a declared field, refusing an absent one by name."""
        value = _field(row, name)
        if value is _MISSING:
            raise ValueError(
                f"{self.key}: row {index} carries no {name!r} — a reduction "
                "says what a row IS, and a group measured over the rows that "
                "happened to carry a field is not the number the document "
                "declared"
            )
        return value

    def _identity(self, index, row, keys):
        """One row's group identity, refusing a key value nothing can be keyed by."""
        raw = []
        for name in keys:
            value = self._cell(index, row, name)
            try:
                hash(value)
            except TypeError as exc:
                raise ValueError(
                    f"{self.key}: row {index} has an unusable key value "
                    f"{value!r} for {name!r} — a group key must be something a "
                    f"mapping can be keyed by ({exc})"
                ) from exc
            raw.append(value)
        raw = tuple(raw)
        return tuple(_identity_part(value) for value in raw), raw

    def _sorted(self, groups):
        """Order the group identities for output, refusing values nothing can order."""
        try:
            return sorted(
                groups,
                key=lambda identity: tuple(
                    _sort_part(value) for value in groups[identity][0]
                ),
            )
        except TypeError as exc:
            raise ValueError(
                f"{self.key}: the group key values cannot be ordered against "
                f"each other ({exc}) — output order is what makes two runs "
                "over one stream write the same rows, so a key whose values "
                "have no order is refused rather than emitted arbitrarily"
            ) from exc

    def _instant(self, field, index, row):
        """One row's order value, refusing anything that cannot order a group."""
        value = self._cell(index, row, field)
        if not number_ok(value):
            raise ValueError(
                f"{self.key}: order_field {field!r} orders each group's rows, "
                f"and row {index} holds {value!r} — that is not a number "
                "(records.number_ok: a non-bool int or a finite float), so "
                "which row is FIRST and which is LAST cannot be answered"
            )
        return value

    def _ordered(self, members):
        """Order a group's ``(index, row)`` members — input order when undeclared."""
        field = self.params.get("order_field")
        if field is None:
            return members
        return sorted(members, key=lambda member: self._instant(field, *member))

    def _cells(self, out_field, spec, members):
        """Read the cells one aggregate reduces, refusing an unusable one by name."""
        rule = _AGG_OPS[spec["op"]]
        if not rule.needs_field:
            return [row for _, row in members]
        field = spec["field"]
        cells = []
        for index, row in members:
            value = self._cell(index, row, field)
            if rule.numeric and not number_ok(value):
                raise ValueError(
                    f"{self.key}: aggregate {out_field!r} ({spec['op']}) needs "
                    f"numbers, and row {index} field {field!r} holds {value!r} "
                    "— that is not a number (records.number_ok), and a "
                    f"{spec['op']} over the cells that happened to parse is "
                    "not the number the document declared"
                )
            cells.append(value)
        return cells

    def _reduced(self, out_field, spec, identity, members):
        """One aggregate's value over a group, refusing cells its reducer defeats."""
        cells = self._cells(out_field, spec, members)
        try:
            return _AGG_OPS[spec["op"]].reduce(cells)
        except TypeError as exc:
            raise ValueError(
                f"{self.key}: aggregate {out_field!r} ({spec['op']}) cannot "
                f"reduce group {identity!r} ({exc}) — the op is declared and "
                "the cells are not what it takes"
            ) from exc

    def _group_row(self, keys, identity, members):
        """One output record: the group's key fields, then its aggregates."""
        members = self._ordered(members)
        row = dict(zip(keys, identity, strict=True))
        for out_field, spec in self.params["aggregates"].items():
            row[out_field] = self._reduced(out_field, spec, identity, members)
        return row

    def run(self, ctx, inputs):
        """Reduce the stream to one record per group, sorted by key values.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused here beyond the node's own logging.
        inputs : dict
            ``records`` — the rows to reduce (list or tuple).

        Returns
        -------
        dict
            ``records`` — one mapping per group (list), sorted by key
            values; ``metrics`` — ``rows_in`` and ``groups``.

        Raises
        ------
        ValueError
            On a row missing a key, an aggregate's field or the order
            field; an unusable key value; a non-numeric cell under a
            numeric op; a non-numeric order value; key values that cannot
            be ordered against each other; or cells a reducer defeats.
        """
        records = inputs["records"]
        keys = _field_list(self.params["keys"])
        groups = {}
        for index, row in enumerate(records):
            identity, raw = self._identity(index, row, keys)
            groups.setdefault(identity, (raw, []))[1].append((index, row))
        out = [
            self._group_row(keys, groups[identity][0], groups[identity][1])
            for identity in self._sorted(groups)
        ]
        self.log.info(
            "groupby reduced %d record(s) to %d group(s) by %s",
            len(records),
            len(out),
            list(keys),
        )
        return {"records": out, "metrics": {"rows_in": len(records), "groups": len(out)}}


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

#: The kinds this module ships, in registration order — the
#: single-stream verbs, then the four relational ones. The banking chain
#: registers separately, from :mod:`dskit.pipeline.kinds_banking`.
_KINDS = (
    ("filter", Filter),
    ("event-grid", EventGrid),
    ("concat", Concat),
    ("join", Join),
    ("derive", Derive),
    ("groupby", GroupBy),
)


def register(registry=None):
    """Register the six record-flow kinds, ``owned=False``.

    Idempotent by SKIPPING any name already present — never shadowing an
    existing registration (deliberate re-binding goes through the
    registry itself, which refuses duplicates loudly). Nothing registers
    at import time; calling this is the explicit opt-in.

    Parameters
    ----------
    registry : NodeKindRegistry or None
        Where to register; ``None`` means
        :data:`~dskit.pipeline.node.DEFAULT_NODE_KINDS`.

    Returns
    -------
    NodeKindRegistry
        The same registry, for chaining.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in _KINDS:
        if name not in registry:
            registry.register(name, cls, owned=False)
    return registry
